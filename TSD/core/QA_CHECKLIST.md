# TSD QA Checklist

## Purpose

This checklist defines the 11 mandatory gates that every TSD version must pass before delivery. No version may be declared final with any gate FAIL.

Updated: 2026-05-11 (新增 Gate H: Code Change Pattern Grouping Gate)
Updated: 2026-05-11 (新增 Gate I: Reference Output Regression Gate)
Updated: 2026-05-12 (新增 Gate J: Representative Example Priority Gate)
Updated: 2026-05-14 (新增 Gate K: Template Mode Gate)

---

## Summary Gate Table

| Gate | Name | Blocks delivery |
|---|---|---|
| A | File Coverage | Yes |
| B | Section Grouping | Yes |
| C | Word Style | Yes |
| D | Hyperlink & Bookmark | Yes |
| E | Diff Colour | Yes |
| F | Patch Fact | Yes |
| G | Render / Visual QA | Yes |
| H | Code Change Pattern Grouping | Yes |
| I | Reference Output Regression | Yes |
| J | Representative Example Priority | Yes |
| K | Template Mode Gate | Yes |

All 11 gates must be PASS for delivery to proceed.

---

## Gate A — File Coverage

**Purpose:** Verify every patch-changed file is accounted for.

**Checks:**

| Check | Pass Condition |
|---|---|
| A1 — All IN_SCOPE files in appendix | Every IN_SCOPE file from patch appears in TSD appendix |
| A2 — Coverage equation balances | `patch files = appendix + SUPPORTING_DOCUMENT + OUT_OF_SCOPE + UNKNOWN; missing = 0` |
| A3 — SUPPORTING_DOCUMENT in QA report | README / docs files listed in QA report, not in TSD appendix |
| A4 — No phantom files | No file in TSD appendix that is NOT in the patch |

**FAIL trigger:** missing > 0 or phantom file found.

**Coverage equation (PRJ-A example):**
```
91 patch files = 90 appendix (IN_SCOPE) + 1 SUPPORTING_DOCUMENT + 0 UNKNOWN
missing = 0  →  PASS
```

---

## Gate B — Section Grouping

**Purpose:** Verify each section follows the grouping decision flow.

**Checks:**

| Check | Pass Condition |
|---|---|
| B1 — No single-file over-enumeration | Files sharing the same pattern are grouped, not individually sectioned |
| B2 — No over-merge | Different business flows are not merged into one vague section |
| B3 — Section title readable | Section title is business-readable, no engineering analysis labels |
| B4 — 3.x.1 format correct | Body text uses three-part 0421 format; no `Code Change Type:` labels |
| B5 — Representative examples present | 1–2 traceable examples per section (or `[TO BE CONFIRMED]` with reason) |

**FAIL trigger:** Engineering jargon labels in TSD main body; over-merge creating untraceable sections; missing examples without `[TO BE CONFIRMED]`.

---

## Gate C — Word Style

**Purpose:** Verify the template was cloned and styles are preserved.

**Checks:**

| Check | Pass Condition |
|---|---|
| C1 — Template clone confirmed | `doc = Document(template_path)`; NOT `Document()` |
| C2 — Cover page intact | Cover page fields present and unmodified |
| C3 — TOC structure intact | TOC `<w:hyperlink>` structures preserved; PAGEREF fields intact |
| C4 — Header/footer intact | Header/footer XML unchanged |
| C5 — Section 4–11 intact | Non-change sections unmodified |
| C6 — Appendix pStyle correct | `v24ListBullet` used for file bullets; `proto_bullet` from `appendix_title_idx + 2` |

**FAIL trigger:** Blank `Document()` base; TOC structure broken; `v24ListBullet` missing.

---

## Gate D — Hyperlink & Bookmark

**Purpose:** Verify all cross-links between main body and appendix are functional.

**Checks:**

| Check | Pass Condition |
|---|---|
| D1 — MAIN_SECTION_N bookmarks present | One bookmark per TSD section at 3.x heading |
| D2 — APPENDIX_SECTION_N bookmarks present | One bookmark per appendix sub-item heading |
| D3 — 3.x.2 appendix hyperlink valid (`placement = appendix_only` only) | Every 3.x.2 paragraph is a hyperlink pointing to correct `APPENDIX_SECTION_N` |
| D4 — Appendix back-links valid (all modes) | Every appendix sub-item has「回到主文第 N 項」hyperlink pointing to `MAIN_SECTION_N` |
| D5 — 3.x.2 content matches declared placement | `placement = appendix_only`: 3.x.2 contains ONLY the hyperlink paragraph, not a file list. `placement = inline_per_file`: 3.x.2 contains the per-file entries required for that placement, and D3 does not apply. |

