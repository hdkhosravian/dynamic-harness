# Changelog

All notable changes to dynamic-harness. Versions follow [semantic versioning](https://semver.org/).

## Unreleased

- **Plugin hash.** The session check hashes only the shipped plugin directories and the entries Claude Code
  loads, so a development checkout's docs, tests and caches no longer stop a session; a file that vanishes
  mid-hash is retried once, then fails closed.
- **Setup asks before changing global settings.** `/dynamic-harness:setup` lists the changes to this folder
  and the optional change to `~/.claude/settings.json` apart. `confirm <code>` changes this folder only;
  `confirm <code> global` also makes the global change. `status` says which one applies.
- **Command classifier.** Closed four bypasses: a redirect target named like a heredoc placeholder, `sed`
  `w`/`e` flags after a delimiter other than `/`, `awk` piping through a variable, and a glob in the command
  path (`/???/r? -rf ~`).
- **Look-alike file names.** Protected-path matching folds fullwidth, styled, accented, zero-width,
  Cyrillic and Greek look-alikes, so `СLAUDE.md` (Cyrillic С) is protected like `CLAUDE.md`. Write exceptions
  (a job's `work/` and `requests/`) still match exactly. Closes red-team rows rt106 and rt107.

- **Found by running it live** (`claude -p` in a scratch folder):
  - Claude Code writes per-session `.in_use/` markers into an installed plugin's folder. They are no longer
    hashed, so a new session is no longer taken for tampering.
  - A loop-contract that says 1.1.0 but whose `fold_ledger.py` lacks the gate's flags is refused at setup
    and session start, instead of every task failing at the gate. After an update, setup finds the
    installed loop-contract again.
  - A quick answer that names money without a figure ("net margin is net income over revenue") is no longer
    sent back for a task card.
  - A `harness-*` command whose quoted text holds `;` or `( )` is recorded. One that is not alone says it was
    not recorded.
  - A JSON argument such as `[{"month": "1402-12"}]` no longer crashes the classifier as a bad glob range.
  - `setup undo` after a re-run that changed no settings reverses the real setup.
  - The answer header says to re-run with `finance.reconcile` when given a `finance.kpi` output.
- **Checks.** `SUPPORTED`/`REFUTED` need a passing `research.check` from the same attempt. A verifier reply
  needs one plain yes per criterion. `model.checks` fails a delivered table that is missing model cells, and a
  yearly amount in a monthly model. A percent assumption is stored as a fraction. A Toman/Rial switch also
  converts the dollar rate and the risk threshold. The owner's accounts, currency and footer cannot be
  replaced by a check's args. A fiscal year must be numbered in the calendar its start day uses.
- **Protected paths.** A nested `.claude/` folder and `.mcp.json` are protected like the root ones.
- **Amounts.** A comma that is not thousands grouping (`1,5`) makes an amount unreadable, so the row is reported
  instead of counted as 15. The `awk` check reads strings, regexes and comments in one pass, so a quote inside a
  regex or a comment cannot hide a command.

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
