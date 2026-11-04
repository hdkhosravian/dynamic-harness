"""``harness-model assume|register|scenario|import``: the model writer (spec §Business simulations "Assumptions",
"Iteration", "Persistence"; R-14; build phase P5).

- ``assume ID VALUE --unit U --period P --source S``: an assumption row in ``run/assumptions.json``. Only the
  owner's plain approval of the rendered assumption sheet (``harness-contact assumption_sheet``, then
  ``--accept latest``) marks values owner-confirmed.
  An assumption recorded after 50% progress is flagged ``late``.
- ``register --deliverable FILE``: after a receipt that verifies (``MODEL_REGISTERED`` is a receipt-safe event),
  the model registry entry ``{model_id, version, input_hash, assumptions_ref, deliverable_path,
  deliverable_sha, scenario_history[]}`` in ``${CLAUDE_PLUGIN_DATA}/models/registry.json``. It is outside the
  business layer on purpose: the receipt's HMAC covers the business hash.
- ``scenario --set ID=VALUE…``: "now try 15%" on a DONE, registered model. It opens the child job
  ``<job>.s<n>`` (``scenario_of``) through ``compile.apply`` in-process, copies the assumptions with the delta
  applied and the delivered model into the child's work/, and needs no contact.
- ``import FILE``: a returned assumption sheet (CSV ``id,value``). Unchanged: nothing to do. Only known
  assumption values changed: a scenario run. Anything else is a structural change: a new task with ``follows``.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from pathlib import Path

from . import canon, store
from .errors import Denied
from .events import EventLog, now
from .paths import job_paths
from .safeio import atomic_write, safe_open

SOURCES = ("history", "benchmark", "guess", "owner")
UNITS_RATE = ("ratio", "percent", "index")
PERIODS = ("month", "year", "once")
_ID = re.compile(r"[a-z][a-z0-9_]{0,40}")


def _registry_path(ctx) -> Path:
    return Path(ctx.data_root) / "models" / "registry.json"


def registry(ctx) -> dict:
    f = _registry_path(ctx)
    if not f.exists():
        return {}
    return (json.loads(safe_open(f, [f.parent])) or {}).get("models") or {}


def _write_registry(ctx, models) -> None:
    f = _registry_path(ctx)
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(f, (json.dumps({"schema": "harness/model_registry@1", "models": models}, indent=2, sort_keys=True,
                                ensure_ascii=False) + "\n").encode("utf-8"))


def _value(v):
    if isinstance(v, bool):
        raise Denied("an assumption value is a number or a list of 12 numbers")
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, list) and len(v) == 12 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return v
    raise Denied("an assumption value is a number or a list of 12 numbers (a seasonality)")


def _stated_by_owner(ctx, value) -> bool:
    """The owner's latest turn states this value (12, 12%, 0.12 for a rate, 1,200,000)."""
    from .localize import digits
    from .turns import Turns
    turn = Turns(ctx.data_root).latest() or {}
    if turn.get("system") or not isinstance(value, (int, float)):
        return False
    text = re.sub(r"[,٬،]", "", digits(turn.get("text_norm") or ""))
    nums = {float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)}
    return any(abs(n - value) < 1e-9 or abs(n / 100 - value) < 1e-9 for n in nums)


def _rows(p, log) -> list:
    return (store.read_json(p.run, log, "assumptions.json", None) or {}).get("assumptions", [])


def _save(p, log, rows, why) -> None:
    store.write_json(p.run, log, "assumptions.json", {"schema": "harness/assumptions@1", "assumptions": rows},
                     "ASSUMPTIONS", {"why": why, "count": len(rows)})


