# OQ-4 platform-behaviour spike: results

- **Claude Code:** 2.1.296, macOS (Darwin 25.1), `claude -p --model haiku` (resolved to `claude-haiku-5-5`).
- **Date:** 2026-11-04.
- **Isolation:** the probe plugin loads with `--plugin-dir spike/probe-plugin`. Runs also pass `--setting-sources project --strict-mcp-config` and use `cwd = spike/work`, so user settings, plugins and MCP servers are not loaded. Each run uses `--permission-mode bypassPermissions`, so that the hook decision is the only thing gating a tool call.
- **Re-run:** `spike/run_spike.sh [run ...]`. With no arguments it runs everything except the roughly 11-minute `r5_long`. `spike/summarize.py` prints the evidence digest.
- **Evidence:** each run writes to `spike/logs/<run>/`:
  - `<hook-label>.jsonl` holds the hook stdin JSON, plus argv and env;
  - `stream.jsonl` holds the CLI stream-json, with `--include-hook-events`;
  - `files_created.txt` or `denied_dir_contents.txt` hold the ground truth.
- **Cost:** 11 `claude -p` runs (r8 was run twice), about $0.03 in total.

**EMPIRICAL** means the behaviour was observed in this spike. **DOCS** means it was read from code.claude.com/docs (hooks, sandboxing, scheduled-tasks) and was not exercised here. Where the two overlap, the empirical result agrees with the docs unless noted.

## Support matrix

