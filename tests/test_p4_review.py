"""Regression tests for the P4 review findings (each fails if its guard is removed)."""
from __future__ import annotations

import json
import time

import pytest

from harness_core import budget, compile as compile_, contact, gate, hookmain, lease, sandbox_exec, sessionstate, store
from harness_core.errors import Denied
from harness_core.events import EventLog
from harness_core.paths import job_paths
from test_budget import blocked, set_cap
from test_gate import FakeRunner
from test_gatehook import gate_post
from test_lease import OTHER, ctx_for, start_other
from test_p3_hooks import CARD, SID, compiled, ctx_of, h, post, pre, say, st  # noqa: F401  (fixture h)
from test_state_machine import TWO, again, correct
from test_stopgate import stop


def logp(h, job):
    p = job_paths(h["data"], h["ws"], job)
    return p, EventLog(p.run, p.heads)


def read(h):
    return post(h, "Read", file_path=str(h["ws"] / "orders.csv"))


def stopped_on_budget(h):
    job = compiled(h)
    set_cap(h, job, 1)
    read(h)
    assert blocked(read(h))
    return job


def test_1_an_edit_after_the_gate_needs_a_new_gate(h, monkeypatch):
    job = compiled(h)
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    gate_post(h)
    p, _ = logp(h, job)
    (p.work / "report.md").write_text("changed after the gate\n")
    assert stop(h, "Here it is.")[0] == 2
    assert sessionstate.jobs(ctx_of(h))[job]["state"] != "DONE"


@pytest.mark.parametrize("msg", ["Stuck on x because the totals are verified.",
                                 "All totals are veri​fied.", "Totals CONFIRMED."])
def test_2_state_words_cannot_hide_in_a_template_or_a_zero_width_char(h, msg):
    say(h, "What is the capital of France?", "p1")
    assert stop(h, msg)[0] == 2


def test_3_reunderstand_drops_a_unit_only_when_the_owner_names_it(h):
    compiled(h, card=TWO, text="how many orders came from each region this week?")
    turn = correct(h)  # names only the south region
    only_north_gone = dict(TWO, units=1, unit_list=[TWO["unit_list"][1]])
    with pytest.raises(Denied, match="does not name"):
        again(h, only_north_gone, turn)


def test_4_a_paused_task_runs_only_reads(h):
    job = compiled(h)
    _, log = logp(h, job)
    log.append("PAUSED", {"reason": "handoff"})
    assert pre(h, "Write", file_path=str(h["ws"] / ".claude" / "loops" / job / "work" / "a.md"), content="x") == 2
    assert pre(h, "Read", file_path=str(h["ws"] / "orders.csv")) == 0


def test_5_a_refused_writer_call_is_charged(h):
    job = compiled(h)
    p, log = logp(h, job)
    before = store.read_json(p.run, log, "budget.json")["calls"]
    post(h, "Bash", {"stdout": json.dumps({"tool": "ledger", "kind": "unit", "row": {}})},
         command="harness-ledger append --kind unit --row {}")
    assert store.read_json(p.run, log, "budget.json")["calls"] == before + 1


