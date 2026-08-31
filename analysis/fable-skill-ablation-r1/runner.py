#!/usr/bin/env python3
"""Manifest-driven, provider-neutral harness for the Fable ablation pilot.

A caller injects workspace materialization and a typed provider executor.  The
executor receives only :class:`ModelInvocation`, which contains no treatment
label or orchestrator run identifier, and returns a sealed
:class:`ProviderExecution` rather than a lossy iterable of provider events.

Treatment metadata remains in :class:`WorkspacePlan`, an orchestrator-only
object used before the model process starts.  Evidence emitted after execution
keeps orchestrator identity separate from the explicitly model-visible view.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, Union


def _load_usd_authority() -> Any:
    """Resolve the one canonical persisted USD representation authority."""

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

FABLE_SKILL_IDENTITY = "fable-method"

# The run evidence schema now seals the logical and the physical argv lanes
# separately, records observed sandbox identity, and persists derived USD in
# the one canonical representation.  Condition identity stays orchestrator-side.
RUN_EVIDENCE_SCHEMA = "FABLE_ABLATION_RUN_EVIDENCE/v2"

# One opaque-session width across runner, adapter, and controller: the epoch
# controller accepts exactly 32 lowercase hex, so nothing upstream may accept
# a wider token the controller would later reject.
OPAQUE_SESSION_TOKEN_HEX_WIDTH = 32
_OPAQUE_ID_RE = re.compile(r"[0-9a-f]{%d}" % OPAQUE_SESSION_TOKEN_HEX_WIDTH)
_MISSING = object()
_INVENTORY_DIMENSIONS = frozenset({"tools", "agents", "mcp_servers"})
_DIMENSION_ALIASES = {
    "mcp_servers": ("mcp_servers", "mcpServers"),
}
_SEMANTIC_SKILL_PATHS = (
    ("skills",),
    ("available_skills",),
    ("loaded_skills",),
    ("slash_commands",),
    ("context", "skills"),
    ("context", "available_skills"),
    ("context", "loaded_skills"),
    ("context", "slash_commands"),
    ("system_context", "skills"),
    ("system_context", "available_skills"),
    ("system_context", "loaded_skills"),
)
_SKILL_IDENTITY_KEYS = ("name", "id", "skill", "command")


class HarnessContractError(ValueError):
    """The manifest or injected input cannot prove the harness contract."""


@dataclass(frozen=True)
class ManifestSlot:
    """A selected schedule slot retained only on the orchestrator side."""

    orchestrator_run_id: str
    condition: str
    task_id: str
    prompt: str
    command: tuple[str, ...]
    expected_fable_engaged: bool
    frozen_dimensions: tuple[str, ...]
    source_run_path: str | None
    task_metadata: Mapping[str, Any]
    treatment_metadata: Mapping[str, Any]
    forbidden_model_tokens: tuple[str, ...]


@dataclass(frozen=True)
class WorkspacePlan:
    """Orchestrator-only input for an injected workspace materializer."""

    slot: ManifestSlot
    workspace_path: str
    opaque_identity: str


@dataclass(frozen=True)
class ModelInvocation:
    """The complete request surface an injected provider executor may see."""

    argv: tuple[str, ...]
    cwd: str
    prompt: str
    environment: Mapping[str, str]
    task_visible_run_id: str

    def model_visible_record(self) -> dict[str, Any]:
        secrets_to_redact = tuple(self.environment.values())
        return {
            "argv": [_redact_text(value, secrets_to_redact) for value in self.argv],
            "cwd": _redact_text(self.cwd, secrets_to_redact),
            "prompt": _redact_text(self.prompt, secrets_to_redact),
            # Generic evidence cannot know every policy-specific secret key, so
            # it never serializes raw environment values.  ProviderExecution
            # retains non-secret values and equality-preserving secret hashes.
            "environment": {
                _redact_text(key, secrets_to_redact): {
                    "value": "REDACTED",
                    "sha256_fingerprint": hashlib.sha256(
                        value.encode("utf-8", errors="surrogatepass")
                    ).hexdigest(),
                }
                for key, value in sorted(self.environment.items())
            },
            "task_visible_run_id": _redact_text(
                self.task_visible_run_id, secrets_to_redact
            ),
        }


def _redact_text(value: str, secret_values: Sequence[str]) -> str:
    candidates = sorted({secret for secret in secret_values if secret}, key=len, reverse=True)
    if not candidates:
        return value
    return re.sub("|".join(re.escape(secret) for secret in candidates), "REDACTED", value)


def _nonnegative_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _valid_model_usage(value: Any) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    for model, usage in value.items():
        if not isinstance(model, str) or not model or not isinstance(usage, Mapping) or not usage:
            return False
        for metric, amount in usage.items():
            if (
                not isinstance(metric, str)
                or not metric
                or isinstance(amount, bool)
                or not isinstance(amount, (int, float))
                or amount < 0
                or (isinstance(amount, float) and not math.isfinite(amount))
            ):
                return False
            if metric.casefold().endswith(("tokens", "requests", "window")) and not isinstance(amount, int):
                return False
    return True


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record = dict(pairs)
    if len(record) != len(pairs):
        raise ValueError("duplicate JSON key")
    return record


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _persisted_usd(value: Decimal | None) -> tuple[str | None, str]:
    """Render a derived USD amount in the one canonical persisted form.

    Sealing must never raise, so a non-representable amount is reported as
    such instead of being rounded or silently dropped; the provider-reported
    raw value stays untouched in the raw stream and records.
    """

    if value is None:
        return None, "ABSENT"
    try:
        return _usd.canonical_usd_text(value), "CANONICAL_USD"
    except _usd.CanonicalUsdError:
        return None, "NOT_REPRESENTABLE_IN_CANONICAL_USD"


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class AdapterIdentity:
    """Identity of the source adapter that produced provider evidence."""

    name: str
    version: str
    source_realpath: str | None
    source_sha256: str | None

    def as_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "source_realpath": self.source_realpath,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class ProviderBinaryIdentity:
    """Pinned and observed identity of the provider executable."""

    executable: str
    realpath: str | None
    sha256: str | None
    version: str | None

    def as_record(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "realpath": self.realpath,
            "sha256": self.sha256,
            "version": self.version,
        }


@dataclass(frozen=True)
class EnvironmentValueEvidence:
    """One exact environment value, redacted when it may be a credential."""

    key: str
    value: str
    sha256_fingerprint: str
    redacted: bool

    def as_record(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "sha256_fingerprint": self.sha256_fingerprint,
            "redacted": self.redacted,
        }


@dataclass(frozen=True)
class ProviderInvocationEvidence:
    """Exact process-visible invocation evidence without raw secret values."""

    argv: tuple[str, ...]
    cwd: str
    task_visible_run_id: str
    stdin_sha256: str
    stdin_length: int
    environment: tuple[EnvironmentValueEvidence, ...]
    shell: bool
    close_fds: bool
    start_new_session: bool
    timeout_seconds: float

    @property
    def environment_keys(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.environment)

    def matches_model_invocation(self, invocation: ModelInvocation) -> bool:
        prompt = invocation.prompt.encode("utf-8", errors="surrogatepass")
        expected_environment = tuple(
            (key, _sha256_bytes(value.encode("utf-8", errors="surrogatepass")))
            for key, value in sorted(invocation.environment.items())
        )
        return (
            self.argv == invocation.argv
            and self.cwd == invocation.cwd
            and self.task_visible_run_id == invocation.task_visible_run_id
            and self.stdin_sha256 == _sha256_bytes(prompt)
            and self.stdin_length == len(prompt)
            and tuple((item.key, item.sha256_fingerprint) for item in self.environment)
            == expected_environment
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "cwd": self.cwd,
            "task_visible_run_id": self.task_visible_run_id,
            "stdin_sha256": self.stdin_sha256,
            "stdin_length": self.stdin_length,
            "environment": [item.as_record() for item in self.environment],
            "environment_keys": list(self.environment_keys),
            "shell": self.shell,
            "close_fds": self.close_fds,
            "start_new_session": self.start_new_session,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class ProcessEvidence:
    """Observed subprocess lifecycle, including timeout/group termination."""

    pid: int | None
    pgid: int | None
    returncode: int | None
    status: str
    timed_out: bool
    termination_attempted: bool
    termination_method: str | None
    signals_sent: tuple[int, ...]
    shell: bool
    close_fds: bool
    start_new_session: bool
    timeout_seconds: float

    def as_record(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "pgid": self.pgid,
            "returncode": self.returncode,
            "status": self.status,
            "timed_out": self.timed_out,
            "termination_attempted": self.termination_attempted,
            "termination_method": self.termination_method,
            "signals_sent": list(self.signals_sent),
            "shell": self.shell,
            "close_fds": self.close_fds,
            "start_new_session": self.start_new_session,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class ProviderExecution:
    """Sealed provider evidence; failed runs retain every captured byte."""

    adapter_identity: AdapterIdentity
    provider_binary_identity: ProviderBinaryIdentity
    provider_policy_identity: str
    provider_policy: Mapping[str, Any]
    invocation: ProviderInvocationEvidence
    process: ProcessEvidence
    raw_stdout: bytes
    raw_stderr: bytes
    raw_jsonl_records: tuple[Mapping[str, Any], ...]
    init_index: int | None
    assistant_message_ids: tuple[str, ...]
    terminal_result_index: int | None
    session_id: str | None
    model_usage: Mapping[str, Any] | None
    total_cost_usd: Decimal | None
    validation_errors: tuple[str, ...]
    evidence_sha256: str | None = None
    sandbox_identity: Mapping[str, Any] | None = None

    @property
    def unique_assistant_message_ids(self) -> tuple[str, ...]:
        """Deduplicate IDs without losing their first-observed stream order."""

        return tuple(dict.fromkeys(self.assistant_message_ids))

    @property
    def physical_argv(self) -> tuple[str, ...] | None:
        """The sandbox-prefixed argv actually executed, when one was observed."""

        if self.sandbox_identity is None:
            return None
        observed = self.sandbox_identity.get("physical_argv")
        if not isinstance(observed, (list, tuple)):
            return None
        return tuple(str(part) for part in observed)

    @property
    def persisted_total_cost_usd(self) -> str | None:
        """The derived cost in the one canonical persisted USD representation."""

        return _persisted_usd(self.total_cost_usd)[0]

    def _argv_lanes_record(self) -> dict[str, Any]:
        physical = self.physical_argv
        return {
            "logical": list(self.invocation.argv),
            "logical_authority": "runner.ModelInvocation/ClaudeProviderPolicy",
            "physical": None if physical is None else list(physical),
            "physical_authority": "epoch_controller.build_sandboxed_command",
            "physically_sandboxed": physical is not None,
        }

    def _evidence_record(self) -> dict[str, Any]:
        persisted_cost, cost_representation = _persisted_usd(self.total_cost_usd)
        return {
            "schema": RUN_EVIDENCE_SCHEMA,
            "adapter_identity": self.adapter_identity.as_record(),
            "provider_binary_identity": self.provider_binary_identity.as_record(),
            "provider_policy_identity": self.provider_policy_identity,
            "provider_policy": copy.deepcopy(dict(self.provider_policy)),
            "invocation": self.invocation.as_record(),
            "argv_lanes": self._argv_lanes_record(),
            "sandbox_identity": (
                None
                if self.sandbox_identity is None
                else copy.deepcopy(dict(self.sandbox_identity))
            ),
            "process": self.process.as_record(),
            "raw_stdout_base64": base64.b64encode(self.raw_stdout).decode("ascii"),
            "raw_stdout_sha256": _sha256_bytes(self.raw_stdout),
            "raw_stderr_base64": base64.b64encode(self.raw_stderr).decode("ascii"),
            "raw_stderr_sha256": _sha256_bytes(self.raw_stderr),
            "raw_jsonl_records": [copy.deepcopy(dict(item)) for item in self.raw_jsonl_records],
            "init_index": self.init_index,
            "assistant_message_ids": list(self.assistant_message_ids),
            "unique_assistant_message_ids": list(self.unique_assistant_message_ids),
            "terminal_result_index": self.terminal_result_index,
            "session_id": self.session_id,
            "model_usage": (
                None if self.model_usage is None else copy.deepcopy(dict(self.model_usage))
            ),
            "total_cost_usd": persisted_cost,
            "total_cost_usd_representation": cost_representation,
            "validation_errors": list(self.validation_errors),
        }

    def computed_evidence_sha256(self) -> str:
        return _sha256_bytes(_canonical_json_bytes(self._evidence_record()))

    @property
    def evidence_sealed(self) -> bool:
        try:
            return (
                isinstance(self.evidence_sha256, str)
                and bool(re.fullmatch(r"[0-9a-f]{64}", self.evidence_sha256))
                and secrets.compare_digest(
                    self.evidence_sha256, self.computed_evidence_sha256()
                )
            )
        except (TypeError, ValueError, AttributeError, RecursionError):
            return False

    def sealed(self) -> "ProviderExecution":
        unsealed = replace(self, evidence_sha256=None)
        return replace(unsealed, evidence_sha256=unsealed.computed_evidence_sha256())

    @property
    def run_countable(self) -> bool:
        if self.validation_errors or not self.evidence_sealed or not self.raw_stdout:
            return False
        try:
            # A seal proves integrity, not completeness or consistency of summaries.
            records = tuple(
                json.loads(line.decode("utf-8"), object_pairs_hook=_unique_json_object)
                for line in self.raw_stdout.splitlines()
            )
            if _canonical_json_bytes(records) != _canonical_json_bytes(self.raw_jsonl_records):
                return False
            if not all(isinstance(record, Mapping) for record in records):
                return False
            init_indices = [
                index for index, record in enumerate(records)
                if record.get("type") == "system" and record.get("subtype") == "init"
            ]
            result_indices = [
                index for index, record in enumerate(records) if record.get("type") == "result"
            ]
            if (
                len(init_indices) != 1
                or type(self.init_index) is not int
                or self.init_index != init_indices[0]
                or len(result_indices) != 1
                or type(self.terminal_result_index) is not int
                or self.terminal_result_index != result_indices[0]
                or self.terminal_result_index != len(records) - 1
                or not isinstance(self.session_id, str)
                or not self.session_id
            ):
                return False
            assistant_ids: list[str] = []
            main_models: list[str] = []
            for index, record in enumerate(records):
                required_session = index in init_indices + result_indices or record.get("type") == "assistant"
                if (required_session or "session_id" in record) and record.get("session_id") != self.session_id:
                    return False
                if record.get("type") != "assistant":
                    continue
                message = record.get("message")
                if not isinstance(message, Mapping) or not isinstance(message.get("id"), str) or not message["id"]:
                    return False
                assistant_ids.append(message["id"])
                if not any(record.get(key) for key in ("parent_tool_use_id", "is_subagent", "subagent")):
                    if not self.init_index < index < self.terminal_result_index:
                        return False
                    if not isinstance(message.get("model"), str) or not message["model"]:
                        return False
                    main_models.append(message["model"])
            terminal = records[self.terminal_result_index]
            raw_cost = _nonnegative_decimal(terminal.get("total_cost_usd"))
            return (
                self.process.status == "EXITED"
                and type(self.process.returncode) is int
                and self.process.returncode == 0
                and self.process.timed_out is False
                and type(self.process.pid) is int and self.process.pid > 0
                and self.process.pgid == self.process.pid
                and self.process.shell is False
                and self.process.close_fds is True
                and self.process.start_new_session is True
                and bool(self.adapter_identity.name)
                and bool(self.adapter_identity.version)
                and bool(self.adapter_identity.source_realpath)
                and bool(re.fullmatch(r"[0-9a-f]{64}", self.adapter_identity.source_sha256 or ""))
                and bool(self.provider_binary_identity.executable)
                and bool(self.provider_binary_identity.realpath)
                and bool(self.provider_binary_identity.version)
                and bool(re.fullmatch(r"[0-9a-f]{64}", self.provider_binary_identity.sha256 or ""))
                and bool(re.fullmatch(r"[0-9a-f]{64}", self.provider_policy_identity))
                and bool(self.provider_policy)
                and bool(main_models)
                and tuple(assistant_ids) == self.assistant_message_ids
                and terminal.get("subtype") == "success"
                and terminal.get("is_error", False) is False
                and _valid_model_usage(terminal.get("modelUsage"))
                and self.model_usage == terminal["modelUsage"]
                and all(model in self.model_usage for model in main_models)
                and isinstance(self.total_cost_usd, Decimal)
                and self.total_cost_usd.is_finite()
                and self.persisted_total_cost_usd is not None
                and raw_cost is not None
                and self.total_cost_usd == raw_cost
                and (
                    "num_turns" not in terminal
                    or (type(terminal["num_turns"]) is int and terminal["num_turns"] >= 0)
                )
            )
        except (TypeError, ValueError, AttributeError, KeyError, InvalidOperation, RecursionError):
            return False

    def as_record(self) -> dict[str, Any]:
        record = self._evidence_record()
        record["evidence_sha256"] = self.evidence_sha256
        record["evidence_sealed"] = self.evidence_sealed
        record["run_countable"] = self.run_countable
        return record


@dataclass(frozen=True)
class SurfaceAudit:
    passed: bool = False
    leaks: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "leaks": list(self.leaks),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class InitEvidence:
    init_event_found: bool = False
    valid: bool = False
    candidate_count: int = 0
    event: Mapping[str, Any] | None = None
    inventories: Mapping[str, Any] = field(default_factory=dict)
    skill_identities: frozenset[str] = frozenset()
    skills_resolved: bool = False
    errors: tuple[str, ...] = ()

    @property
    def fable_engaged(self) -> bool | None:
        if not self.skills_resolved:
            return None
        return FABLE_SKILL_IDENTITY in self.skill_identities


@dataclass(frozen=True)
class PurityResult:
    init_event_found: bool = False
    reference_init_event_found: bool = False
    fable_engaged: bool | None = None
    expected_fable_engaged: bool | None = None
    dimension_matches: Mapping[str, bool] = field(default_factory=dict)
    checks: Mapping[str, bool] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    purity_pass: bool = False
    run_countable: bool = False

    def __post_init__(self) -> None:
        if self.purity_pass and not self.init_event_found:
            raise ValueError("purity_pass cannot be true without an init event")
        if self.run_countable and not self.purity_pass:
            raise ValueError("run_countable cannot be true when purity failed")

    def as_record(self) -> dict[str, Any]:
        return {
            "init_event_found": self.init_event_found,
            "reference_init_event_found": self.reference_init_event_found,
            "fable_engaged": self.fable_engaged,
            "expected_fable_engaged": self.expected_fable_engaged,
            "dimension_matches": dict(self.dimension_matches),
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
            "purity_pass": self.purity_pass,
            "run_countable": self.run_countable,
        }


@dataclass(frozen=True)
class GitState:
    state_resolved: bool = False
    final_head: str | None = None
    final_head_tree: str | None = None
    final_worktree_dirty: bool | None = None
    tracked_diff_present: bool | None = None
    untracked_present: bool | None = None
    staged_diff_present: bool | None = None
    unstaged_diff_present: bool | None = None
    filesystem_state_representation: str = "UNRESOLVED"
    errors: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "final_head": self.final_head,
            "final_head_tree": self.final_head_tree,
            "final_worktree_dirty": self.final_worktree_dirty,
            "tracked_diff_present": self.tracked_diff_present,
            "untracked_present": self.untracked_present,
            "staged_diff_present": self.staged_diff_present,
            "unstaged_diff_present": self.unstaged_diff_present,
            "state_resolved": self.state_resolved,
            "filesystem_state_representation": self.filesystem_state_representation,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class RunEvidence:
    """Structured evidence; condition identity is orchestrator-side only."""

    orchestrator_run_id: str
    condition: str
    task_id: str
    model_invocation: ModelInvocation
    surface_audit: SurfaceAudit
    purity: PurityResult
    git_state: GitState
    materializer_called: bool
    executor_called: bool
    materializer_error: str | None
    provider_execution: ProviderExecution | None
    run_countable: bool

    def as_record(self) -> dict[str, Any]:
        return {
            "orchestrator": {
                "run_id": self.orchestrator_run_id,
                "condition": self.condition,
                "task_id": self.task_id,
            },
            "model_visible": self.model_invocation.model_visible_record(),
            "surface_audit": self.surface_audit.as_record(),
            "purity": self.purity.as_record(),
            "git_state": self.git_state.as_record(),
            "materializer_called": self.materializer_called,
            "executor_called": self.executor_called,
            "materializer_error": self.materializer_error,
            "provider_execution": (
                None
                if self.provider_execution is None
                else self.provider_execution.as_record()
            ),
            "run_countable": self.run_countable,
        }


Event = Union[Mapping[str, Any], str, bytes]
Materializer = Callable[[WorkspacePlan], None]
ProviderExecutor = Callable[[ModelInvocation], ProviderExecution]


def _boundary_failure_execution(
    invocation: ModelInvocation, validation_error: str
) -> ProviderExecution:
    """Retain safe invocation evidence when an injected executor breaks type."""

    try:
        stdin_bytes = invocation.prompt.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        stdin_bytes = invocation.prompt.encode("utf-8", errors="surrogatepass")
    environment = tuple(
        EnvironmentValueEvidence(
            key=_redact_text(key, tuple(invocation.environment.values())),
            value="REDACTED",
            sha256_fingerprint=_sha256_bytes(
                value.encode("utf-8", errors="surrogatepass")
            ),
            redacted=True,
        )
        for key, value in sorted(invocation.environment.items())
    )
    secrets_to_redact = tuple(invocation.environment.values())
    invocation_evidence = ProviderInvocationEvidence(
        argv=tuple(_redact_text(value, secrets_to_redact) for value in invocation.argv),
        cwd=_redact_text(invocation.cwd, secrets_to_redact),
        task_visible_run_id=_redact_text(invocation.task_visible_run_id, secrets_to_redact),
        stdin_sha256=_sha256_bytes(stdin_bytes),
        stdin_length=len(stdin_bytes),
        environment=environment,
        shell=False,
        close_fds=True,
        start_new_session=True,
        timeout_seconds=0.0,
    )
    execution = ProviderExecution(
        adapter_identity=AdapterIdentity(
            name="UNRESOLVED_EXECUTOR",
            version="UNRESOLVED",
            source_realpath=None,
            source_sha256=None,
        ),
        provider_binary_identity=ProviderBinaryIdentity(
            executable=(
                _redact_text(invocation.argv[0], secrets_to_redact)
                if invocation.argv else "UNRESOLVED"
            ),
            realpath=None,
            sha256=None,
            version=None,
        ),
        provider_policy_identity="UNRESOLVED",
        provider_policy={},
        invocation=invocation_evidence,
        process=ProcessEvidence(
            pid=None,
            pgid=None,
            returncode=None,
            status="EXECUTOR_BOUNDARY_FAILED",
            timed_out=False,
            termination_attempted=False,
            termination_method=None,
            signals_sent=(),
            shell=False,
            close_fds=True,
            start_new_session=True,
            timeout_seconds=0.0,
        ),
        raw_stdout=b"",
        raw_stderr=b"",
        raw_jsonl_records=(),
        init_index=None,
        assistant_message_ids=(),
        terminal_result_index=None,
        session_id=None,
        model_usage=None,
        total_cost_usd=None,
        validation_errors=(validation_error,),
    )
    return execution.sealed()


def load_manifest(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a manifest without applying any experimental decision in code."""

    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessContractError(f"manifest is not readable JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise HarnessContractError("manifest root must be an object")
    return manifest


def _manifest_schedule(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    top = manifest.get("schedule")
    cto = manifest.get("cto_r1_execution_schedule")
    nested = cto.get("schedule") if isinstance(cto, Mapping) else None
    if top is not None and nested is not None and top != nested:
        raise HarnessContractError("manifest exposes conflicting schedule authorities")
    schedule = nested if nested is not None else top
    if not isinstance(schedule, list) or not schedule:
        raise HarnessContractError("manifest schedule must be a non-empty list")
    if not all(isinstance(item, Mapping) for item in schedule):
        raise HarnessContractError("every schedule entry must be an object")
    return schedule


def _treatment_for_condition(
    manifest: Mapping[str, Any], condition: str
) -> Mapping[str, Any]:
    treatment = manifest.get("treatment")
    if not isinstance(treatment, Mapping):
        raise HarnessContractError("manifest treatment must be an object")

    direct = treatment.get(condition.casefold())
    if isinstance(direct, Mapping):
        return direct

    matches = []
    for value in treatment.values():
        if not isinstance(value, Mapping):
            continue
        label = value.get("label")
        if isinstance(label, str) and label.casefold() == condition.casefold():
            matches.append(value)
    if len(matches) != 1:
        raise HarnessContractError(
            f"condition {condition!r} does not identify exactly one treatment"
        )
    return matches[0]


def select_manifest_slot(
    manifest: Mapping[str, Any], orchestrator_run_id: str
) -> ManifestSlot:
    """Resolve one schedule slot while retaining all decisions in metadata."""

    if not isinstance(orchestrator_run_id, str) or not orchestrator_run_id:
        raise HarnessContractError("orchestrator_run_id must be a non-empty string")

    schedule = _manifest_schedule(manifest)
    matching = [item for item in schedule if item.get("run_id") == orchestrator_run_id]
    if len(matching) != 1:
        raise HarnessContractError(
            f"run id {orchestrator_run_id!r} must identify exactly one schedule slot"
        )
    schedule_entry = matching[0]

    condition = schedule_entry.get("condition")
    task_id = schedule_entry.get("task_id")
    if not isinstance(condition, str) or not condition:
        raise HarnessContractError("schedule condition must be a non-empty string")
    if not isinstance(task_id, str) or not task_id:
        raise HarnessContractError("schedule task_id must be a non-empty string")

    tasks = manifest.get("tasks")
    if not isinstance(tasks, list):
        raise HarnessContractError("manifest tasks must be a list")
    matching_tasks = [task for task in tasks if isinstance(task, Mapping) and task.get("task_id") == task_id]
    if len(matching_tasks) != 1:
        raise HarnessContractError(f"task id {task_id!r} must identify exactly one task")
    task = matching_tasks[0]
    prompt = task.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise HarnessContractError("task prompt must be a non-empty string")
    if task.get("prompt_is_identical_for_both_conditions") is not True:
        raise HarnessContractError("manifest does not affirm a condition-identical prompt")

    treatment = _treatment_for_condition(manifest, condition)
    command = treatment.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise HarnessContractError("treatment command must be a non-empty string list")
    selected_command = tuple(command)
    for item in schedule:
        scheduled_condition = item.get("condition")
        if not isinstance(scheduled_condition, str) or not scheduled_condition:
            raise HarnessContractError("schedule condition must be a non-empty string")
        scheduled_treatment = _treatment_for_condition(manifest, scheduled_condition)
        scheduled_command = scheduled_treatment.get("command")
        if (
            not isinstance(scheduled_command, list)
            or not scheduled_command
            or not all(isinstance(part, str) and part for part in scheduled_command)
        ):
            raise HarnessContractError(
                "every scheduled treatment command must be a non-empty string list"
            )
        if tuple(scheduled_command) != selected_command:
            raise HarnessContractError(
                "scheduled treatment commands differ on a model/process-visible surface"
            )
    expected = treatment.get("run_root_contains_carrier")
    if not isinstance(expected, bool):
        raise HarnessContractError(
            "treatment must explicitly declare run_root_contains_carrier"
        )

    purity_gate = manifest.get("purity_gate")
    dimensions = (
        purity_gate.get("must_be_exactly_equal_between_reference_and_run")
        if isinstance(purity_gate, Mapping)
        else None
    )
    if (
        not isinstance(dimensions, list)
        or not dimensions
        or not all(isinstance(item, str) and item for item in dimensions)
        or len(set(dimensions)) != len(dimensions)
    ):
        raise HarnessContractError("purity dimensions must be a unique non-empty string list")

    forbidden: set[str] = set()
    for item in schedule:
        for key in ("condition", "run_id"):
            value = item.get(key)
            if isinstance(value, str) and value:
                forbidden.add(value)
    all_treatments = manifest.get("treatment")
    if isinstance(all_treatments, Mapping):
        for value in all_treatments.values():
            if isinstance(value, Mapping):
                label = value.get("label")
                if isinstance(label, str) and label:
                    forbidden.add(label)

    source_run_path = schedule_entry.get("run_path")
    if source_run_path is not None and not isinstance(source_run_path, str):
        raise HarnessContractError("schedule run_path must be a string when present")

    return ManifestSlot(
        orchestrator_run_id=orchestrator_run_id,
        condition=condition,
        task_id=task_id,
        prompt=prompt,
        command=selected_command,
        expected_fable_engaged=expected,
        frozen_dimensions=tuple(dimensions),
        source_run_path=source_run_path,
        task_metadata=copy.deepcopy(dict(task)),
        treatment_metadata=copy.deepcopy(dict(treatment)),
        forbidden_model_tokens=tuple(sorted(forbidden)),
    )


def create_condition_neutral_workspace(
    workspace_parent: str | os.PathLike[str],
    opaque_id_factory: Callable[[], str] | None = None,
) -> tuple[Path, str]:
    """Create a workspace identity without accepting condition as an input."""

    parent = Path(workspace_parent)
    if not parent.is_dir():
        raise HarnessContractError("workspace parent must already exist")
    opaque_identity = (
        opaque_id_factory()
        if opaque_id_factory is not None
        else secrets.token_hex(OPAQUE_SESSION_TOKEN_HEX_WIDTH // 2)
    )
    if not isinstance(opaque_identity, str) or not _OPAQUE_ID_RE.fullmatch(opaque_identity):
        raise HarnessContractError(
            "opaque workspace identity must be exactly "
            f"{OPAQUE_SESSION_TOKEN_HEX_WIDTH} lowercase hex characters"
        )
    workspace = parent / f"session-{opaque_identity}"
    try:
        workspace.mkdir(mode=0o700)
    except OSError as exc:
        raise HarnessContractError(f"cannot create unique neutral workspace: {exc}") from exc
    return workspace, opaque_identity


def build_model_invocation(
    slot: ManifestSlot,
    workspace: str | os.PathLike[str],
    opaque_identity: str,
    environment: Mapping[str, str] | None = None,
) -> ModelInvocation:
    """Build the executor request; treatment state is intentionally omitted."""

    if not _OPAQUE_ID_RE.fullmatch(opaque_identity):
        raise HarnessContractError("invalid opaque identity")
    explicit_environment = dict(environment or {})
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in explicit_environment.items()
    ):
        raise HarnessContractError("model environment must contain only strings")
    return ModelInvocation(
        argv=slot.command,
        cwd=str(Path(workspace).resolve()),
        prompt=slot.prompt,
        environment=explicit_environment,
        task_visible_run_id=f"session-{opaque_identity}",
    )


def _contains_token(text: str, token: str) -> bool:
    if not token:
        return False
    # Short condition labels such as ON/OFF are checked case-sensitively so
    # normal prose ("on") is not misclassified.  Longer labels/run ids are
    # case-insensitive.  Boundaries catch filename components without treating
    # substrings such as CANONICAL or offset.py as condition evidence.
    flags = 0 if len(token) <= 3 else re.IGNORECASE
    pattern = rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])"
    return re.search(pattern, text, flags) is not None


