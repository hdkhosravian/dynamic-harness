"""Named invariant tests for P1: R-2 (done only via gate receipt), R-3 (no silent
scope shrink), R-6 (protected writes detected; detection fails gate and Stop)."""
from __future__ import annotations

import json

import pytest

from harness_core import canon, fold, gate
from harness_core.errors import Denied, HarnessError, TamperError
from harness_core.events import EventLog
from harness_core.ledger import Ledger, Outputs
from harness_core.paths import job_paths
from harness_core.turns import Turns

KEY = b"s" * 32
SCOPE = [
    {"id": "u1", "claim": "a", "label": "Alpha", "check_id": "c.a", "deliverable": "a.csv"},
    {"id": "u2", "claim": "b", "label": "Beta", "check_id": "c.b", "deliverable": "b.csv"},
    {"id": "u3", "claim": "c", "label": "Gamma", "check_id": "c.c", "deliverable": "c.csv"},
]
PROFILE = {"allow_blocked": "units", "oracle_cmd": "python3 oracle.py"}


def runner(argv, cwd, stdin, **kw):
    a = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("--")}
    with open(a["--out"], "w") as f:
        f.write("# REPORT\n")
    with open(a["--receipt"], "w") as f:
        json.dump({"caps": [], "mode": a["--mode"], "scope_sha": a["--expect-scope-sha"],
                   "events_head": a["--events-head"]}, f)
    return 0, "", ""


@pytest.fixture
def h(env):
    p = job_paths(env["data"], env["ws"], "j1")
    log = EventLog(p.run, p.heads)
    turns = Turns(env["data"])
    led = Ledger(p, log)
    led.freeze_scope(SCOPE, turns)
    p.work.mkdir(parents=True)
    for u in SCOPE:
        (p.work / u["deliverable"]).write_text(u["id"])
    return {"p": p, "log": log, "led": led, "out": Outputs(p, log), "turns": turns}


def _pass_all(h, units=("u1", "u2", "u3")):
    for u in units:
        unit = next(s for s in SCOPE if s["id"] == u)
        o = h["out"].record(unit["check_id"], u, 1, 0, "pass", {}, u.encode())
        h["led"].append_row("unit", {"id": u, "attempt": 1, "verdict": "PASS", "outputs": [o["sha256"]]})


def _gate(h):
    return gate.run_gate(h["p"], h["log"], KEY, PROFILE, "/lc/fold_ledger.py", runner=runner)


def _verify(h, key=KEY):
    return gate.verify_receipt(h["p"], h["log"], key)


# --- R-2 ------------------------------------------------------------------------------

def test_r2_no_done_without_gate_receipt(h):
    _pass_all(h)
    assert _verify(h)["ok"] is False


def test_r2_done_after_gate_receipt(h):
    _pass_all(h)
    assert _gate(h)["ok"]
    assert _verify(h) == {"ok": True, "state": "Checked", "caps": []}


def test_r2_hand_written_receipt_rejected(h):
    """An agent that writes receipt.json itself (no key, no writer event) is not done."""
    _pass_all(h)
    head = h["log"].verify()
    fields = {k: "" for k in gate.HMAC_FIELDS}
    fields.update(events_head=head, mode="done", caps_sha=canon.sha256_json([]))
    rec = dict(fields, caps=[], hmac=canon.hmac_receipt(b"guess", fields))
    (h["p"].run / "receipt.json").write_text(json.dumps(rec))
    assert _verify(h)["ok"] is False


def test_r2_receipt_invalid_after_ledger_changes(h):
    _pass_all(h, ("u1", "u2"))
    _gate(h)
    assert _verify(h)["ok"]
    _pass_all(h, ("u3",))
    assert _verify(h)["ok"] is False


def test_r2_receipt_invalid_after_scope_change(h):
    _pass_all(h)
    _gate(h)
    h["turns"].append({"turn_id": "t1", "text_norm": "drop gamma"})
    h["led"].descope(["u3"], "t1", h["turns"])
    assert _verify(h)["ok"] is False


def test_r2_receipt_covers_submitted_copy_not_work(h):
    _pass_all(h)
    _gate(h)
    (h["p"].work / "a.csv").write_text("edited after submission")
    assert _verify(h)["ok"] is True  # work/ is not what was gated
    (h["p"].run / "outputs" / "submitted" / "a.csv").write_text("edited")
    assert _verify(h)["ok"] is False


# --- R-3 ------------------------------------------------------------------------------

