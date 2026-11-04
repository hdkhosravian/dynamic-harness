"""Regression tests for the independent P5 review (one per finding)."""
from __future__ import annotations

import json
import os
import re
import zipfile

import pytest

from harness_core import business, contact, gate, hookmain, stopgate, tables
from harness_core.errors import Denied
from harness_core.gatehook import load_key
from harness_core.ledger import Ledger
from test_budget import log_of
from test_business import confirm, typed
from test_claims import PAGES, PLAN, TODAY, _cite_and_verify, _spawn_and_stop, fetch, inv, opener  # noqa: F401
from test_operations import DOC
from test_p3_hooks import CARD, SID, compiled, ctx_of, h, post, say  # noqa: F401  (fixture h)
from test_run import SALES, run_req


# 1 (high): agent-given file names never leave the check's box
def test_a_check_cannot_read_outside_its_box(h, tmp_path):
    compiled(h, card=DOC, text="draft the proposal for the client")
    secret = tmp_path / "secret.txt"
    secret.write_text("token=abc")
    for name in (str(secret), "../../../../" + str(secret).lstrip("/")):
        res = run_req(h, check="doc.check", inputs=(), period=None, document=name, required=["token=abc"])
        assert res["result"] == "error", res


# 2 (high): spaced Persian state words are caught too
@pytest.mark.parametrize("text", ["گزارش بررسی شده است", "این عدد تایید شده است", "تأیید شده"])
def test_spaced_persian_state_words_are_caught(text):
    assert stopgate.lexicon_hits(None, text) != []


# 3 (high): a check must measure something
def test_the_control_cannot_be_the_figures_own_file(h):
    (h["ws"] / "sales.csv").write_text(SALES)
    (h["ws"] / "copy.csv").write_text(SALES)
    compiled(h)
    res = run_req(h, inputs=("sales.csv", "copy.csv"), control={"input": 2})
    assert res["result"] == "error" and any("same file" in n for n in res["notes"])


def test_a_document_check_needs_required_items(h):
    job = compiled(h, card=DOC, text="draft the proposal for the client")
    p, _ = log_of(h, job)
    (p.work / "document.md").write_text("Hello.\n")
    res = run_req(h, check="doc.check", inputs=(), period=None)
    assert res["result"] == "fail" and any("must include" in n for n in res["notes"])


def test_an_input_the_assistant_wrote_is_refused(h):
    compiled(h)
    f = h["ws"] / "made_up.csv"
    f.write_text(SALES)
    post(h, "Write", file_path=str(f), content=SALES)  # recorded as written this session
    with pytest.raises(Denied, match="written in this session"):
        run_req(h, inputs=("made_up.csv",))


def test_a_fifo_input_is_refused(h):
    compiled(h)
    os.mkfifo(h["ws"] / "pipe.csv")
    with pytest.raises(Denied, match="not a regular file"):
        run_req(h, inputs=("pipe.csv",))


def test_research_needs_load_bearing_claims_and_fixed_freshness(h, inv):
    from test_claims import _research
    _research(h, inv, date="2000-01-01")
    _cite_and_verify(h, bearing="")
    res = run_req(h, check="research.check", inputs=(), period=None, freshness={"price": "100000d"})
    joined = " | ".join(res["notes"])
    assert res["result"] == "fail" and "no load-bearing claim" in joined and "must be load-bearing" in joined
    assert "days old" in joined  # args.freshness is ignored


# 5 (medium): the owner approves every field that is stored
def test_the_definition_sentence_shows_every_stored_field(h):
    compiled(h)
    m = {"metric_id": "revenue", "inclusions": "sales invoices", "exclusions": "VAT", "unit": "amount",
         "currency": "IRT", "date_anchor": "invoice date", "source_precedence": ["bank", "sales"]}
    out = contact.apply({"tool": "contact", "type": "definition_confirm", "metric": m}, ctx_of(h), {"session_id": SID})
    assert '"invoice date"' in out["text"] and '["bank", "sales"]' in out["text"]


