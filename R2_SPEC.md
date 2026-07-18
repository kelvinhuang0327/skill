# R2.1 Implementation Spec — R2 Control Plane + Project Attachment Continuation

```
control_plane_version: 1.1.0-draft.1
date: 2026-07-17
status: DRAFT_FOR_OWNER_REVIEW — 本檔為 R2.1 交付說明文件,非 durable rule file。
boundary: 四份 legacy prompt 維持 byte-identical;四份 compiled role prompts 只作離線
          VNext owner-review candidates。未啟動 pilot / activation,未寫 memory / DB,
          未使用 network / remote / push / PR。
```

---

## 1. 檔案清單與分工

**Durable files(恰 5 個)**:

| 檔案 | 唯一權責 |
|---|---|
| `AGENT_CORE.md` | evidence states(含 STALE / OUTDATED)/ task-status / Phase 0A-0B / STOP-WARN / 風險三級 / authorization matrix / 四層模型 / attachment / precedence / Memory Contract |
| `ROLE_PROFILES.md` | 6 個 thin profiles(Handoff Reporter、CTO、CEO、Planner/Compiler、Worker、Independent Reviewer) |
| `ROUTING_AND_LIFECYCLE.md` | routing 決策表 / reviewer 需求 / CEO-CTO cadence / worktree 3 modes + reattach / 狀態機 S0–S11 / 不變式 I1–I4 / cleanup 政策 / Owner override |
| `TASK_MANIFEST.schema.yaml` | manifest schema + lint L1–L22 |
| `WORKER_TASK_TEMPLATE.md` | 渲染規則 R1–R10 / Worker prompt 模板 / active_task projection §P / compiled prompt build 規則 §C |

**Build outputs(不計入 5 檔、不得手工維護)**:`compiled/HANDOFF_REPORTER.compiled.md`、`compiled/CEO_DECISION_REVIEW.compiled.md`、`compiled/CTO_TECHNICAL_REVIEW.compiled.md`、`compiled/PLANNER_COMPILER.compiled.md`、各 repo 的 `active_task.md` view、每次渲染出的 Worker prompt。四份 role prompt 是 inactive VNext owner-review candidates,不代表 activation。CEO / CTO 可使用其 compiled output 作 read-only web review;無 local repo access 時 evidence 為 `UNKNOWN / NOT_RUN`,不得宣稱 independent repo audit。

**Lean compilation profiles**:capsule 是 compiler selection concept,不是第六個 durable source。每個 capsule 都由五檔既有來源的 identified section / schema 結構機械抽取或 compact;validation 逐 capsule 與 fresh build 比對。

| Role | Selected capsules | Hard maximum |
|---|---|---:|
| Handoff | EVIDENCE_MIN / PRECEDENCE_MIN / MEMORY_READ_MIN / MEMORY_CANDIDATE_MIN / REVIEW_BOUNDARY_MIN | 8,872 bytes |
| CEO | EVIDENCE_MIN / PRECEDENCE_MIN / AUTH_ESCALATION_MIN / MEMORY_READ_MIN | 12,509 bytes |
| CTO | EVIDENCE_MIN / PRECEDENCE_MIN / ATTACHMENT_MIN / MEMORY_READ_MIN / AUTH_ESCALATION_MIN | 10,530 bytes |
| Planner | 上述必要 shared capsules + ROUTING_DECISION_MIN / WORKTREE_COMPILER / MANIFEST_COMPILER / WORKER_COMPILER | 21,919 bytes |

Aggregate hard maximum = 53,830 bytes;preferred target = 50,000 bytes。Planner 的 manifest capsule 只含 derived schema shape + compact L1–L22,不得嵌入 full YAML;Worker capsule 只含 render contract、authorization variants、section order 與 active-task projection,不得重複 full template 或 shared capsules。

**Targeted semantic-repair guards**:`MEMORY_CANDIDATE_MIN` 機械抽取 AGENT_CORE §14 規則 7/9/10,不新增 durable source。`validate-role` 固定依 structural → hard size → shared vocabulary/fingerprint → role semantic → forbidden capability → deterministic/provenance 次序驗證;byte equality 只在最後作 provenance check,不得提前成功。穩定錯誤碼為 `ROLE_HANDOFF_MEMORY_CORE_V1_INCOMPLETE`、`ROLE_HANDOFF_MEMORY_WRITE_BOUNDARY_MISSING`、`ROLE_PLANNER_ACTIVE_TASK_P3_INCOMPLETE`、`ROLE_AUTH_METADATA_BOUNDARY_MISSING`、`ROLE_AUTH_EXECUTABLE_ACTIVATION_BOUNDARY_MISSING`、`ROLE_CTO_ATTACHMENT_DISCOVERY_INCOMPLETE` 與 byte-equal regression 的 `ROLE_VALIDATOR_SEMANTIC_BYPASS`。

**Examples(示範,非規則)**:`examples/agent-profile.template.yaml`、`manifest-low.yaml`、`manifest-medium.yaml`、`manifest-high.yaml`。

**Shared vs repo-local 分工**(細節見 AGENT_CORE §10):Shared Control Plane = 跨專案政策與詞彙的唯一 source of truth;repo-local `.ai` = 專案 identity、context、memory 與額外限制。agent-profile 只可放 canonical repo/branch、control-plane binding、risk 上調、protected paths、DB/data/runtime 限制、test aliases、worktree path defaults、publication restrictions。禁止鍵出現即無效並記 `[Risk]`。

