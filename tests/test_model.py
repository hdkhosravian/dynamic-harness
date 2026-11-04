"""The model-checks pack and harness-model (R-14; spec §Business simulations; build phase P5)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from harness_core import contact, gatehook, model, model_cmd, sessionstate, stopgate, store
from harness_core.errors import Denied
from harness_core.ledger import Ledger
from test_budget import log_of
from test_integration_gate import FOLD, fold_ledger_problem
from test_p3_hooks import CARD, SID, compiled, ctx_of, h, say  # noqa: F401  (fixture h)
from test_run import run_req

SIM = dict(CARD, task_type="simulation", deliverable={"kind": "model", "description": "cash forecast"},
           oracle={"type": "model.recalc", "executable": True, "criteria": ["every number is a model cell"]})
MODEL = {"unit": "IRT", "start": "1405/09", "months": 6, "opening_balance": {"assumption": "opening_cash"},
         "lines": [{"id": "sales", "kind": "inflow", "base": {"assumption": "sales_base"},
                    "growth": {"assumption": "sales_growth"}},
                   {"id": "cogs", "kind": "outflow", "share_of": "sales", "rate": {"assumption": "cogs_rate"}},
                   {"id": "rent", "kind": "outflow", "base": {"assumption": "rent"}}],
         "scenarios": {"low": {"sales_growth": -0.05}, "high": {"sales_growth": 0.05}}, "flip": {"driver": "sales"}}
ASSUME = [("opening_cash", 1000000, "IRT", "once"), ("sales_base", 500000, "IRT", "month"),
          ("sales_growth", 0.02, "ratio", "month"), ("cogs_rate", 0.6, "ratio", "month"), ("rent", 200000, "IRT", "month")]
BANK = ("date,description,amount,balance\n1405/06/05,opening,0,900000\n1405/06/20,sale,60000,960000\n"
        "1405/07/10,sale,40000,1000000\n1405/08/12,rent,-150000,850000\n1405/08/20,sale,150000,1000000\n")
HISTORY = "month,sales\n" + "".join(f"m{i},{v}\n" for i, v in enumerate(
    [440000, 450000, 455000, 465000, 470000, 480000, 485000, 490000, 500000]))


def A(**over):
    rows = {k: {"id": k, "value": v, "unit": u, "period": p, "owner_confirmed": True} for k, v, u, p in ASSUME}
    for k, v in over.items():
        rows[k] = dict(rows[k], value=v)
    return rows


def test_the_engine_closes_every_period():
    r = model.run(MODEL, A())
    assert r["cells"]["sales@1405/09"] == 500000 and r["cells"]["cogs@1405/09"] == 300000
    assert r["closing"][0] == 1000000 + 500000 - 300000 - 200000
    assert r["periods"] == ["1405/09", "1405/10", "1405/11", "1405/12", "1406/01", "1406/02"]


def test_the_flip_point_and_drivers():
    f = model.flip(MODEL, A())
    assert f["driver"] == "sales" and Decimal(0) < f["fall_pct"] < Decimal(100)
    r = model.run(MODEL, A(), scale={"sales": 1 - float(f["fall_pct"]) / 100 - 0.01})
    assert any(c < 0 for c in r["closing"])
    assert model.tornado(MODEL, A())[0] in ("sales_base", "cogs_rate")


@pytest.fixture
def sim(h):
    job = compiled(h, card=SIM, text="forecast our cash for the next 6 months")
    for k, v, u, p in ASSUME:
        res = model_cmd.apply({"tool": "model", "sub": "assume", "id": k, "value": v, "unit": u, "period": p,
                               "source": "history"}, ctx_of(h), {"session_id": SID})
        assert res["owner_confirmed"] is False
    out = contact.apply({"tool": "contact", "type": "assumption_sheet"}, ctx_of(h), {"session_id": SID})
    assert "Sales base: 500000 IRT" in out["text"].replace("sales_base", "Sales base") or "500000" in out["text"]
    say(h, "OK", "sheet-ok")
    contact.apply({"tool": "contact", "type": "assumption_sheet", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    p, log = log_of(h, job)
    (p.work / "model.json").write_text(json.dumps(MODEL))
    model.write_table(p.work / "model_table.csv", model.run(MODEL, A()))
    (h["ws"] / "bank.csv").write_text(BANK)
    (h["ws"] / "sales_history.csv").write_text(HISTORY)
    return job


def test_the_sheet_confirms_every_assumption(h, sim):
    p, log = log_of(h, sim)
    rows = store.read_json(p.run, log, "assumptions.json")["assumptions"]
    assert all(r["owner_confirmed"] for r in rows)


def test_an_owner_stated_value_is_confirmed_by_their_words(h, sim):
    say(h, "for the cash forecast use 3% growth instead", "p9")
    res = model_cmd.apply({"tool": "model", "sub": "assume", "id": "sales_growth", "value": 0.03, "unit": "ratio",
                           "period": "month", "source": "owner"}, ctx_of(h), {"session_id": SID})
    assert res["owner_confirmed"] is True
    res = model_cmd.apply({"tool": "model", "sub": "assume", "id": "sales_growth", "value": 0.04, "unit": "ratio",
                           "period": "month", "source": "owner"}, ctx_of(h), {"session_id": SID})
    assert res["owner_confirmed"] is False  # the owner never said 4%


def recalc(h):
    return run_req(h, check="model.recalc", inputs=(), period=None)


def checks(h, **kw):
    return run_req(h, check="model.checks", inputs=("bank.csv", "sales_history.csv"), period=None,
                   **dict({"bank": 1, "history": 2, "table": "model_table.csv"}, **kw))


def test_recalc_records_every_cell_and_the_ranges(h, sim):
    res = recalc(h)
    v = res["values"]
    assert res["result"] == "pass" and v["cells"]["sales@1405/09"] == 500000 and len(v["cells"]) == 6 * 6
    assert set(v["final_closing"]) == {"base", "low", "high"} and v["flip_driver"] == "sales"


def test_checks_pass_on_a_sound_model(h, sim):
    res = checks(h)
    assert res["result"] == "pass", res["notes"]


def test_a_delivered_table_that_differs_fails(h, sim):
    p, log = log_of(h, sim)
    model.write_table(p.work / "model_table.csv", model.run(MODEL, A(sales_base=600000)))
    res = checks(h)
    assert res["result"] == "fail" and any("delivered table differs" in n for n in res["notes"])


def test_a_bank_export_with_a_missing_row_fails_the_back_cast(h, sim):
    (h["ws"] / "bank.csv").write_text(BANK.replace("1405/07/10,sale,40000,1000000\n", ""))
    res = checks(h)
    assert res["result"] == "fail" and any("back-cast" in n for n in res["notes"])


def test_an_overfit_forecast_loses_to_the_naive_baseline(h, sim):
    p, log = log_of(h, sim)
    model_cmd.apply({"tool": "model", "sub": "assume", "id": "sales_growth", "value": 0.3, "unit": "ratio",
                     "period": "month", "source": "guess"}, ctx_of(h), {"session_id": SID})
    model.write_table(p.work / "model_table.csv", model.run(MODEL, A(sales_growth=0.3)))
    res = checks(h)
    assert res["result"] == "fail" and any("does worse than the baseline" in n for n in res["notes"])


def test_extrapolation_beyond_the_history_is_flagged(h, sim):
    p, log = log_of(h, sim)
    m = dict(MODEL, months=24)
    (p.work / "model.json").write_text(json.dumps(m))
    model.write_table(p.work / "model_table.csv", model.run(m, A()))
    res = checks(h)
    assert res["result"] == "fail" and any("extrapolation" in n for n in res["notes"])


def test_mixed_units_fail_the_tags(h, sim):
    model_cmd.apply({"tool": "model", "sub": "assume", "id": "rent", "value": 2000000, "unit": "IRR",
                     "period": "month", "source": "history"}, ctx_of(h), {"session_id": SID})
    res = checks(h)
    assert res["result"] == "fail" and any("units are mixed" in n for n in res["notes"])


def _report(h, job, text):
    p, log = log_of(h, job)
    (p.work / "report.md").write_text(text)


def test_the_report_needs_ranges_drivers_flip_and_both_sentences(h, sim):
    _report(h, sim, "Cash stays positive.\n")
    res = run_req(h, check="model.report", inputs=(), period=None)
    assert res["result"] == "fail" and len(res["notes"]) >= 3


def _good_report(h, job):
    v = recalc(h)["values"]
    f = v["final_closing"]
    text = (f"Cash at the end of 1406/02: low {f['low']:,}, base {f['base']:,}, high {f['high']:,} Toman.\n"
            f"Top drivers: {', '.join(v['top_drivers'])}.\n"
            f"You run out of cash in {v['flip_month']} if sales fall more than {v['flip_fall_pct']}%.\n"
            f"{model.SCENARIO_SENTENCE}\n{model.LIMITS_SENTENCE}\n")
    _report(h, job, text)
    return text


def test_a_complete_report_passes(h, sim):
    _good_report(h, sim)
    res = run_req(h, check="model.report", inputs=(), period=None)
    assert res["result"] == "pass", res["notes"]


def test_the_limits_sentence_is_a_fixed_line_the_lint_allows(h):
    assert stopgate.lexicon_hits(ctx_of(h), model.LIMITS_SENTENCE) == []
    assert stopgate.lexicon_hits(ctx_of(h), "Arithmetic checked and everything verified.") != []


def _gate(h, job):
    res = recalc(h)
    p, log = log_of(h, job)
    Ledger(p, log).append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS", "outputs": [res["sha256"]]})
    c = ctx_of(h)
    c.loop_contract_root = Path(FOLD).parents[3]
    return gatehook.run({"session_id": SID}, c)


@pytest.mark.skipif(fold_ledger_problem() is not None, reason=str(fold_ledger_problem()))
def test_r14_every_number_in_the_report_traces_to_a_model_cell(h, sim):
    _good_report(h, sim)
    out = _gate(h, sim)
    assert out["mode"] == "done" and out["state"] == "Checked", out
    _report(h, sim, _good_report(h, sim) + "Next year we will double to 9,876,543 Toman.\n")
    out = _gate(h, sim)
    assert out["state"] == "Done, not fully checked: numbers_untraced"


@pytest.mark.skipif(fold_ledger_problem() is not None, reason=str(fold_ledger_problem()))
def test_register_then_a_scenario_run_and_an_import(h, sim):
    _good_report(h, sim)
    assert _gate(h, sim)["state"] == "Checked"
    reg = model_cmd.apply({"tool": "model", "sub": "register", "deliverable": "model.json"}, ctx_of(h),
                          {"session_id": SID})
    assert reg["registered"] == sim and model_cmd.registry(ctx_of(h))[sim]["version"] == 1
    v = stopgate.on_stop({"session_id": SID, "last_assistant_message": "Checked"}, ctx_of(h))
    assert sessionstate.jobs(ctx_of(h))[sim]["state"] == "DONE", v
    say(h, "now try 15% growth", "p-scen")
    (h["ws"] / "sheet.csv").write_text("id,value\nnew_branch,1\n")
    res = model_cmd.apply({"tool": "model", "sub": "import", "file": "sheet.csv"}, ctx_of(h), {"session_id": SID})
    assert res["structural"] is True  # an unknown row is a structural change: a new task with follows
    (h["ws"] / "sheet.csv").write_text("id,value\nsales_growth,0.02\n")
    assert model_cmd.apply({"tool": "model", "sub": "import", "file": "sheet.csv"}, ctx_of(h),
                           {"session_id": SID})["unchanged"] is True
    (h["ws"] / "sheet.csv").write_text("id,value\nsales_growth,0.15\n")
    out = model_cmd.apply({"tool": "model", "sub": "import", "file": "sheet.csv"}, ctx_of(h), {"session_id": SID})
    child = out["job_id"]
    assert child == f"{sim}.s1" and out["scenario_of"] == sim
    cp, clog = log_of(h, child)
    rows = {r["id"]: r for r in store.read_json(cp.run, clog, "assumptions.json")["assumptions"]}
    assert rows["sales_growth"]["value"] == 0.15 and rows["sales_growth"]["owner_confirmed"] is True
    assert json.loads((cp.work / "model.json").read_text()) == MODEL
    from harness_core.record import _cards
    assert _cards(cp, clog)[-1]["scenario_of"] == sim


def test_a_scenario_needs_a_finished_registered_model(h, sim):
    with pytest.raises(Denied, match="finished model"):
        model_cmd.apply({"tool": "model", "sub": "scenario", "set": {"sales_growth": 0.15}}, ctx_of(h),
                        {"session_id": SID})
