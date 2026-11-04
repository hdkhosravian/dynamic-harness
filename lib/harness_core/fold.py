"""Project harness rows into the gate's input files (spec §Gate adapter)."""
from __future__ import annotations

import ast
import math

from . import canon, schema
from .errors import HarnessError
from .ledger import FILES, OUTPUTS, ROW_SCHEMA
from .safeio import atomic_write

DONE_STATES = {"PASS", "BLOCKED", "SUPPORTED", "REFUTED", "NOT_INVESTIGATED"}


def _map(row: dict, unit: dict):
    """Harness row -> gate row, or None for states with no gate row (FAIL)."""
    v, uid, n = row["verdict"], row["id"], row["attempt"]
    blobs = [f"outputs/blobs/{s}" for s in row.get("outputs") or []]
    check = f"harness-run {unit.get('check_id')} --unit {uid} --attempt {n}"
    research = f"research.check --unit {uid} --attempt {n}"
    if v == "PASS":
        g = {"verdict": "PASS", "evidence": blobs, "proof_cmd": check, "fix_verdict": "PASS"}
        if unit.get("deliverable"):
            g["fix"] = f"outputs/submitted/{unit['deliverable']}"
        else:
            g.update(fix="none", no_change_reason="no deliverable registered")
    elif v == "PASS_FLAGGED":
        g = {"verdict": "PARTIAL", "missing": row["failure_sig"], "evidence": blobs, "proof_cmd": check}
    elif v == "UNVERIFIED":
        g = {"verdict": "PARTIAL", "missing": "no executable check", "evidence": row.get("evidence") or [],
             "proof_cmd": f"harness-critic verify --unit {uid} --attempt {n}"}
    elif v in ("BLOCKED", "NOT_INVESTIGATED"):
        g = {"verdict": "BLOCKED", "blocked_reason": row.get("reason") or v.lower()}
    elif v in ("SUPPORTED", "REFUTED"):
        # the research.check outputs stay in the evidence: the done-mode oracle replays them
        g = {"verdict": "PASS", "finding": row.get("finding") or v, "evidence": list(dict.fromkeys(
             (row.get("evidence") or []) + blobs)),
             "proof_cmd": research, "fix": "none", "no_change_reason": "investigation verdict"}
    elif v == "INCONCLUSIVE":
        g = {"verdict": "PARTIAL", "missing": f"inconclusive: {row.get('reason') or 'undecided'}",
             "evidence": row.get("evidence") or blobs, "proof_cmd": research}
    else:
        return None
    g["id"] = uid
    if unit.get("unit_type"):
        g["unit_type"] = unit["unit_type"]
    return g


_OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}
_FUNCS = {"ceil": math.ceil, "floor": math.floor}


def _num(node, units):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id == "units":
        return units
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_num(node.left, units), _num(node.right, units))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and len(node.args) == 1 and not node.keywords):
        return _FUNCS[node.func.id](_num(node.args[0], units))
    raise HarnessError(f"allow_blocked: unsupported expression {ast.dump(node)[:80]}")


def allow_blocked(expr, units: int) -> int:
    """Profile allow_blocked: an int, or an expression over `units` with + - * /, ceil() and floor()
    (e.g. "units", "ceil(0.2*units)"). Evaluated by walking the AST; never eval()."""
    if type(expr) is int:
        n = expr
    elif isinstance(expr, str):
        try:
            tree = ast.parse(expr.strip(), mode="eval")
        except SyntaxError as e:
            raise HarnessError(f"allow_blocked: cannot parse {expr!r}") from e
        try:
            n = _num(tree.body, units)
        except ZeroDivisionError as e:
            raise HarnessError(f"allow_blocked: {expr!r} divides by zero") from e
    else:
        raise HarnessError(f"allow_blocked must be an int or an expression, not {expr!r}")
    if type(n) is not int or n < 0:
        raise HarnessError(f"allow_blocked {expr!r} gives {n!r}, not a whole number >= 0")
    return n


def _lines(rows) -> bytes:
    return b"".join(canon.canonical_json(r) + b"\n" for r in rows)


