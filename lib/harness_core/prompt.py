"""UserPromptSubmit (spec §Hooks "UserPromptSubmit"; §The eight steps 1-2; build phase P3).

Order, for every prompt:

1. ``/dynamic-harness:setup …`` goes to ``setup.on_prompt`` (it blocks; the model never sees it).
2. The turn is appended to ``turns.jsonl`` with ``text_norm = pii.redact(prompt)``, ``typed_command`` for a
   slash command, and ``pending_contact_id`` when the session was waiting for a reply. A
   ``<task-notification>`` prompt is a system turn; nothing else happens for it.
3. Resume routing comes first: a reply to a pending contact resumes that contact's task
   (``RESUMED`` if it was paused).
4. Otherwise the probe runs (a probe failure fails towards a card with a T2 floor). With an open task, a
   prompt that is shaped like a continuation, names its subject, or overlaps its frozen scope goes to that
   task and is logged as a correction (evidence only); anything else runs as an inline T0.
5. Without a task in scope, the pre-filter decides: ``T0-fast``, or ``card-needed`` (``pending_card`` with the
   probe's floor) plus context telling the model to draft a card.

Thread matching to terminal jobs and the lease heartbeat belong to the state machine (P4).
"""
from __future__ import annotations

import re
import secrets

from . import corrections, pii, probe, sessionstate, setup, store
from .errors import Denied
from .events import EventLog
from .ledger import Ledger
from .paths import job_paths
from .turns import SYSTEM_PREFIX, Turns

# when the probe itself fails: a card is needed, at T2, and compile still has a probe to work from
FALLBACK_PROBE = {"schema": "harness/probe@1", "request_sha": "", "question_only": False, "action_verbs": [],
                  "irreversible_classes": [], "write_targets": [], "files_named": [], "connectors": [],
                  "write_connectors": [], "money_tokens": [], "number_tokens": [], "quantitative": True,
                  "data_classes": [], "decision_hits": {}, "continuation": False, "floor_tier": "T2"}
STOP = {"the", "and", "for", "with", "that", "this", "from", "what", "which", "how", "our", "your", "please"}


def _words(text) -> set:
    return {w for w in re.findall(r"\w{4,}", text.casefold()) if w not in STOP}


def _matches_open_task(ctx, job, text, p) -> bool:
    if p.get("continuation"):
        return True
    paths = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(paths.run, paths.heads)
    card = store.read_json(paths.run, log, "card.v1.json", None) or {}
    subject = _words(" ".join([card.get("request", ""), card.get("deliverable", {}).get("description", "")]))
    words = _words(text)
    if subject and len(words & subject) >= 2:
        return True
    labels = [u.get("label", "") for u in Ledger(paths, log).scope()]
    return any(lbl and re.search(r"(?<!\w)" + re.escape(lbl.casefold()) + r"(?!\w)", text.casefold()) for lbl in labels)


def _resume_elsewhere(ctx, sid, text, turn_id):
    """"continue" in a session with no task: the single open task in the jobs index. A stale lease is taken
    over (epoch fencing); a live lease elsewhere leaves this prompt outside the task."""
    from . import lease
    try:
        p = probe.run(text, ctx)
    except Exception:  # the main path below fails towards a T2 card
        return None, None
    if not p.get("continuation"):
        return None, None
    rows = {j: r for j, r in sessionstate.jobs(ctx).items() if not r.get("terminal") and r.get("tier")}
    if len(rows) != 1:
        return None, None
    (job,) = rows
    try:
        taken = lease.claim(ctx, job, sid)
    except Denied:
        return None, f"harness: task {job} is open in another session; it continues there."
    note = None
    if taken["taken"]:
        note = (f"harness: took over task {job} from a stale session (epoch {taken['epoch']}); "
                f"remaining units: {', '.join(taken['remaining']) or 'none'}.")
    sessionstate.update(ctx, sid, active_task=job, prompt_scope="active_task", request_turn=turn_id)
    return job, note


def _context(text):
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}


