---
name: task-card
description: Use when the dynamic-harness says a request needs a task card (a "pending card" or tripwire message), or before any business task with numbers, files, money, customers or actions. Explains how to draft the card, validate it, compile it, ask questions within the contact rules, and record progress.
---

# Drafting and compiling a task card

The harness decides the tier (how much checking a task gets) from a **task card** that you draft and code
checks. Until a card compiles, only reads are allowed.

## 1. Draft the card (JSON) in `.claude/loops/drafts/`

Until a card compiles, the only file you may write is the draft itself: a `.json` file directly in
`.claude/loops/drafts/` of the workspace, for example `.claude/loops/drafts/card.json`.

Required fields:

- `task_type`: one of `quick_question`, `data_question`, `document`, `analysis_report`, `investigation`,
  `simulation`, `decision_support`, `one_off_operation`, `bulk_operation`, `automation_build`,
  `monitoring`, `planning`, `regulated`.
- `deliverable`: `{"kind": answer|document|report|research_report|model|decision_memo|prepared_actions,
  "description": "..."}`.
- `oracle`: `{"type": "<registered check id or empty>", "executable": true|false, "criteria": ["..."]}`.
  `executable` is true only when a registered check can verify the result.
- `reliance`: `info`, `operations`, `money` (cash, payments, pricing, credit) or `external` (tax, bank,
  partners, investors).
- `decision_class`: `none`, `financial`, `tax`, `legal`, `employment`, `medical`, `safety`.
- `cost_of_error`: `{"money_at_stake": {"amount": 0, "currency": "IRT"}}`, plus `recipient_count`,
  `customer_facing` and the like where they apply.
- `risk`: `{"level": "low"|"medium"|"high"}`.
- `units` (≥ 1), `unit_definition`, `domain`. With more than one unit, add `unit_list`: one
  `{"label", "claim"}` row per unit.
- `provenance`: for each inferred field, say where it came from: `user`, `inferred`, `probe`, `sources`.
  Anything not stated by the owner counts as inferred, and the harness takes the conservative value.

Do not set `job_id`, `version`, `reversibility` or `versions`: code sets them. You may raise
`irreversible`, `computed_numbers` and `data_classes`, never lower them.

When your draft is risk medium or higher, or would clearly need checking (T1 or T2), also spawn
`dynamic-harness:card-drafter-2` with the request, and save the card it returns. Pass it to compile as
`--second-draft <file>`. It is logged for comparison only; it does not change your card.

## 2. Validate, then compile

Run `harness-card validate <file>` and fix every error. Then run `harness-compile --card <file>`, as a
command on its own (no pipes or `&&`). The harness prints the job id, the tier and an opening line: print
the opening line verbatim and work in `.claude/loops/<job>/work/`.

If the harness says the task is refused, tell the owner exactly the line it gives you.

## 3. Questions and contacts

- Ask only when the answer changes the plan. Never ask what you can find out, or a technical choice.
- Start questions: at most 3, in one `AskUserQuestion` call, multiple choice with a suggested option.
- For a preference the owner may want remembered, start the header with `[pref:tone]`, `[pref:format]`,
  `[pref:report_style]` or `[pref:rounding]`, and use the allowed values as option labels
  (tone: formal, neutral, warm; format: short, detailed; report_style: summary first, table first;
  rounding: none, thousands, millions).
- At most 5 blocking contacts per task. After half the work is done, do not ask: record an assumption
  with the suggested option.
- In a headless run, use `harness-contact <type> --text "..."` (types: question, plan, assumption_sheet,
  definition_confirm, decision_ack) and stop; the owner's reply comes as the next prompt.
- A research plan (investigations only): `harness-contact plan --plan <file>` shows the owner the plan.
  After the owner replies yes, run `harness-contact plan --accept latest`.

## 4. Progress and subagents

- Record milestones once a `work/` file exists: `harness-ledger append --kind milestone --row
  '{"milestone": "draft", "file": "draft.md"}'` (outline, draft, final). Record each unit's result with
  `--kind unit`. BLOCKED needs a reason, and PASS needs a cited output of the unit's registered check.
- To have a unit checked, run `harness-critic render <unit_id>` and spawn `dynamic-harness:verifier` with
  exactly the prompt it prints.
- In an investigation, after the owner accepts the plan, run `harness-research render` and spawn
  `dynamic-harness:web-researcher` with exactly the prompt it prints.

## 5. Corrections

Corrections the owner sends during a task are logged as evidence. Announce the rework ("This changes 9 of
14 items; applying now."); do not ask about it.

## 6. Checks, the report and the gate

- An executable oracle must be a registered check: `finance.reconcile`, `finance.kpi`, `finance.bridge`,
  `finance.list`, `research.check`, `research.report`, `model.recalc`, `model.checks`, `model.report`,
  `doc.check` or `decide.score_flip`. Each pack's skill (`finance`, `research`, `model`, `operations`) says how
  to call it. A script you write is evidence, never the oracle.
- Never add up numbers yourself. Run the unit's check: `harness-run <check> --unit <id> --attempt <n>
  --input <file> --arg key=value`. The hook runs it in a sandbox and prints the output sha and the values. Cite
  that sha in the unit's ledger row (`"outputs": ["<sha>"]`, same attempt). In the report, use only the values
  the checks printed.
- A metric the owner relies on needs a confirmed definition: `harness-contact definition_confirm --metric
  <file>` (`{metric_id, inclusions, exclusions, unit, currency}`), then, after the owner says yes,
  `harness-contact definition_confirm --accept latest`. Re-run the check with `--arg metric_id=<id>`, then
  `harness-answer header --unit <id> --output <sha>` and put the three header lines at the top of the answer.
- Write `work/report.md` before the gate. Notes a check printed (rows left out, sources that disagree, data
  that ends early) go next to the figure they concern. Do not describe your own work as checked, verified,
  confirmed or matching: the harness adds the state line.
- Then run `harness-gate request` on its own. After it, change nothing in `work/`; a change needs a new gate.
- If the owner writes Persian, write the report in Persian too; the harness renders its own lines in the
  owner's language.
