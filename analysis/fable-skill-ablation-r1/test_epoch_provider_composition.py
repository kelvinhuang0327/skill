#!/usr/bin/env python3
"""Offline acceptance tests for the sanctioned provider composition path.

Every test here is provider-free in the paid sense: the only binary the
sandbox ever launches is a local stub that prints a canned stream-json
transcript.  No credential is read, no network call is made, and no real
epoch root is written.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest import mock


sys.dont_write_bytecode = True


def load_sibling(name: str, filename: str) -> Any:
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load_sibling("fable_ablation_runner", "runner.py")
controller = load_sibling("fable_ablation_epoch_controller", "epoch_controller.py")
claude = load_sibling("fable_ablation_claude_executor", "claude_executor.py")
composition = load_sibling(
    "fable_ablation_epoch_provider_composition", "epoch_provider_composition.py"
)

PROFILE_PATH = Path(__file__).with_name("claude-runtime.sb")
SESSION_TOKEN_A = "0123456789abcdef0123456789abcdef"
SESSION_TOKEN_B = "fedcba9876543210fedcba9876543210"
PROVIDER_SESSION_ID = "stub-session-001"
SEALED_CAP = Decimal("2.0000000")

STUB_PROVIDER = '''#!/usr/bin/env python3
"""Offline stub provider: prints a canned transcript, contacts nothing."""
import json
import sys

sys.stdin.read()
RECORDS = [
    {
        "type": "system",
        "subtype": "init",
        "session_id": "%(session)s",
        "model": "%(model)s",
        "effort": "%(effort)s",
        "tools": %(tools)s,
        "agents": ["general-purpose"],
        "mcp_servers": [],
        "plugins": [],
        "permissionMode": "%(permission)s",
        "skills": ["reference-skill"],
    },
    {
        "type": "assistant",
        "session_id": "%(session)s",
        "message": {
            "id": "msg-stub-001",
            "role": "assistant",
            "model": "%(model)s",
            "content": [{"type": "text", "text": "offline"}],
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "%(session)s",
        "num_turns": 1,
        "modelUsage": {"%(model)s": {"input_tokens": 10, "output_tokens": 5}},
        "total_cost_usd": "0.1250000",
    },
]
for record in RECORDS:
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\\n")
'''


def write_stub_provider(parent: Path) -> Path:
    stub = parent / "stub-provider"
    stub.write_text(
        STUB_PROVIDER
        % {
            "session": PROVIDER_SESSION_ID,
            "model": claude.CTO_MODEL_ID,
            "effort": claude.CTO_EFFORT,
            "tools": json.dumps(list(claude.CTO_TOOLSET)),
            "permission": claude.CTO_PERMISSION_MODE,
        },
        encoding="utf-8",
    )
    stub.chmod(0o700)
    return stub


class FakeProcess:
    """Stands in for a real child so no process is created."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = b""
        self.returncode = returncode
        self.pid = 4321

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> Any:
        return self.stdout, self.stderr

    def poll(self) -> int:
        return self.returncode


