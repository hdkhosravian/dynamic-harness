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
- ``definition_confirm --metric FILE`` stores ``definition.json`` and shows the owner a sentence rendered from it
  ("I'll count revenue as …. Is that right?"); ``definition_confirm --accept latest`` after the owner's plain
  approval writes the metric dictionary entry (a changed definition gets a new version) (P5).
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
_YES = r"(?:yes|y|ok|okay|sure|approve|approved|accept|accepted|agreed|go ahead|continue|بله|آره|باشه|تایید|تأیید|قبول|موافقم)"
_TAIL = r"(?:yes|ok|okay|go ahead|continue|approved|approve it|approve the plan|go ahead with the plan|with the plan|please|thanks|thank you|تایید|قبول|ممنون|لطفا)"
APPROVAL = re.compile(rf"{_YES}(?: {_TAIL})*")


def is_approval(text) -> bool:
    t = re.sub(r"[\s،,.!؛;:]+", " ", (text or "").replace("\u200c", " ")).strip().casefold()
    return APPROVAL.fullmatch(t) is not None


def _plan_text(plan) -> str:
    """What the owner sees for a plan: rendered from plan.json itself, so the approval covers the grant."""
    # free-text fields are quoted, so a hypothesis cannot pose as another line of the plan
    return "\n".join([f"Research plan {plan['plan_sha'][:12]}",
                      f"- Hypotheses: {json.dumps(plan['hypotheses'], ensure_ascii=False)}",
                      f"- Searches: {json.dumps(plan['queries'], ensure_ascii=False)}",
                      f"- Pages: {json.dumps(plan['urls'], ensure_ascii=False)}",
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
    if kind == "cost" and not req.get("accept"):
        raise Denied("cost contacts are written by the budget hook; answer one with harness-contact cost --accept")
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    from . import budget
    if kind != "cost" and budget.latest_stop(log) in budget.STOPS[:3]:
        raise Denied(f"task {job} is {budget.latest_stop(log)}: only its cost contact can be answered")
    if req.get("accept"):
        return _accept_cost(req, ctx, p, log) if kind == "cost" else _accept(req, ctx, p, log)
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
    if kind == "assumption_sheet":
        from . import model_cmd
        rows = model_cmd._rows(p, log)
        if not rows:
            raise Denied("record the assumptions first (harness-model assume); the sheet is rendered from them")
        extra["sheet_sha"] = canon.sha256_json(rows)
        text = model_cmd.sheet_text(rows, ctx)  # the owner confirms the recorded values, not a retelling
    if kind == "definition_confirm" and req.get("metric") is not None:
        metric = _metric(req["metric"])
        store.write_json(p.run, log, "definition.json", metric, "DEFINITION", {"metric_id": metric["metric_id"]})
        extra["metric_sha"] = canon.sha256_json(metric)
        text = definition_text(metric, ctx)  # the owner confirms the rendered sentence, not the model's wording
    row = contacts.record(ctx, kind, canon.sha256_bytes(text.encode("utf-8")), handoff=handoff, extra=extra)
    sessionstate.update(ctx, evt.get("session_id"), pending_contact_id=row["contact_id"])
    if handoff:
        log.append("PAUSED", {"reason": "handoff", "contact_id": row["contact_id"]})
        sessionstate.set_job(ctx, job, state="PAUSED")
    return {"contact_id": row["contact_id"], "type": kind, "counted": row["counted"], "text": text,
            "handoff": handoff}


def _metric(m) -> dict:
    from . import business
    schema.validate(m, business.METRIC, "metric")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", m["metric_id"]):
        raise SchemaError("metric_id is lower-case letters, digits and _ (for example revenue or net_sales)")
    for k in ("inclusions", "exclusions"):
        if len(m[k]) > 300 or "\n" in m[k]:
            raise SchemaError(f"{k} is one line of at most 300 characters")
    return dict(m, currency=m["currency"].upper())


def definition_text(metric, ctx) -> str:
    from . import business, templates
    unit = business.CURRENCIES.get(metric["currency"], metric["currency"])
    return templates.render("definition", ctx, metric=metric["metric_id"], inclusions=metric["inclusions"],
                            exclusions=metric["exclusions"] or "nothing", unit=unit)


def _owner_reply(ctx, row, accept):
    after = int(row.get("turn_seq") or 0)
    if accept == "latest":
        replies = [r for r in Turns(ctx.data_root)._rows() if r.get("pending_contact_id") == row["contact_id"]
                   and not r.get("system") and r.get("seq", 0) > after]
        if not replies:
            raise Denied(f"the owner has not replied to contact {row['contact_id']}")
        accept = replies[-1]["turn_id"]
    turn = Turns(ctx.data_root).cite(accept, after_seq=after)
    if turn.get("pending_contact_id") != row["contact_id"]:
        raise Denied(f"turn {accept} is not the owner's reply to contact {row['contact_id']}")
    if not is_approval(turn["text_norm"]):
        raise Denied(f"turn {accept} is not a plain approval (reply yes to continue)")
    return accept


def _accept_cost(req, ctx, p, log) -> dict:
    """Continue after STOPPED_BUDGET / STOPPED_COST: a one-time increment, recorded, then RESUMED."""
    from . import budget
    with log.lock():  # one acceptance per contact, even for parallel calls
        st = contacts.state(ctx)
        rows = [r for r in st.get("rows", []) if r.get("type") == "cost"]
        if not rows or rows[-1].get("accepted"):
            raise Denied("there is no open cost contact to accept")
        row = rows[-1]
        if budget.latest_stop(log) not in ("STOPPED_BUDGET", "STOPPED_COST"):
            raise Denied("the task is not stopped on its budget")
        turn_id = _owner_reply(ctx, row, req["accept"])
        b = store.read_json(p.run, log, "budget.json", None) or {}
        b = dict(b, calls_cap=int(b.get("calls_cap") or 0) + int(row.get("calls") or 0),
                 money_increment=float(b.get("money_increment") or 0) + float(row.get("amount") or 0),
                 seconds_increment=float(b.get("seconds_increment") or 0) + budget.T1_SECONDS / 2,
                 calls_at_verdict=int(b.get("calls") or 0))
        store.write_json(p.run, log, "budget.json", b, "BUDGET", {"increment": True})
        log.append("APPROVAL", {"kind": "cost_increment", "calls": row.get("calls"), "amount": row.get("amount"),
                                "turn_id": turn_id, "contact_id": row["contact_id"]})
        contacts.mark(ctx, row["contact_id"], accepted=True, accepted_turn=turn_id)
        log.append("RESUMED", {"job": p.job, "turn_id": turn_id})
    sessionstate.set_job(ctx, p.job, state="EXECUTING")
    return {"resumed": True, "contact_id": row["contact_id"], "calls": row.get("calls"), "amount": row.get("amount")}


def _open_logs(p, log) -> None:
    """The investigation's search log and contradiction register exist from the start (``--require-logs``)."""
    from .safeio import atomic_write
    with log.lock():
        for name in ("search_log.jsonl", "contradictions.jsonl"):
            if not (p.run / name).exists() and not log.latest_file_sha(name):
                atomic_write(p.run / name, b"")
                log.append("LOG_OPENED", {"log": name}, file=name, file_sha=canon.sha256_bytes(b""))


def _accept_definition(req, ctx, p, log) -> dict:
    """The owner's plain approval of a rendered definition writes the metric dictionary entry."""
    from . import business
    with log.lock():
        rows = [r for r in contacts.state(ctx).get("rows", []) if r.get("type") == "definition_confirm"
                and r.get("metric_sha")]
        if not rows or rows[-1].get("accepted"):
            raise Denied("there is no open definition to accept")
        row = rows[-1]
        metric = store.read_json(p.run, log, "definition.json", None)
        if not isinstance(metric, dict) or canon.sha256_json(metric) != row["metric_sha"]:
            raise Denied("definition.json does not match the latest definition contact")
        turn_id = _owner_reply(ctx, row, req["accept"])
        entry = business.define_metric(ctx, metric, definition_text(metric, ctx), turn_id)
        contacts.mark(ctx, row["contact_id"], accepted=True, accepted_turn=turn_id)
        log.append("APPROVAL", {"kind": "definition", "metric_id": metric["metric_id"], "version": entry["version"],
                                "turn_id": turn_id, "contact_id": row["contact_id"]})
    return {"defined": metric["metric_id"], "version": entry["version"], "restated": entry.get("previous_version")}


def _accept_sheet(req, ctx, p, log) -> dict:
    from . import model_cmd
    rows = [r for r in contacts.state(ctx).get("rows", []) if r.get("type") == "assumption_sheet"]
    if not rows or rows[-1].get("accepted"):
        raise Denied("there is no open assumption sheet to accept")
    turn_id = _owner_reply(ctx, rows[-1], req["accept"])
    out = model_cmd.accept_sheet(ctx, p, log, rows[-1], turn_id)
    contacts.mark(ctx, rows[-1]["contact_id"], accepted=True, accepted_turn=turn_id)
    log.append("APPROVAL", {"kind": "assumption_sheet", "turn_id": turn_id, "contact_id": rows[-1]["contact_id"]})
    return out


def _accept(req, ctx, p, log) -> dict:
    if req.get("type") == "definition_confirm":
        return _accept_definition(req, ctx, p, log)
    if req.get("type") == "assumption_sheet":
        return _accept_sheet(req, ctx, p, log)
    if req.get("type") != "plan":
        raise Denied("only plan, definition_confirm and assumption_sheet contacts are accepted this way")
    st = contacts.state(ctx)
    plans = [r for r in st.get("rows", []) if r.get("type") == "plan"]
    if not plans:
        raise Denied("there is no plan contact to accept")
    last = plans[-1]
    if last.get("accepted"):
        raise Denied(f"plan contact {last['contact_id']} is already accepted")
    accept = req["accept"]
    if accept == "latest":  # the model never sees prompt ids: the owner's latest reply to this plan contact
        replies = [r for r in Turns(ctx.data_root)._rows()
                   if r.get("pending_contact_id") == last["contact_id"] and not r.get("system")]
        if not replies:
            raise Denied(f"the owner has not replied to plan contact {last['contact_id']}")
        accept = replies[-1]["turn_id"]
    req = dict(req, accept=accept)
    turn = Turns(ctx.data_root).cite(accept, after_seq=last.get("turn_seq", 0))
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
    _open_logs(p, log)
    contacts.mark(ctx, last["contact_id"], accepted=True, accepted_turn=req["accept"])
    return {"granted": True, "plan_sha": plan["plan_sha"], "domains": plan["domains"]}
