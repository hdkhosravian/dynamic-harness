"""The gaps closed after the P5 review: Persian check notes, the rows workbook, the forecast workbook, Monte Carlo,
the research saturation stop, and the XLSX writer itself."""
from __future__ import annotations

import json
import zipfile

import pytest

from harness_core import answer, contact, lanes, model, model_cmd, tables, xlsx
from harness_core.errors import Denied
from test_budget import log_of
from test_claims import PLAN, inv  # noqa: F401  (fixture inv)
from test_model import A, MODEL, sim  # noqa: F401  (fixture sim)
from test_p3_hooks import SID, compiled, ctx_of, h, say  # noqa: F401  (fixture h)
from test_run import SALES, run_req

METRIC = {"metric_id": "revenue", "inclusions": "sales invoices", "exclusions": "VAT", "unit": "amount",
          "currency": "IRT"}


def test_the_xlsx_writer_round_trips_through_the_reader(tmp_path):
    f = tmp_path / "t.xlsx"
    f.write_bytes(xlsx.to_bytes([{"name": "S", "rows": [["a", "b"], [1, {"f": "A2*2", "v": 2}], ["x&<y>", True]]}]))
    headers, rows = tables.read(f)
    assert headers == ["a", "b"] and rows == [[1, 2], ["x&<y>", True]]
    assert xlsx.to_bytes([{"name": "S", "rows": [["a"]]}]) == xlsx.to_bytes([{"name": "S", "rows": [["a"]]}])
    with zipfile.ZipFile(f) as z:
        assert z.namelist()[0] == "[Content_Types].xml"


def _defined(h):
    contact.apply({"tool": "contact", "type": "definition_confirm", "metric": METRIC}, ctx_of(h), {"session_id": SID})
    say(h, "yes", "d-yes")
    contact.apply({"tool": "contact", "type": "definition_confirm", "accept": "latest"}, ctx_of(h), {"session_id": SID})


def test_a_persian_owner_reads_the_check_note_in_persian(h):
    from test_business import confirm, typed
    confirm(h, "business", typed(h, "/dynamic-harness:business set language fa", "l1"), "l2")
    (h["ws"] / "sales.csv").write_text(SALES + ",no date,700\n")
    compiled(h)
    _defined(h)
    res = run_req(h, metric_id="revenue")
    line = answer.apply({"tool": "answer", "sub": "header", "unit": "u1", "output": res["sha256"]}, ctx_of(h),
                        {"session_id": SID})["header"].splitlines()[2]
    assert line == "انجام شد، ولی کامل بررسی نشد: کنار گذاشته شد: ۱ ردیف بدون تاریخ."


def test_the_rows_behind_the_figure_are_attached_as_a_workbook(h):
    (h["ws"] / "sales.csv").write_text(SALES)
    job = compiled(h)
    _defined(h)
    res = run_req(h, metric_id="revenue")
    out = answer.apply({"tool": "answer", "sub": "header", "unit": "u1", "output": res["sha256"]}, ctx_of(h),
                       {"session_id": SID})
    p, _ = log_of(h, job)
    headers, rows = tables.read(p.work / out["rows_file"])
    assert headers == ["date", "description", "amount (Toman)"] and len(rows) == 3


def test_a_large_attachment_never_hides_the_result_line(h):
    big = "date,amount (Toman)\n" + "".join(f"1402/12/{1 + i % 28:02d},{i}\n" for i in range(120000))
    (h["ws"] / "sales.csv").write_text(big)
    compiled(h)
    res = run_req(h)
    assert res["result"] in ("pass", "fail") and res["values"]["rows_used"] == 120000


def test_the_forecast_workbook_holds_formulas_and_passes_its_lint(h, sim):
    out = model_cmd.apply({"tool": "model", "sub": "workbook"}, ctx_of(h), {"session_id": SID})
    p, _ = log_of(h, sim)
    data = (p.work / out["workbook"]).read_bytes()
    calc = xlsx.sheet_xml(data, 2).decode()
    assert "<f>Assumptions!$C$" in calc and "sheetProtection" in calc
    headers, rows = tables.read(p.work / out["workbook"])  # the first sheet is the editable Assumptions sheet
    assert headers == ["id", "label", "value", "unit", "period", "source"] and rows[0][0] == "opening_cash"
    res = run_req(h, check="model.workbook", inputs=(), period=None)
    assert res["result"] == "pass", res["notes"]


