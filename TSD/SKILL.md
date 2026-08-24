# TSD Skill

## Purpose

Generate Technical Specification (TSD) documents from patch / diff input.

This skill ensures:

- Patch-based generation
- No hallucinated code / class / method
- Section-to-code traceability
- Consistent structure across projects
- Validator-compatible output

---

## Core Principle

```text
Section → Change Description → Patch Hunk → File → Method → Evidence → Example
```

---

## Validator Alignment

This skill is designed to produce output that passes `validate_tsd_vs_patch.py`.

Validator verdict semantics:

| Verdict | Meaning | Delivery |
|---|---|---|
| ACCEPT | All checks pass | Allowed |
| ACCEPT_WITH_WARNINGS | Warnings only (aggregator / method / evidence) | Allowed with review |
| REGENERATE_REQUIRED | One or more FAIL | **Blocked** (exit code 2) |

Validator performs **6 checks** per example:

| # | Check | Trigger | Verdict |
|---|---|---|---|
| V1 | File exists in patch | File path not found in patch index | **FAIL** |
| V2 | Diff content matches patch | Fewer than 3 of 5 sampled added/removed lines in patch | **FAIL** |
| V3 | No forbidden import contamination | Diff contains forbidden cross-scope import | **FAIL** |
| V4 | Aggregator restriction | File is Service/Controller/Manager in non-orchestration section, not whitelisted | WARN |
| V5 | Method exists in patch | Primary Java method from TSD `+` lines not found in patch block | WARN |
| V6 | Evidence lines confirmed | Fewer than 3 of up to 8 TSD added lines found in patch block | WARN |

---

## Mandatory Rules

### Rule 1 — Patch-Bound Rule (CRITICAL)

All TSD content MUST be derived from the patch.

**FAIL if:**

- File path is not found in the patch file index
- Diff hunk is fabricated or reconstructed without referencing actual patch content

Do NOT:

- Invent file / class / method
- Use similar but unrelated code
- Reconstruct a diff without referencing actual patch content

---

### Rule 2 — Source Mapping Rule

Each representative example MUST include source mapping:

```text
Source:
  file: <filename>
  method: <method name or N/A>
  patch: <patch id or file key>
```

Evidence MUST be included:

```text
Evidence:
  + <key added line from diff>
  + <key added line from diff>
```

---

### Rule 3 — Scope Isolation Rule (CRITICAL)

Each section MUST represent one logical scope only.

Each section has a defined set of `forbidden_import_patterns`. If a diff contains an import matching a forbidden pattern for that section, it is **cross-scope contamination**.

**FAIL if** diff content contains an import matching a forbidden pattern for that section.

Do NOT:

- Mix unrelated domains in the same section
- Use files that import out-of-scope dependencies as examples
- Place cross-domain integration files in single-domain sections

Exception: Section is explicitly about integration (declared as `aggregator_ok=True` in SECTION_DEFS) — this must be noted in TSD.

---

### Rule 4 — Aggregator Restriction Rule

Aggregator-typed files are restricted by default.

**Aggregator suffixes detected by validator:**

```text
Service.java
Controller.java
Manager.java
```

**WARN if** an aggregator file is used in a non-orchestration section and is not whitelisted.

**Allowed without warning:**

1. Section explicitly describes orchestration, API layer, or integration (`aggregator_ok=True`)
2. File is in `AGGREGATOR_WHITELIST` (thin wrappers declared acceptable)

**AGGREGATOR_WHITELIST** (current):

```text
FxCurrencyService.java  — thin JPA wrapper, acceptable in data-layer section
```

Prefer for non-orchestration sections:

```text
BO / DTO / Model / Config / Properties
Request / Response object
Repository / DAO (for data-layer sections)
```

---

### Rule 5 — Example Count Rule

Default:

```text
Each section MUST have 2 representative examples.
```

If fewer than 2 valid patch-traceable examples exist:

```text
Mark missing example as: [TO BE CONFIRMED]
List what evidence is missing.
```

Do NOT generate a placeholder example without marking it as unverified.

---

### Rule 6 — Fallback Rule

If no valid patch evidence exists for an example, do NOT generate content.

Use:

```text
[TO BE CONFIRMED]
Missing evidence: <describe what file or method is needed>
```

---

### Rule 7 — Diff Content Authenticity Rule (CRITICAL)