**Placement source:** the declared template mode maps to a `placement` value in
`core/CHANGE_PROGRAM_CONTRACT.md` Layer 4. That contract owns the semantics of each
placement; Gate D owns the check.

**FAIL trigger:** Missing bookmarks; broken anchors; 3.x.2 content that does not match the
declared placement (for `appendix_only`, a plain-text file list or a hyperlink-less count;
for `inline_per_file`, missing per-file entries).

---

## Gate E — Diff Colour

**Purpose:** Verify all diff cell content is correctly colour-coded at the run level.

**Checks:**

| Check | Pass Condition |
|---|---|
| E1 — `+` lines green | All added lines (`+` prefix) use `<w:color val="1F7A1F">` |
| E2 — `-` lines red | All deleted lines (`-` prefix) use `<w:color val="B00020">` |
| E3 — ALL `@@` lines blue + bold | Every `@@`-starting paragraph uses `<w:color val="2F5597">` + `<w:b/>` + `<w:bCs/>` |
| E4 — No grey `@@` | No `@@` paragraph uses context colour `333333` or lacks bold |
| E5 — Courier New font | All diff cell runs use `w:rFonts ascii="Courier New" hAnsi="Courier New"` |
| E6 — Correct font size | All diff cell runs use `<w:sz val="16"/>` and `<w:szCs val="16"/>` |

**FAIL trigger:** Any `@@` paragraph grey or not bold; `+` lines not green; `-` lines not red.

**Verification method (E3/E4):**
```python
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
for table_idx, table in enumerate(doc.tables):
    for row in table.rows:
        for cell_idx, cell in enumerate(row.cells):
            for p in cell._tc.findall(W + "p"):
                txt = "".join(t.text or "" for t in p.findall(".//" + W + "t"))
                if txt.startswith("@@"):
                    for r in p.findall(W + "r"):
                        rpr = r.find(W + "rPr")
                        color = rpr.find(W + "color") if rpr is not None else None
                        bold = rpr.find(W + "b") if rpr is not None else None
                        assert color is not None and color.get(W + "val") == "2F5597"
                        assert bold is not None
```

---

## Gate F — Patch Fact

**Purpose:** Verify all diff content and identifiers are traceable to the actual patch file.

**Checks:**

| Check | Pass Condition |
|---|---|
| F1 — No ellipsis in diff cells | No `"..."` paragraph inside any diff table cell |
| F2 — No pseudo diff | All diff lines verbatim from patch (≥3 of 5 sampled lines match) |
| F3 — File paths in patch | Every file path in TSD found in patch `diff --git` headers |
| F4 — Method names in patch | Declared methods found in patch `+` lines for that file |
| F5 — Note outside table | Truncation note paragraph is before the table, not inside a cell |
| F6 — Coverage equation pass | Gate A2 (coverage equation) — referenced again here |
| F7 — No git metadata in diff cell | No `diff --git` / `new file mode` / `index` / `rename from/to` inside any diff cell (PFC-11) |
| F8 — Logic-bearing example | Representative example is logic-bearing code, not metadata / boilerplate (PFC-12) |

**FAIL trigger:** `"..."` in diff cell; sampled lines mismatch; file not in patch; note inside cell; git metadata inside diff cell; example is getter/setter/boilerplate only.

**Enforcement:** F1, F2, and F7 are checked deterministically by `tools/rule21_linter.py` (P2_ELLIPSIS_IN_CELL, P3_NOT_VERBATIM_FROM_PATCH, P1_GIT_METADATA_IN_CELL respectively), invoked from `core/WORKFLOW.md`. F3, F4, F5, F6, and F8 remain manual/judgment-based checks; this table's prose is the semantic authority for all eight.

---

## Gate G — Render / Visual QA

**Purpose:** Verify the output docx renders correctly with no visual defects.

**Checks:**

| Check | Pass Condition |
|---|---|
| G1 — No text overflow | No text clipped or overflowing table cell boundaries |
| G2 — No broken rows | All table rows fully visible; no collapsed or zero-height rows |
| G3 — TOC renders correctly | TOC entries show section numbers and page numbers |
| G4 — Diff colour visible | Green / red / blue colour visible in rendered output |
| G5 — Navigation pane bookmarks | Bookmarks appear in Word navigation pane without errors |
| G6 — File size reasonable | Output file size within expected range (±30% of template size) |