def assume(req, ctx, p, log) -> dict:
    from . import contacts
    aid = req.get("id")
    if not isinstance(aid, str) or not _ID.fullmatch(aid):
        raise Denied("an assumption id is lower-case letters, digits and _")
    value = _value(req.get("value"))
    unit, period, source = req.get("unit"), req.get("period"), req.get("source")
    if not isinstance(unit, str) or not unit:
        raise Denied("--unit is required (the model's currency, or ratio, percent or index)")
    if period not in PERIODS:
        raise Denied(f"--period is one of {', '.join(PERIODS)}")
    if source not in SOURCES:
        raise Denied(f"--source is one of {', '.join(SOURCES)}")
    if len(unit) > 20 or not re.fullmatch(r"[\w%./ -]+", unit):
        raise Denied("--unit is a short unit name (IRT, ratio, percent, index)")
    label = str(req.get("label") or aid)
    if len(label) > 80 or any(ord(c) < 32 for c in label):
        raise Denied("--label is one line of at most 80 characters")
    # only the owner's confirmation of the rendered sheet confirms a value: a number that happens to appear in the
    # owner's words ("I have 12 staff") is not a confirmation of growth = 12%
    confirmed = False
    row = {"id": aid, "value": value, "unit": unit, "period": period, "source": source,
           "as_of": req.get("as_of") or time.strftime("%Y-%m-%d"), "owner_confirmed": confirmed,
           "label": label, "late": contacts.progress(ctx) >= 0.5,
           "expires_at": time.strftime("%Y-%m-%d", time.gmtime(time.time() + 90 * 86400))}
    with log.lock():
        rows = [r for r in _rows(p, log) if r["id"] != aid] + [row]
        _save(p, log, rows, f"assume {aid}")
    return {"assumed": aid, "owner_confirmed": confirmed, "late": row["late"]}


def sheet_text(rows, ctx) -> str:
    from . import templates
    lines = [templates.render("assumption_sheet", ctx)]
    for r in rows:
        v = r["value"] if not isinstance(r["value"], list) else "monthly factors " + ", ".join(map(str, r["value"]))
        # free text is quoted, so a label cannot pose as another line of the sheet
        lines.append(f"- {json.dumps(r['label'], ensure_ascii=False)}: {v} {json.dumps(r['unit'], ensure_ascii=False)}"
                     f" per {r['period']} (from {r['source']})")
    return "\n".join(lines)


def accept_sheet(ctx, p, log, row, turn_id) -> dict:
    with log.lock():
        rows = _rows(p, log)
        if canon.sha256_json(rows) != row.get("sheet_sha"):
            raise Denied("the assumptions changed since the sheet was shown; show it again")
        rows = [dict(r, owner_confirmed=True, confirmed_turn=turn_id) for r in rows]
        _save(p, log, rows, "assumption sheet confirmed")
    return {"confirmed": [r["id"] for r in rows]}


def write_workbook(req, ctx, p, log) -> dict:
    """``harness-model workbook [--out FILE]``: the hook builds the deliverable workbook from work/model.json and
    the recorded assumptions (Assumptions, a protected Calc sheet of formulas, Checks, Summary)."""
    from . import model, templates, xlsx
    from .gate import _literal_dir
    out = req.get("out") or "forecast.xlsx"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}\.xlsx", out):
        raise Denied("--out is a file name ending in .xlsx, inside the task's work folder")
    work = _literal_dir(p, p.work)
    if work is None:
        raise Denied("the task has no work folder yet")
    try:
        m = json.loads(safe_open(work / (req.get("model") or "model.json"), [work]))
    except Exception as e:
        raise Denied(f"work/{req.get('model') or 'model.json'} cannot be read: {e}") from e
    rows = {r["id"]: r for r in _rows(p, log)}
    try:
        sheets = model.workbook_sheets(m, rows, templates.language(ctx))
    except (KeyError, ValueError, TypeError) as e:
        raise Denied(f"the model cannot be built: {e}") from e
    if (work / out).is_symlink():
        raise Denied(f"work/{out} is a symlink; nothing was written")
    atomic_write(work / out, xlsx.to_bytes(sheets))
    return {"workbook": out}


def register(req, ctx, p, log) -> dict:
    from . import gate
    from .gatehook import load_key
    v = gate.verify_receipt(p, log, load_key(ctx.data_root))
    if not v["ok"]:
        raise Denied(f"a model is registered after a receipt that verifies ({v['reason']})")
    name = req.get("deliverable") or "model.json"
    sub = p.run / "outputs" / "submitted"
    try:
        data = safe_open(sub / name, [sub])
    except Exception as e:
        raise Denied(f"{name} is not a submitted deliverable: {e}") from e
    models = registry(ctx)
    parent = store.read_json(p.run, log, "scenario.json", None) or {}
    entry = {"model_id": p.job, "version": int((models.get(p.job) or {}).get("version", 0)) + 1,
             "input_hash": canon.sha256_json(_rows(p, log)), "assumptions_ref": f"runs/{p.job}/assumptions.json",
             "deliverable_path": name, "deliverable_sha": canon.sha256_bytes(data), "scenario_history": [],
             "ts": now(), "state": v["state"]}
    models[p.job] = entry
    if parent.get("scenario_of") in models:
        models[parent["scenario_of"]]["scenario_history"].append(
            {"job": p.job, "set": parent.get("set"), "state": v["state"], "ts": now()})
    _write_registry(ctx, models)
    log.append("MODEL_REGISTERED", {"model_id": p.job, "version": entry["version"]})
    return {"registered": p.job, "version": entry["version"]}