The diff snippet placed in the TSD example table MUST be taken verbatim from the patch.

Validator check (`_diff_content_matches_patch`): samples up to 5 added/removed lines from the TSD diff and requires at least 3 of them to appear in the actual patch block.

**FAIL if** fewer than 3 of 5 sampled added/removed lines from the TSD diff are found in the actual patch block.

Requirements:

- Diff hunk MUST begin with a real `diff --git a/<path> b/<path>` header
- Hunk content MUST contain actual `+` / `-` lines from the patch file
- Hunk lines MUST appear verbatim in the same file's patch block

Do NOT:

- Paraphrase or reconstruct diff lines
- Truncate diff content in a way that removes all verifiable lines
- Change `+`/`-` prefixes or alter code content

---

### Rule 8 — Evidence Confirmation Rule (CRITICAL)

Each example MUST have at least 3 verifiable evidence lines confirmed in the patch.

**Evidence extraction (how validator samples):**

- Scans all `+` lines in TSD diff (excludes `+++`)
- Skips trivial lines: empty, `{`, `}`, lines starting with `//`
- Collects up to **8** evidence lines

**Evidence check threshold:**

```text
hits >= min(3, total_evidence_lines)
```

Where `hits` = number of evidence lines that appear verbatim in the patch block.

**WARN if** threshold is not met.

Implication for generation: Include meaningful added lines (`import` statements, method signatures, key assignments) — not just braces or comments.

---

### Rule 9 — Method-Level Rule (CRITICAL)

If the diff shows a Java method change, the method name MUST be included in Source mapping.

**How validator extracts method (generator MUST match this behavior):**

- Scans `+` lines (not `+++`) in diff content sequentially
- Applies regex: `(?:public|protected|private)\s+(?:static\s+)?[\w<>\[\],\s]+\s+(\w+)\s*\(`
- Takes the **first** matching group (method name)
- Returns `N/A` if no method signature found in `+` lines

**WARN if** the declared method name is not found anywhere in the patch block for that file.

Do NOT:

- Invent method names
- Guess method names from class context or similar classes
- Use method names from a different file

---

### Rule 10 — No Cross-Section Reuse Rule (CRITICAL)

Each section MUST have independent examples.

Do NOT:

- Reuse same file path across unrelated sections (detected by scope contamination check)
- Copy-paste example table content between sections
- Use the same diff block under two different section headings

Exception: Allowed only when explicitly documenting a shared pattern — must be annotated in TSD.

---

### Rule 11 — Patch Coverage Awareness Rule

Generator SHOULD:

- Be aware of the full patch file list
- Ensure representative examples cover key files listed in section `major_files`

BUT:

- NOT all patch files need individual representative examples (examples represent the group pattern)
- Batch / infra changes can be abstracted under section description
- Validator coverage check is **informational only** (no FAIL from uncovered major files)

**CRITICAL distinction:**

- Examples: 1–2 per section, representative of the group pattern
- File listing (appendix): ALL IN_SCOPE files must appear individually — no exceptions

See Rule 16 for file-level completeness enforcement.

---

### Rule 12 — Aggregator Exception Rule

Aggregator classes (Service / Controller / Manager) are:

```text
DEFAULT: NOT allowed without justification
```

Allowed when ANY of:

1. Section describes orchestration explicitly
2. Section describes API layer
3. Section describes integration utility
4. File is in AGGREGATOR_WHITELIST

When using an aggregator in a non-standard context, note in TSD:

```text
Note: <FileName> is used as example here because <reason>. This is a declared exception.
```

---

### Rule 13 — Validation Gating Rule (CRITICAL)

Generated TSD MUST be validated before delivery.

Validation command:

```bash
python validate_tsd_vs_patch.py \
  --patch <patch-file> \
  --docx  <generated-docx> \
  --out   <report-path>
```

**Exit codes:**

```text
exit 0  → ACCEPT or ACCEPT_WITH_WARNINGS → may deliver
exit 2  → REGENERATE_REQUIRED → MUST fix before delivery (delivery blocked)
```

Generator output is **not deliverable** until validator returns `ACCEPT` or `ACCEPT_WITH_WARNINGS`.

---

### Rule 14 — Section Heading Detection Rule

Validator detects section context by heading text pattern.

**Default pattern (PRJ-A project):**

```text
^(3\.[1-6])(?:\.\d+)?\s
```

