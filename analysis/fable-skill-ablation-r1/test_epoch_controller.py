#!/usr/bin/env python3
"""Offline acceptance tests for the locked Fable epoch controller (Card A)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import select
import stat
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest import mock


# Importing the sibling modules must not leave __pycache__ in the repository.
sys.dont_write_bytecode = True

CONTROLLER_PATH = Path(__file__).with_name("epoch_controller.py")
BUILD_MANIFEST_PATH = Path(__file__).with_name("build_epoch_manifest.py")
PROFILE_PATH = Path(__file__).with_name("claude-runtime.sb")
SCRIPT_PATH = Path(__file__).with_name("run_epoch_locked.sh")

# Registered under the exact name epoch_controller's own authority resolver
# looks for, so both this test module and the controller share one loaded
# build_epoch_manifest instance instead of importing the file twice.
_MANIFEST_SPEC = importlib.util.spec_from_file_location(
    "build_epoch_manifest", BUILD_MANIFEST_PATH
)
if _MANIFEST_SPEC is None or _MANIFEST_SPEC.loader is None:
    raise RuntimeError(f"cannot import {BUILD_MANIFEST_PATH}")
build_epoch_manifest = importlib.util.module_from_spec(_MANIFEST_SPEC)
sys.modules[_MANIFEST_SPEC.name] = build_epoch_manifest
_MANIFEST_SPEC.loader.exec_module(build_epoch_manifest)

_CONTROLLER_SPEC = importlib.util.spec_from_file_location(
    "fable_ablation_epoch_controller", CONTROLLER_PATH
)
if _CONTROLLER_SPEC is None or _CONTROLLER_SPEC.loader is None:
    raise RuntimeError(f"cannot import {CONTROLLER_PATH}")
controller = importlib.util.module_from_spec(_CONTROLLER_SPEC)
sys.modules[_CONTROLLER_SPEC.name] = controller
_CONTROLLER_SPEC.loader.exec_module(controller)

EPOCH_ID = "a" * 32


def controller_command(lock_path: Path, *arguments: str) -> list[str]:
    return [
        sys.executable,
        str(CONTROLLER_PATH),
        "--lock-path",
        str(lock_path),
        *arguments,
    ]


def run_controller(lock_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        controller_command(lock_path, *arguments),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
        close_fds=True,
    )


def start_lock_holder(lock_path: Path) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        controller_command(
            lock_path,
            "--probe-lock-only",
            "--hold-seconds",
            "30",
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        process.wait(timeout=5)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        raise AssertionError("holder pipes were not created")
    readable, _, _ = select.select([process.stdout], [], [], 5)
    if not readable:
        process.kill()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        raise AssertionError("holder did not report lock acquisition")
    line = process.stdout.readline().strip()
    if line != "FABLE_EPOCH_LOCK_ACQUIRED":
        stderr = process.stderr.read()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
        raise AssertionError(f"holder failed: {line!r} {stderr!r}")
    return process


def stop_test_process(process: subprocess.Popen[str], *, crash: bool = False) -> None:
    if process.poll() is None:
        if crash:
            process.kill()
        else:
            process.terminate()
        process.wait(timeout=5)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def write_v1_ledger(
    path: Path, transitions: list[tuple[str, str, str]]
) -> list[dict[str, Any]]:
    """Write a genuine pre-remediation version 1 ledger, bytes and all.

    The historical writer used ``decimal_text`` stripped decimals, so these
    records are the exact shape that already exists on disk today.
    """

    records: list[dict[str, Any]] = []
    previous_hash = "0" * 64
    for sequence, (transition, slot_id, amount) in enumerate(transitions, start=1):
        unsigned = {
            "version": 1,
            "sequence": sequence,
            "transition": transition,
            "slot_id": slot_id,
            "amount": amount,
            "previous_hash": previous_hash,
        }
        record = dict(unsigned)
        record["entry_hash"] = hashlib.sha256(
            json.dumps(
                unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("ascii")
        ).hexdigest()
        records.append(record)
        previous_hash = record["entry_hash"]
    path.write_bytes(
        b"".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")
            + b"\n"
            for record in records
        )
    )
    path.chmod(0o600)
    return records


def fake_stat(
    *,
    device: int = 10,
    inode: int = 20,
    mode: int = 0o600,
    uid: int | None = None,
    links: int = 1,
) -> os.stat_result:
    return os.stat_result(
        (
            stat.S_IFREG | mode,
            inode,
            device,
            links,
            os.getuid() if uid is None else uid,
            os.getgid(),
            0,
            0,
            0,
            0,
        )
    )


def process_identity(
    pid: int,
    *,
    start: str = "start-1",
    executable_inode: int = 300,
    pgid: int = 400,
    sid: int = 500,
    host: str = "host-1",
    boot: str = "boot-1",
) -> Any:
    return controller.ProcessIdentity(
        host=host,
        boot_id=boot,
        pid=pid,
        start_identity=start,
        executable=controller.ExecutableIdentity(
            path="/offline/provider", device=200, inode=executable_inode
        ),
        pgid=pgid,
        sid=sid,
    )


def reconcile(
    *,
    writer_lease: Any = None,
    live_processes: tuple[Any, ...] = (),
    roots: tuple[Path, ...] = (),
    ledger_snapshot: Any = None,
    run_evidence: tuple[Any, ...] = (),
    ledger_error: str | None = None,
) -> Any:
    return controller.reconcile_fresh_start(
        sealed_host="host-1",
        sealed_boot_id="boot-1",
        current_host="host-1",
        current_boot_id="boot-1",
        writer_lease=writer_lease,
        live_processes=live_processes,
        managed_roots=roots,
        ledger_snapshot=(
            controller.LedgerSnapshot() if ledger_snapshot is None else ledger_snapshot
        ),
        run_evidence=run_evidence,
        ledger_error=ledger_error,
        controller_pid=99999,
    )


def default_attestation(
    epoch_id: str, *, historical_charged_spend_usd: str | None = None
) -> dict[str, Any]:
    charged = (
        build_epoch_manifest.HISTORICAL_FORENSIC_SPEND_USD
        if historical_charged_spend_usd is None
        else historical_charged_spend_usd
    )
    return {
        "schema": build_epoch_manifest.S04_ATTESTATION_SCHEMA,
        "decision": "AUTHORIZE_UNKNOWN_HISTORICAL_COST_HANDLING",
        "epoch_id": epoch_id,
        "run_id": build_epoch_manifest.S04_RUN_ID,
        "cost_state": build_epoch_manifest.S04_COST_STATE,
        "historical_charged_spend_usd": charged,
        "lifetime_hard_cap_usd": build_epoch_manifest.LIFETIME_HARD_CAP_USD,
        "unknown_cost_treatment": "RETAIN_FULL_RESERVED_CAP_UNTIL_RECONCILED",
        "authorization_source_identity": {
            "kind": "test-fixture",
            "identifier": "card-a-focused-acceptance",
            "sha256": "0" * 64,
        },
    }


_DEFAULT = object()


def build_valid_manifest(
    epoch_id: str, *, attestation: Any = _DEFAULT
) -> tuple[dict[str, Any], bytes]:
    """Build a real, fully validated v3 manifest fixture.

    Card A must never accept a manifest through a locally reinvented
    schema; every fixture here is produced by the canonical compiler
    itself, exactly as a real Owner-authorized manifest would be.
    """

    resolved = default_attestation(epoch_id) if attestation is _DEFAULT else attestation
    manifest = build_epoch_manifest.build_manifest(
        epoch_id, s04_budget_attestation=resolved
    )
    data = build_epoch_manifest.serialize_manifest(manifest)
    return manifest, data


class ExternalLockTests(unittest.TestCase):
    def test_01_contention_precedes_manifest_read_and_authority_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / "epoch.lock"
            unreadable_manifest = root / "manifest.fifo"
            os.mkfifo(unreadable_manifest, 0o600)
            authority_marker = root / "authority-marker"
            authority_marker.write_text("unchanged\n", encoding="utf-8")
            holder = start_lock_holder(lock_path)
            try:
                contender = run_controller(
                    lock_path,
                    "--manifest",
                    str(unreadable_manifest),
                )
            finally:
                stop_test_process(holder)

            self.assertEqual(contender.returncode, controller.EXIT_OWNER_BUSY)
            self.assertEqual(contender.stderr.strip(), controller.OWNER_BUSY)
            self.assertEqual(authority_marker.read_text(encoding="utf-8"), "unchanged\n")

    def test_02_lock_path_symlink_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("sentinel\n", encoding="utf-8")
            target.chmod(0o600)
            link = root / "epoch.lock"
            link.symlink_to(target)
            result = run_controller(link, "--probe-lock-only")
            self.assertEqual(result.returncode, controller.EXIT_LOCK_FAILURE)
            self.assertIn(controller.LOCK_FAILURE, result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "sentinel\n")

    def test_03_fd_path_inode_mismatch_is_refused(self) -> None:
        with self.assertRaisesRegex(controller.EpochLockFailure, "inode_mismatch"):
            controller.validate_lock_metadata(
                fake_stat(inode=20), fake_stat(inode=21)
            )

    def test_04_wrong_owner_mode_and_link_count_are_refused(self) -> None:
        cases = {
            "wrong_owner": fake_stat(uid=os.getuid() + 1),
            "wrong_mode": fake_stat(mode=0o640),
            "wrong_link_count": fake_stat(links=2),
        }
        valid = fake_stat()
        for marker, candidate in cases.items():
            with self.subTest(marker=marker):
                with self.assertRaisesRegex(controller.EpochLockFailure, marker):
                    controller.validate_lock_metadata(valid, candidate)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrong_mode_path = root / "wrong-mode.lock"
            wrong_mode_path.write_text("persistent\n", encoding="utf-8")
            wrong_mode_path.chmod(0o640)
            wrong_mode_result = run_controller(
                wrong_mode_path, "--probe-lock-only"
            )
            self.assertEqual(
                wrong_mode_result.returncode, controller.EXIT_LOCK_FAILURE
            )
            self.assertIn("wrong_mode", wrong_mode_result.stderr)

            linked_path = root / "linked.lock"
            linked_path.write_text("persistent\n", encoding="utf-8")
            linked_path.chmod(0o600)
            os.link(linked_path, root / "linked-alias.lock")
            linked_result = run_controller(linked_path, "--probe-lock-only")
            self.assertEqual(linked_result.returncode, controller.EXIT_LOCK_FAILURE)
            self.assertIn("wrong_link_count", linked_result.stderr)

    def test_05_normal_exit_releases_without_replacing_or_truncating_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "epoch.lock"
            lock_path.write_text("persistent-not-metadata\n", encoding="utf-8")
            lock_path.chmod(0o600)
            inode = lock_path.stat().st_ino

            first = run_controller(lock_path, "--probe-lock-only")
            second = run_controller(lock_path, "--probe-lock-only")

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(lock_path.stat().st_ino, inode)
            self.assertEqual(
                lock_path.read_text(encoding="utf-8"), "persistent-not-metadata\n"
            )

    def test_06_sigkill_releases_lock_without_stale_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "epoch.lock"
            holder = start_lock_holder(lock_path)
            inode = lock_path.stat().st_ino
            stop_test_process(holder, crash=True)

            successor = run_controller(lock_path, "--probe-lock-only")
            self.assertEqual(successor.returncode, 0, successor.stderr)
            self.assertTrue(lock_path.exists())
            self.assertEqual(lock_path.stat().st_ino, inode)

    def test_07_fd9_is_not_inherited_by_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_controller(
                Path(temporary) / "epoch.lock",
                "--probe-child-fd9",
                "--probe-lock-only",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                ["FABLE_EPOCH_LOCK_ACQUIRED", "FD9_CLOSED"],
            )


class LifecycleAndLedgerTests(unittest.TestCase):
    def test_08_lifecycle_is_linear_and_authority_freezes_on_durable_intent(self) -> None:
        machine = controller.LifecycleMachine(
            controller.LifecycleState.SEALED, "a" * 64
        )
        machine.reseal("b" * 64)
        machine.observe_durable_transition("reservation")
        self.assertFalse(machine.authority_mutable)
        with self.assertRaisesRegex(controller.LifecycleError, "clean_SEALED"):
            machine.migrate("c" * 64)
        machine.transition(controller.LifecycleState.RUNNING)
        machine.verify_authority("b" * 64)
        with self.assertRaisesRegex(controller.LifecycleError, "invalid_lifecycle"):
            machine.transition(controller.LifecycleState.AGGREGATED)
        machine.transition(controller.LifecycleState.EXECUTION_TERMINAL)
        machine.transition(controller.LifecycleState.AGGREGATED)
        machine.transition(controller.LifecycleState.CLOSED)
        with self.assertRaises(controller.LifecycleError):
            machine.reseal("d" * 64)

    def test_09_exact_decimal_reservations_and_no_silent_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = controller.BudgetLedger(
                Path(temporary) / "ledger.jsonl", Decimal("0.3")
            )
            ledger.reserve("slot-a", Decimal("0.1"))
            ledger.reserve("slot-b", Decimal("0.2"))
            snapshot = ledger.load()
            self.assertEqual(snapshot.outstanding_total, Decimal("0.3"))
            with self.assertRaisesRegex(controller.LedgerError, "hard_cap"):
                ledger.reserve("slot-c", Decimal("0.0000000000000000001"))

            ledger.provider_start_intent("slot-a", Decimal("0.1"))
            ledger.settle("slot-a", Decimal("0.07"))
            ledger.release("slot-b")
            final = ledger.load()
            self.assertEqual(final.lifetime_total_spend, Decimal("0.07"))
            self.assertEqual(final.outstanding_total, Decimal("0"))
            self.assertEqual(len(final.entries), 5)
            with self.assertRaisesRegex(controller.LedgerError, "no_silent_rerun"):
                ledger.reserve("slot-a", Decimal("0.1"))

    def test_10_unresolved_started_cost_is_debited_at_full_sealed_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = controller.BudgetLedger(
                Path(temporary) / "ledger.jsonl", Decimal("5")
            )
            ledger.reserve("slot-a", Decimal("1.25"))
            ledger.provider_start_intent("slot-a", Decimal("1.25"))
            before = ledger.load()
            self.assertEqual(before.conservative_unresolved_debit, Decimal("1.25"))

            records = ledger.conservatively_settle_unresolved()
            after = ledger.load()
            self.assertEqual(len(records), 1)
            self.assertEqual(after.lifetime_total_spend, Decimal("1.25"))
            self.assertFalse(after.outstanding_reservations)
            self.assertFalse(after.started_unresolved)

    def test_11_provider_start_intent_is_durable_before_spawn_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = controller.BudgetLedger(
                Path(temporary) / "ledger.jsonl", Decimal("2")
            )
            ledger.reserve("slot-a", Decimal("1"))
            with mock.patch.object(
                controller,
                "spawn_sandboxed_provider",
                side_effect=controller.SandboxPolicyError("offline refusal"),
            ) as spawn:
                with self.assertRaises(controller.SandboxPolicyError):
                    controller.start_reserved_provider(
                        ledger,
                        "slot-a",
                        Decimal("1"),
                        ["provider-must-not-run"],
                        session_root=Path(temporary) / ("session-" + "a" * 32),
                    )
                spawn.assert_called_once()
            snapshot = ledger.load()
            self.assertEqual(snapshot.started_unresolved, {"slot-a": Decimal("1")})
            with self.assertRaisesRegex(controller.LedgerError, "no_silent_rerun"):
                ledger.reserve("slot-a", Decimal("1"))

    def test_11b_failed_start_intent_persistence_yields_zero_spawn_calls(self) -> None:
        """A start intent that cannot be durably recorded must spawn nothing."""

        with tempfile.TemporaryDirectory() as temporary:
            ledger = controller.BudgetLedger(
                Path(temporary) / "ledger.jsonl", Decimal("2")
            )
            # No reservation exists for "slot-a": provider_start_intent must
            # refuse before spawn_sandboxed_provider is ever reached.
            with mock.patch.object(controller, "spawn_sandboxed_provider") as spawn:
                with self.assertRaisesRegex(
                    controller.LedgerError,
                    "provider_start_requires_one_unstarted_reservation",
                ):
                    controller.start_reserved_provider(
                        ledger,
                        "slot-a",
                        Decimal("1"),
                        ["provider-must-not-run"],
                        session_root=Path(temporary) / ("session-" + "a" * 32),
                    )
                spawn.assert_not_called()
            snapshot = ledger.load()
            self.assertEqual(snapshot.entries, ())

    def test_12_ambiguous_and_half_written_ledgers_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger_path = root / "ledger.jsonl"
            ledger = controller.BudgetLedger(ledger_path, Decimal("10"))
            first = ledger.reserve("slot-a", Decimal("1"))

            unsigned = {
                "version": 1,
                "sequence": 2,
                "transition": "reservation",
                "slot_id": "slot-a",
                "amount": "1",
                "previous_hash": first["entry_hash"],
            }
            duplicate = dict(unsigned)
            duplicate["entry_hash"] = hashlib.sha256(
                json.dumps(
                    unsigned,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("ascii")
            ).hexdigest()
            with ledger_path.open("ab") as handle:
                handle.write(
                    json.dumps(
                        duplicate,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("ascii")
                    + b"\n"
                )
            with self.assertRaisesRegex(controller.LedgerError, "duplicate_reservation"):
                ledger.load()

            partial_path = root / "partial-ledger.jsonl"
            partial_path.write_bytes(b'{"version":1')
            partial_path.chmod(0o600)
            partial = controller.BudgetLedger(partial_path, Decimal("10"))
            with self.assertRaisesRegex(controller.LedgerError, "half_written_ledger"):
                partial.load()

    def test_25_ledger_replacement_and_size_change_refuse_stale_append(self) -> None:
        for mutation in ("replace_inode", "change_size"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / "ledger.jsonl"
                ledger = controller.BudgetLedger(path, Decimal("10"))
                ledger.reserve("slot-a", Decimal("1"))
                before = path.read_bytes()
                original_inode = path.stat().st_ino
                original_load = ledger.load

                def load_then_mutate() -> Any:
                    snapshot = original_load()
                    if mutation == "replace_inode":
                        replacement = root / "replacement.jsonl"
                        replacement.write_bytes(before)
                        replacement.chmod(0o600)
                        replacement.replace(path)
                    else:
                        with path.open("ab") as handle:
                            handle.write(b"\n")
                    return snapshot

                with mock.patch.object(ledger, "load", side_effect=load_then_mutate):
                    with self.assertRaisesRegex(controller.LedgerError, "ledger_changed_after_read"):
                        ledger.reserve("slot-b", Decimal("1"))
                if mutation == "replace_inode":
                    self.assertNotEqual(path.stat().st_ino, original_inode)
                    self.assertEqual(path.read_bytes(), before)
                else:
                    self.assertEqual(path.stat().st_ino, original_inode)
                    self.assertEqual(path.read_bytes(), before + b"\n")

    def test_26_ledger_created_after_absent_snapshot_is_not_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.jsonl"
            ledger = controller.BudgetLedger(path, Decimal("10"))
            original_load = ledger.load

            def load_then_create() -> Any:
                snapshot = original_load()
                path.touch(mode=0o600)
                return snapshot

            with mock.patch.object(ledger, "load", side_effect=load_then_create):
                with self.assertRaisesRegex(controller.LedgerError, "ledger_append_failed"):
                    ledger.reserve("slot-a", Decimal("1"))
            self.assertEqual(path.read_bytes(), b"")

    def test_27_ledger_deleted_after_snapshot_is_not_recreated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.jsonl"
            ledger = controller.BudgetLedger(path, Decimal("10"))
            ledger.reserve("slot-a", Decimal("1"))
            original_load = ledger.load

            def load_then_delete() -> Any:
                snapshot = original_load()
                path.unlink()
                return snapshot

            with mock.patch.object(ledger, "load", side_effect=load_then_delete):
                with self.assertRaisesRegex(controller.LedgerError, "ledger_append_failed"):
                    ledger.reserve("slot-b", Decimal("1"))
            self.assertFalse(path.exists())

    def test_33_new_ledger_writes_are_version_two_fixed_seven_usd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.jsonl"
            ledger = controller.BudgetLedger(path, Decimal("10"))
            ledger.reserve("slot-a", Decimal("2"))
            ledger.reserve("slot-b", Decimal("0"))
            ledger.reserve("slot-c", controller._usd.CANONICAL_USD_QUANTUM)

            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(
                [record["version"] for record in records],
                [controller.LEDGER_WRITE_VERSION] * 3,
            )
            self.assertEqual(
                [record["amount"] for record in records],
                ["2.0000000", "0.0000000", "0.0000001"],
            )
            # str(Decimal("0.0000001")) is "1E-7"; no persisted byte may be.
            self.assertNotIn("1E-7", path.read_text())
            self.assertEqual(str(Decimal("0.0000001")), "1E-7")
            snapshot = ledger.load()
            self.assertEqual(
                snapshot.outstanding_reservations,
                {
                    "slot-a": Decimal("2"),
                    "slot-b": Decimal("0"),
                    "slot-c": Decimal("0.0000001"),
                },
            )

    def test_34_sub_quantum_amounts_fail_closed_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.jsonl"
            ledger = controller.BudgetLedger(path, Decimal("10"))
            with self.assertRaisesRegex(
                controller.LedgerError, "amount_not_representable_in_canonical_usd"
            ):
                ledger.reserve("slot-a", Decimal("0.00000001"))
            self.assertFalse(path.exists())

            ledger.reserve("slot-a", Decimal("1"))
            before = path.read_bytes()
            with self.assertRaisesRegex(
                controller.LedgerError, "amount_not_representable_in_canonical_usd"
            ):
                ledger.reserve("slot-b", Decimal("0.123456789"))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(set(ledger.load().used_slots), {"slot-a"})

            # Cap and representability are separate gates and both fail
            # closed; an over-cap amount is refused for that stronger reason
            # first.
            tight = controller.BudgetLedger(Path(temporary) / "tight.jsonl", Decimal("0"))
            with self.assertRaisesRegex(controller.LedgerError, "hard_cap"):
                tight.reserve("slot-a", Decimal("0.00000001"))
            self.assertFalse((Path(temporary) / "tight.jsonl").exists())

    def test_35_historical_v1_entries_replay_and_are_never_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.jsonl"
            historical = write_v1_ledger(
                path,
                [("reservation", "slot-a", "1"), ("provider-start-intent", "slot-a", "1")],
            )
            ledger = controller.BudgetLedger(path, Decimal("10"))
            snapshot = ledger.load()

            self.assertEqual([entry["version"] for entry in snapshot.entries], [1, 1])
            # The stripped historical representation is preserved verbatim.
            self.assertEqual([entry["amount"] for entry in snapshot.entries], ["1", "1"])
            self.assertEqual(
                [entry["entry_hash"] for entry in snapshot.entries],
                [record["entry_hash"] for record in historical],
            )
            self.assertEqual(snapshot.started_unresolved, {"slot-a": Decimal("1")})

            before = path.read_bytes()
            ledger.settle("slot-a", Decimal("0.07"))
            after = path.read_bytes()
            self.assertTrue(after.startswith(before))
            self.assertEqual(after[: len(before)], before)
            self.assertEqual(ledger.load().lifetime_total_spend, Decimal("0.07"))

    def test_36_v1_to_v2_chain_preserves_previous_hash_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ledger.jsonl"
            historical = write_v1_ledger(
                path,
                [("reservation", "slot-a", "1"), ("reservation", "slot-b", "0.5")],
            )
            ledger = controller.BudgetLedger(path, Decimal("10"))
            ledger.provider_start_intent("slot-a", Decimal("1"))
            ledger.settle("slot-a", Decimal("0.25"))

            entries = ledger.load().entries
            self.assertEqual([entry["version"] for entry in entries], [1, 1, 2, 2])
            self.assertEqual(
                [entry["amount"] for entry in entries],
                ["1", "0.5", "1.0000000", "0.2500000"],
            )
            self.assertEqual(entries[0]["previous_hash"], "0" * 64)
            for previous, current in zip(entries, entries[1:]):
                self.assertEqual(current["previous_hash"], previous["entry_hash"])
            # The boundary entry chains onto the last historical hash exactly.
            self.assertEqual(
                entries[2]["previous_hash"], historical[-1]["entry_hash"]
            )
            snapshot = ledger.load()
            self.assertEqual(snapshot.lifetime_total_spend, Decimal("0.25"))
            self.assertEqual(snapshot.outstanding_reservations, {"slot-b": Decimal("0.5")})

            corrupt = json.loads(path.read_text().splitlines()[-1])
            corrupt["version"] = 3
            path.write_bytes(
                b"\n".join(
                    line.encode()
                    for line in path.read_text().splitlines()[:-1]
                )
                + b"\n"
                + json.dumps(corrupt, sort_keys=True, separators=(",", ":")).encode()
                + b"\n"
            )
            with self.assertRaisesRegex(controller.LedgerError, "line_4_version"):
                ledger.load()


class PairReservationTests(unittest.TestCase):
    """Card A's whole-pair admission primitive: reserve both arms, or neither."""

    def test_pair_reservation_admits_both_arms_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = controller.BudgetLedger(
                Path(temporary) / "ledger.jsonl", Decimal("10")
            )
            first, second = ledger.reserve_pair(
                ("P01-A01", "P01-B01"), Decimal("2"), Decimal("4")
            )
            self.assertEqual(first["slot_id"], "P01-A01")
            self.assertEqual(second["slot_id"], "P01-B01")
            snapshot = ledger.load()
            self.assertEqual(
                snapshot.outstanding_reservations,
                {"P01-A01": Decimal("2"), "P01-B01": Decimal("2")},
            )
            # Both arms are independently launch-eligible once the pair is
            # admitted: each may take its own start intent.
            ledger.provider_start_intent("P01-A01", Decimal("2"))
            ledger.provider_start_intent("P01-B01", Decimal("2"))

    def test_pair_reservation_would_strand_second_arm_admits_neither(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            # Only 3 available: the 4-total pair cannot fit, so neither arm
            # may become launch-eligible -- not even the arm that alone
            # would have fit under the cap.
            ledger = controller.BudgetLedger(
                Path(temporary) / "ledger.jsonl", Decimal("3")
            )
            with self.assertRaisesRegex(
                controller.LedgerError, "pair_reservation_would_strand_second_arm"
            ):
                ledger.reserve_pair(
                    ("P01-A01", "P01-B01"), Decimal("2"), Decimal("4")
                )
            snapshot = ledger.load()
            self.assertEqual(snapshot.entries, ())
            self.assertFalse(snapshot.outstanding_reservations)
            # Neither arm may take a start intent: nothing was reserved.
            with self.assertRaisesRegex(
                controller.LedgerError, "provider_start_requires_one_unstarted_reservation"
            ):
                ledger.provider_start_intent("P01-A01", Decimal("2"))

    def test_pair_reservation_rejects_non_distinct_or_inconsistent_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = controller.BudgetLedger(
                Path(temporary) / "ledger.jsonl", Decimal("10")
            )
            with self.assertRaisesRegex(
                controller.LedgerError, "pair_reservation_requires_two_distinct_slots"
            ):
                ledger.reserve_pair(("P01-A01", "P01-A01"), Decimal("2"), Decimal("4"))
            with self.assertRaisesRegex(
                controller.LedgerError,
                "pair_reservation_total_inconsistent_with_per_arm_cap",
            ):
                ledger.reserve_pair(("P01-A01", "P01-B01"), Decimal("2"), Decimal("5"))
            self.assertEqual(ledger.load().entries, ())

    def test_pair_reservation_second_write_failure_releases_the_first_arm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ledger = controller.BudgetLedger(
                Path(temporary) / "ledger.jsonl", Decimal("10")
            )
            # Pre-consume the second arm's slot id via a normal reservation
            # so the internal second append collides after the whole-pair
            # capacity pre-check already passed.
            ledger.reserve("P01-B01", Decimal("2"))
            ledger.release("P01-B01")
            with self.assertRaisesRegex(
                controller.LedgerError, "slot_already_consumed_no_silent_rerun"
            ):
                ledger.reserve_pair(
                    ("P01-A01", "P01-B01"), Decimal("2"), Decimal("4")
                )
            # The whole-pair pre-check itself catches the used slot before
            # any write for this pair occurs, so P01-A01 was never reserved.
            snapshot = ledger.load()
            self.assertNotIn("P01-A01", snapshot.used_slots)


class ManifestBudgetCarryForwardTests(unittest.TestCase):
    """Card A's lifetime-budget primitive: carry forward, never reset."""

    def test_ledger_from_manifest_carries_forward_historical_charge_not_nominal_cap(
        self,
    ) -> None:
        manifest, _ = build_valid_manifest(EPOCH_ID)
        self.assertEqual(
            manifest["budget"]["budget_authority_status"],
            "AUTHORIZED_BY_EXPLICIT_OWNER_ATTESTATION",
        )
        with tempfile.TemporaryDirectory() as temporary:
            ledger = controller.budget_ledger_from_manifest(
                Path(temporary) / "ledger.jsonl", manifest
            )
            # The effective cap is the manifest's carried-forward remaining
            # budget, not the full nominal epoch cap a brand-new ledger
            # would otherwise imply.
            self.assertEqual(
                ledger.lifetime_hard_cap,
                Decimal(manifest["budget"]["max_additional_spend_usd"]),
            )
            self.assertEqual(
                manifest["budget"]["max_additional_spend_usd"],
                manifest["budget"]["nominal_max_additional_usd"],
            )
            self.assertNotEqual(
                ledger.lifetime_hard_cap,
                Decimal(build_epoch_manifest.LIFETIME_HARD_CAP_USD),
            )
            # A brand-new, empty ledger can still only spend up to the
            # carried-forward remaining amount, never the nominal cap.
            over_remaining = ledger.lifetime_hard_cap + Decimal("0.0000001")
            with self.assertRaisesRegex(controller.LedgerError, "hard_cap"):
                ledger.reserve("slot-a", over_remaining)
            ledger.reserve("slot-a", ledger.lifetime_hard_cap)

    def test_ledger_from_manifest_fails_closed_when_historical_cost_unresolved(
        self,
    ) -> None:
        manifest, _ = build_valid_manifest(EPOCH_ID, attestation=None)
        self.assertEqual(
            manifest["budget"]["budget_authority_status"],
            "BLOCKED_UNKNOWN_HISTORICAL_COST",
        )
        self.assertIsNone(manifest["budget"]["max_additional_spend_usd"])
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                controller.LedgerError, "budget_authority_unresolved"
            ):
                controller.budget_ledger_from_manifest(
                    Path(temporary) / "ledger.jsonl", manifest
                )
            # No ledger file, and therefore zero additional spend authority,
            # is ever created while the historical cost stays unresolved.
            self.assertFalse((Path(temporary) / "ledger.jsonl").exists())


