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
import json
import math
import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, Union


FABLE_SKILL_IDENTITY = "fable-method"
_OPAQUE_ID_RE = re.compile(r"[0-9a-f]{32,64}")
_GIT_OID_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
_MISSING = object()
_INVENTORY_DIMENSIONS = frozenset({"tools", "agents", "mcp_servers", "plugins"})
_DIMENSION_ALIASES = {
    "mcp_servers": ("mcp_servers", "mcpServers"),
    "plugins": ("plugins", "plugin_inventory"),
    "permission_mode": ("permission_mode", "permissionMode", "permission-mode"),
    "output_style": ("output_style", "outputStyle"),
    "host_version": ("host_version", "hostVersion", "claude_code_version"),
    "hooks": ("hooks", "hook_surface", "lifecycle_hooks"),
    "agents_md": ("agents_md", "agentsMd", "agent_instruction_files"),
    "user_rules": ("user_rules", "userRules", "user_agent_rules"),
    "instruction_sources": (
        "instruction_sources",
        "instructionSources",
        "project_instruction_sources",
    ),
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

SKILL_INVENTORY_DIMENSION = "skills"

# Observation states for one global instruction surface dimension.  Absence of
# evidence is never equality: it is UNOBSERVABLE, and ambiguous, malformed, or
# one-sided evidence is UNRESOLVED.
SURFACE_EQUAL = "EQUAL"
SURFACE_DIFFERENT = "DIFFERENT"
SURFACE_UNOBSERVABLE = "UNOBSERVABLE"
SURFACE_UNRESOLVED = "UNRESOLVED"
SURFACE_ALLOWED_TREATMENT_DELTA = "ALLOWED_TREATMENT_DELTA"
_SURFACE_OBSERVED = "OBSERVED"

# The declared treatment either adds/removes a carrier identity or changes the
# content carried by an identity present in both arms.
TREATMENT_MODE_IDENTITY_PRESENCE = "IDENTITY_PRESENCE"
TREATMENT_MODE_CONTENT_DELTA = "CONTENT_DELTA"
TREATMENT_MODES = frozenset(
    {TREATMENT_MODE_IDENTITY_PRESENCE, TREATMENT_MODE_CONTENT_DELTA}
)

TREATMENT_DELTA_NONE = "NONE"
TREATMENT_DELTA_IDENTITY = "IDENTITY_DELTA"
TREATMENT_DELTA_CONTENT = "CONTENT_DELTA"
TREATMENT_DELTA_UNRESOLVED = "UNRESOLVED"

# Every global/model-visible instruction surface this harness knows how to
# classify from init evidence.  Membership here does not assert that a
# dimension is observable in any particular runtime; an unobserved dimension is
# reported as UNOBSERVABLE rather than assumed equal.  The harness deliberately
# does not crawl the home directory to make an unobservable dimension look
# resolved.
GLOBAL_INSTRUCTION_SURFACE_DIMENSIONS = (
    "tools",
    "agents",
    "mcp_servers",
    "plugins",
    "permission_mode",
    "output_style",
    "host_version",
    SKILL_INVENTORY_DIMENSION,
    "hooks",
    "agents_md",
    "user_rules",
    "instruction_sources",
)

# Runner self-binding states.  ``final_runtime_authority`` names the executable
# owner of an epoch, so the runner that is actually executing must prove it is
# that pinned component before any provider call.  Absence is never equality: a
# supplied identity that is missing or unusable and an actual identity that
# cannot be derived are each distinct from an outright mismatch.
RUNNER_BINDING_BOUND = "BOUND"
RUNNER_BINDING_SUPPLIED_MISSING = "SUPPLIED_IDENTITY_MISSING"
RUNNER_BINDING_ACTUAL_UNAVAILABLE = "ACTUAL_IDENTITY_UNAVAILABLE"
RUNNER_BINDING_MISMATCH = "ACTUAL_SUPPLIED_MISMATCH"

# The final_runtime_authority component this runner is required to be, and the
# manifest status that makes a component identity usable at all.
_RUNNER_COMPONENT = "runner"
_IDENTITY_PINNED = "PINNED"


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
    treatment_surface: TreatmentSurface
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

    @property
    def unique_assistant_message_ids(self) -> tuple[str, ...]:
        """Deduplicate IDs without losing their first-observed stream order."""

        return tuple(dict.fromkeys(self.assistant_message_ids))

    def _evidence_record(self) -> dict[str, Any]:
        return {
            "adapter_identity": self.adapter_identity.as_record(),
            "provider_binary_identity": self.provider_binary_identity.as_record(),
            "provider_policy_identity": self.provider_policy_identity,
            "provider_policy": copy.deepcopy(dict(self.provider_policy)),
            "invocation": self.invocation.as_record(),
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
            "total_cost_usd": (
                None if self.total_cost_usd is None else str(self.total_cost_usd)
            ),
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
class RunnerSelfBinding:
    """Whether the executing runner is the runner final_runtime_authority pins."""

    passed: bool = False
    state: str = RUNNER_BINDING_ACTUAL_UNAVAILABLE
    source_path: str | None = None
    actual_blob_oid: str | None = None
    actual_sha256: str | None = None
    supplied_blob_oid: str | None = None
    supplied_sha256: str | None = None
    reasons: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "state": self.state,
            "source_path": self.source_path,
            "actual_blob_oid": self.actual_blob_oid,
            "actual_sha256": self.actual_sha256,
            "supplied_blob_oid": self.supplied_blob_oid,
            "supplied_sha256": self.supplied_sha256,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class TreatmentSurface:
    """The one declared surface on which the two arms are allowed to differ.

    ``mode`` separates the two admissible designs.  ``IDENTITY_PRESENCE`` adds or
    removes a carrier identity from the skill inventory.  ``CONTENT_DELTA`` keeps
    the identity present in both arms and changes only the content that identity
    carries, so identity equality and an allowed content delta stay distinct
    facts.  ``identity`` is declared, never assumed, so no specific carrier is
    hard-coded into the gate.
    """

    dimension: str
    mode: str
    identity: str
    baseline_contains_identity: bool
    expected_treatment_delta: bool

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, str) or not self.dimension:
            raise HarnessContractError("treatment dimension must be a non-empty string")
        if self.mode not in TREATMENT_MODES:
            raise HarnessContractError(f"unknown treatment mode {self.mode!r}")
        if not isinstance(self.identity, str) or not self.identity.strip():
            raise HarnessContractError("treatment identity must be a non-empty string")
        if not isinstance(self.baseline_contains_identity, bool) or not isinstance(
            self.expected_treatment_delta, bool
        ):
            raise HarnessContractError("treatment presence and delta must be booleans")
        object.__setattr__(self, "identity", _normalize_skill_identity(self.identity))
        if self.mode == TREATMENT_MODE_IDENTITY_PRESENCE:
            if self.dimension != SKILL_INVENTORY_DIMENSION:
                raise HarnessContractError(
                    "identity-presence treatment must act on the skill inventory"
                )
        else:
            if self.dimension == SKILL_INVENTORY_DIMENSION:
                raise HarnessContractError(
                    "content-delta treatment must name a content dimension, not the "
                    "skill inventory"
                )
            if not self.baseline_contains_identity:
                raise HarnessContractError(
                    "content-delta treatment requires the identity in both arms"
                )

    @property
    def candidate_contains_identity(self) -> bool:
        if self.mode == TREATMENT_MODE_IDENTITY_PRESENCE:
            return self.baseline_contains_identity != self.expected_treatment_delta
        return self.baseline_contains_identity

    def as_record(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "mode": self.mode,
            "identity": self.identity,
            "baseline_contains_identity": self.baseline_contains_identity,
            "candidate_contains_identity": self.candidate_contains_identity,
            "expected_treatment_delta": self.expected_treatment_delta,
        }


@dataclass(frozen=True)
class GlobalSurfaceIdentity:
    """Baseline/candidate equality across every classifiable instruction surface."""

    passed: bool = False
    treatment_surface: TreatmentSurface | None = None
    dimension_states: Mapping[str, str] = field(default_factory=dict)
    mandatory_dimensions: tuple[str, ...] = ()
    observable_dimensions: tuple[str, ...] = ()
    unobservable_dimensions: tuple[str, ...] = ()
    unresolved_dimensions: tuple[str, ...] = ()
    differing_dimensions: tuple[str, ...] = ()
    identity_equality: bool | None = None
    allowed_treatment_delta: bool = False
    treatment_delta_kind: str = TREATMENT_DELTA_UNRESOLVED
    reasons: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "treatment_surface": (
                None
                if self.treatment_surface is None
                else self.treatment_surface.as_record()
            ),
            "dimension_states": dict(self.dimension_states),
            "mandatory_dimensions": list(self.mandatory_dimensions),
            "observable_dimensions": list(self.observable_dimensions),
            "unobservable_dimensions": list(self.unobservable_dimensions),
            "unresolved_dimensions": list(self.unresolved_dimensions),
            "differing_dimensions": list(self.differing_dimensions),
            "identity_equality": self.identity_equality,
            "allowed_treatment_delta": self.allowed_treatment_delta,
            "treatment_delta_kind": self.treatment_delta_kind,
            "reasons": list(self.reasons),
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
    skill_inventory_found: bool = False
    errors: tuple[str, ...] = ()

    def contains_identity(self, identity: str) -> bool | None:
        """Report carrier engagement for any declared identity, not just Fable."""

        if not self.skills_resolved:
            return None
        return _normalize_skill_identity(identity) in self.skill_identities

    @property
    def fable_engaged(self) -> bool | None:
        return self.contains_identity(FABLE_SKILL_IDENTITY)


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
    global_surface: GlobalSurfaceIdentity | None = None

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
            "global_surface": (
                None if self.global_surface is None else self.global_surface.as_record()
            ),
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
    runner_self_binding: RunnerSelfBinding
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
            "runner_self_binding": self.runner_self_binding.as_record(),
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


def _git_blob_oid(payload: bytes) -> str:
    """The Git blob object id of exact bytes, matching ``git hash-object``."""

    header = f"blob {len(payload)}".encode("ascii") + b"\x00"
    return hashlib.sha1(header + payload).hexdigest()


def _runner_source_path() -> Path | None:
    """The source file of the runner implementation that is executing now."""

    source = globals().get("__file__")
    if not isinstance(source, str) or not source:
        return None
    try:
        return Path(source).resolve()
    except (OSError, RuntimeError):
        return Path(source)


def derive_executing_runner_identity() -> dict[str, Any]:
    """Derive blob/sha256 identity from the runner implementation executing now.

    The identity is read from this module's own source, never from a
    caller-supplied value, so a manifest cannot assert the fact it is meant to
    prove.  An identity that cannot be read is reported with an explicit error
    rather than defaulted to any value.
    """

    source_path = _runner_source_path()
    if source_path is None:
        return {
            "source_path": None,
            "blob_oid": None,
            "sha256": None,
            "error": "runner_source_path_unresolved",
        }
    try:
        payload = source_path.read_bytes()
    except (OSError, ValueError) as exc:
        return {
            "source_path": str(source_path),
            "blob_oid": None,
            "sha256": None,
            "error": f"runner_source_unreadable:{type(exc).__name__}",
        }
    return {
        "source_path": str(source_path),
        "blob_oid": _git_blob_oid(payload),
        "sha256": _sha256_bytes(payload),
        "error": None,
    }


def _supplied_runner_identity(
    manifest: Mapping[str, Any],
) -> tuple[tuple[str, str] | None, str | None]:
    """The runner identity final_runtime_authority pins, or why it is unusable."""

    authority = manifest.get("final_runtime_authority")
    if authority is None:
        return None, "final_runtime_authority_absent"
    if not isinstance(authority, Mapping):
        return None, "final_runtime_authority_is_not_an_object"
    components = authority.get("components")
    if components is None:
        return None, "final_runtime_authority_components_absent"
    if not isinstance(components, Mapping):
        return None, "final_runtime_authority_components_is_not_an_object"
    component = components.get(_RUNNER_COMPONENT)
    if component is None:
        return None, "final_runtime_authority_runner_component_absent"
    if not isinstance(component, Mapping):
        return None, "final_runtime_authority_runner_component_is_not_an_object"
    if component.get("status") != _IDENTITY_PINNED:
        return None, "supplied_runner_identity_not_pinned"

    blob_oid = component.get("blob_oid")
    sha256 = component.get("sha256")
    for label, value in (("blob_oid", blob_oid), ("sha256", sha256)):
        if value is None:
            return None, f"supplied_runner_{label}_absent"
        if not isinstance(value, str):
            return None, f"supplied_runner_{label}_is_not_a_string"
    if not _GIT_OID_RE.fullmatch(blob_oid):
        return None, "supplied_runner_blob_oid_malformed"
    if not _SHA256_HEX_RE.fullmatch(sha256):
        return None, "supplied_runner_sha256_malformed"
    return (blob_oid, sha256), None


def evaluate_runner_self_binding(manifest: Mapping[str, Any]) -> RunnerSelfBinding:
    """Bind the executing runner to the runner final_runtime_authority pins.

    ``canonical_harness`` is deliberately not consulted: it freezes the
    historical base scaffold and may legitimately differ from the runner that
    executes an epoch, whose executable owner is ``final_runtime_authority``.
    A supplied identity that is missing or unusable, an actual identity that
    cannot be derived, and an actual identity that differs from the supplied one
    are three distinct states, and none of them is provider-call eligible.
    """

    actual = derive_executing_runner_identity()
    supplied, supplied_reason = _supplied_runner_identity(manifest)

    reasons: list[str] = []
    if supplied_reason is not None:
        reasons.append(supplied_reason)
    if actual["error"] is not None:
        reasons.append(actual["error"])

    observed: dict[str, Any] = {
        "source_path": actual["source_path"],
        "actual_blob_oid": actual["blob_oid"],
        "actual_sha256": actual["sha256"],
        "supplied_blob_oid": None if supplied is None else supplied[0],
        "supplied_sha256": None if supplied is None else supplied[1],
    }

    if supplied is None:
        return RunnerSelfBinding(
            passed=False,
            state=RUNNER_BINDING_SUPPLIED_MISSING,
            reasons=tuple(reasons),
            **observed,
        )
    if actual["blob_oid"] is None or actual["sha256"] is None:
        return RunnerSelfBinding(
            passed=False,
            state=RUNNER_BINDING_ACTUAL_UNAVAILABLE,
            reasons=tuple(reasons),
            **observed,
        )

    mismatches: list[str] = []
    if actual["blob_oid"] != supplied[0]:
        mismatches.append("runner_blob_oid_mismatch")
    if actual["sha256"] != supplied[1]:
        mismatches.append("runner_sha256_mismatch")
    if mismatches:
        return RunnerSelfBinding(
            passed=False,
            state=RUNNER_BINDING_MISMATCH,
            reasons=tuple(reasons + mismatches),
            **observed,
        )
    return RunnerSelfBinding(
        passed=True,
        state=RUNNER_BINDING_BOUND,
        reasons=tuple(reasons),
        **observed,
    )


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


def _manifest_treatment_surface(
    manifest: Mapping[str, Any], expected_treatment_delta: bool
) -> TreatmentSurface:
    """Resolve the declared treatment surface, defaulting to carrier presence."""

    declared = manifest.get("treatment_surface")
    if declared is None:
        return default_treatment_surface(expected_treatment_delta)
    if not isinstance(declared, Mapping):
        raise HarnessContractError("manifest treatment_surface must be an object")
    allowed = {"dimension", "mode", "identity", "baseline_contains_identity"}
    unknown = sorted(set(declared) - allowed)
    if unknown:
        raise HarnessContractError(
            f"manifest treatment_surface has unknown keys: {unknown}"
        )
    missing = sorted(allowed - set(declared))
    if missing:
        raise HarnessContractError(
            f"manifest treatment_surface is missing keys: {missing}"
        )
    return TreatmentSurface(
        dimension=declared["dimension"],
        mode=declared["mode"],
        identity=declared["identity"],
        baseline_contains_identity=declared["baseline_contains_identity"],
        expected_treatment_delta=expected_treatment_delta,
    )


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
        treatment_surface=_manifest_treatment_surface(manifest, expected),
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
        opaque_id_factory() if opaque_id_factory is not None else secrets.token_hex(16)
    )
    if not isinstance(opaque_identity, str) or not _OPAQUE_ID_RE.fullmatch(opaque_identity):
        raise HarnessContractError("opaque workspace identity must be 32-64 lowercase hex characters")
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


