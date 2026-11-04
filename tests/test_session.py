from __future__ import annotations

import json
import types

import pytest

from harness_core import safety, session
from harness_core.safety import Ctx


class Log:
    def __init__(self):
        self.rows = []

    def append(self, type, payload):
        self.rows.append((type, payload))


@pytest.fixture
def ctx(tmp_path):
    ws, data, root, lc = (tmp_path / n for n in ("ws", "data", "plugin", "lc"))
    for d in (ws, data / "control", data / "business", root / "lib", lc):
        d.mkdir(parents=True)
    (data / "control" / "budgets.json").write_text("{}")
    (data / "control" / "gate.json").write_text('{"timeout": 1790}')
    (data / "business" / "profile.json").write_text('{"currency": "IRR"}')
    (root / "lib" / "x.py").write_text("x = 1\n")
    (lc / "fold_ledger.py").write_text("# fold\n")
    c = Ctx(workspace=ws, data_root=data, plugin_root=root, loop_contract_root=lc, temp_roots=[], log=Log())
    baseline(c)
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
