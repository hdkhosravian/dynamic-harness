"""Probe and pre-filter (spec §The eight steps, steps 1-2; build phase P3). Deterministic, no model.

``run(request, ctx, cwd=None) -> probe`` scans the request text (and the workspace files it names) for:
action verbs, write targets, files named, connectors named, money and number tokens, quantity words,
decision-class keywords and personal data. ``words(ctx)`` gives the keyword lists: the ``words`` of the active locale
packs, then the owner's own extra words in the control file ``probe.json``.

- A verb of ``verbs`` counts toward ``irreversible_classes`` only when it is used as an instruction ("send the
  invoice", "please email Sara", "...and pay it"), not in a question ("did we send it?"); a verb of
  ``verbs_anywhere`` counts wherever it stands. Any verb at all still makes the pre-filter ask for a card.
- ``floor_tier``: T2 with an irreversible class, T1 with a write target or a write-capable connector,
  else T0.
- A named file is read only when its realpath is inside the workspace (and never through a symlink).

``prefilter(probe) -> "T0-fast" | "card-needed"``: the fast path only when the probe found none of: write
targets, files or connectors named, money or number tokens, quantity words, action verbs, decision-class
keywords. (Quantity words go beyond the spec's list: "how many orders…" computes a number, R-11.)
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path

from . import canon, locales, pii, protected, schema
from .errors import HarnessError

MAX_CHARS = 20000
CLASSES = ("external_message", "money", "destructive", "automation_create", "local_reversible")
LISTS = ("imperative_lead", "imperative_join", "question_lead", "money_words", "quantity_words", "continuation")
_FOLD = str.maketrans({"ي": "ی", "ك": "ک", "‌": "", "‍": "", "۰": "0", "۱": "1", "۲": "2", "۳": "3",
                       "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9", "٠": "0", "١": "1", "٢": "2",
                       "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"})


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).translate(_FOLD).casefold()


def _kw(word: str) -> str:
    """A keyword as a regex: whole words (letters of every script count as word characters); symbols as is."""
    w = re.escape(_norm(word))
    return w if not re.match(r"\w", _norm(word)[:1] or "x") else rf"(?<!\w){w}(?!\w)"


def _hits(text: str, words) -> list:
    return sorted({w for w in words if re.search(_kw(w), text)})


def _alt(words) -> str:
    return "|".join(re.escape(_norm(x)) for x in sorted(words, key=len, reverse=True))


def _imperative(text: str, verb: str, leads, joins) -> bool:
    """``verb`` at an instruction position: a sentence start or a join word, then any number of leads."""
    v = (rf"(?:(?:{_alt(leads)})\s+)*" if leads else "") + rf"{re.escape(_norm(verb))}(?!\w)"
    if re.search(rf"(?:^|[.!;?\n:]\s*){v}", text):
        return True
    return bool(joins) and bool(re.search(rf"(?<!\w)(?:{_alt(joins)})\s+{v}", text))


def _strings(v, name: str) -> list:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise HarnessError(f"probe.json: {name} must be a list of strings")
    return v


def _classes(live: dict, key: str) -> dict:
    v = live.get(key, {})
    if not isinstance(v, dict):
        raise HarnessError(f"probe.json: {key} must be an object of lists of strings")
    return {cls: _strings(x, f"{key}.{cls}") for cls, x in v.items()}


def words(ctx) -> dict:
    """The keyword lists in force: the ``words`` of the locale packs, then the entries of the live ``probe.json``
    (duplicates dropped, the first kept). An owner verb that a pack lists under ``verbs`` stays a position verb;
    any other owner verb is matched anywhere."""
    view = locales.view(ctx, every=True)
    w = view.get("words", {})
    live = protected.load_control("probe.json", ctx)
    if not isinstance(live, dict):
        raise HarnessError("probe.json: the file must hold an object")
    out = {k: list(dict.fromkeys(w.get(k, []) + _strings(live.get(k, []), k))) for k in LISTS}
    mine = _classes(live, "verbs")
    pos = {_norm(v) for vs in w.get("verbs", {}).values() for v in vs}
    for key, keep in (("verbs", True), ("verbs_anywhere", False)):
        pack = w.get(key, {})
        out[key] = {cls: list(dict.fromkeys(pack.get(cls, []) + [v for v in mine.get(cls, [])
                                                                 if (_norm(v) in pos) == keep]))
                    for cls in dict.fromkeys(CLASSES + tuple(pack) + tuple(mine))}
    dec, own = w.get("decision_keywords", {}), _classes(live, "decision_keywords")
    out["decision_keywords"] = {cls: list(dict.fromkeys(dec.get(cls, []) + own.get(cls, [])))
                                for cls in dict.fromkeys(list(dec) + list(own))}
    exts = live["file_extensions"] if "file_extensions" in live else \
        json.loads((protected.DEFAULTS_DIR / "probe.json").read_text("utf-8"))["file_extensions"]
    out["file_extensions"] = _strings(exts, "file_extensions")
    out["question_marks"] = list(view.get("question_marks", []))
    return out


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
    k = words(ctx)
    pos = {cls: _hits(text, ws) for cls, ws in k["verbs"].items()}
    anywhere = {cls: _hits(text, ws) for cls, ws in k["verbs_anywhere"].items()}
    action_verbs = sorted({v for d in (pos, anywhere) for vs in d.values() for v in vs})
    leads, joins = k["imperative_lead"], k["imperative_join"]
    imperative = {cls for cls, vs in anywhere.items() if vs}
    imperative |= {cls for cls, vs in pos.items() if any(_imperative(text, v, leads, joins) for v in vs)}
    irreversible = sorted(c for c in imperative if c not in ("local_reversible",))
    first = re.split(r"\s+", text.strip(), maxsplit=1)[0] if text.strip() else ""
    marks = tuple(m for m in map(_norm, k["question_marks"]) if m)  # a mark that folds to nothing ends every text
    asks = text.rstrip().endswith(marks) or first in {_norm(q) for q in k["question_lead"]}
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
    decision = {cls: hits for cls, ws in k["decision_keywords"].items() if (hits := _hits(text, ws))}
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