def _semantic_skills(
    init_event: Mapping[str, Any]
) -> tuple[frozenset[str], bool, bool, list[str]]:
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
    return frozenset(identities), found_inventory and not errors, found_inventory, errors


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

    (
        skill_identities,
        skills_resolved,
        skill_inventory_found,
        skill_errors,
    ) = _semantic_skills(event)
    errors.extend(skill_errors)
    return InitEvidence(
        init_event_found=actual_shape,
        valid=actual_shape and not errors,
        candidate_count=1,
        event=event,
        inventories=inventories,
        skill_identities=skill_identities,
        skills_resolved=skills_resolved,
        skill_inventory_found=skill_inventory_found,
        errors=tuple(errors),
    )


def _surface_observation(evidence: InitEvidence, dimension: str) -> tuple[str, Any]:
    """Classify one dimension for one arm without inventing a value."""

    if evidence.event is None:
        return SURFACE_UNOBSERVABLE, None
    if dimension == SKILL_INVENTORY_DIMENSION:
        if evidence.skills_resolved:
            return _SURFACE_OBSERVED, frozenset(evidence.skill_identities)
        if not evidence.skill_inventory_found:
            return SURFACE_UNOBSERVABLE, None
        return SURFACE_UNRESOLVED, None
    value, errors = _dimension_value(evidence.event, dimension)
    if value is not _MISSING:
        return _SURFACE_OBSERVED, value
    if errors and all(error.startswith("missing_inventory:") for error in errors):
        return SURFACE_UNOBSERVABLE, None
    return SURFACE_UNRESOLVED, None


