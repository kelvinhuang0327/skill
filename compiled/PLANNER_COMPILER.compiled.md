<!-- GENERATED_BUILD_OUTPUT -->
<!-- DRAFT_FOR_OWNER_REVIEW -->
<!-- DO_NOT_EDIT: generated from the five durable control-plane sources -->
<!-- compiled_from: control_plane 1.1.0-draft.1 -->
<!-- control_plane_version: 1.1.0-draft.1 -->
<!-- schema_version: 1 -->
<!-- durable_source_fingerprint: 1c32676bedef32b88fd7f57289e382174f8281a8bae214334e75964f08865bbe -->
<!-- generated_by: prototype/control_plane.rb compile-role planner -->
<!-- source_file_identity: AGENT_CORE.md bytes=16087 sha256=9741b01e60cddc7e1a26f1221e0ddb72de5085d5d46f950577144fa6ec1e9229 -->
<!-- source_file_identity: ROLE_PROFILES.md bytes=10390 sha256=e89902706fd23cfce888c6973cadf2447e896725c080aaecb805e1a8275db0e6 -->
<!-- source_file_identity: ROUTING_AND_LIFECYCLE.md bytes=10490 sha256=2bb094b0533e6bc3a7b29b4915a47e22c1df638fd242dd427d6da622afc9943b -->
<!-- source_file_identity: TASK_MANIFEST.schema.yaml bytes=10747 sha256=5b2d87cad593fd68829109a119c48fe0b95dd4ed031db6b912bf95a3c1e4334a -->
<!-- source_file_identity: WORKER_TASK_TEMPLATE.md bytes=12236 sha256=a256d34c9c58f8eee2d05f40bf9cda8f9f13c337651b65d32737ec420a71d4bb -->

# Planner / Task Compiler — VNext Candidate

This is an inactive VNext candidate. Every rule block below is copied from an identified durable source; this artifact contains no project memory and grants no authorization.
<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §1 -->
## §1 Evidence 詞彙

| 標籤 | 意義 | 使用規則 |
|---|---|---|
| `[Confirmed]` | 本輪有直接證據:command output、檔案內容、對話原文 | 轉述型事實必須註明 source(例:`source = worker report; not independently audited`) |
| `[Inferred]` | 合理推論 | 不得升級為 `[Confirmed]` |
| `[Unknown]` | 資訊不足 | 不得補完、不得推論成事實 |
| `[Risk]` | 需注意的風險 | 必附影響範圍 |
<!-- END SOURCE: AGENT_CORE.md SECTION: §1 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §2 -->
## §2 Evidence state(檢查、測試、驗證與歷史證據的狀態值)

`PASS / FAIL / NOT_RUN / UNKNOWN / STALE / OUTDATED`

1. 預設值是 `NOT_RUN`。未實際執行的測試、命令、驗證一律 `NOT_RUN`,不得預填或改寫為 PASS。
2. **head-SHA binding**:每筆 repository / test / CI evidence 必須附產生當下的 HEAD SHA。
3. `STALE`:evidence 未綁定當前 head SHA、或已超出本任務 scope → 自動 `STALE`,不得當作本輪 PASS。歷史測試結果只能作為背景,不能作為當輪驗證。
4. `NOT_RUN` ≠ `FAIL`。只有 `FAIL` 觸發 required-check 失敗處理;`NOT_RUN` 必附原因。
5. `OUTDATED`:memory / handoff 中**已被 live state 反證**的歷史 evidence。它不是本輪可執行 check 的成功/失敗結果,不得當作 PASS。
6. `STALE` 表示未綁 current head、真偽未定;`OUTDATED` 表示已被較高順位 live evidence 反證。轉換與保留規則見 §13 / §14。
<!-- END SOURCE: AGENT_CORE.md SECTION: §2 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §3 -->
## §3 Task-status(任務終態代數)

`COMPLETE / COMPLETE_WITH_RISKS / PARTIAL / BLOCKED / WAITING_OWNER`

- 角色終態一律為 `<ROLE>_<STATUS>`(例 `WORKER_COMPLETE`、`PLANNER_BLOCKED`)。
- Independent Reviewer 另有對「被審變更」的 verdict:`PASS / PASS_WITH_RISKS / FAIL / BLOCKED`(見 ROLE_PROFILES R6)。
- 禁止各角色自創新終態。
<!-- END SOURCE: AGENT_CORE.md SECTION: §3 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §4 -->
## §4 Phase 0 唯一定義

**Phase 0A — Actual State Verification**(有本機執行能力的角色適用):

1. `pwd`
2. `git rev-parse --show-toplevel`
3. `git branch --show-current`
4. `git rev-parse --git-dir`
5. `git status --short`
6. `git rev-parse HEAD`
7. upstream 與 ahead/behind(如存在)
8. 有 DB → 僅 read-only 驗證(row count / schema / integrity);無 DB → artifact baseline / source tree health
9. 凍結 canonical dirty inventory(before-state)

**Phase 0B — Repo-local Context Load**:

- `.ai/ai-context/PROJECT_PROFILE.md`
- `.ai/ai-context/PROJECT_CONTEXT.md`
- `.ai/ai-context/RUNBOOK.md`
- `.ai/ai-memory/MEMORY_LOG.md`(依 §14 bounded selection,只讀 task-relevant 條目,不得整份載入)
- `.ai/agent-profile.yaml`(attached project 必須存在;版本在 0A 前依 §12 解析,0B 載入其 project restrictions)

規則:

- 順序:**0A → 0B**。舊文件中「Phase 0 / Phase 0.5」兩種命名一律廢止,改用 0A / 0B。
- web-side 角色(無工具):0A 標 `NOT_RUN`,以對話中回報的狀態為替代輸入,必標 `source = user/worker report`。
- `.ai`、agent-profile 或 required context 缺失 → `[Unknown]`;不得假設 personal-ai-flow 已導入;任務依 §12 / ROUTING §0 降級為 Entry Check / Bootstrap Readiness / Repo State Decision 或 STOP;不得自行補齊 `.ai`。
- 0B 是上下文載入,不是治理任務;不得因此擴大 scope。
<!-- END SOURCE: AGENT_CORE.md SECTION: §4 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §5 -->
## §5 STOP / WARN 語義

- **STOP**:立即停止、不修改任何檔案,輸出 STOP report(1 預期狀態 2 實際觀察 3 差異原因 4 風險 5 建議修正 scope)。
- **WARN**:記錄 evidence 後繼續。
- 唯讀角色或唯讀 mode 遇 canonical dirty / pre-existing staged / unrelated changes → **WARN + evidence,不 STOP**。
- 只有兩種情況 dirty 才 STOP:(a) 本任務需要寫入該 workspace;(b) dirty 使證據無法判定。
- STOP 條款只約束**角色自身行為**,不自動約束其審查或規劃的任務內容(該任務的邊界由其 manifest 決定)。
- 任何角色不得以 force 突破 STOP。
<!-- END SOURCE: AGENT_CORE.md SECTION: §5 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §6 -->
## §6 風險分級(唯一清單 — Owner Decision 2)

`risk_class` = 任務內所有動作的**最高**等級。Owner 可上調,任何角色不得下調。

**LOW**(無 repo mutation、無 DB、無外部副作用):

- read-only analysis
- conversation handoff
- GitHub metadata query
- static source review
- 不需要 checkout 的 fixed-head inspection
- 不產生任何外部副作用的文件回覆

**MEDIUM**(一般工程動作):

- 一般 source 與 test 修改
- commit、normal push、draft PR
- 經 CI 與 review gate 後的 normal merge
- clean worktree 的標準 lifecycle 回收
- merged task branch 的安全 `git branch -d`
- no-force remote task branch deletion
- metadata-only lifecycle / catalog 變更
- OBSERVATION、REJECTED、RETIRED 等 non-executable metadata publication
- 不涉及 DB、production activation 或 external publication 的 registry / catalog 維護