def test_6_a_non_approval_reply_keeps_the_cost_contact_open(h):
    stopped_on_budget(h)
    say(h, "why did it stop?", "r1")
    say(h, "yes", "r2")
    out = contact.apply({"tool": "contact", "type": "cost", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    assert out["resumed"]


def test_6_the_owner_resumes_a_stall(h):
    job = compiled(h)
    budget.stop(ctx_of(h), job, "STOPPED_STALL", "x", SID)
    say(h, "continue", "r1")
    _, log = logp(h, job)
    assert budget.latest_stop(log) is None


def test_6_a_second_concurrent_stop_is_a_no_op(h):
    job = compiled(h)
    assert budget.stop(ctx_of(h), job, "STOPPED_STALL", "x", SID)
    assert budget.stop(ctx_of(h), job, "STOPPED_BUDGET", "y", SID) is None


def test_7_rewording_cannot_loop_the_stop_hook(h):
    compiled(h)
    codes = [stop(h, f"Working on it, step {i}.")[0] for i in range(3)]
    assert codes == [2, 2, 0]


def test_7_alternating_rules_cannot_loop_either(h, monkeypatch):
    compiled(h)
    say(h, "What is the capital of France?", "p2")
    msgs = ["Paris (verified).", "Paris, about $5."] * 4
    codes = [stop(h, m)[0] for m in msgs]
    assert 0 in codes


def test_8_one_cost_acceptance(h):
    stopped_on_budget(h)
    say(h, "yes", "r1")
    contact.apply({"tool": "contact", "type": "cost", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    with pytest.raises(Denied):
        contact.apply({"tool": "contact", "type": "cost", "accept": "latest"}, ctx_of(h), {"session_id": SID})


def test_9_a_repeated_blocked_verdict_is_not_progress(h):
    job = compiled(h)
    p, log = logp(h, job)
    for _ in range(3):
        log.append("ROW", {"kind": "unit", "row": {"id": "u1", "attempt": 1, "verdict": "BLOCKED"}})
    assert budget._verdicts(log) == 1


def test_10_a_background_shell_is_not_a_wait(h):
    compiled(h)
    assert stop(h, "Waiting.", background_tasks=[{"id": "b1", "type": "bash", "status": "running"}])[0] == 2


def test_11_the_lease_stays_live_while_a_gate_runs(h, monkeypatch):
    job = compiled(h)
    _, log = logp(h, job)
    log.append("GATE_STARTED", {"at": time.time()})
    start_other(h)
    real = time.time
    monkeypatch.setattr(lease.time, "time", lambda: real() + lease.TTL + 5)
    assert lease.status(ctx_for(h, OTHER, job), job, OTHER) == "other"


def test_11_a_main_hook_charge_during_the_gate_is_not_tamper(h, monkeypatch):
    job = compiled(h)

    class Charging(FakeRunner):
        def __call__(self, argv, cwd, stdin, **kw):
            read(h)  # the parallel main PostToolUse hook charges budget.json mid-gate
            return super().__call__(argv, cwd, stdin, **kw)

    monkeypatch.setattr(sandbox_exec, "run", Charging())
    assert gate_post(h)[0] == 0


def test_12_the_owner_sees_the_cost_question(h):
    job = compiled(h)
    set_cap(h, job, 1)
    read(h)
    code, out = read(h)
    assert code == 0 and out["decision"] == "block" and "Continue?" in out["systemMessage"]


def test_gate_refuses_a_non_owner_session(h, monkeypatch):
    job = compiled(h)
    start_other(h)
    sessionstate.update(ctx_for(h, OTHER), OTHER, active_task=job, prompt_scope="active_task")
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    evt = {"session_id": OTHER, "tool_name": "Bash", "tool_input": {"command": "harness-gate request"},
           "tool_response": {"stdout": json.dumps({"tool": "gate", "action": "request"})}, "cwd": str(h["ws"])}
    assert hookmain.main("gate", evt, h["env"])[0] == 2


def test_t1_time_budget_stops_the_task(h, monkeypatch):
    job = compiled(h)
    real = time.time
    monkeypatch.setattr(budget.time, "time", lambda: real() + budget.T1_SECONDS + 60)
    p, log = logp(h, job)
    if store.read_json(p.run, log, "tier.json")["tier"] != "T1":
        pytest.skip("CARD compiles above T1")
    assert blocked(read(h))
    assert [e for e in log.events() if e["type"] == "STOPPED"][-1]["payload"]["state"] == "STOPPED_BUDGET"


def test_the_opening_line_is_not_a_lint_exemption(h, monkeypatch):
    from harness_core.card import CardError
    say(h, "how many orders came in this week?", "p1")
    with pytest.raises(CardError, match="state words"):
        compile_.apply({"tool": "compile", "card": dict(CARD, deliverable={"kind": "answer",
                        "description": "orders, all totals verified"})}, ctx_of(h), {"session_id": SID})
    job = compiled(h, pid="p2")
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    gate_post(h)
    p, _ = logp(h, job)
    assert stop(h, (p.run / "opening.txt").read_text() + "\nAll totals verified.")[0] == 2