def _done_parent(ctx, evt):
    from . import sessionstate
    st = sessionstate.load(ctx, evt.get("session_id"))
    parent = st.get("active_task")
    row = sessionstate.jobs(ctx).get(parent or "", {})
    if not parent or row.get("state") != "DONE":
        raise Denied("a scenario starts from this session's finished model (DONE)")
    if parent not in registry(ctx):
        raise Denied(f"{parent} is not registered: harness-model register after its gate")
    return parent


def scenario(req, ctx, evt, delta=None, note=None) -> dict:
    from . import compile as compile_
    from .record import _cards
    parent = _done_parent(ctx, evt)
    delta = delta if delta is not None else req.get("set")
    if not isinstance(delta, dict) or not delta:
        raise Denied("--set ID=VALUE names at least one assumption")
    pp = job_paths(ctx.data_root, ctx.workspace, parent)
    plog = EventLog(pp.run, pp.heads)
    base = {r["id"]: r for r in _rows(pp, plog)}
    delta = {k: _value(v) for k, v in delta.items()}  # every value is checked before any job is opened
    unknown = sorted(set(delta) - set(base))
    if unknown:
        raise Denied(f"{unknown} are not assumptions of {parent}: a structural change is a new task with "
                     f"follows: {parent}")
    n = 1 + sum(1 for j in __import__("os").listdir(Path(ctx.data_root) / "runs") if j.startswith(parent + ".s"))
    child = f"{parent}.s{n}"
    card = dict((_cards(pp, plog) or [{}])[-1])
    reg = registry(ctx)[parent]
    draft = {k: card[k] for k in ("task_type", "deliverable", "oracle", "reliance", "decision_class", "cost_of_error",
                                  "risk", "units", "unit_definition", "domain", "provenance") if k in card}
    draft["unit_list"] = [{k: u[k] for k in ("label", "claim", "check_id", "deliverable", "unit_type") if k in u}
                          for u in card.get("unit_list") or []]
    desc = ", ".join(f"{k} = {v}" for k, v in sorted(delta.items()))
    draft["deliverable"] = dict(draft.get("deliverable") or {"kind": "model"},
                                description=f"scenario of {parent}: {desc}"[:200])
    out = compile_.apply({"tool": "compile", "card": draft}, ctx, evt, scenario_parent=parent, job_id=child)
    cp = job_paths(ctx.data_root, ctx.workspace, child)
    clog = EventLog(cp.run, cp.heads)
    rows = []
    for r in base.values():
        if r["id"] in delta:
            r = dict(r, value=delta[r["id"]], source="scenario", owner_confirmed=False, scenario_delta=True,
                     stated_by_owner=_stated_by_owner(ctx, delta[r["id"]]))
        rows.append(r)
    _save(cp, clog, rows, f"scenario of {parent}")
    store.write_json(cp.run, clog, "scenario.json", {"scenario_of": parent, "set": delta, "note": note},
                     "SCENARIO", {"scenario_of": parent})
    sub = pp.run / "outputs" / "submitted" / reg["deliverable_path"]
    cp.work.mkdir(parents=True, exist_ok=True)
    (cp.work / reg["deliverable_path"]).write_bytes(safe_open(sub, [pp.run]))
    return dict(out, scenario_of=parent, set=delta)


