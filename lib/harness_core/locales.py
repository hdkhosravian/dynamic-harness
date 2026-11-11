"""Locale packs (``harness/locale@1``, build phase P1): words, rendering and regional facts as data.

Shipped packs are ``control/defaults/locales/<id>.json``; live packs are ``${CLAUDE_PLUGIN_DATA}/control/locales/
<id>.json`` and are merged on top of a shipped pack of the same id (they can add, never remove). Code reads
``view(ctx)``, the merge of the active packs in the order of ``active(ctx)``. An invalid pack raises LocaleError,
so the hooks fail closed.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from . import protected
from .errors import LocaleError, SchemaError, TamperError
from .safeio import safe_open
from .schema import validate

BASE = "en"               # the base pack: always installed, always active
MAX_BYTES = 256 * 1024    # size limit of one pack file
CONTRACT = Path(__file__).resolve().parents[2] / "schema" / "control" / "locale.json"
HEADER = ("schema", "id", "name")


def _pack(f: Path) -> dict:
    try:
        data = safe_open(f, [f.parent])
    except TamperError as e:  # a symlink, a directory, a hard link
        raise LocaleError(f"locale pack {f}: not a regular file ({e})") from e
    if len(data) > MAX_BYTES:
        raise LocaleError(f"locale pack {f}: larger than {MAX_BYTES} bytes")
    try:
        pack = json.loads(data.decode("utf-8"))
    except ValueError as e:
        raise LocaleError(f"locale pack {f}: not UTF-8 JSON ({e})") from e
    try:
        validate(pack, json.loads(CONTRACT.read_text("utf-8")))
    except SchemaError as e:
        raise LocaleError(f"locale pack {f}: {e}") from e
    stem = f.name[:-len(".json")]
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", stem) or pack["id"] != stem:
        raise LocaleError(f"locale pack {f}: the name must be <id>.json, the id 1 to 32 of a-z 0-9 _ -")
    return pack


def _read(d: Path) -> dict:
    if not d.is_dir():
        return {}
    return {f.name[:-len(".json")]: _pack(f) for f in sorted(d.iterdir()) if f.name.endswith(".json")}


def merge(packs) -> dict:
    """Merge ``[(pack_id, dict)]`` in order: objects recurse, lists append new items, the first scalar wins."""
    out, first = {}, {}
    for pid, pack in packs:
        _into(out, pack, pid, first, "")
    return out


def _kind(v) -> str:
    return "object" if isinstance(v, dict) else "list" if isinstance(v, list) else "scalar"


def _into(out, new, pid, first, path) -> None:
    for k, v in new.items():
        key = path + k
        if k not in out:
            out[k] = {} if isinstance(v, dict) else [] if isinstance(v, list) else v
            first[key] = pid
        old = out[k]
        if _kind(old) != _kind(v):
            raise LocaleError(f"locale packs {first[key]!r} and {pid!r} give {key} a different kind of value "
                              f"({_kind(old)}, {_kind(v)})")
        if isinstance(v, dict):
            _into(old, v, pid, first, key + ".")
        elif isinstance(v, list):
            for x in v:
                if x not in old:
                    old.append(copy.deepcopy(x))


# shortcut: reads the pack files on every call, cache by file time if a hook gets slow
def installed(ctx=None) -> dict:
    """pack_id -> validated pack: the shipped packs, then the live packs merged on top."""
    packs = _read(protected.DEFAULTS_DIR / "locales")
    if BASE not in packs:
        raise LocaleError(f"the base locale pack {BASE!r} is not among the shipped packs")
    data_root = getattr(ctx, "data_root", None)
    if data_root:
        for pid, pack in _read(Path(data_root) / "control" / "locales").items():
            packs[pid] = merge([(pid, packs[pid]), (pid, pack)]) if pid in packs else pack
    return packs


def _order(ctx, ids) -> list:
    from . import business
    prof = business.profile(ctx) if getattr(ctx, "data_root", None) else {}
    chosen = prof.get("locales")
    if isinstance(chosen, list) and chosen and all(isinstance(x, str) and x in ids for x in chosen):
        out = list(dict.fromkeys(chosen))
        return out if BASE in out else out + [BASE]
    return [BASE] + sorted(i for i in ids if i != BASE)  # the 0.4.0 behaviour: every pack applies


def active(ctx=None) -> list:
    """The active pack ids in merge order: the profile list ``locales`` plus the base pack, else every pack."""
    return _order(ctx, installed(ctx))


def view(ctx=None) -> dict:
    """The merged content of the active packs, without the header keys."""
    packs = installed(ctx)
    return merge([(i, {k: v for k, v in packs[i].items() if k not in HEADER}) for i in _order(ctx, packs)])