---

## 2. R2.1 Four-Layer Architecture 與 Project Attachment

| Layer | Source | Contract |
|---|---|---|
| L1 Shared Control Plane | 本目錄 5 個 durable source-of-truth files | 只放跨專案 policy;受版本與 schema binding 約束;不得集中專案 memory |
| L2 Repo-local Project Attachment | `<repo>/.ai/` 四個 context/memory files + `agent-profile.yaml` | 專案身分、限制、test/path defaults 與歷史 memory;不得重定義 Shared Core |
| L3 Current Task Manifest | 每 task 恰一份 manifest | 本輪 scope、steps、tests、authorization、lifecycle、memory policy 的唯一 source of truth |
| L4 Live State | repo / GitHub / CI / DB / data / runtime observation | 當下事實的最高順位來源;不得由 handoff 或 memory 覆蓋 |

固定 attachment flow:

```
ATTACHMENT_DISCOVERY
→ CONTROL_PLANE_VERSION_RESOLUTION
→ PHASE_0A_LIVE_STATE
→ PHASE_0B_PROJECT_CONTEXT_LOAD
→ MEMORY_RELEVANCE_SELECTION
→ ACTIVE_MANIFEST_RESOLUTION
→ ROUTING
→ EXECUTION_OR_STOP
```

- `.ai` 或 agent-profile 缺失 → `ENTRY_CHECK / BOOTSTRAP_READINESS`(LOW、NOT_APPLICABLE、read-only),不得直接實作。
- required context 缺失 → 依 manifest `missing_context_policy` route 到 entry check 或 STOP。
- control-plane version mismatch → `A_VERSION_MISMATCH` STOP;schema mismatch → `A_SCHEMA_MISMATCH` STOP。
- profile、context、memory 或 manifest 指向其他 repo → `A_CROSS_PROJECT` STOP,不得載入。
- memory/handoff 與 live state drift → live 優先;未綁 current head = `STALE`,已被 live state 反證 = `OUTDATED`;若任務前提仍有效則 WARN,否則 STOP。

**Policy precedence**(高至低):

1. non-overridable Shared Core safety invariants
2. current explicit Owner authorization
3. current task manifest
4. repo-local `.ai` restrictions
5. Shared Core defaults

**Fact precedence**(高至低):

1. live repo / GitHub / DB / runtime observation
2. current-head-bound evidence
3. current-task handoff
4. MEMORY_LOG historical entries

Repo-local profile 與 manifest 只能縮小 scope 或增加限制;memory/handoff 不得證明或覆蓋 current state。

---

## 3. personal-ai-flow Compatibility Mapping

| personal-ai-flow artifact / behavior | R2.1 mapping |
|---|---|
| `.ai/ai-context/PROJECT_PROFILE.md` | L2 project identity 與長期邊界;Phase 0B bounded load |
| `.ai/ai-context/PROJECT_CONTEXT.md` | L2 project-specific context;不得成為 authorization |
| `.ai/ai-context/RUNBOOK.md` | L2 repo-specific operational guidance;不得放寬 L1 safety invariant |
| `.ai/ai-memory/MEMORY_LOG.md` | L2 append-only historical context;bounded retrieval;每 repo 隔離 |
| `.ai/agent-profile.yaml` | L2 attachment binding:canonical repo/branch、Shared Control Plane path/version/schema、restrictions/default paths |
| `active_task.md` | 只在 enabled 時由 L3 manifest 投影的 compatibility build output;不是 source of truth |
| 舊式整份 memory / context 注入 | 廢止;改 selectors + `max_entries` 的 task-relevant selection |
| repo-local policy copy | 廢止;repo `.ai` 只引用 L1,不得重定義 evidence/auth/routing/lifecycle/finals |

---

## 4. Memory Contract

1. MEMORY_LOG 是歷史上下文,不是 authorization,也不能證明 current Git/PR/CI/DB/runtime state。
2. 未綁 current head 的 historical PASS = `STALE`;被 live state 反證 = `OUTDATED`;兩者都不得作本輪 PASS。
3. 讀取必須以 `mode + selectors + max_entries` 有界且與 task relevant;禁止整份預載。
4. A 專案 memory 不得進 B 專案的 prompt、handoff 或 manifest。
5. 預設 `memory.write.mode: forbidden`;只有 manifest 明列 `allowed`、exact path、purpose、`core_v1`,且 task authorization 生效時 Worker 才可 append。
6. Handoff Reporter 只能提出 `CANDIDATE — NOT WRITTEN`;Planner 可提案 write policy,但不能製造授權。
7. 修正採 append-only superseding entry;不得原地改寫歷史事實。

---

## 5. Schema、Lint L15–L22 與 active_task Projection

R2.1 schema 新增 `control_plane`、`project_attachment`、`memory`、`context`、`active_task_projection`;並補齊 Worker rendering 所需的 `goal`、`steps`、`success_criteria` 與 `authorization.high_risk_reason`。Allowed enum / fixed value 均在 schema 旁明列。

