"""Task card: draft validation, normalisation and merge (spec §Task card; build phase P3).

- ``validate(draft) -> [error]``: the model's draft against ``schema/run/card_draft.json``, plus the
  rules a subset schema cannot state (``unit_list`` length). Code-owned fields (``job_id``,
  ``reversibility``, ``versions``…) are rejected as unknown properties.
- ``normalize(draft, probe, ctx, request) -> card``: applies the field rules.
  - Set by code from the probe and the class table: ``irreversible``, ``reversibility``, ``data_classes``,
    ``personal_data``, ``computed_numbers``. The draft may raise these, never lower them.
  - Max-merged with the probe: ``decision_class`` (by policy severity).
  - Risk: high when ``cost_of_error`` passes a business-profile threshold; at least medium when its
    provenance is not ``user`` (conservative default for inferred fields). Reliance becomes ``money``
    when a money figure appears and the owner did not state it.
  - A single-unit card gets one scope row from ``unit_definition`` and the deliverable.
  ``job_id``, ``version``, ``versions``, ``follows``, ``scenario_of`` and ``multi_session`` are left to
  ``compile.apply``.
- ``merge(a, b) -> (draft, questions)``: risk takes the maximum, oracle criteria the union, and a
  disagreement on ``request`` or ``deliverable`` becomes one start question.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from . import protected, schema, tier_select
from .errors import HarnessError, SchemaError

RISK = ("low", "medium", "high")
REV = ("R0", "R1", "R2", "R3")


class CardError(HarnessError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


def validate(draft) -> list:
    if not isinstance(draft, dict):
        return ["the card draft must be a JSON object"]
    errors = []
    try:
        schema.check(draft, "card_draft")
    except SchemaError as e:
        errors.append(str(e))
    ul = draft.get("unit_list")
    units = draft.get("units")
    if ul is not None and isinstance(units, int) and isinstance(ul, list) and len(ul) != units:
        errors.append(f"unit_list has {len(ul)} rows but units = {units}")
    if ul is None and isinstance(units, int) and units > 1:
        errors.append("unit_list is required when units > 1 (one row per unit: label and claim)")
    return errors


def _class_table(ctx):
    return protected.load_control("classes.json", ctx).get("classes", {})


def _business(ctx) -> dict:
    f = Path(ctx.data_root) / "business" / "profile.json" if getattr(ctx, "data_root", None) else None
    try:
        v = json.loads(f.read_text("utf-8")) if f and f.is_file() else {}
    except (OSError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _max_class(*classes, ctx=None) -> str:
    rank = tier_select.POLICY_RANK
    known = [c for c in classes if isinstance(c, str)]
    return max(known, key=lambda c: (rank.index(tier_select.policy(c, ctx)) if c != "none" else -1)) if known else "none"


def normalize(draft, probe, ctx, request) -> dict:
    errors = validate(draft)
    if errors:
        raise CardError(errors)
    c = copy.deepcopy(draft)
    prov = c.get("provenance") or {}
    c["provenance"] = prov
    table = _class_table(ctx)
    classes = list(probe.get("irreversible_classes") or [])
    irreversible = bool(classes) or c.get("irreversible") is True
    rev = [table.get(k, {}).get("reversibility", "R3") for k in classes]
    reversibility = max(rev, key=REV.index) if rev else "R0"
    if irreversible and reversibility == "R0":
        reversibility = "R3"  # raised by the draft without a class: the conservative reading
    c["irreversible"], c["reversibility"] = irreversible, reversibility
    c["data_classes"] = sorted(set(probe.get("data_classes") or []) | set(c.get("data_classes") or []))
    pd = c.get("personal_data") or {}
    c["personal_data"] = {"categories": c["data_classes"],
                          "subjects_count": pd.get("subjects_count", 0) if isinstance(pd, dict) else 0}
    c["computed_numbers"] = bool(probe.get("quantitative")) or c.get("computed_numbers") is True
    c["decision_class"] = _max_class(c["decision_class"], *(probe.get("decision_hits") or {}), ctx=ctx)
    # risk: thresholds, then the conservative default for anything the owner did not state
    level = c["risk"]["level"]
    th = (_business(ctx).get("thresholds") or {})
    coe = c["cost_of_error"]
    amount = coe["money_at_stake"]["amount"]
    if (isinstance(th.get("money_at_stake_high"), (int, float)) and amount > th["money_at_stake_high"]) or \
            (isinstance(th.get("recipient_count_high"), int) and coe.get("recipient_count", 0) > th["recipient_count_high"]):
        level = "high"
    if prov.get("risk") != "user":
        level = max(level, "medium", key=RISK.index)
    c["risk"] = {"level": level}
    if prov.get("reliance") != "user" and probe.get("money_tokens"):
        c["reliance"] = "money"
    if not c.get("unit_list"):
        c["unit_list"] = [{"label": c["unit_definition"], "claim": c["deliverable"]["description"]}]
    c["schema"] = "harness/card@1"
    c["request"] = request if isinstance(request, str) else ""
    c.setdefault("resume_of", None)
    return c


def merge(a: dict, b: dict):
    """Deterministic merge of two drafts: (merged draft, start questions)."""
    m = copy.deepcopy(a)
    questions = []
    m["risk"] = {"level": max(a["risk"]["level"], b["risk"]["level"], key=RISK.index)}
    crit = list(a["oracle"].get("criteria") or [])
    crit += [x for x in b["oracle"].get("criteria") or [] if x not in crit]
    m["oracle"] = dict(a["oracle"], criteria=crit)
    if json.dumps(a.get("deliverable"), sort_keys=True) != json.dumps(b.get("deliverable"), sort_keys=True):
        questions.append(f"Two readings of the deliverable: {a['deliverable'].get('description')!r} or "
                         f"{b['deliverable'].get('description')!r}?")
    return m, questions
