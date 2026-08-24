# TSD Change Program Contract（異動程式契約）

## Purpose

This document is the **single normative owner** of the 異動程式 section contract.

Created: 2026-08-24 (change-program contract canonicalization R1)

Normative scope owned here:

- `3.x.2 異動程式清單`
- `3.x.2 逐檔異動程式清單`
- `附錄完整異動程式清單`
- file coverage requirements for this section
- change-program placement by template mode
- change-program-specific validation semantics

**Ownership rule.** Other documents may carry section titles, QA gate names,
historical examples, concise context, and pointers to this file. They MUST NOT
independently redefine the semantics owned here.

**Not owned here.** Bookmark / hyperlink identity and navigation conventions
(`MAIN_SECTION_N`, `APPENDIX_SECTION_N`, and their mapping) remain owned by
`SKILL.md` Rule 22, `OUTPUT_SPEC.md` Hyperlink / Bookmark Rules, and
`STYLE_RULES.md` Rule S7. This contract depends on them; it does not restate or
supersede them. Grouping, example selection, diff-cell rules, Word style, and
template clone rules are likewise owned elsewhere.

---

## Layer 1 — Section / result-level structured result

This is the **section-level** (3.x 調整項目) record. It is not a per-file record.

Source: `OUTPUT_SPEC.md` — 「每個調整項目至少要有：」

```
item_no
title
classification
summary_bullets
file_count
files
example_diffs
confidence
```

These eight fields form one contiguous list. They were physically separated by a
later insertion in `OUTPUT_SPEC.md`; the list is restored here as the canonical
form. Field meanings are unchanged.

Do NOT rename these eight fields to a per-file item type.

---

## Layer 2 — Per-file change-program entry

Derived only from existing change-program / template-mode source text.

| Field | Requirement | Source of the requirement |
|---|---|---|
| `file_path` | required | Must be findable in the patch `diff --git a/<path> b/<path>` headers (`PATCH_FACT_CHECK.md` PFC-1) |
| `change_type` | required | Value set 新增 / 修改 / 刪除 (`TEMPLATE_MODE_RULES.md` appendix format rules). Derivation is UNDEFINED — see below |
| scope classification | where an existing rule applies | `CLASSIFICATION_RULES.md` (IN_SCOPE / SUPPORTING_SCOPE / OUT_OF_SCOPE_REVIEW) and `SKILL.md` Rule 23 (SUPPORTING_DOCUMENT / OUT_OF_SCOPE). See the scope-class note below |
| 15–30 char summary | required only in modes that require it | `TEMPLATE_MODE_RULES.md` Template C appendix format; `PATCH_FACT_CHECK.md` Template C per-file fact-check |
| code / example block reference | required only in modes that require it | `TEMPLATE_MODE_RULES.md` Template C `3.x.3 逐檔異動程式區塊` |

No field is added here beyond what existing source text already requires.

### Layer 1 ↔ Layer 2 join

The Layer 1 field `files` is the join point to the Layer 2 per-file enumeration.

`files` keeps its existing meaning. It is not redefined as a new datatype, and
the Layer 1 field list is not merged into the Layer 2 field list.

---

## Layer 3 — List / common invariants

These invariants are **owned here**. The wording is copied from existing authoritative text and
is not strengthened; the Provenance column records where each came from, not a competing owner.

| Invariant | Provenance (copied from) |
|---|---|
| Every IN_SCOPE file must appear individually in the appendix; grouping does not remove file-level traceability | `SKILL.md` Rule 16; `WORKFLOW.md`; `CLASSIFICATION_RULES.md` File-Level Completeness Rule |
| Every listed path must be traceable to the patch file index | `PATCH_FACT_CHECK.md` PFC-1; validator V1 |
| No phantom files (no appendix path absent from the patch) | `QA_CHECKLIST.md` Gate A4 |
| Coverage equation must balance with `missing = 0` | `QA_CHECKLIST.md` Gate A2; `SKILL.md` Rule 23 |
| `n` is the rendered count associated with the selected change-program enumeration | this contract (see UNDEFINED items — which scope classes contribute to `n` is not defined) |
| Main-body ↔ appendix navigation must exist | NOT owned here — remains owned by `SKILL.md` Rule 22 (see Purpose) |

### Scope-class note (do not conflate)

`SUPPORTING_SCOPE` and `SUPPORTING_DOCUMENT` are **distinct classifications with
distinct owners**. Existing source states, separately:

- `CLASSIFICATION_RULES.md`: a SUPPORTING_SCOPE file 「若被 IN_SCOPE 程式碼直接
  引用」 must appear in the appendix with its classification reason — this
  requirement is **conditional**, not unconditional.
- `CLASSIFICATION_RULES.md`: a SUPPORTING_SCOPE file 「不需計入…主計數」.
- `SKILL.md` Rule 23: a SUPPORTING_DOCUMENT file may be excluded from the TSD
  appendix but must appear in the QA report.

