#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_ROOT='/Users/kelvin/VibeCoding-WorkSpace/skill'
readonly CANONICAL_SOURCE='/Users/kelvin/VibeCoding-WorkSpace/skill/fable-method/platforms/codex/fable-method'
readonly LIVE_DESTINATION='/Users/kelvin/.codex/skills/fable-method'

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 2
}

guard_exact_paths() {
  [[ "$REPOSITORY_ROOT" == '/Users/kelvin/VibeCoding-WorkSpace/skill' ]] || die 'repository path guard failed'
  [[ "$CANONICAL_SOURCE" == '/Users/kelvin/VibeCoding-WorkSpace/skill/fable-method/platforms/codex/fable-method' ]] || die 'canonical path guard failed'
  [[ "$LIVE_DESTINATION" == '/Users/kelvin/.codex/skills/fable-method' ]] || die 'live path guard failed'
  [[ "$CANONICAL_SOURCE" != "$LIVE_DESTINATION" ]] || die 'source and destination must differ'
}

reject_symlinks() {
  local root="$1"
  local link
  link="$(find "$root" -type l -print -quit)"
  [[ -z "$link" ]] || die "symlink is not allowed: $link"
}

validate_source() {
  guard_exact_paths
  [[ -d "$CANONICAL_SOURCE" && ! -L "$CANONICAL_SOURCE" ]] || die 'canonical source directory is missing or is a symlink'
  [[ -f "$CANONICAL_SOURCE/SKILL.md" ]] || die 'canonical source is missing SKILL.md'
  reject_symlinks "$CANONICAL_SOURCE"
  ruby -ryaml -e '
    path = ARGV.fetch(0)
    content = File.read(path)
    match = /\A---\n(.*?)\n---/m.match(content)
    raise "invalid frontmatter format" unless match
    data = YAML.safe_load(match[1], aliases: false)
    raise "frontmatter must be a mapping" unless data.is_a?(Hash)
    keys = data.keys.map(&:to_s).sort
    raise "frontmatter keys must be exactly name and description" unless keys == %w[description name]
    name = data.fetch("name")
    description = data.fetch("description")
    raise "frontmatter name must be a string" unless name.is_a?(String)
    raise "frontmatter description must be a string" unless description.is_a?(String)
    raise "frontmatter name is not hyphen-case" unless /\A[a-z0-9]+(?:-[a-z0-9]+)*\z/.match?(name)
    raise "frontmatter description contains angle brackets" if description.include?("<") || description.include?(">")
    raise "frontmatter description is too long" if description.length > 1024
  ' "$CANONICAL_SOURCE/SKILL.md"
}

relative_files() {
  local root="$1"
  (cd "$root" && find . -type f -print | sed 's#^\./##' | LC_ALL=C sort)
}

relative_directories() {
  local root="$1"
  (cd "$root" && find . -mindepth 1 -type d -print | sed 's#^\./##' | LC_ALL=C sort)
}

check_tree() {
  guard_exact_paths
  [[ -d "$LIVE_DESTINATION" && ! -L "$LIVE_DESTINATION" ]] || {
    printf 'MISSING_DESTINATION: %s\n' "$LIVE_DESTINATION"
    return 1
  }
  reject_symlinks "$LIVE_DESTINATION"

  local drift=0
  local rel
  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    if [[ ! -e "$LIVE_DESTINATION/$rel" ]]; then
      printf 'MISSING: %s\n' "$rel"
      drift=1
    elif [[ ! -f "$LIVE_DESTINATION/$rel" ]]; then
      printf 'TYPE_DRIFT: %s\n' "$rel"
      drift=1
    elif ! cmp -s "$CANONICAL_SOURCE/$rel" "$LIVE_DESTINATION/$rel"; then
      printf 'CHANGED: %s\n' "$rel"
      drift=1
    fi
  done < <(relative_files "$CANONICAL_SOURCE")

  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    if [[ ! -e "$CANONICAL_SOURCE/$rel" ]]; then
      printf 'EXTRA: %s\n' "$rel"
      drift=1
    fi
  done < <(relative_files "$LIVE_DESTINATION")

  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    if [[ ! -d "$LIVE_DESTINATION/$rel" ]]; then
      printf 'MISSING_DIRECTORY: %s\n' "$rel"
      drift=1
    fi
  done < <(relative_directories "$CANONICAL_SOURCE")

  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    if [[ ! -d "$CANONICAL_SOURCE/$rel" ]]; then
      printf 'EXTRA_DIRECTORY: %s\n' "$rel"
      drift=1
    fi
  done < <(relative_directories "$LIVE_DESTINATION")

  if (( drift == 0 )); then
    printf 'NO_DRIFT: %s\n' "$LIVE_DESTINATION"
    return 0
  fi
  return 1
}

require_clean_subtree() {
  local status
  status="$(git -C "$REPOSITORY_ROOT" status --porcelain=v1 --untracked-files=all -- fable-method)"
  [[ -z "$status" ]] || die 'fable-method Git subtree is dirty; deploy refused'
}

deploy() {
  validate_source
  require_clean_subtree
  [[ -d "$LIVE_DESTINATION" && ! -L "$LIVE_DESTINATION" ]] || die 'live destination directory is missing or is a symlink'
  reject_symlinks "$LIVE_DESTINATION"
  rsync -a --delete "$CANONICAL_SOURCE/" "$LIVE_DESTINATION/"
  printf 'DEPLOYED: %s -> %s\n' "$CANONICAL_SOURCE" "$LIVE_DESTINATION"
}

usage() {
  printf 'Usage: %s --check|--deploy\n' "$0" >&2
  exit 2
}

case "${1:-}" in
  --check)
    validate_source
    check_tree
    ;;
  --deploy)
    deploy
    ;;
  *)
    usage
    ;;
esac
