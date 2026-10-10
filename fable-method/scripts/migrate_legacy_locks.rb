#!/usr/bin/env ruby
# frozen_string_literal: true

require 'optparse'
require 'digest'
require 'open3'
require 'pathname'
require_relative 'task_checkpoint'

# Converts legacy worktree-local Fable locks to the generation-2 anchors in
# Git's common metadata directory. Checkpoint and execution history files are
# read only; only legacy lock files are removed after their replacements
# have been created and verified.
class LegacyLockGeneration2Migration
  class Error < StandardError; end

  AUTHORIZATION_ACTION = 'MIGRATE_LEGACY_LOCKS_TO_GENERATION_2'
  QUIESCENCE_ATTESTATION = 'LEGACY_WORKERS_AND_INHERITED_CHILDREN_QUIESCENT: YES'

  Target = Struct.new(
    :namespace,
    :key,
    :filename,
    :legacy_path,
    :legacy_present,
    :legacy_identity,
    :state_path,
    :state_identity,
    keyword_init: true
  )
  Plan = Struct.new(:repository, :common_directory, :task_ids, :checkpoint_paths, :targets, keyword_init: true)
  Result = Struct.new(:plan, :applied, keyword_init: true)

  def initialize(repo_root:, checkpoint_paths: [])
    unless Pathname.new(repo_root.to_s).absolute?
      raise Error, '--repo must be an absolute Git worktree root'
    end

    @repository = StableLockAnchor.repository_root(repo_root)
    @common_directory = common_directory(@repository)
    @checkpoint_paths = Array(checkpoint_paths)
  end

  def run(apply: false, owner_authorization_path: nil)
    if owner_authorization_path && !apply
      raise Error, '--owner-authorization is only valid with --apply'
    end

    plan = build_plan
    return Result.new(plan: plan, applied: false) unless apply

    authorization = validate_owner_authorization!(owner_authorization_path, plan)

    legacy_handles = []
    generation2_handles = []
    begin
      legacy_handles = acquire_legacy_locks(plan)
      assert_plan_unchanged!(plan)
      validate_owner_authorization!(
        owner_authorization_path,
        plan,
        expected_identity: authorization.fetch(:identity),
        expected_sha256: authorization.fetch(:sha256)
      )

      StableLockAnchor.initialize_repository_generation!(
        @repository,
        confirm_legacy_workers_quiescent: true
      )

      plan.targets.each do |target|
        lock = StableLockAnchor.open_lock(
          @repository,
          target.namespace,
          target.key,
          filename: target.filename,
          legacy_paths: []
        )
        unless lock.flock(File::LOCK_EX | File::LOCK_NB)
          lock.close
          raise Error, "generation-2 lock '#{target.namespace}:#{target.key}' is active"
        end

        generation2_handles << lock
      end

      assert_plan_unchanged!(plan)
      validate_owner_authorization!(
        owner_authorization_path,
        plan,
        expected_identity: authorization.fetch(:identity),
        expected_sha256: authorization.fetch(:sha256)
      )
      remove_legacy_locks!(plan, legacy_handles)
      verify_migrated_anchors!(plan)
      Result.new(plan: plan, applied: true)
    ensure
      (generation2_handles + legacy_handles).each do |lock|
        lock.close unless lock.closed?
      rescue IOError
        # Preserve the original migration error if a descriptor was already closed.
      end
    end
  rescue StableLockAnchor::Error, SystemCallError, JSON::ParserError => e
    raise Error, e.message
  end

  def authorization_target(plan)
    scope = {
      'repository' => plan.repository,
      'common_directory' => plan.common_directory,
      'task_ids' => plan.task_ids,
      'checkpoint_paths' => plan.checkpoint_paths
    }
    digest = Digest::SHA256.hexdigest(JSON.generate(scope))
    "legacy-lock-generation2-offline-migration:#{digest}"
  end

  private

  def build_plan
    task_ids = []
    targets = []
    checkpoints = []
    checkpoint_root = File.join(@repository, '.fable', 'checkpoints')

    if File.exist?(File.join(@repository, '.fable')) || File.symlink?(File.join(@repository, '.fable'))
      validate_directory!(File.join(@repository, '.fable'))
    end
    if File.exist?(checkpoint_root) || File.symlink?(checkpoint_root)
      validate_directory!(checkpoint_root)
      Dir.children(checkpoint_root).sort.each do |entry|
        path = File.join(checkpoint_root, entry)
        stat = File.lstat(path)
        raise Error, "unsupported symlink in checkpoint directory: #{path}" if stat.symlink?

        if stat.directory?
          raise Error, "invalid legacy task directory name: #{entry}" unless ExecutionRecord.stable_component?(entry)

          task_ids << entry
          legacy_path = File.join(path, 'execution.lock')
          present = path_exists?(legacy_path)
          legacy_identity = validate_legacy_lock!(legacy_path) if present
          targets << Target.new(
            namespace: 'task-execution',
            key: entry,
            filename: 'execution.lock',
            legacy_path: legacy_path,
            legacy_present: present,
            legacy_identity: legacy_identity,
            state_path: path,
            state_identity: stat_identity(stat)
          )
        elsif stat.file? && entry.end_with?('.json')
          checkpoints << validate_checkpoint_path!(path)
        elsif stat.file? && entry.end_with?('.json.lock')
          checkpoint_path = path.delete_suffix('.lock')
          raise Error, "legacy checkpoint lock has no checkpoint file: #{path}" unless path_exists?(checkpoint_path)

          checkpoints << validate_checkpoint_path!(checkpoint_path)
        end
      end
    end

    @checkpoint_paths.each do |path|
      checkpoints << validate_checkpoint_path!(path)
    end

    checkpoints = checkpoints.uniq.sort
    checkpoints.each do |checkpoint_path|
      legacy_path = "#{checkpoint_path}.lock"
      present = path_exists?(legacy_path)
      legacy_identity = validate_legacy_lock!(legacy_path) if present
      relative_path = Pathname.new(checkpoint_path).relative_path_from(Pathname.new(@repository)).to_s
      targets << Target.new(
        namespace: 'checkpoint-sidecar',
        key: relative_path,
        filename: 'checkpoint.lock',
        legacy_path: legacy_path,
        legacy_present: present,
        legacy_identity: legacy_identity,
        state_path: checkpoint_path,
        state_identity: stat_identity(File.lstat(checkpoint_path))
      )
    end

    Plan.new(
      repository: @repository,
      common_directory: @common_directory,
      task_ids: task_ids.uniq.sort,
      checkpoint_paths: checkpoints,
      targets: targets.uniq { |target| [target.namespace, target.key] }
    )
  rescue Errno::ENOENT => e
    raise Error, "migration input changed during inventory: #{e.message}"
  end

  def validate_directory!(path)
    stat = File.lstat(path)
    unless stat.directory? && !stat.symlink? && stat.uid == Process.uid
      raise Error, "migration directory '#{path}' is not an owned real directory"
    end
  end

  def validate_checkpoint_path!(path)
    expanded = File.expand_path(path.to_s, @repository)
    stat = File.lstat(expanded)
    unless stat.file? && !stat.symlink? && stat.uid == Process.uid
      raise Error, "checkpoint '#{expanded}' is not an owned regular file"
    end

    resolved = File.realpath(expanded)
    prefix = "#{@repository}#{File::SEPARATOR}"
    unless resolved.start_with?(prefix)
      raise Error, "checkpoint '#{expanded}' resolves outside repository '#{@repository}'"
    end
    resolved
  rescue Errno::ENOENT => e
    raise Error, "checkpoint could not be verified: #{e.message}"
  end

  def validate_legacy_lock!(path)
    stat = File.lstat(path)
    unless stat.file? && !stat.symlink? && stat.uid == Process.uid
      raise Error, "legacy lock '#{path}' is not an owned regular file"
    end
    stat_identity(stat)
  rescue Errno::ENOENT => e
    raise Error, "legacy lock changed during inventory: #{e.message}"
  end

  def stat_identity(stat)
    [stat.dev, stat.ino, stat.uid, stat.mode, stat.size, stat.mtime.to_r, stat.ctime.to_r]
  end

  def path_exists?(path)
    File.exist?(path) || File.symlink?(path)
  end

  def validate_owner_authorization!(path, plan, expected_identity: nil, expected_sha256: nil)
    unless path && Pathname.new(path.to_s).absolute?
      raise Error, '--apply requires an absolute --owner-authorization PATH'
    end

    text, identity = read_owner_authorization(path)
    digest = Digest::SHA256.hexdigest(text)
    if expected_identity && (identity != expected_identity || digest != expected_sha256)
      raise Error, 'Owner authorization changed during migration preflight'
    end

    target = authorization_target(plan)
    unless authorization_pairs(text) == [[AUTHORIZATION_ACTION, target]] &&
           TaskReconciler.conversation_authorized?(AUTHORIZATION_ACTION, [text], authorization_target: target)
      raise Error, 'Owner authorization does not exactly bind this generation-2 migration target'
    end
    unless text.each_line.count { |line| line.strip == QUIESCENCE_ATTESTATION } == 1
      raise Error, "Owner authorization must contain exactly one explicit quiescence attestation line: '#{QUIESCENCE_ATTESTATION}'"
    end

    { identity: identity, sha256: digest }
  end

  def authorization_pairs(text)
    text.each_line.each_with_object([]) do |line, pairs|
      action_field = /(?:\A|[^\w])(?:AUTHORIZED_)?ACTION\s*[:=]/i
      target_field = /(?:\A|[^\w])(?:AUTHORIZED_)?TARGET\s*[:=]/i
      next unless line.match?(action_field) || line.match?(target_field)

      binding = line.match(/\A\s*(?:AUTHORIZED_)?ACTION\s*[:=]\s*([A-Z][A-Z0-9_]*)\s*;\s*(?:AUTHORIZED_)?TARGET\s*[:=]\s*(.+?)\s*\z/i)
      raise Error, 'Owner authorization contains an unbound or ambiguous action/target' unless binding

      pairs << [binding[1].strip.upcase, binding[2].strip]
    end
  end

  def read_owner_authorization(path)
    expanded = File.expand_path(path.to_s)
    stat = File.lstat(expanded)
    unless stat.file? && !stat.symlink? && stat.uid == Process.uid && (stat.mode & 0o022).zero?
      raise Error, "Owner authorization '#{expanded}' is not an owned, non-writable regular file"
    end

    flags = File::RDONLY
    flags |= File::NOFOLLOW if File.const_defined?(:NOFOLLOW)
    File.open(expanded, flags) do |file|
      opened_stat = file.stat
      unless stat_identity(stat) == stat_identity(opened_stat)
        raise Error, 'Owner authorization changed while opening'
      end
      bytes = file.read
      final_stat = File.lstat(expanded)
      unless stat_identity(final_stat) == stat_identity(opened_stat)
        raise Error, 'Owner authorization changed while reading'
      end
      text = bytes.dup.force_encoding(Encoding::UTF_8)
      raise Error, 'Owner authorization is not valid UTF-8 text' unless text.valid_encoding?

      [text, stat_identity(opened_stat)]
    end
  rescue SystemCallError => e
    raise Error, "Owner authorization could not be verified: #{e.message}"
  end

  def common_directory(repository)
    output, status = Open3.capture2('git', '-C', repository, 'rev-parse', '--git-common-dir', err: File::NULL)
    raise Error, "Git common directory for '#{repository}' could not be resolved" unless status.success?

    common = output.strip
    File.realpath(Pathname.new(common).absolute? ? common : File.join(repository, common))
  rescue SystemCallError => e
    raise Error, "Git common directory could not be verified: #{e.message}"
  end

  def acquire_legacy_locks(plan)
    handles = []
    plan.targets.select(&:legacy_present).sort_by(&:legacy_path).each do |target|
      path = target.legacy_path
      flags = File::RDWR
      flags |= File::NOFOLLOW if File.const_defined?(:NOFOLLOW)
      lock = File.open(path, flags)
      current = File.lstat(path)
      unless current.file? && !current.symlink? &&
             stat_identity(current) == target.legacy_identity &&
             stat_identity(lock.stat) == target.legacy_identity
        lock.close
        raise Error, "legacy lock '#{path}' changed while opening"
      end
      unless lock.flock(File::LOCK_EX | File::LOCK_NB)
        lock.close
        raise Error, "legacy lock '#{path}' is active"
      end

      handles << lock
    end
    handles
  rescue StandardError
    handles&.each { |lock| lock.close unless lock.closed? }
    raise
  end

  def assert_plan_unchanged!(plan)
    current = build_plan
    original_signature = plan_signature(plan)
    current_signature = plan_signature(current)
    return if original_signature == current_signature

    raise Error, 'Fable checkpoint or legacy-lock inventory changed during migration preflight'
  end

  def plan_signature(plan)
    [
      plan.common_directory,
      plan.task_ids,
      plan.checkpoint_paths,
      plan.targets.map do |target|
        [
          target.namespace,
          target.key,
          target.filename,
          target.legacy_path,
          target.legacy_present,
          target.legacy_identity,
          target.state_path,
          target.state_identity
        ]
      end.sort
    ]
  end

  def remove_legacy_locks!(plan, handles)
    locks_by_path = handles.to_h { |lock| [lock.path, lock] }
    plan.targets.select(&:legacy_present).each do |target|
      path = target.legacy_path
      lock = locks_by_path.fetch(path)
      current = File.lstat(path)
      unless current.file? && !current.symlink? &&
             stat_identity(current) == target.legacy_identity &&
             stat_identity(lock.stat) == target.legacy_identity
        raise Error, "legacy lock '#{path}' changed before removal; migration is incomplete"
      end

      File.unlink(path)
      sync_directory!(File.dirname(path))
    end
  end

  def verify_migrated_anchors!(plan)
    StableLockAnchor.initialize_repository_generation!(
      @repository,
      confirm_legacy_workers_quiescent: true
    )
    plan.targets.each do |target|
      lock = StableLockAnchor.open_existing_lock(
        @repository,
        target.namespace,
        target.key,
        filename: target.filename,
        legacy_paths: []
      )
      lock.close
      if target.legacy_path && path_exists?(target.legacy_path)
        raise Error, "legacy lock '#{target.legacy_path}' remains after migration"
      end
    end
  end

  def sync_directory!(path)
    File.open(path, File::RDONLY) { |directory| directory.fsync }
  end
