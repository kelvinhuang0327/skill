# TSD Template Mode Rules

## Purpose

This document defines the three TSD template modes. Every TSD generation MUST declare one template mode before content is written. The chosen mode governs section structure, appendix format, and QA expectations.

Created: 2026-05-14 (三模板 TSD 架構 Step 1)

---

## Template Mode Overview

| Mode | Name | Files | Primary Reviewer Need |
|------|------|------:|----------------------|
| **A** | Large Migration Template / 大型技術遷移模板 | 100+ (or high-repetition) | Common migration pattern + completeness |
| **B** | Medium Feature Template / 中量功能異動模板 | 10–100 | Section grouping + representative examples |
| **C** | Small Per-File Template / 小型逐檔異動模板 | 3–20 | Per-file detail + individual code blocks |

The template mode MUST be declared at the top of the QA report and rationale report as:

```
Template Mode: A / B / C
```

---

## Common Baseline (applies to ALL three modes)

All template modes MUST comply with the following without exception:

| Baseline Rule | Requirement |
|---|---|
| Template clone | MUST clone from designated Word template source; NOT from `Document()` blank |
| Header/Footer | MUST preserve original Header/Footer; no modification |
| TOC | MUST preserve TOC structure and hyperlinks; no overwrite |
| Section 1, 2, 4–11 | MUST remain unmodified |
| Appendix style | MUST use `v24ListBullet` for file bullets; `v24heading1/v24heading2` for headings |
| File coverage | All patch changed files MUST be accounted for (IN_SCOPE / SUPPORTING_DOCUMENT / OUT_OF_SCOPE / UNKNOWN) |
| README / support docs | May be excluded from main TSD body but MUST be listed as `SUPPORTING_DOCUMENT` or `OUT_OF_SCOPE` in QA report |
| Diff table: no git metadata | `diff --git`, `new file mode`, `index`, `rename from/to` MUST NOT appear inside any diff cell |
| Diff table: no ellipsis | `"..."` MUST NOT appear inside any diff cell |
| Diff colour | `+` green (1F7A1F), `-` red (B00020), `@@` blue+bold (2F5597), context grey (333333) |
| Font & size | Courier New, sz=17 (8.5pt), rPr order: rFonts → [b] → color → sz |
| QA report | Every output MUST have a QA report (with template mode declared) |
| Render QA | Every output MUST attempt render (qlmanage or equivalent); PNG documented in QA report |

---

## Template A — Large Migration Template / 大型技術遷移模板

### When to Use Template A

| Condition | Use A? |
|-----------|--------|
| Files are very numerous (100+) or highly repetitive | ✅ Yes |
| Change pattern is uniform (e.g., all javax→jakarta) | ✅ Yes |
| Framework upgrade / dependency migration | ✅ Yes |
| Reviewer's main concern is migration consistency + completeness | ✅ Yes |
| Per-file explanation would cause document explosion | ✅ Yes |
| Small number of files, each needing individual review | ❌ No → use C |
| Functional change across multiple layers | ❌ No → use B |

### NOT Suitable For

- Per-file review requirements
- Small number of files needing individual detail
- Requirements where each file has distinct business logic

### Template A Section Structure

```
3.x  [Technical Migration Category / Common Change Pattern]

3.x.1  異動說明
       Short description (can be 3-paragraph or summary style).
       Describe the common migration approach and its impact.

3.x.2  異動程式清單
       placement = appendix_only — content per core/CHANGE_PROGRAM_CONTRACT.md Layer 4

3.x.3  異動範例（Before / After）
       Representative diff hunk demonstrating the COMMON migration pattern.
       Choose the hunk that BEST represents the repeated change across all files.
```

### Template A Example Prioritization

For Template A representative examples, prefer hunks that show:
1. The core migration transformation (e.g., import replacement, annotation change, API rename)
2. Before = old pattern / After = new pattern, clearly showing the migration delta
3. A file whose hunk is the clearest and most complete representative of the group

### Template A Appendix Format

