# frozen_string_literal: true

require 'minitest/autorun'
require_relative '../scripts/judge_depth_reconciliation'

# Falsifiable coverage for FABLE_JUDGE_DEPTH_ESCALATION_AND_EVIDENCE_COUPLING_R1
# scenarios A-G. Tests invoke the shipped reconciliation function and bind it
# to the live canonical judge-handoff.md contract text.
class JudgeDepthReconciliationTest < Minitest::Test
  Reconciler = Fable::JudgeDepthReconciliation

  def test_reads_live_canonical_judge_handoff
    assert_equal File.expand_path('../shared/references/judge-handoff.md', __dir__), Reconciler.live_path
    assert File.file?(Reconciler.live_path)
  end

  # A. Packet BOUNDED, Canonical BOUNDED -> MATCH
  def test_scenario_a_packet_bounded_canonical_bounded_matches
    result = Reconciler.reconcile(packet_depth: 'BOUNDED', canonical_required_depth: 'BOUNDED')
    assert_equal 'MATCH', result.fetch(:judge_depth_reconciliation)
    assert_equal 'NONE', result.fetch(:missing_judge_evidence)
    refute result.fetch(:implementation_mutation_required)
  end

  # B. Packet BOUNDED, Canonical FULL, full-suite evidence absent ->
  # ESCALATION_REQUIRED, missing FULL_SUITE, mutation NO
  def test_scenario_b_bounded_to_full_escalation_names_full_suite_and_no_mutation
    result = Reconciler.reconcile(
      packet_depth: 'BOUNDED',
      canonical_required_depth: 'FULL',
      evidence_satisfied: false
    )
    assert_equal 'ESCALATION_REQUIRED', result.fetch(:judge_depth_reconciliation)
    assert_equal 'FULL_SUITE', result.fetch(:missing_judge_evidence)
    refute result.fetch(:implementation_mutation_required)
  end

  # C. Same exact implementation tree after a Continuation Delta supplies the
  # full-suite evidence -> no second escalation blocker; proceed to FULL Judge.
  def test_scenario_c_continuation_delta_evidence_clears_the_blocker
    before = Reconciler.reconcile(
      packet_depth: 'BOUNDED',
      canonical_required_depth: 'FULL',
      evidence_satisfied: false
    )
    assert_equal 'FULL_SUITE', before.fetch(:missing_judge_evidence)

    after = Reconciler.reconcile(
      packet_depth: 'BOUNDED',
      canonical_required_depth: 'FULL',
      evidence_satisfied: true
    )
    assert_equal 'NONE', after.fetch(:missing_judge_evidence)
    refute after.fetch(:implementation_mutation_required)
  end

  # D. Packet FULL, Canonical FULL -> MATCH
  def test_scenario_d_packet_full_canonical_full_matches
    result = Reconciler.reconcile(packet_depth: 'FULL', canonical_required_depth: 'FULL')
    assert_equal 'MATCH', result.fetch(:judge_depth_reconciliation)
    assert_equal 'NONE', result.fetch(:missing_judge_evidence)
  end

  # E. Exact-tree full-suite evidence exists before reconciliation ever runs ->
  # no redundant full-suite request on the very first call.
  def test_scenario_e_preexisting_evidence_needs_no_redundant_request
    result = Reconciler.reconcile(
      packet_depth: 'BOUNDED',
      canonical_required_depth: 'FULL',
      evidence_satisfied: true,
      tree_changed_since_evidence: false
    )
    assert_equal 'NONE', result.fetch(:missing_judge_evidence)
  end

  # F. Implementation tree changed after the earlier evidence -> stale
  # evidence must NOT satisfy the new tree.
  def test_scenario_f_tree_change_invalidates_prior_evidence
    result = Reconciler.reconcile(
      packet_depth: 'BOUNDED',
      canonical_required_depth: 'FULL',
      evidence_satisfied: true,
      tree_changed_since_evidence: true
    )
    assert_equal 'ESCALATION_REQUIRED', result.fetch(:judge_depth_reconciliation)
    assert_equal 'FULL_SUITE', result.fetch(:missing_judge_evidence)
  end

  # G. Escalation must reuse the original worktree/branch: the canonical
  # contract states this explicitly, and the fail-closed binding below proves
  # the reconciler refuses to run if that sentence is ever removed.
  def test_scenario_g_contract_requires_reusing_the_original_worktree_and_branch
    assert_includes flattened_contract,
                     'keep the same branch, the same worktree, the same implementation tree'
  end

  def test_not_applicable_packet_depth_always_matches
    %w[NOT_APPLICABLE BOUNDED FULL].each do |canonical|
      result = Reconciler.reconcile(packet_depth: 'NOT_APPLICABLE', canonical_required_depth: canonical)
      assert_equal 'MATCH', result.fetch(:judge_depth_reconciliation), canonical
      assert_equal 'NONE', result.fetch(:missing_judge_evidence), canonical
    end
  end

  def test_not_applicable_canonical_required_never_escalates
    %w[NOT_APPLICABLE BOUNDED FULL DELTA].each do |packet|
      result = Reconciler.reconcile(packet_depth: packet, canonical_required_depth: 'NOT_APPLICABLE')
      assert_equal 'MATCH', result.fetch(:judge_depth_reconciliation), packet
    end
  end

  def test_delta_never_participates_in_ordering_on_either_side
    refute Reconciler.escalation_required?('DELTA', 'FULL')
    refute Reconciler.escalation_required?('BOUNDED', 'DELTA')
    refute Reconciler.escalation_required?('DELTA', 'DELTA')
  end

  def test_over_specified_packet_depth_is_a_match_not_an_error
    result = Reconciler.reconcile(packet_depth: 'FULL', canonical_required_depth: 'BOUNDED')
    assert_equal 'MATCH', result.fetch(:judge_depth_reconciliation)
  end

  def test_capability_gap_requires_explicit_signal_not_inferred_from_missing_evidence
    ordinary = Reconciler.reconcile(
      packet_depth: 'BOUNDED',
      canonical_required_depth: 'FULL',
      evidence_satisfied: false
    )
    refute ordinary.fetch(:implementation_mutation_required)

    genuine_gap = Reconciler.reconcile(
      packet_depth: 'BOUNDED',
      canonical_required_depth: 'FULL',
      evidence_satisfied: false,
      capability_gap: true
    )
    assert genuine_gap.fetch(:implementation_mutation_required)
  end

  def test_format_fields_renders_all_five_canonical_lines
    result = Reconciler.reconcile(
      packet_depth: 'BOUNDED',
      canonical_required_depth: 'FULL',
      evidence_satisfied: false
    )
    text = Reconciler.format_fields(result)
    assert_equal <<~TEXT.strip, text
      PACKET_JUDGE_DEPTH: BOUNDED
      CANONICAL_REQUIRED_JUDGE_DEPTH: FULL
      JUDGE_DEPTH_RECONCILIATION: ESCALATION_REQUIRED
      MISSING_JUDGE_EVIDENCE: FULL_SUITE
      IMPLEMENTATION_MUTATION_REQUIRED: NO
    TEXT
  end

  def test_unknown_depth_value_fails_closed
    error = assert_raises(Reconciler::MissingContract) do
      Reconciler.reconcile(packet_depth: 'DEEP', canonical_required_depth: 'FULL')
    end
    assert_match(/unknown PACKET_JUDGE_DEPTH/, error.message)
  end

  def flattened_contract
    Reconciler.contract_text.gsub(/\s+/, ' ')
  end

  def test_reconciler_fails_closed_when_enum_line_removed_from_contract
    stripped = flattened_contract.sub(
      'JUDGE_DEPTH_RECONCILIATION: MATCH | ESCALATION_REQUIRED', ''
    )
    error = assert_raises(Reconciler::MissingContract) do
      Reconciler.reconcile(packet_depth: 'BOUNDED', canonical_required_depth: 'BOUNDED', contract: stripped)
    end
    assert_match(/JUDGE_DEPTH_RECONCILIATION: MATCH \| ESCALATION_REQUIRED/, error.message)
  end

  def test_reconciler_fails_closed_when_non_blame_sentence_removed_from_contract
    stripped = flattened_contract.sub(
      'A depth mismatch by itself is never evidence that the implementation is wrong', ''
    )
    error = assert_raises(Reconciler::MissingContract) do
      Reconciler.reconcile(packet_depth: 'BOUNDED', canonical_required_depth: 'FULL', contract: stripped)
    end
    assert_match(/implementation is wrong/, error.message)
  end

  def test_reconciler_fails_closed_when_worktree_preservation_sentence_removed
    stripped = flattened_contract.sub(
      'keep the same branch, the same worktree, the same implementation tree', ''
    )
    error = assert_raises(Reconciler::MissingContract) do
      Reconciler.reconcile(packet_depth: 'BOUNDED', canonical_required_depth: 'FULL', contract: stripped)
    end
    assert_match(/same worktree/, error.message)
  end

  def test_reconciler_fails_closed_when_no_repeat_stop_sentence_removed
    stripped = flattened_contract.sub('must restate this exact escalation', '')
    error = assert_raises(Reconciler::MissingContract) do
      Reconciler.reconcile(packet_depth: 'BOUNDED', canonical_required_depth: 'FULL', contract: stripped)
    end
    assert_match(/must restate this exact escalation/, error.message)
  end
end
