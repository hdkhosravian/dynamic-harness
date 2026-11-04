# Interface contract (P1–P2)

This is the normative design: `/Users/sam/Downloads/dynamic_harness_design_spec.md`.
The sections that matter most are "Trust boundaries and protected writes", "Hooks",
"Gate adapter", "Gate and Stop semantics" and "Irreversible-action control".

This file fixes module boundaries so parallel work fits together. Change it
only by agreement.

## Constraints

- **Language:** Python ≥ 3.9. Hooks and the shipped CLIs run as
  `/usr/bin/python3 -I` (an absolute path, so there is no PATH lookup; `-I`
  ignores user site-packages and `PYTHON*` variables). Use
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
    workspace: Path      # project dir (CLAUDE_PROJECT_DIR, see Hook entry)
    job: str
    run: Path            # data_root/runs/<job>/   (protected run state)
    work: Path           # workspace/.claude/loops/<job>/work/
    requests: Path       # workspace/.claude/loops/<job>/requests/
    heads: Path          # data_root/heads/<job>   (the run event log's head file)
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
  checks on the fd: `S_ISREG` and `st_nlink == 1`. It then checks that the
  realpath is the same inode as the open fd (a swap after open is caught) and
  that the realpath is under one of `allowed_roots`. Raises `TamperError`
  otherwise.
  - `_under(path, roots)` compares against each root **literally**
    (`os.path.abspath`, never resolved). Resolving the root would follow an
    attacker's symlink at the root (`ln -s keys work`). The consequence: a root
    reached through a symlinked parent (for example `/var` → `/private/var`)
    never matches the file's realpath, so the read is refused.
- `atomic_write(path, data: bytes)`: write to a temp file in the same
  directory, fsync, then `os.replace`. Creates parent dirs.
- `append_bytes(path, data: bytes)`: `O_APPEND | O_NOFOLLOW` append (mode
  `0600`), fsynced. Creates parent dirs.

## Event log (`harness_core/events.py`)

- `file_lock(path)`: exclusive `fcntl.flock` on `path`, re-entrant within one
  process (created if missing, `O_NOFOLLOW`).
- `EventLog(run_dir: Path, head_file: Path, name: str = "events.jsonl")`
  - Files: `run_dir/<name>`, the lock `run_dir/<name>.lock`, `head_file`, and
    the started marker `<head_file>.started` (for the run log,
    `data_root/heads/<job>.started`). The marker is written on the first
    append, so deleting both the log and the head afterwards is `TamperError`.
  - `.lock()`: the log's `file_lock`. Hold it across any read-check-write
    sequence (verify + append + head; row file + event).
  - `.append(type: str, payload: dict, row_sha=None, file=None, file_sha=None) -> dict`.
    Takes the lock, verifies the chain first (never extends a tampered chain),
    and validates against `schema/run/event.json`. The event is
    `{seq, ts, type, payload, row_sha?, file?, file_sha?, prev_sha, sha}`,
    where `sha = sha256_json(event without sha)`. It appends the canonical
    bytes plus `\n`, rewrites `head_file`, then re-verifies.
  - Chain hashing is over the **raw lines**: every line on disk must be
    exactly `canonical_json` of its parsed event. A whitespace,
    key-order or duplicate-key variant is `TamperError`, as is an incomplete
    last line.
  - `.verify() -> str`. Recomputes the chain and checks it against
    `head_file`. Returns the head sha (`""` for an empty, never-started log),
    or raises `TamperError`.
  - `.events() -> list[dict]` (verified).
  - `.latest_file_sha(file) -> str|None`.
  - `.check_file(path, file)`. Raises `TamperError` when the bytes on disk
    differ from the latest recorded `file_sha`, when a recorded file is
    missing, or when an unrecorded file is present. `file_sha == ""` records
    a writer's deletion: absent is then fine and a reappearing file is tamper.
  - Crash windows (line then head; row file then event) fail closed with
    `TamperError`. Recovery is operator repair.

## Ledger, outputs and scope (`harness_core/ledger.py`; there is no `writers.py`)

