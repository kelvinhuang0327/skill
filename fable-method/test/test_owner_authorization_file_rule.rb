# frozen_string_literal: true

require 'minitest/autorun'

class OwnerAuthorizationFileRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  REQUIRED_TEXT = [
    'OWNER_AUTHORIZATION_FILE',
    'a line in it names',
    'TASK_ID',
    'packet_sha256',
    'PACKET_FILE',
    '`authorization-check`',
    'authorizes nothing',
    'Never create, edit, or delete an authorization file'
  ].freeze
  OWNER_WORDS = "The Owner's words in the Packet or in this conversation count."

  def test_owner_authorization_file_rule_is_in_hard_boundary_one
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path), encoding: 'UTF-8')
      start = skill.index('1. No push, merge')
      ending = skill.index('2. Never overwrite', start || 0)

      refute_nil start, "#{relative_path} is missing hard boundary 1"
      refute_nil ending, "#{relative_path} is missing hard boundary 2"
      assert_operator start, :<, ending, relative_path

      rule = skill[start...ending]
      normalized = rule.gsub(/\s+/, ' ')
      REQUIRED_TEXT.each do |text|
        assert_includes normalized, text, "#{relative_path} is missing #{text.inspect}"
      end

      owner_words_index = normalized.index(OWNER_WORDS)
      authorization_index = normalized.index('OWNER_AUTHORIZATION_FILE')
      pending_index = normalized.index('Without them, report')
      refute_nil owner_words_index, "#{relative_path} is missing the original Owner words sentence"
      refute_nil authorization_index, "#{relative_path} is missing the authorization file rule"
      refute_nil pending_index, "#{relative_path} is missing the pending instruction"
      assert_operator owner_words_index, :<, authorization_index, relative_path
      assert_operator authorization_index, :<, pending_index, relative_path

      assert_equal 1, skill.scan('OWNER_AUTHORIZATION_FILE').length,
                   "#{relative_path} must mention OWNER_AUTHORIZATION_FILE exactly once"
    end
  end
end
