# Live end-to-end runs

These tests start the real `claude` binary, once per pack (`finance`, `model`, `operations`, `research`). For
each pack a test makes a temporary folder with a sample file, sets it up with `/dynamic-harness:setup` (the
folder-only confirmation, never the global one), sends one request, answers the harness's questions with a
fixed "yes", and then checks the task's receipt with the harness's own `gate.verify_receipt`. A pack passes on
"Checked" or on "Done, not fully checked: ..." with a receipt that verifies. It fails on any stop state, a
timeout, or a missing receipt. The final state is printed as `E2E_RESULT pack=... state=...`.

The `claude` calls are capped at 8 per pack, 15 minutes and 2 USD each (constants in `driver.py`). The real
cost is not known until the first run.

These tests have not been run against a live model yet.

You need: Claude Code on `PATH`, `ANTHROPIC_API_KEY` set (the child gets a temporary home and Claude config
folder, so a saved login is not used), and a loop-contract 1.2.0 or later checkout or plugin folder.

```bash
HARNESS_E2E=1 HARNESS_E2E_LOOP_CONTRACT=/path/to/loop-contract python3 -m pytest tests/e2e -s -v
```

- `HARNESS_E2E=1` turns the tests on. Without it all four are skipped.
- `HARNESS_E2E_LOOP_CONTRACT` is the loop-contract folder. Without a usable one the tests fail with the reason.

Everything the child writes goes into the test's temporary directory, including the harness's data folder.
The `research` pack needs network access and the plan approval, which the fixed "yes" gives.
