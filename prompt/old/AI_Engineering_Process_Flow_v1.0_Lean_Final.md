# AI Engineering Process Flow — v1.0 Lean Final

本文件說明四份流程文件的用途、使用時機與預設路由。

核心目標：

- 最小文件治理；
- 最大程度實作；
- CTO／CEO 按需介入，不是每輪必經；
- Planner 是一般情況下產生施工單的最後一站；
- 高風險任務才啟用完整治理與 standalone Owner authorization。

---

# 1. 四份文件與角色

| 文件 | 主要角色 | 何時使用 | 何時省略 | 不得做 |
|---|---|---|---|---|
| Conversation Handoff v3.1 | 壓縮對話事實與授權／執行鏈 | 換對話、換模型、Worker STOP／BLOCKED、長對話需交接 | 同一對話短續作、極小 continuation | 不做 repo audit、不做架構裁決、不把 report 當獨立驗證 |
| Planner v5.3.1 | 產生下一個單一、可執行 Worker Packet | 幾乎所有下一輪實作、CI、PR、merge、continuation | 已有 exact Packet 只需直接執行 | 不實作、不代替 Owner 做產品決策、不預設加重型 gate |
| CTO v2.1 | 收斂技術邊界與 fix boundary | 不知道改哪一層、data flow 不明、語意衝突、scope 持續擴張 | 已知 bug、已知 CI 修正、已驗證 merge、單一路徑 delta | 不重做 Planner、不全面 audit、不把治理文件排 P0 |
| CEO v2.1 | 產品方向與階段裁決 | 多個合理方向、是否進下一 phase、產品價值或風險政策未決 | 單純 bug、CI、merge、routine cleanup、小型 continuation | 不重做 CTO、不設計低階實作、不自動授權高風險動作 |

---

# 2. 預設流程

## 2.1 一般功能、bug、CI、PR 修正

```text
Worker
→（只有換對話時）Conversation Handoff
→ Planner
→ 下一個 Worker
```

CTO 與 CEO 都不介入。

## 2.2 極小 Continuation

```text
Worker report
→ Planner Minimal Continuation Delta
→ Worker
```

適用：只差 1–3 個 exact paths、單一 stale assertion、CI 小修或明確 lifecycle action。

## 2.3 技術 scope／架構／資料流不清

```text
Worker
→ Handoff
→ CTO
   ├─ fix boundary CONFIRMED → Worker
   └─ fix boundary PROVISIONAL → Planner → Worker
```

## 2.4 產品方向或階段不清

```text
Worker
→ Handoff
→（技術不確定時才加 CTO）
→ CEO
   ├─ WORKER  → Worker
   ├─ PLANNER → Planner → Worker
   ├─ CTO     → 最小 CTO re-analysis
   └─ NONE    → 單一 Owner decision question
```

## 2.5 高風險任務

```text
Worker / Handoff
→ CTO
→ CEO
→ Planner
→ standalone Owner Authorization
→ Worker
```

高風險包括：production DB write／migration／backfill、deployment／release、force delete／force remove、secrets／payments、external publication、不可逆資料刪除。

此路由才考慮：Tier 2 runtime preflight、FULL Judge、durable evidence seal、完整 read-after-write 與 recovery gate。

---

# 3. 快速選擇表

| 當前狀況 | 使用文件 |
|---|---|
| 要換對話，怕前情遺失 | Handoff |
| 下一步已知，要變成 exact Packet | Planner |
| 不知道應改哪一層或 scope 多大 | CTO |
| 不知道是否值得做或先做哪個方向 | CEO |
| 單一 bug／CI fail／PR fix | Planner；換對話才加 Handoff |
| 只差一個 allowlist path | Planner Minimal Continuation Delta |
| 跨 API／DB／frontend 且 boundary 不明 | CTO → Planner |
| 兩個產品方向皆合理 | CEO；技術有疑問才先 CTO |
| Production DB／deploy／不可逆操作 | CTO → CEO → Planner → standalone authorization |

---

# 4. Authority 與授權

1. Live state 優先於舊 handoff。
2. current working directory 不得自動成為 authority。
3. Handoff 只保存事實與來源，不自動延伸上一輪授權。
4. 一般低風險任務可用 single-prompt authorization。
5. 高風險任務必須 standalone Owner authorization。
6. 平台／harness permission 與 Owner authorization 分開處理。
7. Dirty ownership 不明時不得接管。
8. 外部 mutation 結果不明時，先 bounded read-after-write，再決定是否重試。

---

# 5. Gate 啟用原則

## 預設關閉

一般小型任務不預設要求：

- 完整 `.ai` 讀取；
- 兩次 snapshot；
- FULL Judge；
- complete suite；
- browser journey；
- durable evidence package；
- 每個 cache 的 hash／mtime 盤點。

## 按風險啟用

- Dirty takeover／concurrency risk → 兩次 bounded snapshot。
- Auth／security／DB migration／跨模組 lineage → BOUNDED Judge 或 CTO review。
- Production／financial／irreversible → standalone authorization＋FULL／Tier 2 gates。
- Durable evidence 將成為另一個 repo 的 authority → evidence seal。
- User-facing flow真正需要 browser evidence → browser journey。

Packet 標為 mandatory 的 verification、Success Criteria 或 final-tree gate 未滿足時，不得改標 `NOT RUN` 後宣稱完成。

---

# 6. 下一個角色的決策樹

```text
需要保留跨對話事實？
YES → Handoff

技術 boundary 不清？
YES → CTO

產品方向／階段不清？
YES → CEO

scope 已清楚？
YES → Planner 或直接 Worker

只差極小 delta？
YES → Planner Minimal Continuation Delta
```

---

# 7. 文件版本與採用狀態

日常 default：

- `Personal_Web_Conversation_Handoff_Prompt_v3.1_Lean_Final.md`
- `Personal_Planner_Handoff_Prompt_v5.3.1_Lean_Final.md`
- `CTO_Technical_Review_Prompt_v2.1_Lean_Final.md`
- `CEO_Decision_Review_Prompt_v2.1_Lean_Final.md`

舊版：

- 停止作為 default；
- 僅保留 archive／high-risk reference；
- 不得與新版整段混用，避免 mandatory gate 衝突。

值得從舊版查閱的高風險內容：

- Tier 2 toolchain runtime side-effect preflight；
- durable evidence seal 詳細順序；
- portable dirty ownership takeover；
- post-merge branch cleanup preconditions；
- harness permission handling；
- production／DB／deployment 高風險 authorization 清單。

---

# 8. 最終原則

```text
Handoff：保存事實
CTO：收斂技術邊界
CEO：裁決價值與階段
Planner：產生施工單
Worker：實作與驗證
```

不要每輪四份全跑。

日常預設：

```text
Worker →（需要時 Handoff）→ Planner → Worker
```

只有當技術或產品決策真的不清楚時，才加入 CTO 或 CEO。
