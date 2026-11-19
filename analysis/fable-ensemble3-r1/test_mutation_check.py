"""Exercise the mutation harness with real temporary source and test modules."""
import tempfile
import unittest
from pathlib import Path
import mutation_check as mc


class MutationHarnessTests(unittest.TestCase):
    def evaluate(self, replacement, expect=("test_answer",), old="answer = 1", initial="answer = 1\n", assertion="self.assertEqual(subject.answer, 1)"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "subject.py").write_text(initial)
            (root / "test_subject.py").write_text(
                "import unittest, subject\nclass Check(unittest.TestCase):\n"
                " def test_answer(self):\n  " + assertion + "\n"
            )
            mc.check_baseline(root, ["subject.py", "test_subject.py"], ["test_subject"])
            return mc.evaluate_mutant(root, ["subject.py", "test_subject.py"], ["test_subject"],
                                      mc.Mutant("probe", "subject.py", old, replacement, expect))

    def test_only_an_expected_assertion_is_a_kill(self):
        self.assertEqual(self.evaluate("answer = 2").status, mc.KILLED)

    def test_syntax_errors_are_invalid(self):
        self.assertEqual(self.evaluate("answer = (").status, mc.INVALID)

    def test_import_errors_are_invalid(self):
        self.assertEqual(self.evaluate("raise RuntimeError('import')").status, mc.INVALID)

    def test_test_exceptions_are_crashes(self):
        self.assertEqual(self.evaluate("different_name = 1").status, mc.CRASHED)

    def test_subject_assertion_error_is_not_a_behavioral_assertion(self):
        self.assertEqual(self.evaluate("raise AssertionError('subject')", old="return 1",
            initial="def answer():\n    return 1\n", assertion="self.assertEqual(subject.answer(), 1)").status, mc.CRASHED)

    def test_subject_cannot_fake_a_framework_assertion(self):
        self.assertEqual(self.evaluate("import unittest; unittest.TestCase().fail('subject')", old="return 1",
            initial="def answer():\n    return 1\n", assertion="self.assertEqual(subject.answer(), 1)").status, mc.CRASHED)

    def test_unexpected_assertions_are_not_kills(self):
        self.assertEqual(self.evaluate("answer = 2", ("test_elsewhere",)).status, mc.UNEXPECTED)

    def test_survivors_are_reported(self):
        self.assertEqual(self.evaluate("answer = 1 + 0").status, mc.SURVIVED)

    def test_patches_cannot_disappear_or_be_noops(self):
        self.assertEqual(self.evaluate("answer = 1").status, mc.NOOP)
        self.assertEqual(self.evaluate("answer = 2", old="missing").status, mc.PATCH_MISMATCH)

    def test_red_baseline_stops_mutation_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_subject.py").write_text(
                "import unittest\nclass Check(unittest.TestCase):\n def test_bad(self):\n  self.fail('baseline')\n"
            )
            with self.assertRaises(mc.BaselineRed):
                mc.check_baseline(root, ["test_subject.py"], ["test_subject"])


if __name__ == "__main__":
    unittest.main()