**Verification method:** Convert to PDF or open in Word/LibreOffice; screenshot diff tables; visually verify colours.

**FAIL trigger:** Text overflow; TOC broken; diff colour absent in render; file size anomaly.

---

## Gate H — Grouping Mode Gate（雙模式版本）

Updated: 2026-05-12 (Replaced single-mode Code Change Pattern gate with dual-mode Grouping Mode gate)

**Purpose:** Verify the TSD uses a declared, justified grouping mode and that the mode is correctly applied.

**Supports two valid modes:**
- **Mode A — Business Function Grouping:** Sections grouped by shared business purpose / end-to-end flow.
- **Mode B — Code Change Pattern Grouping:** Sections grouped by code change pattern type.

**Checks:**

| Check | Status |
|---|---|
| H1 — Grouping mode is explicitly declared in rationale report | PASS/FAIL |
| H2 — Mode A / Mode B / Hybrid decision is justified | PASS/FAIL |
| H3 — If Mode A: each section has a clearly named business function | PASS/FAIL |
| H4 — If Mode A: mixed code patterns are disclosed in rationale | PASS/FAIL |
| H5 — If Mode B: each section has a primary Code Change Pattern | PASS/FAIL |
| H6 — If Mode B: Shared Purpose is secondary (merge/split confirmation only) | PASS/FAIL |
| H7 — Supporting files are marked as supporting in either mode | PASS/FAIL |
| H8 — Representative examples match selected mode | PASS/FAIL |
| H9 — Rationale report explains why not split / why not merge | PASS/FAIL |
| H10 — File-Level Completeness holds in either mode | PASS/FAIL |
| H11 — No git metadata inside diff example table (both modes) | PASS/FAIL |
| H12 — Representative example is logic-bearing (both modes) | PASS/FAIL |

**Pass Conditions:**

**H1:** Rationale report begins with `Grouping Mode:` declaration (Mode A / Mode B / Hybrid).

**H2:** Rationale report includes `Why this mode:` and `Why not the other mode:` justification.

**H3 (Mode A only):** Each section has a named business function in the rationale. A vague section like "PRJ-A 功能調整" without naming the specific flow is FAIL. "FX 換匯主流程 (Requote + Orders)" is PASS.

**H4 (Mode A only):** When a Mode A section contains multiple code change patterns, the rationale report lists all patterns with primary vs. supporting classification.

**H5 (Mode B only):** Every section in QA report declares a Code Change Pattern from the SECTION_GROUPING_RULES.md pattern table.

**H6 (Mode B only):** Rationale shows Code Change Pattern was primary grouping key; Shared Purpose is secondary.

**H7 (both modes):** Files labelled `supporting-document`, `utility-helper-new`, or `test-mock-new` are not described as primary business changes.

**H8 (both modes):**
- Mode A: example hunk represents core flow logic (service orchestration, controller entry, key external call)
- Mode B: example hunk represents the code change pattern (controller method for controller section, service logic for service section, etc.)

**H9 (both modes):** Rationale contains per-section "Why Not Split" and "Why Not Merge" statements.

**H10 (both modes):** Coverage equation holds — `missing = 0`.

**H11 (both modes):** No diff cell contains `diff --git`, `new file mode`, `deleted file mode`, `index`, `similarity index`, `rename from`, or `rename to` lines.

**H12 (both modes):** 3.x.3 diff example contains at least one hunk with business logic, API handling, data access logic, or migration code — not only getters/setters/package/import/boilerplate.

**FAIL trigger:** Any H1–H12 check is FAIL.

**Important: Mode A + PRJ-A FX is NOT an auto-FAIL.**
Under Mode A, a section containing `controller-api + service-business-logic + dto-bo + service-flow-adjustment`
for one end-to-end business flow is CORRECT. H4 PASS requires only that the mixed patterns are
disclosed in the rationale — not that they are split into separate sections.

**Verification method:**
1. Locate `Grouping Mode:` declaration in rationale report (FAIL if absent → H1).
2. Locate `Why this mode:` justification (FAIL if absent → H2).
3. If Mode A: verify each section has a named business flow (FAIL if vague → H3);
   verify mixed patterns are disclosed (FAIL if undisclosed → H4).
