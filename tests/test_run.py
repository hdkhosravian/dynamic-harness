"""harness-run: registered pack checks run by the hook in a sandbox, outputs recorded, done mode replays them
(build phase P5)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core import canon, gatehook, hookmain, packs, run, store
from harness_core.errors import Denied, HarnessError
from harness_core.ledger import Ledger, Outputs
from test_budget import log_of
from test_integration_gate import FOLD, fold_ledger_problem
from test_p3_hooks import CARD, SID, compiled, ctx_of, h, post  # noqa: F401  (fixture h)

SALES = ("date,description,amount (Toman)\n"
         "1402/12/01,invoice 1,1000\n1402/12/05,invoice 2,2500\n1402/12/29,invoice 3,500\n")


def run_req(h, check="finance.reconcile", unit="u1", attempt=1, inputs=("sales.csv",), **args):
    req = {"tool": "run", "check_id": check, "unit": unit, "attempt": attempt, "inputs": list(inputs),
           "args": dict({"period": {"month": "1402-12"}}, **args)}
    return run.apply(req, ctx_of(h), {"session_id": SID})


@pytest.fixture
def job(h):
    (h["ws"] / "sales.csv").write_text(SALES, encoding="utf-8")
    return compiled(h)


def test_the_finance_pack_is_registered_with_matching_hashes(h):
    c = ctx_of(h)
    for cid in ("finance.reconcile", "finance.kpi", "finance.bridge", "finance.list"):
        assert packs.entry(c, cid).is_file()
    assert not packs.registered(c, "finance.nope") and not packs.registered(c, "x")


def test_a_changed_entry_is_refused(h, tmp_path, monkeypatch):
    c = ctx_of(h)
    real = packs.spec(c, "finance.reconcile")
    monkeypatch.setattr(packs, "spec", lambda ctx, cid: dict(real, sha256="0" * 64))
    with pytest.raises(HarnessError, match="manifest hash"):
        packs.entry(c, "finance.reconcile")


def test_run_records_the_check_output_with_its_snapshots(h, job):
    res = run_req(h)
    assert res["result"] == "pass" and res["values"]["total"] == 4000 and res["values"]["rows_used"] == 3
    p, log = log_of(h, job)
    row = Outputs(p, log).rows()[-1]
    assert row["sha256"] == res["sha256"] and row["check_id"] == "finance.reconcile"
    assert row["inputs"][0]["source"] == "sales.csv"
    for sha in (row["stdin_sha"], row["inputs"][0]["sha256"], row["sha256"]):
        assert (p.run / "outputs" / "blobs" / sha).is_file()


def test_gaps_fail_the_check_and_are_named(h, job):
    (h["ws"] / "sales.csv").write_text(SALES + ",no date,700\n1402/12/10,transfer between own accounts,9000\n",
                                       encoding="utf-8")
    res = run_req(h)
    assert res["result"] == "fail" and res["values"]["total"] == 4000
    assert any("left out: 1 rows with no date" in n for n in res["notes"])
    assert any("transfers between your own accounts" in n for n in res["notes"])


def test_inputs_must_be_workspace_files_outside_protected_paths(h, job):
    with pytest.raises(Denied):
        run_req(h, inputs=[str(h["data"] / "config_log.jsonl")])
    with pytest.raises(Denied):
        run_req(h, inputs=["/etc/hosts"])


def test_unregistered_checks_and_units_are_refused(h, job):
    with pytest.raises(Denied, match="not registered"):
        run_req(h, check="finance.nope")
    with pytest.raises(Denied, match="frozen scope"):
        run_req(h, unit="u9")


def test_a_crashing_check_is_an_error_not_a_result(h, job):
    res = run_req(h, amount_col="no such column")
    assert res["result"] == "error"
    p, log = log_of(h, job)
    with pytest.raises(Denied):  # an error output never backs a PASS
        Ledger(p, log).append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS", "outputs": [res["sha256"]]})


def test_the_main_post_hook_treats_harness_run_as_print_only(h, job):
    out = json.dumps({"tool": "run", "check_id": "finance.reconcile", "unit": "u1", "attempt": 1})
    code, res = post(h, "Bash", {"stdout": out}, command="harness-run finance.reconcile --unit u1 --attempt 1")
    assert code == 0 and res is None
    p, log = log_of(h, job)
    assert not Outputs(p, log).rows()


def test_the_run_hook_entry_runs_and_reports(h, job):
    out = json.dumps({"tool": "run", "check_id": "finance.reconcile", "unit": "u1", "attempt": 1,
                      "inputs": ["sales.csv"], "args": {"period": {"month": "1402-12"}}})
    evt = {"session_id": SID, "tool_name": "Bash", "tool_input": {"command": "harness-run finance.reconcile"},
           "tool_response": {"stdout": out}, "cwd": str(h["ws"])}
    code, res = hookmain.main("run", evt, h["env"])
    ctx_text = res["hookSpecificOutput"]["additionalContext"]
    assert code == 0 and "pass" in ctx_text and '"total": 4000' in ctx_text


def test_cards_need_registered_executable_oracles(h):
    from harness_core import compile as compile_
    from harness_core.card import CardError
    bad = dict(CARD, oracle={"type": "my.script", "executable": True, "criteria": ["x"]})
    with pytest.raises(CardError, match="not a check registered"):
        compiled(h, card=bad)
    job = compiled(h, text="how many orders came in this week? again", pid="p2")
    p, log = log_of(h, job)
    assert Ledger(p, log).scope()[0]["check_id"] == "finance.reconcile"  # the oracle's check by default
    assert compile_ is not None


@pytest.mark.skipif(fold_ledger_problem() is not None, reason=str(fold_ledger_problem()))
def test_done_mode_replays_the_recorded_check_on_the_submitted_deliverable(h, job, monkeypatch):
    c = ctx_of(h)
    c.loop_contract_root = Path(FOLD).parents[3]  # the real fold_ledger under the real sandbox
    res = run_req(h)
    p, log = log_of(h, job)
    Ledger(p, log).append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS", "outputs": [res["sha256"]]})
    (p.work / "report.md").write_text("Esfand 1402 sales: 4,000 Toman.\n")
    out = gatehook.run({"session_id": SID}, c)
    assert out["mode"] == "done" and out["state"] == "Checked", out
    prof = json.loads((p.run / "gate" / "profile.json").read_text())
    assert prof["oracle_cmd"].endswith("oracle.py") and prof["oracle_check"] == "finance.reconcile"


@pytest.mark.skipif(fold_ledger_problem() is not None, reason=str(fold_ledger_problem()))
def test_a_tampered_output_fails_the_replay(h, job, monkeypatch):
    c = ctx_of(h)
    c.loop_contract_root = Path(FOLD).parents[3]  # the real fold_ledger under the real sandbox
    res = run_req(h)
    p, log = log_of(h, job)
    Ledger(p, log).append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS", "outputs": [res["sha256"]]})
    stdin = Outputs(p, log).rows()[-1]["stdin_sha"]
    blob = p.run / "outputs" / "blobs" / stdin
    req = json.loads(blob.read_bytes())
    req["args"]["period"] = {"month": "1402-11"}  # a different question than the recorded one
    blob.write_bytes(canon.canonical_json(req))
    with pytest.raises(HarnessError, match="gate failed"):
        gatehook.run({"session_id": SID}, c)
    assert store is not None
