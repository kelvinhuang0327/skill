# frozen_string_literal: true

require 'minitest/autorun'

class TaskIdPreExecutionGuardTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  GUARD_HEADING = "## Pre-execution TASK_ID guard\n"

  def test_receipt_required_state_changes_stop_before_execution_without_task_id
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      guard_at = skill.index(GUARD_HEADING)
      execution_at = skill.index("## Execution\n")

      refute_nil guard_at, "#{relative_path} is missing the pre-execution TASK_ID guard"
      refute_nil execution_at, "#{relative_path} is missing ## Execution"
      assert_operator guard_at, :<, execution_at, relative_path

      guard = skill[guard_at...execution_at].gsub(/\s+/, ' ').downcase
      [
        '`state_changing_implementation`',
        '`persistent_evidence_root`',
        '`signal_policy`',
        'non-empty, explicit `task_id`',
        'missing or blank',
        'before execution',
        'before any mutation',
        'do not infer or synthesize',
        'do not create or claim',
        '`blocked_missing_task_id`'
      ].each do |required_text|
        assert_includes guard, required_text, "#{relative_path} is missing #{required_text.inspect}"
      end
    end
  end

  def test_guard_preserves_non_state_changing_and_no_packet_flows
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      guard_at = skill.index(GUARD_HEADING)
      execution_at = skill.index("## Execution\n")
      refute_nil guard_at, "#{relative_path} is missing the pre-execution TASK_ID guard"
      refute_nil execution_at, "#{relative_path} is missing ## Execution"

      guard = skill[guard_at...execution_at].gsub(/\s+/, ' ').downcase
      [
        '`pure_qa`',
        '`planning_only`',
        '`read_only_completion_review`',
        'compact flow without a packet'
      ].each do |supported_flow|
        assert_includes guard, supported_flow, "#{relative_path} must preserve #{supported_flow}"
      end
    end
  end
end
