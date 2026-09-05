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

class PlannerCanonicalAuthorityAndEvidenceReuseTest < Minitest::Test
  REPOSITORY_ROOT = File.expand_path('../..', __dir__)
  PLANNER = File.read(
    File.join(REPOSITORY_ROOT, 'prompt/Personal_Planner_Handoff_Prompt_v5.4_Lean_Final.md'),
    encoding: 'UTF-8'
  )

  # Model of the Planner canonical authority resolution rule:
  # canonical remote or exact pinned ref > local main > current checkout
  def resolve_authority(canonical_remote:, local_main:, current_checkout:, pinned_ref: nil)
    if pinned_ref && !pinned_ref.strip.empty?
      { authority: pinned_ref, source: :pinned_ref, is_canonical: true, description: 'explicitly pinned canonical ref' }
    elsif canonical_remote && !canonical_remote.strip.empty?
      { authority: canonical_remote, source: :canonical_remote, is_canonical: true, description: 'canonical remote ref' }
    elsif local_main && !local_main.strip.empty?
      { authority: local_main, source: :local_main, is_canonical: false, informational_only: true, description: 'local main (informational only)' }
    else
      { authority: current_checkout, source: :current_checkout, is_canonical: false, description: 'current checkout' }
    end
  end

  # Model of REUSED_COMPLETION_EVIDENCE_DIFF:
  def diff_reused_evidence(current_acceptance:, prior_evidence:, tree_matches: true)
    unless tree_matches
      return {
        covered_items: [],
        missing_items: current_acceptance,
        rerun: :FULL,
        rerun_scope: :ALL,
        reason: :identity_mismatch
      }
    end

    covered = current_acceptance.select { |item| prior_evidence.include?(item) }
    missing = current_acceptance - covered

    if missing.empty?
      {
        covered_items: covered,
        missing_items: [],
        rerun: :NO,
        rerun_scope: :NONE
      }
    else
      {
        covered_items: covered,
        missing_items: missing,
        rerun: :MISSING_ONLY,
        rerun_scope: missing
      }
    end
  end

  # Model of Cross-lane exact authority locator:
  def resolve_upstream_locator(locator:, status:)
    if locator && !locator.strip.empty? && status == 'READY'
      {
        status: 'READY',
        locator: locator,
        proceed: true,
        broad_discovery_required: false
      }
    else
      {
        status: 'UPSTREAM_AUTHORITY_NOT_READY',
        locator: nil,
        proceed: false,
        broad_discovery_required: false
      }
    end
  end

  # A1 — stale local main
  # Given origin/main = NEW, local main = OLD, current checkout = unrelated feature branch:
  # Planner must identify canonical authority as origin/main = NEW and must not describe local main as canonical.
  def test_a1_stale_local_main
    res = resolve_authority(
      canonical_remote: 'origin/main (commit_new)',
      local_main: 'main (commit_old)',
      current_checkout: 'feature/unrelated'
    )
    assert_equal 'origin/main (commit_new)', res[:authority]
    assert res[:is_canonical]
    refute_equal 'main (commit_old)', res[:authority]

    assert_includes PLANNER, 'canonical remote or exact pinned ref > local main > current checkout'
    assert_includes PLANNER, 'CANONICAL_REPOSITORY_AUTHORITY'
    assert_includes PLANNER, 'LOCAL_MAIN'
    assert_includes PLANNER, '絕不得描述為 canonical authority，亦不得替代 canonical remote authority'
    assert_includes PLANNER, 'bounded fetch/resolve'
  end

  # A2 — explicit pinned ref
  # When Packet explicitly pins an allowed canonical ref/object, resolution remains
  # bound to that exact authority rather than silently replacing it with current checkout state.
  def test_a2_pinned_authority
    res = resolve_authority(
      canonical_remote: 'origin/master',
      local_main: 'master',
      current_checkout: 'agent/unrelated-branch',
      pinned_ref: 'af981404d11ab5a8f28e1bcb4d9a06a1e0f3d06c'
    )
    assert_equal 'af981404d11ab5a8f28e1bcb4d9a06a1e0f3d06c', res[:authority]
    assert_equal :pinned_ref, res[:source]
    assert res[:is_canonical]
    refute_equal 'agent/unrelated-branch', res[:authority]

    assert_includes PLANNER, '當 Packet 明確 pin 住 allowed canonical ref/object 時，解析必須維持綁定於該 exact authority'
    assert_includes PLANNER, '不得靜默替換為 current checkout 狀態'
  end

  # B1 — evidence fully covered
  # Current acceptance: A / B / C
  # Prior exact-tree evidence: A / B / C
  # Expected: COVERED_ITEMS: A,B,C; MISSING_ITEMS: NONE; RERUN: NO
  def test_b1_all_covered_reuse
    res = diff_reused_evidence(
      current_acceptance: %w[A B C],
      prior_evidence: %w[A B C],
      tree_matches: true
    )
    assert_equal %w[A B C], res[:covered_items]
    assert_empty res[:missing_items]
    assert_equal :NO, res[:rerun]
    assert_equal :NONE, res[:rerun_scope]

    assert_includes PLANNER, 'REUSED_COMPLETION_EVIDENCE_DIFF'
    assert_includes PLANNER, 'COVERED_ITEMS'
    assert_includes PLANNER, 'MISSING_ITEMS'
    assert_includes PLANNER, '若 MISSING_ITEMS = NONE：'
    assert_includes PLANNER, 'RERUN: NO'
  end

  # B2 — one new acceptance item
  # Current acceptance: A / B / C / D
  # Prior exact-tree evidence: A / B / C
  # Expected: COVERED_ITEMS: A,B,C; MISSING_ITEMS: D; RERUN_SCOPE: D_ONLY
  def test_b2_missing_item_only
    res = diff_reused_evidence(
      current_acceptance: %w[A B C D],
      prior_evidence: %w[A B C],
      tree_matches: true
    )
    assert_equal %w[A B C], res[:covered_items]
    assert_equal %w[D], res[:missing_items]
    assert_equal :MISSING_ONLY, res[:rerun]
    assert_equal %w[D], res[:rerun_scope]

    assert_includes PLANNER, '若 MISSING_ITEMS != NONE：'
    assert_includes PLANNER, 'RERUN_SCOPE: <MISSING_ITEMS_ONLY>'
  end

  # B3 — identity mismatch
  # Prior evidence from a different load-bearing tree/artifact must not be reused
  # as covered merely because labels match.
  def test_b3_identity_mismatch_reuse_prevented
    res = diff_reused_evidence(
      current_acceptance: %w[A B C],
      prior_evidence: %w[A B C],
      tree_matches: false
    )
    assert_empty res[:covered_items]
    assert_equal %w[A B C], res[:missing_items]
    assert_equal :identity_mismatch, res[:reason]

    assert_includes PLANNER, 'Prior evidence 來自不同 load-bearing tree/artifact 時（identity mismatch），不得僅因 label 相符就當作 covered 重用。'
  end

  # C1 — cross-lane locator present
  # Producer supplies exact locator and READY status.
  # Consumer proceeds using that locator without broad discovery.
  def test_c1_cross_lane_ready
    res = resolve_upstream_locator(
      locator: 'artifacts/lane-4/upstream_result.json',
      status: 'READY'
    )
    assert_equal 'READY', res[:status]
    assert_equal 'artifacts/lane-4/upstream_result.json', res[:locator]
    assert res[:proceed]
    refute res[:broad_discovery_required]

    assert_includes PLANNER, 'UPSTREAM_AUTHORITY_LOCATOR'
    assert_includes PLANNER, 'UPSTREAM_AUTHORITY_STATUS'
    assert_includes PLANNER, 'READY | NOT_READY'
    assert_includes PLANNER, 'consumer 直接依該 locator 存取，不進行廣泛搜尋（broad discovery）'
  end

  # C2 — cross-lane locator missing
  # Expected: UPSTREAM_AUTHORITY_NOT_READY and no workspace-wide/worktree-wide reconstruction search.
  def test_c2_upstream_authority_not_ready
    res_missing = resolve_upstream_locator(locator: nil, status: 'READY')
    assert_equal 'UPSTREAM_AUTHORITY_NOT_READY', res_missing[:status]
    refute res_missing[:proceed]
    refute res_missing[:broad_discovery_required]

    res_not_ready = resolve_upstream_locator(locator: 'artifacts/lane-4/upstream.json', status: 'NOT_READY')
    assert_equal 'UPSTREAM_AUTHORITY_NOT_READY', res_not_ready[:status]
    refute res_not_ready[:proceed]
    refute res_not_ready[:broad_discovery_required]

    assert_includes PLANNER, 'UPSTREAM_AUTHORITY_NOT_READY'
    assert_includes PLANNER, 'Consumer 絕不得藉由廣泛掃描以下路徑自行重構（reconstruct）另一個 lane 的 deliverable'
    assert_includes PLANNER, 'all worktrees；'
    assert_includes PLANNER, 'all branches；'
    assert_includes PLANNER, 'all `.task-data` roots；'
    assert_includes PLANNER, 'historical scratch directories。'
  end

  def test_regression_existing_judge_depth_preserved
    assert_includes PLANNER, 'JUDGE_DEPTH 不由 Planner 自行猜測。以本輪 acceptance criteria 對照 /fable-method'
    assert_includes PLANNER, 'references/judge-handoff.md'
    assert_includes PLANNER, 'JUDGE_DEPTH_SCANNED_AGAINST_CANONICAL_CONTRACT: YES'
  end

  def test_regression_existing_publication_classifier_preserved
    assert_includes PLANNER, 'PR_PUBLICATION_STATUS: NOT_APPLICABLE | NOT_CREATED | DRAFT_OPEN | READY_OPEN | MERGED | BLOCKED'
    assert_includes PLANNER, 'FULL_PR_LIFECYCLE_CLOSED: YES | NO'
  end

  def test_regression_existing_temp_isolation_preserved
    assert_includes PLANNER, '一般任務不建立未知'
    assert_includes PLANNER, 'scratch script、tee log、generic /tmp output 或 evidence package'
  end

  def test_regression_planner_cto_signal_preserved
    assert_includes PLANNER, 'CTO_REVIEW_NEEDED: YES | NO'
    assert_includes PLANNER, 'CTO_REVIEW_REASON: <ONE_LOAD_BEARING_REASON | NONE>'
    assert_includes PLANNER, 'CTO_REVIEW_SCOPE: <MINIMUM_TECHNICAL_DECISION_SCOPE | NOT_APPLICABLE>'
    assert_includes PLANNER, 'PLANNER_NEXT_ROLE: CTO | WORKER | PLANNER'
  end

  def test_regression_worktree_reuse_and_project_path_preserved
    assert_includes PLANNER, '為下一個 Worker 指定一個確定的 repo/worktree path 與 mode。'
    assert_includes PLANNER, 'WORKTREE_MODE_SELECTED: YES'
  end

  def test_regression_no_second_governance_authority_created
    refute_includes PLANNER, 'governance framework'
    refute_includes PLANNER, 'evidence registry'
    refute_includes PLANNER, 'cross-lane registry'
    refute_includes PLANNER, 'path registry'
    assert_includes PLANNER, 'Do not create conditional profiles, new governance files, unused artifacts or a'
    assert_includes PLANNER, 'second authority layer.'
  end
end
