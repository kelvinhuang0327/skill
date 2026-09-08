#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly FABLE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPOSITORY_ROOT="$(cd "${FABLE_ROOT}/.." && pwd)"
readonly CANONICAL_REPOSITORY_ROOT='/Users/kelvin/VibeCoding-WorkSpace/skill'
readonly USER_HOME='/Users/kelvin'
readonly MANIFEST="${FABLE_ROOT}/platforms.yaml"
readonly SYNC_SCRIPT="${SCRIPT_DIR}/sync-platforms.sh"
readonly PLATFORMS=(codex claude gemini antigravity)
readonly TRUSTED_GIT_PATH='/usr/bin:/bin:/usr/sbin:/sbin'
readonly ACTIVATION_LOCK="${USER_HOME}/.fable-method-activation.lock"

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 2
}

usage() {
  cat >&2 <<'EOF'
Usage:
  activate-live.sh --help
  activate-live.sh --check [--platform codex|claude|gemini|antigravity]
  activate-live.sh --activate --platform codex|claude|gemini|antigravity
  activate-live.sh --activate --platform codex --replace-reviewed-local-drift \
      --expected-live-sha256 <64-hex> --expected-canonical-head <40-hex> \
      --expected-canonical-tree <40-hex> --expected-materialization-tree <40-hex>
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

  --activate --platform codex --replace-reviewed-local-drift
                                 Codex only. Overwrites a live installation the
                                 ordinary path refuses (LOCAL_DRIFT) provided the
                                 caller supplies the exact identity of what is
                                 being replaced: the current live bundle's
                                 LIVE_BUNDLE_SHA256_SERIALIZATION_V1 digest via
                                 --expected-live-sha256, and the activation
                                 source's commit/tree/materialization-tree via
                                 --expected-canonical-head, --expected-canonical-tree,
                                 and --expected-materialization-tree. Every one of
                                 the four --expected-* values is required exactly
                                 once and is revalidated, still inside the single
                                 activation lock, immediately before the first
                                 write; any mismatch takes zero live writes. There
                                 is no target-path override, no non-Codex use, and
                                 no combination with --check.

Running --activate against a real installation requires authorization from the
Owner obtained outside this script. This script performs no conversational or
environment-based authorization check of its own - passing the flag is the only
gate it enforces. There is no --activate-all, --force, --overwrite, --yes, or
--skip-check mode.

Platforms: codex, claude, gemini, antigravity. --check accepts zero or one
--platform; --activate requires exactly one.
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
    antigravity) printf '%s\n' 'fable-method/platforms/antigravity/fable-method' ;;
    *) die "unknown platform: $1" ;;
  esac
}

platform_live_path() {
  case "$1" in
    codex) printf '%s\n' '/Users/kelvin/.codex/skills/fable-method' ;;
    claude) printf '%s\n' '/Users/kelvin/.claude/skills/fable-method' ;;
    gemini) printf '%s\n' '/Users/kelvin/.gemini/skills/fable-method' ;;
    antigravity) printf '%s\n' '/Users/kelvin/.gemini/config/skills/fable-method' ;;
    *) die "unknown platform: $1" ;;
  esac
}

repo_path() {
  printf '%s/%s\n' "$REPOSITORY_ROOT" "$1"
}

canonical_directory() {
  local path="$1"
  [[ -d "$path" ]] || return 1
  (cd "$path" 2>/dev/null && pwd -P)
}

