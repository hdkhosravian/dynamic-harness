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


CURRENCY_FA = {"IRT": "تومان", "IRR": "ریال", "USD": "دلار", "EUR": "یورو", "GBP": "پوند", "AED": "درهم", "TRY": "لیر"}
REASON_FA = {"numbers_untraced": "برخی عددها به هیچ بررسی‌ای نمی‌رسند", "lexicon": "متن، بررسی را خودش تأیید کرده است",
             "known mode": "همهٔ بخش‌ها بررسی اجرایی نداشتند", "no executable check": "بررسی اجرایی نداشت"}


def currency(code, lang) -> str:
    from .business import CURRENCIES
    return (CURRENCY_FA if lang == "fa" else CURRENCIES).get(code, code or "")


def state_line(state, ctx=None, lang=None) -> str:
    """The receipt's R-10 state (computed in English) in the owner's language."""
    import re as _re
    lang = lang or language(ctx)
    if lang == "en" or not state:
        return state
    if state == "Checked":
        return render("state.checked", ctx, lang)
    m = _re.fullmatch(r"Partly done: (\d+) of (\d+)", state)
    if m:
        return render("state.partly", ctx, lang, done=int(m.group(1)), total=int(m.group(2)))
    m = _re.fullmatch(r"Done, not fully checked: (.*)", state, _re.S)
    if m:
        what = "؛ ".join(REASON_FA.get(x.strip(), x.strip()) for x in m.group(1).split(";"))
        return render("state.not_fully", ctx, lang, what=what)
    return state


def note(entry, text, ctx=None, lang=None) -> str:
    """A check's note in the owner's language: ``entry`` is its ``{code, params}``; ``text`` the English."""
    lang = lang or language(ctx)
    if lang == "en" or not isinstance(entry, dict) or f"note.{entry.get('code')}" not in load(lang):
        return text
    fa = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    params = {k: (v.translate(fa) if isinstance(v, str) and k != "b" else v) for k, v in (entry.get("params") or {}).items()}
    try:
        return render(f"note.{entry['code']}", ctx, lang, **params)
    except (KeyError, IndexError, ValueError):  # a template that does not fit the params: the English note
        return text
