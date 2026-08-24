#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly FABLE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPOSITORY_ROOT="$(cd "${FABLE_ROOT}/.." && pwd)"
readonly MANIFEST="${FABLE_ROOT}/platforms.yaml"
readonly MATERIALIZED_ROOT="${FABLE_ROOT}/platforms"
readonly EXPECTED_REPOSITORY_ROOT='/Users/kelvin/VibeCoding-WorkSpace/skill'

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 2
}

usage() {
  printf 'Usage: %s --check|--write\n' "$0" >&2
  exit 2
}

[[ "$REPOSITORY_ROOT" == "$EXPECTED_REPOSITORY_ROOT" ]] || die 'repository path guard failed'
[[ -f "$MANIFEST" && ! -L "$MANIFEST" ]] || die 'platform manifest is missing or is a symlink'

manifest_records() {
  ruby -ryaml -e '
    path = ARGV.fetch(0)
    data = YAML.safe_load(File.read(path), permitted_classes: [], aliases: false)
    raise "manifest must be a mapping" unless data.is_a?(Hash)
    raise "schema_version must be 1" unless data.fetch("schema_version") == 1
    raise "repository_root mismatch" unless data.fetch("repository_root") == "/Users/kelvin/VibeCoding-WorkSpace/skill"
    raise "materialized_root mismatch" unless data.fetch("materialized_root") == "fable-method/platforms"

    def rel(value, label)
      raise "#{label} must be a relative path" unless value.is_a?(String) && !value.empty? && !value.start_with?("/")
      parts = value.split("/")
      raise "#{label} contains an unsafe path segment" if parts.any? { |part| part.empty? || part == "." || part == ".." }
      raise "#{label} contains a tab or newline" if value.include?("\t") || value.include?("\n")
      value
    end

    shared = data.fetch("shared")
    raise "shared must be a mapping" unless shared.is_a?(Hash)
    skill = rel(shared.fetch("skill"), "shared.skill")
    raise "shared.skill mismatch" unless skill == "fable-method/shared/SKILL.md"
    refs = shared.fetch("references")
    raise "shared.references must be a non-empty array" unless refs.is_a?(Array) && !refs.empty?
    puts "SHARED_SKILL\t#{skill}"
    refs.each { |ref| puts "SHARED_REF\t#{rel(ref, "shared reference")}" }

    platforms = data.fetch("platforms")
    raise "platforms must be an array" unless platforms.is_a?(Array)
    names = platforms.map { |p| p.fetch("name") }.sort
    raise "platforms must be exactly antigravity, claude, codex, gemini" unless names == %w[antigravity claude codex gemini]
    platforms.each do |platform|
      name = platform.fetch("name")
      raise "invalid platform name" unless %w[antigravity codex claude gemini].include?(name)
      frontmatter = rel(platform.fetch("frontmatter_source"), "#{name}.frontmatter_source")
      destination = rel(platform.fetch("materialized_destination"), "#{name}.materialized_destination")
      expected_destination = name == "antigravity" ? "fable-method/platforms/antigravity" : "fable-method/platforms/#{name}/fable-method"
      raise "#{name} destination mismatch" unless destination == expected_destination
      live = platform.fetch("live_installation_path")
      raise "#{name} live installation path must be absolute metadata" unless live.is_a?(String) && live.start_with?("/")
      raise "#{name} live installation role mismatch" unless platform.fetch("live_installation_role") == "deployment_metadata_only"
      puts "PLATFORM\t#{name}\t#{frontmatter}\t#{destination}\t#{live}"

      adapters = platform.fetch("adapter_sources", [])
      raise "#{name}.adapter_sources must be an array" unless adapters.is_a?(Array)
      adapters.each do |adapter|
        adapter = rel(adapter, "#{name}.adapter_source")
        raise "#{name} adapter is outside its source area" unless adapter.start_with?("fable-method/shared/platforms/#{name}/")
        puts "ADAPTER\t#{name}\t#{adapter}"
      end

      overrides = platform.fetch("reference_overrides", [])
      raise "#{name}.reference_overrides must be an array" unless overrides.is_a?(Array)
      overrides.each do |override|
        source = rel(override.fetch("source"), "#{name}.override.source")
        target = rel(override.fetch("destination"), "#{name}.override.destination")
        raise "#{name} override source is outside its source area" unless source.start_with?("fable-method/shared/platforms/#{name}/")
        raise "#{name} override destination must be under references" unless target.start_with?("references/")
        puts "OVERRIDE\t#{name}\t#{source}\t#{target}"
      end
    end
  ' "$MANIFEST"
}

