#!/usr/bin/env python3
"""Fail-closed single-writer primitives for a Fable ablation epoch.

This module deliberately separates orchestration authority from provider
execution.  The production entry point acquires the persistent external lock
before it reads a manifest.  Provider processes can only be launched through
the sandboxed helper, after a durable provider-start intent, and never inherit
the controller's lock descriptor.

The module is also intentionally useful without a provider: all lock,
lifecycle, ledger, recovery, workspace, and sandbox policy checks can be
exercised by deterministic offline tests.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


def _load_usd_authority() -> Any:
    """Resolve the one canonical persisted USD representation authority.

    The sibling compiler owns that representation.  Resolving it by module
    object -- rather than re-deriving a formatter here -- keeps exactly one
    implementation of persisted money across the runtime.
    """

    for name in ("fable_epoch_manifest", "build_epoch_manifest"):
        module = sys.modules.get(name)
        if module is not None:
            return module
    path = Path(__file__).with_name("build_epoch_manifest.py")
    spec = importlib.util.spec_from_file_location("fable_epoch_manifest", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"canonical USD representation authority missing: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["fable_epoch_manifest"] = module
    spec.loader.exec_module(module)
    return module


_usd = _load_usd_authority()

PRODUCTION_LOCK_PATH = Path("/Users/kelvin/.fable-ablation.lock")
LOCK_FD = 9
LOCKF_PATH = Path("/usr/bin/lockf")
SANDBOX_EXEC_PATH = Path("/usr/bin/sandbox-exec")
SANDBOX_PROFILE_PATH = Path(__file__).with_name("claude-runtime.sb")
LSOF_PATH = Path("/usr/sbin/lsof")

EXIT_OWNER_BUSY = 75
EXIT_LOCK_FAILURE = 70
EXIT_CONTRACT_FAILURE = 65

OWNER_BUSY = "FABLE_EPOCH_OWNER_BUSY"
LOCK_FAILURE = "FABLE_EPOCH_LOCK_FAILURE"
RECOVERY_BLOCKED = "FABLE_EPOCH_RECOVERY_BLOCKED"
AMBIGUOUS_LEDGER = "FABLE_EPOCH_AMBIGUOUS_LEDGER"

OPAQUE_SESSION_TOKEN_HEX_WIDTH = 32
_OPAQUE_SESSION_TOKEN_PATTERN = r"[0-9a-f]{%d}" % OPAQUE_SESSION_TOKEN_HEX_WIDTH
_OPAQUE_SESSION_TOKEN_RE = re.compile(_OPAQUE_SESSION_TOKEN_PATTERN + r"\Z")
_OPAQUE_SESSION_RE = re.compile(r"session-" + _OPAQUE_SESSION_TOKEN_PATTERN + r"\Z")
_SLOT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_LEDGER_TRANSITIONS = frozenset(
    {"reservation", "provider-start-intent", "settlement", "release"}
)

# Ledger version 1 persisted stripped-decimal amounts.  Its serialized bytes
# and entry hashes stay valid forever and are never rewritten; only NEW writes
# use version 2 with the canonical fixed-seven persisted USD representation.
HISTORICAL_LEDGER_VERSION = 1
LEDGER_WRITE_VERSION = 2
SUPPORTED_LEDGER_VERSIONS = frozenset({HISTORICAL_LEDGER_VERSION, LEDGER_WRITE_VERSION})

_PROCESS_LOCK_ACQUIRED = False


class EpochControllerError(RuntimeError):
    """Base class for fail-closed controller errors."""


class EpochOwnerBusy(EpochControllerError):
    """Another controller owns the external lock."""


class EpochLockFailure(EpochControllerError):
    """The lock could not be opened, acquired, or authenticated."""


class LifecycleError(EpochControllerError):
    """A lifecycle or sealed-authority invariant failed."""


class LedgerError(EpochControllerError):
    """The append-only budget ledger is invalid or cannot be extended."""


class RecoveryError(EpochControllerError):
    """Fresh-start reconciliation found unresolved execution state."""


class SandboxPolicyError(EpochControllerError):
    """The required runtime sandbox cannot be enforced."""


class OpaqueWorkspaceError(EpochControllerError):
    """An opaque session root could not be created safely."""


def _error(marker: str, detail: str) -> str:
    return f"{marker}: {detail}"


def validate_lock_metadata(
    descriptor_stat: os.stat_result,
    path_stat: os.stat_result,
    *,
    expected_uid: int | None = None,
) -> None:
    """Authenticate both names for the already-acquired persistent lock."""

    uid = os.getuid() if expected_uid is None else expected_uid
    problems: list[str] = []
    if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
        path_stat.st_dev,
        path_stat.st_ino,
    ):
        problems.append("fd_path_inode_mismatch")
    if not stat.S_ISREG(descriptor_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        problems.append("not_regular_file")
    if descriptor_stat.st_uid != uid or path_stat.st_uid != uid:
        problems.append("wrong_owner")
    if stat.S_IMODE(descriptor_stat.st_mode) != 0o600 or stat.S_IMODE(path_stat.st_mode) != 0o600:
        problems.append("wrong_mode")
    if descriptor_stat.st_nlink != 1 or path_stat.st_nlink != 1:
        problems.append("wrong_link_count")
    if problems:
        raise EpochLockFailure(_error(LOCK_FAILURE, ",".join(problems)))


class EpochLock:
    """Acquire the persistent epoch lock on FD 9 without replacing its path.

    There is intentionally no release method or context-manager protocol.
    After successful acquisition the descriptor remains owned for the life of
    the controller process and is released by the kernel at process exit.
    """

    def __init__(self, path: str | os.PathLike[str] = PRODUCTION_LOCK_PATH) -> None:
        self.path = Path(path)
        self.acquired = False

    def acquire(self) -> None:
        global _PROCESS_LOCK_ACQUIRED

        if self.acquired:
            raise EpochLockFailure(_error(LOCK_FAILURE, "lock_already_acquired"))
        if _PROCESS_LOCK_ACQUIRED:
            raise EpochLockFailure(_error(LOCK_FAILURE, "process_lock_already_acquired"))
        try:
            os.fstat(LOCK_FD)
        except OSError:
            pass
        else:
            raise EpochLockFailure(_error(LOCK_FAILURE, "fd9_already_open"))
        if not hasattr(os, "O_NOFOLLOW"):
            raise EpochLockFailure(_error(LOCK_FAILURE, "O_NOFOLLOW_unavailable"))
        if not LOCKF_PATH.is_file() or not os.access(LOCKF_PATH, os.X_OK):
            raise EpochLockFailure(_error(LOCK_FAILURE, "absolute_lockf_unavailable"))

        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        try:
            opened_fd = os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise EpochLockFailure(
                _error(LOCK_FAILURE, f"secure_open_failed:{exc.errno}:{exc.strerror}")
            ) from exc

        try:
            if opened_fd != LOCK_FD:
                os.dup2(opened_fd, LOCK_FD, inheritable=True)
                os.close(opened_fd)
            else:
                os.set_inheritable(LOCK_FD, True)
        except OSError as exc:
            if opened_fd != LOCK_FD:
                try:
                    os.close(opened_fd)
                except OSError:
                    pass
            raise EpochLockFailure(
                _error(LOCK_FAILURE, f"fd9_install_failed:{exc.errno}:{exc.strerror}")
            ) from exc

        try:
            result = subprocess.run(
                [str(LOCKF_PATH), "-s", "-t", "0", str(LOCK_FD)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                pass_fds=(LOCK_FD,),
                check=False,
            )
        except OSError as exc:
            raise EpochLockFailure(
                _error(LOCK_FAILURE, f"lockf_exec_failed:{exc.errno}:{exc.strerror}")
            ) from exc

        if result.returncode == EXIT_OWNER_BUSY:
            try:
                os.close(LOCK_FD)
            except OSError:
                pass
            raise EpochOwnerBusy(OWNER_BUSY)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            try:
                os.close(LOCK_FD)
            except OSError:
                pass
            raise EpochLockFailure(
                _error(LOCK_FAILURE, f"lockf_exit_{result.returncode}:{stderr}")
            )

        _PROCESS_LOCK_ACQUIRED = True
        # The provider boundary depends on close-on-exec, but lockf itself had
        # to receive the descriptor to establish the inherited open-file lock.
        os.set_inheritable(LOCK_FD, False)
        try:
            validate_lock_metadata(os.fstat(LOCK_FD), os.lstat(self.path))
        except OSError as exc:
            raise EpochLockFailure(
                _error(LOCK_FAILURE, f"post_lock_stat_failed:{exc.errno}:{exc.strerror}")
            ) from exc
        self.acquired = True


class LifecycleState(str, Enum):
    SEALED = "SEALED"
    RUNNING = "RUNNING"
    EXECUTION_TERMINAL = "EXECUTION_TERMINAL"
    AGGREGATED = "AGGREGATED"
    CLOSED = "CLOSED"


_NEXT_LIFECYCLE_STATE = {
    LifecycleState.SEALED: LifecycleState.RUNNING,
    LifecycleState.RUNNING: LifecycleState.EXECUTION_TERMINAL,
    LifecycleState.EXECUTION_TERMINAL: LifecycleState.AGGREGATED,
    LifecycleState.AGGREGATED: LifecycleState.CLOSED,
}


@dataclass
class LifecycleMachine:
    """Strict epoch lifecycle plus immutable sealed-authority tracking."""

    state: LifecycleState
    authority_sha256: str
    durable_reservation_or_start_seen: bool = False

    def __post_init__(self) -> None:
        if not _HEX_SHA256_RE.fullmatch(self.authority_sha256):
            raise LifecycleError("authority_sha256_must_be_lowercase_sha256")

    @property
    def authority_mutable(self) -> bool:
        return (
            self.state is LifecycleState.SEALED
            and not self.durable_reservation_or_start_seen
        )

    def observe_durable_transition(self, transition: str) -> None:
        if transition in {"reservation", "provider-start-intent"}:
            self.durable_reservation_or_start_seen = True

    def observe_ledger(self, snapshot: "LedgerSnapshot") -> None:
        for entry in snapshot.entries:
            transition = entry.get("transition")
            if transition in {"reservation", "provider-start-intent"}:
                self.durable_reservation_or_start_seen = True
                return

    def verify_authority(self, observed_sha256: str) -> None:
        if observed_sha256 != self.authority_sha256:
            disposition = "immutable" if not self.authority_mutable else "sealed"
            raise LifecycleError(f"authority_digest_mismatch:{disposition}")

    def reseal(self, new_authority_sha256: str) -> None:
        if not self.authority_mutable:
            raise LifecycleError("reseal_requires_clean_SEALED_state")
        if not _HEX_SHA256_RE.fullmatch(new_authority_sha256):
            raise LifecycleError("authority_sha256_must_be_lowercase_sha256")
        self.authority_sha256 = new_authority_sha256

    def migrate(self, new_authority_sha256: str) -> None:
        self.reseal(new_authority_sha256)

    def transition(self, target: LifecycleState | str) -> None:
        requested = LifecycleState(target)
        expected = _NEXT_LIFECYCLE_STATE.get(self.state)
        if requested is not expected:
            expected_name = expected.value if expected is not None else "NONE"
            raise LifecycleError(
                f"invalid_lifecycle_transition:{self.state.value}->{requested.value};"
                f"expected:{expected_name}"
            )
        self.state = requested


def decimal_text(value: Decimal | str | int) -> str:
    """Return a non-negative, finite Decimal in canonical non-exponent form."""

    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError(f"invalid_decimal:{value!r}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise LedgerError(f"invalid_decimal:{value!r}")
    if parsed == 0:
        return "0"
    text = format(parsed, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def canonical_amount_text(amount: Decimal | str | int, version: int) -> str:
    """Serialize a ledger amount in the representation its version defines."""

    if version == HISTORICAL_LEDGER_VERSION:
        return decimal_text(amount)
    if version != LEDGER_WRITE_VERSION:
        raise LedgerError(f"unsupported_ledger_version:{version!r}")
    try:
        return _usd.canonical_usd_text(amount)
    except _usd.CanonicalUsdError as exc:
        raise LedgerError(f"amount_not_representable_in_canonical_usd:{exc}") from exc


def _exact_amount(value: Decimal | str | int) -> Decimal:
    """Parse a caller-supplied amount exactly, before any canonicalization.

    Cap and slot invariants are evaluated on this exact value so a sub-quantum
    amount can never slip under a limit by being rounded first.
    """

    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError(f"invalid_decimal:{value!r}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise LedgerError(f"invalid_decimal:{value!r}")
    return parsed


def _decimal(value: Any, field_name: str, version: int) -> Decimal:
    """Parse a persisted amount under the representation of its own version."""

    if not isinstance(value, str):
        raise LedgerError(_error(AMBIGUOUS_LEDGER, f"{field_name}_not_string"))
    if version == LEDGER_WRITE_VERSION:
        if not _usd.is_canonical_usd(value):
            raise LedgerError(_error(AMBIGUOUS_LEDGER, f"{field_name}_not_canonical"))
        return _usd.parse_canonical_usd(value)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise LedgerError(_error(AMBIGUOUS_LEDGER, f"{field_name}_invalid")) from exc
    if not parsed.is_finite() or parsed < 0 or decimal_text(parsed) != value:
        raise LedgerError(_error(AMBIGUOUS_LEDGER, f"{field_name}_not_canonical"))
    return parsed


def _canonical_json(record: Mapping[str, Any]) -> bytes:
    return json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _entry_hash(record_without_hash: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(record_without_hash)).hexdigest()


@dataclass(frozen=True)
class LedgerSnapshot:
    entries: tuple[Mapping[str, Any], ...] = ()
    lifetime_total_spend: Decimal = Decimal("0")
    outstanding_reservations: Mapping[str, Decimal] = field(default_factory=dict)
    started_unresolved: Mapping[str, Decimal] = field(default_factory=dict)
    used_slots: frozenset[str] = frozenset()
    previous_hash: str = "0" * 64
    file_identity: tuple[int, int] | None = None
    file_size: int = 0

    @property
    def outstanding_total(self) -> Decimal:
        return sum(self.outstanding_reservations.values(), Decimal("0"))

    @property
    def conservative_unresolved_debit(self) -> Decimal:
        return sum(self.started_unresolved.values(), Decimal("0"))


class BudgetLedger:
    """Hash-chained append-only exact-decimal budget authority."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        lifetime_hard_cap: Decimal | str | int,
    ) -> None:
        self.path = Path(path)
        self.lifetime_hard_cap = Decimal(decimal_text(lifetime_hard_cap))

    def load(self) -> LedgerSnapshot:
        if not hasattr(os, "O_NOFOLLOW"):
            raise LedgerError(_error(AMBIGUOUS_LEDGER, "O_NOFOLLOW_unavailable"))
        try:
            initial_path_stat = os.lstat(self.path)
        except FileNotFoundError:
            return LedgerSnapshot()
        except OSError as exc:
            raise LedgerError(_error(AMBIGUOUS_LEDGER, f"stat_failed:{exc.errno}")) from exc
        if not stat.S_ISREG(initial_path_stat.st_mode):
            raise LedgerError(_error(AMBIGUOUS_LEDGER, "ledger_not_regular"))
        if initial_path_stat.st_nlink != 1:
            raise LedgerError(_error(AMBIGUOUS_LEDGER, "ledger_link_count"))

        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise LedgerError(_error(AMBIGUOUS_LEDGER, f"secure_open_failed:{exc.errno}")) from exc
        try:
            descriptor_stat = os.fstat(descriptor)
            current_path_stat = os.lstat(self.path)
            if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
                current_path_stat.st_dev,
                current_path_stat.st_ino,
            ):
                raise LedgerError(_error(AMBIGUOUS_LEDGER, "ledger_fd_path_inode_mismatch"))
            if not stat.S_ISREG(descriptor_stat.st_mode) or descriptor_stat.st_nlink != 1:
                raise LedgerError(_error(AMBIGUOUS_LEDGER, "ledger_not_single_link_regular"))
            if (
                descriptor_stat.st_uid != os.getuid()
                or stat.S_IMODE(descriptor_stat.st_mode) != 0o600
            ):
                raise LedgerError(_error(AMBIGUOUS_LEDGER, "ledger_owner_or_mode"))
            blocks: list[bytes] = []
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                blocks.append(block)
            raw = b"".join(blocks)
        except OSError as exc:
            raise LedgerError(_error(AMBIGUOUS_LEDGER, f"read_failed:{exc.errno}")) from exc
        finally:
            os.close(descriptor)
        if raw and not raw.endswith(b"\n"):
            raise LedgerError(_error(AMBIGUOUS_LEDGER, "half_written_ledger"))

        entries: list[Mapping[str, Any]] = []
        reservations: dict[str, Decimal] = {}
        started: dict[str, Decimal] = {}
        used_slots: set[str] = set()
        total_spend = Decimal("0")
        previous_hash = "0" * 64

        for index, raw_line in enumerate(raw.splitlines(), start=1):
            try:
                decoded = raw_line.decode("utf-8")
                record = json.loads(decoded)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise LedgerError(
                    _error(AMBIGUOUS_LEDGER, f"line_{index}_invalid_json")
                ) from exc
            if not isinstance(record, dict):
                raise LedgerError(_error(AMBIGUOUS_LEDGER, f"line_{index}_not_object"))
            version = record.get("version")
            # Version 1 and version 2 differ only in how the amount is
            # written.  Both replay from their own persisted bytes, so no
            # historical entry is ever rewritten to satisfy a newer format.
            if isinstance(version, bool) or version not in SUPPORTED_LEDGER_VERSIONS:
                raise LedgerError(_error(AMBIGUOUS_LEDGER, f"line_{index}_version"))
            if record.get("sequence") != index:
                raise LedgerError(_error(AMBIGUOUS_LEDGER, f"line_{index}_sequence"))
            if record.get("previous_hash") != previous_hash:
                raise LedgerError(_error(AMBIGUOUS_LEDGER, f"line_{index}_chain"))
            recorded_hash = record.get("entry_hash")
            if not isinstance(recorded_hash, str) or not _HEX_SHA256_RE.fullmatch(recorded_hash):
                raise LedgerError(_error(AMBIGUOUS_LEDGER, f"line_{index}_hash_shape"))
            unsigned = dict(record)
            del unsigned["entry_hash"]
            if _entry_hash(unsigned) != recorded_hash:
                raise LedgerError(_error(AMBIGUOUS_LEDGER, f"line_{index}_hash"))

            transition = record.get("transition")
            slot_id = record.get("slot_id")
            if transition not in _LEDGER_TRANSITIONS:
                raise LedgerError(_error(AMBIGUOUS_LEDGER, f"line_{index}_transition"))
            if not isinstance(slot_id, str) or not _SLOT_ID_RE.fullmatch(slot_id):
                raise LedgerError(_error(AMBIGUOUS_LEDGER, f"line_{index}_slot"))
            amount = _decimal(record.get("amount"), f"line_{index}_amount", version)

            if transition == "reservation":
                if slot_id in used_slots:
                    raise LedgerError(_error(AMBIGUOUS_LEDGER, "duplicate_reservation"))
                used_slots.add(slot_id)
                reservations[slot_id] = amount
            elif transition == "provider-start-intent":
                if slot_id not in reservations or slot_id in started:
                    raise LedgerError(
                        _error(AMBIGUOUS_LEDGER, "orphan_or_duplicate_provider_start")
                    )
                if amount != reservations[slot_id]:
                    raise LedgerError(_error(AMBIGUOUS_LEDGER, "start_cap_mismatch"))
                started[slot_id] = amount
            elif transition == "settlement":
                if slot_id not in reservations or slot_id not in started:
                    raise LedgerError(_error(AMBIGUOUS_LEDGER, "orphan_settlement"))
                if amount > reservations[slot_id]:
                    raise LedgerError(_error(AMBIGUOUS_LEDGER, "settlement_over_reservation"))
                total_spend += amount
                del reservations[slot_id]
                del started[slot_id]
            elif transition == "release":
                if slot_id not in reservations or slot_id in started:
                    raise LedgerError(_error(AMBIGUOUS_LEDGER, "invalid_release"))
                if amount != reservations[slot_id]:
                    raise LedgerError(_error(AMBIGUOUS_LEDGER, "release_amount_mismatch"))
                del reservations[slot_id]

            if total_spend + sum(reservations.values(), Decimal("0")) > self.lifetime_hard_cap:
                raise LedgerError(_error(AMBIGUOUS_LEDGER, "lifetime_hard_cap_exceeded"))
            previous_hash = recorded_hash
            entries.append(record)

        return LedgerSnapshot(
            entries=tuple(entries),
            lifetime_total_spend=total_spend,
            outstanding_reservations=dict(reservations),
            started_unresolved=dict(started),
            used_slots=frozenset(used_slots),
            previous_hash=previous_hash,
            file_identity=(descriptor_stat.st_dev, descriptor_stat.st_ino),
            file_size=len(raw),
        )

    def _append(self, transition: str, slot_id: str, amount: Decimal | str | int) -> Mapping[str, Any]:
        if transition not in _LEDGER_TRANSITIONS:
            raise LedgerError(f"unsupported_transition:{transition}")
        if not isinstance(slot_id, str) or not _SLOT_ID_RE.fullmatch(slot_id):
            raise LedgerError("invalid_slot_id")
        exact_amount = _exact_amount(amount)
        snapshot = self.load()

        if transition == "reservation":
            if slot_id in snapshot.used_slots:
                raise LedgerError("slot_already_consumed_no_silent_rerun")
            projected = (
                snapshot.lifetime_total_spend
                + snapshot.outstanding_total
                + exact_amount
            )
            if projected > self.lifetime_hard_cap:
                raise LedgerError("lifetime_hard_cap_would_be_exceeded")
        elif transition == "provider-start-intent":
            expected = snapshot.outstanding_reservations.get(slot_id)
            if expected is None or slot_id in snapshot.started_unresolved:
                raise LedgerError("provider_start_requires_one_unstarted_reservation")
            if exact_amount != expected:
                raise LedgerError("provider_start_cap_mismatch")
        elif transition == "settlement":
            expected = snapshot.started_unresolved.get(slot_id)
            if expected is None:
                raise LedgerError("settlement_requires_started_reservation")
            if exact_amount > expected:
                raise LedgerError("settlement_exceeds_sealed_invocation_cap")
        elif transition == "release":
            expected = snapshot.outstanding_reservations.get(slot_id)
            if expected is None or slot_id in snapshot.started_unresolved:
                raise LedgerError("release_requires_unstarted_reservation")
            if exact_amount != expected:
                raise LedgerError("release_amount_mismatch")

        # Canonicalize only after every invariant held for the exact value, so
        # an amount finer than the quantum fails closed instead of rounding.
        amount_string = canonical_amount_text(exact_amount, LEDGER_WRITE_VERSION)

        unsigned: dict[str, Any] = {
            "version": LEDGER_WRITE_VERSION,
            "sequence": len(snapshot.entries) + 1,
            "transition": transition,
            "slot_id": slot_id,
            "amount": amount_string,
            "previous_hash": snapshot.previous_hash,
        }
        record = dict(unsigned)
        record["entry_hash"] = _entry_hash(unsigned)
        encoded = _canonical_json(record) + b"\n"

        if not hasattr(os, "O_NOFOLLOW"):
            raise LedgerError("O_NOFOLLOW_unavailable")
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
        if snapshot.file_identity is None:
            flags |= os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(self.path, flags, 0o600)
            try:
                file_stat = os.fstat(descriptor)
                path_stat = os.lstat(self.path)
                if (file_stat.st_dev, file_stat.st_ino) != (
                    path_stat.st_dev,
                    path_stat.st_ino,
                ):
                    raise LedgerError("ledger_fd_path_inode_mismatch")
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
                    raise LedgerError("ledger_not_single_link_regular_file")
                if file_stat.st_uid != os.getuid() or stat.S_IMODE(file_stat.st_mode) != 0o600:
                    raise LedgerError("ledger_owner_or_mode_invalid")
                observed_identity = (file_stat.st_dev, file_stat.st_ino)
                if snapshot.file_identity is None:
                    if file_stat.st_size != 0:
                        raise LedgerError("ledger_created_nonempty_after_read")
                elif (
                    observed_identity != snapshot.file_identity
                    or file_stat.st_size != snapshot.file_size
                ):
                    raise LedgerError("ledger_changed_after_read")
                written = os.write(descriptor, encoded)
                if written != len(encoded):
                    raise LedgerError("short_ledger_append")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise LedgerError(f"ledger_append_failed:{exc.errno}:{exc.strerror}") from exc
        return record

    def reserve(self, slot_id: str, sealed_invocation_cap: Decimal | str | int) -> Mapping[str, Any]:
        return self._append("reservation", slot_id, sealed_invocation_cap)

    def provider_start_intent(
        self, slot_id: str, sealed_invocation_cap: Decimal | str | int
    ) -> Mapping[str, Any]:
        return self._append("provider-start-intent", slot_id, sealed_invocation_cap)

    def settle(self, slot_id: str, terminal_cost: Decimal | str | int) -> Mapping[str, Any]:
        return self._append("settlement", slot_id, terminal_cost)

    def release(self, slot_id: str) -> Mapping[str, Any]:
        snapshot = self.load()
        amount = snapshot.outstanding_reservations.get(slot_id)
        if amount is None:
            raise LedgerError("release_requires_outstanding_reservation")
        return self._append("release", slot_id, amount)

    def conservatively_settle_unresolved(self) -> tuple[Mapping[str, Any], ...]:
        """Debit every started unresolved slot at its full sealed cap."""

        records: list[Mapping[str, Any]] = []
        snapshot = self.load()
        for slot_id in sorted(snapshot.started_unresolved):
            records.append(self.settle(slot_id, snapshot.started_unresolved[slot_id]))
        return tuple(records)