**HIGH**(不可逆、或改變正式執行資格):

- canonical DB write、migration、backfill 或 generated rows
- production deploy 或 release
- production configuration activation
- executable generation registry activation 或 ONLINE promotion
- 會改變正式執行資格的 registry publication
- credentials、secrets、payments
- external message、notification 或 data publication
- 真實金流、實單交易或真實下注
- force delete、force remove
- dirty worktree deletion
- unmerged branch force deletion
- broad workspace cleanup
- 沒有 quarantine / manifest / SHA-256 保障的不可逆刪除
- 其他不可逆外部行為

**邊界判例**(Owner Decision 2):

- metadata-only OBSERVATION catalog 變更 = **MEDIUM**,不是 HIGH。
- 只有加入 executable generation registry、ONLINE promotion 或 production activation,才屬 HIGH registry mutation。
<!-- END SOURCE: AGENT_CORE.md SECTION: §6 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §7 -->
## §7 Authorization Matrix(唯一定義 — Owner Decision 3)

| risk_class | authorization.class | 形式 |
|---|---|---|
| LOW | `NONE` | 無 token |
| MEDIUM | `SINGLE_PROMPT` | token 與 task spec 同一則訊息 |
| HIGH | `STANDALONE` | Owner 獨立授權訊息 + 分開的 spec,必述高風險原因 |

**SINGLE_PROMPT 規則**:

1. Owner authorization token 與 task spec 可在同一則訊息。
2. 第一個非空白行包含正確 token 即有效。
3. Worker 不得再要求額外 standalone 確認(不得要求「先單獨貼授權」「下一則才是 spec」「this spec is not authorization」)。
4. 同一 token 只適用一個 `task_id`。
5. 任務終止、完成或 scope 變更後 token 失效。
6. 不得繼承前一輪 token。

**Token ownership**:

- 真實 token 只能由 **Owner** 填入。
- Planner / Compiler 只能輸出佔位符 `PENDING_OWNER_TOKEN`。
- 佔位符未被 Owner 置換前,任何角色不得宣稱任務已授權;Worker 收到 `PENDING_OWNER_TOKEN` 視同未授權 → `WAITING_OWNER`。
- `STANDALONE` 的 token **永不嵌入 spec 訊息**,只能由 Owner 於獨立訊息提供;manifest 中該欄固定為 `SEPARATE_MESSAGE_REQUIRED`。
- Handoff Reporter / CTO / CEO / Reviewer 不得簽發、填入或代轉 token。

**LOW read-only 白名單**僅限:無 repo mutation、無 DB、無外部副作用之任務。目前不建立更廣泛的自動授權白名單(Owner Decision 3)。
<!-- END SOURCE: AGENT_CORE.md SECTION: §7 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §8 -->
## §8 No-fabrication 規則

1. 不把計畫寫成已完成;不把 STOP / BLOCKED 寫成完成。
2. 不把歷史結果寫成未來能力;不把研究結果寫成可投注、可上線、可產品化。
3. 不把建議寫成已授權;前一輪的授權、DB、artifact、commit、push、worktree、cleanup 授權一律不繼承。
4. **Lifecycle 宣稱規則**(合併 legacy 各條):任何 worktree 移除、reusable 恢復、branch 刪除、merge、deployment、registry、publication 的狀態宣稱,必附實際 command output 或 ref evidence;否則只能寫 `NOT_RUN` / `[Unknown]`。
5. 不誇大成果;不把推論寫成事實;production-ready 宣稱需明確證據與 Owner 授權。
<!-- END SOURCE: AGENT_CORE.md SECTION: §8 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §9 -->
## §9 Side-effect 邊界詞彙(manifest 對應欄位)

- `db_policy`: `none | read_only | write_authorized`(write_authorized 必為 HIGH)
- `runtime_policy`: `none | tmp_only | logs_allowed`
- `external_side_effects`: `none | listed`(listed 時逐項列出,逐項套 §6 分級)
- 測試副作用:每條 required test 必有 `side_effects_allowed ∈ {none, tmp_only, logs, db_sandbox}`;副作用超出 allowance 或未確認 → 該測試 `NOT_RUN` + 原因。required 與 forbidden 的一致性由 manifest lint 保證(schema L2/L3)。
<!-- END SOURCE: AGENT_CORE.md SECTION: §9 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §10 -->
## §10 架構分層(四層模型 — R2.1)

| 層 | 內容 | 定位 |
|---|---|---|
| **L1 Shared Control Plane** | 本目錄 5 個 durable files:evidence 詞彙、authorization matrix、routing、worktree lifecycle、manifest schema、prompt rendering | 跨專案共用政策;唯一、一份、必須受版本控制(未版本控制 → pilot BLOCKED);各 repo 不得複製全文 |
| **L2 Repo-local Project Attachment** | `<repo>/.ai/`:`ai-context/PROJECT_PROFILE.md`、`ai-context/PROJECT_CONTEXT.md`、`ai-context/RUNBOOK.md`、`ai-memory/MEMORY_LOG.md`、`agent-profile.yaml` | 專案身分、限制與**專案記憶**。每個專案的 memory 必須留在自己的 repo-local `.ai`,**不得集中到 Shared Control Plane repository** |
| **L3 Current Task Manifest** | 一個 task 恰一份 | 本輪任務唯一 source of truth:scope、tests、authorization、branch、worktree、cleanup、memory policy |
| **L4 Live State** | Git / GitHub PR、CI / DB、data、runtime 當下觀測 | **當下實際狀態的最終事實來源**(§13 fact precedence 第 1 位) |

**Repo-local profile(L2)**:`<repo>/.ai/agent-profile.yaml`(或功能等價的單一 repo-local 檔)。

允許鍵(僅此清單):

- project identity
- canonical repo / branch
- **shared control plane binding**(path、control_plane_version、schema_version)
- risk-domain overrides(只可上調)
- protected paths
- DB / data / runtime restrictions
- repo-specific test aliases
- repo-specific worktree path
- repo-specific publication restrictions
- repo-specific memory log path(預設 `.ai/ai-memory/MEMORY_LOG.md`)

禁止鍵(不得重新定義,出現即無效):

- Evidence vocabulary
- Authorization matrix
- Worktree lifecycle
- Routing rules
- Common final classifications
- Memory Contract 規則(§14)

合併規則:允許鍵 → repo 值覆寫 workspace 預設(依 §13 policy precedence,只可縮小 scope 或增加限制);禁止鍵 → 忽略該鍵、記 `[Risk]`、review 時列為 finding。
<!-- END SOURCE: AGENT_CORE.md SECTION: §10 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §12 -->
## §12 Project Attachment Contract(接手契約)

任何 agent 接手任一專案時,必須依序執行固定流程(狀態處置見 ROUTING §0):

```
ATTACHMENT_DISCOVERY → CONTROL_PLANE_VERSION_RESOLUTION → PHASE_0A_LIVE_STATE
→ PHASE_0B_PROJECT_CONTEXT_LOAD → MEMORY_RELEVANCE_SELECTION
→ ACTIVE_MANIFEST_RESOLUTION → ROUTING → EXECUTION_OR_STOP
```

