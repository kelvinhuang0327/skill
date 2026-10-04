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

  def test_declared_direct_consumer_requires_its_focused_smoke
    contract = Parser.skill.gsub(/\s+/, ' ')
    assert_includes contract,
                    "When the declared acceptance surface includes the directly bound consumer of a changed runtime component, run the consumer's focused smoke alongside the focused check for that component; checking component identity alone does not verify its consumer API."
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

  def test_not_applicable_is_a_hard_worker_handoff_boundary
    clauses = [
      '`JUDGE_TRIGGER` answers whether an independent Judge is required at all.',
      '`JUDGE_MODE` answers how that handoff occurs, or is `NOT_APPLICABLE` when no Judge applies.',
      '`JUDGE_DEPTH` is `BOUNDED`, `FULL`, or `DELTA` only after a Judge actually applies.',
      'When no named mandatory Judge trigger applies and `JUDGE_MODE: NOT_APPLICABLE`, set `JUDGE_DISPATCH: SUPPRESSED`;',
      'the Worker terminal state remains the final task state.',
      'Do not create, schedule, invoke, fall back to, or automatically escalate into any Judge.',
      'When a named mandatory Judge trigger applies, `JUDGE_MODE: NOT_APPLICABLE` is a contract conflict: `STOP: JUDGE_MODE_CONTRACT_CONFLICT`.',
      'Do not suppress the mandatory Judge or silently override the Packet.',
      'Check this boundary after `JUDGE_TRIGGER` resolution and before any Judge handoff or depth evaluation.',
      '`FRESH_CONTEXT` and `SELF_CHECK_ONLY` retain their existing routing semantics.'
    ]
    weakenings = {
      '`JUDGE_TRIGGER` answers whether an independent Judge is required at all.' => '`JUDGE_TRIGGER` is the Judge mode.',
      '`JUDGE_MODE` answers how that handoff occurs, or is `NOT_APPLICABLE` when no Judge applies.' => '`JUDGE_MODE` always requires a Judge.',
      '`JUDGE_DEPTH` is `BOUNDED`, `FULL`, or `DELTA` only after a Judge actually applies.' => '`JUDGE_DEPTH` may be evaluated before a Judge applies.',
      'When no named mandatory Judge trigger applies and `JUDGE_MODE: NOT_APPLICABLE`, set `JUDGE_DISPATCH: SUPPRESSED`;' => 'When no named mandatory Judge trigger applies and `JUDGE_MODE: NOT_APPLICABLE`, launch a default Judge;',
      'the Worker terminal state remains the final task state.' => 'the Worker terminal state is an intermediate state.',
      'Do not create, schedule, invoke, fall back to, or automatically escalate into any Judge.' =>
        'create, schedule, invoke, fall back to, or automatically escalate into a Judge when needed.',
      'When a named mandatory Judge trigger applies, `JUDGE_MODE: NOT_APPLICABLE` is a contract conflict: `STOP: JUDGE_MODE_CONTRACT_CONFLICT`.' =>
        'When a named mandatory Judge trigger applies, `JUDGE_MODE: NOT_APPLICABLE` suppresses the Judge.',
      'Do not suppress the mandatory Judge or silently override the Packet.' =>
        'Suppress the mandatory Judge or silently override the Packet.',
      'Check this boundary after `JUDGE_TRIGGER` resolution and before any Judge handoff or depth evaluation.' =>
        'Check this boundary after Judge handoff or depth evaluation.',
      'retain their existing routing semantics' => 'may be changed by this boundary'
    }
    assert_canonical_contract_controls(
      'references/judge-handoff.md', 'When the Judge gate fires', clauses, weakenings
    )
  end

  def test_read_only_completion_review_obeys_the_resolved_judge_boundary
    clauses = [
      'Use `READ_ONLY_COMPLETION_REVIEW` for claimed-complete work; it is not itself a Judge trigger. Resolve its trigger and mode before dispatch.',
      'With a mandatory trigger, use the resolved Judge mode and no Worker route; without fresh-context capability, self-check only and do not claim independent `VERIFIED`.',
      'When no mandatory Judge trigger applies and mode is `NOT_APPLICABLE`, set `JUDGE_DISPATCH: SUPPRESSED`. A mandatory Judge trigger cannot be suppressed or silently overridden.'
    ]
    weakenings = {
      'it is not itself a Judge trigger' => 'it is itself a Judge trigger',
      'Resolve its trigger and mode before dispatch' => 'Dispatch before resolving its trigger and mode',
      'use the resolved Judge mode and no Worker route' => 'ignore the resolved Judge mode and choose a Worker route',
      'cannot be suppressed or silently overridden' => 'may be suppressed or silently overridden'
    }
    assert_canonical_contract_controls('SKILL.md', 'First output and task class', clauses, weakenings)
  end

  def test_planning_only_and_pure_qa_suppress_judge_dispatch
    clauses = [
      'Planning and pure QA never dispatch a Judge.',
      'When no mandatory Judge trigger applies and mode is `NOT_APPLICABLE`, set `JUDGE_DISPATCH: SUPPRESSED`.'
    ]
    weakenings = {
      'never dispatch a Judge' => 'may dispatch a Judge',
      'set `JUDGE_DISPATCH: SUPPRESSED`' => 'set `JUDGE_DISPATCH: REQUIRED`'
    }
    assert_canonical_contract_controls('SKILL.md', 'First output and task class', clauses, weakenings)
  end

  def test_fresh_context_trigger_keeps_independent_judge_behavior
    clauses = [
      'A named mandatory Judge trigger with `JUDGE_MODE: FRESH_CONTEXT` still requires an independent `fable-judge` handoff.',
      '`FRESH_CONTEXT` and `SELF_CHECK_ONLY` retain their existing routing semantics.'
    ]
    weakenings = {
      'still requires an independent `fable-judge` handoff' => 'may use a Worker self-check instead of an independent `fable-judge` handoff',
      'retain their existing routing semantics' => 'may change routing semantics'
    }
    assert_canonical_contract_controls('references/judge-handoff.md', 'When the Judge gate fires', clauses, weakenings)
  end

  def test_self_check_cannot_claim_independent_verified
    clauses = [
      'A Worker with no fresh-context capability may self-check only,',
      'must mark `JUDGE_MODE: SELF_CHECK_ONLY`,',
      'must not claim independent `VERIFIED` for a Judge-gated task.'
    ]
    weakenings = {
      'may self-check only' => 'may claim independent verification',
      'must mark `JUDGE_MODE: SELF_CHECK_ONLY`' => 'may omit `JUDGE_MODE: SELF_CHECK_ONLY`',
      'must not claim independent `VERIFIED`' => 'may claim independent `VERIFIED`'
    }
    assert_canonical_contract_controls('references/judge-handoff.md', 'When the Judge gate fires', clauses, weakenings)
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

  # Text-only regression against canonical operational guidance; no runtime enforcement.
  def test_exact_locator_first_and_absence_contract
    assert_exact_locator_contract(Parser.operational_gates)
  end

  def test_exact_locator_contract_rejects_broad_discovery_weakening
    live = Parser.operational_gates
    assert_exact_locator_contract(live)
    weakened = live.sub(
      'stop broad discovery for that authority;',
      'continue broad discovery for that authority;'
    )
    refute_equal live, weakened, 'negative control must change the live clause'
    error = assert_raises(Minitest::Assertion) { assert_exact_locator_contract(weakened) }
    assert_includes error.message, 'stop broad discovery for that authority'
    assert_exact_locator_contract(Parser.operational_gates)
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
      '`RUN_THIS_TASK` for checks actually executed this task/current phase',
      '`REUSED_EXACT_TREE_EVIDENCE` when valid evidence is reused for an identical command, environment, HEAD, and tree.',
      'Never call reused evidence a rerun.',
      'Reuse does not require rerunning a check solely to obtain a fresh label; keep `NOT RUN` distinct from `PASS`.'
    ]
    weakenings = {
      'checks actually executed this task/current phase' => 'checks executed during any previous task',
      'identical command, environment, HEAD, and tree' => 'a similar command, environment, HEAD, or tree',
      'Never call reused evidence a rerun.' => 'Reused evidence may be called a rerun.',
      'Reuse does not require rerunning' => 'Reuse requires rerunning',
      'keep `NOT RUN` distinct from `PASS`' => 'report `NOT RUN` as `PASS`'
    }
    assert_canonical_contract_controls('references/reporting.md', 'Evidence labels', clauses, weakenings)
    assert_includes Parser.skill, '`NOT RUN` is never `PASS`'
  end

  # Cleanup/mutation guidance only; no scheduler, worktree, or preservation-ref mutation.
  def test_canonical_recurring_scheduler_runtime_ownership_contract
    clauses = [
      'For runtime/worktree cleanup or mutation, `ACTIVE_RUNTIME_OWNERSHIP` includes either a running process owning or depending on the target, or a loaded/enabled recurring scheduler bound to the target or its runtime source.',
      'Task-relevant schedulers include launchd, cron, systemd, or an equivalent recurring scheduler.',
      'When applicable, inspect only task-relevant schedule state, WorkingDirectory, executable/interpreter, script path, and import/module-root/PYTHONPATH bindings.',
      'A loaded/enabled scheduler bound to the target worktree/source retains active ownership unless an authorized ownership transition removes or repoints the binding.',
      '`NO_CURRENT_PROCESS` does not establish `NO_ACTIVE_RUNTIME_OWNERSHIP`;',
      'do not turn this into a workspace-wide audit.'
    ]
    weakenings = {
      'or a loaded/enabled recurring scheduler bound to the target or its runtime source' => 'with no recurring scheduler ownership',
      'launchd, cron, systemd, or an equivalent recurring scheduler' => 'one-time process only',
      'inspect only task-relevant schedule state' => 'skip task-relevant schedule state',
      'retains active ownership unless an authorized ownership transition removes or repoints the binding' => 'does not count as active ownership',
      'does not establish `NO_ACTIVE_RUNTIME_OWNERSHIP`' => 'establishes `NO_ACTIVE_RUNTIME_OWNERSHIP`',
      'do not turn this into a workspace-wide audit' => 'require a workspace-wide audit'
    }
    assert_canonical_contract_controls(
      'references/operational-gates.md', 'Bounded preflight and write boundary', clauses, weakenings
    )
  end

  def test_canonical_deployed_head_durable_source_authority_contract
    refute_includes Parser.skill.gsub(/\s+/, ' '), 'where exact deployment reproducibility matters'
    refute_includes Parser.skill, 'DEPLOYED_SOURCE_DURABILITY_REQUIRED'
    clauses = [
      'Before deleting or replacing a checkout/worktree that is or was the exact deployed runtime source, require `DEPLOYED_HEAD` to remain reachable through an explicitly recognized durable Git source authority appropriate to the task.',
      'Content-equivalent code/tree on main is NOT sufficient evidence that the exact deployed source may be discarded.',
      'If exact `DEPLOYED_HEAD` has no durable source authority, STOP: `DEPLOYED_HEAD_DURABLE_SOURCE_AUTHORITY_MISSING`.',
      'The Worker MUST NOT automatically create a branch/tag/ref to satisfy this gate.',
      'Creating or changing a preservation ref remains a separate Git mutation and requires applicable task authority / authorization.'
    ]
    weakenings = {
      'Before deleting or replacing' => 'After deleting or replacing',
      '`DEPLOYED_HEAD` to remain reachable' => 'main equivalence is sufficient',
      'is NOT sufficient evidence' => 'is sufficient evidence',
      'STOP: `DEPLOYED_HEAD_DURABLE_SOURCE_AUTHORITY_MISSING`' => 'continue with cleanup',
      'MUST NOT automatically create a branch/tag/ref' => 'may automatically create a branch/tag/ref',
      'requires applicable task authority / authorization' => 'requires no separate authorization'
    }
    assert_canonical_contract_controls(
      'references/operational-gates.md', 'Bounded preflight and write boundary', clauses, weakenings
    )
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

  def test_shared_front_door_skips_git_tier_load_only_for_resolved_local_commit
    clauses = [
      'Consult [operational gates](references/operational-gates.md#git-action-tiers) before any Git lifecycle action except an ordinary local commit on an already-resolved FAST task',
      '`COMMIT_AUTHORIZED: YES`, the exact repository/worktree and write scope are known, and the commit target/history identity is unambiguous.',
      'The exception applies only to a non-destructive local commit with no force/fallback, local branch/worktree deletion, remote mutation (including push, Draft/Ready PR, or merge), or authorization conflict; commit directly without loading the detailed Git-action tiers.',
      'Consult the reference for every other Git action or whenever authorization, scope, or identity is missing, ambiguous, or conflicted.'
    ]
    weakenings = {
      'before any Git lifecycle action except an ordinary local commit' => 'before every Git lifecycle action, including an ordinary local commit',
      'on an already-resolved FAST task' => 'on any task',
      'COMMIT_AUTHORIZED: YES' => 'COMMIT_AUTHORIZED: NO',
      'no force/fallback, local branch/worktree deletion, remote mutation' => 'force/fallback, local branch/worktree deletion, remote mutation',
      'without loading the detailed Git-action tiers' => 'only after loading the detailed Git-action tiers',
      'for every other Git action' => 'only for remote Git actions'
    }
    assert_canonical_contract_controls('SKILL.md', 'Lifecycle and filesystem accounting', clauses, weakenings)
  end

  def test_canonical_exact_untracked_cardinality_contract
    clauses = [
      'When exact untracked-file count or identity is load-bearing, use a file-complete inventory such as `git status --porcelain=v1 --untracked-files=all`;',
      'a collapsed directory entry does not establish file cardinality.',
      'Do not require this inventory when exact count or identity is immaterial.'
    ]
    weakenings = {
      'use a file-complete inventory' => 'use a directory-level summary',
      'does not establish file cardinality' => 'establishes exact file cardinality',
      'when exact count or identity is immaterial' => 'for every preflight regardless of relevance'
    }
    assert_canonical_contract_controls(
      'references/operational-gates.md', 'Bounded preflight and write boundary', clauses, weakenings
    )
  end

  def test_canonical_destructive_result_provenance_contract
    provenance_clauses = [
      'Terminal absence proves current state only.',
      '`ALREADY_ABSENT` does not by itself prove `DELETED_BY_THIS_TASK`.',
      'A handoff claim that this task deleted, removed, changed, or otherwise caused a destructive mutation must be supported by an exact entry in the existing task command or filesystem ledger recording the action and its actual observed result or exit status, not by terminal-state evidence alone.'
    ]
    provenance_weakenings = {
      'does not by itself prove `DELETED_BY_THIS_TASK`' => 'is sufficient to prove `DELETED_BY_THIS_TASK`',
      'must be supported by an exact entry in the existing task command or filesystem ledger' => 'may be inferred without an entry in the existing task command or filesystem ledger',
      'not by terminal-state evidence alone' => 'and terminal-state evidence alone is sufficient'
    }
    assert_canonical_contract_controls(
      'references/reporting.md', 'Filesystem accounting', provenance_clauses, provenance_weakenings
    )
    absence_clauses = [
      'Once confirmed absent, stop searching and do not call delete; current absence alone does not prove this task deleted the target.',
      'For a confirmed-absent target, report `ALREADY_ABSENT`, do not execute delete, and do not claim this task caused the absence.'
    ]
    absence_weakenings = {
      'do not call delete' => 'call delete',
      'do not execute delete, and do not claim this task caused the absence' => 'execute delete and claim this task caused the absence'
    }
    assert_canonical_contract_controls(
      'references/operational-gates.md', 'Packet and authority', absence_clauses, absence_weakenings
    )
  end

  def test_canonical_destructive_action_provenance_contract
    clauses = [
      'When a Worker actually performs a destructive filesystem / durable-resource removal authorized by its Packet, terminal handoff must report the destructive action provenance.',
      'DESTRUCTIVE_ACTION_OCCURRED: YES',
      'DESTRUCTIVE_TARGET: <exact target or compact exact target set>',
      'DESTRUCTIVE_TARGET_PRESTATE: <exact observed state>',
      'DESTRUCTIVE_ACTION: <exact primitive/action>',
      'DESTRUCTIVE_ACTION_AT: <timestamp>',
      'DESTRUCTIVE_ACTION_AUTHORIZATION_SOURCE: <existing authorization evidence vocabulary>',
      'DESTRUCTIVE_ACTION_TASK_OR_RUN_ID: <exact current task/run identity if naturally available>',
      'DESTRUCTIVE_TARGET_POSTSTATE: <exact observed state>',
      '`DESTRUCTIVE_ACTION_AUTHORIZATION_SOURCE` reuses the existing authorization evidence vocabulary; it does not define a second authority model.',
      'DESTRUCTIVE_ACTION_OCCURRED: NO',
      'Do not require the detailed YES-only fields for ordinary non-destructive tasks.',
      'A target observed as `ALREADY_ABSENT` means the current Worker did not need to perform deletion; it must not be reported as evidence that this Worker executed a destructive action.',
      'A later observer may report current absence, but must not infer who deleted the target without provenance evidence.',
      'This is reporting provenance only; it never authorizes a destructive action, and authorization remains governed by [operational gates](operational-gates.md) and the existing authorization evidence vocabulary.',
      'This contract does not require a new persistent receipt/evidence framework, registry, evidence database, receipt file, ledger service, or runtime storage.',
      'Do not require an extra command solely for reporting when the Worker naturally knows the information from the action it just performed.'
    ]
    weakenings = {
      'actually performs a destructive filesystem / durable-resource removal' => 'observes a target',
      'DESTRUCTIVE_ACTION_OCCURRED: YES' => 'DESTRUCTIVE_ACTION_OCCURRED: NO',
      'DESTRUCTIVE_TARGET: <exact target or compact exact target set>' => 'DESTRUCTIVE_TARGET: <target>',
      'DESTRUCTIVE_TARGET_PRESTATE: <exact observed state>' => 'DESTRUCTIVE_TARGET_PRESTATE: <inferred state>',
      'DESTRUCTIVE_ACTION: <exact primitive/action>' => 'DESTRUCTIVE_ACTION: <unspecified action>',
      'DESTRUCTIVE_ACTION_AT: <timestamp>' => 'DESTRUCTIVE_ACTION_AT: <omitted>',
      'DESTRUCTIVE_ACTION_AUTHORIZATION_SOURCE: <existing authorization evidence vocabulary>' => 'DESTRUCTIVE_ACTION_AUTHORIZATION_SOURCE: <new authorization vocabulary>',
      'DESTRUCTIVE_ACTION_TASK_OR_RUN_ID: <exact current task/run identity if naturally available>' => 'DESTRUCTIVE_ACTION_TASK_OR_RUN_ID: <omitted>',
      'DESTRUCTIVE_TARGET_POSTSTATE: <exact observed state>' => 'DESTRUCTIVE_TARGET_POSTSTATE: <inferred state>',
      'reuses the existing authorization evidence vocabulary' => 'invents a new authorization evidence vocabulary',
      'DESTRUCTIVE_ACTION_OCCURRED: NO' => 'DESTRUCTIVE_ACTION_OCCURRED: YES',
      'Do not require the detailed YES-only fields for ordinary non-destructive tasks.' => 'Require the detailed YES-only fields for ordinary non-destructive tasks.',
      'means the current Worker did not need to perform deletion; it must not be reported as evidence that this Worker executed a destructive action.' => 'means this Worker executed a destructive action.',
      'must not infer who deleted the target without provenance evidence.' => 'may infer who deleted the target from current absence.',
      'never authorizes a destructive action' => 'authorizes a destructive action',
      'does not require a new persistent receipt/evidence framework' => 'requires a new persistent receipt/evidence framework',
      'Do not require an extra command solely for reporting' => 'Require an extra command solely for reporting'
    }
    assert_canonical_contract_controls(
      'references/reporting.md', 'Destructive action provenance', clauses, weakenings
    )
  end

  def test_canonical_large_structured_output_transport_contract
    clauses = [
      'For large structured command/tool output used as authority, capture it completely, parse or filter it internally, then project only a bounded summary to the conversational or harness surface;',
      'never derive an authority, count, identity, or completeness claim from display output that may have been truncated.',
      'If complete capture cannot be established and the missing portion could alter the decision, state `UNKNOWN` rather than treat the displayed subset as complete.',
      'This does not require a new durable evidence store; use in-process parsing or an existing safe temporary mechanism.'
    ]
    weakenings = {
      'capture it completely, parse or filter it internally, then project only a bounded summary' => 'display it directly without capturing it completely',
      'never derive an authority, count, identity, or completeness claim from display output that may have been truncated' => 'deriving an authority, count, identity, or completeness claim from possibly truncated display output is acceptable',
      'state `UNKNOWN` rather than treat the displayed subset as complete' => 'treat the displayed subset as complete',
      'does not require a new durable evidence store' => 'requires a new durable evidence store'
    }
    assert_canonical_contract_controls(
      'references/operational-gates.md', 'Bounded preflight and write boundary', clauses, weakenings
    )
  end

  def test_canonical_capability_strict_tri_state_contract
    clauses = [
      'Use the front-door `CAPABILITY_STATUS`: `ALLOWED` requires direct evidence; `UNKNOWN` is never allowed.',
      '`BLOCKED` means direct evidence shows the required execution path is unavailable or denied.',
      'After `BLOCKED`, do not repeat the same preflight, seek repeated authorization as a substitute, or change execution path to bypass the block.',
      'Retry only on exact `CAPABILITY_STATE_CHANGED_EVIDENCE`.',
      'Owner authorization and harness capability are separate facts; capability status does not change Planner routing semantics.'
    ]
    weakenings = {
      '`ALLOWED` requires direct evidence' => '`ALLOWED` needs no direct evidence',
      '`UNKNOWN` is never allowed' => '`UNKNOWN` is allowed',
      'unavailable or denied' => 'available and permitted',
      'do not repeat the same preflight' => 'repeat the same preflight',
      'Retry only on exact `CAPABILITY_STATE_CHANGED_EVIDENCE`' => 'Retry without state-change evidence',
      'Owner authorization and harness capability are separate facts' => 'Owner authorization and harness capability are equivalent facts',
      'does not change Planner routing semantics' => 'changes Planner routing semantics'
    }
    assert_includes Parser.skill, 'CAPABILITY_STATUS: ALLOWED | UNKNOWN | BLOCKED'
    assert_canonical_contract_controls(
      'references/operational-gates.md', 'Authorization evidence and conversation boundary',
      clauses, weakenings
    )
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
      'Every load-bearing `BLOCKED` gate in a terminal handoff includes exactly one compact inline blocker record — `BLOCKER_CODE:`, `BLOCKER_DETAIL:`, and `SMALLEST_NEXT_ACTION:` (or a named-gate prefix, such as `A2_BLOCKER_CODE:`) — so a downstream Agent can select the next action without local filesystem access to the originating Agent.',
      'The inline record is a transfer summary, not a second authority: a durable artifact or exact locator remains canonical for full evidence, but must never be the sole carrier of the fact needed to decide what happens next.',
      'It does not replace artifact paths, hashes, full evidence, runtime receipts, or exact authority locators.',
      '`BLOCKER_DETAIL` states the actual missing or invalid fact when known — for example, a missing strategy, draw, config, seed, unsupported capability, or unresolved authority — never a vague `see artifact`, `blocked`, or `needs investigation` once the exact blocking fact was observed.',
      'When genuinely unknown, state `UNKNOWN` and name the smallest bounded resolution action.',
      '`SMALLEST_NEXT_ACTION` is one bounded progress action, not a roadmap.',
      'Each independently blocked gate carries its own record rather than one blocker duplicated under aliases.',
      'A `COMPLETE` handoff needs no blocker record.'
    ]
    weakenings = {
      'includes exactly one compact inline blocker record' => 'may omit an inline blocker record',
      'must never be the sole carrier of the fact needed to decide what happens next' => 'may be the sole carrier of the fact needed to decide what happens next',
      'states the actual missing or invalid fact when known' => 'may state a vague placeholder even when the fact is known',
      'never a vague `see artifact`, `blocked`, or `needs investigation` once the exact blocking fact was observed' => 'a vague `see artifact`, `blocked`, or `needs investigation` is acceptable when the fact was observed',
      'When genuinely unknown' => 'Even when the fact is known',
      'is one bounded progress action, not a roadmap' => 'may be an open-ended roadmap',
      'carries its own record rather than one blocker duplicated under aliases' => 'may duplicate one blocker under aliases',
      'A `COMPLETE` handoff needs no blocker record' => 'A `COMPLETE` handoff requires a blocker record'
    }
    assert_canonical_contract_controls(
      'references/reporting.md', 'Filesystem accounting', clauses, weakenings
    )
  end

  def test_canonical_bounded_launcher_fallback_contract
    clauses = [
      'Before selecting an execution path for a task-owned expensive or long-running launch, record:',
      'LAUNCHER_STATUS: PRESENT_RUNNABLE | ABSENT_PROVEN_BEFORE_EXECUTION | UNAVAILABLE_PROVEN_BEFORE_EXECUTION | UNCERTAIN | FAILURE_AFTER_DISCOVERY',
      'EXACT_WORKTREE: <one exact absolute worktree authorized by the current Packet>',
      'EXACT_ALLOWLISTED_ARGV: <finite list of exact command and argv vectors authorized by the current Packet>',
      'EFFECT_CLASSIFICATION: LOCAL_ONLY_NO_HIGH_RISK | EXTERNAL_OR_HIGH_RISK | UNCLEAR',
      'When `PRESENT_RUNNABLE`, Workers MUST use the protected `task_checkpoint.rb --run` entrypoint. Direct-local fallback is not allowed.',
      'Direct-local fallback is permitted only when launcher absence or unavailability is positively proven before execution begins.',
      'It requires `EXACT_WORKTREE` and `EXACT_ALLOWLISTED_ARGV` from the Packet, an invoked argv that exactly matches one allowlisted vector, and `EFFECT_CLASSIFICATION: LOCAL_ONLY_NO_HIGH_RISK` confirmed for bounded local effects.',
      'When either pre-execution absence status is proven and every precondition holds, bounded direct-local execution is permitted.',
      'Fallback is forbidden when effects are external, high-risk, destructive, production-mutating, runtime-mutating, or unclear.',
      'Existing high-risk and external authorization and supported-execution requirements remain unchanged.',
      'Do not use command substitution, shell composition, wrappers invented to bypass the launcher, or alternate equivalent commands intended to route around a denial.',
      'When launcher availability is uncertain, do not fall back; use existing capability and STOP rules.',
      'If launcher selection succeeded and its execution later fails, record `FAILURE_AFTER_DISCOVERY`, preserve the original failure, follow existing RCA/STOP rules, and do not switch to direct-local execution.',
      'A direct-local fallback never overrides an actual harness or platform permission denial.',
      'Resume of a task previously blocked by launcher availability requires evidence that the currently loaded policy revision contains this fallback contract and that every fallback precondition still holds.',
      'This narrow exception does not weaken the normal requirement that high-risk or external actions use their existing authorization and supported execution path.'
    ]
    weakenings = {
      'Workers MUST use' => 'Workers may use',
      'Direct-local fallback is not allowed.' => 'Direct-local fallback is allowed.',
      'positively proven before execution begins' => 'assumed at any time',
      'argv that exactly matches one allowlisted vector' => 'argv that is broadly equivalent to an allowlisted vector',
      'LOCAL_ONLY_NO_HIGH_RISK` confirmed for bounded local effects' => 'LOCAL_ONLY_NO_HIGH_RISK` optional for external effects',
      'Fallback is forbidden when effects are external, high-risk, destructive, production-mutating, runtime-mutating, or unclear.' =>
        'Fallback may run when effects are external, high-risk, destructive, production-mutating, runtime-mutating, or unclear.',
      'Do not use command substitution, shell composition, wrappers invented to bypass the launcher, or alternate equivalent commands intended to route around a denial.' =>
        'Use command substitution, shell composition, wrappers, or alternate commands to route around a denial.',
      'do not fall back; use existing capability and STOP rules' => 'fall back when launcher availability is uncertain',
      'do not switch to direct-local execution' => 'switch to direct-local execution',
      'never overrides an actual harness or platform permission denial' =>
        'may override an actual harness or platform permission denial',
      'currently loaded policy revision contains this fallback contract' =>
        'the fallback contract need not be in the currently loaded policy revision',
      'does not weaken the normal requirement' => 'overrides the normal requirement'
    }
    assert_canonical_contract_controls('references/task-checkpoint.md', 'Bounded launcher fallback', clauses, weakenings)
    assert_includes Parser.skill, '[protected run entrypoint](references/task-checkpoint.md#protected-run-entrypoint)'
    assert_includes Parser.skill, '[bounded launcher fallback](references/task-checkpoint.md#bounded-launcher-fallback)'
  end

  def test_canonical_post_attempt_remote_observation_contract
    clauses = [
      'After a remote mutation command returns nonzero or has an ambiguous result, exactly ONE read-only observation of the exact authorized target is permitted before deciding the mutation disposition.',
      'There is no retry loop. Observe only that target; do not add generic workspace or branch discovery.',
      'Classify the observation into exactly these outcomes:',
      'The exact target is now exactly at the authorized desired state.',
      '| `AUTHORIZED_DESIRED_STATE` |',
      'Treat the target as satisfied, but retain and report the original command result as failed or ambiguous.',
      'Do not rewrite that result as `PASS`, retry the mutation, or skip ordinary verification of the achieved desired state.',
      'The exact target is still exactly at the expected pre-mutation state.',
      '| `EXPECTED_PREVIOUS_STATE` |',
      'No advance was observed. Existing RCA and authorization rules determine continuation; this observation grants no retry permission.',
      'The exact target is neither the authorized desired state nor the expected previous state.',
      '| `ANOTHER_STATE` |',
      'Classify as drift or ambiguity and STOP. Do not retry or perform destructive reconciliation.',
      'The exact target cannot be observed reliably.',
      '| `OBSERVATION_UNAVAILABLE` |',
      'State is `UNKNOWN`. Do not infer no mutation, desired completion, or retry permission.',
      'The rule is one observation, read-only, and exact-target scoped.',
      'It does not authorize a remote mutation, a retry, destructive reconciliation, production recovery, or a claim of deployment success.',
      'It does not change or replace production mutation recovery rules.'
    ]
    weakenings = {
      'exactly ONE read-only observation' => 'one or more read-write observations',
      'do not add generic workspace or branch discovery' => 'discover generic workspace or branch state',
      'retain and report the original command result as failed or ambiguous' =>
        'replace the original command result with a successful result',
      'Do not rewrite that result as `PASS`, retry the mutation' =>
        'rewrite that result as `PASS` and retry the mutation',
      'this observation grants no retry permission' => 'this observation grants retry permission',
      'Classify as drift or ambiguity and STOP.' => 'Classify as drift and continue.',
      'State is `UNKNOWN`. Do not infer no mutation, desired completion, or retry permission.' =>
        'Infer no mutation, desired completion, and retry permission.',
      'one observation, read-only, and exact-target scoped' =>
        'repeated observations, including write checks, across targets',
      'does not change or replace production mutation recovery rules' =>
        'changes and replaces production mutation recovery rules'
    }
    assert_canonical_contract_controls(
      'references/task-checkpoint.md', 'Post-attempt remote observation', clauses, weakenings
    )
    assert_includes Parser.skill, '[post-attempt remote observation](references/task-checkpoint.md#post-attempt-remote-observation)'
  end

  # A-F guard the instruction contract, not model timing or execution behavior.
  # The existing helper deletes/weakens each clause in memory, requires the
  # same assertion to fail, then re-reads and verifies the untouched source.
  def test_fast_orchestration_case_a_resolved_facts_and_direct_implementation
    clauses = [
      'Reuse already-authoritative repository/scope facts unless a live contradiction appears; grouping is not a reason to rediscover them.',
      'Repeat repository, history, worktree, or process discovery only when a live contradiction makes it load-bearing.'
    ]
    assert_canonical_contract_controls('SKILL.md', 'Bounded preflight and write boundary', clauses, {
      'unless a live contradiction appears' => 'after every tool call'
    })
    assert_canonical_contract_controls('SKILL.md', 'Route once', [
      'Once the cause and authorized fix are known on a resolved FAST task, implement directly without a second planning phase.',
      'Do not process non-applicable references or repeat discovery.'
    ], { 'without a second planning phase' => 'after a second planning phase' })
  end

  def test_fast_orchestration_case_b_independent_preflight_group
    clauses = [
      'For a resolved FAST task, prefer one bounded grouped read-only action for independent live facts such as repository identity, current HEAD/tree, worktree status, authorized scope, and required local file existence.',
      'Group only when independence and safety are already established.'
    ]
    assert_canonical_contract_controls('SKILL.md', 'Bounded preflight and write boundary', clauses, {
      'prefer one bounded grouped read-only action' => 'require a separate read-only action per fact',
      'independence and safety are already established' => 'independence and safety are unknown'
    })
  end

  def test_fast_orchestration_case_c_dependent_safety_gate_remains_serial
    clauses = [
      'If a next action depends on a prior result to decide whether it is safe, keep those actions serial.',
      'No particular shell syntax or mandatory batching is required.'
    ]
    assert_canonical_contract_controls('SKILL.md', 'Bounded preflight and write boundary', clauses, {
      'keep those actions serial' => 'batch those actions regardless',
      'No particular shell syntax or mandatory batching is required.' => 'Mandatory shell batching is required.'
    })
    assert_canonical_contract_controls('SKILL.md', 'Verification and Judge handoff', [
      'Keep dependent checks serial under the preflight grouping rule above, and observe every required result; a grouped invocation alone is not PASS.'
    ], { 'Keep dependent checks serial' => 'Batch dependent checks' })
  end

  def test_fast_orchestration_case_d_independent_required_acceptance_group
    clauses = [
      'For a resolved FAST task, prefer one bounded verification action containing already-required checks when they are independent and safe to group:',
      'focused tests, formatter/linter checks, `git diff --check`, changed-path verification, and final tracked-status checks when applicable.',
      'observe every required result; a grouped invocation alone is not PASS.'
    ]
    assert_canonical_contract_controls('SKILL.md', 'Verification and Judge handoff', clauses, {
      'already-required checks' => 'additional speculative checks',
      'when they are independent and safe to group' => 'even when they depend on prior results',
      'observe every required result' => 'observe only the final result'
    })
  end

  def test_fast_orchestration_case_e_acceptance_stops_verification
    clauses = [
      'Once acceptance is falsifiably covered and every required check passes, stop verification and reuse that evidence for handoff.',
      'Do not add a broad inspection, full suite, Judge, evidence pass, or separate identity/reporting verification pass unless explicitly required by the task, an applicable existing gate, or a new live contradiction.',
      'Specifically authorized commit/publication and their required identity/status observations still follow the existing lifecycle gates.'
    ]
    assert_canonical_contract_controls('SKILL.md', 'Verification and Judge handoff', clauses, {
      'and every required check passes' => 'even when a required check is missing',
      'stop verification and reuse that evidence' => 'start another verification phase',
      'an applicable existing gate' => 'no existing gate',
      'still follow the existing lifecycle gates' => 'bypass the existing lifecycle gates'
    })
  end

  def test_fast_orchestration_case_f_unresolved_safety_gates_fail_closed
    clauses = [
      'Any unresolved front-door gate blocks direct FAST entry and follows existing fail-closed or escalation behavior.',
      'Write ownership: no overlapping active writer, unresolved overlapping dirty state, or unexplained concurrent mutation.',
      'If write ownership remains unresolved, stop before edit, verification, or ownership mutation.',
      'Grouping never resolves or bypasses an unresolved authority, active-writer, ownership, capability, or destructive Git gate; existing fail-closed handling still applies.'
    ]
    weakenings = {
      'blocks direct FAST entry' => 'permits direct FAST entry',
      'stop before edit, verification, or ownership mutation' => 'continue to edit and verify'
    }
    %w[authority active-writer ownership capability].each do |gate|
      weakenings[gate + ','] = ''
    end
    weakenings['or destructive Git gate'] = 'or harmless Git gate'
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
    authority = text.split('### Exact Packet resolution', 2).last
                    .to_s.split(/^### /, 2).first
                    .to_s.gsub(/\s+/, ' ')
    [
      'After routing, authorization, and repository identity are confirmed, read a Packet-named input through its exact locator first.',
      'If it is readable and matches, stop broad discovery for that authority;',
      'do not scan workspaces, branches, worktrees, or transcripts to reconstruct it.',
      'This only forbids reconstructing Packet authority; it does not prohibit task-scoped source lookup after authority is resolved.',
      'When the locator is unreadable or mismatched, distinguish `ABSENT`, permission denied, network/read error, and identity mismatch.',
      'Only use bounded adjacent resolution already allowed by the Packet; a missing cross-lane input is `UPSTREAM_AUTHORITY_NOT_READY`.',
      'Do not guess a substitute or bypass a STOP.',
      'For a target already in exact cleanup scope, `ALREADY_ABSENT` requires both its filesystem path and Git registration to be absent;',
      'An exact local branch ref confirmed absent makes only that branch `ALREADY_ABSENT`; it does not establish another worktree\'s absence.',
      'Read errors or insufficient permissions are not absence.',
      'Once confirmed absent, stop searching and do not call delete; current absence alone does not prove this task deleted the target.',
      'For a confirmed-absent target, report `ALREADY_ABSENT`, do not execute delete, and do not claim this task caused the absence.'
    ].each { |clause| assert_includes authority, clause }
  end
end
