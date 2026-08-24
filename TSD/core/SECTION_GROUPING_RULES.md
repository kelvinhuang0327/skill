# TSD Section Grouping Rules

## Purpose

This document defines the complete decision flow for grouping patch-changed files into TSD sections. It supplements `CLASSIFICATION_RULES.md` and `SKILL.md Rule 17`.

Updated: 2026-05-11 (Code Change Pattern priority revision — supersedes 2026-05-05)
Updated: 2026-05-12 (Dual-mode grouping system added — Mode A Business Function, Mode B Code Change Pattern)

---

## Dual-Mode Grouping System

This skillpack supports **two valid grouping modes**. Both are correct; the choice depends on project size and reviewer needs.

| Mode | Name | When to Use | Details |  
|------|------|-------------|---------|  
| **Mode A** | Business Function Grouping | Large requirements, end-to-end flows, cross-layer changes | Files grouped by shared business purpose |  
| **Mode B** | Code Change Pattern Grouping | Small projects, targeted fixes, code-type precision | Files grouped by code change pattern type |  

For Mode A rules, decision tree, and examples → see `GROUPING_MODE_RULES.md`.

The rest of **this document describes Mode B** (Code Change Pattern Grouping) — the original and default flow.

**Important:** Mode A is NOT over-merge. When a strong business function groups controller + service + dto + config together, Mode A is correct and should NOT be flagged as a violation. See `GROUPING_MODE_RULES.md` for the PRJ-A FX example.

---

## Mode B Core Principle

```
TSD section = Same Code Change Pattern + Compatible Shared Purpose + File-Level Completeness.

分類優先順序（Mode B only）：
  1. Code Change Pattern / 程式碼異動模式  ← 第一優先
  2. Technical Change Type / 技術改法      ← 與 #1 同層輔助
  3. Shared Purpose / 共同用途             ← 第二層輔助，不是第一順位
  4. File-Level Completeness / 檔案完整性  ← 最終補完驗證

A section represents a code change pattern, not a purpose category.
A file MUST still appear individually in the appendix even when grouped.
```

---

## Code Change Patterns

每個 patch 中的檔案 MUST 被指定 EXACTLY ONE 主要 Code Change Pattern。

判斷 Code Change Pattern 時，必須依據 patch hunk 內容，不可只看檔名或 package 名稱。

| # | Code Change Pattern | Description | Examples |
|---|---|---|---|
| 1 | `controller-api-new` | 新增 Controller API endpoint | `@PostMapping`, `@GetMapping` 新 handler method |
| 2 | `service-logic-new` | 新增 Service 業務邏輯 method | `@Service` 新增 method，含業務流程判斷 |
| 3 | `service-logic-modify` | 修改 Service 流程判斷 | 既有 Service method 加入條件分支、修改呼叫 |
| 4 | `dto-bo-field-new` | 新增 / 調整 DTO、BO、Request、Response 欄位 | 新增 field，調整 annotation |
| 5 | `data-layer-new` | 新增 Entity / PK / Repository / Service 資料層 | `@Entity`, `@Repository`, JPA 新增 |
| 6 | `external-client-new` | 新增 REST Client / SOAP Client / WS Proxy | CXF stub, RestTemplate client, SOAP connector |
| 7 | `constants-errorcode-adjust` | 調整 constants / error code / session key | 常數 class 新增項目，error code enum 調整 |
| 8 | `config-properties-adjust` | 調整 properties / config / bean | `*.properties`, `*.yml`, Spring XML bean 調整 |
| 9 | `technical-migration` | 技術遷移 | `javax→jakarta`, `Swagger→OpenAPI`, `Axis→CXF` |
| 10 | `deletion-removal` | 刪除舊 class / 移除舊功能 | `deleted file mode`, 大量 `-` 行，整 class 移除 |
| 11 | `utility-helper-new` | 新增 utility / helper | Utils class, helper method, constants enum |
| 12 | `test-mock-new` | 新增 test / mock / supporting files | JUnit, Mockito, test fixtures |
| 13 | `view-js-jsp` | 前端畫面 / JS / JSP 更動 | `.js`, `.jsp`, `.css`, `.html` 更動，前端層視圖更改 |
| 14 | `dependency-build` | 依賴與構建設定更動 | `pom.xml`, `build.gradle`, `Dockerfile`, Maven / Gradle 版本升級 |
| 15 | `supporting-document` | supporting document / README | `.md`, 設計文件，無程式異動 |
| 16 | `unknown` | 無法從 patch 判斷 | 標記待人工審查 |

