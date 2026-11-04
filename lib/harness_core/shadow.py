"""The second card draft in shadow mode (spec §Where the model decides: "Second card draft … shadow mode
first"; build phase P3).

``log_second_draft(ctx, job, first, second, probe)`` records, without changing the compiled card, what tier
each draft would get, what the deterministic merge would get, and which fields differ. Rows go to the
protected ``runs/<job>/shadow.jsonl`` (a ``SHADOW`` event). An invalid second draft is logged with its
errors. The data shows whether the second draft is worth enforcing later.
"""
from __future__ import annotations

import json

from . import card, store, tier_select
from .events import EventLog, now
from .paths import job_paths

FILE = "shadow.jsonl"
FIELDS = ("task_type", "deliverable", "oracle", "reliance", "decision_class", "cost_of_error", "risk", "units",
          "unit_definition", "domain")


def _tier(draft, probe, ctx):
    c = card.normalize(draft, probe, ctx, request="")
    c["multi_session"] = card.multi_session(c)  # as compile.apply sets it
    return tier_select.select_tier(c, probe, ctx=ctx)


def log_second_draft(ctx, job, first, second, probe) -> dict:
    t1 = _tier(first, probe, ctx)
    errors = card.validate(second)
    t2 = merged = None
    fields = []
    if not errors:
        t2 = _tier(second, probe, ctx)
        merged = _tier(card.merge(first, second)[0], probe, ctx)
        fields = [f for f in FIELDS if json.dumps(first.get(f), sort_keys=True) != json.dumps(second.get(f), sort_keys=True)]
    row = {"schema": "harness/shadow@1", "ts": now(), "tier_first": t1, "tier_second": t2, "tier_merged": merged,
           "agree": not errors and not fields, "fields": fields, "second_errors": errors}
    p = job_paths(ctx.data_root, ctx.workspace, job)
    store.append_jsonl(p.run, EventLog(p.run, p.heads), FILE, row, "SHADOW", {"agree": row["agree"]})
    return row


def rows(ctx, job) -> list:
    p = job_paths(ctx.data_root, ctx.workspace, job)
    return store.read_jsonl(p.run, EventLog(p.run, p.heads), FILE)
