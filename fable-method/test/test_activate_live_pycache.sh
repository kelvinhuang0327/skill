#!/usr/bin/env bash
set -euo pipefail

readonly TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SOURCE_ROOT="$(cd "${TEST_DIR}/../.." && pwd -P)"
readonly SOURCE_ACTIVATE_SCRIPT="${SOURCE_ROOT}/fable-method/scripts/activate-live.sh"
readonly SCRATCH_BASE="$(cd "${TMPDIR:?TMPDIR must point to the task test-tmp directory}" && pwd -P)"
SCRATCH="$(mktemp -d "${SCRATCH_BASE%/}/fable-activate-live-pycache.XXXXXX")"
readonly FIXTURE_CANONICAL="${SCRATCH}/canonical"
readonly FIXTURE_HOME="${SCRATCH}/home"
readonly FIXTURE_ACTIVATE="${FIXTURE_CANONICAL}/fable-method/scripts/activate-live.sh"
readonly CURRENT_MATERIALIZATION="${FIXTURE_CANONICAL}/fable-method/platforms/codex/fable-method"
readonly LIVE_PARENT="${FIXTURE_HOME}/.codex/skills"
readonly LIVE="${LIVE_PARENT}/fable-method"
readonly HISTORICAL_ROOT="${SCRATCH}/historical"
readonly FAKE_BIN="${SCRATCH}/fake-bin"
readonly EXPECT_PYC_DRIFT="${EXPECT_PYC_DRIFT:-0}"

[[ "$EXPECT_PYC_DRIFT" == 0 || "$EXPECT_PYC_DRIFT" == 1 ]] \
  || { printf 'FAIL: EXPECT_PYC_DRIFT must be 0 or 1\n' >&2; exit 1; }

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

pass_case() {
  printf 'PASS: %s\n' "$1"
}

cleanup() {
  [[ -n "${SCRATCH:-}" && -d "$SCRATCH" ]] || return 0
  case "$SCRATCH" in
    "${SCRATCH_BASE%/}"/fable-activate-live-pycache.*)
      rm -r -- "$SCRATCH"
      ;;
    *)
      printf 'REFUSED_CLEANUP_OUTSIDE_TEST_PREFIX: %s\n' "$SCRATCH" >&2
      return 1
      ;;
  esac
}
trap cleanup EXIT

[[ -f "$SOURCE_ACTIVATE_SCRIPT" ]] || fail 'source activate-live.sh is missing'
mkdir -p "$FIXTURE_CANONICAL" "$FIXTURE_HOME/.codex" "$LIVE_PARENT"
git -C "$SOURCE_ROOT" archive HEAD | tar -x -C "$FIXTURE_CANONICAL"
# Overlay the candidate Fable source so this fixture exercises uncommitted code.
cp -R "$SOURCE_ROOT/fable-method/." "$FIXTURE_CANONICAL/fable-method/"