@dataclass(frozen=True)
class ExecutableIdentity:
    path: str
    device: int | None
    inode: int | None


@dataclass(frozen=True)
class ProcessIdentity:
    host: str
    boot_id: str
    pid: int
    start_identity: str
    executable: ExecutableIdentity
    pgid: int
    sid: int


@dataclass(frozen=True)
class ProcessRecord:
    identity: ProcessIdentity
    ppid: int
    cwd: str | None = None
    open_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class WriterLease:
    writer: ProcessIdentity
    provider_descendants: tuple[ProcessIdentity, ...] = ()


@dataclass(frozen=True)
class RunEvidenceState:
    slot_id: str
    reservation_present: bool
    provider_start_intent_present: bool
    terminal_evidence_present: bool
    complete: bool
    ambiguous: bool = False


@dataclass(frozen=True)
class FreshStartResult:
    passed: bool
    reasons: tuple[str, ...] = ()

    def require_pass(self) -> None:
        if not self.passed:
            raise RecoveryError(_error(RECOVERY_BLOCKED, ",".join(self.reasons)))


def process_identity_differences(
    recorded: ProcessIdentity, observed: ProcessIdentity
) -> tuple[str, ...]:
    """Compare all identity dimensions; PID alone can never prove identity."""

    differences: list[str] = []
    for name in ("host", "boot_id", "pid", "start_identity", "executable", "pgid", "sid"):
        if getattr(recorded, name) != getattr(observed, name):
            differences.append(name)
    return tuple(differences)