| Lint | Coverage |
|---|---|
| L15 | profile path + 四個 required `.ai` files + profile 禁止鍵檢查 |
| L16 | missing `.ai` / profile / required-context routing |
| L17 | control-plane version 與 schema compatibility |
| L18 | bounded memory mode / selectors / `max_entries` |
| L19 | memory write path / purpose / entry schema / task authorization |
| L20 | repo-local memory 與 cross-project isolation |
| L21 | live-state + context/evidence head binding fixed true |
| L22 | projection enabled/disabled consistency + actual manifest-SHA drift |

L1–L14 保持原意且未放寬。L15 同時拒絕 profile 內的 Shared Core redefinition,供 T22 驗收。

`active_task.md` 規則:

- 只有 Planner 且 `enabled=true` 時可產生;`enabled=false` 不得建立或更新。
- 內容是 manifest 嚴格子集,含 AUTO-GENERATED banner、source、control-plane version 與**實際 manifest SHA-256**。
- Planner 每次 compile、Reviewer 每次 review 都重投影並逐字比較;banner/hash 缺失、hash 不符或 diff 均為 `DRIFT`,manifest 永遠勝出。
- 這是 build output,不增加第六個 durable policy file。

---

## 6. Backward Compatibility Mapping(legacy → 新架構)

| Legacy 條款 | 新歸屬 |
|---|---|
| Handoff v2:Critical Boundary / Project Filtering / Traceability Rule / 時間線 | ROLE_PROFILES R1 + compiled/HANDOFF_REPORTER |
| Handoff v2:Evidence Rules(§41–58)/ .ai Rule(§92–111) | AGENT_CORE §1–§2 / §4(0B) |
| Handoff v2:§14 copyable prompt 模板 | **廢止** → next-task intent(R1 output 7) |
| Handoff v2:§15 授權注意事項 | AGENT_CORE §7 |
| Handoff v2:§16 CTO 10 行摘要 / §17 Model-Routing 表 | 廢止 / ROUTING §1(Handoff 只供事實) |
| Handoff v2:§18 分類 | AGENT_CORE §3(`HANDOFF_<STATUS>`) |
| CEO v1:Project Config | manifest + `.ai/agent-profile.yaml` |
| CEO v1:Role Boundary / Core Tasks 1–5 / WAITING_* | ROLE_PROFILES R3(縮為四類輸出) |
| CEO v1:Phase 0 / Phase 0.5 / Evidence / Validation | AGENT_CORE §4 / §1–§2(0A/0B 命名) |
| CEO v1:STOP 條款(19 條) | AGENT_CORE §5(泛用)+ manifest(task 專屬);唯讀 dirty → WARN |
| CEO v1:Authorization Packaging + 高風險清單 | AGENT_CORE §7 + §6(唯一清單) |
| CEO v1:§6/§9 Worker prompt 產生 | **廢止**(Planner 唯一 compiler) |
| CEO v1:active_task.md 寫入權 | **廢止**(projection 專屬 Planner;TEMPLATE §P) |
| CTO v1:七面向檢查 / roadmap 標記 / P0–P3+ / 阻塞表 | ROLE_PROFILES R2(technical findings) |
| CTO v1:Phase 0 / 0.5 / STOP / Validation | AGENT_CORE §4 / §5 |
| CTO v1:§7/§9 Worker prompt 產生;active_task 寫入權 | **廢止** |
| CTO v1:§6 Bootstrap Maintenance + agent_bootstrap 三檔 | **廢止**(由本 control plane 取代;Phase 3 歸檔) |
| Planner v5.1:Core Goals / 驗收表 / 下一任務原則 / 模型建議 | ROLE_PROFILES R4 + compiled/PLANNER_COMPILER |
| Planner v5.1:Evidence Rules(20 條) | AGENT_CORE §1–§2、§8(8 條 lifecycle 宣稱 → §8 第 4 條一條化) |
| Planner v5.1:Authorization Packaging Policy | AGENT_CORE §7 |
| Planner v5.1:Worktree Modes A–D / 選擇順序 / PR-Open Gate / Post-Merge Gate / Owner Override | ROUTING §4–§9(Mode D → `reattach: true`) |
| Planner v5.1:13.1 / 13.2 / 13.3 模板 | WORKER_TASK_TEMPLATE §T(參數化合一,授權區塊三選一) |
| Planner v5.1:Input 欄位 / Git state 欄位 | TASK_MANIFEST schema |
| Planner v5.1:line 196 `maximum_top_level_project_folders: 2` | **刪除**(孤兒設定;Owner Additional Decision 10) |
| 四檔各自的 final classification 詞彙 | AGENT_CORE §3 統一代數 + 角色前綴 |

### 6.1 Four-role legacy major-section coverage

The inventory below treats each labelled legacy responsibility or output section as one major section. Every row has exactly one allowed classification; the destination names the surviving authority or explains why the legacy behavior is removed. Wrapper headings that contain no behavior are not separate sections.

