"""The ``model-checks`` pack (spec §Business simulations; R-14; build phase P5).

A forecast is a driver model the agent writes as ``work/model.json``; its numbers are parameters in
``run/assumptions.json`` (``harness-model assume``), never prose::

    {"unit": "IRT", "start": "1405/09", "months": 12,
     "opening_balance": {"assumption": "opening_cash"},
     "lines": [{"id": "sales", "kind": "inflow", "base": {"assumption": "sales_base"},
                "growth": {"assumption": "sales_growth"}, "seasonality": {"assumption": "season"}},
               {"id": "cogs", "kind": "outflow", "share_of": "sales", "rate": {"assumption": "cogs_rate"}},
               {"id": "rent", "kind": "outflow", "base": 20000000}],
     "scenarios": {"low": {"sales_growth": -0.03}, "high": {"sales_growth": 0.04}},
     "flip": {"driver": "sales"}}

The engine computes every cell in Decimal; ``recalc`` records every cell as a value (so ``numbers_traced`` can
check R-14), the low/base/high results, the runway, the flip point ("cash runs out in month k if sales fall
more than X%") and the top 3 drivers from a ±20% tornado. ``checks`` runs the accounting identities, unit and
period tags, metamorphic tests, the cross-check of a delivered table, the back-cast against a bank export, the
backtest against a naive seasonal baseline, and the model-risk flags. ``report`` checks the report's required
statements.
"""
from __future__ import annotations

import csv
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from . import localize as L
from . import tables

SCENARIO_SENTENCE = "This is a scenario, not a prediction."
LIMITS_SENTENCE = "Arithmetic checked; assumptions not."
RATE_UNITS = ("ratio", "percent", "index")
TOL = Decimal("0.005")  # back-cast and cross-check tolerance (0.5%)


def _d(v) -> Decimal:
    return Decimal(str(v))


def _q(v: Decimal, places=2) -> Decimal:
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def load(req):
    a = req.get("args") or {}
    from .checklib import safe_path
    model = json.loads(safe_path(req.get("work", "work"), a.get("model", "model.json")).read_text("utf-8"))
    rows = json.loads((Path(req.get("run", "run")) / "assumptions.json").read_text("utf-8")).get("assumptions", []) \
        if (Path(req.get("run", "run")) / "assumptions.json").is_file() else []
    return model, {r["id"]: r for r in rows}


def _ref(ref, A, over):
    if isinstance(ref, dict) and "assumption" in ref:
        k = ref["assumption"]
        if k in over:
            return over[k]
        if k not in A:
            raise ValueError(f"assumption {k!r} is not recorded (harness-model assume)")
        return A[k]["value"]
    return ref


def periods(model) -> list:
    y, m = (int(x) for x in L.digits(model["start"]).replace("/", "-").split("-"))
    out = []
    for _ in range(int(model["months"])):
        out.append(f"{y:04d}/{m:02d}")  # Jalali year/month: the shared tokenizer reads it as a date, not a number
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def run(model, A, over=None, scale=None) -> dict:
    """Every cell. ``over``: assumption overrides; ``scale``: {line_id: factor} on a line's base."""
    over, scale = over or {}, scale or {}
    ps = periods(model)
    cells, by_line = {}, {}
    lines = model["lines"]
    if len({ln["id"] for ln in lines}) != len(lines):
        raise ValueError("line ids must be unique")
    for ln in [x for x in lines if "share_of" not in x] + [x for x in lines if "share_of" in x]:
        vals = []
        for t, p in enumerate(ps):
            if "share_of" in ln:
                if ln["share_of"] not in by_line:
                    raise ValueError(f"{ln['id']} is a share of {ln['share_of']!r}, which is not a base line")
                v = _d(_ref(ln["rate"], A, over)) * by_line[ln["share_of"]][t]
            else:
                v = _d(_ref(ln["base"], A, over)) * _d(scale.get(ln["id"], 1))
                g = _ref(ln.get("growth", 0), A, over)
                v = v * (1 + _d(g)) ** t
                if ln.get("seasonality") is not None:
                    season = _ref(ln["seasonality"], A, over)
                    if not isinstance(season, list) or len(season) != 12:
                        raise ValueError(f"{ln['id']}: seasonality is a list of 12 monthly factors")
                    v = v * _d(season[int(p.split("/")[1]) - 1])
            vals.append(v)
            cells[f"{ln['id']}@{p}"] = v
        by_line[ln["id"]] = vals
    opening = _d(_ref(model.get("opening_balance", 0), A, over))
    inflow = [sum((by_line[x["id"]][t] for x in lines if x["kind"] == "inflow"), Decimal(0)) for t in range(len(ps))]
    outflow = [sum((by_line[x["id"]][t] for x in lines if x["kind"] == "outflow"), Decimal(0)) for t in range(len(ps))]
    op, closing, openings = opening, [], []
    for t, p in enumerate(ps):
        openings.append(op)
        c = op + inflow[t] - outflow[t]
        closing.append(c)
        cells[f"inflow@{p}"], cells[f"outflow@{p}"], cells[f"closing@{p}"] = inflow[t], outflow[t], c
        op = c
    return {"periods": ps, "cells": cells, "lines": by_line, "inflow": inflow, "outflow": outflow,
            "opening": openings, "closing": closing}


