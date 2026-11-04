#!/usr/bin/env bash
# P5 setup spike: UserPromptSubmit block for a typed plugin command, nesting inside Claude Code's
# sandbox, and harness-sandbox-exec's outer mode. About $0.01 with haiku. Writes only under a temp dir
# and one scratch plugin data dir (~/.claude/plugins/data/dynamic-harness-p5spike), which it removes.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE="${CLAUDE:-claude}"
T="$(mktemp -d)"; K="$HOME/.claude/plugins/data/dynamic-harness-p5spike"
trap 'rm -rf "$T" "$K"' EXIT
mkdir -p "$T/ws/.git" "$K/keys"; head -c 32 /dev/urandom > "$K/keys/hmac.key"; chmod 700 "$K/keys"; chmod 600 "$K/keys/hmac.key"
cat > "$T/settings.json" <<JSON
{"sandbox": {"enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false,
  "network": {"allowedDomains": [], "strictAllowlist": true},
  "filesystem": {"denyRead": ["$HOME/.claude/plugins/data/*/keys"], "denyWrite": ["$HOME/.claude"]}}}
JSON
cd "$T/ws"
echo "== setup plan (expect: blocked, 0 turns, a plan with a confirm code)"
"$CLAUDE" -p "/dynamic-harness:setup" --model haiku --plugin-dir "$REPO" --setting-sources project \
  --strict-mcp-config --output-format json | python3 -c 'import json,sys; r=json.load(sys.stdin); print("turns", r.get("num_turns"), "cost", r.get("total_cost_usd")); print(r.get("result","")[:400])'
echo "== outer mode (expect: 42 exit=0, then a refusal exit=2)"
"$CLAUDE" -p "Run these two Bash commands one at a time exactly as written, then reply with both raw outputs only.
1) /usr/bin/python3 -I $REPO/bin/harness-sandbox-exec -- /usr/bin/python3 -I -c 'print(6*7)'; echo exit=\$?
2) /usr/bin/python3 -I $REPO/bin/harness-sandbox-exec --cwd .git -- /usr/bin/true; echo exit=\$?" \
  --model haiku --setting-sources project --strict-mcp-config --settings "$T/settings.json" --output-format json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("result",""))'
