#!/usr/bin/env bash
set -euo pipefail

readonly TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_ROOT="$(cd "${TEST_DIR}/../.." && pwd)"
readonly SOURCE_ACTIVATE_SCRIPT="${SOURCE_ROOT}/fable-method/scripts/activate-live.sh"
readonly SYSTEM_PATH='/usr/bin:/bin:/usr/sbin:/sbin'
readonly REAL_ACTIVATION_LOCK='/Users/kelvin/.fable-method-activation.lock'

PASS_COUNT=0
COMMAND_OUTPUT=''
COMMAND_STATUS=0
SCRATCH=''
HOLDER_PID=''

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  if [[ -n "$COMMAND_OUTPUT" ]]; then
    printf '%s\n' "$COMMAND_OUTPUT" >&2
  fi
  exit 1
}

pass_case() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf 'PASS: %s\n' "$1"
}

capture_command() {
  set +e
  COMMAND_OUTPUT="$("$@" 2>&1)"
  COMMAND_STATUS=$?
  set -e
}

assert_success_contains() {
  local label="$1" expected="$2"
  shift 2
  capture_command "$@"
  [[ "$COMMAND_STATUS" -eq 0 ]] \
    || fail "$label: expected exit 0, got $COMMAND_STATUS"
  [[ "$COMMAND_OUTPUT" == *"$expected"* ]] \
    || fail "$label: output did not contain: $expected"
  pass_case "$label"
}

assert_failure_contains() {
  local label="$1" expected="$2"
  shift 2
  capture_command "$@"
  [[ "$COMMAND_STATUS" -ne 0 ]] \
    || fail "$label: expected a nonzero exit"
  [[ "$COMMAND_OUTPUT" == *"$expected"* ]] \
    || fail "$label: output did not contain: $expected"
  pass_case "$label"
}

# Fingerprints every regular file's content under a live root so an assertion
# can prove a refused activation left the target byte-identical.
live_digest() {
  local root="$1" file
  if [[ ! -d "$root" ]]; then
    printf 'ABSENT\n'
    return 0
  fi
  while IFS= read -r -d '' file; do
    printf '%s  %s\n' "$(shasum -a 256 <"$file" | awk '{print $1}')" "${file#"$root"/}"
  done < <(find "$root" -type f -print0 | LC_ALL=C sort -z)
}

real_lock_fingerprint() {
  if [[ -e "$REAL_ACTIVATION_LOCK" ]]; then
    stat -f '%i %z %m %c' "$REAL_ACTIVATION_LOCK"
  else
    printf 'ABSENT\n'
  fi
}

# Probes the lock from a separate process holding its own descriptor, so a
# held lock is observed rather than inferred. Succeeds when the lock is busy.
lock_is_busy() {
  local lock="$1"
  if ( exec 9>>"$lock"; /usr/bin/lockf -s -t 0 9 ) 2>/dev/null; then
    return 1
  fi
  return 0
}

# External activation-lock holder. Acquires the lock through the same
# descriptor form production uses, signals acquisition over a FIFO, then keeps
# the descriptor open while blocked on a second FIFO. No sleeps, no polling.
hold_activation_lock() {
  local lock="$1" ready="$2" release="$3"
  exec 9>>"$lock"
  if ! /usr/bin/lockf -s -t 0 9; then
    printf 'HOLDER_LOCK_FAILED\n' >"$ready"
    exit 3
  fi
  printf 'HOLDER_LOCK_ACQUIRED\n' >"$ready"
  read -r _ <"$release" || true
}

# Guarantees no external lock holder outlives the harness. Without this, a
# case that exits before its own release and reap would leave a holder alive
# still owning the harness's inherited stdout, which stalls any piped caller.
reap_lock_holder() {
  [[ -n "$HOLDER_PID" ]] || return 0
  if kill -0 "$HOLDER_PID" 2>/dev/null; then
    kill -KILL "$HOLDER_PID" 2>/dev/null || true
  fi
  wait "$HOLDER_PID" 2>/dev/null || true
  HOLDER_PID=''
}

cleanup() {
  reap_lock_holder
  [[ -n "$SCRATCH" && -d "$SCRATCH" ]] || return 0
  case "$SCRATCH" in
    "${SCRATCH_BASE%/}"/fable-activate-live-test.*)
      rm -r -- "$SCRATCH"
      ;;
    *)
      printf 'REFUSED_CLEANUP_OUTSIDE_TEST_PREFIX: %s\n' "$SCRATCH" >&2
      return 1
      ;;
  esac
}

trap cleanup EXIT

readonly SCRATCH_BASE="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
SCRATCH="$(mktemp -d "${SCRATCH_BASE%/}/fable-activate-live-test.XXXXXX")"
readonly FIXTURE_CANONICAL="${SCRATCH}/canonical"
readonly FIXTURE_HOME="${SCRATCH}/home"
readonly LINKED_CURRENT="${SCRATCH}/linked-current"
readonly LINKED_STALE="${SCRATCH}/linked-stale"
readonly LINKED_UNTRACKED="${SCRATCH}/linked-untracked"
readonly LINKED_TRACKED_DIRTY="${SCRATCH}/linked-tracked-dirty"
readonly LINKED_BRANCH_CURRENT="${SCRATCH}/linked-branch-current"
readonly INDEPENDENT_CLONE="${SCRATCH}/independent/skill"
readonly COPIED_ROOT="${SCRATCH}/copied"
readonly NESTED_OUTER="${SCRATCH}/nested-outer"
readonly NESTED_ROOT="${NESTED_OUTER}/nested"
readonly FAKE_BIN="${SCRATCH}/fake-bin"

mkdir -p "$FIXTURE_CANONICAL" "$FIXTURE_HOME/.codex"
git -C "$SOURCE_ROOT" archive HEAD | tar -x -C "$FIXTURE_CANONICAL"
# Overlay the entire candidate authority, including uncommitted v2 sources.
# This prevents precommit regression from silently exercising archived v1 files.
for skill in fable-method fable-judge; do
  if [[ -d "$SOURCE_ROOT/$skill" ]]; then
    cp -R "$SOURCE_ROOT/$skill/." "$FIXTURE_CANONICAL/$skill/"
  fi
done

# Production has no runtime bypass. The harness rewrites fixed installation
# metadata only inside its disposable repository so an accepted activation can
# exercise the real write path without ever naming a real live installation.
/usr/bin/ruby -e '
  canonical_root, fixture_home, *paths = ARGV
  replacements = {
    "/Users/kelvin/VibeCoding-WorkSpace/skill" => canonical_root,
    "/Users/kelvin" => fixture_home
  }
  paths.each do |path|
    content = File.binread(path)
    content = content.gsub(Regexp.union(replacements.keys)) { |old| replacements.fetch(old) }
    File.binwrite(path, content)
  end
' "$FIXTURE_CANONICAL" "$FIXTURE_HOME" \
  "$FIXTURE_CANONICAL/fable-method/scripts/activate-live.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/sync-platforms.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/platform_manifest.rb" \
  "$FIXTURE_CANONICAL/fable-method/platforms.yaml"

# Project-local TMPDIR can itself be below /Users/kelvin; strip only the
# two exact fixture roots before checking for surviving real user paths.
if ! /usr/bin/ruby -e '
  canonical, home, *paths = ARGV
  paths.each do |path|
    content = File.read(path).gsub(canonical, "FIXTURE_CANONICAL").gsub(home, "FIXTURE_HOME")
    raise "real user path survived: #{path}" if content.include?("/Users/kelvin")
  end
' "$FIXTURE_CANONICAL" "$FIXTURE_HOME" \
  "$FIXTURE_CANONICAL/fable-method/scripts/activate-live.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/sync-platforms.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/platform_manifest.rb" \
  "$FIXTURE_CANONICAL/fable-method/platforms.yaml"; then
  fail 'fixture isolation: a real user path survived rewriting'
fi
pass_case 'fixture activation targets are scratch-only'

readonly FIXTURE_LOCK="${FIXTURE_HOME}/.fable-method-activation.lock"
readonly REAL_LOCK_BEFORE="$(real_lock_fingerprint)"
grep -Fxq "readonly USER_HOME='${FIXTURE_HOME}'" \
  "$FIXTURE_CANONICAL/fable-method/scripts/activate-live.sh" \
  || fail 'fixture isolation: fixture USER_HOME was not rewritten into scratch'
[[ ! -e "$FIXTURE_LOCK" ]] || fail 'fixture isolation: the fixture lock already exists'
pass_case 'fixture activation lock is scratch-only and absent'

