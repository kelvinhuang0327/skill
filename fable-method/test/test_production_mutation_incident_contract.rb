# frozen_string_literal: true

require 'minitest/autorun'

class ProductionMutationIncidentContractTest < Minitest::Test
  SHARED_ROOT = File.expand_path('../shared', __dir__)
  SKILL = File.read(File.join(SHARED_ROOT, 'SKILL.md'))
  OPERATIONAL_GATES = File.read(File.join(SHARED_ROOT, 'references', 'operational-gates.md'))
  REPORTING = File.read(File.join(SHARED_ROOT, 'references', 'reporting.md'))
  TASK_CHECKPOINT = File.read(File.join(SHARED_ROOT, 'references', 'task-checkpoint.md'))

  def normalized(text)
    text.gsub(/\s+/, ' ')
  end

  # --- Behavior A: same-harness production capability preflight ---

  def test_harness_execution_permission_tristate_present
    assert_match(/HARNESS_EXECUTION_PERMISSION: ALLOWED \| UNKNOWN \| BLOCKED/, OPERATIONAL_GATES)
  end

  def test_preflight_requires_resolving_entrypoint_and_harness_chain_first
    assert_match(
      /resolve the exact\nproduction entrypoint and the actual harness\/wrapper\/launcher chain that will\ninvoke it/,
      OPERATIONAL_GATES
    )
  end

  def test_probe_must_use_same_chain_not_a_different_entry_point
    assert_match(/exact same harness\/wrapper\/launcher chain that will invoke the production\nmutation, not a different entry point merely assumed equivalent/, OPERATIONAL_GATES)
  end

  def test_no_universal_probe_command_prescribed
    assert_match(/does not prescribe one universal probe command/, OPERATIONAL_GATES)
  end

  def test_fail_closed_on_unknown_harness_permission
    assert_match(
      /stop before the production mutation rather than switching to an alternate\nwrapper, introducing a heredoc\/tmp-shell workaround, or assuming a different\ninvocation shape is equivalent/,
      OPERATIONAL_GATES
    )
  end

  def test_owner_authorization_and_harness_capability_are_separate_gates
    assert_match(/Owner authorization and harness capability\nremain separate gates/, OPERATIONAL_GATES)
    assert_match(/an `ALLOWED` probe\nresult never substitutes for standalone Owner authorization/, OPERATIONAL_GATES)
  end

  def test_preflight_scoped_to_production_not_ordinary_commands
    assert_match(/this\npreflight does not apply to an ordinary non-production command/, OPERATIONAL_GATES)
  end

  def test_preflight_reuses_existing_capability_status_tristate_not_a_second_one
    assert_match(/CAPABILITY_STATUS: ALLOWED \| UNKNOWN \| BLOCKED/, SKILL)
    assert_match(
      /This is \[`CAPABILITY_STATUS`\]\(\.\.\/SKILL\.md#intent-authorization-and-surgical-execution\)'s\nexisting tri-state under one added constraint/,
      OPERATIONAL_GATES
    )
    assert_match(/## Intent, authorization, and surgical execution/, SKILL)
  end

  def test_skill_md_points_to_the_same_chain_preflight_contract
    assert_match(
      /Before a production or deployment mutation, exercise this same tri-state through the exact harness\/wrapper\/launcher chain that will invoke the mutation/,
      SKILL
    )
    assert_includes SKILL, 'references/operational-gates.md#production-mutation-harness-preflight'
  end

  # --- Behavior B: ownership/quiescence blocker diagnostic evidence ---

  OWNERSHIP_EVIDENCE_FIELDS = %w[
    OWNER_PID OWNER_PPID OWNER_EXECUTABLE OWNER_ARGV OWNER_CWD
    OWNER_MATCHED_EVIDENCE OWNER_ROLE_MARKERS OWNER_SOURCE_OR_RUNTIME_MATCH
    OWNER_LOCK_STATES OWNERSHIP_OBSERVED_AT
  ].freeze

  def test_all_ownership_evidence_fields_present_in_reporting_contract
    OWNERSHIP_EVIDENCE_FIELDS.each do |field|
      assert_includes REPORTING, "#{field}:", "reporting.md missing ownership evidence field #{field}"
    end
  end

  def test_unavailable_field_reports_unknown_not_invented
    assert_match(/Report `UNKNOWN` for a field that is genuinely unavailable rather than\ninventing a value/, REPORTING)
  end

  def test_ownership_diagnostics_are_reporting_only_and_add_no_second_authority
    diagnostics_section = REPORTING[/## Ownership and quiescence blocker evidence.*?(?=\n## )/m]
    refute_nil diagnostics_section, 'ownership diagnostics section not found'
    normalized_section = normalized(diagnostics_section)
    assert_match(/must NOT define a\nsecond ownership classifier/m, diagnostics_section)
    %w[
      exempt\ a\ process\ from\ an\ existing\ scoped\nownership\ rule
      alter\ role\ semantics
      perform\ a\ workspace-wide\ process\ scan
      weaken\ any\ existing\nscope-qualified\ ownership\ or\ quiescence\ rule
    ].each do |fragment|
      assert_match(/#{fragment}/, diagnostics_section)
    end
    assert_match(/diagnostic reporting only/, normalized_section)
  end

  def test_ownership_diagnostics_cross_references_resolve_to_real_headings
    assert_match(/^## Scope-qualified writer and quiescence checks$/, TASK_CHECKPOINT)
    assert_match(/^## Worktrees and mutation evidence$/, OPERATIONAL_GATES)
    assert_includes REPORTING, 'task-checkpoint.md#scope-qualified-writer-and-quiescence-checks'
    assert_includes REPORTING, 'operational-gates.md#worktrees-and-mutation-evidence'
  end

  # --- Behavior C: RECOVERY_REQUIRED incidents stay durably owned, no schema/enum change ---

  def test_task_lifecycle_state_enum_is_unchanged
    assert_includes TASK_CHECKPOINT, '"task_lifecycle_state": "IN_PROGRESS | BLOCKED | COMPLETED | ABORTED",'
    assert_includes TASK_CHECKPOINT,
      '`task_lifecycle_state`: Overall lifecycle state (`IN_PROGRESS`, `BLOCKED`,'
  end

  def test_recovery_required_is_declared_a_fact_not_a_new_lifecycle_enum
    assert_match(/is not a lifecycle enum — it is a \*\*fact about the target\nproduction system\*\*/, TASK_CHECKPOINT)
  end

  def test_open_incident_reuses_existing_fields_only
    section = TASK_CHECKPOINT[/## Production mutation recovery incidents.*?(?=\n## )/m]
    refute_nil section, 'production mutation recovery incidents section not found'
    incident_block = section[/```text\ntask_lifecycle_state: BLOCKED\n.*?```/m]
    refute_nil incident_block, 'production incident checkpoint block not found'
    assert_match(/task_lifecycle_state: BLOCKED/, incident_block)
    assert_match(/current_blocker: </, incident_block)
    assert_match(/next_action: </, incident_block)
    assert_match(/authorization_boundary: </, incident_block)
  end

  def test_incident_ownership_names_the_owning_task_id
    assert_match(/make explicit which current `task_id`\nowns the unresolved incident/, TASK_CHECKPOINT)
  end

  def test_worker_forbidden_actions_on_open_production_incident
    section = TASK_CHECKPOINT[/## Production mutation recovery incidents.*?(?=\n## )/m]
    refute_nil section, 'production mutation recovery incidents section not found'
    normalized_section = normalized(section)
    assert_match(/mark the production deployment\/cutover itself successful/, normalized_section)
    assert_match(/silently leave the state for an ownerless future session/, normalized_section)
    assert_match(/automatically retry, roll back, or reconcile the production mutation/, normalized_section)
    assert_match(/invent a new recovery task or a new `next_authorized_task_packet_ref`/, normalized_section)
    assert_match(/add a new lifecycle enum or checkpoint schema field for this state/, normalized_section)
  end

  def test_judge_verified_does_not_convert_open_incident_into_success
    assert_match(
      /does NOT convert an open production recovery incident into\ndeployment success; `task_lifecycle_state` stays `BLOCKED`/,
      TASK_CHECKPOINT
    )
  end

  def test_no_new_lifecycle_status_enum_added_to_worker_lifecycle_axes
    assert_includes SKILL,
      'IMPLEMENTATION_LIFECYCLE_STATUS: NOT_STARTED | IN_PROGRESS | COMPLETE | BLOCKED | NOT_APPLICABLE'
    refute_match(/RECOVERY_REQUIRED \|/, SKILL)
    refute_match(/\| RECOVERY_REQUIRED/, SKILL)
  end
end
