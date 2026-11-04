# Contributing

Thank you for helping improve dynamic-harness.

## Before you start

- Read [how it works](docs/how-it-works.md) and [security](docs/security.md). The project's rule is simple: hooks
  are the only trusted writers, and every check fails closed. A change that lets the model write its own record,
  or turns an error into a pass, will not be merged.
- For a new domain pack, follow [development](docs/development.md#adding-a-pack).

## Making a change

1. Fork the repository and create a branch.
2. Write a failing test first, then the change. Tests live in `tests/`; run them with:

   ```bash
   python3 -m pytest -q
   ```

   The gate integration tests use a `loop-contract-skill` checkout next to this repository, or the path in
   `HARNESS_FOLD_LEDGER`.
3. Keep to the standard library. The hooks run under `/usr/bin/python3 -I`.
4. If you change a shipped skill or a pack check entry, run `python3 tools/release_hashes.py "<label>"` so the
   manifests and the approved-procedure hashes match.
5. If you change user-facing text, edit both `templates/en.json` and `templates/fa.json`, and run
   `bin/harness-lint-user-text`.
6. Open a pull request that says what changed and why, and how you tested it.

## Reporting a bug

Open an issue with the Claude Code version, your platform, what you expected and what happened. Leave out
personal data; the harness's decision records never hold it, and issues should not either.
