# frozen_string_literal: true

require 'minitest/autorun'
require 'fileutils'
require 'tmpdir'
require 'json'
require 'open3'
require 'rbconfig'
require 'timeout'
require_relative '../scripts/task_checkpoint'

class TaskCheckpointTest < Minitest::Test
  def setup
    @tmpdir = Dir.mktmpdir('task_checkpoint_test_')
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
    assert_match(/executing repository root is not a Git repository/, stderr)
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
        if self.path == "#{path}.lock" && operation == File::LOCK_EX
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
    File.open("#{path}.lock", File::RDWR) do |lock|
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
    @tmpdir = Dir.mktmpdir('durable_capture_test_')
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
  CLI = File.expand_path('../scripts/task_checkpoint.rb', __dir__)
  TASK_ID = 'CLI_TASK'
  UPSTREAM = <<~'RUBY'
    launches, release, result = ARGV
    File.open(launches, 'a') { |file| file.puts "#{Process.pid}:#{Process.ppid}" }
    STDOUT.write("out\0尾\n")
    STDERR.write("err\0尾\n")
    STDOUT.flush
    STDERR.flush
    unless release == '-'
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 8
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
<<<<<<< HEAD
  NESTED_RECOVERY_UPSTREAM = <<~'RUBY'
    state, observation_path, launches, release, ruby, cli, repo, worktree, task_id, child_task_id, child_id = ARGV
    keys = %w[FABLE_RECOVERY_TASK_ID FABLE_RECOVERY_OLD_EXECUTION_ID
              FABLE_RECOVERY_SUCCESSOR_ID FABLE_RECOVERY_CAPABILITY]
    inherited = keys.to_h { |key| [key, ENV[key]] }
    observation = { 'state_sha256' => Digest::SHA256.hexdigest(File.binread(state)),
                    'inherited' => inherited }
    File.write(observation_path, JSON.generate(observation))
    File.open(launches, 'a') { |file| file.puts Process.pid }
    unless release == '-'
      deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + 8
      until File.file?(release)
        abort 'nested recovery barrier timed out' if Process.clock_gettime(Process::CLOCK_MONOTONIC) >= deadline
        sleep 0.01
      end
    end
    command = [ruby, cli, '--run', '--repo', repo, '--worktree', worktree,
               '--task-id', child_task_id, '--execution-id', child_id, '--',
               ruby, '-e', 'abort "capability leaked" if ENV.key?("FABLE_RECOVERY_CAPABILITY"); STDOUT.write("nested child\\n")']
    out, err, status = Open3.capture3(*command)
    observation['child_exit'] = status.exitstatus
    observation['child_stderr'] = err
    File.write(observation_path, JSON.generate(observation))
    STDOUT.write(out)
    STDERR.write(err)
    exit(status.exitstatus || 1)
=======
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
>>>>>>> 0fd7c60e46f750f07575fae3a75e271a81b94c3f
  RUBY

  def setup
    @tmpdir = Dir.mktmpdir('task_checkpoint_run_test_')
    @repo = File.join(@tmpdir, 'stable-record-root')
    @worktree = File.join(@tmpdir, 'upstream-cwd')
    @caller = File.join(@tmpdir, 'caller-cwd')
    [@repo, @worktree, @caller].each { |path| FileUtils.mkdir_p(path) }
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

<<<<<<< HEAD
  def nested_recovery_upstream(child_task: TASK_ID, barrier: false)
    [RbConfig.ruby, '-rjson', '-rdigest', '-ropen3', '-e', NESTED_RECOVERY_UPSTREAM,
     @application_state, @recovery_observation, @launches, barrier ? @release : '-',
     RbConfig.ruby, CLI, @repo, @worktree, TASK_ID, child_task, 'nested_child']
  end

=======
>>>>>>> 0fd7c60e46f750f07575fae3a75e271a81b94c3f
  def recovery_cli_args(identity, command = nil)
    command ||= recovery_upstream(identity)
    ['--recover-run', '--repo', @repo, '--worktree', @worktree,
     '--task-id', TASK_ID, '--execution-id', identity,
     '--application-state', @application_state, '--', *command]
  end

  def cli_args(identity, command = upstream)
    ['--run', '--repo', @repo, '--worktree', @worktree,
     '--task-id', TASK_ID, '--execution-id', identity, '--', *command]
  end

