"""Stop hook (spec §Gate and Stop semantics; build phase P4). Never runs the gate; 10 s timeout.

With an open task (``prompt_scope = active_task``), in order:

- tamper (event chain, protected files or receipt HMAC) → ``STOPPED_TAMPER``, the stop is allowed;
- a running background subagent → a non-terminal wait (no record, not counted as a block);
- rule 2: a stop record (``STOPPED_*``, ``PAUSED``) with no ``RESUMED`` after it;
- rule 4: the turn ends on a recorded contact (the session waits on ``pending_contact_id``);
- rule 1: a receipt that verifies → the final message is linted (state words outside template lines; every
  money/quantity token in the receipt's traced set), then DONE: jobs index, final decision record, and the
  state line as ``systemMessage``;
- the contact classifier: a final message that asks the owner something counts as a contact when one is
  allowed, else it blocks;
- otherwise block, naming what is missing. Three blocks with the same reason in one request → ``STOPPED_STALL``.

Without one (rule 3): a pending card blocks; the final message is linted for state words; a money token
blocks once and sets ``pending_card`` with floor T1.
"""
from __future__ import annotations

import json
import re
import unicodedata

from . import budget, canon, contacts, gate, pii, probe, protected, record, sessionstate
from .errors import HarnessError, TamperError
from .events import EventLog
from .normalize import quantity_tokens
from .paths import job_paths
from .safeio import safe_open

STALL_BLOCKS = 3
RUNNING = ("running", "pending", "in_progress", "started")
_ASKS = re.compile(r"[?؟]\s*$", re.M)


def _fold(text) -> str:
    """Format characters (zero-width joiners, bidi marks) removed, then the probe's folding."""
    return probe._norm("".join(ch for ch in text if unicodedata.category(ch) != "Cf"))


def lexicon_hits(ctx, text, allowed=()) -> list:
    """State words outside lines the harness itself rendered (``allowed``: only the receipt's state line)."""
    lex = protected.load_control("state_lexicon.json", ctx)
    terms = [_fold(t) for t in gate._flat(lex.get("terms")) if t]
    pats = [re.compile(("(?<!\\w)" if re.match(r"\w", t) else "") + re.escape(t) + ("(?!\\w)" if re.search(r"\w$", t)
                       else "")) for t in terms]
    ok = {_fold(a).strip() for a in allowed if a}
    hits = []
    for line in (text or "").splitlines():
        s = _fold(line)
        if s.strip() in ok:
            continue
        hits += [m.group() for p in pats for m in p.finditer(s)]
    return sorted(set(hits))


def _running(evt) -> bool:
    bt = evt.get("background_tasks")
    if not isinstance(bt, list):
        return False
    # only a running subagent is a wait; a background shell job is not (Stop input: {id, type, status, ...})
    return any(isinstance(t, dict) and t.get("type") == "subagent" and str(t.get("status", "")).lower() in RUNNING
               for t in bt)


def _traced(p, log, rec) -> set:
    """Tokens fold_ledger traced; the list is bound to the receipt HMAC by ``traced_set_sha``."""
    try:
        child = json.loads(safe_open(p.run / "gate" / "fold_receipt.json", [p.run]))
    except (OSError, ValueError, TamperError):
        return set()
    vals = child.get("traced_values") or [] if isinstance(child, dict) else []
    return set(map(str, vals)) if canon.sha256_json(vals) == rec.get("traced_set_sha") else set()


def _block(ctx, sid, st, rule, reason, job=None):
    """Block, or after STALL_BLOCKS blocks under the same rule in this request, end it as a stall. The rule,
    not the reason text, is counted: a reworded message cannot reset the count."""
    prev = st.get("stop_blocks") or {}
    here = prev.get("turn") == st.get("request_turn")
    n = int(prev.get("n", 0)) + 1 if here and prev.get("rule") == rule else 1
    total = int(prev.get("total", 0)) + 1 if here else 1  # alternating rules cannot loop either
    if n < STALL_BLOCKS and total < 2 * STALL_BLOCKS:
        sessionstate.update(ctx, sid, stop_blocks={"turn": st.get("request_turn"), "rule": rule, "n": n,
                                                   "total": total})
        return {"decision": "block", "reason": reason}
    sessionstate.update(ctx, sid, stop_blocks=None, pending_card=None)
    if job:  # the stall record carries no question (§Turns and contacts)
        msg = budget.stop(ctx, job, "STOPPED_STALL", f"the stop was blocked {n} times: {reason}")
        return {"systemMessage": msg or f"Stopped, not done: {reason}"}
    return {"systemMessage": f"Stopped, not done: {reason}"}


