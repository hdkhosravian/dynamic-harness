"""Fail-closed path / working-directory tracking in the Bash classifier (maintainer rules 1-5)."""
from __future__ import annotations

import os

import pytest

from harness_core import classes
from harness_core.safety import Ctx

JOB = ".claude/loops/j1"


@pytest.fixture
def ctx(tmp_path):
    ws, data, root, home = (tmp_path / n for n in ("ws", "data", "plugin", "home"))
    for d in (ws, data, root, home):
        d.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("print(1)\n")
    (ws / "env.sh").write_text("cd /\n")
    (data / "keys").mkdir()
    (data / "keys" / "k").write_text("secret\n")
    (data / "experience").mkdir()
    work, req = ws / JOB / "work", ws / JOB / "requests"
    work.mkdir(parents=True)
    req.mkdir()
    (work / "x.py").write_text("print(1)\n")
    (work / "run.sh").write_text("echo hi\n")
    (work / "Makefile").write_text("all:\n\techo hi\n")
    (req / "r.sh").write_text("echo hi\n")
    os.symlink(str(home), str(ws / "lnk"))
    (root / "bin").mkdir()
    (root / "bin" / "harness-sandbox-exec").write_text("#!/bin/sh\n")  # trusted harness CLI stub
    return Ctx(workspace=ws, data_root=data, plugin_root=root, home=home, job="j1",
               temp_roots=[tmp_path / "tmp"])


def B(ctx, cmd):
    cmd = cmd.format(ws=ctx.workspace, data=ctx.data_root, home=ctx.home, job=JOB)
    return classes.classify("Bash", {"command": cmd}, {}, ctx)


def T(ctx, tool, **inp):
    return classes.classify(tool, inp, {}, ctx)


# ---- rule 1: unresolvable words fail closed; static assignments resolve ----


@pytest.mark.parametrize("cmd", [
    "D={data}/keys; cat $D/k",
    'D={data}; cat "$D"/keys/k',
    "D={data} && E=$D/keys && cat $E/k",
    "export D={data}/keys; cat ${{D}}/k",
    "D={data}/keys; cat < $D/k",
])
def test_static_assignment_resolves_protected_reads(ctx, cmd):
    assert B(ctx, cmd).cls == "protected_path"


@pytest.mark.parametrize("cmd", [
    "D=$(printf {data}); cat $D/keys/k",
    "cat $(printf %s/ke%s {data} ys)/k",
    'cat < "$(printf x)"',
    "for f in {data}/keys/*; do cat $f; done",
    "read f; cat $f",
    "D=src; D=$(pwd); cat $D/app.py",
    'cd "$(mktemp -d)" && cat k',
    "cat ${{F:-{data}/keys/k}}",
    "cat $UNSET_VAR/k",
    "D={data}/keys cat $D/k",  # prefix assignment does not set D for the expansion
])
def test_unresolvable_words_fail_closed(ctx, cmd):
    assert B(ctx, cmd).cls == "unknown"


@pytest.mark.parametrize("cmd", [
    "D=src; cat $D/app.py",
    'D=src && ls "$D"',
    "awk '{{print $1}}' src/app.py",
    "grep -E 'print$' src/app.py",
    "sed -n '$p' src/app.py",
    'echo "$(date)" $USER',
    'test -n "$X" && echo yes',
    "X=$(date); echo $X",
    "cat \\$weird",
    "cd src && cat app.py",
    "cd -P src && cat app.py",
    "sleep 1 & kill $!",
])
def test_resolvable_or_non_path_words_stay_allowed(ctx, cmd):
    c = B(ctx, cmd)
    assert not c.irreversible, c


# ---- rule 2: working-directory drift ---------------------------------------


