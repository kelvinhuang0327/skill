#!/usr/bin/env python3
"""Offline acceptance tests for the sanctioned provider composition path.

Every test here is provider-free in the paid sense: the only binary the
sandbox ever launches is a local stub that prints a canned or synthetic
transcript.  No credential is read, no network call is made, and no real
epoch/root/reservation/spend is written.  The real ``sandbox-exec`` and
``claude-runtime.sb`` are exercised unmocked wherever that is the point of
the test (matching Card A's own "exercise the real binaries" convention);
only ``subprocess.Popen`` is ever substituted, and only to prove composition
never reaches one outside the sanctioned path.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from dataclasses import replace
from pathlib import Path
from typing import Any
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


# claude_executor.py's own source does a plain, hardcoded ``import runner``,
# so ModelInvocation instances built here must come from sys.modules["runner"]
# specifically to satisfy ClaudeExecutor's isinstance check -- matching how
# epoch_provider_composition.py itself loads runner.py.
runner = load_sibling("runner", "runner.py")
controller = load_sibling("fable_ablation_epoch_controller", "epoch_controller.py")
claude = load_sibling("fable_ablation_claude_executor", "claude_executor.py")
composition = load_sibling(
    "epoch_provider_composition", "epoch_provider_composition.py"
)

PROFILE_PATH = Path(__file__).with_name("claude-runtime.sb")
REQUIRED_VERSION = claude.REQUIRED_PROVIDER_VERSION
SESSION_TOKEN_A = "0123456789abcdef0123456789abcdef"
SESSION_TOKEN_B = "fedcba9876543210fedcba9876543210"
SEALED_CAP = Decimal("2.0000000")


def write_stub_provider(root: Path, version: str = REQUIRED_VERSION) -> Path:
    """A shell fake: answers --version, then emits a mode-selected transcript."""

    provider = root / "stub-provider"
    script = f"""#!/bin/sh
if [ "$#" -ge 1 ] && [ "$1" = "--version" ]; then
  printf '%s\\n' '{version}'
  exit 0
fi
/bin/cat > /dev/null
case "${{FAKE_MODE:-with-cost}}" in
  with-cost)
    if [ -n "${{FAKE_INIT:-}}" ]; then
      printf '%s\\n' "$FAKE_INIT"
    else
      printf '%s\\n' '{{"type":"system","subtype":"init"}}'
    fi
    printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":"%s"}}\\n' "${{FAKE_COST:-0.1250000}}"
    ;;
  no-result-event)
    if [ -n "${{FAKE_INIT:-}}" ]; then
      printf '%s\\n' "$FAKE_INIT"
    else
      printf '%s\\n' '{{"type":"system","subtype":"init"}}'
    fi
    ;;
  two-result-events)
    printf '%s\\n' '{{"type":"result","total_cost_usd":"0.1000000"}}'
    printf '%s\\n' '{{"type":"result","total_cost_usd":"0.2000000"}}'
    ;;
  process-error)
    printf '%s\\n' '{{"type":"result","total_cost_usd":"0.1250000"}}'
    exit 23
    ;;
  malformed-cost)
    printf '%s\\n' '{{"type":"result","total_cost_usd":"not-canonical"}}'
    ;;
esac
"""
    provider.write_text(script, encoding="utf-8")
    provider.chmod(0o700)
    return provider


def write_fd9_probe_provider(root: Path, version: str = REQUIRED_VERSION) -> Path:
    """A python fake: answers --version, then reports whether FD 9 is open."""

    provider = root / "fd9-probe-provider"
    script = f"""#!/usr/bin/env python3
import json
import os
import sys

if len(sys.argv) >= 2 and sys.argv[1] == "--version":
    sys.stdout.write("{version}\\n")
    raise SystemExit(0)

sys.stdin.read()
try:
    os.fstat(9)
except OSError:
    fd9 = "closed"
else:
    fd9 = "open"
sys.stdout.write(json.dumps({{"type": "result", "fd9": fd9}}) + "\\n")
"""
    provider.write_text(script, encoding="utf-8")
    provider.chmod(0o700)
    return provider


def make_invocation(
    provider: Path | str,
    cwd: Path,
    *,
    argv_tail: tuple[str, ...] = ("--output-format", "stream-json"),
    prompt: str = "offline composition prompt",
    environment: dict[str, str] | None = None,
) -> Any:
    return runner.ModelInvocation(
        argv=(str(provider), *argv_tail),
        cwd=str(cwd),
        prompt=prompt,
        environment=dict(environment or {}),
        task_visible_run_id=cwd.name,
    )


class FakeControllerPopen:
    """Stands in for the Popen epoch_controller.spawn_sandboxed_provider returns."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr


