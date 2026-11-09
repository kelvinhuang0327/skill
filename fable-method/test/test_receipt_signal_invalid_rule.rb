# frozen_string_literal: true

require 'minitest/autorun'

class ReceiptSignalInvalidRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  REQUIRED_RULE = [
    '- Fix the receipt until it reports `identity_status: MATCH`,',
    '  `lifecycle_state_check: CANONICAL`, and a `signal.status` other than',
    '  `INVALID`. A receipt format slip fixed this way is not an incident: it does',
    '  not by itself make `trigger` `INCIDENT`.'
  ].join("\n").freeze
  OLD_RULE = [
    '  `identity_status: MATCH` and `lifecycle_state_check: CANONICAL`. A receipt',
    '  format slip fixed this way is not an incident: it does not by itself make',
    '  `trigger` `INCIDENT`.'
  ].join("\n").freeze

  def test_invalid_signal_rule_appears_once_inside_task_receipt_in_all_skills
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      receipt = skill.index("### Task receipt\n")
      explicit_triggers = skill.index("## Explicit Packet triggers\n")
      rule = skill.index(REQUIRED_RULE)

      refute_nil receipt, "#{relative_path} is missing ### Task receipt"
      refute_nil explicit_triggers, "#{relative_path} is missing ## Explicit Packet triggers"
      refute_nil rule, "#{relative_path} is missing the INVALID signal condition"
      assert_equal 1, skill.scan(Regexp.new(Regexp.escape(REQUIRED_RULE))).length,
                   "#{relative_path} repeats the INVALID signal condition"
      assert_operator receipt, :<, rule, "#{relative_path} places the condition before Task receipt"
      assert_operator rule + REQUIRED_RULE.length, :<, explicit_triggers,
                      "#{relative_path} places the condition after Explicit Packet triggers"
      assert_includes skill, "`signal.status` other than\n  `INVALID`", relative_path
      refute_includes skill, OLD_RULE, "#{relative_path} retains the old receipt condition"
    end
  end
end
