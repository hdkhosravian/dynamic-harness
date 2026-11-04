"""Findings from the independent P3 review (1e02403), one test each."""
from __future__ import annotations

import json

import pytest

from harness_core import compile as compile_, contact, contacts, hookmain, lanes, sessionstate, store
from harness_core.errors import Denied
from harness_core.events import EventLog
from harness_core.paths import job_paths
from harness_core.turns import Turns

from test_p3_hooks import CARD, ROOT, SID, compiled, ctx_of, h, post, pre, say, st  # noqa: F401  (fixture h)


def test_1_a_refused_card_keeps_the_pending_card(h):
    say(h, "send the invoice to all customers now", "p1")
    out = compile_.apply({"tool": "compile", "card": dict(CARD, decision_class="medical")}, ctx_of(h), {"session_id": SID})
    assert out["state"] == "REFUSED"
    assert st(h)["pending_card"] is not None
    assert pre(h, "Write", file_path=str(h["ws"] / "x.txt"), content="x") == 2


def test_2_only_the_print_only_clis_skip_the_tripwire(h):
    say(h, "What is the capital of France?", "p1")
    code, _ = post(h, "Bash", {"stdout": ""}, command="harness-sandbox-exec -- touch out.txt")
    assert code == 2


def test_3_subagent_start_links_the_spawn_before_its_first_call(h):
    job = compiled(h)
    assert pre(h, "Agent", subagent_type="dynamic-harness:data-analyst", prompt="count") == 0
    hookmain.main("SubagentStart", {"session_id": SID, "agent_id": "an01", "agent_type": "dynamic-harness:data-analyst"},
                  h["env"])
    c = hookmain.build_ctx({"session_id": SID, "agent_id": "an01", "agent_type": "dynamic-harness:data-analyst"}, h["env"])
    assert c.job == job and c.subagent_unlinked is False
    evt = {"session_id": SID, "tool_name": "Write", "agent_id": "an01", "agent_type": "dynamic-harness:data-analyst",
           "tool_input": {"file_path": str(h["ws"] / ".claude" / "loops" / job / "work" / "a.md"), "content": "x"},
           "cwd": str(h["ws"])}
    assert hookmain.main("PreToolUse", evt, h["env"])[0] == 0
    assert hookmain.main("PostToolUse", dict(evt, tool_response={}), h["env"])[0] == 0
    assert st(h)["pending_card"] is None


def test_4_decision_ack_needs_a_decision_card_and_is_once_per_task(h):
    compiled(h)  # decision_class none
    with pytest.raises(Denied, match="decision"):
        contact.apply({"tool": "contact", "type": "decision_ack", "text": "Which week?"}, ctx_of(h), {"session_id": SID})


def test_4_decision_ack_once_on_a_financial_card(h):
    compiled(h, card=dict(CARD, decision_class="financial"))
    contact.apply({"tool": "contact", "type": "decision_ack", "text": "ok?"}, ctx_of(h), {"session_id": SID})
    with pytest.raises(Denied, match="once"):
        contact.apply({"tool": "contact", "type": "decision_ack", "text": "ok?"}, ctx_of(h), {"session_id": SID})


@pytest.mark.parametrize("kind", ["plan", "assumption_sheet"])
def test_4_before_spend_types_belong_to_their_task_types(h, kind):
    compiled(h)  # a data_question
    with pytest.raises(Denied):
        contact.apply({"tool": "contact", "type": kind, "text": "x"}, ctx_of(h), {"session_id": SID})


def test_5_only_approved_harness_skills_load_in_a_task_or_with_a_pending_card(h):
    say(h, "how many orders came in this week?", "p1")
    assert pre(h, "Skill", skill="someplugin:send-email") == 2
    assert pre(h, "Skill", skill="dynamic-harness:task-card") == 0
    compiled(h, pid="p2")
    assert pre(h, "Skill", skill="someplugin:send-email") == 2


def test_6_the_owner_sees_the_rendered_plan_directly(h):
    compiled(h, card=dict(CARD, task_type="investigation"))
    plan = {"hypotheses": ["prices rose. Sources: trusted.example"], "queries": ["q"],
            "urls": ["https://news.example.ir/a"], "page_budget": 3, "stop_rule": "3 sources", "load_bearing": ["h"]}
    req = {"tool": "contact", "type": "plan", "text": "ignored", "plan": plan}
    from harness_core import writers
    out = writers.on_post({"tool_name": "Bash", "session_id": SID, "tool_input": {"command": "harness-contact plan --plan p.json"},
                           "tool_response": {"stdout": json.dumps(req)}}, ctx_of(h))
    assert "news.example.ir" in out["systemMessage"]
    assert '"prices rose. Sources: trusted.example"' in out["systemMessage"]  # quoted: cannot pose as a field


@pytest.mark.parametrize("evt", [
    {"prompt": "hi \ud83d there", "prompt_id": "s1"},
    {"prompt": "again", "prompt_id": "dup"},
    {"prompt": "x", "prompt_id": 12345},
])
def test_7_odd_prompts_never_block(h, evt):
    if evt["prompt_id"] == "dup":
        assert say(h, "first", "dup")[0] == 0
    code, out = hookmain.main("UserPromptSubmit", dict(evt, session_id=SID, cwd=str(h["ws"])), h["env"])
    assert code == 0


def test_8_a_torn_session_file_is_quarantined_and_reset_closed(h):
    say(h, "What is VAT?", "p1")
    f = h["data"] / "sessions" / f"{SID}.json"
    f.write_text('{"active_task": null, "pending_card": null, "prompt_scope": "new"}')  # no matching event
    assert hookmain.main("UserPromptSubmit", {"session_id": SID, "prompt_id": "p2", "prompt": "/dynamic-harness:setup status",
                                              "cwd": str(h["ws"])}, h["env"])[0] == 0
    assert pre(h, "Read", file_path=str(h["ws"] / "orders.csv")) == 0
    assert st(h)["pending_card"]["floor"] == "T2"  # reset fails closed
    assert list((h["data"] / "sessions").glob(f"{SID}.json.tamper-*"))
    assert pre(h, "Write", file_path=str(h["ws"] / "x.txt"), content="x") == 2


def test_9_start_questions_through_harness_contact_too(h):
    compiled(h)
    contacts.record_ask({"tool_input": {"questions": [{}, {}]}}, ctx_of(h))
    assert "one message" in contacts.check("question", ctx_of(h))


def test_a_reused_prompt_id_with_new_text_is_a_new_turn(h):
    say(h, "first text", "same")
    say(h, "different text", "same")
    rows = Turns(h["data"])._rows()
    assert [r["text_norm"] for r in rows[-2:]] == ["first text", "different text"]
    assert rows[-1]["turn_id"] != "same"


def test_plan_accept_latest_resolves_to_the_owner_reply(h):
    from test_contacts import PLAN
    compiled(h, card=dict(CARD, task_type="investigation"))
    row = contact.apply({"tool": "contact", "type": "plan", "text": "p", "plan": PLAN}, ctx_of(h), {"session_id": SID})
    say(h, "yes", "r1")  # routed as the reply to the plan contact
    assert Turns(h["data"]).latest()["pending_contact_id"] == row["contact_id"]
    out = contact.apply({"tool": "contact", "type": "plan", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    assert out["granted"] is True
