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
    to the lexicons of the packs, so a shipped state word your file left out applies again. Such a file whose
    `terms` or `template_lines` is `null` is now refused with an error that names the file (0.4.0 read `null` as
    an empty list); a missing key still counts as empty.
- **Folds from the packs.** Before text is compared, the letter forms a language reads as one letter (the nine
  Arabic letter forms of 0.4.0) now come from `fold.letters` of the locale packs (shipped in `fa`), read from every
  installed pack; a new language adds its own in its pack. Digits of every script now count as ASCII digits (0.4.0:
  only the Arabic-Indic and Extended Arabic-Indic ones), and every invisible format character (Unicode `Cf`) is
  dropped, or read as a space in name matching (0.4.0: two in request matching, six in name matching). Request
  matching and the stop gate now also fold the seven letter forms that only name matching folded in 0.4.0 (for
  example alef with hamza), so a state word written with another alef form is caught, and the hit is shown with
  the plain alef. The request of a pack check holds the
  key `locale.fold`, so the gate replays a check with the folds of its run; a check recorded before this change
  uses the shipped packs.
- **Languages from files.** The language list is the set of files in `templates/`, and digits, separators, script
  ranges and lint rules of a language come from `render.<lang>` in a locale pack; `business set language` accepts
  any language with a template file. The text an owner sees in English or Persian is unchanged.
- **Currencies from the packs.** Currency names and the relations between two coded units of one currency are
  locale pack data: `currencies.<code>.names.<lang>` (and `decimals`) and `currency_pairs` (`{from, to, factor}`,
  1 `from` = `factor` `to`), read from every installed pack. A live pack with a new currency and a new relation
  changes the names in your text, the conversion in a check and in its replay, and the conversion of your limits,
  with no code change. A currency no pack names is shown as its code, in any language. The shipped packs hold the
  seven codes and the one relation of 0.4.0, so no result changes, except: setting the currency that is already
  set no longer warns that your spending limits will be removed (they never were); a currency change that must
  convert or drop a limit, the risk threshold or a rate is refused while a live locale pack is broken (the message
  names the file); and a confirm is refused, with nothing changed, when the packs changed between the offer and
  the confirm in a way that changes what happens to those figures (type the command again). The request of a pack
  check holds the key `locale.currency_pairs`; a check recorded before this change converts with the shipped
  packs, and a request whose `currency_pairs` is not a list ends the check with an error. The
  hint for a missing currency reads `business set currency <code>`, and a code that is not 3 letters is refused
  with "currency is a 3-letter code, for example USD, EUR or JPY".
- **Currency words from the packs.** The words that name one currency are locale pack data,
  `words.currency_words.<code>`, moved out of `money_words` (the shipped packs list `USD`, `EUR`, `IRT`, `IRR`,
  plus the adjective forms تومانی and ریالی), and one rule decides a match everywhere: no letter may touch the
  word, a digit or `_` may. A live pack's word changes the currency of a column header, the type of a research
  claim, the request probe and the quick-answer check, with no code change. What changes for you:
  - `3000<word>`, `5000toman` and `amount_usd` now name money; a word inside a longer word (`europe`,
    `material`) no longer makes a price claim, and a figure with any currency sign does.
  - A column header that names another currency than yours, with no relation between the two, ends the check
    with an error (`cannot convert <A> to <B>`); 0.4.0 summed such a column silently in your currency. A header
    that names both units of a pair is read as the one named first (0.4.0 always took Toman).
  - With the profile list `locales`, the currency words of the packs it leaves out are not matched.
  - The ×10 note reads "check for a missing zero or amounts in another currency", or, when your currency has a
    related unit, "check for a IRT/IRR mix-up or a missing zero" (codes, not names); 0.4.0 said "Toman/Rial" to
    every owner.
  - The request of a pack check holds `locale.currency_words`; a request recorded before it matches with the
    shipped packs. A malformed `locale.currency_pairs` or `locale.currency_words` ends the check with an error
    that names the key.
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
