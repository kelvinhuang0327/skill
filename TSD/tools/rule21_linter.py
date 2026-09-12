#!/usr/bin/env python3
"""
Deterministic enforcement for TSD SKILL.md Rule 21.1-21.3 (git metadata /
ellipsis / non-verbatim-from-patch) on already-extracted diff-table cell
text.

SEMANTIC RULE (human-readable authority — author understanding, manual
review, future maintenance): SKILL.md Rule 21, PATCH_FACT_CHECK.md PFC-11,
QA_CHECKLIST.md Gates F1 / F2 / F7 / H11 / J6 / J8 / J9.

DETERMINISTIC ENFORCEMENT (this file): implements exactly the three
predicates below as one canonical, script-backed check invoked from
core/WORKFLOW.md. It mechanizes the rule; it does not restate or replace
the prose, and it is not an independent semantic authority — SKILL.md
remains authoritative for anything this file does not literally test.

  P1_GIT_METADATA_IN_CELL    -> Rule 21.1; PFC-11; Gate F7; Gate H11; Gate J8
  P2_ELLIPSIS_IN_CELL        -> Rule 21.2; Gate F1; Gate J9
  P3_NOT_VERBATIM_FROM_PATCH -> Rule 21.3; Gate F2; Gate J6

One implementation per predicate; rule_refs on each Violation lists every
citing rule/gate rather than duplicating logic per citation.

Explicitly NOT in scope for this file: Rule 21.4 / Gate F5 (truncation-note
placement). That check is structural (paragraph vs. table-cell placement in
the DOCX), not a text/regex predicate on already-extracted cell content, and
mechanizing it here would reintroduce a DOCX-parser dependency this file is
built to avoid. This file implements Rule 21.1-21.3 deterministic checks —
it is not a complete Rule 21 validator.

Not mechanized here or anywhere by this file: Rule 13 (delegated validator
does not exist in-repo), U1-U6, Template-C parity, or any judgment-based
assertion (logic-bearing / vagueness / framing).

Input contract: this linter operates on ALREADY-EXTRACTED cell text (see
CellUnit below) via a JSON units manifest; it does not open, and has no
dependency on, a .docx file. Extracting cell text out of an actual TSD
.docx, and supplying the manifest, is the caller's responsibility (see the
Rule-21 validation step in core/WORKFLOW.md).

Promoted from the validated Phase 1A pilot; see
analysis/tsd-script-backed-rule-candidate-r1/ for the pilot record and the
evidence this promotion is based on. Predicate logic is unchanged from the
pilot — only this docstring and internal comments were updated for
canonical status.

Exit codes (unchanged from the pilot's verified convention — semantics
only, no shared code):
  0 -> PASS      (no violations in any unit checked)
  1 -> FAIL      (at least one violation found)
  2 -> ERROR     (usage error or unreadable/malformed input)
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Predicate P1 — forbidden git-metadata lines inside a diff/example cell.
#
# Design note (near-miss defense): these regexes match the FULL git-diff
# preamble line SHAPE, not a bare keyword. A naive `"index" in text` or
# `"rename" in text` substring check would false-positive on legitimate
# added code such as `+    int index = 0;` or a comment mentioning
# "rename this variable". Anchoring on the exact preamble syntax (hex blob
# ranges, `a/`...`b/` path pairs, the literal `NNNNNN` mode-bits shape)
# avoids that. Verified by test_rule21_linter.py's near-miss cases.
# ---------------------------------------------------------------------------
_GIT_METADATA_PATTERNS = [
    ("diff --git",      re.compile(r"^diff --git a/.+ b/.+$", re.MULTILINE)),
    ("new file mode",   re.compile(r"^new file mode \d{6}$", re.MULTILINE)),
    ("deleted file mode", re.compile(r"^deleted file mode \d{6}$", re.MULTILINE)),
    ("index",           re.compile(r"^index [0-9a-f]{7,40}\.\.[0-9a-f]{7,40}(\s+\d{6})?$", re.MULTILINE)),
    ("similarity index", re.compile(r"^similarity index \d{1,3}%$", re.MULTILINE)),
    ("rename from",     re.compile(r"^rename from .+$", re.MULTILINE)),
    ("rename to",       re.compile(r"^rename to .+$", re.MULTILINE)),
]

# ---------------------------------------------------------------------------
# Predicate P2 — forbidden ellipsis / truncation marker.
#
# Rule 21's exact FAIL condition: "any paragraph inside a diff cell contains
# text that is exactly `...`" — this is WHOLE-LINE equality, not substring
# containment. A naive `"..." in text` check would false-positive on
# legitimate content like a Java varargs signature (`String... args`) or a
# Python Ellipsis literal used as real code. Checked per-line, trimmed,
# for exact equality only. Verified by test_rule21_linter.py's near-miss
# cases.
# ---------------------------------------------------------------------------
_ELLIPSIS_EXACT = "..."


def _check_git_metadata(text: str):
    hits = []
    for label, pattern in _GIT_METADATA_PATTERNS:
        for m in pattern.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            hits.append((label, line_no, m.group(0)))
    return hits


def _check_ellipsis(text: str):
    hits = []
    for i, line in enumerate(text.split("\n"), start=1):
        if line.strip() == _ELLIPSIS_EXACT:
            hits.append((i, line))
    return hits


def _check_verbatim(cell_lines, patch_text: str):
    """
    Gate F2: "All diff lines verbatim from patch (>=3 of 5 sampled lines
    match)". Content lines only — @@ hunk headers and ---/+++ file headers
    are structural, not sampled (Rule 21 allows them unconditionally
    provided the file header condition is met, which this file does not
    re-verify — that is a separate, not-yet-scanned assertion).

    Undefined-behind-the-rule note (implementation choice, NOT a
    canonicalization of TSD text): Gate F2 does not state what happens
    when fewer than 5 content lines exist. This implementation requires
    ALL content lines to match verbatim when the total is below 5, and the
    stated >=3-of-5 ratio when 5 or more exist. This is disclosed here
    (carried forward unchanged from the Phase 1A pilot; see
    analysis/tsd-script-backed-rule-candidate-r1/ for the pilot record) —
    it is not written back into any TSD core .md file and does not resolve
    or narrow U1-U6.
    """
    content_lines = [
        l for l in cell_lines
        if l and not l.startswith("@@") and not l.startswith("--- a/") and not l.startswith("+++ b/")
    ]
    if not content_lines:
        return True, 0, 0
    sample = content_lines[:5]
    matched = sum(1 for l in sample if l[1:].strip() and l[1:].strip() in patch_text)
    total = len(sample)
    if total < 5:
        ok = matched == total
    else:
        ok = matched >= 3
    return ok, matched, total


@dataclass
class Violation:
    violation_id: str
    rule_refs: list
    location: str
    message: str


@dataclass
class CellUnit:
    """One already-extracted diff/example table cell."""
    location: str
    text: str
    patch_ref: str = ""  # optional: path to the associated patch fixture, for P3


@dataclass
class LintResult:
    status: str  # PASS | FAIL | ERROR
    violations: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        return d


def lint_unit(unit: CellUnit, patch_text: str = "") -> list:
    violations = []

    for label, line_no, matched_text in _check_git_metadata(unit.text):
        violations.append(Violation(
            violation_id="P1_GIT_METADATA_IN_CELL",
            rule_refs=["Rule21.1", "PFC-11", "GateF7", "GateH11", "GateJ8"],
            location=f"{unit.location}:line{line_no}",
            message=f"forbidden git-metadata line ({label}): {matched_text!r}",
        ))

    for line_no, line in _check_ellipsis(unit.text):
        violations.append(Violation(
            violation_id="P2_ELLIPSIS_IN_CELL",
            rule_refs=["Rule21.2", "GateF1", "GateJ9"],
            location=f"{unit.location}:line{line_no}",
            message=f"forbidden exact ellipsis marker: {line!r}",
        ))

    if patch_text:
        ok, matched, total = _check_verbatim(unit.text.split("\n"), patch_text)
        if not ok:
            violations.append(Violation(
                violation_id="P3_NOT_VERBATIM_FROM_PATCH",
                rule_refs=["Rule21.3", "GateF2", "GateJ6"],
                location=unit.location,
                message=f"content lines not traceable to patch ({matched}/{total} sampled lines matched)",
            ))

    return violations


def run(units_manifest_path: str) -> LintResult:
    manifest = json.loads(Path(units_manifest_path).read_text(encoding="utf-8"))
    base = Path(units_manifest_path).parent
    all_violations = []
    for entry in manifest["units"]:
        cell_text = Path(base / entry["cell_file"]).read_text(encoding="utf-8")
        patch_text = ""
        if entry.get("patch_file"):
            patch_text = Path(base / entry["patch_file"]).read_text(encoding="utf-8")
        unit = CellUnit(location=entry["location"], text=cell_text)
        all_violations.extend(lint_unit(unit, patch_text))

    # Deterministic ordering: violation_id, then location, then message —
    # never insertion order, so reruns over the same input are byte-identical.
    all_violations.sort(key=lambda v: (v.violation_id, v.location, v.message))

    status = "FAIL" if all_violations else "PASS"
    return LintResult(status=status, violations=all_violations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="path to a units manifest JSON file")
    args = parser.parse_args()

    try:
        result = run(args.manifest)
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "message": str(exc)}, indent=2))
        sys.exit(2)

    print(json.dumps(result.to_dict(), indent=2, default=lambda o: o.__dict__))
    sys.exit(1 if result.status == "FAIL" else 0)


if __name__ == "__main__":
    main()
