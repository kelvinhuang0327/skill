# TSD Grouping Mode Rules

## Purpose

This document defines the **dual-mode section grouping system** for TSD generation.

Two modes are both valid and intentional. Neither mode is "the only correct approach."
The generator MUST declare the grouping mode in the rationale report (not in the TSD main body).

Created: 2026-05-12

---

## Overview

| Mode | Name | Primary Grouping Key | Best For |
|------|------|---------------------|---------|
| **Mode A** | Business Function Grouping | Shared business purpose / end-to-end flow | Large requirements, cross-layer changes for one flow |
| **Mode B** | Code Change Pattern Grouping | Code change pattern type | Small projects, targeted fixes, precise code-type classification |

Both modes MUST maintain File-Level Completeness (every patch file accounted for).

---

## Mode A — Business Function Grouping / 業務功能分群

### Definition

```
TSD section = All files that jointly support one business function,
              one end-to-end flow, or one shared business purpose.
```

One section may include multiple code change patterns (controller + service + dto + config)
as long as they all serve the same identified business flow.

### Applicable When

1. Requirement is a large business function change
2. Multiple layers (Controller / Service / DTO / Repository / Config) all support the same API endpoint or flow
3. Splitting by code pattern would fragment the flow and confuse business reviewers
4. Primary audience is business / BA / PM / system owner, not just developers
5. Shared purpose across files is strong and explicitly identifiable
6. Examples: PRJ-A FX exchange flow, SSO login flow, ECERT certificate validation flow

### Section Structure Under Mode A

- Section title reflects the business function, e.g., "3.1 FX 換匯主流程"
- 3.x.1 異動說明 describes the business goal and impact across affected layers
- One section may span controller-api + service-business-logic + dto-bo + config etc.
- `SUPPORTING_SCOPE` files must still be labelled as supporting, not main
- Rationale report MUST disclose all included code change patterns

### Allowed in Mode A (Not Allowed in Mode B)

```
A section may include:
  - controller-api  (primary)
  - service-business-logic  (primary)
  - dto-bo-request-response  (primary)
  - dao-repository-mapper  (supporting)
  - entity-pk-repository-service  (supporting for the flow)
  - constants-error-code-session-key  (supporting)
  - config-properties-bean  (supporting)
  - service-flow-adjustment  (supporting)
  - delete-obsolete-class  (supporting)

→ Acceptable in Mode A because all serve ONE identified business function.
```

### Mode A Rationale Requirements

Per section, the rationale report MUST state:

| Required Item | Content |
|---|---|
| Grouping Mode | Business Function Grouping (Mode A) |
| Business Function | Name and description of the shared business flow |
| Why not split by code pattern | Explain that splitting would fragment the flow's reviewability |
| Primary code change patterns | List the dominant patterns (e.g., controller-api, service-business-logic) |
| Supporting code change patterns | List supporting patterns (e.g., service-flow-adjustment, constants) |
| Supporting files | List SUPPORTING_SCOPE files and their role |
| Representative example reason | Why this hunk best represents the core flow logic |

### Mode A Representative Example Selection

Prioritise the hunk that best represents the **core business flow logic**:

1. The service method that orchestrates the main flow (e.g., `orders()`, `requote()`)
2. The controller method that defines the API entry point
3. External client invocation that connects to key external system
4. Data access method for the primary entity of the flow
5. Config or constants that directly affect the flow's runtime behavior

A single representative example may stand for multiple supporting files in the same section.
The rationale report must explain which supporting patterns are covered.

---

## Mode B — Code Change Pattern Grouping / 程式碼異動模式分群

### Definition

```
TSD section = Files with the same (or closely similar) code change pattern
              + compatible shared purpose + file-level completeness.
```

Primary grouping key: code change pattern.
Secondary grouping key: shared purpose (for merge/split decisions within the same pattern).

### Applicable When

1. Small project or targeted change
2. Fewer patch files
3. Change is primarily one type of code modification
4. Business flow is not the main review focus
5. Reviewer needs to quickly scan "what changed in controllers / services / DTOs"
6. Multiple files have different purposes but the same type of code change
7. Technical migration or framework upgrade where code change type dominates
8. Business-purpose grouping would be too vague (files serve unrelated purposes)

### Section Structure Under Mode B

- Section title reflects the code change pattern, e.g., "3.3 Request / Response BO 欄位調整"
- 3.x.1 說明 describes the type of code change and what it achieves
- One section ideally has one primary code change pattern
- Different code change patterns should be in separate sections
- See `SECTION_GROUPING_RULES.md` for the full 10-step Mode B decision flow

### Mode B Rationale Requirements

Per section, the rationale report MUST state:

| Required Item | Content |
|---|---|
| Grouping Mode | Code Change Pattern Grouping (Mode B) |
| Primary Code Change Pattern | From pattern table in SECTION_GROUPING_RULES.md |
| Pattern Evidence | Cite patch hunk lines that confirm the pattern |
| Shared Purpose | Secondary — used as merge/split confirmation |
| Why not merged with adjacent pattern sections | Explain code-pattern difference |
| Why not split further | Explain shared purpose justification |
| Representative example reason | Why this hunk represents the pattern |

