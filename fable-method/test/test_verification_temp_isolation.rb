# frozen_string_literal: true

require 'minitest/autorun'
require 'tmpdir'
require_relative '../scripts/verification_temp_isolation'
require_relative '../scripts/judge_depth_reconciliation'

# Falsifiable coverage for FABLE_VERIFICATION_TEMP_ISOLATION_R1
# scenarios A-G. Tests invoke the shipped verification temp isolation guard
# and bind it to the live canonical judge-handoff.md contract text.
class VerificationTempIsolationTest < Minitest::Test
  Isolation = Fable::VerificationTempIsolation
  Reconciler = Fable::JudgeDepthReconciliation

  def setup
    @worktree = Dir.mktmpdir('fable_test_worktree')
    @outside_temp = Dir.mktmpdir('fable_test_outside_temp')
    @inside_temp = File.join(@worktree, 'scratch')
    FileUtils.mkdir_p(@inside_temp)
    @source_head = '9e94ed00ca8ed05367fbe7b429b719d0366d9935'
    @source_tree = 'b23a31a5c1f0fe82efce82ad67216bdb138f8547'
  end

  def teardown
    FileUtils.rm_rf(@worktree)
    FileUtils.rm_rf(@outside_temp)
  end

  def flattened_contract
    Isolation.contract_text.gsub(/\s+/, ' ')
  end

  def test_reads_live_canonical_judge_handoff
    assert_equal File.expand_path('../shared/references/judge-handoff.md', __dir__), Isolation.live_path
    assert File.file?(Isolation.live_path)
  end

  # A. Ordinary focused test with harmless in-tree temp: no new block.
  def test_scenario_a_ordinary_focused_test_with_in_tree_temp_not_blocked
    result = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @inside_temp,
      expensive: false,
      judge_authoritative: false,
      source_head: @source_head,
      source_tree: @source_tree
    )

    assert_equal 'PASS', result.fetch(:preflight_status)
    refute result.fetch(:blocked)
    assert result.fetch(:ordinary_test)
    assert result.fetch(:expensive_run_permitted)
    assert_equal 'ORDINARY_TEST_NONBLOCK', result.fetch(:reason)

    executed = false
    run_result = Isolation.execute_guarded(
      worktree_root: @worktree,
      temp_root: @inside_temp,
      expensive: false,
      judge_authoritative: false,
      source_head: @source_head,
      source_tree: @source_tree
    ) do
      executed = true
      'focused_test_output'
    end

    assert executed, 'ordinary focused test block must execute'
    assert run_result.fetch(:executed)
    refute run_result.fetch(:consumed), 'ordinary test does not consume an expensive run'
    assert_equal 'focused_test_output', run_result.fetch(:output)
  end

  # B. Judge-authoritative one-shot full verification with temp outside source tree: PASS.
  def test_scenario_b_judge_authoritative_full_verification_outside_temp_passes
    result = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @outside_temp,
      expensive: true,
      judge_authoritative: true,
      source_head: @source_head,
      source_tree: @source_tree
    )

    assert_equal 'PASS', result.fetch(:preflight_status)
    refute result.fetch(:blocked)
    refute result.fetch(:ordinary_test)
    assert result.fetch(:expensive_run_permitted)
    assert_equal Isolation::RESOURCE_CLASS, result.fetch(:resource_class)
    assert_equal @source_head, result.fetch(:frozen_head)
    assert_equal @source_tree, result.fetch(:frozen_tree)

    executed = false
    run_result = Isolation.execute_guarded(
      worktree_root: @worktree,
      temp_root: @outside_temp,
      expensive: true,
      judge_authoritative: true,
      source_head: @source_head,
      source_tree: @source_tree
    ) do
      executed = true
      'full_suite_verified'
    end

    assert executed, 'expensive verification block must execute when temp is outside'
    assert run_result.fetch(:executed)
    assert run_result.fetch(:consumed), 'expensive verification run is consumed on successful execution'
    assert_equal 'full_suite_verified', run_result.fetch(:output)
  end

  # C. Same verification with temp inside source tree where it affects judged tree:
  # fail/preflight before suite execution.
  def test_scenario_c_expensive_verification_inside_temp_fails_preflight_before_execution
    result = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @inside_temp,
      expensive: true,
      judge_authoritative: true,
      source_head: @source_head,
      source_tree: @source_tree,
      affects_judged_tree: true
    )

    assert_equal 'FAIL', result.fetch(:preflight_status)
    assert result.fetch(:blocked)
    refute result.fetch(:expensive_run_permitted)
    assert_equal 'IN_TREE_TEMP_CONTAMINATION', result.fetch(:failure_reason)

    executed = false
    error = assert_raises(Isolation::PreflightError) do
      Isolation.execute_guarded(
        worktree_root: @worktree,
        temp_root: @inside_temp,
        expensive: true,
        judge_authoritative: true,
        source_head: @source_head,
        source_tree: @source_tree,
        affects_judged_tree: true
      ) do
        executed = true
      end
    end

    refute executed, 'expensive suite must NOT execute when preflight fails'
    assert_match(/IN_TREE_TEMP_CONTAMINATION/, error.message)
  end

  # D. Temp root classification: TEMPORARY_DELETE.
  def test_scenario_d_temp_root_classification_is_temporary_delete
    outside = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @outside_temp,
      expensive: true,
      source_head: @source_head,
      source_tree: @source_tree
    )
    assert_equal 'TEMPORARY_DELETE', outside.fetch(:resource_class)

    inside = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @inside_temp,
      expensive: true,
      source_head: @source_head,
      source_tree: @source_tree
    )
    assert_equal 'TEMPORARY_DELETE', inside.fetch(:resource_class)
  end

  # E. Source HEAD/tree frozen before verification.
  def test_scenario_e_source_head_and_tree_frozen_before_verification
    result = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @outside_temp,
      expensive: true,
      source_head: @source_head,
      source_tree: @source_tree
    )
    assert_equal @source_head, result.fetch(:frozen_head)
    assert_equal @source_tree, result.fetch(:frozen_tree)

    # When source HEAD or tree cannot be determined, preflight must fail
    missing_head = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @outside_temp,
      expensive: true,
      source_head: nil,
      source_tree: nil
    )
    assert_equal 'FAIL', missing_head.fetch(:preflight_status)
    assert_equal 'CANNOT_FREEZE_SOURCE_IDENTITY', missing_head.fetch(:failure_reason)
    assert missing_head.fetch(:blocked)
  end

  # F. Preflight failure does not consume/mark the expensive verification as executed.
  def test_scenario_f_preflight_failure_does_not_consume_expensive_run
    # Non-block invocation
    unconsumed = Isolation.execute_guarded(
      worktree_root: @worktree,
      temp_root: @inside_temp,
      expensive: true,
      judge_authoritative: true,
      source_head: @source_head,
      source_tree: @source_tree,
      affects_judged_tree: true
    )
    refute unconsumed.fetch(:executed)
    refute unconsumed.fetch(:consumed)
    assert unconsumed.fetch(:blocked)
    refute unconsumed.fetch(:expensive_run_permitted)

    # Block invocation with spy counter
    invocation_count = 0
    assert_raises(Isolation::PreflightError) do
      Isolation.execute_guarded(
        worktree_root: @worktree,
        temp_root: @inside_temp,
        expensive: true,
        judge_authoritative: true,
        source_head: @source_head,
        source_tree: @source_tree,
        affects_judged_tree: true
      ) do
        invocation_count += 1
      end
    end
    assert_equal 0, invocation_count, 'verification execution was consumed despite preflight failure'
  end

  # G. Existing Judge-depth reconciliation remains unchanged.
  def test_scenario_g_existing_judge_depth_reconciliation_remains_unchanged
    bounded_match = Reconciler.reconcile(packet_depth: 'BOUNDED', canonical_required_depth: 'BOUNDED')
    assert_equal 'MATCH', bounded_match.fetch(:judge_depth_reconciliation)
    assert_equal 'NONE', bounded_match.fetch(:missing_judge_evidence)

    escalation = Reconciler.reconcile(packet_depth: 'BOUNDED', canonical_required_depth: 'FULL')
    assert_equal 'ESCALATION_REQUIRED', escalation.fetch(:judge_depth_reconciliation)
    assert_equal 'FULL_SUITE', escalation.fetch(:missing_judge_evidence)

    full_match = Reconciler.reconcile(packet_depth: 'FULL', canonical_required_depth: 'FULL')
    assert_equal 'MATCH', full_match.fetch(:judge_depth_reconciliation)
    assert_equal 'NONE', full_match.fetch(:missing_judge_evidence)
  end

  # --- Falsifiability and contract binding tests ---------------------------

  def test_falsifiability_in_tree_temp_without_isolation_fails_and_outside_passes
    inside = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @inside_temp,
      expensive: true,
      source_head: @source_head,
      source_tree: @source_tree,
      affects_judged_tree: true
    )
    assert inside.fetch(:blocked), 'in-tree temp must be blocked'

    outside = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @outside_temp,
      expensive: true,
      source_head: @source_head,
      source_tree: @source_tree,
      affects_judged_tree: true
    )
    refute outside.fetch(:blocked), 'outside temp must not be blocked'
  end

  def test_falsifiability_in_tree_temp_that_does_not_affect_tree_is_not_blocked
    result = Isolation.preflight(
      worktree_root: @worktree,
      temp_root: @inside_temp,
      expensive: true,
      source_head: @source_head,
      source_tree: @source_tree,
      affects_judged_tree: false
    )
    assert_equal 'PASS', result.fetch(:preflight_status)
    refute result.fetch(:blocked)
  end

  def test_fails_closed_when_freeze_head_tree_phrase_removed
    stripped = flattened_contract.sub('freeze the judged source HEAD/tree', '')
    error = assert_raises(Isolation::MissingContract) do
      Isolation.preflight(
        worktree_root: @worktree,
        temp_root: @outside_temp,
        expensive: true,
        source_head: @source_head,
        source_tree: @source_tree,
        contract: stripped
      )
    end
    assert_match(/freeze the judged source HEAD\/tree/, error.message)
  end

  def test_fails_closed_when_require_outside_phrase_removed
    stripped = flattened_contract.sub('require temp root outside the judged source worktree', '')
    error = assert_raises(Isolation::MissingContract) do
      Isolation.preflight(
        worktree_root: @worktree,
        temp_root: @outside_temp,
        expensive: true,
        source_head: @source_head,
        source_tree: @source_tree,
        contract: stripped
      )
    end
    assert_match(/require temp root outside the judged source worktree/, error.message)
  end

  def test_fails_closed_when_temporary_delete_phrase_removed
    stripped = flattened_contract.sub('TEMPORARY_DELETE', '')
    error = assert_raises(Isolation::MissingContract) do
      Isolation.preflight(
        worktree_root: @worktree,
        temp_root: @outside_temp,
        expensive: true,
        source_head: @source_head,
        source_tree: @source_tree,
        contract: stripped
      )
    end
    assert_match(/TEMPORARY_DELETE/, error.message)
  end

  def test_fails_closed_when_only_then_consume_phrase_removed
    stripped = flattened_contract.sub('only then consume the expensive verification run', '')
    error = assert_raises(Isolation::MissingContract) do
      Isolation.preflight(
        worktree_root: @worktree,
        temp_root: @outside_temp,
        expensive: true,
        source_head: @source_head,
        source_tree: @source_tree,
        contract: stripped
      )
    end
    assert_match(/only then consume the expensive verification run/, error.message)
  end

  def test_fails_closed_when_ordinary_test_nonblock_phrase_removed
    stripped = flattened_contract.sub('Do not require external temp roots for every ordinary focused test', '')
    error = assert_raises(Isolation::MissingContract) do
      Isolation.preflight(
        worktree_root: @worktree,
        temp_root: @outside_temp,
        expensive: true,
        source_head: @source_head,
        source_tree: @source_tree,
        contract: stripped
      )
    end
    assert_match(/Do not require external temp roots for every ordinary focused test/, error.message)
  end
end
