# frozen_string_literal: true

require 'minitest/autorun'
require 'open3'

class StrategySourcePreflightRuleTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  SKILLS = [
    'fable-method/shared/SKILL.md',
    'fable-method/platforms/claude/fable-method/SKILL.md',
    'fable-method/platforms/codex/fable-method/SKILL.md',
    'fable-method/platforms/gemini/fable-method/SKILL.md',
    'fable-method/platforms/antigravity/fable-method/SKILL.md'
  ].freeze
  CHECKPOINT_REFERENCES = [
    'fable-method/shared/references/task-checkpoint.md',
    'fable-method/platforms/claude/fable-method/references/task-checkpoint.md',
    'fable-method/platforms/codex/fable-method/references/task-checkpoint.md',
    'fable-method/platforms/gemini/fable-method/references/task-checkpoint.md',
    'fable-method/platforms/antigravity/fable-method/references/task-checkpoint.md'
  ].freeze
  RULE = <<~TEXT.chomp
    - Before long historical strategy/backfill work maps strategy semantics or
      starts an expensive replay, resolve each Packet-named source pin (exact
      commit, blob, or path at revision) against only the permitted repository
      refs/object database. For Git, verify the exact commit/object and path (for
      example, `git cat-file -e <commit>^{commit}`,
      `git cat-file -e <commit>:<path>`, or
      `git cat-file -e <blob>^{blob}`) and record the resolved commit/path/blob.
      If the exact source is unavailable, leave that strategy identity unresolved
      and its source semantics `UNKNOWN`; do not map or replay that identity. A
      strategy name, ticket count, present-day adapter, or partial golden output
      cannot substitute for source semantics.
  TEXT
  RUNTIME_SOURCE_RESUME_RULE = <<~TEXT.chomp
    Execution/runtime identity is separate from load-bearing input identity.
    Before resuming persisted computation, require the recorded runtime/source
    identity to match the exact runtime and source bytes that will execute. If
    code bytes differ, preserve the partial run and start a new run under a newly
    computed identity; never override or reuse the old execution version or
    source SHA. If the identity cannot be established or compared, stop before
    continuing.
  TEXT

  def test_source_preflight_rule_is_once_in_execution_for_every_platform
    SKILLS.each do |relative_path|
      skill = File.read(File.join(ROOT, relative_path))
      execution = skill.index("## Execution\n")
      verification = skill.index("## Verification\n")

      refute_nil execution, "#{relative_path} is missing ## Execution"
      refute_nil verification, "#{relative_path} is missing ## Verification"
      assert_operator execution, :<, verification, relative_path
      section = skill[execution...verification]
      assert_equal 1, section.scan(Regexp.new(Regexp.escape(RULE))).length,
                   "#{relative_path} must contain the preflight rule once in ## Execution"
    end
  end

  def test_available_source_locator_is_resolved_and_recorded_before_work
    SKILLS.each do |relative_path|
      section = execution_section(relative_path)
      assert_includes section, 'resolve each Packet-named source pin'
      assert_includes section, 'only the permitted repository'
      assert_includes section, '`git cat-file -e <commit>^{commit}`'
      assert_includes section, '`git cat-file -e <commit>:<path>`'
      assert_includes section, 'record the resolved commit/path/blob'
    end
  end

  def test_unavailable_source_keeps_identity_unresolved_and_rejects_proxies
    SKILLS.each do |relative_path|
      section = execution_section(relative_path).gsub(/\s+/, ' ')
      assert_includes section, 'If the exact source is unavailable'
      assert_includes section, 'leave that strategy identity unresolved'
      assert_includes section, 'source semantics `UNKNOWN`'
      assert_includes section, 'do not map or replay that identity'
      assert_includes section, 'A strategy name, ticket count, present-day adapter, or partial golden output'
      assert_includes section, 'cannot substitute for source semantics'
    end
  end

  def test_git_locator_lookup_distinguishes_present_and_missing_source_paths
    commit = git_output('rev-parse', 'HEAD')
    _stdout, stderr, present_status = git_read('cat-file', '-e', "#{commit}:fable-method/shared/references/task-checkpoint.md")
    assert_predicate present_status, :success?, stderr

    _stdout, _stderr, missing_status = git_read(
      'cat-file', '-e', "#{commit}:fable-method/shared/references/__missing_source_locator__.md"
    )
    refute_predicate missing_status, :success?, 'missing source locator must fail the object lookup'
  end

  def test_runtime_source_identity_resume_rule_remains_in_every_checkpoint_reference
    CHECKPOINT_REFERENCES.each do |relative_path|
      reference = File.read(File.join(ROOT, relative_path))
      assert_equal 1, reference.scan(Regexp.new(Regexp.escape(RUNTIME_SOURCE_RESUME_RULE))).length,
                   "#{relative_path} must retain the runtime/source identity resume rule"
    end
  end

  private

  def execution_section(relative_path)
    skill = File.read(File.join(ROOT, relative_path))
    execution = skill.index("## Execution\n")
    verification = skill.index("## Verification\n")
    refute_nil execution, "#{relative_path} is missing ## Execution"
    refute_nil verification, "#{relative_path} is missing ## Verification"
    skill[execution...verification]
  end

  def git_read(*args)
    Open3.capture3('git', '--no-optional-locks', '-c', 'core.fsmonitor=false', '-C', ROOT, *args)
  end

  def git_output(*args)
    stdout, stderr, status = git_read(*args)
    assert_predicate status, :success?, stderr
    stdout.strip
  end
end
