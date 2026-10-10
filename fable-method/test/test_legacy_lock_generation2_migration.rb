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
    result = migration.run

    refute result.applied
    assert_equal ['LEGACY_TASK_001'], result.plan.task_ids
    assert_equal 2, result.plan.targets.count(&:legacy_present)
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
    preserved = fixture.fetch(:preserved).merge(custom_checkpoint => File.binread(custom_checkpoint))

    result = migration(checkpoint_paths: [custom_checkpoint]).run(
      apply: true,
      confirm_legacy_workers_quiescent: true
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
    assert ExecutionRecord.assert_task_lock_generation!(@repository, 'LEGACY_TASK_001')
    preserved.each { |path, bytes| assert_equal bytes, File.binread(path), path }

    rerun = migration(checkpoint_paths: [custom_checkpoint]).run(
      apply: true,
      confirm_legacy_workers_quiescent: true
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

    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      migration.run(apply: true, confirm_legacy_workers_quiescent: true)
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

  def test_apply_requires_explicit_quiescence_confirmation
    fixture = create_legacy_fixture

    error = assert_raises(LegacyLockGeneration2Migration::Error) do
      migration.run(apply: true)
    end
    assert_includes error.message, '--confirm-legacy-workers-quiescent'
    refute generation_marker_exists?
    assert File.file?(fixture.fetch(:task_lock))
    assert File.file?(fixture.fetch(:checkpoint_lock))
  end

  private

  def migration(checkpoint_paths: [])
    LegacyLockGeneration2Migration.new(
      repo_root: @repository,
      checkpoint_paths: checkpoint_paths
    )
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
