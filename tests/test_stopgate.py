"""Stop hook (spec §Gate and Stop semantics; build phase P4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core import budget, hookmain, normalize, sandbox_exec, sessionstate, store
from harness_core.events import EventLog
from harness_core.paths import job_paths
from test_gate import FakeRunner
from test_gatehook import gate_post
from test_p3_hooks import ROOT, SID, compiled, ctx_of, h, post, say, st  # noqa: F401  (fixture h)


def stop(h, msg="", **kw):
    evt = {"session_id": SID, "cwd": str(h["ws"]), "stop_hook_active": False, "last_assistant_message": msg, **kw}
    return hookmain.main("Stop", evt, h["env"])


def gated(h, monkeypatch):
    job = compiled(h)
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    assert gate_post(h)[0] == 0
    return job


def stop_events(h, job):
    p = job_paths(h["data"], h["ws"], job)
    return [e["payload"]["state"] for e in EventLog(p.run, p.heads).events() if e["type"] == "STOPPED"]


# --- rule 3: quick answers ---

def test_a_plain_quick_answer_stops(h):
    say(h, "What is the capital of France?", "p1")
    assert stop(h, "Paris.") == (0, None)


def test_a_pending_card_blocks_the_stop(h):
    say(h, "how many orders came in this week?", "p1")
    assert stop(h, "About a dozen.")[0] == 2


def test_a_quick_answer_may_not_self_certify(h):
    say(h, "What is the capital of France?", "p1")
    assert stop(h, "Paris (verified).")[0] == 2


def test_money_in_a_quick_answer_blocks_once_and_needs_a_card(h):
    say(h, "What is the capital of France?", "p1")
    assert stop(h, "Paris. A flight costs about $400.")[0] == 2
    assert st(h)["pending_card"]["floor"] == "T1"


# --- with an open task ---

def test_an_unfinished_task_blocks_then_stalls(h, capsys):
    job = compiled(h)
    assert stop(h, "Working on it.")[0] == 2
    assert stop(h, "Working on it.")[0] == 2
    code, out = stop(h, "Working on it.")
    assert code == 0 and "Stuck" in out["systemMessage"]
    assert stop_events(h, job)[-1] == "STOPPED_STALL"


def test_a_verified_receipt_ends_the_task_with_the_state_line(h, monkeypatch):
    job = gated(h, monkeypatch)
    code, out = stop(h, "Here is the report.")
    assert code == 0 and out["systemMessage"] == "Partly done: 0 of 1"
    assert sessionstate.jobs(ctx_of(h))[job]["state"] == "DONE"
    rows = (h["data"] / "records" / f"{job}.jsonl").read_text().splitlines()
    assert json.loads(rows[-1])["state"] == "DONE" and json.loads(rows[-1])["interim"] is False


def test_only_traced_numbers_may_appear_after_the_gate(h, monkeypatch):
    gated(h, monkeypatch)
    assert stop(h, "Orders this week: 4500.")[0] == 2
    assert stop(h, "Orders this week: 42.")[0] == 0  # FakeRunner traced 7 and 42


def test_state_words_after_the_gate_block(h, monkeypatch):
    gated(h, monkeypatch)
    assert stop(h, "All figures verified.")[0] == 2


def test_a_running_subagent_is_a_wait_not_a_block(h):
    job = compiled(h)
    assert stop(h, "Waiting for the researcher.", background_tasks=[{"id": "a1", "status": "running"}]) == (0, None)
    assert sessionstate.jobs(ctx_of(h))[job]["state"] == "EXECUTING" and not st(h).get("stop_blocks")


def test_a_stop_record_allows_the_stop(h):
    job = compiled(h)
    budget.stop(ctx_of(h), job, "STOPPED_STALL", "x", SID)
    assert stop(h, "Stuck.") == (0, None)


def test_a_recorded_contact_allows_the_stop(h):
    compiled(h)
    sessionstate.update(ctx_of(h), SID, pending_contact_id="c1")
    assert stop(h, "Please answer the question above.") == (0, None)


def test_a_question_in_the_final_message_counts_as_a_contact(h):
    job = compiled(h)
    assert stop(h, "Which week do you mean, this one or last?") == (0, None)
    p = job_paths(h["data"], h["ws"], job)
    rows = store.read_json(p.run, EventLog(p.run, p.heads), "contacts.json")["rows"]
    assert rows[-1]["type"] == "question" and rows[-1]["classified"] is True
    assert st(h)["pending_contact_id"] == rows[-1]["contact_id"]


def test_tamper_allows_the_stop_only_as_stopped_tamper(h):
    job = compiled(h)
    p = job_paths(h["data"], h["ws"], job)
    (p.run / "budget.json").write_text(json.dumps({"calls": 0, "calls_cap": 10 ** 6}))
    code, out = stop(h, "Done.")
    assert code == 0 and "changed outside the harness" in out["systemMessage"]
    assert sessionstate.jobs(ctx_of(h))[job]["state"] == "STOPPED_TAMPER"


def test_hooks_json_has_the_stop_entry():
    cfg = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    (g,) = cfg["hooks"]["Stop"]
    assert g["hooks"][0]["timeout"] == 10 and hookmain.watchdog("Stop") == 9


def test_the_tokeniser_matches_loop_contract():
    lc = Path(ROOT).parent / "loop-contract-skill" / "skills" / "loop-contract" / "scripts" / "lc_normalize.py"
    if not lc.is_file():
        pytest.skip("loop-contract checkout not found")
    body = lambda s: s[s.index("import re"):s.index('if __name__') if 'if __name__' in s else len(s)].strip()
    assert body(lc.read_text()) == body((ROOT / "lib" / "harness_core" / "normalize.py").read_text())
