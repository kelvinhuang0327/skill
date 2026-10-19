# frozen_string_literal: true

require 'minitest/autorun'
require 'fileutils'
require 'json'
require 'open3'
require 'rbconfig'
require 'tmpdir'
require 'timeout'
require_relative '../scripts/task_checkpoint'

module HeavyScientificLeaseTestScratch
  ROOT = File.expand_path('../../.task-disposable-scratch', __dir__).freeze

  requested_root = ENV.fetch('FABLE_TEST_SCRATCH_ROOT') do
    raise 'FABLE_TEST_SCRATCH_ROOT must name this worktree disposable root'
  end
  unless File.expand_path(requested_root) == ROOT && File.directory?(ROOT) && File.realpath(ROOT) == ROOT
    raise 'test scratch root is not the canonical worktree disposable root'
  end
  ENV['TMPDIR'] = ROOT
  ENV['GIT_CEILING_DIRECTORIES'] = ROOT

  BOUNDARY_SHIM = File.join(ROOT, 'git-discovery-boundary.rb').freeze
  File.write(BOUNDARY_SHIM, <<~'RUBY')
    class StableLockAnchor
      module DisposableScratchBoundary
        def git_marker_in_ancestors?(path)
          boundary = File.realpath(ENV.fetch('FABLE_TEST_SCRATCH_ROOT'))
          current = File.expand_path(path.to_s)
          loop do
            return false if current == boundary
            marker = File.join(current, '.git')
            return true if File.exist?(marker) || File.symlink?(marker)
            parent = File.dirname(current)
            return false if parent == current
            current = parent
          end
        end
      end
      singleton_class.prepend(DisposableScratchBoundary)
    end
  RUBY
  require BOUNDARY_SHIM
  ENV['RUBYOPT'] = [ENV['RUBYOPT'].to_s, "-r#{BOUNDARY_SHIM}"].reject(&:empty?).join(' ')
  Minitest.after_run { FileUtils.rm_f(BOUNDARY_SHIM) }

  def self.mktmpdir(prefix)
    Dir.mktmpdir(prefix, ROOT)
  end
end

