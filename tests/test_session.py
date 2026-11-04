from __future__ import annotations

import json
import os
import time
import types

import pytest

from harness_core import setup, safety, session
from harness_core.safety import Ctx


class Log:
    def __init__(self):
        self.rows = []

    def append(self, type, payload):
        self.rows.append((type, payload))


@pytest.fixture
def ctx(tmp_path):
    ws, data, root, lc, home = (tmp_path / n for n in ("ws", "data", "plugin", "lc", "home"))
    for d in (ws, data / "control", data / "business", root / "lib", lc / ".claude-plugin", home):
        d.mkdir(parents=True)
    (lc / ".claude-plugin" / "plugin.json").write_text('{"name": "loop-contract", "version": "1.1.0"}')
    (data / "control" / "budgets.json").write_text("{}")
    (data / "control" / "gate.json").write_text('{"timeout": 1790}')
    (data / "business" / "profile.json").write_text('{"currency": "IRR"}')
    (root / "lib" / "x.py").write_text("x = 1\n")
    (lc / "fold_ledger.py").write_text("# fold\n")
    c = Ctx(workspace=ws, data_root=data, plugin_root=root, loop_contract_root=lc, home=home, temp_roots=[], log=Log())
    baseline(c)
    setup._apply(c, setup.plan(c))  # what /dynamic-harness:setup confirm leaves behind
    return c


def baseline(ctx, **override):
    row = {"ts": "2026-11-01T00:00:00Z", "hashes": {**session.current_hashes(ctx), **override}}
    with open(ctx.data_root / "config_log.jsonl", "a") as fh:
        fh.write(json.dumps(row) + "\n")


def test_hashes_cover_layers(ctx):
    assert set(session.current_hashes(ctx)) >= {"control", "business", "gate", "plugin", "loop_contract"}


def test_clean_start(ctx):
    assert session.on_start({"permission_mode": "default"}, ctx) == {}


@pytest.mark.parametrize(
    "rel, layer",
    [
        ("data/control/budgets.json", "control"),
        ("data/business/profile.json", "business"),
        ("data/control/gate.json", "gate"),
        ("plugin/lib/x.py", "plugin"),
        ("lc/fold_ledger.py", "loop_contract"),
    ],
)
def test_tamper_detected(ctx, tmp_path, rel, layer):
    (tmp_path / rel).write_text("tampered")
    out = session.on_start({}, ctx)
    assert out["decision"] == "block" and out["state"] == "STOPPED_TAMPER"
    assert layer in out["mismatch"]
    assert ctx.log.rows[-1][0] == "STOPPED_TAMPER"


def test_added_file_detected(ctx):
    (ctx.data_root / "business" / "new.json").write_text("{}")
    assert session.on_start({}, ctx)["state"] == "STOPPED_TAMPER"


def test_symlink_in_tree_is_tamper(ctx, tmp_path):
    (tmp_path / "outside").write_text("x")
    (ctx.plugin_root / "lib" / "link.py").symlink_to(tmp_path / "outside")
    assert session.on_start({}, ctx)["state"] == "STOPPED_TAMPER"


def test_never_rebaselines(ctx):
    (ctx.data_root / "control" / "budgets.json").write_text("tampered")
    log_before = (ctx.data_root / "config_log.jsonl").read_bytes()
    assert session.on_start({}, ctx)["state"] == "STOPPED_TAMPER"
    assert session.on_start({}, ctx)["state"] == "STOPPED_TAMPER"
    assert (ctx.data_root / "config_log.jsonl").read_bytes() == log_before


def test_latest_entry_per_layer_wins(ctx):
    (ctx.data_root / "business" / "profile.json").write_text('{"currency": "USD"}')
    assert session.on_start({}, ctx)["state"] == "STOPPED_TAMPER"
    # an owner change through the writer appends the expected hash for that layer
    with open(ctx.data_root / "config_log.jsonl", "a") as fh:
        fh.write(json.dumps({"layer": "business", "sha": session.current_hashes(ctx)["business"]}) + "\n")
    assert session.on_start({}, ctx) == {}


def test_missing_config_log_refuses(ctx):
    (ctx.data_root / "config_log.jsonl").unlink()
    out = session.on_start({}, ctx)
    assert out["decision"] == "block" and out["state"] == "REFUSED"


def test_corrupt_config_log_is_tamper(ctx):
    with open(ctx.data_root / "config_log.jsonl", "a") as fh:
        fh.write("{not json\n")
    assert session.on_start({}, ctx)["state"] == "STOPPED_TAMPER"