> **重要**：`business-logic` 不再作為獨立 pattern。改用 `service-logic-new`、`service-logic-modify`、`controller-api-new` 等更精確的 pattern 替代。
> `dao-repository-mapper` 可作為 `data-layer-new` 的子分類（其需求從更改了 DAO / Mapper 層的檔案）。
> `rest-client`、`soap-client`、`ws-proxy` 均歸屬 `external-client-new`，可在 rationale 說明具體子類型。
> 原有分類值（`business-logic`, `controller`, `service`, `dto-model`, `sql-mapper`, `data-layer`, `ws-client` 等）仍可作為 Code Change Type 欄位輔助標記，但 **分群主鍵改為 Code Change Pattern**。

---

## File Scope Values

Each file must be assigned a scope:

| Scope | Meaning | TSD Handling |
|---|---|---|
| `IN_SCOPE` | Belongs to current project requirement | Include in TSD appendix; 3.x.2 inclusion depends on placement (`CHANGE_PROGRAM_CONTRACT.md`) |
| `SUPPORTING_SCOPE` | Shared utility directly referenced | Include in appendix only (not counted in the change-program main count) |
| `SUPPORTING_DOCUMENT` | README / documentation file | Exclude from TSD; include in QA report |
| `OUT_OF_SCOPE` | Different project / feature | Exclude from TSD; include in QA report |
| `UNKNOWN` | Cannot determine | Flag; include in QA report |

---

## 10-Step Grouping Decision Flow（最新版 2026-05-11）

### Step 1 — Patch Inventory / 建立異動檔案清單

Parse the patch file and build a flat list of all changed files.

For each file record:
- Full file path
- Number of lines added / deleted
- `diff --git` block start line in patch

Output: **Patch Inventory Table**

---

### Step 2 — Code Change Pattern Assignment / 為每個檔案判定 Code Change Pattern

For every file in the inventory:

1. 先讀 patch hunk 內容（必要）
2. 再看檔名 / package（輔助）
3. 最後才看 commit message（最弱，不得單獨作為依據）

Key distinctions:
- 僅有 `import javax.*` → `import jakarta.*` 的改動 → `technical-migration`
- 新增 `@PostMapping` handler method → `controller-api-new`
- 在既有 Service method 中加入 `if (amount > 500000)` 判斷 → `service-logic-modify`
- 新增 DTO field 及 annotation → `dto-bo-field-new`
- `.md` README 檔 → `supporting-document`

**Classification evidence priority:**
1. Patch diff content（最強）
2. File name and path
3. Commit message（僅輔助）
4. Business context（最弱，不得單獨使用）

If patch content and commit message conflict → **patch wins**.
If uncertain → assign `unknown` and flag for human review.

---

> **Note:** Steps 3–8 below apply to **Mode B (Code Change Pattern Grouping)** only.
> If Mode A (Business Function Grouping) is selected, skip to `GROUPING_MODE_RULES.md` for the Mode A flow.

### Step 3 — 先依 Code Change Pattern 分群（Mode B）

以 Code Change Pattern 為第一分群鍵，產生 candidate section 清單。

Candidate section 示例：
- `controller-api-new` → Section 候選：Controller API 新增
- `service-logic-new` → Section 候選：Service 業務流程新增
- `dto-bo-field-new` → Section 候選：DTO / BO / Response 欄位新增
- `data-layer-new` → Section 候選：Entity / Repository / Service 資料層新增
- `external-client-new` → Section 候選：外部服務 Client 新增
- `constants-errorcode-adjust` → Section 候選：Constants / Error Code 調整
- `config-properties-adjust` → Section 候選：Config / Properties 調整
- `view-js-jsp` → Section 候選：前端畫面 / JS / JSP 更動
- `dependency-build` → Section 候選：依賴 / 構建設定更動

