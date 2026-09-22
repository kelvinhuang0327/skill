#!/usr/bin/env python3
"""The single sanctioned composition entrypoint for one reserved provider slot.

Card A (``epoch_controller.py``) owns reservation, start-intent, and
settlement authority.  This module composes that authority with the
runner-owned ``ModelInvocation`` request shape and the
``claude_executor``-owned provider process boundary. It verifies the existing
controller handoff, wires the sanctioned launch path, and asks Card A to settle whatever terminal cost the provider actually
reported. The runner owns purity and final countability.

The call chain this module owns end to end:

    epoch_controller reservation / start-intent authority
    -> this module's SandboxedProviderLauncher (the only injectable launch)
    -> claude_executor.ClaudeExecutor (logical preflight, JSONL parsing)
    -> epoch_controller.start_reserved_provider (durable intent, then version probe)
    -> epoch_controller.spawn_sandboxed_provider (sandbox-exec, FD 9 closed)
    -> pinned provider binary

It never implements v3 execution projection, a full epoch scheduler/driver,
or real provider/model execution: every caller supplies an offline local
stub or fake, and this module never invokes ``subprocess.Popen`` itself --
the only launch it can reach goes through ``epoch_controller``'s mandatory
sandbox helper, which has no unsandboxed fallback.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import weakref
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence


def _load_sibling(module_name: str, filename: str) -> Any:
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"required composition sibling is missing: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# claude_executor.py's own source does a plain, hardcoded ``import runner``
# (not a parameterized sibling load), so its ModelInvocation/isinstance
# check is always bound to sys.modules["runner"] specifically -- this must
# load under that exact name, or a ModelInvocation built from this module's
# copy would fail ClaudeExecutor's isinstance check against its own copy.
runner = _load_sibling("runner", "runner.py")
controller = _load_sibling("fable_ablation_epoch_controller", "epoch_controller.py")
claude = _load_sibling("fable_ablation_claude_executor", "claude_executor.py")
_usd = controller._usd
# Process-local receipts are issued only after the sanctioned executor has
# returned. A caller-constructed dataclass or copied ledger record cannot
# claim that a provider was actually executed by this entrypoint.
_EXECUTED_RESULTS: Any = weakref.WeakSet()


class CompositionError(RuntimeError):
    """The sanctioned provider composition cannot be built or trusted."""


def _invocation_sha256(invocation: Any) -> str:
    if not isinstance(invocation, runner.ModelInvocation):
        raise CompositionError("canonical_model_invocation_required")
    raw = json.dumps(invocation.model_visible_record(), sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ControllerSlotAuthority:
    """References to an existing Card A lifecycle, never a reservation factory.

    The controller caller must already own the lock, validate the manifest,
    admit the whole pair, observe its ledger, and enter RUNNING.  Every use
    re-proves those facts using Card A; arbitrary ledger/slot/cap arguments
    cannot manufacture an eligible launch.  The invocation binding is for
    one opaque workspace only, without implementing v3 execution projection.
    """

    lock: Any
    lifecycle: Any
    manifest_path: Path
    ledger: Any
    pair_slot_ids: tuple[str, str]
    slot_id: str
    invocation: Any
    _pid: int = field(init=False, repr=False)
    _manifest_sha256: str = field(init=False, repr=False)
    _invocation_digest: str = field(init=False, repr=False)
    _ledger_path: str = field(init=False, repr=False)
    _ledger_identity: tuple[int, int] | None = field(init=False, repr=False)
    _lock_identity: tuple[int, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.lifecycle) is not controller.LifecycleMachine:
            raise CompositionError("controller_lifecycle_required")
        if type(self.ledger) is not controller.BudgetLedger:
            raise CompositionError("controller_budget_ledger_required")
        self._verify_lock()
        metadata = os.fstat(controller.LOCK_FD)
        object.__setattr__(self, "_lock_identity", (metadata.st_dev, metadata.st_ino))
        object.__setattr__(self, "_pid", os.getpid())
        object.__setattr__(self, "_manifest_sha256", self.lifecycle.authority_sha256)
        object.__setattr__(self, "_invocation_digest", _invocation_sha256(self.invocation))
        object.__setattr__(self, "_ledger_path", str(self.ledger.path.resolve()))
        object.__setattr__(self, "_ledger_identity", self.ledger.load().file_identity)
        self.verify(require_unstarted=True)

    def _verify_lock(self) -> None:
        if (type(self.lock) is not controller.EpochLock or not self.lock.acquired
                or not controller._PROCESS_LOCK_ACQUIRED):
            raise CompositionError("acquired_controller_lock_required")
        controller.validate_lock_metadata(os.fstat(controller.LOCK_FD), os.lstat(self.lock.path))

    def verify(self, *, require_unstarted: bool) -> tuple[Mapping[str, Any], Any]:
        self._verify_lock()
        metadata = os.fstat(controller.LOCK_FD)
        if os.getpid() != self._pid or (metadata.st_dev, metadata.st_ino) != self._lock_identity:
            raise CompositionError("controller_lock_identity_changed")
        if (self.lifecycle.state is not controller.LifecycleState.RUNNING
                or not self.lifecycle.durable_reservation_or_start_seen):
            raise CompositionError("validated_running_controller_lifecycle_required")
        self.lifecycle.verify_authority(self._manifest_sha256)
        manifest = controller.load_manifest_after_lock(
            self.manifest_path, expected_sha256=self._manifest_sha256
        )
        if manifest["publication_gate"]["status"] != "PASS":
            raise CompositionError("manifest_authority_incomplete")
        if _invocation_sha256(self.invocation) != self._invocation_digest:
            raise CompositionError("bound_invocation_changed")
        root = controller.validate_opaque_session_root(self.invocation.cwd)
        if self.invocation.task_visible_run_id != root.name:
            raise CompositionError("opaque_run_identity_mismatch")
        expected_ledger = controller.budget_ledger_from_manifest(self.ledger.path, manifest)
        if (str(self.ledger.path.resolve()) != self._ledger_path
                or self.ledger.lifetime_hard_cap != expected_ledger.lifetime_hard_cap):
            raise CompositionError("manifest_ledger_authority_mismatch")
        snapshot = self.ledger.load()
        if snapshot.file_identity is None or snapshot.file_identity != self._ledger_identity:
            raise CompositionError("bound_ledger_identity_changed_or_missing")
        cap = Decimal(manifest["budget"]["per_invocation_cap_usd"])
        if (len(self.pair_slot_ids) != 2 or len(set(self.pair_slot_ids)) != 2
                or self.slot_id not in self.pair_slot_ids
                or cap * 2 != Decimal(manifest["budget"]["pair_reservation_usd"])):
            raise CompositionError("whole_pair_admission_required")
        reservations = []
        for arm in self.pair_slot_ids:
            records = [e for e in snapshot.entries if e["slot_id"] == arm]
            if not records or records[0]["transition"] != "reservation":
                raise CompositionError("whole_pair_reservation_missing")
            if Decimal(records[0]["amount"]) != cap:
                raise CompositionError("existing_reservation_does_not_match_sealed_cap")
            if any(e["transition"] == "release" for e in records):
                raise CompositionError("whole_pair_reservation_released")
            reservations.append(records[0])
        if reservations[1]["sequence"] != reservations[0]["sequence"] + 1:
            raise CompositionError("whole_pair_reservation_not_admitted_together")
        if any(e["transition"] == "provider-start-intent"
               and e["slot_id"] in self.pair_slot_ids
               and e["sequence"] < reservations[1]["sequence"] for e in snapshot.entries):
            raise CompositionError("whole_pair_must_precede_either_start")
        if require_unstarted:
            if self.slot_id in snapshot.started_unresolved:
                raise CompositionError("slot_already_started_no_silent_rerun")
            if self.slot_id not in snapshot.outstanding_reservations:
                raise CompositionError("slot_already_consumed_no_silent_rerun")
        runtime = manifest["provider_runtime"]
        if (runtime["status"] != "PINNED" or not self.invocation.argv
                or self.invocation.argv[0] != runtime["binary_path"]
                or controller.sha256_file(runtime["binary_path"]) != runtime["sha256"]):
            raise CompositionError("bound_provider_runtime_identity_mismatch")
        # Sandbox paths are fixed Card A authorities, never launch overrides.
        sandbox = manifest["lock_semantics"]["tools"]["sandbox-exec"]
        if (sandbox["status"] != "PINNED"
                or sandbox["binary_path"] != str(controller.SANDBOX_EXEC_PATH)
                or controller.sha256_file(controller.SANDBOX_EXEC_PATH) != sandbox["sha256"]):
            raise CompositionError("bound_sandbox_executable_mismatch")
        if (manifest["runtime_containment"]["status"] != "PINNED"
                or manifest["runtime_containment"]["policy_sha256"]
                != controller.sha256_file(controller.SANDBOX_PROFILE_PATH)):
            raise CompositionError("bound_sandbox_profile_mismatch")
        components = manifest["final_runtime_authority"]["components"]
        for name, path in (("runner", Path(runner.__file__)),
                           ("controller", Path(controller.__file__)),
                           ("sandbox", controller.SANDBOX_PROFILE_PATH)):
            if components[name]["sha256"] != controller.sha256_file(path):
                raise CompositionError(f"bound_runtime_component_mismatch:{name}")
        if manifest["provider_adapter"]["sha256"] != controller.sha256_file(claude.__file__):
            raise CompositionError("bound_provider_adapter_mismatch")
        return manifest, snapshot


@dataclass
class SandboxedProviderLauncher:
    """The only sanctioned launch path, injected as ``ClaudeExecutor.launch``.

    Turns the *logical* provider argv ``ClaudeExecutor`` hands it into the
    *physical* sandbox-prefixed command via Card A, records the durable
    provider-start intent before a process can exist, and refuses every
    launch shape that would weaken FD, session, or shell containment.
    """

    authority: ControllerSlotAuthority
    observed_identity: Any | None = field(default=None, init=False)
    start_intent_record: Mapping[str, Any] | None = field(default=None, init=False)
    launch_count: int = field(default=0, init=False)
    version_probe_called: bool = field(default=False, init=False)

    def identity_record(self) -> Mapping[str, Any] | None:
        """Return the observed sandbox identity, or None if nothing launched."""

        if self.observed_identity is None:
            return None
        return self.observed_identity.as_record()

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: Any = None,
        stdout: Any = None,
        stderr: Any = None,
        cwd: Any = None,
        env: Mapping[str, str] | None = None,
        shell: bool = False,
        close_fds: bool = True,
        start_new_session: bool = True,
        **unsupported: Any,
    ) -> Any:
        if unsupported:
            raise CompositionError(
                "unsupported launch options: " + ",".join(sorted(unsupported))
            )
        # These are not merely defaults to honour: a provider launched with a
        # shell, with inherited descriptors, or inside the caller's own
        # session would break the lock and evidence boundaries outright.
        if shell:
            raise CompositionError("provider_launch_must_not_use_a_shell")
        if not close_fds:
            raise CompositionError("provider_launch_must_close_inherited_descriptors")
        if not start_new_session:
            raise CompositionError("provider_launch_must_start_a_new_session")
        if self.launch_count:
            raise CompositionError("provider_slot_already_launched_no_silent_rerun")

        invocation = self.authority.invocation
        if tuple(argv) != invocation.argv or dict(env or {}) != dict(invocation.environment):
            raise CompositionError("provider_request_must_match_bound_invocation")
        root = controller.validate_opaque_session_root(invocation.cwd)
        if cwd is None or Path(cwd).resolve(strict=False) != root:
            raise CompositionError("provider_cwd_must_be_the_bound_session_root")
        self.launch_count += 1
        return self._spawn(argv, stdin=stdin, stdout=stdout, stderr=stderr)

    def probe_version(self) -> Any:
        """Even the version child is sandboxed and follows durable start intent."""
        if self.version_probe_called or self.launch_count:
            raise CompositionError("provider_version_probe_already_consumed")
        self.version_probe_called = True
        argv = (self.authority.invocation.argv[0], "--version")
        process = self._spawn(argv, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)

    def _spawn(self, argv: Sequence[str], **streams: Any) -> Any:
        if type(self.authority) is not ControllerSlotAuthority:
            raise CompositionError("controller_slot_authority_required")
        manifest, snapshot = self.authority.verify(
            require_unstarted=self.start_intent_record is None
        )
        invocation = self.authority.invocation
        self.observed_identity = controller.observe_sandboxed_command(
            invocation.argv, session_root=invocation.cwd
        )
        options = dict(session_root=invocation.cwd,
                       environment=dict(invocation.environment), **streams)
        if self.start_intent_record is not None:
            if (self.start_intent_record not in snapshot.entries
                    or self.authority.slot_id not in snapshot.started_unresolved):
                raise CompositionError("durable_start_intent_missing_or_already_settled")
            return controller.spawn_sandboxed_provider(argv, **options)
        try:
            return controller.start_reserved_provider(
                self.authority.ledger, self.authority.slot_id,
                manifest["budget"]["per_invocation_cap_usd"], argv, **options
            )
        finally:
            self.start_intent_record = _latest_start_intent(
                self.authority.ledger, self.authority.slot_id
            )


def _latest_start_intent(ledger: Any, slot_id: str) -> Mapping[str, Any] | None:
    try:
        snapshot = ledger.load()
    except Exception:
        return None
    for entry in reversed(snapshot.entries):
        if (
            entry.get("transition") == "provider-start-intent"
            and entry.get("slot_id") == slot_id
        ):
            return entry
    return None


def _canonical_cap(sealed_invocation_cap: Decimal | str | int) -> str:
    try:
        return _usd.canonical_usd_text(sealed_invocation_cap)
    except _usd.CanonicalUsdError as exc:
        raise CompositionError(
            f"sealed invocation cap is not canonical persisted USD: {exc}"
        ) from exc


def _resolve_terminal_cost_usd(events: Sequence[Mapping[str, Any]]) -> Decimal | None:
    """Extract the sole unambiguous terminal cost, or None when unresolved.

    Zero or multiple ``result`` events, or a missing/null/non-canonical
    ``total_cost_usd``, are all left unresolved rather than coerced into a
    settleable figure: an unknown terminal cost must retain the reservation
    and stay fail-closed, never be guessed as zero or as the sealed cap.
    """

    result_events = [
        event
        for event in events
        if isinstance(event, Mapping) and event.get("type") == "result"
    ]
    if len(result_events) != 1:
        return None
    cost = result_events[0].get("total_cost_usd")
    if not _usd.is_canonical_usd(cost):
        return None
    return _usd.parse_canonical_usd(cost)


@dataclass(frozen=True, eq=False)
class CompositionResult:
    """Everything the composed slot observed, with both argv lanes separate."""

    provider_execution: Any
    logical_argv: tuple[str, ...]
    physical_argv: tuple[str, ...] | None
    sandbox_identity: Mapping[str, Any] | None
    reservation_record: Mapping[str, Any] | None
    start_intent_record: Mapping[str, Any] | None
    settlement_record: Mapping[str, Any] | None
    authority: ControllerSlotAuthority
    sealed_invocation_cap_usd: str
    slot_id: str

    def __iter__(self) -> Any:
        return iter(self.provider_execution)

    @property
    def settlement_authority(self) -> Any:
        return runner.evaluate_settlement_authority(
            executor_called=True, executor_error=None, slot_id=self.slot_id,
            invocation=self.authority.invocation, composition_result=self
        )

    def verify_settlement(self, slot_id: str, invocation: Any) -> list[str]:
        if self not in _EXECUTED_RESULTS:
            return ["sanctioned_execution_receipt_required"]
        reasons = []
        if (slot_id != self.slot_id or slot_id != self.authority.slot_id
                or _invocation_sha256(invocation) != self.authority._invocation_digest):
            return ["settlement_execution_identity_mismatch"]
        manifest, snapshot = self.authority.verify(require_unstarted=False)
        if self.logical_argv != invocation.argv or self.provider_execution.argv != invocation.argv:
            reasons.append("execution_argv_mismatch")
        runtime = manifest["provider_runtime"]
        if (self.provider_execution.returncode != 0 or self.provider_execution.provider_identity
                != f"{runtime['binary_path']}@{runtime['version']}"):
            reasons.append("execution_runtime_identity_unresolved")
        observed = controller.observe_sandboxed_command(invocation.argv, session_root=invocation.cwd)
        if self.sandbox_identity != observed.as_record() or self.physical_argv != observed.physical_argv:
            reasons.append("sanctioned_sandbox_identity_unresolved")
        for label, record, transition in (
            ("reservation", self.reservation_record, "reservation"),
            ("start_intent", self.start_intent_record, "provider-start-intent"),
            ("settlement", self.settlement_record, "settlement"),
        ):
            if record is None:
                reasons.append(f"{label}_unresolved")
            elif (record not in snapshot.entries or record.get("slot_id") != slot_id
                  or record.get("transition") != transition):
                reasons.append(f"{label}_identity_or_durability_mismatch")
        cost = _resolve_terminal_cost_usd(list(self.provider_execution))
        if cost is None:
            reasons.append("terminal_cost_unresolved")
        elif self.settlement_record is not None and Decimal(self.settlement_record["amount"]) != cost:
            reasons.append("settlement_terminal_cost_mismatch")
        if any(event.get("is_error") is True for event in self.provider_execution):
            reasons.append("provider_terminal_error")
        if self.settlement_record is not None and self.start_intent_record is not None:
            if self.settlement_record["sequence"] <= self.start_intent_record["sequence"]:
                reasons.append("settlement_precedes_execution")
        return reasons

    def as_record(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "execution_identity": {
                "manifest_sha256": self.authority._manifest_sha256,
                "invocation_sha256": self.authority._invocation_digest,
                "ledger_file_identity": list(self.authority._ledger_identity),
            },
            "sealed_invocation_cap_usd": self.sealed_invocation_cap_usd,
            "logical_argv": list(self.logical_argv),
            "physical_argv": (
                None if self.physical_argv is None else list(self.physical_argv)
            ),
            "logical_argv_authority": "runner.ModelInvocation",
            "physical_argv_authority": "epoch_controller.build_sandboxed_command",
            "sandbox_identity": (
                None if self.sandbox_identity is None else dict(self.sandbox_identity)
            ),
            "reservation_record": (
                None if self.reservation_record is None else dict(self.reservation_record)
            ),
            "start_intent_record": (
                None if self.start_intent_record is None else dict(self.start_intent_record)
            ),
            "settlement_record": (
                None if self.settlement_record is None else dict(self.settlement_record)
            ),
            "settlement_authority": self.settlement_authority.as_record(),
        }


def execute_reserved_provider_slot(
    *,
    authority: ControllerSlotAuthority,
    invocation: Any,
) -> CompositionResult:
    """Consume a proven Card A handoff; never create its own reservation.

    Both provider children (version and prompt) use Card A's sandbox and
    exclude FD 9.  The first child follows durable start intent.  No caller
    can override the manifest's cap, binary identity, or sandbox authority.
    Failures retain their reservation unless a unique known cost can settle.
    """
    if type(authority) is not ControllerSlotAuthority:
        raise CompositionError("controller_slot_authority_required")
    manifest, snapshot = authority.verify(require_unstarted=True)
    if _invocation_sha256(invocation) != authority._invocation_digest:
        raise CompositionError("bound_invocation_mismatch")
    cap_text = _canonical_cap(manifest["budget"]["per_invocation_cap_usd"])
    reservation_record = next(e for e in snapshot.entries
                              if e["slot_id"] == authority.slot_id and e["transition"] == "reservation")
    launcher = SandboxedProviderLauncher(authority)
    runtime = manifest["provider_runtime"]
    executor = claude.ClaudeExecutor(
        runtime["binary_path"], runtime["version"],
        launch=launcher, version_probe=launcher.probe_version,
    )
    try:
        execution = executor(invocation)
    except (claude.ProviderProcessError, claude.ProviderOutputError) as exc:
        # Failure/countability never erases a known provider charge.  Strict
        # parsing keeps ambiguous or unreadable costs reserved, never zero.
        try:
            cost = _resolve_terminal_cost_usd(claude._parse_required_jsonl(exc.stdout, exc.stderr))
        except claude.ProviderOutputError:
            cost = None
        if cost is not None:
            authority.ledger.settle(authority.slot_id, cost)
        raise

    terminal_cost = _resolve_terminal_cost_usd(list(execution))
    settlement_record = None
    if terminal_cost is not None:
        settlement_record = authority.ledger.settle(authority.slot_id, terminal_cost)
    result = CompositionResult(
        provider_execution=execution,
        logical_argv=tuple(invocation.argv),
        physical_argv=launcher.observed_identity.physical_argv,
        sandbox_identity=launcher.identity_record(),
        reservation_record=reservation_record,
        start_intent_record=launcher.start_intent_record,
        settlement_record=settlement_record,
        authority=authority,
        sealed_invocation_cap_usd=cap_text,
        slot_id=authority.slot_id,
    )
    _EXECUTED_RESULTS.add(result)
    return result


__all__ = [
    "CompositionError",
    "CompositionResult",
    "ControllerSlotAuthority",
    "SandboxedProviderLauncher",
    "execute_reserved_provider_slot",
]