def test_bypass_permissions_refused(ctx):
    ctx.permission_mode = "bypassPermissions"
    out = session.on_start({"permission_mode": "bypassPermissions"}, ctx)
    assert out["decision"] == "block" and "bypassPermissions" in out["reason"]
    assert session.on_start({"permission_mode": "bypassPermissions"}, Ctx(workspace=ctx.workspace))["decision"] == "block"


def test_canary_failure_refuses(ctx, monkeypatch):
    monkeypatch.setattr(safety, "on_pre", lambda evt, c: {})
    out = session.on_start({}, ctx)
    assert out["decision"] == "block" and out["state"] == "CANARY_FAILED"


def test_corrupt_live_class_table_fails_closed(ctx):
    (ctx.data_root / "control" / "classes.json").write_text("{}")
    assert safety.on_pre({"tool_name": "Read", "tool_input": {"file_path": "/x"}}, ctx)["decision"] == "deny"


# --- minimum Claude Code version (OQ-4 spike S1: onFailure=block needs >= 2.1.295) -------------

@pytest.mark.parametrize("ver, ok", [("2.1.295", True), ("2.1.296 (Claude Code)", True), ("2.2.0", True),
                                     ("2.1.294", False), ("1.9.999", False), ("garbage", False)])
def test_version_gate(ctx, monkeypatch, ver, ok):
    monkeypatch.setenv("CLAUDE_CODE_VERSION", ver)
    out = session.on_start({}, ctx)
    if ok:
        assert out == {}
    else:
        assert out["decision"] == "block" and out["state"] == "REFUSED"
        assert "2.1.295" in out["reason"] and "above T0" in out["reason"]


