# Security policy

dynamic-harness is a safety layer, so security reports are taken seriously.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability. Use GitHub's private vulnerability reporting
(**Security** tab, **Report a vulnerability**) on this repository. Include the version, your platform, the
steps to reproduce, and what an attacker gains.

## In scope

- A way for the model to write, forge or change a trusted record: a ledger row, a check output, a receipt, the
  business layer, user memory or the control layer.
- A way to reach "Checked" or "done" without the checks the state line claims.
- A sandbox escape from a pack check, the gate or `harness-sandbox-exec`.
- A way past the egress controls, the research grant or the query guard.
- A hook that fails open (allows a call after an error or a timeout).

The trust model and the known limits are described in [docs/security.md](docs/security.md).
