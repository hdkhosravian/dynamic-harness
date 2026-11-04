"""``harness-lint-user-text``: the template lint (spec §User-facing templates and localisation; build phase P5).

It checks harness-authored text only (``templates/*.json``; keys under ``model.`` are hints to the model and are
skipped), never quoted owner content:

- no internal terms: card, gate, tier, oracle, outbox, BLOCKED, unit, ledger, hook, V0 to V3, T0 to T2;
- every language has every English key, with the same ``{placeholders}``;
- Persian text uses Persian ی and ک (never Arabic ي and ك), no em or en dashes, and no line starting with a
  Latin word (it would render left to right).
"""
from __future__ import annotations

import re
import string

from . import templates

INTERNAL = re.compile(r"(?i)\b(?:cards?|gates?|tiers?|oracles?|outbox|units?|ledgers?|hooks?)\b|\bBLOCKED\b|\b[VT][0-3]\b")
_PH = re.compile(r"\{[^{}]*\}")


def _fields(text) -> set:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def lint() -> list:
    problems = []
    en = templates.load("en")
    for lang in templates.LANGS:
        table = templates.load(lang)
        if not table:
            problems.append(f"templates/{lang}.json is missing or empty")
            continue
        for key, text in table.items():
            if key.startswith("model."):
                continue
            plain = _PH.sub(" ", text)
            for m in INTERNAL.finditer(plain):
                problems.append(f"{lang}:{key}: internal term {m.group(0)!r}")
            if lang == "fa":
                if re.search("[يك]", text):
                    problems.append(f"fa:{key}: Arabic ي or ك instead of Persian ی or ک")
                if re.search("[–—]", text):
                    problems.append(f"fa:{key}: a dash in Persian prose")
                first = plain.strip()[:1]
                if first and re.match(r"[A-Za-z]", first):
                    problems.append(f"fa:{key}: starts with a Latin word (renders left to right)")
            if lang != "en" and key in en and _fields(text) != _fields(en[key]):
                problems.append(f"{lang}:{key}: placeholders {sorted(_fields(text))} differ from en "
                                f"{sorted(_fields(en[key]))}")
        if lang != "en":
            missing = sorted(k for k in en if not k.startswith("model.") and k not in table)
            problems += [f"{lang}: missing {k}" for k in missing]
    return problems