| # | Item | Result | Basis | Evidence | Design implication | Spec change? |
|---|---|---|---|---|---|---|
| 1a | PreToolUse `exit 0` | Tool runs. | EMPIRICAL | `logs/r1_exitcodes`: `r1_plain.txt` was created | none | no |
| 1b | PreToolUse `exit 1` (crash) | **Does not block.** The tool runs, and the hook error is shown as a non-blocking error. | EMPIRICAL | `r1_exit1.txt` was created; `stream.jsonl` shows `hook_response exit_code 1 outcome error` | A crashing safety hook **fails open** by default. | **yes** (S1) |
| 1c | PreToolUse `exit 2` + stderr | Blocks. The stderr becomes the tool error the model sees. | EMPIRICAL | `r1_exit2.txt` is absent; the tool_result is `PreToolUse:Bash hook error: ... probe: blocked by exit 2` | Usable as the deny path. | no |
| 1d | JSON `hookSpecificOutput.permissionDecision: "deny"` | Blocks. `permissionDecisionReason` goes to the model. | EMPIRICAL | `r1_deny.txt` is absent; the tool_result is `probe: JSON deny` | Preferred deny path (structured). If hooks disagree, the precedence is `deny > defer > ask > allow` (DOCS). | no |
| 1e | `onFailure: "block"` on the hook entry | A crash (exit 1) **blocks**: "failed; blocking because onFailure is "block"". | EMPIRICAL | `r1_ofexit1.txt` is absent | This is the platform fail-closed switch (needs v2.1.295+). | **yes** (S1) |
| 2a | PreToolUse hook exceeds its `timeout` (3 s, hook sleeps 8 s) | The hook is cancelled and **the tool runs** (fails open). | EMPIRICAL | `r1_sleep.txt` was created; `hook_response ... cancelled` | A slow safety hook allows the call. | **yes** (S1) |
| 2b | Same timeout with `onFailure: "block"` | **Blocks**: "timed out; blocking because onFailure is "block"". | EMPIRICAL | `r1_ofsleep.txt` is absent | Use this for every safety and lane hook. | **yes** (S1) |
| 2c | `timeout: 1800` on one hook | The config is accepted and the hook loads and runs in every run. Default timeouts are 600 s for command hooks, 30 s for UserPromptSubmit, and 10 s for MessageDisplay. The docs state no maximum. | EMPIRICAL (accepted) + DOCS | `post_long.jsonl` in every run | See 2d. | see 2d |
| 2d | A hook with `timeout: 1800` that runs 630 s (past the 600 s default) | **Honoured.** The hook slept 630 s, finished with `outcome success`, and its completion was logged at t+634 s. The tool result then returned and the session ended normally (`duration_ms` 635182). The 600 s default did not cut it off. | EMPIRICAL | `logs/r5_long/post_long.jsonl` | A gate hook with a long timeout works. Up to 630 s was proven. 1800 s itself was not run to expiry, and no platform maximum was hit. While the hook runs, the turn is blocked. | minor (S2) |
| 3a | Stop `decision: "block"` | Works. The `reason` is fed back to the model and the turn continues. | EMPIRICAL | `logs/r3_stopcap`: 10 turns for a "say hi" prompt | none | no |
| 3b | `stop_hook_active` | Present. It is `false` on the first Stop and `true` on every continuation. Stop also receives `last_assistant_message`, `background_tasks` and `session_crons`. | EMPIRICAL | `r3_stopcap/stop.jsonl` = `[F,T,T,T,T,T,T,T,T]` | The gate can detect loops. | no |
| 3c | Continuation cap | **8 consecutive blocks**, then the 9th block is overridden and the turn ends (`subtype=success`). `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP=3` gave exactly 3. The docs say the count resets whenever Claude calls a tool. | EMPIRICAL (+ DOCS for the reset rule) | `r3_stopcap`: 9 Stop calls, counter = 9; `r4_stopcap_env`: 4 Stop calls | A gate that only blocks Stop cannot hold a no-tool loop for more than 8 turns. The session then ends looking "successful". | **yes** (S3) |
| 4 | PostToolUse `tool_response` | Present. For Bash it is `{stdout, stderr, interrupted, isImage, noOutputExpected}`. For an MCP tool it is a content-block list. For Agent it is `{isAsync, status, agentId, resolvedModel, prompt, outputFile...}`. The input also carries `tool_use_id` and `duration_ms`. | EMPIRICAL | `r1_exitcodes/post.jsonl`, `r7_sandbox/post.jsonl`, `r2_subagent/post.jsonl` | The writers and tripwire can read results. | no |
| 5a | PreToolUse/PostToolUse inside a subagent | **Fire**, with `agent_id` and `agent_type` (`probe-plugin:probe-agent`). On main-thread calls both keys are **absent** (not null). | EMPIRICAL | `r2_subagent/pre.jsonl`: Bash with `agent_id=a5cf...`; the Agent call has none | Lane rules keyed on `agent_type` are feasible. "Main thread" means the key is absent. | no (note) |
| 5b | SubagentStart / SubagentStop | Both fire. SubagentStop carries `agent_id`, `agent_type`, `agent_transcript_path`, `last_assistant_message`, `stop_hook_active` and `background_tasks`. It does **not** carry the spawn prompt. The prompt appears only in PreToolUse(Agent) `tool_input.prompt`. `agentId` first appears in PostToolUse(Agent) `tool_response`, joined by `tool_use_id`. | EMPIRICAL | `r2_subagent/sas_exact.jsonl`, `subagent_start.jsonl`, `post.jsonl` | `prompt_sha` must be recorded at PreToolUse(Agent) and joined to the agent via the PostToolUse(Agent) `agentId`. | **yes** (S5) |
| 5c | SubagentStop matcher on agent type | `^probe-plugin:probe-agent$` **matched**, and matcher-less **matched**. Unanchored `probe-agent` did **not** match, and `zzz-nomatch` did not match. Plugin agents are namespaced `<plugin>:<agent>`. | EMPIRICAL | `sas_exact` and `sas_all` have 1 entry each; `sas_substr` and `sas_nomatch` have none | Matchers must use the full namespaced name, e.g. `^harness:web-researcher$`. | **yes** (S5) |
| 5d | Subagents are **async by default** | Agent returns `status: async_launched`. The parent turn ends, and Stop fires with `background_tasks=[{...running}]`. The completion arrives as a **new UserPromptSubmit** whose prompt is `<task-notification>...`. In `-p` mode this produced 2 `result` messages. | EMPIRICAL | `r2_subagent/user_prompt_submit.jsonl` (2 entries); `stop.jsonl` | The Stop gate can run while verifiers or researchers are still running. UserPromptSubmit sees synthetic prompts that are not from the owner. | **yes** (S4) |
| 6 | UserPromptSubmit input | `session_id`, `transcript_path`, `cwd`, `prompt_id`, `permission_mode`, `hook_event_name`, `prompt`. The docs add `session_title` when one is set. `prompt_id` is identical on every hook in that turn. | EMPIRICAL | `r1_exitcodes/user_prompt_submit.jsonl` | `turn_id` can be `prompt_id`. | **yes** (S6, naming) |
| 7 | `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_DATA}` | Both are expanded in hooks.json `command` strings and exported as env vars to the hook process, along with `CLAUDE_PROJECT_DIR`. With `--plugin-dir`, DATA is `~/.claude/plugins/data/probe-plugin-inline`, and it was auto-created. Parent env vars (e.g. `PROBE_RUN`) are inherited. | EMPIRICAL | `session_start.jsonl` argv[2] and env | The data dir's name depends on how the plugin is installed (`-inline` suffix for `--plugin-dir`), so don't hard-code it. | no (note) |
| 8 | AskUserQuestion in `-p` | **Not available.** It is absent from the tool list and from the deferred tools (`r6` model reply: NO_ASKUSER). The docs say that in `-p` it is offered only with a permission host (SDK `canUseTool`). Alternatives are a PreToolUse hook that returns `allow` plus `updatedInput.answers`, or `permissionDecision: "defer"`, which ends the turn with `stop_reason: tool_deferred` and resumes via `claude -p --resume`. In interactive mode it returns inside the turn (DOCS). | EMPIRICAL (`-p`) + DOCS | `logs/r6_askuser/stream.jsonl` | The assumption "AskUserQuestion returns inside the turn" holds only on interactive surfaces. | **yes** (S7) |
| 9a | Sandbox key names | Confirmed working: `sandbox.enabled`, `sandbox.failIfUnavailable`, `sandbox.allowUnsandboxedCommands`, `sandbox.network.allowedDomains`, `sandbox.network.strictAllowlist`, `sandbox.filesystem.denyWrite`, `sandbox.filesystem.denyRead`. Also documented: `filesystem.allowWrite`/`allowRead`, `network.deniedDomains`, `excludedCommands`, `autoAllowBashIfSandboxed`, `credentials`. | EMPIRICAL (listed ones) + DOCS | `logs/r7_sandbox/settings.json`, `r10_netstrict/settings.json` | Use these names. Note `filesystem.` and `network.` nesting. | **yes** if the spec uses flat names (S8) |
| 9b | `allowUnsandboxedCommands: false` | `dangerouslyDisableSandbox: true` was **ignored**, so the write stayed denied. | EMPIRICAL | `r7` step 5: `operation not permitted: denied/escape.txt` | Works as assumed. | no |
| 9c | denyRead / denyWrite for Bash | Enforced (`Operation not permitted`). | EMPIRICAL | `r7` steps 1–2 | none | no |
| 9d | `allowedDomains` in `bypassPermissions` | **Not enforced.** `example.org` returned 200. In bypass mode an out-of-list host is allowed without a prompt (DOCS). With `strictAllowlist: true` it was refused (`deny network-outbound example.org:443`). `strictAllowlist` is honoured only in user, managed or `--settings` scope, not project settings (DOCS). | EMPIRICAL | `r7` step 4 = 200; `r10_netstrict` step 2 = exit 56 + sandbox_violations | Network containment needs `strictAllowlist`, and it cannot ship in a project or plugin. | **yes** (S8) |
| 9e | Sandbox coverage of MCP stdio servers and hooks | **Not covered.** The MCP server wrote into `denyWrite` and read `denyRead` (`WRITE_OK; READ_OK TOPSECRET`). A SessionStart hook also wrote into the denied dir. PreToolUse **does** fire for MCP tool calls (`mcp__probe__probe_fs`). | EMPIRICAL + DOCS ("hooks, local MCP servers ... run with your full access") | `r7_sandbox/denied_dir_contents.txt` = `hook.txt mcp.txt` | MCP servers are a sandbox escape. Containment must be the PreToolUse connector-registry gate plus the trust in each server. | **yes** (S8) |
| 10a | `allowManagedHooksOnly` | From `--settings` it is **ignored**: plugin hooks still ran. In managed settings it blocks user, project, local **and plugin** hooks, except for plugins force-enabled in managed `enabledPlugins` (DOCS). | EMPIRICAL (non-managed) + DOCS | `logs/r9_managedonly/user_prompt_submit.jsonl` present | In a managed org, the harness's safety hooks silently disappear unless the plugin is force-enabled by policy. | **yes** (S9) |
| 10b | ConfigChange | Exists and fires on a mid-session edit of `.claude/settings.json`, with input `{source: "project_settings", file_path}`. Exit 2 or a JSON `decision: block` blocks the change silently (debug log only). `policy_settings` cannot be blocked (DOCS). | EMPIRICAL (fires) + DOCS (block effect) | `r8_configchange/config_change.jsonl`; also seen in the concurrent `r5_long` session | Feasible for `disableAllHooks` and permission loosening during a task. It cannot see launch-time changes (`--bare`, `--setting-sources`, `--settings`, starting without the plugin). | **yes** (S9) |
| 11 | Platform scheduler | `CronCreate`, `CronList`, `CronDelete`, `ScheduleWakeup` and `RemoteTrigger` are in the `-p` tool list. Per the docs, Cron tasks are **session-scoped**: they fire only while that session runs and is idle, have 1-minute granularity, expire after 7 days, and have no catch-up. Cloud routines run on a fresh clone with **no local files** (minimum 1 h). Desktop scheduled tasks are local but run a model prompt. None runs a local deterministic script with no model. | EMPIRICAL (tool presence) + DOCS | `r1_exitcodes/stream.jsonl` init `tools` | "Deterministic script, no model at run time" needs an OS scheduler (launchd/cron), or Desktop tasks for judgement runs. | **yes** (S10) |

