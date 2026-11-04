"""Decision record (R-15; spec §Business governance "Decision record")."""
from __future__ import annotations

import json
import shutil

from harness_core import compile as compile_, record, store
from harness_core.paths import job_paths
from test_p3_hooks import CARD, SID, compiled, ctx_of, h, say  # noqa: F401  (fixture h)


def rows(h, job):
    c = ctx_of(h)
    return store.read_jsonl(c.data_root, store.session_log(c.data_root), f"records/{job}.jsonl")


def test_a_record_holds_the_task_facts_without_personal_data(h):
    job = compiled(h, text="how many orders did sara@shop.example place this week?")
    r = record.write(ctx_of(h), job, "DONE")
    assert r["state"] == "DONE" and r["interim"] is False and r["job_id"] == job
    assert "sara@shop.example" not in json.dumps(r) and r["request_sha"]
    assert r["cards"][0]["task_type"] == "data_question" and r["tier"] == "T1"
    assert r["checks"] == [{"unit": "u1", "label": "the answer", "verdict": None, "check_id": "finance.reconcile"}]
    assert r["versions"]["core"] and "cost" in r


def test_inferred_fields_are_listed_as_assumptions(h):
    job = compiled(h, card=dict(CARD, provenance={"risk": "inferred", "reliance": "user"}))
    assert {"field": "risk", "value": {"level": "medium"}} in record.write(ctx_of(h), job, "DONE")["assumptions"]


def test_records_are_append_only_and_survive_deleting_the_job(h):
    job = compiled(h)
    record.write(ctx_of(h), job, "PAUSED", interim=True)
    record.write(ctx_of(h), job, "DONE")
    assert [(r["state"], r["interim"]) for r in rows(h, job)] == [("PAUSED", True), ("DONE", False)]
    shutil.rmtree(job_paths(h["data"], h["ws"], job).run)
    assert len(rows(h, job)) == 2


def test_a_refused_task_gets_its_record(h):
    say(h, "what dose of this medicine should I take", "p1")
    out = compile_.apply({"tool": "compile", "card": dict(CARD, decision_class="medical")}, ctx_of(h), {"session_id": SID})
    assert rows(h, out["job_id"])[-1]["state"] == "REFUSED"
