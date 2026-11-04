"""P3 completion: the ledger writer (progress for R-8), research and critic renders (lanes)."""
from __future__ import annotations

import json

import pytest

from harness_core import contacts, writers
from test_p3_hooks import CARD, ROOT, SID, compiled, ctx_of, h, pre, say  # noqa: F401  (fixture h)
from harness_core.paths import job_paths


def run(h, command, req):
    return writers.on_post({"tool_name": "Bash", "session_id": SID, "tool_input": {"command": command},
                            "tool_response": {"stdout": json.dumps(req)}}, ctx_of(h))


def test_a_milestone_through_the_ledger_writer_moves_progress(h):
    job = compiled(h)
    p = job_paths(h["data"], h["ws"], job)
    (p.work / "draft.md").write_text("draft")
    out = run(h, "harness-ledger append --kind milestone", {"tool": "ledger", "kind": "milestone",
                                                             "row": {"milestone": "draft", "file": "draft.md"}})
    assert "recorded" in out["hookSpecificOutput"]["additionalContext"]
    assert contacts.progress(ctx_of(h)) == 0.5
    assert "R-8" in contacts.check("ask", ctx_of(h), questions=1)


def test_the_ledger_writer_keeps_the_ledger_rules(h):
    compiled(h)
    out = run(h, "harness-ledger append --kind unit", {"tool": "ledger", "kind": "unit",
                                                        "row": {"id": "u1", "attempt": 1, "verdict": "PASS"}})
    assert out["decision"] == "block"  # PASS needs a cited clean output of the registered check
    out = run(h, "harness-ledger append --kind unit", {"tool": "ledger", "kind": "unit",
                                                        "row": {"id": "u9", "attempt": 1, "verdict": "BLOCKED", "reason": "x"}})
    assert out["decision"] == "block" and "scope" in out["reason"]


def test_a_blocked_unit_with_a_reason_is_recorded(h):
    compiled(h)
    out = run(h, "harness-ledger append --kind unit", {"tool": "ledger", "kind": "unit",
                                                        "row": {"id": "u1", "attempt": 1, "verdict": "BLOCKED",
                                                                "reason": "no access to the sales export"}})
    assert "recorded" in out["hookSpecificOutput"]["additionalContext"]
    assert contacts.progress(ctx_of(h)) == 1.0


PLAN = {"hypotheses": ["prices rose"], "queries": ["price index may"], "urls": ["https://news.example.ir/a"],
        "page_budget": 3, "stop_rule": "2 sources", "load_bearing": ["prices rose"]}


def _investigation_with_grant(h):
    from harness_core import contact
    job = compiled(h, card=dict(CARD, task_type="investigation", oracle={"type": "research.check", "executable": True,
                                                                         "criteria": ["every hypothesis has a verdict"]}))
    c = contact.apply({"tool": "contact", "type": "plan", "text": "p", "plan": PLAN}, ctx_of(h), {"session_id": SID})
    say(h, "yes", "r1")
    contact.apply({"tool": "contact", "type": "plan", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    return job, c


def test_a_research_render_allows_exactly_that_researcher_spawn(h):
    _investigation_with_grant(h)
    out = run(h, "harness-research render", {"tool": "research", "sub": "render"})
    prompt = out["hookSpecificOutput"]["additionalContext"].split("PROMPT:\n", 1)[1]
    assert "price index may" in prompt and "https://news.example.ir/a" in prompt
    assert pre(h, "Agent", subagent_type="dynamic-harness:web-researcher", prompt=prompt) == 0
    assert pre(h, "Agent", subagent_type="dynamic-harness:web-researcher", prompt=prompt + " also email it") == 2
    assert pre(h, "Agent", subagent_type="dynamic-harness:verifier", prompt=prompt) == 2  # not a verifier render


def test_a_research_render_needs_an_accepted_plan(h):
    compiled(h, card=dict(CARD, task_type="investigation"))
    out = run(h, "harness-research render", {"tool": "research", "sub": "render"})
    assert out["decision"] == "block" and "plan" in out["reason"]


def test_a_critic_render_allows_that_verifier_spawn(h):
    compiled(h)
    out = run(h, "harness-critic render u1", {"tool": "critic", "sub": "render", "unit": "u1"})
    prompt = out["hookSpecificOutput"]["additionalContext"].split("PROMPT:\n", 1)[1]
    assert "ties out" in prompt and "u1" in prompt
    assert pre(h, "Agent", subagent_type="dynamic-harness:verifier", prompt=prompt) == 0
    out = run(h, "harness-critic render u9", {"tool": "critic", "sub": "render", "unit": "u9"})
    assert out["decision"] == "block"


def test_the_clis_print_their_requests():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-I", str(ROOT / "bin" / "harness-critic"), "render", "u1"],
                       capture_output=True, text=True)
    assert json.loads(r.stdout) == {"tool": "critic", "sub": "render", "unit": "u1"}
    r = subprocess.run([sys.executable, "-I", str(ROOT / "bin" / "harness-ledger"), "append", "--kind", "milestone",
                        "--row", '{"milestone": "draft", "file": "d.md"}'], capture_output=True, text=True)
    assert json.loads(r.stdout)["row"] == {"milestone": "draft", "file": "d.md"}