@pytest.mark.parametrize("cmd", [
    'eval "cd {data}"; cat keys/k',
    "bash -c 'cd {data}'; cat keys/k",
    "echo $(cd {data}) && cat keys/k",
    "builtin cd {data} && cat keys/k",
    "command cd {data} && cat keys/k",
    "(cd {data}) ; cat keys/k",
    "{{ cd {data}; }} ; cat keys/k",
    "cd -P {data} && cat keys/k",
    "cd -L -- {data} && cat keys/k",
    "pushd {data} && cat keys/k",
])
def test_cd_in_children_and_with_options_propagates(ctx, cmd):
    assert B(ctx, cmd).cls == "protected_path"


@pytest.mark.parametrize("cmd", [
    "source {ws}/env.sh && cat keys/k",
    ". ./env.sh; cat keys/k",
    "cd - && cat keys/k",
    "CDPATH={data} cd keys && cat k",
    "cd {data} && cat $PWD/keys/k",
    "echo {data} | xargs cd; cat keys/k",
    "cd ~nobody && ls",
])
def test_untrackable_cwd_change_fails_closed(ctx, cmd):
    assert B(ctx, cmd).cls == "unknown"


def test_function_body_cd_is_denied(ctx):
    assert B(ctx, "f() {{ cd {data}; }}; f; cat keys/k").irreversible


@pytest.mark.parametrize("cmd", [
    "source ./env.sh && harness-sandbox-exec -- true",
    'eval "cd {home}" && harness-sandbox-exec -- true',
    "echo $(cd {home}) && harness-sandbox-exec -- true",
])
def test_sandbox_exec_sees_drifted_cwd(ctx, cmd):
    assert B(ctx, cmd).irreversible


# ---- rule 3: job-folder integrity ------------------------------------------


@pytest.mark.parametrize("cmd", [
    "ln -s {home} {job}/work/d",
    "ln -s ../../../.. {ws}/{job}/requests/up",
    "ln src/app.py {job}/work/hard",
    "ln -sfn {home} {job}/work",
    "cd {job}/work && ln -s {home} d",
    "cp -s {ws}/src/app.py {job}/work/l",
    "cp -l src/app.py {job}/work/l",
    "cp -al src {job}/work/",
    "cp --symbolic-link src/app.py {job}/work/",
    "ln -s {home} {ws}/tmplink && mv {ws}/tmplink {job}/work/",
    "mv lnk {job}/work/lnk",
    "cp -R lnk {job}/work/",
    "rm -rf {job}/work && mkdir {job}/work",
    "rm -rf {job}/requests; mkdir -p {job}/requests/x",
    "ln -s {home} {job}/work/../work/d",
])
def test_links_into_job_folders_are_denied(ctx, cmd):
    assert B(ctx, cmd).cls == "protected_path"


@pytest.mark.parametrize("cmd", [
    "cp src/app.py {job}/work/copy.py",
    "cp -r src {job}/work/",
    "rm -rf {job}/work/tmp && mkdir {job}/work/tmp",
    "ln -s src/app.py {ws}/alias.py",
    "mkdir -p {job}/work/out && ls {job}/work",
    "rm -rf {job}/work",
])
def test_ordinary_job_folder_writes_stay_allowed(ctx, cmd):
    c = B(ctx, cmd)
    assert not c.irreversible, c


# ---- rule 4: job-folder files always count as agent-written -----------------


@pytest.mark.parametrize("cmd", [
    "python3 {job}/work/x.py",
    "bash {job}/work/run.sh",
    "sh {ws}/{job}/work/run.sh",
    "zsh {job}/requests/r.sh",
    "./{job}/work/run.sh",
    "cd {job}/work && ./run.sh",
    "cd {job}/work && python3 x.py",
    "source {job}/work/run.sh",
    ". {job}/requests/r.sh",
    "make -f {job}/work/Makefile",
    "make -C {job}/work",
    "make -f{job}/work/Makefile all",
    "bash < {job}/work/run.sh",
    "python3 -m pytest {job}/work/x.py",
])
def test_running_job_folder_files_is_self_written_exec(ctx, cmd):
    assert B(ctx, cmd).cls == "self_written_exec"


