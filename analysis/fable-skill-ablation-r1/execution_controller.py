#!/usr/bin/env python3
"""Manifest-bound execution controller for the sealed Fable R2 ablation.

The controller owns the single-writer lock, durable execution state, the sole
reservation/accounting ledger, and restart reconciliation.  It deliberately
does not own a concrete model subprocess.  A provider boundary and executor
identity probe are mandatory dependencies, which keeps every acceptance test
offline and makes the durability boundary observable without invoking Claude.
"""

from __future__ import annotations

import copy
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from runner import ModelInvocation, audit_model_visible_surfaces, select_manifest_slot


REQUIRED_MANIFEST_SHA256 = (
    "7be18d2478759ba76cb0a161159d2660fa4cd7ffa4a7cb51b4cdb772d344cabd"
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SESSION_RE = re.compile(r"session-[0-9a-f]{32,64}\Z")
_PROCESS_LOCK_GUARD = threading.Lock()
_PROCESS_LOCKS: set[tuple[int, int]] = set()


class ExecutionControllerError(RuntimeError):
    """Base class for fail-closed controller failures."""


class ManifestIdentityError(ExecutionControllerError):
    """The manifest bytes or their internal authority are not the sealed R2."""


class AuthorityConflict(ExecutionControllerError):
    """Two required authority surfaces are absent, incomplete, or contradictory."""


class WriterLockBusy(ExecutionControllerError):
    """Another writer owns the canonical nonblocking exclusive OS lock."""


class WriterLockError(ExecutionControllerError):
    """The immutable writer-lease inode cannot be securely locked."""


class EntryPreconditionError(ExecutionControllerError):
    """First entry is not a clean SEALED state."""


class LedgerError(ExecutionControllerError):
    """The execution ledger is incomplete, ambiguous, or internally inconsistent."""


class ProviderIdentityError(ExecutionControllerError):
    """The dependency-injected provider does not match the sealed path/version."""


class ExecutorIdentityError(ExecutionControllerError):
    """The canonical executor commit/tree/source identity does not match."""


class OrderingError(ExecutionControllerError):
    """A reservation, invocation, retry, or pair checkpoint is out of order."""


class RetryNotAllowed(OrderingError):
    """The requested retry is not the sole manifest-authorized retry."""


class BudgetExhausted(ExecutionControllerError):
    """The next manifest-defined reservation would exceed the lifetime cap."""


class UnresolvedProviderCost(ExecutionControllerError):
    """An initiated attempt has no exact authoritative provider cost."""


class RecoveryAborted(ExecutionControllerError):
    """Restart reconciliation durably transitioned the epoch to ABORTED."""


class TerminalStateError(ExecutionControllerError):
    """A terminal execution state has no outgoing transition."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _decode_json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        decoded = raw.decode("utf-8", errors="strict")
        value = json.loads(decoded, object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise AuthorityConflict(f"{label}_invalid_json:{exc}") from exc
    if not isinstance(value, dict):
        raise AuthorityConflict(f"{label}_root_not_object")
    return value


def _canonical_json(record: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            record,
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise AuthorityConflict(f"{label}_not_decimal")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AuthorityConflict(f"{label}_invalid_decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise AuthorityConflict(f"{label}_invalid_decimal")
    return parsed


def decimal_text(value: Any, label: str = "decimal") -> str:
    parsed = _decimal(value, label)
    if parsed == 0:
        return "0"
    rendered = format(parsed, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _ledger_decimal(value: Any, label: str) -> Decimal:
    if not isinstance(value, str):
        raise LedgerError(f"{label}_not_string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise LedgerError(f"{label}_invalid") from exc
    if not parsed.is_finite() or parsed < 0 or decimal_text(parsed) != value:
        raise LedgerError(f"{label}_not_canonical")
    return parsed


def _required(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    traversed: list[str] = []
    for key in keys:
        traversed.append(key)
        if not isinstance(current, Mapping) or key not in current:
            raise AuthorityConflict("missing_manifest_field:" + ".".join(traversed))
        current = current[key]
    return current


def _secure_read_regular(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    if not hasattr(os, "O_NOFOLLOW"):
        raise AuthorityConflict(f"{label}_O_NOFOLLOW_unavailable")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise AuthorityConflict(f"{label}_lstat_failed:{exc.errno}") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise AuthorityConflict(f"{label}_not_single_link_regular")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise AuthorityConflict(f"{label}_secure_open_failed:{exc.errno}") from exc
    try:
        opened = os.fstat(descriptor)
        after = os.lstat(path)
        identities = {
            (before.st_dev, before.st_ino),
            (opened.st_dev, opened.st_ino),
            (after.st_dev, after.st_ino),
        }
        if len(identities) != 1:
            raise AuthorityConflict(f"{label}_inode_changed")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        return b"".join(blocks), opened
    except OSError as exc:
        raise AuthorityConflict(f"{label}_read_failed:{exc.errno}") from exc
    finally:
        os.close(descriptor)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_bytes(path: Path, raw: bytes, mode: int = 0o600) -> None:
    """Same-directory temp -> fsync -> atomic rename -> directory fsync."""

    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode,
        )
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_json(path: Path, record: Mapping[str, Any]) -> None:
    _atomic_write_bytes(path, _canonical_json(record))


def _durable_create_bytes(path: Path, raw: bytes, mode: int = 0o600) -> None:
    """Create an immutable authority artifact; partial crashes fail closed."""

    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
    )
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _durable_create_json(path: Path, record: Mapping[str, Any]) -> None:
    _durable_create_bytes(path, _canonical_json(record))


def _ensure_directory(path: Path, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise AuthorityConflict(f"{label}_missing:{exc.errno}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise AuthorityConflict(f"{label}_not_real_directory")


def _durable_mkdir(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        _ensure_directory(path, "existing_execution_directory")
        return
    _fsync_directory(path.parent)


@dataclass(frozen=True)
class RootMapping:
    """Map sealed logical R2 paths to one physical root for offline tests."""

    logical_root: Path
    physical_root: Path

    def __init__(self, logical_root: str | os.PathLike[str], physical_root: str | os.PathLike[str]):
        logical = Path(logical_root)
        physical = Path(physical_root)
        if not logical.is_absolute() or not physical.is_absolute():
            raise ValueError("logical and physical roots must be absolute")
        object.__setattr__(self, "logical_root", logical)
        object.__setattr__(self, "physical_root", physical)

    def physical(self, logical_path: str | os.PathLike[str]) -> Path:
        candidate = Path(logical_path)
        if not candidate.is_absolute():
            raise AuthorityConflict("manifest_path_not_absolute")
        try:
            relative = candidate.relative_to(self.logical_root)
        except ValueError as exc:
            raise AuthorityConflict(f"path_outside_sealed_root:{candidate}") from exc
        return self.physical_root / relative

    def logical(self, physical_path: str | os.PathLike[str]) -> Path:
        candidate = Path(physical_path).resolve(strict=False)
        root = self.physical_root.resolve(strict=False)
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise AuthorityConflict(f"physical_path_outside_mapped_root:{candidate}") from exc
        return self.logical_root / relative


@dataclass(frozen=True)
class ExecutionAuthorization:
    manifest_sha256: str
    epoch_id: str
    authorization_source: str
    owner_hash_signoff: bool
    paid_execution_authorized: bool


@dataclass(frozen=True)
class ScheduleSlot:
    run_id: str
    task_id: str
    condition: str
    pair_index: int
    position_in_pair: int
    schedule_order: int


@dataclass(frozen=True)
class ExecutorIdentity:
    head: str
    tree: str
    source_sha256: str
    test_sha256: str
    runtime_source_sha256: str


class ExecutorIdentityProbe(Protocol):
    def probe(self, authority: "ManifestAuthority") -> ExecutorIdentity:
        """Return observed identity without invoking a model."""


@dataclass(frozen=True)
class ProviderResult:
    outcome: str
    cost_status: str
    exact_cost: str | Decimal | int | None
    raw_evidence: bytes
    canonical_evidence: Mapping[str, Any]
    result_record: Mapping[str, Any]


class ProviderBoundary(Protocol):
    executable: str

    def verify_version(self) -> str:
        """Return a read-only version string without model inference."""

    def invoke(self, invocation: ModelInvocation) -> ProviderResult:
        """Invoke only after the controller has durably persisted intent."""


@dataclass(frozen=True)
class AttemptKey:
    run_id: str
    attempt_ordinal: int


@dataclass(frozen=True)
class StartupResult:
    first_entry: bool
    reconciliation: str
    state: str
    ledger_revision: int


@dataclass(frozen=True)
class ManifestAuthority:
    raw: bytes
    data: Mapping[str, Any]
    sha256: str
    logical_manifest_path: Path
    logical_root: Path
    lease_path: Path
    ledger_path: Path
    state_path: Path
    runs_root: Path
    workspaces_root: Path
    aggregate_path: Path
    epoch_id: str
    lease_task_id: str
    lease_started_at: str
    ledger_schema: str
    total_budget: Decimal
    pair_reservation: Decimal
    invocation_cap: Decimal
    pre_execution_state: str
    executing_state: str
    completed_state: str
    aborted_state: str
    approved_states: tuple[str, ...]
    provider_executable: str
    provider_version: str
    executor_head: str
    executor_tree: str
    executor_source: str
    executor_source_sha256: str
    executor_test: str
    executor_test_sha256: str
    executor_repository: Path
    slots: tuple[ScheduleSlot, ...]
    slots_by_run: Mapping[str, ScheduleSlot]
    slots_by_pair: Mapping[int, tuple[ScheduleSlot, ScheduleSlot]]
    assets: tuple[Mapping[str, Any], ...]

    @classmethod
    def load(cls, logical_manifest_path: Path, storage: RootMapping) -> "ManifestAuthority":
        if logical_manifest_path != storage.logical_root / "manifest.json":
            raise ManifestIdentityError("manifest_path_is_not_canonical")
        raw, _ = _secure_read_regular(storage.physical(logical_manifest_path), "manifest")
        digest = _sha256_bytes(raw)
        if digest != REQUIRED_MANIFEST_SHA256:
            raise ManifestIdentityError(
                f"manifest_sha256_mismatch:{digest}:{REQUIRED_MANIFEST_SHA256}"
            )
        data = _decode_json_object(raw, "manifest")

        logical_root = Path(_required(data, "authority", "new_canonical_root"))
        if logical_root != storage.logical_root:
            raise AuthorityConflict("manifest_root_mapping_mismatch")
        epoch = _required(data, "authority", "epoch_id")
        lease_epoch = _required(data, "single_writer_lease", "epoch_id")
        bound_epoch = _required(
            data, "lease_execution_contract", "identity_binding", "epoch"
        )
        if not isinstance(epoch, str) or epoch != lease_epoch or epoch != bound_epoch:
            raise AuthorityConflict("manifest_epoch_authority_conflict")
        lease_task = _required(data, "single_writer_lease", "task_id")
        authority_task = _required(data, "authority", "task_id")
        if not isinstance(lease_task, str) or lease_task != authority_task:
            raise AuthorityConflict("manifest_lease_task_conflict")

        lease_path = Path(_required(data, "single_writer_lease", "path"))
        ledger_path = Path(
            _required(data, "lease_execution_contract", "execution_ledger_path")
        )
        canonical_ledger = Path(
            _required(
                data,
                "lease_execution_contract",
                "authority",
                "CANONICAL_LEDGER",
            )
        )
        state_path = Path(
            _required(
                data,
                "lease_execution_contract",
                "lock_identity_and_dynamic_execution_state",
                "DYNAMIC_STATE_PATH",
            )
        )
        immutable_lock = Path(
            _required(
                data,
                "lease_execution_contract",
                "lock_identity_and_dynamic_execution_state",
                "IMMUTABLE_LOCK_PATH",
            )
        )
        runs_root = Path(_required(data, "authority", "runs_root"))
        workspaces_root = Path(_required(data, "authority", "workspace_parent"))
        if lease_path != immutable_lock or ledger_path != canonical_ledger:
            raise AuthorityConflict("manifest_execution_path_conflict")
        for candidate in (lease_path, ledger_path, state_path, runs_root, workspaces_root):
            storage.physical(candidate)

        limits = _required(data, "lease_execution_contract", "frozen_limits")
        if not isinstance(limits, Mapping):
            raise AuthorityConflict("manifest_frozen_limits_not_object")
        total_budget = _decimal(_required(limits, "total_usd"), "total_usd")
        pair_reservation = _decimal(
            _required(limits, "pair_reservation_usd"), "pair_reservation_usd"
        )
        invocation_cap = _decimal(
            _required(limits, "invocation_cap"), "invocation_cap"
        )
        conflicting_totals = (
            _decimal(_required(data, "accounting", "TOTAL_EXPERIMENT_BUDGET_USD"), "accounting_total"),
            _decimal(_required(data, "frozen_methodology", "total_experiment_budget_usd"), "methodology_total"),
        )
        if any(value != total_budget for value in conflicting_totals):
            raise AuthorityConflict("manifest_total_budget_conflict")
        if _decimal(
            _required(data, "frozen_methodology", "pair_reservation_usd"),
            "methodology_pair_reservation",
        ) != pair_reservation:
            raise AuthorityConflict("manifest_pair_reservation_conflict")
        if _decimal(
            _required(data, "frozen_methodology", "per_invocation_max_budget_usd"),
            "methodology_invocation_cap",
        ) != invocation_cap:
            raise AuthorityConflict("manifest_invocation_cap_conflict")
        if pair_reservation != invocation_cap * 2:
            raise AuthorityConflict("pair_reservation_not_exactly_two_invocation_caps")
        execution_gate = _required(data, "execution_gate")
        if not isinstance(execution_gate, Mapping):
            raise AuthorityConflict("execution_gate_not_object")
        if _decimal(
            _required(data, "accounting", "CLEAN_R2_RECORDED_SPEND_USD"),
            "clean_r2_recorded_spend",
        ) != 0 or _decimal(
            _required(execution_gate, "NEW_EXPERIMENT_SPEND_USD"),
            "new_experiment_spend",
        ) != 0:
            raise AuthorityConflict("sealed_manifest_r2_spend_not_zero")
        if (
            _required(execution_gate, "MODEL_INVOCATIONS") != 0
            or _required(execution_gate, "PAID_PROVIDER_CALLS") != 0
            or _required(execution_gate, "runs_root_must_remain_empty") is not True
            or _required(execution_gate, "workspaces_root_must_remain_empty") is not True
        ):
            raise AuthorityConflict("sealed_manifest_pre_execution_gate_invalid")

        state_machine = _required(data, "lease_execution_contract", "state_machine")
        if not isinstance(state_machine, Mapping):
            raise AuthorityConflict("state_machine_not_object")
        approved = _required(state_machine, "APPROVED_STATES")
        if not isinstance(approved, list) or not all(isinstance(v, str) for v in approved):
            raise AuthorityConflict("approved_states_invalid")
        states = {
            "pre": _required(state_machine, "PRE_EXECUTION_STATE"),
            "executing": _required(state_machine, "EXECUTION_ACTIVE_STATE"),
            "completed": _required(state_machine, "SUCCESS_TERMINAL_STATE"),
            "aborted": _required(state_machine, "FAILURE_OR_ABORT_STATE"),
        }
        if set(states.values()) != set(approved) or len(approved) != 4:
            raise AuthorityConflict("state_machine_authority_conflict")

        provider_executable = _required(data, "provider", "executable")
        provider_version = _required(data, "provider", "version")
        if not isinstance(provider_executable, str) or not isinstance(provider_version, str):
            raise AuthorityConflict("provider_identity_invalid")
        treatments = _required(data, "treatment")
        if not isinstance(treatments, Mapping):
            raise AuthorityConflict("treatment_not_object")
        for condition in ("off", "on"):
            command = _required(treatments, condition, "command")
            if not isinstance(command, list) or not command or command[0] != provider_executable:
                raise AuthorityConflict("provider_path_treatment_conflict")

        executor = _required(data, "canonical_executor")
        harness = _required(data, "canonical_harness")
        if not isinstance(executor, Mapping) or not isinstance(harness, Mapping):
            raise AuthorityConflict("executor_or_harness_authority_invalid")

        schedule = _required(data, "schedule")
        if not isinstance(schedule, list) or not schedule:
            raise AuthorityConflict("schedule_invalid")
        slots: list[ScheduleSlot] = []
        run_ids: set[str] = set()
        for expected_order, item in enumerate(schedule, start=1):
            if not isinstance(item, Mapping):
                raise AuthorityConflict("schedule_entry_not_object")
            try:
                slot = ScheduleSlot(
                    run_id=str(item["run_id"]),
                    task_id=str(item["task_id"]),
                    condition=str(item["condition"]),
                    pair_index=int(item["pair_index"]),
                    position_in_pair=int(item["position_in_pair"]),
                    schedule_order=int(item["schedule_order"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise AuthorityConflict("schedule_entry_invalid") from exc
            if slot.schedule_order != expected_order or slot.run_id in run_ids:
                raise AuthorityConflict("schedule_order_or_run_id_conflict")
            if slot.condition not in {"OFF", "ON"} or slot.position_in_pair not in {0, 1}:
                raise AuthorityConflict("schedule_condition_or_position_invalid")
            run_ids.add(slot.run_id)
            slots.append(slot)
        formal_ids = _required(data, "formal_run_ids")
        authority_ids = _required(data, "authority", "formal_run_ids")
        if formal_ids != [slot.run_id for slot in slots] or authority_ids != formal_ids:
            raise AuthorityConflict("formal_run_id_authority_conflict")

        grouped: dict[int, list[ScheduleSlot]] = {}
        for slot in slots:
            grouped.setdefault(slot.pair_index, []).append(slot)
        expected_pairs = int(_required(data, "frozen_methodology", "pairs"))
        if set(grouped) != set(range(1, expected_pairs + 1)):
            raise AuthorityConflict("pair_index_sequence_invalid")
        pair_order = _required(data, "frozen_methodology", "pair_order")
        if not isinstance(pair_order, list) or len(pair_order) != expected_pairs:
            raise AuthorityConflict("pair_order_invalid")
        slots_by_pair: dict[int, tuple[ScheduleSlot, ScheduleSlot]] = {}
        for pair_index in range(1, expected_pairs + 1):
            members = sorted(grouped[pair_index], key=lambda value: value.position_in_pair)
            if len(members) != 2 or [member.position_in_pair for member in members] != [0, 1]:
                raise AuthorityConflict("pair_membership_invalid")
            order = pair_order[pair_index - 1]
            if (
                not isinstance(order, Mapping)
                or order.get("pair_index") != pair_index
                or order.get("task_id") != members[0].task_id
                or members[0].task_id != members[1].task_id
                or order.get("first_condition") != members[0].condition
                or order.get("second_condition") != members[1].condition
            ):
                raise AuthorityConflict("schedule_pair_order_conflict")
            slots_by_pair[pair_index] = (members[0], members[1])

        tasks = _required(data, "tasks")
        if not isinstance(tasks, list):
            raise AuthorityConflict("tasks_invalid")
        tasks_by_id = {
            task.get("task_id"): task
            for task in tasks
            if isinstance(task, Mapping) and isinstance(task.get("task_id"), str)
        }
        if set(tasks_by_id) != {slot.task_id for slot in slots}:
            raise AuthorityConflict("task_schedule_authority_conflict")

        assets = _required(data, "fixture_oracle_authority")
        if not isinstance(assets, list) or not all(isinstance(item, Mapping) for item in assets):
            raise AuthorityConflict("fixture_oracle_authority_invalid")
        asset_keys: set[tuple[str, str]] = set()
        for asset in assets:
            kind = asset.get("kind")
            task_id = asset.get("task_id")
            if kind not in {"fixture", "oracle"} or task_id not in tasks_by_id:
                raise AuthorityConflict("fixture_oracle_binding_invalid")
            key = (str(task_id), str(kind))
            if key in asset_keys:
                raise AuthorityConflict("duplicate_fixture_oracle_authority")
            asset_keys.add(key)
            relative = asset.get("relative_path")
            destination = asset.get("destination_path")
            if (
                not isinstance(relative, str)
                or not isinstance(destination, str)
                or Path(destination) != logical_root / relative
            ):
                raise AuthorityConflict("fixture_oracle_destination_conflict")
            task = tasks_by_id[task_id]
            expected_file = task.get(f"{kind}_file")
            expected_sha = task.get(f"{kind}_sha256")
            if expected_file != relative or expected_sha != asset.get("sha256"):
                raise AuthorityConflict("fixture_oracle_task_conflict")
        expected_asset_keys = {
            (task_id, kind) for task_id in tasks_by_id for kind in ("fixture", "oracle")
        }
        if asset_keys != expected_asset_keys:
            raise AuthorityConflict("fixture_oracle_authority_incomplete")

        ledger_schema = _required(
            data, "lease_execution_contract", "authority", "SCHEMA"
        )
        if ledger_schema != _required(
            data, "lease_execution_contract", "identity_binding", "schema_version"
        ):
            raise AuthorityConflict("ledger_schema_conflict")

        return cls(
            raw=raw,
            data=copy.deepcopy(data),
            sha256=digest,
            logical_manifest_path=logical_manifest_path,
            logical_root=logical_root,
            lease_path=lease_path,
            ledger_path=ledger_path,
            state_path=state_path,
            runs_root=runs_root,
            workspaces_root=workspaces_root,
            aggregate_path=logical_root / "aggregate.json",
            epoch_id=epoch,
            lease_task_id=lease_task,
            lease_started_at=str(_required(data, "single_writer_lease", "started_at")),
            ledger_schema=str(ledger_schema),
            total_budget=total_budget,
            pair_reservation=pair_reservation,
            invocation_cap=invocation_cap,
            pre_execution_state=str(states["pre"]),
            executing_state=str(states["executing"]),
            completed_state=str(states["completed"]),
            aborted_state=str(states["aborted"]),
            approved_states=tuple(approved),
            provider_executable=provider_executable,
            provider_version=provider_version,
            executor_head=str(_required(executor, "head")),
            executor_tree=str(_required(executor, "tree")),
            executor_source=str(_required(executor, "source")),
            executor_source_sha256=str(_required(executor, "source_sha256")),
            executor_test=str(_required(executor, "test")),
            executor_test_sha256=str(_required(executor, "test_sha256")),
            executor_repository=Path(_required(harness, "repository")),
            slots=tuple(slots),
            slots_by_run={slot.run_id: slot for slot in slots},
            slots_by_pair=slots_by_pair,
            assets=tuple(copy.deepcopy(assets)),
        )


class GitExecutorIdentityProbe:
    """Verify the pinned executor commit/tree/blobs and loaded source bytes."""

    @staticmethod
    def _git(repository: Path, *arguments: str) -> bytes:
        environment = dict(os.environ)
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=environment,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ExecutorIdentityError(
                f"git_identity_probe_failed:{' '.join(arguments)}:{detail}"
            )
        return completed.stdout

    def probe(self, authority: ManifestAuthority) -> ExecutorIdentity:
        observed_head = self._git(
            authority.executor_repository,
            "rev-parse",
            f"{authority.executor_head}^{{commit}}",
        ).decode("ascii").strip()
        observed_tree = self._git(
            authority.executor_repository,
            "rev-parse",
            f"{authority.executor_head}^{{tree}}",
        ).decode("ascii").strip()
        source = self._git(
            authority.executor_repository,
            "show",
            f"{authority.executor_head}:{authority.executor_source}",
        )
        test = self._git(
            authority.executor_repository,
            "show",
            f"{authority.executor_head}:{authority.executor_test}",
        )
        runtime_source, _ = _secure_read_regular(
            Path(__file__).with_name("claude_executor.py"), "runtime_executor_source"
        )
        return ExecutorIdentity(
            head=observed_head,
            tree=observed_tree,
            source_sha256=_sha256_bytes(source),
            test_sha256=_sha256_bytes(test),
            runtime_source_sha256=_sha256_bytes(runtime_source),
        )


class WriterLock:
    """Hold the existing immutable lease inode with nonblocking ``flock``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.descriptor: int | None = None
        self.identity: tuple[int, int] | None = None

    def acquire(self) -> tuple[int, int]:
        if self.descriptor is not None:
            raise WriterLockError("writer_lock_already_acquired")
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW)
        except OSError as exc:
            raise WriterLockError(f"writer_lock_secure_open_failed:{exc.errno}") from exc
        try:
            opened = os.fstat(descriptor)
            named = os.lstat(self.path)
            identity = (opened.st_dev, opened.st_ino)
            if identity != (named.st_dev, named.st_ino):
                raise WriterLockError("writer_lock_inode_mismatch")
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise WriterLockError("writer_lock_not_single_link_regular")
            if opened.st_uid != os.getuid() or stat.S_IMODE(opened.st_mode) != 0o600:
                raise WriterLockError("writer_lock_owner_or_mode_mismatch")
            with _PROCESS_LOCK_GUARD:
                if identity in _PROCESS_LOCKS:
                    raise WriterLockBusy("writer_lock_busy")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise WriterLockBusy("writer_lock_busy") from exc
                raise WriterLockError(f"writer_lock_flock_failed:{exc.errno}") from exc
            with _PROCESS_LOCK_GUARD:
                if identity in _PROCESS_LOCKS:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    raise WriterLockBusy("writer_lock_busy")
                _PROCESS_LOCKS.add(identity)
            os.set_inheritable(descriptor, False)
            self.descriptor = descriptor
            self.identity = identity
            return identity
        except Exception:
            os.close(descriptor)
            raise

    def release(self) -> None:
        descriptor = self.descriptor
        identity = self.identity
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            if identity is not None:
                with _PROCESS_LOCK_GUARD:
                    _PROCESS_LOCKS.discard(identity)
            self.descriptor = None
            self.identity = None


