# TSD Patch Fact-Check Rules

## Purpose

This document defines the patch fact-check process. The patch file is the ONLY authoritative source for all TSD diff content, file names, method names, identifiers, and code structure. This process must be run before delivery.

Updated: 2026-05-11 (新增 PFC-11 git metadata 禁止規則、PFC-12 代表範例 logic-bearing 要求)

---

## Core Principle

```
The patch is the source of truth.
If it is not in the patch, it MUST NOT appear in the TSD diff tables.
Git metadata lines (diff --git, new file mode, index, etc.) MUST NOT appear in diff cell content.
Representative examples MUST be logic-bearing code, not metadata or boilerplate.
```

---

## 12 Verification Rules

### PFC-1 — File Exists in Patch

Every file path mentioned in TSD (in 3.x.2 programme list, appendix, or diff examples) MUST be found in the patch's `diff --git a/<path>` headers.

**FAIL if** any file path in TSD is not found in the patch index.

---

### PFC-2 — Diff Content Verbatim

All lines in a 3.x.3 diff table cell MUST appear verbatim in the corresponding patch block.

Verification method (match sample):
1. Extract up to 5 `+` or `-` lines from the diff cell (skip `+++`, `---`).
2. For each line, strip the leading `+` or `-` and search in the patch block for that file.
3. At least 3 of 5 sampled lines MUST match.

**FAIL if** fewer than 3 of 5 sampled lines are found verbatim in the patch block.

---

### PFC-3 — Method Name in Patch

If the section description or source mapping declares a method name, that method name MUST appear in the patch's `+` lines for that file.

**WARN if** the declared method is not found in the patch block.

---

### PFC-4 — No Ellipsis Inside Diff Table

No diff table cell may contain a paragraph whose text is exactly `"..."` or any other truncation marker.

**FAIL if** `"..."` is found inside a diff table cell.

---

### PFC-5 — No Pseudo / Reconstructed Diff

No diff table cell may contain content that is:
- Reconstructed from memory / pattern rather than from the actual patch
- Paraphrased (changed wording, altered logic)
- Generated from similar but different files

**FAIL if** fewer than 3 of 5 sampled diff lines match the actual patch block.

---

### PFC-6 — Hunk Header Authenticity

All `@@ -N,N +N,N @@` hunk markers in diff cells MUST correspond to actual hunk markers in the patch file for that file and offset.

Do NOT invent hunk markers.

---

### PFC-7 — Class / Interface Names in Patch

If a class or interface name appears in the section description, it MUST be findable in either:
- The patch's `+` lines for that file, OR
- The actual file path (class name derivable from filename)

Do NOT invent or guess class names from context.

---

### PFC-8 — No Cross-File Content Leakage

Diff content placed under file X MUST come from file X's patch block. It MUST NOT borrow content from file Y's patch block.

---

### PFC-9 — @@ Count Matches Patch

The number of `@@` hunk headers in a diff cell MUST match the number of hunks extracted from the patch block for that file at the declared offset.

If fewer hunks are shown (partial excerpt), PFC-4 and PFC-5 apply, and the note paragraph rule (STYLE_RULES Rule S6) must be followed.

---

### PFC-10 — Coverage Equation

All patch-changed files must be accounted for in the coverage equation:

```
Total patch changed files
  = TSD appendix IN_SCOPE files
  + SUPPORTING_DOCUMENT / OUT_OF_SCOPE files (QA report)
  + UNKNOWN files (QA report)

missing = 0
```

**FAIL if** `missing > 0`.

---

### PFC-11 — No Git Metadata in Diff Cell（新增）

Diff table cell 內容絕對不可包含以下 git metadata 行：

```
diff --git a/... b/...
new file mode 100644
deleted file mode 100644
index xxxx..xxxx
similarity index ...%
rename from ...
rename to ...
```

**FAIL if** any of the above patterns is found inside a 3.x.3 diff table cell.

**Allowed inside diff cell:**
- `@@ -N,N +N,N @@` hunk header（帶 blue + bold 顏色）
- `+ <新增行>`（帶 green 顏色）
- `- <刪除行>`（帶 red 顏色）
- context 行（一般文字顏色）

**Allowed outside diff cell (table 前段落):**
- 來源說明段落：`範例來源：FileName.java — method 說明`
- 節錄說明段落：`以下為 patch 原文節錄。`

**FAIL trigger:** `diff --git` / `new file mode` / `deleted file mode` / `index` / `similarity index` / `rename from` / `rename to` 出現在任何 diff cell 內部。

**Enforcement:** 本規則語意以此段文字為準；`tools/rule21_linter.py`（violation_id `P1_GIT_METADATA_IN_CELL`，經 `core/WORKFLOW.md` 呼叫）提供決定性機械化檢查，僅實作本段落所列的 git metadata 偵測，不取代本段文字的語意權威。

---

### PFC-12 — Representative Example Must Be Logic-Bearing（新增）

每個 section 的代表範例（3.x.3）必須是「有邏輯意義的程式碼片段」，不得是 git metadata 或 boilerplate。

