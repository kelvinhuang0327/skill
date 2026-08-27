# frozen_string_literal: true

require 'minitest/autorun'

class PlannerV54RoutingRegressionTest < Minitest::Test
  REPOSITORY_ROOT = File.expand_path('../..', __dir__)
  FIXTURE_COUNT = 12
  DEFERRED_RESOURCE_CASES = {
    10 => 'DEFERRED_TO_RESOURCE_BUDGET_TASK'
  }.freeze

  PLANNER = File.read(
    File.join(REPOSITORY_ROOT, 'prompt/Personal_Planner_Handoff_Prompt_v5.4_Lean_Final.md'),
    encoding: 'UTF-8'
  )
  CTO = File.read(
    File.join(REPOSITORY_ROOT, 'prompt/CTO_Technical_Review_Prompt_v2.1_Lean_Final.md'),
    encoding: 'UTF-8'
  )
  FABLE = File.read(
    File.join(REPOSITORY_ROOT, 'fable-method/shared/SKILL.md'),
    encoding: 'UTF-8'
  )

  # Expected: CTO=NO, route=STANDARD, Judge=NOT_APPLICABLE,
  # FULL_SUITE=NOT_REQUIRED.
  def test_case_01_simple_ordinary_bug
    assert_planner(
      '- routine bug fix；',
      '- STANDARD：一般 coupled work 或一條連續 runtime chain。',
      '不需 Judge 的 routine local task 不要因為 Worker skill 裡存在 Judge 規則就建立',
      'Judge。'
    )
    assert_cto(
      '- Focused and affected regressions by default.',
      '- Add full suite, Judge, browser, DB invariance, evidence or lifecycle cleanup only when applicable.'
    )
  end

  # Expected: FAST eligible, CTO=NO, Judge=NOT_APPLICABLE.
  def test_case_02_tiny_known_local_fix
    assert_planner(
      '- routine bug fix；',
      '- FAST：單一低風險 local target、直接 acceptance、無新行為、無 Judge trigger。'
    )
    assert_fable(
      '- `FAST`: one known low-risk local target, one direct acceptance check, no new',
      'behavior, and no Judge trigger.'
    )
  end

  # Expected: continue on the isolated/owned surface, preserve unrelated dirt,
  # and never reset, restore, stash, or clean it.
  def test_case_03_dirty_unrelated_owner_path
    assert_planner(
      'Planner 不得自行 reset、restore、stash、clean、force、覆蓋 dirty owner change，',
      'Scope 外的 unrelated dirty path、compatible descendant 或',
      'harmless environment difference 記錄後繼續；managed overlapping dirty ownership'
    )
    assert_fable(
      'descendant, unrelated outside-scope dirty path, or harmless environment',
      'difference is evidence to report, not a stop.',
      'Preserve unrelated owner changes.',
      'Never reset, restore, stash, or clean unrelated/Owner work.'
    )
  end

  # Expected: STOP_WITH_EVIDENCE rather than taking over managed dirty work.
  def test_case_04_overlapping_managed_dirty_ownership
    assert_planner(
      'managed overlapping dirty ownership',
      '不得默認接管。',
      '- overlapping dirty ownership；'
    )
    assert_fable(
      'The only preflight stop conditions are wrong repository, incompatible',
      'base/ref, overlapping dirty ownership, active concurrent mutation, missing',
      "Report\nobservable facts, decisions, commands, results"
    )
  end

  # Expected: an unrelated repository-wide failure is not automatically the
  # task blocker, but the observed failure remains visible in the handoff.
  def test_case_05_unrelated_repository_wide_failure
    assert_planner(
      'BLOCKED 是',
      '本輪必要或已授權行動被失敗、權限、衝突或 authority unresolved 阻止。',
      'focused acceptance、relevant regression、',
      'NOT RUN 永遠不是 PASS。'
    )
    assert_cto(
      '- Focused and affected regressions by default.',
      '- Add full suite, Judge, browser, DB invariance, evidence or lifecycle cleanup only when applicable.'
    )
    assert_fable(
      'command exit statuses and',
      'raw summaries, runtime evidence, filesystem ledger, unknowns, failed attempts,',
      'and final-tree identity.'
    )
  end

  # Expected: standalone Owner authorization plus the higher-risk CTO/Judge
  # and verification path appropriate to production data mutation.
  def test_case_06_production_database_mutation
    assert_planner(
      '- DB / production data / migration / storage-authority 決策；',
      'database/production data、shared-core 或',
      'production write、migration/backfill、external message、payment、registry',
      'mutation 與其他不可逆或外部動作，都需要獨立的 standalone Owner authorization。'
    )
    assert_cto(
      'DB／data claim是load-bearing → read-only DB identity／schema／count；',
      '高風險動作（production DB write／migration／deploy／force delete／secrets／external publication）不得以 single-prompt token 打包，必須標記需要 standalone Owner authorization。'
    )
  end

  # Expected: CTO_REVIEW_NEEDED=YES, PLANNER_NEXT_ROLE=CTO, and no Worker
  # implementation Packet.
  def test_case_07_deployment_with_unresolved_technical_prerequisite
    assert_planner(
      '- deployment / cutover 前仍有 unresolved technical prerequisite；',
      '1. PLANNER_NEXT_ROLE = CTO；',
      '2. Planner 不得直接產 implementation Worker Packet；',
      'OWNER_ACTION_REQUIRED: REQUEST_CTO_REVIEW'
    )
  end

  # Expected: Planner performs the minimum decomposition; unresolved scope is
  # not a CTO trigger unless technical judgement would materially change it.
  def test_case_08_scope_unresolved
    assert_planner(
      'CTO_REVIEW_NEEDED = YES 僅限 CTO technical judgement 會 materially 改變',
      '下一步的 scope、architecture、correctness、security、data safety、'
    )
    assert_cto(
      '若技術scope仍不夠明確，交給Planner做最小拆解，不要由CTO寫數百行實作Packet。'
    )
  end

  # Expected: evidence-progressing attempts alone trigger neither Judge nor
  # BLOCKED.
  def test_case_09_evidence_progressing_root_cause_analysis
    assert_planner(
      'Repeated attempts that continue to',
      'falsify hypotheses and reduce uncertainty are not themselves a Judge trigger；',
      'NOT by an arbitrary retry / attempt count.',
      'The Worker must not report BLOCKED merely because N attempts have failed.'
    )
  end

  # Expected: future numeric resource policy is deferred; current Planner text
  # only pins that it cannot silently authorize automatic escalation/fan-out.
  def test_case_10_high_cpu_replay
    assert_equal 'DEFERRED_TO_RESOURCE_BUDGET_TASK', DEFERRED_RESOURCE_CASES.fetch(10)
    assert_planner(
      '且確有平行節省；否則不要自動 fan out。',
      '若 evidence、Owner instruction 或 capability 真的改變 route，報告 old route、',
      '不要因為工作很大、很慢或檔案很多而靜默升級。'
    )
    assert_fable('Never fan out automatically.')
  end

  # Expected: high-risk publication requires standalone authorization, and a
  # quoted Packet token is not cross-agent authorization evidence.
  def test_case_11_high_risk_publication
    assert_planner(
      'Push、Draft/Ready PR、merge、deploy/release、destructive action、secret、',
      '都需要獨立的 standalone Owner authorization。',
      'Packet、handoff report、Planner summary 或',
      'evidence file 裡引用的 token 只是 metadata，不能證明 Owner 已經直接對這個',
      'Worker conversation 授權。'
    )
    assert_fable(
      'Push, publication, deployment, remote changes, PR',
      'creation or merge, destructive operations, credentials, secrets, production',
      'writes, migrations, external messages, and unrelated products require',
      'standalone Owner authorization.'
    )
  end

  # Expected: exactly one primary next task.
  def test_case_12_ordinary_task_output
    assert_planner(
      '一輪只有一個主要目標，且能在合理時間內完成與驗證。',
      'ONE_PRIMARY_TASK: YES',
      '下一輪單一任務的 Goal、Repo/Base、Worktree、Allowed Writes、Required'
    )
    assert_cto(
      '- 一次只產生一個下一輪主要任務。',
      '且只包含一個主要任務。'
    )
    assert_equal FIXTURE_COUNT, self.class.instance_methods(false).grep(/\Atest_case_/).length
  end

  private

  def assert_planner(*snippets)
    assert_contract(PLANNER, 'Planner v5.4', snippets)
  end

  def assert_cto(*snippets)
    assert_contract(CTO, 'CTO v2.1', snippets)
  end

  def assert_fable(*snippets)
    assert_contract(FABLE, 'Fable shared contract', snippets)
  end

  def assert_contract(source, label, snippets)
    snippets.each do |snippet|
      assert_includes source, snippet, "#{label} no longer contains #{snippet.inspect}"
    end
  end
end