- Appendix change-program content (paths, change type, per-file summary requirement) is defined
  by `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 (Template A row)
- Section heading structure follows standard `v24heading1 / v24heading2` convention

### Template A QA Focus

| QA Area | Requirement |
|---------|-------------|
| File coverage | 100% — ALL IN_SCOPE files in appendix |
| Migration pattern consistency | Representative example captures common pattern |
| Section grouping | Same-pattern files grouped into one section; do NOT over-split |
| No over-detailed per-file explanation | 3.x.1 describes the pattern, not each file |
| Diff table | No metadata, no ellipsis, correct colours |
| Word style | Template clone, TOC intact |
| Render | PNG produced |

---

## Template B — Medium Feature Template / 中量功能異動模板

### When to Use Template B

| Condition | Use B? |
|-----------|--------|
| Files are medium volume (10–100, cross-layer) | ✅ Yes |
| Functional requirement with clear business purpose | ✅ Yes |
| Spans Controller / Service / DTO / Entity / Config layers | ✅ Yes |
| Section grouping + representative examples are the primary review need | ✅ Yes |
| Per-file individual explanation is NOT required by reviewer | ✅ Yes |
| Very large file set with single migration pattern | ❌ No → use A |
| Small file set, reviewer needs per-file detail | ❌ No → use C |

**Template B is the recommended default for PRJ-A-class projects.**

### Template B Section Structure

```
3.x  [Section Title — business-readable or code-pattern name]

3.x.1  異動說明
       15–30 字短摘要，描述該 section 的主要異動目的與影響。
       Use the three-part 0421 format or short-summary format.

3.x.2  異動程式清單
       placement = appendix_only — content per core/CHANGE_PROGRAM_CONTRACT.md Layer 4

3.x.3  異動範例（Before / After）
       Representative complete method / hunk.
```

### Template B Section Grouping Modes

Template B supports three sub-modes. MUST declare one:

| Sub-mode | When to Use |
|----------|-------------|
| `Business Function Grouping` | End-to-end feature flows, cross-layer changes |
| `Strict Code Change Pattern Grouping` | Code-type precision, technical analysis focus |
| `Hybrid` | Mix of the above — must declare which sections use which mode |

Recommended section types for Strict Code Change Pattern:
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

### Template B Example Prioritization

Prefer examples in this order:
1. Controller: complete handler method (request/response/session handling)
2. Service: complete business method (decision branches / flow calls)
3. Utility / Object: complete method or constructor
4. Object snippet: field + annotation block
5. Other logic-bearing hunk (last resort)

### Template B Appendix Format

- Appendix change-program content is defined by `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4
  (Template B row), including the default of no per-file summaries
- If the user explicitly requests per-file summaries, this becomes a Template B + Template C hybrid — mark as such in QA report and note that document length will increase
- Section heading structure follows standard `v24heading1 / v24heading2` convention

### Template B QA Focus

| QA Area | Requirement |
|---------|-------------|
| Section grouping | Logical, declared mode (Business / Code Pattern / Hybrid) |
| 3.x.1 format | Short summary, 3-part 0421 format, no engineering label |
| 3.x.2 | Matches `placement = appendix_only` per `core/CHANGE_PROGRAM_CONTRACT.md` |
| 3.x.3 example | Follows priority order; logic-bearing; no metadata |
| Diff table | No metadata, no ellipsis, correct colours |
| File coverage | 100% — ALL IN_SCOPE files in appendix |
| No per-file summary by default | Per-file summaries are Template C behaviour |
| Render | PNG produced |

---

## Template C — Small Per-File Template / 小型逐檔異動模板

### When to Use Template C

| Condition | Use C? |
|-----------|--------|
| Small number of files (3–20) | ✅ Yes |
| Reviewer needs per-file review | ✅ Yes |
| Each file needs an individual 15–30 char summary | ✅ Yes |
| Each file or each file group needs a corresponding code block | ✅ Yes |
| Small API fix, local bug fix, small-scope requirement | ✅ Yes |
| EOS-class large migration (100+ files, same pattern) | ❌ No → use A |
| PRJ-A-class medium project (formal delivery, no per-file needed) | ❌ No → use B |
| File count is large — will cause document to become too long | ❌ No → use A or B |

### Template C Section Structure

