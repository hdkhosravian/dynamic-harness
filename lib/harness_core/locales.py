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
import unicodedata
from pathlib import Path

from . import protected
from .errors import LocaleError, SchemaError, TamperError
from .safeio import safe_open
from .schema import validate

BASE = "en"               # the base pack: always installed, always active
MAX_BYTES = 256 * 1024    # size limit of one pack file
CONTRACT = Path(__file__).resolve().parents[2] / "schema" / "control" / "locale.json"
HEADER = ("schema", "id", "name")
ID = r"[a-z0-9_-]{1,32}"  # the form of a pack id and of a language code
CODE = r"[A-Z]{3}"        # the form of a currency code
# supplied by every installed pack, active or not (a file or the owner can name any currency, whatever the profile
# list ``locales`` says, and a currency entry acts only on the codes it names)
EVERY = ("render", "state_lexicon", "question_marks", "fold", "currencies", "currency_pairs")


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
    except RecursionError as e:
        raise LocaleError(f"locale pack {f}: nested too deep") from e
    try:
        validate(pack, json.loads(CONTRACT.read_text("utf-8")))
        _check_render(pack.get("render", {}))
        _check_words(pack)
        _check_fold(pack.get("fold", {}).get("letters", {}))
        _check_currencies(pack)
        merge([(f.name, pack)])  # one walk now, so a pack too deep to merge is named here, not in view()
    except (SchemaError, LocaleError) as e:
        raise LocaleError(f"locale pack {f}: {e}") from e
    stem = f.name[:-len(".json")]
    if not re.fullmatch(ID, stem) or pack["id"] != stem:
        raise LocaleError(f"locale pack {f}: the name must be <id>.json, the id 1 to 32 of a-z 0-9 _ -")
    return pack


def _check_render(render: dict) -> None:
    """What the schema cannot say about ``render``: the code form, exact lengths, ordered ranges, no empty rule."""
    for lang, spec in render.items():
        if not re.fullmatch(ID, lang):
            raise LocaleError(f"render: the language code {lang!r} is not 1 to 32 of a-z 0-9 _ -")
        for key, n in (("digits", 10), ("group", 1), ("decimal", 1)):
            if key in spec and len(spec[key]) != n:
                raise LocaleError(f"render.{lang}.{key}: must be exactly {n} characters")
        if "joiner" in spec and not 1 <= len(spec["joiner"]) <= 8:
            raise LocaleError(f"render.{lang}.joiner: must be 1 to 8 characters")
        for r in spec.get("script", []):
            if len(r) != 2 or r[0] > r[1]:
                raise LocaleError(f"render.{lang}.script: {r!r} is not a range of 2 characters, first <= last")
        for rule in spec.get("lint", []):
            if not rule["chars"] or not rule["message"]:
                raise LocaleError(f"render.{lang}.lint: chars and message must not be empty")


def _check_words(pack: dict) -> None:
    """What the schema cannot say about ``words``, ``state_lexicon`` and ``question_marks``: no empty entry, a mark
    is one character."""
    lists = [(f"state_lexicon.{k}", v) for k, v in pack.get("state_lexicon", {}).items()]
    for k, v in pack.get("words", {}).items():
        lists += [(f"words.{k}.{c}", x) for c, x in v.items()] if isinstance(v, dict) else [(f"words.{k}", v)]
    for name, v in lists:
        if "" in v:
            raise LocaleError(f"{name}: an entry is empty")
    for m in pack.get("question_marks", []):
        if len(m) != 1:
            raise LocaleError(f"question_marks: {m!r} is not exactly 1 character")


def _check_fold(letters: dict) -> None:
    """What the schema cannot say about ``fold.letters``: one letter to another, the key not ASCII, no chain."""
    for k, v in letters.items():
        for c in (k, v):
            if len(c) != 1 or not unicodedata.category(c).startswith("L"):
                raise LocaleError(f"fold.letters: {c!r} is not exactly 1 letter")
        if k.isascii():
            raise LocaleError(f"fold.letters: the key {k!r} is an ASCII character")
        if k == v:
            raise LocaleError(f"fold.letters: {k!r} folds to itself")
        if v in letters:
            raise LocaleError(f"fold.letters: {v!r} is a value and a key (a chain)")


