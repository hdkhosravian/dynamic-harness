"""Process invariants R-1, R-4, R-6 (spec §Requirements), P2 scope."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from harness_core import classes, safety, session, setup
from harness_core.safety import RESEARCHER, Ctx

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ctx(tmp_path):
    ws, data, root, home = (tmp_path / n for n in ("ws", "data", "plugin", "home"))
    for d in (ws, data / "control", data / "business", root, home):
        d.mkdir(parents=True)
    return Ctx(workspace=ws, data_root=data, plugin_root=root, home=home, job="j1",
               temp_roots=[tmp_path / "tmp"], allowlist=[],
               connector_registry={"mcp__gmail__create_draft": "draft"})


def pre(ctx, tool, **inp):
    return safety.on_pre({"tool_name": tool, "tool_input": inp}, ctx)


# ---- R-1: in T0 only the safety hooks block or alter a tool call ----------


def test_r1_hooks_json_registers_only_safety_entry_points():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    # PostToolUse only records writes (written.py); it never blocks or alters a T0 call
    # PostToolUseFailure records writes of failed calls too; neither blocks or alters a T0 call
    # UserPromptSubmit acts only on a typed /dynamic-harness:setup; every other prompt passes untouched (test_setup)
    assert set(hooks) == {"PreToolUse", "PostToolUse", "PostToolUseFailure", "SessionStart", "ConfigChange",
                          "UserPromptSubmit", "SubagentStop", "SubagentStart"}
    for event, entries in hooks.items():
        for e in entries:
            for h in e["hooks"]:
                assert h["command"].startswith("/usr/bin/python3 -I ")
                assert h["command"].endswith(f"harness-hook\" {event}") or (
                    event == "PostToolUse" and h["command"].endswith('harness-hook" PostToolUse --gate'))


@pytest.mark.parametrize(
    "tool, inp",
    [
        ("Read", {"file_path": "README.md"}),
        ("Grep", {"pattern": "x"}),
        ("Bash", {"command": "ls -la && git status"}),
        ("Write", {"file_path": "notes.md", "content": "x"}),
        ("Edit", {"file_path": "src/a.py", "old_string": "a", "new_string": "b"}),
    ],
)
def test_r1_safety_never_alters_an_allowed_call(ctx, tool, inp):
    evt = {"tool_name": tool, "tool_input": dict(inp)}
    out = safety.on_pre(evt, ctx)
    assert out == {}  # no updatedInput, no permission prompt, no context injection
    assert evt["tool_input"] == inp


def test_r1_deny_carries_no_rewritten_input(ctx):
    out = pre(ctx, "Bash", command="git push")
    assert set(out) == {"decision", "reason"}


# ---- R-4: no irreversible class executes without approval (v1: denied) ----

SAMPLES = {
    "external_message": ("mcp__gmail__send", {"to": "a@b.c"}, {"mcp__gmail__send": {"tag": "write", "class": "external_message"}}),
    "money": ("Bash", {"command": "stripe refunds create --charge ch_1"}, {}),
    "payee_change": ("mcp__bank__set_payee", {"iban": "IR00"}, {"mcp__bank__set_payee": {"tag": "write", "class": "payee_change"}}),
    "price_publish": ("mcp__shop__set_price", {"sku": "a"}, {"mcp__shop__set_price": {"tag": "write", "class": "price_publish"}}),
    "accounting_entry": ("mcp__books__journal", {"x": 1}, {"mcp__books__journal": {"tag": "write", "class": "accounting_entry"}}),
    "destructive": ("Bash", {"command": "rm -rf src"}, {}),
    "local_app_automation": ("Bash", {"command": "osascript -e 'beep'"}, {}),
    "external_write": ("mcp__crm__update", {"id": 1}, {"mcp__crm__update": "write"}),
    "automation_create": ("Bash", {"command": "launchctl load job.plist"}, {}),
    "egress_offlist": ("WebFetch", {"url": "https://unlisted.example/"}, {}),
    "egress_with_data": ("WebSearch", {"query": "anything"}, {}),
    "unknown_mcp_write": ("mcp__unregistered__do", {}, {}),
    "self_written_exec": ("Bash", {"command": "python3 -c 'print(1)'"}, {}),
    "protected_path": ("Write", {"file_path": ".claude/settings.json"}, {}),
    "unknown": ("Bash", {"command": "frobnicate --now"}, {}),
}


def test_r4_v1_action_table():
    table = classes.load_table(None)["classes"]
    for name, row in table.items():
        assert row["v1_action"] == ("allow" if not row["irreversible"] else "deny"), name
    assert {n for n, r in table.items() if not r["irreversible"]} == {"local_reversible", "draft_create", "read"}


@pytest.mark.parametrize("cls", sorted(SAMPLES))
def test_r4_every_irreversible_class_denied(ctx, cls):
    tool, inp, reg = SAMPLES[cls]
    ctx.connector_registry = reg
    assert classes.classify(tool, inp, reg, ctx).cls == cls
    assert pre(ctx, tool, **inp)["decision"] == "deny"


def test_r4_every_table_class_has_a_sample():
    table = classes.load_table(None)["classes"]
    assert {n for n, r in table.items() if r["irreversible"]} == set(SAMPLES)


def test_r4_v1_action(ctx):
    """The single v1 exception: researcher-lane GETs under a per-job grant that pass the query guard."""
    url = "https://news.example.ir/market/report"
    assert pre(ctx, "WebFetch", url=url)["decision"] == "deny"  # main agent
    ctx.agent_type = RESEARCHER
    assert pre(ctx, "WebFetch", url=url)["decision"] == "deny"  # no grant
    ctx.grant = {"domains": ["news.example.ir"], "active": True, "expires_at": time.time() + 3600}
    assert pre(ctx, "WebFetch", url=url) == {}  # granted GET
    assert pre(ctx, "WebSearch", query="retail market trends") == {}
    assert pre(ctx, "WebFetch", url="https://other.example/a")["decision"] == "deny"  # off-grant
    assert pre(ctx, "WebFetch", url="https://news.example.ir/p/98765432")["decision"] == "deny"  # guard
    assert pre(ctx, "Bash", command="curl https://news.example.ir/a")["decision"] == "deny"  # not a GET lane
    assert pre(ctx, "Bash", command="rm -rf src")["decision"] == "deny"  # grant covers egress only


def test_r4_allowed_classes(ctx):
    assert pre(ctx, "mcp__gmail__create_draft", to="a@b.c") == {}
    assert pre(ctx, "Write", file_path=str(ctx.workspace / "a.md"), content="x") == {}


# ---- R-6: control-layer writes prevented for tools, detected for all -------


@pytest.mark.parametrize(
    "tool, inp",
    [
        ("Write", {"file_path": "{data}/control/classes.json", "content": "{}"}),
        ("Edit", {"file_path": "{data}/business/profile.json"}),
        ("NotebookEdit", {"notebook_path": "{data}/control/x.ipynb"}),
        ("Bash", {"command": "echo '{}' > {data}/control/classes.json"}),
        ("Bash", {"command": "cp /tmp/x {data}/control/egress_allowlist.json"}),
        ("Bash", {"command": "sed -i '' s/deny/allow/ {data}/control/classes.json"}),
        ("Bash", {"command": "ln -sf /tmp/x {data}/control/classes.json"}),
        ("Bash", {"command": "cd {data} && echo x >> config_log.jsonl"}),
        ("Write", {"file_path": "{root}/hooks/hooks.json", "content": "{}"}),
    ],
)
def test_r6_tool_writes_to_control_prevented(ctx, tool, inp):
    inp = {k: v.replace("{data}", str(ctx.data_root)).replace("{root}", str(ctx.plugin_root)) for k, v in inp.items()}
    assert pre(ctx, tool, **inp)["decision"] == "deny"


def test_r6_out_of_band_write_detected(ctx, tmp_path):
    lc = tmp_path / "loop-contract"
    (lc / ".claude-plugin").mkdir(parents=True)
    (lc / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "loop-contract", "version": "1.1.0"}))
    ctx.loop_contract_root = lc
    (ctx.data_root / "control" / "classes.json").write_text(
        (ROOT / "control" / "defaults" / "classes.json").read_text())
    with open(ctx.data_root / "config_log.jsonl", "w") as fh:
        fh.write(json.dumps({"hashes": session.current_hashes(ctx)}) + "\n")
    setup._apply(ctx, setup.plan(ctx))
    assert session.on_start({}, ctx) == {}
    # a write that bypassed the tool layer (same OS user, outside the sandbox)
    (ctx.data_root / "control" / "classes.json").write_text("{}")
    out = session.on_start({}, ctx)
    assert out["state"] == "STOPPED_TAMPER" and "control" in out["mismatch"]
