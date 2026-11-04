"""``contact.apply``: the writer for ``harness-contact <type> [--handoff | --accept <turn_id>]`` (spec §Trust
boundaries writers: contacts, plan.json, egress grant; §Turns and contacts; build phase P3).

- Re-checks the cap and the 50% rule (``contacts.check``), records the contact in ``contacts.json`` and
  points the session's ``pending_contact_id`` at it, so the owner's next turn routes back as a reply.
- ``--handoff`` (at the cap, once per task, before-spend types only) also appends ``PAUSED{handoff}`` and
  marks the job PAUSED in the jobs index.
- ``plan`` writes the protected ``plan.json`` (``plan_sha`` over the plan) from the request.
- ``plan`` text shown to the owner is rendered from ``plan.json`` (hypotheses, domains, page budget, stop
  rule), never the model's wording, so the approval covers exactly what is granted.
- ``plan --accept <turn_id>``: the cited turn must be the owner's reply to that plan contact (its
  ``pending_contact_id``), come after it, and be an approval as a whole ("yes", "ok, go ahead"; no keyword search); the contact is
  then consumed. The
  egress grant ``{plan_sha, domains, page_budget, active, expires_at}`` is written with a ``GRANT`` event,
  which is the only form ``hookmain`` honours. Domains come from the plan's URLs.
- ``cost`` contacts come with the budget hook (P4) and are refused here.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from . import canon, contacts, schema, sessionstate, store
from .errors import Denied, SchemaError
from .events import EventLog
from .paths import job_paths
from .turns import Turns

PLAN_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["hypotheses", "queries", "urls", "page_budget", "stop_rule", "load_bearing"],
               "properties": {"hypotheses": {"type": "array", "items": {"type": "string"}},
                              "queries": {"type": "array", "items": {"type": "string"}},
                              "urls": {"type": "array", "items": {"type": "string"}},
                              "domains": {"type": "array", "items": {"type": "string"}},
                              "page_budget": {"type": "integer", "minimum": 1},
                              "languages": {"type": "array", "items": {"type": "string"}},
                              "stop_rule": {"type": "string"},
                              "load_bearing": {"type": "array", "items": {"type": "string"}}}}
GRANT_TTL = 24 * 3600
# the whole reply must be an approval (no keyword search: "no, don't, okay?" is not consent)
_YES = r"(?:yes|y|ok|okay|sure|approve|approved|accept|accepted|agreed|go ahead|بله|آره|باشه|تایید|تأیید|قبول|موافقم)"
_TAIL = r"(?:yes|ok|okay|go ahead|approved|approve it|approve the plan|go ahead with the plan|with the plan|please|thanks|thank you|تایید|قبول|ممنون|لطفا)"
APPROVAL = re.compile(rf"{_YES}(?: {_TAIL})*")


def is_approval(text) -> bool:
    t = re.sub(r"[\s،,.!؛;:]+", " ", (text or "").replace("\u200c", " ")).strip().casefold()
    return APPROVAL.fullmatch(t) is not None


def _plan_text(plan) -> str:
    """What the owner sees for a plan: rendered from plan.json itself, so the approval covers the grant."""
    # free-text fields are quoted, so a hypothesis cannot pose as another line of the plan
    return "\n".join([f"Research plan {plan['plan_sha'][:12]}",
                      f"- Hypotheses: {json.dumps(plan['hypotheses'], ensure_ascii=False)}",
                      f"- Sources (domains): {', '.join(plan['domains'])}",
                      f"- Up to {plan['page_budget']} pages",
                      f"- Stop when: {json.dumps(plan['stop_rule'], ensure_ascii=False)}",
                      "Reply yes to approve this plan, or say what to change."])


def _domains(urls) -> list:
    out = set()
    for u in urls:
        host = urlsplit(u).hostname if isinstance(u, str) else None
        if not host or urlsplit(u).scheme != "https":
            raise SchemaError(f"plan URL {u!r} must be an https URL")
        out.add(host.lower())
    return sorted(out)


def apply(req, ctx, evt) -> dict:
    kind = req.get("type")
    job = getattr(ctx, "job", None)
    if not job:
        raise Denied("contacts belong to a task: there is no active task")
    if kind == "cost":
        raise Denied("cost contacts are written by the budget hook (P4)")
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    if req.get("accept"):
        return _accept(req, ctx, p, log)
    handoff = req.get("handoff") is True
    why = contacts.check(kind, ctx, handoff=handoff)  # early, clear refusal; record() re-checks under the lock
    if why:
        raise Denied(why)
    text = req.get("text") if isinstance(req.get("text"), str) else ""
    extra = {"turn_seq": (Turns(ctx.data_root).latest() or {}).get("seq", 0)}
    if kind == "plan":
        plan = req.get("plan")
        schema.validate(plan, PLAN_SCHEMA, "plan")
        plan = dict(plan, domains=_domains(plan["urls"]))
        plan_sha = canon.sha256_json(plan)
        stored = dict(plan, schema="harness/research_plan@1", plan_sha=plan_sha)
        store.write_json(p.run, log, "plan.json", stored, "PLAN", {"plan_sha": plan_sha})
        extra["plan_sha"] = plan_sha
        text = _plan_text(stored)  # the model's own wording is not what the owner approves
    row = contacts.record(ctx, kind, canon.sha256_bytes(text.encode("utf-8")), handoff=handoff, extra=extra)
    sessionstate.update(ctx, evt.get("session_id"), pending_contact_id=row["contact_id"])
    if handoff:
        log.append("PAUSED", {"reason": "handoff", "contact_id": row["contact_id"]})
        sessionstate.set_job(ctx, job, state="PAUSED")
    return {"contact_id": row["contact_id"], "type": kind, "counted": row["counted"], "text": text,
            "handoff": handoff}


def _accept(req, ctx, p, log) -> dict:
    if req.get("type") != "plan":
        raise Denied("only a plan contact is accepted this way in P3")
    st = contacts.state(ctx)
    plans = [r for r in st.get("rows", []) if r.get("type") == "plan"]
    if not plans:
        raise Denied("there is no plan contact to accept")
    last = plans[-1]
    if last.get("accepted"):
        raise Denied(f"plan contact {last['contact_id']} is already accepted")
    turn = Turns(ctx.data_root).cite(req["accept"], after_seq=last.get("turn_seq", 0))
    if not is_approval(turn["text_norm"]):
        raise Denied(f"turn {req['accept']} is not a plain approval of the plan (reply yes, or say what to change)")
    if turn.get("pending_contact_id") != last["contact_id"]:
        raise Denied(f"turn {req['accept']} is not the owner's reply to plan contact {last['contact_id']}")
    plan = store.read_json(p.run, log, "plan.json", None)
    if not isinstance(plan, dict) or plan.get("plan_sha") != last.get("plan_sha"):
        raise Denied("plan.json does not match the latest plan contact")
    grant = {"schema": "harness/egress_grant@1", "plan_sha": plan["plan_sha"], "domains": plan["domains"],
             "page_budget": plan["page_budget"], "active": True, "turn_id": req["accept"],
             "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + GRANT_TTL))}
    store.write_json(p.run, log, "egress_grant.json", grant, "GRANT", {"plan_sha": plan["plan_sha"]})
    contacts.mark(ctx, last["contact_id"], accepted=True, accepted_turn=req["accept"])
    return {"granted": True, "plan_sha": plan["plan_sha"], "domains": plan["domains"]}