Files under `run/`: `{ledger,verdicts,decisions,contradictions,milestones}.jsonl`,
`outputs/outputs.jsonl`, `outputs/blobs/<sha256>` (raw output bytes) and
`scope.v<n>.jsonl` (one file per scope version). Each write holds
`log.lock()` from the `check_file` to the event. It checks the file against
its last `file_sha`, appends the row, then appends an event carrying
`row_sha`, `file` and the new `file_sha`.

- `Ledger(paths, log: EventLog)`
  - `.freeze_scope(rows, turns=None) -> dict` writes `scope.v1.jsonl` and a
    `SCOPE` event `{version, count, scope_sha, turn_seq, removed}`. Refuses a
    second freeze and duplicate unit ids.
  - `.descope(unit_ids, turn_id, turns) -> dict` writes `scope.v(n+1)`; needs a
    cited turn after the latest freeze that names each unit (label or id),
    plus one `DESCOPE` event per unit.
  - `.scope() -> list`, `.sanctioned_scope() -> list` (replays every version
    and checks each file's bytes against its `SCOPE` event; `HarnessError` on a
    unit dropped without `DESCOPE`).
  - `.rows() -> [(kind, row)]` from the verified event log (`ROW` events).
  - `.append_row(kind, row) -> dict`. `kind` is one of `unit`, `verdict`,
    `decision`, `contradiction`, `milestone`. It holds the lock for the whole
    rule, because the rule reads earlier rows.
    - PASS needs at least one cited output. Every cited output must come from
      the row's `attempt` and have `exit_code == 0`, `result == "pass"`, and
      the unit's registered `check_id`.
    - SUPPORTED/REFUTED needs a cited `research.check` output with exit 0.
    - BLOCKED and NOT_INVESTIGATED need a `reason`. PASS_FLAGGED cannot be
      written directly.
    - On a unit's 2nd FAIL, it writes the FAIL row, then PASS_FLAGGED(failure_sig)
      when `work/<deliverable>` exists and the failure came from the registered
      check; otherwise BLOCKED.
    - A milestone (`outline|draft|final`) needs a `work/` file, which it hashes.
  - `.state(unit_id) -> str` (latest verdict, `OPEN`, or `DESCOPED_BY_USER`).
- `Outputs(paths, log)`
  - `.record(check_id, unit, attempt, exit_code, result, values, data: bytes) -> dict`.
    Writes `run/outputs/blobs/<sha256>` and appends the row to
    `run/outputs/outputs.jsonl` (event type `OUTPUT`).
  - `.rows() -> list`.

## Turns and nonces (`harness_core/turns.py`)

- `Turns(data_root)`. Files: `data_root/turns.jsonl`,
  `data_root/nonces_consumed.jsonl`, and a session-level chained event log
  `EventLog(data_root, data_root/session_events.head, name="session_events.jsonl")`.
  - Every turn append writes a `TURN` event `{turn_id, seq, system}`, and every
    consumed nonce a `NONCE` event `{turn_id}`, each with `row_sha`, `file` and
    `file_sha`. Reads `check_file` both JSONL files against the log, so a row
    written outside the class is `TamperError`.
  - `.append(turn) -> dict`. Needs `text_norm`. Adds `seq` (1-based ordinal),
    `ts`, `text_sha` and `system`. `system` is true when the caller passes
    `system: True` or `text_norm` starts with `<task-notification>` (a
    background-subagent result). A duplicate `turn_id` is `SchemaError`. Holds
    the log's lock.
  - `.cite(turn_id, after_seq=None, must_match=None) -> dict`. Raises
    `Denied` for an unknown turn, a `system` turn (never owner consent), a
    turn not after `after_seq`, or one naming none of `must_match` (whole-word,
    case-insensitive). Raises `TamperError` when `text_norm` does not match
    `text_sha`.
  - `.latest() -> dict|None`.
  - `.nonce_ok(command, args) -> bool`. Check and consume run under the log's
    lock. True only when the latest turn is a non-system turn whose
    `typed_command` equals `command args…` (whitespace-split) and its nonce has
    not been consumed. One use.

