# TSD for Gemini

Generate a new EOS Technical Specification document from a real patch file or git change set.

## Input Priority

1. patch / git diff
2. commit messages
3. project file paths and class names

## Shared References

Before writing, read:

- `core/WORKFLOW.md`
- `core/CLASSIFICATION_RULES.md`
- `core/OUTPUT_SPEC.md`
- `core/CHANGE_PROGRAM_CONTRACT.md`
- `core/TEMPLATE_MODE_RULES.md`

Determine the template mode from `core/TEMPLATE_MODE_RULES.md`, then apply
`core/CHANGE_PROGRAM_CONTRACT.md` before generating any 異動程式 section (3.x.2 or appendix).

## Output Rules

- Use `EOS_v28_0421` as the style and structure baseline.
- Keep hyperlinks, appendix links, and before/after formatting stable.
- Do not infer business features from technical migration alone.
- Prefer conservative wording when the evidence only shows framework migration, schema support, connector support, or no-logic change.

## Required Deliverables

- new EOS TSD docx
- optional markdown summary or validation artifact