def _tamper(ctx, job, why) -> dict:
    sessionstate.set_job(ctx, job, state="STOPPED_TAMPER")
    try:
        p = job_paths(ctx.data_root, ctx.workspace, job)
        EventLog(p.run, p.heads).append("STOPPED", {"state": "STOPPED_TAMPER", "reason": why[:300]})
        record.write(ctx, job, "STOPPED_TAMPER")
    except (HarnessError, OSError):  # the chain itself may be what broke; the jobs index already says so
        pass
    return {"systemMessage": f"Stopped: the records of task {job} were changed outside the harness ({why[:200]})."}


def _t0(evt, ctx, sid, st, msg) -> dict:
    if st.get("pending_card"):
        return _block(ctx, sid, st, "card", "a task card is pending: draft it and run harness-compile before answering")
    hits = lexicon_hits(ctx, msg)
    if hits:
        return _block(ctx, sid, st, "lexicon", f"a quick answer may not use state words {hits}; rephrase without them")
    money = probe._hits(probe._norm(msg), protected.load_control("probe.json", ctx)["money_words"])
    if money:
        turn = st.get("request_turn")
        sessionstate.update(ctx, sid, pending_card={"floor": "T1", "reason": f"money in the answer: {money}",
                                                    "turn_id": turn})
        return {"decision": "block", "reason": f"the answer names money ({', '.join(money)}): this needs a task card "
                                               "(floor T1); draft it and run harness-compile first"}
    return {}


def _job(evt, ctx, sid, st, msg, job) -> dict:
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    try:
        log.verify()
        from .fold import check_protected
        check_protected(p, log)
    except TamperError as e:
        return _tamper(ctx, job, str(e))
    if _running(evt):
        return {}
    if budget.latest_stop(log):
        return {}
    if st.get("pending_contact_id"):
        return {}
    v = gate.verify_receipt(p, log, _key(ctx))
    if not v["ok"] and v["reason"].startswith("receipt HMAC"):
        return _tamper(ctx, job, v["reason"])
    if v["ok"] and gate.work_changed(p, log):
        return _block(ctx, sid, st, "work", "work/ changed after the gate: run harness-gate request again", job)
    if v["ok"]:
        hits = lexicon_hits(ctx, msg, allowed=[v["state"]])  # never model-influenced text such as opening.txt
        if hits:
            return _block(ctx, sid, st, "lexicon", f"the final message uses state words {hits} outside the state line", job)
        rec = json.loads(safe_open(p.run / "receipt.json", [p.run]))
        traced = _traced(p, log, rec)
        untraced = sorted({t for t, _ in quantity_tokens(msg)} - traced)
        if untraced:
            return _block(ctx, sid, st, "numbers", f"numbers {untraced[:8]} are not in the gate's traced set; cite only "
                                        "figures from the report", job)
        sessionstate.set_job(ctx, job, state="DONE", result=v["state"])
        record.write(ctx, job, "DONE")
        sessionstate.update(ctx, sid, stop_blocks=None)
        return {"systemMessage": v["state"]}
    if _ASKS.search(msg or ""):
        why = contacts.check("question", ctx)
        if why:
            return _block(ctx, sid, st, "question", f"the final message asks the owner something, but {why}", job)
        row = contacts.record(ctx, "question", canon.sha256_bytes(pii.redact(msg).encode("utf-8")),
                              extra={"classified": True}, recheck=False)
        sessionstate.update(ctx, sid, pending_contact_id=row["contact_id"], stop_blocks=None)
        return {}
    return _block(ctx, sid, st, "not_done", f"task {job} is not done ({v['reason']}): finish the units, run harness-gate "
                                "request, or ask the owner through harness-contact", job)


def _key(ctx) -> bytes:
    from .gatehook import load_key
    return load_key(ctx.data_root)


def on_stop(evt, ctx) -> dict:
    sid = evt.get("session_id")
    if not sid:
        return {}
    st = sessionstate.load(ctx, sid)
    msg = evt.get("last_assistant_message")
    msg = msg if isinstance(msg, str) else ""
    job = getattr(ctx, "job", None)
    if job and not sessionstate.jobs(ctx).get(job, {}).get("terminal"):
        return _job(evt, ctx, sid, st, msg, job)
    return _t0(evt, ctx, sid, st, msg)