def _sheet_rows(data: bytes) -> list:
    """Rows of a returned sheet as dicts: a CSV ``id,value[,...]`` or the delivered workbook's Assumptions sheet."""
    if data[:2] == b"PK":
        import os
        import tempfile
        from . import tables
        fd, tmp = tempfile.mkstemp(suffix=".xlsx")
        try:
            os.write(fd, data)
            os.close(fd)
            headers, body = tables.read(tmp)
        finally:
            os.unlink(tmp)
        return [{h: (str(v) if v is not None else "") for h, v in zip(headers, r)} for r in body]
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def import_sheet(req, ctx, evt) -> dict:
    from .run import _input, _written
    parent = _done_parent(ctx, evt)
    src, data = _input(ctx, req.get("file"), _written(ctx, evt.get("session_id")))
    reg = registry(ctx)[parent]
    if canon.sha256_bytes(data) == reg["deliverable_sha"]:
        return {"unchanged": True, "parent": parent}
    try:
        rows = _sheet_rows(data)
    except (UnicodeDecodeError, csv.Error, ValueError, KeyError) as e:
        raise Denied(f"{src} is not an assumption sheet (CSV id,value or the delivered workbook): {e}") from e
    if not rows or set(rows[0]) - {"id", "value", "unit", "period", "source", "label"} or "value" not in rows[0]:
        return {"structural": True, "parent": parent}
    pp = job_paths(ctx.data_root, ctx.workspace, parent)
    base = {r["id"]: r for r in _rows(pp, EventLog(pp.run, pp.heads))}
    from .localize import parse_amount
    delta, lists = {}, {}
    for r in rows:
        m = re.fullmatch(r"([a-z][a-z0-9_]*)\.(\d{1,2})", str(r.get("id") or ""))
        if m and m.group(1) in base and isinstance(base[m.group(1)]["value"], list):
            v = parse_amount(r["value"])  # one factor of a seasonality row
            if v is None or not 1 <= int(m.group(2)) <= 12:
                raise Denied(f"{r['id']}: {r['value']!r} is not a monthly factor")
            lists.setdefault(m.group(1), list(base[m.group(1)]["value"]))[int(m.group(2)) - 1] = float(v)
            continue
        if r.get("id") not in base:
            if str(r.get("source")) == "model":  # a literal of model.json shown on the sheet: not an assumption
                continue
            return {"structural": True, "parent": parent}
        v = parse_amount(r["value"])
        if v is None:
            raise Denied(f"{r['id']}: {r['value']!r} is not a number")
        v = int(v) if v == v.to_integral_value() else float(v)
        if v != base[r["id"]]["value"]:
            delta[r["id"]] = v
    delta.update({k: v for k, v in lists.items() if v != base[k]["value"]})
    if not delta:
        return {"unchanged": True, "parent": parent}
    return scenario({}, ctx, evt, delta=delta, note=f"imported from {src}")


def apply(req, ctx, evt) -> dict:
    sub = req.get("sub")
    if sub in ("scenario", "import"):
        return scenario(req, ctx, evt) if sub == "scenario" else import_sheet(req, ctx, evt)
    job = getattr(ctx, "job", None)
    if not job:
        raise Denied("harness-model assume and register belong to an open task")
    from . import lease
    lease.require(ctx, job, evt.get("session_id"))
    p = job_paths(ctx.data_root, ctx.workspace, job)
    log = EventLog(p.run, p.heads)
    if sub == "assume":
        return assume(req, ctx, p, log)
    if sub == "register":
        return register(req, ctx, p, log)
    if sub == "workbook":
        return write_workbook(req, ctx, p, log)
    raise Denied("harness-model supports: assume, register, scenario, import")


def say(res) -> str:
    if "assumed" in res:
        flag = " It came after half the work, so it is flagged in the report." if res["late"] else ""
        conf = "confirmed by the owner's own words" if res["owner_confirmed"] else "not yet confirmed by the owner"
        return f"harness: assumption {res['assumed']} recorded ({conf}).{flag}"
    if "workbook" in res:
        return (f"harness: work/{res['workbook']} written from the model (Assumptions, Calc formulas, Checks, Summary). "
                f"Run harness-run model.workbook --arg workbook={res['workbook']} before the gate.")
    if "registered" in res:
        return f"harness: model {res['registered']} registered (version {res['version']})."
    if res.get("unchanged"):
        return f"harness: the returned file matches what {res['parent']} delivered; nothing to rerun."
    if res.get("structural"):
        return (f"harness: the returned file changes more than assumption values: draft a new card with "
                f"follows: {res['parent']} and task_type simulation.")
    if "scenario_of" in res:
        return (f"harness: scenario {res['job_id']} of {res['scenario_of']} opened at {res['tier']} with "
                f"{json.dumps(res['set'])}. Print this opening line verbatim, then run model.recalc and model.checks "
                f"in .claude/loops/{res['job_id']}/work/: {res['opening']}")
    return "harness: model request recorded"
