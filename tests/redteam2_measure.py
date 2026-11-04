#!/usr/bin/env python3
"""Standalone recall harness for tests/corpus/redteam2.jsonl (held-out round 2).

Not a test_ file, so pytest will not auto-collect it. Run: python3 tests/redteam2_measure.py
It imports ONLY harness_core.safety.on_pre (+ Ctx) and exercises the classifier
against the red-team corpus. Placeholders in the corpus map to throwaway tmp dirs:
  /WS -> workspace, /DATA -> plugin data_root, /ROOT -> plugin_root, /HOME -> home.
"""
from __future__ import annotations
import json, os, sys, tempfile, subprocess, dataclasses, shutil, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "lib"))
from harness_core.safety import on_pre, Ctx  # import ONLY the entry point + its Ctx

# agent-callable CLIs from the spec's bin list (hand-copied: this harness must not import classifier internals)
HARNESS_CLIS = ("probe", "card", "compile", "ledger", "gate", "contact", "business", "normalize", "answer",
                "claims", "model", "doc", "decide", "critic", "sandbox-exec", "estimate", "run", "research",
                "admin", "lint-user-text")

def build_tree():
    base = Path(tempfile.mkdtemp(prefix="redteam_"))
    ws = base / "ws"; data = base / "data"; root = base / "root"; home = base / "home"
    for d in (ws, data, root, home):
        d.mkdir(parents=True, exist_ok=True)
    # workspace layout
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "tools").mkdir(parents=True, exist_ok=True)
    (ws / "scripts").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / "loops" / "J1" / "work" / "sub").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / "loops" / "J1" / "requests").mkdir(parents=True, exist_ok=True)
    for p, c in [("src/main.py", "def main():\n    pass\n"), ("README.md", "x\n"),
                 ("report.md", "r\n"), ("tools/existing.py", "print(1)\n"),
                 ("scripts/build.sh", "echo build\n"), ("a.txt", "a\n"), ("b.txt", "b\n"),
                 (".claude/loops/J1/work/draft.md", "a\n"), ("analysis.ipynb", "{}\n"),
                 ("scripts/check.py", "print(1)\n"), ("CLAUDE.md", "c\n"), ("secret.txt", "s\n")]:
        fp = ws / p; fp.write_text(c)
    (ws / "lnk").symlink_to(home / ".claude")  # round 2: pre-existing symlink out of the workspace
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "init", "-q"], cwd=ws, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "add", "-A"], cwd=ws, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    # protected trees
    (data / "keys").mkdir(parents=True, exist_ok=True)
    (data / "experience").mkdir(parents=True, exist_ok=True)
    (data / "runs" / "J1").mkdir(parents=True, exist_ok=True)
    (data / "keys" / "hmac.key").write_text("k\n")
    (data / "experience" / "lessons.jsonl").write_text("{}\n")
    (data / "runs" / "J1" / "events.jsonl").write_text("{}\n")
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(parents=True, exist_ok=True)  # bare harness-<name> is trusted only if the plugin ships it
    for n in HARNESS_CLIS:
        (root / "bin" / ("harness-" + n)).write_text("#!/bin/sh\n")
    return base, ws, data, root, home

def expand(obj, m):
    if isinstance(obj, str):
        for k, v in m.items():
            obj = obj.replace(k, v)
        return obj
    if isinstance(obj, dict):
        return {k: expand(val, m) for k, val in obj.items()}
    if isinstance(obj, list):
        return [expand(x, m) for x in obj]
    return obj

CTX_FIELDS = {f.name for f in dataclasses.fields(Ctx)}

def measure():
    """Run the corpus; returns counts and the missed / falsely denied rows."""
    corpus = HERE / "corpus" / "redteam2.jsonl"
    rows = [json.loads(l) for l in corpus.read_text().splitlines() if l.strip()]
    base, ws, data, root, home = build_tree()
    m = {"/WS": str(ws), "/DATA": str(data), "/ROOT": str(root), "/HOME": str(home)}

    deny_total = deny_hit = 0
    false_denies = []
    misses = []
    errors = []
    for r in rows:
        tool = r["tool"]
        inp = expand(r["input"], m)
        rc = dict(r.get("ctx", {}))
        # base ctx
        kw = dict(
            workspace=ws, data_root=data, plugin_root=root, home=home,
            loop_contract_root=root / "loop-contract", job="J1",
            connector_registry={}, written_this_task=set(),
            protected_terms=["AcmeCorp", "payroll", "Sepehr"],
            owner_tokens=["acme", "sepehr"], allowlist=[],
        )
        # per-row overrides (only known Ctx fields)
        for k, v in rc.items():
            if k not in CTX_FIELDS:
                continue
            if k == "written_this_task":
                v = set(expand(v, m))
            elif k == "grant" and isinstance(v, dict):
                # rows predate the grant schema: a listed grant is an active, unexpired one
                v = {"active": True, "expires_at": time.time() + 3600, **expand(v, m)}
            else:
                v = expand(v, m)
            kw[k] = v
        ctx = Ctx(**kw)
        evt = {"tool_name": tool, "tool_input": inp, "cwd": str(ws)}  # real hook input carries cwd
        try:
            res = on_pre(evt, ctx)
        except BaseException as e:  # on_pre is documented never to raise; record if it does
            errors.append((r["id"], repr(e)))
            res = {"decision": "deny", "reason": "raised:%r" % e}
        decision = "deny" if isinstance(res, dict) and res.get("decision") == "deny" else "allow"
        if r["expected"] == "deny":
            deny_total += 1
            if decision == "deny":
                deny_hit += 1
            else:
                misses.append(r)
        else:
            if decision == "deny":
                false_denies.append(r)

    shutil.rmtree(base, ignore_errors=True)
    return {"rows": rows, "deny_total": deny_total, "deny_hit": deny_hit,
            "false_denies": false_denies, "misses": misses, "errors": errors}


def main():
    res = measure()
    rows, deny_total, deny_hit = res["rows"], res["deny_total"], res["deny_hit"]
    false_denies, misses, errors = res["false_denies"], res["misses"], res["errors"]
    print("=" * 60)
    print("rows:            %d  (deny %d / allow %d)" % (
        len(rows), deny_total, len(rows) - deny_total))
    print("deny recall:     %d/%d = %.1f%%" % (
        deny_hit, deny_total, 100.0 * deny_hit / deny_total if deny_total else 0.0))
    print("false denies:    %d/%d" % (len(false_denies), len(rows) - deny_total))
    if errors:
        print("on_pre raised:   %d -> %s" % (len(errors), errors))
    print("=" * 60)
    if misses:
        print("\nMISSED DENIES (classifier allowed an attack):")
        for r in misses:
            print("  [%s] %-9s %-18s %s" % (r["id"], r["tool"][:9], r["class"], r["note"]))
    if false_denies:
        print("\nFALSE DENIES (classifier blocked a legit call):")
        for r in false_denies:
            print("  [%s] %-9s %-18s %s" % (r["id"], r["tool"][:9], r["class"], r["note"]))
    print()

if __name__ == "__main__":
    main()
