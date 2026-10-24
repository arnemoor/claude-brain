#!/usr/bin/env bash
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
hook="$root/hooks/session-start.sh"

# The hook injects MUSTKNOW.md from every configured root.
tmp=$(mktemp -d)
mkdir -p "$tmp/personal" "$tmp/shared"
printf -- '- alpha personal rule\n' > "$tmp/personal/MUSTKNOW.md"
printf -- '- beta shared rule\n'    > "$tmp/shared/MUSTKNOW.md"
cat > "$tmp/config.json" <<JSON
{"personalVault":{"path":"$tmp/personal"},"sharedTree":{"path":"$tmp/shared"},"journalsDir":"$tmp/j"}
JSON
out=$(BRAIN_CONFIG="$tmp/config.json" bash "$hook" mustKnow)
echo "$out" | python3 -c 'import sys,json; json.load(sys.stdin)' >/dev/null || { echo "FAIL: not valid JSON"; exit 1; }
echo "$out" | grep -q "alpha personal rule" || { echo "FAIL: personal must-know missing"; exit 1; }
echo "$out" | grep -q "beta shared rule"    || { echo "FAIL: shared must-know missing"; exit 1; }

# The pointer line is the only always-on surface telling a session the vault is
# searchable. Without it, search on demand never gets triggered.
echo "$out" | grep -q "brain_lib.py find" || { echo "FAIL: find pointer line missing"; exit 1; }

# The pointer must be runnable as written. brain_lib.py is not on PATH and its
# directory is version-pinned, so the hook emits its own resolved absolute path.
# It must also name uv, or a session uses system python3 and dies on pyyaml.
ptr=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"].split("\n")[0])')
case "$ptr" in
  *"uv run /"*"/scripts/brain_lib.py find"*) ;;
  *) echo "FAIL: pointer is not a runnable command: $ptr"; exit 1 ;;
esac
# The dependency belongs in the script via PEP 723, not in the invocation. A
# caller who has to remember a flag is a caller who forgets it and falls back
# to system python3, which has no pyyaml.
case "$ptr" in
  *"--with pyyaml"*) echo "FAIL: pointer still carries --with pyyaml"; exit 1 ;;
esac
ptr_path=$(printf '%s' "$ptr" | sed -n 's|.*uv run \(/[^ ]*brain_lib\.py\) find.*|\1|p')
[ -f "$ptr_path" ] || { echo "FAIL: pointer path does not exist: $ptr_path"; exit 1; }

# The optional privateVault root is a third source of must-know rules.
priv=$(mktemp -d)
mkdir -p "$priv/personal" "$priv/shared" "$priv/private"
printf -- '- gamma private rule\n' > "$priv/private/MUSTKNOW.md"
printf '{"personalVault":{"path":"%s"},"sharedTree":{"path":"%s"},"privateVault":{"path":"%s"}}' "$priv/personal" "$priv/shared" "$priv/private" > "$priv/c.json"
BRAIN_CONFIG="$priv/c.json" bash "$hook" mustKnow | grep -q "gamma private rule" || { echo "FAIL: private must-know missing"; exit 1; }

# An empty or unreadable root must not discard content already collected.
empty_root=$(mktemp -d)
mkdir -p "$empty_root/personal" "$empty_root/shared"
printf -- '- alpha personal rule\n' > "$empty_root/personal/MUSTKNOW.md"
: > "$empty_root/shared/MUSTKNOW.md"
printf '{"personalVault":{"path":"%s"},"sharedTree":{"path":"%s"}}' "$empty_root/personal" "$empty_root/shared" > "$empty_root/c.json"
empty_out=$(BRAIN_CONFIG="$empty_root/c.json" bash "$hook" mustKnow)
echo "$empty_out" | python3 -c '
import json, sys
ctx = json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"]
rules = ctx.split("Skills from brain")[0]
assert "- alpha personal rule" in rules, rules
' || { echo "FAIL: empty shared root discarded personal must-know"; exit 1; }

# The hook emits only hookSpecificOutput.additionalContext, never a systemMessage.
echo "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert list(d) == ["hookSpecificOutput"], list(d)
assert list(d["hookSpecificOutput"]) == ["hookEventName", "additionalContext"], list(d["hookSpecificOutput"])
' || { echo "FAIL: hook output carries more than additionalContext"; exit 1; }
# A config without a privateVault key is valid and gets no extra output.
printf '{"personalVault":{"path":"%s"},"sharedTree":{"path":"%s"}}' "$empty_root/personal" "$empty_root/shared" > "$empty_root/nopriv.json"
BRAIN_CONFIG="$empty_root/nopriv.json" bash "$hook" mustKnow | grep -q "systemMessage" && { echo "FAIL: config without privateVault produced a systemMessage"; exit 1; }

