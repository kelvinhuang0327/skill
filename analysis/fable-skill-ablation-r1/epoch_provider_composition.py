#!/usr/bin/env python3
"""The single production composition entrypoint for a paid provider slot.

Nothing else in the runtime is allowed to launch a provider.  The call chain
this module owns is exactly:

    epoch/controller authority + reservation
    -> runner / ModelInvocation
    -> ClaudeExecutor logical provider preflight
    -> composition adapter (this module)
    -> epoch_controller.start_reserved_provider
    -> epoch_controller.spawn_sandboxed_provider
    -> sandbox-exec physical command
    -> pinned provider binary

The adapter never bypasses the durable ``provider-start-intent`` record or the
epoch budget reservation, and it never reaches ``subprocess.Popen`` directly:
the only launch it can perform goes through the mandatory sandbox helper,
which itself has no unsandboxed fallback.

This module composes; it does not decide.  It materializes no epoch, signs no
manifest, and invokes no provider unless a caller supplies a real reservation,
a real session root, and a real provider policy.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


def _load_sibling(module_name: str, filename: str, *aliases: str) -> Any:
    for name in (module_name, *aliases):
        existing = sys.modules.get(name)
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


harness = _load_sibling("fable_ablation_runner", "runner.py", "runner")
controller = _load_sibling(
    "fable_ablation_epoch_controller", "epoch_controller.py", "epoch_controller"
)
claude = _load_sibling(
    "fable_ablation_claude_executor", "claude_executor.py", "claude_executor"
)
_usd = harness._load_usd_authority()


class CompositionError(RuntimeError):
    """The sanctioned provider composition cannot be built or trusted."""


@dataclass
class SandboxedProviderLauncher:
    """The only sanctioned launch path, shaped as a ``popen_factory``.

    ``ClaudeExecutor`` hands this callable the *logical* provider argv after
    its own preflight.  The launcher turns that into the *physical*
    sandbox-prefixed command, records the durable start intent through the
    ledger, and only then allows a process to exist.
    """

    ledger: Any
    slot_id: str
    sealed_invocation_cap: Decimal
    session_root: Path
    profile_path: Path = field(default=controller.SANDBOX_PROFILE_PATH)
    sandbox_exec_path: Path = field(default=controller.SANDBOX_EXEC_PATH)
    observed_identity: Any | None = field(default=None, init=False)
    start_intent_record: Mapping[str, Any] | None = field(default=None, init=False)
    launch_count: int = field(default=0, init=False)

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
        # shell, with inherited descriptors, or inside the controller's own
        # session would break the lock and evidence boundaries outright.
        if shell:
            raise CompositionError("provider_launch_must_not_use_a_shell")
        if not close_fds:
            raise CompositionError("provider_launch_must_close_inherited_descriptors")
        if not start_new_session:
            raise CompositionError("provider_launch_must_start_a_new_session")
        if self.launch_count:
            raise CompositionError("provider_slot_already_launched_no_silent_rerun")

        root = controller.validate_opaque_session_root(self.session_root)
        if cwd is not None and Path(cwd).resolve(strict=False) != root:
            raise CompositionError("provider_cwd_must_be_the_bound_session_root")

        # Observe the physical lane before anything durable happens, so a
        # missing sandbox refuses the slot without debiting the epoch budget.
        self.observed_identity = controller.observe_sandboxed_command(
            argv,
            session_root=root,
            profile_path=self.profile_path,
            sandbox_exec_path=self.sandbox_exec_path,
        )
        self.launch_count += 1
        try:
            process = controller.start_reserved_provider(
                self.ledger,
                self.slot_id,
                self.sealed_invocation_cap,
                argv,
                session_root=root,
                environment=None if env is None else dict(env),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                profile_path=self.profile_path,
                sandbox_exec_path=self.sandbox_exec_path,
            )
        finally:
            # start_reserved_provider writes the intent before it spawns, so a
            # spawn failure must still leave that durable record visible.
            self.start_intent_record = _latest_start_intent(self.ledger, self.slot_id)
        return process


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


@dataclass(frozen=True)
class CompositionResult:
    """Everything the composed slot observed, with both argv lanes separate."""

    provider_execution: Any
    logical_argv: tuple[str, ...]
    physical_argv: tuple[str, ...] | None
    sandbox_identity: Mapping[str, Any] | None
    reservation_record: Mapping[str, Any] | None
    start_intent_record: Mapping[str, Any] | None
    sealed_invocation_cap_usd: str
    slot_id: str

    def as_record(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "sealed_invocation_cap_usd": self.sealed_invocation_cap_usd,
            "logical_argv": list(self.logical_argv),
            "physical_argv": (
                None if self.physical_argv is None else list(self.physical_argv)
            ),
            "logical_argv_authority": "runner.ModelInvocation/ClaudeProviderPolicy",
            "physical_argv_authority": "epoch_controller.build_sandboxed_command",
            "sandbox_identity": (
                None
                if self.sandbox_identity is None
                else dict(self.sandbox_identity)
            ),
            "reservation_record": (
                None
                if self.reservation_record is None
                else dict(self.reservation_record)
            ),
            "start_intent_record": (
                None
                if self.start_intent_record is None
                else dict(self.start_intent_record)
            ),
            "provider_execution": (
                None
                if self.provider_execution is None
                else self.provider_execution.as_record()
            ),
        }


def verify_sandbox_policy_binding(
    policy: Any,
    *,
    profile_path: str | os.PathLike[str] = controller.SANDBOX_PROFILE_PATH,
) -> str:
    """Require the policy identity to match the profile bytes that will load."""

    observed = claude.observed_sandbox_policy_identity(profile_path)
    if policy.sandbox_policy_identity != observed:
        raise CompositionError(
            "provider policy sandbox identity does not bind the observed "
            f"{Path(profile_path).name} bytes"
        )
    return observed


def build_composed_executor(
    policy: Any,
    launcher: SandboxedProviderLauncher,
    *,
    binary_identity_resolver: Callable[[str], Any] | None = None,
    adapter_identity_resolver: Callable[[], Any] | None = None,
    group_signal: Callable[[int, int], Any] = os.killpg,
) -> Any:
    """Bind the logical adapter to the one sanctioned physical launch path."""

    if not isinstance(launcher, SandboxedProviderLauncher):
        raise CompositionError("composition requires the sandboxed provider launcher")
    resolvers: dict[str, Any] = {}
    if binary_identity_resolver is not None:
        resolvers["binary_identity_resolver"] = binary_identity_resolver
    if adapter_identity_resolver is not None:
        resolvers["adapter_identity_resolver"] = adapter_identity_resolver
    return claude.ClaudeExecutor(
        policy=policy,
        popen_factory=launcher,
        group_signal=group_signal,
        sandbox_identity_resolver=launcher.identity_record,
        **resolvers,
    )


def execute_reserved_provider_slot(
    *,
    ledger: Any,
    slot_id: str,
    sealed_invocation_cap: Decimal | str | int,
    policy: Any,
    invocation: Any,
    session_root: str | os.PathLike[str],
    profile_path: str | os.PathLike[str] = controller.SANDBOX_PROFILE_PATH,
    sandbox_exec_path: str | os.PathLike[str] = controller.SANDBOX_EXEC_PATH,
    binary_identity_resolver: Callable[[str], Any] | None = None,
    adapter_identity_resolver: Callable[[], Any] | None = None,
    group_signal: Callable[[int, int], Any] = os.killpg,
) -> CompositionResult:
    """Run one reserved slot through the whole sanctioned composition chain."""

    cap_text = _canonical_cap(sealed_invocation_cap)
    cap = Decimal(cap_text)
    root = controller.validate_opaque_session_root(session_root)
    verify_sandbox_policy_binding(policy, profile_path=profile_path)

    reservation_record = _ensure_reservation(ledger, slot_id, cap)

    launcher = SandboxedProviderLauncher(
        ledger=ledger,
        slot_id=slot_id,
        sealed_invocation_cap=cap,
        session_root=root,
        profile_path=Path(profile_path),
        sandbox_exec_path=Path(sandbox_exec_path),
    )
    executor = build_composed_executor(
        policy,
        launcher,
        binary_identity_resolver=binary_identity_resolver,
        adapter_identity_resolver=adapter_identity_resolver,
        group_signal=group_signal,
    )
    execution = executor(invocation)
    identity = launcher.identity_record()
    return CompositionResult(
        provider_execution=execution,
        logical_argv=tuple(invocation.argv),
        physical_argv=(
            None
            if launcher.observed_identity is None
            else launcher.observed_identity.physical_argv
        ),
        sandbox_identity=identity,
        reservation_record=reservation_record,
        start_intent_record=launcher.start_intent_record,
        sealed_invocation_cap_usd=cap_text,
        slot_id=slot_id,
    )


def _canonical_cap(sealed_invocation_cap: Decimal | str | int) -> str:
    try:
        return _usd.canonical_usd_text(sealed_invocation_cap)
    except _usd.CanonicalUsdError as exc:
        raise CompositionError(
            f"sealed invocation cap is not canonical persisted USD: {exc}"
        ) from exc


def _ensure_reservation(ledger: Any, slot_id: str, cap: Decimal) -> Mapping[str, Any] | None:
    """Reserve the slot, or accept an existing unstarted reservation for it."""

    snapshot = ledger.load()
    outstanding = snapshot.outstanding_reservations.get(slot_id)
    if outstanding is None:
        if slot_id in snapshot.used_slots:
            raise CompositionError("slot_already_consumed_no_silent_rerun")
        return ledger.reserve(slot_id, cap)
    if outstanding != cap:
        raise CompositionError("existing_reservation_does_not_match_sealed_cap")
    if slot_id in snapshot.started_unresolved:
        raise CompositionError("slot_already_started_no_silent_rerun")
    for entry in reversed(snapshot.entries):
        if entry.get("transition") == "reservation" and entry.get("slot_id") == slot_id:
            return entry
    return None


__all__ = [
    "CompositionError",
    "CompositionResult",
    "SandboxedProviderLauncher",
    "build_composed_executor",
    "execute_reserved_provider_slot",
    "verify_sandbox_policy_binding",
]
