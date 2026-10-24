#!/usr/bin/env bash
# Tests for hooks/daily-master-check.sh and `brain_git.sh rebase`.
# Every fixture is a throwaway bare origin plus clone in a temp dir, so no test
# touches a real vault and none of them need the network.
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
HOOK="$root/hooks/daily-master-check.sh"
GIT_SH="$root/scripts/brain_git.sh"
TODAY=$(date +%Y-%m-%d)

fail() { echo "FAIL: $1"; exit 1; }

# Extract additionalContext, proving the payload is valid JSON in the process.
ctx() {
  python3 -c 'import sys, json; sys.stdout.write(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])' \
    || fail "hook did not emit valid SessionStart JSON"
}

# A bare origin on master, a seed clone to push new master commits from, and
# `shared` standing in for sharedTree.path.
make_fixture() {
  local d="$1"
  mkdir -p "$d"
  git init --quiet --bare "$d/origin.git"
  git -C "$d/origin.git" symbolic-ref HEAD refs/heads/master
  git clone --quiet "$d/origin.git" "$d/seed" 2>/dev/null
  git -C "$d/seed" config user.email test@example.com
  git -C "$d/seed" config user.name "Test"
  git -C "$d/seed" checkout --quiet -b master 2>/dev/null || true
  printf 'line one\n' > "$d/seed/page.md"
  git -C "$d/seed" add -A
  git -C "$d/seed" commit --quiet -m "seed"
  git -C "$d/seed" push --quiet -u origin master
  git clone --quiet "$d/origin.git" "$d/shared"
  git -C "$d/shared" config user.email test@example.com
  git -C "$d/shared" config user.name "Test"
  mkdir -p "$d/cfg" "$d/personal"
  cat > "$d/cfg/config.json" <<JSON
{"personalVault":{"path":"$d/personal"},"sharedTree":{"path":"$d/shared"},"journalsDir":"$d/j"}
JSON
}

# Push n further commits to origin/master, so the clone falls behind.
advance_origin() {
  local d="$1" n="${2:-1}" i
  for i in $(seq 1 "$n"); do
    printf 'extra %s\n' "$i" >> "$d/seed/page.md"
    git -C "$d/seed" add -A
    git -C "$d/seed" commit --quiet -m "extra $i"
  done
  git -C "$d/seed" push --quiet origin master
}

run_hook() { BRAIN_CONFIG="$1/cfg/config.json" bash "$HOOK"; }

sha() { git -C "$1" rev-parse "$2"; }

# ---------------------------------------------------------------- 1. unconfigured
t=$(mktemp -d)
out=$(BRAIN_CONFIG="$t/nope.json" bash "$HOOK")
[ -z "$(echo "$out" | ctx)" ] || fail "unconfigured hook injected context"

# ---------------------------------------------------------------- 2. stamped today means no network and no ref change
t=$(mktemp -d); make_fixture "$t"
printf '%s\n' "$TODAY" > "$t/cfg/last-master-check"
before=$(sha "$t/shared" master)
advance_origin "$t" 2
out=$(run_hook "$t")
[ "$(sha "$t/shared" master)" = "$before" ] || fail "stamped run still advanced master"
[ -z "$(echo "$out" | ctx)" ] || fail "stamped run injected context"

# ---------------------------------------------------------------- 3. consumer on master fast-forwards
t=$(mktemp -d); make_fixture "$t"
advance_origin "$t" 2
out=$(run_hook "$t")
[ "$(sha "$t/shared" master)" = "$(sha "$t/shared" origin/master)" ] || fail "master was not fast-forwarded"
echo "$out" | ctx | grep -q "2 new commits" || fail "consumer pull was not reported"
[ "$(cat "$t/cfg/last-master-check")" = "$TODAY" ] || fail "stamp not written after a successful check"

# ---------------------------------------------------------------- 4. contributor on a branch gets the rebase offer
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" checkout --quiet -b arm
printf 'mine\n' > "$t/shared/mine.md"
git -C "$t/shared" add -A
git -C "$t/shared" commit --quiet -m "my page"
advance_origin "$t" 2
branch_before=$(sha "$t/shared" arm)
out=$(run_hook "$t")
[ "$(git -C "$t/shared" rev-parse --abbrev-ref HEAD)" = "arm" ] || fail "hook changed the checked-out branch"
[ "$(sha "$t/shared" arm)" = "$branch_before" ] || fail "hook rewrote the branch without consent"
[ "$(sha "$t/shared" master)" = "$(sha "$t/shared" origin/master)" ] || fail "master not advanced while off it"
body=$(echo "$out" | ctx)
echo "$body" | grep -q "rebase" || fail "no rebase offer for a clean branch behind master"
echo "$body" | grep -q "brain_git.sh" || fail "rebase offer did not name the command to run"

