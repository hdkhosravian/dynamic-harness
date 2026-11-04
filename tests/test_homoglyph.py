"""Homoglyph defence for protected-path matching (red-team rt106, rt107).

Awaiting the human-written _norm() in lib/harness_core/protected.py.
Remove the xfail markers once _norm folds confusables.
"""
from __future__ import annotations

import pytest

from harness_core.protected import _norm

CYR_C = "С"  # Cyrillic capital ES, looks like Latin C
CYR_A = "а"  # Cyrillic small a


@pytest.mark.xfail(reason="awaiting human _norm(): confusable folding")
def test_cyrillic_lookalike_claude_md_matches_protected_name():
    assert _norm(f"{CYR_C}LAUDE.md") == _norm("CLAUDE.md")


@pytest.mark.xfail(reason="awaiting human _norm(): confusable folding")
def test_cyrillic_lookalike_in_dot_claude_dir():
    assert _norm(f".cl{CYR_A}ude") == _norm(".claude")


def test_persian_filenames_are_not_folded_into_latin():
    # A Persian report name must stay distinct from any protected Latin name.
    assert _norm("گزارش.md") != _norm("CLAUDE.md")
    assert _norm("گزارش.md") == _norm("گزارش.md")


@pytest.mark.xfail(reason="awaiting human _norm(): ي/ك → ی/ک folding")
def test_arabic_yeh_and_kaf_fold_to_persian():
    # ي/ك (Arabic) and ی/ک (Persian) are the same letter to an owner.
    assert _norm("كتاب") == _norm("کتاب")
    assert _norm("يادداشت") == _norm("یادداشت")
