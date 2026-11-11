# dynamic-harness: implementation plan for the remaining work

Status: plan only. Nothing in this document is implemented. Written 2026-11-11 against commit `8cf9320` on `main`.

This plan covers four things: what exists today (checked against the files), what the owner's needs still lack, the
work to close each gap, and what must be true before the project is shared in public.

## How to read this plan

- **[V]** means verified in this planning session by opening the file or running a command. Evidence is given as
  `file:line`.
- **[I]** means inference or design proposal. It is a judgement, not a fact about the repository.
- "The spec" is `dynamic_harness_design_spec.md` in the repository root. "The article" is the first-version design
  article in Persian kept outside the repository. Harness² means the public repository
  `github.com/google-research/harness2`; its files were read as untrusted data and were never executed.
- Sizes are rough, for one developer working with Claude Code: S = up to 2 days, M = 3 to 5 days, L = 1 to 2 weeks.

## Summary

1. The safety kernel is built and tested: 1,724 tests collected, 1,716 pass on macOS. The learning side is not built:
   no recipes, no lessons, no mistake checks, no jurisdiction rules, no proactive data requests exist in code.
2. **A new user cannot finish any task above the quick-answer tier today.** The published loop-contract plugin
   (1.1.0 on its `origin/main`) does not contain the gate flags that dynamic-harness requires. The flags exist only on
   a local, unpushed branch. This is the first blocker, ahead of all learning work.
3. No end-to-end run that ends in a signed "Checked" result is documented. Public claims must wait for one.
4. Country-specific logic (Toman and Rial, the Jalali calendar, Persian and Iranian patterns) is written into about
   fifteen code modules. The owner's decision "nothing hardcoded to one country or one user" requires moving it to data.
5. The recommended design keeps the caller's frozen kernel and learning shell, with five changes: mistake checks are
   declarative data and not generated code; agent-drafted executable checks never act as oracles; no live
   Harness²-style loop is built in this release; recipes and an owner-acceptance signal come before any improvement
   loop; and jurisdiction rules lower the result state when unconfirmed or expired.
6. Eleven phases, P0 to P10. A usable public beta is possible after P2. The learning claims need P3 to P6 plus the
   trimmed evaluation in P9.

---

## 1. Verified current state

### 1.1 Repository and release state

| Fact | Evidence |
|---|---|
| Branch `main`, working tree clean, one commit ahead of `origin/main` (`8cf9320` is unpushed; `origin/main` is `00fb529`) | [V] `git status`, `git log` |
| Plugin version 0.4.0, MIT licence, no dependency field in the manifest | [V] `.claude-plugin/plugin.json` |
| Python 3.9 standard library only; hooks run as `/usr/bin/python3 -I` | [V] `hooks/hooks.json`, `CONTRIBUTING.md` |
| Every hook has `onFailure: "block"` | [V] `hooks/hooks.json` |
| Minimum Claude Code version 2.1.295; minimum loop-contract 1.1.0 | [V] `lib/harness_core/session.py:33-34` |
| Languages shipped: English and Persian | [V] `lib/harness_core/templates.py:21`, `templates/en.json`, `templates/fa.json` |
| `README.md` says a live end-to-end run to a signed "Checked" is "Being verified" | [V] `README.md:181` |
| `docs/faq.md` calls the signed "Checked" run "still an open check for this release" | [V] `docs/faq.md:55-58` |

### 1.2 Test result

Command (run read-only, with all temporary files in the session scratchpad):

```
TMPDIR=<scratchpad>/pytest-tmp PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m pytest -q -p no:cacheprovider \
  --basetemp=<scratchpad>/pytest-tmp/base /Users/sam/Project/dynamic-harness/tests
```

Result [V]: `1716 passed, 7 skipped, 1 error in 191.07s`; 1,724 tests collected; exit code 1.

- The 7 skips are Linux bubblewrap tests (`tests/test_sandbox_exec.py:364-424`). They cannot run on macOS.
- The 1 error is `tests/test_state_machine.py::test_follows_must_name_a_finished_task`, at fixture setup:
  `config check failed: cannot open safely: .../.remember/tmp/save.lock/pid`. Cause: `session.tree_hash` walks every
  file under the plugin root except four directory names (`session.py:37`, `session.py:40-48`). Another tool's
  short-lived lock file under `.remember/` disappeared during the walk. A rerun of that test file passed
  (`167 passed, 7 skipped` for four files). The error is a flake, but it shows a real defect: the plugin hash covers
  untracked files in a development checkout (see P0).
- The documents give three different counts: "about 1,700" (`README.md:180`), "About 1,670"
  (`docs/development.md:12`) and 1,091 (spec, lines 2160-2164). The real count is 1,724.

### 1.3 What is built

| Area | What exists | Evidence |
|---|---|---|
| Tiers | T0 (quick answer), T1, T2, REFUSE, chosen by code from a compiled task card | [V] `lib/harness_core/tier_select.py`, `control/defaults/tiers.json`, `probe.py:128` |
| Routing | A pre-filter on each prompt returns `T0-fast` or `card-needed` | [V] `probe.py:151-155`, `prompt.py:169-199` |
| Task card | Draft, validate, normalise, compile; 13 task types; frozen scope; budgets | [V] `skills/task-card/SKILL.md`, `compile.py` |
| Packs | Four packs with 12 registered checks: finance (4), model (4), operations (2), research (2) | [V] `skills/*/manifest.json` |
| Check protocol | A check runs in the OS sandbox and receives `{check_id, unit, attempt, args, inputs, work, run, business, card, today}` | [V] `run.py:191`, `checklib.py:53-78` |
| Gate and receipt | The gate replays cited outputs, folds the ledger through loop-contract, and signs a receipt with an HMAC key | [V] `gate.py:225-319` |
| Result states | "Checked", "Done, not fully checked: reasons", "Partly done: N of M" | [V] `gate.py:301-319` |
| Receipt caps | A list of caps is bound into the receipt signature; any cap lowers "Checked" to "Done, not fully checked" | [V] `gate.py:280-288` |
| Trust model | Model-facing tools only print a request; hooks are the only writers; the event log is hash-chained | [V] `posttool.py:13`, `writers.py`, `events.py` |
| Sandbox | macOS `sandbox-exec`, Linux bubblewrap | [V] `sandbox_exec.py:1-30` |
| Owner commands | `business`, `config`, `memory`, `status`, `export`, `off`, plus `setup`; each change needs a one-time code valid for 600 seconds | [V] `admin.py:30-31` |
| Business layer | `profile.json`, `metric_dictionary.json`, `source_registry.json`, `protected_terms.json`; hashed and tamper-checked | [V] `business.py:1-13`, `business.py:129-152` |
| Metric definitions | The owner's plain approval of a rendered definition writes a dictionary entry | [V] `contact.py:219-232`, `business.py:222-229` |
| Source registry | `harness-answer` registers each figure source with its `as_of` date | [V] `answer.py:129-133`, `business.py:249-257` |
| User memory | Four closed preferences: tone, format, report style, rounding | [V] `memory.py:22-23` |
| Corrections | A prompt that matches the open task is logged as a correction | [V] `prompt.py:177-183`, `corrections.py` |
| Follow-up tasks | A new card may name a finished task in `follows` | [V] `compile.py:124-126`, `prompt.py:186-189` |
| Research | Owner-approved plan, egress grants, fixed freshness rules, two independent sources for load-bearing claims | [V] `research.py:53-74`, `claims.py:159`, `docs/packs.md` |
| Dated assumptions | Model assumptions carry `as_of`, `owner_confirmed` and a 90-day `expires_at` | [V] `model_cmd.py:116-118` |
| Decision record | One redacted record per task under `records/<job>.jsonl` | [V] `record.py:1-16` |
| Shadow mode | A second card draft is logged without changing the result; only `contacts` and `tripwire` can be shadowed | [V] `shadow.py:1-40`, `enforcement.py:1-20` |
| Version 1 is read-only | 15 of 18 action classes are denied; only `local_reversible`, `draft_create` and `read` are allowed | [V] `control/defaults/classes.json` |
| Setup | Asks before changing anything; separates "GLOBAL" from "This folder only"; refuses `/`, the home folder and its parents | [V] `setup.py:423-428`, `setup.py:542-550` |

