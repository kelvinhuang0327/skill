# R2.1 Implementation Spec — R2 Control Plane + Project Attachment Continuation

```
control_plane_version: 1.1.0-draft.1
date: 2026-07-17
status: DRAFT_FOR_OWNER_REVIEW — 本檔為 R2.1 交付說明文件,非 durable rule file。
boundary: 本輪未修改四份 legacy prompt、未做任何 git/repo 操作、未寫 memory、
          未宣稱新系統啟用。
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

**Build outputs(不計入 5 檔、不得手工維護)**:`compiled/HANDOFF_REPORTER.compiled.md`、`compiled/PLANNER_COMPILER.compiled.md`、各 repo 的 `active_task.md` view、每次渲染出的 Worker prompt。

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
7. 兩個 compiled prompts 的 `compiled_from` 與 current draft version 一致。
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
| T12 | Compiled 一致性 | compiled 兩檔內容與 durable files 規則逐條一致(diff 檢核清單)+ `compiled_from` 版本戳相符 |
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

1. `[Unknown]` Shared Control Plane 的 version-control 狀態未在本 continuation 執行 Git 驗證;Pilot 1 prerequisite 1 必須由後續授權工作確認。
2. `[Risk]` compiled prompts 為人工 documentary recompile;本輪只能做規則/版本 marker 一致性檢查,不能宣稱 mechanical generation 或 byte identity。
3. `[Unknown]` pilot repo 的四個 `.ai` files、agent-profile 與 version binding 未在本文件任務檢查;不得據此啟動 pilot。
4. `[Risk]` 尚無本 scope 內的 executable renderer/linter/projection generator;T2、fixture-based L1–L22、T21 tamper detection 的 mechanical execution 仍需後續獲授權實作。
5. `[Risk]` web 端仍可能使用 legacy prompt;在兩 pilot + Owner 裁決前,這是刻意保留的 fallback,不是啟用缺陷。
