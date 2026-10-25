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
        root.mkdir(parents=True)
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

    def write_process_packet(self, root: Path, headers: str = "") -> bytes:
        content = f"TASK_ID: {TASK_ID}\n{headers}".encode("utf-8")
        (root / "packet.md").write_bytes(content)
        return content

    def write_process_result(
        self,
        root: Path,
        fields: dict[str, Any] | None = None,
        *,
        raw: str | None = None,
    ) -> bytes:
        result: dict[str, Any] = {"task_id": TASK_ID}
        if fields:
            result.update(fields)
        content = (raw if raw is not None else json.dumps(result, sort_keys=True)) + "\n"
        encoded = content.encode("utf-8")
        (root / "result.json").write_bytes(encoded)
        return encoded

    def process_evidence(self, root: Path) -> dict[str, Any]:
        summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
        return summary.get("process_evidence", {})

    def test_process_evidence_ps01_valid_result_and_absent_policy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps01-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_process_packet(root, "CONTINUES_FROM: NONE\n")
            self.write_process_result(root, {"task_lifecycle_state": "COMPLETED"})
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            evidence = summary.get("process_evidence", {})
            self.check(summary["identity_status"] == "MATCH", "PS01 identity matches")
            self.check(summary["result"]["lifecycle_status"] == "COMPLETED", "PS01 legacy lifecycle value is retained")
            self.check(evidence.get("status") == "EVALUATED", "PS01 process evidence is evaluated")
            self.check(evidence.get("lifecycle_state_check") == "CANONICAL", "PS01 canonical lifecycle is recognized")
            self.check(evidence.get("packet_header") == {"signal_policy": "ABSENT", "continues_from": "NONE"}, "PS01 packet headers are summarized")
            self.check(evidence.get("prior_result_receipts") == 0, "PS01 no prior receipts are counted")
            self.check(evidence.get("signal") == {"status": "ABSENT"}, "PS01 signal is absent")
            self.check(evidence.get("independently_validated") is False, "PS01 process evidence is not independent validation")
        print("PS01: PASS")

    def test_process_evidence_ps02_minimal_high_impact_is_reported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps02-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_process_packet(root)
            signal = {"trigger": "HIGH_IMPACT"}
            self.write_process_result(root, {"process_signal": signal})
            evidence = self.process_evidence(root)
            self.check(evidence.get("signal") == {"status": "REPORTED", "value": signal}, "PS02 minimal HIGH_IMPACT signal is preserved")
        print("PS02: PASS")

    def test_process_evidence_ps03_complete_incident_is_reported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps03-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_process_packet(root)
            signal = {
                "trigger": "INCIDENT",
                "versions": {"planner": "v5.4", "fable": "sha256:abc"},
                "route": {"task_class": "STATE_CHANGING_IMPLEMENTATION", "worker_route": "STANDARD"},
                "cost": {"elapsed_ms": 12, "tokens": 0, "resources": "local", "evidence": "timer"},
                "issue": {"type": "FABLE", "evidence": "failed assertion", "rca": "missing check", "smallest_fix": "add check"},
            }
            self.write_process_result(root, {"process_signal": signal})
            evidence = self.process_evidence(root)
            self.check(evidence.get("signal") == {"status": "REPORTED", "value": signal}, "PS03 complete INCIDENT signal is preserved")
        print("PS03: PASS")

    def test_process_evidence_ps04_incident_requires_issue_type(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps04-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_process_packet(root)
            self.write_process_result(root, {"process_signal": {"trigger": "INCIDENT"}})
            evidence = self.process_evidence(root)
            signal = evidence.get("signal", {})
            self.check(signal.get("status") == "INVALID", "PS04 INCIDENT without issue.type is invalid")
            self.check(bool(signal.get("invalid_reasons")), "PS04 invalid reason is present")
        print("PS04: PASS")

    def test_process_evidence_ps05_attempt_is_not_in_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps05-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_process_packet(root)
            self.write_process_result(root, {"process_signal": {"trigger": "HIGH_IMPACT", "attempt": 2}})
            evidence = self.process_evidence(root)
            self.check(evidence.get("signal", {}).get("status") == "INVALID", "PS05 attempt key invalidates signal")
        print("PS05: PASS")

    def test_process_evidence_ps06_unmeasured_cost_stays_null(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps06-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            self.write_process_packet(root)
            signal = {"trigger": "HIGH_IMPACT", "cost": {"elapsed_ms": None, "tokens": None}}
            self.write_process_result(root, {"process_signal": signal})
            reported = self.process_evidence(root).get("signal", {})
            self.check(reported == {"status": "REPORTED", "value": signal}, "PS06 null cost fields remain null")
            if reported.get("status") == "REPORTED":
                self.check(reported["value"]["cost"]["elapsed_ms"] is None, "PS06 elapsed_ms is not replaced with zero")
                self.check(reported["value"]["cost"]["tokens"] is None, "PS06 tokens are not replaced with zero")
        print("PS06: PASS")

    def test_process_evidence_ps07_invalid_contract_values_are_rejected(self) -> None:
        invalid_signals = [
            {"trigger": "HIGH_IMPACT", "cost": {"elapsed_ms": True}},
            {"trigger": "HIGH_IMPACT", "cost": {"tokens": -1}},
            {"trigger": "HIGH_IMPACT", "cost": {"tokens": "100"}},
            {"trigger": "HIGH_IMPACT", "cost": {"resources": 5}},
            {"trigger": "HIGH"},
            {"trigger": "HIGH_IMPACT", "issue": {"type": "OTHER"}},
            {"trigger": "HIGH_IMPACT", "versions": []},
            {"trigger": "HIGH_IMPACT", "route": {"worker_route": "STANDARD", "extra": "x"}},
            {"trigger": "HIGH_IMPACT", "versions": {"planner": ""}},
            {"trigger": "HIGH_IMPACT", "route": {"depth": "   "}},
        ]
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps07-") as temporary:
            parent = Path(temporary).resolve()
            for index, signal in enumerate(invalid_signals, start=1):
                root = self.task_root(parent / str(index))
                self.write_process_packet(root)
                self.write_process_result(root, {"task_lifecycle_state": "COMPLETED", "process_signal": signal})
                evidence = self.process_evidence(root)
                result = evidence.get("signal", {})
                reasons = result.get("invalid_reasons", [])
                self.check(result.get("status") == "INVALID", f"PS07 invalid signal {index} is rejected")
                self.check(bool(reasons), f"PS07 invalid signal {index} reports a reason")
                self.check(reasons == sorted(reasons), f"PS07 invalid signal {index} reasons are sorted")
                legacy = HELPER.summarize_task_evidence(str(root), TASK_ID)
                self.check(set(legacy) == {
                    "task_id", "task_root", "identity_status", "packet", "result",
                    "reported_acceptance", "unresolved", "process_evidence",
                }, f"PS07 invalid signal {index} does not add unrelated top-level keys")
        print("PS07: PASS")

    def test_process_evidence_ps08_lifecycle_check_uses_only_canonical_field(self) -> None:
        scenarios = [
            ({"task_lifecycle_state": "COMPLETE"}, "NON_CANONICAL", "COMPLETE"),
            ({"task_lifecycle_state": "IN_PROGRESS"}, "CANONICAL", "IN_PROGRESS"),
            ({"task_lifecycle_state": "BLOCKED"}, "CANONICAL", "BLOCKED"),
            ({"task_lifecycle_state": "COMPLETED"}, "CANONICAL", "COMPLETED"),
            ({"task_lifecycle_state": "ABORTED"}, "CANONICAL", "ABORTED"),
            ({"task_local_status": "BLOCKED"}, "MISSING", "BLOCKED"),
        ]
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps08-") as temporary:
            parent = Path(temporary).resolve()
            for index, (fields, expected, legacy_value) in enumerate(scenarios, start=1):
                root = self.task_root(parent / str(index))
                self.write_process_packet(root)
                self.write_process_result(root, fields)
                summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
                self.check(summary["result"]["lifecycle_status"] == legacy_value, f"PS08 lifecycle scenario {index} keeps legacy value")
                self.check(summary.get("process_evidence", {}).get("lifecycle_state_check") == expected, f"PS08 lifecycle scenario {index} is classified")
        print("PS08: PASS")

    def test_process_evidence_ps09_packet_headers_are_parsed_strictly(self) -> None:
        scenarios = [
            ("SIGNAL_POLICY: ALWAYS\nCONTINUES_FROM: X_R1 /path/result.json\n", "ALWAYS", "X_R1"),
            ("SIGNAL_POLICY: ON_INCIDENT\nCONTINUES_FROM: NONE\n", "ON_INCIDENT", "NONE"),
            ("", "ABSENT", "ABSENT"),
            ("SIGNAL_POLICY: ALWAYS\nCONTINUES_FROM: NONE\n\n## Contract example\nSIGNAL_POLICY: ALWAYS | ON_INCIDENT\nCONTINUES_FROM: NONE | <prior task>\n", "ALWAYS", "NONE"),
            ("SIGNAL_POLICY: always\nCONTINUES_FROM: <續接任務填…>\n", "MALFORMED", "MALFORMED"),
            ("SIGNAL_POLICY: <ALWAYS | ON_INCIDENT>\nCONTINUES_FROM: X_R1、/path\n", "MALFORMED", "MALFORMED"),
            ("SIGNAL_POLICY: ALWAYS\nSIGNAL_POLICY: ON_INCIDENT\nCONTINUES_FROM: NONE\n", "MALFORMED", "NONE"),
            ("SIGNAL_POLICY: ALWAYS\nCONTINUES_FROM: NONE\nCONTINUES_FROM: X_R1 /path\n", "ALWAYS", "MALFORMED"),
        ]
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps09-") as temporary:
            parent = Path(temporary).resolve()
            for index, (headers, policy, continuation) in enumerate(scenarios, start=1):
                root = self.task_root(parent / str(index))
                self.write_process_packet(root, headers)
                self.write_process_result(root, {"task_lifecycle_state": "COMPLETED"})
                packet_header = self.process_evidence(root).get("packet_header", {})
                self.check(packet_header.get("signal_policy") == policy, f"PS09 policy scenario {index} parses")
                self.check(packet_header.get("continues_from") == continuation, f"PS09 continuation scenario {index} parses")
        print("PS09: PASS")

    def test_process_evidence_ps10_counts_only_positive_integer_regular_receipts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps10-") as temporary:
            parent = Path(temporary).resolve()
            root = self.task_root(parent)
            self.write_process_packet(root)
            self.write_process_result(root, {"task_lifecycle_state": "COMPLETED"})
            (root / "result.before_1.json").write_text("not json", encoding="utf-8")
            (root / "result.before_2.json").write_text("{}", encoding="utf-8")
            (root / "result.before_x.json").write_text("{}", encoding="utf-8")
            (root / "result.before_0.json").write_text("{}", encoding="utf-8")
            (parent / "receipt-target.json").write_text("{}", encoding="utf-8")
            (root / "result.before_3.json").symlink_to(parent / "receipt-target.json")
            evidence = self.process_evidence(root)
            self.check(evidence.get("prior_result_receipts") == 2, "PS10 counts two regular positive-number receipts without reading contents")
        print("PS10: PASS")

    def test_process_evidence_ps11_nonmatching_identity_is_not_evaluated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps11-") as temporary:
            parent = Path(temporary).resolve()
            for index, kind in enumerate(("result", "packet", "missing_packet"), start=1):
                root = self.task_root(parent / str(index))
                if kind != "missing_packet":
                    self.write_process_packet(root)
                if kind == "packet":
                    (root / "packet.md").write_text("TASK_ID: OTHER_TASK\n", encoding="utf-8")
                result_task_id = "OTHER_TASK" if kind == "result" else TASK_ID
                self.write_process_result(root, {"process_signal": {"trigger": "HIGH_IMPACT"}}, raw=(
                    json.dumps({"task_id": result_task_id, "process_signal": {"trigger": "HIGH_IMPACT"}})
                    if kind == "result" else None
                ))
                evidence = self.process_evidence(root)
                self.check(evidence.get("status") == "NOT_EVALUATED", f"PS11 {kind} identity is not evaluated")
                self.check(evidence.get("reason") == "TASK_IDENTITY_NOT_MATCH", f"PS11 {kind} mismatch reason is exact")
                self.check(evidence.get("independently_validated") is False, f"PS11 {kind} mismatch remains non-independent")
        print("PS11: PASS")

    def test_process_evidence_ps12_invalid_or_duplicate_result_json_is_not_evaluated(self) -> None:
        raw_results = [
            '{"task_id":',
            f'{{"task_id":"{TASK_ID}","process_signal":{{"trigger":"HIGH_IMPACT","trigger":"INCIDENT"}}}}',
        ]
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps12-") as temporary:
            parent = Path(temporary).resolve()
            for index, raw in enumerate(raw_results, start=1):
                root = self.task_root(parent / str(index))
                self.write_process_packet(root)
                self.write_process_result(root, raw=raw)
                summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
                evidence = summary.get("process_evidence", {})
                self.check(summary["result"]["status"] == "INVALID", f"PS12 invalid JSON variant {index} stays INVALID")
                self.check(evidence.get("status") == "NOT_EVALUATED", f"PS12 invalid JSON variant {index} is not evaluated")
        print("PS12: PASS")

    def test_process_evidence_ps13_repeated_read_is_deterministic_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps13-") as temporary:
            repo = Path(temporary).resolve()
            initialized = subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, text=True, check=False)
            self.check(initialized.returncode == 0, "PS13 temporary Git repository initialized")
            root = self.task_root(repo)
            self.write_process_packet(root, "SIGNAL_POLICY: ALWAYS\nCONTINUES_FROM: NONE\n")
            self.write_process_result(root, {
                "task_lifecycle_state": "COMPLETED",
                "process_signal": {"trigger": "HIGH_IMPACT"},
            })
            result_path = root / "result.json"
            digest_before = hashlib.sha256(result_path.read_bytes()).hexdigest()
            names_before = sorted(path.name for path in root.iterdir())
            command = [
                sys.executable, "-B", str(HELPER_PATH), "evidence-summary",
                "--repo", str(repo), "--task-id", TASK_ID,
                "--task-evidence-root", str(root),
            ]
            first = subprocess.run(command, capture_output=True, check=False)
            second = subprocess.run(command, capture_output=True, check=False)
            self.check(first.returncode == 0 and second.returncode == 0, "PS13 both evidence-summary CLI reads succeed")
            self.check(first.stdout == second.stdout, "PS13 repeated CLI output bytes are identical")
            self.check(hashlib.sha256(result_path.read_bytes()).hexdigest() == digest_before, "PS13 result SHA256 is unchanged")
            self.check(sorted(path.name for path in root.iterdir()) == names_before, "PS13 Task directory file list is unchanged")
        print("PS13: PASS")

    def test_process_evidence_ps14_cli_without_task_evidence_root_is_stable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps14-") as temporary:
            repo = Path(temporary).resolve()
            initialized = subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, text=True, check=False)
            self.check(initialized.returncode == 0, "PS14 temporary Git repository initialized")
            command = [sys.executable, "-B", str(HELPER_PATH), "evidence-summary", "--repo", str(repo), "--task-id", TASK_ID]
            first = subprocess.run(command, capture_output=True, check=False)
            second = subprocess.run(command, capture_output=True, check=False)
            self.check(first.returncode == 0 and second.returncode == 0, "PS14 CLI succeeds without task evidence root")
            self.check(first.stdout == second.stdout, "PS14 CLI output is byte-identical across repeated runs")
            summary = json.loads(first.stdout)
            self.check("task_evidence" not in summary, "PS14 no task evidence section is added")
        print("PS14: PASS")

    def test_process_evidence_ps15_non_object_process_signal_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-ps15-") as temporary:
            parent = Path(temporary).resolve()
            for index, value in enumerate(([], "bad", None), start=1):
                root = self.task_root(parent / str(index))
                self.write_process_packet(root)
                self.write_process_result(root, {"process_signal": value})
                signal = self.process_evidence(root).get("signal", {})
                self.check(signal.get("status") == "INVALID", f"PS15 non-object variant {index} is invalid")
                self.check(bool(signal.get("invalid_reasons")), f"PS15 non-object variant {index} has a reason")
        print("PS15: PASS")

    def test_process_evidence_nl01_next_line_task_id_matches(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-nl01-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            (root / "packet.md").write_text(f"TASK_ID:\n{TASK_ID}\n", encoding="utf-8")
            self.write_process_result(root)
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["packet"]["parse_status"] == "VALID_TASK_ID", "NL01 next-line ID is valid")
            self.check(summary["identity_status"] == "MATCH", "NL01 identity matches")

    def test_process_evidence_nl02_next_line_task_id_skips_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-nl02-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            (root / "packet.md").write_text(f"TASK_ID: \t\n\n \t\n  {TASK_ID}  \n", encoding="utf-8")
            self.write_process_result(root)
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["packet"]["parse_status"] == "VALID_TASK_ID", "NL02 blank lines are skipped")
            self.check(summary["identity_status"] == "MATCH", "NL02 identity matches")

    def test_process_evidence_nl03_next_field_is_not_a_task_id(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-nl03-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            (root / "packet.md").write_text("TASK_ID:\nTASK_LANE: C\n", encoding="utf-8")
            self.write_process_result(root)
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["packet"]["parse_status"] == "INVALID_TASK_ID", "NL03 another field is not a value")
            self.check(summary["identity_status"] == "INVALID", "NL03 identity is invalid")

    def test_process_evidence_nl04_next_line_task_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-nl04-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            (root / "packet.md").write_text("TASK_ID:\nOTHER_TASK\n", encoding="utf-8")
            self.write_process_result(root)
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["packet"]["parse_status"] == "TASK_ID_MISMATCH", "NL04 different ID is a mismatch")
            self.check(summary["identity_status"] == "INVALID", "NL04 identity is invalid")

    def test_process_evidence_nl05_mixed_task_id_declarations_are_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-evidence-nl05-") as temporary:
            root = self.task_root(Path(temporary).resolve())
            (root / "packet.md").write_text(f"TASK_ID: {TASK_ID}\nTASK_ID:\n{TASK_ID}\n", encoding="utf-8")
            self.write_process_result(root)
            summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
            self.check(summary["packet"]["parse_status"] == "INVALID_TASK_ID", "NL05 mixed declarations are duplicates")
            self.check(summary["identity_status"] == "INVALID", "NL05 identity is invalid")

    def test_process_evidence_nl06_next_line_task_id_is_header_only(self) -> None:
        scenarios = [
            (f"TASK_ID: {TASK_ID}\n## Body\nTASK_ID:\nOTHER_TASK\n", "VALID_TASK_ID", "MATCH"),
            (f"## Body\nTASK_ID:\n{TASK_ID}\n", "INVALID_TASK_ID", "INVALID"),
        ]
        with tempfile.TemporaryDirectory(prefix="process-evidence-nl06-") as temporary:
            for index, (packet, parse_status, identity) in enumerate(scenarios):
                with self.subTest(index=index):
                    root = self.task_root(Path(temporary).resolve() / str(index))
                    (root / "packet.md").write_text(packet, encoding="utf-8")
                    self.write_process_result(root)
                    summary = HELPER.summarize_task_evidence(str(root), TASK_ID)
                    self.check(summary["packet"]["parse_status"] == parse_status, "NL06 body next-line IDs are ignored")
                    self.check(summary["identity_status"] == identity, "NL06 identity follows header declaration")

    def test_process_evidence_nl07_next_line_process_headers(self) -> None:
        scenarios = [
            ("SIGNAL_POLICY:\n\n ALWAYS \n", "ALWAYS", "ABSENT"),
            ("CONTINUES_FROM:\n\n PRIOR_R1 /path/result.json \n", "ABSENT", "PRIOR_R1"),
            ("CONTINUES_FROM:\nPRIOR_STOP_STATUS:\n", "ABSENT", "MALFORMED"),
            ("CONTINUES_FROM:\n<NONE | 前一 TASK_ID>…\n", "ABSENT", "MALFORMED"),
        ]
        with tempfile.TemporaryDirectory(prefix="process-evidence-nl07-") as temporary:
            for index, (headers, policy, continuation) in enumerate(scenarios):
                with self.subTest(index=index):
                    root = self.task_root(Path(temporary).resolve() / str(index))
                    self.write_process_packet(root, headers)
                    self.write_process_result(root)
                    header = self.process_evidence(root).get("packet_header", {})
                    self.check(header.get("signal_policy") == policy, "NL07 policy uses its next-line value")
                    self.check(header.get("continues_from") == continuation, "NL07 continuation uses its next-line value")

    def test_process_evidence_px01_planner_template_header_contract(self) -> None:
        planner_paths = (
            ROOT / "prompt/Personal_Planner_Handoff_Prompt_v5.5.1_Lean_Final.md",
            ROOT / "planner-handoff/references/planner-handoff-v5.5.1.md",
        )
        test_task_id = "PX01_TEMPLATE_HEADER_CONTRACT_R1"
        results_by_source: dict[str, list[dict[str, Any]]] = {}

        def replace_one_line(packet_text: str, field: str, replacement: str) -> str | None:
            lines = packet_text.splitlines(keepends=True)
            matches = [index for index, line in enumerate(lines) if line.startswith(f"{field}:")]
            if len(matches) != 1:
                return None
            original = lines[matches[0]]
            ending = "\r\n" if original.endswith("\r\n") else "\n" if original.endswith("\n") else ""
            lines[matches[0]] = replacement + ending
            return "".join(lines)

        scenarios = (
            ("unfilled", None, None),
            ("always_prior", "SIGNAL_POLICY: ALWAYS", "CONTINUES_FROM: PX01_PRIOR_R1 /abs/prior/result.json"),
            ("on_incident_none", "SIGNAL_POLICY: ON_INCIDENT", "CONTINUES_FROM: NONE"),
        )

        for planner_path in planner_paths:
            source_name = planner_path.relative_to(ROOT).as_posix()
            source_text = planner_path.read_text(encoding="utf-8")
            section_start = source_text.find("## 9.")
            self.check(section_start >= 0, f"PX01 {source_name} contains the §9 heading")
            if section_start < 0:
                continue

            section = source_text[section_start:]
            fence_start = section.find("```text")
            self.check(fence_start >= 0, f"PX01 {source_name} contains a §9 text block")
            if fence_start < 0:
                continue
            content_start = fence_start + len("```text")
            if section[content_start:content_start + 2] == "\r\n":
                content_start += 2
            elif section[content_start:content_start + 1] == "\n":
                content_start += 1
            closing_fence = section.find("```", content_start)
            self.check(closing_fence >= 0, f"PX01 {source_name} §9 text block is closed")
            if closing_fence < 0:
                continue
            template = section[content_start:closing_fence]

            for field in ("TASK_ID", "SIGNAL_POLICY", "CONTINUES_FROM"):
                matches = [line for line in template.splitlines() if line.startswith(f"{field}:")]
                self.check(len(matches) == 1, f"PX01 {source_name} has exactly one {field} line")
                if len(matches) != 1:
                    break
            else:
                packet_base = replace_one_line(
                    template,
                    "TASK_ID",
                    f"TASK_ID: {test_task_id}",
                )
                self.check(packet_base is not None, f"PX01 {source_name} TASK_ID line can be replaced")
                if packet_base is None:
                    continue

                source_results: list[dict[str, Any]] = []
                for case_name, policy_line, continuation_line in scenarios:
                    packet_text = packet_base
                    if policy_line is not None and continuation_line is not None:
                        packet_text = replace_one_line(packet_text, "SIGNAL_POLICY", policy_line)
                        self.check(packet_text is not None, f"PX01 {source_name} {case_name} policy line can be replaced")
                        if packet_text is None:
                            continue
                        packet_text = replace_one_line(packet_text, "CONTINUES_FROM", continuation_line)
                        self.check(packet_text is not None, f"PX01 {source_name} {case_name} continuation line can be replaced")
                        if packet_text is None:
                            continue

                    with tempfile.TemporaryDirectory(prefix="process-evidence-px01-") as temporary:
                        task_root = self.task_root(Path(temporary).resolve(), test_task_id)
                        (task_root / "packet.md").write_text(packet_text, encoding="utf-8")
                        (task_root / "result.json").write_text(
                            json.dumps({
                                "task_id": test_task_id,
                                "task_lifecycle_state": "COMPLETED",
                            }) + "\n",
                            encoding="utf-8",
                        )
                        summary = HELPER.summarize_task_evidence(str(task_root), test_task_id)
                        evidence = summary.get("process_evidence", {})
                        header = evidence.get("packet_header", {})
                        self.check(summary.get("identity_status") == "MATCH", f"PX01 {source_name} {case_name} identity matches")
                        self.check(evidence.get("status") == "EVALUATED", f"PX01 {source_name} {case_name} is evaluated")
                        self.check(evidence.get("lifecycle_state_check") == "CANONICAL", f"PX01 {source_name} {case_name} lifecycle is canonical")

                        if case_name == "unfilled":
                            self.check(header.get("signal_policy") == "MALFORMED", f"PX01 {source_name} unfilled policy is in the header")
                            self.check(header.get("continues_from") == "MALFORMED", f"PX01 {source_name} unfilled continuation is in the header")
                        elif case_name == "always_prior":
                            self.check(header.get("signal_policy") == "ALWAYS", f"PX01 {source_name} ALWAYS policy parses")
                            self.check(header.get("continues_from") == "PX01_PRIOR_R1", f"PX01 {source_name} prior task ID parses")
                        else:
                            self.check(header.get("signal_policy") == "ON_INCIDENT", f"PX01 {source_name} ON_INCIDENT policy parses")
                            self.check(header.get("continues_from") == "NONE", f"PX01 {source_name} NONE continuation parses")

                        source_results.append({
                            "identity_status": summary.get("identity_status"),
                            "process_evidence": evidence,
                        })
                results_by_source[source_name] = source_results

        self.check(
            results_by_source.get(planner_paths[0].relative_to(ROOT).as_posix())
            == results_by_source.get(planner_paths[1].relative_to(ROOT).as_posix()),
            "PX01 both Planner templates produce identical Helper results",
        )


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TaskEvidenceAvailabilityTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    exit_code = 0 if result.wasSuccessful() else 1
    print(
        f"FOCUSED_TEST_RESULTS: runs={result.testsRun} "
        f"assertions={FocusedTestCase.assertion_count} exit_code={exit_code}"
    )
    raise SystemExit(exit_code)
