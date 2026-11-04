"""Protected-path matching (spec §Trust boundaries and protected writes).

Paths are resolved (``~``, relative-to-workspace, ``..``, symlinks via realpath)
before matching. Matching is case-insensitive and NFC-normalised because the
default macOS filesystem is both, so ``.CLAUDE/x`` is the same file as ``.claude/x``.
"""
from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from pathlib import Path

DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "control" / "defaults"


def load_control(name: str, ctx=None) -> dict:
    """Live control file under ${CLAUDE_PLUGIN_DATA}/control/, else the shipped default."""
    data_root = getattr(ctx, "data_root", None)
    if data_root:
        live = Path(data_root) / "control" / name
        if live.is_file():
            return json.loads(live.read_text("utf-8"))
    return json.loads((DEFAULTS_DIR / name).read_text("utf-8"))


def load_patterns(ctx) -> dict:
    return load_control("protected_paths.json", ctx)


def _home(ctx) -> str:
    h = getattr(ctx, "home", None)
    return str(h) if h else os.path.expanduser("~")


def resolve(path, ctx, cwd=None) -> str:
    """Absolute realpath; relative paths resolve against ``cwd`` or the workspace."""
    s = os.fspath(path)
    if s == "~" or s.startswith("~/"):
        s = _home(ctx) + s[1:]
    if not os.path.isabs(s):
        s = os.path.join(os.fspath(cwd or getattr(ctx, "workspace", None) or os.getcwd()), s)
    return os.path.realpath(s)


def _norm(s: str) -> str:
    """Canonical form used to compare a path against protected patterns.

    TODO(human): fold look-alike characters so `СLAUDE.md` (Cyrillic С) matches
    `CLAUDE.md` (see tests/test_homoglyph.py, red-team rt106/rt107), while keeping
    Persian names distinct and folding Arabic ي/ك to Persian ی/ک.
    """
    return unicodedata.normalize("NFC", s).casefold()


def _vars(ctx) -> dict:
    return {
        "CLAUDE_PLUGIN_DATA": getattr(ctx, "data_root", None),
        "CLAUDE_PLUGIN_ROOT": getattr(ctx, "plugin_root", None),
        "LOOP_CONTRACT_ROOT": getattr(ctx, "loop_contract_root", None),
        "WORKSPACE": getattr(ctx, "workspace", None),
    }


def _expand(pattern: str, ctx):
    """Substitute variables and realpath the literal prefix; None when a variable is unset."""
    vs = _vars(ctx)
    missing = False

    def sub(m):
        nonlocal missing
        v = vs.get(m.group(1))
        if v is None:
            missing = True
            return ""
        return os.fspath(v)

    p = re.sub(r"\$\{(\w+)\}", sub, pattern)
    if missing:
        return None
    if p == "~" or p.startswith("~/"):
        p = _home(ctx) + p[1:]
    m = re.search(r"[*?\[]", p)
    head, tail = (p, "") if not m else (p[: p.rfind("/", 0, m.start())], p[p.rfind("/", 0, m.start()):])
    return os.path.realpath(head) + tail


def _glob_re(pattern: str):
    out = []
    for tok in re.split(r"(/\*\*/|/\*\*$|\*|\?)", pattern):
        if tok == "/**/":
            out.append("(?:/.*)?/")
        elif tok == "/**":
            out.append("(?:/.*)?")
        elif tok == "*":
            out.append("[^/]*")
        elif tok == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(tok))
    return re.compile("^" + "".join(out) + "$", re.S)


def _matches(real: str, patterns, ctx) -> bool:
    n = _norm(real)
    for pat in patterns:
        e = _expand(pat, ctx)
        if e is not None and _glob_re(_norm(e)).match(n):
            return True
    return False


def is_protected(path, ctx, cwd=None) -> bool:
    real = resolve(path, ctx, cwd)
    pats = load_patterns(ctx)
    if _matches(real, pats.get("always_protected", []), ctx):
        return True
    return _matches(real, pats["write_protected"], ctx) and not _matches(real, pats.get("write_exceptions", []), ctx)


def is_scratch(path, ctx, cwd=None) -> bool:
    """Under a job's agent-writable ``work/`` or ``requests/`` folder."""
    return _matches(resolve(path, ctx, cwd), load_patterns(ctx).get("write_exceptions", []), ctx)


def is_read_protected(path, ctx, cwd=None) -> bool:
    return _matches(resolve(path, ctx, cwd), load_patterns(ctx)["read_protected"], ctx)


def _under(child: str, parent: str) -> bool:
    c, p = _norm(child), _norm(parent).rstrip("/")
    return c == p or c.startswith(p + "/")


def contains_read_protected(root, ctx, cwd=None) -> bool:
    """True when a recursive search rooted at ``root`` could reach a read-protected tree."""
    real = resolve(root, ctx, cwd)
    for pat in load_patterns(ctx)["read_protected"]:
        e = _expand(pat, ctx)
        if e is None:
            continue
        base = re.split(r"/[^/]*[*?\[]", e, maxsplit=1)[0]
        if _under(base, real) or _under(real, base):
            return True
    return False


def outside_workspace(path, ctx, cwd=None) -> bool:
    return not _under(resolve(path, ctx, cwd), os.path.realpath(os.fspath(ctx.workspace)))


def under_any(path, roots, ctx, cwd=None) -> bool:
    real = resolve(path, ctx, cwd)
    return any(_under(real, os.path.realpath(os.fspath(r))) for r in roots if r)


def is_hardlinked(path) -> bool:
    try:
        st = os.lstat(os.fspath(path))
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_nlink > 1
