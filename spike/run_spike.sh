#!/usr/bin/env bash
# OQ-4 platform-behaviour spike. Usage: ./run_spike.sh [run ...]   (no args = all runs except r5_long)
# Each run: one cheap `claude -p --model haiku` session with the probe plugin loaded via --plugin-dir,
# user settings/plugins/MCP excluded (--setting-sources project --strict-mcp-config), cwd = spike/work.
# Hook inputs land in logs/<run>/<hook-label>.jsonl; the CLI stream in logs/<run>/stream.jsonl.
set -u
SPIKE="$(cd "$(dirname "$0")" && pwd)"
CLAUDE="${CLAUDE:-/opt/homebrew/bin/claude}"
WORK="$SPIKE/work"

run() {  # run <name> <timeout_s> <prompt> [extra claude args...]
  local name="$1" t="$2" prompt="$3"; shift 3
  local dir="$SPIKE/logs/$name"
  mkdir -p "$dir"
  [ -f "$dir/control.json" ] || echo '{}' > "$dir/control.json"
  echo "== $name"
  (cd "$WORK" && PROBE_RUN="$name" timeout "$t" "$CLAUDE" -p "$prompt" \
      --model haiku --plugin-dir "$SPIKE/probe-plugin" \
      --setting-sources project --strict-mcp-config \
      --permission-mode bypassPermissions \
      --output-format stream-json --verbose --include-hook-events "$@" \
      > "$dir/stream.jsonl" 2> "$dir/stderr.txt")
  echo "exit=$?" > "$dir/exit.txt"; cat "$dir/exit.txt"
}

fresh() { rm -rf "$SPIKE/logs/$1"; mkdir -p "$SPIKE/logs/$1"; }

r1_exitcodes() {  # items 1, 2, 4, 6, 7
  fresh r1_exitcodes; rm -f "$WORK"/r1_*.txt
  run r1_exitcodes 240 "This is a hook test harness. Make SEVEN separate Bash tool calls, one at a time, in this order, exactly as written. Some will be blocked or fail; that is expected - do NOT retry or modify any command, just move on to the next one:
1) echo ok > r1_plain.txt
2) echo PROBE_EXIT1 > r1_exit1.txt
3) echo PROBE_EXIT2 > r1_exit2.txt
4) echo PROBE_DENYJSON > r1_deny.txt
5) echo PROBE_SLEEP > r1_sleep.txt
6) echo PROBE_OF_EXIT1 > r1_ofexit1.txt
7) echo PROBE_OF_SLEEP > r1_ofsleep.txt
Then reply with one word: done."
  ls "$WORK"/r1_*.txt > "$SPIKE/logs/r1_exitcodes/files_created.txt" 2>&1
}

r2_subagent() {  # item 5
  fresh r2_subagent; rm -f "$WORK"/r2_*.txt
  run r2_subagent 240 "Use the Agent tool exactly once with subagent_type \"probe-plugin:probe-agent\" and this prompt: \"Run this Bash command: echo SUBAGENT_CALL > r2_sub.txt\". Do not run any tool yourself. When it returns, reply with one word: done."
  ls "$WORK"/r2_*.txt > "$SPIKE/logs/r2_subagent/files_created.txt" 2>&1
}

r3_stopcap() {  # item 3: hook wants 10 blocks; docs claim the cap is 8
  fresh r3_stopcap; echo '{"stop_blocks": 10}' > "$SPIKE/logs/r3_stopcap/control.json"
  run r3_stopcap 300 "Reply with only the word hi. Use no tools."
}

r4_stopcap_env() {  # item 3: does CLAUDE_CODE_STOP_HOOK_BLOCK_CAP=3 lower the cap?
  fresh r4_stopcap_env; echo '{"stop_blocks": 10}' > "$SPIKE/logs/r4_stopcap_env/control.json"
  CLAUDE_CODE_STOP_HOOK_BLOCK_CAP=3 run r4_stopcap_env 300 "Reply with only the word hi. Use no tools."
}