readonly CONFIG_TEXT="$(manifest_records)"

config_lines() {
  printf '%s\n' "$CONFIG_TEXT"
}

repo_path() {
  printf '%s/%s\n' "$REPOSITORY_ROOT" "$1"
}

shared_skill_rel() {
  config_lines | awk -F '\t' '$1 == "SHARED_SKILL" { print $2; exit }'
}

shared_refs() {
  config_lines | awk -F '\t' '$1 == "SHARED_REF" { print $2 }'
}

platform_names() {
  config_lines | awk -F '\t' '$1 == "PLATFORM" { print $2 }'
}

platform_field() {
  local name="$1"
  local field="$2"
  config_lines | awk -F '\t' -v n="$name" -v f="$field" '$1 == "PLATFORM" && $2 == n { print $f; exit }'
}

platform_adapters() {
  local name="$1"
  config_lines | awk -F '\t' -v n="$name" '$1 == "ADAPTER" && $2 == n { print $3 }'
}

platform_overrides() {
  local name="$1"
  config_lines | awk -F '\t' -v n="$name" '$1 == "OVERRIDE" && $2 == n { print $3 "\t" $4 }'
}

assert_file() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" ]] || die "required source is missing or is a symlink: $path"
}

reject_symlinks() {
  local root="$1"
  local link
  link="$(find "$root" -type l -print -quit)"
  [[ -z "$link" ]] || die "symlink is not allowed in managed source: $link"
}

reject_materialization_symlink_components() {
  local path="$1"
  while [[ "$path" != "$FABLE_ROOT" ]]; do
    [[ ! -L "$path" ]] || die "symlink is not allowed in materialization path: $path"
    path="$(dirname "$path")"
  done
  [[ ! -L "$FABLE_ROOT" ]] || die "symlink is not allowed in materialization path: $FABLE_ROOT"
}

validate_frontmatter() {
  local platform="$1"
  local path="$2"
  ruby -ryaml -e '
    platform, path = ARGV
    content = File.read(path)
    match = /\A---\n(.*?)\n---\n?\z/m.match(content)
    raise "frontmatter must be a complete block" unless match
    data = YAML.safe_load(match[1], permitted_classes: [], aliases: false)
    raise "frontmatter must be a mapping" unless data.is_a?(Hash)
    required = %w[name description]
    allowed = platform == "gemini" ? %w[description name trigger] : required
    raise "unexpected frontmatter keys" unless (data.keys.map(&:to_s) - allowed).empty? && required.all? { |key| data[key].is_a?(String) }
    raise "frontmatter name is not hyphen-case" unless /\A[a-z0-9]+(?:-[a-z0-9]+)*\z/.match?(data.fetch("name"))
    description = data.fetch("description")
    raise "frontmatter description contains angle brackets" if description.include?("<") || description.include?(">")
    raise "frontmatter description is too long" if description.length > 1024
    if platform == "gemini"
      raise "Gemini trigger mismatch" unless data.fetch("trigger") == "/fable-method"
    end
  ' "$platform" "$path"
}

