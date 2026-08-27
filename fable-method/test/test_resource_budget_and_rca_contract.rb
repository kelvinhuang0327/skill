# frozen_string_literal: true

require 'minitest/autorun'

class ResourceBudgetAndRcaContractTest < Minitest::Test
  SHARED_ROOT = File.expand_path('../shared', __dir__)
  SKILL = File.read(File.join(SHARED_ROOT, 'SKILL.md'))
  OPERATIONAL_GATES = File.read(File.join(SHARED_ROOT, 'references', 'operational-gates.md'))
  FLOWCHARTS = File.read(File.join(SHARED_ROOT, 'references', 'flowcharts.md'))
  FAILURE_MODES = File.read(File.join(SHARED_ROOT, 'references', 'failure-modes.md'))
  RCA_CONTRACT = [SKILL, FLOWCHARTS, FAILURE_MODES].join("\n")

  NUMBER_WORD = '(?:one|two|three|four|five|six|seven|eight|nine|ten|\\d+)'

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
end
