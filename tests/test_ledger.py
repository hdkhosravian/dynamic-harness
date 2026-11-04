from __future__ import annotations

import json

import pytest

from harness_core import canon
from harness_core.errors import Denied, HarnessError, SchemaError, TamperError
from harness_core.events import EventLog
from harness_core.ledger import Ledger, Outputs
from harness_core.paths import job_paths
from harness_core.turns import Turns

SCOPE = [
    {"id": "u1", "claim": "invoice totals", "label": "Invoices", "check_id": "recon.totals", "deliverable": "inv.csv"},
    {"id": "u2", "claim": "payroll check", "label": "Payroll", "check_id": "recon.payroll", "deliverable": "pay.csv"},
    {"id": "h1", "claim": "hypothesis", "label": "Churn", "unit_type": "investigation"},
]


@pytest.fixture
def h(env):
    p = job_paths(env["data"], env["ws"], "j1")
    log = EventLog(p.run, p.heads)
    turns = Turns(env["data"])
    led = Ledger(p, log)
    led.freeze_scope(SCOPE, turns)
    return {"p": p, "log": log, "led": led, "out": Outputs(p, log), "turns": turns}


def _ok(h, unit="u1", check="recon.totals", attempt=1, code=0, result="pass", data=b"ok"):
    return h["out"].record(check, unit, attempt, code, result, {"n": 1}, data)


def _jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


# --- Outputs -----------------------------------------------------------------

def test_outputs_record_writes_row_blob_and_event(h):
    r = _ok(h, data=b"result-bytes")
    assert r == {"check_id": "recon.totals", "unit": "u1", "attempt": 1, "exit_code": 0, "result": "pass",
                 "values": {"n": 1}, "sha256": canon.sha256_bytes(b"result-bytes")}
    assert _jsonl(h["p"].run / "outputs" / "outputs.jsonl") == [r]
    assert (h["p"].run / "outputs" / "blobs" / r["sha256"]).read_bytes() == b"result-bytes"
    e = h["log"].events()[-1]
    assert e["type"] == "OUTPUT" and e["row_sha"] == canon.sha256_json(r)
    h["log"].check_file(h["p"].run / "outputs" / "outputs.jsonl", "outputs/outputs.jsonl")


# --- PASS rules -----------------------------------------------------------------

def test_pass_with_valid_output_is_written_with_event(h):
    o = _ok(h)
    row = h["led"].append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS", "outputs": [o["sha256"]]})
    assert _jsonl(h["p"].run / "ledger.jsonl") == [row]
    e = h["log"].events()[-1]
    assert e["type"] == "ROW" and e["row_sha"] == canon.sha256_json(row) and e["file"] == "ledger.jsonl"
    assert h["led"].state("u1") == "PASS"


@pytest.mark.parametrize(
    "kw",
    [
        {"code": 1},
        {"result": "fail"},
        {"check": "other.check"},
        {"unit": "u2", "check": "recon.totals"},
    ],
)
def test_pass_rejected_when_cited_output_is_not_a_clean_registered_pass(h, kw):
    o = _ok(h, **kw)
    with pytest.raises(Denied):
        h["led"].append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS", "outputs": [o["sha256"]]})
    assert h["led"].state("u1") == "OPEN"


def test_pass_rejected_without_outputs_or_with_unknown_output(h):
    with pytest.raises(Denied):
        h["led"].append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS"})
    with pytest.raises(Denied):
        h["led"].append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS", "outputs": ["0" * 64]})


def test_pass_rejected_if_any_cited_output_is_bad(h):
    good, bad = _ok(h, data=b"a"), _ok(h, code=2, data=b"b")
    with pytest.raises(Denied):
        h["led"].append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS",
                                     "outputs": [good["sha256"], bad["sha256"]]})