1. **ATTACHMENT_DISCOVERY**:找到 canonical repo root(`git rev-parse --show-toplevel`,或 Owner / profile 指定);檢查 `.ai/`、`.ai/agent-profile.yaml` 與四個必要檔案:`PROJECT_PROFILE.md`、`PROJECT_CONTEXT.md`、`RUNBOOK.md`、`MEMORY_LOG.md`。
2. **CONTROL_PLANE_VERSION_RESOLUTION**:讀 `.ai/agent-profile.yaml`,解析 shared control plane path、control_plane_version、schema_version、canonical repo 與 repo-specific restrictions;驗證 Shared Core version/schema 存在且相容,且 profile/context/memory path 全屬本 repo。version 不相容 → `A_VERSION_MISMATCH`;schema 不相容 → `A_SCHEMA_MISMATCH`;跨 repo → `A_CROSS_PROJECT`;三者均 **STOP**,不得以其他版本或專案 attachment 充當。
3. **PHASE_0A_LIVE_STATE**:§4 之 0A。live state 是 L4 最終事實來源。
4. **PHASE_0B_PROJECT_CONTEXT_LOAD**:§4 之 0B。
5. **MEMORY_RELEVANCE_SELECTION**:依 §14 規則 11,只選取 task-relevant memory(task_id / branch / PR / risk domain / 最近相關),不得無限制載入整份 MEMORY_LOG。
6. **ACTIVE_MANIFEST_RESOLUTION**:讀 current task manifest(L3)。`active_task.md` 只是投影;與 manifest 不一致以 manifest 為準。無 manifest → 本輪只能是 Planner 編譯輪或 read-only 分析。
7. **DRIFT CHECK**(貫穿 3–6):比較 memory / handoff 與 live state;drift 時 **live state 優先**,memory 條目標 `STALE`(未綁 current head、無法證實)或 `OUTDATED`(已被 live state 反證);見 §13。
8. **ROUTING → EXECUTION_OR_STOP**:ROUTING §0 / §1。

**降級規則**:`.ai` 或 agent-profile 缺失 → `ENTRY_CHECK` / `BOOTSTRAP_READINESS`;其他必要 context 不全 → 依 manifest `missing_context_policy` entry check 或 STOP。降級任務固定 LOW、Worktree NOT_APPLICABLE、read-only,**不得直接進 routine implementation**;且不得自行補齊 `.ai`(§4)。
<!-- END SOURCE: AGENT_CORE.md SECTION: §12 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §13 -->
## §13 Source Precedence(唯一定義)

**Policy precedence**(規則衝突時,由高至低):

1. Non-overridable Shared Core safety invariants(§5–§9、ROUTING §6 不變式)
2. Current explicit Owner authorization
3. Current task manifest
4. Repo-local `.ai` restrictions
5. Shared Core defaults

- Repo-local profile 與 task manifest 只可**縮小** scope 或**增加**限制。
- 沒有對應 Owner authorization 時,不得放寬 Shared Core 高風險邊界(§6 / §7)。

**Fact precedence**(事實衝突時,由高至低):

1. Live repo / GitHub / DB / runtime 觀測(L4)
2. 綁定 current head SHA 的 evidence
3. Current-task handoff
4. MEMORY_LOG historical entries

- Memory、handoff 或歷史 report **不得覆蓋 live evidence**。
- 低位來源與高位來源衝突 → 低位標 `OUTDATED`;低位無法被 current head 證實 → `STALE`。
- `STALE` / `OUTDATED` 條目仍保留為歷史記錄(append-only),但不得作為本輪事實或 PASS。
<!-- END SOURCE: AGENT_CORE.md SECTION: §13 -->

<!-- BEGIN SOURCE: AGENT_CORE.md SECTION: §14 -->
## §14 Memory Contract(唯一定義;不另立 memory policy 文件)

**定位**:

1. MEMORY_LOG 是歷史上下文,**不是 authorization source**。
2. MEMORY_LOG 不能證明 current branch、PR、CI、DB 或 runtime 狀態(§13)。
3. 未綁 current head 的 historical PASS 一律 `STALE`。

**寫入**:

4. Worker 預設**不得**寫 MEMORY_LOG。
5. 只有 task manifest 明確設定 `memory.write.mode: allowed` 才可寫。
6. Memory write 必須有 exact `allowed_path`、`purpose` 與 entry format(`entry_schema: core_v1`)。
7. Handoff Reporter 只能提出 candidate memory entry(標 `CANDIDATE — NOT WRITTEN`),不得直接寫入。
8. Planner 決定 memory 是否需要列入 manifest,但**不能自行授權寫入**——`memory.write: allowed` 的生效仍依 §7 task authorization(lint L18:class ≠ NONE)。

**Entry 格式(core_v1;append-only)**:

9. 每條至少包含:`timestamp`、`task_id`、`source`、`repo/head/PR binding`(如適用)、`classification`(`[Confirmed]` / `[Inferred]` / `[Risk]`)、`confirmed_facts`、`unresolved_risks`、`supersedes` / `superseded_by`。
10. 新 memory 不得修改舊事實;修正一律用 append-only superseding entry(新條目 `supersedes: <舊 id>`;舊條目視為 `superseded_by` 標記)。

**讀取(retrieval budget)**:

11. Memory retrieval 必須有 budget:依 task_id、branch、PR、risk domain 或最近相關記錄選取(manifest `memory.read.selectors` + `max_entries`);**不得預設把整份 MEMORY_LOG 嵌入 Worker prompt**。跨專案隔離:A 專案的 memory 不得出現在 B 專案的任何 prompt。
<!-- END SOURCE: AGENT_CORE.md SECTION: §14 -->

<!-- BEGIN SOURCE: ROLE_PROFILES.md SECTION: R4 -->
## R4 PLANNER_COMPILER(web-side;Owner Decision 1)

**Purpose**:驗收 Worker 結果;**唯一** manifest 作者;**唯一** Worker prompt 渲染者;`active_task.md` view 的唯一產生者。

**Pipeline(必須依序)**:

1. 收斂輸入(handoff / CTO constraints / CEO decision / Worker 回報;含 Handoff Reporter 的 candidate memory entries)
2. **Attachment resolution**(CORE §12):確認 repo `.ai` 四必要檔齊全、`agent-profile.yaml` 可解析、Shared Core 版本相容;缺失 → 下一任務只能編譯為 ENTRY_CHECK / BOOTSTRAP_READINESS(ROUTING §0)
3. 套 ROUTING §0 / §1 決策表 → routing_path
4. 依 CORE §6 定 risk_class → CORE §7 定 authorization.class
5. 填 TASK_MANIFEST(一個 task 恰一份;含 memory.read selectors / budget 與 memory.write 政策 — Planner 可提案 `write: allowed`,但生效依 task authorization,不得自行授權;CORE §14 規則 8)
6. 跑 manifest lint(schema 附錄 L1–L22);任一 FAIL → 不得渲染
7. 用 WORKER_TASK_TEMPLATE 渲染 Worker prompt;產 `active_task.md` projection(TEMPLATE §P)

**Allowed**:

- routing / risk / worktree mode / authorization class 判定
- 下一任務選擇:單一任務、24H 可完成、優先實作 / 修 bug / 驗證 / 合併;有成熟開源方案時優先要求評估
- 模型與 reasoning 建議

**Forbidden**:

- 填入真實 token(只能 `PENDING_OWNER_TOKEN` / `SEPARATE_MESSAGE_REQUIRED`)
- 自行實作或執行任何 lifecycle 動作
- 發明 CTO / CEO / CORE 未給的 constraints;把建議寫成已授權
- 為 normal lifecycle cleanup 另開 task(ROUTING §7)
- 產生大型治理任務
- 把整份 MEMORY_LOG 嵌入渲染 prompt(必須 bounded selection;CORE §14 規則 11)
- 跨專案混用 memory(A 專案 memory 不得進 B 專案 prompt)

**Required outputs**:驗收報告(完成 / 未完成 / NOT_RUN / BLOCKED + lifecycle 狀態表)、manifest、rendered Worker prompt、active_task view、授權需求標記、模型建議。