**禁止（Mode B only）**：直接以用途（如「PRJ-A 功能」）作為唯一分群鍵，把 Controller、Service、DTO、Entity、Config 全部硬塞同一 section。
**注：**在 Mode A 下，若檔案共同支援同一明確業務流程，則此做法屬於合法。

---

### Step 4 — 再用 Shared Purpose 做二次拆分或合併（Mode B）

在相同 Code Change Pattern 的群組內，以 Shared Purpose 做調整：

**可合併（MERGE）當全部條件成立時：**

1. 相同 Code Change Pattern（或高度相似的 pattern family）
2. 相同或相容的 Shared Purpose（服務同一 API、同一流程、同一外部系統）
3. 不同 pattern 的 supporting files 放入主 section 的 supporting list，rationale 必須說明

**同 pattern 可合併範例：**
- 多個 `dto-bo-field-new` 檔案，均為同一 API 的 Request/Response/BO → 合併為一個 section
- 多個 `data-layer-new` 檔案，均為同一資料表的 Entity/PK/Repository → 合併為一個 section
- 全部 `technical-migration` (`javax→jakarta`) 於同一 module → 合併為一個 section
- 多個 `constants-errorcode-adjust`，均支援同一業務功能 → 合併為一個 section

**不可合併（DO NOT MERGE）當任一條件成立時：**

1. 只因用途相同就把不同 Code Change Pattern 全部合併
2. 只因同一 package 就合併
3. 只因都屬於同一個功能就合併（如「PRJ-A 相關」）
4. Controller API、Service 邏輯、DTO 欄位、Entity 資料層、Config 設定，若 Code Change Pattern 不同，應拆開或至少標明主次關係
5. 不同外部系統 client 不可硬合併
6. 不同資料表的 Entity / Repository 若用途不同，不可硬合併
7. `technical-migration` 不可與 `service-logic-new` / `controller-api-new` 混入同一 section
8. supporting utility 不可被描述成主要業務功能 section

**Shared Purpose 輔助合併 / 拆分判斷：**

- 相同 Code Change Pattern 且用途相容 → 合併
- 相同 Code Change Pattern 但用途完全不同 → 拆分
- 用途相同但 Code Change Pattern 差異很大 → 拆分成不同 section，或用主次關係標明

**Over-merge anti-pattern:**
```
BAD:  Section "PRJ-A 功能調整" — 混入 controller-api-new + service-logic-new + dto-bo-field-new + config-properties-adjust
GOOD: Section "PRJ-A 轉帳確認 Controller API 新增" — controller-api-new, 1 pattern
      Section "PRJ-A 轉帳確認 Service 業務邏輯新增" — service-logic-new, 1 pattern
      Section "PRJ-A 相關 DTO / BO 欄位新增" — dto-bo-field-new, 1 pattern
```

---

### Step 5 — 判定 Scope / 為每支檔案指定一個 Scope 分類

For every file in the inventory, assign EXACTLY ONE scope value:

| Scope | Condition | TSD Handling |
|---|---|---|
| `IN_SCOPE` | 清楚屬於目前需求範圍 | 列入 TSD 附錄 |
| `SUPPORTING_SCOPE` | 共用 utility / config，直接被目前需求引用 | 列入附錄（不計入異動程式清單主計數） |
| `SUPPORTING_DOCUMENT` | README / 文件檔，非程式異動主體 | 不列入 TSD；列入 QA report |
| `OUT_OF_SCOPE` | 屬於其他項目 / 功能 | 不列入 TSD；列入 QA report |
| `UNKNOWN` | 無法從 patch 判斷 | Flag for human review；列入 QA report |

**每支檔案必須字屌其中之一。不可消失。**

---

### Step 6 — 建立 Section Candidate 清單

對所有 IN_SCOPE 和 SUPPORTING_SCOPE 檔案，依據 Step 3 产生的 Code Change Pattern 群組，建立 section candidate 清單。

每個 Section Candidate 必須記錄：
- Section 候選編號（暫定，待 Step 7 後確定）
- Primary Code Change Pattern
- 候選包含檔案清單（完整檔名）
- Shared Purpose 兩句討摘

