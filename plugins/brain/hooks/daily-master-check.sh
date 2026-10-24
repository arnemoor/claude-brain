#!/usr/bin/env bash
# SessionStart hook. Once a day, fast-forwards the shared brain's default branch
# from origin so a consumer who never runs a brain command still receives what
# the team merged. When the checkout sits on a contribution branch that has
# fallen behind, it asks the session to offer a rebase.
#
# It never blocks a session, never fails one, and always emits valid JSON.
#
# It never rewrites history. Every move here is `merge --ff-only` or a
# non-checkout `fetch origin <default>:<default>`, and both refuse rather than
# force. The only rewrite lives in scripts/brain_git.sh rebase, which runs only
# after the user says yes.
#
# Almost every session start takes the stamp path: one file read, no git, no
# network. The stamp is written only after a successful fetch, so being offline
# retries at the next session instead of burning the day.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${BRAIN_CONFIG:-$HOME/.config/brain/config.json}"
STAMP="$(dirname "$CONFIG")/last-master-check"
TODAY="$(date +%Y-%m-%d)"
FETCH_TIMEOUT=8

# Emit the SessionStart payload and stop. No argument means inject nothing,
# which is the silent path for every case that needs no decision from the user.
emit() {
  printf '%s' "${1:-}" | python3 -c '
import json, sys
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                         "additionalContext": sys.stdin.read()}}))
' 2>/dev/null || printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":""}}'
  exit 0
}

# Nothing to check until brain-init has written a config.
[ -f "$CONFIG" ] || emit

SHARED="$(python3 - "$CONFIG" <<'PY' 2>/dev/null
import json, sys
try:
    cfg = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(0)
print((cfg.get("sharedTree") or {}).get("path") or "")
PY
)" || SHARED=""
[ -n "$SHARED" ] && [ -d "$SHARED" ] || emit

# The daily gate.
[ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$TODAY" ] && emit

git -C "$SHARED" rev-parse --is-inside-work-tree >/dev/null 2>&1 || emit
[ -n "$(git -C "$SHARED" remote 2>/dev/null)" ] || emit

# A repo mid-rebase already has a decision pending for the user. Moving its refs
# would only make that harder to reason about, and leaving the day unstamped
# means the next session with a resolved repo picks the check up again.
# --absolute-git-dir, not --git-path: the latter answers relative to the repo
# root, and this hook never runs with the repo as its working directory.
gitdir="$(git -C "$SHARED" rev-parse --absolute-git-dir 2>/dev/null)"
if [ -n "$gitdir" ]; then
  for state in rebase-merge rebase-apply; do
    [ -d "$gitdir/$state" ] && emit
  done
fi

# A session start must never stall on a credential prompt or a half-open socket.
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/usr/bin/true
export SSH_ASKPASS=/usr/bin/true
export GIT_SSH_COMMAND="ssh -oBatchMode=yes"

# macOS ships no timeout(1), so bound the fetch with a perl alarm.
# alarm() survives exec, so this caps git itself.
perl -e 'alarm shift; exec @ARGV' "$FETCH_TIMEOUT" \
  git -C "$SHARED" fetch --quiet origin >/dev/null 2>&1 || emit

mkdir -p "$(dirname "$STAMP")" 2>/dev/null
printf '%s\n' "$TODAY" > "$STAMP" 2>/dev/null

default="$(git -C "$SHARED" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)"
if [ -z "$default" ]; then
  git -C "$SHARED" remote set-head origin -a >/dev/null 2>&1
  default="$(git -C "$SHARED" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)"
fi
default="${default#origin/}"
[ -n "$default" ] || default=master
git -C "$SHARED" rev-parse --verify --quiet "refs/remotes/origin/$default" >/dev/null 2>&1 || emit

# A detached HEAD has no branch to name in an offer and no branch to rebase.
branch="$(git -C "$SHARED" symbolic-ref --quiet --short HEAD 2>/dev/null)" || branch=""
[ -n "$branch" ] || emit

new_on_default=0
if git -C "$SHARED" rev-parse --verify --quiet "refs/heads/$default" >/dev/null 2>&1; then
  new_on_default="$(git -C "$SHARED" rev-list --count "$default..origin/$default" 2>/dev/null)"
fi
: "${new_on_default:=0}"

# Untracked files are deliberately not dirt: git rebase tolerates them, so
# counting them would block an offer that would have worked.
dirty=0
git -C "$SHARED" diff --quiet 2>/dev/null && git -C "$SHARED" diff --cached --quiet 2>/dev/null || dirty=1

if [ "$branch" = "$default" ]; then
  [ "$new_on_default" -gt 0 ] || emit
  [ "$dirty" -eq 0 ] || emit "Shared brain: $default advanced by $new_on_default new commits. The pull was skipped because $SHARED has uncommitted changes."
  git -C "$SHARED" merge --ff-only "origin/$default" >/dev/null 2>&1 \
    || emit "Shared brain: $default advanced by $new_on_default new commits but $SHARED could not be fast-forwarded. Mention this in one line."
  noun="commits"
  [ "$new_on_default" -eq 1 ] && noun="commit"
  emit "Shared brain: pulled $new_on_default new $noun from $default."
fi

# Off the default branch. This refspec advances it without checking it out and
# refuses anything that is not a fast-forward, so a diverged local default is
# left alone rather than forced.
git -C "$SHARED" fetch --quiet origin "$default:$default" >/dev/null 2>&1 || emit

behind="$(git -C "$SHARED" rev-list --count "$branch..$default" 2>/dev/null)"
: "${behind:=0}"
[ "$behind" -gt 0 ] || emit

# Report the branch's distance, never the count of commits that reached the
# default branch. A merged contribution PR returns the author's own work, so that
# count overstates what is new to them, and it reads as zero when the default
# branch was already current from an earlier check while the branch is still
# behind. This distance is the only honest and always-meaningful number.
noun="commits"
[ "$behind" -eq 1 ] && noun="commit"

[ "$dirty" -eq 0 ] || emit "Shared brain: branch $branch at $SHARED is $behind $noun behind $default. The rebase was skipped because the tree has uncommitted changes. Do not offer a rebase."

emit "Shared brain: branch $branch at $SHARED is $behind $noun behind $default.
Offer this once, briefly, and only if it does not cut across what the user is already doing: ask whether to rebase $branch onto $default. Run it ONLY on an explicit yes:
  bash \"$ROOT/scripts/brain_git.sh\" rebase \"$SHARED\"
If the user declines or does not answer, drop the subject and do not raise it again this session."
