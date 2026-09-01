#!/usr/bin/env python3
"""The one-time, zero-turn P01 reference-init capture.

This module owns the special pre-treatment session that *produces*
``p01_reference_init.json``.  It is deliberately separate from the paid-pair
provider composition: the reference is not a treatment observation, not an
outcome, and not an OFF/ON self-reference.  It is only the provider
``system/init`` configuration inventory that
:func:`runner.parse_init_events` / :func:`runner.evaluate_purity` compare a
later paid run against.

The capture contract encoded here is hard:

    * an OFF-condition workspace built by the *same* canonical materializer
      the production pair uses;
    * the same ``runner.build_model_invocation``-visible surface;
    * no user task message and no model turn;
    * stream-json init capture only;
    * a maximum budget of exactly ``0.0000000``;
    * explicit provider binary identity verification;
    * no treatment carrier, no canonical epoch materialization, no lock,
      no reservation, and no ledger mutation.

Every abort condition writes no artifact at all.  The module contains the
only sanctioned reference-capture process-spawning code; the process boundary
is injected so offline acceptance can prove the whole contract without a real
provider call.
"""

from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
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
        raise ImportError(f"required reference-capture sibling is missing: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


production = _load_sibling(
    "fable_ablation_p01_production", "p01_production.py", "p01_production"
)
driver = production.driver
execution_builder = production.execution_builder
compiler = production.compiler
runner = production.runner
controller = production.controller
claude = production.claude


# The reference-init record contract has exactly one definition; it lives in
# :mod:`p01_production` because the production loader is its strictest
# consumer.  These names are re-exported so the capture surface is complete.
REFERENCE_INIT_SCHEMA = production.REFERENCE_INIT_SCHEMA
CAPTURE_MODE = production.REFERENCE_CAPTURE_MODE
REFERENCE_ARTIFACT_PATH = production.REFERENCE_ARTIFACT_PATH
canonical_reference_bytes = production.canonical_reference_bytes
reference_artifact_sha256 = production.reference_artifact_sha256
init_event_sha256 = production.init_event_sha256
validate_reference_record = production.validate_reference_record

REFERENCE_MAX_BUDGET_USD = production.REFERENCE_ZERO_USD
REFERENCE_MAX_TURNS = 0
REFERENCE_PROMPT = ""
REFERENCE_TIMEOUT_SECONDS = 120.0

# A failed physical capture must stay fail-closed *and* stay explainable.  The
# diagnostic below is deliberately a different schema from the reference-init
# record and names its own non-authority, so a failure description can never be
# read back as reference authority.
CAPTURE_FAILURE_DIAGNOSTIC_SCHEMA = "FABLE_ABLATION_P01_CAPTURE_FAILURE_DIAGNOSTIC/v1"
DIAGNOSTIC_AUTHORITY = "NON_AUTHORITATIVE"
DIAGNOSTIC_STDERR_PREFIX = "P01_REFERENCE_CAPTURE_FAILURE_DIAGNOSTIC"

_BUDGET_FLAG = "--max-budget-usd"
_TURNS_FLAG = "--max-turns"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_OID_RE = re.compile(r"[0-9a-f]{40}")


class ReferenceCaptureError(RuntimeError):
    """Base class for reference-capture failures."""


class ReferenceCaptureAbort(ReferenceCaptureError):
    """A capture precondition or observation failed; no artifact is written.

    ``diagnostic`` carries an explicitly non-authoritative description of a
    failed physical process when one is available.  It never affects the abort
    itself: the message, reason and detail are unchanged by its presence.
    """

    def __init__(
        self, reason: str, detail: str = "", diagnostic: Mapping = None
    ) -> None:
        super().__init__(reason if not detail else f"{reason}:{detail}")
        self.reason = reason
        self.detail = detail
        self.diagnostic = None if diagnostic is None else dict(diagnostic)


# --- Zero-budget invocation surface ----------------------------------------


def build_reference_argv(provider_executable: str) -> tuple:
    """Return the CTO argv with the budget and turn ceilings driven to zero.

    Every other element of the paid argv is preserved byte-for-byte so the
    reference init reflects the same configuration surface the paid run will
    later present.
    """

    if not isinstance(provider_executable, str) or not provider_executable:
        raise ReferenceCaptureAbort("INVALID_PROVIDER_EXECUTABLE")
    paid = list(claude.build_cto_argv(provider_executable))
    argv = list(paid)
    for flag, value in ((_BUDGET_FLAG, REFERENCE_MAX_BUDGET_USD), (_TURNS_FLAG, str(REFERENCE_MAX_TURNS))):
        if argv.count(flag) != 1:
            raise ReferenceCaptureAbort("PAID_ARGV_SHAPE_UNEXPECTED", flag)
        index = argv.index(flag)
        if index + 1 >= len(argv):
            raise ReferenceCaptureAbort("PAID_ARGV_SHAPE_UNEXPECTED", flag)
        argv[index + 1] = value
    if len(argv) != len(paid):
        raise ReferenceCaptureAbort("REFERENCE_ARGV_SHAPE_CHANGED")
    changed = [index for index, value in enumerate(argv) if value != paid[index]]
    expected_changes = {argv.index(_BUDGET_FLAG) + 1, argv.index(_TURNS_FLAG) + 1}
    if not set(changed).issubset(expected_changes):
        raise ReferenceCaptureAbort("REFERENCE_ARGV_CHANGED_UNRELATED_SURFACE")
    assert_zero_budget_argv(argv)
    return tuple(argv)


def assert_zero_budget_argv(argv: Sequence[str]) -> None:
    """Affirm the exact zero-budget capture configuration; absence never passes."""

    values = list(argv)
    if values.count(_BUDGET_FLAG) != 1 or values.count(_TURNS_FLAG) != 1:
        raise ReferenceCaptureAbort("ZERO_BUDGET_ARGV_AMBIGUOUS")
    budget = values[values.index(_BUDGET_FLAG) + 1]
    turns = values[values.index(_TURNS_FLAG) + 1]
    if budget != REFERENCE_MAX_BUDGET_USD:
        raise ReferenceCaptureAbort("MAX_BUDGET_NOT_EXACTLY_ZERO", budget)
    try:
        if Decimal(budget) != Decimal(0):
            raise ReferenceCaptureAbort("MAX_BUDGET_NOT_EXACTLY_ZERO", budget)
    except InvalidOperation as exc:
        raise ReferenceCaptureAbort("MAX_BUDGET_NOT_EXACTLY_ZERO", budget) from exc
    if turns != str(REFERENCE_MAX_TURNS):
        raise ReferenceCaptureAbort("MAX_TURNS_NOT_ZERO", turns)


def build_reference_invocation(
    slot: Any,
    workspace: Path,
    opaque_identity: str,
    *,
    environment: Mapping = None,
) -> Any:
    """Build the reference request through ``runner.build_model_invocation``.

    The only differences from the paid surface are the zero-budget argv and
    the deliberately empty prompt: no user task message is ever sent.
    """

    reference_slot = replace(
        slot, prompt=REFERENCE_PROMPT, command=build_reference_argv(slot.command[0])
    )
    invocation = runner.build_model_invocation(
        reference_slot, workspace, opaque_identity, environment=dict(environment or {})
    )
    if invocation.prompt != REFERENCE_PROMPT or invocation.prompt:
        raise ReferenceCaptureAbort("USER_TASK_MESSAGE_WOULD_BE_SENT")
    if slot.prompt and invocation.prompt == slot.prompt:
        raise ReferenceCaptureAbort("USER_TASK_MESSAGE_WOULD_BE_SENT")
    assert_zero_budget_argv(invocation.argv)
    return invocation


# --- Injected process boundary ---------------------------------------------


@dataclass(frozen=True)
class CaptureProcessRequest:
    """Everything the process boundary needs; nothing it may reinterpret."""

    logical_argv: tuple
    cwd: str
    stdin_bytes: bytes
    environment: Mapping
    timeout_seconds: float
    session_root: str
    profile_path: str
    sandbox_exec_path: str


@dataclass(frozen=True)
class CaptureProcessResult:
    """The unambiguous outcome of exactly one reference-capture process."""

    returncode: int
    stdout: bytes
    stderr: bytes = b""
    timed_out: bool = False
    physical_argv: tuple = ()


@dataclass(frozen=True)
class SandboxedZeroBudgetCapture:
    """The only sanctioned real reference-capture process boundary.

    It is never exercised by the offline acceptance suite.  Like the paid
    composition chain it refuses to exist without the mandatory sandbox: the
    physical command is built by ``epoch_controller.build_sandboxed_command``
    and there is no unsandboxed fallback.
    """

    def __call__(self, request: CaptureProcessRequest) -> CaptureProcessResult:
        physical = controller.build_sandboxed_command(
            request.logical_argv,
            session_root=request.session_root,
            profile_path=request.profile_path,
            sandbox_exec_path=request.sandbox_exec_path,
        )
        try:
            completed = subprocess.run(
                list(physical),
                input=request.stdin_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=request.cwd,
                env=dict(request.environment),
                shell=False,
                close_fds=True,
                check=False,
                timeout=request.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return CaptureProcessResult(
                returncode=-1,
                stdout=b"",
                stderr=b"",
                timed_out=True,
                physical_argv=tuple(physical),
            )
        except OSError as exc:
            raise ReferenceCaptureAbort("CAPTURE_PROCESS_SPAWN_FAILED", str(exc)) from exc
        return CaptureProcessResult(
            returncode=completed.returncode,
            stdout=completed.stdout or b"",
            stderr=completed.stderr or b"",
            timed_out=False,
            physical_argv=tuple(physical),
        )


def _diagnostic_stream(data: bytes) -> dict:
    """Describe one captured stream reversibly, with no truncation.

    Base64 keeps arbitrary bytes intact and newline-free, so a stream is
    recoverable exactly and a multi-line provider error cannot corrupt the
    single-line outer diagnostic.  The digest lets a reader prove the decode.
    """

    payload = b"" if data is None else bytes(data)
    return {
        "length": len(payload),
        "sha256": driver._sha256(payload),
        "base64": base64.b64encode(payload).decode("ascii"),
    }


def build_process_failure_diagnostic(result: CaptureProcessResult) -> dict:
    """Preserve exactly why one physical capture process failed.

    The record is intentionally limited to the process outcome the boundary
    already returned: return code, timeout state, the physical argv, and the
    two captured streams.  It carries no init event, no cost claim, and no
    invocation environment, so it can explain a sandbox/provider startup
    failure without ever being mistaken for reference authority.
    """

    return {
        "schema": CAPTURE_FAILURE_DIAGNOSTIC_SCHEMA,
        "authority": DIAGNOSTIC_AUTHORITY,
        "is_reference_authority": False,
        "returncode": result.returncode,
        "timed_out": bool(result.timed_out),
        "physical_argv": list(result.physical_argv),
        "stdout": _diagnostic_stream(result.stdout),
        "stderr": _diagnostic_stream(result.stderr),
    }


def canonical_diagnostic_text(diagnostic: Mapping) -> str:
    """Serialize a failure diagnostic deterministically as one line."""

    return json.dumps(dict(diagnostic), sort_keys=True, separators=(",", ":"))


def write_abort_report(stream: Any, exc: BaseException) -> None:
    """Write the unchanged abort line, then any failure diagnostic.

    The abort line is always written first and is unaffected by the presence
    of a diagnostic, so existing consumers of the outer stream keep reading
    exactly the same first line.  A diagnostic, when the abort carries one,
    is only ever an additional line.
    """

    stream.write(f"P01_REFERENCE_CAPTURE_ABORTED:{type(exc).__name__}:{exc}\n")
    diagnostic = getattr(exc, "diagnostic", None)
    if diagnostic is not None:
        stream.write(
            f"{DIAGNOSTIC_STDERR_PREFIX}:{canonical_diagnostic_text(diagnostic)}\n"
        )


# --- Zero-turn stream analysis ---------------------------------------------


@dataclass(frozen=True)
class ZeroTurnStream:
    """The only observations a zero-turn reference session may produce."""

    records: tuple
    init_event: Mapping
    terminal_result: Mapping
    model_turn_count: int
    assistant_message_count: int
    total_cost_usd: Decimal


def parse_stream_records(stdout: bytes) -> tuple:
    """Parse stream-json output strictly; ambiguity is never tolerated."""

    try:
        text = stdout.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ReferenceCaptureAbort("STREAM_NOT_UTF8", str(exc)) from exc
    records = []
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped, object_pairs_hook=driver._unique_object)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ReferenceCaptureAbort("MALFORMED_STREAM_RECORD", f"{index}:{exc}") from exc
        if not isinstance(value, Mapping):
            raise ReferenceCaptureAbort("MALFORMED_STREAM_RECORD", f"{index}:not_object")
        records.append(dict(value))
    if not records:
        raise ReferenceCaptureAbort("EMPTY_STREAM")
    return tuple(records)


def _is_assistant_record(record: Mapping) -> bool:
    if record.get("type") == "assistant":
        return True
    message = record.get("message")
    return isinstance(message, Mapping) and message.get("role") == "assistant"


def analyze_zero_turn_stream(records: Sequence[Mapping]) -> ZeroTurnStream:
    """Affirm a genuine zero-turn init-only session, or abort."""

    assistant = [record for record in records if _is_assistant_record(record)]
    if assistant:
        raise ReferenceCaptureAbort("ASSISTANT_MESSAGE_OBSERVED", str(len(assistant)))
    turns = [record for record in records if record.get("type") in ("user", "tool_use")]
    if turns:
        raise ReferenceCaptureAbort("MODEL_TURN_OBSERVED", str(len(turns)))

    init_indices = [
        index
        for index, record in enumerate(records)
        if record.get("type") == "system" and record.get("subtype") == "init"
    ]
    other_init_candidates = [
        index
        for index, record in enumerate(records)
        if index not in init_indices and record.get("subtype") == "init"
    ]
    if not init_indices:
        raise ReferenceCaptureAbort("MISSING_INIT_EVENT")
    if len(init_indices) > 1 or other_init_candidates:
        raise ReferenceCaptureAbort(
            "CONFLICTING_INIT_AUTHORITY",
            str(len(init_indices) + len(other_init_candidates)),
        )

    result_indices = [
        index for index, record in enumerate(records) if record.get("type") == "result"
    ]
    if len(result_indices) != 1:
        raise ReferenceCaptureAbort("AMBIGUOUS_PROCESS_OUTCOME", "terminal_result")
    terminal = records[result_indices[0]]
    if result_indices[0] != len(records) - 1:
        raise ReferenceCaptureAbort("AMBIGUOUS_PROCESS_OUTCOME", "result_not_last")
    if terminal.get("is_error", False) is not False:
        raise ReferenceCaptureAbort("AMBIGUOUS_PROCESS_OUTCOME", "result_reports_error")

    raw_turns = terminal.get("num_turns")
    if "num_turns" in terminal:
        if type(raw_turns) is not int or raw_turns != 0:
            raise ReferenceCaptureAbort("MODEL_TURN_OBSERVED", str(raw_turns))
    model_turn_count = 0

    if "total_cost_usd" not in terminal:
        raise ReferenceCaptureAbort("AMBIGUOUS_OBSERVED_COST", "absent")
    raw_cost = terminal.get("total_cost_usd")
    if raw_cost is None or isinstance(raw_cost, bool):
        raise ReferenceCaptureAbort("AMBIGUOUS_OBSERVED_COST", "not_a_number")
    try:
        cost = Decimal(str(raw_cost))
    except (InvalidOperation, ValueError) as exc:
        raise ReferenceCaptureAbort("AMBIGUOUS_OBSERVED_COST", str(raw_cost)) from exc
    if not cost.is_finite():
        raise ReferenceCaptureAbort("AMBIGUOUS_OBSERVED_COST", "not_finite")
    if cost != Decimal(0):
        raise ReferenceCaptureAbort("NONZERO_OBSERVED_COST", str(raw_cost))

    return ZeroTurnStream(
        records=tuple(records),
        init_event=copy.deepcopy(dict(records[init_indices[0]])),
        terminal_result=copy.deepcopy(dict(terminal)),
        model_turn_count=model_turn_count,
        assistant_message_count=0,
        total_cost_usd=cost,
    )


def validate_reference_init_authority(init_event: Mapping) -> Any:
    """Apply exactly the authority a later ``parse_init_events`` will need."""

    evidence = runner.parse_init_events(
        [copy.deepcopy(dict(init_event))], sorted(runner.REQUIRED_PURITY_DIMENSIONS)
    )
    if not evidence.init_event_found or not evidence.valid:
        raise ReferenceCaptureAbort("INVALID_INIT_EVENT", ",".join(evidence.errors))
    if not evidence.skills_resolved or not evidence.skill_identities:
        raise ReferenceCaptureAbort("MISSING_REQUIRED_INVENTORY", "skills")
    if runner.FABLE_SKILL_IDENTITY in evidence.skill_identities:
        raise ReferenceCaptureAbort("REFERENCE_CONTAINS_FABLE_METHOD")
    for dimension in sorted(runner.REQUIRED_PURITY_DIMENSIONS):
        if dimension not in evidence.inventories:
            raise ReferenceCaptureAbort("MISSING_REQUIRED_INVENTORY", dimension)
        if not evidence.inventories[dimension]:
            raise ReferenceCaptureAbort("MISSING_REQUIRED_INVENTORY", f"{dimension}:empty")
    return evidence


# --- Workspace and provider preconditions ----------------------------------


def assert_reference_workspace_has_no_carrier(workspace: Path) -> None:
    root = Path(workspace)
    carrier = root / production.CARRIER_DESTINATION
    if carrier.exists() or carrier.is_symlink():
        raise ReferenceCaptureAbort("REFERENCE_WORKSPACE_CONTAINS_FABLE_METHOD")
    for current_root, directories, filenames in os.walk(root, followlinks=False):
        for name in list(directories) + list(filenames):
            if name == runner.FABLE_SKILL_IDENTITY:
                relative = (Path(current_root) / name).relative_to(root)
                raise ReferenceCaptureAbort(
                    "REFERENCE_WORKSPACE_CONTAINS_FABLE_METHOD", str(relative)
                )


def verify_provider_binary_identity(
    expected: Mapping, *, provider_identity_resolver: Callable[[str], Any]
) -> Any:
    """Verify the exact pinned provider binary before any process may exist."""

    if not isinstance(expected, Mapping):
        raise ReferenceCaptureAbort("PROVIDER_BINARY_IDENTITY_MISMATCH", "no_pins")
    binary_path = expected.get("binary_path")
    digest = expected.get("sha256")
    version = expected.get("version")
    if not isinstance(binary_path, str) or not binary_path:
        raise ReferenceCaptureAbort("PROVIDER_BINARY_IDENTITY_MISMATCH", "binary_path")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ReferenceCaptureAbort("PROVIDER_BINARY_IDENTITY_MISMATCH", "sha256")
    if not isinstance(version, str) or not version.strip():
        raise ReferenceCaptureAbort("PROVIDER_BINARY_IDENTITY_MISMATCH", "version")
    try:
        identity = provider_identity_resolver(binary_path)
    except Exception as exc:
        raise ReferenceCaptureAbort(
            "PROVIDER_BINARY_IDENTITY_MISMATCH", f"unresolved:{exc}"
        ) from exc
    if getattr(identity, "sha256", None) != digest:
        raise ReferenceCaptureAbort("PROVIDER_BINARY_IDENTITY_MISMATCH", "sha256")
    if getattr(identity, "version", None) != version:
        raise ReferenceCaptureAbort("PROVIDER_BINARY_IDENTITY_MISMATCH", "version")
    if getattr(identity, "executable", None) != binary_path:
        raise ReferenceCaptureAbort("PROVIDER_BINARY_IDENTITY_MISMATCH", "executable")
    return identity


def verify_source_authority(
    repository: Path, *, authorized_head: str, authorized_tree: str
) -> tuple:
    """Refuse to capture from a source state other than the authorized one."""

    for label, value in (("head", authorized_head), ("tree", authorized_tree)):
        if not isinstance(value, str) or _GIT_OID_RE.fullmatch(value) is None:
            raise ReferenceCaptureAbort("SOURCE_AUTHORITY_MISMATCH", f"{label}_shape")
    try:
        observed_head = production._git_text(Path(repository), "rev-parse", "HEAD")
        observed_tree = production._git_text(Path(repository), "rev-parse", "HEAD^{tree}")
    except production.ProductionWiringError as exc:
        raise ReferenceCaptureAbort("SOURCE_AUTHORITY_MISMATCH", str(exc)) from exc
    if observed_head != authorized_head or observed_tree != authorized_tree:
        raise ReferenceCaptureAbort(
            "SOURCE_AUTHORITY_MISMATCH", f"{observed_head}:{observed_tree}"
        )
    return observed_head, observed_tree


# --- The capture ------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceCaptureRequest:
    """Every input the one-time real capture would need, stated explicitly."""

    execution_manifest: Mapping
    workspace_parent: Path
    hold_out_root: Path
    carrier: Any
    source_repository: Path
    authorized_head: str
    authorized_tree: str
    artifact_path: Path = REFERENCE_ARTIFACT_PATH
    expected_manifest_sha256: str = driver.SEALED_MANIFEST_SHA256
    environment: Mapping = field(default_factory=dict)
    timeout_seconds: float = REFERENCE_TIMEOUT_SECONDS
    profile_path: Path = controller.SANDBOX_PROFILE_PATH
    sandbox_exec_path: Path = controller.SANDBOX_EXEC_PATH
    opaque_id_factory: Callable[[], str] = None


@dataclass(frozen=True)
class ReferenceCaptureOutcome:
    """The observable result of a successful capture."""

    artifact_path: Path
    sha256: str
    record: Mapping
    workspace_path: str
    surface_audit: Any
    process_result: CaptureProcessResult


def build_reference_record(
    *,
    provider_identity: Any,
    invocation: Any,
    stream: ZeroTurnStream,
    expected_manifest_sha256: str,
    profile_path: Path,
) -> dict:
    """Build the minimal deterministic reference record; no outcome data."""

    try:
        sandbox_identity = claude.observed_sandbox_policy_identity(profile_path)
    except Exception as exc:
        raise ReferenceCaptureAbort("SANDBOX_POLICY_UNREADABLE", str(exc)) from exc
    prefix = claude.SANDBOX_POLICY_IDENTITY_PREFIX
    profile_sha256 = (
        sandbox_identity[len(prefix):] if sandbox_identity.startswith(prefix) else ""
    )
    if _SHA256_RE.fullmatch(profile_sha256) is None:
        raise ReferenceCaptureAbort("SANDBOX_POLICY_UNREADABLE", sandbox_identity)

    prompt_bytes = invocation.prompt.encode("utf-8")
    record = {
        "schema": REFERENCE_INIT_SCHEMA,
        "epoch_id": driver.EPOCH_ID,
        "pair_id": execution_builder.PAIR_ID,
        "task_id": execution_builder.TASK_ID,
        "capture_mode": CAPTURE_MODE,
        "sealed_manifest_sha256": expected_manifest_sha256,
        "provider_binary": {
            "executable": provider_identity.executable,
            "realpath": provider_identity.realpath,
            "sha256": provider_identity.sha256,
            "version": provider_identity.version,
        },
        "capture_configuration": {
            "argv": list(invocation.argv),
            "max_budget_usd": REFERENCE_MAX_BUDGET_USD,
            "max_turns": REFERENCE_MAX_TURNS,
            "prompt_sha256": driver._sha256(prompt_bytes),
            "prompt_length": len(prompt_bytes),
            "environment_keys": sorted(invocation.environment),
            "sandbox_profile_sha256": profile_sha256,
        },
        "init_event": copy.deepcopy(dict(stream.init_event)),
        "init_event_sha256": init_event_sha256(stream.init_event),
        "observed_model_turn_count": stream.model_turn_count,
        "observed_assistant_message_count": stream.assistant_message_count,
        "observed_total_cost_usd": compiler.canonical_usd_text(stream.total_cost_usd),
    }
    validate_reference_record(
        record, expected_sealed_manifest_sha256=expected_manifest_sha256
    )
    return record


def write_reference_artifact(path: Path, record: Mapping) -> str:
    """Persist the artifact exactly once; an existing target is never replaced."""

    data = canonical_reference_bytes(record)
    target = Path(path)
    try:
        digest = driver._write_bytes_create_once(target, data, "p01_reference_init")
    except driver.PairDriverError as exc:
        raise ReferenceCaptureAbort("ARTIFACT_WRITE_REFUSED", str(exc)) from exc
    readback = driver._secure_read(target, "p01_reference_init")
    if readback != data or driver._sha256(readback) != digest:
        raise ReferenceCaptureAbort("ARTIFACT_READBACK_MISMATCH")
    return digest


def capture_reference_init(
    request: ReferenceCaptureRequest,
    *,
    process_boundary: Callable[[CaptureProcessRequest], CaptureProcessResult],
    provider_identity_resolver: Callable[[str], Any] = None,
) -> ReferenceCaptureOutcome:
    """Run the one-time zero-turn init-only capture, or abort writing nothing."""

    if process_boundary is None:
        raise ReferenceCaptureAbort("PROCESS_BOUNDARY_REQUIRED")
    resolver = (
        claude.resolve_provider_binary_identity
        if provider_identity_resolver is None
        else provider_identity_resolver
    )
    artifact = Path(request.artifact_path)
    if artifact.exists() or artifact.is_symlink():
        raise ReferenceCaptureAbort("ARTIFACT_ALREADY_EXISTS", str(artifact))

    verify_source_authority(
        request.source_repository,
        authorized_head=request.authorized_head,
        authorized_tree=request.authorized_tree,
    )

    manifest = request.execution_manifest
    authority = manifest.get("authority") if isinstance(manifest, Mapping) else None
    if not isinstance(authority, Mapping):
        raise ReferenceCaptureAbort("EXECUTION_MANIFEST_INVALID", "authority")
    if authority.get("sealed_manifest_sha256") != request.expected_manifest_sha256:
        raise ReferenceCaptureAbort("EXECUTION_MANIFEST_INVALID", "sealed_manifest_sha256")
    provider_pins = authority.get("provider_runtime")
    identity = verify_provider_binary_identity(
        provider_pins, provider_identity_resolver=resolver
    )

    off_run_id = execution_builder.p01_run_ids(request.expected_manifest_sha256)[0]
    slot = runner.select_manifest_slot(manifest, off_run_id)
    if slot.condition != "OFF" or slot.expected_fable_engaged:
        raise ReferenceCaptureAbort("REFERENCE_CONDITION_MUST_BE_OFF", slot.condition)

    workspace, opaque_identity = runner.create_condition_neutral_workspace(
        request.workspace_parent, request.opaque_id_factory
    )
    production.materialize_p01_workspace(
        runner.WorkspacePlan(
            slot=slot,
            workspace_path=str(workspace.resolve()),
            opaque_identity=opaque_identity,
        ),
        carrier=request.carrier,
        hold_out_root=request.hold_out_root,
    )
    assert_reference_workspace_has_no_carrier(workspace)

    invocation = build_reference_invocation(
        slot, workspace, opaque_identity, environment=request.environment
    )
    audit = runner.audit_model_visible_surfaces(
        invocation, workspace, slot.forbidden_model_tokens
    )
    if not audit.passed:
        raise ReferenceCaptureAbort(
            "REFERENCE_SURFACE_AUDIT_FAILED", ",".join(audit.leaks + audit.errors)
        )

    result = process_boundary(
        CaptureProcessRequest(
            logical_argv=tuple(invocation.argv),
            cwd=invocation.cwd,
            stdin_bytes=invocation.prompt.encode("utf-8"),
            environment=dict(invocation.environment),
            timeout_seconds=request.timeout_seconds,
            session_root=invocation.cwd,
            profile_path=str(request.profile_path),
            sandbox_exec_path=str(request.sandbox_exec_path),
        )
    )
    if not isinstance(result, CaptureProcessResult):
        raise ReferenceCaptureAbort("AMBIGUOUS_PROCESS_OUTCOME", "boundary_contract")
    if result.timed_out or result.returncode != 0:
        raise ReferenceCaptureAbort(
            "AMBIGUOUS_PROCESS_OUTCOME",
            f"timed_out={result.timed_out}:returncode={result.returncode}",
            diagnostic=build_process_failure_diagnostic(result),
        )

    stream = analyze_zero_turn_stream(parse_stream_records(result.stdout))
    validate_reference_init_authority(stream.init_event)

    record = build_reference_record(
        provider_identity=identity,
        invocation=invocation,
        stream=stream,
        expected_manifest_sha256=request.expected_manifest_sha256,
        profile_path=request.profile_path,
    )
    digest = write_reference_artifact(artifact, record)
    return ReferenceCaptureOutcome(
        artifact_path=artifact,
        sha256=digest,
        record=record,
        workspace_path=str(workspace),
        surface_audit=audit,
        process_result=result,
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p01_reference_capture",
        description=(
            "Capture the one-time zero-turn P01 reference init at a maximum "
            "budget of exactly 0.0000000 USD. Requires a standalone Owner "
            "authorization bound to the exact source HEAD/TREE below."
        ),
    )
    parser.add_argument("--sealed-manifest", required=True, type=Path)
    parser.add_argument("--workspace-parent", required=True, type=Path)
    parser.add_argument("--hold-out-root", required=True, type=Path)
    parser.add_argument("--authorized-head", required=True)
    parser.add_argument("--authorized-tree", required=True)
    parser.add_argument("--source-repository", default=None, type=Path)
    parser.add_argument("--carrier-repository", default=None, type=Path)
    parser.add_argument("--artifact", default=REFERENCE_ARTIFACT_PATH, type=Path)
    parser.add_argument(
        "--expected-manifest-sha256", default=driver.SEALED_MANIFEST_SHA256
    )
    return parser


def main(argv: Sequence[str] = None) -> int:
    """Module entrypoint for the future one-time real capture."""

    arguments = _build_argument_parser().parse_args(
        list(sys.argv[1:] if argv is None else argv)
    )
    source_repository = (
        Path(__file__).resolve().parents[2]
        if arguments.source_repository is None
        else arguments.source_repository
    )
    try:
        # ``load_manifest_after_lock`` is the existing verified byte-snapshot
        # manifest reader.  The capture deliberately takes no epoch lock: it
        # mutates no epoch, ledger or reservation state, so the lock-ordering
        # discipline that names this loader does not apply here, and reading
        # the manifest a second way would duplicate verified logic.
        sealed = controller.load_manifest_after_lock(
            arguments.sealed_manifest,
            expected_sha256=arguments.expected_manifest_sha256,
        )
        manifest = execution_builder.derive_p01_execution_manifest(
            sealed,
            expected_sealed_manifest_sha256=arguments.expected_manifest_sha256,
        )
        outcome = capture_reference_init(
            ReferenceCaptureRequest(
                execution_manifest=manifest,
                workspace_parent=arguments.workspace_parent,
                hold_out_root=arguments.hold_out_root,
                carrier=production.resolve_carrier_source(
                    repository=arguments.carrier_repository
                ),
                source_repository=source_repository,
                authorized_head=arguments.authorized_head,
                authorized_tree=arguments.authorized_tree,
                artifact_path=arguments.artifact,
                expected_manifest_sha256=arguments.expected_manifest_sha256,
            ),
            process_boundary=SandboxedZeroBudgetCapture(),
        )
    except Exception as exc:  # a capture entrypoint always refuses, never half-runs
        write_abort_report(sys.stderr, exc)
        return 2
    sys.stdout.write(
        json.dumps(
            {"artifact": str(outcome.artifact_path), "sha256": outcome.sha256},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return 0


__all__ = [
    "CAPTURE_FAILURE_DIAGNOSTIC_SCHEMA",
    "CAPTURE_MODE",
    "CaptureProcessRequest",
    "CaptureProcessResult",
    "DIAGNOSTIC_AUTHORITY",
    "DIAGNOSTIC_STDERR_PREFIX",
    "REFERENCE_ARTIFACT_PATH",
    "REFERENCE_INIT_SCHEMA",
    "REFERENCE_MAX_BUDGET_USD",
    "REFERENCE_MAX_TURNS",
    "REFERENCE_PROMPT",
    "ReferenceCaptureAbort",
    "ReferenceCaptureError",
    "ReferenceCaptureOutcome",
    "ReferenceCaptureRequest",
    "SandboxedZeroBudgetCapture",
    "ZeroTurnStream",
    "analyze_zero_turn_stream",
    "assert_reference_workspace_has_no_carrier",
    "assert_zero_budget_argv",
    "build_process_failure_diagnostic",
    "build_reference_argv",
    "build_reference_invocation",
    "build_reference_record",
    "canonical_diagnostic_text",
    "canonical_reference_bytes",
    "capture_reference_init",
    "init_event_sha256",
    "main",
    "parse_stream_records",
    "reference_artifact_sha256",
    "validate_reference_init_authority",
    "validate_reference_record",
    "verify_provider_binary_identity",
    "verify_source_authority",
    "write_abort_report",
    "write_reference_artifact",
]


if __name__ == "__main__":
    raise SystemExit(main())