git_identity() {
  [[ -x /usr/bin/env ]] || return 1
  /usr/bin/env -i PATH="$TRUSTED_GIT_PATH" GIT_OPTIONAL_LOCKS=0 git "$@"
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

reject_git_environment_spoof() {
  [[ -z "${GIT_DIR+x}" ]] \
    || die 'repository identity guard failed: GIT_DIR override is not allowed'
  [[ -z "${GIT_WORK_TREE+x}" ]] \
    || die 'repository identity guard failed: GIT_WORK_TREE override is not allowed'
  [[ -z "${GIT_COMMON_DIR+x}" ]] \
    || die 'repository identity guard failed: GIT_COMMON_DIR override is not allowed'
  [[ -z "${GIT_OBJECT_DIRECTORY+x}" ]] \
    || die 'repository identity guard failed: GIT_OBJECT_DIRECTORY override is not allowed'
  [[ -z "${GIT_ALTERNATE_OBJECT_DIRECTORIES+x}" ]] \
    || die 'repository identity guard failed: GIT_ALTERNATE_OBJECT_DIRECTORIES override is not allowed'
  [[ -z "${GIT_INDEX_FILE+x}" ]] \
    || die 'repository identity guard failed: GIT_INDEX_FILE override is not allowed'
}

assert_repository_identity() {
  local repository_root canonical_root repository_toplevel canonical_toplevel
  local repository_common_dir canonical_common_dir

  repository_root="$(canonical_directory "$REPOSITORY_ROOT")" \
    || die 'repository identity guard failed: executing repository root cannot be canonicalized'
  canonical_root="$(canonical_directory "$CANONICAL_REPOSITORY_ROOT")" \
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

# Global single-writer gate for activation. BSD flock(2) through the documented
# /usr/bin/lockf descriptor form: the lock lives on this process's open file
# description for fd 9, so the kernel releases it when this process exits or
# dies. The zero-byte lock file is never removed and its mere existence is
# never a held lock, so no stale-lock cleanup exists or is needed.
acquire_activation_lock() {
  [[ ! -L "$ACTIVATION_LOCK" ]] || die 'ACTIVATION_LOCK_PATH_IS_SYMLINK'
  exec 9>>"$ACTIVATION_LOCK"
  if ! /usr/bin/lockf -s -t 0 9; then
    printf 'ACTIVATION_LOCK_BUSY\n' >&2
    printf '  lock: %s\n' "$ACTIVATION_LOCK" >&2
    exit 2
  fi
}

guard_repository_root() {
  reject_git_environment_spoof
  assert_repository_identity
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
    raise "platforms must be exactly antigravity, codex, claude, gemini" unless names.sort == %w[antigravity claude codex gemini]
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
  if ! output="$(/bin/bash "$SYNC_SCRIPT" --check 2>&1)"; then
    printf '%s\n' "$output" >&2
    die 'CANONICAL_MATERIALIZATION_DRIFT: sync-platforms.sh --check did not pass'
  fi
}

current_branch() {
  local branch
  if branch="$(git_identity -C "$REPOSITORY_ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null)"; then
    printf '%s\n' "$branch"
  else
    printf '%s\n' 'DETACHED'
  fi
}

activation_source_head() {
  git_identity -C "$REPOSITORY_ROOT" rev-parse --verify 'HEAD^{commit}' 2>/dev/null
}

canonical_master_head() {
  git_identity -C "$REPOSITORY_ROOT" rev-parse --verify 'refs/remotes/origin/master^{commit}' 2>/dev/null
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
  FABLE_UNTRACKED_COUNT=0
  UNRELATED_STAGED_COUNT=0
  UNRELATED_TRACKED_DIRTY_COUNT=0
  UNRELATED_UNTRACKED_COUNT=0

  local status_file
  status_file="$(mktemp)" || die 'unable to read repository status'

  if ! git_identity -C "$REPOSITORY_ROOT" status --porcelain=v2 --untracked-files=all -z >"$status_file"; then
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
      '?')
        local path="${rec#\? }"
        if path_in_fable_scope "$path"; then
          FABLE_UNTRACKED_COUNT=$((FABLE_UNTRACKED_COUNT + 1))
        else
          UNRELATED_UNTRACKED_COUNT=$((UNRELATED_UNTRACKED_COUNT + 1))
        fi
        i=$((i + 1))
        ;;
      '!')
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
  local branch source_head canonical_head
  branch="$(current_branch)"
  source_head="$(activation_source_head)" \
    || die 'ACTIVATION_SOURCE_HEAD_UNRESOLVED: executing worktree HEAD is not a commit'
  canonical_head="$(canonical_master_head)" \
    || die 'CANONICAL_MASTER_REF_UNRESOLVED: refs/remotes/origin/master is not a commit'
  classify_repository_dirty_state
  if [[ "$source_head" != "$canonical_head" || "$FABLE_STAGED_COUNT" -ne 0 \
        || "$FABLE_TRACKED_DIRTY_COUNT" -ne 0 || "$FABLE_UNTRACKED_COUNT" -ne 0 ]]; then
    printf 'ACTIVATION_REPOSITORY_STATE_NOT_READY\n' >&2
    printf '  branch: %s (informational; detached HEAD is allowed)\n' "$branch" >&2
    printf '  activation_source_head: %s\n' "$source_head" >&2
    printf '  canonical_master_head: %s\n' "$canonical_head" >&2
    printf '  fable_staged: %s (required: 0)\n' "$FABLE_STAGED_COUNT" >&2
    printf '  fable_tracked_dirty: %s (required: 0)\n' "$FABLE_TRACKED_DIRTY_COUNT" >&2
    printf '  fable_untracked: %s (required: 0)\n' "$FABLE_UNTRACKED_COUNT" >&2
    if [[ "$UNRELATED_STAGED_COUNT" -ne 0 || "$UNRELATED_TRACKED_DIRTY_COUNT" -ne 0 \
          || "$UNRELATED_UNTRACKED_COUNT" -ne 0 ]]; then
      printf '  unrelated_staged: %s (not blocking)\n' "$UNRELATED_STAGED_COUNT" >&2
      printf '  unrelated_tracked_dirty: %s (not blocking)\n' "$UNRELATED_TRACKED_DIRTY_COUNT" >&2
      printf '  unrelated_untracked: %s (not blocking)\n' "$UNRELATED_UNTRACKED_COUNT" >&2
    fi
    exit 2
  fi
}