4. If Mode B: verify each section has a declared Code Change Pattern (FAIL if absent → H5);
   verify Shared Purpose is secondary (FAIL if sole grouping key → H6).
5. Check supporting files labelling (FAIL if supporting presented as primary → H7).
6. Verify representative example matches mode selection criteria (FAIL if wrong type → H8).
7. Verify per-section rationale has Why Not Split / Why Not Merge (FAIL if absent → H9).
8. Run coverage equation: missing = 0 required (FAIL if missing > 0 → H10).
9. Scan 3.x.3 diff cells for git metadata (FAIL if found → H11).
10. Sample `+` lines from diff cells; check for logic content (FAIL if only boilerplate → H12).

**Enforcement:** H11 (step 9 above) is checked deterministically by `tools/rule21_linter.py` (P1_GIT_METADATA_IN_CELL), invoked from `core/WORKFLOW.md`. H1–H10 and H12 remain manual/judgment-based checks against this table's prose.

---

## Gate I — Reference Output Regression（新增）

**Purpose:** Verify the correct reference files are used and that their roles are not confused.

**Checks:**

| Check | Pass Condition |
|---|---|
| I1 — Template source is assets/base_template.docx | `Document(template_path)` clones `assets/base_template.docx`; NOT PRJ-A v7 | PASS/FAIL |
| I2 — Gate I reference artifact (`<REF_FINAL>`) is bound | For this run, `<REF_FINAL>` is bound to a concrete operator-supplied local artifact; the generated output is evaluated against that bound artifact using the existing Gate I regression semantics. If unbound, I2 is NOT PASS. | PASS/FAIL |
| I3 — New output uses template source as clone base | Generator code clones assets/base_template.docx; PRJ-A v7 is never used as `Document(path)` clone source | PASS/FAIL |
| I4 — New output behavior matches final reference output rules | Style, diff color, hyperlinks, TOC, appendix pStyle verified against the regression checklist for the artifact bound to `<REF_FINAL>` | PASS/FAIL |
| I5 — No PRJ-A-specific content copied into unrelated TSD | PRJ-A project-specific sections/data not inserted into different project TSD | PASS/FAIL |

**Reference Files:**

| Role | File | Allowed Usage |
|---|---|---|
| Template Source | `assets/base_template.docx` | Clone via `Document(template_path)` ✅ |
| Gate I reference artifact (`<REF_FINAL>`) | Operator-supplied local artifact bound for this run | Regression comparison only ✅ / Clone FORBIDDEN ❌ |

**FAIL trigger:** Any I1–I5 check is FAIL.
If `<REF_FINAL>` is not bound to an actual artifact, I2 is not PASS and Gate I
cannot authorize delivery.

**Verification method:**
1. Search generator code for `Document(` call; confirm argument resolves to assets/base_template.docx path (FAIL if resolves to PRJ-A v7 path → I1, I3).
2. Confirm `<REF_FINAL>` is bound to an actual operator-supplied local artifact for this run; the token is not itself the artifact (I2 is NOT PASS when no binding exists).
3. Run behavioral regression: compare new output against the artifact bound to `<REF_FINAL>` using the existing regression checklist (FAIL if the output diverges → I4).
4. Scan new TSD appendix for PRJ-A project file paths (FAIL if found in non-PRJ-A TSD → I5).

See `REFERENCE_OUTPUTS.md` for full role-distinction rules and regression check table.

---

## QA Report Template

After running all gates, produce a QA report with this structure:

