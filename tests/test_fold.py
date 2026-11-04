from __future__ import annotations

import json

import pytest

from harness_core import canon, fold
from harness_core.errors import HarnessError, TamperError
from harness_core.events import EventLog
from harness_core.ledger import Ledger, Outputs
from harness_core.paths import job_paths
from harness_core.turns import Turns

SCOPE = [
    {"id": "u1", "claim": "a", "label": "Alpha", "check_id": "c.a", "deliverable": "a.csv", "unit_type": "recon"},
    {"id": "u2", "claim": "b", "label": "Beta", "check_id": "c.b", "deliverable": "b.csv", "unit_type": "recon"},
]


@pytest.fixture
def h(env):
    p = job_paths(env["data"], env["ws"], "j1")
    log = EventLog(p.run, p.heads)
    turns = Turns(env["data"])
    led = Ledger(p, log)
    led.freeze_scope(SCOPE, turns)
    p.work.mkdir(parents=True)
    return {"p": p, "log": log, "led": led, "out": Outputs(p, log), "turns": turns}


def _pass(h, unit, check, attempt=1):
    o = h["out"].record(check, unit, attempt, 0, "pass", {}, f"{unit}{attempt}".encode())
    h["led"].append_row("unit", {"id": unit, "attempt": attempt, "verdict": "PASS", "outputs": [o["sha256"]]})
    return o


def _fail(h, unit, check, attempt):
    o = h["out"].record(check, unit, attempt, 1, "fail", {}, f"f{unit}{attempt}".encode())
    h["led"].append_row("unit", {"id": unit, "attempt": attempt, "verdict": "FAIL", "outputs": [o["sha256"]],
                                 "failure_sig": "off by 1"})


def _gate(h, name):
    return [json.loads(x) for x in (h["p"].run / "gate" / name).read_text().splitlines()]


def test_all_pass_is_done_mode_with_mapped_rows(h):
    o1 = _pass(h, "u1", "c.a")
    _pass(h, "u2", "c.b")
    res = fold.project(h["p"], h["led"], profile={"allow_blocked": 0})
    assert res["mode"] == "done" and res["allow_blocked"] == 0
    rows = _gate(h, "ledger.jsonl")
    assert [r["id"] for r in rows] == ["u1", "u2"]
    r1 = rows[0]
    assert r1["verdict"] == "PASS" and r1["fix_verdict"] == "PASS"
    assert r1["evidence"] == [f"outputs/blobs/{o1['sha256']}"]
    assert r1["proof_cmd"] == "harness-run c.a --unit u1 --attempt 1"
    assert r1["fix"] == "outputs/submitted/a.csv" and r1["unit_type"] == "recon"
    assert res["rows"] == rows


def test_gate_scope_is_canonical_and_pinned(h):
    _pass(h, "u1", "c.a")
    res = fold.project(h["p"], h["led"])
    b = (h["p"].run / "gate" / "scope.jsonl").read_bytes()
    assert b == canon.scope_bytes(SCOPE)
    pins = json.loads((h["p"].data_root / "pins" / "j1.json").read_text())
    assert pins == {"scope_sha": canon.sha256_bytes(b), "scope_count": 2}
    assert (res["scope_sha"], res["scope_count"]) == (pins["scope_sha"], 2)
    assert json.loads((h["p"].run / "gate" / "profile.json").read_text()) == {}


def test_open_unit_has_no_row_and_gives_known_mode(h):
    _pass(h, "u1", "c.a")
    res = fold.project(h["p"], h["led"], profile={"allow_blocked": 0})
    assert res["mode"] == "known" and res["allow_blocked"] == 2
    assert [r["id"] for r in res["rows"]] == ["u1"]
    assert res["remaining"] == ["u2"]


def test_refuses_while_latest_verdict_is_fail(h):
    _pass(h, "u1", "c.a")
    _fail(h, "u2", "c.b", 1)
    with pytest.raises(HarnessError):
        fold.project(h["p"], h["led"])


