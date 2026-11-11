# Changelog

All notable changes to dynamic-harness. Versions follow [semantic versioning](https://semver.org/).

## Unreleased

- **Locale packs.** Words and regional facts get a data format (`harness/locale@1`, `control/defaults/locales/`,
  live packs in `<data>/control/locales/`) and the profile list `locales` (`business add locales <id>`); with no
  list every installed pack applies, as in 0.4.0.
- **Matching words from the packs.** The verbs, question words, money, quantity, decision and continuation
  words, the state words and the question marks moved from `probe.json` and `state_lexicon.json` into the
  locale packs (`words`, `state_lexicon`, `question_marks`); a new language needs a pack and no code. The list
  `locales` chooses the matching words; state words, question marks and `render` apply for every installed
  pack; the pack of the profile language is always active. For the shipped words no result changes. If you
  already use the harness:
  - `probe.json` now holds only your own extra words, matched in addition to the packs. A word you removed
    from a live 0.4.0 `probe.json` applies again, because it now comes from a pack: packs can add, never remove.
  - `config show probe.json` on a fresh data folder shows empty lists (your additions only).
  - A verb you add that no pack lists is matched anywhere, also inside a question. In 0.4.0 that was true only
    for a verb that does not start with `a-z`.
  - `control/defaults/state_lexicon.json` is gone, and `config show state_lexicon.json` answers "No control
    file". In 0.4.0 a `control/state_lexicon.json` you placed by hand replaced the shipped lexicon; now it adds
    to the lexicons of the packs, so a shipped state word your file left out applies again.
- **Languages from files.** The language list is the set of files in `templates/`, and digits, separators, script
  ranges and lint rules of a language come from `render.<lang>` in a locale pack; `business set language` accepts
  any language with a template file. The text an owner sees in English or Persian is unchanged.
- **Live end-to-end scaffold.** `tests/e2e` runs one scripted `claude -p` request per pack and verifies its
  receipt with the harness's own check; it is skipped unless `HARNESS_E2E=1` and has not been run against a live
  model yet.
- **loop-contract 1.2.0.** The harness now needs loop-contract 1.2.0 or later (the gate flags ship there);
  setup prints the two Claude Code commands that install or update it.
- **Plugin hash.** The session check hashes only the shipped plugin directories and the entries Claude Code
  loads, so a development checkout's docs, tests and caches no longer stop a session; a file that vanishes
  mid-hash is retried once, then fails closed. If you already use the harness: the recorded hash covered more
  than this. Tasks above T0 are first refused because loop-contract is older than 1.2.0; install or update it
  with the commands setup prints, then type `/dynamic-harness:setup`. Its plan warns that the plugin changed
  since the last setup; that is expected after an update. Confirm, then start a new session. If loop-contract
  is already 1.2.0 or later when you update, the first session stops with a plugin hash mismatch instead; the
  same steps fix it.
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
