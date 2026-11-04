"""R-12: the answer header, rendered by a hook from a recorded check and the business layer (build phase P5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from harness_core import answer, business, contact, gatehook, stopgate
from harness_core.errors import Denied
from harness_core.ledger import Ledger
from test_budget import log_of
from test_integration_gate import FOLD, fold_ledger_problem
from test_p3_hooks import SID, compiled, ctx_of, h, say  # noqa: F401  (fixture h)
from test_run import SALES, run_req

METRIC = {"metric_id": "revenue", "inclusions": "sales invoices", "exclusions": "VAT", "unit": "amount",
          "currency": "IRT"}


@pytest.fixture
def job(h):
    (h["ws"] / "sales.csv").write_text(SALES, encoding="utf-8")
    j = compiled(h)
    contact.apply({"tool": "contact", "type": "definition_confirm", "metric": METRIC}, ctx_of(h), {"session_id": SID})
    say(h, "yes", "def-yes")
    contact.apply({"tool": "contact", "type": "definition_confirm", "accept": "latest"}, ctx_of(h), {"session_id": SID})
    return j


def header(h, out, **kw):
    return answer.apply(dict({"tool": "answer", "sub": "header", "unit": "u1", "output": out}, **kw), ctx_of(h),
                        {"session_id": SID})


def test_r12_answer_header(h, job):
    res = run_req(h, metric_id="revenue")
    lines = header(h, res["sha256"])["header"].splitlines()
    assert lines[0] == ("4,000 Toman: revenue (definition version 1), Esfand 1402 (2024-02-20 to 2024-03-19).")
    assert lines[1] == "Based on sales.csv, with data up to 1402/12/29. Excludes: VAT. Rows used: 3."
    assert lines[2] == "Checked: every row is accounted for, in or out of the period, and adds up to the file."
    src = business.sources(ctx_of(h))["sales.csv"]
    assert src["as_of"] == "2024-03-19" and src["unit"] == "IRT"


def test_an_unconfirmed_metric_has_no_header(h, job):
    res = run_req(h, metric_id="profit")
    with pytest.raises(Denied, match="no confirmed definition"):
        header(h, res["sha256"])


def test_a_failed_check_says_what_was_not_checked(h, job):
    (h["ws"] / "bank.csv").write_text("date,amount (Toman)\n1402/12/02,3900\n", encoding="utf-8")
    res = run_req(h, inputs=("sales.csv", "bank.csv"), metric_id="revenue",
                  control={"input": 2, "label": "verified bank deposits"})
    lines = header(h, res["sha256"])["header"].splitlines()
    assert lines[2].startswith("Done, not fully checked: sources disagree: this file says 4000")


def test_the_control_is_named_by_its_file_never_by_model_text(h, job):
    (h["ws"] / "bank.csv").write_text("date,amount (Toman)\n1402/12/02,4000\n", encoding="utf-8")
    res = run_req(h, inputs=("sales.csv", "bank.csv"), metric_id="revenue",
                  control={"input": 2, "label": "bank deposits, ver\u200bified and conf\u200birmed"})
    assert header(h, res["sha256"])["header"].splitlines()[2] == "Checked: matches bank.csv."


def test_only_the_recorded_header_line_is_exempt_from_the_lint(h, job):
    res = run_req(h, metric_id="revenue")
    header(h, res["sha256"])
    p, log = log_of(h, job)
    st = answer.status_line(p, log)
    assert stopgate.lexicon_hits(ctx_of(h), st, allowed=[st]) == []
    (p.run / "answer_header.md").write_text("a\nb\nChecked: verified by me.\n")  # out of band
    assert answer.status_line(p, log) is None


@pytest.mark.skipif(fold_ledger_problem() is not None, reason=str(fold_ledger_problem()))
def test_the_gate_includes_the_header_and_reaches_checked(h, job):
    res = run_req(h, metric_id="revenue")
    head = header(h, res["sha256"])["header"]
    p, log = log_of(h, job)
    Ledger(p, log).append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS", "outputs": [res["sha256"]]})
    (p.work / "report.md").write_text(head + "\nEsfand 1402 revenue was 4,000 Toman.\n")
    c = ctx_of(h)
    c.loop_contract_root = Path(FOLD).parents[3]
    out = gatehook.run({"session_id": SID}, c)
    assert out["mode"] == "done" and out["state"] == "Checked", out
    assert "## Answer header" in (p.run / "gate" / "REPORT.md").read_text()