def test_pass_flagged_maps_to_partial_and_known_mode(h):
    (h["p"].work / "b.csv").write_text("x")
    _pass(h, "u1", "c.a")
    _fail(h, "u2", "c.b", 1)
    _fail(h, "u2", "c.b", 2)
    res = fold.project(h["p"], h["led"])
    r2 = res["rows"][1]
    assert r2["verdict"] == "PARTIAL" and r2["missing"] == "off by 1" and r2["evidence"] and r2["proof_cmd"]
    assert res["mode"] == "known"


def test_blocked_within_allowance_is_done_beyond_is_known(h):
    _pass(h, "u1", "c.a")
    h["led"].append_row("unit", {"id": "u2", "attempt": 1, "verdict": "BLOCKED", "reason": "no access"})
    res = fold.project(h["p"], h["led"], profile={"allow_blocked": "units"})
    assert res["mode"] == "done"
    assert res["rows"][1] == {"id": "u2", "verdict": "BLOCKED", "blocked_reason": "no access", "unit_type": "recon"}
    assert fold.project(h["p"], h["led"], profile={"allow_blocked": 0})["mode"] == "known"


def test_all_blocked_is_known(h):
    for u in ("u1", "u2"):
        h["led"].append_row("unit", {"id": u, "attempt": 1, "verdict": "BLOCKED", "reason": "r"})
    assert fold.project(h["p"], h["led"], profile={"allow_blocked": "units"})["mode"] == "known"


def test_unverified_and_investigation_mappings(env):
    p = job_paths(env["data"], env["ws"], "j3")
    log = EventLog(p.run, p.heads)
    led, out = Ledger(p, log), Outputs(p, log)
    led.freeze_scope([
        {"id": "v", "claim": "c", "label": "V"},
        {"id": "s", "claim": "c", "label": "S"},
        {"id": "i", "claim": "c", "label": "I"},
        {"id": "n", "claim": "c", "label": "N"},
    ])
    led.append_row("unit", {"id": "v", "attempt": 1, "verdict": "UNVERIFIED", "evidence": ["critic/v.json"]})
    o = out.record("research.check", "s", 1, 0, "pass", {}, b"rc")
    led.append_row("verdict", {"id": "s", "attempt": 1, "verdict": "SUPPORTED", "outputs": [o["sha256"]],
                               "finding": "holds", "evidence": ["snapshots/x", "claims.jsonl:3"]})
    led.append_row("verdict", {"id": "i", "attempt": 1, "verdict": "INCONCLUSIVE", "reason": "need Q3 data",
                               "evidence": ["claims.jsonl:4"]})
    led.append_row("verdict", {"id": "n", "attempt": 1, "verdict": "NOT_INVESTIGATED", "reason": "budget"})
    rows = {r["id"]: r for r in fold.project(p, led)["rows"]}
    assert rows["v"]["verdict"] == "PARTIAL" and rows["v"]["missing"] == "no executable check"
    assert rows["v"]["proof_cmd"] == "harness-critic verify --unit v --attempt 1"
    assert rows["s"]["verdict"] == "PASS" and rows["s"]["fix"] == "none"
    assert rows["s"]["no_change_reason"] == "investigation verdict" and rows["s"]["finding"] == "holds"
    assert rows["s"]["evidence"] == ["snapshots/x", "claims.jsonl:3", f"outputs/blobs/{o['sha256']}"]
    assert rows["s"]["proof_cmd"] == "research.check --unit s --attempt 1"
    assert rows["i"]["verdict"] == "PARTIAL" and rows["i"]["missing"] == "inconclusive: need Q3 data"
    assert rows["n"] == {"id": "n", "verdict": "BLOCKED", "blocked_reason": "budget"}