class ExecutionController:
    """Enforce the sealed reservation/accounting/recovery contract under one lock."""

    _LEDGER_KEYS = {
        "schema",
        "manifest_sha256",
        "epoch_id",
        "lease_identity",
        "budget_authority",
        "revision",
        "known_spend_usd",
        "unreleased_reservation_usd",
        "unresolved_cost_items",
        "attempts",
        "pair_checkpoints",
        "budget_exhausted_pairs",
        "history",
    }
    _ATTEMPT_KEYS = {
        "pair_index",
        "position_in_pair",
        "run_id",
        "attempt_ordinal",
        "reserved_amount",
        "unreleased_amount",
        "durable_intent",
        "evidence_paths_and_sha256",
        "result_paths_and_sha256",
        "outcome",
        "cost_status",
        "exact_cost",
        "status",
        "settlement_revision",
        "terminal_commit",
    }

    def __init__(
        self,
        *,
        manifest_path: str | os.PathLike[str],
        storage: RootMapping,
        authorization: ExecutionAuthorization,
        provider: ProviderBoundary,
        executor_probe: ExecutorIdentityProbe | None = None,
        event_sink: Callable[[str], None] | None = None,
    ) -> None:
        self.manifest_path = Path(manifest_path)
        self.storage = storage
        self.authorization = authorization
        self.provider = provider
        self.executor_probe = executor_probe or GitExecutorIdentityProbe()
        self.event_sink = event_sink
        self.authority: ManifestAuthority | None = None
        self.lock: WriterLock | None = None
        self.ledger: dict[str, Any] | None = None
        self.state: dict[str, Any] | None = None
        self.started = False

    def _emit(self, event: str) -> None:
        if self.event_sink is not None:
            self.event_sink(event)

    def _require_started(self) -> tuple[ManifestAuthority, dict[str, Any]]:
        if not self.started or self.authority is None or self.ledger is None:
            raise OrderingError("controller_not_executing")
        if self.lock is None or self.lock.descriptor is None:
            raise OrderingError("writer_lock_not_held")
        return self.authority, self.ledger

    def _physical(self, logical: Path) -> Path:
        return self.storage.physical(logical)

    def _verify_authorization(self, authority: ManifestAuthority) -> None:
        expected_source = _required(
            authority.data, "authority", "authorization_source"
        )
        supplied = self.authorization
        if supplied.manifest_sha256 != authority.sha256:
            raise AuthorityConflict("authorization_manifest_sha_mismatch")
        if supplied.epoch_id != authority.epoch_id:
            raise AuthorityConflict("authorization_epoch_mismatch")
        if supplied.authorization_source != expected_source:
            raise AuthorityConflict("authorization_source_mismatch")
        if supplied.owner_hash_signoff is not True:
            raise AuthorityConflict("direct_owner_hash_signoff_missing")
        if supplied.paid_execution_authorized is not True:
            raise AuthorityConflict("direct_paid_execution_authorization_missing")

    def _verify_lease(self, authority: ManifestAuthority) -> None:
        if self.lock is None or self.lock.identity is None:
            raise WriterLockError("lease_verified_without_held_lock")
        raw, metadata = _secure_read_regular(
            self._physical(authority.lease_path), "writer_lease"
        )
        if (metadata.st_dev, metadata.st_ino) != self.lock.identity:
            raise WriterLockError("writer_lease_inode_changed_under_lock")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise WriterLockError("writer_lease_owner_or_mode_mismatch")
        lease = _decode_json_object(raw, "writer_lease")
        expected = {
            "EPOCH_ID": authority.epoch_id,
            "TASK_ID": authority.lease_task_id,
            "STARTED_AT": authority.lease_started_at,
            "STATE": authority.pre_execution_state,
        }
        for key, value in expected.items():
            if lease.get(key) != value:
                raise AuthorityConflict(f"writer_lease_{key.lower()}_mismatch")
        if not isinstance(lease.get("HOST"), str) or not lease.get("HOST"):
            raise AuthorityConflict("writer_lease_host_invalid")
        if not isinstance(lease.get("PID"), int) or lease["PID"] <= 0:
            raise AuthorityConflict("writer_lease_pid_invalid")
        if lease.get("STATE") != _required(
            authority.data, "single_writer_lease", "state_at_judge_handoff"
        ):
            raise AuthorityConflict("writer_lease_state_authority_conflict")

    def _verify_executor(self, authority: ManifestAuthority) -> None:
        observed = self.executor_probe.probe(authority)
        expected = ExecutorIdentity(
            head=authority.executor_head,
            tree=authority.executor_tree,
            source_sha256=authority.executor_source_sha256,
            test_sha256=authority.executor_test_sha256,
            runtime_source_sha256=authority.executor_source_sha256,
        )
        if observed != expected:
            raise ExecutorIdentityError(
                f"executor_identity_mismatch:observed={observed!r}:expected={expected!r}"
            )

    def _verify_provider(self, authority: ManifestAuthority) -> None:
        if getattr(self.provider, "executable", None) != authority.provider_executable:
            raise ProviderIdentityError("provider_executable_mismatch")
        observed = self.provider.verify_version()
        if observed != authority.provider_version:
            raise ProviderIdentityError(
                f"provider_version_mismatch:{observed!r}:{authority.provider_version!r}"
            )
        self._emit("provider_version_verified_without_inference")

    def _verify_assets(self, authority: ManifestAuthority) -> None:
        expected_by_directory: dict[Path, set[str]] = {}
        for asset in authority.assets:
            logical = Path(str(asset["destination_path"]))
            physical = self._physical(logical)
            raw, metadata = _secure_read_regular(
                physical, f"{asset['kind']}_{asset['task_id']}"
            )
            if metadata.st_size != asset.get("byte_size"):
                raise AuthorityConflict(
                    f"{asset['kind']}_{asset['task_id']}_byte_size_mismatch"
                )
            if _sha256_bytes(raw) != asset.get("sha256"):
                raise AuthorityConflict(
                    f"{asset['kind']}_{asset['task_id']}_sha256_mismatch"
                )
            expected_by_directory.setdefault(physical.parent, set()).add(physical.name)
        for directory, expected_names in expected_by_directory.items():
            _ensure_directory(directory, "asset_directory")
            observed_names = set(os.listdir(directory))
            if observed_names != expected_names:
                raise AuthorityConflict("fixture_or_oracle_directory_has_extra_or_missing_entry")

    def _verify_root_inventory(
        self, authority: ManifestAuthority, *, ledger_expected: bool, state_expected: bool
    ) -> None:
        _ensure_directory(self.storage.physical_root, "mapped_r2_root")
        expected = {
            self._physical(authority.logical_manifest_path).name,
            self._physical(authority.lease_path).name,
            self._physical(authority.runs_root).name,
            self._physical(authority.workspaces_root).name,
            "fixtures",
            "oracles",
        }
        if ledger_expected:
            expected.add(self._physical(authority.ledger_path).name)
        if state_expected:
            expected.add(self._physical(authority.state_path).name)
        observed = set(os.listdir(self.storage.physical_root))
        if observed != expected:
            extras = sorted(observed - expected)
            missing = sorted(expected - observed)
            raise EntryPreconditionError(
                f"r2_root_inventory_ambiguous:extra={extras}:missing={missing}"
            )

    def _verify_first_entry_surfaces(self, authority: ManifestAuthority) -> None:
        self._verify_root_inventory(authority, ledger_expected=False, state_expected=False)
        if os.path.lexists(self._physical(authority.ledger_path)):
            raise EntryPreconditionError("execution_ledger_preexists_on_first_entry")
        if os.path.lexists(self._physical(authority.state_path)):
            raise EntryPreconditionError("execution_state_preexists_on_first_entry")
        if os.path.lexists(self._physical(authority.aggregate_path)):
            raise EntryPreconditionError("aggregate_preexists_on_first_entry")
        for logical, label in (
            (authority.runs_root, "runs"),
            (authority.workspaces_root, "workspaces"),
        ):
            physical = self._physical(logical)
            _ensure_directory(physical, label)
            if os.listdir(physical):
                raise EntryPreconditionError(f"{label}_not_empty_on_first_entry")

    def _lease_identity_record(self, authority: ManifestAuthority) -> dict[str, Any]:
        if self.lock is None or self.lock.identity is None:
            raise WriterLockError("lease_identity_requested_without_lock")
        device, inode = self.lock.identity
        return {
            "path": str(authority.lease_path),
            "device": device,
            "inode": inode,
        }

    def _initial_ledger(self, authority: ManifestAuthority) -> dict[str, Any]:
        return {
            "schema": authority.ledger_schema,
            "manifest_sha256": authority.sha256,
            "epoch_id": authority.epoch_id,
            "lease_identity": self._lease_identity_record(authority),
            "budget_authority": {
                "total_usd": decimal_text(authority.total_budget),
                "pair_reservation_usd": decimal_text(authority.pair_reservation),
                "invocation_cap_usd": decimal_text(authority.invocation_cap),
            },
            "revision": 0,
            "known_spend_usd": "0",
            "unreleased_reservation_usd": "0",
            "unresolved_cost_items": [],
            "attempts": [],
            "pair_checkpoints": [],
            "budget_exhausted_pairs": [],
            "history": [],
        }

    def _state_record(
        self, authority: ManifestAuthority, state: str, ledger_revision: int
    ) -> dict[str, Any]:
        if state not in authority.approved_states:
            raise AuthorityConflict("state_not_manifest_approved")
        return {
            "state": state,
            "manifest_sha256": authority.sha256,
            "epoch_id": authority.epoch_id,
            "lease_identity": self._lease_identity_record(authority),
            "ledger_revision": ledger_revision,
        }

    def _write_state(self, state: str) -> None:
        authority, ledger = self._require_started()
        record = self._state_record(authority, state, ledger["revision"])
        _atomic_write_json(self._physical(authority.state_path), record)
        self.state = record
        self._emit(f"state:{state}")

    def _read_canonical_record(self, logical: Path, label: str) -> dict[str, Any]:
        raw, _ = _secure_read_regular(self._physical(logical), label)
        record = _decode_json_object(raw, label)
        if raw != _canonical_json(record):
            raise AuthorityConflict(f"{label}_not_canonical_json")
        return record

    def _validate_state(
        self, authority: ManifestAuthority, state_record: Mapping[str, Any], ledger_revision: int
    ) -> None:
        if set(state_record) != {
            "state",
            "manifest_sha256",
            "epoch_id",
            "lease_identity",
            "ledger_revision",
        }:
            raise AuthorityConflict("execution_state_fields_invalid")
        if state_record.get("manifest_sha256") != authority.sha256:
            raise AuthorityConflict("execution_state_manifest_mismatch")
        if state_record.get("epoch_id") != authority.epoch_id:
            raise AuthorityConflict("execution_state_epoch_mismatch")
        if state_record.get("lease_identity") != self._lease_identity_record(authority):
            raise AuthorityConflict("execution_state_lease_mismatch")
        state = state_record.get("state")
        if state not in authority.approved_states or state == authority.pre_execution_state:
            raise AuthorityConflict("execution_state_value_invalid")
        recorded_revision = state_record.get("ledger_revision")
        if not isinstance(recorded_revision, int) or recorded_revision < 0:
            raise AuthorityConflict("execution_state_revision_invalid")
        if recorded_revision > ledger_revision:
            raise AuthorityConflict("execution_state_ahead_of_ledger")
        if state in {authority.completed_state, authority.aborted_state} and (
            recorded_revision != ledger_revision
        ):
            raise AuthorityConflict("terminal_state_revision_mismatch")

    @staticmethod
    def _ref_is_valid(reference: Any) -> bool:
        return (
            isinstance(reference, Mapping)
            and set(reference) >= {"path", "sha256"}
            and isinstance(reference.get("path"), str)
            and isinstance(reference.get("sha256"), str)
            and bool(_SHA256_RE.fullmatch(reference["sha256"]))
        )

    def _validate_ledger(self, authority: ManifestAuthority, ledger: Mapping[str, Any]) -> None:
        if set(ledger) != self._LEDGER_KEYS:
            raise LedgerError("ledger_top_level_fields_invalid")
        if ledger.get("schema") != authority.ledger_schema:
            raise LedgerError("ledger_schema_mismatch")
        if ledger.get("manifest_sha256") != authority.sha256:
            raise LedgerError("ledger_manifest_mismatch")
        if ledger.get("epoch_id") != authority.epoch_id:
            raise LedgerError("ledger_epoch_mismatch")
        if ledger.get("lease_identity") != self._lease_identity_record(authority):
            raise LedgerError("ledger_lease_identity_mismatch")
        expected_budget = {
            "total_usd": decimal_text(authority.total_budget),
            "pair_reservation_usd": decimal_text(authority.pair_reservation),
            "invocation_cap_usd": decimal_text(authority.invocation_cap),
        }
        if ledger.get("budget_authority") != expected_budget:
            raise LedgerError("ledger_budget_authority_mismatch")
        revision = ledger.get("revision")
        history = ledger.get("history")
        if not isinstance(revision, int) or revision < 0 or not isinstance(history, list):
            raise LedgerError("ledger_revision_invalid")
        if revision != len(history):
            raise LedgerError("ledger_revision_history_length_mismatch")
        for expected_revision, item in enumerate(history, start=1):
            if (
                not isinstance(item, Mapping)
                or set(item) != {"revision", "action"}
                or item.get("revision") != expected_revision
                or not isinstance(item.get("action"), str)
                or not item.get("action")
            ):
                raise LedgerError("ledger_history_not_strictly_monotonic")

        attempts = ledger.get("attempts")
        if not isinstance(attempts, list):
            raise LedgerError("ledger_attempts_not_list")
        seen_keys: set[tuple[str, int]] = set()
        known_spend = Decimal("0")
        unreleased = Decimal("0")
        unresolved_keys: list[dict[str, Any]] = []
        attempts_by_run: dict[str, list[Mapping[str, Any]]] = {}
        allowed_terminal_outcomes = set(
            _required(
                authority.data,
                "failure_denominator",
                "counted_in_denominator",
            )
        ) | {"INFRA_ERROR", "INFRA_FAIL", "ORPHANED"}
        for attempt in attempts:
            if not isinstance(attempt, Mapping) or set(attempt) != self._ATTEMPT_KEYS:
                raise LedgerError("ledger_attempt_fields_invalid")
            run_id = attempt.get("run_id")
            ordinal = attempt.get("attempt_ordinal")
            if not isinstance(run_id, str) or run_id not in authority.slots_by_run:
                raise LedgerError("ledger_attempt_run_id_invalid")
            if ordinal not in {1, 2}:
                raise LedgerError("ledger_attempt_ordinal_invalid")
            key = (run_id, ordinal)
            if key in seen_keys:
                raise LedgerError("ledger_duplicate_attempt")
            seen_keys.add(key)
            slot = authority.slots_by_run[run_id]
            if (
                attempt.get("pair_index") != slot.pair_index
                or attempt.get("position_in_pair") != slot.position_in_pair
            ):
                raise LedgerError("ledger_attempt_schedule_binding_invalid")
            reserved = _ledger_decimal(attempt.get("reserved_amount"), "reserved_amount")
            remaining = _ledger_decimal(attempt.get("unreleased_amount"), "unreleased_amount")
            if reserved != authority.invocation_cap or remaining not in {Decimal("0"), reserved}:
                raise LedgerError("ledger_attempt_reservation_invalid")
            status_value = attempt.get("status")
            if status_value not in {
                "RESERVED",
                "INTENT_PERSISTED",
                "SETTLED",
                "UNRESOLVED",
                "ORPHANED",
            }:
                raise LedgerError("ledger_attempt_status_invalid")
            intent = attempt.get("durable_intent")
            if status_value == "RESERVED":
                if intent is not None or attempt.get("outcome") is not None:
                    raise LedgerError("reserved_attempt_contains_execution_evidence")
            else:
                if (
                    not self._ref_is_valid(intent)
                    or set(intent) != {
                        "path",
                        "sha256",
                        "workspace_path",
                        "invocation_sha256",
                    }
                    or not isinstance(intent.get("workspace_path"), str)
                    or not isinstance(intent.get("invocation_sha256"), str)
                    or not _SHA256_RE.fullmatch(intent["invocation_sha256"])
                ):
                    raise LedgerError("attempt_intent_invalid")
                expected_intent_path = (
                    authority.runs_root
                    / run_id
                    / f"attempt-{ordinal}"
                    / "INVOCATION_INTENT.json"
                )
                if Path(intent["path"]) != expected_intent_path:
                    raise LedgerError("attempt_intent_path_invalid")
                workspace_path = Path(intent["workspace_path"])
                try:
                    workspace_relative = workspace_path.relative_to(
                        authority.workspaces_root
                    )
                except ValueError as exc:
                    raise LedgerError("attempt_workspace_path_invalid") from exc
                if (
                    len(workspace_relative.parts) != 1
                    or not _SESSION_RE.fullmatch(workspace_relative.name)
                ):
                    raise LedgerError("attempt_workspace_identity_invalid")
            cost_status = attempt.get("cost_status")
            exact_cost = attempt.get("exact_cost")
            outcome = attempt.get("outcome")
            if status_value not in {"RESERVED", "INTENT_PERSISTED"} and (
                outcome not in allowed_terminal_outcomes
            ):
                raise LedgerError("attempt_outcome_not_manifest_authorized")
            if status_value in {"RESERVED", "INTENT_PERSISTED"}:
                if (
                    cost_status != "PENDING"
                    or exact_cost is not None
                    or outcome is not None
                    or remaining != reserved
                ):
                    raise LedgerError("pending_attempt_cost_state_invalid")
            elif cost_status == "KNOWN":
                if status_value not in {"SETTLED", "ORPHANED"}:
                    raise LedgerError("known_cost_requires_settled_or_orphaned_status")
                cost = _ledger_decimal(exact_cost, "exact_cost")
                if cost > reserved or remaining != 0:
                    raise LedgerError("known_cost_settlement_invalid")
                if not isinstance(outcome, str) or not outcome:
                    raise LedgerError("settled_attempt_outcome_invalid")
                known_spend += cost
            elif cost_status == "UNRESOLVED":
                if status_value not in {"UNRESOLVED", "ORPHANED"}:
                    raise LedgerError("unresolved_cost_status_binding_invalid")
                if exact_cost is not None or remaining != reserved:
                    raise LedgerError("unresolved_cost_must_retain_full_reservation")
                unresolved_keys.append(
                    {"run_id": run_id, "attempt_ordinal": ordinal}
                )
            else:
                raise LedgerError("terminal_attempt_cost_status_invalid")
            if status_value == "ORPHANED" and outcome != "ORPHANED":
                raise LedgerError("orphaned_attempt_outcome_invalid")
            evidence_refs = attempt.get("evidence_paths_and_sha256")
            result_refs = attempt.get("result_paths_and_sha256")
            if not isinstance(evidence_refs, list) or not isinstance(result_refs, list):
                raise LedgerError("attempt_evidence_lists_invalid")
            if not all(self._ref_is_valid(item) for item in evidence_refs + result_refs):
                raise LedgerError("attempt_evidence_reference_invalid")
            expected_attempt_root = (
                authority.runs_root / run_id / f"attempt-{ordinal}"
            )
            if status_value in {"SETTLED", "UNRESOLVED"}:
                if [Path(item["path"]) for item in evidence_refs] != [
                    expected_attempt_root / "provider-raw.bin",
                    expected_attempt_root / "canonical-evidence.json",
                ] or [Path(item["path"]) for item in result_refs] != [
                    expected_attempt_root / "result.json"
                ]:
                    raise LedgerError("attempt_evidence_paths_invalid")
            elif status_value == "ORPHANED":
                evidence_paths = [Path(item["path"]) for item in evidence_refs]
                result_paths = [Path(item["path"]) for item in result_refs]
                if (
                    len(set(evidence_paths + result_paths))
                    != len(evidence_paths) + len(result_paths)
                    or any(path.parent != expected_attempt_root for path in evidence_paths)
                    or any(
                        path.name in {"INVOCATION_INTENT.json", "result.json"}
                        for path in evidence_paths
                    )
                    or result_paths not in ([], [expected_attempt_root / "result.json"])
                ):
                    raise LedgerError("orphaned_attempt_evidence_paths_invalid")
                if cost_status == "KNOWN" and (
                    evidence_paths
                    != [
                        expected_attempt_root / "provider-raw.bin",
                        expected_attempt_root / "canonical-evidence.json",
                    ]
                    or result_paths != [expected_attempt_root / "result.json"]
                ):
                    raise LedgerError("known_orphan_requires_complete_provider_evidence")
            elif evidence_refs or result_refs:
                raise LedgerError("nonsettled_attempt_contains_terminal_evidence")
            settlement_revision = attempt.get("settlement_revision")
            if status_value in {"SETTLED", "UNRESOLVED"}:
                if (
                    not isinstance(settlement_revision, int)
                    or settlement_revision <= 0
                    or settlement_revision > revision
                    or not evidence_refs
                    or not result_refs
                ):
                    raise LedgerError("attempt_settlement_revision_or_evidence_invalid")
            elif status_value == "ORPHANED" and (evidence_refs or result_refs):
                if (
                    not isinstance(settlement_revision, int)
                    or settlement_revision <= 0
                    or settlement_revision > revision
                ):
                    raise LedgerError("orphaned_attempt_evidence_revision_invalid")
            elif settlement_revision is not None:
                raise LedgerError("nonsettled_attempt_has_settlement_revision")
            terminal = attempt.get("terminal_commit")
            if terminal is not None and (
                status_value != "SETTLED"
                or not self._ref_is_valid(terminal)
                or set(terminal) != {"path", "sha256", "settlement_revision"}
                or Path(terminal["path"]) != expected_attempt_root / "TERMINAL_COMMIT.json"
            ):
                raise LedgerError("terminal_commit_binding_invalid")
            if terminal is not None and terminal.get("settlement_revision") != settlement_revision:
                raise LedgerError("terminal_commit_revision_mismatch")
            unreleased += remaining
            attempts_by_run.setdefault(run_id, []).append(attempt)

        for run_id, run_attempts in attempts_by_run.items():
            ordered = sorted(run_attempts, key=lambda item: item["attempt_ordinal"])
            if [item["attempt_ordinal"] for item in ordered] not in ([1], [1, 2]):
                raise LedgerError("attempt_ordinal_sequence_invalid")
            if len(ordered) == 2:
                first = ordered[0]
                if (
                    first.get("status") != "SETTLED"
                    or first.get("outcome") != "INFRA_ERROR"
                    or first.get("cost_status") != "KNOWN"
                    or first.get("terminal_commit") is None
                ):
                    raise LedgerError("retry_without_terminal_infra_error_authority")
                if ordered[1].get("outcome") == "INFRA_ERROR":
                    raise LedgerError("second_infra_error_not_normalized_to_infra_fail")

        attempts_by_pair: dict[int, set[str]] = {}
        for attempt in attempts:
            if attempt["attempt_ordinal"] == 1:
                attempts_by_pair.setdefault(attempt["pair_index"], set()).add(
                    attempt["run_id"]
                )
        for pair_index, member_run_ids in attempts_by_pair.items():
            expected_run_ids = {
                member.run_id for member in authority.slots_by_pair[pair_index]
            }
            if member_run_ids != expected_run_ids:
                raise LedgerError("pair_reservation_not_atomic_for_two_members")

        if _ledger_decimal(ledger.get("known_spend_usd"), "known_spend_usd") != known_spend:
            raise LedgerError("ledger_known_spend_projection_mismatch")
        if (
            _ledger_decimal(
                ledger.get("unreleased_reservation_usd"),
                "unreleased_reservation_usd",
            )
            != unreleased
        ):
            raise LedgerError("ledger_unreleased_projection_mismatch")
        if ledger.get("unresolved_cost_items") != unresolved_keys:
            raise LedgerError("ledger_unresolved_cost_projection_mismatch")
        if known_spend + unreleased > authority.total_budget:
            raise LedgerError("ledger_lifetime_cap_exceeded")

        checkpoints = ledger.get("pair_checkpoints")
        if not isinstance(checkpoints, list):
            raise LedgerError("pair_checkpoints_not_list")
        for expected_pair, checkpoint in enumerate(checkpoints, start=1):
            if not isinstance(checkpoint, Mapping) or set(checkpoint) != {
                "pair_index",
                "exact_two_members",
                "terminal_commit_references",
                "completion_revision",
            }:
                raise LedgerError("pair_checkpoint_fields_invalid")
            if checkpoint.get("pair_index") != expected_pair:
                raise LedgerError("pair_checkpoint_order_invalid")
            completion_revision = checkpoint.get("completion_revision")
            if (
                not isinstance(completion_revision, int)
                or completion_revision <= 0
                or completion_revision > revision
                or history[completion_revision - 1].get("action") != "pair_checkpoint"
            ):
                raise LedgerError("pair_checkpoint_revision_invalid")
            expected_members = [
                {"run_id": slot.run_id, "position_in_pair": slot.position_in_pair}
                for slot in authority.slots_by_pair[expected_pair]
            ]
            if checkpoint.get("exact_two_members") != expected_members:
                raise LedgerError("pair_checkpoint_members_invalid")
            references = checkpoint.get("terminal_commit_references")
            if (
                not isinstance(references, list)
                or len(references) != 2
                or not all(self._ref_is_valid(item) for item in references)
            ):
                raise LedgerError("pair_checkpoint_terminal_references_invalid")
            expected_references: list[Mapping[str, Any]] = []
            for member in authority.slots_by_pair[expected_pair]:
                run_attempts = sorted(
                    attempts_by_run.get(member.run_id, []),
                    key=lambda item: item["attempt_ordinal"],
                )
                if not run_attempts:
                    raise LedgerError("pair_checkpoint_member_attempt_missing")
                effective = run_attempts[-1] if run_attempts[0]["outcome"] == "INFRA_ERROR" else run_attempts[0]
                if effective.get("status") != "SETTLED" or effective.get("terminal_commit") is None:
                    raise LedgerError("pair_checkpoint_member_not_terminal")
                expected_references.append(effective["terminal_commit"])
            if references != expected_references:
                raise LedgerError("pair_checkpoint_terminal_binding_mismatch")

        latest_permitted_pair = len(checkpoints) + 1
        if any(attempt["pair_index"] > latest_permitted_pair for attempt in attempts):
            raise LedgerError("attempt_exists_beyond_next_uncheckpointed_pair")

        exhausted = ledger.get("budget_exhausted_pairs")
        if not isinstance(exhausted, list) or not all(isinstance(item, int) for item in exhausted):
            raise LedgerError("budget_exhausted_pairs_invalid")
        if exhausted:
            if len(exhausted) != 1 or exhausted[0] != len(checkpoints) + 1:
                raise LedgerError("budget_exhausted_pair_not_next_pair")

    def _read_ledger(self, authority: ManifestAuthority) -> dict[str, Any]:
        record = self._read_canonical_record(authority.ledger_path, "execution_ledger")
        self._validate_ledger(authority, record)
        return record

    def _recompute_ledger_projections(self, ledger: dict[str, Any]) -> None:
        known = Decimal("0")
        unreleased = Decimal("0")
        unresolved: list[dict[str, Any]] = []
        for attempt in ledger["attempts"]:
            unreleased += _ledger_decimal(
                attempt["unreleased_amount"], "unreleased_amount"
            )
            if attempt["cost_status"] == "KNOWN":
                known += _ledger_decimal(attempt["exact_cost"], "exact_cost")
            elif attempt["cost_status"] == "UNRESOLVED":
                unresolved.append(
                    {
                        "run_id": attempt["run_id"],
                        "attempt_ordinal": attempt["attempt_ordinal"],
                    }
                )
        ledger["known_spend_usd"] = decimal_text(known)
        ledger["unreleased_reservation_usd"] = decimal_text(unreleased)
        ledger["unresolved_cost_items"] = unresolved

    def _update_ledger(
        self,
        action: str,
        mutate: Callable[[dict[str, Any], int], None],
    ) -> dict[str, Any]:
        authority, current = self._require_started()
        candidate = copy.deepcopy(current)
        next_revision = current["revision"] + 1
        mutate(candidate, next_revision)
        candidate["revision"] = next_revision
        candidate["history"].append(
            {"revision": next_revision, "action": action}
        )
        self._recompute_ledger_projections(candidate)
        self._validate_ledger(authority, candidate)
        _atomic_write_json(self._physical(authority.ledger_path), candidate)
        self.ledger = candidate
        self._emit(f"ledger:{action}")
        return candidate

    def _verify_artifact_ref(self, reference: Mapping[str, Any], label: str) -> bytes:
        if not self._ref_is_valid(reference):
            raise AuthorityConflict(f"{label}_reference_invalid")
        logical = Path(reference["path"])
        raw, _ = _secure_read_regular(self._physical(logical), label)
        if _sha256_bytes(raw) != reference["sha256"]:
            raise AuthorityConflict(f"{label}_sha256_mismatch")
        return raw

    def _attempts_for_run(self, run_id: str) -> list[dict[str, Any]]:
        _, ledger = self._require_started()
        return sorted(
            [item for item in ledger["attempts"] if item["run_id"] == run_id],
            key=lambda item: item["attempt_ordinal"],
        )

    def _find_attempt(self, key: AttemptKey) -> dict[str, Any]:
        matches = [
            item
            for item in self._attempts_for_run(key.run_id)
            if item["attempt_ordinal"] == key.attempt_ordinal
        ]
        if len(matches) != 1:
            raise OrderingError("attempt_not_uniquely_reserved")
        return matches[0]

    def _terminal_attempt_for_slot(self, run_id: str) -> dict[str, Any] | None:
        attempts = self._attempts_for_run(run_id)
        if not attempts:
            return None
        first = attempts[0]
        if first["status"] != "SETTLED" or first["terminal_commit"] is None:
            return None
        if first["outcome"] == "INFRA_ERROR":
            if len(attempts) != 2:
                return None
            second = attempts[1]
            if second["status"] != "SETTLED" or second["terminal_commit"] is None:
                return None
            return second
        return first

    def _marker_record(
        self, authority: ManifestAuthority, attempt: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "manifest_sha256": authority.sha256,
            "epoch_id": authority.epoch_id,
            "run_id": attempt["run_id"],
            "attempt_ordinal": attempt["attempt_ordinal"],
            "outcome": attempt["outcome"],
            "cost_status": attempt["cost_status"],
            "exact_cost": attempt["exact_cost"],
            "evidence_paths_and_sha256": attempt["evidence_paths_and_sha256"],
            "result_paths_and_sha256": attempt["result_paths_and_sha256"],
            "settlement_revision": attempt["settlement_revision"],
        }

    def _terminal_marker_path(self, authority: ManifestAuthority, attempt: Mapping[str, Any]) -> Path:
        return (
            authority.runs_root
            / attempt["run_id"]
            / f"attempt-{attempt['attempt_ordinal']}"
            / "TERMINAL_COMMIT.json"
        )

    def _bind_terminal_marker(
        self, key: AttemptKey, *, recovery: bool
    ) -> None:
        authority, _ = self._require_started()
        attempt = self._find_attempt(key)
        if attempt["status"] != "SETTLED" or attempt["cost_status"] != "KNOWN":
            raise OrderingError("terminal_marker_requires_known_settlement")
        for index, reference in enumerate(
            attempt["evidence_paths_and_sha256"] + attempt["result_paths_and_sha256"]
        ):
            self._verify_artifact_ref(reference, f"terminal_evidence_{index}")
        logical_marker = self._terminal_marker_path(authority, attempt)
        physical_marker = self._physical(logical_marker)
        expected_raw = _canonical_json(self._marker_record(authority, attempt))
        if os.path.lexists(physical_marker):
            observed, _ = _secure_read_regular(physical_marker, "terminal_marker")
            if observed != expected_raw:
                raise AuthorityConflict("terminal_marker_conflict")
        else:
            _durable_create_bytes(physical_marker, expected_raw)
            self._emit("terminal_marker_persisted")
        reference = {
            "path": str(logical_marker),
            "sha256": _sha256_bytes(expected_raw),
            "settlement_revision": attempt["settlement_revision"],
        }
        if attempt["terminal_commit"] is not None:
            if attempt["terminal_commit"] != reference:
                raise LedgerError("terminal_marker_ledger_binding_conflict")
            return

        def bind(candidate: dict[str, Any], _: int) -> None:
            matches = [
                item
                for item in candidate["attempts"]
                if item["run_id"] == key.run_id
                and item["attempt_ordinal"] == key.attempt_ordinal
            ]
            if len(matches) != 1 or matches[0]["terminal_commit"] is not None:
                raise LedgerError("terminal_marker_bind_target_conflict")
            matches[0]["terminal_commit"] = reference

        self._update_ledger(
            "terminal_commit_reconciled" if recovery else "terminal_commit_bound",
            bind,
        )

    def _read_crash_partial_intent(
        self, authority: ManifestAuthority, attempt: Mapping[str, Any]
    ) -> dict[str, Any]:
        logical_attempt = (
            authority.runs_root
            / attempt["run_id"]
            / f"attempt-{attempt['attempt_ordinal']}"
        )
        physical_run = self._physical(authority.runs_root / attempt["run_id"])
        physical_attempt = self._physical(logical_attempt)
        _ensure_directory(physical_run, "crash_partial_run_directory")
        _ensure_directory(physical_attempt, "crash_partial_attempt_directory")
        logical_intent = logical_attempt / "INVOCATION_INTENT.json"
        raw, _ = _secure_read_regular(
            self._physical(logical_intent), "crash_partial_invocation_intent"
        )
        record = _decode_json_object(raw, "crash_partial_invocation_intent")
        if raw != _canonical_json(record):
            raise AuthorityConflict("crash_partial_invocation_intent_not_canonical")
        if set(record) != {
            "manifest_sha256",
            "epoch_id",
            "pair_index",
            "run_id",
            "attempt_ordinal",
            "workspace_path",
            "invocation_sha256",
        }:
            raise AuthorityConflict("crash_partial_invocation_intent_fields_invalid")
        if (
            type(record.get("pair_index")) is not int
            or type(record.get("attempt_ordinal")) is not int
            or not isinstance(record.get("workspace_path"), str)
            or not isinstance(record.get("invocation_sha256"), str)
            or not _SHA256_RE.fullmatch(record["invocation_sha256"])
        ):
            raise AuthorityConflict("crash_partial_invocation_intent_types_invalid")
        workspace_path = Path(record["workspace_path"])
        try:
            workspace_relative = workspace_path.relative_to(authority.workspaces_root)
        except ValueError as exc:
            raise AuthorityConflict("crash_partial_workspace_outside_authority") from exc
        if (
            len(workspace_relative.parts) != 1
            or not _SESSION_RE.fullmatch(workspace_relative.name)
        ):
            raise AuthorityConflict("crash_partial_workspace_identity_invalid")
        _ensure_directory(
            self._physical(workspace_path), "crash_partial_intent_workspace"
        )
        expected = {
            "manifest_sha256": authority.sha256,
            "epoch_id": authority.epoch_id,
            "pair_index": attempt["pair_index"],
            "run_id": attempt["run_id"],
            "attempt_ordinal": attempt["attempt_ordinal"],
            "workspace_path": str(workspace_path),
            "invocation_sha256": record["invocation_sha256"],
        }
        if record != expected:
            raise AuthorityConflict("crash_partial_invocation_intent_semantic_mismatch")
        reference = {
            "path": str(logical_intent),
            "sha256": _sha256_bytes(raw),
            "workspace_path": str(workspace_path),
            "invocation_sha256": record["invocation_sha256"],
        }
        bound = attempt.get("durable_intent")
        if bound is not None and bound != reference:
            raise AuthorityConflict("crash_partial_invocation_intent_binding_mismatch")
        return reference

    def _recoverable_crash_partial_cost(
        self,
        authority: ManifestAuthority,
        attempt: Mapping[str, Any],
        raw_by_name: Mapping[str, bytes],
    ) -> str | None:
        if set(raw_by_name) != {
            "INVOCATION_INTENT.json",
            "provider-raw.bin",
            "canonical-evidence.json",
            "result.json",
        }:
            return None
        try:
            canonical_record = _decode_json_object(
                raw_by_name["canonical-evidence.json"],
                "crash_partial_canonical_evidence",
            )
            result_record = _decode_json_object(
                raw_by_name["result.json"], "crash_partial_result"
            )
        except AuthorityConflict:
            return None
        if (
            raw_by_name["canonical-evidence.json"]
            != _canonical_json(canonical_record)
            or raw_by_name["result.json"] != _canonical_json(result_record)
            or set(result_record)
            != {"provider_result", "outcome", "cost_status", "exact_cost"}
            or not isinstance(result_record.get("provider_result"), Mapping)
            or result_record.get("cost_status") != "KNOWN"
            or not isinstance(result_record.get("exact_cost"), str)
        ):
            return None
        allowed_outcomes = set(
            _required(
                authority.data,
                "failure_denominator",
                "counted_in_denominator",
            )
        ) | {"INFRA_ERROR", "INFRA_FAIL"}
        outcome = result_record.get("outcome")
        if not isinstance(outcome, str) or outcome not in allowed_outcomes or (
            attempt["attempt_ordinal"] == 2 and outcome == "INFRA_ERROR"
        ):
            return None
        try:
            exact_cost = _ledger_decimal(result_record["exact_cost"], "exact_cost")
        except LedgerError:
            return None
        if exact_cost > authority.invocation_cap:
            return None
        return decimal_text(exact_cost)

    def _collect_crash_partial_attempt(
        self, authority: ManifestAuthority, attempt: Mapping[str, Any]
    ) -> dict[str, Any]:
        logical_attempt = (
            authority.runs_root
            / attempt["run_id"]
            / f"attempt-{attempt['attempt_ordinal']}"
        )
        physical_attempt = self._physical(logical_attempt)
        intent_reference = self._read_crash_partial_intent(authority, attempt)
        names = sorted(os.listdir(physical_attempt))
        raw_by_name: dict[str, bytes] = {}
        references_by_name: dict[str, dict[str, str]] = {}
        for name in names:
            raw, _ = _secure_read_regular(
                physical_attempt / name, f"crash_partial_artifact_{name}"
            )
            raw_by_name[name] = raw
            references_by_name[name] = {
                "path": str(logical_attempt / name),
                "sha256": _sha256_bytes(raw),
            }
        if references_by_name.get("INVOCATION_INTENT.json") != {
            "path": intent_reference["path"],
            "sha256": intent_reference["sha256"],
        }:
            raise AuthorityConflict("crash_partial_intent_inventory_mismatch")
        ordered_evidence_names = [
            name
            for name in ("provider-raw.bin", "canonical-evidence.json")
            if name in references_by_name
        ] + [
            name
            for name in names
            if name
            not in {
                "INVOCATION_INTENT.json",
                "provider-raw.bin",
                "canonical-evidence.json",
                "result.json",
            }
        ]
        evidence_refs = [
            references_by_name[name] for name in ordered_evidence_names
        ]
        result_refs = (
            [references_by_name["result.json"]]
            if "result.json" in references_by_name
            else []
        )
        return {
            "intent": intent_reference,
            "evidence": evidence_refs,
            "results": result_refs,
            "exact_cost": self._recoverable_crash_partial_cost(
                authority, attempt, raw_by_name
            ),
        }

    def _reconcile_crash_partial_invocations(
        self, authority: ManifestAuthority
    ) -> None:
        _, ledger = self._require_started()
        recoveries: dict[tuple[str, int], dict[str, Any]] = {}
        original_statuses: dict[tuple[str, int], str] = {}
        for attempt in ledger["attempts"]:
            if attempt["status"] not in {"RESERVED", "INTENT_PERSISTED"}:
                continue
            logical_intent = (
                authority.runs_root
                / attempt["run_id"]
                / f"attempt-{attempt['attempt_ordinal']}"
                / "INVOCATION_INTENT.json"
            )
            if attempt["status"] == "RESERVED" and not os.path.lexists(
                self._physical(logical_intent)
            ):
                continue
            key = (attempt["run_id"], attempt["attempt_ordinal"])
            recoveries[key] = self._collect_crash_partial_attempt(
                authority, attempt
            )
            original_statuses[key] = attempt["status"]
        if not recoveries:
            return

        def orphan(candidate: dict[str, Any], next_revision: int) -> None:
            seen: set[tuple[str, int]] = set()
            for target in candidate["attempts"]:
                key = (target["run_id"], target["attempt_ordinal"])
                recovery = recoveries.get(key)
                if recovery is None:
                    continue
                if target["status"] != original_statuses[key]:
                    raise LedgerError("crash_partial_reconciliation_target_changed")
                target["durable_intent"] = copy.deepcopy(recovery["intent"])
                target["evidence_paths_and_sha256"] = copy.deepcopy(
                    recovery["evidence"]
                )
                target["result_paths_and_sha256"] = copy.deepcopy(
                    recovery["results"]
                )
                target["outcome"] = "ORPHANED"
                target["status"] = "ORPHANED"
                target["terminal_commit"] = None
                if recovery["evidence"] or recovery["results"]:
                    target["settlement_revision"] = next_revision
                else:
                    target["settlement_revision"] = None
                if recovery["exact_cost"] is None:
                    target["cost_status"] = "UNRESOLVED"
                    target["exact_cost"] = None
                    target["unreleased_amount"] = target["reserved_amount"]
                else:
                    target["cost_status"] = "KNOWN"
                    target["exact_cost"] = recovery["exact_cost"]
                    target["unreleased_amount"] = "0"
                seen.add(key)
            if seen != set(recoveries):
                raise LedgerError("crash_partial_reconciliation_target_missing")

        self._update_ledger("restart_crash_partial_orphaned", orphan)
        self._write_state(authority.aborted_state)
        self.started = False
        if self.lock is not None:
            self.lock.release()
        raise RecoveryAborted("CRASH_CASE_3:crash_partial_invocation_abort_only")

    def _verify_restart_artifacts(self, authority: ManifestAuthority) -> None:
        _, ledger = self._require_started()
        allowed_files: set[Path] = set()
        allowed_attempt_directories: set[Path] = set()
        allowed_run_directories: set[Path] = set()
        allowed_workspaces: set[Path] = set()
        for attempt in ledger["attempts"]:
            intent = attempt["durable_intent"]
            if intent is not None:
                intent_raw = self._verify_artifact_ref(intent, "invocation_intent")
                expected_intent = {
                    "manifest_sha256": authority.sha256,
                    "epoch_id": authority.epoch_id,
                    "pair_index": attempt["pair_index"],
                    "run_id": attempt["run_id"],
                    "attempt_ordinal": attempt["attempt_ordinal"],
                    "workspace_path": intent["workspace_path"],
                    "invocation_sha256": intent["invocation_sha256"],
                }
                if intent_raw != _canonical_json(expected_intent):
                    raise AuthorityConflict("invocation_intent_semantic_mismatch")
                allowed_files.add(self._physical(Path(intent["path"])).resolve(strict=False))
                workspace = self._physical(Path(intent["workspace_path"]))
                _ensure_directory(workspace, "intent_workspace")
                allowed_workspaces.add(workspace.resolve(strict=False))
            for index, reference in enumerate(
                attempt["evidence_paths_and_sha256"]
                + attempt["result_paths_and_sha256"]
            ):
                self._verify_artifact_ref(reference, f"restart_evidence_{index}")
                allowed_files.add(self._physical(Path(reference["path"])).resolve(strict=False))
            terminal = attempt["terminal_commit"]
            if terminal is not None:
                terminal_raw = self._verify_artifact_ref(
                    terminal, "restart_terminal_commit"
                )
                if terminal_raw != _canonical_json(
                    self._marker_record(authority, attempt)
                ):
                    raise AuthorityConflict("terminal_commit_semantic_mismatch")
                allowed_files.add(self._physical(Path(terminal["path"])).resolve(strict=False))
            if intent is not None or attempt["evidence_paths_and_sha256"] or terminal is not None:
                attempt_directory = self._physical(
                    authority.runs_root
                    / attempt["run_id"]
                    / f"attempt-{attempt['attempt_ordinal']}"
                ).resolve(strict=False)
                allowed_attempt_directories.add(attempt_directory)
                allowed_run_directories.add(attempt_directory.parent)

        physical_runs = self._physical(authority.runs_root)
        for current_root, directories, filenames in os.walk(physical_runs, followlinks=False):
            current = Path(current_root).resolve(strict=False)
            for name in directories:
                child = (current / name).resolve(strict=False)
                metadata = os.lstat(child)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise AuthorityConflict("runs_contains_non_directory_or_symlink")
                if child not in allowed_run_directories | allowed_attempt_directories:
                    raise AuthorityConflict("runs_contains_unbound_directory")
            for name in filenames:
                child = (current / name).resolve(strict=False)
                if child not in allowed_files:
                    raise AuthorityConflict("runs_contains_unbound_file")

        physical_workspaces = self._physical(authority.workspaces_root)
        observed_workspaces: set[Path] = set()
        for name in os.listdir(physical_workspaces):
            child = physical_workspaces / name
            metadata = os.lstat(child)
            if not stat.S_ISDIR(metadata.st_mode):
                raise AuthorityConflict("workspaces_contains_non_directory_or_symlink")
            observed_workspaces.add(child.resolve(strict=False))
        if observed_workspaces != allowed_workspaces:
            raise AuthorityConflict("workspaces_contains_unbound_preparation")

    def _persist_pair_checkpoint(self, pair_index: int, *, recovery: bool) -> None:
        authority, ledger = self._require_started()
        if pair_index != len(ledger["pair_checkpoints"]) + 1:
            raise OrderingError("pair_checkpoint_not_next")
        members = authority.slots_by_pair.get(pair_index)
        if members is None:
            raise OrderingError("pair_checkpoint_out_of_schedule")
        terminals: list[dict[str, Any]] = []
        for member in members:
            terminal_attempt = self._terminal_attempt_for_slot(member.run_id)
            if terminal_attempt is None:
                raise OrderingError("pair_checkpoint_before_both_members_terminal")
            terminals.append(copy.deepcopy(terminal_attempt["terminal_commit"]))

        def checkpoint(candidate: dict[str, Any], next_revision: int) -> None:
            candidate["pair_checkpoints"].append(
                {
                    "pair_index": pair_index,
                    "exact_two_members": [
                        {
                            "run_id": member.run_id,
                            "position_in_pair": member.position_in_pair,
                        }
                        for member in members
                    ],
                    "terminal_commit_references": terminals,
                    "completion_revision": next_revision,
                }
            )

        self._update_ledger("pair_checkpoint", checkpoint)
        self._emit(
            "pair_checkpoint_reconciled" if recovery else "pair_checkpoint_persisted"
        )

    def _reconcile_restart(self, authority: ManifestAuthority) -> str:
        _, ledger = self._require_started()
        self._reconcile_crash_partial_invocations(authority)
        self._verify_restart_artifacts(authority)
        if any(
            attempt["status"] == "ORPHANED"
            or attempt["cost_status"] == "UNRESOLVED"
            for attempt in ledger["attempts"]
        ):
            self._write_state(authority.aborted_state)
            self.started = False
            if self.lock is not None:
                self.lock.release()
            raise RecoveryAborted("restart_unresolved_cost_abort_only")

        for attempt in list(ledger["attempts"]):
            if attempt["status"] == "SETTLED" and attempt["terminal_commit"] is None:
                self._bind_terminal_marker(
                    AttemptKey(attempt["run_id"], attempt["attempt_ordinal"]),
                    recovery=True,
                )
                ledger = self.ledger or ledger

        while len(ledger["pair_checkpoints"]) < len(authority.slots_by_pair):
            next_pair = len(ledger["pair_checkpoints"]) + 1
            if all(
                self._terminal_attempt_for_slot(member.run_id) is not None
                for member in authority.slots_by_pair[next_pair]
            ):
                self._persist_pair_checkpoint(next_pair, recovery=True)
                ledger = self.ledger or ledger
            else:
                break

        self._verify_restart_artifacts(authority)
        attempts = ledger["attempts"]
        if not attempts or not any(
            attempt["durable_intent"] is not None for attempt in attempts
        ):
            return "CRASH_CASE_2"
        current_pair = len(ledger["pair_checkpoints"]) + 1
        if current_pair in authority.slots_by_pair:
            first, second = authority.slots_by_pair[current_pair]
            if (
                self._terminal_attempt_for_slot(first.run_id) is not None
                and self._terminal_attempt_for_slot(second.run_id) is None
            ):
                return "CRASH_CASE_4"
        if ledger["pair_checkpoints"] and not any(
            attempt["pair_index"] > len(ledger["pair_checkpoints"])
            for attempt in attempts
        ):
            return "CRASH_CASE_5"
        return "RESTART_RECONCILED"

    def start(self) -> StartupResult:
        if self.started:
            raise OrderingError("controller_already_started")
        prelock = ManifestAuthority.load(self.manifest_path, self.storage)
        physical_lock = self._physical(prelock.lease_path)
        lock = WriterLock(physical_lock)
        self.lock = lock
        lock.acquire()
        try:
            authority = ManifestAuthority.load(self.manifest_path, self.storage)
            if authority.raw != prelock.raw:
                raise ManifestIdentityError("manifest_changed_during_lock_acquisition")
            self.authority = authority
            self._verify_authorization(authority)
            self._verify_lease(authority)
            self._verify_executor(authority)
            self._verify_provider(authority)
            self._verify_assets(authority)

            ledger_exists = os.path.lexists(self._physical(authority.ledger_path))
            state_exists = os.path.lexists(self._physical(authority.state_path))
            if not ledger_exists and not state_exists:
                self._verify_first_entry_surfaces(authority)
                ledger = self._initial_ledger(authority)
                self._validate_ledger(authority, ledger)
                _atomic_write_json(self._physical(authority.ledger_path), ledger)
                self.ledger = ledger
                self.started = True
                self._emit("ledger:initialized")
                self._write_state(authority.executing_state)
                return StartupResult(
                    first_entry=True,
                    reconciliation="FIRST_ENTRY",
                    state=authority.executing_state,
                    ledger_revision=0,
                )
            if ledger_exists and not state_exists:
                self._verify_root_inventory(
                    authority, ledger_expected=True, state_expected=False
                )
                ledger = self._read_ledger(authority)
                if ledger["revision"] != 0 or ledger["attempts"]:
                    raise AuthorityConflict("state_missing_with_nonpristine_ledger")
                if os.listdir(self._physical(authority.runs_root)) or os.listdir(
                    self._physical(authority.workspaces_root)
                ):
                    raise AuthorityConflict("CRASH_CASE_1_artifacts_not_empty")
                self.ledger = ledger
                self.started = True
                self._write_state(authority.executing_state)
                return StartupResult(
                    first_entry=False,
                    reconciliation="CRASH_CASE_1",
                    state=authority.executing_state,
                    ledger_revision=ledger["revision"],
                )
            if state_exists and not ledger_exists:
                raise AuthorityConflict("execution_state_exists_without_ledger")

            self._verify_root_inventory(
                authority, ledger_expected=True, state_expected=True
            )
            ledger = self._read_ledger(authority)
            state_record = self._read_canonical_record(
                authority.state_path, "execution_state"
            )
            self._validate_state(authority, state_record, ledger["revision"])
            self.ledger = ledger
            self.state = state_record
            self.started = True
            if state_record["state"] in {
                authority.completed_state,
                authority.aborted_state,
            }:
                raise TerminalStateError(
                    f"terminal_state_has_no_outgoing_transition:{state_record['state']}"
                )
            if state_record["state"] != authority.executing_state:
                raise AuthorityConflict("restart_state_not_executing")
            reconciliation = self._reconcile_restart(authority)
            return StartupResult(
                first_entry=False,
                reconciliation=reconciliation,
                state=authority.executing_state,
                ledger_revision=(self.ledger or ledger)["revision"],
            )
        except Exception:
            if self.lock is not None:
                self.lock.release()
            self.started = False
            raise

    def close(self) -> None:
        if self.lock is not None:
            self.lock.release()
        self.started = False

    def __enter__(self) -> "ExecutionController":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _new_attempt(self, slot: ScheduleSlot, ordinal: int) -> dict[str, Any]:
        authority, _ = self._require_started()
        return {
            "pair_index": slot.pair_index,
            "position_in_pair": slot.position_in_pair,
            "run_id": slot.run_id,
            "attempt_ordinal": ordinal,
            "reserved_amount": decimal_text(authority.invocation_cap),
            "unreleased_amount": decimal_text(authority.invocation_cap),
            "durable_intent": None,
            "evidence_paths_and_sha256": [],
            "result_paths_and_sha256": [],
            "outcome": None,
            "cost_status": "PENDING",
            "exact_cost": None,
            "status": "RESERVED",
            "settlement_revision": None,
            "terminal_commit": None,
        }

    def reserve_pair(self, pair_index: int) -> tuple[AttemptKey, AttemptKey]:
        authority, ledger = self._require_started()
        expected_pair = len(ledger["pair_checkpoints"]) + 1
        if pair_index != expected_pair or pair_index not in authority.slots_by_pair:
            raise OrderingError("pair_reservation_not_next_scheduled_pair")
        existing = [
            attempt for attempt in ledger["attempts"] if attempt["pair_index"] == pair_index
        ]
        members = authority.slots_by_pair[pair_index]
        if existing:
            expected_keys = {(member.run_id, 1) for member in members}
            if {
                (attempt["run_id"], attempt["attempt_ordinal"])
                for attempt in existing
                if attempt["attempt_ordinal"] == 1
            } != expected_keys:
                raise LedgerError("existing_pair_reservation_conflicts_with_schedule")
            return tuple(AttemptKey(member.run_id, 1) for member in members)  # type: ignore[return-value]
        if ledger["budget_exhausted_pairs"]:
            raise BudgetExhausted("pair_already_marked_not_run_budget_exhausted")
        known = _ledger_decimal(ledger["known_spend_usd"], "known_spend_usd")
        outstanding = _ledger_decimal(
            ledger["unreleased_reservation_usd"], "unreleased_reservation_usd"
        )
        if known + outstanding + authority.pair_reservation > authority.total_budget:
            def mark_exhausted(candidate: dict[str, Any], _: int) -> None:
                candidate["budget_exhausted_pairs"].append(pair_index)

            self._update_ledger("pair_budget_exhausted", mark_exhausted)
            raise BudgetExhausted(
                f"pair_{pair_index}_reservation_would_exceed_total_budget"
            )

        def reserve(candidate: dict[str, Any], _: int) -> None:
            candidate["attempts"].extend(
                self._new_attempt(member, 1) for member in members
            )

        self._update_ledger("pair_reservation", reserve)
        return tuple(AttemptKey(member.run_id, 1) for member in members)  # type: ignore[return-value]

    def retry_eligible(self, run_id: str) -> bool:
        attempts = self._attempts_for_run(run_id)
        return (
            len(attempts) == 1
            and attempts[0]["attempt_ordinal"] == 1
            and attempts[0]["status"] == "SETTLED"
            and attempts[0]["outcome"] == "INFRA_ERROR"
            and attempts[0]["cost_status"] == "KNOWN"
            and attempts[0]["terminal_commit"] is not None
        )

    def reserve_retry(self, run_id: str) -> AttemptKey:
        authority, ledger = self._require_started()
        if run_id not in authority.slots_by_run or not self.retry_eligible(run_id):
            raise RetryNotAllowed("same_slot_retry_not_eligible")
        known = _ledger_decimal(ledger["known_spend_usd"], "known_spend_usd")
        outstanding = _ledger_decimal(
            ledger["unreleased_reservation_usd"], "unreleased_reservation_usd"
        )
        if known + outstanding + authority.invocation_cap > authority.total_budget:
            raise BudgetExhausted("retry_reservation_would_exceed_total_budget")
        slot = authority.slots_by_run[run_id]

        def reserve(candidate: dict[str, Any], _: int) -> None:
            candidate["attempts"].append(self._new_attempt(slot, 2))

        self._update_ledger("retry_reservation", reserve)
        return AttemptKey(run_id, 2)

    def _expected_invocation_key(self) -> AttemptKey:
        authority, ledger = self._require_started()
        pair_index = len(ledger["pair_checkpoints"]) + 1
        members = authority.slots_by_pair.get(pair_index)
        if members is None:
            raise OrderingError("all_pairs_already_checkpointed")
        for member in members:
            attempts = self._attempts_for_run(member.run_id)
            if not attempts:
                raise OrderingError("current_pair_not_reserved")
            first = attempts[0]
            if first["status"] == "RESERVED":
                return AttemptKey(member.run_id, 1)
            if first["status"] == "INTENT_PERSISTED":
                raise OrderingError("nonterminal_intent_requires_restart_reconciliation")
            if first["status"] in {"UNRESOLVED", "ORPHANED"}:
                raise UnresolvedProviderCost("current_pair_contains_unresolved_attempt")
            if first["terminal_commit"] is None:
                raise OrderingError("settlement_not_terminally_committed")
            if first["outcome"] == "INFRA_ERROR":
                if len(attempts) == 1:
                    raise RetryNotAllowed("eligible_retry_must_be_reserved_before_member_two")
                second = attempts[1]
                if second["status"] == "RESERVED":
                    return AttemptKey(member.run_id, 2)
                if second["status"] != "SETTLED" or second["terminal_commit"] is None:
                    raise OrderingError("retry_not_terminally_committed")
        raise OrderingError("both_members_terminal_pair_checkpoint_required")

    def _validate_invocation(
        self, authority: ManifestAuthority, key: AttemptKey, invocation: ModelInvocation
    ) -> Path:
        if not isinstance(invocation, ModelInvocation):
            raise OrderingError("provider_requires_canonical_ModelInvocation")
        manifest_slot = select_manifest_slot(authority.data, key.run_id)
        if invocation.argv != manifest_slot.command or invocation.prompt != manifest_slot.prompt:
            raise OrderingError("invocation_differs_from_manifest_slot")
        if not invocation.argv or invocation.argv[0] != authority.provider_executable:
            raise ProviderIdentityError("invocation_provider_path_mismatch")
        try:
            workspace = Path(invocation.cwd).resolve(strict=True)
            workspace_root = self._physical(authority.workspaces_root).resolve(strict=True)
            relative = workspace.relative_to(workspace_root)
        except (OSError, ValueError) as exc:
            raise OrderingError("invocation_workspace_outside_manifest_root") from exc
        if len(relative.parts) != 1 or not _SESSION_RE.fullmatch(relative.name):
            raise OrderingError("invocation_workspace_identity_not_opaque")
        if invocation.task_visible_run_id != relative.name:
            raise OrderingError("task_visible_run_id_workspace_mismatch")
        audit = audit_model_visible_surfaces(
            invocation,
            workspace,
            manifest_slot.forbidden_model_tokens,
        )
        if not audit.passed:
            raise OrderingError(
                f"model_visible_surface_not_condition_neutral:{audit.as_record()}"
            )
        return workspace

    def _attempt_directory(
        self, authority: ManifestAuthority, key: AttemptKey
    ) -> tuple[Path, Path]:
        logical_run = authority.runs_root / key.run_id
        logical_attempt = logical_run / f"attempt-{key.attempt_ordinal}"
        physical_run = self._physical(logical_run)
        physical_attempt = self._physical(logical_attempt)
        _durable_mkdir(physical_run)
        _durable_mkdir(physical_attempt)
        return logical_attempt, physical_attempt

    def _persist_invocation_intent(
        self,
        authority: ManifestAuthority,
        key: AttemptKey,
        invocation: ModelInvocation,
        workspace: Path,
    ) -> None:
        logical_attempt, physical_attempt = self._attempt_directory(authority, key)
        logical_intent = logical_attempt / "INVOCATION_INTENT.json"
        invocation_record = invocation.model_visible_record()
        intent_record = {
            "manifest_sha256": authority.sha256,
            "epoch_id": authority.epoch_id,
            "pair_index": authority.slots_by_run[key.run_id].pair_index,
            "run_id": key.run_id,
            "attempt_ordinal": key.attempt_ordinal,
            "workspace_path": str(self.storage.logical(workspace)),
            "invocation_sha256": _sha256_bytes(_canonical_json(invocation_record)),
        }
        raw = _canonical_json(intent_record)
        _durable_create_bytes(physical_attempt / "INVOCATION_INTENT.json", raw)
        self._emit("invocation_intent_file_persisted")
        reference = {
            "path": str(logical_intent),
            "sha256": _sha256_bytes(raw),
            "workspace_path": str(self.storage.logical(workspace)),
            "invocation_sha256": intent_record["invocation_sha256"],
        }

        def persist(candidate: dict[str, Any], _: int) -> None:
            matches = [
                item
                for item in candidate["attempts"]
                if item["run_id"] == key.run_id
                and item["attempt_ordinal"] == key.attempt_ordinal
            ]
            if len(matches) != 1 or matches[0]["status"] != "RESERVED":
                raise LedgerError("invocation_intent_target_not_reserved")
            matches[0]["durable_intent"] = reference
            matches[0]["status"] = "INTENT_PERSISTED"

        self._update_ledger("invocation_intent", persist)

    def _persist_provider_observation(
        self,
        authority: ManifestAuthority,
        key: AttemptKey,
        observation: ProviderResult,
    ) -> ProviderResult:
        attempt = self._find_attempt(key)
        if attempt["status"] != "INTENT_PERSISTED":
            raise OrderingError("provider_observation_without_durable_intent")
        allowed_outcomes = set(
            _required(authority.data, "failure_denominator", "counted_in_denominator")
        ) | {"INFRA_ERROR", "INFRA_FAIL"}
        outcome = observation.outcome
        if outcome not in allowed_outcomes:
            outcome = "INFRA_ERROR"
            cost_status = "UNRESOLVED"
            exact_cost: str | None = None
        else:
            cost_status = observation.cost_status
            exact_cost = None
        if key.attempt_ordinal == 2 and outcome == "INFRA_ERROR":
            outcome = "INFRA_FAIL"
        if cost_status == "KNOWN" and observation.exact_cost is not None:
            parsed_cost = _decimal(observation.exact_cost, "provider_exact_cost")
            if parsed_cost <= authority.invocation_cap:
                exact_cost = decimal_text(parsed_cost)
            else:
                cost_status = "UNRESOLVED"
        elif cost_status != "UNRESOLVED":
            cost_status = "UNRESOLVED"
        if cost_status == "UNRESOLVED":
            exact_cost = None

        logical_attempt = (
            authority.runs_root / key.run_id / f"attempt-{key.attempt_ordinal}"
        )
        physical_attempt = self._physical(logical_attempt)
        raw_path = logical_attempt / "provider-raw.bin"
        canonical_path = logical_attempt / "canonical-evidence.json"
        result_path = logical_attempt / "result.json"
        raw_evidence = (
            observation.raw_evidence
            if isinstance(observation.raw_evidence, bytes)
            else repr(observation.raw_evidence).encode("utf-8")
        )
        canonical_record = (
            dict(observation.canonical_evidence)
            if isinstance(observation.canonical_evidence, Mapping)
            else {"invalid_canonical_evidence": repr(observation.canonical_evidence)}
        )
        provider_record = (
            dict(observation.result_record)
            if isinstance(observation.result_record, Mapping)
            else {"invalid_result_record": repr(observation.result_record)}
        )
        result_record = {
            "provider_result": provider_record,
            "outcome": outcome,
            "cost_status": cost_status,
            "exact_cost": exact_cost,
        }
        canonical_raw = _canonical_json(canonical_record)
        result_raw = _canonical_json(result_record)
        _atomic_write_bytes(physical_attempt / raw_path.name, raw_evidence)
        _atomic_write_bytes(physical_attempt / canonical_path.name, canonical_raw)
        _atomic_write_bytes(physical_attempt / result_path.name, result_raw)
        _fsync_directory(physical_attempt)
        self._emit("provider_evidence_persisted")
        evidence_refs = [
            {"path": str(raw_path), "sha256": _sha256_bytes(raw_evidence)},
            {"path": str(canonical_path), "sha256": _sha256_bytes(canonical_raw)},
        ]
        result_refs = [
            {"path": str(result_path), "sha256": _sha256_bytes(result_raw)}
        ]

        def settle(candidate: dict[str, Any], next_revision: int) -> None:
            matches = [
                item
                for item in candidate["attempts"]
                if item["run_id"] == key.run_id
                and item["attempt_ordinal"] == key.attempt_ordinal
            ]
            if len(matches) != 1 or matches[0]["status"] != "INTENT_PERSISTED":
                raise LedgerError("provider_settlement_target_invalid")
            target = matches[0]
            target["evidence_paths_and_sha256"] = evidence_refs
            target["result_paths_and_sha256"] = result_refs
            target["outcome"] = outcome
            target["cost_status"] = cost_status
            target["exact_cost"] = exact_cost
            target["status"] = "SETTLED" if cost_status == "KNOWN" else "UNRESOLVED"
            target["settlement_revision"] = next_revision
            if cost_status == "KNOWN":
                target["unreleased_amount"] = "0"

        self._update_ledger("attempt_settlement", settle)
        if cost_status == "UNRESOLVED":
            self._write_state(authority.aborted_state)
            self.started = False
            if self.lock is not None:
                self.lock.release()
            raise UnresolvedProviderCost(
                f"provider_cost_unresolved:{key.run_id}:{key.attempt_ordinal}"
            )
        self._bind_terminal_marker(key, recovery=False)
        return ProviderResult(
            outcome=outcome,
            cost_status="KNOWN",
            exact_cost=exact_cost,
            raw_evidence=raw_evidence,
            canonical_evidence=canonical_record,
            result_record=provider_record,
        )

    def invoke(
        self, key: AttemptKey, invocation: ModelInvocation
    ) -> ProviderResult:
        authority, _ = self._require_started()
        if key != self._expected_invocation_key():
            raise OrderingError("invocation_not_next_scheduled_attempt")
        attempt = self._find_attempt(key)
        if attempt["status"] != "RESERVED":
            raise OrderingError("invocation_attempt_not_reserved")
        workspace = self._validate_invocation(authority, key, invocation)
        self._persist_invocation_intent(authority, key, invocation, workspace)
        self._emit("provider_call_boundary_reached")
        try:
            result = self.provider.invoke(invocation)
            if not isinstance(result, ProviderResult):
                raise TypeError("provider boundary did not return ProviderResult")
            if (
                not isinstance(result.outcome, str)
                or not isinstance(result.cost_status, str)
                or not isinstance(result.raw_evidence, bytes)
                or not isinstance(result.canonical_evidence, Mapping)
                or not isinstance(result.result_record, Mapping)
            ):
                raise TypeError("provider boundary returned malformed terminal evidence")
        except Exception as exc:
            result = ProviderResult(
                outcome="INFRA_ERROR",
                cost_status="UNRESOLVED",
                exact_cost=None,
                raw_evidence=repr(exc).encode("utf-8"),
                canonical_evidence={"provider_boundary_exception": type(exc).__name__},
                result_record={"exception": repr(exc)},
            )
        return self._persist_provider_observation(authority, key, result)

    def complete_pair(self, pair_index: int) -> None:
        self._persist_pair_checkpoint(pair_index, recovery=False)

    def finish(self) -> None:
        authority, ledger = self._require_started()
        completed_pairs = len(ledger["pair_checkpoints"])
        all_pairs_complete = completed_pairs == len(authority.slots_by_pair)
        stopped_by_cap = ledger["budget_exhausted_pairs"] == [completed_pairs + 1]
        if not all_pairs_complete and not stopped_by_cap:
            raise OrderingError("cannot_complete_with_unfinished_schedulable_pair")
        if ledger["unresolved_cost_items"] or _ledger_decimal(
            ledger["unreleased_reservation_usd"], "unreleased_reservation_usd"
        ) != 0:
            raise LedgerError("cannot_complete_with_unsettled_accounting")
        self._write_state(authority.completed_state)
        self.started = False
        if self.lock is not None:
            self.lock.release()

    def abort(self) -> None:
        authority, _ = self._require_started()
        self._write_state(authority.aborted_state)
        self.started = False
        if self.lock is not None:
            self.lock.release()


__all__ = [
    "AttemptKey",
    "AuthorityConflict",
    "BudgetExhausted",
    "EntryPreconditionError",
    "ExecutionAuthorization",
    "ExecutionController",
    "ExecutionControllerError",
    "ExecutorIdentity",
    "ExecutorIdentityError",
    "GitExecutorIdentityProbe",
    "LedgerError",
    "ManifestAuthority",
    "ManifestIdentityError",
    "OrderingError",
    "ProviderBoundary",
    "ProviderIdentityError",
    "ProviderResult",
    "REQUIRED_MANIFEST_SHA256",
    "RecoveryAborted",
    "RetryNotAllowed",
    "RootMapping",
    "StartupResult",
    "TerminalStateError",
    "UnresolvedProviderCost",
    "WriterLock",
    "WriterLockBusy",
    "WriterLockError",
    "decimal_text",
]
