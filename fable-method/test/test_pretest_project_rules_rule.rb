# frozen_string_literal: true

require 'minitest/autorun'

class PretestProjectRulesRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  RULE = [
    'Before any test command, read applicable `AGENTS.md` and `AGENTS.override.md` files from the repository root through each test target directory.',
    'Follow their required interpreter or virtual environment and test scratch/temp policy, including pytest cache and `--basetemp` rules;',
    "do not substitute system Python or pytest's default temporary directory when project rules specify other settings."
  ].join(' ')

  def test_project_test_environment_rules_precede_verification
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      execution = skill.index("## Execution\n")
      verification = skill.index("## Verification\n")

      refute_nil execution, "#{relative_path} is missing ## Execution"
      refute_nil verification, "#{relative_path} is missing ## Verification"
      assert_operator execution, :<, verification, relative_path

      normalized = skill.gsub(/\s+/, ' ')
      execution_start = normalized.index('## Execution ')
      verification_start = normalized.index('## Verification ')
      section = normalized[execution_start...verification_start]
      assert_equal 1, normalized.scan(Regexp.new(Regexp.escape(RULE))).length,
                   "#{relative_path} repeats the pretest rule"
      assert_includes section, RULE, "#{relative_path} places the rule outside ## Execution"
    end
  end
end
