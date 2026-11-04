"""Bypass corpus (spec §Evaluation stage 1): guard recall 100% on expected-deny rows,
0 false denies on expected-allow rows and no class mismatches. Each row runs through ``safety.on_pre``.

Row: {id, tool, input, expected: allow|deny, class, note, ctx?}. ``${WS}``, ``${DATA}``, ``${TMP}``,
``${ROOT}`` and ``${HOME}`` in inputs expand to the fixture's paths. ``ctx`` may set
agent_type, grant, written (paths), protected_terms, registry, temp_roots.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core import classes, safety
from harness_core.safety import Ctx

CORPUS = Path(__file__).parent / "corpus" / "bypass.jsonl"
REGISTRY = {
    "mcp__gmail__search": "read",
    "mcp__gmail__create_draft": "draft",
    "mcp__gmail__send": {"tag": "write", "class": "external_message"},
    "mcp__sheets__*": "write",
}


def rows():
    return [json.loads(l) for l in CORPUS.read_text("utf-8").splitlines() if l.strip()]


def _expand(obj, subs):
    if isinstance(obj, str):
        for k, v in subs.items():
            obj = obj.replace(k, v)
        return obj
    if isinstance(obj, dict):
        return {k: _expand(v, subs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v, subs) for v in obj]
    return obj


@pytest.fixture
def base(tmp_path):
    ws, data, root, home = (tmp_path / n for n in ("ws", "data", "plugin", "home"))
    for d in (ws / "src", ws / ".git", ws / ".claude" / "loops" / "j1" / "work", data / "keys", root / "bin", home):
        d.mkdir(parents=True)
    (ws / "src" / "app.py").write_text("print(1)\n")
    (ws / "run.sh").write_text("echo hi\n")
    (ws / "gen.py").write_text("print(2)\n")
    (data / "keys" / "hmac.key").write_text("k")
    for n in classes.HARNESS_CLIS:  # bare harness-<name> is trusted only when the plugin ships it
        (root / "bin" / f"harness-{n}").write_text("#!/bin/sh\n")
    (tmp_path / "tmp").mkdir()  # the temp root: on Linux tmp_path is under /tmp, which then holds the data root
    subs = {"${WS}": str(ws), "${DATA}": str(data), "${ROOT}": str(root), "${HOME}": str(home), "${TMP}": str(tmp_path / "tmp")}
    return tmp_path, subs


def make_ctx(base, row):
    tmp_path, subs = base
    extra = _expand(row.get("ctx", {}), subs)
    return Ctx(
        workspace=Path(subs["${WS}"]), data_root=Path(subs["${DATA}"]), plugin_root=Path(subs["${ROOT}"]),
        home=Path(subs["${HOME}"]), job="j1", temp_roots=extra.get("temp_roots", [tmp_path / "tmp"]),
        allowlist=["docs.python.org"], connector_registry=extra.get("registry", REGISTRY),
        agent_type=extra.get("agent_type"), grant=extra.get("grant"),
        written_this_task=set(extra.get("written", [])), protected_terms=extra.get("protected_terms", []),
    ), subs


def test_corpus_shape():
    rs = rows()
    assert len(rs) >= 200
    assert len({r["id"] for r in rs}) == len(rs)
    table = classes.load_table(None)["classes"]
    for r in rs:
        assert r["expected"] in ("allow", "deny") and r["class"] in table and r["note"], r["id"]


def test_corpus_recall_and_false_denies(base, capsys):
    misses, false_denies, cls_mismatch = [], [], []
    rs = rows()
    for r in rs:
        ctx, subs = make_ctx(base, r)
        inp = _expand(r["input"], subs)
        out = safety.on_pre({"tool_name": r["tool"], "tool_input": inp}, ctx)
        got = "deny" if out.get("decision") == "deny" else "allow"
        if r["expected"] == "deny" and got == "allow":
            misses.append(r["id"])
        if r["expected"] == "allow" and got == "deny":
            false_denies.append((r["id"], out.get("reason")))
        c = classes.classify(r["tool"], inp, ctx.connector_registry, ctx)
        if c.cls != r["class"]:
            cls_mismatch.append((r["id"], r["class"], c.cls))
    deny_rows = [r for r in rs if r["expected"] == "deny"]
    recall = 1 - len(misses) / len(deny_rows)
    with capsys.disabled():
        print(f"\nbypass corpus: {len(rs)} rows, {len(deny_rows)} expected-deny, recall {recall:.1%}, "
              f"false denies {len(false_denies)}, class mismatches {len(cls_mismatch)}")
        if misses:
            print("  missed:", misses)
        if cls_mismatch:
            print("  class mismatches:", cls_mismatch)
    assert recall == 1.0, misses
    assert not false_denies, false_denies
    assert not cls_mismatch, cls_mismatch