def on_prompt(evt, ctx) -> dict:
    if setup.is_setup_prompt(evt):
        return setup.on_prompt(evt, ctx)
    sid, text = evt.get("session_id"), evt.get("prompt")
    if not sid or not isinstance(text, str):
        return {}
    text = text.encode("utf-8", "replace").decode("utf-8")  # a lone surrogate would not survive canonical JSON
    pid = evt.get("prompt_id")
    turn_id = pid if isinstance(pid, str) and pid else str(pid) if isinstance(pid, int) else f"{sid}:{secrets.token_hex(8)}"
    st = sessionstate.load(ctx, sid)
    row = {"turn_id": turn_id, "text_norm": pii.redact(text)}
    if text.lstrip().startswith("/"):
        row["typed_command"] = text.strip().splitlines()[0]
    if st.get("pending_contact_id") and not text.lstrip().startswith(SYSTEM_PREFIX):
        row["pending_contact_id"] = st["pending_contact_id"]
    turns = Turns(ctx.data_root)
    known = next((r for r in turns._rows() if r.get("turn_id") == turn_id), None)
    if known is not None and known.get("text_norm") != row["text_norm"]:
        row["turn_id"] = turn_id = f"{turn_id}:{secrets.token_hex(4)}"  # same id, other text: a new turn
        known = None
    turn = known or turns.append(row)  # the same prompt_id and text twice (a retried hook) is one turn
    if turn["system"]:
        return {}
    job = st.get("active_task")
    open_job = job and not sessionstate.jobs(ctx).get(job, {}).get("terminal")
    held = None
    if not open_job:
        job, note = _resume_elsewhere(ctx, sid, text, turn_id)
        open_job = bool(job)
        if open_job:
            return _context(note) if note else {}
        held = note  # held elsewhere: this "continue" is a new request here and still needs its card
    elif open_job:
        from . import lease
        lease.heartbeat(ctx, job, sid)
    if row.get("pending_contact_id") and open_job:
        p = job_paths(ctx.data_root, ctx.workspace, job)
        from . import budget
        if budget.latest_stop(EventLog(p.run, p.heads)) == "PAUSED":  # never ends a budget, cost or stall stop
            EventLog(p.run, p.heads).append("RESUMED", {"job": job, "turn_id": turn_id})
            sessionstate.set_job(ctx, job, state="EXECUTING")
        sessionstate.update(ctx, sid, pending_contact_id=None, prompt_scope="active_task", request_turn=turn_id)
        return {}
    try:
        pr = probe.run(text, ctx, cwd=evt.get("cwd"))
        verdict = probe.prefilter(pr)
    except Exception as e:  # fail towards a card, never towards T0
        pr, verdict = dict(FALLBACK_PROBE), "card-needed"
        reason = f"probe failed ({type(e).__name__})"
    else:
        reason = "probe"
    if open_job and reason == "probe" and _matches_open_task(ctx, job, text, pr):
        sessionstate.update(ctx, sid, prompt_scope="active_task", request_turn=turn_id)
        if not pr.get("continuation"):
            corrections.log(ctx, job, turn_id)
            return _context(f"harness: this is a correction to task {job} (turn {turn_id}). If it changes the "
                            "units, deliverable or oracle, re-draft the card and run harness-compile --card <file> "
                            f"--reunderstand {turn_id}; otherwise carry on.")
        return {}
    scope = "inline_t0" if open_job else "new"
    if verdict == "T0-fast":
        sessionstate.update(ctx, sid, prompt_scope=scope, probe=pr, request_turn=turn_id, pending_card=None)
        return _context(held) if held else {}
    floor = pr["floor_tier"]
    sessionstate.update(ctx, sid, prompt_scope=scope, probe=pr, request_turn=turn_id,
                        pending_card={"floor": floor, "reason": reason, "turn_id": turn_id})
    bindir = f"{ctx.plugin_root}/bin" if getattr(ctx, "plugin_root", None) else "bin"
    return _context((f"{held}\n" if held else "") + f"harness: this request needs a task card before any work (floor {floor}). Draft it as the "
                    f"dynamic-harness skill describes, then run {bindir}/harness-card validate <file> and "
                    f"{bindir}/harness-compile --card <file>. Until it compiles, only reads are allowed.")
