# frozen_string_literal: true

require 'minitest/autorun'

class EvidenceSummaryOptionalRepoRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  RULE = 'leave out `--repo <repo>` when the Packet names no repository or forbids reading it'

  def test_optional_repo_instruction_appears_once_inside_task_receipt
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      receipt = skill.index("### Task receipt\n")
      triggers = skill.index("## Explicit Packet triggers\n")

      refute_nil receipt, "#{relative_path} is missing ### Task receipt"
      refute_nil triggers, "#{relative_path} is missing ## Explicit Packet triggers"
      assert_operator receipt, :<, triggers, relative_path

      normalized = skill.gsub(/\s+/, ' ')
      assert_equal 1, normalized.scan(Regexp.new(Regexp.escape(RULE))).length, "#{relative_path} repeats the optional-repo instruction"

      receipt_section = skill[receipt...triggers].gsub(/\s+/, ' ')
      assert_equal 1, receipt_section.scan(Regexp.new(Regexp.escape(RULE))).length, "#{relative_path} instruction is outside Task receipt"
    end
  end
end
