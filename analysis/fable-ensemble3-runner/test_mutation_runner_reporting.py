"""Timeout evidence must not disappear or be counted as a caught mutant."""
import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mutation_runner as runner


class ReportingTests(unittest.TestCase):
    def report(self, run):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base"
            source = base / "analysis/fable-ensemble3-runner"
            source.mkdir(parents=True)
            (source / "subject.py").write_text("answer = 1\n")
            case = ("report probe", "subject.py", "answer = 1", "answer = 2", "test_subject.Check.test_answer")
            with mock.patch.object(runner.mc, "_imports_cleanly", return_value=(True, "")), \
                    mock.patch.object(runner.mc, "_run_tests", return_value=run) as executed:
                result = runner.run_case(base, Path(directory), 0, case, timeout=17)
            self.assertEqual(executed.call_args.args[-1], 17)
            self.assertFalse((Path(directory) / "m0").exists())
            return json.loads(json.dumps(result))

    def test_timeout_is_reported_separately_from_a_survivor(self):
        result = self.report(runner.mc.TestRun(True, False, {}, -1))
        self.assertEqual((result["status"], result["test_outcome"], result["timed_out"], result["returncode"]),
                         ("TIMED_OUT", "TIMEOUT", True, -1))
        self.assertEqual(result["timeout_seconds"], 17)
        self.assertGreaterEqual(result["test_elapsed_seconds"], 0)

    def test_timeout_never_counts_as_a_kill_even_with_partial_failure_data(self):
        result = self.report(runner.mc.TestRun(True, False, {"test_answer": "FAIL"}, -1))
        self.assertEqual(result["status"], "TIMED_OUT")

    def test_only_the_expected_completed_assertion_counts(self):
        self.assertEqual(self.report(runner.mc.TestRun(False, False, {"test_answer": "FAIL"}, 1))["status"], "KILLED")
        self.assertEqual(self.report(runner.mc.TestRun(False, False, {"test_other": "FAIL"}, 1))["status"], "NOT_KILLED")
        result = self.report(runner.mc.TestRun(False, False, {"test_answer": "ERROR"}, 1))
        self.assertEqual((result["status"], result["test_outcome"]), ("NOT_KILLED", "ERROR"))

    def test_completed_pass_and_load_error_remain_distinct(self):
        result = self.report(runner.mc.TestRun(False, False, {}, 0))
        self.assertEqual((result["status"], result["test_outcome"], result["timed_out"]), ("NOT_KILLED", "PASS", False))
        result = self.report(runner.mc.TestRun(False, True, {}, 1))
        self.assertEqual((result["status"], result["test_outcome"], result["load_error"]), ("NOT_KILLED", "LOAD_ERROR", True))

    def test_partial_timeout_output_survives_json_and_exception_propagation(self):
        captures = []
        expired = subprocess.TimeoutExpired(["probe"], 0.1, output=b"progress\n", stderr=b"stack\n")
        with self.assertRaises(subprocess.TimeoutExpired):
            runner.captured_run(mock.Mock(side_effect=expired), captures, ["probe"], capture_output=True, timeout=0.1)
        record = json.loads(json.dumps(captures))[0]
        self.assertEqual((record["timed_out"], record["returncode"], record["stdout"], record["stderr"]),
                         (True, None, "progress\n", "stack\n"))

    def test_real_subprocess_completion_is_preserved(self):
        captures = []
        result = runner.captured_run(subprocess.run, captures, [sys.executable, "-B", "-c", "print('done')"],
                                     capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual((captures[0]["timed_out"], captures[0]["stdout"]), (False, "done\n"))

    def test_nonpositive_timeouts_are_rejected(self):
        for value in ("0", "-1"):
            with self.assertRaises(argparse.ArgumentTypeError):
                runner.positive_timeout(value)
        self.assertEqual(runner.positive_timeout("180"), 180)

    def test_filtered_cli_retains_its_baseline_and_a_timeout_stays_red(self):
        cases = [case for case in runner.CASES if case[0] in (
            "R3 D0-1 replay_run drops the verdict (seen by the every-exit oracle)",
            "R3 R3-3 a finished run's write ledger is not checked (seen by the random oracle)")]
        self.assertEqual(len(cases), 2)
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "results.json"
            argv = ["mutation_runner.py", "--case-timeout", "234", "--results-json", str(result_path)]
            for case in cases:
                argv.extend(["--only", case[0]])
            baseline = runner.mc.TestRun(False, False, {}, 0)
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(runner, "build_base", return_value=Path(directory)), \
                    mock.patch.object(runner.mc, "_run_tests", return_value=baseline) as check, \
                    mock.patch.object(runner, "run_case", side_effect=[{"name": cases[0][0], "status": "KILLED"},
                                                                         {"name": cases[1][0], "status": "TIMED_OUT"}]) as execute, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(), 1)
            self.assertEqual(check.call_args.args[1], [case[4] for case in cases])
            self.assertEqual([call.args[-1] for call in execute.call_args_list], [234, 234])
            saved = json.loads(result_path.read_text())
            self.assertEqual(saved["baseline"], baseline.__dict__)
            self.assertEqual(saved["baseline_tests"], [case[4] for case in cases])
            self.assertEqual([result["status"] for result in saved["results"]], ["KILLED", "TIMED_OUT"])


if __name__ == "__main__":
    unittest.main()
