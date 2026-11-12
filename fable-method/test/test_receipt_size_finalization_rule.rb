# frozen_string_literal: true

require 'minitest/autorun'

class ReceiptSizeFinalizationRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  MARKER = 'When a Packet requires `task_folder_bytes`'
  RULE = 'When a Packet requires `task_folder_bytes`, finish writing every required task receipt first. Then compute the task-folder total and files over 100 MB; update the required size fields in `result.json` and `final_reply.md`, recalculating until both records match the final files.'

  def test_size_finalization_rule_appears_once_in_each_skill_file
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path)).gsub(/\s+/, ' ')

      assert_equal 1, skill.scan(MARKER).length, "#{relative_path} must contain the rule marker once"
      assert_equal 1, skill.scan(RULE).length, "#{relative_path} must contain the complete rule once"
    end
  end
end