def same_process_identity(recorded: ProcessIdentity, observed: ProcessIdentity) -> bool:
    return not process_identity_differences(recorded, observed)


def _path_is_within(candidate: str | os.PathLike[str], root: Path) -> bool:
    try:
        canonical_candidate = Path(candidate).resolve(strict=False)
        canonical_root = root.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RecoveryError("managed_path_resolution_failed") from exc
    try:
        canonical_candidate.relative_to(canonical_root)
    except ValueError:
        return False
    return True


def reconcile_fresh_start(
    *,
    sealed_host: str | None,
    sealed_boot_id: str | None,
    current_host: str,
    current_boot_id: str,
    writer_lease: WriterLease | None,
    live_processes: Sequence[ProcessRecord],
    managed_roots: Sequence[str | os.PathLike[str]],
    ledger_snapshot: LedgerSnapshot | None,
    run_evidence: Sequence[RunEvidenceState],
    ledger_error: str | None = None,
    controller_pid: int | None = None,
) -> FreshStartResult:
    """Reconcile every required recovery surface without killing or cleaning."""

    reasons: list[str] = []
    if sealed_host is None:
        reasons.append("missing_evidence:host")
    elif sealed_host != current_host:
        reasons.append("host_identity_mismatch")
    if sealed_boot_id is None:
        reasons.append("missing_evidence:boot")
    elif sealed_boot_id != current_boot_id:
        reasons.append("boot_identity_mismatch")

    records_by_pid = {record.identity.pid: record for record in live_processes}
    if len(records_by_pid) != len(live_processes):
        reasons.append("ambiguous_process_table")

    if writer_lease is not None:
        writer_record = records_by_pid.get(writer_lease.writer.pid)
        if writer_record is None:
            reasons.append("stale_writer_lease")
        else:
            differences = process_identity_differences(
                writer_lease.writer, writer_record.identity
            )
            if differences:
                reasons.append("stale_pid_reuse:" + "+".join(differences))
                reasons.append("stale_writer_lease")
            else:
                reasons.append("writer_process_still_alive")

        for descendant in writer_lease.provider_descendants:
            observed = records_by_pid.get(descendant.pid)
            if observed is not None and same_process_identity(descendant, observed.identity):
                reasons.append(f"surviving_provider_descendant:{descendant.pid}")

        # A surviving, previously unrecorded process in the writer's process
        # group/session is ambiguous and therefore blocks recovery too.
        for record in live_processes:
            if record.identity.pid in {
                writer_lease.writer.pid,
                *(identity.pid for identity in writer_lease.provider_descendants),
            }:
                continue
            if controller_pid is not None and record.identity.pid == controller_pid:
                continue
            if (
                record.identity.host == writer_lease.writer.host
                and record.identity.boot_id == writer_lease.writer.boot_id
                and (
                    record.identity.pgid == writer_lease.writer.pgid
                    or record.identity.sid == writer_lease.writer.sid
                )
            ):
                reasons.append(f"ambiguous_surviving_descendant:{record.identity.pid}")

    roots = tuple(Path(root) for root in managed_roots)
    for record in live_processes:
        if controller_pid is not None and record.identity.pid == controller_pid:
            continue
        if record.cwd is not None and any(
            _path_is_within(record.cwd, root) for root in roots
        ):
            reasons.append(f"foreign_cwd_under_epoch_root:{record.identity.pid}")
        for open_path in record.open_paths:
            if any(_path_is_within(open_path, root) for root in roots):
                reasons.append(f"foreign_open_handle:{record.identity.pid}")
                break

    if ledger_error is not None:
        reasons.append(f"ambiguous_ledger:{ledger_error}")
    if ledger_snapshot is None:
        reasons.append("missing_evidence:ledger")
    else:
        for slot_id in sorted(ledger_snapshot.started_unresolved):
            reasons.append(f"unresolved_provider_start_intent:{slot_id}")

    if ledger_snapshot is not None:
        expected_slots = set(ledger_snapshot.used_slots)
        observed_slots = {item.slot_id for item in run_evidence}
        for slot_id in sorted(expected_slots - observed_slots):
            reasons.append(f"missing_evidence:run:{slot_id}")
    for item in run_evidence:
        if item.ambiguous:
            reasons.append(f"ambiguous_run_evidence:{item.slot_id}")
        if not item.complete:
            reasons.append(f"half_written_run:{item.slot_id}")
        if item.provider_start_intent_present and not item.terminal_evidence_present:
            reasons.append(f"provider_start_without_terminal:{item.slot_id}")
        if item.reservation_present and not item.complete:
            reasons.append(f"reservation_without_complete_evidence:{item.slot_id}")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return FreshStartResult(passed=not unique_reasons, reasons=unique_reasons)


