"""Operations pack: documents and decision support (spec §Task taxonomy rows 3 and 7; build phase P5).

- ``doc_check`` (``doc.check``): a document in work/ has every required item, no placeholders left, the
  business profile's footer, and no recipient on the do-not-contact list. Its figures are traced by the gate
  (``numbers_traced`` scans every submitted text file).
- ``score_flip`` (``decide.score_flip``): the weighted score of each option recomputed by code, the options that
  break a constraint set aside, and the flip test: each weight moved ±20% (the rest renormalised); any move that
  changes the winner is reported, so the recommendation says what would change it. The criteria and weights are
  the owner's: a weight not confirmed by the owner is noted.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from . import localize as L

PLACEHOLDER = re.compile(
    r"\[(?:todo|tbd|name|date|amount|company|customer|address|x+|\.\.\.|…)\]|\{\{[^}]*\}\}|<(?:name|date|amount|"
    r"company|customer|insert[^>]*)>|\bxxx+\b|lorem ipsum|\?\?\?|_{4,}|\[(?:نام|تاریخ|مبلغ|شرکت|مشتری)\]",
    re.I)


def _q(v: Decimal, places=4) -> Decimal:
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def doc_check(req):
    a = req.get("args") or {}
    prof = (req.get("business") or {}).get("profile") or {}
    from .checklib import safe_path
    path = safe_path(req.get("work", "work"), a.get("document", "document.md"))
    text = path.read_text("utf-8")
    folded = L.fold_name(text)
    notes = []
    required = [str(r) for r in a.get("required") or []]
    if not required:
        notes.append("name the items the owner said the document must include (args.required)")
    missing = [r for r in required if L.fold_name(r) not in folded]
    if missing:
        notes.append(f"missing required items: {', '.join(missing)}")
    holes = sorted({m.group(0) for m in PLACEHOLDER.finditer(text)})
    if holes:
        notes.append(f"placeholders left: {', '.join(holes[:8])}")
    footer = a.get("footer", prof.get("footer"))
    if footer and L.fold_name(footer) not in L.fold_name(text[-max(len(footer) * 3, 400):]):
        notes.append("the required footer is not at the end of the document")
    blocked = [n for n in prof.get("do_not_contact") or [] if L.fold_name(n) and L.fold_name(n) in folded]
    if blocked:
        notes.append(f"the document names someone on the do-not-contact list: {', '.join(blocked)}")
    values = {"required_items": len(required), "required_found": len(required) - len(missing),
              "placeholders": len(holes)}
    return ("fail" if notes else "pass"), values, notes


def _scores(options, weights):
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights must add up to more than 0")
    out = {}
    for o in options:
        s = sum(Decimal(str(o["scores"].get(c, 0))) * w for c, w in weights.items()) / total
        out[o["id"]] = _q(s)
    return out


def _winner(scores):
    best = max(scores.values())
    tied = sorted(k for k, v in scores.items() if v == best)
    return tied[0] if len(tied) == 1 else None


def score_flip(req):
    a = req.get("args") or {}
    crit = a.get("criteria") or []
    options = a.get("options") or []
    if len(options) < 2 or not crit:
        raise ValueError("score_flip needs criteria [{id, weight}] and at least 2 options [{id, scores}]")
    weights = {c["id"]: Decimal(str(c["weight"])) for c in crit}
    notes = []
    unconfirmed = [c["id"] for c in crit if c.get("source") != "owner"]
    if unconfirmed:
        notes.append(f"weights not set by you: {', '.join(unconfirmed)} (the criteria and weights are yours)")
    ok, broken = [], {}
    for o in options:
        why = [f"{c['criterion']} {o['scores'].get(c['criterion'])} < {c['min']}" for c in a.get("constraints") or []
               if "min" in c and Decimal(str(o["scores"].get(c["criterion"], 0))) < Decimal(str(c["min"]))]
        why += [f"{c['criterion']} {o['scores'].get(c['criterion'])} > {c['max']}" for c in a.get("constraints") or []
                if "max" in c and Decimal(str(o["scores"].get(c["criterion"], 0))) > Decimal(str(c["max"]))]
        (broken.__setitem__(o["id"], why) if why else ok.append(o))
    for oid, why in broken.items():
        notes.append(f"{oid} breaks a constraint: {'; '.join(why)}")
    if not ok:
        return "fail", {"options": len(options), "eligible": 0}, notes + ["no option meets every constraint"]
    scores = _scores(ok, weights)
    win = _winner(scores)
    flips = []
    for c in weights:
        for f in (Decimal("0.8"), Decimal("1.2")):
            w = dict(weights, **{c: weights[c] * f})
            alt = _winner(_scores(ok, w))
            if alt != win:
                flips.append(f"{c} {'-' if f < 1 else '+'}20% -> {alt or 'a tie'}")
    values = {"scores": {k: float(v) for k, v in scores.items()}, "winner": win, "flips": flips,
              "eligible": len(ok), "options": len(options)}
    if flips:
        notes.append("the recommendation changes if: " + "; ".join(flips))
    if win is None:
        notes.append("two options tie: there is no single recommendation")
        return "fail", values, notes
    return "pass", values, notes
