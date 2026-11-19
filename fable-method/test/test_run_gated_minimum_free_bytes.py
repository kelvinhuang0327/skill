"""Exercise the run-gated CLI with deterministic disk readings and a real child."""

import io
import json
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

HELPER = Path(__file__).resolve().parents[1] / "scripts" / "fable_artifact_controls.py"


class RunGatedMinimumFreeBytesTest(unittest.TestCase):
    def invoke(self, readings, *options):
        with tempfile.TemporaryDirectory() as directory:
            volume = Path(directory).resolve()
            marker = volume / "launched"
            argv = [
                "fable_artifact_controls.py", "run-gated",
                "--path", str(volume), "--operation", "high-disk-task", *options, "--",
                sys.executable, "-B", "-c", "import pathlib, sys; pathlib.Path(sys.argv[1]).touch()", str(marker),
            ]
            stderr = io.StringIO()
            samples = [SimpleNamespace(free=free) for free in readings]
            with patch("shutil.disk_usage", side_effect=samples) as measure, redirect_stderr(stderr):
                with patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(HELPER), run_name="__main__")
            self.assertEqual(measure.call_args_list, [call(volume)] * len(readings))
            return stopped.exception.code, marker.exists(), stderr.getvalue()

    def test_below_floor_blocks_before_child_launch(self):
        for available in (9, 16 * 1024**3):
            with self.subTest(available=available):
                floor = available + 1
                code, launched, stderr = self.invoke([available], "--minimum-free-bytes", str(floor))
                self.assertEqual(code, 2)
                self.assertFalse(launched)
                result = json.loads(stderr)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual(result["available_bytes"], available)
                self.assertEqual(result["minimum_free_bytes"], floor)

    def test_floor_allows_equality_and_more_space(self):
        for before in (10, 11):
            with self.subTest(before=before):
                code, launched, stderr = self.invoke([before, 0], "--minimum-free-bytes", "10")
                self.assertEqual(code, 0)
                self.assertTrue(launched)
                result = json.loads(stderr.splitlines()[-1])
                self.assertEqual(result["status"], "COMPLETED")
                self.assertEqual(result["before"]["available_bytes"], before)
                self.assertEqual(result["after"]["available_bytes"], 0)

    def test_omitted_floor_warns_and_starts_child(self):
        code, launched, stderr = self.invoke([0, 0])
        self.assertEqual(code, 0)
        self.assertTrue(launched)
        warning, result = map(json.loads, stderr.splitlines())
        self.assertEqual(warning["status"], "WARN")
        self.assertEqual(warning["phase"], "before")
        self.assertEqual(result["status"], "COMPLETED")

    def test_invalid_floor_fails_before_measurement_or_launch(self):
        for value in ("0", "-1", "1.5", "abc"):
            with self.subTest(value=value):
                code, launched, stderr = self.invoke([], "--minimum-free-bytes", value)
                self.assertEqual(code, 2)
                self.assertFalse(launched)
                self.assertIn("--minimum-free-bytes", stderr)
                self.assertRegex(stderr, "positive integer|invalid int value")


if __name__ == "__main__":
    unittest.main()