# ---------------------------------------------------------------- 5. dirty tree blocks the offer but still advances master
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" checkout --quiet -b arm
printf 'uncommitted\n' >> "$t/shared/page.md"
advance_origin "$t" 1
out=$(run_hook "$t")
[ "$(sha "$t/shared" master)" = "$(sha "$t/shared" origin/master)" ] || fail "dirty tree blocked the master fast-forward"
body=$(echo "$out" | ctx)
echo "$body" | grep -q "uncommitted" || fail "dirty tree was not reported"
echo "$body" | grep -qi "only if\|ask whether" && fail "dirty tree still offered a rebase"
# The contribute nudge owns the changes now, so this notice must not ask Claude to mention them.
echo "$body" | grep -q "Mention this" && fail "dirty branch notice still asks Claude to mention the changes"
echo "$body" | grep -q "The rebase was skipped because the tree has uncommitted changes. Do not offer a rebase." || fail "dirty branch notice has the wrong text"
echo "$body" | grep -q "^Shared brain: branch arm at $t/shared is 1 commit behind master\." || fail "dirty branch notice lost its distance line"
grep -q uncommitted "$t/shared/page.md" || fail "hook discarded uncommitted work"

# ---------------------------------------------------------------- 5b. dirty tree on the default branch skips the pull
t=$(mktemp -d); make_fixture "$t"
printf 'uncommitted\n' >> "$t/shared/page.md"
advance_origin "$t" 2
out=$(run_hook "$t")
body=$(echo "$out" | ctx)
[ "$(sha "$t/shared" master)" != "$(sha "$t/shared" origin/master)" ] || fail "dirty default branch was fast-forwarded"
echo "$body" | grep -q "Mention this" && fail "dirty default notice still asks Claude to mention the changes"
echo "$body" | grep -q "^Shared brain: master advanced by 2 new commits. The pull was skipped because $t/shared has uncommitted changes.$" || fail "dirty default notice has the wrong text: $body"
grep -q uncommitted "$t/shared/page.md" || fail "hook discarded uncommitted work on the default branch"

# ---------------------------------------------------------------- 6. nothing new is silent
t=$(mktemp -d); make_fixture "$t"
out=$(run_hook "$t")
[ -z "$(echo "$out" | ctx)" ] || fail "up-to-date repo injected context"
[ "$(cat "$t/cfg/last-master-check")" = "$TODAY" ] || fail "stamp not written on an up-to-date check"

# ---------------------------------------------------------------- 7. no remote is silent
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" remote remove origin
out=$(run_hook "$t")
[ -z "$(echo "$out" | ctx)" ] || fail "remoteless repo injected context"

# ---------------------------------------------------------------- 8. an unreachable remote leaves the day unstamped
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" remote set-url origin "$t/does-not-exist.git"
out=$(run_hook "$t")
[ -z "$(echo "$out" | ctx)" ] || fail "unreachable remote injected context"
[ ! -f "$t/cfg/last-master-check" ] || fail "failed fetch burned the daily stamp"

# ---------------------------------------------------------------- 9. a rebase already in progress is left alone
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" checkout --quiet -b arm
printf 'branch version\n' > "$t/shared/page.md"
git -C "$t/shared" add -A
git -C "$t/shared" commit --quiet -m "branch edit"
printf 'master version\n' > "$t/seed/page.md"
git -C "$t/seed" add -A
git -C "$t/seed" commit --quiet -m "master edit"
git -C "$t/seed" push --quiet origin master
git -C "$t/shared" fetch --quiet origin master:master
git -C "$t/shared" rebase master >/dev/null 2>&1 || true
gitdir=$(git -C "$t/shared" rev-parse --absolute-git-dir)
[ -d "$gitdir/rebase-merge" ] || [ -d "$gitdir/rebase-apply" ] \
  || fail "fixture did not produce a conflicted rebase"
mid=$(sha "$t/shared" master)
advance_origin "$t" 1
out=$(run_hook "$t")
[ "$(sha "$t/shared" master)" = "$mid" ] || fail "hook touched a repo mid-rebase"
[ -z "$(echo "$out" | ctx)" ] || fail "mid-rebase repo injected context"
# The guard must bail before the fetch, so resolving the rebase later the same
# day still gets a check. A conflicted rebase also detaches HEAD, so without
# this the detached-HEAD guard would pass the test on its own.
[ ! -f "$t/cfg/last-master-check" ] || fail "mid-rebase repo consumed the daily stamp"

