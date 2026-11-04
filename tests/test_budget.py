"""Budget, stall and cost contacts (spec §Budget and failure handling; build phase P4)."""
from __future__ import annotations

import json

import pytest

from harness_core import budget, contact, sessionstate, store
from harness_core.errors import Denied
from harness_core.events import EventLog
from harness_core.paths import job_paths
from test_p3_hooks import CARD, SID, compiled, ctx_of, h, post, pre, say, st  # noqa: F401  (fixture h)


def log_of(h, job):
    p = job_paths(h["data"], h["ws"], job)
    return p, EventLog(p.run, p.heads)


def set_cap(h, job, cap):
    p, log = log_of(h, job)
    b = store.read_json(p.run, log, "budget.json")
    store.write_json(p.run, log, "budget.json", dict(b, calls_cap=cap), "BUDGET", {"cap": cap})


def test_tool_calls_in_a_task_are_charged(h):
    job = compiled(h)
    for _ in range(3):
        post(h, "Read", file_path=str(h["ws"] / "orders.csv"))
    p, log = log_of(h, job)
    assert store.read_json(p.run, log, "budget.json")["calls"] == 3


def test_over_the_call_cap_the_task_stops_with_a_cost_contact(h, capsys):
    job = compiled(h)
    set_cap(h, job, 2)
    codes = [post(h, "Read", file_path=str(h["ws"] / "orders.csv"))[0] for _ in range(3)]
    assert codes == [0, 0, 2] and "Stopped, not done" in capsys.readouterr().err
    p, log = log_of(h, job)
    assert [e for e in log.events() if e["type"] == "STOPPED"][-1]["payload"]["state"] == "STOPPED_BUDGET"
    assert sessionstate.jobs(ctx_of(h))[job]["state"] == "STOPPED_BUDGET"
    rows = store.read_json(p.run, log, "contacts.json")["rows"]
    assert rows[-1]["type"] == "cost" and rows[-1]["counted"] is False


def test_after_a_budget_stop_only_reads_and_contacts_run(h):
    job = compiled(h)
    set_cap(h, job, 1)
    post(h, "Read", file_path=str(h["ws"] / "orders.csv"))
    post(h, "Read", file_path=str(h["ws"] / "orders.csv"))
    assert pre(h, "Write", file_path=str(h["ws"] / "x.txt"), content="x") == 2
    assert pre(h, "Read", file_path=str(h["ws"] / "orders.csv")) == 0
    assert pre(h, "Bash", command="harness-contact cost --accept latest") == 0


def test_continue_grants_a_recorded_increment_and_resumes(h):
    job = compiled(h)
    set_cap(h, job, 1)
    post(h, "Read", file_path=str(h["ws"] / "orders.csv"))
    post(h, "Read", file_path=str(h["ws"] / "orders.csv"))
    say(h, "yes, continue", "r1")
    out = contact.apply({"tool": "contact", "type": "cost", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    assert out["resumed"] is True
    p, log = log_of(h, job)
    types = [e["type"] for e in log.events()]
    assert "APPROVAL" in types and "RESUMED" in types[len(types) - types[::-1].index("STOPPED"):]
    assert budget.latest_stop(log) is None
    assert store.read_json(p.run, log, "budget.json")["calls_cap"] > 1
    assert pre(h, "Write", file_path=str(h["ws"] / ".claude" / "loops" / job / "work" / "a.md"), content="x") == 0


def test_cost_accept_needs_the_owner_reply_and_an_approval(h):
    job = compiled(h)
    set_cap(h, job, 1)
    post(h, "Read", file_path=str(h["ws"] / "orders.csv"))
    post(h, "Read", file_path=str(h["ws"] / "orders.csv"))
    say(h, "no, stop here", "r1")
    with pytest.raises(Denied):
        contact.apply({"tool": "contact", "type": "cost", "accept": "latest"}, ctx_of(h), {"session_id": SID})


def test_no_new_verdict_in_40_calls_is_a_stall(h, capsys):
    job = compiled(h)
    set_cap(h, job, 1000)
    codes = [post(h, "Read", file_path=str(h["ws"] / "orders.csv"))[0] for _ in range(budget.STALL_CALLS + 1)]
    assert codes[-1] == 2 and "Stuck" in capsys.readouterr().err
    p, log = log_of(h, job)
    assert [e for e in log.events() if e["type"] == "STOPPED"][-1]["payload"]["state"] == "STOPPED_STALL"


def test_money_cap_from_transcript_usage(h, tmp_path):
    job = compiled(h)
    (h["data"] / "business").mkdir(exist_ok=True)
    (h["data"] / "business" / "profile.json").write_text(json.dumps({"caps": {"task": 1.0, "currency": "USD"}}))
    (h["data"] / "control").mkdir(exist_ok=True)
    (h["data"] / "control" / "prices.json").write_text(json.dumps({"version": 1, "currency": "USD", "as_of": "2026-11-01",
                                                                    "per_mtok": {"input": 3.0, "output": 15.0}}))
    tr = tmp_path / "t.jsonl"
    tr.write_text(json.dumps({"type": "assistant", "timestamp": "2099-01-01T00:00:00Z",
                              "message": {"usage": {"input_tokens": 200000, "output_tokens": 40000}}}) + "\n")
    evt = {"session_id": SID, "tool_name": "Read", "tool_input": {"file_path": str(h["ws"] / "orders.csv")},
           "tool_response": {}, "cwd": str(h["ws"]), "transcript_path": str(tr)}
    from harness_core import hookmain
    assert hookmain.main("PostToolUse", evt, h["env"])[0] == 2
    p, log = log_of(h, job)
    assert [e for e in log.events() if e["type"] == "STOPPED"][-1]["payload"]["state"] == "STOPPED_COST"
