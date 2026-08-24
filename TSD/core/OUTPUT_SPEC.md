# TSD Output Spec

## Base Template

Base template：

- Runtime canonical path：`assets/base_template.docx`
- 該 `.docx` 為 **operator-supplied local build input**，不納入 Git canonical source；
  由 runtime materializer 以顯式輸入參數帶入並materialize 到上述路徑。
- 身分依據為檔案內容 hash，不是檔名或路徑。

---

## Required Variables

- `project_path`
- `workspace_path`
- `base_docx`
- `output_docx`
- `module_code`
- `module_label`
- `document_date`
- `source_type`
- `source_path` 或 `commit_range`

---

## Required Sections

輸出 TSD 時至少要維持：

1. 首頁目錄
2. `3 Module Description`
3. 各項 `3.x.1 異動說明`
4. `3.x.2 異動程式清單` — 內容與 placement 由 `core/CHANGE_PROGRAM_CONTRACT.md` 定義
5. `3.x.3 異動範例（Before / After）`
6. 附錄完整異動程式清單 — 內容由 `core/CHANGE_PROGRAM_CONTRACT.md` 定義

（`1.1 / 1.2 / 1.3` 為舊編號，已由 `3.x.1 / 3.x.2 / 3.x.3` 取代；舊編號僅得出現於明確標示為
historical / legacy 的敘述中。）

---

## Formatting Requirements

- 風格應跟隨 `EOS_v28_0421`
- 不可破壞目錄超連結
- 不可破壞附錄回鏈
- 程式範例區塊需保留 `v28` 的表格型 Before / After 呈現
- 路徑顯示應移除不必要的本機絕對路徑前綴

---

## OOXML Style Constraints（驗證過的生成規則，2026-04-28）

### 目錄（TOC）條目

- 每個 TOC 段落（`pStyle='21'`）內含 `<w:hyperlink>` 結構，**不可**整段用 `set_text()` 覆寫
- 正確做法：只更新 `<w:hyperlink>` 內的 content runs（section 號碼 run 與 title run），
  **保留** `<w:tab/>`、webHidden PAGEREF field runs 完整不動
- run rStyle 必須維持 `'ae'`；rFonts `eastAsia='標楷體'`

### 異動程式範例表格（diff cell）

- cell[1]（diff 內容格）原始有 66 runs / 45 `<w:t>` nodes；**不可**用單一 `set_text()` 整格覆寫
- 正確做法：移除所有原始 runs，逐行建立獨立 `<w:r>`，每行一個 `<w:t>` + `<w:br/>`
- 每行 `<w:rPr>` 顏色依 unified-diff 規則：

  | 行前綴 | `<w:color val>` | 其他 |
  |--------|----------------|------|
  | `---` / `-` | `B00020` | 紅，刪除 |
  | `+++` / `+` | `1F7A1F` | 綠，新增 |
  | `@@`  | `2F5597` | 藍 + `<w:b/>` |
  | 其他  | `333333` | 深灰，context/header |

- rPr 元素順序（符合 EOS_v28）：`rFonts` → `[b]` → `color` → `sz`
- `rFonts eastAsia='標楷體'`、`sz val='17'`（8.5pt）

### 附錄完整清單

- 附錄標題段落：`pStyle='v24heading1'`
- 各子群標題（`1. 項目名稱`）：`pStyle='v24heading2'`
- 檔案路徑清單：`pStyle='v24ListBullet'` ← **必須從 `appendix_title_idx + 2` 取 proto_bullet**，
  不可從 `+1`（那是 v24heading2 sub-heading）
- 回鏈段落（「回到主文第 N 項」）：`pStyle='none'`

---

## Companion Files

建議同步輸出：

1. `PATCH_DIFF_SUMMARY_WITH_FILES_vNN.md`
2. `PATCH_DIFF_DESCRIPTION_REVIEW_vNN.md` 或 validation 結果

---

## Minimal Structured Result

每個調整項目至少要有：

- `item_no`
- `title`
- `classification`
- `summary_bullets`
- `file_count`
- `files`
- `example_diffs`
- `confidence`

這八個欄位為 **section / 調整項目層級** 的結構化結果，不是 per-file 欄位。
`files` 為連結至 per-file 異動程式列舉的接點。
正式定義見 `core/CHANGE_PROGRAM_CONTRACT.md` Layer 1。

---

## Three-Template Output Differences

Updated: 2026-05-14 (三模板 TSD 架構 Step 1)

For full template mode rules see `core/TEMPLATE_MODE_RULES.md` and `core/TEMPLATE_MODE_OUTPUT_SPEC.md`.

### Template A — Large Migration Template

| Element | Format |
|---------|--------|
| 3.x.1 | Short paragraph / summary; describes **common migration pattern**; no per-file detail |
| 3.x.2 | `placement = appendix_only` — see `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 |
| 3.x.3 | One representative hunk showing the common migration transformation |
| Appendix file entries | see `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 (Template A row) |
| Appendix code blocks | Not required |
| Typical sections | Few (grouped by migration type) |