<!-- BEGIN FOUR-ROLE LEGACY MAPPING -->
| ID | Legacy major section | Classification | VNext owner or disposition |
|---|---|---|---|
| H01 | Core Purpose | RETAINED_IN_ROLE | ROLE_PROFILES R1 purpose and required outputs |
| H02 | Critical Boundary | RETAINED_IN_ROLE | R1 report-is-not-audit and source qualification |
| H03 | Evidence Rules | MOVED_TO_SHARED_CORE | AGENT_CORE §1 §2 §8 |
| H04 | Project Filtering Rule | RETAINED_IN_ROLE | R1 project filtering and cross-project exclusion |
| H05 | Planner and Worker Traceability Rule | RETAINED_IN_ROLE | R1 traceability decisions and output matrix |
| H06 | AI Context and .ai Rule | MOVED_TO_SHARED_CORE | AGENT_CORE §4 §12 §14 bounded attachment load |
| H07 | 1. 本輪目標 | RETAINED_IN_ROLE | R1 goal and direction-change history |
| H08 | 2. 起承轉合分析 | RETAINED_IN_ROLE | R1 goal history plus event timeline without duplicate prose mandate |
| H09 | 3. 對話事件時間線 | RETAINED_IN_ROLE | R1 required event timeline |
| H10 | 4. Planner and Worker Traceability Matrix | RETAINED_IN_ROLE | R1 required traceability matrix |
| H11 | 5. 已完成事項 | RETAINED_IN_ROLE | R1 completed versus planned separation |
| H12 | 6. 修改或產出的檔案 | RETAINED_IN_ROLE | R1 actual-state snapshot when evidence exists |
| H13 | 7. 驗證結果和測試結果 | MOVED_TO_SHARED_CORE | AGENT_CORE §2 check states and head binding |
| H14 | 8. 實際狀態快照 | RETAINED_IN_ROLE | R1 required actual-state snapshot |
| H15 | 9. 目前結論 | REMOVED_AS_DUPLICATE | Folded into goals timeline completed and risks outputs |
| H16 | 10. 被修正或需要 CTO 重新檢查的假設 | RETAINED_IN_ROLE | R1 risks unknowns and escalation gaps |
| H17 | 11. 尚未完成事項 | RETAINED_IN_ROLE | R1 planned NOT_RUN blocked and excluded separation |
| H18 | 12. 風險與不確定點 | RETAINED_IN_ROLE | R1 risks and unknowns |
| H19 | 13. 建議今天優先處理的方向 | REMOVED_AS_ROLE_VIOLATION | Handoff supplies one next-task intent but does not prioritize execution |
| H20 | 14. 下一輪可直接執行的 task prompt | REMOVED_AS_ROLE_VIOLATION | Complete Worker prompt generation belongs only to Planner |
| H21 | 15. Owner Authorization Needed | MOVED_TO_SHARED_CORE | AGENT_CORE §6 §7 and Handoff cannot authorize |
| H22 | 16. CTO Agent 10 行內摘要 | REMOVED_AS_DUPLICATE | Handoff report and routing escalation carry the same facts |
| H23 | 17. Model and Routing Recommendation | MOVED_TO_ROUTING | ROUTING §1 §3 and Planner model recommendation |
| H24 | 18. Final Classification | MOVED_TO_SHARED_CORE | AGENT_CORE §3 role-prefixed final algebra |
| C01 | Project Config | MOVED_TO_MANIFEST | TASK_MANIFEST repo scope policies tests and worktree fields |
| C02 | CEO Role Boundary | RETAINED_IN_ROLE | ROLE_PROFILES R3 allowed outputs and forbidden actions |
| C03 | Phase 0 Mandatory Actual-State Verification | MOVED_TO_SHARED_CORE | AGENT_CORE §4 Phase 0A |
| C04 | Phase 0.5 AI Context Load | MOVED_TO_SHARED_CORE | AGENT_CORE §4 Phase 0B and §12 attachment |
| C05 | STOP Conditions | MOVED_TO_SHARED_CORE | AGENT_CORE §5 plus task-specific manifest gates |
| C06 | Evidence Rules | MOVED_TO_SHARED_CORE | AGENT_CORE §1 §2 §8 §13 |
| C07 | Allowed Read Sources | RETAINED_IN_ROLE | R3 required inputs constrained by attachment and manifest |
| C08 | Authorization Packaging Policy | MOVED_TO_SHARED_CORE | AGENT_CORE §6 §7 and Worker template authorization blocks |
| C09 | Core Task 1 Recent Work Value Review | RETAINED_IN_ROLE | R3 approved direction and value assessment |
| C10 | Core Task 2 CTO Judgment Review | RETAINED_IN_ROLE | R3 adopt partially adopt reject or unable-to-determine decision |
| C11 | Core Task 3 Roadmap and Progress Gap | RETAINED_IN_ROLE | R3 priority decision and direction review |
| C12 | Core Task 4 Today Focus Direction | RETAINED_IN_ROLE | R3 continue pause pivot and focused direction |
| C13 | Core Task 5 CEO Priority Decision | RETAINED_IN_ROLE | R3 P0 through P3+ decision and Owner requirements |
| C14 | Core Task 6 Executable Worker Task | REMOVED_AS_ROLE_VIOLATION | Planner alone creates manifests and complete Worker prompts |
| C15 | Optional Update A CEO-Decision.md | REMOVED_AS_ROLE_VIOLATION | CEO compiled role is decision output only and writes no files |
| C16 | Optional Update B active_task.md | REMOVED_AS_ROLE_VIOLATION | Planner projection is the sole active_task writer |
| C17 | Optional Update C roadmap.md and CTO-Analysis.md | REMOVED_AS_ROLE_VIOLATION | CEO does not mutate roadmap source or CTO analysis |
| C18 | Validation | MOVED_TO_SHARED_CORE | AGENT_CORE §2 evidence states and manifest-required checks |
| C19 | Final Response 1 Read and Reference Inventory | REMOVED_AS_DUPLICATE | Source qualification is part of every retained CEO finding |
| C20 | Final Response 2 Phase 0 State | MOVED_TO_SHARED_CORE | AGENT_CORE §4 actual-state evidence |
| C21 | Final Response 3 Recent Work Value Review | RETAINED_IN_ROLE | R3 recent-work value decision |
| C22 | Final Response 4 CTO Judgment Review | RETAINED_IN_ROLE | R3 CTO adoption decision |
| C23 | Final Response 5 Roadmap Gap Assessment | RETAINED_IN_ROLE | R3 priority and direction decision |
| C24 | Final Response 6 CEO Priority Decision | RETAINED_IN_ROLE | R3 P0 through P3+ ordering |
| C25 | Final Response 7 Today Focus Direction | RETAINED_IN_ROLE | R3 continue pause pivot output |
| C26 | Final Response 8 Executable Task Name | REMOVED_AS_ROLE_VIOLATION | Planner selects and manifests the task |
| C27 | Final Response 9 Copyable Worker Prompt | REMOVED_AS_ROLE_VIOLATION | Planner is the only Worker-prompt renderer |
| C28 | Final Response 10 File Updates | REMOVED_AS_ROLE_VIOLATION | CEO makes no routine file updates |
| C29 | Final Response 11 Validation | MOVED_TO_SHARED_CORE | Shared evidence state and source qualification |
| C30 | Final Response 12 Risks and Blind Spots | RETAINED_IN_ROLE | R3 risk constraints and unknowns |
| C31 | Final Response 13 Final CEO Decision | RETAINED_IN_ROLE | R3 direction and Owner-decision output |
| C32 | Final Response 14 CTO Briefing | RETAINED_IN_ROLE | R3 concise cross-role executive briefing |
| C33 | Final Response 15 CEO Briefing | RETAINED_IN_ROLE | R3 concise executive briefing |
| C34 | Final Response 16 Required Completion Check | REMOVED_AS_DUPLICATE | Evidence and final status already cover completion truth |
| C35 | Final Response 17 Final Classification | MOVED_TO_SHARED_CORE | AGENT_CORE §3 role-prefixed final algebra |
| T01 | Project Config | MOVED_TO_MANIFEST | TASK_MANIFEST repo scope policies tests and worktree fields |
| T02 | Phase 0 Mandatory Actual-State Verification | MOVED_TO_SHARED_CORE | AGENT_CORE §4 Phase 0A |
| T03 | Phase 0.5 AI Context Load | MOVED_TO_SHARED_CORE | AGENT_CORE §4 Phase 0B and §12 attachment |
| T04 | STOP Conditions | MOVED_TO_SHARED_CORE | AGENT_CORE §5 plus manifest gates |
| T05 | Allowed Read Sources | RETAINED_IN_ROLE | R2 required technical inputs constrained by attachment |
| T06 | Core Task 1 Roadmap Alignment | RETAINED_IN_ROLE | R2 roadmap alignment markers |
| T07 | Core Task 2 Code-Level CTO Review | RETAINED_IN_ROLE | R2 seven technical review dimensions |
| T08 | Core Task 3 P0 through P3+ Reordering | RETAINED_IN_ROLE | R2 technical priority recommendation |
| T09 | Core Task 4 Critical Blocker Analysis | RETAINED_IN_ROLE | R2 blockers acceptance constraints and risk |
| T10 | Core Task 5 Next-Stage Optimization Directions | RETAINED_IN_ROLE | R2 bounded technical findings and roadmap alignment |
| T11 | Core Task 6 Shared Agent Bootstrap Maintenance | REMOVED_AS_ROLE_VIOLATION | Control-plane sources replace bootstrap files and CTO does not implement |
| T12 | Core Task 7 Executable Worker Task | REMOVED_AS_ROLE_VIOLATION | Planner alone creates manifests and Worker prompts |
| T13 | Optional Update A roadmap.md | REMOVED_AS_ROLE_VIOLATION | CTO compiled role is read-only technical review |
| T14 | Optional Update B CTO-Analysis.md | REMOVED_AS_ROLE_VIOLATION | Findings are returned as role output and not written routinely |
| T15 | Optional Update C active_task.md | REMOVED_AS_ROLE_VIOLATION | Planner projection is the sole active_task writer |
| T16 | Validation | MOVED_TO_SHARED_CORE | AGENT_CORE §2 evidence and manifest-required checks |
| T17 | Final Response 1 Read and Reference Inventory | REMOVED_AS_DUPLICATE | Source qualification accompanies retained technical findings |
| T18 | Final Response 2 Phase 0 State | MOVED_TO_SHARED_CORE | AGENT_CORE §4 actual-state evidence |
| T19 | Final Response 3 Code-Level CTO Review | RETAINED_IN_ROLE | R2 technical findings |
| T20 | Final Response 4 Roadmap Alignment | RETAINED_IN_ROLE | R2 roadmap markers |
| T21 | Final Response 5 P0 through P3+ Ordering | RETAINED_IN_ROLE | R2 priority recommendation |
| T22 | Final Response 6 Critical Blockers | RETAINED_IN_ROLE | R2 blocker analysis |
| T23 | Final Response 7 Optimization Directions | RETAINED_IN_ROLE | R2 technical recommendation |
| T24 | Final Response 8 Executable Task Name | REMOVED_AS_ROLE_VIOLATION | Planner selects the manifested task |
| T25 | Final Response 9 Copyable Worker Prompt | REMOVED_AS_ROLE_VIOLATION | Planner is the only Worker-prompt renderer |
| T26 | Final Response 10 File Updates | REMOVED_AS_ROLE_VIOLATION | CTO does not perform routine writes |
| T27 | Final Response 11 Validation | MOVED_TO_SHARED_CORE | Shared evidence state and head binding |
| T28 | Final Response 12 Risks and Unknowns | RETAINED_IN_ROLE | R2 technical risk recommendation |
| T29 | Final Response 13 Final CTO Recommendation | RETAINED_IN_ROLE | R2 findings constraints tests and escalations |
| T30 | Final Response 14 Required Completion Check | REMOVED_AS_DUPLICATE | Evidence and final status already cover completion truth |
| T31 | Final Response 15 Final Classification | MOVED_TO_SHARED_CORE | AGENT_CORE §3 role-prefixed final algebra |
| P01 | Core Goals | RETAINED_IN_ROLE | ROLE_PROFILES R4 purpose and pipeline |
| P02 | Input | RETAINED_IN_ROLE | R4 consumes Handoff CTO CEO and Worker inputs |
| P03 | Evidence Rules | MOVED_TO_SHARED_CORE | AGENT_CORE §1 §2 §8 §13 |
| P04 | AI Context Loading Rule | MOVED_TO_SHARED_CORE | AGENT_CORE §4 §12 §14 bounded attachment load |
| P05 | Authorization Packaging Policy | MOVED_TO_SHARED_CORE | AGENT_CORE §6 §7 and WORKER_TASK_TEMPLATE authorization blocks |
| P06 | Worktree Lifecycle Policy | MOVED_TO_ROUTING | ROUTING §4 through §8 |
| P07 | Mode A NOT_APPLICABLE | MOVED_TO_ROUTING | ROUTING §4 NOT_APPLICABLE |
| P08 | Mode B REUSABLE_AGENT_WORKTREE | MOVED_TO_ROUTING | ROUTING §4 REUSABLE |
| P09 | Mode C EPHEMERAL_TASK_WORKTREE | MOVED_TO_ROUTING | ROUTING §4 EPHEMERAL |
| P10 | Mode D EXISTING_TASK_WORKTREE | MOVED_TO_ROUTING | ROUTING §4 reattach true |
| P11 | Worktree Selection Priority | MOVED_TO_ROUTING | ROUTING §4 selection order |
| P12 | PR Open Lifecycle Gate | MOVED_TO_ROUTING | ROUTING §8 |
| P13 | Post-Merge Branch Cleanup Gate | MOVED_TO_ROUTING | ROUTING §5 §7 and Worker template conditional gate |
| P14 | Owner Override | MOVED_TO_ROUTING | ROUTING §9 |
| P15 | Output 1 Current Goal | RETAINED_IN_ROLE | R4 Worker result acceptance |
| P16 | Output 2 Actual Completion | RETAINED_IN_ROLE | R4 acceptance report |
| P17 | Output 3 NOT_RUN STOP and Excluded | RETAINED_IN_ROLE | R4 acceptance and lifecycle state table |
| P18 | Output 4 Actual-State Snapshot | RETAINED_IN_ROLE | R4 acceptance evidence under source precedence |
| P19 | Output 5 Verification and Tests | MOVED_TO_MANIFEST | TASK_MANIFEST tests review and evidence fields |
| P20 | Output 6 Modified and Produced Inventory | RETAINED_IN_ROLE | R4 acceptance report and manifest traceability |
| P21 | Output 7 Engineering or Research Conclusions | RETAINED_IN_ROLE | R4 separates completed NOT_RUN blocked and risks |
| P22 | Output 8 Next 24H Task | RETAINED_IN_ROLE | R4 selects one bounded task |
| P23 | Output 9 Owner Authorization Needed | MOVED_TO_SHARED_CORE | AGENT_CORE §6 §7 and manifest authorization fields |
| P24 | Output 10 Final Classification | MOVED_TO_SHARED_CORE | AGENT_CORE §3 role-prefixed final algebra |
| P25 | Output 11 CTO Briefing Draft | REMOVED_AS_DUPLICATE | Planner consumes CTO constraints and does not impersonate CTO output |
| P26 | Output 12 CEO Briefing Draft | REMOVED_AS_DUPLICATE | Planner consumes CEO decisions and does not impersonate CEO output |
| P27 | Output 13 Copyable 24H Worker Prompt | RETAINED_IN_ROLE | R4 and WORKER_TASK_TEMPLATE complete rendering |
| P28 | Output 13.1 Single-Prompt Authorization Template | MOVED_TO_SHARED_CORE | AGENT_CORE §7 and template SINGLE_PROMPT block |
| P29 | Output 13.2 No-Authorization Template | MOVED_TO_SHARED_CORE | AGENT_CORE §7 and template NONE block |
| P30 | Output 13.3 High-Risk Standalone Authorization | MOVED_TO_SHARED_CORE | AGENT_CORE §6 §7 and template STANDALONE block |
| P31 | Output 14 Model and Reasoning Recommendation | RETAINED_IN_ROLE | R4 allowed model and reasoning recommendation |
| P32 | Output 15 Final Reminder | REMOVED_AS_DUPLICATE | R4 purpose forbidden list and pipeline already state the boundary |
<!-- END FOUR-ROLE LEGACY MAPPING -->

