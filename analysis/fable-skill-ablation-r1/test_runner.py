#!/usr/bin/env python3
"""Deterministic offline acceptance tests for the canonical ablation harness."""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
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
        self, executor: Any, *, environment: Any = None, materializer: Any = None
    ) -> Any:
        def initialize(plan: Any) -> None:
            make_git_repository(Path(plan.workspace_path))

        with tempfile.TemporaryDirectory() as temporary:
            return runner.execute_manifest_slot(
                synthetic_manifest(),
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

    def _manifest_with_dimensions(self, dimensions: Any) -> dict[str, Any]:
        manifest = synthetic_manifest()
        manifest["purity_gate"][
            "must_be_exactly_equal_between_reference_and_run"
        ] = dimensions
        return manifest

    def _assert_declaration_rejected(self, dimensions: Any) -> None:
        with self.assertRaises(runner.HarnessContractError):
            runner.select_manifest_slot(
                self._manifest_with_dimensions(dimensions), "s01-A01-r0-OFF"
            )

    def test_30_required_purity_dimension_floor_is_code_owned(self) -> None:
        # The floor is exactly the inventory the runner freezes in
        # evaluate_purity, so the manifest can never narrow the comparison.
        self.assertEqual(
            runner.REQUIRED_PURITY_DIMENSIONS,
            frozenset({"tools", "agents", "mcp_servers"}),
        )
        self.assertEqual(runner.REQUIRED_PURITY_DIMENSIONS, set(FROZEN_DIMENSIONS))
        # The treatment delta surface must differ between arms, so freezing it
        # would contradict skill_delta_is_exactly_treatment.
        self.assertNotIn("skills", runner.REQUIRED_PURITY_DIMENSIONS)
        # Surfaces owned by the executor condition-neutrality comparison are
        # not restated as runner purity dimensions.
        for owned_elsewhere in (
            "plugins",
            "permission_mode",
            "output_style",
            "hooks",
            "agents_md",
            "user_rules",
            "instruction_sources",
        ):
            self.assertNotIn(owned_elsewhere, runner.REQUIRED_PURITY_DIMENSIONS)

        slot = runner.select_manifest_slot(
            self._manifest_with_dimensions(list(FROZEN_DIMENSIONS)), "s01-A01-r0-OFF"
        )
        self.assertEqual(slot.frozen_dimensions, FROZEN_DIMENSIONS)

    def test_31_exact_floor_is_accepted_in_any_order(self) -> None:
        for ordering in itertools.permutations(FROZEN_DIMENSIONS):
            with self.subTest(ordering=ordering):
                slot = runner.select_manifest_slot(
                    self._manifest_with_dimensions(list(ordering)), "s01-A01-r0-OFF"
                )
                self.assertEqual(slot.frozen_dimensions, ordering)
                self.assertEqual(
                    set(slot.frozen_dimensions), runner.REQUIRED_PURITY_DIMENSIONS
                )

    def test_32_valid_superset_is_accepted_and_retained(self) -> None:
        declared = list(FROZEN_DIMENSIONS) + ["plugins", "permission_mode"]
        slot = runner.select_manifest_slot(
            self._manifest_with_dimensions(declared), "s01-A01-r0-OFF"
        )
        self.assertEqual(slot.frozen_dimensions, tuple(declared))
        self.assertTrue(
            runner.REQUIRED_PURITY_DIMENSIONS.issubset(set(slot.frozen_dimensions))
        )

    def test_33_strict_subsets_of_the_floor_fail_closed(self) -> None:
        subsets = [
            list(combination)
            for size in range(1, len(FROZEN_DIMENSIONS))
            for combination in itertools.combinations(FROZEN_DIMENSIONS, size)
        ]
        # Every proper subset, including each floor member missing by exactly
        # one, must be refused.
        self.assertEqual(len(subsets), 6)
        for declared in subsets:
            with self.subTest(declared=tuple(declared)):
                self._assert_declaration_rejected(declared)

    def test_34_one_missing_dimension_names_only_what_is_missing(self) -> None:
        for missing in FROZEN_DIMENSIONS:
            declared = [item for item in FROZEN_DIMENSIONS if item != missing]
            with self.subTest(missing=missing):
                with self.assertRaises(runner.HarnessContractError) as raised:
                    runner.select_manifest_slot(
                        self._manifest_with_dimensions(declared), "s01-A01-r0-OFF"
                    )
                message = str(raised.exception)
                self.assertIn("omit required runner dimensions", message)
                self.assertIn(missing, message)
                for retained in declared:
                    self.assertNotIn(retained, message)

    def test_35_under_declared_and_malformed_gates_stay_fail_closed(self) -> None:
        # A demonstrably under-declared list, including one naming only the
        # treatment delta surface, is never accepted.
        for declared in (
            ["skills"],
            ["skills", "tools"],
            ["plugins", "permission_mode", "output_style"],
            [],
            ["tools", "tools", "agents", "mcp_servers"],
            ["tools", "agents", "mcp_servers", "tools"],
        ):
            with self.subTest(declared=tuple(declared)):
                self._assert_declaration_rejected(declared)

        # Absence of the gate, and a gate that carries no usable declaration,
        # remain fail-closed.
        without_gate = synthetic_manifest()
        del without_gate["purity_gate"]
        with self.assertRaises(runner.HarnessContractError):
            runner.select_manifest_slot(without_gate, "s01-A01-r0-OFF")
        for gate in ({}, {"must_be_exactly_equal_between_reference_and_run": None}, []):
            with self.subTest(gate=repr(gate)):
                manifest = synthetic_manifest()
                manifest["purity_gate"] = gate
                with self.assertRaises(runner.HarnessContractError):
                    runner.select_manifest_slot(manifest, "s01-A01-r0-OFF")

    def test_36_under_declaration_would_have_masked_a_real_asymmetry(self) -> None:
        # Why the floor is load-bearing: a narrowed comparison marks a
        # genuinely asymmetric run countable, so the manifest must not be able
        # to choose the narrower set.
        run = [init_event(mcp_servers=["local-test", "unexpected-mcp"])]
        reference = [init_event()]
        narrowed = runner.evaluate_purity(
            run,
            reference,
            expected_fable_engaged=False,
            frozen_dimensions=("tools",),
            surface_audit=PASSING_AUDIT,
        )
        complete = runner.evaluate_purity(
            run,
            reference,
            expected_fable_engaged=False,
            frozen_dimensions=tuple(sorted(runner.REQUIRED_PURITY_DIMENSIONS)),
            surface_audit=PASSING_AUDIT,
        )
        self.assertTrue(narrowed.run_countable)
        self.assertFalse(complete.run_countable)
        self.assertFalse(complete.dimension_matches["mcp_servers"])
        # The narrowed declaration can no longer reach evaluate_purity at all.
        self._assert_declaration_rejected(["tools"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