<<<<<<< HEAD
  def start_cli(args, cwd: @caller, pgroup: true, env: nil)
    command = []
    command << env if env
    stdin, stdout, stderr, wait = Open3.popen3(*command, RbConfig.ruby, CLI, *args,
                                                chdir: cwd, pgroup: pgroup)
=======
  def start_cli(args, cwd: @caller, pgroup: true, env: nil, inherit_fds: [])
    spawn_env = env ? [env] : []
    spawn_opts = { chdir: cwd, pgroup: pgroup }
    inherit_fds.each { |fd| spawn_opts[fd] = fd }
    stdin, stdout, stderr, wait = Open3.popen3(*spawn_env, RbConfig.ruby, CLI, *args, spawn_opts)
>>>>>>> 0fd7c60e46f750f07575fae3a75e271a81b94c3f
    stdin.close
    readers = [stdout, stderr].map { |io| Thread.new { begin; io.read; ensure; io.close; end } }
    child = { wait: wait, readers: readers }
    @children << child
    child
  end

  def finish_cli(child)
    assert child[:wait].join(5), 'CLI timed out'
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
    assert_equal [ExecutionRecord.task_lock_path(@repo, TASK_ID), record_path(identity)].sort,
                 Dir.glob(File.join(@repo, '**', '*'), File::FNM_DOTMATCH).select { |path| File.file?(path) }.sort
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

<<<<<<< HEAD
  def test_recovery_successor_launches_same_lineage_nested_protected_child
    old_id = 'nested_stale_fixture'
    seed_stale_execution(old_id)
    old_bytes = File.binread(record_path(old_id))
    state_bytes = File.binread(@application_state)

    result = run_cli(recovery_cli_args(old_id, nested_recovery_upstream))

    assert_equal 0, result[2].exitstatus, result[1]
    assert_equal "nested child\n", result[0]
    assert_equal state_bytes, File.binread(@application_state)
    assert_equal old_bytes, File.binread(record_path(old_id))
    observation = JSON.parse(File.read(@recovery_observation))
    assert_equal Digest::SHA256.hexdigest(state_bytes), observation.fetch('state_sha256')
    assert_equal 0, observation.fetch('child_exit')
    successor_id = successor_execution_id(old_id)
    transition = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))
    assert_equal successor_id, transition.successor_execution_id
    successor = ExecutionRecord.load(record_path(successor_id))
    assert_equal ExecutionRecord::STATUS_COMPLETED, successor.status
    assert_equal old_id, successor.recovery_old_execution_id
    assert_equal ExecutionRecord.nested_capability_digest(TASK_ID, successor_id,
                                                          observation.fetch('inherited').fetch('FABLE_RECOVERY_CAPABILITY')),
                 successor.nested_capability_sha256
    refute_includes File.binread(record_path(successor_id)),
                    observation.fetch('inherited').fetch('FABLE_RECOVERY_CAPABILITY')
    refute_includes File.binread(recovery_transition_path(old_id)),
                    observation.fetch('inherited').fetch('FABLE_RECOVERY_CAPABILITY')
    child = ExecutionRecord.load(record_path('nested_child'))
    assert_equal ExecutionRecord::STATUS_COMPLETED, child.status
    assert_equal successor_id, child.parent_execution_id
    assert_equal old_id, child.recovery_old_execution_id
    assert_equal "nested child\n", DurableCommandCapture.load(child.durable_capture_path).stdout
  end

  def test_independent_writer_is_rejected_during_nested_recovery
    old_id = 'nested_competitor_fixture'
    seed_stale_execution(old_id)
    owner = start_cli(recovery_cli_args(old_id, nested_recovery_upstream(barrier: true)))
    wait_until('recovered driver did not start') { File.file?(@recovery_observation) }

    competitor = run_cli(cli_args('independent_writer'))
    assert_equal 1, competitor[2].exitstatus
    assert_includes competitor[1], ExecutionRecord::CLASSIFICATION_ACTIVE
    refute File.exist?(record_path('independent_writer'))

    File.write(@release, 'go')
    assert_equal 0, finish_cli(owner)[2].exitstatus
  end

  def test_nested_capability_cannot_cross_task_identity
    old_id = 'nested_wrong_task_fixture'
    seed_stale_execution(old_id)
    other_task = 'OTHER_TASK'

    result = run_cli(recovery_cli_args(old_id, nested_recovery_upstream(child_task: other_task)))

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], 'nested recovery task or execution identity does not match'
    refute File.exist?(ExecutionRecord.default_path(@repo, other_task, 'nested_child'))
  end

  def test_copied_environment_cannot_spoof_recovery_ancestry
    old_id = 'nested_spoof_fixture'
    seed_stale_execution(old_id)
    owner = start_cli(recovery_cli_args(old_id, nested_recovery_upstream(barrier: true)))
    wait_until('recovered driver did not expose test capability') { File.file?(@recovery_observation) }
    copied_env = JSON.parse(File.read(@recovery_observation)).fetch('inherited')

    spoof = run_cli(cli_args('spoofed_writer'), env: copied_env)
    assert_equal 1, spoof[2].exitstatus
    assert_includes spoof[1], 'nested recovery owner capability or ancestry is invalid'
    refute File.exist?(record_path('spoofed_writer'))

    File.write(@release, 'go')
    assert_equal 0, finish_cli(owner)[2].exitstatus
  end

  def test_partial_recovery_environment_fails_closed
    result = run_cli(cli_args('partial_capability'),
                     env: { 'FABLE_RECOVERY_TASK_ID' => TASK_ID })

    assert_equal 1, result[2].exitstatus
    assert_includes result[1], 'nested recovery capability is incomplete or malformed'
    refute File.exist?(record_path('partial_capability'))
  end

  def test_nested_child_lease_blocks_new_top_level_writer
    nested_lock = ExecutionRecord.acquire_nested_lock!(@repo, TASK_ID)
    begin
      result = run_cli(cli_args('writer_during_orphaned_child'))
      assert_equal 1, result[2].exitstatus
      assert_includes result[1], ExecutionRecord::CLASSIFICATION_ACTIVE
      refute File.exist?(record_path('writer_during_orphaned_child'))
    ensure
      nested_lock.close
    end
  end

