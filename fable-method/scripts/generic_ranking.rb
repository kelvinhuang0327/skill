#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'

# Pure, caller-defined comparison. No IO, inferred metrics, or cohort-derived scales.
module Fable
  module Ranking
    class InvalidInput < ArgumentError; end

    # Bound the number of implicit decimal places materialized for ordinary
    # Rational output. Larger gaps stay symbolic in score_exact.
    EXPANSION_DIGITS = 4096
    private_constant :EXPANSION_DIGITS

    # Sum of coefficient * 10**power terms. Merge nearby or overlapping digit
    # runs; leave large empty gaps implicit. Disjoint runs cannot outweigh the
    # most significant run, even when their signs differ.
    class DecimalSum
      attr_reader :terms

      def initialize(terms)
        @terms = []
        terms.sort.each do |power, coefficient|
          next if coefficient.zero?

          if @terms.last
            previous_power, previous_coefficient = @terms.last
            gap = power - previous_power
            if gap <= EXPANSION_DIGITS || gap <= previous_coefficient.abs.to_s.size
              @terms.pop
              coefficient = previous_coefficient + coefficient * 10**gap
              power = previous_power
            end
          end
          @terms << [power, coefficient] unless coefficient.zero?
        end
      end

      def sign
        @terms.empty? ? 0 : @terms.last[1] <=> 0
      end

      def +(other)
        DecimalSum.new(@terms + other.terms)
      end

      def -@
        DecimalSum.new(@terms.map { |power, coefficient| [power, -coefficient] })
      end

      def *(other)
        products = @terms.flat_map do |power, coefficient|
          other.terms.map { |p, c| [power + p, coefficient * c] }
        end
        DecimalSum.new(products)
      end

      def to_s
        parts = @terms.reverse.map { |p, c| p.zero? ? c.to_s : "#{c}e#{p}" }
        parts.size == 1 ? parts.first : "(#{parts.join('+').gsub('+-', '-')})"
      end
    end
    private_constant :DecimalSum

    # A quotient of two sparse decimal sums. Weighted normalization, unlike a
    # lexical comparison, also needs exact differences, sums and division.
    # Ordinary arithmetic stays on Ruby's reduced Rational fast path.
    class SparseRatio < Numeric
      attr_reader :top, :bottom

      def initialize(top, bottom)
        @top = top
        @bottom = bottom
      end

      def self.parse(literal)
        mantissa, _, exponent = literal.partition(/[eE]/)
        whole, _, fraction = mantissa.partition('.')
        digits = whole + fraction
        last = digits.rindex(/[1-9]/)
        return Rational(0) unless last

        coefficient = Integer(digits[0..last], 10)
        trailing_zeros = digits.size - last - 1
        scaled(Rational(coefficient), exponent.to_i - fraction.size + trailing_zeros)
      end

      def self.scaled(ratio, power)
        return ratio if ratio.zero?
        return ratio * 10**power if power.abs <= EXPANSION_DIGITS

        new(DecimalSum.new([[power, ratio.numerator]]), DecimalSum.new([[0, ratio.denominator]]))
      end

      def self.reduce(top, bottom)
        raise ZeroDivisionError if bottom.sign.zero?
        return Rational(0) if top.sign.zero?
        return Rational(1) if top.terms == bottom.terms
        if top.terms.size == 1 && bottom.terms.size == 1
          p, n = top.terms.first
          q, d = bottom.terms.first
          return scaled(Rational(n, d), p - q)
        end

        bottom.sign.negative? ? new(-top, -bottom) : new(top, bottom)
      end

      def self.wrap(value)
        return value if value.is_a?(self)

        new(DecimalSum.new([[0, value.numerator]]), DecimalSum.new([[0, value.denominator]]))
      end

      def coerce(other)
        [SparseRatio.wrap(other), self]
      end

      def -@
        SparseRatio.new(-top, bottom)
      end

      def +(other)
        other = SparseRatio.wrap(other)
        return SparseRatio.reduce(top + other.top, bottom) if bottom.terms == other.bottom.terms

        SparseRatio.reduce(top * other.bottom + other.top * bottom, bottom * other.bottom)
      end

      def -(other)
        self + -other
      end

      def *(other)
        other = SparseRatio.wrap(other)
        SparseRatio.reduce(top * other.top, bottom * other.bottom)
      end

      def /(other)
        other = SparseRatio.wrap(other)
        SparseRatio.reduce(top * other.bottom, bottom * other.top)
      end

      def <=>(other)
        other = SparseRatio.wrap(other)
        (top * other.bottom + -(other.top * bottom)).sign
      end

      def ==(other)
        other.is_a?(Numeric) && (self <=> other).zero?
      end

      # A single power prints as 1e-..., a separated sum as (1-1e-...).
      # Keep the denominator explicit when the exact score is a quotient.
      def to_s
        bottom.terms == [[0, 1]] ? top.to_s : "#{top}/#{bottom}"
      end
    end
    private_constant :SparseRatio

    # JSON calls this constructor with the original decimal token. Preserve it
    # for every echo, including non-metric data; compare its exact value.
    class JsonDecimal
      attr_reader :value

      def initialize(literal)
        unless literal.is_a?(String) && /\A-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\z/.match?(literal)
          raise InvalidInput, 'invalid JSON number'
        end
        @literal = literal.dup.freeze
        @value = SparseRatio.parse(@literal)
        freeze
      end

      def ==(other)
        case other
        when JsonDecimal then value == other.value
        when Integer then value == other
        when Float then other.finite? && value == SparseRatio.parse(other.to_s)
        else false
        end
      end

      def to_s
        @literal
      end

      def to_json(*)
        @literal
      end
    end

    def self.compare(candidate_a, candidate_b, contract)
      Engine.new(contract).compare(candidate_a, candidate_b)
    end

    def self.rank(candidates, contract)
      Engine.new(contract).rank(candidates)
    end

    class Engine
      DIRECTIONS = %w[HIGHER_IS_BETTER LOWER_IS_BETTER].freeze
      ELIGIBILITY = %w[ELIGIBLE INELIGIBLE UNRESOLVED].freeze
      STATES = %w[VALUE_PRESENT MISSING UNOBSERVABLE NOT_APPLICABLE].freeze
      DECIMAL = /\A[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\z/.freeze
      SCALE = 12

      def initialize(contract)
        @contract = copy_json(contract)
        object!(@contract, 'contract')
        keys!(@contract, %w[method universe dimensions tie_break evidence provenance], 'contract')
        @method = enum!(@contract['method'], %w[LEXICOGRAPHIC WEIGHTED], 'method')
        unless resolved_universe?(@contract['universe'])
          fail!('contract.universe requires non-empty population and evaluation_window strings')
        end
        dimensions = @contract['dimensions']
        fail!('dimensions must be a non-empty array') unless dimensions.is_a?(Array) && !dimensions.empty?
        @dimensions = dimensions.map { |dimension| compile_dimension(dimension) }
        @by_id = @dimensions.each_with_object({}) { |dimension, result| result[dimension[:id]] = dimension }
        fail!('dimension ids must be unique') unless @by_id.length == @dimensions.length
        @weight_total = @dimensions.sum { |dimension| dimension[:weight] } if @method == 'WEIGHTED'
        fail!('weights must have a positive sum') if @weight_total && @weight_total <= 0
        validate_tie_break!
      end

      def compare(candidate_a, candidate_b)
        a, b = [candidate_a, candidate_b].map { |candidate| prepare(candidate) }
        if id(a) == id(b) && a[:result]['candidate'] != b[:result]['candidate']
          fail!('the same candidate_id cannot identify different candidates')
        end
        decision(a, b).merge('contract' => @contract, 'a' => a[:result], 'b' => b[:result])
      end

      def rank(candidates)
        fail!('candidates must be an array') unless candidates.is_a?(Array)
        prepared = candidates.map { |candidate| prepare(candidate) }.sort_by { |candidate| id(candidate) }
        fail!('candidate ids must be unique') unless prepared.map { |candidate| id(candidate) }.uniq.length == prepared.length
        groups = prepared.group_by { |candidate| candidate[:result]['status'] }
        ordered = (groups['ELIGIBLE'] || []).sort { |a, b| decision(a, b)['order'] }
        previous_key = nil
        rank_number = 0
        rows = ordered.each_with_index.map do |candidate, index|
          rank_number = index + 1 if index.zero? || candidate[:primary] != previous_key
          previous_key = candidate[:primary]
          candidate[:result].merge(
            'rank' => rank_number, 'position' => index + 1,
            'comparison_to_next' => (decision(candidate, ordered[index + 1]) if ordered[index + 1])
          )
        end
        buckets = %w[INELIGIBLE UNRESOLVED NOT_COMPARABLE].map do |state|
          (groups[state] || []).map { |candidate| candidate[:result] }
        end
        status = if prepared.empty?
                   'EMPTY'
                 elsif rows.empty?
                   'NOT_COMPARABLE'
                 elsif buckets[1].any? || buckets[2].any?
                   'PARTIAL'
                 else
                   'RANKED'
                 end
        {
          'status' => status, 'contract' => @contract, 'ordered' => rows,
          'ties' => rows.group_by { |row| row['rank'] }.map do |rank, tied|
            { 'rank' => rank, 'candidate_ids' => tied.map { |row| row['candidate']['candidate_id'] } } if tied.length > 1
          end.compact,
          'ineligible' => buckets[0], 'unresolved' => buckets[1], 'not_comparable' => buckets[2]
        }
      end

      private

      def fail!(message)
        raise InvalidInput, message
      end

      # Copy JSON data so neither caller input nor nested provenance is mutated.
      def copy_json(value, depth = 0)
        fail!('input must be acyclic JSON data with nesting at most 100') if depth > 100
        case value
        when Hash
          fail!('object keys must be strings') unless value.keys.all? { |key| key.is_a?(String) }
          value.keys.sort.each_with_object({}) { |key, result| result[key.dup] = copy_json(value[key], depth + 1) }
        when Array then value.map { |item| copy_json(item, depth + 1) }
        when String then value.dup
        when Integer, JsonDecimal, TrueClass, FalseClass, NilClass then value
        when Float
          fail!('non-finite numbers are not allowed') unless value.finite?
          value
        else fail!('input must contain only JSON-compatible values')
        end
      end

      def object!(value, label)
        fail!("#{label} must be an object") unless value.is_a?(Hash)
      end

      def keys!(value, allowed, label)
        unknown = value.keys - allowed
        fail!("unknown #{label} fields: #{unknown.join(', ')}") unless unknown.empty?
      end

      def text?(value)
        value.is_a?(String) && !value.strip.empty?
      end

      def enum!(value, allowed, label)
        fail!("#{label} must be one of #{allowed.join(', ')}") unless allowed.include?(value)
        value
      end

      def number!(value, label)
        return value.value if value.is_a?(JsonDecimal)
        return Rational(value) if value.is_a?(Integer)
        return SparseRatio.parse(value.to_s) if value.is_a?(Float) || (value.is_a?(String) && DECIMAL.match?(value))

        fail!("#{label} must be a finite number or decimal string")
      end

      # Exact rationals order the ranking; this renders them as parseable decimals.
      # Half-up at a fixed scale, so two candidates can share a rendered score yet
      # still be ordered - score_exact is what actually decided the comparison.
      def decimal(rational)
        unit = 10**SCALE
        # All normalized display values lie in [0, 1]. Round a sparse ratio
        # against exact half-unit boundaries without expanding its denominator.
        rounded = if rational.is_a?(SparseRatio)
                    (0..unit).bsearch { |n| rational < Rational(2 * n + 1, 2 * unit) }
                  else
                    (rational * unit).round
                  end
        whole, fraction = rounded.divmod(unit)
        return whole.to_s if fraction.zero?

        "#{whole}.#{fraction.to_s.rjust(SCALE, '0').sub(/0+\z/, '')}"
      end

      def compile_dimension(dimension)
        object!(dimension, 'dimension')
        keys!(dimension, %w[id direction weight normalization evidence provenance], 'dimension')
        fail!('dimension.id must be a non-empty string') unless text?(dimension['id'])
        result = {
          id: dimension['id'], direction: enum!(dimension['direction'], DIRECTIONS, 'dimension.direction'),
          source: dimension
        }
        if @method == 'WEIGHTED'
          result[:weight] = number!(dimension['weight'], 'dimension.weight')
          fail!('weights cannot be negative') if result[:weight] < 0
          bounds = dimension['normalization']
          object!(bounds, 'dimension.normalization')
          keys!(bounds, %w[min max], 'normalization')
          result[:min] = number!(bounds['min'], 'normalization.min')
          result[:max] = number!(bounds['max'], 'normalization.max')
          fail!('normalization requires min < max') unless result[:min] < result[:max]
        elsif dimension.key?('weight') || dimension.key?('normalization')
          fail!('weights and normalization require method WEIGHTED')
        end
        result
      end

      def validate_tie_break!
        @tie_break = @contract['tie_break']
        unless @tie_break.is_a?(Array) && !@tie_break.empty?
          fail!('tie_break must end with an explicit candidate_id rule')
        end
        dimensions = []
        @tie_break.each_with_index do |rule, index|
          object!(rule, 'tie-break rule')
          if rule.key?('dimension')
            keys!(rule, %w[dimension], 'dimension tie-break')
            fail!('tie-break dimension must be declared once') unless @by_id.key?(rule['dimension']) && !dimensions.include?(rule['dimension'])
            dimensions << rule['dimension']
          else
            keys!(rule, %w[field direction], 'candidate-id tie-break')
            unless rule['field'] == 'candidate_id' && index == @tie_break.length - 1
              fail!('candidate_id must be the final tie-break rule')
            end
            enum!(rule['direction'], %w[ASCENDING DESCENDING], 'candidate-id direction')
          end
        end
        fail!('tie_break must end with candidate_id') unless @tie_break.last['field'] == 'candidate_id'
      end

      def resolved_universe?(universe)
        universe.is_a?(Hash) && %w[population evaluation_window].all? { |key| text?(universe[key]) }
      end

      def universe_issue(universe)
        if !resolved_universe?(universe)
          { 'code' => 'UNRESOLVED_UNIVERSE', 'actual' => universe }
        elsif universe != @contract['universe']
          { 'code' => 'UNIVERSE_MISMATCH', 'expected' => @contract['universe'], 'actual' => universe }
        end
      end

      def evidence!(value)
        valid = value.is_a?(Array) && value.all? do |reference|
          text?(reference) || (reference.is_a?(Hash) && text?(reference['ref']))
        end
        fail!('metric evidence must be an array of non-empty strings or objects with ref') unless valid
        value
      end

      def prepare(input)
        candidate = copy_json(input)
        object!(candidate, 'candidate')
        fail!('candidate_id must be a non-empty string') unless text?(candidate['candidate_id'])
        eligibility = enum!(candidate['eligibility'] || 'UNRESOLVED', ELIGIBILITY, 'eligibility')
        row = { 'candidate' => candidate, 'eligibility' => eligibility, 'status' => eligibility, 'issues' => [], 'dimensions' => [] }
        result = { result: row, values: {} }
        if eligibility != 'ELIGIBLE'
          row['issues'] << { 'code' => eligibility }
          return result
        end
        issue = universe_issue(candidate['universe'])
        if issue
          row['status'] = 'NOT_COMPARABLE'
          row['issues'] << issue
          return result
        end
        metrics = candidate['metrics'] || {}
        object!(metrics, 'metrics')
        contributions = []
        @dimensions.each do |dimension|
          name = dimension[:id]
          observation = metrics[name] || { 'state' => 'MISSING' }
          object!(observation, "metric #{name}")
          state = enum!(observation['state'], STATES, "metric #{name} state")
          evidence = evidence!(observation.fetch('evidence', []))
          trace = { 'id' => name, 'direction' => dimension[:direction], 'state' => state, 'value' => observation['value'], 'evidence' => evidence }
          trace['provenance'] = observation['provenance'] if observation.key?('provenance')
          row['dimensions'] << trace
          if state != 'VALUE_PRESENT'
            fail!("metric #{name}: #{state} cannot carry a value") unless observation['value'].nil?
            row['issues'] << { 'code' => 'MISSING_REQUIRED_VALUE', 'dimension' => name, 'state' => state }
            next
          end
          value = number!(observation['value'], "metric #{name} value")
          result[:values][name] = value
          row['issues'] << { 'code' => 'MISSING_EVIDENCE', 'dimension' => name } if evidence.empty?
          if observation.key?('universe')
            issue = universe_issue(observation['universe'])
            row['issues'] << issue.merge('dimension' => name) if issue
          end
          next unless @method == 'WEIGHTED'

          trace['weight'] = dimension[:source]['weight']
          trace['normalization'] = dimension[:source]['normalization']
          trace['normalized_weight'] = decimal(dimension[:weight] / @weight_total)
          if value < dimension[:min] || value > dimension[:max]
            row['issues'] << { 'code' => 'OUT_OF_NORMALIZATION_RANGE', 'dimension' => name }
            next
          end
          normalized = (value - dimension[:min]) / (dimension[:max] - dimension[:min])
          normalized = 1 - normalized if dimension[:direction] == 'LOWER_IS_BETTER'
          contribution = normalized * dimension[:weight] / @weight_total
          trace['normalized_value'] = decimal(normalized)
          trace['weighted_contribution'] = decimal(contribution)
          contributions << contribution
        end
        if row['issues'].any?
          row['status'] = 'NOT_COMPARABLE'
        elsif @method == 'WEIGHTED'
          score = contributions.sum
          row['score'] = decimal(score)
          row['score_exact'] = score.to_s
          result[:primary] = [-score]
        else
          result[:primary] = @dimensions.map do |dimension|
            value = result[:values].fetch(dimension[:id])
            dimension[:direction] == 'HIGHER_IS_BETTER' ? -value : value
          end
        end
        result
      end

      def id(candidate)
        candidate[:result]['candidate']['candidate_id']
      end

      def dimension_order(a, b, dimension)
        order = a[:values].fetch(dimension[:id]) <=> b[:values].fetch(dimension[:id])
        dimension[:direction] == 'HIGHER_IS_BETTER' ? -order : order
      end

      # Negative order means A precedes B. A metric tie survives an ordering tie-break.
      def decision(a, b)
        result = {
          'a_id' => id(a), 'b_id' => id(b),
          'eligibility' => { 'a' => a[:result]['eligibility'], 'b' => b[:result]['eligibility'] }
        }
        unless [a, b].all? { |candidate| candidate[:result]['status'] == 'ELIGIBLE' }
          return result.merge(
            'status' => 'NOT_COMPARABLE', 'relation' => 'NOT_COMPARABLE', 'order' => nil, 'primary_order' => nil,
            'reason' => ([a, b].any? { |candidate| candidate[:result]['eligibility'] != 'ELIGIBLE' } ? 'ELIGIBILITY' : 'INCOMPARABLE_EVIDENCE'),
            'issues' => { 'a' => a[:result]['issues'], 'b' => b[:result]['issues'] }
          )
        end
        dimensions = @dimensions.map do |dimension|
          name = dimension[:id]
          {
            'id' => name, 'direction' => dimension[:direction], 'order' => dimension_order(a, b, dimension),
            'a_value' => a[:result]['candidate']['metrics'][name]['value'],
            'b_value' => b[:result]['candidate']['metrics'][name]['value']
          }
        end
        primary_order = a[:primary] <=> b[:primary]
        order = primary_order
        applied = []
        if primary_order.zero?
          @tie_break.each do |rule|
            if rule.key?('dimension')
              dimension = @by_id.fetch(rule['dimension'])
              order = dimension_order(a, b, dimension)
              comparison = dimensions.find { |item| item['id'] == rule['dimension'] }
              applied << comparison.merge('rule' => rule)
            else
              order = id(a) <=> id(b)
              order = -order if rule['direction'] == 'DESCENDING'
              applied << { 'rule' => rule, 'a_value' => id(a), 'b_value' => id(b), 'order' => order }
            end
            break unless order.zero?
          end
        end
        decisive = dimensions.find { |dimension| !dimension['order'].zero? } if @method == 'LEXICOGRAPHIC'
        reason = if primary_order.zero?
                   order.zero? ? 'TIE' : 'TIE_BREAK'
                 else
                   @method == 'LEXICOGRAPHIC' ? 'DIMENSION' : 'WEIGHTED_SCORE'
                 end
        result.merge(
          'status' => 'COMPARABLE', 'relation' => { -1 => 'A_BEFORE_B', 0 => 'TIE', 1 => 'B_BEFORE_A' }.fetch(order),
          'order' => order, 'primary_order' => primary_order, 'primary_tie' => primary_order.zero?, 'reason' => reason,
          'decisive_dimension' => decisive && decisive['id'],
          'tied_dimensions' => dimensions.select { |dimension| dimension['order'].zero? }.map { |dimension| dimension['id'] },
          'dimensions' => dimensions, 'tie_break' => applied,
          'scores' => (if @method == 'WEIGHTED'
                         { 'a' => a[:result]['score'], 'b' => b[:result]['score'],
                           'a_exact' => a[:result]['score_exact'], 'b_exact' => b[:result]['score_exact'] }
                       end)
        )
      end
    end
    private_constant :Engine
  end
end

if $PROGRAM_NAME == __FILE__
  begin
    unless ARGV.length == 1 && %w[rank compare].include?(ARGV[0])
      raise Fable::Ranking::InvalidInput, 'usage: ruby generic_ranking.rb rank|compare < input.json'
    end
    input = JSON.parse($stdin.read, decimal_class: Fable::Ranking::JsonDecimal)
    unless input.is_a?(Hash) && (input.keys - %w[candidates contract]).empty?
      raise Fable::Ranking::InvalidInput, 'input must be an object with candidates and contract'
    end
    if ARGV[0] == 'compare'
      unless input['candidates'].is_a?(Array) && input['candidates'].length == 2
        raise Fable::Ranking::InvalidInput, 'compare requires exactly two candidates'
      end
      result = Fable::Ranking.compare(*input['candidates'], input['contract'])
    else
      result = Fable::Ranking.rank(input['candidates'], input['contract'])
    end
    puts JSON.pretty_generate(result)
  rescue JSON::ParserError, Fable::Ranking::InvalidInput => e
    warn JSON.generate('error' => 'INVALID_INPUT', 'message' => e.message)
    exit 2
  end
end
