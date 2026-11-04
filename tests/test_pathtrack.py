"""Unit tests for harness_core.pathtrack (pure helpers behind the fail-closed path rules)."""
from __future__ import annotations

import os

import pytest

from harness_core import pathtrack as pt
from harness_core.safety import Ctx

M = pt.MASK


# ---- rule 1: quoting and static words --------------------------------------


def test_mask_literal_dollars_masks_single_quoted_and_escaped_only():
    assert pt.mask_literal_dollars("a '$x' \"$y\" \\$z") == f"a '{M}x' \"$y\" \\{M}z"
    assert pt.mask_literal_dollars("\"it's $y\"") == "\"it's $y\""  # ' inside "..." is literal
    assert pt.mask_literal_dollars('"\\$y"') == f'"\\{M}y"'


def test_note_masks_conflicting_occurrences_keep_the_expanding_form():
    masks = {}
    pt.note_masks(masks, ["$a", "b"], [f"{M}a", "b"])
    assert masks == {"$a": f"{M}a"}
    pt.note_masks(masks, ["$a"], ["$a"])  # same text unquoted later: must expand
    assert masks["$a"] == "$a"


VARS = {"HOME": "/h", "D": "/data", "E": ""}


@pytest.mark.parametrize("masked, out", [
    ("$D/k", "/data/k"), ("${D}/k", "/data/k"), ("x$E", "x"), ("~/a", "/h/a"), ("~", "/h"),
    (f"{M}D/k", "$D/k"), ("plain", "plain"),
])
def test_static_word_resolves(masked, out):
    assert pt.static_word(masked, VARS) == out


@pytest.mark.parametrize("masked", ["$NOPE/k", "$__SUBST__", "$__PARAM__/x", "~bob/x", "~+/x", "$@", "$1", "a$"])
def test_static_word_unresolvable_is_none(masked):
    assert pt.static_word(masked, VARS) is None


def test_union_keeps_order_and_dedups():
    assert pt.union(["/a", None], ["/b", "/a", None]) == ["/a", None, "/b"]


# ---- rule 3: job folders ---------------------------------------------------


@pytest.fixture
def ctx(tmp_path):
    ws, data = tmp_path / "ws", tmp_path / "data"
    (ws / ".claude" / "loops" / "j1" / "work").mkdir(parents=True)
    (ws / ".claude" / "loops" / "j1" / "requests").mkdir()
    data.mkdir()
    return Ctx(workspace=ws, data_root=data, home=tmp_path / "home")


def test_is_job_folder(ctx):
    loops = os.path.join(ctx.workspace, ".claude", "loops", "j1")
    assert pt.is_job_folder(os.path.join(loops, "work"), ctx)
    assert pt.is_job_folder(os.path.join(loops, "requests"), ctx)
    assert not pt.is_job_folder(os.path.join(loops, "work", "sub"), ctx)
    assert not pt.is_job_folder(loops, ctx)


def test_scratch_entry_does_not_follow_a_final_symlink(ctx, tmp_path):
    work = os.path.join(ctx.workspace, ".claude", "loops", "j1", "work")
    os.symlink(str(tmp_path), os.path.join(work, "l"))
    assert pt.scratch_entry(pt.entry(os.path.join(work, "l")), ctx)


# ---- rule 5: search roots --------------------------------------------------


@pytest.mark.parametrize("inp, extra", [
    ({"pattern": "../data/keys/*"}, "{ws}/../data/keys/"),
    ({"pattern": "src/**/../../x"}, "/"),
    ({"pattern": "/abs/exp*"}, "/abs/"),
    ({"pattern": "~/x/*"}, "~/x/"),
    ({"pattern": "**/*.py"}, "{ws}/"),
])
def test_search_roots_glob(ctx, inp, extra):
    roots = pt.search_roots("Glob", inp, ctx)
    assert roots[0] == str(ctx.workspace)
    assert extra.format(ws=os.path.realpath(ctx.workspace)) in roots


def test_search_roots_non_glob_is_just_the_path(ctx):
    assert pt.search_roots("Grep", {"pattern": "../x", "path": "src"}, ctx) == ["src"]
    assert pt.search_roots("Glob", {"pattern": 3, "path": None}, ctx) == [str(ctx.workspace)]