**Final**:`PLANNER_{COMPLETE | COMPLETE_WITH_RISKS | PARTIAL | BLOCKED | WAITING_OWNER}`

---
<!-- END SOURCE: ROLE_PROFILES.md SECTION: R4 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §0 -->
## §0 Project Attachment States(前置於一切 routing)

執行 CORE §12 八階段(名稱不得縮寫或重排):

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

狀態與處置:

| 狀態 | 條件 | 處置 |
|---|---|---|
| `A_OK` | `.ai` 四必要檔齊全、agent-profile 可解析且屬於當前 repo、Shared Core version/schema 相容、manifest 可解析 | 進 §1 routing |
| `A_NO_ATTACHMENT` | `.ai` 或 `.ai/agent-profile.yaml` 缺失 | route 到 **ENTRY_CHECK / BOOTSTRAP_READINESS**(type=entry_check、LOW、Worktree NOT_APPLICABLE、read-only);不得進 routine implementation;不得自行補齊 `.ai` |
| `A_NO_CONTEXT` | `.ai` 存在,但 manifest 所列必要 context 不全 | `missing_context_policy=entry_check` → ENTRY_CHECK / BOOTSTRAP_READINESS;`stop` → **STOP**;兩者均不得逕行實作 |
| `A_VERSION_MISMATCH` | agent-profile 指定的 Shared Core 版本不存在或不相容 | **STOP**(不得以其他版本充當;回報所需版本與現有版本) |
| `A_SCHEMA_MISMATCH` | manifest schema version、control-plane schema version、agent-profile schema binding 任一不相容,或 manifest 無法依指定 schema 解析 | **STOP**(回報 expected / actual schema;不得猜測轉換或以舊 schema 續行) |
| `A_CROSS_PROJECT` | profile canonical repo、required context、memory selector/path 或 manifest project path 指向另一專案 | **STOP**;拒絕 attachment,不得載入或複製另一專案的 `.ai` / memory |
| `A_NO_MANIFEST` | `.ai` 齊全但無 current task manifest | 本輪只能是 Planner 編譯輪(產 manifest)或 read-only 分析 |
| `A_DRIFT` | memory / handoff 與 live state 衝突 | **WARN**:live 優先,衝突條目標 `STALE` / `OUTDATED`(CORE §13),續行;若 drift 使任務前提失效(如 manifest base_commit 不在 origin/main、指定 branch / worktree 已消失)→ **STOP** 回報 |

ENTRY_CHECK / BOOTSTRAP_READINESS 任務的產出:`.ai` 現況、缺失清單、建立建議 — 由 Owner 決定是否授權 bootstrap。

**Evidence state transition**:

- historical evidence 未綁 current head 或尚未重驗 → `STALE`;
- live state 明確反證該 historical evidence → `STALE` 或未分類歷史條目轉為 `OUTDATED`;
- 重跑並綁 current head 才能產生新的 `PASS` / `FAIL`;不得把舊條目原地改回 PASS;
- `OUTDATED` 保留為 append-only 歷史;若需修正,新增 superseding entry(CORE §14)。

**STOP / WARN 邊界**:version/schema/cross-project 錯誤、`missing_context_policy=stop`、或 drift 使 manifest 前提失效 → STOP;僅 historical drift、STALE/OUTDATED、或不影響判定的唯讀 dirty observation → WARN 並續行。任何跨專案 attachment 都不得降級為 WARN。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §0 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §6 -->
## §6 不變式(取代 legacy 長禁令清單)

- **I1** 無 STANDALONE 授權,永不 force:`branch -D`、`--force`、`rm -rf`、`reset --hard`、`git clean`、任意 `worktree prune`、dirty worktree 刪除、unmerged branch 刪除。
- **I2** 不可作為 cleanup 對象:protected paths、committed durable artifacts / reports / evidence、`.ai`、DB、data、runtime、logs、dependencies。
- **I3** 只碰 manifest 點名的 path 與 branch;unrelated branch / worktree 不可觸。
- **I4** canonical repo 對 Worker 唯讀(除非 manifest 明確把該路徑列入 allowed_files)。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §6 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §1 -->
## §1 Routing 決策表

前置:§0 狀態必須為 `A_OK`(或依 §0 表處置)。
輸入:next-task intent(或 Owner 指示)+ CORE §6 risk_class。
判定順序:先檢 STRATEGIC 觸發 → 再檢 TECHNICAL 觸發 → 否則 FAST。**取最高升級**。

| 觸發群 | 條件(任一成立) | 路徑 |
|---|---|---|
| T-STRAT | 動作含 CORE §6 HIGH 任一項;roadmap 優先級衝突;scope 超出當前 phase;預算 / 額度裁決;kill / pivot;**修改 control plane 本身** | STRATEGIC |
| T-TECH | 新依賴;新模組或跨模組介面變更;schema / 資料模型變更;效能關鍵路徑;安全敏感面;同一任務連續 ≥2 輪 FAIL / BLOCKED;測試架構變更;懷疑 roadmap drift;CTO 被 Owner 點名 | TECHNICAL |
| — | 上述皆否(LOW / MEDIUM 的例行 feature / bugfix / test / docs / metadata / merge / pr_fix / analysis) | FAST |

路徑序列(`[HANDOFF]` 僅在有 web 對話輪時;`MERGE*` 依 manifest.merge_policy;`REVIEW*` 見 §2):

```
FAST:      [HANDOFF] → PLANNER → WORKER → REVIEW* → MERGE*
TECHNICAL: [HANDOFF] → CTO → PLANNER → WORKER → REVIEW → MERGE*
STRATEGIC: [HANDOFF] → CTO → CEO → OWNER_DECISION → PLANNER → WORKER → REVIEW
```

機械對映:`risk_class=HIGH ⟹ routing_path=STRATEGIC`(lint L14)。MEDIUM 不自動升級,僅由 T-TECH 條件升級。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §1 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §2 -->
## §2 Reviewer 需求規則

Independent Reviewer **required** 當任一成立:

- merge 進 canonical branch
- 觸及 evidence 敏感路徑(hash inventory / generation registry / catalog / protected paths)
- risk_class ≥ MEDIUM 且含 push / PR
- 作者與審查者必須分離的任務(reviewer ≠ author)

純 read-only 分析(無 PR、無 merge)可免 review。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §2 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §3 -->
## §3 CEO / CTO Cadence

- CTO:僅 TECHNICAL / STRATEGIC 觸發時進場;**不做每輪全 repo review**。
- CEO:僅 STRATEGIC;另可由 Owner 設定週期性檢視(頻率由 Owner 決定,預設無)。
- routine FAST 任務不經 CEO、不需 CTO 重審。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §3 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §4 -->
## §4 Worktree Modes(3 modes + reattach — Owner Additional Decision 6)

選擇順序:`NOT_APPLICABLE → REUSABLE → EPHEMERAL`。不得預設為每個 task 建 isolated worktree。

**Mode NOT_APPLICABLE**
適用:純交接、文件回覆、GitHub metadata 查詢、不需 checkout 的 read-only audit / fixed-head inspection。
規則:不建 worktree、不建 task branch(除非任務明確需要)、不建任何新資料匣。

**Mode REUSABLE**(sequential implementation 預設)

- 固定路徑 `<PROJECT_PARENT>/<PROJECT_NAME>-agent`(repo-local profile 可覆寫),不含 task ID。
- 使用前:存在、`git status --short` 為空、無 staged、fetch origin、自 origin/main 建立或切換 manifest 指定 task branch;dirty / branch 狀態不明 → STOP。
- 完成後(push + PR + exact-head CI green):確認 clean → fetch origin/main → `git switch --detach origin/main` → 再驗 clean → 回報已恢復 baseline。
- 禁止:為該 task 另建 sibling worktree;dirty 時自行 stash / reset / clean;混入其他 active task;force;刪除 reusable worktree;當 durable artifact storage。

