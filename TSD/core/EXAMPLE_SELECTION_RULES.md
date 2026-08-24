# TSD Representative Example Priority Rule

## Purpose

This document defines the mandatory priority order for selecting the 3.x.3 representative example (代表範例) in every TSD section. Selection is NOT driven solely by the section title or code change pattern name — it is driven by the file roles present in the section's assigned patch files.

Created: 2026-05-12

---

## Core Rule: Representative Example Priority Rule

```
3.x.3 representative example MUST follow the file-role priority order below.
Priority 1 is always checked first. Only fall through to the next priority if no
valid candidate exists at the current level.
```

---

## Priority Order

### Priority 1 — Controller: First Added or Changed Method

**Trigger:** The section's assigned files contain one or more Controller classes.

**Selection:**
- From all Controller patch hunks in this section, select the **first added or changed method**.
- Prefer: endpoint / request mapping / API handler method.
- Prefer: `@RequestMapping`, `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping` annotated methods.
- Prefer: `public ResponseEntity`, `public ModelAndView`, `public Object xxx(...)` signatures.

**Do NOT select:**
- Class declaration header (`public class FooController extends ...`)
- Package declaration line
- Import-only block
- Annotation-only block (no method body)
- Constructor with no logic

**Pass condition:** At least one `+` line in the hunk belongs to the method body (beyond the signature).

---

### Priority 2 — Service: First Added or Changed Method

**Trigger:** The section has no Controller, OR the Controller has no valid added/changed method candidate.

**Selection:**
- From all Service patch hunks in this section, select the **first added or changed method**.
- Prefer: methods with business logic, conditional judgment (`if/else/switch`), external call, session operation, or repository call.

**Do NOT select:**
- Pure getter / setter
- Empty method (`{  }` with no body)
- Method with only `log.xxx()` and no real logic
- Package declaration
- Import block
- Class header

**Pass condition:** At least one `+` line in the hunk contains logic (assignment, method call, conditional, return with expression).

---

### Priority 3 — Object Class: First Added or Changed Method

**Trigger:** No Controller or Service method candidate is available.

**Selection:**
- Object classes include: DTO, BO, Request, Response, Entity, VO, Model.
- Select the **first added or changed method** from these classes.
- Prefer: methods with data transformation, field handling, validation, mapping, constructor with logic, factory, or helper logic.
- If only field additions exist (no method change), the field hunk may be used (see Priority 4).

**Do NOT preferentially select:**
- Plain getter / setter (only acceptable as last resort within this priority)
- `toString()` / `equals()` / `hashCode()` unless it is the only substantive change

**Pass condition:** At least one `+` line in the hunk is a field declaration, constructor, or method body beyond a trivial getter/setter.

---

### Priority 4 — Object Class Code Snippet Fallback

**Trigger:** No Controller, Service, or Object method candidate is available.

**Selection:**
- Select the first substantive code snippet from object class files, such as:
  - Field addition (new `private` / `public` field declaration)
  - Enum value addition
  - Constant group addition
  - Request / Response field addition
  - Entity field addition
  - Repository interface method declaration

**Must satisfy:**
- Content comes verbatim from the patch hunk
- The snippet represents the section's change purpose
- No pseudo diff
- No git metadata
- No ellipsis

---

### Priority 5 — Other Logic-Bearing Hunk (Final Fallback)

**Trigger:** Priorities 1–4 all produce no valid candidate.

**Selection:**
- May use other file types: config, properties, constants, external client, repository / DAO.
- The hunk MUST be logic-bearing (see PASS conditions under PFC-12).

**Required:** Mark as `WEAK` in QA report. Explain why no Controller / Service / Object method candidate was available.

---

## Method Detection Signals

When scanning a patch hunk to identify "a method", use the following signals:

| Signal | Example |
|---|---|
| Public/private/protected method declaration | `+    public ResponseEntity<?> submitOrder(` |
| Mapping annotation above a method | `+    @PostMapping("/order")` |
| Return type + method name + `(` | `+    public FXOrdersResponse buildOrders(` |
| Method body with new/deleted logic | `+        if (request == null) throw new BadRequestException` |
| Context line showing method name | ` public void processTransfer(` |

---

## "First Added or Changed Method" Definition

### First Added Method
The **earliest** occurrence in the patch hunk that begins a new method declaration (signature line with `+`) and whose body contains at least one more `+` line.

### First Changed Method
The **earliest** occurrence in the patch hunk where an existing method body has at least one `+` or `-` line inside the method body (not just the signature).

### Weak Candidate
A hunk that shows only a method signature `+` line but no body changes. This is a **weak candidate** — acceptable only if no stronger candidate exists.

---