def _runway(r):
    return next((r["periods"][t] for t, c in enumerate(r["closing"]) if c < 0), None)


def flip(model, A) -> dict:
    """The smallest fall in the driver line that makes cash negative within the horizon."""
    driver = (model.get("flip") or {}).get("driver") or next(x["id"] for x in model["lines"] if x["kind"] == "inflow")
    if _runway(run(model, A)):
        return {"driver": driver, "fall_pct": Decimal(0), "month": _runway(run(model, A))}
    if not _runway(run(model, A, scale={driver: 0})):
        return {"driver": driver, "fall_pct": None, "month": None}
    lo, hi = Decimal(0), Decimal(1)
    for _ in range(30):
        mid = (lo + hi) / 2
        if _runway(run(model, A, scale={driver: 1 - mid})):
            hi = mid
        else:
            lo = mid
    return {"driver": driver, "fall_pct": _q(hi * 100, 1), "month": _runway(run(model, A, scale={driver: 1 - hi}))}


def tornado(model, A) -> list:
    base = run(model, A)["closing"][-1]
    swing = []
    for k, row in A.items():
        v = row.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v == 0:
            continue
        try:
            hi = run(model, A, over={k: float(_d(v) * Decimal("1.2"))})["closing"][-1]
            lo = run(model, A, over={k: float(_d(v) * Decimal("0.8"))})["closing"][-1]
        except ValueError:
            continue
        swing.append((abs(hi - lo), k))
    if base is None:
        return []
    return [k for _, k in sorted(swing, key=lambda x: (-x[0], x[1]))][:3]


def _num(v):
    return int(v) if v == v.to_integral_value() else float(_q(v))


def recalc(req):
    model, A = load(req)
    base = run(model, A)
    sc = model.get("scenarios") or {}
    res = {"base": base}
    for name in ("low", "high"):
        if name in sc:
            res[name] = run(model, A, over=sc[name])
    f = flip(model, A)
    values = {"cells": {k: _num(_q(v)) for k, v in base["cells"].items()},
              "final_closing": {k: _num(_q(r["closing"][-1])) for k, r in res.items()},
              "min_closing": {k: _num(_q(min(r["closing"]))) for k, r in res.items()},
              "final_closing_millions": {k: _num(_q(r["closing"][-1] / 1000000)) for k, r in res.items()},
              "runway": {k: _runway(r) for k, r in res.items()}, "months": len(base["periods"]),
              "flip_driver": f["driver"], "flip_fall_pct": _num(f["fall_pct"]) if f["fall_pct"] is not None else None,
              "flip_month": f["month"], "top_drivers": tornado(model, A), "unit": model.get("unit"),
              "scenario_overrides": model.get("scenarios") or {}}
    notes = []
    mc = model.get("monte_carlo")
    if mc:
        mv, mnotes = monte_carlo(model, A, mc)
        values["monte_carlo"] = mv
        notes += mnotes
        if any(n.startswith("monte carlo:") for n in mnotes):
            return "fail", values, notes
    missing = [k for k in ("low", "high") if k not in res]
    if missing:
        notes.append(f"no {' and '.join(missing)} scenario: results must be ranges")
    unconfirmed = sorted(k for k in values["top_drivers"] if not A.get(k, {}).get("owner_confirmed"))
    if unconfirmed:
        values["unconfirmed_top_drivers"] = unconfirmed
        notes.append(f"top drivers not confirmed by you: {', '.join(unconfirmed)}")
    return ("fail" if missing else "pass"), values, notes