report_canonical_repository_state_for_check() {
  local branch source_head canonical_head
  branch="$(current_branch)"
  source_head="$(activation_source_head)" \
    || die 'ACTIVATION_SOURCE_HEAD_UNRESOLVED: executing worktree HEAD is not a commit'
  canonical_head="$(canonical_master_head)" \
    || die 'CANONICAL_MASTER_REF_UNRESOLVED: refs/remotes/origin/master is not a commit'
  classify_repository_dirty_state
  if [[ "$source_head" != "$canonical_head" || "$FABLE_STAGED_COUNT" -ne 0 \
        || "$FABLE_TRACKED_DIRTY_COUNT" -ne 0 || "$FABLE_UNTRACKED_COUNT" -ne 0 \
        || "$UNRELATED_STAGED_COUNT" -ne 0 || "$UNRELATED_TRACKED_DIRTY_COUNT" -ne 0 \
        || "$UNRELATED_UNTRACKED_COUNT" -ne 0 ]]; then
    printf 'CANONICAL_REPOSITORY_STATE_NOTE: branch=%s activation_source_head=%s canonical_master_head=%s fable_staged=%s fable_tracked_dirty=%s fable_untracked=%s unrelated_staged=%s unrelated_tracked_dirty=%s unrelated_untracked=%s\n' \
      "$branch" "$source_head" "$canonical_head" "$FABLE_STAGED_COUNT" "$FABLE_TRACKED_DIRTY_COUNT" \
      "$FABLE_UNTRACKED_COUNT" "$UNRELATED_STAGED_COUNT" "$UNRELATED_TRACKED_DIRTY_COUNT" \
      "$UNRELATED_UNTRACKED_COUNT"
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

# Metadata-only inventory of the pinned HEAD history, including directories.
# NUL delimiters preserve raw path bytes. Failures propagate through pipefail;
# no historical blob content is read to establish which live paths are known.
reviewed_replacement_known_entries() {
  local rel commits commit
  rel="$(platform_materialized_rel codex)"
  commits="$(git_identity -C "$REPOSITORY_ROOT" log --format=%H HEAD -- "$rel")" || return 1
  [[ -n "$commits" ]] || return 1
  while IFS= read -r commit; do
    git_identity -C "$REPOSITORY_ROOT" ls-tree -r -t -z "$commit" -- "$rel" || return 1
  done <<<"$commits"
}

# LIVE_BUNDLE_SHA256_SERIALIZATION_V1. First inventory all path names/types
# against known Codex history, before opening ANY live content. Reject unknown
# paths, symlinks, special files, and known paths with unexpected types.
# Re-lstat before reading, open regular files without following symlinks, and
# verify descriptors and the final inventory against the original metadata.
# Records sort by raw relative-path bytes (including root ".") and concatenate:
# 1 byte D/F + 4-byte BE path-length + path bytes + 4-byte BE (mode & 07777)
# + 32-byte SHA256 content digest (F) or 32 zero bytes (D). L is SHA256 of
# that stream, lowercase hex. Size and mtime never substitute for file bytes.
compute_live_bundle_sha256() {
  local root="$1"
  reviewed_replacement_known_entries | ruby -e '
    require "digest"

    def kind(stat)
      return :directory if stat.directory?
      return :file if stat.file?
      :other
    end

    def identity(stat)
      [stat.dev, stat.ino, stat.mode, stat.size, stat.mtime, stat.ctime]
    end

    begin
      root, prefix = ARGV
      prefix = prefix.b
      allowed = {".".b => [:directory]}
      STDIN.binmode.read.split("\x00").each do |entry|
        metadata, path = entry.split("\t", 2)
        mode, type, _hash = metadata.split(" ")
        # ls-tree -t emits ancestor trees on the path to the requested root.
        next if path && mode == "040000" && type == "tree" && prefix.start_with?(path + "/")
        raise "invalid history metadata" unless path && (path == prefix || path.start_with?(prefix + "/"))
        rel = path == prefix ? ".".b : path.byteslice(prefix.bytesize + 1..-1)
        expected = case [mode, type]
                   when ["040000", "tree"] then :directory
                   when ["100644", "blob"], ["100755", "blob"] then :file
                   else :other
                   end
        (allowed[rel] ||= []) << expected
      end
      raise "empty history inventory" if allowed.size == 1
      root_stat = File.lstat(root)
      raise "root is not a directory" unless kind(root_stat) == :directory
      inventory = [[".".b, root_stat]]
      walk = lambda do |dir, rel|
        Dir.children(dir, encoding: Encoding::BINARY).sort.each do |name|
          child = File.join(dir, name)
          child_rel = rel == "." ? name : "#{rel}/#{name}"
          stat = File.lstat(child)
          actual = kind(stat)
          raise "unexpected path/type: #{child_rel}" unless [:directory, :file].include?(actual)
          raise "unknown path: #{child_rel}" unless allowed.key?(child_rel)
          raise "unexpected known path type: #{child_rel}" unless allowed.fetch(child_rel).include?(actual)
          inventory << [child_rel, stat]
          walk.call(child, child_rel) if actual == :directory
        end
      end
      walk.call(root, ".".b)
      inventory.sort_by! { |rel, _stat| rel.b }
      records = inventory.map do |rel, original|
        full = rel == "." ? root : File.join(root, rel)
        fresh = File.lstat(full)
        raise "path/type changed during scan: #{rel}" unless identity(fresh) == identity(original)
        digest = if kind(original) == :file
          File.open(full, File::RDONLY | File::NOFOLLOW | File::NONBLOCK) do |file|
            raise "path/type changed before read: #{rel}" unless identity(file.stat) == identity(original)
            hash = Digest::SHA256.new
            buffer = "".b
            hash.update(buffer) while file.read(65536, buffer)
            raise "content changed during read: #{rel}" unless identity(file.stat) == identity(original)
            hash.digest
          end
        else
          "\x00" * 32
        end
        type_byte = kind(original) == :directory ? "D".b : "F".b
        type_byte + [rel.bytesize].pack("N") + rel + [original.mode & 07777].pack("N") + digest
      end
      inventory.each do |rel, original|
        full = rel == "." ? root : File.join(root, rel)
        raise "path/type changed during scan: #{rel}" unless identity(File.lstat(full)) == identity(original)
      end
      puts Digest::SHA256.hexdigest(records.join)
    rescue => e
      warn "LIVE_BUNDLE_REJECTED: #{e.message}"
      exit 1
    end
  ' "$root" "$(platform_materialized_rel codex)"
}

# Binds the reviewed-replacement Owner-supplied H/T/M to the executing
# repository, independent of and in addition to require_canonical_repository_
# state_for_activation. H must match both the activation source HEAD and
# refs/remotes/origin/master (not merely "whatever origin/master is now"); T
# must match HEAD's own tree; M must match the Codex materialization tree
# recorded inside H. Called once for initial validation and again, unchanged,
# for prewrite revalidation.
require_reviewed_replacement_canonical_identity() {
  local expected_head="$1" expected_tree="$2" expected_materialization_tree="$3"
  local source_head canonical_head source_tree materialization_tree

  source_head="$(activation_source_head)" \
    || die 'ACTIVATION_SOURCE_HEAD_UNRESOLVED: executing worktree HEAD is not a commit'
  canonical_head="$(canonical_master_head)" \
    || die 'CANONICAL_MASTER_REF_UNRESOLVED: refs/remotes/origin/master is not a commit'

  [[ "$source_head" == "$expected_head" ]] \
    || die "REVIEWED_REPLACEMENT_HEAD_MISMATCH: expected ${expected_head}, observed activation_source_head ${source_head}"
  [[ "$canonical_head" == "$expected_head" ]] \
    || die "REVIEWED_REPLACEMENT_CANONICAL_REF_MISMATCH: expected ${expected_head}, observed canonical_master_head ${canonical_head}"

  source_tree="$(git_identity -C "$REPOSITORY_ROOT" rev-parse --verify 'HEAD^{tree}' 2>/dev/null)" \
    || die 'REVIEWED_REPLACEMENT_TREE_UNRESOLVED: executing worktree HEAD^{tree} is not a tree'
  [[ "$source_tree" == "$expected_tree" ]] \
    || die "REVIEWED_REPLACEMENT_TREE_MISMATCH: expected ${expected_tree}, observed HEAD^{tree} ${source_tree}"

  materialization_tree="$(git_identity -C "$REPOSITORY_ROOT" rev-parse --verify \
    "${expected_head}:$(platform_materialized_rel codex)" 2>/dev/null)" \
    || die "REVIEWED_REPLACEMENT_MATERIALIZATION_TREE_UNRESOLVED: ${expected_head}:$(platform_materialized_rel codex) is not a tree"
  [[ "$materialization_tree" == "$expected_materialization_tree" ]] \
    || die "REVIEWED_REPLACEMENT_MATERIALIZATION_TREE_MISMATCH: expected ${expected_materialization_tree}, observed ${materialization_tree}"
  require_canonical_source_gate
  require_canonical_repository_state_for_activation
}

known_paths_for_platform() {
  local platform="$1"
  local rel commit
  rel="$(platform_materialized_rel "$platform")"
  {
    while IFS= read -r commit; do
      [[ -n "$commit" ]] || continue
      git_identity -C "$REPOSITORY_ROOT" ls-tree -r --name-only "$commit" -- "$rel" 2>/dev/null
    done < <(git_identity -C "$REPOSITORY_ROOT" log --format=%H -- "$rel")
  } | sed "s#^${rel}/##" | LC_ALL=C sort -u
}

commit_tree_entries() {
  local commit="$1" rel="$2"
  git_identity -C "$REPOSITORY_ROOT" ls-tree -r "$commit" -- "$rel" 2>/dev/null | while IFS=$'\t' read -r meta path; do
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
    hash="$(git_identity -C "$REPOSITORY_ROOT" hash-object --no-filters -- "$f")"
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
    CLASSIFY_MATCHED_COMMIT="$(activation_source_head)"
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
  done < <(git_identity -C "$REPOSITORY_ROOT" log --format=%H -- "$rel")

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

  local source_head canonical_head
  source_head="$(activation_source_head)" \
    || die 'ACTIVATION_SOURCE_HEAD_UNRESOLVED: executing worktree HEAD is not a commit'
  canonical_head="$(canonical_master_head)" \
    || die 'CANONICAL_MASTER_REF_UNRESOLVED: refs/remotes/origin/master is not a commit'

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
    printf 'CANONICAL_HEAD: %s\n' "$source_head"
    printf 'CANONICAL_MASTER_HEAD: %s\n' "$canonical_head"
    printf 'LIVE_TARGET: %s\n' "$(platform_live_path "$platform")"
    printf 'ACTIVATION_READY: %s\n' "$ready"
  done

  return "$overall"
}

do_activate() {
  acquire_activation_lock
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
      if [[ -d "$parent" ]]; then
        [[ ! -L "$parent" ]] || die 'ACTIVATION_PARENT_NOT_READY'
      else
        local grandparent
        grandparent="$(dirname "$parent")"
        [[ -d "$grandparent" && ! -L "$grandparent" ]] || die 'ACTIVATION_PARENT_NOT_READY'
        mkdir "$parent"
      fi
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
  printf 'CANONICAL_HEAD: %s\n' "$(activation_source_head)"
  printf 'CANONICAL_MASTER_HEAD: %s\n' "$(canonical_master_head)"
  printf 'LIVE_TARGET: %s\n' "$live"
}

# Codex-only. Reuses the same single activation lock, repository-identity
# guards, and rsync architecture as do_activate, but replaces classify_platform
# eligibility with an Owner-supplied identity binding (L/H/T/M) so it may
# overwrite a live target that classify_platform would report LOCAL_DRIFT.
# Validates once, then - still holding the lock, with no live write between
# the two - revalidates immediately before the first write; any mismatch
# stops with zero live writes. Post-check still requires
# EXACT_CURRENT_MATERIALIZATION exactly like ordinary activation.
do_activate_replace_reviewed_local_drift() {
  acquire_activation_lock
  local platform="$1" expected_l="$2" expected_h="$3" expected_t="$4" expected_m="$5"
  [[ "$platform" == codex ]] \
    || die 'REVIEWED_REPLACEMENT_CODEX_ONLY: reviewed local drift replacement is Codex-only'
  guard_repository_root
  verify_manifest_paths
  require_canonical_source_gate
  require_canonical_repository_state_for_activation
  require_rsync

  local live canonical_abs
  live="$(platform_live_path "$platform")"
  canonical_abs="$(repo_path "$(platform_materialized_rel "$platform")")"
  [[ -d "$canonical_abs" && ! -L "$canonical_abs" ]] || die 'canonical materialization is missing or is a symlink'

  local sym
  if sym="$(find_symlink_component "$live")"; then
    die "ACTIVATION_PARENT_NOT_READY: symlink component in live target path: ${sym}"
  fi
  [[ -d "$live" && ! -L "$live" ]] \
    || die 'REVIEWED_REPLACEMENT_LIVE_TARGET_NOT_READY: live target is not an existing plain directory'

  require_reviewed_replacement_canonical_identity "$expected_h" "$expected_t" "$expected_m"
  local observed_l
  observed_l="$(compute_live_bundle_sha256 "$live")" \
    || die 'REVIEWED_REPLACEMENT_LIVE_BUNDLE_REJECTED: live target inventory failed identity serialization'
  [[ "$observed_l" == "$expected_l" ]] \
    || die "REVIEWED_REPLACEMENT_LIVE_BUNDLE_SHA256_MISMATCH: expected ${expected_l}, observed ${observed_l}"

  # Prewrite revalidation: same checks, same lock, no write yet issued. Any
  # mismatch introduced since the checks above stops here with zero writes.
  require_reviewed_replacement_canonical_identity "$expected_h" "$expected_t" "$expected_m"
  if sym="$(find_symlink_component "$live")"; then
    die "ACTIVATION_PARENT_NOT_READY: symlink component appeared in live target path before write: ${sym}"
  fi
  [[ -d "$live" && ! -L "$live" ]] \
    || die 'REVIEWED_REPLACEMENT_LIVE_TARGET_NOT_READY: live target changed type before write'
  local prewrite_l
  prewrite_l="$(compute_live_bundle_sha256 "$live")" \
    || die 'REVIEWED_REPLACEMENT_LIVE_BUNDLE_REJECTED: live target inventory failed identity serialization before write'
  [[ "$prewrite_l" == "$expected_l" ]] \
    || die "REVIEWED_REPLACEMENT_PREWRITE_LIVE_BUNDLE_SHA256_MISMATCH: expected ${expected_l}, observed ${prewrite_l}"

  rsync -a --checksum --delete "$canonical_abs"/ "$live"/ \
    || die 'REVIEWED_REPLACEMENT_WRITE_FAILED: rsync failed'

  require_reviewed_replacement_canonical_identity "$expected_h" "$expected_t" "$expected_m"
  classify_platform "$platform"
  if [[ "$CLASSIFY_STATE" != EXACT_CURRENT_MATERIALIZATION ]]; then
    printf 'ACTIVATION_VERIFICATION_FAILED\n' >&2
    printf '  post_write_state: %s\n' "$CLASSIFY_STATE" >&2
    exit 2
  fi

  # The legacy classifier uses Git file modes and omits empty directories.
  # Check the full serialization as well so exact success also binds all
  # directory entries and permissions, including the root.
  local source_bundle_l final_bundle_l
  source_bundle_l="$(compute_live_bundle_sha256 "$canonical_abs")" \
    || die 'ACTIVATION_VERIFICATION_FAILED: canonical bundle inventory failed'
  final_bundle_l="$(compute_live_bundle_sha256 "$live")" \
    || die 'ACTIVATION_VERIFICATION_FAILED: live bundle inventory failed'
  [[ "$final_bundle_l" == "$source_bundle_l" ]] \
    || die 'ACTIVATION_VERIFICATION_FAILED: full bundle differs from canonical materialization'

  printf 'PLATFORM: %s\n' "$platform"
  printf 'ACTIVATION_MODE: REPLACE_REVIEWED_LOCAL_DRIFT\n'
  printf 'EXPECTED_LIVE_SHA256: %s\n' "$expected_l"
  printf 'EXPECTED_CANONICAL_HEAD: %s\n' "$expected_h"
  printf 'EXPECTED_CANONICAL_TREE: %s\n' "$expected_t"
  printf 'EXPECTED_MATERIALIZATION_TREE: %s\n' "$expected_m"
  printf 'ACTIVATION_RESULT: ACTIVATED\n'
  printf 'FINAL_STATE: %s\n' "$CLASSIFY_STATE"
  printf 'CANONICAL_HEAD: %s\n' "$(activation_source_head)"
  printf 'CANONICAL_MASTER_HEAD: %s\n' "$(canonical_master_head)"
  printf 'LIVE_TARGET: %s\n' "$live"
}

main() {
  if [[ $# -eq 0 ]]; then
    usage
  fi

  local mode="" platform="" platform_count=0
  local replace_mode=0 replace_flag_count=0
  local expected_l="" expected_l_count=0
  local expected_h="" expected_h_count=0
  local expected_t="" expected_t_count=0
  local expected_m="" expected_m_count=0
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
      --replace-reviewed-local-drift)
        replace_mode=1
        replace_flag_count=$((replace_flag_count + 1))
        shift
        ;;
      --expected-live-sha256)
        [[ $# -ge 2 ]] || die '--expected-live-sha256 requires a value'
        expected_l="$2"
        expected_l_count=$((expected_l_count + 1))
        shift 2
        ;;
      --expected-canonical-head)
        [[ $# -ge 2 ]] || die '--expected-canonical-head requires a value'
        expected_h="$2"
        expected_h_count=$((expected_h_count + 1))
        shift 2
        ;;
      --expected-canonical-tree)
        [[ $# -ge 2 ]] || die '--expected-canonical-tree requires a value'
        expected_t="$2"
        expected_t_count=$((expected_t_count + 1))
        shift 2
        ;;
      --expected-materialization-tree)
        [[ $# -ge 2 ]] || die '--expected-materialization-tree requires a value'
        expected_m="$2"
        expected_m_count=$((expected_m_count + 1))
        shift 2
        ;;
      *)
        die "unknown argument: $1"
        ;;
    esac
  done

  (( replace_flag_count <= 1 )) || die '--replace-reviewed-local-drift may be given at most once'

  if (( replace_mode )) && [[ -z "$mode" ]]; then
    die 'REVIEWED_REPLACEMENT_REQUIRES_ACTIVATE: --replace-reviewed-local-drift requires --activate'
  fi

  [[ -n "$mode" ]] || usage
  (( platform_count <= 1 )) || die '--platform may be given at most once'
  if [[ -n "$platform" ]] && ! is_known_platform "$platform"; then
    die "unknown platform: $platform"
  fi

  if (( replace_mode )); then
    [[ "$mode" == activate ]] \
      || die 'REVIEWED_REPLACEMENT_REQUIRES_ACTIVATE: --replace-reviewed-local-drift requires --activate'
    [[ -n "$platform" ]] \
      || die '--activate requires exactly one --platform'
    [[ "$platform" == codex ]] \
      || die "REVIEWED_REPLACEMENT_CODEX_ONLY: --replace-reviewed-local-drift requires --platform codex (got ${platform})"
    (( expected_l_count == 1 )) \
      || die "REVIEWED_REPLACEMENT_MISSING_IDENTITY: --expected-live-sha256 must be given exactly once (got ${expected_l_count})"
    (( expected_h_count == 1 )) \
      || die "REVIEWED_REPLACEMENT_MISSING_IDENTITY: --expected-canonical-head must be given exactly once (got ${expected_h_count})"
    (( expected_t_count == 1 )) \
      || die "REVIEWED_REPLACEMENT_MISSING_IDENTITY: --expected-canonical-tree must be given exactly once (got ${expected_t_count})"
    (( expected_m_count == 1 )) \
      || die "REVIEWED_REPLACEMENT_MISSING_IDENTITY: --expected-materialization-tree must be given exactly once (got ${expected_m_count})"
    [[ "$expected_l" =~ ^[0-9a-f]{64}$ ]] \
      || die 'REVIEWED_REPLACEMENT_MALFORMED_IDENTITY: --expected-live-sha256 must be exactly 64 lowercase hex characters'
    [[ "$expected_h" =~ ^[0-9a-f]{40}$ ]] \
      || die 'REVIEWED_REPLACEMENT_MALFORMED_IDENTITY: --expected-canonical-head must be exactly 40 lowercase hex characters'
    [[ "$expected_t" =~ ^[0-9a-f]{40}$ ]] \
      || die 'REVIEWED_REPLACEMENT_MALFORMED_IDENTITY: --expected-canonical-tree must be exactly 40 lowercase hex characters'
    [[ "$expected_m" =~ ^[0-9a-f]{40}$ ]] \
      || die 'REVIEWED_REPLACEMENT_MALFORMED_IDENTITY: --expected-materialization-tree must be exactly 40 lowercase hex characters'
  elif (( expected_l_count > 0 || expected_h_count > 0 || expected_t_count > 0 || expected_m_count > 0 )); then
    die 'REVIEWED_REPLACEMENT_IDENTITY_WITHOUT_MODE: --expected-* flags require --replace-reviewed-local-drift'
  fi

  case "$mode" in
    check)
      do_check "$platform"
      ;;
    activate)
      [[ -n "$platform" ]] || die '--activate requires exactly one --platform'
      if (( replace_mode )); then
        do_activate_replace_reviewed_local_drift \
          "$platform" "$expected_l" "$expected_h" "$expected_t" "$expected_m"
      else
        do_activate "$platform"
      fi
      ;;
  esac
}

# Guards the auto-invocation so this file can also be sourced by the focused
# test suite to call its internal functions directly (LIVE_BUNDLE_SHA256_
# SERIALIZATION_V1 in particular) without duplicating their logic. When run
# normally - `bash activate-live.sh ...` or as an executable - BASH_SOURCE[0]
# and $0 are the same path and main still runs exactly as before.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