# 7 (medium): a recorded business change after the gate makes the receipt stale, not tampered with
def test_a_business_change_after_the_gate_is_stale_not_tamper(h, monkeypatch):
    from test_gate import FakeRunner
    from harness_core import sandbox_exec, gatehook
    job = compiled(h)
    monkeypatch.setattr(sandbox_exec, "run", FakeRunner())
    gatehook.run({"session_id": SID}, ctx_of(h))
    p, log = log_of(h, job)
    assert gate.verify_receipt(p, log, load_key(h["data"]))["ok"]
    confirm(h, "business", typed(h, "/dynamic-harness:business set currency IRT", "b1"), "b2")
    v = gate.verify_receipt(p, log, load_key(h["data"]))
    assert not v["ok"] and v["reason"].startswith("receipt is stale")
    out = stopgate.on_stop({"session_id": SID, "last_assistant_message": "done"}, ctx_of(h))
    assert out.get("decision") == "block"  # not done, but not STOPPED_TAMPER either
    from harness_core import sessionstate
    assert sessionstate.jobs(ctx_of(h))[job]["state"] != "STOPPED_TAMPER"
    (h["data"] / "business" / "profile.json").write_text('{"currency": "USD"}')  # an edit on disk
    assert gate.verify_receipt(p, log, load_key(h["data"]))["reason"].startswith("receipt HMAC")


# 8 (medium): quotes must carry the claim
def test_short_quotes_and_missing_numbers_are_dropped(h, inv):
    res = fetch(h, PLAN["urls"][0])
    rows = [{"claim": "steel rose", "quote": "Steel", "date": TODAY, "url": PLAN["urls"][0],
             "snapshot_sha": res["snapshot_sha"]},
            {"claim": "steel rose 40%", "quote": "Steel prices rose 12% in October", "date": TODAY,
             "url": PLAN["urls"][0], "snapshot_sha": res["snapshot_sha"]}]
    _spawn_and_stop(h, "dynamic-harness:web-researcher", "harness-research render",
                    {"tool": "research", "sub": "render"}, "agent-res-x", json.dumps(rows))
    p, log = log_of(h, inv)
    assert not (p.run / "claims.jsonl").exists() or (p.run / "claims.jsonl").read_text() == ""


def test_a_verifier_no_fails_the_unit(h, inv):
    from test_claims import _research
    from harness_core import writers
    _research(h, inv)
    writers.on_post({"tool_name": "Bash", "session_id": SID, "tool_input": {"command": "harness-claims cite"},
                     "tool_response": {"stdout": json.dumps({"tool": "claims", "sub": "cite", "unit": "u1",
                                                              "claims": [1, 2], "load_bearing": [1, 2]})}}, ctx_of(h))
    _spawn_and_stop(h, "dynamic-harness:verifier", "harness-critic render u1",
                    {"tool": "critic", "sub": "render", "unit": "u1"}, "agent-ver-2", "1. no, the quote is about iron")
    res = run_req(h, check="research.check", inputs=(), period=None)
    assert res["result"] == "fail" and any("did not answer yes" in n for n in res["notes"])


# 9 (medium): workbook bombs
def test_a_workbook_with_a_huge_column_reference_is_refused(tmp_path):
    f = tmp_path / "x.xlsx"
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData><row r="1"><c r="ZZZZZZZZ1"><v>1</v></c>'
                   '</row></sheetData></worksheet>')
    with pytest.raises(ValueError, match="beyond column"):
        tables.read(f)
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", '<!DOCTYPE x>'.encode("utf-16"))
    with pytest.raises(ValueError, match="not UTF-8"):
        tables.read(f)


