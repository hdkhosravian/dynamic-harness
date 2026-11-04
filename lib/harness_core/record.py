"""Decision record (R-15; spec §Business governance "Decision record"; build phase P4).

``write(ctx, job, state, interim=False)`` appends one row to ``${CLAUDE_PLUGIN_DATA}/records/<job>.jsonl``
(a ``RECORD`` event in the session log). It is assembled from existing artefacts only:

- the request (redacted) and its hash, and the requester;
- the card versions with their provenance; the tier;
- the assumptions: card fields whose provenance is ``inferred`` (never prose);
- checks run and not run: the latest verdict per scoped unit;
- approvals: accepted plan contacts and recorded ``APPROVAL`` events;
- harness, pack, control and memory versions; the cost (``budget.json``);
- the gate receipt's mode, caps and HMAC; the REPORT hash and its headings (structured sections only).

Every string goes through ``pii.redact``, so a record never holds personal data values; deleting a job
never deletes its record. Terminal states write a final row; resumable stops write ``interim`` rows.
"""
from __future__ import annotations

import glob
import os
import re

from . import canon, pii, store
from .events import EventLog, now
from .ledger import ROW_SCHEMA, Ledger
from .paths import job_paths
from .safeio import safe_open


def _redact(v):
    if isinstance(v, str):
        return pii.redact(v)
    if isinstance(v, dict):
        return {k: _redact(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact(x) for x in v]
    return v


def _cards(p, log) -> list:
    out = []
    for f in sorted(glob.glob(str(p.run / "card.v*.json")), key=lambda x: int(re.search(r"v(\d+)", x).group(1))):
        c = store.read_json(p.run, log, os.path.basename(f), None)
        if isinstance(c, dict):
            out.append(c)
    return out


def write(ctx, job, state, interim=False) -> dict:
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    cards = _cards(p, log) if p.run.is_dir() else []
    last = cards[-1] if cards else {}
    tier = (store.read_json(p.run, log, "tier.json", None) or {}).get("tier") if p.run.is_dir() else None
    checks, approvals = [], []
    if p.run.is_dir():
        led = Ledger(p, log)
        latest = {}
        for kind, row in led.rows():
            if kind in ROW_SCHEMA:
                latest[row["id"]] = row
        for u in led.scope():
            r = latest.get(u["id"], {})
            checks.append({"unit": u["id"], "label": u.get("label"), "verdict": r.get("verdict"),
                           "check_id": u.get("check_id")})
        contacts = store.read_json(p.run, log, "contacts.json", None) or {}
        approvals += [{"kind": "plan", "contact_id": r["contact_id"], "turn_id": r.get("accepted_turn"),
                       "payload_hash": r.get("plan_sha")} for r in contacts.get("rows", []) if r.get("accepted")]
        approvals += [{"kind": e["payload"].get("kind"), "ts": e.get("ts"), "payload": e["payload"]}
                      for e in log.events() if e["type"] == "APPROVAL"]
    receipt = store.read_json(p.run, log, "receipt.json", None) if (p.run / "receipt.json").exists() else None
    report = p.run / "gate" / "REPORT.md"
    report_sha, headings = None, []
    if report.is_file():
        data = safe_open(report, [p.run])
        report_sha = canon.sha256_bytes(data)
        headings = [m.strip() for m in re.findall(r"(?m)^#{1,6} (.+)$", data.decode("utf-8", "replace"))]
    budget = store.read_json(p.run, log, "budget.json", None) if p.run.is_dir() else None
    tier_row = (store.read_json(p.run, log, "tier.json", None) or {}) if p.run.is_dir() else {}
    duration = None
    if tier_row.get("ts"):
        import calendar
        import time
        duration = int(time.time() - calendar.timegm(time.strptime(tier_row["ts"][:19], "%Y-%m-%dT%H:%M:%S")))
    row = {
        "schema": "harness/record@1", "job_id": job, "state": state, "interim": bool(interim), "ts": now(),
        "request": last.get("request", ""), "request_sha": canon.sha256_bytes(last.get("request", "").encode("utf-8")),
        "requester": last.get("requester", "owner"),
        "cards": cards, "tier": tier,
        "assumptions": [{"field": k, "value": last.get(k)} for k, v in (last.get("provenance") or {}).items()
                        if v == "inferred"],
        "checks": checks, "approvals": approvals,
        "versions": dict(last.get("versions") or {}, business_hash=last.get("business_hash")),
        "cost": {"calls": (budget or {}).get("calls"), "money": (budget or {}).get("money")},
        "duration_s": duration, "task_type": last.get("task_type"),
        "receipt": {k: receipt.get(k) for k in ("mode", "caps", "hmac")} if isinstance(receipt, dict) else None,
        "report_sha": report_sha, "report_sections": headings,
    }
    row = _redact(row)
    store.append_jsonl(ctx.data_root, store.session_log(ctx.data_root), f"records/{job}.jsonl", row, "RECORD",
                       {"job_id": job, "state": state, "interim": bool(interim)})
    return row
