#!/usr/bin/env ruby
# frozen_string_literal: true

# Judge-depth reconciliation for /fable-method: couples a Packet's declared
# Judge depth against the canonical required depth (fable-method/shared/
# references/judge-handoff.md, "Depth and evidence reuse") so an
# under-specified Packet escalates exactly once, naming missing evidence,
# without implying the implementation is wrong. Reads the live canonical
# contract; it is not a second Judge framework, evidence registry, or
# task-state service.
module Fable
  module JudgeDepthReconciliation
    class MissingContract < StandardError; end

    SHARED_ROOT = File.expand_path('../shared', __dir__)
    JUDGE_HANDOFF_PATH = File.join(SHARED_ROOT, 'references', 'judge-handoff.md')

    DEPTHS = %w[NOT_APPLICABLE BOUNDED FULL DELTA].freeze
    ORDERED_DEPTHS = %w[BOUNDED FULL].freeze

    EVIDENCE_FOR_DEPTH = { 'FULL' => 'FULL_SUITE' }.freeze

    REQUIRED_PHRASES = [
      'JUDGE_DEPTH_RECONCILIATION: MATCH | ESCALATION_REQUIRED',
      'A depth mismatch by itself is never evidence that the implementation is wrong',
      'keep the same branch, the same worktree, the same implementation tree',
      'must restate this exact escalation'
    ].freeze

    class << self
      def live_path
        JUDGE_HANDOFF_PATH
      end

      def contract_text(contract = nil)
        contract || File.read(JUDGE_HANDOFF_PATH, encoding: 'UTF-8')
      end

      # Pure reconciliation: every input is caller-supplied (no filesystem or
      # registry lookups beyond binding to the live contract text), so the
      # same inputs always yield the same result and nothing is persisted.
      #
      # packet_depth               - PACKET_JUDGE_DEPTH as declared by the Packet
      # canonical_required_depth   - CANONICAL_REQUIRED_JUDGE_DEPTH per judge-handoff.md
      # evidence_satisfied         - whether valid required-depth evidence has been captured
      # tree_changed_since_evidence- whether the tree changed after that evidence was captured
      # capability_gap            - true only when the deeper depth's evidence cannot be
      #                              produced at all because a demanded capability is
      #                              genuinely absent (a Planner Delta/Owner decision,
      #                              never inferred silently) rather than merely not yet run
      def reconcile(packet_depth:, canonical_required_depth:, evidence_satisfied: false,
                    tree_changed_since_evidence: false, capability_gap: false, contract: nil)
        require_contract!(contract)
        validate_depth!(packet_depth, 'PACKET_JUDGE_DEPTH')
        validate_depth!(canonical_required_depth, 'CANONICAL_REQUIRED_JUDGE_DEPTH')

        escalation = escalation_required?(packet_depth, canonical_required_depth)
        effective_evidence_satisfied = evidence_satisfied && !tree_changed_since_evidence

        missing =
          if !escalation || effective_evidence_satisfied
            'NONE'
          else
            evidence_label_for(canonical_required_depth)
          end

        {
          packet_judge_depth: packet_depth,
          canonical_required_judge_depth: canonical_required_depth,
          judge_depth_reconciliation: escalation ? 'ESCALATION_REQUIRED' : 'MATCH',
          missing_judge_evidence: missing,
          implementation_mutation_required: escalation && capability_gap
        }
      end

      def escalation_required?(packet_depth, canonical_required_depth)
        return false unless ORDERED_DEPTHS.include?(canonical_required_depth)
        return false unless ORDERED_DEPTHS.include?(packet_depth)

        ORDERED_DEPTHS.index(packet_depth) < ORDERED_DEPTHS.index(canonical_required_depth)
      end

      def format_fields(result)
        mutation = result.fetch(:implementation_mutation_required) ? 'YES' : 'NO'
        [
          "PACKET_JUDGE_DEPTH: #{result.fetch(:packet_judge_depth)}",
          "CANONICAL_REQUIRED_JUDGE_DEPTH: #{result.fetch(:canonical_required_judge_depth)}",
          "JUDGE_DEPTH_RECONCILIATION: #{result.fetch(:judge_depth_reconciliation)}",
          "MISSING_JUDGE_EVIDENCE: #{result.fetch(:missing_judge_evidence)}",
          "IMPLEMENTATION_MUTATION_REQUIRED: #{mutation}"
        ].join("\n")
      end

      private

      def evidence_label_for(depth)
        EVIDENCE_FOR_DEPTH.fetch(depth) do
          raise MissingContract, "no evidence label defined for canonical required depth #{depth.inspect}"
        end
      end

      def validate_depth!(depth, label)
        return if DEPTHS.include?(depth)

        raise MissingContract, "unknown #{label} #{depth.inspect}; expected one of #{DEPTHS.join(' | ')}"
      end

      def require_contract!(contract)
        haystack = flatten(contract_text(contract))
        missing = REQUIRED_PHRASES.reject { |phrase| haystack.include?(flatten(phrase)) }
        return if missing.empty?

        raise MissingContract, "Judge-depth reconciliation contract is missing: #{missing.join(', ')}"
      end

      def flatten(text)
        text.to_s.gsub(/\s+/, ' ')
      end
    end
  end
end