def _intercept_sandboxed_popen_only(handler: Any) -> Any:
    """A subprocess.Popen side_effect that only intercepts the sandboxed launch.

    ``claude_executor.py`` and ``epoch_controller.py`` both do a plain
    ``import subprocess``, so ``claude.subprocess`` and ``controller.subprocess``
    are the exact same real module: patching ``Popen`` unconditionally would
    also break the executor's own unrelated ``--version`` preflight (which
    goes through ``subprocess.run``, itself implemented via ``Popen``). Only
    a command whose argv[0] is the real sandbox-exec path -- i.e. the actual
    provider launch -- is routed to ``handler``; everything else reaches the
    real ``Popen`` unchanged.
    """

    real_popen = controller.subprocess.Popen

    def _side_effect(command: Any, **kwargs: Any) -> Any:
        if command and command[0] == str(controller.SANDBOX_EXEC_PATH):
            return handler(command, **kwargs)
        return real_popen(command, **kwargs)

    return _side_effect


class ReservedProviderSlotCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # This is a test-only lock in a TemporaryDirectory, never the real
        # epoch lock. Card A itself performs the real lock acquisition.
        cls.lock_directory = tempfile.TemporaryDirectory()
        cls.lock = controller.EpochLock(Path(cls.lock_directory.name) / "owner.lock")
        cls.lock.acquire()

    @classmethod
    def tearDownClass(cls) -> None:
        os.close(controller.LOCK_FD)
        controller._PROCESS_LOCK_ACQUIRED = False
        cls.lock_directory.cleanup()

    def authority(
        self, root: Path, ledger: Any, invocation: Any, slot_id: str = "slot-a",
        *, pair_slot_ids: tuple[str, str] = ("slot-a", "slot-b"),
        admit_pair: bool = True,
    ) -> Any:
        usd = controller._usd
        epoch_id = "c" * 32
        def artifact(path: Path) -> dict[str, str]:
            raw = path.read_bytes()
            return {"blob_oid": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                    "sha256": hashlib.sha256(raw).hexdigest()}
        manifest = usd.build_manifest(
            epoch_id,
            final_runtime_authority={
                "commit": "a" * 40, "tree": "b" * 40,
                "runner": artifact(Path(runner.__file__)),
                "controller": artifact(Path(controller.__file__)),
                "sandbox": artifact(PROFILE_PATH),
            },
            provider_adapter=artifact(Path(claude.__file__)),
            provider_runtime={"binary_path": invocation.argv[0],
                              "sha256": controller.sha256_file(invocation.argv[0]),
                              "version": REQUIRED_VERSION},
            task_pins={task: {key: "e" * 64 for key in
                             ("prompt_sha256", "fixture_sha256", "oracle_sha256")}
                       for task in usd.TASK_IDS},
            runtime_containment={
                "policy_id": "offline-fixture", "policy_sha256": controller.sha256_file(PROFILE_PATH),
                "epoch_write_scope": "PROSPECTIVE_EPOCH_ONLY",
                "filesystem_write_policy": usd.RUNTIME_FILESYSTEM_WRITE_POLICY,
                "filesystem_read_policy": usd.RUNTIME_FILESYSTEM_READ_POLICY,
                "process_policy": usd.RUNTIME_PROCESS_POLICY,
                "environment_policy": usd.RUNTIME_ENVIRONMENT_POLICY,
                "network_policy": usd.RUNTIME_NETWORK_POLICY,
                "network_enforcement": usd.RUNTIME_NETWORK_ENFORCEMENT,
                "provider_subprocess_composition": usd.RUNTIME_PROVIDER_SUBPROCESS_COMPOSITION,
                "provider_subprocess_required": True,
            },
            lock_tool_identities={name: {"binary_path": path,
                                        "sha256": controller.sha256_file(path),
                                        "version": "offline-test-tool-pin"}
                                  for name, path in (("lockf", "/usr/bin/lockf"),
                                                     ("lsof", "/usr/sbin/lsof"),
                                                     ("sandbox-exec", "/usr/bin/sandbox-exec"))},
            evidence_schema={"schema": "offline-test-only", "sha256": "d" * 64},
            s04_budget_attestation={
                "schema": usd.S04_ATTESTATION_SCHEMA,
                "decision": "AUTHORIZE_UNKNOWN_HISTORICAL_COST_HANDLING",
                "epoch_id": epoch_id, "run_id": usd.S04_RUN_ID,
                "cost_state": usd.S04_COST_STATE,
                "historical_charged_spend_usd": "0.2660758",
                "lifetime_hard_cap_usd": "10.0000000",
                "unknown_cost_treatment": "SYNTHETIC_OFFLINE_TEST_AUTHORITY_ONLY",
                "authorization_source_identity": {
                    "kind": "offline-test", "identifier": "not-real-owner-signoff", "sha256": "f" * 64
                },
            },
            initial_state={key: 0 for key in
                           ("run_count", "workspace_count", "reservation_count", "aggregate_count")},
        )
        self.assertEqual(manifest["publication_gate"]["status"], "PASS")
        manifest_path = root / "manifest.json"
        manifest_path.write_bytes(usd.serialize_manifest(manifest))
        validated = controller.load_manifest_after_lock(
            manifest_path, expected_sha256=usd.manifest_sha256(manifest)
        )
        self.assertEqual(validated, manifest)
        if admit_pair and not ledger.load().entries:
            ledger.reserve_pair(pair_slot_ids, SEALED_CAP, Decimal("4.0000000"))
        lifecycle = controller.LifecycleMachine(
            controller.LifecycleState.SEALED, usd.manifest_sha256(manifest)
        )
        lifecycle.observe_ledger(ledger.load())
        lifecycle.transition(controller.LifecycleState.RUNNING)
        return composition.ControllerSlotAuthority(
            self.lock, lifecycle, manifest_path, ledger, pair_slot_ids, slot_id, invocation
        )

    def execute(self, *, ledger: Any, slot_id: str, sealed_invocation_cap: Any,
                invocation: Any, session_root: Path, provider_executable: str,
                required_provider_version: str) -> Any:
        # Test controller setup is deliberately outside the production entry.
        composition._canonical_cap(sealed_invocation_cap)
        self.assertEqual(Decimal(sealed_invocation_cap), SEALED_CAP)
        pair = ("slot-a", "slot-b") if slot_id in ("slot-a", "slot-b") else (slot_id, slot_id + "-peer")
        authority = self.authority(session_root.parent, ledger, invocation, slot_id,
                                   pair_slot_ids=pair)
        return composition.execute_reserved_provider_slot(authority=authority, invocation=invocation)

    def ledger(self, root: Path, cap: str = "9.7339242") -> Any:
        return controller.BudgetLedger(root / "ledger.jsonl", Decimal(cap))

    def compose(
        self,
        root: Path,
        *,
        token: str = SESSION_TOKEN_A,
        ledger: Any | None = None,
        slot_id: str = "slot-a",
        environment: dict[str, str] | None = None,
        sealed_invocation_cap: Decimal | str | int = SEALED_CAP,
    ) -> tuple[Any, Path, Any]:
        stub = write_stub_provider(root)
        session_root = controller.create_opaque_session_root(root, lambda: token)
        invocation = make_invocation(stub, session_root, environment=environment)
        used_ledger = self.ledger(root) if ledger is None else ledger
        result = self.execute(
            ledger=used_ledger,
            slot_id=slot_id,
            sealed_invocation_cap=sealed_invocation_cap,
            invocation=invocation,
            session_root=session_root,
            provider_executable=str(stub),
            required_provider_version=REQUIRED_VERSION,
        )
        return result, session_root, used_ledger

    def test_01_composition_builds_logical_and_sandbox_prefixed_physical_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, session_root, _ = self.compose(Path(temporary))

        logical = result.logical_argv
        physical = result.physical_argv
        self.assertNotEqual(logical, physical)
        self.assertIsNotNone(physical)
        # The physical lane prefixes, and never replaces, the logical lane.
        self.assertEqual(physical[0], str(controller.SANDBOX_EXEC_PATH))
        self.assertEqual(physical[1], "-f")
        self.assertEqual(physical[2], str(PROFILE_PATH.resolve()))
        self.assertEqual(physical[3], "-D")
        self.assertEqual(physical[4], f"SESSION_ROOT={session_root.resolve()}")
        self.assertEqual(physical[5:], logical)
        self.assertNotIn(str(controller.SANDBOX_EXEC_PATH), logical)

    def test_02_reservation_start_intent_and_settlement_are_ordered_in_the_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            result, _, _ = self.compose(root, ledger=ledger)

            entries = ledger.load().entries
            transitions = [entry["transition"] for entry in entries]
            self.assertEqual(
                transitions, ["reservation", "reservation", "provider-start-intent", "settlement"]
            )
            self.assertEqual(entries[0]["amount"], "2.0000000")
            self.assertEqual(entries[1]["amount"], "2.0000000")
            self.assertEqual(entries[2]["amount"], "2.0000000")
            self.assertEqual(entries[3]["amount"], "0.1250000")
            for later, earlier in zip(entries[1:], entries[:-1]):
                self.assertEqual(later["previous_hash"], earlier["entry_hash"])

            self.assertIsNotNone(result.reservation_record)
            self.assertIsNotNone(result.start_intent_record)
            self.assertIsNotNone(result.settlement_record)
            self.assertTrue(
                result.settlement_authority.passed, result.settlement_authority.reasons
            )
            self.assertEqual(result.sealed_invocation_cap_usd, "2.0000000")

            snapshot = ledger.load()
            self.assertEqual(snapshot.outstanding_reservations, {"slot-b": SEALED_CAP})
            self.assertEqual(snapshot.started_unresolved, {})
            self.assertEqual(snapshot.lifetime_total_spend, Decimal("0.1250000"))

    def test_03_unresolved_terminal_cost_retains_reservation_and_stays_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            result, _, _ = self.compose(
                root, ledger=ledger, environment={"FAKE_MODE": "no-result-event"}
            )

            self.assertIsNone(result.settlement_record)
            self.assertFalse(result.settlement_authority.passed)
            self.assertIn("settlement_unresolved", result.settlement_authority.reasons)
            snapshot = ledger.load()
            self.assertEqual(snapshot.started_unresolved, {"slot-a": Decimal("2.0000000")})
            self.assertEqual(
                snapshot.outstanding_reservations, {"slot-a": SEALED_CAP, "slot-b": SEALED_CAP}
            )
            self.assertEqual(snapshot.lifetime_total_spend, Decimal("0"))

    def test_04_ambiguous_result_events_leave_cost_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            result, _, _ = self.compose(
                root, ledger=ledger, environment={"FAKE_MODE": "two-result-events"}
            )

            self.assertIsNone(result.settlement_record)
            self.assertFalse(result.settlement_authority.passed)
            self.assertEqual(
                ledger.load().started_unresolved, {"slot-a": Decimal("2.0000000")}
            )

    def test_05_malformed_cost_leaves_cost_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            result, _, _ = self.compose(
                root, ledger=ledger, environment={"FAKE_MODE": "malformed-cost"}
            )

            self.assertIsNone(result.settlement_record)
            self.assertFalse(result.settlement_authority.passed)
            self.assertEqual(
                ledger.load().started_unresolved, {"slot-a": Decimal("2.0000000")}
            )

    def test_06_settlement_exceeding_the_sealed_cap_raises_and_reservation_survives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            invocation = make_invocation(
                stub,
                session_root,
                environment={"FAKE_MODE": "with-cost", "FAKE_COST": "3.0000000"},
            )

            with self.assertRaises(controller.LedgerError):
                self.execute(
                    ledger=ledger,
                    slot_id="slot-a",
                    sealed_invocation_cap=SEALED_CAP,
                    invocation=invocation,
                    session_root=session_root,
                    provider_executable=str(stub),
                    required_provider_version=REQUIRED_VERSION,
                )

            snapshot = ledger.load()
            self.assertEqual(snapshot.started_unresolved, {"slot-a": Decimal("2.0000000")})
            self.assertEqual(snapshot.lifetime_total_spend, Decimal("0"))

            # Spawning again for the identical slot must never be a silent retry.
            with self.assertRaisesRegex(composition.CompositionError, "no_silent_rerun"):
                self.execute(
                    ledger=ledger,
                    slot_id="slot-a",
                    sealed_invocation_cap=SEALED_CAP,
                    invocation=invocation,
                    session_root=session_root,
                    provider_executable=str(stub),
                    required_provider_version=REQUIRED_VERSION,
                )

    def test_07_spawn_failure_leaves_a_durable_start_intent_and_forbids_silent_relaunch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            invocation = make_invocation(stub, session_root)

            def induced_spawn_failure(command: Any, **kwargs: Any) -> Any:
                raise OSError("induced")

            with mock.patch.object(
                controller.subprocess,
                "Popen",
                side_effect=_intercept_sandboxed_popen_only(induced_spawn_failure),
            ):
                with self.assertRaises(claude.ProviderVersionError):
                    self.execute(
                        ledger=ledger,
                        slot_id="slot-a",
                        sealed_invocation_cap=SEALED_CAP,
                        invocation=invocation,
                        session_root=session_root,
                        provider_executable=str(stub),
                        required_provider_version=REQUIRED_VERSION,
                    )

            snapshot = ledger.load()
            self.assertEqual(snapshot.started_unresolved, {"slot-a": Decimal("2.0000000")})
            self.assertEqual(snapshot.lifetime_total_spend, Decimal("0"))

            with self.assertRaisesRegex(composition.CompositionError, "no_silent_rerun"):
                self.execute(
                    ledger=ledger,
                    slot_id="slot-a",
                    sealed_invocation_cap=SEALED_CAP,
                    invocation=invocation,
                    session_root=session_root,
                    provider_executable=str(stub),
                    required_provider_version=REQUIRED_VERSION,
                )

    def test_08_launcher_refuses_unsafe_launch_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            launcher = composition.SandboxedProviderLauncher(
                self.authority(root, self.ledger(root), make_invocation(stub, session_root))
            )
            argv = (str(stub), "--output-format", "stream-json")
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
                            launcher(argv, **overrides)
                popen.assert_not_called()

            self.assertEqual(launcher.launch_count, 0)
            self.assertIsNone(launcher.identity_record())

    def test_09_launcher_refuses_a_second_launch_on_the_same_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            ledger.reserve_pair(("slot-a", "slot-b"), SEALED_CAP, Decimal("4.0000000"))
            launcher = composition.SandboxedProviderLauncher(
                self.authority(root, ledger, make_invocation(stub, session_root))
            )
            argv = (str(stub), "--output-format", "stream-json")
            kwargs = dict(
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(session_root),
            )

            process = launcher(argv, **kwargs)
            process.communicate(timeout=30)

            with self.assertRaisesRegex(composition.CompositionError, "already_launched"):
                launcher(argv, **kwargs)

    def test_10_existing_matching_reservation_is_reused_not_recreated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            pre_existing = ledger.reserve_pair(("slot-a", "slot-b"), SEALED_CAP, Decimal("4.0000000"))[0]
            result, _, _ = self.compose(root, ledger=ledger)

            self.assertEqual(
                result.reservation_record["entry_hash"], pre_existing["entry_hash"]
            )
            reservation_entries = [
                entry for entry in ledger.load().entries if entry["transition"] == "reservation"
            ]
            self.assertEqual(len(reservation_entries), 2)

    def test_11_existing_reservation_with_a_different_cap_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            ledger.reserve("slot-a", Decimal("1.0000000"))
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            invocation = make_invocation(stub, session_root)

            with self.assertRaisesRegex(
                composition.CompositionError, "does_not_match_sealed_cap"
            ):
                self.execute(
                    ledger=ledger,
                    slot_id="slot-a",
                    sealed_invocation_cap=SEALED_CAP,
                    invocation=invocation,
                    session_root=session_root,
                    provider_executable=str(stub),
                    required_provider_version=REQUIRED_VERSION,
                )

    def test_12_sub_quantum_sealed_cap_is_refused_before_any_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            invocation = make_invocation(stub, session_root)

            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(
                    composition.CompositionError, "canonical persisted USD"
                ):
                    self.execute(
                        ledger=ledger,
                        slot_id="slot-a",
                        sealed_invocation_cap=Decimal("0.00000001"),
                        invocation=invocation,
                        session_root=session_root,
                        provider_executable=str(stub),
                        required_provider_version=REQUIRED_VERSION,
                    )
                popen.assert_not_called()
            self.assertFalse((root / "ledger.jsonl").exists())

    def test_13_slot_already_consumed_cannot_be_reserved_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            result, session_root, _ = self.compose(root, ledger=ledger)
            self.assertTrue(result.settlement_authority.passed)

            stub = write_stub_provider(root)
            invocation = make_invocation(stub, session_root)
            with self.assertRaisesRegex(composition.CompositionError, "already_consumed"):
                self.execute(
                    ledger=ledger,
                    slot_id="slot-a",
                    sealed_invocation_cap=SEALED_CAP,
                    invocation=invocation,
                    session_root=session_root,
                    provider_executable=str(stub),
                    required_provider_version=REQUIRED_VERSION,
                )

    def test_14_fd9_does_not_reach_the_real_sandboxed_provider_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            stub = write_fd9_probe_provider(root)
            session_root = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            # /usr/bin/env needs PATH to locate python3; the shell-based stub
            # in other tests needs no environment at all, but this one does.
            invocation = make_invocation(
                stub, session_root, environment={"PATH": "/usr/bin:/bin"}
            )
            # The real controller owns FD 9. Even if made inheritable by a
            # hostile caller, both sandboxed children must exclude it.
            os.fstat(9)
            os.set_inheritable(9, True)
            try:
                result = self.execute(
                    ledger=ledger, slot_id="slot-fd9", sealed_invocation_cap=SEALED_CAP,
                    invocation=invocation, session_root=session_root,
                    provider_executable=str(stub), required_provider_version=REQUIRED_VERSION,
                )
            finally:
                os.set_inheritable(9, False)

            events = list(result.provider_execution)
            self.assertEqual(events[0]["fd9"], "closed")

    def test_15_composition_never_reaches_an_unsandboxed_popen(self) -> None:
        recorded: list[tuple[str, ...]] = []

        def fake_popen(command: Any, **kwargs: Any) -> Any:
            recorded.append(tuple(command))
            self.assertTrue(kwargs["close_fds"])
            self.assertTrue(kwargs["start_new_session"])
            if command[-1] == "--version":
                return FakeControllerPopen(stdout=(REQUIRED_VERSION + "\n").encode())
            return FakeControllerPopen(
                stdout=b'{"type":"result","total_cost_usd":"0.1250000"}\n'
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            stub = write_stub_provider(root)
            session_root = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            invocation = make_invocation(stub, session_root)

            with mock.patch.object(claude.subprocess, "Popen") as claude_popen:
                with mock.patch.object(
                    controller.subprocess, "Popen", side_effect=fake_popen
                ):
                    self.execute(
                        ledger=ledger,
                        slot_id="slot-a",
                        sealed_invocation_cap=SEALED_CAP,
                        invocation=invocation,
                        session_root=session_root,
                        provider_executable=str(stub),
                        required_provider_version=REQUIRED_VERSION,
                    )
                claude_popen.assert_not_called()

        self.assertEqual(len(recorded), 2)
        self.assertTrue(all(argv[0] == str(controller.SANDBOX_EXEC_PATH) for argv in recorded))
        command = recorded[-1]
        self.assertEqual(command[0], str(controller.SANDBOX_EXEC_PATH))

    def test_16_condition_neutrality_holds_across_two_opaque_session_roots(self) -> None:
        # One pinned provider binary and one ledger, two independently random
        # session roots -- exactly the shape a real OFF/ON pair takes.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            left, _, _ = self.compose(root, token=SESSION_TOKEN_A, ledger=ledger, slot_id="slot-a")
            right, _, _ = self.compose(root, token=SESSION_TOKEN_B, ledger=ledger, slot_id="slot-b")

        self.assertNotEqual(left.physical_argv, right.physical_argv)
        self.assertEqual(left.logical_argv, right.logical_argv)
        self.assertEqual(
            left.sandbox_identity["profile_sha256"], right.sandbox_identity["profile_sha256"]
        )

    def test_17_as_record_round_trips_through_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result, _, _ = self.compose(Path(temporary))
            record = result.as_record()
        restored = json.loads(json.dumps(record, sort_keys=True))
        self.assertEqual(restored["slot_id"], "slot-a")
        self.assertEqual(restored["sealed_invocation_cap_usd"], "2.0000000")
        self.assertEqual(restored["logical_argv"], list(result.logical_argv))
        self.assertEqual(restored["physical_argv"], list(result.physical_argv))
        self.assertTrue(restored["settlement_authority"]["passed"])
        self.assertIsNotNone(restored["settlement_record"])

    def prepared(self, root: Path) -> tuple[Any, Any, Any]:
        stub = write_stub_provider(root)
        session = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
        invocation = make_invocation(stub, session)
        ledger = self.ledger(root)
        return self.authority(root, ledger, invocation), invocation, ledger

    def test_18_arbitrary_ledger_slot_cap_cannot_create_production_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = self.ledger(root)
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with self.assertRaises(TypeError):
                    composition.execute_reserved_provider_slot(
                        ledger=ledger, slot_id="slot-a", sealed_invocation_cap=SEALED_CAP,
                        invocation=None,
                    )
                with self.assertRaises(composition.CompositionError):
                    composition.execute_reserved_provider_slot(authority=None, invocation=None)
                popen.assert_not_called()
            self.assertFalse(ledger.path.exists())

    def test_19_one_arm_reservation_never_launches_or_reserves_its_peer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            session = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            ledger = self.ledger(root)
            ledger.reserve("slot-a", SEALED_CAP)
            before = ledger.path.read_bytes()
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(composition.CompositionError, "whole_pair"):
                    self.authority(root, ledger, make_invocation(stub, session), admit_pair=False)
                popen.assert_not_called()
            self.assertEqual(ledger.path.read_bytes(), before)

    def test_20_lost_lock_and_incomplete_lifecycle_refuse_before_any_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, invocation, ledger = self.prepared(Path(temporary))
            before = ledger.path.read_bytes()
            mutations = [(authority.lock, "acquired", False),
                         (authority.lifecycle, "state", controller.LifecycleState.SEALED),
                         (authority.lifecycle, "durable_reservation_or_start_seen", False)]
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                for owner, attribute, value in mutations:
                    with self.subTest(attribute=attribute), mock.patch.object(owner, attribute, value):
                        with self.assertRaises(composition.CompositionError):
                            composition.execute_reserved_provider_slot(authority=authority, invocation=invocation)
                popen.assert_not_called()
            self.assertEqual(ledger.path.read_bytes(), before)

    def test_21_manifest_bytes_must_still_match_controller_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, invocation, ledger = self.prepared(Path(temporary))
            before = ledger.path.read_bytes()
            authority.manifest_path.write_bytes(authority.manifest_path.read_bytes() + b" ")
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with self.assertRaises(controller.LifecycleError):
                    composition.execute_reserved_provider_slot(authority=authority, invocation=invocation)
                popen.assert_not_called()
            self.assertEqual(ledger.path.read_bytes(), before)

    def test_22_runtime_bytes_and_manifest_budget_cannot_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, invocation, ledger = self.prepared(Path(temporary))
            before = ledger.path.read_bytes()
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with mock.patch.object(ledger, "lifetime_hard_cap", Decimal("10.0000000")):
                    with self.assertRaisesRegex(composition.CompositionError, "ledger_authority"):
                        composition.execute_reserved_provider_slot(authority=authority, invocation=invocation)
                provider = Path(invocation.argv[0])
                provider.write_bytes(provider.read_bytes() + b"\n# drift\n")
                with self.assertRaisesRegex(composition.CompositionError, "provider_runtime"):
                    composition.execute_reserved_provider_slot(authority=authority, invocation=invocation)
                popen.assert_not_called()
            self.assertEqual(ledger.path.read_bytes(), before)

    def test_23_invocation_binding_covers_prompt_environment_workspace_and_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, invocation, ledger = self.prepared(Path(temporary))
            before = ledger.path.read_bytes()
            changes = ({"prompt": "different"}, {"environment": {"INJECTED": "1"}},
                       {"cwd": str(Path(temporary))}, {"task_visible_run_id": "foreign"},
                       {"argv": (*invocation.argv, "--foreign")})
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                for fields in changes:
                    with self.subTest(fields=fields), self.assertRaisesRegex(
                            composition.CompositionError, "bound_invocation"):
                        composition.execute_reserved_provider_slot(
                            authority=authority, invocation=replace(invocation, **fields)
                        )
                popen.assert_not_called()
            self.assertEqual(ledger.path.read_bytes(), before)

    def test_24_released_peer_and_start_before_pair_admission_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, invocation, ledger = self.prepared(Path(temporary))
            ledger.release("slot-b")
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(composition.CompositionError, "whole_pair"):
                    composition.execute_reserved_provider_slot(authority=authority, invocation=invocation)
                popen.assert_not_called()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            session = controller.create_opaque_session_root(root, lambda: SESSION_TOKEN_A)
            ledger = self.ledger(root)
            ledger.reserve("slot-a", SEALED_CAP)
            ledger.provider_start_intent("slot-a", SEALED_CAP)
            ledger.reserve("slot-b", SEALED_CAP)
            with self.assertRaisesRegex(composition.CompositionError, "whole_pair"):
                self.authority(root, ledger, make_invocation(stub, session), "slot-b", admit_pair=False)

    def test_25_durable_pair_and_start_intent_precede_both_physical_children(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, invocation, ledger = self.prepared(Path(temporary))
            real_popen = controller.subprocess.Popen
            observed = []
            def observe(argv: Any, **kwargs: Any) -> Any:
                snapshot = ledger.load()
                self.assertEqual([e["transition"] for e in snapshot.entries],
                                 ["reservation", "reservation", "provider-start-intent"])
                self.assertEqual(snapshot.started_unresolved, {"slot-a": SEALED_CAP})
                self.assertEqual(argv[0], str(controller.SANDBOX_EXEC_PATH))
                self.assertTrue(kwargs["close_fds"])
                self.assertNotIn("pass_fds", kwargs)
                observed.append(tuple(argv))
                return real_popen(argv, **kwargs)
            with mock.patch.object(controller.subprocess, "Popen", side_effect=observe):
                result = composition.execute_reserved_provider_slot(authority=authority, invocation=invocation)
            self.assertTrue(result.settlement_authority.passed)
            self.assertEqual(len(observed), 2)
            self.assertEqual(observed[0][-1], "--version")
            self.assertEqual(observed[1][5:], invocation.argv)

    def run_composed(self, *, mode: str = "with-cost", run_event: Any = None,
                     global_events: Any = None, transform: Any = None,
                     invocation_transform: Any = None, settlement_failure: bool = False) -> tuple[Any, Any]:
        # The existing runner's injected slot interface is used unchanged;
        # this is not a v3 execution projection or an epoch driver.
        import test_runner as fixtures
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = write_stub_provider(root)
            ledger = self.ledger(root)
            pair = ("s01-A01-r0-OFF", "s02-A01-r0-ON")
            event = fixtures.carrier_init_event(carrier=True) if run_event is None else run_event
            global_events = ({"claude": {
                "OFF": [fixtures.carrier_init_event(carrier=False)],
                "ON": [fixtures.carrier_init_event(carrier=True)],
            }} if global_events is None else global_events)
            def execute(invocation: Any) -> Any:
                request = invocation if invocation_transform is None else invocation_transform(invocation)
                authority = self.authority(root, ledger, request, pair[1], pair_slot_ids=pair)
                if settlement_failure:
                    with mock.patch.object(ledger, "settle", side_effect=controller.LedgerError("induced")):
                        return composition.execute_reserved_provider_slot(authority=authority, invocation=request)
                result = composition.execute_reserved_provider_slot(authority=authority, invocation=request)
                return result if transform is None else transform(result, ledger)
            evidence = runner.execute_manifest_slot(
                fixtures.synthetic_manifest(str(stub)), pair[1], workspace_parent=root,
                reference_events=[fixtures.init_event()],
                materializer=lambda plan: fixtures.make_git_repository(Path(plan.workspace_path)),
                executor=execute, provider_instruction_surface_events=global_events,
                expected_providers=("claude",),
                environment={"FAKE_MODE": mode, "FAKE_INIT": json.dumps(event)},
                opaque_id_factory=lambda: SESSION_TOKEN_A,
            )
            return evidence, ledger.load()

    def test_26_final_countability_requires_and_accepts_exact_durable_settlement(self) -> None:
        evidence, snapshot = self.run_composed()
        self.assertTrue(evidence.purity.purity_pass, evidence.purity.reasons)
        self.assertTrue(evidence.global_instruction_surface.passed)
        self.assertTrue(evidence.settlement_authority.passed, evidence.settlement_authority.reasons)
        self.assertTrue(evidence.run_countable, evidence.as_record())
        self.assertEqual(snapshot.lifetime_total_spend, Decimal("0.1250000"))

    def test_27_missing_foreign_and_non_durable_settlement_or_start_fail_final_gate(self) -> None:
        def foreign(result: Any, ledger: Any) -> Any:
            other = "s01-A01-r0-OFF"
            ledger.provider_start_intent(other, SEALED_CAP)
            record = ledger.settle(other, Decimal("0.2500000"))
            result.settlement_record.clear()
            result.settlement_record.update(record)
            return result
        changes = {
            "missing": lambda r, l: replace(r, settlement_record=None),
            "foreign_slot": foreign,
            "wrong_run": lambda r, l: replace(r, slot_id="other-slot"),
            "missing_start": lambda r, l: replace(r, start_intent_record=None),
            "non_durable": lambda r, l: replace(r, settlement_record={**r.settlement_record, "amount": "0.2000000"}),
        }
        for label, transform in changes.items():
            with self.subTest(label=label):
                evidence, snapshot = self.run_composed(transform=transform)
                self.assertTrue(evidence.purity.purity_pass)
                self.assertTrue(evidence.global_instruction_surface.passed)
                self.assertFalse(evidence.settlement_authority.passed)
                self.assertFalse(evidence.run_countable)
                self.assertGreaterEqual(snapshot.lifetime_total_spend, Decimal("0.1250000"))

    def test_28_same_slot_different_invocation_cannot_borrow_settlement(self) -> None:
        evidence, snapshot = self.run_composed(
            invocation_transform=lambda i: replace(i, prompt="a different executed prompt")
        )
        self.assertTrue(evidence.purity.purity_pass)
        self.assertFalse(evidence.settlement_authority.passed)
        self.assertIn("settlement_execution_identity_mismatch", evidence.settlement_authority.reasons)
        self.assertFalse(evidence.run_countable)
        self.assertEqual(snapshot.lifetime_total_spend, Decimal("0.1250000"))

    def test_29_unknown_cost_is_uncountable_and_retains_full_cap(self) -> None:
        evidence, snapshot = self.run_composed(mode="no-result-event")
        self.assertTrue(evidence.purity.purity_pass)
        self.assertTrue(evidence.global_instruction_surface.passed)
        self.assertFalse(evidence.settlement_authority.passed)
        self.assertFalse(evidence.run_countable)
        self.assertEqual(snapshot.started_unresolved, {"s02-A01-r0-ON": SEALED_CAP})
        self.assertEqual(snapshot.outstanding_total, Decimal("4.0000000"))
        self.assertEqual(snapshot.lifetime_total_spend, Decimal("0"))

    def test_30_failed_settlement_remains_uncountable_with_reserved_cost(self) -> None:
        evidence, snapshot = self.run_composed(settlement_failure=True)
        self.assertFalse(evidence.run_countable)
        self.assertFalse(evidence.settlement_authority.passed)
        self.assertIn("LedgerError", evidence.executor_error)
        self.assertEqual(snapshot.started_unresolved, {"s02-A01-r0-ON": SEALED_CAP})

    def test_31_global_failure_and_missing_surfaces_do_not_erase_known_cost(self) -> None:
        import test_runner as fixtures
        variants = ({}, {"claude": {
            "OFF": [fixtures.carrier_init_event(carrier=False)],
            "ON": [fixtures.carrier_init_event(carrier=True, output_style="drift")],
        }})
        for events in variants:
            with self.subTest(events=events):
                evidence, snapshot = self.run_composed(global_events=events)
                self.assertTrue(evidence.purity.purity_pass)
                self.assertTrue(evidence.settlement_authority.passed)
                self.assertFalse(evidence.global_instruction_surface.passed)
                self.assertFalse(evidence.run_countable)
                self.assertEqual(snapshot.lifetime_total_spend, Decimal("0.1250000"))

    def test_32_purity_and_inventory_failures_do_not_erase_known_cost(self) -> None:
        import test_runner as fixtures
        variants = [fixtures.carrier_init_event(carrier=False),
                    fixtures.carrier_init_event(carrier=True, tools=["Unexpected"]),
                    {"type": "system", "subtype": "init"}]
        for event in variants:
            with self.subTest(event=event):
                evidence, snapshot = self.run_composed(run_event=event)
                self.assertTrue(evidence.global_instruction_surface.passed)
                self.assertTrue(evidence.settlement_authority.passed)
                self.assertFalse(evidence.purity.purity_pass)
                self.assertFalse(evidence.run_countable)
                self.assertEqual(snapshot.lifetime_total_spend, Decimal("0.1250000"))

    def test_33_nonzero_execution_with_known_cost_still_settles(self) -> None:
        evidence, snapshot = self.run_composed(mode="process-error")
        self.assertFalse(evidence.run_countable)
        self.assertIn("ProviderProcessError", evidence.executor_error)
        self.assertEqual(snapshot.lifetime_total_spend, Decimal("0.1250000"))
        self.assertEqual(snapshot.started_unresolved, {})
        self.assertEqual(snapshot.outstanding_reservations, {"s01-A01-r0-OFF": SEALED_CAP})

    def test_34_caller_constructed_receipt_cannot_reuse_real_settlement(self) -> None:
        evidence, snapshot = self.run_composed(transform=lambda result, ledger: replace(result))
        self.assertTrue(evidence.purity.purity_pass)
        self.assertTrue(evidence.global_instruction_surface.passed)
        self.assertFalse(evidence.run_countable)
        self.assertIn("sanctioned_execution_receipt_required", evidence.settlement_authority.reasons)
        self.assertEqual(snapshot.lifetime_total_spend, Decimal("0.1250000"))

    def test_35_final_countability_regression_detects_removed_settlement_conjunct(self) -> None:
        # Controlled in-memory mutation: no product file or WIP is rewritten.
        # Run the actual unknown-cost regression against a copy with precisely
        # the old missing-conjunct defect, require its countability assertion
        # to fail, then restore the function and require the same check green.
        source = inspect.getsource(runner.execute_manifest_slot)
        guarded = "        and settlement_authority.passed\n"
        self.assertEqual(source.count(guarded), 1)
        namespace = dict(vars(runner))
        exec(compile(source.replace(guarded, "", 1), "<offline-gate-mutation>", "exec"), namespace)
        regression = type(self)("test_29_unknown_cost_is_uncountable_and_retains_full_cap")
        observed = unittest.TestResult()
        with mock.patch.object(runner, "execute_manifest_slot", namespace["execute_manifest_slot"]):
            regression.run(observed)
        self.assertEqual(observed.errors, [])
        self.assertEqual(len(observed.failures), 1)
        self.assertIn("self.assertFalse(evidence.run_countable)", observed.failures[0][1])
        restored = unittest.TestResult()
        type(self)("test_29_unknown_cost_is_uncountable_and_retains_full_cap").run(restored)
        self.assertTrue(restored.wasSuccessful(), restored.failures + restored.errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
