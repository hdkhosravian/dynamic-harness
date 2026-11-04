# Packs and checks

A pack is a set of registered checks for one kind of work. The model never adds up numbers itself: it runs a
check, the hook runs that check in a sandbox and records the result, and the report may use only the values
the check printed.

| Pack | Checks | For |
| --- | --- | --- |
| [`finance`](#finance) | `finance.reconcile`, `finance.kpi`, `finance.bridge`, `finance.list` | Questions on your own data: sales, bank exports, invoices. |
| [`research`](#research) | `research.check`, `research.report` | Investigations: market, competitor, supplier, regulation. |
| [`model`](#model) | `model.recalc`, `model.checks`, `model.report`, `model.workbook` | Forecasts, what-ifs, break-even, Monte Carlo. |
| [`operations`](#operations) | `doc.check`, `decide.score_flip` | Documents (proposals, contract drafts, replies) and decisions (vendor, pricing, make or buy). |

## How a check runs

The model runs:

```bash
harness-run finance.reconcile --unit u1 --attempt 1 --input sales.csv --arg amount_col=amount
```

Then:

1. The `harness-run` hook copies each `--input` file, the task's `work/` folder and the run-state files the
   pack's manifest names into a fresh folder.
2. It runs the check there under the OS sandbox: no network, writes only in that folder.
3. The check prints one JSON line: `{"result": "pass" | "fail", "values": {...}, "notes": [...]}`.
4. The hook stores the output, its input and every input file by hash, and prints the output's sha and values.
5. The model cites that sha in the unit's ledger row. A PASS row needs a clean output of the unit's own check
   from the same attempt.

A crash, a missing result line or a timeout is recorded as `error`, and never backs a PASS. `harness-run`
refuses inputs written in this session, and anything that is not a regular file.

**Done mode.** At the gate, the oracle replays every output a passing unit cites, with the same inputs but
the deliverables as submitted. It requires the same result and the same values. So an edit after a check
fails the gate.

**Notes in your language.** Check notes (rows left out, sources that disagree, data that ends early, and so
on) are rendered in the owner's language, English or Persian.

## Finance

Every total is computed in the check, in exact decimal arithmetic. A gap you must know about is a note, and
every such gap makes the result `fail`. The answer can then be at best "Done, not fully checked: \<gap\>".

| Check | Use for | What it checks |
| --- | --- | --- |
| `finance.reconcile` | One figure over a period ("March sales") | A second computation path (the file's own total row, else monthly subtotals); a control total from a second source; coverage of the period; own-account transfers excluded and counted; a ×10 magnitude check against the prior period. |
| `finance.kpi` | Several KPIs in one report | `reconcile` per KPI, plus a sanity bound against the prior period. |
| `finance.bridge` | "Why did it change?" | Price, volume and new or lost item effects add up to the total change. |
| `finance.list` | A list deliverable | The list's row count and sum equal the filtered total; every row has an ID and a date. |

Main arguments for `finance.reconcile`: `period` (`{"month": "1402-12"}`, `{"fiscal_year": 1402}` or
`{"from", "to"}`), `amount_col`, `date_col`, `filters`, `metric_id`, `control`.

### The answer header

For a figure you rely on, the model runs:

```bash
harness-answer header --unit u1 --output <sha>
```

The writer renders three lines from the recorded check and your business settings, never from the model's
wording:

1. the figure, the metric and its definition version, the period in your calendar and in Gregorian, the unit
   and currency;
2. the source and the date its data runs to, the exclusions, and the number of rows used;
3. the check status in plain words.

It also writes the rows behind the figure to `work/rows-<unit>.xlsx`, so you can open them in a spreadsheet.

A metric needs a definition you confirmed first. The model sends it with `harness-contact definition_confirm`;
you see one sentence ("I'll count sales as ..., excluding ..., in Toman. Is that right?") and answer yes. A
changed definition gets a new version.

## Research

Page text never reaches the main model.

1. The model proposes a research plan: hypotheses, searches, pages, a page budget and a stop rule. You see it
   directly and reply yes. That reply opens a research grant for this task only.
2. The `web-researcher` subagent searches and fetches pages with `harness-claims fetch`. The hook does the
   fetch: https only, public addresses only, at most 2 MB, under the grant. It stores the page's visible text
   as a snapshot.
3. The researcher returns claim rows only: a claim, a verbatim quote of at least 20 characters that contains
   every number the claim states, a date, the URL and the snapshot sha. The SubagentStop hook keeps a row only
   when its quote is on the stored page.
4. **Saturation.** After 5 searches that found nothing new, further WebSearch calls are denied with a message
   to return the claim rows.
5. The model cites the rows that decide each hypothesis (`harness-claims cite`), has a verifier answer the
   frozen criteria, then runs the checks.

| Check | What it checks |
| --- | --- |
| `research.check` | Each cited claim rests on a snapshot this task fetched, quotes it verbatim, is fresh enough for its type (prices 30 days, laws by in-force date, others 365 days), and was not flagged for embedded instructions. Load-bearing claims come from at least two independent sources. A verifier verdict was recorded. It also computes the confidence (High, Medium, Low). |
| `research.report` | Every line under `## Findings` cites `[claim N]` or says "model knowledge, not checked". |

Two copies of one article count once. A claim about your own data says "associated with", never "caused".

## Model

A forecast is a driver model: `work/model.json` over assumptions recorded with `harness-model assume`. Every
number in it is a parameter, never prose.

1. The model records each assumption, with its unit, period and source (history, benchmark, guess or owner).
2. You see the assumption sheet. Only the top 3 by sensitivity are worth your attention. A plain "OK" confirms
   them.
3. The model writes `work/model.json` and a results table, then runs the checks.
4. The report gives ranges (low, base, high), the top 3 drivers and the flip point ("you run out of cash in
   month 7 if sales fall more than 14%"). It always says "This is a scenario, not a prediction." and
   "Arithmetic checked; assumptions not."

| Check | What it checks |
| --- | --- |
| `model.recalc` | Every cell of `work/model.json` from the recorded assumptions; low, base and high; runway; flip point; top 3 drivers. With Monte Carlo, also P10, P50 and P90 of the final cash and the chance of running out. |
| `model.checks` | Accounting identities, unit and period tags, metamorphic tests, a cross-check of the delivered table, a back-cast against your bank export, a backtest against a naive seasonal baseline, model-risk flags. |
| `model.report` | The report gives ranges, the top drivers, the flip point and the two required sentences. |
| `model.workbook` | The forecast workbook: no constants in calculation cells, no `#REF`, and every check cell true. |

### The forecast workbook

```bash
harness-model workbook
```

The hook writes `work/forecast.xlsx` from `work/model.json` (`--out NAME.xlsx` picks another file name):

| Sheet | Contents |
| --- | --- |
| Assumptions | Every assumption, coloured as an input. |
| Calc | The model as formulas. The sheet is protected. |
| Checks | The cash identities again, as formulas. |
| Summary | The results. |

The workbook is written by the plugin's own standard-library XLSX writer (`lib/harness_core/xlsx.py`); there is
nothing to install. You can change assumptions in the workbook (or in a CSV with `id,value` columns) and hand it
back. `harness-model import <file>` reads either: if only known assumption values changed, it runs a scenario;
any other change is a new task that follows the old one.

### Monte Carlo

Add a `monte_carlo` block to `work/model.json`. Its shape:

```text
"monte_carlo": {
  "runs": 1000,
  "seed": 7,
  "vary": {
    "sales_growth": {"dist": "normal", "sd": 0.02, "why": "monthly growth varied by about 2 points last year"},
    "cogs_rate": {"dist": "triangular", "low": 0.55, "high": 0.7, "why": "supplier quotes range from 55% to 70%"},
    "rent": {"dist": "uniform", "low": 180000000, "high": 220000000, "why": "the lease renews at a rate in this band"}
  }
}
```

- `normal` takes `sd` (and an optional `mean`, which defaults to the recorded value); `uniform` takes `low` and
  `high`; `triangular` takes `low`, `high` and an optional `mode` (the recorded value by default).
- `seed` is required, so the same model gives the same figures; `runs` is between 100 and 5,000.
- Every varied assumption needs a `why`: the reason for its distribution.
- The varied assumptions are treated as independent, and the report lists that as an assumption.
- `model.recalc` then reports P10, P50 and P90 of the final cash and the chance of running out.

### Scenarios

After the gate and DONE, the model registers the model (`harness-model register`). "Now try 15%" is then a
scenario run, a new child task with its own receipt:

```bash
harness-model scenario --set sales_growth=0.15
```

## Operations

v1 prepares work only. Nothing is sent, published, signed or paid. The report says "I've prepared this.
Nothing was sent or changed."

| Check | What it checks |
| --- | --- |
| `doc.check` | Every required item you named is present; no placeholders left (`[name]`, `{{date}}`, `XXX`); the footer from your business profile closes the document; nobody on the do-not-contact list is addressed. |
| `decide.score_flip` | Weighted scores recomputed by code; constraints; the flip test, which moves each weight by 20% and names every move that changes the winner. |

The criteria and weights for a decision are always yours.

## Adding a pack

See [development.md](development.md#adding-a-pack).
