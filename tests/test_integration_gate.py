"""End to end: harness ledger -> fold.project -> REAL fold_ledger.py under the REAL sandbox_exec -> receipt.

fold_ledger path: env HARNESS_FOLD_LEDGER, default the loop-contract checkout next to this repo.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from harness_core import canon, gate, sandbox_exec
from harness_core.errors import TamperError
from harness_core.events import EventLog
from harness_core.ledger import Ledger, Outputs
from harness_core.paths import job_paths

FOLD = os.environ.get(
    "HARNESS_FOLD_LEDGER",
    "/Users/sam/Project/loop-contract-skill/skills/loop-contract/scripts/fold_ledger.py",
)
KEY = b"k" * 32
PROFILE = {"allow_blocked": "units", "oracle_cmd": "/usr/bin/true", "oracle_timeout": 30,
           "verdict_vocab": "ANSWERED,ANSWERED-WITH-GAPS,UNANSWERED"}
U1 = {"id": "u1", "claim": "How many widgets shipped?", "label": "Widgets", "check_id": "dq.check",
      "deliverable": "answer.csv", "unit_type": "data_question"}
U2 = dict(U1, id="u2", claim="Which region led?", label="Region")

need_fold = pytest.mark.skipif(not os.path.isfile(FOLD), reason=f"fold_ledger.py not found at {FOLD}")
need_sandbox = pytest.mark.skipif(not (os.path.exists(sandbox_exec.SANDBOX_EXEC) or shutil.which("sandbox-exec")),
                                  reason="sandbox-exec absent")
pytestmark = need_fold


@pytest.fixture
def job(env):
    def make(units=(U1,)):
        p = job_paths(env["data"], env["ws"], "j1")
        log = EventLog(p.run, p.heads)
        led = Ledger(p, log)
        led.freeze_scope(list(units))
        p.work.mkdir(parents=True)
        (p.work / "answer.csv").write_text("widgets\n1234\n")
        return {"p": p, "log": log, "led": led, "out": Outputs(p, log)}
    return make


def _pass(h, uid="u1", values=None):
    o = h["out"].record("dq.check", uid, 1, 0, "pass", values or {"total": 1234}, f"{uid} ok".encode())
    h["led"].append_row("unit", {"id": uid, "attempt": 1, "verdict": "PASS", "outputs": [o["sha256"]]})


def _report(h, text="The total is 1,234 widgets.\n"):
    (h["p"].work / "report.md").write_text(text)


def _gate(h, profile=PROFILE):
    return gate.run_gate(h["p"], h["log"], KEY, profile, FOLD)


def _verify(h):
    return gate.verify_receipt(h["p"], h["log"], KEY)


@need_sandbox
def test_traced_report_is_checked(job):
    h = job()
    _pass(h)
    _report(h)
    res = _gate(h)
    assert res["ok"] is True, res
    assert res["receipt"]["mode"] == "done" and res["receipt"]["caps"] == []
    assert res["receipt"]["traced_set_sha"] == canon.sha256_json(["1234"])  # fold receipt's traced_values
    assert _verify(h) == {"ok": True, "state": "Checked", "caps": []}


@need_sandbox
def test_untraced_number_caps_the_state(job):
    h = job()
    _pass(h)
    _report(h, "The total is 9,999 widgets.\n")
    res = _gate(h)
    assert res["ok"] is True, res
    assert res["receipt"]["caps"] == ["numbers_untraced"]
    v = _verify(h)
    assert v["state"] == "Done, not fully checked" and v["caps"] == ["numbers_untraced"]


@need_sandbox
def test_two_fails_flag_and_gate_in_known_mode(job):
    h = job()
    for n in (1, 2):
        o = h["out"].record("dq.check", "u1", n, 1, "fail", {}, f"fail {n}".encode())
        h["led"].append_row("unit", {"id": "u1", "attempt": n, "verdict": "FAIL", "outputs": [o["sha256"]],
                                     "failure_sig": "row count mismatch"})
    assert h["led"].state("u1") == "PASS_FLAGGED"
    _report(h, "No figure yet.\n")
    res = _gate(h, profile={"allow_blocked": 0})  # known mode needs no oracle
    assert res["ok"] is True, res
    assert res["receipt"]["mode"] == "known"
    assert (h["p"].run / "receipt.json").exists()
    assert _verify(h)["state"] == "Done, not fully checked"


@need_sandbox
def test_blocked_unit_is_partly_done(job):
    h = job(units=(U1, U2))
    _pass(h)
    h["led"].append_row("unit", {"id": "u2", "attempt": 1, "verdict": "BLOCKED", "reason": "no data access"})
    _report(h)
    res = _gate(h)
    assert res["ok"] is True, res
    assert _verify(h)["state"] == "Partly done"


@need_sandbox
def test_edit_submitted_after_gate_fails_verify(job):
    h = job()
    _pass(h)
    _report(h)
    assert _gate(h)["ok"]
    (h["p"].run / "outputs" / "submitted" / "report.md").write_text("The total is 1,234 widgets!\n")
    assert _verify(h)["ok"] is False


@need_sandbox
def test_disallowed_event_after_receipt_fails_verify(job):
    h = job()
    _pass(h)
    _report(h)
    assert _gate(h)["ok"]
    h["log"].append("TURN", {})  # allowed
    assert _verify(h)["ok"] is True
    h["log"].append("SAFETY_DENY", {"tool": "Bash"})
    v = _verify(h)
    assert v["ok"] is False and "SAFETY_DENY" in v["reason"]


@need_sandbox
def test_stale_receipt_does_not_survive_a_failing_gate(job):
    h = job()
    _pass(h)
    _report(h)
    assert _gate(h)["ok"] and _verify(h)["ok"]
    res = _gate(h, profile=dict(PROFILE, oracle_cmd="/usr/bin/false"))
    assert res["ok"] is False and res["code"] == 1
    assert not (h["p"].run / "receipt.json").exists()
    assert h["log"].events()[-1]["type"] == "GATE_FAILED"
    assert _verify(h)["ok"] is False


@need_sandbox
def test_gate_child_cannot_read_keys(job):
    """The oracle is agent-influenced code: it must not read the HMAC key dir."""
    h = job()
    h["p"].keys.mkdir(parents=True)
    (h["p"].keys / "hmac.key").write_bytes(KEY)
    _pass(h)
    _report(h)
    res = _gate(h, profile=dict(PROFILE, oracle_cmd=f"/bin/cat {h['p'].keys / 'hmac.key'}"))
    assert res["ok"] is False, "gate child read the key"


def _verify_chain(h):
    return subprocess.run(["python3", FOLD, "--verify-chain", str(h["p"].run / "events.jsonl"), str(h["p"].heads)],
                          capture_output=True, text=True)


def test_fold_ledger_verify_chain_agrees_with_eventlog(job):
    h = job()
    _pass(h)
    head = h["log"].verify()
    r = _verify_chain(h)
    assert r.returncode == 0 and r.stdout.strip() == f"chain ok {head}", r.stderr
    ev = h["p"].run / "events.jsonl"
    raw = bytearray(ev.read_bytes())
    i = raw.index(b'"type":"ROW"') + len(b'"type":"')
    raw[i:i + 3] = b"RAW"  # one-byte-class edit inside an event body
    ev.write_bytes(bytes(raw))
    r = _verify_chain(h)
    assert r.returncode == 1 and "CHAIN FAILED" in r.stderr
    with pytest.raises(TamperError):
        h["log"].verify()


# ---- gate wiring: lexicon lint, required logs, claims ----


@need_sandbox
def test_lexicon_term_outside_template_line_caps_state(job):
    h = job()
    _pass(h)
    _report(h, "The total is 1,234 widgets. All figures verified.\n")
    res = _gate(h)
    assert res["ok"] is True, res
    assert res["receipt"]["caps"] == ["lexicon"]
    assert _verify(h) == {"ok": True, "state": "Done, not fully checked", "caps": ["lexicon"]}


INVESTIGATION = dict(PROFILE, task_type="investigation")


@need_sandbox
def test_investigation_without_logs_fails_gate(job):
    h = job()
    _pass(h)
    _report(h)
    res = _gate(h, profile=INVESTIGATION)
    assert res["ok"] is False and "required log" in res["stderr"], res
    h["led"].append_row("contradiction", {"id": "c1", "claim": "a", "note": "none found"})
    (h["p"].run / "search_log.jsonl").write_text('{"q": "widgets"}\n')
    assert _gate(h, profile=INVESTIGATION)["ok"] is True  # positive control: logs present -> passes


SNAP = b"Acme shipped 1,234 widgets in March, the company said.\n"


def _claims(h, quote):
    run, sha = h["p"].run, canon.sha256_bytes(SNAP)
    (run / "snapshots").mkdir(exist_ok=True)
    (run / "snapshots" / sha).write_bytes(SNAP)
    (run / "fetch_log.jsonl").write_text(json.dumps({"url": "https://news.example.ir/a", "snapshot_sha": sha,
                                                     "ts": "2026-11-01T00:00:00Z"}) + "\n")
    claim = {"claim": "1,234 widgets shipped", "claim_type": "fact", "load_bearing": False, "source_id": "s1",
             "url": "https://news.example.ir/a", "retrieved_at": "2026-11-01", "snapshot_sha": sha,
             "verbatim_quote": quote, "source_date": "2026-11-01", "source_class": "news", "status": "OK"}
    (run / "claims.jsonl").write_text(json.dumps(claim) + "\n")


@need_sandbox
def test_claim_quote_mismatch_fails_gate(job):
    h = job()
    _pass(h)
    _report(h)
    _claims(h, "Acme shipped 9,999 widgets")
    res = _gate(h)
    assert res["ok"] is False and "verbatim_quote" in res["stderr"], res


@need_sandbox
def test_good_claims_pass_gate(job):
    h = job()
    _pass(h)
    _report(h)
    _claims(h, "Acme shipped 1,234 widgets in March")
    res = _gate(h)
    assert res["ok"] is True, res
    assert _verify(h)["state"] == "Checked"
