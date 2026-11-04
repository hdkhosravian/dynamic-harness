"""Gate hook: PostToolUse on a lone ``harness-gate request`` (spec §Gate adapter; build phase P4).

Its own hooks.json entry (matcher ``Bash``, fixed 1800 s timeout, watchdog 1795 s); every other Bash call
returns at once. The main PostToolUse entry ignores ``harness-gate`` (it only prints a request).

The hook, never the agent, runs ``gate.run_gate`` for the open task:

- the task must be T1/T2, owned by this session (lease) and not stopped;
- the profile is ``gate.json`` ``<tier>.<task_type>`` or ``<tier>.default``, plus the task type;
- the HMAC key is ``keys/hmac.key`` (created 0600 on first use; the agent cannot read ``keys/``);
- fold_ledger is ``<loop_contract_root>/skills/loop-contract/scripts/fold_ledger.py``.

Done mode needs an ``oracle_cmd`` from a pack manifest (P5); until then a done-mode gate is refused with
that reason, and known mode writes a receipt.
"""
from __future__ import annotations

import os

from . import budget, contacts, gate, lease, protected
from .errors import Denied, HarnessError
from .events import EventLog
from .paths import job_paths
from .writers import _lone_harness, _request

FOLD = ("skills", "loop-contract", "scripts", "fold_ledger.py")


def load_key(data_root) -> bytes:
    d = os.path.join(os.fspath(data_root), "keys")
    os.makedirs(d, mode=0o700, exist_ok=True)
    f = os.path.join(d, "hmac.key")
    try:
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as fh:
            fh.write(os.urandom(32))
    fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        key = fh.read()
    if len(key) != 32:
        raise HarnessError("keys/hmac.key is not a 32-byte key")
    return key


def _profile(ctx, tier, task_type) -> dict:
    profiles = protected.load_control("gate.json", ctx).get("profiles", {})
    prof = profiles.get(f"{tier}.{task_type}") or profiles.get(f"{tier}.default")
    if not prof:
        raise Denied(f"no gate profile for {tier}.{task_type}")
    return dict(prof, task_type=task_type)


def run(evt, ctx) -> dict:
    job = getattr(ctx, "job", None)
    if not job:
        raise Denied("there is no open task to gate")
    sid = evt.get("session_id")
    lease.require(ctx, job, sid)
    tier = contacts.tier(ctx)
    if tier not in ("T1", "T2"):
        raise Denied(f"task {job} is {tier}; only T1/T2 tasks are gated")
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    stopped = budget.latest_stop(log)
    if stopped:
        raise Denied(f"task {job} is {stopped}; resume it before the gate")
    from .sessionstate import jobs
    prof = _profile(ctx, tier, jobs(ctx).get(job, {}).get("task_type") or "default")
    if not ctx.loop_contract_root:
        raise Denied("loop-contract is not configured (run /dynamic-harness:setup)")
    res = gate.run_gate(p, log, load_key(ctx.data_root), prof, os.path.join(os.fspath(ctx.loop_contract_root), *FOLD))
    if not res.get("ok"):
        raise HarnessError(f"gate failed (exit {res.get('code')}): {(res.get('stderr') or '')[-500:]}")
    v = gate.verify_receipt(p, log, load_key(ctx.data_root))
    return {"state": v.get("state"), "mode": res["receipt"]["mode"], "caps": res["receipt"]["caps"]}


def on_post(evt, ctx) -> dict:
    if evt.get("tool_name") != "Bash":
        return {}
    parsed = _lone_harness((evt.get("tool_input") or {}).get("command"))
    if parsed is None or parsed[0] != "gate":
        return {}
    try:
        req = _request(evt, "gate")
        if req.get("action") != "request":
            raise Denied("harness-gate supports only: request")
        out = run(evt, ctx)
    except HarnessError as e:
        return {"decision": "block", "reason": f"harness-gate not run: {e}"}
    caps = f" (caps: {', '.join(out['caps'])})" if out["caps"] else ""
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
            f"harness: gate ran in {out['mode']} mode{caps}. State: {out['state']}. You may stop now."}}
