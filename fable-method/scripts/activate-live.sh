#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly FABLE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPOSITORY_ROOT="$(cd "${FABLE_ROOT}/.." && pwd)"
readonly EXPECTED_REPOSITORY_ROOT='/Users/kelvin/VibeCoding-WorkSpace/skill'
readonly USER_HOME='/Users/kelvin'
readonly MANIFEST="${FABLE_ROOT}/platforms.yaml"
readonly SYNC_SCRIPT="${SCRIPT_DIR}/sync-platforms.sh"
readonly PLATFORMS=(codex claude gemini)

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 2
}

usage() {
  cat >&2 <<'EOF'
Usage:
  activate-live.sh --help
  activate-live.sh --check [--platform codex|claude|gemini]
  activate-live.sh --activate --platform codex|claude|gemini
EOF
  exit 2
}

print_help() {
  cat <<'EOF'
activate-live.sh - repository-owned activation for the Fable Method live skill installs.

  --check                       Read-only. Classifies every configured platform's
                                 live installation against this repository's current
                                 and historical materializations. Never writes to
                                 the filesystem.
  --check --platform <name>     Read-only. Same classification for one platform.
  --activate --platform <name>  Writes exactly the one named platform's configured
                                 live installation, and only when its current state
                                 is provably ABSENT, an exact copy of the current
                                 repository materialization, or an exact copy of an
                                 earlier committed materialization. Any live state
                                 this script cannot fully account for - local drift,
                                 a symlink, or an unresolved read - is never
                                 overwritten, mirrored, or deleted.
  --help                        Show this help.

Running --activate against a real installation requires authorization from the
Owner obtained outside this script. This script performs no conversational or
environment-based authorization check of its own - passing the flag is the only
gate it enforces. There is no --activate-all, --force, --overwrite, --yes, or
--skip-check mode.

Platforms: codex, claude, gemini. --check accepts zero or one --platform;
--activate requires exactly one.
EOF
}

is_known_platform() {
  local name="$1" p
  for p in "${PLATFORMS[@]}"; do
    [[ "$p" == "$name" ]] && return 0
  done
  return 1
}

platform_materialized_rel() {
  case "$1" in
    codex) printf '%s\n' 'fable-method/platforms/codex/fable-method' ;;
    claude) printf '%s\n' 'fable-method/platforms/claude/fable-method' ;;
    gemini) printf '%s\n' 'fable-method/platforms/gemini/fable-method' ;;
    *) die "unknown platform: $1" ;;
  esac
}

platform_live_path() {
  case "$1" in
    codex) printf '%s\n' '/Users/kelvin/.codex/skills/fable-method' ;;
    claude) printf '%s\n' '/Users/kelvin/.claude/skills/fable-method' ;;
    gemini) printf '%s\n' '/Users/kelvin/.gemini/skills/fable-method' ;;
    *) die "unknown platform: $1" ;;
  esac
}

repo_path() {
  printf '%s/%s\n' "$REPOSITORY_ROOT" "$1"
}

guard_repository_root() {
  [[ "$REPOSITORY_ROOT" == "$EXPECTED_REPOSITORY_ROOT" ]] \
    || die 'repository path guard failed: script is not running from the canonical repository'
  local toplevel
  toplevel="$(git -C "$REPOSITORY_ROOT" rev-parse --show-toplevel 2>/dev/null)" \
    || die 'repository path guard failed: canonical root is not a git repository'
  [[ "$toplevel" == "$EXPECTED_REPOSITORY_ROOT" ]] \
    || die 'repository path guard failed: git toplevel does not match the expected repository root'
}

