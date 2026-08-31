#!/usr/bin/env python3
"""Deterministic offline acceptance tests for the canonical ablation harness."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


# Importing the sibling module must not leave __pycache__ in the repository.
sys.dont_write_bytecode = True
RUNNER_PATH = Path(__file__).with_name("runner.py")
SPEC = importlib.util.spec_from_file_location("fable_ablation_runner", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {RUNNER_PATH}")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


FROZEN_DIMENSIONS = ("tools", "agents", "mcp_servers")
PASSING_AUDIT = runner.SurfaceAudit(passed=True)


def init_event(
    *,
    skills: list[Any] | dict[str, Any] | None = None,
    tools: list[Any] | dict[str, Any] | None = None,
    agents: list[Any] | dict[str, Any] | None = None,
    mcp_servers: list[Any] | dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "system",
        "subtype": "init",
        "skills": ["reference-skill"] if skills is None else skills,
        "tools": ["Read", "Edit"] if tools is None else tools,
        "agents": ["general-purpose"] if agents is None else agents,
        "mcp_servers": ["local-test"] if mcp_servers is None else mcp_servers,
    }
    event.update(extra)
    return event


def evaluate(
    run_events: list[Any],
    reference_events: list[Any] | None = None,
    *,
    expected_fable: bool = False,
    audit: Any = PASSING_AUDIT,
) -> Any:
    reference = reference_events or [init_event()]
    return runner.evaluate_purity(
        run_events,
        reference,
        expected_fable_engaged=expected_fable,
        frozen_dimensions=FROZEN_DIMENSIONS,
        surface_audit=audit,
    )


def git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr}"
        )
    return result.stdout.strip()


def make_git_repository(root: Path) -> tuple[str, str]:
    git(root, "init", "-q")
    git(root, "config", "user.name", "Offline Test")
    git(root, "config", "user.email", "offline@example.invalid")
    (root / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-q", "-m", "baseline")
    return git(root, "rev-parse", "HEAD"), git(root, "rev-parse", "HEAD^{tree}")


def synthetic_manifest(provider_binary: str = "provider-binary-must-not-run") -> dict[str, Any]:
    schedule = [
        {
            "run_id": "s01-A01-r0-OFF",
            "condition": "OFF",
            "task_id": "A01",
            "run_path": "runs/s01-A01-r0-OFF/work",
        },
        {
            "run_id": "s02-A01-r0-ON",
            "condition": "ON",
            "task_id": "A01",
            "run_path": "runs/s02-A01-r0-ON/work",
        },
    ]
    return {
        "schedule": schedule,
        "tasks": [
            {
                "task_id": "A01",
                "prompt": "Repair the local fixture and run its offline checks.",
                "prompt_is_identical_for_both_conditions": True,
            }
        ],
        "treatment": {
            "off": {
                "label": "FABLE_OFF",
                "command": [provider_binary, "--stream-json"],
                "run_root_contains_carrier": False,
            },
            "on": {
                "label": "FABLE_ON",
                "command": [provider_binary, "--stream-json"],
                "run_root_contains_carrier": True,
            },
        },
        "purity_gate": {
            "must_be_exactly_equal_between_reference_and_run": list(FROZEN_DIMENSIONS)
        },
    }


class CanonicalHarnessTests(unittest.TestCase):
    def test_01_on_off_workspace_identity_is_condition_neutral(self) -> None:
        manifest = synthetic_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            off_slot = runner.select_manifest_slot(manifest, "s01-A01-r0-OFF")
            on_slot = runner.select_manifest_slot(manifest, "s02-A01-r0-ON")
            off_workspace, off_id = runner.create_condition_neutral_workspace(
                parent, lambda: "0123456789abcdef0123456789abcdef"
            )
            on_workspace, on_id = runner.create_condition_neutral_workspace(
                parent, lambda: "fedcba9876543210fedcba9876543210"
            )
            off_invocation = runner.build_model_invocation(off_slot, off_workspace, off_id)
            on_invocation = runner.build_model_invocation(on_slot, on_workspace, on_id)
            off_audit = runner.audit_model_visible_surfaces(
                off_invocation, off_workspace, off_slot.forbidden_model_tokens
            )
            on_audit = runner.audit_model_visible_surfaces(
                on_invocation, on_workspace, on_slot.forbidden_model_tokens
            )

            self.assertNotEqual(off_workspace.name, on_workspace.name)
            self.assertEqual(off_workspace.name, f"session-{off_id}")
            self.assertEqual(on_workspace.name, f"session-{on_id}")
            for workspace in (off_workspace, on_workspace):
                self.assertNotIn("ON", workspace.name)
                self.assertNotIn("OFF", workspace.name)
                self.assertNotIn("FABLE_ON", str(workspace))
                self.assertNotIn("FABLE_OFF", str(workspace))
            self.assertTrue(off_audit.passed, off_audit)
            self.assertTrue(on_audit.passed, on_audit)
            self.assertEqual(off_invocation.argv, on_invocation.argv)
            self.assertFalse(hasattr(off_invocation, "condition"))
            self.assertFalse(hasattr(on_invocation, "condition"))

            asymmetric = synthetic_manifest()
            asymmetric["treatment"]["on"]["command"].append("opaque-but-reversible-alias")
            with self.assertRaises(runner.HarnessContractError):
                runner.select_manifest_slot(asymmetric, "s02-A01-r0-ON")

    def test_02_actual_system_init_shape_is_detected(self) -> None:
        evidence = runner.parse_init_events(
            [
                {"type": "system", "subtype": "init"},
                {"type": "system", "subtype": "status"},
            ]
        )
        self.assertTrue(evidence.init_event_found)
        self.assertTrue(evidence.valid)
        self.assertEqual(evidence.candidate_count, 1)

    def test_03_missing_init_fails_purity(self) -> None:
        result = evaluate([{"type": "assistant", "message": {}}])
        self.assertFalse(result.init_event_found)
        self.assertFalse(result.purity_pass)
        self.assertFalse(result.run_countable)

    def test_04_malformed_init_fails_purity(self) -> None:
        result = evaluate(["{not-json"])
        self.assertFalse(result.purity_pass)
        self.assertFalse(result.run_countable)
        self.assertTrue(any("unparseable_event" in reason for reason in result.reasons))

    def test_05_wrong_init_subtype_fails_purity(self) -> None:
        wrong = init_event()
        wrong["subtype"] = "initialize"
        result = evaluate([wrong])
        self.assertFalse(result.init_event_found)
        self.assertFalse(result.purity_pass)
        self.assertTrue(any("wrong_init_subtype" in reason for reason in result.reasons))

    def test_06_multiple_init_candidates_are_ambiguous(self) -> None:
        result = evaluate([init_event(), init_event()])
        self.assertFalse(result.purity_pass)
        self.assertFalse(result.run_countable)
        self.assertTrue(any("ambiguous_init_events" in reason for reason in result.reasons))

    def test_07_fable_in_paths_cannot_set_fable_engaged(self) -> None:
        event = init_event(
            skills=[],
            cwd="/tmp/fable-ablation/fable-method/work",
            task_visible_run_id="fable-method-path-only",
            arbitrary="fable-method",
        )
        evidence = runner.parse_init_events([event], FROZEN_DIMENSIONS)
        self.assertTrue(evidence.valid)
        self.assertTrue(evidence.skills_resolved)
        self.assertFalse(evidence.fable_engaged)

    def test_08_off_semantic_init_without_fable_is_false(self) -> None:
        result = evaluate([init_event()])
        self.assertFalse(result.fable_engaged)
        self.assertTrue(result.purity_pass, result.reasons)
        self.assertTrue(result.run_countable)

    def test_09_on_semantic_init_with_fable_is_true(self) -> None:
        run = init_event(skills=["reference-skill", {"name": "fable-method"}])
        result = evaluate([run], expected_fable=True)
        self.assertTrue(result.fable_engaged)
        self.assertTrue(result.purity_pass, result.reasons)
        self.assertTrue(result.run_countable)

    def test_10_unexpected_tool_inventory_asymmetry_fails(self) -> None:
        run = init_event(tools=["Read", "Edit", "UnexpectedTool"])
        result = evaluate([run])
        self.assertFalse(result.dimension_matches["tools"])
        self.assertFalse(result.purity_pass)
        self.assertFalse(result.run_countable)

    def test_11_agent_and_mcp_asymmetry_each_fail(self) -> None:
        cases = {
            "agents": init_event(agents=["general-purpose", "unexpected-agent"]),
            "mcp_servers": init_event(mcp_servers=["local-test", "unexpected-mcp"]),
        }
        for dimension, event in cases.items():
            with self.subTest(dimension=dimension):
                result = evaluate([event])
                self.assertFalse(result.dimension_matches[dimension])
                self.assertFalse(result.purity_pass)
                self.assertFalse(result.run_countable)

    def test_12_clean_git_repository_is_reported_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            head, tree = make_git_repository(root)
            state = runner.capture_git_state(root)
            self.assertTrue(state.state_resolved, state.errors)
            self.assertEqual(state.final_head, head)
            self.assertEqual(state.final_head_tree, tree)
            self.assertFalse(state.final_worktree_dirty)
            self.assertFalse(state.tracked_diff_present)
            self.assertFalse(state.untracked_present)
            self.assertEqual(state.filesystem_state_representation, "HEAD_TREE_AND_CLEAN_WORKTREE")

    def test_13_tracked_modification_is_reported_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_git_repository(root)
            (root / "tracked.txt").write_text("modified\n", encoding="utf-8")
            state = runner.capture_git_state(root)
            self.assertTrue(state.state_resolved, state.errors)
            self.assertTrue(state.final_worktree_dirty)
            self.assertTrue(state.tracked_diff_present)
            self.assertFalse(state.untracked_present)

    def test_14_untracked_file_is_reported_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_git_repository(root)
            (root / "untracked.txt").write_text("new\n", encoding="utf-8")
            state = runner.capture_git_state(root)
            self.assertTrue(state.state_resolved, state.errors)
            self.assertTrue(state.final_worktree_dirty)
            self.assertFalse(state.tracked_diff_present)
            self.assertTrue(state.untracked_present)

    def test_15_tracked_and_untracked_changes_are_both_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_git_repository(root)
            (root / "tracked.txt").write_text("modified\n", encoding="utf-8")
            (root / "untracked.txt").write_text("new\n", encoding="utf-8")
            state = runner.capture_git_state(root)
            self.assertTrue(state.state_resolved, state.errors)
            self.assertTrue(state.final_worktree_dirty)
            self.assertTrue(state.tracked_diff_present)
            self.assertTrue(state.untracked_present)

    def test_16_dirty_state_is_never_represented_only_by_head_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, tree = make_git_repository(root)
            (root / "tracked.txt").write_text("modified\n", encoding="utf-8")
            (root / "untracked.txt").write_text("new\n", encoding="utf-8")
            state = runner.capture_git_state(root)
            record = state.as_record()
            self.assertEqual(record["final_head_tree"], tree)
            self.assertTrue(record["final_worktree_dirty"])
            self.assertTrue(record["tracked_diff_present"])
            self.assertTrue(record["untracked_present"])
            self.assertEqual(
                record["filesystem_state_representation"],
                "HEAD_TREE_PLUS_WORKTREE_CHANGES",
            )

    def test_17_invalid_or_unresolved_purity_defaults_non_countable(self) -> None:
        default = runner.PurityResult()
        unresolved = runner.evaluate_purity(
            [],
            [],
            expected_fable_engaged=False,
            frozen_dimensions=(),
            surface_audit=None,
        )
        self.assertFalse(default.purity_pass)
        self.assertFalse(default.run_countable)
        self.assertFalse(unresolved.purity_pass)
        self.assertFalse(unresolved.run_countable)
        with tempfile.TemporaryDirectory() as temporary:
            git_state = runner.capture_git_state(temporary)
            self.assertFalse(git_state.state_resolved)
            self.assertIsNone(git_state.final_worktree_dirty)

    def test_18_injected_executor_does_not_invoke_real_provider(self) -> None:
        manifest = synthetic_manifest("definitely-not-an-installed-provider")
        executor_calls: list[Any] = []

        def materializer(plan: Any) -> None:
            root = Path(plan.workspace_path)
            make_git_repository(root)
            skill_root = root / ".claude" / "skills" / "fable-method"
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text("offline fixture\n", encoding="utf-8")
            git(root, "add", ".claude/skills/fable-method/SKILL.md")
            git(root, "commit", "-q", "-m", "materialize treatment")

        def fake_executor(invocation: Any) -> list[dict[str, Any]]:
            executor_calls.append(invocation)
            return [init_event(skills=["reference-skill", "fable-method"])]

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.execute_manifest_slot(
                manifest,
                "s02-A01-r0-ON",
                workspace_parent=temporary,
                reference_events=[init_event()],
                materializer=materializer,
                executor=fake_executor,
                opaque_id_factory=lambda: "00112233445566778899aabbccddeeff",
            )

        self.assertEqual(len(executor_calls), 1)
        self.assertEqual(executor_calls[0].argv[0], "definitely-not-an-installed-provider")
        self.assertFalse(hasattr(executor_calls[0], "condition"))
        self.assertTrue(evidence.executor_called)
        self.assertIsNone(evidence.executor_error)
        self.assertTrue(evidence.purity.purity_pass, evidence.purity.reasons)
        self.assertTrue(evidence.run_countable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