def _check_currencies(pack: dict) -> None:
    """What the schema cannot say about ``currencies`` and ``currency_pairs``: the code and language code forms, at
    most 18 decimals, no empty name, two different codes in a pair, the same two codes once in a pack."""
    for code, spec in pack.get("currencies", {}).items():
        if not re.fullmatch(CODE, code):
            raise LocaleError(f"currencies.{code}: a currency code is exactly 3 letters A-Z")
        if spec.get("decimals", 0) > 18:
            raise LocaleError(f"currencies.{code}.decimals: must be 0 to 18")
        for lang, name in spec.get("names", {}).items():
            if not re.fullmatch(ID, lang):
                raise LocaleError(f"currencies.{code}.names: the language code {lang!r} is not 1 to 32 of a-z 0-9 _ -")
            if not name:
                raise LocaleError(f"currencies.{code}.names.{lang}: the name is empty")
    seen = {}
    for i, e in enumerate(pack.get("currency_pairs", [])):
        for side in ("from", "to"):
            if not re.fullmatch(CODE, e[side]):
                raise LocaleError(f"currency_pairs[{i}].{side}: {e[side]!r} is not a code of exactly 3 letters A-Z")
        if e["from"] == e["to"]:
            raise LocaleError(f"currency_pairs[{i}]: from and to are the same code")
        two = frozenset((e["from"], e["to"]))
        if two in seen:
            raise LocaleError(f"currency_pairs[{i}]: the same two codes as currency_pairs[{seen[two]}]")
        seen[two] = i


def _read(d: Path) -> dict:
    if not d.is_dir():
        return {}
    return {f.name[:-len(".json")]: _pack(f) for f in sorted(d.iterdir()) if f.name.endswith(".json")}


def merge(packs) -> dict:
    """Merge ``[(pack_id, dict)]`` in order: objects recurse, a later list adds its items not yet present, the first
    scalar wins."""
    out, first = {}, {}
    try:
        for pid, pack in packs:
            _into(out, pack, pid, first, "")
    except RecursionError as e:
        raise LocaleError(f"locale packs {', '.join(pid for pid, _ in packs)}: nested too deep") from e
    return out


def _kind(v) -> str:
    return "object" if isinstance(v, dict) else "list" if isinstance(v, list) else "scalar"


def _into(out, new, pid, first, path) -> None:
    for k, v in new.items():
        key = path + k
        if k not in out:  # the first list is kept as written; a later one adds only what is not there yet
            out[k] = {} if isinstance(v, dict) else copy.deepcopy(v)
            first[key] = pid
            if not isinstance(v, dict):
                continue
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
        lang = prof.get("language")
        if isinstance(lang, str):  # the packs that say how the profile language is written keep its words
            out += [i for i in sorted(ids) if i not in out and lang in ids[i].get("render", {})]
        return out if BASE in out else out + [BASE]
    return [BASE] + sorted(i for i in ids if i != BASE)  # the 0.4.0 behaviour: every pack applies


def active(ctx=None) -> list:
    """The active pack ids in merge order: the profile list ``locales``, then the packs that hold ``render.<profile
    language>``, then the base pack; with no list in force, every pack."""
    return _order(ctx, installed(ctx))


def view(ctx=None, every=False) -> dict:
    """The merged content of the active packs, without the header keys; ``every=True`` then adds the keys named in
    ``EVERY`` of the other installed packs, sorted by id."""
    packs = installed(ctx)
    order = _order(ctx, packs)
    parts = [(i, {k: v for k, v in packs[i].items() if k not in HEADER}) for i in order]
    if every:
        parts += [(i, {k: v for k, v in packs[i].items() if k in EVERY}) for i in sorted(packs) if i not in order]
    return merge(parts)
