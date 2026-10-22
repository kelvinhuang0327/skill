# frozen_string_literal: true

require 'minitest/autorun'
require 'fileutils'
require 'tmpdir'
require 'json'
require 'open3'
require 'rbconfig'
require 'timeout'
require_relative '../scripts/task_checkpoint'

module TaskCheckpointTestScratch
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

module SyntheticGitRepo
  def initialize_synthetic_git_repo(path, lock_generation: true)
    FileUtils.mkdir_p(path)
    git = lambda do |*args|
      _stdout, stderr, status = Open3.capture3('git', '-C', path, *args)
      raise "synthetic git command failed: git #{args.join(' ')}: #{stderr}" unless status.success?
    end

    git.call('init', '--quiet')
    git.call('config', 'user.name', 'Fable Test')
    git.call('config', 'user.email', 'fable-test@example.invalid')
    File.write(File.join(path, '.synthetic-test-fixture'), "synthetic\n")
    git.call('add', '.synthetic-test-fixture')
    git.call('commit', '--quiet', '-m', 'synthetic base')
    if lock_generation
      StableLockAnchor.initialize_repository_generation!(path, confirm_legacy_workers_quiescent: true)
    end
  end
end

class TaskCheckpointTest < Minitest::Test
  include SyntheticGitRepo

  def setup
    @tmpdir = TaskCheckpointTestScratch.mktmpdir('task_checkpoint_test_')
    @repo_dir = File.join(@tmpdir, 'repo')
    @worktree_dir = File.join(@tmpdir, 'worktree')
    FileUtils.mkdir_p(@repo_dir)
    FileUtils.mkdir_p(@worktree_dir)
    FileUtils.mkdir_p(File.join(@repo_dir, 'prompt'))
    File.write(
      File.join(@repo_dir, 'prompt', 'Personal_Planner_Handoff_Prompt_v5.4_Lean_Final.md'),
      "# Task 001 Authoritative Packet\nGoal: Resumable Checkpoints\n"
    )
    @task_b_packet_ref = 'prompt/TASK_B_002.md'
    File.write(
      File.join(@repo_dir, @task_b_packet_ref),
      "# Executable Owner-authorized Packet\nTask: TASK_B_002\nIndependent: YES\n"
    )

    @valid_attrs = {
      task_id: 'TEST_TASK_001',
      repository: @repo_dir,
      worktree: @worktree_dir,
      authoritative_packet_ref: 'prompt/Personal_Planner_Handoff_Prompt_v5.4_Lean_Final.md#task-001',
      branch: 'master',
      current_head: 'c0ffee1234567890abcdef1234567890abcdef12',
      current_tree: 'tree1234567890abcdef1234567890abcdef1234',
      task_lifecycle_state: 'IN_PROGRESS',
      current_blocker: nil,
      next_action: 'implement_bounded_reconciliation',
      authorization_boundary: 'NONE',
      pr_state: 'NONE',
      pr_number: nil,
      pr_url: nil,
      updated_at: Time.now.utc.iso8601,
      revision: 1
    }
  end

  def teardown
    (@save_children || []).each do |child|
      Process.kill('TERM', child[:wait].pid) if child[:wait].alive?
      assert child[:wait].join(5), 'test-owned save process did not terminate'
      child[:input].close unless child[:input].closed?
      child[:readers].each { |reader| assert reader.join(5), 'save pipe did not close' }
    end
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.directory?(@tmpdir)
  end

  def deferred_task_a(existing_deferred_checkpoints: [])
    cp = TaskCheckpoint.new(@valid_attrs)
    cp.defer_for_authorized_task!(
      blocker: 'transient external service outage',
      blocker_disposition: 'TRANSIENT_ELIGIBLE',
      resume_after_task_id: 'TASK_B_002',
      next_authorized_task_packet_ref: @task_b_packet_ref,
      task_b_independent: true,
      task_b_packet_authorized: true,
      existing_deferred_checkpoints: existing_deferred_checkpoints
    )
    cp
  end

  def task_b_checkpoint(state: 'COMPLETED', task_id: 'TASK_B_002', packet_ref: @task_b_packet_ref)
    TaskCheckpoint.new(@valid_attrs.merge(
      task_id: task_id,
      authoritative_packet_ref: packet_ref,
      task_lifecycle_state: state,
      current_blocker: state == 'BLOCKED' ? 'Task B terminal blocker' : nil,
      next_action: 'TASK_B_TERMINAL_HANDOFF'
    ))
  end

  def live_reconciliation_options(extra = {})
    {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree]
    }.merge(extra)
  end

  def direct_owner_authorization_token(action:, target:, handoff_mode: 'OWNER_DIRECT_PACKET',
                                       owner_auth: 'PRESENT_IN_CURRENT_OWNER_MESSAGE',
                                       evidence: 'CURRENT_OWNER_USER_MESSAGE', include_packet: true)
    packet = if include_packet
               <<~PACKET
                 /fable-method

                 MODE: WORKER_EXECUTION

                 [Executable Worker Task — TEST_TASK_001]
               PACKET
             else
               ''
             end

    <<~TEXT
      OWNER AUTHORIZATION — TEST_TASK_001

      I authorize exactly:
      - ACTION=#{action}; TARGET=#{target}

      Not authorized:
      - PUSH

      AUTHORIZATION_TOKEN:
      TEST_TASK_001

      #{packet}OWNER_ACTION_AUTHORIZATION:
      #{owner_auth}

      AUTHORIZATION_HANDOFF_MODE:
      #{handoff_mode}

      AUTHORIZATION_EVIDENCE:
      #{evidence}

      AUTHORIZED_ACTION_SCOPE:
      ACTION=#{action}; TARGET=#{target}

      SEPARATE_AUTHORIZATION_ONLY_MESSAGE_REQUIRED:
      NO
    TEXT
  end

  def multi_action_owner_authorization_token
    <<~TEXT
      OWNER AUTHORIZATION — TEST_TASK_001

      I authorize exactly:
      - ACTION=CLOSE_PR; TARGET=kelvinhuang0327/skill#46
      - ACTION=MERGE_PR; TARGET=kelvinhuang0327/skill#48

      Not authorized:
      - PUSH

      AUTHORIZATION_TOKEN:
      TEST_TASK_001

      OWNER_ACTION_AUTHORIZATION:
      PRESENT_IN_CURRENT_OWNER_MESSAGE

      AUTHORIZATION_HANDOFF_MODE:
      OWNER_DIRECT_PACKET

      AUTHORIZATION_EVIDENCE:
      CURRENT_OWNER_USER_MESSAGE

      AUTHORIZED_ACTION_SCOPE:
      ACTION=CLOSE_PR; TARGET=kelvinhuang0327/skill#46
      ACTION=MERGE_PR; TARGET=kelvinhuang0327/skill#48

      SEPARATE_AUTHORIZATION_ONLY_MESSAGE_REQUIRED:
      NO
    TEXT
  end

  def test_sync_guard_rejects_fake_git_from_caller_path
    source_fable_root = File.expand_path('..', __dir__)
    copied_repository = File.join(@tmpdir, 'copied-repository')
    fake_bin = File.join(@tmpdir, 'fake-bin')
    FileUtils.mkdir_p(copied_repository)
    FileUtils.mkdir_p(fake_bin)
    FileUtils.cp_r(source_fable_root, copied_repository)

    fake_git = File.join(fake_bin, 'git')
    File.write(fake_git, <<~'SH')
      #!/bin/sh
      root=''
      if [ "$1" = '-C' ]; then
        root="$2"
        shift 2
      fi
      [ "$1" = 'rev-parse' ] || exit 99
      case "$2" in
        --show-toplevel) printf '%s\n' "$root" ;;
        --git-common-dir) printf '%s\n' '/Users/kelvin/VibeCoding-WorkSpace/skill/.git' ;;
        *) exit 99 ;;
      esac
    SH
    FileUtils.chmod(0o755, fake_git)

    script = File.join(copied_repository, 'fable-method', 'scripts', 'sync-platforms.sh')
    stdout, stderr, status = Open3.capture3(
      { 'PATH' => "#{fake_bin}:#{ENV.fetch('PATH', '')}" },
      '/bin/bash', script, '--check'
    )

    assert_equal 2, status.exitstatus, "stdout=#{stdout.inspect} stderr=#{stderr.inspect}"
    # The contained fixture lives below the task worktree, so trusted Git may
    # resolve the outer worktree and reject the copy as a non-top-level root.
    assert_match(/executing repository root is not (?:a Git repository|its Git top-level)/, stderr)
    refute_match(/NO_DRIFT/, stdout)
  end

  # =========================================================================
  # 1. Serialization, Deserialization, Validation & Fail-Closed Tests
  # =========================================================================

  def test_checkpoint_validates_required_fields
    cp = TaskCheckpoint.new(@valid_attrs)
    assert cp.valid?
    assert cp.validate!
  end

  def test_checkpoint_requires_absolute_repository_and_worktree_locators
    %i[repository worktree].each do |field|
      cp = TaskCheckpoint.new(@valid_attrs.merge(field => "relative/#{field}"))
      error = assert_raises(TaskCheckpoint::ValidationError) { cp.validate! }
      assert_match(/#{field} must be an absolute locator/, error.message)
    end
  end

  def test_checkpoint_fails_validation_when_required_fields_missing
    required = %i[task_id repository worktree authoritative_packet_ref current_head current_tree next_action]
    required.each do |field|
      attrs = @valid_attrs.dup
      attrs.delete(field)
      cp = TaskCheckpoint.new(attrs)
      refute cp.valid?, "Expected checkpoint to be invalid without #{field}"
      assert_raises(TaskCheckpoint::ValidationError) { cp.validate! }
    end
  end

  def test_checkpoint_requires_absolute_repository_and_worktree_locators
    %i[repository worktree].each do |field|
      cp = TaskCheckpoint.new(@valid_attrs.merge(field => "relative/#{field}"))
      error = assert_raises(TaskCheckpoint::ValidationError) { cp.validate! }
      assert_match(/#{field} must be an absolute locator/, error.message)
    end
  end

  def test_checkpoint_rejects_bare_inherited_without_locator
    attrs = @valid_attrs.merge(authoritative_packet_ref: 'ORIGINAL_TASK_RULES_INHERITED: YES')
    cp = TaskCheckpoint.new(attrs)
    refute cp.valid?
    err = assert_raises(TaskCheckpoint::ValidationError) { cp.validate! }
    assert_match(/authoritative_packet_ref must specify an explicit locator/, err.message)
  end

  def test_checkpoint_rejects_invalid_lifecycle_state
    attrs = @valid_attrs.merge(task_lifecycle_state: 'INVALID_STATE')
    cp = TaskCheckpoint.new(attrs)
    refute cp.valid?
    err = assert_raises(TaskCheckpoint::ValidationError) { cp.validate! }
    assert_match(/invalid task_lifecycle_state/, err.message)
  end

  def test_checkpoint_json_roundtrip
    cp = TaskCheckpoint.new(@valid_attrs)
    json = cp.to_json
    loaded = TaskCheckpoint.from_json(json)

    assert_equal cp.task_id, loaded.task_id
    assert_equal cp.repository, loaded.repository
    assert_equal cp.worktree, loaded.worktree
    assert_equal cp.authoritative_packet_ref, loaded.authoritative_packet_ref
    assert_equal cp.current_head, loaded.current_head
    assert_equal cp.current_tree, loaded.current_tree
    assert_equal cp.task_lifecycle_state, loaded.task_lifecycle_state
    assert_equal cp.next_action, loaded.next_action
    assert_equal cp.revision, loaded.revision
  end

  def test_malformed_json_fails_closed
    assert_raises(TaskCheckpoint::ValidationError) do
      TaskCheckpoint.from_json('{"task_id": "bad json...')
    end
    assert_raises(TaskCheckpoint::ValidationError) do
      TaskCheckpoint.from_json('["array instead of object"]')
    end
  end

  # =========================================================================
  # 2. Concurrency & Revision Protection
  # =========================================================================

  def test_save_increments_revision_and_protects_concurrency
    cp_path = File.join(@tmpdir, 'checkpoint.json')
    cp = TaskCheckpoint.new(@valid_attrs)
    cp.save(cp_path)

    loaded = TaskCheckpoint.load(cp_path)
    assert_equal 1, loaded.revision

    # Save update
    loaded.next_action = 'step_two'
    loaded.save(cp_path, expected_revision: 1)
    assert_equal 2, loaded.revision

    # Stale save attempt with wrong expected revision should fail
    stale = TaskCheckpoint.load(cp_path)
    assert_raises(TaskCheckpoint::ConcurrencyError) do
      stale.save(cp_path, expected_revision: 1)
    end
  end

  # Interposition lives only in freshly exec'd test processes. All processes
  # start before save takes a lock, so no production lock descriptor is inherited.
  SAVE_PROCESS = <<~'RUBY'
    lib, path, writer, mode = ARGV
    require lib
    STDOUT.sync = true
    emit = ->(event, data = nil) { puts JSON.generate([event, data]) }
    release = -> { raise 'IPC closed' unless STDIN.gets == "go\n" }
    cp = TaskCheckpoint.load(path)
    revision = cp.revision
    cp.next_action = "writer_#{writer}"
    cp.current_blocker = "complete payload #{writer}"
    cp.branch = "branch_#{writer}"

    File.prepend(Module.new do
      define_method(:flock) do |operation|
        if self.path == TaskCheckpoint.lock_path_for(path) && operation == File::LOCK_EX
          return false if mode == 'lock_false'
          raise IOError, 'injected flock failure' if mode == 'lock_raise'
          # Report actual contention, not merely that a writer was started.
          acquired = super(operation | File::LOCK_NB)
          return acquired if acquired
          emit.call('contended')
        end
        super(operation)
      end
    end)

    if %w[race no_expected].include?(mode)
      cp.define_singleton_method(:to_json) do |*args|
        raw = super(*args)
        # Revision decisions are complete and no replacement has committed.
        emit.call('prepared', JSON.parse(raw))
        release.call
        raw
      end
    end

    if %w[crash exception after_commit].include?(mode)
      File.singleton_class.prepend(Module.new do
        define_method(:rename) do |source, destination|
          if destination == path
            raise IOError, 'injected pre-commit failure' if mode == 'exception'
            if mode == 'crash'
              emit.call('before_rename', source)
              release.call
            end
          end
          result = super(source, destination)
          if destination == path && mode == 'after_commit'
            emit.call('after_rename')
            release.call
          end
          result
        end
      end)
    end

    emit.call('ready', revision)
    release.call
    begin
      cp.save(path, expected_revision: mode == 'no_expected' ? nil : revision)
      emit.call('success', cp.to_h)
    rescue TaskCheckpoint::ConcurrencyError
      emit.call('concurrency_error')
    rescue IOError => error
      emit.call('io_error', error.message)
    end
  RUBY

  def start_save_process(path, writer, mode)
    @save_events ||= Queue.new
    input, output, error, wait = Open3.popen3(
      RbConfig.ruby, '-e', SAVE_PROCESS,
      File.expand_path('../scripts/task_checkpoint.rb', __dir__), path, writer.to_s, mode
    )
    child = { id: writer, input: input, wait: wait }
    child[:readers] = [
      Thread.new do
        begin
          output.each_line { |line| @save_events << [writer, *JSON.parse(line)] }
        ensure
          output.close
        end
      end,
      Thread.new { begin; error.read; ensure; error.close; end }
    ]
    (@save_children ||= []) << child
    child
  end

  def save_event
    Timeout.timeout(5) { @save_events.pop }
  end

  def release_save(child)
    child[:input].puts('go')
    child[:input].flush
  end

  def finish_save(child, signal: nil)
    assert child[:wait].join(5), 'save timed out (not concurrency evidence)'
    child[:readers].each { |reader| assert reader.join(5), 'save output timed out' }
    assert_equal '', child[:readers].last.value
    status = child[:wait].value
    signal ? assert_equal(Signal.list.fetch(signal), status.termsig) : assert_equal(0, status.exitstatus)
  end

  def checkpoint_for_save_test
    path = File.join(@tmpdir, 'atomic', 'checkpoint.json')
    TaskCheckpoint.new(@valid_attrs).save(path)
    path
  end

  def assert_save_lock_available(path)
    File.open(TaskCheckpoint.lock_path_for(path), File::RDWR) do |lock|
      assert_equal 0, lock.flock(File::LOCK_EX | File::LOCK_NB)
    end
  end

  def run_save_race(mode)
    path = checkpoint_for_save_test
    children = 2.times.map { |writer| start_save_process(path, writer, mode) }
    ready = 2.times.map { save_event }
    assert_equal [0, 1], ready.map(&:first).sort
    ready.each { |event| assert_equal ['ready', 1], event[1, 2] }
    children.each { |child| release_save(child) }

    # With the lock, one writer prepares and the other demonstrably contends.
    # With the lock bypassed, BOTH must prepare before either may commit. The
    # same assertions below then expose double success, never a timeout proof.
    rendezvous = 2.times.map { save_event }
    assert_equal [0, 1], rendezvous.map(&:first).sort
    assert_includes [%w[contended prepared], %w[prepared prepared]], rendezvous.map { |e| e[1] }.sort
    prepared = rendezvous.select { |event| event[1] == 'prepared' }
    prepared.each { |event| release_save(children.fetch(event[0])) }
    outcomes = []
    until outcomes.length == 2
      event = save_event
      if event[1] == 'prepared' && mode == 'no_expected'
        release_save(children.fetch(event[0]))
      else
        assert_includes %w[success concurrency_error], event[1]
        outcomes << event
      end
    end
    children.each { |child| finish_save(child) }
    successes = outcomes.select { |event| event[1] == 'success' }
    expected_successes = mode == 'race' ? 1 : 2
    assert_equal expected_successes, successes.length, "lost update: #{outcomes.inspect}"
    assert_equal 2 - expected_successes, outcomes.count { |event| event[1] == 'concurrency_error' }
    final = JSON.parse(File.read(path))
    assert_equal 1 + expected_successes, final.fetch('revision')
    assert_equal successes.max_by { |event| event[2].fetch('revision') }[2], final
    assert TaskCheckpoint.load(path).valid?
    assert_empty Dir.glob("#{path}.tmp.*")
    assert_save_lock_available(path)
  end

  def test_atomic_save_two_processes_reject_one_stale_writer
    run_save_race('race')
  end

  def test_git_checkpoint_sidecar_survives_stash_replacement_and_serializes_writers
    repository = File.join(@tmpdir, 'sidecar-lock-repo')
    initialize_synthetic_git_repo(repository)
    path = File.join(repository, '.fable', 'checkpoints', 'sidecar_task.json')
    TaskCheckpoint.new(@valid_attrs).save(path)
    lock_path = TaskCheckpoint.lock_path_for(path)
    refute_equal "#{path}.lock", lock_path
    lock_identity = [File.stat(lock_path).dev, File.stat(lock_path).ino]

    first = start_save_process(path, 0, 'race')
    assert_equal [0, 'ready', 1], save_event
    release_save(first)
    assert_equal [0, 'prepared'], save_event.take(2)

    _stash_out, stash_err, stash_status = Open3.capture3(
      'git', '-C', repository, 'stash', 'push', '--include-untracked', '--message', 'sidecar replacement regression'
    )
    assert stash_status.success?, stash_err
    refute File.exist?(path), 'git stash -u removes the untracked checkpoint from the worktree'
    assert_equal lock_identity, [File.stat(lock_path).dev, File.stat(lock_path).ino]

    _pop_out, pop_err, pop_status = Open3.capture3('git', '-C', repository, 'stash', 'pop')
    assert pop_status.success?, pop_err
    assert_equal 1, TaskCheckpoint.load(path).revision
    assert_equal lock_identity, [File.stat(lock_path).dev, File.stat(lock_path).ino]

    second = start_save_process(path, 1, 'race')
    assert_equal [1, 'ready', 1], save_event
    release_save(second)
    assert_equal [1, 'contended'], save_event.take(2)

    release_save(first)
    first_success = save_event
    assert_equal [0, 'success'], first_success.take(2)
    assert_equal 2, first_success[2].fetch('revision')
    finish_save(first)
    second_outcome = save_event
    assert_equal [1, 'concurrency_error'], second_outcome.take(2), second_outcome.inspect
    finish_save(second)
    saved = TaskCheckpoint.load(path)
    assert_equal 2, saved.revision
    assert_equal 'writer_0', saved.next_action
    assert_equal lock_identity, [File.stat(lock_path).dev, File.stat(lock_path).ino]

    legacy_sidecar = "#{path}.lock"
    File.write(legacy_sidecar, 'legacy checkpoint lock')
    error = assert_raises(StableLockAnchor::MigrationRequired) do
      TaskCheckpoint.new(@valid_attrs).save(path)
    end
    assert_includes error.message, 'migration gate required'
    FileUtils.rm_f(legacy_sidecar)

    replacement_target = File.join(@tmpdir, 'sidecar-lock-target')
    File.write(replacement_target, 'untouched')
    File.unlink(lock_path)
    File.symlink(replacement_target, lock_path)
    assert_raises(StableLockAnchor::Error) { TaskCheckpoint.new(@valid_attrs).save(path) }
    assert_equal 'untouched', File.read(replacement_target)
  end

  def test_atomic_save_without_expected_revision_serializes_both_updates
    run_save_race('no_expected')
  end

  def test_atomic_save_sequential_absent_and_malformed_compatibility
    path = File.join(@tmpdir, 'checkpoint.json')
    cp = TaskCheckpoint.new(@valid_attrs.merge(revision: 4))
    cp.save(path, expected_revision: 99)
    assert_equal cp.to_h, TaskCheckpoint.load(path).to_h
    cp.save(path)
    assert_equal 5, TaskCheckpoint.load(path).revision
    cp.revision = 10
    cp.save(path)
    assert_equal 10, TaskCheckpoint.load(path).revision
    ['{broken', '[]'].each do |malformed|
      [nil, 999].each do |expected|
        File.write(path, malformed)
        fresh = TaskCheckpoint.new(@valid_attrs)
        fresh.save(path, expected_revision: expected)
        assert_equal fresh.to_h, TaskCheckpoint.load(path).to_h
        assert_equal 1, fresh.revision
      end
    end
    assert_empty Dir.glob("#{path}.tmp.*")
  end

  def test_atomic_save_precommit_crash_releases_lock_and_preserves_authority
    path = checkpoint_for_save_test
    before = File.binread(path)
    child = start_save_process(path, 0, 'crash')
    assert_equal [0, 'ready', 1], save_event
    release_save(child)
    event = save_event
    assert_equal [0, 'before_rename'], event.take(2)
    assert_equal 2, JSON.parse(File.read(event[2])).fetch('revision')
    File.open("#{path}.lock", File::RDWR) do |lock|
      assert_equal false, lock.flock(File::LOCK_EX | File::LOCK_NB)
    end
    # Only this test-owned process is terminated; no subprocess exists under it.
    Process.kill('KILL', child[:wait].pid)
    finish_save(child, signal: 'KILL')
    assert_equal before, File.binread(path)
    assert_equal 1, TaskCheckpoint.load(path).revision
    assert File.file?(event[2]), 'crash temp is deliberately left for reader-authority check'
    assert_save_lock_available(path)
    cp = TaskCheckpoint.load(path)
    cp.save(path, expected_revision: 1)
    assert_equal 2, TaskCheckpoint.load(path).revision
    assert File.file?("#{path}.lock")
  end

  def test_atomic_save_postcommit_crash_keeps_committed_revision
    path = checkpoint_for_save_test
    child = start_save_process(path, 0, 'after_commit')
    assert_equal [0, 'ready', 1], save_event
    release_save(child)
    assert_equal [0, 'after_rename', nil], save_event
    Process.kill('KILL', child[:wait].pid)
    finish_save(child, signal: 'KILL')
    cp = TaskCheckpoint.load(path)
    assert_equal 2, cp.revision
    assert_equal 'writer_0', cp.next_action
    assert_save_lock_available(path)
    cp.save(path, expected_revision: 2)
    assert_equal 3, TaskCheckpoint.load(path).revision
  end

  def test_atomic_save_exception_cleans_temp_and_persistent_sidecar_is_reusable
    path = checkpoint_for_save_test
    before = File.binread(path)
    File.write("#{path}.lock", 'persistent sidecar')
    inode = File.stat("#{path}.lock").ino
    child = start_save_process(path, 0, 'exception')
    assert_equal [0, 'ready', 1], save_event
    release_save(child)
    assert_equal [0, 'io_error', 'injected pre-commit failure'], save_event
    finish_save(child)
    assert_equal before, File.binread(path)
    assert_empty Dir.glob("#{path}.tmp.*")
    assert_save_lock_available(path)
    TaskCheckpoint.load(path).save(path, expected_revision: 1)
    assert_equal 2, TaskCheckpoint.load(path).revision
    assert_equal inode, File.stat("#{path}.lock").ino
    assert_equal 'persistent sidecar', File.read("#{path}.lock")
  end

  def test_atomic_save_flock_failure_never_writes_unlocked
    path = checkpoint_for_save_test
    before = File.binread(path)
    %w[lock_false lock_raise].each do |mode|
      child = start_save_process(path, 0, mode)
      assert_equal [0, 'ready', 1], save_event
      release_save(child)
      assert_equal [0, 'io_error'], save_event.take(2)
      finish_save(child)
      assert_equal before, File.binread(path)
      assert_empty Dir.glob("#{path}.tmp.*")
      assert_save_lock_available(path)
    end
    TaskCheckpoint.load(path).save(path, expected_revision: 1)
    assert_equal 2, TaskCheckpoint.load(path).revision
  end

  # =========================================================================
  # 3. Dogfood Scenario 1: Same session resume
  # =========================================================================

  def test_dogfood_case_1_same_session_resume
    # State: Implementation in progress, live state unchanged
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      task_lifecycle_state: 'IN_PROGRESS',
      next_action: 'implement_bounded_reconciliation'
    ))

    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree],
      branch: 'master',
      pr_state: 'NONE'
    })

    result = reconciler.reconcile
    assert_equal 'CONTINUE', result.verdict
    assert_equal 'implement_bounded_reconciliation', result.recommended_action
    assert_match(/match checkpoint cleanly/i, result.reason)
  end

  # =========================================================================
  # 4. Dogfood Scenario 2: New agent, task already completed externally
  # =========================================================================

  def test_dogfood_case_2_externally_completed_pr_merged
    # Checkpoint: PR DRAFT / waiting CI
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      pr_state: 'DRAFT_OPEN',
      pr_number: 42,
      next_action: 'WAIT_FOR_CI'
    ))

    # Live: PR is merged
    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree],
      pr_state: 'MERGED',
      pr_number: 42
    })

    result = reconciler.reconcile
    assert_equal 'ALREADY_COMPLETED', result.verdict
    assert_match(/PR.*already been merged/i, result.reason)
  end

  def test_dogfood_case_2_terminal_completed_state
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      task_lifecycle_state: 'COMPLETED',
      next_action: 'NONE_REQUIRED'
    ))

    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree]
    })

    result = reconciler.reconcile
    assert_equal 'ALREADY_COMPLETED', result.verdict
    assert_match(/already in terminal state COMPLETED/i, result.reason)
  end

  # =========================================================================
  # 5. Dogfood Scenario 3: Live branch/main advanced compatibly
  # =========================================================================

  def test_dogfood_case_3_compatible_advancement
    # Checkpoint HEAD/tree is older than live, but changes are compatible
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      current_head: 'old_sha_1111111111111111111111111111111111',
      current_tree: 'old_tree_111111111111111111111111111111111',
      next_action: 'continue_feature_work'
    ))

    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: 'new_sha_2222222222222222222222222222222222',
      tree: 'new_tree_222222222222222222222222222222222',
      compatible_advancement: true
    })

    result = reconciler.reconcile
    assert_equal 'RECONCILE_LIVE_STATE', result.verdict
    assert_match(/advanced compatibly/i, result.reason)
    assert_match(/continue_feature_work/, result.recommended_action)
  end

  # =========================================================================
  # 6. Dogfood Scenario 4: Real conflict
  # =========================================================================

  def test_dogfood_case_4_real_conflict
    # Checkpoint task tree and live state have conflicting task-owned changes
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      current_head: 'head_a_111111111111111111111111111111111111',
      current_tree: 'tree_a_111111111111111111111111111111111111',
      next_action: 'apply_patch_v2'
    ))

    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: 'head_b_222222222222222222222222222222222222',
      tree: 'tree_b_222222222222222222222222222222222222',
      has_conflict: true
    })

    result = reconciler.reconcile
    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/Material conflict detected/i, result.reason)
    assert_match(/Resolve.*conflict/i, result.recommended_action)
  end

  # =========================================================================
  # 7. Dogfood Scenario 5: Authorization does not transfer
  # =========================================================================

  def test_dogfood_case_5_authorization_does_not_transfer
    # Checkpoint says next_action = MERGE_PR, with boundary requiring direct Owner auth
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      next_action: 'MERGE_PR',
      authorization_boundary: 'CURRENT_WORKER_CONVERSATION_OWNER_AUTH_REQUIRED'
    ))

    # New Worker conversation has NO direct Owner authorization
    reconciler_no_auth = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree],
      conversation_authorizations: []
    })

    result_no_auth = reconciler_no_auth.reconcile
    assert_equal 'AUTHORIZATION_REQUIRED', result_no_auth.verdict
    assert_match(/requires explicit Owner authorization naming its exact target identity in the current conversation/i, result_no_auth.reason)
    assert_match(/quoted tokens in checkpoint do not transfer/i, result_no_auth.reason)

    # One direct Owner message may carry the exact authorization and Worker Packet.
    reconciler_with_auth = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree],
      conversation_authorizations: [direct_owner_authorization_token(action: 'MERGE_PR', target: @repo_dir)],
      authorization_target: @repo_dir
    })

    result_with_auth = reconciler_with_auth.reconcile
    assert_equal 'CONTINUE', result_with_auth.verdict
  end

  def test_dogfood_case_5_rejects_invalid_action_provenance_and_target
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      next_action: 'MERGE_PR',
      authorization_boundary: 'CURRENT_WORKER_CONVERSATION_OWNER_AUTH_REQUIRED'
    ))
    invalid_tokens = [
      'OWNER_AUTHORIZED',
      "OWNER_DIRECT_PACKET_AUTHORIZATION: ACTION=MERGE_PR; TARGET=#{@repo_dir}",
      "OWNER_DIRECT_PACKET_AUTHORIZATION: ACTION=PUSH; TARGET=#{@repo_dir}",
      "ASSISTANT_AUTHORIZED_MERGE_PR: TARGET=#{@repo_dir}",
      "PLANNER_GENERATED_MERGE_PR_AUTHORIZED: TARGET=#{@repo_dir}",
      "QUOTED_IN_PACKET_OR_HANDOFF: ACTION=MERGE_PR; TARGET=#{@repo_dir}",
      "OWNER_DIRECT_PACKET_AUTHORIZATION: ACTION=MERGE_PR; TARGET=/other-target"
    ]

    invalid_tokens.each do |token|
      result = TaskReconciler.new(cp, {
        repository: @repo_dir,
        worktree: @worktree_dir,
        head: @valid_attrs[:current_head],
        tree: @valid_attrs[:current_tree],
        conversation_authorizations: [token],
        authorization_target: @repo_dir
      }).reconcile

      assert_equal 'AUTHORIZATION_REQUIRED', result.verdict, "token unexpectedly authorized: #{token}"
    end
  end

  def test_owner_direct_packet_authorization_contract_cases_one_through_eight
    # CASE 1 — PASS: one direct Owner message contains exact authorization + Packet.
    combined = TaskCheckpoint.new(@valid_attrs.merge(
      next_action: 'MERGE_PR',
      authorization_boundary: 'CURRENT_WORKER_CONVERSATION_OWNER_AUTH_REQUIRED'
    ))
    result = TaskReconciler.new(combined, live_reconciliation_options(
      conversation_authorizations: [direct_owner_authorization_token(action: 'MERGE_PR', target: @repo_dir)],
      authorization_target: @repo_dir
    )).reconcile
    assert_equal 'CONTINUE', result.verdict

    # A high-risk authorization cannot pass when the Worker has no expected target to bind.
    result = TaskReconciler.new(combined, live_reconciliation_options(
      conversation_authorizations: [direct_owner_authorization_token(action: 'MERGE_PR', target: @repo_dir)]
    )).reconcile
    assert_equal 'AUTHORIZATION_REQUIRED', result.verdict

    # CASE 2 — PASS: reusable exact authorization from an earlier message.
    prior_auth = direct_owner_authorization_token(
      action: 'MERGE_PR',
      target: @repo_dir,
      handoff_mode: 'SAME_CONVERSATION_PRIOR_AUTH',
      owner_auth: 'REUSED_FROM_PRIOR_OWNER_MESSAGE',
      evidence: 'PRIOR_APPLICABLE_OWNER_USER_MESSAGE',
      include_packet: false
    )
    result = TaskReconciler.new(combined, live_reconciliation_options(
      conversation_authorizations: [prior_auth],
      authorization_target: @repo_dir
    )).reconcile
    assert_equal 'CONTINUE', result.verdict

    # CASE 3 — FAIL: assistant output is not direct Owner evidence.
    assistant_output = "ASSISTANT OUTPUT:\n#{direct_owner_authorization_token(action: 'MERGE_PR', target: @repo_dir)}"
    result = TaskReconciler.new(combined, live_reconciliation_options(
      conversation_authorizations: [assistant_output],
      authorization_target: @repo_dir
    )).reconcile
    assert_equal 'AUTHORIZATION_REQUIRED', result.verdict

    # CASE 4 — FAIL: quoted authorization from another conversation is not direct evidence.
    quoted = "QUOTED FROM ANOTHER CONVERSATION:\n#{direct_owner_authorization_token(action: 'MERGE_PR', target: @repo_dir)}"
    result = TaskReconciler.new(combined, live_reconciliation_options(
      conversation_authorizations: [quoted],
      authorization_target: @repo_dir
    )).reconcile
    assert_equal 'AUTHORIZATION_REQUIRED', result.verdict

    # CASE 5 — FAIL: task assignment does not authorize a production action.
    production = TaskCheckpoint.new(@valid_attrs.merge(
      next_action: 'DEPLOY_PRODUCTION',
      authorization_boundary: 'CURRENT_WORKER_CONVERSATION_OWNER_AUTH_REQUIRED'
    ))
    result = TaskReconciler.new(production, live_reconciliation_options(
      conversation_authorizations: ['Owner says: do the task']
    )).reconcile
    assert_equal 'AUTHORIZATION_REQUIRED', result.verdict

    # CASE 6 — FAIL: authorization for merge PR #123 does not authorize branch deletion.
    branch_delete = TaskCheckpoint.new(@valid_attrs.merge(
      next_action: 'DELETE_BRANCH',
      authorization_boundary: 'CURRENT_WORKER_CONVERSATION_OWNER_AUTH_REQUIRED'
    ))
    merge_only = direct_owner_authorization_token(action: 'MERGE_PR', target: 'PR #123')
    result = TaskReconciler.new(branch_delete, live_reconciliation_options(
      conversation_authorizations: [merge_only],
      authorization_target: 'feature/authorized-branch'
    )).reconcile
    assert_equal 'AUTHORIZATION_REQUIRED', result.verdict

    # CASE 7 — FAIL: exact action with a different target is out of scope.
    wrong_target = direct_owner_authorization_token(action: 'MERGE_PR', target: '/other-target')
    result = TaskReconciler.new(combined, live_reconciliation_options(
      conversation_authorizations: [wrong_target],
      authorization_target: @repo_dir
    )).reconcile
    assert_equal 'AUTHORIZATION_REQUIRED', result.verdict

    # CASE 8 — PASS: no high-risk action requires no authorization envelope.
    ordinary = TaskCheckpoint.new(@valid_attrs.merge(
      next_action: 'implement_bounded_reconciliation',
      authorization_boundary: 'NONE'
    ))
    result = TaskReconciler.new(ordinary, live_reconciliation_options).reconcile
    assert_equal 'CONTINUE', result.verdict
  end

  def test_owner_authorization_binds_each_action_to_its_exact_target
    token = multi_action_owner_authorization_token
    cases = [
      ['CLOSE_PR', 'kelvinhuang0327/skill#46', 'CONTINUE'],
      ['MERGE_PR', 'kelvinhuang0327/skill#48', 'CONTINUE'],
      ['MERGE_PR', 'kelvinhuang0327/skill#46', 'AUTHORIZATION_REQUIRED']
    ]

    observed = cases.to_h do |action, target, _expected|
      checkpoint = TaskCheckpoint.new(@valid_attrs.merge(
        next_action: action,
        authorization_boundary: 'CURRENT_WORKER_CONVERSATION_OWNER_AUTH_REQUIRED'
      ))
      result = TaskReconciler.new(checkpoint, live_reconciliation_options(
        conversation_authorizations: [token],
        authorization_target: target
      )).reconcile
      ["#{action} + #{target}", result.verdict]
    end

    assert_equal({
      'CLOSE_PR + kelvinhuang0327/skill#46' => 'CONTINUE',
      'MERGE_PR + kelvinhuang0327/skill#48' => 'CONTINUE',
      'MERGE_PR + kelvinhuang0327/skill#46' => 'AUTHORIZATION_REQUIRED'
    }, observed)
  end

  def test_owner_authorization_allows_exact_target_containing_planner
    target = 'agent/planner-fable-owner-auth-clean-successor-r1'
    checkpoint = TaskCheckpoint.new(@valid_attrs.merge(
      next_action: 'MERGE_PR',
      authorization_boundary: 'CURRENT_WORKER_CONVERSATION_OWNER_AUTH_REQUIRED'
    ))

    result = TaskReconciler.new(checkpoint, live_reconciliation_options(
      conversation_authorizations: [direct_owner_authorization_token(action: 'MERGE_PR', target: target)],
      authorization_target: target
    )).reconcile

    assert_equal 'CONTINUE', result.verdict
  end

  def test_requested_worktree_must_resolve_to_the_checkpoint_worktree
    other_worktree = File.join(@tmpdir, 'other-worktree')
    FileUtils.mkdir_p(other_worktree)
    checkpoint = TaskCheckpoint.new(@valid_attrs)

    result = TaskReconciler.new(checkpoint, live_reconciliation_options(worktree: other_worktree)).reconcile

    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/Exact worktree locator mismatch/, result.reason)
    assert_includes result.reason, @worktree_dir
    assert_includes result.reason, other_worktree
  end

  def test_worktree_symlink_to_a_different_checkout_fails_closed
    other_worktree = File.join(@tmpdir, 'other-worktree')
    alias_worktree = File.join(@tmpdir, 'worktree-alias')
    FileUtils.mkdir_p(other_worktree)
    File.symlink(other_worktree, alias_worktree)
    checkpoint = TaskCheckpoint.new(@valid_attrs)

    result = TaskReconciler.new(checkpoint, live_reconciliation_options(worktree: alias_worktree)).reconcile

    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/Exact worktree locator mismatch/, result.reason)
  end

  # =========================================================================
  # 8. Dogfood Scenario 6: Interrupted debugging (RCA continuation)
  # =========================================================================

  def test_dogfood_case_6_interrupted_rca_debugging
    # Checkpoint: CURRENT_BLOCKER = parity mismatch, NEXT_ACTION = investigate first divergent intermediate
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      current_blocker: 'parity mismatch in module auth_crypto at byte 128',
      next_action: 'investigate first divergent intermediate at trace 0x4f'
    ))

    # New session: live state unchanged
    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree]
    })

    result = reconciler.reconcile
    assert_equal 'CONTINUE', result.verdict
    assert_equal 'investigate first divergent intermediate at trace 0x4f', result.recommended_action
    assert_match(/Resuming interrupted root-cause investigation: parity mismatch/i, result.reason)
  end

  # =========================================================================
  # 9. Repository Mismatch & Worktree Missing Guards
  # =========================================================================

  def test_repository_mismatch_fails_closed
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      repository: '/some/other/project/path'
    ))

    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir
    })

    result = reconciler.reconcile
    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/Repository identity mismatch/i, result.reason)
  end

  def test_requested_worktree_must_match_the_checkpoint_locator
    cp = TaskCheckpoint.new(@valid_attrs)
    other_worktree = File.join(@tmpdir, 'other-worktree')
    FileUtils.mkdir_p(other_worktree)
    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: other_worktree
    })

    result = reconciler.reconcile

    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/Exact worktree locator mismatch/, result.reason)
    assert_includes result.reason, cp.worktree
    assert_includes result.reason, other_worktree
  end

  def test_missing_worktree_fails_closed
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      worktree: '/nonexistent/worktree/dir'
    ))

    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: '/nonexistent/worktree/dir'
    })

    result = reconciler.reconcile
    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/Worktree directory does not exist/i, result.reason)
  end

  # =========================================================================
  # 10. Cross-Agent Authoritative Packet Resolution
  # =========================================================================

  def test_cross_agent_resolves_repo_relative_packet_file
    # Proves a fresh Worker in a new session can resolve the authoritative packet
    # directly from the repository filesystem without access to prior chat memory.
    packet_path = File.join(@repo_dir, 'docs', 'packets', 'TASK-042.md')
    FileUtils.mkdir_p(File.dirname(packet_path))
    File.write(packet_path, "# Authoritative Worker Packet for Task 042\nRules: Standard\n")

    cp = TaskCheckpoint.new(@valid_attrs.merge(
      task_id: 'TASK-042',
      authoritative_packet_ref: 'docs/packets/TASK-042.md'
    ))

    resolved = cp.resolve_authoritative_packet(@repo_dir)
    assert_equal :resolved, resolved[:status]
    assert_equal :file, resolved[:source]
    assert_includes resolved[:content], 'Authoritative Worker Packet for Task 042'

    # Reconciler should pass packet resolution guard and return CONTINUE
    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree]
    })
    result = reconciler.reconcile
    assert_equal 'CONTINUE', result.verdict
  end

  def test_cross_agent_rejects_ephemeral_conversation_uri
    # Proves that ephemeral conversation URIs fail closed, preventing a new Agent
    # from assuming authority based on unreachable chat history.
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      authoritative_packet_ref: 'conversation://4625a5d9-70ff-433f-8c4f-ca8ed3194922'
    ))

    refute cp.valid?
    assert_raises(TaskCheckpoint::ValidationError) { cp.validate! }

    # Reconciliation also fails closed with STOP_UNRESOLVED
    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree]
    })
    result = reconciler.reconcile
    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/ephemeral session URI/i, result.reason)
    assert_match(/cannot be resolved by a fresh Worker without chat memory/i, result.reason)
  end

  def test_cross_agent_fails_closed_on_missing_packet_file
    # If the referenced packet file cannot be found in the repo/worktree, fail closed.
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      authoritative_packet_ref: 'docs/missing_packet.md'
    ))

    reconciler = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree]
    })
    result = reconciler.reconcile
    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/Durable packet file not found/i, result.reason)
  end

  # =========================================================================
  # 11. Deferred blocked-task queue contract
  # =========================================================================

  def test_old_schema_v1_checkpoint_remains_queue_field_free_and_loadable
    cp = TaskCheckpoint.new(@valid_attrs)
    data = cp.to_h

    assert_equal 1, data.fetch('schema_version')
    TaskCheckpoint::OPTIONAL_QUEUE_FIELDS.each do |field|
      refute data.key?(field.to_s), "legacy non-deferred checkpoint unexpectedly emitted #{field}"
    end

    loaded = TaskCheckpoint.from_json(JSON.generate(data))
    assert loaded.validate!
    refute loaded.deferred?
  end

  def test_blocked_deferred_is_not_a_lifecycle_enum
    assert_equal %w[IN_PROGRESS BLOCKED COMPLETED ABORTED], TaskCheckpoint::VALID_LIFECYCLE_STATES
    refute_includes TaskCheckpoint::VALID_LIFECYCLE_STATES, 'BLOCKED_DEFERRED'
    assert_equal 1, TaskCheckpoint::SCHEMA_VERSION
  end

  def test_inconsistent_or_partial_queue_fields_fail_closed
    partial = TaskCheckpoint.new(@valid_attrs.merge(resume_after_task_id: 'TASK_B_002'))
    error = assert_raises(TaskCheckpoint::ValidationError) { partial.validate! }
    assert_match(/queue-specific fields require queue_disposition or a complete consumed queue-run marker/, error.message)

    inconsistent = TaskCheckpoint.new(@valid_attrs.merge(
      queue_disposition: 'BLOCKED_DEFERRED',
      resume_after_task_id: 'TASK_B_002',
      next_authorized_task_packet_ref: 'conversation://task-b',
      deferred_resume_action: 'resume-a',
      deferred_recheck_count: 2
    ))
    error = assert_raises(TaskCheckpoint::ValidationError) { inconsistent.validate! }
    assert_match(/task_lifecycle_state BLOCKED/, error.message)
    assert_match(/ephemeral session URI/, error.message)
    assert_match(/integer from 0 to 1/, error.message)

    invalid_consumed_marker = TaskCheckpoint.new(@valid_attrs.merge(
      resume_after_task_id: 'TASK_B_002',
      next_authorized_task_packet_ref: 'conversation://task-b',
      deferred_resume_action: 'resume-a',
      deferred_recheck_count: 1
    ))
    error = assert_raises(TaskCheckpoint::ValidationError) { invalid_consumed_marker.validate! }
    assert_match(/ephemeral session URI/, error.message)
  end

  def test_defer_persists_queue_disposition_and_original_continuation
    cp = deferred_task_a
    checkpoint_path = File.join(@tmpdir, 'task-a-deferred.json')
    cp.save(checkpoint_path)
    loaded = TaskCheckpoint.load(checkpoint_path)

    assert_equal 1, loaded.schema_version
    assert_equal 'BLOCKED', loaded.task_lifecycle_state
    assert_equal 'BLOCKED_DEFERRED', loaded.queue_disposition
    assert_equal 'TASK_B_002', loaded.resume_after_task_id
    assert_equal @task_b_packet_ref, loaded.next_authorized_task_packet_ref
    assert_equal 'implement_bounded_reconciliation', loaded.deferred_resume_action
    assert_equal 'RECHECK_DEFERRED_RESUME_GATE', loaded.next_action
    assert_equal 0, loaded.deferred_recheck_count
    assert_equal({ task_id: 'TASK_B_002', authoritative_packet_ref: @task_b_packet_ref }, loaded.authorized_deferred_task)
  end

  def test_defer_requires_explicit_deferred_checkpoint_inventory
    cp = TaskCheckpoint.new(@valid_attrs)

    error = assert_raises(ArgumentError) do
      cp.defer_for_authorized_task!(
        blocker: 'transient external service outage',
        blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002',
        next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true,
        task_b_packet_authorized: true
      )
    end

    assert_match(/existing_deferred_checkpoints/, error.message)
    refute cp.deferred?
  end

  def test_defer_rejects_ineligible_blocker_classes
    %w[SEMANTIC AUTHORIZATION SAFETY DATABASE_AUTHORITY PERMANENT].each do |blocker_class|
      cp = TaskCheckpoint.new(@valid_attrs)
      error = assert_raises(TaskCheckpoint::DeferredQueueEligibilityError) do
        cp.defer_for_authorized_task!(
          blocker: 'not transient',
          blocker_disposition: blocker_class,
          resume_after_task_id: 'TASK_B_002',
          next_authorized_task_packet_ref: @task_b_packet_ref,
          task_b_independent: true,
          task_b_packet_authorized: true,
          existing_deferred_checkpoints: []
        )
      end
      assert_match(/TRANSIENT_ELIGIBLE/, error.message)
      refute cp.deferred?
    end
  end

  def test_defer_requires_independent_already_authorized_task_b
    cp = TaskCheckpoint.new(@valid_attrs)
    assert_raises(TaskCheckpoint::DeferredQueueEligibilityError) do
      cp.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: false, task_b_packet_authorized: true,
        existing_deferred_checkpoints: []
      )
    end

    assert_raises(TaskCheckpoint::DeferredQueueEligibilityError) do
      cp.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true, task_b_packet_authorized: false,
        existing_deferred_checkpoints: []
      )
    end

    assert_raises(TaskCheckpoint::DeferredQueueStateError) do
      cp.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: cp.task_id, next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true, task_b_packet_authorized: true,
        existing_deferred_checkpoints: []
      )
    end

    completed = TaskCheckpoint.new(@valid_attrs.merge(task_lifecycle_state: 'COMPLETED'))
    assert_raises(TaskCheckpoint::DeferredQueueStateError) do
      completed.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true, task_b_packet_authorized: true,
        existing_deferred_checkpoints: []
      )
    end
  end

  def test_overlapping_ownership_rejects_task_b_independence
    cp = TaskCheckpoint.new(@valid_attrs)

    error = assert_raises(TaskCheckpoint::DeferredQueueEligibilityError) do
      cp.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true, task_b_packet_authorized: true,
        existing_deferred_checkpoints: [],
        task_a_owned_paths: ['fable-method/scripts'],
        task_b_owned_paths: ['fable-method/scripts/task_checkpoint.rb']
      )
    end

    assert_match(/ownership surfaces overlap/, error.message)
    refute cp.deferred?
    assert TaskCheckpoint.ownership_surfaces_overlap?(
      ['.'],
      ['fable-method/scripts/task_checkpoint.rb']
    )
  end

  def test_defer_rejects_ephemeral_or_unresolvable_task_b_packet
    ephemeral = TaskCheckpoint.new(@valid_attrs)
    assert_raises(TaskCheckpoint::ResolutionError) do
      ephemeral.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: 'conversation://task-b',
        task_b_independent: true, task_b_packet_authorized: true,
        existing_deferred_checkpoints: []
      )
    end

    missing = TaskCheckpoint.new(@valid_attrs)
    assert_raises(TaskCheckpoint::ResolutionError) do
      missing.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: 'prompt/missing-task-b.md',
        task_b_independent: true, task_b_packet_authorized: true,
        existing_deferred_checkpoints: []
      )
    end
  end

  def test_maximum_one_deferred_task_prevents_task_c_chaining
    task_a = deferred_task_a
    task_b = TaskCheckpoint.new(@valid_attrs.merge(task_id: 'TASK_B_002', next_action: 'TASK_B_CONTINUE'))

    error = assert_raises(TaskCheckpoint::DeferredQueueLimitError) do
      task_b.defer_for_authorized_task!(
        blocker: 'another transient blocker', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_C_003', next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true, task_b_packet_authorized: true,
        existing_deferred_checkpoints: [task_a]
      )
    end
    assert_match(/Task C chaining is prohibited/, error.message)

    second_deferred = TaskCheckpoint.from_json(task_a.to_json)
    second_deferred.task_id = 'TASK_OTHER'
    second_deferred.resume_after_task_id = 'TASK_OTHER_B'
    assert_raises(TaskCheckpoint::DeferredQueueLimitError) do
      TaskCheckpoint.validate_deferred_limit!([task_a, second_deferred])
    end
  end

  def test_task_b_must_be_terminal_before_the_single_recheck
    task_a = deferred_task_a
    task_b = task_b_checkpoint(state: 'IN_PROGRESS')

    error = assert_raises(TaskCheckpoint::DeferredQueueStateError) do
      task_a.recheck_deferred_resume_gate!(completed_task: task_b, gate_passed: true)
    end
    assert_match(/terminal end-of-task state/, error.message)
    assert_equal 0, task_a.deferred_recheck_count
  end

  def test_recheck_requires_task_b_checkpoint_rooted_in_named_packet
    task_a = deferred_task_a
    wrong_packet_task_b = task_b_checkpoint(packet_ref: @valid_attrs[:authoritative_packet_ref])

    error = assert_raises(TaskCheckpoint::DeferredQueueStateError) do
      task_a.recheck_deferred_resume_gate!(completed_task: wrong_packet_task_b, gate_passed: true)
    end
    assert_match(/Packet ref does not match/, error.message)
    assert_equal 0, task_a.deferred_recheck_count
  end

  def test_failed_recheck_remains_blocked_deferred_and_cannot_repeat
    task_a = deferred_task_a
    blocked_task_b = task_b_checkpoint(state: 'BLOCKED')

    assert_equal :blocked_deferred,
                 task_a.recheck_deferred_resume_gate!(completed_task: blocked_task_b, gate_passed: false)
    assert_equal 'BLOCKED', task_a.task_lifecycle_state
    assert_equal 'BLOCKED_DEFERRED', task_a.queue_disposition
    assert_equal 'RECHECK_DEFERRED_RESUME_GATE', task_a.next_action
    assert_equal 1, task_a.deferred_recheck_count

    assert_raises(TaskCheckpoint::DeferredQueueLimitError) do
      task_a.recheck_deferred_resume_gate!(completed_task: blocked_task_b, gate_passed: true)
    end
  end

  def test_passing_recheck_resumes_preserved_task_a_action
    task_a = deferred_task_a
    completed_task_b = task_b_checkpoint

    assert_equal :resumed,
                 task_a.recheck_deferred_resume_gate!(completed_task: completed_task_b, gate_passed: true)
    assert_equal 'IN_PROGRESS', task_a.task_lifecycle_state
    assert_nil task_a.current_blocker
    assert_equal 'implement_bounded_reconciliation', task_a.next_action
    refute task_a.deferred?
    assert_nil task_a.queue_disposition
    assert_equal 'TASK_B_002', task_a.resume_after_task_id
    assert_equal @task_b_packet_ref, task_a.next_authorized_task_packet_ref
    assert_equal 'implement_bounded_reconciliation', task_a.deferred_resume_action
    assert_equal 1, task_a.deferred_recheck_count
    assert task_a.deferred_queue_run_consumed?
    assert_nil task_a.authorized_deferred_task
  end

  def test_resumed_task_a_recurrence_blocks_without_second_automatic_retry
    task_a = deferred_task_a
    assert_equal :resumed,
                 task_a.recheck_deferred_resume_gate!(completed_task: task_b_checkpoint, gate_passed: true)

    checkpoint_path = File.join(@tmpdir, 'task-a-consumed-run.json')
    task_a.save(checkpoint_path)
    fresh_task_a = TaskCheckpoint.load(checkpoint_path)
    assert fresh_task_a.deferred_queue_run_consumed?

    error = assert_raises(TaskCheckpoint::DeferredQueueLimitError) do
      fresh_task_a.defer_for_authorized_task!(
        blocker: 'same transient blocker recurred', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true, task_b_packet_authorized: true,
        existing_deferred_checkpoints: []
      )
    end
    assert_match(/automatic deferred queue run is already consumed/, error.message)

    assert_equal :blocked_deferred_recurrence,
                 fresh_task_a.block_recurrent_transient!(
                   blocker: 'same transient blocker recurred',
                   blocker_disposition: 'TRANSIENT_ELIGIBLE'
                 )
    assert_equal 'BLOCKED', fresh_task_a.task_lifecycle_state
    assert_equal 'BLOCKED_DEFERRED', fresh_task_a.queue_disposition
    assert_equal 1, fresh_task_a.deferred_recheck_count
    assert_nil fresh_task_a.authorized_deferred_task

    fresh_task_a.save(checkpoint_path, expected_revision: 1)
    reloaded = TaskCheckpoint.load(checkpoint_path)
    result = TaskReconciler.new(reloaded, live_reconciliation_options).reconcile
    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/single automatic end-of-task recheck/, result.reason)
  end

  def test_recurrent_ineligible_blocker_does_not_enter_deferred_queue
    task_a = deferred_task_a
    task_a.recheck_deferred_resume_gate!(completed_task: task_b_checkpoint, gate_passed: true)

    assert_raises(TaskCheckpoint::DeferredQueueEligibilityError) do
      task_a.block_recurrent_transient!(
        blocker: 'authorization is missing',
        blocker_disposition: 'AUTHORIZATION'
      )
    end
    refute task_a.deferred?
    assert task_a.deferred_queue_run_consumed?

    terminal = TaskCheckpoint.from_json(task_a.to_json)
    terminal.task_lifecycle_state = 'COMPLETED'
    assert_raises(TaskCheckpoint::DeferredQueueStateError) do
      terminal.block_recurrent_transient!(
        blocker: 'transient dependency returned after completion',
        blocker_disposition: 'TRANSIENT_ELIGIBLE'
      )
    end
  end

  def test_reconciler_executes_only_named_task_b_then_exposes_recheck_gate
    task_a = deferred_task_a

    pending = TaskReconciler.new(task_a, live_reconciliation_options).reconcile
    assert_equal 'CONTINUE', pending.verdict
    assert_match(/EXECUTE_AUTHORIZED_DEFERRED_TASK task_id=TASK_B_002/, pending.recommended_action)
    assert_match(/packet_ref=#{Regexp.escape(@task_b_packet_ref)}/, pending.recommended_action)

    in_progress = TaskReconciler.new(
      task_a,
      live_reconciliation_options(resume_after_task_checkpoint: task_b_checkpoint(state: 'IN_PROGRESS'))
    ).reconcile
    assert_equal 'CONTINUE', in_progress.verdict
    assert_match(/EXECUTE_AUTHORIZED_DEFERRED_TASK/, in_progress.recommended_action)

    terminal = TaskReconciler.new(
      task_a,
      live_reconciliation_options(resume_after_task_checkpoint: task_b_checkpoint)
    ).reconcile
    assert_equal 'CONTINUE', terminal.verdict
    assert_equal 'RECHECK_DEFERRED_RESUME_GATE', terminal.recommended_action
    assert_match(/one recheck available/, terminal.reason)
  end

  def test_reconciler_fails_closed_when_durable_task_b_packet_is_missing
    task_a = TaskCheckpoint.new(@valid_attrs.merge(
      task_lifecycle_state: 'BLOCKED',
      current_blocker: 'transient blocker',
      next_action: 'RECHECK_DEFERRED_RESUME_GATE',
      queue_disposition: 'BLOCKED_DEFERRED',
      resume_after_task_id: 'TASK_B_002',
      next_authorized_task_packet_ref: 'prompt/missing-task-b.md',
      deferred_resume_action: 'implement_bounded_reconciliation',
      deferred_recheck_count: 0
    ))
    assert task_a.validate!

    result = TaskReconciler.new(task_a, live_reconciliation_options).reconcile
    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/Task B packet ref cannot be resolved/, result.reason)
  end

  def test_reconciler_fails_closed_if_task_b_is_already_deferred_to_task_c
    task_a = deferred_task_a
    task_b = TaskCheckpoint.new(@valid_attrs.merge(
      task_id: 'TASK_B_002',
      authoritative_packet_ref: @task_b_packet_ref,
      next_action: 'TASK_B_CONTINUE'
    ))
    task_b.defer_for_authorized_task!(
      blocker: 'Task B transient blocker', blocker_disposition: 'TRANSIENT_ELIGIBLE',
      resume_after_task_id: 'TASK_C_003', next_authorized_task_packet_ref: @task_b_packet_ref,
      task_b_independent: true, task_b_packet_authorized: true,
      existing_deferred_checkpoints: []
    )

    result = TaskReconciler.new(
      task_a,
      live_reconciliation_options(resume_after_task_checkpoint: task_b)
    ).reconcile
    assert_equal 'STOP_UNRESOLVED', result.verdict
    assert_match(/Task B attempted to defer to Task C/, result.reason)
  end

  def test_reconciler_rejects_wrong_task_b_and_exhausted_recheck
    task_a = deferred_task_a
    wrong_task = TaskReconciler.new(
      task_a,
      live_reconciliation_options(resume_after_task_checkpoint: task_b_checkpoint(task_id: 'TASK_X'))
    ).reconcile
    assert_equal 'STOP_UNRESOLVED', wrong_task.verdict
    assert_match(/expected Task B 'TASK_B_002'/, wrong_task.reason)

    wrong_packet = TaskReconciler.new(
      task_a,
      live_reconciliation_options(
        resume_after_task_checkpoint: task_b_checkpoint(packet_ref: @valid_attrs[:authoritative_packet_ref])
      )
    ).reconcile
    assert_equal 'STOP_UNRESOLVED', wrong_packet.verdict
    assert_match(/Packet ref does not match/, wrong_packet.reason)

    task_a.recheck_deferred_resume_gate!(completed_task: task_b_checkpoint(state: 'BLOCKED'), gate_passed: false)
    exhausted = TaskReconciler.new(
      task_a,
      live_reconciliation_options(resume_after_task_checkpoint: task_b_checkpoint)
    ).reconcile
    assert_equal 'STOP_UNRESOLVED', exhausted.verdict
    assert_match(/single automatic end-of-task recheck/, exhausted.reason)
  end

  def test_fresh_process_loads_deferred_state_and_reconciles_terminal_task_b
    task_a_path = File.join(@tmpdir, 'fresh-process-task-a.json')
    task_b_path = File.join(@tmpdir, 'fresh-process-task-b.json')
    deferred_task_a.save(task_a_path)
    task_b_checkpoint.save(task_b_path)
    script = File.expand_path('../scripts/task_checkpoint.rb', __dir__)

    show_stdout, show_stderr, show_status = Open3.capture3(RbConfig.ruby, script, '--show', task_a_path)
    assert show_status.success?, show_stderr
    shown = JSON.parse(show_stdout)
    assert_equal 'BLOCKED_DEFERRED', shown.fetch('queue_disposition')
    assert_equal 'implement_bounded_reconciliation', shown.fetch('deferred_resume_action')

    stdout, stderr, status = Open3.capture3(
      RbConfig.ruby, script, '--reconcile',
      '--repo', @repo_dir, '--worktree', @worktree_dir,
      '--head', @valid_attrs[:current_head], '--tree', @valid_attrs[:current_tree],
      '--resume-after-task-checkpoint', task_b_path,
      task_a_path
    )
    assert status.success?, stderr
    assert_match(/RECONCILIATION_VERDICT: CONTINUE/, stdout)
    assert_match(/RECOMMENDED_ACTION: RECHECK_DEFERRED_RESUME_GATE/, stdout)
  end

  def test_unrelated_external_process_does_not_create_false_active_writer_block
    before_snapshot = { head: 'same-head', tree: 'same-tree', status: '' }
    after_snapshot = before_snapshot.dup
    owned_paths = ['fable-method/scripts/task_checkpoint.rb']
    unrelated_processes = [
      {
        process_name: 'pytest',
        cwd: File.join(@tmpdir, 'other-repository'),
        target_paths: ['tests']
      },
      {
        process_name: 'python',
        target_paths: [File.join(@tmpdir, 'unrelated-output')]
      },
      { process_name: 'Agent' }
    ]

    assert_equal 5, TaskCheckpoint::DEFAULT_QUIESCENCE_OBSERVATION_SECONDS
    refute TaskCheckpoint.scope_qualified_active_writer?(
      worktree: @worktree_dir,
      task_owned_paths: owned_paths,
      writer_evidence: unrelated_processes,
      before_snapshot: before_snapshot,
      after_snapshot: after_snapshot
    )

    assert TaskCheckpoint.scope_qualified_active_writer?(
      worktree: @worktree_dir,
      task_owned_paths: owned_paths,
      writer_evidence: [{
        process_name: 'ruby',
        cwd: @worktree_dir,
        target_paths: ['fable-method/scripts/task_checkpoint.rb']
      }],
      before_snapshot: before_snapshot,
      after_snapshot: after_snapshot
    )

    assert TaskCheckpoint.scope_qualified_active_writer?(
      worktree: @worktree_dir,
      task_owned_paths: owned_paths,
      writer_evidence: [],
      before_snapshot: before_snapshot,
      after_snapshot: before_snapshot.merge(status: 'M task_checkpoint.rb')
    )
  end