Detects headings `3.1` through `3.6`. Sub-section headings like `3.1.3` are mapped to the parent section `3.1`.

Example tables are detected by cell[0] matching:

```text
代表範例\s*(\d+)
```

**If a project uses different section numbers** (e.g., EOS 3.1–3.26), the validator's `extract_tsd_examples()` regex and `SECTION_DEFS` must be updated before running.

TSD section headings MUST use the format:

```text
3.X  <section name>
```

---

### Rule 15 — Project Scope First Rule (CRITICAL)

TSD generation MUST prioritize the declared project / requirement scope.

Patch files may contain changes from multiple concurrent features, hotfixes, or projects mixed into a single commit. TSD MUST NOT blindly document all changes — it MUST document only the changes that belong to the current requirement.

**Classification tiers for each patch file:**

| Classification | Meaning | TSD Handling |
|---|---|---|
| `IN_SCOPE` | Clearly belongs to current project requirement | Include in TSD |
| `SUPPORTING_SCOPE` | Shared utility / config directly referenced by current requirement | Include if directly referenced |
| `OUT_OF_SCOPE_REVIEW` | Change belongs to a different project / feature | Exclude from TSD; write to Scope Review Report |
| `NO_TSD_IMPACT` | Build artifact / test file / infra / binary only | No TSD entry required |
| `UNKNOWN_REQUIRES_CONFIRMATION` | Cannot determine scope from patch alone | Flag for human review; WARN |

**Enforcement rules:**

- Main TSD MUST NOT include `OUT_OF_SCOPE_REVIEW` items automatically.
- Validator MUST NOT FAIL main TSD solely due to `OUT_OF_SCOPE_REVIEW` patch content.
- `OUT_OF_SCOPE_REVIEW` changes MUST be written to a separate **Scope Review Report**.
- Human confirmation is required before any `OUT_OF_SCOPE_REVIEW` item enters main TSD.
- `UNKNOWN_REQUIRES_CONFIRMATION` → WARN in validator, listed in Scope Review Report.

**Content-level scope check (V7):**

Even when a file is `IN_SCOPE` or `SUPPORTING_SCOPE`, its example diff content may contain additions from a different domain (e.g., a shared constants file that receives both FX and KYC constants in the same commit). The validator checks for such out-of-scope content patterns (V7) and raises a WARN + flags them in the Scope Review Report.

The main TSD example MUST be limited to in-scope lines. Cross-domain constants / methods in the example diff must be either:
- Excluded from the example snippet, OR
- Moved to the Scope Review Report with a recommendation for human review.

---

### Rule 16 — File-Level Appendix Completeness Rule (CRITICAL)

Every IN_SCOPE or directly-referenced SUPPORTING_SCOPE file from the patch MUST appear in the appendix.

**FAIL if** a file classified as IN_SCOPE is missing from the authoritative change-program
enumeration (the appendix complete file list). See `core/CHANGE_PROGRAM_CONTRACT.md` Layer 3.

Requirements:

- Grouped sections MUST still list every individual file path
- "Grouped" does NOT mean files disappear — it means they share a section heading
- Section description SHOULD state "this section covers N files with pattern X"

Do NOT:

- List only representative files and omit the rest
- Summarize a group as "various files" without individual paths in the appendix
- Treat grouping as a license to drop file-level traceability

---

### Rule 17 — Section Grouping Rule

Updated: 2026-05-12 (Dual-mode grouping system added)

**Dual-mode grouping system:** See `core/GROUPING_MODE_RULES.md` for the complete Mode A / Mode B decision tree, decision conditions, examples, and rationale templates.

Two modes are valid — **neither is the only correct approach**:

- **Mode A (Business Function Grouping):** Files grouped by shared business purpose / end-to-end flow.
  Controller + Service + DTO + Config may share one section when they all support one identified flow.
  Preferred for large requirements (e.g., PRJ-A FX exchange flow).

- **Mode B (Code Change Pattern Grouping):** Files grouped by code change pattern type.
  Preferred for small projects, targeted fixes, and technical migrations.
  See `core/SECTION_GROUPING_RULES.md` for the full 10-step Mode B decision flow.

The grouping mode MUST be declared in the rationale report (not in the TSD main body).

**The merge/split guidelines below apply primarily to Mode B.** For Mode A, see `core/GROUPING_MODE_RULES.md`.

