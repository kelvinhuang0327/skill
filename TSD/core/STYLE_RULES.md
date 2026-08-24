# TSD Style Rules

## Purpose

This document defines the Word / OOXML style rules for TSD generation. All rules are mandatory unless marked as project-specific.

Updated: 2026-05-05 (formalised from PRJ-A TSD multi-round correction process)

---

## Rule S1 — Template Clone Rule (CRITICAL)

**Always clone the designated template. Never build from blank.**

```python
# CORRECT
doc = Document(template_docx_path)

# WRONG — will lose all styles, TOC, header/footer, bookmarks
doc = Document()
```

The template contains:
- Cover page with field codes
- TOC with hyperlink structure and PAGEREF fields
- Header / footer with page numbers
- Section 4–11 baseline content
- All `pStyle` definitions (v24heading1, v24heading2, v24ListBullet, etc.)
- All colour / font theme settings
- Margin and page layout

**FAIL if** a blank `Document()` is used as the base.

Default template path:
```
assets/base_template.docx
```
(or override via `base_docx` parameter — see OUTPUT_SPEC.md)

---

## Rule S2 — TOC Update Rule

Do NOT use `set_text()` or overwrite an entire TOC paragraph.

**Correct approach:**
1. Find the `<w:hyperlink>` element within each TOC paragraph.
2. Update only the two content runs inside `<w:hyperlink>`:
   - Section number run (e.g., `3.1`)
   - Section title run (e.g., `PRJ-A 轉帳確認`)
3. Preserve the `<w:tab/>` and `webHidden PAGEREF field` runs untouched.
4. `rStyle` must remain `'ae'`; `rFonts eastAsia='標楷體'`.

---

## Rule S3 — Paragraph Style Mapping

| Section | Required pStyle |
|---|---|
| Appendix title heading | `v24heading1` |
| Appendix sub-section (「1. 項目名稱」) | `v24heading2` |
| Appendix file path bullets | `v24ListBullet` |
| Appendix back-link paragraph (「回到主文第 N 項」) | `none` (body text) |
| 3.x.1 body paragraphs | `none` (body text) / `Normal` |
| 3.x.2 paragraphs (hyperlink or per-file entries) | `none` (body text) |
| Note paragraph before diff table | `none` (body text), italic, grey |

`proto_bullet` for `v24ListBullet` must be sourced from `appendix_title_idx + 2` (NOT `+1`).

---

## Rule S4 — Diff Cell Colour Rule (Run-Level)

All diff content runs MUST use explicit `<w:color>` in `<w:rPr>`.

| Line type | `w:color val` | `<w:b/>` | `<w:bCs/>` |
|-----------|--------------|----------|------------|
| `+` added line | `1F7A1F` | No | No |
| `-` deleted line | `B00020` | No | No |
| `@@` hunk header | `2F5597` | **Yes** | **Yes** |
| Context / `diff --git` header | `333333` | No | No |

Font for diff cells: Courier New
```xml
<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>
```

Size: `<w:sz w:val="16"/>` (8pt) and `<w:szCs w:val="16"/>`

`<w:rPr>` element order (must match EOS_v28_0421 ordering):
```
rFonts → [b] → [bCs] → color → sz → szCs
```

---

## Rule S5 — All @@ Hunks Must Be Blue + Bold

A diff cell may contain multiple `@@` hunk headers if the patch hunk spans multiple `@@ -N,N +N,N @@` lines.

**ALL `@@` paragraphs in all diff cells MUST be blue (`2F5597`) + bold.**

Not just the first one.

### Anti-Pattern (Forbidden)
```python
# This only sets the first @@ hunk — other hunks stay grey
first_hunk_para = find_first_at_para(cell)
apply_blue_bold(first_hunk_para)
```

### Correct Pattern
```python
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
for p in cell._tc.findall(W + "p"):
    # collect full text of paragraph
    txt = "".join(t.text or "" for t in p.findall(".//" + W + "t"))
    if txt.startswith("@@"):
        # apply blue + bold to ALL runs in this paragraph
        for r in p.findall(W + "r"):
            rpr = r.find(W + "rPr")
            if rpr is None:
                rpr = SubElement(r, W + "rPr")
                r.insert(0, rpr)
            # color
            col = rpr.find(W + "color")
            if col is None:
                col = SubElement(rpr, W + "color")
            col.set(W + "val", "2F5597")
            # bold
            if rpr.find(W + "b") is None:
                SubElement(rpr, W + "b")
            if rpr.find(W + "bCs") is None:
                SubElement(rpr, W + "bCs")
```

### Known Failure Case (PRJ-A v6 → v7)

Tables 4, 8, and 11 each had multiple hunks. Only the first `@@` was blue+bold from `extract_diff_block()`. 12 additional `@@` paragraphs across those 3 tables were left grey/context-coloured. This was corrected in v7 by scanning ALL `@@`-starting paragraphs.

Affected lines before fix:
- Table 4: `@@ -6`, `-20`, `-69`, `-82` (4 missed)
- Table 8: `@@ -8`, `-54`, `-71`, `-111`, `-125` (5 missed)
- Table 11 Row[1]: `@@ -28`, `-54`, `-86` (3 missed)
- Table 11 Row[3]: `@@ -293` (1 missed)

---

## Rule S6 — Note Paragraph Outside Diff Table

If a diff block is a partial excerpt of the full patch hunk, a note paragraph MUST be placed before the table:

```
以下為 patch 原文節錄。
```

Style requirements for this note paragraph:
- `italic = True`
- `color = 888888` (light grey)
- Font: same as document body (not Courier New)
- Position: immediately before the diff table, within the document body (not inside any cell)

**FAIL if** this note appears inside a diff cell.

---

## Rule S7 — Hyperlink / Bookmark Style

Hyperlinks in 3.x.2 and appendix back-links use the Word internal anchor mechanism:

```xml
<w:hyperlink w:anchor="APPENDIX_SECTION_N" r:id="...">
  <w:r>
    <w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr>
    <w:t>查看附錄完整清單（n 個檔案）</w:t>
  </w:r>
</w:hyperlink>
```

Bookmarks:
```xml
<w:bookmarkStart w:id="NNN" w:name="MAIN_SECTION_N"/>
...paragraph content...
<w:bookmarkEnd w:id="NNN"/>
```

**FAIL if** bookmarks are missing or hyperlinks point to incorrect anchors.

---

## Rule S8 — Render / Visual QA

After generating the docx, perform a render check before delivery:

1. Open in Word or convert to PDF / PNG.
2. Verify:
   - No text overflow in diff table cells
   - No clipped or truncated table rows
   - TOC entries render correctly with page numbers
   - Diff colour visible (green / red / blue)
   - No missing bookmarks in navigation pane
3. If any visual defect is found → fix the generator, regenerate, re-render.

**Delivery is blocked** until render check passes.

---

## Cross-Reference

- `SKILL.md` Rule 18 (Template Clone), Rule 20 (Diff Color), Rule 21 (No Ellipsis), Rule 22 (Hyperlink)
- `OUTPUT_SPEC.md` — 3.x format, appendix format, OOXML constraints
- `QA_CHECKLIST.md` Gates C, D, E, G
