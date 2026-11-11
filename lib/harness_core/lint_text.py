"""``harness-lint-user-text``: the template lint (spec §User-facing templates and localisation; build phase P5).

It checks harness-authored text only (``templates/*.json``; keys under ``model.`` are hints to the model and are
skipped), never quoted owner content:

- no internal terms: card, gate, tier, oracle, outbox, BLOCKED, unit, ledger, hook, V0 to V3, T0 to T2;
- every language has every key of the base language, with the same ``{placeholders}``;
- the rules of the shipped locale packs: no character listed in ``render.<lang>.lint``, and in a right-to-left
  language no text starting with a left-to-right letter (it would render left to right).
"""
from __future__ import annotations

import re
import string
import unicodedata

from . import locales, templates

INTERNAL = re.compile(r"(?i)\b(?:cards?|gates?|tiers?|oracles?|outbox|units?|ledgers?|hooks?)\b|\bBLOCKED\b|\b[VT][0-3]\b")
_PH = re.compile(r"\{[^{}]*\}")


def _fields(text) -> set:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def lint() -> list:
    problems = []
    base = templates.load(locales.BASE)
    data = locales.view().get("render", {})
    for lang in templates.langs():
        table = templates.load(lang)
        if not table:
            problems.append(f"templates/{lang}.json is missing or empty")
            continue
        spec = data.get(lang, {})
        for key, text in table.items():
            if key.startswith("model."):
                continue
            plain = _PH.sub(" ", text)
            for m in INTERNAL.finditer(plain):
                problems.append(f"{lang}:{key}: internal term {m.group(0)!r}")
            for rule in spec.get("lint", []):
                if any(c in text for c in rule["chars"]):
                    problems.append(f"{lang}:{key}: {rule['message']}")
            first = plain.strip()[:1]
            if spec.get("direction") == "rtl" and first and unicodedata.bidirectional(first) == "L":
                problems.append(f"{lang}:{key}: starts with a left-to-right letter (renders left to right)")
            if lang != locales.BASE and key in base and _fields(text) != _fields(base[key]):
                problems.append(f"{lang}:{key}: placeholders {sorted(_fields(text))} differ from {locales.BASE} "
                                f"{sorted(_fields(base[key]))}")
        if lang != locales.BASE:
            missing = sorted(k for k in base if not k.startswith("model.") and k not in table)
            problems += [f"{lang}: missing {k}" for k in missing]
    return problems
