from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from harness_core import safety
from harness_core.safety import RESEARCHER, Ctx


class Log:
    def __init__(self):
        self.rows = []

    def append(self, type, payload):
        self.rows.append((type, payload))


@pytest.fixture
def ctx(tmp_path):
    ws, data, root, home = (tmp_path / n for n in ("ws", "data", "plugin", "home"))
    for d in (ws, data, root / "bin", home):
        d.mkdir(parents=True)
    (root / "bin" / "harness-claims").write_text("#!/usr/bin/python3 -I\n")  # harness CLIs are trusted only when shipped
    return Ctx(
        workspace=ws, data_root=data, plugin_root=root, home=home, job="j1",
        temp_roots=[tmp_path / "tmp"], allowlist=[],
        connector_registry={"mcp__gmail__create_draft": "draft", "mcp__gmail__read": "read"},
    )


def pre(ctx, tool, **inp):
    return safety.on_pre({"tool_name": tool, "tool_input": inp}, ctx)


def researcher(ctx, **grant):
    ctx.agent_type = RESEARCHER
    ctx.grant = {"domains": ["news.example.ir"], "active": True, "expires_at": time.time() + 3600, **grant}
    return ctx


def test_allow_returns_empty_dict(ctx):
    assert pre(ctx, "Read", file_path=str(ctx.workspace / "a.txt")) == {}
    assert pre(ctx, "Bash", command="ls -la") == {}
    assert pre(ctx, "Write", file_path=str(ctx.workspace / "a.txt"), content="x") == {}
    assert pre(ctx, "mcp__gmail__create_draft", to="a@b.c") == {}


def test_deny_shape(ctx):
    out = pre(ctx, "Bash", command="git push")
    assert out["decision"] == "deny" and "destructive" in out["reason"]


@pytest.mark.parametrize(
    "tool, inp",
    [
        ("Bash", {"command": "curl https://evil.com"}),
        ("Bash", {"command": "osascript -e 'x'"}),
        ("Bash", {"command": "rm -rf /"}),
        ("Bash", {"command": "python3 -c 'print(1)'"}),
        ("mcp__stripe__refund", {"id": "1"}),
        ("WebFetch", {"url": "https://evil.com/"}),
        ("WebSearch", {"query": "market size"}),
        ("Edit", {"file_path": "/etc/hosts"}),
        ("Frobnicate", {}),
    ],
)
def test_irreversible_denied(ctx, tool, inp):
    assert safety.on_pre({"tool_name": tool, "tool_input": inp}, ctx)["decision"] == "deny"


def test_malformed_event_denied(ctx):
    assert safety.on_pre({}, ctx)["decision"] == "deny"
    assert safety.on_pre(None, ctx)["decision"] == "deny"
    assert safety.on_pre({"tool_name": "Bash", "tool_input": "rm -rf /"}, ctx)["decision"] == "deny"


def test_researcher_get_under_grant_allowed(ctx):
    researcher(ctx)
    assert pre(ctx, "WebFetch", url="https://news.example.ir/market/report", prompt="x") == {}
    assert pre(ctx, "WebSearch", query="retail market trends tehran") == {}
    assert pre(ctx, "Bash", command="harness-claims fetch https://news.example.ir/a") == {}


def test_grant_needs_researcher(ctx):
    ctx.grant = {"domains": ["news.example.ir"]}
    assert pre(ctx, "WebFetch", url="https://news.example.ir/a")["decision"] == "deny"


def test_researcher_needs_grant(ctx):
    ctx.agent_type = RESEARCHER
    assert pre(ctx, "WebFetch", url="https://news.example.ir/a")["decision"] == "deny"


def test_researcher_off_grant_domain_denied(ctx):
    researcher(ctx)
    assert pre(ctx, "WebFetch", url="https://evil.com/a")["decision"] == "deny"
    assert pre(ctx, "WebFetch", url="https://x.news.example.ir/a")["decision"] == "deny"


def test_researcher_query_guard_applies(ctx):
    researcher(ctx)
    ctx.protected_terms = ["Zarin Co"]
    assert pre(ctx, "WebFetch", url="https://news.example.ir/p/123456")["decision"] == "deny"
    assert pre(ctx, "WebSearch", query="zarin co prices")["decision"] == "deny"
    assert pre(ctx, "WebFetch", url="https://news.example.ir/?q=" + "A1b2C3d4E5f6G7h8I9j0K" * 2)["decision"] == "deny"