---

### Step 7 — Same-Pattern Merge / 合併相同模式的 section

在相同 Code Change Pattern 的 section candidate 之間，判斷是否可合併為一個 section。

**可合併（全部條件成立）：**
1. 相同 Code Change Pattern（或高度相似的 pattern family）
2. 相同或相容的 Shared Purpose（服務同一 API、同一流程、同一外部系統）
3. 合併後 3.x.1 說明仍能明確且非模糊

**可合併範例：**
- 多個 `dto-bo-field-new` 檔案，均為同一 API 的 Request/Response/BO → 合併
- 多個 `data-layer-new` 檔案，均為同一資料表的 Entity/PK/Repository → 合併
- 全部 `technical-migration` (`javax→jakarta`) 於同一 module → 合併
- 多個 `constants-errorcode-adjust`，均支援同一業務功能 → 合併

---

### Step 8 — Do Not Over-Merge / 禁止過度合併（Mode B）

以下規則僅適用於 **Mode B (Code Change Pattern Grouping)**。
Mode A 允許不同 code pattern 的檔案共存於同一 section，條件是它們共同支援一個明確的業務流程。

下列任一條件成立即禁止合併（Mode B）：

1. 只因用途相同就把不同 Code Change Pattern 全部合併
2. 只因同一 package 就合併
3. 只因都屬於同一個功能就合併（如「PRJ-A 相關」）
4. Controller API、Service 邏輯、DTO 欄位、Entity 資料層、Config 設定，若 Code Change Pattern 不同，不应硬塞同一 section
5. 不同外部系統 client 不可硬合併
6. 不同資料表的 Entity / Repository 若用途不同，不可硬合併
7. `technical-migration` 不可與 `service-logic-new` / `controller-api-new` 混入同一 section
8. supporting utility 不可被描述成主要業務功能 section
9. 合併後若 3.x.1 說明變模糊，必須拆分
10. 合併後若代表範例無法代表共同 code pattern，必須拆分

**Over-merge anti-pattern (Mode B only):**
```
BAD (Mode B):  Section "PRJ-A 功能調整" — 混入 controller-api-new + service-logic-new + dto-bo-field-new
GOOD (Mode B): Section "PRJ-A 轉帳確認 Controller API 新增" — controller-api-new, 1 pattern
               Section "PRJ-A 轉帳確認 Service 業務邏輯新增" — service-logic-new, 1 pattern
               Section "PRJ-A 相關 DTO / BO 欄位新增" — dto-bo-field-new, 1 pattern

OK (Mode A):   Section "FX 換匯主流程" — controller-api + service-logic + dto-bo + service-flow-adjustment
               → Valid under Mode A because all serve FX 換匯 end-to-end flow.
               See GROUPING_MODE_RULES.md for full Mode A example.
```

---

### Step 9 — Representative Example Selection / 為每個 section 指定代表範例

依据 Section Rationale 中的「代表範例選擇規則」，為每個 section 選取 1–2 個 diff hunk snippet。

**選取標準：**
必須是「有邏輯意義的程式碼片段」，不得是 git metadata 或 boilerplate。

詳見本檔 「**Representative Example Selection / 代表範例選擇規則**」 區塊和 `OUTPUT_SPEC.md` 3.x.3 節。

---

### Step 10 — File-Level Completeness Reconciliation / 確認所有檔案已涵蓋

After grouping, verify every patch-changed file appears in ONE of:
- TSD appendix (IN_SCOPE)
- SUPPORTING_DOCUMENT (QA report)
- OUT_OF_SCOPE (QA report)
- UNKNOWN (QA report)

**Coverage equation MUST hold:**
```
Total patch changed files
  = sum(TSD appendix IN_SCOPE files)
  + sum(SUPPORTING_SCOPE appendix files)
  + sum(SUPPORTING_DOCUMENT files in QA report)
  + sum(OUT_OF_SCOPE files in QA report)
  + sum(UNKNOWN files flagged)

missing = 0
```

---

## Representative Example Selection / 代表範例選擇規則

### 優先選擇（Priority Order）

