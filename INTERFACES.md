# Interface contract (P1–P2)

This is the normative design: `/Users/sam/Downloads/dynamic_harness_design_spec.md`.
The sections that matter most are "Trust boundaries and protected writes", "Hooks",
"Gate adapter", "Gate and Stop semantics" and "Irreversible-action control".

This file fixes module boundaries so parallel work fits together. Change it
only by agreement.

## Constraints

- **Language:** Python ≥ 3.9 (the system `python3` runs the hooks). Use
  `from __future__ import annotations`. No `match` statements, and no
  `X | Y` in runtime expressions.
- **Dependencies:** stdlib only. JSON-Schema validation goes through
  `harness_core.schema.validate`, a small subset validator.
- **Tests:** `pytest` from the repo root. `tests/conftest.py` puts `lib/` on
  `sys.path`.
- **Process:** TDD. Write a failing test first, then make it pass.

## Paths (`harness_core/paths.py`)

```python
@dataclass(frozen=True)
class JobPaths:
    data_root: Path      # ${CLAUDE_PLUGIN_DATA}
    workspace: Path      # project cwd
    job: str
    run: Path            # data_root/runs/<job>/   (protected run state)
    work: Path           # workspace/.claude/loops/<job>/work/
    requests: Path       # workspace/.claude/loops/<job>/requests/
    heads: Path          # data_root/heads/<job>
    keys: Path           # data_root/keys/
def job_paths(data_root, workspace, job) -> JobPaths
```

## Errors (`harness_core/errors.py`)

`HarnessError`, `TamperError(HarnessError)`, `Denied(HarnessError)` (has a
`.reason`), `SchemaError(HarnessError)`.

## Canonical hashing (`harness_core/canon.py`)

- `canonical_json(obj) -> bytes`: sorted keys, `(',',':')` separators,
  `ensure_ascii=False`, UTF-8.
- `sha256_json(obj) -> str`: hex digest of `canonical_json`.
- `sha256_bytes(b) -> str`.
- `scope_bytes(rows) -> bytes`: canonical JSON lines, each terminated by `\n`.
- `tree_sha(files: list[Path], base: Path) -> str`: sha256 of the canonical
  JSON of the sorted `[relpath, sha256]` pairs. Reads use `safe_open`.
- `hmac_receipt(key: bytes, fields: dict) -> str`: HMAC-SHA256 over
  `canonical_json(fields)`.

## Safe file IO (`harness_core/safeio.py`)

- `safe_open(path, allowed_roots) -> bytes`: opens with `O_NOFOLLOW`, then
  checks on the fd: `S_ISREG`, `st_nlink == 1`, and that the real path is
  under one of `allowed_roots`. Raises `TamperError` otherwise.
- `atomic_write(path, data: bytes)`: write to a temp file, then
  `os.replace`. Creates parent dirs.
- `append_bytes(path, data: bytes)`: append-only write (creates parents).

## Event log (`harness_core/events.py`)

- `EventLog(run_dir: Path, head_file: Path)`
  - `.append(type: str, payload: dict, row_sha: str|None=None, file: str|None=None, file_sha: str|None=None) -> dict`.
    The event is `{seq, ts, type, payload, row_sha?, file?, file_sha?, prev_sha, sha}`.
    It is written to `run_dir/events.jsonl` and updates `head_file`.
  - `.verify() -> str`. Recomputes the chain and checks it against
    `head_file`. Returns the head sha, or raises `TamperError`.
  - `.latest_file_sha(file) -> str|None`.
  - `.check_file(path, file)`. Raises `TamperError` when the bytes on disk
    differ from the latest recorded `file_sha`, when a recorded file is
    missing, or when an unrecorded file is present. `file_sha == ""` records
    a writer's deletion: absent is then fine and a reappearing file is tamper.
  - `.events() -> list[dict]`.

## Ledger, outputs and scope (`harness_core/ledger.py`; there is no `writers.py`)