def _claims(paths, log, units) -> list:
    """The claims the gate checks: every row a unit cites (uncited rows support nothing), plus, per unit, its
    load-bearing rows under one key, so check_claims needs two independent origins per unit's finding."""
    from . import store
    raw = store.read_jsonl(paths.run, log, "claims.jsonl")
    latest = {}
    for c in store.read_jsonl(paths.run, log, "cites.jsonl"):
        if c.get("unit") in units:
            latest[c["unit"]] = c
    out, seen = [], set()
    for uid, c in sorted(latest.items()):
        for n in c.get("claims") or []:
            if 1 <= n <= len(raw) and n not in seen:
                seen.add(n)
                out.append(dict(raw[n - 1], load_bearing=False))
        for n in c.get("load_bearing") or []:
            if 1 <= n <= len(raw):
                out.append(dict(raw[n - 1], load_bearing=True, claim=f"[{uid}] {units[uid].get('claim')}"))
    return out


def check_protected(paths, log) -> None:
    """R-6 detection: verify the chain, then every file a writer event recorded plus the
    row files (catches planted ones). Raises TamperError."""
    log.verify()
    keys = {e["file"] for e in log.events() if "file" in e} | set(FILES.values()) | {OUTPUTS}
    pins = f"pins/{paths.job}.json"
    for k in sorted(keys):
        log.check_file(paths.data_root / k if k == pins else paths.run / k, k)


def project(paths, ledger, scope=None, profile=None) -> dict:
    log = ledger.log
    check_protected(paths, log)
    units = ledger.sanctioned_scope()
    if scope is not None and list(scope) != units:
        raise HarnessError("scope passed to project() differs from the latest sanctioned scope")
    by_id = {u["id"]: u for u in units}
    history = [r for k, r in ledger.rows() if k in ROW_SCHEMA and r["id"] in by_id]
    latest = {}
    for r in history:
        latest[r["id"]] = r
    failing = sorted(u for u, r in latest.items() if r["verdict"] == "FAIL")
    if failing:
        raise HarnessError(f"gate refused: latest verdict is FAIL for {failing}")

    rows = [_map(latest[u["id"]], u) for u in units if u["id"] in latest]
    mapped = [g for g in (_map(r, by_id[r["id"]]) for r in history) if g is not None]
    profile = dict(profile or {})
    allow = allow_blocked(profile.get("allow_blocked", 0), len(units))
    blocked = sum(1 for g in rows if g["verdict"] == "BLOCKED")
    states = [latest[u["id"]]["verdict"] if u["id"] in latest else "OPEN" for u in units]
    done = (all(s in DONE_STATES for s in states) and blocked <= allow
            and any(g["verdict"] == "PASS" for g in rows))
    mode = "done" if done else "known"
    if mode == "known":
        allow = len(units)

    gate = paths.run / "gate"
    scope_data = canon.scope_bytes(units)
    files = {
        "ledger.jsonl": _lines(rows),
        "verdicts.jsonl": _lines(mapped),
        "scope.jsonl": scope_data,
        "profile.json": canon.canonical_json(profile),
        "decisions.jsonl": _lines(r for k, r in ledger.rows() if k == "decision"),
    }
    if (paths.run / "claims.jsonl").exists():
        files["claims.jsonl"] = _lines(_claims(paths, log, by_id))
    for name, data in files.items():
        atomic_write(gate / name, data)
    pins = {"scope_sha": canon.sha256_bytes(scope_data), "scope_count": len(units)}
    schema.check(pins, "pins")
    pins_path = paths.data_root / "pins" / f"{paths.job}.json"
    atomic_write(pins_path, canon.canonical_json(pins))
    log.append("PROJECTED", {"mode": mode, "allow_blocked": allow, **pins,
                             "files": {f"gate/{k}": canon.sha256_bytes(v) for k, v in files.items()}},
               file=f"pins/{paths.job}.json", file_sha=canon.sha256_bytes(canon.canonical_json(pins)))
    return {"mode": mode, "rows": rows, "allow_blocked": allow, "remaining": [u["id"] for u in units
            if u["id"] not in latest], **pins}
