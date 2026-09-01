#!/usr/bin/env python3
"""Canonical, restart-safe orchestration for prospective epoch pair P01.

The module is deliberately inert on import.  It defines the future production
paths and composes the already-sealed runner/controller/provider primitives,
but it creates no canonical epoch root and performs no provider call unless an
authorized caller explicitly invokes :func:`execute_pair` with all required
runtime dependencies and paid-pair authorization.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import socket
import stat
import sys
import time
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


def _load_sibling(module_name: str, filename: str, *aliases: str) -> Any:
    for name in (module_name, *aliases):
        module = sys.modules.get(name)
        if module is not None:
            return module
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"required pair-driver sibling is missing: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


execution_builder = _load_sibling(
    "fable_ablation_execution_manifest",
    "build_execution_manifest.py",
    "build_execution_manifest",
)
compiler = execution_builder.compiler
runner = execution_builder.runner
controller = _load_sibling(
    "fable_ablation_epoch_controller", "epoch_controller.py", "epoch_controller"
)
composition = _load_sibling(
    "fable_ablation_epoch_provider_composition",
    "epoch_provider_composition.py",
    "epoch_provider_composition",
)


EPOCH_ID = compiler.CTO_PROSPECTIVE_EPOCH_ID
SEALED_MANIFEST_SHA256 = (
    "49aa913fdf5487b839565dba8a57de243b936a61e0c70a384fdc4df73b2fbd94"
)
CANONICAL_AUTHORITY_ROOT = Path("/Users/kelvin/fable-ablation")
CANONICAL_EPOCH_ROOT = CANONICAL_AUTHORITY_ROOT / "epochs" / EPOCH_ID
STAGING_AUTHORITY_ROOT = Path(
    "/Users/kelvin/FableAblation-Epoch-56a62a62e5aae9fe3a230cbbed831a2a"
)

LEDGER_CAP_USD = "7.7339242"
PER_INVOCATION_CAP_USD = "2.0000000"
PAIR_MAX_SPEND_USD = "4.0000000"
LIFETIME_HARD_CAP_USD = "10.0000000"

EPOCH_OPEN_SCHEMA = "FABLE_ABLATION_EPOCH_OPEN/v1"
PAID_PAIR_AUTHORIZATION_SCHEMA = "FABLE_ABLATION_PAID_PAIR_AUTHORIZATION/v1"
PAIR_AGGREGATE_SCHEMA = "FABLE_ABLATION_PAIR_AGGREGATE/v1"
RUN_EVIDENCE_SCHEMA = runner.RUN_EVIDENCE_SCHEMA


class PairDriverError(RuntimeError):
    """Base class for fail-closed pair orchestration errors."""


class AuthorityPreflightError(PairDriverError):
    """The sealed authority/signoff provenance could not be proven."""


class PaidAuthorizationError(PairDriverError):
    """The exact future paid-pair authorization is absent or mismatched."""


class EpochOpenError(PairDriverError):
    """The durable epoch-open seal/layout is missing, ambiguous, or changed."""


class RunEvidenceError(PairDriverError):
    """Run evidence could not be persisted or replayed without ambiguity."""


class PairRecoveryError(PairDriverError):
    """Restart state cannot prove a safe next action."""


class PairExecutionError(PairDriverError):
    """An arm failed after orchestration began."""


@dataclass(frozen=True)
class EpochPaths:
    """All authority/runtime paths; only the four fixed runtime categories exist."""

    epoch_root: Path
    manifest_path: Path
    owner_signoff_path: Path
    owner_signoff_decision_path: Path
    lock_path: Path

    @classmethod
    def canonical(cls) -> "EpochPaths":
        return cls(
            epoch_root=CANONICAL_EPOCH_ROOT,
            manifest_path=STAGING_AUTHORITY_ROOT / "build" / "manifest.json",
            owner_signoff_path=STAGING_AUTHORITY_ROOT / "build" / "owner-signoff.json",
            owner_signoff_decision_path=(
                STAGING_AUTHORITY_ROOT / "authority" / "owner-signoff-decision.txt"
            ),
            lock_path=controller.PRODUCTION_LOCK_PATH,
        )

    @property
    def reservations(self) -> Path:
        return self.epoch_root / "reservations"

    @property
    def workspaces(self) -> Path:
        return self.epoch_root / "workspaces"

    @property
    def runs(self) -> Path:
        return self.epoch_root / "runs"

    @property
    def aggregates(self) -> Path:
        return self.epoch_root / "aggregates"

    @property
    def ledger(self) -> Path:
        return self.reservations / "ledger.jsonl"

    @property
    def epoch_open_seal(self) -> Path:
        return self.reservations / "epoch-open.json"

    @property
    def execution_manifest(self) -> Path:
        return self.runs / "P01-execution-manifest.json"

    @property
    def pair_aggregate(self) -> Path:
        return self.aggregates / "P01.json"

    def run_evidence(self, run_id: str) -> Path:
        return self.runs / run_id / "run-evidence.json"


@dataclass(frozen=True)
class PersistedEpochOpen:
    path: Path
    record: Mapping[str, Any]
    sha256: str


@dataclass(frozen=True)
class PersistedRunEvidence:
    path: Path
    record: Mapping[str, Any]
    sha256: str
    state: Any


@dataclass(frozen=True)
class ArmTerminal:
    condition: str
    run_id: str
    disposition: str
    cost_usd: str
    evidence: PersistedRunEvidence


def _default_ledger_factory(path: Path, cap: Decimal) -> Any:
    return controller.BudgetLedger(path, cap)


def _creation_identity() -> Mapping[str, Any]:
    identity = controller.capture_process_identity(os.getpid())
    return {
        "host": identity.host,
        "boot_id": identity.boot_id,
        "pid": identity.pid,
        "start_identity": identity.start_identity,
        "executable": {
            "path": identity.executable.path,
            "device": identity.executable.device,
            "inode": identity.executable.inode,
        },
        "pgid": identity.pgid,
        "sid": identity.sid,
    }


@dataclass
class PairRuntimeDependencies:
    """Injected boundaries required to execute the runner without hidden fallback."""

    reference_events: Sequence[Any]
    materializer: Callable[[Any], None]
    policy: Any
    environment: Mapping[str, str] = field(default_factory=dict)
    opaque_id_factory: Callable[[], str] | None = None
    lock_factory: Callable[[Path], Any] = controller.EpochLock
    host_provider: Callable[[], str] = socket.gethostname
    boot_provider: Callable[[], str] = controller.current_boot_id
    process_scanner: Callable[[Sequence[Path]], Sequence[Any]] = (
        controller.scan_managed_root_processes
    )
    creation_identity_provider: Callable[[], Mapping[str, Any]] = _creation_identity
    time_ns_provider: Callable[[], int] = time.time_ns
    ledger_factory: Callable[[Path, Decimal], Any] = _default_ledger_factory
    reconciler: Callable[..., Any] = controller.reconcile_fresh_start
    run_slot_executor: Callable[..., Any] = runner.execute_manifest_slot
    composition_executor: Callable[..., Any] = composition.execute_reserved_provider_slot


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise PairDriverError(f"value is not deterministic JSON: {exc}") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key:{key}")
        result[key] = value
    return result


def _load_unique_json(data: bytes, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant:{token}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise PairDriverError(f"{label} is not strict JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise PairDriverError(f"{label} root must be an object")
    return value


def _secure_read(path: Path, label: str) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise PairDriverError(f"{label}:O_NOFOLLOW_unavailable")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise PairDriverError(f"{label}:secure_open_failed:{exc.errno}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise PairDriverError(f"{label}:not_single_link_regular_file")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise PairDriverError(f"{label}:changed_during_read")
        raw = b"".join(blocks)
        if len(raw) != before.st_size:
            raise PairDriverError(f"{label}:short_read")
        return raw
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_bytes_create_once(path: Path, data: bytes, label: str) -> str:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise PairDriverError(f"{label}:parent_must_be_existing_real_directory")
    if not hasattr(os, "O_NOFOLLOW"):
        raise PairDriverError(f"{label}:O_NOFOLLOW_unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise PairDriverError(f"{label}:create_once_target_exists") from exc
    except OSError as exc:
        raise PairDriverError(f"{label}:create_failed:{exc.errno}") from exc
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise PairDriverError(f"{label}:short_write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)
    return _sha256(data)


def _write_json_create_once(path: Path, record: Mapping[str, Any], label: str) -> str:
    return _write_bytes_create_once(path, _canonical_json_bytes(record) + b"\n", label)


def _validate_authority_and_signoff(
    paths: EpochPaths,
    *,
    expected_manifest_sha256: str,
) -> tuple[Mapping[str, Any], bytes]:
    try:
        manifest = controller.load_manifest_after_lock(
            paths.manifest_path, expected_sha256=expected_manifest_sha256
        )
        compiler.validate_manifest(manifest)
        manifest_bytes = compiler.serialize_manifest(manifest)
    except Exception as exc:
        raise AuthorityPreflightError(f"sealed manifest preflight failed:{exc}") from exc
    if _sha256(manifest_bytes) != expected_manifest_sha256:
        raise AuthorityPreflightError("canonical manifest bytes differ from exact authority")

    try:
        signoff_bytes = _secure_read(paths.owner_signoff_path, "owner_signoff")
        signoff = _load_unique_json(signoff_bytes, "owner_signoff")
        compiler.validate_owner_signoff(manifest_bytes, signoff)
    except Exception as exc:
        raise AuthorityPreflightError(f"owner signoff validation failed:{exc}") from exc

    source = signoff.get("authorization_source_identity")
    if not isinstance(source, Mapping) or set(source) != {"kind", "identifier", "sha256"}:
        raise AuthorityPreflightError("owner signoff provenance identity is invalid")
    try:
        decision_bytes = _secure_read(
            paths.owner_signoff_decision_path, "owner_signoff_decision"
        )
    except Exception as exc:
        raise AuthorityPreflightError(str(exc)) from exc
    if source.get("sha256") != _sha256(decision_bytes):
        raise AuthorityPreflightError(
            "owner signoff authorization_source_identity does not bind exact decision bytes"
        )
    first_line = decision_bytes.splitlines()[0].decode("utf-8", errors="strict")
    if first_line != f"AUTHORIZE_{source.get('identifier')}":
        raise AuthorityPreflightError("owner signoff decision token differs from provenance")
    return manifest, manifest_bytes


def validate_paid_pair_authorization(
    authorization: Mapping[str, Any] | None,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
) -> Mapping[str, Any]:
    """Validate record semantics only; human-turn provenance remains an Owner gate."""

    if authorization is None:
        raise PaidAuthorizationError("future exact paid-pair authorization is required")
    if not isinstance(authorization, Mapping):
        raise PaidAuthorizationError("paid-pair authorization must be an object")
    required = {
        "schema",
        "decision",
        "epoch_id",
        "manifest_sha256",
        "pair_id",
        "task_id",
        "arm_order",
        "maximum_pair_spend_usd",
        "per_invocation_cap_usd",
        "pre_pair_headroom_usd",
    }
    if set(authorization) != required:
        raise PaidAuthorizationError("paid-pair authorization field set is invalid")
    expected = {
        "schema": PAID_PAIR_AUTHORIZATION_SCHEMA,
        "decision": "AUTHORIZE_PAID_PAIR",
        "epoch_id": EPOCH_ID,
        "manifest_sha256": manifest_sha256,
        "pair_id": execution_builder.PAIR_ID,
        "task_id": execution_builder.TASK_ID,
        "arm_order": list(execution_builder.ARM_ORDER),
        "maximum_pair_spend_usd": PAIR_MAX_SPEND_USD,
        "per_invocation_cap_usd": PER_INVOCATION_CAP_USD,
        "pre_pair_headroom_usd": LEDGER_CAP_USD,
    }
    if dict(authorization) != expected:
        raise PaidAuthorizationError("paid-pair authorization does not bind exact P01 limits")
    budget = manifest.get("budget")
    if not isinstance(budget, Mapping) or budget.get("max_additional_spend_usd") != LEDGER_CAP_USD:
        raise PaidAuthorizationError("paid authorization headroom differs from manifest")
    return authorization


def _validate_runtime_layout(paths: EpochPaths) -> None:
    try:
        root_stat = os.lstat(paths.epoch_root)
    except OSError as exc:
        raise EpochOpenError(f"epoch root must be materialized before driver entry:{exc}") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise EpochOpenError("epoch root must be a real directory")
    for name, candidate in (
        ("reservations", paths.reservations),
        ("workspaces", paths.workspaces),
        ("runs", paths.runs),
        ("aggregates", paths.aggregates),
    ):
        try:
            metadata = os.lstat(candidate)
        except OSError as exc:
            raise EpochOpenError(f"runtime category missing:{name}:{exc.errno}") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise EpochOpenError(f"runtime category is not a real directory:{name}")


def _runtime_state_exists_without_seal(paths: EpochPaths) -> bool:
    for root in (paths.reservations, paths.workspaces, paths.runs, paths.aggregates):
        for child in root.iterdir():
            if child != paths.epoch_open_seal:
                return True
    return False


def _epoch_open_record(
    paths: EpochPaths,
    manifest_sha256: str,
    *,
    host: str,
    boot_id: str,
    creation_identity: Mapping[str, Any],
    created_at_unix_ns: int,
) -> dict[str, Any]:
    if not isinstance(created_at_unix_ns, int) or isinstance(created_at_unix_ns, bool) or created_at_unix_ns <= 0:
        raise EpochOpenError("epoch-open creation time must be positive UNIX nanoseconds")
    if not isinstance(creation_identity, Mapping) or not creation_identity:
        raise EpochOpenError("epoch-open creation identity is required")
    return {
        "schema": EPOCH_OPEN_SCHEMA,
        "version": 1,
        "epoch_id": EPOCH_ID,
        "canonical_epoch_root": str(paths.epoch_root.resolve(strict=True)),
        "sealed_manifest_sha256": manifest_sha256,
        "observed_host_identity": host,
        "observed_boot_identity": boot_id,
        "creation": {
            "identity": copy.deepcopy(dict(creation_identity)),
            "time": {
                "clock": "CLOCK_REALTIME",
                "representation": "UNIX_NANOSECONDS_DECIMAL_STRING",
                "value": str(created_at_unix_ns),
            },
        },
    }


def _validate_epoch_open_record(
    record: Mapping[str, Any], paths: EpochPaths, manifest_sha256: str
) -> None:
    required = {
        "schema",
        "version",
        "epoch_id",
        "canonical_epoch_root",
        "sealed_manifest_sha256",
        "observed_host_identity",
        "observed_boot_identity",
        "creation",
    }
    if set(record) != required:
        raise EpochOpenError("epoch-open record field set is invalid")
    if record.get("schema") != EPOCH_OPEN_SCHEMA or record.get("version") != 1:
        raise EpochOpenError("epoch-open schema/version is invalid")
    expected = {
        "epoch_id": EPOCH_ID,
        "canonical_epoch_root": str(paths.epoch_root.resolve(strict=True)),
        "sealed_manifest_sha256": manifest_sha256,
    }
    for field_name, value in expected.items():
        if record.get(field_name) != value:
            raise EpochOpenError(f"epoch-open {field_name} differs from authority")
    if not isinstance(record.get("observed_host_identity"), str) or not record[
        "observed_host_identity"
    ]:
        raise EpochOpenError("epoch-open host identity is missing")
    if not isinstance(record.get("observed_boot_identity"), str) or not record[
        "observed_boot_identity"
    ]:
        raise EpochOpenError("epoch-open boot identity is missing")
    creation = record.get("creation")
    if not isinstance(creation, Mapping) or set(creation) != {"identity", "time"}:
        raise EpochOpenError("epoch-open creation record is invalid")
    if not isinstance(creation.get("identity"), Mapping) or not creation["identity"]:
        raise EpochOpenError("epoch-open creation identity is invalid")
    time_record = creation.get("time")
    if (
        not isinstance(time_record, Mapping)
        or set(time_record) != {"clock", "representation", "value"}
        or time_record.get("clock") != "CLOCK_REALTIME"
        or time_record.get("representation")
        != "UNIX_NANOSECONDS_DECIMAL_STRING"
    ):
        raise EpochOpenError("epoch-open creation time representation is invalid")
    if re.fullmatch(r"[1-9][0-9]*", str(time_record.get("value"))) is None:
        raise EpochOpenError("epoch-open creation time value is invalid")


def load_epoch_open_seal(
    paths: EpochPaths, manifest_sha256: str
) -> PersistedEpochOpen:
    try:
        raw = _secure_read(paths.epoch_open_seal, "epoch_open")
        if not raw.endswith(b"\n"):
            raise EpochOpenError("epoch-open record is not newline terminated")
        record = _load_unique_json(raw, "epoch_open")
        if _canonical_json_bytes(record) + b"\n" != raw:
            raise EpochOpenError("epoch-open record is not canonical JSON")
        _validate_epoch_open_record(record, paths, manifest_sha256)
    except EpochOpenError:
        raise
    except Exception as exc:
        raise EpochOpenError(f"epoch-open record cannot be read:{exc}") from exc
    return PersistedEpochOpen(paths.epoch_open_seal, record, _sha256(raw))


def ensure_epoch_open_seal(
    paths: EpochPaths,
    manifest_sha256: str,
    dependencies: PairRuntimeDependencies,
    *,
    current_host: str,
    current_boot_id: str,
) -> PersistedEpochOpen:
    if not paths.epoch_open_seal.exists():
        if _runtime_state_exists_without_seal(paths):
            raise EpochOpenError("durable epoch-open seal missing for existing runtime state")
        record = _epoch_open_record(
            paths,
            manifest_sha256,
            host=current_host,
            boot_id=current_boot_id,
            creation_identity=dependencies.creation_identity_provider(),
            created_at_unix_ns=dependencies.time_ns_provider(),
        )
        _write_json_create_once(paths.epoch_open_seal, record, "epoch_open")
    # Reconciliation consumes only a record re-read from durable storage.
    return load_epoch_open_seal(paths, manifest_sha256)


def build_budget_ledger(
    paths: EpochPaths,
    manifest: Mapping[str, Any],
    ledger_factory: Callable[[Path, Decimal], Any] = _default_ledger_factory,
) -> Any:
    budget = manifest.get("budget")
    if not isinstance(budget, Mapping):
        raise PairRecoveryError("manifest budget is missing")
    cap = budget.get("max_additional_spend_usd")
    if cap != LEDGER_CAP_USD:
        raise PairRecoveryError("BudgetLedger must use max_additional_spend_usd=7.7339242")
    if cap == budget.get("lifetime_hard_cap_usd") or cap == LIFETIME_HARD_CAP_USD:
        raise PairRecoveryError("10.0000000 is forbidden as the new-epoch ledger cap")
    return ledger_factory(paths.ledger, Decimal(cap))


def _ledger_entries_for_slot(snapshot: Any, slot_id: str) -> tuple[Mapping[str, Any], ...]:
    return tuple(entry for entry in snapshot.entries if entry.get("slot_id") == slot_id)


def _entry_for_transition(
    snapshot: Any, slot_id: str, transition: str
) -> Mapping[str, Any] | None:
    matches = [
        entry
        for entry in _ledger_entries_for_slot(snapshot, slot_id)
        if entry.get("transition") == transition
    ]
    return matches[-1] if matches else None


def _validate_run_evidence_record(record: Mapping[str, Any], slot_id: str) -> None:
    required = {
        "schema",
        "slot_id",
        "reservation",
        "reservation_entry_sha256",
        "provider_start_intent",
        "provider_start_entry_sha256",
        "terminal",
        "run_evidence",
        "run_evidence_sha256",
    }
    if set(record) != required:
        raise RunEvidenceError("run-evidence field set is invalid")
    if record.get("schema") != RUN_EVIDENCE_SCHEMA or record.get("slot_id") != slot_id:
        raise RunEvidenceError("run-evidence schema/slot identity is invalid")
    if record.get("reservation") is not True:
        raise RunEvidenceError("run-evidence does not bind a reservation")
    if re.fullmatch(r"[0-9a-f]{64}", str(record.get("reservation_entry_sha256"))) is None:
        raise RunEvidenceError("run-evidence reservation hash is invalid")
    start = record.get("provider_start_intent")
    if not isinstance(start, bool):
        raise RunEvidenceError("run-evidence start-intent flag is invalid")
    start_hash = record.get("provider_start_entry_sha256")
    if start:
        if re.fullmatch(r"[0-9a-f]{64}", str(start_hash)) is None:
            raise RunEvidenceError("run-evidence start-intent hash is invalid")
    elif start_hash is not None:
        raise RunEvidenceError("unstarted run must not claim a start-intent hash")
    semantic = record.get("run_evidence")
    if not isinstance(semantic, Mapping):
        raise RunEvidenceError("runner semantic evidence is missing")
    if record.get("run_evidence_sha256") != _sha256(_canonical_json_bytes(semantic)):
        raise RunEvidenceError("runner semantic evidence hash is invalid")
    terminal = record.get("terminal")
    if terminal is None:
        if not start:
            raise RunEvidenceError("unstarted evidence without terminal disposition is invalid")
        return
    if not isinstance(terminal, Mapping) or set(terminal) != {
        "disposition",
        "cost_usd",
        "ledger_transition",
        "ledger_entry_sha256",
    }:
        raise RunEvidenceError("run-evidence terminal disposition is invalid")
    disposition = terminal.get("disposition")
    if disposition not in {"settled", "released"}:
        raise RunEvidenceError("run-evidence disposition is invalid")
    if disposition == "settled" and (
        not start or terminal.get("ledger_transition") != "settlement"
    ):
        raise RunEvidenceError("settled evidence does not bind a provider start")
    if disposition == "released" and (
        start
        or terminal.get("ledger_transition") != "release"
        or terminal.get("cost_usd") != compiler.CANONICAL_USD_ZERO
    ):
        raise RunEvidenceError("released evidence has invalid semantics")
    try:
        compiler.parse_canonical_usd(terminal.get("cost_usd"))
    except Exception as exc:
        raise RunEvidenceError("terminal cost is not canonical USD") from exc
    if re.fullmatch(r"[0-9a-f]{64}", str(terminal.get("ledger_entry_sha256"))) is None:
        raise RunEvidenceError("terminal ledger hash is invalid")


def write_run_evidence(
    path: Path,
    *,
    slot_id: str,
    reservation_entry: Mapping[str, Any],
    provider_start_entry: Mapping[str, Any] | None,
    terminal: Mapping[str, Any] | None,
    evidence: Any,
) -> PersistedRunEvidence:
    if path.exists() or path.parent.exists():
        raise RunEvidenceError("run evidence is create-once and its run directory exists")
    try:
        path.parent.mkdir(mode=0o700)
    except OSError as exc:
        raise RunEvidenceError(f"run evidence directory create failed:{exc}") from exc
    try:
        _fsync_directory(path.parent.parent)
    except OSError as exc:
        raise RunEvidenceError(
            f"run evidence directory entry durability failed:{exc}"
        ) from exc
    semantic = evidence.as_record()
    record = {
        "schema": RUN_EVIDENCE_SCHEMA,
        "slot_id": slot_id,
        "reservation": True,
        "reservation_entry_sha256": reservation_entry.get("entry_hash"),
        "provider_start_intent": provider_start_entry is not None,
        "provider_start_entry_sha256": (
            None if provider_start_entry is None else provider_start_entry.get("entry_hash")
        ),
        "terminal": None if terminal is None else copy.deepcopy(dict(terminal)),
        "run_evidence": semantic,
        "run_evidence_sha256": _sha256(_canonical_json_bytes(semantic)),
    }
    _validate_run_evidence_record(record, slot_id)
    try:
        _write_json_create_once(path, record, "run_evidence")
    except Exception as exc:
        raise RunEvidenceError(str(exc)) from exc
    return read_run_evidence_record(path, slot_id)


def read_run_evidence_record(path: Path, slot_id: str) -> PersistedRunEvidence:
    try:
        raw = _secure_read(path, "run_evidence")
        if not raw.endswith(b"\n"):
            raise RunEvidenceError("run evidence is not newline terminated")
        record = _load_unique_json(raw, "run_evidence")
        if _canonical_json_bytes(record) + b"\n" != raw:
            raise RunEvidenceError("run evidence is not canonical JSON")
        _validate_run_evidence_record(record, slot_id)
        state = controller.read_run_evidence(path, slot_id)
        if state.ambiguous:
            raise RunEvidenceError("existing run-evidence reader found ambiguity")
    except RunEvidenceError:
        raise
    except Exception as exc:
        raise RunEvidenceError(f"run evidence cannot be read:{exc}") from exc
    return PersistedRunEvidence(path, record, _sha256(raw), state)


def _existing_run_evidence(
    paths: EpochPaths, run_ids: Sequence[str]
) -> dict[str, PersistedRunEvidence]:
    records: dict[str, PersistedRunEvidence] = {}
    for run_id in run_ids:
        path = paths.run_evidence(run_id)
        if path.exists():
            records[run_id] = read_run_evidence_record(path, run_id)
        elif path.parent.exists():
            raise PairRecoveryError(f"half-written run directory:{run_id}")
    return records


def _validate_pair_reservation_order(
    snapshot: Any,
    run_ids: Sequence[str],
    *,
    require_complete: bool = False,
) -> None:
    if len(run_ids) != 2:
        raise PairRecoveryError("P01 must have exactly two reservation slots")
    observed = tuple(
        entry.get("slot_id")
        for entry in snapshot.entries
        if entry.get("transition") == "reservation"
    )
    expected_prefix = tuple(run_ids[: len(observed)])
    if len(observed) > len(run_ids) or observed != expected_prefix:
        raise PairRecoveryError(
            "persisted reservation order is not an exact OFF-to-ON prefix"
        )
    if require_complete and observed != tuple(run_ids):
        raise PairRecoveryError("both OFF-to-ON reservations are not durable")


def reconcile_pair_runtime(
    paths: EpochPaths,
    ledger: Any,
    seal: PersistedEpochOpen,
    run_ids: Sequence[str],
    dependencies: PairRuntimeDependencies,
    *,
    current_host: str,
    current_boot_id: str,
) -> dict[str, PersistedRunEvidence]:
    snapshot = ledger.load()
    _validate_pair_reservation_order(snapshot, run_ids)
    expected_slots = set(run_ids)
    if not set(snapshot.used_slots).issubset(expected_slots):
        raise PairRecoveryError("ledger contains a slot outside P01")
    evidence = _existing_run_evidence(paths, run_ids)

    # An unstarted reservation legitimately has no run record.  Validate that
    # case here, then let the existing controller reconcile every process,
    # host/boot, started-intent, and persisted-evidence surface it owns.
    reconciled_snapshot = replace(snapshot, used_slots=frozenset(evidence))
    try:
        live_processes = tuple(dependencies.process_scanner((paths.epoch_root,)))
        result = dependencies.reconciler(
            sealed_host=seal.record["observed_host_identity"],
            sealed_boot_id=seal.record["observed_boot_identity"],
            current_host=current_host,
            current_boot_id=current_boot_id,
            writer_lease=None,
            live_processes=live_processes,
            managed_roots=(paths.epoch_root,),
            ledger_snapshot=reconciled_snapshot,
            run_evidence=tuple(item.state for item in evidence.values()),
            controller_pid=os.getpid(),
        )
        result.require_pass()
    except Exception as exc:
        raise PairRecoveryError(f"fresh-start reconciliation blocked:{exc}") from exc

    for index, run_id in enumerate(run_ids):
        item = evidence.get(run_id)
        entries = _ledger_entries_for_slot(snapshot, run_id)
        outstanding = snapshot.outstanding_reservations.get(run_id)
        if item is None:
            if run_id in snapshot.started_unresolved:
                raise PairRecoveryError(f"started_unresolved:{run_id}")
            if run_id in snapshot.used_slots and outstanding is None:
                raise PairRecoveryError(f"ledger terminal state lacks run evidence:{run_id}")
            continue
        if not item.state.complete or item.record["terminal"] is None:
            raise PairRecoveryError(f"incomplete run evidence:{run_id}")
        if item.record["run_evidence"].get("run_countable") is not True:
            raise PairRecoveryError(f"terminal run is not countable:{run_id}")
        terminal = item.record["terminal"]
        transition = terminal["ledger_transition"]
        terminal_entries = [entry for entry in entries if entry.get("transition") == transition]
        if len(terminal_entries) != 1:
            raise PairRecoveryError(f"terminal ledger transition mismatch:{run_id}")
        ledger_terminal = terminal_entries[0]
        if ledger_terminal.get("entry_hash") != terminal.get("ledger_entry_sha256"):
            raise PairRecoveryError(f"terminal ledger hash mismatch:{run_id}")
        if transition == "settlement" and ledger_terminal.get("amount") != terminal.get("cost_usd"):
            raise PairRecoveryError(f"settled cost mismatch:{run_id}")
        if outstanding is not None or run_id in snapshot.started_unresolved:
            raise PairRecoveryError(f"terminal run remains outstanding:{run_id}")
        if index == 1 and run_ids[0] not in evidence:
            raise PairRecoveryError("ON terminal evidence exists before OFF")
    if run_ids[0] in evidence and run_ids[1] not in snapshot.used_slots:
        raise PairRecoveryError("OFF completed without a prior ON reservation")
    return evidence


def ensure_pair_reservations(
    ledger: Any,
    run_ids: Sequence[str],
    completed: Mapping[str, PersistedRunEvidence],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    if len(run_ids) != 2:
        raise PairRecoveryError("P01 must have exactly two reservation slots")
    _validate_pair_reservation_order(ledger.load(), run_ids)
    cap = Decimal(PER_INVOCATION_CAP_USD)
    records: list[Mapping[str, Any] | None] = []
    for run_id in run_ids:
        snapshot = ledger.load()
        reservation = _entry_for_transition(snapshot, run_id, "reservation")
        if run_id in completed:
            if reservation is None:
                raise PairRecoveryError(f"completed slot lacks reservation:{run_id}")
            records.append(reservation)
            continue
        outstanding = snapshot.outstanding_reservations.get(run_id)
        if outstanding is None:
            if run_id in snapshot.used_slots:
                raise PairRecoveryError(f"slot already consumed without terminal evidence:{run_id}")
            reservation = ledger.reserve(run_id, cap)
        elif outstanding != cap:
            raise PairRecoveryError(f"reservation cap mismatch:{run_id}")
        if run_id in ledger.load().started_unresolved:
            raise PairRecoveryError(f"started reservation cannot be resumed:{run_id}")
        records.append(reservation)

    snapshot = ledger.load()
    _validate_pair_reservation_order(snapshot, run_ids, require_complete=True)
    pending = [run_id for run_id in run_ids if run_id not in completed]
    for run_id in pending:
        if snapshot.outstanding_reservations.get(run_id) != cap:
            raise PairRecoveryError("both pending 2.0000000 reservations are not durable")
    reservation_entries = [
        entry for entry in snapshot.entries if entry.get("transition") == "reservation"
    ]
    if len(reservation_entries) > 2:
        raise PairRecoveryError("third/double pair reservation is forbidden")
    return records[0], records[1]


def ensure_execution_manifest_artifact(
    paths: EpochPaths, manifest: Mapping[str, Any]
) -> str:
    data = execution_builder.serialize_execution_manifest(manifest)
    digest = _sha256(data)
    if paths.execution_manifest.exists():
        existing = _secure_read(paths.execution_manifest, "execution_manifest")
        if existing != data:
            raise PairRecoveryError("persisted P01 execution manifest differs")
        return digest
    _write_bytes_create_once(paths.execution_manifest, data, "execution_manifest")
    return digest


def _arm_from_persisted(
    condition: str, run_id: str, persisted: PersistedRunEvidence
) -> ArmTerminal:
    terminal = persisted.record["terminal"]
    if not isinstance(terminal, Mapping):
        raise PairRecoveryError(f"run is not terminal:{run_id}")
    return ArmTerminal(
        condition=condition,
        run_id=run_id,
        disposition=terminal["disposition"],
        cost_usd=terminal["cost_usd"],
        evidence=persisted,
    )


def _run_arm(
    *,
    condition: str,
    run_id: str,
    execution_manifest: Mapping[str, Any],
    paths: EpochPaths,
    ledger: Any,
    dependencies: PairRuntimeDependencies,
) -> ArmTerminal:
    composition_result: Any | None = None

    def provider_executor(invocation: Any) -> Any:
        nonlocal composition_result
        composition_result = dependencies.composition_executor(
            ledger=ledger,
            slot_id=run_id,
            sealed_invocation_cap=Decimal(PER_INVOCATION_CAP_USD),
            policy=dependencies.policy,
            invocation=invocation,
            session_root=invocation.cwd,
        )
        provider_execution = getattr(composition_result, "provider_execution", None)
        if provider_execution is None:
            raise PairExecutionError("composition returned no provider execution")
        return provider_execution

    evidence = dependencies.run_slot_executor(
        execution_manifest,
        run_id,
        workspace_parent=paths.workspaces,
        reference_events=tuple(dependencies.reference_events),
        materializer=dependencies.materializer,
        executor=provider_executor,
        environment=dict(dependencies.environment),
        opaque_id_factory=dependencies.opaque_id_factory,
    )

    snapshot = ledger.load()
    reservation = _entry_for_transition(snapshot, run_id, "reservation")
    if reservation is None:
        raise PairExecutionError("runner returned without a durable reservation")
    start_entry = _entry_for_transition(snapshot, run_id, "provider-start-intent")
    terminal_record: Mapping[str, Any] | None = None

    if start_entry is not None:
        provider_execution = evidence.provider_execution
        cost = None if provider_execution is None else provider_execution.total_cost_usd
        if cost is None:
            persisted = write_run_evidence(
                paths.run_evidence(run_id),
                slot_id=run_id,
                reservation_entry=reservation,
                provider_start_entry=start_entry,
                terminal=None,
                evidence=evidence,
            )
            raise PairExecutionError(
                f"unknown provider charge; started reservation remains unresolved:{persisted.sha256}"
            )
        try:
            cost_text = compiler.canonical_usd_text(cost)
        except Exception as exc:
            write_run_evidence(
                paths.run_evidence(run_id),
                slot_id=run_id,
                reservation_entry=reservation,
                provider_start_entry=start_entry,
                terminal=None,
                evidence=evidence,
            )
            raise PairExecutionError("provider charge is not canonical USD") from exc
        try:
            settlement = ledger.settle(run_id, Decimal(cost_text))
        except Exception as exc:
            write_run_evidence(
                paths.run_evidence(run_id),
                slot_id=run_id,
                reservation_entry=reservation,
                provider_start_entry=start_entry,
                terminal=None,
                evidence=evidence,
            )
            raise PairExecutionError("settlement failed; next arm is blocked") from exc
        terminal_record = {
            "disposition": "settled",
            "cost_usd": cost_text,
            "ledger_transition": "settlement",
            "ledger_entry_sha256": settlement["entry_hash"],
        }
    else:
        try:
            release = ledger.release(run_id)
        except Exception as exc:
            raise PairExecutionError("unstarted reservation release failed") from exc
        terminal_record = {
            "disposition": "released",
            "cost_usd": compiler.CANONICAL_USD_ZERO,
            "ledger_transition": "release",
            "ledger_entry_sha256": release["entry_hash"],
        }

    persisted = write_run_evidence(
        paths.run_evidence(run_id),
        slot_id=run_id,
        reservation_entry=reservation,
        provider_start_entry=start_entry,
        terminal=terminal_record,
        evidence=evidence,
    )
    if evidence.run_countable is not True:
        raise PairExecutionError("terminal arm evidence is not countable; next arm is blocked")
    return _arm_from_persisted(condition, run_id, persisted)


def _pair_aggregate_record(
    *,
    manifest_sha256: str,
    execution_manifest_sha256: str,
    arms: Sequence[ArmTerminal],
    ledger: Any,
) -> dict[str, Any]:
    if len(arms) != 2 or [arm.condition for arm in arms] != ["OFF", "ON"]:
        raise PairRecoveryError("aggregate requires terminal OFF then ON evidence")
    if any(not arm.evidence.state.complete for arm in arms):
        raise PairRecoveryError("aggregate requires complete evidence for both arms")
    snapshot = ledger.load()
    if snapshot.outstanding_reservations or snapshot.started_unresolved:
        raise PairRecoveryError("aggregate cannot be written with outstanding reservations")
    costs = [compiler.parse_canonical_usd(arm.cost_usd) for arm in arms]
    pair_cost = sum(costs, Decimal("0"))
    remaining = Decimal(LEDGER_CAP_USD) - snapshot.lifetime_total_spend
    if remaining < 0:
        raise PairRecoveryError("ledger cap exceeded before aggregate")
    return {
        "schema": PAIR_AGGREGATE_SCHEMA,
        "epoch_id": EPOCH_ID,
        "sealed_manifest_sha256": manifest_sha256,
        "execution_manifest_sha256": execution_manifest_sha256,
        "pair_id": execution_builder.PAIR_ID,
        "task_id": execution_builder.TASK_ID,
        "arm_order": ["OFF", "ON"],
        "arms": [
            {
                "condition": arm.condition,
                "run_id": arm.run_id,
                "run_evidence_sha256": arm.evidence.sha256,
                "disposition": arm.disposition,
                "cost_usd": arm.cost_usd,
            }
            for arm in arms
        ],
        "ledger_terminal": {
            "entry_count": len(snapshot.entries),
            "terminal_hash": snapshot.previous_hash,
            "settled_spend_usd": compiler.canonical_usd_text(
                snapshot.lifetime_total_spend
            ),
            "outstanding_reservations_usd": compiler.canonical_usd_text(
                snapshot.outstanding_total
            ),
            "started_unresolved_usd": compiler.canonical_usd_text(
                snapshot.conservative_unresolved_debit
            ),
        },
        "total_pair_cost_usd": compiler.canonical_usd_text(pair_cost),
        "remaining_headroom_usd": compiler.canonical_usd_text(remaining),
        "claim_boundary": "OBSERVED_COMPLETE_PAIR_EVIDENCE_ONLY",
    }


def write_pair_aggregate(path: Path, record: Mapping[str, Any]) -> Mapping[str, Any]:
    if path.exists():
        raw = _secure_read(path, "pair_aggregate")
        existing = _load_unique_json(raw, "pair_aggregate")
        if _canonical_json_bytes(existing) + b"\n" != raw or existing != record:
            raise PairRecoveryError("existing P01 aggregate differs from observed state")
        return existing
    _write_json_create_once(path, record, "pair_aggregate")
    return _load_unique_json(_secure_read(path, "pair_aggregate"), "pair_aggregate")


def execute_pair(
    paths: EpochPaths,
    *,
    paid_authorization: Mapping[str, Any] | None,
    dependencies: PairRuntimeDependencies,
    expected_manifest_sha256: str = SEALED_MANIFEST_SHA256,
) -> Mapping[str, Any]:
    """Execute or safely resume the exact P01 pair under the epoch lock."""

    lock = dependencies.lock_factory(paths.lock_path)
    lock.acquire()

    manifest, _manifest_bytes = _validate_authority_and_signoff(
        paths, expected_manifest_sha256=expected_manifest_sha256
    )
    validate_paid_pair_authorization(
        paid_authorization, manifest, expected_manifest_sha256
    )
    _validate_runtime_layout(paths)

    current_host = dependencies.host_provider()
    current_boot_id = dependencies.boot_provider()
    seal = ensure_epoch_open_seal(
        paths,
        expected_manifest_sha256,
        dependencies,
        current_host=current_host,
        current_boot_id=current_boot_id,
    )
    ledger = build_budget_ledger(paths, manifest, dependencies.ledger_factory)
    run_ids = execution_builder.p01_run_ids(expected_manifest_sha256)
    persisted = reconcile_pair_runtime(
        paths,
        ledger,
        seal,
        run_ids,
        dependencies,
        current_host=current_host,
        current_boot_id=current_boot_id,
    )
    ensure_pair_reservations(ledger, run_ids, persisted)

    execution_manifest = execution_builder.derive_p01_execution_manifest(
        manifest,
        expected_sealed_manifest_sha256=expected_manifest_sha256,
    )
    execution_sha256 = ensure_execution_manifest_artifact(paths, execution_manifest)

    arms: list[ArmTerminal] = []
    for condition, run_id in zip(execution_builder.ARM_ORDER, run_ids):
        existing = persisted.get(run_id)
        if existing is not None:
            arms.append(_arm_from_persisted(condition, run_id, existing))
            continue
        arm = _run_arm(
            condition=condition,
            run_id=run_id,
            execution_manifest=execution_manifest,
            paths=paths,
            ledger=ledger,
            dependencies=dependencies,
        )
        arms.append(arm)
        persisted[run_id] = arm.evidence

    aggregate = _pair_aggregate_record(
        manifest_sha256=expected_manifest_sha256,
        execution_manifest_sha256=execution_sha256,
        arms=arms,
        ledger=ledger,
    )
    return write_pair_aggregate(paths.pair_aggregate, aggregate)


__all__ = [
    "CANONICAL_EPOCH_ROOT",
    "EPOCH_ID",
    "EPOCH_OPEN_SCHEMA",
    "EpochOpenError",
    "EpochPaths",
    "LEDGER_CAP_USD",
    "PAIR_AGGREGATE_SCHEMA",
    "PAIR_MAX_SPEND_USD",
    "PAID_PAIR_AUTHORIZATION_SCHEMA",
    "PER_INVOCATION_CAP_USD",
    "PairDriverError",
    "PairExecutionError",
    "PairRecoveryError",
    "PairRuntimeDependencies",
    "PaidAuthorizationError",
    "PersistedRunEvidence",
    "RUN_EVIDENCE_SCHEMA",
    "RunEvidenceError",
    "SEALED_MANIFEST_SHA256",
    "build_budget_ledger",
    "ensure_epoch_open_seal",
    "ensure_pair_reservations",
    "execute_pair",
    "load_epoch_open_seal",
    "read_run_evidence_record",
    "reconcile_pair_runtime",
    "validate_paid_pair_authorization",
    "write_pair_aggregate",
    "write_run_evidence",
]