def test_version_falls_back_to_claude_cli(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_VERSION", raising=False)
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return types.SimpleNamespace(stdout="2.1.296 (Claude Code)\n", returncode=0)

    assert session.claude_version(run=run) == (2, 1, 296)
    assert calls == [["claude", "--version"]]


def test_version_unknown_when_cli_missing(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_VERSION", raising=False)

    def run(argv, **kw):
        raise FileNotFoundError("claude")

    assert session.claude_version(run=run) is None


# --- loop-contract root (finding 6) -------------------------------------------------------

def test_missing_loop_contract_root_refused(ctx):
    ctx.loop_contract_root = None
    out = session.on_start({}, ctx)
    assert out["state"] == "REFUSED" and "loop-contract" in out["reason"]


@pytest.mark.parametrize("manifest", ['{"version": "1.0.9"}', '{"version": "x"}', "{not json", None])
def test_old_or_unreadable_loop_contract_refused(ctx, manifest):
    f = ctx.loop_contract_root / ".claude-plugin" / "plugin.json"
    if manifest is None:
        f.unlink()
    else:
        f.write_text(manifest)
    out = session.on_start({}, ctx)
    assert out["state"] == "REFUSED" and "1.1.0" in out["reason"]


def test_newer_loop_contract_accepted(ctx):
    (ctx.loop_contract_root / ".claude-plugin" / "plugin.json").write_text('{"version": "1.10.0"}')
    with open(ctx.data_root / "config_log.jsonl", "a") as fh:
        fh.write(json.dumps({"layer": "loop_contract", "sha": session.current_hashes(ctx)["loop_contract"]}) + "\n")
    assert session.on_start({}, ctx) == {}


def test_layer_missing_from_config_log_is_tamper(ctx):
    rows = [json.loads(x) for x in (ctx.data_root / "config_log.jsonl").read_text().splitlines()]
    rows[0]["hashes"].pop("loop_contract")
    (ctx.data_root / "config_log.jsonl").write_text(json.dumps(rows[0]) + "\n")
    out = session.on_start({}, ctx)
    assert out["state"] == "STOPPED_TAMPER" and "loop_contract" in out["mismatch"]


# --- status.json + session snapshots (findings 1, 2, 7) -------------------------------------

def status(ctx, sid="s1"):
    return json.loads((ctx.data_root / "sessions" / sid / "status.json").read_text())


def test_ok_status_written(ctx):
    assert session.on_start({"session_id": "s1"}, ctx) == {}
    st = status(ctx)
    assert st["ok"] is True and st["reasons"] == [] and st["canary"] is True
    assert abs(st["ts"] - time.time()) < 60 and st["cwd"] == str(ctx.workspace)


@pytest.mark.parametrize("breakit, state", [
    (lambda c: (c.data_root / "config_log.jsonl").unlink(), "REFUSED"),
    (lambda c: (c.data_root / "control" / "budgets.json").write_text("x"), "STOPPED_TAMPER"),
    (lambda c: setattr(c, "loop_contract_root", None), "REFUSED"),
])
def test_refusal_status_written(ctx, breakit, state):
    breakit(ctx)
    out = session.on_start({"session_id": "s1"}, ctx)
    st = status(ctx)
    assert out["state"] == state and st["ok"] is False and st["reasons"] == [out["reason"]]


def test_version_refusal_status_written(ctx, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_VERSION", "2.1.0")
    session.on_start({"session_id": "s1"}, ctx)
    assert status(ctx)["ok"] is False


def test_canary_failure_status(ctx, monkeypatch):
    monkeypatch.setattr(safety, "on_pre", lambda evt, c: {})
    session.on_start({"session_id": "s1"}, ctx)
    assert status(ctx)["ok"] is False and status(ctx)["canary"] is False


def test_crash_mid_checks_leaves_a_failed_status(ctx, monkeypatch):
    assert session.on_start({"session_id": "s1"}, ctx) == {}  # an earlier ok status (e.g. before a resume)

    def boom(c):
        raise RuntimeError("crash")

    monkeypatch.setattr(session, "current_hashes", boom)
    with pytest.raises(RuntimeError):
        session.on_start({"session_id": "s1"}, ctx)
    assert status(ctx)["ok"] is False


def test_canary_does_not_log_its_own_deny(ctx):
    session.on_start({"session_id": "s1"}, ctx)
    assert [t for t, _ in ctx.log.rows] == []


def test_session_start_starts_written_record(ctx):
    session.on_start({"session_id": "s1", "source": "startup"}, ctx)
    sdir = ctx.data_root / "sessions" / "s1"
    assert json.loads((sdir / "written.json").read_text()) == []
    assert isinstance(json.loads((sdir / "tracked.json").read_text()), dict)


def test_settings_baseline_snapshot(ctx):
    (ctx.home / ".claude").mkdir(exist_ok=True)
    (ctx.home / ".claude" / "settings.json").write_text('{"permissions": {"deny": ["Read(~/.ssh/**)"]}}')
    (ctx.workspace / ".claude").mkdir(exist_ok=True)
    (ctx.workspace / ".claude" / "settings.json").write_text('{"sandbox": {"enabled": true}}')
    (ctx.workspace / ".claude" / "settings.local.json").write_text("{bad")
    session.on_start({"session_id": "s1"}, ctx)
    base = json.loads((ctx.data_root / "sessions" / "s1" / "settings_baseline.json").read_text())
    r = os.path.realpath
    assert base[r(ctx.home / ".claude" / "settings.json")] == {"permissions": {"deny": ["Read(~/.ssh/**)"]}}
    assert base[r(ctx.workspace / ".claude" / "settings.json")] == {"sandbox": {"enabled": True}}
    assert base[r(ctx.workspace / ".claude" / "settings.local.json")] == {}  # unreadable: judged from nothing
    # a refused session still snapshots, so ConfigChange keeps a baseline
    (ctx.data_root / "config_log.jsonl").unlink()
    session.on_start({"session_id": "s2"}, ctx)
    assert (ctx.data_root / "sessions" / "s2" / "settings_baseline.json").is_file()


def test_bad_session_id_raises(ctx):
    with pytest.raises(ValueError):
        session.on_start({"session_id": "../x"}, ctx)


@pytest.mark.parametrize("source", ["compact", "resume", "clear", None])
def test_session_start_keeps_the_written_record_unless_startup(ctx, source):
    session.on_start({"session_id": "s1", "source": "startup"}, ctx)
    sdir = ctx.data_root / "sessions" / "s1"
    (sdir / "written.json").write_text(json.dumps(["/ws/earlier.py"]))
    tracked, started = (sdir / "tracked.json").read_bytes(), (sdir / "started.json").read_bytes()
    evt = {"session_id": "s1"} if source is None else {"session_id": "s1", "source": source}
    assert session.on_start(evt, ctx) == {}
    assert json.loads((sdir / "written.json").read_text()) == ["/ws/earlier.py"]
    # the ctime baseline must not move either, or files written before the compact would look pre-existing
    assert (sdir / "tracked.json").read_bytes() == tracked and (sdir / "started.json").read_bytes() == started
    session.on_start({"session_id": "s1", "source": "startup"}, ctx)
    assert json.loads((sdir / "written.json").read_text()) == []


def test_non_startup_without_a_record_stays_strict(ctx):
    # e.g. a fork into a new session id: what the parent wrote is unknown, so no empty record is invented
    session.on_start({"session_id": "s2", "source": "fork"}, ctx)
    sdir = ctx.data_root / "sessions" / "s2"
    assert not (sdir / "written.json").exists() and (sdir / "tracked.json").exists()


def test_non_startup_with_a_lost_baseline_trusts_no_tracked_file(tmp_path):
    # tracked.json present, started.json lost (pre-upgrade session): a re-snapshot would trust files the agent changed
    import subprocess
    from harness_core import written

    ws, data = tmp_path / "ws", tmp_path / "data"
    ws.mkdir()
    data.mkdir()
    (ws / "foo.py").write_text("")
    git = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=b"foo.py\0")
    written.start(data, "s1", ws, run=git, source="startup")
    sdir = data / "sessions" / "s1"
    assert json.loads((sdir / "tracked.json").read_text())  # foo.py is tracked
    (sdir / "started.json").unlink()
    written.start(data, "s1", ws, run=git, source="resume")
    assert json.loads((sdir / "tracked.json").read_text()) == {}
    assert not (sdir / "written.json").exists()


def test_session_without_a_working_os_sandbox_is_refused(ctx, monkeypatch):
    # e.g. Ubuntu with unprivileged user namespaces blocked, or bubblewrap not installed
    from harness_core import sandbox_exec
    monkeypatch.setattr(sandbox_exec, "probe", lambda: "bwrap cannot create a sandbox here")
    out = session.on_start({"session_id": "s1"}, ctx)
    assert out["state"] == "REFUSED" and "bwrap cannot create a sandbox here" in out["reason"]
    assert status(ctx)["ok"] is False


def test_session_without_setup_settings_is_refused(ctx):
    # e.g. setup undone, or a project opened before setup ran
    (ctx.workspace / ".claude" / "settings.local.json").unlink()
    out = session.on_start({"session_id": "s1"}, ctx)
    assert out["state"] == "REFUSED" and "/dynamic-harness:setup" in out["reason"]
    assert "sandbox.enabled" in out["reason"] and status(ctx)["ok"] is False


# --- R-5: only approved procedures load ---


def _skill(ctx, rel="skills/x/SKILL.md", body="# x\n"):
    f = ctx.plugin_root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


def test_an_unapproved_skill_stops_the_session_as_tamper(ctx):
    _skill(ctx)
    baseline(ctx)  # re-record the plugin hash: the skill is not a hash mismatch, only unapproved
    out = session.on_start({"session_id": "s1"}, ctx)
    assert out["state"] == "STOPPED_TAMPER" and "skills/x/SKILL.md" in out["reason"]


def test_an_approved_skill_with_its_hash_passes(ctx):
    import hashlib
    f = _skill(ctx)
    (ctx.data_root / "control" / "approved_procedures.json").write_text(json.dumps({"version": 1, "procedures": [
        {"path": "skills/x/SKILL.md", "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "approver": "op"}]}))
    baseline(ctx)
    assert session.on_start({"session_id": "s1"}, ctx).get("decision") != "block"


def test_a_changed_skill_is_no_longer_approved(ctx):
    import hashlib
    f = _skill(ctx)
    (ctx.data_root / "control" / "approved_procedures.json").write_text(json.dumps({"version": 1, "procedures": [
        {"path": "skills/x/SKILL.md", "sha256": hashlib.sha256(b"# other\n").hexdigest(), "approver": "op"}]}))
    baseline(ctx)
    assert session.on_start({"session_id": "s1"}, ctx)["state"] == "STOPPED_TAMPER"


def test_the_release_approves_every_shipped_skill():
    import hashlib
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    approved = {p["path"]: p["sha256"] for p in json.loads((root / "control/defaults/approved_procedures.json").read_text())["procedures"]}
    shipped = {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest() for f in root.glob("skills/**/SKILL.md")}
    assert shipped and shipped == approved


def test_a_symlinked_skill_directory_is_unapproved(ctx, tmp_path):
    other = tmp_path / "elsewhere" / "evil"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("# evil\n")
    (ctx.plugin_root / "skills").mkdir()
    (ctx.plugin_root / "skills" / "evil").symlink_to(other)
    baseline(ctx)
    out = session.on_start({"session_id": "s1"}, ctx)
    assert out["state"] == "STOPPED_TAMPER" and "skills/evil" in out["reason"]