def read_run_evidence(path: str | os.PathLike[str], slot_id: str) -> RunEvidenceState:
    """Read one newline-terminated run record; partial JSON fails closed."""

    candidate = Path(path)
    if not candidate.exists():
        return RunEvidenceState(slot_id, False, False, False, False)
    try:
        raw = candidate.read_bytes()
    except OSError:
        return RunEvidenceState(slot_id, False, False, False, False, ambiguous=True)
    if not raw or not raw.endswith(b"\n"):
        return RunEvidenceState(slot_id, False, False, False, False, ambiguous=True)
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return RunEvidenceState(slot_id, False, False, False, False, ambiguous=True)
    if not isinstance(record, dict) or record.get("slot_id") != slot_id:
        return RunEvidenceState(slot_id, False, False, False, False, ambiguous=True)
    reservation = record.get("reservation") is True
    start = record.get("provider_start_intent") is True
    terminal = isinstance(record.get("terminal"), dict)
    complete = reservation and (not start or terminal)
    return RunEvidenceState(slot_id, reservation, start, terminal, complete)


def current_boot_id() -> str:
    """Return a host boot identity without treating wall-clock time as proof."""

    candidates = (
        ["/usr/sbin/sysctl", "-n", "kern.boottime"],
        ["/sbin/sysctl", "-n", "kern.boottime"],
    )
    for command in candidates:
        if not Path(command[0]).is_file():
            continue
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            close_fds=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return hashlib.sha256(result.stdout.strip()).hexdigest()
    proc_stat = Path("/proc/stat")
    if proc_stat.is_file():
        for line in proc_stat.read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return hashlib.sha256(line.encode("ascii")).hexdigest()
    raise RecoveryError("boot_identity_unavailable")


