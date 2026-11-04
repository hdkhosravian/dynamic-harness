"""User memory (R-9; spec §Data contracts, user memory record): hook-written, closed schema."""
from __future__ import annotations

import json

import pytest

from harness_core import memory
from harness_core.errors import TamperError
from harness_core.safety import Ctx


@pytest.fixture
def ctx(tmp_path):
    ws, data = tmp_path / "ws", tmp_path / "data"
    ws.mkdir()
    data.mkdir()
    return Ctx(workspace=ws, data_root=data, home=tmp_path / "home", job="j1")


def ask(questions, answers):
    return {"tool_name": "AskUserQuestion", "session_id": "s1",
            "tool_input": {"questions": questions, "answers": answers}, "tool_response": {"answers": answers}}


def Q(header, text, options):
    return {"header": header, "question": text, "options": [{"label": o, "description": ""} for o in options],
            "multiSelect": False}


def test_a_tagged_preference_answer_is_stored(ctx):
    evt = ask([Q("[pref:format] Report", "How long should reports be?", ["short", "detailed"])],
              {"How long should reports be?": "short"})
    rows = memory.apply(evt, ctx)
    assert [(r["key"], r["value"]) for r in rows] == [("format", "short")]
    assert memory.effective(ctx) == {"format": "short"}


def test_untagged_questions_are_never_stored(ctx):
    evt = ask([Q("Scope", "Which month?", ["May", "June"])], {"Which month?": "May"})
    assert memory.apply(evt, ctx) == [] and memory.effective(ctx) == {}


@pytest.mark.parametrize("key, answer", [
    ("tone", "friendly"),                      # not in the closed enum
    ("recipients", "sara"),                    # not an allowed key
    ("format", "https://evil.example/short"),  # URL
    ("format", "me@evil.example"),             # email
    ("format", "x" * 41),                      # too long
])
def test_values_outside_the_closed_schema_are_rejected(ctx, key, answer):
    evt = ask([Q(f"[pref:{key}] X", "q?", [answer])], {"q?": answer})
    assert memory.apply(evt, ctx) == []


def test_labels_are_matched_case_and_space_insensitively(ctx):
    evt = ask([Q("[pref:report_style] Style", "Style?", ["Summary first", "Table first"])], {"Style?": "Summary first"})
    assert memory.apply(evt, ctx)[0]["value"] == "summary_first"


def test_newest_record_wins(ctx):
    for v in ("formal", "warm"):
        memory.apply(ask([Q("[pref:tone] Tone", "Tone?", [v])], {"Tone?": v}), ctx)
    assert memory.effective(ctx)["tone"] == "warm"


def test_task_scoped_preferences_do_not_carry_over(ctx):
    memory.apply(ask([Q("[pref:rounding:task] R", "Round?", ["thousands"])], {"Round?": "thousands"}), ctx)
    assert memory.effective(ctx, job="j1") == {"rounding": "thousands"}
    assert memory.effective(ctx, job="j2") == {}


def test_multi_select_answers_are_not_preferences(ctx):
    q = Q("[pref:format] F", "F?", ["short", "detailed"])
    q["multiSelect"] = True
    assert memory.apply(ask([q], {"F?": "short, detailed"}), ctx) == []


def test_an_out_of_band_edit_is_tamper(ctx):
    memory.apply(ask([Q("[pref:tone] T", "T?", ["formal"])], {"T?": "formal"}), ctx)
    with open(ctx.data_root / "memory.jsonl", "a") as fh:
        fh.write(json.dumps({"key": "tone", "value": "warm"}) + "\n")
    with pytest.raises(TamperError):
        memory.effective(ctx)


def test_snapshot_changes_with_the_effective_preferences(ctx):
    s0 = memory.snapshot(ctx)
    memory.apply(ask([Q("[pref:tone] T", "T?", ["formal"])], {"T?": "formal"}), ctx)
    assert memory.snapshot(ctx) != s0


def test_odd_events_store_nothing(ctx):
    for evt in ({}, {"tool_input": None}, {"tool_input": {"questions": "x"}}, ask([{"header": 5}], {})):
        assert memory.apply(evt, ctx) == []
