"""State machine end to end (spec §State machine and lifecycle; build phase P4): compile → stops → resume →
gate → DONE; handoff at the cap → PAUSED → resume; inline T0 beside an open task; RE_UNDERSTAND."""
from __future__ import annotations

import json

import pytest

from harness_core import budget, compile as compile_, contact, contacts, hookmain, sandbox_exec, sessionstate, store
from harness_core.errors import Denied
from harness_core.events import EventLog
from harness_core.ledger import Ledger
from harness_core.paths import job_paths
from test_budget import set_cap
from test_gate import FakeRunner
from test_gatehook import gate_post
from test_p3_hooks import CARD, SID, compiled, ctx_of, h, post, say, st  # noqa: F401  (fixture h)
from test_stopgate import stop

TWO = dict(CARD, units=2, unit_list=[{"label": "north region", "claim": "orders from the north region"},
                                     {"label": "south region", "claim": "orders from the south region"}])


def log_of(h, job):
    p = job_paths(h["data"], h["ws"], job)
    return p, EventLog(p.run, p.heads)


def read(h):
    return post(h, "Read", file_path=str(h["ws"] / "orders.csv"))[0]


def test_lifecycle_budget_stop_resume_gate_done(h, monkeypatch):
    job = compiled(h)
    set_cap(h, job, 1)
    assert [read(h), read(h)] == [0, 2]
    assert stop(h, "Stopped, waiting for the owner.") == (0, None)  # rule 2: a stop record
    say(h, "yes", "r1")
    assert contact.apply({"tool": "contact", "type": "cost", "accept": "latest"}, ctx_of(h), {"session_id": SID})["resumed"]
    assert sessionstate.jobs(ctx_of(h))[job]["state"] == "EXECUTING"
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    assert gate_post(h)[0] == 0
    code, out = stop(h, "The report is ready.")
    assert code == 0 and out["systemMessage"].startswith("Partly done")
    row = sessionstate.jobs(ctx_of(h))[job]
    assert row["state"] == "DONE" and row["terminal"] is True
    say(h, "how many refunds came in this week?", "p2")  # a terminal task never captures the next request
    assert st(h)["prompt_scope"] == "new" and st(h)["pending_card"]


def test_handoff_at_the_cap_pauses_and_the_owner_reply_resumes(h):
    job = compiled(h)
    for _ in range(5):
        contacts.record(ctx_of(h), "question", "", recheck=False)
    contact.apply({"tool": "contact", "type": "definition_confirm", "text": "is an order a paid invoice?",
                   "handoff": True}, ctx_of(h), {"session_id": SID})
    p, log = log_of(h, job)
    assert budget.latest_stop(log) == "PAUSED"
    assert stop(h, "Paused until you reply.") == (0, None)
    say(h, "an order is any invoice, paid or not", "r1")
    assert budget.latest_stop(log) is None and sessionstate.jobs(ctx_of(h))[job]["state"] == "EXECUTING"


def test_an_inline_t0_beside_an_open_task_leaves_it_alone(h):
    job = compiled(h)
    p, log = log_of(h, job)
    calls = store.read_json(p.run, log, "budget.json")["calls"]
    say(h, "What is the capital of France?", "p2")
    assert st(h)["prompt_scope"] == "inline_t0"
    read(h)
    assert stop(h, "Paris.") == (0, None)
    assert store.read_json(p.run, log, "budget.json")["calls"] == calls
    assert sessionstate.jobs(ctx_of(h))[job]["state"] == "EXECUTING"


# --- RE_UNDERSTAND ---

def correct(h, text="actually for the south region use last week instead", pid="c1"):
    code, out = say(h, text, pid)
    assert code == 0 and "--reunderstand" in out["hookSpecificOutput"]["additionalContext"]
    return pid


def again(h, card, turn):
    return compile_.apply({"tool": "compile", "card": card, "reunderstand": turn}, ctx_of(h), {"session_id": SID})


def test_reunderstand_writes_a_new_card_and_scope_with_a_rework_notice(h):
    job = compiled(h, card=TWO, text="how many orders came from each region this week?")
    p, log = log_of(h, job)
    tier0 = store.read_json(p.run, log, "tier.json")["tier"]
    cs = store.read_json(p.run, log, "contacts.json")
    store.write_json(p.run, log, "contacts.json", dict(cs, start_used=True), "CONTACT_UPDATE", {"t": 1})
    turn = correct(h)
    new = dict(TWO, unit_list=[TWO["unit_list"][0], {"label": "south region last week",
                                                     "claim": "orders from the south region last week"}])
    out = again(h, new, turn)
    assert out["reunderstood"] and out["version"] == 2 and out["notice"] == "This changes 2 of 2 items; applying now."
    scope = Ledger(p, log).sanctioned_scope()
    assert [u["id"] for u in scope] == ["u1", "u3"] and out["removed"] == ["u2"]
    assert Ledger(p, log).state("u2") == "DESCOPED_BY_USER"
    assert (p.run / "card.v2.json").is_file()
    assert store.read_json(p.run, log, "tier.json")["tier"] >= tier0
    assert store.read_json(p.run, log, "contacts.json")["start_used"] is False  # the turn names a changed unit


def test_reunderstand_needs_a_recorded_correction_and_happens_once(h):
    job = compiled(h, card=TWO, text="how many orders came from each region this week?")
    with pytest.raises(Denied, match="not a recorded correction"):
        again(h, TWO, "p1")
    turn = correct(h)
    again(h, TWO, turn)
    turn2 = correct(h, "and for the north region use last week too", "c2")
    with pytest.raises(Denied, match="already re-understood"):
        again(h, TWO, turn2)
    p, log = log_of(h, job)
    assert sum(e["type"] == "RE_UNDERSTAND" for e in log.events()) == 1


def test_a_changed_oracle_carries_no_verdict_over(h):
    job = compiled(h, card=TWO, text="how many orders came from each region this week?")
    turn = correct(h)
    new = dict(TWO, oracle={"type": "finance.reconcile", "executable": True, "criteria": ["ties out", "per region"]})
    out = again(h, new, turn)
    p, log = log_of(h, job)
    assert [u["id"] for u in Ledger(p, log).sanctioned_scope()] == ["u3", "u4"] and out["removed"] == ["u1", "u2"]


def test_the_tier_never_goes_down(h):
    job = compiled(h, card=dict(TWO, risk={"level": "high"}), text="how many orders came from each region this week?")
    p, log = log_of(h, job)
    before = store.read_json(p.run, log, "tier.json")["tier"]
    turn = correct(h)
    out = again(h, dict(TWO, risk={"level": "low"}), turn)
    assert out["tier"] == before


def test_a_follow_up_to_a_done_task_is_a_new_job_that_names_it(h, monkeypatch):
    job = compiled(h)
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    gate_post(h)
    stop(h, "The report is ready.")
    code, out = say(h, "now the same orders this week but only paid ones", "p2")
    assert "follows: " + job in out["hookSpecificOutput"]["additionalContext"]
    new = compile_.apply({"tool": "compile", "card": dict(CARD, follows=job)}, ctx_of(h), {"session_id": SID})
    assert new["job_id"] != job and new["state"] == "EXECUTING"


def test_follows_must_name_a_finished_task(h):
    from harness_core.card import CardError
    job = compiled(h)
    say(h, "What is the capital of France? also how many orders came in", "p2")
    sessionstate.update(ctx_of(h), SID, prompt_scope="new")
    with pytest.raises(CardError, match="not a finished task"):
        compile_.apply({"tool": "compile", "card": dict(CARD, follows=job)}, ctx_of(h), {"session_id": SID})