**Mode EPHEMERAL**(parallel / isolation / audit reproduction 才用)

- 集中路徑 `<PROJECT_PARENT>/.worktrees/<PROJECT_NAME>/<TASK_ID>-<SHORT_NAME>`。
- 只能建 manifest 指定的 exact path;禁止 fallback / backup / scratch / copy / alternative path;禁止 canonical repo 內 nested worktree。
- 已存在:clean 且 branch 正確才可重用;否則 STOP(不得自行刪除、清理、覆蓋、重建)。
- 完成後(push + PR + exact-head CI green):驗 clean → `git worktree remove <EXACT_PATH>` → 驗 path 消失 → 驗 `git worktree list` 無此項。
- CI RED / pending:保留並回報 `retained for CI fix`(path / branch / HEAD / dirty / blocker)。

**reattach: true**(原 Mode D 併入 — 既有 PR fix / audit continuation)

- 適用:修正尚未 merge 的既有 PR、audit 後需要修改。
- 若原 worktree 已回收:自 remote PR branch 重建**同一** exact ephemeral path(manifest 必附 `pr_ref`)。
- 修正 push 且 exact-head CI green 後:再次套用 EPHEMERAL 回收規則。
- 不得因 PR OPEN 而無限期保留資料匣。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §4 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §5 -->
## §5 任務生命週期狀態機

```
S0 ROUTED → S1 WT_READY → S2 BRANCHED → S3 COMMITTED → S4 PUSHED → S5 PR_OPEN
S5 → S6 CI{GREEN|RED}
S6 GREEN → S7 WT_RECLAIMED → S8 REVIEWED{PASS|FAIL} → S9 MERGED → S10 BRANCHES_DELETED → S11 CLOSED
S6 RED   → 停留 S5(worktree 保留;下一任務 = 同 PR fix,reattach)
任一狀態 + Owner Override → FROZEN(記 reason + retention / 重審條件;解除後回原狀態)
```

轉移前置條件:

| 轉移 | 前置條件 |
|---|---|
| S0→S1 | mode ≠ NOT_APPLICABLE;exact path 符合 manifest;目標乾淨(REUSABLE clean / EPHEMERAL 不存在或 clean-correct) |
| S1→S4 | 只碰 allowed_files;commit;normal push(無 force) |
| S5→S6 GREEN | exact-head required CI 於 **PR head SHA** 成功 |
| S6 GREEN→S7 | worktree clean;EPHEMERAL → remove + 驗證消失;REUSABLE → detach origin/main + 驗 clean;**local + remote task branch 保留**(PR OPEN 期間為正常狀態,非治理失敗) |
| S6 RED | 停留 S5;保留 worktree;回報 blocker;不視為治理失敗 |
| S7→S8 | Reviewer 於 PR head SHA 審查(§2 required 時) |
| S8 PASS→S9 | REVIEW verdict PASS(如 required);merge_policy 允許;授權在場(CORE §7) |
| S9→S10 | PR MERGED;origin/main 含 merge commit;無任何 worktree checkout 該 branch;無需 force;branch 名稱與本任務一致 → `git branch -d` + `git push origin --delete`(remote 已被平台自動刪除 → 記 `ALREADY_ABSENT`) |
| S10→S11 | refs 消失驗證;canonical 與 durable artifacts 未動 |

失敗規則:任何前置不成立 → **停在原狀態、回報唯一 blocker、不做 broad cleanup、不 force**。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §5 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §7 -->
## §7 Cleanup 政策(Owner Additional Decision 8)

- normal lifecycle cleanup(S6→S7、S9→S10)**屬原任務授權範圍**,不需也不得另開 cleanup task。
- broad workspace cleanup = HIGH → STRATEGIC path + STANDALONE 授權 + quarantine / manifest / SHA-256 保障。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §7 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §8 -->
## §8 PR-Open Gate(摘要)

PR OPEN 期間:local / remote task branch 保留;durable artifacts 保留;canonical repo 不動;ephemeral worktree 於 CI green 後移除。
保留 ephemeral worktree 的唯一合法理由:CI pending / failed、dirty 無法安全移除、audit / fix 進行中、Owner override。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §8 -->

<!-- BEGIN SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §9 -->
## §9 Owner Override 格式

```
Retain this task worktree: <REASON>
Retain this local branch after merge: <REASON>
Retain this remote branch after merge: <REASON>
```

- 必附理由。Worker 回報:retained path / branch、HEAD、reason、retention expiry 或重審條件、cleanup pending。
- Planner 不得自行推定 override。
<!-- END SOURCE: ROUTING_AND_LIFECYCLE.md SECTION: §9 -->

<!-- BEGIN SOURCE: WORKER_TASK_TEMPLATE.md SECTION: §R -->
## §R 渲染規則(manifest → Worker prompt)

- **R1** 僅 PLANNER_COMPILER 可渲染;渲染前 manifest lint(schema L1–L22)必須全 PASS。
- **R2** 確定性:同一 manifest 渲染兩次必須逐字相同(時間戳只存在 manifest,不進 prompt)。
- **R3** 每個 `{{slot}}` 必須由 manifest 解析;有未解析 slot → 渲染 FAIL(禁止留佔位、禁止猜預設值)。
- **R4** 條件區塊:
  - 授權區塊依 `authorization.class` 三選一(§T-AUTH)
  - lifecycle 區塊只嵌入 `worktree.mode` 對應段,其他 mode 的規則**不得出現**
  - Post-Merge Branch Cleanup Gate 只在 `policies.merge ≠ none` 時嵌入
  - reattach 段只在 `worktree.reattach=true` 時嵌入
- **R5** prompt 尾端附錄完整 manifest 原文(traceability;標示 generated copy)。
- **R6** 頭部標記 `compiled_from: control_plane <version>` + `task_id` + `source_path` + computed `manifest_sha256`。
- **R7** 核心摘錄區(evidence / check-state / precedence / no-fabrication 精簡版)為模板固定文字,屬 generated 內容,允許出現在渲染產物。
- **R8**(R2.1)Memory 條件渲染:
  - `memory.read.mode ≠ none` → 嵌入 bounded selection 指示(selectors + max_entries);Planner 不得把整份 MEMORY_LOG 內容貼進 prompt
  - `memory.write.mode = allowed` → 才嵌入 Memory Write 區塊(exact path / purpose / core_v1);否則嵌入明確的 `Memory Write: FORBIDDEN` 行
  - 只可引用**本專案** repo-local memory(跨專案隔離)
- **R9**(R2.1)Project Attachment 區塊(§T-BODY 之 Attachment & Version Check)為固定嵌入,不可省略。
- **R10**(R2.1)精簡與 mode-specific:只渲染選定 worktree mode、適用的 authorization、實際 tests 與非空 task 欄位;不得嵌入其他 mode、整份 memory 或重複政策全文。manifest 原文只在附錄出現一次。

---
<!-- END SOURCE: WORKER_TASK_TEMPLATE.md SECTION: §R -->

<!-- BEGIN SOURCE: WORKER_TASK_TEMPLATE.md SECTION: §T -->
## §T Worker Prompt 模板

### §T-AUTH 授權區塊(三選一)

**[A] authorization.class = NONE**
```
No Owner Authorization required — LOW read-only task
(無 repo mutation、無 DB、無外部副作用;AGENT_CORE §7)。
```