Files under `run/`: `{ledger,verdicts,decisions,contradictions,milestones}.jsonl`,
`outputs/outputs.jsonl`, `outputs/blobs/<sha256>` (raw output bytes) and
`scope.v<n>.jsonl` (one file per scope version). Each write first checks the
file against its last `file_sha`, then appends an event carrying `row_sha`,
`file` and the new `file_sha`.

- `Ledger(paths, log: EventLog)`
  - `.freeze_scope(rows, turns=None) -> dict` writes `scope.v1.jsonl` and a
    `SCOPE` event `{version, count, scope_sha, turn_seq, removed}`. Refuses a
    second freeze.
  - `.descope(unit_ids, turn_id, turns) -> dict` writes `scope.v(n+1)`; needs a
    cited turn after the freeze that names each unit, plus one `DESCOPE` event
    per unit.
  - `.scope() -> list`, `.sanctioned_scope() -> list` (replays every version;
    `HarnessError` on a unit dropped without `DESCOPE`).
  - `.rows() -> [(kind, row)]` from the verified event log.
  - `.append_row(kind, row) -> dict`. `kind` is one of `unit`, `verdict`,
    `decision`, `contradiction`, `milestone`.
    - PASS needs at least one cited output, and every cited output needs
      `exit_code == 0`, `result == "pass"`, and the unit's registered
      `check_id`.
    - SUPPORTED/REFUTED needs a cited `research.check` output with exit 0.
    - On a unit's 2nd FAIL, writes PASS_FLAGGED(failure_sig) when
      `work/<deliverable>` exists and the failure came from the registered
      check; otherwise BLOCKED.
  - `.state(unit_id) -> str` (latest verdict, `OPEN`, or `DESCOPED_BY_USER`).
- `Outputs(paths, log)`
  - `.record(check_id, unit, attempt, exit_code, result, values, data: bytes) -> dict`.
    Writes `run/outputs/blobs/<sha256>` and appends the row to
    `run/outputs/outputs.jsonl` (event type `OUTPUT`).
  - `.rows() -> list`.
- `Turns(data_root)` (`turns.py`; `data_root/turns.jsonl`, `nonces_consumed.jsonl`)
  - `.append(turn) -> dict`. Adds `seq` (1-based ordinal), `ts` and `text_sha`.
    `after_seq` in `cite()` compares against that `seq`.
  - `.cite(turn_id, after_seq=None, must_match=None) -> dict`. Raises
    `Denied`.
  - `.latest() -> dict`
  - `.nonce_ok(command, args) -> bool`. One-use: consumes the nonce.

## Gate adapter (`harness_core/fold.py`, `harness_core/gate.py`)

- `project(paths, ledger, scope=None, profile=None) -> dict`. Runs
  `check_protected` first. Writes `run/gate/{ledger.jsonl, verdicts.jsonl,
  scope.jsonl, profile.json}` and `data_root/pins/<job>.json`, and returns
  `{mode, rows, allow_blocked, remaining, scope_sha, scope_count}`.
  `profile.allow_blocked` may be an int or `"units"`.
- `check_protected(paths, log) -> None`: chain verify plus `check_file` on
  every file an event recorded and the row files. Raises `TamperError`.
  - Mapping table: spec §Gate adapter.
  - Mode is `done` only when all units are PASS/BLOCKED/SUPPORTED/REFUTED/NOT_INVESTIGATED,
    BLOCKED ≤ allow_blocked, and ≥ 1 PASS. Otherwise `known`, with no
    `--oracle-cmd` and `allow_blocked = units`.
  - Refuses (`HarnessError`) while any unit's latest verdict is FAIL.
