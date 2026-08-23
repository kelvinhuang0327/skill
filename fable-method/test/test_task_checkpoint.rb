# frozen_string_literal: true

require 'minitest/autorun'
require 'fileutils'
require 'tmpdir'
require 'json'
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
end
