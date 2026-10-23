#!/usr/bin/env python3
"""Focused tests for direct, read-only Task evidence identification."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "fable-method/scripts/fable_artifact_controls.py"
TASK_ID = "NUMBER2_C_TASK_EVIDENCE_AVAILABILITY_R1"
SPEC = importlib.util.spec_from_file_location("fable_artifact_controls", HELPER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load helper at {HELPER_PATH}")
HELPER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HELPER
SPEC.loader.exec_module(HELPER)


class FocusedTestCase(unittest.TestCase):
    assertion_count = 0

    def check(self, condition: Any, message: str) -> None:
        FocusedTestCase.assertion_count += 1
        self.assertTrue(condition, message)

    def task_root(self, parent: Path, task_id: str = TASK_ID) -> Path:
        root = parent / task_id
        root.mkdir()
        return root

    def write_packet(self, root: Path, task_id: str = TASK_ID) -> bytes:
        content = f"TASK_ID: {task_id}\nTASK_LANE: C\n".encode("utf-8")
        (root / "packet.md").write_bytes(content)
        return content

    def write_result(self, root: Path, result: dict[str, Any] | None = None) -> bytes:
        if result is None:
            result = {"task_id": TASK_ID, "task_local_status": "COMPLETE"}
        content = (json.dumps(result, sort_keys=True) + "\n").encode("utf-8")
        (root / "result.json").write_bytes(content)
        return content

    def expect_control_error(self, root_text: str, phrase: str) -> None:
        try:
            HELPER.summarize_task_evidence(root_text, TASK_ID)
        except HELPER.ControlError as exc:
            self.check(phrase in str(exc), f"rejected safely: {exc}")
        else:
            self.check(False, f"expected ControlError containing {phrase!r}")


class TaskEvidenceAvailabilityTest(FocusedTestCase):
    def test_case01_matching_packet_and_result_are_identified(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-case01-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            packet = self.write_packet(root)
            result = self.write_result(root, {
                "task_id": TASK_ID,
                "task_local_status": "COMPLETE",
                "ac01_to_ac15": {"AC01": {"status": "PASS"}},
            })
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["identity_status"] == "MATCH", "CASE01 identity matches")
            self.check(summary["packet"]["status"] == "PRESENT", "CASE01 packet is present")
            self.check(summary["packet"]["task_id"] == TASK_ID, "CASE01 packet Task ID is recorded")
            self.check(summary["packet"]["sha256"] == hashlib.sha256(packet).hexdigest(), "CASE01 packet hash is recorded")
            self.check(summary["result"]["status"] == "PRESENT", "CASE01 result is present")
            self.check(summary["result"]["task_id"] == TASK_ID, "CASE01 result Task ID is recorded")
            self.check(summary["result"]["sha256"] == hashlib.sha256(result).hexdigest(), "CASE01 result hash is recorded")
            self.check(summary["result"]["lifecycle_status"] == "COMPLETE", "CASE01 reported lifecycle is recorded")
            self.check(summary["reported_acceptance"]["status"] == "REPORTED", "CASE01 AC declaration is labeled REPORTED")
            self.check(summary["reported_acceptance"]["independently_validated"] is False, "CASE01 AC declaration is not independent validation")
            self.check(summary["packet"]["locator"] == str(root / "packet.md"), "CASE01 packet locator is absolute")
            self.check(summary["result"]["locator"] == str(root / "result.json"), "CASE01 result locator is absolute")
        print("CASE01: PASS")

    def test_case02_missing_packet_stays_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-case02-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root)
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["packet"]["status"] == "MISSING", "CASE02 missing packet is MISSING")
            self.check(not (root / "packet.md").exists(), "CASE02 helper does not create a packet")
            self.check(summary["identity_status"] == "UNKNOWN", "CASE02 missing packet leaves identity incomplete")
            self.check(summary["unresolved"]["FIRST_ATTEMPT_STATUS"] == "UNKNOWN", "CASE02 missing packet does not imply execution state")
        print("CASE02: PASS")

    def test_case03_missing_result_stays_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-case03-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["status"] == "MISSING", "CASE03 missing result is MISSING")
            self.check(not (root / "result.json").exists(), "CASE03 helper does not create a result")
            self.check(summary["identity_status"] == "UNKNOWN", "CASE03 missing result leaves identity incomplete")
            self.check(summary["result"]["lifecycle_status"] == "UNKNOWN", "CASE03 missing result does not infer lifecycle")
        print("CASE03: PASS")

    def test_case04_mismatched_result_identity_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-case04-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            self.write_result(root, {"task_id": "OTHER_TASK", "task_local_status": "COMPLETE"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["status"] == "INVALID", "CASE04 mismatched result is INVALID")
            self.check(summary["result"]["task_id"] == "OTHER_TASK", "CASE04 actual result identity is retained")
            self.check(summary["result"]["parse_status"] == "TASK_ID_MISMATCH", "CASE04 mismatch is explicit")
            self.check(summary["identity_status"] == "INVALID", "CASE04 mismatch cannot produce a matching identity")
        print("CASE04: PASS")

    def test_case05_malformed_result_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-case05-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            (root / "result.json").write_text('{"task_id":', encoding="utf-8")
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["status"] == "INVALID", "CASE05 malformed result is INVALID")
            self.check(summary["result"]["parse_status"] == "INVALID_JSON", "CASE05 parse failure is explicit")
            self.check(summary["identity_status"] == "INVALID", "CASE05 invalid result cannot match")
            self.check(summary["result"]["sha256"] is not None, "CASE05 malformed source bytes remain identifiable")

            (root / "result.json").write_text(
                f'{{"task_id":"OTHER_TASK","task_id":"{TASK_ID}"}}',
                encoding="utf-8",
            )
            duplicate_summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(duplicate_summary["result"]["status"] == "INVALID", "CASE05 duplicate JSON identity is INVALID")
            self.check(duplicate_summary["result"]["parse_status"] == "INVALID_JSON", "CASE05 duplicate JSON keys are rejected")
        print("CASE05: PASS")

    def test_case06_symlinks_and_parent_traversal_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-case06-") as temporary:
            parent = Path(temporary).resolve()
            root = self.task_root(parent)
            outside = parent / "outside.md"
            outside.write_text(f"TASK_ID: {TASK_ID}\n", encoding="utf-8")
            (root / "packet.md").symlink_to(outside)
            self.expect_control_error(str(root), "must not be a symlink")

            alternate_parent = parent / "alternate"
            alternate_parent.mkdir()
            linked_root = alternate_parent / TASK_ID
            linked_root.symlink_to(root, target_is_directory=True)
            self.expect_control_error(str(linked_root), "must not contain symlinks")

            wrong_scope = parent / "OTHER_TASK"
            wrong_scope.mkdir()
            self.expect_control_error(str(wrong_scope), "directory name must match --task-id")

            traversed_root = str(parent / "outside" / ".." / TASK_ID)
            self.expect_control_error(traversed_root, "parent-directory traversal")
        print("CASE06: PASS")

    def test_case07_legacy_cli_output_shape_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-case07-") as temporary:
            repo = Path(temporary).resolve()
            initialized = subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, text=True, check=False)
            self.check(initialized.returncode == 0, "CASE07 temporary Git repository initialized")
            command = [
                sys.executable,
                "-B",
                str(HELPER_PATH),
                "evidence-summary",
                "--repo",
                str(repo),
                "--task-id",
                TASK_ID,
            ]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            self.check(completed.returncode == 0, f"CASE07 legacy CLI exits successfully: {completed.stderr.strip()}")
            summary = json.loads(completed.stdout)
            expected_keys = {
                "schema", "repository", "task_filter", "inventory", "coverage",
                "problems", "orphan_captures", "recommendations", "executions",
            }
            self.check(set(summary) == expected_keys, "CASE07 legacy output keys are unchanged")
            self.check("task_evidence" not in summary, "CASE07 no evidence detail appears without the option")
        print("CASE07: PASS")

    def test_case08_final_complete_does_not_imply_first_attempt_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-case08-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root, {"task_id": TASK_ID, "task_local_status": "COMPLETE"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["lifecycle_status"] == "COMPLETE", "CASE08 final lifecycle is reported")
            self.check(summary["unresolved"]["FIRST_ATTEMPT_STATUS"] == "UNKNOWN", "CASE08 first attempt remains UNKNOWN")
            self.check(summary["unresolved"]["FIRST_COMPLETION_RATE"] == "UNKNOWN", "CASE08 first completion rate remains UNKNOWN")
            self.check(summary["unresolved"]["AGENT_RETRY_COUNT"] == "UNKNOWN", "CASE08 retry count remains UNKNOWN")
            self.check(summary["unresolved"]["JUDGE_VERDICT"] == "UNKNOWN", "CASE08 Judge verdict remains UNKNOWN")
            self.check(summary["unresolved"]["FULL_TASK_ELAPSED_TIME"] == "UNKNOWN", "CASE08 full elapsed time remains UNKNOWN")
        print("CASE08: PASS")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TaskEvidenceAvailabilityTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    exit_code = 0 if result.wasSuccessful() else 1
    print(
        f"FOCUSED_TEST_RESULTS: runs={result.testsRun} "
        f"assertions={FocusedTestCase.assertion_count} exit_code={exit_code}"
    )
    raise SystemExit(exit_code)
