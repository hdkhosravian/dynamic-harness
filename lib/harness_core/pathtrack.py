"""Fail-closed path and working-directory tracking for the Bash classifier (classes.py).

Maintainer rules this module implements (classes.py calls in at marked ``pathtrack`` sites):
1. A path word, redirect or working directory that cannot be resolved statically (dynamic
   variable, command substitution, unknown cwd) is ``unknown``. ``NAME=literal`` assignments
   are tracked so ordinary scripts still resolve; quoted/escaped ``$`` is literal.
2. cd/pushd in eval, source, subshells, ``builtin``/``command`` and substitutions either
   propagates (union of possible cwds) or adds an unknown (None) cwd plus an ``unknown`` result.
3. Creating a symlink / hard link inside (or in place of) a job's ``work/`` or ``requests/``
   folder, or recreating such a folder removed earlier in the same command, is ``protected_path``.
4. Files under ``work/`` and ``requests/`` always count as agent-written (``classes.is_written``);
   runners pointed at them are ``self_written_exec``.
5. Glob patterns resolve against the search path; ``..`` after a wildcard may reach any ancestor.

The helpers at the top are pure; ``on_simple`` and ``bash_write_targets`` are the glue that
reads and updates a ``classes._Bash`` instance.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import protected

MASK = "\x00"  # stands for a `$` that cannot expand; NUL is rejected before tokenizing, so it never collides
_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_NAME = re.compile(r"[A-Za-z_]\w*")

# commands that never open their arguments as files: unresolvable words there are harmless
NO_PATH_ARGS = {
    "echo", "printf", "test", "[", "[[", "true", "false", ":", "sleep", "seq", "expr", "exit", "return",
    "shift", "set", "wait", "kill", "basename", "dirname", "date", "pwd", "unset", "read",
    "export", "local", "declare", "typeset", "readonly",
}
EXEC_RUNNERS = {"make", "gmake", "just", "rake", "ninja", "pytest", "py.test", "tox", "nox", "jest", "vitest",
                "mocha", "ctest", "meson", "cmake", "bats", "phpunit", "rspec"}
PY_RUNNERS = {"pytest", "unittest", "doctest", "coverage"}


@dataclass
class Track:
    """Per-command state shared by a _Bash and all its children."""
    masks: dict = field(default_factory=dict)  # token -> token with literal `$` masked
    links: set = field(default_factory=set)  # entries created as links earlier in the command
    removed: set = field(default_factory=set)  # job folders removed earlier in the command
    assigned: set = field(default_factory=set)  # every variable name assigned in the command
    writes: set = field(default_factory=set)  # every resolved write target, allowed or not
    unresolved_writes: int = 0  # write targets the classifier could not resolve
    drift: bool = False  # an untrackable cwd change takes effect from the next command


# ---- pure helpers -----------------------------------------------------------


def mask_literal_dollars(text: str) -> str:
    """Replace each `$` that cannot expand (single-quoted or backslash-escaped) with MASK.

    Only `$` changes, so the masked text tokenizes into the same words as ``text``.
    """
    out, i, n, dq = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if ch == "'" and not dq:
            k = text.find("'", i + 1)
            k = n - 1 if k < 0 else k
            out.append(text[i:k + 1].replace("$", MASK))
            i = k + 1
            continue
        if ch == "\\":
            nxt = text[i + 1:i + 2]
            out.append("\\" + (MASK if nxt == "$" else nxt))
            i += 2
            continue
        if ch == '"':
            dq = not dq
        out.append(ch)
        i += 1
    return "".join(out)


def note_masks(masks: dict, toks, shadow) -> None:
    """Record token -> masked token; a text seen both quoted and unquoted keeps the expanding form."""
    if len(toks) != len(shadow):
        return  # cannot align: every `$` counts as expanding (stricter)
    for t, s in zip(toks, shadow):
        if t != s or t in masks:
            masks[t] = s if masks.get(t, s) == s else t


def static_word(masked: str, vars: dict):
    """The word after expansion, or None when an expansion cannot be resolved statically."""
    if masked.startswith("~"):
        head, sep, tail = masked.partition("/")
        if head != "~" or vars.get("HOME") is None:
            return None
        masked = vars["HOME"] + sep + tail
    missing = []

    def sub(m):
        v = vars.get(m.group(1) or m.group(2))
        if v is None:
            missing.append(m.group(0))
            return ""
        return v

    out = _VAR.sub(sub, masked)
    if missing or "$" in out:
        return None
    return out.replace(MASK, "$")


def union(a, b):
    return list(dict.fromkeys(list(a) + list(b)))


def entry(path: str) -> str:
    """Where a directory entry lives: parent resolved, final component kept (a link is not followed)."""
    p = path.rstrip("/") or "/"
    return os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))


def scratch_entry(e: str, ctx) -> bool:
    """``e`` (already an entry, not re-resolved) is a job work/ or requests/ folder or inside one."""
    return protected._matches(e, protected.load_patterns(ctx).get("write_exceptions", []), ctx)


def is_job_folder(p: str, ctx) -> bool:
    return scratch_entry(p, ctx) and not scratch_entry(os.path.dirname(p), ctx)


def search_roots(tool, inp, ctx):
    """Trees a Grep/Glob/LS call may read. Relative Glob patterns resolve against the search path."""
    base = inp.get("path") or os.fspath(getattr(ctx, "cwd", None) or ctx.workspace)
    roots = [base]
    pat = inp.get("pattern")
    if tool != "Glob" or not isinstance(pat, str) or not isinstance(base, str):
        return roots
    m = re.search(r"[*?\[{]", pat)
    head = pat[:m.start()] if m else pat
    if ".." in pat[len(head):]:
        roots.append("/")  # `..` after a wildcard can climb to any ancestor
    d = head[:head.rfind("/") + 1] or head
    roots.append(d if d.startswith(("/", "~")) else os.path.join(protected.resolve(base, ctx, getattr(ctx, "cwd", None)), d))
    return roots


def _split(rest):
    """(options, positionals, target_dir) for cp/mv/ln/rsync/ditto/install."""
    opts, pos, tdir, i = [], [], None, 0
    while i < len(rest):
        a = rest[i]
        if a == "--":
            pos += rest[i + 1:]
            break
        if a.startswith("-") and a != "-":
            opts.append(a)
            if a in ("-t", "--target-directory") and i + 1 < len(rest):
                tdir = rest[i + 1]
                i += 1
            elif a in ("-S", "-m", "-o", "-g", "-e", "--suffix") and i + 1 < len(rest):
                i += 1
            elif a.startswith("--target-directory="):
                tdir = a.split("=", 1)[1]
        else:
            pos.append(a)
        i += 1
    return opts, pos, tdir


# ---- glue: called from classes._Bash -----------------------------------------


def _mask(b, w):
    return b.track.masks.get(w, w)


def _assign(b, word):
    name = word.split("=", 1)[0]
    b.track.assigned.add(name)
    v = static_word(_mask(b, word).split("=", 1)[1], b.vars)
    if v is None or name == "CDPATH":  # CDPATH changes what a relative cd means: never resolve it
        b.vars.pop(name, None)
    else:
        b.vars[name] = v


def _drift(b, reason, later=False):
    if later:  # `source f`: f itself still resolves against the current cwd
        b.track.drift = True
    else:
        b.cwds = union(b.cwds, [None])
    b.add("unknown", reason)


def _cd(b, name, rest, dynamic):
    i = 0
    while i < len(rest) and rest[i].startswith("-") and rest[i] != "-":
        i += 1
        if rest[i - 1] == "--":
            break
    target = rest[i] if i < len(rest) else "~"
    b.vars.pop("PWD", None)  # several cwds are possible now
    e = None
    if not dynamic and target != "-" and not (name == "pushd" and re.fullmatch(r"[+-]\d+", target)):
        e = static_word(_mask(b, target), b.vars)
    if e is not None and not os.path.isabs(e) and e.split("/")[0] not in (".", "..") and "CDPATH" in b.track.assigned:
        e = None
    if e is None:
        return _drift(b, f"{name} target {target!r} cannot be resolved statically")
    new = [os.path.realpath(e if os.path.isabs(e) else os.path.join(c, e)) for c in b.cwds if c is not None]
    if None in b.cwds and not os.path.isabs(e):
        new.append(None)
    b.cwds = union(b.cwds, new)
    if any(p and protected.contains_read_protected(p, b.ctx) for p in new):  # maintainer R2: deny, not just track
        b.add("protected_path", f"{name} into, or to an ancestor of, keys/ or experience/")


def _links(b, name, rest):
    t, ctx = b.track, b.ctx
    opts, pos, tdir = _split(rest)
    short = set("".join(o[1:] for o in opts if not o.startswith("--")))
    longs = {o.split("=", 1)[0] for o in opts if o.startswith("--")}
    makes = name == "ln" or (name == "cp" and bool(short & set("sl") or longs & {"--symbolic-link", "--link"}))
    keeps = name in ("mv", "ditto") or (
        name == "cp" and bool(short & set("rRaPd") or longs & {"--recursive", "--archive", "--no-dereference"})) or (
        name == "rsync" and bool(short & set("al") or longs & {"--archive", "--links"}))
    if tdir:
        dest, srcs = tdir, pos
    elif name == "ln" and len(pos) == 1:
        dest, srcs = ".", pos
    elif len(pos) >= 2:
        dest, srcs = pos[-1], pos[:-1]
    else:
        return
    d = b.expand(dest)
    ss = [s for s in (b.expand(x) for x in srcs) if s is not None]
    if d is None:
        return  # unresolvable destination: write_target already fails closed
    for c in b.cwds:
        if c is None:
            continue
        base = os.path.join(c, d)
        entries = [entry(base)] + [entry(os.path.join(base, os.path.basename(s.rstrip("/")))) for s in ss]
        src_entries = [entry(os.path.join(c, s)) for s in ss]
        linky = makes or (keeps and any(os.path.islink(e) or e in t.links for e in src_entries))
        if linky and any(scratch_entry(e, ctx) for e in entries):
            return b.add("protected_path", f"{name} would place a link inside a job's work/ or requests/ folder")
        if linky:
            t.links.update(entries)
        if name == "mv":
            t.removed.update(e for e in src_entries if is_job_folder(e, ctx))


def _scratch_arg(b, w):
    vals = [w, w.split("=", 1)[-1]] + ([w[2:]] if re.match(r"-[A-Za-z].", w) else [])
    return any(protected.is_scratch(p, b.ctx) for v in vals for p in (b.candidates(v) or ()))


def on_simple(b, args, dynamic=False):
    """Rules 1-4 for one simple command; adds results to ``b`` and never short-circuits classes."""
    from . import classes as C  # classes imports this module; resolved at call time

    t, words, prefix = b.track, list(args), []
    if t.drift:
        b.cwds = union(b.cwds, [None])
    while words and C._ASSIGN.match(words[0]):
        prefix.append(words.pop(0))
    if not words:  # pure assignment: persists in the shell
        for w in prefix:
            _assign(b, w)
        return
    t.assigned.update(w.split("=", 1)[0] for w in prefix)  # prefix: only the command's environment
    while words and words[0] in C.KEYWORDS:
        words.pop(0)
    if not words:
        return
    head = static_word(_mask(b, words[0]), b.vars)
    if head is None:
        return  # classes: "command name is dynamic"
    name, rest = os.path.basename(head).casefold(), words[1:]
    if name in ("for", "select"):
        if rest:
            b.vars.pop(rest[0], None)
            t.assigned.add(rest[0])
        return
    if name in C.HEADERS or name in C.WRAPPERS:
        return  # wrappers re-enter classes.simple with the inner command
    if name in ("cd", "pushd"):
        return _cd(b, name, rest, dynamic)
    if name in ("source", "."):
        return _drift(b, f"{name} may change the working directory, which cannot be tracked", later=True)
    if name in ("export", "local", "declare", "typeset", "readonly"):
        for w in rest:
            if C._ASSIGN.match(w):
                _assign(b, w)
    if name in ("read", "unset"):
        for w in rest:
            if _NAME.fullmatch(w):
                b.vars.pop(w, None)
    if name == "printf" and "-v" in rest[:-1]:
        b.vars.pop(rest[rest.index("-v") + 1], None)
    if name not in NO_PATH_ARGS:
        for w in rest:
            if static_word(_mask(b, w), b.vars) is None:
                b.add("unknown", f"{name} argument {w!r} cannot be resolved statically")
                break
    # rule 3: job-folder integrity
    if t.removed and name not in C.READ_ONLY and name not in C.REMOVERS:
        for w in rest:
            if any(protected._under(p, r) for p in (b.candidates(w) or ()) for r in t.removed):
                return b.add("protected_path", "recreates a job work/ or requests/ folder removed earlier in this command")
    if name in C.REMOVERS:
        for w in rest:
            if not w.startswith("-"):
                t.removed.update(p for p in (b.candidates(w) or ()) if is_job_folder(p, b.ctx))
    if name in ("ln", "cp", "mv", "ditto", "rsync", "install"):
        _links(b, name, rest)
    # rule 4: runners pointed at job-folder files
    if C._INTERP.match(name) and rest[:1] == ["-m"] and rest[1:2] and rest[1] in PY_RUNNERS:
        name, rest = rest[1], rest[2:]
    if name in EXEC_RUNNERS and any(_scratch_arg(b, w) for w in rest):
        b.add("self_written_exec", f"{name} runs a file under a job's work/ or requests/ folder; use harness-sandbox-exec")


def note_write(t: Track, cands) -> None:
    if cands is None:
        t.unresolved_writes += 1
    else:
        t.writes.update(cands)


def note_dir(t: Track, cands) -> None:
    """Maintainer R5: a copy/extract fills a whole directory; record it as ``<dir>/`` (see bash_write_targets)."""
    note_write(t, None if cands is None else [p.rstrip("/") + "/" for p in cands])


def bash_write_targets(command: str, ctx):
    """Realpaths the classifier treats as written by ``command``; None stands for an unresolvable target.

    An entry ending in ``/`` is a DIRECTORY marker (cp -r, rsync, ditto, tar -x, unzip, git apply): the
    command may create or change anything below it, so callers must prefix-match it (treat every path
    under ``<dir>/`` as written), never compare it for equality.
    """
    from . import classes as C

    b = C._Bash(ctx)
    try:
        b.run(command)
    except C.ParseError:
        return [None]
    return sorted(b.track.writes) + [None] * bool(b.track.unresolved_writes)