read_manifest_records() {
  ruby -ryaml -e '
    path = ARGV.fetch(0)
    data = YAML.safe_load(File.read(path), permitted_classes: [], aliases: false)
    raise "manifest must be a mapping" unless data.is_a?(Hash)
    raise "schema_version must be 1" unless data.fetch("schema_version") == 1
    platforms = data.fetch("platforms")
    raise "platforms must be an array" unless platforms.is_a?(Array)
    names = platforms.map { |p| p.fetch("name") }
    raise "duplicate platform name" unless names.uniq.length == names.length
    raise "platforms must be exactly codex, claude, gemini" unless names.sort == %w[claude codex gemini]
    platforms.each do |platform|
      name = platform.fetch("name")
      dest = platform.fetch("materialized_destination")
      live = platform.fetch("live_installation_path")
      raise "#{name} destination must be a relative path" if dest.start_with?("/")
      raise "#{name} live path must be an absolute path" unless live.start_with?("/")
      puts "#{name}\t#{dest}\t#{live}"
    end
  ' "$MANIFEST"
}

verify_manifest_paths() {
  [[ -f "$MANIFEST" && ! -L "$MANIFEST" ]] || die 'platform manifest is missing or is a symlink'
  local records
  records="$(read_manifest_records)" \
    || die 'ACTIVATION_MANIFEST_PATH_MISMATCH: manifest failed schema validation (unknown, duplicate, or missing platform)'
  local name dest live expected_dest expected_live mismatch=0
  while IFS=$'\t' read -r name dest live; do
    [[ -n "$name" ]] || continue
    expected_dest="$(platform_materialized_rel "$name")"
    expected_live="$(platform_live_path "$name")"
    if [[ "$dest" != "$expected_dest" || "$live" != "$expected_live" ]]; then
      printf 'ACTIVATION_MANIFEST_PATH_MISMATCH: %s\n' "$name" >&2
      printf '  manifest materialized_destination: %s\n' "$dest" >&2
      printf '  expected materialized_destination: %s\n' "$expected_dest" >&2
      printf '  manifest live_installation_path:   %s\n' "$live" >&2
      printf '  expected live_installation_path:   %s\n' "$expected_live" >&2
      mismatch=1
    fi
  done <<<"$records"
  (( mismatch == 0 )) || exit 2
}

require_canonical_source_gate() {
  [[ -f "$SYNC_SCRIPT" ]] || die "canonical sync script missing: $SYNC_SCRIPT"
  local output
  if ! output="$(bash "$SYNC_SCRIPT" --check 2>&1)"; then
    printf '%s\n' "$output" >&2
    die 'CANONICAL_MATERIALIZATION_DRIFT: sync-platforms.sh --check did not pass'
  fi
}

current_branch() {
  git -C "$REPOSITORY_ROOT" rev-parse --abbrev-ref HEAD
}