```markdown
# TSD QA Report — <version> — <date>

## Summary

| Gate | Status | Notes |
|---|---|---|
| A — File Coverage | PASS / FAIL | |
| B — Section Grouping | PASS / FAIL | |
| C — Word Style | PASS / FAIL | |
| D — Hyperlink & Bookmark | PASS / FAIL | |
| E — Diff Colour | PASS / FAIL | |
| F — Patch Fact | PASS / FAIL | |
| G — Render / Visual QA | PASS / FAIL | |
| H — Code Change Pattern Grouping | PASS / FAIL | |
| I — Reference Output Regression | PASS / FAIL | |
| J — Representative Example Priority | PASS / FAIL | |
| K — Template Mode Gate | PASS / FAIL | |

## Gate A — File Coverage

### Patch Inventory
[QA Table 1 from PATCH_FACT_CHECK.md]

### Coverage Equation
[QA Table 2 from PATCH_FACT_CHECK.md]

## Gate B — Section Grouping
[Section-by-section pass/fail]

## Gate C — Word Style
[Style check results]

## Gate D — Hyperlink & Bookmark
[Bookmark / hyperlink check results]

## Gate E — Diff Colour
[Per-table @@ check results]

## Gate F — Patch Fact
[QA Tables 4 and 5 from PATCH_FACT_CHECK.md]

## Gate G — Render
[Screenshot references or render check log]

## Gate H — Code Change Pattern Grouping
[Pattern-per-section pass/fail table]

## Gate I — Reference Output Regression
[Reference file identity check; behavioral regression result]

## Gate J — Representative Example Priority
[QA Table 6 from PATCH_FACT_CHECK.md — per-section priority check]

## Gate K — Template Mode Gate
[Template mode declaration; scale match check; mode-specific behaviour check]

## Delivery Decision
PASS — all 11 gates pass → delivery allowed
FAIL — [list failing gates] → delivery blocked
```

---

## Gate J — Representative Example Priority Gate

> Note: Gate J was the 10th gate. Gate K (Template Mode Gate) is the 11th gate. See below.

Updated: 2026-05-12 (new gate added for Rule 27 / EXAMPLE_SELECTION_RULES)

### Purpose

Verify that the 3.x.3 representative example for every section follows the mandatory Controller-first priority order defined in `core/EXAMPLE_SELECTION_RULES.md`.

### J Checks

| Check | Description |
|---|---|
| J1 | Section files scanned for Controller presence |
| J2 | Controller method used when Controller candidate available |
| J3 | Service method used when no Controller candidate |
| J4 | Object method (DTO/BO/Request/Response/Entity) used when no Controller/Service candidate |
| J5 | Object field/snippet used only as fallback (Priority 4) |
| J6 | Example is from patch original hunk (verbatim) |
| J7 | Example is logic-bearing (not annotation-only / getter-setter / empty body) |
| J8 | Example does not contain git metadata (`diff --git`, `new file mode`, `deleted file mode`, `index`) |
| J9 | Example does not contain ellipsis (`...`) |
| J10 | Weak fallback (Priority 5) is explicitly marked in QA report |

### Evaluation

| Situation | J-Check impacted | Verdict |
|---|---|---|
| Controller method exists but Service selected | J2 | **FAIL** |
| No Controller; Service exists but DTO/field selected | J3 | **FAIL** |
| No Controller/Service; Object method exists but field-only selected | J4 | **WEAK** (acceptable if no method) |
| No method candidate at all; field/snippet used | J5 | PASS |
| Git metadata in example | J8 | **FAIL** |
| Ellipsis in example | J9 | **FAIL** |
| Priority 5 fallback but not marked WEAK | J10 | **FAIL** |
| Priority 5 fallback and marked WEAK with explanation | J10 | PASS |

**FAIL trigger:** Any J1–J10 check is FAIL.

**Enforcement:** J6, J8, and J9 are checked deterministically by `tools/rule21_linter.py` (P3_NOT_VERBATIM_FROM_PATCH, P1_GIT_METADATA_IN_CELL, P2_ELLIPSIS_IN_CELL respectively), invoked from `core/WORKFLOW.md`. J1–J5, J7, and J10 remain manual/judgment-based checks against this table's prose.

**Full priority detail:** See `core/EXAMPLE_SELECTION_RULES.md`.

**PATCH_FACT_CHECK Table:** See QA Table 6 in `PATCH_FACT_CHECK.md`.

---

## Gate K — Template Mode Gate

Updated: 2026-05-14 (三模板 TSD 架構 Step 1)

**Purpose:** Verify the TSD declares a template mode (A / B / C), that the declared mode matches the project scale, and that the output conforms to that mode's rules.

**Checks:**

