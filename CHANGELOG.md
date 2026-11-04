# Changelog

All notable changes to dynamic-harness. Versions follow [semantic versioning](https://semver.org/).

## 0.4.0

The first complete release of the v1 scope.

- **Domain packs and `harness-run`.** Registered checks for figures (`finance`), research (`research`), forecasts
  (`model`) and documents and decisions (`operations`), run by a hook in an OS sandbox and recorded with hashes.
- **Done mode.** The gate replays every check a passing unit cites, on the files as delivered, before it signs.
- **Business layer.** `/dynamic-harness:business` for currency, calendar, fiscal year, spending limits, own
  accounts and protected names; metric definitions confirmed by the owner; per-task and monthly money caps.
- **Answer header** for every money figure, with the rows behind it attached as a workbook.
- **Research** with pages fetched by the harness itself, verbatim quotes, freshness, two independent sources,
  a verifier verdict and a saturation stop.
- **Forecasts** with every number traced to a model cell, ranges, a flip point, a backtest, Monte Carlo and a
  formula workbook the owner can edit and send back.
- **Persian** templates written natively, Jalali dates and Toman/Rial handling; check notes in the owner's
  language.
- **Owner commands** `memory`, `status`, `export`, `config` and `off`, all answered by a hook.
- **License:** MIT.

## 0.3.0

- Task lease with epoch fencing; budget, stall and cost stops; the gate hook and the Stop hook; corrections
  that re-understand a task; follow-ups; decision records.
- `/dynamic-harness:setup` with a two-step, code-confirmed plan and `undo`.

## 0.2.0

- Probe and pre-filter, task card, tier selector and compiler; contact caps; user memory; subagent lanes; the
  tripwire; personal-data redaction.

## 0.1.0

- Canonical hashing, the hash-chained event log, the ledger and scope versions, the gate adapter and signed
  receipts; the safety hook, irreversible-action classes, egress control, protected paths and the sandbox
  launcher.
