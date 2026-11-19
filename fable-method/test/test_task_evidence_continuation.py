"""Explicit continuation roots retain task identity and no-follow checks."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "scripts/fable_artifact_controls.py"
TASK_ID = "ORIGINAL_TASK_R1"
CONTINUATION_ID = "CONTINUATION_TASK_R2"


class ContinuationEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="fable-continuation-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.root = self.parent / CONTINUATION_ID
        self.root.mkdir()
        self.packet = (
            f"TASK_ID: {TASK_ID}\nSIGNAL_POLICY: ALWAYS\nCONTINUES_FROM: NONE\n"
        )
        self.result = {
            "task_id": TASK_ID,
            "continuation_id": CONTINUATION_ID,
            "task_lifecycle_state": "COMPLETED",
            "process_signal": {"trigger": "HIGH_IMPACT"},
        }
        (self.root / "packet.md").write_text(self.packet)
        self.write_result()

    def write_result(self) -> None:
        (self.root / "result.json").write_text(json.dumps(self.result))

    def call(
        self, root: Path | str | None = None, *extra: str
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            "-B",
            str(HELPER),
            "evidence-summary",
            "--task-id",
            TASK_ID,
        ]
        if root is not None:
            command.extend(["--task-evidence-root", str(root)])
        return subprocess.run(
            command + list(extra), capture_output=True, text=True, check=False
        )

    def call_continuation(
        self, root: Path | str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return self.call(root or self.root, "--continuation-id", CONTINUATION_ID)

    def assert_blocked(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stderr)["status"], "BLOCKED")

    def test_direct_continuation_and_original_default(self) -> None:
        direct = self.call_continuation()
        self.assertEqual(direct.returncode, 0, direct.stderr)
        summary = json.loads(direct.stdout)
        evidence = summary["task_evidence"]
        self.assertEqual(evidence["task_root"], str(self.root))
        self.assertEqual(evidence["task_id"], TASK_ID)
        self.assertEqual(evidence["identity_status"], "MATCH")
        self.assertEqual(
            evidence["continuation_root_check"],
            {
                "status": "MATCH",
                "continuation_id": CONTINUATION_ID,
            },
        )
        self.assertEqual(
            evidence["process_evidence"]["lifecycle_state_check"], "CANONICAL"
        )
        self.assertEqual(evidence["process_evidence"]["signal"]["status"], "REPORTED")
        self.assertEqual(summary["execution_history"], "NOT_READ_WITHOUT_REPOSITORY")
        self.assertFalse(evidence["reported_acceptance"]["independently_validated"])

        self.assert_blocked(self.call(self.root))
        original = self.parent / TASK_ID
        original.mkdir()
        (original / "packet.md").write_text(self.packet)
        (original / "result.json").write_text(json.dumps(self.result))
        legacy = self.call(original)
        self.assertEqual(legacy.returncode, 0, legacy.stderr)
        legacy_evidence = json.loads(legacy.stdout)["task_evidence"]
        self.assertEqual(legacy_evidence["identity_status"], "MATCH")
        self.assertNotIn("continuation_root_check", legacy_evidence)

    def test_root_continuation_and_task_mismatches_fail_closed(self) -> None:
        self.assert_blocked(self.call(self.root, "--continuation-id", "WRONG_R2"))
        self.result["continuation_id"] = "WRONG_R2"
        self.write_result()
        self.assert_blocked(self.call_continuation())
        self.result.pop("continuation_id")
        self.write_result()
        self.assert_blocked(self.call_continuation())
        self.result["continuation_id"] = CONTINUATION_ID
        self.result["task_id"] = "OTHER_TASK_R1"
        self.write_result()
        self.assert_blocked(self.call_continuation())
        self.result["task_id"] = TASK_ID
        self.write_result()
        (self.root / "packet.md").write_text("TASK_ID: OTHER_TASK_R1\n")
        self.assert_blocked(self.call_continuation())

    def test_missing_and_duplicate_inputs_fail_closed(self) -> None:
        (self.root / "result.json").unlink()
        self.assert_blocked(self.call_continuation())
        (self.root / "result.json").write_text(
            f'{{"task_id":"{TASK_ID}","task_id":"{TASK_ID}",'
            f'"continuation_id":"{CONTINUATION_ID}"}}'
        )
        self.assert_blocked(self.call_continuation())
        self.assert_blocked(
            self.call(self.parent / "MISSING_R2", "--continuation-id", "MISSING_R2")
        )

    def test_no_follow_and_parent_traversal(self) -> None:
        intermediate = self.parent / "link"
        intermediate.symlink_to(self.parent, target_is_directory=True)
        self.assert_blocked(self.call_continuation(intermediate / CONTINUATION_ID))
        self.assert_blocked(
            self.call_continuation(str(self.root / ".." / CONTINUATION_ID))
        )
        packet = self.root / "packet.md"
        packet.unlink()
        packet.symlink_to(self.parent / "packet.md")
        (self.parent / "packet.md").write_text(self.packet)
        self.assert_blocked(self.call_continuation())
        packet.unlink()
        packet.write_text(self.packet)
        result = self.root / "result.json"
        result.unlink()
        (self.parent / "result.json").write_text(json.dumps(self.result))
        result.symlink_to(self.parent / "result.json")
        self.assert_blocked(self.call_continuation())

    def test_repo_and_task_local_modes_agree(self) -> None:
        subprocess.run(["git", "init", "-q", str(self.parent)], check=True)
        direct = self.call_continuation()
        repo = self.call(
            self.root, "--continuation-id", CONTINUATION_ID, "--repo", str(self.parent)
        )
        self.assertEqual(repo.returncode, 0, repo.stderr)
        self.assertEqual(
            json.loads(repo.stdout)["task_evidence"],
            json.loads(direct.stdout)["task_evidence"],
        )
        self.assert_blocked(
            self.call(
                None, "--repo", str(self.parent), "--continuation-id", CONTINUATION_ID
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