def test_r3_descope_requires_cited_user_turn(h):
    with pytest.raises(Denied):
        h["led"].descope(["u3"], "no-such-turn", h["turns"])
    h["turns"].append({"turn_id": "t1", "text_norm": "fine, do it all"})
    with pytest.raises(Denied):
        h["led"].descope(["u3"], "t1", h["turns"])
    h["turns"].append({"turn_id": "t2", "text_norm": "leave out gamma"})
    h["led"].descope(["u3"], "t2", h["turns"])
    assert [e["payload"]["unit"] for e in h["log"].events() if e["type"] == "DESCOPE"] == ["u3"]
    assert h["led"].state("u3") == "DESCOPED_BY_USER"


def test_r3_gate_count_equals_latest_sanctioned_scope(h):
    h["turns"].append({"turn_id": "t1", "text_norm": "drop gamma"})
    h["led"].descope(["u3"], "t1", h["turns"])
    _pass_all(h, ("u1", "u2"))
    res = _gate(h)
    assert res["ok"]
    pins = json.loads((h["p"].data_root / "pins" / "j1.json").read_text())
    assert pins["scope_count"] == 2
    assert pins["scope_sha"] == res["receipt"]["scope_sha"]


def test_r3_silent_removal_via_new_version_raises(h):
    _pass_all(h, ("u1", "u2"))
    data = canon.scope_bytes(SCOPE[:2])
    (h["p"].run / "scope.v2.jsonl").write_bytes(data)
    h["log"].append("SCOPE", {"version": 2, "count": 2, "scope_sha": canon.sha256_bytes(data),
                              "turn_seq": 0, "removed": []}, file="scope.v2.jsonl", file_sha=canon.sha256_bytes(data))
    with pytest.raises(HarnessError):
        fold.project(h["p"], h["led"])


def test_r3_silent_removal_by_editing_scope_file_raises(h):
    _pass_all(h, ("u1", "u2"))
    (h["p"].run / "scope.v1.jsonl").write_bytes(canon.scope_bytes(SCOPE[:2]))
    with pytest.raises(HarnessError):
        fold.project(h["p"], h["led"])


def test_r3_every_scoped_unit_is_a_row_or_remaining(h):
    _pass_all(h, ("u1",))
    h["led"].append_row("unit", {"id": "u2", "attempt": 1, "verdict": "BLOCKED", "reason": "no data"})
    res = fold.project(h["p"], h["led"], profile=PROFILE)
    assert sorted([r["id"] for r in res["rows"]] + res["remaining"]) == ["u1", "u2", "u3"]


# --- R-6 ------------------------------------------------------------------------------

@pytest.mark.parametrize("target", ["ledger.jsonl", "outputs/outputs.jsonl", "events.jsonl", "scope.v1.jsonl"])
def test_r6_out_of_band_write_fails_gate(h, target):
    """gate -> out-of-band write -> gate again: the earlier receipt is revoked and no new one is written."""
    _pass_all(h)
    assert _gate(h)["ok"] and (h["p"].run / "receipt.json").exists()
    p = h["p"].run / target
    p.write_bytes(p.read_bytes() + b'{"id":"u9"}\n')
    with pytest.raises(TamperError):
        _gate(h)
    assert not (h["p"].run / "receipt.json").exists()
    assert _verify(h)["ok"] is False
    if target != "events.jsonl":  # a broken chain cannot take a new event; the deletion alone revokes
        types = [e["type"] for e in h["log"].events()]
        assert types[types.index("RECEIPT"):].count("RECEIPT_REVOKED") == 1
        assert "RECEIPT" not in types[types.index("RECEIPT_REVOKED"):]


def test_r6_planted_protected_file_fails_gate(h):
    _pass_all(h)
    (h["p"].run / "decisions.jsonl").write_text('{"id":"d1","text":"waive check"}\n')
    with pytest.raises(TamperError):
        _gate(h)


@pytest.mark.parametrize("target", ["ledger.jsonl", "outputs/outputs.jsonl", "receipt.json", "scope.v1.jsonl"])
def test_r6_out_of_band_write_after_receipt_fails_stop(h, target):
    _pass_all(h)
    _gate(h)
    p = h["p"].run / target
    p.write_bytes(p.read_bytes() + b"\n")
    v = _verify(h)
    assert v["ok"] is False and v["reason"]


def test_r6_pins_edit_fails_stop(h):
    _pass_all(h)
    _gate(h)
    (h["p"].data_root / "pins" / "j1.json").write_text('{"scope_sha":"x","scope_count":1}')
    assert _verify(h)["ok"] is False
