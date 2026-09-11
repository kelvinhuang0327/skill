#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly FABLE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPOSITORY_ROOT="$(cd "${FABLE_ROOT}/.." && pwd)"
readonly MANIFEST="${FABLE_ROOT}/platforms.yaml"
readonly MATERIALIZED_ROOT="${FABLE_ROOT}/platforms"
readonly EXPECTED_REPOSITORY_ROOT='/Users/kelvin/VibeCoding-WorkSpace/skill'
readonly TRUSTED_GIT_PATH='/usr/bin:/bin:/usr/sbin:/sbin'

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 2
}

usage() {
  printf 'Usage: %s --check|--write [--skill fable-method|fable-judge]\n' "$0" >&2
  exit 2
}

canonical_directory() {
  local path="$1"
  [[ -d "$path" ]] || return 1
  (cd "$path" 2>/dev/null && pwd -P)
}

git_identity() {
  [[ -x /usr/bin/env ]] || return 1
  /usr/bin/env -i PATH="$TRUSTED_GIT_PATH" git "$@"
}

git_toplevel() {
  git_identity -C "$1" rev-parse --show-toplevel 2>/dev/null
}

git_common_directory() {
  local root="$1"
  local common_dir
  common_dir="$(git_identity -C "$root" rev-parse --git-common-dir 2>/dev/null)" || return 1
  if [[ "$common_dir" == /* ]]; then
    canonical_directory "$common_dir"
  else
    canonical_directory "$root/$common_dir"
  fi
}

assert_repository_identity() {
  local repository_root canonical_root repository_toplevel canonical_toplevel
  local repository_common_dir canonical_common_dir

  repository_root="$(canonical_directory "$REPOSITORY_ROOT")" \
    || die 'repository identity guard failed: executing repository root cannot be canonicalized'
  canonical_root="$(canonical_directory "$EXPECTED_REPOSITORY_ROOT")" \
    || die 'repository identity guard failed: canonical repository root cannot be canonicalized'
  repository_toplevel="$(git_toplevel "$repository_root")" \
    || die 'repository identity guard failed: executing repository root is not a Git repository'
  canonical_toplevel="$(git_toplevel "$canonical_root")" \
    || die 'repository identity guard failed: canonical repository root is not a Git repository'
  repository_toplevel="$(canonical_directory "$repository_toplevel")" \
    || die 'repository identity guard failed: executing Git top-level cannot be canonicalized'
  canonical_toplevel="$(canonical_directory "$canonical_toplevel")" \
    || die 'repository identity guard failed: canonical Git top-level cannot be canonicalized'

  [[ "$repository_root" == "$repository_toplevel" ]] \
    || die 'repository identity guard failed: executing repository root is not its Git top-level'
  [[ "$canonical_root" == "$canonical_toplevel" ]] \
    || die 'repository identity guard failed: canonical repository root is not its Git top-level'

  repository_common_dir="$(git_common_directory "$repository_root")" \
    || die 'repository identity guard failed: executing Git common directory cannot be resolved'
  canonical_common_dir="$(git_common_directory "$canonical_root")" \
    || die 'repository identity guard failed: canonical Git common directory cannot be resolved'

  [[ "$repository_common_dir" == "$canonical_common_dir" ]] \
    || die 'repository identity guard failed: Git common directory does not match canonical repository'
}

mode=""
skill="fable-method"
skill_count=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check|--write)
      [[ -z "$mode" ]] || die 'cannot combine modes'
      mode="$1"
      shift
      ;;
    --skill)
      [[ $# -ge 2 ]] || die '--skill requires a value'
      skill="$2"
      skill_count=$((skill_count + 1))
      shift 2
      ;;
    *) usage ;;
  esac
done
[[ -n "$mode" ]] || usage
(( skill_count <= 1 )) || die '--skill may be given at most once'
assert_repository_identity
exec ruby "$SCRIPT_DIR/platform_manifest.rb" --sync "$MANIFEST" "$skill" "$REPOSITORY_ROOT" "$mode"