**[B] authorization.class = SINGLE_PROMPT**
```
Owner Authorization: {{authorization.token}}

Authorization Handling:
- 若上行為 PENDING_OWNER_TOKEN:本任務尚未授權,Worker 必須以 WORKER_WAITING_OWNER 停止。
- 若為 Owner 置換之 token:本訊息即授權與 spec;首個非空白行含正確 token 即進入 Attachment。
- 不得要求額外 standalone 確認。
- 本 token 僅適用 task_id {{task.id}};任務終止、完成或 scope 變更即失效;不得繼承。
```

**[C] authorization.class = STANDALONE**
```
本任務為 HIGH:{{authorization.high_risk_reason}}。
Standalone Owner Authorization required — token 不嵌入本 spec,
必須由 Owner 於獨立訊息提供。未收到 → WORKER_WAITING_OWNER。
```

### §T-BODY 任務本體

```
[Executable Worker Task — {{task.name}}]
task_id: {{task.id}} | type: {{task.type}} | risk: {{task.risk_class}} | path: {{task.routing_path}}
compiled_from: control_plane {{control_plane.version}} @ {{control_plane.source_path}}
manifest_sha256: {{manifest_sha256}}

Project / Repo
- Project Path: {{repo.project_path}}
- Base: {{repo.base_branch}} @ {{repo.base_commit}}
- Task Branch: {{repo.task_branch}}
- Worktree Mode: {{worktree.mode}}{{if reattach}} (reattach; PR: {{worktree.pr_ref}}){{end}}
- Worktree Path: {{worktree.path}}

Core Rules(generated excerpt)
- 未執行 = NOT_RUN;evidence 必綁當前 HEAD SHA,否則 STALE
- [Confirmed]/[Inferred]/[Unknown]/[Risk];不把計畫寫成完成、不把建議寫成授權
- Fact precedence:live state > head-bound evidence > current handoff > MEMORY_LOG
  (memory / 歷史 report 不得覆蓋 live evidence;衝突 → 標 STALE / OUTDATED,live 勝)
- lifecycle 狀態宣稱必附 command / ref evidence
- 唯讀遇 dirty → WARN 續行;本任務要寫入的 workspace dirty → STOP
- 無 STANDALONE 授權永不 force;只碰本 manifest 點名的 path 與 branch

Project Attachment & Version Check(AGENT_CORE §12;不可省略)
1. 定位 canonical repo root;確認 = {{repo.project_path}}
2. 檢查 .ai/ 存在,且四必要檔齊全:
   {{project_attachment.required_context_files}}
   `.ai` 或 agent-profile 缺失 → 本輪降級為 ENTRY_CHECK / BOOTSTRAP_READINESS,不進實作;
   `.ai` 已存在但必要 context 缺失 → 依 {{project_attachment.missing_context_policy}}:
   entry_check = 降級為 ENTRY_CHECK;stop = STOP
3. 讀 {{project_attachment.profile_path}};解析 shared control plane path /
   control_plane_version / schema_version / repo restrictions
4. 驗證 profile 指定版本與本 prompt 的 {{control_plane.version}} 相容,且
   profile / manifest / control plane schema_version 一致;
   version 不相容 → STOP(A_VERSION_MISMATCH);schema 不相容 → STOP(A_SCHEMA_MISMATCH)
5. 驗證 profile canonical repo、context 與 memory 路徑均屬本專案;
   跨專案 reference → STOP(A_CROSS_PROJECT),不得載入
6. profile_sha256_or_version 為 UNKNOWN 時,記錄實際讀到的值於 Handoff

Phase 0A — Live State Verification(L4 最終事實來源)
- pwd / rev-parse toplevel / branch --show-current / git-dir / status --short / HEAD / upstream + ahead-behind
- 凍結 canonical dirty inventory(before-state);有 DB → 僅 read-only 驗證
- live state 與 memory / handoff 衝突 → live 勝;衝突條目標 STALE / OUTDATED 並記入 Handoff

Phase 0B — Project Context Load(L2)
- .ai/ai-context/PROJECT_PROFILE.md、PROJECT_CONTEXT.md、RUNBOOK.md
- 摘要:risk_domains / do_not_touch / hard_gates / 本任務相關限制
- Memory(bounded;AGENT_CORE §14):
  mode: {{memory.read.mode}};selectors: {{memory.read.selectors}};
  max_entries: {{memory.read.max_entries}}
  只讀取符合 selectors 的條目,至多 max_entries 條;不得整份載入 MEMORY_LOG;
  memory 只是歷史上下文,不得當作 current branch / PR / CI / DB / runtime 狀態

Worktree Rules({{worktree.mode}} 專屬;generated from ROUTING §4)
{{worktree_mode_block}}

Goal
- {{goal}}

Allowed Writes(exact;空清單 = read-only)
{{scope.allowed_files}}

Protected(invariant;不可觸,review 時重算 hash)
{{scope.protected_paths}}

Forbidden
- {{scope.forbidden_subsystems}}
- policies: db={{policies.db}} runtime={{policies.runtime}} external={{policies.external_side_effects}}
- ROUTING §6 不變式 I1–I4

Memory Write
{{if memory.write.mode == allowed}}
- ALLOWED(本任務授權範圍內):僅可 append 至 {{memory.write.allowed_path}}
- purpose: {{memory.write.purpose}}
- entry format: core_v1 — timestamp / task_id / source / repo+head+PR binding /
  classification / confirmed_facts / unresolved_risks / supersedes 或 superseded_by
- append-only;不得修改或刪除既有條目;修正舊事實用 superseding entry
{{else}}
- FORBIDDEN:本任務不得寫 MEMORY_LOG 或任何 memory / governance 檔案
{{end}}

Steps
{{steps}}

Verification(全部附 head SHA;未執行 = NOT_RUN)
{{tests:  - {name}: {cmd} [side_effects_allowed: {side_effects_allowed}]}}
- git diff --check
- changed paths == Allowed Writes(exact)
- invariant pins hash 不變:{{invariant_pins}}
{{if policies.pr != none}}- exact-head required CI at PR head SHA{{end}}

Success Criteria
- {{success_criteria}}
- 選定 mode 的 lifecycle 動作完成;canonical 未動;unrelated worktrees 未動;無未授權外部副作用

Stop Conditions
- .ai / agent-profile 缺失 → ENTRY_CHECK;必要 context 缺失(依 missing_context_policy);
  Shared Core version/schema 不相容(A_VERSION_MISMATCH / A_SCHEMA_MISMATCH);跨專案 attachment(A_CROSS_PROJECT)
- canonical dirty inventory 改變;選定 worktree dirty / branch 錯誤;任何操作需要 force
- changed-path allowlist 失敗;required test 或 exact-head CI FAIL
- cleanup 會影響 unrelated 對象;DB / registry / publication 授權缺失
- 授權狀態為 PENDING_OWNER_TOKEN / 未收到 standalone 授權
- 任務要求寫 memory 但 manifest memory.write.mode ≠ allowed

Handoff Output(必填)
- Attachment 結果:.ai 四檔狀態、profile 解析值、control plane 版本驗證
- Phase 0A/0B 摘要;before / after state;STALE / OUTDATED 標記清單(如有)
- Worktree Mode 與 exact path lifecycle 結果(附 command evidence)
- branch / base / HEAD;modified files;實際執行的命令
- tests 實際結果:PASS / FAIL / NOT_RUN + head SHA
- push / PR / exact-head CI 狀態
- reusable restored 或 ephemeral removed:YES / NO / NOT_APPLICABLE + evidence
- local / remote task branch 狀態;canonical touched: YES/NO;unrelated touched: YES/NO
- DB / registry / publication / external side effects:NONE / DETAILS
- memory entries written:NONE / 條數 + path + entry id(附 evidence);
  candidate memory entries proposed(如有,標 CANDIDATE — NOT WRITTEN)
- Owner override used:YES / NO + reason;remaining blockers
- Final: WORKER_{COMPLETE | COMPLETE_WITH_RISKS | PARTIAL | BLOCKED | WAITING_OWNER}

{{if policies.merge != none}}
Post-Merge Branch Cleanup Gate
1. 驗 PR state = MERGED
2. fetch origin
3. 驗 origin/main 含 merge commit
4. 驗無任何 worktree checkout 該 branch
5. 驗 cleanup 無需 force
6. git branch -d {{repo.task_branch}}
7. git push origin --delete {{repo.task_branch}}(remote 已被平台刪除 → 記 ALREADY_ABSENT)
8. 驗 local / remote refs 消失
9. 驗 canonical 與 durable artifacts 未動
任一失敗 → STOP cleanup、保留 branch、回報唯一 blocker、不 force
{{end}}

=== 附錄:Task Manifest 原文(generated copy;source of truth 為 manifest 檔案)===
{{manifest_yaml}}
```