**PASS 條件（至少符合一項）：**
- 包含 Service method 中的業務流程邏輯（if / loop / method call）
- 包含 Controller API method 的 request / response / session 處理
- 包含外部服務 client 的 request 組裝 / response handling
- 包含 DAO / Repository / Service 的資料存取邏輯
- 包含 Config / Bean 中實際影響 runtime 的設定
- 包含 Constants / error code 中與業務有直接關係的新增項目
- 包含技術遷移中能代表遷移模式的 import / annotation / API replacement

**FAIL 條件（任一項即 FAIL）：**
- 選取的 diff hunk 只有 getter / setter
- 選取的 diff hunk 只有 package 宣告
- 選取的 diff hunk 只有 import 行（無業務邏輯）
- 選取的 diff hunk 只有空殼 class（無 method body）
- 選取的 diff hunk 來自 README 或 `.md` 文件
- 選取的 diff hunk 完全由 git metadata 構成

**FAIL if** sampled `+` lines contain only getters/setters, package declarations, imports, boilerplate, or metadata with no logic.

---

## QA Table Templates

Use the following table templates in the QA report. All cells must be filled.

---

### QA Table 1 — Patch Inventory Summary

| # | File path | Lines added | Lines deleted | Scope | Code Change Type |
|---|---|---|---|---|---|
| 1 | `path/to/File.java` | 42 | 18 | IN_SCOPE | business-logic |
| 2 | `path/to/README.md` | 5 | 0 | SUPPORTING_DOCUMENT | supporting-document |
| … | … | … | … | … | … |
| N | Total | ΣΔ+ | ΣΔ- | — | — |

---

### QA Table 2 — File Coverage Verification

| Category | Count | File list |
|---|---|---|
| TSD appendix (IN_SCOPE) | 90 | [link or inline list] |
| SUPPORTING_DOCUMENT / OUT_OF_SCOPE | 1 | `ECERTRestClient_README.md` |
| UNKNOWN | 0 | — |
| **Total patch files** | **91** | — |
| Missing (must = 0) | **0** | — |

---

### QA Table 3 — Section Description Fact Check

| Section | Claimed files | In patch? | Claimed type | Evidence in patch? | Verdict |
|---|---|---|---|---|---|
| 3.1 | File A, File B | ✓ | 業務功能 | ✓ `+public void transfer(...)` | PASS |
| 3.2 | File C | ✓ | 技術遷移 | ✓ `+import jakarta.*` | PASS |
| … | … | … | … | … | … |

---

### QA Table 4 — Diff Example Exact Match Check

| Section | File used | @@ header | Sample line 1 | In patch? | Sample line 2 | In patch? | Sample line 3 | In patch? | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 3.1 | `PaymentService.java` | `@@ -45,12 +45,18 @@` | `+return repo.save(tx)` | ✓ | `+if (amount > 500000)` | ✓ | `+throw new LimitException` | ✓ | PASS |
| … | … | … | … | … | … | … | … | … | … |

---

### QA Table 5 — Identifier Fact Check

| Section | Identifier type | Declared value | Found in patch? | Verdict |
|---|---|---|---|---|
| 3.1 | Method name | `processTransfer` | ✓ line 47 | PASS |
| 3.2 | Class name | `ECERTRestClient` | ✓ filename | PASS |
| 3.3 | File path | `src/main/java/...` | ✓ diff --git header | PASS |
| … | … | … | … | … |

---

### QA Table 6 — Representative Example Priority Check

Updated: 2026-05-12 (added for Rule 27 / Gate J)

Judgment criteria:

| Situation | Verdict |
|---|---|
| Controller method exists in section; selected example is from Controller | PASS |
| Controller method exists but selected Service / DTO / field instead | **FAIL** |
| No Controller method; Service method exists; selected Service | PASS |
| No Controller; Service exists but selected DTO / Object field instead | **FAIL** |
| No Controller / Service; Object method exists; selected Object method | PASS |
| No Controller / Service; Object method exists but selected field-only | **WEAK** (acceptable if no method candidate) |
| No Controller / Service / Object method; selected field / constant snippet | PASS (Priority 4 fallback) |
| Selected git metadata / ellipsis / pseudo diff | **FAIL** |
| Priority 5 fallback used but marked WEAK in QA report | PASS (with note) |
| Priority 5 fallback used but NOT marked | **FAIL** |

| Section | Controller Candidate Exists | Service Candidate Exists | Object Candidate Exists | Selected Example File | Selected Candidate Type | Priority Correct | Status |
|---|---|---|---|---|---|---|---|
| 3.1 | ✓ / ✗ | ✓ / ✗ | ✓ / ✗ | `XxxController.java` | Controller method | ✓ | PASS |
| 3.2 | ✓ / ✗ | ✓ / ✗ | ✓ / ✗ | `XxxService.java` | Service method | ✓ | PASS |
| … | … | … | … | … | … | … | … |

Fill one row per section. All rows must be PASS or WEAK (with note) for delivery to proceed.

