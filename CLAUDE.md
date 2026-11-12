# dynamic-harness

A Claude Code plugin that builds a working harness for a business from what its owner asks for. Python 3.9,
standard library only. The plan is `PLAN.md`. The interfaces are `INTERFACES.md`. The layout is in
`docs/development.md`.

## The rule that outranks speed

The harness never reports a wrong number. A cell, a word or a file it cannot read is counted and reported, never
guessed. Every check fails closed: an error is never a pass. Hooks are the only trusted writers.
When a faster way and this rule disagree, the rule wins. Everything else on this page is about speed.

## Hard limits

- Standard library only. Hooks run as `/usr/bin/python3 -I` on Python 3.9.6: no syntax or module newer than 3.9.
- Never edit `lib/harness_core/normalize.py`. It is a shared copy from loop-contract.
- Edit `PLAN.md`, `dynamic_harness_design_spec.md`, `skills/*/SKILL.md` and `agents/*.md` only when the task
  brief names the file.
- No country, currency, language, calendar or person in code. Region data lives in locale packs
  (`control/defaults/locales/`), and the mechanism must serve any region with data alone. Do not add languages,
  currencies or packs to prove it. A test of a mechanism uses made-up codes and pack ids (`QDA`, `t-minor`,
  `zorb`). `tests/test_locale_literals.py` holds a count per file that may only go down.
- A reader of untrusted text (cells, pack words, owner input) is linear in the length of its input.
- A setup step says whether its change is global or for one project. No silent global change.
- Never run `claude -p`, `claude --print`, or pytest with `HARNESS_E2E=1`: they cost money. Never write under
  `~/.claude`. Never push, tag or publish.

## Commands

```bash
/usr/bin/python3 tools/suite.py tests/test_x.py tests/test_y.py   # the test files you touched, in parallel
/usr/bin/python3 tools/suite.py                                   # the whole suite, about 100 seconds
/usr/bin/python3 -m pytest -q -p no:cacheprovider tests/test_x.py -k name   # one test, with its traceback
ruff check --select F --ignore F811 <files you changed>           # lint; F811 is noise from imported fixtures
bin/harness-lint-user-text                                        # after a change to user-facing text
```

`tools/suite.py` prints one result line, then `PASS` or `FAIL`. Quote that line in a report. It runs a failing
file a second time alone, so a test that fails only on a busy machine does not cost a round.

## Work fast

These rules come from a measurement of this repository: 12 tasks took 18 hours, and most of that time went to
serial test runs, fix rounds that found no fault, and whole-file reads.

1. **Tests while you work: only the files you touch.** Run the whole suite once, before you hand back. Do not
   run it after each edit, and do not run it at the base commit to compare.
2. **Never copy the repository.** For old behaviour, read `git show <base>:<path>`.
3. **Read with a purpose.** `grep -n` first, then read the lines you need. Do not read a whole file of more
   than 200 lines.
4. **Tests are tables.** One `pytest.mark.parametrize` table per rule, with the expected values written out by
   hand. No second implementation of the rule inside the tests. Every row must be able to fail for a real fault;
   leave out a row that takes the same path as another row. Start a subprocess only where the hook or the
   command line is the thing under test. A new test file runs in under 5 seconds.
5. **Keep the suite under 150 seconds** with `tools/suite.py`. The task that takes it over that makes its tests
   cheaper before it closes.
6. **Stay inside the brief.** A fault outside it goes in your hand-back as one line under "Later"; do not fix
   it. The one exception: it gives a wrong number today. Then stop and report it first.
7. **Documents in the same commit.** When behaviour or an interface changes, update `INTERFACES.md`, the page
   in `docs/` that describes it, and `CHANGELOG.md`.
8. **Short hand-back.** At most 15 lines: status, commits, the `tools/suite.py` result line, concerns, "Later".
   Detail goes in the report file the brief names. Subagents do not start subagents.

To implement a task of the plan, use the `run-task` skill (`.claude/skills/run-task/SKILL.md`). It replaces the
longer subagent loop in this repository.

## Code style

Match the file you edit: its naming, its comment density, its idiom. A comment says why, in one line. Keep lines
under about 115 columns. Commit subjects read `type(P<n>): a plain sentence`.

## Shell notes

macOS and zsh: quote every glob; `grep` has no `-P`. If a wrapper rewrites command output (for example `rtk`),
use its raw mode (`rtk proxy <command>`) when you need the exact output.