# This container's Git emits a Darwin temp-directory warning on stderr. The
# production script captures Git stderr with its inventory output, so filter
# only that environment warning inside this disposable fixture's trusted PATH.
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/git" <<'EOF'
#!/bin/bash
stderr_file="$(mktemp "${0%/*}/git-stderr.XXXXXX")"
if /usr/bin/git "$@" 2>"$stderr_file"; then
  git_rc=0
else
  git_rc=$?
fi
/usr/bin/grep -vF "warning: confstr() failed with code 5: couldn't get path of DARWIN_USER_TEMP_DIR; using /tmp instead" "$stderr_file" >&2 || true
rm -f -- "$stderr_file"
exit "$git_rc"
EOF
chmod +x "$FAKE_BIN/git"

# Rewrite every fixed production path only inside the disposable fixture.
/usr/bin/ruby -e '
  canonical_root, fixture_home, fake_bin, *paths = ARGV
  replacements = {
    "/Users/kelvin/VibeCoding-WorkSpace/skill" => canonical_root,
    "/Users/kelvin" => fixture_home
  }
  paths.each do |path|
    content = File.binread(path)
    content = content.gsub(Regexp.union(replacements.keys)) { |old| replacements.fetch(old) }
    if path.end_with?("activate-live.sh")
      matches = content.scan(/^readonly TRUSTED_GIT_PATH=/).length
      raise "fixture trusted Git PATH declaration count: #{matches}" unless matches == 1
      fixture_path = "readonly TRUSTED_GIT_PATH='#{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin'"
      content = content.sub(/^readonly TRUSTED_GIT_PATH=.*$/, fixture_path)
    end
    File.binwrite(path, content)
  end
' "$FIXTURE_CANONICAL" "$FIXTURE_HOME" "$FAKE_BIN" \
  "$FIXTURE_CANONICAL/fable-method/scripts/activate-live.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/sync-platforms.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/platform_manifest.rb" \
  "$FIXTURE_CANONICAL/fable-method/platforms.yaml"

/usr/bin/ruby -e '
  canonical, home, *paths = ARGV
  paths.each do |path|
    content = File.read(path).gsub(canonical, "FIXTURE_CANONICAL").gsub(home, "FIXTURE_HOME")
    content = content.gsub(File.dirname(canonical), "FIXTURE_SCRATCH")
    raise "real user path survived: #{path}" if content.include?("/Users/kelvin")
  end
' "$FIXTURE_CANONICAL" "$FIXTURE_HOME" \
  "$FIXTURE_CANONICAL/fable-method/scripts/activate-live.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/sync-platforms.sh" \
  "$FIXTURE_CANONICAL/fable-method/scripts/platform_manifest.rb" \
  "$FIXTURE_CANONICAL/fable-method/platforms.yaml" \
  || fail 'fixture isolation: a production path survived rewriting'

git -C "$FIXTURE_CANONICAL" init -q -b master
git -C "$FIXTURE_CANONICAL" config user.name 'Fable Activate Pycache Test'
git -C "$FIXTURE_CANONICAL" config user.email 'fable-activate-pycache@example.invalid'

# Create one prior Codex materialization and then restore the current version.
readonly CODEX_SKILL_FILE="${CURRENT_MATERIALIZATION}/SKILL.md"
cp "$CODEX_SKILL_FILE" "$SCRATCH/current-codex-skill.md"
printf '\nHistorical pycache fixture revision.\n' >> "$CODEX_SKILL_FILE"
git -C "$FIXTURE_CANONICAL" add --all
git -C "$FIXTURE_CANONICAL" commit -q -m 'fixture historical codex materialization'
readonly HISTORICAL_HEAD="$(git -C "$FIXTURE_CANONICAL" rev-parse HEAD)"
cp "$SCRATCH/current-codex-skill.md" "$CODEX_SKILL_FILE"
git -C "$FIXTURE_CANONICAL" add --all
git -C "$FIXTURE_CANONICAL" commit -q -m 'fixture current codex materialization'
readonly CURRENT_HEAD="$(git -C "$FIXTURE_CANONICAL" rev-parse HEAD)"
git -C "$FIXTURE_CANONICAL" remote add origin "$FIXTURE_CANONICAL"
git -C "$FIXTURE_CANONICAL" update-ref refs/remotes/origin/master "$CURRENT_HEAD"
dirty_porcelain="$(git -C "$FIXTURE_CANONICAL" status --porcelain)"
[[ -z "$dirty_porcelain" ]] || fail "fixture canonical repository is unexpectedly dirty: ${dirty_porcelain}"

assert_check_state() {
  local label="$1" expected_state="$2" output check_rc
  if output="$("$FIXTURE_ACTIVATE" --check --platform codex 2>&1)"; then
    check_rc=0
  else
    check_rc=$?
  fi
  [[ "$output" == *"STATE: ${expected_state}"* ]] \
    || fail "${label}: expected STATE ${expected_state}; got exit ${check_rc}: ${output}"
  if [[ "$expected_state" == LOCAL_DRIFT ]]; then
    [[ "$check_rc" -eq 1 ]] || fail "${label}: expected --check exit 1, got ${check_rc}"
  else
    [[ "$check_rc" -eq 0 ]] || fail "${label}: expected --check exit 0, got ${check_rc}"
  fi
}

reset_live_current() {
  case "$LIVE" in
    "${FIXTURE_HOME}"/*) rm -r -- "$LIVE" 2>/dev/null || true ;;
    *) fail 'refused to reset a live path outside fixture home' ;;
  esac
  cp -R "$CURRENT_MATERIALIZATION" "$LIVE"
}

reset_live_current
assert_check_state 'P1 precondition current bundle' EXACT_CURRENT_MATERIALIZATION
canonical_digest="$(bash -c 'source "$1"; compute_live_bundle_sha256 "$2" codex digest' _ "$FIXTURE_ACTIVATE" "$CURRENT_MATERIALIZATION")"
live_digest="$(bash -c 'source "$1"; compute_live_bundle_sha256 "$2" codex digest' _ "$FIXTURE_ACTIVATE" "$LIVE")"
[[ "$canonical_digest" == "$live_digest" ]] || fail 'digest mode changed on identical bundles'
pass_case 'digest mode is unchanged for an identical clean bundle'

mkdir -p "$LIVE/scripts/__pycache__"
printf 'fixture bytecode\n' > "$LIVE/scripts/__pycache__/fable_artifact_controls.cpython-313.pyc"
if [[ "$EXPECT_PYC_DRIFT" == 1 ]]; then
  assert_check_state 'negative P1 pyc-only cache without exception' LOCAL_DRIFT
  pass_case 'NEGATIVE CONTROL: P1 pyc-only cache is rejected when the exception is removed'
else
  assert_check_state 'P1 pyc-only cache' EXACT_CURRENT_MATERIALIZATION
  pass_case 'P1 current bundle plus pyc-only __pycache__ remains exact current materialization'
fi

if digest_output="$(bash -c 'source "$1"; compute_live_bundle_sha256 "$2" codex digest' _ "$FIXTURE_ACTIVATE" "$LIVE" 2>&1)"; then
  fail 'digest mode unexpectedly ignored a pyc-only __pycache__'
else
  digest_rc=$?
fi
[[ "$digest_rc" -eq 1 && "$digest_output" == *'unknown path: scripts/__pycache__'* ]] \
  || fail "digest mode changed its unknown-path rejection: ${digest_output}"
pass_case 'digest mode keeps its existing pycache rejection and serialization branch'

rm "$LIVE/scripts/__pycache__/fable_artifact_controls.cpython-313.pyc"
if [[ "$EXPECT_PYC_DRIFT" == 1 ]]; then
  rm -r -- "$LIVE/scripts/__pycache__"
else
  assert_check_state 'empty cache directory' EXACT_CURRENT_MATERIALIZATION
  pass_case 'an empty __pycache__ directory is ignored in git-entries mode'
fi

case "$LIVE" in
  "${FIXTURE_HOME}"/*) rm -r -- "$LIVE" ;;
  *) fail 'refused to reset a live path outside fixture home' ;;
esac
mkdir -p "$HISTORICAL_ROOT"
git -C "$FIXTURE_CANONICAL" archive "$HISTORICAL_HEAD" fable-method/platforms/codex/fable-method | tar -x -C "$HISTORICAL_ROOT"
readonly HISTORICAL_MATERIALIZATION="${HISTORICAL_ROOT}/fable-method/platforms/codex/fable-method"
cp -R "$HISTORICAL_MATERIALIZATION" "$LIVE"
mkdir -p "$LIVE/scripts/__pycache__"
printf 'fixture bytecode\n' > "$LIVE/scripts/__pycache__/fable_artifact_controls.cpython-313.pyc"
if [[ "$EXPECT_PYC_DRIFT" == 1 ]]; then
  if activation_output="$("$FIXTURE_ACTIVATE" --activate --platform codex 2>&1)"; then
    fail 'negative P2 activated despite pyc-only cache being rejected'
  else
    activation_rc=$?
  fi
  [[ "$activation_rc" -eq 2 && "$activation_output" == *'LOCAL_DRIFT'* ]] \
    || fail "negative P2 did not reject the cache as LOCAL_DRIFT: ${activation_output}"
  [[ -f "$LIVE/scripts/__pycache__/fable_artifact_controls.cpython-313.pyc" ]] \
    || fail 'negative P2 refusal changed the live cache'
  pass_case 'NEGATIVE CONTROL: P2 activation refuses a pyc-only cache when the exception is removed'
else
  activation_output="$("$FIXTURE_ACTIVATE" --activate --platform codex 2>&1)" \
    || fail "P2 historical activation failed: ${activation_output}"
  [[ "$activation_output" == *'ACTIVATION_RESULT: ACTIVATED'* ]] \
    || fail "P2 did not report activation: ${activation_output}"
  [[ "$activation_output" == *'FINAL_STATE: EXACT_CURRENT_MATERIALIZATION'* ]] \
    || fail "P2 did not verify current materialization: ${activation_output}"
  [[ ! -e "$LIVE/scripts/__pycache__" ]] || fail 'P2 activation left __pycache__ behind'
  assert_check_state 'P2 post-activation' EXACT_CURRENT_MATERIALIZATION
  pass_case 'P2 historical bundle with pyc cache activates and rsync --delete removes it'
fi

reset_live_current
mkdir -p "$LIVE/scripts/__pycache__"
printf 'local source drift\n' > "$LIVE/scripts/__pycache__/evil.py"
assert_check_state 'N1 non-pyc cache file' LOCAL_DRIFT
if activation_output="$("$FIXTURE_ACTIVATE" --activate --platform codex 2>&1)"; then
  fail 'N1 activation unexpectedly accepted scripts/__pycache__/evil.py'
else
  activation_rc=$?
fi
[[ "$activation_rc" -eq 2 && "$activation_output" == *'LOCAL_DRIFT'* ]] \
  || fail "N1 did not refuse drift: ${activation_output}"
[[ -f "$LIVE/scripts/__pycache__/evil.py" ]] || fail 'N1 refusal removed the local file'
pass_case 'N1 non-pyc file remains LOCAL_DRIFT and activation refuses without deletion'

reset_live_current
mkdir -p "$LIVE/scripts/__pycache__/sub"
printf 'nested bytecode\n' > "$LIVE/scripts/__pycache__/sub/x.pyc"
assert_check_state 'N2 nested cache directory' LOCAL_DRIFT
pass_case 'N2 nested directory under __pycache__ remains LOCAL_DRIFT'

reset_live_current
printf 'unmanaged bytecode\n' > "$LIVE/scripts/x.pyc"
assert_check_state 'N3 pyc outside cache' LOCAL_DRIFT
pass_case 'N3 pyc outside __pycache__ remains LOCAL_DRIFT'
