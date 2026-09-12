#!/usr/bin/env ruby
# frozen_string_literal: true

# Verification temp isolation guard for /fable-method:
# Ensures Judge-authoritative expensive/full verification does not contaminate
# the judged source worktree by enforcing pre-run temp root isolation outside
# the source worktree, freezing judged source HEAD/tree, and classifying temp
# resources as TEMPORARY_DELETE. Leaves expensive verification unconsumed on
# preflight failure, while not over-governing ordinary focused tests.
# Reads the live canonical contract (fable-method/shared/references/judge-handoff.md).

require 'open3'

module Fable
  module VerificationTempIsolation
    class MissingContract < StandardError; end
    class PreflightError < StandardError; end

    SHARED_ROOT = File.expand_path('../shared', __dir__)
    JUDGE_HANDOFF_PATH = File.join(SHARED_ROOT, 'references', 'judge-handoff.md')

    REQUIRED_PHRASES = [
      'freeze the judged source HEAD/tree',
      'resolve the verification temp/scratch root',
      'require temp root outside the judged source worktree',
      'TEMPORARY_DELETE',
      'only then consume the expensive verification run',
      'Do not require external temp roots for every ordinary focused test'
    ].freeze

    RESOURCE_CLASS = 'TEMPORARY_DELETE'

    class << self
      def live_path
        JUDGE_HANDOFF_PATH
      end

      def contract_text(contract = nil)
        contract || File.read(JUDGE_HANDOFF_PATH, encoding: 'UTF-8')
      end

      def canonical_path(path)
        return nil if path.nil? || path.to_s.strip.empty?

        expanded = File.expand_path(path.to_s.strip)
        if File.exist?(expanded)
          File.realpath(expanded)
        else
          parent = expanded
          suffix = []
          until File.exist?(parent) || parent == File.dirname(parent)
            suffix.unshift(File.basename(parent))
            parent = File.dirname(parent)
          end
          real_parent = File.exist?(parent) ? File.realpath(parent) : parent
          File.expand_path(File.join(real_parent, *suffix))
        end
      end

      def inside_worktree?(worktree_root, temp_root)
        w_canon = canonical_path(worktree_root)
        t_canon = canonical_path(temp_root)
        return false if w_canon.nil? || t_canon.nil?

        t_canon == w_canon || t_canon.start_with?(File.join(w_canon, ''))
      end

      # Pre-run verification temp isolation preflight.
      #
      # worktree_root:       path to the judged source worktree
      # temp_root:           path to the verification temp/scratch root
      # expensive:           true if expensive / full verification
      # judge_authoritative: true if Judge-authoritative verification
      # source_head:         explicit HEAD SHA (or resolved from git in worktree)
      # source_tree:         explicit tree SHA (or resolved from git in worktree)
      # affects_judged_tree: whether in-tree temp could affect judged tree/evidence identity
      # contract:            optional contract text override for testing
      def preflight(worktree_root:, temp_root:, expensive: false, judge_authoritative: false,
                    source_head: nil, source_tree: nil, affects_judged_tree: true, contract: nil)
        require_contract!(contract)

        is_expensive_run = expensive || judge_authoritative

        # For ordinary focused tests: no new block even if temp is inside worktree
        unless is_expensive_run
          return {
            preflight_status: 'PASS',
            ordinary_test: true,
            blocked: false,
            expensive_run_permitted: true,
            reason: 'ORDINARY_TEST_NONBLOCK',
            resource_class: nil,
            frozen_head: source_head,
            frozen_tree: source_tree
          }
        end

        # 1. Freeze the judged source HEAD/tree
        frozen_head = source_head || git_head(worktree_root)
        frozen_tree = source_tree || git_tree(worktree_root)

        if frozen_head.nil? || frozen_head.empty? || frozen_tree.nil? || frozen_tree.empty?
          return {
            preflight_status: 'FAIL',
            ordinary_test: false,
            blocked: true,
            expensive_run_permitted: false,
            failure_reason: 'CANNOT_FREEZE_SOURCE_IDENTITY',
            resource_class: RESOURCE_CLASS,
            frozen_head: frozen_head,
            frozen_tree: frozen_tree
          }
        end

        # 2. Resolve verification temp/scratch root
        resolved_temp = canonical_path(temp_root)
        resolved_worktree = canonical_path(worktree_root)

        if resolved_temp.nil?
          return {
            preflight_status: 'FAIL',
            ordinary_test: false,
            blocked: true,
            expensive_run_permitted: false,
            failure_reason: 'UNRESOLVABLE_TEMP_ROOT',
            resource_class: RESOURCE_CLASS,
            frozen_head: frozen_head,
            frozen_tree: frozen_tree
          }
        end

        # 3. If in-tree temp state could affect judged tree/evidence identity:
        #    require temp root outside judged source worktree
        in_tree = inside_worktree?(resolved_worktree, resolved_temp)
        if in_tree && affects_judged_tree
          return {
            preflight_status: 'FAIL',
            ordinary_test: false,
            blocked: true,
            expensive_run_permitted: false,
            failure_reason: 'IN_TREE_TEMP_CONTAMINATION',
            resource_class: RESOURCE_CLASS,
            frozen_head: frozen_head,
            frozen_tree: frozen_tree,
            temp_root: resolved_temp,
            worktree_root: resolved_worktree
          }
        end

        # 4. Classify that temp resource: TEMPORARY_DELETE
        # 5. Only then consume the expensive verification run
        {
          preflight_status: 'PASS',
          ordinary_test: false,
          blocked: false,
          expensive_run_permitted: true,
          resource_class: RESOURCE_CLASS,
          frozen_head: frozen_head,
          frozen_tree: frozen_tree,
          temp_root: resolved_temp,
          worktree_root: resolved_worktree
        }
      end

      # Execute verification guarded by preflight.
      # If preflight fails, the expensive verification run is NOT executed and NOT consumed.
      def execute_guarded(worktree_root:, temp_root:, expensive: false, judge_authoritative: false,
                          source_head: nil, source_tree: nil, affects_judged_tree: true, contract: nil)
        result = preflight(
          worktree_root: worktree_root,
          temp_root: temp_root,
          expensive: expensive,
          judge_authoritative: judge_authoritative,
          source_head: source_head,
          source_tree: source_tree,
          affects_judged_tree: affects_judged_tree,
          contract: contract
        )

        if result[:blocked]
          if block_given?
            raise PreflightError, "Preflight blocked: #{result[:failure_reason]}"
          else
            return result.merge(executed: false, consumed: false)
          end
        end

        if block_given?
          output = yield(result)
          is_expensive_run = expensive || judge_authoritative
          result.merge(executed: true, consumed: is_expensive_run, output: output)
        else
          result.merge(executed: false, consumed: false)
        end
      end

      private

      def git_head(worktree_root)
        git_command(worktree_root, %w[rev-parse HEAD])
      end

      def git_tree(worktree_root)
        git_command(worktree_root, %w[rev-parse HEAD^{tree}])
      end

      def git_command(dir, args)
        return nil if dir.nil? || !File.directory?(dir)

        stdout, _stderr, status = Open3.capture3('/usr/bin/env', 'git', '-C', dir.to_s, *args)
        status.success? ? stdout.strip : nil
      rescue StandardError
        nil
      end

      def require_contract!(contract)
        haystack = flatten(contract_text(contract))
        missing = REQUIRED_PHRASES.reject { |phrase| haystack.include?(flatten(phrase)) }
        return if missing.empty?

        raise MissingContract, "Verification temp isolation contract is missing: #{missing.join(', ')}"
      end

      def flatten(text)
        text.to_s.gsub(/\s+/, ' ')
      end
    end
  end
end