## Spec changes needed

- **S1. Fail-closed is not the default.** Exit 1 and hook timeouts both allow the call (1b, 2a). Changes:
  - Every safety, lane and contacts PreToolUse hook must set `"onFailure": "block"`, which needs Claude Code ≥ 2.1.295. Make that version a SessionStart check and a support-matrix row.
  - Set an explicit `timeout`, e.g. 5 s, slightly above the 4 s module deadline.
  - Deny with JSON `permissionDecision: "deny"` or exit 2, never exit 1.
  - The spec's "4 s deadline returns deny" is necessary but not sufficient on its own.
- **S2. 1800 s gate hook.** It is accepted, and it runs past the 600 s default (630 s observed). Keep the "profile `timeout` ≤ 1790 s" rule. Changes:
  - Also set `onFailure: "block"` on the gate hook, so that a gate timeout cannot pass silently.
  - Note that the default timeout is 600 s, not 60 s, and that UserPromptSubmit defaults to 30 s. The UserPromptSubmit hook does lease takeover plus `fold.project()` inside the hook, so it needs an explicit `timeout` and its own fail mode.
  - Expiry at exactly 1800 s was not exercised.
- **S3. Stop continuation cap = 8**, overridable with `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`; the count resets on any tool call. Changes:
  - The Stop gate must count consecutive `stop_hook_active=true` calls.
  - On the call that would exceed the cap, it should write a terminal `STOPPED_GATE`/`PAUSED` record. The platform will end the turn anyway, and the ledger must not read as success.
  - Spec §Gate and Stop semantics should state the cap.
