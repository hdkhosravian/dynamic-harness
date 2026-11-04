"""v1 learning: corrections are evidence only (spec §Learning); the shadow second draft (spec §Where the
model decides: second card draft, shadow mode first)."""
from __future__ import annotations

import copy

from pathlib import Path

import pytest

from harness_core import corrections, probe, shadow, store
from harness_core.errors import Denied
from harness_core.paths import job_paths
from harness_core.safety import Ctx
from harness_core.turns import Turns


@pytest.fixture
def ctx(tmp_path):
    ws, data = tmp_path / "ws", tmp_path / "data"
    ws.mkdir()
    data.mkdir()
    return Ctx(workspace=ws, data_root=data, home=tmp_path / "home", job="j1",
               plugin_root=Path(__file__).resolve().parents[1])  # the shipped packs register card oracles


def test_a_correction_from_a_real_turn_is_logged_verbatim(ctx):
    t = Turns(ctx.data_root).append({"turn_id": "t1", "text_norm": "no, use the June numbers, not May"})
    row = corrections.log(ctx, "j1", t["turn_id"])
    assert row["text_norm"] == "no, use the June numbers, not May" and row["turn_id"] == "t1"
    p = job_paths(ctx.data_root, ctx.workspace, "j1")
    assert corrections.rows(ctx, "j1") == [row]
    assert any(e["type"] == "CORRECTION" for e in store.EventLog(p.run, p.heads).events())


def test_system_turns_and_unknown_turns_are_not_corrections(ctx):
    Turns(ctx.data_root).append({"turn_id": "t1", "text_norm": "<task-notification>agent done"})
    with pytest.raises(Denied):
        corrections.log(ctx, "j1", "t1")
    with pytest.raises(Denied):
        corrections.log(ctx, "j1", "nope")


def test_logging_a_correction_changes_nothing_else(ctx):
    Turns(ctx.data_root).append({"turn_id": "t1", "text_norm": "fix the total"})
    corrections.log(ctx, "j1", "t1")
    p = job_paths(ctx.data_root, ctx.workspace, "j1")
    assert sorted(x.name for x in p.run.iterdir() if not x.name.startswith("events")) == ["corrections.jsonl"]


DRAFT = {
    "task_type": "data_question",
    "deliverable": {"kind": "answer", "description": "late customers"},
    "oracle": {"type": "finance.reconcile", "executable": True, "criteria": ["ties out"]},
    "reliance": "money", "decision_class": "none",
    "cost_of_error": {"money_at_stake": {"amount": 0, "currency": "IRT"}},
    "risk": {"level": "medium"}, "units": 1, "unit_definition": "the answer", "domain": "finance",
    "provenance": {"risk": "user", "reliance": "user"},
}


def test_shadow_logs_the_tier_each_draft_would_get_and_the_merge(ctx):
    p = probe.run("Which customers are late?", ctx)
    b = copy.deepcopy(DRAFT)
    b["risk"] = {"level": "high"}
    row = shadow.log_second_draft(ctx, "j1", DRAFT, b, p)
    assert (row["tier_first"], row["tier_second"], row["tier_merged"]) == ("T1", "T2", "T2")
    assert row["agree"] is False and "risk" in row["fields"]
    assert shadow.rows(ctx, "j1") == [row]


def test_shadow_with_an_invalid_second_draft_logs_the_errors(ctx):
    p = probe.run("Which customers are late?", ctx)
    row = shadow.log_second_draft(ctx, "j1", DRAFT, {"task_type": "nope"}, p)
    assert row["tier_second"] is None and row["second_errors"]
