# frozen_string_literal: true

require 'minitest/autorun'
require 'fileutils'
require 'json'
require 'open3'
require 'rbconfig'
require 'tmpdir'
require 'timeout'
require_relative '../scripts/migrate_legacy_locks'

class LegacyLockGeneration2MigrationTest < Minitest::Test
  def setup
    @tmpdir = Dir.mktmpdir('legacy_lock_generation2_migration_')
    @repository = File.join(@tmpdir, 'repo')
    initialize_synthetic_git_repo(@repository)
  end

  def teardown
    FileUtils.remove_entry(@tmpdir) if File.directory?(@tmpdir)
  end

  def test_dry_run_lists_legacy_locks_without_writing
    fixture = create_legacy_fixture
    instance = migration
    result = instance.run

    refute result.applied
    assert_equal ['LEGACY_TASK_001'], result.plan.task_ids
    assert_equal 2, result.plan.targets.count(&:legacy_present)
    assert_equal 1, result.plan.targets.count { |target| target.namespace == 'checkpoint-sidecar' }
    assert_match(/\Alegacy-lock-generation2-offline-migration:[0-9a-f]{64}\z/, instance.authorization_target(result.plan))
    assert File.file?(fixture.fetch(:task_lock))
    assert File.file?(fixture.fetch(:checkpoint_lock))
    refute generation_marker_exists?
    refute File.exist?(ExecutionRecord.task_lock_path(@repository, 'LEGACY_TASK_001'))
  end

  def test_apply_migrates_anchors_and_preserves_checkpoint_history_and_captures
    fixture = create_legacy_fixture
    custom_checkpoint = File.join(@repository, 'custom', 'checkpoint.json')
    FileUtils.mkdir_p(File.dirname(custom_checkpoint))
    File.write(custom_checkpoint, '{"custom":"checkpoint"}')
    custom_lock = "#{custom_checkpoint}.lock"
    File.write(custom_lock, 'legacy custom checkpoint lock')
    checkpoint_without_sidecar = File.join(@repository, 'custom', 'checkpoint-without-sidecar.json')
    File.write(checkpoint_without_sidecar, '{"custom":"checkpoint without sidecar"}')
    preserved = fixture.fetch(:preserved).merge(
      custom_checkpoint => File.binread(custom_checkpoint),
      checkpoint_without_sidecar => File.binread(checkpoint_without_sidecar)
    )
    instance = migration(checkpoint_paths: [custom_checkpoint, checkpoint_without_sidecar])
    authorization_path = owner_authorization_file(instance)

    result = instance.run(
      apply: true,
      owner_authorization_path: authorization_path
    )

    assert result.applied
    assert generation_marker_exists?
    assert_equal JSON.parse(File.read(generation_marker_path)).fetch('generation'), StableLockAnchor::GENERATION
    refute File.exist?(fixture.fetch(:task_lock))
    refute File.exist?(fixture.fetch(:checkpoint_lock))
    refute File.exist?(custom_lock)
    assert File.file?(ExecutionRecord.task_lock_path(@repository, 'LEGACY_TASK_001'))
    assert File.file?(TaskCheckpoint.lock_path_for(fixture.fetch(:checkpoint)))
    assert File.file?(TaskCheckpoint.lock_path_for(custom_checkpoint))
    assert File.file?(TaskCheckpoint.lock_path_for(checkpoint_without_sidecar))
    assert ExecutionRecord.assert_task_lock_generation!(@repository, 'LEGACY_TASK_001')
    preserved.each { |path, bytes| assert_equal bytes, File.binread(path), path }

    rerun = migration(checkpoint_paths: [custom_checkpoint, checkpoint_without_sidecar]).run(
      apply: true,
      owner_authorization_path: authorization_path
    )
    assert rerun.applied
    preserved.each { |path, bytes| assert_equal bytes, File.binread(path), path }
  end

  def test_active_legacy_lock_fails_before_marker_or_anchor_writes
    fixture = create_legacy_fixture
    child_code = <<~'RUBY'
      file = File.open(ARGV.fetch(0), 'r+')
      abort 'could not acquire fixture lock' unless file.flock(File::LOCK_EX)
      puts 'ready'
      STDOUT.flush
      STDIN.gets
      file.close
    RUBY
    stdin, stdout, _stderr, wait_thread = Open3.popen3(
      RbConfig.ruby, '-e', child_code, fixture.fetch(:task_lock)
    )
    assert_equal "ready\n", Timeout.timeout(5) { stdout.gets }
    instance = migration
    authorization_path = owner_authorization_file(instance)

    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      instance.run(apply: true, owner_authorization_path: authorization_path)
    end
    assert_includes error.message, 'is active'
    refute generation_marker_exists?
    assert File.file?(fixture.fetch(:task_lock))
    assert File.file?(fixture.fetch(:checkpoint_lock))
    refute File.exist?(ExecutionRecord.task_lock_path(@repository, 'LEGACY_TASK_001'))
  ensure
    stdin&.write("release\n") unless stdin&.closed?
    stdin&.close unless stdin&.closed?
    Timeout.timeout(5) { wait_thread.value } if wait_thread
    stdout&.close unless stdout&.closed?
  end

  def test_apply_requires_exact_current_owner_authorization
    fixture = create_legacy_fixture
    instance = migration

    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      instance.run(apply: true)
    end
    assert_includes error.message, '--owner-authorization'
    refute generation_marker_exists?
    assert File.file?(fixture.fetch(:task_lock))
    assert File.file?(fixture.fetch(:checkpoint_lock))

    authorization_path = owner_authorization_file(instance, target: 'another-repository')
    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      instance.run(apply: true, owner_authorization_path: authorization_path)
    end
    assert_includes error.message, 'does not exactly bind'
    refute generation_marker_exists?

    authorization_path = owner_authorization_file(instance, action: 'OTHER_ACTION')
    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      instance.run(apply: true, owner_authorization_path: authorization_path)
    end
    assert_includes error.message, 'does not exactly bind'
    refute generation_marker_exists?

    authorization_path = owner_authorization_file(instance, provenance: :quoted)
    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      instance.run(apply: true, owner_authorization_path: authorization_path)
    end
    assert_includes error.message, 'does not exactly bind'
    refute generation_marker_exists?

    authorization_path = owner_authorization_file(instance)
    File.open(authorization_path, 'a') { |file| file.puts 'AUTHORIZED_ACTION: EXTRA_ACTION' }
    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      instance.run(apply: true, owner_authorization_path: authorization_path)
    end
    assert_includes error.message, 'unbound or ambiguous'
    refute generation_marker_exists?
  end

  def test_apply_requires_quiescence_attestation_before_writing
    fixture = create_legacy_fixture
    instance = migration
    authorization_path = owner_authorization_file(instance, quiescent: false)

    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      instance.run(apply: true, owner_authorization_path: authorization_path)
    end

    assert_includes error.message, 'quiescence attestation'
    refute generation_marker_exists?
    assert File.file?(fixture.fetch(:task_lock))
    assert File.file?(fixture.fetch(:checkpoint_lock))

    authorization_path = owner_authorization_file(instance)
    File.open(authorization_path, 'a') do |file|
      file.puts 'LEGACY_WORKERS_AND_INHERITED_CHILDREN_QUIESCENT: NO'
    end
    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      instance.run(apply: true, owner_authorization_path: authorization_path)
    end

    assert_includes error.message, 'unambiguous positive quiescence attestation'
    refute generation_marker_exists?
    assert File.file?(fixture.fetch(:task_lock))
    assert File.file?(fixture.fetch(:checkpoint_lock))
  end

  def test_inherited_child_keeps_legacy_lock_active_after_parent_closes
    fixture = create_legacy_fixture
    parent_lock = File.open(fixture.fetch(:task_lock), 'r+')
    assert parent_lock.flock(File::LOCK_EX | File::LOCK_NB)
    parent_lock.close_on_exec = false
    child_code = <<~'RUBY'
      file = File.for_fd(Integer(ARGV.fetch(0)), autoclose: false)
      puts 'ready'
      STDOUT.flush
      STDIN.gets
      file.close
    RUBY
    stdin, stdout, _stderr, wait_thread = Open3.popen3(
      RbConfig.ruby, '-e', child_code, parent_lock.fileno.to_s, close_others: false
    )
    assert_equal "ready\n", Timeout.timeout(5) { stdout.gets }
    parent_lock.close

    instance = migration
    authorization_path = owner_authorization_file(instance)
    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      instance.run(apply: true, owner_authorization_path: authorization_path)
    end
    assert_includes error.message, 'is active'
    refute generation_marker_exists?
    assert File.file?(fixture.fetch(:task_lock))

    stdin.write("release\n")
    stdin.close
    Timeout.timeout(5) { wait_thread.value }
    stdout.close
    assert instance.run(apply: true, owner_authorization_path: authorization_path).applied
  ensure
    parent_lock&.close unless parent_lock&.closed?
    stdin&.write("release\n") unless stdin&.closed?
    stdin&.close unless stdin&.closed?
    Timeout.timeout(5) { wait_thread.value } if wait_thread && !wait_thread.join(0)
    stdout&.close unless stdout&.closed?
  end

  def test_partial_migration_resumes_with_same_exact_authorization
    fixture = create_legacy_fixture
    instance = migration
    authorization_path = owner_authorization_file(instance)
    target = instance.authorization_target(instance.run.plan)
    interrupted = migration
    sync_count = 0
    interrupted.define_singleton_method(:sync_directory!) do |path|
      sync_count += 1
      raise Errno::EIO, 'synthetic interruption after legacy unlink' if sync_count == 1

      File.open(path, File::RDONLY) { |directory| directory.fsync }
    end

    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      interrupted.run(apply: true, owner_authorization_path: authorization_path)
    end
    assert_includes error.message, 'synthetic interruption'
    assert generation_marker_exists?
    refute File.exist?(fixture.fetch(:task_lock))
    assert File.file?(fixture.fetch(:checkpoint_lock))

    resumed = migration
    assert_equal target, resumed.authorization_target(resumed.run.plan)
    result = resumed.run(apply: true, owner_authorization_path: authorization_path)

    assert result.applied
    refute File.exist?(fixture.fetch(:checkpoint_lock))
    assert File.file?(ExecutionRecord.task_lock_path(@repository, 'LEGACY_TASK_001'))
    assert File.file?(TaskCheckpoint.lock_path_for(fixture.fetch(:checkpoint)))
  ensure
    fixture&.fetch(:preserved, {})&.each do |path, bytes|
      assert_equal bytes, File.binread(path), path
    end
  end

  private

  def migration(checkpoint_paths: [])
    LegacyLockGeneration2Migration.new(
      repo_root: @repository,
      checkpoint_paths: checkpoint_paths
    )
  end

  def owner_authorization_file(instance, target: nil, action: LegacyLockGeneration2Migration::AUTHORIZATION_ACTION,
                               provenance: :direct, quiescent: true)
    target ||= instance.authorization_target(instance.run.plan)
    lines = [
      'OWNER_DIRECT_PACKET_AUTHORIZATION: YES',
      'AUTHORIZATION_HANDOFF_MODE: OWNER_DIRECT_PACKET',
      'OWNER_ACTION_AUTHORIZATION: PRESENT_IN_CURRENT_OWNER_MESSAGE',
      'AUTHORIZATION_EVIDENCE: CURRENT_OWNER_USER_MESSAGE',
      "AUTHORIZED_ACTION: #{action}; AUTHORIZED_TARGET: #{target}"
    ]
    lines << 'AUTHORIZATION_SOURCE: QUOTED_IN_PACKET_OR_HANDOFF' if provenance == :quoted
    lines << LegacyLockGeneration2Migration::QUIESCENCE_ATTESTATION if quiescent
    path = File.join(@tmpdir, 'owner-authorization.txt')
    File.write(path, "#{lines.join("\n")}\n")
    File.chmod(0o600, path)
    path
  end

  def initialize_synthetic_git_repo(path)
    FileUtils.mkdir_p(path)
    git(path, 'init', '--quiet')
    git(path, 'config', 'user.name', 'Fable Test')
    git(path, 'config', 'user.email', 'fable-test@example.invalid')
    File.write(File.join(path, '.synthetic-test-fixture'), "synthetic\n")
    git(path, 'add', '.synthetic-test-fixture')
    git(path, 'commit', '--quiet', '-m', 'synthetic base')
  end

  def git(path, *args)
    _stdout, stderr, status = Open3.capture3('git', '-C', path, *args)
    raise "synthetic Git command failed: #{stderr}" unless status.success?
  end

  def create_legacy_fixture
    task_id = 'LEGACY_TASK_001'
    task_dir = File.join(@repository, '.fable', 'checkpoints', task_id)
    execution_dir = File.join(task_dir, 'executions')
    capture_dir = File.join(task_dir, 'captures')
    FileUtils.mkdir_p(execution_dir)
    FileUtils.mkdir_p(capture_dir)
    task_lock = File.join(task_dir, 'execution.lock')
    File.write(task_lock, 'legacy task lock')

    record = File.join(execution_dir, 'execution-001.json')
    capture = File.join(capture_dir, 'capture-001.bin')
    File.write(record, '{"execution_id":"execution-001","status":"COMPLETED"}')
    File.binwrite(capture, "\x00synthetic capture\xff".b)

    checkpoint = File.join(@repository, '.fable', 'checkpoints', "#{task_id}.json")
    checkpoint_payload = JSON.generate(
      'schema_version' => 1,
      'task_id' => task_id,
      'repository' => @repository,
      'worktree' => @repository,
      'authoritative_packet_ref' => 'synthetic/packet.md',
      'branch' => 'synthetic',
      'current_head' => 'a' * 40,
      'current_tree' => 'b' * 40,
      'task_lifecycle_state' => 'COMPLETED',
      'next_action' => 'none',
      'authorization_boundary' => 'synthetic fixture',
      'updated_at' => Time.now.utc.iso8601,
      'revision' => 1
    )
    File.write(checkpoint, checkpoint_payload)
    checkpoint_lock = "#{checkpoint}.lock"
    File.write(checkpoint_lock, 'legacy checkpoint sidecar')

    {
      task_lock: task_lock,
      checkpoint: checkpoint,
      checkpoint_lock: checkpoint_lock,
      preserved: {
        record => File.binread(record),
        capture => File.binread(capture),
        checkpoint => File.binread(checkpoint)
      }
    }
  end

  def generation_marker_path
    common_dir, status = Open3.capture2(
      'git', '-C', @repository, 'rev-parse', '--git-common-dir'
    )
    raise 'synthetic Git common directory could not be resolved' unless status.success?

    common = File.realpath(File.expand_path(common_dir.strip, @repository))
    File.join(common, 'fable-locks', StableLockAnchor::REPOSITORY_GENERATION_FILENAME)
  end

  def generation_marker_exists?
    File.file?(generation_marker_path)
  end
end
