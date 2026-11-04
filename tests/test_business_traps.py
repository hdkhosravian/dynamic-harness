"""Planted business traps (spec §Evaluation plan stage 4: "the output must report the trap"; stage 4b; P5).

Each test plants one trap class from the spec's list and checks that the harness either handles it correctly or
reports it where the owner sees it (a check note, the answer header, or the gate state). Traps already covered
end to end elsewhere are referenced: model traps in test_model.py, claim traps in test_claims.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core import gatehook, probe, stopgate, tier_select
from harness_core.ledger import Ledger
from test_budget import log_of
from test_integration_gate import FOLD, fold_ledger_problem
from test_p3_hooks import CARD, SID, compiled, ctx_of, h, say  # noqa: F401  (fixture h)
from test_run import run_req


@pytest.fixture
def job(h):
    return compiled(h)


def sales(h, text, name="sales.csv"):
    (h["ws"] / name).write_text(text, encoding="utf-8")


def notes(res):
    return " | ".join(res["notes"])


# --- R-11 -------------------------------------------------------------------------------------------------------
def test_r11_money_never_t0(h):
    """A figure someone relies on, or computed numbers, is never a quick answer; a quick answer that names money
    is stopped once and needs a card."""
    p = probe.run("what were our sales last month in toman?", ctx_of(h))
    assert probe.prefilter(p) == "card-needed"
    for card in (dict(CARD, reliance="money"), dict(CARD, computed_numbers=True)):
        assert tier_select.select_tier(dict(card, risk={"level": "low"}), dict(p, floor_tier="T0"),
                                       ctx=ctx_of(h)) != "T0"
    say(h, "What is the capital of France?", "q1")
    out = stopgate.on_stop({"session_id": SID, "last_assistant_message": "It costs about 1,500,000 Toman."},
                           ctx_of(h))
    assert out["decision"] == "block" and "task card" in out["reason"]
    from harness_core import sessionstate
    assert sessionstate.load(ctx_of(h), SID)["pending_card"]["floor"] == "T1"


# --- own data (finance.reconcile) --------------------------------------------------------------------------------
def test_toman_rial_x10_is_reported(h, job):
    sales(h, "date,amount (Toman)\n" + "".join(f"1402/11/{d:02d},1000\n" for d in (1, 2, 3)) +
          "".join(f"1402/12/{d:02d},10000\n" for d in (1, 2, 3)))  # Esfand typed in Rial under a Toman header
    res = run_req(h)
    assert res["result"] == "fail" and "10.0 times the prior period's" in notes(res) and "Toman/Rial" in notes(res)


def test_leap_esfand_and_the_fiscal_boundary(h, job):
    sales(h, "date,amount (Toman)\n1403/12/01,100\n1403/12/30,200\n1404/01/01,999\n1403/11/30,555\n")
    res = run_req(h, period={"month": "1403-12"})
    assert res["values"]["total"] == 300 and res["values"]["rows_used"] == 2  # Esfand 30 counts, Farvardin 1 not


def test_an_invalid_esfand_30_is_left_out_and_reported(h, job):
    sales(h, "date,amount (Toman)\n1402/12/01,100\n1402/12/30,200\n1402/12/29,50\n")
    res = run_req(h)
    assert res["values"]["total"] == 150 and "left out: 1 rows with no date" in notes(res)


def test_name_variants_with_arabic_letters_are_one_customer(h, job):
    sales(h, "date,customer,amount (Toman)\n1402/12/01,علی کریمی,100\n1402/12/02,علي كريمي,200\n"
             "1402/12/29,سارا,999\n")
    res = run_req(h, filters={"customer": "علی کریمی"})
    assert res["values"]["total"] == 300


def test_persian_digit_dates_and_amounts(h, job):
    sales(h, "تاریخ,مبلغ (تومان)\n۱۴۰۲/۱۲/۰۱,۱٬۰۰۰\n۱۴۰۲/۱۲/۲۹,۲٬۵۰۰\n")
    res = run_req(h)
    assert res["result"] == "pass" and res["values"]["total"] == 3500


def test_missing_dates_are_counted_not_dropped_silently(h, job):
    sales(h, "date,amount (Toman)\n1402/12/01,100\n,300\n1402/12/29,100\n")
    res = run_req(h)
    assert res["result"] == "fail" and "left out: 1 rows with no date" in notes(res)


def test_disagreeing_sources_show_both_and_the_gap(h, job):
    sales(h, "date,amount (Toman)\n1402/12/01,1240\n1402/12/29,0\n")
    sales(h, "date,amount (Toman)\n1402/12/03,1180\n1402/12/29,0\n", "bank.csv")
    res = run_req(h, inputs=("sales.csv", "bank.csv"), control={"input": 2, "label": "bank deposits"})
    assert res["result"] == "fail" and "this file says 1240, bank deposits says 1180; the gap of 60" in notes(res)


def test_a_stale_export_says_incomplete(h, job):
    sales(h, "date,amount (Toman)\n1402/12/01,100\n1402/12/15,100\n")
    res = run_req(h)
    assert res["result"] == "fail" and "incomplete: the data ends 1402/12/15" in notes(res)


def test_the_wrong_file_has_no_rows_in_the_period(h, job):
    sales(h, "date,amount (Toman)\n1401/12/01,100\n1401/12/29,100\n")  # last year's export
    res = run_req(h)
    assert res["result"] == "fail" and "no rows fall in the period" in notes(res)


def test_a_transfer_between_own_accounts_is_not_revenue(h, job):
    sales(h, "date,description,account,amount (Toman)\n1402/12/01,invoice 7,,1000\n"
             "1402/12/10,deposit,0101-22,5000\n1402/12/29,invoice 8,,500\n")
    res = run_req(h, own_accounts=["0101-22"])
    assert res["values"]["total"] == 1500 and res["values"]["transfers_excluded"] == 1
    assert "1 transfers between your own accounts were excluded" in notes(res)


def test_same_snapshot_same_question_same_figure(h, job):
    sales(h, "date,amount (Toman)\n1402/12/01,1000\n1402/12/29,2500\n")
    a, b = run_req(h), run_req(h, attempt=2)
    assert a["values"] == b["values"]


@pytest.mark.skipif(fold_ledger_problem() is not None, reason=str(fold_ledger_problem()))
def test_a_figure_the_model_added_up_itself_is_reported(h, job):
    """Calculation trap: the report states a total no check produced; the gate caps the state and lists it."""
    sales(h, "date,amount (Toman)\n1402/12/01,1000\n1402/12/29,2500\n")
    res = run_req(h)
    p, log = log_of(h, job)
    Ledger(p, log).append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS", "outputs": [res["sha256"]]})
    (p.work / "report.md").write_text("Esfand 1402 sales: 3,600 Toman.\n")
    c = ctx_of(h)
    c.loop_contract_root = Path(FOLD).parents[3]
    out = gatehook.run({"session_id": SID}, c)
    assert out["state"] == "Done, not fully checked: numbers_untraced"
    assert "3600 (" in (p.run / "gate" / "REPORT.md").read_text()  # listed under "Numbers not traced"


# --- research and model traps: covered end to end in their own files ---------------------------------------------
@pytest.mark.parametrize("trap, test", [
    ("fabricated citation", "test_p3_hooks.py::test_researcher_claims_are_checked_against_fetched_snapshots"),
    ("real citation that does not support the claim", "test_claims.py::test_an_unverified_unit_fails"),
    ("stale source", "test_claims.py::test_a_stale_price_claim_fails"),
    ("prompt-injected page", "test_claims.py::test_pages_addressed_to_the_assistant_are_flagged"),
    ("syndicated copy counted as independent", "test_claims.py::test_a_syndicated_copy_is_not_a_second_source"),
    ("formula", "test_model.py::test_a_delivered_table_that_differs_fails"),
    ("overfit forecast", "test_model.py::test_an_overfit_forecast_loses_to_the_naive_baseline"),
    ("extrapolation beyond the history", "test_model.py::test_extrapolation_beyond_the_history_is_flagged"),
])
def test_every_trap_class_has_a_test(trap, test):
    f, name = test.split("::")
    assert f"def {name}(" in (Path(__file__).parent / f).read_text(), trap


def test_contradicting_sources_are_reported(h):
    from test_claims import _cite_and_verify, _research, inv as _inv  # noqa: F401
    from harness_core import ledger as led
    card = dict(CARD, task_type="investigation", deliverable={"kind": "research_report", "description": "steel"},
                oracle={"type": "research.check", "executable": True, "criteria": ["x"]})
    from harness_core import contact
    from test_claims import PLAN
    job = compiled(h, card=card, text="did steel prices rise this month?")
    contact.apply({"tool": "contact", "type": "plan", "plan": PLAN}, ctx_of(h), {"session_id": SID})
    say(h, "yes", "r1")
    contact.apply({"tool": "contact", "type": "plan", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    _research(h, job)
    _cite_and_verify(h)
    p, log = log_of(h, job)
    led.Ledger(p, log).append_row("contradiction", {"unit": "u1", "a": "claim 1", "b": "a ministry page says 3%"})
    res = run_req(h, check="research.check", inputs=(), period=None)
    assert "1 contradiction(s) are recorded for u1; the report must show them" in notes(res)
    assert json.dumps(res["values"]).count("contradictions") == 1
