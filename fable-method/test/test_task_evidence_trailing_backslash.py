"""A Markdown hard-break backslash after TASK_ID does not change task identity."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parents[1] / "scripts/fable_artifact_controls.py"
TASK_ID = "ESCAPED_TASK_R1"


class TrailingBackslashTaskIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="fable-taskid-escape-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / TASK_ID
        self.root.mkdir()
        (self.root / "result.json").write_text(
            json.dumps({"task_id": TASK_ID, "task_lifecycle_state": "COMPLETED"})
        )

    def summary(self, packet: bytes) -> dict:
        (self.root / "packet.md").write_bytes(packet)
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(HELPER),
                "evidence-summary",
                "--task-id",
                TASK_ID,
                "--task-evidence-root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)["task_evidence"]

    def test_plain_and_single_trailing_backslash_match(self) -> None:
        cases = {
            "plain": b"TASK_ID: ESCAPED_TASK_R1\nTASK_LANE: C\n",
            "escaped_lf": b"TASK_ID: ESCAPED_TASK_R1\\\nTASK_LANE: C\\\n\\\n",
            "escaped_crlf": b"TASK_ID: ESCAPED_TASK_R1\\\r\nTASK_LANE: C\\\r\n",
            "escaped_preamble": b"# Packet\\\n\\\nTASK_ID: ESCAPED_TASK_R1\\\n",
        }
        for label, packet in cases.items():
            with self.subTest(label=label):
                evidence = self.summary(packet)
                self.assertEqual(evidence["identity_status"], "MATCH")
                self.assertEqual(evidence["packet"]["task_id"], TASK_ID)

    def test_escaped_headers_and_multiline_identity(self) -> None:
        (self.root / "result.json").write_text(json.dumps({
            "task_id": TASK_ID, "task_lifecycle_state": "COMPLETED",
            "process_signal": {"trigger": "HIGH_IMPACT"},
        }))
        packet = ("TASK_ID:\\\n\\\n" + TASK_ID + "\\\n"
                  "SIGNAL_POLICY: ALWAYS\\\nCONTINUES_FROM: NONE\\\n"
                  "\\## Body\\\n").encode()
        evidence = self.summary(packet)
        self.assertEqual(evidence["identity_status"], "MATCH")
        process = evidence["process_evidence"]
        self.assertEqual(process["lifecycle_state_check"], "CANONICAL")
        self.assertEqual(process["signal"]["status"], "REPORTED")
        self.assertEqual(process["packet_header"], {
            "signal_policy": "ALWAYS", "continues_from": "NONE"})
        self.assertEqual((self.root / "packet.md").read_bytes(), packet)

    def test_malformed_duplicate_and_heading_boundary_fail_closed(self) -> None:
        cases = [
            b"TASK_ID: ESCAPED_TASK_R1\\\nTASK_ID:\\\nINVALID!\\\n",
            b"TASK_ID:\\\n\\## Body\\\nESCAPED_TASK_R1\\\n",
        ]
        for packet in cases:
            with self.subTest(packet=packet):
                self.assertEqual(self.summary(packet)["identity_status"], "INVALID")

    def test_other_identities_still_fail_closed(self) -> None:
        cases = {
            "double_backslash": b"TASK_ID: ESCAPED_TASK_R1\\\\\n",
            "inner_backslash": b"TASK_ID: ESCAPED_TASK\\_R1\n",
            "other_task": b"TASK_ID: OTHER_TASK_R1\\\n",
            "two_ids": b"TASK_ID: ESCAPED_TASK_R1\\\nTASK_ID: OTHER_TASK_R1\\\n",
            "trailing_text": b"TASK_ID: ESCAPED_TASK_R1 extra\\\n",
        }
        for label, packet in cases.items():
            with self.subTest(label=label):
                self.assertEqual(self.summary(packet)["identity_status"], "INVALID")


if __name__ == "__main__":
    unittest.main()
