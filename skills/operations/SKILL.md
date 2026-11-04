---
name: operations
description: Operations pack for dynamic-harness documents (proposals, contract drafts, customer replies) and decision support (vendor, pricing, make or buy). Use when a task card's oracle is doc.check or decide.score_flip.
---

# Operations pack

v1 prepares work only. Nothing is sent, published, signed or paid; the report says "I've prepared this. Nothing
was sent or changed."

## Documents: `doc.check`

`harness-run doc.check --unit <id> --attempt <n> --arg document=proposal.md --arg 'required=["price", "delivery date"]'`

- Every required item the owner named must appear; ask for the must-include terms at the start.
- No placeholders left (`[name]`, `{{date}}`, `XXX`, `____`).
- The footer from the business profile closes the document.
- Nobody on the do-not-contact list is addressed.
- Every figure must come from a recorded check or the owner's own words; the gate traces them.

## Decision support: `decide.score_flip`

`harness-run decide.score_flip --unit <id> --attempt <n> --args decision.json`, where `decision.json` holds
`criteria` (`[{id, weight, source}]`, `source: owner` when the owner gave the weight), `options`
(`[{id, scores: {criterion: number}}]`) and `constraints` (`[{criterion, min|max}]`).

The criteria and weights are always the owner's. The report gives the recommendation, the weights, and what
would change it: the flip test moves each weight by 20% and names every move that changes the winner.
