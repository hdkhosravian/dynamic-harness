from __future__ import annotations

import json
import sys
import types

import pytest

from harness_core import canon, gate
from harness_core.errors import HarnessError, TamperError
from harness_core.events import EventLog
from harness_core.ledger import Ledger, Outputs
from harness_core.paths import job_paths
from harness_core.turns import Turns

KEY = b"k" * 32
SCOPE = [
    {"id": "u1", "claim": "a", "label": "Alpha", "check_id": "c.a", "deliverable": "a.csv"},
    {"id": "u2", "claim": "b", "label": "Beta", "check_id": "c.b", "deliverable": "b.csv"},
]
PROFILE = {"allow_blocked": "units", "oracle_cmd": "python3 oracle.py", "oracle_timeout": 5000,
           "verdict_vocab": "ANSWERED,ANSWERED-WITH-GAPS,UNANSWERED", "control_hash": "ctl", "business_hash": "biz"}


class FakeRunner:
    """Stands in for sandboxed fold_ledger: writes REPORT.md and the --receipt JSON."""

    def __init__(self, code=0, caps=()):
        self.code, self.caps, self.calls = code, list(caps), []

    def __call__(self, argv, cwd, stdin, **kw):
        self.calls.append(argv)
        self.kw = kw
        a = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("--")}
        if self.code == 0:
            with open(a["--out"], "w") as f:
                f.write("# REPORT\nverdict: ANSWERED\n")
            with open(a["--receipt"], "w") as f:
                json.dump({"exit": 0, "caps": self.caps, "traced_values": ["7", "42"]}, f)
        return self.code, "", "" if self.code == 0 else "gate failed"


@pytest.fixture
def h(env):
    p = job_paths(env["data"], env["ws"], "j1")
    log = EventLog(p.run, p.heads)
    led = Ledger(p, log)
    led.freeze_scope(SCOPE, Turns(env["data"]))
    p.work.mkdir(parents=True)
    (p.work / "a.csv").write_text("a\n")
    (p.work / "sub").mkdir()
    (p.work / "sub" / "b.csv").write_text("b\n")
    (p.work / "report.md").write_text("narrative\n")
    return {"p": p, "log": log, "led": led, "out": Outputs(p, log)}


def _pass(h, unit, check):
    o = h["out"].record(check, unit, 1, 0, "pass", {}, unit.encode())
    h["led"].append_row("unit", {"id": unit, "attempt": 1, "verdict": "PASS", "outputs": [o["sha256"]]})


def _all_pass(h):
    _pass(h, "u1", "c.a")
    _pass(h, "u2", "c.b")


def _run(h, runner=None, profile=PROFILE):
    runner = runner or FakeRunner()
    res = gate.run_gate(h["p"], h["log"], KEY, profile, "/lc/fold_ledger.py", runner=runner)
    return res, runner


def _flag(argv, name):
    return argv[argv.index(name) + 1]


def test_done_mode_argv(h):
    _all_pass(h)
    res, r = _run(h)
    argv = r.calls[0]
    assert argv[:2] == ["python3", "/lc/fold_ledger.py"]
    assert _flag(argv, "--mode") == "done"
    assert _flag(argv, "--oracle-cmd") == "python3 oracle.py"
    assert _flag(argv, "--oracle-timeout") == "1790"  # capped
    assert _flag(argv, "--allow-blocked") == "2"
    assert _flag(argv, "--scope") == str(h["p"].run / "gate" / "scope.jsonl")
    sb = (h["p"].run / "gate" / "scope.jsonl").read_bytes()
    assert _flag(argv, "--expect-scope-sha") == canon.sha256_bytes(sb)
    assert _flag(argv, "--expect-scope-count") == "2"
    assert _flag(argv, "--verdict-vocab") == PROFILE["verdict_vocab"]
    assert _flag(argv, "--repo-root") == str(h["p"].run)
    assert "--skip-citation-check" not in argv
    assert res["ok"] is True


def test_known_mode_omits_oracle_and_allows_all_blocked(h):
    _pass(h, "u1", "c.a")  # u2 open -> known
    res, r = _run(h, profile=dict(PROFILE, allow_blocked=0))
    argv = r.calls[0]
    assert _flag(argv, "--mode") == "known"
    assert "--oracle-cmd" not in argv and "--oracle-timeout" not in argv
    assert _flag(argv, "--allow-blocked") == "2"
    assert res["receipt"]["mode"] == "known"


def test_done_mode_without_oracle_fails_closed(h):
    _all_pass(h)
    with pytest.raises(HarnessError):
        _run(h, profile={"allow_blocked": 0})