MC_MAX_RUNS = 5000


def monte_carlo(model, A, mc):
    """P10/P50/P90 of the final cash and the chance of running out, from stated and justified distributions.
    Seeded, so the same model gives the same figures; independence between the varied assumptions is listed."""
    import random
    runs, seed = int(mc.get("runs", 1000)), mc.get("seed")
    vary = mc.get("vary") or {}
    problems = []
    if not isinstance(seed, int):
        problems.append("monte carlo: a seed is required, so the run can be repeated")
    if not 100 <= runs <= MC_MAX_RUNS:
        problems.append(f"monte carlo: runs must be between 100 and {MC_MAX_RUNS}")
    for k, d in vary.items():
        if k not in A or isinstance(A[k].get("value"), list):
            problems.append(f"monte carlo: {k} is not a recorded single-value assumption")
        elif not isinstance(d, dict) or d.get("dist") not in ("normal", "uniform", "triangular"):
            problems.append(f"monte carlo: {k} needs dist normal, uniform or triangular")
        elif not str(d.get("why") or "").strip():
            problems.append(f"monte carlo: {k} needs a why: the reason for its distribution")
    if not vary:
        problems.append("monte carlo: vary names no assumption")
    if problems:
        return {}, problems
    rnd = random.Random(seed)

    def draw(k, d):
        base = float(A[k]["value"])
        if d["dist"] == "normal":
            return rnd.gauss(float(d.get("mean", base)), float(d["sd"]))
        if d["dist"] == "uniform":
            return rnd.uniform(float(d["low"]), float(d["high"]))
        return rnd.triangular(float(d["low"]), float(d["high"]), float(d.get("mode", base)))
    finals, out_of_cash = [], 0
    for _ in range(runs):
        over = {k: draw(k, d) for k, d in sorted(vary.items())}
        r = run(model, A, over=over)
        finals.append(r["closing"][-1])
        out_of_cash += any(c < 0 for c in r["closing"])
    finals.sort()

    def pct(q):
        return _num(_q(finals[min(len(finals) - 1, int(q * len(finals)))]))
    values = {"runs": runs, "seed": seed, "p10": pct(0.1), "p50": pct(0.5), "p90": pct(0.9),
              "chance_out_of_cash_pct": _num(_q(Decimal(out_of_cash * 100) / runs, 1)),
              "varied": sorted(vary)}
    notes = [f"the varied assumptions ({', '.join(sorted(vary))}) are treated as independent: that is an assumption"]
    notes += [f"{k}: {d['dist']} because {d['why']}" for k, d in sorted(vary.items())]
    return values, notes


def _table(path) -> dict:
    h, rows = tables.read(path)
    out = {}
    for r in rows:
        for i, p in enumerate(h[1:], 1):
            v = L.parse_amount(r[i]) if i < len(r) else None
            if v is not None:
                out[f"{L.fold_name(str(r[0]))}@{L.digits(p).strip()}"] = v
    return out


def _monthly(path, args):
    """{jalali YYYY-MM: (sum of amounts, last balance)} from a bank export (signed amounts, optional balance)."""
    from .finance import AMOUNT_WORDS, DATE_WORDS, _date, _find
    h, rows = tables.read(path)
    ai = _find(h, args.get("amount_col"), AMOUNT_WORDS)
    di = _find(h, args.get("date_col"), DATE_WORDS)
    bi = _find(h, args.get("balance_col"), ("balance", "مانده"))
    out = {}
    for r in sorted((r for r in rows if _date(r[di]) is not None), key=lambda r: _date(r[di])):
        a = L.parse_amount(r[ai])
        if a is None:
            continue
        y, m, _ = L.to_jalali(_date(r[di]))
        k = f"{y:04d}/{m:02d}"
        s, bal = out.get(k, (Decimal(0), None))
        b = L.parse_amount(r[bi]) if bi is not None else None
        out[k] = (s + a, b if b is not None else bal)
    return out


