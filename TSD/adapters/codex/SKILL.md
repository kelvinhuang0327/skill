---
name: TSD
description: Build or update EOS Technical Specification documents from a new patch file or git commit history, using the EOS_v28_0421 TSD format and the project's validated classification rules. Use when generating a new EOS TSD version from patch/diff evidence, not when merely converting existing Markdown into Word.
---

# TSD

Use this skill when the user wants a new EOS TSD generated from:

- a `patch` file
- a `git diff`
- a `commit range`

Do not use this skill for simple Markdown-to-DOCX conversion.

## Workflow

1. Read [WORKFLOW.md](../../core/WORKFLOW.md).
2. Read [CLASSIFICATION_RULES.md](../../core/CLASSIFICATION_RULES.md).
3. Read [OUTPUT_SPEC.md](../../core/OUTPUT_SPEC.md).
4. Determine the template mode from [TEMPLATE_MODE_RULES.md](../../core/TEMPLATE_MODE_RULES.md).
5. Read [CHANGE_PROGRAM_CONTRACT.md](../../core/CHANGE_PROGRAM_CONTRACT.md) before generating any 異動程式 section (3.x.2 or appendix).
6. Inspect the provided patch / git evidence.
7. Produce a structured intermediate result before editing the TSD.
8. Apply only evidence-supported classifications.
9. Keep `EOS_v28_0421` formatting, hyperlinks, appendix backlinks, and before/after tables intact.

## Rules

- Prefer real diff evidence over commit wording.
- If a change is mainly `jakarta`, `openapi`, `swagger`, `cxf`, `axis`, `@EmbeddedId`, or import cleanup, do not oversell it as a business feature.
- If uncertain, mark `[unknown]` or keep the description at technical-support level.

## Expected Output

- New versioned EOS TSD docx
- Optional supporting markdown summary / validation file

## Example Request

`使用 $TSD，根據 /path/to/EOS_DIFF.patch 與指定 commit range，從 EOS_v28_0421 產出 v29。`
