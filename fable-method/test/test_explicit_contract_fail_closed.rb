# frozen_string_literal: true

require 'minitest/autorun'
require_relative '../scripts/explicit_contract'

# Fail-closed binding for five explicit /fable-method Worker contract
# semantics. Tests invoke the shipped parser on live canonical files.
class ExplicitContractFailClosedTest < Minitest::Test
  Parser = Fable::ExplicitContract

  def test_parser_reads_live_canonical_files
    paths = Parser.live_paths
    assert_equal File.expand_path('../shared/SKILL.md', __dir__), paths[:skill]
    assert_equal File.expand_path('../shared/references/reporting.md', __dir__), paths[:reporting]
    assert_equal File.expand_path('../shared/references/operational-gates.md', __dir__),
                 paths[:operational_gates]
    paths.each_value { |path| assert File.file?(path), path }
  end

  def test_result_binding_nonzero_git_diff_check_cannot_be_pass
    refute Parser.load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 1,
      observed_satisfies_acceptance: false
    )
    refute Parser.load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 1,
      observed_satisfies_acceptance: true
    )
    assert Parser.load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 0,
      observed_satisfies_acceptance: true
    )
  end

  def test_result_binding_command_execution_alone_is_not_pass
    refute Parser.load_bearing_pass?(
      command: 'git diff --check',
      exit_status: 0,
      observed_satisfies_acceptance: false
    )
    refute Parser.load_bearing_pass?(
      command: 'true',
      exit_status: 0,
      observed_satisfies_acceptance: false
    )
  end

  def test_result_binding_detector_fails_closed_without_contract_sentence
    error = assert_raises(Parser::MissingContract) do
      Parser.load_bearing_pass?(
        command: 'git diff --check',
        exit_status: 1,
        observed_satisfies_acceptance: true,
        contract: 'NOT RUN is never PASS'
      )
    end
    assert_match(/result-binding contract is missing/, error.message)
  end

  def test_result_binding_fails_closed_when_observed_result_phrase_removed
    stripped = "#{Parser.skill}\n#{Parser.reporting}".gsub('exact observed result', 'claimed result')
    error = assert_raises(Parser::MissingContract) do
      Parser.load_bearing_pass?(
        command: 'git diff --check',
        exit_status: 0,
        observed_satisfies_acceptance: true,
        contract: stripped
      )
    end
    assert_match(/exact observed result/, error.message)
  end

  def test_stop_blocks_mutation_and_workarounds
    %i[mutation equivalent_command_substitution metadata_workaround upstream_rewrite retry_under_different_action_class].each do |action|
      refute Parser.stop_allows?(action, stop_reached: true)
    end
    assert Parser.stop_allows?(:mutation, stop_reached: false)
    assert Parser.stop_allows?(:mutation, stop_reached: true, continuation_authority: :owner_instruction)
    assert Parser.stop_allows?(:mutation, stop_reached: true, continuation_authority: :continuation_delta)
  end

  def test_stop_fails_closed_when_no_mutation_phrase_removed
    stripped = Parser.skill.sub('no mutation, ', '')
    refute_includes stripped.gsub(/\s+/, ' '), 'no mutation'
    error = assert_raises(Parser::MissingContract) do
      Parser.stop_allows?(:mutation, stop_reached: true, contract: stripped)
    end
    assert_match(/no mutation/, error.message)
  end

  def test_stop_unknown_action_is_not_silently_allowed
    error = assert_raises(Parser::MissingContract) do
      Parser.stop_allows?(:metadata_rewrite, stop_reached: false)
    end
    assert_match(/unknown STOP action/, error.message)
  end

  def test_forbidden_transcript_cannot_be_fallback
    refute Parser.forbidden_fallback_allowed?(
      'transcript',
      source_forbidden: true,
      preferred_incomplete: true
    )
    refute Parser.forbidden_fallback_allowed?(
      'transcript',
      source_forbidden: false,
      preferred_incomplete: true
    )
    refute Parser.forbidden_fallback_allowed?(
      'evidence_class',
      source_forbidden: true,
      preferred_incomplete: true
    )
  end

  def test_forbidden_fails_closed_when_transcript_sentence_removed
    stripped = "#{Parser.skill}\n#{Parser.operational_gates}".sub(
      /Transcript is\s+not an authority fallback by default\.?/i,
      ''
    )
    error = assert_raises(Parser::MissingContract) do
      Parser.forbidden_fallback_allowed?(
        'transcript',
        source_forbidden: false,
        preferred_incomplete: true,
        contract: stripped
      )
    end
    assert_match(/transcript is not an authority fallback by default/, error.message)
  end

  def test_non_force_rejects_git_force_family
    [
      'git push --force origin master',
      'git push -f origin master',
      'git push --force-with-lease origin master',
      'git push --force-if-includes origin master',
      'git push --force-with-lease=refs/heads/master origin master'
    ].each do |command|
      assert Parser.git_force_rejected?(command, force_fallback_authorized: false), command
      refute Parser.git_force_rejected?(command, force_fallback_authorized: true), command
    end
    refute Parser.git_force_rejected?('sandbox-exec -f profile.sb git status', force_fallback_authorized: false)
    refute Parser.git_force_rejected?('git status', force_fallback_authorized: false)
  end

  def test_canonical_judge_mode_enum_rejects_unknown_values
    canonical = Parser.routing_enum('JUDGE_MODE')
    assert_includes canonical, 'FRESH_CONTEXT'
    assert_includes canonical, 'SELF_CHECK_ONLY'
    assert_includes canonical, 'NOT_APPLICABLE'
    assert Parser.judge_mode_accepted?('FRESH_CONTEXT')
    assert Parser.judge_mode_accepted?('SELF_CHECK_ONLY')
    assert Parser.judge_mode_accepted?('NOT_APPLICABLE')
    refute Parser.judge_mode_accepted?('AUTO_VERIFIED')
    refute Parser.judge_mode_accepted?('INDEPENDENT')
    refute Parser.judge_mode_accepted?('BOUNDED')
  end

  def test_unknown_judge_mode_does_not_silently_join_the_live_enum
    live = Parser.routing_enum('JUDGE_MODE')
    refute_includes live, 'AUTO_VERIFIED'
    fixture = Parser.skill.sub(
      /^JUDGE_MODE: .+$/,
      'JUDGE_MODE: FRESH_CONTEXT | NOT_APPLICABLE'
    )
    refute Parser.judge_mode_accepted?('SELF_CHECK_ONLY', contract: fixture)
    refute_includes Parser.routing_enum('JUDGE_MODE', contract: fixture), 'SELF_CHECK_ONLY'
  end

  def test_canonical_route_and_task_class_enums_reject_unknown_values
    assert Parser.enum_accepted?('WORKER_ROUTE', 'STANDARD_JUDGED')
    assert Parser.enum_accepted?('TASK_CLASS', 'STATE_CHANGING_IMPLEMENTATION')
    refute Parser.enum_accepted?('WORKER_ROUTE', 'AUTO')
    refute Parser.enum_accepted?('TASK_CLASS', 'WHATEVER')
  end

  # Text-only regression against the real shared Skill; no runtime enforcement.
  def test_exact_locator_first_and_absence_contract
    assert_exact_locator_contract(Parser.skill)
  end

  def test_exact_locator_contract_rejects_broad_discovery_weakening
    live = Parser.skill
    assert_exact_locator_contract(live)
    weakened = live.sub(
      'use it directly and stop broad discovery for the same authority.',
      'use it directly and allow broad discovery for the same authority.'
    )
    refute_equal live, weakened, 'negative control must change the live clause'
    error = assert_raises(Minitest::Assertion) { assert_exact_locator_contract(weakened) }
    assert_includes error.message, 'stop broad discovery for the same authority'
    assert_exact_locator_contract(Parser.skill)
  end

  # Text-only A/B/C regressions; all negative controls are in-memory copies.
  # These bind the canonical guidance, not TaskCheckpoint runtime behavior.
  def test_canonical_input_identity_contract
    clauses = [
      'For long-running / sealed-evaluation recovery, distinguish `LOAD_BEARING_INPUT_IDENTITY` (logical input identity) from `CONTAINER_FILE_IDENTITY` (container/file identity) when the Packet defines them separately.',
      'The Packet owns the exact load-bearing input scope, the hash / identity definition, and whether whole-container identity is authoritative.',
      'The Worker must never decide for itself that the container is non-authoritative.',
      'When the Packet defines container/file identity as informational, a container file SHA/size/mtime change must not automatically invalidate or replay completed expensive computation if the Packet-authoritative logical input identity is unchanged.',
      'If the Packet makes whole-container identity authoritative, that identity remains binding.',
      'This is not a global rule that DB/container identity never matters.'
    ]
    weakenings = {
      'when the Packet defines them separately' => 'whenever the Worker prefers',
      'The Packet owns the exact load-bearing input scope' => 'The Worker owns the exact load-bearing input scope',
      'the hash / identity definition, and whether whole-container identity is authoritative' => 'the hash / identity definition; the Worker decides whether whole-container identity is authoritative',
      'must never decide for itself' => 'may decide for itself',
      'When the Packet defines container/file identity as informational' => 'Regardless of Packet-defined container authority',
      'must not automatically invalidate or replay' => 'must automatically invalidate and replay',
      'logical input identity is unchanged' => 'logical input identity has changed',
      'that identity remains binding' => 'the Worker may ignore that identity',
      'This is not a global rule' => 'This is a global rule'
    }
    assert_canonical_contract_controls(
      'references/task-checkpoint.md', 'Long-running execution recovery', clauses, weakenings
    )
  end

  def test_canonical_unsealed_evaluation_contract
    clauses = [
      '`EVALUATION_COMPLETE_UNSEALED` is a conditional evidence milestone for an expensive evaluation whose main computation completed but sealing/final aggregation/closure did not.',
      'On takeover, if compatible durable evidence confirms that milestone, reuse the completed computation and continue only the remaining authorized seal/finalization work.',
      'Do not claim this milestone unless evidence actually proves that the expensive evaluation portion completed.',
      'This milestone is not a new global lifecycle enum and does not mean the whole task is `COMPLETE`.',
      'It does not bypass existing checkpoint storage authority, protected execution, identity validation, recovery or STOP rules.'
    ]
    weakenings = {
      'a conditional evidence milestone' => 'an unconditional completion label',
      'whose main computation completed' => 'whose main computation merely started',
      'if compatible durable evidence confirms that milestone' => 'even without evidence confirming that milestone',
      'compatible durable evidence' => 'compatible session recollection',
      'compatible durable evidence confirms' => 'incompatible durable evidence confirms',
      'reuse the completed computation' => 'rerun the completed computation',
      'only the remaining authorized seal/finalization work' => 'any additional seal/finalization work',
      'Do not claim this milestone unless evidence actually proves' => 'Claim this milestone even if no evidence proves',
      'is not a new global lifecycle enum' => 'is a new global lifecycle enum',
      'does not mean the whole task is `COMPLETE`' => 'means the whole task is `COMPLETE`',
      'does not bypass existing checkpoint storage authority' => 'bypasses existing checkpoint storage authority',
      'protected execution, ' => '',
      'identity validation, ' => '',
      'recovery or STOP rules' => 'recovery rules only'
    }
    assert_canonical_contract_controls(
      'references/task-checkpoint.md', 'Long-running execution recovery', clauses, weakenings
    )
  end

  def test_canonical_verification_provenance_contract
    clauses = [
      '`RUN_THIS_TASK` for checks actually executed during the current task/current execution phase',
      '`REUSED_EXACT_TREE_EVIDENCE` for prior verification reused because the exact load-bearing tree/artifact identity remains valid.',
      'Reused evidence must never be reported as a check rerun this task or labeled `RUN_THIS_TASK`.',
      'Reuse does not require rerunning a check merely to obtain a fresh `PASS` label.',
      'Keep `NOT RUN` distinct from `PASS`;',
      'provenance accuracy does not increase verification volume.',
      '`NOT RUN` is never `PASS`'
    ]
    weakenings = {
      'checks actually executed during the current task/current execution phase' => 'checks executed during any previous task',
      'the exact load-bearing tree/artifact identity remains valid' => 'a similar tree/artifact identity seems valid',
      'must never be reported as a check rerun this task or labeled `RUN_THIS_TASK`' => 'may be reported as a check rerun this task or labeled `RUN_THIS_TASK`',
      'Reuse does not require rerunning' => 'Reuse requires rerunning',
      'Keep `NOT RUN` distinct from `PASS`' => 'Report `NOT RUN` as `PASS`',
      'does not increase verification volume' => 'requires increased verification volume',
      '`NOT RUN` is never `PASS`' => '`NOT RUN` may be `PASS`'
    }
    assert_canonical_contract_controls('SKILL.md', 'Verification and Judge handoff', clauses, weakenings)
  end

  # Cleanup/mutation guidance only; no scheduler, worktree, or preservation-ref mutation.
  def test_canonical_recurring_scheduler_runtime_ownership_contract
    clauses = [
      'For runtime/worktree cleanup or mutation, `ACTIVE_RUNTIME_OWNERSHIP` exists when EITHER a currently running process owns or depends on the target OR a loaded or enabled recurring scheduler is bound to the target or its runtime source.',
      'Task-relevant mechanisms include launchd, cron, systemd, or an equivalent recurring scheduler.',
      'When applicable, cleanup or mutation preflight must inspect task-relevant binding data, including at least loaded/enabled schedule state; WorkingDirectory; executable / interpreter; script path; and import path / module root / PYTHONPATH binding.',
      '`NO_CURRENT_PROCESS` does NOT imply `NO_ACTIVE_RUNTIME_OWNERSHIP`.',
      'A loaded or enabled recurring scheduler bound to the target worktree/source retains active ownership for cleanup or mutation unless an authorized ownership transition explicitly removes or repoints the binding.',
      'Inspection remains task-relevant and bounded; do not require a workspace-wide scheduler audit.'
    ]
    weakenings = {
      # Preserve enabled schedulers and both cleanup/mutation entry points.
      'loaded or enabled recurring scheduler' => 'loaded recurring scheduler',
      'runtime/worktree cleanup or mutation' => 'runtime/worktree cleanup',
      'cleanup or mutation preflight' => 'cleanup preflight',
      'ownership for cleanup or mutation' => 'ownership for cleanup',
      # A1: reduce ownership to current processes only.
      ' OR a loaded or enabled recurring scheduler is bound to the target or its runtime source' => '',
      'EITHER a currently running process owns or depends on the target OR' => 'BOTH a currently running process owns or depends on the target AND',
      ' or its runtime source' => '',
      'launchd, ' => '',
      'cron, ' => '',
      'systemd, ' => '',
      ', or an equivalent recurring scheduler' => '',
      # A2: retain the binding but deny active ownership.
      'retains active ownership for cleanup or mutation' => 'does not count as active ownership for cleanup',
      'does NOT imply' => 'implies',
      'an authorized ownership transition explicitly removes or repoints the binding' => 'the Worker assumes the binding is idle',
      # A3: remove each load-bearing binding surface independently.
      'loaded/enabled schedule state; ' => '',
      'WorkingDirectory; ' => '',
      'executable / ' => '',
      'interpreter; ' => '',
      'script path; ' => '',
      'import path / ' => '',
      'module root / ' => '',
      'PYTHONPATH binding' => 'unspecified binding',
      'must inspect task-relevant binding data' => 'may skip task-relevant binding data',
      'Inspection remains task-relevant and bounded; do not require a workspace-wide scheduler audit.' => 'Require a workspace-wide scheduler audit.'
    }
    assert_canonical_contract_controls('SKILL.md', 'Bounded preflight and write boundary', clauses, weakenings)
  end

  def test_canonical_deployed_head_durable_source_authority_contract
    refute_includes Parser.skill.gsub(/\s+/, ' '), 'where exact deployment reproducibility matters'
    refute_includes Parser.skill, 'DEPLOYED_SOURCE_DURABILITY_REQUIRED'
    clauses = [
      'Before deleting or replacing a checkout/worktree that is or was the exact deployed runtime source, `DEPLOYED_HEAD` must have `DURABLE_SOURCE_AUTHORITY`:',
      'the exact deployed commit must remain reachable through an explicitly recognized durable Git source authority appropriate to the task.',
      'Content-equivalent code/tree on main is NOT sufficient evidence that the exact deployed source may be discarded.',
      'If exact `DEPLOYED_HEAD` has no durable source authority, STOP: `DEPLOYED_HEAD_DURABLE_SOURCE_AUTHORITY_MISSING`.',
      'The Worker MUST NOT automatically create a branch/tag/ref to satisfy this gate.',
      'Creating or changing a preservation ref remains a separate Git mutation and requires applicable task authority / authorization.'
    ]
    weakenings = {
      'Before deleting or replacing' => 'After deleting or replacing',
      'deleting or replacing' => 'deleting',
      'a checkout/worktree that is or was' => 'a detached worktree that is or was',
      'that is or was' => 'that is',
      'the exact deployed runtime source, `DEPLOYED_HEAD`' => 'the exact deployed runtime source where exact deployment reproducibility matters, `DEPLOYED_HEAD`',
      'DEPLOYED_HEAD_DURABLE_SOURCE_AUTHORITY_MISSING' => 'DEPLOYED_SOURCE_DURABILITY_REQUIRED',
      'that is or was the exact deployed runtime source' => 'that is currently the deployed runtime source',
      # B1: substitute content equivalence for exact deployed-head durability.
      '`DEPLOYED_HEAD` must have `DURABLE_SOURCE_AUTHORITY`' => 'content-equivalent main is sufficient',
      'the exact deployed commit must remain reachable' => 'a content-equivalent commit on main must remain reachable',
      'an explicitly recognized durable Git source authority appropriate to the task' => 'any available Git object',
      'is NOT sufficient evidence' => 'is sufficient evidence',
      # B2: remove the fail-closed gate.
      'STOP: `DEPLOYED_HEAD_DURABLE_SOURCE_AUTHORITY_MISSING`' => 'continue cleanup',
      # B3: allow automatic branch/tag/ref creation or waive its authority.
      'MUST NOT automatically create a branch/tag/ref' => 'may automatically create a branch/tag/ref',
      'Creating or changing a preservation ref' => 'Creating a preservation ref',
      'requires applicable task authority / authorization' => 'requires no separate authority / authorization'
    }
    assert_canonical_contract_controls('SKILL.md', 'Bounded preflight and write boundary', clauses, weakenings)
  end

  private

  def canonical_contract_section(relative_path, heading)
    path = File.expand_path("../shared/#{relative_path}", __dir__)
    text = File.read(path, encoding: 'UTF-8')
    section = text.split("## #{heading}\n", 2)[1]
    refute_nil section, "missing canonical section: #{relative_path} / #{heading}"
    section.split(/^## /, 2).first.gsub(/\s+/, ' ')
  end

  def assert_contract_clauses(text, clauses)
    clauses.each { |clause| assert_includes text, clause }
  end

  def assert_canonical_contract_controls(relative_path, heading, clauses, weakenings)
    live = canonical_contract_section(relative_path, heading)
    assert_contract_clauses(live, clauses)
    # Delete every protected clause, then materially weaken selected semantics.
    mutations = clauses.map { |clause| [clause, ''] } + weakenings.to_a
    mutations.each do |original, replacement|
      mutated = live.sub(original, replacement)
      refute_equal live, mutated, "negative control must change the clause: #{original}"
      guarded_clause = clauses.find { |clause| clause.include?(original) }
      refute_nil guarded_clause, "mutation must target an asserted protection: #{original}"
      error = assert_raises(Minitest::Assertion) { assert_contract_clauses(mutated, clauses) }
      assert_includes error.message, guarded_clause
    end
    # Re-read canonical source: no negative control may alter the real file.
    restored = canonical_contract_section(relative_path, heading)
    assert_equal live, restored
    assert_contract_clauses(restored, clauses)
  end

  def assert_exact_locator_contract(text)
    authority = text.split('## Authority and Packet fast path', 2).last
                    .to_s.split('## Bounded preflight and write boundary', 2).first
                    .to_s.gsub(/\s+/, ' ')
    [
      'After required routing, authorization, and repository identity confirmation, the first content lookup for a Packet-specified input must directly use its exact locator.',
      'Existing safety rules and required project-guidance reads still apply.',
      'If the exact locator is readable and its identity matches, use it directly and stop broad discovery for the same authority.',
      'Do not scan the workspace, all worktrees/branches, or historical transcripts to reconstruct that supplied authority.',
      'This does not prohibit scoped ordinary source lookup required by the task.',
      'If the locator is unreadable or mismatched, distinguish `ABSENT`, permission denied, network/read error, and identity mismatch.',
      'Only bounded adjacent resolution already supported by the original Packet is allowed; do not guess another path as substitute authority or bypass an existing STOP.',
      'Missing required cross-lane input returns `UPSTREAM_AUTHORITY_NOT_READY`; do not replan another task.',
      'Only for exact targets already in cleanup scope: a worktree is `ALREADY_ABSENT` only when both its filesystem path and Git registration are confirmed absent;',
      'an exact local branch ref confirmed absent makes that branch `ALREADY_ABSENT`.',
      'For a confirmed-absent target, stop searching and do not call delete.',
      'One absent branch does not imply another worktree is absent.',
      'Read errors or insufficient permissions are not absence.'
    ].each { |clause| assert_includes authority, clause }
  end
end