---

## 7. Deprecated Legacy Clauses(pilot 成功且 Owner 裁決後正式失效)

1. 四處 Worker-prompt 產生器 → 只留 Planner(Handoff §14、CEO §6/§9、CTO §7/§9 廢止)。
2. CEO / CTO 的 active_task.md 寫入權 → 廢止;view 由 Planner 投影。
3. 「Phase 0 / Phase 0.5」雙命名 → 廢止,改 0A / 0B。
4. CEO 版與 Planner 版兩份高風險清單 → 廢止,唯一清單 = AGENT_CORE §6(Owner Decision 2 三級制)。
5. Mode D EXISTING_TASK_WORKTREE → 廢止,改 `EPHEMERAL + reattach: true`。
6. 「staged files already exist → STOP」「dirty → STOP」對唯讀角色 → 改 WARN(AGENT_CORE §5)。
7. 一般任務要求「先單獨貼授權 / this spec is not authorization」→ 廢止(SINGLE_PROMPT 規則)。
8. normal lifecycle cleanup 另開 task → 廢止(ROUTING §7)。
9. Planner Evidence Rules 中 8 條 lifecycle 宣稱條款 → 併為 AGENT_CORE §8 第 4 條。
10. agent_bootstrap 三檔(SHARED_AGENT_BOOTSTRAP / TASK_TEMPLATES / CURRENT_STATE)→ 由 control plane 5 檔取代。
11. `maximum_top_level_project_folders: 2` 孤兒行 → 刪除。
12. 每檔自帶 Project Config 區塊 → manifest + agent-profile.yaml。
13. 四套互不相容的 final classification → 統一代數。
14. 未綁 head SHA 的歷史 PASS 可被引用 → 廢止(STALE 機制)。
15. 把專案 memory 集中到 Shared Control Plane → 廢止;memory 留在各 repo `.ai`。
16. memory / handoff 可充當 authorization 或 current-state proof → 廢止。
17. 整份 MEMORY_LOG 預載進 prompt → 廢止;改 bounded task-relevant retrieval。
18. 手工維護 `active_task.md` 或把它視為 source of truth → 廢止;manifest 勝出。
19. repo-local `.ai` 重定義 evidence / authorization / routing / lifecycle / final classifications → 無效並記 `[Risk]`。