end

if __FILE__ == $PROGRAM_NAME
  options = { checkpoint_paths: [] }
  parser = OptionParser.new do |opts|
    opts.banner = <<~USAGE
      Usage: migrate_legacy_locks.rb --repo PATH [--checkpoint PATH ...] [--apply --owner-authorization PATH]

      Without --apply, list the generation-2 anchors and legacy locks that would be migrated.
      --checkpoint PATH may be repeated for checkpoint files outside .fable/checkpoints.
      Apply requires an exact Owner authorization file with the migration action, dry-run target, and quiescence attestation.
    USAGE
    opts.on('--repo PATH', 'Absolute Git worktree root to inspect') { |value| options[:repo_root] = value }
    opts.on('--checkpoint PATH', 'Additional checkpoint file whose adjacent .lock sidecar should be migrated') do |value|
      options[:checkpoint_paths] << value
    end
    opts.on('--apply', 'Write generation-2 markers and anchors, then remove migrated lock sidecars') do
      options[:apply] = true
    end
    opts.on('--owner-authorization PATH', 'Current Owner authorization file binding the exact migration action and target') do |value|
      options[:owner_authorization_path] = value
    end
  end

  begin
    parser.parse!
    raise OptionParser::MissingArgument, '--repo PATH' unless options[:repo_root]
    raise OptionParser::InvalidArgument, 'unexpected positional arguments' unless ARGV.empty?

    migration = LegacyLockGeneration2Migration.new(
      repo_root: options[:repo_root],
      checkpoint_paths: options[:checkpoint_paths]
    )
    result = migration.run(
      apply: options.fetch(:apply, false),
      owner_authorization_path: options[:owner_authorization_path]
    )
    puts(result.applied ? 'MIGRATED' : 'DRY RUN')
    puts "Repository: #{result.plan.repository}"
    puts "Git common directory: #{result.plan.common_directory}"
    puts "Task anchors: #{result.plan.task_ids.length}"
    puts "Checkpoint files inspected: #{result.plan.checkpoint_paths.length}"
    puts "Legacy lock files to remove: #{result.plan.targets.count(&:legacy_present)}"
    puts "Owner authorization binding: AUTHORIZED_ACTION: #{LegacyLockGeneration2Migration::AUTHORIZATION_ACTION}; AUTHORIZED_TARGET: #{migration.authorization_target(result.plan)}"
    puts "Required quiescence attestation: #{LegacyLockGeneration2Migration::QUIESCENCE_ATTESTATION}"
    result.plan.targets.each do |target|
      puts "Anchor: #{target.namespace} #{target.key}"
      puts "Remove legacy lock: #{target.legacy_path}" if target.legacy_present
    end
  rescue OptionParser::ParseError, LegacyLockGeneration2Migration::Error, StableLockAnchor::Error => e
    warn "ERROR: #{e.message}"
    warn parser
    exit 2
  end
end
