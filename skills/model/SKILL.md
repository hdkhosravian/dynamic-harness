---
name: model
description: Model-checks pack for dynamic-harness forecasts, what-ifs and break-even (task type 6). Use when a task card's oracle is model.recalc, model.checks or model.report.
---

# Model-checks pack

Assumptions are parameters, not questions. Every number in the report comes from a model cell.

## Steps

1. Record each assumption: `harness-model assume <id> <value> --unit IRT|ratio|percent|index --period month|once
   --source history|benchmark|guess|owner`. Defaults come from the data first (trailing growth, seasonal index),
   then benchmarks, then a labelled guess. A seasonality is a list of 12 monthly factors (JSON).
2. Show the owner the assumption sheet: `harness-contact assumption_sheet`. Only the top 3 by sensitivity are worth
   asking about. After the owner's plain "OK", `harness-contact assumption_sheet --accept latest` marks them
   confirmed. Assumptions found after 50% progress are recorded and flagged, never asked.
3. Write `work/model.json` (lines with `base`, `growth`, `seasonality`, or `share_of` + `rate`; `scenarios`
   `low` and `high`; `flip.driver`), and a table of the results, `work/model_table.csv` (line x month).
4. `harness-run model.recalc --unit <id> --attempt <n>`: every cell, the ranges, the runway, the flip point, the
   top drivers. `harness-run model.checks ... --input bank.csv --input sales_history.csv --arg bank=1
   --arg history=2 --arg table=model_table.csv`: identities, tags, metamorphic tests, the back-cast, the
   backtest and the risk flags.
5. Write `work/report.md`: ranges (low, base, high) rounded in the owner's unit, the top 3 drivers, the flip point
   ("you run out of cash in month 7 if sales fall more than 14%"), "This is a scenario, not a prediction." and
   "Arithmetic checked; assumptions not." Then `harness-run model.report`.
6. After the gate and DONE, `harness-model register --deliverable model.json`. "Now try 15%" is a scenario run:
   `harness-model scenario --set sales_growth=0.15`.

Use the simplest model first: naive seasonal, then linear, then driver-based. A more complex method must beat
the baseline on held-out months.