**Guidelines for Mode B (see full detail in SECTION_GROUPING_RULES.md and CLASSIFICATION_RULES.md):**

**Merge when** (all of the following within the group):
1. Same Code Change Pattern (primary grouping key in Mode B)
2. Same business intent (same feature / flow step) — used as secondary merge confirmation
3. Same technical change pattern (same migration type)
4. Same external system target (same connector / API)
5. Same persistence layer set (Entity + Repository + Service for one feature)

**Do NOT merge when (Mode B):**
1. Different Code Change Patterns (primary rule)
2. Different business flows or domain sub-systems
3. Different external system targets
4. Technical migration mixed with business logic changes
5. Section description would be too vague to support representative examples

**Technical migration grouping:**

`javax→jakarta`, `Swagger 2→OpenAPI 3`, `DTO annotation migration`, `CXF connector set`, `Axis→CXF` — each migration TYPE should be ONE section, not one section per file.

**New object grouping (Mode A or Mode B):**

New classes forming a layer set for one feature (Controller + Service + BO + Request + Response + Entity + Repository) MAY be one section under Mode A. ALL files must still be listed individually in the appendix regardless of mode.

---

## Section Structure Template

**Authoritative subsection structure:** the current 3.x subsection structure
(`3.x.1 / 3.x.2 / 3.x.3`) is governed by `core/OUTPUT_SPEC.md` and
`core/TEMPLATE_MODE_RULES.md`, with `3.x.2` and the appendix change-program list governed by
`core/CHANGE_PROGRAM_CONTRACT.md`. The list below is the historical content checklist for what a
section must convey; it does not define an alternative subsection architecture.

```text
1. Section title       — format: "3.X  <section name>"
2. Change description  — what changed and why
3. Technical impact    — affected layers / components
4. Representative examples (minimum 2, or [TO BE CONFIRMED])
5. Source mapping      — file, method, patch reference
6. Evidence lines      — verbatim + lines from actual patch hunk
```

---

## Output Requirements

Generated TSD MUST:

- Preserve base document structure
- Preserve heading and table style
- Update only affected sections
- Include revision history
- Avoid unrelated changes
- Avoid speculative business logic

---

## FAIL vs WARN Reference Table

| Check | Trigger | Verdict | Delivery Impact |
|---|---|---|---|
| V1 File not in patch | File path absent from patch index | **FAIL** | Blocked (exit 2) |
| V2 Diff content mismatch | <3 of 5 sampled lines match patch | **FAIL** | Blocked (exit 2) |
| V3 Forbidden import | Diff imports out-of-scope dependency | **FAIL** | Blocked (exit 2) |
| V4 Aggregator misuse | Service/Controller/Manager, not whitelisted | WARN | Review required |
| V5 Method not in patch | Declared method not found in patch text | WARN | Review required |
| V6 Evidence not confirmed | <3 of 8 evidence lines in patch block | WARN | Review required |
| V7 Out-of-scope content | Example diff contains cross-domain code/constants | WARN | Review + Scope Report |
| R16 File missing from appendix | IN_SCOPE file absent from the authoritative change-program enumeration (appendix) | **FAIL** | Completeness blocked |

---

## Anti-Patterns

Strictly forbidden:

- Cross-domain example reuse
- Patch-unrelated examples
- Missing source mapping
- Hallucinated class / method / diff lines
- Aggregator class used without declaration
- Same example reused across unrelated sections
- Paraphrased or reconstructed diff content
- Empty evidence lines (braces / comments only)
- Invented method names in Source mapping
- Including OUT_OF_SCOPE_REVIEW changes in main TSD without human confirmation
- Expanding section description to cover unrelated domain logic just because it appears in a shared file's diff

---

## Design Philosophy

```text
Correctness > Completeness
Traceability > Readability
Deterministic output > Creative generation
Patch evidence > Assumption
Validator ACCEPT > Human judgment alone
```

---

## Cross-Project Reusability

This skill is universal. Per-project configuration is isolated in the validator:

| Item | Where to configure |
|---|---|
| Section definitions (scope keywords, forbidden patterns, aggregator_ok) | `SECTION_DEFS` in `validate_tsd_vs_patch.py` |
| AGGREGATOR_WHITELIST | `validate_tsd_vs_patch.py` |
| Section heading detection range | regex in `extract_tsd_examples()` |
| Major files per section | `SECTION_DEFS[sec].major_files` |
| Out-of-scope file path patterns (Rule 15) | `OUT_OF_SCOPE_FILE_PATTERNS` in `validate_tsd_vs_patch.py` |
| Out-of-scope content patterns per section (Rule 15 / V7) | `EXAMPLE_CONTENT_OOS_PATTERNS` in `validate_tsd_vs_patch.py` |

Rules 1–15 are universal and apply to all projects using this skill.

---

## Final TSD Generation Principles (PRJ-A Final — 2026-05-05)

The following principles were formalised after the PRJ-A TSD multi-round correction process. They are now mandatory for all future TSD generations.

### Reference Files (Mandatory)

| Item | Locator / Binding | Role |
|---|---|---|
| **Template Source** | `assets/base_template.docx` | Word template clone base — REQUIRED for all TSD generation |
| **Gate I reference artifact** | `<REF_FINAL>` | Operator-bound V7 regression baseline — behavior comparison only |

**Bindings (the concrete reference locator remains local input):**
```
Template Source:
  `<OPERATOR_BASE_TEMPLATE_INPUT>`

Gate I reference artifact:
  `<REF_FINAL>` (symbolic token; bind to a concrete local artifact for each run)
```

**Critical role distinction:**
- `assets/base_template.docx` = clone base. New TSD MUST `Document(this_file)`. Never from blank.
- `<REF_FINAL>` = symbolic binding for the exact operator-supplied local regression artifact. It is not a literal filename, Git-owned artifact, or bundled runtime asset. Gate I2 MUST NOT PASS until it is bound; compare behavior against the bound artifact, do NOT clone it, and do NOT copy PRJ-A-specific content to other projects.

### Principle 1 — Template Clone Only

**TSD MUST be generated by cloning the designated template docx. It MUST NOT be built from a blank Document().**

- The template must be opened with `python-docx Document(template_path)`.
- Only the content sections listed in the per-project scope may be replaced.
- The following elements MUST be preserved from the template and MUST NOT be rebuilt:
  - Cover page
  - Table of Contents (TOC)
  - Header / Footer
  - Section 4–11 (or equivalent non-change-detail sections)
  - Page layout, margins, column settings
  - Bookmarks: `MAIN_SECTION_1..N`, `APPENDIX_SECTION_1..N`

**FAIL if** a blank `Document()` is used as the base.

---

### Rule 18 — Template Clone Rule (CRITICAL)

```text
DO:     doc = Document(template_docx_path)
DO NOT: doc = Document()
```

Substitution scope (the ONLY parts that may change):

| Section | May Replace |
|---|---|
| 3.x headings | Title text only |
| 3.x.1 body paragraphs | Full replacement |
| 3.x.2 programme list | Full replacement; content per `core/CHANGE_PROGRAM_CONTRACT.md` |
| 3.x.3 diff tables | Diff cell content only |
| Appendix section headings | Title text only |
| Appendix file bullet list | Full replacement |

**Strictly MUST NOT change:**
- Section 1, 2, 4–11 body content
- Cover page fields
- TOC structure (use `update_toc_entry()` only)
- Header / footer XML
- Page margins / section properties

---

### Rule 19 — 0421 Writing Style Rule

All 3.x.1 異動說明 MUST follow the 0421 three-part structure:

```
Part 1:  What this section is — one sentence, understandable to a non-technical reviewer.
Part 2:  What changed and its impact — concise, patch-based.
Part 3:  What other files / patterns are covered — "同類型異動主要涵蓋…".
```

**Forbidden in 3.x.1 body text:**

```
Code Change Type:
Shared Purpose:
Same-Pattern Merge
Do Not Over-Merge
File-Level Completeness
Section Grouping Rationale:
```

These belong in the rationale report, NOT the TSD main body.

**Style target:** EOS_v28_0421 — short, reader-friendly, business-readable. Not an engineering analysis.

---

### Rule 20 — Diff Run-Level Color Rule (CRITICAL)

All diff content in 3.x.3 tables MUST use **run-level** (per `<w:r>`) colour formatting.

| Line type | Colour (`w:color val`) | Bold |
|-----------|----------------------|------|
| `+` added line | `1F7A1F` (green) | No |
| `-` deleted line | `B00020` (red) | No |
| `@@` hunk header | `2F5597` (blue) | **Yes** (`<w:b/>` + `<w:bCs/>`) |
| Context / header | `333333` (dark grey) | No |

