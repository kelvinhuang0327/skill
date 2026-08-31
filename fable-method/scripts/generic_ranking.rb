#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'

# Pure, caller-defined comparison. No IO, inferred metrics, or cohort-derived scales.
module Fable
  module Ranking
    class InvalidInput < ArgumentError; end

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
        when Integer, TrueClass, FalseClass, NilClass then value
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
        valid = value.is_a?(Integer) || (value.is_a?(Float) && value.finite?) ||
                (value.is_a?(String) && DECIMAL.match?(value))
        fail!("#{label} must be a finite number or decimal string") unless valid
        Rational(value.to_s)
      end

      # Exact rationals order the ranking; this renders them as parseable decimals.
      # Half-up at a fixed scale, so two candidates can share a rendered score yet
      # still be ordered - score_exact is what actually decided the comparison.
      def decimal(rational)
        sign = rational.negative? ? '-' : ''
        whole, fraction = (rational.abs * (10**SCALE)).round.divmod(10**SCALE)
        return "#{sign}#{whole}" if fraction.zero?

        "#{sign}#{whole}.#{fraction.to_s.rjust(SCALE, '0').sub(/0+\z/, '')}"
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
    input = JSON.parse($stdin.read)
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
