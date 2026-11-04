"""End to end: harness ledger -> fold.project -> REAL fold_ledger.py under the REAL sandbox_exec -> receipt.

fold_ledger path: env HARNESS_FOLD_LEDGER, default the loop-contract checkout next to this repo.
HARNESS_REQUIRE_INTEGRATION=1 turns every "fold_ledger missing / too old" skip into a failure (CI).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import subprocess
from pathlib import Path

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

REQUIRE = os.environ.get("HARNESS_REQUIRE_INTEGRATION") == "1"
FLAGS_1_1_0 = ("--receipt", "--events-head", "--sources", "--fetch-log", "--snapshots", "--numbers-trace",
               "--outputs", "--require-logs", "--include", "--lint-lexicon", "--verify-chain", "--oracle-timeout")


def fold_ledger_problem(path=FOLD):
    """None when fold_ledger is present and speaks every 1.1.0 flag the gate passes, else why not."""
    if not os.path.isfile(path):
        return f"fold_ledger.py not found at {path}"
    r = subprocess.run(["python3", path, "--help"], capture_output=True, text=True, timeout=30)
    missing = [f for f in FLAGS_1_1_0 if f not in r.stdout]
    return f"fold_ledger at {path} lacks 1.1.0 flags {missing}" if r.returncode or missing else None


@pytest.fixture
def visible_dir(tmp_path):
    """A directory the gate child can read. The Linux sandbox hides the host /tmp (X11, ssh-agent
    sockets), and pytest's tmp_path lives there; production oracles live in the project."""
    if sandbox_exec.backend() != "bwrap" or not str(tmp_path).startswith("/tmp/"):
        yield tmp_path
        return
    import pwd  # the account's home, not $HOME (conftest points $HOME at a temp dir under /tmp)
    d = Path(tempfile.mkdtemp(prefix="harness-test-", dir=pwd.getpwuid(os.getuid()).pw_dir))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


PROBLEM = fold_ledger_problem()
need_fold = pytest.mark.skipif(PROBLEM is not None and not REQUIRE, reason=str(PROBLEM))
need_sandbox = pytest.mark.skipif(sandbox_exec.probe() is not None, reason="no working OS sandbox")
pytestmark = need_fold


def test_fold_ledger_is_present_and_1_1_0():
    assert PROBLEM is None, PROBLEM


def test_missing_fold_ledger_is_reported(tmp_path):
    assert "not found" in fold_ledger_problem(str(tmp_path / "nope.py"))
    old = tmp_path / "old.py"
    old.write_text("import argparse; argparse.ArgumentParser().parse_args()\n")
    assert "lacks 1.1.0 flags" in fold_ledger_problem(str(old))


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
    assert v["state"] == "Done, not fully checked: numbers_untraced" and v["caps"] == ["numbers_untraced"]


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
    assert _verify(h)["state"].startswith("Done, not fully checked: ") and "row count mismatch" in _verify(h)["state"]


@need_sandbox
def test_blocked_unit_is_partly_done(job):
    h = job(units=(U1, U2))
    _pass(h)
    h["led"].append_row("unit", {"id": "u2", "attempt": 1, "verdict": "BLOCKED", "reason": "no data access"})
    _report(h)
    res = _gate(h)
    assert res["ok"] is True, res
    assert _verify(h)["state"] == "Partly done: 1 of 2"


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
    h["log"].append("SAFETY_DENY", {"tool": "Bash"})  # a denied call does not undo the work
    assert _verify(h)["ok"] is True
    h["log"].append("TAMPER", {"where": "x"})
    v = _verify(h)
    assert v["ok"] is False and "TAMPER" in v["reason"]


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
    assert _verify(h) == {"ok": True, "state": "Done, not fully checked: lexicon", "caps": ["lexicon"]}


INVESTIGATION = dict(PROFILE, task_type="investigation")


@need_sandbox
def test_investigation_without_logs_fails_gate(job):
    h = job()
    _pass(h)
    _report(h)
    _claims(h, "Acme shipped 1,234 widgets in March")
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


# ---- the gate child cannot rewrite run/ or launder its own inputs (finding 1, real fold_ledger + sandbox) ----

EVIL = r"""
import json, pathlib, sys
run = pathlib.Path(sys.argv[1])
hits = []
for rel in ("gate/ledger.jsonl", "outputs/submitted/answer.csv", "gate/fold_receipt.json"):
    try:
        (run / rel).write_text("forged\n")
        hits.append(rel)
    except OSError:
        pass
if sys.argv[2] == "copy":  # its own cwd is the temp copy: launder the BLOCKED row there
    g = pathlib.Path("gate/ledger.jsonl")
    g.write_text(g.read_text().replace('"BLOCKED"', '"PASS"'))
print("wrote", hits)
"""


@need_sandbox
@pytest.mark.parametrize("where", ["run", "copy"])
def test_oracle_cannot_launder_the_gate(job, tmp_path, where, visible_dir):
    h = job(units=(U1, U2))
    _pass(h)
    h["led"].append_row("unit", {"id": "u2", "attempt": 1, "verdict": "BLOCKED", "reason": "no data access"})
    _report(h)
    evil = visible_dir / "evil.py"
    evil.write_text(EVIL)
    run = h["p"].run
    res = _gate(h, profile=dict(PROFILE, oracle_cmd=f"/usr/bin/python3 -I {evil} {run} {where}"))
    projected = [e for e in h["log"].events() if e["type"] == "PROJECTED"][-1]["payload"]["files"]
    assert canon.sha256_bytes((run / "gate" / "ledger.jsonl").read_bytes()) == projected["gate/ledger.jsonl"]
    assert (run / "outputs" / "submitted" / "answer.csv").read_bytes() == (h["p"].work / "answer.csv").read_bytes()
    fold_receipt = run / "gate" / "fold_receipt.json"
    assert not fold_receipt.exists() or b"forged" not in fold_receipt.read_bytes()
    if where == "run":  # every write into run/ was denied; the honest state survives
        assert res["ok"] is True, res
        assert _verify(h)["state"] == "Partly done: 1 of 2"
    else:
        assert res["ok"] is False and not (run / "receipt.json").exists()
        assert h["log"].events()[-1]["type"] == "TAMPER"
