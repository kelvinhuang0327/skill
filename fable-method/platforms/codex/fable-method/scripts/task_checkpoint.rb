#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'
require 'fileutils'
require 'open3'
require 'pathname'
require 'time'
require 'digest'
require 'tempfile'
require 'securerandom'
require 'shellwords'
require 'tmpdir'
require 'rbconfig'
require 'base64'
require 'rexml/document'

# StableLockAnchor puts flock authorities in Git's common metadata directory,
# outside the worktree paths that `git stash -u` can remove. The manifest binds
# the task/checkpoint identity to the lock inode, so a same-name replacement is
# not silently accepted as a new authority.
class StableLockAnchor
  GENERATION = 2
  FORMAT_VERSION = 1
  REPOSITORY_GENERATION_FILENAME = 'repository-generation.json'

  class Error < StandardError; end
  class MigrationRequired < Error; end

  class << self
    def repository_root(repo_root)
      root = File.realpath(File.expand_path(repo_root.to_s))
      output, status = Open3.capture2('git', '-C', root, 'rev-parse', '--show-toplevel', err: File::NULL)
      raise Error, "'#{root}' is not a readable Git worktree" unless status.success?

      resolved_root = File.realpath(output.strip)
      raise Error, "'#{root}' is not the Git worktree root '#{resolved_root}'" unless resolved_root == root

      root
    rescue SystemCallError => e
      raise Error, "Git worktree root could not be verified: #{e.message}"
    end

    # Protected-run storage may live below the Git worktree that owns the
    # generation-2 lock. Resolve both identities explicitly and reject path
    # aliases before either history or lock state is touched.
    def resolve_root_pair(storage_root, lock_root: nil)
      # Keep the historical single-root caller path byte-for-byte compatible:
      # repository_root already resolves its canonical Git root.
      unless lock_root
        root = repository_root(storage_root)
        return [root, root]
      end

      storage = canonical_directory!(storage_root, 'storage root')
      lock = canonical_directory!(lock_root, 'lock root')
      git_root = begin
        repository_root(lock)
      rescue Error => e
        raise Error, "--lock-root must name the exact Git worktree top-level: #{e.message}"
      end
      raise Error, "lock root '#{lock}' is not the exact Git worktree top-level" unless git_root == lock

      output, status = Open3.capture2('git', '-C', storage, 'rev-parse', '--show-toplevel', err: File::NULL)
      unless status.success?
        raise Error, "storage root '#{storage}' is not inside lock root '#{lock}'"
      end
      storage_git_root = File.realpath(output.strip)
      unless storage_git_root == lock && physical_subtree_or_same?(lock, storage)
        raise Error, "storage root '#{storage}' does not belong to lock root '#{lock}'"
      end

      [storage, lock]
    rescue Error
      raise
    rescue SystemCallError => e
      raise Error, "storage and lock roots could not be verified: #{e.message}"
    end

    def repository_root_for_path(file_path)
      absolute = File.expand_path(file_path.to_s)
      parent = File.realpath(File.dirname(absolute))
      output, status = Open3.capture2('git', '-C', parent, 'rev-parse', '--show-toplevel', err: File::NULL)
      unless status.success?
        raise Error, "Git worktree for '#{file_path}' could not be resolved" if git_marker_in_ancestors?(parent)

        return nil
      end

      root = File.realpath(output.strip)
      candidate = File.join(parent, File.basename(absolute))
      prefix = "#{root}#{File::SEPARATOR}"
      unless candidate == root || candidate.start_with?(prefix)
        raise Error, "checkpoint path '#{file_path}' is outside its Git worktree"
      end
      root
    rescue SystemCallError => e
      raise Error, "checkpoint Git worktree could not be verified: #{e.message}"
    end

    def lock_path(repo_root, namespace, key, filename: 'lock', lock_root: nil)
      storage, root = resolve_root_pair(repo_root, lock_root: lock_root)
      common = common_directory(root)
      identity = lock_identity(root, namespace, key, storage_root: storage)
      digest = Digest::SHA256.hexdigest(JSON.generate(identity))
      File.join(common, 'fable-locks', namespace.to_s, digest, filename)
    end

    # This is deliberately an explicit offline step. Existing v1 workers do
    # not know about a v2 task anchor, so ordinary lock acquisition must not
    # infer that an absent worktree lock means there is no legacy owner.
    def initialize_repository_generation!(repo_root, confirm_legacy_workers_quiescent: false)
      root = repository_root(repo_root)
      common = common_directory(root)
      marker_path = repository_generation_path(common)
      if File.exist?(marker_path) || File.symlink?(marker_path)
        validate_repository_generation!(common, marker_path)
        return true
      end
      unless confirm_legacy_workers_quiescent
        raise MigrationRequired,
              "repository '#{common}' has no generation-#{GENERATION} lock marker; migration gate required: " \
              'confirm every legacy protected worker and inherited child is quiescent, then explicitly initialize offline'
      end

      common_root = File.dirname(marker_path)
      ensure_private_directory!(common_root, common)
      manifest = {
        'format_version' => FORMAT_VERSION,
        'generation' => GENERATION,
        'common_directory' => common
      }
      Tempfile.create(['.repository-generation-', '.tmp'], common_root) do |temp|
        temp.write(JSON.pretty_generate(manifest))
        temp.flush
        temp.fsync
        begin
          File.link(temp.path, marker_path)
          sync_directory!(common_root)
        rescue Errno::EEXIST
          # A concurrent explicit initializer may have won; accept only its
          # exact verified marker, never a same-name file by itself.
          validate_repository_generation!(common, marker_path)
        end
      end
      validate_repository_generation!(common, marker_path)
      true
    rescue Error, MigrationRequired
      raise
    rescue SystemCallError, JSON::ParserError => e
      raise Error, "repository lock generation could not be initialized safely: #{e.message}"
    end

    def open_lock(repo_root, namespace, key, filename: 'lock', legacy_paths: [], lock_root: nil)
      storage, root = resolve_root_pair(repo_root, lock_root: lock_root)
      common = common_directory(root)
      validate_repository_generation!(common, repository_generation_path(common))
      identity = lock_identity(root, namespace, key, storage_root: storage)
      reject_legacy_paths!(legacy_paths, namespace)
      common_root = File.join(common, 'fable-locks')
      namespace_root = File.join(common_root, namespace.to_s)
      ensure_private_directory!(common_root, common)
      ensure_private_directory!(namespace_root, common_root)
      digest = Digest::SHA256.hexdigest(JSON.generate(identity))
      anchor_dir = File.join(namespace_root, digest)
      ensure_anchor!(anchor_dir, identity, filename)
      open_verified_lock(anchor_dir, identity, filename)
    rescue Error
      raise
    rescue SystemCallError, JSON::ParserError => e
      raise Error, "stable #{namespace} lock authority could not be verified: #{e.message}"
    end

    def open_existing_lock(repo_root, namespace, key, filename: 'lock', legacy_paths: [], lock_root: nil)
      storage, root = resolve_root_pair(repo_root, lock_root: lock_root)
      common = common_directory(root)
      validate_repository_generation!(common, repository_generation_path(common))
      identity = lock_identity(root, namespace, key, storage_root: storage)
      reject_legacy_paths!(legacy_paths, namespace)
      common_root = File.join(common, 'fable-locks')
      namespace_root = File.join(common_root, namespace.to_s)
      validate_private_directory!(common_root, common)
      validate_private_directory!(namespace_root, common_root)
      digest = Digest::SHA256.hexdigest(JSON.generate(identity))
      anchor_dir = File.join(namespace_root, digest)
      raise Error, "stable #{namespace} lock anchor is missing" unless File.directory?(anchor_dir) && !File.symlink?(anchor_dir)

      open_verified_lock(anchor_dir, identity, filename)
    rescue Error
      raise
    rescue SystemCallError, JSON::ParserError => e
      raise Error, "stable #{namespace} lock authority could not be verified: #{e.message}"
    end

    def assert_generation_available!(repo_root, namespace, key, legacy_paths: [], legacy_state_paths: [], lock_root: nil)
      storage, root = resolve_root_pair(repo_root, lock_root: lock_root)
      common = common_directory(root)
      validate_repository_generation!(common, repository_generation_path(common))
      identity = lock_identity(root, namespace, key, storage_root: storage)
      reject_legacy_paths!(legacy_paths, namespace)
      digest = Digest::SHA256.hexdigest(JSON.generate(identity))
      anchor_dir = File.join(common, 'fable-locks', namespace.to_s, digest)
      unless File.exist?(anchor_dir) || File.symlink?(anchor_dir)
        legacy_state_paths.each do |path|
          if File.exist?(path) || File.symlink?(path)
            raise MigrationRequired,
                  "legacy #{namespace} state '#{path}' exists without a stable lock anchor; migration gate required: " \
                  'confirm all old protected workers and inherited children are quiescent, then perform an explicitly authorized offline migration'
          end
        end
      end
      true
    rescue Error, MigrationRequired
      raise
    rescue SystemCallError => e
      raise Error, "stable #{namespace} lock generation could not be checked: #{e.message}"
    end

    # A device-anchor recovery snapshot reads the existing task-execution
    # authority without accepting its recorded device number as current. It
    # still requires the exact repository/key, private paths, regular files,
    # and unchanged lock inode. This is intentionally separate from open_lock:
    # ordinary callers must continue to reject device drift.
    def device_recovery_snapshot(repo_root, task_id, lock_root: nil)
      storage, root = resolve_root_pair(repo_root, lock_root: lock_root)
      common = common_directory(root)
      validate_repository_generation!(common, repository_generation_path(common))
      identity = lock_identity(root, 'task-execution', task_id.to_s, storage_root: storage)
      common_root = File.join(common, 'fable-locks')
      namespace_root = File.join(common_root, 'task-execution')
      validate_private_directory!(common_root, common)
      validate_private_directory!(namespace_root, common_root)

      lock_path = File.join(namespace_root, Digest::SHA256.hexdigest(JSON.generate(identity)), 'execution.lock')
      anchor_dir = File.dirname(lock_path)
      anchor_stat = File.lstat(anchor_dir)
      unless anchor_stat.directory? && !anchor_stat.symlink? && anchor_stat.uid == Process.uid &&
             (anchor_stat.mode & 0o022).zero?
        raise Error, "stable task-execution anchor '#{anchor_dir}' is not a private owned directory"
      end

      authority_path = File.join(anchor_dir, 'authority.json')
      authority_stat = File.lstat(authority_path)
      verify_owned_regular!(authority_path, authority_stat)
      authority_bytes = File.binread(authority_path)
      manifest = JSON.parse(authority_bytes)
      raise Error, 'stable task-execution authority must be a JSON Object' unless manifest.is_a?(Hash)

      identity.each do |key, value|
        raise Error, "stable task-execution authority has mismatched #{key}" unless manifest[key] == value
      end
      unless manifest['lock_device'].is_a?(Integer) && manifest['lock_device'].positive? &&
             manifest['lock_inode'].is_a?(Integer) && manifest['lock_inode'].positive?
        raise Error, 'stable task-execution authority has invalid device or inode identity'
      end

      lock_stat = File.lstat(lock_path)
      verify_owned_regular!(lock_path, lock_stat)
      unless lock_stat.ino == manifest['lock_inode']
        raise Error, "stable task-execution anchor '#{anchor_dir}' lock inode changed"
      end

      {
        repository: root,
        storage_root: storage,
        task_id: task_id.to_s,
        common_directory: common,
        anchor_dir: anchor_dir,
        authority_path: authority_path,
        lock_path: lock_path,
        manifest: manifest,
        authority_bytes: authority_bytes,
        authority_sha256: Digest::SHA256.hexdigest(authority_bytes),
        stored_lock_device: manifest['lock_device'],
        current_lock_device: lock_stat.dev,
        lock_inode: lock_stat.ino,
        stored_volume_identity: manifest['volume_identity'],
        current_volume_identity: stable_volume_identity(common)
      }
    rescue Error
      raise
    rescue SystemCallError, JSON::ParserError => e
      raise Error, "device-anchor recovery snapshot is unverifiable: #{e.message}"
    end

    # Holds the original lock inode while the caller checks the execution
    # journal or atomically updates authority.json. The lock contents and inode
    # remain untouched by this operation.
    def with_device_recovery_lock(snapshot)
      lock = File.open(snapshot.fetch(:lock_path), File::RDWR | nofollow_flag)
      verify_owned_regular!(snapshot.fetch(:lock_path), lock.stat)
      unless lock.stat.dev == snapshot.fetch(:current_lock_device) &&
             lock.stat.ino == snapshot.fetch(:lock_inode)
        raise Error, 'device-anchor recovery lock changed after preflight'
      end
      unless lock.flock(File::LOCK_EX | File::LOCK_NB)
        raise Error, 'device-anchor recovery refused: ACTIVE_OR_COMPETING_WRITER'
      end

      current = device_recovery_snapshot(snapshot.fetch(:storage_root), snapshot.fetch(:task_id),
                                         lock_root: snapshot.fetch(:repository))
      unless current.fetch(:authority_sha256) == snapshot.fetch(:authority_sha256) &&
             current.fetch(:lock_inode) == snapshot.fetch(:lock_inode) &&
             current.fetch(:current_lock_device) == snapshot.fetch(:current_lock_device)
        raise Error, 'device-anchor recovery target changed after preflight'
      end
      yield current, lock
    ensure
      if lock
        lock.flock(File::LOCK_UN) rescue nil
        lock.close unless lock.closed?
      end
    end

    def stable_volume_identity(path)
      if RUBY_PLATFORM.include?('darwin')
        output, status = Open3.capture2('df', '-P', path, err: File::NULL)
        return nil unless status.success?

        row = output.lines.last.to_s.strip
        match = /\A\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(.+)\z/.match(row)
        return nil unless match

        plist, plist_status = Open3.capture2('diskutil', 'info', '-plist', match[1], err: File::NULL)
        return nil unless plist_status.success?

        document = REXML::Document.new(plist)
        dictionary = REXML::XPath.first(document, '//dict')
        return nil unless dictionary

        values = dictionary.elements.to_a
        volume_uuid = values.each_slice(2).find do |key, _value|
          key&.name == 'key' && key.text == 'VolumeUUID'
        end&.last
        value = volume_uuid&.text.to_s.strip
        return nil unless /\A[0-9A-Fa-f-]{16,64}\z/.match?(value)

        "macos:VolumeUUID:#{value.upcase}"
      elsif RbConfig::CONFIG['host_os'].to_s.match?(/linux/i)
        output, status = Open3.capture2('findmnt', '--json', '--target', path, '--output', 'UUID', err: File::NULL)
        return nil unless status.success?

        value = JSON.parse(output).dig('filesystems', 0, 'uuid').to_s.strip
        return nil unless /\A[0-9A-Fa-f-]{16,64}\z/.match?(value)

        "linux:UUID:#{value.upcase}"
      end
    rescue SystemCallError, JSON::ParserError, REXML::ParseException
      nil
    end

    def replace_device_recovery_manifest!(snapshot, manifest, expected_sha256:)
      authority_path = snapshot.fetch(:authority_path)
      anchor_dir = snapshot.fetch(:anchor_dir)
      authority_stat = File.lstat(authority_path)
      verify_owned_regular!(authority_path, authority_stat)
      current_bytes = File.binread(authority_path)
      unless Digest::SHA256.hexdigest(current_bytes) == expected_sha256
        raise Error, 'device-anchor recovery authority changed before atomic replacement'
      end

      Tempfile.create(['.authority-device-recovery-', '.tmp'], anchor_dir) do |temporary|
        temporary.write(JSON.pretty_generate(manifest))
        temporary.flush
        temporary.fsync
        current_stat = File.lstat(authority_path)
        verify_owned_regular!(authority_path, current_stat)
        unless Digest::SHA256.file(authority_path).hexdigest == expected_sha256
          raise Error, 'device-anchor recovery authority changed before atomic replacement'
        end
        File.rename(temporary.path, authority_path)
        sync_directory!(anchor_dir)
      end

      readback = File.binread(authority_path)
      unless JSON.parse(readback) == manifest
        raise Error, 'device-anchor recovery authority did not match its atomic readback'
      end
      true
    rescue Error
      raise
    rescue SystemCallError, JSON::ParserError => e
      raise Error, "device-anchor recovery could not atomically replace authority.json: #{e.message}"
    end
    private :replace_device_recovery_manifest!

    private

    def repository_generation_path(common)
      File.join(common, 'fable-locks', REPOSITORY_GENERATION_FILENAME)
    end

    def canonical_directory!(path, label)
      value = path.to_s
      unless !value.include?("\0") && Pathname.new(value).absolute? && File.expand_path(value) == value
        raise Error, "#{label} '#{value}' is not an unambiguous absolute path"
      end
      stat = File.lstat(value)
      unless stat.directory? && !stat.symlink? && File.realpath(value) == value
        raise Error, "#{label} '#{value}' is a symlink, non-directory, or non-canonical path"
      end
      value
    rescue SystemCallError => e
      raise Error, "#{label} '#{value}' could not be verified: #{e.message}"
    end

    def validate_repository_generation!(common, marker_path)
      unless File.exist?(marker_path) && !File.symlink?(marker_path)
        raise MigrationRequired,
              "repository '#{common}' has no generation-#{GENERATION} lock marker; migration gate required: " \
              'confirm every legacy protected worker and inherited child is quiescent, then explicitly initialize offline'
      end

      marker_stat = File.lstat(marker_path)
      verify_owned_regular!(marker_path, marker_stat)
      manifest = JSON.parse(File.binread(marker_path))
      expected = {
        'format_version' => FORMAT_VERSION,
        'generation' => GENERATION,
        'common_directory' => common
      }
      unless expected.all? { |key, value| manifest[key] == value }
        raise Error, "repository lock generation marker '#{marker_path}' has mismatched identity"
      end
      true
    rescue Errno::ENOENT
      raise MigrationRequired,
            "repository '#{common}' has no generation-#{GENERATION} lock marker; migration gate required: " \
            'confirm every legacy protected worker and inherited child is quiescent, then explicitly initialize offline'
    end

    def lock_identity(root, namespace, key, storage_root: root)
      identity = {
        'format_version' => FORMAT_VERSION,
        'generation' => GENERATION,
        'namespace' => namespace.to_s,
        'repository' => root,
        'key' => key.to_s
      }
      unless storage_root == root
        identity['storage_root'] = storage_root
        identity['lock_root'] = root
      end
      identity
    end

    # File.realpath can preserve different case spellings for the same
    # directory on a case-insensitive filesystem. Walk physical parents and
    # compare directory identities instead of relying on lexical prefixes.
    def physical_subtree_or_same?(ancestor, candidate)
      ancestor_path = File.realpath(ancestor)
      current = File.realpath(candidate)
      loop do
        return true if File.identical?(ancestor_path, current)

        parent = File.dirname(current)
        return false if parent == current

        current = parent
      end
    end

    def common_directory(root)
      output, status = Open3.capture2('git', '-C', root, 'rev-parse', '--git-common-dir', err: File::NULL)
      raise Error, "Git common metadata directory for '#{root}' could not be resolved" unless status.success?

      path = File.realpath(File.expand_path(output.strip, root))
      stat = File.lstat(path)
      unless stat.directory? && stat.uid == Process.uid
        raise Error, "Git common metadata directory '#{path}' is not an owned directory"
      end
      if physical_subtree_or_same?(root, path)
        git_dir = File.join(root, '.git')
        safe_git_dir = !File.symlink?(git_dir) && File.directory?(git_dir) &&
                       begin
                         real_git_dir = File.realpath(git_dir)
                         physical_subtree_or_same?(real_git_dir, path)
                       rescue SystemCallError
                         false
                       end
        unless safe_git_dir
          raise MigrationRequired,
                "Git common metadata directory '#{path}' is inside stash-managed worktree '#{root}'; " \
                'move Git metadata outside the worktree or use its real .git metadata directory before initializing Fable locks'
        end
      end
      path
    rescue Error, MigrationRequired
      raise
    rescue SystemCallError => e
      raise Error, "Git common metadata directory could not be verified: #{e.message}"
    end

    def git_marker_in_ancestors?(path)
      current = path
      loop do
        marker = File.join(current, '.git')
        return true if File.exist?(marker) || File.symlink?(marker)

        parent = File.dirname(current)
        return false if parent == current

        current = parent
      end
    end

    def reject_legacy_paths!(paths, namespace)
      Array(paths).each do |path|
        next unless File.exist?(path) || File.symlink?(path)

        raise MigrationRequired,
              "legacy #{namespace} lock '#{path}' is present; migration gate required: " \
              'confirm all old protected workers and inherited children are quiescent, then perform an explicitly authorized offline migration'
      end
    end

    def ensure_private_directory!(path, parent)
      begin
        Dir.mkdir(path, 0o700)
      rescue Errno::EEXIST
        # Validate the existing directory below; never trust the path name alone.
      end
      validate_private_directory!(path, parent)
    rescue SystemCallError => e
      raise Error, "stable lock directory '#{path}' could not be verified: #{e.message}"
    end

    def validate_private_directory!(path, parent)
      stat = File.lstat(path)
      unless stat.directory? && !stat.symlink? && stat.uid == Process.uid && (stat.mode & 0o022).zero?
        raise Error, "stable lock directory '#{path}' is not a private owned directory"
      end
      expected = File.join(File.realpath(parent), File.basename(path))
      raise Error, "stable lock directory '#{path}' resolves outside its expected parent" unless File.realpath(path) == expected
    rescue SystemCallError => e
      raise Error, "stable lock directory '#{path}' could not be verified: #{e.message}"
    end

    def ensure_anchor!(anchor_dir, identity, filename)
      unless File.exist?(anchor_dir) || File.symlink?(anchor_dir)
        temporary_dir = Dir.mktmpdir('.anchor-init-', File.dirname(anchor_dir))
        begin
          lock_path = File.join(temporary_dir, filename)
          flags = File::RDWR | File::CREAT | File::EXCL | nofollow_flag
          File.open(lock_path, flags, 0o600) do |lock|
            verify_owned_regular!(lock_path, lock.stat)
            lock.fsync
            manifest = identity.merge('lock_device' => lock.stat.dev, 'lock_inode' => lock.stat.ino)
            volume_identity = stable_volume_identity(File.dirname(File.dirname(File.dirname(anchor_dir))))
            manifest['volume_identity'] = volume_identity if volume_identity
            File.open(File.join(temporary_dir, 'authority.json'), File::WRONLY | File::CREAT | File::EXCL | nofollow_flag, 0o600) do |file|
              file.write(JSON.pretty_generate(manifest))
              file.flush
              file.fsync
            end
          end
          sync_directory!(temporary_dir)
          begin
            File.rename(temporary_dir, anchor_dir)
            sync_directory!(File.dirname(anchor_dir))
          rescue SystemCallError
            raise unless File.directory?(anchor_dir) && !File.symlink?(anchor_dir)
          end
        ensure
          FileUtils.remove_entry_secure(temporary_dir) if File.directory?(temporary_dir)
        end
      end
      validate_anchor!(anchor_dir, identity, filename)
    end

    def validate_anchor!(anchor_dir, identity, filename)
      dir_stat = File.lstat(anchor_dir)
      unless dir_stat.directory? && !dir_stat.symlink? && dir_stat.uid == Process.uid && (dir_stat.mode & 0o022).zero?
        raise Error, "stable lock anchor '#{anchor_dir}' is not a private owned directory"
      end

      manifest_path = File.join(anchor_dir, 'authority.json')
      manifest_stat = File.lstat(manifest_path)
      verify_owned_regular!(manifest_path, manifest_stat)
      manifest = JSON.parse(File.binread(manifest_path))
      identity.each do |key, value|
        raise Error, "stable lock anchor '#{anchor_dir}' has mismatched #{key}" unless manifest[key] == value
      end

      lock_path = File.join(anchor_dir, filename)
      lock_stat = File.lstat(lock_path)
      verify_owned_regular!(lock_path, lock_stat)
      unless manifest['lock_device'] == lock_stat.dev && manifest['lock_inode'] == lock_stat.ino
        raise Error, "stable lock anchor '#{anchor_dir}' lock inode changed"
      end
      manifest
    rescue Errno::ENOENT => e
      raise Error, "stable lock anchor '#{anchor_dir}' is incomplete: #{e.message}"
    end

    def open_verified_lock(anchor_dir, identity, filename)
      manifest = validate_anchor!(anchor_dir, identity, filename)
      lock_path = File.join(anchor_dir, filename)
      lock = File.open(lock_path, File::RDWR | nofollow_flag)
      verify_owned_regular!(lock_path, lock.stat)
      unless lock.stat.dev == manifest['lock_device'] && lock.stat.ino == manifest['lock_inode']
        lock.close
        raise Error, "stable lock anchor '#{anchor_dir}' changed while opening"
      end
      lock
    rescue SystemCallError => e
      lock.close if defined?(lock) && lock && !lock.closed?
      raise Error, "stable lock file '#{lock_path}' could not be opened safely: #{e.message}"
    end

    def verify_owned_regular!(path, stat)
      unless stat.file? && !stat.symlink? && stat.uid == Process.uid && (stat.mode & 0o022).zero?
        raise Error, "stable lock file '#{path}' is not an owned private regular file"
      end
    end

    def nofollow_flag
      File.const_defined?(:NOFOLLOW) ? File::NOFOLLOW : 0
    end

    def sync_directory!(path)
      File.open(path, File::RDONLY) { |directory| directory.fsync }
    end
  end
end