class RecoveryGateTests(unittest.TestCase):
    def test_13_pid_reuse_is_discriminated_by_start_and_executable_identity(self) -> None:
        recorded = process_identity(123, start="old", executable_inode=10)
        reused = process_identity(123, start="new", executable_inode=11)
        differences = controller.process_identity_differences(recorded, reused)
        self.assertIn("start_identity", differences)
        self.assertIn("executable", differences)
        self.assertFalse(controller.same_process_identity(recorded, reused))

        result = reconcile(
            writer_lease=controller.WriterLease(recorded),
            live_processes=(controller.ProcessRecord(reused, ppid=1),),
        )
        self.assertFalse(result.passed)
        self.assertTrue(
            any(reason.startswith("stale_pid_reuse:") for reason in result.reasons)
        )

    def test_14_surviving_provider_child_fails_closed(self) -> None:
        writer = process_identity(200, pgid=200, sid=200)
        child = process_identity(201, pgid=201, sid=201)
        result = reconcile(
            writer_lease=controller.WriterLease(writer, (child,)),
            live_processes=(
                controller.ProcessRecord(writer, ppid=1),
                controller.ProcessRecord(child, ppid=200),
            ),
        )
        self.assertFalse(result.passed)
        self.assertIn("surviving_provider_descendant:201", result.reasons)

    def test_15_foreign_open_handle_and_cwd_fail_closed_without_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "epoch"
            session = root / ("session-" + "1" * 32)
            session.mkdir(parents=True)
            foreign = process_identity(300)
            handle_result = reconcile(
                live_processes=(
                    controller.ProcessRecord(
                        foreign,
                        ppid=1,
                        cwd="/outside",
                        open_paths=(str(session / "evidence.json"),),
                    ),
                ),
                roots=(root,),
            )
            cwd_result = reconcile(
                live_processes=(
                    controller.ProcessRecord(
                        foreign,
                        ppid=1,
                        cwd=str(session),
                    ),
                ),
                roots=(root,),
            )
            self.assertIn("foreign_open_handle:300", handle_result.reasons)
            self.assertIn("foreign_cwd_under_epoch_root:300", cwd_result.reasons)
            self.assertTrue(session.exists())

    def test_17_half_written_run_missing_evidence_and_start_intent_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_path = root / "run.json"
            run_path.write_bytes(b'{"slot_id":"slot-a"')
            run_state = controller.read_run_evidence(run_path, "slot-a")
            snapshot = controller.LedgerSnapshot(
                entries=({},),
                outstanding_reservations={"slot-a": Decimal("1")},
                started_unresolved={"slot-a": Decimal("1")},
                used_slots=frozenset({"slot-a", "slot-missing"}),
            )
            result = reconcile(
                roots=(root,),
                ledger_snapshot=snapshot,
                run_evidence=(run_state,),
            )
            self.assertFalse(result.passed)
            self.assertIn("half_written_run:slot-a", result.reasons)
            self.assertIn("unresolved_provider_start_intent:slot-a", result.reasons)
            self.assertIn("missing_evidence:run:slot-missing", result.reasons)

    def test_18_missing_host_boot_and_ambiguous_ledger_fail_closed(self) -> None:
        result = controller.reconcile_fresh_start(
            sealed_host=None,
            sealed_boot_id=None,
            current_host="host-1",
            current_boot_id="boot-1",
            writer_lease=None,
            live_processes=(),
            managed_roots=(),
            ledger_snapshot=None,
            run_evidence=(),
            ledger_error="hash_chain_mismatch",
        )
        self.assertFalse(result.passed)
        self.assertIn("missing_evidence:host", result.reasons)
        self.assertIn("missing_evidence:boot", result.reasons)
        self.assertIn("missing_evidence:ledger", result.reasons)
        self.assertIn("ambiguous_ledger:hash_chain_mismatch", result.reasons)
        with self.assertRaises(controller.RecoveryError):
            result.require_pass()

    def test_19_live_process_identity_includes_more_than_pid(self) -> None:
        identity = controller.capture_process_identity(os.getpid())
        self.assertEqual(identity.pid, os.getpid())
        self.assertTrue(identity.host)
        self.assertRegex(identity.boot_id, r"[0-9a-f]{64}")
        self.assertTrue(identity.start_identity)
        self.assertTrue(identity.executable.path)
        self.assertEqual(identity.pgid, os.getpgid(os.getpid()))
        self.assertEqual(identity.sid, os.getsid(os.getpid()))

    def test_32_unresolvable_handle_path_cannot_pass_the_foreign_handle_gate(self) -> None:
        record = controller.ProcessRecord(
            process_identity(300),
            ppid=1,
            open_paths=("/offline/epoch/held-open.txt",),
        )
        with mock.patch.object(Path, "resolve", side_effect=PermissionError("offline denial")):
            with self.assertRaisesRegex(controller.RecoveryError, "path_resolution_failed"):
                reconcile(live_processes=(record,), roots=(Path("/offline/epoch"),))


