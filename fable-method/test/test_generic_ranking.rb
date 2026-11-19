# frozen_string_literal: true

require 'minitest/autorun'
require 'bigdecimal'
require 'json'
require 'open3'
require 'rbconfig'
require_relative '../scripts/generic_ranking'

# Focused coverage of the load-bearing generic ranking contract. Every fixture
# supplies its own metric, direction, weight and universe: the engine is only
# ever allowed to execute a comparison the caller stated explicitly.
class GenericRankingTest < Minitest::Test
  SCRIPT = File.expand_path('../scripts/generic_ranking.rb', __dir__)
  UNIVERSE = { 'population' => 'cohort-1', 'evaluation_window' => '2024-01-01..2024-12-31' }.freeze

  def metric(value, evidence: nil, provenance: nil, state: 'VALUE_PRESENT')
    observation = { 'state' => state, 'evidence' => evidence || ['ev:default'] }
    observation['value'] = value unless value.nil?
    observation['provenance'] = provenance if provenance
    observation
  end

  def candidate(id, accuracy: '0.5', latency: '100', eligibility: 'ELIGIBLE', universe: UNIVERSE)
    {
      'candidate_id' => id, 'eligibility' => eligibility, 'universe' => universe,
      'metrics' => {
        'accuracy' => accuracy.is_a?(Hash) ? accuracy : metric(accuracy, evidence: ["ev:#{id}:accuracy"]),
        'latency' => latency.is_a?(Hash) ? latency : metric(latency, evidence: ["ev:#{id}:latency"])
      }
    }
  end

  def lexicographic(dimensions: nil, tie_break: nil)
    {
      'method' => 'LEXICOGRAPHIC', 'universe' => UNIVERSE,
      'dimensions' => dimensions || [
        { 'id' => 'accuracy', 'direction' => 'HIGHER_IS_BETTER' },
        { 'id' => 'latency', 'direction' => 'LOWER_IS_BETTER' }
      ],
      'tie_break' => tie_break || [{ 'field' => 'candidate_id', 'direction' => 'ASCENDING' }]
    }
  end

  def weighted(tie_break: nil, accuracy_weight: '0.5', latency_weight: '0.5', latency_max: '100')
    {
      'method' => 'WEIGHTED', 'universe' => UNIVERSE,
      'dimensions' => [
        { 'id' => 'accuracy', 'direction' => 'HIGHER_IS_BETTER', 'weight' => accuracy_weight,
          'normalization' => { 'min' => '0', 'max' => '1' } },
        { 'id' => 'latency', 'direction' => 'LOWER_IS_BETTER', 'weight' => latency_weight,
          'normalization' => { 'min' => '0', 'max' => latency_max } }
      ],
      'tie_break' => tie_break || [{ 'field' => 'candidate_id', 'direction' => 'ASCENDING' }]
    }
  end

  def ids(result)
    result['ordered'].map { |row| row['candidate']['candidate_id'] }
  end

  # 1. Determinism: input order must not influence the emitted ranking.
  def test_ranking_is_deterministic_across_input_permutations
    contract = lexicographic
    candidates = [
      candidate('c', accuracy: '0.90', latency: '10'),
      candidate('a', accuracy: '0.70', latency: '20'),
      candidate('b', accuracy: '0.90', latency: '30')
    ]
    baseline = JSON.generate(Fable::Ranking.rank(candidates, contract))
    candidates.permutation.each do |permuted|
      assert_equal baseline, JSON.generate(Fable::Ranking.rank(permuted, contract)),
                   "permutation #{permuted.map { |c| c['candidate_id'] }.join(',')} changed the result"
    end
    assert_equal %w[c b a], ids(JSON.parse(baseline))
  end

  # 2. HIGHER_IS_BETTER.
  def test_higher_is_better_puts_the_larger_value_first
    contract = lexicographic(dimensions: [{ 'id' => 'accuracy', 'direction' => 'HIGHER_IS_BETTER' }])
    result = Fable::Ranking.rank(
      [candidate('low', accuracy: '0.10'), candidate('high', accuracy: '0.90')], contract
    )
    assert_equal %w[high low], ids(result)
  end

  # 3. LOWER_IS_BETTER on the same raw values must invert the order.
  def test_lower_is_better_puts_the_smaller_value_first
    contract = lexicographic(dimensions: [{ 'id' => 'accuracy', 'direction' => 'LOWER_IS_BETTER' }])
    result = Fable::Ranking.rank(
      [candidate('low', accuracy: '0.10'), candidate('high', accuracy: '0.90')], contract
    )
    assert_equal %w[low high], ids(result)
  end

  # Direction is never guessed.
  def test_dimension_without_direction_is_rejected
    error = assert_raises(Fable::Ranking::InvalidInput) do
      Fable::Ranking.rank([candidate('a')], lexicographic(dimensions: [{ 'id' => 'accuracy' }]))
    end
    assert_match(/direction/, error.message)
  end

  # 4. Ineligible candidates never enter the ranked list.
  def test_ineligible_candidate_is_excluded_from_the_ranking
    result = Fable::Ranking.rank(
      [candidate('ok', accuracy: '0.10'), candidate('banned', accuracy: '0.99', eligibility: 'INELIGIBLE')],
      lexicographic
    )
    assert_equal %w[ok], ids(result)
    assert_equal %w[banned], result['ineligible'].map { |row| row['candidate']['candidate_id'] }
    # Ineligibility is the caller's own declaration, so the ranking of the eligible
    # set is complete. PARTIAL is reserved for candidates the engine could not place.
    assert_equal 'RANKED', result['status']
    assert_equal 'PARTIAL', Fable::Ranking.rank(
      [candidate('ok'), candidate('pending', eligibility: 'UNRESOLVED')], lexicographic
    )['status']
  end

  # 5. Unresolved eligibility is held back rather than assumed eligible - including
  # when the caller omits the field entirely.
  def test_unresolved_eligibility_is_held_back
    omitted = candidate('omitted', accuracy: '0.99')
    omitted.delete('eligibility')
    result = Fable::Ranking.rank(
      [candidate('ok', accuracy: '0.10'), candidate('pending', accuracy: '0.99', eligibility: 'UNRESOLVED'), omitted],
      lexicographic
    )
    assert_equal %w[ok], ids(result)
    assert_equal %w[omitted pending], result['unresolved'].map { |row| row['candidate']['candidate_id'] }.sort
  end

  # 6. A missing value must not silently become 0. Under LOWER_IS_BETTER a
  # zero-filled latency would rank first, so absence of that promotion is the proof.
  def test_missing_value_does_not_become_zero
    contract = lexicographic(dimensions: [{ 'id' => 'latency', 'direction' => 'LOWER_IS_BETTER' }])
    blank = candidate('blank', latency: metric(nil, state: 'MISSING', evidence: []))
    result = Fable::Ranking.rank([candidate('present', latency: '50'), blank], contract)

    assert_equal %w[present], ids(result)
    refute_includes ids(result), 'blank', 'a missing metric was ranked as if it were zero'
    assert_equal %w[blank], result['not_comparable'].map { |row| row['candidate']['candidate_id'] }
    issue = result['not_comparable'][0]['issues'].find { |i| i['code'] == 'MISSING_REQUIRED_VALUE' }
    assert_equal 'MISSING', issue['state']
    trace = result['not_comparable'][0]['dimensions'].find { |d| d['id'] == 'latency' }
    assert_nil trace['value']
  end

  def test_unobservable_and_not_applicable_are_distinguished_from_missing
    contract = lexicographic(dimensions: [{ 'id' => 'latency', 'direction' => 'LOWER_IS_BETTER' }])
    %w[UNOBSERVABLE NOT_APPLICABLE].each do |state|
      result = Fable::Ranking.rank(
        [candidate('x', latency: metric(nil, state: state, evidence: []))], contract
      )
      issue = result['not_comparable'][0]['issues'].find { |i| i['code'] == 'MISSING_REQUIRED_VALUE' }
      assert_equal state, issue['state']
      assert_equal state, result['not_comparable'][0]['dimensions'][0]['state']
    end
  end

  def test_non_present_state_may_not_carry_a_value
    contract = lexicographic(dimensions: [{ 'id' => 'latency', 'direction' => 'LOWER_IS_BETTER' }])
    error = assert_raises(Fable::Ranking::InvalidInput) do
      Fable::Ranking.rank([candidate('x', latency: metric('0', state: 'MISSING'))], contract)
    end
    assert_match(/cannot carry a value/, error.message)
  end

  # 7. A different population is not silently pooled into one ranking.
  def test_different_population_is_not_comparable
    other = UNIVERSE.merge('population' => 'cohort-2')
    result = Fable::Ranking.rank(
      [candidate('same', accuracy: '0.10'), candidate('other', accuracy: '0.99', universe: other)],
      lexicographic
    )
    assert_equal %w[same], ids(result)
    row = result['not_comparable'][0]
    assert_equal 'other', row['candidate']['candidate_id']
    assert_equal 'UNIVERSE_MISMATCH', row['issues'][0]['code']
    assert_equal other, row['issues'][0]['actual']
  end

  # 8. Same population, different evaluation window - still not comparable.
  def test_different_evaluation_window_is_not_comparable
    other = UNIVERSE.merge('evaluation_window' => '2023-01-01..2023-12-31')
    result = Fable::Ranking.rank(
      [candidate('same', accuracy: '0.10'), candidate('stale', accuracy: '0.99', universe: other)],
      lexicographic
    )
    assert_equal %w[same], ids(result)
    assert_equal 'UNIVERSE_MISMATCH', result['not_comparable'][0]['issues'][0]['code']
  end

  def test_candidate_without_a_resolved_universe_is_not_comparable
    result = Fable::Ranking.rank(
      [candidate('same'), candidate('vague', universe: { 'population' => 'cohort-1' })], lexicographic
    )
    assert_equal 'UNRESOLVED_UNIVERSE', result['not_comparable'][0]['issues'][0]['code']
  end

  # 9. Weighted comparison uses only caller-supplied weights.
  def test_explicit_weighted_comparison
    contract = weighted(accuracy_weight: '0.7', latency_weight: '0.3')
    # a: 0.72*0.7 + (1-0.90)*0.3 = 0.534 ; b: 0.70*0.7 + (1-0.20)*0.3 = 0.730
    result = Fable::Ranking.rank(
      [candidate('a', accuracy: '0.72', latency: '90'), candidate('b', accuracy: '0.70', latency: '20')],
      contract
    )
    assert_equal %w[b a], ids(result)
    assert_equal '0.73', result['ordered'][0]['score']
    assert_equal '0.534', result['ordered'][1]['score']
    assert_equal 0.73, result['ordered'][0]['score'].to_f, 'score must survive a plain float parse'
    assert_equal '73/100', result['ordered'][0]['score_exact']
  end

  def test_weighted_method_requires_explicit_weight_and_normalization
    contract = weighted
    contract['dimensions'][0].delete('weight')
    assert_raises(Fable::Ranking::InvalidInput) { Fable::Ranking.rank([candidate('a')], contract) }

    contract = weighted
    contract['dimensions'][0].delete('normalization')
    assert_raises(Fable::Ranking::InvalidInput) { Fable::Ranking.rank([candidate('a')], contract) }
  end

  def test_weights_are_rejected_when_the_method_is_not_weighted
    contract = lexicographic
    contract['dimensions'][0]['weight'] = '0.9'
    error = assert_raises(Fable::Ranking::InvalidInput) { Fable::Ranking.rank([candidate('a')], contract) }
    assert_match(/require method WEIGHTED/, error.message)
  end

  # 10. Normalization is an explicit, declared contract - values outside the
  # declared bounds are refused instead of being rescaled to the observed cohort.
  def test_normalization_contract_is_explicit_and_bounded
    contract = weighted(latency_max: '100')
    result = Fable::Ranking.rank(
      [candidate('inside', accuracy: '0.5', latency: '50'), candidate('outside', accuracy: '0.5', latency: '150')],
      contract
    )
    assert_equal %w[inside], ids(result)
    assert_equal 'OUT_OF_NORMALIZATION_RANGE', result['not_comparable'][0]['issues'][0]['code']

    trace = result['ordered'][0]['dimensions'].find { |d| d['id'] == 'latency' }
    assert_equal({ 'min' => '0', 'max' => '100' }, trace['normalization'])
    assert_equal '0.5', trace['normalized_value']   # LOWER_IS_BETTER inverts 50/100
    assert_equal '0.25', trace['weighted_contribution']

    # The same raw value under different declared bounds must normalize differently.
    wider = Fable::Ranking.rank([candidate('inside', accuracy: '0.5', latency: '50')], weighted(latency_max: '200'))
    assert_equal '0.75', wider['ordered'][0]['dimensions'].find { |d| d['id'] == 'latency' }['normalized_value']
  end

  def test_normalization_requires_min_below_max
    contract = weighted
    contract['dimensions'][0]['normalization'] = { 'min' => '1', 'max' => '1' }
    assert_raises(Fable::Ranking::InvalidInput) { Fable::Ranking.rank([candidate('a')], contract) }
  end

  # 11. A semantic tie shares a rank; the deterministic order is reported separately.
  def test_semantic_tie_shares_a_rank_while_positions_stay_distinct
    result = Fable::Ranking.rank(
      [candidate('b', accuracy: '0.50', latency: '10'), candidate('a', accuracy: '0.50', latency: '10'),
       candidate('c', accuracy: '0.10', latency: '10')],
      lexicographic
    )
    assert_equal %w[a b c], ids(result)
    assert_equal [1, 1, 3], result['ordered'].map { |row| row['rank'] }
    assert_equal [1, 2, 3], result['ordered'].map { |row| row['position'] }
    assert_equal [{ 'rank' => 1, 'candidate_ids' => %w[a b] }], result['ties']

    decision = result['ordered'][0]['comparison_to_next']
    assert_equal true, decision['primary_tie'], 'a and b are semantically tied'
    assert_equal 'TIE_BREAK', decision['reason']
  end

  # 12. Tie-break rules are explicit, ordered and reproducible - no randomness.
  def test_deterministic_tie_break_applies_the_declared_secondary_rule
    # Both score 0.6, so only the declared latency tie-break can separate them.
    contract = weighted(tie_break: [{ 'dimension' => 'latency' }, { 'field' => 'candidate_id', 'direction' => 'ASCENDING' }])
    candidates = [candidate('a', accuracy: '0.8', latency: '60'), candidate('b', accuracy: '0.6', latency: '40')]
    result = Fable::Ranking.rank(candidates, contract)

    assert_equal %w[0.6 0.6], result['ordered'].map { |row| row['score'] }
    assert_equal %w[b a], ids(result), 'the declared latency tie-break must decide'
    assert_equal [1, 1], result['ordered'].map { |row| row['rank'] }

    applied = result['ordered'][0]['comparison_to_next']['tie_break']
    assert_equal({ 'dimension' => 'latency' }, applied[0]['rule'])
    assert_equal '40', applied[0]['a_value']
    assert_equal '60', applied[0]['b_value']

    # Reversing the final candidate_id rule flips an otherwise total tie.
    ascending = lexicographic(tie_break: [{ 'field' => 'candidate_id', 'direction' => 'ASCENDING' }])
    descending = lexicographic(tie_break: [{ 'field' => 'candidate_id', 'direction' => 'DESCENDING' }])
    identical = [candidate('a'), candidate('b')]
    assert_equal %w[a b], ids(Fable::Ranking.rank(identical, ascending))
    assert_equal %w[b a], ids(Fable::Ranking.rank(identical, descending))
    10.times { assert_equal %w[b a], ids(Fable::Ranking.rank(identical, descending)) }
  end

  def test_tie_break_must_end_with_candidate_id
    error = assert_raises(Fable::Ranking::InvalidInput) do
      Fable::Ranking.rank([candidate('a')], lexicographic(tie_break: [{ 'dimension' => 'latency' }]))
    end
    assert_match(/candidate_id/, error.message)
  end

  # 13. The stated explanation must match the comparison that actually happened.
  def test_explanation_matches_the_actual_decision
    contract = lexicographic
    # accuracy ties, so latency (LOWER_IS_BETTER) is genuinely decisive.
    fast = candidate('fast', accuracy: '0.80', latency: '10')
    slow = candidate('slow', accuracy: '0.80', latency: '90')
    decision = Fable::Ranking.compare(fast, slow, contract)

    assert_equal 'COMPARABLE', decision['status']
    assert_equal 'A_BEFORE_B', decision['relation']
    assert_equal 'DIMENSION', decision['reason']
    assert_equal 'latency', decision['decisive_dimension']
    assert_equal %w[accuracy], decision['tied_dimensions']

    decisive = decision['dimensions'].find { |d| d['id'] == 'latency' }
    assert_equal 'LOWER_IS_BETTER', decisive['direction']
    assert_equal '10', decisive['a_value']
    assert_equal '90', decisive['b_value']
    assert_equal(-1, decisive['order'])
    assert_empty decision['tie_break'], 'no tie-break was needed'

    # The claimed decisive dimension really is the one that drives the order:
    # flipping only that dimension flips the result.
    flipped = Fable::Ranking.compare(candidate('fast', accuracy: '0.80', latency: '99'), slow, contract)
    assert_equal 'B_BEFORE_A', flipped['relation']
    assert_equal 'latency', flipped['decisive_dimension']

    assert_equal %w[fast slow], ids(Fable::Ranking.rank([slow, fast], contract))
  end

  def test_explanation_reports_eligibility_when_that_is_the_reason
    decision = Fable::Ranking.compare(
      candidate('a'), candidate('b', eligibility: 'INELIGIBLE'), lexicographic
    )
    assert_equal 'NOT_COMPARABLE', decision['status']
    assert_equal 'ELIGIBILITY', decision['reason']
    assert_nil decision['order']
    assert_equal 'INELIGIBLE', decision['eligibility']['b']
  end

  # 14. Evidence and provenance survive ranking, for ranked and rejected candidates alike.
  def test_evidence_and_provenance_survive_ranking
    provenance = { 'source' => 'replay-log', 'digest' => 'sha256:abc' }
    winner = candidate(
      'winner',
      accuracy: metric('0.90', evidence: ['ev:winner:accuracy', { 'ref' => 'run/42', 'note' => 'primary' }],
                       provenance: provenance),
      latency: metric('10', evidence: ['ev:winner:latency'])
    )
    rejected = candidate('rejected', accuracy: '0.10', eligibility: 'INELIGIBLE')
    result = Fable::Ranking.rank([winner, rejected], lexicographic)

    trace = result['ordered'][0]['dimensions'].find { |d| d['id'] == 'accuracy' }
    assert_equal ['ev:winner:accuracy', { 'ref' => 'run/42', 'note' => 'primary' }], trace['evidence']
    assert_equal provenance, trace['provenance']
    assert_equal ['ev:winner:latency'],
                 result['ordered'][0]['dimensions'].find { |d| d['id'] == 'latency' }['evidence']

    # Rejection must not strip the evidence trail either.
    assert_equal ['ev:rejected:accuracy'],
                 result['ineligible'][0]['candidate']['metrics']['accuracy']['evidence']
  end

  def test_value_without_evidence_fails_safe_instead_of_ranking
    naked = candidate('naked', accuracy: metric('0.99', evidence: []))
    result = Fable::Ranking.rank([candidate('sourced', accuracy: '0.10'), naked], lexicographic)
    assert_equal %w[sourced], ids(result)
    assert_equal 'MISSING_EVIDENCE', result['not_comparable'][0]['issues'][0]['code']
  end

  def test_caller_input_is_not_mutated
    contract = lexicographic
    candidates = [candidate('a'), candidate('b')]
    before = JSON.generate([candidates, contract])
    Fable::Ranking.rank(candidates, contract)
    Fable::Ranking.compare(candidates[0], candidates[1], contract)
    assert_equal before, JSON.generate([candidates, contract])
  end

  def test_duplicate_candidate_ids_are_rejected
    assert_raises(Fable::Ranking::InvalidInput) do
      Fable::Ranking.rank([candidate('a', accuracy: '0.1'), candidate('a', accuracy: '0.2')], lexicographic)
    end
  end

  def test_empty_candidate_list_is_reported_as_empty
    result = Fable::Ranking.rank([], lexicographic)
    assert_equal 'EMPTY', result['status']
    assert_empty result['ordered']
  end

  def test_cli_rank_and_compare_round_trip
    ruby = RbConfig.ruby
    payload = JSON.generate(
      'contract' => lexicographic,
      'candidates' => [candidate('a', accuracy: '0.10'), candidate('b', accuracy: '0.90')]
    )
    out, _err, status = Open3.capture3(ruby, SCRIPT, 'rank', stdin_data: payload)
    assert_equal 0, status.exitstatus
    assert_equal %w[b a], ids(JSON.parse(out))

    out, _err, status = Open3.capture3(ruby, SCRIPT, 'compare', stdin_data: payload)
    assert_equal 0, status.exitstatus
    assert_equal 'B_BEFORE_A', JSON.parse(out)['relation']

    _out, err, status = Open3.capture3(ruby, SCRIPT, 'rank', stdin_data: '{"contract":{}}')
    assert_equal 2, status.exitstatus
    assert_equal 'INVALID_INPUT', JSON.parse(err)['error']
  end

  def test_cli_preserves_distinct_json_decimal_scores
    # These unquoted JSON numbers are distinct, but both round to the same Float.
    smaller = '0.1'
    larger = '1.0000000000000001e-1'
    payload = JSON.generate(
      'contract' => weighted(accuracy_weight: 1, latency_weight: 0),
      'candidates' => [candidate('a', accuracy: 'small'), candidate('z', accuracy: 'large')]
    ).sub('"small"', smaller).sub('"large"', larger)

    out, err, status = Open3.capture3(RbConfig.ruby, SCRIPT, 'rank', stdin_data: payload)
    assert status.success?, err
    ranking = JSON.parse(out, decimal_class: BigDecimal)
    assert_equal %w[z a], ids(ranking)
    assert_empty ranking['ties']
    winner = ranking['ordered'].first
    assert_equal Rational(larger).to_s, winner['score_exact']
    assert_equal BigDecimal(larger), winner['candidate']['metrics']['accuracy']['value']

    out, err, status = Open3.capture3(RbConfig.ruby, SCRIPT, 'compare', stdin_data: payload)
    assert status.success?, err
    comparison = JSON.parse(out)
    assert_equal 'B_BEFORE_A', comparison['relation']
    assert_equal 'WEIGHTED_SCORE', comparison['reason']
  end

  # Reading the output with a token recorder checks both the spelling and JSON
  # type. A Float or BigDecimal parser would itself lose this regression.
  JsonNumberToken = Struct.new(:literal)
  TINY = '1e-8192'

  def invoke_cli_decimals(action, contract, candidates, tokens)
    payload = JSON.generate('contract' => contract, 'candidates' => candidates)
    tokens.each { |marker, literal| payload = payload.gsub(JSON.generate(marker), literal) }
    Open3.capture3(RbConfig.ruby, SCRIPT, action, stdin_data: payload)
  end

  def cli_decimals(action, contract, candidates, tokens)
    out, err, status = invoke_cli_decimals(action, contract, candidates, tokens)
    assert status.success?, err
    JSON.parse(out, decimal_class: JsonNumberToken)
  end

  def zero_and_tiny(action)
    contract = weighted
    contract['dimensions'] = contract['dimensions'].take(1)
    cli_decimals(action, contract, [candidate('a', accuracy: 0), candidate('z', accuracy: 'tiny')],
                 'tiny' => TINY)
  end

  def test_cli_rank_preserves_underflowing_decimal
    ranking = zero_and_tiny('rank')
    assert_equal %w[z a], ids(ranking)
    assert_empty ranking['ties']
    winner = ranking['ordered'].first
    assert_equal JsonNumberToken.new(TINY), winner.dig('candidate', 'metrics', 'accuracy', 'value')
    assert_equal JsonNumberToken.new(TINY), winner.dig('dimensions', 0, 'value')
    assert_equal Rational(TINY).to_s, winner['score_exact']
  end

  def test_cli_compare_preserves_underflowing_decimal
    decision = zero_and_tiny('compare')
    assert_equal 'B_BEFORE_A', decision['relation']
    assert_equal 'WEIGHTED_SCORE', decision['reason']
    assert_equal false, decision['primary_tie']
    assert_empty decision['tie_break']
    assert_equal JsonNumberToken.new(TINY), decision.dig('dimensions', 0, 'b_value')
    assert_equal Rational(TINY).to_s, decision.dig('scores', 'b_exact')
  end

  def test_cli_lower_is_better_score_exact_is_a_reduced_fraction
    contract = weighted(latency_weight: 1, latency_max: 1)
    contract['dimensions'] = [contract['dimensions'].last]
    rows = [candidate('a', latency: 'nearby'), candidate('b', latency: 'tiny'), candidate('z', latency: 0)]
    ranking = cli_decimals('rank', contract, rows, 'tiny' => '1e-4097', 'nearby' => '1.0000000000000001e-4097')
    assert_equal %w[z b a], ids(ranking)
    assert_empty ranking['ties']
    row = ranking['ordered'][1]
    assert_equal JsonNumberToken.new('1e-4097'), row.dig('candidate', 'metrics', 'latency', 'value')
    exact = row.fetch('score_exact')
    assert_match(%r{\A\d+/\d+\z}, exact)
    denominator = 10**4097
    expected = Rational(denominator - 1, denominator)
    assert_equal expected, Rational(exact)
    assert_equal expected.to_s, exact
    assert_equal exact, ranking['ordered'].first.dig('comparison_to_next', 'scores', 'b_exact')
  end

  def test_cli_rejects_unsupported_decimal_exponents_and_scales
    rows = [candidate('a', accuracy: 0), candidate('z', accuracy: 'outside')]
    tokens = %w[1e-9999999999999999999999 1e+9999999999999999999999 0e8193 1.1e-8192]
    tokens << "0.#{'0' * 8192}1"
    %w[rank compare].each do |action|
      tokens.each do |literal|
        out, err, status = invoke_cli_decimals(action, weighted, rows, 'outside' => literal)
        assert_equal 2, status.exitstatus
        assert_empty out
        error = JSON.parse(err)
        assert_equal 'INVALID_INPUT', error['error']
        assert_match(/exponent or scale.*8192/, error['message'])
      end
    end
  end

  def test_cli_orders_supported_signed_exponents_and_ties_equivalent_spellings
    power = 8192
    values = {
      'large' => 'large_token', 'equal' => "+10.10e#{power - 1}", 'higher' => 'higher_token',
      'tiny' => 'tiny_token', 'zero' => 'zero_token', 'integer_zero' => 0,
      'negative_tiny' => "-#{TINY}", 'negative_large' => 'negative_token'
    }
    tokens = { 'large_token' => "1.01E+#{power}", 'higher_token' => "1.011e#{power}",
               'zero_token' => "-0.00e#{power}", 'tiny_token' => TINY, 'negative_token' => "-1e#{power}" }
    contract = lexicographic(dimensions: [{ 'id' => 'accuracy', 'direction' => 'HIGHER_IS_BETTER' }])
    rows = values.map { |id, value| candidate(id, accuracy: value) }
    ranking = cli_decimals('rank', contract, rows, tokens)
    assert_equal %w[higher equal large tiny integer_zero zero negative_tiny negative_large], ids(ranking)
    assert_equal [%w[equal large], %w[integer_zero zero]], ranking['ties'].map { |tie| tie['candidate_ids'] }
    ranking['ordered'].each do |row|
      input = values.fetch(row.dig('candidate', 'candidate_id'))
      expected = tokens.key?(input) ? JsonNumberToken.new(tokens[input]) : input
      assert_equal expected, row.dig('candidate', 'metrics', 'accuracy', 'value')
    end
  end

  def test_equivalent_json_numbers_preserve_candidate_and_universe_identity
    contract = weighted(accuracy_weight: 1, latency_weight: 0)
    contract['universe'] = UNIVERSE.merge('version' => 1)
    plain = candidate('same', accuracy: 'short', universe: contract['universe'])
    padded = candidate('same', accuracy: 'long', universe: UNIVERSE.merge('version' => 'version_token'))
    long = "0.1#{'0' * 5000}"
    result = cli_decimals('compare', contract, [plain, padded],
                          'short' => '1e-1', 'long' => long, 'version_token' => '1.00')
    assert_equal 'TIE', result['relation']
    assert_equal '1/10', result.dig('scores', 'b_exact')
    assert_equal JsonNumberToken.new(long), result.dig('dimensions', 0, 'b_value')
  end

  def test_weighted_contributions_cancel_exactly_and_round_at_half_units
    contract = weighted(latency_max: 1)
    rows = [candidate('a', accuracy: 0, latency: 0), candidate('y', accuracy: 'tiny', latency: 'tiny'),
            candidate('z', accuracy: 'tiny', latency: 0)]
    ranking = cli_decimals('rank', contract, rows, 'tiny' => TINY)
    assert_equal %w[z a y], ids(ranking)
    assert_equal [%w[a y]], ranking['ties'].map { |tie| tie['candidate_ids'] }
    assert_equal ['0.5'] * 3, ranking['ordered'].map { |row| row['score'] }
    assert_equal [((1 + Rational(TINY)) / 2).to_s, '1/2', '1/2'], ranking['ordered'].map { |row| row['score_exact'] }

    rows = [candidate('a', accuracy: '1e-12', latency: 'tiny'), candidate('b', accuracy: '1e-12', latency: 0)]
    decision = cli_decimals('compare', contract, rows, 'tiny' => TINY)
    assert_equal 'B_BEFORE_A', decision['relation']
    assert_equal '0.5', decision.dig('scores', 'a')
    assert_equal '0.500000000001', decision.dig('scores', 'b')
  end

  def test_weighted_order_with_huge_weight_and_normalization_span
    # Write H for 10**8192. With common denominator
    # (H+1)**2 the scores are 2H+1, 2H and 4H respectively.
    contract = weighted(accuracy_weight: 'huge', latency_weight: 1)
    contract['dimensions'][0]['normalization'] = { 'min' => -1, 'max' => 'huge' }
    rows = [candidate('a', accuracy: 0, latency: 0), candidate('b', accuracy: 1, latency: 100),
            candidate('z', accuracy: 3, latency: 100), candidate('outside', accuracy: 'too_large')]
    huge = '1E+8192'
    ranking = cli_decimals('rank', contract, rows, 'huge' => huge, 'too_large' => '2e8192')
    assert_equal %w[z a b], ids(ranking)
    assert_empty ranking['ties']
    assert_equal 'OUT_OF_NORMALIZATION_RANGE', ranking.dig('not_comparable', 0, 'issues', 0, 'code')
    assert_equal JsonNumberToken.new(huge), ranking.dig('contract', 'dimensions', 0, 'weight')
  end
end
