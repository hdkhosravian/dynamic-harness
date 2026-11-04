"""Tier selection, by code (spec §Tier selection (code); build phase P3). Called only by ``compile.apply``.

``select_tier(card, probe, control=None, stored_tier=None) -> "T0" | "T1" | "T2" | "REFUSE"`` follows the
spec's pseudocode line by line. ``stored_tier(job) -> tier | None`` reads the tier of a parent job for a
resume or scenario run; an unknown parent tier counts as T2, so it never comes out lower.

Fail closed: a field of the wrong type is read as its conservative value (``units`` = many, risk high,
irreversible, oracle not executable), and an unknown decision class gets the ``signoff`` policy.
"""
from __future__ import annotations

from . import protected

ORDER = ("T0", "T1", "T2")
REFUSE = "REFUSE"
POLICY_RANK = ("none", "inform", "signoff", "refuse")


def tier_max(*tiers) -> str:
    return max(tiers, key=ORDER.index)


def _control(control):
    c = dict(protected.load_control("tiers.json"))
    c.update(control or {})
    return c


def policy(decision_class, ctx=None) -> str:
    pols = protected.load_control("decision_policies.json", ctx).get("policies", {})
    p = pols.get(decision_class) if isinstance(decision_class, str) else None
    return p if p in POLICY_RANK else "signoff"


def _units(card):
    u = card.get("units")
    return u if isinstance(u, int) and not isinstance(u, bool) and u >= 1 else 10 ** 6


def _risk(card):
    r = card.get("risk")
    level = r.get("level") if isinstance(r, dict) else None
    return level if level in ("low", "medium", "high") else "high"


def _flag(card, key, safe):
    v = card.get(key)
    return v if isinstance(v, bool) else safe


def _executable(card):
    o = card.get("oracle")
    return isinstance(o, dict) and o.get("executable") is True


def select_tier(card, probe, control=None, stored_tier=None, ctx=None) -> str:
    c = _control(control)
    floor = probe.get("floor_tier") if probe.get("floor_tier") in ORDER else "T2"
    pol = policy(card.get("decision_class"), ctx)
    if card.get("decision_class") != "none" and pol == "refuse":
        return REFUSE
    irreversible = _flag(card, "irreversible", True)
    risk = _risk(card)

    def parent(job):
        t = stored_tier(job) if stored_tier else None
        return t if t in ORDER else "T2"

    if card.get("scenario_of"):  # scenario run (§Business simulations)
        hi = "T2" if (risk == "high" or irreversible or pol == "signoff") else "T1"
        return tier_max(hi, parent(card["scenario_of"]), floor)
    if card.get("resume_of"):
        return tier_max(parent(card["resume_of"]), floor)  # never lower
    write_targets = probe.get("write_targets")
    if (probe.get("question_only") is True and not write_targets and risk == "low"
            and card.get("reliance") == "info" and _flag(card, "computed_numbers", True) is False
            and pol in ("none", "inform")):
        tier = "T0"  # general knowledge, style, quick lookup
    elif (irreversible or risk == "high" or pol == "signoff" or _units(card) > int(c["t1_max_units"])
          or _flag(card, "multi_session", True)):
        tier = "T2"
    elif _executable(card):  # pack-declared business invariants count
        tier = "T1"  # multi-unit single-session work: T1 + frozen scope
    else:
        tier = "T2"
    return tier_max(tier, floor)
