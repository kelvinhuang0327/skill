# frozen_string_literal: true

require 'minitest/autorun'
require_relative '../scripts/explicit_contract'

# Fail-closed binding for five explicit /fable-method Worker contract
# semantics. Tests invoke the shipped parser on live canonical files.
class ExplicitContractFailClosedTest < Minitest::Test
  Parser = Fable::ExplicitContract

  def test_parser_reads_live_canonical_files
    paths = Parser.live_paths
    assert_equal File.expand_path('../shared/SKILL.md', __dir__), paths[:skill]
    assert_equal File.expand_path('../shared/references/reporting.md', __dir__), paths[:reporting]
    assert_equal File.expand_path('../shared/references/operational-gates.md', __dir__),
                 paths[:operational_gates]
    paths.each_value { |path| assert File.file?(path), path }
  end

  def test_result_binding_nonzero_git_diff_check_cannot_be_pass
    refute Parser.load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 1,
      observed_satisfies_acceptance: false
    )
    refute Parser.load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 1,
      observed_satisfies_acceptance: true
    )
    assert Parser.load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 0,
      observed_satisfies_acceptance: true
    )
  end

  def test_result_binding_command_execution_alone_is_not_pass
    refute Parser.load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 0,
      observed_satisfies_acceptance: false
    )
    refute Parser.load_bearing_pass?(
      command: 'true',
      exit_status: 0,
      observed_satisfies_acceptance: false
    )
  end

  def test_result_binding_detector_fails_closed_without_contract_sentence
    error = assert_raises(Parser::MissingContract) do
      Parser.load_bearing_pass?(
        command: 'git diff --check',
        exit_status: 1,
        observed_satisfies_acceptance: true,
        contract: 'NOT RUN is never PASS'
      )
    end
    assert_match(/result-binding contract is missing/, error.message)
  end

  def test_result_binding_fails_closed_when_observed_result_phrase_removed
    stripped = "#{Parser.skill}\n#{Parser.reporting}".gsub('exact observed result', 'claimed result')
    error = assert_raises(Parser::MissingContract) do
      Parser.load_bearing_pass?(
        command: 'git diff --check',
        exit_status: 0,
        observed_satisfies_acceptance: true,
        contract: stripped
      )
    end
    assert_match(/exact observed result/, error.message)
  end

  def test_stop_blocks_mutation_and_workarounds
    %i[mutation equivalent_command_substitution metadata_workaround upstream_rewrite retry_under_different_action_class].each do |action|
      refute Parser.stop_allows?(action, stop_reached: true)
    end
    assert Parser.stop_allows?(:mutation, stop_reached: false)
    assert Parser.stop_allows?(:mutation, stop_reached: true, continuation_authority: :owner_instruction)
    assert Parser.stop_allows?(:mutation, stop_reached: true, continuation_authority: :continuation_delta)
  end

  def test_stop_fails_closed_when_no_mutation_phrase_removed
    stripped = Parser.skill.sub('no mutation, ', '')
    refute_includes stripped.gsub(/\s+/, ' '), 'no mutation'
    error = assert_raises(Parser::MissingContract) do
      Parser.stop_allows?(:mutation, stop_reached: true, contract: stripped)
    end
    assert_match(/no mutation/, error.message)
  end

  def test_stop_unknown_action_is_not_silently_allowed
    error = assert_raises(Parser::MissingContract) do
      Parser.stop_allows?(:metadata_rewrite, stop_reached: false)
    end
    assert_match(/unknown STOP action/, error.message)
  end

  def test_forbidden_transcript_cannot_be_fallback
    refute Parser.forbidden_fallback_allowed?(
      'transcript',
      source_forbidden: true,
      preferred_incomplete: true
    )
    refute Parser.forbidden_fallback_allowed?(
      'transcript',
      source_forbidden: false,
      preferred_incomplete: true
    )
    refute Parser.forbidden_fallback_allowed?(
      'evidence_class',
      source_forbidden: true,
      preferred_incomplete: true
    )
  end

  def test_forbidden_fails_closed_when_transcript_sentence_removed
    stripped = "#{Parser.skill}\n#{Parser.operational_gates}".sub(
      /Transcript is\s+not an authority fallback by default\.?/i,
      ''
    )
    error = assert_raises(Parser::MissingContract) do
      Parser.forbidden_fallback_allowed?(
        'transcript',
        source_forbidden: false,
        preferred_incomplete: true,
        contract: stripped
      )
    end
    assert_match(/transcript is not an authority fallback by default/, error.message)
  end

  def test_non_force_rejects_git_force_family
    [
      'git push --force origin master',
      'git push -f origin master',
      'git push --force-with-lease origin master',
      'git push --force-if-includes origin master',
      'git push --force-with-lease=refs/heads/master origin master'
    ].each do |command|
      assert Parser.git_force_rejected?(command, force_fallback_authorized: false), command
      refute Parser.git_force_rejected?(command, force_fallback_authorized: true), command
    end
    refute Parser.git_force_rejected?('sandbox-exec -f profile.sb git status', force_fallback_authorized: false)
    refute Parser.git_force_rejected?('git status', force_fallback_authorized: false)
  end

  def test_canonical_judge_mode_enum_rejects_unknown_values
    canonical = Parser.routing_enum('JUDGE_MODE')
    assert_includes canonical, 'FRESH_CONTEXT'
    assert_includes canonical, 'SELF_CHECK_ONLY'
    assert_includes canonical, 'NOT_APPLICABLE'
    assert Parser.judge_mode_accepted?('FRESH_CONTEXT')
    assert Parser.judge_mode_accepted?('SELF_CHECK_ONLY')
    assert Parser.judge_mode_accepted?('NOT_APPLICABLE')
    refute Parser.judge_mode_accepted?('AUTO_VERIFIED')
    refute Parser.judge_mode_accepted?('INDEPENDENT')
    refute Parser.judge_mode_accepted?('BOUNDED')
  end

  def test_unknown_judge_mode_does_not_silently_join_the_live_enum
    live = Parser.routing_enum('JUDGE_MODE')
    refute_includes live, 'AUTO_VERIFIED'
    fixture = Parser.skill.sub(
      /^JUDGE_MODE: .+$/,
      'JUDGE_MODE: FRESH_CONTEXT | NOT_APPLICABLE'
    )
    refute Parser.judge_mode_accepted?('SELF_CHECK_ONLY', contract: fixture)
    refute_includes Parser.routing_enum('JUDGE_MODE', contract: fixture), 'SELF_CHECK_ONLY'
  end

  def test_canonical_route_and_task_class_enums_reject_unknown_values
    assert Parser.enum_accepted?('WORKER_ROUTE', 'STANDARD_JUDGED')
    assert Parser.enum_accepted?('TASK_CLASS', 'STATE_CHANGING_IMPLEMENTATION')
    refute Parser.enum_accepted?('WORKER_ROUTE', 'AUTO')
    refute Parser.enum_accepted?('TASK_CLASS', 'WHATEVER')
  end
end
