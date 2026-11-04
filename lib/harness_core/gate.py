"""Gate run (submission, projection, sandboxed fold_ledger, HMAC receipt) and receipt verification."""
from __future__ import annotations

import importlib
import json
import os
import shutil
from pathlib import Path

from . import canon, fold, protected, schema
from .errors import HarnessError, TamperError
from .ledger import Ledger
from .safeio import atomic_write, safe_open

HMAC_FIELDS = ("events_head", "scope_sha", "tree_sha", "report_sha", "gate_sha", "control_hash",
               "business_hash", "core_hash", "mode", "caps_sha", "traced_set_sha")
AFTER_RECEIPT = {"RECEIPT", "MODEL_REGISTERED", "RECORD", "BUDGET_CHARGE", "TURN", "LEASE_HEARTBEAT", "CONTACT"}
GATE_FILES = ("ledger.jsonl", "verdicts.jsonl", "scope.jsonl", "profile.json", "lexicon.json", "fold_receipt.json")
ORACLE_TIMEOUT_MAX = 1790
SANDBOX_TIMEOUT = 1793  # > oracle cap, < the 1795 s gate watchdog
TEXT_SUFFIXES = {".md", ".txt", ".csv"}


def _files(root: Path) -> list:
    out = []
    for d, dirs, names in os.walk(root):
        for n in dirs + names:
            p = Path(d) / n
            if p.is_symlink():
                raise TamperError(f"symlink in tree: {p}")
        out += [Path(d) / n for n in names]
    return sorted(out)


def _submit(paths, log) -> str:
    sub = paths.run / "outputs" / "submitted"
    if sub.exists():
        shutil.rmtree(sub)
    sub.mkdir(parents=True)
    try:
        for f in _files(paths.work) if paths.work.exists() else []:
            atomic_write(sub / f.relative_to(paths.work), safe_open(f, [paths.work]))
    except TamperError as e:
        log.append("TAMPER", {"where": "submission", "reason": str(e)})
        raise
    tree = canon.tree_sha(_files(sub), sub)
    log.append("SUBMITTED", {"tree_sha": tree})
    return tree


def _sha_or_empty(path: Path, root: Path) -> str:
    return canon.sha256_bytes(safe_open(path, [root])) if path.exists() or path.is_symlink() else ""


def _gate_sha(paths) -> str:
    g = paths.run / "gate"
    return canon.sha256_json({n: _sha_or_empty(g / n, g) for n in GATE_FILES})


def _core_hash() -> str:
    here = Path(__file__).resolve().parent
    return canon.sha256_json({p.name: canon.sha256_bytes(p.read_bytes()) for p in sorted(here.glob("*.py"))})


def _live_fields(paths) -> dict:
    """Fields recomputed from disk at gate time and again at verify time."""
    g, sub = paths.run / "gate", paths.run / "outputs" / "submitted"
    return {
        "scope_sha": _sha_or_empty(g / "scope.jsonl", g),
        "tree_sha": canon.tree_sha(_files(sub), sub) if sub.exists() else canon.sha256_json([]),
        "report_sha": _sha_or_empty(g / "REPORT.md", g),
        "gate_sha": _gate_sha(paths),
        "core_hash": _core_hash(),
    }


def _flat(v) -> list:
    """state_lexicon.json keeps terms per language ({"en": [...], "fa": [...]}); the gate wants one list."""
    return [str(x) for x in (sum((list(x) for x in v.values()), []) if isinstance(v, dict) else list(v or []))]

def _lexicon(paths) -> Path:
    lex = protected.load_control("state_lexicon.json", paths)  # live control under data_root, else default
    out = paths.run / "gate" / "lexicon.json"
    atomic_write(out, canon.canonical_json({"terms": _flat(lex.get("terms")),
                                            "template_lines": _flat(lex.get("template_lines"))}))
    return out

def _argv(paths, proj: dict, profile: dict, fold_ledger_path, events_head: str) -> list:
    g, run = paths.run / "gate", paths.run
    argv = ["python3", str(fold_ledger_path), "--mode", proj["mode"],
            "--ledger", str(g / "ledger.jsonl"), "--verdicts", str(g / "verdicts.jsonl"),
            "--decisions", str(g / "decisions.jsonl"), "--out", str(g / "REPORT.md"),
            "--scope", str(g / "scope.jsonl"), "--expect-scope-sha", proj["scope_sha"],
            "--expect-scope-count", str(proj["scope_count"]), "--allow-blocked", str(proj["allow_blocked"]),
            "--group-by", "unit_type", "--repo-root", str(paths.run), "--receipt", str(g / "fold_receipt.json"),
            "--events-head", events_head]
    sub, outputs = paths.run / "outputs" / "submitted", paths.run / "outputs" / "outputs.jsonl"
    texts = sorted(str(f) for f in _files(sub) if f.suffix in TEXT_SUFFIXES) if sub.exists() else []
    if texts:  # spec §Gate adapter numbers_traced: report.md and every submitted text deliverable
        argv += ["--outputs", str(outputs) if outputs.exists() else os.devnull, "--numbers-trace", *texts]
    if profile.get("lint_lexicon", True):
        argv += ["--lint-lexicon", str(_lexicon(paths))]
    if (run / "answer_header.md").exists():
        argv += ["--include", str(run / "answer_header.md")]
    if (run / "claims.jsonl").exists():  # fold_ledger fails closed when the fetch log or snapshots are missing
        argv += ["--sources", str(run / "claims.jsonl"), "--fetch-log", str(run / "fetch_log.jsonl"),
                 "--snapshots", str(run / "snapshots"), "--profile", str(g / "profile.json")]
    if profile.get("require_logs") or profile.get("task_type") == "investigation":
        argv += ["--require-logs", str(run / "contradictions.jsonl"), str(run / "search_log.jsonl")]
    if profile.get("verdict_vocab"):
        argv += ["--verdict-vocab", profile["verdict_vocab"]]
    if proj["mode"] == "done":
        if not profile.get("oracle_cmd"):
            raise HarnessError("done mode needs an oracle_cmd in the gate profile")
        timeout = min(int(profile.get("oracle_timeout", ORACLE_TIMEOUT_MAX)), ORACLE_TIMEOUT_MAX)
        argv += ["--oracle-cmd", profile["oracle_cmd"], "--oracle-timeout", str(timeout)]
    return argv