def capture_process_identity(pid: int) -> ProcessIdentity:
    """Capture PID plus start, executable, group, session, host, and boot IDs."""

    if pid <= 0:
        raise RecoveryError("invalid_pid")
    start_result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        close_fds=True,
    )
    executable_result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "comm="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        close_fds=True,
    )
    if start_result.returncode != 0 or executable_result.returncode != 0:
        raise RecoveryError(f"process_identity_unavailable:{pid}")
    start_identity = start_result.stdout.decode("utf-8", errors="strict").strip()
    executable_path = executable_result.stdout.decode("utf-8", errors="strict").strip()
    if not start_identity or not executable_path:
        raise RecoveryError(f"process_identity_unavailable:{pid}")
    try:
        executable_stat = os.stat(executable_path)
        executable = ExecutableIdentity(
            executable_path, executable_stat.st_dev, executable_stat.st_ino
        )
    except OSError:
        executable = ExecutableIdentity(executable_path, None, None)
    try:
        pgid = os.getpgid(pid)
        sid = os.getsid(pid)
    except OSError as exc:
        raise RecoveryError(f"process_group_identity_unavailable:{pid}") from exc
    return ProcessIdentity(
        host=socket.gethostname(),
        boot_id=current_boot_id(),
        pid=pid,
        start_identity=start_identity,
        executable=executable,
        pgid=pgid,
        sid=sid,
    )


