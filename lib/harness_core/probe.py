"""Probe and pre-filter (spec §The eight steps, steps 1-2; build phase P3). Deterministic, no model.

``run(request, ctx, cwd=None) -> probe`` scans the request text (and the workspace files it names) for:
action verbs, write targets, files named, connectors named, money and number tokens, quantity words,
decision-class keywords and personal data. Keyword lists live in the control file ``probe.json``.

- A verb counts toward ``irreversible_classes`` only when it is used as an instruction ("send the
  invoice", "please email Sara", "...and pay it"), not in a question ("did we send it?"). Any verb at all
  still makes the pre-filter ask for a card.
- ``floor_tier``: T2 with an irreversible class, T1 with a write target or a write-capable connector,
  else T0.
- A named file is read only when its realpath is inside the workspace (and never through a symlink).

``prefilter(probe) -> "T0-fast" | "card-needed"``: the fast path only when the probe found none of: write
targets, files or connectors named, money or number tokens, quantity words, action verbs, decision-class
keywords. (Quantity words go beyond the spec's list: "how many orders…" computes a number, R-11.)
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from . import canon, pii, protected, schema

MAX_CHARS = 20000
_FOLD = str.maketrans({"ي": "ی", "ك": "ک", "‌": "", "‍": "", "۰": "0", "۱": "1", "۲": "2", "۳": "3",
                       "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9", "٠": "0", "١": "1", "٢": "2",
                       "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"})


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).translate(_FOLD).casefold()


def _kw(word: str) -> str:
    """A keyword as a regex: whole words (Persian letters count as word characters); symbols as is."""
    w = re.escape(_norm(word))
    return w if not re.match(r"\w", _norm(word)[:1] or "x") else rf"(?<!\w){w}(?!\w)"


def _hits(text: str, words) -> list:
    return sorted({w for w in words if re.search(_kw(w), text)})


def _imperative(text: str, verb: str, leads) -> bool:
    if not re.match(r"[a-z]", _norm(verb)):
        return bool(re.search(_kw(verb), text))  # the Persian lists hold imperative forms only
    lead = "|".join(re.escape(_norm(x)) for x in sorted(leads, key=len, reverse=True))
    v = re.escape(_norm(verb))
    start = rf"(?:^|[.!;?\n:]\s*)(?:(?:{lead})\s+)*{v}(?!\w)"
    joined = rf"(?<!\w)(?:and|then|also|please)\s+(?:(?:{lead})\s+)*{v}(?!\w)"
    return bool(re.search(start, text) or re.search(joined, text))


def _files(text: str, exts) -> list:
    ext = "|".join(re.escape(e) for e in exts)
    pat = rf"(?<![\w@/.~])((?:\.{{1,2}}/|~/|/)?(?:[\w\-]+/)*[\w\-.]+\.(?:{ext}))(?![\w])"
    return sorted(set(re.findall(pat, text, flags=re.IGNORECASE)))


def _in_workspace(name: str, ctx, cwd) -> Path:
    base = cwd or getattr(ctx, "cwd", None) or ctx.workspace
    p = Path(os.path.expanduser(name)) if name.startswith("~/") else Path(base) / name
    real = os.path.realpath(p)
    ws = os.path.realpath(ctx.workspace)
    if real != ws and not real.startswith(ws.rstrip("/") + "/"):
        return None
    if os.path.islink(p):
        return None
    return Path(real)


def _connectors(text: str, ctx):
    """Connector (MCP server) names in the text, and those with any non-read scope in the registry."""
    reg = _registry(ctx)
    servers = {}
    for tool, scope in reg.items():
        parts = tool.split("__")
        if len(parts) >= 3 and parts[0] == "mcp":
            servers.setdefault(parts[1], set()).add(scope)
    named = sorted(s for s in servers if re.search(_kw(s.replace("_", " ")), text) or re.search(_kw(s), text))
    write = sorted(s for s in named if any(sc != "read" for sc in servers[s]))
    return named, write


def _registry(ctx) -> dict:
    import json
    reg = getattr(ctx, "connector_registry", None)
    if isinstance(reg, dict) and reg:
        return reg
    f = Path(ctx.data_root) / "business" / "connector_registry.json" if getattr(ctx, "data_root", None) else None
    try:
        v = json.loads(f.read_text("utf-8")) if f and f.is_file() else {}
    except (OSError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def run(request, ctx, cwd=None) -> dict:
    raw = request if isinstance(request, str) else ""
    text = _norm(raw[:MAX_CHARS])
    k = protected.load_control("probe.json", ctx)
    leads = k["imperative_lead"]
    verbs = {cls: _hits(text, words) for cls, words in k["verbs"].items()}
    action_verbs = sorted({v for vs in verbs.values() for v in vs})
    imperative = {cls for cls, vs in verbs.items() if any(_imperative(text, v, leads) for v in vs)}
    irreversible = sorted(c for c in imperative if c not in ("local_reversible",))
    first = re.split(r"\s+", text.strip(), maxsplit=1)[0] if text.strip() else ""
    asks = text.rstrip().endswith(("?", "؟")) or first in {_norm(q) for q in k["question_lead"]}
    question_only = bool(asks and not imperative)
    files = _files(raw[:MAX_CHARS], k["file_extensions"])
    writes_files = bool(imperative & {"local_reversible", "destructive"})
    write_targets = files if writes_files else []
    data_classes = set(pii.scan(raw[:MAX_CHARS]))
    data_file = False
    for name in files:
        p = _in_workspace(name, ctx, cwd)
        if p is not None and p.is_file():
            data_classes |= set(pii.scan(p))
        data_file = data_file or name.lower().endswith((".csv", ".tsv", ".xlsx", ".xls", ".json"))
    money = _hits(text, k["money_words"])
    numbers = sorted(set(re.findall(r"\d+(?:[.,]\d+)*", text)))[:20]
    quantity = _hits(text, k["quantity_words"])
    decision = {cls: hits for cls, words in k["decision_keywords"].items() if (hits := _hits(text, words))}
    connectors, write_conn = _connectors(text, ctx)
    floor = "T2" if irreversible else "T1" if (write_targets or write_conn) else "T0"
    out = {
        "schema": "harness/probe@1",
        "request_sha": canon.sha256_bytes(raw.encode("utf-8", "surrogatepass")),
        "question_only": question_only,
        "action_verbs": action_verbs,
        "irreversible_classes": irreversible,
        "write_targets": write_targets,
        "files_named": files,
        "connectors": connectors,
        "write_connectors": write_conn,
        "money_tokens": money,
        "number_tokens": numbers,
        "quantitative": bool(money or quantity or numbers or data_file),
        "data_classes": sorted(data_classes),
        "decision_hits": decision,
        "continuation": bool(_hits(text, k["continuation"])),
        "floor_tier": floor,
    }
    schema.check(out, "probe")
    return out


def prefilter(p: dict) -> str:
    # quantitative also covers quantity words ("how many…"): a computed number is coming (R-11)
    hint = (p.get("write_targets") or p.get("files_named") or p.get("connectors") or p.get("money_tokens")
            or p.get("number_tokens") or p.get("quantitative") or p.get("action_verbs") or p.get("decision_hits"))
    return "card-needed" if hint else "T0-fast"
