#!/usr/bin/env bash
# SessionStart hook. Syncs each vault root when its last sync is over an hour old,
# then re-injects this plugin's skill catalog and the vault must-know rules.
# It never blocks a session, never fails a session, and emits valid JSON on any
# problem. Each vault root is skipped independently when it cannot be read.
#
# Claude Code applies its output limit per hook command, and hooks.json runs this
# script as one command, so the whole payload shares one budget. A payload over
# the limit is replaced by a 2KB preview, so the script clips it first.
# It injects MUSTKNOW.md from every configured root in full, plus one line
# telling the session the vault is searchable. Those rules must be applied
# unprompted.
#
# There is no page index here on purpose. A slug list costs thousands of tokens
# in every session, so pages are found on demand with `brain_lib.py find`. The
# argument is accepted and ignored.
set -uo pipefail

MUSTKNOW_FILE="MUSTKNOW.md"
# brain_lib.py is not on PATH and its directory carries the plugin version, so
# the pointer resolves the path here rather than naming a command a session
# cannot run. The script carries its own PEP 723 dependency block, so uv
# resolves pyyaml with no flag for the caller to forget.
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
POINTER="Vault memory: run \`uv run ${PLUGIN_ROOT}/scripts/brain_lib.py find <query>\` to search all pages by slug, title, summary, tag and topic. Add --body for page text."

CATALOG='Skills from brain remain available after compaction. Invoke with the Skill tool when a request matches:
- brain-capture: Capture a URL, local file, or repo document into the LLM-Wiki vault.
- brain-digest: Run the weekly synthesis of vault changes, pinned pages, and stale material.
- brain-freshness: Verify time-sensitive vault claims against live git, GitHub, Gitea, or Cloudflare sources.
- brain-graduate: Distil durable knowledge from a project progress journal into the vault.
- brain-init: Bootstrap or reconfigure the LLM-Wiki brain and its tiered vault.
- brain-lint: Check vault integrity, links, frontmatter, must-know size, and secrets.
- brain-sync: Reconcile vault frontmatter, links, and overlapping pages non-destructively.'
CONFIG="${BRAIN_CONFIG:-$HOME/.config/brain/config.json}"

roots=()
sync_roots=()
if [ -f "$CONFIG" ]; then
  config_roots="$(python3 - "$CONFIG" <<'PY' 2>/dev/null
import json, os, sys
c = json.load(open(sys.argv[1]))
for k in ["personalVault", "sharedTree", "privateVault"]:
    p = (c.get(k) or {}).get("path")
    if p:
        print(k + "\t" + os.path.expanduser(p))
PY
  )" || config_roots=""
  while IFS=$'\t' read -r key root; do
    [ -n "$root" ] || continue
    roots+=("$root")
    # The shared tree takes changes only through pull requests, so it is never
    # pushed. daily-master-check.sh fast-forwards it instead.
    [ "$key" = "sharedTree" ] || sync_roots+=("$root")
  done <<< "$config_roots"
fi

# Sync each root with its remote first, at most once an hour, so the rules read
# below include what another machine pushed. The sync never fails the session
# and prints at most one line per root.
sync_note=""
# macOS /bin/bash is 3.2, where an empty array under set -u aborts, so guard it.
for root in ${sync_roots[@]+"${sync_roots[@]}"}; do
  [ -d "$root" ] || continue
  line="$(bash "$PLUGIN_ROOT/scripts/brain_autosync.sh" "$root" 2>/dev/null | head -n 1)" || line=""
  [ -n "$line" ] && sync_note+="${sync_note:+$'\n'}$line"
done

combined=""
for root in ${roots[@]+"${roots[@]}"}; do
  mustknow="$root/$MUSTKNOW_FILE"
  [ -f "$mustknow" ] || continue
  content="$(cat "$mustknow" 2>/dev/null)" || continue
  [ -n "$content" ] || continue
  [ -n "$combined" ] && combined+=$'\n'
  combined+="$content"
done

# The pointer always leads, even when no root yielded a MUSTKNOW.md. It is the
# only always-on surface telling a session the vault exists and is searchable,
# so losing it would leave search on demand with nothing to trigger it.
if [ -n "$combined" ]; then
  combined="$POINTER"$'\n\n'"$combined"
else
  combined="$POINTER"
fi

# Rules first, catalog last, so the rules survive if the payload is ever cut.
# If python3 fails, emit catalog-only context.
catalog="$CATALOG"
[ -n "$sync_note" ] && combined+=$'\n\n'"$sync_note"

printf '%s' "$combined" | python3 -c '
import json, sys

# Claude Code replaces SessionStart output over 10000 chars with a 2KB preview
# (anthropics/claude-code#44086), so the whole payload silently vanishes. This
# is a backstop only: brain-lint fails long before a root gets this large.
BUDGET = 9000
NOTE = "[clipped to fit the hook output limit, see index.md]"

catalog = sys.argv[1]
rules = sys.stdin.read().rstrip("\n")
room = BUDGET - len(catalog) - (2 if catalog else 0)

if len(rules) > room:
    lines = rules.split("\n")
    while lines and len("\n".join(lines)) + len(NOTE) + 1 > room:
        lines.pop()
    rules = "\n".join(lines + [NOTE])

txt = (rules + "\n\n" + catalog).strip("\n") if catalog else rules
out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": txt}}
print(json.dumps(out))
' "$catalog" 2>/dev/null || printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Skills from brain remain available after compaction."}}'

exit 0
