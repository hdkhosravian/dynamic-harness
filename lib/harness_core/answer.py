"""``harness-answer header --unit U --output SHA``: the answer header (R-12; spec §Business questions "Answer
header"; build phase P5).

The writer renders ``run/answer_header.md`` from a recorded check output and the business layer, never from the
model's wording:

1. the figure, the metric (dictionary id and version), the period in the owner's calendar plus Gregorian, the
   unit and currency;
2. the basis: the source and the date its data runs to, the exclusions (the confirmed definition's, plus
   own-account transfers and left-out rows the check counted) and the row count;
3. the check status in plain words, from the check's own result.

A metric with no confirmed definition is refused: it becomes a ``definition_confirm`` contact first. The source
is registered in the source registry with the facts the check computed (as-of date, coverage, unit, sha256).
The gate includes the header in ``REPORT.md`` (``--include``), traces its numbers, and lets its status line, and
only that line, use state words.
"""
from __future__ import annotations

import re
from decimal import Decimal

from . import business, canon, store, templates
from .errors import Denied
from .events import EventLog
from .ledger import Ledger, Outputs
from .paths import job_paths
from .safeio import atomic_write, safe_open

FILE = "answer_header.md"
HEADER_CHECKS = ("finance.reconcile",)


def _clean(text) -> str:
    """Model-given labels lose state words, so the status line's exemption cannot carry extra claims."""
    from .stopgate import lexicon_hits
    t = " ".join(str(text or "").split())[:80]
    for w in lexicon_hits(None, t):
        t = re.sub(re.escape(w), "", t, flags=re.I)
    return " ".join(t.split()) or "the control source"


def _figure(v) -> str:
    d = Decimal(str(v))
    return f"{int(d):,}" if d == d.to_integral_value() else f"{d:,}"


def status_line(paths, log):
    """The header's status line, read back from the recorded file (None when there is no header)."""
    f = paths.run / FILE
    if not f.exists():
        return None
    ev = [e for e in log.events() if e.get("file") == FILE]
    data = safe_open(f, [paths.run])
    if not ev or ev[-1].get("file_sha") != canon.sha256_bytes(data):
        return None
    lines = data.decode("utf-8").splitlines()
    return lines[2] if len(lines) >= 3 else None


def apply(req, ctx, evt) -> dict:
    job = getattr(ctx, "job", None)
    if not job:
        raise Denied("an answer header belongs to a task: there is no open task")
    if req.get("sub") != "header":
        raise Denied("harness-answer supports only: header")
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    unit = next((u for u in Ledger(p, log).sanctioned_scope() if u["id"] == req.get("unit")), None)
    if unit is None:
        raise Denied(f"unit {req.get('unit')!r} is not in the frozen scope")
    out = next((o for o in Outputs(p, log).rows() if o["sha256"] == req.get("output")), None)
    if out is None or out["unit"] != unit["id"]:
        raise Denied(f"output {req.get('output')!r} is not a recorded output of {unit['id']}")
    if out["check_id"] not in HEADER_CHECKS or out["result"] not in ("pass", "fail"):
        raise Denied(f"an answer header needs a clean run of {', '.join(HEADER_CHECKS)}")
    v = out["values"]
    metric_id = v.get("metric_id")
    entry = business.dictionary(ctx).get(metric_id) if metric_id else None
    if not entry:
        raise Denied(f"metric {metric_id!r} has no confirmed definition: confirm it with harness-contact "
                     "definition_confirm --metric FILE, then re-run the check with metric_id")
    if v.get("metric_version") != entry.get("version"):
        raise Denied(f"the check used version {v.get('metric_version')} of {metric_id}; the dictionary has "
                     f"version {entry.get('version')}: re-run the check")
    prof = business.profile(ctx)
    lang = templates.language(ctx)
    unit_name = templates.currency(v.get("unit"), lang)
    calendar = prof.get("calendar") or ("jalali" if v.get("unit") in ("IRT", "IRR") else "gregorian")
    period = v.get("period") or ""
    other = v.get("period_gregorian") if calendar == "jalali" else v.get("period_jalali")
    if lang == "fa":
        from .localize import label_fa
        period, other = label_fa(period), label_fa(other or "")
    excl = [entry.get("exclusions")] if entry.get("exclusions") else []
    if v.get("transfers_excluded"):
        excl.append(templates.render("header.transfers", ctx, lang, n=v["transfers_excluded"]))
    for k in ("rows_left_out_undated", "rows_left_out_unparsable"):
        if v.get(k):
            excl.append(templates.render(f"header.{k}", ctx, lang, n=v[k]))
    src = out["inputs"][0]["source"] if out.get("inputs") else "the file you gave"
    line1 = templates.render("header.figure", ctx, lang, figure=_figure(v["total"]), unit=unit_name,
                             metric=metric_id, version=entry["version"], period=period, other=other or "")
    line2 = templates.render("header.basis", ctx, lang, source=src, as_of=v.get("data_end_jalali" if calendar ==
                             "jalali" else "data_end") or "unknown", exclusions="; ".join(excl) or "nothing",
                             rows=v.get("rows_used", 0))
    control = (out.get("values") or {}).get("control_total")
    label = _clean(((req.get("control_label") or "") if control is not None else "") or "the control source")
    if out["result"] == "pass":
        line3 = templates.render("header.checked_control" if control is not None else "header.checked", ctx, lang,
                                 control=label)
    else:
        reason = (out.get("notes") or ["a check did not pass"])[0]
        line3 = templates.render("header.not_checked", ctx, lang, reason=reason.rstrip("."))
        card = store.read_json(p.run, log, "card.v1.json", None) or {}
        if card.get("decision_class") in ("financial", "tax"):
            line3 += " " + templates.render("header.not_for_tax", ctx, lang)
    text = "\n".join([line1, line2, line3]) + "\n"
    data = text.encode("utf-8")
    with log.lock():
        atomic_write(p.run / FILE, data)
        log.append("ANSWER_HEADER", {"unit": unit["id"], "output": out["sha256"], "metric_id": metric_id,
                                     "version": entry["version"]}, file=FILE, file_sha=canon.sha256_bytes(data))
    if out.get("inputs"):
        business.register_source(ctx, src, {"role": "primary", "as_of": v.get("data_end"),
                                            "coverage_min": v.get("data_start"), "coverage_max": v.get("data_end"),
                                            "unit": v.get("unit"), "sha256": out["inputs"][0]["sha256"]})
    return {"header": text, "unit": unit["id"]}
