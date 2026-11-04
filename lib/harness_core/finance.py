"""Finance pack checks (spec §Business questions "Oracles by answer type"; R-11; build phase P5).

Every aggregate is computed here, in Decimal, never by the model. Each check returns ``(result, values,
notes)`` for ``checklib``; a gap the owner must know about (left-out rows, data that ends early, sources that
disagree, a ×10 jump, own-account transfers) is a note, and every such gap makes the result ``fail``, so the
deliverable can at best be "Done, not fully checked: <gap>".

- ``reconcile``: one aggregate over a period; second path (the file's own total row, else monthly subtotals);
  control total from a second source; coverage; own-account transfers excluded and counted; magnitude check
  against the prior period.
- ``kpi``: ``reconcile`` per KPI, plus a sanity bound against the prior period.
- ``bridge``: price, volume and new/lost-item effects must add up to the total change.
- ``list``: a delivered list's row count and sum equal the filtered aggregate, and every row has an ID and date.
"""
from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from . import localize as L
from . import tables

AMOUNT_WORDS = ("amount", "total", "sum", "value", "مبلغ", "جمع", "مقدار", "بستانکار", "واریز", "price")
DATE_WORDS = ("date", "تاریخ", "day", "روز")
DESC_WORDS = ("description", "desc", "memo", "note", "شرح", "توضیح", "بابت")
ACCOUNT_WORDS = ("account", "counterparty", "حساب", "طرف حساب")
TOTAL_LABELS = ("total", "grand total", "sum", "جمع", "جمع کل", "مجموع", "total amount")
TRANSFER_WORDS = ("transfer between", "own account", "internal transfer", "انتقال بین حساب", "انتقال وجه بین",
                  "حواله داخلی", "انتقال به حساب خود", "transfer to own", "between own accounts")
JUMP = Decimal(8)  # a ×10 unit slip (Toman/Rial) shows as a ratio of 8 or more against the prior period


def _find(headers, want, words):
    folded = [L.fold_name(h) for h in headers]
    if want is not None:
        if isinstance(want, int) and 0 <= want < len(headers):
            return want
        w = L.fold_name(str(want))
        for i, h in enumerate(folded):
            if h == w:
                return i
        for i, h in enumerate(folded):
            if w and w in h:
                return i
        raise ValueError(f"no column named {want!r}; columns are {headers}")
    for i, h in enumerate(folded):
        if any(L.fold_name(x) in h for x in words):
            return i
    return None


