"""P0-T1: the plugin hash covers the shipped directories only, and survives a file that vanishes mid-walk.

``session.plugin_hash`` is looked up inside each test body, so the file still collects before it exists.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_session import baseline, ctx  # noqa: F401  (fixture ctx)

from harness_core import canon, safeio, session
from harness_core.errors import HarnessError

SHIPPED = (".claude-plugin", "agents", "bin", "commands", "control", "hooks", "lib", "schema", "skills", "templates")
EXTRA_FILES = (".mcp.json", ".lsp.json", "settings.json")
EXTRA_DIRS = ("output-styles", "monitors", "themes")
EXTRAS = EXTRA_FILES + EXTRA_DIRS
FAIL_CLOSED = (HarnessError, ValueError, OSError)  # what on_start turns into STOPPED_TAMPER


def ph(root):
    return session.plugin_hash(root)


def put(root: Path, rel: str, text: str = "x") -> Path:
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "plugin"
    put(r, "lib/x.py", "x = 1\n")
    put(r, "skills/s/SKILL.md", "# s\n")
    return r


def vanish_on_read(monkeypatch, name: str, *, every_attempt: bool):
    """Seam: replace `safeio.safe_open` and every module-level alias of it (session, canon) with a wrapper that,
    when asked to read a file called `name`, deletes it first and then delegates (so the real safe_open reports the
    file missing, exactly as a file removed between os.walk and the read would). With every_attempt=True the file
    is put back before each read, so it also vanishes on a retry. Returns the list of reads of `name`."""
    real = safeio.safe_open
    seen = []

    def wrapper(path, allowed_roots):
        p = Path(path)
        if p.name == name:
            seen.append(p)
            if every_attempt and not p.exists():
                p.write_text("back again")
            if len(seen) == 1 or every_attempt:
                p.unlink()
        return real(path, allowed_roots)

    monkeypatch.setattr(safeio, "safe_open", wrapper)  # canon.tree_sha imports it from safeio at call time
    for mod in (session, canon):  # module-level aliases, should an implementation import it at the top
        monkeypatch.setattr(mod, "safe_open", wrapper, raising=mod is session)
    return seen


def count_reads(monkeypatch, *names):
    real = safeio.safe_open
    seen = []

    def wrapper(path, allowed_roots):
        if Path(path).name in names:
            seen.append(Path(path))
        return real(path, allowed_roots)

    monkeypatch.setattr(safeio, "safe_open", wrapper)
    for mod in (session, canon):
        monkeypatch.setattr(mod, "safe_open", wrapper, raising=mod is session)
    return seen


# --- constants and trivial cases -------------------------------------------------------------------------------

def test_the_shipped_and_loaded_names_are_exactly_those_in_the_brief():
    assert session.SHIPPED_DIRS == SHIPPED
    assert session.LOADED_EXTRAS == EXTRAS


@pytest.mark.parametrize("bad", [None, "", "no/such/dir"])
def test_an_absent_root_hashes_as_absent(bad, tmp_path):
    assert ph(bad if bad != "no/such/dir" else tmp_path / bad) == "absent"


# --- acceptance: only shipped entries count --------------------------------------------------------------------

@pytest.mark.parametrize(
    "rel",
    [".remember/tmp/x", ".ruff_cache/y", ".superpowers/z", "docs/a.md", "tests/t.py", "README.md", "PLAN.md",
     "unknown/dir/f", "notes.txt"],
)
def test_a_file_outside_the_shipped_entries_does_not_change_the_plugin_hash(root, rel):
    before = ph(root)
    put(root, rel)
    assert ph(root) == before


def test_all_the_non_release_files_together_do_not_change_the_plugin_hash(root):
    before = ph(root)
    for rel in (".remember/tmp/x", ".ruff_cache/y", ".superpowers/z", "docs/a.md", "tests/t.py", "README.md", "PLAN.md"):
        put(root, rel)
    assert ph(root) == before


@pytest.mark.parametrize("skip", ["__pycache__", ".pytest_cache", ".git", ".in_use"])
def test_skip_dirs_still_apply_inside_a_shipped_directory(root, skip):
    before = ph(root)
    put(root, f"lib/{skip}/junk", "junk")
    assert ph(root) == before


# --- acceptance: every shipped directory counts ----------------------------------------------------------------

@pytest.mark.parametrize("d", SHIPPED)
def test_changing_a_file_in_a_shipped_dir_changes_the_plugin_hash(root, d):
    put(root, f"{d}/f.txt", "one")
    before = ph(root)
    put(root, f"{d}/f.txt", "two")
    assert ph(root) != before


@pytest.mark.parametrize("d", SHIPPED)
def test_adding_a_file_to_a_shipped_dir_changes_the_plugin_hash(root, d):
    before = ph(root)
    put(root, f"{d}/new.txt")
    assert ph(root) != before


@pytest.mark.parametrize("d", SHIPPED)
def test_removing_a_file_from_a_shipped_dir_changes_the_plugin_hash(root, d):
    put(root, f"{d}/keep.txt")
    gone = put(root, f"{d}/gone.txt")
    before = ph(root)
    gone.unlink()
    assert ph(root) != before


def test_paths_are_relative_to_the_root_so_the_directory_name_counts(tmp_path):
    a, b = put(tmp_path / "a", "lib/x.py", "same"), put(tmp_path / "b", "skills/x.py", "same")
    assert ph(a.parents[1]) != ph(b.parents[1])


# --- acceptance: every loaded extra counts ---------------------------------------------------------------------

@pytest.mark.parametrize("name", EXTRAS)
def test_adding_a_loaded_extra_changes_the_plugin_hash(root, name):
    before = ph(root)
    put(root, name if name in EXTRA_FILES else f"{name}/f.txt")
    assert ph(root) != before


@pytest.mark.parametrize("name", EXTRAS)
def test_changing_a_loaded_extra_changes_the_plugin_hash(root, name):
    rel = name if name in EXTRA_FILES else f"{name}/f.txt"
    put(root, rel, "one")
    before = ph(root)
    put(root, rel, "two")
    assert ph(root) != before


# --- acceptance: a symbolic link at a shipped name fails closed -------------------------------------------------

@pytest.mark.parametrize("name", SHIPPED + EXTRAS)
def test_a_symlink_named_like_a_shipped_entry_raises(root, tmp_path, name):
    elsewhere = tmp_path / "elsewhere"
    put(elsewhere, "f.txt")
    link = root / name
    if link.is_symlink() or link.exists():
        if link.is_dir() and not link.is_symlink():
            import shutil
            shutil.rmtree(link)
        else:
            link.unlink()
    link.symlink_to(elsewhere)
    with pytest.raises(FAIL_CLOSED):
        ph(root)


def test_a_dangling_symlink_named_lib_raises(root, tmp_path):
    import shutil
    shutil.rmtree(root / "lib")
    (root / "lib").symlink_to(tmp_path / "nowhere")
    with pytest.raises(FAIL_CLOSED):
        ph(root)


def test_a_symlink_outside_the_shipped_names_has_no_effect(root, tmp_path):
    before = ph(root)
    (root / "docs").symlink_to(tmp_path)
    assert ph(root) == before


@pytest.mark.parametrize("fn", ["tree_hash", "plugin_hash"])
def test_a_symlinked_file_inside_a_shipped_dir_raises_at_once_with_no_retry(root, monkeypatch, fn):
    (root / "lib" / "real.py").write_text("r")
    (root / "lib" / "link.py").symlink_to(root / "lib" / "real.py")
    reads = count_reads(monkeypatch, "link.py")
    with pytest.raises(FAIL_CLOSED):
        getattr(session, fn)(root)
    assert len(reads) == 1


@pytest.mark.parametrize("fn", ["tree_hash", "plugin_hash"])
def test_a_hard_linked_file_inside_a_shipped_dir_raises_at_once_with_no_retry(root, monkeypatch, fn):
    (root / "lib" / "one.py").write_text("r")
    os.link(root / "lib" / "one.py", root / "lib" / "two.py")
    reads = count_reads(monkeypatch, "one.py", "two.py")
    with pytest.raises(FAIL_CLOSED):
        getattr(session, fn)(root)
    assert len(reads) == 1


# --- acceptance: a vanished file is retried once, then fails closed ---------------------------------------------

@pytest.mark.parametrize("fn", ["tree_hash", "plugin_hash"])
def test_a_file_that_vanishes_once_does_not_fail_and_is_left_out_of_the_hash(tmp_path, monkeypatch, fn):
    full, expected = tmp_path / "full", tmp_path / "expected"
    for r in (full, expected):
        put(r, "lib/a.py", "a")
        put(r, "skills/s/SKILL.md", "s")
    put(full, "lib/vanishing.py", "v")
    reads = vanish_on_read(monkeypatch, "vanishing.py", every_attempt=False)
    got = getattr(session, fn)(full)
    monkeypatch.undo()
    assert got == getattr(session, fn)(expected)
    assert len(reads) == 1  # the second pass no longer lists it


@pytest.mark.parametrize("fn", ["tree_hash", "plugin_hash"])
def test_a_file_that_vanishes_on_both_attempts_fails_closed(tmp_path, monkeypatch, fn):
    put(tmp_path, "lib/a.py", "a")
    put(tmp_path, "lib/vanishing.py", "v")
    reads = vanish_on_read(monkeypatch, "vanishing.py", every_attempt=True)
    with pytest.raises(FAIL_CLOSED):
        getattr(session, fn)(tmp_path)
    assert len(reads) == 2  # one retry, no more


def test_on_start_stops_as_tamper_when_a_file_vanishes_on_both_attempts(ctx, monkeypatch):
    put(ctx.plugin_root, "lib/vanishing.py", "v")
    baseline(ctx)
    vanish_on_read(monkeypatch, "vanishing.py", every_attempt=True)
    out = session.on_start({"session_id": "s1"}, ctx)
    assert out["state"] == "STOPPED_TAMPER" and "config check failed" in out["reason"]


def test_on_start_survives_a_file_that_vanishes_once(ctx, monkeypatch):
    baseline(ctx)  # recorded without the file
    put(ctx.plugin_root, "lib/vanishing.py", "v")
    vanish_on_read(monkeypatch, "vanishing.py", every_attempt=False)
    assert session.on_start({"session_id": "s1"}, ctx) == {}


# --- acceptance: the other layers keep the full walk -------------------------------------------------------------

@pytest.mark.parametrize(
    "layer, rel",
    [
        ("control", "control/unknown_dir/f.txt"),
        ("business", "business/odd-name/f.txt"),
        ("loop_contract", "oddname/f.txt"),
        ("loop_contract", "skills/loop-contract/scripts/fold_ledger.py"),
        ("control", "control/budgets.json"),
        ("business", "business/profile.json"),
    ],
)
def test_other_layers_change_for_any_file_even_in_an_unknown_directory(ctx, layer, rel):
    base = ctx.loop_contract_root if layer == "loop_contract" else ctx.data_root
    before = session.current_hashes(ctx)
    put(base, rel, "changed")
    after = session.current_hashes(ctx)
    assert after[layer] != before[layer]
    assert after["plugin"] == before["plugin"]


def test_the_plugin_layer_of_current_hashes_ignores_a_non_release_file(ctx):
    before = session.current_hashes(ctx)
    put(ctx.plugin_root, ".remember/tmp/x")
    put(ctx.plugin_root, "docs/a.md")
    assert session.current_hashes(ctx) == before


def test_the_plugin_layer_of_current_hashes_is_the_plugin_hash(ctx):
    put(ctx.plugin_root, "skills/s/SKILL.md")
    assert session.current_hashes(ctx)["plugin"] == ph(ctx.plugin_root)


def test_a_non_release_file_does_not_stop_a_session(ctx):
    put(ctx.plugin_root, ".remember/tmp/save.lock/pid", "1")
    put(ctx.plugin_root, "docs/a.md")
    assert session.on_start({"permission_mode": "default"}, ctx) == {}
