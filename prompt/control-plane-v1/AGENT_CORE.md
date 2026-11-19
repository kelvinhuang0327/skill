# AGENT_CORE — Agent Control Plane 共用核心

```
control_plane_version: 1.1.0-draft.1
schema_compat: TASK_MANIFEST schema_version 1
status: DRAFT_FOR_OWNER_REVIEW — R2.1 產出,系統尚未啟用。
        兩個 pilot 成功且 Owner 裁決前,prompt/ 下四份 legacy prompt 仍為有效 fallback。
authority: 本檔是 Evidence 詞彙、check-state、task-status、Phase 0、STOP/WARN、
           風險分級、Authorization、架構分層(§10)、Project Attachment(§12)、
           Source Precedence(§13)、Memory Contract(§14)的唯一 source of truth。
           其他檔案只得以「AGENT_CORE §n」引用,不得複製全文。
           Repo-local profile 不得重新定義本檔任何內容。
```

---

## §1 Evidence 詞彙

| 標籤 | 意義 | 使用規則 |
|---|---|---|
| `[Confirmed]` | 本輪有直接證據:command output、檔案內容、對話原文 | 轉述型事實必須註明 source(例:`source = worker report; not independently audited`) |
| `[Inferred]` | 合理推論 | 不得升級為 `[Confirmed]` |
| `[Unknown]` | 資訊不足 | 不得補完、不得推論成事實 |
| `[Risk]` | 需注意的風險 | 必附影響範圍 |

## §2 Evidence state(檢查、測試、驗證與歷史證據的狀態值)

`PASS / FAIL / NOT_RUN / UNKNOWN / STALE / OUTDATED`

1. 預設值是 `NOT_RUN`。未實際執行的測試、命令、驗證一律 `NOT_RUN`,不得預填或改寫為 PASS。
2. **head-SHA binding**:每筆 repository / test / CI evidence 必須附產生當下的 HEAD SHA。
3. `STALE`:evidence 未綁定當前 head SHA、或已超出本任務 scope → 自動 `STALE`,不得當作本輪 PASS。歷史測試結果只能作為背景,不能作為當輪驗證。
4. `NOT_RUN` ≠ `FAIL`。只有 `FAIL` 觸發 required-check 失敗處理;`NOT_RUN` 必附原因。
5. `OUTDATED`:memory / handoff 中**已被 live state 反證**的歷史 evidence。它不是本輪可執行 check 的成功/失敗結果,不得當作 PASS。
6. `STALE` 表示未綁 current head、真偽未定;`OUTDATED` 表示已被較高順位 live evidence 反證。轉換與保留規則見 §13 / §14。

## §3 Task-status(任務終態代數)

`COMPLETE / COMPLETE_WITH_RISKS / PARTIAL / BLOCKED / WAITING_OWNER`

- 角色終態一律為 `<ROLE>_<STATUS>`(例 `WORKER_COMPLETE`、`PLANNER_BLOCKED`)。
- Independent Reviewer 另有對「被審變更」的 verdict:`PASS / PASS_WITH_RISKS / FAIL / BLOCKED`(見 ROLE_PROFILES R6)。
- 禁止各角色自創新終態。

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

## §5 STOP / WARN 語義

- **STOP**:立即停止、不修改任何檔案,輸出 STOP report(1 預期狀態 2 實際觀察 3 差異原因 4 風險 5 建議修正 scope)。
- **WARN**:記錄 evidence 後繼續。
- 唯讀角色或唯讀 mode 遇 canonical dirty / pre-existing staged / unrelated changes → **WARN + evidence,不 STOP**。
- 只有兩種情況 dirty 才 STOP:(a) 本任務需要寫入該 workspace;(b) dirty 使證據無法判定。
- STOP 條款只約束**角色自身行為**,不自動約束其審查或規劃的任務內容(該任務的邊界由其 manifest 決定)。
- 任何角色不得以 force 突破 STOP。

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

## §8 No-fabrication 規則

1. 不把計畫寫成已完成;不把 STOP / BLOCKED 寫成完成。
2. 不把歷史結果寫成未來能力;不把研究結果寫成可投注、可上線、可產品化。
3. 不把建議寫成已授權;前一輪的授權、DB、artifact、commit、push、worktree、cleanup 授權一律不繼承。
4. **Lifecycle 宣稱規則**(合併 legacy 各條):任何 worktree 移除、reusable 恢復、branch 刪除、merge、deployment、registry、publication 的狀態宣稱,必附實際 command output 或 ref evidence;否則只能寫 `NOT_RUN` / `[Unknown]`。
5. 不誇大成果;不把推論寫成事實;production-ready 宣稱需明確證據與 Owner 授權。

## §9 Side-effect 邊界詞彙(manifest 對應欄位)

- `db_policy`: `none | read_only | write_authorized`(write_authorized 必為 HIGH)
- `runtime_policy`: `none | tmp_only | logs_allowed`
- `external_side_effects`: `none | listed`(listed 時逐項列出,逐項套 §6 分級)
- 測試副作用:每條 required test 必有 `side_effects_allowed ∈ {none, tmp_only, logs, db_sandbox}`;副作用超出 allowance 或未確認 → 該測試 `NOT_RUN` + 原因。required 與 forbidden 的一致性由 manifest lint 保證(schema L2/L3)。

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

## §11 版本與引用

- Compiled prompt(Handoff Reporter / Planner)與 `active_task.md` view 為 **build output**:必帶 `compiled_from: control_plane vX.Y.Z`,不得手工維護,不計入 durable files。
- 修改本目錄任一 durable file 屬「control plane 變更」→ 依 ROUTING_AND_LIFECYCLE 為 STRATEGIC path,需 Owner 裁決。

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
