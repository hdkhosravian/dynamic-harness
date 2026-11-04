#!/usr/bin/env python3
"""OQ-4 probe hook. argv[1] = label. Logs stdin JSON + env to spike/logs/<label>.jsonl,
then behaves per markers in the Bash command (PreToolUse) or per spike/control.json."""
import json, os, sys, time

SPIKE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS = os.path.join(SPIKE, "logs", os.environ.get("PROBE_RUN", "default"))  # per-run dir
label = sys.argv[1] if len(sys.argv) > 1 else "unknown"


def control():
    try:
        with open(os.path.join(LOGS, "control.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def log(extra=None):
    os.makedirs(LOGS, exist_ok=True)
    rec = {"label": label, "ts": time.time(), "argv": sys.argv,
           "env": {k: os.environ.get(k) for k in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR")},
           "input": data}
    if extra:
        rec.update(extra)
    with open(os.path.join(LOGS, label + ".jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")


raw = sys.stdin.read()
try:
    data = json.loads(raw)
except Exception:
    data = {"_unparsed": raw}
log()
ctl = control()
cmd = str((data.get("tool_input") or {}).get("command", ""))

if label == "pre":
    if "PROBE_EXIT1" in cmd:
        sys.stderr.write("probe: simulated crash (exit 1)\n"); sys.exit(1)
    if "PROBE_EXIT2" in cmd:
        sys.stderr.write("probe: blocked by exit 2\n"); sys.exit(2)
    if "PROBE_DENYJSON" in cmd:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
              "permissionDecision": "deny", "permissionDecisionReason": "probe: JSON deny"}}))
        sys.exit(0)
    if "PROBE_SLEEP" in cmd:  # hook timeout is 3 s
        time.sleep(8); log({"phase": "woke_after_sleep"})
elif label == "pre_onfail":  # same hook config but with onFailure: block
    if "PROBE_OF_EXIT1" in cmd:
        sys.stderr.write("probe: simulated crash (exit 1, onFailure=block)\n"); sys.exit(1)
    if "PROBE_OF_SLEEP" in cmd:
        time.sleep(8); log({"phase": "woke_after_sleep"})
elif label == "post_long":
    secs = int(ctl.get("long_sleep", 0))
    if "PROBE_LONG" in cmd and secs:
        t0 = time.time(); time.sleep(secs)
        log({"phase": "completed_long_sleep", "slept": time.time() - t0})
elif label == "stop":
    n = int(ctl.get("stop_blocks", 0))
    cpath = os.path.join(LOGS, "stop_counter")
    count = int(open(cpath).read()) if os.path.exists(cpath) else 0
    if count < n:
        open(cpath, "w").write(str(count + 1))
        print(json.dumps({"decision": "block", "reason": "probe block %d: reply with only the word again" % (count + 1)}))
elif label == "config_change":
    if ctl.get("config_change_block"):
        sys.stderr.write("probe: config change blocked\n"); sys.exit(2)
elif label == "session_start":
    target = ctl.get("session_start_write")
    if target:  # sandbox probe: can a hook write where Bash is denied?
        try:
            open(target, "w").write("hook wrote this\n"); log({"phase": "hook_write_ok", "target": target})
        except Exception as e:
            log({"phase": "hook_write_failed", "error": str(e)})
sys.exit(0)
