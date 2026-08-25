#!/usr/bin/env bash
set -euo pipefail

readonly TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_ROOT="$(cd "${TEST_DIR}/../.." && pwd)"
readonly SOURCE_ACTIVATE_SCRIPT="${SOURCE_ROOT}/fable-method/scripts/activate-live.sh"
readonly SYSTEM_PATH='/usr/bin:/bin:/usr/sbin:/sbin'

PASS_COUNT=0
COMMAND_OUTPUT=''
COMMAND_STATUS=0
SCRATCH=''

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

cleanup() {
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

readonly SCRATCH_BASE="${TMPDIR:-/tmp}"
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
cp "$SOURCE_ACTIVATE_SCRIPT" "$FIXTURE_CANONICAL/fable-method/scripts/activate-live.sh"

# Production has no runtime bypass. The harness rewrites fixed installation
# metadata only inside its disposable repository so an accepted activation can
# exercise the real write path without ever naming a real live installation.
/usr/bin/ruby -e '
  canonical_root, fixture_home, activate, sync, manifest = ARGV
  replacements = {
    "/Users/kelvin/VibeCoding-WorkSpace/skill" => canonical_root,
    "/Users/kelvin" => fixture_home
  }
  [activate, sync, manifest].each do |path|
    content = File.binread(path)
    replacements.each { |old, new_value| content = content.gsub(old, new_value) }
    File.binwrite(path, content)
  end
' "$FIXTURE_CANONICAL" "$FIXTURE_HOME" \
  "$FIXTURE_CANONICAL/fable-method/scripts/activate-live.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/sync-platforms.sh" \
  "$FIXTURE_CANONICAL/fable-method/platforms.yaml"

if grep -Fq '/Users/kelvin' \
  "$FIXTURE_CANONICAL/fable-method/scripts/activate-live.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/sync-platforms.sh" \
  "$FIXTURE_CANONICAL/fable-method/platforms.yaml"; then
  fail 'fixture isolation: a real user path survived rewriting'
fi
pass_case 'fixture activation targets are scratch-only'

git -C "$FIXTURE_CANONICAL" init -q -b master
git -C "$FIXTURE_CANONICAL" config user.name 'Fable Activate Test'
git -C "$FIXTURE_CANONICAL" config user.email 'fable-activate-test@example.invalid'
git -C "$FIXTURE_CANONICAL" add --all
git -C "$FIXTURE_CANONICAL" commit -q -m 'fixture stale master'
readonly STALE_HEAD="$(git -C "$FIXTURE_CANONICAL" rev-parse HEAD)"

git -C "$FIXTURE_CANONICAL" switch -q -c canonical-tip
git -C "$FIXTURE_CANONICAL" commit -q --allow-empty -m 'fixture canonical origin master'
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

git -C "$FIXTURE_CANONICAL" update-ref -d refs/remotes/origin/master
assert_failure_contains \
  'unresolved canonical origin master fails closed' \
  'CANONICAL_MASTER_REF_UNRESOLVED' \
  "$CURRENT_SCRIPT" --check --platform codex

printf 'PASS: %s focused activate-live cases\n' "$PASS_COUNT"
