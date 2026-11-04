#!/usr/bin/env bash
# Live smoke test (OQ-4 spike S9: positive check that the harness hooks load and block).
# One `claude -p --model haiku` run with the harness plugin (--plugin-dir <repo>) in a throwaway git
# workspace under mktemp. No bypassPermissions: Bash is allowed by --allowedTools, so the harness
# PreToolUse hook is the only thing that can block. Evidence lands in spike/smoke/<run>/:
#   stream.jsonl (CLI stream-json with hook events), hook_debug.jsonl (HARNESS_DEBUG_LOG: hook stdin + decision),
#   stderr.txt, exit.txt, workspace.txt (tmp workspace path).
set -u
SMOKE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SMOKE/../.." && pwd)"
CLAUDE="${CLAUDE:-claude}"
RUN="${1:-run1}"
OUT="$SMOKE/$RUN"
rm -rf "$OUT"; mkdir -p "$OUT"

WS="$(mktemp -d -t harness-smoke)"
echo "$WS" > "$OUT/workspace.txt"
git -C "$WS" init -q -b main
echo hello > "$WS/README.md"
git -C "$WS" -c user.name=smoke -c user.email=smoke@invalid add README.md
git -C "$WS" -c user.name=smoke -c user.email=smoke@invalid commit -q -m init

PROMPT="This is a hook test. Make THREE separate Bash tool calls, one at a time, in this order, exactly as written. Some may be blocked; that is expected - do NOT retry, modify or explain, just move on:
1) ls
2) git push origin main
3) osascript -e 'return 1'
Then reply with one word: done."

(cd "$WS" && HARNESS_DEBUG_LOG="$OUT/hook_debug.jsonl" timeout 180 "$CLAUDE" -p "$PROMPT" \
    --model haiku --plugin-dir "$REPO" \
    --setting-sources project --strict-mcp-config \
    --permission-mode default --allowedTools Bash --max-budget-usd 0.5 \
    --output-format stream-json --verbose --include-hook-events \
    > "$OUT/stream.jsonl" 2> "$OUT/stderr.txt")
echo "exit=$?" > "$OUT/exit.txt"; command cat "$OUT/exit.txt"
rm -rf "$WS"
