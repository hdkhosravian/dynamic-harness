"""Gate run (submission, projection, sandboxed fold_ledger, HMAC receipt) and receipt verification.

Trust boundary: fold_ledger and the oracle it spawns are agent-influenced code. The
child runs under sandbox_exec with a temp copy of run/ as its cwd (the only tree it may
write) and run/ itself denied for writes. After it exits the harness re-hashes run/ and
the copy against the pre-run hashes, checks run/ against the SUBMITTED tree_sha and the
PROJECTED gate-file hashes, allows only receipt-safe events after the head it passed,
and requires the child receipt to echo the mode, scope_sha and events_head it was given.
Any mismatch is a TAMPER event, ok False and no receipt. verify_receipt derives the
user-facing state from event-backed ledger rows, never from gate/ files.

User-facing states (R-10): "Checked", "Done, not fully checked: <reasons>",
"Partly done: N of M"."""
from __future__ import annotations

import importlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from . import canon, fold, protected, sandbox_exec, schema
from .errors import HarnessError, TamperError
from .ledger import ROW_SCHEMA, Ledger
from .safeio import atomic_write, safe_open

HMAC_FIELDS = ("events_head", "scope_sha", "tree_sha", "report_sha", "gate_sha", "control_hash",
               "business_hash", "core_hash", "mode", "caps_sha", "traced_set_sha")
# CONTACT only when question-free status (payload question_free: true); a denied call does not undo the work
AFTER_RECEIPT = {"RECEIPT", "MODEL_REGISTERED", "RECORD", "BUDGET_CHARGE", "TURN", "LEASE_HEARTBEAT", "CONTACT",
                 "SAFETY_DENY"}
GATE_FILES = ("ledger.jsonl", "verdicts.jsonl", "scope.jsonl", "profile.json", "decisions.jsonl", "lexicon.json",
              "fold_receipt.json")
CHILD_OUT = ("gate/REPORT.md", "gate/fold_receipt.json")
NOT_COPIED = {"receipt.json", "events.jsonl", "events.jsonl.lock", *CHILD_OUT}
ORACLE_TIMEOUT_MAX = 1790
SANDBOX_TIMEOUT = 1793  # > oracle cap, < the 1795 s gate watchdog
TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".html", ".tsv"}  # compared lower-cased
NOT_DONE = {"BLOCKED", "NOT_INVESTIGATED", "FAIL", "OPEN"}


def after_receipt_ok(e: dict) -> bool:
    return e["type"] in AFTER_RECEIPT and (e["type"] != "CONTACT" or e["payload"].get("question_free") is True)


def _files(root: Path) -> list:
    out = []
    for d, dirs, names in os.walk(root):
        for n in dirs + names:
            p = Path(d) / n
            if p.is_symlink():
                raise TamperError(f"symlink in tree: {p}")
        out += [Path(d) / n for n in names]
    return sorted(out)


def _literal_dir(paths, target):
    """``target`` (under the workspace) as a literal path: no symlink in any component; None if absent."""
    cur = Path(os.path.realpath(paths.workspace))
    for part in Path(target).relative_to(paths.workspace).parts:
        cur = cur / part
        if os.path.islink(cur):
            raise TamperError(f"symlink in the work path: {cur}")
        if not os.path.lexists(cur):
            return None
    if os.path.realpath(cur) != str(cur) or not cur.is_dir():
        raise TamperError(f"work path is not the expected directory: {cur}")
    return cur


def _submit(paths, log) -> str:
    sub = paths.run / "outputs" / "submitted"
    if sub.exists():
        shutil.rmtree(sub)
    sub.mkdir(parents=True)
    try:
        _literal_dir(paths, paths.requests)  # never read here, but a symlinked requests/ is tamper too
        work = _literal_dir(paths, paths.work)
        for f in _files(work) if work else []:
            atomic_write(sub / f.relative_to(work), safe_open(f, [work]))
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


def _tree(root: Path) -> str:
    return canon.tree_sha(_files(root), root) if root.is_dir() else canon.sha256_json([])


def _hashes(root: Path, skip=()) -> dict:
    out = {}
    for f in _files(root):
        rel = f.relative_to(root).as_posix()
        if rel not in skip:
            out[rel] = canon.sha256_bytes(safe_open(f, [root]))
    return out