# ---------------------------------------------------------------- 10. brain_git.sh rebase replays the branch onto master
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" checkout --quiet -b arm
printf 'mine\n' > "$t/shared/mine.md"
git -C "$t/shared" add -A
git -C "$t/shared" commit --quiet -m "my page"
advance_origin "$t" 2
run_hook "$t" >/dev/null
bash "$GIT_SH" rebase "$t/shared" >/dev/null || fail "rebase subcommand failed on a clean branch"
git -C "$t/shared" merge-base --is-ancestor master arm || fail "branch was not replayed onto master"
[ "$(git -C "$t/shared" log -1 --format=%s arm)" = "my page" ] || fail "branch lost its own commit"
[ "$(git -C "$t/shared" rev-parse --abbrev-ref HEAD)" = "arm" ] || fail "rebase left a different branch checked out"

# ---------------------------------------------------------------- 11. rebase refuses a dirty tree
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" checkout --quiet -b arm
advance_origin "$t" 1
run_hook "$t" >/dev/null
printf 'uncommitted\n' >> "$t/shared/page.md"
before=$(sha "$t/shared" arm)
out=$(bash "$GIT_SH" rebase "$t/shared" 2>&1) && fail "rebase accepted a dirty tree"
[ "$(sha "$t/shared" arm)" = "$before" ] || fail "refused rebase still moved the branch"
grep -q uncommitted "$t/shared/page.md" || fail "refused rebase discarded uncommitted work"
# git refuses a dirty rebase by itself, so assert on OUR message. Without this
# the test would pass with our own guard deleted.
echo "$out" | grep -q "brain-contribute" || fail "dirty refusal did not point at the contribute route"

# ---------------------------------------------------------------- 12. rebase refuses on the default branch
t=$(mktemp -d); make_fixture "$t"
bash "$GIT_SH" rebase "$t/shared" >/dev/null 2>&1 && fail "rebase ran on the default branch"

# ---------------------------------------------------------------- 13. a conflict stops and reports
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" checkout --quiet -b arm
printf 'branch version\n' > "$t/shared/page.md"
git -C "$t/shared" add -A
git -C "$t/shared" commit --quiet -m "branch edit"
printf 'master version\n' > "$t/seed/page.md"
git -C "$t/seed" add -A
git -C "$t/seed" commit --quiet -m "master edit"
git -C "$t/seed" push --quiet origin master
run_hook "$t" >/dev/null
out=$(bash "$GIT_SH" rebase "$t/shared" 2>&1 || true)
echo "$out" | grep -q "page.md" || fail "conflict did not name the conflicting file"
echo "$out" | grep -q -- "--abort" || fail "conflict did not print the abort command"

# ------------------ 14. a merged contribution PR must not be counted as new to its author
# The author's own commits come back on the default branch through the merge, so
# the count of new default-branch commits overstates what is actually new to
# them. Only the branch's distance from the default branch is honest, and the
# two must not be conflated.
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" checkout --quiet -b arm
printf 'mine\n' > "$t/shared/mine.md"
git -C "$t/shared" add -A
git -C "$t/shared" commit --quiet -m "my page"
git -C "$t/shared" push --quiet -u origin arm
git -C "$t/seed" fetch --quiet origin
git -C "$t/seed" merge --quiet --no-ff -m "merge arm" origin/arm
printf 'from a teammate\n' > "$t/seed/theirs.md"
git -C "$t/seed" add -A
git -C "$t/seed" commit --quiet -m "teammate page"
git -C "$t/seed" push --quiet origin master
out=$(run_hook "$t")
body=$(echo "$out" | ctx)
# 3 commits reached master (mine, the merge, the teammate's) but only 2 of them
# are new to this branch. Reporting 3 would bill the author for their own work.
echo "$body" | grep -q "is 2 commits behind master" || fail "branch distance wrong: $body"
echo "$body" | grep -q "3 commits" && fail "reported the default-branch commit count instead of the branch distance: $body"

# ---------------- 15. a branch stays behind even when the default branch did not move
# The second check of a day sees an already-current default branch. The branch is
# no less behind for that, and reporting the default branch's movement would
# announce a meaningless zero.
t=$(mktemp -d); make_fixture "$t"
git -C "$t/shared" checkout --quiet -b arm
advance_origin "$t" 1
body=$(run_hook "$t" | ctx)
echo "$body" | grep -q "is 1 commit behind master" || fail "singular distance misreported: $body"
rm -f "$t/cfg/last-master-check"
body=$(run_hook "$t" | ctx)
[ "$(sha "$t/shared" master)" = "$(sha "$t/shared" origin/master)" ] || fail "second check moved master"
echo "$body" | grep -q "is 1 commit behind master" || fail "branch stopped being reported as behind: $body"
echo "$body" | grep -q "0 commit" && fail "announced a meaningless zero: $body"

echo "PASS test_daily_master_check.sh"