def _compare_observations(
    baseline_state: str, baseline_value: Any, candidate_state: str, candidate_value: Any
) -> str:
    if SURFACE_UNRESOLVED in (baseline_state, candidate_state):
        return SURFACE_UNRESOLVED
    if baseline_state != candidate_state:
        # Observable in exactly one arm.  One-sided evidence proves nothing about
        # the other arm, so it can never be reported as equality.
        return SURFACE_UNRESOLVED
    if baseline_state == SURFACE_UNOBSERVABLE:
        return SURFACE_UNOBSERVABLE
    return SURFACE_EQUAL if baseline_value == candidate_value else SURFACE_DIFFERENT


def _normalized_content_map(value: Any) -> dict[str, Any] | None:
    """Normalize carrier-identity keys; an ambiguous map resolves to nothing."""

    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            return None
        identity = _normalize_skill_identity(key)
        if identity in normalized:
            return None
        normalized[identity] = item
    return normalized


def _identity_presence_verdict(
    treatment: TreatmentSurface, baseline_value: Any, candidate_value: Any
) -> tuple[str, bool, list[str]]:
    """Only the declared identity may enter or leave the inventory."""

    reasons: list[str] = []
    baseline_identities = set(baseline_value)
    candidate_identities = set(candidate_value)
    if (treatment.identity in baseline_identities) != treatment.baseline_contains_identity:
        reasons.append("baseline_treatment_identity_presence_mismatch")
    if (
        treatment.identity in candidate_identities
    ) != treatment.candidate_contains_identity:
        reasons.append("candidate_treatment_identity_presence_mismatch")
    residual_baseline = baseline_identities - {treatment.identity}
    residual_candidate = candidate_identities - {treatment.identity}
    if residual_baseline != residual_candidate:
        delta = sorted(residual_baseline.symmetric_difference(residual_candidate))
        reasons.append("non_treatment_identity_delta:" + ",".join(delta))
    equal = baseline_identities == candidate_identities
    if reasons:
        return (SURFACE_EQUAL if equal else SURFACE_DIFFERENT), False, reasons
    if treatment.expected_treatment_delta:
        return SURFACE_ALLOWED_TREATMENT_DELTA, True, reasons
    return SURFACE_EQUAL, True, reasons


