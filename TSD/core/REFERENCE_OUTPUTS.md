# TSD Reference Outputs

## Purpose

This document defines the two reference roles used in TSD skillpack:
1. The Word template source used for cloning
2. The operator-bound final reference artifact used for regression comparison

Created: 2026-05-11

---

## Template Source

**Path:**

```
`<OPERATOR_BASE_TEMPLATE_INPUT>`
```

**Skill asset alias:**

```
assets/base_template.docx
```

**Role:** Word template source.

**Usage:**

- Clone base — `doc = Document(this_file)` for ALL new TSD
- Style source — all `pStyle`, font, colour, margin definitions come from here
- Structure source — Cover page, TOC, Header, Footer, Section 4–11 come from here

**What it provides:**

| Element | Provided by Template |
|---|---|
| Cover page + metadata table | ✅ |
| TOC with PAGEREF fields | ✅ |
| Header + Footer | ✅ |
| Section 1 Preamble | ✅ |
| Section 2 System Overview | ✅ |
| Section 4 System Interfaces through Section 11 | ✅ |
| Appendix style | ✅ |
| v24heading1, v24heading2, v24ListBullet | ✅ |
| Diff table style | ✅ |
| Courier New / 標楷體 font settings | ✅ |

---

## Final Reference Output

**Binding token:**

```
`<REF_FINAL>`
```

`<REF_FINAL>` is a symbolic baseline binding, not a literal filename or path.
It names the exact operator-supplied local reference artifact bound for Gate I
regression comparison. The concrete locator is local run input and is
intentionally not embedded in Git-owned source.

**Role:** V7 Template-B final reference output / regression baseline, once
bound by the operator for the run.

**Binding requirements:**

- Before Gate I runs, the operator MUST bind `<REF_FINAL>` to an actual local
  reference artifact for that run.
- The bound artifact is the baseline used by the existing Gate I regression
  checks below; the token itself is not an executable artifact.
- Do not hardcode the concrete local locator, commit the reference artifact, or
  bundle it into the generated runtime tree.
- If `<REF_FINAL>` is not bound to an actual artifact, Gate I2 MUST NOT be
  reported PASS.

**Usage:**

- Compare final behavior between new TSD generations and the artifact bound to
  `<REF_FINAL>`
- Validate section 3 layout and writing style
- Validate 3.x.2 `placement = appendix_only` behaviour (Template B baseline only)
- Validate appendix list behavior
- Validate README / SUPPORTING_DOCUMENT exclusion handling
- Validate diff example display behavior (logic-bearing, no git metadata, no ellipsis)
- Validate diff run-level color behavior
- Validate all `@@` blue + bold behavior (including multiple hunks)
- Validate hyperlink / bookmark existence between main body and appendix
- Validate render sanity (no broken layout, no text overflow)

---

## Important Role Distinction

| Attribute | Template Source | Gate I Reference Artifact |
|---|---|---|
| Binding | `assets/base_template.docx` | `<REF_FINAL>` |
| Role | Clone base | Operator-bound V7 regression baseline |
| Concrete locator | Operator-supplied local input | Operator-supplied local input; never hardcoded in Git |
| Use `Document(this_file)` | **YES — REQUIRED** | **NO — FORBIDDEN** |
| Copy content to new TSD | Structure only (section 4-11) | **NO — never copy PRJ-A-specific content** |
| Contains project-specific content | No (generic template) | Yes (PRJ-A project) |

---

## Gate I Binding and Execution

Gate I2 is executable only when `<REF_FINAL>` has been bound to a concrete
operator-supplied local artifact. The run must then evaluate the generated
output against that bound artifact using the existing regression checks in this
document. Comparing the token to itself, or treating the token as a resolvable
artifact without a binding, is not a Gate I regression run.

## Reference Checks (Regression Behavior)

