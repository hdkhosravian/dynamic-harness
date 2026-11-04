"""Taxonomy vs selector (spec §Tier selection; §Task taxonomy): every fixture card gets its expected tier."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core import tier_select

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "cards").glob("*.json"))
TASK_TYPES = ("quick_question", "data_question", "document", "analysis_report", "investigation", "simulation",
              "decision_support", "one_off_operation", "bulk_operation", "automation_build", "monitoring",
              "planning", "regulated")


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_taxonomy_fixture_gets_its_tier(path):
    f = json.loads(path.read_text())
    assert tier_select.select_tier(f["card"], f["probe"]) == f["v1_expected_tier"], f["note"]


def test_every_task_type_has_a_fixture():
    seen = {json.loads(p.read_text())["card"]["task_type"] for p in FIXTURES}
    assert set(TASK_TYPES) <= seen


def test_tier_task_types_match_the_card_schema():
    from harness_core import schema
    assert set(schema.load("card")["properties"]["task_type"]["enum"]) == set(TASK_TYPES)


def card(**kw):
    c = {"task_type": "data_question", "decision_class": "none", "risk": {"level": "medium"}, "irreversible": False,
         "units": 1, "multi_session": False, "oracle": {"executable": True}, "reliance": "money",
         "computed_numbers": True, "resume_of": None, "scenario_of": None}
    c.update(kw)
    return c


PROBE = {"question_only": False, "write_targets": [], "floor_tier": "T0"}


def test_resume_never_lowers_the_stored_tier():
    c = card(resume_of="j-old", risk={"level": "low"}, reliance="info", computed_numbers=False)
    assert tier_select.select_tier(c, PROBE, stored_tier=lambda job: "T2") == "T2"


def test_scenario_run_takes_the_stored_tier_or_higher():
    assert tier_select.select_tier(card(scenario_of="m1"), PROBE, stored_tier=lambda j: "T2") == "T2"
    assert tier_select.select_tier(card(scenario_of="m1"), PROBE, stored_tier=lambda j: "T1") == "T1"
    assert tier_select.select_tier(card(scenario_of="m1", risk={"level": "high"}), PROBE,
                                   stored_tier=lambda j: "T1") == "T2"


def test_unknown_stored_tier_fails_towards_t2():
    # a resume or scenario whose parent tier cannot be read must not come out lower
    assert tier_select.select_tier(card(resume_of="j-gone"), PROBE, stored_tier=lambda j: None) == "T2"


def test_refuse_wins_over_every_other_rule():
    c = card(decision_class="safety", irreversible=True, resume_of="j1")
    assert tier_select.select_tier(c, dict(PROBE, floor_tier="T2"), stored_tier=lambda j: "T2") == "REFUSE"


def test_unknown_decision_class_is_treated_as_signoff():
    assert tier_select.policy("astrology") == "signoff"


def test_t1_max_units_comes_from_control():
    c = card(units=4)
    assert tier_select.select_tier(c, PROBE, control={"t1_max_units": 3}) == "T2"
    assert tier_select.select_tier(c, PROBE, control={"t1_max_units": 5}) == "T1"


@pytest.mark.parametrize("bad", [{"units": "3"}, {"risk": "high"}, {"oracle": None}, {"irreversible": "yes"}])
def test_malformed_fields_fail_towards_t2(bad):
    assert tier_select.select_tier(card(**bad), PROBE) == "T2"


def test_tier_max_orders_tiers():
    assert tier_select.tier_max("T0", "T2", "T1") == "T2" and tier_select.tier_max("T0") == "T0"