---
<!-- END SOURCE: WORKER_TASK_TEMPLATE.md SECTION: §T -->

<!-- BEGIN SOURCE: WORKER_TASK_TEMPLATE.md SECTION: §P -->
## §P active_task.md Compatibility Projection(過渡期 — Owner Decision 4)

- **P1 產生者**:僅 PLANNER_COMPILER。只有 `active_task_projection.enabled=true` 時,才於每次 manifest 建立 / 更新後重新投影並寫至 `output_path`;CEO / CTO / Worker 不得手動編輯。`enabled=false` 時不得產生或更新 view。
- **P2 內容 = manifest 嚴格子集**,禁止出現 manifest 沒有的規則或授權:

```
<!-- AUTO-GENERATED COMPATIBILITY VIEW — DO NOT EDIT -->
<!-- source: <manifest 路徑> -->
<!-- manifest_sha256: <hash>(active_task_projection.manifest_sha256_required=true 時必填)-->
<!-- compiled_from: control_plane <version> -->

# Active Task: {{task.id}} — {{task.name}}
- Type / Risk / Path: {{task.type}} / {{task.risk_class}} / {{task.routing_path}}
- Goal: {{goal}}
- Repo / Base / Branch / Worktree: {{repo.*}} {{worktree.*}}
- Allowed / Protected / Forbidden: (摘要)
- Tests: (清單)
- Memory: read={{memory.read.mode}} write={{memory.write.mode}}
- Authorization: {{authorization.class}} — token 狀態原樣呈現
  (PENDING_OWNER_TOKEN 不得改寫成已授權)
- Status: {{status.*}}
```

- **P3 Drift detection**:
  - 檢查點:Planner 每次編譯時;Independent Reviewer 於 review 時。
  - 方法:自 manifest 重新投影 → 與現存 view 逐字 diff;有差 → `DRIFT [Risk]`,**以 manifest 為準**,重寫 view 並記錄事件。
  - banner 缺失或 `manifest_sha256` 與 manifest 實際 hash 不符 → 視為被手動編輯 → `DRIFT`。
- **P4 Deprecation**(≥2 個 pilot 成功且 Owner 裁決後):
  1. 新 manifest 預設 `active_task_projection.enabled: false`。
  2. `active_task.md` 以 stub 取代(只含指向 manifest 的 pointer + banner)。
  3. 觀察期(建議 2 週)無讀取需求 → archive 該檔,更新 roadmap 文件引用。
<!-- END SOURCE: WORKER_TASK_TEMPLATE.md SECTION: §P -->

