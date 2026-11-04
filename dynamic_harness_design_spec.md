# Dynamic Harness for Claude Code: Architecture Specification

*   **Author:** @Sam
*   **Status:** Draft v2 (revised after the EGM Goldfish review; see `egm-harness-review/`)
*   **Date:** 2026-11-04
*   **Companion paper:** `هارنسینگ پویا برای Claude Code.md` (the rationale; this file is the normative spec)
*   **Builds on:** loop-contract-skill v1.0.0 (https://github.com/hdkhosravian/loop-contract-skill)

--------------------------------------------------------------------------------

## Overview

Claude Code is now used by business owners as well as developers. They use it
for answers about their own data, research, forecasts, documents, operations,
and automations. Today the structure around a task (the *harness*) is either
fixed or improvised by the model.

This system builds the harness per task from a structured **task card**:
1. A deterministic selector picks one of three tiers.
2. The tier decides which quality machinery runs (gate, ledger, verification
   level).
3. Safety machinery runs at every tier: irreversible-action control,
   control-layer protection, and the egress allowlist.
4. The user is contacted only at fixed, counted moments.
5. Lessons are collected outside the run. They change behaviour only after a
   human approves them.

The system is a Claude Code plugin that depends on loop-contract. loop-contract
already provides the contract, frozen scope, append-only ledger, executing
gate (`fold_ledger.py`) and oracle ladder. This system adds the parts
loop-contract states it lacks ("the gate is opt-in… compliance, not
enforcement", README:146): hook enforcement, a tier selector that runs as code,
and a user-communication contract.

## The Problem

Three failures recur when a task is handed to an agent:

1. **Wrong amount of structure.** A fixed harness either buries a simple
   question in process or leaves a risky task unguarded.
2. **"Done" that is not done.** A model is not a reliable judge of its own
   work.
3. **Unbalanced communication.** The agent either keeps asking and tires the
   user, or never asks and delivers the wrong thing without saying so.

Business use adds three more:

4. **A cheap-looking answer can carry a costly decision.** "What is our cash
   runway?" has no side effect, but a wrong number drives a real decision.
5. **Most business work has no executable test.** Research, forecasts and
   reports need different oracles than code does.
6. **Business actions cannot be cleanly undone.** You cannot unsend an email.
   A price change was already seen by customers. An accounting entry needs a
   reversing entry, not a deletion.

## Goals

| ID | Goal | Measure |
| --- | --- | --- |
| GL-1 | Simple questions have a bounded extra cost | T0 overhead ≤ 1.5k tokens and ≤ 2 s at p95 compared with the raw agent; identical tool-call sequence on ≥ 95% of replayed T0 tasks |
| GL-2 | No false "done" | 0 completion records without a gate receipt in the bypass suite; 0 in shadow logs |
| GL-3 | No irreversible action without approval of its exact payload | Guard recall ≥ 95% on the bypass corpus (95% CI reported); 0 unapproved executions in the pilot |
| GL-4 | Few, useful contacts | Median blocking contacts per task ≤ 2; ≥ 80% of tasks need no mid-task contact |
| GL-5 | Business outputs carry provenance and verification status | 100% of delivered answers, reports and models have a provenance block and a user-facing state (R-10) |
| GL-6 | Learning never weakens safety | 0 approved lessons that touch permissions, approvals, irreversibility or verification (enforced by lint) |
| GL-7 | Numbers the owner relies on are right or visibly flagged | Pilot: post-delivery figure errors ≤ 1% of money figures, and every failed reconciliation is visible next to its number; 0 unit errors (Toman/Rial, period) on the planted-trap suite |
| GL-8 | The owner gets value, not process | Pilot: share of deliverables accepted without rework ≥ the best baseline; owner net minutes per task below doing it by hand (§Evaluation plan stage 8) |

## Non-Goals

*   Data access, retrieval and tool design. The harness uses whatever tools
    the session has; it only decides which are allowed and how they are
    classified.
*   Replacing Claude Code permissions or the sandbox. The harness relies on
    them and configures them (§Security model). It is not a sandbox itself.
*   Automatic, unreviewed self-improvement (the Harness²-style online loop).
*   Professional advice. The harness does not replace an accountant, lawyer
    or tax advisor (§Business governance (decision classes)).
*   Running for more than one tenant on a shared store in v1. v1 is one
    business per installation.
*   Liability for business outcomes. The owner remains the decision-maker.
    Outputs in regulated classes are "prepared, not final" until a named
    professional signs off.

## Requirements (normative)

**R-1…R-10 are process invariants.** Stage 1 of the evaluation plan tests
each one deterministically with a bypass matrix.

**R-11…R-17 are business requirements.** Stage 4 (planted traps), stage 4b
and the business test files test them.

"Proven" is not claimed. Every numeric threshold in this spec is a
configurable default in `control/defaults/*.yaml` unless it is marked
*fixed*.

**Release.** Requirements tagged **[v2]** become normative when that
release ships (§Release scope).

**Origin column.**
- §n: a section of the companion paper.
- G-n: an invariant ID in draft v1.
- Ax / Bx-n: a finding ID in `egm-harness-review/`.

| ID | Requirement | Origin |
| --- | --- | --- |
| R-1 | In T0, no hook other than the always-on safety hooks and the tripwire blocks or alters a tool call | G1 |
| R-2 | A completion record ("done") exists only if the gate wrote it, with a receipt hash matching the current ledger, scope and tree | G2 |
| R-3 | The frozen work list does not shrink silently. At the end, the units equal the latest sanctioned scope version, plus any accepted leads. Each unit ends in one of these states: PASS, PASS_FLAGGED (reason), UNVERIFIED (no executable check), BLOCKED (with reason), DESCOPED_BY_USER (citing the user turn), or an investigation verdict (§Business investigations) | G3 |
| R-4 | No action of an irreversible class executes without a recorded approval. **v1:** every irreversible class is denied (`v1_action`; v1 connectors are read-only). The one exception is researcher-lane GETs under a per-job grant written from an answered `plan` contact (§Irreversible-action control); `test_r4_v1_action` asserts this exception. **[v2]:** the approval is bound to the hash of the exact payload (§Outbox and approval) | G4 |
| R-5 | The executor cannot read the experience store or load an unapproved procedure. Every loaded procedure carries an approval record (content hash + approver) | G5 |
| R-6 | Control-layer writes are *prevented* for tool writes and *detected* for all writes. Detection fails the gate and the Stop hook | G6 |
| R-7 | At most 5 blocking contacts per task, counted by a hook. These are not counted: safety approvals, `cost` and `decision_ack` contacts, one `--handoff` per task, and follow-ups that are the owner's own replies in the same thread (§Turns and contacts). At most 3 start questions, sent in one message | §7 |
| R-8 | `AskUserQuestion` is denied once progress reaches 50%. Progress follows §Turns and contacts: units when the scope has more than 1 unit, otherwise milestones, where 50% is the draft milestone. `decision_ack`, `cost` and before-spend contacts are exempt | §7 rule 2 |
| R-9 | User memory is written only by a hook, from structured answer events, using a closed schema | §7 rule 4 |
| R-10 | Every business deliverable carries a provenance block and exactly one of three **user-facing states**: **Checked** · **Done, not fully checked: <named item>** · **Partly done: N of M** (§User-facing templates and localisation). Money figures never carry a generic label. A failed check shows its reason next to the number | new |
| R-11 | A deliverable the owner will rely on (`reliance ≠ info`) or that contains computed numbers is never T0. Every aggregate is computed by a script, never summed by the model in context | new (BQ-1, BS-1, BV-1) |
| R-12 | Every money figure states: metric (dictionary id and version), period in the owner's calendar plus Gregorian, source and as-of date, unit and currency, and exclusions | new (BQ-3) |
| R-13 | Every factual claim and number in a research report traces to a claim-ledger row (snapshot, verbatim quote, date, source class). A claim with no row is dropped or labelled "model knowledge, not checked" | new (BI-1, BI-6) |
| R-14 | Every number in a model or forecast report traces to a model cell. Results are ranges with drivers and a flip point. The report states "arithmetic checked; assumptions not" | new (BS-2, BS-6) |
| R-15 | Every terminal task writes a code-generated decision record, kept outside the workspace and exportable (§Business governance). A T0-fast answer with no card is exempt; a tripwire promotion creates the card and its record | new (BG-1) |
| R-16 | **[v2]** Above the owner's threshold, the requester cannot approve alone. Approvals follow the approval matrix, including out-of-band approval (§Business governance). v1 has a single owner and denies every irreversible class, so R-16 does not apply in v1 | new (BG-5, BU-2) |
| R-17 | **[v2]** No persistent automation runs without a registered Automation Spec in state LIVE, a standing approval bound to its hash, and an owner (§Automations) | new (BA-1, BG-8) |

## Deployment surface and actors

**Assumption, to be confirmed (OQ-1):**
- **v1:** Claude Desktop / Cowork, or the Claude Code CLI. These offer
  `AskUserQuestion`, hooks, plugins and managed settings. An *operator*
  installs the plugin and connects the data sources.
- **v2:** an Agent SDK host with its own business UI, using the same plugin
  underneath.

The approval card and multiple-choice questions use only what the host can
render. On the CLI they are plain numbered lists.

| Actor | Who | Can change | Safe default if absent |
| --- | --- | --- | --- |
| Owner | Business owner | Business layer (profile, roles, approval matrix, policies, metric dictionary, sources); approves cards; edits memory with `/dynamic-harness:memory` | — (required) |
| Staff | Employee such as an accountant or salesperson | Requests tasks; approves only within the delegated limits in the approval matrix | Only the owner approves |
| Operator (was "maintainer") | IT contractor, managed-service provider, or a tech-savvy owner | Control layer, domain packs, lesson approval, setup | Setup not done → only T0 runs. Unapproved lessons → not applied. No shadow review → components stay in shadow |
| Professional | External accountant or lawyer | Read-only access and export; signs off regulated outputs | Regulated outputs stay "prepared, not final" |
| Executor | The main Claude Code agent | Run state only, append-only, through scripts | — |
| Hooks and scripts | Deterministic code | As each hook's table row specifies | Fail closed for safety |

Each card and report names the **requester**. When the requester cannot
approve an action, the card names who must: "Requested by Sara (sales). This
needs Mr. Karimi's approval. It waits until he approves."

## What is reused and what is new

| Component | Source | Notes |
| --- | --- | --- |
| Contract, frozen scope, `scope.jsonl` | loop-contract (reused) | Scope sha and count are pinned by a hook at freeze time (new) |
| Append-only ledger, `PROGRESS.md` (the only rewritable view of progress) | loop-contract (reused) | The loop-contract ledger keeps its own format. The harness adds a separate hash-chained `events.jsonl` (§Gate adapter) |
| Gate `fold_ledger.py` | loop-contract (reused, ≥ 1.1.0) | Run by the gate hook with profile flags from `control/gate.yaml` (mode chosen per run, §Gate adapter). Harness unit states are converted by the gate adapter. Stop only verifies the HMAC receipt (new) |
| Oracle ladder (8 rungs) | loop-contract (reused) | Used *inside* V2 to pick the strongest available oracle |
| Stop kinds: BUDGET, STALL, APPROVAL; "stopped, not done" | loop-contract (reused) | Mapped to terminal states (§State machine) |
| Triage (direct / skill / contract) | loop-contract (prose) → **new code** | Deterministic tier selector over card fields |
| Retro and decisions (Loop 4) | loop-contract (prose) → **new, phased** | Lesson pipeline (§Learning) |
| Business oracles: finance tie-out, numeric predictive oracle, non-code checks ("every quoted string actually appears", citation resolution, "recompute every number") | loop-contract `oracle-catalog.md` (reused) | The domain packs cite these. They are not reimplemented |
| `sources.jsonl {claim, url, retrieved_at}` + `check_citations` (fold_ledger.py:252; resolves evidence and fix paths, tolerating `path:line`; unchanged) | loop-contract (reused, **extended**) | The claim ledger `claims.jsonl` uses the `sources.jsonl` row format and adds `snapshot_sha`, `verbatim_quote`, `source_date`, `source_class`, `claim_type`, `load_bearing` and `status`. The new `check_claims` (`--sources`, loop-contract 1.1.0) checks quote match and independence. `check_citations` is unchanged |
| `--verdict-vocab`, `--headline` | loop-contract gate (reused) | `--headline` carries the answer line. `--verdict-vocab` takes exactly 3 report words: `ANSWERED,ANSWERED-WITH-GAPS,UNANSWERED`. Investigation verdicts are mapped by the gate adapter |
| Recurring jobs (`reconcile`, `dataquality`, delta reporting, `/loop` vs `CronCreate`) | loop-contract `recurring-jobs.md` (reused) | Recurring questions and v2 automations are recurring-jobs contracts |
| Hooks for every enforcement point | **new** | loop-contract has none |
| Normaliser, answer header, independence/syndication check, `model-checks` pack | **new** | The only new business code |
| Task card, outbox, contact contract, user memory, business profiles | **new** | |

## Technical Plan (Architecture)

### Layers

| Layer | Contents | Location | Who changes it |
| --- | --- | --- | --- |
| Control | Permissions template, budgets, irreversible-class table, egress allowlist, gate flags, thresholds | `${CLAUDE_PLUGIN_DATA}/control/`, outside the workspace | Operator only, through `/dynamic-harness:config` with a hash recorded |
| Business | Business profile (currency and display unit, calendar, fiscal-year start, work week and holidays, jurisdiction, language), roles and approval matrix, hard policies (discount caps, do-not-contact list, send windows, required footers), metric dictionary, source registry, connector registry, standing approvals, automation and model registries, cost caps in money, risk / spend / decision-card thresholds, protected terms (names the query guard blocks), consents, style exemplars | `${CLAUDE_PLUGIN_DATA}/business/` | Owner, through `/dynamic-harness:business` → `harness-business set` (written by a hook, with a rendered confirmation sentence). Never written by memory or by the agent. Tightening (e.g. "never email X again") can start from chat and needs one confirm. Loosening needs the owner's approval (V3) |
| User memory | Soft preferences only: tone, format, report style, rounding | `${CLAUDE_PLUGIN_DATA}/memory.jsonl` | PostToolUse hook only (R-9); the user through `/dynamic-harness:memory`. Assumptions, definitions and policies are never stored here |
| Core | loop-contract, hooks, tier selector, compiler, card validator | Plugin directory (versioned) | Plugin release |
| Run state | Card, tier, ledger, scope, outbox, journal, PROGRESS.md, metrics | `${CLAUDE_PLUGIN_DATA}/runs/<job>/`. The workspace holds only `.claude/loops/<job>/{requests,work}/` | The agent only requests changes through `bin/harness-*`. Hooks write the protected files (§Trust boundaries and protected writes) |
| Experience store (v2) | Lesson candidates, replays | `${CLAUDE_PLUGIN_DATA}/experience/` | Offline loop. The executor is denied reads |

Domain packs are ordinary **Skills**, one per domain (finance, sales/CRM,
marketing, operations, research). Claude Code loads them by description. The
compiler names the required pack in the card, and the gate checks that the
pack's checks ran. There is no custom loader. SkillsBench reports that
small, focused skills outperform large bundles.

### The eight steps

| # | Step | Input → Output | Run by | Claude Code feature used |
| --- | --- | --- | --- | --- |
| 1 | Probe | Request + workspace → `probe.json` (connectors, write-capable tools, files named, action verbs, data classes) | Script (`bin/harness-probe`), no model | UserPromptSubmit hook |
| 2 | Pre-filter | `probe.json` → `T0-fast` only when the probe finds none of these: write targets, files or connectors named, money or number tokens, action verbs, or a decision-class keyword hit (any class other than none). Code then sets `reliance=info`, `risk=low`, `computed_numbers=false`. Otherwise `card-needed` (writes `pending_card`). Resume routing comes first (§Hooks): a reply to a pending contact, or a prompt that resolves to a non-terminal job, is `resume` and sets no `pending_card`. Only when the prompt matches a *terminal* previous job in the thread (§State machine and lifecycle, matching), and that job had `reliance ≠ info` or `computed_numbers = true`, the result is always `card-needed` | Script | Same hook |
| 3 | Card draft | Request + probe → `card.v1.json` | Model (main agent, guided by the harness skill) | Skill |
| 4 | Card check | Card → valid / errors. Optional second draft for disagreement | Script, plus an optional model subagent | `bin/harness-card validate`; Agent |
| 5 | Tier selection and compile | Card → `tier.json` + run directory + scope freeze | Script | `bin/harness-compile`; PostToolUse records the tier |
| 6 | Execute | Units → ledger rows, outbox entries | Model | Hooks guard every tool call |
| 7 | Gate | Ledger + scope + oracle → HMAC receipt or failure | Gate hook, run on `harness-gate request` | PostToolUse hook; Stop verifies |
| 8 | Report and approval | Outbox, ledger → approval card, final report | The model writes `report.md` (narrative) *before* step 7, and the gate lints it; step 8 only renders deterministic sections | Stop hook; **[v2]** `/dynamic-harness:approve` |

Offline loop (outside the run): the transcript, corrections and verdicts
become a lesson candidate. The editor drafts a procedure diff, replay runs in
a sandbox, and the operator approves.

### Where the model decides and where code decides

| Decision | Model or code | Context | Trigger |
| --- | --- | --- | --- |
| Card draft (meaning of the request) | Model | Main | Step 3 |
| Second card draft | Model | Fresh subagent with a different framing (derive the card from the deliverable) | `select_tier(draft 1)` ≥ T1 or risk ≥ medium (shadow mode first) |
| Tier, V-level, active hooks, budget | Code | n/a | Step 5 |
| Irreversible classification | Code (class table) | n/a | Every PreToolUse |
| The work itself | Model | Main or units in fresh subagents | Step 6 |
| Critic (V1 yes/no criteria) | Model | Fresh subagent; prompt built from a template plus frozen criteria | V1 units |
| Tie-break selector (deep path) | Model | Fresh subagent; sees only candidates that pass the oracle | Deep path |
| Editor (lesson diff) | Model | Offline; never the executor | Lesson pipeline |
| Done / not done | Code (gate) | n/a | Stop |

Four model judgements are architectural: card, work, critic, editor. The
selector only breaks ties and never certifies a result.

--------------------------------------------------------------------------------

## Data contracts

All files are JSON or JSONL with a `schema` field (`harness/<name>@1`).

### Task card (`card.vN.json`)

```json
{
  "schema": "harness/card@1",
  "job_id": "2026-11-04-late-customers",
  "version": 1,
  "request": "Which customers are more than 30 days late, and how much do they owe?",
  "task_type": "data_question",
  "deliverable": {"kind": "answer", "description": "list of late customers with amounts and a total"},
  "oracle": {"type": "finance.reconcile_ar", "executable": true,
             "criteria": ["list total equals the AR aging total for >30 days",
                          "every row has an invoice ID and due date"]},
  "requester": "sara@shop",
  "thread_id": "th-0192",
  "follows": null,
  "reliance": "money",
  "computed_numbers": true,
  "decision_class": "financial",
  "cost_of_error": {"money_at_stake": {"amount": 0, "currency": "IRT"}, "customer_facing": false,
                    "recipient_count": 0, "legal_exposure": false, "public": false, "employees_affected": 0},
  "risk": {"level": "medium"},
  "irreversible": false,
  "reversibility": "R0",
  "units": 1,
  "unit_definition": "the answer",
  "domain": "finance",
  "data_classes": ["customer_pii"],
  "personal_data": {"categories": ["name", "amount_owed"], "subjects_count": 37},
  "resume_of": null,
  "scenario_of": null,
  "multi_session": false,
  "assumption_ids": ["a1"],
  "trifecta_leg_removed": "egress",
  "business_hash": "…",
  "versions": {"core": "1.0.0", "packs": {"finance": "1.0.0"}, "control_hash": "…", "memory_snapshot": "…"},
  "provenance": {"request": "user", "risk": "inferred", "irreversible": "probe", "units": "sources"}
}
```

Field rules:
- **Set by code, never by the model:** `irreversible`, `reversibility`,
  `data_classes`, `personal_data` and `computed_numbers` come from the probe
  and the class table.
- **Max-merged with a deterministic scan:** `decision_class` (keyword and
  domain classifier) and `cost_of_error` (column totals, recipient lists).
  The model may raise these fields, never lower them.
- **Set by the compiler:** `multi_session` is true when the tool calls estimated by `harness-estimate` (from the probe and past records) exceed the T1 budget (150), when `resume_of` is set, or when the task must wait on someone other than the owner.
- **Set by the compiler, also:** `follows` (from thread context), `scenario_of` (from `harness-model scenario`), and `business_hash`. `business_hash` pins the business layer for the task; a change applies only from the next task.
- **Owner-confirmed where possible:** `reliance` ∈ {info, operations, money,
  external}. *money* covers cash, payments, pricing and credit. *external*
  covers tax, bank, partners and investors.
- **Conservative defaults for inferred fields:** a field whose provenance is
  `inferred` takes the conservative value when uncertain: `risk.level ≥
  medium`, `units > 1`, `reliance = money` when a money figure appears.
- **Thresholds:** `risk.level` follows from `cost_of_error` and the owner's
  thresholds (business profile). If `money_at_stake` or `recipient_count`
  exceeds a threshold, risk is high, which forces T2 and V3 for the action.
- **Task type:** `task_type` is one of the 13 values in §Business task
  profiles.

### Tier selection (code)

```text
def select_tier(card, probe):
    floor = probe.floor_tier                                       # e.g. connector with write scope → T1
    if card.decision_class != "none" and policy(card.decision_class) == "refuse":
        return REFUSE                                              # e.g. medical dosing → state REFUSED
    if card.scenario_of:                                           # scenario run (§Business simulations)
        hi = T2 if (card.risk.level == "high" or card.irreversible
                    or policy(card.decision_class) == "signoff") else T1
        return max(hi, stored_tier(card.scenario_of), floor)
    if card.resume_of: return max(stored_tier(card.resume_of), floor)  # never lower
    if probe.question_only and not probe.write_targets and card.risk.level == "low" \
       and card.reliance == "info" and not card.computed_numbers \
       and policy(card.decision_class) in ("none", "inform"):
        tier = T0                                                  # general knowledge, style, quick lookup
    elif card.irreversible or card.risk.level == "high" \
       or policy(card.decision_class) == "signoff" \
       or card.units > control.t1_max_units or card.multi_session:  # default t1_max_units = 5
        tier = T2
    elif card.oracle.executable:                                   # pack-declared business invariants count
        tier = T1                                                  # multi-unit single-session work: T1 + frozen scope
    else:
        tier = T2
    return max(tier, floor)
# decision_class with policy "inform" (e.g. financial) adds the mandatory label; it does not raise the tier.
# tests/test_tier.py applies every row of the §Business task profiles taxonomy to this function.
```

When the two drafts disagree, the merge is deterministic:
- risk: take the maximum (the tier follows from the merged card);
- oracle criteria: take the union;
- `request` or `deliverable`: one start question; if no questions remain,
  record an assumption.

### Tier × verification

| Tier | Default V | Minimum V | Done means |
| --- | --- | --- | --- |
| T0 | V0 (the user reads it) | V0. Never used for relied-on or computed numbers (R-11) | The answer is delivered with its user-facing state |
| T1 | V2 (T1 requires `oracle.executable`) | V2 | Gate receipt. A multi-unit T1 also freezes its scope (loop-contract allows this in one session) |
| T2 | V2 | V2 for every unit that has an executable check, V1 for the rest | Gate receipt |
| Any irreversible item | V3 | V3, which a label cannot waive | v1: denied, never executed. [v2]: approval record plus post-execution check |

V-levels describe *who* verifies and *when*. Loop-contract rungs describe *how
strong* the oracle is. Inside V2 the unit uses the highest rung available
(native check > invariant > differential > metamorphic > …).

### Other records

| Record | Fields |
| --- | --- |
| `tier.json` | `{job_id, session_id, tier, v_default, set_by: "compiler", ts, prev_tier}`. Monotonic: the hook rejects any decrease |
| Events and ledger | See §Gate adapter: `events.jsonl` (hash-chained harness events; the head is in `${CLAUDE_PLUGIN_DATA}/heads/<job>`, written only by a hook) and the loop-contract `ledger.jsonl` / `verdicts.jsonl` rows |
| Assumptions (`assumptions.yaml`) | The canonical store: `{id, value, unit, period, source, as_of, owner_confirmed, confirmed_turn_id?, sensitivity_rank, expires_at, dependents[]}`. Written only through `harness-model assume`. `owner_confirmed` is set only from a user turn (`confirmed_turn_id`). The card holds IDs only |
| Outbox item (`outbox.jsonl`) | `{item_id, lane: A\|B, tool, input, sha256, class, produced_ids[], depends_on[], verification_status, precondition_snapshot, idempotency_key, group, status: PENDING\|APPROVED\|REJECTED_BY_USER\|EXECUTED\|FAILED\|EXPIRED, expires_at}` |
| Execution journal (`journal.jsonl`) | `INTENT(item_id, key)` → `DONE(item_id, external_id)` \| `FAILED(item_id, error)` |
| User memory record | `{id, scope: global\|person\|project\|task, person_id, key (closed schema), verbatim, ts, source_task, expires_at?}`. Allowed keys and closed value enums: tone ∈ {formal, neutral, warm}; format ∈ {short, detailed}; report_style ∈ {summary_first, table_first}; rounding ∈ {none, thousands, millions}; default_choices is stored only as a `question_id → option_id` map. Language lives in the business profile. Values containing URLs or emails, or longer than 40 characters, are rejected. Free text is stored only after the owner confirms it in `/dynamic-harness:memory`. Forbidden: recipients, approval waivers, permissions, budgets, and anything the business layer owns (currency, calendar, definitions, policies). The hook stores only answers to questions tagged with an allowed key. Within one person, the newest record wins. Conflicts across people are shown to the user, never resolved silently |
| Lesson candidate | `{id, evidence_refs, target_skill, diff, lint_result, replay_result, external_content: bool, approver?, approved_hash?}` |
| Compiler output | `state.json`, `tier.json`, `scope.jsonl` (always; one row for a single-unit task), `pins/<job>.json`, `opening.txt`, `card` link, `budget.json`, `active_task` pointer, required-pack list, contact counter. Hooks themselves are static; they read these files |

--------------------------------------------------------------------------------

## Irreversible-action control

**Definition.** An action is irreversible if it falls in a class listed in the
human-owned table `control/classes.yaml`:

| Class | Examples | Reversibility | Default |
| --- | --- | --- | --- |
| external_message | send email / SMS / chat, post to social media, publish | R3: cannot be taken back (a deleted post may already be screenshotted) | irreversible; hold window 15 min where the provider supports scheduled send |
| money | payment, refund, payout, invoice issue | R3 | irreversible, needs its own confirmation; approval matrix applies |
| payee_change | new or changed supplier bank details (Sheba/IBAN) | R3 (classic fraud vector) | V3 plus verification through a second channel, not the one that supplied the details |
| price_publish | price change in a live shop | R1: revertible, but customers saw it and may have ordered | irreversible; before-values stored so a "revert batch" exists |
| accounting_entry | journal entry, invoice void | R2: correct only by compensation (reversing entry or credit note) | delete and void denied; reversing entries only |
| destructive | delete files outside run state, job scratch or temp, `rm -rf`, drop table, delete a cloud resource, `git push` (any), `git push --force` | R3 | irreversible |
| local_app_automation | `osascript`, AppleScript/JXA, UI scripting that drives Mail, Messages or a browser; on Linux `xdotool`, `ydotool`, `wtype`, D-Bus calls (`dbus-send`, `gdbus`, `qdbus`, `busctl`), `xdg-open`, `gio` | R3 | irreversible |
| external_write | CRM / ERP writes, Sheets shared with others, deploy | R1 | irreversible (dependent writes use lane A) |
| automation_create | creating a scheduled job, routine, webhook or standing rule | persistent effect | V3; only through §Automations |
| egress_offlist | any network request to a domain not on the allowlist | R3 | denied, except under a per-job research grant (§Business investigations) |
| egress_with_data | any request, **including to allowlisted domains**, that carries workspace data or tagged business names (WebFetch query strings, image URLs, search queries), and copying to the clipboard (`pbcopy`; on Linux `xclip`, `xsel`, `wl-copy`) | R3 (data leakage) | irreversible. Search queries pass the query guard (§Business investigations) |
| unknown_mcp_write | any MCP tool not tagged `read` in the connector registry | R3 until classified | irreversible |
| self_written_exec | running a script the agent wrote this task that uses the network | — | routed through the sandbox; egress is checked against the allowlist |
| local_reversible | files in run state or a git-tracked workspace | R0 | not irreversible |
| draft_create | creating a draft (email or document) with a connector-registry tool tagged `draft` | R0 | allowed in v1 |

**Classifier policy decisions (P1–P2 build).**
- **Allowed as `local_reversible`:** `git add`, `git restore --staged` (without `--worktree`), and `git stash` (except `drop`/`clear`). `git commit` is denied in v1.
- **Process control:** `kill` is allowed only for this task's job specs (`%1`, `$!`). Any PID (including 1 and −1), `pkill`, `killall`, `xargs kill` and `launchctl kill|stop|bootout|remove` are `destructive`.
- **Copy and move:** `cp`, `mv`, `rsync`, `ditto`, `install` or `ln` with a destination outside the workspace is `external_write`. This includes `/tmp`.
- **Paths:** a remover argument using `..` may delete only inside the job's scratch folders. `~user` that cannot be resolved is denied. The workspace's parents and the home, data and plugin roots never count as temp.
- **Fail closed:** shell `case` statements, function definitions, parse errors and deep nesting are denied.
- **Measured:** the 401-row training corpus gives 100% recall with 0 false denies. The independent 178-row red-team corpus scored 94.1% before the fixes and 98.5% after, with 0 false denies. The two remaining misses are homoglyph paths, pending `protected._norm`. The red-team figure is no longer fully held out, because its misses were fixed; a fresh red-team corpus is needed per release.

**v1 action** (`v1_action` in `classes.yaml`). Every class above except `local_reversible` and `draft_create` is **denied** in v1. **Exception:** inside the `dynamic-harness:web-researcher` lane with an active per-job research grant, GETs to granted domains and fetches/queries that pass the query guard are allowed. The native `ask` prompt is never used for these classes in v1: it would show the owner a raw command, and an injected agent could count on a rubber stamp.

Every approval card states, for each group, the fix if the action turns out
wrong and what that fix costs ("If wrong: send a correction email to 37
customers"). This is recorded at approval time.

PreToolUse classifies each call by tool name and MCP tag, Bash command parse
(including `sh -c`, `python -c`, `curl -X`), and destination domain. Unknown
always resolves to *irreversible*. Bash patterns are known to be fragile, so
the hard backstop is the Claude Code sandbox, enabled by setup (§Security model).

**Lethal trifecta.** Private data, untrusted input and egress together. Every
card with `data_classes ≠ []` that also reads untrusted content (web, inbound
email) records which leg it removes. The default is to remove egress to
non-allowlisted domains.

--------------------------------------------------------------------------------

## Trust boundaries and protected writes

**Principle.** Anything the agent can run counts as the agent. Hooks run
outside the sandbox and are not started by the agent, which makes them the
only trusted writers. Each script in `bin/harness-*` that the agent starts
through Bash does two things only:

- prints a JSON *request* on stdout;
- writes untrusted scratch files under `.claude/loops/<job>/requests/`.

It never writes `events.jsonl` or any other protected path.

The PostToolUse hook for that call reads the request from `tool_response`,
validates it against its schema, and is the **sole writer** of the protected
result.

**Generic rule.** Every `bin/harness-<tool> <sub>` has exactly one PostToolUse
writer, `<tool>.apply`. A protected file with no writer row is written by no
one.

**Job layout.** Each job lives in two places:

- **Run state** (protected): `${CLAUDE_PLUGIN_DATA}/runs/<job>/`. It is
  outside the workspace, and setup protects the whole tree with one sandbox
  `denyWrite` rule.
- **Workspace** (agent-writable): `.claude/loops/<job>/requests/` for
  scratch files, and `.claude/loops/<job>/work/` for deliverable drafts and
  `work/report.md`. Nothing else in the workspace belongs to the job.

Every writer event carries `file_sha` for the file it wrote. Every hook that
reads a protected file first checks that sha against the latest event for
that file. A mismatch gives `STOPPED_TAMPER`.

On `harness-gate request`, the hook copies `work/**` into the run state's
`outputs/submitted/` and hashes it. The gate and the receipt cover only the
submitted copy, so an edit made after submission needs a new gate run.

**Hook reads of agent-writable paths.** Hooks open files with `O_NOFOLLOW`.
On the opened file descriptor they check `S_ISREG`, `st_nlink == 1` and the
fd's real path (`fstat`, `F_GETPATH`), so the file cannot be swapped between
check and read. Every path argument the agent passes, for example to
`harness-model import`, must lie under `work/` or `requests/`. Hooks never
run agent-influenced code or parse agent-supplied xlsx in their own process;
they do it only through `harness-sandbox-exec`, under the gate-child
constraints. This applies to answer re-computes, scenario re-runs and
imports. In addition, every hook that reads or copies from
`work/`, `requests/` or the scope paths uses `lstat`. It rejects:

- symlinks;
- any file with `st_nlink > 1`;
- any realpath outside that job's `work/` or `requests/`.

A rejection writes a `TAMPER` event.

**Protected paths.** These are listed in `control/protected_paths.yaml`.
PreToolUse denies Edit, Write, NotebookEdit and recognised Bash writes to
them, checking after `realpath`, and setup adds sandbox `denyWrite` for all
of them. PreToolUse also denies Edit, Write and NotebookEdit to any realpath
outside the workspace. `hooks.json` runs `python3 -I`, and `openpyxl` and `formulas` are vendored (schemas use the stdlib subset validator `harness_core.schema`) under `${CLAUDE_PLUGIN_ROOT}`, so no
user site-packages are loaded.

| Area | Paths |
| --- | --- |
| Plugin data | all of `${CLAUDE_PLUGIN_DATA}/**`: control, business, memory, heads, pins, keys, records, sessions, jobs index, config log, turns |
| Code | `${CLAUDE_PLUGIN_ROOT}/**` (hooks.json, lib, bin, skills, pack manifests and their check entrypoints); the loop-contract plugin root (including `fold_ledger.py`); `~/.claude/**`; `~/.claude.json`; the managed-settings path |
| Run state | all of `${CLAUDE_PLUGIN_DATA}/runs/<job>/`. That includes `events.jsonl`, `tier.json`, `budget.json`, `contacts.json`, `lease.json`, `receipt.json`, `scope.jsonl`, `ledger.jsonl`, `verdicts.jsonl`, `gate/**`, `outputs/**`, `claims.jsonl`, `decisions.jsonl`, `assumptions.yaml`, `contradictions.jsonl`, `search_log.jsonl`, `answer_header.md`, `opening.txt`, `card.vN.json`, `snapshots/**`, `outbox.jsonl` |
| Workspace | `.claude/**` (apart from each job's `requests/` and `work/`), `.mcp.json`, `CLAUDE.md`, `.git/**` |
| Session | `turns.jsonl`, `sessions/<id>.json`, `jobs/index.json`, `lease.json` (all in plugin data) |

`keys/` and `experience/` also get read protection. Sandbox `denyRead` covers
Bash. PreToolUse denies Read, Grep, Glob, NotebookRead and MCP file reads
whose realpath is under either folder. Setup writes the matching
`permissions.deny` rules.

**User turns.** UserPromptSubmit appends `{seq, turn_id, ts, text_sha, text_norm,
typed_command?, pending_contact_id?}` to the protected `turns.jsonl`. `turn_id` is
the platform's `prompt_id`, and `text_sha = sha256(text_norm)`. A prompt that
starts with `<task-notification>` is a background-subagent result: it is
recorded as a *system* turn, which never counts as owner consent. `text_norm`
is the normalised text with personal data redacted. Writers match against it
and verify it against `text_sha`. When
the user typed a slash command, `typed_command` holds that command and its
arguments. Any write that needs user consent cites a `turn_id` from this
file. That turn must come after the matching contact or stop record, and the
user's text or the chosen option must name the exact item: the units, the
amount, or the assumption.

**Writers.**

| Protected object | Sole writer (a hook) | Condition |
| --- | --- | --- |
| `events.jsonl`, `heads/<job>` | Every hook writer: the PostToolUse writers, UserPromptSubmit, Stop and the budget hook | Each write appends a chained event `{seq, prev_sha, type, payload, row_sha?}`. Every ledger, verdict and scope append carries the row's sha256. `--verify-chain` runs after each append; a failure stops the task with `STOPPED_TAMPER` |
| Ledger, verdict and scope rows; decision and contradiction rows | PostToolUse on `harness-ledger append --kind unit\|verdict\|decision\|contradiction` | The row must be schema-valid. PASS is accepted only when the row cites at least one recorded output, and every cited output has `exit_code = 0`, `result = pass`, and the unit's registered `check_id`. SUPPORTED and REFUTED are accepted only when the row cites a recorded `harness-run research.check` output for that unit with exit 0. That check verifies three things: the cited claim rows are load-bearing for the hypothesis, they pass `check_claims`, and a `harness-critic` verdict is recorded. Otherwise the hook rejects the row or records it as FAIL |
| `outputs/outputs.jsonl` (recorded outputs) | PostToolUse on `harness-run <check_id>` | The hook runs the registered entrypoint as a sandboxed child. **Inputs** come only from source-registry paths, which the hook snapshots (sha256 plus `as_of`), or from earlier recorded outputs. A `work/` file may be used only as the deliverable under test, never as source data. It records `{check_id, unit, attempt, exit_code, result, values, sha256}`. Output from a script the agent wrote is stored as evidence only, and caps the state at "Done, not fully checked" |
| `answer_header.md` | PostToolUse on `harness-answer header` | The hook re-runs the compute as a registered entrypoint and renders the header from those recorded values |
| `claims.jsonl` | SubagentStop for `dynamic-harness:web-researcher` | Rows come only from validated researcher output. Code derives `source_class` from the source registry, and `load_bearing` from the plan |
| `search_log.jsonl` | PostToolUse on WebSearch / WebFetch in the researcher lane | Records each query and its result URLs |
| Card, `opening.txt`, tier, scope v(n), pins, budget, contacts, egress grant, `plan.json` | `compile.apply`, `budget.apply`, `contact.apply` | **compile.apply on success:** sets the session's `active_task = <job>` and `prompt_scope = active_task`, and clears `pending_card`. It checks that a gate profile exists for the tier and task type; if none does, it refuses the card. **On REFUSE:** writes the card and a `REFUSED` stop record. **`harness-contact plan --accept <turn_id>`:** checks that the turn answers the latest `plan` contact, then calls `compile.apply` in-process with units = `plan.hypotheses` (scope v(n+1), re-pin, tier only goes up), and writes the grant `{plan_sha, domains, page_budget}`. **budget.apply on STOPPED_COST or STOPPED_BUDGET:** also writes a `cost` contact `{contact_id, amount, calls}`. If another job is open and the compiled tier is ≥ T1, it appends `PAUSED(handoff)` to that job. Scenario and import writers call `compile.apply` in-process; nothing else builds a card. **contact.apply for `plan`:** writes the protected `plan.json` (schema `research_plan`: `{plan_sha, hypotheses[], queries[], urls[], domains[], page_budget, languages, stop_rule, load_bearing[]}`). The lanes hook, the grant and the claims writer read only this file. DESCOPE, accepted leads and RE_UNDERSTAND each cite a turn that comes after the latest freeze. `harness-compile --descope` needs a cited turn whose text matches at least 1 unit label or ordinal; the rework notice then lists the resolved unit ids. The egress grant needs a cited turn that answers the `plan` contact |
| Cost or budget increment | PostToolUse on `harness-contact cost --accept <turn_id>` | The cited turn must answer the latest `cost` contact, and it binds that contact's amount. The writer appends `APPROVAL{cost_increment}` and then `RESUMED`. One contact type covers both STOPPED_COST (money) and STOPPED_BUDGET (calls). It is exempt from the cap. The writer reads the money caps from the *live* business layer, so raising a cap (by typed `/dynamic-harness:business`) is exempt from the task's `business_hash` pin. If the owner does not want to continue, `/dynamic-harness:status --abandon <job>` (typed) writes the terminal state `ABANDONED` |
| Assumptions | PostToolUse on `harness-model assume` | `owner_confirmed` is set only with a cited turn. Values the owner stated in a turn are recorded the same way |
| Business layer | PostToolUse on `harness-business set` | PreToolUse denies `set` and `register-source` unless one of these holds: **(a)** the latest `turns.jsonl` row has `typed_command = /dynamic-harness:business` with the same arguments (a one-use nonce bound to the payload hash); or **(b)** for a *tightening* change or a `definition_confirm` dictionary entry, a cited turn answers a contact whose rendered-sentence hash equals the payload hash. Loosening always needs (a) and the owner role, and applies from the next task. Code classifies the diff. *Tightening* is only these changes: adding a protected term, adding a do-not-contact entry, lowering a cap or threshold, or adding a required footer. **Every other change is loosening.** Each change appends its expected hash to `config_log.jsonl` |
| Control layer, `enforcement.yaml`, settings, memory edits, repair, export | PostToolUse on `harness-admin <cmd>` (behind `/dynamic-harness:setup`, `config`, `off`, `memory`, `status`, `export`) | Denied unless the latest `turns.jsonl` row has `typed_command` equal to that command with the same arguments (one-use nonce). Repair first shows the scope and pin diff, then needs a second typed confirmation |
| User memory | PostToolUse on `AskUserQuestion` (R-9) | Allowed keys and closed value enums only |
| Snapshots and fetch log | PostToolUse on `harness-claims fetch` (own matcher, 60 s timeout) | Before fetching, the hook re-checks, and does not trust PreToolUse: `agent_type = dynamic-harness:web-researcher` from the hook input, an active grant for this job, an exact URL match against the plan or frozen-query results, and the query guard on the full URL. GET only. A URL already in the fetch log returns its `snapshot_sha` without a new download or a budget charge, and `harness-research render` lists the URLs already fetched (crash-safe). Each redirect hop is re-checked, and an off-grant hop is not followed. Each fetch counts against the page budget. The hook downloads the raw page and logs `(url, snapshot_sha, ts)`. A failed check gives a `FETCH_DENIED` event; a timeout gives `status = UNREACHABLE` |
| Model registry | PostToolUse on `harness-model register` | Only with a verified DONE receipt |
| Gate output and receipt | PostToolUse on `harness-gate request` (its own matcher, fixed 1800 s timeout) | See §Gate adapter |
| Decision record | Stop hook (`record.write`) | At a terminal state |
| `turns.jsonl`, session file, lease, `RESUMED` events | UserPromptSubmit | On every prompt |
| Stall and stop records, record events | Stop hook | §Gate and Stop semantics |
| `jobs/index.json` | `compile.apply` and Stop | On compile and at each terminal state |
| `state.json`, `PROGRESS.md` | Re-rendered from `events.jsonl` after each append | Derived only; never edited |
| Milestones | PostToolUse on `harness-ledger append --kind milestone <outline\|draft\|final>` | Accepted only with a hashed `work/` file. The contacts hook reads the latest milestone |
| FAIL → PASS_FLAGGED / BLOCKED | `ledger.append` | On a unit's 2nd FAIL: PASS_FLAGGED(failure_sig) if `work/` holds the unit's deliverable and the failure came from its registered `check_id`; otherwise BLOCKED |

**Receipt.** The receipt is:

```
HMAC_sha256(key, events_head@gate ‖ scope_sha ‖ tree_sha ‖ report_sha ‖ gate_sha ‖ control_hash ‖ business_hash ‖ core_hash ‖ mode ‖ caps_sha ‖ traced_set_sha)
```

The key lives under `keys/`, which the agent cannot read. The receipt binds
the event-log head as it was when the gate ran.

**Verifying later.** Stop verifies the HMAC over that head. After the gate,
only these event types may follow: `RECEIPT`, `MODEL_REGISTERED`, `RECORD`,
`BUDGET_CHARGE`, `TURN`, `LEASE_HEARTBEAT`, `CONTACT(question-free status)`.
Verification hashes `outputs/submitted/**`, not `work/**`. Any other later event
invalidates the receipt. A `decision_ack` is therefore issued *before*
`harness-gate request`.

**Gate children are sandboxed.** `gate.run` runs `harness-sandbox-exec --
python3 fold_ledger.py …` with `--oracle-timeout ≤ 1790`, so fold_ledger and
every oracle it spawns run under these constraints:

- no network;
- `denyRead` on `keys/`;
- a read-only copy of the scope paths as the working directory;
- arguments derived from the card passed as a schema-validated JSON file through `--oracle-stdin`, never put into a shell string.

The HMAC is computed in the hook process after the child exits. `tree_sha`
is computed in-process, without git.

**Commands.** `setup`, `business`, `config`, `memory`, `status`, `off` and
**[v2]** `approve` set `disable-model-invocation: true`, so the agent cannot
invoke them. Their writes also go through the nonce-checked writers. PreToolUse also denies any direct call to `fold_ledger.py`.

**Session checks.** SessionStart:

- compares the control, business, gate, plugin-tree and loop-contract-tree
  hashes against the last entries in `config_log.jsonl`, which hold the
  release hashes. It never re-baselines on its own.
- runs a canary PreToolUse round-trip and writes
  `sessions/<sid>/status.json` `{ok, reasons, canary, ts}`. PreToolUse
  denies every non-read call unless a fresh `ok` status exists for this
  session. Compile and gate refuse to run unless it exists, so a session
  where the hooks never loaded cannot reach a receipt.

A card that exists with no `tier.json` is `STOPPED_TAMPER`, never T0.

**Tamper recovery.** The operator runs `/dynamic-harness:status --repair`. It
restores every protected file to the bytes recorded at the last verified
`seq`, and moves the tampered copies to `tamper/`. It pins only those
restored bytes, never what is currently on disk, then resumes the task.

**Hook failure is a deny.** `bin/harness-hook` wraps every handler in
`try/except BaseException`, with one watchdog for the whole process. Each
matcher's watchdog is its timeout minus 5 s for the gate and fetch hooks
(1795 s, 55 s) and minus 1 s otherwise (Stop 9 s, every other hook 4 s). The gate and fetch hooks are
exempt from the 4 s limit. Any
exception or overrun exits with code 2: a deny for PreToolUse, a block for
Stop. Claude Code treats any other non-zero exit as allow, which is why the
wrapper uses 2. The bypass corpus includes inputs that crash the parser.

**Residual risk.** A process running outside the sandbox as the same OS user
can still change anything. The config log and hash checks detect it; nothing
prevents it.

## Hooks

The **scope rule** comes first. When `prompt_scope ∈ {inline_t0, new}`,
hooks do **not** resolve to `active_task`. Only these run: safety,
UserPromptSubmit, the tripwire, budget (charged to a session T0 counter) and
the PostToolUse writers.

**Resume routing.** This check comes before the pre-filter's thread rule.
If the turn has a `pending_contact_id`, or resolves to a non-terminal job,
the result is `resume`:

- no `pending_card` is set;
- `prompt_scope = active_task`;
- the hook sets the session's `active_task = <job>` after it acquires or
  takes over the lease.

The thread rule applies only when the matched job is terminal.

| Hook (event) | Active at | Does |
| --- | --- | --- |
| `UserPromptSubmit` | all (explicit timeout; default is 30 s) | Appends to `turns.jsonl`, recording `<task-notification>` prompts as system turns. Runs the probe and the pre-filter. Sets `prompt_scope` ∈ {`active_task`, `inline_t0`, `new`}. Writes `pending_card` when the result is `card-needed`. If the prompt resolves to a job whose latest record is a resumable stop, appends `RESUMED{job, turn_id}`. The cost stops are the exception: they resume through `harness-contact cost --accept`. Refreshes the lease heartbeat. On a lease takeover, runs `fold.project()`, then `--remaining`, then the takeover, all inside the hook. Gives the probe the thread context |
| `PreToolUse` safety (always `enforce`) | **all tiers** | After a `STOPPED_COST` or `STOPPED_BUDGET` record with no `RESUMED` after it, denies every call except reads and `harness-contact`. Classifies the call (§Irreversible-action control); in v1 every irreversible class is denied except under the research grant. Denies writes to protected paths, `osascript`/`open`/`shortcuts`/`automator`, execution of any file written in this task except through `harness-sandbox-exec`, MCP servers missing from the connector registry, and direct calls to `fold_ledger.py`. Denies `harness-business set|register-source` without the nonce. While `pending_card` is set, denies every non-read call except `harness-card`, `harness-compile`, and `harness-model scenario|import`. That exception applies only when three things hold: the probe marked the prompt as a follow-up to that model's job, the parent job has a registry entry, and the probe found no write targets, no action verbs and no new files other than one `.xlsx` for `import`. Rejects any decrease of the tier. Each module has a 4 s deadline and returns `deny` when it expires (fails closed) |
| `PreToolUse` lanes | T1, T2 (researcher rules only when `task_type = investigation`) | Web and browser tools are allowed only when `agent_type = dynamic-harness:web-researcher`. That agent may run exactly one Bash form, `harness-claims fetch <url>`, and is denied Read, Grep, Glob, other Bash, and data MCP tools. A researcher spawn is allowed only when its prompt equals `harness-research render <plan_sha>` (the hash is recorded). Continuing or messaging a running researcher (SendMessage) is denied. A fetch URL must exactly match either a URL in the approved plan or one that a frozen query returned (as recorded in `search_log.jsonl`). The grant's domains are derived from those URLs. Plan domains must be in the source registry or typed by the owner. The query guard runs on the full URL. It percent-decodes each path and query segment first, then rejects any segment longer than 64 characters or with entropy above 4.0 bits per character. The entropy is measured on the decoded text, so Persian URLs pass. Verifier spawns must carry the prompt produced by `harness-critic render`. Spawns of non-harness agent types are denied |
| `PreToolUse` contacts | T1, T2 | Counts `AskUserQuestion` and `harness-contact`. Denies them above the cap, and after 50% progress except for exempt types (R-7, R-8) |
| `PostToolUse` writers | all | The trusted writers listed in §Trust boundaries |
| `PostToolUse` budget | all (scope-aware) | Charges `budget.json`, or the session T0 counter. Writes `STOPPED_BUDGET`, `STOPPED_COST` or `STOPPED_STALL` (§Budget and failure handling) |
| `PostToolUse` tripwire | T0, only while `pending_card` is unset | **Triggers:** a side-effecting call; more than N = 8 tool calls (counted per `session_id`, including subagents; per `turn_id` when `prompt_scope = inline_t0`); a second unit; or a data-tool call when the probe found a money or data class. **Effect:** sets `pending_card` with a floor of T1 (T2 if the probe saw an irreversible class), and returns a block reason. Only read calls are then allowed until the model drafts the card and `harness-compile` succeeds. The compiled tier is `max(floor, select_tier)`. For an inline T0, the hook also appends `PAUSED(handoff)` to the open job |
| `SessionStart` | all | Runs the session checks. Lists non-terminal jobs with a stale lease from `jobs/index.json`. Refuses `bypassPermissions` |
| `ConfigChange` | all | Blocks `disableAllHooks`, disabling the plugin, and loosening permissions during a task |
| `Stop` | all | §Gate and Stop semantics |
| `SubagentStop` | T1, T2 (matchers use the full anchored name, `^dynamic-harness:<agent>$`) | Records `{agent_type, agent_id, prompt_sha, verdict_sha}` for verifiers. SubagentStop carries no spawn prompt, so `prompt_sha` comes from the lanes hook, which records it at the Agent call and links it to `agent_id`. Researcher output is accepted only as schema-valid rows `{claim, quote ≤ 300 chars, date, url, snapshot_sha}`. Free text is dropped. Quotes are checked against the hook-fetched snapshots before the main agent sees them. Never requires the gate |

Hooks find the tier this way: session file → `prompt_scope` (`active_task`
only) → `tier.json`. If no tier is found, the safety hooks still apply, and
Stop refuses a T0 completion while `pending_card` is set. A missing tier
never fails open.

*Platform facts (OQ-4 spike, Claude Code 2.1.296, `dynamic-harness/spike/RESULTS.md`).*
These are normative for v1:

- **Failure handling.**
  - A hook that crashes (exit 1) or times out lets the tool run. Every
    harness hook therefore sets `onFailure: "block"` and an explicit
    timeout. This needs Claude Code ≥ 2.1.295, which SessionStart checks;
    below it, tasks above T0 are refused.
  - Denies use exit 2 or JSON `permissionDecision: deny`, never exit 1.
  - The default timeout is 600 s (UserPromptSubmit: 30 s). `timeout: 1800`
    is accepted.
- **Stop hook.** `decision: block` works and `stop_hook_active` is present.
  The platform ignores the 9th consecutive block (the cap is set by
  `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`). The harness writes its own
  STOPPED_STALL after 3 blocks, so the cap is never reached.
- **Hook inputs.**
  - PostToolUse carries `tool_response` (Bash stdout/stderr, MCP, Agent).
  - UserPromptSubmit carries `session_id`, `prompt_id`, `prompt`, `cwd`,
    `transcript_path` and `permission_mode`. `prompt_id` is used as
    `turn_id`.
- **Subagents.**
  - Hooks fire inside subagents with `agent_id` and
    `agent_type = <plugin>:<agent>`. Both fields are absent on main-thread
    calls.
  - SubagentStop matchers must use the full, anchored name.
  - SubagentStop does not carry the spawn prompt. The lanes hook therefore
    records `prompt_sha` at the Agent call and links it to `agent_id`.
  - Subagents run in the background by default. Stop treats a running
    subagent (`background_tasks`) as not done. UserPromptSubmit treats
    `<task-notification>` prompts as system turns, never as owner prompts.
- **Headless runs.** `AskUserQuestion` is unavailable in `-p` mode. Headless
  runs use `harness-contact` with defer and resume.
- **Sandbox.**
  - Key names: `sandbox.filesystem.denyWrite` / `denyRead` and
    `sandbox.network.allowedDomains`. `allowUnsandboxedCommands: false`
    works.
  - `allowedDomains` is not enforced in bypass mode without
    `strictAllowlist: true`. Setup therefore writes `strictAllowlist` and
    `failIfUnavailable` in user, managed or `--settings` scope (a plugin
    cannot set them).
  - **MCP stdio servers and hooks run outside the sandbox.** MCP servers
    not in the connector registry are denied. Hooks are the trusted
    writers by design.
- **Loading.**
  - `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are expanded in hook
    commands and exported to hook processes.
  - `allowManagedHooksOnly` outside managed settings is ignored. In managed
    settings it disables plugin hooks unless the plugin is force-enabled.
  - Starting with `--bare`, or without the plugin, skips every hook, so the
    SessionStart canary is a positive check that the hooks loaded.
  - ConfigChange fires on mid-session settings edits.
- **Scheduling.** Cron tools fire only while their session is open, and
  cloud routines cannot see local files. Deterministic automations [v2] use
  the OS scheduler (launchd or cron).

**Fallback.** If a hook capability the safety layer depends on is missing,
v1 refuses tasks above T0 on that surface. The support matrix (OQ-1) records
the result.

## Gate adapter

**What the gate accepts.** loop-contract's gate (`fold_ledger.py`) reads one
row per unit, with these fields:

- `id`;
- `verdict` ∈ {PASS, FAIL, PARTIAL, BLOCKED};
- `evidence[]`;
- `proof_cmd`, unique per verdict;
- in done mode, `fix` with `fix_verdict = PASS`.

In done mode the gate fails on any PARTIAL or FAIL row. `--verdict-vocab`
takes exactly three words and renames only the verdict at report level.

**Files.** Raw harness rows (`ledger.jsonl`, `verdicts.jsonl`) store the
**harness state** in `verdict`. The gate never reads them. `gate.run` calls
`fold.project()`, which writes:

- `gate/ledger.jsonl`: one row per unit in the latest scope. Descoped units
  are dropped. Accepted leads are added; accepting a lead writes scope
  v(n+1) and re-pins.
- `gate/verdicts.jsonl`: the mapped history.
- `gate/profile.json`: the profile exported from `gate.yaml` as JSON, since
  the gate uses only the standard library.

For rows and scope, fold_ledger reads only the `gate/` files, including for
`--remaining`. It also reads the inputs its check flags name: `claims.jsonl`,
`outputs.jsonl` and `outputs/submitted/`. The gate hook gives it read-only
copies.

| Harness state | Gate row (`verdict`, extra fields) | User-facing state |
| --- | --- | --- |
| PASS | PASS; `evidence` = recorded output paths; `proof_cmd` = `<pack entrypoint> --unit {id} --attempt {n}`; `fix` = deliverable path relative to `--repo-root`, `fix_verdict = PASS` | Checked |
| PASS_FLAGGED(reason) (a check failed twice) | PARTIAL; `missing = reason`; `evidence`, `proof_cmd` as for PASS | Done, not fully checked: reason |
| UNVERIFIED (no executable check) | PARTIAL; `missing = "no executable check"`; `evidence` = the V1 critic verdict record; `proof_cmd = harness-critic verify --unit {id} --attempt {n}` | Done, not fully checked |
| FAIL (latest verdict) | No row. `gate.run` refuses to run while any unit's latest verdict is FAIL. The 2nd FAIL is handled by `ledger.append` (§Trust boundaries, writers): PASS_FLAGGED or BLOCKED, and the task continues | — |
| BLOCKED(reason) | BLOCKED; `blocked_reason` | Partly done |
| Investigation SUPPORTED / REFUTED | PASS; `finding`; `evidence = [snapshot paths, "claims.jsonl:<line>"]` (these resolve under `check_citations`); `proof_cmd = research.check --unit {id} --attempt {n}`; `fix = "none"`, `no_change_reason = "investigation verdict"` | Checked |
| Investigation INCONCLUSIVE | PARTIAL; `missing = "inconclusive: <what would decide it>"`; `evidence` and `proof_cmd` as for SUPPORTED | Done, not fully checked |
| Investigation NOT_INVESTIGATED | BLOCKED; `blocked_reason` | Partly done |
| DESCOPED_BY_USER | no row (removed in scope v(n+1)) | Listed in the report |

**Mode.**

- **done mode** applies only when all three of these hold:
  - every final unit state is PASS, BLOCKED, SUPPORTED/REFUTED or
    NOT_INVESTIGATED;
  - the BLOCKED count is at most `allow_blocked`;
  - at least one unit is PASS.
- **known mode** applies otherwise. It runs **without** `--oracle-cmd`,
  which fold_ledger allows. The check results are already recorded outputs
  cited in the rows, so a check that failed twice cannot fail the gate
  again. It also runs with `--allow-blocked = units`. That covers any PARTIAL
  state, too many BLOCKED units, or every unit BLOCKED. The receipt is
  written, and the state line shows the failed check next to the figure
  (GL-7).

`fold.project()` writes rows only for units with a terminal harness state.
Open units have no row and are listed as remaining. `--remaining` exit 1
means "items remain", not an error. An empty gate ledger at takeover means
nothing has been done yet.

`fold.project()` also writes `gate/scope.jsonl` in canonical bytes, and the
pin covers exactly those bytes. When a tier and task type has no specific profile, the tier default
profile (`T1.default`, `T2.default`) applies. `compile.apply` refuses the
card only if neither exists.

The receipt records the mode. A known-mode receipt makes the user-facing
state "Done, not fully checked".

**Profile.** `control/gate.yaml` holds one profile per tier and task type,
exported to `gate/profile.json`. Each profile sets:

- `--mode` (chosen as above);
- `--allow-blocked` in done mode (`units` for T1, so "Partly done" is
  reachable; `ceil(0.2·units)` for investigations); always `units` in known
  mode;
- `--verdict-vocab ANSWERED,ANSWERED-WITH-GAPS,UNANSWERED` (renames the
  report-level word only; per-unit verdicts are a row field, and the answer
  line uses `--headline`);
- `--scope gate/scope.jsonl`;
- `--expect-scope-sha` and `--expect-scope-count` (from pins);
- `--oracle-cmd` from `card.oracle.type`, checked against the pack manifest (profiles hold flags only), plus `--oracle-stdin` (card arguments as JSON) and `--oracle-timeout ≤ 1790`;
- `--out gate/REPORT.md`, `--decisions gate/decisions.jsonl`, `--group-by unit_type`, `--repo-root <job dir>`;
- `--skip-citation-check` off;
- `--sources claims.jsonl` (a superset of loop-contract's `sources.jsonl` row format), `--fetch-log`, `--snapshots`;
- the freshness table;
- `timeout` ≤ 1790 s.

The spec's appendix carries a complete example. Scripts the agent writes are
evidence, never the oracle. `card validate` rejects `oracle.executable =
true` unless `oracle.type` is a check registered in a pack manifest.

**Checks added in loop-contract 1.1.0** (see the implementation plan):

- **`check_claims`** (`--sources`, `--fetch-log`, `--snapshots`). For every
  claim row `{claim, claim_type, load_bearing, source_id, url, retrieved_at,
  snapshot_sha, verbatim_quote, source_date, source_class, status}` it checks:
  - the snapshot is present in the fetch log;
  - the quote is a substring of the snapshot;
  - load-bearing claims have ≥ 2 independent origins (distinct registrable
    domain and text hash). The registrable domain is the last two hostname
    labels, or the last three when the top-level label is a 2-letter country
    code (`bbc.co.uk`, `law.gov.ir`), so `www.ft.com` and `markets.ft.com` are
    one origin;
  - freshness per `claim_type`;
  - `status ≠ UNREACHABLE`.

  The gate does no network I/O. `check_citations` is unchanged.
- **`numbers_traced`** (`--numbers-trace`). It applies to tokens the
  normaliser tags as money or quantity. It runs on
  `outputs/submitted/report.md`, every submitted text deliverable, the
  headline, and the included header. A token
  counts as traced when it equals one of these:
  - a hook-recorded output of a registered entrypoint;
  - a value quoted in the claim ledger;
  - a model-registry cell;
  - an owner-stated value recorded through `harness-model assume` with
    `owner_confirmed`. These are excluded: dates, times (`10:30`), version
  strings (`1.1.0`), IDs and template-rendered counts. Plain years are
  counted. The sign is ignored when matching (12 traces to a recorded −12).
  The check scans submitted `.md`, `.txt` and `.csv` files. An untraced token adds `numbers_untraced` to the receipt's `caps`, and caps the state at
  "Done, not fully checked". The normaliser is a stdlib module vendored into
  loop-contract, with fixtures shared with `harness-normalize`.
- **Logs** (`--require-logs`). `contradictions.jsonl` and
  `search_log.jsonl` exist and are part of the event chain.

**Report before the gate.** The model writes `work/report.md` (the
narrative) *before* `harness-gate request`, and the hook submits it with
`work/**`. The gate lints it for `numbers_traced`, the
R-10 state and the template lint. It also includes `answer_header.md`
(`--include`) and adds `report_sha` to the HMAC. After the gate, step 8 only
renders deterministic sections, and any later edit to `report.md`
invalidates the receipt.

**Hashes.**

- All harness hashes are sha256 over canonical JSON: sorted keys, `(',',':')`
  separators.
- `scope.jsonl` is written as canonical JSON lines ending in `\n`.
  `scope_sha` is the sha256 of its exact bytes, which is what fold_ledger
  hashes.
- `tree_sha` is the sha256 of the sorted `(path, sha256)` list of
  `outputs/submitted/**` and the job's scope paths, computed in-process.

## Gate and Stop semantics

- **Who runs the gate.** The agent calls `harness-gate request`. The gate
  hook (§Gate adapter) runs fold_ledger and writes the receipt. The Stop
  hook never runs the gate. It only verifies the receipt, with a 10 s
  timeout.
- **The Stop hook allows a stop when one of these holds:**
  1. A receipt exists and verifies against the current state. The receipt
     carries `caps` (for example `numbers_untraced` or `script_evidence`), and
     `caps` is part of the HMAC input. The user-facing state follows:
     - **Partly done:** any unit is BLOCKED or NOT_INVESTIGATED, in either
       mode;
     - **Done, not fully checked:** otherwise, if the mode is known or `caps`
       is not empty;
     - **Checked:** otherwise.

     Stop emits this state line from the receipt as `systemMessage`. **[v2]** When the outbox is not empty, only POST_GATE
     writes the DONE receipt.
  2. A hook-written stop record exists and no `RESUMED` event follows it.
     The records are `STOPPED_BUDGET`, `STOPPED_COST`, `STOPPED_STALL`,
     `STOPPED_TAMPER`, `PAUSED`, `REFUSED` and `ABANDONED`. In v2 they also include `REJECTED`,
     `AWAITING_APPROVAL`, `WAITING_EXTERNAL`, `PAUSED_VENDOR` and `ACTIVE`.
  3. `prompt_scope` is `inline_t0` or `new`, `pending_card` is unset, no job
     was compiled in this turn, and the answer carries its user-facing state.
     A T0 answer may use only "Done, not fully checked" or no state label at
     all; it never shows "Checked".
  4. The turn ends on a contact that `contact.apply` recorded, whether
     counted or exempt, or on a counted `AskUserQuestion`.
- **Final-message lint.** The final chat message is linted the same way as
  `report.md`.
  - Outside text rendered from `templates/*.json` (the state line and the
    answer header's status line), neither the final message nor
    `report.md` may contain any term from `control/state_lexicon.yaml`:
    checked, verified, reconciled, confirmed, matches, ✓, ✔, and their
    Persian equivalents. The gate enforces the same rule with
    `--lint-lexicon`, and a failure adds the cap `lexicon`.
  - **Under rule 1,** every money or quantity token must be in the receipt's
    traced set.
  - **Under rule 3 (T0),** a money token blocks the stop once and sets
    `pending_card` with a floor of T1.

  A violation blocks the stop.
- **Otherwise** the hook blocks, with a reason that names what is missing,
  and honours `stop_hook_active`. After 3 blocks with the same reason it
  writes `STOPPED_STALL` itself. That stall record cannot carry a question
  (§Turns and contacts).
- **Tamper.** If the chain, the config log or the HMAC fails, the hook allows
  the stop only as `STOPPED_TAMPER`.
- **Stale receipts.** Before every gate attempt, `run_gate` deletes
  `run/receipt.json` and appends `RECEIPT_REVOKED`. A receipt from an earlier
  passing run therefore never survives a later failed run.
- **Running subagents.** If any background subagent is still running (Stop's
  `background_tasks`), the task is not done. Stop allows the turn to end as a
  non-terminal wait (no completion record). These turns do not count toward
  the 3-block stall rule. The `<task-notification>` system turn resumes the
  task.
- **Resumable stops.** `STOPPED_BUDGET`, `STOPPED_COST`, `STOPPED_STALL` and
  `PAUSED` can be resumed. The job stays in `jobs/index.json` and keeps its
  `job_id` and grants. Its decision record is marked `interim`. A `RESUMED`
  event cancels the earlier stop record for rule 2. The terminal states are
  DONE, `REFUSED`, `ABANDONED` and `STOPPED_TAMPER` (until repaired), plus **[v2]** `REJECTED`.

## Turns and contacts

In interactive sessions `AskUserQuestion` returns its answer inside the same turn. In `-p` mode it is unavailable, so contacts go through `harness-contact` with defer and resume (§Hooks, platform facts)
(OQ-4).

- **Blocking contact.** A blocking contact is either an `AskUserQuestion`
  call or a contact rendered by `bin/harness-contact <type>`. Both are
  counted by PreToolUse.
  - **Types:** `question`, `plan`, `assumption_sheet`, `definition_confirm`,
    `decision_ack`, `cost`.
  - **Classifier.** The Stop hook runs a contact classifier on the final
    message. If the message asks for input anywhere in its text and was not
    rendered by `harness-contact`, it counts as a contact. After 50%
    progress, or once the cap is reached, such a message blocks the stop
    (except safety contacts).
- **No timers in v1.** Silence becomes visible only on the next user turn.
  v1 has no reminders.
- **Non-blocking previews** are plain statements inside the turn. A reply to
  one is handled as a correction on the next turn.
- **Before-spend contacts.** `plan`, `assumption_sheet` and
  `definition_confirm` come before any spend.
- **Exempt from the 50% denial:** `decision_ack`, `cost`, and every
  before-spend contact. `decision_ack` and `cost` are also exempt from the
  cap.
- **At the cap.** PreToolUse allows one `harness-contact <type> --handoff`,
  uncounted, once per task, for a pending before-spend contact only. Its writer records the contact (and `plan_sha` for a plan),
  then appends `PAUSED(handoff)`. Any other question is denied,
  and the agent records it as an assumption with the suggested option. A
  reply after a handoff that the model wrote counts as a contact.
- **Allowance reset.** Only a hook-observed `RE_UNDERSTAND` resets the
  start-question allowance, and only when it is triggered by user text that
  names the changed item.
- **Rework notice.** Rework from a correction is announced, not asked: "This
  changes 9 of 14 items; applying now." A correction to a definition goes
  through `definition_confirm`.
- **Progress.** When the frozen scope has more than 1 unit, progress is
  terminal verdicts divided by units. Otherwise progress is counted in
  milestones (outline, draft, final), and 50% is the draft milestone.
- **Question tags.** The question header carries a prefix that hooks parse:
  `[pref:<key>]` or `[cat:goal|input]`.
- **Opening line.** It is `opening.txt`, rendered by `harness-compile` from
  the card. The model prints it verbatim.

--------------------------------------------------------------------------------

## Outbox and approval (two lanes) [v2]

**In v1** connectors are read-only and email is drafts only. Every
irreversible class is denied. The design below takes effect
together with write connectors.

- **Lane A (phase approval).** An irreversible action whose result later
  units need (`produced_ids` consumed by `depends_on`) ends a phase.
  1. The task pauses at the phase boundary and shows an approval card.
  2. On approval, the action runs and is verified (V3), and the dependents
     continue.
  3. This is loop-contract's APPROVAL-before-the-write-phase.
- **Lane B (outbox).** Independent leaf actions, such as N reminder emails.
  1. PreToolUse **denies** the call and records the item.
  2. At the end, a single card covers all items, grouped by action type.
  3. On approval, `/dynamic-harness:approve` writes the approved hashes.
  4. Replay depends on the connector:
     - **script connectors:** `bin/harness-outbox exec` runs the approved
       items with its own write credentials;
     - **MCP connectors:** the agent re-issues the calls, and the hook allows
       only calls whose payload hash is approved.
- **Card rendering.**
  - The hook renders the card deterministically from structured fields:
    recipients, amounts, destinations, attachment names and diffs. The model
    may add one plain-language line per group, but it never replaces the
    fields.
  - Money, deletions, first-time recipients and anything over the approval
    limit get their own confirmation line.
  - Unverified content inside an item is shown as such.
  - Large batches show counts, the 5 most unusual items, a stratified sample, and a "see all" list.
- **Binding and staleness.**
  - Approval is per item or per group and binds the payload sha256.
  - The outbox is locked once previewed; any change re-renders the card.
  - Each item stores a precondition snapshot. If it changed, or 72 h passed
    (`expires_at`), a dry-run reruns and a new card is shown. Nothing stale
    executes.
- **Execution.**
  - Each item is journaled `INTENT` → call → `DONE(external_id)`, using an
    idempotency key where the API supports one.
  - Execution stops at the first failure and never auto-retries an item that
    is not idempotent.
  - On resume, an INTENT without DONE is reconciled by querying the external
    system. If the item cannot be queried, it is BLOCKED and needs a human.
- **After execution.** A second gate checks the real effects (for example,
  that each email shows up in Sent and each CRM record exists) and reports
  per item.
- **Rejection, timeout and partial approval.**
  - A rejected item becomes `REJECTED_BY_USER`. Its dependents become
    BLOCKED, and the item is not offered again in this task.
  - No reply leaves the task as `AWAITING_APPROVAL`, stored on disk. The
    report is delivered with the pending card.
  - With partial approval, only the approved items execute.

--------------------------------------------------------------------------------

## Communication contract

- **Contact.** A contact is one message to the user that waits for an answer.
  - Non-blocking messages (the opening line, the final report) are not
    counted against blocking contacts.
  - Cap: at most 5 blocking contacts per task, counted by a hook keyed on
    `job_id`. A thread follow-up that is the owner's own reply does not count.
  - Safety contacts are exempt from the cap: phase approvals, the outbox
    card, and permission prompts. Managed settings keep permission prompts
    rare.
- **Start questions.**
  - At most 3, batched into one `AskUserQuestion` call, multiple choice with
    a suggested option.
  - Ask only when the answer changes the plan (Horvitz's expected value is
    the rationale; there is no formula at run time).
  - Never ask what can be discovered, or a technical choice. Record those as
    decisions in `decisions.jsonl`.
- **Progress.** Defined in §Turns and contacts.
- **Preview.**
  - The preview goes out after ⌈0.15·N⌉ units, or after the outline
    milestone.
  - It is shown only when the task is large (more than 5 units, or a
    long-form deliverable) and ambiguity is recorded on the card.
  - Work continues while the preview waits for a reply, and the preview is
    not counted.
  - **Exceptions:** an investigation's research plan and a simulation's
    assumption sheet come *before* the spend. Both are blocking and count as
    one contact.
- **Late questions.** After 50% progress, the hook denies `AskUserQuestion`.
  The agent assumes and records the assumption.
  - The question category (goal or input) is a declared header tag:
    count-limited by code, category self-declared.
- **Corrections.** A user reply to the preview, or any later correction, is a
  **correction**, not a question.
  - It triggers `RE_UNDERSTAND` (§State machine) and shows the rework cost:
    "This changes 9 of 14 items; applying now." This is a statement, not a question. A definition correction goes through `definition_confirm`
  - Allowance reset: see §Turns and contacts.
- **BLOCKED units** appear only in the final report, never as separate
  contacts.
- **Ask once, remember.**
  - The PostToolUse hook stores answers as user-memory records (§Data
    contracts).
  - The opening line names the preferences applied ("Using your usual
    format: short report.") and, separately, the business settings in use
    ("Amounts in Toman, Jalali dates.").
  - The final report lists new or changed preferences.
  - `/dynamic-harness:memory` lets the user view, edit or delete them.
  - On conflict, the newest record wins. Task-scoped records never carry
    over to other tasks.
- **Language.** The user never sees technical terms. Every user-facing string
  comes from templates (§Business task profiles → User-facing templates).
- **Bypass mode.** The harness refuses to run with `bypassPermissions`.

--------------------------------------------------------------------------------

## State machine and lifecycle

```text
NEW → UNDERSTANDING → EXECUTING(phase k)
         ↑   ↑             │
         │   └──── RE_UNDERSTAND (user correction | 1 gate re-entry max)
         │                 ├─→ [v2] AWAITING_APPROVAL(lane A) ─approve→ EXECUTING(phase k+1)
         │                 ├─→ GATE ─fail→ EXECUTING (retry; [v2] deep path) ─stall→ STOPPED_STALL
         │                 │        └pass→ DONE      [v2]: └pass→ AWAITING_APPROVAL(lane B) → EXECUTING_OUTBOX → POST_GATE → DONE
         │                 ├─→ STOPPED_BUDGET
         │                 └─→ PAUSED (unrelated request / user pause)
         └──── resume ("continue", approval reply) from STOPPED_BUDGET | STOPPED_COST | STOPPED_STALL | PAUSED | EXECUTING (stale lease) | [v2] AWAITING_APPROVAL
[v2] REJECTED: approval rejected for an item that other items depend on → dependents BLOCKED → report → terminal

Business states:
DONE + model registry entry ("MODEL_READY"): scenario runs allowed, each writes its own receipt (§Business simulations)
ABANDONED         owner abandoned the job with `/dynamic-harness:status --abandon <job>` (typed); terminal; decision record
REFUSED           decision class with policy `refuse`; terminal; decision record; template "I can't help with this kind of decision. Please ask a qualified professional."
STOPPED_COST      money cap reached (§Budget and failure handling)
STOPPED_TAMPER    hash mismatch (§Gate and Stop semantics)
[v2] WAITING_EXTERNAL  waiting on a third party (supplier reply, document); timer + reminder; lease TTL 7 days
[v2] PAUSED_VENDOR     connector or provider unavailable (outage, shutdown); retries do not spend budget; lease TTL 14 days
[v2] Automation spec:  DRAFT → DRY_RUN → CANARY → LIVE ⇄ PAUSED → RETIRED   (§Automations; any edit → DRAFT)
[v2] ACTIVE            an automation or monitor in LIVE, with health checks; never "done"
```

- **Location.** Every task has run state in `${CLAUDE_PLUGIN_DATA}/runs/<job>/`,
  plus `.claude/loops/<job>/{requests,work}/` in the workspace. Both are
  located through the per-session `active_task` pointer.
- **Jobs index.** `${CLAUDE_PLUGIN_DATA}/jobs/index.json` lists non-terminal
  jobs. Resuming keeps the `job_id`. `resume_of` is used only to reopen a
  terminal job.
- **Follow-ups.** A follow-up in a thread to a DONE job creates a new job.
  Its card is copied from the previous card plus the delta (`follows:
  <job>`), and the probe receives the thread context. A filter change is a
  one-off unless the owner confirms the dictionary sentence.
- **Lease.** `lease.json` holds `{session_id, epoch, heartbeat, ttl=15 min}`.
  The heartbeat is refreshed on every UserPromptSubmit and tool call. Each
  takeover raises the epoch, and every append carrying a stale epoch is
  rejected.
  - A second session gets read-only status.
  - A stale lease is taken over only after `fold_ledger.py --remaining`
    recomputes the remaining work.
- **On disk.** The card, tier, frozen list, contact counter, budget and
  outbox are all on disk, so context compaction is safe. T1 tasks also get a
  minimal card and `state.json`.
- **Matching a new request** (loop-contract `INDEX.md` rules). A new request
  continues an existing task only when one of these holds:
  - it is shaped like a continuation ("continue", "now also…");
  - it names the same subject;
  - it overlaps the task's frozen scope.

  If two tasks match, ask in one line. An unrelated request runs as an inline
  T0 that does not touch the open task's ledger. If it needs ≥ T1, the open
  task is PAUSED with a handoff.
- **Crash recovery.**
  - Every unit appends `STARTED(pre_state_ref)` (git sha or file hashes).
  - On resume, a unit that has STARTED but no verdict is reverted to its
    pre-state or re-verified. It is never assumed done.
- **Re-understand.**
  - A new card version and a scope v(n+1) are created, with a recorded diff.
  - Removed units are `DESCOPED_BY_USER` and must cite the user turn.
  - Completed units carry over only after re-verification against the new
    oracle.
  - The tier can only go up.
- **Subagents** inherit `job_id`, tier and outbox. There are no nested
  cards: a unit that needs to be broken down returns BLOCKED and the
  orchestrator re-scopes it.
- **Version pinning.** The card records the core version, pack versions, the
  control hash and a snapshot of user memory. A mismatch on resume
  re-validates the gate. A memory change mid-task applies to the next task
  only.

## Deep path [v2]

In v1, a unit that fails two different attempts becomes PASS_FLAGGED or
BLOCKED (§Gate adapter), and the task continues. The deep path below is added in v2.

- **Trigger.** A unit has failed two different attempts. Each
  attempt logs a hypothesis. Code rejects an attempt whose hypothesis or diff
  repeats an earlier one. "Different" means a different tool, scope or
  method (loop-contract §6).
- **Method.** N = 3 independent repeats in fresh contexts, paid from a
  reserved 20% budget slice.
  - Candidates are filtered by the oracle.
  - The selector only breaks ties among passing candidates; the result is
    labelled "chosen by model among verified candidates".
  - If no candidate passes, the unit is BLOCKED.
- **Exceptions.** With no executable check, the deep path is skipped and the
  unit is reported `unverified`. If the unit is irreversible or the budget is
  exhausted, it is BLOCKED with a reason, and the task continues with the
  other units.
- **Propose–Probe (Harness²)** is future work. It is promoted only if it
  beats independent repeats at equal tokens.

## Budget and failure handling

| Condition | Detection (code) | Threshold (control layer) | Action | User sees |
| --- | --- | --- | --- | --- |
| Budget | Tool calls (primary), wall-clock, spawns, approximate tokens and money from the transcript | Per tier: T0 has no budget of its own (the tripwire at 8 calls promotes it); T1 150 calls / 30 min; T2 per unit 60 calls, task cap set by the owner in money | `STOPPED_BUDGET` + handoff | "Stopped, not done: 23 of 37 done. Continuing costs about X. Continue?" |
| Same failure | `failure_sig = (check_id, criterion_id, error_class)` from gate output | A unit's 2nd FAIL (signature recorded) | v1: PASS_FLAGGED or BLOCKED, written by `ledger.append` (§Trust boundaries); [v2] deep path; or one RE_UNDERSTAND per task. `STOPPED_STALL` is only the Stop-hook 3-block rule and the K = 5 units / 40 calls rule | Nothing until the report |
| Stall | No new verdict in K = 5 units or 40 calls; or the Stop hook blocked 3 times with the same reason | As listed | `STOPPED_STALL` | "Stuck on X because Y." |
| Money cap | Estimated spend from transcript usage × versioned price table (accuracy stated as ±30%) | Per task and per month, in the owner's currency (business profile) | `STOPPED_COST` + handoff; retries (and the [v2] deep path) check headroom before starting | "Stopped at your limit of X. Finishing needs about Y more." |
| Tamper | Hash mismatch | Any | `STOPPED_TAMPER` | Plain warning |
| Connector outage | Tool error class `unavailable` | 3 failures in a row | v1: pause the unit, continue the others, retry at the end; if every remaining unit needs the connector → `PAUSED(vendor)`, which UserPromptSubmit resumes. [v2]: PAUSED_VENDOR | Listed in the report |

**Continue after STOPPED_COST.** "Continue" grants a one-time per-task
increment equal to the estimate shown. It is recorded as an `APPROVAL{kind:
cost_increment, amount}` event and does not change the business layer. If it
would exceed the monthly cap, the reply says the monthly limit must be raised
in settings, and the task stays STOPPED_COST.

**Who owns the thresholds.** The operator owns every threshold in this table
through the control layer, except the money caps, which the owner sets in the
business layer. The card cannot raise any budget default.

Budget accumulates across sessions. Each "continue" adds a recorded
increment; it never silently resets. The money cap is expressed in the
owner's currency and shown before a long task begins.

## Learning (phased)

- **v1.** User corrections are logged verbatim, as evidence only. A hook
  verifies they came from a real user turn. Nothing changes behaviour.
- **v2.** Enabled once ≥ 20 corrections show a repeated pattern.
  - The editor drafts a typed diff to one skill.
  - **Lint.** The diff is rejected if it mentions tools, URLs, recipients,
    approvals, irreversibility or skipping verification. Lessons drawn from
    external content are flagged.
  - **Replay** runs in a sandbox: snapshot inputs, outbox forced on, no
    network. It must pass R-1…R-10 and the bypass corpus.
  - The operator approves, recording the approval hash.
- **Never learned:** permissions, budgets, approval rules, acceptance
  criteria, gate code, irreversible classes.
- **Privacy.**
  - Secrets are redacted at write time.
  - Learning and experience logs keep the shape of a run, not business
    contents. The decision record keeps business contents by design and has
    its own retention (§Business governance).
  - Retention is 180 days by default.
  - The owner consents to corrections being used.
  - Each installation has its own store.
- **Trigger.** Once per task, only when the task reaches a terminal state.

--------------------------------------------------------------------------------

## Security model

| Threat | Mitigation | Residual |
| --- | --- | --- |
| Prompt injection lowers the tier | The tier gates quality only, and the safety hooks always run. The probe sets a floor. Inferred fields can only raise the tier. Tripwires promote the tier | The injection may still mislead the work itself. The output carries a verification label |
| Guard bypass (curl, scripts, MCP, `sh -c`) | Class table; unknown → irreversible; egress allowlist; sandbox with network allowlist set up at install | Novel patterns outside the sandbox → measure recall against the corpus |
| Tampering with control, ledger or gate through Bash | §Trust boundaries and protected writes: hooks are the only writers; protected paths; sandbox `denyWrite`; HMAC receipt; hash chain verified after every append; config log; fail closed | Same OS user: tampering is *detected*, not always prevented |
| Hooks disabled | `ConfigChange` hook; `disableBypassPermissionsMode`; harness hooks installed in managed settings before `allowManagedHooksOnly` is set; SessionStart canary round-trip; `allowUnsandboxedCommands: false` | A local admin can still uninstall |
| Approval spoofing or TOCTOU | Deterministic card; payload hash binding; outbox lock; expiry | A user who approves without reading → own confirmation line for money and new recipients |
| Memory poisoning | Hook-only writes; closed key and value enums; values with URLs, emails or > 40 characters rejected; free text only after `/dynamic-harness:memory` confirmation | An owner picking a misleading option can still store a wrong style preference |
| Irreversible action with no network traffic (Mail.app via `osascript`, `open mailto:`, Shortcuts) | Those commands are denied; files the agent wrote run only through `harness-sandbox-exec`; MCP servers not in the registry are denied | Unknown local automation paths, so the bypass corpus is extended |
| Exfiltration during research | Lanes enforced by `agent_type`; query list frozen from the plan; query guard; researcher output schema-validated at SubagentStop | Covert channels in allowed queries |
| MCP servers and hooks run outside the sandbox (OQ-4 spike) | MCP servers not in the connector registry are denied; registered servers are trusted connectors; hooks are the trusted writers | A registered MCP server that is malicious is not contained |
| Forged verifier verdicts or snapshots | Verifier prompt must equal `harness-critic render` (hash recorded); snapshots only via `harness-claims fetch` with a hook-written fetch log | WebFetch returns a summary rather than the raw page; `harness-claims fetch` stores the raw page |
| Lesson poisoning | Lint, provenance, replay in sandbox, human approval | Operator fatigue → batch limit of 5 lessons per review |
| Data exfiltration (lethal trifecta) | `egress_with_data` class; domain allowlist; per-card trifecta leg | Covert channels inside allowed domains |
| Secrets in logs | Redaction scanner at write time; shape-only logging | Unknown secret formats |

**Setup.** A plugin cannot ship permissions or a sandbox, so
`/dynamic-harness:setup` writes them into user or managed settings, with consent:
- the deny list, including Read/Grep/Glob denies for `keys/` and `experience/`;
- sandbox `denyWrite` for every protected path (§Trust boundaries and protected writes);
- sandbox `denyRead` for `${CLAUDE_PLUGIN_DATA}/keys/` and `${CLAUDE_PLUGIN_DATA}/experience/` (R-5);
- `sandbox.allowUnsandboxedCommands: false` and an empty `sandbox.excludedCommands`;
- an empty `sandbox.network.allowedDomains` in v1, because sandboxed Bash needs no network (the hooks fetch). The egress allowlist applies only to WebFetch, WebSearch and hook fetches;
- `disableBypassPermissionsMode`;
- `sandbox.network.strictAllowlist: true` and `failIfUnavailable: true`. Without `strictAllowlist`, `allowedDomains` is not enforced in bypass mode. A plugin cannot set these keys, so setup writes them in user, managed or `--settings` scope;
- `cleanupPeriodDays`;
- where managed settings are used, the harness hooks are installed there before `allowManagedHooksOnly` is set.

The harness refuses to start a task above T0 if setup has not been done.
As built (P5), setup writes the user file only for `strictAllowlist` and
puts everything else in the workspace's `.claude/settings.local.json`; see
§Build decisions (P5 setup).

--------------------------------------------------------------------------------

## Business task profiles

Business harm comes from **reliance**: a number or claim someone acts on.
Side effects are not the only source of it. The tier selector therefore reads
`reliance`, `computed_numbers`, `decision_class` and `cost_of_error` as well
as `irreversible`. Every task type below has a named **verification family**.
Domain packs implement these families as executable checks, so most business
work reaches V2 and T1 without falling back to T2. Single-session work with
up to `t1_max_units` units stays T1, with a frozen scope.

### Task taxonomy

| # | `task_type` | Typical tier | Verification family (V2 oracle) | Owner-only inputs | Typical irreversible | Done means |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `quick_question` (general knowledge, wording) | T0 | — (V0) | — | — | Answer delivered |
| 2 | `data_question` (own data) | T1 | Reconciliation: second computation path, control total, coverage, unit check | Source of truth, metric definition (once, then dictionary) | — | Figure + answer header + tie-out passed |
| 3 | `document` (proposal, contract draft, copy, customer reply) | T1 draft; T2 if sent | Required-items checklist; every figure traced; no placeholders; policy check (footer, tone) | Audience, must-include terms | Send, publish, sign | Checklist met; figures traced |
| 4 | `analysis_report` (KPI, monthly report) | T1; T2 if multi-session or > 5 units | Each KPI recomputed by a second method; sanity bounds vs prior period | KPI definitions, period, baseline | Distribution | Each KPI traced; exclusions listed |
| 5 | `investigation` (market, competitor, supplier, regulation, root cause) | T1 when the plan, after recorded omissions, has ≤ `t1_max_units` hypotheses and one session; T2 otherwise | Claim ledger: reachability, quote match, ≥2 independent origins, freshness, numeric recompute; root cause on own data → reconciliation + bridge | Decision served, cost of error, depth, geography, data-sharing limits | Contacting suppliers or prospects | Every hypothesis has a verdict; R-13 holds |
| 6 | `simulation` (forecast, what-if, break-even, Monte Carlo) | T1 (T2 when multi-session or risk is high) | `model-checks`: accounting identities, back-cast ≤ 0.5%, unit tags, metamorphic tests, backtest vs naive seasonal baseline, independent recompute, reproducibility | Growth, seasonality, planned changes, risk appetite (top 3 by sensitivity) | — (decision card) | MODEL_READY; R-14 holds |
| 7 | `decision_support` (vendor, pricing, make vs buy) | T1; T2 when risk is high or the class needs sign-off | Weighted score recomputed by code; flip test (weights ±20%); constraint check | Criteria and weights (always the owner's) | Sign, purchase | Recommendation + weights + what would change it |
| 8 | `one_off_operation` (one invoice, CRM update) | T1 if R0; T2 if any irreversible class (v2: write connectors) | Post-condition check (record exists, values match); before/after diff | Which record | Send, pay | Post-check passed; fix path stated |
| 9 | `bulk_operation` (300 emails, 2,000 prices, 5,000 categorisations) | T2 | Per-item deterministic checks; stratified sample audit (20–30 items); count reconciliation; dry-run diff | Batch approval, caps | Mass send, price publish | Counts reconcile; failures listed |
| 10 | `automation_build` (recurring report, follow-up flow) | T2 | Dry run on real data; canary; N=3 parallel cycles match the manual result | Trigger, recipients, caps, stop rules, owner | Every future run | Spec LIVE (§Automations) |
| 11 | `monitoring` (alerts) [v2] | T2 (`automation_create` is irreversible) | Precision and missed-event rate on known past events | Thresholds, channel, quiet hours | Any automatic action | Never "done"; ACTIVE with health report |
| 12 | `planning` (90-day plan, hiring plan, business plan) | T1 (one session, ≤ 5 units); T2 if multi-session, > 5 units or risk high | Constraint checks: budget sums, dependency order, capacity | Goals, constraints, budget ceiling | Committing spend or hires | Plan with assumptions, owners, milestones; constraints pass |
| 13 | `regulated` (tax, payroll, legal notice, licence) | T2 + professional | Reconciliation and rule checks; professional sign-off is the only real oracle | Jurisdiction, entity type, period | Filing, payment, notice | "Prepared, not final" hand-off package |

**Choice classes** classify the open choices in a task. They are separate from
the `decision_class` card field. They replace the old split between "only the
user knows" and "technical":

| Class | Handling |
| --- | --- |
| Fact | The agent looks it up |
| Technical | The agent chooses and logs the choice in `decisions.jsonl` |
| Owner value | Price, discount, supplier, risk appetite, wording to customers. The owner chooses, or accepts a named default shown on the first screen of the report. A default is not allowed above the spend threshold |
| Irreversible | Approval (§Outbox and approval) |

### Business questions (task types 2 and 4)

- **The model never sums rows.** It writes the query or script, and the
  script computes every aggregate (R-11).
- **Answer header (R-12).** Line 1 gives the figure, metric, period and unit.
  Line 2 gives the basis: source, data-up-to date, exclusions and row count.
  Line 3 gives the check status in plain words:
  - "Checked: matches bank deposits."
  - "Done, not fully checked: 3 undated rows left out."
  - "Done, not fully checked: <reason>. Don't use this for tax or payments
    yet."

  The rows used are attached as an Excel file.
- **Oracles by answer type.**

  | Answer type | Check |
  | --- | --- |
  | Aggregate | A second computation path (the file's own total row, or a different filter); reconciliation to a control total where one exists (bank, invoice register); unit, coverage and outlier checks against the prior period |
  | "Why" (bridge) | Price, volume, mix and cost effects must sum to the total change within a stated residual. Otherwise the answer says "possible reason", not "because" |
  | List | Row count and sum equal the filtered aggregate; each row shows its ID and date |
  | Projection (runway) | Inputs shown with their dates; known commitments that were left out are listed, never treated as zero; a backtest of the last 3 months' estimates is shown |

- **Sources.** The source registry stores `{source_id, role, as_of,
  coverage_min/max, unit}`. Sources are always shown with their age.
  - The owner sets source precedence per metric once.
  - When sources disagree, the answer shows both figures and the gap. The
    agent never picks one.
  - When the data ends before the end of the period, the answer says
    "incomplete: data ends <date>".
- **Metric dictionary.** Each entry stores `{metric_id, inclusions,
  exclusions, unit, currency, source_precedence, date_anchor, version,
  approved_by, approved_at}`.
  - Entries are created only when the owner confirms a rendered sentence:
    "Revenue = sales invoices net of returns, excluding VAT, in Toman.
    Confirm?"
  - An unknown metric or period becomes a start question.
  - A definition change bumps the version and restates past figures with a
    visible "restated" line.
- **Thread context.** Follow-ups inherit `{thread_id, snapshot_id, sources,
  dictionary versions, last query hash}`.
  - Any change prints one line: "Differs from the 1,240 million Toman shown
    earlier: returns now excluded."
  - A follow-up that is the owner's own reply is not a contact (R-7).
- **Recurring questions** are loop-contract recurring-jobs contracts
  (`reconcile` / `dataquality` / delta reporting). Each one runs from a saved
  template holding its metrics, sources, period rule and checks. Same snapshot plus same query hash gives the same figure,
  and this is regression-tested. Scheduled runs are [v2]
  (automations). In v1 a recurring question re-runs its saved template when the
  owner asks.
- **Answer log.** Every money answer is recorded in the decision record
  (§Business governance). There is no separate log. It can be exported as
  CSV.

### Business investigations (task type 5)

- **Units are hypotheses or questions,** frozen from the approved research
  plan.
  - Each domain pack supplies a hypothesis checklist. For customer or sales
    loss it covers price, product/quality, competitor move, channel/traffic,
    seasonality, data artefact and cohort. Omitting an item needs a recorded
    reason.
  - Verdicts: `SUPPORTED`, `REFUTED`, `INCONCLUSIVE`, `NOT_INVESTIGATED`,
    mapped to gate rows by the gate adapter. The answer line is passed with
    `--headline`.
    "Unknown" is a valid terminal verdict, not a failure.
  - New leads go to an append-only, capped leads lane (≤ 5). R-3 counts the
    frozen hypotheses plus the accepted leads. Accepting a lead that takes the
    units above `t1_max_units` promotes the task to T2.
- **Claim ledger.** `claims.jsonl` uses loop-contract's `sources.jsonl` row
  format, with more fields. Only the SubagentStop writer adds rows. Each row stores `{claim, source_id, url, retrieved_at,
  snapshot_sha, verbatim_quote, source_date, source_class: primary |
  secondary | aggregator | unknown}`. The new `check_claims` (`--sources`, loop-contract 1.1.0) checks the rows; `check_citations` is unchanged. Rows also carry `claim_type`, `load_bearing` and `status`. It checks:
  - reachability, with the snapshot stored;
  - that the quote is a verbatim substring of the snapshot (a search snippet
    is not "read");
  - independence: each load-bearing claim needs ≥ 2 origins with distinct
    domains and text hashes, so syndicated copies count once;
  - freshness per claim type (prices and law need an as-of or in-force
    date);
  - numeric recomputation;
  - a contradiction register;
  - an append-only search log.

  Whether a source *supports* a claim is a labelled V1 judgement, made by a
  fresh-context checker that sees only the claim and the quote. The report
  separates the two: "source exists and quote present" (checked) vs
  "supports the claim" (judged).
- **Lane split against the lethal trifecta.** The three roles run as
  separate subagents with tool restrictions:
  - the data analyst has owner files and no web access;
  - the web researcher has web access and no owner files, and receives a
    sanitised question;
  - the researcher returns only schema-valid rows `{claim, quote, date, url,
    snapshot_sha}`. The hook fetched every snapshot itself, and code checks each
    quote against its snapshot before the main agent sees it.

  The PreToolUse lanes hook enforces this split by `agent_type` (§Hooks).
  Page text never reaches the main agent as free text: SubagentStop accepts
  only schema-valid rows. Queries come from the approved plan's frozen query
  list, or are hash-matched to it. They are length-capped. The query guard
  denies them if they contain digit runs, tokens from owner files, or
  `protected_terms` (customer and supplier names, unreleased prices,
  contract terms). Snapshots are fetched only by `harness-claims fetch` into
  the run state's `snapshots/` and kept for 180 days. Fetched content can never become a
  preference, a lesson or a tier input.
- **Preview.** Before any spend, the preview is a research plan: the
  question tree, hypotheses, source plan (including languages), stop rule,
  cost estimate, and the assumptions to confirm. Owner-only inputs:
  - the decision it serves and its cost of error;
  - depth and deadline;
  - which competitors and markets;
  - what may leave the machine.

  The plan is a `plan` contact, shown before spend. Approving it re-compiles
  the job (the tier only goes up) and writes a per-job egress grant. The
  grant is GET-only and limited to the researcher lane. It is capped at the
  plan's page budget, excludes `protected_terms`, and expires at the
  terminal state. Domains outside the grant are denied.
- **Stopping.** The investigation stops on:
  - **saturation:** the last k = 5 searches added no new source or claim;
  - **coverage:** every hypothesis has a verdict;
  - **budget:** pages fetched and tokens read;
  - **confidence:** every load-bearing claim passes its checks.

  Anything else ends "stopped, not done", with open questions and the cost to
  continue. There is no deep path for research: an unresolved hypothesis
  becomes INCONCLUSIVE. One "the question changed" stop is allowed outside
  the cap, and it carries the finding.
- **Report.**
  - The answer and recommended decision come first, in five lines or fewer.
  - Confidence is High, Medium or Low, computed by code from source count,
    source class and freshness.
  - Then: what was not checked, contradictions, assumptions, what would
    change the answer, and a valid-as-of date with re-check triggers.
  - Root-cause claims on the owner's own data say "associated with" or
    "supported by comparison", never "caused".
  - Quotes keep their original language, with translations flagged.
  - When the decision is costly, the report ends with a **human-only
    verification list**, for example "call the supplier's reference before
    paying the deposit". The harness cannot block a payment the owner makes
    later, and says so.
- **Updates** diff against the stored ledger and snapshots. They do not start
  over.

### Business simulations (task type 6)

- **Assumptions are parameters, not questions.** `assumptions.yaml` holds
  `{id, value, unit, period, source: history | benchmark | guess | owner,
  as_of, owner_confirmed, sensitivity_rank, expires_at}`.
  - Defaults come from the data first (trailing growth, seasonal index), then
    benchmarks, then a labelled guess.
  - A quick tornado pass ranks sensitivity. Only the top 3 become start
    questions.
  - The preview is the **assumption sheet**, one editable table: "OK" or
    edit.
  - Assumptions found after 50% progress are recorded and flagged, never
    asked.
  - Assumptions never go into user memory. They live with the model and
    expire.
- **`model-checks` pack (V2).** Each check is code, writes to the ledger, and
  feeds the gate:
  - **accounting identities:** opening + inflows − outflows = closing in
    every period, and segments sum to totals;
  - **back-cast:** the model reproduces actual historical bank closing
    balances within 0.5%, which catches transfers between the owner's own
    accounts, duplicates and VAT handling;
  - **tags:** unit, currency and period on every input and output; mixing
    Toman and Rial, nominal and real, or monthly and annual fails;
  - **metamorphic tests:** doubling price at zero elasticity doubles revenue;
    −20% sales scales variable costs only; zero growth gives a flat line;
  - **backtest:** held-out months against a naive seasonal baseline; the
    model fails if it does worse;
  - **independent recomputation** in a fresh context and a different medium
    (Python vs the spreadsheet formulas);
  - **reproducibility:** seed, input hashes and versions are recorded.
- **Model risk rules.**
  - Use the simplest model first: naive seasonal → linear → driver-based. A
    more complex method must beat the baseline on held-out data.
  - Code flags: a horizon longer than the history, inputs outside their
    historical range, fewer than 2 seasonal cycles.
  - Monte Carlo states and justifies its distributions and correlations.
    Assumed independence is listed as an assumption.
- **Deliverable.** A live spreadsheet:
  - one coloured Assumptions sheet;
  - formula-only, protected calculation sheets;
  - a Checks sheet that repeats the identities as formulas, so the owner's
    own edits get checked too;
  - a one-page plain summary.

  The gate lints for constants in calculation ranges, `#REF` errors and
  failing checks. A file the owner sends back is diffed against the delivered
  hash before reuse.
- **Results** are ranges (low/base/high, or P10/P50/P90), with the top 3
  drivers, the **flip point** ("you run out of cash in month 7 if sales fall
  more than 14%"), rounded in the owner's unit, and the sentence "This is a
  scenario, not a prediction." Every number traces to a cell (R-14).
- **Input contract.** Bank exports are CSV or XLSX with date, signed amount,
  account, currency and description (`business/schema/bank_export.json`). A
  code-checked classifier tags transfers between the owner's own accounts.
  These transfers are excluded from revenue and cost, and the report shows
  how many were excluded.
- **Decision card.** When `decision_class = financial` and the result feeds
  a hiring, debt, pricing or ordering decision above the owner's threshold, the final report carries a
  decision card: the recommendation as a range, the flip condition, the
  unconfirmed assumptions that matter, and a "stale after" date. The owner
  acknowledges it in a `decision_ack` contact, which is exempt from the 50%
  question denial.
- **Iteration.** The task ends DONE, and the model gets an entry in the
  model registry. "MODEL_READY" means DONE plus that registry entry, not a
  separate lifecycle.
  - "Now try 15%" is a **scenario run**. It opens a child job `<job>.s<n>`
    with `scenario_of: <job>`. `select_tier` applies the scenario rule
    (§Tier selection), and the compiler sets `multi_session = false`. The
    PostToolUse writer on `harness-model scenario` derives the child card
    from the registry entry and calls `compile.apply` in-process,
    clears `pending_card`, and copies the assumptions and the parent's
    `deliverable_path` into `<job>.s<n>/work/`, so no contact is needed. Then,
    in order:
    1. the agent applies the delta through `harness-model assume`;
    2. it runs `harness-run model.recalc` (a deterministic re-execution) and
       `harness-run model.cells`;
    3. the checks are re-run, the result is diffed against the base, and the
       gate writes the child's own receipt;
    4. after DONE, `harness-model register --scenario` appends the run to the
       parent's `scenario_history[]`.

    The parent's DONE receipt is kept. The
    tripwire does not fire for `harness-model scenario`. Target cost ≤ 2k
    tokens plus the checks.
  - **Returned spreadsheet.** `harness-model import <file>` diffs it against
    the delivered hash. If only Assumptions cells changed, it runs a scenario.
    Any other change opens a new job (`follows: <job>`, `task_type =
    simulation`, with the delta in the card). `assumptions.yaml` stays
    authoritative, and sheet edits are imported into it.
  - A structural change ("add a third branch") is RE_UNDERSTAND.
- **Persistence.** `harness-model register` writes the registry entry, via a
  hook, after a DONE receipt. Schema: `{model_id, version, input_hash,
  assumptions_ref, deliverable_path, deliverable_sha, scenario_history[]}`.
  Before the gate, `harness-run model.cells` records every cell of the
  submitted workbook in `outputs.jsonl`, so `numbers_traced` can check R-14 on
  the first forecast. "Update with October actuals" is a
  card with `resume_of` and `task_type = simulation`. "Update with October actuals" first produces a variance
  table against actuals. That forecast error feeds evaluation (stage 8) and
  learning (as evidence).

### Business operations and bulk (task types 8 and 9) [v2]

- **Approval card for bulk.** Counts, a stratified sample including the 5
  most unusual items, and the exceptions (large amounts, first-time
  recipients, missing fields).
  - Per-item deterministic checks: the recipient is allowed by policy, the
    amount equals the ledger, there is no unfilled placeholder, the item was
    not already sent.
  - Reversible bulk (R0/R1) gets a sample audit and reconciliation. Only
    irreversible bulk gets the full card.
- **Execution.**
  - Rate limits per connector, set in the control layer.
  - A circuit breaker (3 failures in a row, or > 2% failed). Transient errors
    retry with backoff. Items that are not idempotent are never retried.
  - Idempotency key `(automation|task, period, recipient, document_id)`,
    checked against a persistent sent-log that survives across runs. Before
    any retry, reconcile against the real Sent folder or the shop's current
    state.
  - Hold window: 15 min for messages where the provider supports scheduled
    send. Before-values are stored for price changes.
- **Policies** (business layer) are checked **per item at execution time**,
  not at approval, because a policy can change in between. An owner override
  ("give this customer 30%") goes through an exception path with a recorded
  reason.
- **Matching vs posting.** Fuzzy matching (bank reconciliation) only
  *proposes*. Auto-post happens only on an exact amount-plus-reference match.
  Everything else goes to a review queue with a confidence band.

### Automations (task types 10 and 11) [v2]

In v1, `automation_create` is denied.

- **Build vs run.** A Build task produces an **Automation Spec**, a
  versioned, hashed object in the business layer. It holds the trigger,
  allowed tools and scopes, recipient class, volume caps, approval mode per
  action class (auto / digest / each item), owner, purpose, data touched,
  next review and the kill command.
- **Runtime.** Where possible, an automation compiles to a **deterministic
  script** (no model at run time), triggered by the OS scheduler (launchd
  or cron). Claude Code's Cron tools fire only while their session is open,
  and cloud routines cannot see local files (OQ-4 spike). A run that
  needs judgement is a new task whose card is copied from the spec, never
  inferred. Its tier is the spec's and cannot be lowered. The always-on
  safety hooks apply.
- **Lifecycle.** `DRAFT → DRY_RUN → CANARY → LIVE ⇄ PAUSED → RETIRED`.
  - **DRY_RUN:** real data, all writes diverted to the outbox and rendered,
    nothing executed. If a system has no sandbox, the card says "not tested
    against the real system".
  - **CANARY:** one item, or the owner's own address as the recipient.
  - **LIVE:** the first 5 runs are approved per item, then digest approval
    ("12 emails, 1 unusual").
  - Promotion to LIVE is a V3 action bound to the spec hash. Any edit sends
    the spec back to DRAFT.
- **Standing approval.** Bound to the spec hash and caps. It is exempt from
  the 72 h expiry, is reconfirmed every 30 days, and runs out at the 90-day
  review, which pauses the automation if overdue. Anything outside the
  envelope goes to the owner's queue as `AWAITING_APPROVAL`.
- **Monitoring.**
  - Each run is compared with its history; a volume outside its envelope
    holds the run.
  - Input schema check; an empty result is flagged.
  - A heartbeat alerts on a missed run.
  - The owner gets a one-screen run history with pause and kill.
- **Credentials.**
  - A connector registry lists the scopes each automation needs and uses a
    separate low-role identity per automation where the service allows it.
  - Write credentials are held only by the outbox runner
    (`bin/harness-outbox exec`), never by the agent session. This applies
    to script connectors; MCP connectors are limited by replay with hash
    approval (shortcut: MCP tools belong to the session; upgrade when MCP
    supports per-call credentials).
  - An expired token ends the run as "stopped, not done" plus a
    notification. The run never prompts for a login.
- **Multi-day workflows** such as supplier onboarding use
  `WAITING_EXTERNAL`.

### Business governance

- **Decision record (R-15).** At every terminal state the Stop hook calls
  `record.write`. It copies the card, the REPORT hash and its structured
  sections, the gate receipt and the cost into `${CLAUDE_PLUGIN_DATA}/records/`, append-only, and
  pseudonymises every field and table cell tagged in `data_classes` /
  `personal_data` on the way, keeping only the originals' hashes. It also
  runs the personal-data scanner over all copied text, including the
  `REPORT.md` narrative. The record stores the report's hash plus its
  structured sections only. Because a
  record never holds personal data values, deleting a subject's data means
  deleting the task artefacts (the job directory); the record can stay. It is assembled from
  existing artefacts; there is no new log. Deleting a job never deletes its
  record. Together these hold:
  - the request, pseudonymised, plus its hash, and the requester;
  - the card versions with provenance;
  - the inputs `{path, sha256, as_of}`;
  - the assumptions, taken from fields with `inferred` provenance, not from
    prose;
  - checks run and not run, with verdicts;
  - approvals `{approver_id, role, ts, channel, payload_hash}`;
  - the model ID, harness, pack and control versions;
  - the cost.

  It stores references and hashes, never personal data values. In v2,
  `/dynamic-harness:forget` also redacts subject tokens in the record. Retention is set in
  the business profile, defaulting to 7 years to match bookkeeping periods
  (OQ-8). `/dynamic-harness:export --task|--period` produces CSV in v1, and a
  plain-language PDF in v2.
- **Decision classes.** `decision_class ∈ {none, financial, tax, legal,
  employment, medical, safety}`. Policy per class:

  | Policy | What it does | Default classes |
  | --- | --- | --- |
  | `inform` | Mandatory "not professional advice" line plus the assumption list | financial |
  | `signoff` | The output stays DRAFT until a named professional approves it | tax, legal, employment |
  | `refuse` | The task is declined | medical, safety |

  Filing a legal document is an irreversible action (denied in v1), not a
  class policy.

  Labels state what they cover: "figures recomputed; whether the tax rule
  applies is not checked".
- **Approval matrix [v2].** Action class × amount band → required approvers
  (count, role, ≠ requester). v1 has a single owner, and R-16 does not
  apply in v1.
  - Delegations are `{delegate, scope, cap, expires}`.
  - Above the high threshold, approval is collected outside the agent session
    (a second device or a signed link; OQ-7) and bound to the
    payload hash.
  - Changes to the business layer need the owner and are logged.
  - The owner sees a plain change log of approved lessons that touch any
    decision class, and can veto.
- **Personal data.** Detected by a column and pattern scanner (national ID,
  Sheba/IBAN, phone, email, salary).
  - Scripts compute aggregates and joins. The model sees pseudonymised or
    aggregated values unless the task needs individual rows; then the owner
    confirms once per dataset, with a purpose.
  - **[v2]** Each task keeps a data map of which files and tools received
    personal data.
  - **[v2]** `/dynamic-harness:forget <subject>` purges task artefacts and redacts
    the subject in decision records.
  - In v1, personal data is tagged and the lethal-trifecta check applies.
  - Setup writes `cleanupPeriodDays` for transcript retention.
  - The spec for each deployment path states the model provider's data
    location and retention.
- **Cost.**
  - Caps per task and per month in money.
  - T1/T2 tasks open with an estimated range: "About 20 minutes and roughly
    X Toman."
  - Actual cost appears in the report and the decision record.
  - A monthly statement lists cost per task.
- **Vendors.** The connector registry holds `{service, data_location,
  scopes, approved_by}`.
  - An unregistered connector counts as write and needs approval.
  - An outage follows §Budget and failure handling (v1). In v2 it moves the task to `PAUSED_VENDOR`.
  - Switching to a fallback vendor needs explicit owner approval, because it
    is a new data transfer.

### User-facing templates and localisation

The lint `harness-lint-user-text` checks harness-authored template text
only (`templates/*.json`), not quoted owner content, for internal terms: card, gate, tier, oracle, outbox, BLOCKED, unit,
ledger, hook, V0–V3, T0–T2. Templates are written natively in Persian and
English, never machine-translated. The English versions:

| Moment | Template |
| --- | --- |
| Opening | "I'll make the March sales summary from the file you gave me. I'm assuming amounts are in Toman and 'March' means the month of Esfand (all of it). Tell me if either is wrong; I'm starting now. Using your usual: short report." |
| Question | "Who is this report for? (1) You only, short. (2) Your partners, detailed [suggested]. (3) The bank, formal. (4) Not sure, use the suggestion." Each option says what it changes |
| Preview | "Here are the first 6 of about 40 invoices. Do they look right? If you don't reply I'll carry on, and I'll note in the report that you didn't confirm this sample." |
| Approval | "Ready to send. 12 emails to existing customers (your usual template). **2 go to people you haven't emailed before.** 1 payment: **25,000,000 Toman (twenty-five million Toman; 250,000,000 Rial) to Rahimi Co.** If wrong: payment must be reclaimed from Rahimi Co. This stays open until Saturday 18:00; after that nothing is sent. [See all] [Send] [Don't send]" |
| State: Checked | "Done. I re-checked the totals against your bank file." |
| State: Done, not fully checked | "Done, but I couldn't check the VAT column. Please look at it before using this for tax." |
| State: Partly done | "I did 9 of 14. Left: 5 invoices, because the customer address is missing. Nothing was sent." Every internal stop (budget, stall, cost, approval wait) reaches the user in this state |
| Stopped at budget or cost | "I did 23 of 37 and stopped at your limit of X. Nothing else was sent or changed. Finishing needs about Y more. Continue?" |
| Disconnect | "Connection lost. Your work is saved. Nothing was sent. Continue when you're ready." |
| Research plan (`plan`) | "Before I start: I'll check these 7 possible reasons [list], using these kinds of sources [list], for about X. I won't search for your customers' or suppliers' names. OK, or change something?" |
| Assumption sheet | "These are the numbers I'll assume. The top 3 matter most. Change any of them, or say OK." (an editable table) |
| Definition confirmation | "I'll count revenue as sales invoices minus returns, without VAT, in Toman. Is that right?" |
| Decision card (`decision_ack`) | "My suggestion: hire one person now, not two (range: 1–2). This changes if monthly sales fall below X. Three assumptions you haven't confirmed: [list]. This advice goes stale after <date>." |
| Sources disagree | "Your sales file says 1,240 million Toman; bank deposits say 1,180 million. The 60 million gap is not explained. I've shown both." |
| Incomplete data | "Incomplete: your file ends on <date>, before the end of the period." |
| Restated figure | "Restated: last month's figure changes from X to Y, because the revenue definition changed on <date>." |
| Prepared, not sent (v1) | "I've prepared this. Nothing was sent or changed." |
| Refused | "I can't help with this kind of decision. Please ask a qualified professional." |
| Late approval | "You asked for this on Wednesday. Still wanted? Rates used: Wednesday. [Refresh and show again] [Send as is] [Cancel]" |

**Rules:**
- **Three user-visible states** only, shown once per deliverable in the same
  place, as words plus an icon (never colour alone). They name the specific
  unchecked item. They always say "nothing was sent or changed" when that is
  true.
- **Answers.**
  - Partial answers: unanswered questions take the suggested option, and the
    report lists them as assumed.
  - Silence is never consent and never "preview OK". In v1, silence is
    seen only on the next user turn (§Turns and contacts).
  - A late answer is accepted, and its rework cost is shown.
- **Rubber-stamp defence [v2, with write connectors].**
  - Cards lead with what is unusual. There is no "approve all" across action
    types. Money is echoed in digits and in words.
  - Owner-defined **standing rules** for routine actions keep cards rare ("emails
    to existing customers with template T, up to 20 a day, auto-send").
  - Median approval time and approval rate are measured. A month of
    approvals under 3 s each, with a 100% rate, triggers a suggestion to
    tighten.
- **Reminders [v2].** One reminder per pending approval through the owner's
  registered channel (OQ-7). No reminders in the business
  profile's off-hours or weekend (for example Friday, and Thursday
  afternoon).
- **Correction after delivery.** Each assumption in the report is a tappable
  line with alternatives: "Assumed: Toman. [Change to Rial]". It re-runs only
  the dependents (assumption IDs → dependent outputs) and says so: "Updates 3
  tables, about 1 min."
- **Remembered preferences.**
  - A "What I remember" view with date and use count; edit, delete, or
    forget everything.
  - The first capture asks "just this time, or always?".
  - Facts about money, rates and people are not memory. They live in the
    business layer or with the model, and expire after 90 days. Style does
    not expire.
  - Conflicts between people's preferences are shown, never resolved
    silently.
- **Localisation and normalisation** (deterministic, in code, before any
  matching or arithmetic):
  - Persian, Arabic and Latin digits; ی/ي and ک/ك; ZWNJ.
  - Jalali dates, including Esfand in leap years. The fiscal year starts
    Farvardin 1 unless the profile says otherwise.
  - Mixed RTL/LTR text in cards (tested).
  - Rows that fail to parse are counted and shown as "left out: N rows".
  - Currency: the unit is set once at setup, and every amount shows its unit,
    with the other unit on cards ("50,000 Toman (500,000 Rial)"). A
    **magnitude check** compares each figure with its historical range and
    flags a ×10 jump.
- **Phone and poor connectivity.** Cards are short and work on a narrow
  screen. Approval taps are idempotent, so a second tap does nothing.
  Questions can be answered with one tap.

### Onboarding

1. **`/dynamic-harness:setup`:** permissions, sandbox and transcript retention.
2. **`/dynamic-harness:business`:** currency and unit, calendar and fiscal year, work
   week, jurisdiction, roles and limits, money caps, do-not-contact list,
   required footers.
3. **Connect read-only sources** (v1: CSV/Excel, Google Sheets, one
   accounting export, one CRM, one email account as drafts only). Confirm the
   source of truth for the five most-used metrics, which seeds the
   dictionary.
4. **Three example deliverables** the owner liked, kept as style exemplars.
   They are more useful early than learning.
5. **One supervised task** before any standing rule or automation.

### Release scope

| Release | Includes |
| --- | --- |
| **v1** (read-only connectors; email drafts only) | 1. Probe and the T0 fast path. 2. A single card draft; the second draft runs in shadow mode only. 3. The tier selector. 4. Safety hooks: irreversible classes are denied; egress allowlist, per-job research grants and query guard; protected paths and the trust boundary (hook-only writers, HMAC receipt); lanes; SessionStart and ConfigChange. 5. The gate hook runs `fold_ledger.py` with profile flags through the gate adapter. The Stop hook verifies the HMAC receipt. 6. The contact cap. 7. Business layer: profile, single owner, metric dictionary, source registry, policies, money caps. 8. `harness-normalize` and the answer header. 9. Claim checks: the new `check_claims`, with `check_citations` unchanged. 10. The `model-checks` pack. 11. Template lint and the three user-facing states. 12. The decision record (card, REPORT hash and structured sections, receipt, cost), with CSV export. 13. The lease (a lockfile with a heartbeat). **Task types 1–7:** questions, documents, reports, investigations, simulations, decision support |
| **v2** (with write connectors) | Two-lane outbox, journal, idempotency and reconcile; one-off and bulk operations (types 8–9); automations and monitoring (types 10–11); approval matrix with out-of-band approval; deep path; the WAITING_EXTERNAL and PAUSED_VENDOR states; reminders; rubber-stamp metrics; data map, `forget` and PDF export; learning editor; planning packs (type 12) |
| **Never final by the harness** | Outputs in a `signoff` class, including type 13. The harness never marks them final. They stay "prepared, not final" until a named professional signs off |
| **v1 routing for types 8–13** | Types 8–12 compile with `deliverable.kind = prepared_actions`, and their tier comes from the selector (type 12 → T2 in v1; no planning pack). The harness produces the document or list only, and the report says "I've prepared this. Nothing was sent or changed." Type 13 → T2 + signoff |

--------------------------------------------------------------------------------

## Alternatives Considered

| Alternative | Pros | Cons | Reason for rejection |
| --- | --- | --- | --- |
| A. One fixed harness (always T1) | Simple | Simple questions pay its cost; it under-protects risky work | Fails GL-1 |
| B. Start every task at T0 and escalate only through tripwires | No understanding cost | Irreversible or decision-grade risk is detected late | Kept as the T0 safety net, not the main path |
| C. The model picks the tier; code validates | Flexible | Injection and self-serving bias; not testable deterministically | The tier is code; the model only fills card fields |
| D. Native plan mode plus permissions only | No new code | No gate, no frozen scope, no contact budget, no provenance | It is the baseline we compare against |
| E. loop-contract triage as is | Already exists | Prose only; no T1; no enforcement | It is extended, not replaced |
| F. Harness²-style online evolution | Can improve per task | Not better than test-time scaling at equal budget on Terminal-Bench (2607.12227); needs a large budget | Future work |
| G. LLM judge as the oracle | Covers non-code work | JudgeBench: many strong models are only slightly better than random; self-preference | The critic is V1 only, with binary criteria and a label |
| H. All irreversible actions at the end (paper v1) | Least interruption | Breaks dependent work; certifies effects that have not happened | Replaced by the two lanes |
| I. Two tiers instead of three | Simpler | Single-session verified work would pay for the full contract | Three tiers kept; T1 is the common business case |
| J. Chat with Projects (the owner's default) | No setup; writes one-off documents without setup | No reconciliation, no approval binding, no persistent business rules, no receipts | Compete on relied-on numbers, sends and rules, not on writing (stage 8) |
| K. Zapier/Make for recurring work | Deterministic, cheap per run | Building flows needs skill; no judgement steps | Automations compile to deterministic scripts where possible; this harness builds and verifies them |
| L. Tier by side effects only (paper v1) | Simple | A read-only answer the owner relies on (cash, tax) gets no check | Replaced by reliance-aware selection (R-11) |

--------------------------------------------------------------------------------

## Non-Functional Requirements

- **Performance.**
  - T0 overhead: ≤ 1.5k tokens and ≤ 2 s at p95.
  - Each hook: ≤ 100 ms p95.
  - The gate runs inside the dedicated PostToolUse gate hook. That hook is
    exempt from the 100 ms budget and has a fixed 1800 s timeout; the profile
    `timeout` must be ≤ 1790 s (verified in OQ-4).
- **Reliability.** Every state is on disk; resume is defined for every state.
  Hooks fail closed for safety and fail open only for non-safety telemetry.
- **Privacy.** See §Learning and §Security model. v1 has one business per
  installation.
- **Localisation.** All user-facing templates exist in Persian (RTL, Persian
  digits optional, Jalali dates, Toman/Rial) and English.
- **Rollout and rollback.**
  1. **Shadow.** Each component logs what it *would* do and blocks nothing.
  2. **Opt-in enforce,** per component, flipped by the operator once its
     shadow criterion is met (§Evaluation plan).
  3. **Default.**

  Kill switch: `/dynamic-harness:off` sets the `enforcement.yaml` keys to shadow.
  The safety hooks, lanes, protected paths, egress, query guard, trusted
  writers and the receipt check always stay enforced; shadow mode applies
  only to `enforcement.yaml` keys. The core and the packs are semver-versioned. Ledger schema
  migrations ship as versioned scripts and are applied on resume.

--------------------------------------------------------------------------------

## Evaluation plan

Each stage has a written go / no-go criterion. A stage starts only when the
previous one passes.

| # | Stage | Metric | Threshold | n | Mode |
| --- | --- | --- | --- | --- | --- |
| 1 | Invariant suite | R-1…R-10 (process invariants) plus a bypass matrix (Bash, symlink, `mv`, MCP, `sh -c`, T0 downgrade, scope rename/merge/split/blocked; [v2] outbox edit after preview) | 100% pass; guard recall ≥ 95% on the corpus | corpus ≥ 200 cases | Offline, no model, plus `claude -p` end-to-end for R-2 |
| 2 | T0 differential replay | Identical tool sequence; overhead | ≥ 95%; ≤ 1.5k tokens and ≤ 2 s at p95 | ≥ 100 T0 tasks | Offline replay |
| 3 | Card and tier accuracy | Tier confusion matrix; under-classification of irreversible, multi-unit and decision-grade tasks | Upper 95% bound ≤ 3% for under-classification; over-classification ≤ 20%; per-field Krippendorff α ≥ 0.67 between 2 labellers | ≈ 400 real requests, stratified by task type | Shadow |
| 3b | Two-draft value | AUROC of draft disagreement for predicting card error | ≥ 0.70 to keep the second draft | same set | Shadow |
| 3c | Card ablation | Task outcome with a correct card vs a wrong card vs no card, on the same tasks (measures the cost of under-classification) | Wrong-card regret ≤ ε (set before the run) | ≥ 60 tasks × 3 arms | Offline replay |
| 4 | Verifier sensitivity | Detection recall per error class; false alarms on clean outputs | Recall ≥ 80%, false alarms ≤ 10%. Business traps: the output must *report* the trap, recall ≥ 95%; 100% for the Toman/Rial ×10 and period classes (GL-7) | ≥ 30 plants per class, written by someone other than the verifier's author. **Classes:** calculation; formula; wrong file; Toman/Rial ×10; Jalali fiscal boundary and leap Esfand; ی/ي name variant; Persian-digit dates; missing dates; disagreeing sources; stale export; transfer between own accounts counted as revenue; fabricated citation; real citation that does not support the claim; stale source; prompt-injected page; syndicated copy counted as independent; contradicting sources; overfit forecast; extrapolation beyond the history | Offline |
| 4b | Business requirements | R-11…R-15 (v1): selector vs taxonomy table, answer header, claim trace, cell trace, decision record. R-16 and R-17 are added with v2 (`test_approvals.py`, `test_automations.py`) | 100% pass | `test_tier.py`, `test_business_traps.py`, `test_claims.py`, `test_model_checks.py`, `test_records.py` | Offline |
| 5 | Shadow on real work | Would-block and would-ask events logged; would-block precision and recall vs operator labels; false-block rate; gate verdict vs later rework | False blocks ≤ 2%; ≥ 4 weeks; ≥ 50 tasks per tier | — | Shadow |
| 6 | Equal-budget comparison | Win = passes an executable oracle, or a blind human preference; budget = total tokens including harness overhead | Harness ≥ raw on wins, with a paired sign test at p < 0.05 | ≈ 90 pairs | Live |
| 7 | Communication pilot | Blocking contacts per task; corrections per task (by type: fact, figure, tone, scope); time to useful result; acceptance without rework; median approval time and approval rate (rubber-stamp signal); behaviour on late, absent and partial replies | GL-4, and acceptance ≥ the raw-agent arm | ≥ 10 businesses (about 8 also keep a 2-week diary), within-subject, phone and desktop | Live |
| 8 | Business value vs the owner's real alternatives | Acceptance without rework; owner net minutes (produce + review + correct, from the diary); cost per task in money; post-delivery figure errors within 30 days; escalation rate; trust (7-day return rate, NPS, share granting a standing rule); forecast error vs actuals (re-forecasts) | Harness wins on its claimed conditions: figures reconcile to source, sends wait for approval, rules persist across tasks, every claim has a receipt. Not expected to win on one-off writing | Baselines: chat with Projects; Zapier/Make for recurring work; manual work; the accountant's turnaround | Live |

**Stage cost and timing.** These are estimates and must be replaced with the
project plan.

| Stages | Effort | Model cost | Elapsed |
| --- | --- | --- | --- |
| 1, 2, 4, 4b | about 2 engineer-weeks | about 0, apart from the e2e `claude -p` runs | weeks 1–3 |
| 3, 3b, 3c | about 30 h of labelling (2 labellers × 400 requests) plus replay | low | weeks 3–5 |
| 5 | operator review about 2 h per week | normal usage | ≥ 4 weeks |
| 6–8 | pilot coordination; diaries | normal usage plus the baseline arms | 6–8 weeks |

**Simulation (paper Appendix A).**
- It is illustrative of structure only, until the code and priors are
  published.
- Required before any number is cited:
  - a pre-registered utility U = value − λ·interruptions − Σ P(wrong
    assumption)·cost;
  - a frozen policy evaluated on held-out priors written by someone else;
  - results per task type with confidence intervals, plus a λ sweep;
  - a falsification rule: the claim fails if the policy is not within δ of
    the best on ≥ 70% of held-out configurations;
  - a user model that includes late replies, absent replies, partial
    replies, changes of mind, blind approval, and phone versus desktop use;
  - task types for investigation, forecasting, recurring work and decision
    support.
- Shadow mode cannot measure interruptions; stage 7 does.

## Open Questions

| ID | Question | Decision criterion / owner |
| --- | --- | --- |
| OQ-1 | Deployment surface: Desktop/Cowork/CLI (assumed v1) vs an Agent SDK host | Owner; decide **before stage 1**, using the OQ-4 support matrix (hooks, sandbox, managed settings, `AskUserQuestion` on each surface) |
| OQ-2 | Simulation code and priors: publish, or keep the result "illustrative" | Author; before the paper is submitted |
| OQ-3 | Contact cap 5 and start questions 3: keep, or tune from the pilot | Stage 7 data |
| OQ-4 | Platform behaviours: **resolved by the spike** (§Hooks, platform facts). Not yet tested: expiry at exactly 1800 s, and whether a ConfigChange hook can block a change | Re-run `spike/run_spike.sh` on each Claude Code upgrade |
| OQ-5 | Second card draft: keep or drop | Stage 3b AUROC ≥ 0.70 |
| OQ-7 | Out-of-session channel for reminders and high-value approvals (chat app, SMS, signed link) | Depends on OQ-1 |
| OQ-8 | Default retention for decision records (7 years assumed) under local bookkeeping law | Operator, with the owner's accountant |

## Glossary

| Term | Meaning |
| --- | --- |
| Harness | Everything around the model for one task: card, tier, hooks, gate, outbox, contacts |
| Task card | Structured description of the task (§Data contracts) |
| Tier (T0–T2) | How much quality machinery runs. Chosen by code |
| V-level (V0–V3) | Who verifies and when |
| Oracle | A way to know a result is right that does not rely on the producer's claim. Written before the work; judged by someone other than the producer |
| Rung | Oracle strength in loop-contract's 8-step ladder |
| Gate | The gate hook running `fold_ledger.py` in done or known mode through the gate adapter |
| Unit | One independently verifiable item in the frozen list, for example one customer's email, one report section or one research hypothesis. A scenario is a child job |
| Outbox | Store of held irreversible actions (`outbox.jsonl`). Lane A items end a phase and are approved at the phase boundary. Lane B items are independent and are approved together on one card at the end [v2] |
| Reliance | What the user will use the result for: info, operations, money or external |
| Reversibility R0–R3 | R0: fully reversible. R1: reversible, but already seen externally. R2: correctable only by compensation. R3: cannot be undone |
| Receipt | The HMAC written by the gate hook (§Trust boundaries and protected writes) |
| Lethal trifecta | Private data, untrusted input and egress in one task |
| User-facing state | One of the three R-10 states |
| Choice classes | Fact, technical, owner value, irreversible: how an open choice in a task is resolved |
| Phase approval | Lane A approval before an irreversible action that later units depend on |
| Deep path | Bounded best-of-N for a unit that has failed twice |
| Shadow mode | A component logs what it would do without blocking |
| Operator | The person who installs and configures the harness for a business (formerly "maintainer") |

--------------------------------------------------------------------------------

## Detailed Implementation Plan

Items marked **[v2]** are not created in v1, and no v1 code path imports
them.

Paths are relative to two repos:
- `harness/`: this new plugin.
- `loop-contract-skill/`: https://github.com/hdkhosravian/loop-contract-skill (a sibling checkout).

### Build phases

Each phase ends buildable, with its tests green.

| Phase | Builds | Tests green at end |
| --- | --- | --- |
| P1 | `harness_core/{canon,events,ledger,fold}.py`, loop-contract 1.1.0 changes | R-2, R-3, R-6; chain-break and receipt fixtures |
| P2 | `safety.py`, classes, egress, query guard, `protected_paths`, `harness-sandbox-exec`, `session.py`, `config_guard.py`, `turns.py`, `writers.py` (nonce, cited turns), bypass corpus | R-1, R-4, R-6; corpus recall ≥ 95% |
| P3 | probe, card, compile, `tier_select`, contacts, memory, tripwire, lanes, `subagent.py`, `pii.py`, `corrections.py`, `shadow.py` | R-5, R-7, R-8, R-9; taxonomy-vs-selector test |
| P4 | Stop, gate hook, lease, budget, stall, decision record | R-2 end-to-end (`claude -p`), R-10, R-15 |
| P5 | business tools, packs, templates, commands, setup | R-11 to R-14; business trap suite |

`control/enforcement.yaml` holds `{component: shadow|enforce}` for `contacts`, `tripwire` and `shadow_draft`. All of them default to `shadow`. Stage 1 runs every key in `enforce`. `budget`, including the money cap, is fixed `enforce`. `safety`, `budget`, `lanes`, `protected_paths`, `egress`, `query_guard`, the trusted writers and Stop's receipt check are fixed `enforce` and are not keys in this file. Every hook reads this file, and in shadow
mode it logs what it would have done without blocking anything.

### Target Files to Create/Modify

#### 1. `harness/.claude-plugin/plugin.json` (Create)
*   **Type / Scope:** Plugin manifest.
*   **Proposed Changes:** Plugin name `harness`. Requires loop-contract
    ≥ 1.1.0, installed side by side; SessionStart checks the version. Lists
    the hooks, skills and commands.

#### 2. `harness/hooks/hooks.json` (Create)
*   **Type / Scope:** Hook registration.
*   **Proposed Changes:**
    *   Every event in §Hooks calls `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/harness-hook" <event>`.
    *   Every entry sets `onFailure: "block"` and an explicit `timeout`. Without `onFailure`, a crashed or timed-out hook lets the tool run (OQ-4 spike). This needs Claude Code ≥ 2.1.295, which SessionStart checks. `tests/test_hooks_config.py` asserts it.
    *   Timeouts: 5 s by default; 10 s for Stop. A separate PostToolUse matcher for `Bash(harness-gate request*)` has a fixed 1800 s timeout, and one for `Bash(harness-claims fetch*)` has 60 s, since `hooks.json` is static. Profile `timeout` must be ≤ 1790 s.

#### 3. `harness/lib/harness_core/` (Create, Python ≥ 3.9: the system `python3` runs hooks)
*   **Type / Scope:** Hook and CLI library.
*   **Proposed Changes:** One module per concern. Each handler takes the hook
    event dict and returns `{decision, reason}`, or `{}`.

| Hook row / feature | Module : function |
| --- | --- |
| UserPromptSubmit | `prompt.py: on_prompt(evt)`. Calls `probe.run(request, cwd) -> Probe` and `prefilter(probe) -> "T0-fast"\|"card-needed"` |
| PreToolUse safety | `safety.py: on_pre(evt)`. Uses `classes.classify(tool, input) -> Class`, `egress.check(url, query, job)` and `query_guard.check(query, protected_terms)` |
| PreToolUse lanes | `lanes.py: on_pre(evt)` |
| PreToolUse contacts | `contacts.py: on_pre(evt)`, `contacts.count(evt)` |
| PostToolUse writers | [P3/P4] a dispatcher `writers.py: on_post(evt)` routes to the trusted writers. In P1–P2, `Ledger`, `Outputs` and `Turns` are called directly, and nonce and turn citation live in `turns.py` |
| PostToolUse budget / stall | `budget.py: charge(evt)`, `budget.check() -> StopKind\|None`, `stall.check(ledger) -> bool`. Prices come from `control/prices.yaml` (with a "rate as of" date) |
| PostToolUse tripwire | `tripwire.py: on_post(evt)` |
| SessionStart | `session.py: on_start(evt)`. Covers the config log, the canary, the skills hash check against `control/approved_procedures.yaml` (R-5 in v1: a mismatch gives STOPPED_TAMPER), the loop-contract version, and jobs index listing |
| ConfigChange | `config_guard.py: on_change(evt)` |
| Stop | `stopgate.py: on_stop(evt)`. Calls `record.write(job)` at terminal states |
| SubagentStop | `subagent.py: on_subagent_stop(evt)` |
| Tier selection | `tier_select.py: select_tier(card, probe, control) -> Tier`. Called only by `compile.apply` |
| Lease | `lease.py: acquire(job, sid)`, `heartbeat()`, `takeover(job) -> epoch`. Each takeover raises the epoch, and appends that carry a stale epoch are rejected |
| Personal data | `pii.py: scan(path_or_text) -> [Tag]` |
| Corrections (v1 learning) | `corrections.py: log(evt)` |
| Second draft (shadow) | `shadow.py: log_second_draft(card1, card2)` |
| Gate adapter | `fold.py: project(events, ledger, verdicts) -> gate_rows`; `gate.run(job) -> receipt\|error` |
| Canonical hashing | `canon.py: sha256_json(obj)`, `scope_bytes(rows)`, `tree_sha(job)`, `hmac_receipt(...)` |
| Trusted writers | [P3/P4] the `writers.py` dispatcher routes to: `ledger.append(req)`, `compile.apply(req)`, `business.apply(req, nonce)`, `memory.apply(evt)`, `claims.apply_fetch(url)` (the hook fetches), `run.apply(check_id, inputs)`, `model.apply(req)`, `contact.apply(req)`, `answer.apply(req)`, `budget.apply(evt)`, `admin.apply(cmd, nonce)`, `gate.run(job)`. Claims rows come from `subagent.on_subagent_stop` |
| Turns | `turns.py: append(evt)`, `cite(turn_id, contact_id) -> bool` |
| Decision record | `record.write(job, interim: bool)` |
| CLI back-ends | `answer.py`, `model.py` (assume, check, scenario, import, trace, backtest, register), `doc.py`, `decide.py`, `normalize.py`, `estimate.py`, `critic.py` (render, verify), `contact.py`, `research.py` (render), `sandbox_exec.py`, `card.py` (validate, merge), `claims.py` (fetch, confidence, cite), `admin.py` (setup, config, off, memory, status, export), `export.py` (CSV), `lint_text.py` |

#### 4. `harness/bin/` (Create)
*   **Type / Scope:** Thin CLIs on PATH. Every script only prints a JSON request
    on stdout, and may write scratch files under `requests/`. None writes
    `events.jsonl` or any protected path.
*   **Proposed Changes:**
    *   `harness-hook`: hook entrypoint.
    *   `harness-probe`, `harness-card validate|merge`, `harness-compile`.
    *   `harness-ledger append`, `harness-gate request`.
    *   `harness-contact <type> [--handoff|--accept <turn_id>]`: requests a contact; `contact.apply` renders it and records it.
    *   `harness-business set|show|register-source`.
    *   `harness-normalize`.
    *   `harness-answer compute|reconcile|bridge|header`: requests the header; `answer.apply` re-runs the compute and renders `answer_header.md`, which the gate includes in `REPORT.md`.
    *   `harness-claims fetch|confidence|cite`. Claim rows are added only by the SubagentStop writer.
    *   `harness-model assume|check|scenario|trace|backtest|register`.
    *   `harness-doc check`: required items, placeholders, figure trace,
        policy footer.
    *   `harness-decide score|flip`.
    *   `harness-critic render <unit>`.
    *   `harness-sandbox-exec <file>`: runs a file the agent wrote, with no
        network and no Apple Events.
    *   `harness-estimate`: tier median from past records, ±30%.
    *   `harness-run <check_id>`: runs a registered pack entrypoint, which the hook records.
    *   `harness-research render <plan_sha>`, `harness-critic verify`, `harness-model import`.
    *   `harness-admin <cmd>`: the back-end for `setup`, `config`, `off`, `memory edit` and `status --repair`. Its writer checks the typed-command nonce.
    *   `harness-lint-user-text`.
    *   **[v2]** `harness-outbox`, `harness-approve`.

#### 5. `harness/control/defaults/` (Create)
*   **Type / Scope:** Default control layer. `/dynamic-harness:setup` copies it to
    `${CLAUDE_PLUGIN_DATA}/control/`.
*   **Proposed Changes:**
    *   `classes.yaml`, including `v1_action`. In v1 every irreversible
        class is `deny`; `draft_create` is allowed.
    *   `egress_allowlist.yaml`, `protected_paths.yaml`, `budgets.yaml`,
        `prices.yaml`, `gate.yaml` (profiles, freshness table, timeout).
    *   `enforcement.yaml`, `approved_procedures.yaml`.
    *   **[v2]** `approval_limits.yaml`.

#### 6. `harness/schema/` (Create)
*   **Type / Scope:** JSON Schemas, one per file named `harness/<name>@1`.
    `harness-card validate` and every writer validate against them.
*   **Proposed Changes:**
    *   `run/`: card, probe (`floor_tier`, `question_only`, `write_targets`,
        `data_classes`, `money_tokens`, `action_verbs`), tier, budget, lease,
        state, session (`prompt_scope`, `pending_card`, `active_task`),
        contacts, receipt, stop_record, decision, correction, event,
        ledger_row, verdict_row, scope_row, pins, memory_record, assumptions, claim_row, fetch_log, contradiction, search_log, egress_grant, turns, request, jobs_index, gate_profile, research_plan, outputs_row, critic_verdict, snapshot_meta, state.
    *   `plugin/`: config_log.
    *   `control/`: one schema per control YAML.
    *   `business/`: profile (including thresholds), roles, policies,
        metric_dictionary, source_registry, connector_registry,
        protected_terms, consents, exemplars, model_registry,
        bank_export.
    *   **[v2]** automation_spec, approval_matrix.
    *   The card enums are `oracle.type` (registered check IDs) and
        `deliverable.kind` ∈ {answer, document, report, research_report,
        model, decision_memo, prepared_actions}.

#### 7. `harness/skills/` (Create)
*   **Type / Scope:** Executor guidance, plus domain packs.
*   **Proposed Changes:**
    *   `harness/SKILL.md` covers card drafting rules, assumption recording,
        the report format and state rendering.
    *   Domain packs, each with a `manifest.json` that registers its check
        entrypoints and their hashes:
        *   `finance/`, `sales-crm/`, `marketing/`, `operations/` (v1 limits
            these to read-only, prepared work);
        *   `research/` (registers `research.check`, which checks that cited claim rows are load-bearing, pass `check_claims`, and have a recorded critic verdict);
        *   `model-checks/`;
        *   the `operations/` manifest also registers `doc.check` → `harness-doc check` and `decide.score_flip` → `harness-decide score|flip`, so documents and decision support can reach T1.
        *   **[v2]** `automation/`.

#### 7b. `harness/agents/*.md` (Create)
*   **Type / Scope:** Plugin subagents, with `tools` restricted. Hooks enforce the rest, because plugin agents ignore `hooks`/`permissionMode` frontmatter.
*   **Proposed Changes:**
    *   `web-researcher`: WebSearch, WebFetch, `Bash(harness-claims fetch *)`. Returns claim rows only.
    *   `data-analyst`: Read, Grep, Glob, `Bash(harness-run *)`. No web.
    *   `verifier`: Read only. Its prompt comes from `harness-critic render`.
    *   `card-drafter-2` (shadow): Read only.

#### 8. `harness/templates/{en,fa}.json` (Create)
*   **Type / Scope:** User-facing strings, in the form `{moment_id: string}`.
*   **Proposed Changes:** Every template in §User-facing templates and
    localisation. The Persian file is written natively under the
    persian-writing skill. The lint runs on these files only.

#### 9. `harness/commands/*.md` (Create)
*   **Type / Scope:** Slash commands. `setup`, `business`, `config`, `memory`,
    `status`, `export`, `off` and **[v2]** `approve`, `forget` set
    `disable-model-invocation: true`. Each one runs through `harness-admin` or
    `harness-business`, whose writers check the typed-command nonce.

| Command | Arguments | Writes | Confirmation |
| --- | --- | --- | --- |
| `/dynamic-harness:setup` | — / `confirm <code>` / `undo` / `status` | As built (§Build decisions (P5 setup)): `~/.claude/settings.json` gets only `sandbox.network.strictAllowlist: true`. The workspace's `.claude/settings.local.json` gets `sandbox.enabled`, `failIfUnavailable`, `allowUnsandboxedCommands: false`, `excludedCommands: []`, `network.allowedDomains: []`, `network.allowUnixSockets: []`, `filesystem.denyRead` and `denyWrite`, the `permissions.deny` Read/Edit rules and `disableBypassPermissionsMode`. Creates `keys/hmac.key`; records `loop_contract_root` and the release hashes in `config_log.jsonl` | The hook shows the plan grouped by file in plain words, with a one-time code; nothing changes until `confirm <code>` |
| `/dynamic-harness:business` | `set <object> <field>` / `show` | Through `harness-business set` | A rendered sentence |
| `/dynamic-harness:config` | `<file> <key> <value>` | Control layer and config log | Operator only |
| `/dynamic-harness:memory` | `show` / `edit` / `forget` | Through the memory writer | Per item |
| `/dynamic-harness:export` | `--task` / `--period` | CSV export of records (`harness-admin export` → `export.py`) | Typed by the user |
| `/dynamic-harness:status` | `[--repair]` | Repair is operator only | — |
| `/dynamic-harness:off` | — | `enforcement.yaml` → shadow; safety hooks stay on | Operator only |
| **[v2]** `/dynamic-harness:approve`, `/dynamic-harness:forget` | — | — | — |

#### 10. `loop-contract-skill/skills/loop-contract/scripts/fold_ledger.py` (Modify; release loop-contract 1.1.0)
*   **Type / Scope:** The gate. Stdlib only, as today.
*   **Proposed Changes:**
    *   `--verify-chain <events.jsonl> <head_file>`: a standalone mode that skips `--mode` and the empty-ledger exit. It checks the hash chain (`prev_sha`, each event's `sha`), the `seq` order (it fails on a duplicate or out-of-order `seq`) and the head file. Row and file hashes (`row_sha`, `file_sha`) are verified by the harness `EventLog.check_file`, not by fold_ledger. Verdict supersession is unchanged.
    *   `--receipt <path>`: on exit 0 only, writes `{mode, events_head, scope_sha, tree_sha, report_sha, gate_sha, verdict, units, caps, traced_values, ts}`. The harness hook adds the HMAC.
    *   `--sources <claims.jsonl> --fetch-log <file> --snapshots <dir>` and `check_claims()` (§Gate adapter). The freshness table is read from `gate/profile.json` (JSON, not YAML). `check_citations` is unchanged.
    *   `--oracle-stdin <file>` (passed as stdin to the oracle and to `--scope-count-cmd`) and `--oracle-timeout <s>` (default 1800, the current hard-coded value).
    *   `--numbers-trace <gate/REPORT.md> --outputs <outputs.jsonl> --assumptions <assumptions.yaml>`, using a vendored stdlib normaliser `lc_normalize.py` with fixtures shared with `harness-normalize`.
    *   `--require-logs <contradictions.jsonl> <search_log.jsonl>` and `--include <answer_header.md>`.
    *   `--lint-lexicon <state_lexicon.json>`: rejects state words outside the template-rendered lines, and reports `caps: [lexicon]`.
    *   `--numbers-trace` takes several files (`outputs/submitted/report.md` and each submitted text deliverable).
    *   **Version bump to 1.1.0:** `.claude-plugin/plugin.json`, `marketplace.json`, and the gate section of `SKILL.md`.
*   **Tests:** a new `tests/` folder in loop-contract-skill with fixture ledgers for: chain break, duplicate seq, receipt fields, quote mismatch, syndicated source, stale source, untraced number, missing log, and done vs known mode.
*   **`--remaining` usage** (run by the UserPromptSubmit hook on takeover): `fold_ledger.py --mode <mode> --ledger gate/ledger.jsonl --verdicts gate/verdicts.jsonl --scope gate/scope.jsonl --remaining`.

#### 10b. Gate profile example (`control/defaults/gate.yaml`, excerpt)

```yaml
profiles:
  T1.default: {mode: auto, allow_blocked: units, numbers_trace: true, lint_lexicon: true, timeout: 1790}
  T2.default: {mode: auto, allow_blocked: units, numbers_trace: true, lint_lexicon: true, timeout: 1790}
  T1.data_question:
    mode: auto            # done if every final state is PASS/BLOCKED, else known
    allow_blocked: units
    verdict_vocab: ANSWERED,ANSWERED-WITH-GAPS,UNANSWERED
    # --oracle-cmd comes from card.oracle.type (checked against the pack manifest)
    proof_cmd: "finance.reconcile_ar --unit {id} --attempt {n}"
    group_by: unit_type
    skip_citation_check: false
    numbers_trace: true
    timeout: 1790
  T2.investigation:
    mode: auto
    allow_blocked: "ceil(0.2*units)"
    sources: claims.jsonl
    require_logs: true
    freshness: {price: 30d, law: in_force_date, default: 365d}
    timeout: 1790
```

#### 11. `harness/tests/` (Create)
*   **Type / Scope:** pytest suite.
*   **Proposed Changes:**
    *   `test_invariants.py`: one named test per requirement, from
        `test_r1_t0_no_block` to `test_r10_states`. It also holds
        `test_r4_v1_action`, which checks that every class resolves to its
        `v1_action`.
    *   `tests/corpus/bypass.jsonl`: rows of the form `{id, tool, input,
        expected: allow|deny|ask, class}`, at least 200. They cover Bash,
        symlink, `mv`, MCP, `sh -c`, `osascript`, `open`, a script the agent
        wrote, `dangerouslyDisableSandbox`, a T0 downgrade, protected-path
        writes, forged receipts and heads, and agent self-invocation of
        commands.
    *   `tests/fixtures/cards/<task_type>_<variant>.json`, each with an
        `expected_tier` and `v1_expected_tier`. `test_tier.py` runs every fixture.
    *   `test_state_machine.py` covers every transition, crash and resume,
        lease takeover with epoch fencing, inline T0 with an open task, and
        the cap deadlock leading to PAUSED.
    *   One test file per module: `test_budget.py` (including STOPPED_COST and continue), `test_stall.py`, `test_tripwire.py`, `test_config_guard.py`, `test_subagent.py`, `test_session.py` (canary, config log, tree hashes), `test_pii.py`, `test_corrections.py`, `test_shadow.py`, `test_egress_grant.py`, `test_turns.py`, `test_writers.py` (nonce, cited turns, recorded outputs), `test_fold.py` (mapping table, mode choice, FAIL refusal).
    *   Named business tests: `test_r11_money_never_t0`, `test_r12_answer_header`, `test_r13_claim_trace`, `test_r14_cell_trace`, `test_r15_decision_record`.
    *   `test_contacts.py`, `test_lanes.py`, `test_business_traps.py`
        (including same-snapshot reproducibility), `test_claims.py`,
        `test_model_checks.py`, `test_user_text.py`, `test_records.py`, `test_prompt.py` (pre-filter, thread rule, RESUMED), `test_export.py`, `test_final_lint.py`,
        `test_memory.py` (closed enums, rejecting URLs and emails).
    *   **[v2]** `test_outbox.py`, `test_approvals.py`, `test_automations.py`.
    *   `e2e/`: runs `claude -p` against R-2 and R-4.

### Build decisions (P1–P2, as built in `dynamic-harness` @ `feat/p1-p2`)

- **Control files are JSON.** Wherever this spec says `*.yaml` for control or run files (`classes.yaml`, `gate.yaml`, `protected_paths.yaml`, `state_lexicon.yaml`, `enforcement.yaml`, `assumptions.yaml`, …), the stdlib-only build uses the same name with `.json`.
- **Signatures.** Where the plan lists older signatures (`run_gate(paths, log, key, profile, fold_ledger_path)`, `project(paths, ledger, scope)`), the built ones below are authoritative.
- **The gate receipt and the harness receipt.** fold_ledger's receipt carries `{mode, events_head, scope_sha, report_sha, verdict, units, caps, traced_values, ts}`. The harness computes `tree_sha`, `gate_sha`, `control_hash`, `business_hash` and `core_hash` itself, and signs them all. Further facts:
  - `--require-logs` checks that the two log files exist; the harness `EventLog` covers the chain.
  - Further 1.1.0 flags: `--events-head`, `--profile`, `--outputs`, `--assumptions`.
  - Chain verification runs in-process (`EventLog.verify`); `fold_ledger --verify-chain` is the independent second implementation, checked against it in `test_integration_gate.py`.
- **Python ≥ 3.9 and stdlib only.** JSON Schema validation uses the subset validator `harness_core.schema`.
- **No `writers.py`.** `Ledger` (`ledger.py`) is the only writer for rows. Scope versions live in `ledger.py` (`freeze_scope`, `descope`, `sanctioned_scope`) and are stored as `run/scope.v<n>.jsonl`.
- **Output blobs.** These are stored at `run/outputs/blobs/<sha>`. Rows cite outputs by their sha256.
- **Return shapes.**
  - `project(paths, ledger, scope=None, profile=None)` returns `{mode, rows, allow_blocked, remaining, scope_sha, scope_count}`.
  - `run_gate` returns `{ok, code, receipt|stderr}`.
  - `verify_receipt` returns `{ok, state, caps, reason}`.
- **2nd FAIL.** On a unit's 2nd FAIL, both the FAIL row and the resulting PASS_FLAGGED or BLOCKED row are written.
- **Gate child.** It runs under `sandbox-exec` on a temp copy of `run/` (its cwd, the only tree it may write), with `run/` itself denied for writes, `keys/` and `experience/` unreadable, and a 1793 s outer timeout (`--oracle-timeout` ≤ 1790). After it exits, the harness re-hashes `run/` and the copy. `run/` must still match its pre-run hashes, the `SUBMITTED` tree_sha and the `PROJECTED` gate-file hashes. Only receipt-safe events may follow the head the child was given. The fold receipt must echo the `mode`, `scope_sha` and `events_head` it was given. Any mismatch is a `TAMPER` event and no receipt. `REPORT.md` and `fold_receipt.json` are copied back only after these checks pass. `project()` also writes `gate/decisions.jsonl`, which `--decisions` reads.
- **Investigations require claims.** `run_gate` refuses `task_type: investigation` when `run/claims.jsonl` is missing.
- **`allow_blocked`.** It is an int or an expression over `units` (`+ - * /`, `ceil()`, `floor()`, for example `ceil(0.2*units)`). The harness evaluates it by walking the AST, never with `eval()`.
- **Receipt states.** `verify_receipt` derives the state from event-backed ledger rows, never from `gate/` files. The states are `"Checked"`, `"Done, not fully checked: <reasons>"` and `"Partly done: N of M"`. A stale `receipt.json` is revoked (`RECEIPT_REVOKED`) at the start of every gate attempt. After a receipt, only RECEIPT, MODEL_REGISTERED, RECORD, BUDGET_CHARGE, TURN, LEASE_HEARTBEAT and SAFETY_DENY may follow (a denied call does not undo the work). CONTACT may follow only with `payload.question_free: true`.
- **Event log.** `EventLog(run_dir, head_file, name="events.jsonl")` has `.lock()`, a re-entrant `flock` on `<name>.lock`. Writers hold it from `check_file` to the event, so the row file and its event cannot interleave with another writer. The chain hashes the raw lines: every line must be exactly the canonical JSON of its event. A `<head>.started` marker (`heads/<job>.started`) is written on the first append, so deleting both the log and the head is detected. Crash windows fail closed; operator repair is the recovery path.
- **Turns.** Turn rows carry a `system` field, true for `<task-notification>` (background-subagent) prompts. A system turn is never owner consent: `cite()` and `nonce_ok()` refuse it. Every turn append writes a chained `TURN` event, and every consumed nonce a `NONCE` event, to `data_root/session_events.jsonl`. Reads check `turns.jsonl` and `nonces_consumed.jsonl` against those events. Nonce check-and-consume runs under that log's lock.
- **Literal roots in `safe_open`.** `safeio._under` compares the realpath against each allowed root literally (`abspath`, never resolved), so a symlink planted at a root cannot redirect a read.
- **Written files.** `written.py` keeps `sessions/<sid>/written.json`. Every writer holds an `flock` on `written.lock` for its read-modify-write. There are three writers:
  - PreToolUse (`record_bash`) records the write targets of an allowed Bash call *before* the command runs, so the record never depends on PostToolUse firing.
  - PostToolUse and PostToolUseFailure (`Write|Edit|MultiEdit|NotebookEdit`) record the written path. A failed write may still have written part of the file.
  - An unresolvable target or any writer failure deletes the record, which forces the strict fallback.

  SessionStart with `source: startup` creates the record, `started.json` (the session's ctime baseline) and `tracked.json` (git-tracked files with their ctime). Compact, resume, clear and fork keep what is there when `started.json` and `tracked.json` both exist. If either is missing, the record is deleted (strict fallback) and `tracked.json` is written empty, so no tracked file is trusted. A fork or clear into a new session id therefore runs on the strict fallback for the whole session. A PostToolUse with no record leaves the fallback in place and does not fail. Files under a job's `work/` and `requests/` always count as written. A recorded directory covers every file under it (a `cp -r` or `tar -x` destination). If the record is missing or unreadable, the strict fallback treats every workspace file as written, except a tracked file whose ctime is unchanged. Separately, `untrusted_files` lists every workspace file whose ctime is at or after session start, and every tracked file whose ctime changed; the classifier treats them as written. mtime is never used.
- **Path tracking.** `pathtrack.py` makes the Bash classifier fail closed on any path, redirect or cwd it cannot resolve statically. It tracks `cd`/`pushd` through nesting, and treats links into job folders and recreated job folders as `protected_path`. `bash_write_targets(command, ctx)` lists every write target the classifier resolved, plus `None` for an unresolvable one.
- **Classifier policy.**
  - harness-\* trust rule: a `harness-<x>` command is trusted only when `<x>` is on the spec's agent-callable list and it resolves to `<plugin_root>/bin/harness-<x>`. Otherwise it is `unknown`.
  - make policy: `make`/`gmake` is `unknown` even with `-n`, or `self_written_exec` once code has been written this task.
  - `source`/`.` makes the cwd untrackable, so it is `unknown`.
  - Every word and here-string is resolved before it is judged. `cd`, `pushd` and `env -C`/`--chdir` (also attached or clustered, `-C/dir`, `-iC dir`) into, or to an ancestor of, `keys/` or `experience/` are `protected_path` (maintainer R2). When the data root is under the home directory, that includes `cd`, `cd ~` and `cd /`. An `env` short-option cluster it does not understand is `unknown`. BSD `find -f path` start paths are checked, and so is the cwd. `PYTEST_*` assignments are `unknown`, and a runner given `@file` arguments counts as running untrusted code when any exists. A directory filled earlier in the same command (`cp -r`, `rsync`, `tar -x`) makes every file under it written, so running code there is `self_written_exec`. Recursive readers (`grep -r` or `-d recurse`, `rg`, `ag`, `ack`, `diff -r`, `du`, `zip`, `cp -r`, `rsync`, `ditto`) and `git -C` are checked against the protected trees they reach. No option narrows a recursive `grep` (R3, revised): every word, the words after `--` and the cwd are checked as search roots. Recursion detection scans every word and fails closed on `--recur…`/`--dir…` prefixes and on any `-d` value except `read` or `skip`. Exec from any fd-like path (`/dev/fd/*`, `/dev/stdin`, `/proc/*/fd/*`) is inline code.
  - Assigning `CLAUDE_PLUGIN_*`, `CLAUDE_PROJECT_DIR`, `TMPDIR`, `PATH`, `DYLD_*`, `LD_*` and similar variables is `unknown`.
- **Architecture decisions on the parser (P1–P2 review rounds).**
  - The Bash classifier is defence in depth, not the boundary. It cannot fully contain an agent that runs as the same user, and every review round found a new place where it reads a command differently from the real tool (getopt clusters and abbreviations, GNU vs BSD vs ugrep, shell quoting it never sees). The boundary is the OS sandbox: `harness-sandbox-exec` and the gate child now, and the Claude Code sandbox with `denyRead` on `keys/` once P5 setup installs it. That is the next build step, before P3.
  - So the classifier is tuned to fail closed, never to unlock. No parsed option may turn a deny into an allow. R3 (`grep --exclude-dir` narrowing) was the one such rule; it is removed.
  - R2 stays: a `cd` into, or to an ancestor of, `keys/` is denied, including `cd ~`. Agents work inside the workspace and can use absolute paths; tracking alone would leave unmodelled recursive readers run from `~`.
- **Platforms.** macOS and Linux. Verified on macOS (APFS, `sandbox-exec`) and in Ubuntu 22.04 (bubblewrap 0.6.1) and 24.04 (bubblewrap 0.9.0) containers. Requirements: `/usr/bin/python3` ≥ 3.9 and `/usr/bin/git`; on Linux also `/usr/bin/bwrap` (`apt install bubblewrap`) with unprivileged user namespaces allowed. SessionStart runs `sandbox_exec.probe()` and refuses the session (`REFUSED`) when the backend is missing or cannot create a sandbox, quoting bwrap's error. Not yet verified on a real Ubuntu 24.04 host with AppArmor's unprivileged-userns restriction on, on other distributions, or on WSL2.
- **Sandbox exec.** `harness-sandbox-exec` resolves every path once (`_resolve`: realpath, then `lstat` of each component), so a symlink swapped in after the checks cannot move a rule. It denies reads and writes under the whole data root, and reads of `keys/` and `experience/` of every installed plugin (`secret_dirs`). The profile starts from `(deny default)`. It allows exec, fork, same-sandbox signals, sysctl reads, file reads, and writes under the cwd and a temp dir. It denies network, Apple Events and all mach-lookup, and denies writes to `CLAUDE*.md` and `.mcp.json` at any depth. The child runs in its own session, and its process group is killed on timeout and after exit.
- **Sandbox exec on Linux (bubblewrap).** The same `run()` contract, as mounts:
  - `/` read-only, with a fresh `/dev` and `/proc`. `/tmp`, `/var/tmp` and `/run` are replaced by empty read-only tmpfs, so X11, D-Bus, ssh-agent and docker sockets are unreachable. Every namespace is unshared: no network, and abstract unix sockets are per network namespace.
  - The cwd, a fresh temp dir and `allow_write` are writable. `deny_write` and every existing `CLAUDE*.md`/`.mcp.json` under them are read-only. Between `deny_write` and `allow_write` the most specific path wins, on both platforms: a job `work/` inside a denied `.claude/` is writable, and a denied path inside an allowed one is not; on the same path, deny wins. Each `deny_read` directory is replaced by an empty mode-000 tmpfs.
  - The child is pid 2 of its own pid namespace, with `--die-with-parent`, so a `setsid()` child cannot outlive the call.
  - Differences from macOS: bwrap cannot refuse *creating* a path that did not exist: a new `CLAUDE*.md`/`.mcp.json` at any depth, or a missing `deny_write` path such as `.claude/`. After the child exits, `run()` compares instruction files by inode (so a rename-then-recreate is caught), renames each new path aside to `<path>.harness-quarantine-<random>` (so a moved original or user files inside a new directory survive; only a path that cannot be moved is removed, and if that fails too the call raises), reports moved originals, and returns 126 (`INSTRUCTION_CODE`). The pid namespace is gone by then, so nothing races the check. Read-only mounts are not walked; a writable `.git` is. A directory the child made unlistable gets owner rwx back and is listed again; if it still cannot be listed, the call raises. The host `/tmp` is not readable inside the sandbox (macOS allows reads there). ext4 is case-sensitive, so a `.CLAUDE` directory is not `.claude`.
- **Sessions.** SessionStart writes `sessions/<sid>/status.json` `{ok, reasons, canary, ts, cwd}`. It writes a failed status before the checks, so a crash never leaves an old `ok`. It also writes the settings baseline (`settings_baseline.json`) that ConfigChange judges changes against; with no baseline, ConfigChange blocks. PreToolUse allows only reads unless a passing status under 24 h old exists. So a SessionStart refusal now restricts the session to reads.
- **Workspace and roots.** The workspace is `CLAUDE_PROJECT_DIR`, else the cwd SessionStart recorded, never the event's current cwd. Missing `CLAUDE_PLUGIN_DATA`/`CLAUDE_PLUGIN_ROOT` fails closed. The loop-contract root comes from `config_log.jsonl` (`loop_contract_root`), else `LOOP_CONTRACT_ROOT`. SessionStart needs loop-contract ≥ 1.1.0, read from its `.claude-plugin/plugin.json`.
- **Interpreter.** Hooks and shipped CLIs run as `/usr/bin/python3 -I`: an absolute path, with no user site-packages or `PYTHON*` variables.
- **Measured (`feat/p1-p2` @ `4c6fac6`).**
  - Test suite: macOS 1091 passed, 4 skipped (Linux-only), 3 xfailed. Ubuntu 22.04 and 24.04: 1090 passed, 5 skipped (macOS profile tests), 3 xfailed. loop-contract: 60 passed on all three.
  - Red team round 1: 133 of 135 held, on macOS and Linux.
  - Red team round 2: 181 of 188 held-out cases held before fixes, and 188 of 188 after, with 0 of 44 false denies.
  - The round-2 cases are now regression tests, so they no longer measure unseen attacks. Every release needs a fresh held-out red-team round.
- **Known gaps carried to P3** (see §Build decisions (P3) for what P3 closed).
  - Setup (P5) now installs the Claude Code sandbox and the deny rules (§Build decisions (P5 setup)). Until the owner runs it, a session is refused above T0.
  - If the PostToolUse hook times out after a Write or Edit, the write is not recorded. The file still counts as written through `untrusted_files` (its ctime is after session start). Bash writes no longer depend on PostToolUse.
  - `written.session_sets` walks the whole workspace on every PreToolUse call (for `untrusted_files` and the strict fallback), and walks every recorded directory. After `tar -x`, `unzip` or `cp -r` into the workspace root, the whole workspace counts as written for the rest of the session. Both are slow for large workspaces, and a walk past the 4 s watchdog denies the call.
  - On macOS, a gate or sandbox child that calls `setsid()` leaves the process group and escapes the group kill. Linux is not affected (pid namespace).
  - On Linux, creating a new `CLAUDE*.md`/`.mcp.json` or a missing `deny_write` path is detected and quarantined after the run, not prevented. A concurrent Claude session could read such a file while the command runs.
  - `source venv/bin/activate` (and any `source`/`.`) is now denied, because the cwd cannot be tracked through it.
  - `safeio._under` refuses roots reached through a symlinked parent (for example `/var` → `/private/var`). Callers must pass realpath'd roots.
  - Only `harness-hook` and `harness-sandbox-exec` exist under `bin/`. Every other `harness-*` CLI fails the trust rule, so it is denied live.
  - Verified live: the Bash tool's environment has no `CLAUDE_PROJECT_DIR` or `CLAUDE_PLUGIN_*`. Inside Claude Code's sandbox, `harness-sandbox-exec` therefore needs none of them (outer mode, §Build decisions (P5 setup)). Without that sandbox, it still refuses to run without them.
  - Nothing writes the claims, fetch-log or search-log files yet.
  - `protected._norm` homoglyph folding is reserved for the maintainer.
  - The opt-in `HARNESS_DEBUG_LOG` records hook metadata, with tool inputs and responses redacted to type and length. It is for development only.

### Build decisions (P3, as built in `dynamic-harness` @ `feat/p1-p2`)

- **Flow.**
  - UserPromptSubmit (`prompt.py`) records every turn, routes it, and runs the probe and the pre-filter.
  - When a card is needed, the model is told to draft one. It runs `harness-card validate` and `harness-compile --card <file>`. The plugin's `bin/` is on the Bash PATH, verified live.
  - `compile.apply` writes the job and points the session at it.
  - PreToolUse runs the pending-card gate, then lanes, then the contacts gate, then safety. PostToolUse runs the written-file record, the writers and then the tripwire. SubagentStop records harness subagents.
- **Probe and pre-filter** (`probe.py`, keywords in `control/probe.json`, English and Persian).
  - A verb sets `irreversible_classes`, and with it a T2 floor, only when it is an instruction, not a question: "send it" counts, "did we send it?" does not.
  - Deviation from the spec's list: the pre-filter also counts quantity words ("how many…") as needing a card, because a computed number is coming (R-11).
  - Named files are scanned for personal data only inside the workspace.
- **Card** (`card.py`).
  - Code sets `irreversible`, `reversibility`, `data_classes`, `personal_data` and `computed_numbers`. Deviation: the draft may raise these fields, never lower them.
  - `decision_class` is max-merged by policy severity.
  - Risk is at least medium unless its provenance is `user`, and high above a business-profile threshold.
  - `multi_session` is true only with `resume_of`, because `harness-estimate` is not built yet.
- **Tier** (`tier_select.py`).
  - It follows the pseudocode. A malformed field, an unknown decision class or an unknown parent tier fails towards T2.
  - 28 taxonomy fixtures cover all 13 task types.
- **Compile** (`compile.apply`).
  - The card's `request` is the owner turn's redacted text.
  - One owner prompt makes one job.
  - A prompt routed to an open task cannot compile a new card; re-understanding is P4.
  - A new task at T1 or above pauses the open one.
  - REFUSE writes the card and `STOPPED{REFUSED}`.
  - A tier above T0 needs a gate profile: `control/gate.json`, `<tier>.<task_type>` or `<tier>.default`.
- **Contacts** (`contacts.py`, `contact.py`; R-7, R-8).
  - The cap is 5 counted contacts per task, with `decision_ack` and `cost` exempt. One `--handoff` per task is allowed, for a before-spend contact only.
  - Start questions go in one message, at most 3.
  - No question once progress reaches 50%. Progress counts terminal units, or milestones for a single-unit task.
  - The cap is re-checked under the lock that records the contact.
  - Each typed contact serves only its purpose, so its exemptions are no way around the cap or R-8:
    - `plan` belongs to an investigation, before any work;
    - `assumption_sheet` belongs to a simulation, before any work;
    - `decision_ack` needs a `financial` card and is sent once per task.

    Start questions go in one message, through `harness-contact question` too.
  - A plan's text is rendered from `plan.json`, with free-text fields quoted. The hook shows it to the owner directly (`systemMessage`), not through the model. `--accept` needs the owner's reply to that plan contact, and the whole reply must be an approval. The contact is then consumed, and the grant is written with a `GRANT` event.
- **Memory** (`memory.py`; R-9).
  - Only `[pref:<key>]` questions are stored, and only one of the offered options from the closed enums.
  - Answers are read from `tool_response` only, because the model writes `tool_input`.
  - `default_choices` is not stored in v1.
  - Not yet verified: the exact `AskUserQuestion` response shape in an interactive session, since the tool is unavailable in `-p`.
- **Neutral tools.** `classes.NEUTRAL_TOOLS` lists Skill, TodoWrite, TodoRead, ToolSearch, ListMcpResourcesTool and AskUserQuestion. They pass the session gate and the pending-card gate, and they never trip the tripwire. Spawning an agent is not neutral.
- **Lanes** (`lanes.py`).
  - Web tools are allowed only for the researcher, in an investigation.
  - A researcher or verifier spawn needs a matching render, recorded in `renders.jsonl` with its kind:
    - `harness-research render` works only after an accepted plan, and renders from `plan.json`;
    - `harness-critic render <unit>` works only for a unit in scope, and renders from the frozen criteria.

    The spawn's prompt sha and agent type must both match.
  - A subagent's calls keep the task it was spawned in: its agent id must be linked in the task's `spawns.jsonl`. A SubagentStart hook links it before the subagent's first call, matching the oldest unlinked spawn of the same type; the Agent PostToolUse also links it. A subagent that is not linked keeps the open task's gates, with no grant, and may only read. A call with `agent_type` but no `agent_id` is the main thread.
- **Tripwire.** It is active at T0 while no card is pending. It trips on a side effect, or on the 9th call. When the probe saw money or personal data, it trips on any call other than the named neutral tools; there is no tool or file-name list to get around.
- **R-5.** SessionStart stops as `STOPPED_TAMPER` when a shipped `skills/**/SKILL.md` has no matching `approved_procedures.json` entry, and when there is any symlink under `skills/`. In a task, or while a card is pending, the Skill tool loads only approved `dynamic-harness:<dir>` skills.
- **Ledger writer.** `harness-ledger append --kind <kind> --row …` goes through the existing ledger rules (unit, verdict, decision, contradiction, milestone). This is what moves progress, and with it R-8.
- **Robustness.**
  - The prompt hook replaces lone surrogates and accepts a repeated or non-string `prompt_id` (a retried hook is one turn).
  - A session file that fails its check is moved aside (`.tamper-<id>`) and reset closed, with a T2 pending card, by a `SESSION_RESET` event. Setup and reads keep working.
  - A refused card keeps the pending card, so a refusal cannot lift the gate.
  - The tripwire skips only the print-only CLIs; `harness-sandbox-exec` counts.
- **Corrections and shadow.** Corrections are logged verbatim, as evidence only, and cite a real owner turn. The second draft is logged in shadow and never changes the card.
- **Known gaps carried to P4** (see §Build decisions (P4) for what P4 closed).
  - The Stop hook does not exist yet. A model can still answer in plain text while a card is pending, and a live `-p` run did exactly that (2 orders, 30 toman, from a read). The spec's enforcement for this is Stop refusing a T0 completion while `pending_card` is set.
  - Not built yet: lease, budget, stall, RE_UNDERSTAND, thread matching to terminal jobs, `cost` contacts, `harness-claims fetch` (P5 business tools) and `harness-run` (P5 packs).
  - `templates/fa.json` is a P5 item; P3 ships English only.

### Build decisions (P4, as built in `dynamic-harness` @ `feat/p1-p2`)

- **Lease.** `lease.json` has one owner session. `require` fails closed: with no lease, only the session in the jobs index may claim one. `claim` runs under the event-log lock and either acquires, or takes over a stale lease and raises the epoch. "continue" in another session claims the lease; a live lease elsewhere leaves that prompt outside the task, and it still needs its own card.
- **Budget.** PostToolUse charges every tool call in a T1/T2 task, subagents included (`BUDGET_CHARGE`). The checks run in this order:
  - calls above `calls_cap`, or a T1 task older than 30 minutes, give `STOPPED_BUDGET`;
  - 40 calls with no new unit verdict give `STOPPED_STALL`;
  - an estimated spend above the owner's task cap gives `STOPPED_COST`.
- **Money estimate.** The estimate is the transcript's token usage since the task started, priced by `control/prices.json`. It is active only when `business/profile.json` has `caps.task` in the price table's currency, so it stays off until the business layer (P5) sets a cap. The transcript is read again on each call; a byte offset is the upgrade if transcripts grow large.
- **Stops.** A stop appends `STOPPED{state}`, marks the jobs index and writes an interim decision record. Budget and cost stops also record an uncounted `cost` contact. The contact stores the latest turn seq, so only a later reply can accept it. While a task is stopped, only reads and `harness-contact cost --accept` run, and `contact.apply` refuses every other contact type. A `PAUSED` never masks a budget, cost or stall stop. The prompt hook resumes only a `PAUSED` task.
- **Continue after a stop.** `harness-contact cost --accept latest` needs the owner's reply to that contact, and the whole reply must be an approval ("yes", "continue"). The writer grants a one-time increment: half the old cap in calls (at least 20), 15 more minutes for T1, and half the spend for money. It then appends `APPROVAL{cost_increment}` and `RESUMED`.
- **Gate hook.** It has its own PostToolUse entry: matcher `Bash`, 1800 s timeout, watchdog 1795 s. It returns at once for any other command. On a lone `harness-gate request` it checks that the task is T1/T2, owned by this session and not stopped. It then runs `gate.run_gate` with:
  - the profile `gate.json` `<tier>.<task_type>` or `<tier>.default`, plus the task type;
  - the key `keys/hmac.key`, created 0600 on first use;
  - fold_ledger from the loop-contract root.

  The main PostToolUse entry treats `harness-gate` as print-only. Both entries run in parallel; a `BUDGET_CHARGE` after the receipt head is allowed, and a concurrent stop invalidates the receipt as it should.
- **Done mode needs packs.** Done mode needs an `oracle_cmd` that comes from a pack manifest, and no live writer records check outputs before `harness-run` (P5). In P4 a live task therefore gates in known mode, and its state line is "Partly done" or "Done, not fully checked".
- **Stop hook.** It never runs the gate (10 s timeout). With an open task it allows a stop on:
  - tamper, as `STOPPED_TAMPER`;
  - a running background subagent, as a wait with no record and no block count;
  - a stop record with no `RESUMED` after it;
  - a recorded contact (`pending_contact_id`);
  - a verified receipt, if the final message passes the lint. The task is then DONE in the jobs index, a final decision record is written, and the state line is the `systemMessage`.

  A final message that asks the owner something counts as a contact when one is allowed; otherwise it blocks. Any other stop blocks with the missing item named. Three blocks with the same reason in one request write `STOPPED_STALL`; that is how `stop_hook_active` loops end.
- **Final-message lint.** The lint finds state-lexicon words outside lines that match a `templates/en.json` template or the state line. After the gate, every money or quantity token must be in the receipt's traced set. That set is read from `gate/fold_receipt.json` and checked against `traced_set_sha` in the HMAC. The tokeniser is vendored from loop-contract (`harness_core/normalize.py`), and a test keeps the two copies identical.
- **Quick answers (rule 3).** A pending card blocks. State words block. A money word in the answer blocks once and sets `pending_card` with floor T1. This closes the P3 gap where a model answered in plain text while a card was pending.
- **RE_UNDERSTAND.** `harness-compile --card F --reunderstand <turn>` re-compiles the open task, once per task. The turn must be a correction that the prompt hook recorded after the latest scope freeze; the hook now tells the model how to re-understand. The writer:
  - writes card v(n+1), which keeps the owner's original request;
  - lets the tier only go up;
  - keeps a unit's id and verdicts only if the unit is identical and the oracle is unchanged, and gives every other unit a new id, so nothing carries over unverified;
  - marks units that are not kept `DESCOPED_BY_USER`, citing the turn;
  - resets the start-question allowance only when the turn names a changed unit;
  - returns the rework notice as a statement.
- **Follow-ups.** A prompt that matches the session's DONE task gets a hint to copy its card with the change and set `follows`. `compile` refuses `follows` unless it names a finished task. Refused and abandoned tasks are not followed up.
- **Decision record (R-15).** `records/<job>.jsonl` gets interim rows at resumable stops and a final row at REFUSED, DONE and STOPPED_TAMPER. It is assembled from artefacts only, and every string is redacted.
- **Review fixes (P4).** An independent review found 12 issues. All are fixed, and each has a regression test in `test_p4_review.py`:
  - Stop re-hashes `work/` against the latest `SUBMITTED` tree, so an edit after the gate needs a new gate.
  - The lexicon lint exempts only the receipt's state line. `compile` refuses a `deliverable.description` that contains state words, because the opening line quotes it. It removes format characters and folds text the way the probe does.
  - RE_UNDERSTAND drops a unit only when the correction names it. A unit replaced under the same label is superseded.
  - A `PAUSED` task runs only reads.
  - A call that a writer refused is still charged.
  - The cost contact stays open after a reply that is not an approval. The owner ends a stall with a plain "continue" or "yes". Two concurrent stops write one record.
  - Stop counts blocks per rule, not per reason text, and also caps the total of mixed blocks in one request.
  - Cost acceptance and RE_UNDERSTAND run their checks under the event-log lock.
  - Stall progress counts units with a terminal verdict, and the stall fires on the 40th call.
  - Only a running `subagent` background task makes Stop wait.
  - `budget.json` and `lease.json` are left out of the gate's run/ snapshot, because the parallel main hook writes them during the gate. A `GATE_STARTED` event keeps the lease live while a gate runs.
  - The owner sees the cost question as a `systemMessage`. A PostToolUse block with a `systemMessage` exits 0 with JSON.
- **Card drafts.** A live `claude -p` run showed that a model could not write its card draft while a card was pending. A `.json` file directly in `.claude/loops/drafts/` is now writable while a card is pending (no symlink on the way). That run also verified the Stop hook live: it blocked a plain-text answer three times and then ended the request with "Stopped, not done".
- **Tests.** `test_lease`, `test_budget`, `test_gatehook`, `test_stopgate`, `test_state_machine` and `test_p4_review` cover these paths. The suite passes on macOS and on Ubuntu 22.04 and 24.04 (`--privileged` containers, real fold_ledger, `HARNESS_REQUIRE_INTEGRATION=1`).
- **Known gaps carried to P5.** `harness-claims fetch`, `harness-run` and packs (done mode), `templates/fa.json`, `/dynamic-harness:memory`, the business layer (which turns the money cap on) and `/dynamic-harness:status --abandon`. All were built in P5 (see below).

### Build decisions (P5 setup, as built in `dynamic-harness` @ `feat/p1-p2`)

- **Plugin name.** The plugin is `dynamic-harness`, so commands are `/dynamic-harness:<cmd>` and agent types `dynamic-harness:<agent>`. An unrelated public plugin named `harness` (an agent-team designer) is common, and plugin names namespace commands and agent types. The `harness-*` CLI names are unchanged.
- **Where setup runs.** Setup runs entirely in the UserPromptSubmit hook, outside the Claude Code sandbox, and the model never takes part. Verified on 2.1.296 and in the docs:
  - the hook gets the typed line before the command expands;
  - a JSON `block` decision stops the prompt, shows the reason to the user, and adds nothing to context (0 turns, $0).

  `commands/setup.md` sets `disable-model-invocation: true`. Its body only matters if the hook did not run, and it tells the model to change nothing. Every other prompt passes through before any state is read.
- **Two-step consent.** `/dynamic-harness:setup` shows the plan, grouped by file, in plain words with the exact keys and entries, plus a one-time 6-digit code. `confirm <code>` applies exactly that plan only if all of these hold:
  - the code matches;
  - it is under 10 minutes old;
  - it comes from the same session;
  - no settings file changed since the plan.

  The code works once. `undo` reverses setup's own recorded operations, with the same two steps. A value changed after setup is left as is and listed; every other later edit is kept. `status` runs `verify`.
- **Scopes.** These follow the documented merge rules:
  - `sandbox.network.strictAllowlist` is honoured only from user, managed or `--settings` scope, so it is the one key written to `~/.claude/settings.json`. It acts only where the sandbox is on.
  - Everything else goes into the workspace's `.claude/settings.local.json`, so other projects keep their settings. Local beats project and user for single values; permission rules and sandbox path lists merge across scopes; `disableBypassPermissionsMode` takes the strictest value.
  - Lists that merge and must be empty (`excludedCommands`, `network.allowedDomains`, `network.allowUnixSockets`) are emptied in whichever file sets them.
  - Sandbox-weakening switches (`enableWeakerNestedSandbox`, `allowNestedUserNamespaces`, `network.allowAllUnixSockets`, `filesystem.disabled`) are turned off wherever they are on. Custom proxy ports (`network.httpProxyPort`, `socksProxyPort`) are removed. `filesystem.allowRead`/`allowWrite` entries on or inside a protected path are removed.
  - When the user file sets such a list or switch, the plan says that other projects lose it too.
- **What it writes.**
  - Sandbox: on, with `failIfUnavailable` and no unsandboxed escape hatch.
  - `denyRead`: the whole data root, and every plugin's `keys/` and `experience/` (glob).
  - `denyWrite`: `~/.claude`, `~/.claude.json`, the data, plugin and loop-contract roots, and `CLAUDE.md`/`CLAUDE.local.md` at any depth. Claude Code already protects `.claude` settings/skills/agents/commands/hooks, `.mcp.json` and `.git` hooks/config.
  - Permissions: `Read(//…/keys/**)`, `Read(//…/experience/**)` and `Edit(//…/**)` rules for those roots, plus `disableBypassPermissionsMode: "disable"`.
  - Harness files: `keys/hmac.key` (32 random bytes, 0600 in a 0700 directory, never rotated by a re-run), and a `config_log.jsonl` row with the release hashes and the loop-contract root. loop-contract is found through `config_log`, `LOOP_CONTRACT_ROOT` or `installed_plugins.json`, and must be ≥ 1.1.0.
  - Verified live: Claude Code loads the generated settings without a settings error, and they deny the data root.
- **Care.**
  - Values are compared by JSON type and value, so `1` never counts as `true`.
  - A setting of the wrong shape (a non-object container, a list that is not a list of strings) blocks the plan with a message. It is never rewritten silently.
  - Before it offers a plan, setup checks the state it would produce, managed settings included. If anything would still be missing, it blocks and names it; an administrator's managed settings are named as the cause.
  - Setup never overwrites invalid JSON.
  - It never writes through a symlinked workspace `.claude/` or `settings*.json`, because a cloned repo controls those. It follows the user's own symlinked dotfile and names the real target.
  - It keeps file modes and backs up every file it changes.
  - It refuses the home directory, or one of its parents, as the workspace.
  - A release hash that changed since the last setup is named as a warning in the plan. It is never re-baselined quietly, since that would hide what SessionStart reports as `STOPPED_TAMPER`.
- **Not done, by decision.**
  - `cleanupPeriodDays` is not changed. The spec gives no value, and the key applies to every project, so the Claude Code default (30 days) stands.
  - The `openpyxl`/`formulas` check belongs to the business packs, which are not built yet.
  - Managed deployments need an administrator: install the plugin in managed `enabledPlugins` before `allowManagedHooksOnly` (spike S9).
- **SessionStart.** It runs `setup.verify` over the effective settings: managed > local > project > user, with Claude Code's strictest-wins and user-only rules. It refuses above T0, quoting what is missing, until setup has run.
- **`harness-sandbox-exec` inside Claude Code's sandbox (outer mode).**
  - Verified live: the native sandbox cannot nest there (`sandbox_apply: Operation not permitted`, exit 71), and the Bash tool's environment has no `CLAUDE_*` variables.
  - The mode is decided once per run: `ok`, `nested` (only that exact error on macOS) or `broken`. `broken` fails closed.
  - When nested, `run()` verifies from the inside, then runs the command in the surrounding sandbox. It adds a clean env (no proxy credentials), the timeout, the process-group kill, and the quarantine guard over the whole project. It refuses unless all of these hold:
    - every plugin's `keys/` is unreadable (home from `pwd`, not `$HOME`), including `hmac.key` opened by name, and `experience/` too;
    - there is no direct network, and no proxy from the environment opens a CONNECT tunnel;
    - every plugin data root is unwritable;
    - every `deny_read` path is unreadable, and every existing `deny_write` path is unwritable.

    These checks prove properties, not identity. Faking them without a sandbox needs writes to the data root, which the classifier denies.
  - The launcher's outer path finds the project from `CLAUDE_PROJECT_DIR` or the nearest `.git`/`.claude` ancestor. It keeps the protected-cwd refusal and passes Claude Code's protected names and every `CLAUDE*.md` as `deny_write`.
  - Verified live: the command printed 42 with exit 0; the sandbox proxy refused the probe's CONNECT; `--cwd .git` was refused.
  - Not verified: Claude Code's Linux sandbox. There the probe has to prove containment, and a failed bwrap counts as nested.

### Build decisions (P5, as built in `dynamic-harness` @ `feat/p1-p2`, release 0.4.0)

- **Packs.** A pack is a skill directory with a `manifest.json` (`harness/pack@1`) that registers check
  entrypoints with their sha256. A check id is `<family>.<name>` and exactly one pack registers it; the
  `operations` pack registers `doc.check` and `decide.score_flip` as §7 lists them. Shipped packs: `finance`
  (`finance.reconcile`, `.kpi`, `.bridge`, `.list`), `research` (`research.check`, `.report`), `model` (the
  model-checks pack: `model.recalc`, `.checks`, `.report`) and `operations`. `card` refuses an executable oracle
  that no pack registers, and each unit takes the oracle's check unless it names another registered one.
  `tools/release_hashes.py` re-hashes manifests and approves the shipped skills (R-5).
- **`harness-run`.** It has its own PostToolUse entry (matcher `Bash`, 300 s, watchdog 295 s); the main entry
  treats it as print-only. The hook runs the check under `sandbox_exec` on copies: each `--input` (a regular
  workspace file outside protected paths, at most 20 MB), `work/`, and the run-state files the manifest names.
  The output row keeps the check's stdout, its stdin and every input as blobs. A crash, a missing result line
  or a timeout is recorded as `error` and never backs a PASS. The `harness-doc`, `harness-decide` and
  `harness-estimate` CLIs of §4 are not separate tools: documents and decisions run through `harness-run
  doc.check | decide.score_flip`, and the estimate is part of the opening line.
- **Done mode.** When the card's oracle is an executable registered check, the gate profile gets
  `oracle_cmd = python3 -I lib/harness_core/oracle.py`. Inside the gate sandbox the oracle replays every output
  a PASS row cites, with the recorded stdin and input snapshots but the submitted deliverables, and requires
  the same result and values. An edit after a check, a tampered blob or a changed pack entry fails the gate.
- **Business layer.** `/dynamic-harness:business show | set | add | remove | metric remove` runs in the
  UserPromptSubmit hook, like setup: a plain-language change, a one-time code, `confirm` in the same session
  within 10 minutes. There is no model-callable `harness-business`; the classifier keeps refusing it. Every
  write first checks that the layer still equals its recorded hash (`business.guard`), then appends the new
  hash to `config_log.jsonl`, so an owner's change never shows as tamper and an outside edit is never
  re-baselined. Metric definitions come from `harness-contact definition_confirm --metric F`: the owner sees a
  sentence rendered from the metric and confirms it with a plain yes; a changed definition gets a new version.
- **Money.** `control/defaults/prices.json` is in USD; an owner in another currency sets `fx.USD`. With no rate
  the money caps stay off and the call caps still apply. The monthly cap adds this month's recorded spend. The
  estimate is stored in `budget.json` `money` and copied into the decision record with the task's duration.
  The opening line carries the median time (and money) of at least 3 past DONE tasks at the same tier, ±30%.
- **Answer header (R-12).** `harness-answer header --unit U --output SHA` renders three lines from a
  recorded `finance.reconcile` output and the business layer; a metric with no confirmed definition, or a
  check on an older definition version, is refused. The source is registered with the facts the check
  computed. The gate includes the header and traces its numbers; its status line, read back from the recorded
  file, is the one extra line the gate and Stop let use state words. Control labels lose state words.
- **Claims (R-13).** `harness-claims fetch` has its own PostToolUse entry (60 s). The hook fetches for the
  web-researcher only, under the active grant: https, every resolved address public, the connection pinned to
  the checked address, redirects re-checked by hand, 2 MB. The snapshot is the page's visible text; the fetch
  log keeps the raw hash, the status and an `injection_suspect` flag. SubagentStop writes full claim-ledger
  rows; `harness-claims cite` says which rows decide a unit. The gate checks only cited claims, with each
  unit's load-bearing rows under one key, so `check_claims` and `research.check` agree on independence.
  Accepting a plan opens `search_log.jsonl` and `contradictions.jsonl`. Confidence is a value of
  `research.check`; there is no separate `harness-claims confidence`.
- **Models (R-14).** A forecast is `work/model.json` over `run/assumptions.json`. `model.recalc` records every
  cell, so `numbers_traced` checks R-14 on the first forecast; the gate also passes `--assumptions`. Period
  labels are written `1405/09`, which the shared tokenizer reads as a date. `harness-model assume` marks a
  value owner-confirmed only when the owner's own latest turn states it; the assumption sheet confirms the
  rest. `register` runs after a receipt that verifies and writes `models/registry.json`, outside the business
  layer, because the receipt's HMAC covers the business hash. `scenario` opens `<job>.s<n>` through
  `compile.apply` in-process (`scenario_of` cannot come from a CLI request); `import` reads a returned CSV sheet.
  The back-cast rebuilds the bank export's month-end balances; the backtest compares the model with a naive
  seasonal baseline on the last 3 months.
- **Templates.** `templates/en.json` holds the owner-facing moments; hints to the model are `model.*` keys.
  `templates/fa.json` is written natively. The language is the profile's `language`, else the owner's latest
  turn. The opening line, budget and stall stops, the research plan, the answer header and the Stop state
  line follow it, with Persian digits. The stall message no longer quotes internal reasons.
  `harness-lint-user-text` checks internal terms, missing keys, placeholders and Persian orthography.
- **Commands.** `/dynamic-harness:memory`, `status [--abandon|--repair]`, `export --task|--period`, `config`
  and `off` run in the hook like setup; every change needs a code. `config` edits only the tunable control
  files: classes, protected paths, approved procedures and the lexicon are fixed in v1. `--repair` closes a
  `STOPPED_TAMPER` task as ABANDONED without trusting or re-signing it; layer hashes are re-baselined by setup.
  `control/enforcement.json` ships `contacts` and `tripwire` as `enforce` (stage 1 runs every key in enforce);
  `/dynamic-harness:off` sets them to `shadow`, where they log `SHADOW_WOULD_BLOCK` and block nothing.
- **Fix found in P5.** `gate.work_changed` hashed its temporary copy through macOS's `/var` symlink, so Stop saw
  any non-empty `work/` as tamper. It now uses the real path; a regression test covers it.
- **Tests.** `test_run`, `test_business`, `test_answer`, `test_claims`, `test_model`, `test_operations`,
  `test_user_text`, `test_admin`, `test_localize` and `test_business_traps` (one test per trap class), with
  `test_r11_money_never_t0` to `test_r15_decision_record`. The done-mode, claim and model tests run the real
  fold_ledger under the real sandbox.
- **Known gaps.** Check notes are in English inside Persian text. The rows behind an answer are not attached
  as an Excel file, and the model deliverable is a JSON model plus a CSV table, not a formula workbook (no
  `openpyxl` or `formulas` dependency). Monte Carlo runs are not built. The stall and saturation stop rules for
  research are recorded but not enforced. v2 items stay v2.

### Verification Plan

*   **Build:** `claude plugin validate harness/.claude-plugin/plugin.json`.
*   **Unit tests:** `pytest harness/tests -q` and `pytest loop-contract-skill/tests -q`.
*   **Static analysis:** `ruff check harness`, plus `shellcheck` on any shell shims.
*   **Coverage:** ≥ 90% branch coverage on `safety.py`, `stopgate.py`, `tier_select.py`, `ledger.py`, `gate.py` and `fold.py` (plus `writers.py` once it exists in P3/P4).
*   **E2E:** `pytest harness/tests/e2e -m e2e`. Needs `claude` on PATH.
*   **Dependencies (pinned, vendored):** `openpyxl` and `formulas`, used for spreadsheet writing and the independent recompute. JSON Schema validation uses the stdlib subset validator `harness_core.schema`. Everything else is stdlib.

*Drafted with EGM*
