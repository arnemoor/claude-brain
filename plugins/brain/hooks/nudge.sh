#!/usr/bin/env bash
# Thin wrapper for the contribution nudges: `nudge.sh start|end|stop`. Claude Code
# runs it for SessionStart, SessionEnd and Stop, and it hands the hook JSON on
# stdin to scripts/brain_nudge.py.
#
# It never blocks a session, never fails one, and always prints valid output.
# When python3 is missing, crashes or prints nothing, it prints the empty payload
# for the event: the SessionStart JSON with empty context for `start`, `{}` for
# `stop`, and nothing for `end`, whose output Claude Code discards. An unknown
# argument prints nothing. The exit code is always 0.
#
# brain_nudge.py imports stdlib only, so this calls python3 and never `uv run`.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "${1:-}" in
  start) fallback='{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":""}}' ;;
  stop)  fallback='{}' ;;
  end)   fallback='' ;;
  *)     exit 0 ;;
esac

out="$(python3 "$ROOT/scripts/brain_nudge.py" "$1" 2>/dev/null)" || out=""
if [ -n "$out" ]; then
  printf '%s\n' "$out"
elif [ -n "$fallback" ]; then
  printf '%s\n' "$fallback"
fi
exit 0
