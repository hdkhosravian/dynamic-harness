"""Typed commands: memory, status, export, config, off (spec §Detailed plan 9; build phase P5)."""
from __future__ import annotations

import csv
import json

from harness_core import contacts, enforcement, hookmain, memory, sessionstate, store
from test_business import confirm, typed
from test_p3_hooks import SID, compiled, ctx_of, h, post, say  # noqa: F401  (fixture h)


def test_memory_set_show_and_forget(h):
    assert "don't remember" in typed(h, "/dynamic-harness:memory show", "m1")
    shown = typed(h, "/dynamic-harness:memory set tone warm", "m2")
    assert memory.effective(ctx_of(h)) == {}
    confirm(h, "memory", shown, "m3")
    assert memory.effective(ctx_of(h)) == {"tone": "warm"}
    assert "- tone: warm (since" in typed(h, "/dynamic-harness:memory show", "m4")
    confirm(h, "memory", typed(h, "/dynamic-harness:memory set format short", "m5"), "m6")
    confirm(h, "memory", typed(h, "/dynamic-harness:memory forget tone", "m7"), "m8")
    assert memory.effective(ctx_of(h)) == {"format": "short"}
    confirm(h, "memory", typed(h, "/dynamic-harness:memory forget all", "m9"), "m10")
    assert memory.effective(ctx_of(h)) == {}


def test_memory_keeps_its_closed_schema(h):
    assert "I can remember only these" in typed(h, "/dynamic-harness:memory set tone https://x.example", "m1")
    assert "I can remember only these" in typed(h, "/dynamic-harness:memory set salary 100", "m2")


def test_status_lists_and_abandons_a_task(h):
    job = compiled(h)
    assert job in typed(h, "/dynamic-harness:status", "s1")
    shown = typed(h, f"/dynamic-harness:status --abandon {job}", "s2")
    assert sessionstate.jobs(ctx_of(h))[job]["state"] == "EXECUTING"
    assert "Closed" in confirm(h, "status", shown, "s3")
    row = sessionstate.jobs(ctx_of(h))[job]
    assert row["state"] == "ABANDONED" and row["terminal"] is True
    assert json.loads((h["data"] / "records" / f"{job}.jsonl").read_text().splitlines()[-1])["state"] == "ABANDONED"
    assert "already ended" in typed(h, f"/dynamic-harness:status --abandon {job}", "s4")


def test_repair_closes_only_a_tampered_task(h):
    job = compiled(h)
    assert "--repair closes a task stopped for tampering" in typed(h, f"/dynamic-harness:status --repair {job}", "r1")
    sessionstate.set_job(ctx_of(h), job, state="STOPPED_TAMPER")
    confirm(h, "status", typed(h, f"/dynamic-harness:status --repair {job}", "r2"), "r3")
    assert sessionstate.jobs(ctx_of(h))[job]["state"] == "ABANDONED"


def test_export_writes_a_csv_of_the_records(h):
    job = compiled(h)
    confirm(h, "status", typed(h, f"/dynamic-harness:status --abandon {job}", "e1"), "e2")
    out = typed(h, f"/dynamic-harness:export --task {job}", "e3")
    assert "Wrote 1 decision record rows" in out
    f = next((h["ws"] / "exports").glob("*.csv"))
    rows = list(csv.DictReader(f.open()))
    assert rows[0]["job_id"] == job and rows[0]["state"] == "ABANDONED" and rows[0]["checks"] == "u1=None"
    month = rows[0]["ts"][:7]
    assert "Wrote 1" in typed(h, f"/dynamic-harness:export --period {month}", "e4")
    assert "No decision records" in typed(h, "/dynamic-harness:export --period 1999-01", "e5")


def test_config_changes_a_control_value_and_rebaselines(h):
    shown = typed(h, "/dynamic-harness:config set tiers t1_max_units 7", "c1")
    assert "from 5 to 7" in shown
    confirm(h, "config", shown, "c2")
    from harness_core import protected
    assert protected.load_control("tiers.json", ctx_of(h))["t1_max_units"] == 7
    assert hookmain.main("SessionStart", {"session_id": "s2", "cwd": str(h["ws"]), "source": "startup",
                                          "permission_mode": "default"}, h["env"])[0] == 0
    status = json.loads((h["data"] / "sessions" / "s2" / "status.json").read_text())
    assert status["ok"] is True, status


def test_config_refuses_fixed_files_wrong_types_and_unknown_keys(h):
    assert "fixed in v1" in typed(h, "/dynamic-harness:config set classes x 1", "c1")
    assert "is a int" in typed(h, "/dynamic-harness:config set tiers t1_max_units many", "c2")
    assert "No key" in typed(h, "/dynamic-harness:config set tiers nope 1", "c3")


def test_off_puts_the_contact_cap_in_shadow_and_on_restores_it(h):
    job = compiled(h)
    from harness_core import contact
    for i in range(5):
        contact.apply({"tool": "contact", "type": "definition_confirm", "text": f"q{i}"}, ctx_of(h), {"session_id": SID})
    assert "cap" in (contacts.check("definition_confirm", ctx_of(h)) or "")
    confirm(h, "off", typed(h, "/dynamic-harness:off", "o1"), "o2")
    assert enforcement.mode("contacts", ctx_of(h)) == "shadow"
    assert contacts.check("definition_confirm", ctx_of(h)) is None
    log = store.session_log(h["data"])
    assert any(e["type"] == "SHADOW_WOULD_BLOCK" and e["payload"]["component"] == "contacts" for e in log.events())
    confirm(h, "off", typed(h, "/dynamic-harness:off on", "o3"), "o4")
    assert "cap" in (contacts.check("definition_confirm", ctx_of(h)) or "")
    assert job


def test_the_tripwire_in_shadow_mode_logs_and_lets_the_call_through(h):
    confirm(h, "off", typed(h, "/dynamic-harness:off", "o1"), "o2")
    say(h, "What is the capital of France?", "t1")
    code, out = post(h, "Write", file_path=str(h["ws"] / "x.txt"), content="x")
    assert code == 0 and not (out or {}).get("decision")
    assert sessionstate.load(ctx_of(h), SID).get("pending_card") is None
