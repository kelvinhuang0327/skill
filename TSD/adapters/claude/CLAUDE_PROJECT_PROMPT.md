# TSD for Claude

You are generating a new EOS Technical Specification document from a real patch file or git commit history.

## Task

Build a new EOS TSD version using:

- actual patch / diff evidence
- commit messages only as supporting evidence
- the validated classification rules in:
  - `core/WORKFLOW.md`
  - `core/CLASSIFICATION_RULES.md`
  - `core/OUTPUT_SPEC.md`
  - `core/CHANGE_PROGRAM_CONTRACT.md`
  - `core/TEMPLATE_MODE_RULES.md`

## Critical Rules

- Do not generate the TSD from previously written markdown alone.
- Use `EOS_v28_0421` as the output format baseline.
- Preserve the document structure, hyperlink behavior, appendix backlinks, and before/after example style.
- Do not over-interpret `jakarta`, `swagger`, `openapi`, `axis/cxf`, `@Id class`, or import cleanup as new business functionality.
- If evidence is weak or conflicting, mark `[unknown]` or keep the wording at technical-support level.

## Required Process

1. Parse the patch / git diff.
2. Group changed files by functional intent.
3. Classify each group using the shared rules.
4. Determine the template mode from `core/TEMPLATE_MODE_RULES.md`, then read
   `core/CHANGE_PROGRAM_CONTRACT.md` before generating any 異動程式 section (3.x.2 or appendix).
5. Draft structured item data.
6. Apply the structured result into the `assets/base_template.docx` TSD format, following the OOXML constraints below.
7. Output the new docx and, if requested, a supporting markdown review file.

## OOXML Generation Rules（2026-04-28 驗證）

### TOC Entry Updates
- **Do NOT** use `set_text()` on a TOC paragraph — it destroys the `<w:hyperlink>` structure and PAGEREF field.
- Correct: find the `<w:hyperlink>` inside the paragraph, update only the section-number run and the title run, leave all `<w:tab/>` and webHidden PAGEREF runs untouched.

### Diff Example Table (cell[1])
- **Do NOT** use a single `set_text()` on the diff cell — `\n` is stripped by XML normalization, collapsing all lines into one.
- Correct: remove all existing runs, then create one `<w:r>` per line with its own `<w:rPr>` and `<w:t>`, followed by `<w:br/>` between lines.
- Color per line type (`rFonts eastAsia='標楷體'`, `sz val='17'`):

  | Line prefix | `color val` | Notes |
  |-------------|-------------|-------|
  | `---` / `-` | `B00020` | red, removed |
  | `+++` / `+` | `1F7A1F` | green, added |
  | `@@`        | `2F5597` | blue + `<w:b/>` bold |
  | others      | `333333` | dark grey |

### Appendix File List Bullets
- The proto_bullet for cloning must come from `body[appendix_title_idx + 2]` (a `v24ListBullet` paragraph).
- `body[appendix_title_idx + 1]` is a `v24heading2` sub-heading — **do not** use it as the bullet prototype.