<!-- BEGIN SOURCE: TASK_MANIFEST.schema.yaml SECTION: WHOLE_FILE -->
```yaml
# TASK_MANIFEST.schema.yaml — 任務清單 schema 與 lint 規則
# control_plane_version: 1.1.0-draft.1
# status: DRAFT_FOR_OWNER_REVIEW
# authority: 每個 task 恰有一份 manifest,是該 task 的唯一 source of truth(L3;Owner Decision 4)。
#            active_task.md 只是由 manifest 投影的 compatibility view(WORKER_TASK_TEMPLATE §P);
#            兩者不一致時以 manifest 為準。
# 產生者: 僅 PLANNER_COMPILER(ROLE_PROFILES R4)。
# 分層:  L1 Shared Control Plane / L2 repo-local .ai / L3 本 manifest / L4 live state(AGENT_CORE §10)。

schema_version: 1

control_plane:                    # L1 繫結(R2.1 新增)
  version: "1.1.0-draft.1"        # 必須與 agent-profile 解析出的 Shared Core 版本相容
  schema_version: 1
  source_path: ""                 # shared control plane 目錄(例 <WORKSPACE>/prompt/control-plane-v1)

project_attachment:               # L2 繫結(R2.1 新增;AGENT_CORE §12)
  profile_path: ".ai/agent-profile.yaml"
  required_context_files:
    - .ai/ai-context/PROJECT_PROFILE.md
    - .ai/ai-context/PROJECT_CONTEXT.md
    - .ai/ai-context/RUNBOOK.md
    - .ai/ai-memory/MEMORY_LOG.md
  missing_context_policy: "entry_check"   # enum: entry_check|stop(缺失時降級或停止;不得逕行實作)
  profile_sha256_or_version: ""   # allowed:64-hex SHA-256|profile version string|UNKNOWN;Worker 於 handoff 記錄實際值,不得暗改 manifest

task:
  id: ""                          # 必填、唯一;例 P600G-T1
  name: ""
  type: ""                        # enum: feature|bugfix|test|docs|metadata|merge|pr_fix|analysis|maintenance|entry_check
  risk_class: ""                  # enum: LOW|MEDIUM|HIGH(AGENT_CORE §6;Owner 可上調不可下調)
  routing_path: ""                # enum: FAST|TECHNICAL|STRATEGIC(ROUTING §0/§1)
  source_decision: ""             # CTO/CEO 輸出引用;FAST 可空
  created_by: planner             # 固定值;其他角色不得產 manifest
  created_at: ""                  # ISO 8601

goal: ""                          # non-empty string;Worker prompt 的單一目標
steps: []                         # non-empty ordered string list;每步不得超出 scope/policies
success_criteria: []              # non-empty observable string list

repo:
  project_path: ""
  base_branch: "main"
  base_commit: ""                 # 未知填 UNKNOWN,不得杜撰
  task_branch: ""                 # NOT_APPLICABLE 任務通常留空

worktree:
  mode: ""                        # enum: NOT_APPLICABLE|REUSABLE|EPHEMERAL(ROUTING §4)
  reattach: false                 # true = 既有 PR fix / audit continuation(原 Mode D)
  path: ""                        # exact path;mode=NOT_APPLICABLE 留空;禁止 fallback 路徑
  pr_ref: ""                      # reattach=true 必填(PR number / URL)

scope:
  allowed_files: []               # exact 允許變更路徑;空 = read-only task
  protected_paths: []             # 不變式路徑(審查時重算 hash)
  forbidden_subsystems: []
  pins:                           # invariant: hash 不得變;before_after: 記錄前後 hash
    - path: ""
      type: ""                    # enum: invariant|before_after

policies:
  db: "none"                      # enum: none|read_only|write_authorized(write_authorized 必為 HIGH)
  runtime: "none"                 # enum: none|tmp_only|logs_allowed
  external_side_effects: "none"   # enum: none|listed
  external_list: []               # listed 時逐項列出,逐項套 CORE §6 分級
  pr: "none"                      # enum: none|draft|ready
  merge: "none"                   # enum: none|after_review|after_ci_and_review
  cleanup: "standard_lifecycle"   # enum: standard_lifecycle|retain_with_reason|owner_override
  cleanup_reason: ""              # retain/override 時必填

tests:
  - name: ""
    cmd: ""
    required: true
    side_effects_allowed: ""      # enum: none|tmp_only|logs|db_sandbox(必填;CORE §9)

memory:                           # R2.1 新增(AGENT_CORE §14;不另立 memory policy 文件)
  read:
    mode: "relevant"              # enum: none|relevant|bounded_recent
    selectors: []                 # allowed prefixes:task_id:|branch:|pr:|risk_domain:|keyword:;只可選本專案 MEMORY_LOG
    max_entries: 0                # mode≠none 時必 ≥1
  write:
    mode: "forbidden"             # enum: forbidden|allowed(Worker 預設不得寫;CORE §14 規則 4-5)
    allowed_path: ""              # allowed 時必填;預設 .ai/ai-memory/MEMORY_LOG.md
    purpose: ""                   # allowed 時必填
    entry_schema: ""              # allowed 時必為 core_v1(CORE §14 規則 9-10)

context:                          # R2.1 新增(AGENT_CORE §13)
  live_state_required: true       # 固定 true:L4 live state 為最終事實來源
  head_binding_required: true     # 固定 true:evidence 必綁 current head SHA
  stale_evidence_policy: "mark_stale"   # enum: mark_stale|reject(未綁 head 的證據如何處置)

authorization:
  class: ""                       # enum: NONE|SINGLE_PROMPT|STANDALONE(CORE §7)
  token: "PENDING_OWNER_TOKEN"    # NONE → NOT_REQUIRED;SINGLE_PROMPT → PENDING_OWNER_TOKEN(僅 Owner 可置換);
                                  # STANDALONE → SEPARATE_MESSAGE_REQUIRED(token 永不嵌入 spec)
  scope: ""                       # 必須等於 task.id
  high_risk_reason: ""            # risk_class=HIGH 時必填且精確列出 HIGH 動作;LOW/MEDIUM 留空

review:
  required: true
  mode: "fixed_head"              # 綁 PR head SHA
  independent: true               # reviewer ≠ author(ROLE_PROFILES R6)

evidence:
  head_sha_binding: true          # 固定 true;未綁當前 head 的證據自動 STALE(CORE §2)
  required:
    - git_status_before_after
    - changed_path_list
    - test_output_with_head_sha

active_task_projection:           # R2.1:取代 R2 的 compat.write_active_task_view
  enabled: true                   # boolean;過渡期預設 true;false 時不得產生或更新 view
  output_path: ""                 # 例 00-Plan/roadmap/active_task.md(repo 相對路徑)
  manifest_sha256_required: true  # boolean;enabled=true 時固定 true,view banner 必含實際 manifest hash

status:                           # 由各角色回填,不由 Planner 預填
  worker_final: "NOT_RUN"         # WORKER_<STATUS> 或 NOT_RUN
  review_verdict: "NOT_RUN"       # PASS|PASS_WITH_RISKS|FAIL|BLOCKED|NOT_RUN
  lifecycle_state: "S0"           # ROUTING §5 狀態機

# ─────────────────────────────────────────────────────────────
# Manifest Lint Rules(Planner 渲染前必跑;任一 FAIL → 不得渲染 Worker prompt)
#
# L1  task.id 非空且唯一;authorization.scope == task.id;goal、steps、success_criteria 非空
# L2  required 動作與 forbidden / policies 不矛盾:steps 與 tests 所需動作
#     不得落在 forbidden_subsystems,也不得超出 policies
#     (例:test 需寫 DB 但 policies.db=none → FAIL)
# L3  每條 required=true 的 test 都有 side_effects_allowed
# L4  type=invariant 的 pin.path ⊆ protected_paths 且 ∉ allowed_files
#     (合法新增/修改的檔案用 before_after,不用 invariant)
# L5  mode ∈ {REUSABLE, EPHEMERAL} → worktree.path 非空(exact);
#     mode=NOT_APPLICABLE → path 空,且不建 task branch(除非任務明確需要)
# L6  policies.merge ≠ none → review.required=true 且 review.independent=true
# L7  authorization.class 與 risk_class 對映:LOW→NONE、MEDIUM→SINGLE_PROMPT、
#     HIGH→STANDALONE;只允許向上覆寫(如 MEDIUM→STANDALONE),禁止向下;
#     HIGH 時 authorization.high_risk_reason 非空且精確列出 HIGH 動作
# L8  token 值域:NONE→NOT_REQUIRED;SINGLE_PROMPT→PENDING_OWNER_TOKEN 或 Owner 置換值;
#     STANDALONE→SEPARATE_MESSAGE_REQUIRED。
#     PENDING 狀態下 manifest 不得標示已授權;渲染出的 prompt 必含 WAITING_OWNER 語義
# L9  policies.db=write_authorized、或 external_list 含 CORE §6 HIGH 項 → risk_class=HIGH
# L10 allowed_files 為空 → read-only task:tests 只可 side_effects_allowed ∈ {none, tmp_only};
#     policies.pr=none;policies.merge=none;memory.write.mode=forbidden
# L11 policies.pr ∈ {draft, ready} → 隱含 push → risk_class ≥ MEDIUM
# L12 cleanup=standard_lifecycle → 不得存在引用本 task 的獨立 cleanup 任務;
#     cleanup ∈ {retain_with_reason, owner_override} → cleanup_reason 非空
# L13 reattach=true → worktree.pr_ref 非空且 mode=EPHEMERAL
# L14 risk_class=HIGH → routing_path=STRATEGIC
#
# ── R2.1 新增 ──
# L15 project_attachment.profile_path 必須是本 repo `.ai/agent-profile.yaml`;
#     required_context_files 必含四個 .ai 必要檔;profile_sha256_or_version 非空;
#     profile 可解析且不得含 CORE §10 禁止鍵(發現禁止鍵 → 該鍵無效 + [Risk],T22)
# L16 `.ai` 或 profile 缺失 → A_NO_ATTACHMENT → ENTRY_CHECK / BOOTSTRAP_READINESS;
#     其他必要 context 缺失時 missing_context_policy ∈ {entry_check, stop} 並依值 routing;
#     不得編譯 routine implementation
# L17 control_plane.version / schema_version / source_path 必填;schema_version 與本 schema
#     及 profile binding 相同;version 與 profile 相容;version mismatch → A_VERSION_MISMATCH;
#     schema mismatch → A_SCHEMA_MISMATCH;兩者均 STOP
# L18 memory.read.mode ∈ {none, relevant, bounded_recent};
#     mode≠none → selectors 非空且 max_entries ≥ 1;
#     每個 selector 必有允許 prefix 且 task-relevant;mode=none → selectors 空且 max_entries = 0
# L19 memory.write.mode=allowed → allowed_path 非空、purpose 非空、entry_schema=core_v1、
#     authorization.class ≠ NONE、risk_class ≥ MEDIUM
#     (Planner 只能提案,生效依 task authorization;CORE §14 規則 8)
# L20 memory.read.selectors 與 memory.write.allowed_path 只可指向本專案 repo-local
#     `.ai/ai-memory/`;absolute path、`..` 或其他 repo reference → A_CROSS_PROJECT + FAIL
# L21 context.live_state_required=true、context.head_binding_required=true、
#     evidence.head_sha_binding=true(固定;false → FAIL);stale_evidence_policy ∈ {mark_stale, reject}
# L22 active_task_projection.enabled=true → output_path 非空、manifest_sha256_required=true;
#     generated view 必含實際 manifest SHA-256,缺失/不符/重新投影 diff → DRIFT + FAIL;
#     enabled=false → output_path 空且 manifest_sha256_required=false,不得產生 view
# ─────────────────────────────────────────────────────────────
```
<!-- END SOURCE: TASK_MANIFEST.schema.yaml SECTION: WHOLE_FILE -->
