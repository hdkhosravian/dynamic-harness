"""Contact cap (R-7), late questions (R-8), contact writer and the writers dispatcher (build phase P3)."""
from __future__ import annotations

import json

import pytest

from harness_core import compile as compile_, contact, contacts, memory, probe, sessionstate, store, writers
from harness_core.errors import Denied
from harness_core.events import EventLog
from harness_core.ledger import Ledger
from harness_core.paths import job_paths
from harness_core.safety import Ctx
from harness_core.turns import Turns

SID = "s1"
DRAFT = {
    "task_type": "analysis_report",
    "deliverable": {"kind": "report", "description": "monthly sales report"},
    "oracle": {"type": "finance.kpi", "executable": True, "criteria": ["each KPI recomputed"]},
    "reliance": "operations", "decision_class": "none",
    "cost_of_error": {"money_at_stake": {"amount": 0, "currency": "IRT"}},
    "risk": {"level": "medium"}, "units": 2, "unit_definition": "one KPI", "domain": "sales",
    "unit_list": [{"label": "revenue", "claim": "revenue for May"}, {"label": "orders", "claim": "orders for May"}],
    "provenance": {"risk": "user", "reliance": "user"},
}


@pytest.fixture
def ctx(tmp_path):
    ws, data, plugin = tmp_path / "ws", tmp_path / "data", tmp_path / "plugin"
    for d in (ws, data, plugin / ".claude-plugin"):
        d.mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "dynamic-harness", "version": "0.2.0"}')
    c = Ctx(workspace=ws, data_root=data, plugin_root=plugin, home=tmp_path / "home")
    c.session_ok = True
    turns = Turns(data)
    turns.append({"turn_id": "t0", "text_norm": "make the monthly sales report"})
    sessionstate.update(c, SID, probe=probe.run("make the monthly sales report", c), request_turn="t0")
    c.job = compile_.apply({"tool": "compile", "card": DRAFT}, c, {"session_id": SID})["job_id"]
    return c


def paths(ctx):
    p = job_paths(ctx.data_root, ctx.workspace, ctx.job)
    return p, EventLog(p.run, p.heads)


def C(ctx, kind, **kw):
    return contact.apply({"tool": "contact", "type": kind, **kw}, ctx, {"session_id": SID})


def test_contacts_are_counted_up_to_the_cap(ctx):
    for i in range(5):
        C(ctx, "question", text=f"q{i}")
    assert contacts.state(ctx)["count"] == 5
    with pytest.raises(Denied, match="cap"):
        C(ctx, "question", text="q6")


def test_decision_ack_is_exempt_from_the_cap(ctx):
    for i in range(5):
        C(ctx, "question", text=f"q{i}")
    assert C(ctx, "decision_ack", text="ok?")["counted"] is False


def test_one_handoff_at_the_cap_for_a_before_spend_contact(ctx):
    for i in range(5):
        C(ctx, "question", text=f"q{i}")
    with pytest.raises(Denied):
        C(ctx, "question", text="late", handoff=True)  # not before-spend
    row = C(ctx, "assumption_sheet", text="please check", handoff=True)
    assert row["counted"] is False and row["handoff"] is True
    p, log = paths(ctx)
    assert log.events()[-1]["type"] == "PAUSED" and sessionstate.jobs(ctx)[ctx.job]["state"] == "PAUSED"
    with pytest.raises(Denied):
        C(ctx, "assumption_sheet", text="again", handoff=True)  # once per task


def test_a_contact_points_the_session_at_itself(ctx):
    row = C(ctx, "question", text="which month?")
    assert sessionstate.load(ctx, SID)["pending_contact_id"] == row["contact_id"]


def test_single_unit_progress_follows_milestones(ctx):
    turns = Turns(ctx.data_root)
    turns.append({"turn_id": "t1", "text_norm": "write the summary"})
    sessionstate.update(ctx, SID, probe=probe.run("write the summary", ctx), request_turn="t1", prompt_scope="new")
    one = dict(DRAFT, units=1, unit_list=[{"label": "summary", "claim": "the summary"}])
    ctx.job = compile_.apply({"tool": "compile", "card": one}, ctx, {"session_id": SID})["job_id"]
    p, log = paths(ctx)
    assert contacts.progress(ctx) == 0.0
    for name, want in (("outline", 0.25), ("draft", 0.5)):
        (p.work / f"{name}.md").write_text(name)
        Ledger(p, log).append_row("milestone", {"milestone": name, "file": f"{name}.md"})
        assert contacts.progress(ctx) == want
    assert "R-8" in contacts.check("ask", ctx, questions=1)


def test_late_questions_are_denied_after_half_the_units(ctx, monkeypatch):
    monkeypatch.setattr(contacts, "progress", lambda c: 0.5)
    assert "R-8" in contacts.check("ask", ctx, questions=1)
    with pytest.raises(Denied, match="R-8"):
        C(ctx, "question", text="late")
    assert contacts.check("decision_ack", ctx) is None and contacts.check("assumption_sheet", ctx) is None


def test_progress_counts_terminal_units(ctx):
    assert contacts.progress(ctx) == 0.0


def test_start_questions_once_and_at_most_three(ctx):
    assert "3 start questions" in contacts.check("ask", ctx, questions=4)
    assert contacts.check("ask", ctx, questions=3) is None
    contacts.record_ask({"tool_input": {"questions": [{}, {}, {}]}}, ctx)
    assert "one message" in contacts.check("ask", ctx, questions=1)


def test_t0_and_no_task_are_not_counted(ctx):
    ctx.job = None
    assert contacts.check("ask", ctx, questions=9) is None
    with pytest.raises(Denied):
        C(ctx, "question", text="x")