- `run_gate(paths, log, key, profile, fold_ledger_path, runner=None) -> dict`.
  - First revokes any existing `run/receipt.json` (deletes it, event
    `RECEIPT_REVOKED` with `file_sha ""`), so a stale receipt never outlives a
    new attempt.
  - Copies `work/**` to `run/outputs/submitted/` (`SUBMITTED`), calls
    `project()`, then runs fold_ledger through `runner` (default
    `sandbox_exec.run`) as `runner(argv, cwd=str(run), None, timeout=1793,
    deny_read=[keys/, experience/])`, with `cwd = run/`.
  - argv: `--mode --ledger --verdicts --decisions --out gate/REPORT.md --scope
    --expect-scope-sha --expect-scope-count --allow-blocked --group-by unit_type
    --repo-root run/ --receipt gate/fold_receipt.json`, plus `--verdict-vocab`
    when set, `--oracle-cmd`/`--oracle-timeout ≤ 1790` in done mode
    (`HarnessError` if done mode has no `oracle_cmd`), and `--outputs
    outputs/outputs.jsonl --numbers-trace <submitted *.md/*.txt/*.csv>` when any exist.
    Also `--events-head <head at gate time>`; `--lint-lexicon gate/lexicon.json`
    (state_lexicon.json flattened to `{terms, template_lines}`; off with
    `profile.lint_lexicon = false`); `--include run/answer_header.md` when present;
    `--sources run/claims.jsonl --fetch-log run/fetch_log.jsonl --snapshots run/snapshots
    --profile gate/profile.json` when `claims.jsonl` exists; `--require-logs
    run/contradictions.jsonl run/search_log.jsonl` when `profile.task_type ==
    "investigation"` or `profile.require_logs`.
  - Returns `{ok: True, code: 0, receipt}` or `{ok: False, code, stderr}`
    (event `GATE_FAILED`). On success writes `run/receipt.json` (fields
    `HMAC_FIELDS` + `caps` + `hmac`; `traced_set_sha` = sha256_json of the fold
    receipt's `traced_values`) and a `RECEIPT` event.
- `verify_receipt(paths, log, key) -> dict`. Returns `{ok: True, state, caps}`
  or `{ok: False, state: None, caps: [], reason}`.
  `state` ∈ {Checked, Partly done, Done, not fully checked}.
  - Only these event types may follow the receipt: RECEIPT, MODEL_REGISTERED,
    RECORD, BUDGET_CHARGE, TURN, LEASE_HEARTBEAT, CONTACT.

## Safety (`harness_core/classes.py`, `egress.py`, `query_guard.py`, `protected.py`, `safety.py`)

- `classify(tool_name, tool_input, registry=None, ctx=None) -> Classification`.
  `registry` defaults to `ctx.connector_registry`. Never raises.
  `Classification` (frozen) has `cls`, `reversibility` (`R0`–`R3`),
  `irreversible` (bool), `reason`, `url` and `query` (both optional; set for
  egress so the researcher exception can check them). The class table is in
  `control/defaults/classes.json`: irreversible `external_message`, `money`,
  `payee_change`, `price_publish`, `accounting_entry`, `destructive`,
  `local_app_automation`, `external_write`, `automation_create`,
  `egress_offlist`, `egress_with_data`, `unknown_mcp_write`,
  `self_written_exec`, `protected_path`, `unknown`; reversible `read`,
  `local_reversible`, `draft_create`. An unreadable table makes everything
  irreversible.
  - The Bash parse covers `sh -c`, `bash -c`, `python -c`, `node -e`, `curl`,
    `wget`, `git push`, `osascript`, `open`, `shortcuts`, `automator`, `rm`,
    redirections, and running a file written this task.
  - Anything unknown resolves to irreversible.
- `is_protected(path, ctx, cwd=None) -> bool` (also `is_read_protected`,
  `is_scratch`, `outside_workspace`). Patterns are in
  `control/defaults/protected_paths.json`. The path is resolved first;
  realpath is applied when the file exists.
- `egress.check(url, ctx) -> None | raises Denied`. Checks the allowlist and
  any per-job grant.
- `query_guard.check(text_or_url, protected_terms=(), owner_tokens=()) -> None | raises Denied`.
  Percent-decodes, then rejects segments longer than 64 characters,
  entropy above 4.0 bits per character, digit runs, and protected terms.
- `safety.Ctx` (dataclass, built by `hookmain.build_ctx`): `workspace`,
  `data_root`, `plugin_root`, `home`, `loop_contract_root`, `job`,
  `agent_type`, `permission_mode`, `connector_registry`, `written_this_task`,
  `task_started`, `grant`, `allowlist`, `protected_terms`, `owner_tokens`,
  `nonce_ok`, `temp_roots` (None = system temp dirs), `settings_baseline`,
  `plugin_id`, `log`.
- `safety.on_pre(evt: dict, ctx: Ctx) -> dict`. Returns `{}` for allow or
  `{"decision":"deny","reason":...}`. Implements the "PreToolUse safety" row
  in v1: every irreversible class is denied, except researcher-lane GETs
  under a grant. Logs `SAFETY_DENY` to `ctx.log` when set.
- `session.on_start(evt, ctx) -> dict` (SessionStart). Returns `{}` or
  `{"decision":"block", "state", "reason", ...}`. Refuses, in order:
  `bypassPermissions`; Claude Code < 2.1.295 or unknown version
  (`session.claude_version(env=None, run=subprocess.run)` reads
  `CLAUDE_CODE_VERSION`, else parses `claude --version`); no
  `config_log.jsonl` (`REFUSED`); hash mismatch or unreadable tree
  (`STOPPED_TAMPER`); failed PreToolUse canary (`CANARY_FAILED`).
- `config_guard.on_change(evt, ctx) -> dict` (ConfigChange).

## Hook entry (`bin/harness-hook`, `harness_core/hookmain.py`)

- `main(event, stdin_json, env, handlers=None, timeouts=None) -> (exit_code, stdout_json)`.
  Never raises.
  - `HANDLERS`: `PreToolUse → safety.on_pre`, `SessionStart →
    session.on_start`, `ConfigChange → config_guard.on_change`; a handler is
    called as `fn(stdin_json, build_ctx(stdin_json, env))`. An unknown event
    exits 2.
  - Wraps every handler in `try/except BaseException`. A watchdog per
    event (`WATCHDOG`): default 4 s, Stop 9 s, fetch 55 s, gate 1795 s.
    `hooks/hooks.json` sets `timeout: 5` and `onFailure: "block"` on every
    hook (OQ-4 S1).
  - Any exception, overrun or non-dict result exits 2: deny for PreToolUse,
    block for Stop.
  - A normal deny/block also exits 2 with `harness: <reason>` on stderr
    (spike 1c: blocks, and the stderr reaches the model). `stdout_json` is
    `None` then; a non-empty allow result is printed as JSON with exit 0.
  - Opt-in debug trace: `HARNESS_DEBUG_LOG=<file>` appends one JSON line per
    call `{event, input, code, out, reason}`. A logging failure never changes
    the decision.
- The plugin manifest is `.claude-plugin/plugin.json` (`name: harness`);
  hooks load from `hooks/hooks.json`.

## Sandbox exec (`harness_core/sandbox_exec.py`, `bin/harness-sandbox-exec`)

- `run(argv, cwd, stdin_bytes=None, timeout=60, deny_read=(), deny_write=(), allow_write=(), env=None) -> (code, out: bytes, err: bytes)`.
  - On macOS it uses `/usr/bin/sandbox-exec` with a generated profile
    (`profile(cwd, tmp, deny_read, deny_write, allow_write)`): deny network
    and Apple Events, deny writing outside `cwd`, a fresh temp dir and
    `allow_write`, deny reading and writing `deny_read`.
  - The child env is only `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, `HOME` and
    `TMPDIR` (the temp dir) and `LANG`, plus `env`.
  - A timeout returns code `124` (`TIMEOUT_CODE`).
  - When `sandbox-exec` is missing, it raises `HarnessError` (fails closed).
