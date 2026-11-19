# ROUTING_AND_LIFECYCLE — 路徑決策與生命週期狀態機

```
control_plane_version: 1.1.0-draft.1
status: DRAFT_FOR_OWNER_REVIEW
authority: 本檔是 routing、project attachment states 與 worktree / branch lifecycle
           的唯一 source of truth。attachment 契約本文見 AGENT_CORE §12。
```

---

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

## §2 Reviewer 需求規則

Independent Reviewer **required** 當任一成立:

- merge 進 canonical branch
- 觸及 evidence 敏感路徑(hash inventory / generation registry / catalog / protected paths)
- risk_class ≥ MEDIUM 且含 push / PR
- 作者與審查者必須分離的任務(reviewer ≠ author)

純 read-only 分析(無 PR、無 merge)可免 review。

## §3 CEO / CTO Cadence

- CTO:僅 TECHNICAL / STRATEGIC 觸發時進場;**不做每輪全 repo review**。
- CEO:僅 STRATEGIC;另可由 Owner 設定週期性檢視(頻率由 Owner 決定,預設無)。
- routine FAST 任務不經 CEO、不需 CTO 重審。

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

## §6 不變式(取代 legacy 長禁令清單)

- **I1** 無 STANDALONE 授權,永不 force:`branch -D`、`--force`、`rm -rf`、`reset --hard`、`git clean`、任意 `worktree prune`、dirty worktree 刪除、unmerged branch 刪除。
- **I2** 不可作為 cleanup 對象:protected paths、committed durable artifacts / reports / evidence、`.ai`、DB、data、runtime、logs、dependencies。
- **I3** 只碰 manifest 點名的 path 與 branch;unrelated branch / worktree 不可觸。
- **I4** canonical repo 對 Worker 唯讀(除非 manifest 明確把該路徑列入 allowed_files)。

## §7 Cleanup 政策(Owner Additional Decision 8)

- normal lifecycle cleanup(S6→S7、S9→S10)**屬原任務授權範圍**,不需也不得另開 cleanup task。
- broad workspace cleanup = HIGH → STRATEGIC path + STANDALONE 授權 + quarantine / manifest / SHA-256 保障。

## §8 PR-Open Gate(摘要)

PR OPEN 期間:local / remote task branch 保留;durable artifacts 保留;canonical repo 不動;ephemeral worktree 於 CI green 後移除。
保留 ephemeral worktree 的唯一合法理由:CI pending / failed、dirty 無法安全移除、audit / fix 進行中、Owner override。

## §9 Owner Override 格式

```
Retain this task worktree: <REASON>
Retain this local branch after merge: <REASON>
Retain this remote branch after merge: <REASON>
```

- 必附理由。Worker 回報:retained path / branch、HEAD、reason、retention expiry 或重審條件、cleanup pending。
- Planner 不得自行推定 override。