**Critical detail — all `@@` hunks MUST be coloured:**

A diff cell may contain multiple `@@` hunk headers. ALL paragraphs whose text starts with `@@` MUST be individually coloured blue + bold.

**FAIL if:** Any `@@` paragraph in any diff cell uses grey (`333333`) or lacks `<w:b/>`.

Font: Courier New (`w:rFonts ascii="Courier New" hAnsi="Courier New"`), `sz val="16"` (8pt).

Anti-pattern (forbidden):
```python
# WRONG — only fixes first @@
first_hunk = cell.paragraphs[hunk_idx]
apply_blue_bold(first_hunk)
# subsequent hunks LEFT UNSTYLED
```

Correct approach:
```python
for p in cell._tc.findall(W + "p"):
    txt = "".join(t.text or "" for t in p.findall(".//" + W + "t"))
    if txt.startswith("@@"):
        apply_blue_bold_to_all_runs(p)
```

---

### Rule 21 — No Pseudo Diff / No Ellipsis / No Git Metadata in Diff Table (CRITICAL)

Diff table cells MUST contain ONLY verbatim patch hunk content lines.

**Allowed in diff table cells:**
```
@@ -N,N +N,N @@
+ <added line from patch>
- <deleted line from patch>
  <context line from patch>
--- a/<path>     (only if the hunk file header appears in excerpt)
+++ b/<path>     (only if the hunk file header appears in excerpt)
```

**Strictly FORBIDDEN in diff table cells:**
```
diff --git a/... b/...          ← git metadata — FORBIDDEN
new file mode NNNNNN            ← git metadata — FORBIDDEN
deleted file mode NNNNNN        ← git metadata — FORBIDDEN
index xxxxxxx..xxxxxxx          ← git metadata — FORBIDDEN
similarity index ...%           ← git metadata — FORBIDDEN
rename from ...                 ← git metadata — FORBIDDEN
rename to ...                   ← git metadata — FORBIDDEN
...                             ← truncation marker — FORBIDDEN
(pseudo code)                   ← not from patch — FORBIDDEN
(reconstructed / paraphrased diffs) ← not from patch — FORBIDDEN
(invented method names)             ← not from patch — FORBIDDEN
(class stubs not from patch)        ← not from patch — FORBIDDEN
```

**FAIL if** any `diff --git`, `new file mode`, `deleted file mode`, `index`, `similarity index`, `rename from`, or `rename to` line appears inside a diff table cell. See PFC-11.

**If the diff is a partial excerpt**, the truncation note MUST appear OUTSIDE the table — as a paragraph before the table:

```
以下為 patch 原文節錄。
```

**If the diff is a partial excerpt**, the truncation note MUST appear OUTSIDE the table — as a paragraph before the table:

```
以下為 patch 原文節錄。
```

**FAIL if** any paragraph inside a diff cell contains text that is exactly `"..."` or is not traceable to the patch.

---

### Rule 22 — Appendix Hyperlink / Bookmark Rule (CRITICAL)

The main body and appendix MUST be cross-linked via Word bookmarks.

**Required bookmarks:**

| Bookmark name | Location |
|---|---|
| `MAIN_SECTION_1` … `MAIN_SECTION_N` | At each 3.x section heading |
| `APPENDIX_SECTION_1` … `APPENDIX_SECTION_N` | At each appendix sub-section heading |

**Required hyperlinks:**

| Location | Target |
|---|---|
| 3.x.2 "查看附錄完整清單（n 個檔案）" | `APPENDIX_SECTION_N` |
| Appendix "回到主文第 N 項" | `MAIN_SECTION_N` |

**FAIL if:**
- An appendix back-link is missing or broken
- Bookmarks are absent or misnamed

3.x.2 content form (whether it is a hyperlink paragraph or per-file entries) is owned by
`core/CHANGE_PROGRAM_CONTRACT.md` and verified by `QA_CHECKLIST.md` Gate D5, not by this rule.
The bookmark and hyperlink tables above remain authoritative here.

---

### Rule 23 — Supporting Document Exclusion Rule

README files and documentation-type files MAY be excluded from the TSD appendix's programme change list.

However, they MUST NOT disappear. They MUST appear in the QA report under:

```
Classification: SUPPORTING_DOCUMENT / OUT_OF_SCOPE
```