def checks(req):
    model, A = load(req)
    a = req.get("args") or {}
    ins = req.get("inputs") or []
    notes, fail = [], False
    base = run(model, A)
    # 1. accounting identities
    for t, p in enumerate(base["periods"]):
        if base["opening"][t] + base["inflow"][t] - base["outflow"][t] != base["closing"][t]:
            notes.append(f"{p}: opening + inflows - outflows is not the closing balance")
            fail = True
        if t and base["opening"][t] != base["closing"][t - 1]:
            notes.append(f"{p}: the opening balance is not the previous closing balance")
            fail = True
    # 2. tags: unit, currency and period on every input
    unit = model.get("unit")
    for k, row in A.items():
        if not row.get("unit") or not row.get("period"):
            notes.append(f"assumption {k} has no unit or no period")
            fail = True
        elif row["unit"] not in RATE_UNITS and row["unit"] != unit:
            notes.append(f"assumption {k} is in {row['unit']} but the model is in {unit}: units are mixed")
            fail = True
        elif row["unit"] in ("ratio", "percent") and row["period"] not in ("month", "once"):
            notes.append(f"assumption {k} is a {row['period']} rate in a monthly model")
            fail = True
    # 3. metamorphic tests
    zero = {k: 0 for k in A if any(ln.get("growth") == {"assumption": k} for ln in model["lines"])}
    flat = run(model, A, over=zero)
    for ln in model["lines"]:
        if "share_of" not in ln and ln.get("seasonality") is None and len(set(flat["lines"][ln["id"]])) > 1:
            notes.append(f"with zero growth, {ln['id']} is not flat")
            fail = True
    driver = (model.get("flip") or {}).get("driver") or next(x["id"] for x in model["lines"] if x["kind"] == "inflow")
    dbl = run(model, A, scale={driver: 2})
    for ln in model["lines"]:
        want = 2 if ln["id"] == driver or ln.get("share_of") == driver else 1
        if any(_q(x * want) != _q(y) for x, y in zip(base["lines"][ln["id"]], dbl["lines"][ln["id"]])):
            notes.append(f"doubling {driver} should multiply {ln['id']} by {want}")
            fail = True
    # 4. the delivered table against the engine (independent medium)
    if a.get("table"):
        from .checklib import safe_path
        got = _table(safe_path(req.get("work", "work"), a["table"]))
        bad = [k for k, v in base["cells"].items() if k in got and abs(got[k] - v) > max(abs(v) * TOL, Decimal(1))]
        if bad or not got:
            notes.append(f"the delivered table differs from the model at {bad[:5] or 'every cell (none read)'}")
            fail = True
    else:
        notes.append("no delivered table to cross-check against the model")
        fail = True
    # 5. back-cast: the bank export's own month-end balances are reproduced within 0.5%
    hist_n = None
    if a.get("bank") is not None:
        months = _monthly(ins[int(a["bank"]) - 1]["path"], a.get("bank_cols") or {})
        keys = sorted(months)
        hist_n = len(keys)
        bal = None
        for k in keys:
            s, end = months[k]
            if bal is None:
                bal = (end - s) if end is not None else None
            if bal is None or end is None:
                break
            bal += s
            if abs(bal - end) > max(abs(end) * TOL, Decimal(1)):
                notes.append(f"back-cast: month {k} rebuilds to {_q(bal)} but the bank says {end}: rows are missing or "
                             "duplicated")
                fail = True
                break
            bal = end
        last = months[keys[-1]][1] if keys else None
        opening = _d(_ref(model.get("opening_balance", 0), A, {}))
        if last is not None and abs(opening - last) > max(abs(last) * TOL, Decimal(1)):
            notes.append(f"the opening balance {opening} is not the bank's last balance {last}")
            fail = True
    # 6. backtest against a naive seasonal baseline on held-out months
    if a.get("history") is not None:
        h, rows = tables.read(ins[int(a["history"]) - 1]["path"])
        series = [L.parse_amount(r[1]) for r in rows if len(r) > 1 and L.parse_amount(r[1]) is not None]
        hist_n = max(hist_n or 0, len(series))
        ln = next(x for x in model["lines"] if x["id"] == driver)
        g = _d(_ref(ln.get("growth", 0), A, {}))
        if len(series) >= 6:
            train, test = series[:-3], series[-3:]
            pred = [train[-1] * (1 + g) ** (k + 1) for k in range(3)]
            naive = [series[len(train) + k - 12] if len(series) >= 15 else train[-1] for k in range(3)]
            mae = sum(abs(p - t) for p, t in zip(pred, test)) / 3
            mae_n = sum(abs(p - t) for p, t in zip(naive, test)) / 3
            if mae > mae_n:
                notes.append(f"the model misses the last 3 months by {_q(mae)} on average; a naive seasonal "
                             f"forecast misses by {_q(mae_n)}: the model does worse than the baseline")
                fail = True
            lo, hi = min(series), max(series)
            b0 = _d(_ref(ln["base"], A, {}))
            if not lo <= b0 <= hi:
                notes.append(f"{driver} starts at {b0}, outside its history ({lo} to {hi})")
                fail = True
    # 7. model-risk flags
    if hist_n is not None and int(model["months"]) > hist_n:
        notes.append(f"the forecast runs {model['months']} months on {hist_n} months of history: extrapolation")
        fail = True
    if any(x.get("seasonality") is not None for x in model["lines"]) and (hist_n or 0) < 24:
        notes.append("seasonality needs at least 2 years of history")
        fail = True
    return ("fail" if fail else "pass"), {"identities": len(base["periods"]), "history_months": hist_n}, notes


