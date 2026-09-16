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


def instruction_surface_block(**overrides: Any) -> dict[str, Any]:
    """The five required instruction surfaces with representative values.

    ``hooks``/``agents_md``/``user_rules`` default to explicit empty lists --
    a legitimate observed value, distinct from the surface being absent --
    and ``instruction_sources`` defaults to one non-treatment source.
    """

    block: dict[str, Any] = {
        "output_style": "default",
        "hooks": [],
        "agents_md": [],
        "user_rules": [],
        "instruction_sources": ["cli-default"],
    }
    block.update(overrides)
    return block


def carrier_init_event(*, carrier: bool, skills: list[Any] | None = None, **surface_overrides: Any) -> dict[str, Any]:
    """A condition-neutral init event carrying every required surface.

    ``carrier=False`` is the OFF-shaped skill inventory (no fable-method);
    ``carrier=True`` adds exactly the pinned carrier on top of the same
    baseline skills, matching how the real OFF/ON arms are meant to differ.
    """

    baseline = ["reference-skill"] if skills is None else list(skills)
    resolved_skills = [*baseline, runner.FABLE_SKILL_IDENTITY] if carrier else baseline
    return init_event(skills=resolved_skills, **instruction_surface_block(**surface_overrides))


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

        passing_global_evidence = {
            "claude": {
                "OFF": [carrier_init_event(carrier=False)],
                "ON": [carrier_init_event(carrier=True)],
            }
        }

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.execute_manifest_slot(
                manifest,
                "s02-A01-r0-ON",
                workspace_parent=temporary,
                reference_events=[init_event()],
                materializer=materializer,
                executor=fake_executor,
                provider_instruction_surface_events=passing_global_evidence,
                expected_providers=("claude",),
                opaque_id_factory=lambda: "00112233445566778899aabbccddeeff",
            )

        self.assertEqual(len(executor_calls), 1)
        self.assertEqual(executor_calls[0].argv[0], "definitely-not-an-installed-provider")
        self.assertFalse(hasattr(executor_calls[0], "condition"))
        self.assertTrue(evidence.executor_called)
        self.assertIsNone(evidence.executor_error)
        self.assertTrue(evidence.purity.purity_pass, evidence.purity.reasons)
        self.assertTrue(
            evidence.global_instruction_surface.passed, evidence.global_instruction_surface.reasons
        )
        self.assertTrue(evidence.run_countable)


SURFACES_WITH_ALTERNATE_ALIAS = {
    "output_style": "outputStyle",
    "agents_md": "agentsMd",
    "user_rules": "userRules",
    "instruction_sources": "instructionSources",
}