def test_submission_copies_work_tree(h):
    _all_pass(h)
    _run(h)
    sub = h["p"].run / "outputs" / "submitted"
    assert (sub / "a.csv").read_text() == "a\n" and (sub / "sub" / "b.csv").read_text() == "b\n"
    assert (sub / "report.md").exists()


def test_submission_rejects_symlink_and_records_tamper(h, tmp_path):
    _all_pass(h)
    (tmp_path / "secret").write_text("s")
    (h["p"].work / "leak").symlink_to(tmp_path / "secret")
    r = FakeRunner()
    with pytest.raises(TamperError):
        _run(h, runner=r)
    assert r.calls == []
    assert h["log"].events()[-1]["type"] == "TAMPER"


def test_receipt_written_with_hmac_and_event(h):
    _all_pass(h)
    res, _ = _run(h, runner=FakeRunner(caps=["numbers_untraced"]))
    rec = json.loads((h["p"].run / "receipt.json").read_text())
    assert rec == res["receipt"]
    fields = {k: rec[k] for k in gate.HMAC_FIELDS}
    assert rec["hmac"] == canon.hmac_receipt(KEY, fields)
    assert rec["caps"] == ["numbers_untraced"] and rec["caps_sha"] == canon.sha256_json(["numbers_untraced"])
    assert rec["control_hash"] == "ctl" and rec["business_hash"] == "biz" and rec["traced_set_sha"] == canon.sha256_json(["7", "42"])
    assert rec["report_sha"] == canon.sha256_bytes(b"# REPORT\nverdict: ANSWERED\n")
    evs = h["log"].events()
    assert evs[-1]["type"] == "RECEIPT" and evs[-2]["sha"] == rec["events_head"]


def test_gate_failure_writes_no_receipt(h):
    _all_pass(h)
    res, _ = _run(h, runner=FakeRunner(code=1))
    assert res["ok"] is False and res["code"] == 1
    assert not (h["p"].run / "receipt.json").exists()
    assert h["log"].events()[-1]["type"] == "GATE_FAILED"


def test_default_runner_is_sandbox_exec(h, monkeypatch):
    _all_pass(h)
    fake = FakeRunner()
    mod = types.ModuleType("harness_core.sandbox_exec")
    mod.run = lambda argv, cwd, stdin_bytes=None, **kw: fake(argv, cwd, stdin_bytes)
    monkeypatch.setitem(sys.modules, "harness_core.sandbox_exec", mod)
    res = gate.run_gate(h["p"], h["log"], KEY, PROFILE, "/lc/fold_ledger.py")
    assert res["ok"] and len(fake.calls) == 1


# --- verify_receipt -------------------------------------------------------------------

def test_verify_checked(h):
    _all_pass(h)
    _run(h)
    assert gate.verify_receipt(h["p"], h["log"], KEY) == {"ok": True, "state": "Checked", "caps": []}


def test_verify_known_mode_is_done_not_fully_checked(h):
    _pass(h, "u1", "c.a")
    h["led"].append_row("unit", {"id": "u2", "attempt": 1, "verdict": "UNVERIFIED", "evidence": ["c.json"]})
    _run(h)
    assert gate.verify_receipt(h["p"], h["log"], KEY)["state"] == "Done, not fully checked"


def test_verify_caps_is_done_not_fully_checked(h):
    _all_pass(h)
    _run(h, runner=FakeRunner(caps=["numbers_untraced"]))
    v = gate.verify_receipt(h["p"], h["log"], KEY)
    assert v["state"] == "Done, not fully checked" and v["caps"] == ["numbers_untraced"]


def test_verify_blocked_is_partly_done_in_either_mode(h):
    _pass(h, "u1", "c.a")
    h["led"].append_row("unit", {"id": "u2", "attempt": 1, "verdict": "BLOCKED", "reason": "no access"})
    res, _ = _run(h)
    assert res["receipt"]["mode"] == "done"
    assert gate.verify_receipt(h["p"], h["log"], KEY)["state"] == "Partly done"


def test_verify_open_unit_is_partly_done(h):
    _pass(h, "u1", "c.a")
    _run(h)
    assert gate.verify_receipt(h["p"], h["log"], KEY)["state"] == "Partly done"


def test_allowed_events_after_receipt_keep_it_valid(h):
    _all_pass(h)
    _run(h)
    for t in ("TURN", "BUDGET_CHARGE", "LEASE_HEARTBEAT", "RECORD", "CONTACT", "MODEL_REGISTERED"):
        h["log"].append(t, {})
    assert gate.verify_receipt(h["p"], h["log"], KEY)["ok"] is True