1. Service method 中的業務流程邏輯（含條件判斷、呼叫外部 / 資料層）
2. Controller API method 的 request / response / session 處理
3. 外部服務 client 的 request 組裝 / response handling
4. DAO / Repository / Service 的資料存取邏輯
5. Config / Bean 中實際影響 runtime 的設定
6. Constants / error code 中與業務流程有直接關係的新增內容
7. 技術遷移中能代表遷移模式的 import / annotation / API replacement

### 禁止選擇（Forbidden）

1. 純 git metadata（`diff --git`, `new file mode`, `deleted file mode`, `index ...`, `similarity index ...`, `rename from/to`）
2. 只有 package 宣告
3. 純 import-only（只有 import 行，無邏輯）
4. getter / setter（除非 section 本身就是 DTO / migration）
5. 空殼 class / boilerplate
6. README / 無語意的說明文件
7. test / mock files（除非 section 本身就是 test coverage）
8. 空或近空的異動

Each example MUST have a diff hunk traceable to the actual patch.

---

## Section Rationale Report Requirements / 分群理由必填欄位

### Grouping Mode Declaration (Required at top of rationale report)

Every rationale report must begin with:

```
Grouping Mode: [Business Function Grouping (Mode A) | Code Change Pattern Grouping (Mode B) | Hybrid]
Why this mode: [2–3 sentence justification]
Why not the other mode: [1–2 sentence explanation]
```

For full rationale template, see `GROUPING_MODE_RULES.md` — Rationale Report Template.

### Per-Section Rationale Items

每個 section 的 rationale 必須說明以下全部項目（用於 QA 報告，不得進入 TSD 主文）：

**Mode B (Code Change Pattern Grouping) — required items:**

| # | 必填說明項目 |
|---|---|
| 1 | **Code Change Pattern 是什麼**（從 pattern 定義表選取） |
| 2 | 為什麼這些檔案屬於相同 Code Change Pattern（引用 patch hunk 證據） |
| 3 | **Shared Purpose 是什麼**（服務哪個 API / 流程 / 外部系統） |
| 4 | Shared Purpose 是否只是輔助合併理由（是 / 否，並說明） |
| 5 | 為什麼沒有只按用途合併（說明 Code Change Pattern 差異） |
| 6 | 為什麼沒有把不同 Code Change Pattern 硬塞同一組 |
| 7 | 代表範例為什麼是邏輯片段（說明該片段含業務流程 / 技術決策） |
| 8 | 代表範例為什麼不是 git metadata / boilerplate |

**Mode A (Business Function Grouping) — required items:**

| # | 必填說明項目 |
|---|---|
| 1 | **Business Function / 業務功能名稱**（此 section 代表哪個業務功能） |
| 2 | 為什麼這些檔案共同支援此業務功能（引用 patch hunk 證據） |
| 3 | **所有包含的 Code Change Patterns**（完整清單，主要 vs. 配套） |
| 4 | 哪些是主要 pattern，哪些是 supporting pattern |
| 5 | SUPPORTING_SCOPE 檔案清單及各自的支援角色 |
| 6 | 為什麼不按 code pattern 拆分（說明拆分後 reviewer 的困難） |
| 7 | 代表範例為什麼能代表整體業務流程的核心邏輯 |
| 8 | 代表範例為什麼不是 git metadata / boilerplate |

**Rationale 格式範例：**

```
Section 3.2 — Rationale

Code Change Pattern: service-logic-new
Pattern Evidence: FXExchangeService.java patch 中新增 orders() method，
                  含 buildOrderRequest() 呼叫與 fxClient.post() 整合邏輯。
                  ECERTService.java patch 中新增 verifySignature() method，
                  含業務流程判斷 if (result.getStatus() != SUCCESS)。
Shared Purpose: 均為 FX 外匯交易流程的 Service 層業務邏輯，支援同一 API 端點。
Shared Purpose 角色: 輔助合併理由（因 Code Change Pattern 已相同，Shared Purpose 確認可合併）
未只按用途合併: Controller API 新增已獨立為 Section 3.1；
               DTO 欄位調整已獨立為 Section 3.3。
代表範例: FXExchangeService.java — orders() 建立交易暫存並呼叫 FX orders
         patch hunk 含 buildOrderRequest() 組裝邏輯，屬業務流程判斷。
非 metadata 原因: 選取 @@ -120,6 +120,18 @@ hunk，顯示新增業務邏輯行，
                  不含 diff --git header 或 new file mode。
```

