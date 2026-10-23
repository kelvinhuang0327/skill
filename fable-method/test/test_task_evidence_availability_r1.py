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

    def test_required_case01_canonical_in_progress_and_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case01-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root, {"task_id": TASK_ID, "task_lifecycle_state": "IN_PROGRESS"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["lifecycle_status"] == "IN_PROGRESS", "CASE01 canonical lifecycle value is reported")
            self.check(summary["result"]["lifecycle_status_source"] == "task_lifecycle_state", "CASE01 canonical field source is identified")
        print("REQUIRED_CASE01: PASS")

    def test_required_case02_canonical_complete_is_reported_verbatim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case02-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root, {"task_id": TASK_ID, "task_lifecycle_state": "COMPLETE"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["lifecycle_status"] == "COMPLETE", "CASE02 canonical COMPLETE value is reported")
            self.check(summary["result"]["lifecycle_status_source"] == "task_lifecycle_state", "CASE02 canonical field source is identified")
            self.check(summary["unresolved"]["FIRST_ATTEMPT_STATUS"] == "UNKNOWN", "CASE02 does not infer first-attempt status")
        print("REQUIRED_CASE02: PASS")

    def test_required_case03_legacy_task_local_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case03-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root, {"task_id": TASK_ID, "task_local_status": "BLOCKED"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["lifecycle_status"] == "BLOCKED", "CASE03 legacy task-local status remains supported")
            self.check(summary["result"]["lifecycle_status_source"] == "task_local_status", "CASE03 legacy source is identified")
        print("REQUIRED_CASE03: PASS")

    def test_required_case04_legacy_lifecycle_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case04-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root, {"task_id": TASK_ID, "lifecycle_status": "IN_PROGRESS"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["lifecycle_status"] == "IN_PROGRESS", "CASE04 legacy lifecycle status remains supported")
            self.check(summary["result"]["lifecycle_status_source"] == "lifecycle_status", "CASE04 legacy source is identified")
        print("REQUIRED_CASE04: PASS")

    def test_required_case05_missing_or_invalid_fields_stay_unknown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case05-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root, {
                "task_id": TASK_ID,
                "task_local_status": None,
                "lifecycle_status": "",
            })
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["lifecycle_status"] == "UNKNOWN", "CASE05 no valid lifecycle declaration stays UNKNOWN")
            self.check(summary["result"]["lifecycle_status_source"] == "task_local_status", "CASE05 selected legacy source is explicit")
        print("REQUIRED_CASE05: PASS")

    def test_required_case06_unreadable_canonical_does_not_fall_back_to_complete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case06-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root, {
                "task_id": TASK_ID,
                "task_lifecycle_state": {"status": "COMPLETE"},
                "task_local_status": "COMPLETE",
            })
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["lifecycle_status"] == "UNKNOWN", "CASE06 non-string canonical value cannot become COMPLETE")
            self.check(summary["result"]["lifecycle_status_source"] == "task_lifecycle_state", "CASE06 invalid canonical source remains identifiable")
        print("REQUIRED_CASE06: PASS")

    def test_required_case07_mismatched_task_identity_rejects_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case07-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root, {"task_id": "OTHER_TASK", "task_lifecycle_state": "COMPLETE"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["status"] == "INVALID", "CASE07 mismatched identity is INVALID")
            self.check(summary["result"]["lifecycle_status"] == "UNKNOWN", "CASE07 mismatched lifecycle is not trusted")
        print("REQUIRED_CASE07: PASS")

    def test_required_case08_malformed_and_duplicate_json_reject_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case08-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            (root / "result.json").write_text(
                f'{{"task_id":"{TASK_ID}","task_lifecycle_state":"COMPLETE","task_id":"{TASK_ID}"}}',
                encoding="utf-8",
            )
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["status"] == "INVALID", "CASE08 duplicate JSON key is INVALID")
            self.check(summary["result"]["lifecycle_status"] == "UNKNOWN", "CASE08 duplicate-key lifecycle is not trusted")

            (root / "result.json").write_text('{"task_id":', encoding="utf-8")
            malformed = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(malformed["result"]["status"] == "INVALID", "CASE08 malformed JSON is INVALID")
            self.check(malformed["result"]["lifecycle_status"] == "UNKNOWN", "CASE08 malformed lifecycle is not trusted")
        print("REQUIRED_CASE08: PASS")

    def test_required_case09_complete_keeps_first_attempt_unknown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case09-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_result(root, {"task_id": TASK_ID, "task_lifecycle_state": "COMPLETE"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["lifecycle_status"] == "COMPLETE", "CASE09 final lifecycle is COMPLETE")
            self.check(summary["unresolved"]["FIRST_ATTEMPT_STATUS"] == "UNKNOWN", "CASE09 first attempt remains UNKNOWN")
            self.check(summary["unresolved"]["AGENT_RETRY_COUNT"] == "UNKNOWN", "CASE09 retry count remains UNKNOWN")
            self.check(summary["unresolved"]["JUDGE_VERDICT"] == "UNKNOWN", "CASE09 Judge verdict remains UNKNOWN")
        print("REQUIRED_CASE09: PASS")

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

    def test_required_case10_legacy_cli_output_without_task_evidence_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="task-evidence-required-case10-") as temporary:
            repo = Path(temporary).resolve()
            initialized = subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, text=True, check=False)
            self.check(initialized.returncode == 0, "CASE10 temporary Git repository initialized")
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
            self.check(completed.returncode == 0, f"CASE10 legacy CLI exits successfully: {completed.stderr.strip()}")
            summary = json.loads(completed.stdout)
            expected_keys = {
                "schema", "repository", "task_filter", "inventory", "coverage",
                "problems", "orphan_captures", "recommendations", "executions",
            }
            self.check(set(summary) == expected_keys, "CASE10 legacy output keys are unchanged")
            self.check("task_evidence" not in summary, "CASE10 no evidence detail appears without the option")
        print("REQUIRED_CASE10: PASS")

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

    def test_reported_acceptance_case01_variable_ac12_block(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case01-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            acceptance = {
                f"AC{number:02d}": {"status": "PASS" if number in {1, 12} else "FAIL"}
                for number in range(1, 13)
            }
            self.write_result(root, {"task_id": TASK_ID, "ac01_to_ac12": acceptance})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["reported_acceptance"]["status"] == "REPORTED", "CASE01 AC12 block is recognized")
            self.check(summary["reported_acceptance"]["reported_passes"] == ["AC01", "AC12"], "CASE01 only explicit PASS entries are reported")
            self.check(summary["reported_acceptance"]["independently_validated"] is False, "CASE01 worker report is not independent validation")
        print("CASE01: PASS")

    def test_reported_acceptance_case02_legacy_ac15_compatibility(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case02-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            acceptance = {
                f"AC{number:02d}": {"status": "PASS" if number == 15 else "FAIL"}
                for number in range(1, 16)
            }
            self.write_result(root, {"task_id": TASK_ID, "ac01_to_ac15": acceptance})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["reported_acceptance"]["status"] == "REPORTED", "CASE02 legacy AC15 block remains supported")
            self.check(summary["reported_acceptance"]["reported_passes"] == ["AC15"], "CASE02 legacy PASS result is retained")
        print("CASE02: PASS")

    def test_reported_acceptance_case03_uppercase_ac16_string_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case03-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            acceptance = {
                f"AC{number:02d}": ("PASS: verified by worker" if number == 16 else "FAIL: not met")
                for number in range(1, 17)
            }
            self.write_result(root, {"task_id": TASK_ID, "AC01_TO_AC16": acceptance})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["reported_acceptance"]["status"] == "REPORTED", "CASE03 observed uppercase AC16 block is recognized")
            self.check(summary["reported_acceptance"]["reported_passes"] == ["AC16"], "CASE03 explicit string PASS is recognized")
        print("CASE03: PASS")

    def test_reported_acceptance_case04_block_without_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case04-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            acceptance = {
                f"AC{number:02d}": {"status": "FAIL", "evidence": "not met"}
                for number in range(1, 13)
            }
            self.write_result(root, {"task_id": TASK_ID, "AC01_TO_AC12": acceptance})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["reported_acceptance"]["status"] == "UNKNOWN", "CASE04 no PASS claims leaves status UNKNOWN")
            self.check(summary["reported_acceptance"]["reported_passes"] == [], "CASE04 no AC is reported as PASS")
        print("CASE04: PASS")

    def test_reported_acceptance_case05_no_recognizable_block(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case05-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            self.write_result(root, {
                "task_id": TASK_ID,
                "task_local_status": "COMPLETE",
                "acceptance_notes": {"AC01": {"status": "PASS"}},
            })
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["reported_acceptance"]["status"] == "UNKNOWN", "CASE05 unrecognized nested field is ignored")
            self.check(summary["reported_acceptance"]["reported_passes"] == [], "CASE05 nested PASS is not an AC declaration")
        print("CASE05: PASS")

    def test_reported_acceptance_case06_conflicting_blocks_are_not_merged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case06-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            self.write_result(root, {
                "task_id": TASK_ID,
                "AC01_TO_AC12": {"AC01": {"status": "PASS"}},
                "AC01_TO_AC16": {"AC02": {"status": "PASS"}},
            })
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["reported_acceptance"]["status"] == "UNKNOWN", "CASE06 conflicting declarations remain UNKNOWN")
            self.check(summary["reported_acceptance"]["reported_passes"] == [], "CASE06 conflicting blocks are not merged or selected")
        print("CASE06: PASS")

    def test_reported_acceptance_case07_mismatched_task_id_is_untrusted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case07-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            self.write_result(root, {
                "task_id": "OTHER_TASK",
                "AC01_TO_AC12": {"AC01": {"status": "PASS"}},
            })
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["status"] == "INVALID", "CASE07 mismatched Task ID remains INVALID")
            self.check(summary["identity_status"] == "INVALID", "CASE07 identity is INVALID")
            self.check(summary["reported_acceptance"]["status"] == "UNKNOWN", "CASE07 mismatched result cannot assert acceptance")
            self.check(summary["reported_acceptance"]["reported_passes"] == [], "CASE07 mismatched PASS is discarded")
        print("CASE07: PASS")

    def test_reported_acceptance_case08_invalid_and_duplicate_json_are_untrusted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case08-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            result_path = root / "result.json"
            result_path.write_text('{"task_id":', encoding="utf-8")
            malformed = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(malformed["result"]["status"] == "INVALID", "CASE08 malformed JSON is INVALID")
            self.check(malformed["reported_acceptance"]["status"] == "UNKNOWN", "CASE08 malformed JSON has no trusted acceptance")

            result_path.write_text(
                f'{{"task_id":"{TASK_ID}","task_id":"{TASK_ID}","AC01_TO_AC12":{{"AC01":{{"status":"PASS"}}}}}}',
                encoding="utf-8",
            )
            duplicate = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(duplicate["result"]["status"] == "INVALID", "CASE08 duplicate JSON key is INVALID")
            self.check(duplicate["reported_acceptance"]["reported_passes"] == [], "CASE08 duplicate-key PASS is discarded")
        print("CASE08: PASS")

    def test_reported_acceptance_case09_plain_pass_text_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case09-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            self.write_result(root, {
                "task_id": TASK_ID,
                "report": "PASS: all acceptance criteria are met",
                "notes": "AC01 PASS",
            })
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["reported_acceptance"]["status"] == "UNKNOWN", "CASE09 plain text PASS is ignored")
            self.check(summary["reported_acceptance"]["reported_passes"] == [], "CASE09 arbitrary text cannot create AC results")
        print("CASE09: PASS")

    def test_reported_acceptance_case10_completion_keeps_first_attempt_unknown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case10-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            self.write_result(root, {"task_id": TASK_ID, "task_lifecycle_state": "COMPLETED"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["result"]["lifecycle_status"] == "COMPLETED", "CASE10 final Task status is reported verbatim")
            self.check(summary["unresolved"]["FIRST_ATTEMPT_STATUS"] == "UNKNOWN", "CASE10 first-attempt status remains UNKNOWN")
            self.check(summary["unresolved"]["FIRST_COMPLETION_RATE"] == "UNKNOWN", "CASE10 first-completion rate remains UNKNOWN")
        print("CASE10: PASS")

    def test_reported_acceptance_case11_cli_without_evidence_root_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case11-") as temporary:
            repo = Path(temporary).resolve()
            initialized = subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, text=True, check=False)
            self.check(initialized.returncode == 0, "CASE11 temporary Git repository initialized")
            command = [
                sys.executable, "-B", str(HELPER_PATH), "evidence-summary",
                "--repo", str(repo), "--task-id", TASK_ID,
            ]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            self.check(completed.returncode == 0, f"CASE11 legacy CLI exits successfully: {completed.stderr.strip()}")
            summary = json.loads(completed.stdout)
            expected_keys = {
                "schema", "repository", "task_filter", "inventory", "coverage",
                "problems", "orphan_captures", "recommendations", "executions",
            }
            self.check(set(summary) == expected_keys, "CASE11 legacy CLI output keys are unchanged")
            self.check("task_evidence" not in summary, "CASE11 no task-evidence section appears without the option")
        print("CASE11: PASS")

    def test_reported_acceptance_case12_repeated_reads_are_stable_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="reported-acceptance-case12-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_packet(root)
            self.write_result(root, {
                "task_id": TASK_ID,
                "AC01_TO_AC12": {"AC01": {"status": "PASS"}},
            })
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            first = HELPER.summarize_task_evidence(str(root), TASK_ID)
            second = HELPER.summarize_task_evidence(str(root), TASK_ID)
            after = {path.name: path.read_bytes() for path in root.iterdir()}
            self.check(first == second, "CASE12 repeated parsing produces identical summaries")
            self.check(before == after, "CASE12 source receipt files are unchanged")
            self.check(set(after) == {"packet.md", "result.json"}, "CASE12 parser creates no files")
        print("CASE12: PASS")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TaskEvidenceAvailabilityTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    exit_code = 0 if result.wasSuccessful() else 1
    print(
        f"FOCUSED_TEST_RESULTS: runs={result.testsRun} "
        f"assertions={FocusedTestCase.assertion_count} exit_code={exit_code}"
    )
    raise SystemExit(exit_code)
