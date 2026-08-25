# frozen_string_literal: true

require 'minitest/autorun'
require 'fileutils'
require 'tmpdir'
require 'json'
require 'open3'
require 'rbconfig'
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
    # Checkpoint says next_action = MERGE_PR, with boundary requiring standalone auth
    cp = TaskCheckpoint.new(@valid_attrs.merge(
      next_action: 'MERGE_PR',
      authorization_boundary: 'CURRENT_WORKER_CONVERSATION_STANDALONE_AUTH_REQUIRED'
    ))

    # New Worker conversation has NO direct standalone authorization
    reconciler_no_auth = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree],
      conversation_authorizations: []
    })

    result_no_auth = reconciler_no_auth.reconcile
    assert_equal 'AUTHORIZATION_REQUIRED', result_no_auth.verdict
    assert_match(/requires standalone Owner authorization in the current conversation/i, result_no_auth.reason)
    assert_match(/quoted tokens in checkpoint do not transfer/i, result_no_auth.reason)

    # When direct authorization is provided in the current conversation:
    reconciler_with_auth = TaskReconciler.new(cp, {
      repository: @repo_dir,
      worktree: @worktree_dir,
      head: @valid_attrs[:current_head],
      tree: @valid_attrs[:current_tree],
      conversation_authorizations: ['OWNER_DIRECT_MESSAGE_MERGE_PR_AUTHORIZED']
    })

    result_with_auth = reconciler_with_auth.reconcile
    assert_equal 'CONTINUE', result_with_auth.verdict
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
    assert_match(/queue-specific fields require queue_disposition/, error.message)

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
          task_b_packet_authorized: true
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
        task_b_independent: false, task_b_packet_authorized: true
      )
    end

    assert_raises(TaskCheckpoint::DeferredQueueEligibilityError) do
      cp.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true, task_b_packet_authorized: false
      )
    end

    assert_raises(TaskCheckpoint::DeferredQueueStateError) do
      cp.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: cp.task_id, next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true, task_b_packet_authorized: true
      )
    end

    completed = TaskCheckpoint.new(@valid_attrs.merge(task_lifecycle_state: 'COMPLETED'))
    assert_raises(TaskCheckpoint::DeferredQueueStateError) do
      completed.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: @task_b_packet_ref,
        task_b_independent: true, task_b_packet_authorized: true
      )
    end
  end

  def test_defer_rejects_ephemeral_or_unresolvable_task_b_packet
    ephemeral = TaskCheckpoint.new(@valid_attrs)
    assert_raises(TaskCheckpoint::ResolutionError) do
      ephemeral.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: 'conversation://task-b',
        task_b_independent: true, task_b_packet_authorized: true
      )
    end

    missing = TaskCheckpoint.new(@valid_attrs)
    assert_raises(TaskCheckpoint::ResolutionError) do
      missing.defer_for_authorized_task!(
        blocker: 'transient', blocker_disposition: 'TRANSIENT_ELIGIBLE',
        resume_after_task_id: 'TASK_B_002', next_authorized_task_packet_ref: 'prompt/missing-task-b.md',
        task_b_independent: true, task_b_packet_authorized: true
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
    TaskCheckpoint::OPTIONAL_QUEUE_FIELDS.each do |field|
      assert_nil task_a.send(field)
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
      task_b_independent: true, task_b_packet_authorized: true
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
end