- **S4. Subagents are async by default.** Changes:
  - The Stop gate must treat non-empty `background_tasks` (subagent running) as "not done". Either block with a reason or record PAUSED.
  - UserPromptSubmit must classify prompts that start with `<task-notification>` as system turns. They must not be owner prompts for probe/pre-filter/`prompt_scope`/contacts or the R-7/R-8 counters.
  - Researcher and verifier spawns may need to be forced foreground, if the tool exposes that.
- **S5. SubagentStop record.** `prompt_sha` is not in SubagentStop. Record it at PreToolUse(Agent) (`tool_input.prompt`, keyed by `tool_use_id`), then bind it to `agent_id` from the PostToolUse(Agent) `tool_response.agentId`. Write all agent-type matchers as anchored, plugin-namespaced names (`^harness:web-researcher$`). The spawn tool is named `Agent` in hook input (`Task` in the init tool list), so match both.
- **S6.** Use `prompt_id` (present on every hook in a turn) as `turn_id`. Treat a missing `agent_type` key as "main thread".
- **S7. AskUserQuestion.** The "returns inside the turn" assumption holds only on interactive CLI/Desktop. On `-p`/headless:
  - The tool is absent.
  - Blocking contacts must go through `harness-contact` with `permissionDecision: "defer"` + `--resume`, or through an SDK `canUseTool` host.
  - The contacts hook must count both paths.
  - Add a per-surface row to the support matrix.
- **S8. Sandbox.**
  - Use the nested key names (`sandbox.filesystem.denyWrite/denyRead`, `sandbox.network.allowedDomains`).
  - Require `sandbox.network.strictAllowlist: true` and `failIfUnavailable: true`. Without `strictAllowlist`, `allowedDomains` is not enforced in `bypassPermissions` (and the spec already refuses bypass). Both must live in user, managed or `--settings` scope, because a plugin or project cannot set them. SessionStart should verify them.
  - State explicitly that MCP stdio servers and hooks are **outside** the sandbox. The connector-registry PreToolUse deny is the only control on MCP, and registry admission must assume full user access.