| Check | Description | Pass Condition |
|-------|-------------|----------------|
| K1 | Template mode declared in QA report | QA report states `Template Mode: A / B / C (or Hybrid)` |
| K2 | Template mode matches project scale | A=large migration, B=medium feature (PRJ-A-class), C=small/per-file; mismatch requires note |
| K3 | If Template A: large migration grouping applied | Few sections; same-pattern files grouped together; common pattern stated in 3.x.1 |
| K4 | If Template B: medium feature style applied | 10–15 sections typical; `placement = appendix_only` per `CHANGE_PROGRAM_CONTRACT.md`; representative example per section |
| K5 | If Template C: per-file style applied | `placement = inline_per_file` per `CHANGE_PROGRAM_CONTRACT.md`; per-file 3.x.2 entries with 15–30 char summaries; per-file or per-group code blocks |
| K6 | Per-file summaries are only default in Template C | Template B does NOT include per-file 15–30 char summaries unless explicitly requested |
| K7 | File coverage preserved in all modes | Gate A (File Coverage) must pass regardless of template mode |
| K8 | Word style preserved in all modes | Gate C (Word Style) must pass regardless of template mode |
| K9 | Render QA required in all modes | Gate G (Render) must pass regardless of template mode |

**Pass Conditions:**

**K1:** QA report header or summary section contains a line: `Template Mode: [A / B / C / Hybrid]`.

**K2:** Template mode selection is appropriate:
- Template A → project has 50+ files sharing the same migration pattern (e.g., javax→jakarta)
- Template B → project is PRJ-A-class (10–100 files, mixed feature work)
- Template C → project has 3–20 files (small API fix, localized change)
- Mismatch allowed only if QA report contains `PASS_FOR_DEMONSTRATION` note with explanation

**K3 (Template A only):** Files sharing the same migration pattern appear in ONE section, not individual sections per file.

**K4 (Template B only):** 3.x.2 uses hyperlink paragraph `查看附錄完整清單（n 個檔案）`; no per-file detail in main body.

**K5 (Template C only):** Each file or file group has a 3.x.2 entry with 15–30 char summary; appendix includes per-file summaries.

**K6:** Per-file 15–30 char summaries in appendix = Template C feature. If a Template B TSD has per-file summaries, it MUST be labelled `B+C Hybrid`; the QA report must note this is NOT the Template B default.

**K7–K9:** Template mode selection does NOT exempt the TSD from file coverage, Word style, or render requirements.

**FAIL trigger:** Any K1–K9 check is FAIL.

**v19 specific application:**
`PRJ-A_20260505_v19_SHORT_CHANGE_SUMMARY.docx` → Template Mode = `C prototype / B+C Hybrid demonstration`
- K6: PASS (QA report notes per-file summaries are Template C behaviour)
- K2: PASS with `PASS_FOR_DEMONSTRATION` (applied to 90-file project; acceptable for demonstration)

**Verification method:**
1. Locate `Template Mode:` declaration in QA report (FAIL if absent → K1).
2. Check file count and project type against mode criteria (FAIL if mismatch without note → K2).
3. If Template A: count sections; verify no per-file sectioning (FAIL if over-split → K3).
4. If Template B: scan 3.x.2 paragraphs; verify hyperlink-only (FAIL if per-file entries present as default → K4, K6).
5. If Template C: verify per-file 3.x.2 entries with summaries; verify appendix per-file descriptions (FAIL if absent → K5).
6. Re-confirm Gates A, C, G pass — template mode changes section structure only, not these requirements (FAIL if any → K7, K8, K9).

---

## Cross-Reference

- `SKILL.md` Rule 24 (QA Gate Rule summary); Rule 25 (Template Source Rule); Rule 26 (Final Reference Output Rule); Rule 27 (Representative Example Priority Rule); Rule 28 (Template Mode Declaration Rule)
- `PATCH_FACT_CHECK.md` — Patch fact verification detail (Gates F, A); PFC-11 git metadata; PFC-12 logic-bearing; QA Table 6 (Gate J); Three-template fact-check differences
- `STYLE_RULES.md` — Word style verification detail (Gates C, E)
- `OUTPUT_SPEC.md` — Section format rules (Gates B, D); 3.x.3 diff metadata prohibition; 代表範例 priority order; Three-template output differences
- `SECTION_GROUPING_RULES.md` — Grouping verification detail (Gates B, H); Code Change Pattern priority; 10-step flow; Three-template grouping differences
- `REFERENCE_OUTPUTS.md` — Reference file role distinction; regression check table (Gate I)
- `TEMPLATE_RULES.md` — Template clone rules (Gate C, Gate I)
- `core/EXAMPLE_SELECTION_RULES.md` — Complete priority rule, fallback flow, method detection signals (Gate J)
- `core/TEMPLATE_MODE_RULES.md` — Three-template architecture full rules (Gate K)
- `core/TEMPLATE_MODE_OUTPUT_SPEC.md` — Output format differences per template mode (Gate K)
