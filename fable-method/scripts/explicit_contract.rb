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
    ROUTING_LABELS = %w[TASK_CLASS WORKER_ROUTE IMPLEMENTATION_DEPTH JUDGE_MODE].freeze
    ROUTING_PACKET_LABELS = %w[TASK_CLASS WORKER_ROUTE IMPLEMENTATION_DEPTH JUDGE_MODE].freeze
    ROUTING_PACKET_REQUIRED_LABELS = %w[TASK_CLASS WORKER_ROUTE JUDGE_MODE].freeze
    ROUTING_PACKET_STATUSES = %w[
      ROUTING_STRUCTURE_PASS
      ROUTING_STRUCTURE_INVALID
      INSUFFICIENT_PACKET_FIELDS
    ].freeze
    ROUTING_PACKET_LIMITATIONS = [
      'This checks explicit routing structure only.',
      'It does not verify owner authorization, substantive Judge requirements or completion, task risk or success, or whether ENHANCED depth was necessary.'
    ].join(' ').freeze
    ROUTING_SKILL_PHRASES = [
      '`IMPLEMENTATION_DEPTH` applies only to `STATE_CHANGING_IMPLEMENTATION`.',
      '`PLANNING_ONLY` and `PURE_QA` change nothing and have no route.',
      'Planning and pure QA never dispatch a Judge.',
      'direct completion review uses `fable-judge`, never a Worker route.'
    ].freeze
    ROUTING_DEPTH_PHRASES = [
      '`FAST` already requires "no new behavior" and "one direct acceptance check", so a task that genuinely needs `ENHANCED` was very likely never a correct `FAST` classification in the first place',
      'It never changes `WORKER_ROUTE`.'
    ].freeze

    class << self
      def live_paths
        {
          skill: File.join(SHARED_ROOT, 'SKILL.md'),
          implementation_depth: File.join(SHARED_ROOT, 'references', 'implementation-depth.md'),
          reporting: File.join(SHARED_ROOT, 'references', 'reporting.md'),
          operational_gates: File.join(SHARED_ROOT, 'references', 'operational-gates.md')
        }
      end

      def skill(contract = nil)
        contract || File.read(live_paths[:skill], encoding: 'UTF-8')
      end

      def implementation_depth(contract = nil)
        contract || File.read(live_paths[:implementation_depth], encoding: 'UTF-8')
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

      # Checks only the primary, contiguous routing-field block in an existing
      # Task Packet. Later examples or quoted packets are deliberately ignored.
      def check_routing_packet(packet)
        require_phrases!(skill, ROUTING_SKILL_PHRASES, 'canonical routing contract is missing')
        require_phrases!(implementation_depth, ROUTING_DEPTH_PHRASES, 'canonical implementation-depth contract is missing')

        fields, duplicates = primary_routing_fields(packet)
        return routing_result('INSUFFICIENT_PACKET_FIELDS', ['No primary routing-field block was found.'], fields) if fields.empty?

        unless duplicates.empty?
          return routing_result(
            'ROUTING_STRUCTURE_INVALID',
            ["The primary routing block repeats fields: #{duplicates.uniq.join(', ')}."],
            fields
          )
        end

        missing = ROUTING_PACKET_REQUIRED_LABELS.reject { |label| fields.key?(label) }
        unless missing.empty?
          return routing_result(
            'INSUFFICIENT_PACKET_FIELDS',
            ["Missing required routing fields: #{missing.join(', ')}."],
            fields
          )
        end

        invalid_values = fields.each_with_object([]) do |(label, value), invalid|
          invalid << "#{label}=#{value.inspect}" unless enum_accepted?(label, value)
        end
        unless invalid_values.empty?
          return routing_result(
            'ROUTING_STRUCTURE_INVALID',
            ["Values outside the canonical routing enums: #{invalid_values.join(', ')}."],
            fields
          )
        end

        task_class = fields.fetch('TASK_CLASS')
        route = fields.fetch('WORKER_ROUTE')
        judge_mode = fields.fetch('JUDGE_MODE')
        depth = fields['IMPLEMENTATION_DEPTH']

        if task_class == 'STATE_CHANGING_IMPLEMENTATION'
          return routing_result(
            'INSUFFICIENT_PACKET_FIELDS',
            ['Missing required routing field: IMPLEMENTATION_DEPTH.'],
            fields
          ) if depth.nil?

          return routing_result(
            'ROUTING_STRUCTURE_INVALID',
            ['STATE_CHANGING_IMPLEMENTATION requires a worker route.'],
            fields
          ) if route == 'NOT_APPLICABLE'

          if route == 'FAST' && depth == 'ENHANCED'
            return routing_result(
              'ROUTING_STRUCTURE_INVALID',
              ['FAST requires NORMAL depth; ENHANCED does not change the selected route.'],
              fields
            )
          end

          if %w[STANDARD_JUDGED LOOP_JUDGED].include?(route) && judge_mode == 'NOT_APPLICABLE'
            return routing_result(
              'ROUTING_STRUCTURE_INVALID',
              ["#{route} conflicts with JUDGE_MODE: NOT_APPLICABLE."],
              fields
            )
          end
        else
          if depth
            return routing_result(
              'ROUTING_STRUCTURE_INVALID',
              ['IMPLEMENTATION_DEPTH applies only to STATE_CHANGING_IMPLEMENTATION.'],
              fields
            )
          end

          if route != 'NOT_APPLICABLE'
            return routing_result(
              'ROUTING_STRUCTURE_INVALID',
              ["#{task_class} has no Worker route; WORKER_ROUTE must be NOT_APPLICABLE."],
              fields
            )
          end

          if %w[PLANNING_ONLY PURE_QA].include?(task_class) && judge_mode != 'NOT_APPLICABLE'
            return routing_result(
              'ROUTING_STRUCTURE_INVALID',
              ["#{task_class} never dispatches a Judge; JUDGE_MODE must be NOT_APPLICABLE."],
              fields
            )
          end
        end

        routing_result(
          'ROUTING_STRUCTURE_PASS',
          ['The primary routing fields form a canonical structural combination.'],
          fields
        )
      end

      def run_routing_packet_cli(args, out: $stdout, err: $stderr)
        unless args.length == 2 && args[0] == '--check-routing-packet'
          err.puts 'Usage: ruby explicit_contract.rb --check-routing-packet /absolute/path/to/packet.md'
          return 64
        end

        begin
          packet = File.read(args[1], encoding: 'UTF-8')
          result = check_routing_packet(packet)
        rescue SystemCallError, IOError, EncodingError => error
          result = routing_result(
            'INSUFFICIENT_PACKET_FIELDS',
            ["Unable to read packet: #{error.message}"],
            {}
          )
        end

        out.puts result.fetch(:status)
        result.fetch(:details).each { |detail| out.puts "DETAIL: #{detail}" }
        out.puts "SEMANTIC_LIMITATIONS: #{ROUTING_PACKET_LIMITATIONS}"
        {
          'ROUTING_STRUCTURE_PASS' => 0,
          'ROUTING_STRUCTURE_INVALID' => 1,
          'INSUFFICIENT_PACKET_FIELDS' => 2
        }.fetch(result.fetch(:status))
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

      def primary_routing_fields(packet)
        pattern = /^\s*(#{ROUTING_PACKET_LABELS.join('|')}):\s*(.*?)\s*$/
        lines = packet.to_s.lines
        start = lines.index { |line| pattern.match?(line) }
        return [{}, []] if start.nil?

        fields = {}
        duplicates = []
        lines[start..].each do |line|
          match = pattern.match(line)
          break if match.nil?

          label = match[1]
          duplicates << label if fields.key?(label)
          fields[label] = match[2]
        end
        [fields, duplicates]
      end

      def routing_result(status, details, fields)
        raise MissingContract, "unknown routing result status #{status}" unless ROUTING_PACKET_STATUSES.include?(status)

        { status: status, details: details.freeze, fields: fields.dup.freeze }
      end

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

if $PROGRAM_NAME == __FILE__
  exit Fable::ExplicitContract.run_routing_packet_cli(ARGV)
end