---

## 8. Two-Pilot Migration Plan(尚未開始)

**Pilot 1 prerequisites(八項全滿足才可開始)**:

1. Shared Control Plane 已納入 version control。
2. Pilot repo 有四個 required `.ai` files:`PROJECT_PROFILE.md`、`PROJECT_CONTEXT.md`、`RUNBOOK.md`、`MEMORY_LOG.md`。
3. Pilot repo 有 `.ai/agent-profile.yaml`。
4. agent-profile 指定的 control-plane version 與 schema 可解析且相容。
5. LOW manifest 通過 L1–L22 lint。
6. manifest memory read/write policy 明確。
7. 四個 compiled role prompts 的 `compiled_from`、schema 與 durable-source fingerprint 與 current draft sources 一致。
8. active_task projection 的實際 manifest-SHA drift checking 可用(即使該 LOW manifest 設 `enabled=false`)。

上述條件在本文件整合任務中**未執行 pilot 驗證**;任一不成立即不啟動。

**Pilot 1 — LOW / read-only / NOT_APPLICABLE**

- Scope:一個 `examples/manifest-low.yaml` 型 fixed-head static review;全程 attachment → manifest → lint → concise render → Worker → Planner acceptance。
- Success:不建 worktree/branch、不產生 repo/DB/runtime/external/memory write;所有 evidence 綁 current head;authorization=`NONE`;projection disabled 時確認未寫 view。
- Fallback:停止 pilot,保留 R2.1 為 draft,該任務改走未修改的 legacy prompt;記錄卡點供修訂。

