---
name: run-task
description: Use when implementing a task from PLAN.md in this repository - the fast task loop (one brief, one builder, one risk review, one fix round). Replaces the superpowers subagent-driven-development loop here.
---

# run-task: the fast task loop

Target: a normal task closes in under 45 minutes. The old loop took 141 minutes per task; the time went to
serial test runs, fix rounds that found no fault, and long briefs. The rule that does not move: the harness
never reports a wrong number. `CLAUDE.md` holds the rules every agent follows; do not repeat them in a brief.

You are the controller. Workspace: `.superpowers/sdd/PLAN/` (ledger `progress.md`, parked items `later.md`).

## Who does what

| Work | Agent | Model |
|---|---|---|
| Tests, code and documents of one task | `dh-builder` | Opus 5.5, medium (its default) |
| Mechanical task (complete spec, at most 2 files) or documents only | `dh-builder` with `model: "sonnet"` | Sonnet 5.5, high |
| Review of a risk diff | `dh-reviewer` | Sonnet 5.5, high (its default) |
| Product prompt text (`skills/*/SKILL.md`, `agents/*.md`) | general-purpose with `model: "fable"` | Fable, high |
| Whole-branch review, once, at the end | fresh general-purpose with `model: "fable"`, file paths only | Fable, high |
| A change under about 40 lines in one file | the controller, inline | - |

One writer at a time. Never two agents that write to the repository at once.

## Steps

1. **Size.** One bullet of the plan is one task. Batch small bullets of the same shape into one dispatch. Do
   not split a task and do not add a task without the owner's word. The one exception: a wrong number a user
   can reach today.

2. **Brief**, at most 60 lines, at `<workspace>/task-<id>-brief.md`:
   - goal, in two sentences;
   - files to change;
   - acceptance table: input and expected result, written by you, by hand. For a reader of numbers, money or
     dates, put the sign, zero, empty, separator and overflow rows in the table now. They are what review
     found late in earlier tasks;
   - documents to update;
   - out of scope.

   A number in a brief is a number you ran. Prototype for at most 10 minutes. If the design is not clear after
   that, the task is too big: cut it at a seam and tell the owner.

3. **Dispatch.** Write `BASE=<git rev-parse HEAD>` to the ledger. Dispatch one `dh-builder` with: one line on
   where the task fits, the brief path, the report path. Nothing else: no history, no pasted code.

4. **Check**, about 3 minutes, yourself:
   - `git diff --stat BASE` matches the brief's file list;
   - `/usr/bin/python3 tools/suite.py` prints `PASS`;
   - three rows of the acceptance table, run by hand.

5. **Review only a risk diff.** A risk diff changes one of: reading of a number, amount or date; money
   arithmetic; a hook or a gate; a schema or a validator; a writer under the data root; a permission or path
   check. Write the diff to a file (`git diff -U10 BASE > <workspace>/task-<id>.diff`) and dispatch one
   `dh-reviewer` with the brief, report and diff paths. For any other diff, read it yourself and close.

6. **One fix round.**
   - Reproduce each blocking finding yourself before you send it. A finding that involves a number counts as
     blocking, whatever rating the reviewer gave it.
   - Send all confirmed findings to the builder in one message (`SendMessage` to the same agent).
   - Everything else is one line in `later.md`. It does not open a round.
   - After the fix: run the reviewer's reproduction, run the suite, read the fix diff. Dispatch a second review
     only when the fix diff is over 60 lines or changes the design.
   - A second fix round only when the fix itself is wrong on a wrong number, a fail-open or a hard limit. After
     that, stop and tell the owner.

7. **Close.** One ledger line:
   `Task <id>: complete (commits <base7>..<head7>, <suite result line>, review: <none|clean|n fixed, m later>)`

## Controller rules

- Read excerpts: `grep -n`, then the lines you need. Never paste an agent's output into a dispatch or the
  ledger; point to the file.
- One ledger line per event. A decision you make alone is one line:
  `Ruling: <what> - <why> - <cost if wrong>`.
- Do not poll a running agent. Its hand-back arrives on its own.
- When a builder hands back `NEEDS_CONTEXT`, answer the question and resume the same agent.

## Phase end and branch end

- Phase end: one serial run of the whole suite in the background
  (`/usr/bin/python3 -m pytest -q -p no:cacheprovider tests`), to catch a test that depends on order. Then
  show `later.md` to the owner and let the owner choose what to fix.
- Branch end: one whole-branch review by a fresh Fable agent that gets file paths only, one fix dispatch, then
  `superpowers:finishing-a-development-branch`.