## Gate adapter (`harness_core/fold.py`, `harness_core/gate.py`)

- `fold.allow_blocked(expr, units) -> int`. `expr` is an int or an expression
  over `units` using `+ - * /`, `ceil()` and `floor()` (for example `"units"`,
  `"ceil(0.2*units)"`). It is evaluated by walking the AST, never `eval()`.
  The result must be a whole number ≥ 0, else `HarnessError`.
- `check_protected(paths, log) -> None`: verifies the chain, then runs
  `check_file` on every file an event recorded, plus the row files and
  `outputs/outputs.jsonl` (this catches planted files).
  `pins/<job>.json` is checked under `data_root`. Raises `TamperError`.
- `project(paths, ledger, scope=None, profile=None) -> dict`. Runs
  `check_protected` first and uses `ledger.sanctioned_scope()`. A `scope`
  argument that differs from it is `HarnessError`. Writes
  `run/gate/{ledger.jsonl, verdicts.jsonl, scope.jsonl, profile.json,
  decisions.jsonl}` and `data_root/pins/<job>.json` (`{scope_sha,
  scope_count}`). It then appends a `PROJECTED` event carrying the sha of each
  gate file (`file = pins/<job>.json`). Returns `{mode, rows, allow_blocked,
  remaining, scope_sha, scope_count}`.
  - Mapping table: spec §Gate adapter.
  - Mode is `done` only when all units are PASS/BLOCKED/SUPPORTED/REFUTED/NOT_INVESTIGATED,
    BLOCKED ≤ `allow_blocked(profile.allow_blocked, units)`, and ≥ 1 PASS.
    Otherwise `known`, with no `--oracle-cmd` and `allow_blocked = units`.
  - Refuses (`HarnessError`) while any unit's latest verdict is FAIL.
- `run_gate(paths, log, key, profile, fold_ledger_path, runner=None) -> dict`.
  1. Revokes any existing `run/receipt.json` (deletes it; event
     `RECEIPT_REVOKED` with `file_sha ""`), so a stale receipt never outlives a
     new attempt.
  2. Investigations need claims: `profile.task_type == "investigation"`
     without `run/claims.jsonl` is `HarnessError`.
  3. Copies `work/**` to `run/outputs/submitted/` (event `SUBMITTED {tree_sha}`).
     A symlink anywhere in the `work/` or `requests/` path, or in the tree, is
     a `TAMPER` event plus `TamperError`.
  4. Calls `project()`, deletes stale `gate/REPORT.md` and
     `gate/fold_receipt.json`, and writes `gate/lexicon.json`.
  5. Takes the event head, hashes every file under `run/` (except
     `receipt.json`, `events.jsonl`, its lock and the child outputs), and
     copies them into a fresh temp dir (`harness-gate-*`).
  6. Runs fold_ledger with the temp copy as cwd:
     `runner(argv, str(copy), None, timeout=1793, deny_read=[keys/, experience/],
     deny_write=[run/])`. The default runner is `sandbox_exec.run`. The child
     can write only the copy and its own temp dir.
  - argv (every path is inside the copy): `python3 <fold_ledger_path> --mode
    --ledger gate/ledger.jsonl --verdicts gate/verdicts.jsonl --decisions
    gate/decisions.jsonl --out gate/REPORT.md --scope gate/scope.jsonl
    --expect-scope-sha --expect-scope-count --allow-blocked --group-by unit_type
    --repo-root <copy> --receipt gate/fold_receipt.json --events-head <head>`.
    It adds:
    - `--outputs outputs/outputs.jsonl` (or `/dev/null` when absent) and
      `--numbers-trace <submitted files>` when any submitted file has a text
      suffix (`.md .txt .csv .json .html .tsv`, case-insensitive);
    - `--lint-lexicon gate/lexicon.json` (state_lexicon.json flattened to
      `{terms, template_lines}`; off with `profile.lint_lexicon = false`);
    - `--include answer_header.md` when present;
    - `--sources claims.jsonl --fetch-log fetch_log.jsonl --snapshots snapshots
      --profile gate/profile.json` when `claims.jsonl` exists;
    - `--require-logs contradictions.jsonl search_log.jsonl` when
      `profile.task_type == "investigation"` or `profile.require_logs`;
    - `--verdict-vocab` when set;
    - `--oracle-cmd`/`--oracle-timeout ≤ 1790` in done mode (`HarnessError` if
      done mode has no `oracle_cmd`).
  7. A non-zero exit is event `GATE_FAILED` and returns `{ok: False, code, stderr}`.
     After a zero exit it checks five things. Any failure is a `TAMPER` event
     and `{ok: False, code, stderr: <problem>}`, with no receipt.
     - `run/` must be unchanged since the pre-run hashes, and still match the
       `SUBMITTED` tree_sha and the `PROJECTED` gate-file hashes.
     - The copy's inputs must be unchanged.
     - The head passed to the child must still be in the log.
     - Only receipt-safe events may follow that head.
     - `fold_receipt.json` must echo the `mode`, `scope_sha` and `events_head`
       it was given.
     A missing or unparseable fold receipt is `GATE_FAILED`.
  8. On success it copies `REPORT.md` and `fold_receipt.json` back into
     `run/gate/`, then writes `run/receipt.json` (fields `HMAC_FIELDS` + `caps`
     + `hmac`; `traced_set_sha` = sha256_json of the fold receipt's
     `traced_values`; `caps_sha` = sha256_json of the sorted caps). It then
     appends a `RECEIPT` event and returns `{ok: True, code: 0, receipt}`.
  - `HMAC_FIELDS = (events_head, scope_sha, tree_sha, report_sha, gate_sha,
    control_hash, business_hash, core_hash, mode, caps_sha, traced_set_sha)`.
    `gate_sha` covers `gate/{ledger,verdicts,scope,decisions}.jsonl`,
    `profile.json`, `lexicon.json` and `fold_receipt.json`.