**Coverage equation MUST hold:**

```
patch changed files
  = TSD appendix files (IN_SCOPE)
  + SUPPORTING_DOCUMENT / OUT_OF_SCOPE files
  + UNKNOWN files
```

If the equation does not balance, QA FAILS.

**Canonical example (PRJ-A project):**

```
File:   mobileapp/.../ECERTRestClient_README.md
Status: SUPPORTING_DOCUMENT / OUT_OF_SCOPE
Reason: README documentation file; not a programme change; excluded from TSD appendix
        but listed in QA report SUPPORTING_DOCUMENT section.
```

Coverage result: 91 patch files = 90 appendix + 1 SUPPORTING_DOCUMENT + 0 UNKNOWN + 0 missing.

---

### Rule 24 — QA Gate Rule

No TSD version may be declared final until ALL of the following gates pass.
See `QA_CHECKLIST.md` for full gate definitions.

| Gate | Description | Blocks delivery |
|---|---|---|
| A — File Coverage | All patch files accounted for | Yes |
| B — Section Grouping | Each section has grouping rationale | Yes |
| C — Word Style | Template cloned, styles preserved | Yes |
| D — Hyperlink | Bookmarks and cross-links valid | Yes |
| E — Diff Color | All `@@` blue+bold; `+` green; `-` red | Yes |
| F — Patch Fact | No pseudo diff; no `...`; no git metadata in diff cell; logic-bearing example | Yes |
| G — Render | Visual QA: no overflow, no clipping | Yes |
| H — Code Change Pattern Grouping | Each section has declared Code Change Pattern as primary key | Yes |
| I — Reference Output Regression | Template source = assets/base_template.docx; v7 is baseline not clone base | Yes |
| J — Representative Example Priority | Controller-first → Service → Object method → snippet; no metadata/ellipsis | Yes |

---

### Rule 25 — Template Source Rule (CRITICAL)

The designated Word template source MUST be used for all TSD generation.

**Template Source:**

```
File: assets/base_template.docx
Path: `<OPERATOR_BASE_TEMPLATE_INPUT>`
Skill asset alias: assets/base_template.docx
```

**The template provides:**
- Cover page and document metadata table
- Table of Contents (TOC) with PAGEREF fields
- Header and Footer
- Section 1 Preamble, Section 2 System Overview
- Section 4 System Interfaces through Section 11 Document Approvals
- Appendix style
- All `pStyle` definitions (v24heading1, v24heading2, v24ListBullet, etc.)
- Hyperlink / bookmark structure
- Diff example table style

**FAIL if any of the following occur:**
- `doc = Document()` is used as TSD base
- Template is not opened from `assets/base_template.docx`
- Header / Footer is manually reconstructed
- TOC structure is rebuilt from scratch
- Section 4–11 is deleted or manually rewritten
- Appendix style is rebuilt instead of cloned from template

See `TEMPLATE_RULES.md` for full detail.

---

### Rule 26 — Final Reference Output Rule (CRITICAL)

The Final Reference Output provides the behavior baseline for regression comparison.

**Gate I reference artifact:**

```
Binding token: `<REF_FINAL>`
Concrete locator: operator-supplied local input for this run; not stored in Git
Role: V7 Final Reference Output / Template-B Regression Baseline
```

**Use for comparison of:**
- 3.x.1 three-part 0421 writing style
- 3.x.2 `placement = appendix_only` behaviour (Template B baseline only)
- Full appendix file list behavior
- README / SUPPORTING_DOCUMENT exclusion handling
- diff example logic-bearing snippet behavior
- diff example no git metadata / no ellipsis
- All `+` lines green, all `-` lines red, all `@@` blue + bold
- All `@@` hunk headers checked (not only the first)
- Hyperlink / bookmark between main body and appendix
- Output render without broken layout

**STRICTLY FORBIDDEN:**
- Using the artifact bound to `<REF_FINAL>` as clone template source
- Treating `<REF_FINAL>` as a literal path or comparing the token to itself
- Copying PRJ-A-specific content (project name, file paths, amounts) into unrelated TSD
- Treating v7 final output as the canonical template

See `REFERENCE_OUTPUTS.md` for full detail.

---

### Rule 27 — Representative Example Priority Rule (CRITICAL)

Updated: 2026-05-12