def audit_model_visible_surfaces(
    invocation: ModelInvocation,
    workspace: str | os.PathLike[str],
    forbidden_tokens: Sequence[str],
) -> SurfaceAudit:
    """Affirm that condition/run identity is absent from every visible surface."""

    root = Path(workspace)
    errors: list[str] = []
    leaks: list[str] = []
    if not root.is_dir():
        errors.append("workspace_missing")
    try:
        if root.resolve() != Path(invocation.cwd).resolve():
            errors.append("invocation_cwd_does_not_match_workspace")
    except OSError:
        errors.append("workspace_resolution_failed")

    surfaces: list[tuple[str, str]] = [
        ("cwd", invocation.cwd),
        ("prompt", invocation.prompt),
        ("task_visible_run_id", invocation.task_visible_run_id),
    ]
    surfaces.extend((f"argv[{index}]", value) for index, value in enumerate(invocation.argv))
    for key, value in invocation.environment.items():
        surfaces.append((f"environment_key[{key}]", key))
        surfaces.append((f"environment_value[{key}]", value))

    if root.is_dir():
        try:
            for current_root, directories, filenames in os.walk(root, followlinks=False):
                current = Path(current_root)
                for name in directories + filenames:
                    entry = current / name
                    surfaces.append((f"workspace_name[{entry.relative_to(root)}]", name))
                    if entry.is_symlink():
                        try:
                            surfaces.append((f"symlink_target[{entry.relative_to(root)}]", os.readlink(entry)))
                        except OSError:
                            errors.append(f"symlink_target_unreadable:{entry.relative_to(root)}")
        except OSError as exc:
            errors.append(f"workspace_inventory_failed:{exc}")

    for label, value in surfaces:
        for token in forbidden_tokens:
            if _contains_token(value, token):
                leaks.append(f"{label}:{token}")

    unique_leaks = tuple(sorted(set(leaks)))
    unique_errors = tuple(sorted(set(errors)))
    return SurfaceAudit(
        passed=not unique_leaks and not unique_errors,
        leaks=unique_leaks,
        errors=unique_errors,
    )