Each half is preserved with its own scope and its own condition. Do NOT flatten
them into a single unconditional rule, and do NOT infer SUPPORTING_DOCUMENT
behaviour from SUPPORTING_SCOPE behaviour or vice versa. Any cross-class
counting relationship is UNDEFINED.

---

## Layer 4 — Template mode adapter

The single place where the mode difference is represented. Do not create a
second A/B/C change-program table in any other document.

### Placement

| Mode | `placement` |
|---|---|
| A | `appendix_only` |
| B | `appendix_only` |
| B+C Hybrid | `appendix_only` body, with opted-in per-file appendix detail |
| C | `inline_per_file` |

### 3.x.2 content by placement

| `placement` | 3.x.2 section title | 3.x.2 content |
|---|---|---|
| `appendix_only` | 異動程式清單 | Exactly one hyperlink paragraph: 「查看附錄完整清單（n 個檔案）」, anchored to this item's appendix bookmark. No file paths, no plain-text count without the hyperlink, no table. |
| `inline_per_file` | 逐檔異動程式清單 | Per-file entries carrying 檔案 / 異動類型 / 異動摘要, per existing Template C section structure. |

### Appendix content by mode

| Mode | Paths + change type | Per-file 15–30 char summary | Per-file code block |
|---|---|---|---|
| A | required | not required | not required |
| B | required | not required (default) | not required |
| B+C Hybrid | required | required (opted in; declare Hybrid in QA report) | not required |
| C | required | required — format ` — {action verb}{subject}{context}`, patch-supported | supported / required per existing Template C rules |

B's opt-in path and the Hybrid declaration requirement are unchanged from
`TEMPLATE_MODE_RULES.md`.

---

## Explicitly UNDEFINED semantics

The following are **not defined** by current authoritative text. Do NOT choose a
value, and do NOT resolve them as a side effect of any other change.

| # | Item | Status | Reason |
|---|---|---|---|
| U1 | Ordering of entries within a change-program enumeration | UNDEFINED | No current authoritative rule. The shipped historical output artifact under `assets/` uses an undocumented layer-style order; no rule authorizes it as canonical. |
| U2 | Deduplication of repeated paths | UNDEFINED | No current authoritative rule. |
| U3 | Empty-state behaviour (`n = 0`, section with no entries) | UNDEFINED | No current authoritative rule. |
| U4 | `change_type` derivation (how 新增 / 修改 / 刪除 is decided from the patch) | UNDEFINED | The only patch-level signals (`new file mode`, `deleted file mode`) are simultaneously forbidden from appearing in diff cells; no rule derives the label. |
| U5 | Rename disposition | UNDEFINED | `rename from` / `rename to` are recognised only as forbidden diff-cell metadata. No `change_type` value covers a rename. |
| U6 | `n` / `file_count` derivation, including which scope classes contribute to the rendered count | UNDEFINED | Current authorities do not define one complete, internally verified counting rule covering every relevant scope class and observed artifact behaviour. Existing prose contains scope-class-specific counting statements; the shipped historical artifact's section-count total differs from its appendix entry count, and the extra item is classified SUPPORTING_DOCUMENT while the cited prose rule concerns SUPPORTING_SCOPE. These are distinct classifications and are NOT asserted to be a direct contradiction. |

Resolving U1–U6 requires an Owner decision and, for parity-sensitive items, a
compliant reference artifact. It is out of scope for any task that has not been
explicitly authorized to define them.

---

## Reference / regression scope

Existing reference-output expectations for `3.x.2` describe a Template-B-era
baseline artifact. They are Template B baseline behaviour and are scoped as such
in `REFERENCE_OUTPUTS.md`.

Template C reference parity is `NOT DEFINED / PENDING COMPLIANT TEMPLATE-C
ARTIFACT`. No Template C golden or reference shape is defined by this contract.

The absence of a Template C reference artifact does not block applying the
Template C normative rules already established in `TEMPLATE_MODE_RULES.md`.

---

## Cross-reference

| Concern | Owner |
|---|---|
| Change-program contract (this file) | `core/CHANGE_PROGRAM_CONTRACT.md` |
| Template mode selection / decision tree | `core/TEMPLATE_MODE_RULES.md` |
| Bookmark / hyperlink navigation | `SKILL.md` Rule 22; `OUTPUT_SPEC.md`; `STYLE_RULES.md` S7 |
| Section grouping | `core/SECTION_GROUPING_RULES.md`; `core/GROUPING_MODE_RULES.md` |
| Representative example selection | `core/EXAMPLE_SELECTION_RULES.md` |
| Diff cell / patch fact rules | `core/PATCH_FACT_CHECK.md`; `core/OUTPUT_SPEC.md` |
| Word style / template clone | `core/STYLE_RULES.md`; `core/TEMPLATE_RULES.md` |
| QA gates verifying this contract | `core/QA_CHECKLIST.md` Gates A, D, K |
