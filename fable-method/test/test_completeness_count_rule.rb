# frozen_string_literal: true

require 'minitest/autorun'

class CompletenessCountRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  RULE = <<~TEXT.chomp
    When the work enumerates or recomputes a set (targets, records, candidates),
    take the set's expected size from the Packet or from an independent count made
    before the run, and report expected and produced counts side by side. An
    unexplained difference fails verification: fix it, or report `BLOCKED` with
    both counts; never report `COMPLETED` over it. If no expected size can be
    known, say so and state how the whole set was covered.
  TEXT
  REQUIRED_TEXT = [
    'expected and produced counts side by side',
    'never report `COMPLETED` over it',
    'If no expected size can be'
  ].freeze

  def test_expected_and_produced_count_rule_appears_once_in_verification
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      verification = skill.index("## Verification\n")
      report = skill.index("## Report\n")

      refute_nil verification, "#{relative_path} is missing ## Verification"
      refute_nil report, "#{relative_path} is missing ## Report"
      assert_operator verification, :<, report, relative_path
      rule_pattern = Regexp.new(Regexp.escape(RULE))
      assert_equal 1, skill.scan(rule_pattern).length, "#{relative_path} repeats the count rule"

      section = skill[verification...report]
      assert_equal 1, section.scan(rule_pattern).length, "#{relative_path} count rule is outside ## Verification"
      REQUIRED_TEXT.each do |text|
        assert_includes section, text, "#{relative_path} is missing #{text.inspect}"
      end
    end
  end
end
