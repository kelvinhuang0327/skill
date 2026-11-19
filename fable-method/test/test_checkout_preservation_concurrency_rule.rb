# frozen_string_literal: true

require 'minitest/autorun'

class CheckoutPreservationConcurrencyRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  RULE_START = '- When a task requires its final snapshot'
  RULE_END = 'run it in a different checkout.'
  REQUIRED_TEXT = [
    "a checkout's `HEAD`, index, or working tree stayed unchanged",
    'reserve that exact checkout from task start through the final snapshot',
    'The Owner and every agent must complete any `commit`, `pull`, `switch`/`checkout`, staging',
    'or other operation that would change those states before the task starts',
    'defer it until after the final snapshot or',
    'run it in a different checkout.'
  ].freeze

  def test_checkout_preservation_rule_covers_owner_agents_and_all_generated_platforms
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      execution = skill.index("## Execution\n")
      verification = skill.index("## Verification\n")

      refute_nil execution, "#{relative_path} is missing ## Execution"
      refute_nil verification, "#{relative_path} is missing ## Verification"
      assert_operator execution, :<, verification, relative_path

      section = skill[execution...verification].gsub(/\s+/, ' ')
      start = section.index(RULE_START)
      ending = section.index(RULE_END, start || 0)

      refute_nil start, "#{relative_path} is missing the checkout reservation rule"
      refute_nil ending, "#{relative_path} is missing the rule's isolation action"
      assert_equal 1, section.scan(RULE_START).length, "#{relative_path} repeats the rule"
      assert_equal 1, section.scan(RULE_END).length, "#{relative_path} repeats the isolation action"

      rule = section[start...(ending + RULE_END.length)]
      REQUIRED_TEXT.each do |text|
        assert_includes rule, text, "#{relative_path} is missing #{text.inspect}"
      end
    end
  end
end
