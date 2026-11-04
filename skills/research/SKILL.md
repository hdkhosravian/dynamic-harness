---
name: research
description: Research pack for dynamic-harness investigations (market, competitor, supplier, regulation). Use when a task card's oracle is research.check or research.report.
---

# Research pack

Page text never reaches you. The web-researcher subagent fetches pages with `harness-claims fetch`, and returns
claim rows only; the SubagentStop hook keeps a row only when its quote is on the page the hook stored.

## Steps

1. Propose the research plan with `harness-contact plan --plan <file>` (hypotheses, searches, URLs, page budget,
   stop rule). Wait for the owner's reply, then `harness-contact plan --accept latest`.
2. `harness-research render`, then spawn `dynamic-harness:web-researcher` with exactly the rendered prompt.
3. For each hypothesis (unit), cite the claim rows that decide it:
   `harness-claims cite --unit <id> --claims 3,5 --load-bearing 3,5`. Load-bearing claims need two independent
   sources: two copies of one article count once.
4. `harness-critic render <unit>`, then spawn `dynamic-harness:verifier` with the rendered prompt.
5. `harness-run research.check --unit <id> --attempt <n>`. Record SUPPORTED or REFUTED citing its output, or
   INCONCLUSIVE with what would decide it. "Unknown" is a valid verdict.
6. Write `work/report.md`: the answer first, then a `## Findings` section where every line cites `[claim N]` or
   says "model knowledge, not checked". Run `harness-run research.report` on it before the gate.

Root-cause claims about the owner's own data say "associated with", never "caused". Keep quotes in their
original language and mark translations.
