# frozen_string_literal: true

require 'minitest/autorun'

class TaskReceiptRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  REQUIRED_TEXT = [
    'packet.md',
    '`task_id` key whose value',
    'copied exactly (same case)',
    '`IN_PROGRESS`',
    '`COMPLETED`',
    '`BLOCKED`',
    '`ABORTED`',
    'SIGNAL_POLICY',
    'process_signal',
    'UNNECESSARY_WORK',
    'Add no other key',
    '`status`',
    '`summary`',
    '`outcome`',
    '`signal_policy`',
    '`attempt`',
    '`/goal `',
    'is not an incident',
    'evidence-summary'
  ].freeze

  def test_task_receipt_rule_is_present_between_report_and_packet_triggers
    SKILLS.each do |relative_path|
      path = File.join(ROOT, relative_path)
      skill = File.read(path)
      report = skill.index("## Report\n")
      receipt = skill.index("### Task receipt\n")
      triggers = skill.index("## Explicit Packet triggers\n")

      refute_nil report, "#{relative_path} is missing ## Report"
      refute_nil receipt, "#{relative_path} is missing ### Task receipt"
      refute_nil triggers, "#{relative_path} is missing ## Explicit Packet triggers"
      assert_operator report, :<, receipt, relative_path
      assert_operator receipt, :<, triggers, relative_path
      assert_equal receipt, skill.rindex("### Task receipt\n"), relative_path

      REQUIRED_TEXT.each do |text|
        assert_includes skill, text, "#{relative_path} is missing #{text.inspect}"
      end
    end
  end

  def test_structured_receipt_allowlist_precedes_parsing_and_shape_inspection
    skill_path = File.join(ROOT, SKILLS.first)
    skill = File.read(skill_path)

    assert_match(
      /Before parsing or inspecting a structured receipt, define an explicit\s+per-file field allowlist from the Packet\. Read structured files only through\s+that allowlist; never enumerate keys, inspect shape\/schema, or perform broad\s+field\/schema discovery\./,
      skill
    )
  end
end
