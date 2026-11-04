"""Domain packs: registered check entrypoints (spec §Detailed plan 7 "Domain packs"; build phase P5).

A pack is a skill directory ``skills/<pack>/`` with a ``SKILL.md`` (approved like every skill, R-5) and a
``manifest.json``::

    {"schema": "harness/pack@1", "pack": "finance", "version": "1.0.0",
     "checks": {"finance.reconcile": {"entry": "checks/reconcile.py", "sha256": "<hex>",
                                      "task_types": ["data_question"], "run_files": [], "timeout": 120}}}

- A check id is ``<family>.<name>`` (``finance.reconcile``, ``doc.check``) and belongs to the one pack that
  registers it; two packs registering one id is an error.
- ``entry(check_id)`` returns the entry file only when it is a regular file inside the pack (no symlink on the
  way) whose sha256 equals the manifest's. The plugin tree hash (SessionStart) covers the same files; this
  re-check closes the window between the session check and the run.
- ``run_files`` names run-state files the check reads (copied read-only into its sandbox); only the names in
  ``RUN_FILES`` are allowed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from . import schema
from .errors import Denied, HarnessError

RUN_FILES = ("claims.jsonl", "fetch_log.jsonl", "snapshots", "subagents.jsonl", "renders.jsonl", "cites.jsonl",
             "assumptions.json", "card", "contradictions.jsonl", "search_log.jsonl", "scope")
TIMEOUT_MAX = 280  # < the 295 s watchdog of the harness-run hook
_ID = re.compile(r"[a-z][a-z0-9_-]*\.[a-z][a-z0-9_]*")
MANIFEST = {
    "type": "object", "additionalProperties": False, "required": ["schema", "pack", "version", "checks"],
    "properties": {
        "schema": {"enum": ["harness/pack@1"]}, "pack": {"type": "string"}, "version": {"type": "string"},
        "description": {"type": "string"},
        "checks": {"type": "object", "additionalProperties": {
            "type": "object", "additionalProperties": False, "required": ["entry", "sha256"],
            "properties": {"entry": {"type": "string"}, "sha256": {"type": "string"},
                           "task_types": {"type": "array", "items": {"type": "string"}},
                           "run_files": {"type": "array", "items": {"enum": list(RUN_FILES)}},
                           "timeout": {"type": "integer", "minimum": 1, "maximum": TIMEOUT_MAX},
                           "description": {"type": "string"}}}},
    },
}


def _root(ctx) -> Path:
    root = getattr(ctx, "plugin_root", None)
    if not root:
        raise HarnessError("plugin root unknown: packs cannot be loaded")
    return Path(os.path.realpath(root)) / "skills"


def manifests(ctx) -> dict:
    """``{pack: manifest}`` for every ``skills/*/manifest.json``. A malformed manifest raises."""
    out = {}
    base = _root(ctx)
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        f = d / "manifest.json"
        if d.is_symlink() or not f.exists():
            continue
        if f.is_symlink() or not f.is_file():
            raise HarnessError(f"{f}: a pack manifest must be a regular file")
        try:
            m = json.loads(f.read_text("utf-8"))
        except ValueError as e:
            raise HarnessError(f"{f}: not JSON ({e})") from e
        schema.validate(m, MANIFEST, f"pack {d.name}")
        if m["pack"] != d.name:
            raise HarnessError(f"{f}: pack {m['pack']!r} lives in directory {d.name!r}")
        for cid in m["checks"]:
            if not _ID.fullmatch(cid):
                raise HarnessError(f"{f}: check id {cid!r} must be <family>.<name>")
            owner = next((q for q, o in out.items() if cid in o["checks"]), None)
            if owner:
                raise HarnessError(f"{f}: check id {cid!r} is already registered by pack {owner!r}")
        out[m["pack"]] = m
    return out


def spec(ctx, check_id) -> dict:
    """The manifest entry of a registered check, plus its pack directory; Denied when not registered."""
    if not isinstance(check_id, str) or not _ID.fullmatch(check_id):
        raise Denied(f"{check_id!r} is not a check id (<pack>.<name>)")
    m = next((x for x in manifests(ctx).values() if check_id in x["checks"]), None)
    if m is None:
        raise Denied(f"check {check_id!r} is not registered in a pack manifest")
    return dict(m["checks"][check_id], pack=m["pack"], pack_version=m["version"], dir=str(_root(ctx) / m["pack"]))


def registered(ctx, check_id) -> bool:
    try:
        spec(ctx, check_id)
        return True
    except Denied:
        return False


def entry(ctx, check_id) -> Path:
    """The verified entry file of a registered check."""
    s = spec(ctx, check_id)
    pack = Path(s["dir"])
    cur = pack
    for part in Path(s["entry"]).parts:
        if part in ("..", "") or part.startswith("/"):
            raise HarnessError(f"{check_id}: entry {s['entry']!r} leaves the pack")
        cur = cur / part
        if cur.is_symlink():
            raise HarnessError(f"{check_id}: symlink on the entry path ({cur})")
    if not cur.is_file() or os.path.realpath(cur) != str(cur):
        raise HarnessError(f"{check_id}: entry {cur} is not a regular file in the pack")
    if hashlib.sha256(cur.read_bytes()).hexdigest() != s["sha256"]:
        raise HarnessError(f"{check_id}: entry {s['entry']} does not match its manifest hash")
    return cur


def hash_entries(pack_dir) -> dict:
    """Operator tool: the manifest with every entry's current sha256 (used when releasing a pack)."""
    pack_dir = Path(pack_dir)
    m = json.loads((pack_dir / "manifest.json").read_text("utf-8"))
    for c in m["checks"].values():
        c["sha256"] = hashlib.sha256((pack_dir / c["entry"]).read_bytes()).hexdigest()
    return m