def test_a_workbook_with_a_typed_over_formula_fails_the_lint(h, sim):
    model_cmd.apply({"tool": "model", "sub": "workbook"}, ctx_of(h), {"session_id": SID})
    p, _ = log_of(h, sim)
    sheets = model.workbook_sheets(MODEL, A())
    sheets[1]["rows"][1][2] = 123456  # someone typed a number over a formula
    (p.work / "forecast.xlsx").write_bytes(xlsx.to_bytes(sheets))
    res = run_req(h, check="model.workbook", inputs=(), period=None)
    assert res["result"] == "fail" and any("Calc sheet (formulas) differs" in n for n in res["notes"])


def test_a_crafted_formula_that_is_a_constant_fails_the_lint(h, sim):
    model_cmd.apply({"tool": "model", "sub": "workbook"}, ctx_of(h), {"session_id": SID})
    p, _ = log_of(h, sim)
    sheets = model.workbook_sheets(MODEL, A())
    sheets[1]["rows"][1][2] = {"f": "999999", "v": 999999}  # looks like a formula, computes nothing
    sheets[2]["rows"][1][1] = {"f": "TRUE", "v": True, "style": "check"}  # a check that checks nothing
    (p.work / "forecast.xlsx").write_bytes(xlsx.to_bytes(sheets))
    res = run_req(h, check="model.workbook", inputs=(), period=None)
    assert res["result"] == "fail" and len(res["notes"]) == 2


def test_monte_carlo_is_seeded_justified_and_reproducible(h, sim):
    p, _ = log_of(h, sim)
    m = dict(MODEL, monte_carlo={"runs": 200, "seed": 7, "vary": {
        "sales_growth": {"dist": "normal", "sd": 0.02, "why": "last year's monthly growth varied by about 2 points"},
        "cogs_rate": {"dist": "triangular", "low": 0.55, "high": 0.7, "why": "supplier quotes range 55-70%"}}})
    (p.work / "model.json").write_text(json.dumps(m))
    a, b = run_req(h, check="model.recalc", inputs=(), period=None), \
        run_req(h, check="model.recalc", inputs=(), period=None, attempt=2)
    mc = a["values"]["monte_carlo"]
    assert a["result"] == "pass" and mc == b["values"]["monte_carlo"]
    assert mc["p10"] <= mc["p50"] <= mc["p90"] and 0 <= mc["chance_out_of_cash_pct"] <= 100
    assert any("treated as independent" in n for n in a["notes"])
    m["monte_carlo"]["vary"]["cogs_rate"].pop("why")
    (p.work / "model.json").write_text(json.dumps(m))
    res = run_req(h, check="model.recalc", inputs=(), period=None, attempt=3)
    assert res["result"] == "fail" and any("needs a why" in n for n in res["notes"])


def test_research_stops_at_saturation(h, inv):
    from harness_core import store
    p, log = log_of(h, inv)
    for i in range(7):
        urls = ["https://a.example.com/p"] if i else ["https://a.example.com/p", "https://b.example.org/q"]
        store.append_jsonl(p.run, log, "search_log.jsonl", {"ts": f"2026-11-05T00:00:0{i}Z", "query": f"q{i}",
                                                            "urls": urls}, "SEARCH", {})
    c = ctx_of(h)
    out = lanes.on_pre({"session_id": SID, "tool_name": "WebSearch", "tool_input": {"query": "more"},
                        "agent_type": "dynamic-harness:web-researcher"}, c)
    assert out.get("decision") == "deny" and "saturation" in out["reason"]
    assert PLAN


def test_import_reads_the_returned_workbook(h, sim):
    p, _ = log_of(h, sim)
    sheets = model.workbook_sheets(MODEL, A())
    for row in sheets[0]["rows"][1:]:
        if row[0] == "sales_growth":
            row[2] = {"v": 0.15, "style": "input"}
    (h["ws"] / "returned.xlsx").write_bytes(xlsx.to_bytes(sheets))
    rows = model_cmd._sheet_rows((h["ws"] / "returned.xlsx").read_bytes())
    assert {r["id"]: r["value"] for r in rows}["sales_growth"] == "0.15"
    with pytest.raises(Denied, match="finished model"):  # the parent is not DONE in this test
        model_cmd.apply({"tool": "model", "sub": "import", "file": "returned.xlsx"}, ctx_of(h), {"session_id": SID})