def test_verdicts_history_ends_with_latest_state(h):
    (h["p"].work / "a.csv").write_text("x")
    _pass(h, "u1", "c.a", 1)
    _fail(h, "u1", "c.a", 2)
    _fail(h, "u1", "c.a", 3)
    fold.project(h["p"], h["led"])
    hist = [r for r in _gate(h, "verdicts.jsonl") if r["id"] == "u1"]
    assert [r["verdict"] for r in hist] == ["PASS", "PARTIAL"]


def test_descoped_unit_dropped_from_gate(h):
    _pass(h, "u1", "c.a")
    h["turns"].append({"turn_id": "t1", "text_norm": "skip beta"})
    h["led"].descope(["u2"], "t1", h["turns"])
    res = fold.project(h["p"], h["led"])
    assert res["scope_count"] == 1 and res["mode"] == "done"
    assert (h["p"].run / "gate" / "scope.jsonl").read_bytes() == canon.scope_bytes(SCOPE[:1])


def test_silent_scope_removal_raises(h):
    _pass(h, "u1", "c.a")
    data = canon.scope_bytes(SCOPE[:1])
    (h["p"].run / "scope.v2.jsonl").write_bytes(data)
    h["log"].append("SCOPE", {"version": 2, "count": 1, "scope_sha": canon.sha256_bytes(data),
                              "turn_seq": 0, "removed": []}, file="scope.v2.jsonl", file_sha=canon.sha256_bytes(data))
    with pytest.raises(HarnessError):
        fold.project(h["p"], h["led"])


def test_caller_scope_must_equal_sanctioned(h):
    _pass(h, "u1", "c.a")
    with pytest.raises(HarnessError):
        fold.project(h["p"], h["led"], scope=SCOPE[:1])
    assert fold.project(h["p"], h["led"], scope=SCOPE)["scope_count"] == 2


def test_tampered_row_file_fails_projection(h):
    _pass(h, "u1", "c.a")
    p = h["p"].run / "outputs" / "outputs.jsonl"
    p.write_bytes(p.read_bytes() + b"{}\n")
    with pytest.raises(TamperError):
        fold.project(h["p"], h["led"])


# --- review fixes: allow_blocked expressions (8), decisions projection (9) ---

@pytest.mark.parametrize("expr, units, want", [
    ("units", 2, 2), (1, 2, 1), ("1", 2, 1), ("ceil(0.2*units)", 2, 1), ("ceil(0.2 * units)", 11, 3),
    ("floor(0.5*units)", 3, 1),
])
def test_allow_blocked_expression(expr, units, want):
    assert fold.allow_blocked(expr, units) == want


@pytest.mark.parametrize("expr", ["__import__('os').system('x')", "units**99", "sqrt(units)", "unit", "0.5*units",
                                  "ceil(units, 2)", True, -1, "", None, "ceil(x.y)"])
def test_unknown_allow_blocked_expression_raises(expr):
    with pytest.raises(HarnessError):
        fold.allow_blocked(expr, 4)


def test_project_evaluates_the_profile_expression(h):
    _pass(h, "u1", "c.a")
    h["led"].append_row("unit", {"id": "u2", "attempt": 1, "verdict": "BLOCKED", "reason": "no access"})
    res = fold.project(h["p"], h["led"], profile={"allow_blocked": "ceil(0.2*units)"})
    assert res["mode"] == "done" and res["allow_blocked"] == 1
    with pytest.raises(HarnessError):
        fold.project(h["p"], h["led"], profile={"allow_blocked": "lots"})


def test_project_writes_decisions_from_ledger_rows(h):
    _pass(h, "u1", "c.a")
    h["led"].append_row("decision", {"id": "d1", "text": "use March data"})
    fold.project(h["p"], h["led"])
    assert _gate(h, "decisions.jsonl") == [{"id": "d1", "text": "use March data"}]
    projected = [e for e in h["log"].events() if e["type"] == "PROJECTED"][-1]
    assert projected["payload"]["files"]["gate/decisions.jsonl"] == canon.sha256_bytes(
        (h["p"].run / "gate" / "decisions.jsonl").read_bytes())