def _content_delta_verdict(
    treatment: TreatmentSurface,
    baseline_map: Mapping[str, Any],
    candidate_map: Mapping[str, Any],
) -> tuple[str, bool, list[str]]:
    """Only the declared identity's carried content may differ."""

    reasons: list[str] = []
    if (
        treatment.identity not in baseline_map
        or treatment.identity not in candidate_map
    ):
        reasons.append("treatment_identity_absent_from_content_dimension")
    if set(baseline_map) != set(candidate_map):
        delta = sorted(set(baseline_map).symmetric_difference(set(candidate_map)))
        reasons.append("non_treatment_content_key_delta:" + ",".join(delta))
    residual_baseline = {
        key: value for key, value in baseline_map.items() if key != treatment.identity
    }
    residual_candidate = {
        key: value for key, value in candidate_map.items() if key != treatment.identity
    }
    if residual_baseline != residual_candidate:
        reasons.append("non_treatment_content_value_delta")
    equal = baseline_map == candidate_map
    if reasons:
        return (SURFACE_EQUAL if equal else SURFACE_DIFFERENT), False, reasons
    observed_delta = (
        baseline_map[treatment.identity] != candidate_map[treatment.identity]
    )
    if observed_delta is not treatment.expected_treatment_delta:
        reasons.append("treatment_content_delta_does_not_match_declaration")
        return (SURFACE_DIFFERENT if observed_delta else SURFACE_EQUAL), False, reasons
    if observed_delta:
        return SURFACE_ALLOWED_TREATMENT_DELTA, True, reasons
    return SURFACE_EQUAL, True, reasons