### Template B — Medium Feature Template (PRJ-A 預設)

| Element | Format |
|---------|--------|
| 3.x.1 | 15–30 char section summary; three-part 0421 or short-summary format |
| 3.x.2 | `placement = appendix_only` — see `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 |
| 3.x.3 | Representative complete method / hunk; Controller → Service → Object priority |
| Appendix file entries | see `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 (Template B row) |
| Appendix code blocks | Not required |
| Per-file summary | Only if user explicitly requests (→ B+C Hybrid; document will be longer) |

### Template C — Small Per-File Template

| Element | Format |
|---------|--------|
| 3.x.1 | 15–30 char section summary |
| 3.x.2 | `placement = inline_per_file` — see `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 |
| 3.x.3 | Per-file or per-group code blocks |
| Appendix file entries | see `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 (Template C row) |
| Appendix code blocks | Supported |
| Applied to medium/large project | MUST add `PASS_FOR_DEMONSTRATION` note in QA report |

### v19 Output Classification

| File | Template Classification |
|------|------------------------|
| `PRJ-A_20260505_v19_SHORT_CHANGE_SUMMARY.docx` | Template C prototype / B+C Hybrid demonstration |
| Appendix 90-entry per-file summaries | Template C behaviour (not Template B default) |
| Section grouping (10 sections) | Template B behaviour |

Template B does NOT default to per-file summaries. v19 per-file summaries belong to Template C.

---

## 3.x Section Format Rules (Final — 2026-05-05)

每個 3.x 項目必須包含下列三個子項：3.x.1、3.x.2、3.x.3。

---

### 3.x.1 異動說明 — 三段式格式

內容必須採用三段式結構（0421 格式）：

```
第一段：這一項是什麼，讓非技術 reviewer 可理解業務目的。
第二段：調整內容與影響（patch 為依據，精確描述）。
第三段：同類型異動主要涵蓋哪些檔案或改法（「本項目共涵蓋 N 個檔案，均為 X 模式」）。
```

**範例（良）：**
```
本項目調整 ECERT 電子認證連線客戶端設定，支援新版 REST 介面。

主要異動包括新增 ECERTRestClient 連線參數設定，更新 API 端點 URL 及逾時設定；
相關 Service 層方法同步更新呼叫邏輯。

本項目共涵蓋 3 個檔案，均為連線參數與 Service 方法調整。
```

**嚴格禁止出現在 3.x.1 主文中：**

```
Code Change Type:
Shared Purpose:
Same-Pattern Merge
Do Not Over-Merge
File-Level Completeness
Section Grouping Rationale:
Grouping Mode:
Code Change Pattern Grouping:
Business Function Grouping:
Mode A:
Mode B:
Hybrid:
[grouping analysis / engineering jargon]
```

這些分析標籤只能出現在分群理由報告（rationale report）和 QA 報告，不可進入 TSD 主文。

---

## Grouping Mode Metadata — Must NOT Appear in TSD Main Body

Updated: 2026-05-12

The TSD main document (3.x sections) must **NOT** declare or reference the grouping mode.
Grouping mode analysis belongs in the **rationale report** and **QA report** only.

**TSD 主文（3.1 ～ 3.N）中嚴格禁止：**

| Forbidden Label | Reason |
|-----------------|--------|
| `Grouping Mode:` | Internal QA metadata, not business content |
| `Code Change Pattern Grouping:` | Internal QA metadata |
| `Business Function Grouping:` | Internal QA metadata |
| `Mode A:` / `Mode B:` / `Hybrid:` | Internal QA metadata |
| `Code Change Pattern:` | Internal QA metadata |

**Allowed location:** QA report / rationale report (not part of the docx delivery file).

Violation of this rule = Gate B (Format) FAIL.

---

### 3.x.2 異動程式清單

3.x.2 的內容、section title 與 placement 由
**`core/CHANGE_PROGRAM_CONTRACT.md`（唯一 normative owner）** 定義。

本檔不再獨立宣告 3.x.2 的內容規則，亦不重述 template mode 與 placement 的對應表。3.x.2 超連結所指向的書籤命名與 hyperlink 結構，
仍由本檔 Hyperlink / Bookmark Rules 與 `SKILL.md` Rule 22 擁有。

---

### 3.x.3 代表範例 — Diff 表格

3.x.3 表格只允許放置 patch hunk 原文內容，且代表範例必須是「有邏輯意義的程式碼片段」。

#### 代表範例選擇規則（優先順序）

Updated: 2026-05-12 — Controller-first priority rule (see `core/EXAMPLE_SELECTION_RULES.md`)

| 優先順序 | 來源 | 觸發條件 |
|---|---|---|
| **Priority 1** | **Controller** — 第一個新增或異動的 method | section 包含 Controller 類別 |
| **Priority 2** | **Service** — 第一個新增或異動的 method | 無 Controller method 候選 |
| **Priority 3** | **物件類（DTO / BO / Request / Response / Entity / VO / Model）** — 第一個新增或異動的 method | 無 Controller / Service 候選 |
| **Priority 4** | **物件類程式片段** — field 新增 / enum value / 常數群組 / repository method 宣告 | 無物件類 method 候選 |
| **Priority 5** | 其他有邏輯意義的 hunk（config / constants / client / DAO） | Priority 1–4 皆無候選 — 須標記 WEAK |

