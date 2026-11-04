from __future__ import annotations

import os

import pytest

from harness_core import protected
from harness_core.safety import Ctx


@pytest.fixture
def ctx(tmp_path):
    ws = tmp_path / "ws"
    data = tmp_path / "data"
    root = tmp_path / "plugin"
    home = tmp_path / "home"
    lc = tmp_path / "loop-contract"
    for d in (ws, data, root, home, lc):
        d.mkdir()
    return Ctx(workspace=ws, data_root=data, plugin_root=root, home=home, loop_contract_root=lc)


@pytest.mark.parametrize(
    "rel",
    [
        "data/control/classes.json",
        "data/runs/j1/events.jsonl",
        "data/keys/hmac.key",
        "plugin/hooks/hooks.json",
        "plugin/lib/harness_core/safety.py",
        "loop-contract/fold_ledger.py",
        "home/.claude/settings.json",
        "home/.claude.json",
        "ws/.claude/settings.json",
        "ws/.claude/loops/j1/state.json",
        "ws/.mcp.json",
        "ws/CLAUDE.md",
        "ws/sub/CLAUDE.md",
        "ws/.git/config",
        "ws/.git",
    ],
)
def test_protected_paths(ctx, tmp_path, rel):
    assert protected.is_protected(tmp_path / rel, ctx)


@pytest.mark.parametrize(
    "rel",
    [
        "ws/src/main.py",
        "ws/.claude/loops/j1/work/report.md",
        "ws/.claude/loops/j1/requests/r1.json",
        "ws/README.md",
        "ws/.gitignore",
        "home/notes.txt",
    ],
)
def test_unprotected_paths(ctx, tmp_path, rel):
    assert not protected.is_protected(tmp_path / rel, ctx)


def test_dotdot_resolved_before_matching(ctx):
    p = ctx.workspace / ".claude" / "loops" / "j1" / "work" / ".." / ".." / ".." / "settings.json"
    assert protected.is_protected(p, ctx)


def test_symlink_resolved_before_matching(ctx):
    target = ctx.data_root / "control"
    target.mkdir()
    link = ctx.workspace / "innocent"
    link.symlink_to(target)
    assert protected.is_protected(link / "classes.json", ctx)


def test_relative_path_resolves_against_workspace(ctx):
    assert protected.is_protected(".git/HEAD", ctx)
    assert not protected.is_protected("src/x.py", ctx)


def test_case_and_tilde_variants(ctx):
    assert protected.is_protected(ctx.workspace / ".CLAUDE" / "settings.json", ctx)
    assert protected.is_protected("~/.claude/settings.json", ctx)


def test_read_protected_keys_and_experience(ctx):
    assert protected.is_read_protected(ctx.data_root / "keys" / "k", ctx)
    assert protected.is_read_protected(ctx.data_root / "experience" / "e.json", ctx)
    assert not protected.is_read_protected(ctx.data_root / "control" / "x", ctx)


def test_contains_read_protected(ctx):
    # a recursive search rooted above keys/ would read it
    assert protected.contains_read_protected(ctx.data_root, ctx)
    assert protected.contains_read_protected(ctx.data_root.parent, ctx)
    assert not protected.contains_read_protected(ctx.workspace, ctx)


def test_outside_workspace(ctx, tmp_path):
    assert protected.outside_workspace(tmp_path / "elsewhere.txt", ctx)
    assert not protected.outside_workspace(ctx.workspace / "a" / "b.txt", ctx)
    link = ctx.workspace / "out"
    link.symlink_to(tmp_path)
    assert protected.outside_workspace(link / "x", ctx)


def test_hardlinked_file_is_flagged(ctx, tmp_path):
    a = ctx.workspace / "a.txt"
    a.write_text("x")
    os.link(a, ctx.workspace / "b.txt")
    assert protected.is_hardlinked(a)
    assert not protected.is_hardlinked(ctx.workspace / "missing")


def test_patterns_come_from_defaults_file():
    pats = protected.load_patterns(None)
    assert "${CLAUDE_PLUGIN_DATA}/**" in pats["write_protected"]
    assert any("keys" in p for p in pats["read_protected"])
