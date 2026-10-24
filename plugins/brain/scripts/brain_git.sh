#!/bin/bash
# Safe repository operations for the brain workflows.

set -u

usage() {
  echo "usage: brain_git.sh save <repo-path> <message> | pull <repo-path> | rebase <repo-path>" >&2
  exit 2
}

require_repo() {
  if ! git -C "$1" rev-parse --is-inside-work-tree >/dev/null 2>&1
  then
    echo "Not a git repository: $1" >&2
    exit 2
  fi
}

save() {
  local repo="$1"
  local message="$2"
  local email

  require_repo "$repo"
  email="$(git -C "$repo" config user.email || true)"
  if [ -z "$email" ]
  then
    echo "Cannot save $repo because it has no repository-local git user.email. Run /brain-init to set this repository's git identity." >&2
    exit 0
  fi

  git -C "$repo" add -A
  if git -C "$repo" diff --cached --quiet
  then
    echo "No changes to save in $repo"
    exit 0
  fi

  git -C "$repo" commit -m "$message"
  # A local-only vault has no remote. The commit is the save.
  if [ -z "$(git -C "$repo" remote)" ]
  then
    echo "Saved locally in $repo. It has no remote, so nothing was pushed."
    return 0
  fi
  if ! git -C "$repo" push
  then
    echo "Saved locally in $repo, but push failed. The local commit is preserved." >&2
  fi
}

pull() {
  local repo="$1"
  local output

  require_repo "$repo"
  if [ -z "$(git -C "$repo" remote)" ]
  then
    echo "No remote for $repo. Skipping pull."
    exit 0
  fi

  if output="$(git -C "$repo" pull --rebase 2>&1)"
  then
    printf '%s\n' "$output"
    # A good pull counts as a sync, so the session-start sync waits its interval.
    local gitdir
    gitdir="$(git -C "$repo" rev-parse --absolute-git-dir 2>/dev/null)" && printf 'ok\n' > "$gitdir/brain-sync"
    exit 0
  fi

  printf '%s\n' "$output" >&2
  if [[ "$output" == *"Could not resolve host"* || "$output" == *"Could not read from remote repository"* || "$output" == *"Failed to connect"* || "$output" == *"Network is unreachable"* || "$output" == *"Connection timed out"* || "$output" == *"Connection reset"* ]]
  then
    echo "Could not reach the remote for $repo. Skipping pull." >&2
    exit 0
  fi
  exit 1
}

# Replay the current contribution branch onto the default branch. This is the one
# operation in the brain that rewrites history, so it runs only when the user has
# explicitly agreed to it, never from a hook and never on a schedule.
rebase() {
  local repo="$1"
  local default branch output

  require_repo "$repo"

  default="$(git -C "$repo" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)"
  default="${default#origin/}"
  [ -n "$default" ] || default=master

  branch="$(git -C "$repo" symbolic-ref --quiet --short HEAD 2>/dev/null)" || branch=""
  if [ -z "$branch" ]
  then
    echo "$repo has a detached HEAD. Check out your contribution branch first." >&2
    exit 1
  fi
  if [ "$branch" = "$default" ]
  then
    echo "$repo is already on $default. There is nothing to rebase." >&2
    exit 1
  fi
  if ! git -C "$repo" diff --quiet || ! git -C "$repo" diff --cached --quiet
  then
    echo "$repo has uncommitted changes. Commit them or run /brain-contribute before rebasing." >&2
    exit 1
  fi

  if output="$(git -C "$repo" rebase "$default" 2>&1)"
  then
    printf '%s\n' "$output"
    echo "Rebased $branch onto $default in $repo."
    exit 0
  fi

  # Leave the stopped rebase in place. Aborting for the user would discard their
  # chance to resolve it, so report the conflict and hand back both exits.
  printf '%s\n' "$output" >&2
  echo "Rebase of $branch onto $default stopped with conflicts in:" >&2
  git -C "$repo" diff --name-only --diff-filter=U >&2
  echo "Resolve them, then: git -C \"$repo\" rebase --continue" >&2
  echo "Or discard the rebase with: git -C \"$repo\" rebase --abort" >&2
  exit 1
}

if [ "${1:-}" = "save" ]
then
  [ "$#" -eq 3 ] || usage
  save "$2" "$3"
elif [ "${1:-}" = "pull" ]
then
  [ "$#" -eq 2 ] || usage
  pull "$2"
elif [ "${1:-}" = "rebase" ]
then
  [ "$#" -eq 2 ] || usage
  rebase "$2"
else
  usage
fi