end

class PublicationLiveStateClassifierTest < Minitest::Test
  Classifier = PublicationLiveStateClassifier

  def pr(number:, state:, head_ref:, draft: false, head_sha: nil, merge_commit_sha: nil, base_ref: 'master')
    {
      number: number,
      url: "https://github.com/kelvinhuang0327/skill/pull/#{number}",
      state: state,
      draft: draft,
      head_ref: head_ref,
      base_ref: base_ref,
      head_sha: head_sha,
      merge_commit_sha: merge_commit_sha,
      merged_at: state == 'MERGED' ? Time.now.utc.iso8601 : nil
    }
  end

  # A. Draft open -> state DRAFT_PR_OPEN.
  def test_a_draft_pr_open_state
    classifier = Classifier.new(
      branch: 'agent/task-a',
      remote_branch_exists_fetcher: ->(_branch) { true },
      prs_by_branch_fetcher: ->(_branch) { [pr(number: 10, state: 'OPEN', draft: true, head_ref: 'agent/task-a')] }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_DRAFT_PR_OPEN, result.state
    assert_nil result.ready_action
    refute result.conflict?
  end

  # B. Ready open -> state READY_PR_OPEN and Ready short-circuits.
  def test_b_ready_pr_open_short_circuits_ready_action
    classifier = Classifier.new(
      branch: 'agent/task-b',
      remote_branch_exists_fetcher: ->(_branch) { true },
      prs_by_branch_fetcher: ->(_branch) { [pr(number: 11, state: 'OPEN', draft: false, head_ref: 'agent/task-b')] }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_READY_PR_OPEN, result.state
    assert_equal Classifier::ACTION_SKIP_ALREADY_COMPLETE, result.ready_action
  end

  # C. Merged + postmerge pending -> Ready/Merge skip; postmerge remains required.
  def test_c_merged_postmerge_pending_requires_postmerge
    classifier = Classifier.new(
      branch: 'agent/task-c',
      remote_branch_exists_fetcher: ->(_branch) { false },
      prs_by_branch_fetcher: lambda { |_branch|
        [pr(number: 12, state: 'MERGED', head_ref: 'agent/task-c', merge_commit_sha: 'mergedsha12')]
      }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_MERGED_POSTMERGE_PENDING, result.state
    assert_equal Classifier::ACTION_SKIP_ALREADY_COMPLETE, result.ready_action
    assert_equal Classifier::ACTION_SKIP_ALREADY_COMPLETE, result.merge_action
    assert_equal Classifier::ACTION_VERIFY_OR_COMPLETE_MISSING_POSTMERGE, result.postmerge_action
    assert_nil result.terminal_action
  end

  # D. Merged + postmerge complete -> Ready/Merge skip; terminal completion.
  def test_d_merged_postmerge_complete_reuses_evidence_and_reaches_terminal
    classifier = Classifier.new(
      branch: 'agent/task-d',
      postmerge_evidence: { pr_number: 13, merge_commit_sha: 'mergedsha13', verified: true },
      remote_branch_exists_fetcher: ->(_branch) { false },
      prs_by_branch_fetcher: lambda { |_branch|
        [pr(number: 13, state: 'MERGED', head_ref: 'agent/task-d', merge_commit_sha: 'mergedsha13')]
      }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_MERGED_POSTMERGE_COMPLETE, result.state
    assert_equal Classifier::ACTION_SKIP_ALREADY_COMPLETE, result.ready_action
    assert_equal Classifier::ACTION_SKIP_ALREADY_COMPLETE, result.merge_action
    assert_equal Classifier::ACTION_REUSE_VERIFIED_EVIDENCE_OR_VERIFY_ONLY_IF_MISSING, result.postmerge_action
    assert_equal Classifier::ACTION_COMPLETION_HANDOFF, result.terminal_action
  end

  # E. Remote branch exists / no PR -> REMOTE_BRANCH_ONLY.
  def test_e_remote_branch_only_when_no_pr_exists
    classifier = Classifier.new(
      branch: 'agent/task-e',
      remote_branch_exists_fetcher: ->(_branch) { true },
      prs_by_branch_fetcher: ->(_branch) { [] }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_REMOTE_BRANCH_ONLY, result.state
    assert_nil result.ready_action
  end

  # F. No remote task branch -> LOCAL_ONLY.
  def test_f_local_only_when_no_remote_branch_or_pr
    classifier = Classifier.new(
      branch: 'agent/task-f',
      remote_branch_exists_fetcher: ->(_branch) { false },
      prs_by_branch_fetcher: ->(_branch) { [] }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_LOCAL_ONLY, result.state
    assert_nil result.ready_action
  end

  # G. Checkpoint says Draft but live PR is Merged -> live Merged state wins.
  def test_g_stale_checkpoint_draft_but_live_merged_state_wins
    stale_checkpoint = TaskCheckpoint.new(
      task_id: 'TASK_G', repository: '/tmp/repo', worktree: '/tmp/repo',
      authoritative_packet_ref: 'prompt/packet.md#task-g', branch: 'agent/task-g',
      current_head: 'a' * 40, current_tree: 'b' * 40, task_lifecycle_state: 'IN_PROGRESS',
      next_action: 'WAIT_FOR_CI', authorization_boundary: 'NONE',
      pr_state: 'DRAFT_OPEN', pr_number: 14, updated_at: Time.now.utc.iso8601, revision: 1
    )
    assert_equal 'DRAFT_OPEN', stale_checkpoint.pr_state

    classifier = Classifier.new(
      branch: stale_checkpoint.branch,
      named_pr_number: stale_checkpoint.pr_number,
      remote_branch_exists_fetcher: ->(_branch) { false },
      pr_by_number_fetcher: lambda { |_number|
        pr(number: 14, state: 'MERGED', head_ref: 'agent/task-g', merge_commit_sha: 'mergedsha14')
      }
    )
    result = classifier.classify

    refute_equal stale_checkpoint.pr_state, result.state
    assert_equal Classifier::STATE_MERGED_POSTMERGE_PENDING, result.state
    assert_equal Classifier::ACTION_SKIP_ALREADY_COMPLETE, result.ready_action
  end

  # H. Named PR belongs to wrong branch -> IDENTITY_CONFLICT / STOP_UNRESOLVED.
  def test_h_named_pr_wrong_branch_is_identity_conflict
    classifier = Classifier.new(
      branch: 'agent/task-h',
      named_pr_number: 15,
      remote_branch_exists_fetcher: ->(_branch) { true },
      pr_by_number_fetcher: ->(_number) { pr(number: 15, state: 'OPEN', head_ref: 'agent/some-other-task') }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_IDENTITY_CONFLICT, result.state
    assert result.conflict?
    assert_equal Classifier::ACTION_STOP_UNRESOLVED, result.ready_action
    assert_match(/points to branch/i, result.reason)
  end

  # I. Two ambiguous open PRs on same task branch -> IDENTITY_CONFLICT.
  def test_i_ambiguous_open_prs_is_identity_conflict
    classifier = Classifier.new(
      branch: 'agent/task-i',
      remote_branch_exists_fetcher: ->(_branch) { true },
      prs_by_branch_fetcher: lambda { |_branch|
        [
          pr(number: 16, state: 'OPEN', head_ref: 'agent/task-i'),
          pr(number: 17, state: 'OPEN', head_ref: 'agent/task-i')
        ]
      }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_IDENTITY_CONFLICT, result.state
    assert_match(/more than one open PR/i, result.reason)
    assert_match(/#16/, result.reason)
    assert_match(/#17/, result.reason)
  end

  # J. Exact already-verified postmerge evidence at same identity is reused
  # rather than rerun; mismatched identity is not silently reused.
  def test_j_postmerge_evidence_reused_only_at_exact_identity
    live_pr = -> { pr(number: 18, state: 'MERGED', head_ref: 'agent/task-j', merge_commit_sha: 'mergedsha18') }

    matching = Classifier.new(
      branch: 'agent/task-j',
      postmerge_evidence: { pr_number: 18, merge_commit_sha: 'mergedsha18', verified: true },
      remote_branch_exists_fetcher: ->(_branch) { false },
      prs_by_branch_fetcher: ->(_branch) { [live_pr.call] }
    ).classify
    assert_equal Classifier::STATE_MERGED_POSTMERGE_COMPLETE, matching.state
    assert_equal Classifier::ACTION_REUSE_VERIFIED_EVIDENCE_OR_VERIFY_ONLY_IF_MISSING, matching.postmerge_action

    mismatched = Classifier.new(
      branch: 'agent/task-j',
      postmerge_evidence: { pr_number: 18, merge_commit_sha: 'DIFFERENT_SHA', verified: true },
      remote_branch_exists_fetcher: ->(_branch) { false },
      prs_by_branch_fetcher: ->(_branch) { [live_pr.call] }
    ).classify
    assert_equal Classifier::STATE_MERGED_POSTMERGE_PENDING, mismatched.state
    assert_equal Classifier::ACTION_VERIFY_OR_COMPLETE_MISSING_POSTMERGE, mismatched.postmerge_action

    unverified = Classifier.new(
      branch: 'agent/task-j',
      postmerge_evidence: { pr_number: 18, merge_commit_sha: 'mergedsha18', verified: false },
      remote_branch_exists_fetcher: ->(_branch) { false },
      prs_by_branch_fetcher: ->(_branch) { [live_pr.call] }
    ).classify
    assert_equal Classifier::STATE_MERGED_POSTMERGE_PENDING, unverified.state
  end

  # K. Live PR head inconsistent with expected task lineage -> IDENTITY_CONFLICT.
  def test_k_open_pr_head_sha_inconsistent_with_expected_lineage_is_conflict
    classifier = Classifier.new(
      branch: 'agent/task-k',
      expected_head_sha: 'expectedsha000',
      remote_branch_exists_fetcher: ->(_branch) { true },
      prs_by_branch_fetcher: lambda { |_branch|
        [pr(number: 19, state: 'OPEN', head_ref: 'agent/task-k', head_sha: 'unexpectedsha999')]
      }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_IDENTITY_CONFLICT, result.state
    assert_match(/inconsistent with expected task lineage/i, result.reason)
  end

  def test_live_lookup_failure_fails_closed_as_identity_conflict
    classifier = Classifier.new(
      branch: 'agent/task-unresolvable',
      remote_branch_exists_fetcher: ->(_branch) { raise Classifier::PrLookupError, 'network unreachable' }
    )
    result = classifier.classify
    assert_equal Classifier::STATE_IDENTITY_CONFLICT, result.state
    assert_match(/could not be independently resolved/i, result.reason)
  end

  def test_classifier_adds_no_new_persistent_lifecycle_state_or_store
    assert_equal %w[IN_PROGRESS BLOCKED COMPLETED ABORTED], TaskCheckpoint::VALID_LIFECYCLE_STATES
    refute_includes TaskCheckpoint::VALID_LIFECYCLE_STATES, PublicationLiveStateClassifier::STATE_MERGED_POSTMERGE_COMPLETE

    classifier = Classifier.new(branch: 'agent/no-op', remote_branch_exists_fetcher: ->(_b) { false },
                                 prs_by_branch_fetcher: ->(_b) { [] })
    refute_respond_to classifier, :save
    refute_respond_to Classifier::Classification.new, :save
  end
end

class DurableCommandCaptureTest < Minitest::Test
  def setup
    @tmpdir = TaskCheckpointTestScratch.mktmpdir('durable_capture_test_')
  end

  def teardown
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.directory?(@tmpdir)
  end

  def capture_path(name)
    File.join(@tmpdir, 'captures', "#{name}.json")
  end

  # E. Durable Judge success: exact command/stdout/stderr/exit 0/start/end.
  def test_e_durable_success_capture_contains_all_required_fields
    path = capture_path('success')
    command = ['ruby', '-e', 'puts "ok"; exit 0']
    run_result = DurableCommandCapture.run_and_capture(command, file_path: path)

    assert File.file?(path)
    reloaded = DurableCommandCapture.load(path)
    assert_equal command, reloaded.command
    assert_includes reloaded.stdout, 'ok'
    assert_equal 0, reloaded.exit_status
    refute_nil reloaded.started_at
    refute_nil reloaded.ended_at
    assert_equal DurableCommandCapture::VERDICT_PASS, reloaded.verdict
    assert_equal run_result.exit_status, reloaded.exit_status
  end

  def test_protected_capture_binds_exact_execution_repository_worktree_launcher_and_runtime
    repository = File.join(@tmpdir, 'execution-repository')
    FileUtils.mkdir_p(repository)
    run_git = lambda do |*arguments|
      stdout, stderr, status = Open3.capture3('git', '-C', repository, *arguments)
      assert status.success?, stderr
      stdout.strip
    end
    run_git.call('init', '--quiet')
    run_git.call('config', 'user.name', 'Fable capture test')
    run_git.call('config', 'user.email', 'fable-capture@example.invalid')
    File.write(File.join(repository, 'bound.txt'), "capture identity\n")
    run_git.call('add', 'bound.txt')
    run_git.call('commit', '--quiet', '-m', 'capture identity')
    expected_head = run_git.call('rev-parse', 'HEAD')
    expected_tree = run_git.call('rev-parse', 'HEAD^{tree}')
    identity = DurableCommandCapture.capture_execution_identity(
      repository_path: repository, worktree_path: repository
    )
    path = capture_path('execution_identity')

    DurableCommandCapture.run_and_capture(
      ['git', 'rev-parse', 'HEAD'], file_path: path, chdir: repository,
      execution_identity: identity
    )
    captured = DurableCommandCapture.load(path)

    assert_equal expected_head, captured.stdout.strip
    assert_equal expected_head, captured.execution_identity['head']
    assert_equal expected_tree, captured.execution_identity['tree']
    assert_equal File.realpath(repository), captured.execution_identity['repository_path']
    assert_equal File.realpath(repository), captured.execution_identity['worktree_path']
    launcher = File.realpath(File.expand_path('../scripts/task_checkpoint.rb', __dir__))
    assert_equal launcher, captured.execution_identity['launcher_path']
    assert_equal Digest::SHA256.file(launcher).hexdigest, captured.execution_identity['launcher_sha256']
    assert_equal File.realpath(RbConfig.ruby), captured.execution_identity['runtime_executable']
    assert_equal RUBY_DESCRIPTION, captured.execution_identity['runtime_version']
  end

  # F. Durable Judge failure: non-zero exit preserved, verdict can never be PASS.
  def test_f_durable_failure_preserves_nonzero_exit_and_cannot_pass
    path = capture_path('failure')
    DurableCommandCapture.run_and_capture(['ruby', '-e', 'warn "boom"; exit 7'], file_path: path)

    reloaded = DurableCommandCapture.load(path)
    assert_equal 7, reloaded.exit_status
    assert_includes reloaded.stderr, 'boom'
    assert_equal DurableCommandCapture::VERDICT_FAIL, reloaded.verdict
    refute_equal DurableCommandCapture::VERDICT_PASS, reloaded.verdict
  end

  # G. Interrupted / incomplete evidence: never fabricate PASS/FAIL; unaffected
  # evidence stays reusable.
  def test_g_incomplete_evidence_is_unknown_unverifiable_not_fabricated
    incomplete_path = capture_path('incomplete')
    FileUtils.mkdir_p(File.dirname(incomplete_path))
    File.write(incomplete_path, JSON.generate('schema_version' => 1, 'command' => ['ruby', '-e', '1']))

    assert_equal DurableCommandCapture::EVIDENCE_UNKNOWN_UNVERIFIABLE,
                 DurableCommandCapture.classify_evidence(incomplete_path)
    reloaded = DurableCommandCapture.load(incomplete_path)
    assert_equal DurableCommandCapture::VERDICT_UNKNOWN_UNVERIFIABLE, reloaded.verdict
    refute_equal DurableCommandCapture::VERDICT_PASS, reloaded.verdict
    refute_equal DurableCommandCapture::VERDICT_FAIL, reloaded.verdict

    missing_path = capture_path('does_not_exist')
    assert_equal DurableCommandCapture::EVIDENCE_UNKNOWN_UNVERIFIABLE,
                 DurableCommandCapture.classify_evidence(missing_path)

    good_path = capture_path('unaffected')
    DurableCommandCapture.run_and_capture(['ruby', '-e', 'exit 0'], file_path: good_path)
    assert_equal DurableCommandCapture::EVIDENCE_COMPLETE, DurableCommandCapture.classify_evidence(good_path)
  end

  # H. Stream independence: the durable file alone reconstructs the result.
  def test_h_durable_record_survives_discarding_in_memory_result
    path = capture_path('stream_independence')
    DurableCommandCapture.run_and_capture(['ruby', '-e', 'puts "durable"; exit 0'], file_path: path)

    reloaded = DurableCommandCapture.load(path)
    assert_equal 0, reloaded.exit_status
    assert_includes reloaded.stdout, 'durable'
  end

  # I. Secret discipline: no automatic ENV capture.
  def test_i_no_automatic_environment_capture
    sentinel = "FABLE_TEST_SENTINEL_#{rand(1_000_000)}"
    path = capture_path('secret_not_printed')
    begin
      ENV['FABLE_TEST_SECRET_SENTINEL'] = sentinel
      DurableCommandCapture.run_and_capture(['ruby', '-e', 'puts "quiet"'], file_path: path)
      refute_includes File.read(path), sentinel
    ensure
      ENV.delete('FABLE_TEST_SECRET_SENTINEL')
    end
  end

  # I. Secret discipline: a command that deliberately prints a secret is
  # captured verbatim, same as any other output — no new redaction system.
  def test_i_secret_appears_only_when_command_deliberately_prints_it
    sentinel = "FABLE_TEST_SENTINEL_#{rand(1_000_000)}"
    path = capture_path('secret_printed')
    begin
      ENV['FABLE_TEST_SECRET_SENTINEL'] = sentinel
      DurableCommandCapture.run_and_capture(['ruby', '-e', 'print ENV["FABLE_TEST_SECRET_SENTINEL"]'], file_path: path)
      reloaded = DurableCommandCapture.load(path)
      assert_includes reloaded.stdout, sentinel
    ensure
      ENV.delete('FABLE_TEST_SECRET_SENTINEL')
    end
  end

  def test_signal_during_capture_to_record_commit_is_deferred
    capture_file = capture_path('signal_during_commit')
    record_file = File.join(@tmpdir, 'executions', 'signal_during_commit.json')
    command = ['ruby', '-e', 'puts "durable"; exit 0']

    capture = DurableCommandCapture.run_and_capture(command, file_path: capture_file) do |candidate|
      assert File.file?(capture_file), 'capture must be saved before record finalization'
      Process.kill('INT', Process.pid)
      Process.kill('TERM', Process.pid)

      record = ExecutionRecord.start!(record_file, task_id: 'SIGNAL_TASK',
                                      execution_id: 'signal_during_commit', pid: Process.pid)
      record.complete!(record_file, durable_capture_path: capture_file)
      assert_equal ExecutionRecord::STATUS_COMPLETED, ExecutionRecord.load(record_file).status
      assert_equal candidate.exit_status, DurableCommandCapture.load(capture_file).exit_status
    end

    assert_equal Signal.list.fetch('INT'), capture.wrapper_signal
    assert_equal DurableCommandCapture::EVIDENCE_COMPLETE,
                 DurableCommandCapture.classify_evidence(capture_file)
    refute_includes File.read(capture_file), 'wrapper_signal'
  end
end

class TaskCheckpointRunTest < Minitest::Test
  include SyntheticGitRepo

  CLI = File.expand_path('../scripts/task_checkpoint.rb', __dir__)
  CLI_TIMEOUT_SECONDS = 15
  TASK_ID = 'CLI_TASK'
  UPSTREAM = <<~'RUBY'
    launches, release, result = ARGV
    File.open(launches, 'a') { |file| file.puts "#{Process.pid}:#{Process.ppid}" }
    STDOUT.write("out\0尾\n")
    STDERR.write("err\0尾\n")
    STDOUT.flush
    STDERR.flush
    unless release == '-'
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 30
      until File.file?(release)
        abort 'upstream barrier timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
        sleep 0.01
      end
    end
    result == 'TERM' ? Process.kill('TERM', Process.pid) : exit(Integer(result))
  RUBY
  HANDLED_TERM_UPSTREAM = <<~'RUBY'
    launches, release = ARGV
    File.open(launches, 'a') { |file| file.puts "#{Process.pid}:#{Process.ppid}" }
    Signal.trap('TERM') do
      STDOUT.write("handled-out\n")
      STDERR.write("handled-err\n")
      STDOUT.flush
      STDERR.flush
      exit 143
    end
    STDOUT.write("out\0尾\n")
    STDERR.write("err\0尾\n")
    STDOUT.flush
    STDERR.flush
    until File.file?(release)
      sleep 0.01
    end
    exit 0
  RUBY
  DESCENDANT_UPSTREAM = <<~'RUBY'
    descendant_path, launches, ruby_exe = ARGV
    File.open(launches, 'a') { |file| file.puts "#{Process.pid}:#{Process.ppid}" }
    descendant_pid = Process.spawn(ruby_exe, '-e', 'sleep 30')
    File.write(descendant_path, descendant_pid.to_s)
    STDOUT.write("out\0尾\n")
    STDERR.write("err\0尾\n")
    STDOUT.flush
    STDERR.flush
    sleep 30
  RUBY
  STREAM_RACE_UPSTREAM = <<~'RUBY'
    launches, release = ARGV
    received = false
    Signal.trap('INT') { received = true }
    Signal.trap('TERM') { received = true }
    File.open(launches, 'a') { |file| file.puts "#{Process.pid}:#{Process.ppid}" }
    until File.file?(release)
      sleep 0.001
    end
    64.times do |index|
      STDOUT.write("out#{index}:#{'o' * 32768}\n")
      STDERR.write("err#{index}:#{'e' * 32768}\n")
      STDOUT.flush
      STDERR.flush
      sleep 0.002
    end
    exit(received ? 130 : 0)
  RUBY
  RECOVERY_UPSTREAM = <<~'RUBY'
    state_path, transition_path, observation_path, launches = ARGV
    transition = JSON.parse(File.read(transition_path))
    observation = {
      'state_sha256' => Digest::SHA256.hexdigest(File.binread(state_path)),
      'old_execution_id' => transition.fetch('old_execution_id'),
      'successor_execution_id' => transition.fetch('successor_execution_id'),
      'old_disposition' => transition.fetch('old_disposition'),
      'worktree_path' => transition.fetch('worktree_path'),
      'observed_working_directory' => File.realpath(Dir.pwd),
      'successor_command_sha256' => transition.fetch('successor_command_sha256')
    }
    File.write(observation_path, JSON.generate(observation))
    File.open(launches, 'a') { |file| file.puts Process.pid }
    STDOUT.write("protected recovery\n")
    exit 0
  RUBY
  RECOVERY_BARRIER_UPSTREAM = <<~'RUBY'
    state_path, transition_path, observation_path, launches, release = ARGV
    transition = JSON.parse(File.read(transition_path))
    File.write(observation_path, Digest::SHA256.hexdigest(File.binread(state_path)))
    File.open(launches, 'a') { |file| file.puts Process.pid }
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 8
    until File.file?(release)
      abort 'recovery barrier timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
      sleep 0.01
    end
    STDOUT.write("protected recovery\n")
    exit 0
  RUBY
  # A synthetic protected application that launches further protected runs.
  # Like a well-behaved application, it forwards the named inherited task-lock
  # descriptor unless a step models one that closes its descriptors.
  NESTED_PARENT_UPSTREAM = <<~'RUBY'
    spec = JSON.parse(File.read(ARGV.fetch(0)))
    File.open(spec.fetch('launches'), 'a') { |file| file.puts "#{Process.pid}:#{Process.ppid}" }
    await = lambda do |path|
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 20
      until File.file?(path)
        abort "nested parent barrier timed out: #{path}" if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
        sleep 0.01
      end
    end
    spec.fetch('steps').each do |step|
      await.call(step['go']) if step['go']
      options = {}
      fd = ENV['FABLE_PROTECTED_RUN_LOCK_FD']
      if !step.fetch('pass_fd')
        options[:close_others] = true
      elsif fd
        options[Integer(fd)] = Integer(fd)
      end
      stdout, stderr, status = Open3.capture3(step.fetch('env'), *step.fetch('argv'), options)
      temp = "#{step.fetch('result')}.tmp"
      File.write(temp, JSON.generate('stdout' => stdout, 'stderr' => stderr, 'exitstatus' => status.exitstatus))
      File.rename(temp, step.fetch('result'))
    end
    await.call(spec['release']) if spec['release']
    STDOUT.write("nested parent done\n")
    exit 0
  RUBY
  ENV_DUMP_UPSTREAM = <<~'RUBY'
    dump, launches, release = ARGV
    File.write("#{dump}.tmp", JSON.generate(ENV.to_h.select { |key, _| key.start_with?('FABLE_PROTECTED_RUN_') }))
    File.rename("#{dump}.tmp", dump)
    File.open(launches, 'a') { |file| file.puts "#{Process.pid}:#{Process.ppid}" }
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 8
    until File.file?(release)
      abort 'env dump barrier timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
      sleep 0.01
    end
    exit 0
  RUBY
  NESTED_LEAF_UPSTREAM = <<~'RUBY'
    launches, label = ARGV
    File.open(launches, 'a') { |file| file.puts "#{Process.pid}:#{Process.ppid}" }
    STDOUT.write("leaf #{label}\n")
    exit 0
  RUBY

  def setup
    @tmpdir = TaskCheckpointTestScratch.mktmpdir('task_checkpoint_run_test_')
    @repo = File.join(@tmpdir, 'stable-record-root')
    @worktree = File.join(@tmpdir, 'upstream-cwd')
    @caller = File.join(@tmpdir, 'caller-cwd')
    [@repo, @worktree, @caller].each { |path| FileUtils.mkdir_p(path) }
    _init_out, init_err, init_status = Open3.capture3('git', '-C', @repo, 'init', '--quiet')
    assert init_status.success?, init_err
    _config_out, config_err, config_status = Open3.capture3('git', '-C', @repo, 'config', 'user.name', 'Fable Test')
    assert config_status.success?, config_err
    _email_out, email_err, email_status = Open3.capture3(
      'git', '-C', @repo, 'config', 'user.email', 'fable-test@example.invalid'
    )
    assert email_status.success?, email_err
    _commit_out, commit_err, commit_status = Open3.capture3(
      'git', '-C', @repo, 'commit', '--allow-empty', '--quiet', '-m', 'synthetic base'
    )
    assert commit_status.success?, commit_err
    StableLockAnchor.initialize_repository_generation!(@repo, confirm_legacy_workers_quiescent: true)
    ExecutionRecord.with_task_lock(@repo, TASK_ID) { |_lock| }
    @launches = File.join(@tmpdir, 'launches')
    @release = File.join(@tmpdir, 'release')
    @application_state = File.join(@tmpdir, 'application-checkpoint.bin')
    @recovery_observation = File.join(@tmpdir, 'recovery-observation.json')
    File.binwrite(@application_state, "synthetic-checkpoint\0v1\n")
    @children = []
    @sentinels = []
    @managed_pids = []
  end

  def teardown
    @sentinels.each do |pid|
      begin
        Process.kill('TERM', pid)
      rescue Errno::ESRCH
        # The sentinel already observed an unexpected signal and exited.
      end
      begin
        Process.wait(pid)
      rescue Errno::ECHILD
        # The sentinel was already reaped.
      end
    end
    # Every CLI gets a test-owned process group. This also stops the tiny
    # upstream left behind by the intentional foreground-termination test.
    @children.each do |child|
      begin
        Process.kill('TERM', -child[:wait].pid)
      rescue Errno::ESRCH
        # The complete test-owned group has already exited.
      end
      assert child[:wait].join(5), 'test-owned CLI did not terminate'
      child[:readers].each { |reader| assert reader.join(5), 'test pipe did not close' }
    end
    launch_records.each do |line|
      pid = Integer(line.split(':').first)
      begin
        Process.kill('TERM', -pid)
      rescue Errno::ESRCH
        # The dedicated upstream group has already exited.
      end
      wait_until('test-owned upstream did not terminate') { ExecutionRecord.pid_alive?(pid) == false }
    end
    @managed_pids.each do |pid|
      begin
        Process.kill('TERM', pid)
      rescue Errno::ESRCH
        # The managed descendant already exited with its upstream group.
      end
      wait_until('test-owned managed descendant did not terminate') { ExecutionRecord.pid_alive?(pid) == false }
    end
    FileUtils.remove_entry(@tmpdir)
    refute File.exist?(@tmpdir), 'test temporary state must be deleted'
  end

  def wait_until(message)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 5
    until yield
      flunk message if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
      sleep 0.01
    end
  end

  def upstream(result: '0', barrier: false)
    [RbConfig.ruby, '-e', UPSTREAM, @launches, barrier ? @release : '-', result]
  end

  def handled_term_upstream
    [RbConfig.ruby, '-e', HANDLED_TERM_UPSTREAM, @launches, @release]
  end

  def descendant_upstream(descendant_path)
    [RbConfig.ruby, '-e', DESCENDANT_UPSTREAM, descendant_path, @launches, RbConfig.ruby]
  end

  def stream_race_upstream
    [RbConfig.ruby, '-e', STREAM_RACE_UPSTREAM, @launches, @release]
  end

  def recovery_upstream(identity)
    [RbConfig.ruby, '-rjson', '-rdigest', '-e', RECOVERY_UPSTREAM,
     @application_state, ExecutionRecoveryTransition.default_path(@repo, TASK_ID, identity),
     @recovery_observation, @launches]
  end

  def recovery_barrier_upstream(identity)
    [RbConfig.ruby, '-rjson', '-rdigest', '-e', RECOVERY_BARRIER_UPSTREAM,
     @application_state, ExecutionRecoveryTransition.default_path(@repo, TASK_ID, identity),
     @recovery_observation, @launches, @release]
  end

  def recovery_cli_args(identity, command = nil)
    command ||= recovery_upstream(identity)
    ['--recover-run', '--repo', @repo, '--worktree', @worktree,
     '--task-id', TASK_ID, '--execution-id', identity,
     '--application-state', @application_state, '--', *command]
  end

  # The retry branch's old recovery capability fixture is mapped to the
  # current inherited descriptor API; it grants no capability via history.
  def retry_nested_upstream(barrier: false)
    observer = <<~'RUBY'
      require ARGV.fetch(0)
      repo, task = ARGV.drop(1)
      inherited = ENV.to_h.select { |key, _| key.start_with?('FABLE_PROTECTED_RUN_') }
      lineage = JSON.parse(File.read(ExecutionRecord.task_lock_path(repo, task)))
      STDOUT.write(JSON.generate('inherited' => inherited, 'lineage' => lineage))
    RUBY
    command = [RbConfig.ruby, '-e', observer, CLI, @repo, TASK_ID]
    nested_parent([nested_step(inner_cli_args('nested_child', command), result: @recovery_observation)],
                  release: barrier ? @release : nil)
  end

  def retry_recovery_cli_args(prior_id, command)
    ['--retry-recover-run', '--repo', @repo, '--worktree', @worktree,
     '--task-id', TASK_ID, '--execution-id', prior_id,
     '--application-state', @application_state, '--', *command]
  end

  def cli_args(identity, command = upstream)
    ['--run', '--repo', @repo, '--worktree', @worktree,
     '--task-id', TASK_ID, '--execution-id', identity, '--', *command]
  end

  def start_cli(args, cwd: @caller, pgroup: true, env: nil, inherit_fds: [])
    spawn_env = env ? [env] : []
    spawn_opts = { chdir: cwd, pgroup: pgroup }
    inherit_fds.each { |fd| spawn_opts[fd] = fd }
    stdin, stdout, stderr, wait = Open3.popen3(*spawn_env, RbConfig.ruby, CLI, *args, spawn_opts)
    stdin.close
    readers = [stdout, stderr].map { |io| Thread.new { begin; io.read; ensure; io.close; end } }
    child = { wait: wait, readers: readers }
    @children << child
    child
  end

  def finish_cli(child)
    assert child[:wait].join(CLI_TIMEOUT_SECONDS), 'CLI timed out'
    child[:readers].each { |reader| assert reader.join(5), 'CLI output timed out' }
    [*child[:readers].map(&:value), child[:wait].value]
  end

  def run_cli(args, **options)
    finish_cli(start_cli(args, **options))
  end

  def record_path(identity)
    ExecutionRecord.default_path(@repo, TASK_ID, identity)
  end

  def capture_path(identity)
    DurableCommandCapture.default_path(@repo, TASK_ID, identity)
  end

  def recovery_transition_path(identity)
    ExecutionRecoveryTransition.default_path(@repo, TASK_ID, identity)
  end

  def successor_execution_id(identity)
    ExecutionRecoveryTransition.successor_id_for(TASK_ID, identity)
  end

  def dead_pid
    pid = Process.spawn(RbConfig.ruby, '-e', 'exit 0')
    Process.wait(pid)
    pid
  end

  def seed_stale_execution(identity, pid: dead_pid)
    ExecutionRecord.start!(record_path(identity), task_id: TASK_ID,
                           execution_id: identity, pid: pid)
  end

  def launch_records
    File.file?(@launches) ? File.readlines(@launches) : []
  end

  def assert_output(result, exit_status)
    assert_equal "out\0尾\n", result[0]
    assert_equal "err\0尾\n", result[1]
    assert_equal exit_status, result[2].exitstatus
  end

  def assert_signal_result(result, signal_name)
    assert result[2].signaled?, "expected #{signal_name}, got #{result[2].inspect}"
    assert_equal Signal.list.fetch(signal_name), result[2].termsig
  end

  def start_group_sentinel(group_pid)
    marker = File.join(@tmpdir, "sentinel_#{@sentinels.length}.signal")
    script = <<~'RUBY'
      marker = ARGV.fetch(0)
      %w[INT TERM].each do |name|
        Signal.trap(name) do
          File.write(marker, name)
          exit 99
        end
      end
      loop { sleep 1 }
    RUBY
    pid = Process.spawn(RbConfig.ruby, '-e', script, marker, pgroup: group_pid)
    @sentinels << pid
    [pid, marker]
  end

  def assert_wrapper_signal_finalization(identity, signal_name)
    command = upstream(barrier: true)
    owner = start_cli(cli_args(identity, command))
    wait_until('test upstream did not start') { !launch_records.empty? }
    upstream_pid = Integer(launch_records.first.split(':').first)

    Process.kill(signal_name, owner[:wait].pid)
    result = finish_cli(owner)
    assert result[0].start_with?("out\0尾\n")
    assert result[1].start_with?("err\0尾\n")
    assert_signal_result(result, signal_name)
    wait_until('forwarded upstream did not terminate') { ExecutionRecord.pid_alive?(upstream_pid) == false }

    record = ExecutionRecord.load(record_path(identity))
    assert_equal ExecutionRecord::STATUS_COMPLETED, record.status
    assert_equal owner[:wait].pid, record.pid
    assert_equal capture_path(identity), record.durable_capture_path
    capture = DurableCommandCapture.load(capture_path(identity))
    assert_equal "SIGNALED:#{Signal.list.fetch(signal_name)}", capture.exit_status
    assert_equal command, capture.command
    assert_equal [result[0], result[1]], [capture.stdout, capture.stderr]
    assert_operator Time.iso8601(record.ended_at), :>=, Time.iso8601(capture.ended_at)

    before = [File.binread(record_path(identity)), File.binread(capture_path(identity))]
    replay = run_cli(cli_args(identity, command), cwd: @worktree)
    assert_equal result[0, 2], replay[0, 2]
    assert_signal_result(replay, signal_name)
    assert_equal 1, launch_records.size, 'signaled replay must not relaunch upstream'
    assert_equal before, [File.binread(record_path(identity)), File.binread(capture_path(identity))]
  end

  def test_run_wrapper_sigint_finalizes_capture_before_propagating
    assert_wrapper_signal_finalization('wrapper_sigint', 'INT')
  end

  def test_run_wrapper_sigterm_finalizes_capture_before_propagating
    assert_wrapper_signal_finalization('wrapper_sigterm', 'TERM')
  end

  def test_run_child_handled_sigterm_preserves_integer_exit_status
    identity = 'handled_term'
    command = handled_term_upstream
    owner = start_cli(cli_args(identity, command))
    wait_until('signal-handling upstream did not start') { !launch_records.empty? }

    Process.kill('TERM', owner[:wait].pid)
    result = finish_cli(owner)
    assert_signal_result(result, 'TERM')
    assert_includes result[0], "out\0尾\n"
    assert_includes result[0], "handled-out\n"
    assert_includes result[1], "err\0尾\n"
    assert_includes result[1], "handled-err\n"

    record = ExecutionRecord.load(record_path(identity))
    capture = DurableCommandCapture.load(record.durable_capture_path)
    assert_equal ExecutionRecord::STATUS_COMPLETED, record.status
    assert_equal 143, capture.exit_status
    assert_instance_of Integer, capture.exit_status
    assert_equal 1, launch_records.size
  end

  def test_run_foreground_group_signal_does_not_hit_unrelated_sentinel
    identity = 'foreground_isolation'
    owner = start_cli(cli_args(identity, upstream(barrier: true)))
    wait_until('foreground-isolation upstream did not start') { !launch_records.empty? }
    sentinel_pid, marker = start_group_sentinel(owner[:wait].pid)
    wait_until('foreground-isolation sentinel did not start') { ExecutionRecord.pid_alive?(sentinel_pid) }

    Process.kill('INT', owner[:wait].pid)
    result = finish_cli(owner)
    assert_signal_result(result, 'INT')
    wait_until('foreground-isolation upstream did not terminate') do
      ExecutionRecord.pid_alive?(Integer(launch_records.first.split(':').first)) == false
    end
    assert File.exist?(marker) == false, 'forwarding must not target the wrapper foreground group'
    assert ExecutionRecord.pid_alive?(sentinel_pid), 'unrelated sentinel must remain alive'
    assert_equal ExecutionRecord::STATUS_COMPLETED, ExecutionRecord.load(record_path(identity)).status
  end

  def test_run_owned_group_cleanup_removes_managed_descendant
    identity = 'owned_group_cleanup'
    descendant_path = File.join(@tmpdir, 'descendant_pid')
    command = descendant_upstream(descendant_path)
    owner = start_cli(cli_args(identity, command))
    wait_until('owned-group upstream did not start') { !launch_records.empty? }
    wait_until('managed descendant did not start') { File.file?(descendant_path) }
    upstream_pid = Integer(launch_records.first.split(':').first)
    descendant_pid = Integer(File.read(descendant_path))
    @managed_pids << descendant_pid

    Process.kill('INT', owner[:wait].pid)
    result = finish_cli(owner)
    assert_signal_result(result, 'INT')
    wait_until('owned upstream child was not reaped') { ExecutionRecord.pid_alive?(upstream_pid) == false }
    wait_until('managed descendant escaped its owned group') { ExecutionRecord.pid_alive?(descendant_pid) == false }
    record = ExecutionRecord.load(record_path(identity))
    assert_equal ExecutionRecord::STATUS_COMPLETED, record.status
    assert_equal "SIGNALED:#{Signal.list.fetch('INT')}", DurableCommandCapture.load(record.durable_capture_path).exit_status
  end

  def test_run_repeated_signals_while_draining_do_not_skip_finalization
    identity = 'repeated_signals'
    command = stream_race_upstream
    owner = start_cli(cli_args(identity, command))
    wait_until('stream-race upstream did not start') { !launch_records.empty? }

    Process.kill('INT', owner[:wait].pid)
    spammer = Thread.new do
      while owner[:wait].alive?
        %w[TERM INT].each do |name|
          begin
            Process.kill(name, owner[:wait].pid)
          rescue Errno::ESRCH
            break
          end
        end
        sleep 0.001
      end
    end
    File.write(@release, 'go')
    result = finish_cli(owner)
    assert spammer.join(5), 'signal spammer did not stop'
    assert result[2].signaled?
    assert_includes [Signal.list.fetch('INT'), Signal.list.fetch('TERM')], result[2].termsig
    assert_includes result[0], 'out0:'
    assert_includes result[1], 'err0:'

    record = ExecutionRecord.load(record_path(identity))
    capture = DurableCommandCapture.load(record.durable_capture_path)
    assert_equal ExecutionRecord::STATUS_COMPLETED, record.status
    assert_equal 130, capture.exit_status
    assert_instance_of Integer, capture.exit_status
    assert_equal result[0], capture.stdout
    assert_equal result[1], capture.stderr
    assert_equal 1, launch_records.size
  end

  def test_run_cli_persists_candidate_identity_for_the_generation2_locked_worktree
    identity = 'candidate_identity'
    command = [RbConfig.ruby, '-e', 'puts Dir.pwd']
    args = ['--run', '--repo', @repo, '--worktree', @repo,
            '--task-id', TASK_ID, '--execution-id', identity, '--', *command]

    result = run_cli(args)

    assert_equal 0, result[2].exitstatus, "stdout=#{result[0].inspect} stderr=#{result[1].inspect}"
    capture = DurableCommandCapture.load(capture_path(identity))
    candidate = capture.execution_identity
    assert_equal File.realpath(@repo), candidate.fetch('repository_path')
    assert_equal File.realpath(@repo), candidate.fetch('worktree_path')
    assert_equal Open3.capture2('git', '-C', @repo, 'rev-parse', 'HEAD').first.strip,
                 candidate.fetch('head')
    assert_equal Open3.capture2('git', '-C', @repo, 'rev-parse', 'HEAD^{tree}').first.strip,
                 candidate.fetch('tree')
    launcher = File.realpath(CLI)
    assert_equal launcher, candidate.fetch('launcher_path')
    assert_equal Digest::SHA256.file(launcher).hexdigest, candidate.fetch('launcher_sha256')
    assert_equal File.realpath(RbConfig.ruby), candidate.fetch('runtime_executable')
    assert_equal RUBY_DESCRIPTION, candidate.fetch('runtime_version')
    assert_equal File.realpath(@repo), result[0].strip
  end

  def test_run_real_cli_two_contenders_and_completed_replay
    identity = 'concurrent'
    command = upstream(barrier: true)
    contenders = 2.times.map { start_cli(cli_args(identity, command)) }
    wait_until('upstream did not start') { !launch_records.empty? }
    wait_until('loser did not stop while winner was active') do
      launch_records.size > 1 || contenders.any? { |child| !child[:wait].alive? }
    end
    assert_equal 1, launch_records.size, 'only one contender may invoke upstream'
    loser = contenders.find { |child| !child[:wait].alive? }
    winner = (contenders - [loser]).first
    lost = finish_cli(loser)
    assert_equal '', lost[0]
    assert_equal 1, lost[2].exitstatus
    assert_includes lost[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    record = ExecutionRecord.load(record_path(identity))
    assert_equal winner[:wait].pid, record.pid, 'foreground CLI must own the record'
    assert_equal record.pid, Integer(launch_records.first.split(':').last), 'upstream must be a child of the owner'
    assert_equal ExecutionRecord::STATUS_STARTED, record.status
    refute File.exist?(capture_path(identity)), 'capture is not terminal while upstream waits'

    File.write(@release, 'go')
    original = finish_cli(winner)
    assert_output(original, 0)
    record = ExecutionRecord.load(record_path(identity))
    assert_equal ExecutionRecord::STATUS_COMPLETED, record.status
    assert_equal capture_path(identity), record.durable_capture_path
    capture = DurableCommandCapture.load(record.durable_capture_path)
    assert capture.complete?
    assert_equal command, capture.command
    assert_equal original[0, 2], [capture.stdout, capture.stderr]
    assert_equal File.realpath(@repo), capture.execution_identity['repository_path']
    assert_equal File.realpath(@worktree), capture.execution_identity['worktree_path']
    assert_nil capture.execution_identity['head'], 'non-Git worktrees must remain explicitly unavailable'
    assert_nil capture.execution_identity['tree'], 'non-Git worktrees must remain explicitly unavailable'
    launcher = File.realpath(CLI)
    assert_equal launcher, capture.execution_identity['launcher_path']
    assert_equal Digest::SHA256.file(launcher).hexdigest, capture.execution_identity['launcher_sha256']
    assert_equal File.realpath(RbConfig.ruby), capture.execution_identity['runtime_executable']
    assert_equal RUBY_DESCRIPTION, capture.execution_identity['runtime_version']
    before = [File.binread(record_path(identity)), File.binread(capture_path(identity))]
    replay = run_cli(cli_args(identity, command), cwd: @worktree)
    assert_output(replay, 0)
    assert_equal original[0, 2], replay[0, 2]
    assert_equal 1, launch_records.size, 'third invocation must reuse the durable result'
    assert_equal before, [File.binread(record_path(identity)), File.binread(capture_path(identity))]
    [@caller, @worktree].each { |path| refute File.exist?(File.join(path, '.fable')) }
  end

  def test_run_rejects_different_execution_id_while_task_writer_is_active
    first = start_cli(cli_args('writer_first', upstream(barrier: true)))
    wait_until('first task writer did not start') { !launch_records.empty? }

    second = run_cli(cli_args('writer_second'))

    assert_equal 1, second[2].exitstatus
    assert_includes second[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    assert_equal 1, launch_records.size
    File.write(@release, 'go')
    first_result = finish_cli(first)
    assert_output(first_result, 0)
    assert_equal 1, launch_records.size
  end

  def test_stash_u_lock_replacement_cannot_admit_a_second_task_writer
    identity = 'stash_lock_owner'
    owner = start_cli(cli_args(identity, upstream(barrier: true)))
    wait_until('task-lock owner did not start') { !launch_records.empty? }
    record_before = File.binread(record_path(identity))
    stable_lock_path = ExecutionRecord.task_lock_path(@repo, TASK_ID)
    stable_lock_identity = [File.stat(stable_lock_path).dev, File.stat(stable_lock_path).ino]

    legacy_path = ExecutionRecord.legacy_task_lock_path(@repo, TASK_ID)
    FileUtils.mkdir_p(File.dirname(legacy_path))
    legacy_bytes = JSON.generate('schema_version' => ExecutionRecord::SCHEMA_VERSION,
                                 'task_id' => TASK_ID, 'record_root' => File.realpath(@repo),
                                 'execution_id' => 'legacy_fixture', 'capability_sha256' => 'a' * 64)
    File.write(legacy_path, legacy_bytes)
    legacy_descriptor = File.open(legacy_path, File::RDWR)
    legacy_identity = [legacy_descriptor.stat.dev, legacy_descriptor.stat.ino]

    _stash_out, stash_err, stash_status = Open3.capture3(
      'git', '-C', @repo, 'stash', 'push', '--include-untracked', '--message', 'task lock replacement regression'
    )
    assert stash_status.success?, stash_err
    refute File.exist?(record_path(identity))
    refute File.exist?(legacy_path)
    assert_equal stable_lock_identity, [File.stat(stable_lock_path).dev, File.stat(stable_lock_path).ino]

    _pop_out, pop_err, pop_status = Open3.capture3('git', '-C', @repo, 'stash', 'pop')
    assert pop_status.success?, pop_err
    assert_equal legacy_bytes, File.binread(legacy_path)
    refute_equal legacy_identity, [File.stat(legacy_path).dev, File.stat(legacy_path).ino]
    assert_equal record_before, File.binread(record_path(identity))
    assert_equal ExecutionRecord::CLASSIFICATION_ACTIVE, ExecutionRecord.load(record_path(identity)).classify
    assert_equal stable_lock_identity, [File.stat(stable_lock_path).dev, File.stat(stable_lock_path).ino]
    File.open(stable_lock_path, File::RDWR) do |probe|
      refute probe.flock(File::LOCK_EX | File::LOCK_NB), 'Worker A still owns the stable lock inode'
    end

    legacy_overlap = run_cli(cli_args('legacy_generation_probe'))
    assert_equal 1, legacy_overlap[2].exitstatus
    assert_includes legacy_overlap[1], 'migration gate required'
    assert_equal 1, launch_records.size
    FileUtils.rm_f(legacy_path)
    legacy_descriptor.close

    second_while_active = run_cli(cli_args('writer_second'))
    assert_equal 1, second_while_active[2].exitstatus
    assert_includes second_while_active[1], 'another execution claim in progress'
    assert_equal 1, launch_records.size

    File.write(@release, 'go')
    assert_output(finish_cli(owner), 0)
    FileUtils.rm_f(@release)

    successors = 2.times.map do |index|
      start_cli(cli_args("successor_#{index}", upstream(barrier: true)))
    end
    wait_until('successor ownership was not resolved') do
      launch_records.size >= 2 && successors.any? { |child| !child[:wait].alive? }
    end
    assert_equal 2, launch_records.size, 'normal release allows one task-scoped successor'
    loser = successors.find { |child| !child[:wait].alive? }
    winner = (successors - [loser]).first
    loser_result = finish_cli(loser)
    assert_equal 1, loser_result[2].exitstatus
    assert(loser_result[1].include?('another execution claim in progress') ||
           loser_result[1].include?(ExecutionRecord::CLASSIFICATION_ACTIVE), loser_result[1])
    File.write(@release, 'go')
    assert_output(finish_cli(winner), 0)
    assert_equal 2, launch_records.size
  end

  def test_legacy_writer_hidden_by_stash_cannot_bootstrap_a_new_generation
    legacy_repo = File.join(@tmpdir, 'uninitialized-generation-repo')
    initialize_synthetic_git_repo(legacy_repo, lock_generation: false)
    legacy_task_id = 'V1_STASH_TASK'
    legacy_path = ExecutionRecord.legacy_task_lock_path(legacy_repo, legacy_task_id)
    FileUtils.mkdir_p(File.dirname(legacy_path))
    assert_raises(StableLockAnchor::MigrationRequired) do
      StableLockAnchor.initialize_repository_generation!(legacy_repo)
    end

    File.open(legacy_path, File::RDWR | File::CREAT, 0o600) do |legacy_owner|
      assert legacy_owner.flock(File::LOCK_EX | File::LOCK_NB)
      legacy_owner.write('live generation-1 owner')
      legacy_owner.flush
      legacy_inode = [legacy_owner.stat.dev, legacy_owner.stat.ino]

      _stash_out, stash_err, stash_status = Open3.capture3(
        'git', '-C', legacy_repo, 'stash', 'push', '--include-untracked', '--message', 'hide legacy lock regression'
      )
      assert stash_status.success?, stash_err
      refute File.exist?(legacy_path)

      args = cli_args('new_generation_overlap')
      args[args.index('--repo') + 1] = legacy_repo
      args[args.index('--task-id') + 1] = legacy_task_id
      result = run_cli(args)
      assert_equal 1, result[2].exitstatus
      assert_includes result[1], 'migration gate required'
      assert_includes result[1], 'generation-2 lock marker'
      refute File.exist?(ExecutionRecord.task_lock_path(legacy_repo, legacy_task_id))
      refute File.exist?(ExecutionRecord.default_path(legacy_repo, legacy_task_id, 'new_generation_overlap'))
      assert_empty launch_records

      _pop_out, pop_err, pop_status = Open3.capture3('git', '-C', legacy_repo, 'stash', 'pop')
      assert pop_status.success?, pop_err
      assert File.exist?(legacy_path)
      refute_equal legacy_inode, [File.stat(legacy_path).dev, File.stat(legacy_path).ino]
      File.open(legacy_path, File::RDWR) do |replacement|
        assert replacement.flock(File::LOCK_EX | File::LOCK_NB),
               'the restored legacy path is an independent inode while the old owner still holds its descriptor'
        replacement.flock(File::LOCK_UN)
      end
      assert_equal legacy_inode, [legacy_owner.stat.dev, legacy_owner.stat.ino]
    end
  end

  def test_live_legacy_lock_generation_requires_migration_gate
    legacy_task_id = 'LEGACY_TASK'
    legacy_path = ExecutionRecord.legacy_task_lock_path(@repo, legacy_task_id)
    FileUtils.mkdir_p(File.dirname(legacy_path))
    File.open(legacy_path, File::RDWR | File::CREAT, 0o600) do |legacy_lock|
      assert legacy_lock.flock(File::LOCK_EX | File::LOCK_NB)
      legacy_lock.write('legacy generation')
      legacy_lock.flush

      args = cli_args('new_generation_overlap')
      args[args.index('--task-id') + 1] = legacy_task_id
      result = run_cli(args)
      assert_equal 1, result[2].exitstatus
      assert_includes result[1], 'migration gate required'
      assert_empty launch_records
      refute File.exist?(ExecutionRecord.task_lock_path(@repo, legacy_task_id))
      refute File.exist?(ExecutionRecord.default_path(@repo, legacy_task_id, 'new_generation_overlap'))
      File.open(legacy_path, File::RDWR) do |probe|
        refute probe.flock(File::LOCK_EX | File::LOCK_NB), 'the legacy owner remains untouched'
      end
    end
  end

  def test_separate_git_metadata_inside_worktree_is_rejected_before_lock_initialization
    repository = File.join(@tmpdir, 'separate-git-dir-repo')
    metadata = File.join(repository, 'metadata')
    FileUtils.mkdir_p(repository)
    _stdout, stderr, status = Open3.capture3(
      'git', 'init', '--quiet', '--separate-git-dir', metadata, repository
    )
    assert status.success?, stderr
    git = lambda do |*args|
      _out, error, result = Open3.capture3('git', '-C', repository, *args)
      raise "separate-git-dir fixture command failed: #{error}" unless result.success?
    end
    git.call('config', 'user.name', 'Fable Test')
    git.call('config', 'user.email', 'fable-test@example.invalid')
    File.write(File.join(repository, 'tracked'), "base\n")
    git.call('add', 'tracked')
    git.call('commit', '--quiet', '-m', 'synthetic separate git directory base')

    error = assert_raises(StableLockAnchor::MigrationRequired) do
      StableLockAnchor.initialize_repository_generation!(repository, confirm_legacy_workers_quiescent: true)
    end
    assert_includes error.message, 'inside stash-managed worktree'
    assert_includes error.message, 'move Git metadata outside the worktree'
    refute File.exist?(File.join(metadata, 'fable-locks'))

    execution_error = assert_raises(StableLockAnchor::MigrationRequired) do
      ExecutionRecord.with_task_lock(repository, 'unsafe_metadata_task') do
        flunk 'no task lock may be acquired under stash-managed common metadata'
      end
    end
    assert_includes execution_error.message, 'inside stash-managed worktree'
    refute File.exist?(File.join(repository, '.fable'))

    root_metadata_repo = File.join(@tmpdir, 'git-dir-is-worktree-root')
    FileUtils.mkdir_p(root_metadata_repo)
    _root_out, root_err, root_status = Open3.capture3(
      'git', 'init', '--quiet', '--separate-git-dir', root_metadata_repo, root_metadata_repo
    )
    assert root_status.success?, root_err
    root_error = assert_raises(StableLockAnchor::MigrationRequired) do
      StableLockAnchor.initialize_repository_generation!(root_metadata_repo,
                                                          confirm_legacy_workers_quiescent: true)
    end
    assert_includes root_error.message, 'inside stash-managed worktree'
    refute File.exist?(File.join(root_metadata_repo, 'fable-locks'))
  end

  def test_case_alias_cannot_bypass_physical_lock_anchor_containment
    repository = File.join(@tmpdir, 'FJ001CaseAliasRepo')
    repository_alias = File.join(@tmpdir, 'fj001casealiasrepo')
    FileUtils.mkdir_p(repository)
    skip 'case-insensitive filesystem is required for the FJ-001 alias regression' unless
      File.directory?(repository_alias) && File.identical?(repository, repository_alias)

    _stdout, stderr, status = Open3.capture3(
      'git', 'init', '--quiet', '--separate-git-dir', repository, repository
    )
    assert status.success?, stderr
    File.write(File.join(repository, '.git'), "gitdir: #{repository_alias}\n")

    top_level, top_level_err, top_level_status = Open3.capture3(
      'git', '-C', repository, 'rev-parse', '--show-toplevel'
    )
    assert top_level_status.success?, top_level_err
    assert_equal File.realpath(repository), File.realpath(top_level.strip)
    common_output, common_err, common_status = Open3.capture3(
      'git', '-C', repository, 'rev-parse', '--git-common-dir'
    )
    assert common_status.success?, common_err
    common_path = File.realpath(File.expand_path(common_output.strip, repository))
    assert File.identical?(repository, common_path), 'Git common-dir alias must resolve to the worktree root'
    skip 'filesystem canonicalized away the case alias before the lexical containment check' if
      File.realpath(repository) == common_path

    error = assert_raises(StableLockAnchor::MigrationRequired) do
      StableLockAnchor.initialize_repository_generation!(repository, confirm_legacy_workers_quiescent: true)
    end
    assert_includes error.message, 'inside stash-managed worktree'
    refute File.exist?(File.join(repository_alias, 'fable-locks'))
  end

  def test_task_lock_anchor_replacement_and_symlink_tampering_fail_closed
    ExecutionRecord.with_task_lock(@repo, 'distinct_scope_a') do
      ExecutionRecord.with_task_lock(@repo, 'distinct_scope_b') { assert true }
    end

    replacement_path = ExecutionRecord.task_lock_path(@repo, 'replace_anchor')
    ExecutionRecord.with_task_lock(@repo, 'replace_anchor') { assert File.file?(replacement_path) }
    File.unlink(replacement_path)
    File.write(replacement_path, 'replacement')
    error = assert_raises(StableLockAnchor::Error) do
      ExecutionRecord.with_task_lock(@repo, 'replace_anchor') { flunk 'replacement inode must not be acquired' }
    end
    assert_includes error.message, 'inode changed'

    symlink_path = ExecutionRecord.task_lock_path(@repo, 'symlink_anchor')
    ExecutionRecord.with_task_lock(@repo, 'symlink_anchor') { assert File.file?(symlink_path) }
    target = File.join(@tmpdir, 'symlink-lock-target')
    File.write(target, 'untouched')
    File.unlink(symlink_path)
    File.symlink(target, symlink_path)
    assert_raises(StableLockAnchor::Error) do
      ExecutionRecord.with_task_lock(@repo, 'symlink_anchor') { flunk 'symlink must not be acquired' }
    end
    assert_equal 'untouched', File.read(target)

    namespace_task = 'symlink_namespace'
    namespace_lock_path = ExecutionRecord.task_lock_path(@repo, namespace_task)
    ExecutionRecord.with_task_lock(@repo, namespace_task) { assert File.file?(namespace_lock_path) }
    namespace_root = File.dirname(File.dirname(namespace_lock_path))
    moved_namespace = "#{namespace_root}.moved"
    File.rename(namespace_root, moved_namespace)
    begin
      File.symlink(moved_namespace, namespace_root)
      opened = nil
      begin
        assert_raises(StableLockAnchor::Error) do
          opened = StableLockAnchor.open_existing_lock(@repo, 'task-execution', namespace_task,
                                                        filename: 'execution.lock')
        end
      ensure
        opened&.close
      end
      assert_raises(StableLockAnchor::Error) do
        ExecutionRecord.with_task_lock(@repo, namespace_task) { flunk 'symlinked namespace must not be trusted' }
      end
    ensure
      File.unlink(namespace_root) if File.symlink?(namespace_root)
      File.rename(moved_namespace, namespace_root) if File.directory?(moved_namespace)
    end
  end

  def test_run_nonzero_result_replay_does_not_launch_again
    args = cli_args('nonzero', upstream(result: '7'))
    original = run_cli(args)
    replay = run_cli(args)
    assert_output(original, 7)
    assert_output(replay, 7)
    assert_equal original[0, 2], replay[0, 2]
    assert_equal 7, DurableCommandCapture.load(capture_path('nonzero')).exit_status
    assert_equal 1, launch_records.size
  end

  def test_run_signaled_result_replays_the_signal_and_output
    args = cli_args('signaled', upstream(result: 'TERM'))
    results = [run_cli(args), run_cli(args)]
    results.each do |result|
      assert_equal "out\0尾\n", result[0]
      assert_equal "err\0尾\n", result[1]
      assert result[2].signaled?
      assert_equal Signal.list.fetch('TERM'), result[2].termsig
    end
    assert_equal "SIGNALED:#{Signal.list.fetch('TERM')}", DurableCommandCapture.load(capture_path('signaled')).exit_status
    assert_equal 1, launch_records.size
  end

  def test_run_terminated_foreground_retains_incomplete_record_without_rerun
    identity = 'terminated'
    args = cli_args(identity, upstream(barrier: true))
    owner = start_cli(args)
    wait_until('test upstream did not start') { !launch_records.empty? }
    before = File.binread(record_path(identity))
    # Abrupt loss must bypass Open3's TERM ensure, which waits for upstream.
    # Only this test-owned foreground PID is killed; the inherited task lease
    # blocks retry until the test releases the surviving application child.
    Process.kill('KILL', owner[:wait].pid)
    assert owner[:wait].join(5), 'killed wrapper was not reaped'
    assert owner[:wait].value.signaled?
    assert_equal Signal.list.fetch('KILL'), owner[:wait].value.termsig
    upstream_pid = Integer(launch_records.first.split(':').first)

    retry_while_child_live = run_cli(args)
    assert_equal 1, retry_while_child_live[2].exitstatus
    assert_includes retry_while_child_live[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    assert_equal 1, launch_records.size

    File.write(@release, 'go')
    wait_until('test upstream did not exit after release') { ExecutionRecord.pid_alive?(upstream_pid) == false }
    owner[:readers].each { |reader| assert reader.join(5), 'test upstream output pipe did not close' }

    retry_result = run_cli(args)
    assert_equal '', retry_result[0]
    assert_equal 1, retry_result[2].exitstatus
    assert_includes retry_result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    assert_equal before, File.binread(record_path(identity))
    assert_equal ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE, ExecutionRecord.load(record_path(identity)).classify
    assert_equal 1, launch_records.size
    refute File.exist?(capture_path(identity))
    assert File.file?(ExecutionRecord.task_lock_path(@repo, TASK_ID))
    worktree_files = Dir.glob(File.join(@repo, '.fable', '**', '*'), File::FNM_DOTMATCH)
                        .select { |path| File.file?(path) }.sort
    assert_equal [record_path(identity)], worktree_files
  end

  def test_recover_run_stale_dead_execution_preserves_history_and_checkpoint_before_child
    old_id = 'stale_fixture_a'
    seed_stale_execution(old_id)
    old_bytes = File.binread(record_path(old_id))
    state_bytes = File.binread(@application_state)

    result = run_cli(recovery_cli_args(old_id))

    assert_equal "protected recovery\n", result[0]
    assert_equal '', result[1]
    assert_equal 0, result[2].exitstatus
    successor_id = successor_execution_id(old_id)
    transition = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))
    assert_equal old_id, transition.old_execution_id
    assert_equal successor_id, transition.successor_execution_id
    assert_equal ExecutionRecoveryTransition::DISPOSITION_TERMINATED_INCOMPLETE, transition.old_disposition
    assert_equal Digest::SHA256.hexdigest(old_bytes), transition.old_execution_sha256
    assert_equal File.expand_path(@application_state), transition.application_state_path
    assert_equal Digest::SHA256.hexdigest(state_bytes), transition.application_state_sha256
    assert_equal File.realpath(@worktree), transition.worktree_path
    assert_equal Digest::SHA256.hexdigest(JSON.generate(recovery_upstream(old_id))),
                 transition.successor_command_sha256
    assert_equal old_bytes, File.binread(record_path(old_id)), 'the stale execution record remains immutable'
    assert_equal state_bytes, File.binread(@application_state), 'the recovery wrapper never edits application state'

    observed = JSON.parse(File.read(@recovery_observation))
    assert_equal Digest::SHA256.hexdigest(state_bytes), observed.fetch('state_sha256'),
                 'the child sees the exact bytes hashed before recovery'
    assert_equal old_id, observed.fetch('old_execution_id')
    assert_equal successor_id, observed.fetch('successor_execution_id')
    assert_equal ExecutionRecoveryTransition::DISPOSITION_TERMINATED_INCOMPLETE,
                 observed.fetch('old_disposition'), 'the durable link exists before child launch'
    assert_equal File.realpath(@worktree), observed.fetch('worktree_path')
    assert_equal observed.fetch('worktree_path'), observed.fetch('observed_working_directory')
    assert_equal transition.successor_command_sha256, observed.fetch('successor_command_sha256')
    assert_equal 1, launch_records.size
    successor = ExecutionRecord.load(record_path(successor_id))
    assert_equal ExecutionRecord::STATUS_COMPLETED, successor.status
    assert_equal recovery_upstream(old_id), DurableCommandCapture.load(successor.durable_capture_path).command
  end

  def test_recover_run_refuses_live_stale_pid
    old_id = 'stale_fixture_live'
    pid = Process.spawn(RbConfig.ruby, '-e', 'sleep 5')
    begin
      ExecutionRecord.start!(record_path(old_id), task_id: TASK_ID, execution_id: old_id, pid: pid)
      old_bytes = File.binread(record_path(old_id))

      result = run_cli(recovery_cli_args(old_id))

      assert_equal 1, result[2].exitstatus
      assert_includes result[1], ExecutionRecord::CLASSIFICATION_ACTIVE
      assert_equal old_bytes, File.binread(record_path(old_id))
      refute File.exist?(recovery_transition_path(old_id))
      assert_empty launch_records
    ensure
      Process.kill('TERM', pid)
      Process.wait(pid)
    end
  end

  def test_recover_run_refuses_competing_active_task_owner
    old_id = 'stale_fixture_competing'
    seed_stale_execution(old_id)
    competitor_id = 'other_active_execution'
    pid = Process.spawn(RbConfig.ruby, '-e', 'sleep 5')
    begin
      ExecutionRecord.start!(record_path(competitor_id), task_id: TASK_ID,
                             execution_id: competitor_id, pid: pid)

      result = run_cli(recovery_cli_args(old_id))

      assert_equal 1, result[2].exitstatus
      assert_includes result[1], ExecutionRecord::CLASSIFICATION_ACTIVE
      refute File.exist?(recovery_transition_path(old_id))
      assert_empty launch_records
    ensure
      Process.kill('TERM', pid)
      Process.wait(pid)
    end
  end

  def test_recover_run_refuses_terminal_old_execution
    old_id = 'terminal_old_execution'
    capture_file = capture_path(old_id)
    DurableCommandCapture.run_and_capture([RbConfig.ruby, '-e', 'exit 0'], file_path: capture_file)
    record = ExecutionRecord.start!(record_path(old_id), task_id: TASK_ID,
                                    execution_id: old_id, pid: dead_pid)
    record.complete!(record_path(old_id), durable_capture_path: capture_file)
    old_bytes = File.binread(record_path(old_id))

    result = run_cli(recovery_cli_args(old_id))

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    assert_equal old_bytes, File.binread(record_path(old_id))
    refute File.exist?(recovery_transition_path(old_id))
    assert_empty launch_records
  end

  def test_recover_run_fails_closed_for_missing_malformed_and_ambiguous_old_identity
    cases = [
      ['missing_old', :missing],
      ['malformed_old', :malformed],
      ['wrong_task_old', :wrong_task],
      ['missing_pid_old', :missing_pid]
    ]
    originals = {}
    cases.each do |identity, kind|
      path = record_path(identity)
      case kind
      when :malformed
        FileUtils.mkdir_p(File.dirname(path))
        File.binwrite(path, '{broken stale record')
      when :wrong_task
        ExecutionRecord.start!(path, task_id: 'OTHER_TASK', execution_id: identity, pid: dead_pid)
      when :missing_pid
        ExecutionRecord.start!(path, task_id: TASK_ID, execution_id: identity, pid: nil)
      end
      originals[identity] = File.binread(path) if File.file?(path)

      result = run_cli(recovery_cli_args(identity))

      assert_equal 1, result[2].exitstatus, kind.to_s
      assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED, kind.to_s
      refute File.exist?(recovery_transition_path(identity)), kind.to_s
      assert_equal originals[identity], File.binread(path) if originals.key?(identity)
    end
    assert_empty launch_records
  end

  def test_recover_caller_death_after_transition_reuses_same_successor
    old_id = 'caller_died_after_transition'
    seed_stale_execution(old_id)
    old_bytes = File.binread(record_path(old_id))
    library = File.expand_path('../scripts/task_checkpoint.rb', __dir__)
    script = <<~'RUBY'
      require ARGV.fetch(0)
      repo, task, old_id, state, worktree, command_json = ARGV.drop(1)
      ExecutionRecoveryTransition.reserve!(repo_root: repo, task_id: task,
                                            old_execution_id: old_id,
                                            application_state_path: state,
                                            worktree_path: worktree,
                                            command: JSON.parse(command_json))
      Process.kill('KILL', Process.pid)
    RUBY
    command_json = JSON.generate(recovery_upstream(old_id))
    pid = Process.spawn(RbConfig.ruby, '-e', script, library, @repo, TASK_ID, old_id,
                        @application_state, @worktree, command_json)
    _waited_pid, status = Process.wait2(pid)
    assert status.signaled?
    assert_equal Signal.list.fetch('KILL'), status.termsig
    assert old_bytes == File.binread(record_path(old_id))
    refute File.exist?(record_path(successor_execution_id(old_id)))
    transition_before = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))

    result = run_cli(recovery_cli_args(old_id))

    assert_equal 0, result[2].exitstatus, result[1]
    transition_after = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))
    assert_equal transition_before.successor_execution_id, transition_after.successor_execution_id
    assert_equal 1, launch_records.size
    assert_equal old_bytes, File.binread(record_path(old_id))
  end

  def test_recover_run_refuses_checkpoint_bytes_changed_after_transition
    old_id = 'changed_checkpoint_fixture'
    seed_stale_execution(old_id)
    transition = ExecutionRecoveryTransition.reserve!(repo_root: @repo, task_id: TASK_ID,
                                                       old_execution_id: old_id,
                                                       application_state_path: @application_state,
                                                       worktree_path: @worktree,
                                                       command: recovery_upstream(old_id))
    File.binwrite(@application_state, "unexpected-checkpoint-change\0v2\n")

    result = run_cli(recovery_cli_args(old_id))

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(record_path(transition.successor_execution_id))
    assert_empty launch_records
  end

  def test_recover_run_refuses_changed_application_state_locator
    old_id = 'changed_locator_fixture'
    seed_stale_execution(old_id)
    ExecutionRecoveryTransition.reserve!(repo_root: @repo, task_id: TASK_ID,
                                         old_execution_id: old_id,
                                         application_state_path: @application_state,
                                         worktree_path: @worktree,
                                         command: recovery_upstream(old_id))
    alternate_state = File.join(@tmpdir, 'alternate-application-checkpoint.bin')
    File.binwrite(alternate_state, File.binread(@application_state))
    args = recovery_cli_args(old_id)
    args[args.index('--application-state') + 1] = alternate_state

    result = run_cli(args)

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(record_path(successor_execution_id(old_id)))
    assert_empty launch_records
  end

  def test_recover_run_refuses_changed_original_command
    old_id = 'changed_command_fixture'
    seed_stale_execution(old_id)
    ExecutionRecoveryTransition.reserve!(repo_root: @repo, task_id: TASK_ID,
                                         old_execution_id: old_id,
                                         application_state_path: @application_state,
                                         worktree_path: @worktree,
                                         command: recovery_upstream(old_id))

    result = run_cli(recovery_cli_args(old_id, [RbConfig.ruby, '-e', 'exit 0']))

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(record_path(successor_execution_id(old_id)))
    assert_empty launch_records
  end

  def test_recover_run_refuses_changed_original_worktree
    old_id = 'changed_worktree_fixture'
    seed_stale_execution(old_id)
    ExecutionRecoveryTransition.reserve!(repo_root: @repo, task_id: TASK_ID,
                                         old_execution_id: old_id,
                                         application_state_path: @application_state,
                                         worktree_path: @worktree,
                                         command: recovery_upstream(old_id))
    alternate_worktree = File.join(@tmpdir, 'alternate-worktree')
    FileUtils.mkdir_p(alternate_worktree)
    args = recovery_cli_args(old_id)
    args[args.index('--worktree') + 1] = alternate_worktree

    result = run_cli(args)

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(record_path(successor_execution_id(old_id)))
    assert_empty launch_records
  end

  def test_normal_run_stale_record_remains_fail_closed_without_transition
    old_id = 'normal_run_stale_fixture'
    seed_stale_execution(old_id)
    old_bytes = File.binread(record_path(old_id))

    result = run_cli(cli_args(old_id))

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    assert_equal old_bytes, File.binread(record_path(old_id))
    refute File.exist?(recovery_transition_path(old_id))
    refute File.exist?(record_path(successor_execution_id(old_id)))
    assert_empty launch_records
  end

  def test_normal_run_cannot_bypass_unrecovered_stale_execution_with_new_id
    old_id = 'rotated_id_stale_fixture'
    new_id = 'rotated_id_bypass_fixture'
    seed_stale_execution(old_id)
    old_bytes = File.binread(record_path(old_id))

    result = run_cli(cli_args(new_id))

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    assert_equal old_bytes, File.binread(record_path(old_id))
    refute File.exist?(record_path(new_id))
    refute File.exist?(recovery_transition_path(old_id))
    assert_empty launch_records
  end

  def test_normal_run_cannot_bypass_reserved_recovery_transition_before_successor_exists
    old_id = 'reserved_transition_stale_fixture'
    new_id = 'reserved_transition_bypass_fixture'
    seed_stale_execution(old_id)
    ExecutionRecoveryTransition.reserve!(repo_root: @repo, task_id: TASK_ID,
                                         old_execution_id: old_id,
                                         application_state_path: @application_state,
                                         worktree_path: @worktree,
                                         command: recovery_upstream(old_id))

    result = run_cli(cli_args(new_id))

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(record_path(new_id))
    refute File.exist?(record_path(successor_execution_id(old_id)))
    assert_empty launch_records
  end

  def test_normal_run_replays_completed_recovery_successor
    old_id = 'completed_recovery_replay_fixture'
    seed_stale_execution(old_id)
    recovered = run_cli(recovery_cli_args(old_id))
    successor_id = successor_execution_id(old_id)

    # Replay with matching command succeeds
    replay = run_cli(cli_args(successor_id, recovery_upstream(old_id)))

    assert_equal 0, recovered[2].exitstatus
    assert_equal recovered[0], replay[0]
    assert_equal recovered[1], replay[1]
    assert_equal 0, replay[2].exitstatus
    assert_equal 1, launch_records.size, 'normal replay must reuse the completed explicit recovery successor'

    # Replay with mismatched command must be rejected
    mismatch_replay = run_cli(cli_args(successor_id))
    assert_equal 1, mismatch_replay[2].exitstatus
    assert_includes mismatch_replay[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
  end

  def test_duplicate_recovery_replays_one_terminal_successor
    old_id = 'duplicate_recovery_fixture'
    seed_stale_execution(old_id)

    first = run_cli(recovery_cli_args(old_id))
    transition_before = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))
    second = run_cli(recovery_cli_args(old_id))
    transition_after = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))

    assert_equal 0, first[2].exitstatus
    assert_equal first[0], second[0]
    assert_equal first[1], second[1]
    assert_equal 0, second[2].exitstatus
    assert_equal transition_before.successor_execution_id, transition_after.successor_execution_id
    assert_equal 1, launch_records.size, 'duplicate recovery reuses the terminal successor capture'
    records = Dir.glob(File.join(@repo, '.fable', 'checkpoints', TASK_ID, 'executions', '*.json'))
    assert_equal [record_path(old_id), record_path(successor_execution_id(old_id))].sort, records.sort
  end

  def test_failed_terminal_recovery_has_distinct_retry_and_immutable_prior_evidence
    old_id = 'failed_retry_fixture'
    seed_stale_execution(old_id)
    first = run_cli(recovery_cli_args(old_id, upstream(result: '7')))
    assert_equal 7, first[2].exitstatus
    prior_id = successor_execution_id(old_id)
    old_bytes = File.binread(record_path(old_id))
    prior_record_bytes = File.binread(record_path(prior_id))
    prior_capture_bytes = File.binread(capture_path(prior_id))
    prior_transition_bytes = File.binread(recovery_transition_path(old_id))
    state_bytes = File.binread(@application_state)

    command = recovery_upstream(prior_id)
    ordinary = run_cli(recovery_cli_args(old_id, command))
    assert_equal 1, ordinary[2].exitstatus, 'ordinary recovery must not accept changed argv'
    refute File.exist?(recovery_transition_path(prior_id))
    result = run_cli(retry_recovery_cli_args(prior_id, command))

    assert_equal 0, result[2].exitstatus, result[1]
    retry_id = successor_execution_id(prior_id)
    refute_equal prior_id, retry_id
    retry_transition = ExecutionRecoveryTransition.load(recovery_transition_path(prior_id))
    retry_record = ExecutionRecord.load(record_path(retry_id))
    assert_equal prior_id, retry_transition.old_execution_id
    assert_equal Digest::SHA256.hexdigest(prior_record_bytes), retry_transition.old_execution_sha256
    assert_equal Digest::SHA256.hexdigest(prior_capture_bytes), retry_transition.old_capture_sha256
    assert_equal ExecutionRecoveryTransition::DISPOSITION_FAILED_RECOVERY, retry_transition.old_disposition
    assert_equal Digest::SHA256.hexdigest(JSON.generate(command)), retry_transition.successor_command_sha256
    refute_equal ExecutionRecoveryTransition.load(recovery_transition_path(old_id)).successor_command_sha256,
                 retry_transition.successor_command_sha256
    assert_equal prior_id, retry_record.recovery_parent_execution_id
    assert_nil retry_record.parent_execution_id, 'a retry creates a fresh active lineage; its failed predecessor is history'
    assert_equal old_id, retry_record.recovery_old_execution_id
    assert_equal command, DurableCommandCapture.load(capture_path(retry_id)).command
    assert_equal Digest::SHA256.hexdigest(state_bytes), JSON.parse(File.read(@recovery_observation)).fetch('state_sha256')
    assert_equal state_bytes, File.binread(@application_state)
    assert_equal old_bytes, File.binread(record_path(old_id))
    assert_equal prior_record_bytes, File.binread(record_path(prior_id))
    assert_equal prior_capture_bytes, File.binread(capture_path(prior_id))
    assert_equal prior_transition_bytes, File.binread(recovery_transition_path(old_id))
    assert_equal 2, launch_records.size

    replay = run_cli(retry_recovery_cli_args(prior_id, command))
    assert_equal 0, replay[2].exitstatus
    assert_equal 2, launch_records.size, 'same explicit edge replays its terminal capture'
  end

  def test_retry_accepts_changed_synthetic_interpreter_path
    old_id = 'interpreter_retry_fixture'
    seed_stale_execution(old_id)
    python_a = File.join(@tmpdir, 'python-a')
    python_b = File.join(@tmpdir, 'python-b')
    File.symlink(RbConfig.ruby, python_a)
    File.symlink(RbConfig.ruby, python_b)
    old_command = [python_a, '-e', 'exit 3']
    assert_equal 3, run_cli(recovery_cli_args(old_id, old_command))[2].exitstatus
    prior_id = successor_execution_id(old_id)
    new_command = [python_b, '-e', 'exit 0']

    assert_equal 0, run_cli(retry_recovery_cli_args(prior_id, new_command))[2].exitstatus
    assert_equal old_command, DurableCommandCapture.load(capture_path(prior_id)).command
    assert_equal new_command, DurableCommandCapture.load(capture_path(successor_execution_id(prior_id))).command
    assert_equal Digest::SHA256.hexdigest(JSON.generate(new_command)),
                 ExecutionRecoveryTransition.load(recovery_transition_path(prior_id)).successor_command_sha256
  end

  def test_retry_rejects_successful_and_non_recovery_prior_executions
    old_id = 'successful_retry_source'
    seed_stale_execution(old_id)
    assert_equal 0, run_cli(recovery_cli_args(old_id))[2].exitstatus
    prior_id = successor_execution_id(old_id)
    result = run_cli(retry_recovery_cli_args(prior_id, upstream))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], 'RECOVERY_SUCCESSOR_ALREADY_SUCCEEDED'
    refute File.exist?(recovery_transition_path(prior_id))

    normal_id = 'ordinary_failed_run'
    assert_equal 3, run_cli(cli_args(normal_id, upstream(result: '3')))[2].exitstatus
    normal_retry = run_cli(retry_recovery_cli_args(normal_id, upstream))
    assert_equal 1, normal_retry[2].exitstatus
    assert_includes normal_retry[1], 'not a terminal recovery successor'
    refute File.exist?(recovery_transition_path(normal_id))
  end

  def test_retry_requires_complete_explicit_application_argv
    old_id = 'retry_missing_argv'
    seed_stale_execution(old_id)
    assert_equal 7, run_cli(recovery_cli_args(old_id, upstream(result: '7')))[2].exitstatus
    prior_id = successor_execution_id(old_id)
    args = retry_recovery_cli_args(prior_id, upstream)
    args = args.take(args.index('--') + 1)

    result = run_cli(args)

    assert_equal 2, result[2].exitstatus
    assert_includes result[1], '--retry-recover-run requires upstream argv'
    refute File.exist?(recovery_transition_path(prior_id))
  end

  def test_retry_rejects_active_and_incomplete_recovery_successors
    old_id = 'active_retry_source'
    seed_stale_execution(old_id)
    first = start_cli(recovery_cli_args(old_id, recovery_barrier_upstream(old_id)))
    wait_until('recovery successor child did not start') { !launch_records.empty? }
    prior_id = successor_execution_id(old_id)
    active = run_cli(retry_recovery_cli_args(prior_id, upstream))
    assert_equal 1, active[2].exitstatus
    assert_includes active[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    refute File.exist?(recovery_transition_path(prior_id))
    File.write(@release, 'go')
    assert_equal 0, finish_cli(first)[2].exitstatus

    second_old = 'incomplete_retry_source'
    seed_stale_execution(second_old)
    transition = ExecutionRecoveryTransition.reserve!(
      repo_root: @repo, task_id: TASK_ID, old_execution_id: second_old,
      application_state_path: @application_state, worktree_path: @worktree,
      command: recovery_upstream(second_old)
    )
    ExecutionRecord.start!(record_path(transition.successor_execution_id), task_id: TASK_ID,
                           execution_id: transition.successor_execution_id, pid: dead_pid)
    incomplete = run_cli(retry_recovery_cli_args(transition.successor_execution_id, upstream))
    assert_equal 1, incomplete[2].exitstatus
    assert_includes incomplete[1], 'not a terminal recovery successor'
  end

  def test_retry_rejects_competing_writer_wrong_task_and_broken_lineage
    old_id = 'retry_identity_fixture'
    seed_stale_execution(old_id)
    assert_equal 7, run_cli(recovery_cli_args(old_id, upstream(result: '7')))[2].exitstatus
    prior_id = successor_execution_id(old_id)
    competitor = Process.spawn(RbConfig.ruby, '-e', 'sleep 5')
    begin
      ExecutionRecord.start!(record_path('competing_writer'), task_id: TASK_ID,
                             execution_id: 'competing_writer', pid: competitor)
      blocked = run_cli(retry_recovery_cli_args(prior_id, upstream))
      assert_equal 1, blocked[2].exitstatus
      assert_includes blocked[1], ExecutionRecord::CLASSIFICATION_ACTIVE
      refute File.exist?(recovery_transition_path(prior_id))
    ensure
      Process.kill('TERM', competitor)
      Process.wait(competitor)
    end

    wrong_task = retry_recovery_cli_args(prior_id, upstream)
    wrong_task[wrong_task.index('--task-id') + 1] = 'OTHER_TASK'
    assert_equal 1, run_cli(wrong_task)[2].exitstatus
    refute File.exist?(ExecutionRecoveryTransition.default_path(@repo, 'OTHER_TASK', prior_id))

    prior = ExecutionRecord.load(record_path(prior_id))
    prior.recovery_old_execution_id = 'wrong_root'
    prior.save(record_path(prior_id))
    broken = run_cli(retry_recovery_cli_args(prior_id, upstream))
    assert_equal 1, broken[2].exitstatus
    assert_includes broken[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(recovery_transition_path(prior_id))
  end

  def test_retry_setup_preserves_application_checkpoint_before_spawn
    old_id = 'retry_state_fixture'
    seed_stale_execution(old_id)
    assert_equal 7, run_cli(recovery_cli_args(old_id, upstream(result: '7')))[2].exitstatus
    prior_id = successor_execution_id(old_id)
    before = File.binread(@application_state)
    launches_before = launch_records.size

    recovery = ExecutionRecoveryTransition.acquire_successor!(
      repo_root: @repo, task_id: TASK_ID, old_execution_id: prior_id,
      application_state_path: @application_state, worktree_path: @worktree,
      command: recovery_upstream(prior_id), pid: Process.pid, retry_attempt: true
    )

    assert_nil recovery.classification
    assert_equal before, File.binread(@application_state)
    assert_equal Digest::SHA256.hexdigest(before), recovery.recovery_transition.application_state_sha256
    assert_equal launches_before, launch_records.size, 'retry setup must not start the application'
  end

  def test_retry_refuses_checkpoint_drift_and_prior_capture_change_after_reservation
    old_id = 'retry_drift_fixture'
    seed_stale_execution(old_id)
    assert_equal 7, run_cli(recovery_cli_args(old_id, upstream(result: '7')))[2].exitstatus
    prior_id = successor_execution_id(old_id)
    command = recovery_upstream(prior_id)
    transition = ExecutionRecoveryTransition.reserve_retry!(
      repo_root: @repo, task_id: TASK_ID, prior_execution_id: prior_id,
      application_state_path: @application_state, worktree_path: @worktree, command: command
    )
    assert_equal Digest::SHA256.hexdigest(File.binread(capture_path(prior_id))), transition.old_capture_sha256
    File.binwrite(@application_state, "synthetic-checkpoint\0v2\n")

    drift = run_cli(retry_recovery_cli_args(prior_id, command))
    assert_equal 1, drift[2].exitstatus
    assert_includes drift[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(record_path(successor_execution_id(prior_id)))

    File.binwrite(@application_state, "synthetic-checkpoint\0v1\n")
    capture = DurableCommandCapture.load(capture_path(prior_id))
    capture.stderr = "tampered prior evidence\n"
    capture.save(capture_path(prior_id))
    changed_capture = run_cli(retry_recovery_cli_args(prior_id, command))
    assert_equal 1, changed_capture[2].exitstatus
    assert_includes changed_capture[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(record_path(successor_execution_id(prior_id)))
  end

  def test_retry_refuses_changed_checkpoint_locator_worktree_and_preexisting_successor
    old_id = 'retry_locator_fixture'
    seed_stale_execution(old_id)
    assert_equal 7, run_cli(recovery_cli_args(old_id, upstream(result: '7')))[2].exitstatus
    prior_id = successor_execution_id(old_id)
    command = recovery_upstream(prior_id)
    alternate_state = File.join(@tmpdir, 'alternate-checkpoint.bin')
    File.binwrite(alternate_state, File.binread(@application_state))
    wrong_state = retry_recovery_cli_args(prior_id, command)
    wrong_state[wrong_state.index('--application-state') + 1] = alternate_state
    assert_equal 1, run_cli(wrong_state)[2].exitstatus

    alternate_worktree = File.join(@tmpdir, 'alternate-worktree')
    FileUtils.mkdir_p(alternate_worktree)
    wrong_worktree = retry_recovery_cli_args(prior_id, command)
    wrong_worktree[wrong_worktree.index('--worktree') + 1] = alternate_worktree
    assert_equal 1, run_cli(wrong_worktree)[2].exitstatus
    refute File.exist?(recovery_transition_path(prior_id))

    retry_id = successor_execution_id(prior_id)
    ExecutionRecord.start!(record_path(retry_id), task_id: TASK_ID,
                           execution_id: retry_id, pid: dead_pid)
    existing = run_cli(retry_recovery_cli_args(prior_id, command))
    assert_equal 1, existing[2].exitstatus
    assert_includes existing[1], 'already exists without a recovery transition'
    refute File.exist?(recovery_transition_path(prior_id))
  end

  def test_retry_successor_keeps_nested_child_and_rejects_independent_writer
    old_id = 'retry_nested_fixture'
    seed_stale_execution(old_id)
    assert_equal 7, run_cli(recovery_cli_args(old_id, upstream(result: '7')))[2].exitstatus
    prior_id = successor_execution_id(old_id)
    owner = start_cli(retry_recovery_cli_args(prior_id, retry_nested_upstream(barrier: true)))
    wait_until('retry driver did not start') { File.file?(@recovery_observation) }
    retry_id = successor_execution_id(prior_id)

    competitor = run_cli(cli_args('retry_competing_writer'))
    assert_equal 1, competitor[2].exitstatus
    assert_includes competitor[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    refute File.exist?(record_path('retry_competing_writer'))

    File.write(@release, 'go')
    assert_equal 0, finish_cli(owner)[2].exitstatus
    observation = JSON.parse(File.read(@recovery_observation))
    assert_equal 0, observation.fetch('exitstatus'), observation.fetch('stderr')
    inherited = JSON.parse(observation.fetch('stdout'))
    assert_equal retry_id, inherited.fetch('lineage').fetch('execution_id')
    assert_equal File.realpath(@repo), inherited.fetch('inherited').fetch(ExecutionRecord::NESTED_RECORD_ROOT_ENV)
    assert_equal DurableCommandCapture::INHERITED_TASK_LOCK_CHILD_FD.to_s,
                 inherited.fetch('inherited').fetch(ExecutionRecord::NESTED_LOCK_FD_ENV)
    child = ExecutionRecord.load(record_path('nested_child'))
    assert_equal retry_id, child.parent_execution_id
    assert_equal old_id, ExecutionRecord.load(record_path(retry_id)).recovery_old_execution_id
  end

  def test_two_explicit_retry_callers_share_one_successor_identity
    old_id = 'retry_contenders_fixture'
    seed_stale_execution(old_id)
    assert_equal 7, run_cli(recovery_cli_args(old_id, upstream(result: '7')))[2].exitstatus
    prior_id = successor_execution_id(old_id)
    command = recovery_barrier_upstream(prior_id)
    first = start_cli(retry_recovery_cli_args(prior_id, command))
    wait_until('retry successor child did not start') { launch_records.size == 2 }

    second = run_cli(retry_recovery_cli_args(prior_id, command))
    assert_equal 1, second[2].exitstatus
    assert_includes second[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    assert_equal 2, launch_records.size

    File.write(@release, 'go')
    assert_equal 0, finish_cli(first)[2].exitstatus
    replay = run_cli(retry_recovery_cli_args(prior_id, command))
    assert_equal 0, replay[2].exitstatus
    assert_equal 2, launch_records.size
    retry_id = successor_execution_id(prior_id)
    records = Dir.glob(File.join(@repo, '.fable', 'checkpoints', TASK_ID, 'executions', '*.json'))
    assert_equal [record_path(old_id), record_path(prior_id), record_path(retry_id)].sort, records.sort
  end

  def test_each_failed_retry_requires_another_explicit_source
    old_id = 'retry_chain_fixture'
    seed_stale_execution(old_id)
    assert_equal 7, run_cli(recovery_cli_args(old_id, upstream(result: '7')))[2].exitstatus
    first_id = successor_execution_id(old_id)
    assert_equal 8, run_cli(retry_recovery_cli_args(first_id, upstream(result: '8')))[2].exitstatus
    second_id = successor_execution_id(first_id)
    refute File.exist?(recovery_transition_path(second_id))

    third = run_cli(retry_recovery_cli_args(second_id, retry_nested_upstream))
    assert_equal 0, third[2].exitstatus, third[1]
    third_id = successor_execution_id(second_id)
    assert_equal second_id, ExecutionRecord.load(record_path(third_id)).recovery_parent_execution_id
    assert_nil ExecutionRecord.load(record_path(third_id)).parent_execution_id
    assert_equal old_id, ExecutionRecord.load(record_path(third_id)).recovery_old_execution_id
    assert_equal 0, JSON.parse(File.read(@recovery_observation)).fetch('exitstatus')
    assert_equal third_id, ExecutionRecord.load(record_path('nested_child')).parent_execution_id
    assert_equal 3, launch_records.size
    after_success = run_cli(retry_recovery_cli_args(third_id, upstream))
    assert_equal 1, after_success[2].exitstatus
    assert_includes after_success[1], 'RECOVERY_SUCCESSOR_ALREADY_SUCCEEDED'
    refute File.exist?(recovery_transition_path(third_id))
  end

  def test_second_recovery_refuses_an_active_successor
    old_id = 'active_successor_fixture'
    seed_stale_execution(old_id)
    first = start_cli(recovery_cli_args(old_id, recovery_barrier_upstream(old_id)))
    wait_until('recovery successor child did not start') { !launch_records.empty? }

    second = run_cli(recovery_cli_args(old_id, recovery_barrier_upstream(old_id)))

    assert_equal 1, second[2].exitstatus
    assert_includes second[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    assert_equal 1, launch_records.size
    transition = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))
    assert_equal successor_execution_id(old_id), transition.successor_execution_id
    File.write(@release, 'go')
    first_result = finish_cli(first)
    assert_equal 0, first_result[2].exitstatus, first_result[1]
    assert_equal 1, launch_records.size
  end

  def test_recover_run_keeps_task_lease_in_orphaned_child_after_wrapper_death
    old_id = 'orphaned_child_fixture'
    seed_stale_execution(old_id)
    first = start_cli(recovery_cli_args(old_id, recovery_barrier_upstream(old_id)))
    wait_until('recovery successor child did not start') { !launch_records.empty? }
    child_pid = Integer(launch_records.first)
    wrapper_pid = first[:wait].pid

    Process.kill('KILL', wrapper_pid)
    assert first[:wait].join(5), 'killed recovery wrapper was not reaped'
    assert first[:wait].value.signaled?
    assert_equal Signal.list.fetch('KILL'), first[:wait].value.termsig
    assert_equal false, ExecutionRecord.pid_alive?(wrapper_pid)
    assert_equal true, ExecutionRecord.pid_alive?(child_pid), 'the application child intentionally outlives its wrapper'

    retry_while_orphaned = run_cli(recovery_cli_args(old_id, recovery_barrier_upstream(old_id)))
    assert_equal 1, retry_while_orphaned[2].exitstatus
    assert_includes retry_while_orphaned[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    assert_equal 1, launch_records.size, 'an orphaned child must retain the single-writer lease'
    successor_id = successor_execution_id(old_id)
    assert_equal successor_id, ExecutionRecoveryTransition.load(recovery_transition_path(old_id)).successor_execution_id

    File.write(@release, 'go')
    wait_until('orphaned application child did not exit') { ExecutionRecord.pid_alive?(child_pid) == false }
    first[:readers].each { |reader| assert reader.join(5), 'orphaned child output pipe did not close' }

    resumed = run_cli(recovery_cli_args(old_id, recovery_barrier_upstream(old_id)))
    assert_equal 0, resumed[2].exitstatus, resumed[1]
    assert_equal 2, launch_records.size, 'a later retry may resume only after the orphaned child exits'
    assert_equal successor_id, ExecutionRecoveryTransition.load(recovery_transition_path(old_id)).successor_execution_id
    records = Dir.glob(File.join(@repo, '.fable', 'checkpoints', TASK_ID, 'executions', '*.json'))
    assert_equal [record_path(old_id), record_path(successor_id)].sort, records.sort
  end

  def test_dead_started_recovery_successor_retry_preserves_nested_lineage_and_history
    old_id = 'dead_successor_fixture'
    seed_stale_execution(old_id)
    old_bytes = File.binread(record_path(old_id))
    nested_attempt = File.join(@tmpdir, 'retried-recovery-nested.json')
    command = nested_parent(
      [nested_step(inner_cli_args('retried_recovery_nested', nested_leaf('retry')),
                   result: nested_attempt)],
      release: @release
    )
    transition = ExecutionRecoveryTransition.reserve!(repo_root: @repo, task_id: TASK_ID,
                                                       old_execution_id: old_id,
                                                       application_state_path: @application_state,
                                                       worktree_path: @worktree,
                                                       command: command)
    stale_successor = ExecutionRecord.acquire!(record_path(transition.successor_execution_id),
                                               task_id: TASK_ID,
                                               execution_id: transition.successor_execution_id,
                                               pid: dead_pid)
    assert_nil stale_successor.classification
    stale_successor_bytes = File.binread(record_path(transition.successor_execution_id))

    recovery = start_cli(recovery_cli_args(old_id, command))
    wait_until('retried recovery application did not start') { launch_records.size == 1 }
    nested = nested_result(nested_attempt)
    assert_equal 0, nested.fetch('exitstatus'), nested.fetch('stderr')
    assert_equal "leaf retry\n", nested.fetch('stdout')
    successor_id = transition.successor_execution_id
    nested_record = ExecutionRecord.load(record_path('retried_recovery_nested'))
    assert_equal successor_id, nested_record.parent_execution_id
    transition_during_run = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))
    assert_equal [stale_successor_bytes],
                 transition_during_run.successor_attempt_history.map { |entry| entry.fetch('record_json') }
    assert_equal old_bytes, File.binread(record_path(old_id))

    File.write(@release, 'go')
    result = finish_cli(recovery)

    assert_equal 0, result[2].exitstatus, result[1]
    reread = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))
    assert_equal successor_id, reread.successor_execution_id
    assert_equal [stale_successor_bytes], reread.successor_attempt_history.map { |entry| entry.fetch('record_json') }
    assert_equal ExecutionRecord::STATUS_COMPLETED, ExecutionRecord.load(record_path(successor_id)).status
    assert_equal old_bytes, File.binread(record_path(old_id))
    assert_equal 2, launch_records.size
  end

  def test_run_unresolved_and_malformed_states_fail_closed
    states = ['{invalid json', '[]', JSON.generate('status' => 'UNKNOWN')]
    unresolved = ExecutionRecord.new(task_id: TASK_ID, execution_id: 'unresolved', status: 'STARTED', pid: nil)
    cases = states.each_with_index.map { |state, index| ["malformed_#{index}", state] }
    cases << ['unresolved', unresolved.to_json]
    cases.each do |identity, raw|
      path = record_path(identity)
      FileUtils.mkdir_p(File.dirname(path))
      File.write(path, raw)
      result = run_cli(cli_args(identity))
      assert_equal '', result[0]
      assert_equal 1, result[2].exitstatus
      assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
      assert_equal raw, File.read(path), 'malformed state must not be replaced or deleted'
      refute File.exist?(capture_path(identity))
    end
    assert_empty launch_records
  end

  def test_run_malformed_completed_result_is_not_replayed_or_rerun
    identity = 'malformed_result'
    capture = DurableCommandCapture.new(command: ['test'], stdout: 'must not replay', exit_status: 'bogus',
                                        started_at: Time.now.utc.iso8601, ended_at: Time.now.utc.iso8601,
                                        task_id: TASK_ID, execution_id: identity,
                                        execution_identity: {
                                          'repository_path' => @repo,
                                          'worktree_path' => @worktree,
                                          'task_id' => TASK_ID,
                                          'execution_id' => identity
                                        })
    capture.save(capture_path(identity))
    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    record.complete!(record_path(identity), durable_capture_path: capture_path(identity))
    before = File.binread(record_path(identity))
    result = run_cli(cli_args(identity))
    assert_equal '', result[0]
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    assert_equal before, File.binread(record_path(identity))
    assert_empty launch_records
  end

  def test_run_preserves_argv_and_uses_worktree_only_for_upstream_cwd
    payload = ['a b', '$(touch forbidden)', '; touch forbidden', '--repo', '', '繁體']
    command = [RbConfig.ruby, '-rjson', '-e', 'print JSON.generate([Dir.pwd, ARGV])', *payload]
    result = run_cli(cli_args('argv', command))
    assert_equal 0, result[2].exitstatus, result[1]
    assert_equal '', result[1]
    assert_equal [File.realpath(@worktree), payload], JSON.parse(result[0])
    assert_equal command, DurableCommandCapture.load(capture_path('argv')).command
    assert_empty Dir.children(@worktree)
    assert_empty Dir.children(@caller)
  end

  def test_run_single_executable_argument_never_uses_shell_parsing
    executable = File.join(@tmpdir, 'upstream ; literal')
    File.write(executable, "#!#{RbConfig.ruby}\nSTDOUT.write('literal executable')\n")
    FileUtils.chmod(0o700, executable)
    result = run_cli(cli_args('single_argv', [executable]))
    assert_equal 0, result[2].exitstatus, result[1]
    assert_equal 'literal executable', result[0]
    assert_equal '', result[1]
    assert_equal [executable], DurableCommandCapture.load(capture_path('single_argv')).command
  end

  def test_run_requires_explicit_roots_stable_ids_and_argv_delimiter
    valid = cli_args('invalid')
    cases = %w[--repo --worktree --task-id --execution-id].map do |flag|
      args = valid.dup
      args.slice!(args.index(flag), 2)
      args
    end
    cases += [valid.reject { |arg| arg == '--' }, valid.take(valid.index('--') + 1), ['--show', *valid]]
    recovery_valid = recovery_cli_args('invalid_recovery')
    missing_state = recovery_valid.dup
    missing_state.slice!(missing_state.index('--application-state'), 2)
    cases << missing_state
    { '--repo' => '.', '--worktree' => '.', '--task-id' => '../escape', '--execution-id' => '' }.each do |flag, value|
      args = valid.dup
      args[args.index(flag) + 1] = value
      cases << args
    end
    cases.each do |args|
      result = run_cli(args)
      assert_equal 2, result[2].exitstatus, result[1]
      assert_equal '', result[0]
    end
    assert_empty launch_records
    assert_equal ['.git'], Dir.children(@repo).sort
    assert_empty Dir.children(@worktree)
    assert_empty Dir.children(@caller)
  end

  def nested_leaf(label)
    [RbConfig.ruby, '-e', NESTED_LEAF_UPSTREAM, @launches, label]
  end

  def inner_cli_args(identity, command, task_id: TASK_ID, repo: @repo)
    [RbConfig.ruby, CLI, '--run', '--repo', repo, '--worktree', @worktree,
     '--task-id', task_id, '--execution-id', identity, '--', *command]
  end

  def nested_step(argv, result:, env: {}, go: nil, pass_fd: true)
    { 'argv' => argv, 'result' => result, 'env' => env, 'go' => go, 'pass_fd' => pass_fd }
  end

  def nested_parent(steps, release: nil)
    @nested_specs = (@nested_specs || 0) + 1
    spec_path = File.join(@tmpdir, "nested-parent-#{@nested_specs}.json")
    File.write(spec_path, JSON.generate('launches' => @launches, 'steps' => steps, 'release' => release))
    [RbConfig.ruby, '-rjson', '-ropen3', '-e', NESTED_PARENT_UPSTREAM, spec_path]
  end

  def nested_result(path)
    wait_until("nested step did not report: #{path}") { File.file?(path) }
    JSON.parse(File.read(path))
  end

  def assert_nested_refused(result, classification)
    assert_equal 1, result.fetch('exitstatus'), result.fetch('stderr')
    assert_equal '', result.fetch('stdout')
    assert_includes result.fetch('stderr'), classification
  end

  def test_nested_run_from_protected_parent_is_independently_auditable
    leaf = nested_leaf('inner')
    inner_result = File.join(@tmpdir, 'inner-result.json')
    parent = nested_parent([nested_step(inner_cli_args('nested_inner', leaf), result: inner_result)])

    outer = run_cli(cli_args('nested_outer', parent))

    assert_equal 0, outer[2].exitstatus, outer[1]
    assert_equal "nested parent done\n", outer[0]
    inner = nested_result(inner_result)
    assert_equal 0, inner.fetch('exitstatus'), inner.fetch('stderr')
    assert_equal "leaf inner\n", inner.fetch('stdout')
    outer_record = ExecutionRecord.load(record_path('nested_outer'))
    inner_record = ExecutionRecord.load(record_path('nested_inner'))
    assert_equal ExecutionRecord::STATUS_COMPLETED, outer_record.status
    refute JSON.parse(File.read(record_path('nested_outer'))).key?('parent_execution_id'),
           'a top-level record keeps its existing shape'
    assert_equal ExecutionRecord::STATUS_COMPLETED, inner_record.status
    assert_equal 'nested_outer', inner_record.parent_execution_id
    refute_equal outer_record.pid, inner_record.pid
    assert_equal capture_path('nested_inner'), inner_record.durable_capture_path
    assert_equal leaf, DurableCommandCapture.load(capture_path('nested_inner')).command
    assert_equal "leaf inner\n", DurableCommandCapture.load(capture_path('nested_inner')).stdout
    assert_equal parent, DurableCommandCapture.load(capture_path('nested_outer')).command
    parent_line, leaf_line = launch_records
    assert_equal outer_record.pid, Integer(parent_line.split(':').last), 'the parent is the outer launcher child'
    assert_equal inner_record.pid, Integer(leaf_line.split(':').last), 'the leaf is the nested launcher child'
    lineage = JSON.parse(File.read(ExecutionRecord.task_lock_path(@repo, TASK_ID)))
    assert_equal({ 'schema_version' => ExecutionRecord::SCHEMA_VERSION,
                   'lock_generation' => StableLockAnchor::GENERATION, 'task_id' => TASK_ID,
                   'record_root' => File.realpath(@repo), 'execution_id' => 'nested_outer' },
                 lineage.reject { |key, _| key == 'capability_sha256' })
    assert_match(/\A[0-9a-f]{64}\z/, lineage.fetch('capability_sha256'))

    later = run_cli(cli_args('after_nested_lineage'))
    assert_output(later, 0)
    replay = run_cli(cli_args('nested_inner', leaf))
    assert_equal ["leaf inner\n", ''], replay[0, 2]
    assert_equal 0, replay[2].exitstatus
    assert_equal 3, launch_records.size, 'a completed nested record replays without relaunch'
  end

  def test_nested_run_survives_wrapper_death_while_unrelated_owners_stay_blocked
    go = File.join(@tmpdir, 'nested-go')
    inner_result = File.join(@tmpdir, 'orphan-inner-result.json')
    step = nested_step(inner_cli_args('orphan_inner', nested_leaf('after_wrapper_death')),
                       result: inner_result, go: go)
    owner = start_cli(cli_args('orphan_outer', nested_parent([step], release: @release)))
    wait_until('protected parent did not start') { launch_records.size == 1 }
    parent_pid = Integer(launch_records.first.split(':').first)

    live_wrapper_contender = run_cli(cli_args('rotated_while_wrapper_lives'))
    assert_equal 1, live_wrapper_contender[2].exitstatus
    assert_includes live_wrapper_contender[1], ExecutionRecord::CLASSIFICATION_ACTIVE

    Process.kill('KILL', owner[:wait].pid)
    assert owner[:wait].join(5), 'killed outer wrapper was not reaped'
    assert_equal true, ExecutionRecord.pid_alive?(parent_pid), 'the protected parent outlives its wrapper'
    [cli_args('rotated_after_wrapper_death'), cli_args('orphan_outer')].each do |args|
      refused = run_cli(args)
      assert_equal 1, refused[2].exitstatus
      assert_includes refused[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    end
    recovery = run_cli(recovery_cli_args('orphan_outer'))
    assert_equal 1, recovery[2].exitstatus
    assert_includes recovery[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    refute File.exist?(recovery_transition_path('orphan_outer'))
    %w[rotated_while_wrapper_lives rotated_after_wrapper_death].each { |id| refute File.exist?(record_path(id)) }
    assert_equal 1, launch_records.size

    File.write(go, 'go')
    inner = nested_result(inner_result)
    assert_equal 0, inner.fetch('exitstatus'), inner.fetch('stderr')
    assert_equal "leaf after_wrapper_death\n", inner.fetch('stdout')
    inner_record = ExecutionRecord.load(record_path('orphan_inner'))
    assert_equal ExecutionRecord::STATUS_COMPLETED, inner_record.status
    assert_equal 'orphan_outer', inner_record.parent_execution_id
    assert_equal 2, launch_records.size

    File.write(@release, 'go')
    wait_until('protected parent did not exit') { ExecutionRecord.pid_alive?(parent_pid) == false }
    owner[:readers].each { |reader| assert reader.join(5), 'orphaned parent output pipe did not close' }
    after_parent = run_cli(cli_args('rotated_after_parent_exit'))
    assert_equal 1, after_parent[2].exitstatus
    assert_includes after_parent[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(record_path('rotated_after_parent_exit'))
    assert_equal 2, launch_records.size
  end

  def test_nested_capability_environment_without_inherited_lock_is_refused
    owner = start_cli(cli_args('forgery_owner', upstream(barrier: true)))
    wait_until('forgery owner did not start') { !launch_records.empty? }
    lock_path = ExecutionRecord.task_lock_path(@repo, TASK_ID)
    stat = File.stat(lock_path)
    forged = {
      ExecutionRecord::NESTED_TASK_ID_ENV => TASK_ID,
      ExecutionRecord::NESTED_RECORD_ROOT_ENV => File.realpath(@repo),
      ExecutionRecord::NESTED_LOCK_ROOT_ENV => nil,
      ExecutionRecord::NESTED_LOCK_FD_ENV => '200',
      ExecutionRecord::NESTED_LOCK_IDENTITY_ENV => "#{stat.dev}:#{stat.ino}",
      ExecutionRecord::NESTED_PARENT_ENV => 'forgery_owner',
      ExecutionRecord::NESTED_SECRET_ENV => 'f' * 64
    }

    without_descriptor = run_cli(cli_args('forged_without_fd'), env: forged)
    assert_equal 1, without_descriptor[2].exitstatus
    assert_includes without_descriptor[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    File.open(lock_path, File::RDWR) do |unlocked|
      held_elsewhere = run_cli(cli_args('forged_unlocked_fd'),
                               env: forged.merge(ExecutionRecord::NESTED_LOCK_FD_ENV => unlocked.fileno.to_s),
                               inherit_fds: [unlocked.fileno])
      assert_equal 1, held_elsewhere[2].exitstatus
      assert_includes held_elsewhere[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    end
    %w[forged_without_fd forged_unlocked_fd].each { |id| refute File.exist?(record_path(id)) }
    assert_equal 1, launch_records.size

    File.write(@release, 'go')
    assert_output(finish_cli(owner), 0)
    File.open(lock_path, File::RDWR) do |unlocked|
      unheld = run_cli(cli_args('forged_after_release'),
                       env: forged.merge(ExecutionRecord::NESTED_LOCK_FD_ENV => unlocked.fileno.to_s),
                       inherit_fds: [unlocked.fileno])
      assert_equal 1, unheld[2].exitstatus
      assert_includes unheld[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    end
    refute File.exist?(record_path('forged_after_release'))
    assert_output(run_cli(cli_args('ordinary_after_forgery')), 0)
    assert_equal 2, launch_records.size
  end

  def test_nested_capability_is_bound_to_its_task_and_record_root
    other_task = 'CLI_TASK_OTHER'
    free_task = 'CLI_TASK_FREE'
    other_root = File.join(@tmpdir, 'other-record-root')
    FileUtils.mkdir_p(other_root)
    _git_out, git_err, git_status = Open3.capture3('git', '-C', other_root, 'init', '--quiet')
    assert git_status.success?, git_err
    StableLockAnchor.initialize_repository_generation!(other_root, confirm_legacy_workers_quiescent: true)
    other_release = File.join(@tmpdir, 'other-release')
    other_command = [RbConfig.ruby, '-e', UPSTREAM, @launches, other_release, '0']
    other_owners = [[@repo, other_task, 'other_task_owner'], [other_root, TASK_ID, 'other_root_owner']].map do |repo, task, id|
      start_cli(['--run', '--repo', repo, '--worktree', @worktree, '--task-id', task,
                 '--execution-id', id, '--', *other_command])
    end
    wait_until('other scope owners did not start') { launch_records.size == 2 }
    leaf = nested_leaf('other_scope')
    results = 5.times.map { |index| File.join(@tmpdir, "scope-result-#{index}.json") }
    steps = [
      nested_step(inner_cli_args('inherited_scope_other_task', leaf, task_id: other_task), result: results[0]),
      nested_step(inner_cli_args('forged_scope_other_task', leaf, task_id: other_task), result: results[1],
                  env: { ExecutionRecord::NESTED_TASK_ID_ENV => other_task }),
      nested_step(inner_cli_args('inherited_scope_other_root', leaf, repo: other_root), result: results[2]),
      nested_step(inner_cli_args('forged_scope_other_root', leaf, repo: other_root), result: results[3],
                  env: { ExecutionRecord::NESTED_RECORD_ROOT_ENV => File.realpath(other_root) }),
      nested_step(inner_cli_args('inherited_scope_free_task', leaf, task_id: free_task), result: results[4])
    ]

    outer = run_cli(cli_args('scope_outer', nested_parent(steps)))

    assert_equal 0, outer[2].exitstatus, outer[1]
    expected = [ExecutionRecord::CLASSIFICATION_ACTIVE, ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED] * 2
    results.first(4).zip(expected).each { |path, classification| assert_nested_refused(nested_result(path), classification) }
    %w[inherited_scope_other_task forged_scope_other_task].each do |id|
      refute File.exist?(ExecutionRecord.default_path(@repo, other_task, id))
    end
    %w[inherited_scope_other_root forged_scope_other_root].each do |id|
      refute File.exist?(ExecutionRecord.default_path(other_root, TASK_ID, id))
    end
    free = nested_result(results[4])
    assert_equal 0, free.fetch('exitstatus'), free.fetch('stderr')
    free_record = ExecutionRecord.load(ExecutionRecord.default_path(@repo, free_task, 'inherited_scope_free_task'))
    assert_equal ExecutionRecord::STATUS_COMPLETED, free_record.status
    assert_nil free_record.parent_execution_id, 'another scope only gets ordinary top-level acquisition'
    assert_equal 4, launch_records.size

    File.write(other_release, 'go')
    other_owners.each { |owner| assert_output(finish_cli(owner), 0) }
  end

  def test_nested_capability_requires_inherited_descriptor_and_lineage_parent
    unrelated_completed = 'unrelated_completed'
    capture_file = capture_path(unrelated_completed)
    DurableCommandCapture.run_and_capture([RbConfig.ruby, '-e', 'exit 0'], file_path: capture_file)
    ExecutionRecord.start!(record_path(unrelated_completed), task_id: TASK_ID,
                           execution_id: unrelated_completed, pid: dead_pid)
                   .complete!(record_path(unrelated_completed), durable_capture_path: capture_file)
    go_stale = File.join(@tmpdir, 'descriptor-go-stale')
    go_valid = File.join(@tmpdir, 'descriptor-go-valid')
    leaf = nested_leaf('valid_after_refusals')
    cases = [
      ['closed_descriptor', {}, false],
      ['malformed_descriptor', { ExecutionRecord::NESTED_LOCK_FD_ENV => 'not-a-descriptor' }, true],
      ['standard_descriptor', { ExecutionRecord::NESTED_LOCK_FD_ENV => '2' }, true],
      ['missing_descriptor', { ExecutionRecord::NESTED_LOCK_FD_ENV => nil }, true],
      ['wrong_lock_identity', { ExecutionRecord::NESTED_LOCK_IDENTITY_ENV => '0:0' }, true],
      ['missing_parent', { ExecutionRecord::NESTED_PARENT_ENV => 'not_in_lineage' }, true],
      ['completed_parent', { ExecutionRecord::NESTED_PARENT_ENV => unrelated_completed }, true],
      ['missing_lineage_secret', { ExecutionRecord::NESTED_SECRET_ENV => nil }, true],
      ['wrong_lineage_secret', { ExecutionRecord::NESTED_SECRET_ENV => '0' * 64 }, true],
      ['descriptor_outer', {}, true],
      ['stale_top_level_parent', { ExecutionRecord::NESTED_PARENT_ENV => 'unrelated_stale' }, true]
    ]
    steps = cases.each_with_index.map do |(id, env, pass_fd), index|
      nested_step(inner_cli_args(id, leaf), result: File.join(@tmpdir, "descriptor-#{index}.json"),
                                            env: env, pass_fd: pass_fd,
                                            go: id == 'stale_top_level_parent' ? go_stale : nil)
    end
    valid_result = File.join(@tmpdir, 'descriptor-valid.json')
    steps << nested_step(inner_cli_args('valid_after_refusals', leaf), result: valid_result, go: go_valid)
    owner = start_cli(cli_args('descriptor_outer', nested_parent(steps)))
    wait_until('descriptor parent did not start') { launch_records.size == 1 }
    steps.first(cases.length - 1).each do |step|
      assert_nested_refused(nested_result(step['result']), ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED)
    end
    # A top-level stale record seeded outside the running lineage cannot be named as its parent.
    seed_stale_execution('unrelated_stale')
    File.write(go_stale, 'go')
    assert_nested_refused(nested_result(steps[cases.length - 1]['result']),
                          ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED)
    FileUtils.rm_f(record_path('unrelated_stale'))
    File.write(go_valid, 'go')

    outer = finish_cli(owner)
    assert_equal 0, outer[2].exitstatus, outer[1]
    cases.map(&:first).each do |id|
      next if id == 'descriptor_outer'

      refute File.exist?(record_path(id)), id
    end
    assert_equal ExecutionRecord::STATUS_COMPLETED, ExecutionRecord.load(record_path('descriptor_outer')).status
    valid = nested_result(valid_result)
    assert_equal 0, valid.fetch('exitstatus'), valid.fetch('stderr')
    assert_equal 'descriptor_outer', ExecutionRecord.load(record_path('valid_after_refusals')).parent_execution_id
    assert_equal 2, launch_records.size, 'refused nested requests never launch and never release the lineage'
  end

  def test_nested_run_keeps_unrelated_active_and_stale_records_protective
    go_active = File.join(@tmpdir, 'go-active')
    go_stale = File.join(@tmpdir, 'go-stale')
    results = [File.join(@tmpdir, 'blocked-active.json'), File.join(@tmpdir, 'blocked-stale.json')]
    leaf = nested_leaf('must_not_run')
    steps = [
      nested_step(inner_cli_args('blocked_by_active', leaf), result: results[0], go: go_active),
      nested_step(inner_cli_args('blocked_by_stale', leaf), result: results[1], go: go_stale)
    ]
    owner = start_cli(cli_args('protective_outer', nested_parent(steps)))
    wait_until('protective parent did not start') { launch_records.size == 1 }
    sleeper = Process.spawn(RbConfig.ruby, '-e', 'sleep 30')
    begin
      ExecutionRecord.start!(record_path('unrelated_owner'), task_id: TASK_ID,
                             execution_id: 'unrelated_owner', pid: sleeper)
      File.write(go_active, 'go')
      assert_nested_refused(nested_result(results[0]), ExecutionRecord::CLASSIFICATION_ACTIVE)
    ensure
      Process.kill('TERM', sleeper)
      Process.wait(sleeper)
    end
    File.write(go_stale, 'go')
    assert_nested_refused(nested_result(results[1]), ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE)

    outer = finish_cli(owner)
    assert_equal 0, outer[2].exitstatus, outer[1]
    %w[blocked_by_active blocked_by_stale].each { |id| refute File.exist?(record_path(id)) }
    assert_equal 1, launch_records.size
  end

  def test_nested_capability_propagates_through_a_protected_nested_child
    deep_result = File.join(@tmpdir, 'depth-two.json')
    middle_result = File.join(@tmpdir, 'depth-one.json')
    middle_parent = nested_parent([nested_step(inner_cli_args('depth_two', nested_leaf('deepest')),
                                               result: deep_result)])
    outer_parent = nested_parent([nested_step(inner_cli_args('depth_one', middle_parent), result: middle_result)])

    outer = run_cli(cli_args('depth_zero', outer_parent))

    assert_equal 0, outer[2].exitstatus, outer[1]
    middle = nested_result(middle_result)
    assert_equal [0, "nested parent done\n"], [middle.fetch('exitstatus'), middle.fetch('stdout')], middle.fetch('stderr')
    deep = nested_result(deep_result)
    assert_equal [0, "leaf deepest\n"], [deep.fetch('exitstatus'), deep.fetch('stdout')], deep.fetch('stderr')
    { 'depth_zero' => nil, 'depth_one' => 'depth_zero', 'depth_two' => 'depth_one' }.each do |id, parent_id|
      record = ExecutionRecord.load(record_path(id))
      assert_equal ExecutionRecord::STATUS_COMPLETED, record.status, id
      parent_id ? assert_equal(parent_id, record.parent_execution_id, id) : assert_nil(record.parent_execution_id, id)
    end
    assert_equal 3, launch_records.size
  end

  def test_dead_lineage_cannot_be_reused_by_a_self_acquired_lock
    dump = File.join(@tmpdir, 'lineage-env.json')
    command = [RbConfig.ruby, '-rjson', '-e', ENV_DUMP_UPSTREAM, dump, @launches, @release]
    owner = start_cli(cli_args('dead_lineage_root', command))
    wait_until('lineage child did not start') { File.file?(dump) && launch_records.size == 1 }
    child_pid = Integer(launch_records.first.split(':').first)
    harvested = JSON.parse(File.read(dump))
    secret = harvested.fetch(ExecutionRecord::NESTED_SECRET_ENV)
    lock_path = ExecutionRecord.task_lock_path(@repo, TASK_ID)
    assert_match(/\A[0-9a-f]{64}\z/, secret)
    refute_includes File.read(lock_path), secret, 'only the secret digest is stored'
    # The lineage dies abnormally: wrapper killed, then its child exits.
    Process.kill('KILL', owner[:wait].pid)
    assert owner[:wait].join(5), 'killed wrapper was not reaped'
    File.write(@release, 'go')
    wait_until('lineage child did not exit') { ExecutionRecord.pid_alive?(child_pid) == false }
    owner[:readers].each { |reader| assert reader.join(5), 'lineage child output pipe did not close' }
    root_bytes = File.binread(record_path('dead_lineage_root'))
    refute_equal 0, File.size(lock_path), 'the dead lineage record is still on disk'

    forge = lambda do |identity, env|
      File.open(lock_path, File::RDWR) do |own_lock|
        assert own_lock.flock(File::LOCK_EX | File::LOCK_NB), 'no lineage holds the lock any more'
        run_cli(cli_args(identity), env: env.merge(ExecutionRecord::NESTED_LOCK_FD_ENV => own_lock.fileno.to_s),
                                    inherit_fds: [own_lock.fileno])
      end
    end
    [['forged_without_secret', harvested.merge(ExecutionRecord::NESTED_SECRET_ENV => nil)],
     ['forged_guessed_secret', harvested.merge(ExecutionRecord::NESTED_SECRET_ENV => '0' * 64)]].each do |id, env|
      forged = forge.call(id, env)
      assert_equal 1, forged[2].exitstatus, forged[1]
      assert_includes forged[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    end

    rotated = run_cli(cli_args('rotated_after_dead_lineage'))
    assert_equal 1, rotated[2].exitstatus
    assert_includes rotated[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    rotated_lineage = JSON.parse(File.binread(lock_path))
    assert_equal StableLockAnchor::GENERATION, rotated_lineage.fetch('lock_generation')
    assert_equal TASK_ID, rotated_lineage.fetch('task_id')
    assert_equal File.realpath(@repo), rotated_lineage.fetch('record_root')
    refute rotated_lineage.key?('execution_id'), 'a conforming acquisition clears the dead execution owner'
    refute rotated_lineage.key?('capability_sha256'), 'a conforming acquisition clears the dead lineage secret'
    cleared_lineage = JSON.parse(File.read(lock_path))
    assert_equal StableLockAnchor::GENERATION, cleared_lineage.fetch('lock_generation')
    refute cleared_lineage.key?('execution_id'), 'a conforming acquisition clears nested lineage authority'
    refute cleared_lineage.key?('capability_sha256')
    replayed = forge.call('forged_harvested_secret', harvested)
    assert_equal 1, replayed[2].exitstatus, replayed[1]
    assert_includes replayed[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED

    %w[forged_without_secret forged_guessed_secret rotated_after_dead_lineage forged_harvested_secret].each do |id|
      refute File.exist?(record_path(id)), id
    end
    assert_equal root_bytes, File.binread(record_path('dead_lineage_root'))
    assert_equal 1, launch_records.size, 'no forged nested run may bypass the unrecovered stale owner'
  end

  def test_recover_run_successor_delegates_nested_execution_and_rejects_independent_writer
    old_id = 'recovery_lineage_stale'
    seed_stale_execution(old_id)
    old_bytes = File.binread(record_path(old_id))
    state_bytes = File.binread(@application_state)
    nested_attempt = File.join(@tmpdir, 'under-recovery.json')
    command = nested_parent([nested_step(inner_cli_args('nested_under_recovery', nested_leaf('recovered')),
                                         result: nested_attempt)], release: @release)
    recovery = start_cli(recovery_cli_args(old_id, command))

    wait_until('recovered application did not start') { launch_records.size == 1 }
    nested = nested_result(nested_attempt)
    assert_equal 0, nested.fetch('exitstatus'), nested.fetch('stderr')
    assert_equal "leaf recovered\n", nested.fetch('stdout')
    successor_id = successor_execution_id(old_id)
    successor = ExecutionRecord.load(record_path(successor_id))
    nested_record = ExecutionRecord.load(record_path('nested_under_recovery'))
    assert_equal ExecutionRecord::STATUS_STARTED, successor.status
    assert_nil successor.parent_execution_id
    assert_equal successor_id, nested_record.parent_execution_id
    lineage = JSON.parse(File.read(ExecutionRecord.task_lock_path(@repo, TASK_ID)))
    assert_equal successor_id, lineage.fetch('execution_id')
    assert_match(/\A[0-9a-f]{64}\z/, lineage.fetch('capability_sha256'))
    assert_equal old_bytes, File.binread(record_path(old_id))
    assert_equal state_bytes, File.binread(@application_state)

    independent = run_cli(cli_args('independent_during_recovery'))
    assert_equal 1, independent[2].exitstatus
    assert_includes independent[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    refute File.exist?(record_path('independent_during_recovery'))
    assert_equal 2, launch_records.size, 'an independent writer cannot join the recovered lineage'

    File.write(@release, 'go')
    recovered = finish_cli(recovery)
    assert_equal 0, recovered[2].exitstatus, recovered[1]
    assert_equal ExecutionRecord::STATUS_COMPLETED, ExecutionRecord.load(record_path(successor_id)).status
    assert_equal old_bytes, File.binread(record_path(old_id))
    assert_equal state_bytes, File.binread(@application_state)
    assert_equal 2, launch_records.size
  end

  def test_recover_run_successor_lineage_refuses_stale_predecessor_and_forged_capabilities
    old_id = 'recovery_forgery_stale'
    seed_stale_execution(old_id)
    old_bytes = File.binread(record_path(old_id))
    state_bytes = File.binread(@application_state)
    leaf = nested_leaf('valid_after_recovery_refusals')
    cases = [
      # The stale predecessor is historical evidence: it can never be named as the lineage parent.
      ['recovery_predecessor_parent', { ExecutionRecord::NESTED_PARENT_ENV => old_id }, true],
      ['recovery_wrong_secret', { ExecutionRecord::NESTED_SECRET_ENV => '0' * 64 }, true],
      ['recovery_wrong_lock_identity', { ExecutionRecord::NESTED_LOCK_IDENTITY_ENV => '0:0' }, true],
      # A copied environment without the inherited lock descriptor grants nothing.
      ['recovery_copied_env_without_descriptor', {}, false]
    ]
    steps = cases.each_with_index.map do |(id, env, pass_fd), index|
      nested_step(inner_cli_args(id, leaf), result: File.join(@tmpdir, "recovery-forgery-#{index}.json"),
                                            env: env, pass_fd: pass_fd)
    end
    valid_result = File.join(@tmpdir, 'recovery-forgery-valid.json')
    steps << nested_step(inner_cli_args('valid_after_recovery_refusals', leaf), result: valid_result)

    recovery = start_cli(recovery_cli_args(old_id, nested_parent(steps)))
    wait_until('recovery forgery parent did not start') { !launch_records.empty? }
    steps.first(cases.length).each do |step|
      assert_nested_refused(nested_result(step['result']), ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED)
    end
    valid = nested_result(valid_result)
    assert_equal 0, valid.fetch('exitstatus'), valid.fetch('stderr')

    recovered = finish_cli(recovery)
    assert_equal 0, recovered[2].exitstatus, recovered[1]
    successor_id = successor_execution_id(old_id)
    cases.map(&:first).each { |id| refute File.exist?(record_path(id)), id }
    assert_equal successor_id, ExecutionRecord.load(record_path('valid_after_recovery_refusals')).parent_execution_id
    assert_equal ExecutionRecord::STATUS_COMPLETED, ExecutionRecord.load(record_path(successor_id)).status
    assert_equal old_bytes, File.binread(record_path(old_id))
    assert_equal state_bytes, File.binread(@application_state)
    assert_equal 2, launch_records.size, 'refused nested requests never launch and never release the lineage'
  end
end

class ExecutionRecoveryTest < Minitest::Test
  # A contender process: waits for a shared go-file, then races to acquire
  # the exact same execution identity. ARGV: lib_path, go_file, exec_path,
  # side_effect_path, result_path. Single-quoted heredoc so #{...} below is
  # evaluated by the spawned child, never by the parent test process.
  CONTENDER_SCRIPT = <<~'RUBY'
    lib_path, go_file, exec_path, side_effect_path, result_path = ARGV
    require lib_path

    until File.exist?(go_file)
      sleep 0.001
    end

    begin
      recovery = ExecutionRecord.acquire!(
        exec_path, task_id: 'RACE_TASK', execution_id: 'race_exec', pid: Process.pid
      )
      if recovery.classification.nil?
        current = File.file?(side_effect_path) ? File.read(side_effect_path).to_i : 0
        File.write(side_effect_path, (current + 1).to_s)
        sleep 0.3
        File.write(result_path, 'RAN_UPSTREAM')
      else
        File.write(result_path, "DID_NOT_RUN:#{recovery.classification}")
      end
    rescue ExecutionRecord::DuplicateExecutionError => e
      File.write(result_path, "DID_NOT_RUN:RAISED_DUPLICATE:#{e.message}")
    end
  RUBY

  def setup
    @tmpdir = TaskCheckpointTestScratch.mktmpdir('execution_recovery_test_')
  end

  def teardown
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.directory?(@tmpdir)
  end

  def exec_path(name)
    File.join(@tmpdir, 'executions', "#{name}.json")
  end

  # A. Active prior process: duplicate launch rejected.
  def test_a_active_prior_process_blocks_duplicate_launch
    path = exec_path('active')
    pid = Process.spawn('sleep', '5')
    begin
      ExecutionRecord.start!(path, task_id: 'T_A', execution_id: 'active', pid: pid)
      assert_raises(ExecutionRecord::DuplicateExecutionError) do
        ExecutionRecord.recover_before_execution(path)
      end
    ensure
      Process.kill('TERM', pid)
      Process.wait(pid)
    end
  end

  # B. Completed prior execution: result reused, upstream command not rerun.
  def test_b_completed_prior_execution_is_reused_without_rerunning_upstream
    counter_path = File.join(@tmpdir, 'run_counter')
    File.write(counter_path, '0')
    path = exec_path('completed')
    capture_file = File.join(@tmpdir, 'captures', 'completed_capture.json')

    bump = ['ruby', '-e', "n = File.read(#{counter_path.inspect}).to_i; File.write(#{counter_path.inspect}, (n + 1).to_s)"]
    DurableCommandCapture.run_and_capture(
      bump, file_path: capture_file, task_id: 'T_B', execution_id: 'completed',
      execution_identity: {
        'repository_path' => @tmpdir,
        'worktree_path' => @tmpdir,
        'task_id' => 'T_B',
        'execution_id' => 'completed'
      }
    )
    assert_equal '1', File.read(counter_path)

    record = ExecutionRecord.start!(path, task_id: 'T_B', execution_id: 'completed', pid: Process.pid)
    record.complete!(path, durable_capture_path: capture_file)

    recovery = ExecutionRecord.recover_before_execution(path)
    assert_equal ExecutionRecord::CLASSIFICATION_COMPLETED, recovery.classification
    refute_nil recovery.durable_capture
    assert_equal 0, recovery.durable_capture.exit_status
    assert_equal '1', File.read(counter_path),
                 'the upstream command must not be re-invoked when reusing a completed result'
  end

  # C. Incomplete prior execution: classified TERMINATED_INCOMPLETE; rerun
  # eligibility is delegated to the original task authority, not decided here.
  def test_c_terminated_incomplete_execution_delegates_rerun_eligibility
    path = exec_path('terminated_incomplete')
    pid = Process.spawn('ruby', '-e', 'exit 0')
    Process.wait(pid)

    ExecutionRecord.start!(path, task_id: 'T_C', execution_id: 'terminated_incomplete', pid: pid)

    recovery = ExecutionRecord.recover_before_execution(path)
    assert_equal ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE, recovery.classification
    assert_nil recovery.durable_capture
  end

  # D. Unresolved overlap: fail closed, no duplicate execution.
  def test_d_unresolved_liveness_fails_closed
    path = exec_path('unresolved')
    ExecutionRecord.start!(path, task_id: 'T_D', execution_id: 'unresolved', pid: 123_456)

    ambiguous = ->(_pid) { raise 'liveness cannot be established in this sandbox' }
    assert_raises(ExecutionRecord::UnresolvedExecutionStateError) do
      ExecutionRecord.recover_before_execution(path, pid_alive: ambiguous)
    end
  end

  # D. Unresolved overlap: a malformed record also fails closed.
  def test_d_malformed_record_fails_closed
    path = exec_path('malformed')
    FileUtils.mkdir_p(File.dirname(path))
    File.write(path, '{ this is not valid json')

    assert_raises(ExecutionRecord::UnresolvedExecutionStateError) do
      ExecutionRecord.recover_before_execution(path)
    end
  end

  def test_no_prior_record_is_not_treated_as_failure
    path = exec_path('never_started')
    recovery = ExecutionRecord.recover_before_execution(path)
    assert_nil recovery.classification
    assert_nil recovery.execution_record
    assert_nil recovery.durable_capture
  end

  # E. .acquire! is the atomic combination of recover_before_execution and
  # start! for one exact identity. A nil classification means this call
  # itself won: the STARTED record is already durably persisted.
  def test_e_acquire_wins_when_no_prior_record_exists
    path = exec_path('acquire_wins')
    recovery = ExecutionRecord.acquire!(path, task_id: 'T_E', execution_id: 'acquire_wins', pid: Process.pid)

    assert_nil recovery.classification
    refute_nil recovery.execution_record
    assert_equal ExecutionRecord::STATUS_STARTED, recovery.execution_record.status
    assert File.file?(path), 'acquire! must durably persist the STARTED record before returning'
  end

  # E. Once acquired, a second acquire! against the same identity while the
  # first pid is still alive must be rejected exactly like
  # recover_before_execution rejects an active prior process, never silently
  # re-acquired.
  def test_e_acquire_rejects_second_contender_once_first_is_recorded_active
    path = exec_path('acquire_rejects_duplicate')
    pid = Process.spawn('sleep', '5')
    begin
      first = ExecutionRecord.acquire!(path, task_id: 'T_F', execution_id: 'acquire_rejects_duplicate', pid: pid)
      assert_nil first.classification

      assert_raises(ExecutionRecord::DuplicateExecutionError) do
        ExecutionRecord.acquire!(path, task_id: 'T_F', execution_id: 'acquire_rejects_duplicate', pid: Process.pid)
      end
    ensure
      Process.kill('TERM', pid)
      Process.wait(pid)
    end
  end

  # E. The concurrency regression: two independent contenders (real OS
  # processes, not threads) race to acquire the exact same execution
  # identity from no prior record, synchronized via a shared go-file so the
  # acquisition race is genuinely exercised. Exactly one may reach the
  # upstream side effect; the other must not, whether it is rejected
  # outright (ACTIVE) or classified TERMINATED_INCOMPLETE if it happens to
  # observe the winner only after the winner's own process has exited.
  def test_e_concurrent_contenders_grant_execution_ownership_to_exactly_one
    lib_path = File.expand_path('../scripts/task_checkpoint.rb', __dir__)
    go_file = File.join(@tmpdir, 'go')
    path = exec_path('race')
    side_effect_path = File.join(@tmpdir, 'side_effect_counter')
    result_a = File.join(@tmpdir, 'result_a.txt')
    result_b = File.join(@tmpdir, 'result_b.txt')
    script_path = File.join(@tmpdir, 'contender.rb')
    File.write(script_path, CONTENDER_SCRIPT)

    pid_a = Process.spawn('ruby', script_path, lib_path, go_file, path, side_effect_path, result_a)
    pid_b = Process.spawn('ruby', script_path, lib_path, go_file, path, side_effect_path, result_b)

    sleep 0.2 # let both contenders reach their busy-wait before releasing them together
    File.write(go_file, 'go')

    Process.wait(pid_a)
    Process.wait(pid_b)

    outcomes = [File.read(result_a), File.read(result_b)]

    assert_equal '1', File.read(side_effect_path),
                 "exactly one contender may perform the upstream side effect, got outcomes: #{outcomes.inspect}"
    assert_equal 1, outcomes.count { |o| o == 'RAN_UPSTREAM' },
                 "expected exactly one winner, got: #{outcomes.inspect}"
    assert outcomes.any? { |o| o.start_with?('DID_NOT_RUN') },
           "expected the losing contender to not run the upstream command, got: #{outcomes.inspect}"
  end
end

# Pins the recovery-successor lineage wording that once drifted from the code.
# Whitespace is normalized so a re-wrap of the reference cannot break it.
class NestedRecoveryLineageContractTest < Minitest::Test
  REFERENCE = File.expand_path('../shared/references/task-checkpoint.md', __dir__)

  def contract
    File.read(REFERENCE, encoding: 'UTF-8').gsub(/\s+/, ' ')
  end

  def test_reference_matches_recovery_successor_lineage_behavior
    text = contract
    refute_match(/`--recover-run` passes no capability/, text)
    assert_match(/validated `--recover-run` successor, writes its own execution ID/, text)
    assert_match(/stale predecessor stays historical evidence and never delegates/, text)
    assert_match(/deterministic successor takes the lock and establishes its own lineage from the successor execution ID/, text)
    assert_match(/retry of the same transition reuses that successor identity and therefore the same semantics/, text)
    assert_match(/Independent writers remain refused/, text)
  end
end

# Focused integration coverage for the generation-2 task anchor and the
# previously verified machine-wide heavy-scientific lease.
class Gen2HeavyLeaseCompatibilityTest < Minitest::Test
  def setup
    @tmpdir = TaskCheckpointTestScratch.mktmpdir('gen2_heavy_lease_compat_')
    @main_repo = File.join(@tmpdir, 'common-repository')
    @repo = File.join(@tmpdir, 'linked-worktree')
    @worktree = File.join(@tmpdir, 'upstream-cwd')
    make_git_repo(@main_repo)
    git!(@main_repo, 'worktree', 'add', '--quiet', '--detach', @repo, 'HEAD')
    StableLockAnchor.initialize_repository_generation!(@main_repo, confirm_legacy_workers_quiescent: true)
    FileUtils.mkdir_p(@worktree)

    @source_path = ENV.fetch('FABLE_HEAVY_TEST_SOURCE', File.expand_path('../scripts/task_checkpoint.rb', __dir__))
    @test_launcher_path = File.join(@tmpdir, 'task_checkpoint_test_launcher.rb')
    launcher_source = File.read(@source_path, encoding: 'UTF-8')
    default_lock_definition = <<~'SOURCE'
      DEFAULT_LOCK_PATH = File.join('/tmp', "fable-heavy-scientific-slot-#{Process.uid}.lock").freeze
    SOURCE
    raise 'test lease path seam did not match exactly once' unless launcher_source.scan(Regexp.new(Regexp.escape(default_lock_definition))).length == 1

    launcher_source.sub!(default_lock_definition,
                         "  DEFAULT_LOCK_PATH = ENV.fetch('FABLE_TEST_HEAVY_LEASE_LOCK').freeze\n")
    # Keep ambient workstation processes outside this isolated lease fixture.
    legacy_writer_default = [
      '  def self.acquire!(**kwargs)',
      '    new(**kwargs).tap(&:acquire!)',
      '  end'
    ].join("\n") + "\n"
    isolated_acquire = [
      '  def self.acquire!(**kwargs)',
      '    kwargs[:legacy_writer_checker] ||= -> { [] }',
      '    new(**kwargs).tap(&:acquire!)',
      '  end'
    ].join("\n") + "\n"
    raise 'test legacy-writer seam did not match exactly once' unless
      launcher_source.scan(Regexp.new(Regexp.escape(legacy_writer_default))).length == 1
    launcher_source.sub!(legacy_writer_default, isolated_acquire)
    File.write(@test_launcher_path, launcher_source)
    @lease_path = File.join(@tmpdir, 'machine-heavy-scientific-slot.lock')
    @children = []
    @release_files = []
  end

  def teardown
    Array(@release_files).each { |path| File.write(path, 'release') }
    Array(@children).each do |pid|
      begin
        Timeout.timeout(12) { Process.wait(pid) }
      rescue Errno::ECHILD
        nil
      rescue Timeout::Error
        flunk "test-owned fake protected launcher did not exit: #{pid}"
      end
    end
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.directory?(@tmpdir)
  end

  def test_gen2_marker_and_legacy_lock_guards_fail_closed
    unmarked_repo = File.join(@tmpdir, 'unmarked-repository')
    unmarked_worktree = File.join(@tmpdir, 'unmarked-cwd')
    make_git_repo(unmarked_repo)
    FileUtils.mkdir_p(unmarked_worktree)
    unmarked_marker = repository_generation_path(unmarked_repo)
    refute File.exist?(unmarked_marker)

    task_id = 'UNMARKED_TASK'
    child_marker = File.join(@tmpdir, 'unmarked-child-started')
    output = run_cli(protected_args(
      task_id: task_id, execution_id: 'unmarked-run',
      command: quick_fake_command(child_marker), repo: unmarked_repo,
      worktree: unmarked_worktree, run_root: make_run_root('unmarked-run')
    ))
    refute output[2].success?
    assert_match(/generation-2 lock marker/, output[0] + output[1])
    refute File.exist?(unmarked_marker), 'protected --run must not initialize repository authority'
    refute File.exist?(child_marker), 'missing generation authority must stop before child launch'
    refute File.exist?(ExecutionRecord.default_path(unmarked_repo, task_id, 'unmarked-run'))
    refute File.exist?(ExecutionRecord.task_lock_path(unmarked_repo, task_id))
    refute File.exist?(@lease_path)

    legacy_task = 'LEGACY_TASK'
    legacy_lock = ExecutionRecord.legacy_task_lock_path(@repo, legacy_task)
    FileUtils.mkdir_p(File.dirname(legacy_lock))
    File.binwrite(legacy_lock, "pre-existing legacy lock\n")
    original_legacy_bytes = File.binread(legacy_lock)
    legacy_child = File.join(@tmpdir, 'legacy-child-started')
    legacy_output = run_cli(protected_args(
      task_id: legacy_task, execution_id: 'legacy-blocked',
      command: quick_fake_command(legacy_child), run_root: make_run_root('legacy-run')
    ))
    refute legacy_output[2].success?
    assert_match(/legacy.*migration gate required/i, legacy_output[0] + legacy_output[1])
    assert_equal original_legacy_bytes, File.binread(legacy_lock)
    refute File.exist?(legacy_child), 'an unresolved legacy lock must stop before child launch'
    refute File.exist?(ExecutionRecord.default_path(@repo, legacy_task, 'legacy-blocked'))
    refute File.exist?(@lease_path)
  end

  def test_task_and_checkpoint_lock_paths_use_git_common_directory
    common = common_directory(@repo)
    assert_equal File.realpath(File.join(@main_repo, '.git')), common
    assert File.file?(File.join(@repo, '.git')), 'fixture must be a linked worktree with a .git pointer'

    task_lock = ExecutionRecord.task_lock_path(@repo, 'ANCHOR_TASK')
    assert task_lock.start_with?("#{common}/fable-locks/task-execution/")

    checkpoint_path = TaskCheckpoint.default_path(@repo, 'ANCHOR_CHECKPOINT')
    checkpoint = TaskCheckpoint.new(
      task_id: 'ANCHOR_CHECKPOINT',
      repository: @repo,
      worktree: @repo,
      authoritative_packet_ref: 'README.md',
      branch: 'compat-test',
      current_head: 'synthetic-head',
      current_tree: 'synthetic-tree',
      task_lifecycle_state: 'IN_PROGRESS',
      next_action: 'verify generation-2 checkpoint lock',
      authorization_boundary: 'NONE',
      updated_at: Time.now.utc.iso8601,
      revision: 1
    )
    assert checkpoint.save(checkpoint_path)

    checkpoint_lock = TaskCheckpoint.lock_path_for(checkpoint_path)
    assert checkpoint_lock.start_with?("#{common}/fable-locks/checkpoint-sidecar/")
    assert File.file?(checkpoint_lock)
    refute File.exist?("#{checkpoint_path}.lock"), 'Git worktrees use the common-directory checkpoint anchor'
    marker = repository_generation_path(@repo)
    assert File.file?(marker)
  end

  def test_heavy_protected_run_excludes_competing_tasks_until_child_exits
    release = File.join(@tmpdir, 'release-heavy-owner')
    @release_files << release
    owner_marker = File.join(@tmpdir, 'owner-child-started')
    holding_child = <<~'RUBY'
      marker, release = ARGV
      File.write(marker, Process.pid.to_s)
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 15
      until File.file?(release)
        abort 'fake heavy child release timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
        sleep 0.01
      end
      STDOUT.write("FAKE_HEAVY_CHILD_DONE\n")
    RUBY
    owner_task = 'HEAVY_TASK_A'
    owner_root = make_run_root('heavy-owner')
    owner_pid = start_cli(protected_args(
      task_id: owner_task, execution_id: 'heavy-owner',
      command: [RbConfig.ruby, '-e', holding_child, owner_marker, release],
      run_root: owner_root
    ), 'heavy-owner')
    wait_for_file(owner_marker)

    owner_record = ExecutionRecord.load(ExecutionRecord.default_path(@repo, owner_task, 'heavy-owner'))
    assert_equal true, owner_record.heavy_scientific
    assert_equal owner_root, owner_record.heavy_run_root

    common = common_directory(@repo)
    task_lock_path = ExecutionRecord.task_lock_path(@repo, owner_task)
    assert task_lock_path.start_with?("#{common}/fable-locks/task-execution/")
    lineage = JSON.parse(File.binread(task_lock_path))
    assert_equal StableLockAnchor::GENERATION, lineage.fetch('lock_generation')
    task_probe = StableLockAnchor.open_existing_lock(
      @repo, 'task-execution', owner_task, filename: 'execution.lock'
    )
    begin
      refute task_probe.flock(File::LOCK_EX | File::LOCK_NB),
             'the common-directory task lock remains held while the child runs'
    ensure
      task_probe.close unless task_probe.closed?
    end

    same_task_marker = File.join(@tmpdir, 'same-task-child-started')
    same_task_attempt = run_cli(protected_args(
      task_id: owner_task, execution_id: 'same-task-contender',
      command: quick_fake_command(same_task_marker), run_root: owner_root
    ))
    refute same_task_attempt[2].success?
    refute File.exist?(same_task_marker), 'the task anchor excludes a second execution ID'

    other_task_marker = File.join(@tmpdir, 'other-task-child-started')
    other_task_attempt = run_cli(protected_args(
      task_id: 'HEAVY_TASK_B', execution_id: 'heavy-contender',
      command: quick_fake_command(other_task_marker),
      run_root: make_run_root('heavy-contender')
    ))
    assert_equal 3, other_task_attempt[2].exitstatus, other_task_attempt[0] + other_task_attempt[1]
    assert_includes other_task_attempt[0] + other_task_attempt[1], 'STOPPED_HEAVY_SLOT_OWNED'
    refute File.exist?(other_task_marker), 'the machine-wide lease blocks a different task before launch'
    blocked_capture = completed_capture('HEAVY_TASK_B', 'heavy-contender')
    assert_equal 3, blocked_capture.exit_status

    lease_probe = File.open(@lease_path, File::RDWR)
    begin
      refute lease_probe.flock(File::LOCK_EX | File::LOCK_NB),
             'the machine-wide heavy lease remains held while the protected child runs'
    ensure
      lease_probe.close unless lease_probe.closed?
    end

    File.write(release, 'release')
    owner_status = wait_cli(owner_pid)
    assert_equal 0, owner_status.exitstatus
    owner_capture = completed_capture(owner_task, 'heavy-owner')
    assert_equal 0, owner_capture.exit_status
    assert_includes owner_capture.stdout, 'FAKE_HEAVY_CHILD_DONE'

    available_probe = File.open(@lease_path, File::RDWR)
    begin
      assert available_probe.flock(File::LOCK_EX | File::LOCK_NB),
             'natural child termination releases the machine-wide lease'
      available_probe.flock(File::LOCK_UN)
    ensure
      available_probe.close unless available_probe.closed?
    end

    fresh_marker = File.join(@tmpdir, 'fresh-child-started')
    fresh_run = run_cli(protected_args(
      task_id: 'HEAVY_TASK_C', execution_id: 'after-release',
      command: quick_fake_command(fresh_marker), run_root: make_run_root('after-release')
    ))
    assert_equal 0, fresh_run[2].exitstatus, fresh_run[0] + fresh_run[1]
    assert File.file?(fresh_marker)
    assert_equal 0, completed_capture('HEAVY_TASK_C', 'after-release').exit_status
  ensure
    File.write(release, 'release') if release
  end

  def test_nested_heavy_protected_run_reuses_both_lock_authorities
    task_id = 'NESTED_HEAVY_TASK'
    nested_marker = File.join(@tmpdir, 'nested-leaf-started')
    run_root = make_run_root('nested-heavy-run')
    nested_driver = <<~'RUBY'
      require 'open3'
      require 'rbconfig'
      launcher, repo, worktree, task_id, run_root, marker = ARGV
      leaf = [RbConfig.ruby, '-e',
              'File.write(ARGV.fetch(0), "nested\\n"); puts "NESTED_LEAF_OK"', marker]
      nested_args = ['--run', '--heavy-scientific', '--repo', repo, '--worktree', worktree,
                     '--task-id', task_id, '--execution-id', 'nested-inner',
                     '--run-root', run_root, '--', *leaf]
      command = [RbConfig.ruby, launcher, *nested_args]
      stdout, stderr, status = Open3.capture3(*command, { 10 => 10, 11 => 11 })
      STDERR.write(stderr)
      STDOUT.write(stdout)
      exit(status.exitstatus || 1)
    RUBY
    outer = run_cli(protected_args(
      task_id: task_id, execution_id: 'nested-outer',
      command: [RbConfig.ruby, '-e', nested_driver, @test_launcher_path,
                @repo, @worktree, task_id, run_root, nested_marker],
      run_root: run_root
    ))

    assert_equal 0, outer[2].exitstatus, outer[0] + outer[1]
    assert File.file?(nested_marker)
    assert_includes outer[0], 'NESTED_LEAF_OK'
    assert_equal 1, outer[1].scan('LEASE_ACQUIRED / WOULD_LAUNCH').length,
                 'the nested run reuses the inherited machine-wide lease'
    outer_capture = completed_capture(task_id, 'nested-outer')
    nested_capture = completed_capture(task_id, 'nested-inner')
    assert_equal 0, outer_capture.exit_status
    assert_equal 0, nested_capture.exit_status
    assert_includes nested_capture.stdout, 'NESTED_LEAF_OK'
    assert_equal 'nested-outer', ExecutionRecord.load(
      ExecutionRecord.default_path(@repo, task_id, 'nested-inner')
    ).parent_execution_id
    assert_equal true, ExecutionRecord.load(
      ExecutionRecord.default_path(@repo, task_id, 'nested-outer')
    ).heavy_scientific
    assert_equal true, ExecutionRecord.load(
      ExecutionRecord.default_path(@repo, task_id, 'nested-inner')
    ).heavy_scientific
    lineage = JSON.parse(File.binread(ExecutionRecord.task_lock_path(@repo, task_id)))
    assert_equal StableLockAnchor::GENERATION, lineage.fetch('lock_generation')
  end

  def test_heavy_recovery_holds_slot_excludes_contenders_and_replays_completion
    task_id = 'EXPLICIT_HEAVY_RECOVERY'
    old_id = 'stale-heavy-run'
    application_state = seed_stale_execution(task_id: task_id, execution_id: old_id, heavy_scientific: true)
    release = File.join(@tmpdir, 'release-heavy-recovery')
    @release_files << release
    child_marker = File.join(@tmpdir, 'heavy-recovery-child-started')
    launch_log = File.join(@tmpdir, 'heavy-recovery-launches')
    command = [RbConfig.ruby, '-e', <<~'RUBY', child_marker, release, launch_log]
      marker, release, launches = ARGV
      File.write(marker, Process.pid.to_s)
      File.open(launches, 'a') { |file| file.puts(Process.pid) }
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 12
      until File.file?(release)
        abort 'fake recovery child release timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
        sleep 0.01
      end
      puts 'FAKE_RECOVERY_CHILD_DONE'
    RUBY
    args = recovery_args(task_id: task_id, old_execution_id: old_id,
                         application_state: application_state, command: command)
    recovery_pid = start_cli(args, 'heavy-recovery-owner')
    wait_for_file(child_marker)

    successor_id = ExecutionRecoveryTransition.successor_id_for(task_id, old_id)
    successor = ExecutionRecord.load(ExecutionRecord.default_path(@repo, task_id, successor_id))
    assert_equal true, successor.heavy_scientific
    assert_equal File.realpath(@worktree), successor.heavy_run_root
    lease_metadata = JSON.parse(File.binread("#{@lease_path}.owner.json"))
    assert_equal task_id, lease_metadata.fetch('TASK_ID')
    assert_equal successor_id, lease_metadata.fetch('EXECUTION_ID')
    assert_equal File.realpath(@worktree), lease_metadata.fetch('RUN_ROOT')

    lease_probe = File.open(@lease_path, File::RDWR)
    begin
      refute lease_probe.flock(File::LOCK_EX | File::LOCK_NB),
             'recovery holds the machine-wide lease while its child is alive'
    ensure
      lease_probe.close unless lease_probe.closed?
    end

    same_run_attempt = run_cli(args)
    assert_equal 1, same_run_attempt[2].exitstatus
    assert_includes same_run_attempt[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    assert_equal 1, File.readlines(launch_log).length,
                 'only one recovery process may resume the same logical run'

    other_child = File.join(@tmpdir, 'other-heavy-run-during-recovery')
    other_run = run_cli(protected_args(
      task_id: 'HEAVY_TASK_DURING_RECOVERY', execution_id: 'competing-heavy-run',
      command: quick_fake_command(other_child), run_root: make_run_root('competing-heavy-run')
    ))
    assert_equal 3, other_run[2].exitstatus, other_run[0] + other_run[1]
    assert_includes other_run[0] + other_run[1], 'STOPPED_HEAVY_SLOT_OWNED'
    refute File.exist?(other_child), 'a competing heavy task is stopped before its child starts'

    File.write(release, 'release')
    recovery_status = wait_cli(recovery_pid)
    assert_equal 0, recovery_status.exitstatus
    completed = completed_capture(task_id, successor_id)
    assert_equal 0, completed.exit_status
    assert_includes completed.stdout, 'FAKE_RECOVERY_CHILD_DONE'

    available_probe = File.open(@lease_path, File::RDWR)
    begin
      assert available_probe.flock(File::LOCK_EX | File::LOCK_NB),
             'natural recovery-child completion releases the machine-wide lease'
      available_probe.flock(File::LOCK_UN)
    ensure
      available_probe.close unless available_probe.closed?
    end

    replay = run_cli(args)
    assert_equal 0, replay[2].exitstatus, replay[0] + replay[1]
    assert_equal 1, File.readlines(launch_log).length,
                 'a completed recovery capture is replayed without restarting its child'
  ensure
    File.write(release, 'release') if release
  end

  def test_legacy_scientific_recovery_is_blocked_by_another_heavy_owner
    release = File.join(@tmpdir, 'release-first-run-owner')
    @release_files << release
    owner_marker = File.join(@tmpdir, 'first-run-owner-started')
    holding_child = <<~'RUBY'
      marker, release = ARGV
      File.write(marker, Process.pid.to_s)
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 12
      until File.file?(release)
        abort 'fake heavy child release timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
        sleep 0.01
      end
      puts 'FAKE_HEAVY_OWNER_DONE'
    RUBY
    owner_pid = start_cli(protected_args(
      task_id: 'HEAVY_SLOT_OWNER', execution_id: 'slot-owner',
      command: [RbConfig.ruby, '-e', holding_child, owner_marker, release],
      run_root: make_run_root('slot-owner')
    ), 'first-run-owner')
    wait_for_file(owner_marker)

    recovery_task = 'B649_LEGACY_RECOVERY'
    old_id = 'legacy-stale-run'
    application_state = seed_stale_execution(task_id: recovery_task, execution_id: old_id)
    recovery_child = File.join(@tmpdir, 'legacy-recovery-child-started')
    result = run_cli(recovery_args(
      task_id: recovery_task, old_execution_id: old_id,
      application_state: application_state,
      command: quick_fake_command(recovery_child), heavy_flag: false
    ))

    assert_equal 3, result[2].exitstatus, result[0] + result[1]
    assert_includes result[0] + result[1], 'STOPPED_HEAVY_SLOT_OWNED'
    refute File.exist?(recovery_child), 'legacy task identity classifies recovery before child launch'
    successor_id = ExecutionRecoveryTransition.successor_id_for(recovery_task, old_id)
    assert_equal 3, completed_capture(recovery_task, successor_id).exit_status
    successor = ExecutionRecord.load(ExecutionRecord.default_path(@repo, recovery_task, successor_id))
    assert_equal true, successor.heavy_scientific

    File.write(release, 'release')
    assert_equal 0, wait_cli(owner_pid).exitstatus
  ensure
    File.write(release, 'release') if release
  end

  def test_heavy_recovery_flag_alone_cannot_designate_an_ordinary_stale_execution
    task_id = 'ORDINARY_RECOVERY_TASK'
    old_id = 'ordinary-stale-run'
    application_state = seed_stale_execution(task_id: task_id, execution_id: old_id)
    child_marker = File.join(@tmpdir, 'ordinary-recovery-child-started')

    result = run_cli(recovery_args(
      task_id: task_id, old_execution_id: old_id, application_state: application_state,
      command: quick_fake_command(child_marker), heavy_flag: true
    ))

    refute result[2].success?
    assert_includes result[0] + result[1], 'heavy authority from the existing execution or task command'
    refute File.exist?(child_marker)
    refute File.exist?(ExecutionRecoveryTransition.default_path(@repo, task_id, old_id)),
           'the assertion is checked before reserving a recovery transition'
    refute File.exist?(@lease_path), 'the assertion alone must not acquire a Heavy Slot'
  end

  def test_heavy_retry_recovery_keeps_the_original_slot_authority
    task_id = 'HEAVY_RETRY_RECOVERY'
    old_id = 'stale-heavy-retry-source'
    application_state = seed_stale_execution(task_id: task_id, execution_id: old_id, heavy_scientific: true)
    failed_command = [RbConfig.ruby, '-e', "puts 'FAKE_RECOVERY_FAILURE'; exit 7"]

    first = run_cli(recovery_args(
      task_id: task_id, old_execution_id: old_id, application_state: application_state,
      command: failed_command
    ))
    assert_equal 7, first[2].exitstatus
    first_successor_id = ExecutionRecoveryTransition.successor_id_for(task_id, old_id)
    assert_equal 7, completed_capture(task_id, first_successor_id).exit_status

    retry_child = File.join(@tmpdir, 'heavy-retry-child-started')
    retry_successor_id = ExecutionRecoveryTransition.successor_id_for(task_id, first_successor_id)
    retry_args = ['--retry-recover-run', '--heavy-scientific', '--repo', @repo,
                  '--worktree', @worktree, '--task-id', task_id,
                  '--execution-id', first_successor_id, '--application-state', application_state,
                  '--', *quick_fake_command(retry_child)]
    retried = run_cli(retry_args)

    assert_equal 0, retried[2].exitstatus, retried[0] + retried[1]
    assert_includes retried[1], 'LEASE_ACQUIRED / WOULD_LAUNCH'
    assert File.file?(retry_child)
    retry_record = ExecutionRecord.load(ExecutionRecord.default_path(@repo, task_id, retry_successor_id))
    assert_equal true, retry_record.heavy_scientific
    assert_equal File.realpath(@worktree), retry_record.heavy_run_root
    assert_equal 0, completed_capture(task_id, retry_successor_id).exit_status
    refute File.exist?("#{@lease_path}.owner.json"), 'natural retry completion clears lease ownership metadata'
  end

  private

  def make_git_repo(path)
    FileUtils.mkdir_p(path)
    git!(path, 'init', '--quiet')
    git!(path, 'config', 'user.name', 'Fable Compatibility Test')
    git!(path, 'config', 'user.email', 'fable-compatibility@example.invalid')
    File.write(File.join(path, 'README.md'), "isolated lock fixture\n")
    git!(path, 'add', '-f', 'README.md')
    git!(path, 'commit', '--quiet', '-m', 'isolated lock fixture')
  end

  def git!(path, *args)
    stdout, stderr, status = Open3.capture3('git', '-C', path, *args)
    assert status.success?, "git #{args.join(' ')} failed: #{stdout}#{stderr}"
    stdout
  end

  def common_directory(path)
    output, stderr, status = Open3.capture3('git', '-C', path, 'rev-parse', '--git-common-dir')
    assert status.success?, stderr
    File.realpath(File.expand_path(output.strip, path))
  end

  def repository_generation_path(path)
    File.join(common_directory(path), 'fable-locks', StableLockAnchor::REPOSITORY_GENERATION_FILENAME)
  end

  def make_run_root(name)
    path = File.join(@tmpdir, 'run-roots', name)
    FileUtils.mkdir_p(path)
    path
  end

  def quick_fake_command(marker)
    [RbConfig.ruby, '-e', 'File.write(ARGV.fetch(0), "FAKE_CHILD_STARTED"); puts "FAKE_CHILD_DONE"', marker]
  end

  def protected_args(task_id:, execution_id:, command:, repo: @repo, worktree: @worktree, run_root:)
    ['--run', '--heavy-scientific', '--repo', repo, '--worktree', worktree,
     '--task-id', task_id, '--execution-id', execution_id,
     '--run-root', run_root, '--', *command]
  end

  def recovery_args(task_id:, old_execution_id:, application_state:, command:, heavy_flag: true,
                    repo: @repo, worktree: @worktree)
    args = ['--recover-run']
    args << '--heavy-scientific' if heavy_flag
    args.concat(['--repo', repo, '--worktree', worktree,
                 '--task-id', task_id, '--execution-id', old_execution_id,
                 '--application-state', application_state, '--', *command])
  end

  def seed_stale_execution(task_id:, execution_id:, heavy_scientific: nil)
    application_state = File.join(@tmpdir, "#{task_id}-application-state.bin")
    File.binwrite(application_state, "fake scientific checkpoint for #{task_id}\n")
    ExecutionRecord.with_task_lock(@repo, task_id) { |_lock| }
    pid = Process.spawn(RbConfig.ruby, '-e', 'exit 0')
    Process.wait(pid)
    attrs = { task_id: task_id, execution_id: execution_id, pid: pid }
    if heavy_scientific
      attrs[:heavy_scientific] = true
      attrs[:heavy_run_root] = File.realpath(@worktree)
    end
    ExecutionRecord.start!(ExecutionRecord.default_path(@repo, task_id, execution_id), **attrs)
    application_state
  end

  def cli_argv(args)
    [RbConfig.ruby, @test_launcher_path, *args]
  end

  def run_cli(args)
    Open3.capture3({ 'FABLE_TEST_HEAVY_LEASE_LOCK' => @lease_path }, *cli_argv(args))
  end

  def start_cli(args, label)
    stdout = File.join(@tmpdir, "#{label}.stdout")
    stderr = File.join(@tmpdir, "#{label}.stderr")
    pid = Process.spawn(
      { 'FABLE_TEST_HEAVY_LEASE_LOCK' => @lease_path },
      *cli_argv(args),
      out: stdout,
      err: stderr,
      pgroup: true
    )
    @children << pid
    pid
  end

  def wait_cli(pid)
    _, status = Process.wait2(pid)
    @children.delete(pid)
    status
  end

  def wait_for_file(path)
    Timeout.timeout(5) { sleep 0.01 until File.file?(path) }
  rescue Timeout::Error
    flunk "fake protected child did not reach its marker: #{path}"
  end

  def completed_capture(task_id, execution_id)
    record = ExecutionRecord.load(ExecutionRecord.default_path(@repo, task_id, execution_id))
    assert_equal ExecutionRecord::STATUS_COMPLETED, record.status
    capture = DurableCommandCapture.load(record.durable_capture_path)
    assert capture.complete?
    capture
  end
end

class Gen2CompletedCaptureReplayCompatTest < Minitest::Test
  include SyntheticGitRepo

  CLI = File.expand_path('../scripts/task_checkpoint.rb', __dir__)
  CLI_TIMEOUT_SECONDS = 15
  TASK_ID = 'GEN2_COMPAT_TASK'

  def setup
    @tmpdir = TaskCheckpointTestScratch.mktmpdir('gen2_compat_test_')
    @repo = File.join(@tmpdir, 'repo')
    @worktree = File.join(@tmpdir, 'worktree')
    @caller = File.join(@tmpdir, 'caller')
    [@repo, @worktree, @caller].each { |p| FileUtils.mkdir_p(p) }
    initialize_synthetic_git_repo(@repo)
    ExecutionRecord.with_task_lock(@repo, TASK_ID) { assert true }

    @launches = File.join(@tmpdir, 'launches')
    @sentinel_marker = File.join(@tmpdir, 'upstream_sentinel.ran')
    @application_state = File.join(@tmpdir, 'application-checkpoint.bin')
    File.binwrite(@application_state, "synthetic-checkpoint\0v1\n")
    @children = []
  end

  def teardown
    @children.each do |child|
      begin
        Process.kill('TERM', -child[:wait].pid)
      rescue Errno::ESRCH, Errno::EPERM
      end
    end
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.directory?(@tmpdir)
  end

  def sentinel_upstream(result: 0)
    [RbConfig.ruby, '-e', 'marker, res = ARGV; File.write(marker, "RUN"); exit(Integer(res))', @sentinel_marker, result.to_s]
  end

  def cli_args(identity, command = nil)
    command ||= sentinel_upstream
    ['--run', '--repo', @repo, '--worktree', @worktree,
     '--task-id', TASK_ID, '--execution-id', identity, '--', *command]
  end

  def start_cli(args, cwd: @caller)
    stdin, stdout, stderr, wait = Open3.popen3(RbConfig.ruby, CLI, *args, { chdir: cwd, pgroup: true })
    stdin.close
    readers = [stdout, stderr].map { |io| Thread.new { begin; io.read; ensure; io.close; end } }
    child = { wait: wait, readers: readers }
    @children << child
    child
  end

  def finish_cli(child)
    assert child[:wait].join(CLI_TIMEOUT_SECONDS), 'CLI timed out'
    child[:readers].each { |reader| assert reader.join(5), 'CLI output timed out' }
    [*child[:readers].map(&:value), child[:wait].value]
  end

  def run_cli(args, **options)
    finish_cli(start_cli(args, **options))
  end

  def record_path(identity)
    ExecutionRecord.default_path(@repo, TASK_ID, identity)
  end

  def capture_path(identity)
    DurableCommandCapture.default_path(@repo, TASK_ID, identity)
  end

  # A. 新 Capture 的 explicit path 可重播 (New Capture replays from explicit path)
  def test_11a_new_capture_explicit_path_replays
    identity = 'case_11a'
    custom_capture = File.join(@tmpdir, 'custom_captures', "#{identity}.json")
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "original_out\n",
      stderr: "original_err\n",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(custom_capture)

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    record.complete!(record_path(identity), durable_capture_path: custom_capture)

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal "original_out\n", result[0]
    assert_equal "original_err\n", result[1]
    assert_equal 0, result[2].exitstatus
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'
  end

  # B. 舊路徑失效但合法 Capture 已就位 (Original path invalid, canonical capture in place)
  def test_11b_original_path_invalid_falls_back_to_canonical_capture
    identity = 'case_11b'
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "fallback_out\n",
      stderr: "fallback_err\n",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent_old_machine', 'captures', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)
    before_record = File.binread(record_path(identity))

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal "fallback_out\n", result[0]
    assert_equal "fallback_err\n", result[1]
    assert_equal 0, result[2].exitstatus
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'
    assert_equal before_record, File.binread(record_path(identity)), 'ExecutionRecord on disk must NOT be modified'
  end

  # C. Capture 不存在 (Capture missing)
  def test_11c_capture_does_not_exist_fails_closed
    identity = 'case_11c'
    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    result = run_cli(cli_args(identity))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'
  end

  # D. Capture 損毀 (Capture corrupt JSON)
  def test_11d_capture_corrupt_fails_closed
    identity = 'case_11d'
    FileUtils.mkdir_p(File.dirname(capture_path(identity)))
    File.write(capture_path(identity), '{ invalid json syntax !!!')

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    result = run_cli(cli_args(identity))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'
  end

  # E. Capture 不完整 (Capture incomplete)
  def test_11e_capture_incomplete_fails_closed
    identity = 'case_11e'
    FileUtils.mkdir_p(File.dirname(capture_path(identity)))
    incomplete_json = JSON.generate(schema_version: 1, command: ['ruby', '-e', '1'], started_at: Time.now.utc.iso8601, ended_at: Time.now.utc.iso8601)
    File.write(capture_path(identity), incomplete_json)

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    result = run_cli(cli_args(identity))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'
  end

  # F. Capture command 不相符 (Capture command mismatch)
  def test_11f_capture_command_mismatch_rejected
    identity = 'case_11f'
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "out\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    # Direct recover_before_execution check
    assert_raises(ExecutionRecord::UnresolvedExecutionStateError) do
      ExecutionRecord.recover_before_execution(record_path(identity), command: ['ruby', '-e', 'puts 999'])
    end

    # CLI check: command mismatch must fail closed with exit 1 and not execute upstream
    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 999']))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute on command mismatch'
  end

  # G. Capture Symlink / 路徑跳脫 (Symlink and path traversal rejected)
  def test_11g_capture_symlink_and_traversal_rejected
    identity = 'case_11g'
    real_capture = File.join(@tmpdir, 'outside_capture.json')
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "symlink_out\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(real_capture)

    # G1: File is a symlink
    FileUtils.mkdir_p(File.dirname(capture_path(identity)))
    File.symlink(real_capture, capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'

    # G2: Directory traversal in execution_id / task_id is rejected by stable_component?
    refute ExecutionRecord.stable_component?('../escape')
    refute ExecutionRecord.stable_component?('sub/dir')
    refute ExecutionRecord.stable_component?('')
    refute ExecutionRecord.stable_component?('.')
  end

  # H. COMPLETED 原始 exit status = 0
  def test_11h_completed_exit_0_replayed
    identity = 'case_11h'
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "exit_0_out\n",
      stderr: "exit_0_err\n",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 0, result[2].exitstatus
    assert_equal "exit_0_out\n", result[0]
    assert_equal "exit_0_err\n", result[1]
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'
  end

  # I. COMPLETED 原始 exit status 非 0
  def test_11i_completed_exit_nonzero_preserved_not_converted_to_zero
    identity = 'case_11i'
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "exit_23_out\n",
      stderr: "exit_23_err\n",
      exit_status: 23,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 23, result[2].exitstatus, 'non-zero exit status must be preserved and not converted to 0'
    assert_equal "exit_23_out\n", result[0]
    assert_equal "exit_23_err\n", result[1]
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'
  end

  # J. STARTED 不得自動重跑 (STARTED does not auto-rerun)
  def test_11j_started_record_does_not_auto_rerun
    identity = 'case_11j'
    dead_pid = Process.spawn(RbConfig.ruby, '-e', 'exit 0')
    Process.wait(dead_pid)

    # Place a valid capture at the canonical path
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "should_not_replay\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(capture_path(identity))

    # Record is STARTED, not COMPLETED
    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: dead_pid)

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'

    # Verify status remains STARTED
    loaded = ExecutionRecord.load(record_path(identity))
    assert_equal ExecutionRecord::STATUS_STARTED, loaded.status
  end

  # K. --recover-run 原防護不得退化
  def test_11k_recover_run_original_protection_preserved
    identity = 'case_11k'
    dead_pid = Process.spawn(RbConfig.ruby, '-e', 'exit 0')
    Process.wait(dead_pid)
    ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: dead_pid)

    # Reserve transition with sentinel_upstream command
    ExecutionRecoveryTransition.reserve!(
      repo_root: @repo, task_id: TASK_ID, old_execution_id: identity,
      application_state_path: @application_state,
      worktree_path: @worktree,
      command: sentinel_upstream
    )

    # Attempt --recover-run with mismatched command
    args = ['--recover-run', '--repo', @repo, '--worktree', @worktree,
            '--task-id', TASK_ID, '--execution-id', identity,
            '--application-state', @application_state, '--', RbConfig.ruby, '-e', 'exit 99']
    result = run_cli(args)
    # Must fail closed with exit status 1
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute on failed recover-run'
  end

  # 12. 隔離 Runtime 重播 Capture，不執行 upstream sentinel
  def test_12_isolated_runtime_no_upstream_sentinel_executed
    identity = 'case_12'
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "sentinel_check_out\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    # 1. 原 R1 錯誤成功案例（CLI 傳入與 capture 不同的 command）必須變成拒絕
    mismatch_result = run_cli(cli_args(identity)) # passes sentinel_upstream
    assert_equal 1, mismatch_result[2].exitstatus
    assert_includes mismatch_result[1], ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
    refute File.exist?(@sentinel_marker), 'upstream sentinel must NEVER execute on command mismatch'

    # 2. 正確相同命令可重播，且不執行 upstream
    2.times do
      result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
      assert_equal 0, result[2].exitstatus
      assert_equal "sentinel_check_out\n", result[0]
      refute File.exist?(@sentinel_marker), 'upstream sentinel must NEVER be executed'
    end
  end

  # 13. 保留 stdout、stderr、exit status 的原始語義 (Binary parity 包括非 UTF-8 bytes)
  def test_13_stdout_stderr_binary_parity
    identity = 'case_13'
    binary_out = "Binary\0Null\n\xFF\xFE\x00\x80 Non-UTF8\n\xE2\x9C\x93 UTF-8 Checkmark\n".b
    binary_err = "Err\0Binary\n\x80\xFD Non-UTF8\n\xE2\x9C\x97 Cross\n".b
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: binary_out,
      stderr: binary_err,
      exit_status: 12,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 12, result[2].exitstatus
    assert_equal binary_out.bytes, result[0].b.bytes
    assert_equal binary_err.bytes, result[1].b.bytes
    refute File.exist?(@sentinel_marker)
  end

  # 14. 缺少 schema_version 必須拒絕 (AC09, MISSING_SCHEMA_NEGATIVE)
  def test_14_missing_schema_version_rejected
    identity = 'case_14_missing_schema'
    FileUtils.mkdir_p(File.dirname(capture_path(identity)))
    # Valid capture JSON but completely missing schema_version
    json = JSON.generate(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "out\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity
    )
    File.write(capture_path(identity), json)

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    record.complete!(record_path(identity), durable_capture_path: capture_path(identity))

    # Loading capture directly must raise ValidationError
    assert_raises(DurableCommandCapture::ValidationError) do
      DurableCommandCapture.load(capture_path(identity))
    end

    # CLI replay must fail closed with exit status 1
    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute'
  end

  # 15. 不受支援的 schema_version 必須拒絕 (AC10)
  def test_15_unsupported_schema_version_rejected
    identity = 'case_15_unsupported_schema'
    FileUtils.mkdir_p(File.dirname(capture_path(identity)))
    [2, 0, '1', 99].each do |bad_ver|
      json = JSON.generate(
        schema_version: bad_ver,
        command: ['ruby', '-e', 'puts 1'],
        stdout: "out\n",
        stderr: "",
        exit_status: 0,
        started_at: Time.now.utc.iso8601,
        ended_at: Time.now.utc.iso8601,
        task_id: TASK_ID,
        execution_id: identity
      )
      File.write(capture_path(identity), json)

      assert_raises(DurableCommandCapture::ValidationError) do
        DurableCommandCapture.load(capture_path(identity))
      end
    end
  end

  # 16. 將另一個 Task 的 Capture 放入預期位置，不得被接受 (AC05, WRONG_TASK_CAPTURE_REJECTION)
  def test_16_wrong_task_capture_rejected
    identity = 'case_16_wrong_task'
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "spoofed_out\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: 'OTHER_TASK_ATTACK',
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => 'OTHER_TASK_ATTACK',
        'execution_id' => identity
      }
    )
    capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    record.complete!(record_path(identity), durable_capture_path: capture_path(identity))

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute when task_id mismatches'
  end

  # 17. 將另一個 Execution 的 Capture 放入預期位置，不得被接受 (AC06, WRONG_EXECUTION_CAPTURE_REJECTION)
  def test_17_wrong_execution_capture_rejected
    identity = 'case_17_wrong_exec'
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "spoofed_out\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: 'OTHER_EXECUTION_ATTACK',
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => 'OTHER_EXECUTION_ATTACK'
      }
    )
    capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    record.complete!(record_path(identity), durable_capture_path: capture_path(identity))

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute when execution_id mismatches'
  end

  # 18. Legacy Capture 若 execution_identity=nil，缺乏可信證據時 Fallback fail closed (AC07, LEGACY_NIL_NEGATIVE)
  def test_18_legacy_fallback_with_nil_identity_fails_closed
    identity = 'case_18_legacy_nil'
    # Legacy capture without execution_identity and without explicit task_id/execution_id
    legacy_capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "legacy_out\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601
    )
    assert_nil legacy_capture.execution_identity
    legacy_capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent_legacy', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    # Fallback to canonical location with nil execution_identity must FAIL CLOSED
    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute on legacy nil fallback'
  end

  # 19. 祖先目錄含 Symlink 必須拒絕 (AC11, ANCESTOR_SYMLINK_NEGATIVE)
  def test_19_ancestor_symlink_rejected
    identity = 'case_19_ancestor_symlink'
    outside_captures_dir = File.join(@tmpdir, 'outside_captures')
    FileUtils.mkdir_p(outside_captures_dir)

    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "ancestor_symlink_out\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(File.join(outside_captures_dir, "#{identity}.json"))

    # Test symlink at captures dir ancestor level
    captures_target = File.join(@repo, '.fable', 'checkpoints', TASK_ID, 'captures')
    FileUtils.rm_rf(captures_target) if File.exist?(captures_target) || File.symlink?(captures_target)
    FileUtils.mkdir_p(File.dirname(captures_target))
    File.symlink(outside_captures_dir, captures_target)

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    stale_path = File.join(@tmpdir, 'nonexistent', "#{identity}.json")
    record.complete!(record_path(identity), durable_capture_path: stale_path)

    result = run_cli(cli_args(identity, ['ruby', '-e', 'puts 1']))
    assert_equal 1, result[2].exitstatus
    assert_includes result[1], ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
    refute File.exist?(@sentinel_marker), 'upstream sentinel must not execute when ancestor is symlink'

    # Cleanup the symlinked captures directory
    FileUtils.rm_rf(captures_target)
  end

  # 20. Nested CLI acquisition 也比較 command，不相符時拒絕 (AC03, AC04, AC18)
  def test_20_nested_cli_command_mismatch_rejected
    identity = 'case_20_nested'
    capture = DurableCommandCapture.new(
      command: ['ruby', '-e', 'puts 1'],
      stdout: "nested_out\n",
      stderr: "",
      exit_status: 0,
      started_at: Time.now.utc.iso8601,
      ended_at: Time.now.utc.iso8601,
      task_id: TASK_ID,
      execution_id: identity,
      execution_identity: {
        'repository_path' => @repo,
        'worktree_path' => @worktree,
        'task_id' => TASK_ID,
        'execution_id' => identity
      }
    )
    capture.save(capture_path(identity))

    record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID, execution_id: identity, pid: Process.pid)
    record.complete!(record_path(identity), durable_capture_path: capture_path(identity))

    # Set up mock nested capability in ENV
    ExecutionRecord.with_task_lock(@repo, TASK_ID) do |lock|
      secret = ExecutionRecord.write_task_lineage!(lock, @repo, TASK_ID, 'parent_id')
      nested_env = ExecutionRecord.nested_capability_env(@repo, TASK_ID, lock, 'parent_id', secret)

      # In nested environment, acquire_nested_owned! must reject command mismatch
      assert_raises(ExecutionRecord::UnresolvedExecutionStateError) do
        nested = ExecutionRecord.verify_nested_capability!(
          @repo, TASK_ID, identity,
          ExecutionRecord.nested_capability_request(nested_env, @repo, TASK_ID)
        )
        ExecutionRecord.acquire_nested_owned!(
          @repo, TASK_ID, identity, nested,
          pid: Process.pid, command: ['ruby', '-e', 'puts 999']
        )
      end
    end
  end

  # 21. Explicit path and canonical fallback enforce the same Capture identity and command rules.
  def test_21_explicit_and_fallback_capture_identity_parity
    cases = {
      correct_new_capture: { accepted: true },
      legacy_nil_identity: { accepted: false },
      wrong_task_id: { accepted: false, task_id: 'OTHER_TASK_ATTACK' },
      wrong_execution_id: { accepted: false, execution_id: 'OTHER_EXECUTION_ATTACK' },
      wrong_command: { accepted: false, command: [RbConfig.ruby, '-e', 'print "different command"'] }
    }

    %i[explicit fallback].each do |location|
      cases.each do |case_name, scenario|
        identity = "case_21_#{location}_#{case_name}"
        capture_task_id = scenario.fetch(:task_id, TASK_ID)
        capture_execution_id = scenario.fetch(:execution_id, identity)
        capture_command = scenario.fetch(:command, sentinel_upstream)
        capture_stdout = "replayed_#{location}_#{case_name}\n"
        capture_attrs = {
          command: capture_command,
          stdout: capture_stdout,
          stderr: "capture_stderr\n",
          exit_status: 0,
          started_at: Time.now.utc.iso8601,
          ended_at: Time.now.utc.iso8601
        }
        unless case_name == :legacy_nil_identity
          capture_attrs[:task_id] = capture_task_id
          capture_attrs[:execution_id] = capture_execution_id
          capture_attrs[:execution_identity] = {
            'repository_path' => @repo,
            'worktree_path' => @worktree,
            'task_id' => capture_task_id,
            'execution_id' => capture_execution_id
          }
        end
        capture = DurableCommandCapture.new(capture_attrs)
        explicit_path = File.join(@tmpdir, 'explicit_captures', "#{identity}.json")
        capture.save(explicit_path) if location == :explicit
        capture.save(capture_path(identity)) if location == :fallback

        record = ExecutionRecord.start!(record_path(identity), task_id: TASK_ID,
                                        execution_id: identity, pid: Process.pid)
        durable_path = if location == :explicit
                         explicit_path
                       else
                         File.join(@tmpdir, 'missing_original_capture', "#{identity}.json")
                       end
        record.complete!(record_path(identity), durable_capture_path: durable_path)

        result = run_cli(cli_args(identity))
        refute File.exist?(@sentinel_marker), "upstream sentinel executed for #{location} #{case_name}"

        if scenario[:accepted]
          assert_equal 0, result[2].exitstatus, "#{location} #{case_name} should replay"
          assert_equal capture_stdout, result[0]
          assert_equal "capture_stderr\n", result[1]
        else
          assert_equal 1, result[2].exitstatus, "#{location} #{case_name} should fail closed"
          expected_classification = if case_name == :wrong_command
                                      ExecutionRecord::CLASSIFICATION_STATE_UNRESOLVED
                                    else
                                      ExecutionRecord::CLASSIFICATION_TERMINATED_INCOMPLETE
                                    end
          assert_includes result[1], expected_classification
        end
      end
    end
  end
end

# Disposable integration coverage for separating execution-history storage
# from the Git worktree whose common directory owns the generation-2 lock.
class DualRootLockRootCliTest < Minitest::Test
  include SyntheticGitRepo

  CLI = File.expand_path('../scripts/task_checkpoint.rb', __dir__)
  OMIT_LOCK_ROOT = Object.new.freeze

  def setup
    @tmpdir = File.realpath(TaskCheckpointTestScratch.mktmpdir('dual_root_lock_root_test_'))
    @lock_root = File.join(@tmpdir, 'git-root')
    initialize_synthetic_git_repo(@lock_root)
    @storage_root = File.join(@lock_root, '.task-data', 'task-a')
    FileUtils.mkdir_p(@storage_root)
    @worktree = @lock_root
    @outside_root = File.join(@tmpdir, 'outside-storage')
    FileUtils.mkdir_p(@outside_root)
  end

  def teardown
    FileUtils.remove_entry(@tmpdir) if @tmpdir && File.directory?(@tmpdir)
  end

  def test_nested_storage_root_keeps_history_and_capture_and_binds_lock_identity
    task_id = 'TASK_A_DUAL_ROOT'
    execution_id = 'nested-storage-run'
    marker = File.join(@tmpdir, 'nested-storage-child-started')
    stdout, stderr, status = run_cli(protected_args(
      storage_root: @storage_root, lock_root: @lock_root,
      task_id: task_id, execution_id: execution_id, command: success_command(marker)
    ))

    assert status.success?, "stdout=#{stdout.inspect} stderr=#{stderr.inspect}"
    assert File.file?(marker)
    record_path = ExecutionRecord.default_path(@storage_root, task_id, execution_id)
    capture_path = DurableCommandCapture.default_path(@storage_root, task_id, execution_id)
    assert File.file?(record_path)
    assert File.file?(capture_path)
    assert_equal ExecutionRecord::STATUS_COMPLETED, ExecutionRecord.load(record_path).status
    capture_identity = DurableCommandCapture.load(capture_path).execution_identity
    assert_equal @storage_root, capture_identity.fetch('storage_root')
    assert_equal @lock_root, capture_identity.fetch('lock_root')

    lock_path = ExecutionRecord.task_lock_path(@storage_root, task_id, lock_root: @lock_root)
    common = common_directory(@lock_root)
    assert lock_path.start_with?(File.join(common, 'fable-locks', 'task-execution'))
    anchor = JSON.parse(File.read(File.join(File.dirname(lock_path), 'authority.json')))
    assert_equal @lock_root, anchor.fetch('repository')
    assert_equal @lock_root, anchor.fetch('lock_root')
    assert_equal @storage_root, anchor.fetch('storage_root')
    assert_equal task_id, anchor.fetch('key')

    marker_path = File.join(common, 'fable-locks', StableLockAnchor::REPOSITORY_GENERATION_FILENAME)
    assert File.file?(marker_path)
    refute File.exist?(File.join(@storage_root, 'fable-locks', StableLockAnchor::REPOSITORY_GENERATION_FILENAME))
    lineage = JSON.parse(File.binread(lock_path))
    assert_equal @storage_root, lineage.fetch('record_root')
    assert_equal @lock_root, lineage.fetch('lock_root')

    second_storage = File.join(@lock_root, '.task-data', 'task-a-second')
    FileUtils.mkdir_p(second_storage)
    refute_equal lock_path,
                 ExecutionRecord.task_lock_path(second_storage, task_id, lock_root: @lock_root)
    refute_equal lock_path,
                 ExecutionRecord.task_lock_path(@storage_root, 'TASK_A_OTHER', lock_root: @lock_root)
  end

  def test_same_root_explicit_and_legacy_calls_keep_the_existing_lock_identity
    task_id = 'TASK_B_SAME_ROOT'
    legacy_path = ExecutionRecord.task_lock_path(@lock_root, task_id)
    explicit_path = ExecutionRecord.task_lock_path(@lock_root, task_id, lock_root: @lock_root)
    assert_equal legacy_path, explicit_path

    marker = File.join(@tmpdir, 'same-root-child-started')
    stdout, stderr, status = run_cli(protected_args(
      storage_root: @lock_root, lock_root: @lock_root,
      task_id: task_id, execution_id: 'same-root-run', command: success_command(marker)
    ))
    assert status.success?, "stdout=#{stdout.inspect} stderr=#{stderr.inspect}"
    assert File.file?(marker)
    assert File.file?(ExecutionRecord.default_path(@lock_root, task_id, 'same-root-run'))
    same_root_capture = DurableCommandCapture.load(
      DurableCommandCapture.default_path(@lock_root, task_id, 'same-root-run')
    )
    assert_equal @lock_root, same_root_capture.execution_identity.fetch('repository_path')
    refute same_root_capture.execution_identity.key?('storage_root')
    refute same_root_capture.execution_identity.key?('lock_root')

    anchor = JSON.parse(File.read(File.join(File.dirname(explicit_path), 'authority.json')))
    assert_equal @lock_root, anchor.fetch('repository')
    refute anchor.key?('storage_root'), 'same-root identity must retain the legacy digest shape'
    refute anchor.key?('lock_root'), 'same-root identity must not add redundant root fields'
  end

  def test_same_root_nested_capability_clears_an_inherited_lock_root
    task_id = 'TASK_B_INHERITED_LOCK_ROOT'
    capability_env = nil
    ExecutionRecord.with_task_lock(@lock_root, task_id) do |lock|
      capability_env = ExecutionRecord.nested_capability_env(
        @lock_root, task_id, lock, 'same-root-parent', 'test-secret', lock_root: @lock_root
      )
    end

    lock_root_key = ExecutionRecord::NESTED_LOCK_ROOT_ENV
    assert capability_env.key?(lock_root_key), 'same-root child must explicitly clear inherited lock-root state'
    assert_nil capability_env.fetch(lock_root_key)

    inherited = { lock_root_key => @outside_root }
    child_env = inherited.merge(capability_env)
    _, _, status = Open3.capture3(child_env, RbConfig.ruby, '-e',
                                  "exit(ENV.key?(#{lock_root_key.dump}) ? 1 : 0)")
    assert status.success?, 'nil nested environment values must remove inherited lock-root state'
  end

  def test_nested_storage_recovery_uses_the_same_lock_root_and_preserves_history
    task_id = 'TASK_A_DUAL_ROOT_RECOVERY'
    old_id = 'stale-before-recovery'
    ExecutionRecord.with_task_lock(@storage_root, task_id, lock_root: @lock_root) { |_lock| }

    pid = Process.spawn(RbConfig.ruby, '-e', 'exit 0')
    Process.wait(pid)
    old_path = ExecutionRecord.default_path(@storage_root, task_id, old_id)
    ExecutionRecord.start!(old_path, task_id: task_id, execution_id: old_id, pid: pid)
    old_bytes = File.binread(old_path)
    state_path = File.join(@storage_root, 'application-state.bin')
    File.binwrite(state_path, "disposable state\0\n")
    child_marker = File.join(@tmpdir, 'recovery-child-started')
    command = success_command(child_marker)
    stdout, stderr, status = run_cli(protected_args(
      mode: '--recover-run', storage_root: @storage_root, lock_root: @lock_root,
      task_id: task_id, execution_id: old_id, application_state: state_path, command: command
    ))

    assert status.success?, "stdout=#{stdout.inspect} stderr=#{stderr.inspect}"
    assert File.file?(child_marker)
    assert_equal old_bytes, File.binread(old_path), 'recovery must retain the stale source record'

    transition_path = ExecutionRecoveryTransition.default_path(@storage_root, task_id, old_id)
    transition = ExecutionRecoveryTransition.load(transition_path)
    assert_equal old_id, transition.old_execution_id
    successor_id = transition.successor_execution_id
    successor_path = ExecutionRecord.default_path(@storage_root, task_id, successor_id)
    successor = ExecutionRecord.load(successor_path)
    assert_equal ExecutionRecord::STATUS_COMPLETED, successor.status
    assert_equal old_id, successor.recovery_old_execution_id
    assert File.file?(successor.durable_capture_path)
    recovery_identity = DurableCommandCapture.load(successor.durable_capture_path).execution_identity
    assert_equal @storage_root, recovery_identity.fetch('storage_root')
    assert_equal @lock_root, recovery_identity.fetch('lock_root')
    assert transition_path.start_with?(@storage_root)
    assert successor.durable_capture_path.start_with?(@storage_root)

    lock_path = ExecutionRecord.task_lock_path(@storage_root, task_id, lock_root: @lock_root)
    lineage = JSON.parse(File.binread(lock_path))
    assert_equal @storage_root, lineage.fetch('record_root')
    assert_equal @lock_root, lineage.fetch('lock_root')
    assert_equal successor_id, lineage.fetch('execution_id')
    assert File.file?(File.join(common_directory(@lock_root), 'fable-locks',
                               StableLockAnchor::REPOSITORY_GENERATION_FILENAME))
  end

  def test_nested_acquisition_inherits_the_dual_root_identity_and_lineage
    task_id = 'TASK_A_DUAL_ROOT_NESTED'
    parent_id = 'parent-run'
    child_id = 'nested-child-run'
    nested_storage_root = File.join(@lock_root, 'task-a-state')
    FileUtils.mkdir_p(nested_storage_root)
    child_marker = File.join(@tmpdir, 'nested-child-started')
    nested_parent = <<~'RUBY'
      require 'open3'
      require 'rbconfig'
      launcher, storage, lock_root, worktree, task_id, execution_id, marker = ARGV
      fd = Integer(ENV.fetch('FABLE_PROTECTED_RUN_LOCK_FD'))
      argv = [RbConfig.ruby, launcher, '--run', '--repo', storage, '--lock-root', lock_root,
              '--worktree', worktree, '--task-id', task_id, '--execution-id', execution_id,
              '--', RbConfig.ruby, '-e', 'File.write(ARGV.fetch(0), "nested\n")', marker]
      stdout, stderr, status = Open3.capture3(*argv, fd => fd)
      STDOUT.write(stdout)
      STDERR.write(stderr)
      exit(status.exitstatus || 1)
    RUBY
    parent_command = [RbConfig.ruby, '-e', nested_parent, CLI, nested_storage_root, @lock_root,
                      @worktree, task_id, child_id, child_marker]
    stdout, stderr, status = run_cli(protected_args(
      storage_root: nested_storage_root, lock_root: @lock_root, task_id: task_id,
      execution_id: parent_id, command: parent_command
    ))

    assert status.success?, "stdout=#{stdout.inspect} stderr=#{stderr.inspect}"
    assert File.file?(child_marker)
    parent = ExecutionRecord.load(ExecutionRecord.default_path(nested_storage_root, task_id, parent_id))
    child = ExecutionRecord.load(ExecutionRecord.default_path(nested_storage_root, task_id, child_id))
    assert_equal ExecutionRecord::STATUS_COMPLETED, parent.status
    assert_equal ExecutionRecord::STATUS_COMPLETED, child.status
    assert_equal parent_id, child.parent_execution_id
    assert File.file?(parent.durable_capture_path)
    assert File.file?(child.durable_capture_path)

    lock_path = ExecutionRecord.task_lock_path(nested_storage_root, task_id, lock_root: @lock_root)
    lineage = JSON.parse(File.binread(lock_path))
    assert_equal nested_storage_root, lineage.fetch('record_root')
    assert_equal @lock_root, lineage.fetch('lock_root')
    assert_equal parent_id, lineage.fetch('execution_id')
  end

  def test_invalid_roots_fail_closed_without_starting_the_child
    other_root = File.join(@tmpdir, 'other-git-root')
    initialize_synthetic_git_repo(other_root)
    non_root = File.join(@lock_root, '.fable')
    FileUtils.mkdir_p(non_root)
    storage_symlink = File.join(@tmpdir, 'storage-symlink')
    lock_symlink = File.join(@tmpdir, 'lock-symlink')
    File.symlink(@storage_root, storage_symlink)
    File.symlink(@lock_root, lock_symlink)
    ambiguous_storage = File.join(@lock_root, '.task-data', '..', '.task-data', 'task-a')

    cases = [
      ['nested storage without explicit lock root', @storage_root, OMIT_LOCK_ROOT],
      ['storage escapes lock root', @outside_root, @lock_root],
      ['wrong Git worktree root', @storage_root, other_root],
      ['non-top-level Git path', @storage_root, non_root],
      ['non-Git lock root', @storage_root, @outside_root],
      ['storage root symlink', storage_symlink, @lock_root],
      ['lock root symlink', @storage_root, lock_symlink],
      ['ambiguous storage path', ambiguous_storage, @lock_root]
    ]

    cases.each_with_index do |(label, storage, lock_root), index|
      marker = File.join(@tmpdir, "invalid-root-child-#{index}")
      args = protected_args(storage_root: storage, lock_root: lock_root,
                            task_id: "INVALID_ROOT_#{index}", execution_id: 'must-not-start',
                            command: success_command(marker))
      stdout, stderr, status = run_cli(args)
      refute status.success?, "#{label}: unexpectedly succeeded"
      refute File.exist?(marker), "#{label}: upstream child ran"
      refute File.exist?(ExecutionRecord.default_path(storage, "INVALID_ROOT_#{index}", 'must-not-start')),
             "#{label}: execution history was written"
      refute_empty(stdout + stderr, "#{label}: expected a diagnostic")
    end
  end

  def test_legacy_execution_lock_remains_a_migration_required_stop
    task_id = 'TASK_A_LEGACY_LOCK'
    legacy_lock = ExecutionRecord.legacy_task_lock_path(@storage_root, task_id)
    FileUtils.mkdir_p(File.dirname(legacy_lock))
    legacy_bytes = "legacy lock fixture\0\n"
    File.binwrite(legacy_lock, legacy_bytes)
    marker = File.join(@tmpdir, 'legacy-lock-child-started')
    stdout, stderr, status = run_cli(protected_args(
      storage_root: @storage_root, lock_root: @lock_root, task_id: task_id,
      execution_id: 'must-not-start', command: success_command(marker)
    ))

    refute status.success?
    assert_match(/migration gate required/i, stdout + stderr)
    assert_equal legacy_bytes, File.binread(legacy_lock)
    refute File.exist?(marker)
    refute File.exist?(ExecutionRecord.default_path(@storage_root, task_id, 'must-not-start'))
    lock_path = ExecutionRecord.task_lock_path(@storage_root, task_id, lock_root: @lock_root)
    refute File.exist?(File.dirname(lock_path)), 'legacy state must not cause a replacement lock anchor'
  end

  def test_unanchored_legacy_task_history_is_not_skipped_or_migrated
    task_id = 'TASK_A_LEGACY_HISTORY'
    old_path = ExecutionRecord.default_path(@storage_root, task_id, 'old-history')
    FileUtils.mkdir_p(File.dirname(old_path))
    old_bytes = "legacy execution history fixture\0\n"
    File.binwrite(old_path, old_bytes)
    child_marker = File.join(@tmpdir, 'legacy-history-child-started')
    stdout, stderr, status = run_cli(protected_args(
      storage_root: @storage_root, lock_root: @lock_root, task_id: task_id,
      execution_id: 'must-not-start', command: success_command(child_marker)
    ))

    refute status.success?
    assert_match(/migration gate required/i, stdout + stderr)
    assert_equal old_bytes, File.binread(old_path)
    refute File.exist?(child_marker)
    refute File.exist?(ExecutionRecord.default_path(@storage_root, task_id, 'must-not-start'))
    lock_path = ExecutionRecord.task_lock_path(@storage_root, task_id, lock_root: @lock_root)
    refute File.exist?(File.dirname(lock_path)), 'legacy task history must not cause a new lock anchor'
  end

  private

  def protected_args(storage_root:, lock_root: OMIT_LOCK_ROOT, task_id:, execution_id:, command:,
                     mode: '--run', application_state: nil)
    args = [mode, '--repo', storage_root]
    args.concat(['--lock-root', lock_root]) unless lock_root.equal?(OMIT_LOCK_ROOT)
    args.concat(['--worktree', @worktree, '--task-id', task_id, '--execution-id', execution_id])
    args.concat(['--application-state', application_state]) if application_state
    args.concat(['--', *command])
    args
  end

  def success_command(marker)
    [RbConfig.ruby, '-e', 'File.write(ARGV.fetch(0), "child started\n"); puts "child done"', marker]
  end

  def run_cli(args)
    Open3.capture3(RbConfig.ruby, CLI, *args)
  end

  def common_directory(path)
    output, status = Open3.capture2('git', '-C', path, 'rev-parse', '--git-common-dir')
    raise 'fixture Git common directory could not be read' unless status.success?

    File.realpath(File.expand_path(output.strip, path))
  end
end
