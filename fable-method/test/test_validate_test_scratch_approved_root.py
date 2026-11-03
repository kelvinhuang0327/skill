#!/usr/bin/env python3
"""Exercise approved test scratch roots with isolated Git repositories."""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest


CONTROL = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fable_artifact_controls.py"


def run_git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        raise AssertionError("git {} failed: {}".format(" ".join(args), result.stderr))
    return result.stdout.strip()


class ApprovedScratchRootTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="approved-scratch-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        run_git(self.repo, "init", "-q")
        run_git(self.repo, "config", "user.name", "Fable Test")
        run_git(self.repo, "config", "user.email", "fable-test@example.invalid")
        (self.repo / ".gitignore").write_text("approved-scratch/\nlinked-scratch/\n")
        (self.repo / "tracked.txt").write_text("fixture\n")
        run_git(self.repo, "add", ".gitignore", "tracked.txt")
        run_git(self.repo, "commit", "-qm", "fixture")
        self.approved = self.repo / "approved-scratch"
        self.approved.mkdir()
        self.scratch = self.approved / "pytest"

        self.linked = self.root / "linked"
        run_git(self.repo, "worktree", "add", "-qb", "linked-test", str(self.linked))

    def validate(self, path, approved_root=None):
        command = [
            "python3", "-B", str(CONTROL), "validate-test-scratch",
            "--repo", str(self.repo), "--path", str(path),
        ]
        if approved_root is not None:
            command.extend(["--approved-root", str(approved_root)])
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            self.fail("validator did not return JSON: {}".format(result.stdout + result.stderr))
        self.assertEqual(result.returncode, 0 if payload["status"] == "PASS" else 2)
        return payload

    def test_p1_approved_ignored_root_passes_and_reports_checks(self):
        result = self.validate(self.scratch, self.approved)
        self.assertEqual(result["status"], "PASS", result["violations"])
        self.assertEqual(result["approved_root"]["status"], "PASS")
        self.assertTrue(all(item["passed"] for item in result["approved_root"]["checks"].values()))

    def test_n1_same_path_without_approval_is_base_blocked_result(self):
        result = self.validate(self.scratch)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["violations"], [
            "test scratch path is inside the repository worktree",
            "test scratch path is inside a Git worktree",
        ])
        self.assertNotIn("approved_root", result)

    def test_n2_root_must_be_ignored(self):
        root = self.repo / "ordinary-scratch"
        root.mkdir()
        result = self.validate(root / "pytest", root)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["approved_root"]["checks"]["ignored_in_main_worktree"]["passed"])
        self.assertTrue(any("must be ignored by Git" in item for item in result["violations"]))

    def test_n3_root_in_linked_worktree_is_blocked(self):
        root = self.linked / "linked-scratch"
        root.mkdir()
        result = self.validate(root / "pytest", root)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["approved_root"]["checks"]["inside_main_worktree"]["passed"])
        self.assertFalse(result["approved_root"]["checks"]["outside_linked_worktrees"]["passed"])

    def test_n4_repo_path_outside_approved_root_is_blocked(self):
        result = self.validate(self.repo / "ordinary", self.approved)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("inside the repository worktree" in item for item in result["violations"]))

    def test_n5_protected_b649_case_variants_are_blocked(self):
        for component in ("b649", "B649"):
            with self.subTest(component=component):
                result = self.validate(self.approved / component / "pytest", self.approved)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertTrue(any("B649" in item for item in result["violations"]))

    def test_n6_lotterynew_component_is_blocked(self):
        result = self.validate(self.approved / "LotteryNew" / "pytest", self.approved)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("LotteryNew" in item for item in result["violations"]))

    def test_n7_root_parent_component_and_symlink_are_blocked(self):
        parent_root = str(self.repo) + "/discard/../approved-scratch"
        parent_result = self.validate(self.scratch, parent_root)
        self.assertEqual(parent_result["status"], "BLOCKED")
        self.assertFalse(parent_result["approved_root"]["checks"]["no_parent_components"]["passed"])

        link = self.repo / "approved-link"
        os.symlink(self.approved, link)
        link_result = self.validate(link / "pytest", link)
        self.assertEqual(link_result["status"], "BLOCKED")
        self.assertFalse(link_result["approved_root"]["checks"]["no_symlinks"]["passed"])

    def test_n8_linked_worktree_path_is_blocked_with_valid_root(self):
        result = self.validate(self.linked / "linked-scratch" / "pytest", self.approved)
        self.assertEqual(result["approved_root"]["status"], "PASS")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("Git worktree" in item for item in result["violations"]))

    def test_n9_no_argument_results_match_base_cases(self):
        neutral = self.root / "neutral-scratch"
        cases = [
            (neutral, "PASS", []),
            (self.repo / "ordinary", "BLOCKED", [
                "test scratch path is inside the repository worktree",
                "test scratch path is inside a Git worktree",
            ]),
            (self.root / "B649" / "pytest", "BLOCKED", ["test scratch path contains protected B649 token"]),
        ]
        for path, status, violations in cases:
            with self.subTest(path=str(path)):
                result = self.validate(path)
                self.assertEqual((result["status"], result["violations"]), (status, violations))


if __name__ == "__main__":
    unittest.main(verbosity=2)