r5_long() {  # item 2: PostToolUse hook with timeout 1800 sleeping past the 600 s default
  fresh r5_long; echo '{"long_sleep": 630}' > "$SPIKE/logs/r5_long/control.json"
  run r5_long 1000 "Run this single Bash command exactly: echo PROBE_LONG. Then reply with one word: done."
}

r6_askuser() {  # item 8
  fresh r6_askuser
  run r6_askuser 180 "Use the AskUserQuestion tool to ask me one question: 'Pick a colour' with options red and blue. Then tell me in one line exactly what the tool returned. If the tool is not available to you, say NO_ASKUSER and list the names of every tool you have."
}

r7_sandbox() {  # item 9: sandbox keys; Bash vs MCP stdio server vs hook process
  fresh r7_sandbox; d="$SPIKE/logs/r7_sandbox"
  mkdir -p "$WORK/denied"; rm -f "$WORK"/denied/*; echo "TOPSECRET" > "$WORK/secret.txt"
  echo "{\"session_start_write\": \"$WORK/denied/hook.txt\"}" > "$d/control.json"
  cat > "$d/settings.json" <<EOF
{"sandbox": {"enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false,
  "network": {"allowedDomains": ["example.com"]},
  "filesystem": {"denyWrite": ["$WORK/denied"], "denyRead": ["$WORK/secret.txt"]}}}
EOF
  cat > "$d/mcp.json" <<EOF
{"mcpServers": {"probe": {"type": "stdio", "command": "python3", "args": ["$SPIKE/mcp/probe_mcp.py"]}}}
EOF
  run r7_sandbox 240 "Sandbox test. Do each step once, in order, and do not retry anything that fails:
1) Bash: cat secret.txt
2) Bash: echo bash > denied/bash.txt
3) Bash: curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://example.com
4) Bash: curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://example.org
5) Bash with dangerouslyDisableSandbox set to true: echo escape > denied/escape.txt
6) Call the MCP tool probe_fs with write_path \"$WORK/denied/mcp.txt\" and read_path \"$WORK/secret.txt\".
Then report the raw output of every step, one line each." \
    --settings "$d/settings.json" --mcp-config "$d/mcp.json"
  ls "$WORK"/denied/ > "$d/denied_dir_contents.txt" 2>&1
}

r8_configchange() {  # item 10: ConfigChange fires on a project settings edit mid-session; exit 2 blocks
  fresh r8_configchange; d="$SPIKE/logs/r8_configchange"
  echo '{"config_change_block": true}' > "$d/control.json"
  echo '{}' > "$WORK/.claude/settings.json"
  ( sleep 12; echo '{"env": {"PROBE_CHANGED": "1"}}' > "$WORK/.claude/settings.json" ) &
  # plain `sleep N` is refused by Claude Code's own sleep guard, so wait via python
  run r8_configchange 180 "Run this single Bash command exactly: python3 -c 'import time; time.sleep(25); print(\"slept\")'. Then reply with one word: done."
  wait; echo '{}' > "$WORK/.claude/settings.json"
}

r10_netstrict() {  # item 9: allowedDomains is not enforced in bypassPermissions unless strictAllowlist is set
  fresh r10_netstrict; d="$SPIKE/logs/r10_netstrict"
  echo '{"sandbox": {"enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false, "network": {"allowedDomains": ["example.com"], "strictAllowlist": true}}}' > "$d/settings.json"
  run r10_netstrict 180 "Run these two Bash commands separately, once each, no retries, then report both raw outputs:
1) curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://example.com
2) curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://example.org" --settings "$d/settings.json"
}

r9_managedonly() {  # item 10: allowManagedHooksOnly from a non-managed (--settings) source
  fresh r9_managedonly; d="$SPIKE/logs/r9_managedonly"
  echo '{"allowManagedHooksOnly": true}' > "$d/settings.json"
  run r9_managedonly 120 "Reply with only the word hi. Use no tools." --settings "$d/settings.json"
}

mkdir -p "$WORK/.claude"
RUNS="${*:-r1_exitcodes r2_subagent r3_stopcap r4_stopcap_env r6_askuser r7_sandbox r8_configchange r9_managedonly r10_netstrict}"
for r in $RUNS; do "$r"; done
python3 "$SPIKE/summarize.py"