def test_no_receipt_is_not_ok(h):
    assert gate.verify_receipt(h["p"], h["log"], KEY)["ok"] is False


@pytest.mark.parametrize("forge", ["key", "field", "event", "submitted", "report", "gate_file", "chain"])
def test_forged_receipt_is_rejected(h, forge):
    _all_pass(h)
    _run(h)
    key = KEY
    if forge == "key":
        key = b"x" * 32
    elif forge == "field":
        p = h["p"].run / "receipt.json"
        rec = json.loads(p.read_text())
        rec["mode"] = "known" if rec["mode"] == "done" else "done"
        p.write_text(json.dumps(rec))
    elif forge == "event":
        h["log"].append("ROW", {"kind": "unit", "row": {}})
    elif forge == "submitted":
        (h["p"].run / "outputs" / "submitted" / "a.csv").write_text("edited\n")
    elif forge == "report":
        (h["p"].run / "gate" / "REPORT.md").write_text("# REPORT\nall good\n")
    elif forge == "gate_file":
        (h["p"].run / "gate" / "ledger.jsonl").write_text("")
    elif forge == "chain":
        h["p"].heads.write_text("0" * 64)
    v = gate.verify_receipt(h["p"], h["log"], key)
    assert v["ok"] is False and v["reason"]


# ---- gate wiring: events head, lexicon, include, claims, logs, numbers trace over every text deliverable ----


def _multi(argv, name):
    i = argv.index(name) + 1
    out = []
    while i < len(argv) and not argv[i].startswith("--"):
        out.append(argv[i])
        i += 1
    return out


def test_argv_passes_events_head_at_gate_time(h):
    _all_pass(h)
    _, r = _run(h)
    projected = [e for e in h["log"].events() if e["type"] == "PROJECTED"][-1]
    assert _flag(r.calls[0], "--events-head") == projected["sha"]


def test_argv_lint_lexicon_is_converted_to_gate_shape(h):
    _all_pass(h)
    _, r = _run(h)
    lex = json.loads(open(_flag(r.calls[0], "--lint-lexicon"), encoding="utf-8").read())
    assert set(lex) == {"terms", "template_lines"}
    assert "verified" in lex["terms"] and "تأیید شد" in lex["terms"]
    assert all(isinstance(t, str) for t in lex["terms"]) and isinstance(lex["template_lines"], list)


def test_argv_lint_lexicon_can_be_switched_off(h):
    _all_pass(h)
    _, r = _run(h, profile=dict(PROFILE, lint_lexicon=False))
    assert "--lint-lexicon" not in r.calls[0]


def test_argv_numbers_trace_covers_every_text_deliverable(h):
    (h["p"].work / "notes.txt").write_text("n\n")
    (h["p"].work / "chart.png").write_bytes(b"\x89PNG")
    _all_pass(h)
    _, r = _run(h)
    sub = h["p"].run / "outputs" / "submitted"
    assert sorted(_multi(r.calls[0], "--numbers-trace")) == sorted(
        str(sub / n) for n in ("a.csv", "sub/b.csv", "report.md", "notes.txt"))


def test_argv_include_answer_header_only_when_present(h):
    _all_pass(h)
    _, r = _run(h)
    assert "--include" not in r.calls[0]
    (h["p"].run / "answer_header.md").write_text("Answer: 42\n")
    _, r = _run(h)
    assert _flag(r.calls[0], "--include") == str(h["p"].run / "answer_header.md")


def test_argv_claims_flags_only_when_job_has_claims(h):
    _all_pass(h)
    _, r = _run(h)
    assert not {"--sources", "--fetch-log", "--snapshots", "--profile"} & set(r.calls[0])
    (h["p"].run / "claims.jsonl").write_text("{}\n")
    _, r = _run(h)
    run, argv = h["p"].run, r.calls[0]
    assert _flag(argv, "--sources") == str(run / "claims.jsonl")
    assert _flag(argv, "--fetch-log") == str(run / "fetch_log.jsonl")
    assert _flag(argv, "--snapshots") == str(run / "snapshots")
    assert _flag(argv, "--profile") == str(run / "gate" / "profile.json")


def test_argv_require_logs_for_investigations(h):
    _all_pass(h)
    _, r = _run(h)
    assert "--require-logs" not in r.calls[0]
    _, r = _run(h, profile=dict(PROFILE, task_type="investigation"))
    assert _multi(r.calls[0], "--require-logs") == [str(h["p"].run / "contradictions.jsonl"),
                                                     str(h["p"].run / "search_log.jsonl")]