---

## Canonical Example — SUPPORTING_DOCUMENT Exclusion (PRJ-A)

```
File:    mobileapp/src/main/java/com/example/mobileapp/ecert/ECERTRestClient_README.md
Scope:   SUPPORTING_DOCUMENT / OUT_OF_SCOPE
Reason:  Documentation file; no programme change; no Java class or method.
         Excluded from TSD appendix programme change list.
         Included in QA report — Table 2, SUPPORTING_DOCUMENT row.

Coverage equation:
  91 patch changed files
    = 90 IN_SCOPE (appendix)
    + 1 SUPPORTING_DOCUMENT (QA report)
    + 0 UNKNOWN
  missing = 0  → PASS
```

---

## Anti-Patterns

| Anti-Pattern | Rule | Verdict |
|---|---|---|
| `"..."` inside diff table cell | PFC-4 | FAIL |
| Reconstructed diff from memory | PFC-2, PFC-5 | FAIL |
| File in TSD but not in patch | PFC-1 | FAIL |
| Method name invented without checking patch | PFC-3 | WARN |
| `@@ -1,N +1,N @@` header fabricated | PFC-6 | FAIL |
| Coverage equation does not balance | PFC-10 | FAIL |
| Diff line from wrong file | PFC-8 | FAIL |

---

## Cross-Reference

- `SKILL.md` Rule 7 (Diff Authenticity), Rule 21 (No Ellipsis), Rule 23 (SUPPORTING_DOCUMENT); Rule 28 (Template Mode Declaration Rule)
- `QA_CHECKLIST.md` Gate F — Patch Fact gate; Gate K — Template Mode Gate
- `STYLE_RULES.md` Rule S6 (Note paragraph outside table)
- `SECTION_GROUPING_RULES.md` Step 2 (Scope classification)

---

## Three-Template Fact-Check Differences

Updated: 2026-05-14 (三模板 TSD 架構 Step 1)

Patch fact-check requirements differ by template mode. Core rules PFC-1 through PFC-12 apply to ALL modes.
The following additions apply per mode:

### Template A — Large Migration Template

| Verification Item | Requirement |
|-------------------|-------------|
| Common migration pattern | PFC-3: Verify that the declared migration type (e.g., `javax→jakarta`) is evidenced by `+` lines in the patch |
| Representative hunk | PFC-2: Select one representative hunk showing the common migration transformation; verify verbatim match |
| Per-file summary | NOT required; absence is CORRECT |
| Appendix file list | All IN_SCOPE paths must appear; no per-file summaries required |
| Coverage equation | Must hold: `missing = 0` |

**Template A Specific Anti-Pattern:**
```
FAIL: Representative example invented without checking which files actually share the migration pattern
PASS: Example hunk extracted verbatim from one representative file's patch diff
```

### Template B — Medium Feature Template

| Verification Item | Requirement |
|-------------------|-------------|
| Section summary | PFC-3: Verify section 3.x.1 description matches actual patch content (files, method names) |
| Representative example | PFC-2, PFC-5: Verify example is verbatim from patch; Controller/Service/Object priority applied |
| Per-file summary | NOT required by default; if absent → PASS; if present → must be labelled B+C Hybrid |
| Appendix file list | All IN_SCOPE paths must appear; no per-file summaries by default |
| Coverage equation | Must hold: `missing = 0` |

**Template B Specific Anti-Pattern:**
```
FAIL: Adding per-file summaries as Template B default (without labelling B+C Hybrid)
PASS: 3.x.2 matches `placement = appendix_only` (no per-file summary in main body)
```

### Template C — Small Per-File Template

| Verification Item | Requirement |
|-------------------|-------------|
| Per-file summary (3.x.2) | Required by `placement = inline_per_file`; see `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 |
| Per-file summary (appendix) | Every IN_SCOPE appendix entry must have a 15–30 char description |
| Summary fact check | Each per-file summary must be patch-supported (PFC-3 applied per file) |
| Per-file / per-group code block | PFC-2, PFC-5: Verify code block is verbatim from patch for that file |
| Coverage equation | Must hold: `missing = 0` |
| If applied to medium/large project | QA report MUST contain `PASS_FOR_DEMONSTRATION` note |

**Template C Specific Fact-Check (per-file summary):**
```
For each appendix entry with a 15–30 char description:
  1. Identify the source file path
  2. Locate that file in patch `diff --git a/... b/...`
  3. Verify the description reflects actual `+` or `-` lines in that file's hunk
  4. If description says "新增轉帳API" → verify `+` lines contain API method / endpoint
  5. FAIL if summary is fabricated without patch evidence
```

**v19 Specific Application:**
`PRJ-A_20260505_v19_SHORT_CHANGE_SUMMARY.docx` (Template C prototype / B+C Hybrid):
- 90 appendix entries × 15–30 char summaries = 90 fact-check items
- Each summary must be patch-supported
- Any summary without a matching `+` line or `diff --git` entry → FAIL (PFC-3)
