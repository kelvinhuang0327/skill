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

  def test_canonical_implementation_depth_provenance_contract
    implementation_depth = File.read(
      File.expand_path('../shared/references/implementation-depth.md', __dir__),
      encoding: 'UTF-8'
    )
    judge_handoff = File.read(
      File.expand_path('../shared/references/judge-handoff.md', __dir__),
      encoding: 'UTF-8'
    )

    assert_includes implementation_depth, 'IMPLEMENTATION_DEPTH: NORMAL | ENHANCED'
    exact_enum = 'DEPTH_SOURCE: PLANNER_SUPPLIED | SKILL_FALLBACK'
    exact_enum_lines = lambda do |text|
      text.lines.map(&:chomp).select { |line| line.start_with?('DEPTH_SOURCE:') }
    end
    assert_equal [exact_enum], exact_enum_lines.call(implementation_depth)

    enum_mutations = [
      implementation_depth.sub(exact_enum, ''),
      implementation_depth.sub(exact_enum, "#{exact_enum} | AUTO")
    ]
    enum_mutations.each do |mutated|
      refute_equal implementation_depth, mutated
      assert_raises(Minitest::Assertion) { assert_equal [exact_enum], exact_enum_lines.call(mutated) }
    end

    depth_clauses = [
      'Every selected `IMPLEMENTATION_DEPTH` MUST be reported with exactly one `DEPTH_SOURCE`.',
      'When the Packet contains a valid `IMPLEMENTATION_DEPTH` value (`NORMAL` or `ENHANCED`), the Worker reports `DEPTH_SOURCE: PLANNER_SUPPLIED`.',
      'When the Packet omits `IMPLEMENTATION_DEPTH` and Fable selects `NORMAL` or `ENHANCED` as the Skill fallback, the Worker reports `DEPTH_SOURCE: SKILL_FALLBACK`.',
      '`DEPTH_SOURCE` records selection provenance only.',
      'Provenance does not change `WORKER_ROUTE`, create or resize a Judge or lower canonical Judge reconciliation, change model or native reasoning effort, change agent count or Loop eligibility, expand scope or acceptance, change budget, grant authorization, or resolve an unresolved authority/capability `STOP`.',
    ]
    depth_weakenings = {
      'MUST be reported with exactly one `DEPTH_SOURCE`' => 'may be reported without `DEPTH_SOURCE`',
      'reports `DEPTH_SOURCE: PLANNER_SUPPLIED`' => 'reports `DEPTH_SOURCE: SKILL_FALLBACK`',
      'reports `DEPTH_SOURCE: SKILL_FALLBACK`' => 'reports `DEPTH_SOURCE: PLANNER_SUPPLIED`',
      'selection provenance only' => 'authority for Judge depth',
      'Provenance does not change' => 'Provenance changes'
    }
    assert_canonical_contract_controls(
      'references/implementation-depth.md', 'Selecting a depth', depth_clauses, depth_weakenings
    )

    handoff_clauses = [
      'The implementation-depth evidence in this payload must include `DEPTH_SOURCE` alongside `IMPLEMENTATION_DEPTH`, using the exact two-value enum defined by `implementation-depth.md`.',
      'The Judge independently derives its own Judge trigger/depth and must not treat `DEPTH_SOURCE` as authority for Judge depth.',
    ]
    handoff_weakenings = {
      'must include `DEPTH_SOURCE` alongside `IMPLEMENTATION_DEPTH`' => 'may omit `DEPTH_SOURCE` alongside `IMPLEMENTATION_DEPTH`',
      'independently derives its own Judge trigger/depth' => 'takes its Judge depth from `DEPTH_SOURCE`'
    }
    assert_canonical_contract_controls(
      'references/judge-handoff.md', 'Handoff payload', handoff_clauses, handoff_weakenings
    )

    assert_includes judge_handoff, 'DEPTH_SOURCE'
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

  def test_canonical_publication_scope_authority_contract
    clauses = [
      'For publication-bound work, the Final artifact gate\'s existing changed-path authorization requirement resolves at PR-equivalent scope, not commit-local scope.',
      'Fresh-resolve and freeze the intended canonical publication base and the exact candidate head, then compute the changed-path scope as `git diff --name-only <canonical-base>...<candidate-head>` (or an equivalent provider compare API with the same base/head semantics), and validate that path set against the task\'s authorized publication scope:',
      'PUBLICATION_SCOPE_AUTHORITY = INTENDED_CANONICAL_BASE ... CANDIDATE_HEAD',
      'COMMIT_LOCAL_DIFF != INTENDED_PR_DIFF',
      '`COMMIT_LOCAL_DIFF` — candidate-parent → candidate-head — is commit-local evidence only and MUST NOT be accepted as proof that the intended PR is scope-clean.',
      'If canonical-base → candidate-head contains unauthorized ancestry paths, stop before push, PR creation, mark-ready, or merge.',
      'This replaces the prior changed-path interpretation; it is not a second publication-scope gate.'
    ]
    weakenings = {
      # Weaken the check back to commit-local (candidate-parent) scope.
      'requirement resolves at PR-equivalent scope, not commit-local scope' => 'requirement resolves at commit-local scope',
      'PUBLICATION_SCOPE_AUTHORITY = INTENDED_CANONICAL_BASE ... CANDIDATE_HEAD' => 'PUBLICATION_SCOPE_AUTHORITY = CANDIDATE_PARENT ... CANDIDATE_HEAD',
      'COMMIT_LOCAL_DIFF != INTENDED_PR_DIFF' => 'COMMIT_LOCAL_DIFF == INTENDED_PR_DIFF',
      'is commit-local evidence only and MUST NOT be accepted as proof that the intended PR is scope-clean' => 'is commit-local evidence and MAY be accepted as proof that the intended PR is scope-clean',
      'If canonical-base → candidate-head contains unauthorized ancestry paths, stop before push, PR creation, mark-ready, or merge.' => 'If canonical-base → candidate-head contains unauthorized ancestry paths, continue to push, PR creation, mark-ready, or merge.',
      'This replaces the prior changed-path interpretation; it is not a second publication-scope gate.' => 'This adds a second publication-scope gate alongside the prior changed-path interpretation.'
    }
    assert_canonical_contract_controls('references/operational-gates.md', 'Git action tiers', clauses, weakenings)
  end

  def test_canonical_exact_untracked_cardinality_contract
    clauses = [
      'When acceptance or safety depends on the exact file-level count or identity of untracked content, use a file-complete inventory such as `git status --porcelain=v1 --untracked-files=all` or another command proven to expose every individual file;',
      'directory-collapsed untracked output is insufficient evidence for an exact file count,',
      'nine files represented by one collapsed untracked directory entry must not be reported as exact cardinality one.',
      'Do not require `--untracked-files=all` for every task;',
      'trigger it only when exact cardinality or file identity is load-bearing.'
    ]
    weakenings = {
      'use a file-complete inventory such as' => 'use a directory-level summary such as',
      'is insufficient evidence for an exact file count' => 'is sufficient evidence for an exact file count',
      'must not be reported as exact cardinality one' => 'may be reported as exact cardinality one',
      'Do not require `--untracked-files=all` for every task' => 'Require `--untracked-files=all` for every task',
      'trigger it only when exact cardinality or file identity is load-bearing' => 'trigger it for every preflight regardless of relevance'
    }
    assert_canonical_contract_controls('SKILL.md', 'Bounded preflight and write boundary', clauses, weakenings)
  end

  def test_canonical_destructive_result_provenance_contract
    clauses = [
      'Terminal absence proves current state only:',
      '`ALREADY_ABSENT` does not by itself prove `DELETED_BY_THIS_TASK`.',
      'A handoff claim that this task deleted, removed, changed, or otherwise caused a destructive mutation must be supported by an exact entry in the existing task command or filesystem ledger recording the action and its actual observed result or exit status, not by terminal-state evidence alone;',
      'when the target is already absent before action, report `ALREADY_ABSENT`, do not execute delete, and do not claim this task caused the absence.'
    ]
    weakenings = {
      'does not by itself prove `DELETED_BY_THIS_TASK`' => 'is sufficient to prove `DELETED_BY_THIS_TASK`',
      'must be supported by an exact entry in the existing task command or filesystem ledger' => 'may be inferred without an entry in the existing task command or filesystem ledger',
      'not by terminal-state evidence alone' => 'and terminal-state evidence alone is sufficient',
      'do not execute delete, and do not claim this task caused the absence' => 'execute delete and report that this task caused the absence'
    }
    assert_canonical_contract_controls('SKILL.md', 'Authority and Packet fast path', clauses, weakenings)
  end

  def test_canonical_large_structured_output_transport_contract
    clauses = [
      'For large structured command or tool output used as load-bearing authority: capture it completely, parse or filter it internally, then project only a bounded summary to the conversational or harness surface;',
      'never derive an authority, count, identity, or completeness claim from display output that may have been truncated.',
      'If complete capture cannot be established and the missing portion could alter the decision, state `UNKNOWN` rather than treat the displayed subset as complete.',
      'This does not require a new durable evidence store',
      'use in-process parsing or an existing safe temporary mechanism.'
    ]
    weakenings = {
      'capture it completely, parse or filter it internally, then project only a bounded summary' => 'display it directly without capturing it completely',
      'never derive an authority, count, identity, or completeness claim from display output that may have been truncated' => 'deriving an authority, count, identity, or completeness claim from possibly truncated display output is acceptable',
      'state `UNKNOWN` rather than treat the displayed subset as complete' => 'treat the displayed subset as complete',
      'This does not require a new durable evidence store' => 'This requires a new durable evidence store'
    }
    assert_canonical_contract_controls('SKILL.md', 'Verification and Judge handoff', clauses, weakenings)
  end

  def test_canonical_capability_strict_tri_state_contract
    clauses = [
      'CAPABILITY_STATUS: ALLOWED | UNKNOWN | BLOCKED',
      '`ALLOWED` requires direct evidence that the current harness can perform the required execution path.',
      '`UNKNOWN` means capability has not been established and must never be treated as `ALLOWED`.',
      '`BLOCKED` means direct evidence shows the required execution path is unavailable or denied;',
      'do not repeat the same capability preflight, do not request repeated Owner action authorization as a substitute, and do not change execution path merely to bypass the block',
      'retry only on exact `CAPABILITY_STATE_CHANGED_EVIDENCE`.',
      'Owner action authorization and harness capability remain separate facts, and this leaves Planner routing semantics unchanged.'
    ]
    weakenings = {
      'must never be treated as `ALLOWED`' => 'may be treated as `ALLOWED`',
      'do not repeat the same capability preflight, do not request repeated Owner action authorization as a substitute, and do not change execution path merely to bypass the block' => 'repeat the same capability preflight or request repeated Owner action authorization as a substitute',
      'retry only on exact `CAPABILITY_STATE_CHANGED_EVIDENCE`' => 'retry at any time regardless of evidence',
      'Owner action authorization and harness capability remain separate facts' => 'Owner action authorization is equivalent to harness capability',
      'this leaves Planner routing semantics unchanged' => 'this changes Planner routing semantics'
    }
    assert_canonical_contract_controls('SKILL.md', 'Intent, authorization, and surgical execution', clauses, weakenings)
  end

  def test_canonical_dependency_topology_falsifiability_contract
    clauses = [
      'a dependency fingerprint, import closure, resource manifest, or similar graph the fix reasons over',
      'a simplified fixture is not sufficient evidence by itself unless it demonstrably reproduces that structure',
      'generalizing from an unrepresentative fixture to the real repository is not evidence the fix works there',
      'exercise a bounded case against real repository topology, or show concretely that the fixture covers the load-bearing structure',
      'Verify both directions of the identity the fix computes: a noncausal change — one the guarded structure does not depend on — must leave that identity unchanged, and a causal change — one it does depend on — must change it.',
      'A check that only ever exercises one direction cannot distinguish a real dependency boundary from an accidental one.',
      'Reuse coverage that already exercises the real structure instead of adding another probe.',
      'when no safe bounded case is possible, say so and report the untested scope honestly rather than marking it `PASS`'
    ]
    weakenings = {
      'is not sufficient evidence by itself unless it demonstrably reproduces that structure' => 'is sufficient evidence by itself even when it does not reproduce that structure',
      'is not evidence the fix works there' => 'is evidence the fix works there',
      'or show concretely that the fixture covers the load-bearing structure' => 'or assume the fixture covers the load-bearing structure',
      'a noncausal change — one the guarded structure does not depend on — must leave that identity unchanged' => 'a noncausal change — one the guarded structure does not depend on — may leave that identity unchanged',
      'and a causal change — one it does depend on — must change it' => 'and a causal change — one it does depend on — may change it',
      'cannot distinguish a real dependency boundary from an accidental one' => 'can still distinguish a real dependency boundary from an accidental one',
      'instead of adding another probe' => 'in addition to adding another probe',
      'rather than marking it `PASS`' => 'and marking it `PASS` regardless'
    }
    assert_canonical_contract_controls('references/test-falsifiability.md', 'Dependency-boundary structure', clauses, weakenings)
  end

  def test_canonical_blocked_terminal_inline_handoff_contract
    clauses = [
      'Every load-bearing `BLOCKED` gate in a terminal handoff includes exactly one compact inline blocker record — `BLOCKER_CODE:`, `BLOCKER_DETAIL:`, `SMALLEST_NEXT_ACTION:` (or a named-gate prefix, e.g. `A2_BLOCKER_CODE:`) — so a downstream Agent can pick the next action without local filesystem access to the originating Agent.',
      'The inline record is a transfer summary, not a second authority:',
      'a durable artifact or exact locator remains canonical for full evidence, but it must never be the sole carrier of the fact needed to decide what happens next,',
      'and this does not replace artifact paths, hashes, full evidence, runtime receipts, or exact authority locators.',
      '`BLOCKER_DETAIL` states the actual missing or invalid fact when known — a missing strategy, draw, config, seed, unsupported capability, or unresolved authority — never a vague `see artifact`, `blocked`, or `needs investigation` once the exact blocking fact was already observed;',
      'when genuinely unknown, state `UNKNOWN` honestly and name the smallest bounded resolution action instead.',
      '`SMALLEST_NEXT_ACTION` is one bounded progress action, not a roadmap,',
      'and each independently blocked gate carries its own record rather than one blocker duplicated under multiple aliases.',
      'A `COMPLETE` handoff is not required to carry these fields.'
    ]
    weakenings = {
      'includes exactly one compact inline blocker record' => 'may omit an inline blocker record',
      'must never be the sole carrier of the fact needed to decide what happens next' => 'may be the sole carrier of the fact needed to decide what happens next',
      'states the actual missing or invalid fact when known' => 'may state a vague placeholder even when the fact is known',
      'never a vague `see artifact`, `blocked`, or `needs investigation` once the exact blocking fact was already observed' => 'a vague `see artifact`, `blocked`, or `needs investigation` is acceptable even once the exact blocking fact was already observed',
      'is one bounded progress action, not a roadmap' => 'may be an open-ended roadmap',
      'each independently blocked gate carries its own record rather than one blocker duplicated under multiple aliases' => 'one blocker may be duplicated under multiple aliases',
      'A `COMPLETE` handoff is not required to carry these fields' => 'A `COMPLETE` handoff is required to carry these fields'
    }
    assert_canonical_contract_controls('SKILL.md', 'Lifecycle and filesystem accounting', clauses, weakenings)
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
      # Check the guarded clause directly against the mutated text rather than
      # against a raised assertion's formatted message: under a non-UTF-8
      # Encoding.default_external (e.g. an unset LANG/LC_ALL), Minitest's own
      # message pretty-printer escapes non-ASCII characters (such as an
      # em dash or arrow inside a long clause) to `\uXXXX`, so a literal
      # substring match against error.message is encoding-dependent and can
      # false-fail even though the semantic weakening is real.
      refute mutated.include?(guarded_clause),
             "negative control must remove or weaken the guarded clause verbatim: #{guarded_clause}"
      assert_raises(Minitest::Assertion) { assert_contract_clauses(mutated, clauses) }
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
