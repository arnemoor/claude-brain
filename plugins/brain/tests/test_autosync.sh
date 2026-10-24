#!/usr/bin/env bash
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
sync="$root/scripts/brain_autosync.sh"
hook="$root/hooks/session-start.sh"

tmp=$(mktemp -d)
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export BRAIN_SYNC_INTERVAL=0 BRAIN_SYNC_RETRY=0
# The rebuild after a pull reads the configured roots, like `brain_lib.py rebuild`.
export BRAIN_CONFIG="$tmp/c.json"

printf '{"privateVault":{"path":"%s"},"journalsDir":"%s"}' "$tmp/a" "$tmp/journals" > "$tmp/c.json"
git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/a" 2>/dev/null
setup() {
  git -C "$1" config user.name tests
  git -C "$1" config user.email tests@example.invalid
}
setup "$tmp/a"
printf 'log.md merge=union\n' > "$tmp/a/.gitattributes"
printf 'index.md\nMUSTKNOW.md\n' > "$tmp/a/.gitignore"
printf '# Operation Log\n\n## first\n' > "$tmp/a/log.md"
printf -- '- base rule\n' > "$tmp/a/rule.md"
git -C "$tmp/a" add -A && git -C "$tmp/a" commit -qm base && git -C "$tmp/a" push -q origin main
git clone -q "$tmp/origin.git" "$tmp/b"
setup "$tmp/b"

prepend() { printf '# Operation Log\n\n## %s\n%s' "$2" "$(tail -n +3 "$1/log.md")" > "$1/log.md"; }
commit_in() { git -C "$1" add -A && git -C "$1" commit -qm "$2"; }

# Pull: b pushed a commit, a gets it.
prepend "$tmp/b" "from b"; commit_in "$tmp/b" "b one"; git -C "$tmp/b" push -q
out=$(bash "$sync" "$tmp/a")
case "$out" in *"pulled 1 commit"*) ;; *) echo "FAIL: pull not reported: $out"; exit 1 ;; esac
[ "$(git -C "$tmp/a" rev-parse HEAD)" = "$(git -C "$tmp/b" rev-parse HEAD)" ] || { echo "FAIL: a did not reach origin"; exit 1; }
[ "$(cat "$tmp/a/.git/brain-sync")" = "ok" ] || { echo "FAIL: stamp not ok"; exit 1; }

# Both Macs prepend to log.md: union keeps both entries and the rebase goes through.
prepend "$tmp/b" "b two"; commit_in "$tmp/b" "b two"; git -C "$tmp/b" push -q
prepend "$tmp/a" "a two"; commit_in "$tmp/a" "a two"
out=$(bash "$sync" "$tmp/a")
case "$out" in *"pulled 1 commit"*"pushed 1 local commit"*) ;; *) echo "FAIL: union rebase or push not reported: $out"; exit 1 ;; esac
grep -q "## a two" "$tmp/a/log.md" && grep -q "## b two" "$tmp/a/log.md" || { echo "FAIL: union lost a log entry"; exit 1; }
[ "$(git -C "$tmp/a" rev-parse HEAD)" = "$(git -C "$tmp/origin.git" rev-parse main)" ] || { echo "FAIL: a's commit not pushed"; exit 1; }
git -C "$tmp/b" pull -q --rebase

# Nothing to do: no output.
out=$(bash "$sync" "$tmp/a")
[ -z "$out" ] || { echo "FAIL: idle sync printed: $out"; exit 1; }

# Inside the interval the remote is not contacted at all.
git -C "$tmp/a" remote set-url origin "$tmp/missing.git"
out=$(BRAIN_SYNC_INTERVAL=3600 bash "$sync" "$tmp/a")
[ -z "$out" ] || { echo "FAIL: sync ran inside the interval: $out"; exit 1; }

# Unreachable remote: one line, stamp fail, the session goes on.
out=$(bash "$sync" "$tmp/a")
case "$out" in *"unreachable"*) ;; *) echo "FAIL: unreachable remote not reported: $out"; exit 1 ;; esac
[ "$(cat "$tmp/a/.git/brain-sync")" = "fail" ] || { echo "FAIL: stamp not fail"; exit 1; }
# After a failure the retry interval applies, not the full one.
out=$(BRAIN_SYNC_INTERVAL=3600 BRAIN_SYNC_RETRY=0 bash "$sync" "$tmp/a")
case "$out" in *"unreachable"*) ;; *) echo "FAIL: retry interval not used after a failure: $out"; exit 1 ;; esac
git -C "$tmp/a" remote set-url origin "$tmp/origin.git"

