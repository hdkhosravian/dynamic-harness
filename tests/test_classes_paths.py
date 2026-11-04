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
    "cd src && cat $PWD/keys/k",
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


# ---- maintainer rules, round 2 ------------------------------------------------


def _cwd(ctx, p):
    object.__setattr__(ctx, "cwd", p)


# R1: every word (not only ones with "/"), here-strings and redirect sources resolve against every cwd


@pytest.mark.parametrize("cmd", [
    "cat keys",
    "head experience",
    "base64 -i keys",
    "cd {ws} && cat keys",  # the event cwd stays a candidate after a cd
    "xargs cat <<< keys/k",
    "xargs cat <<< 'x keys/k'",
    "wc -c < keys/k",
])
def test_r1_bare_words_resolve_against_every_cwd(ctx, cmd):
    _cwd(ctx, ctx.data_root)
    assert B(ctx, cmd).cls == "protected_path"


@pytest.mark.parametrize("cmd", ["xargs cat <<< {data}/keys/k", "xargs -I{{}} cat {{}} <<< '{data}/experience/x'"])
def test_r1_here_string_words_are_read_targets(ctx, cmd):
    assert B(ctx, cmd).cls == "protected_path"


@pytest.mark.parametrize("cmd", ["cat keys", "echo keys", "cat <<< keys", "wc -l < src/app.py"])
def test_r1_bare_words_in_the_workspace_stay_allowed(ctx, cmd):
    c = B(ctx, cmd)
    assert not c.irreversible, c


# R2: cd/pushd into, or to an ancestor of, a protected read tree is denied


@pytest.mark.parametrize("cmd", [
    "cd {data}",
    "cd {data}/keys",
    "pushd {data}/experience",
    "cd {data}/..",
    "cd {ws}/.. && ls",
    "cd {data}/keys/sub",
])
def test_r2_cd_into_or_above_protected_tree_is_denied(ctx, cmd):
    assert B(ctx, cmd).cls == "protected_path"


@pytest.mark.parametrize("cmd", ["cd src", "cd {ws}", "pushd {ws}/src && popd", "cd {data}/control"])
def test_r2_cd_elsewhere_stays_allowed(ctx, cmd):
    c = B(ctx, cmd)
    assert not c.irreversible, c


# R3: recursive readers over a tree that contains a protected read tree


@pytest.mark.parametrize("cmd", [
    "diff -r {data} {ws}/src",
    "diff -R {ws}/src {data}",
    "diff --recursive {data} {ws}/src",
    "grep -r secret",  # no path: grep -r searches the cwd
    "rg secret",
    "grep -r --exclude-dir=keys secret",  # experience/ still reachable
    "grep -R --exclude-dir=keys --exclude-dir=experience secret",  # -R follows links into the trees
    "rg --exclude-dir=keys --exclude-dir=experience secret",  # not an rg narrowing
    "find -L {data} -name k",
    "find -H {data}",
    "du -a {data}",
    "du -sh",
    "tar cf - {data} > {ws}/out.tar",
    "tar -cf {ws}/out.tar -C {data} keys",
    "zip -r {ws}/out.zip {data}",
    "rsync -a {data}/ {ws}/copy/",
    "rsync -r {data}/keys {ws}/x",
    "cp --recursive {data} {ws}/copy",
    "cp --archive {data}/keys {ws}/copy",
    "ditto {data} {ws}/copy",
    "git -C {data} ls-files",
    "git -C {data} grep secret",
    "git -C {data}/keys log",
    "git -C {ws}/src diff --no-index ../../data/keys/k /dev/null",
    "git --work-tree={data} ls-files",
    "grep -d recurse secret",  # -d recurse is -r
    "grep --directories=recurse secret",
    "grep --directories recurse secret",
    # option values and words after -- are not exclusions
    "grep -r -e --exclude-dir=keys -e --exclude-dir=experience .",  # with -e, every positional is a path
    "grep -r -m --exclude-dir=keys --exclude-dir=experience secret",
    "grep -r secret -- --exclude-dir=keys --exclude-dir=experience .",
    "grep -re --exclude-dir=keys --exclude-dir=experience .",  # -re: -e takes the next word
    "grep -rm --exclude-dir=keys --exclude-dir=experience secret",
    "grep -d rec secret",  # getopt accepts abbreviated -d values
    "grep --dir=recurse secret",  # and abbreviated long options
    "grep --recur secret",
    "grep -e -- -r .",  # -e takes --, so -r is still an option
    "grep -r --exclude-dir keys --exclude-dir experience secret",  # only the = form narrows
    "grep -r -A1 --exclude-dir=keys --exclude-dir=experience secret",
    "grep -r --exclude-d=keys --exclude-d=experience secret",
    "grep -r '--exclude-dir={{keys,experience}}' secret",  # quoted: grep gets literal braces, matches nothing
    "grep -r --exclude-dir='[k]eys' --exclude-dir='\\experience' secret",  # Python and C fnmatch differ on \
])
def test_r3_recursive_readers_over_protected_trees_are_denied(ctx, cmd):
    _cwd(ctx, ctx.data_root.parent) if "{data}" not in cmd and "{ws}" not in cmd else None
    assert B(ctx, cmd).cls == "protected_path"