git -C "$FIXTURE_CANONICAL" init -q -b master
git -C "$FIXTURE_CANONICAL" config user.name 'Fable Activate Test'
git -C "$FIXTURE_CANONICAL" config user.email 'fable-activate-test@example.invalid'
# Distinct historical bundles, with sources kept internally consistent. Change
# an existing heading so a near-limit canonical Skill keeps its line budget.
for skill in fable-method fable-judge; do
  /usr/bin/ruby -e '
    path = ARGV.fetch(0)
    body = File.binread(path)
    historical = body.sub(/^# [^\n]+$/) { |heading| "#{heading} (Historical fixture revision.)" }
    raise "historical fixture heading missing" if historical == body
    File.binwrite(path, historical)
  ' "$FIXTURE_CANONICAL/$skill/shared/SKILL.md"
  "$FIXTURE_CANONICAL/fable-method/scripts/sync-platforms.sh" --write --skill "$skill" >/dev/null
done
git -C "$FIXTURE_CANONICAL" add --all
git -C "$FIXTURE_CANONICAL" commit -q -m 'fixture stale master'
readonly STALE_HEAD="$(git -C "$FIXTURE_CANONICAL" rev-parse HEAD)"

git -C "$FIXTURE_CANONICAL" switch -q -c canonical-tip
for skill in fable-method fable-judge; do
  cp "$SOURCE_ROOT/$skill/shared/SKILL.md" "$FIXTURE_CANONICAL/$skill/shared/SKILL.md"
  "$FIXTURE_CANONICAL/fable-method/scripts/sync-platforms.sh" --write --skill "$skill" >/dev/null
done
git -C "$FIXTURE_CANONICAL" add fable-method fable-judge
git -C "$FIXTURE_CANONICAL" commit -q -m 'fixture canonical origin master'
readonly CURRENT_HEAD="$(git -C "$FIXTURE_CANONICAL" rev-parse HEAD)"
git -C "$FIXTURE_CANONICAL" remote add origin "$FIXTURE_CANONICAL"
git -C "$FIXTURE_CANONICAL" update-ref refs/remotes/origin/master "$CURRENT_HEAD"
git -C "$FIXTURE_CANONICAL" switch -q master

git -C "$FIXTURE_CANONICAL" worktree add -q --detach "$LINKED_CURRENT" "$CURRENT_HEAD"
git -C "$FIXTURE_CANONICAL" worktree add -q --detach "$LINKED_STALE" "$STALE_HEAD"
git -C "$FIXTURE_CANONICAL" worktree add -q --detach "$LINKED_UNTRACKED" "$CURRENT_HEAD"
git -C "$FIXTURE_CANONICAL" worktree add -q --detach "$LINKED_TRACKED_DIRTY" "$CURRENT_HEAD"
git -C "$FIXTURE_CANONICAL" worktree add -q "$LINKED_BRANCH_CURRENT" canonical-tip

readonly CURRENT_SCRIPT="${LINKED_CURRENT}/fable-method/scripts/activate-live.sh"
readonly STALE_SCRIPT="${LINKED_STALE}/fable-method/scripts/activate-live.sh"
readonly OLD_MASTER_SCRIPT="${FIXTURE_CANONICAL}/fable-method/scripts/activate-live.sh"
readonly SCRATCH_LIVE="${FIXTURE_HOME}/.codex/skills/fable-method"

[[ ! -e "$SCRATCH_LIVE" ]] || fail 'normal --check precondition: scratch live target already exists'
assert_success_contains \
  'same common repository detached current master --check' \
  "CANONICAL_MASTER_HEAD: ${CURRENT_HEAD}" \
  "$CURRENT_SCRIPT" --check
[[ ! -e "$SCRATCH_LIVE" ]] || fail 'normal --check wrote the scratch live target'
pass_case 'normal --check made no live write'

mkdir -p "$FAKE_BIN"
ln -s /usr/bin/false "$FAKE_BIN/git"
assert_success_contains \
  'hostile PATH git substitution is neutralized' \
  'ACTIVATION_READY: YES' \
  /usr/bin/env PATH="${FAKE_BIN}:${SYSTEM_PATH}" \
  "$CURRENT_SCRIPT" --check --platform codex

assert_failure_contains \
  'GIT_DIR and GIT_WORK_TREE spoof is rejected' \
  'GIT_DIR override is not allowed' \
  /usr/bin/env GIT_DIR="${INDEPENDENT_CLONE}/.git" GIT_WORK_TREE="$INDEPENDENT_CLONE" \
  "$CURRENT_SCRIPT" --check --platform codex

assert_failure_contains \
  'same repository stale detached source is rejected' \
  "activation_source_head: ${STALE_HEAD}" \
  "$STALE_SCRIPT" --activate --platform codex
[[ ! -e "$SCRATCH_LIVE" ]] || fail 'stale detached source wrote the scratch live target'

assert_failure_contains \
  'old local master branch name alone is rejected' \
  'branch: master (informational; detached HEAD is allowed)' \
  "$OLD_MASTER_SCRIPT" --activate --platform codex
[[ ! -e "$SCRATCH_LIVE" ]] || fail 'old local master source wrote the scratch live target'

mkdir -p "$(dirname "$INDEPENDENT_CLONE")"
git clone -q "$FIXTURE_CANONICAL" "$INDEPENDENT_CLONE"
assert_failure_contains \
  'independent clone with the same basename is rejected' \
  'Git common directory does not match canonical repository' \
  "$INDEPENDENT_CLONE/fable-method/scripts/activate-live.sh" --activate --platform codex

mkdir -p "$COPIED_ROOT"
cp -R "$LINKED_CURRENT/fable-method" "$COPIED_ROOT/fable-method"
assert_failure_contains \
  'copied non-Git repository is rejected' \
  'executing repository root is not a Git repository' \
  "$COPIED_ROOT/fable-method/scripts/activate-live.sh" --activate --platform codex

mkdir -p "$NESTED_ROOT"
git -C "$NESTED_OUTER" init -q -b outer
cp -R "$LINKED_CURRENT/fable-method" "$NESTED_ROOT/fable-method"
git -C "$NESTED_ROOT" init -q -b unrelated
assert_failure_contains \
  'nested unrelated repository is rejected' \
  'Git common directory does not match canonical repository' \
  "$NESTED_ROOT/fable-method/scripts/activate-live.sh" --activate --platform codex

: >"$LINKED_UNTRACKED/fable-method/untracked-source"
assert_failure_contains \
  'untracked Fable source is rejected' \
  'fable_untracked: 1 (required: 0)' \
  "$LINKED_UNTRACKED/fable-method/scripts/activate-live.sh" --activate --platform codex

printf '\n# tracked-dirty fixture\n' >>"$LINKED_TRACKED_DIRTY/fable-method/scripts/activate-live.sh"
assert_failure_contains \
  'tracked dirty Fable source is rejected' \
  'fable_tracked_dirty: 1 (required: 0)' \
  "$LINKED_TRACKED_DIRTY/fable-method/scripts/activate-live.sh" --activate --platform codex

assert_success_contains \
  'same common repository detached current master activates scratch target' \
  'ACTIVATION_RESULT: ACTIVATED' \
  "$CURRENT_SCRIPT" --activate --platform codex
[[ -f "$SCRATCH_LIVE/SKILL.md" ]] || fail 'accepted detached activation did not write the scratch fixture'
pass_case 'detached current master activation stayed inside scratch'

assert_success_contains \
  'branch worktree at canonical origin master is accepted' \
  'ACTIVATION_RESULT: ALREADY_CURRENT' \
  "$LINKED_BRANCH_CURRENT/fable-method/scripts/activate-live.sh" --activate --platform codex

# --- Activation single-writer lock ----------------------------------------
# These cases run before refs/remotes/origin/master is deleted, because an
# accepted activation still requires that ref to resolve.

readonly HOLDER_READY="${SCRATCH}/lock-holder-ready.fifo"
readonly HOLDER_RELEASE="${SCRATCH}/lock-holder-release.fifo"
readonly LIVE_DIGEST_BEFORE_LOCK="$(live_digest "$SCRATCH_LIVE")"

[[ "$LIVE_DIGEST_BEFORE_LOCK" != 'ABSENT' ]] \
  || fail 'T8a precondition: the scratch live target is absent'
[[ -d "$FIXTURE_HOME" && -w "$FIXTURE_HOME" ]] \
  || fail 'T8a precondition: the fixture home is not a writable directory'
mkfifo "$HOLDER_READY" "$HOLDER_RELEASE"

# T8a - contention fails closed and writes nothing.
hold_activation_lock "$FIXTURE_LOCK" "$HOLDER_READY" "$HOLDER_RELEASE" &
HOLDER_PID=$!
read -r HOLDER_SIGNAL <"$HOLDER_READY"
[[ "$HOLDER_SIGNAL" == 'HOLDER_LOCK_ACQUIRED' ]] \
  || fail "T8a: the external holder did not acquire the fixture lock: ${HOLDER_SIGNAL}"
pass_case 'T8a external holder owns the activation lock'

assert_failure_contains \
  'T8a contended --activate fails closed' \
  'ACTIVATION_LOCK_BUSY' \
  "$CURRENT_SCRIPT" --activate --platform codex
[[ "$COMMAND_OUTPUT" == *"lock: ${FIXTURE_LOCK}"* ]] \
  || fail 'T8a: the contention report did not name the fixture lock'
[[ "$(live_digest "$SCRATCH_LIVE")" == "$LIVE_DIGEST_BEFORE_LOCK" ]] \
  || fail 'T8a: contended --activate changed the scratch live target'
pass_case 'T8a contended --activate left the live target byte-identical'

# T8b - --check stays lock-free and read-only while the lock is held.
assert_success_contains \
  'T8b --check succeeds while the activation lock is held' \
  'ACTIVATION_READY: YES' \
  "$CURRENT_SCRIPT" --check --platform codex
lock_is_busy "$FIXTURE_LOCK" \
  || fail 'T8b: --check released, stole, or never left the held activation lock'
[[ "$(live_digest "$SCRATCH_LIVE")" == "$LIVE_DIGEST_BEFORE_LOCK" ]] \
  || fail 'T8b: --check altered the scratch live target'
for OTHER_LIVE_ROOT in .claude/skills .gemini/skills .gemini/config/skills; do
  [[ ! -e "${FIXTURE_HOME}/${OTHER_LIVE_ROOT}/fable-method" ]] \
    || fail "T8b: --check created a fixture live root: ${OTHER_LIVE_ROOT}"
done
pass_case 'T8b --check left the held lock and every fixture live root untouched'

# T8c - a normally released lock is reacquired with no manual cleanup.
printf 'release\n' >"$HOLDER_RELEASE"
set +e
wait "$HOLDER_PID"
HOLDER_STATUS=$?
set -e
HOLDER_PID=''
[[ "$HOLDER_STATUS" -eq 0 ]] \
  || fail "T8c: the holder did not exit normally: ${HOLDER_STATUS}"
pass_case 'T8c external holder released the lock and was reaped'

case "$SCRATCH_LIVE" in
  "${SCRATCH}"/*) rm -r -- "$SCRATCH_LIVE" ;;
  *) fail 'T8c: refused to remove a live target outside scratch' ;;
esac
assert_success_contains \
  'T8c activation succeeds after a normal lock release' \
  'ACTIVATION_RESULT: ACTIVATED' \
  "$CURRENT_SCRIPT" --activate --platform codex
[[ "$COMMAND_OUTPUT" == *'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION'* ]] \
  || fail 'T8c: post-write verification did not report the current materialization'
[[ -f "$SCRATCH_LIVE/SKILL.md" ]] \
  || fail 'T8c: the accepted activation did not write the scratch fixture'
[[ -f "$FIXTURE_LOCK" && ! -L "$FIXTURE_LOCK" ]] \
  || fail 'T8c: the activation lock file was removed or replaced'
pass_case 'T8c activation wrote the live target under the lock with no cleanup'

# T8d - a crashed holder is released by the kernel. Mandatory regression for
# the falsified PID/stale-file design: no sleep, no retry, no lock removal.
hold_activation_lock "$FIXTURE_LOCK" "$HOLDER_READY" "$HOLDER_RELEASE" &
HOLDER_PID=$!
read -r HOLDER_SIGNAL <"$HOLDER_READY"
[[ "$HOLDER_SIGNAL" == 'HOLDER_LOCK_ACQUIRED' ]] \
  || fail "T8d: the external holder did not acquire the fixture lock: ${HOLDER_SIGNAL}"
lock_is_busy "$FIXTURE_LOCK" \
  || fail 'T8d: the holder does not actually own the lock'
kill -KILL "$HOLDER_PID"
set +e
# The signal death is deliberate, so the shell's job notification for it is
# noise; HOLDER_STATUS below is the assertion that the holder actually died.
wait "$HOLDER_PID" 2>/dev/null
HOLDER_STATUS=$?
set -e
HOLDER_PID=''
[[ "$HOLDER_STATUS" -eq 137 ]] \
  || fail "T8d: the killed holder did not report SIGKILL: ${HOLDER_STATUS}"
pass_case 'T8d crashed holder was reaped'

assert_success_contains \
  'T8d activation succeeds immediately after a holder crash' \
  'ACTIVATION_RESULT: ALREADY_CURRENT' \
  "$CURRENT_SCRIPT" --activate --platform codex
[[ -f "$FIXTURE_LOCK" ]] \
  || fail 'T8d: the lock file was removed to recover from the crash'
pass_case 'T8d kernel released the crashed lock with no stale cleanup'

# T8e - structural guard on the production script, not the rewritten fixture.
readonly LOCK_ASSIGNMENT='readonly ACTIVATION_LOCK="${USER_HOME}/.fable-method-activation.lock"'
grep -Fxq "$LOCK_ASSIGNMENT" "$SOURCE_ACTIVATE_SCRIPT" \
  || fail 'T8e: the activation lock path is not derived verbatim from USER_HOME'
[[ "$(grep -c 'ACTIVATION_LOCK=' "$SOURCE_ACTIVATE_SCRIPT")" -eq 1 ]] \
  || fail 'T8e: the activation lock path is assigned more than once'
[[ "$(grep -c -F '.fable-method-activation.lock' "$SOURCE_ACTIVATE_SCRIPT")" -eq 1 ]] \
  || fail 'T8e: more than one activation lock path exists'
grep -Fq '/usr/bin/lockf -s -t 0 9' "$SOURCE_ACTIVATE_SCRIPT" \
  || fail 'T8e: the absolute /usr/bin/lockf descriptor form is missing'
grep -Fq 'exec 9>>"$ACTIVATION_LOCK"' "$SOURCE_ACTIVATE_SCRIPT" \
  || fail 'T8e: fd 9 is not opened for append on the activation lock'
readonly FD9_REDIRECTIONS="$(grep -o -E '(^|[^0-9])9[<>]' "$SOURCE_ACTIVATE_SCRIPT" | wc -l | tr -d ' ')"
[[ "$FD9_REDIRECTIONS" -eq 1 ]] \
  || fail "T8e: expected exactly one fd-9 redirection, found ${FD9_REDIRECTIONS}"
grep -Fq '! -L "$ACTIVATION_LOCK"' "$SOURCE_ACTIVATE_SCRIPT" \
  || fail 'T8e: the symlink refusal guard on the lock path is missing'
readonly SYMLINK_GUARD_LINE="$(grep -n -F "die 'ACTIVATION_LOCK_PATH_IS_SYMLINK'" \
  "$SOURCE_ACTIVATE_SCRIPT" | cut -d: -f1)"
readonly FD9_OPEN_LINE="$(grep -n -F 'exec 9>>"$ACTIVATION_LOCK"' \
  "$SOURCE_ACTIVATE_SCRIPT" | cut -d: -f1)"
[[ -n "$SYMLINK_GUARD_LINE" && -n "$FD9_OPEN_LINE" \
  && "$SYMLINK_GUARD_LINE" -lt "$FD9_OPEN_LINE" ]] \
  || fail 'T8e: the symlink guard does not precede opening fd 9'
pass_case 'T8e production locking uses the absolute lockf fd-9 form'

readonly FIRST_ACTIVATE_STATEMENT="$(/usr/bin/awk '
  $0 == "do_activate() {" { found = 1; next }
  found { sub(/^[[:space:]]+/, "", $0); print; exit }
' "$SOURCE_ACTIVATE_SCRIPT")"
[[ "$FIRST_ACTIVATE_STATEMENT" == 'acquire_activation_lock' ]] \
  || fail "T8e: do_activate does not acquire the lock first: ${FIRST_ACTIVATE_STATEMENT}"

readonly DO_ACTIVATE_BODY="$(/usr/bin/awk '
  $0 == "do_activate() {" { inside = 1; next }
  inside && $0 == "}" { exit }
  inside { print }
' "$SOURCE_ACTIVATE_SCRIPT")"
[[ "$(printf '%s\n' "$DO_ACTIVATE_BODY" | grep -c 'acquire_activation_lock')" -eq 1 ]] \
  || fail 'T8e: do_activate acquires the activation lock more than once'

readonly DO_CHECK_BODY="$(/usr/bin/awk '
  $0 == "do_check() {" { inside = 1; next }
  inside && $0 == "}" { exit }
  inside { print }
' "$SOURCE_ACTIVATE_SCRIPT")"
if printf '%s\n' "$DO_CHECK_BODY" | grep -Fq 'acquire_activation_lock'; then
  fail 'T8e: do_check acquires the activation lock'
fi
pass_case 'T8e acquisition is the first and only statement of do_activate, never of do_check'

forbid_in_source() {
  local label="$1" pattern="$2"
  if grep -E -q "$pattern" "$SOURCE_ACTIVATE_SCRIPT"; then
    fail "T8e: ${label}"
  fi
}

forbid_in_source 'the superseded shlock primitive is referenced' 'shlock'
forbid_in_source 'fd 9 is explicitly closed' '9>&-|9<&-'
forbid_in_source 'a trap touches the activation lock' 'trap.*(ACTIVATION_LOCK|lockf|9>)'
forbid_in_source 'the activation lock is unlinked' '(^|[[:space:]])(rm|unlink)[[:space:]][^|&;]*ACTIVATION_LOCK'
forbid_in_source 'the lock path has an environment override' 'ACTIVATION_LOCK[^=]*:-|:-[^=]*ACTIVATION_LOCK'
forbid_in_source 'the lock path is derived from HOME or TMPDIR' '\$\{HOME\}|\$HOME([^_A-Za-z]|$)|\$\{?TMPDIR'
forbid_in_source 'activation locking depends on a checkpoint' '[Cc]heckpoint'
forbid_in_source 'lockf is resolved through PATH' '(^|[[:space:]])lockf[[:space:]]'
pass_case 'T8e no stale-cleanup, override, trap, unlink, or PATH-resolved lock path exists'

readonly POST_WRITE_REGION="$(printf '%s\n' "$DO_ACTIVATE_BODY" | /usr/bin/awk '
  /rsync -a --checksum --delete/ { seen = 1 }
  seen { print }
  seen && /classify_platform/ { exit }
')"
[[ "$POST_WRITE_REGION" == *'rsync -a --checksum --delete'* && "$POST_WRITE_REGION" == *'classify_platform'* ]] \
  || fail 'T8e: could not locate the rsync-to-verification region of do_activate'
if printf '%s\n' "$POST_WRITE_REGION" \
  | grep -E -q '9[<>]|lockf|ACTIVATION_LOCK|(^|[[:space:]])(rm|unlink)[[:space:]]'; then
  fail 'T8e: an explicit lock release exists between rsync and post-write verification'
fi
pass_case 'T8e no explicit lock release exists between rsync and post-write verification'

[[ "$(real_lock_fingerprint)" == "$REAL_LOCK_BEFORE" ]] \
  || fail 'T8: the real activation lock was created or modified'
pass_case 'T8 real activation lock was never created or modified'

# --- Reviewed Codex local-drift replacement mode (--replace-reviewed-local-drift)
#
# H/T/M bind to CURRENT_HEAD, which is the exact commit LINKED_CURRENT/
# CURRENT_SCRIPT already runs at, so the identity checks reuse fixture state
# the suite above already validated rather than staging a parallel commit.
readonly RLD_H="$CURRENT_HEAD"
readonly RLD_T="$(git -C "$FIXTURE_CANONICAL" rev-parse "${CURRENT_HEAD}^{tree}")"
readonly RLD_M="$(git -C "$FIXTURE_CANONICAL" rev-parse "${CURRENT_HEAD}:fable-method/platforms/codex/fable-method")"
readonly RLD_ZERO_L="$(printf '0%.0s' $(seq 1 64))"
readonly RLD_ZERO_OBJ="$(printf '0%.0s' $(seq 1 40))"

# Recovers SCRATCH_LIVE's true current LIVE_BUNDLE_SHA256_SERIALIZATION_V1
# value by provoking a deliberate mismatch against RLD_ZERO_L and reading the
# production script's own reported "observed" value back out of its refusal -
# black-box, and it proves the CLI's mismatch reporting round-trips exactly,
# without duplicating the serialization algorithm inside the test.
rld_probe_observed_l() {
  local platform="${1:-codex}" materialization_tree="${2:-$RLD_M}" skill="${3:-fable-method}"
  capture_command "$CURRENT_SCRIPT" --activate --platform "$platform" --replace-reviewed-local-drift \
    --expected-live-sha256 "$RLD_ZERO_L" \
    --expected-canonical-head "$RLD_H" \
    --expected-canonical-tree "$RLD_T" \
    --expected-materialization-tree "$materialization_tree" --skill "$skill"
  [[ "$COMMAND_STATUS" -ne 0 ]] \
    || fail 'rld_probe_observed_l: the all-zero probe unexpectedly succeeded'
  local observed
  observed="$(printf '%s\n' "$COMMAND_OUTPUT" | sed -n 's/.*observed \([0-9a-f]\{64\}\)$/\1/p')"
  [[ "$observed" =~ ^[0-9a-f]{64}$ ]] \
    || fail "rld_probe_observed_l: could not recover an observed sha256 from: ${COMMAND_OUTPUT}"
  printf '%s\n' "$observed"
}

# C/J structural checks supplement the deterministic runtime injections below.
# The injections wrap functions only in a sourced disposable fixture process;
# production has no synchronization hook or runtime test bypass.
readonly RLD_DO_ACTIVATE_REPLACE_BODY="$(/usr/bin/awk '
  $0 == "do_activate_replace_reviewed_local_drift() {" { inside = 1; next }
  inside && $0 == "}" { exit }
  inside { print }
' "$SOURCE_ACTIVATE_SCRIPT")"
[[ -n "$RLD_DO_ACTIVATE_REPLACE_BODY" ]] \
  || fail 'C/J: could not locate do_activate_replace_reviewed_local_drift in the source script'

readonly RLD_FIRST_REPLACE_STATEMENT="$(printf '%s\n' "$RLD_DO_ACTIVATE_REPLACE_BODY" | /usr/bin/awk '
  { sub(/^[[:space:]]+/, "", $0); if ($0 != "") { print; exit } }
')"
[[ "$RLD_FIRST_REPLACE_STATEMENT" == 'acquire_activation_lock' ]] \
  || fail "C/J: do_activate_replace_reviewed_local_drift does not acquire the lock first: ${RLD_FIRST_REPLACE_STATEMENT}"
pass_case 'C/J structural: replacement acquires the shared activation lock as its first statement'

readonly RLD_PREWRITE_REGION="$(printf '%s\n' "$RLD_DO_ACTIVATE_REPLACE_BODY" | /usr/bin/awk '
  /rsync -a --checksum --delete/ { exit }
  { print }
')"
readonly RLD_IDENTITY_CALLS="$(printf '%s\n' "$RLD_PREWRITE_REGION" | grep -c -F 'require_reviewed_replacement_canonical_identity')"
readonly RLD_BUNDLE_CALLS="$(printf '%s\n' "$RLD_PREWRITE_REGION" | grep -c -F 'compute_live_bundle_sha256')"
[[ "$RLD_IDENTITY_CALLS" -eq 2 ]] \
  || fail "C: expected exactly two pre-write require_reviewed_replacement_canonical_identity calls, found ${RLD_IDENTITY_CALLS}"
[[ "$RLD_BUNDLE_CALLS" -eq 2 ]] \
  || fail "C: expected exactly two pre-write compute_live_bundle_sha256 calls, found ${RLD_BUNDLE_CALLS}"
if printf '%s\n' "$RLD_PREWRITE_REGION" | grep -E -q '(^|[[:space:]])(mkdir|rsync)[[:space:]]'; then
  fail 'C: a write statement exists before the prewrite revalidation completes'
fi
pass_case 'C: exactly two full identity+bundle revalidations precede the write, with no write between them'

readonly RLD_POST_WRITE_REGION="$(printf '%s\n' "$RLD_DO_ACTIVATE_REPLACE_BODY" | /usr/bin/awk '
  /rsync -a --checksum --delete/ { seen = 1 }
  seen { print }
')"
[[ "$RLD_POST_WRITE_REGION" == *'rsync -a --checksum --delete'* ]] \
  || fail 'J: could not locate the rsync call in the replacement function'
[[ "$RLD_POST_WRITE_REGION" == *'classify_platform "$platform"'* ]] \
  || fail 'J: the replacement function does not re-classify after the write'
[[ "$RLD_POST_WRITE_REGION" == *'CLASSIFY_STATE" != EXACT_CURRENT_MATERIALIZATION'* ]] \
  || fail 'J: the replacement function does not require EXACT_CURRENT_MATERIALIZATION post-write'
readonly RLD_RESULT_LINE_NUM="$(printf '%s\n' "$RLD_POST_WRITE_REGION" \
  | grep -n -F "printf 'ACTIVATION_RESULT: ACTIVATED" | head -1 | cut -d: -f1)"
readonly RLD_CHECK_LINE_NUM="$(printf '%s\n' "$RLD_POST_WRITE_REGION" \
  | grep -n -F 'CLASSIFY_STATE" != EXACT_CURRENT_MATERIALIZATION' | head -1 | cut -d: -f1)"
[[ -n "$RLD_RESULT_LINE_NUM" && -n "$RLD_CHECK_LINE_NUM" && "$RLD_CHECK_LINE_NUM" -lt "$RLD_RESULT_LINE_NUM" ]] \
  || fail 'J: ACTIVATION_RESULT: ACTIVATED is not gated behind the post-write EXACT_CURRENT_MATERIALIZATION check'
pass_case 'J: post-write success output is structurally gated behind a re-verified EXACT_CURRENT_MATERIALIZATION'

# G - lock contention blocks the replacement write path exactly like ordinary
# activation (T8a above): acquisition is the first statement, so a placeholder
# identity is enough - contention must fail before any identity is examined.
hold_activation_lock "$FIXTURE_LOCK" "$HOLDER_READY" "$HOLDER_RELEASE" &
HOLDER_PID=$!
read -r HOLDER_SIGNAL <"$HOLDER_READY"
[[ "$HOLDER_SIGNAL" == 'HOLDER_LOCK_ACQUIRED' ]] \
  || fail "G: the external holder did not acquire the fixture lock: ${HOLDER_SIGNAL}"

readonly RLD_DIGEST_BEFORE_G="$(live_digest "$SCRATCH_LIVE")"
assert_failure_contains \
  'G: contended replacement activation fails closed' \
  'ACTIVATION_LOCK_BUSY' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"
[[ "$COMMAND_OUTPUT" == *"lock: ${FIXTURE_LOCK}"* ]] \
  || fail 'G: the contention report did not name the fixture lock'
[[ "$(live_digest "$SCRATCH_LIVE")" == "$RLD_DIGEST_BEFORE_G" ]] \
  || fail 'G: a lock-contended replacement activation changed the live target'
pass_case 'G: replacement activation cannot enter the write path while the lock is held'

printf 'release\n' >"$HOLDER_RELEASE"
set +e
wait "$HOLDER_PID"
HOLDER_STATUS=$?
set -e
HOLDER_PID=''
[[ "$HOLDER_STATUS" -eq 0 ]] \
  || fail "G: the holder did not exit normally: ${HOLDER_STATUS}"
pass_case 'G external holder released the lock and was reaped'

# SCRATCH_LIVE is EXACT_CURRENT_MATERIALIZATION here (T8c/T8d above). Drift it
# so the negative cases below exercise real LOCAL_DRIFT content, and so the
# eventual case-A probe below observes a genuine, non-trivial bundle.
[[ -f "$SCRATCH_LIVE/SKILL.md" ]] || fail 'replacement fixture precondition: SCRATCH_LIVE/SKILL.md is missing'
printf '\n<!-- reviewed local drift fixture marker -->\n' >>"$SCRATCH_LIVE/SKILL.md"
readonly RLD_DIGEST_DRIFT_BASELINE="$(live_digest "$SCRATCH_LIVE")"

# K - CLI fail-closed matrix. None of these reach the write path (they fail
# at argument parsing or at the H/T/M check, which runs before the live
# bundle is even inventoried), so RLD_ZERO_L stands in for a real L throughout.
assert_failure_contains \
  'K: missing --expected-materialization-tree is rejected' \
  'REVIEWED_REPLACEMENT_MISSING_IDENTITY' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T"

assert_failure_contains \
  'K: duplicate --expected-live-sha256 is rejected' \
  'REVIEWED_REPLACEMENT_MISSING_IDENTITY' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"

assert_failure_contains \
  'K: duplicate --replace-reviewed-local-drift is rejected' \
  'may be given at most once' \
  "$CURRENT_SCRIPT" --activate --platform codex \
  --replace-reviewed-local-drift --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"

assert_failure_contains \
  'K: malformed (wrong length) --expected-live-sha256 is rejected' \
  'REVIEWED_REPLACEMENT_MALFORMED_IDENTITY' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 'abc123' \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"

readonly RLD_H_UPPER="$(printf '%s' "$RLD_H" | tr '[:lower:]' '[:upper:]')"
assert_failure_contains \
  'K: uppercase-hex --expected-canonical-head is rejected as malformed' \
  'REVIEWED_REPLACEMENT_MALFORMED_IDENTITY' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H_UPPER" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"

assert_failure_contains \
  'K: --replace-reviewed-local-drift with platform gemini is rejected' \
  'REVIEWED_REPLACEMENT_UNSUPPORTED_PLATFORM' \
  "$CURRENT_SCRIPT" --activate --platform gemini --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"

assert_failure_contains \
  'K: --check combined with --replace-reviewed-local-drift is rejected' \
  'REVIEWED_REPLACEMENT_REQUIRES_ACTIVATE' \
  "$CURRENT_SCRIPT" --check --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"

assert_failure_contains \
  'K: --replace-reviewed-local-drift without any mode is rejected' \
  'REVIEWED_REPLACEMENT_REQUIRES_ACTIVATE' \
  "$CURRENT_SCRIPT" --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"

assert_failure_contains \
  'K: a generic --force flag does not exist' \
  'unknown argument: --force' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift --force \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"

[[ "$(live_digest "$SCRATCH_LIVE")" == "$RLD_DIGEST_DRIFT_BASELINE" ]] \
  || fail 'K: a CLI fail-closed case changed the live target'
pass_case 'K: CLI fail-closed matrix left the live target byte-identical'

# F - canonical identity (H/T/M) mismatches are rejected, independent of L.
assert_failure_contains \
  'F: wrong expected-canonical-head is rejected' \
  'REVIEWED_REPLACEMENT_HEAD_MISMATCH' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_ZERO_OBJ" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"

assert_failure_contains \
  'F: wrong expected-canonical-tree is rejected' \
  'REVIEWED_REPLACEMENT_TREE_MISMATCH' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_ZERO_OBJ" \
  --expected-materialization-tree "$RLD_M"

assert_failure_contains \
  'F: wrong expected-materialization-tree is rejected' \
  'REVIEWED_REPLACEMENT_MATERIALIZATION_TREE_MISMATCH' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_ZERO_OBJ"

[[ "$(live_digest "$SCRATCH_LIVE")" == "$RLD_DIGEST_DRIFT_BASELINE" ]] \
  || fail 'F: an H/T/M mismatch case changed the live target'
pass_case 'F: canonical identity (H/T/M) mismatches are rejected with zero live writes'

# B - wrong expected-live-sha256 alone (correct H/T/M) is rejected.
assert_failure_contains \
  'B: wrong expected-live-sha256 is rejected' \
  'REVIEWED_REPLACEMENT_LIVE_BUNDLE_SHA256_MISMATCH' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"
[[ "$(live_digest "$SCRATCH_LIVE")" == "$RLD_DIGEST_DRIFT_BASELINE" ]] \
  || fail 'B: a rejected wrong-L replacement changed the live target'
pass_case 'B: wrong expected-live-sha256 leaves the live target byte-identical'

# D - path/type drift under the live target is rejected outright; a rejected
# activation must leave the offending path exactly as it was (rsync --delete
# would otherwise have removed it), which is stronger proof of zero writes
# than a content digest that free of construction ignores non-regular-files.
ln -s /etc/hosts "$SCRATCH_LIVE/rld-evil-symlink"
assert_failure_contains \
  'D: a symlink under the live target is rejected' \
  'LIVE_BUNDLE_REJECTED: unexpected path/type: rld-evil-symlink' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"
[[ -L "$SCRATCH_LIVE/rld-evil-symlink" ]] \
  || fail 'D: the rejected activation removed the symlink instead of leaving it untouched'
rm -f "$SCRATCH_LIVE/rld-evil-symlink"
pass_case 'D: a symlink under the live target is rejected with zero live writes'

mkfifo "$SCRATCH_LIVE/rld-evil-fifo"
assert_failure_contains \
  'D: a FIFO under the live target is rejected' \
  'LIVE_BUNDLE_REJECTED: unexpected path/type: rld-evil-fifo' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"
[[ -p "$SCRATCH_LIVE/rld-evil-fifo" ]] \
  || fail 'D: the rejected activation removed the FIFO instead of leaving it untouched'
rm -f "$SCRATCH_LIVE/rld-evil-fifo"
pass_case 'D: a FIFO under the live target is rejected with zero live writes'

# E - even an earlier-sorting known file must remain unopened when inventory
# later encounters an unknown ordinary file. Permissions alone are not proof:
# the serializer spy below separately detects any attempted content open.
printf 'unknown fixture content\n' >"$SCRATCH_LIVE/ZZZ-rld-unknown"
assert_failure_contains \
  'E: unknown ordinary path is rejected before hashing' \
  'LIVE_BUNDLE_REJECTED: unknown path: ZZZ-rld-unknown' \
  "$CURRENT_SCRIPT" --activate --platform codex --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M"
[[ -f "$SCRATCH_LIVE/ZZZ-rld-unknown" ]] || fail 'E: unknown file was removed'
rm "$SCRATCH_LIVE/ZZZ-rld-unknown"
pass_case 'E: unknown ordinary path is preserved on refusal'

[[ "$(live_digest "$SCRATCH_LIVE")" == "$RLD_DIGEST_DRIFT_BASELINE" ]] \
  || fail 'D/E: the live target was not restored to the drifted baseline after cleanup'
pass_case 'D/E cleanup restored the drifted baseline exactly'

# Runtime-only fault injection. Every path comes from this suite's scratch
# root. Source the rewritten fixture, retain the real guards, and wrap only
# the boundary at which the fault must occur. No production hook is added.
readonly RLD_RUNNER="$SCRATCH/rld-runner.sh"
cat >"$RLD_RUNNER" <<'RLD_RUNNER_EOF'
#!/usr/bin/env bash
set -euo pipefail
script="$1" scenario="$2" trace="$3" stale="$4"
shift 4
source "$script"
rld_platform=""
rld_prev=""
for rld_arg in "$@"; do
  if [[ "$rld_prev" == --platform ]]; then
    rld_platform="$rld_arg"
  elif [[ "$rld_prev" == --skill ]]; then
    SELECTED_SKILL="$rld_arg"
  fi
  rld_prev="$rld_arg"
done
[[ -n "$rld_platform" ]] || die 'RLD_RUNNER: could not determine --platform from argv'
live="$(platform_live_path "$rld_platform")"
eval "$(declare -f require_reviewed_replacement_canonical_identity | sed '1s/require_reviewed_replacement_canonical_identity/original_identity/')"
eval "$(declare -f classify_platform | sed '1s/classify_platform/original_classify/')"
# Negative control for the Antigravity extension; only this fixture process
# restores the former Method platform gate. Production has no bypass or hook.
if [[ "$scenario" == without-method-antigravity ]]; then
  eval "$(declare -f is_reviewed_replacement_platform | sed '1s/is_reviewed_replacement_platform/original_supported/')"
  is_reviewed_replacement_platform() {
    [[ "$SELECTED_SKILL" != fable-method || "$1" != antigravity ]] || return 1
    original_supported "$@"
  }
fi
identity_calls=0
writes=0
require_reviewed_replacement_canonical_identity() {
  identity_calls=$((identity_calls + 1))
  if [[ "$identity_calls" -eq 2 ]]; then
    case "$scenario" in
      live-change) printf '\nprewrite injected change\n' >>"$live/SKILL.md" ;;
      canonical-ref-change) git -C "$REPOSITORY_ROOT" update-ref refs/remotes/origin/master "$stale" ;;
      source-change) printf '\nsource injected change\n' >>"$REPOSITORY_ROOT/$(platform_materialized_rel "$rld_platform")/SKILL.md" ;;
    esac
  fi
  original_identity "$@"
}
assert_lock_held() {
  if /bin/bash -c 'exec 8>>"$1"; /usr/bin/lockf -s -t 0 8' _ "$ACTIVATION_LOCK" 2>/dev/null; then
    die "FIXTURE_LOCK_NOT_HELD: $1"
  fi
  printf 'LOCK_HELD:%s\n' "$1" >>"$trace"
}
rsync() {
  writes=$((writes + 1))
  printf 'WRITE:%s\n' "$*" >>"$trace"
  assert_lock_held write
  [[ "$scenario" != write-failure ]] || return 23
  if [[ "$scenario" == without-checksum ]]; then
    local -a args=()
    local arg
    for arg in "$@"; do [[ "$arg" == --checksum ]] || args+=("$arg"); done
    /usr/bin/rsync "${args[@]}" || return $?
  else
    /usr/bin/rsync "$@" || return $?
  fi
  if [[ "$scenario" == post-failure ]]; then
    printf '\npostwrite injected change\n' >>"$live/SKILL.md"
  elif [[ "$scenario" == post-mode-failure ]]; then
    /usr/bin/ruby -e 'File.chmod(File.stat(ARGV[0]).mode ^ 0001, ARGV[0])' "$live"
  fi
}
classify_platform() {
  if [[ "$writes" -gt 0 ]]; then assert_lock_held post-check; fi
  original_classify "$@"
}
main "$@"
RLD_RUNNER_EOF

rld_args=(--activate --platform codex --replace-reviewed-local-drift
  --expected-live-sha256 "$RLD_ZERO_L" --expected-canonical-head "$RLD_H"
  --expected-canonical-tree "$RLD_T" --expected-materialization-tree "$RLD_M")
readonly RLD_TRACE="$SCRATCH/rld.trace"
readonly RLD_SOURCE="$LINKED_CURRENT/fable-method/platforms/codex/fable-method"
rld_reset_drift() {
  local source_dir="${1:-$RLD_SOURCE}" live_dir="${2:-$SCRATCH_LIVE}"
  /usr/bin/rsync -a --checksum --delete "$source_dir/" "$live_dir/"
  printf '\nreviewed fixture drift\n' >>"$live_dir/SKILL.md"
  : >"$RLD_TRACE"
}
rld_run_injected() {
  local scenario="$1" expected_l="$2" platform="${3:-codex}" skill="${4:-fable-method}"
  local -a args
  if [[ "$skill" == fable-judge ]]; then
    args=("${judge_args[@]}")
  else
  case "$platform" in
    codex) args=("${rld_args[@]}") ;;
    claude) args=("${RLD_CLAUDE_ARGS[@]}") ;;
    antigravity) args=("${RLD_ANTIGRAVITY_ARGS[@]}") ;;
    *) fail "rld_run_injected: unsupported platform: $platform" ;;
  esac
  fi
  args[5]="$expected_l"
  capture_command /bin/bash "$RLD_RUNNER" "$CURRENT_SCRIPT" "$scenario" "$RLD_TRACE" "$STALE_HEAD" "${args[@]}"
}
rld_assert_refusal() {
  local label="$1" expected="$2"
  [[ "$COMMAND_STATUS" -ne 0 && "$COMMAND_OUTPUT" == *"$expected"* ]] || fail "$label"
  [[ "$COMMAND_OUTPUT" != *'ACTIVATION_RESULT: ACTIVATED'* ]] || fail "$label: false success"
}
rld_assert_no_write() {
  [[ ! -s "$RLD_TRACE" ]] || fail "$1: entered rsync despite prewrite refusal"
  pass_case "$1: zero rsync calls"
}

rld_reset_drift
rld_l="$(rld_probe_observed_l)"
assert_failure_contains 'ordinary LOCAL_DRIFT remains fail-closed' 'LOCAL_DRIFT' \
  "$CURRENT_SCRIPT" --activate --platform codex
rld_before="$(live_digest "$SCRATCH_LIVE")"
rld_run_injected normal "$RLD_ZERO_L"
rld_assert_refusal 'wrong L runtime' 'REVIEWED_REPLACEMENT_LIVE_BUNDLE_SHA256_MISMATCH'
rld_assert_no_write 'wrong L runtime'
[[ "$(live_digest "$SCRATCH_LIVE")" == "$rld_before" ]] || fail 'wrong L runtime changed bytes'

rld_run_injected live-change "$rld_l"
rld_assert_refusal 'C live change before first write' 'REVIEWED_REPLACEMENT_PREWRITE_LIVE_BUNDLE_SHA256_MISMATCH'
rld_assert_no_write 'C live change before first write'
[[ "$(tail -1 "$SCRATCH_LIVE/SKILL.md")" == 'prewrite injected change' ]] || fail 'C injected live change was overwritten'

rld_reset_drift
rld_l="$(rld_probe_observed_l)"
rld_run_injected canonical-ref-change "$rld_l"
rld_assert_refusal 'F canonical ref moved before first write' 'REVIEWED_REPLACEMENT_CANONICAL_REF_MISMATCH'
rld_assert_no_write 'F canonical ref moved before first write'
git -C "$FIXTURE_CANONICAL" update-ref refs/remotes/origin/master "$RLD_H"

# Actual source bytes must stay bound to M, not just the unchanged Git object.
cp "$RLD_SOURCE/SKILL.md" "$SCRATCH/source-skill.saved"
rld_run_injected source-change "$rld_l"
rld_assert_refusal 'F source bytes changed before first write' 'CANONICAL_MATERIALIZATION_DRIFT'
rld_assert_no_write 'F source bytes changed before first write'
cp "$SCRATCH/source-skill.saved" "$RLD_SOURCE/SKILL.md"

# Known file -> directory is a rejected type change, not an allowed deletion.
mv "$SCRATCH_LIVE/SKILL.md" "$SCRATCH/live-skill.saved"
mkdir "$SCRATCH_LIVE/SKILL.md"
rld_run_injected normal "$rld_l"
rld_assert_refusal 'D known file changed to directory' 'unexpected known path type: SKILL.md'
rld_assert_no_write 'D known file changed to directory'
rmdir "$SCRATCH_LIVE/SKILL.md"
mv "$SCRATCH/live-skill.saved" "$SCRATCH_LIVE/SKILL.md"

rld_reset_drift
rld_l="$(rld_probe_observed_l)"
rld_run_injected write-failure "$rld_l"
rld_assert_refusal 'I write failure' 'REVIEWED_REPLACEMENT_WRITE_FAILED'
grep -q '^WRITE:' "$RLD_TRACE" || fail 'I write failure was not injected'
pass_case 'I nonzero rsync cannot report success'

rld_reset_drift
rld_l="$(rld_probe_observed_l)"
rld_run_injected post-failure "$rld_l"
rld_assert_refusal 'J post-check failure' 'ACTIVATION_VERIFICATION_FAILED'
grep -q '^LOCK_HELD:post-check$' "$RLD_TRACE" || fail 'J post-check lock was not observed'
pass_case 'J real post-write LOCAL_DRIFT fails with lock held and no false success'

rld_reset_drift
rld_l="$(rld_probe_observed_l)"
rld_run_injected post-mode-failure "$rld_l"
rld_assert_refusal 'J post-check root mode mismatch' 'ACTIVATION_VERIFICATION_FAILED: full bundle differs'
pass_case 'J full bundle post-check rejects permission drift invisible to Git file-mode classification'

rld_reset_drift
rld_l="$(rld_probe_observed_l)"
rld_run_injected normal "$rld_l"
[[ "$COMMAND_STATUS" -eq 0 && "$COMMAND_OUTPUT" == *'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION'* ]] || fail 'A reviewed drift happy path'
grep -q '^LOCK_HELD:write$' "$RLD_TRACE" || fail 'A write lock not observed'
grep -q '^LOCK_HELD:post-check$' "$RLD_TRACE" || fail 'A post-check lock not observed'
[[ "$(live_digest "$SCRATCH_LIVE")" == "$(live_digest "$RLD_SOURCE")" ]] || fail 'A full bundle bytes differ'
pass_case 'A reviewed LOCAL_DRIFT replacement succeeds with lock through exact post-check'

# H - real rsync quick-check collision. Only bytes differ; size and nanosecond
# mtime equal source. Removing --checksum in the disposable wrapper is the
# falsifying mutation, which must fail the exact same production post-check.
rld_same_metadata_drift() {
  /usr/bin/rsync -a --checksum --delete "$RLD_SOURCE/" "$SCRATCH_LIVE/"
  /usr/bin/ruby -e '
    source, target = ARGV
    stat = File.stat(source)
    bytes = File.binread(source)
    bytes.setbyte(0, bytes.getbyte(0) ^ 1)
    File.binwrite(target, bytes)
    # macOS File.utime may round arbitrary subsecond mtimes. Give both
    # fixture files the same exact whole-second timestamp before comparing.
    stamp = Time.at(1700000000)
    File.utime(stamp, stamp, source, target)
    raise "size/mtime fixture mismatch" unless File.size(target) == stat.size && File.mtime(target) == File.mtime(source)
  ' "$RLD_SOURCE/SKILL.md" "$SCRATCH_LIVE/SKILL.md"
  : >"$RLD_TRACE"
}
rld_same_metadata_drift
rld_l="$(rld_probe_observed_l)"
rld_run_injected without-checksum "$rld_l"
rld_assert_refusal 'H checksum falsification' 'ACTIVATION_VERIFICATION_FAILED'
pass_case 'H falsifiability: removing checksum leaves equal-size/mtime drift and fails post-check'
rld_same_metadata_drift
rld_l="$(rld_probe_observed_l)"
# Exercise the unwrapped executable for the passing checksum case.
rld_actual_args=("${rld_args[@]}")
rld_actual_args[5]="$rld_l"
assert_success_contains 'H real CLI checksum replacement repairs equal-size/mtime byte drift' \
  'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION' "$CURRENT_SCRIPT" "${rld_actual_args[@]}"
cmp "$RLD_SOURCE/SKILL.md" "$SCRATCH_LIVE/SKILL.md" || fail 'H checksum replacement left stale bytes'

# Independent serialization vector includes root mode, nested-directory mode,
# two contents and raw byte sort order. Permissions and non-SKILL content are
# independently changed and must change L. This oracle does not call production.
readonly RLD_VECTOR="$SCRATCH/serialization-vector"
mkdir -p "$RLD_VECTOR/references"
printf 'A' >"$RLD_VECTOR/SKILL.md"
printf 'B' >"$RLD_VECTOR/references/operational-gates.md"
chmod 750 "$RLD_VECTOR"
chmod 700 "$RLD_VECTOR/references"
chmod 640 "$RLD_VECTOR/SKILL.md"
chmod 600 "$RLD_VECTOR/references/operational-gates.md"
rld_vector_expected="$(/usr/bin/ruby -rdigest -e '
  records = [["D", ".", 0750, nil], ["F", "SKILL.md", 0640, "A"],
             ["D", "references", 0700, nil], ["F", "references/operational-gates.md", 0600, "B"]]
  bytes = records.map { |type, path, mode, content|
    type + [path.bytesize].pack("N") + path + [mode].pack("N") + (content ? Digest::SHA256.digest(content) : "\x00" * 32)
  }.join
  puts Digest::SHA256.hexdigest(bytes)
')"
rld_serialize() {
  /bin/bash -c 'source "$1"; compute_live_bundle_sha256 "$2" "$3"' _ "$CURRENT_SCRIPT" "$1" "${2:-codex}"
}
[[ "$(rld_serialize "$RLD_VECTOR")" == "$rld_vector_expected" ]] || fail 'serialization independent vector mismatch'
pass_case 'serialization V1 independent full-record vector'
chmod 751 "$RLD_VECTOR"
[[ "$(rld_serialize "$RLD_VECTOR")" != "$rld_vector_expected" ]] || fail 'root mode not hashed'
chmod 750 "$RLD_VECTOR"
printf C >"$RLD_VECTOR/references/operational-gates.md"
[[ "$(rld_serialize "$RLD_VECTOR")" != "$rld_vector_expected" ]] || fail 'non-SKILL content not hashed'
printf B >"$RLD_VECTOR/references/operational-gates.md"
[[ "$(rld_serialize "$RLD_VECTOR")" == "$rld_vector_expected" ]] || fail 'serialization vector restore mismatch'
pass_case 'serialization binds root permissions and non-SKILL bytes'

# Ruby seam runs only inside the sourced fixture subprocess. The read spy
# fails on ANY content open; with a late unknown path, the expected error must
# still be inventory rejection. A second mode changes a known file to a
# symlink between inventory and content passes and verifies no target read.
readonly RLD_SERIALIZER_SPY="$SCRATCH/serializer-spy.rb"
cat >"$RLD_SERIALIZER_SPY" <<'RLD_SPY_EOF'
module SerializerSpy
  def open(path, *args, &block)
    if path.to_s.start_with?(ENV.fetch("RLD_SPY_ROOT") + "/")
      raise "FIXTURE_CONTENT_OPENED"
    end
    super
  end
  def lstat(path)
    if ENV["RLD_SPY_MODE"] == "type-change" && path == ENV.fetch("RLD_SPY_ROOT")
      @root_calls = (@root_calls || 0) + 1
      if @root_calls == 2
        target = File.join(path, "SKILL.md")
        File.unlink(target)
        File.symlink(ENV.fetch("RLD_SPY_DECOY"), target)
      end
    end
    super
  end
end
File.singleton_class.prepend(SerializerSpy)
RLD_SPY_EOF
printf 'scratch decoy' >"$SCRATCH/decoy"
printf 'must not read' >"$RLD_VECTOR/ZZZ-unknown"
assert_failure_contains 'E runtime spy: unknown path rejected before any content open' \
  'LIVE_BUNDLE_REJECTED: unknown path: ZZZ-unknown' \
  env RUBYOPT="-r$RLD_SERIALIZER_SPY" RLD_SPY_ROOT="$RLD_VECTOR" RLD_SPY_MODE=unknown \
  /bin/bash -c 'source "$1"; compute_live_bundle_sha256 "$2" codex' _ "$CURRENT_SCRIPT" "$RLD_VECTOR"
[[ "$COMMAND_OUTPUT" != *FIXTURE_CONTENT_OPENED* ]] || fail 'E read occurred before unknown path rejection'
rm "$RLD_VECTOR/ZZZ-unknown"
assert_failure_contains 'D runtime spy: mid-scan type change rejects before content open' \
  'LIVE_BUNDLE_REJECTED: path/type changed during scan:' \
  env RUBYOPT="-r$RLD_SERIALIZER_SPY" RLD_SPY_ROOT="$RLD_VECTOR" RLD_SPY_MODE=type-change RLD_SPY_DECOY="$SCRATCH/decoy" \
  /bin/bash -c 'source "$1"; compute_live_bundle_sha256 "$2" codex' _ "$CURRENT_SCRIPT" "$RLD_VECTOR"
[[ "$COMMAND_OUTPUT" != *FIXTURE_CONTENT_OPENED* ]] || fail 'D type change opened content'
[[ -L "$RLD_VECTOR/SKILL.md" ]] || fail 'D type-change injection did not happen'

# Every identity flag: missing, duplicate, malformed and missing value. All
# invoke the same CLI dispatcher with a write spy and assert zero rsync calls.
for rld_index in 4 6 8 10; do
  for rld_variant in missing duplicate malformed no-value; do
    rld_cli_args=("${rld_args[@]}")
    case "$rld_variant" in
      missing) unset 'rld_cli_args[rld_index]' 'rld_cli_args[rld_index+1]' ;;
      duplicate) rld_cli_args+=("${rld_args[$rld_index]}" "${rld_args[$((rld_index + 1))]}") ;;
      malformed) rld_cli_args[$((rld_index + 1))]='not-hex' ;;
      no-value)
        unset 'rld_cli_args[rld_index]' 'rld_cli_args[rld_index+1]'
        rld_cli_args+=("${rld_args[$rld_index]}")
        ;;
    esac
    : >"$RLD_TRACE"
    capture_command /bin/bash "$RLD_RUNNER" "$CURRENT_SCRIPT" normal "$RLD_TRACE" "$STALE_HEAD" "${rld_cli_args[@]}"
    rld_assert_refusal "CLI $rld_variant ${rld_args[$rld_index]}" 'ERROR:'
    rld_assert_no_write "CLI $rld_variant ${rld_args[$rld_index]}"
  done
done
assert_failure_contains 'identity flags without replacement mode fail closed' \
  'REVIEWED_REPLACEMENT_IDENTITY_WITHOUT_MODE' "$CURRENT_SCRIPT" --activate --platform codex --expected-live-sha256 "$RLD_ZERO_L"

[[ "$(real_lock_fingerprint)" == "$REAL_LOCK_BEFORE" ]] || fail 'replacement cases touched the real activation lock'
pass_case 'all replacement fixtures stayed isolated; real activation lock unchanged'

# --- Reviewed Claude local-drift replacement mode (platform-aware extension)
#
# Reuses RLD_H/RLD_T (the commit/tree identity is platform-independent) and
# the same fixture worktree; only the platform, its live target, its source
# materialization, and its M value differ from the Codex section above.
readonly RLD_CLAUDE_M="$(git -C "$FIXTURE_CANONICAL" rev-parse "${CURRENT_HEAD}:fable-method/platforms/claude/fable-method")"
readonly SCRATCH_LIVE_CLAUDE="${FIXTURE_HOME}/.claude/skills/fable-method"
readonly RLD_CLAUDE_SOURCE="${LINKED_CURRENT}/fable-method/platforms/claude/fable-method"
RLD_CLAUDE_ARGS=(--activate --platform claude --replace-reviewed-local-drift
  --expected-live-sha256 "$RLD_ZERO_L" --expected-canonical-head "$RLD_H"
  --expected-canonical-tree "$RLD_T" --expected-materialization-tree "$RLD_CLAUDE_M")

[[ ! -e "$SCRATCH_LIVE_CLAUDE" ]] || fail 'Claude RLD precondition: the scratch Claude live target already exists'
mkdir -p "$SCRATCH_LIVE_CLAUDE"
rld_reset_drift "$RLD_CLAUDE_SOURCE" "$SCRATCH_LIVE_CLAUDE"
readonly RLD_CLAUDE_DIGEST_DRIFT_BASELINE="$(live_digest "$SCRATCH_LIVE_CLAUDE")"

assert_failure_contains \
  'ordinary Claude --activate continues refusing LOCAL_DRIFT' \
  'LOCAL_DRIFT' \
  "$CURRENT_SCRIPT" --activate --platform claude
[[ "$(live_digest "$SCRATCH_LIVE_CLAUDE")" == "$RLD_CLAUDE_DIGEST_DRIFT_BASELINE" ]] \
  || fail 'ordinary Claude --activate LOCAL_DRIFT refusal changed the live target'
pass_case 'Claude: ordinary --activate refuses LOCAL_DRIFT and leaves it byte-identical'

rld_claude_l="$(rld_probe_observed_l claude "$RLD_CLAUDE_M")"

assert_failure_contains \
  'Claude: wrong expected-live-sha256 is rejected' \
  'REVIEWED_REPLACEMENT_LIVE_BUNDLE_SHA256_MISMATCH' \
  "$CURRENT_SCRIPT" --activate --platform claude --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_CLAUDE_M"

assert_failure_contains \
  'Claude: wrong expected-canonical-head is rejected' \
  'REVIEWED_REPLACEMENT_HEAD_MISMATCH' \
  "$CURRENT_SCRIPT" --activate --platform claude --replace-reviewed-local-drift \
  --expected-live-sha256 "$rld_claude_l" \
  --expected-canonical-head "$RLD_ZERO_OBJ" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_CLAUDE_M"

assert_failure_contains \
  'Claude: wrong expected-canonical-tree is rejected' \
  'REVIEWED_REPLACEMENT_TREE_MISMATCH' \
  "$CURRENT_SCRIPT" --activate --platform claude --replace-reviewed-local-drift \
  --expected-live-sha256 "$rld_claude_l" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_ZERO_OBJ" \
  --expected-materialization-tree "$RLD_CLAUDE_M"

assert_failure_contains \
  'Claude: wrong expected-materialization-tree is rejected' \
  'REVIEWED_REPLACEMENT_MATERIALIZATION_TREE_MISMATCH' \
  "$CURRENT_SCRIPT" --activate --platform claude --replace-reviewed-local-drift \
  --expected-live-sha256 "$rld_claude_l" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_ZERO_OBJ"

[[ "$(live_digest "$SCRATCH_LIVE_CLAUDE")" == "$RLD_CLAUDE_DIGEST_DRIFT_BASELINE" ]] \
  || fail 'Claude: an L/H/T/M mismatch case changed the live target'
pass_case 'Claude: L/H/T/M mismatches are each rejected with zero live writes'

printf 'must not be trusted\n' >"$SCRATCH_LIVE_CLAUDE/ZZZ-claude-unknown"
assert_failure_contains \
  'Claude: unknown ordinary path is rejected before content trust/write' \
  'LIVE_BUNDLE_REJECTED: unknown path: ZZZ-claude-unknown' \
  "$CURRENT_SCRIPT" --activate --platform claude --replace-reviewed-local-drift \
  --expected-live-sha256 "$rld_claude_l" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_CLAUDE_M"
[[ -f "$SCRATCH_LIVE_CLAUDE/ZZZ-claude-unknown" ]] || fail 'Claude: unknown file was removed'
rm "$SCRATCH_LIVE_CLAUDE/ZZZ-claude-unknown"

ln -s /etc/hosts "$SCRATCH_LIVE_CLAUDE/rld-claude-evil-symlink"
assert_failure_contains \
  'Claude: a symlink under the live target is rejected' \
  'LIVE_BUNDLE_REJECTED: unexpected path/type: rld-claude-evil-symlink' \
  "$CURRENT_SCRIPT" --activate --platform claude --replace-reviewed-local-drift \
  --expected-live-sha256 "$rld_claude_l" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_CLAUDE_M"
[[ -L "$SCRATCH_LIVE_CLAUDE/rld-claude-evil-symlink" ]] \
  || fail 'Claude: the rejected activation removed the symlink instead of leaving it untouched'
rm -f "$SCRATCH_LIVE_CLAUDE/rld-claude-evil-symlink"

mkfifo "$SCRATCH_LIVE_CLAUDE/rld-claude-evil-fifo"
assert_failure_contains \
  'Claude: a FIFO (special file) under the live target is rejected' \
  'LIVE_BUNDLE_REJECTED: unexpected path/type: rld-claude-evil-fifo' \
  "$CURRENT_SCRIPT" --activate --platform claude --replace-reviewed-local-drift \
  --expected-live-sha256 "$rld_claude_l" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_CLAUDE_M"
[[ -p "$SCRATCH_LIVE_CLAUDE/rld-claude-evil-fifo" ]] \
  || fail 'Claude: the rejected activation removed the FIFO instead of leaving it untouched'
rm -f "$SCRATCH_LIVE_CLAUDE/rld-claude-evil-fifo"

mv "$SCRATCH_LIVE_CLAUDE/SKILL.md" "$SCRATCH/claude-live-skill.saved"
mkdir "$SCRATCH_LIVE_CLAUDE/SKILL.md"
assert_failure_contains \
  'Claude: a known file changed to a directory (wrong type) is rejected' \
  'unexpected known path type: SKILL.md' \
  "$CURRENT_SCRIPT" --activate --platform claude --replace-reviewed-local-drift \
  --expected-live-sha256 "$rld_claude_l" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_CLAUDE_M"
rmdir "$SCRATCH_LIVE_CLAUDE/SKILL.md"
mv "$SCRATCH/claude-live-skill.saved" "$SCRATCH_LIVE_CLAUDE/SKILL.md"

[[ "$(live_digest "$SCRATCH_LIVE_CLAUDE")" == "$RLD_CLAUDE_DIGEST_DRIFT_BASELINE" ]] \
  || fail 'Claude: unknown-path/symlink/FIFO/type-change rejection left the live target changed'
pass_case 'Claude: unknown path, symlink, FIFO, and known-file-to-directory drift are each rejected with zero live writes'

rld_run_injected live-change "$rld_claude_l" claude
rld_assert_refusal 'Claude: prewrite live identity change is rejected' 'REVIEWED_REPLACEMENT_PREWRITE_LIVE_BUNDLE_SHA256_MISMATCH'
rld_assert_no_write 'Claude: prewrite live identity change is rejected'
[[ "$(tail -1 "$SCRATCH_LIVE_CLAUDE/SKILL.md")" == 'prewrite injected change' ]] \
  || fail 'Claude: injected prewrite live change was overwritten'

rld_reset_drift "$RLD_CLAUDE_SOURCE" "$SCRATCH_LIVE_CLAUDE"
rld_claude_l="$(rld_probe_observed_l claude "$RLD_CLAUDE_M")"
rld_run_injected canonical-ref-change "$rld_claude_l" claude
rld_assert_refusal 'Claude: canonical ref moved before first write' 'REVIEWED_REPLACEMENT_CANONICAL_REF_MISMATCH'
rld_assert_no_write 'Claude: canonical ref moved before first write'
git -C "$FIXTURE_CANONICAL" update-ref refs/remotes/origin/master "$RLD_H"

cp "$RLD_CLAUDE_SOURCE/SKILL.md" "$SCRATCH/claude-source-skill.saved"
rld_run_injected source-change "$rld_claude_l" claude
rld_assert_refusal 'Claude: source bytes changed before first write' 'CANONICAL_MATERIALIZATION_DRIFT'
rld_assert_no_write 'Claude: source bytes changed before first write'
cp "$SCRATCH/claude-source-skill.saved" "$RLD_CLAUDE_SOURCE/SKILL.md"
pass_case 'Claude: prewrite live/canonical-ref/source identity changes are each rejected with zero writes'

rld_reset_drift "$RLD_CLAUDE_SOURCE" "$SCRATCH_LIVE_CLAUDE"
rld_claude_l="$(rld_probe_observed_l claude "$RLD_CLAUDE_M")"
rld_run_injected normal "$rld_claude_l" claude
[[ "$COMMAND_STATUS" -eq 0 && "$COMMAND_OUTPUT" == *'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION'* ]] \
  || fail 'Claude: reviewed drift happy path did not report exact current materialization'
grep -q '^LOCK_HELD:write$' "$RLD_TRACE" || fail 'Claude: A write lock not observed'
grep -q '^LOCK_HELD:post-check$' "$RLD_TRACE" || fail 'Claude: A post-check lock not observed'
[[ "$(live_digest "$SCRATCH_LIVE_CLAUDE")" == "$(live_digest "$RLD_CLAUDE_SOURCE")" ]] \
  || fail 'Claude: A full bundle bytes differ from canonical materialization'
pass_case 'Claude: reviewed LOCAL_DRIFT replacement succeeds and the final bundle equals canonical materialization'

[[ "$(real_lock_fingerprint)" == "$REAL_LOCK_BEFORE" ]] \
  || fail 'Claude replacement cases touched the real activation lock'
pass_case 'Claude: all replacement fixtures stayed isolated; real activation lock unchanged'

# Schema-v2 pair regressions; every live path is read from the rewritten
# fixture manifest, and every mutation remains in this suite's scratch root.
for skill in fable-method fable-judge; do
  pair_platforms=(codex claude gemini antigravity)
  for platform in "${pair_platforms[@]}"; do
    pair_live="$(/bin/bash -c 'source "$1"; SELECTED_SKILL="$2"; platform_live_path "$3"' _ "$CURRENT_SCRIPT" "$skill" "$platform")"
    pair_source="$LINKED_CURRENT/fable-method/platforms/$platform/$skill"
    case "$pair_live" in "$FIXTURE_HOME"/*) ;; *) fail 'pair live escaped fixture home' ;; esac
    mkdir -p "$(dirname "$pair_live")"
    if [[ ! -e "$pair_live" ]]; then
      assert_success_contains "$skill/$platform ABSENT" 'STATE: ABSENT' "$CURRENT_SCRIPT" --check --skill "$skill" --platform "$platform"
      assert_success_contains "$skill/$platform exact-pair activation" 'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION' "$CURRENT_SCRIPT" --activate --skill "$skill" --platform "$platform"
    fi
    assert_success_contains "$skill/$platform CURRENT" 'STATE: EXACT_CURRENT_MATERIALIZATION' "$CURRENT_SCRIPT" --check --skill "$skill" --platform "$platform"
    /usr/bin/rsync -a --checksum --delete "$LINKED_STALE/fable-method/platforms/$platform/$skill/" "$pair_live/"
    assert_success_contains "$skill/$platform HISTORICAL" 'STATE: EXACT_HISTORICAL_MATERIALIZATION' "$CURRENT_SCRIPT" --check --skill "$skill" --platform "$platform"
    assert_success_contains "$skill/$platform historical activation" 'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION' "$CURRENT_SCRIPT" --activate --skill "$skill" --platform "$platform"
  done
done

# --- Reviewed Antigravity Method replacement ------------------------------
# The pair matrix above populated every Method/Judge target. Reuse its exact
# manifest-owned destinations and the generic identity/lock/fault-injection
# harness; serializer/type/CLI invariants retain their existing generic tests.
readonly RLD_ANTIGRAVITY_M="$(git -C "$FIXTURE_CANONICAL" rev-parse "${CURRENT_HEAD}:fable-method/platforms/antigravity/fable-method")"
readonly SCRATCH_LIVE_ANTIGRAVITY="${FIXTURE_HOME}/.gemini/config/skills/fable-method"
readonly RLD_ANTIGRAVITY_SOURCE="${LINKED_CURRENT}/fable-method/platforms/antigravity/fable-method"
RLD_ANTIGRAVITY_ARGS=(--activate --platform antigravity --replace-reviewed-local-drift
  --expected-live-sha256 "$RLD_ZERO_L" --expected-canonical-head "$RLD_H"
  --expected-canonical-tree "$RLD_T" --expected-materialization-tree "$RLD_ANTIGRAVITY_M"
  --skill fable-method)

# Full V1 identities bind every sibling's paths, bytes, and permissions,
# including all three other Method platforms and all four Judge targets.
antigravity_sibling_bundles() {
  /bin/bash -c '
    source "$1"
    for SELECTED_SKILL in fable-method fable-judge; do
      verify_manifest_paths
      for platform in codex claude gemini antigravity; do
        [[ "$SELECTED_SKILL/$platform" != fable-method/antigravity ]] || continue
        printf "%s/%s: " "$SELECTED_SKILL" "$platform"
        compute_live_bundle_sha256 "$(platform_live_path "$platform")" "$platform" || exit 1
      done
    done
  ' _ "$CURRENT_SCRIPT"
}
RLD_ANTIGRAVITY_SIBLINGS_BEFORE="$(antigravity_sibling_bundles)"
readonly RLD_ANTIGRAVITY_SIBLINGS_BEFORE
rld_reset_drift "$RLD_ANTIGRAVITY_SOURCE" "$SCRATCH_LIVE_ANTIGRAVITY"
rld_antigravity_l="$(rld_probe_observed_l antigravity "$RLD_ANTIGRAVITY_M")"
RLD_ANTIGRAVITY_DRIFT="$(rld_serialize "$SCRATCH_LIVE_ANTIGRAVITY" antigravity)"
readonly RLD_ANTIGRAVITY_DRIFT

capture_command /bin/bash "$RLD_RUNNER" "$CURRENT_SCRIPT" normal "$RLD_TRACE" "$STALE_HEAD" \
  --activate --skill fable-method --platform antigravity
rld_assert_refusal 'Antigravity: ordinary activation refuses LOCAL_DRIFT' 'state: LOCAL_DRIFT'
rld_assert_no_write 'Antigravity: ordinary activation refuses LOCAL_DRIFT'
[[ "$(rld_serialize "$SCRATCH_LIVE_ANTIGRAVITY" antigravity)" == "$RLD_ANTIGRAVITY_DRIFT" ]] \
  || fail 'Antigravity: ordinary refusal changed the live bundle'

for index in 5 7 9 11; do
  args=("${RLD_ANTIGRAVITY_ARGS[@]}")
  args[5]="$rld_antigravity_l"
  case "$index" in
    5) args[$index]="$RLD_ZERO_L"; expected=REVIEWED_REPLACEMENT_LIVE_BUNDLE_SHA256_MISMATCH ;;
    7) args[$index]="$RLD_ZERO_OBJ"; expected=REVIEWED_REPLACEMENT_HEAD_MISMATCH ;;
    9) args[$index]="$RLD_ZERO_OBJ"; expected=REVIEWED_REPLACEMENT_TREE_MISMATCH ;;
    11) args[$index]="$RLD_M"; expected=REVIEWED_REPLACEMENT_MATERIALIZATION_TREE_MISMATCH ;;
  esac
  : >"$RLD_TRACE"
  capture_command /bin/bash "$RLD_RUNNER" "$CURRENT_SCRIPT" normal "$RLD_TRACE" "$STALE_HEAD" "${args[@]}"
  rld_assert_refusal "Antigravity: wrong ${args[$((index - 1))]}" "$expected"
  rld_assert_no_write "Antigravity: wrong ${args[$((index - 1))]}"
  [[ "$(rld_serialize "$SCRATCH_LIVE_ANTIGRAVITY" antigravity)" == "$RLD_ANTIGRAVITY_DRIFT" ]] \
    || fail "Antigravity: identity $index refusal changed the live bundle"
done

# Its extra .gemini/config path component must retain the shared path guard.
mv "$FIXTURE_HOME/.gemini/config" "$SCRATCH/antigravity-config.saved"
ln -s "$SCRATCH/antigravity-config.saved" "$FIXTURE_HOME/.gemini/config"
rld_run_injected normal "$rld_antigravity_l" antigravity
rld_assert_refusal 'Antigravity: symlink parent' 'ACTIVATION_PARENT_NOT_READY'
rld_assert_no_write 'Antigravity: symlink parent'
[[ -L "$FIXTURE_HOME/.gemini/config" ]] || fail 'Antigravity: symlink parent was replaced'
rm "$FIXTURE_HOME/.gemini/config"
mv "$SCRATCH/antigravity-config.saved" "$FIXTURE_HOME/.gemini/config"

rld_run_injected live-change "$rld_antigravity_l" antigravity
rld_assert_refusal 'Antigravity: live changes before first write' 'REVIEWED_REPLACEMENT_PREWRITE_LIVE_BUNDLE_SHA256_MISMATCH'
rld_assert_no_write 'Antigravity: live changes before first write'
[[ "$(tail -1 "$SCRATCH_LIVE_ANTIGRAVITY/SKILL.md")" == 'prewrite injected change' ]] \
  || fail 'Antigravity: prewrite live change was overwritten'

rld_reset_drift "$RLD_ANTIGRAVITY_SOURCE" "$SCRATCH_LIVE_ANTIGRAVITY"
rld_antigravity_l="$(rld_probe_observed_l antigravity "$RLD_ANTIGRAVITY_M")"
rld_run_injected canonical-ref-change "$rld_antigravity_l" antigravity
rld_assert_refusal 'Antigravity: canonical ref changes before first write' 'REVIEWED_REPLACEMENT_CANONICAL_REF_MISMATCH'
rld_assert_no_write 'Antigravity: canonical ref changes before first write'
git -C "$FIXTURE_CANONICAL" update-ref refs/remotes/origin/master "$RLD_H"
cp "$RLD_ANTIGRAVITY_SOURCE/SKILL.md" "$SCRATCH/antigravity-source.saved"
rld_run_injected source-change "$rld_antigravity_l" antigravity
rld_assert_refusal 'Antigravity: source changes before first write' 'CANONICAL_MATERIALIZATION_DRIFT'
rld_assert_no_write 'Antigravity: source changes before first write'
cp "$SCRATCH/antigravity-source.saved" "$RLD_ANTIGRAVITY_SOURCE/SKILL.md"

hold_activation_lock "$FIXTURE_LOCK" "$HOLDER_READY" "$HOLDER_RELEASE" &
HOLDER_PID=$!
read -r HOLDER_SIGNAL <"$HOLDER_READY"
[[ "$HOLDER_SIGNAL" == HOLDER_LOCK_ACQUIRED ]] || fail 'Antigravity: lock holder failed'
rld_run_injected normal "$rld_antigravity_l" antigravity
rld_assert_refusal 'Antigravity: activation lock contention' 'ACTIVATION_LOCK_BUSY'
rld_assert_no_write 'Antigravity: activation lock contention'
[[ "$COMMAND_OUTPUT" == *"lock: ${FIXTURE_LOCK}"* ]] || fail 'Antigravity: wrong activation lock'
[[ "$(rld_serialize "$SCRATCH_LIVE_ANTIGRAVITY" antigravity)" == "$rld_antigravity_l" ]] \
  || fail 'Antigravity: lock contention changed the live bundle'
printf 'release\n' >"$HOLDER_RELEASE"
wait "$HOLDER_PID"
HOLDER_PID=''

rld_run_injected normal "$rld_antigravity_l" antigravity
[[ "$COMMAND_STATUS" -eq 0 && "$COMMAND_OUTPUT" == *'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION'* ]] \
  || fail 'Antigravity: reviewed replacement with correct exact bindings failed'
[[ "$(grep -c '^WRITE:' "$RLD_TRACE")" -eq 1 ]] || fail 'Antigravity: expected exactly one write'
grep -Fxq "WRITE:-a --checksum --delete $RLD_ANTIGRAVITY_SOURCE/ $SCRATCH_LIVE_ANTIGRAVITY/" "$RLD_TRACE" \
  || fail 'Antigravity: write source/destination differs from its manifest-owned pair'
grep -q '^LOCK_HELD:write$' "$RLD_TRACE" || fail 'Antigravity: write lock not observed'
grep -q '^LOCK_HELD:post-check$' "$RLD_TRACE" || fail 'Antigravity: post-check lock not observed'
[[ "$(rld_serialize "$SCRATCH_LIVE_ANTIGRAVITY" antigravity)" == "$(rld_serialize "$RLD_ANTIGRAVITY_SOURCE" antigravity)" ]] \
  || fail 'Antigravity: final full bundle differs from current materialization'
pass_case 'Antigravity: exact reviewed replacement succeeds with the lock held through post-check'

# Green -> former unsupported-platform gate -> green, using the same bindings.
rld_reset_drift "$RLD_ANTIGRAVITY_SOURCE" "$SCRATCH_LIVE_ANTIGRAVITY"
rld_antigravity_l="$(rld_probe_observed_l antigravity "$RLD_ANTIGRAVITY_M")"
rld_run_injected without-method-antigravity "$rld_antigravity_l" antigravity
rld_assert_refusal 'Antigravity: former platform gate negative control' 'REVIEWED_REPLACEMENT_UNSUPPORTED_PLATFORM'
rld_assert_no_write 'Antigravity: former platform gate negative control'
args=("${RLD_ANTIGRAVITY_ARGS[@]}")
args[5]="$rld_antigravity_l"
assert_success_contains 'Antigravity: restored unwrapped CLI replaces reviewed LOCAL_DRIFT' \
  'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION' "$CURRENT_SCRIPT" "${args[@]}"
assert_success_contains 'Antigravity: post-success check is exact current materialization' \
  'STATE: EXACT_CURRENT_MATERIALIZATION' "$CURRENT_SCRIPT" --check --skill fable-method --platform antigravity
[[ "$(rld_serialize "$SCRATCH_LIVE_ANTIGRAVITY" antigravity)" == "$(rld_serialize "$RLD_ANTIGRAVITY_SOURCE" antigravity)" ]] \
  || fail 'Antigravity: unwrapped CLI left a different full bundle'
[[ "$(antigravity_sibling_bundles)" == "$RLD_ANTIGRAVITY_SIBLINGS_BEFORE" ]] \
  || fail 'Antigravity: another Method or Judge live target changed'
pass_case 'Antigravity: Codex/Claude/Gemini Method and all Judge bundles stayed identical'

capture_command "$CURRENT_SCRIPT" --help
[[ "$COMMAND_STATUS" -eq 0 ]] || fail 'help did not exit successfully'
for expected in 'Method: codex|claude|antigravity.' 'Judge: codex|claude|gemini.' \
  'LOCAL_DRIFT' 'LIVE_BUNDLE_SHA256_SERIALIZATION_V1' \
  '--expected-live-sha256' '--expected-canonical-head' '--expected-canonical-tree' '--expected-materialization-tree' \
  'is required exactly' 'immediately before the first' 'does not perform semantic review'; do
  [[ "$COMMAND_OUTPUT" == *"$expected"* ]] || fail "help omitted: $expected"
done
pass_case 'help names the skill-specific platforms, fail-closed path, and exact identity contract'
capture_command "$CURRENT_SCRIPT"
[[ "$COMMAND_STATUS" -eq 2 && "$COMMAND_OUTPUT" == *'Method reviewed replacement: codex|claude|antigravity.'* \
  && "$COMMAND_OUTPUT" == *'(reviewed replacement: codex|claude|gemini)'* \
  && "$COMMAND_OUTPUT" == *'Ordinary activation refuses LOCAL_DRIFT.'* \
  && "$COMMAND_OUTPUT" == *'exact identity bindings, each exactly once'* ]] || fail 'usage misstated reviewed replacement semantics'
pass_case 'usage states Method/Judge platform limits and ordinary LOCAL_DRIFT refusal'

assert_failure_contains 'unknown Judge platform rejected' 'unknown platform' "$CURRENT_SCRIPT" --check --skill fable-judge --platform unknown
assert_failure_contains 'unknown skill rejected' 'unknown skill' "$CURRENT_SCRIPT" --check --skill all
assert_failure_contains 'duplicate skill rejected' '--skill may be given at most once' "$CURRENT_SCRIPT" --check --skill fable-judge --skill fable-judge
assert_failure_contains 'Judge activation requires exact platform' 'requires exactly one --platform' "$CURRENT_SCRIPT" --activate --skill fable-judge
assert_failure_contains 'duplicate platform rejected' '--platform may be given at most once' "$CURRENT_SCRIPT" --check --skill fable-judge --platform codex --platform codex
assert_failure_contains \
  'Judge --replace-reviewed-local-drift with platform antigravity is rejected' \
  'REVIEWED_REPLACEMENT_UNSUPPORTED_PLATFORM' \
  "$CURRENT_SCRIPT" --activate --platform antigravity --replace-reviewed-local-drift \
  --expected-live-sha256 "$RLD_ZERO_L" \
  --expected-canonical-head "$RLD_H" \
  --expected-canonical-tree "$RLD_T" \
  --expected-materialization-tree "$RLD_M" \
  --skill fable-judge
capture_command "$CURRENT_SCRIPT" --check
[[ "$COMMAND_STATUS" -eq 0 && "$COMMAND_OUTPUT" != *'SKILL: fable-judge'* ]] || fail 'default activation check includes Judge'
legacy_output="$COMMAND_OUTPUT"
capture_command "$CURRENT_SCRIPT" --check --skill fable-method
[[ "$COMMAND_STATUS" -eq 0 && "$COMMAND_OUTPUT" == "$legacy_output" ]] || fail 'explicit Method activation check differs from legacy'
pass_case 'activation legacy default equals explicit Method-only check'

for platform in codex claude gemini; do
  judge_live="$(/bin/bash -c 'source "$1"; SELECTED_SKILL=fable-judge; platform_live_path "$2"' _ "$CURRENT_SCRIPT" "$platform")"
  judge_source="$LINKED_CURRENT/fable-method/platforms/$platform/fable-judge"
  judge_m="$(git -C "$LINKED_CURRENT" rev-parse "$RLD_H:fable-method/platforms/$platform/fable-judge")"
  method_live="$(/bin/bash -c 'source "$1"; platform_live_path "$2"' _ "$CURRENT_SCRIPT" "$platform")"
  sibling_before="$(live_digest "$method_live")"
  judge_args=(--activate --platform "$platform" --replace-reviewed-local-drift
    --expected-live-sha256 "$RLD_ZERO_L" --expected-canonical-head "$RLD_H"
    --expected-canonical-tree "$RLD_T" --expected-materialization-tree "$judge_m" --skill fable-judge)
  rld_reset_drift "$judge_source" "$judge_live"
  assert_failure_contains "Judge/$platform ordinary LOCAL_DRIFT" 'LOCAL_DRIFT' "$CURRENT_SCRIPT" --activate --skill fable-judge --platform "$platform"
  judge_l="$(rld_probe_observed_l "$platform" "$judge_m" fable-judge)"
  for index in 5 7 9 11; do
    args=("${judge_args[@]}")
    args[5]="$judge_l"
    if [[ "$index" -eq 5 ]]; then args[$index]="$RLD_ZERO_L"; else args[$index]="$RLD_ZERO_OBJ"; fi
    : >"$RLD_TRACE"
    capture_command /bin/bash "$RLD_RUNNER" "$CURRENT_SCRIPT" normal "$RLD_TRACE" "$STALE_HEAD" "${args[@]}"
    rld_assert_refusal "Judge/$platform identity $index" 'MISMATCH'
    rld_assert_no_write "Judge/$platform identity $index"
  done
  for kind in unknown empty symlink wrong-type unreadable; do
    case "$kind" in
      unknown) printf unknown >"$judge_live/ZZZ-unknown" ;;
      empty) mkdir "$judge_live/ZZZ-empty" ;;
      symlink) ln -s "$judge_source/SKILL.md" "$judge_live/ZZZ-symlink" ;;
      wrong-type) mv "$judge_live/SKILL.md" "$SCRATCH/judge-saved"; mkdir "$judge_live/SKILL.md" ;;
      unreadable) chmod 000 "$judge_live/SKILL.md" ;;
    esac
    expected_state=LOCAL_DRIFT
    [[ "$kind" != symlink && "$kind" != wrong-type ]] || expected_state=SYMLINK_OR_WRONG_TYPE
    [[ "$kind" != unreadable ]] || expected_state=UNRESOLVED
    assert_failure_contains "Judge/$platform $kind classification" "STATE: $expected_state" "$CURRENT_SCRIPT" --check --skill fable-judge --platform "$platform"
    : >"$RLD_TRACE"
    rld_run_injected normal "$judge_l" "$platform" fable-judge
    rld_assert_refusal "Judge/$platform $kind replacement" 'LIVE_BUNDLE_REJECTED'
    rld_assert_no_write "Judge/$platform $kind replacement"
    case "$kind" in
      unknown) rm "$judge_live/ZZZ-unknown" ;;
      empty) rmdir "$judge_live/ZZZ-empty" ;;
      symlink) rm "$judge_live/ZZZ-symlink" ;;
      wrong-type) rmdir "$judge_live/SKILL.md"; mv "$SCRATCH/judge-saved" "$judge_live/SKILL.md" ;;
      unreadable) chmod 644 "$judge_live/SKILL.md" ;;
    esac
  done
  for scenario in live-change canonical-ref-change source-change write-failure post-failure; do
    rld_reset_drift "$judge_source" "$judge_live"
    judge_l="$(rld_probe_observed_l "$platform" "$judge_m" fable-judge)"
    cp "$judge_source/SKILL.md" "$SCRATCH/judge-source-saved"
    rld_run_injected "$scenario" "$judge_l" "$platform" fable-judge
    case "$scenario" in
      live-change) expected=REVIEWED_REPLACEMENT_PREWRITE_LIVE_BUNDLE_SHA256_MISMATCH ;;
      canonical-ref-change) expected=REVIEWED_REPLACEMENT_CANONICAL_REF_MISMATCH ;;
      source-change) expected=CANONICAL_MATERIALIZATION_DRIFT ;;
      write-failure) expected=REVIEWED_REPLACEMENT_WRITE_FAILED ;;
      post-failure) expected=ACTIVATION_VERIFICATION_FAILED ;;
    esac
    rld_assert_refusal "Judge/$platform $scenario" "$expected"
    if [[ "$scenario" != write-failure && "$scenario" != post-failure ]]; then rld_assert_no_write "Judge/$platform $scenario"; fi
    git -C "$FIXTURE_CANONICAL" update-ref refs/remotes/origin/master "$RLD_H"
    cp "$SCRATCH/judge-source-saved" "$judge_source/SKILL.md"
  done
  # Same size and timestamp, different bytes: only checksum copy repairs it.
  /usr/bin/rsync -a --checksum --delete "$judge_source/" "$judge_live/"
  ruby -e 's,t=ARGV; b=File.binread(s); b.setbyte(0,b.getbyte(0)^1); File.binwrite(t,b); stamp=Time.at(1700000000); File.utime(stamp,stamp,s,t)' "$judge_source/SKILL.md" "$judge_live/SKILL.md"
  judge_l="$(rld_probe_observed_l "$platform" "$judge_m" fable-judge)"
  : >"$RLD_TRACE"
  rld_run_injected without-checksum "$judge_l" "$platform" fable-judge
  rld_assert_refusal "Judge/$platform checksum negative control" 'ACTIVATION_VERIFICATION_FAILED'
  pass_case "Judge/$platform checksum negative control fails as required"
  : >"$RLD_TRACE"
  rld_run_injected normal "$judge_l" "$platform" fable-judge
  [[ "$COMMAND_STATUS" -eq 0 && "$COMMAND_OUTPUT" == *'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION'* ]] || fail "Judge/$platform reviewed replacement"
  [[ "$(live_digest "$judge_live")" == "$(live_digest "$judge_source")" ]] || fail "Judge/$platform full bundle mismatch"
  [[ "$(live_digest "$method_live")" == "$sibling_before" ]] || fail "Judge/$platform mutated Method sibling"
  pass_case "Judge/$platform reviewed replacement with exact bundle and unchanged Method sibling"
done

# Authority scopes include both skills and cross-scope renames.
for skill in fable-method fable-judge; do
  printf dirty >"$LINKED_CURRENT/$skill/authority-dirty"
  assert_failure_contains "$skill dirty authority refuses Judge activation" 'ACTIVATION_REPOSITORY_STATE_NOT_READY' "$CURRENT_SCRIPT" --activate --skill fable-judge --platform codex
  rm "$LINKED_CURRENT/$skill/authority-dirty"
done
git -C "$LINKED_CURRENT" mv fable-judge/MIGRATION.md judge-moved.md
assert_failure_contains 'Judge cross-scope rename refuses activation' 'ACTIVATION_REPOSITORY_STATE_NOT_READY' "$CURRENT_SCRIPT" --activate --skill fable-judge --platform codex
git -C "$LINKED_CURRENT" mv judge-moved.md fable-judge/MIGRATION.md

readonly JUDGE_READY_FIFO="$SCRATCH/judge-lock-ready"
readonly JUDGE_RELEASE_FIFO="$SCRATCH/judge-lock-release"
mkfifo "$JUDGE_READY_FIFO" "$JUDGE_RELEASE_FIFO"
hold_activation_lock "$FIXTURE_LOCK" "$JUDGE_READY_FIFO" "$JUDGE_RELEASE_FIFO" &
HOLDER_PID=$!
read -r judge_lock_status <"$JUDGE_READY_FIFO"
[[ "$judge_lock_status" == HOLDER_LOCK_ACQUIRED ]] || fail 'Judge lock fixture failed'
assert_failure_contains 'Judge shares Method activation lock' 'ACTIVATION_LOCK_BUSY' "$CURRENT_SCRIPT" --activate --skill fable-judge --platform codex
printf release >"$JUDGE_RELEASE_FIFO"
wait "$HOLDER_PID"
HOLDER_PID=''
[[ "$(real_lock_fingerprint)" == "$REAL_LOCK_BEFORE" ]] || fail 'Judge fixtures changed real activation lock'
pass_case 'all Judge mutations were scratch-only'

git -C "$FIXTURE_CANONICAL" update-ref -d refs/remotes/origin/master
assert_failure_contains \
  'unresolved canonical origin master fails closed' \
  'CANONICAL_MASTER_REF_UNRESOLVED' \
  "$CURRENT_SCRIPT" --check --platform codex

printf 'PASS: %s focused activate-live cases\n' "$PASS_COUNT"
