# frozen_string_literal: true

require 'minitest/autorun'
require 'fileutils'
require 'tmpdir'
require 'json'
require 'open3'
require 'rbconfig'
require 'find'
require 'digest'
require 'timeout'
require_relative '../scripts/task_checkpoint'

class LockDeviceAnchorRecoveryTest < Minitest::Test
  SCRIPT = File.expand_path('../scripts/task_checkpoint.rb', __dir__).freeze
  REPOSITORY = File.realpath(File.expand_path('../..', __dir__)).freeze
  TASK_ID = 'synthetic_device_anchor'.freeze
  TEST_ROOT = File.expand_path(ENV.fetch('FABLE_DEVICE_ANCHOR_TEST_ROOT')).freeze
  EVIDENCE_ROOT = File.expand_path(ENV.fetch('FABLE_DEVICE_ANCHOR_EVIDENCE_ROOT')).freeze

  unless File.directory?(TEST_ROOT) && File.realpath(TEST_ROOT) == TEST_ROOT &&
         File.directory?(EVIDENCE_ROOT) && File.realpath(EVIDENCE_ROOT) == EVIDENCE_ROOT &&
         !TEST_ROOT.start_with?("#{REPOSITORY}#{File::SEPARATOR}") &&
         TEST_ROOT.start_with?("#{EVIDENCE_ROOT}#{File::SEPARATOR}")
    raise 'device-anchor test scratch must be canonical, task-scoped, and outside the source repository'
  end

  def setup
    @tmpdir = Dir.mktmpdir('device-anchor-recovery-', TEST_ROOT)
    @repo = File.join(@tmpdir, 'synthetic-repo')
    FileUtils.mkdir_p(@repo)
    git!('init', '--quiet')
    git!('config', 'user.name', 'Fable Synthetic Test')
    git!('config', 'user.email', 'fable-synthetic@example.invalid')
    File.write(File.join(@repo, 'synthetic.txt'), "synthetic anchor fixture\n")
    git!('add', 'synthetic.txt')
    git!('commit', '--quiet', '-m', 'synthetic base')
    StableLockAnchor.initialize_repository_generation!(@repo, confirm_legacy_workers_quiescent: true)
    ExecutionRecord.with_task_lock(@repo, TASK_ID) { |_lock| }
    @lock_path = ExecutionRecord.task_lock_path(@repo, TASK_ID)
    @anchor_dir = File.dirname(@lock_path)
    @authority_path = File.join(@anchor_dir, 'authority.json')
    @manifest = JSON.parse(File.binread(@authority_path))
    skip 'the test filesystem does not expose a stable volume UUID' unless @manifest['volume_identity']
  end

  def teardown
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.exist?(@tmpdir)
  end

  def test_same_device_preflight_and_normal_run_keep_existing_behavior
    authority_before = File.binread(@authority_path)
    result = device_cli('--recover-device-anchor')
    assert_equal 'ALREADY_CURRENT', result.fetch('status')
    assert_equal authority_before, File.binread(@authority_path)

    marker = File.join(@tmpdir, 'normal-run-started')
    status, _stdout, stderr = run_cli('--run', '--repo', @repo, '--worktree', @repo,
                                      '--task-id', TASK_ID, '--execution-id', 'same-device-run', '--',
                                      RbConfig.ruby, '-e', 'File.write(ARGV.fetch(0), "started")', marker)
    assert_equal 0, status, stderr
    assert_equal 'started', File.read(marker)
    assert_equal authority_before, File.binread(@authority_path)
    refute @manifest.key?('lock_device_recovery_history')
  end

  def test_explicit_recovery_preserves_failure_evidence_and_same_lock_identity
    run_id = 'completed-failure'
    status, _stdout, stderr = run_cli('--run', '--repo', @repo, '--worktree', @repo,
                                      '--task-id', TASK_ID, '--execution-id', run_id, '--',
                                      RbConfig.ruby, '-e', 'STDERR.write("synthetic failure\n"); exit 7')
    assert_equal 7, status, stderr
    record_path = ExecutionRecord.default_path(@repo, TASK_ID, run_id)
    capture_path = DurableCommandCapture.default_path(@repo, TASK_ID, run_id)
    record_before = File.binread(record_path)
    capture_before = File.binread(capture_path)
    lock_before = File.binread(@lock_path)
    inode_before = File.stat(@lock_path).ino

    drift_device!
    authority_before = File.binread(@authority_path)
    journal_before = directory_snapshot(File.dirname(record_path))
    preflight = device_cli('--recover-device-anchor')
    assert_equal 'ELIGIBLE_READ_ONLY', preflight.fetch('status')
    assert_equal authority_before, File.binread(@authority_path)
    assert_equal journal_before, directory_snapshot(File.dirname(record_path))

    authorization = File.join(@tmpdir, 'owner-authorization.txt')
    write_private(authorization, preflight.fetch('owner_authorization_text'))
    recovered = device_cli('--recover-device-anchor', '--apply-device-anchor-recovery',
                           '--owner-authorization', authorization)
    assert_equal 'RECOVERED', recovered.fetch('status')
    manifest_after = JSON.parse(File.binread(@authority_path))
    assert_equal File.stat(@lock_path).dev, manifest_after.fetch('lock_device')
    assert_equal inode_before, manifest_after.fetch('lock_inode')
    assert_equal inode_before, File.stat(@lock_path).ino
    assert_equal lock_before, File.binread(@lock_path)
    assert_equal record_before, File.binread(record_path)
    assert_equal capture_before, File.binread(capture_path)
    assert_equal 1, manifest_after.fetch('lock_device_recovery_history').length

    retry_result = device_cli('--recover-device-anchor', '--apply-device-anchor-recovery',
                               '--owner-authorization', authorization)
    assert_equal 'ALREADY_RECOVERED', retry_result.fetch('status')
    assert_equal 1, JSON.parse(File.binread(@authority_path)).fetch('lock_device_recovery_history').length
  end

  def test_missing_historical_volume_identity_refuses_without_writes
    drift_device!
    write_manifest { |manifest| manifest.delete('volume_identity') }
    before = recovery_state_snapshot
    preflight = device_cli('--recover-device-anchor')
    assert_equal 'REFUSED', preflight.fetch('status')
    assert_equal 'HISTORICAL_VOLUME_IDENTITY_MISSING', preflight.fetch('code')
    assert_equal before, recovery_state_snapshot

    authorization = File.join(@tmpdir, 'unused-authorization.txt')
    write_private(authorization, 'not an authorization')
    applied = device_cli('--recover-device-anchor', '--apply-device-anchor-recovery',
                         '--owner-authorization', authorization)
    assert_equal 'REFUSED', applied.fetch('status')
    assert_equal 'HISTORICAL_VOLUME_IDENTITY_MISSING', applied.fetch('code')
    assert_equal before, recovery_state_snapshot
  end

  def test_volume_repo_inode_and_anchor_replacements_refuse_without_recovery_writes
    drift_device!
    cases = {
      'volume identity' => lambda { |manifest| manifest['volume_identity'] = 'macos:VolumeUUID:00000000-0000-0000-0000-000000000001' },
      'repository identity' => lambda { |manifest| manifest['repository'] = File.join(@tmpdir, 'other-repo') },
      'lock inode' => lambda { |manifest| manifest['lock_inode'] += 1 }
    }
    cases.each do |label, mutate|
      restore_anchor_fixture
      drift_device!
      write_manifest(&mutate)
      before = recovery_state_snapshot
      result = device_cli('--recover-device-anchor')
      assert_equal 'REFUSED', result.fetch('status'), label
      assert_equal before, recovery_state_snapshot, label
    end

    restore_anchor_fixture
    drift_device!
    saved_anchor = "#{@anchor_dir}.saved"
    File.rename(@anchor_dir, saved_anchor)
    File.symlink(saved_anchor, @anchor_dir)
    before = recovery_state_snapshot
    result = device_cli('--recover-device-anchor')
    assert_equal 'REFUSED', result.fetch('status'), 'anchor directory symlink'
    assert_equal before, recovery_state_snapshot, 'anchor directory symlink'
  end

  def test_normal_run_rejects_drift_without_launch_or_anchor_rebinding
    drift_device!
    authority_before = File.binread(@authority_path)
    lock_before = File.binread(@lock_path)
    marker = File.join(@tmpdir, 'must-not-start')
    status, _stdout, _stderr = run_cli('--run', '--repo', @repo, '--worktree', @repo,
                                       '--task-id', TASK_ID, '--execution-id', 'drift-run', '--',
                                       RbConfig.ruby, '-e', 'File.write(ARGV.fetch(0), "started")', marker)
    refute_equal 0, status
    refute File.exist?(marker)
    assert_equal authority_before, File.binread(@authority_path)
    assert_equal lock_before, File.binread(@lock_path)
  end

  def test_active_cross_process_lock_and_unknown_prior_writer_both_refuse
    drift_device!
    authorization = authorization_for_current_plan
    authority_before = File.binread(@authority_path)
    ready = File.join(@tmpdir, 'writer-ready')
    release = File.join(@tmpdir, 'writer-release')
    child_source = <<~'RUBY'
      lock = File.open(ARGV.fetch(0), 'r+')
      abort 'writer lock was not acquired' unless lock.flock(File::LOCK_EX | File::LOCK_NB)
      File.write(ARGV.fetch(1), 'locked')
      sleep 0.01 until File.exist?(ARGV.fetch(2))
      lock.close
    RUBY
    pid = Process.spawn(RbConfig.ruby, '-e', child_source, @lock_path, ready, release,
                        out: File::NULL, err: File::NULL)
    begin
      wait_for_file(ready)
      preflight = device_cli('--recover-device-anchor')
      assert_equal 'REFUSED', preflight.fetch('status')
      assert_equal 'ACTIVE_OR_COMPETING_WRITER', preflight.fetch('code')
      applied = device_cli('--recover-device-anchor', '--apply-device-anchor-recovery',
                           '--owner-authorization', authorization)
      assert_equal 'REFUSED', applied.fetch('status')
      assert_equal 'ACTIVE_OR_COMPETING_WRITER', applied.fetch('code')
      assert_equal authority_before, File.binread(@authority_path)
    ensure
      File.write(release, 'release')
      begin
        Process.kill('TERM', pid)
      rescue Errno::ESRCH
        nil
      end
      Process.wait(pid) rescue nil
    end

    stale_dir = File.join(@repo, '.fable', 'checkpoints', TASK_ID, 'executions')
    FileUtils.mkdir_p(stale_dir)
    ExecutionRecord.new(schema_version: 1, task_id: TASK_ID, execution_id: 'unknown-prior',
                        pid: Process.pid + 100_000, status: 'STARTED', durable_capture_path: nil,
                        started_at: Time.now.utc.iso8601, ended_at: nil).save(
                          File.join(stale_dir, 'unknown-prior.json')
                        )
    before = recovery_state_snapshot
    unresolved = device_cli('--recover-device-anchor')
    assert_equal 'REFUSED', unresolved.fetch('status')
    assert_equal 'JOURNAL_WRITER_OR_STATE_UNKNOWN', unresolved.fetch('code')
    assert_equal before, recovery_state_snapshot
  end

  def test_incomplete_journal_and_bad_owner_authorization_refuse_without_writes
    drift_device!
    auth_path = File.join(@tmpdir, 'bad-owner.txt')
    write_private(auth_path, 'wrong target')
    authority_before = File.binread(@authority_path)
    bad_auth = device_cli('--recover-device-anchor', '--apply-device-anchor-recovery',
                          '--owner-authorization', auth_path)
    assert_equal 'REFUSED', bad_auth.fetch('status')
    assert_equal 'OWNER_AUTHORIZATION_MISMATCH', bad_auth.fetch('code')
    assert_equal authority_before, File.binread(@authority_path)

    write_manifest { |manifest| manifest['lock_device_recovery_history'] = [{ 'from_device' => manifest['lock_device'] }] }
    history_before = recovery_state_snapshot
    bad_history = device_cli('--recover-device-anchor')
    assert_equal 'REFUSED', bad_history.fetch('status')
    assert_equal 'RECOVERY_HISTORY_INVALID', bad_history.fetch('code')
    assert_equal history_before, recovery_state_snapshot
    write_manifest { |manifest| manifest.delete('lock_device_recovery_history') }

    original_lineage = File.binread(@lock_path)
    File.binwrite(@lock_path, JSON.generate('schema_version' => 1, 'lock_generation' => 2,
                                            'task_id' => TASK_ID, 'record_root' => File.join(@tmpdir, 'wrong-repo')))
    lineage_before = recovery_state_snapshot
    bad_lineage = device_cli('--recover-device-anchor')
    assert_equal 'REFUSED', bad_lineage.fetch('status')
    assert_equal 'LOCK_LINEAGE_UNVERIFIABLE', bad_lineage.fetch('code')
    assert_equal lineage_before, recovery_state_snapshot
    File.binwrite(@lock_path, original_lineage)

    record_dir = File.join(@repo, '.fable', 'checkpoints', TASK_ID, 'executions')
    FileUtils.mkdir_p(record_dir)
    incomplete = ExecutionRecord.new(schema_version: 1, task_id: TASK_ID, execution_id: 'missing-capture',
                                     pid: Process.pid, status: 'COMPLETED', durable_capture_path: nil,
                                     started_at: Time.now.utc.iso8601, ended_at: Time.now.utc.iso8601)
    incomplete.save(File.join(record_dir, 'missing-capture.json'))
    before = recovery_state_snapshot
    result = device_cli('--recover-device-anchor')
    assert_equal 'REFUSED', result.fetch('status')
    assert_equal 'JOURNAL_WRITER_OR_STATE_UNKNOWN', result.fetch('code')
    assert_equal before, recovery_state_snapshot
  end

  def test_crash_before_and_after_atomic_rename_are_retryable_and_idempotent
    drift_device!
    authorization = authorization_for_current_plan
    old_authority = File.binread(@authority_path)
    old_lock = File.binread(@lock_path)

    crash_at_atomic_rename('before')
    assert_equal old_authority, File.binread(@authority_path)
    assert_equal old_lock, File.binread(@lock_path)
    retried = device_cli('--recover-device-anchor', '--apply-device-anchor-recovery',
                         '--owner-authorization', authorization)
    assert_equal 'RECOVERED', retried.fetch('status')
    assert_equal 1, JSON.parse(File.binread(@authority_path)).fetch('lock_device_recovery_history').length

    restore_anchor_fixture
    drift_device!
    authorization = authorization_for_current_plan
    crash_at_atomic_rename('after')
    after_rename = JSON.parse(File.binread(@authority_path))
    assert_equal File.stat(@lock_path).dev, after_rename.fetch('lock_device')
    assert_equal 1, after_rename.fetch('lock_device_recovery_history').length
    retry_after_rename = device_cli('--recover-device-anchor', '--apply-device-anchor-recovery',
                                    '--owner-authorization', authorization)
    assert_equal 'ALREADY_RECOVERED', retry_after_rename.fetch('status')
    assert_equal 1, JSON.parse(File.binread(@authority_path)).fetch('lock_device_recovery_history').length
  end

  private

  def git!(*args)
    _stdout, stderr, status = Open3.capture3('git', '-C', @repo, *args)
    raise "synthetic Git fixture failed: #{stderr}" unless status.success?
  end

  def run_cli(*args)
    stdout, stderr, status = Open3.capture3(RbConfig.ruby, SCRIPT, *args)
    [status.exitstatus, stdout, stderr]
  end

  def device_cli(*args)
    _status, stdout, stderr = run_cli(*(['--repo', @repo, '--task-id', TASK_ID] + args))
    JSON.parse(stdout)
  rescue JSON::ParserError => e
    flunk "device-anchor CLI did not return JSON: #{e.message}; stdout=#{stdout.inspect}; stderr=#{stderr.inspect}"
  end

  def drift_device!
    write_manifest do |manifest|
      current = File.stat(@lock_path).dev
      manifest['lock_device'] = current == manifest['lock_device'] ? current + 100_000 : manifest['lock_device']
    end
  end

  def write_manifest
    manifest = JSON.parse(File.binread(@authority_path))
    yield manifest
    File.binwrite(@authority_path, JSON.pretty_generate(manifest))
  end

  def restore_anchor_fixture
    FileUtils.rm_rf(@repo)
    FileUtils.mkdir_p(@repo)
    git!('init', '--quiet')
    git!('config', 'user.name', 'Fable Synthetic Test')
    git!('config', 'user.email', 'fable-synthetic@example.invalid')
    File.write(File.join(@repo, 'synthetic.txt'), "synthetic anchor fixture\n")
    git!('add', 'synthetic.txt')
    git!('commit', '--quiet', '-m', 'synthetic base')
    StableLockAnchor.initialize_repository_generation!(@repo, confirm_legacy_workers_quiescent: true)
    ExecutionRecord.with_task_lock(@repo, TASK_ID) { |_lock| }
    @lock_path = ExecutionRecord.task_lock_path(@repo, TASK_ID)
    @anchor_dir = File.dirname(@lock_path)
    @authority_path = File.join(@anchor_dir, 'authority.json')
    @manifest = JSON.parse(File.binread(@authority_path))
  end

  def authorization_for_current_plan
    plan = device_cli('--recover-device-anchor')
    assert_equal 'ELIGIBLE_READ_ONLY', plan.fetch('status')
    path = File.join(@tmpdir, 'owner-authorization.txt')
    write_private(path, plan.fetch('owner_authorization_text'))
    path
  end

  def write_private(path, content)
    File.binwrite(path, content)
    File.chmod(0o600, path)
    path
  end

  def wait_for_file(path)
    Timeout.timeout(10) { sleep 0.01 until File.file?(path) }
  end

  def crash_at_atomic_rename(phase)
    marker = File.join(@tmpdir, "rename-#{phase}-ready")
    hook_loaded = File.join(@tmpdir, "rename-#{phase}-hook-loaded")
    hook = File.join(@tmpdir, "rename-#{phase}-hook.rb")
    hook_source = <<~'RUBY'
      $PROGRAM_NAME = __FILE__
      require ENV.fetch('FABLE_RECOVERY_SOURCE')
      $PROGRAM_NAME = ENV.fetch('FABLE_RECOVERY_SOURCE')
      File.write(ENV.fetch('FABLE_RECOVERY_HOOK_MARKER'), 'loaded')
      class << File
        alias_method :fable_recovery_test_original_rename, :rename
        define_method(:rename) do |source, destination|
          if File.expand_path(destination) == ENV.fetch('FABLE_RECOVERY_AUTHORITY')
            if ENV.fetch('FABLE_RECOVERY_CRASH_PHASE') == 'after'
              fable_recovery_test_original_rename(source, destination)
            end
            File.write(ENV.fetch('FABLE_RECOVERY_CRASH_MARKER'), 'ready')
            sleep 60
          else
            fable_recovery_test_original_rename(source, destination)
          end
        end
      end
    RUBY
    File.write(hook, hook_source)
    env = {
      'FABLE_RECOVERY_SOURCE' => SCRIPT,
      'FABLE_RECOVERY_AUTHORITY' => @authority_path,
      'FABLE_RECOVERY_CRASH_PHASE' => phase,
      'FABLE_RECOVERY_CRASH_MARKER' => marker,
      'FABLE_RECOVERY_HOOK_MARKER' => hook_loaded
    }
    Open3.popen3(env, RbConfig.ruby, "-r#{hook}", SCRIPT, '--recover-device-anchor',
                 '--apply-device-anchor-recovery', '--repo', @repo, '--task-id', TASK_ID,
                 '--owner-authorization', File.join(@tmpdir, 'owner-authorization.txt')) do |stdin, stdout, stderr, wait|
      stdin.close
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 10
      until File.file?(marker)
        if wait.join(0.05)
          flunk "crash hook child exited before rename; status=#{wait.value.exitstatus}; " \
                "hook_loaded=#{File.exist?(hook_loaded)}; stdout=#{stdout.read.inspect}; stderr=#{stderr.read.inspect}"
        end
        flunk "crash hook did not reach rename; hook_loaded=#{File.exist?(hook_loaded)}" if
          Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
      end
      Process.kill('KILL', wait.pid)
      wait.join
      stdout.read
      stderr.read
    end
  end

  def directory_snapshot(directory)
    return {} unless File.directory?(directory)

    Find.find(directory).sort.each_with_object({}) do |path, snapshot|
      next if path == directory || File.directory?(path)

      relative = path.delete_prefix("#{directory}#{File::SEPARATOR}")
      snapshot[relative] = if File.symlink?(path)
                             "SYMLINK:#{File.readlink(path)}"
                           else
                             Digest::SHA256.file(path).hexdigest
                           end
    end
  end

  def recovery_state_snapshot
    {
      'anchor' => directory_snapshot(@anchor_dir),
      'journal' => directory_snapshot(File.join(@repo, '.fable', 'checkpoints', TASK_ID))
    }
  end
end