def default_treatment_surface(expected_treatment_delta: bool) -> TreatmentSurface:
    """The default contract: treatment adds the Fable carrier to the inventory."""

    return TreatmentSurface(
        dimension=SKILL_INVENTORY_DIMENSION,
        mode=TREATMENT_MODE_IDENTITY_PRESENCE,
        identity=FABLE_SKILL_IDENTITY,
        baseline_contains_identity=False,
        expected_treatment_delta=expected_treatment_delta,
    )


def evaluate_global_instruction_surface_identity(
    baseline: InitEvidence,
    candidate: InitEvidence,
    *,
    treatment_surface: TreatmentSurface,
    mandatory_dimensions: Sequence[str] = (),
) -> GlobalSurfaceIdentity:
    """Fail closed unless only the declared treatment surface differs.

    Every classifiable global/model-visible instruction surface is compared.  A
    dimension this harness cannot observe is reported ``UNOBSERVABLE`` and a
    dimension whose evidence is ambiguous, malformed, or present in only one arm
    is reported ``UNRESOLVED``; neither is ever converted into equality.  A
    mandatory dimension must be affirmatively equal (or be the declared treatment
    surface); any other dimension may be unobservable but never different or
    unresolved.
    """

    if not isinstance(treatment_surface, TreatmentSurface):
        raise HarnessContractError("a declared TreatmentSurface is required")

    dimensions: list[str] = []
    for name in (
        *GLOBAL_INSTRUCTION_SURFACE_DIMENSIONS,
        *mandatory_dimensions,
        SKILL_INVENTORY_DIMENSION,
        treatment_surface.dimension,
    ):
        if not isinstance(name, str) or not name:
            raise HarnessContractError("surface dimensions must be non-empty strings")
        if name not in dimensions:
            dimensions.append(name)

    mandatory: list[str] = []
    for name in (
        *mandatory_dimensions,
        SKILL_INVENTORY_DIMENSION,
        treatment_surface.dimension,
    ):
        if name not in mandatory:
            mandatory.append(name)

    reasons: list[str] = []
    if baseline.event is None:
        reasons.append("baseline_init_event_absent")
    if candidate.event is None:
        reasons.append("candidate_init_event_absent")

    observations: dict[str, tuple[str, Any, str, Any]] = {}
    states: dict[str, str] = {}
    for name in dimensions:
        baseline_state, baseline_value = _surface_observation(baseline, name)
        candidate_state, candidate_value = _surface_observation(candidate, name)
        observations[name] = (
            baseline_state,
            baseline_value,
            candidate_state,
            candidate_value,
        )
        states[name] = _compare_observations(
            baseline_state, baseline_value, candidate_state, candidate_value
        )

    skill_observation = observations[SKILL_INVENTORY_DIMENSION]
    identity_equality: bool | None = None
    if (
        skill_observation[0] == _SURFACE_OBSERVED
        and skill_observation[2] == _SURFACE_OBSERVED
    ):
        identity_equality = skill_observation[1] == skill_observation[3]

    treatment_dimension = treatment_surface.dimension
    baseline_state, baseline_value, candidate_state, candidate_value = observations[
        treatment_dimension
    ]
    treatment_satisfied = False
    if baseline_state != _SURFACE_OBSERVED or candidate_state != _SURFACE_OBSERVED:
        reasons.append(f"treatment_surface_not_observed:{treatment_dimension}")
    elif treatment_surface.mode == TREATMENT_MODE_IDENTITY_PRESENCE:
        state, treatment_satisfied, treatment_reasons = _identity_presence_verdict(
            treatment_surface, baseline_value, candidate_value
        )
        states[treatment_dimension] = state
        reasons.extend(treatment_reasons)
    else:
        baseline_map = _normalized_content_map(baseline_value)
        candidate_map = _normalized_content_map(candidate_value)
        if baseline_map is None or candidate_map is None:
            states[treatment_dimension] = SURFACE_UNRESOLVED
            reasons.append("treatment_content_dimension_is_not_an_identity_map")
        else:
            state, treatment_satisfied, treatment_reasons = _content_delta_verdict(
                treatment_surface, baseline_map, candidate_map
            )
            states[treatment_dimension] = state
            reasons.extend(treatment_reasons)
        # A content-delta design asserts identity equality; it is a separate,
        # affirmatively required fact, not a by-product of the content check.
        if states[SKILL_INVENTORY_DIMENSION] != SURFACE_EQUAL or identity_equality is not True:
            treatment_satisfied = False
            reasons.append("content_delta_requires_skill_identity_equality")
        else:
            for arm, evidence in (("baseline", baseline), ("candidate", candidate)):
                if evidence.contains_identity(treatment_surface.identity) is not True:
                    treatment_satisfied = False
                    reasons.append(f"{arm}_missing_declared_treatment_identity")

    for name in mandatory:
        if states[name] not in (SURFACE_EQUAL, SURFACE_ALLOWED_TREATMENT_DELTA):
            reasons.append(f"mandatory_dimension_not_equal:{name}:{states[name]}")
    for name in dimensions:
        if name in mandatory:
            continue
        if states[name] in (SURFACE_DIFFERENT, SURFACE_UNRESOLVED):
            reasons.append(f"non_treatment_surface_delta:{name}:{states[name]}")

    passed = (
        treatment_satisfied
        and all(
            states[name] in (SURFACE_EQUAL, SURFACE_ALLOWED_TREATMENT_DELTA)
            for name in mandatory
        )
        and all(
            states[name]
            in (SURFACE_EQUAL, SURFACE_UNOBSERVABLE, SURFACE_ALLOWED_TREATMENT_DELTA)
            for name in dimensions
        )
    )

    if not treatment_satisfied:
        delta_kind = TREATMENT_DELTA_UNRESOLVED
    elif states[treatment_dimension] == SURFACE_ALLOWED_TREATMENT_DELTA:
        delta_kind = (
            TREATMENT_DELTA_IDENTITY
            if treatment_surface.mode == TREATMENT_MODE_IDENTITY_PRESENCE
            else TREATMENT_DELTA_CONTENT
        )
    else:
        delta_kind = TREATMENT_DELTA_NONE

    return GlobalSurfaceIdentity(
        passed=passed,
        treatment_surface=treatment_surface,
        dimension_states=dict(states),
        mandatory_dimensions=tuple(mandatory),
        observable_dimensions=tuple(
            name
            for name in dimensions
            if states[name]
            in (SURFACE_EQUAL, SURFACE_DIFFERENT, SURFACE_ALLOWED_TREATMENT_DELTA)
        ),
        unobservable_dimensions=tuple(
            name for name in dimensions if states[name] == SURFACE_UNOBSERVABLE
        ),
        unresolved_dimensions=tuple(
            name for name in dimensions if states[name] == SURFACE_UNRESOLVED
        ),
        differing_dimensions=tuple(
            name for name in dimensions if states[name] == SURFACE_DIFFERENT
        ),
        identity_equality=identity_equality,
        allowed_treatment_delta=(
            treatment_satisfied
            and states[treatment_dimension] == SURFACE_ALLOWED_TREATMENT_DELTA
        ),
        treatment_delta_kind=delta_kind,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def evaluate_purity(
    run_events: Iterable[Event],
    reference_events: Iterable[Event],
    *,
    expected_fable_engaged: bool,
    frozen_dimensions: Sequence[str],
    surface_audit: SurfaceAudit | None,
    treatment_surface: TreatmentSurface | None = None,
) -> PurityResult:
    """Evaluate every purity dimension affirmatively; absence never passes."""

    run = parse_init_events(run_events, frozen_dimensions)
    reference = parse_init_events(reference_events, frozen_dimensions)
    treatment = (
        default_treatment_surface(expected_fable_engaged)
        if treatment_surface is None
        else treatment_surface
    )
    if treatment.expected_treatment_delta is not expected_fable_engaged:
        raise HarnessContractError(
            "declared treatment surface contradicts the slot's expected treatment state"
        )
    global_surface = evaluate_global_instruction_surface_identity(
        reference,
        run,
        treatment_surface=treatment,
        mandatory_dimensions=frozen_dimensions,
    )
    checks: dict[str, bool] = {
        "run_init_valid": run.valid,
        "reference_init_valid": reference.valid,
        "model_visible_condition_neutral": bool(surface_audit and surface_audit.passed),
        "run_semantic_skill_inventory_resolved": run.skills_resolved,
        "reference_semantic_skill_inventory_resolved": reference.skills_resolved,
        "global_instruction_surface_identity": global_surface.passed,
    }

    fable_engaged = run.contains_identity(treatment.identity)
    checks["intended_treatment_state"] = (
        fable_engaged is not None
        and fable_engaged is treatment.candidate_contains_identity
    )

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
    reasons.extend(f"global_surface:{reason}" for reason in global_surface.reasons)
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
        global_surface=global_surface,
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

    A runner that cannot prove it is the component pinned by
    ``final_runtime_authority``, invalid reference evidence, or model-visible
    leakage prevents executor invocation.  Provider failures retain a typed
    execution record; unresolved Git state and any provider/purity failure make
    the observation non-countable.
    """

    runner_self_binding = evaluate_runner_self_binding(manifest)
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
        and reference.contains_identity(slot.treatment_surface.identity)
        is slot.treatment_surface.baseline_contains_identity
    )

    executor_called = False
    provider_execution: ProviderExecution | None = None
    run_records: tuple[Mapping[str, Any], ...] = ()
    if (
        runner_self_binding.passed
        and surface_audit.passed
        and reference_preflight_pass
        and materializer_error is None
    ):
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
        treatment_surface=slot.treatment_surface,
    )
    git_state = capture_git_state(workspace)
    countable = (
        runner_self_binding.passed
        and purity.run_countable
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
        runner_self_binding=runner_self_binding,
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
    "GLOBAL_INSTRUCTION_SURFACE_DIMENSIONS",
    "GitState",
    "GlobalSurfaceIdentity",
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
    "RUNNER_BINDING_ACTUAL_UNAVAILABLE",
    "RUNNER_BINDING_BOUND",
    "RUNNER_BINDING_MISMATCH",
    "RUNNER_BINDING_SUPPLIED_MISSING",
    "RunEvidence",
    "RunnerSelfBinding",
    "SKILL_INVENTORY_DIMENSION",
    "SURFACE_ALLOWED_TREATMENT_DELTA",
    "SURFACE_DIFFERENT",
    "SURFACE_EQUAL",
    "SURFACE_UNOBSERVABLE",
    "SURFACE_UNRESOLVED",
    "SurfaceAudit",
    "TREATMENT_MODE_CONTENT_DELTA",
    "TREATMENT_MODE_IDENTITY_PRESENCE",
    "TreatmentSurface",
    "WorkspacePlan",
    "audit_model_visible_surfaces",
    "build_model_invocation",
    "capture_git_state",
    "create_condition_neutral_workspace",
    "default_treatment_surface",
    "derive_executing_runner_identity",
    "evaluate_global_instruction_surface_identity",
    "evaluate_purity",
    "evaluate_runner_self_binding",
    "execute_manifest_slot",
    "load_manifest",
    "parse_init_events",
    "select_manifest_slot",
]