def run_gate(paths, log, key: bytes, profile: dict, fold_ledger_path, runner=None) -> dict:
    if runner is None:
        runner = importlib.import_module(__package__ + ".sandbox_exec").run
    profile = dict(profile or {})
    stale = paths.run / "receipt.json"
    if stale.exists() or stale.is_symlink():  # an earlier receipt must not outlive a new gate attempt
        stale.unlink()
        log.append("RECEIPT_REVOKED", {}, file="receipt.json", file_sha="")
    _submit(paths, log)
    proj = fold.project(paths, Ledger(paths, log), profile=profile)
    argv = _argv(paths, proj, profile, fold_ledger_path, log.verify())
    receipt_path = paths.run / "gate" / "fold_receipt.json"
    if receipt_path.exists():
        receipt_path.unlink()
    code, _out, err = runner(argv, str(paths.run), None, timeout=SANDBOX_TIMEOUT,
                             deny_read=[str(paths.keys), str(paths.data_root / "experience")])
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    if code != 0:
        log.append("GATE_FAILED", {"code": code, "stderr": (err or "")[-2000:]})
        return {"ok": False, "code": code, "stderr": err}
    try:
        child = json.loads(safe_open(receipt_path, [paths.run / "gate"]))
    except (TamperError, ValueError) as e:
        log.append("GATE_FAILED", {"code": code, "stderr": f"no usable fold receipt: {e}"})
        return {"ok": False, "code": code, "stderr": str(e)}
    caps = sorted({str(c) for c in child.get("caps") or []})
    fields = dict(_live_fields(paths))
    fields.update(
        events_head=log.verify(),
        control_hash=str(profile.get("control_hash", "")),
        business_hash=str(profile.get("business_hash", "")),
        mode=proj["mode"],
        caps_sha=canon.sha256_json(caps),
        traced_set_sha=canon.sha256_json(child.get("traced_values") or []),
    )
    receipt = dict(fields, caps=caps, hmac=canon.hmac_receipt(key, fields))
    schema.check(receipt, "receipt")
    data = canon.canonical_json(receipt)
    atomic_write(paths.run / "receipt.json", data)
    log.append("RECEIPT", {"hmac": receipt["hmac"], "mode": receipt["mode"], "caps": caps},
               file="receipt.json", file_sha=canon.sha256_bytes(data))
    return {"ok": True, "code": 0, "receipt": receipt}


def _fail(reason: str) -> dict:
    return {"ok": False, "state": None, "caps": [], "reason": reason}


def verify_receipt(paths, log, key: bytes) -> dict:
    import hmac as _hmac

    try:
        path = paths.run / "receipt.json"
        if not path.exists():
            log.verify()
            return _fail("no receipt")
        fold.check_protected(paths, log)
        rec = json.loads(safe_open(path, [paths.run]))
        schema.check(rec, "receipt")
        evs = log.events()
        idx = next((i for i, e in enumerate(evs) if e["sha"] == rec["events_head"]), None)
        if idx is None:
            return _fail("receipt head not in event log")
        bad = sorted({e["type"] for e in evs[idx + 1:]} - AFTER_RECEIPT)
        if bad:
            return _fail(f"events after receipt invalidate it: {bad}")
        fields = {k: rec[k] for k in HMAC_FIELDS}
        fields.update(_live_fields(paths))
        fields["caps_sha"] = canon.sha256_json(rec["caps"])
        if not _hmac.compare_digest(canon.hmac_receipt(key, fields), rec["hmac"]):
            return _fail("receipt HMAC does not match current state")
        g = paths.run / "gate"
        rows = [json.loads(x) for x in safe_open(g / "ledger.jsonl", [g]).splitlines()]
        count = len(safe_open(g / "scope.jsonl", [g]).splitlines())
    except (HarnessError, ValueError, KeyError) as e:
        return _fail(str(e))
    if any(r.get("verdict") == "BLOCKED" for r in rows) or len(rows) < count:
        state = "Partly done"
    elif rec["mode"] == "known" or rec["caps"]:
        state = "Done, not fully checked"
    else:
        state = "Checked"
    return {"ok": True, "state": state, "caps": rec["caps"]}