def _live_fields(paths) -> dict:
    """Fields recomputed from disk at gate time and again at verify time."""
    g = paths.run / "gate"
    return {
        "scope_sha": _sha_or_empty(g / "scope.jsonl", g),
        "tree_sha": _tree(paths.run / "outputs" / "submitted"),
        "report_sha": _sha_or_empty(g / "REPORT.md", g),
        "gate_sha": _gate_sha(paths),
        "core_hash": _core_hash(),
        "control_hash": _tree(paths.data_root / "control"),
        "business_hash": _tree(paths.data_root / "business"),
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

def _argv(run: Path, proj: dict, profile: dict, fold_ledger_path, events_head: str) -> list:
    """argv for fold_ledger over ``run`` (the child's temp copy of run/)."""
    g = run / "gate"
    argv = ["/usr/bin/python3", "-I", str(fold_ledger_path), "--mode", proj["mode"],
            "--ledger", str(g / "ledger.jsonl"), "--verdicts", str(g / "verdicts.jsonl"),
            "--decisions", str(g / "decisions.jsonl"), "--out", str(g / "REPORT.md"),
            "--scope", str(g / "scope.jsonl"), "--expect-scope-sha", proj["scope_sha"],
            "--expect-scope-count", str(proj["scope_count"]), "--allow-blocked", str(proj["allow_blocked"]),
            "--group-by", "unit_type", "--repo-root", str(run), "--receipt", str(g / "fold_receipt.json"),
            "--events-head", events_head]
    sub, outputs = run / "outputs" / "submitted", run / "outputs" / "outputs.jsonl"
    texts = sorted(str(f) for f in _files(sub) if f.suffix.lower() in TEXT_SUFFIXES) if sub.exists() else []
    if texts:  # spec §Gate adapter numbers_traced: report.md and every submitted text deliverable
        argv += ["--outputs", str(outputs) if outputs.exists() else os.devnull, "--numbers-trace", *texts]
    if profile.get("lint_lexicon", True):
        argv += ["--lint-lexicon", str(g / "lexicon.json")]
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


def _child_problem(paths, log, box: Path, before: dict, head: str, proj: dict):
    """Why the child's run cannot be signed (None when it can); TamperError counts as a problem."""
    evs = log.events()
    tree = next(e for e in reversed(evs) if e["type"] == "SUBMITTED")["payload"]["tree_sha"]
    files = next(e for e in reversed(evs) if e["type"] == "PROJECTED")["payload"]["files"]
    if _hashes(paths.run, NOT_COPIED) != before:
        return "run/ changed while the gate child ran"
    if _tree(paths.run / "outputs" / "submitted") != tree:
        return "outputs/submitted differs from its SUBMITTED event"
    bad = sorted(k for k, v in files.items() if before.get(k) != v)
    if bad:
        return f"{bad} differ from the PROJECTED event"
    copy = _hashes(box)
    bad = sorted(k for k, v in before.items() if copy.get(k) != v)
    if bad:
        return f"gate child changed its inputs: {bad[:5]}"
    idx = next((i for i, e in enumerate(evs) if e["sha"] == head), None)
    if head and idx is None:
        return "event head passed to the gate child is gone"
    bad = sorted({e["type"] for e in evs[(idx if head else -1) + 1:] if not after_receipt_ok(e)})
    if bad:
        return f"events appended while the gate child ran: {bad}"
    return None


def run_gate(paths, log, key: bytes, profile: dict, fold_ledger_path, runner=None) -> dict:
    if runner is None:
        runner = importlib.import_module(__package__ + ".sandbox_exec").run
    profile = dict(profile or {})
    stale = paths.run / "receipt.json"
    if stale.exists() or stale.is_symlink():  # an earlier receipt must not outlive a new gate attempt
        stale.unlink()
        log.append("RECEIPT_REVOKED", {}, file="receipt.json", file_sha="")
    if profile.get("task_type") == "investigation" and not (paths.run / "claims.jsonl").is_file():
        raise HarnessError("investigation gate refused: run/claims.jsonl is missing")
    _submit(paths, log)
    proj = fold.project(paths, Ledger(paths, log), profile=profile)
    for name in CHILD_OUT:
        if (paths.run / name).exists():
            (paths.run / name).unlink()
    _lexicon(paths)
    head = log.verify()
    before = _hashes(paths.run, NOT_COPIED)
    box = Path(os.path.realpath(tempfile.mkdtemp(prefix="harness-gate-")))
    try:
        for rel in before:
            (box / rel).parent.mkdir(parents=True, exist_ok=True)
            (box / rel).write_bytes(safe_open(paths.run / rel, [paths.run]))
        argv = _argv(box, proj, profile, fold_ledger_path, head)
        # the whole data root (keys, experience, lock files) and every installed plugin's secrets
        data = os.path.realpath(paths.data_root)
        deny_read = [data] + [os.path.realpath(d) for d in sandbox_exec.secret_dirs(data, Path.home())]
        code, _out, err = runner(argv, str(box), None, timeout=SANDBOX_TIMEOUT, deny_read=deny_read,
                                 deny_write=[os.path.realpath(paths.run)])
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        if code != 0:
            log.append("GATE_FAILED", {"code": code, "stderr": (err or "")[-2000:]})
            return {"ok": False, "code": code, "stderr": err}
        try:
            problem = _child_problem(paths, log, box, before, head, proj)
        except TamperError as e:
            problem = str(e)
        if problem is None:
            try:
                child = json.loads(safe_open(box / "gate" / "fold_receipt.json", [box]))
                report = safe_open(box / "gate" / "REPORT.md", [box])
            except (TamperError, ValueError) as e:
                log.append("GATE_FAILED", {"code": code, "stderr": f"no usable fold receipt: {e}"})
                return {"ok": False, "code": code, "stderr": str(e)}
            want = {"mode": proj["mode"], "scope_sha": proj["scope_sha"], "events_head": head}
            lied = sorted(k for k, v in want.items() if not isinstance(child, dict) or child.get(k) != v)
            problem = f"fold receipt does not echo the gate's {lied}" if lied else None
        if problem:
            log.append("TAMPER", {"where": "gate child", "reason": problem})
            return {"ok": False, "code": code, "stderr": problem}
        atomic_write(paths.run / "gate" / "REPORT.md", report)
        atomic_write(paths.run / "gate" / "fold_receipt.json", canon.canonical_json(child))
    finally:
        shutil.rmtree(box, ignore_errors=True)
    caps = sorted({str(c) for c in child.get("caps") or []})
    fields = dict(_live_fields(paths))
    fields.update(
        events_head=head,
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


def _state(paths, log, rec: dict) -> str:
    """R-10 state line from event-backed ledger rows (the receipt head bounds them: no ROW may follow it)."""
    led = Ledger(paths, log)
    units = led.sanctioned_scope()
    latest = {}
    for kind, row in led.rows():
        if kind in ROW_SCHEMA:
            latest[row["id"]] = row
    done = [u for u in units if latest.get(u["id"], {}).get("verdict", "OPEN") not in NOT_DONE]
    if len(done) < len(units):
        return f"Partly done: {len(done)} of {len(units)}"
    reasons = list(rec["caps"])
    for u in units:
        g = fold._map(latest[u["id"]], u)
        if g and g["verdict"] == "PARTIAL":
            reasons.append(g["missing"])
    if rec["mode"] == "known" and not reasons:
        reasons.append("known mode")
    return "Done, not fully checked: " + "; ".join(reasons) if reasons else "Checked"


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
        bad = sorted({e["type"] for e in evs[idx + 1:] if not after_receipt_ok(e)})
        if bad:
            return _fail(f"events after receipt invalidate it: {bad}")
        fields = {k: rec[k] for k in HMAC_FIELDS}
        fields.update(_live_fields(paths))
        fields["caps_sha"] = canon.sha256_json(rec["caps"])
        if not _hmac.compare_digest(canon.hmac_receipt(key, fields), rec["hmac"]):
            return _fail("receipt HMAC does not match current state")
        state = _state(paths, log, rec)
    except (HarnessError, ValueError, KeyError) as e:
        return _fail(str(e))
    return {"ok": True, "state": state, "caps": rec["caps"]}
