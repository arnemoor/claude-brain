#!/bin/bash
# Session-start sync of the vault with its remote, at most once per interval.
#
#   brain_autosync.sh <vault-path>
#
# Called by hooks/session-start.sh once per configured root before it reads
# MUSTKNOW.md, so a session starts with the rules another machine pushed. If the last attempt is younger than
# the interval it does nothing, so most session starts cost one stat call.
# Otherwise it fetches, rebases the local branch onto its upstream, pushes local
# commits, and rebuilds MUSTKNOW.md and index.md when HEAD moved or they are
# missing. The rebuild covers every configured root, like `brain_lib.py rebuild`.
#
# It never blocks or fails a session. It prints at most one line, and only when
# something changed or needs attention. It leaves the vault alone when the work
# tree has uncommitted changes, a rebase or merge is in progress, the branch is
# not the remote's default branch, or the branch has no upstream. A rebase that stops is aborted, so
# the vault is never left mid-rebase. Network steps run under a time limit, and
# after a failed fetch the next attempt waits only the retry interval.
#
# Environment:
#   BRAIN_AUTOSYNC=0             turn the sync off
#   BRAIN_SYNC_INTERVAL=<sec>    interval after a good sync, default 3600
#   BRAIN_SYNC_RETRY=<sec>       interval after a failed fetch, default 600
#
# The stamp lives in the vault's git dir, so it is never committed. brain_git.sh
# pull refreshes it too.

set -u

vault="${1:-}"
[ "${BRAIN_AUTOSYNC:-1}" = "0" ] && exit 0
[ -n "$vault" ] && [ -d "$vault" ] || exit 0

INTERVAL="${BRAIN_SYNC_INTERVAL:-3600}"
RETRY="${BRAIN_SYNC_RETRY:-600}"
NET_LIMIT=12

g() { git -C "$vault" "$@"; }
# perl's alarm is the portable time limit on macOS, which has no timeout(1).
limited() { perl -e 'alarm shift; exec @ARGV' "$@"; }

gitdir="$(g rev-parse --absolute-git-dir 2>/dev/null)" || exit 0
stamp="$gitdir/brain-sync"
lock="$gitdir/brain-sync.lock"

now="$(date +%s)"
if [ -f "$stamp" ]; then
  last="$(stat -f %m "$stamp" 2>/dev/null || stat -c %Y "$stamp" 2>/dev/null || echo 0)"
  wait="$INTERVAL"
  [ "$(cat "$stamp" 2>/dev/null)" = "fail" ] && wait="$RETRY"
  [ $((now - last)) -lt "$wait" ] && exit 0
fi

# One sync at a time. A lock older than two minutes belongs to a dead run.
if ! mkdir "$lock" 2>/dev/null; then
  lock_age=$(( now - $(stat -f %m "$lock" 2>/dev/null || stat -c %Y "$lock" 2>/dev/null || echo "$now") ))
  [ "$lock_age" -gt 120 ] || exit 0
  rmdir "$lock" 2>/dev/null
  mkdir "$lock" 2>/dev/null || exit 0
fi
trap 'rmdir "$lock" 2>/dev/null' EXIT

mark() { printf '%s\n' "$1" > "$stamp"; }

[ -n "$(g remote 2>/dev/null)" ] || exit 0
branch="$(g symbolic-ref --quiet --short HEAD 2>/dev/null)"
default="$(g symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"
default="${default#origin/}"
[ -n "$default" ] || default=main
if [ "$branch" != "$default" ]; then
  mark skip
  echo "Brain sync skipped: $vault is on ${branch:-a detached HEAD}, not $default."
  exit 0
fi
g rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1 || exit 0
if [ -d "$gitdir/rebase-merge" ] || [ -d "$gitdir/rebase-apply" ] || [ -f "$gitdir/MERGE_HEAD" ]; then
  mark skip
  echo "Brain sync skipped: a rebase or merge is in progress in the vault. Finish it or run /brain-sync."
  exit 0
fi
if [ -n "$(g status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  mark skip
  echo "Brain sync skipped: the vault has uncommitted changes. Save them, then run /brain-sync."
  exit 0
fi

# Never wait for a passphrase or a host-key prompt during a session start.
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes -o ConnectTimeout=8}"
export GIT_TERMINAL_PROMPT=0

before="$(g rev-parse HEAD)"
if ! limited "$NET_LIMIT" git -C "$vault" fetch --quiet 2>/dev/null; then
  mark fail
  echo "Brain sync skipped: the vault remote is unreachable. Retrying in $((RETRY / 60)) min."
  exit 0
fi

notes=""
note() { notes="${notes:+$notes, }$1"; }
behind="$(g rev-list --count 'HEAD..@{u}' 2>/dev/null || echo 0)"
if [ "$behind" -gt 0 ]; then
  if g rebase --quiet '@{u}' >/dev/null 2>&1; then
    note "pulled $behind commit(s)"
  else
    g rebase --abort >/dev/null 2>&1
    mark skip
    echo "Brain sync stopped: rebasing onto origin conflicted and was aborted. Run /brain-sync to resolve it."
    exit 0
  fi
fi

ahead="$(g rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
if [ "$ahead" -gt 0 ]; then
  if limited "$NET_LIMIT" git -C "$vault" push --quiet 2>/dev/null; then
    note "pushed $ahead local commit(s)"
  else
    note "$ahead local commit(s) not pushed, push failed"
  fi
fi

after="$(g rev-parse HEAD)"
if [ "$before" != "$after" ] || [ ! -f "$vault/MUSTKNOW.md" ] || [ ! -f "$vault/index.md" ]; then
  uv_bin="$(command -v uv || true)"
  for c in /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    [ -n "$uv_bin" ] && break
    [ -x "$c" ] && uv_bin="$c"
  done
  script_dir="$(cd "$(dirname "$0")" && pwd)"
  if [ -n "$uv_bin" ] && limited 20 "$uv_bin" run --quiet "$script_dir/brain_lib.py" rebuild >/dev/null 2>&1; then
    [ "$before" != "$after" ] && note "rebuilt MUSTKNOW.md and index.md"
  else
    note "rebuild failed, run /brain-sync"
  fi
fi

mark ok
[ -n "$notes" ] && echo "Brain synced with origin: $notes."
exit 0
