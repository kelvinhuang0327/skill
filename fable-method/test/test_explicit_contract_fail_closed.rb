# frozen_string_literal: true

require 'minitest/autorun'

# Fail-closed binding for five explicit /fable-method Worker contract
# semantics. Helpers parse the live canonical contract; they do not invent a
# second authority or a Worker simulator.
class ExplicitContractFailClosedTest < Minitest::Test
  SHARED_ROOT = File.expand_path('../shared', __dir__)
  SKILL = File.read(File.join(SHARED_ROOT, 'SKILL.md'), encoding: 'UTF-8')
  REPORTING = File.read(File.join(SHARED_ROOT, 'references', 'reporting.md'), encoding: 'UTF-8')
  OPERATIONAL_GATES = File.read(
    File.join(SHARED_ROOT, 'references', 'operational-gates.md'),
    encoding: 'UTF-8'
  )
  RESULT_CONTRACT = [SKILL, REPORTING].join("\n")

  def routing_enum(label, contract = SKILL)
    match = /^#{Regexp.escape(label)}: (.+)$/.match(contract)
    raise "missing routing enum #{label}" if match.nil?

    match[1].split('|').map(&:strip)
  end

  def git_force_family(contract = OPERATIONAL_GATES)
    match = /GIT_FORCE_FAMILY:\n((?:.+\n)+?)```/.match(contract)
    raise 'missing GIT_FORCE_FAMILY' if match.nil?

    match[1].split("\n").map(&:strip).reject(&:empty?)
  end

  def load_bearing_pass?(command:, exit_status:, observed_satisfies_acceptance:, contract: RESULT_CONTRACT)
    flattened = contract.gsub(/\s+/, ' ')
    unless flattened.include?('Command execution alone is not `PASS`') &&
           flattened.include?('`git diff --check` cannot be reported `PASS`')
      raise 'result-binding contract is missing'
    end

    return false if exit_status.nil?
    return false if command == 'git diff --check' && exit_status != 0
    return false unless observed_satisfies_acceptance

    true
  end

  def stop_allows?(action, stop_reached:, continuation_authority: nil, contract: SKILL)
    unless contract.include?('no equivalent command substitution') &&
           contract.include?('Continuation Delta')
      raise 'STOP fail-closed contract is missing'
    end

    return true unless stop_reached
    return true if %i[owner_instruction continuation_delta].include?(continuation_authority)

    false
  end

  FORBIDDEN_CONTRACT = [SKILL, OPERATIONAL_GATES].join("\n")

  def forbidden_fallback_allowed?(source_class, source_forbidden:, preferred_incomplete:, contract: FORBIDDEN_CONTRACT)
    flattened = contract.gsub(/\s+/, ' ')
    unless flattened.include?('as a fallback because preferred evidence is incomplete') &&
           flattened.downcase.include?('transcript is not an authority fallback by default')
      raise 'FORBIDDEN fallback contract is missing'
    end

    return false if source_forbidden
    return false if source_class == 'transcript' && preferred_incomplete

    !preferred_incomplete || !source_forbidden
  end

  def git_force_rejected?(command, force_fallback_authorized:, contract: OPERATIONAL_GATES)
    unless contract.include?('is the live NON_FORCE Git authorization')
      raise 'NON_FORCE Git force-family contract is missing'
    end

    return false if force_fallback_authorized
    tokens = command.split(/\s+/)
    return false unless tokens.first == 'git'

    family = git_force_family(contract)
    !(tokens & family).empty?
  end

  def judge_mode_accepted?(value, contract: SKILL)
    routing_enum('JUDGE_MODE', contract).include?(value)
  end

  def test_result_binding_nonzero_git_diff_check_cannot_be_pass
    refute load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 1,
      observed_satisfies_acceptance: false
    )
    refute load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 1,
      observed_satisfies_acceptance: true
    )
    assert load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 0,
      observed_satisfies_acceptance: true
    )
  end

  def test_result_binding_command_execution_alone_is_not_pass
    refute load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 0,
      observed_satisfies_acceptance: false
    )
  end

  def test_result_binding_detector_fails_closed_without_contract_sentence
    error = assert_raises(RuntimeError) do
      load_bearing_pass?(
        command: 'git diff --check',
        exit_status: 1,
        observed_satisfies_acceptance: true,
        contract: 'NOT RUN is never PASS'
      )
    end
    assert_match(/result-binding contract is missing/, error.message)
  end

  def test_stop_blocks_mutation_and_workarounds
    %i[mutation equivalent_command_substitution metadata_workaround upstream_rewrite retry_under_different_action_class].each do |action|
      refute stop_allows?(action, stop_reached: true)
    end
    assert stop_allows?(:mutation, stop_reached: false)
    assert stop_allows?(:mutation, stop_reached: true, continuation_authority: :owner_instruction)
    assert stop_allows?(:mutation, stop_reached: true, continuation_authority: :continuation_delta)
  end

  def test_forbidden_transcript_cannot_be_fallback
    refute forbidden_fallback_allowed?(
      'transcript',
      source_forbidden: true,
      preferred_incomplete: true
    )
    refute forbidden_fallback_allowed?(
      'transcript',
      source_forbidden: false,
      preferred_incomplete: true
    )
    refute forbidden_fallback_allowed?(
      'evidence_class',
      source_forbidden: true,
      preferred_incomplete: true
    )
  end

  def test_non_force_rejects_git_force_family
    [
      'git push --force origin master',
      'git push -f origin master',
      'git push --force-with-lease origin master',
      'git push --force-if-includes origin master'
    ].each do |command|
      assert git_force_rejected?(command, force_fallback_authorized: false), command
      refute git_force_rejected?(command, force_fallback_authorized: true), command
    end
    refute git_force_rejected?('sandbox-exec -f profile.sb git status', force_fallback_authorized: false)
    refute git_force_rejected?('git status', force_fallback_authorized: false)
  end

  def test_canonical_judge_mode_enum_rejects_unknown_values
    canonical = routing_enum('JUDGE_MODE')
    assert_includes canonical, 'FRESH_CONTEXT'
    assert_includes canonical, 'NOT_APPLICABLE'
    assert judge_mode_accepted?('FRESH_CONTEXT')
    assert judge_mode_accepted?('NOT_APPLICABLE')
    refute judge_mode_accepted?('AUTO_VERIFIED')
    refute judge_mode_accepted?('INDEPENDENT')
    refute judge_mode_accepted?('BOUNDED')
  end

  def test_unknown_judge_mode_does_not_silently_join_the_live_enum
    live = routing_enum('JUDGE_MODE')
    refute_includes live, 'AUTO_VERIFIED'
    fixture = SKILL.sub(
      /^JUDGE_MODE: .+$/,
      'JUDGE_MODE: FRESH_CONTEXT | NOT_APPLICABLE'
    )
    refute judge_mode_accepted?('SELF_CHECK_ONLY', contract: fixture)
    assert judge_mode_accepted?('SELF_CHECK_ONLY') == live.include?('SELF_CHECK_ONLY')
  end
end