class ManifestAuthorityTests(unittest.TestCase):
    def test_load_manifest_after_lock_returns_the_canonically_validated_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, data = build_valid_manifest(EPOCH_ID)
            path = Path(temporary) / "manifest.json"
            path.write_bytes(data)
            loaded = controller.load_manifest_after_lock(path)
            self.assertEqual(loaded, manifest)
            self.assertEqual(loaded["schema"], build_epoch_manifest.MANIFEST_SCHEMA)

    def test_29_manifest_cli_verifies_exact_hash_and_a_valid_v3_manifest_succeeds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            _, raw = build_valid_manifest(EPOCH_ID)
            manifest.write_bytes(raw)
            valid = run_controller(
                root / "epoch.lock",
                "--manifest", str(manifest),
                "--expected-manifest-sha256", hashlib.sha256(raw).hexdigest(),
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            # main() also prints a leading FABLE_EPOCH_LOCK_ACQUIRED line;
            # the structural JSON result is the final line of stdout.
            payload = json.loads(valid.stdout.splitlines()[-1])
            self.assertIs(payload["authority_verified"], True)
            self.assertIn("schema", payload["manifest_keys"])
            self.assertIn("budget", payload["manifest_keys"])

            mismatch = run_controller(
                root / "epoch.lock",
                "--manifest", str(manifest),
                "--expected-manifest-sha256", "0" * 64,
            )
            self.assertEqual(mismatch.returncode, controller.EXIT_CONTRACT_FAILURE)
            self.assertIn("authority_digest_mismatch", mismatch.stderr)

            for invalid in (b"{", b"\xff", b"[]"):
                with self.subTest(invalid=invalid):
                    manifest.write_bytes(invalid)
                    result = run_controller(
                        root / "epoch.lock",
                        "--manifest", str(manifest),
                        "--expected-manifest-sha256", hashlib.sha256(invalid).hexdigest(),
                    )
                    self.assertEqual(result.returncode, controller.EXIT_CONTRACT_FAILURE)
                    self.assertNotIn('"authority_verified":true', result.stdout)

    def test_manifest_failing_canonical_schema_validation_is_rejected(self) -> None:
        """A structurally-tampered manifest is rejected by the canonical
        compiler even though it is well-formed JSON with a matching hash --
        proving Card A validates schema/signoff authority, not just bytes.
        """

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, _ = build_valid_manifest(EPOCH_ID)
            tampered = dict(manifest)
            tampered["schema"] = "SOME_OTHER_SCHEMA/v1"
            tampered_bytes = (
                json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_bytes(tampered_bytes)
            result = run_controller(
                root / "epoch.lock",
                "--manifest", str(manifest_path),
                "--expected-manifest-sha256", hashlib.sha256(tampered_bytes).hexdigest(),
            )
            self.assertEqual(result.returncode, controller.EXIT_CONTRACT_FAILURE)
            self.assertIn("manifest_authority_rejected", result.stderr)
            self.assertNotIn('"authority_verified":true', result.stdout)

            with self.assertRaises(controller.ManifestAuthorityError):
                controller.load_manifest_after_lock(manifest_path)

    def test_30_manifest_symlink_fifo_and_directory_are_refused_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "manifest.json"
            _, raw = build_valid_manifest(EPOCH_ID)
            target.write_bytes(raw)
            symlink = root / "symlink.json"
            symlink.symlink_to(target)
            fifo = root / "manifest.fifo"
            os.mkfifo(fifo, 0o600)
            directory = root / "manifest-directory"
            directory.mkdir()
            for candidate in (symlink, fifo, directory):
                with self.subTest(candidate=candidate.name):
                    result = run_controller(
                        root / "epoch.lock", "--manifest", str(candidate)
                    )
                    self.assertEqual(result.returncode, controller.EXIT_CONTRACT_FAILURE)
                    self.assertNotIn('"authority_verified":true', result.stdout)
            self.assertEqual(target.read_bytes(), raw)

    def test_31_manifest_modified_during_descriptor_read_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            _, raw = build_valid_manifest(EPOCH_ID)
            path.write_bytes(raw)
            replacement = b'{"changed":true,"longer":true}\n'
            native_read = controller.os.read
            descriptors: list[int] = []

            def read_then_modify(descriptor: int, size: int) -> bytes:
                block = native_read(descriptor, size)
                if not descriptors:
                    descriptors.append(descriptor)
                    path.write_bytes(replacement)
                return block

            with (
                mock.patch.object(controller.os, "read", side_effect=read_then_modify),
                mock.patch.object(controller.hashlib, "sha256") as digest,
            ):
                with self.assertRaisesRegex(
                    controller.EpochControllerError, "manifest_changed_during_read"
                ):
                    controller.load_manifest_after_lock(path)
                digest.assert_not_called()
            self.assertEqual(path.read_bytes(), replacement)
            self.assertEqual(len(descriptors), 1)
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])


