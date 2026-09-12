# frozen_string_literal: true
require 'minitest/autorun'
require_relative '../../fable-method/scripts/platform_manifest'

class JudgeContractTest < Minitest::Test
  ROOT = File.expand_path('../..', __dir__)
  Model = Fable::PlatformManifest
  VERDICTS = %w[VERIFIED VERIFIED_WITH_CAVEATS REFUTED BLOCKED_UNVERIFIABLE SELF_CHECK_ONLY].freeze

  def setup
    @source = File.read(File.join(ROOT, 'fable-judge/shared/SKILL.md'))
    @model = Model.load(File.join(ROOT, 'fable-method/platforms.yaml'))
  end

  # Contract validation only: prose gates are falsified below, never used as
  # evidence that a language model will obey the contract in every situation.
  def contract_errors(source)
    text = source.gsub(/\s+/, ' ')
    required = [
      'independent adversarial verifier, not a Planner, implementer, or repair agent',
      'Never modify product source or product tests, repair the candidate',
      'mutate Git lifecycle, silently expand scope',
      'A continuation of that context yields SELF_CHECK_ONLY and cannot satisfy an independent Judge gate',
      'Before choosing or reconciling depth, reusing evidence, or reviewing remediation, read and apply the complete [Judge depth contract](references/judge-depth-contract.md)',
      'the sole semantic authority for depth, evidence reuse, reconciliation, and remediation limits',
      'A supplied depth cannot lower the canonical required depth',
      'valid reusable exact-tree evidence or the minimum independent reproduction',
      'NOT RUN, never VERIFIED',
      'BOUNDED and DELTA do not automatically rerun the complete suite',
      'severity: BLOCKING | NON_BLOCKING',
      'FINDING_CONFIDENCE: CONFIRMED | SUSPECTED',
      'A SUSPECTED finding also requires:',
      'DISCRIMINATING_CHECK: observation: supports_if: refutes_if: not_run_reason:',
      'Without a discriminating observation, the concern is SPECULATION and must not be reported as a finding',
      'Report all material CONFIRMED findings. There is no fixed count cap',
      'Missing required evidence cannot become VERIFIED_WITH_CAVEATS',
      'SELF_CHECK_ONLY cannot become independent VERIFIED',
      'one bounded remediation cycle'
    ]
    errors = required.reject { |phrase| text.include?(phrase) }
    actual = source.scan(/^- ([A-Z_]+):/).flatten
    errors << 'verdict vocabulary' unless actual == VERDICTS
    errors << 'bare unverifiable alias' if source.match?(/(?<![A-Z_])UNVERIFIABLE(?![A-Z_])/)
    errors << 'renamed severity field' if source.include?('FINDING_IMPACT')
    %w[finding_id claim criterion_or_rule evidence expected observed].each do |field|
      errors << field unless source.match?(/^#{field}:$/)
    end
    errors
  end

  def test_canonical_role_verdict_evidence_and_finding_contract
    assert_empty contract_errors(@source)
  end

  def test_removed_suspected_gate_is_detected
    mutation = @source.sub(/Without a discriminating observation,.*?reported as a finding\./m, '')
    refute_equal @source, mutation
    refute_empty contract_errors(mutation)
    assert_empty contract_errors(@source)
  end

  def test_weakened_mandatory_depth_reuse_link_is_detected
    mutation = @source.sub('read and apply the complete', 'optionally skim the')
    refute_equal @source, mutation
    refute_empty contract_errors(mutation)
    mutation = @source.sub('(references/judge-depth-contract.md)', '(references/other.md)')
    refute_empty contract_errors(mutation)
    assert_empty contract_errors(@source)
  end

  def test_all_four_platforms_have_identical_body_and_exact_depth_projection
    bodies = []
    depth_source = File.binread(File.join(ROOT, Model::DEPTH_SOURCE))
    projected = Model.project(depth_source, Model::DEPTH_SECTIONS)
    %w[codex claude gemini antigravity].each do |platform|
      bundle = @model.render(ROOT, 'fable-judge', platform)
      bodies << bundle.fetch('SKILL.md').sub(/\A---\n.*?\n---\n/m, '')
      assert_empty contract_errors(bundle.fetch('SKILL.md'))
      assert_equal projected, bundle.fetch(Model::DEPTH_DESTINATION)
      bundle.each do |path, bytes|
        destination = @model.pair('fable-judge', platform)['materialized_destination']
        assert_equal bytes, File.binread(File.join(ROOT, destination, path))
      end
    end
    assert_equal 1, bodies.uniq.length
    assert_includes projected, 'A supplied depth never lowers a fired trigger'
    assert_includes projected, 'Reuse evidence only when command, environment, HEAD, and tree are identical'
    assert_includes projected, 'Allow at most one bounded remediation'
  end

  def test_judge_has_no_handwritten_second_trigger_list_or_extra_platform
    refute_includes @source, 'Subject-matter triggers'
    refute_includes @source, 'Workload-shape triggers'
    assert_equal %w[antigravity claude codex gemini], @model.platforms('fable-judge').map { |p| p['name'] }.sort
    @model.platforms('fable-judge').each do |p|
      assert_empty p['adapter_sources']
      assert_empty p['reference_overrides']
    end
  end
end
