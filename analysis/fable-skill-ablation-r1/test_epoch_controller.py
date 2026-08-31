#!/usr/bin/env python3
"""Offline acceptance tests for the locked Fable epoch controller."""

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


# Importing the sibling module must not leave __pycache__ in the repository.
sys.dont_write_bytecode = True
CONTROLLER_PATH = Path(__file__).with_name("epoch_controller.py")
PROFILE_PATH = Path(__file__).with_name("claude-runtime.sb")
SCRIPT_PATH = Path(__file__).with_name("run_epoch_locked.sh")
SPEC = importlib.util.spec_from_file_location(
    "fable_ablation_epoch_controller", CONTROLLER_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {CONTROLLER_PATH}")
controller = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controller
SPEC.loader.exec_module(controller)


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

    def test_24_repeated_acquire_preserves_the_process_owned_lock(self) -> None:
        script = """
import os
import subprocess
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import epoch_controller as c

lock_path, other_path = sys.argv[2:]
held = c.EpochLock(lock_path)
held.acquire()
before = os.fstat(c.LOCK_FD)
for attempt in (held, c.EpochLock(lock_path), c.EpochLock(other_path)):
    try:
        attempt.acquire()
    except c.EpochLockFailure as exc:
        print(str(exc))
    else:
        raise AssertionError("repeated acquire succeeded")
    after = os.fstat(c.LOCK_FD)
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
assert not os.path.exists(other_path)
contender = subprocess.run(
    [sys.executable, c.__file__, "--lock-path", lock_path, "--probe-lock-only"],
    capture_output=True, text=True, timeout=10, close_fds=True,
)
assert contender.returncode == c.EXIT_OWNER_BUSY, contender.stderr
assert contender.stderr.strip() == c.OWNER_BUSY
print("REPEATED_ACQUIRE_LOCK_PRESERVED")
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / "epoch.lock"
            other_path = root / "other.lock"
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(CONTROLLER_PATH.parent),
                    str(lock_path),
                    str(other_path),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                close_fds=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("lock_already_acquired", result.stdout)
            self.assertEqual(result.stdout.count("process_lock_already_acquired"), 2)
            self.assertIn("REPEATED_ACQUIRE_LOCK_PRESERVED", result.stdout)
            self.assertFalse(other_path.exists())
            inode = lock_path.stat().st_ino
            successor = run_controller(lock_path, "--probe-lock-only")
            self.assertEqual(successor.returncode, 0, successor.stderr)
            self.assertEqual(lock_path.stat().st_ino, inode)


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
            ):
                with self.assertRaises(controller.SandboxPolicyError):
                    controller.start_reserved_provider(
                        ledger,
                        "slot-a",
                        Decimal("1"),
                        ["provider-must-not-run"],
                        session_root=Path(temporary) / ("session-" + "a" * 32),
                    )
            snapshot = ledger.load()
            self.assertEqual(snapshot.started_unresolved, {"slot-a": Decimal("1")})
            with self.assertRaisesRegex(controller.LedgerError, "no_silent_rerun"):
                ledger.reserve("slot-a", Decimal("1"))

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

    def test_16_live_lsof_scan_finds_test_owned_child_cwd_and_handle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "epoch"
            root.mkdir()
            session = controller.create_opaque_session_root(
                root, lambda: "4" * 32
            )
            held_file = session / "held-open.txt"
            held_file.write_text("offline\n", encoding="utf-8")
            with held_file.open("rb") as child_input:
                child = subprocess.Popen(
                    ["/bin/sleep", "30"],
                    cwd=session,
                    stdin=child_input,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
            try:
                canonical_root = root.resolve()
                # macOS tempfile paths commonly use /var while lsof reports
                # /private/var.  Exercise both spellings through the real scan.
                alias_root = Path("/var") / canonical_root.relative_to("/private/var")
                self.assertEqual(alias_root.resolve(), canonical_root)
                for scan_root in (alias_root, canonical_root):
                    with self.subTest(scan_root=str(scan_root)):
                        records = controller.scan_managed_root_processes((scan_root,))
                        child_record = next(
                            record for record in records if record.identity.pid == child.pid
                        )
                        self.assertEqual(
                            Path(child_record.cwd or "").resolve(), session.resolve()
                        )
                        self.assertIn(
                            held_file.resolve(),
                            {Path(path).resolve() for path in child_record.open_paths},
                        )
                        for gate_root in (alias_root, canonical_root):
                            result = reconcile(live_processes=records, roots=(gate_root,))
                            self.assertIn(
                                f"foreign_cwd_under_epoch_root:{child.pid}", result.reasons
                            )
                            self.assertIn(f"foreign_open_handle:{child.pid}", result.reasons)
                sibling = root.with_name(root.name + "-sibling")
                sibling.mkdir()
                outside = controller.ProcessRecord(
                    child_record.identity,
                    ppid=child_record.ppid,
                    cwd=str(sibling),
                    open_paths=(str(sibling / "held-open.txt"),),
                )
                self.assertTrue(reconcile(live_processes=(outside,), roots=(root,)).passed)
            finally:
                child.terminate()
                child.wait(timeout=5)

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
    def test_28_manifest_hash_and_parse_use_one_opened_inode_after_lock(self) -> None:
        script = """
import os
import sys
from pathlib import Path
from unittest import mock
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import epoch_controller as c

lock_path, manifest_path, replacement_path, expected = sys.argv[2:]
original = Path(manifest_path).read_bytes()
original_stat = Path(manifest_path).stat()
opened = []
read_identities = []
hash_inputs = []
parse_inputs = []
native_open = c.os.open
native_read = c.os.read
native_hash = c.hashlib.sha256
native_loads = c.json.loads

def observed_open(path, flags, *args, **kwargs):
    if os.fspath(path) == manifest_path:
        assert c._PROCESS_LOCK_ACQUIRED
        os.fstat(c.LOCK_FD)
        assert flags & os.O_NOFOLLOW
    descriptor = native_open(path, flags, *args, **kwargs)
    if os.fspath(path) == manifest_path:
        metadata = os.fstat(descriptor)
        opened.append((descriptor, metadata.st_dev, metadata.st_ino))
    return descriptor

def observed_read(descriptor, size):
    if opened and descriptor == opened[0][0]:
        metadata = os.fstat(descriptor)
        read_identities.append((metadata.st_dev, metadata.st_ino))
    return native_read(descriptor, size)

def replace_path_during_hash(raw):
    hash_inputs.append(raw)
    Path(replacement_path).replace(manifest_path)
    return native_hash(raw)

def observed_parse(raw, *args, **kwargs):
    parse_inputs.append(raw)
    return native_loads(raw, *args, **kwargs)

with (
    mock.patch.object(c.os, "open", side_effect=observed_open),
    mock.patch.object(c.os, "read", side_effect=observed_read),
    mock.patch.object(c.hashlib, "sha256", side_effect=replace_path_during_hash),
    mock.patch.object(c.json, "loads", side_effect=observed_parse),
    mock.patch.object(c, "sha256_file", side_effect=AssertionError("path reread")),
):
    status = c.main([
        "--lock-path", lock_path,
        "--manifest", manifest_path,
        "--expected-manifest-sha256", expected,
    ])
assert status == 0, status
assert len(opened) == 1, opened
assert opened[0][1:] == (original_stat.st_dev, original_stat.st_ino)
assert read_identities and all(item == opened[0][1:] for item in read_identities)
assert hash_inputs == [original], hash_inputs
assert parse_inputs == [original.decode("utf-8")], parse_inputs
print("SINGLE_INODE_MANIFEST_VERIFIED")
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            replacement_path = root / "replacement.json"
            original = b'{"safe":true}\n'
            replacement = b'{"unexpected":true}\n'
            manifest_path.write_bytes(original)
            replacement_path.write_bytes(replacement)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(CONTROLLER_PATH.parent),
                    str(root / "epoch.lock"),
                    str(manifest_path),
                    str(replacement_path),
                    hashlib.sha256(original).hexdigest(),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                close_fds=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"manifest_keys":["safe"]', result.stdout)
            self.assertIn("SINGLE_INODE_MANIFEST_VERIFIED", result.stdout)
            self.assertEqual(manifest_path.read_bytes(), replacement)

    def test_29_manifest_cli_verifies_exact_hash_and_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            raw = b'{ "epoch": "offline", "value": 1 }\n'
            manifest.write_bytes(raw)
            valid = run_controller(
                root / "epoch.lock",
                "--manifest", str(manifest),
                "--expected-manifest-sha256", hashlib.sha256(raw).hexdigest(),
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn('"authority_verified":true', valid.stdout)
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

    def test_30_manifest_symlink_fifo_and_directory_are_refused_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "manifest.json"
            raw = b'{"safe":true}\n'
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
            path.write_bytes(b'{"safe":true}\n')
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