class WorkspaceAndSandboxTests(unittest.TestCase):
    def sandbox_run(
        self, session_root: Path, provider_argv: list[str]
    ) -> subprocess.CompletedProcess[str]:
        command = controller.build_sandboxed_command(
            provider_argv,
            session_root=session_root,
            profile_path=PROFILE_PATH,
        )
        return subprocess.run(
            command,
            cwd=session_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
            close_fds=True,
        )

    def test_20_opaque_session_name_is_condition_free_and_create_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            token = "0123456789abcdef0123456789abcdef"
            session = controller.create_opaque_session_root(parent, lambda: token)
            self.assertEqual(session.name, f"session-{token}")
            for forbidden in (
                "ON",
                "OFF",
                "condition",
                "task-id",
                "pair-id",
                "order",
                "formal-run-id",
            ):
                self.assertNotIn(forbidden, session.name)
            with self.assertRaisesRegex(
                controller.OpaqueWorkspaceError, "collision"
            ):
                controller.create_opaque_session_root(parent, lambda: token)

    def test_21_sandbox_allows_only_current_session_devnull_and_pipes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            session = controller.create_opaque_session_root(
                parent, lambda: "1" * 32
            )
            sibling = controller.create_opaque_session_root(
                parent, lambda: "2" * 32
            )
            historical = parent / "historical-r1"
            historical.mkdir()
            generic_temp = parent / "generic-temp-write"

            allowed = self.sandbox_run(
                session, ["/usr/bin/touch", str(session / "allowed")]
            )
            pipe_and_devnull = self.sandbox_run(
                session,
                ["/bin/sh", "-c", "printf ignored >/dev/null; printf pipe-ok"],
            )
            generic_denied = self.sandbox_run(
                session, ["/usr/bin/touch", str(generic_temp)]
            )
            historical_denied = self.sandbox_run(
                session, ["/usr/bin/touch", str(historical / "denied")]
            )
            sibling_denied = self.sandbox_run(
                session, ["/usr/bin/touch", str(sibling / "denied")]
            )

            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertTrue((session / "allowed").is_file())
            self.assertEqual(pipe_and_devnull.returncode, 0, pipe_and_devnull.stderr)
            self.assertEqual(pipe_and_devnull.stdout, "pipe-ok")
            self.assertNotEqual(generic_denied.returncode, 0)
            self.assertFalse(generic_temp.exists())
            self.assertNotEqual(historical_denied.returncode, 0)
            self.assertFalse((historical / "denied").exists())
            self.assertNotEqual(sibling_denied.returncode, 0)
            self.assertFalse((sibling / "denied").exists())

    def test_22_missing_sandbox_has_no_unsandboxed_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = controller.create_opaque_session_root(
                temporary, lambda: "3" * 32
            )
            with mock.patch.object(controller.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(
                    controller.SandboxPolicyError, "no_unsandboxed_fallback"
                ):
                    controller.spawn_sandboxed_provider(
                        ["provider-must-not-run"],
                        session_root=session,
                        sandbox_exec_path=Path(temporary) / "missing-sandbox-exec",
                    )
                popen.assert_not_called()

    def test_23_profile_and_launcher_encode_fail_closed_entry_path(self) -> None:
        profile = PROFILE_PATH.read_text(encoding="utf-8")
        launcher = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("(deny file-write*)", profile)
        self.assertIn('(param "SESSION_ROOT")', profile)
        self.assertNotIn("unsandboxed", launcher.casefold())
        self.assertIn("exec /usr/bin/env python3", launcher)
        self.assertNotIn("trap", launcher)
        self.assertNotIn("unlink", launcher)

    def write_probe(self, parent: Path, name: str, body: str) -> Path:
        probe = parent / name
        probe.write_text(f"#!/usr/bin/env python3\n{body}", encoding="utf-8")
        probe.chmod(0o700)
        return probe

    def sandboxed_output(self, session: Path, probe: Path) -> tuple[int, bytes, bytes, int]:
        process = controller.spawn_sandboxed_provider(
            [str(probe)],
            session_root=session,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            profile_path=PROFILE_PATH,
        )
        stdout, stderr = process.communicate(timeout=30)
        return process.returncode, stdout, stderr, process.pid

    def test_37_fd9_stays_closed_inside_the_real_sandboxed_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            session = controller.create_opaque_session_root(parent, lambda: "5" * 32)
            probe = self.write_probe(
                parent,
                "fd9-probe",
                "import os, sys\n"
                "try:\n"
                "    os.fstat(9)\n"
                "except OSError:\n"
                "    sys.stdout.write('FD9_CLOSED')\n"
                "else:\n"
                "    sys.stdout.write('FD9_OPEN')\n",
            )
            marker = parent / "fd9-marker"
            marker.write_bytes(b"lock-stand-in")

            # Fail loudly rather than clobber a descriptor this process needs.
            with self.assertRaises(OSError):
                os.fstat(controller.LOCK_FD)

            holder = os.open(marker, os.O_RDONLY)
            installed = False
            try:
                if holder != controller.LOCK_FD:
                    os.dup2(holder, controller.LOCK_FD, inheritable=True)
                installed = True
                os.fstat(controller.LOCK_FD)
                returncode, stdout, stderr, _ = self.sandboxed_output(session, probe)
            finally:
                if installed:
                    try:
                        os.close(controller.LOCK_FD)
                    except OSError:
                        pass
                if holder != controller.LOCK_FD:
                    try:
                        os.close(holder)
                    except OSError:
                        pass

            self.assertEqual(returncode, 0, stderr)
            self.assertEqual(stdout, b"FD9_CLOSED")

    def test_38_pid_pgid_and_new_session_hold_through_sandbox_exec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            session = controller.create_opaque_session_root(parent, lambda: "6" * 32)
            probe = self.write_probe(
                parent,
                "identity-probe",
                "import os, sys\n"
                "sys.stdout.write(f'{os.getpid()} {os.getpgid(0)} {os.getsid(0)}')\n",
            )
            returncode, stdout, stderr, spawned_pid = self.sandboxed_output(session, probe)

            self.assertEqual(returncode, 0, stderr)
            pid, pgid, sid = (int(value) for value in stdout.split())
            # sandbox-exec execs the provider in place, so the PID the
            # harness holds really is the provider's; the group-signal
            # termination path depends on exactly this.
            self.assertEqual(pid, spawned_pid)
            self.assertEqual(pgid, spawned_pid)
            self.assertEqual(sid, spawned_pid)
            self.assertNotEqual(sid, os.getsid(0))
            self.assertNotEqual(pgid, os.getpgid(0))

    def test_39_sandbox_identity_is_observed_not_asserted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            session = controller.create_opaque_session_root(parent, lambda: "7" * 32)
            provider_argv = ["/usr/bin/true"]
            identity = controller.observe_sandboxed_command(
                provider_argv, session_root=session, profile_path=PROFILE_PATH
            )

            self.assertEqual(
                identity.profile_sha256,
                hashlib.sha256(PROFILE_PATH.read_bytes()).hexdigest(),
            )
            self.assertEqual(identity.profile_realpath, str(PROFILE_PATH.resolve()))
            self.assertEqual(
                identity.sandbox_exec_sha256,
                controller.sha256_file(controller.SANDBOX_EXEC_PATH),
            )
            self.assertEqual(
                identity.physical_argv,
                controller.build_sandboxed_command(
                    provider_argv, session_root=session, profile_path=PROFILE_PATH
                ),
            )
            self.assertEqual(identity.session_root, str(session.resolve()))
            record = identity.as_record()
            self.assertEqual(record["identity_source"], "OBSERVED_FILESYSTEM_BYTES")
            self.assertEqual(
                record["physical_argv_authority"],
                "epoch_controller.build_sandboxed_command",
            )

            # A different profile observes a different hash: the binding
            # tracks bytes, so no caller-supplied label can stand in for
            # them.
            drifted = parent / "drifted-runtime.sb"
            drifted.write_bytes(PROFILE_PATH.read_bytes() + b"\n;; drifted\n")
            other = controller.observe_sandboxed_command(
                provider_argv, session_root=session, profile_path=drifted
            )
            self.assertNotEqual(other.profile_sha256, identity.profile_sha256)

            with self.assertRaisesRegex(
                controller.SandboxPolicyError, "no_unsandboxed_fallback"
            ):
                controller.observe_sandboxed_command(
                    provider_argv,
                    session_root=session,
                    profile_path=PROFILE_PATH,
                    sandbox_exec_path=parent / "missing-sandbox-exec",
                )

    def test_40_session_token_width_is_exactly_thirty_two_lowercase_hex(self) -> None:
        self.assertEqual(controller.OPAQUE_SESSION_TOKEN_HEX_WIDTH, 32)
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for token in ("a" * 31, "a" * 33, "a" * 64, "A" * 32, "g" * 32, ""):
                with self.subTest(token=token):
                    with self.assertRaisesRegex(
                        controller.OpaqueWorkspaceError, "32_lowercase_hex"
                    ):
                        controller.create_opaque_session_root(parent, lambda: token)
            self.assertEqual(sorted(item.name for item in parent.iterdir()), [])

            accepted = controller.create_opaque_session_root(parent, lambda: "8" * 32)
            self.assertEqual(
                controller.validate_opaque_session_root(accepted), accepted.resolve()
            )
            wide = parent / ("session-" + "a" * 64)
            wide.mkdir(mode=0o700)
            with self.assertRaisesRegex(
                controller.SandboxPolicyError, "SESSION_ROOT_must_be_absolute_opaque_session"
            ):
                controller.validate_opaque_session_root(wide)
            with self.assertRaises(controller.SandboxPolicyError):
                controller.build_sandboxed_command(
                    ["/usr/bin/true"], session_root=wide, profile_path=PROFILE_PATH
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