class GlobalInstructionSurfaceGateTests(unittest.TestCase):
    """Focused acceptance for the runner-owned global instruction-surface gate."""

    # -- Required-surface matrix / content binding ------------------------

    def test_01_both_arms_observed_and_equal_resolves_for_every_surface(self) -> None:
        off = carrier_init_event(carrier=False)
        on = carrier_init_event(carrier=True)
        off_evidence = runner.parse_init_events([off])
        on_evidence = runner.parse_init_events([on])

        self.assertTrue(off_evidence.instruction_surfaces_resolved, off_evidence.instruction_surface_errors)
        self.assertTrue(on_evidence.instruction_surfaces_resolved, on_evidence.instruction_surface_errors)
        self.assertEqual(set(off_evidence.instruction_surfaces), set(runner.INSTRUCTION_SURFACES))
        self.assertEqual(off_evidence.instruction_surfaces, on_evidence.instruction_surfaces)

    def test_02_both_arms_missing_a_surface_fails(self) -> None:
        for surface in runner.INSTRUCTION_SURFACES:
            with self.subTest(surface=surface):
                block = instruction_surface_block()
                del block[surface]
                event = init_event(**block)
                evidence = runner.parse_init_events([event])
                self.assertFalse(evidence.instruction_surfaces_resolved)
                self.assertIn(f"missing_instruction_surface:{surface}", evidence.instruction_surface_errors)

    def test_03_one_arm_missing_a_surface_fails(self) -> None:
        for surface in runner.INSTRUCTION_SURFACES:
            with self.subTest(surface=surface):
                complete = init_event(**instruction_surface_block())
                block = instruction_surface_block()
                del block[surface]
                incomplete = init_event(**block)
                self.assertTrue(runner.parse_init_events([complete]).instruction_surfaces_resolved)
                self.assertFalse(runner.parse_init_events([incomplete]).instruction_surfaces_resolved)

    def test_04_null_surface_value_fails(self) -> None:
        for surface in runner.INSTRUCTION_SURFACES:
            with self.subTest(surface=surface):
                event = init_event(**instruction_surface_block(**{surface: None}))
                evidence = runner.parse_init_events([event])
                self.assertFalse(evidence.instruction_surfaces_resolved)
                self.assertIn(f"null_instruction_surface:{surface}", evidence.instruction_surface_errors)

    def test_05_malformed_surface_value_fails(self) -> None:
        event = init_event(**instruction_surface_block(output_style=float("nan")))
        evidence = runner.parse_init_events([event])
        self.assertFalse(evidence.instruction_surfaces_resolved)
        self.assertIn("malformed_instruction_surface:output_style", evidence.instruction_surface_errors)

    def test_06_ambiguous_alias_fails_even_when_both_spellings_agree(self) -> None:
        for surface, alternate in SURFACES_WITH_ALTERNATE_ALIAS.items():
            with self.subTest(surface=surface):
                block = instruction_surface_block()
                block[alternate] = block[surface]
                event = init_event(**block)
                evidence = runner.parse_init_events([event])
                self.assertFalse(evidence.instruction_surfaces_resolved)
                self.assertIn(
                    f"ambiguous_instruction_surface_alias:{surface}", evidence.instruction_surface_errors
                )

    def test_07_explicit_empty_surfaces_on_both_arms_may_match(self) -> None:
        block = instruction_surface_block(
            hooks=[], agents_md=[], user_rules=[], instruction_sources=[]
        )
        off = init_event(**block)
        on = init_event(skills=["reference-skill", runner.FABLE_SKILL_IDENTITY], **block)
        off_evidence = runner.parse_init_events([off])
        on_evidence = runner.parse_init_events([on])
        self.assertTrue(off_evidence.instruction_surfaces_resolved)
        self.assertEqual(off_evidence.instruction_surfaces, on_evidence.instruction_surfaces)

    def test_08_content_drift_with_the_same_surface_present_is_detected(self) -> None:
        off = init_event(**instruction_surface_block(instruction_sources=["cli-default"]))
        on = init_event(**instruction_surface_block(instruction_sources=["cli-default-modified"]))
        off_evidence = runner.parse_init_events([off])
        on_evidence = runner.parse_init_events([on])
        self.assertNotEqual(
            off_evidence.instruction_surfaces["instruction_sources"],
            on_evidence.instruction_surfaces["instruction_sources"],
        )

    def test_09_source_order_change_is_detected_as_drift(self) -> None:
        off = init_event(**instruction_surface_block(instruction_sources=["cli-default", "enterprise-policy"]))
        on = init_event(**instruction_surface_block(instruction_sources=["enterprise-policy", "cli-default"]))
        off_evidence = runner.parse_init_events([off])
        on_evidence = runner.parse_init_events([on])
        self.assertNotEqual(
            off_evidence.instruction_surfaces["instruction_sources"],
            on_evidence.instruction_surfaces["instruction_sources"],
        )

    # -- Carrier ------------------------------------------------------------

    def test_10_carrier_off_absent_on_exact_addition_is_the_only_passing_shape(self) -> None:
        off_skills = frozenset({"reference-skill"})
        on_skills = frozenset({"reference-skill", runner.FABLE_SKILL_IDENTITY})
        self.assertTrue(
            runner._carrier_delta_is_exact_treatment(off_skills, on_skills, runner.FABLE_SKILL_IDENTITY)
        )

    def test_11_carrier_missing_from_on_fails(self) -> None:
        off_skills = frozenset({"reference-skill"})
        on_skills = frozenset({"reference-skill"})
        self.assertFalse(
            runner._carrier_delta_is_exact_treatment(off_skills, on_skills, runner.FABLE_SKILL_IDENTITY)
        )

    def test_12_carrier_present_in_off_fails(self) -> None:
        # The donor's symmetric-difference-only check (off ^ on <= {carrier})
        # wrongly accepts this shape, since the sets are identical and the
        # symmetric difference is empty.  The directional check must not.
        off_skills = frozenset({"reference-skill", runner.FABLE_SKILL_IDENTITY})
        on_skills = frozenset({"reference-skill", runner.FABLE_SKILL_IDENTITY})
        self.assertFalse(
            runner._carrier_delta_is_exact_treatment(off_skills, on_skills, runner.FABLE_SKILL_IDENTITY)
        )

    def test_13_additional_skill_delta_beyond_the_carrier_fails(self) -> None:
        off_skills = frozenset({"reference-skill"})
        on_skills = frozenset({"reference-skill", runner.FABLE_SKILL_IDENTITY, "unexpected-skill"})
        self.assertFalse(
            runner._carrier_delta_is_exact_treatment(off_skills, on_skills, runner.FABLE_SKILL_IDENTITY)
        )

    # -- Cross-provider -------------------------------------------------------

    def test_14_two_providers_locally_consistent_but_globally_different_fails(self) -> None:
        provider_events = {
            "provider-a": {
                "OFF": [carrier_init_event(carrier=False)],
                "ON": [carrier_init_event(carrier=True)],
            },
            "provider-b": {
                "OFF": [carrier_init_event(carrier=False, output_style="explanatory")],
                "ON": [carrier_init_event(carrier=True, output_style="explanatory")],
            },
        }
        result = runner.evaluate_global_instruction_surface(
            provider_events, expected_providers=("provider-a", "provider-b")
        )
        self.assertTrue(result.resolved)
        self.assertTrue(result.checks["provider_local_surfaces_equal:provider-a"])
        self.assertTrue(result.checks["provider_local_surfaces_equal:provider-b"])
        self.assertFalse(result.checks["cross_provider_instruction_surfaces_equal"])
        self.assertFalse(result.passed)

    def test_15_two_providers_with_identical_normalized_surfaces_pass(self) -> None:
        provider_events = {
            "provider-a": {
                "OFF": [carrier_init_event(carrier=False)],
                "ON": [carrier_init_event(carrier=True)],
            },
            "provider-b": {
                "OFF": [carrier_init_event(carrier=False)],
                "ON": [carrier_init_event(carrier=True)],
            },
        }
        result = runner.evaluate_global_instruction_surface(
            provider_events, expected_providers=("provider-a", "provider-b")
        )
        self.assertTrue(result.resolved)
        self.assertTrue(result.passed, result.reasons)

    def test_16_missing_expected_provider_is_unresolved_not_skipped(self) -> None:
        provider_events = {
            "provider-a": {
                "OFF": [carrier_init_event(carrier=False)],
                "ON": [carrier_init_event(carrier=True)],
            }
        }
        result = runner.evaluate_global_instruction_surface(
            provider_events, expected_providers=("provider-a", "provider-b")
        )
        self.assertFalse(result.resolved)
        self.assertFalse(result.passed)
        self.assertIn("missing_provider:provider-b", result.reasons)

    def test_17_missing_arm_for_an_expected_provider_is_unresolved(self) -> None:
        provider_events = {
            "provider-a": {"OFF": [carrier_init_event(carrier=False)]},
        }
        result = runner.evaluate_global_instruction_surface(
            provider_events, expected_providers=("provider-a",)
        )
        self.assertFalse(result.resolved)
        self.assertIn("missing_arm:provider-a:ON", result.reasons)

    # -- Inventory independence / unavoidable integration --------------------

    def test_18_instruction_surface_pass_does_not_override_tools_inventory_drift(self) -> None:
        manifest = synthetic_manifest("definitely-not-an-installed-provider")

        def materializer(plan: Any) -> None:
            make_git_repository(Path(plan.workspace_path))

        def fake_executor(invocation: Any) -> list[dict[str, Any]]:
            return [init_event(skills=["reference-skill", runner.FABLE_SKILL_IDENTITY], tools=["Read", "Edit", "UnexpectedTool"])]

        passing_global_evidence = {
            "claude": {
                "OFF": [carrier_init_event(carrier=False)],
                "ON": [carrier_init_event(carrier=True)],
            }
        }

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.execute_manifest_slot(
                manifest,
                "s02-A01-r0-ON",
                workspace_parent=temporary,
                reference_events=[init_event()],
                materializer=materializer,
                executor=fake_executor,
                provider_instruction_surface_events=passing_global_evidence,
                expected_providers=("claude",),
                opaque_id_factory=lambda: "aa112233445566778899aabbccddeeff",
            )

        self.assertFalse(evidence.purity.dimension_matches["tools"])
        self.assertFalse(evidence.purity.purity_pass)
        self.assertTrue(
            evidence.global_instruction_surface.passed, evidence.global_instruction_surface.reasons
        )
        self.assertFalse(evidence.run_countable)

    def test_19_run_countable_false_when_global_gate_unresolved(self) -> None:
        manifest = synthetic_manifest("definitely-not-an-installed-provider")

        def materializer(plan: Any) -> None:
            make_git_repository(Path(plan.workspace_path))

        def fake_executor(invocation: Any) -> list[dict[str, Any]]:
            return [init_event(skills=["reference-skill", runner.FABLE_SKILL_IDENTITY])]

        incomplete_global_evidence = {
            "claude": {"OFF": [carrier_init_event(carrier=False)]},  # ON arm never supplied
        }

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.execute_manifest_slot(
                manifest,
                "s02-A01-r0-ON",
                workspace_parent=temporary,
                reference_events=[init_event()],
                materializer=materializer,
                executor=fake_executor,
                provider_instruction_surface_events=incomplete_global_evidence,
                expected_providers=("claude",),
                opaque_id_factory=lambda: "bb112233445566778899aabbccddeeff",
            )

        self.assertTrue(evidence.purity.purity_pass, evidence.purity.reasons)
        self.assertFalse(evidence.global_instruction_surface.resolved)
        self.assertFalse(evidence.run_countable)

    def test_20_run_countable_false_when_global_gate_resolved_but_failed(self) -> None:
        manifest = synthetic_manifest("definitely-not-an-installed-provider")

        def materializer(plan: Any) -> None:
            make_git_repository(Path(plan.workspace_path))

        def fake_executor(invocation: Any) -> list[dict[str, Any]]:
            return [init_event(skills=["reference-skill", runner.FABLE_SKILL_IDENTITY])]

        failing_global_evidence = {
            "claude": {
                "OFF": [carrier_init_event(carrier=False)],
                "ON": [carrier_init_event(carrier=True, output_style="explanatory")],
            }
        }

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.execute_manifest_slot(
                manifest,
                "s02-A01-r0-ON",
                workspace_parent=temporary,
                reference_events=[init_event()],
                materializer=materializer,
                executor=fake_executor,
                provider_instruction_surface_events=failing_global_evidence,
                expected_providers=("claude",),
                opaque_id_factory=lambda: "cc112233445566778899aabbccddeeff",
            )

        self.assertTrue(evidence.purity.purity_pass, evidence.purity.reasons)
        self.assertTrue(evidence.global_instruction_surface.resolved)
        self.assertFalse(evidence.global_instruction_surface.passed)
        self.assertFalse(evidence.run_countable)

    def test_21_donor_bug_shape_five_surfaces_absent_yet_run_countable_must_be_false(self) -> None:
        """Named regression: the donor computed a rich condition-neutrality
        comparison but never wired it into execute_manifest_slot's own
        countability, so a run with zero instruction-surface evidence could
        still finish with run_countable=True.  This must fail closed here."""

        manifest = synthetic_manifest("definitely-not-an-installed-provider")

        def materializer(plan: Any) -> None:
            make_git_repository(Path(plan.workspace_path))

        def fake_executor(invocation: Any) -> list[dict[str, Any]]:
            return [init_event(skills=["reference-skill", runner.FABLE_SKILL_IDENTITY])]

        # init_event() carries none of the five required instruction
        # surfaces -- the exact donor shape.
        donor_shaped_evidence = {
            "claude": {
                "OFF": [init_event()],
                "ON": [init_event(skills=["reference-skill", runner.FABLE_SKILL_IDENTITY])],
            }
        }

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.execute_manifest_slot(
                manifest,
                "s02-A01-r0-ON",
                workspace_parent=temporary,
                reference_events=[init_event()],
                materializer=materializer,
                executor=fake_executor,
                provider_instruction_surface_events=donor_shaped_evidence,
                expected_providers=("claude",),
                opaque_id_factory=lambda: "dd112233445566778899aabbccddeeff",
            )

        self.assertIsNone(evidence.materializer_error)
        self.assertTrue(evidence.executor_called)
        self.assertIsNone(evidence.executor_error)
        self.assertTrue(evidence.git_state.state_resolved)
        self.assertTrue(evidence.purity.purity_pass, evidence.purity.reasons)
        self.assertFalse(evidence.global_instruction_surface.resolved)
        self.assertFalse(evidence.run_countable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
