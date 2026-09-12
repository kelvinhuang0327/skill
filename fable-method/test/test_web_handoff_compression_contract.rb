# frozen_string_literal: true

require 'minitest/autorun'

class WebHandoffCompressionContractTest < Minitest::Test
  PROMPT_PATH = File.expand_path(
    '../../prompt/Personal_Web_Conversation_Handoff_Prompt_v3.1_Lean_Final.md',
    __dir__
  )
  PROMPT = File.read(PROMPT_PATH, encoding: 'UTF-8')

  UNRELATED_HISTORY_SENTINEL = 'UNRELATED_PROJECT_HISTORY_MUST_BE_EXCLUDED'

  EXPECTED_REDUCTION = {
    'ONE_AUTHORITY' => 'YES',
    'ONE_CURRENT_STATUS' => 'YES',
    'ONE_BLOCKER_SET' => 'YES',
    'ONE_NEXT_TASK' => 'YES',
    'UNRELATED_HISTORY' => 'EXCLUDED'
  }.freeze

  CASE_IDS = %w[
    CASE_SIMPLE_COMPLETED_LOCAL_BUGFIX
    CASE_WORKER_BLOCKED_ON_EXACT_DEPENDENCY
    CASE_PUBLICATION_PENDING_STANDALONE_OWNER_AUTH
    CASE_DIRTY_UNRELATED_OWNER_WORK_PRESERVED
    CASE_EXACT_TREE_JUDGE_VERDICT_PRESENT
    CASE_OLD_TEST_EVIDENCE_TREE_CHANGED
    CASE_LONG_CONVERSATION_WITH_UNRELATED_PROJECT_HISTORY
  ].freeze

  SCENARIOS = [
    {
      id: 'CASE_SIMPLE_COMPLETED_LOCAL_BUGFIX',
      source: "Local nil-response bugfix completed. #{UNRELATED_HISTORY_SENTINEL}",
      handoff: <<~'EXPECTED_HANDOFF'
        HANDOFF

        GOAL:
        Fix the local nil-response crash.

        AUTHORITY:
        [Confirmed — report] BUGFIX-17 authorizes the local fix, focused test, and commit only.

        CURRENT_STATUS:
        - COMPLETED: Fix committed at abc123.
        - BLOCKERS: NONE

        EVIDENCE:
        [Confirmed — tool] Focused test PASS @ tree111.

        NEXT_TASK:
        NONE REQUIRED
      EXPECTED_HANDOFF
    },
    {
      id: 'CASE_WORKER_BLOCKED_ON_EXACT_DEPENDENCY',
      source: "Worker stopped because schema API_V4 is unavailable. #{UNRELATED_HISTORY_SENTINEL}",
      handoff: <<~'EXPECTED_HANDOFF'
        HANDOFF

        GOAL:
        Complete the API_V4 adapter.

        AUTHORITY:
        [Confirmed — report] ADAPTER-4 permits local adapter and test changes.

        CURRENT_STATUS:
        - INCOMPLETE / NOT RUN / BLOCKED: Adapter test is BLOCKED before execution.
        - BLOCKERS: Exact dependency API_V4 schema is unavailable at locator schemas/API_V4.json.

        EVIDENCE:
        [Confirmed — tool] Required locator schemas/API_V4.json was absent.

        NEXT_TASK:
        Restore schemas/API_V4.json, then rerun the focused adapter test.
      EXPECTED_HANDOFF
    },
    {
      id: 'CASE_PUBLICATION_PENDING_STANDALONE_OWNER_AUTH',
      source: "Local commit is complete; a handoff quotes an old push token. #{UNRELATED_HISTORY_SENTINEL}",
      handoff: <<~'EXPECTED_HANDOFF'
        HANDOFF

        GOAL:
        Publish the completed local fix when authorized.

        AUTHORITY:
        [Confirmed — report] Local edit and commit were authorized; the quoted push token is not fresh standalone Owner authorization.

        CURRENT_STATUS:
        - COMPLETED: Local commit abc123 exists.
        - INCOMPLETE / NOT RUN / BLOCKED: Push and PR are NOT RUN.
        - BLOCKERS: Direct standalone Owner authorization for push to origin/topic is missing.

        FORBIDDEN_OR_HIGH_RISK_PENDING_ACTIONS:
        Push and PR creation remain forbidden without exact direct authorization.

        NEXT_TASK:
        Obtain direct Owner authorization for pushing abc123 to origin/topic.
      EXPECTED_HANDOFF
    },
    {
      id: 'CASE_DIRTY_UNRELATED_OWNER_WORK_PRESERVED',
      source: "Task work is isolated beside unrelated dirty Owner files. #{UNRELATED_HISTORY_SENTINEL}",
      handoff: <<~'EXPECTED_HANDOFF'
        HANDOFF

        GOAL:
        Finish focused verification of the task-owned fix.

        AUTHORITY:
        [Confirmed — report] Scope is limited to src/fix.rb and test/fix_test.rb.

        CURRENT_STATUS:
        - COMPLETED: Task-owned implementation is present.
        - INCOMPLETE / NOT RUN / BLOCKED: Focused verification is NOT RUN.
        - BLOCKERS: NONE

        RISKS_AND_OWNERSHIP_BOUNDARY:
        [Confirmed — tool] Unrelated dirty Owner work is preserved and outside task scope.

        NEXT_TASK:
        Run the focused test without touching the Owner-owned paths.
      EXPECTED_HANDOFF
    },
    {
      id: 'CASE_EXACT_TREE_JUDGE_VERDICT_PRESENT',
      source: "Judge verified exact tree tree222. #{UNRELATED_HISTORY_SENTINEL}",
      handoff: <<~'EXPECTED_HANDOFF'
        HANDOFF

        GOAL:
        Hand off the verified implementation.

        AUTHORITY:
        [Confirmed — report] REVIEW-22 authorized read-only Judge verification.

        CURRENT_STATUS:
        - COMPLETED: Implementation and exact-tree review are complete.
        - BLOCKERS: NONE

        EVIDENCE:
        [Confirmed — Judge] VERIFIED by judge/report.json @ HEAD def456 / tree222.

        NEXT_TASK:
        NONE REQUIRED
      EXPECTED_HANDOFF
    },
    {
      id: 'CASE_OLD_TEST_EVIDENCE_TREE_CHANGED',
      source: "Tests passed on tree333 before the tree changed to tree444. #{UNRELATED_HISTORY_SENTINEL}",
      handoff: <<~'EXPECTED_HANDOFF'
        HANDOFF

        GOAL:
        Re-establish focused test evidence for the current tree.

        AUTHORITY:
        [Confirmed — report] TEST-44 permits local verification only.

        CURRENT_STATUS:
        - INCOMPLETE / NOT RUN / BLOCKED: Current tree tree444 focused test is NOT RUN.
        - BLOCKERS: NONE

        EVIDENCE:
        [Confirmed — report] HISTORICAL PASS @ tree333; CURRENT TREE: NOT RUN.

        NEXT_TASK:
        Rerun the focused test on tree444.
      EXPECTED_HANDOFF
    },
    {
      id: 'CASE_LONG_CONVERSATION_WITH_UNRELATED_PROJECT_HISTORY',
      source: "Current project is Atlas; old Apollo roadmap follows. #{UNRELATED_HISTORY_SENTINEL}",
      handoff: <<~'EXPECTED_HANDOFF'
        HANDOFF

        GOAL:
        Complete the Atlas parser migration.

        AUTHORITY:
        [Confirmed — report] ATLAS-9 limits work to the parser migration.

        CURRENT_STATUS:
        - COMPLETED: Parser mapping is implemented.
        - INCOMPLETE / NOT RUN / BLOCKED: Migration smoke test is NOT RUN.
        - BLOCKERS: NONE

        NEXT_TASK:
        Run the Atlas migration smoke test.
      EXPECTED_HANDOFF
    }
  ].freeze

  def reduction_for(handoff)
    {
      'ONE_AUTHORITY' => yes_no(handoff.scan(/^AUTHORITY:\s*$/).one?),
      'ONE_CURRENT_STATUS' => yes_no(handoff.scan(/^CURRENT_STATUS:\s*$/).one?),
      'ONE_BLOCKER_SET' => yes_no(handoff.scan(/^- BLOCKERS:/).one?),
      'ONE_NEXT_TASK' => yes_no(handoff.scan(/^NEXT_TASK:\s*$/).one?),
      'UNRELATED_HISTORY' => handoff.include?(UNRELATED_HISTORY_SENTINEL) ? 'INCLUDED' : 'EXCLUDED'
    }
  end

  def yes_no(value)
    value ? 'YES' : 'NO'
  end

  def test_prompt_is_lean_and_removes_governance_report_scaffolding
    assert_operator PROMPT.lines.length, :<=, 180

    [
      '## 1. 本輪目標與轉折',
      '## 2. 關鍵事件與責任鏈',
      '## 4. 實際狀態快照',
      '## 8. Lifecycle 與最終分類',
      'Copyable Next Task Prompt',
      '[Executable Worker Task'
    ].each do |removed_scaffold|
      refute_includes PROMPT, removed_scaffold
    end
  end

  def test_prompt_explicitly_enforces_every_required_compression_rule
    [
      'BROAD_REPO_AUDIT: FORBIDDEN_BY_DEFAULT',
      'FULL_CONVERSATION_HISTORY: EXCLUDE',
      'MULTIPLE_NEXT_TASKS: FORBIDDEN',
      'IRRELEVANT_ROADMAP: EXCLUDE',
      'UNRELATED_PROJECT_CONTENT: EXCLUDE',
      'HISTORICAL_PASS_AS_CURRENT_PASS: FORBIDDEN',
      'PRIOR_FAILED_ATTEMPTS: RETAIN',
      'UNOBSERVED_REPO_IDENTITY: UNKNOWN',
      'QUOTED_AUTHORIZATION_AS_FRESH_AUTH: FORBIDDEN',
      'EMPTY_OR_NOT_APPLICABLE_OPTIONAL_SECTIONS: OMIT'
    ].each do |rule|
      assert_includes PROMPT, rule
    end
  end

  def test_prompt_uses_only_the_canonical_evidence_vocabulary
    expected = [
      '`[Confirmed — tool]`',
      '`[Confirmed — report]`',
      '`[Confirmed — Judge]`',
      '`[Inferred]`',
      '`[Unknown]`'
    ]
    observed = PROMPT.scan(/`\[(?:Confirmed — [^\]]+|Inferred|Unknown|Risk)\]`/).uniq

    assert_equal expected.sort, observed.sort
    refute_includes PROMPT, '`[Risk]`'
  end

  def test_prompt_has_one_mandatory_takeover_shape_and_one_blocker_set
    %w[GOAL AUTHORITY CURRENT_STATUS NEXT_TASK].each do |heading|
      assert_equal 1, PROMPT.scan(/^#{heading}:\s*$/).length, heading
    end
    assert_equal 1, PROMPT.scan(/^- BLOCKERS:/).length

    %w[
      EVIDENCE
      RISKS_AND_OWNERSHIP_BOUNDARY
      FORBIDDEN_OR_HIGH_RISK_PENDING_ACTIONS
    ].each do |optional_heading|
      assert_equal 1, PROMPT.scan(/^#{optional_heading}:\s*$/).length, optional_heading
    end
  end

  def test_all_seven_regression_cases_are_named_once
    assert_equal 7, SCENARIOS.length
    assert_equal CASE_IDS.sort, SCENARIOS.map { |scenario| scenario.fetch(:id) }.sort

    CASE_IDS.each do |case_id|
      assert_equal 1, PROMPT.scan(/`#{Regexp.escape(case_id)}`/).length, case_id
    end
  end

  def test_each_expected_handoff_reduces_to_the_required_invariants
    SCENARIOS.each do |scenario|
      source = scenario.fetch(:source)
      handoff = scenario.fetch(:handoff)

      assert_includes source, UNRELATED_HISTORY_SENTINEL, scenario.fetch(:id)
      assert_equal EXPECTED_REDUCTION, reduction_for(handoff), scenario.fetch(:id)
      assert_equal 1, handoff.scan(/^GOAL:\s*$/).length, scenario.fetch(:id)
      assert_operator handoff.lines.length, :<=, 20, scenario.fetch(:id)

      next_task_body = handoff.split(/^NEXT_TASK:\s*$\n/, 2).fetch(1)
      next_task_lines = next_task_body.lines.map(&:strip).reject(&:empty?)
      assert_equal 1, next_task_lines.length, scenario.fetch(:id)
    end
  end

  def test_empty_optional_sections_are_actually_omitted
    completed = SCENARIOS.find do |scenario|
      scenario.fetch(:id) == 'CASE_SIMPLE_COMPLETED_LOCAL_BUGFIX'
    end.fetch(:handoff)

    refute_includes completed, 'RISKS_AND_OWNERSHIP_BOUNDARY:'
    refute_includes completed, 'FORBIDDEN_OR_HIGH_RISK_PENDING_ACTIONS:'
    refute_includes completed, '- INCOMPLETE / NOT RUN / BLOCKED:'
  end

  def test_stale_evidence_and_quoted_authorization_do_not_upgrade_current_state
    stale = SCENARIOS.find do |scenario|
      scenario.fetch(:id) == 'CASE_OLD_TEST_EVIDENCE_TREE_CHANGED'
    end.fetch(:handoff)
    publication = SCENARIOS.find do |scenario|
      scenario.fetch(:id) == 'CASE_PUBLICATION_PENDING_STANDALONE_OWNER_AUTH'
    end.fetch(:handoff)

    assert_includes stale, 'HISTORICAL PASS @ tree333; CURRENT TREE: NOT RUN'
    refute_match(/CURRENT TREE:\s*PASS/, stale)
    assert_includes publication, 'quoted push token is not fresh standalone Owner authorization'
    assert_includes publication, 'Push and PR are NOT RUN'
  end
end