def report(req):
    a = req.get("args") or {}
    from .checklib import safe_path
    text = safe_path(req.get("work", "work"), a.get("report", "report.md")).read_text("utf-8")
    low = text.casefold()
    model, A = load(req)
    notes = []
    if SCENARIO_SENTENCE.casefold() not in low and "این یک سناریو است، نه پیش‌بینی" not in text:
        notes.append(f"the report must say: {SCENARIO_SENTENCE}")
    if LIMITS_SENTENCE.casefold() not in low and "محاسبات بررسی شد؛ فرض‌ها نه" not in text:
        notes.append(f"the report must say: {LIMITS_SENTENCE}")
    if not (all(w in low for w in ("low", "base", "high")) or all(w in low for w in ("p10", "p50", "p90"))
            or all(w in text for w in ("بدبینانه", "پایه", "خوش‌بینانه"))):
        notes.append("results must be ranges (low, base and high)")
    drivers = tornado(model, A)
    named = [d for d in drivers if d.casefold() in low or (A.get(d, {}).get("label") or "\0").casefold() in low]
    if len(named) < len(drivers):
        notes.append(f"name the top drivers: {', '.join(d for d in drivers if d not in named)}")
    f = flip(model, A)
    if f["month"] and f["month"] not in text and str(f["fall_pct"]) not in text:
        notes.append(f"state the flip point: cash runs out in {f['month']} if {f['driver']} falls more than "
                     f"{f['fall_pct']}%")
    return ("fail" if notes else "pass"), {"drivers_named": len(named)}, notes