class HeavyScientificLeaseTest < Minitest::Test
  def setup
    @tmpdir = HeavyScientificLeaseTestScratch.mktmpdir('heavy_scientific_lease_test_')
    @repo = File.join(@tmpdir, 'repo')
    @worktree = File.join(@tmpdir, 'worktree')
    FileUtils.mkdir_p([@repo, @worktree])
    @lock_path = File.join(@tmpdir, 'machine-heavy-slot.lock')
    @source_path = ENV['FABLE_HEAVY_TEST_SOURCE'] || File.expand_path('../scripts/task_checkpoint.rb', __dir__)
    @children = []
  end

  def teardown
    @children.each do |pid|
      begin
        Process.kill('TERM', pid) if Process.kill(0, pid)
      rescue Errno::ESRCH
        nil
      end
      begin
        Process.wait(pid)
      rescue Errno::ECHILD
        nil
      end
    end
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.directory?(@tmpdir)
  end

  def lease(task_id: 'FAKE_HEAVY', execution_id: 'run1', checker: -> { [] }, lock_path: @lock_path)
    HeavyScientificLease.acquire!(task_id: task_id, execution_id: execution_id,
                                  worktree: @worktree, record_root: @repo,
                                  run_root: File.join(@worktree, '.task-data', task_id, execution_id),
                                  lock_path: lock_path, legacy_writer_checker: checker)
  end

  def test_concurrent_fake_protected_launches_exclude_exactly_one_and_reacquire_after_owner_terminates
    events = File.join(@tmpdir, 'events.txt')
    barrier = File.join(@tmpdir, 'acquire-barrier')
    first_marker = File.join(@tmpdir, 'first-child.pid')
    second_marker = File.join(@tmpdir, 'second-child.pid')
    first_pid = start_fake_launcher(events, first_marker, 'hold', barrier: barrier, role: 'first')
    @debug_pid = first_pid
    wait_until("first fake launch did not reach the protected acquisition boundary: #{launcher_diagnostics(first_marker)}") do
      File.file?("#{barrier}.first.ready")
    end

    second_pid = start_fake_launcher(events, second_marker, 'quick', barrier: barrier, role: 'second')
    @debug_pid = second_pid
    second_ready = File.join(@tmpdir, 'acquire-barrier.second.ready')
    wait_until("second fake launch neither stopped nor reached the acquisition boundary: #{launcher_diagnostics(second_marker)}") do
      File.file?(second_ready) ||
        (File.file?(events) && File.readlines(events, chomp: true).include?('STOPPED_HEAVY_SLOT_OWNED'))
    end
    @debug_pid = nil

    second_entered_critical_section = File.file?(second_ready)
    File.write("#{barrier}.release", 'go')
    _, second_status = Process.wait2(second_pid)
    @children.delete(second_pid)
    assert_equal 3, second_status.exitstatus
    @debug_pid = first_pid
    wait_until('first fake scientific child did not start after the lease was granted') { File.file?(first_marker) }
    @debug_pid = nil
    assert_equal ['LEASE_ACQUIRED / WOULD_LAUNCH', 'STOPPED_HEAVY_SLOT_OWNED'].sort,
                 File.readlines(events, chomp: true).sort
    refute second_entered_critical_section, 'the contender must stop before final writer validation'
    assert File.file?(first_marker)
    refute File.exist?(second_marker), 'the blocked launcher must not start its fake child'

    Process.kill('TERM', first_pid)
    _, first_status = Process.wait2(first_pid)
    @children.delete(first_pid)
    assert first_status.signaled?, 'the first fake owner should terminate after forwarding TERM to its child'

    fresh_events = File.join(@tmpdir, 'fresh-events.txt')
    fresh_marker = File.join(@tmpdir, 'fresh-child.pid')
    fresh_pid = start_fake_launcher(fresh_events, fresh_marker, 'quick')
    _, fresh_status = Process.wait2(fresh_pid)
    @children.delete(fresh_pid)
    assert_equal 0, fresh_status.exitstatus
    assert_equal ['LEASE_ACQUIRED / WOULD_LAUNCH'], File.readlines(fresh_events, chomp: true)
    assert File.file?(fresh_marker)
  end

  def test_child_inherits_the_lease_until_its_own_terminal_state
    owner = lease
    child = Process.spawn(RbConfig.ruby, '-e', 'sleep 30', owner.lock.fileno => owner.lock.fileno)
    @children << child

    owner.release!
    assert_raises(HeavyScientificLease::SlotOwned) { lease(execution_id: 'contender') }

    Process.kill('TERM', child)
    Process.wait(child)
    @children.delete(child)
    next_owner = lease(execution_id: 'after-child-terminal')
    assert next_owner.owned?
    next_owner.release!
  end

  def test_stale_owner_is_reclaimed_only_after_recorded_pid_is_dead
    stale_pid = Process.spawn(RbConfig.ruby, '-e', 'sleep 30')
    @children << stale_pid
    start_identity = HeavyScientificLease.process_start_identity(stale_pid)
    refute_nil start_identity
    Process.kill('TERM', stale_pid)
    Process.wait(stale_pid)
    @children.delete(stale_pid)

    write_metadata('TASK_ID' => 'OLD_TASK', 'EXECUTION_ID' => 'old-run', 'PID' => stale_pid,
                   'PROCESS_START_IDENTITY' => start_identity, 'WORKTREE' => @worktree,
                   'RUN_ROOT' => File.join(@worktree, 'old-run'), 'ACQUIRED_AT' => Time.now.utc.iso8601)
    recovered = lease(task_id: 'NEW_TASK', execution_id: 'new-run')
    assert recovered.owned?
    assert_equal 'NEW_TASK', JSON.parse(File.read(metadata_path)).fetch('TASK_ID')
    recovered.release!
  end

  def test_live_metadata_owner_fails_closed_and_is_not_replaced
    live_pid = Process.spawn(RbConfig.ruby, '-e', 'sleep 30')
    @children << live_pid
    start_identity = HeavyScientificLease.process_start_identity(live_pid)
    refute_nil start_identity
    original = {
      'TASK_ID' => 'LIVE_TASK', 'EXECUTION_ID' => 'live-run', 'PID' => live_pid,
      'PROCESS_START_IDENTITY' => start_identity, 'WORKTREE' => @worktree,
      'RUN_ROOT' => File.join(@worktree, 'live-run'), 'ACQUIRED_AT' => Time.now.utc.iso8601
    }
    write_metadata(original)

    error = assert_raises(HeavyScientificLease::SlotOwned) do
      lease(task_id: 'NEW_TASK', execution_id: 'blocked-run')
    end
    assert_equal 'STOPPED_HEAVY_SLOT_OWNED', error.message
    assert_equal original, JSON.parse(File.read(metadata_path))

    Process.kill('TERM', live_pid)
    Process.wait(live_pid)
    @children.delete(live_pid)
    recovered = lease(task_id: 'NEW_TASK', execution_id: 'after-live-owner')
    assert recovered.owned?
    recovered.release!
  end

  def test_nested_same_owner_reuses_the_inherited_lease_without_a_second_acquire
    owner = lease(task_id: 'NESTED_TASK', execution_id: 'outer-run')
    nested = HeavyScientificLease.verify_nested!(
      request: owner.capability_env, repo_root: @repo, task_id: 'NESTED_TASK',
      execution_id: 'inner-run', ancestor_execution_ids: ['outer-run'], lock_path: @lock_path
    )

    refute nested.owned?
    assert_equal owner.lock.fileno, nested.lock.fileno
    assert_equal owner.capability_env, nested.capability_env
    nested.release!
    assert_raises(HeavyScientificLease::SlotOwned) { lease(task_id: 'NESTED_TASK', execution_id: 'independent') }
    owner.release!
  end

  def test_protected_child_inherits_task_and_scientific_locks_on_distinct_descriptors
    task_lock_path = File.join(@tmpdir, 'task.lock')
    task_lock = File.open(task_lock_path, File::RDWR | File::CREAT, 0o600)
    owner = lease(task_id: 'NESTED_TASK', execution_id: 'outer-run')
    expected_task_identity = [task_lock.stat.dev, task_lock.stat.ino].join(':')
    child = <<~'RUBY'
      repo_root, lock_path, expected_task_identity = ARGV
      task_lock = File.for_fd(10, autoclose: false)
      abort 'task lock descriptor identity mismatch' unless
        [task_lock.stat.dev, task_lock.stat.ino].join(':') == expected_task_identity
      request = HeavyScientificLease.nested_capability_request(ENV)
      nested = HeavyScientificLease.verify_nested!(
        request: request, repo_root: repo_root, task_id: 'NESTED_TASK',
        execution_id: 'inner-run', ancestor_execution_ids: ['outer-run'], lock_path: lock_path
      )
      abort 'lease descriptor was not inherited on FD 11' unless nested.lock.fileno == 11 && !nested.owned?
      nested.release!
      puts 'TWO_LOCKS_OK'
    RUBY

    capture = DurableCommandCapture.run_and_capture(
      [RbConfig.ruby, '-r', @source_path, '-e', child, @repo, @lock_path, expected_task_identity],
      file_path: File.join(@tmpdir, 'two-lock-capture.json'),
      inherited_locks: [task_lock, owner.lock],
      child_env: owner.capability_env(child_fd: 11)
    )

    assert_equal 0, capture.exit_status, capture.stderr
    assert_equal "TWO_LOCKS_OK\n", capture.stdout
  ensure
    owner.release! if owner && owner.owned?
    task_lock.close unless task_lock.nil? || task_lock.closed?
  end

  def test_legacy_writer_guard_stops_once_without_polling
    calls = 0
    writer = { 'PID' => 42, 'TASK_ID' => 'LEGACY_TASK' }
    error = assert_raises(HeavyScientificLease::LegacyWriterActive) do
      lease(checker: lambda {
        calls += 1
        [writer]
      })
    end

    assert_equal [writer], error.writers
    assert_equal 1, calls
    refute File.exist?(metadata_path)
    recovered = lease(execution_id: 'after-stop-once')
    assert recovered.owned?
    recovered.release!
  end

  private

  def metadata_path
    HeavyScientificLease.default_metadata_path(@lock_path)
  end

  def write_metadata(value)
    File.open(metadata_path, 'w', 0o600) { |file| file.write(JSON.generate(value)) }
  end

  def start_fake_launcher(events, child_marker, action, barrier: nil, role: nil)
    runner = <<~'RUBY'
      source, lock_path, events, marker, action, startup, barrier, role = ARGV
      File.write(startup, Process.pid.to_s)
      require source
      owner = nil
      begin
        task_id = "SYNTHETIC_HEAVY_#{Process.pid}"
        owner = HeavyScientificLease.acquire!(
          task_id: task_id, execution_id: 'run1', worktree: Dir.pwd,
          record_root: Dir.pwd, run_root: Dir.pwd, lock_path: lock_path,
          legacy_writer_checker: lambda {
            unless barrier == '-'
              File.write("#{barrier}.#{role}.ready", Process.pid.to_s)
              deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 10
              until File.file?("#{barrier}.release")
                abort 'synthetic acquisition barrier timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
                sleep 0.01
              end
            end
            []
          }
        )
        File.open(events, 'a') { |file| file.puts 'LEASE_ACQUIRED / WOULD_LAUNCH' }
        command = if action == 'hold'
                    [RbConfig.ruby, '-e', 'File.write(ARGV.fetch(0), Process.pid.to_s); sleep 30', marker]
                  else
                    [RbConfig.ruby, '-e', 'File.write(ARGV.fetch(0), Process.pid.to_s); exit 0', marker]
                  end
        capture = DurableCommandCapture.run_and_capture(command, file_path: "#{marker}.capture.json",
                                                        inherited_locks: [owner.lock])
        if capture.wrapper_signal
          Signal.trap(capture.wrapper_signal, 'SYSTEM_DEFAULT')
          Process.kill(capture.wrapper_signal, Process.pid)
        end
        exit 0
      rescue HeavyScientificLease::SlotOwned
        File.open(events, 'a') { |file| file.puts 'STOPPED_HEAVY_SLOT_OWNED' }
        exit 3
      ensure
        owner.release! if owner && owner.owned?
      end
    RUBY
    barrier ||= '-'
    role ||= '-'
    pid = Process.spawn(RbConfig.ruby, '-e', runner, '--', @source_path, @lock_path, events,
                        child_marker, action, "#{child_marker}.started", barrier, role,
                        out: "#{child_marker}.stdout",
                        err: "#{child_marker}.stderr", pgroup: true)
    @children << pid
    pid
  end

  def wait_until(message, timeout: 5)
    Timeout.timeout(timeout) do
      sleep 0.01 until yield
    end
  rescue Timeout::Error
    process, = Open3.capture2('/bin/ps', '-ww', '-p', @debug_pid.to_s, '-o', 'pid=,command=') if @debug_pid
    flunk "#{message} pid=#{@debug_pid} process=#{process.inspect}"
  end

  def launcher_diagnostics(child_marker)
    stdout_path = "#{child_marker}.stdout"
    stderr_path = "#{child_marker}.stderr"
    startup_path = "#{child_marker}.started"
    stdout = File.file?(stdout_path) ? File.read(stdout_path) : ''
    stderr = File.file?(stderr_path) ? File.read(stderr_path) : ''
    startup = File.file?(startup_path) ? File.read(startup_path) : ''
    events_path = File.join(@tmpdir, 'events.txt')
    events = File.file?(events_path) ? File.read(events_path) : ''
    "startup=#{startup.inspect} stdout=#{stdout.inspect} stderr=#{stderr.inspect} events=#{events.inspect}"
  end
end
