# frozen_string_literal: true

require 'minitest/autorun'

class DonorCharacterizationProbeTest < Minitest::Test
  REPOSITORY_ROOT = File.expand_path('../..', __dir__)
  SKILL = File.read(File.join(REPOSITORY_ROOT, 'fable-method/shared/SKILL.md'), encoding: 'UTF-8')
  FALSIFIABILITY = File.read(
    File.join(REPOSITORY_ROOT, 'fable-method/shared/references/test-falsifiability.md'),
    encoding: 'UTF-8'
  )

  # The shipped rule is parsed, never restated, so weakening the contract text
  # moves these assertions instead of leaving them green against a local copy.
  RULE = (FALSIFIABILITY[/^## Legacy-donor characterization probe\s*\n(.*?)(?=^## |\z)/m, 1] || '')

  PRECONDITIONS = %w[cheap bounded safe dependency-feasible].freeze
  FALLBACK_TOKENS = %w[BLOCKED DISPROPORTIONATE UNSAFE DEPENDENCY_INFEASIBLE].freeze
  EXCLUDED_MANDATES = [
    'full donor replay',
    'exhaustive parameter sweep',
    'performance benchmark',
    'production mutation',
    'external spend',
    'broad historical reconstruction'
  ].freeze

  # A clause that permits SOURCE_ONLY without naming a genuine blocker lets a
  # cheap executable donor silently stay source-only.
  PERMISSIVE = /\b(?:accept\w*|allow\w*|permit\w*|remain\w*|stay\w*|suffic\w*|may|can)\b/i
  # A permission can also be phrased as a waived obligation.
  WAIVER = /\bno\s+(?:justification|limitation|blocker|reason)\b|\bwithout\s+(?:justification|limitation|a\s+blocker)\b/i

  def default_token
    RULE.include?('characterization probe by default') ? 'REQUIRED_BY_DEFAULT' : nil
  end

  def declared_preconditions
    PRECONDITIONS.select { |condition| RULE.include?(condition) }
  end

  def declared_fallbacks
    FALLBACK_TOKENS.select { |token| RULE.include?("`#{token}`") }
  end

  # Disposition derived from the shipped rule text rather than from a second
  # copy of the policy held in this test.
  def probe_disposition(cheap:, bounded:, safe:, dependency_feasible:, blocker: nil)
    token = default_token
    refute_nil token, 'shipped rule no longer declares a by-default probe disposition'
    assert_equal PRECONDITIONS.sort, declared_preconditions.sort,
                 'shipped rule no longer gates the default on all four preconditions'

    unless blocker.nil?
      assert_includes declared_fallbacks, blocker, "shipped rule does not recognise blocker #{blocker}"
      return 'SOURCE_ONLY'
    end

    return 'SOURCE_ONLY' unless [cheap, bounded, safe, dependency_feasible].all?

    token
  end

  def unconditioned_source_only_clauses(text)
    text.gsub(/\s+/, ' ').split(/(?<=\.)\s+/).flat_map { |sentence| sentence.split(/;\s*/) }
        .select { |clause| clause.include?('SOURCE_ONLY') }
        .select { |clause| PERMISSIVE.match?(clause) || WAIVER.match?(clause) }
        .reject { |clause| FALLBACK_TOKENS.any? { |token| clause.include?(token) } }
  end

  # --- A: cheap + bounded + safe + dependencies available ------------------

  def test_cheap_bounded_safe_feasible_donor_requires_a_probe_by_default
    assert_equal 'REQUIRED_BY_DEFAULT',
                 probe_disposition(cheap: true, bounded: true, safe: true, dependency_feasible: true)
    assert_match(/before frozen behavior semantics are finalized/i, RULE)
    assert_match(/one small executable characterization\s+probe/, RULE)
  end

  def test_default_applies_only_when_all_four_preconditions_hold
    PRECONDITIONS.each do |missing|
      inputs = {
        cheap: missing != 'cheap',
        bounded: missing != 'bounded',
        safe: missing != 'safe',
        dependency_feasible: missing != 'dependency-feasible'
      }
      assert_equal 'SOURCE_ONLY', probe_disposition(**inputs),
                   "probe was still required by default without #{missing}"
    end
    assert_match(/all four conditions must\s+hold/, RULE)
  end

  # --- B: execution technically impossible ---------------------------------

  def test_technically_impossible_execution_allows_source_only
    assert_equal 'SOURCE_ONLY',
                 probe_disposition(cheap: true, bounded: true, safe: true, dependency_feasible: false)
    assert_equal 'SOURCE_ONLY',
                 probe_disposition(cheap: true, bounded: true, safe: true, dependency_feasible: true,
                                   blocker: 'BLOCKED')
    assert_includes declared_fallbacks, 'DEPENDENCY_INFEASIBLE'
  end

  # --- C: execution disproportionate ---------------------------------------

  def test_disproportionate_execution_allows_source_only_with_reported_limitation
    assert_equal 'SOURCE_ONLY',
                 probe_disposition(cheap: true, bounded: true, safe: true, dependency_feasible: true,
                                   blocker: 'DISPROPORTIONATE')
    assert_equal FALLBACK_TOKENS.sort, declared_fallbacks.sort
    assert_match(/report the\s+limitation/, RULE)
  end

  # --- D: probe is not a full replay or benchmarking mandate ---------------

  def test_probe_is_characterization_not_benchmarking
    assert_match(/Run the minimum probe sufficient to test the load-bearing observed\s+behavior/, RULE)
    assert_match(/characterization,\s+not\s+benchmarking/, RULE)
    normalized_rule = RULE.gsub(/\s+/, ' ')
    EXCLUDED_MANDATES.each do |mandate|
      assert_includes normalized_rule, mandate, "shipped rule stopped excluding #{mandate}"
    end
    refute_match(/it never requires a full donor replay.*\bis required\b/, RULE)
  end

  def test_no_full_replay_mandate_anywhere_in_the_shared_contract
    [SKILL, FALSIFIABILITY].each do |contract|
      refute_match(/(?:full|complete|entire) donor replay is (?:required|mandatory)/i, contract)
      refute_match(/replay the (?:full|entire|complete) donor/i, contract)
      refute_match(/exhaustive (?:parameter )?sweep is (?:required|mandatory)/i, contract)
      refute_match(/benchmark the donor/i, contract)
    end
  end

  # --- E: donor provenance and frozen-semantics authority unchanged --------

  def test_rule_does_not_change_planner_owned_authority_or_status
    # Retired because Planner v5.5.1 no longer carries the two v5.4 fields.
    assert_match(/changes no Planner-owned authority mode, execution\s+status/, RULE)
  end

  def test_shared_contract_does_not_restate_a_second_donor_framework
    [SKILL, FALSIFIABILITY].each do |contract|
      refute_match(/CODE_FIRST_CONFLICT_TRIGGERED_PROVENANCE/, contract,
                   'shared contract redefines the Planner-owned donor authority mode')
      refute_match(/EXECUTABLE\s*\|\s*REVIVED\s*\|\s*SOURCE_ONLY/, contract,
                   'shared contract redefines the Planner-owned donor execution enum')
    end
    assert_equal 0, SKILL.scan(/^## Legacy-donor characterization probe$/).length,
                 'SKILL.md carries the relocated donor characterization rule'
    assert_equal 1, FALSIFIABILITY.scan(/^## Legacy-donor characterization probe$/).length,
                 'test-falsifiability.md must carry exactly one donor characterization rule'
  end

  # --- Falsifiability: silent SOURCE_ONLY must not be reachable ------------

  def test_silent_source_only_detector_flags_known_weakenings
    [
      'SOURCE_ONLY stays acceptable for any legacy donor.',
      'A cheap executable donor may remain SOURCE_ONLY.',
      'SOURCE_ONLY is always allowed when reading the donor source is easier.',
      'Source-only characterization is sufficient; SOURCE_ONLY needs no justification.'
    ].each do |weakening|
      refute_empty unconditioned_source_only_clauses(weakening),
                   "detector missed a silent SOURCE_ONLY permission: #{weakening}"
    end
  end

  def test_silent_source_only_detector_permits_the_shipped_rule
    [RULE, SKILL].each do |contract|
      assert_empty unconditioned_source_only_clauses(contract),
                   'shared contract permits SOURCE_ONLY without naming a genuine blocker'
    end
  end
end
