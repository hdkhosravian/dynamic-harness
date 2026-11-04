"""Research pack checks (spec §Business investigations "Claim ledger", "Report"; R-13; build phase P5).

- ``check``: the claims a unit cites (``cites.jsonl``, written by ``harness-claims cite``) each rest on a snapshot
  this run fetched, quote it verbatim, are fresh enough for their type and not UNREACHABLE; the page was not
  flagged for embedded instructions; the load-bearing ones come from at least two independent origins
  (distinct registrable domain and distinct text); and a verifier verdict was recorded for the unit. Confidence
  (High / Medium / Low) is computed here from source count, class and freshness.
- ``report``: every line under the report's findings cites a claim row (``[claim N]``) or is labelled
  "model knowledge, not checked"; every cited row exists.

The domain rule matches loop-contract's ``check_claims`` (last two labels, three under a two-letter country
code with a short second level), so the pack and the gate agree on what "independent" means.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

SECOND_LEVEL = {"co", "com", "org", "ac", "gov", "net", "edu"}
FRESHNESS = {"price": "30d", "law": "in_force_date", "default": "365d"}
UNCHECKED = "model knowledge, not checked"
_CITE = re.compile(r"\[claims?\s+(\d+(?:\s*,\s*\d+)*)\]", re.I)


def registrable_domain(url) -> str:
    host = (urlsplit(url or "").hostname or "").lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and (len(labels[-2]) == 2 or labels[-2] in SECOND_LEVEL):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def squash(text) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def _fresh(claim, rules, today):
    """None when fresh enough, else why not."""
    rule = rules.get(claim.get("claim_type"), rules.get("default"))
    if rule == "in_force_date":
        return None if claim.get("in_force_date") else f"a {claim.get('claim_type')} claim needs an in-force date"
    m = re.fullmatch(r"(\d+)d", str(rule or ""))
    if not m:
        return f"freshness rule {rule!r} is not understood"
    try:
        d = dt.date.fromisoformat(str(claim.get("source_date") or claim.get("date"))[:10])
    except ValueError:
        return "the source has no date, so its freshness is unknown"
    age = (today - d).days
    return f"the source is {age} days old; {claim.get('claim_type') or 'this'} claims allow {rule}" \
        if age > int(m.group(1)) else None


def check(req):
    unit = req["unit"]["id"]
    run = Path(req.get("run", "run"))
    today = dt.date.fromisoformat(req.get("today") or dt.date.today().isoformat())
    rules = dict(FRESHNESS)  # fixed: the agent cannot relax freshness through args
    claims = _jsonl(run / "claims.jsonl")
    fetched = {r.get("snapshot_sha"): r for r in _jsonl(run / "fetch_log.jsonl")}
    cites = [c for c in _jsonl(run / "cites.jsonl") if c.get("unit") == unit]
    notes, fail = [], False
    if not cites:
        return "fail", {"claims_cited": 0}, [f"{unit} cites no claim rows (harness-claims cite)"]
    cite = cites[-1]
    lines, bearing = cite.get("claims") or [], set(cite.get("load_bearing") or [])
    if not bearing:
        notes.append(f"{unit} names no load-bearing claim: mark the rows that decide it (--load-bearing)")
        fail = True
    for n in lines:  # a price or law claim that is cited always carries weight
        if 1 <= n <= len(claims) and claims[n - 1].get("claim_type") in ("price", "law") and n not in bearing:
            notes.append(f"claim {n} is a {claims[n - 1]['claim_type']} claim: it must be load-bearing")
            fail = True
    origins, classes, fresh_all = [], set(), True
    for n in lines:
        if not 1 <= n <= len(claims):
            notes.append(f"claim {n} does not exist")
            fail = True
            continue
        c = claims[n - 1]
        tag = f"claim {n} ({registrable_domain(c.get('url'))})"
        snap = c.get("snapshot_sha") or ""
        f = fetched.get(snap)
        if not re.fullmatch(r"[0-9a-f]{64}", snap) or f is None:
            notes.append(f"{tag}: its page was not fetched by this task")
            fail = True
            continue
        if c.get("status") == "UNREACHABLE" or f.get("status") == "UNREACHABLE":
            notes.append(f"{tag}: the source was unreachable")
            fail = True
            continue
        if f.get("injection_suspect"):
            notes.append(f"{tag}: the page contained instructions aimed at the assistant; its claims are not used")
            fail = True
            continue
        path = run / "snapshots" / snap
        raw = path.read_bytes() if path.is_file() else None
        if raw is None or hashlib.sha256(raw).hexdigest() != snap:
            notes.append(f"{tag}: the stored page is missing or altered")
            fail = True
            continue
        quote = squash(c.get("verbatim_quote") or c.get("quote") or "")
        if not quote or quote not in squash(raw.decode("utf-8", "replace")):
            notes.append(f"{tag}: the quote is not on the page")
            fail = True
        why = _fresh(c, rules, today)
        if why:
            notes.append(f"{tag}: {why}")
            fresh_all = False
            fail = True
        classes.add(c.get("source_class") or "unknown")
        if n in bearing:
            origins.append((registrable_domain(c.get("url")), hashlib.sha256(squash(raw.decode("utf-8", "replace"))
                                                                              .encode("utf-8")).hexdigest()))
    independent = len({o[0] for o in origins}) >= 2 and len({o[1] for o in origins}) >= 2 and any(
        a[0] != b[0] and a[1] != b[1] for i, a in enumerate(origins) for b in origins[i + 1:])
    if bearing and not independent:
        notes.append("the load-bearing claims have fewer than 2 independent sources (copies of one article count once)")
        fail = True
    renders = {r.get("prompt_sha") for r in _jsonl(run / "renders.jsonl")
               if r.get("kind") == "verifier" and r.get("unit") == unit}
    verdicts = [s for s in _jsonl(run / "subagents.jsonl")
                if s.get("agent_type") == "dynamic-harness:verifier" and s.get("prompt_sha") in renders]
    if not verdicts:
        notes.append(f"no verifier verdict is recorded for {unit} (harness-critic render, then the verifier)")
        fail = True
    elif not verdicts[-1].get("answers") or "no" in verdicts[-1]["answers"]:
        notes.append(f"the verifier did not answer yes to every criterion for {unit}: the sources may not support "
                     "the claim")
        fail = True
    contra = [r for r in _jsonl(run / "contradictions.jsonl") if unit in json.dumps(r)]
    if contra:
        notes.append(f"{len(contra)} contradiction(s) are recorded for {unit}; the report must show them")
    sources = len({o[0] for o in origins}) if origins else len({registrable_domain(claims[n - 1].get("url"))
                                                                  for n in lines if 1 <= n <= len(claims)})
    if independent and fresh_all and "primary" in classes:
        confidence = "High"
    elif independent or "primary" in classes:
        confidence = "Medium"
    else:
        confidence = "Low"
    values = {"claims_cited": len(lines), "load_bearing": len(bearing), "independent_sources": sources,
              "confidence": confidence, "contradictions": len(contra)}
    return ("fail" if fail else "pass"), values, notes


def report(req):
    """Every findings line cites claim rows or carries the unchecked label (R-13)."""
    a = req.get("args") or {}
    from .checklib import safe_path
    path = safe_path(req.get("work", "work"), a.get("report", "report.md"))
    claims = _jsonl(Path(req.get("run", "run")) / "claims.jsonl")
    text = path.read_text("utf-8")
    notes, fail, cited, unchecked = [], False, set(), 0
    section = a.get("section", "findings").casefold()
    inside = False
    for ln, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            inside = section in line.casefold()
            continue
        if not inside or not line.strip():
            continue
        refs = [int(x) for m in _CITE.finditer(line) for x in m.group(1).split(",")]
        if not refs:
            if UNCHECKED in line.casefold():
                unchecked += 1
                continue
            notes.append(f"line {ln} states a finding with no claim row: cite [claim N] or label it "
                         f"\"{UNCHECKED}\"")
            fail = True
        for n in refs:
            if not 1 <= n <= len(claims):
                notes.append(f"line {ln} cites claim {n}, which does not exist")
                fail = True
            cited.add(n)
    if not cited and not unchecked:
        notes.append(f"the report has no '{section}' lines")
        fail = True
    return ("fail" if fail else "pass"), {"claims_referenced": len(cited), "unchecked_lines": unchecked}, notes