### Mode B Representative Example Selection

Select the hunk that best demonstrates the **code change pattern**:

| Pattern | Preferred Hunk |
|---------|---------------|
| controller-api | Controller method (@PostMapping / @GetMapping handler) |
| service-logic-new | Service logic method with business conditions |
| dto-bo-field-new | Field additions and annotations (not getter/setter) |
| data-layer-new | Repository / DAO query method or Entity field definition |
| external-client-new | Request build and HTTP invocation code |
| constants-errorcode-adjust | Business-meaningful constants or error code additions |
| config-properties-adjust | Runtime-affecting property or bean definition |
| technical-migration | Import or annotation replacement that shows the migration pattern |

---

## Grouping Mode Decision

### Decision Tree

```
Step 1: Is the requirement a large-scale end-to-end business flow change?
        → YES: Is the audience primarily business reviewers / BA / PM?
               → YES: Do the files collectively support one clearly named business flow?
                      → YES: → Use Mode A (Business Function Grouping)
                      → NO:  → Consider Mode B or Hybrid
               → NO:  → Continue to Step 2
        → NO:  → Continue to Step 2

Step 2: Is the change primarily one type of code modification (controllers, services, DTOs...)?
        → YES: Does splitting by code type help reviewers understand what changed?
               → YES: → Use Mode B (Code Change Pattern Grouping)
               → NO:  → Consider Mode A or Hybrid
        → NO:  → Continue to Step 3

Step 3: Does the patch have multiple independent changes of different types?
        → YES: → Use Mode B (Code Change Pattern Grouping)
        → NO:  → Hybrid: main sections Mode A, supporting sections Mode B
```

### Select Mode A (Business Function Grouping) When ALL of the Following Apply

1. Files support a clearly named end-to-end business flow
2. Controller / Service / DTO / Repository / Config are all required for the same flow
3. Splitting by code pattern would fragment flow reviewability
4. Primary reviewers need business-flow context, not code-type breakdowns
5. Shared purpose is strong, explicit, and traceable to one business function
6. Large requirements or cross-layer changes (e.g., PRJ-A FX exchange)

### Select Mode B (Code Change Pattern Grouping) When ANY of the Following Apply

1. Small requirement or targeted fix
2. Fewer patch files (rough guide: < 20 files total)
3. Code change type is more important than business flow
4. Business flow is not the primary review concern
5. Multiple files with different purposes share the same code change type
6. Reviewer needs to quickly identify "which controllers changed / which services changed"
7. Technical migration or framework upgrade
8. Business-purpose grouping would produce vague or misleading sections

### If Uncertain — Apply These Defaults

| Signal | Default |
|--------|---------|
| End-to-end flow + cross-layer + large requirement | Mode A |
| Small fix + code type clear + scattered purpose | Mode B |
| Large project (Mode A) with standalone utility changes | Mode A main, Mode B subsections (Hybrid) |
| Small project where one module has strong flow identity | Mode B with Shared Purpose secondary note |

---

## File-Level Completeness — Applies to Both Modes

Regardless of Mode A or Mode B, EVERY patch-changed file MUST appear in ONE of:

1. TSD appendix (IN_SCOPE files)
2. SUPPORTING_DOCUMENT list in QA report
3. OUT_OF_SCOPE list in QA report
4. UNKNOWN list in QA report (flagged for human review)

**Coverage equation (both modes):**
```
Total patch changed files
  = IN_SCOPE (appendix)
  + SUPPORTING_SCOPE (appendix)
  + SUPPORTING_DOCUMENT (QA report)
  + OUT_OF_SCOPE (QA report)
  + UNKNOWN (QA report)

missing = 0
```

Grouping mode does NOT grant permission to omit files from the appendix.
Mode A grouping does NOT allow summarising a group without listing individual files.

---

## Rationale Report Template — Dual-Mode Version

Each TSD must include a rationale report (not part of the TSD main body) with:

### Top-Level Declaration

```markdown
## Grouping Mode Declaration

Grouping Mode: [Business Function Grouping (Mode A) | Code Change Pattern Grouping (Mode B) | Hybrid]

Why this mode:
[2–3 sentences explaining why this mode was selected for this project]

Why not the other mode:
[1–2 sentences]
```

### Per-Section Analysis

For each section, the rationale report MUST include:

```markdown
### Section [N] — [Title]

| Item | Value |
|------|-------|
| Grouping Mode | Mode A / Mode B |
| Primary Shared Purpose (Mode A) | [business flow name] OR N/A |
| Primary Code Change Pattern (Mode B) | [pattern name] OR N/A |
| All Included Code Change Patterns | [list all patterns present] |
| Main Files | [list of IN_SCOPE primary files] |
| Supporting Files | [list of SUPPORTING_SCOPE files and roles] |
| Why Not Split | [reason] |
| Why Not Merge With Adjacent Section | [reason] |
| Representative Example Reason | [why this hunk was selected] |
```

