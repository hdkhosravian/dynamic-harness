# Live end-to-end runs

These tests start the real `claude` binary, once per pack (`finance`, `model`, `operations`, `research`). For
each pack a test makes a temporary folder with a sample file, sets it up with `/dynamic-harness:setup` (the
folder-only confirmation, never the global one), sends one request, answers the harness's questions with a
fixed "yes", and then checks the task's receipt with the harness's own `gate.verify_receipt`. A pack passes on
"Checked" or on "Done, not fully checked: ..." with a receipt that verifies. It fails on any stop state, a
timeout, or a missing receipt. Every outcome prints one line, `E2E_RESULT pack=... state=...`; the state is
the receipt's state, or a word such as `TIMEOUT_RUN`, `CLAUDE_EXIT`, `SETUP_ERROR` or `NO_RECEIPT(...)`.

These tests have not been run against a live model yet.

## Cost

Each pack makes up to 10 `claude` calls (2 setup calls and up to 8 request calls, `SETUP_CALLS` and `MAX_TURNS`
in `driver.py`), each with `--max-budget-usd 2`: at most 20 USD per pack, 80 USD for all four. The cap is soft
per call (a call may overshoot a little), and there is no cap across packs. Each call times out after 15
minutes. The real cost is not known until the first run. Run one pack first, for example with `-k finance`.

## Run

You need: Claude Code on `PATH`, `ANTHROPIC_API_KEY` set (the child gets a temporary home and Claude config
folder, so a saved login is not used), and a loop-contract 1.2.0 or later checkout or plugin folder.

```bash
HARNESS_E2E=1 HARNESS_E2E_LOOP_CONTRACT=/path/to/loop-contract python3 -m pytest tests/e2e -s -v
```

- `HARNESS_E2E=1` turns the tests on. Only the exact value `1` does; anything else skips all four.
- `HARNESS_E2E_LOOP_CONTRACT` is the loop-contract folder. Without a usable one the tests fail with the reason.

The driver sets `HOME` and `CLAUDE_CONFIG_DIR` to folders inside the test's temporary directory, which should
put the harness's data folder there too. That is an assumption about the binary. After the setup plan call (the
hook blocks it, so it costs nothing) the driver checks that the harness's plan file for that session is under the
temporary directory, and stops with an error before the confirm if it is not. The `research` pack needs network
access and the plan approval, which the fixed "yes" gives.

## Safety

Tools allowed without a prompt (nobody can answer one): `finance`, `model` and `operations` get `Bash`, `Read`
and `Write`. `research` gets those plus `WebSearch` and `Agent` (it starts the researcher and verifier
subagents). No pack gets a fetch tool: pages are fetched by `harness-claims fetch`, which runs through `Bash`.
`Bash` is plain `Bash`, not a command pattern: the pack files could not prove a pattern set covers every
command the model runs, and a wrong set would make every run fail. So in these runs the only checks on `Bash`
are the harness hooks and the sandbox settings that setup writes, and those are the thing under test. Make the
first live run in a disposable environment: a container, a virtual machine, or a separate user account.