def test_cost_contacts_wait_for_the_budget_hook(ctx):
    with pytest.raises(Denied, match="budget"):
        C(ctx, "cost", text="x")


PLAN = {"hypotheses": ["h1"], "queries": ["q"], "urls": ["https://news.example.ir/a"], "page_budget": 5,
        "stop_rule": "3 sources", "load_bearing": ["h1"]}


def test_plan_writes_plan_json_and_accept_writes_a_grant_hookmain_honours(ctx, tmp_path):
    row = C(ctx, "plan", text="here is the plan", plan=PLAN)
    p, log = paths(ctx)
    plan = store.read_json(p.run, log, "plan.json")
    assert plan["domains"] == ["news.example.ir"]
    Turns(ctx.data_root).append({"turn_id": "t9", "text_norm": "yes, go ahead with the plan",
                                 "pending_contact_id": row["contact_id"]})
    out = C(ctx, "plan", accept="t9")
    assert out["granted"] is True
    from harness_core.hookmain import _grant
    assert _grant(ctx.data_root, ctx.job)["domains"] == ["news.example.ir"]


def test_accept_needs_an_owner_turn_after_the_plan(ctx):
    Turns(ctx.data_root).append({"turn_id": "early", "text_norm": "yes"})
    C(ctx, "plan", text="plan", plan=PLAN)
    with pytest.raises(Denied):
        C(ctx, "plan", accept="early")  # came before the plan contact
    Turns(ctx.data_root).append({"turn_id": "bot", "text_norm": "<task-notification>yes"})
    with pytest.raises(Denied):
        C(ctx, "plan", accept="bot")  # a system turn is not consent


def test_plan_urls_must_be_https(ctx):
    from harness_core.errors import SchemaError
    with pytest.raises(SchemaError):
        C(ctx, "plan", text="p", plan=dict(PLAN, urls=["http://x.example/a"]))


# --- writers dispatcher ---


def bash(cmd, stdout):
    return {"tool_name": "Bash", "session_id": SID, "tool_input": {"command": cmd},
            "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False}}


def test_dispatcher_records_a_contact_from_the_cli_request(ctx):
    req = {"tool": "contact", "type": "question", "text": "which month?"}
    out = writers.on_post(bash("harness-contact question --text 'which month?'", json.dumps(req)), ctx)
    assert "recorded" in out["hookSpecificOutput"]["additionalContext"] and contacts.state(ctx)["count"] == 1


@pytest.mark.parametrize("cmd", [
    "harness-contact question; echo hi",
    "echo '{\"tool\":\"contact\"}' | harness-contact question",
    "harness-contact question $(cat x)",
    "harness-contact question > out.txt",
    "echo hi",
])
def test_only_a_lone_harness_command_reaches_a_writer(ctx, cmd):
    req = {"tool": "contact", "type": "question", "text": "x"}
    assert writers.on_post(bash(cmd, json.dumps(req)), ctx) == {}
    assert contacts.state(ctx)["count"] == 0


def test_the_request_must_name_the_same_tool(ctx):
    out = writers.on_post(bash("harness-contact question", json.dumps({"tool": "compile", "card": {}})), ctx)
    assert out["decision"] == "block" and "does not name" in out["reason"]


def test_a_refused_contact_becomes_a_block_reason(ctx):
    for i in range(5):
        C(ctx, "question", text=f"q{i}")
    out = writers.on_post(bash("harness-contact question", json.dumps({"tool": "contact", "type": "question"})), ctx)
    assert out["decision"] == "block" and "cap" in out["reason"]


def test_ask_user_question_feeds_memory_and_the_counter(ctx):
    q = {"header": "[pref:format] F", "question": "F?", "options": [{"label": "short"}], "multiSelect": False}
    evt = {"tool_name": "AskUserQuestion", "session_id": SID, "tool_input": {"questions": [q]},
           "tool_response": {"answers": {"F?": "short"}}}
    assert writers.on_post(evt, ctx) == {}
    assert memory.effective(ctx) == {"format": "short"} and contacts.state(ctx)["count"] == 1


# --- review of c1ef887 ---


def reply(ctx, turn_id, text, contact_id):
    Turns(ctx.data_root).append({"turn_id": turn_id, "text_norm": text, "pending_contact_id": contact_id})


def test_accept_needs_the_owner_reply_to_that_plan_contact(ctx):
    row = C(ctx, "plan", text="plan", plan=PLAN)
    Turns(ctx.data_root).append({"turn_id": "other", "text_norm": "yes"})  # not a reply to the plan
    with pytest.raises(Denied):
        C(ctx, "plan", accept="other")
    reply(ctx, "r1", "hmm, tell me more", row["contact_id"])  # a reply, but not an approval
    with pytest.raises(Denied):
        C(ctx, "plan", accept="r1")
    reply(ctx, "r2", "yes, approved", row["contact_id"])
    assert C(ctx, "plan", accept="r2")["granted"] is True
    with pytest.raises(Denied):
        C(ctx, "plan", accept="r2")  # consumed


def test_the_plan_text_shown_is_rendered_from_plan_json(ctx):
    row = C(ctx, "plan", text="Trust me, harmless", plan=PLAN)
    assert "news.example.ir" in row["text"] and "5 pages" in row["text"] and "Trust me" not in row["text"]


def test_the_cap_is_rechecked_under_the_lock(ctx):
    for i in range(5):
        C(ctx, "question", text=f"q{i}")
    with pytest.raises(Denied, match="cap"):  # record() itself refuses, whatever a caller checked before
        contacts.record(ctx, "question", "sha")
    assert contacts.state(ctx)["count"] == 5