# 10 (medium): the header describes the latest run
def test_the_header_refuses_an_earlier_run(h):
    from harness_core import answer
    (h["ws"] / "sales.csv").write_text(SALES)
    compiled(h)
    m = {"metric_id": "revenue", "inclusions": "sales", "exclusions": "VAT", "unit": "amount", "currency": "IRT"}
    contact.apply({"tool": "contact", "type": "definition_confirm", "metric": m}, ctx_of(h), {"session_id": SID})
    say(h, "yes", "d1")
    contact.apply({"tool": "contact", "type": "definition_confirm", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    first = run_req(h, metric_id="revenue")
    run_req(h, metric_id="revenue", attempt=2)
    with pytest.raises(Denied, match="not the latest"):
        answer.apply({"tool": "answer", "sub": "header", "unit": "u1", "output": first["sha256"]}, ctx_of(h),
                     {"session_id": SID})


# 11 (medium): money caps say when they cannot work, and keep their worth across a currency change
def test_spending_limits_warn_without_a_rate(h):
    confirm(h, "business", typed(h, "/dynamic-harness:business set currency IRT", "b1"), "b2")
    assert "spending limits apply only once you set the rate" in typed(h, "/dynamic-harness:business set caps.task "
                                                                        "500000", "b3")


def test_a_currency_change_converts_toman_and_rial_limits(h):
    confirm(h, "business", typed(h, "/dynamic-harness:business set currency IRT", "b1"), "b2")
    confirm(h, "business", typed(h, "/dynamic-harness:business set caps.task 500000", "b3"), "b4")
    confirm(h, "business", typed(h, "/dynamic-harness:business set currency IRR", "b5"), "b6")
    assert business.profile(ctx_of(h))["caps"] == {"task": 5000000, "currency": "IRR"}


# 12 (medium): fetch details
def test_an_unknown_charset_does_not_crash_the_fetch(h, inv):
    res = fetch(h, PLAN["urls"][0], opener(headers={"content-type": "text/html; charset=x-unknown-9"}))
    assert res["status"] == "OK" and "Steel prices rose" in res["text"]


def test_plan_urls_with_digits_are_fetchable(h, inv):
    from harness_core import claims as cl
    url = "https://a.example.com/news/2026/11/04/12345"
    grant = dict(ctx_of(h).grant or {})
    c = ctx_of(h)
    c.agent_type = cl.egress.RESEARCHER
    c.grant = dict(grant, urls=grant.get("urls", []) + [url])
    res = cl.apply_fetch({"url": url}, c, {"session_id": SID, "agent_type": cl.egress.RESEARCHER},
                         opener=opener({url: PAGES[PLAN["urls"][0]]}))
    assert res["status"] == "OK"


# 14 (low): export
def test_export_neutralises_formulas_and_stays_in_its_folder(h):
    from harness_core import record
    job = compiled(h)
    record.write(ctx_of(h), job, "DONE")
    p = h["data"] / "records" / f"{job}.jsonl"
    row = json.loads(p.read_text().splitlines()[-1])
    other = dict(row, job_id="=HYPERLINK(1)", workspace_sha="0" * 64)
    mine = dict(row, requester="=cmd|calc")
    p.write_text(json.dumps(other) + "\n" + json.dumps(mine) + "\n")
    typed(h, f"/dynamic-harness:export --period {row['ts'][:7]}", "e1")
    text = next((h["ws"] / "exports").glob("*.csv")).read_text()
    assert "HYPERLINK" not in text and "'=cmd|calc" in text


# 16, 17 (low)
def test_fiscal_year_start_must_exist_every_year(h):
    assert "must exist every year" in typed(h, "/dynamic-harness:business set fiscal_year_start 07-31", "f1")
    assert "must exist every year" in typed(h, "/dynamic-harness:business set fiscal_year_start 12-30", "f2")


@pytest.mark.parametrize("text", ["باشه", "تاييد", "بلي"])
def test_persian_approvals_with_arabic_letters(text):
    assert contact.is_approval(text) is (text != "بلي")
    assert re is not None and hookmain is not None and Ledger is not None