def write_table(path, r) -> None:
    """Helper for tests and examples: the engine's cells as a CSV table (lines x periods)."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["line", *r["periods"]])
        for k, vals in r["lines"].items():
            w.writerow([k, *[str(_q(v)) for v in vals]])


# --- the deliverable workbook (spec §Business simulations "Deliverable") -------------------------------------------
def _assumption_rows(model, A):
    """[(id, label, value, unit, period, source)] for the Assumptions sheet; literals in model.json become rows
    too, so the calculation sheet holds formulas only. A seasonality becomes 12 rows ``<id>.1`` to ``<id>.12``."""
    rows, where = [], {}

    def add(key, label, value, unit, period, source):
        where[key] = len(rows)
        rows.append((key, label, value, unit, period, source))

    def ref(r, key, unit, period):
        if isinstance(r, dict) and "assumption" in r:
            k = r["assumption"]
            a = A[k]
            if isinstance(a["value"], list):
                for i, v in enumerate(a["value"], 1):
                    if f"{k}.{i}" not in where:
                        add(f"{k}.{i}", f"{a.get('label') or k} ({L.JALALI_MONTHS[i - 1]})", v, a["unit"], a["period"],
                            a.get("source"))
                return k
            if k not in where:
                add(k, a.get("label") or k, a["value"], a["unit"], a["period"], a.get("source"))
            return k
        add(key, key, r, unit, period, "model")
        return key

    keys = {"opening": ref(model.get("opening_balance", 0), "opening_balance", model.get("unit"), "once")}
    for ln in model["lines"]:
        if "share_of" in ln:
            keys[(ln["id"], "rate")] = ref(ln["rate"], f"{ln['id']}_rate", "ratio", "month")
        else:
            keys[(ln["id"], "base")] = ref(ln["base"], f"{ln['id']}_base", model.get("unit"), "month")
            keys[(ln["id"], "growth")] = ref(ln.get("growth", 0), f"{ln['id']}_growth", "ratio", "month")
            if ln.get("seasonality") is not None:
                keys[(ln["id"], "season")] = ref(ln["seasonality"], f"{ln['id']}_season", "index", "month")
    return rows, where, keys


def workbook_sheets(model, A, lang="en") -> list:
    from .xlsx import ref as cell
    r = run(model, A)
    arows, where, keys = _assumption_rows(model, A)
    fa = lang == "fa"
    head = ["id", "label", "value", "unit", "period", "source"]
    sheet1 = [[{"v": h, "style": "header"} for h in head]]
    for k, label, value, unit, period, source in arows:
        sheet1.append([k, label, {"v": value, "style": "input"}, unit, period, source])

    def a(key):  # absolute reference to an assumption's value cell
        return f"Assumptions!$C${where[key] + 2}"

    ps = r["periods"]
    lines = model["lines"]
    calc = [[{"v": "line", "style": "header"}] + [{"v": p, "style": "header"} for p in ps]]
    row_of = {}
    order = [x for x in lines if "share_of" not in x] + [x for x in lines if "share_of" in x]
    for ln in order:
        row_of[ln["id"]] = len(calc)
        out = [ln["id"]]
        for t, p in enumerate(ps):
            if "share_of" in ln:
                f = f"{a(keys[(ln['id'], 'rate')])}*{cell(row_of[ln['share_of']], t + 1)}"
            else:
                f = f"{a(keys[(ln['id'], 'base')])}*(1+{a(keys[(ln['id'], 'growth')])})^{t}"
                if (ln["id"], "season") in keys:
                    f += f"*{a(keys[(ln['id'], 'season')] + '.' + str(int(p.split('/')[1])))}"
            out.append({"f": f, "v": float(_q(r["lines"][ln["id"]][t]))})
        calc.append(out)
    totals = {}
    for name, kind in (("inflow", "inflow"), ("outflow", "outflow")):
        totals[name] = len(calc)
        idx = [row_of[x["id"]] for x in lines if x["kind"] == kind]
        calc.append([name] + [{"f": "+".join(cell(i, t + 1) for i in idx) if idx else "0",
                               "v": float(_q(r[name][t]))} for t in range(len(ps))])
    totals["opening"], totals["closing"] = len(calc), len(calc) + 1
    calc.append(["opening"] + [{"f": a(keys["opening"]) if t == 0 else cell(totals["closing"], t),
                                "v": float(_q(r["opening"][t]))} for t in range(len(ps))])
    calc.append(["closing"] + [{"f": f"{cell(totals['opening'], t + 1)}+{cell(totals['inflow'], t + 1)}-"
                                     f"{cell(totals['outflow'], t + 1)}", "v": float(_q(r["closing"][t]))}
                               for t in range(len(ps))])
    checks = [[{"v": h, "style": "header"} for h in ("period", "closing = opening + inflow - outflow",
                                                       "opening = previous closing")]]
    for t, p in enumerate(ps):
        c = lambda name: f"Calc!{cell(totals[name], t + 1)}"  # noqa: E731
        ident = {"f": f"ROUND({c('opening')}+{c('inflow')}-{c('outflow')}-{c('closing')},2)=0", "v": True,
                 "style": "check"}
        cont = ({"f": f"{c('opening')}={a(keys['opening'])}", "v": True, "style": "check"} if t == 0 else
                {"f": f"Calc!{cell(totals['opening'], t + 1)}=Calc!{cell(totals['closing'], t)}", "v": True,
                 "style": "check"})
        checks.append([p, ident, cont])
    sc = model.get("scenarios") or {}
    res = {"base": r, **{k: run(model, A, over=sc[k]) for k in ("low", "high") if k in sc}}
    f = flip(model, A)
    summary = [[{"v": "خلاصه" if fa else "Summary", "style": "header"}, ""]]
    for k in ("low", "base", "high"):
        if k in res:
            summary.append([f"final closing ({k})", float(_q(res[k]["closing"][-1]))])
    summary.append(["runway (base)", _runway(r) or "beyond the horizon"])
    if f["month"]:
        summary.append(["flip point", f"cash runs out in {f['month']} if {f['driver']} falls more than "
                                      f"{f['fall_pct']}%"])
    summary.append(["top drivers", ", ".join(tornado(model, A))])
    summary.append([SCENARIO_SENTENCE if not fa else "این یک سناریو است، نه پیش‌بینی.", ""])
    summary.append([LIMITS_SENTENCE if not fa else "محاسبات بررسی شد؛ فرض‌ها نه.", ""])
    return [{"name": "Assumptions", "rows": sheet1, "widths": [22, 32, 16, 10, 10, 12]},
            {"name": "Calc", "rows": calc, "protect": True, "widths": [14] + [14] * len(ps)},
            {"name": "Checks", "rows": checks, "protect": True, "widths": [12, 36, 28]},
            {"name": "Summary", "rows": summary, "widths": [24, 60]}]


def workbook(req):
    """The ``model.workbook`` check: the delivered workbook must be the one the model builds. Its structure
    (workbook, relationships, content types), its Calc sheet and its Checks sheet must equal, byte for byte, what
    ``workbook_sheets`` produces from model.json and the recorded assumptions, every sheet included. The owner's
    edits come back through ``harness-model import``, never through this check. A regex over the XML would let a
    crafted file pass."""
    import io
    import zipfile
    from .checklib import safe_path
    from .xlsx import build
    a = req.get("args") or {}
    data = safe_path(req.get("work", "work"), a.get("workbook", "forecast.xlsx")).read_bytes()
    model, A = load(req)
    want = build(workbook_sheets(model, A, a.get("lang", "en")))
    want_fa = build(workbook_sheets(model, A, "fa"))
    notes = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            got = {n: z.read(n) for n in z.namelist() if z.getinfo(n).file_size <= 50 * 1024 * 1024}
            names = z.namelist()
    except zipfile.BadZipFile:
        return "fail", {}, ["the workbook is not a valid .xlsx file"]
    if sorted(names) != sorted(want):
        notes.append(f"the workbook's parts differ from the model's: {sorted(set(names) ^ set(want))[:6]}")
    fixed = ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml",
             "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml",
             "xl/worksheets/sheet4.xml")
    for part in fixed:
        if got.get(part) not in (want.get(part), want_fa.get(part)):
            label = {"xl/worksheets/sheet1.xml": "the Assumptions sheet (recorded values)",
                     "xl/worksheets/sheet2.xml": "the Calc sheet (formulas)",
                     "xl/worksheets/sheet3.xml": "the Checks sheet",
                     "xl/worksheets/sheet4.xml": "the Summary sheet"}.get(part, part)
            notes.append(f"{label} differs from the one the model builds: rebuild it with harness-model workbook")
    return ("fail" if notes else "pass"), {"parts_compared": len(fixed)}, notes
