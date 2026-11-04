"""Lease (spec §State machine "Lease": one owner session, heartbeat, epoch fencing, takeover)."""
from __future__ import annotations

import json
import time

import pytest

from harness_core import contact, hookmain, lease, sessionstate, store, writers
from harness_core.errors import Denied
from harness_core.events import EventLog
from harness_core.paths import job_paths
from test_p3_hooks import CARD, SID, compiled, ctx_of, h, say  # noqa: F401  (fixture h)

OTHER = "other-session"


def start_other(h):
    assert hookmain.main("SessionStart", {"session_id": OTHER, "cwd": str(h["ws"]), "source": "startup",
                                          "permission_mode": "default"}, h["env"])[0] == 0


def ctx_for(h, sid, job=None):
    c = hookmain.build_ctx({"session_id": sid}, h["env"])
    if job:
        c.job = job
    return c


def test_compile_acquires_the_lease(h):
    job = compiled(h)
    l = lease.read(ctx_of(h), job)
    assert l["session_id"] == SID and l["epoch"] == 1


def test_a_second_session_gets_read_only_while_the_lease_is_live(h):
    job = compiled(h)
    start_other(h)
    sessionstate.update(ctx_for(h, OTHER), OTHER, active_task=job, prompt_scope="active_task")
    evt = {"session_id": OTHER, "tool_name": "Write", "cwd": str(h["ws"]),
           "tool_input": {"file_path": str(h["ws"] / "x.txt"), "content": "x"}}
    assert hookmain.main("PreToolUse", evt, h["env"])[0] == 2
    evt.update(tool_name="Read", tool_input={"file_path": str(h["ws"] / "orders.csv")})
    assert hookmain.main("PreToolUse", evt, h["env"])[0] == 0


def test_writes_from_a_non_owner_session_are_refused(h):
    job = compiled(h)
    start_other(h)
    c = ctx_for(h, OTHER, job)
    with pytest.raises(Denied, match="another session"):
        lease.require(c, job, OTHER)
    out = writers.on_post({"tool_name": "Bash", "session_id": OTHER, "tool_input": {"command": "harness-contact question"},
                           "tool_response": {"stdout": json.dumps({"tool": "contact", "type": "question", "text": "q"})}}, c)
    assert out["decision"] == "block" and "another session" in out["reason"]


def test_a_stale_lease_is_taken_over_by_continue_and_fences_the_old_session(h, monkeypatch):
    job = compiled(h)
    start_other(h)
    real = time.time
    monkeypatch.setattr(lease.time, "time", lambda: real() + lease.TTL + 5)
    code, out = hookmain.main("UserPromptSubmit", {"session_id": OTHER, "prompt_id": "o1", "prompt": "continue",
                                                   "cwd": str(h["ws"])}, h["env"])
    assert code == 0 and "took over" in out["hookSpecificOutput"]["additionalContext"]
    l = lease.read(ctx_of(h), job)
    assert l["session_id"] == OTHER and l["epoch"] == 2
    assert sessionstate.load(ctx_for(h, OTHER), OTHER)["active_task"] == job
    with pytest.raises(Denied, match="another session"):
        lease.require(ctx_of(h), job, SID)  # the old owner is fenced


def test_continue_with_a_live_lease_elsewhere_does_not_take_over(h):
    job = compiled(h)
    start_other(h)
    code, out = hookmain.main("UserPromptSubmit", {"session_id": OTHER, "prompt_id": "o1", "prompt": "continue",
                                                   "cwd": str(h["ws"])}, h["env"])
    assert "another session" in out["hookSpecificOutput"]["additionalContext"]
    assert lease.read(ctx_of(h), job)["session_id"] == SID


def test_heartbeats_are_throttled(h):
    job = compiled(h)
    p = job_paths(h["data"], h["ws"], job)
    n = lambda: sum(e["type"] == "LEASE_HEARTBEAT" for e in EventLog(p.run, p.heads).events())
    before = n()
    for _ in range(5):
        lease.heartbeat(ctx_of(h), job, SID)
    assert n() == before  # fresh lease: nothing to write yet