**Pilot 2 — MEDIUM / normal implementation / no DB**

- Entry:Pilot 1 成功且 Owner 明確允許 Pilot 2;沿用八項 prerequisite,改用 MEDIUM manifest 與 `SINGLE_PROMPT` Owner token。
- Scope:`examples/manifest-medium.yaml` 型 bounded source+test change;REUSABLE(或有明確 isolation 理由時 EPHEMERAL);draft PR、exact-head CI、fixed-head review、normal lifecycle cleanup;memory write 預設 forbidden。
- Success:S0→S11 有 command/ref evidence;CI green 後 worktree 正確回收;merge 後 branch cleanup 無獨立 task;review at exact head;DB/deployment/registry/external side effects均 NONE;enabled projection 的 manifest SHA drift-free。
- Fallback:同 Pilot 1;不得因此啟動任何 HIGH action。

兩個 pilot 均成功後仍須 Owner 裁決 legacy deprecation 與 active_task step-down。**在此之前 legacy 四檔不加 banner、不移動、不修改,control plane 不宣稱 active。**

---

## 9. Rollout / Rollback Plan

| Stage | 內容 | Rollback |
|---|---|---|
| 0 | Owner review 本 1.1.0-draft.1 文件集;不啟用 | 要求修訂;所有檔維持 draft |
| 1 | Owner 選定 version-control 位置並準備 Pilot 1 attachment prerequisites | 停止準備;不改 legacy |
| 2 | 離線 lint/render/projection-drift dry validation | 修 schema/template/compiled outputs後重驗 |
| 3 | Pilot 1(LOW) | 停止 pilot;任務改走 legacy;R2.1 保留 draft |
| 4 | Owner review Pilot 1 evidence後才可 Pilot 2(MEDIUM) | 不啟動 Pilot 2 |
| 5 | 兩 pilot 成功後由 Owner 裁決 legacy banner/archive、compiled 唯一入口、active_task step-down | compiled 停用;legacy 保持或恢復原入口 |
| 6 | Owner 設定觀察期後才可考慮去 draft | 版本維持/降回 draft,重開缺失修訂 |