### 1.4 What is not built

All of these were checked by searching `lib/`, `skills/`, `control/`, `bin/` and `agents/` [V]:

- **No experience store.** The path `${CLAUDE_PLUGIN_DATA}/experience/**` is only read-protected
  (`control/defaults/protected_paths.json:24`). No code reads or writes it.
- **No recipes, golden cases or owner-acceptance signal.** A search for `golden`, `recipe`, `template_id`,
  `saved template` and `accepted_by_owner` finds nothing.
- **No mistake checks.** Corrections are stored and never change later behaviour (`corrections.py:6`: "Nothing reads
  these rows to change behaviour in v1"). The only reader works inside the same task (`reunderstand.py:51`).
- **No jurisdiction logic.** `jurisdiction`, `work_week` and `owner_name` appear only as profile field labels
  (`business.py:63`, `business.py:68`). No code reads them.
- **No person model.** Every memory row has `"person_id": "owner"` (`memory.py:65`, `admin.py:200`); the decision
  record's requester defaults to "owner" (`record.py:88`).
- **No proactive message.** `session.on_start` returns an empty result when all checks pass
  (`session.py:190-205`). There are no timers.
- **The model is not given the business layer.** `business.describe` has one caller, the owner command
  (`admin.py:125`). Business data reaches results only through checks (`run.py:191`), the card's currency
  (`compile.py:76`) and the answer header (`answer.py:78-103`).
- **No `harness-estimate`, `harness-doc` or `harness-decide` tool**, although the spec names them (spec lines
  2369-2371).
- **No `tests/e2e` directory**, although the spec's verification plan names one (spec lines 2467-2474).

### 1.5 The first blocker: the loop-contract release

Checked by read-only git commands in `~/Project/loop-contract-skill` [V]:

| Ref | Commit | Plugin version | Gate flags present (of 12 required) |
|---|---|---|---|
| local `main` | `bafd972` | 1.0.0 | 0 |
| `origin/main` | `9d13d9a` | 1.1.0 | 0 |
| local branch `feat/gate-1.1.0` (not pushed, no tags) | `f98ddf3` | 1.1.0 | 12 |

- The installed plugin on this machine is `loop-contract 1.1.0` at commit `9d13d9a` (`~/.claude/plugins/installed_plugins.json`).
  It has none of the flags.
- dynamic-harness checks both the version and the flag text (`session.py:168-183`). With the published plugin,
  SessionStart reports a loop-contract problem (`session.py:223-224`) and setup stops with "the loop-contract plugin
  (>= 1.1.0) was not found" (`setup.py:427-428`).
- The local branch and `origin/main` both changed `.claude-plugin/marketplace.json`, `.claude-plugin/plugin.json`,
  `skills/loop-contract/scripts/fold_ledger.py` and `skills/loop-contract/SKILL.md`. A rebase will need manual conflict
  work.
- The version number 1.1.0 is already used by a release without the flags. The gate release must use a higher number.

### 1.6 Country-specific logic in code

The owner decided that country, language, currency, calendar and person are data. Today they are partly code [V]:

- Currency: `business.py:59` (currency table), `business.py:80`, `business.py:170` (Toman to Rial factor of 10),
  `admin.py:114-118`, `finance.py:65, 162, 183, 211, 233-242`, `claims.py:273`, `model.py:260-271, 473, 555`,
  `model_cmd.py:102`.
- Calendar: `business.py:83-84` (only `jalali` or `gregorian`), `answer.py:88` (a Toman or Rial figure defaults to the
  Jalali calendar), `localize.py`.
- Personal-data patterns: `pii.py:7-8, 24, 34-35, 43, 86-87`; `memory.py:26` (a URL pattern that names `.ir`).
- Language: `templates.py:21` (two languages), `lint_text.py:40-44`, `protected.py:53, 71`,
  `control/defaults/state_lexicon.json`, `control/defaults/probe.json` (money and quantity words), `probe.py:111`.
- Examples in skills and documents use Toman, Esfand and Persian (`skills/task-card/SKILL.md:28, 101`,
  `docs/getting-started.md:100-102`, `docs/owner-guide.md:38-40`).

### 1.7 Usability friction on the normal path

- The pre-filter asks for a task card when the prompt has any digit, quantity word, money word, file name, action verb
  or decision keyword (`probe.py:141`, `probe.py:151-155`) [V]. Many harmless questions therefore need a card.
- The Stop hook blocks a quick answer that has a money word and any digit anywhere in the text
  (`stopgate.py:113-120`) [V].
- `AskUserQuestion` is absent in `claude -p`, and its behaviour in an interactive session was never tested
  (`spike/RESULTS.md`) [V]. Start questions and preference capture depend on it (`memory.py:1-12`).
- One owner prompt makes one task; repeat work starts from an empty card each time [V] (no recipe code exists).
- Setup is blocked today by the loop-contract state in 1.5 [V].

### 1.8 Corrections to the brief

Each item below is a point where the files differ from the claims given to this planning task.

| # | Claim in the brief | What the files show |
|---|---|---|
| 1 | Several commits are unpushed | Only `8cf9320` is ahead of `origin/main` [V] |
| 2 | User memory is written only by the PostToolUse hook | There is a second writer: the typed owner command `/dynamic-harness:memory` (`admin.py:181-223`, row at `admin.py:200`) [V] |
| 3 | Nothing reads corrections | True for later tasks. One reader exists inside the same task (`reunderstand.py:51`) [V] |
| 4 | The evaluation plan has eight stages | Eight numbered stages in eleven rows: 1, 2, 3, 3b, 3c, 4, 4b, 5, 6, 7, 8 (spec lines 1805-1822) [V] |
| 5 | Stages 2 to 8 were not run and there is no live run | Live `claude -p` runs did happen (`CHANGELOG.md:17-29`, spec line 2296). What is missing is a documented run that ends in a signed "Checked" [V] |
| 6 | 16 command-line tools in `bin/` | 15 model-facing tools plus the hook entry `harness-hook` [V] |
| 7 | loop-contract only needs its branch released as 1.1.0 | `origin/main` is already 1.1.0 without the flags; the branch needs a rebase and a higher version (section 1.5) [V] |
| 8 | Three identical Stop blocks end a request | The code counts blocks per rule and also stops at six blocks in total (`stopgate.py:85`). `hooks/hooks.json` describes only the first rule [V] |
| 9 | Harness² budget "4k / 3k" | It is 4 × k rollouts and 3 × k improvement stages, where k is the recursion budget; retries, selection and reflection are counted separately (Harness² `core/ledger.py`) [V] |
| 10 | Spec-versus-build drift list | Confirmed, with additions: the spec says three model checks in one place and four in another (lines 2358-2364 and 2459-2460); it mentions vendored `openpyxl` and `formulas` although the build is standard library only; `INTERFACES.md` says "there is no `writers.py`" although the file exists; `posttool.py:13` names a `business` tool that does not exist; the `business.py:12-13` docstring names a `business_cmd` module that does not exist (the writer is `admin.py`); `control/defaults/approved_procedures.json` carries "unreleased (after 0.4.0)" labels [V] |
| 11 | The two copies of the spec are the same | The copy in `~/Downloads` (2,473 lines) is slightly older than the repository copy (2,475 lines) [V] |

Claims that were confirmed without change [V]: tiers T0, T1, T2 and REFUSE; four packs and 12 checks; four closed
preferences; `person_id` is always "owner"; no code reads `jurisdiction`; `harness-answer` registers sources; the Stop
rule on a money word plus a digit; version 1 is read-only; a SessionStart hook exists and there are no timers.

### 1.9 Harness² facts used in this plan

Read from the repository files only. The paper itself and `harness2/core/prompts.py` were not read.

| Fact | Evidence in the Harness² repository |
|---|---|
| Eight editable components, including guardrails and plugins | [V] `README.md:70-73`; baseline harness YAML (`guardrails`, `plugins_dir`) |
| Two levels: a task-level Propose-Probe-Compose loop "without ground-truth feedback" and a harness-level improvement loop | [V] `README.md:27-33` |
| Cost per task: 4 × k rollouts and 3 × k improvement stages | [V] `core/ledger.py` |
| The final answer is picked by a model among anonymised candidates; the base run is not in the pool; there is "no abstention by construction"; with k = 1 there is no selection | [V] `core/selection.py` |
| The judge is outside the method: "Nothing in the method imports this module" | [V] `core/judge.py:15-19` |
| Lessons are stored as cue, lesson, evidence, confidence; an entry without evidence is refused; lessons go to the improver session, not to the executor | [V] `core/library.py`, `core/selection.py` |
| The executor's network is unrestricted and Codex runs with its sandbox disabled | [V] `README.md:166-171` |
| The improver's Claude session runs with `--dangerously-skip-permissions` | [V] `core/proposer.py:201-207` |
| One rollout downloaded its own grading rubric without being told to | [V] `docs/EXPERIMENTS.md:95-98` |
| Reported gains: up to 11.5 points on WorkBuddy-Bench, 4.5 points on JobBench, 11.8 percentage points on LAB partial pass rate | [V] `README.md:41-45` (their claim; not reproduced here) |

---

## 2. Need-by-need coverage

| Owner need | Exists today | Missing | Closed by |
|---|---|---|---|
| A harness built for each need | A compiled task card sets scope, units, oracle, budget and questions; the tier and a per-type profile pick the checks (`gatehook.py:50-64`) | Reuse of earlier accepted work; automatic use of the business's facts, rules and guards; an honest path when no pack fits | P5 (recipes, assembly), P7 (provisional parts) |
| Communicates with the user | Up to 3 start questions and 5 blocking contacts; definition confirmation; cost contacts; three plain result states; English and Persian templates | A tested interactive question path; a text fallback when `AskUserQuestion` is absent; languages as data; a view of what the tool has learned | P0 (test), P1 (language data), P2 (fallback), P3 (learned view) |
| Learns from the user and remembers | Four preferences; metric definitions; source registry; a corrections log that nothing uses | Facts, guards, recipes and lessons; promotion rules; expiry; a forget and export path for each | P3, P4, P5, P7 |
| Does not repeat a mistake | Nothing | A check at the gate, created from a correction, that fails the same mistake next time | P4 |
| Searches | Research pack with an owner-approved plan, egress grants, freshness rules and two-source rule | A documented live run; research that proposes dated rules for owner confirmation | P0, P6 |
| Reads data | Data-analyst agent; table and spreadsheet readers (`tables.py`, `xlsx.py`); sandboxed checks; source registry | A tested list of supported file formats; a recorded path for figures typed in the prompt | P2 |
| Calculates financial figures from the prompt or files | Finance pack (reconcile, KPI, bridge, list); model pack (workbook, recalculation, checks, report); `harness-answer` prints a figure with its basis | Currency and calendar as data; a recorded path for owner-typed inputs; an end-to-end "Checked" run | P0, P1, P2 |
| Takes the user's location into account | A `jurisdiction` profile field that no code reads | A dated rules store keyed by jurisdiction and topic; delivery to checks; a lower state for unconfirmed or expired rules | P1 (field becomes live), P6 |
| Asks for data based on past data | The source registry records each source's `as_of` date | A recipe's required inputs and cadence; a comparison with source dates; a short list shown at session start | P5 |

---

## 3. Target architecture

Everything in this section is a design proposal [I] built on the verified mechanisms in section 1.

### 3.1 Two parts: a frozen kernel and a learning shell

**Frozen kernel.** Never learned, never edited by the model, changed only by a release with new hashes:

- hooks, the gate, tier selection, budgets and stall rules;
- the receipt, the event log and the key;
- the sandbox, permissions, egress control and protected paths;
- action classes and the approval rules;
- the pack loader and every executable check (`packs.py:95-116`);
- the rule that an oracle must be a registered, hashed check (`gatehook.py:58-64`).

This matches the spec's list of things that are never learned (spec lines 1151-1173).

**Learning shell.** Data only. Each store has one schema, one trusted writer (a hook), one promotion rule and one
defined effect. No store can change the kernel. No store entry is ever created from fetched content or file content
alone (spec lines 1356-1357).

### 3.2 The stores

All stores live under `${CLAUDE_PLUGIN_DATA}`, which is write-protected for every tool. The business-layer stores are
covered by the existing tamper check (`business.guard` and `business.baseline`, `business.py:129-152`). The
experience stores get the same treatment as a new hashed layer `experience` in `session.current_hashes`
(`session.py:53-63`).

| Store | File | Status | Who proposes | Who confirms (promotion rule) | How it takes effect |
|---|---|---|---|---|---|
| Profile | `business/profile.json` | exists | Owner | Owner command with code | Checks receive it in `business`; answer header; card currency |
| People | `business/people.json` | new | Owner | Owner command with code | Sets the active person; preferences and records carry that id |
| Preferences | `memory.jsonl` | exists | Structured answer or owner command | Written at once (closed values only) | Pinned into the card at compile (`compile.py:81, 140`) |
| Metric dictionary | `business/metric_dictionary.json` | exists | Model renders a definition | Owner's plain approval (`contact.py:219-232`) | Checks and answer header |
| Facts | `business/facts.json` | new | Model, from the owner's own turn | Owner's plain approval of the rendered fact; one confirmation is enough | Checks receive it in `business`; applicable facts are listed in the card's `assembly` block |
| Source registry | `business/source_registry.json` | exists, extended | Code, from check outputs | None (code-derived) | Freshness checks; proactive data requests |
| Jurisdiction rules | `business/rules.json` | new | Research task or owner | Owner's plain approval; each rule has `as_of` and `expires` | Checks receive it in `business`; an unconfirmed or expired rule used by a task adds a receipt cap |
| Mistake guards | `business/guards.json` | new | Model, after a correction | Owner's plain approval, and the guard must fail on the corrected mistake and pass on the fixed result | Evaluated by kernel code at the gate; a failing guard fails the unit |
| Recipes | `experience/recipes/<id>.json` | new | Hook, from an accepted task | Owner types the keep command | Pre-fills the next matching card |
| Golden cases | `experience/golden/<job>/` | new | Hook, with the recipe | Same as the recipe | Input for offline replay (P8) and regression tests of guards |
| Lessons | `experience/lessons.jsonl` | new | Model or offline replay | Owner approval; evidence is required | Shown to the card drafter as notes; never changes a check, tier, budget or permission |
| Corrections log | `runs/<job>/…` | exists | Hook | None (raw evidence) | Source material for facts, guards and lessons |
| Open items | `open_items.json` | new | Hook | None (computed) | Printed at session start |

Schema outlines (JSON; names are proposals):

```
harness/people@1     {people: {<id>: {name, role, language, added_turn}}, active_default: <id>}
harness/facts@1      {facts: {<id>: {kind: "value"|"convention"|"mapping", key, value, unit, applies_to: {metrics, sources, task_types},
                      status: "confirmed"|"retired", as_of, expires, source: {turn_id, job}, confirmed_by, supersedes}}}
harness/rules@1      {rules: {<id>: {jurisdiction, topic, value, unit, effective_from, effective_to, as_of, expires,
                      sources: [{url, title, date, sha256}], status: "proposed"|"confirmed"|"retired", confirmed_by, confirmed_turn}}}
harness/guards@1     {guards: {<id>: {text, applies_to: {task_types, metrics, sources, checks}, assert: {op, left, right, tolerance},
                      origin: {job, correction_turn}, proof: {failed_on, passed_on}, status: "active"|"retired", hits, last_hit}}}
harness/recipe@1     {id, title, task_type, card_template, inputs: [{source_id, role, cadence, period_rule}], checks, questions_answered,
                      origin_job, accepted_turn, uses, last_used, status}
harness/lesson@1     {id, cue, lesson, evidence: [{job, check, value}], confidence, scope, status: "proposed"|"approved"|"retired", approved_turn}
harness/open_items@1 {computed_at, items: [{kind, text_key, args, due}]}
```

Guard assertions use a closed set of operators: `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `in`, `not_in`, `abs_diff_le`,
`ratio_between`, `present`, `unit_is`, `period_is`, `sign_is`. Operands are literals or JSON pointers into the
recorded check values, the compiled card and the confirmed facts. There are no regular expressions and no code.

### 3.3 How a harness is assembled for one need

1. The owner's prompt arrives. The pre-filter decides quick answer or card (`prompt.py:169-199`).
2. New step, in the UserPromptSubmit hook: match the prompt against kept recipes (task type, metric ids, source ids,
   recipe title words). On a match, the hook writes a pre-filled draft card to `.claude/loops/drafts/<id>.json`
   (an existing write exception in `control/defaults/protected_paths.json`) and tells the model to start from it.
3. The model completes the card. `harness-compile` validates it as today.
4. New step, in compile: an `assembly` block is added to the compiled card. It lists the id and hash of each fact,
   rule, guard, recipe and lesson that applies. This pins the versions used, the same way `memory_snapshot` is pinned
   today (`compile.py:138-140`).
5. Execution and checks run as today. Checks see facts and rules through the existing `business` and `today` fields
   (`run.py:191`).
6. At the gate, kernel code evaluates the applicable guards against recorded check values, then adds harness-side caps
   (expired rule, unconfirmed rule, provisional part) to the caps list before the receipt is signed
   (`gate.py:280-288`).
7. When no pack check fits the need, the result can still be produced, but the state is "Done, not fully checked",
   as it is today for a non-executable oracle. The gap is written to a `pack_gaps.jsonl` log so maintainers can see
   which checks are worth building.

### 3.4 Trust ladder for generated parts

| Level | What it is | Can it lead to "Checked"? | How it moves up |
|---|---|---|---|
| 0 | Raw evidence: corrections, fetched pages, agent-written scripts | No. Never an input to a tier, a check or a store | It does not move; it can only be cited as evidence |
| 1 | Proposed entry: a fact, rule, guard, lesson or recipe waiting for the owner | No. If a task uses it, the receipt gets a cap | The owner confirms in the owner's own turn |
| 2 | Confirmed data: facts, rules, guards, recipes, approved lessons | Yes, through registered checks and the guard evaluator | Stays until retired, superseded or expired; expiry moves it back to level 1 |
| 3 | Provisional prose skill drafted by the agent for a need no pack covers | No. Always adds the cap "provisional method" | An operator reviews it and ships it in a release |
| 4 | Released pack: skill, manifest and hashed checks | Yes | Existing release path (`tools/release_hashes.py`, `docs/development.md:56-143`) |

An executable check written by the agent stays at level 0 in this release. It can be attached to a pull request as a
proposal for a maintainer. It never runs as an oracle.

### 3.5 People and jurisdictions as data

- **People.** Claude Code gives the plugin no authenticated identity. A person is a named profile that the owner adds
  and selects with an owner command and a code. Preferences, corrections and records carry that person id. The
  documents must say plainly that this is a label for convenience and audit, not access control. Approval rules per
  person (the "approval matrix") stay out of scope.
- **Locale packs.** `control/defaults/locales/<id>.json` holds everything that differs by language or country:
  currency codes and names, related currency pairs with their factor (for example a pair with factor 10), number and
  digit folding, question marks, money and quantity words, state lexicon, personal-data patterns, date formats and
  the calendar name. Code reads the active packs named in the profile and has no branch on a specific country,
  currency or language value.
- **Calendars.** A calendar conversion is an algorithm, so it stays in code as a small registry of calendars. Which
  calendar is used is data. Adding a new calendar needs a code release; this limit must be stated in the documents.
- **Jurisdiction rules.** No country's rules ship with the plugin. The rules store starts empty. A research task
  proposes a rule with its sources and dates; the owner confirms it. Several jurisdictions can be held at once; a card
  names the one it uses, with the profile's value as the default.

### 3.6 Protection of memory

| Risk | Control |
|---|---|
| Injected entry (a web page or a file tells the model to "remember" something) | A proposal is accepted only when its source is an owner turn id recorded by the UserPromptSubmit hook. The hook checks that the turn exists and that the proposed text shares content with it. Fetched and file content can be cited as evidence, never as the source |
| Entry confirmed by mistake | Each confirmation shows the entry in plain words before the owner approves. Every entry can be retired by an owner command. `export` lists all entries |
| Stale entry | Facts and rules carry `as_of` and `expires`. An expired entry used by a task adds a cap. The open-items list shows entries that expire soon |
| Wrong guard that blocks correct work | A guard must prove itself before activation (fail on the bad result, pass on the fixed one). Each guard counts its hits. The failure message names the guard and the command that retires it |
| Tampering outside the harness | The business and experience layers are hashed; a mismatch stops work as it does today (`business.py:129-140`, `session.py:232-238`) |
| Silent drift between runs | The card's `assembly` block pins ids and hashes of everything used; the decision record keeps them |
| Unbounded growth | Hard caps per store (for example 200 facts, 100 guards, 50 recipes, 200 lessons); the oldest unused entries are offered for retirement |
| Personal data in stores | Entries pass through the existing redaction (`pii.py`) before they are written |
| Leak of evaluation answers into learning | Golden cases store accepted outputs under the read-protected experience path. The executor never reads them. This copies the Harness² rule that grader verdicts must not appear in improver context |

### 3.7 Where a Harness²-style loop runs

- **Not in live tasks.** No Propose-Probe-Compose loop runs while the owner waits.
- **Offline only (P8).** The owner starts it with a command. It replays golden cases in the sandbox. It may propose
  changes to two things only: the text of prose skill notes, and recipe parameters. A candidate is kept only when
  every golden case still passes its registered checks and all guards, and at least one case improves on a measure
  fixed before the run (fewer failed attempts or fewer questions). No model picks a winner. The owner sees the budget
  before the run and the diff after it, and approves or rejects.
- **If a live deep path is ever added,** it should follow the spec (lines 1104-1124): independent repeats in fresh
  context, candidates filtered by the executable oracle first, a selector that only breaks ties among passing
  candidates, no passing candidate means blocked, and no deep path without an executable oracle.

### 3.8 Where this plan disagrees with the caller's position

1. **Mistake checks should be data, not generated code.** The caller wrote "executable mistake checks". The kernel
   denies self-written execution (`control/defaults/classes.json`), accepts only registered checks as oracles
   (`gatehook.py:58-64`), and the spec states that a script the agent wrote is only evidence. A guard is therefore a
   declarative assertion evaluated by one piece of frozen code. This keeps the promise without opening a path for
   model-written code to decide a result.
2. **The promise "cannot be signed off again" must be narrowed.** It holds only for a mistake that can be written as
   an assertion over check values, card fields and facts. A correction about style, judgement or wording becomes an
   approved lesson. The documents and the article must say this.
3. **Drafted checks should not become oracles through evidence alone.** The caller allowed new components to be
   promoted by evidence. For prose skills and recipes this is fine with a cap. For executable checks, promotion stays
   a human release step. Passing its own golden cases does not show that a check is correct.
4. **No live loop in this release, including the opt-in deep path.** The cost is 4 × k rollouts plus 3 × k sessions
   per task; selection has no ground truth and no way to abstain; and the article itself says Propose-Probe becomes a
   default only if it beats independent repetition at equal cost. That comparison (evaluation stage 6) has not been
   run. Building the path first would be work without evidence.
5. **Recipes and an acceptance signal come before any loop.** No code today records that the owner accepted a result.
   Without that signal there are no golden cases, and without golden cases an offline loop has nothing to run on.
6. **One confirmed correction is enough for a fact, as the caller said, but the spec's threshold of 20 corrections
   should stay for changes to skill text.** The two are different risks: a fact is narrow and owner-stated; a skill
   edit changes behaviour broadly.
7. **Approved lessons may reach the executor.** Harness² gives lessons to the improver only. Here an approved lesson
   is shown to the card drafter, because the owner has approved it. Unapproved lessons never reach any model session
   that does task work.
8. **Jurisdiction rules need a state consequence.** A rule store alone does not protect the owner. A task that uses an
   unconfirmed or expired rule must end as "Done, not fully checked", and tax, legal and employment results stay
   "prepared, not final" (`docs/owner-guide.md:72`).
9. **Open items must be computed at session start, not only stored.** Whether a source is late depends on today's
   date. The computation is a date comparison over three small files and fits the 5-second hook limit. It must fail
   silently, because every hook is set to block on failure.
10. **Multi-person support cannot claim identity.** It is a named profile selected by the owner. Anything stronger
    needs a host application, which the owner has ruled out for now.
11. **Learning is not the first task.** The first task is to make the product work for a new user (section 1.5) and to
    record one signed "Checked" run.

---

## 4. Phased work plan

Order and dependencies:

```
P0 -> P1 -> P2 -> P3 -> P4 -> P5 -> P6 -> P7 -> P8
                   \________________________/
P9 (evaluation) starts after P2 and runs alongside; its learning parts need P3 to P6.
P10 (release and launch) closes each release point: after P2, after P6, after P8.
```

Rules for every phase: standard library only; every new model-facing action prints a request and a hook writes; each
new user-facing text is added to all template files; each new file under the plugin root is covered by the release
hashes; tests are written first for each acceptance criterion.

### P0. Unblock and prove (size M)

**Goal.** A new user can install, set up and finish one task with a signed "Checked" result.

**Work.**

1. loop-contract (separate repository):
   - Rebase the three commits of `feat/gate-1.1.0` onto `origin/main`; resolve conflicts in the four files named in
     section 1.5.
   - Release as **1.2.0**, tag it, and update its marketplace entry.
   - Add a test in that repository that asserts all 12 gate flags exist in `fold_ledger.py`.
2. dynamic-harness:
   - `lib/harness_core/session.py:33`: `MIN_LOOP_CONTRACT = (1, 2, 0)`.
   - `lib/harness_core/setup.py:427-428`: the message names 1.2.0 and prints the two install commands.
   - `lib/harness_core/session.py:37-48`: the plugin hash covers only the directories a release ships
     (`.claude-plugin`, `agents`, `bin`, `commands`, `control`, `hooks`, `lib`, `schema`, `skills`, `templates`) and
     tolerates a file that disappears during the walk by retrying once, then failing closed.
   - Push `8cf9320` (owner action).
   - Add `tests/e2e/` with one scripted `claude -p` run per pack in a temporary set-up folder. The tests are skipped
     unless an environment variable opts in, because they cost money.
   - Run one interactive session by hand and record: whether `AskUserQuestion` works with the hooks, the shape of its
     answer, and whether a SessionStart message is visible to the owner. Save the transcript and the receipt check as
     `docs/evidence/first-checked-run.md`.
   - Run the full suite on Ubuntu 22.04 and 24.04 with bubblewrap so the 7 skipped tests run.
   - Fix the small drift that misleads users now: the test count in `README.md:180` and `docs/development.md:12`; the
     `hooks/hooks.json` stall description; `docs/security.md:142-143`.

**Acceptance.**

- On a clean machine: install loop-contract 1.2.0, install dynamic-harness, run setup, ask for one finance task with a
  sample file, receive "Checked"; the receipt's HMAC verifies with `harness-gate`'s own verification.
- `python3 -m pytest -q` passes with no error on macOS when `.remember/` and `.ruff_cache/` exist in the checkout.
- The suite passes on both Ubuntu versions with 0 skips in `tests/test_sandbox_exec.py`.

**Risks.** The rebase may show that `origin/main`'s new coordination code and the gate flags interact. The interactive
`AskUserQuestion` path may not work under the hooks; if so, P2's text fallback becomes the main path. Narrowing the
hash to shipped directories is a security-relevant change and needs the review in section 5.

### P1. Locale and person as data (size L)

**Goal.** No code path depends on one country, currency, calendar, language or person.

**Depends on.** P0.

**Work.**

- Create `control/defaults/locales/` with `en.json` and `fa.json` (language data) and move into them: money and
  quantity words from `probe.json`, the state lexicon, digit folding, question marks, date formats.
- Create a currency table in data: code, name, decimal places and related pairs with a factor. Remove the literals at
  the lines listed in section 1.6 (`business.py:59, 170`, `admin.py:114-118`, `finance.py`, `claims.py:273`,
  `model.py`, `model_cmd.py:102`).
- Calendar registry: `localize.py` exposes calendars by name; `answer.py:88` uses the profile's calendar and no longer
  infers one from a currency; `business.py:83-84` accepts any registered calendar.
- Personal-data patterns: `pii.py` loads pattern sets from the active locale packs; a general set (email, card
  number, IBAN) always applies.
- `templates.py:21`: the language list comes from the files in `templates/`.
- People: add `business/people.json`; add `business person add|use|remove` to the owner command in `admin.py`; replace
  the literal "owner" at `memory.py:65`, `admin.py:200`, `record.py:88` and `business.py:229` with the active person.
  Existing rows with "owner" stay valid.
- Rewrite examples in `skills/task-card/SKILL.md` and the documents so that they show two different regions.

**Data contracts.** `harness/locale@1`, `harness/people@1` (section 3.2). The profile gains `locales: [..]`.

**Tests.** A test that searches `lib/` for the strings `IRT`, `IRR`, `jalali`, `Toman`, `Rial`, `.ir` and `fa` and
fails if any appears outside data files and tests. Parametrised runs of the finance and model checks with at least
three currency pairs and two calendars. A migration test: a data folder created by 0.4.0 still loads.

**Acceptance.** The search test passes. All existing tests pass with the Persian behaviour now coming from data. A new
locale pack added in a test, with no code change, changes currency names, money words and personal-data patterns.

**Risks.** This touches about fifteen modules, including the personal-data redaction, which is security-relevant. Move
one concern at a time and keep the existing tests green at each step. A weak pattern pack must not reduce redaction:
the general set stays in code.

### P2. Normal-path friction (size M)

**Goal.** Ordinary questions get quick answers; real work gets a card; the path works without `AskUserQuestion`.

**Depends on.** P0, P1.

**Work.**

- Pre-filter (`probe.py:151-155`): stop treating a bare number or quantity word as a reason for a card when there is
  no money token, file, connector, action verb or decision keyword. Before switching, log the new decision next to the
  old one for real prompts, using the existing shadow pattern (`shadow.py`), and switch only when P9's E4 threshold is
  met.
- Stop money rule (`stopgate.py:113-120`): require a number next to a money word or currency code, and ignore figures
  that repeat a figure from the owner's own prompt in the same turn.
- Text fallback for questions: when `AskUserQuestion` is not available, the contact is printed as numbered choices and
  the owner's next plain reply resolves it, using the pending-contact route that already exists (`prompt.py:163-168`).
- Owner-typed inputs: figures the owner types in a prompt are saved by the UserPromptSubmit hook as an input file with
  the turn id, and registered as a source with role `owner_stated`. Checks can then cite them.
- Supported inputs: add a test file for each format the readers accept and state the list in `README.md` and
  `docs/faq.md`.
- Messages: each block message names the next command; the setup message for a missing loop-contract gives the exact
  commands.

**Acceptance.** On the E4 prompt set, quick-answer routing rises and no request that needs a card is routed to a quick
answer. A finance task with figures typed in the prompt ends in "Checked" with the owner-stated source in the answer
header. A full task completes in `claude -p` without `AskUserQuestion`.

**Risks.** Loosening the pre-filter is the main safety risk in this plan; that is why it ships in shadow first and has
a fixed threshold. The Stop rule remains as the second line.

### P3. Facts and corrections (size M)

**Goal.** One correction from the owner about a business fact is remembered and applied to every later task.

**Depends on.** P1.

**Work.**

- New tool `bin/harness-learn` (print-only; add it to `posttool.PRINT_ONLY` and remove the stale `business` name).
  Subcommands in this phase: `propose fact`.
- New module `lib/harness_core/learn.py`: validates a proposal, checks that its source turn is an owner turn, renders
  it in plain words, and opens a confirmation contact. The owner's plain approval writes `business/facts.json`. This
  reuses the definition flow (`contact.py:219-232`).
- After a correction is logged (`prompt.py:177-183`), the hook's message to the model adds: if this corrects a lasting
  fact, propose it with `harness-learn`.
- `business.snapshot` (`business.py:53-55`) includes confirmed, unexpired facts.
- `compile.py`: add the `assembly` block with ids and hashes of applicable facts.
- `gate.py`: add harness-side caps before signing; first cap: "fact <id> expired".
- Owner command: `business facts` (list), `business fact retire <id>`; `export` includes facts.

**Data contracts.** `harness/facts@1`; card field `assembly`; receipt caps from the harness.

**Tests.** Proposal with a source that is not an owner turn is refused. Proposal sourced from fetched content is
refused. A confirmed fact appears in the next task's check request. An expired fact adds a cap and the state is
"Done, not fully checked". A tampered `facts.json` stops work.

**Acceptance.** Scripted run: task 1 uses a wrong convention; the owner corrects it and confirms the fact; task 2, in a
new session, applies the fact without being told and lists it in the answer's basis.

**Risks.** Too many confirmation questions. Limit: at most one fact proposal per correction, and proposals count
against the existing contact caps.

### P4. Mistake guards (size M)

**Goal.** A mistake the owner corrected cannot receive "Checked" again, when the mistake can be written as an
assertion.

**Depends on.** P3.

**Work.**

- `harness-learn propose guard`: the model writes the assertion and a plain sentence.
- `lib/harness_core/guards.py`: the closed operator set, pointer resolution, and evaluation. No regular expressions,
  no code execution, bounded input size.
- Proof before activation: the hook evaluates the guard on the corrected task's recorded outputs. It must fail on the
  attempt the owner corrected and pass on the fixed attempt. Otherwise the proposal is refused with the reason.
- `gate.run_gate` (`gate.py:225`): evaluate active guards whose `applies_to` matches the card. A failing guard fails
  the unit. A guard that cannot be evaluated (a value is missing) adds the cap "guard <id> not evaluated".
- Owner command: `memory guards` (list with hit counts), `memory guard retire <id>`.

**Tests.** Each operator; pointer errors; the proof rule; a guard cannot reference a path outside the allowed roots; a
guard cannot be created or changed by any tool call; 100 guards evaluate within the gate's time limit.

**Acceptance.** Scripted run: plant a ×10 unit mistake; the owner corrects it; the guard is confirmed. Plant the same
mistake in a new task: the gate fails the unit and names the guard. The same task with correct data ends in "Checked".

**Risks.** A guard that is too broad blocks correct work. Controls: the proof rule, narrow `applies_to`, hit counts,
and a one-command retirement. A guard language that is too weak covers few mistakes; record every correction that
could not be expressed, and review the list in P9.

### P5. Recipes, acceptance, pre-filled card, proactive data requests (size L)

**Goal.** Repeat work starts from what worked last time, and the tool tells the owner which data it needs next.

**Depends on.** P3 (assembly block), P4 (guards are part of a recipe's checks).

**Work.**

- Acceptance signal: after a "Checked" result, the final message adds one line: type `/dynamic-harness:memory keep` to
  reuse this as a template. Only this explicit command counts. Silence is never approval.
- `lib/harness_core/recipes.py`: on keep, the hook writes the recipe (card template with task-specific values
  removed, inputs with source ids and roles, checks, answered questions) and the golden case (card, input hashes, a
  copy of inputs up to a size cap, accepted check values).
- Matching in the UserPromptSubmit hook: by recipe title words, task type, metric ids and source ids. On a match the
  hook writes the pre-filled draft and says which recipe it used. The owner can say "not this one".
- Cadence: on keep, the owner is asked once how often this work repeats (never, weekly, monthly, quarterly, yearly).
  The source registry gains `cadence` and `covers_to`.
- Open items: `lib/harness_core/openitems.py` compares each kept recipe's inputs and cadence with the registry's
  dates and today's date, and adds facts and rules that expire within 14 days and proposals that wait for the owner.
  `session.on_start` prints at most five lines when all checks pass. Any error in this step is swallowed and prints
  nothing.
- New hashed layer `experience` in `session.current_hashes`.

**Data contracts.** `harness/recipe@1`, golden case layout, `harness/open_items@1`, registry fields.

**Tests.** A recipe never stores a figure value from the accepted run in the card template. Matching is deterministic.
The open-items computation finishes within 1 second for 50 recipes and 500 sources. An exception inside it does not
block SessionStart. Golden inputs over the size cap are stored as hashes only.

**Acceptance.** Scripted run: month 1 task is kept as monthly. In a new session one month later (clock injected in the
test), the session start shows "Monthly margin report: the sales file ends on <date>; the next period needs a newer
file". The owner supplies the file and asks again; the card arrives pre-filled; the number of questions asked is lower
than in month 1.

**Risks.** Whether a SessionStart message is visible to the owner is not yet verified (P0 records it). Fallback: show
the list on the first prompt of a session. A recipe copied from one accepted run may fit the next request badly; the
owner sees which recipe was used and can reject it.

### P6. Jurisdiction rules (size M)

**Goal.** Location is taken into account through dated, sourced, owner-confirmed rules, with no country logic in code.

**Depends on.** P1, P3.

**Work.**

- `harness-learn propose rule`, sourced from a finished research task's claims (two independent sources for a
  load-bearing claim, `docs/packs.md`) or from an owner turn.
- `business/rules.json`; `business.snapshot` includes confirmed rules for the card's jurisdiction; checks receive
  them with `today`.
- Card field `jurisdiction`, default from the profile; the field that no code reads today becomes live.
- Caps at the gate: "rule <id> not confirmed", "rule <id> expired", "no rule for <topic> in <jurisdiction>" when a
  check asked for one.
- Research freshness for laws already uses the in-force date (`docs/packs.md`); the rule's `expires` defaults to the
  earlier of the source's stated end date and one year.
- Owner command: `business rules`, `business rule retire <id>`.
- Documents: tax, legal and employment results stay "prepared, not final".

**Tests.** A proposed rule is never passed to checks as confirmed. An expired rule adds a cap. Two jurisdictions in one
installation do not mix. A rule proposed from fetched content without a research task's claim record is refused.

**Acceptance.** Scripted run with two made-up jurisdictions and a rate rule for each: the same calculation gives the
right result for each; after the injected clock passes the expiry date, the result is "Done, not fully checked:
rule … expired" and the open items ask for a refresh.

**Risks.** Owners may read a confirmed rule as legal advice. The confirmation text and the answer basis must show the
source and date every time. Wrong rules from research are the owner's to confirm; the tool must show both sources.

### P7. Provisional parts and lessons (size M)

**Goal.** A need that no pack covers still gets structured help, marked honestly, and approved lessons improve card
drafting.

**Depends on.** P3 to P6.

**Work.**

- Lessons: `harness-learn propose lesson` with cue, lesson, evidence and confidence; evidence must cite a job and a
  recorded value; a lint refuses any lesson that mentions tools, addresses, recipients, approvals, budgets, tiers or
  removing a check (the spec's list). Owner approval writes `experience/lessons.jsonl`. Approved lessons whose cue
  matches are given to the card drafter in the hook's context text.
- Provisional prose skill: when no recipe and no pack fits, the model may draft a method note under the work folder.
  The hook records it; the task carries the cap "provisional method". After the owner keeps the result, the note is
  stored with the recipe.
- `pack_gaps.jsonl`: one row for each task that ended without an executable oracle, with task type and the missing
  check kind.
- `docs/development.md`: how a maintainer turns a provisional method and a pack gap into a released check.

**Tests.** The lint list; a lesson without evidence is refused; an unapproved lesson never appears in any context
text; a task with a provisional method can never be "Checked".

**Acceptance.** Scripted run: a task type with no matching pack ends as "Done, not fully checked: provisional method";
the second run of the same need starts from the stored note and recipe.

**Risks.** Lessons can turn into a second, unreviewed prompt. Controls: the lint, the cap on count, owner approval,
and that a lesson can change only how a card is drafted.

### P8. Offline improvement replay (size L, optional for the first public release)

**Goal.** Improve prose notes and recipe parameters using accepted work, without any live cost to the owner.

**Depends on.** P5, P7, and at least ten golden cases.

**Work.**

- Owner command `memory improve` shows the budget (number of replays, estimated cost from `estimate.py`) and asks for
  confirmation.
- `lib/harness_core/replay.py`: for each golden case, run the task in the sandbox against the stored inputs with the
  candidate note or parameter; judge only by registered checks and guards; compare with the fixed measure.
- No candidate is adopted automatically. The owner sees the diff and the before-and-after table and approves.
- Replay sessions cannot read the stored accepted outputs.

**Acceptance.** On a seeded set of golden cases with one deliberately poor note, the replay proposes a change that
reduces failed attempts, with every case still passing; a candidate that makes one case fail is dropped.

**Risks.** Cost, and overfitting to a few cases. Controls: the owner-visible budget, the ten-case minimum, and the
all-cases-pass rule. If P9 shows no benefit, this phase is removed and stated as such.

### P9. Evaluation (size L, runs alongside from P2)

See section 6. Output: `docs/evidence/` with one file per experiment, the raw data, and the thresholds written before
each run.

### P10. Release readiness and launch (size M per release point)

See sections 5 and 7. Three release points:

| Release | Contains | Public claim allowed |
|---|---|---|
| 0.5.0 | P0 to P2 | Works end to end; read-only; any language pack and currency; small, honest safety claims |
| 0.6.0 | P3 to P6 | Learns facts, guards, recipes and rules; asks for data; with E6 and E7 evidence |
| 0.7.0 | P7, P8 | Provisional methods, lessons, offline improvement |

---

## 5. Ready for all users

### 5.1 The loop-contract dependency

- Release 1.2.0 as in P0. Until it is published, the README must not suggest that installation works.
- The plugin manifest has no dependency field (`.claude-plugin/plugin.json`). Keep the run-time check in
  `session.loop_contract_problem`, and keep the flag-text check, because a version number alone was not enough once
  already.
- Add a continuous-integration job in dynamic-harness that installs the published loop-contract and runs the gate
  integration tests against it, not only against a sibling checkout (`CONTRIBUTING.md`).

### 5.2 Supported platforms

| Platform | Status today | Plan |
|---|---|---|
| macOS | Tests run here (Darwin 25.1.0, Python 3.9.6) [V] | Supported |
| Ubuntu 22.04 and 24.04 with bubblewrap | Claimed in `README.md:180`; not verified in this session; 7 sandbox tests skip on macOS | Verify in P0; supported after that |
| Other Linux | Not tested; `docs/security.md:137-139` lists gaps | "Should work; not verified" |
| Windows | Not supported (`docs/faq.md`) | Stays unsupported |
| WSL2 | Unverified (`docs/faq.md`) | Stays unverified unless someone tests it |

Claude Code: minimum 2.1.295 (`session.py:34`); the platform spike used 2.1.296. P0 records the newest version tested.
State in the README that `claude -p` and interactive sessions are both tested only after P0's run confirms it.

### 5.3 Setup

- Already true [V]: setup asks before any change, separates global changes from folder changes, makes the global
  change only when the owner types `confirm <code> global`, and writes folder changes to
  `.claude/settings.local.json` (`setup.py:542-550`, `docs/getting-started.md:76-88`).
- To add: the missing loop-contract message with exact commands (P0); a setup step that asks for language, currency,
  calendar and jurisdiction with no default tied to one country (P1); an uninstall section that lists every file
  setup changed.

### 5.4 Security review items before each release

1. The narrowed plugin hash (P0): confirm that no file outside the hashed directories is ever loaded or executed.
2. `harness-learn` and each new store: the model cannot write, the source-turn rule holds, and tampering is detected.
3. The guard evaluator: no code execution, bounded time and memory, pointers cannot leave the allowed roots.
4. Recipe matching and the pre-filled draft: a file or web page cannot cause a recipe to be chosen or changed.
5. Open items at session start: no file content is printed, only store metadata.
6. Locale packs: a pack cannot weaken redaction, the state lexicon or the money rule below the built-in general set.
7. The relaxed pre-filter and Stop rule (P2): rerun the guard-recall corpus.
8. A fresh red-team round per release, as `docs/security.md:144-145` already requires.
9. `SECURITY.md`: add the new stores to the "in scope" list.

### 5.5 Document updates

| Document | Change |
|---|---|
| `README.md` | Real test count; loop-contract 1.2.0; status line after the first "Checked" run; language and currency as data; supported platforms table; what the tool learns and what it does not |
| `CHANGELOG.md` | Move "Unreleased" into versions; one entry per release point |
| `INTERFACES.md` | Rewrite: it still describes phases P1 and P2 and says `writers.py` does not exist. Add the new stores and `harness-learn` |
| `CONTRIBUTING.md` | End-to-end tests and how to opt in; locale packs; the published loop-contract in tests |
| `SECURITY.md` | New in-scope items |
| `docs/getting-started.md` | Neutral examples; new setup questions; uninstall |
| `docs/owner-guide.md` | Facts, guards, recipes, rules, people, open items; how to see, retire and export each |
| `docs/how-it-works.md` | Frozen kernel and learning shell; the trust ladder; data layout with the new files |
| `docs/security.md` | Memory protection table; remove the loop-contract warning after 1.2.0; Linux verification result |
| `docs/packs.md`, `docs/development.md` | Locale packs; pack gaps; from provisional method to released check; real test count |
| `docs/faq.md` | "Does it learn?", "Can it give tax advice?", "Who is a person?", supported file formats |
| `dynamic_harness_design_spec.md` | Add an "as built" appendix that resolves the drift in section 1.8, or mark the spec as historical and point to `docs/how-it-works.md` |
| `hooks/hooks.json` | Correct the stall description |
| `.claude-plugin/plugin.json` | Version; description without a fixed language pair |

### 5.6 Versioning and changelog

- Semantic versions. Data files carry their own schema version (`harness/<name>@<n>`); a release that changes a schema
  ships a migration and a test that loads the previous version's files.
- `tools/release_hashes.py` runs for every release; the "unreleased (after 0.4.0)" labels in
  `control/defaults/approved_procedures.json` are replaced by real version labels.
- Do not use the number 1.0.0 until the evaluation thresholds in section 6 are met.

---

## 6. Evidence before claims

The spec's evaluation plan (lines 1805-1822) needs several labellers, about 400 real requests, four weeks of shadow
use and ten businesses. One person cannot run that. The table below is the smallest version that still supports honest
claims. **Every threshold is fixed here, before any run.** A missed threshold is reported as missed.

| ID | Experiment (spec stage) | Size | Pass threshold | Permits this public claim |
|---|---|---|---|---|
| E1 | Invariant suite on macOS and two Ubuntu versions (1) | Full suite | 100% pass; 0 sandbox tests skipped on Linux | "About 1,700 automated tests pass on macOS and Ubuntu" |
| E2 | End-to-end runs through `claude -p` and one interactive session (1) | 12 tasks, 3 per pack | 12 of 12 reach a signed final state; every receipt verifies; at least 9 of 12 are "Checked" | "Tasks run end to end to a signed result" |
| E3 | Planted errors (4) | 30 plants in 6 classes (unit scale, period, sign, stale source, wrong definition, total mismatch) plus 30 clean twins; plants written before the run, by a second person if possible | Recall at least 90% overall and 100% for unit scale and period; false alarms at most 10%; 0 planted errors end in "Checked" | "In our test, N of 30 planted errors were caught and none was marked Checked" |
| E4 | Routing accuracy (3) | 100 prompts, labelled by the owner before the run | 0 prompts that need a card routed to a quick answer; at most 20 over-routed | "No under-classification in 100 labelled requests (95% upper bound about 3%)"; state that there was one labeller |
| E5 | Quick-answer overhead (2) | 30 quick prompts with and without the plugin | Added time at most 2 seconds at the 95th percentile; added context at most 1,500 tokens | "Quick answers cost at most X seconds and Y tokens more" |
| E6 | Learning | 10 fact corrections; 10 repeats of corrected mistakes; 5 recipes run twice; 10 injection attempts through files and web pages | 10 of 10 facts applied in the next task; 10 of 10 repeats not "Checked"; repeat runs ask no more questions than first runs and fewer in at least 4 of 5; 0 store writes from injections | "A corrected fact is applied next time", "a corrected mistake of these kinds was not signed off again in 10 of 10 tests" |
| E7 | Location | 2 made-up jurisdictions × 3 topics; each with a confirmed, an unconfirmed and an expired rule | Correct value in 6 of 6 confirmed cases; 12 of 12 unconfirmed or expired cases end in "Done, not fully checked" | "Rules are data with dates and sources; an expired rule lowers the result state" |
| E8 | Shadow use on the owner's real work (5) | 2 weeks, at least 50 tasks | At most 1 false block in 50; every false block is listed | "In two weeks of our own use, N of M tasks were blocked wrongly" |

Not run, so no claim: equal-budget comparison with repeated runs (stage 6), a pilot with ten businesses (stage 7),
business value against other tools (stage 8), two-labeller agreement, and any benchmark. The public texts must not say
"more accurate than", "better than Harness²" or "proven with businesses".

---

## 7. Launch plan

Nothing is published by this plan. This section is a plan for a later owner action.

### 7.1 Article outline

1. The problem: a business owner needs an answer they can rely on, and a model's "done" is not evidence.
2. What a harness is, in plain words, and why one fixed harness does not fit every request.
3. The idea: a small frozen kernel that decides what "checked" means, and a learning shell made of the owner's own
   facts, rules, guards and recipes.
4. One worked example from request to signed result, with the receipt.
5. How it learns: one correction becomes a fact; one corrected mistake becomes a guard; one accepted result becomes a
   recipe. What it never learns.
6. Location and people as data: dated rules with sources; no country in the code.
7. Honest comparison with Harness² (7.3).
8. Evidence: the E1 to E8 table with real numbers, including any missed threshold.
9. Limits: read-only; no timers; not an accountant, lawyer or tax adviser; one business per installation; a person is
   a label, not a login; the routing accuracy is tested with one labeller.
10. How to try it, and how to report a problem.

Base text: the existing Persian article can supply sections 1 to 3 and 9; it must be updated where the build differs
(section 1.8).

### 7.2 Social posts

| Post | Content | Needs |
|---|---|---|
| 1 | One sentence on the problem, a 30-second screen recording of a request ending in "Checked", link to the article | E2 |
| 2 | "It did not sign off the same mistake twice": the guard example | E6 |
| 3 | "Nothing about my country is in the code": locale and rules as data | P1, E7 |
| 4 | What it does not do, and why | none |
| 5 | Thanks to the Harness² authors; what was taken and what was left | 7.3 |

Each post states one fact that a reader can check in the repository.

### 7.3 Comparison with Harness²

Harness² is a research method measured on public benchmarks. dynamic-harness is a safety and memory layer for one
business inside Claude Code and has no benchmark result. They have different goals; the article must not rank them.

| Taken from Harness² | How it is used here |
|---|---|
| Lesson format: cue, lesson, evidence, confidence, with evidence required | The lessons store (P7) |
| A library of adopted harnesses per domain, read only from earlier entries | Recipes and golden cases (P5) |
| The judge is outside the method | Stored accepted outputs are never visible to task or replay sessions |
| Checks that grader verdicts do not leak into improver context | The memory protection rules (3.6) |
| A failed evaluation is reported as unscored, not as zero | Evaluation reporting (section 6) |
| The idea that the harness, not only the prompt, is what adapts to a task | The assembly step (3.3) |

| Rejected | Why |
|---|---|
| Guardrails and plugins as editable parts | Here these are the safety kernel; a system that can edit its own guard cannot promise anything about it |
| A live Propose-Probe-Compose loop for each task | 4 × k rollouts and 3 × k sessions per task; a business user pays for and waits on each one |
| A model choosing the final answer with no ground truth and no abstention | A business result needs an executable check, and "blocked" must be a possible outcome |
| Unrestricted network, disabled sandbox, skipped permissions | Acceptable in a benchmark container, not on an owner's machine with real files |
| Lessons that feed an improver which rewrites the harness | Here an approved lesson only helps draft the task card |

### 7.4 Day-of-posting checklist

- [ ] loop-contract 1.2.0 is published and the install commands in the README work on a clean machine.
- [ ] The tagged release matches the hashes from `tools/release_hashes.py`.
- [ ] A fresh install, setup and one task to "Checked" were done that day on macOS and on Ubuntu.
- [ ] Every number in the article and posts appears in `docs/evidence/` with its raw data.
- [ ] No text says "better than", "proven" or "guaranteed"; every missed threshold is stated.
- [ ] The README states the limits in its first screen.
- [ ] Private vulnerability reporting is switched on for the repository (`SECURITY.md`).
- [ ] The Harness² repository is linked and credited; no text suggests endorsement.
- [ ] No private data (names, real figures, paths, email addresses) appears in screenshots, recordings or evidence files.
- [ ] The issue tracker has templates for a bug, a pack request and a locale pack.

---

## 8. Open decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| 1 | Version number for the loop-contract gate release, and when to publish it | 1.2.0, now; it blocks everything else |
| 2 | When to go public | Private beta after 0.5.0; public article after 0.6.0 with E1 to E7 done |
| 3 | Whether a failing guard fails the gate or only lowers the state | Fail the gate; the owner can retire the guard with one command |
| 4 | Whether Persian stays in the main repository | Yes, as one of at least two shipped language packs, with no default pointing to it |
| 5 | Whether research may propose jurisdiction rules from the web | Yes, with two independent sources and owner confirmation, and with the "prepared, not final" label kept |
| 6 | Named person profiles without authentication | Accept, and say so in the documents; leave the approval matrix out |
| 7 | Whether a second person writes the planted errors for E3 | Yes; ask one colleague for 30 plants. Without this, state that the author wrote them |
| 8 | Whether to build the offline replay (P8) before the first public release | No; decide after ten golden cases exist |
| 9 | What happens to the spec document | Mark it historical and add an "as built" appendix |
| 10 | Retention of learned data | The existing `export` covers every store; add a single "forget everything learned" command; no automatic deletion |
| 11 | Whether `PLAN.md` stays in the repository root | Keep it until 0.5.0. Until P0's hash change, a file in the plugin root changes the plugin hash if this checkout is the installed plugin |

---

## 9. Not doing

| Left out | Reason |
|---|---|
| A live Harness²-style loop or deep path | No evidence that it beats repeated runs at equal cost; high cost per task (3.7, 3.8) |
| Any learning that changes the kernel, tiers, budgets, permissions or checks | It would remove the basis of every result state |
| Agent-written executable checks as oracles | Denied by the kernel and by the spec; promotion stays a human release step |
| Timers, reminders outside a session, or any out-of-session channel | The platform's schedule is session-scoped; the owner chose Claude Code only |
| Write connectors, an outbox, bulk actions | Version 1 is read-only (15 of 18 classes denied); this is version 2 work in the spec |
| An approval matrix and authentication | No identity is available inside Claude Code |
| A separate host application | Owner decision |
| Shipping real country rule sets | Rules go stale and carry legal weight; the store starts empty and is filled with sources and owner confirmation |
| Windows support | No sandbox implementation |
| Third-party Python packages | The standard-library rule keeps the trusted code small |
| More than one business per installation | Spec lines 79-80 |
| Full-text or vector search over past records | Recipes, facts and the source registry cover the stated needs with less risk |
| Benchmark runs and comparison claims | No capacity for a fair run; the article says so |
| The spec's full evaluation (stages 6 to 8, two labellers, 400 requests) | Not possible for one person; section 6 says which claims are therefore not made |

---

## Appendix: not verified in this planning session

- `harness2/core/prompts.py` was never fetched. Most of the bodies of `core/proposer.py`, `core/recursion.py` and
  `core/context.py` were searched, not read in full. The Harness² paper was not read.
- Linux behaviour, bubblewrap, and the Ubuntu claim in `README.md:180`.
- Interactive Claude Code sessions: `AskUserQuestion` under the hooks, and whether SessionStart output is visible to
  the owner.
- Which file formats `tables.py` and `xlsx.py` accept beyond their names.
- Whether figures typed in a prompt can already be cited by a check.
- The installed Claude Code version on this machine.
- How `.remember/` and `.ruff_cache/` stay out of `git status` (they are not in `.gitignore`).
- The loop-contract rebase was not attempted; the conflict list comes from comparing changed file names.