def test_row_for_unit_outside_scope_or_bad_schema_rejected(h):
    with pytest.raises(Denied):
        h["led"].append_row("unit", {"id": "zz", "attempt": 1, "verdict": "BLOCKED", "reason": "x"})
    with pytest.raises(SchemaError):
        h["led"].append_row("unit", {"id": "u1", "attempt": 0, "verdict": "BLOCKED"})
    with pytest.raises(SchemaError):
        h["led"].append_row("unit", {"id": "u1", "attempt": 1, "verdict": "DONE"})
    with pytest.raises(HarnessError):
        h["led"].append_row("bogus", {"id": "u1"})


def test_pass_flagged_cannot_be_written_directly(h):
    with pytest.raises(Denied):
        h["led"].append_row("unit", {"id": "u1", "attempt": 1, "verdict": "PASS_FLAGGED", "failure_sig": "x"})


def test_blocked_needs_reason(h):
    with pytest.raises(Denied):
        h["led"].append_row("unit", {"id": "u1", "attempt": 1, "verdict": "BLOCKED"})


# --- SUPPORTED / REFUTED ----------------------------------------------------------

def test_supported_needs_research_check_exit_zero(h):
    with pytest.raises(Denied):
        h["led"].append_row("verdict", {"id": "h1", "attempt": 1, "verdict": "SUPPORTED"})
    bad = _ok(h, unit="h1", check="research.check", code=1, result="fail", data=b"r1")
    with pytest.raises(Denied):
        h["led"].append_row("verdict", {"id": "h1", "attempt": 1, "verdict": "REFUTED", "outputs": [bad["sha256"]]})
    good = _ok(h, unit="h1", check="research.check", data=b"r2")
    row = h["led"].append_row("verdict", {"id": "h1", "attempt": 1, "verdict": "SUPPORTED",
                                          "outputs": [good["sha256"]], "finding": "yes"})
    assert _jsonl(h["p"].run / "verdicts.jsonl") == [row]
    assert h["led"].state("h1") == "SUPPORTED"


# --- 2nd FAIL rule ------------------------------------------------------------------

def _fail(h, attempt, unit="u1", check="recon.totals"):
    o = _ok(h, unit=unit, check=check, attempt=attempt, code=1, result="fail", data=f"f{attempt}{check}".encode())
    return h["led"].append_row("unit", {"id": unit, "attempt": attempt, "verdict": "FAIL",
                                        "outputs": [o["sha256"]], "failure_sig": "total off by 3"})


def test_first_fail_is_fail(h):
    assert _fail(h, 1)["verdict"] == "FAIL"
    assert h["led"].state("u1") == "FAIL"


def test_second_fail_with_deliverable_from_registered_check_is_pass_flagged(h):
    h["p"].work.mkdir(parents=True)
    (h["p"].work / "inv.csv").write_text("a,b\n")
    _fail(h, 1)
    row = _fail(h, 2)
    assert row["verdict"] == "PASS_FLAGGED" and row["failure_sig"] == "total off by 3"
    assert h["led"].state("u1") == "PASS_FLAGGED"
    assert [r["verdict"] for r in _jsonl(h["p"].run / "ledger.jsonl")] == ["FAIL", "FAIL", "PASS_FLAGGED"]


def test_second_fail_without_deliverable_is_blocked(h):
    _fail(h, 1)
    row = _fail(h, 2)
    assert row["verdict"] == "BLOCKED" and row["reason"]
    assert h["led"].state("u1") == "BLOCKED"


def test_second_fail_not_from_registered_check_is_blocked(h):
    h["p"].work.mkdir(parents=True)
    (h["p"].work / "inv.csv").write_text("a,b\n")
    _fail(h, 1)
    assert _fail(h, 2, check="agent.script")["verdict"] == "BLOCKED"


def test_second_fail_with_symlinked_deliverable_is_blocked(h, tmp_path):
    h["p"].work.mkdir(parents=True)
    (tmp_path / "elsewhere").write_text("x")
    (h["p"].work / "inv.csv").symlink_to(tmp_path / "elsewhere")
    _fail(h, 1)
    assert _fail(h, 2)["verdict"] == "BLOCKED"


