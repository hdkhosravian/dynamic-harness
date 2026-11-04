# Development

This page is for people who change the harness itself: running the tests, finding your way around the
repository, and adding a pack.

## Run the tests

```bash
python3 -m pytest -q
```

- About 1,670 tests, standard library plus pytest. They pass on macOS (sandbox-exec) and on Ubuntu 22.04 and
  24.04 (bubblewrap).
- The gate integration tests use a real `fold_ledger.py` from a `loop-contract-skill` checkout next to this
  repository, or from the path in `HARNESS_FOLD_LEDGER`. Set `HARNESS_REQUIRE_INTEGRATION=1` to turn a missing
  fold_ledger into a failure instead of a skip.
- On Linux, run the suite in a `--privileged` container, because bubblewrap needs user namespaces. Under
  bubblewrap `/tmp` is private, so fold_ledger must not live under `/tmp`.
- `tests/corpus/bypass.jsonl` holds 200+ attempts to get around the safety hook, each with its expected
  decision.
- `spike/` holds the platform spike that settled how Claude Code hooks behave, and the setup spike. See
  [`spike/RESULTS.md`](../spike/RESULTS.md).

Lint the harness's own user-facing text (internal terms, missing keys, placeholders, Persian orthography):

```bash
bin/harness-lint-user-text
```

## Repository layout

```text
.claude-plugin/     plugin.json, marketplace.json
agents/             plugin subagents (tool-restricted; hooks enforce the rest)
bin/                harness-* CLIs (request printers) and harness-hook (the hook entry point)
commands/           slash commands (setup, business, memory, status, export, config, off)
control/defaults/   default control layer: classes, egress allowlist, protected paths, gate profiles, tiers, ...
hooks/hooks.json    hook registration (every entry fails closed)
lib/harness_core/   the library: one module per concern
schema/             JSON Schemas for run records and the card
skills/             executor guidance (task-card) and the packs (finance, research, model, operations)
tools/              release tooling (pack hashes, skill approvals)
templates/          user-facing text in English (en.json) and Persian (fa.json)
tests/              pytest suite, bypass corpus, card fixtures
spike/              Claude Code platform spike
```

Rules the code follows:

- Hooks and shipped CLIs run as `/usr/bin/python3 -I`: an absolute path, no user site-packages, no `PYTHON*`
  variables.
- Python 3.9 and the standard library only.
- Control files are JSON, even where the spec says `.yaml`.
- Every handler fails closed: an exception is a denial.

## Adding a pack

A pack is a skill directory under `skills/` with a `SKILL.md` and a `manifest.json`.

```text
skills/<pack>/
  SKILL.md            tells the model when and how to call the checks
  manifest.json       registers the checks
  checks/<name>.py    one entry file per check
```

### The manifest

```json
{
  "schema": "harness/pack@1",
  "pack": "mypack",
  "version": "1.0.0",
  "description": "What the pack is for.",
  "checks": {
    "mypack.total": {
      "entry": "checks/total.py",
      "sha256": "<filled in by tools/release_hashes.py>",
      "task_types": ["data_question"],
      "timeout": 120,
      "run_files": [],
      "description": "What this check proves."
    }
  }
}
```

- A check id is `<family>.<name>`, and exactly one pack may register it.
- `task_types` lists the card task types the check serves.
- `run_files` names the run-state files the check may read (for example `assumptions.json` or
  `claims.jsonl`). They are copied into the check's folder; the check never sees the real run state. Only the
  names in `packs.RUN_FILES` are allowed.
- `timeout` is in seconds, at most 280.
- The hook runs an entry only when it is a regular file inside the pack whose sha256 equals the manifest's.

### The check protocol

The hook runs the entry as `/usr/bin/python3 -I <entry>` in the OS sandbox, with this working folder:

```text
inputs/<n>-<name>   read-only copies of the files named with --input
work/               the task's deliverables (at gate time: the submitted copy)
run/                the run-state files the manifest's run_files names
```

On stdin it gets one JSON object: `{check_id, unit, attempt, args, inputs, work, run, business, card, today}`.
It must print one JSON line last and exit 0:

```json
{"result": "pass", "values": {"total": 1250000}, "notes": []}
```

- `values` holds the numbers the report may cite. Every total is computed here, never by the model.
- `notes` holds plain-language findings the report must show (rows left out, sources that disagree).
- A gap the owner must know about should make the result `fail`.
- Use `harness_core.checklib`: `read_request()`, `emit()`, and `safe_path()` for every file name the agent
  passes in, so a name cannot reach outside the check's folder.

The shipped entries are three lines that call into `lib/harness_core`, for example
`skills/finance/checks/reconcile.py`.

### Register and approve

1. Add the check id to the list of registered checks in `skills/task-card/SKILL.md`, so the model knows it.
2. Review the changed files, then re-hash the manifests and approve the skills:

   ```bash
   python3 tools/release_hashes.py "<release label>"
   ```

   This writes each entry's sha256 into its manifest and records every `SKILL.md` hash in
   `control/defaults/approved_procedures.json` (R-5). A skill whose hash did not change keeps its approver.
3. Add tests. The existing pack tests (`tests/test_run.py`, `tests/test_model.py`, ...) show the pattern.

A changed entry file without a re-hash is refused at run time and at the gate. A `SKILL.md` without an
approval stops the session as `STOPPED_TAMPER`. Re-run `/dynamic-harness:setup` after a release, so the
session's release-hash baseline matches.

## Design documents

- [`dynamic_harness_design_spec.md`](../dynamic_harness_design_spec.md): the normative design. Its "Build
  decisions" sections record how each phase was built and why it differs from the draft where it does.
- [`INTERFACES.md`](../INTERFACES.md): the interface contract between modules, phase by phase.