def _capture_parent_pid(pid: int) -> int:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "ppid="],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        close_fds=True,
    )
    if result.returncode != 0:
        raise RecoveryError(f"parent_process_identity_unavailable:{pid}")
    try:
        parent_pid = int(result.stdout.decode("ascii", errors="strict").strip())
    except (UnicodeError, ValueError) as exc:
        raise RecoveryError(f"parent_process_identity_unavailable:{pid}") from exc
    return parent_pid


def scan_managed_root_processes(
    managed_roots: Sequence[str | os.PathLike[str]],
    *,
    lsof_path: str | os.PathLike[str] = LSOF_PATH,
) -> tuple[ProcessRecord, ...]:
    """Read cwd/open-handle evidence below managed roots without cleanup.

    Any missing capability, unreadable root, malformed output, or process-table
    race fails closed as a recovery error instead of being treated as no match.
    """

    lsof = Path(lsof_path)
    if not lsof.is_file() or not os.access(lsof, os.X_OK):
        raise RecoveryError("lsof_required_for_open_handle_reconciliation")

    observations: dict[int, dict[str, Any]] = {}
    for supplied_root in managed_roots:
        root = Path(supplied_root)
        try:
            root_stat = os.lstat(root)
        except OSError as exc:
            raise RecoveryError(f"managed_root_unavailable:{root}:{exc.errno}") from exc
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise RecoveryError(f"managed_root_not_real_directory:{root}")

        result = subprocess.run(
            [str(lsof), "-n", "-P", "-F", "pfn", "+D", str(root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            close_fds=True,
        )
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        if result.returncode not in {0, 1} or stderr:
            raise RecoveryError(
                f"lsof_reconciliation_failed:{result.returncode}:{stderr}"
            )

        current_pid: int | None = None
        current_descriptor: str | None = None
        for raw_line in result.stdout.splitlines():
            if not raw_line:
                continue
            prefix = raw_line[:1]
            value = raw_line[1:].decode("utf-8", errors="strict")
            if prefix == b"p":
                try:
                    current_pid = int(value)
                except ValueError as exc:
                    raise RecoveryError("malformed_lsof_pid") from exc
                observations.setdefault(
                    current_pid, {"cwd": None, "open_paths": set()}
                )
                current_descriptor = None
            elif prefix == b"f":
                if current_pid is None:
                    raise RecoveryError("malformed_lsof_descriptor_order")
                current_descriptor = value
            elif prefix == b"n":
                if current_pid is None or current_descriptor is None:
                    raise RecoveryError("malformed_lsof_name_order")
                if current_descriptor == "cwd":
                    observations[current_pid]["cwd"] = value
                else:
                    observations[current_pid]["open_paths"].add(value)

    records: list[ProcessRecord] = []
    for pid in sorted(observations):
        evidence = observations[pid]
        records.append(
            ProcessRecord(
                identity=capture_process_identity(pid),
                ppid=_capture_parent_pid(pid),
                cwd=evidence["cwd"],
                open_paths=tuple(sorted(evidence["open_paths"])),
            )
        )
    return tuple(records)


def create_opaque_session_root(
    parent: str | os.PathLike[str],
    token_factory: Callable[[], str] | None = None,
) -> Path:
    """Create exactly one independently random, condition-neutral workspace."""

    root = Path(parent)
    if not root.is_dir():
        raise OpaqueWorkspaceError("session_parent_must_exist")
    token = (
        secrets.token_hex(OPAQUE_SESSION_TOKEN_HEX_WIDTH // 2)
        if token_factory is None
        else token_factory()
    )
    if not isinstance(token, str) or _OPAQUE_SESSION_TOKEN_RE.fullmatch(token) is None:
        raise OpaqueWorkspaceError(
            f"session_token_must_be_{OPAQUE_SESSION_TOKEN_HEX_WIDTH}_lowercase_hex"
        )
    session_root = root / f"session-{token}"
    try:
        session_root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise OpaqueWorkspaceError("opaque_session_collision") from exc
    except OSError as exc:
        raise OpaqueWorkspaceError(
            f"opaque_session_create_failed:{exc.errno}:{exc.strerror}"
        ) from exc
    return session_root


def validate_opaque_session_root(session_root: str | os.PathLike[str]) -> Path:
    root = Path(session_root)
    if not root.is_absolute() or not _OPAQUE_SESSION_RE.fullmatch(root.name):
        raise SandboxPolicyError("SESSION_ROOT_must_be_absolute_opaque_session")
    try:
        metadata = os.lstat(root)
    except OSError as exc:
        raise SandboxPolicyError(f"SESSION_ROOT_unavailable:{exc.errno}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SandboxPolicyError("SESSION_ROOT_must_be_real_directory")
    return root.resolve(strict=True)


def build_sandboxed_command(
    provider_argv: Sequence[str],
    *,
    session_root: str | os.PathLike[str],
    profile_path: str | os.PathLike[str] = SANDBOX_PROFILE_PATH,
    sandbox_exec_path: str | os.PathLike[str] = SANDBOX_EXEC_PATH,
) -> tuple[str, ...]:
    """Build the only supported provider command; no direct fallback exists."""

    if not provider_argv or not all(isinstance(part, str) and part for part in provider_argv):
        raise SandboxPolicyError("provider_argv_must_be_nonempty_strings")
    root = validate_opaque_session_root(session_root)
    profile = Path(profile_path)
    sandbox_exec = Path(sandbox_exec_path)
    if not sandbox_exec.is_file() or not os.access(sandbox_exec, os.X_OK):
        raise SandboxPolicyError("sandbox_exec_required_no_unsandboxed_fallback")
    try:
        profile_stat = os.lstat(profile)
    except OSError as exc:
        raise SandboxPolicyError("sandbox_profile_required_no_unsandboxed_fallback") from exc
    if not stat.S_ISREG(profile_stat.st_mode) or stat.S_ISLNK(profile_stat.st_mode):
        raise SandboxPolicyError("sandbox_profile_must_be_regular_file")
    return (
        str(sandbox_exec),
        "-f",
        str(profile.resolve(strict=True)),
        "-D",
        f"SESSION_ROOT={root}",
        *tuple(provider_argv),
    )


@dataclass(frozen=True)
class ObservedSandboxIdentity:
    """Sandbox identity read from the filesystem, never asserted by a caller.

    Every field here is observed at composition time: the resolved
    ``sandbox-exec`` binary and its bytes, the resolved profile and the SHA256
    of its actual bytes, the bound SESSION_ROOT, and the physical argv that
    will really be executed.
    """

    sandbox_exec_path: str
    sandbox_exec_realpath: str
    sandbox_exec_sha256: str
    sandbox_exec_device: int
    sandbox_exec_inode: int
    profile_path: str
    profile_realpath: str
    profile_sha256: str
    session_root: str
    physical_argv: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "sandbox_exec_path": self.sandbox_exec_path,
            "sandbox_exec_realpath": self.sandbox_exec_realpath,
            "sandbox_exec_sha256": self.sandbox_exec_sha256,
            "sandbox_exec_device": self.sandbox_exec_device,
            "sandbox_exec_inode": self.sandbox_exec_inode,
            "profile_path": self.profile_path,
            "profile_realpath": self.profile_realpath,
            "profile_sha256": self.profile_sha256,
            "session_root": self.session_root,
            "physical_argv": list(self.physical_argv),
            "physical_argv_authority": "epoch_controller.build_sandboxed_command",
            "identity_source": "OBSERVED_FILESYSTEM_BYTES",
        }


def observe_sandboxed_command(
    provider_argv: Sequence[str],
    *,
    session_root: str | os.PathLike[str],
    profile_path: str | os.PathLike[str] = SANDBOX_PROFILE_PATH,
    sandbox_exec_path: str | os.PathLike[str] = SANDBOX_EXEC_PATH,
) -> ObservedSandboxIdentity:
    """Build the physical command and observe the identity that will run it."""

    physical_argv = build_sandboxed_command(
        provider_argv,
        session_root=session_root,
        profile_path=profile_path,
        sandbox_exec_path=sandbox_exec_path,
    )
    sandbox_exec = Path(sandbox_exec_path)
    profile = Path(profile_path)
    try:
        sandbox_exec_realpath = sandbox_exec.resolve(strict=True)
        profile_realpath = profile.resolve(strict=True)
        sandbox_exec_stat = os.stat(sandbox_exec_realpath)
        sandbox_exec_sha256 = sha256_file(sandbox_exec_realpath)
        profile_sha256 = sha256_file(profile_realpath)
    except OSError as exc:
        raise SandboxPolicyError(
            f"sandbox_identity_unobservable:{exc.errno}:{exc.strerror}"
        ) from exc
    return ObservedSandboxIdentity(
        sandbox_exec_path=str(sandbox_exec),
        sandbox_exec_realpath=str(sandbox_exec_realpath),
        sandbox_exec_sha256=sandbox_exec_sha256,
        sandbox_exec_device=sandbox_exec_stat.st_dev,
        sandbox_exec_inode=sandbox_exec_stat.st_ino,
        profile_path=str(profile),
        profile_realpath=str(profile_realpath),
        profile_sha256=profile_sha256,
        session_root=str(validate_opaque_session_root(session_root)),
        physical_argv=physical_argv,
    )


def spawn_sandboxed_provider(
    provider_argv: Sequence[str],
    *,
    session_root: str | os.PathLike[str],
    environment: Mapping[str, str] | None = None,
    stdin: Any = None,
    stdout: Any = None,
    stderr: Any = None,
    profile_path: str | os.PathLike[str] = SANDBOX_PROFILE_PATH,
    sandbox_exec_path: str | os.PathLike[str] = SANDBOX_EXEC_PATH,
) -> subprocess.Popen[Any]:
    """Spawn a provider inside the mandatory sandbox with FD 9 closed."""

    command = build_sandboxed_command(
        provider_argv,
        session_root=session_root,
        profile_path=profile_path,
        sandbox_exec_path=sandbox_exec_path,
    )
    explicit_environment = None
    if environment is not None:
        explicit_environment = dict(environment)
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in explicit_environment.items()
        ):
            raise SandboxPolicyError("provider_environment_must_contain_strings")
    return subprocess.Popen(
        command,
        cwd=str(validate_opaque_session_root(session_root)),
        env=explicit_environment,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        close_fds=True,
        start_new_session=True,
    )


def start_reserved_provider(
    ledger: BudgetLedger,
    slot_id: str,
    sealed_invocation_cap: Decimal | str | int,
    provider_argv: Sequence[str],
    *,
    session_root: str | os.PathLike[str],
    **spawn_options: Any,
) -> subprocess.Popen[Any]:
    """Durably record start intent before the sandboxed process can exist."""

    ledger.provider_start_intent(slot_id, sealed_invocation_cap)
    return spawn_sandboxed_provider(
        provider_argv,
        session_root=session_root,
        **spawn_options,
    )


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def load_manifest_after_lock(
    path: str | os.PathLike[str],
    *,
    expected_sha256: str | None = None,
) -> Mapping[str, Any]:
    """Hash and parse one securely opened snapshot after acquiring EpochLock."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise EpochControllerError("manifest_O_NOFOLLOW_unavailable")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise EpochControllerError(f"manifest_unreadable:{type(exc).__name__}:{exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise EpochControllerError("manifest_must_be_regular_file")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        raw = b"".join(blocks)
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or len(raw) != before.st_size
        ):
            raise EpochControllerError("manifest_changed_during_read")
    except OSError as exc:
        raise EpochControllerError(f"manifest_unreadable:{type(exc).__name__}:{exc}") from exc
    finally:
        os.close(descriptor)

    # Both operations consume this exact byte snapshot.  Reopening the pathname
    # could authenticate different content from the JSON used as authority.
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and observed_sha256 != expected_sha256:
        raise LifecycleError("authority_digest_mismatch")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EpochControllerError(f"manifest_unreadable:{type(exc).__name__}:{exc}") from exc
    if not isinstance(manifest, dict):
        raise EpochControllerError("manifest_root_must_be_object")
    return manifest


def acquire_before_authority(
    lock_path: str | os.PathLike[str],
    authority_loader: Callable[[], Any],
) -> tuple[EpochLock, Any]:
    """Make lock-before-authority ordering explicit and directly testable."""

    lock = EpochLock(lock_path)
    lock.acquire()
    return lock, authority_loader()


def _child_fd9_probe() -> str:
    script = (
        "import os,sys\n"
        "try:\n os.fstat(9)\n"
        "except OSError:\n sys.stdout.write('FD9_CLOSED\\n')\n"
        "else:\n sys.stdout.write('FD9_OPEN\\n'); sys.exit(1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        close_fds=True,
    )
    if result.returncode != 0:
        raise EpochLockFailure(_error(LOCK_FAILURE, "fd9_inherited_by_child"))
    return result.stdout.decode("utf-8", errors="strict").strip()


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Acquire the Fable epoch owner lock before authority access."
    )
    parser.add_argument("--lock-path", default=str(PRODUCTION_LOCK_PATH))
    parser.add_argument("--manifest")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--probe-lock-only", action="store_true")
    parser.add_argument("--probe-child-fd9", action="store_true")
    parser.add_argument("--hold-seconds", type=float, default=0.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        lock = EpochLock(args.lock_path)
        lock.acquire()
        print("FABLE_EPOCH_LOCK_ACQUIRED", flush=True)

        if args.probe_child_fd9:
            print(_child_fd9_probe(), flush=True)
        if args.hold_seconds < 0:
            raise EpochControllerError("hold_seconds_must_be_nonnegative")
        if args.hold_seconds:
            time.sleep(args.hold_seconds)
        if args.probe_lock_only:
            return 0

        if args.manifest is None:
            raise EpochControllerError("manifest_required")
        manifest = load_manifest_after_lock(
            args.manifest, expected_sha256=args.expected_manifest_sha256
        )
        # Emit only a structural result; this card never starts a real provider
        # or materializes a real epoch through the command-line entry point.
        print(
            json.dumps(
                {"authority_verified": True, "manifest_keys": sorted(manifest)},
                sort_keys=True,
                separators=(",", ":"),
            ),
            flush=True,
        )
        return 0
    except EpochOwnerBusy:
        print(OWNER_BUSY, file=sys.stderr, flush=True)
        return EXIT_OWNER_BUSY
    except EpochLockFailure as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return EXIT_LOCK_FAILURE
    except EpochControllerError as exc:
        print(f"FABLE_EPOCH_CONTRACT_FAILURE: {exc}", file=sys.stderr, flush=True)
        return EXIT_CONTRACT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AMBIGUOUS_LEDGER",
    "HISTORICAL_LEDGER_VERSION",
    "LEDGER_WRITE_VERSION",
    "OPAQUE_SESSION_TOKEN_HEX_WIDTH",
    "ObservedSandboxIdentity",
    "SUPPORTED_LEDGER_VERSIONS",
    "BudgetLedger",
    "EpochControllerError",
    "EpochLock",
    "EpochLockFailure",
    "EpochOwnerBusy",
    "ExecutableIdentity",
    "FreshStartResult",
    "LedgerError",
    "LedgerSnapshot",
    "LifecycleError",
    "LifecycleMachine",
    "LifecycleState",
    "OpaqueWorkspaceError",
    "ProcessIdentity",
    "ProcessRecord",
    "RecoveryError",
    "RunEvidenceState",
    "SandboxPolicyError",
    "WriterLease",
    "acquire_before_authority",
    "build_sandboxed_command",
    "canonical_amount_text",
    "capture_process_identity",
    "create_opaque_session_root",
    "current_boot_id",
    "decimal_text",
    "load_manifest_after_lock",
    "observe_sandboxed_command",
    "process_identity_differences",
    "read_run_evidence",
    "reconcile_fresh_start",
    "same_process_identity",
    "scan_managed_root_processes",
    "sha256_file",
    "spawn_sandboxed_provider",
    "start_reserved_provider",
    "validate_lock_metadata",
    "validate_opaque_session_root",
]