**Missing = 0** is required for delivery.

If a file is grouped into a section but does NOT appear in the appendix → **completeness failure**.

Grouping DOES NOT grant permission to omit files from the appendix.

---

## Concrete Classification Example (PRJ-A Project)

### File: `mobileapp/.../ECERTRestClient_README.md`

```
Step 2: Scope = SUPPORTING_DOCUMENT / OUT_OF_SCOPE
        (README file, not a programme change)
Step 3: Code Change Type = supporting-document
Step 4: No shared purpose with IN_SCOPE files (documentation only)
Step 5: Not a section candidate
Step 6: N/A
Step 7: Excluded from TSD
Step 8: N/A (no representative example)
Step 9: Appears in QA report SUPPORTING_DOCUMENT section
        Coverage: 91 patch files = 90 appendix + 1 SUPPORTING_DOCUMENT
```

---

## Forbidden Section Titles (Anti-Patterns)

Do NOT use vague or engineering-jargon section titles:

```
FORBIDDEN: "Code Change Type: technical-migration"
FORBIDDEN: "Shared Purpose: javax→jakarta migration"
FORBIDDEN: "Same-Pattern Merge Group 3"
FORBIDDEN: "File-Level Completeness Section"
FORBIDDEN: "Do Not Over-Merge Example"
```

Use business-readable titles in the TSD main body:

```
GOOD: "3.4  Spring Jakarta EE 升版相容（Service 層）"
GOOD: "3.7  ECERT 連線客戶端設定調整"
GOOD: "3.1  PRJ-A 轉帳確認流程新增"
```

The engineering analysis labels go in the rationale report only.

---

## Cross-Reference

- `GROUPING_MODE_RULES.md` — Dual-mode decision rules, Mode A definition, PRJ-A and small-project examples
- `SKILL.md` Rule 17 — Section Grouping Rule (overview, now references dual-mode)
- `CLASSIFICATION_RULES.md` — Category definitions and merge conditions (pre-existing)
- `OUTPUT_SPEC.md` — 3.x.1 / 3.x.2 / 3.x.3 format per section; main text must not declare grouping mode
- `QA_CHECKLIST.md` Gate H — Grouping Mode Gate (dual-mode validation)

---

## Representative Example Selection — Cross-Mode Rule

Updated: 2026-05-12

Representative example selection (3.x.3 代表範例) is **NOT** driven solely by section title or code change pattern name.

**The priority is always determined by the file roles actually present in the section.**

### Mode A (Business Function Grouping)

Even when a section groups Controller + Service + DTO + Config files under one business function:

- If the section contains a **Controller class with an added/changed method** → use Controller method as example (Priority 1).
- If no Controller method → fall back to Service method (Priority 2).
- If no Service method → fall back to Object class method or snippet (Priority 3/4).

Do NOT select a Service method simply because the section title sounds like a service-level description.

### Mode B (Code Change Pattern Grouping)

The expected example source is determined by the section's declared Code Change Pattern:

| Pattern | Expected Example Source |
|---|---|
| `controller-api` / `controller-api-new` | Controller method (Priority 1) |
| `service-business-logic` / `service-logic-new` | Service method (Priority 2) |
| `dto-bo-request-response` / `dto-bo-field-new` | Object method or field snippet (Priority 3/4) |
| `data-layer-new` | Repository method or Entity field (Priority 4) |
| `external-client-new` | Client method (Priority 2/3) |
| `constants-errorcode-adjust` | Constants / enum snippet (Priority 4) |
| `config-properties-adjust` | Config snippet (Priority 5 fallback) |

Selecting a lower-priority source when a higher-priority candidate exists = **Gate J FAIL**.

**Full priority rule, fallback flow, and method detection signals:** See `core/EXAMPLE_SELECTION_RULES.md`.

---

## Three-Template Grouping Differences

