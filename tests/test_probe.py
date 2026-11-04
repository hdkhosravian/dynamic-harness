"""Probe and pre-filter (spec §The eight steps, steps 1-2): deterministic, no model."""
from __future__ import annotations

import pytest

from harness_core import probe
from harness_core.safety import Ctx


@pytest.fixture
def ctx(tmp_path):
    ws, data = tmp_path / "ws", tmp_path / "data"
    ws.mkdir()
    data.mkdir()
    (ws / "customers.csv").write_text("Customer Name,Email,Amount Owed\nA,a@x.example,10\n")
    return Ctx(workspace=ws, data_root=data, home=tmp_path / "home")


def P(ctx, text):
    return probe.run(text, ctx)


@pytest.mark.parametrize("text", [
    "What is the capital of France?",
    "Explain what a balance sheet is",
    "rewrite this sentence to sound friendlier: thanks for waiting",
    "چرا آسمان آبی است؟",
])
def test_plain_questions_take_the_fast_path(ctx, text):
    p = P(ctx, text)
    assert probe.prefilter(p) == "T0-fast", p


@pytest.mark.parametrize("text", [
    "Which customers owe more than 30 days?",            # money word
    "How many orders came in last week",                 # quantity
    "summarise customers.csv",                           # file named
    "send the invoice to Sara",                          # action verb
    "should I take this loan?",                          # decision class
    "در ۳ ماه گذشته چند مشتری داشتیم؟",                  # Persian digits + quantity
    "مشتری‌های بدهکار را بگو",                           # Persian money word
])
def test_anything_with_a_hint_needs_a_card(ctx, text):
    assert probe.prefilter(P(ctx, text)) == "card-needed"


def test_imperative_irreversible_verb_sets_a_t2_floor(ctx):
    p = P(ctx, "please send the overdue reminders to every customer")
    assert "external_message" in p["irreversible_classes"] and p["floor_tier"] == "T2"


def test_the_same_verb_in_a_question_is_not_an_action(ctx):
    p = P(ctx, "did we send the reminders last week?")
    assert p["irreversible_classes"] == [] and p["question_only"] is True
    assert "send" in p["action_verbs"]  # still enough for card-needed


def test_persian_imperative(ctx):
    p = P(ctx, "این فاکتور را برای مشتری ایمیل کن")
    assert "external_message" in p["irreversible_classes"] and p["floor_tier"] == "T2"


def test_write_verb_with_a_file_makes_a_write_target_and_t1_floor(ctx):
    p = P(ctx, "update customers.csv with the new totals")
    assert p["write_targets"] == ["customers.csv"] and p["floor_tier"] == "T1"


def test_named_workspace_file_is_scanned_for_personal_data(ctx):
    p = P(ctx, "summarise customers.csv")
    assert {"person_name", "email"} <= set(p["data_classes"])
    assert p["quantitative"] is True  # a data file is quantitative work


def test_files_outside_the_workspace_are_named_but_not_read(ctx, tmp_path):
    (tmp_path / "secret.csv").write_text("email\n")
    p = P(ctx, "summarise ../secret.csv")
    assert "../secret.csv" in p["files_named"] and "email" not in p["data_classes"]


def test_request_text_personal_data_is_tagged(ctx):
    assert "email" in P(ctx, "what did sara@shop.example order?")["data_classes"]


def test_decision_hits_by_class(ctx):
    p = P(ctx, "what dose of ibuprofen should I take")
    assert "medical" in p["decision_hits"]


def test_write_capable_connector_named_sets_t1_floor(ctx):
    import json
    (ctx.data_root / "business").mkdir()
    (ctx.data_root / "business" / "connector_registry.json").write_text(json.dumps(
        {"mcp__gmail__create_draft": "draft", "mcp__sheets__read": "read"}))
    p = P(ctx, "check gmail for the latest order")
    assert p["connectors"] == ["gmail"] and p["floor_tier"] == "T1"
    assert P(ctx, "what is in sheets")["floor_tier"] == "T0"


def test_continuation_shape(ctx):
    assert P(ctx, "continue")["continuation"] is True
    assert P(ctx, "ادامه بده")["continuation"] is True
    assert P(ctx, "what is VAT?")["continuation"] is False


def test_probe_validates_against_its_schema(ctx):
    from harness_core import schema
    schema.check(P(ctx, "send customers.csv to sara@shop.example for 2,000 toman"), "probe")


def test_probe_is_deterministic(ctx):
    t = "update customers.csv and email Sara the total in toman"
    assert P(ctx, t) == P(ctx, t)


def test_huge_or_odd_input_does_not_crash(ctx):
    for t in ("", " ", "\x00" * 10, "?" * 100000, "a" * 200000):
        p = P(ctx, t)
        assert probe.prefilter(p) in ("T0-fast", "card-needed")
