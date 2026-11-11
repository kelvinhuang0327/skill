# frozen_string_literal: true

require 'minitest/autorun'

class ProjectInterpreterPreflightRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  PYTHON_RULE = <<~TEXT.chomp
    - When the repository or Packet specifies a Python interpreter, use that exact
      interpreter for every Python command, including one-line helpers and receipt
      utilities. For disk-space checks, prefer shell `df`; do not invoke system
      `python` or `python3` as a shortcut.
  TEXT
  R100_EXACT_SOURCE_RULE = <<~TEXT.chomp
    - When an exact source pin is absent locally, probe a remote only if the
      Packet explicitly allows remote source lookup and pins one exact commit or
      blob. Fetch only that object into an isolated disposable bare probe (for
      Git, `git fetch --no-tags --no-write-fetch-head <allowed-remote>
      <exact-object>`); do not fetch tags, branches, all refs, or history, and do
      not change the destination project's refs. Record exact fetch result
      separately from local presence; keep credential-bearing URLs and command
      output out of evidence. If that exact fetch is unavailable, leave identity
      unresolved and source semantics `UNKNOWN`; never use a nearby ref or proxy
      evidence.
  TEXT

  def test_python_rule_is_once_in_execution_for_shared_and_all_platform_skills
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      execution = skill.index("## Execution\n")
      verification = skill.index("## Verification\n")

      refute_nil execution, "#{relative_path} is missing ## Execution"
      refute_nil verification, "#{relative_path} is missing ## Verification"
      assert_operator execution, :<, verification, relative_path
      section = skill[execution...verification]
      assert_equal 1, section.scan(Regexp.new(Regexp.escape(PYTHON_RULE))).length,
                   "#{relative_path} must contain the Python interpreter rule once"
    end
  end

  def test_r100_exact_source_probe_rule_remains_once_in_every_skill
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      execution = skill.index("## Execution\n")
      verification = skill.index("## Verification\n")

      refute_nil execution, "#{relative_path} is missing ## Execution"
      refute_nil verification, "#{relative_path} is missing ## Verification"
      section = skill[execution...verification]
      assert_equal 1, section.scan(Regexp.new(Regexp.escape(R100_EXACT_SOURCE_RULE))).length,
                   "#{relative_path} must retain the R100 exact-source rule once"
    end
  end
end
