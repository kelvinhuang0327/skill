#!/usr/bin/env python3
"""Focused tests for the canonical Rule 21.1-21.3 linter.

Synthetic and self-contained: every fixture below is hand-authored text,
not copied from production TSD content or the Phase 1A pilot's fixture
files. It preserves the same load-bearing test classes the Phase 1A pilot
validated (see analysis/tsd-script-backed-rule-candidate-r1/phase1a/):
positive cases, one case per forbidden git-metadata pattern, an ellipsis
failure, a non-verbatim/pseudo-diff failure, near-miss cases proving no
naive substring overmatch, stable violation IDs, deterministic ordering,
and exit 0/1/2 CLI behavior.

Run: python3 tools/test_rule21_linter.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rule21_linter as r21  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("%-58s %s%s" % (name, "PASS" if ok else "FAIL",
                          "  " + detail if detail and not ok else ""))


# ---------------------------------------------------------------------------
# Synthetic fixtures (frozen, hand-authored — no production/pilot data).
# ---------------------------------------------------------------------------

PATCH_TEXT = (
    "diff --git a/src/main/java/com/example/OrderService.java b/src/main/java/com/example/OrderService.java\n"
    "index 1111111..2222222 100644\n"
    "--- a/src/main/java/com/example/OrderService.java\n"
    "+++ b/src/main/java/com/example/OrderService.java\n"
    "@@ -40,7 +40,9 @@ public class OrderService {\n"
    "-        return null;\n"
    "+        Order order = repository.findById(id);\n"
    "+        return applyDiscount(order);\n"
)

CLEAN_CELL_1 = (
    "@@ -40,7 +40,9 @@ public class OrderService {\n"
    "-        return null;\n"
    "+        Order order = repository.findById(id);\n"
    "+        return applyDiscount(order);"
)

CLEAN_CELL_2 = (
    "@@ -5,2 +5,2 @@\n"
    "-    int total = 0;\n"
    "+    int total = computeTotal(items);"
)

NEG_DIFF_GIT = (
    "diff --git a/src/Foo.java b/src/Foo.java\n"
    "@@ -1,1 +1,1 @@\n"
    "-old\n"
    "+new"
)

NEG_NEW_FILE_MODE = (
    "new file mode 100644\n"
    "@@ -0,0 +1,1 @@\n"
    "+new"
)

NEG_DELETED_FILE_MODE = (
    "deleted file mode 100644\n"
    "@@ -1,1 +0,0 @@\n"
    "-old"
)

NEG_INDEX = (
    "index 1111111..2222222 100644\n"
    "@@ -1,1 +1,1 @@\n"
    "-old\n"
    "+new"
)

NEG_SIMILARITY_INDEX = (
    "similarity index 87%\n"
    "@@ -1,1 +1,1 @@\n"
    "-old\n"
    "+new"
)

NEG_RENAME_FROM = (
    "rename from old/Path.java\n"
    "@@ -1,1 +1,1 @@\n"
    "-old\n"
    "+new"
)

NEG_RENAME_TO = (
    "rename to new/Path.java\n"
    "@@ -1,1 +1,1 @@\n"
    "-old\n"
    "+new"
)

NEG_ELLIPSIS = (
    "@@ -1,3 +1,3 @@\n"
    "+first line\n"
    "...\n"
    "+last line"
)

NEG_PSEUDO_DIFF = (
    "@@ -40,7 +40,9 @@ public class OrderService {\n"
    "-        return legacyNullResult();\n"
    "+        return fancyNewLogic(order);"
)

NM_WORD_INDEX_IN_CODE = (
    "@@ -1,2 +1,2 @@\n"
    "-    int index = 0;\n"
    "+    int index = computeStart();"
)

NM_RENAME_IN_COMMENT = (
    "@@ -1,2 +1,2 @@\n"
    "+    // rename this variable to something clearer later\n"
    "+    int total = 0;"
)

NM_VARARGS_ELLIPSIS = (
    "@@ -1,2 +1,2 @@\n"
    "+    public void handle(String... args) {\n"
    "+    }"
)

NM_SIMILARITY_WORD_IN_CODE = (
    "@@ -1,2 +1,2 @@\n"
    "-    double similarityScore = 0.0;\n"
    "+    double similarityScore = calc(a, b);"
)

# location -> (cell_text, patch_text_or_empty, expected_violation_ids)
VECTORS = {
    "positive/clean_cell_1": (CLEAN_CELL_1, PATCH_TEXT, []),
    "positive/clean_cell_2": (CLEAN_CELL_2, "", []),
    "negative/git_diff_git": (NEG_DIFF_GIT, "", ["P1_GIT_METADATA_IN_CELL"]),
    "negative/git_new_file_mode": (NEG_NEW_FILE_MODE, "", ["P1_GIT_METADATA_IN_CELL"]),
    "negative/git_deleted_file_mode": (NEG_DELETED_FILE_MODE, "", ["P1_GIT_METADATA_IN_CELL"]),
    "negative/git_index": (NEG_INDEX, "", ["P1_GIT_METADATA_IN_CELL"]),
    "negative/git_similarity_index": (NEG_SIMILARITY_INDEX, "", ["P1_GIT_METADATA_IN_CELL"]),
    "negative/git_rename_from": (NEG_RENAME_FROM, "", ["P1_GIT_METADATA_IN_CELL"]),
    "negative/git_rename_to": (NEG_RENAME_TO, "", ["P1_GIT_METADATA_IN_CELL"]),
    "negative/ellipsis_marker": (NEG_ELLIPSIS, "", ["P2_ELLIPSIS_IN_CELL"]),
    "negative/pseudo_diff_not_verbatim": (NEG_PSEUDO_DIFF, PATCH_TEXT, ["P3_NOT_VERBATIM_FROM_PATCH"]),
    "near_miss/word_index_in_code": (NM_WORD_INDEX_IN_CODE, "", []),
    "near_miss/rename_in_comment": (NM_RENAME_IN_COMMENT, "", []),
    "near_miss/varargs_ellipsis": (NM_VARARGS_ELLIPSIS, "", []),
    "near_miss/similarity_word_in_code": (NM_SIMILARITY_WORD_IN_CODE, "", []),
}

CLEAN_LOCATIONS = [loc for loc, (_, _, ids) in VECTORS.items() if not ids]
VIOLATION_LOCATIONS = [loc for loc, (_, _, ids) in VECTORS.items() if ids]


def per_unit_check():
    false_positives = []
    false_negatives = []
    exact_matches = 0

    for loc, (cell_text, patch_text, expected_ids) in VECTORS.items():
        unit = r21.CellUnit(location=loc, text=cell_text)
        violations = r21.lint_unit(unit, patch_text)
        actual_ids = sorted({v.violation_id for v in violations})
        expected_sorted = sorted(expected_ids)

        extra = set(actual_ids) - set(expected_sorted)
        missing = set(expected_sorted) - set(actual_ids)
        if extra:
            false_positives.append((loc, sorted(extra)))
        if missing:
            false_negatives.append((loc, sorted(missing)))
        if not extra and not missing:
            exact_matches += 1

    return exact_matches, len(VECTORS), false_positives, false_negatives


def stable_violation_id_check():
    ok = True
    for loc, (cell_text, patch_text, _expected_ids) in VECTORS.items():
        unit = r21.CellUnit(location=loc, text=cell_text)
        for v in r21.lint_unit(unit, patch_text):
            if v.violation_id not in (
                "P1_GIT_METADATA_IN_CELL",
                "P2_ELLIPSIS_IN_CELL",
                "P3_NOT_VERBATIM_FROM_PATCH",
            ):
                ok = False
    return ok


def deterministic_rerun_check():
    run1 = []
    run2 = []
    for loc, (cell_text, patch_text, _expected_ids) in VECTORS.items():
        unit = r21.CellUnit(location=loc, text=cell_text)
        run1.append([v.violation_id for v in r21.lint_unit(unit, patch_text)])
        run2.append([v.violation_id for v in r21.lint_unit(unit, patch_text)])
    return run1 == run2


def write_manifest(tmp_dir, name, locations):
    manifest = {"units": []}
    for loc in locations:
        cell_text, patch_text, _expected_ids = VECTORS[loc]
        cell_file = loc.replace("/", "_") + ".txt"
        (tmp_dir / cell_file).write_text(cell_text, encoding="utf-8")
        entry = {"location": loc, "cell_file": cell_file}
        if patch_text:
            patch_file = loc.replace("/", "_") + ".patch"
            (tmp_dir / patch_file).write_text(patch_text, encoding="utf-8")
            entry["patch_file"] = patch_file
        manifest["units"].append(entry)
    manifest_path = tmp_dir / name
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def ordering_check(tmp_dir):
    # Deliberately listed out of violation_id order (P2, P1, P3) — the
    # linter must still emit them sorted P1, P2, P3, proving `run()` sorts
    # rather than preserving manifest/insertion order.
    manifest_path = write_manifest(
        tmp_dir, "manifest_ordering.json",
        [
            "negative/ellipsis_marker",          # -> P2
            "negative/git_diff_git",             # -> P1
            "negative/pseudo_diff_not_verbatim",  # -> P3
        ],
    )
    result1 = r21.run(str(manifest_path))
    result2 = r21.run(str(manifest_path))
    ids = [v.violation_id for v in result1.violations]
    ordered = ids == [
        "P1_GIT_METADATA_IN_CELL",
        "P2_ELLIPSIS_IN_CELL",
        "P3_NOT_VERBATIM_FROM_PATCH",
    ]
    rerun_identical = [v.violation_id for v in result2.violations] == ids
    return ordered, rerun_identical, ids


def cli_exit_code_check(tools_dir, tmp_dir):
    results = {}

    clean_manifest = write_manifest(tmp_dir, "manifest_clean.json", CLEAN_LOCATIONS)
    p = subprocess.run(
        [sys.executable, str(tools_dir / "rule21_linter.py"), str(clean_manifest)],
        capture_output=True, text=True,
    )
    results["clean_exit_code"] = p.returncode
    results["clean_expected"] = 0

    violations_manifest = write_manifest(tmp_dir, "manifest_violations.json", VIOLATION_LOCATIONS)
    p = subprocess.run(
        [sys.executable, str(tools_dir / "rule21_linter.py"), str(violations_manifest)],
        capture_output=True, text=True,
    )
    results["violations_exit_code"] = p.returncode
    results["violations_expected"] = 1

    p = subprocess.run(
        [sys.executable, str(tools_dir / "rule21_linter.py"), str(tmp_dir / "does_not_exist.json")],
        capture_output=True, text=True,
    )
    results["missing_manifest_exit_code"] = p.returncode
    results["missing_manifest_expected"] = 2

    p1 = subprocess.run(
        [sys.executable, str(tools_dir / "rule21_linter.py"), str(violations_manifest)],
        capture_output=True, text=True,
    )
    p2 = subprocess.run(
        [sys.executable, str(tools_dir / "rule21_linter.py"), str(violations_manifest)],
        capture_output=True, text=True,
    )
    results["cli_rerun_byte_identical"] = (p1.stdout == p2.stdout) and (p1.returncode == p2.returncode)

    return results


def main():
    tools_dir = Path(__file__).resolve().parent

    print("=== per-unit fixture check ===")
    exact, total, fps, fns = per_unit_check()
    check("per-unit classification exact match", exact == total,
          "exact=%d/%d fps=%s fns=%s" % (exact, total, fps, fns))
    check("no false positives", not fps, str(fps))
    check("no false negatives", not fns, str(fns))

    print("=== stable violation IDs ===")
    check("only frozen violation IDs emitted", stable_violation_id_check())

    print("=== deterministic rerun (library level) ===")
    check("library rerun identical", deterministic_rerun_check())

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        print("=== deterministic ordering (run()/manifest level) ===")
        ordered, rerun_identical, ids = ordering_check(tmp_dir)
        check("violations sorted P1 < P2 < P3 regardless of manifest order", ordered, str(ids))
        check("run() rerun identical", rerun_identical)

        print("=== CLI exit-code conformance ===")
        cli = cli_exit_code_check(tools_dir, tmp_dir)
        check("exit 0 on clean manifest", cli["clean_exit_code"] == cli["clean_expected"],
              "got %r" % cli["clean_exit_code"])
        check("exit 1 on violations manifest", cli["violations_exit_code"] == cli["violations_expected"],
              "got %r" % cli["violations_exit_code"])
        check("exit 2 on missing manifest", cli["missing_manifest_exit_code"] == cli["missing_manifest_expected"],
              "got %r" % cli["missing_manifest_exit_code"])
        check("CLI rerun byte-identical", cli["cli_rerun_byte_identical"])

    failed = [n for n, ok, _ in RESULTS if not ok]
    print()
    print("%d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("FAILED: %s" % ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
