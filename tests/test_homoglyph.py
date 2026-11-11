"""Homoglyph defence for protected-path matching (red-team rt106, rt107)."""
from __future__ import annotations

from harness_core import protected
from harness_core.protected import _norm

from test_protected import ctx  # noqa: F401  (fixture)

CYR_C = "С"  # Cyrillic capital ES, looks like Latin C
CYR_A = "а"  # Cyrillic small a
CYR_O = "о"  # Cyrillic small o


def test_cyrillic_lookalike_claude_md_matches_protected_name():
    assert _norm(f"{CYR_C}LAUDE.md") == _norm("CLAUDE.md")


def test_cyrillic_lookalike_in_dot_claude_dir():
    assert _norm(f".cl{CYR_A}ude") == _norm(".claude")


def test_persian_filenames_are_not_folded_into_latin():
    assert _norm("گزارش.md") != _norm("CLAUDE.md")
    assert _norm("گزارش.md") == _norm("گزارش.md")


def test_arabic_yeh_and_kaf_fold_to_persian():
    assert _norm("كتاب") == _norm("کتاب")
    assert _norm("يادداشت") == _norm("یادداشت")


def test_fullwidth_styled_zero_width_and_accented_variants_fold():
    for s in ("ＣＬＡＵＤＥ.md", "𝐂𝐋𝐀𝐔𝐃𝐄.md", "CLA​UDE.md", "CLAÚDE.md", "CLAUDE­.md"):
        assert _norm(s) == _norm("CLAUDE.md"), s


def test_folding_never_invents_a_path_separator():
    assert "/" not in _norm("a／b") and "/" not in _norm("a∕b")


def test_homoglyph_protected_names_are_protected(ctx):  # noqa: F811
    assert protected.is_protected(ctx.workspace / f"{CYR_C}LAUDE.md", ctx)
    assert protected.is_protected(ctx.workspace / "sub" / "ＣＬＡＵＤＥ.md", ctx)
    assert protected.is_protected(ctx.workspace / f".cl{CYR_A}ude" / "settings.json", ctx)


def test_homoglyph_does_not_widen_scratch_exceptions(ctx):  # noqa: F811
    real = ctx.workspace / ".claude" / "loops" / "j1" / "work" / "x.txt"
    fake = ctx.workspace / ".claude" / "loops" / "j1" / f"w{CYR_O}rk" / "x.txt"
    assert not protected.is_protected(real, ctx) and protected.is_scratch(real, ctx)
    assert protected.is_protected(fake, ctx) and not protected.is_scratch(fake, ctx)
    sneaky = ctx.workspace / ".claude" / "loops" / "j1" / "work／..／..／settings.json"
    assert protected.is_protected(sneaky, ctx)
