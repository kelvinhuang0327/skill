"""Authorization checks require matching task, packet, action and target."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "scripts/fable_artifact_controls.py"
TASK_ID = "OWNER_AUTHORIZATION_CHECK_TEST"
ACTION = "create one local commit"
TARGET = "PER/skill-work branch lanec/owner-authorization-file-20261116"
PACKET_BYTES = b"TASK_ID: OWNER_AUTHORIZATION_CHECK_TEST\nTARGET: local-only\n"


class OwnerAuthorizationCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="fable-authorization-check-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.authorization_path = self.root / "owner-authorization.txt"
        self.packet_path = self.root / "packet.md"
        self.packet_path.write_bytes(PACKET_BYTES)
        self.packet_sha256 = hashlib.sha256(PACKET_BYTES).hexdigest()

    @staticmethod
    def authorization_line(task_id: str, content: str, packet_sha256: str) -> str:
        return f"我授權 {task_id}：{content}｜packet_sha256={packet_sha256}｜2026-11-16 15:00 CST"

    def run_check(self, task_id: str = TASK_ID, action: str = ACTION, target: str = TARGET):
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(HELPER),
                "authorization-check",
                "--file",
                str(self.authorization_path),
                "--task-id",
                task_id,
                "--packet-file",
                str(self.packet_path),
                "--action",
                action,
                "--target",
                target,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return completed, json.loads(completed.stdout)

    def assert_uncovered_scope(self, content: str) -> None:
        self.authorization_path.write_text(
            self.authorization_line(TASK_ID, content, self.packet_sha256), encoding="utf-8"
        )
        completed, result = self.run_check()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["reason"], "ACTION_OR_TARGET_NOT_COVERED")

    def test_valid_authorization_matches(self) -> None:
        content = f"{ACTION} on {TARGET}"
        self.authorization_path.write_text(
            self.authorization_line(TASK_ID, content, self.packet_sha256), encoding="utf-8"
        )

        completed, result = self.run_check()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result["status"], "MATCH")
        self.assertIsNone(result["reason"])
        self.assertEqual(result["matched_line"], 1)
        self.assertEqual(result["packet_sha256"], self.packet_sha256)

    def test_wrong_task_id_does_not_match(self) -> None:
        self.authorization_path.write_text(
            self.authorization_line("ANOTHER_TASK", f"{ACTION} on {TARGET}", self.packet_sha256),
            encoding="utf-8",
        )

        completed, result = self.run_check()

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["reason"], "NO_LINE_FOR_TASK_ID")
        self.assertIsNone(result["matched_line"])

    def test_changed_packet_fingerprint_does_not_match(self) -> None:
        self.authorization_path.write_text(
            self.authorization_line(TASK_ID, f"{ACTION} on {TARGET}", self.packet_sha256),
            encoding="utf-8",
        )
        self.packet_path.write_bytes(PACKET_BYTES + b"# changed\n")
        changed_sha256 = hashlib.sha256(self.packet_path.read_bytes()).hexdigest()

        completed, result = self.run_check()

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["reason"], "PACKET_SHA256_MISMATCH")
        self.assertEqual(result["packet_sha256"], changed_sha256)

    def test_missing_authorization_file_does_not_match(self) -> None:
        completed, result = self.run_check()

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["reason"], "FILE_MISSING")
        self.assertEqual(result["packet_sha256"], self.packet_sha256)

    def test_appended_scope_matches_on_second_line(self) -> None:
        first_line = self.authorization_line(TASK_ID, ACTION, self.packet_sha256)
        second_line = self.authorization_line(TASK_ID, f"{ACTION} on {TARGET}", self.packet_sha256)
        self.authorization_path.write_text(f"{first_line}\n{second_line}\n", encoding="utf-8")

        completed, result = self.run_check()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["matched_line"], 2)

    def test_action_mismatch_does_not_match(self) -> None:
        self.assert_uncovered_scope(f"a different operation on {TARGET}")

    def test_target_mismatch_does_not_match(self) -> None:
        self.assert_uncovered_scope(f"{ACTION} on another target")

    def test_scope_cannot_be_spliced_across_two_lines(self) -> None:
        first_line = self.authorization_line(
            TASK_ID, f"{ACTION} on an unrelated target", self.packet_sha256
        )
        second_line = self.authorization_line(
            TASK_ID, f"a different operation on {TARGET}", self.packet_sha256
        )
        self.authorization_path.write_text(f"{first_line}\n{second_line}\n", encoding="utf-8")

        completed, result = self.run_check()

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["reason"], "ACTION_OR_TARGET_NOT_COVERED")

    def test_recheck_after_authorization_file_deletion_does_not_match(self) -> None:
        content = f"{ACTION} on {TARGET}"
        self.authorization_path.write_text(
            self.authorization_line(TASK_ID, content, self.packet_sha256), encoding="utf-8"
        )

        first_completed, first_result = self.run_check()
        self.assertEqual(first_completed.returncode, 0, first_completed.stderr)
        self.assertEqual(first_result["status"], "MATCH")

        self.authorization_path.unlink()
        second_completed, second_result = self.run_check()

        self.assertEqual(second_completed.returncode, 2)
        self.assertEqual(second_result["status"], "NO_MATCH")
        self.assertEqual(second_result["reason"], "FILE_MISSING")

    def test_authorization_and_packet_files_are_unchanged(self) -> None:
        content = f"{ACTION} on {TARGET}"
        self.authorization_path.write_text(
            self.authorization_line(TASK_ID, content, self.packet_sha256), encoding="utf-8"
        )
        authorization_before = self.authorization_path.read_bytes()
        packet_before = self.packet_path.read_bytes()

        completed, result = self.run_check()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(self.authorization_path.read_bytes(), authorization_before)
        self.assertEqual(self.packet_path.read_bytes(), packet_before)

    def test_missing_packet_file_has_its_own_reason(self) -> None:
        content = f"{ACTION} on {TARGET}"
        self.authorization_path.write_text(
            self.authorization_line(TASK_ID, content, self.packet_sha256), encoding="utf-8"
        )
        self.packet_path.unlink()

        completed, result = self.run_check()

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["reason"], "PACKET_FILE_MISSING")
        self.assertIsNone(result["packet_sha256"])

    def test_blank_task_action_or_target_is_rejected_as_invalid_arguments(self) -> None:
        for task_id, action, target in (
            ("", ACTION, TARGET),
            (TASK_ID, "", TARGET),
            (TASK_ID, ACTION, ""),
        ):
            with self.subTest(task_id=task_id, action=action, target=target):
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(HELPER),
                        "authorization-check",
                        "--file",
                        str(self.authorization_path),
                        "--task-id",
                        task_id,
                        "--packet-file",
                        str(self.packet_path),
                        "--action",
                        action,
                        "--target",
                        target,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertIn("must not be empty", completed.stderr)


if __name__ == "__main__":
    unittest.main()