path_in_fable_scope() {
  local p="$1"
  [[ "$p" == "fable-method" || "$p" == fable-method/* ]]
}

classify_one_status_entry() {
  local xy="$1" path="$2" origpath="$3"
  local x="${xy:0:1}" y="${xy:1:1}"
  local in_scope=0
  if path_in_fable_scope "$path"; then
    in_scope=1
  elif [[ -n "$origpath" ]] && path_in_fable_scope "$origpath"; then
    in_scope=1
  fi
  if [[ "$in_scope" -eq 1 ]]; then
    [[ "$x" == '.' ]] || FABLE_STAGED_COUNT=$((FABLE_STAGED_COUNT + 1))
    [[ "$y" == '.' ]] || FABLE_TRACKED_DIRTY_COUNT=$((FABLE_TRACKED_DIRTY_COUNT + 1))
  else
    [[ "$x" == '.' ]] || UNRELATED_STAGED_COUNT=$((UNRELATED_STAGED_COUNT + 1))
    [[ "$y" == '.' ]] || UNRELATED_TRACKED_DIRTY_COUNT=$((UNRELATED_TRACKED_DIRTY_COUNT + 1))
  fi
}

# Classifies every changed path against fable-method/** using porcelain=v2 -z
# so renamed paths and paths containing spaces parse unambiguously. A rename
# crossing the fable-method/** boundary in either direction is scoped in, so
# it always blocks rather than risking a false "unrelated" classification.
classify_repository_dirty_state() {
  FABLE_STAGED_COUNT=0
  FABLE_TRACKED_DIRTY_COUNT=0
  UNRELATED_STAGED_COUNT=0
  UNRELATED_TRACKED_DIRTY_COUNT=0

  local status_file
  status_file="$(mktemp)" || die 'unable to read repository status'

  if ! git -C "$REPOSITORY_ROOT" status --porcelain=v2 --untracked-files=all -z >"$status_file"; then
    rm -f "$status_file"
    die 'unable to read repository status'
  fi

  local -a records=()
  local record
  while IFS= read -r -d '' record; do
    records+=("$record")
  done <"$status_file"
  rm -f "$status_file"

  local i=0
  local n=${#records[@]}
  while (( i < n )); do
    local rec="${records[$i]}"
    if [[ -z "$rec" ]]; then
      i=$((i + 1))
      continue
    fi
    local rtype="${rec%% *}"
    case "$rtype" in
      '?'|'!')
        i=$((i + 1))
        ;;
      1)
        local t f2 f3 f4 f5 f6 f7 f8 path
        read -r t f2 f3 f4 f5 f6 f7 f8 path <<<"$rec"
        classify_one_status_entry "$f2" "$path" ""
        i=$((i + 1))
        ;;
      2)
        local t f2 f3 f4 f5 f6 f7 f8 f9 path origpath
        read -r t f2 f3 f4 f5 f6 f7 f8 f9 path <<<"$rec"
        (( i + 1 < n )) || die 'ACTIVATION_REPOSITORY_STATUS_UNRECOGNIZED: truncated rename record'
        origpath="${records[$((i + 1))]}"
        classify_one_status_entry "$f2" "$path" "$origpath"
        i=$((i + 2))
        ;;
      u)
        local t f2 f3 f4 f5 f6 f7 f8 f9 f10 path
        read -r t f2 f3 f4 f5 f6 f7 f8 f9 f10 path <<<"$rec"
        classify_one_status_entry "$f2" "$path" ""
        i=$((i + 1))
        ;;
      *)
        die "ACTIVATION_REPOSITORY_STATUS_UNRECOGNIZED: unexpected git status record: ${rec}"
        ;;
    esac
  done
}

require_canonical_repository_state_for_activation() {
  local branch
  branch="$(current_branch)"
  classify_repository_dirty_state
  if [[ "$branch" != 'master' || "$FABLE_STAGED_COUNT" -ne 0 || "$FABLE_TRACKED_DIRTY_COUNT" -ne 0 ]]; then
    printf 'ACTIVATION_REPOSITORY_STATE_NOT_READY\n' >&2
    printf '  branch: %s (required: master)\n' "$branch" >&2
    printf '  fable_staged: %s (required: 0)\n' "$FABLE_STAGED_COUNT" >&2
    printf '  fable_tracked_dirty: %s (required: 0)\n' "$FABLE_TRACKED_DIRTY_COUNT" >&2
    if [[ "$UNRELATED_STAGED_COUNT" -ne 0 || "$UNRELATED_TRACKED_DIRTY_COUNT" -ne 0 ]]; then
      printf '  unrelated_staged: %s (not blocking)\n' "$UNRELATED_STAGED_COUNT" >&2
      printf '  unrelated_tracked_dirty: %s (not blocking)\n' "$UNRELATED_TRACKED_DIRTY_COUNT" >&2
    fi
    exit 2
  fi
}

report_canonical_repository_state_for_check() {
  local branch
  branch="$(current_branch)"
  classify_repository_dirty_state
  if [[ "$branch" != 'master' || "$FABLE_STAGED_COUNT" -ne 0 || "$FABLE_TRACKED_DIRTY_COUNT" -ne 0 \
        || "$UNRELATED_STAGED_COUNT" -ne 0 || "$UNRELATED_TRACKED_DIRTY_COUNT" -ne 0 ]]; then
    printf 'CANONICAL_REPOSITORY_STATE_NOTE: branch=%s fable_staged=%s fable_tracked_dirty=%s unrelated_staged=%s unrelated_tracked_dirty=%s\n' \
      "$branch" "$FABLE_STAGED_COUNT" "$FABLE_TRACKED_DIRTY_COUNT" "$UNRELATED_STAGED_COUNT" "$UNRELATED_TRACKED_DIRTY_COUNT"
  fi
}

require_rsync() {
  command -v rsync >/dev/null 2>&1 || die 'ACTIVATION_CAPABILITY_MISSING: rsync is required for --activate'
}

find_symlink_component() {
  local target="$1"
  case "$target" in
    "$USER_HOME"/*) ;;
    *) die "live target outside expected home: $target" ;;
  esac
  local rest="${target#"$USER_HOME"/}"
  local cur="$USER_HOME"
  local saved_ifs="$IFS"
  local -a parts
  IFS='/' read -r -a parts <<<"$rest"
  IFS="$saved_ifs"
  local part
  for part in "${parts[@]}"; do
    [[ -n "$part" ]] || continue
    cur="$cur/$part"
    if [[ -L "$cur" ]]; then
      printf '%s\n' "$cur"
      return 0
    fi
  done
  return 1
}

known_paths_for_platform() {
  local platform="$1"
  local rel commit
  rel="$(platform_materialized_rel "$platform")"
  {
    while IFS= read -r commit; do
      [[ -n "$commit" ]] || continue
      git -C "$REPOSITORY_ROOT" ls-tree -r --name-only "$commit" -- "$rel" 2>/dev/null
    done < <(git -C "$REPOSITORY_ROOT" log --format=%H -- "$rel")
  } | sed "s#^${rel}/##" | LC_ALL=C sort -u
}

commit_tree_entries() {
  local commit="$1" rel="$2"
  git -C "$REPOSITORY_ROOT" ls-tree -r "$commit" -- "$rel" 2>/dev/null | while IFS=$'\t' read -r meta path; do
    [[ -n "$path" ]] || continue
    local mode hash
    mode="$(awk '{print $1}' <<<"$meta")"
    hash="$(awk '{print $3}' <<<"$meta")"
    printf '%s\t%s\t%s\n' "$mode" "$hash" "${path#"$rel"/}"
  done | LC_ALL=C sort
}

live_tree_entries() {
  local live="$1" paths="$2"
  local rel f mode hash
  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    f="$live/$rel"
    if [[ -x "$f" ]]; then mode=100755; else mode=100644; fi
    hash="$(git -C "$REPOSITORY_ROOT" hash-object --no-filters -- "$f")"
    printf '%s\t%s\t%s\n' "$mode" "$hash" "$rel"
  done <<<"$paths" | LC_ALL=C sort
}

# Sets CLASSIFY_STATE and CLASSIFY_MATCHED_COMMIT. Read-only: never mutates the
# live target. Unknown live paths are checked by name against known Fable
# history before any file is opened or hashed, so an unrecognized extra file's
# content is never read.
classify_platform() {
  local platform="$1"
  local live rel
  live="$(platform_live_path "$platform")"
  rel="$(platform_materialized_rel "$platform")"

  CLASSIFY_STATE=""
  CLASSIFY_MATCHED_COMMIT="NOT_APPLICABLE"

  local sym
  if sym="$(find_symlink_component "$live")"; then
    CLASSIFY_STATE=SYMLINK_OR_WRONG_TYPE
    return 0
  fi

  if [[ ! -e "$live" ]]; then
    CLASSIFY_STATE=ABSENT
    return 0
  fi

  if [[ ! -d "$live" ]]; then
    CLASSIFY_STATE=SYMLINK_OR_WRONG_TYPE
    return 0
  fi

  local stray
  if ! stray="$(find "$live" -type l -print -quit 2>/dev/null)"; then
    CLASSIFY_STATE=UNRESOLVED
    return 0
  fi
  if [[ -n "$stray" ]]; then
    CLASSIFY_STATE=SYMLINK_OR_WRONG_TYPE
    return 0
  fi

  if ! stray="$(find "$live" -mindepth 1 -not -type f -not -type d -print -quit 2>/dev/null)"; then
    CLASSIFY_STATE=UNRESOLVED
    return 0
  fi
  if [[ -n "$stray" ]]; then
    CLASSIFY_STATE=SYMLINK_OR_WRONG_TYPE
    return 0
  fi

  local raw_paths live_paths
  if ! raw_paths="$(find "$live" -type f -print 2>/dev/null | LC_ALL=C sort)"; then
    CLASSIFY_STATE=UNRESOLVED
    return 0
  fi
  live_paths="$(while IFS= read -r f; do [[ -n "$f" ]] && printf '%s\n' "${f#"$live"/}"; done <<<"$raw_paths")"

  local known p
  known="$(known_paths_for_platform "$platform")"
  while IFS= read -r p; do
    [[ -n "$p" ]] || continue
    if ! printf '%s\n' "$known" | grep -Fxq "$p"; then
      CLASSIFY_STATE=LOCAL_DRIFT
      return 0
    fi
  done <<<"$live_paths"

  local live_entries current_entries
  live_entries="$(live_tree_entries "$live" "$live_paths")"
  current_entries="$(commit_tree_entries HEAD "$rel")"
  if [[ "$live_entries" == "$current_entries" ]]; then
    CLASSIFY_STATE=EXACT_CURRENT_MATERIALIZATION
    CLASSIFY_MATCHED_COMMIT="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
    return 0
  fi

  local commit hist_entries
  while IFS= read -r commit; do
    [[ -n "$commit" ]] || continue
    hist_entries="$(commit_tree_entries "$commit" "$rel")"
    if [[ -n "$hist_entries" && "$live_entries" == "$hist_entries" ]]; then
      CLASSIFY_STATE=EXACT_HISTORICAL_MATERIALIZATION
      CLASSIFY_MATCHED_COMMIT="$commit"
      return 0
    fi
  done < <(git -C "$REPOSITORY_ROOT" log --format=%H -- "$rel")

  CLASSIFY_STATE=LOCAL_DRIFT
  return 0
}

eligible_for_activation() {
  case "$1" in
    ABSENT|EXACT_CURRENT_MATERIALIZATION|EXACT_HISTORICAL_MATERIALIZATION) return 0 ;;
    *) return 1 ;;
  esac
}

do_check() {
  local only_platform="$1"
  guard_repository_root
  verify_manifest_paths
  require_canonical_source_gate
  report_canonical_repository_state_for_check

  local -a targets=()
  if [[ -n "$only_platform" ]]; then
    targets=("$only_platform")
  else
    targets=("${PLATFORMS[@]}")
  fi

  local overall=0 platform ready
  for platform in "${targets[@]}"; do
    classify_platform "$platform"
    case "$CLASSIFY_STATE" in
      ABSENT|EXACT_CURRENT_MATERIALIZATION|EXACT_HISTORICAL_MATERIALIZATION) ready=YES ;;
      *) ready=NO; overall=1 ;;
    esac
    printf 'PLATFORM: %s\n' "$platform"
    printf 'STATE: %s\n' "$CLASSIFY_STATE"
    printf 'MATCHED_COMMIT: %s\n' "$CLASSIFY_MATCHED_COMMIT"
    printf 'CANONICAL_HEAD: %s\n' "$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
    printf 'LIVE_TARGET: %s\n' "$(platform_live_path "$platform")"
    printf 'ACTIVATION_READY: %s\n' "$ready"
  done

  return "$overall"
}

do_activate() {
  local platform="$1"
  guard_repository_root
  verify_manifest_paths
  require_canonical_source_gate
  require_canonical_repository_state_for_activation
  require_rsync

  classify_platform "$platform"
  local prev_state="$CLASSIFY_STATE"
  local prev_matched="$CLASSIFY_MATCHED_COMMIT"

  if ! eligible_for_activation "$prev_state"; then
    printf 'ACTIVATION_NOT_ELIGIBLE\n' >&2
    printf '  platform: %s\n' "$platform" >&2
    printf '  state: %s\n' "$prev_state" >&2
    exit 2
  fi

  classify_platform "$platform"
  if [[ "$CLASSIFY_STATE" != "$prev_state" || "$CLASSIFY_MATCHED_COMMIT" != "$prev_matched" ]]; then
    printf 'LIVE_TARGET_CHANGED_BEFORE_WRITE\n' >&2
    printf '  previous: %s (%s)\n' "$prev_state" "$prev_matched" >&2
    printf '  prewrite: %s (%s)\n' "$CLASSIFY_STATE" "$CLASSIFY_MATCHED_COMMIT" >&2
    exit 2
  fi

  local live canonical_abs result
  live="$(platform_live_path "$platform")"
  canonical_abs="$(repo_path "$(platform_materialized_rel "$platform")")"
  [[ -d "$canonical_abs" && ! -L "$canonical_abs" ]] || die 'canonical materialization is missing or is a symlink'

  case "$prev_state" in
    ABSENT)
      local parent
      parent="$(dirname "$live")"
      [[ -d "$parent" && ! -L "$parent" ]] || die 'ACTIVATION_PARENT_NOT_READY'
      mkdir "$live"
      rsync -a --delete "$canonical_abs"/ "$live"/
      result=ACTIVATED
      ;;
    EXACT_HISTORICAL_MATERIALIZATION)
      rsync -a --delete "$canonical_abs"/ "$live"/
      result=ACTIVATED
      ;;
    EXACT_CURRENT_MATERIALIZATION)
      result=ALREADY_CURRENT
      ;;
  esac

  classify_platform "$platform"
  if [[ "$CLASSIFY_STATE" != EXACT_CURRENT_MATERIALIZATION ]]; then
    printf 'ACTIVATION_VERIFICATION_FAILED\n' >&2
    printf '  post_write_state: %s\n' "$CLASSIFY_STATE" >&2
    exit 2
  fi

  printf 'PLATFORM: %s\n' "$platform"
  printf 'PREVIOUS_STATE: %s\n' "$prev_state"
  if [[ "$prev_state" == EXACT_HISTORICAL_MATERIALIZATION ]]; then
    printf 'PREVIOUS_MATCHED_COMMIT: %s\n' "$prev_matched"
  fi
  printf 'ACTIVATION_RESULT: %s\n' "$result"
  printf 'FINAL_STATE: %s\n' "$CLASSIFY_STATE"
  printf 'CANONICAL_HEAD: %s\n' "$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
  printf 'LIVE_TARGET: %s\n' "$live"
}

main() {
  if [[ $# -eq 0 ]]; then
    usage
  fi

  local mode="" platform="" platform_count=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help)
        print_help
        exit 0
        ;;
      --check)
        [[ -z "$mode" ]] || die 'cannot combine modes'
        mode=check
        shift
        ;;
      --activate)
        [[ -z "$mode" ]] || die 'cannot combine modes'
        mode=activate
        shift
        ;;
      --platform)
        [[ $# -ge 2 ]] || die '--platform requires a value'
        platform="$2"
        platform_count=$((platform_count + 1))
        shift 2
        ;;
      *)
        die "unknown argument: $1"
        ;;
    esac
  done

  [[ -n "$mode" ]] || usage
  (( platform_count <= 1 )) || die '--platform may be given at most once'
  if [[ -n "$platform" ]] && ! is_known_platform "$platform"; then
    die "unknown platform: $platform"
  fi

  case "$mode" in
    check)
      do_check "$platform"
      ;;
    activate)
      [[ -n "$platform" ]] || die '--activate requires exactly one --platform'
      do_activate "$platform"
      ;;
  esac
}

main "$@"
