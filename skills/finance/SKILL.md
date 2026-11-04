---
name: finance
description: Finance pack for dynamic-harness tasks on the owner's own data (sales, bank, invoices). Use when a task card's oracle is finance.reconcile, finance.kpi, finance.bridge or finance.list.
---

# Finance pack

Never add up rows yourself. Every figure in the answer comes from a check that the harness runs and records.

## Checks

Run each with `harness-run <check> --unit <id> --attempt <n> --input <file> --arg key=value ...`. The hook runs
the check in a sandbox, records its output, and prints the output sha and values.

| Check | Use for | Main args |
| --- | --- | --- |
| `finance.reconcile` | One figure over a period ("March sales") | `period` (`{"month": "1402-12"}`, `{"fiscal_year": 1402}` or `{"from", "to"}`), `amount_col`, `date_col`, `filters`, `metric_id`, `control` (`{"input": 2, "amount_col", "label"}`) |
| `finance.kpi` | Several KPIs in one report | `kpis: [{id, amount_col, filters, period, max_change}]` |
| `finance.bridge` | "Why did it change?" | `current` and `prior` input numbers, `item_col`, `qty_col`, `price_col` |
| `finance.list` | A list deliverable | `list` (a CSV in work/), `id_col`, `date_col`, plus the source filters |

## Rules

- A `fail` result names the gap in its notes (rows left out, data ending early, sources that disagree, a jump
  of ten times or more against the prior period). Show those notes to the owner next to the figure. Never pick
  one of two disagreeing sources.
- After a `pass`, append the unit's ledger row with `"outputs": ["<sha>"]` and the same attempt number. After a
  `fail`, append a `FAIL` row citing the output; a second failure ends the unit as "done, not fully checked".
- Cite only the values the check printed: `total`, `total_millions`, `total_other_unit`, `rows_used`, and so on.
- For a figure the owner relies on, run `harness-answer header --unit <id> --output <sha>` before the gate. It
  also writes the rows behind the figure as `work/rows-<unit>.xlsx`; attach it to the answer.
