"""Ratchet: locale literals (currency, calendar, region, language, non-Latin script) still in code.

Every count in ALLOWED is a literal that a later task moves into a locale pack. A count may go down, never up."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    "currency": r"(?i)(?<![a-z0-9])(irt|irr|tomans?|rials?)(?![a-z0-9])",
    "calendar": r"(?i)(?<![a-z0-9])(jalali|shamsi|gregorian|hijri|farvardin|ordibehesht|khordad|mordad|shahrivar"
                r"|bahman|esfand)(?![a-z0-9])",
    "region": r"(?i)(?<![a-z0-9])(iran|iranian|persian|farsi|sheba)(?![a-z0-9])",
    "tld": r"(?i)\.ir(?![a-z0-9_])",
    "lang": r"(?i)(?<![a-z0-9])fa(?![a-z0-9])",
}
REGEXES = {k: re.compile(v) for k, v in PATTERNS.items()}

NOTE = ("A count may only go down. Lower it in the commit that moves the literal into a data file. "
        "A new literal belongs in a locale pack, not in code.")


def count(text: str) -> dict:
    """{rule: hits} for one text, only rules with a hit."""
    out = {k: len(rx.findall(text)) for k, rx in REGEXES.items()}
    out["script"] = sum(1 for c in text if ord(c) > 127 and unicodedata.category(c)[0] in "LMN")
    return {k: n for k, n in out.items() if n}


def scanned_files(root: Path):
    yield from sorted((root / "lib").rglob("*.py"))
    yield from sorted(p for p in (root / "bin").iterdir() if p.is_file())
    yield from sorted((root / "tools").glob("*.py"))
    yield from sorted(p for p in (root / "hooks").iterdir() if p.is_file())


def scan(root: Path = ROOT) -> dict:
    out = {}
    for p in scanned_files(root):
        hits = count(p.read_text("utf-8"))
        if hits:
            out[p.relative_to(root).as_posix()] = hits
    return out


# The scan of the repository at the commit that added this test, sorted by path.
ALLOWED = {
    "bin/harness-lint-user-text": {"region": 1},
    "bin/harness-normalize": {"calendar": 3},
    "lib/harness_core/admin.py": {"calendar": 1, "currency": 3},
    "lib/harness_core/answer.py": {"calendar": 8, "currency": 2, "lang": 5, "script": 16},
    "lib/harness_core/business.py": {"calendar": 7, "currency": 13, "lang": 2},
    "lib/harness_core/claims.py": {"currency": 2, "script": 88},
    "lib/harness_core/contact.py": {"script": 56},
    "lib/harness_core/finance.py": {"calendar": 13, "currency": 10, "script": 139},
    "lib/harness_core/gate.py": {"lang": 1},
    "lib/harness_core/lint_text.py": {"lang": 4, "region": 4, "script": 10},
    "lib/harness_core/localize.py": {"calendar": 51, "currency": 24, "lang": 5, "region": 4, "script": 147},
    "lib/harness_core/model.py": {"calendar": 4, "currency": 1, "lang": 8, "script": 119},
    "lib/harness_core/model_cmd.py": {"currency": 1},
    "lib/harness_core/normalize.py": {"calendar": 3, "region": 1, "script": 20},
    "lib/harness_core/operations.py": {"script": 21},
    "lib/harness_core/pii.py": {"region": 5, "script": 156},
    "lib/harness_core/probe.py": {"region": 2, "script": 24},
    "lib/harness_core/protected.py": {"region": 3, "script": 76},
    "lib/harness_core/query_guard.py": {"region": 2, "script": 12},
    "lib/harness_core/stopgate.py": {"region": 1},
    "lib/harness_core/templates.py": {"currency": 2, "lang": 14, "region": 5, "script": 146},
}

# Hits that stay after P1: the reason is part of the record.
_SHARED = "copy shared with the loop-contract plugin; tests/test_stopgate.py compares the two files"
PERMANENT = {
    ("lib/harness_core/normalize.py", "calendar"): _SHARED,
    ("lib/harness_core/normalize.py", "region"): _SHARED,
    ("lib/harness_core/normalize.py", "script"): _SHARED,
}


def differences(found: dict, recorded: dict):
    rows = []
    for path in sorted(set(found) | set(recorded)):
        for rule in sorted(set(found.get(path, {})) | set(recorded.get(path, {}))):
            want, got = recorded.get(path, {}).get(rule, 0), found.get(path, {}).get(rule, 0)
            if want != got:
                rows.append((path, rule, want, got))
    return rows


def test_the_literals_in_code_equal_the_recorded_counts():
    rows = differences(scan(), ALLOWED)
    lines = [f"  {p}  {r}: recorded {a}, found {b}" for p, r, a, b in rows]
    assert not rows, "locale literals differ from ALLOWED:\n" + "\n".join(lines) + "\n" + NOTE


def test_the_permanent_hits_are_recorded_and_explained():
    for (path, rule), reason in PERMANENT.items():
        assert ALLOWED.get(path, {}).get(rule, 0) > 0, (path, rule)
        assert isinstance(reason, str) and reason.strip(), (path, rule)


def test_every_allowed_count_is_positive_and_names_a_known_rule():
    for path, rules in ALLOWED.items():
        assert (ROOT / path).is_file(), path
        assert rules and set(rules) <= set(PATTERNS) | {"script"}, path
        assert all(isinstance(n, int) and n > 0 for n in rules.values()), path


HITS = [
    ("IRT", {"currency": 1}), ("irr", {"currency": 1}), ("Toman", {"currency": 1}), ("rials", {"currency": 1}),
    ("jalali", {"calendar": 1}), ("Esfand 30", {"calendar": 1}), ("Persian digits", {"region": 1}),
    ("news.example.ir", {"tld": 1}), ('"fa"', {"lang": 1}), ("label_fa", {"lang": 1}),
    ("CURRENCY_FA", {"lang": 1}), ("lang=fa,", {"lang": 1}),
    ("ا", {"script": 1}),  # a Persian letter
    ("۱", {"script": 1}),  # a Persian digit
    ("IRT IRR", {"currency": 2}), ("اب x ۱", {"script": 3}),
]
MISSES = ["default", "dirt", "dir", "first", "x.iri", "0xfa", "trial", "material",
          "—", "§", "→", "", "plain ASCII text 123"]


@pytest.mark.parametrize("text,expected", HITS)
def test_a_literal_is_counted_by_its_rule(text, expected):
    assert count(text) == expected


@pytest.mark.parametrize("text", MISSES)
def test_a_look_alike_is_not_counted(text):
    assert count(text) == {}