def _path_value(source: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = source
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _normalize_skill_identity(raw: str) -> str:
    identity = raw.strip().casefold()
    while identity.startswith(("/", "$")):
        identity = identity[1:]
    return identity


def _parse_skill_inventory(value: Any) -> tuple[set[str], list[str]]:
    identities: set[str] = set()
    errors: list[str] = []

    def add_identity(raw: Any, location: str) -> None:
        if not isinstance(raw, str) or not raw.strip():
            errors.append(f"invalid_skill_identity:{location}")
            return
        identities.add(_normalize_skill_identity(raw))

    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            if isinstance(item, str):
                add_identity(item, str(index))
            elif isinstance(item, Mapping):
                present = [key for key in _SKILL_IDENTITY_KEYS if key in item]
                if not present:
                    errors.append(f"skill_record_without_identity:{index}")
                for key in present:
                    add_identity(item[key], f"{index}.{key}")
            else:
                errors.append(f"invalid_skill_record:{index}")
    elif isinstance(value, Mapping):
        present = [key for key in _SKILL_IDENTITY_KEYS if key in value]
        if present:
            for key in present:
                add_identity(value[key], key)
        else:
            for key in value:
                add_identity(key, "mapping_key")
    else:
        errors.append("skill_inventory_not_list_or_object")
    return identities, errors


def _semantic_skills(init_event: Mapping[str, Any]) -> tuple[frozenset[str], bool, list[str]]:
    identities: set[str] = set()
    found_inventory = False
    errors: list[str] = []
    for path in _SEMANTIC_SKILL_PATHS:
        value = _path_value(init_event, path)
        if value is _MISSING:
            continue
        found_inventory = True
        parsed, parse_errors = _parse_skill_inventory(value)
        identities.update(parsed)
        errors.extend(f"{'.'.join(path)}:{error}" for error in parse_errors)
    return frozenset(identities), found_inventory and not errors, errors


def _dimension_value(init_event: Mapping[str, Any], dimension: str) -> tuple[Any, list[str]]:
    aliases = _DIMENSION_ALIASES.get(dimension, (dimension,))
    found: list[tuple[str, Any]] = []
    for alias in aliases:
        value = _path_value(init_event, alias.split("."))
        if value is not _MISSING:
            found.append((alias, value))
    if not found:
        return _MISSING, [f"missing_inventory:{dimension}"]
    canonical = [json.dumps(value, sort_keys=True, separators=(",", ":")) for _, value in found]
    if len(set(canonical)) != 1:
        return _MISSING, [f"ambiguous_inventory_aliases:{dimension}"]
    value = found[0][1]
    if dimension in _INVENTORY_DIMENSIONS and not isinstance(value, (list, dict)):
        return _MISSING, [f"malformed_inventory:{dimension}"]
    if value is None:
        return _MISSING, [f"null_inventory:{dimension}"]
    return copy.deepcopy(value), []


def parse_init_events(
    events: Iterable[Event], required_dimensions: Sequence[str] = ()
) -> InitEvidence:
    """Parse the actual ``system/init`` shape and fail closed on ambiguity."""

    parsed_events: list[Mapping[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(events):
        if isinstance(raw, Mapping):
            parsed = copy.deepcopy(dict(raw))
        elif isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8").strip()
                if not text:
                    continue
                parsed = json.loads(text)
            except (UnicodeError, json.JSONDecodeError) as exc:
                errors.append(f"unparseable_event:{index}:{type(exc).__name__}")
                continue
        elif isinstance(raw, str):
            text = raw.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                errors.append(f"unparseable_event:{index}:JSONDecodeError")
                continue
        else:
            errors.append(f"unparseable_event:{index}:unsupported_type")
            continue
        if not isinstance(parsed, Mapping):
            errors.append(f"malformed_event:{index}:not_object")
            continue
        parsed_events.append(dict(parsed))

    exact_candidates = [
        event
        for event in parsed_events
        if event.get("type") == "system" and event.get("subtype") == "init"
    ]
    malformed_type_candidates = [
        event
        for event in parsed_events
        if event.get("subtype") == "init" and event.get("type") != "system"
    ]
    semantic_candidate_count = len(exact_candidates) + len(malformed_type_candidates)
    if semantic_candidate_count > 1:
        errors.append("ambiguous_init_events")
        return InitEvidence(candidate_count=semantic_candidate_count, errors=tuple(errors))

    if exact_candidates:
        event = exact_candidates[0]
    elif malformed_type_candidates:
        event = malformed_type_candidates[0]
    else:
        # A lone system record with the wrong/missing subtype is a malformed
        # init candidate.  Other system records are ignored once one exact
        # system/init has been found, so later status events do not create
        # false ambiguity.
        system_candidates = [event for event in parsed_events if event.get("type") == "system"]
        if not system_candidates:
            errors.append("missing_init_event")
            return InitEvidence(candidate_count=0, errors=tuple(errors))
        if len(system_candidates) != 1:
            errors.append("ambiguous_init_events")
            return InitEvidence(candidate_count=len(system_candidates), errors=tuple(errors))
        event = system_candidates[0]

    actual_shape = event.get("type") == "system" and event.get("subtype") == "init"
    if event.get("type") != "system":
        errors.append("malformed_init_type")
    if "subtype" not in event:
        errors.append("malformed_init_subtype_missing")
    elif event.get("subtype") != "init":
        errors.append("wrong_init_subtype")

    inventories: dict[str, Any] = {}
    for dimension in required_dimensions:
        value, dimension_errors = _dimension_value(event, dimension)
        errors.extend(dimension_errors)
        if value is not _MISSING:
            inventories[dimension] = value

    skill_identities, skills_resolved, skill_errors = _semantic_skills(event)
    errors.extend(skill_errors)
    return InitEvidence(
        init_event_found=actual_shape,
        valid=actual_shape and not errors,
        candidate_count=1,
        event=event,
        inventories=inventories,
        skill_identities=skill_identities,
        skills_resolved=skills_resolved,
        errors=tuple(errors),
    )


def evaluate_purity(
    run_events: Iterable[Event],
    reference_events: Iterable[Event],
    *,
    expected_fable_engaged: bool,
    frozen_dimensions: Sequence[str],
    surface_audit: SurfaceAudit | None,
) -> PurityResult:
    """Evaluate every purity dimension affirmatively; absence never passes."""

    run = parse_init_events(run_events, frozen_dimensions)
    reference = parse_init_events(reference_events, frozen_dimensions)
    checks: dict[str, bool] = {
        "run_init_valid": run.valid,
        "reference_init_valid": reference.valid,
        "model_visible_condition_neutral": bool(surface_audit and surface_audit.passed),
        "run_semantic_skill_inventory_resolved": run.skills_resolved,
        "reference_semantic_skill_inventory_resolved": reference.skills_resolved,
    }

    fable_engaged = run.fable_engaged
    checks["intended_fable_state"] = (
        fable_engaged is not None and fable_engaged is expected_fable_engaged
    )

    if run.skills_resolved and reference.skills_resolved:
        reference_has_fable = FABLE_SKILL_IDENTITY in reference.skill_identities
        if expected_fable_engaged:
            expected_skills = set(reference.skill_identities)
            expected_skills.add(FABLE_SKILL_IDENTITY)
            skill_delta_matches = (
                not reference_has_fable
                and set(run.skill_identities) == expected_skills
            )
        else:
            skill_delta_matches = (
                not reference_has_fable
                and set(run.skill_identities) == set(reference.skill_identities)
            )
    else:
        skill_delta_matches = False
    checks["skill_delta_is_exactly_treatment"] = skill_delta_matches

    dimension_matches: dict[str, bool] = {}
    for dimension in frozen_dimensions:
        matched = (
            run.valid
            and reference.valid
            and dimension in run.inventories
            and dimension in reference.inventories
            and run.inventories[dimension] == reference.inventories[dimension]
        )
        dimension_matches[dimension] = matched
        checks[f"frozen_dimension:{dimension}"] = matched

    purity_pass = bool(checks) and all(checks.values())
    reasons: list[str] = []
    reasons.extend(f"run:{error}" for error in run.errors)
    reasons.extend(f"reference:{error}" for error in reference.errors)
    if surface_audit is None:
        reasons.append("model_visible_surface_audit_absent")
    else:
        reasons.extend(f"surface_leak:{leak}" for leak in surface_audit.leaks)
        reasons.extend(f"surface_error:{error}" for error in surface_audit.errors)
    reasons.extend(name for name, passed in checks.items() if not passed)

    return PurityResult(
        init_event_found=run.init_event_found,
        reference_init_event_found=reference.init_event_found,
        fable_engaged=fable_engaged,
        expected_fable_engaged=expected_fable_engaged,
        dimension_matches=dimension_matches,
        checks=checks,
        reasons=tuple(dict.fromkeys(reasons)),
        purity_pass=purity_pass,
        run_countable=purity_pass,
    )


def _git(
    repository: Path, args: Sequence[str], allowed_returncodes: frozenset[int] = frozenset({0})
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"git invocation failed: {exc}") from exc
    if result.returncode not in allowed_returncodes:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {stderr}")
    return result


def capture_git_state(repository: str | os.PathLike[str]) -> GitState:
    """Capture HEAD metadata and dirty filesystem facts as distinct concepts."""

    candidate = Path(repository)
    try:
        root_result = _git(candidate, ("rev-parse", "--show-toplevel"))
        root = Path(root_result.stdout.decode("utf-8", errors="strict").strip())
        final_head = _git(root, ("rev-parse", "HEAD")).stdout.decode().strip()
        final_head_tree = _git(root, ("rev-parse", "HEAD^{tree}")).stdout.decode().strip()
        unstaged_result = _git(
            root,
            ("diff", "--quiet", "--ignore-submodules=none", "--"),
            frozenset({0, 1}),
        )
        staged_result = _git(
            root,
            ("diff", "--cached", "--quiet", "--ignore-submodules=none", "--"),
            frozenset({0, 1}),
        )
        untracked_output = _git(
            root, ("ls-files", "--others", "--exclude-standard", "-z")
        ).stdout
        unstaged = unstaged_result.returncode == 1
        staged = staged_result.returncode == 1
        tracked = unstaged or staged
        untracked = bool(untracked_output)
        dirty = tracked or untracked
    except (RuntimeError, UnicodeError) as exc:
        return GitState(errors=(str(exc),))

    if dirty:
        representation = "HEAD_TREE_PLUS_WORKTREE_CHANGES"
    else:
        representation = "HEAD_TREE_AND_CLEAN_WORKTREE"
    return GitState(
        state_resolved=True,
        final_head=final_head,
        final_head_tree=final_head_tree,
        final_worktree_dirty=dirty,
        tracked_diff_present=tracked,
        untracked_present=untracked,
        staged_diff_present=staged,
        unstaged_diff_present=unstaged,
        filesystem_state_representation=representation,
    )


def execute_manifest_slot(
    manifest: Mapping[str, Any],
    orchestrator_run_id: str,
    *,
    workspace_parent: str | os.PathLike[str],
    reference_events: Iterable[Event],
    materializer: Materializer,
    executor: ProviderExecutor,
    environment: Mapping[str, str] | None = None,
    opaque_id_factory: Callable[[], str] | None = None,
) -> RunEvidence:
    """Materialize and execute one slot through injected interfaces only.

    Invalid reference evidence or model-visible leakage prevents executor
    invocation.  Provider failures retain a typed execution record; unresolved
    Git state and any provider/purity failure make the observation non-countable.
    """

    slot = select_manifest_slot(manifest, orchestrator_run_id)
    workspace, opaque_identity = create_condition_neutral_workspace(
        workspace_parent, opaque_id_factory
    )
    plan = WorkspacePlan(
        slot=slot,
        workspace_path=str(workspace.resolve()),
        opaque_identity=opaque_identity,
    )
    invocation = build_model_invocation(
        slot, workspace, opaque_identity, environment=environment
    )

    materializer_called = True
    materializer_error: str | None = None
    try:
        materializer(plan)
    except Exception as exc:  # injected boundary: convert failure to evidence
        materializer_error = _redact_text(
            f"{type(exc).__name__}: {exc}", tuple(invocation.environment.values())
        )

    surface_audit = audit_model_visible_surfaces(
        invocation, workspace, slot.forbidden_model_tokens
    )
    if materializer_error is not None:
        surface_audit = SurfaceAudit(
            passed=False,
            leaks=surface_audit.leaks,
            errors=surface_audit.errors + ("workspace_materialization_failed",),
        )

    reference_records = list(reference_events)
    reference = parse_init_events(reference_records, slot.frozen_dimensions)
    reference_preflight_pass = (
        reference.valid
        and reference.skills_resolved
        and FABLE_SKILL_IDENTITY not in reference.skill_identities
    )

    executor_called = False
    provider_execution: ProviderExecution | None = None
    run_records: tuple[Mapping[str, Any], ...] = ()
    if surface_audit.passed and reference_preflight_pass and materializer_error is None:
        executor_called = True
        try:
            candidate = executor(invocation)
        except Exception as exc:  # boundary failure still retains safe evidence
            provider_execution = _boundary_failure_execution(
                invocation, f"executor_boundary_exception:{type(exc).__name__}"
            )
        else:
            if isinstance(candidate, ProviderExecution):
                provider_execution = candidate
            else:
                provider_execution = _boundary_failure_execution(
                    invocation, "executor_returned_non_provider_execution"
                )
        run_records = provider_execution.raw_jsonl_records

    purity = evaluate_purity(
        run_records,
        reference_records,
        expected_fable_engaged=slot.expected_fable_engaged,
        frozen_dimensions=slot.frozen_dimensions,
        surface_audit=surface_audit,
    )
    git_state = capture_git_state(workspace)
    countable = (
        purity.run_countable
        and git_state.state_resolved
        and executor_called
        and provider_execution is not None
        and provider_execution.run_countable
        and provider_execution.invocation.matches_model_invocation(invocation)
        and materializer_error is None
    )

    return RunEvidence(
        orchestrator_run_id=slot.orchestrator_run_id,
        condition=slot.condition,
        task_id=slot.task_id,
        model_invocation=invocation,
        surface_audit=surface_audit,
        purity=purity,
        git_state=git_state,
        materializer_called=materializer_called,
        executor_called=executor_called,
        materializer_error=materializer_error,
        provider_execution=provider_execution,
        run_countable=countable,
    )


__all__ = [
    "AdapterIdentity",
    "EnvironmentValueEvidence",
    "FABLE_SKILL_IDENTITY",
    "OPAQUE_SESSION_TOKEN_HEX_WIDTH",
    "RUN_EVIDENCE_SCHEMA",
    "GitState",
    "HarnessContractError",
    "InitEvidence",
    "ManifestSlot",
    "ModelInvocation",
    "ProcessEvidence",
    "ProviderBinaryIdentity",
    "ProviderExecution",
    "ProviderExecutor",
    "ProviderInvocationEvidence",
    "PurityResult",
    "RunEvidence",
    "SurfaceAudit",
    "WorkspacePlan",
    "audit_model_visible_surfaces",
    "build_model_invocation",
    "capture_git_state",
    "create_condition_neutral_workspace",
    "evaluate_purity",
    "execute_manifest_slot",
    "load_manifest",
    "parse_init_events",
    "select_manifest_slot",
]
