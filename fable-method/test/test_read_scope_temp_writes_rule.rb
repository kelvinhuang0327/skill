# frozen_string_literal: true

require 'minitest/autorun'

class ReadScopeTempWritesRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  TEMP_WRITES_RULE = '- Temporary files are writes too.'
  READ_SCOPE_RULE = '- Keep reads inside what the Packet lets you read.'
  REQUIRED_TEXT = [
    '`PERSISTENT_EVIDENCE_ROOT`',
    'the Packet, the project rules, or this skill names',
    '`/tmp`',
    "a tool's default temporary directory",
    'point tools that default there at one of those paths',
    'explicit path operands',
    'non-empty before using it',
    'an empty list must stop the command, not widen it to the whole repository',
    '`/bin/bash`',
    '`mapfile`',
    '`readarray`',
    'Python bytecode counts too:',
    '`-B`',
    '`PYTHONDONTWRITEBYTECODE=1`',
    'so no `__pycache__` appears there'
  ].freeze

  def test_temp_write_and_read_scope_rules_appear_once_in_execution_order
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      execution = skill.index("## Execution\n")
      verification = skill.index("## Verification\n")

      refute_nil execution, "#{relative_path} is missing ## Execution"
      refute_nil verification, "#{relative_path} is missing ## Verification"
      assert_operator execution, :<, verification, relative_path

      section = skill[execution...verification]
      normalized = section.gsub(/\s+/, ' ')
      [TEMP_WRITES_RULE, READ_SCOPE_RULE].each do |rule|
        assert_equal 1, skill.scan(Regexp.new(Regexp.escape(rule))).length, "#{relative_path} repeats #{rule.inspect}"
        assert_equal 1, normalized.scan(Regexp.new(Regexp.escape(rule))).length, "#{relative_path} rule is not unique in ## Execution"
      end

      ordered_text = [
        'a new outcome needs a Planner Delta.',
        TEMP_WRITES_RULE,
        'Python bytecode counts too:',
        READ_SCOPE_RULE,
        '- Read the exact target before editing.'
      ]
      positions = ordered_text.map { |text| normalized.index(text) }
      positions.each_with_index do |position, index|
        refute_nil position, "#{relative_path} is missing ordered text #{ordered_text[index].inspect}"
      end
      assert_equal positions.sort, positions, "#{relative_path} rule order changed"

      REQUIRED_TEXT.each do |text|
        assert_includes normalized, text, "#{relative_path} is missing #{text.inspect}"
      end
    end
  end
end
