# frozen_string_literal: true

require 'minitest/autorun'

class ResourceBudgetAndRcaContractTest < Minitest::Test
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
    assert_match(/CPU-heavy work uses the `SHARED_WORKSTATION` budget/, SKILL)
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
    assert_match(/fourth or later\nevidence-progressing step is permitted/, SKILL)
    assert_match(/no arbitrary numeric ceiling/, SKILL)
  end

  def test_identical_blind_retry_is_not_evidence_progress
    assert_match(/Identical blind retries and speculative patches are not evidence progress/, SKILL)
    assert_match(/must test a falsifiable hypothesis/, SKILL)
    assert_match(/materially reduce uncertainty/, SKILL)
  end

  def test_no_fixed_numeric_retry_terminal_rule_remains
    refute_match(/evidence-backed attempts\s*<\s*\d+/i, RCA_CONTRACT)
    refute_match(/BLOCKED_AFTER_[A-Z0-9]+_EVIDENCE_BACKED_ATTEMPTS/, RCA_CONTRACT)
    refute_match(/#{NUMBER_WORD}-attempt bound/i, RCA_CONTRACT)
    refute_match(/after (?:exactly )?#{NUMBER_WORD} evidence-backed (?:failed )?(?:attempts|failures),? stop/i,
                 RCA_CONTRACT)
    assert_match(/This is not unlimited retry permission/, SKILL)
    assert_match(/stop when scope, safety, authority, capability, proportionality, or\ndiscriminating evidence is exhausted/,
                 SKILL)
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
    assert_match(/verification, or material unknown evidence\./, SKILL)
    assert_match(
      /A single acceptance failure is not\na trigger at any attempt number; material unknown still applies\./,
      SKILL
    )
  end

  def test_judge_handoff_defers_to_skill_for_the_trigger_definition
    assert_match(/The Judge trigger has one definition, in `SKILL\.md` "Route once"/, JUDGE_HANDOFF)
    assert_match(/Do not restate or widen that list\nhere\./, JUDGE_HANDOFF)
    refute_match(/acceptance failure/i, JUDGE_HANDOFF,
                 'judge-handoff.md restates Judge-trigger semantics instead of deferring to SKILL.md')
  end
end