# Without a usable config the hook reads no vault, even when CLAUDE_VAULT points at
# one. It still emits valid JSON with the pointer line and the catalog.
env_vault=$(mktemp -d)
printf -- '- environment vault marker\n' > "$env_vault/MUSTKNOW.md"
bad_config=$(mktemp)
printf '{ this is not json' > "$bad_config"
pathless=$(mktemp)
printf '{"personalVault":{},"sharedTree":{}}' > "$pathless"
absent="$(mktemp -d)/absent.json"
for cfg in "$bad_config" "$pathless" "$absent"; do
  bad_out=$(CLAUDE_VAULT="$env_vault" BRAIN_CONFIG="$cfg" bash "$hook" mustKnow)
  echo "$bad_out" | python3 -c '
import json, sys
ctx = json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"]
assert "brain_lib.py find" in ctx and "Skills from brain" in ctx, ctx
assert "environment vault marker" not in ctx, ctx
' || { echo "FAIL: unusable config ($cfg) did not give pointer and catalog only"; exit 1; }
done

# The nudge hooks are registered. nudge.sh start sits after daily-master-check.sh in
# the SessionStart entry. Stop and SessionEnd carry no matcher and no timeout.
python3 - "$root/hooks/hooks.json" <<'PY' || { echo "FAIL: hooks.json does not register the nudge hooks"; exit 1; }
import json, sys
hooks = json.load(open(sys.argv[1]))["hooks"]
ss = hooks["SessionStart"]
assert len(ss) == 1 and ss[0]["matcher"] == "startup|resume|clear|compact", ss
cmds = [h["command"] for h in ss[0]["hooks"]]
assert len(cmds) == 3, cmds
assert "session-start.sh" in cmds[0] and "daily-master-check.sh" in cmds[1], cmds
assert cmds[2].endswith('hooks/nudge.sh" start'), cmds[2]
for event, arg in (("Stop", "stop"), ("SessionEnd", "end")):
    entries = hooks[event]
    assert len(entries) == 1 and "matcher" not in entries[0], entries
    assert len(entries[0]["hooks"]) == 1, entries
    h = entries[0]["hooks"][0]
    assert h["type"] == "command" and h["async"] is False and "timeout" not in h, h
    assert h["command"] == 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/nudge.sh" ' + arg, h["command"]
assert all(h["async"] is False for h in ss[0]["hooks"]), ss
PY

# nudge.sh never fails and always prints valid output for its event. Every run
# points BRAIN_CONFIG at a temp path so the real ~/.config is never touched.
nudge="$root/hooks/nudge.sh"
ncfg="$tmp/nudge-cfg"; mkdir -p "$ncfg"
printf '{ this is not json' > "$ncfg/config.json"
nout=$(BRAIN_CONFIG="$ncfg/config.json" bash "$nudge" start </dev/null) || { echo "FAIL: nudge.sh start exited non-zero"; exit 1; }
echo "$nout" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d["hookSpecificOutput"]["hookEventName"] == "SessionStart", d
assert d["hookSpecificOutput"]["additionalContext"] == "", d
' || { echo "FAIL: nudge.sh start with a broken config is not an empty SessionStart payload"; exit 1; }
nout=$(BRAIN_CONFIG="$ncfg/config.json" bash "$nudge" stop </dev/null) || { echo "FAIL: nudge.sh stop exited non-zero"; exit 1; }
[ "$nout" = "{}" ] || { echo "FAIL: nudge.sh stop with empty stdin printed: $nout"; exit 1; }
nout=$(BRAIN_CONFIG="$ncfg/config.json" bash "$nudge" end </dev/null) || { echo "FAIL: nudge.sh end exited non-zero"; exit 1; }
[ -z "$nout" ] || { echo "FAIL: nudge.sh end printed: $nout"; exit 1; }
nout=$(BRAIN_CONFIG="$ncfg/config.json" bash "$nudge" bogus </dev/null) || { echo "FAIL: nudge.sh with an unknown argument exited non-zero"; exit 1; }
[ -z "$nout" ] || { echo "FAIL: nudge.sh with an unknown argument printed: $nout"; exit 1; }
nout=$(BRAIN_CONFIG="$ncfg/config.json" bash "$nudge" </dev/null) || { echo "FAIL: nudge.sh without an argument exited non-zero"; exit 1; }
[ -z "$nout" ] || { echo "FAIL: nudge.sh without an argument printed: $nout"; exit 1; }

# A missing python3 falls back to the same payloads. The PATH holds only bash
# built-ins plus the few tools the wrapper may call, none of them python3.
nopy="$tmp/nopy"; mkdir -p "$nopy"
for tool in dirname cat; do ln -s "$(command -v $tool)" "$nopy/$tool"; done
nout=$(PATH="$nopy" BRAIN_CONFIG="$ncfg/config.json" "$(command -v bash)" "$nudge" start </dev/null) || { echo "FAIL: nudge.sh start without python3 exited non-zero"; exit 1; }
[ "$nout" = '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":""}}' ] || { echo "FAIL: nudge.sh start without python3 printed: $nout"; exit 1; }
nout=$(PATH="$nopy" BRAIN_CONFIG="$ncfg/config.json" "$(command -v bash)" "$nudge" stop </dev/null) || { echo "FAIL: nudge.sh stop without python3 exited non-zero"; exit 1; }
[ "$nout" = "{}" ] || { echo "FAIL: nudge.sh stop without python3 printed: $nout"; exit 1; }

echo "PASS test_hook.sh"