- **S9. Hook liveness.**
  - `allowManagedHooksOnly` (managed) silently disables plugin hooks unless the plugin is force-enabled in managed `enabledPlugins`. Document that as a deployment requirement.
  - ConfigChange catches mid-session edits only. Launch-time bypasses (`--bare`, `--setting-sources`, no plugin) produce no hook at all.
  - The "missing capability → refuse above T0" fallback therefore needs a positive liveness signal. For example, the gate and `harness-compile` refuse unless the SessionStart/UserPromptSubmit records for this `session_id` exist.
- **S10. Scheduler.** Replace "Claude Code routines / cron where available" as follows:
  - Deterministic automations use an OS scheduler (launchd/cron) that calls the compiled script.
  - Judgement runs use Desktop scheduled tasks (local) or cloud routines (no local files; minimum 1 h).
  - Session `CronCreate` is session-bound with a 7-day expiry, so it is not durable.

## Side effects

The CLI auto-created `~/.claude/plugins/data/probe-plugin-inline/`, which is empty and is the `CLAUDE_PLUGIN_DATA` for the `--plugin-dir` plugin. It is safe to delete. No user settings, plugins or CLAUDE.md were modified.

`logs/r5_long.console.txt` ends with `d: unbound variable`. That error came from editing `run_spike.sh` while the background r5 run was still executing it (bash reads scripts incrementally). It is not a probe result. The r5 session itself completed with exit 0, and the current script passes `bash -n`.

## P5 setup spike (2026-11-04, Claude Code 2.1.296, macOS, `claude -p --model haiku`)

Re-run with `spike/run_p5_spike.sh`. Each item is EMPIRICAL unless marked.

| # | Item | Result | Design implication |
|---|---|---|---|
| 11 | UserPromptSubmit and a typed plugin command `/probe2:setup confirm 1234` | The hook's `prompt` is the raw typed line. JSON `{"decision":"block","reason":…}` gives 0 turns and $0. The result text is "UserPromptSubmit operation blocked by hook: <reason> … Original prompt: …". DOCS agree: the line comes before expansion, the reason is shown to the user and not added to context. | Setup can run entirely in the hook, without the model. |
| 12a | Nested `sandbox-exec` inside Claude Code's sandbox | `sandbox-exec: sandbox_apply: Operation not permitted`, exit 71. A nested profile also fails under a plain restrictive outer `sandbox-exec`. | `harness-sandbox-exec` needs an outer mode. |
| 12b | Claude Code's sandbox from inside | `denyRead` file gives EPERM; a direct TCP connect gives EPERM. Env has `SANDBOX_RUNTIME=1` (forgeable) and `HTTP(S)_PROXY` with credentials. | Check properties from the inside, not env markers. Drop the proxy variables from the child env. |
| 12c | LaunchServices and Apple Events inside Claude Code's sandbox | `bootstrap_look_up(launchservicesd)` = 0 (allowed); pasteboard and appleevents = 1100 (denied); `open -a TextEdit` failed; `osascript -e 'return 1'` exit 0; `pbcopy` exit 1. | Claude Code's sandbox is close to the harness profile but not identical. The classifier still denies `open`/`osascript`. |
| 13 | Bash tool environment | No `CLAUDE_PROJECT_DIR` and no `CLAUDE_PLUGIN_*`. | The launcher's outer path must not need them. |
| 14 | Plugin loaded with `--plugin-dir` | An ordinary prompt reaches the model (1 turn). `/dynamic-harness:setup` and `status` give 0 turns and $0. The real `~/.claude/settings.json` is unchanged, byte for byte. | Pre-filtering other prompts works; the plan step writes nothing. |
| 15 | The generated settings via `--settings` | No settings error; `ls <data root>` gives "Operation not permitted"; workspace writes work. | The keys setup writes are accepted and enforced. |
| 16 | `harness-sandbox-exec` in outer mode, with no `CLAUDE_*` env | `42`, exit 0. The sandbox proxy refused the probe's CONNECT ("deny network-outbound example.com:443 (host is not on the allow list)"). `--cwd .git` refused, exit 2. | Outer mode works and keeps the workspace checks. |

| 17 | Outer mode with the stricter checks: missing `CLAUDE.md`/`.mcp.json`/`.git/hooks` must be uncreatable, a proxy must exist and refuse | `42`, exit 0, with one refused CONNECT. A child writing `sub/CLAUDE.md` got `PermissionError: [Errno 1] Operation not permitted` from Claude Code's sandbox, and nothing was left behind. | setup's `**/CLAUDE.md` denyWrite holds; outer mode's preconditions are met in a set-up folder. |

Not tested: Claude Code's sandbox on Linux, and an interactive (non `-p`) session.
