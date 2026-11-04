#!/usr/bin/env python3
"""Minimal stdio MCP server for the sandbox probe: one tool that writes a file and reads a file,
so we can see whether sandbox denyWrite/denyRead apply to MCP server processes."""
import json, sys

TOOL = {"name": "probe_fs", "description": "Write text to write_path and read read_path; returns what happened.",
        "inputSchema": {"type": "object", "properties": {"write_path": {"type": "string"}, "read_path": {"type": "string"}},
                        "required": ["write_path", "read_path"]}}


def reply(id_, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}) + "\n"); sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        continue
    m, id_ = msg.get("method"), msg.get("id")
    if m == "initialize":
        reply(id_, {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}}, "serverInfo": {"name": "probe", "version": "0.1"}})
    elif m == "tools/list":
        reply(id_, {"tools": [TOOL]})
    elif m == "tools/call":
        a = msg["params"].get("arguments", {})
        out = []
        try:
            open(a["write_path"], "w").write("mcp wrote this\n"); out.append("WRITE_OK")
        except Exception as e:
            out.append("WRITE_FAIL " + str(e))
        try:
            out.append("READ_OK " + open(a["read_path"]).read().strip())
        except Exception as e:
            out.append("READ_FAIL " + str(e))
        reply(id_, {"content": [{"type": "text", "text": "; ".join(out)}]})
    elif id_ is not None:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": id_, "error": {"code": -32601, "message": "no method"}}) + "\n"); sys.stdout.flush()
