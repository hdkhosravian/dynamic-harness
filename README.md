# dynamic-harness

A Claude Code plugin that builds a per-task harness on top of loop-contract.

- **Design:** `dynamic_harness_design_spec.md` (normative). The review trail is
  in `egm-harness-review/`.
- **Status:** build phases P1–P2 and the P5 setup step. This covers the core
  event log, ledger, gate adapter and receipts, plus the safety hook,
  irreversible classes, egress, query guard, protected paths, the sandbox
  launcher, the bypass corpus and `/dynamic-harness:setup`.
- **Platforms:** macOS (`sandbox-exec`) and Linux (`bubblewrap`). Ubuntu 22.04
  and 24.04 are tested in containers.
- **Tests:** run `python3 -m pytest -q`.
- **Platform spike (OQ-4 and P5):** see `spike/`.

## Requirements

- Claude Code 2.1.295 or later. Hooks must be able to fail closed.
- `/usr/bin/python3` 3.9 or later, and `/usr/bin/git`.
- The loop-contract plugin, version 1.1.0 or later.
- On Linux: `apt install bubblewrap`, and unprivileged user namespaces must be
  allowed. If they are not, the session start check says so.

## Set up a folder

1. Install and enable the plugin. Open Claude Code in the project folder and
   accept the folder trust prompt, so that the hooks run.
2. Type `/dynamic-harness:setup`. The hook shows every change it would make,
   grouped by file, plus a one-time code. Nothing changes yet, and Claude
   never sees this exchange.
3. To apply, type `/dynamic-harness:setup confirm <code>`. The code works once,
   in this session, for 10 minutes.
4. Start a new Claude Code session in the folder. The sandbox and the session
   checks apply from then on.

Use `/dynamic-harness:setup status` to check the folder, and
`/dynamic-harness:setup undo` to restore the files from before setup.

### What setup changes

- `~/.claude/settings.json`: one key only, `sandbox.network.strictAllowlist`.
  It acts only in projects that turn the sandbox on. In those projects,
  commands reach only the domains that project allows, with no prompt to add
  one.
- This folder's `.claude/settings.local.json`:
  - the sandbox is on, and commands do not run if it cannot start;
  - no command may run outside the sandbox, and commands get no network access;
  - commands cannot read the harness's private data, or change Claude's
    settings, the plugins or any `CLAUDE.md`;
  - permission-bypass mode is off.

Other projects keep their own settings.