### Mode A Disclosure (Mandatory if Mode A Selected)

When Mode A is used and a section contains multiple code change patterns:

```markdown
Section [N] — Mixed Pattern Disclosure

This section is grouped under Mode A (Business Function Grouping).
The following code change patterns are present in this section:
  - [pattern 1]: PRIMARY — [file names]
  - [pattern 2]: SUPPORTING — [file names]
  - [pattern 3]: SUPPORTING — [file names]

Justification: These files collectively support [business flow].
Splitting them by code pattern would require reviewers to cross-reference
[N] separate sections to understand the full impact of [business flow].
```

---

## Representative Example Rules — Mode-Aware Summary

| Situation | Mode A | Mode B |
|-----------|--------|--------|
| Multiple patterns in section | Select hunk for core flow logic | Avoid mixing — each section should have one pattern |
| Supporting files present | Example can represent the main flow; note supporting role in rationale | Supporting files should be in a separate section or labelled |
| Service method available | Prefer service orchestration hunk | Prefer service logic hunk for the specific pattern |
| Config / properties | Select if directly affects the flow | Select if config change is the section's primary pattern |

Both modes PROHIBIT:
- `diff --git` / `new file mode` / `deleted file mode` / `index` / `similarity index` / `rename from/to` in diff cells
- `...` (ellipsis) in diff cells
- Pseudo diff or fabricated content
- Pure getter / setter / package / import-only hunks (unless section is specifically about migration)
- Any content not traceable to actual patch file

---

## Example: PRJ-A FX Main Flow — Mode A Is Correct

### Project Context

PRJ-A FX exchange is a large end-to-end business requirement:
- New API endpoints for FX orders and requote
- Business service logic for order building, FX integration, ECERT verification
- New DTO/BO objects for request/response data transfer
- Supporting utilities for notes, PDF, and document management

### Files Involved (Section 3.1)

| File | Code Change Pattern | Scope |
|------|---------------------|-------|
| FXExchangeController.java | controller-api | IN_SCOPE |
| FXExchangeService.java | service-business-logic (new) | IN_SCOPE |
| FXRequoteBO.java | dto-bo-request-response | IN_SCOPE |
| FXOrdersBO.java | dto-bo-request-response | IN_SCOPE |
| CheckMidStatusResponse.java | dto-bo-request-response | IN_SCOPE |
| FXQueryVerifyResultRequest.java | dto-bo-request-response | IN_SCOPE |
| FXQueryVerifyResultResponse.java | dto-bo-request-response | IN_SCOPE |
| GetOrderDocResponse.java | delete-obsolete-class | IN_SCOPE |
| NoteManagerService.java | service-flow-adjustment | SUPPORTING_SCOPE |
| PDFManagerService.java | service-flow-adjustment | SUPPORTING_SCOPE |
| UtilityService.java | service-flow-adjustment | SUPPORTING_SCOPE |

### Mode Decision

**Mode A — Business Function Grouping**

**Reason:** All files support the PRJ-A FX Requote / Orders main flow.
Splitting by code pattern (controller section, service section, DTO section separately) would force
reviewers to jump across 3+ sections to understand the full impact of a single business flow change.
The NoteManagerService / PDFManagerService / UtilityService are SUPPORTING_SCOPE
(they support the flow but are not the primary implementation).

**Rationale disclosure required:**
```
Main patterns: controller-api, service-business-logic
Supporting patterns: dto-bo-request-response, service-flow-adjustment, delete-obsolete-class
SUPPORTING_SCOPE files: NoteManagerService, PDFManagerService, UtilityService
Why not split: Splitting would fragment a single business flow across multiple sections
```

**This Mode A grouping is correct and should NOT be flagged as over-merge.**

---

## Example: Small API Enhancement — Mode B Is Preferred

### Project Context

A small patch with:
- 1 Controller update (new endpoint added)
- 1 Service update (new business method)
- 2 Request / Response DTO objects (new fields)
- 1 Properties file update (new config key)

### Mode Decision

**Mode B — Code Change Pattern Grouping**

**Sections:**

```
3.1 Controller API 調整        — controller-api (1 file)
3.2 Service 邏輯調整           — service-logic-new (1 file)
3.3 Request / Response 物件調整 — dto-bo-field-new (2 files)
3.4 Properties 設定調整        — config-properties-adjust (1 file)
```

**Reason:** Reviewer needs to quickly identify what type of code changed.
Business flow is simple enough that code-type grouping is clearer.
Splitting by code type does not fragment any complex flow — the flow is a single API call.

**This Mode B grouping is correct and preferred for small projects.**

---

## Cross-Reference

- `SECTION_GROUPING_RULES.md` — Full 10-step Mode B decision flow + Code Change Pattern table
- `QA_CHECKLIST.md` Gate H — Grouping Mode Gate (dual-mode validation)
- `OUTPUT_SPEC.md` — TSD main body format (no grouping mode metadata in main text)
- `SKILL.md` Rule 17 — Section grouping overview (updated to reference dual-mode)