The 3.x.3 representative example MUST be selected following a fixed file-role priority order. Selection is NOT driven solely by the section title or code change pattern name — it is driven by the roles of files present in the section.

**Priority order:**

| Priority | Candidate Source | Condition |
|---|---|---|
| 1 | **Controller** first added/changed method | Section contains a Controller class |
| 2 | **Service** first added/changed method | No Controller candidate available |
| 3 | **Object class** (DTO/BO/Request/Response/Entity/VO/Model) first added/changed method | No Controller/Service candidate available |
| 4 | **Object class code snippet** (field/enum/constant/repo method decl) | No object class method candidate |
| 5 | **Other logic-bearing hunk** (config/constants/client/DAO) | All above unavailable — mark WEAK in QA |

**Fallback flow (Step 1–7):** See `core/EXAMPLE_SELECTION_RULES.md` for the complete step-by-step fallback flow and method detection signals.

**Mode A (Business Function Grouping):** Controller-first priority still applies even when a section contains mixed code patterns.

**Mode B (Code Change Pattern Grouping):** Example source must match the section's declared pattern (controller-api → Controller method; service-business-logic → Service method; dto-bo → Object method/field).

**Prohibited example sources:**
- git metadata (`diff --git`, `new file mode`, `deleted file mode`, `index`)
- Ellipsis (`...`)
- Package/import-only block
- Class declaration only
- Annotation-only block
- Getter/setter-only
- Pseudo diff (content not in patch)

**QA gate:** Gate J — Representative Example Priority Gate (see `QA_CHECKLIST.md`).

**Full rules:** See `core/EXAMPLE_SELECTION_RULES.md`.

---

## Three-Template TSD Architecture

Updated: 2026-05-14 (三模板 TSD 架構 Step 1)

TSD supports three template modes. The mode MUST be declared before content is generated.

### Rule 28 — Template Mode Declaration Rule (CRITICAL)

Every TSD generation MUST declare a template mode at the start of QA report and rationale report:

```
Template Mode: A / B / C
```

**FAIL if** template mode is not declared in QA report.

See `core/TEMPLATE_MODE_RULES.md` for complete definitions.

---

### Template A — Large Migration Template / 大型技術遷移模板

Use when:
- Files are very numerous or change pattern is highly repetitive
- Technical migration / framework upgrade / dependency migration
- EOS-class projects

Section structure: `3.x.1` describes common pattern; `3.x.2` `placement = appendix_only`; `3.x.3` best-representative hunk.
Appendix and 3.x.2 content: `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 (Template A row).

---

### Template B — Medium Feature Template / 中量功能異動模板

Use when:
- Medium volume files (10–100), cross-layer functional changes
- Reviewer needs section grouping + representative examples
- Per-file individual detail is NOT required

**Template B is the recommended default for PRJ-A-class projects.**

Section structure: `3.x.1` 15–30 char section summary; `3.x.2` `placement = appendix_only`; `3.x.3` representative method (Controller → Service → Object priority).
Appendix and 3.x.2 content: `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 (Template B row).

---

### Template C — Small Per-File Template / 小型逐檔異動模板

Use when:
- Small file set (3–20 files)
- Reviewer needs per-file detail
- Each file needs 15–30 char summary and code block

Section structure: `3.x.1` section summary; `3.x.2` `placement = inline_per_file`; `3.x.3` per-file code blocks.
Appendix and 3.x.2 content: `core/CHANGE_PROGRAM_CONTRACT.md` Layer 4 (Template C row).

---

### v19 Positioning

`PRJ-A_20260505_v19_SHORT_CHANGE_SUMMARY.docx` (per-file appendix summaries) is positioned as:

```
Template C prototype / B+C Hybrid demonstration
```

- Template B does NOT default to per-file summaries
- Template C defaults to per-file 15–30 char summaries
- v19 applied Template C summary style to a PRJ-A medium project → B+C Hybrid (demonstration only)

See `core/TEMPLATE_MODE_RULES.md` and `core/TEMPLATE_MODE_OUTPUT_SPEC.md` for full detail.

---

### Template Mode QA Gate

Gate K (Template Mode Gate) is added to the QA checklist. See `core/QA_CHECKLIST.md` Gate K.

| Gate | Description | Blocks delivery |
|---|---|---|
| K — Template Mode | Mode declared; mode matches project scale; correct mode behaviour applied | Yes |
