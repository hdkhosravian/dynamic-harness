"""``research.apply`` and ``critic.apply``: the renders that lanes require before a researcher or verifier
spawn (spec §Hooks "PreToolUse lanes"; build phase P3).

- ``harness-research render``: needs an accepted plan (``plan.json`` and the matching ``GRANT``). The prompt
  is rendered from the plan alone (hypotheses, the frozen queries, the URLs) and its sha is recorded in
  ``renders.jsonl`` as ``{kind: researcher, prompt_sha, plan_sha}``.
- ``harness-critic render <unit>``: the unit must be in the frozen scope; the prompt is rendered from the
  card's frozen oracle criteria and the unit, and recorded as ``{kind: verifier, prompt_sha, unit}``.

Lanes allow a spawn only when its prompt's sha and its agent type match a recorded render.
"""
from __future__ import annotations

import json

from . import canon, store
from .errors import Denied
from .events import EventLog, now
from .ledger import Ledger
from .paths import job_paths


def _job(ctx):
    job = getattr(ctx, "job", None)
    if not job:
        raise Denied("renders belong to a task: there is no active task")
    p = job_paths(ctx.data_root, ctx.workspace, job)
    return job, p, EventLog(p.run, p.heads)


def _record(p, log, kind, prompt, **extra) -> str:
    sha = canon.sha256_bytes(prompt.encode("utf-8"))
    store.append_jsonl(p.run, log, "renders.jsonl", {"ts": now(), "kind": kind, "prompt_sha": sha, **extra},
                       "RENDER", {"kind": kind, "prompt_sha": sha})
    return sha


def research_apply(req, ctx, evt) -> dict:
    if req.get("sub") != "render":
        raise Denied("harness-research supports only render")
    job, p, log = _job(ctx)
    plan = store.read_json(p.run, log, "plan.json", None)
    grant = store.read_json(p.run, log, "egress_grant.json", None)
    if not isinstance(plan, dict) or not isinstance(grant, dict) or grant.get("plan_sha") != plan.get("plan_sha"):
        raise Denied("a research render needs a plan the owner accepted (harness-contact plan, then --accept)")
    prompt = "\n".join([
        f"You are the dynamic-harness researcher for research plan {plan['plan_sha'][:12]}.",
        f"Hypotheses: {json.dumps(plan['hypotheses'], ensure_ascii=False)}",
        f"Run only these searches: {json.dumps(plan['queries'], ensure_ascii=False)}",
        f"Fetch pages only with `harness-claims fetch <url>`, only these URLs or ones these searches return: "
        f"{json.dumps(plan['urls'], ensure_ascii=False)}",
        f"Stop when: {json.dumps(plan['stop_rule'], ensure_ascii=False)}. At most {plan['page_budget']} pages.",
        'Return only a JSON list of claim rows {"claim", "quote" (at most 300 characters, copied exactly), "date", '
        '"url", "snapshot_sha"}.'])
    sha = _record(p, log, "researcher", prompt, plan_sha=plan["plan_sha"])
    return {"kind": "researcher", "prompt": prompt, "prompt_sha": sha}


def critic_apply(req, ctx, evt) -> dict:
    if req.get("sub") != "render":
        raise Denied("harness-critic supports only render here")
    job, p, log = _job(ctx)
    unit = next((u for u in Ledger(p, log).scope() if u["id"] == req.get("unit")), None)
    if unit is None:
        raise Denied(f"unit {req.get('unit')!r} is not in the frozen scope")
    card = store.read_json(p.run, log, "card.v1.json", None) or {}
    criteria = (card.get("oracle") or {}).get("criteria") or []
    if not criteria:
        raise Denied("the card has no oracle criteria to check")
    lines = [f"You are the dynamic-harness verifier for unit {unit['id']} ({json.dumps(unit['label'], ensure_ascii=False)}):"
             f" {json.dumps(unit['claim'], ensure_ascii=False)}.",
             f"Read only the deliverable files under .claude/loops/{job}/work/.",
             "Answer each criterion with yes or no and one short reason:"]
    lines += [f"{i}. {json.dumps(c, ensure_ascii=False)}" for i, c in enumerate(criteria, 1)]
    prompt = "\n".join(lines)
    sha = _record(p, log, "verifier", prompt, unit=unit["id"])
    return {"kind": "verifier", "prompt": prompt, "prompt_sha": sha}


def ledger_apply(req, ctx, evt) -> dict:
    job, p, log = _job(ctx)
    kind = req.get("kind")
    row = req.get("row")
    if not isinstance(row, dict):
        raise Denied("harness-ledger append needs a row object")
    return {"kind": kind, "row": Ledger(p, log).append_row(kind, row)}