@pytest.mark.parametrize("cmd", [
    "harness-sandbox-exec -- python3 {job}/work/x.py",
    "harness-sandbox-exec -- bash {job}/work/run.sh",
    "cat {job}/work/x.py",
    "python3 src/app.py",
])
def test_job_folder_files_ok_inside_sandbox_or_when_read(ctx, cmd):
    c = B(ctx, cmd)
    assert not c.irreversible, c


# ---- rule 5: relative search patterns resolve against the workspace --------


@pytest.mark.parametrize("tool, inp", [
    ("Glob", {"pattern": "../data/keys/*"}),
    ("Glob", {"pattern": "../data/exp*"}),
    ("Glob", {"pattern": "../../data/keys/**", "path": "src"}),
    ("Glob", {"pattern": "src/**/../../../data/keys/*"}),
    ("Glob", {"pattern": "{data}/exp*"}),
    ("Grep", {"pattern": "x", "path": "../data/keys"}),
    ("Read", {"file_path": "../data/keys/k"}),
    ("LS", {"path": "../data"}),
])
def test_search_patterns_climbing_to_protected_trees_are_denied(ctx, tool, inp):
    inp = {k: v.format(data=ctx.data_root) for k, v in inp.items()}
    assert T(ctx, tool, **inp).cls == "protected_path"


@pytest.mark.parametrize("tool, inp", [
    ("Glob", {"pattern": "**/*.py"}),
    ("Glob", {"pattern": "src/*.py"}),
    ("Glob", {"pattern": "../ws/src/*.py"}),
    ("Grep", {"pattern": "print", "path": "src"}),
    ("Read", {"file_path": "src/app.py"}),
])
def test_search_patterns_inside_workspace_stay_allowed(ctx, tool, inp):
    c = T(ctx, tool, **inp)
    assert not c.irreversible, c


# ---- handoff: write-target API, event cwd, ctime-based self-written check ---


def test_bash_write_targets_lists_resolved_and_unresolvable(ctx):
    from harness_core import pathtrack
    ws = os.path.realpath(ctx.workspace)
    got = pathtrack.bash_write_targets("echo x > a.txt && touch src/b && cp src/app.py c.py", ctx)
    assert {os.path.join(ws, "a.txt"), os.path.join(ws, "src", "b"), os.path.join(ws, "c.py")} <= set(got)
    assert None not in got
    assert pathtrack.bash_write_targets('echo x > "$(mktemp)"', ctx)[-1] is None
    assert pathtrack.bash_write_targets("cat src/app.py", ctx) == []


def test_relative_paths_resolve_against_event_cwd(ctx):
    object.__setattr__(ctx, "cwd", ctx.data_root)
    assert B(ctx, "cat keys/k").cls == "protected_path"
    assert T(ctx, "Glob", pattern="keys/*").cls == "protected_path"
    from harness_core import pathtrack
    object.__setattr__(ctx, "cwd", ctx.workspace / "src")
    assert pathtrack.bash_write_targets("touch n", ctx) == [os.path.join(os.path.realpath(ctx.workspace), "src", "n")]


def test_bash_write_targets_includes_denied_or_approvable_targets(ctx):
    from harness_core import pathtrack
    got = pathtrack.bash_write_targets("touch {}/n".format(ctx.data_root), ctx)
    assert got == [os.path.join(os.path.realpath(ctx.data_root), "n")]


def test_self_written_uses_ctime_not_mtime(ctx):
    import time
    old = ctx.workspace / "old.py"
    old.write_text("print(1)\n")
    os.utime(old, (time.time() + 3600,) * 2)  # future mtime, ctime stays before the task
    ctx.task_started = os.stat(old).st_ctime + 0.01
    assert B(ctx, "python3 old.py").cls == "local_reversible"
    new = ctx.workspace / "new.py"
    new.write_text("print(1)\n")
    os.utime(new, (0, 0))  # backdated mtime cannot hide a file changed this task
    ctx.task_started = os.stat(new).st_ctime - 0.01
    assert B(ctx, "python3 new.py").cls == "self_written_exec"
