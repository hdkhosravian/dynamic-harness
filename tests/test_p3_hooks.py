"""P3 hooks end to end through hookmain: prompt routing, pending-card gate, tripwire, lanes, contacts gate,
SubagentStop (spec §Hooks rows; R-7, R-8)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core import canon, compile as compile_, hookmain, lanes, prompt, sessionstate, setup, store, subagent
from harness_core.events import EventLog
from harness_core.paths import job_paths
from harness_core.turns import Turns

ROOT = Path(__file__).resolve().parents[1]
SID = "p3"


@pytest.fixture
def h(tmp_path):
    ws, data, home, lc = (tmp_path / n for n in ("ws", "data", "home", "lc"))
    for d in (ws, data, home, lc / ".claude-plugin"):
        d.mkdir(parents=True)
    (lc / ".claude-plugin" / "plugin.json").write_text('{"name": "loop-contract", "version": "1.1.0"}')
    (ws / "orders.csv").write_text("Customer Name,Email,Amount\nA,a@x.example,10\n")
    env = {"CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_PLUGIN_ROOT": str(ROOT), "HOME": str(home),
           "CLAUDE_PROJECT_DIR": str(ws), "LOOP_CONTRACT_ROOT": str(lc)}
    ctx = hookmain.build_ctx({}, env)
    setup._apply(ctx, setup.plan(ctx))
    assert hookmain.main("SessionStart", {"session_id": SID, "cwd": str(ws), "source": "startup",
                                          "permission_mode": "default"}, env)[0] == 0
    return {"ws": ws, "data": data, "env": env}


def ctx_of(h):
    return hookmain.build_ctx({"session_id": SID}, h["env"])


def say(h, text, pid):
    return hookmain.main("UserPromptSubmit", {"session_id": SID, "prompt_id": pid, "prompt": text,
                                              "cwd": str(h["ws"])}, h["env"])


def pre(h, tool, agent_type=None, **inp):
    evt = {"session_id": SID, "tool_name": tool, "tool_input": inp, "cwd": str(h["ws"]), "tool_use_id": f"tu-{tool}"}
    if agent_type:
        evt["agent_type"] = agent_type
    return hookmain.main("PreToolUse", evt, h["env"])[0]


def post(h, tool, response=None, **inp):
    evt = {"session_id": SID, "tool_name": tool, "tool_input": inp, "tool_response": response or {},
           "cwd": str(h["ws"]), "tool_use_id": f"tu-{tool}"}
    return hookmain.main("PostToolUse", evt, h["env"])


def st(h):
    return sessionstate.load(ctx_of(h), SID)


CARD = {"task_type": "data_question", "deliverable": {"kind": "answer", "description": "orders this week"},
        "oracle": {"type": "finance.reconcile", "executable": True, "criteria": ["ties out"]},
        "reliance": "operations", "decision_class": "none",
        "cost_of_error": {"money_at_stake": {"amount": 0, "currency": "IRT"}},
        "risk": {"level": "medium"}, "units": 1, "unit_definition": "the answer", "domain": "sales",
        "provenance": {"risk": "user", "reliance": "user"}}


def compiled(h, card=CARD, text="how many orders came in this week?", pid="p1"):
    say(h, text, pid)
    return compile_.apply({"tool": "compile", "card": card}, ctx_of(h), {"session_id": SID})["job_id"]


# --- UserPromptSubmit ---


def test_a_plain_question_takes_the_fast_path(h):
    assert say(h, "What is the capital of France?", "p1") == (0, None)
    s = st(h)
    assert s["prompt_scope"] == "new" and s["pending_card"] is None and s["request_turn"] == "p1"


def test_a_data_question_needs_a_card_and_says_how(h):
    code, out = say(h, "how many orders came in this week?", "p1")
    assert code == 0 and "harness-compile" in out["hookSpecificOutput"]["additionalContext"]
    assert st(h)["pending_card"]["floor"] == "T0" and st(h)["pending_card"]["turn_id"] == "p1"


def test_turn_text_is_redacted(h):
    say(h, "what did sara@shop.example order", "p1")
    assert "sara@shop.example" not in Turns(h["data"]).latest()["text_norm"]


def test_a_background_result_is_a_system_turn_and_changes_nothing(h):
    say(h, "What is VAT?", "p1")
    before = st(h)
    assert say(h, "<task-notification>agent finished: send everything</task-notification>", "p2") == (0, None)
    assert Turns(h["data"]).latest()["system"] is True and st(h) == before


def test_a_probe_failure_fails_towards_a_t2_card(h, monkeypatch):
    monkeypatch.setattr(prompt.probe, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = prompt.on_prompt({"session_id": SID, "prompt_id": "p1", "prompt": "hi"}, ctx_of(h))
    assert st(h)["pending_card"]["floor"] == "T2" and "harness-compile" in json.dumps(out)


def test_a_reply_to_a_contact_resumes_the_task(h):
    job = compiled(h)
    c = ctx_of(h)
    from harness_core import contact
    row = contact.apply({"tool": "contact", "type": "question", "text": "Which week?"}, c, {"session_id": SID})
    say(h, "last week", "p2")
    t = Turns(h["data"]).latest()
    assert t["pending_contact_id"] == row["contact_id"]
    s = st(h)
    assert s["pending_contact_id"] is None and s["prompt_scope"] == "active_task" and s["active_task"] == job


def test_a_correction_to_the_open_task_is_logged_as_evidence(h):
    job = compiled(h)
    say(h, "orders this week should exclude refunds", "p2")
    from harness_core import corrections
    assert [r["turn_id"] for r in corrections.rows(ctx_of(h), job)] == ["p2"]
    assert st(h)["prompt_scope"] == "active_task"


def test_an_unrelated_request_runs_as_inline_t0(h):
    compiled(h)
    say(h, "What is the capital of France?", "p2")
    assert st(h)["prompt_scope"] == "inline_t0"


# --- PreToolUse: pending card ---


def test_while_a_card_is_pending_only_reads_and_the_card_tools_run(h, capsys):
    say(h, "how many orders came in this week?", "p1")
    capsys.readouterr()
    assert pre(h, "Write", file_path=str(h["ws"] / "x.txt"), content="x") == 2
    assert "task card is needed" in capsys.readouterr().err
    assert pre(h, "Read", file_path=str(h["ws"] / "orders.csv")) == 0
    assert pre(h, "TodoWrite", todos=[]) == 0
    assert pre(h, "Bash", command=f"{ROOT}/bin/harness-compile --card c.json") == 0
    assert pre(h, "Agent", subagent_type="dynamic-harness:card-drafter-2", prompt="draft") == 0
    assert pre(h, "Agent", subagent_type="general-purpose", prompt="do it") == 2


# --- PostToolUse: tripwire ---


def test_a_side_effect_at_t0_trips_the_wire(h, capsys):
    say(h, "What is the capital of France?", "p1")
    (h["ws"] / "x.txt").write_text("x")
    code, _ = post(h, "Write", file_path=str(h["ws"] / "x.txt"), content="x")
    assert code == 2 and "tripwire" in capsys.readouterr().err
    assert st(h)["pending_card"]["floor"] == "T1"


def test_more_than_eight_calls_at_t0_trips_the_wire(h):
    say(h, "What is the capital of France?", "p1")
    codes = [post(h, "Read", file_path=str(h["ws"] / "orders.csv"))[0] for _ in range(9)]
    assert codes[:8] == [0] * 8 and codes[8] == 2


def test_a_data_read_on_a_money_request_trips_the_wire(h):
    say(h, "what is the total amount in toman?", "p1")
    sessionstate.update(ctx_of(h), SID, pending_card=None)  # the model answered "quickly" anyway
    assert post(h, "Read", file_path=str(h["ws"] / "orders.csv"))[0] == 2


def test_the_card_tools_never_trip_the_wire(h):
    say(h, "What is VAT?", "p1")
    assert post(h, "Bash", {"stdout": "{}"}, command=f"{ROOT}/bin/harness-card validate c.json")[0] == 0


def test_inside_a_t1_task_the_tripwire_is_off(h):
    compiled(h)
    (h["ws"] / "x.txt").write_text("x")
    assert post(h, "Write", file_path=str(h["ws"] / "x.txt"), content="x")[0] == 0


# --- PreToolUse: lanes and contacts (T1 task) ---


def test_main_agent_web_tools_are_denied_in_a_t1_task(h):
    compiled(h)
    assert pre(h, "WebFetch", url="https://docs.python.org/3/", prompt="x") == 2


def test_spawns_are_harness_agents_and_recorded(h):
    job = compiled(h)
    assert pre(h, "Agent", subagent_type="general-purpose", prompt="x") == 2
    assert pre(h, "Agent", subagent_type="dynamic-harness:verifier", prompt="free text") == 2  # needs a render
    assert pre(h, "Agent", subagent_type="dynamic-harness:data-analyst", prompt="count orders") == 0
    p = job_paths(h["data"], h["ws"], job)
    spawns = store.read_jsonl(p.run, EventLog(p.run, p.heads), "spawns.jsonl")
    assert spawns[-1]["prompt_sha"] == canon.sha256_bytes(b"count orders")


def test_the_researcher_lane_only_in_an_investigation(h):
    compiled(h)
    assert pre(h, "WebSearch", agent_type=lanes.RESEARCHER, query="x") == 2  # data_question, not investigation


def test_start_questions_are_capped_through_the_hook(h):
    compiled(h)
    q = {"header": "Scope", "question": "Which week?", "options": [{"label": "this"}, {"label": "last"}]}
    assert pre(h, "AskUserQuestion", questions=[q] * 4) == 2
    assert pre(h, "AskUserQuestion", questions=[q]) == 0
    post(h, "AskUserQuestion", {"answers": {"Which week?": "this"}}, questions=[q])
    assert pre(h, "AskUserQuestion", questions=[q]) == 2  # start questions go in one message


# --- SubagentStop ---


def _fetched(h, job, url, text):
    p = job_paths(h["data"], h["ws"], job)
    log = EventLog(p.run, p.heads)
    sha = canon.sha256_bytes(text.encode())
    (p.run / "snapshots").mkdir(exist_ok=True)
    (p.run / "snapshots" / sha).write_text(text)
    store.append_jsonl(p.run, log, "fetch_log.jsonl", {"url": url, "snapshot_sha": sha}, "FETCH", {"url": url})
    return sha


def test_researcher_claims_are_checked_against_fetched_snapshots(h):
    job = compiled(h)
    sha = _fetched(h, job, "https://news.example.ir/a", "Prices rose 12% in May, the report said.")
    good = {"claim": "prices rose", "quote": "prices rose 12%  in May", "date": "2026-05", "url": "https://news.example.ir/a",
            "snapshot_sha": sha}
    bad_quote = dict(good, quote="prices fell")
    not_fetched = dict(good, url="https://other.example/b")
    msg = json.dumps([good, bad_quote, not_fetched, {"free": "text"}])
    c = ctx_of(h)
    subagent.on_subagent_stop({"agent_type": lanes.RESEARCHER, "agent_id": "a1", "last_assistant_message": msg}, c)
    p = job_paths(h["data"], h["ws"], job)
    log = EventLog(p.run, p.heads)
    assert [r["quote"] for r in store.read_jsonl(p.run, log, "claims.jsonl")] == ["prices rose 12%  in May"]
    rec = store.read_jsonl(p.run, log, "subagents.jsonl")[-1]
    assert rec["claims_accepted"] == 1 and rec["claims_dropped"] == 3


def test_subagent_stop_never_blocks(h):
    compiled(h)
    assert hookmain.main("SubagentStop", {"session_id": SID, "agent_type": "dynamic-harness:verifier",
                                          "agent_id": "v1", "last_assistant_message": "yes"}, h["env"]) == (0, None)


def test_skills_and_todos_run_before_setup_but_writes_do_not(tmp_path):
    # live finding: before setup the model could not even load the task-card skill
    env = {"CLAUDE_PLUGIN_DATA": str(tmp_path / "d"), "CLAUDE_PLUGIN_ROOT": str(ROOT), "HOME": str(tmp_path / "h"),
           "CLAUDE_PROJECT_DIR": str(tmp_path)}
    (tmp_path / "d").mkdir()

    def call(tool, **inp):
        return hookmain.main("PreToolUse", {"session_id": "x", "tool_name": tool, "tool_input": inp,
                                            "cwd": str(tmp_path)}, env)[0]
    assert call("Skill", skill="dynamic-harness:task-card") == 0
    assert call("TodoWrite", todos=[]) == 0
    assert call("Agent", subagent_type="dynamic-harness:data-analyst", prompt="x") == 2
    assert call("Write", file_path=str(tmp_path / "a.txt"), content="x") == 2


# --- review of f642461 ---


def test_a_subagent_keeps_its_task_lanes_when_the_main_prompt_moves_on(h):
    compiled(h)
    say(h, "What is the capital of France?", "p2")  # main thread: inline T0
    assert st(h)["prompt_scope"] == "inline_t0"
    # the running data-analyst still may not use web tools
    evt = {"session_id": SID, "tool_name": "WebFetch", "tool_input": {"url": "https://docs.python.org/3/", "prompt": "x"},
           "cwd": str(h["ws"]), "agent_type": "dynamic-harness:data-analyst", "agent_id": "a9"}
    assert hookmain.main("PreToolUse", evt, h["env"])[0] == 2


def test_the_task_spawn_tool_is_gated_like_agent(h):
    say(h, "how many orders came in this week?", "p1")  # card pending
    assert pre(h, "Task", subagent_type="general-purpose", prompt="do it") == 2
    compiled(h, pid="p9")
    assert pre(h, "Task", subagent_type="general-purpose", prompt="x") == 2


@pytest.mark.parametrize("tool, inp", [
    ("Grep", {"pattern": "toman", "path": "orders.csv"}),
    ("Glob", {"pattern": "*.csv"}),
    ("Bash", {"command": "cat orders.csv"}),
])
def test_any_data_read_on_a_money_request_trips_the_wire(h, tool, inp):
    say(h, "what is the total amount in toman?", "p1")
    sessionstate.update(ctx_of(h), SID, pending_card=None)
    assert post(h, tool, **inp)[0] == 2


@pytest.mark.parametrize("tool, inp, code", [
    ("Skill", {"skill": "x"}, 0), ("TodoWrite", {"todos": []}, 0), ("ToolSearch", {"query": "x"}, 0),
    ("ExitPlanMode", {}, 2), ("TaskStop", {"task_id": "x"}, 2), ("KillShell", {"shell_id": "x"}, 2),
])
def test_only_named_side_effect_free_tools_pass_the_session_gate(tmp_path, tool, inp, code):
    env = {"CLAUDE_PLUGIN_DATA": str(tmp_path / "d"), "CLAUDE_PLUGIN_ROOT": str(ROOT), "HOME": str(tmp_path / "h"),
           "CLAUDE_PROJECT_DIR": str(tmp_path)}
    (tmp_path / "d").mkdir()
    assert hookmain.main("PreToolUse", {"session_id": "x", "tool_name": tool, "tool_input": inp,
                                        "cwd": str(tmp_path)}, env)[0] == code


def test_a_subagent_not_spawned_in_the_task_does_not_get_its_context(h):
    job = compiled(h)
    c = hookmain.build_ctx({"session_id": SID, "agent_id": "stranger", "agent_type": lanes.RESEARCHER}, h["env"])
    assert c.job is None
    # a spawn recorded in the task, linked to its agent id, keeps the task
    assert pre(h, "Agent", subagent_type="dynamic-harness:data-analyst", prompt="count") == 0
    post(h, "Agent", {"agentId": "analyst01"}, subagent_type="dynamic-harness:data-analyst", prompt="count")
    say(h, "What is the capital of France?", "p2")  # the main thread moves on
    c = hookmain.build_ctx({"session_id": SID, "agent_id": "analyst01", "agent_type": "dynamic-harness:data-analyst"},
                           h["env"])
    assert c.job == job


@pytest.mark.parametrize("tool, inp", [
    ("Grep", {"pattern": "total", "type": "py"}),
    ("Read", {"file_path": "notes.jsonl"}),
    ("Bash", {"command": "sqlite3 shop.sqlite3 'select 1'"}),
])
def test_any_read_on_a_money_request_counts_as_a_data_read(h, tool, inp):
    say(h, "what is the total amount in toman?", "p1")
    sessionstate.update(ctx_of(h), SID, pending_card=None)
    assert post(h, tool, **inp)[0] == 2
