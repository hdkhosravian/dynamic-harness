# dynamic-harness

A Claude Code plugin that builds a per-task harness on top of loop-contract.

- **Design:** `dynamic_harness_design_spec.md` (normative). The review trail is
  in `egm-harness-review/`.
- **Status:** build phases P1–P2. This covers the core event log, ledger,
  gate adapter and receipts, plus the safety hook, irreversible classes,
  egress, query guard, protected paths, sandbox exec and the bypass corpus.
- **Tests:** run `python3 -m pytest -q`.
- **Platform spike (OQ-4):** see `spike/`.
