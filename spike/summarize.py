#!/usr/bin/env python3
"""Print a compact per-run digest of hook logs + stream results (evidence for RESULTS.md)."""
import glob, json, os

SPIKE = os.path.dirname(os.path.abspath(__file__))
for d in sorted(glob.glob(os.path.join(SPIKE, "logs", "*/"))):
    run = os.path.basename(d.rstrip("/"))
    print("=== %s  %s" % (run, open(d + "exit.txt").read().strip() if os.path.exists(d + "exit.txt") else ""))
    for f in sorted(glob.glob(d + "*.jsonl")):
        if f.endswith("stream.jsonl"):
            continue
        recs = [json.loads(l) for l in open(f) if l.strip()]
        line = "  %-20s n=%d" % (os.path.basename(f)[:-6], len(recs))
        tools = [r["input"].get("tool_name") for r in recs if r["input"].get("tool_name")]
        if tools:
            line += " tools=%s" % tools
        agents = sorted({str(r["input"].get("agent_type")) for r in recs if "agent_type" in r["input"]})
        if agents:
            line += " agent_type=%s" % agents
        phases = [r["phase"] for r in recs if "phase" in r]
        if phases:
            line += " phases=%s" % phases
        print(line)
    s = d + "stream.jsonl"
    if os.path.exists(s):
        for l in open(s):
            try:
                m = json.loads(l)
            except Exception:
                continue
            if m.get("type") == "result":
                print("  result: subtype=%s turns=%s cost=%s stop=%s text=%r" % (
                    m.get("subtype"), m.get("num_turns"), m.get("total_cost_usd"),
                    m.get("stop_reason"), str(m.get("result"))[:160]))
    for extra in ("files_created.txt", "denied_dir_contents.txt"):
        if os.path.exists(d + extra):
            print("  %s: %s" % (extra, open(d + extra).read().split()))