```
3.x  [Section Title]

3.x.1  異動說明
       15–30 字 section 摘要.

3.x.2  逐檔異動程式清單

       placement = inline_per_file — per-file entry fields (檔案 / 異動類型 / 異動摘要)
       are defined by core/CHANGE_PROGRAM_CONTRACT.md Layer 4.

3.x.3  逐檔異動程式區塊

       For each file (or representative file) in the section:
       ─────────────────────────────────
       檔案：path/to/File.java
       異動程式區塊：
       [patch hunk / method snippet — must be patch-traceable]
       ─────────────────────────────────
```

### Template C Appendix Format

- Appendix change-program content is defined by `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4
  (Template C row), including the ` — {action verb}{subject}{context}` description format
- Per-file summary MUST be patch-supported — no invented descriptions
- Section heading structure follows standard `v24heading1 / v24heading2` convention

### Template C When Applied to Large/Medium Projects

If Template C is applied to a medium or large project (e.g., PRJ-A 100-file project):

- Mark in QA report as `PASS_FOR_DEMONSTRATION`
- Add note: `Template C applied for demonstration purposes. Per-file summary coverage verified. Not the recommended template for formal delivery of medium/large projects.`
- Do NOT use this as evidence that Template C is suitable for medium/large formal delivery

### Template C QA Focus

| QA Area | Requirement |
|---------|-------------|
| Per-file summary | Every file has 15–30 char patch-supported summary |
| Per-file code block | Every file or representative file has corresponding code block |
| Document length | Monitor — flag if becoming excessively long |
| Diff table | No metadata, no ellipsis, correct colours |
| File coverage | 100% — ALL IN_SCOPE files in appendix |
| Render | PNG produced |
| If applied to medium/large project | PASS_FOR_DEMONSTRATION label required |

---

## Template Mode Decision Rule

### Primary Decision Tree

```
1. Are files very numerous (100+) or highly repetitive in change pattern?
   AND is the main change a technical migration / framework upgrade?
   → Template A

2. Are files medium volume (10–100) cross-layer functional changes?
   AND does the reviewer NOT require per-file individual detail?
   → Template B

3. Are files small in number (3–20)?
   AND does the reviewer need per-file review / individual code blocks?
   → Template C
```

### Quick Reference

| Signal | Template |
|--------|----------|
| Files very numerous + same migration pattern | A |
| Files medium + functional + cross-layer | B |
| Files few + per-file review | C |
| Uncertain — very many files, same pattern | A |
| Uncertain — medium files, functional | B |
| Uncertain — few files, reviewer wants detail | C |

### Hybrid Allowed

- Template B with user-requested per-file summaries → **B+C Hybrid**
  - Must be declared as Hybrid in QA report
  - Note: document will become longer than standard B
  - v19 (PRJ-A appendix per-file summaries) is an example of B+C Hybrid applied as demonstration

---

## v19 Positioning Rule

v19 (`PRJ-A_20260505_v19_SHORT_CHANGE_SUMMARY.docx`) added 15–30 character summaries to all 90 appendix file entries.

**This is NOT Template B default behaviour.**

v19 is positioned as:
```
Template C — Small Per-File Template prototype / reference behaviour
```

Specifically:
- Template B default: appendix lists files without per-file summaries
- Template C default: appendix includes per-file 15–30 char summaries
- v19 applied Template C summary style to a PRJ-A medium project → constitutes B+C Hybrid (demonstration)
- v19 is VALID as a QA-PASS demonstration of Template C summary capability
- v19 MUST NOT be treated as evidence that Template B requires per-file summaries

This positioning is also recorded in `TEMPLATE_MODE_OUTPUT_SPEC.md`.

---

## Anti-Patterns

| Anti-Pattern | Description | Correct Behaviour |
|---|---|---|
| Treating v19 as Template B default | Using v19's per-file summaries as the standard for all Template B outputs | Declare v19 as Template C prototype; Template B defaults to no per-file summary |
| Applying Template C to large projects without marking demonstration | Producing per-file summaries for 100+ files without flagging | Add PASS_FOR_DEMONSTRATION note in QA report |
| Selecting Template A for functional multi-layer changes | Grouping Controller+Service+DTO together as "migration" | Use Template B for cross-layer functional changes |
| No template mode declaration | Generating TSD without declaring A / B / C | Always declare mode at top of QA and rationale reports |