**Scope: `TEMPLATE_B_BASELINE_BEHAVIOR`.** The Final Reference Output is a Template-B-era
artifact, so the checks below describe Template B baseline behaviour. They are NOT a
mode-independent rule and MUST NOT be generalized to Template C.

Template C reference parity is `NOT DEFINED / PENDING COMPLIANT TEMPLATE-C ARTIFACT`.
No Template C golden or reference shape is defined here. The absence of one does not block
applying the Template C rules in `core/TEMPLATE_MODE_RULES.md` and
`core/CHANGE_PROGRAM_CONTRACT.md`.

When comparing a new Template B TSD against the artifact bound to `<REF_FINAL>`,
verify:

| Check | Expected Behavior (based on PRJ-A v7 final; Template B baseline) |
|---|---|
| 3.x.1 writing style | Three-part 0421 format: What / What changed / What files |
| 3.x.2 content (Template B baseline) | Single hyperlink paragraph only: 「查看附錄完整清單（n 個檔案）」 — `placement = appendix_only` per `core/CHANGE_PROGRAM_CONTRACT.md` |
| 3.x.2 plain file list (Template B baseline) | None — forbidden under `placement = appendix_only` |
| Appendix content | Full changed-file list in v24ListBullet format |
| README exclusion | README listed as SUPPORTING_DOCUMENT in QA report; absent from appendix |
| diff example type | Logic-bearing patch hunk snippet (business logic, API, data access) |
| diff example git metadata | None — `diff --git`, `new file mode`, `index` must not appear in diff cell |
| diff example ellipsis | None — `...` must not appear in diff cell |
| `+` line color | Green `1F7A1F` |
| `-` line color | Red `B00020` |
| `@@` hunk header color | Blue `2F5597` + bold |
| Multiple `@@` in same cell | All checked — not only first hunk |
| 3.x.2 → appendix hyperlink | Functional Word bookmark link to `APPENDIX_SECTION_N` |
| Appendix → 3.x back-link | Functional Word bookmark link to `MAIN_SECTION_N` |
| Output render | No broken layout, no text overflow, TOC renders correctly |

---

## Strictly Forbidden Operations

1. Treating `<REF_FINAL>` as a literal filename/path or using the bound
   reference artifact as the TSD clone base
2. Copying PRJ-A-specific content (project name, file paths, FX/ECERT identifiers, amounts like "PRJ-A") into new TSD
3. Treating the v7 final output as a template for future projects
4. Using PRJ-A file path list as the default appendix content for another project

---

## Lesson Learned (PRJ-A v6 → v7 Correction)

During the PRJ-A TSD generation process, the following issues were discovered in v6 and corrected in v7:

| Issue | Root Cause | Correction |
|---|---|---|
| Multiple `@@` headers not blue+bold | `extract_diff_block()` only processed first `@@` | Scan ALL `@@`-starting paragraphs in every diff cell |
| git metadata in diff cell | `diff --git`, `new file mode`, `index` placed inside table | Forbidden from diff cell — allowed only in table-preceding paragraph |
| Ellipsis in diff cell | `...` used as truncation marker inside table | Forbidden from diff cell — use `以下為 patch 原文節錄。` before table |
| README file missing from coverage | `ECERTRestClient_README.md` silently excluded | Must appear in SUPPORTING_DOCUMENT section of QA report |

See also: `STYLE_RULES.md` Rule S5 (Known Failure Case), `PATCH_FACT_CHECK.md` PFC-11, PFC-12.

---

## Cross-Reference

- `SKILL.md` Rule 25 (Template Source Rule), Rule 26 (Final Reference Output Rule)
- `TEMPLATE_RULES.md` — Full template usage rules
- `QA_CHECKLIST.md` Gate I (Reference Output Regression Gate)
- `STYLE_RULES.md` Rule S1 (Template Clone), S5 (All @@ blue+bold), S8 (Render QA)
- `PATCH_FACT_CHECK.md` PFC-11 (No Git Metadata), PFC-12 (Logic-Bearing Example)