- `verify_receipt(paths, log, key) -> dict`. Returns `{ok: True, state, caps}`
  or `{ok: False, state: None, caps: [], reason}`. It runs
  `check_protected`, finds the receipt's `events_head` in the log, checks the
  events after it, then recomputes the live fields from disk and compares the
  HMAC.
  - `state` is derived from event-backed ledger rows, never from `gate/` files.
    It is one of:
    - `"Partly done: N of M"` while any unit's latest verdict is BLOCKED,
      NOT_INVESTIGATED, FAIL or OPEN;
    - `"Done, not fully checked: <reasons>"` (reasons: the caps, each PARTIAL
      row's `missing`, or `known mode`; joined with `; `);
    - `"Checked"`.
  - Only these event types may follow the receipt (`AFTER_RECEIPT`): RECEIPT,
    MODEL_REGISTERED, RECORD, BUDGET_CHARGE, TURN, LEASE_HEARTBEAT, SAFETY_DENY
    (a denied call does not undo the work), and CONTACT only when
    `payload.question_free is True` (`after_receipt_ok(e)`).

## Safety (`harness_core/classes.py`, `pathtrack.py`, `egress.py`, `query_guard.py`, `protected.py`, `safety.py`)

- `classify(tool_name, tool_input, registry=None, ctx=None) -> Classification`.
  `registry` defaults to `ctx.connector_registry`. Never raises.
  `Classification` (frozen) has `cls`, `reversibility` (`R0`–`R3`),
  `irreversible` (bool), `reason`, `url` and `query` (both optional; set for
  egress so the researcher exception can check them). The class table is in
  `control/defaults/classes.json` and each row has a `v1_action`.
  Irreversible classes: `external_message`, `money`,
  `payee_change`, `price_publish`, `accounting_entry`, `destructive`,
  `local_app_automation`, `external_write`, `automation_create`,
  `egress_offlist`, `egress_with_data`, `unknown_mcp_write`,
  `self_written_exec`, `protected_path`, `unknown`. Reversible classes: `read`,
  `local_reversible`, `draft_create`. An unreadable table makes everything
  irreversible.
  - `classify_bash(command, ctx)`: a compound command takes its worst segment.
    The parse covers `sh -c`, `bash -c`, `python -c`, `node -e`, `eval`,
    `source`, wrappers, `curl`, `wget`, `git push`, `osascript`, `open`,
    `shortcuts`, `automator`, `rm`, redirections, and running a file written
    this task.
  - Anything unknown resolves to irreversible.
  - **harness-\* trust rule.** A command named `harness-<x>` is trusted only
    when `<x>` is in `HARNESS_CLIS` (the spec's agent-callable bin list;
    `harness-hook` is excluded) and it resolves to
    `<plugin_root>/bin/harness-<x>` as an existing file. Otherwise it is
    `unknown`. `harness-sandbox-exec` accepts only `--cwd`, `--deny-read` and
    `--timeout`. Every possible effective cwd must be inside the workspace and
    not protected, else the call is `destructive`. Inside it,
    `self_written_exec` and egress results become `local_reversible`.
  - **make policy.** `make`/`gmake` is `unknown` (even with `-n`: `$(shell …)`
    and `+` lines still run), or `self_written_exec` when code was written
    this task.
  - `source`/`.` runs `exec_file`, and the cwd can no longer be tracked, so
    the result is `unknown` (this denies `source venv/bin/activate`).
  - Assigning or exporting a variable in `DANGEROUS_ENV` (including
    `CLAUDE_PLUGIN_DATA`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PROJECT_DIR`, `TMPDIR`,
    `PATH`) or with a prefix `DYLD_`, `LD_` or `GIT_TRACE` is `unknown`.
  - Written-this-task test (`is_written`): the path is in
    `ctx.written_this_task`, was written earlier in the same command, is under
    a job's `work/` or `requests/`, is in `ctx.untrusted_files`, or has a
    ctime ≥ `ctx.task_started`.
- `pathtrack.py` (fail-closed path and cwd tracking, called from `classes._Bash`):
  - Rule 1: a path word, redirect or cwd that cannot be resolved statically is
    `unknown`. `NAME=literal` is tracked; a quoted or escaped `$` is literal.
  - Rule 2: `cd`/`pushd` inside eval, source, subshells and substitutions
    either propagates (a union of possible cwds) or adds an unknown cwd.
    `env -C DIR`/`--chdir[=]DIR`/`-iC DIR`/`-CDIR` changes the cwd the same
    way. A `cd`, `pushd` or `env -C` into, or to an ancestor of, `keys/` or
    `experience/` is `protected_path` (maintainer R2), including `cd ~` when
    the data root is under home.
  - Rule 3: creating a link inside a job's `work/` or `requests/`, or
    recreating one removed earlier in the command, is `protected_path`.
  - Rule 4: files under `work/` and `requests/` always count as agent-written,
    so runners pointed at them are `self_written_exec`.
  - Rule 5: Glob patterns resolve against the search path (`search_roots`); a
    `..` after a wildcard may reach `/`.
  - `bash_write_targets(command, ctx) -> list`: sorted realpaths the
    classifier treats as written (allowed or not), plus one `None` when any
    target is unresolvable. A parse error gives `[None]`.
- `is_protected(path, ctx, cwd=None) -> bool` (also `is_read_protected`,
  `is_scratch`, `outside_workspace`, `contains_read_protected`, `under_any`,
  `is_hardlinked`). Patterns are in `control/defaults/protected_paths.json`
  (`write_protected`, `write_exceptions`, `read_protected`,
  `always_protected`). The path is resolved first (realpath). Matching is
  NFC-normalised and case-insensitive. `load_control(name, ctx)` reads
  `data_root/control/<name>`, else the shipped default.
- `egress.check(url, ctx) -> None | raises Denied`. Checks the allowlist
  (suffix match) and any per-job grant (researcher lane only, exact host).
  Ports must match; an entry without a port admits only 443/80.
- `query_guard.check(text_or_url, protected_terms=(), owner_tokens=()) -> None | raises Denied`.
  Percent-decodes repeatedly, then rejects segments longer than 64 characters,
  entropy above 4.0 bits per character, runs of 4+ digits (any script),
  protected terms and owner tokens.
- `safety.Ctx` (dataclass, built by `hookmain.build_ctx`): `workspace`,
  `data_root`, `plugin_root`, `home`, `loop_contract_root`, `job`,
  `agent_type`, `permission_mode`, `connector_registry`, `written_this_task`,
  `task_started`, `grant`, `allowlist`, `protected_terms`, `owner_tokens`,
  `nonce_ok`, `temp_roots` (None = system temp dirs), `settings_baseline`,
  `plugin_id` (`"dynamic-harness"`), `log`, `session_ok`, `session_reason`
  (`None` = no session gate, for in-process callers).
- `safety.on_pre(evt: dict, ctx: Ctx) -> dict`. Returns `{}` for allow or
  `{"decision":"deny","reason":...}`.
  - When `ctx.session_ok is False`, only the `read` class is allowed.
  - Otherwise the class table's `v1_action` decides: every irreversible
    class is denied, except researcher-lane GETs and queries under an active,
    unexpired grant that pass the query guard.
  - Logs `SAFETY_DENY {tool, cls, reason}` to `ctx.log` when set. A logging
    failure never turns a deny into an allow.
- `session.on_start(evt, ctx) -> dict` (SessionStart). Returns `{}` or
  `{"decision":"block", "state", "reason", ...}`.
  - With a `session_id`, it first writes `sessions/<sid>/status.json` as
    failed, so a crash never leaves an earlier `ok` behind. It also writes
    `settings_baseline.json` (`config_guard.snapshot`) and the written-files
    record (`written.start`). When the checks finish it rewrites
    `status.json` as `{ok, reasons, canary, ts, cwd}`.
  - Refuses, in order:
    1. `bypassPermissions` (`REFUSED`);
    2. Claude Code < 2.1.295 or an unknown version (`REFUSED`;
       `session.claude_version(env=None, run=subprocess.run)` reads
       `CLAUDE_CODE_VERSION`, else parses `claude --version`);
    3. an unknown loop-contract root (`REFUSED`);
    4. loop-contract < 1.1.0, read from `<root>/.claude-plugin/plugin.json`
       (`REFUSED`);
    5. no `config_log.jsonl` (`REFUSED`);
    6. an unreadable tree, a corrupt or empty log, or a hash mismatch on any of
       `control`, `business`, `gate` (`control/gate.json`), `plugin` or
       `loop_contract` (`STOPPED_TAMPER`; a layer present now but never
       recorded is a mismatch too);
    7. a failed PreToolUse canary (`CANARY_FAILED`).
- `config_guard.on_change(evt, ctx) -> dict` (ConfigChange). Judges the
  changed file against `ctx.settings_baseline` (`{realpath: settings}` over
  `~/.claude/settings.json`, `<ws>/.claude/settings.json` and
  `<ws>/.claude/settings.local.json`).
  - No baseline, or an unreadable file, is blocked.
  - Always blocked: `disableAllHooks`; disabling or dropping this plugin;
    `allowManagedHooksOnly` outside policy settings; `bypassPermissions`; and
    any loosening of the backstop (deny rules, sandbox enabled, denyWrite,
    denyRead, allowedDomains, strictAllowlist, failIfUnavailable,
    allowUnsandboxedCommands).
  - During a task (`ctx.job`), any other loosening is blocked too: allow rules,
    ask rules, additional directories, excluded commands, unix sockets,
    permission mode, `disableBypassPermissionsMode`, and local binding.

## Written files (`harness_core/written.py`)

- `safe_id(v, what) -> str` (`[A-Za-z0-9_.-]{1,128}`, not `.`/`..`; else
  `ValueError`), `session_dir(data_root, sid) -> data_root/sessions/<sid>`.
- `start(data_root, sid, workspace, run=subprocess.run)`. Writes
  `tracked.json`, the git-tracked realpaths with their ctime (`/usr/bin/git -c
  core.fsmonitor=false ls-files -z`). Also writes `written.json = []`.
- Every writer holds an `flock` on `sessions/<sid>/written.lock`.
- `start(...)` writes fresh state only for `source == "startup"`: `written.json
  = []`, `started.json` `{ctime_ns}` and `tracked.json`. Compact, resume, clear
  and fork keep the existing files when both baseline files exist; otherwise
  they delete `written.json` and write `tracked.json = {}`.
- `record_bash(evt, ctx) -> None` (PreToolUse writer). Called by
  `safety.on_pre` once a Bash call is allowed, before it runs. Appends
  `pathtrack.bash_write_targets(command, ctx)`; a `None` target deletes the
  record.
- `on_post(evt, ctx) -> dict` (PostToolUse and PostToolUseFailure writer).
  Appends the realpath that Write, Edit, MultiEdit or NotebookEdit wrote to
  `written.json`. A missing record is left missing (already strict). Any
  other failure deletes the record and re-raises, so readers fall back to
  the strict rule.
- `session_sets(data_root, sid, workspace) -> (written, untrusted)`. One
  workspace walk (`.git` skipped). A recorded directory covers every file
  under it. `untrusted` is every file with ctime at or after `started.json`,
  plus every tracked file whose ctime changed. `build_ctx` puts them in
  `ctx.written_this_task` and `ctx.untrusted_files` (PreToolUse only).
- `self_written(data_root, sid, workspace) -> set`. Returns the recorded
  writes plus every file under `.claude/loops/*/{work,requests}`. When the
  record is missing or unreadable, it returns every workspace file except a
  tracked file whose ctime is unchanged since SessionStart (the strict
  fallback; it walks the workspace). mtime is never used.

## Hook entry (`bin/harness-hook`, `harness_core/hookmain.py`)

- `main(event, stdin_json, env, handlers=None, timeouts=None) -> (exit_code, stdout_json)`.
  Never raises.
  - `HANDLERS`: `PreToolUse → safety.on_pre`, `PostToolUse` and
    `PostToolUseFailure → written.on_post`,
    `SessionStart → session.on_start`, `ConfigChange → config_guard.on_change`.
    Only the module for the event is imported. A handler is called as
    `fn(stdin_json, build_ctx(stdin_json, env, event))`. An unknown event
    exits 2.
  - Wraps every handler in `try/except BaseException` and a per-event watchdog
    (`WATCHDOG`): default 4 s, Stop 9 s, fetch 55 s, gate 1795 s.
    `hooks/hooks.json` runs `/usr/bin/python3 -I
    "${CLAUDE_PLUGIN_ROOT}/bin/harness-hook" <Event>` with `timeout: 5` and
    `onFailure: "block"` on every hook (OQ-4 S1). The PostToolUse and
    PostToolUseFailure matcher is `Write|Edit|MultiEdit|NotebookEdit`; Bash
    writes are recorded in PreToolUse instead.
  - Any exception, overrun, non-dict result or non-object input exits 2: deny
    for PreToolUse, block for Stop.
  - A normal deny/block also exits 2 with `harness: <reason>` on stderr
    (spike 1c: blocks, and the stderr reaches the model). `stdout_json` is
    `None` then; a non-empty allow result is printed as JSON with exit 0.
  - Opt-in debug trace: `HARNESS_DEBUG_LOG=<file>` appends one JSON line per
    call `{event, input, code, out, reason}`. `tool_input`/`tool_response`
    values and `transcript_path` are redacted (type and length only). A
    logging failure never changes the decision.
- `build_ctx(evt, env, event=None) -> Ctx`. Fails closed (raises, so exit 2):
  - `CLAUDE_PLUGIN_DATA` or `CLAUDE_PLUGIN_ROOT` is unset, or no workspace is
    known.
  - The workspace is `CLAUDE_PROJECT_DIR`, else the event's `cwd` for
    SessionStart only, else the `cwd` recorded in `status.json`. It is never
    the event's current cwd.
  - The loop-contract root is the latest `loop_contract_root` in
    `config_log.jsonl`, else `LOOP_CONTRACT_ROOT`.
  - `session_ok` is false without a `status.json` that has `ok: true`, a `ts`
    under 24 h old, and a known loop-contract root.
  - With a `session_id`: `ctx.log = EventLog(sessions/<sid>, sessions/<sid>/head)`,
    `settings_baseline` comes from `sessions/<sid>/settings_baseline.json`, and
    `written_this_task` and `untrusted_files` come from
    `written.session_sets` (PreToolUse only).
    The job is `sessions/<sid>.json` `active_task`. For it, the grant is
    `runs/<job>/egress_grant.json`, honoured only when the latest event
    recording it is a `GRANT` with its sha. `owner_tokens` comes from
    `owner_tokens.json` and `task_started` from `tier.json` `started_ts`.
  - Hook state is read with `safe_open`.
- The plugin manifest is `.claude-plugin/plugin.json` (`name: harness`);
  hooks load from `hooks/hooks.json`.

## Sandbox exec (`harness_core/sandbox_exec.py`, `bin/harness-sandbox-exec`)

- `run(argv, cwd, stdin_bytes=None, timeout=60, deny_read=(), deny_write=(), allow_write=(), env=None) -> (code, out: bytes, err: bytes)`.
  - On macOS it uses `/usr/bin/sandbox-exec` with a generated profile
    (`profile(cwd, tmp, deny_read, deny_write, allow_write)`). The profile is
    deny-default, and SBPL's last match wins, so rule order is the policy:
    1. `(deny default)`;
    2. allow process-exec/fork, signals to the same sandbox, sysctl reads,
       all file reads, and ioctl on `/dev/null`, `/dev/fd/*` and `/dev/tty*`;
    3. deny network and Apple Events (with no mach-lookup at all, so
       LaunchServices `open` fails);
    4. allow writes under `cwd`, a fresh temp dir, `/dev/null`, `/dev/zero`,
       `/dev/fd/*`, `/dev/tty*` and `/dev/dtrace`;
    5. deny `deny_write`, then re-allow `allow_write`;
    6. deny writes to `CLAUDE*.md` and `.mcp.json` at any depth;
    7. deny reading and writing `deny_read`.
  - The child env is only `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, `HOME` and
    `TMPDIR` (both the temp dir) and `LANG=en_US.UTF-8`, plus `env`.
  - The child starts in its own session (`start_new_session=True`). Its
    process group is killed with SIGKILL on timeout and again after it exits,
    so background children cannot outlive the call.
  - A timeout returns code `124` (`TIMEOUT_CODE`).
  - When `sandbox-exec` is missing, it raises `HarnessError` (fails closed).
- `bin/harness-sandbox-exec [--cwd DIR] [--deny-read DIR]... [--timeout S] -- ARGV`
  (default timeout 600 s). It refuses (exit 2) in these cases:
  - `CLAUDE_PLUGIN_DATA` or `CLAUDE_PROJECT_DIR` is unset;
  - the cwd is outside the project, or inside `~/.claude`, the data root or the
    plugin root;
  - the cwd is inside a protected workspace path (`.git`, `.claude`,
    `CLAUDE.md`, `CLAUDE.local.md`, `.mcp.json`) other than a job's
    `work/`/`requests/`;
  - any `.claude`, `loops`, job, `work` or `requests` directory is a symlink.

  Every path is resolved once (`_resolve`: realpath, then `lstat` of each
  component), and only resolved paths reach the profile and the child's cwd.
  It denies reads and writes under the whole data root, and reads of every
  installed plugin's `keys/` and `experience/` (`sandbox_exec.secret_dirs`).
  It allows writes to job folders and denies writes to the protected workspace
  paths, `CLAUDE*.md` files, the data root, the plugin root and `~/.claude`.