# Uncommitted changes: skipped, nothing touched.
printf 'edit\n' >> "$tmp/a/rule.md"
out=$(bash "$sync" "$tmp/a")
case "$out" in *"uncommitted changes"*) ;; *) echo "FAIL: dirty tree not skipped: $out"; exit 1 ;; esac
git -C "$tmp/a" checkout -q -- rule.md

# A real conflict is aborted, never left mid-rebase.
printf -- '- rule from b\n' > "$tmp/b/rule.md"; commit_in "$tmp/b" "b rule"; git -C "$tmp/b" push -q
printf -- '- rule from a\n' > "$tmp/a/rule.md"; commit_in "$tmp/a" "a rule"
head_before=$(git -C "$tmp/a" rev-parse HEAD)
out=$(bash "$sync" "$tmp/a")
case "$out" in *"aborted"*) ;; *) echo "FAIL: conflict not reported: $out"; exit 1 ;; esac
[ ! -d "$tmp/a/.git/rebase-merge" ] && [ ! -d "$tmp/a/.git/rebase-apply" ] || { echo "FAIL: left mid-rebase"; exit 1; }
[ "$(git -C "$tmp/a" rev-parse HEAD)" = "$head_before" ] || { echo "FAIL: HEAD moved after an aborted rebase"; exit 1; }
git -C "$tmp/a" reset -q --hard origin/main

# Another branch is left alone.
git -C "$tmp/a" checkout -q -b side
out=$(bash "$sync" "$tmp/a")
case "$out" in *"not main"*) ;; *) echo "FAIL: other branch not skipped: $out"; exit 1 ;; esac
git -C "$tmp/a" checkout -q main

# A held lock means another sync is running.
mkdir "$tmp/a/.git/brain-sync.lock"
prepend "$tmp/b" "b three"; commit_in "$tmp/b" "b three"; git -C "$tmp/b" push -q
out=$(bash "$sync" "$tmp/a")
[ -z "$out" ] || { echo "FAIL: sync ran while locked: $out"; exit 1; }
rmdir "$tmp/a/.git/brain-sync.lock"

# Switched off.
out=$(BRAIN_AUTOSYNC=0 bash "$sync" "$tmp/a")
[ -z "$out" ] || { echo "FAIL: BRAIN_AUTOSYNC=0 still synced: $out"; exit 1; }

# Through the hook: the note reaches the session after the rules, the pointer
# still leads, and the output stays valid JSON.
# The pulled commit carries a page with a must_know line, so the rebuild that
# follows the pull must put it into MUSTKNOW.md before the hook reads it.
mkdir -p "$tmp/b/wiki-me/projects/t"
printf -- '---\ntitle: T\nsummary: t\nsources: []\nlast_updated: 2026-10-11\ntags: [me, projects]\npin: false\nmust_know: alpha vault rule\n---\n\n# T\n' > "$tmp/b/wiki-me/projects/t/t-page.md"
commit_in "$tmp/b" "b page"; git -C "$tmp/b" push -q
out=$(bash "$hook" mustKnow)
ctx=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])')
echo "$ctx" | head -1 | grep -q "brain_lib.py find" || { echo "FAIL: pointer no longer leads"; exit 1; }
echo "$ctx" | grep -q "alpha vault rule" || { echo "FAIL: rules missing"; exit 1; }
echo "$ctx" | grep -q "Brain synced with origin: pulled 2 commit(s), rebuilt MUSTKNOW.md" || { echo "FAIL: sync note missing from the hook: $ctx"; exit 1; }

# A good brain_git.sh pull refreshes the stamp.
rm -f "$tmp/a/.git/brain-sync"
bash "$root/scripts/brain_git.sh" pull "$tmp/a" >/dev/null
[ "$(cat "$tmp/a/.git/brain-sync")" = "ok" ] || { echo "FAIL: brain_git.sh pull did not stamp"; exit 1; }

echo "PASS test_autosync.sh"