def test_researcher_is_not_exempt_from_other_classes(ctx):
    researcher(ctx)
    assert pre(ctx, "Bash", command="curl https://news.example.ir/a")["decision"] == "deny"
    assert pre(ctx, "Bash", command="git push")["decision"] == "deny"


def test_inactive_grant(ctx):
    researcher(ctx, active=False)
    assert pre(ctx, "WebFetch", url="https://news.example.ir/a")["decision"] == "deny"


def test_deny_is_logged(ctx):
    ctx.log = Log()
    pre(ctx, "Bash", command="git push")
    pre(ctx, "Bash", command="ls")
    assert [t for t, _ in ctx.log.rows] == ["SAFETY_DENY"]
    assert ctx.log.rows[0][1]["cls"] == "destructive"


def test_failing_log_still_denies(ctx):
    class Bad:
        def append(self, *a):
            raise OSError("disk full")

    ctx.log = Bad()
    assert pre(ctx, "Bash", command="git push")["decision"] == "deny"


def test_failing_log_is_reported_on_stderr(ctx, capsys):
    class Bad:
        def append(self, *a):
            raise OSError("disk full")

    ctx.log = Bad()
    pre(ctx, "Bash", command="git push")
    assert "disk full" in capsys.readouterr().err


# ---- v1_action decides (finding 8) ----------------------------------------

DEFAULT_TABLE = Path(__file__).resolve().parents[1] / "control" / "defaults" / "classes.json"


def live_table(ctx, edit):
    t = json.loads(DEFAULT_TABLE.read_text())
    edit(t["classes"])
    (ctx.data_root / "control").mkdir(exist_ok=True)
    (ctx.data_root / "control" / "classes.json").write_text(json.dumps(t))


def test_v1_action_deny_on_reversible_class_denies(ctx):
    live_table(ctx, lambda c: c["read"].update(v1_action="deny"))
    assert pre(ctx, "Read", file_path=str(ctx.workspace / "a.txt"))["decision"] == "deny"


def test_missing_v1_action_denies(ctx):
    live_table(ctx, lambda c: c["local_reversible"].pop("v1_action"))
    assert pre(ctx, "Bash", command="touch a")["decision"] == "deny"


def test_v1_action_allow_does_not_override_irreversible(ctx):
    live_table(ctx, lambda c: c["destructive"].update(v1_action="allow"))
    assert pre(ctx, "Bash", command="git push")["decision"] == "deny"


# ---- grants need active: true and an unexpired expires_at ------------------

@pytest.mark.parametrize("grant", [
    {"active": None},
    {"active": "yes"},
    {"expires_at": None},
    {"expires_at": time.time() - 1},
    {"expires_at": "garbage"},
    {"expires_at": "2000-01-01T00:00:00Z"},
    {"expires_at": "2999-01-01T00:00:00"},  # naive timestamp: zone unknown, not honoured
])
def test_grant_without_active_or_expiry_is_ignored(ctx, grant):
    researcher(ctx)
    ctx.grant = {k: v for k, v in {**ctx.grant, **grant}.items() if v is not None}
    assert pre(ctx, "WebFetch", url="https://news.example.ir/a")["decision"] == "deny"


def test_grant_iso_expiry_in_future_is_honoured(ctx):
    researcher(ctx, expires_at="2999-01-01T00:00:00Z")
    assert pre(ctx, "WebFetch", url="https://news.example.ir/a") == {}


# ---- session status gate (finding 2) ------------------------------------------

def test_failed_session_allows_only_reads(ctx):
    ctx.session_ok, ctx.session_reason = False, "Claude Code too old"
    assert pre(ctx, "Read", file_path=str(ctx.workspace / "a.txt")) == {}
    assert pre(ctx, "Bash", command="ls") == {}
    for tool, inp in (("Bash", {"command": "touch a"}), ("Write", {"file_path": str(ctx.workspace / "a"), "content": ""}),
                      ("mcp__gmail__create_draft", {"to": "a@b.c"})):
        out = safety.on_pre({"tool_name": tool, "tool_input": inp}, ctx)
        assert out["decision"] == "deny" and "Claude Code too old" in out["reason"]


def test_session_deny_is_logged(ctx):
    ctx.log, ctx.session_ok = Log(), False
    pre(ctx, "Bash", command="touch a")
    assert ctx.log.rows[0][0] == "SAFETY_DENY" and ctx.log.rows[0][1]["cls"] == "session"
