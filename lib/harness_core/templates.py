"""User-facing text (spec §User-facing templates and localisation; build phase P5).

Every ``templates/<lang>.json`` holds ``{moment_id: text}`` in one language, written natively, never
machine-translated; the files present are the languages (``langs()``). ``render(key, ctx, **values)`` picks the
language:

1. the business profile's ``language`` (``/dynamic-harness:business set language <lang>``);
2. else the language whose script ranges (locale packs, ``render.<lang>.script``) hold most letters of the
   owner's latest turn;
3. else the base language.

A key missing in a language falls back to the base language. Numbers passed as values are written with the digits
and marks of ``render.<lang>``; strings are left alone (they may hold codes, paths or quotes).
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from . import locales
from .errors import HarnessError

ROOT = Path(__file__).resolve().parents[2] / "templates"


def langs() -> list:
    """The stems of the ``*.json`` files in ``ROOT``, the base language first, the others sorted."""
    if not ROOT.is_dir():
        return []
    found = sorted(n for n in (f.name[:-len(".json")] for f in ROOT.iterdir() if f.name.endswith(".json"))
                   if re.fullmatch(locales.ID, n))
    return sorted(found, key=lambda x: x != locales.BASE)


def load(lang) -> dict:
    f = ROOT / f"{lang}.json"
    return json.loads(f.read_text("utf-8")) if f.is_file() else {}


def _data(ctx, key="render") -> dict:
    """``key`` (``render``, ``currencies``) of every installed pack (the profile list ``locales`` does not choose how
    a language is written or what a currency is called); the shipped packs when the live view cannot be read (text
    for the owner is not a gate, above all the message that reports a broken file)."""
    try:
        return locales.view(ctx, every=True).get(key, {})
    except (HarnessError, OSError):
        return locales.view(None, every=True).get(key, {})


def language(ctx=None, _given=None) -> str:
    try:
        from . import business
        lang = business.profile(ctx).get("language") if ctx is not None and getattr(ctx, "data_root", None) else None
    except Exception:  # an unreadable profile never breaks a message
        lang = None
    names = langs()
    if lang in names:
        return lang
    try:
        from .turns import Turns
        last = Turns(ctx.data_root).latest() if ctx is not None and getattr(ctx, "data_root", None) else None
    except Exception:
        last = None
    text = (last or {}).get("text_norm") or ""
    if not text:
        return locales.BASE
    data = _data(ctx) if _given is None else _given
    counts = {n: sum(1 for c in text if any(r[0] <= c <= r[1] for r in data[n]["script"]))
              for n in names if data.get(n, {}).get("script")}
    best = max(counts.values(), default=0)
    tied = [n for n, k in counts.items() if k == best]
    return locales.BASE if not best else tied[0]


def _fmt(v, spec):
    number = isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
    if number and spec.keys() & {"digits", "group", "decimal"}:
        marks = {",": spec.get("group", ","), ".": spec.get("decimal", ".")}
        marks.update(zip("0123456789", spec.get("digits", "0123456789")))
        return f"{v:,}".translate(str.maketrans(marks))
    return v


def _render(key, lang, data, values) -> str:
    table = load(lang) if lang in langs() else {}
    text = table.get(key)
    if text is None:
        lang, text = locales.BASE, load(locales.BASE).get(key, key)
    spec = data.get(lang, {})
    return text.format(**{k: _fmt(v, spec) for k, v in values.items()}) if values else text


def render(key, ctx=None, lang=None, **values) -> str:
    if not values:  # no number to write: only the language guess may need pack data
        return _render(key, lang or language(ctx), {}, values)
    data = _data(ctx)
    return _render(key, lang or language(ctx, data), data, values)


def currency(code, lang, ctx=None) -> str:
    """The name of ``code`` in ``lang`` (``currencies.<code>.names`` of the packs), else the code itself: a name in
    another language would read worse inside the owner's sentence than the code."""
    if not code:
        return ""
    return _data(ctx, "currencies").get(code, {}).get("names", {}).get(lang, code)


def state_line(state, ctx=None, lang=None) -> str:
    """The receipt's R-10 state (computed in English) in the owner's language."""
    if lang == locales.BASE or not state:
        return state
    data = _data(ctx)
    lang = lang or language(ctx, data)
    if lang == locales.BASE:
        return state
    if state == "Checked":
        return _render("state.checked", lang, data, {})
    m = re.fullmatch(r"Partly done: (\d+) of (\d+)", state)
    if m:
        return _render("state.partly", lang, data, {"done": int(m.group(1)), "total": int(m.group(2))})
    m = re.fullmatch(r"Done, not fully checked: (.*)", state, re.S)
    if m:
        table = load(lang)
        reasons = [x.strip() for x in m.group(1).split(";")]
        what = data.get(lang, {}).get("joiner", "; ").join(
            table.get("reason." + re.sub(r"[^a-z0-9]+", "_", x.lower()).strip("_"), x) for x in reasons)
        return _render("state.not_fully", lang, data, {"what": what})
    return state


def note(entry, text, ctx=None, lang=None) -> str:
    """A check's note in the owner's language: ``entry`` is its ``{code, params}``; ``text`` the English."""
    if lang == locales.BASE or not isinstance(entry, dict):
        return text
    data = _data(ctx)
    lang = lang or language(ctx, data)
    if lang == locales.BASE or f"note.{entry.get('code')}" not in load(lang):
        return text
    digits = data.get(lang, {}).get("digits")
    tr = str.maketrans("0123456789", digits) if digits else {}
    params = {k: (v.translate(tr) if isinstance(v, str) and k != "b" else v) for k, v in (entry.get("params") or {}).items()}
    try:
        return _render(f"note.{entry['code']}", lang, data, params)
    except (KeyError, IndexError, ValueError):  # a template that does not fit the params: the English note
        return text