# TaskCheckpoint encapsulates the minimal, durable, authoritative continuation state
# for Fable Worker execution across sessions and model boundaries.
class TaskCheckpoint
  SCHEMA_VERSION = 1

  VALID_LIFECYCLE_STATES = %w[IN_PROGRESS BLOCKED COMPLETED ABORTED].freeze
  VALID_VERDICTS = %w[CONTINUE ALREADY_COMPLETED RECONCILE_LIVE_STATE AUTHORIZATION_REQUIRED STOP_UNRESOLVED].freeze
  QUEUE_DISPOSITION_BLOCKED_DEFERRED = 'BLOCKED_DEFERRED'
  DEFERRED_RECHECK_ACTION = 'RECHECK_DEFERRED_RESUME_GATE'
  DEFER_ELIGIBLE_BLOCKER = 'TRANSIENT_ELIGIBLE'
  MAX_DEFERRED_TASKS = 1
  MAX_AUTOMATIC_END_RECHECKS = 1
  DEFAULT_QUIESCENCE_OBSERVATION_SECONDS = 5
  DEFERRED_TASK_TERMINAL_STATES = %w[BLOCKED COMPLETED ABORTED].freeze
  OPTIONAL_QUEUE_FIELDS = %i[
    queue_disposition
    resume_after_task_id
    next_authorized_task_packet_ref
    deferred_resume_action
    deferred_recheck_count
  ].freeze

  REQUIRED_FIELDS = %i[
    task_id
    repository
    worktree
    authoritative_packet_ref
    branch
    current_head
    current_tree
    task_lifecycle_state
    next_action
    authorization_boundary
    updated_at
    revision
  ].freeze

  attr_accessor :schema_version,
                :task_id,
                :repository,
                :worktree,
                :authoritative_packet_ref,
                :branch,
                :current_head,
                :current_tree,
                :task_lifecycle_state,
                :current_blocker,
                :next_action,
                :authorization_boundary,
                :pr_state,
                :pr_number,
                :pr_url,
                :updated_at,
                :revision,
                :queue_disposition,
                :resume_after_task_id,
                :next_authorized_task_packet_ref,
                :deferred_resume_action,
                :deferred_recheck_count

  def initialize(attrs = {})
    @schema_version = attrs[:schema_version] || attrs['schema_version'] || SCHEMA_VERSION
    @task_id = (attrs[:task_id] || attrs['task_id'])&.to_s
    @repository = (attrs[:repository] || attrs['repository'])&.to_s
    @worktree = (attrs[:worktree] || attrs['worktree'])&.to_s
    @authoritative_packet_ref = (attrs[:authoritative_packet_ref] || attrs['authoritative_packet_ref'])&.to_s
    @branch = (attrs[:branch] || attrs['branch'] || 'master')&.to_s
    @current_head = (attrs[:current_head] || attrs['current_head'])&.to_s
    @current_tree = (attrs[:current_tree] || attrs['current_tree'])&.to_s
    @task_lifecycle_state = (attrs[:task_lifecycle_state] || attrs['task_lifecycle_state'] || 'IN_PROGRESS')&.to_s
    @current_blocker = attrs[:current_blocker] || attrs['current_blocker']
    @next_action = (attrs[:next_action] || attrs['next_action'])&.to_s
    @authorization_boundary = (attrs[:authorization_boundary] || attrs['authorization_boundary'] || 'NONE')&.to_s
    @pr_state = attrs[:pr_state] || attrs['pr_state']
    @pr_number = attrs[:pr_number] || attrs['pr_number']
    @pr_url = attrs[:pr_url] || attrs['pr_url']
    @updated_at = (attrs[:updated_at] || attrs['updated_at'] || Time.now.utc.iso8601)&.to_s
    @revision = (attrs[:revision] || attrs['revision'] || 1).to_i
    @queue_disposition = optional_value(attrs, :queue_disposition)&.to_s
    @resume_after_task_id = optional_value(attrs, :resume_after_task_id)&.to_s
    @next_authorized_task_packet_ref = optional_value(attrs, :next_authorized_task_packet_ref)&.to_s
    @deferred_resume_action = optional_value(attrs, :deferred_resume_action)&.to_s
    @deferred_recheck_count = optional_value(attrs, :deferred_recheck_count)
  end

  def validate!
    errors = []

    errors << "schema_version must be #{SCHEMA_VERSION}" unless @schema_version == SCHEMA_VERSION

    REQUIRED_FIELDS.each do |field|
      val = send(field)
      errors << "missing required field: #{field}" if val.nil? || val.to_s.strip.empty?
    end

    %i[repository worktree].each do |field|
      errors << "#{field} must be an absolute locator" unless Pathname.new(send(field).to_s).absolute?
    end

    if @authoritative_packet_ref.to_s.strip == 'ORIGINAL_TASK_RULES_INHERITED: YES'
      errors << 'authoritative_packet_ref must specify an explicit locator/source, not just inheritance affirmation'
    end

    if @authoritative_packet_ref.to_s.strip =~ %r{\A(conversation|session|chat)://}i
      errors << 'authoritative_packet_ref must be a durable cross-agent locator (repo file path or git locator), not an ephemeral session URI'
    end

    unless VALID_LIFECYCLE_STATES.include?(@task_lifecycle_state)
      errors << "invalid task_lifecycle_state: #{@task_lifecycle_state} (must be one of #{VALID_LIFECYCLE_STATES.join(', ')})"
    end

    errors << 'revision must be a positive integer >= 1' if @revision.nil? || @revision < 1

    validate_queue_fields(errors)

    raise ValidationError, "Checkpoint validation failed: #{errors.join('; ')}" unless errors.empty?

    true
  end

  def valid?
    validate!
    true
  rescue ValidationError
    false
  end

  def to_h
    data = {
      'schema_version' => @schema_version,
      'task_id' => @task_id,
      'repository' => @repository,
      'worktree' => @worktree,
      'authoritative_packet_ref' => @authoritative_packet_ref,
      'branch' => @branch,
      'current_head' => @current_head,
      'current_tree' => @current_tree,
      'task_lifecycle_state' => @task_lifecycle_state,
      'current_blocker' => @current_blocker,
      'next_action' => @next_action,
      'authorization_boundary' => @authorization_boundary,
      'pr_state' => @pr_state,
      'pr_number' => @pr_number,
      'pr_url' => @pr_url,
      'updated_at' => @updated_at,
      'revision' => @revision
    }
    OPTIONAL_QUEUE_FIELDS.each do |field|
      value = send(field)
      data[field.to_s] = value unless value.nil?
    end
    data
  end

  def to_json(*args)
    JSON.pretty_generate(to_h, *args)
  end

  def self.from_json(json_str)
    data = JSON.parse(json_str)
    raise ValidationError, 'checkpoint JSON root must be an Object' unless data.is_a?(Hash)

    new(data)
  rescue JSON::ParserError => e
    raise ValidationError, "Malformed JSON checkpoint: #{e.message}"
  end

  def self.default_path(repo_root, task_id)
    File.join(repo_root, '.fable', 'checkpoints', "#{task_id}.json")
  end

  def self.lock_path_for(file_path)
    absolute = File.expand_path(file_path.to_s)
    repository = StableLockAnchor.repository_root_for_path(absolute)
    return "#{absolute}.lock" unless repository

    canonical_path = File.join(File.realpath(File.dirname(absolute)), File.basename(absolute))
    relative_path = Pathname.new(canonical_path).relative_path_from(Pathname.new(repository)).to_s
    StableLockAnchor.lock_path(repository, 'checkpoint-sidecar', relative_path, filename: 'checkpoint.lock')
  end

  def self.open_lock_for(file_path)
    absolute = File.expand_path(file_path.to_s)
    repository = StableLockAnchor.repository_root_for_path(absolute)
    return File.open("#{absolute}.lock", File::RDWR | File::CREAT, 0o600) unless repository

    canonical_path = File.join(File.realpath(File.dirname(absolute)), File.basename(absolute))
    relative_path = Pathname.new(canonical_path).relative_path_from(Pathname.new(repository)).to_s
    StableLockAnchor.open_lock(repository, 'checkpoint-sidecar', relative_path,
                               filename: 'checkpoint.lock', legacy_paths: ["#{absolute}.lock"])
  end


  def self.load(file_path)
    raise LoadError, "Checkpoint file does not exist: #{file_path}" unless File.file?(file_path)

    raw = File.read(file_path, encoding: 'UTF-8')
    cp = from_json(raw)
    cp.validate!
    cp
  end

  def save(file_path, expected_revision: nil)
    validate!

    dir = File.dirname(file_path)
    FileUtils.mkdir_p(dir)

    # Repository checkpoints use a Git-common metadata anchor so a worktree
    # stash cannot replace the lock inode. Standalone files outside Git retain
    # the adjacent sidecar behavior.
    lock = self.class.open_lock_for(file_path)
    begin
      raise IOError, 'Could not acquire checkpoint lock' unless lock.flock(File::LOCK_EX)
      lock_directory = File.dirname(lock.path)
      unless File.stat(lock_directory).dev == File.stat(dir).dev
        raise IOError,
              "stable checkpoint lock and checkpoint '#{file_path}' are on different filesystems; atomic replacement is unavailable"
      end

      if File.exist?(file_path)
        existing_raw = File.read(file_path, encoding: 'UTF-8')
        begin
          existing = self.class.from_json(existing_raw)
          if expected_revision
            if existing.revision != expected_revision
              raise ConcurrencyError,
                    "Revision mismatch: expected #{expected_revision}, but stored revision is #{existing.revision}"
            end
            @revision = existing.revision + 1
          elsif @revision <= existing.revision
            @revision = existing.revision + 1
          end
        rescue JSON::ParserError, ValidationError
          # Overwrite corrupt file if explicitly directed
        end
      end

      @updated_at = Time.now.utc.iso8601
      # Stage beside the stable lock, in Git common metadata for repository
      # checkpoints. A worktree stash must not remove and recreate the temporary
      # inode while the atomic replacement is in flight.
      Tempfile.create(["#{File.basename(file_path)}.tmp.", ''], lock_directory, encoding: 'UTF-8') do |temp|
        temp.write(to_json)
        temp.close
        File.rename(temp.path, file_path) # Commit point; no power-loss durability claim.
      end
    ensure
      lock.close unless lock.closed?
    end
    true
  end

  def deferred?
    @queue_disposition == QUEUE_DISPOSITION_BLOCKED_DEFERRED
  end

  def deferred_recheck_consumed?
    @deferred_recheck_count == MAX_AUTOMATIC_END_RECHECKS && deferred_queue_identity_complete?
  end

  def deferred_queue_run_consumed?
    @queue_disposition.nil? && deferred_recheck_consumed?
  end

  def self.scope_qualified_active_writer?(worktree:, task_owned_paths:, writer_evidence:,
                                          before_snapshot:, after_snapshot:)
    root = worktree.to_s.strip
    raise WriterEvidenceError, 'worktree must be a non-empty path' if root.empty?
    if before_snapshot.nil? || after_snapshot.nil?
      raise WriterEvidenceError, 'bounded quiescence requires both before and after scoped snapshots'
    end

    normalized_root = File.expand_path(root)
    owned_surfaces = Array(task_owned_paths).map do |path|
      value = path.to_s.strip
      File.expand_path(value, normalized_root) unless value.empty?
    end.compact
    surfaces = [normalized_root, *owned_surfaces].uniq

    return true unless before_snapshot == after_snapshot

    Array(writer_evidence).any? do |evidence|
      next false unless evidence.respond_to?(:[])

      cwd = evidence[:cwd] || evidence['cwd']
      cwd = cwd.to_s.strip
      cwd = nil unless !cwd.empty? && Pathname.new(cwd).absolute?
      targets = evidence[:target_paths] || evidence['target_paths']

      Array(targets).any? do |target|
        target = target.to_s.strip
        next false if target.empty?

        absolute_target = if Pathname.new(target).absolute?
                            File.expand_path(target)
                          elsif cwd
                            File.expand_path(target, cwd)
                          end
        next false unless absolute_target

        surfaces.any? { |surface| path_surfaces_overlap?(absolute_target, surface) }
      end
    end
  end

  def self.ownership_surfaces_overlap?(task_a_owned_paths, task_b_owned_paths)
    task_a = Array(task_a_owned_paths).map { |path| normalize_ownership_surface(path) }
    task_b = Array(task_b_owned_paths).map { |path| normalize_ownership_surface(path) }
    task_a.product(task_b).any? { |left, right| path_surfaces_overlap?(left, right) }
  end

  def self.validate_deferred_limit!(checkpoints)
    checkpoints = Array(checkpoints)
    unless checkpoints.all? { |checkpoint| checkpoint.is_a?(TaskCheckpoint) }
      raise DeferredQueueStateError, 'deferred-task inventory must contain only TaskCheckpoint records'
    end
    checkpoints.each(&:validate!)

    deferred_count = checkpoints.count(&:deferred?)
    return true if deferred_count <= MAX_DEFERRED_TASKS

    raise DeferredQueueLimitError,
          "maximum deferred tasks exceeded: #{deferred_count} present, #{MAX_DEFERRED_TASKS} allowed"
  end

  def defer_for_authorized_task!(blocker:, blocker_disposition:, resume_after_task_id:,
                                 next_authorized_task_packet_ref:, task_b_independent:,
                                 task_b_packet_authorized:, existing_deferred_checkpoints:,
                                 task_a_owned_paths: nil, task_b_owned_paths: nil)
    validate!
    if deferred_queue_run_consumed?
      raise DeferredQueueLimitError,
            'automatic deferred queue run is already consumed; record a recurrent transient blocker without executing Task B again'
    end
    raise DeferredQueueStateError, "task #{@task_id} is already deferred" if deferred?
    unless %w[IN_PROGRESS BLOCKED].include?(@task_lifecycle_state)
      raise DeferredQueueStateError, "terminal Task A state #{@task_lifecycle_state} cannot enter the deferred queue"
    end
    unless blocker_disposition.to_s == DEFER_ELIGIBLE_BLOCKER
      raise DeferredQueueEligibilityError,
            "blocker disposition must be #{DEFER_ELIGIBLE_BLOCKER}; semantic, authorization, safety, database-authority, and permanent blockers are ineligible"
    end
    unless task_b_independent == true
      raise DeferredQueueEligibilityError, 'Task B independence must be explicitly confirmed by the authoritative Packet'
    end
    validate_task_b_ownership!(task_a_owned_paths, task_b_owned_paths)
    unless task_b_packet_authorized == true
      raise DeferredQueueEligibilityError, 'Task B must already have an executable Owner-authorized Packet'
    end

    blocker = blocker.to_s.strip
    task_b_id = resume_after_task_id.to_s.strip
    task_b_packet_ref = next_authorized_task_packet_ref.to_s.strip
    original_next_action = @next_action.to_s.strip
    raise DeferredQueueStateError, 'deferred blocker must be specific and non-empty' if blocker.empty?
    raise DeferredQueueStateError, 'resume_after_task_id must be non-empty' if task_b_id.empty?
    raise DeferredQueueStateError, 'Task B must be independent from Task A' if task_b_id == @task_id
    if original_next_action.empty? || original_next_action == DEFERRED_RECHECK_ACTION
      raise DeferredQueueStateError, 'Task A must have an original continuation action distinct from the deferred recheck gate'
    end

    inventory = Array(existing_deferred_checkpoints)
    self.class.validate_deferred_limit!(inventory)
    if inventory.any?(&:deferred?)
      raise DeferredQueueLimitError, "maximum deferred tasks is #{MAX_DEFERRED_TASKS}; Task C chaining is prohibited"
    end

    resolve_packet_locator(task_b_packet_ref, @repository)

    @task_lifecycle_state = 'BLOCKED'
    @current_blocker = blocker
    @queue_disposition = QUEUE_DISPOSITION_BLOCKED_DEFERRED
    @resume_after_task_id = task_b_id
    @next_authorized_task_packet_ref = task_b_packet_ref
    @deferred_resume_action = original_next_action
    @deferred_recheck_count = 0
    @next_action = DEFERRED_RECHECK_ACTION
    validate!
    self
  end

  def block_recurrent_transient!(blocker:, blocker_disposition:)
    validate!
    unless deferred_queue_run_consumed?
      raise DeferredQueueStateError, 'Task A has no consumed deferred queue run to recur from'
    end
    unless %w[IN_PROGRESS BLOCKED].include?(@task_lifecycle_state)
      raise DeferredQueueStateError, "terminal Task A state #{@task_lifecycle_state} cannot record a recurrent deferred blocker"
    end
    unless blocker_disposition.to_s == DEFER_ELIGIBLE_BLOCKER
      raise DeferredQueueEligibilityError,
            "recurrent blocker disposition must be #{DEFER_ELIGIBLE_BLOCKER}; ineligible blockers do not enter the queue"
    end

    blocker = blocker.to_s.strip
    raise DeferredQueueStateError, 'recurrent deferred blocker must be specific and non-empty' if blocker.empty?

    @task_lifecycle_state = 'BLOCKED'
    @current_blocker = blocker
    @queue_disposition = QUEUE_DISPOSITION_BLOCKED_DEFERRED
    @next_action = DEFERRED_RECHECK_ACTION
    validate!
    :blocked_deferred_recurrence
  end

  def authorized_deferred_task
    return nil unless deferred?
    return nil if deferred_recheck_consumed?

    {
      task_id: @resume_after_task_id,
      authoritative_packet_ref: @next_authorized_task_packet_ref
    }
  end

  def recheck_deferred_resume_gate!(completed_task:, gate_passed:)
    validate!
    raise DeferredQueueStateError, "task #{@task_id} is not BLOCKED_DEFERRED" unless deferred?
    unless completed_task.is_a?(TaskCheckpoint)
      raise DeferredQueueStateError, 'deferred resume recheck requires the exact Task B checkpoint'
    end
    completed_task.validate!
    unless completed_task.task_id == @resume_after_task_id
      raise DeferredQueueStateError,
            "deferred resume expected Task B '#{@resume_after_task_id}', received '#{completed_task.task_id}'"
    end
    unless completed_task.authoritative_packet_ref == @next_authorized_task_packet_ref
      raise DeferredQueueStateError,
            "Task B checkpoint Packet ref does not match '#{@next_authorized_task_packet_ref}'"
    end
    if completed_task.deferred?
      raise DeferredQueueLimitError, 'Task B cannot defer to Task C while Task A occupies the single deferred slot'
    end
    unless DEFERRED_TASK_TERMINAL_STATES.include?(completed_task.task_lifecycle_state)
      raise DeferredQueueStateError,
            "Task B must reach a terminal end-of-task state before recheck (received #{completed_task.task_lifecycle_state})"
    end
    unless gate_passed == true || gate_passed == false
      raise DeferredQueueStateError, 'gate_passed must be exactly true or false'
    end
    if @deferred_recheck_count >= MAX_AUTOMATIC_END_RECHECKS
      raise DeferredQueueLimitError,
            "maximum automatic end rechecks is #{MAX_AUTOMATIC_END_RECHECKS}"
    end

    @deferred_recheck_count += 1
    unless gate_passed
      validate!
      return :blocked_deferred
    end

    resumed_action = @deferred_resume_action
    @task_lifecycle_state = 'IN_PROGRESS'
    @current_blocker = nil
    @next_action = resumed_action
    @queue_disposition = nil
    validate!
    :resumed
  end

  def resolve_authoritative_packet(base_repo = nil)
    locator = @authoritative_packet_ref.to_s.strip
    raise ResolutionError, 'authoritative_packet_ref is empty' if locator.empty?

    if locator == 'ORIGINAL_TASK_RULES_INHERITED: YES'
      raise ResolutionError, 'authoritative_packet_ref is only an inheritance affirmation without an explicit locator'
    end

    if locator =~ %r{\A(conversation|session|chat)://}i
      raise ResolutionError, "authoritative_packet_ref uses ephemeral session URI '#{locator}' which cannot be resolved by a fresh Worker without chat memory"
    end

    resolve_packet_locator(locator, base_repo)
  end

  def resolve_packet_locator(locator, base_repo = nil)
    locator = locator.to_s.strip
    raise ResolutionError, 'packet locator is empty' if locator.empty?
    if locator == 'ORIGINAL_TASK_RULES_INHERITED: YES'
      raise ResolutionError, 'packet locator is only an inheritance affirmation without an explicit locator'
    end
    if locator =~ %r{\A(conversation|session|chat)://}i
      raise ResolutionError, "packet locator uses ephemeral session URI '#{locator}' which cannot be resolved by a fresh Worker without chat memory"
    end

    root = base_repo || @repository || Dir.pwd

    # Handle git-backed locator: git:<ref>:<path>
    if locator =~ /\Agit:([^:]+):(.+)\z/
      git_ref = Regexp.last_match(1)
      git_path = Regexp.last_match(2)
      stdout, _stderr, status = Open3.capture3('git', 'show', "#{git_ref}:#{git_path}", chdir: root)
      if status.success? && !stdout.empty?
        return { status: :resolved, source: :git, locator: locator, content: stdout }
      else
        raise ResolutionError, "Git-backed packet locator '#{locator}' could not be resolved from Git object database"
      end
    end

    # Handle file path (with optional #section-anchor)
    clean_path = locator.split('#').first
    target_path = File.expand_path(clean_path, root)

    # Also check if it was relative to worktree
    if !File.file?(target_path) && @worktree && File.directory?(@worktree)
      worktree_target = File.expand_path(clean_path, @worktree)
      target_path = worktree_target if File.file?(worktree_target)
    end

    if File.file?(target_path)
      content = File.read(target_path, encoding: 'UTF-8')
      return { status: :resolved, source: :file, path: target_path, content: content }
    end

    raise ResolutionError, "Durable packet file not found at '#{target_path}' (locator: '#{locator}')"
  end

  private

  def optional_value(attrs, field)
    return attrs[field] if attrs.key?(field)

    attrs[field.to_s]
  end

  def validate_queue_fields(errors)
    if @queue_disposition.nil?
      populated = OPTIONAL_QUEUE_FIELDS.reject { |field| field == :queue_disposition }.select do |field|
        !send(field).nil?
      end
      return if populated.empty?

      unless deferred_queue_identity_complete?
        errors << "queue-specific fields require queue_disposition or a complete consumed queue-run marker: #{populated.join(', ')}"
      end
      validate_deferred_queue_identity(errors, 'consumed queue-run marker')
      unless @deferred_recheck_count == MAX_AUTOMATIC_END_RECHECKS
        errors << "consumed queue-run marker requires deferred_recheck_count #{MAX_AUTOMATIC_END_RECHECKS}"
      end
      return
    end

    unless @queue_disposition == QUEUE_DISPOSITION_BLOCKED_DEFERRED
      errors << "invalid queue_disposition: #{@queue_disposition} (must be #{QUEUE_DISPOSITION_BLOCKED_DEFERRED})"
      return
    end

    errors << 'BLOCKED_DEFERRED requires task_lifecycle_state BLOCKED' unless @task_lifecycle_state == 'BLOCKED'
    if @current_blocker.nil? || @current_blocker.to_s.strip.empty?
      errors << 'BLOCKED_DEFERRED requires a specific current_blocker'
    end
    unless @next_action == DEFERRED_RECHECK_ACTION
      errors << "BLOCKED_DEFERRED next_action must be #{DEFERRED_RECHECK_ACTION}"
    end
    validate_deferred_queue_identity(errors)
    unless @deferred_recheck_count.is_a?(Integer) && @deferred_recheck_count.between?(0, MAX_AUTOMATIC_END_RECHECKS)
      errors << "deferred_recheck_count must be an integer from 0 to #{MAX_AUTOMATIC_END_RECHECKS}"
    end
  end

  def deferred_queue_identity_complete?
    !@resume_after_task_id.to_s.strip.empty? &&
      !@next_authorized_task_packet_ref.to_s.strip.empty? &&
      !@deferred_resume_action.to_s.strip.empty?
  end

  def validate_deferred_queue_identity(errors, label = 'BLOCKED_DEFERRED')
    if @resume_after_task_id.nil? || @resume_after_task_id.strip.empty?
      errors << "#{label} requires resume_after_task_id"
    elsif @resume_after_task_id == @task_id
      errors << 'resume_after_task_id must identify an independent Task B'
    end
    if @next_authorized_task_packet_ref.nil? || @next_authorized_task_packet_ref.strip.empty?
      errors << "#{label} requires next_authorized_task_packet_ref"
    elsif @next_authorized_task_packet_ref == 'ORIGINAL_TASK_RULES_INHERITED: YES'
      errors << 'next_authorized_task_packet_ref must be an explicit durable locator'
    elsif @next_authorized_task_packet_ref =~ %r{\A(conversation|session|chat)://}i
      errors << 'next_authorized_task_packet_ref must be durable, not an ephemeral session URI'
    end
    if @deferred_resume_action.nil? || @deferred_resume_action.strip.empty?
      errors << "#{label} requires deferred_resume_action"
    elsif @deferred_resume_action == DEFERRED_RECHECK_ACTION
      errors << 'deferred_resume_action must preserve Task A continuation separately from the recheck gate'
    end
  end

  def validate_task_b_ownership!(task_a_owned_paths, task_b_owned_paths)
    return if task_a_owned_paths.nil? && task_b_owned_paths.nil?

    if task_a_owned_paths.nil? || task_b_owned_paths.nil?
      raise DeferredQueueEligibilityError, 'Task A and Task B ownership evidence must be supplied together'
    end
    if Array(task_a_owned_paths).empty? || Array(task_b_owned_paths).empty?
      raise DeferredQueueEligibilityError, 'Task A and Task B ownership evidence must be non-empty'
    end
    if self.class.ownership_surfaces_overlap?(task_a_owned_paths, task_b_owned_paths)
      raise DeferredQueueEligibilityError, 'Task A and Task B ownership surfaces overlap; Task B is not independent'
    end
  end

  def self.path_surfaces_overlap?(left, right)
    return true if [left, right].any? { |surface| surface == '.' || surface == File::SEPARATOR }

    left == right || left.start_with?("#{right}#{File::SEPARATOR}") || right.start_with?("#{left}#{File::SEPARATOR}")
  end

  def self.normalize_ownership_surface(path)
    value = path.to_s.strip
    raise DeferredQueueStateError, 'ownership surface must be non-empty' if value.empty?

    normalized = Pathname.new(value).cleanpath.to_s
    if normalized == '..' || normalized.start_with?("..#{File::SEPARATOR}")
      raise DeferredQueueStateError, "ownership surface escapes its namespace: #{value}"
    end
    normalized
  end

  private_class_method :path_surfaces_overlap?, :normalize_ownership_surface

  class ValidationError < StandardError; end
  class LoadError < StandardError; end
  class ConcurrencyError < StandardError; end
  class ResolutionError < StandardError; end
  class DeferredQueueError < StandardError; end
  class DeferredQueueEligibilityError < DeferredQueueError; end
  class DeferredQueueStateError < DeferredQueueError; end
  class DeferredQueueLimitError < DeferredQueueError; end
  class WriterEvidenceError < StandardError; end
end

# TaskReconciler performs bounded reconciliation between a durable checkpoint
# and the live repository/worktree/git/PR/authorization state.
class TaskReconciler
  attr_reader :checkpoint, :options

  def self.conversation_authorized?(action, auth_tokens, authorization_target: nil)
    allocate.send(:conversation_authorized?, action, auth_tokens,
                  authorization_target: authorization_target)
  end

  ReconciliationResult = Struct.new(
    :verdict,
    :reason,
    :recommended_action,
    :checkpoint,
    :live_state,
    keyword_init: true
  ) do
    def to_h
      {
        'verdict' => verdict,
        'reason' => reason,
        'recommended_action' => recommended_action,
        'checkpoint_task_id' => checkpoint&.task_id,
        'live_state' => live_state
      }
    end

    def to_text
      <<~TEXT.strip
        RECONCILIATION_VERDICT: #{verdict}
        REASON: #{reason}
        RECOMMENDED_ACTION: #{recommended_action}
        TASK_ID: #{checkpoint&.task_id}
      TEXT
    end
  end

  def initialize(checkpoint, options = {})
    @checkpoint = checkpoint
    @options = options
  end

  def reconcile
    if options[:worktree] && !options[:worktree].to_s.strip.empty?
      begin
        expected_worktree = File.realpath(checkpoint.worktree)
        requested_worktree = File.realpath(options[:worktree])
      rescue SystemCallError => e
        reason = if e.is_a?(Errno::ENOENT)
                   "Worktree directory does not exist (exact locator could not be verified: #{e.class})"
                 else
                   "Exact worktree locator could not be verified: #{e.class}"
                 end
        return ReconciliationResult.new(
          verdict: 'STOP_UNRESOLVED',
          reason: reason,
          recommended_action: 'Use the existing checkpoint worktree and verify both locators',
          checkpoint: checkpoint,
          live_state: { worktree: options[:worktree] }
        )
      end

      unless requested_worktree == expected_worktree
        return ReconciliationResult.new(
          verdict: 'STOP_UNRESOLVED',
          reason: "Exact worktree locator mismatch: checkpoint requires '#{checkpoint.worktree}' but request resolves to '#{requested_worktree}'",
          recommended_action: 'Use the checkpoint worktree locator or obtain an updated authoritative checkpoint',
          checkpoint: checkpoint,
          live_state: { worktree: options[:worktree], resolved_worktree: requested_worktree }
        )
      end
    end

    live = capture_live_state

    # Guard 1: Repository Identity
    if live[:repository] && File.expand_path(live[:repository]) != File.expand_path(checkpoint.repository)
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Repository identity mismatch: checkpoint expects '#{checkpoint.repository}' but live is '#{live[:repository]}'",
        recommended_action: 'Switch to expected repository or verify checkpoint path',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    # Guard 2: Worktree existence & access
    if live[:worktree] && !File.directory?(live[:worktree])
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Worktree directory does not exist or is not accessible: '#{live[:worktree]}'",
        recommended_action: 'Re-create worktree or verify checkout path',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    # Guard 2b: Authoritative Packet Resolution
    begin
      checkpoint.resolve_authoritative_packet(live[:repository])
    rescue TaskCheckpoint::ResolutionError => e
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Authoritative packet ref cannot be resolved across sessions: #{e.message}",
        recommended_action: 'Specify a durable repo-relative or git-backed authoritative packet locator',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if checkpoint.deferred? && !checkpoint.deferred_recheck_consumed?
      begin
        checkpoint.resolve_packet_locator(checkpoint.next_authorized_task_packet_ref, live[:repository])
      rescue TaskCheckpoint::ResolutionError => e
        return ReconciliationResult.new(
          verdict: 'STOP_UNRESOLVED',
          reason: "Deferred Task B packet ref cannot be resolved across sessions: #{e.message}",
          recommended_action: 'Provide the existing executable Owner-authorized Task B Packet through a durable locator',
          checkpoint: checkpoint,
          live_state: live
        )
      end
    end

    # Guard 3: PR / Task Terminal Status
    if checkpoint.task_lifecycle_state == 'COMPLETED'
      return ReconciliationResult.new(
        verdict: 'ALREADY_COMPLETED',
        reason: "Task #{checkpoint.task_id} is already in terminal state COMPLETED",
        recommended_action: 'Close task lifecycle or archive checkpoint',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if live[:pr_state] == 'MERGED' || checkpoint.pr_state == 'MERGED'
      return ReconciliationResult.new(
        verdict: 'ALREADY_COMPLETED',
        reason: "Associated PR (#{live[:pr_number] || checkpoint.pr_number || 'remote'}) has already been merged into target branch",
        recommended_action: 'Close PR lifecycle, verify postmerge status, and archive checkpoint',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    # Guard 4: Direct Owner Authorization Boundary Check
    if action_requires_owner_auth?(checkpoint.next_action, checkpoint.authorization_boundary)
      conversation_auths = options[:conversation_authorizations] || []
      authorization_target = options[:authorization_target].to_s.strip
      unless !authorization_target.empty? &&
             conversation_authorized?(checkpoint.next_action, conversation_auths,
                                      authorization_target: authorization_target)
        return ReconciliationResult.new(
          verdict: 'AUTHORIZATION_REQUIRED',
          reason: "Next action '#{checkpoint.next_action}' requires explicit Owner authorization naming its exact target identity in the current conversation; quoted tokens in checkpoint do not transfer",
          recommended_action: "Request explicit Owner authorization for '#{checkpoint.next_action}' naming the exact target identity in the current conversation before proceeding",
          checkpoint: checkpoint,
          live_state: live
        )
      end
    end

    # Guard 5: Live Git Tree & HEAD Comparison (Root-Cause-First)
    head_matches = (live[:head] == checkpoint.current_head) || checkpoint.current_head == 'UNCOMMITTED'
    tree_matches = (live[:tree] == checkpoint.current_tree) || checkpoint.current_tree == 'UNCOMMITTED'

    if head_matches && tree_matches
      # Tree matches checkpoint state exactly
      return reconcile_deferred_checkpoint(live) if checkpoint.deferred?

      if checkpoint.current_blocker && !checkpoint.current_blocker.strip.empty?
        return ReconciliationResult.new(
          verdict: 'CONTINUE',
          reason: "Resuming interrupted root-cause investigation: #{checkpoint.current_blocker}",
          recommended_action: checkpoint.next_action,
          checkpoint: checkpoint,
          live_state: live
        )
      else
        return ReconciliationResult.new(
          verdict: 'CONTINUE',
          reason: 'Live repository and worktree state match checkpoint cleanly',
          recommended_action: checkpoint.next_action,
          checkpoint: checkpoint,
          live_state: live
        )
      end
    end

    # Live HEAD/tree differed from checkpoint: determine compatible advancement vs conflict
    compatible_status = evaluate_git_advancement(live)

    case compatible_status[:status]
    when :compatible_advancement
      ReconciliationResult.new(
        verdict: 'RECONCILE_LIVE_STATE',
        reason: "Live state advanced compatibly from #{checkpoint.current_head[0..7]} to #{live[:head][0..7]} without semantic conflict",
        recommended_action: "Update checkpoint current_head/current_tree to live values and continue with '#{checkpoint.next_action}'",
        checkpoint: checkpoint,
        live_state: live
      )
    when :already_completed
      ReconciliationResult.new(
        verdict: 'ALREADY_COMPLETED',
        reason: compatible_status[:reason] || 'Load-bearing change already merged or committed in live state',
        recommended_action: 'Verify acceptance on current HEAD and finalize task',
        checkpoint: checkpoint,
        live_state: live
      )
    when :material_conflict
      ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Material conflict detected between checkpoint state and live tree: #{compatible_status[:reason]}",
        recommended_action: 'Resolve file/tree conflicts or request Planner Delta before resuming execution',
        checkpoint: checkpoint,
        live_state: live
      )
    else
      ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Unreconciled difference between checkpoint (#{checkpoint.current_head[0..7]}) and live (#{live[:head]&.[](0..7)}): #{compatible_status[:reason]}",
        recommended_action: 'Inspect git tree diff and resolve divergence before continuing',
        checkpoint: checkpoint,
        live_state: live
      )
    end
  end

  private

  def reconcile_deferred_checkpoint(live)
    if checkpoint.deferred_recheck_count >= TaskCheckpoint::MAX_AUTOMATIC_END_RECHECKS
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: 'Task A remains BLOCKED_DEFERRED after its single automatic end-of-task recheck',
        recommended_action: 'Retain the durable checkpoint and request a new Owner/Planner decision; do not recheck again automatically',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    task_b = options[:resume_after_task_checkpoint]
    if task_b && !task_b.is_a?(TaskCheckpoint)
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: 'Deferred Task B evidence is not a TaskCheckpoint record',
        recommended_action: 'Load the exact durable Task B checkpoint before rechecking Task A',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if task_b && task_b.task_id != checkpoint.resume_after_task_id
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Deferred queue expected Task B '#{checkpoint.resume_after_task_id}', received '#{task_b.task_id}'",
        recommended_action: 'Load only the exact Task B named by Task A checkpoint',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if task_b && task_b.authoritative_packet_ref != checkpoint.next_authorized_task_packet_ref
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Deferred Task B checkpoint Packet ref does not match '#{checkpoint.next_authorized_task_packet_ref}'",
        recommended_action: 'Load the exact Task B checkpoint rooted in the Packet named by Task A',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    if task_b&.deferred?
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: 'Task B attempted to defer to Task C while Task A occupies the single deferred slot',
        recommended_action: 'Stop Task B chaining; retain Task A as the only deferred task',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    task_b_state = task_b&.task_lifecycle_state
    if task_b_state.nil?
      return ReconciliationResult.new(
        verdict: 'CONTINUE',
        reason: "Task A is durably BLOCKED_DEFERRED pending the exact authorized independent Task B '#{checkpoint.resume_after_task_id}'",
        recommended_action: deferred_task_execution_action,
        checkpoint: checkpoint,
        live_state: live
      )
    end

    unless TaskCheckpoint::VALID_LIFECYCLE_STATES.include?(task_b_state)
      return ReconciliationResult.new(
        verdict: 'STOP_UNRESOLVED',
        reason: "Deferred Task B lifecycle state is invalid: #{task_b_state}",
        recommended_action: 'Reconcile the exact Task B checkpoint before continuing',
        checkpoint: checkpoint,
        live_state: live
      )
    end

    unless TaskCheckpoint::DEFERRED_TASK_TERMINAL_STATES.include?(task_b_state)
      return ReconciliationResult.new(
        verdict: 'CONTINUE',
        reason: "Authorized Task B '#{checkpoint.resume_after_task_id}' has not reached an end-of-task state (#{task_b_state})",
        recommended_action: deferred_task_execution_action,
        checkpoint: checkpoint,
        live_state: live
      )
    end

    ReconciliationResult.new(
      verdict: 'CONTINUE',
      reason: "Authorized Task B '#{checkpoint.resume_after_task_id}' reached #{task_b_state}; Task C chaining is prohibited and Task A has one recheck available",
      recommended_action: TaskCheckpoint::DEFERRED_RECHECK_ACTION,
      checkpoint: checkpoint,
      live_state: live
    )
  end

  def deferred_task_execution_action
    "EXECUTE_AUTHORIZED_DEFERRED_TASK task_id=#{checkpoint.resume_after_task_id} packet_ref=#{checkpoint.next_authorized_task_packet_ref}"
  end

  def capture_live_state
    {
      repository: options[:repository] || detect_git_repo_root,
      worktree: options[:worktree] || checkpoint.worktree,
      head: options[:head] || detect_git_head,
      tree: options[:tree] || detect_git_tree,
      branch: options[:branch] || detect_git_branch,
      pr_state: options[:pr_state] || checkpoint.pr_state,
      pr_number: options[:pr_number] || checkpoint.pr_number,
      has_conflict: options[:has_conflict] || false,
      compatible_advancement: options[:compatible_advancement]
    }
  end

  def detect_git_repo_root
    stdout, _stderr, status = Open3.capture3('git', 'rev-parse', '--show-toplevel', chdir: checkpoint.worktree)
    status.success? ? stdout.strip : checkpoint.repository
  rescue StandardError
    checkpoint.repository
  end

  def detect_git_head
    stdout, _stderr, status = Open3.capture3('git', 'rev-parse', 'HEAD', chdir: checkpoint.worktree)
    status.success? ? stdout.strip : 'UNKNOWN'
  rescue StandardError
    'UNKNOWN'
  end

  def detect_git_tree
    stdout, _stderr, status = Open3.capture3('git', 'rev-parse', 'HEAD^{tree}', chdir: checkpoint.worktree)
    status.success? ? stdout.strip : 'UNKNOWN'
  rescue StandardError
    'UNKNOWN'
  end

  def detect_git_branch
    stdout, _stderr, status = Open3.capture3('git', 'rev-parse', '--abbrev-ref', 'HEAD', chdir: checkpoint.worktree)
    status.success? ? stdout.strip : 'master'
  rescue StandardError
    'master'
  end

  def action_requires_owner_auth?(action, boundary)
    # Accept the canonical boundary and the legacy alias for resumable
    # checkpoints, but neither boundary requires an authorization-only message.
    return true if boundary =~ /CURRENT_WORKER_CONVERSATION_(?:OWNER|STANDALONE)_AUTH_REQUIRED/i
    return true if boundary =~ /(?:OWNER|STANDALONE)_AUTH/i

    high_risk_actions = %w[MERGE_PR PUSH PUSH_BRANCH DEPLOY DESTRUCTIVE_CLEANUP ACTIVATE_LIVE]
    high_risk_actions.any? { |hra| action.to_s.upcase.include?(hra) }
  end

  def conversation_authorized?(action, auth_tokens, authorization_target: nil)
    return false if auth_tokens.nil? || auth_tokens.empty?

    auth_tokens.any? do |token|
      text = token.to_s.strip
      normalized = text.upcase
      next false if text.length <= 3
      next false if authorization_provenance_invalid?(text)
      next false if normalized.match?(/AUTHORIZATION_HANDOFF_MODE\s*:\s*SEND[_ -]?STANDALONE[_ -]?FIRST/)
      next false if normalized.match?(/SEPARATE[_ -]?AUTHORIZATION[_ -]?ONLY[_ -]?MESSAGE_REQUIRED\s*:\s*YES/)
      next false unless direct_owner_authorization?(normalized)
      next false unless authorization_provenance_valid?(normalized)
      next false unless explicit_authorization?(normalized)

      authorized_scope = authorized_scope_text(text)
      next false unless action_matches_authorized_scope?(
        action,
        authorized_scope,
        authorization_target: authorization_target
      )

      true
    end
  end

  def authorization_provenance_invalid?(text)
    normalized = text.to_s.strip.upcase
    return true if normalized.match?(/\A(?:ASSISTANT|PLANNER|QUOTED|CROSS[\s_-]+CONVERSATION)(?:\b|[_ -])/)

    normalized.each_line.any? do |line|
      line.match?(/\A\s*(?:PROVENANCE|AUTHORIZATION_PROVENANCE|AUTHORIZATION_SOURCE)\s*[:=]\s*(?:ASSISTANT|PLANNER|QUOTED|CROSS[\s_-]+CONVERSATION)(?:\b|[_ -])/)
    end
  end

  def direct_owner_authorization?(normalized)
    normalized.match?(/\bOWNER_DIRECT_PACKET_AUTHORIZATION\b/) ||
      normalized.match?(/OWNER\s+AUTHORIZATION\s*[—:-]/) ||
      normalized.match?(/\bOWNER_DIRECT_MESSAGE\b/) ||
      normalized.match?(/OWNER_ACTION_AUTHORIZATION\s*:\s*(?:PRESENT_IN_CURRENT_OWNER_MESSAGE|REUSED_FROM_PRIOR_OWNER_MESSAGE)/)
  end

  def authorization_provenance_valid?(normalized)
    mode = normalized[/AUTHORIZATION_HANDOFF_MODE\s*:\s*(OWNER_DIRECT_PACKET|SAME_CONVERSATION_PRIOR_AUTH)\b/, 1]
    owner_auth = normalized[/OWNER_ACTION_AUTHORIZATION\s*:\s*(PRESENT_IN_CURRENT_OWNER_MESSAGE|REUSED_FROM_PRIOR_OWNER_MESSAGE)\b/, 1]
    evidence = normalized[/AUTHORIZATION_EVIDENCE\s*:\s*(CURRENT_OWNER_USER_MESSAGE|PRIOR_APPLICABLE_OWNER_USER_MESSAGE)\b/, 1]
    return false unless mode && owner_auth && evidence

    case mode
    when 'OWNER_DIRECT_PACKET'
      owner_auth == 'PRESENT_IN_CURRENT_OWNER_MESSAGE' && evidence == 'CURRENT_OWNER_USER_MESSAGE'
    when 'SAME_CONVERSATION_PRIOR_AUTH'
      owner_auth == 'REUSED_FROM_PRIOR_OWNER_MESSAGE' && evidence == 'PRIOR_APPLICABLE_OWNER_USER_MESSAGE'
    else
      false
    end
  end

  def explicit_authorization?(normalized)
    normalized.match?(/\bI\s+AUTHORIZE\s+EXACTLY\b/) ||
      normalized.match?(/\bOWNER_DIRECT_PACKET_AUTHORIZATION\b/) ||
      normalized.match?(/OWNER_ACTION_AUTHORIZATION\s*:\s*(?:PRESENT_IN_CURRENT_OWNER_MESSAGE|REUSED_FROM_PRIOR_OWNER_MESSAGE)/) ||
      normalized.match?(/\bOWNER_DIRECT_MESSAGE\b.*\bAUTHORIZED\b/m)
  end

  def authorized_scope_text(text)
    cutoff = text.match(/\bNOT\s+AUTHORIZED\s*:/i)
    cutoff ? text[0...cutoff.begin(0)] : text
  end

  def action_matches_authorized_scope?(action, scope, authorization_target: nil)
    requested_action = action.to_s.strip.upcase.tr('_', ' ')
    return false if requested_action.empty?

    requested_target = authorization_target.to_s.strip
    return false if authorization_target && requested_target.empty?

    authorized_action_target_pairs(scope).any? do |authorized_action, authorized_target|
      next false unless authorized_action.upcase.tr('_', ' ') == requested_action

      authorization_target.nil? || authorized_target == requested_target
    end
  end

  def authorized_action_target_pairs(scope)
    pairs = []
    scope.to_s.each_line do |line|
      action_match = line.match(
        /(?:\A|[^\w])(?:AUTHORIZED_)?ACTION\s*[:=]\s*([A-Z][A-Z0-9_]*)/i
      )
      target_match = line.match(
        /(?:\A|[^\w])(?:AUTHORIZED_)?TARGET\s*[:=]\s*(.+?)(?=\s*(?:;|\z))/i
      )
      next unless action_match && target_match

      pairs << [action_match[1].strip.upcase, target_match[1].strip]
    end
    pairs.uniq
  end

  def evaluate_git_advancement(live)
    return { status: :material_conflict, reason: 'Explicit merge conflict or conflicting modifications detected' } if live[:has_conflict]

    if live[:compatible_advancement] == true
      return { status: :compatible_advancement }
    elsif live[:compatible_advancement] == false
      return { status: :material_conflict, reason: 'Conflicting task-owned changes detected in tree diff' }
    end

    # Check git ancestry if live worktree is accessible
    if File.directory?(checkpoint.worktree) && live[:head] != 'UNKNOWN' && checkpoint.current_head != 'UNCOMMITTED'
      # Check if checkpoint HEAD is an ancestor of live HEAD
      _stdout, _stderr, status = Open3.capture3('git', 'merge-base', '--is-ancestor', checkpoint.current_head, live[:head], chdir: checkpoint.worktree)
      if status.success?
        # Check if the diff between checkpoint HEAD and live HEAD touches forbidden or conflicting files
        diff_stdout, _diff_err, diff_status = Open3.capture3('git', 'diff', '--name-only', checkpoint.current_head, live[:head], chdir: checkpoint.worktree)
        if diff_status.success?
          # If changes exist, check if there are merge conflicts
          conflict_stdout, _conflict_err, conflict_status = Open3.capture3('git', 'status', '--porcelain', chdir: checkpoint.worktree)
          if conflict_status.success? && conflict_stdout.include?('UU ')
            return { status: :material_conflict, reason: 'Git unmerged path conflict present' }
          end
          return { status: :compatible_advancement }
        end
      end
    end

    { status: :unresolved_divergence, reason: 'HEAD/tree mismatch without verifiable ancestor lineage' }
  rescue StandardError => e
    { status: :material_conflict, reason: "Git evaluation error: #{e.message}" }
  end
end

# PublicationLiveStateClassifier resolves what is already true about a task's
# Git publication lifecycle from freshly-queried remote facts. It is a
# DERIVED LIVE VIEW only: every call re-resolves remote branch and PR state
# at classification time rather than trusting a checkpoint's recorded
# pr_state, and it persists nothing (no save/load, no new lifecycle store).
# The canonical lifecycle axes (PR_PUBLICATION_STATUS, POSTMERGE_LIFECYCLE_STATUS,
# BRANCH_CLEANUP_STATUS, FULL_PR_LIFECYCLE_CLOSED) and the Git action tiers in
# operational-gates.md remain authoritative; this classifier only tells a
# caller what is already true so it can avoid replaying or incorrectly
# skipping a Ready/Merge/postmerge action. It never performs or authorizes a
# new external action.
class PublicationLiveStateClassifier
  STATE_LOCAL_ONLY = 'LOCAL_ONLY'
  STATE_REMOTE_BRANCH_ONLY = 'REMOTE_BRANCH_ONLY'
  STATE_DRAFT_PR_OPEN = 'DRAFT_PR_OPEN'
  STATE_READY_PR_OPEN = 'READY_PR_OPEN'
  STATE_MERGED_POSTMERGE_PENDING = 'MERGED_POSTMERGE_PENDING'
  STATE_MERGED_POSTMERGE_COMPLETE = 'MERGED_POSTMERGE_COMPLETE'
  STATE_IDENTITY_CONFLICT = 'IDENTITY_CONFLICT'

  VALID_STATES = [
    STATE_LOCAL_ONLY, STATE_REMOTE_BRANCH_ONLY, STATE_DRAFT_PR_OPEN,
    STATE_READY_PR_OPEN, STATE_MERGED_POSTMERGE_PENDING,
    STATE_MERGED_POSTMERGE_COMPLETE, STATE_IDENTITY_CONFLICT
  ].freeze

  ACTION_SKIP_ALREADY_COMPLETE = 'SKIP_ALREADY_COMPLETE'
  ACTION_VERIFY_OR_COMPLETE_MISSING_POSTMERGE = 'VERIFY_OR_COMPLETE_MISSING_POSTMERGE'
  ACTION_REUSE_VERIFIED_EVIDENCE_OR_VERIFY_ONLY_IF_MISSING = 'REUSE_VERIFIED_EVIDENCE_OR_VERIFY_ONLY_IF_MISSING'
  ACTION_COMPLETION_HANDOFF = 'COMPLETION_HANDOFF'
  ACTION_STOP_UNRESOLVED = 'STOP_UNRESOLVED'

  GH_PR_FIELDS = 'number,url,state,isDraft,headRefName,baseRefName,headRefOid,mergeCommit,mergedAt'

  Classification = Struct.new(
    :state, :reason, :ready_action, :merge_action, :postmerge_action,
    :terminal_action, :resolved_pr, :remote_branch_exists, keyword_init: true
  ) do
    def conflict?
      state == PublicationLiveStateClassifier::STATE_IDENTITY_CONFLICT
    end
  end

  def initialize(branch:, repository: nil, worktree: nil, remote_name: 'origin',
                 named_pr_number: nil, expected_head_sha: nil, postmerge_evidence: nil,
                 remote_branch_exists_fetcher: nil, pr_by_number_fetcher: nil,
                 prs_by_branch_fetcher: nil)
    raise ArgumentError, 'branch must be a non-empty string' if branch.to_s.strip.empty?

    @branch = branch.to_s.strip
    @repository = repository
    @worktree = worktree || repository
    @remote_name = (remote_name || 'origin').to_s
    @named_pr_number = named_pr_number
    @expected_head_sha = expected_head_sha
    @postmerge_evidence = postmerge_evidence
    @remote_branch_exists_fetcher = remote_branch_exists_fetcher || method(:default_remote_branch_exists)
    @pr_by_number_fetcher = pr_by_number_fetcher || method(:default_pr_by_number)
    @prs_by_branch_fetcher = prs_by_branch_fetcher || method(:default_prs_by_branch)
  end

  def classify
    remote_branch_exists = begin
      @remote_branch_exists_fetcher.call(@branch)
    rescue PrLookupError => e
      return conflict_result("live remote branch state could not be independently resolved: #{e.message}")
    end

    prs = begin
      lookup_prs
    rescue PrLookupError => e
      return conflict_result("live PR state could not be independently resolved: #{e.message}")
    end

    resolved_pr, conflict = resolve_identity(prs)
    return conflict if conflict

    build_classification(resolved_pr, remote_branch_exists)
  end

  # Real, network-backed default fetchers (LIVE_PR_FETCH_IMPLEMENTED). Tests
  # and other callers inject deterministic fetchers instead of exercising
  # these directly; they are exposed as class methods so they stay
  # independently inspectable and reusable outside this class.
  def self.fetch_remote_branch_exists_via_git(branch, remote_name:, worktree: nil)
    _stdout, stderr, status = Open3.capture3(
      'git', 'ls-remote', '--exit-code', '--heads', remote_name, branch, chdir: worktree || Dir.pwd
    )
    return true if status.success?
    return false if status.exitstatus == 2

    raise PrLookupError, "git ls-remote failed: #{stderr.strip}"
  rescue Errno::ENOENT => e
    raise PrLookupError, "git executable unavailable: #{e.message}"
  end

  def self.fetch_pr_by_number_via_gh(number, repo_slug: nil, worktree: nil)
    args = ['gh', 'pr', 'view', number.to_s, '--json', GH_PR_FIELDS]
    args += ['--repo', repo_slug] if repo_slug
    stdout, stderr, status = Open3.capture3(*args, chdir: worktree || Dir.pwd)
    if !status.success?
      return nil if stderr.to_s =~ /could not resolve to a pullrequest|no pull requests found|not found/i

      raise PrLookupError, "gh pr view failed: #{stderr.strip}"
    end
    normalize_gh_pr(JSON.parse(stdout))
  rescue Errno::ENOENT => e
    raise PrLookupError, "gh executable unavailable: #{e.message}"
  rescue JSON::ParserError => e
    raise PrLookupError, "gh pr view returned malformed JSON: #{e.message}"
  end

  def self.fetch_prs_by_branch_via_gh(branch, repo_slug: nil, worktree: nil)
    args = ['gh', 'pr', 'list', '--head', branch, '--state', 'all', '--json', GH_PR_FIELDS]
    args += ['--repo', repo_slug] if repo_slug
    stdout, stderr, status = Open3.capture3(*args, chdir: worktree || Dir.pwd)
    raise PrLookupError, "gh pr list failed: #{stderr.strip}" unless status.success?

    JSON.parse(stdout).map { |pr_data| normalize_gh_pr(pr_data) }
  rescue Errno::ENOENT => e
    raise PrLookupError, "gh executable unavailable: #{e.message}"
  rescue JSON::ParserError => e
    raise PrLookupError, "gh pr list returned malformed JSON: #{e.message}"
  end

  def self.detect_repo_slug(worktree)
    stdout, _stderr, status = Open3.capture3('git', 'remote', 'get-url', 'origin', chdir: worktree || Dir.pwd)
    return nil unless status.success?

    match = %r{github\.com[:/]([^/]+)/(.+?)(?:\.git)?\z}.match(stdout.strip)
    match ? "#{match[1]}/#{match[2]}" : nil
  rescue StandardError
    nil
  end

  def self.normalize_gh_pr(data)
    {
      number: data['number'],
      url: data['url'],
      state: data['state'],
      draft: data['isDraft'] == true,
      head_ref: data['headRefName'],
      base_ref: data['baseRefName'],
      head_sha: data['headRefOid'],
      merge_commit_sha: data.dig('mergeCommit', 'oid'),
      merged_at: data['mergedAt']
    }
  end

  private_class_method :normalize_gh_pr

  private

  def default_remote_branch_exists(branch)
    self.class.fetch_remote_branch_exists_via_git(branch, remote_name: @remote_name, worktree: @worktree)
  end

  def default_pr_by_number(number)
    self.class.fetch_pr_by_number_via_gh(number, repo_slug: repo_slug, worktree: @worktree)
  end

  def default_prs_by_branch(branch)
    self.class.fetch_prs_by_branch_via_gh(branch, repo_slug: repo_slug, worktree: @worktree)
  end

  def repo_slug
    @repo_slug ||= self.class.detect_repo_slug(@worktree)
  end

  def lookup_prs
    if @named_pr_number
      pr = @pr_by_number_fetcher.call(@named_pr_number)
      pr.nil? ? [] : [pr]
    else
      Array(@prs_by_branch_fetcher.call(@branch))
    end
  end

  def resolve_identity(prs)
    if @named_pr_number
      resolve_named_identity(prs)
    else
      resolve_branch_identity(prs)
    end
  end

  def resolve_named_identity(prs)
    target = prs.first
    return [nil, conflict_result("Packet-named PR ##{@named_pr_number} does not exist")] if target.nil?

    if target[:head_ref] != @branch
      return [nil, conflict_result(
        "Packet-named PR ##{@named_pr_number} points to branch '#{target[:head_ref]}', not the task branch '#{@branch}'"
      )]
    end
    return [nil, conflict_result(lineage_conflict_reason(target))] if lineage_mismatch?(target)

    [target, nil]
  end

  def resolve_branch_identity(prs)
    open_candidates = prs.select { |candidate| candidate[:state] == 'OPEN' }
    if open_candidates.size > 1
      numbers = open_candidates.map { |candidate| "##{candidate[:number]}" }.join(', ')
      return [nil, conflict_result("more than one open PR ambiguously claims task branch '#{@branch}': #{numbers}")]
    end

    resolved = open_candidates.first || prs.find { |candidate| candidate[:state] == 'MERGED' } || prs.first
    return [nil, conflict_result(lineage_conflict_reason(resolved))] if resolved && lineage_mismatch?(resolved)

    [resolved, nil]
  end

  def lineage_mismatch?(pr)
    return false if @expected_head_sha.to_s.strip.empty?
    return false if pr[:state] == 'MERGED'
    return false if pr[:head_sha].to_s.strip.empty?

    pr[:head_sha] != @expected_head_sha
  end

  def lineage_conflict_reason(pr)
    "live PR ##{pr[:number]} head #{pr[:head_sha]} is inconsistent with expected task lineage #{@expected_head_sha}"
  end

  def build_classification(resolved_pr, remote_branch_exists)
    return no_pr_classification(remote_branch_exists) if resolved_pr.nil?

    case resolved_pr[:state]
    when 'MERGED'
      merged_classification(resolved_pr, remote_branch_exists)
    when 'OPEN'
      open_classification(resolved_pr, remote_branch_exists)
    else
      no_pr_classification(remote_branch_exists, closed_pr: resolved_pr)
    end
  end

  def no_pr_classification(remote_branch_exists, closed_pr: nil)
    if remote_branch_exists
      reason = closed_pr ? "prior PR ##{closed_pr[:number]} was closed without merging; remote branch '#{@branch}' still exists" : "remote branch '#{@branch}' exists with no associated pull request"
      Classification.new(state: STATE_REMOTE_BRANCH_ONLY, reason: reason, resolved_pr: closed_pr, remote_branch_exists: true)
    else
      reason = closed_pr ? "prior PR ##{closed_pr[:number]} was closed without merging and remote branch '#{@branch}' no longer exists" : "no remote branch '#{@branch}' and no associated pull request"
      Classification.new(state: STATE_LOCAL_ONLY, reason: reason, resolved_pr: closed_pr, remote_branch_exists: false)
    end
  end

  def open_classification(pr, remote_branch_exists)
    if pr[:draft]
      Classification.new(
        state: STATE_DRAFT_PR_OPEN, reason: "PR ##{pr[:number]} is open as a draft",
        resolved_pr: pr, remote_branch_exists: remote_branch_exists
      )
    else
      Classification.new(
        state: STATE_READY_PR_OPEN, reason: "PR ##{pr[:number]} is open and already marked ready",
        ready_action: ACTION_SKIP_ALREADY_COMPLETE,
        resolved_pr: pr, remote_branch_exists: remote_branch_exists
      )
    end
  end

  def merged_classification(pr, remote_branch_exists)
    if postmerge_evidence_matches?(pr)
      Classification.new(
        state: STATE_MERGED_POSTMERGE_COMPLETE,
        reason: "PR ##{pr[:number]} is merged and verified postmerge evidence matches this exact identity",
        ready_action: ACTION_SKIP_ALREADY_COMPLETE, merge_action: ACTION_SKIP_ALREADY_COMPLETE,
        postmerge_action: ACTION_REUSE_VERIFIED_EVIDENCE_OR_VERIFY_ONLY_IF_MISSING,
        terminal_action: ACTION_COMPLETION_HANDOFF,
        resolved_pr: pr, remote_branch_exists: remote_branch_exists
      )
    else
      Classification.new(
        state: STATE_MERGED_POSTMERGE_PENDING,
        reason: "PR ##{pr[:number]} is merged but no exact-identity verified postmerge evidence is present",
        ready_action: ACTION_SKIP_ALREADY_COMPLETE, merge_action: ACTION_SKIP_ALREADY_COMPLETE,
        postmerge_action: ACTION_VERIFY_OR_COMPLETE_MISSING_POSTMERGE,
        resolved_pr: pr, remote_branch_exists: remote_branch_exists
      )
    end
  end

  def postmerge_evidence_matches?(pr)
    return false if @postmerge_evidence.nil?

    evidence = @postmerge_evidence
    verified = evidence.key?(:verified) ? evidence[:verified] : evidence['verified']
    return false unless verified == true

    evidence_number = evidence[:pr_number] || evidence['pr_number']
    return false if evidence_number.nil? || evidence_number.to_s != pr[:number].to_s

    evidence_sha = evidence[:merge_commit_sha] || evidence['merge_commit_sha']
    return true if evidence_sha.to_s.strip.empty? || pr[:merge_commit_sha].to_s.strip.empty?

    evidence_sha.to_s == pr[:merge_commit_sha].to_s
  end

  def conflict_result(reason)
    Classification.new(
      state: STATE_IDENTITY_CONFLICT, reason: reason,
      ready_action: ACTION_STOP_UNRESOLVED, merge_action: ACTION_STOP_UNRESOLVED,
      postmerge_action: ACTION_STOP_UNRESOLVED
    )
  end

  class PrLookupError < StandardError; end
end

# DurableCommandCapture persists the exact terminal evidence of a task-owned
# long-running command to one canonical task-owned location, so a Judge or a
# resuming Worker never has to treat UI/subagent streaming as the sole
# authority for a load-bearing result. It never captures the parent
# environment; only the exact argv, exact stdout/stderr, exit status, and
# start/end timestamps are persisted.
class DurableCommandCapture
  SCHEMA_VERSION = 1
  SUPPORTED_SCHEMA_VERSIONS = [1].freeze

  # FD 9 is reserved by the epoch runner. Keep the task lock explicitly
  # allowlisted for durability and nested lineage, but give it a separate
  # child slot so ordinary protected children receive FD 9 closed.
  INHERITED_TASK_LOCK_CHILD_FD = 10
  INHERITED_HEAVY_SCIENTIFIC_LEASE_CHILD_FD = 11

  VERDICT_PASS = 'PASS'
  VERDICT_FAIL = 'FAIL'
  VERDICT_UNKNOWN_UNVERIFIABLE = 'UNKNOWN_UNVERIFIABLE'

  EVIDENCE_COMPLETE = 'EVIDENCE_COMPLETE'
  EVIDENCE_UNKNOWN_UNVERIFIABLE = 'EVIDENCE_UNKNOWN_UNVERIFIABLE'

  attr_accessor :schema_version, :command, :stdout, :stderr, :exit_status, :started_at, :ended_at,
                :wrapper_signal, :execution_identity, :task_id, :execution_id

  def initialize(attrs = {})
    has_schema = attrs.key?(:schema_version) || attrs.key?('schema_version')
    raw_schema = attrs[:schema_version] || attrs['schema_version']
    if has_schema
      unless SUPPORTED_SCHEMA_VERSIONS.include?(raw_schema)
        raise ValidationError, "unsupported durable capture schema_version: #{raw_schema.inspect}"
      end
      @schema_version = raw_schema
    else
      @schema_version = SCHEMA_VERSION
    end

    @command = Array(attrs[:command] || attrs['command']).map(&:to_s)
    @exit_status = attrs.key?(:exit_status) ? attrs[:exit_status] : attrs['exit_status']
    @started_at = (attrs[:started_at] || attrs['started_at'])&.to_s
    @ended_at = (attrs[:ended_at] || attrs['ended_at'])&.to_s
    @wrapper_signal = attrs[:wrapper_signal] || attrs['wrapper_signal']
    @execution_identity = attrs[:execution_identity] || attrs['execution_identity']

    # Binary-safe string decoding
    @stdout = self.class.decode_stream(attrs, :stdout)
    @stderr = self.class.decode_stream(attrs, :stderr)

    # Task and execution identity binding metadata (structured validation, non-cryptographic)
    exec_id_map = @execution_identity.is_a?(Hash) ? @execution_identity : nil
    @task_id = (attrs[:task_id] || attrs['task_id'] || exec_id_map&.[]('task_id'))&.to_s
    @execution_id = (attrs[:execution_id] || attrs['execution_id'] || exec_id_map&.[]('execution_id'))&.to_s
  end

  def complete?
    !@command.empty? && !@exit_status.nil? &&
      !@started_at.to_s.strip.empty? && !@ended_at.to_s.strip.empty?
  end

  def verdict
    return VERDICT_UNKNOWN_UNVERIFIABLE unless complete?

    @exit_status == 0 ? VERDICT_PASS : VERDICT_FAIL
  end

  def to_h
    data = {
      'schema_version' => @schema_version,
      'command' => @command,
      'exit_status' => @exit_status,
      'started_at' => @started_at,
      'ended_at' => @ended_at
    }
    self.class.encode_stream!(data, 'stdout', @stdout)
    self.class.encode_stream!(data, 'stderr', @stderr)

    data['task_id'] = @task_id if @task_id
    data['execution_id'] = @execution_id if @execution_id
    data['execution_identity'] = @execution_identity if @execution_identity
    data
  end

  def to_json(*args)
    JSON.pretty_generate(to_h, *args)
  end

  def self.decode_stream(attrs, key)
    enc_key = "#{key}_encoding"
    val = attrs[key] || attrs[key.to_s]
    enc = attrs[enc_key.to_sym] || attrs[enc_key]
    return '' if val.nil?

    if enc.to_s == 'base64'
      Base64.decode64(val.to_s)
    else
      val.to_s
    end
  end

  def self.encode_stream!(data, key, str)
    str = str.to_s
    if str.dup.force_encoding(Encoding::UTF_8).valid_encoding?
      data[key] = str
    else
      data["#{key}_encoding"] = 'base64'
      data[key] = Base64.strict_encode64(str.b)
    end
  end

  def self.from_json(json_str)
    data = JSON.parse(json_str)
    raise ValidationError, 'durable capture JSON root must be an Object' unless data.is_a?(Hash)
    unless data.key?('schema_version')
      raise ValidationError, 'durable capture missing schema_version'
    end
    unless SUPPORTED_SCHEMA_VERSIONS.include?(data['schema_version'])
      raise ValidationError, "unsupported durable capture schema_version: #{data['schema_version'].inspect}"
    end

    new(data)
  rescue JSON::ParserError => e
    raise ValidationError, "Malformed durable capture JSON: #{e.message}"
  end

  def self.load(file_path)
    raise LoadError, "Durable capture file is a symlink: #{file_path}" if File.symlink?(file_path)
    raise LoadError, "Durable capture file does not exist: #{file_path}" unless File.file?(file_path)

    from_json(File.read(file_path, encoding: 'UTF-8'))
  end

  def save(file_path)
    dir = File.dirname(file_path)
    FileUtils.mkdir_p(dir)
    temp_path = "#{file_path}.tmp.#{Process.pid}.#{Time.now.to_i}"
    File.open(temp_path, 'w:UTF-8') { |f| f.write(to_json) }
    File.rename(temp_path, file_path)
    true
  end

  def self.default_path(repo_root, task_id, capture_id)
    File.join(repo_root, '.fable', 'checkpoints', task_id.to_s, 'captures', "#{capture_id}.json")
  end

  # Captures the minimal load-bearing execution identity of the host and
  # launcher/runtime that produced it. Git identity is recorded only when the
  # execution worktree itself is the Git root; non-Git execution roots remain
  # supported with explicit nil HEAD/tree values.
  def self.capture_execution_identity(repository_path:, worktree_path:, task_id: nil, execution_id: nil,
                                      lock_root: nil)
    storage_root, lock_repository = StableLockAnchor.resolve_root_pair(repository_path, lock_root: lock_root)
    worktree = File.realpath(worktree_path)
    launcher = File.realpath(__FILE__)
    git_value = lambda do |*arguments|
      stdout, _stderr, status = Open3.capture3('git', '-C', worktree, *arguments, err: File::NULL)
      status.success? ? stdout.strip : nil
    rescue SystemCallError
      nil
    end
    git_root = git_value.call('rev-parse', '--show-toplevel')
    begin
      git_root = File.realpath(git_root) if git_root
    rescue SystemCallError
      git_root = nil
    end
    bound_git = git_root == worktree

    identity = {
      'repository_path' => lock_repository,
      'worktree_path' => worktree,
      'head' => bound_git ? git_value.call('rev-parse', 'HEAD') : nil,
      'tree' => bound_git ? git_value.call('rev-parse', 'HEAD^{tree}') : nil,
      'launcher_path' => launcher,
      'launcher_sha256' => Digest::SHA256.file(launcher).hexdigest,
      'runtime_executable' => File.realpath(RbConfig.ruby),
      'runtime_version' => RUBY_DESCRIPTION
    }
    unless storage_root == lock_repository
      identity['storage_root'] = storage_root
      identity['lock_root'] = lock_repository
    end
    identity['task_id'] = task_id.to_s if task_id
    identity['execution_id'] = execution_id.to_s if execution_id
    identity
  end

  # Classifies the evidence at file_path without inferring PASS/FAIL: missing,
  # unreadable, malformed, or incomplete evidence is always
  # EVIDENCE_UNKNOWN_UNVERIFIABLE rather than a guessed outcome.
  def self.classify_evidence(file_path, expected_task_id: nil, expected_execution_id: nil, is_fallback: false)
    return EVIDENCE_UNKNOWN_UNVERIFIABLE if file_path.nil? || File.symlink?(file_path) || !File.file?(file_path)

    begin
      capture = load(file_path)
    rescue ValidationError, LoadError
      return EVIDENCE_UNKNOWN_UNVERIFIABLE
    end

    return EVIDENCE_UNKNOWN_UNVERIFIABLE unless capture.complete?

    if expected_task_id && capture.task_id && capture.task_id != expected_task_id.to_s
      return EVIDENCE_UNKNOWN_UNVERIFIABLE
    end

    if expected_execution_id && capture.execution_id && capture.execution_id != expected_execution_id.to_s
      return EVIDENCE_UNKNOWN_UNVERIFIABLE
    end

    # An explicit durable_capture_path is not weaker identity evidence than
    # the canonical fallback. When the owning record supplies both expected
    # IDs, require the same binding and execution identity for either path.
    if is_fallback || (expected_task_id && expected_execution_id)
      return EVIDENCE_UNKNOWN_UNVERIFIABLE if capture.execution_identity.nil?
      return EVIDENCE_UNKNOWN_UNVERIFIABLE if capture.task_id.nil? || capture.execution_id.nil?
      return EVIDENCE_UNKNOWN_UNVERIFIABLE unless capture.task_id == expected_task_id.to_s &&
                                                  capture.execution_id == expected_execution_id.to_s
    end

    EVIDENCE_COMPLETE
  end

  # Runs command (an argv array, never a shell string) to completion and
  # persists the exact terminal evidence to file_path before returning, so
  # the durable record exists before any caller can rely on it for a
  # verdict. Never reads or persists ENV; stdout/stderr are captured exactly
  # as the command produced them. When a block is given, signal handling stays
  # installed until the block finishes its durable finalization and output
  # work; the block must not propagate a signal itself. child_env only sets or
  # unsets the named variables for the child; it is never persisted.
  def self.run_and_capture(command, file_path:, chdir: nil, before_spawn: nil, inherited_lock: nil,
                           inherited_locks: nil, child_env: nil, execution_identity: nil,
                           task_id: nil, execution_id: nil)
    command = Array(command).map(&:to_s)
    raise ArgumentError, 'command must be a non-empty argv array' if command.empty?

    started_at = Time.now.utc.iso8601
    pending_signal = nil
    forwarding_error = nil
    upstream_pgid = nil
    signal_forwarded = false
    previous_handlers = {}
    installed_signals = []
    signal_names = %w[INT TERM]
    forward_signal = lambda do |signal|
      pgid = upstream_pgid
      next unless pgid
      next if signal_forwarded

      begin
        Process.kill(signal, -pgid)
        signal_forwarded = true
      rescue Errno::ESRCH
        # The child group may have been reaped between the trap and forwarding.
        signal_forwarded = true
      rescue StandardError => e
        # Signal traps must not raise into pipe reads or finalization. Surface a
        # forwarding failure after the child has been waited and drained.
        forwarding_error ||= e
      end
    end

    begin
      signal_names.each do |name|
        previous_handlers[name] = Signal.trap(name) do
          signal = Signal.list.fetch(name)
          pending_signal ||= signal
          forward_signal.call(signal)
        end
        installed_signals << name
      end

      spawn_opts = { pgroup: true, close_others: true }
      spawn_opts[:chdir] = chdir if chdir
      locks_to_inherit = Array(inherited_locks)
      locks_to_inherit << inherited_lock if inherited_lock
      locks_to_inherit = locks_to_inherit.uniq
      if locks_to_inherit.length > 2
        raise ArgumentError, 'at most the task and heavy-scientific lock descriptors may be inherited'
      end
      locks_to_inherit.each_with_index do |lock, index|
        unless lock.respond_to?(:fileno) && !lock.closed?
          raise ArgumentError, 'inherited locks must be open file descriptors'
        end
        child_fd = index.zero? ? INHERITED_TASK_LOCK_CHILD_FD : INHERITED_HEAVY_SCIENTIFIC_LEASE_CHILD_FD
        spawn_opts[child_fd] = lock.fileno
      end
      before_spawn.call if before_spawn
      candidate_identity = execution_identity.respond_to?(:call) ? execution_identity.call : execution_identity
      # The executable/argv0 pair also prevents Ruby's single-string shell
      # fallback when the upstream argv contains only an executable name.
      # Pass spawn options as a positional hash because the inherited task-lock
      # descriptor uses an integer key on Ruby versions that reject it in **.
      spawn_env = child_env ? [child_env] : []
      Open3.popen3(*spawn_env, [command.first, command.first], *command.drop(1), spawn_opts) do |stdin, stdout, stderr, wait_thr|
        upstream_pgid = wait_thr.pid
        stdin.close
        forward_signal.call(pending_signal) if pending_signal

        stdout_reader = Thread.new { stdout.read }
        stderr_reader = Thread.new { stderr.read }
        status = wait_thr.value
        terminal_at = Time.now.utc.iso8601
        stdout_str = stdout_reader.value
        stderr_str = stderr_reader.value
        # wait_thr.value has reaped the direct child and both readers have
        # drained their pipes. The wrapper is no longer allowed to target this
        # invocation while it commits the durable evidence.
        upstream_pgid = nil

        raise forwarding_error if forwarding_error

        capture = new(
          command: command,
          stdout: stdout_str,
          stderr: stderr_str,
          exit_status: status.signaled? ? "SIGNALED:#{status.termsig}" : status.exitstatus,
          started_at: started_at,
          ended_at: terminal_at,
          execution_identity: candidate_identity,
          task_id: task_id,
          execution_id: execution_id
        )
        capture.save(file_path)
        yield capture if block_given?
        capture.wrapper_signal = pending_signal
        capture
      end
    rescue StandardError
      if upstream_pgid
        begin
          Process.kill('TERM', -upstream_pgid)
        rescue Errno::ESRCH
          # The child group already exited while the failure was being handled.
        end
      end
      raise
    ensure
      upstream_pgid = nil
      installed_signals.reverse_each do |name|
        Signal.trap(name, previous_handlers[name])
      end
    end
  end


  class ValidationError < StandardError; end
  class LoadError < StandardError; end
end

# A single host-wide slot across protected heavy scientific --run executions.
# The lock file is stable; ownership metadata is atomically replaced under its
# flock and is deliberately limited to the launch identity needed for stale
# owner recovery.
class HeavyScientificLease
  DEFAULT_LOCK_PATH = File.join('/tmp', "fable-heavy-scientific-slot-#{Process.uid}.lock").freeze
  ENV_FD = 'FABLE_HEAVY_SCIENTIFIC_LEASE_FD'
  ENV_IDENTITY = 'FABLE_HEAVY_SCIENTIFIC_LEASE_IDENTITY'
  ENV_TASK_ID = 'FABLE_HEAVY_SCIENTIFIC_LEASE_TASK_ID'
  ENV_EXECUTION_ID = 'FABLE_HEAVY_SCIENTIFIC_LEASE_EXECUTION_ID'
  ENV_RECORD_ROOT = 'FABLE_HEAVY_SCIENTIFIC_LEASE_RECORD_ROOT'
  ENV_WORKTREE = 'FABLE_HEAVY_SCIENTIFIC_LEASE_WORKTREE'
  ENV_RUN_ROOT = 'FABLE_HEAVY_SCIENTIFIC_LEASE_RUN_ROOT'
  ENV_KEYS = [ENV_FD, ENV_IDENTITY, ENV_TASK_ID, ENV_EXECUTION_ID,
              ENV_RECORD_ROOT, ENV_WORKTREE, ENV_RUN_ROOT].freeze
  METADATA_KEYS = %w[TASK_ID EXECUTION_ID PID PROCESS_START_IDENTITY WORKTREE RUN_ROOT ACQUIRED_AT].freeze

  class Error < StandardError; end
  class SlotOwned < Error; end
  class LegacyWriterActive < Error
    attr_reader :writers

    def initialize(writers)
      @writers = writers
      super('STOP_ONCE: HEAVY_SCIENTIFIC_WRITER_ACTIVE')
    end
  end
  class OwnershipUnresolved < Error; end

  attr_reader :lock, :metadata, :lock_path

  def self.enabled?(env = ENV)
    env['FABLE_HEAVY_SCIENTIFIC_LEASE'] == 'on'
  end

  def self.heavy_command?(task_id, command)
    return true if task_id.to_s.match?(/B649|SCIENTIFIC|WBC/i)

    Array(command).any? do |argument|
      value = argument.to_s.downcase
      value.include?('/.task-data/') || value.include?('b649_k') || value.include?('b649-k') ||
        %w[tbr_engine ldr_engine exact_score.py].include?(File.basename(value)) ||
        value.include?('lottolab.research')
    end
  end

  def self.run_root_from_command(command, worktree)
    argv = Array(command).map(&:to_s)
    argv.each_with_index do |argument, index|
      value = if %w[--run-root --run_root].include?(argument)
                argv[index + 1]
              elsif argument.start_with?('--run-root=')
                argument.split('=', 2).last
              elsif argument.start_with?('--run_root=')
                argument.split('=', 2).last
              end
      return File.expand_path(value) if value && Pathname.new(value).absolute?
    end
    File.realpath(worktree)
  rescue SystemCallError => e
    raise OwnershipUnresolved, "protected worktree could not be resolved for the heavy lease: #{e.message}"
  end

  def self.clear_capability_env
    ENV_KEYS.map { |key| [key, nil] }.to_h
  end

  def self.nested_capability_request(env = ENV)
    values = ENV_KEYS.map { |key| env[key] }
    return nil if values.all?(&:nil?)
    unless values.all? { |value| value.is_a?(String) && !value.empty? }
      raise OwnershipUnresolved, 'nested heavy-scientific lease capability is incomplete'
    end

    ENV_KEYS.zip(values).to_h
  end

  def self.process_start_identity(pid)
    output, status = Open3.capture2('/bin/ps', '-ww', '-p', Integer(pid).to_s, '-o', 'lstart=')
    return nil unless status.success?

    identities = output.lines.map(&:strip).reject(&:empty?)
    identities.length == 1 ? identities.first : nil
  rescue SystemCallError, ArgumentError
    nil
  end

  # Recognize the controller's complete argv, including its task-owned repository,
  # role, source identity and child command. A runtime name or low CPU alone is
  # insufficient; unknown options or workloads deliberately retain the writer gate.
  def self.local_runtime_child_argv(argv)
    return nil unless File.basename(argv.first.to_s).match?(/\Apython(?:\d+(?:\.\d+)*)?\z/i)
    return nil unless argv[1, 3] == %w[-m lottolab.infrastructure.local_runtime _launch]
    return nil unless [4, 6, 8, 10, 12, 14].map { |i| argv[i] } ==
                      %w[--role --token --repository --source-commit --cwd --]

    role, token, repository, source, cwd = [5, 7, 9, 11, 13].map { |i| argv[i] }
    return nil unless %w[backend frontend].include?(role) && token.to_s.match?(/\A[0-9a-f]{32}\z/)
    return nil unless source.to_s.match?(/\A(?:[0-9a-f]{40}|[0-9a-f]{64})\z/)
    return nil unless repository.to_s.start_with?('/') && Pathname.new(repository).cleanpath.to_s == repository
    return nil unless repository.match?(%r{/\.task-data/[A-Za-z0-9_-]+/repo\z})
    return nil unless cwd == (role == 'backend' ? repository : "#{repository}/frontend")

    child = argv[15..-1]
    return nil unless child && child.first.to_s.start_with?('/')

    expected = if role == 'backend' && File.basename(child.first) == 'uv'
                 [child.first, 'run', '--no-sync', 'uvicorn', '--factory',
                  'lottolab.interfaces.api.local_app:create_local_app', '--app-dir', "#{repository}/src",
                  '--host', '127.0.0.1', '--port', '8000', '--header', "X-LottoLab-Owner:#{token}",
                  '--no-access-log', '--log-level', 'warning']
               elsif role == 'frontend' && File.basename(child.first) == 'node'
                 [child.first, "#{repository}/frontend/node_modules/vite/bin/vite.js",
                  '--host', '127.0.0.1', '--port', '5173', '--strictPort',
                  '--config', "#{repository}/frontend/vite.config.ts"]
               end
    child == expected ? child : nil
  end

  def self.low_load_local_runtime_pids(rows)
    # Duplicate identities or unparseable argv cannot establish a runtime role.
    return [] unless rows.map(&:first).uniq.length == rows.length

    parsed = rows.each_with_object({}) do |row, memo|
      pid, parent, cpu, _memory, state, command = row
      next unless pid.positive? && parent >= 0 && cpu.finite? && cpu >= 0.0 && cpu < 5.0
      next if state.start_with?('Z')

      begin
        memo[pid] = [parent, Shellwords.split(command)]
      rescue ArgumentError
        next
      end
    end
    launchers = parsed.each_with_object({}) do |(pid, (_parent, argv)), memo|
      child = local_runtime_child_argv(argv)
      memo[pid] = child if child
    end
    runtime_pids = launchers.keys
    parsed.each do |pid, (parent, argv)|
      next unless File.basename(argv.first.to_s).match?(/\Apython(?:\d+(?:\.\d+)*)?\z/i)

      child = launchers[parent]
      if !child && parsed[parent]
        bridge_parent, bridge_argv = parsed[parent]
        candidate = launchers[bridge_parent]
        child = candidate if candidate && bridge_argv == candidate
      end
      next unless child && File.basename(child.first) == 'uv'

      # Uvicorn must belong to that backend launcher, with exactly the same
      # app, repository, loopback endpoint and ownership token, through at most
      # its declared uv child. Descendant Python workers receive no exemption.
      repository = child[7].delete_suffix('/src')
      if argv[1] == "#{repository}/.venv/bin/uvicorn" && argv[2..-1] == child[4..-1]
        runtime_pids << pid
      end
    end
    runtime_pids
  end

  def self.legacy_heavy_writers_once
    output, status = Open3.capture2('/bin/ps', '-ww', '-axo', 'pid=,ppid=,%cpu=,%mem=,state=,command=')
    raise OwnershipUnresolved, 'legacy heavy-writer snapshot could not be read safely' unless status.success?

    rows = output.lines.map do |line|
      fields = line.strip.split(/\s+/, 6)
      raise OwnershipUnresolved, 'legacy heavy-writer snapshot was unparseable' unless fields.length == 6

      pid, parent, cpu, memory, state, command = fields
      begin
        [Integer(pid), Integer(parent), Float(cpu), Float(memory), state, command]
      rescue ArgumentError
        raise OwnershipUnresolved, 'legacy heavy-writer snapshot contained invalid process identity'
      end
    end
    parents = rows.each_with_object({}) { |row, memo| memo[row[0]] = row[1] }
    ancestors = []
    current = Process.pid
    until current.zero? || ancestors.include?(current)
      ancestors << current
      current = parents.fetch(current, 0)
    end
    runtime_pids = low_load_local_runtime_pids(rows)

    rows.each_with_object([]) do |(pid, _parent, cpu, memory, state, command), writers|
      next if ancestors.include?(pid) || state.start_with?('Z')

      begin
        argv = Shellwords.split(command)
      rescue ArgumentError
        argv = command.split
      end
      next if argv.empty?

      executable = File.basename(argv.first).downcase
      scientific = command.include?('/.task-data/') ||
                   argv.any? { |arg| arg.downcase.include?('b649_k') || arg.downcase.include?('b649-k') } ||
                   argv.any? { |arg| %w[tbr_engine ldr_engine exact_score.py].include?(File.basename(arg)) } ||
                   command.include?('lottolab.research')
      computational = executable.include?('python') || %w[tbr_engine ldr_engine].include?(executable) ||
                      (scientific && cpu >= 5.0 &&
                       !%w[ruby zsh bash sh ps tail cat].include?(executable))
      next unless scientific && computational
      next if runtime_pids.include?(pid)

      evidence = { 'PID' => pid, 'COMMAND' => command, 'CPU_PERCENT' => cpu, 'MEMORY_PERCENT' => memory }
      argv.each do |argument|
        next unless argument.start_with?('/') && argument.include?('/.task-data/')

        worktree, suffix = argument.split('/.task-data/', 2)
        task_id = suffix.to_s.split('/', 2).first
        if task_id && task_id.match?(/\A[A-Za-z0-9_-]+\z/)
          evidence['TASK_ID'] = task_id
          evidence['WORKTREE'] = worktree
          break
        end
      end
      writers << evidence
    end
  rescue SystemCallError => e
    raise OwnershipUnresolved, "legacy heavy-writer snapshot failed: #{e.message}"
  end

  def self.default_metadata_path(lock_path = DEFAULT_LOCK_PATH)
    "#{lock_path}.owner.json"
  end

  def initialize(task_id:, execution_id:, worktree:, run_root:, record_root: nil, lock_path: DEFAULT_LOCK_PATH,
                 legacy_writer_checker: nil)
    @task_id = task_id.to_s
    @execution_id = execution_id.to_s
    @worktree = File.realpath(worktree)
    @record_root = File.realpath(record_root || worktree)
    @run_root = File.expand_path(run_root.to_s)
    @lock_path = File.expand_path(lock_path.to_s)
    @metadata_path = self.class.default_metadata_path(@lock_path)
    @legacy_writer_checker = legacy_writer_checker || self.class.method(:legacy_heavy_writers_once)
    @lock = nil
    @metadata = nil
    @owned = false
  rescue SystemCallError => e
    raise OwnershipUnresolved, "heavy lease identity could not be resolved: #{e.message}"
  end

  def self.acquire!(**kwargs)
    new(**kwargs).tap(&:acquire!)
  end

  def owned?
    @owned
  end

  def acquire!
    FileUtils.mkdir_p(File.dirname(@lock_path))
    flags = File::RDWR | File::CREAT
    flags |= File::NOFOLLOW if File.const_defined?(:NOFOLLOW)
    @lock = File.open(@lock_path, flags, 0o600)
    validate_lock_file!
    unless @lock.flock(File::LOCK_EX | File::LOCK_NB)
      raise SlotOwned, 'STOPPED_HEAVY_SLOT_OWNED'
    end

    recover_stale_metadata!
    writers = @legacy_writer_checker.call
    raise LegacyWriterActive, writers unless Array(writers).empty?

    start_identity = self.class.process_start_identity(Process.pid)
    raise OwnershipUnresolved, 'current protected launcher process identity is unavailable' unless start_identity

    @metadata = {
      'TASK_ID' => @task_id,
      'EXECUTION_ID' => @execution_id,
      'PID' => Process.pid,
      'PROCESS_START_IDENTITY' => start_identity,
      'WORKTREE' => @worktree,
      'RUN_ROOT' => @run_root,
      'ACQUIRED_AT' => Time.now.utc.iso8601
    }
    write_metadata!
    @owned = true
    self
  rescue Error
    release_lock_only
    raise
  rescue SystemCallError, IOError, JSON::ParserError => e
    release_lock_only
    raise OwnershipUnresolved, "heavy-scientific lease could not be established safely: #{e.message}"
  end

  def capability_env(child_fd: @lock.fileno)
    raise OwnershipUnresolved, 'heavy lease is not held' unless @lock && !@lock.closed? && @metadata
    stat = @lock.stat
    {
      ENV_FD => Integer(child_fd).to_s,
      ENV_IDENTITY => "#{stat.dev}:#{stat.ino}",
      ENV_TASK_ID => @metadata.fetch('TASK_ID'),
      ENV_EXECUTION_ID => @metadata.fetch('EXECUTION_ID'),
      ENV_RECORD_ROOT => @record_root,
      ENV_WORKTREE => @metadata.fetch('WORKTREE'),
      ENV_RUN_ROOT => @metadata.fetch('RUN_ROOT')
    }
  end

  def self.verify_nested!(request:, repo_root:, task_id:, execution_id:, ancestor_execution_ids:,
                          lock_path: DEFAULT_LOCK_PATH)
    fd_text = request[ENV_FD].to_s
    identity = request[ENV_IDENTITY].to_s
    owner_task = request[ENV_TASK_ID].to_s
    owner_execution = request[ENV_EXECUTION_ID].to_s
    owner_root = request[ENV_RECORD_ROOT].to_s
    unless fd_text.match?(/\A[1-9]\d{0,8}\z/) && fd_text.to_i >= 3 && identity.match?(/\A\d+:\d+\z/) &&
           ExecutionRecord.stable_component?(owner_task) && ExecutionRecord.stable_component?(owner_execution) &&
           Pathname.new(owner_root).absolute? && owner_task == task_id.to_s &&
           owner_execution != execution_id.to_s && File.realpath(owner_root) == File.realpath(repo_root) &&
           Array(ancestor_execution_ids).include?(owner_execution) &&
           Pathname.new(request[ENV_WORKTREE].to_s).absolute? && Pathname.new(request[ENV_RUN_ROOT].to_s).absolute?
      raise OwnershipUnresolved, 'nested heavy-scientific lease identity is out of scope or incomplete'
    end

    resolved_lock_path = File.expand_path(lock_path)
    inherited = File.for_fd(Integer(fd_text), autoclose: false)
    inherited_stat = inherited.stat
    path_stat = File.lstat(resolved_lock_path)
    unless inherited_stat.file? && inherited_stat.uid == Process.uid && path_stat.file? &&
           [inherited_stat.dev, inherited_stat.ino] == identity.split(':').map(&:to_i) &&
           [path_stat.dev, path_stat.ino] == [inherited_stat.dev, inherited_stat.ino]
      raise OwnershipUnresolved, 'nested heavy-scientific lease descriptor identity does not match'
    end
    unless inherited.flock(File::LOCK_EX | File::LOCK_NB)
      raise SlotOwned, 'STOPPED_HEAVY_SLOT_OWNED'
    end

    metadata_path = default_metadata_path(resolved_lock_path)
    metadata = read_metadata_file!(metadata_path)
    unless metadata && metadata['TASK_ID'] == owner_task && metadata['EXECUTION_ID'] == owner_execution &&
           metadata['WORKTREE'] == request[ENV_WORKTREE] && metadata['RUN_ROOT'] == request[ENV_RUN_ROOT]
      raise OwnershipUnresolved, 'nested heavy-scientific lease metadata does not match its capability'
    end

    File.open(resolved_lock_path, File::RDWR) do |probe|
      unless [probe.stat.dev, probe.stat.ino] == [inherited_stat.dev, inherited_stat.ino]
        raise OwnershipUnresolved, 'nested heavy-scientific lock path changed during verification'
      end
      if probe.flock(File::LOCK_EX | File::LOCK_NB)
        probe.flock(File::LOCK_UN)
        raise OwnershipUnresolved, 'nested heavy-scientific lease is not held by its protected parent'
      end
    end
    lease = allocate
    lease.instance_variable_set(:@task_id, owner_task)
    lease.instance_variable_set(:@execution_id, owner_execution)
    lease.instance_variable_set(:@worktree, owner_root)
    lease.instance_variable_set(:@record_root, File.realpath(repo_root))
    lease.instance_variable_set(:@run_root, request[ENV_RUN_ROOT])
    lease.instance_variable_set(:@lock_path, resolved_lock_path)
    lease.instance_variable_set(:@metadata_path, metadata_path)
    lease.instance_variable_set(:@lock, inherited)
    lease.instance_variable_set(:@metadata, metadata)
    lease.instance_variable_set(:@owned, false)
    lease
  rescue SystemCallError, ArgumentError => e
    raise OwnershipUnresolved, "nested heavy-scientific lease could not be verified: #{e.message}"
  end

  def release!
    return unless @lock

    if @owned
      begin
        current = self.class.read_metadata_file!(@metadata_path)
        File.unlink(@metadata_path) if current == @metadata
      rescue Error, SystemCallError, JSON::ParserError
        # Preserve any ambiguous metadata. The next owner must prove it stale.
      ensure
        release_lock_only
      end
    else
      @lock = nil # The inherited descriptor belongs to the protected parent.
    end
    nil
  end

  def self.read_metadata_file!(path)
    return nil unless File.exist?(path) || File.symlink?(path)
    raise OwnershipUnresolved, 'heavy lease metadata path is a symlink' if File.symlink?(path)

    stat = File.lstat(path)
    unless stat.file? && stat.uid == Process.uid && (stat.mode & 0o077).zero?
      raise OwnershipUnresolved, 'heavy lease metadata file is not a private regular file'
    end
    data = JSON.parse(File.binread(path))
    unless data.is_a?(Hash) && data.keys.sort == METADATA_KEYS.sort &&
           data['TASK_ID'].is_a?(String) && !data['TASK_ID'].empty? &&
           data['EXECUTION_ID'].is_a?(String) && !data['EXECUTION_ID'].empty? &&
           data['PID'].is_a?(Integer) && data['PID'].positive? &&
           data['PROCESS_START_IDENTITY'].is_a?(String) && !data['PROCESS_START_IDENTITY'].empty? &&
           data['WORKTREE'].is_a?(String) && Pathname.new(data['WORKTREE']).absolute? &&
           data['RUN_ROOT'].is_a?(String) && Pathname.new(data['RUN_ROOT']).absolute? &&
           data['ACQUIRED_AT'].is_a?(String) && !data['ACQUIRED_AT'].empty?
      raise OwnershipUnresolved, 'heavy lease metadata is incomplete or ambiguous'
    end
    data
  rescue JSON::ParserError => e
    raise OwnershipUnresolved, "heavy lease metadata is malformed: #{e.message}"
  end

  private

  def validate_lock_file!
    stat = @lock.stat
    path_stat = File.lstat(@lock_path)
    unless stat.file? && stat.uid == Process.uid && (stat.mode & 0o077).zero? &&
           path_stat.file? && [stat.dev, stat.ino] == [path_stat.dev, path_stat.ino]
      raise OwnershipUnresolved, 'heavy lease lock is not a private regular file'
    end
  end

  def recover_stale_metadata!
    existing = self.class.read_metadata_file!(@metadata_path)
    return unless existing

    state = owner_liveness(existing)
    case state
    when :dead
      File.unlink(@metadata_path)
    when :alive
      raise SlotOwned, 'STOPPED_HEAVY_SLOT_OWNED'
    else
      raise OwnershipUnresolved, 'recorded heavy lease owner identity could not be established safely'
    end
  end

  def owner_liveness(metadata)
    Process.kill(0, metadata.fetch('PID'))
    current_start = self.class.process_start_identity(metadata.fetch('PID'))
    return :unknown unless current_start

    current_start == metadata.fetch('PROCESS_START_IDENTITY') ? :alive : :dead
  rescue Errno::ESRCH
    :dead
  rescue Errno::EPERM
    :unknown
  end

  def write_metadata!
    current = self.class.read_metadata_file!(@metadata_path)
    raise OwnershipUnresolved, 'heavy lease metadata changed before atomic update' if current && current != @metadata

    directory = File.dirname(@metadata_path)
    temp_path = File.join(directory, ".heavy-scientific-lease-#{Process.pid}-#{SecureRandom.hex(8)}.tmp")
    File.open(temp_path, File::WRONLY | File::CREAT | File::EXCL, 0o600) do |file|
      file.write(JSON.generate(@metadata))
      file.flush
      file.fsync
    end
    File.rename(temp_path, @metadata_path)
  ensure
    File.unlink(temp_path) if temp_path && File.file?(temp_path)
  end

  def release_lock_only
    return unless @lock

    @lock.close unless @lock.closed?
    @lock = nil
    @owned = false
  end
end

# ExecutionRecord classifies a task-owned long-running execution that may
# have outlived its originating session, so a resuming Worker can decide
# whether to reuse a completed result, avoid launching a duplicate, or fail
# closed rather than guess. It never scans the OS process table; it only
# inspects the exact PID this task itself recorded.
class ExecutionRecord
  SCHEMA_VERSION = 1

  STATUS_STARTED = 'STARTED'
  STATUS_COMPLETED = 'COMPLETED'

  CLASSIFICATION_ACTIVE = 'PRIOR_PROCESS_ACTIVE'
  CLASSIFICATION_COMPLETED = 'PRIOR_PROCESS_COMPLETED'
  CLASSIFICATION_TERMINATED_INCOMPLETE = 'PRIOR_PROCESS_TERMINATED_INCOMPLETE'
  CLASSIFICATION_STATE_UNRESOLVED = 'PRIOR_PROCESS_STATE_UNRESOLVED'

  # A nested protected run may launch only from a descendant that still holds
  # the inherited task-lock descriptor. These variables name it; the lineage
  # secret exists only in the lineage's environment, never on disk.
  NESTED_TASK_ID_ENV = 'FABLE_PROTECTED_RUN_TASK_ID'
  NESTED_RECORD_ROOT_ENV = 'FABLE_PROTECTED_RUN_RECORD_ROOT'
  NESTED_LOCK_ROOT_ENV = 'FABLE_PROTECTED_RUN_LOCK_ROOT'
  NESTED_LOCK_FD_ENV = 'FABLE_PROTECTED_RUN_LOCK_FD'
  NESTED_LOCK_IDENTITY_ENV = 'FABLE_PROTECTED_RUN_LOCK_IDENTITY'
  NESTED_PARENT_ENV = 'FABLE_PROTECTED_RUN_PARENT_EXECUTION_ID'
  NESTED_SECRET_ENV = 'FABLE_PROTECTED_RUN_LINEAGE_SECRET'
  NESTED_ENV_KEYS = [NESTED_TASK_ID_ENV, NESTED_RECORD_ROOT_ENV, NESTED_LOCK_ROOT_ENV, NESTED_LOCK_FD_ENV,
                     NESTED_LOCK_IDENTITY_ENV, NESTED_PARENT_ENV, NESTED_SECRET_ENV].freeze
  NESTED_MAX_DEPTH = 16
  TASK_LINEAGE_MAX_BYTES = 4096

  NestedCapability = Struct.new(:lock, :parent_execution_id, :ancestor_execution_ids, :secret,
                                keyword_init: true)

  attr_accessor :schema_version, :task_id, :execution_id, :pid, :status,
                :durable_capture_path, :started_at, :ended_at, :parent_execution_id,
                :continuation_from_execution_id, :continuation_checkpoint_sha256,
                :continuation_transition_sha256, :recovery_old_execution_id,
                :recovery_parent_execution_id, :heavy_scientific, :heavy_run_root, :record_path

  def initialize(attrs = {})
    @schema_version = attrs[:schema_version] || attrs['schema_version'] || SCHEMA_VERSION
    @task_id = (attrs[:task_id] || attrs['task_id'])&.to_s
    @execution_id = (attrs[:execution_id] || attrs['execution_id'])&.to_s
    @pid = attrs.key?(:pid) ? attrs[:pid] : attrs['pid']
    @status = (attrs[:status] || attrs['status'])&.to_s
    @durable_capture_path = (attrs[:durable_capture_path] || attrs['durable_capture_path'])&.to_s
    @started_at = (attrs[:started_at] || attrs['started_at'])&.to_s
    @ended_at = (attrs[:ended_at] || attrs['ended_at'])&.to_s
    @parent_execution_id = (attrs[:parent_execution_id] || attrs['parent_execution_id'])&.to_s
    @continuation_from_execution_id =
      (attrs[:continuation_from_execution_id] || attrs['continuation_from_execution_id'])&.to_s
    @continuation_checkpoint_sha256 =
      (attrs[:continuation_checkpoint_sha256] || attrs['continuation_checkpoint_sha256'])&.to_s
    @continuation_transition_sha256 =
      (attrs[:continuation_transition_sha256] || attrs['continuation_transition_sha256'])&.to_s
    @recovery_old_execution_id = (attrs[:recovery_old_execution_id] || attrs['recovery_old_execution_id'])&.to_s
    @recovery_parent_execution_id = (attrs[:recovery_parent_execution_id] || attrs['recovery_parent_execution_id'])&.to_s
    @heavy_scientific = if attrs.key?(:heavy_scientific)
                          attrs[:heavy_scientific]
                        else
                          attrs['heavy_scientific']
                        end
    @heavy_run_root = if attrs.key?(:heavy_run_root)
                        attrs[:heavy_run_root]&.to_s
                      else
                        attrs['heavy_run_root']&.to_s
                      end
    @record_path = (attrs[:record_path] || attrs['record_path'])&.to_s
  end

  def to_h
    data = {
      'schema_version' => @schema_version,
      'task_id' => @task_id,
      'execution_id' => @execution_id,
      'pid' => @pid,
      'status' => @status,
      'durable_capture_path' => @durable_capture_path,
      'started_at' => @started_at,
      'ended_at' => @ended_at
    }
    # Optional identity and lineage fields preserve the original bytes for ordinary records.
    data['parent_execution_id'] = @parent_execution_id unless @parent_execution_id.nil?
    unless @continuation_from_execution_id.nil?
      data['continuation_from_execution_id'] = @continuation_from_execution_id
      data['continuation_checkpoint_sha256'] = @continuation_checkpoint_sha256
      data['continuation_transition_sha256'] = @continuation_transition_sha256
    end
    data['recovery_old_execution_id'] = @recovery_old_execution_id unless @recovery_old_execution_id.nil?
    data['recovery_parent_execution_id'] = @recovery_parent_execution_id unless @recovery_parent_execution_id.nil?
    data['heavy_scientific'] = @heavy_scientific unless @heavy_scientific.nil?
    data['heavy_run_root'] = @heavy_run_root unless @heavy_run_root.nil?
    data
  end

  def to_json(*args)
    JSON.pretty_generate(to_h, *args)
  end

  def self.from_json(json_str)
    data = JSON.parse(json_str)
    raise ValidationError, 'execution record JSON root must be an Object' unless data.is_a?(Hash)

    new(data)
  rescue JSON::ParserError => e
    raise ValidationError, "Malformed execution record JSON: #{e.message}"
  end

  def self.load(file_path)
    raise LoadError, "Execution record file does not exist: #{file_path}" unless File.file?(file_path)

    record = from_json(File.read(file_path, encoding: 'UTF-8'))
    record.record_path = File.expand_path(file_path)
    record
  end

  def save(file_path)
    @record_path = File.expand_path(file_path)
    dir = File.dirname(file_path)
    FileUtils.mkdir_p(dir)
    Tempfile.create(['.execution-record-', '.tmp'], dir) do |file|
      file.write(to_json)
      file.flush
      file.fsync
      File.rename(file.path, file_path)
      self.class.sync_directory!(dir)
    end
    true
  end

  # Persists this record to file_path only if no file exists there yet, using
  # create-temp-then-hardlink so file_path never becomes visible in a
  # partially written state. Raises Errno::EEXIST when file_path already
  # exists; the caller (see .acquire!) decides how to proceed rather than
  # silently overwriting a record another contender may have just won.
  def save_if_absent!(file_path)
    @record_path = File.expand_path(file_path)
    dir = File.dirname(file_path)
    FileUtils.mkdir_p(dir)
    Tempfile.create(['.execution-record-', '.tmp'], dir) do |file|
      file.write(to_json)
      file.flush
      file.fsync
      File.link(file.path, file_path)
      self.class.sync_directory!(dir)
    end
    true
  end

  def self.sync_directory!(directory)
    File.open(directory, File::RDONLY) { |dir| dir.fsync }
  end

  def self.default_path(repo_root, task_id, execution_id)
    File.join(repo_root, '.fable', 'checkpoints', task_id.to_s, 'executions', "#{execution_id}.json")
  end

  def self.verify_heavy_authority_metadata!(record)
    unless record.heavy_scientific.nil? || record.heavy_scientific == true
      raise UnresolvedExecutionStateError, 'execution heavy-scientific authority is malformed'
    end
    if record.heavy_scientific
      root = record.heavy_run_root
      unless root && Pathname.new(root).absolute? && !root.include?("\0") && File.expand_path(root) == root
        raise UnresolvedExecutionStateError, 'execution heavy-scientific run root is missing or ambiguous'
      end
    elsif record.heavy_run_root
      raise UnresolvedExecutionStateError, 'execution heavy-scientific run root has no protected authority'
    end
    true
  rescue ArgumentError, TypeError
    raise UnresolvedExecutionStateError, 'execution heavy-scientific run root is malformed'
  end

  def self.task_lock_path(repo_root, task_id, lock_root: nil)
    StableLockAnchor.lock_path(repo_root, 'task-execution', task_id.to_s,
                               filename: 'execution.lock', lock_root: lock_root)
  end

  def self.legacy_task_lock_path(repo_root, task_id)
    File.join(repo_root, '.fable', 'checkpoints', task_id.to_s, 'execution.lock')
  end

  def self.assert_task_lock_generation!(repo_root, task_id, lock_root: nil)
    legacy_state_dir = File.join(repo_root, '.fable', 'checkpoints', task_id.to_s)
    StableLockAnchor.assert_generation_available!(
      repo_root, 'task-execution', task_id.to_s,
      legacy_paths: [legacy_task_lock_path(repo_root, task_id)],
      legacy_state_paths: [legacy_state_dir], lock_root: lock_root
    )
  end

  # Serializes task-scoped ownership checks and acquisition across distinct
  # execution IDs. Protected launches also pass this open descriptor to the
  # application child, so the OS lock remains held if the wrapper is killed.
  def self.with_task_lock(repo_root, task_id, lock_root: nil)
    assert_task_lock_generation!(repo_root, task_id, lock_root: lock_root)
    lock = StableLockAnchor.open_lock(
      repo_root, 'task-execution', task_id.to_s, filename: 'execution.lock',
      legacy_paths: [legacy_task_lock_path(repo_root, task_id)], lock_root: lock_root
    )
    begin
      unless lock.flock(File::LOCK_EX | File::LOCK_NB)
        raise DuplicateExecutionError, "task '#{task_id}' has another execution claim in progress"
      end
      reset_task_lineage!(lock, repo_root, task_id, lock_root: lock_root)

      yield lock
    ensure
      lock.close unless lock.closed?
    end
  end

  def self.reset_task_lineage!(lock, repo_root, task_id, lock_root: nil)
    lock.truncate(0)
    record_root = File.realpath(repo_root)
    lineage = { 'schema_version' => SCHEMA_VERSION, 'lock_generation' => StableLockAnchor::GENERATION,
                'task_id' => task_id.to_s, 'record_root' => record_root }
    resolved_lock_root = File.realpath(lock_root || repo_root)
    lineage['lock_root'] = resolved_lock_root unless resolved_lock_root == record_root
    lock.pwrite(JSON.generate(lineage), 0)
    lock.fsync
    true
  end

  # Finds any live or unresolved record that could own this task's writer
  # scope. Completed and definitely-dead records remain historical evidence;
  # a live recovery successor is exempt only inside its verified nested lineage.
  def self.verify_no_active_owner!(repo_root, task_id, except_execution_ids: [],
                                  reject_unrecovered_stale: false, nested_lineage_execution_ids: [])
    execution_dir = File.join(repo_root, '.fable', 'checkpoints', task_id.to_s, 'executions')
    return true unless File.directory?(execution_dir)

    Dir.glob(File.join(execution_dir, '*.json')).sort.each do |path|
      execution_id = File.basename(path, '.json')
      next if Array(except_execution_ids).include?(execution_id)
      if File.symlink?(path)
        raise UnresolvedExecutionStateError, "execution record '#{path}' is a symlink"
      end

      record = begin
        load(path)
      rescue ValidationError, LoadError => e
        raise UnresolvedExecutionStateError, "execution record '#{path}' is unreadable: #{e.message}"
      end
      unless record.schema_version == SCHEMA_VERSION && record.task_id == task_id.to_s &&
             record.execution_id == execution_id
        raise UnresolvedExecutionStateError, "execution record '#{path}' has ambiguous identity"
      end

      case record.status
      when STATUS_STARTED
        unless record.pid.is_a?(Integer) && record.pid.positive?
          raise UnresolvedExecutionStateError,
                "task '#{task_id}' execution '#{record.execution_id}' has an invalid recorded pid"
        end
        classification = record.classify
        if classification == CLASSIFICATION_ACTIVE
          raise DuplicateExecutionError,
                "task '#{task_id}' is owned by execution '#{record.execution_id}' (pid=#{record.pid})"
        elsif classification == CLASSIFICATION_STATE_UNRESOLVED
          raise UnresolvedExecutionStateError,
                "task '#{task_id}' execution '#{record.execution_id}' liveness is unresolved"
        elsif reject_unrecovered_stale &&
              !recovered_stale_chain_exempt_for_writer?(
                repo_root, task_id, record,
                allowed_active_successor_ids: nested_lineage_execution_ids
              )
          raise UnresolvedExecutionStateError,
                "#{CLASSIFICATION_TERMINATED_INCOMPLETE}: task '#{task_id}' has an unrecovered stale execution " \
                "'#{record.execution_id}'; only --recover-run may continue that execution"
        end
      when STATUS_COMPLETED
        # A terminal record never owns the live writer scope.
      else
        raise UnresolvedExecutionStateError, "execution record '#{path}' has unknown status"
      end
    end
    true
  end

  # A stale record under another execution ID must not be bypassed by starting
  # the same task again with a rotated ID. A completed chain is historical;
  # while its successor is active, only that successor's verified nested
  # lineage may treat the stale ancestor as resolved for its writer check.
  def self.recovered_stale_chain_exempt_for_writer?(repo_root, task_id, record,
                                                    allowed_active_successor_ids: [])
    current = record
    visited = {}

    loop do
      current_id = current.execution_id.to_s
      if visited[current_id]
        raise UnresolvedExecutionStateError, 'stale recovery successor chain contains a cycle'
      end
      visited[current_id] = true

      current_path = default_path(repo_root, task_id, current_id)
      if File.symlink?(current_path) || !File.file?(current_path)
        raise UnresolvedExecutionStateError, "stale execution record '#{current_path}' is not a regular file"
      end
      current_bytes = File.binread(current_path)
      transition_path = ExecutionRecoveryTransition.default_path(repo_root, task_id, current_id)
      return false unless File.exist?(transition_path) || File.symlink?(transition_path)

      transition = ExecutionRecoveryTransition.load(transition_path)
      transition.assert_compatible!(
        task_id: task_id,
        old_execution_id: current_id,
        old_execution_sha256: Digest::SHA256.hexdigest(current_bytes),
        application_state_path: transition.application_state_path,
        worktree_path: transition.worktree_path,
        command_sha256: transition.successor_command_sha256
      )
      transition.verify_readback!(transition_path)

      successor_id = transition.successor_execution_id
      successor_path = default_path(repo_root, task_id, successor_id)
      if File.symlink?(successor_path) || !File.file?(successor_path)
        raise UnresolvedExecutionStateError,
              "recovery successor execution '#{successor_id}' is missing or is not a regular file"
      end
      successor = load(successor_path)
      unless successor.schema_version == SCHEMA_VERSION && successor.task_id == task_id.to_s &&
             successor.execution_id == successor_id
        raise UnresolvedExecutionStateError, "recovery successor execution '#{successor_id}' has ambiguous identity"
      end

      case successor.classify
      when CLASSIFICATION_COMPLETED
        return true
      when CLASSIFICATION_ACTIVE
        return true if Array(allowed_active_successor_ids).include?(successor_id)

        raise DuplicateExecutionError,
              "task '#{task_id}' is owned by recovery successor '#{successor_id}' (pid=#{successor.pid})"
      when CLASSIFICATION_STATE_UNRESOLVED
        raise UnresolvedExecutionStateError,
              "recovery successor execution '#{successor_id}' liveness or terminal state is unresolved"
      when CLASSIFICATION_TERMINATED_INCOMPLETE
        unless successor.status == STATUS_STARTED && successor.pid.is_a?(Integer) && successor.pid.positive? &&
               successor.started_at && !successor.started_at.empty? &&
               successor.durable_capture_path.to_s.empty? && successor.ended_at.nil?
          raise UnresolvedExecutionStateError,
                "recovery successor execution '#{successor_id}' is not an unambiguous stale STARTED record"
        end
        current = successor
      else
        raise UnresolvedExecutionStateError,
              "recovery successor execution '#{successor_id}' has an unknown classification"
      end
    end
  rescue ValidationError, LoadError => e
    raise UnresolvedExecutionStateError, "stale recovery successor chain is unreadable: #{e.message}"
  end

  def self.acquire_task_owned!(repo_root, task_id, execution_id, pid:, heavy_scientific: nil,
                               heavy_run_root: nil, command: nil, lock_root: nil)
    with_task_lock(repo_root, task_id, lock_root: lock_root) do
      verify_no_active_owner!(repo_root, task_id, except_execution_ids: [execution_id.to_s],
                              reject_unrecovered_stale: true)
      acquire!(default_path(repo_root, task_id, execution_id),
               task_id: task_id, execution_id: execution_id, pid: pid,
               heavy_scientific: heavy_scientific, heavy_run_root: heavy_run_root,
               command: command, repo_root: repo_root)
    end
  end

  # Records which top-level --run owns the held task lock and returns the new
  # lineage secret, whose SHA-256 alone is stored. Only the lock holder writes
  # it, just before launching its child; nil leaves the lineage empty.
  def self.write_task_lineage!(lock, repo_root, task_id, execution_id, lock_root: nil)
    lock.truncate(0)
    secret = nil
    unless execution_id.nil?
      secret = SecureRandom.hex(32)
      record_root = File.realpath(repo_root)
      lineage = { 'schema_version' => SCHEMA_VERSION,
                  'lock_generation' => StableLockAnchor::GENERATION, 'task_id' => task_id.to_s,
                  'record_root' => record_root,
                  'execution_id' => execution_id.to_s,
                  'capability_sha256' => Digest::SHA256.hexdigest(secret) }
      resolved_lock_root = File.realpath(lock_root || repo_root)
      lineage['lock_root'] = resolved_lock_root unless resolved_lock_root == record_root
      lock.pwrite(JSON.generate(lineage), 0)
    else
      reset_task_lineage!(lock, repo_root, task_id, lock_root: lock_root)
    end
    lock.fsync
    secret
  end

  def self.nested_capability_env(repo_root, task_id, lock, parent_execution_id, secret, lock_root: nil)
    stat = lock.stat
    record_root = File.realpath(repo_root)
    resolved_lock_root = File.realpath(lock_root || repo_root)
    env = {
      NESTED_TASK_ID_ENV => task_id.to_s,
      NESTED_RECORD_ROOT_ENV => record_root,
      NESTED_LOCK_FD_ENV => DurableCommandCapture::INHERITED_TASK_LOCK_CHILD_FD.to_s,
      NESTED_LOCK_IDENTITY_ENV => "#{stat.dev}:#{stat.ino}",
      NESTED_PARENT_ENV => parent_execution_id.to_s,
      NESTED_SECRET_ENV => secret.to_s
    }
    env[NESTED_LOCK_ROOT_ENV] = resolved_lock_root == record_root ? nil : resolved_lock_root
    env
  end

  def self.without_nested_capability_env
    NESTED_ENV_KEYS.map { |key| [key, nil] }.to_h
  end

  # Returns nil when env names no protected lineage for this exact task and
  # record root. A lineage scoped elsewhere grants nothing here, so the caller
  # keeps ordinary top-level acquisition; an unscoped capability fails closed.
  def self.nested_capability_request(env, repo_root, task_id, lock_root: nil)
    required_keys = NESTED_ENV_KEYS - [NESTED_LOCK_ROOT_ENV]
    required_values = required_keys.map { |key| env[key] }
    scope_lock_root = env[NESTED_LOCK_ROOT_ENV]
    return nil if required_values.all?(&:nil?) && scope_lock_root.nil?

    unless required_values.all? { |value| value.is_a?(String) && !value.empty? } &&
           (scope_lock_root.nil? || (scope_lock_root.is_a?(String) && !scope_lock_root.empty?))
      raise UnresolvedExecutionStateError, 'nested protected-run capability is incomplete'
    end
    scope_task = env[NESTED_TASK_ID_ENV]
    scope_root = env[NESTED_RECORD_ROOT_ENV]
    unless stable_component?(scope_task) && scope_root && Pathname.new(scope_root).absolute?
      raise UnresolvedExecutionStateError, 'nested protected-run capability does not name its task and record root'
    end
    return nil unless scope_task == task_id.to_s && scope_root == File.realpath(repo_root)

    expected_lock_root = File.realpath(lock_root || repo_root)
    if scope_lock_root
      unless Pathname.new(scope_lock_root).absolute? && File.expand_path(scope_lock_root) == scope_lock_root &&
             scope_lock_root == expected_lock_root
        raise UnresolvedExecutionStateError, 'nested protected-run capability lock root does not match'
      end
    elsif expected_lock_root != File.realpath(repo_root)
      raise UnresolvedExecutionStateError, 'nested protected-run capability does not name its lock root'
    end

    request = required_keys.zip(required_values).to_h
    request[NESTED_LOCK_ROOT_ENV] = scope_lock_root if scope_lock_root
    request
  end

  # Proves the caller already holds this task's lock through an inherited
  # descriptor: it must be the exact owned lock file, a fresh open of that path
  # must be excluded, and the inherited description must re-assert the same
  # exclusive lock. Because a process can also take a free lock itself, the
  # lineage secret must match the held lineage too. Environment values alone
  # never pass, and nothing here waits for or takes a second task-lock ownership.
  def self.verify_nested_capability!(repo_root, task_id, execution_id, request, lock_root: nil)
    fd_text = request[NESTED_LOCK_FD_ENV].to_s
    identity = request[NESTED_LOCK_IDENTITY_ENV].to_s
    parent_id = request[NESTED_PARENT_ENV]
    secret = request[NESTED_SECRET_ENV].to_s
    unless fd_text.match?(/\A[1-9]\d{0,8}\z/) && Integer(fd_text) > 2 && identity.match?(/\A\d+:\d+\z/) &&
           stable_component?(parent_id) && parent_id != execution_id.to_s && secret.match?(/\A[0-9a-f]{64}\z/)
      raise UnresolvedExecutionStateError, 'nested protected-run capability is incomplete or malformed'
    end

    assert_task_lock_generation!(repo_root, task_id, lock_root: lock_root)
    lock = begin
             File.for_fd(Integer(fd_text), autoclose: false)
           rescue Errno::EBADF
             raise UnresolvedExecutionStateError, 'inherited task-lock descriptor is not open'
           end
    lock_stat = lock.stat
    probe = StableLockAnchor.open_existing_lock(
      repo_root, 'task-execution', task_id.to_s, filename: 'execution.lock',
      legacy_paths: [legacy_task_lock_path(repo_root, task_id)], lock_root: lock_root
    )
    begin
      path_stat = probe.stat
      unless lock_stat.file? && lock_stat.uid == Process.uid && "#{lock_stat.dev}:#{lock_stat.ino}" == identity &&
             [lock_stat.dev, lock_stat.ino] == [path_stat.dev, path_stat.ino]
        raise UnresolvedExecutionStateError, "inherited descriptor is not task '#{task_id}' owned execution lock"
      end

      if probe.flock(File::LOCK_EX | File::LOCK_NB)
        raise UnresolvedExecutionStateError, "task '#{task_id}' execution lock is not held by a protected lineage"
      end
    ensure
      probe.close unless probe.closed?
    end
    unless lock.flock(File::LOCK_EX | File::LOCK_NB)
      raise DuplicateExecutionError, "task '#{task_id}' execution lock is held outside this protected lineage"
    end

    lineage_id, capability_sha256 = read_task_lineage!(lock, repo_root, task_id, lock_root: lock_root)
    unless Digest::SHA256.hexdigest(secret) == capability_sha256
      raise UnresolvedExecutionStateError, "nested protected-run capability does not match task '#{task_id}' lineage"
    end
    ancestors = verify_lineage_ancestry!(repo_root, task_id, parent_id, lineage_id)
    if ancestors.include?(execution_id.to_s)
      raise UnresolvedExecutionStateError, "nested execution '#{execution_id}' reuses a protected ancestor identity"
    end
    NestedCapability.new(lock: lock, parent_execution_id: parent_id, ancestor_execution_ids: ancestors,
                         secret: secret)
  rescue SystemCallError => e
    raise UnresolvedExecutionStateError, "nested protected-run capability could not be verified: #{e.message}"
  end

  def self.read_task_lineage!(lock, repo_root, task_id, lock_root: nil)
    size = lock.stat.size
    unless size.positive? && size <= TASK_LINEAGE_MAX_BYTES
      raise UnresolvedExecutionStateError, "task '#{task_id}' execution lock names no nested-capable lineage"
    end
    lineage = JSON.parse(lock.pread(size, 0))
    unless lineage.is_a?(Hash)
      raise UnresolvedExecutionStateError, "task '#{task_id}' execution lock lineage is ambiguous"
    end
    record_root = File.realpath(repo_root)
    expected_lock_root = File.realpath(lock_root || repo_root)
    lock_root_matches = if expected_lock_root == record_root
                          lineage['lock_root'].nil? || lineage['lock_root'] == expected_lock_root
                        else
                          lineage['lock_root'] == expected_lock_root
                        end
    unless lineage.is_a?(Hash) && lineage['schema_version'] == SCHEMA_VERSION &&
           lineage['lock_generation'] == StableLockAnchor::GENERATION &&
           lineage['task_id'] == task_id.to_s && lineage['record_root'] == record_root && lock_root_matches &&
           stable_component?(lineage['execution_id']) && lineage['capability_sha256'].to_s.match?(/\A[0-9a-f]{64}\z/)
      raise UnresolvedExecutionStateError, "task '#{task_id}' execution lock lineage is ambiguous"
    end
    lineage.values_at('execution_id', 'capability_sha256')
  rescue JSON::ParserError, EOFError => e
    raise UnresolvedExecutionStateError, "task execution lock lineage is unreadable: #{e.message}"
  end

  # Walks the caller's protected parents up to the top-level execution named
  # by the held lock's lineage. Only these in-progress ancestors are exempt
  # from the owner check. The chain is named by lineage members, which are
  # already the task's single writer; every record outside it keeps protection.
  def self.verify_lineage_ancestry!(repo_root, task_id, parent_execution_id, lineage_execution_id)
    ancestors = []
    current_id = parent_execution_id
    loop do
      if ancestors.include?(current_id) || ancestors.length >= NESTED_MAX_DEPTH
        raise UnresolvedExecutionStateError, 'nested protected-run ancestry is cyclic or too deep'
      end
      path = default_path(repo_root, task_id, current_id)
      if File.symlink?(path) || !File.file?(path)
        raise UnresolvedExecutionStateError,
              "protected parent execution '#{current_id}' is missing or not a regular file"
      end
      record = load(path)
      unless record.schema_version == SCHEMA_VERSION && record.task_id == task_id.to_s &&
             record.execution_id == current_id && record.status == STATUS_STARTED &&
             record.pid.is_a?(Integer) && record.pid.positive? && record.durable_capture_path.to_s.empty?
        raise UnresolvedExecutionStateError,
              "protected parent execution '#{current_id}' is not an in-progress member of this lineage"
      end
      ancestors << current_id
      if current_id == lineage_execution_id
        return ancestors if record.parent_execution_id.nil?

        raise UnresolvedExecutionStateError, "lineage owner execution '#{current_id}' is not a top-level execution"
      end
      current_id = record.parent_execution_id
      unless stable_component?(current_id)
        raise UnresolvedExecutionStateError,
              "protected parent execution '#{ancestors.last}' does not descend from the task-lock lineage"
      end
    end
  rescue ValidationError, LoadError => e
    raise UnresolvedExecutionStateError, "protected parent execution is unreadable: #{e.message}"
  end

  # Nested acquisition takes no second task-lock ownership: the caller has
  # already proved it holds the lineage lock. It records its own identity and
  # parent after the normal owner check, excluding only verified ancestors.
  def self.acquire_nested_owned!(repo_root, task_id, execution_id, capability, pid:,
                                 heavy_scientific: nil, heavy_run_root: nil, command: nil, lock_root: nil)
    StableLockAnchor.resolve_root_pair(repo_root, lock_root: lock_root)
    verify_no_active_owner!(repo_root, task_id,
                            except_execution_ids: [execution_id.to_s, *capability.ancestor_execution_ids],
                            reject_unrecovered_stale: true,
                            nested_lineage_execution_ids: capability.ancestor_execution_ids)
    acquire!(default_path(repo_root, task_id, execution_id), task_id: task_id, execution_id: execution_id,
             pid: pid, parent_execution_id: capability.parent_execution_id,
             heavy_scientific: heavy_scientific, heavy_run_root: heavy_run_root,
             command: command, repo_root: repo_root)
  end

  def self.stable_component?(value)
    value.is_a?(String) && !value.strip.empty? && !%w[. ..].include?(value) && !value.match?(/[\/\\\x00]/)
  end

  def self.start!(file_path, task_id:, execution_id:, pid:, heavy_scientific: nil, heavy_run_root: nil)
    record = new(
      task_id: task_id,
      execution_id: execution_id,
      pid: pid,
      status: STATUS_STARTED,
      started_at: Time.now.utc.iso8601,
      heavy_scientific: heavy_scientific,
      heavy_run_root: heavy_run_root
    )
    record.record_path = File.expand_path(file_path)
    record.save(file_path)
    record
  end

  def complete!(file_path, durable_capture_path:)
    @status = STATUS_COMPLETED
    @durable_capture_path = durable_capture_path
    @ended_at = Time.now.utc.iso8601
    @record_path = File.expand_path(file_path)
    save(file_path)
    self
  end

  def self.physical_subtree_or_same?(ancestor, candidate)
    ancestor_path = File.realpath(ancestor)
    current = File.realpath(candidate)
    loop do
      return true if File.identical?(ancestor_path, current)

      parent = File.dirname(current)
      return false if parent == current

      current = parent
    end
  rescue SystemCallError
    false
  end

  def resolve_durable_capture_path(record_path: @record_path, repo_root: nil)
    path, _is_fallback = resolve_durable_capture_path_info(record_path: record_path, repo_root: repo_root)
    path
  end

  def resolve_durable_capture_path_info(record_path: @record_path, repo_root: nil)
    primary = @durable_capture_path.to_s.strip
    if !primary.empty? && !File.symlink?(primary) && File.file?(primary)
      return [primary, false]
    end

    fallback = resolve_canonical_fallback_path(record_path: record_path, repo_root: repo_root)
    return [nil, false] if fallback.nil?

    [fallback, true]
  end

  def resolve_canonical_fallback_path(record_path: @record_path, repo_root: nil)
    return nil unless @status == STATUS_COMPLETED
    return nil unless self.class.stable_component?(@task_id) && self.class.stable_component?(@execution_id)

    storage_root = nil
    if repo_root && !repo_root.to_s.strip.empty?
      storage_root = File.expand_path(repo_root.to_s)
    end

    if record_path && !record_path.to_s.strip.empty?
      expanded_record = File.expand_path(record_path.to_s)
      record_dir = File.dirname(expanded_record)
      task_dir = File.dirname(record_dir)
      checkpoints_dir = File.dirname(task_dir)
      fable_dir = File.dirname(checkpoints_dir)
      inferred_root = File.dirname(fable_dir)

      return nil unless File.basename(record_dir) == 'executions' && File.basename(expanded_record) == "#{@execution_id}.json"
      return nil unless File.basename(task_dir) == @task_id
      return nil unless File.basename(checkpoints_dir) == 'checkpoints'
      return nil unless File.basename(fable_dir) == '.fable'

      if storage_root
        begin
          return nil unless File.realpath(storage_root) == File.realpath(inferred_root)
        rescue SystemCallError
          return nil
        end
      else
        storage_root = inferred_root
      end
    end

    return nil if storage_root.nil?

    begin
      return nil if File.symlink?(storage_root) || !File.directory?(storage_root)
      real_storage_root = File.realpath(storage_root)
    rescue SystemCallError
      return nil
    end

    fable_dir = File.join(storage_root, '.fable')
    checkpoints_dir = File.join(fable_dir, 'checkpoints')
    task_dir = File.join(checkpoints_dir, @task_id)
    captures_dir = File.join(task_dir, 'captures')
    candidate_path = File.join(captures_dir, "#{@execution_id}.json")

    # Verify each ancestor directory: must exist, must not be symlink, must be directory,
    # realpath must be strictly contained within real_storage_root without escape.
    ancestor_dirs = [fable_dir, checkpoints_dir, task_dir, captures_dir]
    ancestor_dirs.each do |dir|
      begin
        return nil if File.symlink?(dir)
        stat = File.lstat(dir)
        return nil if stat.symlink? || !stat.directory?

        real_dir = File.realpath(dir)
        prefix = "#{real_storage_root}#{File::SEPARATOR}"
        return nil unless real_dir.start_with?(prefix)
        return nil unless self.class.physical_subtree_or_same?(real_storage_root, real_dir)
      rescue SystemCallError
        return nil
      end
    end

    # Verify target candidate capture file: must exist, must not be symlink, must be regular file,
    # realpath must be in real captures_dir and contained within real_storage_root.
    begin
      return nil if File.symlink?(candidate_path)
      fstat = File.lstat(candidate_path)
      return nil if fstat.symlink? || !fstat.file?

      real_file = File.realpath(candidate_path)
      real_captures = File.realpath(captures_dir)
      return nil unless File.dirname(real_file) == real_captures
      prefix = "#{real_storage_root}#{File::SEPARATOR}"
      return nil unless real_file.start_with?(prefix)
      return nil unless File.basename(real_file) == "#{@execution_id}.json"
    rescue SystemCallError
      return nil
    end

    candidate_path
  end

  def evidence_complete?(record_path: @record_path, repo_root: nil)
    capture_path, is_fallback = resolve_durable_capture_path_info(record_path: record_path, repo_root: repo_root)
    return false if capture_path.nil?

    DurableCommandCapture.classify_evidence(
      capture_path,
      expected_task_id: @task_id,
      expected_execution_id: @execution_id,
      is_fallback: is_fallback
    ) == DurableCommandCapture::EVIDENCE_COMPLETE
  end

  # Classifies this record's prior execution without starting anything new.
  # pid_alive is injectable so callers/tests can supply a deterministic or
  # sandbox-safe liveness check instead of a real OS signal.
  def classify(pid_alive: self.class.method(:pid_alive?), record_path: @record_path, repo_root: nil)
    case @status
    when STATUS_STARTED
      liveness = begin
                   pid_alive.call(@pid)
                 rescue StandardError
                   nil
                 end
      case liveness
      when true then CLASSIFICATION_ACTIVE
      when false then CLASSIFICATION_TERMINATED_INCOMPLETE
      else CLASSIFICATION_STATE_UNRESOLVED
      end
    when STATUS_COMPLETED
      evidence_complete?(record_path: record_path, repo_root: repo_root) ? CLASSIFICATION_COMPLETED : CLASSIFICATION_TERMINATED_INCOMPLETE
    else
      CLASSIFICATION_STATE_UNRESOLVED
    end
  end

  # true = confirmed alive, false = confirmed dead, nil = ambiguous (e.g. no
  # recorded pid, or a liveness check that could not be established) and
  # must fail closed rather than guess.
  def self.pid_alive?(pid)
    return nil if pid.nil?

    Process.kill(0, Integer(pid))
    true
  rescue Errno::ESRCH
    false
  rescue Errno::EPERM
    nil
  rescue ArgumentError, TypeError
    nil
  end

  Recovery = Struct.new(:classification, :execution_record, :durable_capture, :recovery_transition,
                        keyword_init: true)

  # The Contract A guard: call this before starting a possibly-duplicate
  # expensive execution. Raises for ACTIVE and STATE_UNRESOLVED so a caller
  # cannot silently fall through into a duplicate launch; returns a Recovery
  # for COMPLETED (carrying the reusable durable_capture) and for
  # TERMINATED_INCOMPLETE (carrying no capture — rerun eligibility is left to
  # the original task authority, not decided here). Returns a Recovery with a
  # nil classification when no prior record exists at all, since a missing
  # record is not itself evidence of failure.
  def self.recover_before_execution(file_path, pid_alive: method(:pid_alive?), command: nil, repo_root: nil)
    return Recovery.new(classification: nil, execution_record: nil, durable_capture: nil) unless File.file?(file_path)

    record = begin
               load(file_path)
             rescue ValidationError, LoadError
               nil
             end

    classification = record.nil? ? CLASSIFICATION_STATE_UNRESOLVED : record.classify(pid_alive: pid_alive, record_path: file_path, repo_root: repo_root)

    case classification
    when CLASSIFICATION_ACTIVE
      raise DuplicateExecutionError,
            "prior execution '#{record.execution_id}' (pid=#{record.pid}) is still active; refusing duplicate launch"
    when CLASSIFICATION_STATE_UNRESOLVED
      raise UnresolvedExecutionStateError,
            "prior execution state at '#{file_path}' could not be resolved; failing closed rather than risking a duplicate"
    end

    capture = if record && classification == CLASSIFICATION_COMPLETED
                capture_path, _is_fallback = record.resolve_durable_capture_path_info(record_path: file_path, repo_root: repo_root)
                if capture_path && !File.symlink?(capture_path) && File.file?(capture_path)
                  begin
                    DurableCommandCapture.load(capture_path)
                  rescue DurableCommandCapture::ValidationError, DurableCommandCapture::LoadError
                    nil
                  end
                end
              end

    if classification == CLASSIFICATION_COMPLETED
      if capture.nil? || !capture.complete?
        raise UnresolvedExecutionStateError,
              "prior completed execution at '#{file_path}' has unresolvable durable capture"
      end

      if capture.task_id && capture.task_id != record.task_id
        raise UnresolvedExecutionStateError,
              "prior execution capture task_id '#{capture.task_id}' does not match expected task_id '#{record.task_id}'"
      end

      if capture.execution_id && capture.execution_id != record.execution_id
        raise UnresolvedExecutionStateError,
              "prior execution capture execution_id '#{capture.execution_id}' does not match expected execution_id '#{record.execution_id}'"
      end

      if capture.execution_identity.nil?
        raise UnresolvedExecutionStateError,
              'prior execution capture has no credible execution identity'
      end
      if capture.task_id.nil? || capture.execution_id.nil?
        raise UnresolvedExecutionStateError,
              'prior execution capture lacks required task_id and execution_id binding'
      end

      if command
        expected_cmd = Array(command).map(&:to_s)
        if capture.command != expected_cmd
          raise UnresolvedExecutionStateError,
                "prior execution capture command does not match requested command"
        end
      end
    end

    Recovery.new(classification: classification, execution_record: record, durable_capture: capture)
  end

  # The atomic acquisition boundary: combines the recover_before_execution
  # classification with execution-ownership acquisition into a single atomic
  # filesystem operation for the exact task_id + execution_id identity, so
  # two contenders that both observe no existing record cannot both receive
  # permission to invoke the upstream long-running command.
  # save_if_absent! uses create-temp-then-hardlink, which the OS resolves
  # atomically across concurrent callers and which never exposes a partially
  # written record to a losing contender.
  #
  # Returns the same Recovery shape as recover_before_execution. A nil
  # classification means this exact call just won the race: the STARTED
  # record is already durably persisted and returned as
  # recovery.execution_record, and the caller should proceed to invoke the
  # upstream command. Any other classification, or a raised
  # DuplicateExecutionError / UnresolvedExecutionStateError, means a record
  # already existed (a concurrent winner or an earlier session); the losing
  # caller must not proceed, under the exact same rules
  # recover_before_execution already applies to that existing record.
  def self.acquire!(file_path, task_id:, execution_id:, pid:, pid_alive: method(:pid_alive?),
                    parent_execution_id: nil, continuation_from_execution_id: nil,
                    continuation_checkpoint_sha256: nil, continuation_transition_sha256: nil,
                    recovery_old_execution_id: nil, recovery_parent_execution_id: nil,
                    heavy_scientific: nil, heavy_run_root: nil, command: nil, repo_root: nil)
    record = new(
      task_id: task_id,
      execution_id: execution_id,
      pid: pid,
      status: STATUS_STARTED,
      started_at: Time.now.utc.iso8601,
      parent_execution_id: parent_execution_id,
      continuation_from_execution_id: continuation_from_execution_id,
      continuation_checkpoint_sha256: continuation_checkpoint_sha256,
      continuation_transition_sha256: continuation_transition_sha256,
      recovery_old_execution_id: recovery_old_execution_id,
      recovery_parent_execution_id: recovery_parent_execution_id,
      heavy_scientific: heavy_scientific,
      heavy_run_root: heavy_run_root
    )

    begin
      record.save_if_absent!(file_path)
      return Recovery.new(classification: nil, execution_record: record, durable_capture: nil)
    rescue Errno::EEXIST
      recover_before_execution(file_path, pid_alive: pid_alive, command: command, repo_root: repo_root)
    end
  end

  class ValidationError < StandardError; end
  class LoadError < StandardError; end
  class DuplicateExecutionError < StandardError; end
  class UnresolvedExecutionStateError < StandardError; end
end

# One durable transition per execution edge. Ordinary recovery links a stale
# execution to its first successor; an explicit terminal-failure retry links
# that failed successor to a distinct successor without rewriting either edge.
class ExecutionRecoveryTransition
  SCHEMA_VERSION = 1
  STATUS_RESERVED = 'SUCCESSOR_RESERVED'
  DISPOSITION_TERMINATED_INCOMPLETE = ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
  DISPOSITION_FAILED_RECOVERY = 'PRIOR_RECOVERY_SUCCESSOR_FAILED'

  attr_accessor :schema_version, :task_id, :old_execution_id, :old_execution_sha256,
                :old_disposition, :successor_execution_id, :application_state_path,
                :application_state_sha256, :worktree_path, :successor_command_sha256,
                :status, :created_at, :successor_attempt_history, :old_capture_sha256

  def initialize(attrs = {})
    @schema_version = attrs[:schema_version] || attrs['schema_version'] || SCHEMA_VERSION
    @task_id = (attrs[:task_id] || attrs['task_id'])&.to_s
    @old_execution_id = (attrs[:old_execution_id] || attrs['old_execution_id'])&.to_s
    @old_execution_sha256 = (attrs[:old_execution_sha256] || attrs['old_execution_sha256'])&.to_s
    @old_capture_sha256 = (attrs[:old_capture_sha256] || attrs['old_capture_sha256'])&.to_s
    @old_disposition = (attrs[:old_disposition] || attrs['old_disposition'])&.to_s
    @successor_execution_id = (attrs[:successor_execution_id] || attrs['successor_execution_id'])&.to_s
    @application_state_path = (attrs[:application_state_path] || attrs['application_state_path'])&.to_s
    @application_state_sha256 = (attrs[:application_state_sha256] || attrs['application_state_sha256'])&.to_s
    @worktree_path = (attrs[:worktree_path] || attrs['worktree_path'])&.to_s
    @successor_command_sha256 = (attrs[:successor_command_sha256] || attrs['successor_command_sha256'])&.to_s
    @status = (attrs[:status] || attrs['status'])&.to_s
    @created_at = (attrs[:created_at] || attrs['created_at'])&.to_s
    @successor_attempt_history = Array(attrs[:successor_attempt_history] || attrs['successor_attempt_history']).map do |item|
      item.is_a?(Hash) ? item.dup : item
    end
  end

  def to_h
    data = {
      'schema_version' => @schema_version,
      'task_id' => @task_id,
      'old_execution_id' => @old_execution_id,
      'old_execution_sha256' => @old_execution_sha256,
      'old_disposition' => @old_disposition,
      'successor_execution_id' => @successor_execution_id,
      'application_state_path' => @application_state_path,
      'application_state_sha256' => @application_state_sha256,
      'worktree_path' => @worktree_path,
      'successor_command_sha256' => @successor_command_sha256,
      'status' => @status,
      'created_at' => @created_at,
      'successor_attempt_history' => @successor_attempt_history
    }
    data['old_capture_sha256'] = @old_capture_sha256 if @old_capture_sha256
    data
  end

  def to_json(*args)
    JSON.pretty_generate(to_h, *args)
  end

  def self.from_json(json_str)
    data = JSON.parse(json_str)
    raise ValidationError, 'recovery transition JSON root must be an Object' unless data.is_a?(Hash)

    new(data)
  rescue JSON::ParserError => e
    raise ValidationError, "Malformed recovery transition JSON: #{e.message}"
  end

  def self.default_path(repo_root, task_id, old_execution_id)
    File.join(repo_root, '.fable', 'checkpoints', task_id.to_s, 'recoveries', "#{old_execution_id}.json")
  end

  def self.successor_id_for(task_id, old_execution_id)
    digest = Digest::SHA256.hexdigest([task_id.to_s, old_execution_id.to_s].join("\0"))
    "recovery-#{digest}"
  end

  def self.verify_stale_execution!(repo_root:, task_id:, old_execution_id:)
    path = ExecutionRecord.default_path(repo_root, task_id, old_execution_id)
    if File.symlink?(path) || !File.file?(path)
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "stale execution record '#{path}' is missing or is not a regular file"
    end

    bytes = File.binread(path)
    record = ExecutionRecord.from_json(bytes)
    ExecutionRecord.verify_heavy_authority_metadata!(record)
    unless record.schema_version == ExecutionRecord::SCHEMA_VERSION &&
           record.task_id == task_id.to_s && record.execution_id == old_execution_id.to_s
      raise ExecutionRecord::UnresolvedExecutionStateError, 'stale execution identity or schema is ambiguous'
    end
    unless record.status == ExecutionRecord::STATUS_STARTED && record.pid.is_a?(Integer) && record.pid.positive? &&
           record.started_at && !record.started_at.empty? && record.durable_capture_path.to_s.empty? && record.ended_at.nil?
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'only an unambiguous STARTED execution without terminal capture is eligible for stale recovery'
    end

    case ExecutionRecord.pid_alive?(record.pid)
    when true
      raise ExecutionRecord::DuplicateExecutionError,
            "stale execution '#{old_execution_id}' still has a live recorded pid=#{record.pid}"
    when false
      [record, bytes, Digest::SHA256.hexdigest(bytes)]
    else
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "stale execution '#{old_execution_id}' pid liveness could not be established"
    end
  rescue ExecutionRecord::ValidationError => e
    raise ExecutionRecord::UnresolvedExecutionStateError, "stale execution record is malformed: #{e.message}"
  end

  # A retry source must be a captured, failed recovery successor whose entire
  # chain still reaches the original stale execution. Each edge has its own
  # immutable record/capture and a transition bound to those exact bytes.
  def self.verify_failed_successor!(repo_root:, task_id:, execution_id:, visited: [])
    id = execution_id.to_s
    if id.empty? || %w[. ..].include?(id) || id.match?(/[\/\\\x00]/) || visited.include?(id) || visited.length >= 64
      raise ExecutionRecord::UnresolvedExecutionStateError, 'recovery retry lineage is cyclic or ambiguous'
    end
    record_path = ExecutionRecord.default_path(repo_root, task_id, id)
    if File.symlink?(record_path) || !File.file?(record_path)
      raise ExecutionRecord::UnresolvedExecutionStateError, 'prior recovery successor record is missing or not regular'
    end
    record_bytes = File.binread(record_path)
    record = ExecutionRecord.from_json(record_bytes)
    ExecutionRecord.verify_heavy_authority_metadata!(record)
    root_id = record.recovery_old_execution_id.to_s
    if record.schema_version != ExecutionRecord::SCHEMA_VERSION || record.task_id != task_id.to_s ||
       record.execution_id != id || root_id.empty? || %w[. ..].include?(root_id) ||
       root_id.match?(/[\/\\\x00]/) || record.status != ExecutionRecord::STATUS_COMPLETED ||
       !record.pid.is_a?(Integer) || !record.pid.positive? || record.started_at.to_s.empty? ||
       record.ended_at.to_s.empty?
      raise ExecutionRecord::UnresolvedExecutionStateError, 'prior execution is not a terminal recovery successor'
    end
    capture_path = DurableCommandCapture.default_path(repo_root, task_id, id)
    if record.durable_capture_path != capture_path || File.symlink?(capture_path) || !File.file?(capture_path)
      raise ExecutionRecord::UnresolvedExecutionStateError, 'prior recovery capture is missing or ambiguous'
    end
    capture_bytes = File.binread(capture_path)
    capture = DurableCommandCapture.from_json(capture_bytes)
    unless capture.schema_version == DurableCommandCapture::SCHEMA_VERSION && capture.complete?
      raise ExecutionRecord::UnresolvedExecutionStateError, 'prior recovery capture is not durably terminal'
    end
    raise ExecutionRecord::UnresolvedExecutionStateError, 'RECOVERY_SUCCESSOR_ALREADY_SUCCEEDED' if capture.exit_status == 0

    signaled = capture.exit_status.is_a?(String) && /\ASIGNALED:([1-9]\d*)\z/.match(capture.exit_status)
    signal_number = signaled[1].to_i if signaled
    unless (capture.exit_status.is_a?(Integer) && (1..255).cover?(capture.exit_status)) ||
           (signal_number && Signal.list.value?(signal_number) &&
             !%w[STOP CONT].any? { |name| Signal.list[name] == signal_number })
      raise ExecutionRecord::UnresolvedExecutionStateError, 'prior recovery failure result is ambiguous'
    end

    parent_id = record.recovery_parent_execution_id
    if parent_id
      parent, _parent_bytes, source_sha, source_capture_sha, parent_transition = verify_failed_successor!(
        repo_root: repo_root, task_id: task_id, execution_id: parent_id, visited: visited + [id]
      )
      unless parent.recovery_old_execution_id == root_id
        raise ExecutionRecord::UnresolvedExecutionStateError, 'recovery retry root identity does not match'
      end
      source_id = parent_id
      disposition = DISPOSITION_FAILED_RECOVERY
    else
      _original, _original_bytes, source_sha = verify_stale_execution!(
        repo_root: repo_root, task_id: task_id, old_execution_id: root_id
      )
      source_id = root_id
      source_capture_sha = nil
      disposition = DISPOSITION_TERMINATED_INCOMPLETE
    end
    transition_path = default_path(repo_root, task_id, source_id)
    transition = load(transition_path)
    transition.assert_compatible!(
      task_id: task_id, old_execution_id: source_id, old_execution_sha256: source_sha,
      old_capture_sha256: source_capture_sha, old_disposition: disposition,
      application_state_path: transition.application_state_path,
      worktree_path: transition.worktree_path, command_sha256: command_sha256!(capture.command)
    )
    if transition.successor_execution_id != id ||
       (parent_id && (transition.application_state_path != parent_transition.application_state_path ||
                      transition.worktree_path != parent_transition.worktree_path))
      raise ExecutionRecord::UnresolvedExecutionStateError, 'recovery successor lineage does not match its transition'
    end
    transition.verify_readback!(transition_path)
    [record, record_bytes, Digest::SHA256.hexdigest(record_bytes),
     Digest::SHA256.hexdigest(capture_bytes), transition]
  rescue ExecutionRecord::ValidationError, DurableCommandCapture::ValidationError => e
    raise ExecutionRecord::UnresolvedExecutionStateError, "prior recovery provenance is malformed: #{e.message}"
  end

  def self.reserve!(repo_root:, task_id:, old_execution_id:, application_state_path:, worktree_path:, command:)
    old_record, old_bytes, old_sha256 = verify_stale_execution!(
      repo_root: repo_root, task_id: task_id, old_execution_id: old_execution_id
    )
    path = default_path(repo_root, task_id, old_execution_id)
    state_path = normalized_application_state_path(application_state_path)
    command_sha256 = command_sha256!(command)
    cwd_path = normalized_worktree_path(worktree_path)

    if File.exist?(path) || File.symlink?(path)
      ExecutionRecord.sync_directory!(File.dirname(path))
      transition = load(path)
      transition.assert_compatible!(task_id: task_id, old_execution_id: old_execution_id,
                                    old_execution_sha256: old_sha256,
                                    application_state_path: state_path,
                                    worktree_path: cwd_path, command_sha256: command_sha256)
      transition.verify_readback!(path)
      return transition
    end

    successor_id = successor_id_for(task_id, old_execution_id)
    successor_path = ExecutionRecord.default_path(repo_root, task_id, successor_id)
    if File.exist?(successor_path) || File.symlink?(successor_path)
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "deterministic successor '#{successor_id}' already exists without a recovery transition"
    end

    state_sha256 = application_state_sha256!(state_path)
    candidate = new(
      task_id: task_id,
      old_execution_id: old_record.execution_id,
      old_execution_sha256: old_sha256,
      old_disposition: DISPOSITION_TERMINATED_INCOMPLETE,
      successor_execution_id: successor_id,
      application_state_path: state_path,
      application_state_sha256: state_sha256,
      worktree_path: cwd_path,
      successor_command_sha256: command_sha256,
      status: STATUS_RESERVED,
      created_at: Time.now.utc.iso8601,
      successor_attempt_history: []
    )
    begin
      candidate.save_if_absent!(path)
    rescue Errno::EEXIST
      # A concurrent recovery may have reserved the same exact transition.
    end

    transition = load(path)
    transition.assert_compatible!(task_id: task_id, old_execution_id: old_execution_id,
                                  old_execution_sha256: old_sha256,
                                  application_state_path: state_path,
                                  worktree_path: cwd_path, command_sha256: command_sha256)
    unless transition.application_state_sha256 == state_sha256
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'application-state bytes changed while the recovery transition was being reserved'
    end
    transition.verify_readback!(path)
    transition
  end

  def self.reserve_retry!(repo_root:, task_id:, prior_execution_id:, application_state_path:, worktree_path:, command:)
    prior, _bytes, prior_sha, capture_sha, prior_transition = verify_failed_successor!(
      repo_root: repo_root, task_id: task_id, execution_id: prior_execution_id
    )
    state_path = normalized_application_state_path(application_state_path)
    cwd_path = normalized_worktree_path(worktree_path)
    unless state_path == prior_transition.application_state_path && cwd_path == prior_transition.worktree_path
      raise ExecutionRecord::UnresolvedExecutionStateError, 'recovery retry checkpoint or worktree identity changed'
    end
    command_sha256 = command_sha256!(command)
    path = default_path(repo_root, task_id, prior_execution_id)
    if File.exist?(path) || File.symlink?(path)
      transition = load(path)
      transition.assert_compatible!(
        task_id: task_id, old_execution_id: prior_execution_id, old_execution_sha256: prior_sha,
        old_capture_sha256: capture_sha, old_disposition: DISPOSITION_FAILED_RECOVERY,
        application_state_path: state_path, worktree_path: cwd_path, command_sha256: command_sha256
      )
      transition.verify_readback!(path)
      return transition
    end

    successor_id = successor_id_for(task_id, prior_execution_id)
    successor_path = ExecutionRecord.default_path(repo_root, task_id, successor_id)
    if File.exist?(successor_path) || File.symlink?(successor_path)
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "retry successor '#{successor_id}' already exists without a recovery transition"
    end
    state_sha256 = application_state_sha256!(state_path)
    candidate = new(
      task_id: task_id, old_execution_id: prior.execution_id, old_execution_sha256: prior_sha,
      old_capture_sha256: capture_sha, old_disposition: DISPOSITION_FAILED_RECOVERY,
      successor_execution_id: successor_id, application_state_path: state_path,
      application_state_sha256: state_sha256, worktree_path: cwd_path,
      successor_command_sha256: command_sha256, status: STATUS_RESERVED,
      created_at: Time.now.utc.iso8601, successor_attempt_history: []
    )
    begin
      candidate.save_if_absent!(path)
    rescue Errno::EEXIST
      # A concurrent explicit retry may have reserved this exact edge.
    end
    transition = load(path)
    transition.assert_compatible!(
      task_id: task_id, old_execution_id: prior_execution_id, old_execution_sha256: prior_sha,
      old_capture_sha256: capture_sha, old_disposition: DISPOSITION_FAILED_RECOVERY,
      application_state_path: state_path, worktree_path: cwd_path, command_sha256: command_sha256
    )
    unless transition.application_state_sha256 == state_sha256
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'application-state bytes changed while the retry transition was being reserved'
    end
    transition.verify_readback!(path)
    transition
  end

  def self.load(file_path)
    if File.symlink?(file_path) || !File.file?(file_path)
      raise ValidationError, "recovery transition '#{file_path}' is missing or not a regular file"
    end
    from_json(File.binread(file_path))
  rescue JSON::ParserError, ValidationError => e
    raise ExecutionRecord::UnresolvedExecutionStateError, "recovery transition is unreadable: #{e.message}"
  end

  def save(file_path)
    dir = File.dirname(file_path)
    FileUtils.mkdir_p(dir)
    Tempfile.create(['.execution-recovery-', '.tmp'], dir) do |file|
      file.write(to_json)
      file.flush
      file.fsync
      File.rename(file.path, file_path)
      ExecutionRecord.sync_directory!(dir)
    end
    true
  end

  def save_if_absent!(file_path)
    dir = File.dirname(file_path)
    FileUtils.mkdir_p(dir)
    Tempfile.create(['.execution-recovery-', '.tmp'], dir) do |file|
      file.write(to_json)
      file.flush
      file.fsync
      File.link(file.path, file_path)
      ExecutionRecord.sync_directory!(dir)
    end
    true
  end

  def assert_compatible!(task_id:, old_execution_id:, old_execution_sha256:, application_state_path:,
                         worktree_path:, command_sha256:, old_disposition: DISPOSITION_TERMINATED_INCOMPLETE,
                         old_capture_sha256: nil)
    valid_history = @successor_attempt_history.all? do |entry|
      entry.is_a?(Hash) && entry['record_json'].is_a?(String) &&
        entry['record_sha256'] == Digest::SHA256.hexdigest(entry['record_json'])
    end
    unless @schema_version == SCHEMA_VERSION && @task_id == task_id.to_s &&
           @old_execution_id == old_execution_id.to_s && @old_execution_sha256 == old_execution_sha256 &&
           @old_disposition == old_disposition && @old_capture_sha256 == old_capture_sha256 &&
           @successor_execution_id == self.class.successor_id_for(task_id, old_execution_id) &&
           @application_state_path == application_state_path &&
           @application_state_sha256.to_s.match?(/\A[0-9a-f]{64}\z/) &&
           @worktree_path == worktree_path && @successor_command_sha256 == command_sha256 &&
           @status == STATUS_RESERVED && @created_at && !@created_at.empty? && valid_history
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'recovery transition identity, provenance, state locator, or schema is ambiguous'
    end
    true
  end

  def verify_readback!(file_path)
    persisted = self.class.load(file_path)
    unless persisted.to_h == to_h
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'old-to-successor recovery transition failed read-back verification'
    end
    true
  end

  def verify_application_state!
    current_sha256 = self.class.application_state_sha256!(@application_state_path)
    unless current_sha256 == @application_state_sha256
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'application-state locator or bytes changed before successor child launch'
    end
    true
  end

  def archive_successor_attempt!(file_path, record_bytes)
    digest = Digest::SHA256.hexdigest(record_bytes)
    unless @successor_attempt_history.any? { |entry| entry['record_sha256'] == digest }
      @successor_attempt_history << { 'record_sha256' => digest, 'record_json' => record_bytes }
      save(file_path)
    end
    verify_readback!(file_path)
    self
  end

  def self.acquire_successor!(repo_root:, task_id:, old_execution_id:, application_state_path:,
                              worktree_path:, command:, pid:, retry_attempt: false, lock_root: nil)
    ExecutionRecord.with_task_lock(repo_root, task_id, lock_root: lock_root) do
      authority_record = if retry_attempt
        prior, = verify_failed_successor!(repo_root: repo_root, task_id: task_id,
                                          execution_id: old_execution_id)
        root_id = prior.recovery_old_execution_id
        prior
      else
        old_record, _old_bytes, = verify_stale_execution!(
          repo_root: repo_root, task_id: task_id, old_execution_id: old_execution_id
        )
        root_id = old_execution_id
        old_record
      end
      ExecutionRecord.verify_no_active_owner!(repo_root, task_id,
                                              except_execution_ids: retry_attempt ? [] : [old_execution_id.to_s])
      transition = if retry_attempt
                     reserve_retry!(repo_root: repo_root, task_id: task_id,
                                    prior_execution_id: old_execution_id,
                                    application_state_path: application_state_path,
                                    worktree_path: worktree_path, command: command)
                   else
                     reserve!(repo_root: repo_root, task_id: task_id,
                              old_execution_id: old_execution_id,
                              application_state_path: application_state_path,
                              worktree_path: worktree_path, command: command)
                   end
      transition_path = default_path(repo_root, task_id, old_execution_id)
      transition.verify_readback!(transition_path)
      heavy_scientific = HeavyScientificLease.enabled? &&
                         (authority_record.heavy_scientific == true ||
                          HeavyScientificLease.heavy_command?(transition.task_id, command))
      heavy_run_root = if heavy_scientific
                         authority_record.heavy_scientific == true ? authority_record.heavy_run_root :
                           HeavyScientificLease.run_root_from_command(command, worktree_path)
                       end

      successor_id = transition.successor_execution_id
      successor_path = ExecutionRecord.default_path(repo_root, task_id, successor_id)
      expected_capture_path = DurableCommandCapture.default_path(repo_root, task_id, successor_id)
      if File.exist?(successor_path) || File.symlink?(successor_path)
        if File.symlink?(successor_path) || !File.file?(successor_path)
          raise ExecutionRecord::UnresolvedExecutionStateError, 'successor execution record is not a regular file'
        end
        record_bytes = File.binread(successor_path)
        record = ExecutionRecord.from_json(record_bytes)
        ExecutionRecord.verify_heavy_authority_metadata!(record)
        unless record.schema_version == ExecutionRecord::SCHEMA_VERSION &&
               record.task_id == task_id.to_s && record.execution_id == successor_id &&
               (!retry_attempt || (record.recovery_old_execution_id == root_id &&
                                   record.recovery_parent_execution_id == old_execution_id.to_s))
          raise ExecutionRecord::UnresolvedExecutionStateError, 'successor execution identity is ambiguous'
        end

        if record.status == ExecutionRecord::STATUS_COMPLETED
          unless record.durable_capture_path == expected_capture_path &&
                 (!retry_attempt || (!File.symlink?(expected_capture_path) &&
                                     record.started_at && record.ended_at)) &&
                 record.classify(record_path: successor_path, repo_root: repo_root) == ExecutionRecord::CLASSIFICATION_COMPLETED
            raise ExecutionRecord::UnresolvedExecutionStateError,
                  'successor terminal record or durable capture is ambiguous'
          end
          capture = DurableCommandCapture.load(record.durable_capture_path)
          if retry_attempt && command_sha256!(capture.command) != transition.successor_command_sha256
            raise ExecutionRecord::UnresolvedExecutionStateError, 'retry successor capture command identity changed'
          end
          ExecutionRecord::Recovery.new(classification: ExecutionRecord::CLASSIFICATION_COMPLETED,
                                        execution_record: record, durable_capture: capture,
                                        recovery_transition: transition)
        elsif record.status == ExecutionRecord::STATUS_STARTED
          if HeavyScientificLease.enabled? && record.heavy_scientific == true &&
             (!heavy_scientific || record.heavy_run_root != heavy_run_root)
            raise ExecutionRecord::UnresolvedExecutionStateError,
                  'recovery successor heavy authority does not match its validated transition'
          end
          unless record.pid.is_a?(Integer) && record.pid.positive? && record.started_at &&
                 !record.started_at.empty? && record.durable_capture_path.to_s.empty? && record.ended_at.nil?
            raise ExecutionRecord::UnresolvedExecutionStateError,
                  'successor STARTED record is ambiguous and cannot be resumed'
          end
          case ExecutionRecord.pid_alive?(record.pid)
          when true
            raise ExecutionRecord::DuplicateExecutionError,
                  "successor execution '#{successor_id}' is already active (pid=#{record.pid})"
          when nil
            raise ExecutionRecord::UnresolvedExecutionStateError,
                  "successor execution '#{successor_id}' liveness is unresolved"
          end

          transition.verify_application_state!
          transition.archive_successor_attempt!(transition_path, record_bytes)
          resumed = ExecutionRecord.new(task_id: task_id, execution_id: successor_id, pid: pid,
                                        status: ExecutionRecord::STATUS_STARTED,
                                        started_at: Time.now.utc.iso8601,
                                        recovery_old_execution_id: root_id,
                                        recovery_parent_execution_id: retry_attempt ? old_execution_id : nil,
                                        heavy_scientific: heavy_scientific ? true : nil,
                                        heavy_run_root: heavy_scientific ? heavy_run_root : nil)
          resumed.save(successor_path)
          verified = ExecutionRecord.load(successor_path)
          unless verified.schema_version == ExecutionRecord::SCHEMA_VERSION &&
                 verified.task_id == task_id.to_s && verified.execution_id == successor_id &&
                 verified.status == ExecutionRecord::STATUS_STARTED && verified.pid == pid
            raise ExecutionRecord::UnresolvedExecutionStateError,
                  'resumed successor execution claim failed read-back verification'
          end
          ExecutionRecord::Recovery.new(classification: nil, execution_record: verified,
                                        durable_capture: nil, recovery_transition: transition)
        else
          raise ExecutionRecord::UnresolvedExecutionStateError, 'successor execution has unknown status'
        end
      else
        transition.verify_application_state!
        recovery = ExecutionRecord.acquire!(successor_path, task_id: task_id,
                                             execution_id: successor_id, pid: pid,
                                             recovery_parent_execution_id: retry_attempt ? old_execution_id : nil,
                                             recovery_old_execution_id: root_id,
                                             heavy_scientific: heavy_scientific ? true : nil,
                                             heavy_run_root: heavy_scientific ? heavy_run_root : nil)
        unless recovery.classification.nil? && recovery.execution_record &&
               recovery.execution_record.task_id == task_id.to_s &&
               recovery.execution_record.execution_id == successor_id &&
               recovery.execution_record.pid == pid
          raise ExecutionRecord::UnresolvedExecutionStateError,
                'successor identity reservation was not granted to this recovery caller'
        end
        ExecutionRecord::Recovery.new(classification: nil,
                                      execution_record: recovery.execution_record,
                                      durable_capture: nil, recovery_transition: transition)
      end
    end
  rescue ExecutionRecord::ValidationError => e
    raise ExecutionRecord::UnresolvedExecutionStateError, "recovery execution record is malformed: #{e.message}"
  end

  def self.normalized_application_state_path(path)
    unless path && Pathname.new(path.to_s).absolute?
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'application-state locator must be an absolute file path'
    end
    File.expand_path(path.to_s)
  end

  def self.normalized_worktree_path(path)
    unless path && Pathname.new(path.to_s).absolute? && File.directory?(path)
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'protected worktree must be an existing absolute directory'
    end
    File.realpath(path.to_s)
  rescue SystemCallError => e
    raise ExecutionRecord::UnresolvedExecutionStateError,
          "protected worktree '#{path}' could not be resolved: #{e.message}"
  end

  def self.command_sha256!(command)
    argv = Array(command).map(&:to_s)
    if argv.empty? || argv.first.empty?
      raise ExecutionRecord::UnresolvedExecutionStateError,
            'protected recovery command must be a non-empty argv vector'
    end
    Digest::SHA256.hexdigest(JSON.generate(argv))
  end

  def self.application_state_sha256!(path)
    unless Pathname.new(path.to_s).absolute? && File.file?(path) && !File.symlink?(path)
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "application-state locator '#{path}' is missing or not a regular file"
    end

    before = File.stat(path)
    unless before.uid == Process.uid
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "application-state locator '#{path}' is not owned by the current user"
    end
    digest = Digest::SHA256.file(path).hexdigest
    after = File.stat(path)
    unless [before.dev, before.ino, before.size, before.mtime, before.ctime] ==
           [after.dev, after.ino, after.size, after.mtime, after.ctime]
      raise ExecutionRecord::UnresolvedExecutionStateError,
            "application-state locator '#{path}' changed while its bytes were being verified"
    end
    digest
  rescue SystemCallError => e
    raise ExecutionRecord::UnresolvedExecutionStateError,
          "application-state locator '#{path}' could not be verified: #{e.message}"
  end

  class ValidationError < StandardError; end
end

# Checkpoint-specific paused continuation is a distinct authorization mode.

# A narrowly scoped, offline repair path for a task-execution anchor whose
# device number drifted while its lock inode and original stable volume did
# not. Ordinary --run never calls this class and continues to fail closed.
class LockDeviceAnchorRecovery
  AUTHORIZATION_HEADER = 'OWNER_AUTHORIZED_DEVICE_ANCHOR_RECOVERY_V1'

  class Refusal < StandardError
    attr_reader :code

    def initialize(code, message)
      @code = code
      super(message)
    end
  end

  class << self
    def preflight(repo_root:, task_id:, lock_root: nil)
      validate_task_id!(task_id)
      snapshot = StableLockAnchor.device_recovery_snapshot(repo_root, task_id, lock_root: lock_root)
      initial = identity_status(snapshot)
      return initial unless initial.fetch('status') == 'CANDIDATE'

      StableLockAnchor.with_device_recovery_lock(snapshot) do |locked_snapshot, lock|
        current = identity_status(locked_snapshot)
        return current unless current.fetch('status') == 'CANDIDATE'

        validate_execution_journal!(locked_snapshot, lock)
        current.merge('status' => 'ELIGIBLE_READ_ONLY',
                      'owner_authorization_text' => owner_authorization_text(locked_snapshot))
      end
    rescue Refusal => e
      refused(e.code, e.message)
    rescue StableLockAnchor::Error => e
      code = e.message.include?('ACTIVE_OR_COMPETING_WRITER') ? 'ACTIVE_OR_COMPETING_WRITER' : 'ANCHOR_UNVERIFIABLE'
      refused(code, e.message)
    rescue StandardError => e
      refused('PREFLIGHT_UNVERIFIABLE', e.message)
    end

    def apply(repo_root:, task_id:, owner_authorization_path:, lock_root: nil)
      validate_task_id!(task_id)
      snapshot = StableLockAnchor.device_recovery_snapshot(repo_root, task_id, lock_root: lock_root)
      initial = identity_status(snapshot)
      return initial unless initial.fetch('status') == 'CANDIDATE'

      StableLockAnchor.with_device_recovery_lock(snapshot) do |locked_snapshot, lock|
        current = identity_status(locked_snapshot)
        return current unless current.fetch('status') == 'CANDIDATE'

        authorization = read_owner_authorization!(owner_authorization_path)
        unless authorization == owner_authorization_text(locked_snapshot)
          raise Refusal.new('OWNER_AUTHORIZATION_MISMATCH',
                            'owner authorization does not bind the current repository, anchor bytes, volume, and device change')
        end
        validate_execution_journal!(locked_snapshot, lock)

        latest = StableLockAnchor.device_recovery_snapshot(
          locked_snapshot.fetch(:storage_root), locked_snapshot.fetch(:task_id),
          lock_root: locked_snapshot.fetch(:repository)
        )
        unless latest.fetch(:authority_sha256) == locked_snapshot.fetch(:authority_sha256) &&
               latest.fetch(:lock_inode) == locked_snapshot.fetch(:lock_inode) &&
               latest.fetch(:current_lock_device) == locked_snapshot.fetch(:current_lock_device) &&
               latest.fetch(:current_volume_identity) == locked_snapshot.fetch(:current_volume_identity)
          raise Refusal.new('TARGET_CHANGED', 'device-anchor recovery target changed during validation')
        end

        manifest = locked_snapshot.fetch(:manifest).dup
        history = manifest['lock_device_recovery_history'] || []
        unless history.is_a?(Array)
          raise Refusal.new('RECOVERY_HISTORY_INVALID', 'device-anchor recovery history is malformed')
        end
        history << {
          'source_repository' => locked_snapshot.fetch(:repository),
          'from_device' => locked_snapshot.fetch(:stored_lock_device),
          'to_device' => locked_snapshot.fetch(:current_lock_device),
          'lock_inode' => locked_snapshot.fetch(:lock_inode),
          'volume_identity' => locked_snapshot.fetch(:current_volume_identity),
          'previous_authority_sha256' => locked_snapshot.fetch(:authority_sha256),
          'authorization_sha256' => Digest::SHA256.hexdigest(authorization),
          'recovered_at' => Time.now.utc.iso8601
        }
        manifest['lock_device'] = locked_snapshot.fetch(:current_lock_device)
        manifest['lock_device_recovery_history'] = history
        StableLockAnchor.send(:replace_device_recovery_manifest!,
          locked_snapshot, manifest, expected_sha256: locked_snapshot.fetch(:authority_sha256)
        )

        verified = StableLockAnchor.device_recovery_snapshot(
          locked_snapshot.fetch(:storage_root), locked_snapshot.fetch(:task_id),
          lock_root: locked_snapshot.fetch(:repository)
        )
        unless verified.fetch(:stored_lock_device) == verified.fetch(:current_lock_device) &&
               verified.fetch(:lock_inode) == locked_snapshot.fetch(:lock_inode) &&
               verified.fetch(:stored_volume_identity) == verified.fetch(:current_volume_identity) &&
               verified.fetch(:manifest).fetch('lock_device_recovery_history').last == history.last
          raise Refusal.new('READBACK_MISMATCH', 'device-anchor recovery did not pass exact readback verification')
        end
        result(verified, 'RECOVERED', 'same lock inode retained; authority device number atomically rebound')
      end
    rescue Refusal => e
      refused(e.code, e.message)
    rescue StableLockAnchor::Error => e
      code = e.message.include?('ACTIVE_OR_COMPETING_WRITER') ? 'ACTIVE_OR_COMPETING_WRITER' : 'ANCHOR_UNVERIFIABLE'
      refused(code, e.message)
    rescue StandardError => e
      refused('RECOVERY_UNVERIFIABLE', e.message)
    end

    private

    def validate_task_id!(task_id)
      value = task_id.to_s
      unless !value.strip.empty? && !%w[. ..].include?(value) && !value.match?(/[\/\\\x00]/)
        raise Refusal.new('INVALID_TASK_ID', 'device-anchor recovery requires one stable task id')
      end
    end

    def identity_status(snapshot)
      manifest = snapshot.fetch(:manifest)
      history = validate_recovery_history!(snapshot, manifest['lock_device_recovery_history'] || [])

      if snapshot.fetch(:stored_lock_device) == snapshot.fetch(:current_lock_device)
        return result(snapshot, 'ALREADY_CURRENT', 'stored device and current device already agree') if history.empty?

        validate_volume_identity!(snapshot)
        entry = history.last
        unless entry['to_device'] == snapshot.fetch(:current_lock_device) &&
               entry['volume_identity'] == snapshot.fetch(:current_volume_identity)
          raise Refusal.new('RECOVERY_HISTORY_UNVERIFIABLE',
                            'completed device-anchor recovery history does not match the current anchor identity')
        end
        return result(snapshot, 'ALREADY_RECOVERED', 'the exact device recovery is already present')
      end

      validate_volume_identity!(snapshot)
      result(snapshot, 'CANDIDATE', 'device drift is present and stable volume identity matches')
    end

    def validate_recovery_history!(snapshot, history)
      unless history.is_a?(Array)
        raise Refusal.new('RECOVERY_HISTORY_INVALID', 'device-anchor recovery history is malformed')
      end
      fields = %w[authorization_sha256 from_device lock_inode previous_authority_sha256 recovered_at
                  source_repository to_device volume_identity].sort
      previous_to_device = nil
      history.each do |entry|
        unless entry.is_a?(Hash) && entry.keys.sort == fields &&
               entry['source_repository'] == snapshot.fetch(:repository) &&
               entry['lock_inode'] == snapshot.fetch(:lock_inode) &&
               entry['volume_identity'] == snapshot.fetch(:stored_volume_identity) &&
               entry['from_device'].is_a?(Integer) && entry['from_device'].positive? &&
               entry['to_device'].is_a?(Integer) && entry['to_device'].positive? &&
               entry['from_device'] != entry['to_device'] &&
               entry['previous_authority_sha256'].to_s.match?(/\A[0-9a-f]{64}\z/) &&
               entry['authorization_sha256'].to_s.match?(/\A[0-9a-f]{64}\z/)
          raise Refusal.new('RECOVERY_HISTORY_INVALID', 'device-anchor recovery history entry is incomplete or mismatched')
        end
        if previous_to_device && entry['from_device'] != previous_to_device
          raise Refusal.new('RECOVERY_HISTORY_INVALID', 'device-anchor recovery history device chain is discontinuous')
        end
        begin
          Time.iso8601(entry['recovered_at'])
        rescue ArgumentError, TypeError
          raise Refusal.new('RECOVERY_HISTORY_INVALID', 'device-anchor recovery history timestamp is malformed')
        end
        previous_to_device = entry['to_device']
      end
      if previous_to_device && previous_to_device != snapshot.fetch(:stored_lock_device)
        raise Refusal.new('RECOVERY_HISTORY_INVALID', 'device-anchor recovery history does not end at the recorded device')
      end
      history
    end

    def validate_volume_identity!(snapshot)
      stored = snapshot.fetch(:stored_volume_identity)
      current = snapshot.fetch(:current_volume_identity)
      if stored.nil? || stored.to_s.empty?
        raise Refusal.new('HISTORICAL_VOLUME_IDENTITY_MISSING',
                          'anchor has no stable volume identity recorded when it was created')
      end
      if current.nil? || current.to_s.empty?
        raise Refusal.new('CURRENT_VOLUME_IDENTITY_UNAVAILABLE',
                          'the current filesystem does not expose a trusted stable volume identity')
      end
      unless stored == current
        raise Refusal.new('VOLUME_IDENTITY_MISMATCH', 'stored and current stable volume identities differ')
      end
    end

    def validate_execution_journal!(snapshot, lock)
      storage = snapshot.fetch(:storage_root)
      task_id = snapshot.fetch(:task_id)
      execution_dir = File.join(storage, '.fable', 'checkpoints', task_id, 'executions')
      records = {}
      if File.exist?(execution_dir) || File.symlink?(execution_dir)
        stat = File.lstat(execution_dir)
        unless stat.directory? && !stat.symlink? && stat.uid == Process.uid
          raise Refusal.new('JOURNAL_UNVERIFIABLE', 'execution journal directory is not an owned directory')
        end

        Dir.children(execution_dir).sort.each do |name|
          path = File.join(execution_dir, name)
          file_stat = File.lstat(path)
          unless name.end_with?('.json') && file_stat.file? && !file_stat.symlink? &&
                 file_stat.uid == Process.uid && (file_stat.mode & 0o022).zero?
            raise Refusal.new('JOURNAL_UNVERIFIABLE', "execution journal contains an unexpected or unsafe entry '#{name}'")
          end

          execution_id = name.delete_suffix('.json')
          record = ExecutionRecord.load(path)
          unless record.schema_version == ExecutionRecord::SCHEMA_VERSION &&
                 record.task_id == task_id && record.execution_id == execution_id
            raise Refusal.new('JOURNAL_IDENTITY_MISMATCH', "execution journal record '#{name}' has mismatched identity")
          end
          classification = record.classify(record_path: path, repo_root: storage)
          unless record.status == ExecutionRecord::STATUS_COMPLETED &&
                 classification == ExecutionRecord::CLASSIFICATION_COMPLETED
            raise Refusal.new('JOURNAL_WRITER_OR_STATE_UNKNOWN',
                              "execution journal record '#{name}' is active, stale, incomplete, or unresolved")
          end
          records[execution_id] = record
        end
      end

      lineage_bytes = lock.pread(lock.stat.size, 0)
      return true if lineage_bytes.empty?

      lineage = JSON.parse(lineage_bytes)
      unless lineage.is_a?(Hash)
        raise Refusal.new('LOCK_LINEAGE_UNVERIFIABLE', 'execution lock lineage is not a JSON Object')
      end
      expected_root = snapshot.fetch(:repository)
      expected = {
        'schema_version' => ExecutionRecord::SCHEMA_VERSION,
        'lock_generation' => StableLockAnchor::GENERATION,
        'task_id' => task_id,
        'record_root' => storage
      }
      expected['lock_root'] = expected_root unless expected_root == storage
      unless expected.all? { |key, value| lineage[key] == value }
        raise Refusal.new('LOCK_LINEAGE_UNVERIFIABLE', 'execution lock lineage source identity does not match')
      end
      extra_keys = lineage.keys - expected.keys - %w[execution_id capability_sha256]
      unless extra_keys.empty?
        raise Refusal.new('LOCK_LINEAGE_UNVERIFIABLE', 'execution lock lineage contains unknown fields')
      end
      execution_id = lineage['execution_id']
      capability_sha256 = lineage['capability_sha256']
      if execution_id
        unless execution_id.is_a?(String) && records.key?(execution_id) &&
               capability_sha256.is_a?(String) && /\A[0-9a-f]{64}\z/.match?(capability_sha256)
          raise Refusal.new('LOCK_LINEAGE_UNVERIFIABLE',
                            'execution lock lineage does not identify a complete historical execution')
        end
      elsif capability_sha256
        raise Refusal.new('LOCK_LINEAGE_UNVERIFIABLE', 'execution lock lineage has an orphan capability digest')
      end
      true
    rescue Refusal
      raise
    rescue StandardError => e
      raise Refusal.new('JOURNAL_UNVERIFIABLE', e.message)
    end

    def owner_authorization_text(snapshot)
      payload = {
        'action' => 'recover-device-anchor',
        'source_repository' => snapshot.fetch(:repository),
        'storage_root' => snapshot.fetch(:storage_root),
        'task_id' => snapshot.fetch(:task_id),
        'original_authority_sha256' => snapshot.fetch(:authority_sha256),
        'original_lock_device' => snapshot.fetch(:stored_lock_device),
        'current_lock_device' => snapshot.fetch(:current_lock_device),
        'lock_inode' => snapshot.fetch(:lock_inode),
        'stable_volume_identity' => snapshot.fetch(:stored_volume_identity)
      }
      "#{AUTHORIZATION_HEADER}\n#{JSON.generate(payload)}\n"
    end

    def read_owner_authorization!(path)
      value = path.to_s
      unless Pathname.new(value).absolute? && File.expand_path(value) == value && !File.symlink?(value)
        raise Refusal.new('OWNER_AUTHORIZATION_INVALID', 'owner authorization must be an absolute regular file path')
      end
      stat = File.lstat(value)
      unless stat.file? && stat.uid == Process.uid && (stat.mode & 0o022).zero?
        raise Refusal.new('OWNER_AUTHORIZATION_INVALID', 'owner authorization file must be owned, regular, and private')
      end
      File.binread(value)
    rescue Refusal
      raise
    rescue SystemCallError => e
      raise Refusal.new('OWNER_AUTHORIZATION_INVALID', e.message)
    end

    def result(snapshot, status, message)
      {
        'status' => status,
        'message' => message,
        'source_repository' => snapshot.fetch(:repository),
        'storage_root' => snapshot.fetch(:storage_root),
        'task_id' => snapshot.fetch(:task_id),
        'stored_lock_device' => snapshot.fetch(:stored_lock_device),
        'current_lock_device' => snapshot.fetch(:current_lock_device),
        'lock_inode' => snapshot.fetch(:lock_inode),
        'stored_volume_identity' => snapshot.fetch(:stored_volume_identity),
        'current_volume_identity' => snapshot.fetch(:current_volume_identity),
        'authority_sha256' => snapshot.fetch(:authority_sha256)
      }
    end

    def refused(code, message)
      { 'status' => 'REFUSED', 'code' => code, 'message' => message }
    end
  end
end

# CLI interface when executed directly
if __FILE__ == $PROGRAM_NAME
  require 'optparse'

  options = {}
  mode = :reconcile
  selected_modes = []
  separator = ARGV.index('--')
  upstream_argv = ARGV.drop(separator + 1) if separator

  parser = OptionParser.new do |opts|
    opts.banner = 'Usage: task_checkpoint.rb [options] <checkpoint_file_or_task_id>'

    opts.separator '   or: task_checkpoint.rb --run --repo STORAGE_ROOT [--lock-root GIT_ROOT] --worktree PATH --task-id ID --execution-id ID -- <argv...>'
    opts.separator '   or: task_checkpoint.rb --recover-run --repo STORAGE_ROOT [--lock-root GIT_ROOT] --worktree PATH --task-id ID --execution-id OLD_ID --application-state PATH -- <argv...>'
    opts.separator '   or: task_checkpoint.rb --retry-recover-run --repo STORAGE_ROOT [--lock-root GIT_ROOT] --worktree PATH --task-id ID --execution-id FAILED_SUCCESSOR_ID --application-state PATH -- <argv...>'
    opts.separator '   or: task_checkpoint.rb --continue-paused-run --repo PATH --worktree PATH --task-id ID --previous-execution-id OLD_ID --execution-id NEW_ID --application-state PATH --owner-authorization PATH -- <argv...>'
    opts.separator '   or: task_checkpoint.rb --recover-device-anchor --repo STORAGE_ROOT [--lock-root GIT_ROOT] --task-id ID'
    opts.separator '       add --apply-device-anchor-recovery --owner-authorization PATH to apply after read-only preflight'
    opts.on('--reconcile', 'Reconcile live state against checkpoint (default)') { selected_modes << (mode = :reconcile) }
    opts.on('--show', 'Display checkpoint contents') { selected_modes << (mode = :show) }
    opts.on('--save', 'Save/update checkpoint') { selected_modes << (mode = :save) }
    opts.on('--run', 'Acquire, capture, and complete a task-owned execution, or reuse its result') { selected_modes << (mode = :run) }
    opts.on('--heavy-scientific', 'Require the machine-wide heavy-scientific slot for protected runs and recoveries') do
      options[:heavy_scientific] = true
    end
    opts.on('--recover-run', 'Explicitly resume one stale execution through a durable successor transition') { selected_modes << (mode = :recover_run) }
    opts.on('--retry-recover-run', 'Explicitly retry one terminal failed recovery successor') { selected_modes << (mode = :retry_recover_run) }
    opts.on('--continue-paused-run', 'Start a new execution from an exactly authorized PAUSED_RESUMABLE checkpoint') { selected_modes << (mode = :continue_paused_run) }
    opts.on('--recover-device-anchor', 'Read-only device-anchor recovery preflight; never starts an upstream command') do
      selected_modes << (mode = :device_anchor_recovery)
    end
    opts.on('--apply-device-anchor-recovery', 'Apply a preflighted recovery after exact Owner authorization') do
      options[:apply_device_anchor_recovery] = true
    end

    opts.on('--repo PATH', 'Absolute ExecutionRecord, capture, and recovery storage root') { |v| options[:repository] = v }
    opts.on('--lock-root PATH', 'Exact Git worktree top-level that owns the generation-2 lock') { |v| options[:lock_root] = v }
    opts.on('--worktree PATH', 'Live worktree path; --run uses only this absolute upstream cwd') { |v| options[:worktree] = v }
    opts.on('--run-root PATH', 'Absolute run root recorded in the heavy-scientific lease') { |v| options[:run_root] = v }
    opts.on('--task-id ID', 'Caller-supplied stable task identity for --run') { |v| options[:task_id] = v }
    opts.on('--execution-id ID', 'Caller-supplied stable execution identity for --run') { |v| options[:execution_id] = v }
    opts.on('--application-state PATH', 'Absolute durable application checkpoint file required by recovery operations') { |v| options[:application_state] = v }
    opts.on('--previous-execution-id ID', 'Completed protected execution that owns the paused journal prefix') { |v| options[:previous_execution_id] = v }
    opts.on('--owner-authorization PATH', 'Owner authorization file binding the exact protected-operation target') { |v| options[:owner_authorization] = v }
    opts.on('--head SHA', 'Override live git HEAD SHA') { |v| options[:head] = v }
    opts.on('--tree SHA', 'Override live git tree SHA') { |v| options[:tree] = v }
    opts.on('--branch NAME', 'Override live git branch') { |v| options[:branch] = v }
    opts.on('--pr-state STATE', 'Override live PR state') { |v| options[:pr_state] = v }
    opts.on('--resume-after-task-checkpoint PATH', 'Load the exact Task B checkpoint for deferred resume reconciliation') do |v|
      options[:resume_after_task_checkpoint_path] = v
    end
    opts.on('--auth TOKEN', 'Provide direct conversation authorization token') do |v|
      (options[:conversation_authorizations] ||= []) << v
    end
    opts.on('--auth-target TARGET', 'Require the authorization token to name the exact target') do |v|
      options[:authorization_target] = v
    end
  end

  begin
    parser.parse!
    if selected_modes.include?(:run) && selected_modes != [:run]
      raise OptionParser::InvalidArgument, '--run cannot be combined with another mode'
    end
    if selected_modes.include?(:recover_run) && selected_modes != [:recover_run]
      raise OptionParser::InvalidArgument, '--recover-run cannot be combined with another mode'
    end
    if selected_modes.include?(:retry_recover_run) && selected_modes != [:retry_recover_run]
      raise OptionParser::InvalidArgument, '--retry-recover-run cannot be combined with another mode'
    end
    if selected_modes.include?(:continue_paused_run) && selected_modes != [:continue_paused_run]
      raise OptionParser::InvalidArgument, '--continue-paused-run cannot be combined with another mode'
    end
    if selected_modes.include?(:device_anchor_recovery) && selected_modes != [:device_anchor_recovery]
      raise OptionParser::InvalidArgument, '--recover-device-anchor cannot be combined with another mode'
    end
    if options[:heavy_scientific] && !%i[run recover_run retry_recover_run].include?(mode)
      raise OptionParser::InvalidArgument,
            '--heavy-scientific is only valid with protected run and recovery operations'
    end
    if options[:run_root] && mode != :run
      raise OptionParser::InvalidArgument, '--run-root is only valid with --run'
    end
    if options[:run_root] && !Pathname.new(options[:run_root]).absolute?
      raise OptionParser::InvalidArgument, '--run-root must be an absolute path'
    end
    if options[:lock_root] && !%i[run recover_run retry_recover_run device_anchor_recovery].include?(mode)
      raise OptionParser::InvalidArgument, '--lock-root is only valid with protected run and recovery operations'
    end
    if options[:lock_root] && (!Pathname.new(options[:lock_root]).absolute? || !File.directory?(options[:lock_root]))
      raise OptionParser::InvalidArgument, '--lock-root must explicitly name an existing absolute directory'
    end
    if %i[run recover_run retry_recover_run continue_paused_run].include?(mode)
      mode_flag = { run: 'run', recover_run: 'recover-run', retry_recover_run: 'retry-recover-run',
                    continue_paused_run: 'continue-paused-run' }.fetch(mode)
      unless upstream_argv && !upstream_argv.empty? && ARGV == upstream_argv && !upstream_argv.first.empty?
        raise OptionParser::InvalidArgument, "--#{mode_flag} requires upstream argv after -- and no positional arguments before it"
      end
      %i[repository worktree].each do |key|
        value = options[key]
        unless value && Pathname.new(value).absolute? && File.directory?(value)
          flag = key == :repository ? 'repo' : 'worktree'
          raise OptionParser::InvalidArgument, "--#{flag} must explicitly name an existing absolute directory"
        end
      end
      %i[task_id execution_id].each do |key|
        value = options[key]
        if value.to_s.strip.empty? || %w[. ..].include?(value) || value.match?(/[\/\\\x00]/)
          raise OptionParser::InvalidArgument, "--#{key.to_s.tr('_', '-')} must supply a stable non-empty path component"
        end
      end
      if %i[recover_run retry_recover_run continue_paused_run].include?(mode)
        value = options[:application_state]
        unless value && Pathname.new(value).absolute?
          raise OptionParser::InvalidArgument, "--#{mode_flag} requires an absolute --application-state file path"
        end
      elsif options[:application_state]
        raise OptionParser::InvalidArgument, '--application-state is only valid with recovery or continuation operations'
      end
      if mode == :continue_paused_run
        previous_id = options[:previous_execution_id]
        if previous_id.to_s.strip.empty? || %w[. ..].include?(previous_id) || previous_id.match?(/[\/\\\x00]/)
          raise OptionParser::InvalidArgument,
                '--continue-paused-run requires a stable --previous-execution-id'
        end
        if previous_id == options[:execution_id]
          raise OptionParser::InvalidArgument,
                '--previous-execution-id and --execution-id must identify different executions'
        end
        value = options[:owner_authorization]
        unless value && Pathname.new(value).absolute?
          raise OptionParser::InvalidArgument,
                '--continue-paused-run requires an absolute --owner-authorization file path'
        end
      elsif options[:previous_execution_id] || options[:owner_authorization]
        raise OptionParser::InvalidArgument,
              '--previous-execution-id and --owner-authorization are only valid with --continue-paused-run'
      end
    end
    if options[:apply_device_anchor_recovery] && mode != :device_anchor_recovery
      raise OptionParser::InvalidArgument, '--apply-device-anchor-recovery requires --recover-device-anchor'
    end
    if mode == :device_anchor_recovery
      unless separator.nil? && ARGV.empty?
        raise OptionParser::InvalidArgument, '--recover-device-anchor accepts no positional or upstream command arguments'
      end
      value = options[:repository]
      unless value && Pathname.new(value).absolute? && File.directory?(value)
        raise OptionParser::InvalidArgument, '--recover-device-anchor requires an existing absolute --repo directory'
      end
      value = options[:lock_root]
      if value && (!Pathname.new(value).absolute? || !File.directory?(value))
        raise OptionParser::InvalidArgument, '--lock-root must explicitly name an existing absolute directory'
      end
      task_id = options[:task_id].to_s
      if task_id.strip.empty? || %w[. ..].include?(task_id) || task_id.match?(/[\/\\\x00]/)
        raise OptionParser::InvalidArgument, '--recover-device-anchor requires a stable --task-id'
      end
      protected_run_only = %i[worktree run_root execution_id application_state previous_execution_id]
      unless protected_run_only.all? { |key| options[key].nil? }
        raise OptionParser::InvalidArgument, '--recover-device-anchor cannot be combined with protected-run or execution-recovery options'
      end
      if options[:apply_device_anchor_recovery]
        value = options[:owner_authorization]
        unless value && Pathname.new(value).absolute?
          raise OptionParser::InvalidArgument,
                '--apply-device-anchor-recovery requires an absolute --owner-authorization path'
        end
      elsif options[:owner_authorization]
        raise OptionParser::InvalidArgument,
              '--owner-authorization is only valid for an applied device-anchor recovery or --continue-paused-run'
      end
    elsif options[:apply_device_anchor_recovery]
      raise OptionParser::InvalidArgument, '--apply-device-anchor-recovery requires --recover-device-anchor'
    end
  rescue OptionParser::ParseError => e
    warn "ERROR: #{e.message}"
    warn parser
    exit 2
  end

  if mode == :device_anchor_recovery
    result = if options[:apply_device_anchor_recovery]
               LockDeviceAnchorRecovery.apply(
                 repo_root: options[:repository], task_id: options[:task_id],
                 lock_root: options[:lock_root], owner_authorization_path: options[:owner_authorization]
               )
             else
               LockDeviceAnchorRecovery.preflight(
                 repo_root: options[:repository], task_id: options[:task_id], lock_root: options[:lock_root]
               )
             end
    puts JSON.generate(result)
    exit(%w[ELIGIBLE_READ_ONLY RECOVERED ALREADY_CURRENT ALREADY_RECOVERED].include?(result['status']) ? 0 : 1)
  end

  heavy_scientific = HeavyScientificLease.enabled? && mode == :run &&
                     (options[:heavy_scientific] || HeavyScientificLease.heavy_command?(options[:task_id], upstream_argv))
  heavy_run_root = if heavy_scientific
                     File.expand_path(options[:run_root] ||
                       HeavyScientificLease.run_root_from_command(upstream_argv, options[:worktree]))
                   end

  if %i[run recover_run retry_recover_run continue_paused_run].include?(mode)
    begin
      storage_root, resolved_lock_root = StableLockAnchor.resolve_root_pair(
        options[:repository], lock_root: options[:lock_root]
      )
      if options[:lock_root]
        options[:repository] = storage_root
        options[:lock_root] = resolved_lock_root
      end
      nested_request = nil
      ExecutionRecord.assert_task_lock_generation!(options[:repository], options[:task_id],
                                                   lock_root: options[:lock_root])
      if HeavyScientificLease.enabled? && options[:heavy_scientific] &&
         %i[recover_run retry_recover_run].include?(mode)
        authority_record = if mode == :recover_run
                            ExecutionRecoveryTransition.verify_stale_execution!(
                              repo_root: options[:repository], task_id: options[:task_id],
                              old_execution_id: options[:execution_id]
                            ).first
                          else
                            ExecutionRecoveryTransition.verify_failed_successor!(
                              repo_root: options[:repository], task_id: options[:task_id],
                              execution_id: options[:execution_id]
                            ).first
                          end
        unless authority_record.heavy_scientific == true ||
               HeavyScientificLease.heavy_command?(authority_record.task_id, upstream_argv)
          raise ExecutionRecord::UnresolvedExecutionStateError,
                '--heavy-scientific recovery requires heavy authority from the existing execution or task command'
        end
      end
      record_path = ExecutionRecord.default_path(options[:repository], options[:task_id], options[:execution_id])
      if %i[recover_run retry_recover_run].include?(mode)
        recovery = ExecutionRecoveryTransition.acquire_successor!(
          repo_root: options[:repository], task_id: options[:task_id],
          old_execution_id: options[:execution_id],
          application_state_path: options[:application_state],
          worktree_path: options[:worktree], command: upstream_argv, pid: Process.pid,
          retry_attempt: mode == :retry_recover_run, lock_root: options[:lock_root]
        )
        record_path = ExecutionRecord.default_path(
          options[:repository], options[:task_id], recovery.execution_record.execution_id
        )
      elsif mode == :continue_paused_run
        require_relative 'paused_run_continuation'
        recovery = PausedRunContinuationTransition.acquire_successor!(
          repo_root: options[:repository], task_id: options[:task_id],
          predecessor_execution_id: options[:previous_execution_id],
          successor_execution_id: options[:execution_id],
          application_state_path: options[:application_state],
          owner_authorization_path: options[:owner_authorization],
          worktree_path: options[:worktree], command: upstream_argv, pid: Process.pid
        )
        record_path = ExecutionRecord.default_path(
          options[:repository], options[:task_id], recovery.execution_record.execution_id
        )
      else
        nested_request = ExecutionRecord.nested_capability_request(
          ENV, options[:repository], options[:task_id], lock_root: options[:lock_root]
        )
        recovery = if nested_request
                     nested = ExecutionRecord.verify_nested_capability!(
                       options[:repository], options[:task_id], options[:execution_id], nested_request,
                       lock_root: options[:lock_root]
                     )
                     ExecutionRecord.acquire_nested_owned!(options[:repository], options[:task_id],
                                                          options[:execution_id], nested, pid: Process.pid,
                                                          heavy_scientific: heavy_scientific ? true : nil,
                                                          heavy_run_root: heavy_scientific ? heavy_run_root : nil,
                                                          command: upstream_argv, lock_root: options[:lock_root])
                   else
                     ExecutionRecord.acquire_task_owned!(
                       options[:repository], options[:task_id], options[:execution_id], pid: Process.pid,
                       heavy_scientific: heavy_scientific ? true : nil,
                       heavy_run_root: heavy_scientific ? heavy_run_root : nil,
                       command: upstream_argv, lock_root: options[:lock_root]
                     )
                   end
      end
      record = recovery.execution_record
      unless record && record.schema_version == ExecutionRecord::SCHEMA_VERSION &&
             record.task_id == options[:task_id] &&
             (mode == :run ? record.execution_id == options[:execution_id] :
               (%i[recover_run retry_recover_run].include?(mode) ?
                 record.execution_id == recovery.recovery_transition.successor_execution_id :
                 record.execution_id == options[:execution_id] &&
                   record.continuation_from_execution_id == options[:previous_execution_id]))
        raise ExecutionRecord::UnresolvedExecutionStateError, 'execution record identity or schema is malformed'
      end
      unless mode == :run
        ExecutionRecord.verify_heavy_authority_metadata!(record)
        heavy_scientific = HeavyScientificLease.enabled? &&
                           (record.heavy_scientific == true ||
                            HeavyScientificLease.heavy_command?(record.task_id, upstream_argv))
        if HeavyScientificLease.enabled? && options[:heavy_scientific] &&
           !heavy_scientific && recovery.classification.nil?
          raise ExecutionRecord::UnresolvedExecutionStateError,
                '--heavy-scientific recovery requires heavy authority from the execution record or validated task command'
        end
        heavy_run_root = if heavy_scientific
                           record.heavy_scientific == true ? record.heavy_run_root :
                             HeavyScientificLease.run_root_from_command(upstream_argv, options[:worktree])
                         end
      end
      capture_path = DurableCommandCapture.default_path(
        options[:repository], options[:task_id], record.execution_id
      )

      validate_capture = lambda do |candidate|
        unless candidate && candidate.schema_version == DurableCommandCapture::SCHEMA_VERSION && candidate.complete?
          raise ExecutionRecord::UnresolvedExecutionStateError, 'terminal capture is malformed or incomplete'
        end

        unless candidate.command == upstream_argv
          raise ExecutionRecord::UnresolvedExecutionStateError, 'terminal capture command mismatch'
        end
        unless candidate.task_id == options[:task_id] && candidate.execution_id == record.execution_id
          raise ExecutionRecord::UnresolvedExecutionStateError, 'terminal capture task or execution identity mismatch'
        end
        identity = candidate.execution_identity
        unless identity.is_a?(Hash) && identity['task_id'] == options[:task_id] &&
               identity['execution_id'] == record.execution_id
          raise ExecutionRecord::UnresolvedExecutionStateError, 'terminal capture execution identity is missing or mismatched'
        end

        result = candidate.exit_status
        signal_match = result.is_a?(String) && /\ASIGNALED:([1-9]\d*)\z/.match(result)
        signal = signal_match[1].to_i if signal_match
        unless (result.is_a?(Integer) && (0..255).cover?(result)) ||
               (signal && Signal.list.value?(signal) && !%w[STOP CONT].any? { |name| Signal.list[name] == signal })
          raise ExecutionRecord::UnresolvedExecutionStateError, 'terminal capture exit result is malformed'
        end
        [result, signal]
      end

      emit_capture = lambda do |candidate|
        $stdout.binmode.write(candidate.stdout)
        $stderr.binmode.write(candidate.stderr)
        $stdout.flush
        $stderr.flush
      end

      result = nil
      capture_signal = nil
      case recovery.classification
      when nil
        before_spawn = if %i[recover_run retry_recover_run].include?(mode)
                         lambda do
                           transition = recovery.recovery_transition
                           transition_path = ExecutionRecoveryTransition.default_path(
                             options[:repository], options[:task_id], options[:execution_id]
                           )
                           transition.verify_readback!(transition_path)
                           transition.verify_application_state!
                           if mode == :retry_recover_run
                             prior, _prior_bytes, prior_sha, capture_sha, =
                               ExecutionRecoveryTransition.verify_failed_successor!(
                                 repo_root: options[:repository], task_id: options[:task_id],
                                 execution_id: options[:execution_id]
                               )
                             unless prior.recovery_old_execution_id == record.recovery_old_execution_id &&
                                    transition.old_execution_sha256 == prior_sha &&
                                    transition.old_capture_sha256 == capture_sha
                               raise ExecutionRecord::UnresolvedExecutionStateError,
                                     'recovery retry parent changed before successor child launch'
                             end
                           end
                         end
                       elsif mode == :continue_paused_run
                         lambda do
                           transition = recovery.recovery_transition
                           transition_path = PausedRunContinuationTransition.default_path(
                             options[:repository], options[:task_id], transition.checkpoint_sha256
                           )
                           transition.verify_readback!(transition_path)
                           transition.verify_inputs!(
                             repo_root: options[:repository],
                             application_state_path: options[:application_state],
                             owner_authorization_path: options[:owner_authorization],
                             worktree_path: options[:worktree], command: upstream_argv
                           )
                           ExecutionRecord.verify_no_active_owner!(
                             options[:repository], options[:task_id],
                             except_execution_ids: [record.execution_id],
                             reject_unrecovered_stale: true
                           )
                         end
                       end
        capture_blocked = lambda do |status_line, detail|
          now = Time.now.utc.iso8601
          candidate = DurableCommandCapture.new(command: upstream_argv, stdout: "#{status_line}\n",
                                               stderr: detail.to_s, exit_status: 3,
                                               started_at: now, ended_at: now,
                                               task_id: options[:task_id],
                                               execution_id: record.execution_id,
                                               execution_identity: DurableCommandCapture.capture_execution_identity(
                                                 repository_path: options[:repository],
                                                 worktree_path: options[:worktree],
                                                 task_id: options[:task_id],
                                                 execution_id: record.execution_id,
                                                 lock_root: options[:lock_root]
                                               ))
          candidate.save(capture_path)
          result, capture_signal = validate_capture.call(candidate)
          record.complete!(record_path, durable_capture_path: capture_path)
          emit_capture.call(candidate)
          candidate
        end
        capture_command = lambda do |task_lock, child_env, nested = nil|
          lease = nil
          begin
            inherited_locks = [task_lock]
            if HeavyScientificLease.enabled? && heavy_scientific
              lease = if nested
                        request = HeavyScientificLease.nested_capability_request(ENV)
                        if request
                          HeavyScientificLease.verify_nested!(
                            request: request, repo_root: options[:repository], task_id: options[:task_id],
                            execution_id: record.execution_id,
                            ancestor_execution_ids: nested.ancestor_execution_ids
                          )
                        else
                          HeavyScientificLease.acquire!(
                            task_id: options[:task_id], execution_id: record.execution_id,
                            worktree: options[:worktree], run_root: heavy_run_root,
                            record_root: options[:repository]
                          )
                        end
                      else
                        HeavyScientificLease.acquire!(
                          task_id: options[:task_id], execution_id: record.execution_id,
                          worktree: options[:worktree], run_root: heavy_run_root,
                          record_root: options[:repository]
                        )
                      end
              child_env = child_env.merge(lease.capability_env(
                child_fd: DurableCommandCapture::INHERITED_HEAVY_SCIENTIFIC_LEASE_CHILD_FD
              ))
              inherited_locks << lease.lock
              warn 'LEASE_ACQUIRED / WOULD_LAUNCH' if lease.owned?
            else
              child_env = child_env.merge(HeavyScientificLease.clear_capability_env)
            end

            DurableCommandCapture.run_and_capture(upstream_argv, file_path: capture_path,
                                                  chdir: options[:worktree], before_spawn: before_spawn,
                                                  inherited_locks: inherited_locks, child_env: child_env,
                                                  task_id: options[:task_id],
                                                  execution_id: record.execution_id,
                                                  execution_identity: lambda {
                                                    DurableCommandCapture.capture_execution_identity(
                                                      repository_path: options[:repository],
                                                      worktree_path: options[:worktree],
                                                      task_id: options[:task_id],
                                                      execution_id: record.execution_id,
                                                      lock_root: options[:lock_root]
                                                    )
                                                  }) do |candidate|
              result, capture_signal = validate_capture.call(candidate)
              record.complete!(record_path, durable_capture_path: capture_path)
              emit_capture.call(candidate)
            end
          rescue HeavyScientificLease::LegacyWriterActive => e
            warn 'STOP_ONCE: HEAVY_SCIENTIFIC_WRITER_ACTIVE'
            capture_blocked.call('STOP_ONCE: HEAVY_SCIENTIFIC_WRITER_ACTIVE', e.writers.inspect)
          rescue HeavyScientificLease::SlotOwned => e
            warn 'STOPPED_HEAVY_SLOT_OWNED'
            capture_blocked.call('STOPPED_HEAVY_SLOT_OWNED', e.message)
          rescue HeavyScientificLease::Error => e
            warn 'STOPPED_HEAVY_SLOT_OWNED'
            capture_blocked.call('STOPPED_HEAVY_SLOT_OWNED', "heavy lease failed closed: #{e.message}")
          ensure
            lease.release! if lease && lease.owned?
          end
        end
        capture = if nested_request
                    # Re-prove the lineage after this record is durable, so of two
                    # concurrently claimed siblings at least one observes the other.
                    nested = ExecutionRecord.verify_nested_capability!(
                      options[:repository], options[:task_id], record.execution_id, nested_request,
                      lock_root: options[:lock_root]
                    )
                    ExecutionRecord.verify_no_active_owner!(
                      options[:repository], options[:task_id],
                      except_execution_ids: [record.execution_id, *nested.ancestor_execution_ids],
                      reject_unrecovered_stale: true,
                      nested_lineage_execution_ids: nested.ancestor_execution_ids
                    )
                    capture_command.call(nested.lock, ExecutionRecord.nested_capability_env(
                      options[:repository], options[:task_id], nested.lock, record.execution_id, nested.secret,
                      lock_root: options[:lock_root]
                    ), nested)
                  else
                    ExecutionRecord.with_task_lock(options[:repository], options[:task_id],
                                                   lock_root: options[:lock_root]) do |task_lock|
                      ExecutionRecord.verify_no_active_owner!(options[:repository], options[:task_id],
                                                              except_execution_ids: [record.execution_id],
                                                              reject_unrecovered_stale: %i[run continue_paused_run].include?(mode))
                      # A normal run and a recovery successor both own this lock
                      # while their upstream command runs, so each may delegate
                      # nested execution through its protected lineage.
                      lineage_owner = record.execution_id
                      lineage_secret = ExecutionRecord.write_task_lineage!(task_lock, options[:repository],
                                                                           options[:task_id], lineage_owner,
                                                                           lock_root: options[:lock_root])
                      child_env = if lineage_owner
                                    ExecutionRecord.nested_capability_env(options[:repository], options[:task_id],
                                                                          task_lock, lineage_owner, lineage_secret,
                                                                          lock_root: options[:lock_root])
                                  else
                                    ExecutionRecord.without_nested_capability_env
                                  end
                      child_env.merge!(
                        'FABLE_PAUSED_CONTINUATION_PREDECESSOR_EXECUTION_ID' => nil,
                        'FABLE_PAUSED_CONTINUATION_CHECKPOINT_SHA256' => nil,
                        'FABLE_PAUSED_CONTINUATION_NEXT_ATTEMPT' => nil
                      )
                      if mode == :continue_paused_run
                        recovery.recovery_transition.verify_inputs!(
                          repo_root: options[:repository],
                          application_state_path: options[:application_state],
                          owner_authorization_path: options[:owner_authorization],
                          worktree_path: options[:worktree], command: upstream_argv
                        )
                        child_env.merge!(recovery.recovery_transition.child_environment)
                      end
                      capture_command.call(task_lock, child_env)
                    end
                  end
      when ExecutionRecord::CLASSIFICATION_COMPLETED
        capture = recovery.durable_capture
        result, capture_signal = validate_capture.call(capture)
        emit_capture.call(capture)
      when ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
        warn "#{recovery.classification}: original task authority must resolve rerun eligibility; record retained"
        exit 1
      else
        raise ExecutionRecord::UnresolvedExecutionStateError, 'acquisition did not grant execution ownership'
      end

      signal = capture.wrapper_signal || capture_signal
      if signal
        Signal.trap(signal, 'SYSTEM_DEFAULT') unless signal == Signal.list['KILL']
        Process.kill(signal, Process.pid)
        exit(128 + signal)
      end
      exit result
    rescue ExecutionRecord::DuplicateExecutionError => e
      warn "#{ExecutionRecord::CLASSIFICATION_ACTIVE}: #{e.message}"
      exit 1
    rescue ExecutionRecoveryTransition::ValidationError => e
      warn "#{ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED}: #{e.message}"
      exit 1
    rescue StandardError => e
      warn "#{ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED}: #{e.message}"
      exit 1
    end
  end

  target = ARGV.first
  if target.nil? || target.empty?
    warn parser
    exit 2
  end

  checkpoint_path = if File.file?(target)
                      target
                    else
                      # Look for task_id in .fable/checkpoints
                      TaskCheckpoint.default_path(Dir.pwd, target)
                    end

  case mode
  when :show
    begin
      cp = TaskCheckpoint.load(checkpoint_path)
      puts cp.to_json
      exit 0
    rescue StandardError => e
      warn "ERROR: #{e.message}"
      exit 1
    end

  when :reconcile
    begin
      cp = TaskCheckpoint.load(checkpoint_path)
      if options[:resume_after_task_checkpoint_path]
        options[:resume_after_task_checkpoint] = TaskCheckpoint.load(options.delete(:resume_after_task_checkpoint_path))
      end
      reconciler = TaskReconciler.new(cp, options)
      result = reconciler.reconcile
      puts result.to_text
      exit(result.verdict == 'STOP_UNRESOLVED' ? 1 : 0)
    rescue TaskCheckpoint::ValidationError, TaskCheckpoint::LoadError => e
      puts "RECONCILIATION_VERDICT: STOP_UNRESOLVED\nREASON: Malformed or unreadable checkpoint: #{e.message}\nRECOMMENDED_ACTION: Inspect or regenerate checkpoint"
      exit 1
    end
  end
end
