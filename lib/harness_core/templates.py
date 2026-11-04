"""User-facing text (spec §User-facing templates and localisation; build phase P5).

``templates/en.json`` and ``templates/fa.json`` hold ``{moment_id: text}``; the Persian file is written natively,
never machine-translated. ``render(key, ctx, **values)`` picks the language:

1. the business profile's ``language`` (``/dynamic-harness:business set language fa``);
2. else the language of the owner's latest turn (mostly Arabic-script letters: Persian);
3. else English.

A key missing in Persian falls back to English. Numbers passed as values are written with Persian digits in
Persian text; strings are left alone (they may hold codes, paths or quotes).
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "templates"
LANGS = ("en", "fa")
_FA_DIGITS = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")
_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")


def load(lang) -> dict:
    f = ROOT / f"{lang}.json"
    return json.loads(f.read_text("utf-8")) if f.is_file() else {}


def language(ctx=None) -> str:
    try:
        from . import business
        lang = business.profile(ctx).get("language") if ctx is not None and getattr(ctx, "data_root", None) else None
    except Exception:  # an unreadable profile never breaks a message
        lang = None
    if lang in LANGS:
        return lang
    try:
        from .turns import Turns
        last = Turns(ctx.data_root).latest() if ctx is not None and getattr(ctx, "data_root", None) else None
    except Exception:
        last = None
    text = (last or {}).get("text_norm") or ""
    return "fa" if len(_ARABIC.findall(text)) > len(_LATIN.findall(text)) else "en"


def _fmt(v, lang):
    if lang == "fa" and isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return f"{v:,}".replace(",", "٬").translate(_FA_DIGITS)
    return v


def render(key, ctx=None, lang=None, **values) -> str:
    lang = lang or language(ctx)
    table = load(lang) if lang in LANGS else {}
    text = table.get(key)
    if text is None:
        lang, text = "en", load("en").get(key, key)
    return text.format(**{k: _fmt(v, lang) for k, v in values.items()}) if values else text