## Fallback Flow (Step-by-Step)

```
Step 1:
  Collect the section's assigned patch files (IN_SCOPE only).

Step 2:
  Does any file end with "Controller.java" or contain @RequestMapping?
    → YES: Scan Controller patch hunks → find first added/changed method.
    → FOUND: Use this hunk as 3.x.3 example. STOP.

Step 3:
  No Controller method candidate found.
  Does any file end with "Service.java" or "ServiceImpl.java"?
    → YES: Scan Service patch hunks → find first added/changed method.
    → FOUND: Use this hunk. STOP.

Step 4:
  No Service method candidate found.
  Does any file contain BO / DTO / Request / Response / Entity / VO / Model?
    → YES: Scan object class patch hunks → find first added/changed method.
    → FOUND: Use this hunk. STOP.

Step 5:
  No object class method candidate found.
  Select the first substantive code snippet from object classes:
  field / enum / constant / repository method declaration / entity mapping.
    → FOUND: Use this snippet. STOP.

Step 6:
  Still no valid candidate.
  Select another logic-bearing hunk from any remaining assigned file.
  Mark as WEAK in QA report.
  Explain: "No Controller/Service/Object method candidate available in this section."

Step 7:
  If only a weak example can be found, mark QA Gate J check
  "Weak fallback is explicitly marked = PASS" with the explanation note.
```

---

## Applicability: Mode A and Mode B

### Mode A — Business Function Grouping

When a single business-function section contains controller + service + DTO files:

- Priority 1 still applies: **use Controller method first**.
- If Controller has no valid method → fall to Service.
- If Service has no valid method → fall to DTO/BO/Response.
- The presence of multiple code patterns in a Mode A section does NOT change the priority order.

### Mode B — Code Change Pattern Grouping

When the section itself is typed by code pattern:

| Section Pattern | Expected Example Source |
|---|---|
| `controller-api` / `controller-api-new` | Controller method (Priority 1) |
| `service-business-logic` / `service-logic-new` / `service-logic-modify` | Service method (Priority 2) |
| `dto-bo-request-response` / `dto-bo-field-new` | Object method or field snippet (Priority 3/4) |
| `data-layer-new` | Repository method declaration or Entity field (Priority 4) |
| `external-client-new` | Client method (Priority 2/3 logic) |
| `constants-errorcode-adjust` | Constants / enum snippet (Priority 4) |
| `config-properties-adjust` | Config snippet (Priority 5 fallback) |
| `deletion-removal` | Deleted method body (applicable `-` lines) |

In Mode B, deviating from the expected example source without justification = **Gate J FAIL**.

---

## Prohibited Example Sources

The following MUST NOT be used as the primary 3.x.3 representative example:

| Prohibited Content | Reason |
|---|---|
| `diff --git a/... b/...` | git metadata, not code |
| `new file mode 100644` | git metadata |
| `deleted file mode 100644` | git metadata |
| `index xxxx..xxxx` | git metadata |
| `--- a/...` / `+++ b/...` | git diff file header |
| `...` (ellipsis) | truncation marker, not patch content |
| package declaration only | no logic |
| import-only block | no logic (unless technical migration section) |
| class declaration only | no method body |
| annotation-only block | no logic |
| getter/setter-only | no business logic |
| empty class / boilerplate | no content |
| README / `.md` file | documentation, not code |
| unrelated supporting file | out of section scope |
| pseudo code (not in patch) | fabricated content |

**Exception:** If the section is explicitly a **technical migration** section and the core change IS import or annotation replacement, then import / annotation hunks may be used as Priority 3 examples. This exception must be noted in the QA report.

---

## Source Labeling in TSD (3.x.3)

### Required label format (outside diff table, before the table):

```
範例來源：<FileName.java> — <methodName(...)> [or 欄位新增 / 常數新增]
以下為 patch 原文節錄：
```

### If it is a fallback example:

```
範例來源：<FileName.java> — 欄位新增（Controller/Service method 不適用）
以下為 patch 原文節錄：
```

### MUST NOT appear in TSD main body:
- Priority level number (e.g., "Priority 1")
- The word "WEAK"
- Internal QA labels

Priority judgment details belong in the **QA report only** (Gate J).

---

## Cross-Reference

- `QA_CHECKLIST.md` Gate J — Representative Example Priority Gate
- `PATCH_FACT_CHECK.md` — PFC-12 logic-bearing rule; Representative Example Priority Check table
- `OUTPUT_SPEC.md` — 3.x.3 代表範例 format, source priority list
- `SECTION_GROUPING_RULES.md` — Mode A / Mode B; example not driven solely by section title
- `SKILL.md` — Rule 10 (Representative Example Priority Rule)