=======
>>>>>>> 0fd7c60e46f750f07575fae3a75e271a81b94c3f
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

<<<<<<< HEAD
=======
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

    replay = run_cli(cli_args(successor_id))

    assert_equal 0, recovered[2].exitstatus
    assert_equal recovered[0], replay[0]
    assert_equal recovered[1], replay[1]
    assert_equal 0, replay[2].exitstatus
    assert_equal 1, launch_records.size, 'normal replay must reuse the completed explicit recovery successor'
  end

>>>>>>> 0fd7c60e46f750f07575fae3a75e271a81b94c3f
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

<<<<<<< HEAD
  def test_dead_started_successor_resumes_only_under_same_identity_and_checkpoint
    old_id = 'dead_successor_fixture'
    seed_stale_execution(old_id)
=======
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
>>>>>>> 0fd7c60e46f750f07575fae3a75e271a81b94c3f
    transition = ExecutionRecoveryTransition.reserve!(repo_root: @repo, task_id: TASK_ID,
                                                       old_execution_id: old_id,
                                                       application_state_path: @application_state,
                                                       worktree_path: @worktree,
<<<<<<< HEAD
                                                       command: recovery_upstream(old_id))
=======
                                                       command: command)
>>>>>>> 0fd7c60e46f750f07575fae3a75e271a81b94c3f
    stale_successor = ExecutionRecord.acquire!(record_path(transition.successor_execution_id),
                                               task_id: TASK_ID,
                                               execution_id: transition.successor_execution_id,
                                               pid: dead_pid)
    assert_nil stale_successor.classification
    stale_successor_bytes = File.binread(record_path(transition.successor_execution_id))

<<<<<<< HEAD
    result = run_cli(recovery_cli_args(old_id))

    assert_equal 0, result[2].exitstatus, result[1]
    reread = ExecutionRecoveryTransition.load(recovery_transition_path(old_id))
    assert_equal transition.successor_execution_id, reread.successor_execution_id
    assert_equal [stale_successor_bytes], reread.successor_attempt_history.map { |entry| entry.fetch('record_json') }
    assert_equal 1, launch_records.size
=======
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
>>>>>>> 0fd7c60e46f750f07575fae3a75e271a81b94c3f
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
                                        started_at: Time.now.utc.iso8601, ended_at: Time.now.utc.iso8601)
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
    [@repo, @worktree, @caller].each { |path| assert_empty Dir.children(path) }
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
    assert_equal({ 'schema_version' => ExecutionRecord::SCHEMA_VERSION, 'task_id' => TASK_ID,
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
    assert_equal 0, File.size(lock_path), 'a conforming acquisition clears the dead lineage'
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
    @tmpdir = Dir.mktmpdir('execution_recovery_test_')
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
    DurableCommandCapture.run_and_capture(bump, file_path: capture_file)
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