validate_sources() {
  local skill_rel
  skill_rel="$(shared_skill_rel)"
  assert_file "$(repo_path "$skill_rel")"
  reject_symlinks "$(repo_path fable-method/shared)"
  local skill_lines
  skill_lines="$(wc -l < "$(repo_path "$skill_rel")")"
  (( skill_lines < 500 )) || die "shared SKILL.md is not below 500 lines"

  local ref rel
  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    assert_file "$(repo_path "$ref")"
    rel="${ref#fable-method/shared/}"
    [[ "$rel" == references/* ]] || die "shared reference is outside references: $ref"
  done < <(shared_refs)

  local platform frontmatter adapter source override
  while IFS= read -r platform; do
    [[ -n "$platform" ]] || continue
    frontmatter="$(repo_path "$(platform_field "$platform" 3)")"
    assert_file "$frontmatter"
    validate_frontmatter "$platform" "$frontmatter"
    while IFS= read -r adapter; do
      [[ -n "$adapter" ]] || continue
      assert_file "$(repo_path "$adapter")"
    done < <(platform_adapters "$platform")
    while IFS=$'\t' read -r source override; do
      [[ -n "$source" ]] || continue
      assert_file "$(repo_path "$source")"
      [[ "$override" == references/* ]] || die "override destination is outside references: $override"
    done < <(platform_overrides "$platform")
  done < <(platform_names)
}

render_skill() {
  local platform="$1"
  local frontmatter adapter
  frontmatter="$(repo_path "$(platform_field "$platform" 3)")"
  awk '
    NR == 1 && $0 == "---" { in_frontmatter = 1; next }
    in_frontmatter && $0 == "---" { in_frontmatter = 0; body = 1; next }
    body { print }
  ' "$(repo_path "$(shared_skill_rel)")"
  while IFS= read -r adapter; do
    [[ -n "$adapter" ]] || continue
    printf '\n'
    awk '{ print }' "$(repo_path "$adapter")"
  done < <(platform_adapters "$platform")
}

render_frontmatter_and_skill() {
  local platform="$1"
  local frontmatter
  frontmatter="$(repo_path "$(platform_field "$platform" 3)")"
  awk '{ print }' "$frontmatter"
  render_skill "$platform"
}

shared_destination() {
  local ref="$1"
  printf '%s\n' "${ref#fable-method/shared/}"
}

# Antigravity requires a plugin.json manifest at the destination root with
# skill content nested under skills/<skill-name>/ (confirmed via `agy plugin
# validate`); the other platforms consume a bare SKILL.md at the destination
# root itself. This is the one seam where "destination" (the whole platform
# materialization) and "skill root" (where SKILL.md/references land) differ.
platform_skill_root() {
  local platform="$1"
  local destination="$2"
  if [[ "$platform" == "antigravity" ]]; then
    printf '%s/skills/fable-method\n' "$destination"
  else
    printf '%s\n' "$destination"
  fi
}

platform_plugin_manifest_path() {
  local platform="$1"
  local destination="$2"
  [[ "$platform" == "antigravity" ]] || return 1
  printf '%s/plugin.json\n' "$destination"
}

render_plugin_manifest() {
  printf '{"name": "fable-method"}\n'
}

is_expected_file() {
  local platform="$1"
  local rel="$2"
  if [[ "$platform" == "antigravity" ]]; then
    [[ "$rel" == "plugin.json" ]] && return 0
    local skill_prefix="skills/fable-method/"
    [[ "$rel" == "$skill_prefix"* ]] || return 1
    rel="${rel#"$skill_prefix"}"
  fi
  [[ "$rel" == "SKILL.md" ]] && return 0
  local ref
  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    [[ "$(shared_destination "$ref")" == "$rel" ]] && return 0
  done < <(shared_refs)
  local source target
  while IFS=$'\t' read -r source target; do
    [[ -n "$source" ]] || continue
    [[ "$target" == "$rel" ]] && return 0
  done < <(platform_overrides "$platform")
  return 1
}

check_destination() {
  local platform="$1"
  local destination
  destination="$(repo_path "$(platform_field "$platform" 4)")"
  reject_materialization_symlink_components "$destination"
  local drift=0
  if [[ ! -d "$destination" || -L "$destination" ]]; then
    printf 'MISSING_DESTINATION: %s\n' "$platform"
    return 1
  fi
  local link
  link="$(find "$destination" -type l -print -quit)"
  if [[ -n "$link" ]]; then
    printf 'SYMLINK_DESTINATION: %s\n' "$platform"
    drift=1
  fi
  local manifest
  if manifest="$(platform_plugin_manifest_path "$platform" "$destination")"; then
    if [[ ! -f "$manifest" ]] || ! cmp -s <(render_plugin_manifest) "$manifest"; then
      printf 'CHANGED: %s/plugin.json\n' "$platform"
      drift=1
    fi
  fi
  local skill_root
  skill_root="$(platform_skill_root "$platform" "$destination")"
  if [[ ! -f "$skill_root/SKILL.md" ]] || ! cmp -s <(render_frontmatter_and_skill "$platform") "$skill_root/SKILL.md"; then
    printf 'CHANGED: %s/SKILL.md\n' "$platform"
    drift=1
  fi
  local ref target source rel
  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    target="$skill_root/$(shared_destination "$ref")"
    if [[ ! -f "$target" ]] || ! cmp -s "$(repo_path "$ref")" "$target"; then
      printf 'CHANGED: %s/%s\n' "$platform" "$(shared_destination "$ref")"
      drift=1
    fi
  done < <(shared_refs)
  while IFS=$'\t' read -r source rel; do
    [[ -n "$source" ]] || continue
    target="$skill_root/$rel"
    if [[ ! -f "$target" ]] || ! cmp -s "$(repo_path "$source")" "$target"; then
      printf 'CHANGED: %s/%s\n' "$platform" "$rel"
      drift=1
    fi
  done < <(platform_overrides "$platform")
  while IFS= read -r source; do
    [[ -n "$source" ]] || continue
    rel="${source#"$destination"/}"
    if ! is_expected_file "$platform" "$rel"; then
      printf 'EXTRA: %s/%s\n' "$platform" "$rel"
      drift=1
    fi
  done < <(find "$destination" -type f -print)
  if (( drift == 0 )); then
    printf 'NO_DRIFT: %s\n' "$platform"
    return 0
  fi
  return 1
}

remove_unexpected_materialization() {
  local platform="$1"
  local destination="$2"
  [[ -d "$destination" && ! -L "$destination" ]] || return 0
  local path rel
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    rel="${path#"$destination"/}"
    if ! is_expected_file "$platform" "$rel"; then
      rm -f "$path"
    fi
  done < <(find "$destination" -type f -print)
  find "$destination" -type l -delete
  find "$destination" -mindepth 1 -depth -type d -empty -delete
}

write_if_changed() {
  local source="$1"
  local target="$2"
  if [[ -f "$target" ]] && cmp -s "$source" "$target"; then
    return 0
  fi
  cp "$source" "$target"
}

write_skill_if_changed() {
  local platform="$1"
  local target="$2"
  if [[ -f "$target" ]] && cmp -s <(render_frontmatter_and_skill "$platform") "$target"; then
    return 0
  fi
  render_frontmatter_and_skill "$platform" > "$target"
}

write_materialization() {
  local platform="$1"
  local destination
  destination="$(repo_path "$(platform_field "$platform" 4)")"
  reject_materialization_symlink_components "$destination"
  mkdir -p "$destination"
  remove_unexpected_materialization "$platform" "$destination"
  local manifest
  if manifest="$(platform_plugin_manifest_path "$platform" "$destination")"; then
    if [[ ! -f "$manifest" ]] || ! cmp -s <(render_plugin_manifest) "$manifest"; then
      render_plugin_manifest > "$manifest"
    fi
  fi
  local skill_root
  skill_root="$(platform_skill_root "$platform" "$destination")"
  mkdir -p "$skill_root"
  write_skill_if_changed "$platform" "$skill_root/SKILL.md"
  local ref target parent source rel
  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    rel="$(shared_destination "$ref")"
    target="$skill_root/$rel"
    parent="$(dirname "$target")"
    mkdir -p "$parent"
    write_if_changed "$(repo_path "$ref")" "$target"
  done < <(shared_refs)
  while IFS=$'\t' read -r source rel; do
    [[ -n "$source" ]] || continue
    target="$skill_root/$rel"
    parent="$(dirname "$target")"
    mkdir -p "$parent"
    write_if_changed "$(repo_path "$source")" "$target"
  done < <(platform_overrides "$platform")
  printf 'WRITTEN: %s\n' "$platform"
}

mode="${1:-}"
case "$mode" in
  --check)
    [[ "$#" == 1 ]] || usage
    validate_sources
    status=0
    while IFS= read -r platform; do
      [[ -n "$platform" ]] || continue
      check_destination "$platform" || status=1
    done < <(platform_names)
    exit "$status"
    ;;
  --write)
    [[ "$#" == 1 ]] || usage
    validate_sources
    while IFS= read -r platform; do
      [[ -n "$platform" ]] || continue
      write_materialization "$platform"
    done < <(platform_names)
    ;;
  *)
    usage
    ;;
esac