def _date(v):
    return tables.excel_date(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else L.parse_date(v)


def _round(d: Decimal, places=2) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _display(total: Decimal, unit) -> dict:
    """Forms a report may print: the figure, in thousands and millions, and in the other unit (Toman/Rial)."""
    out = {"total": total, "total_thousands": _round(total / 1000), "total_millions": _round(total / 1000000),
           "total_billions": _round(total / 1000000000, 3)}
    other = {"IRT": "IRR", "IRR": "IRT"}.get(unit)
    if other:
        o = L.to_unit(total, unit, other)
        out.update(total_other_unit=o, total_other_unit_millions=_round(o / 1000000))
    return out


def _is_total_row(row) -> bool:
    return any(isinstance(v, str) and L.fold_name(v) in TOTAL_LABELS for v in row)


def _transfer(row, desc_i, acct_i, own) -> bool:
    if desc_i is not None and isinstance(row[desc_i], str):
        d = L.fold_name(row[desc_i])
        if any(L.fold_name(w) in d for w in TRANSFER_WORDS):
            return True
    if acct_i is not None and row[acct_i] not in (None, ""):
        return L.fold_name(str(row[acct_i])) in own
    return False


def _match(row, headers, filters) -> bool:
    for col, want in (filters or {}).items():
        i = _find(headers, col, ())
        vals = want if isinstance(want, list) else [want]
        if L.fold_name(str(row[i] if row[i] is not None else "")) not in {L.fold_name(str(x)) for x in vals}:
            return False
    return True


def aggregate(path, args, profile, period=None) -> dict:
    """Sum of one amount column over one period, with every row accounted for."""
    headers, rows = tables.read(path)
    if not headers:
        raise ValueError(f"{path}: empty table")
    amt_i = _find(headers, args.get("amount_col"), AMOUNT_WORDS)
    date_i = _find(headers, args.get("date_col"), DATE_WORDS)
    if amt_i is None:
        raise ValueError(f"no amount column found in {headers}; pass amount_col")
    if period is not None and date_i is None:
        raise ValueError(f"a period needs a date column; none found in {headers} (pass date_col)")
    desc_i = _find(headers, args.get("desc_col"), DESC_WORDS)
    acct_i = _find(headers, args.get("account_col"), ACCOUNT_WORDS)
    target = args.get("currency") or profile.get("currency")
    unit = args.get("unit") or L.currency_of(headers[amt_i]) or target
    own = {L.fold_name(str(a)) for a in (args.get("own_accounts") or profile.get("own_accounts") or [])}
    exclude_transfers = args.get("exclude_transfers", True)
    per = period
    s = {"rows_total": 0, "rows_used": 0, "undated": 0, "unparsable": 0, "outside_period": 0, "filtered_out": 0,
         "transfers": 0, "total_rows_in_file": 0}
    total, transfers_amount, file_total = Decimal(0), Decimal(0), None
    by_month, dates, prior, kept, seen = {}, [], Decimal(0), [], {}
    prior_rows = 0
    span = (per["end"] - per["start"]) if per else None
    for row in rows:
        if _is_total_row(row):
            v = L.parse_amount(row[amt_i])
            if v is not None:
                file_total = L.to_unit(v, unit, target)
                s["total_rows_in_file"] += 1
            continue
        s["rows_total"] += 1
        amount = L.parse_amount(row[amt_i])
        if amount is None:
            s["unparsable"] += 1
            continue
        amount = L.to_unit(amount, unit, target)
        d = _date(row[date_i]) if date_i is not None else None
        if date_i is not None and d is None:
            s["undated"] += 1
            continue
        if d is not None:
            dates.append(d)
        if not _match(row, headers, args.get("filters")):
            s["filtered_out"] += 1
            continue
        if exclude_transfers and _transfer(row, desc_i, acct_i, own):
            s["transfers"] += 1
            transfers_amount += amount
            continue
        if per and d is not None and not per["start"] <= d <= per["end"]:
            if per["start"] - span - dt.timedelta(days=1) <= d < per["start"]:
                prior += amount
                prior_rows += 1
            s["outside_period"] += 1
            continue
        key = (d, amount, L.fold_name(str(row[desc_i])) if desc_i is not None and row[desc_i] else "")
        seen[key] = seen.get(key, 0) + 1
        total += amount
        s["rows_used"] += 1
        if d is not None:
            by_month[L.to_jalali(d)[:2]] = by_month.get(L.to_jalali(d)[:2], Decimal(0)) + amount
        kept.append(row)
    return {"total": total, "stats": s, "unit_in": unit, "unit": target, "file_total": file_total,
            "monthly_sum": sum(by_month.values(), Decimal(0)), "transfers_amount": transfers_amount,
            "data_start": min(dates) if dates else None, "data_end": max(dates) if dates else None,
            "prior": prior if prior_rows else None, "duplicates": sum(n - 1 for n in seen.values() if n > 1),
            "headers": headers, "kept": kept}


def period_years(per) -> list:
    """The years a report may name for the period (plain years count as numbers for the gate's trace)."""
    days = (per["start"], per["end"])
    return sorted({d.year for d in days} | {L.to_jalali(d)[0] for d in days})


def _input(req, n):
    ins = req.get("inputs") or []
    if not 1 <= n <= len(ins):
        raise ValueError(f"input {n} is missing: pass it with --input")
    return ins[n - 1]["path"]


def reconcile(req):
    a = req.get("args") or {}
    profile = (req.get("business") or {}).get("profile") or {}
    per = L.period(a["period"], profile) if a.get("period") else None
    agg = aggregate(_input(req, int(a.get("input", 1))), a, profile, per)
    st, notes, fail = agg["stats"], [], False
    total = agg["total"]
    values = {"total": total, "rows_used": st["rows_used"], "rows_in_file": st["rows_total"],
              "unit": agg["unit"], **{k: v for k, v in _display(total, agg["unit"]).items() if k != "total"}}
    if a.get("metric_id"):
        m = ((req.get("business") or {}).get("dictionary") or {}).get(a["metric_id"])
        values.update(metric_id=a["metric_id"], metric_version=(m or {}).get("version"))
        if not m:
            notes.append(f"metric {a['metric_id']} has no confirmed definition yet")
            fail = True
    if per:
        values.update(period=per["label"], period_gregorian=per["label_gregorian"],
                      period_jalali=per["label_jalali"], period_years=period_years(per))
    if agg["unit_in"] != agg["unit"] and agg["unit_in"] and agg["unit"]:
        notes.append(f"amounts were in {agg['unit_in']} and are shown in {agg['unit']}")
    for k, what in (("undated", "no date"), ("unparsable", "an amount that could not be read")):
        if st[k]:
            values[f"rows_left_out_{k}"] = st[k]
            notes.append(f"left out: {st[k]} rows with {what}")
            fail = True
    if st["transfers"]:
        values.update(transfers_excluded=st["transfers"], transfers_amount=agg["transfers_amount"])
        notes.append(f"{st['transfers']} transfers between your own accounts were excluded")
    if agg["duplicates"]:
        values["possible_duplicates"] = agg["duplicates"]
        notes.append(f"{agg['duplicates']} rows look like duplicates (same date, amount and description)")
    if agg["data_end"]:
        values.update(data_start=agg["data_start"].isoformat(), data_end=agg["data_end"].isoformat(),
                      data_end_jalali=L.jalali_str(agg["data_end"]))
    if per and (agg["data_end"] is None or agg["data_end"] < per["end"]):
        notes.append(f"incomplete: the data ends {L.jalali_str(agg['data_end']) if agg['data_end'] else 'before the period'}"
                     f" ({agg['data_end'].isoformat() if agg['data_end'] else 'no dated rows'}), before the period ends")
        fail = True
    if per and agg["data_start"] and agg["data_start"] > per["start"]:
        notes.append(f"incomplete: the data starts {L.jalali_str(agg['data_start'])}, after the period starts")
        fail = True
    if st["rows_used"] == 0:
        notes.append("no rows fall in the period")
        fail = True
    # second computation path
    if agg["file_total"] is not None and not per and not a.get("filters"):
        values["file_total_row"] = agg["file_total"]
        if agg["file_total"] != total + agg["transfers_amount"]:
            notes.append(f"the file's own total row says {agg['file_total']}, the rows add up to "
                         f"{total + agg['transfers_amount']}")
            fail = True
    elif agg["monthly_sum"] != total and agg["data_end"] is not None:
        notes.append("monthly subtotals do not add up to the total")
        fail = True
    if agg["prior"] is not None:
        values["prior_period_total"] = agg["prior"]
        lo, hi = sorted((abs(total), abs(agg["prior"])))
        if lo and hi / lo >= JUMP:
            notes.append(f"the total is {_round(hi / lo, 1)} times the prior period's: check for a Toman/Rial "
                         "mix-up or a missing zero")
            fail = True
    control = a.get("control")
    if control:
        c = aggregate(_input(req, int(control.get("input", 2))), dict(control, currency=agg["unit"]), profile, per)
        tol = Decimal(str(control.get("tolerance", 0)))
        gap = total - c["total"]
        values.update(control_total=c["total"], gap=gap, control_rows_used=c["stats"]["rows_used"])
        if abs(gap) > tol:
            label = control.get("label") or "the control source"
            notes.append(f"sources disagree: this file says {total}, {label} says {c['total']}; the gap of "
                         f"{abs(gap)} is not explained")
            fail = True
    return ("fail" if fail else "pass"), values, notes


def kpi(req):
    """Each KPI: ``reconcile`` (two paths, coverage) plus a bound on the change against the prior period."""
    a = req.get("args") or {}
    out, notes, fail = {}, [], False
    for k in a.get("kpis") or []:
        sub = dict(req, args=dict({x: y for x, y in a.items() if x != "kpis"}, **k))
        res, vals, n = reconcile(sub)
        out[k["id"]] = vals
        notes += [f"{k['id']}: {x}" for x in n]
        fail |= res == "fail"
        bound = Decimal(str(k.get("max_change", a.get("max_change", 0.5))))
        prior = vals.get("prior_period_total")
        if prior:
            change = (Decimal(vals["total"]) - Decimal(prior)) / abs(Decimal(prior))
            vals["change_vs_prior"] = _round(change * 100, 1)
            if abs(change) > bound:
                notes.append(f"{k['id']}: changed {_round(change * 100, 1)}% against the prior period, beyond the "
                             f"{_round(bound * 100, 0)}% sanity bound")
                fail = True
    if not out:
        raise ValueError("kpi needs args.kpis: [{id, amount_col, filters, period}]")
    return ("fail" if fail else "pass"), {"kpis": out}, notes


def bridge(req):
    """Price/volume/new/lost effects of the change between two periods; they must sum to the change."""
    a = req.get("args") or {}
    cur, pri = (tables.read(_input(req, n)) for n in (int(a.get("current", 1)), int(a.get("prior", 2))))

    def items(t):
        h, rows = t
        ii, qi, pi = _find(h, a.get("item_col"), ("item", "product", "کالا")), _find(h, a.get("qty_col"), ("qty", "quantity", "تعداد")), _find(h, a.get("price_col"), ("price", "rate", "قیمت"))
        if None in (ii, qi, pi):
            raise ValueError(f"bridge needs item, qty and price columns; got {h}")
        out = {}
        for r in rows:
            q, p = L.parse_amount(r[qi]), L.parse_amount(r[pi])
            if q is None or p is None:
                continue
            k = L.fold_name(str(r[ii]))
            q0, v0 = out.get(k, (Decimal(0), Decimal(0)))
            out[k] = (q0 + q, v0 + q * p)
        return out
    c, p = items(cur), items(pri)
    price = volume = new = lost = Decimal(0)
    for k in set(c) | set(p):
        if k in c and k in p and c[k][0] and p[k][0]:
            q1, p1 = c[k][0], c[k][1] / c[k][0]
            q0, p0 = p[k][0], p[k][1] / p[k][0]
            price += (p1 - p0) * q1
            volume += (q1 - q0) * p0
        elif k in c:
            new += c[k][1]
        else:
            lost -= p[k][1]
    change = sum(v for _, v in c.values()) - sum(v for _, v in p.values())
    residual = change - (price + volume + new + lost)
    tol = Decimal(str(a.get("tolerance", "0.01"))) * max(abs(change), Decimal(1))
    values = {"change": change, "price_effect": _round(price), "volume_effect": _round(volume),
              "new_items_effect": new, "lost_items_effect": lost, "residual": _round(residual)}
    if abs(residual) > tol:
        return "fail", values, [f"the effects leave {residual} unexplained: say 'possible reason', not 'because'"]
    return "pass", values, []


def listing(req):
    """A delivered list (CSV in work/) against the filtered aggregate of the source."""
    a = req.get("args") or {}
    profile = (req.get("business") or {}).get("profile") or {}
    per = L.period(a["period"], profile) if a.get("period") else None
    src = aggregate(_input(req, int(a.get("input", 1))), a, profile, per)
    path = f"{req.get('work', 'work')}/{a['list']}"
    lh, lrows = tables.read(path)
    amt_i = _find(lh, a.get("list_amount_col") or a.get("amount_col"), AMOUNT_WORDS)
    id_i = _find(lh, a.get("id_col"), ("id", "invoice", "number", "شماره", "کد"))
    date_i = _find(lh, a.get("list_date_col") or a.get("date_col"), DATE_WORDS)
    notes, fail = [], False
    total = Decimal(0)
    for r in lrows:
        if _is_total_row(r):
            continue
        v = L.parse_amount(r[amt_i]) if amt_i is not None else None
        total += v or 0
        if id_i is None or r[id_i] in (None, "") or date_i is None or _date(r[date_i]) is None:
            fail = True
    if fail:
        notes.append("every listed row needs its ID and date")
    n = sum(1 for r in lrows if not _is_total_row(r))
    values = {"list_rows": n, "list_total": total, "source_rows": src["stats"]["rows_used"],
              "source_total": src["total"]}
    if n != src["stats"]["rows_used"] or total != src["total"]:
        notes.append(f"the list has {n} rows totalling {total}; the source has {src['stats']['rows_used']} rows "
                     f"totalling {src['total']}")
        fail = True
    return ("fail" if fail else "pass"), values, notes