---

## 10. Acceptance Tests(prompt 系統驗收)

| # | 測試 | 通過準則 |
|---|---|---|
| T1 | Manifest lint 套件 | L1–L22 各有 FAIL fixture 且被正確攔截;三個 examples 全 PASS |
| T2 | 渲染確定性 | 同一 manifest 渲染兩次逐字相同 |
| T3 | 未解析 slot | 缺欄位 manifest → 渲染 FAIL,不產出 prompt |
| T4 | Routing 確定性 | 三個 examples → 恰好 FAST/FAST/STRATEGIC;HIGH fixture 必落 STRATEGIC |
| T5 | 單一 source of truth | 高風險清單 / lifecycle 規則 / 授權格式在 5 個 durable files 中各僅定義一次(grep 檢核) |
| T6 | 長度 | MEDIUM 渲染 prompt ≤ legacy Planner 13.1 模板行數 50% |
| T7 | NOT_RUN 預設 | durable + compiled 檔案中不存在任何預填 PASS;status 欄預設 NOT_RUN |
| T8 | STALE 機制 | 綁舊 head 的 evidence fixture → review 表自動 STALE |
| T9 | Lifecycle 唯一性 | merge≠none 的 manifest → 渲染含 Post-Merge Gate 恰一次;無任何獨立 cleanup task 產生路徑 |
| T10 | 角色滲漏 | CEO / CTO profile 與其輸出格式中 grep 不到 Worker prompt 模板結構 |
| T11 | Projection | manifest → active_task view 確定性;手動竄改 view → DRIFT 被偵測且 manifest 勝出;view 欄位 ⊆ manifest |
| T12 | Compiled 一致性與長度 | compiled 四檔由五個 durable sources 機械生成、fresh bytes 相等、selected capsules 相同、`compiled_from` / schema / fingerprint 一致;每 role 與 aggregate hard gate 全 PASS,並回報 preferred target status |
| T13 | Profile 禁止鍵 | agent-profile fixture 含禁止鍵(如重定義 authorization)→ 被忽略 + `[Risk]` finding |
| T14 | 授權措辭 | SINGLE_PROMPT 渲染中不含「先單獨貼授權 / this spec is not authorization」;PENDING_OWNER_TOKEN fixture → Worker 語義為 WAITING_OWNER |
| T15 | Project attachment discovery | `.ai`/profile/四必要檔存在→續行;缺失→ENTRY_CHECK 或 manifest 指定 STOP;不得直接實作 |
| T16 | Fact / policy precedence | policy 與 fact 各只有一個明確順位;live evidence 可覆蓋 handoff/memory,反向不可 |
| T17 | Memory authorization | forbidden manifest 無 write instructions;allowed fixture 缺 path/purpose/core_v1/auth 任一即 lint FAIL |
| T18 | Bounded retrieval | relevant/bounded_recent 缺 selectors 或 `max_entries<1` 即 FAIL;render 不含整份 MEMORY_LOG |
| T19 | Cross-project isolation | profile/memory reference 指向另一 repo → `A_CROSS_PROJECT` + lint/route STOP |
| T20 | Control-plane version drift | profile/manifest/core version 或 schema 不相容 → `A_VERSION_MISMATCH` / `A_SCHEMA_MISMATCH` STOP |
| T21 | active_task manifest-SHA drift | enabled view 缺/錯 actual manifest SHA 或重投影 diff → DRIFT;manifest 勝出 |
| T22 | No Shared Core redefinition in repo `.ai` | profile 含 evidence/auth/routing/lifecycle/finals/memory-contract 定義 → key 無效 + `[Risk]`;L1 policy 不被覆蓋 |

---

## 11. Risks / Open Items

1. `[Risk]` 四份 compiled role prompts 仍是 inactive VNext candidates;ROLE_PROFILES 只澄清 CEO / CTO 可作 read-only web review,不構成 replacement、pilot 或 activation。
2. `[Unknown]` pilot repo 的四個 `.ai` files、agent-profile 與 version binding 未在本 compiler continuation 檢查;不得據此啟動 pilot。
3. `[Risk]` compiler 以 durable section identifiers 做 exact extraction;authoritative heading rename 會 fail closed,必須同步更新 recipe 並重跑 fresh-byte verification。
4. `[Risk]` web 端仍可能使用 legacy prompt;在兩 pilot + Owner 裁決前,這是刻意保留的 fallback,不是啟用缺陷。
