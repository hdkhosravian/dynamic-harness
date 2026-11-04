# Getting started

This page takes you from nothing to a first checked task. It takes about five minutes.

## 1. Check the requirements

| You need | Version | Check with |
| --- | --- | --- |
| Claude Code | 2.1.295 or later (hooks must be able to fail closed) | `claude --version` |
| Python | `/usr/bin/python3` 3.9 or later | `/usr/bin/python3 --version` |
| git | `/usr/bin/git` | `/usr/bin/git --version` |
| loop-contract plugin | 1.1.0 or later | installed in step 2 |
| An OS sandbox | macOS (`sandbox-exec`, built in), or Linux with bubblewrap and unprivileged user namespaces allowed | Linux: `bwrap --version` |

On Debian or Ubuntu, install bubblewrap with:

```bash
sudo apt install bubblewrap
```

Everything else is the Python standard library. There is nothing to `pip install`.

## 2. Install the plugins

In Claude Code, type:

```text
/plugin marketplace add hdkhosravian/loop-contract-skill
/plugin install loop-contract@loop-contract
/plugin marketplace add hdkhosravian/dynamic-harness
/plugin install dynamic-harness@dynamic-harness
```

## 3. Set up your folder

Do this once in each folder where you want to use the harness. Until a folder is set up, only quick answers
run there.

1. Open Claude Code in the folder, and accept the folder trust prompt so that the hooks run.
2. Type:

   ```text
   /dynamic-harness:setup
   ```

   The hook shows every change it would make, grouped by file, plus a one-time code. Nothing changes yet,
   and Claude never sees this exchange.
3. Read the plan. To apply it, type:

   ```text
   /dynamic-harness:setup confirm <code>
   ```

   The code works once, in this session, for 10 minutes.
4. Quit and start a new Claude Code session in the folder. The sandbox and the session checks apply from then
   on.

To check the folder later:

```text
/dynamic-harness:setup status
```

To reverse setup's own changes:

```text
/dynamic-harness:setup undo
```

Undo does not touch a value you changed after setup, and it keeps every other edit. The undo plan lists both.

### What setup changes

- **`~/.claude/settings.json`**: the key `sandbox.network.strictAllowlist`. It acts only in projects that turn
  the sandbox on: there, commands reach only the domains that project allows, with no prompt to add one.
  Some lists apply to every project: allowed network domains, sandbox-exempt commands, socket allowances and
  custom proxy ports. If your user settings set any of these, setup removes them there too, and other
  projects lose them. The plan says so before you confirm.
- **This folder's `.claude/settings.local.json`**:
  - the sandbox is on, and commands do not run if it cannot start;
  - no command may run outside the sandbox, and commands get no network access;
  - commands cannot read the harness's private data, or change Claude's settings, the plugins or any
    `CLAUDE.md`;
  - permission-bypass mode is off.
- **The harness's data folder**: a 32-byte signing key (`keys/hmac.key`, mode 0600) and a baseline of the
  release hashes in `config_log.jsonl`.

Other projects keep their own settings. Setup backs up every file it changes, never overwrites invalid JSON,
and refuses to run in your home directory.

## 4. Tell it about your business (optional)

If you work with money, set your currency and calendar. Each change asks for a code, like setup.

```text
/dynamic-harness:business set currency IRT
/dynamic-harness:business set calendar jalali
/dynamic-harness:business set language fa
```

See the [owner guide](owner-guide.md#your-commands) for every field.

## 5. Try it

**A quick question.** Ask something that needs no data, for example:

```text
What is the difference between gross margin and net margin?
```

You get a direct answer. The harness does not slow it down.

**A checked task.** Put a sales export (CSV) in the folder and ask:

```text
What were our total sales in March, from sales.csv?
```

What happens:

1. The probe sees a computed number, so the task needs a card. Claude drafts it and compiles it.
2. Claude prints the opening line, for example "I'll prepare: March sales. I'll check it before I call it
   done."
3. The first time, you are asked to confirm what "sales" means, in one sentence.
4. Claude runs `finance.reconcile` through `harness-run`. The check computes the total, and a second path
   cross-checks it.
5. Claude writes the report, puts the three-line answer header on top, and requests the gate.
6. The gate signs a receipt, and you see the state line: **Checked**, **Done, not fully checked: \<what\>**,
   or **Partly done: N of M**.

The deliverable is in `.claude/loops/<job>/work/`, with the rows behind the figure in `rows-<unit>.xlsx`.

To see your tasks:

```text
/dynamic-harness:status
```

## If something does not work

| Symptom | Cause | Fix |
| --- | --- | --- |
| "The dynamic-harness hooks are not active in this session" | The plugin is disabled, or you did not accept the folder trust prompt. | Enable the plugin, accept the trust prompt, start a new session. |
| Only quick answers run; tasks are refused | The folder is not set up, or setup is incomplete. | Run `/dynamic-harness:setup status`, then `/dynamic-harness:setup`. |
| The session is refused at start, quoting bwrap | Linux: bubblewrap is missing, or unprivileged user namespaces are blocked. | Install bubblewrap, and allow unprivileged user namespaces. |
| A task stopped as `STOPPED_TAMPER` | A protected record or a release hash changed outside the harness. | `/dynamic-harness:status --repair <job>` closes the task. Re-run setup if a release hash changed. |
| Setup names your managed settings | An administrator's settings block a required value. | Ask the administrator. Managed deployments must install the plugin in managed `enabledPlugins`. |

More answers are in the [FAQ](faq.md).