# --- other kinds ----------------------------------------------------------------------

def test_milestone_needs_hashed_work_file(h):
    with pytest.raises(Denied):
        h["led"].append_row("milestone", {"milestone": "draft", "file": "report.md"})
    h["p"].work.mkdir(parents=True)
    (h["p"].work / "report.md").write_text("draft")
    row = h["led"].append_row("milestone", {"milestone": "draft", "file": "report.md"})
    assert row["sha256"] == canon.sha256_bytes(b"draft")
    with pytest.raises(SchemaError):
        h["led"].append_row("milestone", {"milestone": "nearly", "file": "report.md"})


def test_decision_and_contradiction_rows(h):
    h["led"].append_row("decision", {"id": "d1", "text": "use FY"})
    h["led"].append_row("contradiction", {"id": "c1", "a": "x", "b": "y"})
    assert _jsonl(h["p"].run / "decisions.jsonl")[0]["id"] == "d1"
    assert _jsonl(h["p"].run / "contradictions.jsonl")[0]["id"] == "c1"


def test_tampered_ledger_file_is_detected_on_next_append(h):
    h["led"].append_row("unit", {"id": "u2", "attempt": 1, "verdict": "BLOCKED", "reason": "no data"})
    p = h["p"].run / "ledger.jsonl"
    p.write_text(p.read_text().replace("BLOCKED", "PASS"))
    with pytest.raises(TamperError):
        h["led"].append_row("unit", {"id": "u1", "attempt": 1, "verdict": "BLOCKED", "reason": "x"})


# --- scope versions -------------------------------------------------------------------

def test_freeze_writes_v1_canonical_and_only_once(h):
    v1 = h["p"].run / "scope.v1.jsonl"
    assert v1.read_bytes() == canon.scope_bytes(SCOPE)
    assert h["led"].scope() == SCOPE
    with pytest.raises(HarnessError):
        h["led"].freeze_scope(SCOPE, h["turns"])


def test_descope_needs_cited_turn_after_freeze_naming_the_unit(h):
    t = h["turns"]
    with pytest.raises(Denied):
        h["led"].descope(["u2"], "missing", t)
    t.append({"turn_id": "t1", "text_norm": "please drop invoices"})
    with pytest.raises(Denied):
        h["led"].descope(["u2"], "t1", t)  # does not name Payroll
    with pytest.raises(Denied):
        h["led"].descope(["u1", "u2"], "t1", t)  # names only one of them
    v2 = h["led"].descope(["u1"], "t1", t)
    assert v2["version"] == 2 and v2["count"] == 2
    assert [r["id"] for r in h["led"].scope()] == ["u2", "h1"]
    des = [e for e in h["log"].events() if e["type"] == "DESCOPE"]
    assert [(e["payload"]["unit"], e["payload"]["turn_id"]) for e in des] == [("u1", "t1")]
    assert h["led"].state("u1") == "DESCOPED_BY_USER"
    with pytest.raises(Denied):
        h["led"].descope(["u2"], "t1", t)  # t1 is not after the v2 freeze


def test_descope_turn_before_freeze_is_denied(env):
    p = job_paths(env["data"], env["ws"], "j2")
    log, turns = EventLog(p.run, p.heads), Turns(env["data"])
    turns.append({"turn_id": "t0", "text_norm": "drop payroll"})
    led = Ledger(p, log)
    led.freeze_scope(SCOPE, turns)
    with pytest.raises(Denied):
        led.descope(["u2"], "t0", turns)


def test_edited_scope_file_is_detected(h):
    p = h["p"].run / "scope.v1.jsonl"
    p.write_bytes(canon.scope_bytes(SCOPE[:1]))
    with pytest.raises(TamperError):
        h["led"].scope()
