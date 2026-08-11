# WORKER_TASK_TEMPLATE — 渲染規則與模板

```
control_plane_version: 1.1.0-draft.1
status: DRAFT_FOR_OWNER_REVIEW
authority: manifest → Worker prompt 與 manifest → active_task.md view 的唯一渲染定義。
           渲染產物為 build output,不得手工維護、不計入 durable files。
```

---

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

## §C Compiled Role Prompts(build 規則)

- `compiled/HANDOFF_REPORTER.compiled.md` 與 `compiled/PLANNER_COMPILER.compiled.md` 由 CORE + ROLE_PROFILES + ROUTING + 本檔生成,供 web 對話貼上使用。
- 必帶 `compiled_from: control_plane <version>`;durable files 任一改版 → 必須重新編譯並更新版本戳。
- 不得手工編輯 compiled 檔;內容與 durable files 不一致 → 以 durable files 為準並重編譯。
- Compiled prompt 只含規則與格式,**不得內嵌任何專案的 memory 內容**(跨專案隔離;CORE §14 規則 11)。
