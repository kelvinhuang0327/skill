# TSD Template Mode Output Spec

## Purpose

This document defines the output format differences between Template A, B, and C.
It supplements `OUTPUT_SPEC.md` and `TEMPLATE_MODE_RULES.md`.

Created: 2026-05-14 (三模板 TSD 架構 Step 1)

---

## Common Outputs (All Templates)

Every template mode produces the following mandatory outputs:

| Output | Description |
|--------|-------------|
| Word `.docx` | Cloned from designated template; never blank |
| QA report `.md` | Includes template mode declaration; all gates checked |
| Render | qlmanage PNG or equivalent; documented in QA report |

---

## 3.x.1 Change Description Format

| Template | Format | Length |
|----------|--------|--------|
| A | Short paragraph or 3-paragraph summary. Describes **common pattern** across all files. | No per-file detail |
| B | 15–30 char section summary (三段式 0421 or short-summary). Describes **section's main purpose**. | One short summary per section |
| C | 15–30 char section summary (same as B). Each section is small so detail is higher. | One short summary per section |

---

## 3.x.2 Programme List Format

Owned by `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4. This document does not restate the
per-template change-program table; A / B map to `placement = appendix_only` and C maps to
`placement = inline_per_file`.

---

## 3.x.3 Example Format

| Template | Example Type | Priority |
|----------|-------------|---------|
| A | One representative hunk showing the **common migration transformation** | Best-representative-of-pattern |
| B | One representative complete method / hunk | Controller → Service → Utility → Object → Snippet |
| C | Per-file or per-group code block; every file (or representative file) has its own block | Each file covered |

---

## Appendix Format

Owned by `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 (appendix content by mode). Not restated here.

**B+C Hybrid:** If Template B is used with user-requested per-file summaries, declare as Hybrid.
Appendix gains per-file summaries (like Template C) while main body retains Template B section structure.

---

## v19 Output Classification

| Output | Template Classification |
|--------|------------------------|
| `PRJ-A_20260505_v19_SHORT_CHANGE_SUMMARY.docx` | **Template C prototype / B+C Hybrid demonstration** |
| Appendix per-file summaries (90 entries) | Template C behaviour |
| Section grouping (10 sections, Code Change Pattern) | Template B behaviour |

v19 MUST NOT be treated as evidence that Template B requires per-file appendix summaries.

---

## QA Report Template Mode Section

Every QA report MUST begin with a Template Mode declaration block:

```
## Template Mode
Template Mode: [A / B / C / B+C Hybrid]
Rationale: [Why this mode was chosen]
v19 Note (if applicable): Template C prototype behaviour; per-file summaries are C-style, not B default.
```

---

## Output Size Expectations

| Template | Typical Word Size | Notes |
|----------|-----------------|-------|
| A | Compact — fewer sections, no per-file entries | EOS-scale = 100+ files, but sections few |
| B | Medium — 10 sections, representative examples | PRJ-A-scale typical |
| C | Can be long — each file has detail block | Monitor length; flag if excessive |

**Gate C6 (file size):** All modes require output within ±30% of template size. Template C may legitimately be larger; flag if >2× template size.

---

## Cross-Reference

| Rule | Location |
|------|----------|
| Template mode decision tree | `core/TEMPLATE_MODE_RULES.md` |
| Section grouping by template | `core/SECTION_GROUPING_RULES.md` |
| Patch fact-check by template | `core/PATCH_FACT_CHECK.md` |
| QA gate K (template mode gate) | `core/QA_CHECKLIST.md` |
| Output spec (OOXML details) | `core/OUTPUT_SPEC.md` |