Updated: 2026-05-14 (三模板 TSD 架構 Step 1)

The existing Mode A / Mode B dual-mode system primarily applies to Template B and Template C.
Template A introduces a distinct large-migration grouping model.

### Template A — Same-Pattern / Technical Migration Grouping

| Aspect | Template A Behaviour |
|--------|---------------------|
| Primary grouping key | Migration pattern type (e.g., `javax→jakarta`, `Swagger→OpenAPI`, `Axis→CXF`) |
| Number of sections | Intentionally **few** — group all files sharing the same migration pattern into ONE section |
| File-level detail | NOT per-file; section describes the common transformation |
| Section title style | Technical migration type (e.g., `3.1 Spring Jakarta EE 命名空間升版`) |
| Example selection | Best-representative hunk showing the common migration transformation |
| Forbidden | One section per file; over-splitting same-pattern files |
| Required | ALL files listed in appendix; common pattern stated clearly in 3.x.1 |

**Template A Grouping Anti-pattern:**
```
BAD:  One section per file for javax→jakarta migration (50 sections for 50 files)
GOOD: One section "Spring Jakarta EE 命名空間升版" covering all 50 files
```

**Template A Grouping Rule:**
- Same technical migration type → ONE section (regardless of file count)
- Different migration types (e.g., javax→jakarta vs Swagger→OpenAPI) → SEPARATE sections
- Business logic changes mixed with migration → MUST separate (migration in A, logic in B)

### Template B — Business Function / Code Pattern / Hybrid Grouping

Template B uses the existing Mode A / Mode B dual-mode system:

| Mode | When to Use in Template B | Details |
|------|--------------------------|---------|
| Business Function (Mode A) | End-to-end feature flows, cross-layer | Controller+Service+DTO+Config under one flow name |
| Code Change Pattern (Mode B) | Code-type precision, technical clarity | Each pattern type gets its own section |
| Hybrid | Mix of both | Must declare which sections use which approach |

**Template B is the primary use case for the Mode A / Mode B system defined earlier in this document.**

Recommended Code Change Pattern section types for Template B:
- Controller API
- Service Business Logic
- Service Flow Adjustment
- DTO / BO / Request / Response
- Entity / Repository / DAO / Data Service
- REST Client
- SOAP / WS Proxy
- Constants / Error Code / Session Key
- Config / Properties / Bean
- Delete Obsolete Class

**PRJ-A is the canonical Template B example.** Its 10-section Code Change Pattern structure is the reference grouping.

### Template C — Code Pattern / Per-File Grouping

| Aspect | Template C Behaviour |
|--------|---------------------|
| Primary grouping key | Code Change Pattern OR per-file (for very small sets) |
| Number of sections | Can be more granular than Template B (fewer files per section) |
| File-level detail | Per-file 3.x.2 / 3.x.3 detail per `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 |
| Section title style | Code pattern or file role |
| Example selection | Per-file or per-group; every file covered |
| Suitable for | 3–20 files; small API fix; localized bug fix |
| Forbidden | Applying to 100+ files without PASS_FOR_DEMONSTRATION note |

**Template C per-file grouping (micro-sections):**
- When file count is very small (3–5), each file MAY be its own section with full detail
- When file count is moderate (5–20), group by Code Change Pattern and provide per-file entries within section

**v19 grouping note:** v19 used Template B Code Change Pattern grouping (10 sections) while adding Template C per-file summaries to the appendix. This constitutes B+C Hybrid grouping — the section structure is Template B; the appendix detail is Template C.

### Summary Comparison

| Aspect | Template A | Template B | Template C |
|--------|-----------|-----------|-----------|
| Grouping basis | Same migration pattern | Business function / Code pattern | Code pattern / Per-file |
| Sections | Few | Medium (10–15 typical) | Can be granular |
| Files per section | Many (all sharing same pattern) | Medium | Few (per-file detail) |
| Per-file explanation | No | No (default) | Yes |
| Mode A / Mode B applicable | No (distinct A-model) | Yes (primary use case) | Yes (Mode B typical) |
| v19 (PRJ-A per-file summaries) | No | Hybrid only (B+C) | Reference behaviour |
