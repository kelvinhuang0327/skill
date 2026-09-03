#!/usr/bin/env ruby
# frozen_string_literal: true

# Accept/reject parser for five explicit /fable-method fail-closed semantics.
# Reads the live canonical contract; it is not a Worker simulator.
module Fable
  module ExplicitContract
    class MissingContract < StandardError; end

    SHARED_ROOT = File.expand_path('../shared', __dir__)

    RESULT_PHRASES = [
      'Command execution alone is not `PASS`',
      'exact observed result',
      '`git diff --check` cannot be reported `PASS`'
    ].freeze

    STOP_ACTION_PHRASES = {
      mutation: 'no mutation',
      equivalent_command_substitution: 'no equivalent command substitution',
      metadata_workaround: 'no metadata workaround',
      upstream_rewrite: 'no upstream rewrite',
      retry_under_different_action_class: 'no retry under a different action class'
    }.freeze

    STOP_CONTINUATION_PHRASES = [
      'Owner instruction',
      'Continuation Delta'
    ].freeze

    FORBIDDEN_PHRASES = [
      'forbidden command, path, source, transcript, or evidence class',
      'as a fallback because preferred evidence is incomplete',
      'transcript is not an authority fallback by default'
    ].freeze

    NON_FORCE_PHRASE = 'is the live NON_FORCE Git authorization'
    CONTINUATION_AUTHORITIES = %i[owner_instruction continuation_delta].freeze
    ROUTING_LABELS = %w[TASK_CLASS WORKER_ROUTE JUDGE_MODE].freeze

    class << self
      def live_paths
        {
          skill: File.join(SHARED_ROOT, 'SKILL.md'),
          reporting: File.join(SHARED_ROOT, 'references', 'reporting.md'),
          operational_gates: File.join(SHARED_ROOT, 'references', 'operational-gates.md')
        }
      end

      def skill(contract = nil)
        contract || File.read(live_paths[:skill], encoding: 'UTF-8')
      end

      def reporting(contract = nil)
        contract || File.read(live_paths[:reporting], encoding: 'UTF-8')
      end

      def operational_gates(contract = nil)
        contract || File.read(live_paths[:operational_gates], encoding: 'UTF-8')
      end

      def routing_enum(label, contract: nil)
        unless ROUTING_LABELS.include?(label)
          raise MissingContract, "unknown routing enum #{label}"
        end

        match = /^#{Regexp.escape(label)}: (.+)$/.match(skill(contract))
        raise MissingContract, "missing routing enum #{label}" if match.nil?

        match[1].split('|').map(&:strip)
      end

      def enum_accepted?(label, value, contract: nil)
        routing_enum(label, contract: contract).include?(value)
      end

      def judge_mode_accepted?(value, contract: nil)
        enum_accepted?('JUDGE_MODE', value, contract: contract)
      end

      def git_force_family(contract: nil)
        match = /GIT_FORCE_FAMILY:\n((?:.+\n)+?)```/.match(operational_gates(contract))
        raise MissingContract, 'missing GIT_FORCE_FAMILY' if match.nil?

        match[1].split("\n").map(&:strip).reject(&:empty?)
      end

      def load_bearing_pass?(command:, exit_status:, observed_satisfies_acceptance:, contract: nil)
        text = contract || "#{skill}\n#{reporting}"
        require_phrases!(text, RESULT_PHRASES, 'result-binding contract is missing')

        return false if exit_status.nil?
        return false if git_diff_check?(command) && exit_status != 0
        return false if exit_status != 0
        return false unless observed_satisfies_acceptance

        true
      end

      def stop_allows?(action, stop_reached:, continuation_authority: nil, contract: nil)
        text = skill(contract)
        phrases = STOP_ACTION_PHRASES.values + STOP_CONTINUATION_PHRASES
        require_phrases!(text, phrases, 'STOP fail-closed contract is missing')
        unless STOP_ACTION_PHRASES.key?(action)
          raise MissingContract, "unknown STOP action #{action}"
        end
        require_phrases!(text, [STOP_ACTION_PHRASES.fetch(action)], 'STOP fail-closed contract is missing')

        return true unless stop_reached
        return true if CONTINUATION_AUTHORITIES.include?(continuation_authority)

        false
      end

      def forbidden_fallback_allowed?(source_class, source_forbidden:, preferred_incomplete:, contract: nil)
        text = contract || "#{skill}\n#{operational_gates}"
        require_phrases!(text, FORBIDDEN_PHRASES, 'FORBIDDEN fallback contract is missing', downcase: true)

        return false if source_forbidden
        return false if source_class.to_s == 'transcript' && preferred_incomplete

        !preferred_incomplete || !source_forbidden
      end

      def git_force_rejected?(command, force_fallback_authorized:, contract: nil)
        text = operational_gates(contract)
        require_phrases!(text, [NON_FORCE_PHRASE], 'NON_FORCE Git force-family contract is missing')
        family = git_force_family(contract: contract)

        return false if force_fallback_authorized
        tokens = command.to_s.split(/\s+/)
        return false unless tokens.first == 'git'

        tokens.any? { |token| git_force_token?(token, family) }
      end

      private

      def flatten(text)
        text.to_s.gsub(/\s+/, ' ')
      end

      def require_phrases!(text, phrases, message, downcase: false)
        haystack = flatten(text)
        haystack = haystack.downcase if downcase
        missing = phrases.reject do |phrase|
          needle = downcase ? phrase.downcase : phrase
          haystack.include?(needle)
        end
        raise MissingContract, "#{message}: #{missing.join(', ')}" unless missing.empty?
      end

      def git_diff_check?(command)
        command.to_s.split(/\s+/)[0, 3] == %w[git diff --check]
      end

      def git_force_token?(token, family)
        family.any? do |flag|
          if flag == '-f'
            token == '-f'
          else
            token == flag || token.start_with?("#{flag}=")
          end
        end
      end
    end
  end
end