class CompositionTestCase(unittest.TestCase):
    def make_policy(self, executable: str) -> Any:
        binary = runner.ProviderBinaryIdentity(
            executable=executable,
            realpath=executable,
            sha256="b" * 64,
            version="offline-stub-1.0",
        )
        policy = claude.make_cto_policy(
            binary,
            timeout_seconds=30.0,
            expected_tools=claude.CTO_TOOLSET,
            expected_agents=("general-purpose",),
            expected_mcp_servers=(),
            expected_plugins=(),
        )
        return policy, binary

    def make_invocation(self, policy: Any, session_root: Path) -> Any:
        return runner.ModelInvocation(
            argv=tuple(policy.argv_template),
            cwd=str(session_root),
            prompt="Perform the offline fixture check.",
            environment={
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            task_visible_run_id=session_root.name,
        )

    def ledger(self, root: Path, cap: str = "10.0000000") -> Any:
        return controller.BudgetLedger(root / "ledger.jsonl", Decimal(cap))

    def compose(
        self,
        temporary: Path,
        *,
        token: str = SESSION_TOKEN_A,
        ledger: Any | None = None,
        slot_id: str = "slot-a",
    ) -> tuple[Any, Any, Path]:
        stub = write_stub_provider(temporary)
        session_root = controller.create_opaque_session_root(temporary, lambda: token)
        policy, binary = self.make_policy(str(stub))
        invocation = self.make_invocation(policy, session_root)
        result = composition.execute_reserved_provider_slot(
            ledger=self.ledger(temporary) if ledger is None else ledger,
            slot_id=slot_id,
            sealed_invocation_cap=SEALED_CAP,
            policy=policy,
            invocation=invocation,
            session_root=session_root,
            profile_path=PROFILE_PATH,
            binary_identity_resolver=lambda _: binary,
            adapter_identity_resolver=lambda: policy.adapter_source,
        )
        return result, policy, session_root


class ProviderCompositionTests(CompositionTestCase):
    def test_01_composition_builds_logical_and_sandbox_prefixed_physical_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, policy, session_root = self.compose(Path(temporary))

        logical = result.logical_argv
        physical = result.physical_argv
        self.assertEqual(logical, tuple(policy.argv_template))
        self.assertNotEqual(logical, physical)
        self.assertIsNotNone(physical)
        # The physical lane prefixes, and never replaces, the logical lane.
        self.assertEqual(physical[0], str(controller.SANDBOX_EXEC_PATH))
        self.assertEqual(physical[1], "-f")
        self.assertEqual(physical[2], str(PROFILE_PATH.resolve()))
        self.assertEqual(physical[3], "-D")
        # The bound SESSION_ROOT is the fully resolved real directory, so the
        # kernel policy cannot be aimed at a symlinked alias of it.
        self.assertEqual(physical[4], f"SESSION_ROOT={session_root.resolve()}")
        self.assertEqual(physical[5:], logical)
        self.assertNotIn(str(controller.SANDBOX_EXEC_PATH), logical)

    def test_02_logical_argv_preflight_still_passes_through_composition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _, _ = self.compose(Path(temporary))

        execution = result.provider_execution
        self.assertEqual(execution.validation_errors, ())
        self.assertTrue(execution.evidence_sealed)
        self.assertTrue(execution.run_countable)
        # The preflight lane -- and the sealed invocation evidence -- remain
        # the unprefixed provider command.
        self.assertEqual(execution.invocation.argv, result.logical_argv)

    def test_03_composition_cannot_reach_an_unsandboxed_popen(self) -> None:
        recorded: list[Sequence[str]] = []

        def fake_popen(command: Sequence[str], **kwargs: Any) -> Any:
            recorded.append(tuple(command))
            return FakeProcess()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(
                root, lambda: SESSION_TOKEN_A
            )
            policy, binary = self.make_policy(str(stub))
            invocation = self.make_invocation(policy, session_root)
            with mock.patch.object(claude.subprocess, "Popen") as adapter_popen:
                with mock.patch.object(
                    controller.subprocess, "Popen", side_effect=fake_popen
                ):
                    composition.execute_reserved_provider_slot(
                        ledger=self.ledger(root),
                        slot_id="slot-a",
                        sealed_invocation_cap=SEALED_CAP,
                        policy=policy,
                        invocation=invocation,
                        session_root=session_root,
                        profile_path=PROFILE_PATH,
                        binary_identity_resolver=lambda _: binary,
                        adapter_identity_resolver=lambda: policy.adapter_source,
                    )
                adapter_popen.assert_not_called()

        self.assertEqual(len(recorded), 1)
        command = recorded[0]
        self.assertEqual(command[0], str(controller.SANDBOX_EXEC_PATH))
        self.assertIn(f"SESSION_ROOT={session_root.resolve()}", command)
        self.assertEqual(command[-len(policy.argv_template):], tuple(policy.argv_template))

    def test_04_start_intent_is_durable_before_an_induced_spawn_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(
                root, lambda: SESSION_TOKEN_A
            )
            policy, binary = self.make_policy(str(stub))
            invocation = self.make_invocation(policy, session_root)
            ledger = self.ledger(root)
            with mock.patch.object(
                controller.subprocess, "Popen", side_effect=OSError("induced")
            ):
                result = composition.execute_reserved_provider_slot(
                    ledger=ledger,
                    slot_id="slot-a",
                    sealed_invocation_cap=SEALED_CAP,
                    policy=policy,
                    invocation=invocation,
                    session_root=session_root,
                    profile_path=PROFILE_PATH,
                    binary_identity_resolver=lambda _: binary,
                    adapter_identity_resolver=lambda: policy.adapter_source,
                )

            snapshot = ledger.load()
            self.assertEqual(
                snapshot.started_unresolved, {"slot-a": Decimal("2.0000000")}
            )
            self.assertIsNotNone(result.start_intent_record)
            self.assertEqual(
                result.start_intent_record["transition"], "provider-start-intent"
            )
            self.assertEqual(result.provider_execution.process.status, "START_FAILED")
            self.assertFalse(result.provider_execution.run_countable)
            self.assertTrue(result.provider_execution.evidence_sealed)
            with self.assertRaisesRegex(controller.LedgerError, "no_silent_rerun"):
                ledger.reserve("slot-a", SEALED_CAP)

    def test_05_reservation_precedes_start_intent_and_is_never_bypassed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            result, _, _ = self.compose(root, ledger=ledger)

            entries = ledger.load().entries
            transitions = [entry["transition"] for entry in entries]
            self.assertEqual(transitions, ["reservation", "provider-start-intent"])
            self.assertEqual(entries[0]["amount"], "2.0000000")
            self.assertEqual(entries[1]["amount"], "2.0000000")
            self.assertEqual(entries[1]["previous_hash"], entries[0]["entry_hash"])
            self.assertIsNotNone(result.reservation_record)
            self.assertEqual(result.sealed_invocation_cap_usd, "2.0000000")

    def test_06_condition_neutrality_holds_across_two_opaque_session_roots(self) -> None:
        # One pinned provider binary and one ledger, two independently random
        # session roots -- exactly the shape a real OFF/ON pair takes.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            left, _, _ = self.compose(
                root, token=SESSION_TOKEN_A, ledger=ledger, slot_id="slot-a"
            )
            right, _, _ = self.compose(
                root, token=SESSION_TOKEN_B, ledger=ledger, slot_id="slot-b"
            )

        self.assertNotEqual(left.physical_argv, right.physical_argv)
        comparison = claude.compare_condition_neutral_evidence(
            left.provider_execution, right.provider_execution
        )
        self.assertTrue(comparison.passed, comparison.errors)
        self.assertTrue(
            comparison.checks["physical_argv_equal_after_opaque_root_normalization"]
        )
        self.assertTrue(comparison.checks["observed_sandbox_policy_bytes_equal"])

    def test_07_both_argv_lanes_survive_evidence_serialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _, session_root = self.compose(Path(temporary))

        execution = result.provider_execution
        record = execution.as_record()
        restored = json.loads(json.dumps(record, sort_keys=True, allow_nan=False))
        lanes = restored["argv_lanes"]
        self.assertEqual(tuple(lanes["logical"]), result.logical_argv)
        self.assertEqual(tuple(lanes["physical"]), result.physical_argv)
        self.assertNotEqual(lanes["logical"], lanes["physical"])
        self.assertTrue(lanes["physically_sandboxed"])
        self.assertEqual(restored["schema"], runner.RUN_EVIDENCE_SCHEMA)
        self.assertTrue(restored["evidence_sealed"])
        self.assertEqual(restored["evidence_sha256"], execution.computed_evidence_sha256())
        identity = restored["sandbox_identity"]
        self.assertEqual(identity["identity_source"], "OBSERVED_FILESYSTEM_BYTES")
        self.assertEqual(
            identity["profile_sha256"], controller.sha256_file(PROFILE_PATH)
        )
        self.assertEqual(
            identity["sandbox_exec_sha256"],
            controller.sha256_file(controller.SANDBOX_EXEC_PATH),
        )
        self.assertEqual(identity["session_root"], str(session_root.resolve()))
        # The derived cost persists canonically, never in exponent form.
        self.assertEqual(restored["total_cost_usd"], "0.1250000")
        self.assertEqual(restored["total_cost_usd_representation"], "CANONICAL_USD")

    def test_08_wrong_composition_modes_stay_fail_closed_and_provider_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(
                root, lambda: SESSION_TOKEN_A
            )
            policy, binary = self.make_policy(str(stub))
            invocation = self.make_invocation(policy, session_root)
            launcher = composition.SandboxedProviderLauncher(
                ledger=self.ledger(root),
                slot_id="slot-a",
                sealed_invocation_cap=SEALED_CAP,
                session_root=session_root,
                profile_path=PROFILE_PATH,
            )
            refusals = {
                "shell": {"shell": True},
                "inherited_descriptors": {"close_fds": False},
                "shared_session": {"start_new_session": False},
                "unsupported_option": {"preexec_fn": lambda: None},
                "foreign_cwd": {"cwd": str(root)},
            }
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                for label, overrides in refusals.items():
                    with self.subTest(mode=label):
                        with self.assertRaises(composition.CompositionError):
                            launcher(tuple(policy.argv_template), **overrides)
                popen.assert_not_called()
                self.assertEqual(launcher.launch_count, 0)
                self.assertIsNone(launcher.identity_record())

                # An executor built without the sanctioned launcher is refused
                # outright rather than quietly falling back to a raw Popen.
                with self.assertRaises(composition.CompositionError):
                    composition.build_composed_executor(policy, lambda *a, **k: None)
                with self.assertRaises(runner.HarnessContractError):
                    claude.ClaudeExecutor(policy=policy)
                with self.assertRaises(runner.HarnessContractError):
                    claude.execute_claude(invocation, policy)
                popen.assert_not_called()

    def test_09_composition_requires_exactly_thirty_two_hex_session_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            policy, binary = self.make_policy(str(stub))
            wide = root / ("session-" + "a" * 64)
            wide.mkdir(mode=0o700)
            invocation = runner.ModelInvocation(
                argv=tuple(policy.argv_template),
                cwd=str(wide),
                prompt="Perform the offline fixture check.",
                environment={},
                task_visible_run_id=wide.name,
            )
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with self.assertRaises(controller.SandboxPolicyError):
                    composition.execute_reserved_provider_slot(
                        ledger=self.ledger(root),
                        slot_id="slot-a",
                        sealed_invocation_cap=SEALED_CAP,
                        policy=policy,
                        invocation=invocation,
                        session_root=wide,
                        profile_path=PROFILE_PATH,
                        binary_identity_resolver=lambda _: binary,
                        adapter_identity_resolver=lambda: policy.adapter_source,
                    )
                popen.assert_not_called()
            self.assertFalse((root / "ledger.jsonl").exists())

    def test_10_policy_sandbox_identity_must_bind_the_observed_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(
                root, lambda: SESSION_TOKEN_A
            )
            policy, binary = self.make_policy(str(stub))
            other_profile = root / "other-runtime.sb"
            other_profile.write_bytes(PROFILE_PATH.read_bytes() + b"\n;; drifted\n")
            invocation = self.make_invocation(policy, session_root)
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with self.assertRaises(composition.CompositionError):
                    composition.execute_reserved_provider_slot(
                        ledger=self.ledger(root),
                        slot_id="slot-a",
                        sealed_invocation_cap=SEALED_CAP,
                        policy=policy,
                        invocation=invocation,
                        session_root=session_root,
                        profile_path=other_profile,
                        binary_identity_resolver=lambda _: binary,
                        adapter_identity_resolver=lambda: policy.adapter_source,
                    )
                popen.assert_not_called()
            self.assertFalse((root / "ledger.jsonl").exists())

    def test_11_sub_quantum_sealed_cap_is_refused_before_any_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(
                root, lambda: SESSION_TOKEN_A
            )
            policy, binary = self.make_policy(str(stub))
            invocation = self.make_invocation(policy, session_root)
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(
                    composition.CompositionError, "canonical persisted USD"
                ):
                    composition.execute_reserved_provider_slot(
                        ledger=self.ledger(root),
                        slot_id="slot-a",
                        sealed_invocation_cap=Decimal("0.00000001"),
                        policy=policy,
                        invocation=invocation,
                        session_root=session_root,
                        profile_path=PROFILE_PATH,
                        binary_identity_resolver=lambda _: binary,
                        adapter_identity_resolver=lambda: policy.adapter_source,
                    )
                popen.assert_not_called()
            self.assertFalse((root / "ledger.jsonl").exists())

    def test_12_manifest_and_runtime_bind_the_same_profile_bytes(self) -> None:
        compiler = load_sibling("fable_epoch_manifest", "build_epoch_manifest.py")
        observed = compiler.observed_runtime_policy_sha256()
        self.assertEqual(observed, controller.sha256_file(PROFILE_PATH))
        # The manifest authority input and the adapter policy identity are two
        # views of one observation, not two independent assertions.
        self.assertEqual(
            claude.observed_sandbox_policy_identity(PROFILE_PATH),
            f"{claude.SANDBOX_POLICY_IDENTITY_PREFIX}{observed}",
        )
        self.assertEqual(
            compiler.RUNTIME_POLICY_PATH.resolve(),
            controller.SANDBOX_PROFILE_PATH.resolve(),
        )
        self.assertEqual(
            claude.SANDBOX_PROFILE_PATH.resolve(),
            controller.SANDBOX_PROFILE_PATH.resolve(),
        )

        with tempfile.TemporaryDirectory() as temporary:
            result, _, _ = self.compose(Path(temporary))
        self.assertEqual(result.sandbox_identity["profile_sha256"], observed)
        # The one canonical USD authority is one module object everywhere.
        self.assertIs(compiler, runner._usd)
        self.assertIs(compiler, controller._usd)
        self.assertIs(compiler, claude._usd)
        self.assertIs(compiler, composition._usd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
