# frozen_string_literal: true

require 'minitest/autorun'
require_relative 'support/contract_fixtures'

class ResourceBudgetAndRcaContractTest < Minitest::Test
  include ContractFixtures
  SHARED_ROOT = File.expand_path('../shared', __dir__)
  SKILL = File.read(File.join(SHARED_ROOT, 'SKILL.md'))
  OPERATIONAL_GATES = File.read(File.join(SHARED_ROOT, 'references', 'operational-gates.md'))
  FLOWCHARTS = File.read(File.join(SHARED_ROOT, 'references', 'flowcharts.md'))
  FAILURE_MODES = File.read(File.join(SHARED_ROOT, 'references', 'failure-modes.md'))
  RCA_CONTRACT = [SKILL, FLOWCHARTS, FAILURE_MODES].join("\n")
  JUDGE_HANDOFF = File.read(File.join(SHARED_ROOT, 'references', 'judge-handoff.md'))
  JUDGE_TRIGGER_CONTRACT = {
    'SKILL.md' => SKILL,
    'references/judge-handoff.md' => JUDGE_HANDOFF,
    'references/flowcharts.md' => FLOWCHARTS,
    'references/failure-modes.md' => FAILURE_MODES
  }.freeze

  NUMBER_WORD = '(?:one|two|three|four|five|six|seven|eight|nine|ten|\\d+)'

  # An ordinal-position Judge trigger escalates by attempt number ("a second
  # retry ... is a trigger") instead of by the material unknown category.
  # Sentences are gated on Judge/escalation vocabulary, then split on clause
  # boundaries so a compound "X is not a trigger; Y is" cannot hide an
  # affirmative ordinal clause behind the negation in its first half.
  ORDINAL_ATTEMPT = /
    (?:second|third|fourth|fifth|sixth|nth|\d+(?:st|nd|rd|th))\s+
    (?:(?:consecutive|failed|unattributed|blind|repeated)\s+)*
    (?:retry|retries|attempt|attempts|failure|failures)
    |
    (?:retry|attempt|failure)\s+(?:number|count)
  /xi
  JUDGE_ESCALATION = /\b(?:judge|escalat\w*|trigger\w*)\b/i
  TRIGGER_NEGATION = /\b(?:not|no|never|neither|nor|without|merely)\b/i

  def resource_value(key)
    match = /^#{Regexp.escape(key)}:\n([^\n]+)$/.match(OPERATIONAL_GATES)
    refute_nil match, "missing resource field #{key}"
    match[1]
  end

  def cpu_request_permitted?(workers:, cpu_heavy:, owner_authorized: false)
    return true unless cpu_heavy

    workers <= Integer(resource_value('CPU_BOUND_MAX_WORKERS_WITHOUT_OWNER_AUTHORIZATION')) || owner_authorized
  end

  def test_cpu_heavy_default_is_two
    assert_equal 'SHARED_WORKSTATION', resource_value('RESOURCE_POLICY')
    assert_equal 2, Integer(resource_value('CPU_BOUND_DEFAULT_WORKERS'))
    gates = linked_reference(SKILL, 'operational-gates.md')
    assert_equal OPERATIONAL_GATES, gates
    assert_guarded_contract(gates, [
      'This budget applies only to CPU-heavy work; do not artificially limit ordinary low-CPU commands.',
      'It must not raise concurrency above 2 without direct Owner authorization;',
      'Never use unrestricted CPU-heavy worker selection'
    ], weakenings: {'must not raise concurrency above 2' => 'may raise concurrency above 2'})
    assert_equal 'FORBIDDEN', resource_value('AUTO_CPU_SCALING')
    assert_equal 'FORBIDDEN', resource_value('ALL_CORE_EXECUTION')
    assert cpu_request_permitted?(workers: 1, cpu_heavy: true)
    refute cpu_request_permitted?(workers: 3, cpu_heavy: true)
  end

  def test_cpu_heavy_max_without_owner_authorization_is_two
    assert_equal 2, Integer(resource_value('CPU_BOUND_MAX_WORKERS_WITHOUT_OWNER_AUTHORIZATION'))
    assert_equal 'FORBIDDEN', resource_value('WORKSTATION_SATURATION')
  end

  def test_ten_worker_request_is_rejected_without_owner_authorization
    refute cpu_request_permitted?(workers: 10, cpu_heavy: true)
    assert cpu_request_permitted?(workers: 10, cpu_heavy: true, owner_authorized: true)
    assert_match(/10-worker request is rejected without that authorization/, OPERATIONAL_GATES)
  end

  def test_worker_may_reduce_two_to_one_without_authorization
    assert cpu_request_permitted?(workers: 1, cpu_heavy: true)
    assert_match(/reduce CPU-heavy concurrency from 2 to 1 without authorization/, OPERATIONAL_GATES)
  end

  def test_auto_and_all_core_worker_selection_are_rejected
    assert_equal 'FORBIDDEN', resource_value('AUTO_CPU_SCALING')
    assert_equal 'FORBIDDEN', resource_value('ALL_CORE_EXECUTION')
    ['--workers auto', '--workers > 2', '-j auto', 'pytest -n auto',
     'os.cpu_count()', 'multiprocessing.cpu_count()', 'nproc'].each do |selection|
      assert_includes OPERATIONAL_GATES, selection
    end
  end

  def test_ordinary_low_cpu_command_is_not_artificially_blocked
    assert cpu_request_permitted?(workers: 10, cpu_heavy: false)
    assert_match(/applies only to CPU-heavy work; do not artificially limit ordinary\nlow-CPU commands/, OPERATIONAL_GATES)
  end

  def test_hidden_thread_oversubscription_guidance_exists
    assert_match(/hidden BLAS\/OpenMP thread\noversubscription/, OPERATIONAL_GATES)
    assert_match(/technically applicable and semantics-preserving/, OPERATIONAL_GATES)
    %w[OMP_NUM_THREADS MKL_NUM_THREADS OPENBLAS_NUM_THREADS NUMEXPR_NUM_THREADS].each do |variable|
      assert_includes OPERATIONAL_GATES, "#{variable}=1"
    end
  end

  def test_fourth_evidence_progressing_rca_step_is_permitted
    failures = linked_reference(SKILL, 'failure-modes.md')
    assert_guarded_contract(failures, [
      'Evidence-progressing RCA has no arbitrary numeric ceiling, but stop when scope, safety, authority, capability, proportionality, or discriminating evidence is exhausted.',
      'Each continuation must test a falsifiable hypothesis and materially reduce uncertainty.'
    ], weakenings: {'no arbitrary numeric ceiling' => 'a maximum of three attempts'})
    refute_match(/(?:fourth|later|\d+(?:st|nd|rd|th)) (?:attempt|step).*forbidden/i, RCA_CONTRACT)
  end

  def test_identical_blind_retry_is_not_evidence_progress
    assert_guarded_contract(linked_reference(SKILL, 'failure-modes.md'), [
      'Identical blind retries and speculative patches are not evidence progress.',
      'Each continuation must test a falsifiable hypothesis and materially reduce uncertainty.',
      'When a correction is applied, rerun the real check and retain its actual output.'
    ], weakenings: {'are not evidence progress.' => 'are evidence progress.'})
  end

  def test_no_fixed_numeric_retry_terminal_rule_remains
    refute_match(/evidence-backed attempts\s*<\s*\d+/i, RCA_CONTRACT)
    refute_match(/BLOCKED_AFTER_[A-Z0-9]+_EVIDENCE_BACKED_ATTEMPTS/, RCA_CONTRACT)
    refute_match(/#{NUMBER_WORD}-attempt bound/i, RCA_CONTRACT)
    refute_match(/after (?:exactly )?#{NUMBER_WORD} evidence-backed (?:failed )?(?:attempts|failures),? stop/i, RCA_CONTRACT)
    assert_guarded_contract(SKILL, [
      'stop when scope, safety, authority, capability, proportionality, or discriminating evidence is exhausted.'
    ], weakenings: {'discriminating evidence is exhausted.' => 'attempt number reaches three.'})
    failures = linked_reference(SKILL, 'failure-modes.md')
    assert_guarded_contract(failures, [
      'External credentials, permissions, missing runtimes, and unresolved authority remain blockers.',
      'A stop token is final for the current task authority: no mutation, equivalent command substitution, metadata workaround, upstream rewrite, or retry under a different action class until a new authoritative Owner instruction or valid Continuation Delta.'
    ])
  end

  def ordinal_judge_trigger_clauses(text)
    text.gsub(/\s+/, ' ').split(/(?<=\.)\s+/).flat_map do |sentence|
      next [] unless sentence.match?(JUDGE_ESCALATION)

      sentence.split(/;\s*/).select do |clause|
        clause.match?(ORDINAL_ATTEMPT) && !clause.match?(TRIGGER_NEGATION)
      end
    end
  end

  def test_ordinal_trigger_detector_flags_known_reintroductions
    [
      'A single acceptance failure is not a trigger; a second retry whose cause is still unattributed is.',
      'A single acceptance failure is not a trigger; a third failed attempt escalates to the Judge.',
      'The 2nd retry automatically triggers the Judge.',
      'Escalate to the Judge once the retry count reaches two.'
    ].each do |reintroduction|
      refute_empty ordinal_judge_trigger_clauses(reintroduction),
                   "detector missed an ordinal Judge trigger: #{reintroduction}"
    end
  end

  def test_ordinal_trigger_detector_permits_non_ordinal_semantics
    [
      'A single acceptance failure is not a trigger at any attempt number; material unknown still applies.',
      'A trigger never arises from ordinal position, including a second retry.',
      'Evidence-progressing RCA has no arbitrary numeric ceiling, so a fourth or later evidence-progressing step is permitted.'
    ].each do |permitted|
      assert_empty ordinal_judge_trigger_clauses(permitted),
                   "detector false-positived on permitted semantics: #{permitted}"
    end
  end

  def test_no_ordinal_position_judge_trigger_in_shared_contract
    JUDGE_TRIGGER_CONTRACT.each do |name, text|
      assert_empty ordinal_judge_trigger_clauses(text),
                   "#{name} escalates to the Judge by attempt number"
    end
  end

  def test_retired_ordinal_trigger_clause_is_absent_everywhere
    JUDGE_TRIGGER_CONTRACT.each do |name, text|
      refute_match(/a second retry whose cause is still unattributed/i, text,
                   "#{name} still carries the retired ordinal Judge trigger")
    end
  end

  def test_material_unknown_remains_the_uncertainty_trigger
    assert_guarded_contract(SKILL, [
      'A Judge trigger requires both a listed category and a material consequence:',
      'explicit independent verification, or material unknown evidence.',
      'A single acceptance failure is not a trigger by itself.'
    ], weakenings: {
      'or material unknown evidence.' => '.',
      'not a trigger by itself.' => 'always a trigger.'
    })
    ordinal_fixture = SKILL + ' A second failed attempt automatically triggers the Judge.'
    refute_empty ordinal_judge_trigger_clauses(ordinal_fixture)
    assert_empty ordinal_judge_trigger_clauses(SKILL)
  end

  def test_judge_handoff_defers_to_skill_for_the_trigger_definition
    assert_match(/The Judge trigger has one definition, in `SKILL\.md` "Route once"/, JUDGE_HANDOFF)
    assert_match(/Do not restate or widen that list\nhere\./, JUDGE_HANDOFF)
    refute_match(/acceptance failure/i, JUDGE_HANDOFF,
                 'judge-handoff.md restates Judge-trigger semantics instead of deferring to SKILL.md')
  end

  def compacted_skill
    SKILL.gsub(/\s+/, ' ')
  end

  def recovery_clause(state)
    compacted_skill[/`#{Regexp.escape(state)}`:[^`]{0,160}/] || ''
  end

  def test_expensive_execution_recovery_classifies_exactly_four_states
    prose = compacted_skill
    %w[ACTIVE COMPLETE_VALID TERMINATED_INCOMPLETE UNRESOLVED].each do |state|
      assert_includes SKILL, "`#{state}`", "shared Skill dropped recovery state #{state}"
    end
    assert_match(
      /classify the prior execution as exactly one of `ACTIVE`, `COMPLETE_VALID`, `TERMINATED_INCOMPLETE`, or `UNRESOLVED`/,
      prose
    )
    assert_match(
      /Session, quota, shell, or handoff loss does not prove execution failure/,
      prose
    )
    assert_match(
      /Resolve the exact scope-qualified process\/writer and canonical durable output/,
      prose
    )
    assert_match(/File existence alone is not `COMPLETE_VALID`/, prose)
  end

  def test_no_duplicate_launch_while_active_or_unresolved
    prose = compacted_skill
    assert_match(/`ACTIVE`: wait, observe, or attach; never launch a duplicate/, prose)
    assert_match(/`UNRESOLVED`: stop rather than risk concurrent or duplicate execution/, prose)
    refute_match(/rerun is permitted/, recovery_clause('ACTIVE'))
    refute_match(/rerun is permitted/, recovery_clause('UNRESOLVED'))
    refute_match(/launch a duplicate/, recovery_clause('ACTIVE').sub(/never launch a duplicate/, ''))
  end

  def test_complete_valid_reuses_output_and_does_not_rerun
    prose = compacted_skill
    assert_match(/`COMPLETE_VALID`: reuse the existing output; do not rerun/, prose)
    refute_match(/rerun is permitted/, recovery_clause('COMPLETE_VALID'))
  end

  def test_rerun_is_permitted_only_on_terminated_incomplete
    assert_match(/`TERMINATED_INCOMPLETE`: rerun is permitted/, compacted_skill)
    %w[ACTIVE COMPLETE_VALID UNRESOLVED].each do |state|
      refute_match(/rerun is permitted/, recovery_clause(state),
                   "#{state} must not authorize rerun")
    end
  end

  def test_determinism_reuses_same_result_without_upstream_recomputation
    prose = compacted_skill
    assert_match(
      /When acceptance concerns serialization or artifact byte determinism, reuse the same already-computed semantic result/,
      prose
    )
    assert_match(/independently serialize or reconstruct the serialized bytes, and compare them/, prose)
    assert_match(
      /Do not rerun an expensive upstream computation solely for byte-level determinism/,
      prose
    )
  end

  def test_independent_replay_is_required_only_when_packet_makes_replay_acceptance
    prose = compacted_skill
    assert_match(
      /Independent algorithm\/process replay is required only when the Packet explicitly makes replay part of acceptance/,
      prose
    )
    assert_match(
      /Do not weaken existing `NOT RUN`, retry-ledger, RCA, Judge, or evidence-honesty rules/,
      prose
    )
  end
end
