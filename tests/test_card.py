"""Card draft validation, normalisation and merge (spec §Task card, field rules; §Tier selection merge)."""
from __future__ import annotations

import copy
import json

import pytest

from harness_core import card, probe, schema
from harness_core.safety import Ctx


@pytest.fixture
def ctx(tmp_path):
    ws, data = tmp_path / "ws", tmp_path / "data"
    ws.mkdir()
    data.mkdir()
    return Ctx(workspace=ws, data_root=data, home=tmp_path / "home")


DRAFT = {
    "task_type": "data_question",
    "deliverable": {"kind": "answer", "description": "late customers with amounts and a total"},
    "oracle": {"type": "finance.reconcile_ar", "executable": True, "criteria": ["total ties to AR aging"]},
    "reliance": "money", "decision_class": "none",
    "cost_of_error": {"money_at_stake": {"amount": 0, "currency": "IRT"}},
    "risk": {"level": "medium"}, "units": 1, "unit_definition": "the answer", "domain": "finance",
    "provenance": {"risk": "user", "reliance": "user"},
}


def draft(**kw):
    d = copy.deepcopy(DRAFT)
    d.update(kw)
    return d


def P(ctx, text="Which customers are more than 30 days late?"):
    return probe.run(text, ctx)


def test_a_good_draft_validates():
    assert card.validate(DRAFT) == []


@pytest.mark.parametrize("bad, needle", [
    ({"task_type": "poetry"}, "task_type"),
    ({"units": 0}, "units"),
    ({"risk": {"level": "extreme"}}, "risk"),
    ({"job_id": "x"}, "job_id"),  # code-owned: the draft may not set it
    ({"reversibility": "R0"}, "reversibility"),
])
def test_bad_drafts_list_their_errors(bad, needle):
    errs = card.validate(draft(**bad))
    assert errs and any(needle in e for e in errs), errs


def test_unit_list_must_match_units():
    errs = card.validate(draft(units=2, unit_list=[{"label": "a", "claim": "a"}]))
    assert any("unit_list" in e for e in errs)


def test_normalize_sets_code_owned_fields_from_the_probe(ctx):
    p = P(ctx, "please email the late customers a reminder")
    c = card.normalize(draft(), p, ctx, request="please email the late customers a reminder")
    assert c["irreversible"] is True and c["reversibility"] == "R3"
    assert c["schema"] == "harness/card@1" and c["request"].startswith("please email")
    # compile.apply adds the rest
    schema.check({**c, "job_id": "j1", "version": 1, "versions": {}, "multi_session": False, "follows": None,
                  "scenario_of": None}, "card")


def test_the_model_cannot_lower_code_owned_fields(ctx):
    p = P(ctx, "please email sara@shop.example the total")
    c = card.normalize(draft(irreversible=False, computed_numbers=False, data_classes=[]), p, ctx, request="x")
    assert c["irreversible"] is True and "email" in c["data_classes"] and c["computed_numbers"] is True


def test_the_model_may_raise_them(ctx):
    p = P(ctx, "What is VAT?")
    c = card.normalize(draft(irreversible=True, computed_numbers=True, data_classes=["salary"]), p, ctx, request="x")
    assert c["irreversible"] is True and c["computed_numbers"] is True and "salary" in c["data_classes"]


def test_decision_class_is_max_merged_with_the_probe(ctx):
    p = P(ctx, "should we sign this contract with the supplier")
    assert card.normalize(draft(decision_class="none"), p, ctx, request="x")["decision_class"] == "legal"
    p2 = P(ctx, "what dose should I take, and is it a tax issue")
    assert card.normalize(draft(decision_class="financial"), p2, ctx, request="x")["decision_class"] == "medical"


def test_inferred_risk_is_at_least_medium(ctx):
    c = card.normalize(draft(risk={"level": "low"}, provenance={"risk": "inferred"}), P(ctx), ctx, request="x")
    assert c["risk"]["level"] == "medium"
    c = card.normalize(draft(risk={"level": "low"}, provenance={}), P(ctx), ctx, request="x")
    assert c["risk"]["level"] == "medium"  # no provenance: inferred
    c = card.normalize(draft(risk={"level": "low"}, provenance={"risk": "user"}), P(ctx), ctx, request="x")
    assert c["risk"]["level"] == "low"


def test_money_figure_makes_inferred_reliance_money(ctx):
    p = P(ctx, "what is our revenue in toman")
    c = card.normalize(draft(reliance="info", provenance={"reliance": "inferred", "risk": "user"}), p, ctx, request="x")
    assert c["reliance"] == "money"
    c = card.normalize(draft(reliance="info", provenance={"reliance": "user", "risk": "user"}), p, ctx, request="x")
    assert c["reliance"] == "info"  # owner-confirmed


def test_business_thresholds_make_risk_high(ctx):
    (ctx.data_root / "business").mkdir()
    (ctx.data_root / "business" / "profile.json").write_text(json.dumps(
        {"thresholds": {"money_at_stake_high": 1000, "recipient_count_high": 50}}))
    big = draft(cost_of_error={"money_at_stake": {"amount": 5000, "currency": "IRT"}}, provenance={"risk": "user"})
    assert card.normalize(big, P(ctx), ctx, request="x")["risk"]["level"] == "high"
    many = draft(cost_of_error={"money_at_stake": {"amount": 0, "currency": "IRT"}, "recipient_count": 51},
                 provenance={"risk": "user"})
    assert card.normalize(many, P(ctx), ctx, request="x")["risk"]["level"] == "high"


def test_single_unit_gets_one_scope_row(ctx):
    c = card.normalize(draft(), P(ctx), ctx, request="x")
    assert c["unit_list"] == [{"label": "the answer", "claim": "late customers with amounts and a total"}]


def test_personal_data_categories_follow_data_classes(ctx):
    p = P(ctx, "list customers with their email")
    c = card.normalize(draft(), p, ctx, request="x")
    assert c["personal_data"]["categories"] == c["data_classes"]


def test_normalize_refuses_an_invalid_draft(ctx):
    with pytest.raises(card.CardError):
        card.normalize(draft(units=0), P(ctx), ctx, request="x")


# --- merge (shadow second draft) ---


def test_merge_takes_max_risk_and_union_of_criteria():
    a = draft(risk={"level": "low"})
    b = draft(risk={"level": "high"}, oracle={"type": "finance.reconcile_ar", "executable": True,
                                              "criteria": ["every row has an invoice id"]})
    m, questions = card.merge(a, b)
    assert m["risk"]["level"] == "high" and questions == []
    assert m["oracle"]["criteria"] == ["total ties to AR aging", "every row has an invoice id"]


def test_merge_turns_a_deliverable_disagreement_into_one_question():
    a = draft()
    b = draft(deliverable={"kind": "report", "description": "an aging report"})
    m, questions = card.merge(a, b)
    assert len(questions) == 1 and "deliverable" in questions[0]
