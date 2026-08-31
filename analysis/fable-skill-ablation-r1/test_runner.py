#!/usr/bin/env python3
"""Deterministic offline acceptance tests for the canonical ablation harness."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
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

COMPILER_PATH = Path(__file__).with_name("build_epoch_manifest.py")
COMPILER_SPEC = importlib.util.spec_from_file_location(
    "fable_ablation_epoch_compiler", COMPILER_PATH
)
if COMPILER_SPEC is None or COMPILER_SPEC.loader is None:
    raise RuntimeError(f"cannot import {COMPILER_PATH}")
compiler = importlib.util.module_from_spec(COMPILER_SPEC)
sys.modules[COMPILER_SPEC.name] = compiler
COMPILER_SPEC.loader.exec_module(compiler)

# The identity of the runner implementation these tests actually execute,
# derived without calling the runner's own helper so the self-binding gate is
# checked against an outside oracle rather than against itself.
RUNNER_BYTES = RUNNER_PATH.read_bytes()
ACTUAL_RUNNER_SHA256 = hashlib.sha256(RUNNER_BYTES).hexdigest()
ACTUAL_RUNNER_BLOB_OID = hashlib.sha1(
    b"blob " + str(len(RUNNER_BYTES)).encode("ascii") + b"\x00" + RUNNER_BYTES
).hexdigest()


FROZEN_DIMENSIONS = ("tools", "agents", "mcp_servers")
PASSING_AUDIT = runner.SurfaceAudit(passed=True)
CONTENT_DIMENSION = "skill_carrier_digests"

# Every non-treatment global instruction surface this harness can classify, set
# to an identical value so any single-dimension delta is the only variable.
GLOBAL_SURFACE: dict[str, Any] = {
    "plugins": ["local-plugin"],
    "permission_mode": "acceptEdits",
    "output_style": "default",
    "host_version": "offline-host-0",
    "hooks": {"PreToolUse": [], "SessionStart": []},
    "agents_md": [{"path": "AGENTS.md", "sha256": "a" * 64}],
    "user_rules": [],
    "instruction_sources": ["project", "user"],
}
NON_TREATMENT_DIMENSIONS = tuple(GLOBAL_SURFACE) + FROZEN_DIMENSIONS


def global_surface_event(**overrides: Any) -> dict[str, Any]:
    """An init event that resolves every classifiable global surface."""

    surface = dict(GLOBAL_SURFACE)
    skills = overrides.pop("skills", None)
    surface.update(overrides)
    return init_event(skills=skills, **surface)


def content_delta_treatment(
    expected_treatment_delta: bool, identity: str = "fable-method"
) -> Any:
    return runner.TreatmentSurface(
        dimension=CONTENT_DIMENSION,
        mode=runner.TREATMENT_MODE_CONTENT_DELTA,
        identity=identity,
        baseline_contains_identity=True,
        expected_treatment_delta=expected_treatment_delta,
    )


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
    frozen_dimensions: Any = FROZEN_DIMENSIONS,
    treatment_surface: Any = None,
) -> Any:
    reference = reference_events or [init_event()]
    return runner.evaluate_purity(
        run_events,
        reference,
        expected_fable_engaged=expected_fable,
        frozen_dimensions=frozen_dimensions,
        surface_audit=audit,
        treatment_surface=treatment_surface,
    )


def provider_execution(
    invocation: Any, records: list[dict[str, Any]]
) -> Any:
    """Build sealed offline provider evidence for generic harness tests."""

    prompt_bytes = invocation.prompt.encode("utf-8")
    environment = tuple(
        runner.EnvironmentValueEvidence(
            key=key,
            value="REDACTED",
            sha256_fingerprint=hashlib.sha256(value.encode("utf-8")).hexdigest(),
            redacted=True,
        )
        for key, value in sorted(invocation.environment.items())
    )
    raw_stdout = b"\n".join(
        json.dumps(record, separators=(",", ":")).encode("utf-8")
        for record in records
    ) + b"\n"
    terminal = records[-1]
    execution = runner.ProviderExecution(
        adapter_identity=runner.AdapterIdentity(
            name="offline-test-adapter",
            version="1",
            source_realpath="/offline/test/adapter.py",
            source_sha256="a" * 64,
        ),
        provider_binary_identity=runner.ProviderBinaryIdentity(
            executable=invocation.argv[0],
            realpath="/offline/test/provider",
            sha256="b" * 64,
            version="offline-test",
        ),
        provider_policy_identity="c" * 64,
        provider_policy={"identity": "offline-test-policy"},
        invocation=runner.ProviderInvocationEvidence(
            argv=tuple(invocation.argv),
            cwd=invocation.cwd,
            task_visible_run_id=invocation.task_visible_run_id,
            stdin_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
            stdin_length=len(prompt_bytes),
            environment=environment,
            shell=False,
            close_fds=True,
            start_new_session=True,
            timeout_seconds=1.0,
        ),
        process=runner.ProcessEvidence(
            pid=1234,
            pgid=1234,
            returncode=0,
            status="EXITED",
            timed_out=False,
            termination_attempted=False,
            termination_method=None,
            signals_sent=(),
            shell=False,
            close_fds=True,
            start_new_session=True,
            timeout_seconds=1.0,
        ),
        raw_stdout=raw_stdout,
        raw_stderr=b"",
        raw_jsonl_records=tuple(records),
        init_index=0,
        assistant_message_ids=tuple(
            record["message"]["id"]
            for record in records
            if record.get("type") == "assistant"
        ),
        terminal_result_index=len(records) - 1,
        session_id="offline-session",
        model_usage=terminal["modelUsage"],
        total_cost_usd=Decimal(str(terminal["total_cost_usd"])),
        validation_errors=(),
    )
    return execution.sealed()


def git_blob_oid(payload: bytes) -> str:
    """Git's own blob id for exact bytes, independent of the runner's helper."""

    result = subprocess.run(
        ["git", "hash-object", "--stdin"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git hash-object failed: {result.stderr!r}")
    return result.stdout.decode("ascii").strip()


def final_runtime_authority(
    *,
    blob_oid: str | None = ACTUAL_RUNNER_BLOB_OID,
    sha256: str | None = ACTUAL_RUNNER_SHA256,
) -> dict[str, Any]:
    """A final_runtime_authority block in the compiled manifest's own shape."""

    def component(oid: str | None, digest: str | None) -> dict[str, Any]:
        pinned = oid is not None and digest is not None
        return {
            "status": "PINNED" if pinned else "UNRESOLVED",
            "blob_oid": oid,
            "sha256": digest,
        }

    return {
        "status": "PINNED",
        "publication_state": "PUBLISHED_DESCENDANT_PINNED",
        "commit": "1" * 40,
        "tree": "2" * 40,
        "components": {
            "runner": component(blob_oid, sha256),
            "controller": component("3" * 40, "d" * 64),
            "sandbox": component("4" * 40, "e" * 64),
        },
    }


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
        "final_runtime_authority": final_runtime_authority(),
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

        def fake_executor(invocation: Any) -> Any:
            executor_calls.append(invocation)
            return provider_execution(
                invocation,
                [
                    init_event(
                        skills=["reference-skill", "fable-method"],
                        session_id="offline-session",
                    ),
                    {
                        "type": "assistant",
                        "session_id": "offline-session",
                        "message": {"id": "msg-offline", "model": "offline-model"},
                    },
                    {
                        "type": "result",
                        "subtype": "success",
                        "session_id": "offline-session",
                        "modelUsage": {"offline-model": {"input_tokens": 1}},
                        "total_cost_usd": "0.0",
                    },
                ],
            )

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
        self.assertIsNotNone(evidence.provider_execution)
        self.assertTrue(evidence.provider_execution.evidence_sealed)
        self.assertTrue(evidence.provider_execution.run_countable)
        self.assertTrue(evidence.purity.purity_pass, evidence.purity.reasons)
        self.assertTrue(evidence.run_countable)


    def _complete_execution(self, invocation: Any | None = None) -> Any:
        if invocation is None:
            invocation = runner.ModelInvocation(
                argv=("/offline/provider", "--stream-json"),
                cwd="/opaque/session-00112233445566778899aabbccddeeff",
                prompt="Offline fixture.",
                environment={},
                task_visible_run_id="session-00112233445566778899aabbccddeeff",
            )
        return provider_execution(
            invocation,
            [
                init_event(session_id="offline-session"),
                {
                    "type": "assistant", "session_id": "offline-session",
                    "message": {"id": "msg-offline", "model": "offline-model"},
                },
                {
                    "type": "result", "subtype": "success", "is_error": False,
                    "session_id": "offline-session",
                    "modelUsage": {"offline-model": {"input_tokens": 1}},
                    "total_cost_usd": "0.0",
                },
            ],
        )

    def _execute_offline_slot(
        self,
        executor: Any,
        *,
        environment: Any = None,
        materializer: Any = None,
        manifest: Any = None,
    ) -> Any:
        def initialize(plan: Any) -> None:
            make_git_repository(Path(plan.workspace_path))

        with tempfile.TemporaryDirectory() as temporary:
            return runner.execute_manifest_slot(
                synthetic_manifest() if manifest is None else manifest,
                "s01-A01-r0-OFF",
                workspace_parent=temporary,
                reference_events=[init_event()],
                materializer=initialize if materializer is None else materializer,
                executor=executor,
                environment=environment,
                opaque_id_factory=lambda: "00112233445566778899aabbccddeeff",
            )

    def test_19_a_seal_cannot_replace_missing_or_inconsistent_raw_evidence(self) -> None:
        execution = self._complete_execution()
        self.assertTrue(execution.run_countable)
        changes = [
            {"raw_stdout": b""}, {"raw_jsonl_records": ()},
            {"init_index": None}, {"init_index": 999}, {"init_index": True},
            {"terminal_result_index": None}, {"terminal_result_index": 0},
            {"assistant_message_ids": ()}, {"assistant_message_ids": ("invented-id",)},
            {"session_id": None}, {"session_id": ""}, {"session_id": "invented-session"},
            {"model_usage": None}, {"model_usage": {"offline-model": {"input_tokens": 2}}},
            {"total_cost_usd": None}, {"total_cost_usd": Decimal("1.0")},
        ]
        for change in changes:
            with self.subTest(change=change):
                invalid = replace(execution, **change).sealed()
                self.assertTrue(invalid.evidence_sealed)
                self.assertFalse(invalid.run_countable)
        unsealed = replace(execution, evidence_sha256=None)
        self.assertFalse(unsealed.run_countable)

    def test_20_terminal_raw_evidence_cannot_borrow_valid_summary_fields(self) -> None:
        execution = self._complete_execution()
        mutations = [
            {"subtype": "error_max_turns"}, {"subtype": []}, {"is_error": True},
            {"modelUsage": {}}, {"modelUsage": {"offline-model": {}}},
            {"modelUsage": {"offline-model": {"input_tokens": -1}}},
            {"total_cost_usd": None}, {"total_cost_usd": "NaN"},
            {"total_cost_usd": "-0.1"}, {"num_turns": -1},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                records = [dict(record) for record in execution.raw_jsonl_records]
                records[-1].update(mutation)
                raw = b"\n".join(json.dumps(record).encode() for record in records) + b"\n"
                invalid = replace(execution, raw_stdout=raw, raw_jsonl_records=tuple(records)).sealed()
                self.assertTrue(invalid.evidence_sealed)
                self.assertFalse(invalid.run_countable)

    def test_21_missing_process_and_provider_identity_evidence_fail_closed(self) -> None:
        execution = self._complete_execution()
        invalid_processes = [
            replace(execution.process, status="START_FAILED"),
            replace(execution.process, returncode=False),
            replace(execution.process, returncode=None),
            replace(execution.process, timed_out=True),
            replace(execution.process, pid=None),
            replace(execution.process, pgid=None),
            replace(execution.process, shell=True),
            replace(execution.process, close_fds=False),
            replace(execution.process, start_new_session=False),
        ]
        for process in invalid_processes:
            with self.subTest(process=process):
                self.assertFalse(replace(execution, process=process).sealed().run_countable)
        self.assertFalse(
            replace(execution, provider_binary_identity=replace(
                execution.provider_binary_identity, sha256=None
            )).sealed().run_countable
        )
        self.assertFalse(
            replace(execution, adapter_identity=replace(
                execution.adapter_identity, source_realpath=None
            )).sealed().run_countable
        )
        self.assertFalse(replace(execution, provider_policy={}).sealed().run_countable)

    def test_22_malformed_or_duplicate_raw_json_cannot_be_countable(self) -> None:
        execution = self._complete_execution()
        duplicate = execution.raw_stdout.replace(
            b'"type":"result"', b'"type":"assistant","type":"result"'
        )
        for raw in (duplicate, b"\xff\n" + execution.raw_stdout, b"\n" + execution.raw_stdout):
            with self.subTest(raw_length=len(raw)):
                self.assertFalse(replace(execution, raw_stdout=raw).sealed().run_countable)
        malformed = replace(execution, raw_jsonl_records=(None,))
        self.assertFalse(malformed.evidence_sealed)
        self.assertFalse(malformed.run_countable)

    def test_23_nested_session_and_subagent_evidence_cannot_be_ignored(self) -> None:
        execution = self._complete_execution()
        extras = [
            {"type": "system", "subtype": "status", "session_id": "different-session"},
            {"type": "assistant", "subagent": True, "session_id": "different-session",
             "message": {"id": "msg-subagent", "model": "offline-model"}},
        ]
        for extra in extras:
            with self.subTest(extra=extra):
                records = list(execution.raw_jsonl_records)
                records.insert(2, extra)
                invalid = provider_execution(
                    runner.ModelInvocation(
                        execution.invocation.argv, execution.invocation.cwd, "Offline fixture.", {},
                        execution.invocation.task_visible_run_id,
                    ),
                    records,
                )
                self.assertTrue(invalid.evidence_sealed)
                self.assertFalse(invalid.run_countable)

    def test_24_provider_evidence_must_match_the_actual_harness_invocation(self) -> None:
        for field in ("argv", "cwd", "prompt", "environment", "task_visible_run_id"):
            with self.subTest(field=field):
                def mismatched(invocation: Any) -> Any:
                    values = {
                        "argv": ("/other/offline-provider",),
                        "cwd": "/other/session-00112233445566778899aabbccddeeff",
                        "prompt": "Different offline prompt.",
                        "environment": {"OFFLINE_SETTING": "different"},
                        "task_visible_run_id": "session-ffffffffffffffffffffffffffffffff",
                    }
                    return self._complete_execution(replace(invocation, **{field: values[field]}))

                evidence = self._execute_offline_slot(mismatched)
                self.assertTrue(evidence.purity.purity_pass)
                self.assertTrue(evidence.provider_execution.run_countable)
                self.assertFalse(evidence.run_countable)

    def test_25_boundary_failures_retain_sealed_evidence_without_secret_messages(self) -> None:
        secret = "synthetic-boundary-secret"

        def fail(_invocation: Any) -> Any:
            raise OSError(secret)

        for executor in (fail, lambda _: [init_event()]):
            with self.subTest(executor=executor.__name__):
                evidence = self._execute_offline_slot(
                    executor, environment={"CUSTOM_VALUE": secret}
                )
                self.assertTrue(evidence.executor_called)
                self.assertTrue(evidence.provider_execution.evidence_sealed)
                self.assertFalse(evidence.run_countable)
                self.assertNotIn(secret, json.dumps(evidence.as_record()))

    def test_26_generic_model_visible_record_redacts_environment_value_aliases(self) -> None:
        secret = "synthetic-visible-secret"
        invocation = runner.ModelInvocation(
            argv=("/offline/provider", secret), cwd=f"/opaque/{secret}",
            prompt=f"Prompt containing {secret}.",
            environment={"CUSTOM_VALUE": secret, secret: secret},
            task_visible_run_id=secret,
        )
        record = invocation.model_visible_record()
        self.assertNotIn(secret, json.dumps(record))
        self.assertEqual(
            record["environment"]["CUSTOM_VALUE"]["sha256_fingerprint"],
            hashlib.sha256(secret.encode()).hexdigest(),
        )
        boundary = runner._boundary_failure_execution(invocation, "offline_failure")
        self.assertTrue(boundary.evidence_sealed)
        self.assertNotIn(secret, json.dumps(boundary.as_record()))
        self.assertFalse(boundary.run_countable)

    def test_27_passing_purity_cannot_make_an_incomplete_provider_run_countable(self) -> None:
        def init_only(invocation: Any) -> Any:
            execution = self._complete_execution(invocation)
            records = execution.raw_jsonl_records[:1]
            return replace(
                execution,
                raw_stdout=json.dumps(records[0]).encode() + b"\n",
                raw_jsonl_records=records,
                terminal_result_index=None,
            ).sealed()

        evidence = self._execute_offline_slot(init_only)
        self.assertTrue(evidence.purity.purity_pass)
        self.assertTrue(evidence.provider_execution.evidence_sealed)
        self.assertFalse(evidence.provider_execution.run_countable)
        self.assertFalse(evidence.run_countable)

    def test_28_materializer_failure_does_not_leak_environment_values(self) -> None:
        secret = "synthetic-materializer-secret"

        def materializer(_plan: Any) -> None:
            raise OSError(secret)

        def forbidden_executor(_invocation: Any) -> Any:
            self.fail("executor must not run after materializer failure")

        evidence = self._execute_offline_slot(
            forbidden_executor, materializer=materializer,
            environment={"CUSTOM_VALUE": secret},
        )
        self.assertFalse(evidence.executor_called)
        self.assertFalse(evidence.run_countable)
        self.assertIn("OSError", evidence.materializer_error)
        self.assertNotIn(secret, json.dumps(evidence.as_record()))

    def test_29_invalid_unicode_environment_still_retains_boundary_evidence(self) -> None:
        def fail(_invocation: Any) -> Any:
            raise OSError("offline-only")

        evidence = self._execute_offline_slot(
            fail, environment={"CUSTOM_VALUE": "\ud800"}
        )
        self.assertTrue(evidence.provider_execution.evidence_sealed)
        self.assertFalse(evidence.run_countable)
        self.assertEqual(
            evidence.provider_execution.invocation.environment[0].sha256_fingerprint,
            hashlib.sha256("\ud800".encode("utf-8", errors="surrogatepass")).hexdigest(),
        )
        json.dumps(evidence.as_record(), allow_nan=False)

    def test_30_identical_non_treatment_surfaces_pass(self) -> None:
        reference = global_surface_event(skills=["reference-skill"])
        run = global_surface_event(skills=["reference-skill", {"name": "fable-method"}])
        result = evaluate([run], [reference], expected_fable=True)
        surface = result.global_surface

        self.assertTrue(result.purity_pass, result.reasons)
        self.assertTrue(result.run_countable)
        self.assertTrue(surface.passed, surface.reasons)
        self.assertEqual(
            surface.dimension_states[runner.SKILL_INVENTORY_DIMENSION],
            runner.SURFACE_ALLOWED_TREATMENT_DELTA,
        )
        self.assertEqual(surface.treatment_delta_kind, "IDENTITY_DELTA")
        self.assertTrue(surface.allowed_treatment_delta)
        self.assertFalse(surface.identity_equality)
        for dimension in NON_TREATMENT_DIMENSIONS:
            with self.subTest(dimension=dimension):
                self.assertEqual(
                    surface.dimension_states[dimension], runner.SURFACE_EQUAL
                )
        self.assertEqual(surface.differing_dimensions, ())
        self.assertEqual(surface.unresolved_dimensions, ())
        json.dumps(result.as_record(), allow_nan=False)

    def test_31_unrelated_plugin_delta_fails_closed(self) -> None:
        reference = global_surface_event(skills=["reference-skill"])
        run = global_surface_event(
            skills=["reference-skill", {"name": "fable-method"}],
            plugins=["local-plugin", "unrelated-plugin"],
        )
        result = evaluate([run], [reference], expected_fable=True)
        surface = result.global_surface

        self.assertFalse(result.purity_pass)
        self.assertFalse(result.run_countable)
        self.assertFalse(surface.passed)
        self.assertEqual(
            surface.dimension_states["plugins"], runner.SURFACE_DIFFERENT
        )
        self.assertIn("plugins", surface.differing_dimensions)
        self.assertIn(
            "non_treatment_surface_delta:plugins:DIFFERENT", surface.reasons
        )
        # The treatment itself was applied exactly; only the unrelated surface
        # is what makes the observation non-countable.
        self.assertTrue(surface.allowed_treatment_delta)

    def test_32_observable_hook_and_instruction_deltas_fail_closed(self) -> None:
        deltas = {
            "hooks": {"PreToolUse": [{"command": "audit.sh"}], "SessionStart": []},
            "agents_md": [{"path": "AGENTS.md", "sha256": "b" * 64}],
            "user_rules": ["always-use-tabs"],
            "instruction_sources": ["project", "user", "enterprise"],
            "permission_mode": "bypassPermissions",
            "output_style": "explanatory",
            "host_version": "offline-host-1",
        }
        reference = global_surface_event(skills=["reference-skill"])
        for dimension, value in deltas.items():
            with self.subTest(dimension=dimension):
                run = global_surface_event(
                    skills=["reference-skill", {"name": "fable-method"}],
                    **{dimension: value},
                )
                result = evaluate([run], [reference], expected_fable=True)
                surface = result.global_surface
                self.assertFalse(result.purity_pass)
                self.assertFalse(result.run_countable)
                self.assertEqual(
                    surface.dimension_states[dimension], runner.SURFACE_DIFFERENT
                )
                self.assertIn(
                    f"non_treatment_surface_delta:{dimension}:DIFFERENT",
                    surface.reasons,
                )

    def test_33_same_skill_identity_with_declared_content_delta_passes(self) -> None:
        reference = global_surface_event(
            skills=["reference-skill", "fable-method"],
            **{
                CONTENT_DIMENSION: {
                    "fable-method": "a" * 64,
                    "reference-skill": "b" * 64,
                }
            },
        )
        run = global_surface_event(
            skills=["reference-skill", "fable-method"],
            **{
                CONTENT_DIMENSION: {
                    "fable-method": "c" * 64,
                    "reference-skill": "b" * 64,
                }
            },
        )
        result = evaluate(
            [run],
            [reference],
            expected_fable=True,
            treatment_surface=content_delta_treatment(True),
        )
        surface = result.global_surface

        self.assertTrue(result.purity_pass, result.reasons)
        self.assertTrue(result.run_countable)
        self.assertTrue(surface.identity_equality)
        self.assertTrue(surface.allowed_treatment_delta)
        self.assertEqual(surface.treatment_delta_kind, "CONTENT_DELTA")
        self.assertEqual(
            surface.dimension_states[runner.SKILL_INVENTORY_DIMENSION],
            runner.SURFACE_EQUAL,
        )
        self.assertEqual(
            surface.dimension_states[CONTENT_DIMENSION],
            runner.SURFACE_ALLOWED_TREATMENT_DELTA,
        )

        control = evaluate(
            [reference],
            [reference],
            expected_fable=False,
            treatment_surface=content_delta_treatment(False),
        )
        self.assertTrue(control.purity_pass, control.reasons)
        self.assertTrue(control.global_surface.identity_equality)
        self.assertFalse(control.global_surface.allowed_treatment_delta)
        self.assertEqual(control.global_surface.treatment_delta_kind, "NONE")

    def test_34_extra_unrelated_skill_or_content_key_fails_closed(self) -> None:
        reference = global_surface_event(skills=["reference-skill"])
        run = global_surface_event(
            skills=["reference-skill", "fable-method", "unrelated-skill"]
        )
        result = evaluate([run], [reference], expected_fable=True)
        surface = result.global_surface
        self.assertFalse(result.purity_pass)
        self.assertFalse(result.run_countable)
        self.assertFalse(surface.allowed_treatment_delta)
        self.assertIn(
            "non_treatment_identity_delta:unrelated-skill", surface.reasons
        )

        content_reference = global_surface_event(
            skills=["reference-skill", "fable-method"],
            **{
                CONTENT_DIMENSION: {
                    "fable-method": "a" * 64,
                    "reference-skill": "b" * 64,
                }
            },
        )
        content_run = global_surface_event(
            skills=["reference-skill", "fable-method"],
            **{
                CONTENT_DIMENSION: {
                    "fable-method": "c" * 64,
                    "reference-skill": "b" * 64,
                    "unrelated-skill": "d" * 64,
                }
            },
        )
        content = evaluate(
            [content_run],
            [content_reference],
            expected_fable=True,
            treatment_surface=content_delta_treatment(True),
        )
        self.assertFalse(content.purity_pass)
        self.assertFalse(content.run_countable)
        self.assertFalse(content.global_surface.allowed_treatment_delta)
        self.assertIn(
            "non_treatment_content_key_delta:unrelated-skill",
            content.global_surface.reasons,
        )

    def test_35_unobservable_surfaces_never_become_equality(self) -> None:
        mandatory = FROZEN_DIMENSIONS + ("plugins",)
        reference = init_event(skills=["reference-skill"])
        run = init_event(skills=["reference-skill", "fable-method"])
        missing = evaluate(
            [run], [reference], expected_fable=True, frozen_dimensions=mandatory
        )
        surface = missing.global_surface
        self.assertFalse(missing.purity_pass)
        self.assertFalse(missing.run_countable)
        self.assertIn("plugins", surface.mandatory_dimensions)
        self.assertIn("plugins", surface.unobservable_dimensions)
        self.assertEqual(
            surface.dimension_states["plugins"], runner.SURFACE_UNOBSERVABLE
        )
        self.assertIn(
            "mandatory_dimension_not_equal:plugins:UNOBSERVABLE", surface.reasons
        )

        one_sided = evaluate(
            [init_event(skills=["reference-skill", "fable-method"], hooks={"a": []})],
            [reference],
            expected_fable=True,
        )
        self.assertFalse(one_sided.purity_pass)
        self.assertEqual(
            one_sided.global_surface.dimension_states["hooks"],
            runner.SURFACE_UNRESOLVED,
        )
        self.assertIn(
            "non_treatment_surface_delta:hooks:UNRESOLVED",
            one_sided.global_surface.reasons,
        )

        no_inventory = evaluate(
            [{"type": "system", "subtype": "init"}],
            [{"type": "system", "subtype": "init"}],
            expected_fable=True,
            frozen_dimensions=(),
        )
        self.assertFalse(no_inventory.purity_pass)
        self.assertEqual(
            no_inventory.global_surface.dimension_states[
                runner.SKILL_INVENTORY_DIMENSION
            ],
            runner.SURFACE_UNOBSERVABLE,
        )
        self.assertIn(
            "treatment_surface_not_observed:skills",
            no_inventory.global_surface.reasons,
        )
        self.assertIsNone(no_inventory.global_surface.identity_equality)

    def test_36_gate_is_generic_over_the_declared_carrier_identity(self) -> None:
        treatment = runner.TreatmentSurface(
            dimension=runner.SKILL_INVENTORY_DIMENSION,
            mode=runner.TREATMENT_MODE_IDENTITY_PRESENCE,
            identity="some-other-carrier",
            baseline_contains_identity=False,
            expected_treatment_delta=True,
        )
        reference = global_surface_event(skills=["reference-skill"])
        declared = global_surface_event(
            skills=["reference-skill", "some-other-carrier"]
        )
        result = evaluate(
            [declared], [reference], expected_fable=True, treatment_surface=treatment
        )
        self.assertTrue(result.purity_pass, result.reasons)
        self.assertTrue(result.fable_engaged)

        undeclared = global_surface_event(skills=["reference-skill", "fable-method"])
        wrong = evaluate(
            [undeclared], [reference], expected_fable=True, treatment_surface=treatment
        )
        self.assertFalse(wrong.purity_pass)
        self.assertFalse(wrong.run_countable)
        self.assertFalse(wrong.global_surface.allowed_treatment_delta)
        # The undeclared carrier is an ordinary non-treatment identity delta,
        # and the declared carrier never arrived in the candidate arm.
        self.assertIn(
            "non_treatment_identity_delta:fable-method",
            wrong.global_surface.reasons,
        )
        self.assertIn(
            "candidate_treatment_identity_presence_mismatch",
            wrong.global_surface.reasons,
        )

        with self.assertRaises(runner.HarnessContractError):
            runner.TreatmentSurface(
                dimension=runner.SKILL_INVENTORY_DIMENSION,
                mode=runner.TREATMENT_MODE_CONTENT_DELTA,
                identity="fable-method",
                baseline_contains_identity=True,
                expected_treatment_delta=True,
            )

    def test_37_manifest_declares_the_allowed_treatment_surface(self) -> None:
        default_slot = runner.select_manifest_slot(
            synthetic_manifest(), "s02-A01-r0-ON"
        )
        self.assertEqual(
            default_slot.treatment_surface.dimension, runner.SKILL_INVENTORY_DIMENSION
        )
        self.assertEqual(
            default_slot.treatment_surface.mode,
            runner.TREATMENT_MODE_IDENTITY_PRESENCE,
        )
        self.assertFalse(default_slot.treatment_surface.baseline_contains_identity)
        self.assertTrue(default_slot.treatment_surface.expected_treatment_delta)

        manifest = synthetic_manifest("definitely-not-an-installed-provider")
        manifest["treatment_surface"] = {
            "dimension": CONTENT_DIMENSION,
            "mode": runner.TREATMENT_MODE_CONTENT_DELTA,
            "identity": "fable-method",
            "baseline_contains_identity": True,
        }
        slot = runner.select_manifest_slot(manifest, "s02-A01-r0-ON")
        self.assertEqual(slot.treatment_surface.dimension, CONTENT_DIMENSION)
        self.assertTrue(slot.treatment_surface.baseline_contains_identity)
        self.assertTrue(slot.treatment_surface.candidate_contains_identity)

        rejected = synthetic_manifest()
        rejected["treatment_surface"] = {"dimension": CONTENT_DIMENSION}
        with self.assertRaises(runner.HarnessContractError):
            runner.select_manifest_slot(rejected, "s02-A01-r0-ON")

        reference = global_surface_event(
            skills=["reference-skill", "fable-method"],
            **{
                CONTENT_DIMENSION: {
                    "fable-method": "a" * 64,
                    "reference-skill": "b" * 64,
                }
            },
        )

        def fake_executor(invocation: Any) -> Any:
            return provider_execution(
                invocation,
                [
                    global_surface_event(
                        skills=["reference-skill", "fable-method"],
                        session_id="offline-session",
                        **{
                            CONTENT_DIMENSION: {
                                "fable-method": "c" * 64,
                                "reference-skill": "b" * 64,
                            }
                        },
                    ),
                    {
                        "type": "assistant",
                        "session_id": "offline-session",
                        "message": {"id": "msg-offline", "model": "offline-model"},
                    },
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "session_id": "offline-session",
                        "modelUsage": {"offline-model": {"input_tokens": 1}},
                        "total_cost_usd": "0.0",
                    },
                ],
            )

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.execute_manifest_slot(
                manifest,
                "s02-A01-r0-ON",
                workspace_parent=temporary,
                reference_events=[reference],
                materializer=lambda plan: make_git_repository(
                    Path(plan.workspace_path)
                ),
                executor=fake_executor,
                opaque_id_factory=lambda: "00112233445566778899aabbccddeeff",
            )

        # A baseline that already carries the Fable identity is now a legal
        # reference arm; only the declared carrier content differs.
        self.assertTrue(evidence.executor_called)
        self.assertTrue(evidence.purity.purity_pass, evidence.purity.reasons)
        self.assertTrue(evidence.run_countable)
        self.assertTrue(evidence.purity.global_surface.identity_equality)
        self.assertEqual(
            evidence.purity.global_surface.treatment_delta_kind, "CONTENT_DELTA"
        )
        json.dumps(evidence.as_record(), allow_nan=False)


    def _binding_blocked_evidence(self, manifest: Any) -> tuple[Any, list[Any]]:
        """Run one slot whose provider executor must never be reached."""

        calls: list[Any] = []

        def forbidden_executor(invocation: Any) -> Any:
            calls.append(invocation)
            raise AssertionError("provider executor reached with an unbound runner")

        with tempfile.TemporaryDirectory() as temporary:
            evidence = runner.execute_manifest_slot(
                manifest,
                "s01-A01-r0-OFF",
                workspace_parent=temporary,
                reference_events=[init_event()],
                materializer=lambda plan: make_git_repository(
                    Path(plan.workspace_path)
                ),
                executor=forbidden_executor,
                opaque_id_factory=lambda: "00112233445566778899aabbccddeeff",
            )
        return evidence, calls

    def _assert_closed_before_provider(
        self, evidence: Any, calls: list[Any], state: str
    ) -> None:
        self.assertEqual(calls, [])
        self.assertFalse(evidence.executor_called)
        self.assertIsNone(evidence.provider_execution)
        self.assertFalse(evidence.run_countable)
        self.assertFalse(evidence.runner_self_binding.passed)
        self.assertEqual(evidence.runner_self_binding.state, state)
        json.dumps(evidence.as_record(), allow_nan=False)

    def test_38_executing_runner_binds_to_the_final_runtime_authority_pin(self) -> None:
        derived = runner.derive_executing_runner_identity()
        self.assertIsNone(derived["error"])
        self.assertEqual(Path(derived["source_path"]), RUNNER_PATH.resolve())
        self.assertEqual(derived["sha256"], ACTUAL_RUNNER_SHA256)
        # The blob identity is Git's, not a private digest scheme.
        self.assertEqual(derived["blob_oid"], ACTUAL_RUNNER_BLOB_OID)
        self.assertEqual(derived["blob_oid"], git_blob_oid(RUNNER_BYTES))

        # The supplied pin is validated in the compiled manifest's own shape,
        # so the gate reuses that identity contract instead of adding one.
        authority = final_runtime_authority()
        self.assertEqual(
            compiler._normalize_final_runtime(
                compiler._final_runtime_input(authority)
            ),
            authority,
        )

        binding = runner.evaluate_runner_self_binding(synthetic_manifest())
        self.assertTrue(binding.passed, binding.reasons)
        self.assertEqual(binding.state, runner.RUNNER_BINDING_BOUND)
        self.assertEqual(binding.reasons, ())
        self.assertEqual(binding.actual_blob_oid, binding.supplied_blob_oid)
        self.assertEqual(binding.actual_sha256, binding.supplied_sha256)

        calls: list[Any] = []

        def counting_executor(invocation: Any) -> Any:
            calls.append(invocation)
            return self._complete_execution(invocation)

        evidence = self._execute_offline_slot(counting_executor)
        self.assertEqual(len(calls), 1)
        self.assertTrue(evidence.runner_self_binding.passed)
        self.assertTrue(evidence.executor_called)
        self.assertTrue(evidence.run_countable, evidence.purity.reasons)
        json.dumps(evidence.as_record(), allow_nan=False)

    def test_39_missing_supplied_runner_identity_fails_before_the_provider(self) -> None:
        absent = synthetic_manifest()
        del absent["final_runtime_authority"]

        no_components = synthetic_manifest()
        del no_components["final_runtime_authority"]["components"]

        no_runner = synthetic_manifest()
        del no_runner["final_runtime_authority"]["components"]["runner"]

        unresolved = synthetic_manifest()
        unresolved["final_runtime_authority"]["components"]["runner"] = {
            "status": "UNRESOLVED",
            "blob_oid": None,
            "sha256": None,
        }

        null_sha = synthetic_manifest()
        null_sha["final_runtime_authority"]["components"]["runner"]["sha256"] = None

        malformed = synthetic_manifest()
        malformed["final_runtime_authority"]["components"]["runner"][
            "blob_oid"
        ] = "not-a-git-object-id"

        not_an_object = synthetic_manifest()
        not_an_object["final_runtime_authority"] = "PINNED"

        cases = (
            ("authority_absent", absent, "final_runtime_authority_absent"),
            (
                "components_absent",
                no_components,
                "final_runtime_authority_components_absent",
            ),
            (
                "runner_component_absent",
                no_runner,
                "final_runtime_authority_runner_component_absent",
            ),
            ("not_pinned", unresolved, "supplied_runner_identity_not_pinned"),
            ("sha256_absent", null_sha, "supplied_runner_sha256_absent"),
            ("blob_oid_malformed", malformed, "supplied_runner_blob_oid_malformed"),
            (
                "authority_not_an_object",
                not_an_object,
                "final_runtime_authority_is_not_an_object",
            ),
        )
        for label, manifest, reason in cases:
            with self.subTest(case=label):
                binding = runner.evaluate_runner_self_binding(manifest)
                self.assertFalse(binding.passed)
                self.assertEqual(
                    binding.state, runner.RUNNER_BINDING_SUPPLIED_MISSING
                )
                self.assertIn(reason, binding.reasons)
                # An unusable pin is never silently completed from the runner.
                self.assertIsNone(binding.supplied_blob_oid)
                self.assertIsNone(binding.supplied_sha256)
                # The executing runner stays observable; only the pin is absent.
                self.assertEqual(binding.actual_sha256, ACTUAL_RUNNER_SHA256)

                evidence, calls = self._binding_blocked_evidence(manifest)
                self._assert_closed_before_provider(
                    evidence, calls, runner.RUNNER_BINDING_SUPPLIED_MISSING
                )

    def test_40_unavailable_actual_runner_identity_fails_before_the_provider(self) -> None:
        manifest = synthetic_manifest()
        original = runner.__file__

        with tempfile.TemporaryDirectory() as temporary:
            removed_source = str(Path(temporary) / "runner-was-removed.py")
            cases = (
                (
                    "source_unreadable",
                    removed_source,
                    "runner_source_unreadable:FileNotFoundError",
                ),
                ("module_file_absent", None, "runner_source_path_unresolved"),
            )
            for label, replacement, reason in cases:
                with self.subTest(case=label):
                    try:
                        if replacement is None:
                            del runner.__file__
                        else:
                            runner.__file__ = replacement
                        binding = runner.evaluate_runner_self_binding(manifest)
                        evidence, calls = self._binding_blocked_evidence(manifest)
                    finally:
                        runner.__file__ = original

                    self.assertFalse(binding.passed)
                    self.assertEqual(
                        binding.state, runner.RUNNER_BINDING_ACTUAL_UNAVAILABLE
                    )
                    self.assertIn(reason, binding.reasons)
                    # Absence is never equality: no identity is invented.
                    self.assertIsNone(binding.actual_blob_oid)
                    self.assertIsNone(binding.actual_sha256)
                    # The pin was readable; only the executing runner was not.
                    self.assertEqual(binding.supplied_sha256, ACTUAL_RUNNER_SHA256)

                    self._assert_closed_before_provider(
                        evidence, calls, runner.RUNNER_BINDING_ACTUAL_UNAVAILABLE
                    )

        self.assertEqual(runner.__file__, original)
        self.assertTrue(runner.evaluate_runner_self_binding(manifest).passed)

    def test_41_supplied_runner_identity_mismatch_fails_before_the_provider(self) -> None:
        other_blob_oid = "0" * 40
        other_sha256 = "0" * 64
        self.assertNotEqual(other_blob_oid, ACTUAL_RUNNER_BLOB_OID)
        self.assertNotEqual(other_sha256, ACTUAL_RUNNER_SHA256)

        cases = (
            (
                "blob_oid_differs",
                other_blob_oid,
                ACTUAL_RUNNER_SHA256,
                ("runner_blob_oid_mismatch",),
            ),
            (
                "sha256_differs",
                ACTUAL_RUNNER_BLOB_OID,
                other_sha256,
                ("runner_sha256_mismatch",),
            ),
            (
                "both_differ",
                other_blob_oid,
                other_sha256,
                ("runner_blob_oid_mismatch", "runner_sha256_mismatch"),
            ),
        )
        for label, blob_oid, sha256, reasons in cases:
            with self.subTest(case=label):
                manifest = synthetic_manifest()
                manifest["final_runtime_authority"] = final_runtime_authority(
                    blob_oid=blob_oid, sha256=sha256
                )
                binding = runner.evaluate_runner_self_binding(manifest)
                self.assertFalse(binding.passed)
                self.assertEqual(binding.state, runner.RUNNER_BINDING_MISMATCH)
                self.assertEqual(binding.reasons, reasons)
                self.assertEqual(binding.actual_blob_oid, ACTUAL_RUNNER_BLOB_OID)
                self.assertEqual(binding.actual_sha256, ACTUAL_RUNNER_SHA256)
                self.assertEqual(binding.supplied_blob_oid, blob_oid)
                self.assertEqual(binding.supplied_sha256, sha256)

                evidence, calls = self._binding_blocked_evidence(manifest)
                self._assert_closed_before_provider(
                    evidence, calls, runner.RUNNER_BINDING_MISMATCH
                )

    def test_42_historical_canonical_harness_pin_does_not_bind_the_runner(self) -> None:
        historical = compiler.CANONICAL_HARNESS["runner"]["sha256"]
        # The frozen base scaffold is by design a different runner than the one
        # executing this epoch; that difference is not a binding failure.
        self.assertNotEqual(historical, ACTUAL_RUNNER_SHA256)

        manifest = synthetic_manifest("definitely-not-an-installed-provider")
        manifest["canonical_harness"] = copy.deepcopy(compiler.CANONICAL_HARNESS)

        binding = runner.evaluate_runner_self_binding(manifest)
        self.assertTrue(binding.passed, binding.reasons)
        self.assertEqual(binding.state, runner.RUNNER_BINDING_BOUND)
        self.assertEqual(binding.actual_sha256, ACTUAL_RUNNER_SHA256)
        self.assertNotEqual(binding.supplied_sha256, historical)

        calls: list[Any] = []

        def counting_executor(invocation: Any) -> Any:
            calls.append(invocation)
            return self._complete_execution(invocation)

        evidence = self._execute_offline_slot(counting_executor, manifest=manifest)
        self.assertEqual(len(calls), 1)
        self.assertTrue(evidence.executor_called)
        self.assertTrue(evidence.runner_self_binding.passed)
        self.assertTrue(evidence.run_countable, evidence.purity.reasons)
        json.dumps(evidence.as_record(), allow_nan=False)

        # Absent the historical pin the binding is unchanged: the executable
        # owner is final_runtime_authority alone.
        without_history = synthetic_manifest()
        self.assertNotIn("canonical_harness", without_history)
        self.assertTrue(runner.evaluate_runner_self_binding(without_history).passed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
