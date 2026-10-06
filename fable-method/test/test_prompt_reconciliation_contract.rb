# frozen_string_literal: true
require 'minitest/autorun'
require 'digest'
require_relative 'support/contract_fixtures'

class PromptReconciliationContractTest < Minitest::Test
  include ContractFixtures
  ROOT = File.expand_path('../..', __dir__)
  SHARED = File.join(ROOT, 'fable-method/shared')

  def test_judge_containment_is_scoped_read_only_and_remains_worker_owned
    skill = File.read(File.join(SHARED, 'SKILL.md'))
    handoff = linked_reference(skill, 'judge-handoff.md')
    section = handoff.split('## Containment and memory evaluation', 2).last.split('## Verdict', 2).first
    assert_guarded_contract(section, [
      'When containment or memory writes are load-bearing for the Packet, evaluate those boundaries read-only',
      "Compare only the current task's T0/T1 interval;",
      'unattributed sibling changes are report-only unless they affect canonical state or a required input.',
      'exact path and purpose authorization.',
      'Do not broaden this into an automatic workspace or memory audit.',
      'The Judge reports observed violations; it does not create, remove, or repair them.',
      'Remediation stays Worker-owned',
      'This check adds no Judge trigger or authorization.'
    ], weakenings: {'read-only' => 'with repair permissions', 'report-only' => 'automatic failure'})
    assert_guarded_contract(linked_reference(skill, 'memory-boundary.md'), [
      'Memory is context only and never repository authority.',
      'unless the Packet authorizes the exact path and purpose;'
    ])
    assert_guarded_contract(linked_reference(skill, 'workspace-containment.md'), [
      'CREATE_EXTERNAL_WORKSPACE: YES', 'EXACT_ABSOLUTE_PATH:', 'CLEANUP_DISPOSITION:',
      'Do not modify, move, or delete any pre-existing sibling directory.'
    ])
    %w[codex claude gemini antigravity].each do |platform|
      actual = File.read(File.join(ROOT, 'fable-method/platforms', platform, 'fable-method/references/judge-handoff.md'))
      assert_equal handoff, actual
    end
  end

  def test_planner_path_and_continuation_guards_do_not_expand_authority
    planner = File.read(File.join(ROOT, 'prompt/Personal_Planner_Handoff_Prompt_v5.4_Lean_Final.md'))
    assert_guarded_contract(planner, [
      'Write paths 必須依 exact Packet path、適用 repository instructions 或既有 canonical module/documentation convention 解析；',
      'STOP: PROJECT_WRITE_PATH_AUTHORITY_UNRESOLVED。',
      'local commit authorization 不會擴張 write root。',
      'Continuation 繼承原 worktree/root、canonical path convention 與 resource policy。',
      'UPDATED_ALLOWLIST 不會自行授權新 hierarchy、external workspace 或 concurrency escalation；'
    ])
    assert_includes planner, 'v5.4 is the successor'
    flow = File.read(File.join(ROOT, 'prompt/AI_Engineering_Process_Flow_v1.1.1_Lean_Final.md'))
    assert_includes flow, '`Personal_Planner_Handoff_Prompt_v5.4_Lean_Final.md`'
    refute_includes flow, '`Personal_Planner_Handoff_Prompt_v5.4.1_Lean_Final.md`'
  end

  def test_active_role_handoffs_inherit_owner_direct_packet_without_self_authorization
    roles = %w[CTO_Technical_Review_Prompt_v2.1_Lean_Final.md CEO_Decision_Review_Prompt_v2.1_Lean_Final.md]
    roles.each do |name|
      role = name.split('_').first
      text = File.read(File.join(ROOT, 'prompt', name))
      assert_guarded_contract(text, [
        "不得由 #{role}-generated token 自行授權；",
        'exact action/target 的 direct Owner authorization。',
        '一則 direct Owner user message 可同時附授權與 executable Packet；',
        'prior direct Owner authorization 可重用，不要求額外 auth-only message。'
      ], weakenings: {"不得由 #{role}-generated token 自行授權；" => "可由 #{role} 自行授權；"})
      refute_includes text, 'standalone Owner authorization'
    end
    handoff = File.read(File.join(ROOT, 'prompt/Personal_Web_Conversation_Handoff_Prompt_v3.1_Lean_Final.md'))
    assert_guarded_contract(handoff, [
      '不要把 quoted authorization 寫成已授權。',
      'exact action/target 授權與 executable Packet；',
      '仍適用且未被 supersede 的 prior direct Owner authorization 可重用，不要求額外 auth-only message。'
    ], weakenings: {'不要把 quoted authorization 寫成已授權。' => '把 quoted authorization 寫成已授權。'})
    flow = File.read(File.join(ROOT, 'prompt/AI_Engineering_Process_Flow_v1.1.1_Lean_Final.md'))
    assert_guarded_contract(flow, [
      '若與角色文件衝突，以 Planner v5.4 的 Authority／Authorization 規則為準。',
      '高風險任務必須 direct Owner authorization。',
      'exact action/target 與 provenance 依 canonical Planner §4.3；'
    ])
  end

  def test_historical_e399_prompt_bytes_are_preserved_exactly
    assert_equal '5e116e50f347a126fa7c3af19c93c80648d7fd8461ff55b34310e5bf336d44e6', Digest::SHA256.file(File.join(ROOT, 'prompt/old/e399412', 'Personal_Planner_Handoff_Prompt_v5.3.3_Lean_Final.md')).hexdigest
    assert_equal '62eccabf6210eab85dc47f0b898e83e033278804f4fc59c478c51c19e36677f4', Digest::SHA256.file(File.join(ROOT, 'prompt/old/e399412', 'Personal_Planner_Handoff_Prompt_v5.3.2_Lean_Final.md')).hexdigest
    assert_equal '88c86b2d46a280c5990f2e8bbc57d8cd4e5d3efd454c0f6a47fd8d6d5b4f35f1', Digest::SHA256.file(File.join(ROOT, 'prompt/old/e399412', 'Personal_Planner_Handoff_Prompt_v5.4.1_Lean_Final.md')).hexdigest
    assert_equal '49926d8c444663cb82e86a3e29587a240a4c15e16d57deba368360872a1bfea0', Digest::SHA256.file(File.join(ROOT, 'prompt/old/e399412', 'AI_Engineering_Process_Flow_v1.1.1_Lean_Final.md')).hexdigest
  end
end