**選擇規則細節（含 method 辨識信號、fallback flow、Mode A / B 適用性）：**
→ 參見 `core/EXAMPLE_SELECTION_RULES.md`（完整規則文件）

**QA gate：** Gate J — Representative Example Priority Gate（見 `core/QA_CHECKLIST.md`）

#### 禁止作為代表範例（Forbidden）

- getter / setter（除非 section 本身就是 DTO / migration）
- 空殼 class / boilerplate
- 純 import-only（只有 import 行，無邏輯）
- README / 無語意說明文件
- test / mock files（除非 section 本身就是 test coverage）

#### 允許的 diff cell 內容（patch hunk 原文）

```
@@ -N,N +N,N @@
 <context 行，來自 patch>
+ <新增行，來自 patch>
- <刪除行，來自 patch>
 <context 行，來自 patch>
```

**建議格式（table 外說明 + table 內 hunk 原文）：**

```
範例來源：FXExchangeService.java — orders() 建立交易暫存並呼叫 FX orders

以下為 patch 原文節錄：

@@ -120,6 +120,18 @@
 context line
- old logic
+ new logic
+ new logic
```

**若為 fallback 範例（Priority 3 / Priority 4）：**

```
範例來源：FXOrdersRequest.java — 欄位新增（Controller / Service method 不適用）
以下為 patch 原文節錄：
```

**注意：** Priority 層號（Priority 1 / 2 / 3 / 4）以及 WEAK 標記**不得出現在 TSD 主文**中。這些判斷結果僅限 QA 報告（Gate J）記錄。

#### 嚴格禁止在 diff cell 內出現的 git metadata

以下內容**絕對禁止**出現在 3.x.3 diff table cell 內部：

```
diff --git a/... b/...
new file mode 100644
deleted file mode 100644
index xxxx..xxxx
similarity index ...%
rename from ...
rename to ...
```

這些是 git metadata，不是程式碼異動內容，不得出現在 diff 表格中。

#### 允許的 diff header（在 table 外段落說明用）

若需標示範例來源，請在 **table 外**（table 前段落）寫明：

```
範例來源：<FileName.java> — <method 或說明>
以下為 patch 原文節錄：
```

**嚴格禁止在 diff cell 內：**
```
...                        ← 截斷省略號
（不完整的 pseudo code）    ← 非 patch 原文
（重建或改寫的 diff）       ← 非 patch 原文
diff --git a/<path> b/<path>   ← git metadata，禁止
new file mode / deleted file mode  ← git metadata，禁止
index xxxx..xxxx               ← git metadata，禁止
```

**若 diff 為部分節錄（partial excerpt）**，必須在 table 外（table 前一段落）加入說明：

```
以下為 patch 原文節錄。
```

此說明段落必須在 table **外部**，不得放在 diff cell 內。

---

## Appendix Format Rules (Final — 2026-05-05)

### 附錄結構

每個 TSD 項目對應一個附錄子項。結構如下：

```
[附錄標題段落]  pStyle='v24heading1'
  附錄完整異動程式清單

  [子項標題]  pStyle='v24heading2'
    1. 第 N 項：<項目名稱>

    [檔案清單 bullet]  pStyle='v24ListBullet'
      <file path 1>
      <file path 2>
      ...

    [回鏈段落]  pStyle='none'（或任意 body style）
      回到主文第 N 項
      （此段落為 Word 超連結，指向 MAIN_SECTION_N）
```

### 附錄必填規則

1. 附錄的異動程式內容（檔案路徑、異動類型、per-file summary 是否必要）由
   `core/CHANGE_PROGRAM_CONTRACT.md` 定義；本檔只負責 bullet 的 Word 樣式呈現。
2. 每個子項末尾必須有「回到主文第 N 項」超連結段落，指向 `MAIN_SECTION_N`。
3. `proto_bullet` 必須從 `appendix_title_idx + 2` 取得（style = `v24ListBullet`）；不可從 `+1`（那是 `v24heading2`）。

---

## Hyperlink / Bookmark Rules

| 書籤名稱 | 建立位置 |
|---|---|
| `MAIN_SECTION_1` … `MAIN_SECTION_N` | 各 3.x 項目標題段落 |
| `APPENDIX_SECTION_1` … `APPENDIX_SECTION_N` | 各附錄子項標題段落 |

| 超連結位置 | 目標書籤 |
|---|---|
| 3.x.2「查看附錄完整清單（n 個檔案）」 | `APPENDIX_SECTION_N` |
| 附錄「回到主文第 N 項」 | `MAIN_SECTION_N` |

超連結在 OOXML 中使用 `<w:hyperlink w:anchor="BOOKMARK_NAME">` 結構，**不可**改成純文字。