@pytest.mark.parametrize("cmd", [
    "grep -r --exclude-dir=keys --exclude-dir=experience secret",
])
def test_r3_grep_narrowed_to_exclude_the_trees_stays_allowed(ctx, cmd):
    _cwd(ctx, ctx.data_root.parent)
    c = B(ctx, cmd)
    assert not c.irreversible, c


@pytest.mark.parametrize("cmd", [
    "grep -r print src", "grep -r print", "rg print", "find src -name '*.py'", "du -sh src", "du -sh",
    "rsync -a src/ out/", "git -C src status", "tar -cf out.tar -C src .", "diff -r src src", "cp -R src out",
])
def test_r3_recursive_readers_in_the_workspace_stay_allowed(ctx, cmd):
    c = B(ctx, cmd)
    assert not c.irreversible, c


# R4: untrusted_files (hook layer, ctime since session start) count as agent-written


def test_r4_ctx_untrusted_files_defaults_to_empty_set(tmp_path):
    assert Ctx(workspace=tmp_path).untrusted_files == set()


@pytest.mark.parametrize("cmd", [
    "pytest", "py.test -q", "python3 -m pytest tests", "python3 -m unittest", "tox", "npm test", "make test",
    "python3 tests/conftest.py",
])
def test_r4_runner_over_untrusted_code_is_self_written_exec(ctx, cmd):
    (ctx.workspace / "tests").mkdir()
    conf = ctx.workspace / "tests" / "conftest.py"
    conf.write_text("import os\n")
    object.__setattr__(ctx, "untrusted_files", {os.path.realpath(conf)})
    assert B(ctx, cmd).cls == "self_written_exec"


def test_r4_runner_pointed_at_a_root_with_untrusted_code(ctx, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "test_x.py").write_text("")
    object.__setattr__(ctx, "untrusted_files", {os.path.realpath(other / "test_x.py")})
    assert B(ctx, "pytest {}".format(other)).cls == "self_written_exec"
    assert B(ctx, "pytest").cls == "local_reversible"  # outside the discovery root


def test_r4_untrusted_non_code_does_not_block_runners(ctx):
    (ctx.workspace / "README.md").write_text("x")
    object.__setattr__(ctx, "untrusted_files", {os.path.realpath(ctx.workspace / "README.md")})
    assert B(ctx, "pytest").cls == "local_reversible"


def test_r4_untrusted_config_in_an_ancestor_dir_counts(ctx, tmp_path):
    for n in ("conftest.py", "stray.py"):
        (tmp_path / n).write_text("")
    object.__setattr__(ctx, "untrusted_files", {os.path.realpath(tmp_path / "stray.py")})
    assert B(ctx, "pytest").cls == "local_reversible"  # a stray module above the root is not auto-loaded
    object.__setattr__(ctx, "untrusted_files", {os.path.realpath(tmp_path / "conftest.py")})
    assert B(ctx, "pytest").cls == "self_written_exec"  # pytest walks up to load conftest.py


def test_env_chdir_into_protected_tree_is_denied(ctx):
    # env -C / --chdir changes the directory like cd (gap left by the read-bypass fixes)
    for cmd in (f"env -C {ctx.data_root} cat keys/hmac.key", f"env --chdir={ctx.data_root}/keys cat hmac.key",
                f"env --chdir {ctx.data_root} ls keys"):
        r = classes.classify("Bash", {"command": cmd}, None, ctx)
        assert r.irreversible, cmd


# review of ddf4b30..de86b27


@pytest.mark.parametrize("cmd", [
    "tar -xf a.tar -C src && python3 src/app.py",
    "cp -r /tmp/y/. src && python3 src/app.py",
    "rsync -a /tmp/y/ src/ && bash src/run.sh",
    "tar -xf a.tar -C src && pytest",
])
def test_running_code_inside_a_directory_filled_earlier_in_the_command_is_self_written(ctx, cmd):
    assert B(ctx, cmd).cls == "self_written_exec"


@pytest.mark.parametrize("cmd", [
    "find -f {data} -- -type f -exec cat {{}} +",
    "find -L -f {data} -- -name k",
    "find -f{data} -- -name k",
])
def test_bsd_find_dash_f_start_path_is_checked(ctx, cmd):
    assert B(ctx, cmd).cls == "protected_path"


@pytest.mark.parametrize("cmd", [
    "env -C{data} cat keys/k",
    "env -iC {data} cat keys/k",
    "env -iC{data} cat keys/k",
])
def test_env_attached_or_clustered_chdir_is_tracked(ctx, cmd):
    assert B(ctx, cmd).irreversible


def test_env_unknown_short_cluster_fails_closed(ctx):
    assert B(ctx, "env -iZ ls").cls == "unknown"


@pytest.mark.parametrize("cmd", [
    "PYTEST_ADDOPTS=../tests pytest", "env PYTEST_ADDOPTS=../tests pytest", "pytest @args.txt",
    "export PYTEST_ADDOPTS=../tests; pytest",
])
def test_runner_arguments_from_env_or_file_fail_closed(ctx, cmd):
    (ctx.workspace / "tests").mkdir()
    conf = ctx.workspace / "tests" / "conftest.py"
    conf.write_text("import os\n")
    (ctx.workspace / "src" / "args.txt").write_text("../tests\n")
    object.__setattr__(ctx, "untrusted_files", {os.path.realpath(conf)})
    _cwd(ctx, ctx.workspace / "src")
    assert B(ctx, cmd).irreversible, cmd
