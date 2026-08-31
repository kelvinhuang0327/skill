#!/usr/bin/env python3
"""Compile and validate a prospective Fable ablation epoch manifest.

The compiler is deliberately prospective and provider-neutral.  It serializes
only supplied identities and fixed CTO decisions; it does not materialize an
authority root, sign a manifest, or invoke a provider.  A manifest becomes
eligible for an external Owner signoff only after every publication gate is
affirmatively resolved.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence


MANIFEST_SCHEMA = "FABLE_ABLATION_PROSPECTIVE_EPOCH/v3"
OWNER_SIGNOFF_SCHEMA = "FABLE_ABLATION_OWNER_SIGNOFF/v1"
S04_ATTESTATION_SCHEMA = "FABLE_ABLATION_S04_BUDGET_ATTESTATION/v1"
CTO_PROSPECTIVE_EPOCH_ID = "56a62a62e5aae9fe3a230cbbed831a2a"
AUTHORITY_ROOT = "/Users/kelvin/fable-ablation"
FORBIDDEN_OUTPUT_ROOTS = (
    AUTHORITY_ROOT,
    "/Users/kelvin/fable-ablation-r1",
    "/Users/kelvin/fable-ablation-r2",
)

CANONICAL_FABLE = {
    "head": "946ed9885850ddfe7a97af392e9c64aa09f8fb18",
    "tree": "f40799e247700fcd9fc6bd98848baf39ef665ef9",
    "carrier_subtree": "2955ce579a1fb0c029fae7e8fdbfe777a1c91985",
    "carrier_skill_md_sha256": (
        "75ffb4afeda065d827eee215b3c4e0e629ee24a33bdbd378db27766aa69ab260"
    ),
}

CANONICAL_HARNESS = {
    "head": "dfb8db36b6aec027dc4d257c4bb62ec512b871cc",
    "tree": "d8daa91a085c6b4bce500098fe4b665b7ee13ded",
    "runner": {
        "path": "analysis/fable-skill-ablation-r1/runner.py",
        "sha256": "da150cb61f6f1b0e39bcd216a148eaa5c8d8d88767ee8b90c39a285896b6c9a2",
    },
    "test_runner": {
        "path": "analysis/fable-skill-ablation-r1/test_runner.py",
        "sha256": "b132adc3bbe8e2f4c468778f4fb5d73e679ed75881378322ce3b628f17eaad8f",
    },
}

HISTORICAL_FORENSIC_SPEND_USD = "0.2660758"
LIFETIME_HARD_CAP_USD = "10.0000000"
NOMINAL_MAX_ADDITIONAL_USD = "9.7339242"
INITIAL_NEW_EPOCH_SPEND_USD = "0.0000000"
INITIAL_OUTSTANDING_RESERVATIONS_USD = "0.0000000"
PER_INVOCATION_CAP_USD = "2.0000000"
PAIR_RESERVATION_USD = "4.0000000"
S04_RUN_ID = "s04-B01-r0-OFF"
S04_COST_STATE = "UNKNOWN_PERMANENTLY"

_EPOCH_ID_RE = re.compile(r"[0-9a-f]{32}")
_GIT_OID_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MONEY_RE = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]{7}")
_MONEY_QUANTUM = Decimal("0.0000001")

_SCHEDULE_SPEC = (
    ("P01", "A01", ("OFF", "ON")),
    ("P02", "B01", ("ON", "OFF")),
    ("P03", "A02", ("ON", "OFF")),
    ("P04", "B02", ("OFF", "ON")),
    ("P05", "A03", ("OFF", "ON")),
    ("P06", "B03", ("ON", "OFF")),
    ("P07", "A04", ("ON", "OFF")),
    ("P08", "B04", ("OFF", "ON")),
)
TASK_IDS = tuple(item[1] for item in _SCHEDULE_SPEC)

GATE_ORDER = (
    "final_runtime_identity",
    "provider_adapter_identity",
    "provider_runtime_identity",
    "lock_tool_identities",
    "task_prompt_fixture_oracle_hashes",
    "runtime_containment_policy",
    "evidence_schema",
    "lifetime_budget_authority",
    "s04_budget_attestation",
    "empty_prospective_epoch_state",
    "pair_reservation_capacity",
)

OWNER_SIGNOFF_REQUIRED_FIELDS = (
    "schema",
    "decision",
    "epoch_id",
    "manifest_sha256",
    "lifetime_hard_cap_usd",
    "historical_spend_usd",
    "max_additional_spend_usd",
    "authorization_source_identity",
)

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "authority_lineage",
        "epoch_id",
        "epoch_namespace",
        "manifest_hash_scope",
        "canonical_fable",
        "canonical_harness",
        "final_runtime_authority",
        "provider_adapter",
        "provider_runtime",
        "fixture_oracle_index",
        "tasks",
        "schedule",
        "budget",
        "runtime_containment",
        "lock_semantics",
        "evidence_schema",
        "historical_quarantine",
        "claim_boundary",
        "lifecycle",
        "forbidden_actions",
        "publication_gate",
        "owner_signoff_gate",
        "status",
    }
)


class ManifestValidationError(ValueError):
    """Raised when a manifest or one of its prospective inputs is invalid."""


class OwnerSignoffValidationError(ManifestValidationError):
    """Raised when an external Owner-signoff sidecar does not bind exactly."""


def _fail(message: str) -> None:
    raise ManifestValidationError(message)


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        _fail(f"{label} keys must be strings")
    return value


def _reject_unknown_keys(
    value: Mapping[str, Any], allowed: Sequence[str], label: str
) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        _fail(f"{label} has unknown keys: {', '.join(unknown)}")


def _reject_non_json_contract_types(value: Any, label: str = "manifest") -> None:
    if isinstance(value, float):
        _fail(f"{label} must not contain binary floating-point values")
    if value is None or isinstance(value, (str, int, bool)):
        return

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail(f"{label} contains a non-string object key")
            _reject_non_json_contract_types(child, f"{label}.{key}")
        return

    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_non_json_contract_types(child, f"{label}[{index}]")
        return

    _fail(f"{label} contains unsupported type {type(value).__name__}")


def validate_epoch_id(epoch_id: str) -> str:
    """Validate an externally supplied 128-bit lowercase-hex epoch ID."""

    if not isinstance(epoch_id, str) or _EPOCH_ID_RE.fullmatch(epoch_id) is None:
        _fail("epoch_id must be exactly 32 lowercase hexadecimal characters")
    return epoch_id


def _validate_optional_hex(value: Any, pattern: re.Pattern[str], label: str) -> None:
    if value is not None and (
        not isinstance(value, str) or pattern.fullmatch(value) is None
    ):
        _fail(f"{label} has an invalid identity shape")


def _money(value: Any, label: str) -> Decimal:
    if not isinstance(value, str) or _MONEY_RE.fullmatch(value) is None:
        _fail(f"{label} must be a non-negative decimal string with seven places")
    return Decimal(value)


def _format_money(value: Decimal) -> str:
    if value < 0:
        _fail("internal monetary result must not be negative")
    return format(value.quantize(_MONEY_QUANTUM), "f")


def _schedule_records() -> list[dict[str, Any]]:
    return [
        {"pair_id": pair_id, "task_id": task_id, "arm_order": list(arms)}
        for pair_id, task_id, arms in _SCHEDULE_SPEC
    ]


def _normalize_artifact_identity(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {"status": "UNRESOLVED", "blob_oid": None, "sha256": None}
    source = _require_mapping(value, label)
    _reject_unknown_keys(source, ("blob_oid", "sha256"), label)
    blob_oid = source.get("blob_oid")
    sha256 = source.get("sha256")
    _validate_optional_hex(blob_oid, _GIT_OID_RE, f"{label}.blob_oid")
    _validate_optional_hex(sha256, _SHA256_RE, f"{label}.sha256")
    status = "PINNED" if blob_oid is not None and sha256 is not None else "UNRESOLVED"
    return {"status": status, "blob_oid": blob_oid, "sha256": sha256}


def _artifact_input(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    source = _require_mapping(value, label)
    if set(source) != {"status", "blob_oid", "sha256"}:
        _fail(f"{label} has an invalid normalized shape")
    return {"blob_oid": source["blob_oid"], "sha256": source["sha256"]}


def _normalize_final_runtime(value: Any) -> dict[str, Any]:
    allowed = ("commit", "tree", "runner", "controller", "sandbox")
    if value is None:
        source: Mapping[str, Any] = {}
    else:
        source = _require_mapping(value, "final_runtime_authority")
        _reject_unknown_keys(source, allowed, "final_runtime_authority")

    commit = source.get("commit")
    tree = source.get("tree")
    _validate_optional_hex(commit, _GIT_OID_RE, "final_runtime_authority.commit")
    _validate_optional_hex(tree, _GIT_OID_RE, "final_runtime_authority.tree")
    components = {
        name: _normalize_artifact_identity(
            source.get(name), f"final_runtime_authority.{name}"
        )
        for name in ("runner", "controller", "sandbox")
    }
    pinned = bool(commit and tree) and all(
        component["status"] == "PINNED" for component in components.values()
    )
    return {
        "status": "PINNED" if pinned else "UNKNOWN_REQUIRED_INPUT",
        "publication_state": (
            "PUBLISHED_DESCENDANT_PINNED" if pinned else "UNKNOWN_REQUIRED_INPUT"
        ),
        "commit": commit,
        "tree": tree,
        "components": components,
    }


def _final_runtime_input(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _require_mapping(value, "final_runtime_authority")
    if set(source) != {
        "status",
        "publication_state",
        "commit",
        "tree",
        "components",
    }:
        _fail("final_runtime_authority has an invalid normalized shape")
    components = _require_mapping(
        source["components"], "final_runtime_authority.components"
    )
    if set(components) != {"runner", "controller", "sandbox"}:
        _fail("final_runtime_authority.components must pin runner/controller/sandbox")
    return {
        "commit": source["commit"],
        "tree": source["tree"],
        **{
            name: _artifact_input(
                _require_mapping(
                    components[name], f"final_runtime_authority.components.{name}"
                ),
                f"final_runtime_authority.components.{name}",
            )
            for name in ("runner", "controller", "sandbox")
        },
    }


def _normalize_provider_adapter(value: Any) -> dict[str, Any]:
    identity = _normalize_artifact_identity(value, "provider_adapter")
    return {
        **identity,
        "compiler_invokes_provider": False,
        "invocation_contract": "EXTERNAL_INJECTED_ADAPTER_ONLY",
    }


def _provider_adapter_input(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _require_mapping(value, "provider_adapter")
    if set(source) != {
        "status",
        "blob_oid",
        "sha256",
        "compiler_invokes_provider",
        "invocation_contract",
    }:
        _fail("provider_adapter has an invalid normalized shape")
    return {"blob_oid": source["blob_oid"], "sha256": source["sha256"]}


def _normalize_provider_runtime(value: Any) -> dict[str, Any]:
    allowed = ("binary_path", "sha256", "version")
    if value is None:
        source: Mapping[str, Any] = {}
    else:
        source = _require_mapping(value, "provider_runtime")
        _reject_unknown_keys(source, allowed, "provider_runtime")
    binary_path = source.get("binary_path")
    sha256 = source.get("sha256")
    version = source.get("version")
    if binary_path is not None and (
        not isinstance(binary_path, str) or not os.path.isabs(binary_path)
    ):
        _fail("provider_runtime.binary_path must be an absolute path")
    _validate_optional_hex(sha256, _SHA256_RE, "provider_runtime.sha256")
    if version is not None and (not isinstance(version, str) or not version.strip()):
        _fail("provider_runtime.version must be a non-empty string")
    pinned = binary_path is not None and sha256 is not None and version is not None
    return {
        "status": "PINNED" if pinned else "UNRESOLVED",
        "binary_path": binary_path,
        "sha256": sha256,
        "version": version,
        "compiler_invokes_provider": False,
    }


def _provider_runtime_input(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _require_mapping(value, "provider_runtime")
    if set(source) != {
        "status",
        "binary_path",
        "sha256",
        "version",
        "compiler_invokes_provider",
    }:
        _fail("provider_runtime has an invalid normalized shape")
    return {
        "binary_path": source["binary_path"],
        "sha256": source["sha256"],
        "version": source["version"],
    }


def _normalize_task_pins(value: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if value is None:
        source: Mapping[str, Any] = {}
    else:
        source = _require_mapping(value, "task_pins")
        unknown = sorted(set(source) - set(TASK_IDS))
        if unknown:
            _fail(f"task_pins has unknown task IDs: {', '.join(unknown)}")

    pair_by_task = {task_id: pair_id for pair_id, task_id, _ in _SCHEDULE_SPEC}
    tasks: list[dict[str, Any]] = []
    index_entries: list[dict[str, Any]] = []
    for task_id in TASK_IDS:
        raw = source.get(task_id)
        if raw is None:
            pins: Mapping[str, Any] = {}
        else:
            pins = _require_mapping(raw, f"task_pins.{task_id}")
            _reject_unknown_keys(
                pins,
                ("prompt_sha256", "fixture_sha256", "oracle_sha256"),
                f"task_pins.{task_id}",
            )
        normalized: dict[str, Any] = {}
        for name in ("prompt_sha256", "fixture_sha256", "oracle_sha256"):
            pin = pins.get(name)
            _validate_optional_hex(pin, _SHA256_RE, f"task_pins.{task_id}.{name}")
            normalized[name] = pin
        pinned = all(normalized.values())
        tasks.append(
            {
                "task_id": task_id,
                "pair_id": pair_by_task[task_id],
                "status": "PINNED" if pinned else "UNRESOLVED",
                **normalized,
            }
        )
        index_entries.append(
            {
                "task_id": task_id,
                "fixture_sha256": normalized["fixture_sha256"],
                "oracle_sha256": normalized["oracle_sha256"],
            }
        )
    all_pinned = all(task["status"] == "PINNED" for task in tasks)
    return tasks, {
        "schema": "FABLE_ABLATION_FIXTURE_ORACLE_INDEX/v1",
        "status": "PINNED" if all_pinned else "UNRESOLVED",
        "entries": index_entries,
    }


def _task_pins_input(tasks: Any) -> dict[str, Any]:
    if not isinstance(tasks, list):
        _fail("tasks must be a list")
    if len(tasks) != len(TASK_IDS):
        _fail("tasks must contain every fixed task exactly once")
    result: dict[str, Any] = {}
    for expected_id, raw in zip(TASK_IDS, tasks):
        task = _require_mapping(raw, f"tasks.{expected_id}")
        if set(task) != {
            "task_id",
            "pair_id",
            "status",
            "prompt_sha256",
            "fixture_sha256",
            "oracle_sha256",
        }:
            _fail(f"tasks.{expected_id} has an invalid normalized shape")
        if task["task_id"] != expected_id:
            _fail("task ordering or identity differs from the fixed schedule")
        result[expected_id] = {
            "prompt_sha256": task["prompt_sha256"],
            "fixture_sha256": task["fixture_sha256"],
            "oracle_sha256": task["oracle_sha256"],
        }
    return result


def _normalize_runtime_containment(value: Any) -> dict[str, Any]:
    allowed = (
        "policy_id",
        "policy_sha256",
        "epoch_write_scope",
        "network_policy",
        "provider_subprocess_required",
    )
    if value is None:
        source: Mapping[str, Any] = {}
    else:
        source = _require_mapping(value, "runtime_containment")
        _reject_unknown_keys(source, allowed, "runtime_containment")
    policy_id = source.get("policy_id")
    policy_sha256 = source.get("policy_sha256")
    epoch_write_scope = source.get("epoch_write_scope")
    network_policy = source.get("network_policy")
    subprocess_required = source.get("provider_subprocess_required")
    if policy_id is not None and (not isinstance(policy_id, str) or not policy_id.strip()):
        _fail("runtime_containment.policy_id must be a non-empty string")
    _validate_optional_hex(
        policy_sha256, _SHA256_RE, "runtime_containment.policy_sha256"
    )
    if epoch_write_scope is not None and not isinstance(epoch_write_scope, str):
        _fail("runtime_containment.epoch_write_scope must be a string")
    if network_policy is not None and not isinstance(network_policy, str):
        _fail("runtime_containment.network_policy must be a string")
    if subprocess_required is not None and not isinstance(subprocess_required, bool):
        _fail("runtime_containment.provider_subprocess_required must be boolean")
    pinned = (
        policy_id is not None
        and policy_sha256 is not None
        and epoch_write_scope == "PROSPECTIVE_EPOCH_ONLY"
        and network_policy == "PROVIDER_ADAPTER_ONLY"
        and subprocess_required is True
    )
    return {
        "status": "PINNED" if pinned else "UNRESOLVED",
        "policy_id": policy_id,
        "policy_sha256": policy_sha256,
        "epoch_write_scope": epoch_write_scope,
        "network_policy": network_policy,
        "provider_subprocess_required": subprocess_required,
    }


def _runtime_containment_input(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _require_mapping(value, "runtime_containment")
    if set(source) != {
        "status",
        "policy_id",
        "policy_sha256",
        "epoch_write_scope",
        "network_policy",
        "provider_subprocess_required",
    }:
        _fail("runtime_containment has an invalid normalized shape")
    return {key: source[key] for key in source if key != "status"}


def _normalize_tool_identity(value: Any, label: str) -> dict[str, Any]:
    allowed = ("binary_path", "sha256", "version")
    if value is None:
        source: Mapping[str, Any] = {}
    else:
        source = _require_mapping(value, label)
        _reject_unknown_keys(source, allowed, label)
    binary_path = source.get("binary_path")
    sha256 = source.get("sha256")
    version = source.get("version")
    if binary_path is not None and (
        not isinstance(binary_path, str) or not os.path.isabs(binary_path)
    ):
        _fail(f"{label}.binary_path must be an absolute path")
    _validate_optional_hex(sha256, _SHA256_RE, f"{label}.sha256")
    if version is not None and (not isinstance(version, str) or not version.strip()):
        _fail(f"{label}.version must be a non-empty string")
    pinned = binary_path is not None and sha256 is not None and version is not None
    return {
        "status": "PINNED" if pinned else "UNRESOLVED",
        "binary_path": binary_path,
        "sha256": sha256,
        "version": version,
    }


def _tool_identity_input(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    source = _require_mapping(value, label)
    if set(source) != {"status", "binary_path", "sha256", "version"}:
        _fail(f"{label} has an invalid normalized shape")
    return {key: source[key] for key in ("binary_path", "sha256", "version")}


def _normalize_lock_semantics(value: Any) -> dict[str, Any]:
    tool_names = ("lockf", "lsof", "sandbox-exec")
    if value is None:
        source: Mapping[str, Any] = {}
    else:
        source = _require_mapping(value, "lock_tool_identities")
        _reject_unknown_keys(source, tool_names, "lock_tool_identities")
    tools = {
        name: _normalize_tool_identity(
            source.get(name), f"lock_tool_identities.{name}"
        )
        for name in tool_names
    }
    pinned = all(tool["status"] == "PINNED" for tool in tools.values())
    return {
        "status": "PINNED" if pinned else "UNRESOLVED",
        "exclusive_epoch_lock": True,
        "reservation_precedes_either_arm": True,
        "lock_order": ["epoch", "budget_ledger", "pair_reservation", "run"],
        "tools": tools,
    }


def _lock_tools_input(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _require_mapping(value, "lock_semantics")
    if set(source) != {
        "status",
        "exclusive_epoch_lock",
        "reservation_precedes_either_arm",
        "lock_order",
        "tools",
    }:
        _fail("lock_semantics has an invalid normalized shape")
    tools = _require_mapping(source["tools"], "lock_semantics.tools")
    if set(tools) != {"lockf", "lsof", "sandbox-exec"}:
        _fail("lock_semantics.tools must identify lockf/lsof/sandbox-exec")
    return {
        name: _tool_identity_input(
            _require_mapping(tools[name], f"lock_semantics.tools.{name}"),
            f"lock_semantics.tools.{name}",
        )
        for name in ("lockf", "lsof", "sandbox-exec")
    }


def _normalize_evidence_schema(value: Any) -> dict[str, Any]:
    allowed = ("schema", "sha256")
    if value is None:
        source: Mapping[str, Any] = {}
    else:
        source = _require_mapping(value, "evidence_schema")
        _reject_unknown_keys(source, allowed, "evidence_schema")
    schema = source.get("schema")
    sha256 = source.get("sha256")
    if schema is not None and (not isinstance(schema, str) or not schema.strip()):
        _fail("evidence_schema.schema must be a non-empty string")
    _validate_optional_hex(sha256, _SHA256_RE, "evidence_schema.sha256")
    pinned = schema is not None and sha256 is not None
    return {
        "status": "PINNED" if pinned else "UNRESOLVED",
        "schema": schema,
        "sha256": sha256,
        "run_countable_requires_affirmative_purity": True,
    }


def _evidence_schema_input(value: Mapping[str, Any]) -> dict[str, Any]:
    source = _require_mapping(value, "evidence_schema")
    if set(source) != {
        "status",
        "schema",
        "sha256",
        "run_countable_requires_affirmative_purity",
    }:
        _fail("evidence_schema has an invalid normalized shape")
    return {"schema": source["schema"], "sha256": source["sha256"]}


def _normalize_authorization_source(value: Any, label: str) -> dict[str, str]:
    source = _require_mapping(value, label)
    if set(source) != {"kind", "identifier", "sha256"}:
        _fail(f"{label} must contain kind, identifier, and sha256")
    kind = source["kind"]
    identifier = source["identifier"]
    sha256 = source["sha256"]
    if not isinstance(kind, str) or not kind.strip():
        _fail(f"{label}.kind must be a non-empty string")
    if not isinstance(identifier, str) or not identifier.strip():
        _fail(f"{label}.identifier must be a non-empty string")
    _validate_optional_hex(sha256, _SHA256_RE, f"{label}.sha256")
    if sha256 is None:
        _fail(f"{label}.sha256 is required")
    return {"kind": kind, "identifier": identifier, "sha256": sha256}


def _normalize_s04_attestation(value: Any, epoch_id: str) -> dict[str, Any]:
    if value is None:
        return {
            "schema": S04_ATTESTATION_SCHEMA,
            "status": "MISSING",
            "decision": None,
            "epoch_id": epoch_id,
            "run_id": S04_RUN_ID,
            "cost_state": S04_COST_STATE,
            "historical_charged_spend_usd": None,
            "lifetime_hard_cap_usd": LIFETIME_HARD_CAP_USD,
            "unknown_cost_treatment": None,
            "authorization_source_identity": None,
        }

    source = _require_mapping(value, "s04_budget_attestation")
    required = {
        "schema",
        "decision",
        "epoch_id",
        "run_id",
        "cost_state",
        "historical_charged_spend_usd",
        "lifetime_hard_cap_usd",
        "unknown_cost_treatment",
        "authorization_source_identity",
    }
    if set(source) != required:
        _fail("s04_budget_attestation has an invalid field set")
    if source["schema"] != S04_ATTESTATION_SCHEMA:
        _fail("s04_budget_attestation.schema is invalid")
    if source["decision"] != "AUTHORIZE_UNKNOWN_HISTORICAL_COST_HANDLING":
        _fail("s04_budget_attestation.decision is not authorizing")
    if source["epoch_id"] != epoch_id:
        _fail("s04_budget_attestation.epoch_id does not match")
    if source["run_id"] != S04_RUN_ID or source["cost_state"] != S04_COST_STATE:
        _fail("s04_budget_attestation does not bind the permanent-unknown s04 record")
    if source["lifetime_hard_cap_usd"] != LIFETIME_HARD_CAP_USD:
        _fail("s04_budget_attestation changes the lifetime hard cap")
    charged = _money(
        source["historical_charged_spend_usd"],
        "s04_budget_attestation.historical_charged_spend_usd",
    )
    if charged < Decimal(HISTORICAL_FORENSIC_SPEND_USD):
        _fail("s04 attestation cannot discard known historical spend")
    treatment = source["unknown_cost_treatment"]
    if not isinstance(treatment, str) or not treatment.strip():
        _fail("s04_budget_attestation.unknown_cost_treatment must be explicit")
    if treatment.strip().upper() in {
        "ZERO",
        "TREAT_AS_ZERO",
        "IGNORE",
        "IGNORED",
        "NO_CHARGE_WITHOUT_OWNER_DECISION",
    }:
        _fail("s04 UNKNOWN_PERMANENTLY may not be silently treated as zero")
    authorization_source = _normalize_authorization_source(
        source["authorization_source_identity"],
        "s04_budget_attestation.authorization_source_identity",
    )
    return {
        "schema": S04_ATTESTATION_SCHEMA,
        "status": "VALIDATED_EXTERNAL_OWNER_ATTESTATION",
        "decision": source["decision"],
        "epoch_id": epoch_id,
        "run_id": S04_RUN_ID,
        "cost_state": S04_COST_STATE,
        "historical_charged_spend_usd": source["historical_charged_spend_usd"],
        "lifetime_hard_cap_usd": LIFETIME_HARD_CAP_USD,
        "unknown_cost_treatment": treatment,
        "authorization_source_identity": authorization_source,
    }


def _s04_attestation_input(value: Mapping[str, Any]) -> dict[str, Any] | None:
    source = _require_mapping(value, "budget.s04_budget_attestation")
    if source.get("status") == "MISSING":
        return None
    return {key: source[key] for key in source if key != "status"}


def _build_budget(s04_budget_attestation: Any, epoch_id: str) -> dict[str, Any]:
    attestation = _normalize_s04_attestation(s04_budget_attestation, epoch_id)
    charged_text = attestation["historical_charged_spend_usd"]
    charged = Decimal(charged_text) if charged_text is not None else None
    hard_cap = Decimal(LIFETIME_HARD_CAP_USD)
    new_spend = Decimal(INITIAL_NEW_EPOCH_SPEND_USD)
    outstanding = Decimal(INITIAL_OUTSTANDING_RESERVATIONS_USD)
    pair_reservation = Decimal(PAIR_RESERVATION_USD)

    if charged is None:
        max_additional = None
        invariant_lhs = None
        cap_exceeded = None
        capacity = "UNRESOLVED_UNKNOWN_HISTORICAL_COST"
        authority_status = "BLOCKED_UNKNOWN_HISTORICAL_COST"
    else:
        remaining = hard_cap - charged
        cap_exceeded = remaining < 0
        max_additional = _format_money(max(remaining, Decimal("0")))
        lhs = charged + new_spend + outstanding + pair_reservation
        invariant_lhs = _format_money(lhs)
        capacity = "AVAILABLE" if lhs <= hard_cap else "EXCEEDED"
        authority_status = "AUTHORIZED_BY_EXPLICIT_OWNER_ATTESTATION"

    return {
        "currency": "USD",
        "decimal_places": 7,
        "cap_reset": False,
        "historical_forensic_spend_usd": HISTORICAL_FORENSIC_SPEND_USD,
        "historical_charged_spend_usd": charged_text,
        "lifetime_hard_cap_usd": LIFETIME_HARD_CAP_USD,
        "nominal_max_additional_usd": NOMINAL_MAX_ADDITIONAL_USD,
        "max_additional_spend_usd": max_additional,
        "new_epoch_spend_usd": INITIAL_NEW_EPOCH_SPEND_USD,
        "outstanding_reservations_usd": INITIAL_OUTSTANDING_RESERVATIONS_USD,
        "per_invocation_cap_usd": PER_INVOCATION_CAP_USD,
        "pair_reservation_usd": PAIR_RESERVATION_USD,
        "reservation_precedes_either_arm": True,
        "pair_reservation_invariant": (
            "historical_charged_spend_usd + new_epoch_spend_usd + "
            "outstanding_reservations_usd + pair_reservation_usd <= "
            "lifetime_hard_cap_usd"
        ),
        "pair_reservation_invariant_lhs_usd": invariant_lhs,
        "pair_reservation_capacity": capacity,
        "cap_exceeded": cap_exceeded,
        "unresolved_started_arm": {
            "classification": "EXECUTED_COST_UNRESOLVED",
            "conservative_debit_usd": PER_INVOCATION_CAP_USD,
            "applies_only_after_arm_started": True,
        },
        "not_run_budget_exhausted": {
            "classification": "NOT_RUN_BUDGET_EXHAUSTED",
            "executed": False,
            "debit_usd": "0.0000000",
        },
        "historical_unknown_cost": {
            "run_id": S04_RUN_ID,
            "cost_state": S04_COST_STATE,
            "treated_as_zero": False,
        },
        "s04_budget_attestation": attestation,
        "budget_authority_status": authority_status,
    }


def _initial_state_counts(value: Any) -> dict[str, Any]:
    count_keys = (
        "run_count",
        "workspace_count",
        "reservation_count",
        "aggregate_count",
    )
    if value is None:
        return {
            "status": "UNVERIFIED",
            **{key: None for key in count_keys},
            "historical_content_copied": False,
        }

    source = _require_mapping(value, "initial_state")
    list_keys = ("runs", "workspaces", "reservations", "aggregates")
    if set(source).issubset(set(count_keys)) and set(source) == set(count_keys):
        counts = {key: source[key] for key in count_keys}
    elif set(source) == set(list_keys):
        counts = {}
        for list_key, count_key in zip(list_keys, count_keys):
            entries = source[list_key]
            if not isinstance(entries, list):
                _fail(f"initial_state.{list_key} must be a list")
            counts[count_key] = len(entries)
    else:
        _fail("initial_state must provide exactly four state counts or four state lists")

    for key, count in counts.items():
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            _fail(f"initial_state.{key} must be a non-negative integer")
    empty = all(count == 0 for count in counts.values())
    return {
        "status": "VERIFIED_EMPTY" if empty else "NONEMPTY",
        **counts,
        "historical_content_copied": False,
    }


def _initial_state_input(value: Mapping[str, Any]) -> dict[str, Any] | None:
    source = _require_mapping(value, "lifecycle.initial_state")
    count_keys = (
        "run_count",
        "workspace_count",
        "reservation_count",
        "aggregate_count",
    )
    if set(source) != {"status", *count_keys, "historical_content_copied"}:
        _fail("lifecycle.initial_state has an invalid normalized shape")
    if source["status"] == "UNVERIFIED":
        return None
    return {key: source[key] for key in count_keys}


def _gate(passed: bool, pass_reason: str, fail_reason: str) -> dict[str, Any]:
    return {"passed": passed, "reason": pass_reason if passed else fail_reason}


def _derive_gates(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    budget = _require_mapping(manifest["budget"], "budget")
    lifecycle = _require_mapping(manifest["lifecycle"], "lifecycle")
    initial_state = _require_mapping(lifecycle["initial_state"], "lifecycle.initial_state")
    return {
        "final_runtime_identity": _gate(
            manifest["final_runtime_authority"]["status"] == "PINNED",
            "FINAL_RUNTIME_IDENTITY_PINNED",
            "MISSING_FINAL_RUNTIME_IDENTITY",
        ),
        "provider_adapter_identity": _gate(
            manifest["provider_adapter"]["status"] == "PINNED",
            "PROVIDER_ADAPTER_IDENTITY_PINNED",
            "MISSING_PROVIDER_ADAPTER_IDENTITY",
        ),
        "provider_runtime_identity": _gate(
            manifest["provider_runtime"]["status"] == "PINNED",
            "PROVIDER_RUNTIME_IDENTITY_PINNED",
            "MISSING_PROVIDER_RUNTIME_IDENTITY",
        ),
        "lock_tool_identities": _gate(
            manifest["lock_semantics"]["status"] == "PINNED",
            "LOCK_TOOL_IDENTITIES_PINNED",
            "MISSING_LOCKF_LSOF_SANDBOX_EXEC_IDENTITIES",
        ),
        "task_prompt_fixture_oracle_hashes": _gate(
            all(task["status"] == "PINNED" for task in manifest["tasks"]),
            "ALL_TASK_ARTIFACT_HASHES_PINNED",
            "MISSING_TASK_PROMPT_FIXTURE_ORACLE_HASHES",
        ),
        "runtime_containment_policy": _gate(
            manifest["runtime_containment"]["status"] == "PINNED",
            "RUNTIME_CONTAINMENT_POLICY_PINNED",
            "MISSING_RUNTIME_CONTAINMENT_POLICY",
        ),
        "evidence_schema": _gate(
            manifest["evidence_schema"]["status"] == "PINNED",
            "EVIDENCE_SCHEMA_PINNED",
            "MISSING_EVIDENCE_SCHEMA",
        ),
        "lifetime_budget_authority": _gate(
            budget["budget_authority_status"]
            == "AUTHORIZED_BY_EXPLICIT_OWNER_ATTESTATION",
            "LIFETIME_BUDGET_AUTHORITY_RESOLVED",
            "BLOCKED_UNKNOWN_HISTORICAL_COST",
        ),
        "s04_budget_attestation": _gate(
            budget["s04_budget_attestation"]["status"]
            == "VALIDATED_EXTERNAL_OWNER_ATTESTATION",
            "S04_BUDGET_ATTESTATION_VALIDATED",
            "MISSING_S04_BUDGET_ATTESTATION",
        ),
        "empty_prospective_epoch_state": _gate(
            initial_state["status"] == "VERIFIED_EMPTY",
            "PROSPECTIVE_EPOCH_STATE_VERIFIED_EMPTY",
            "PROSPECTIVE_EPOCH_STATE_NOT_VERIFIED_EMPTY",
        ),
        "pair_reservation_capacity": _gate(
            budget["pair_reservation_capacity"] == "AVAILABLE",
            "PAIR_RESERVATION_WITHIN_LIFETIME_CAP",
            "PAIR_RESERVATION_NOT_PROVEN_WITHIN_LIFETIME_CAP",
        ),
    }


def _publication_gate(gates: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    blockers = [gates[name]["reason"] for name in GATE_ORDER if not gates[name]["passed"]]
    return {
        "target_status": "SEALED_REQUIRES_EXTERNAL_OWNER_SIGNOFF",
        "gate_order": list(GATE_ORDER),
        "gates": copy.deepcopy(dict(gates)),
        "blockers": blockers,
        "status": "PASS" if not blockers else "BLOCKED",
    }


def _owner_signoff_gate(signable: bool) -> dict[str, Any]:
    return {
        "schema": OWNER_SIGNOFF_SCHEMA,
        "decision_required": "AUTHORIZE",
        "sidecar_filename": "owner-signoff.json",
        "sidecar_in_manifest_hash": False,
        "required_fields": list(OWNER_SIGNOFF_REQUIRED_FIELDS),
        "status": (
            "REQUIRED_EXTERNAL_SIDECAR"
            if signable
            else "INELIGIBLE_UNTIL_PUBLICATION_GATES_PASS"
        ),
    }


def build_manifest(
    epoch_id: str,
    *,
    final_runtime_authority: Any = None,
    provider_adapter: Any = None,
    provider_runtime: Any = None,
    task_pins: Any = None,
    runtime_containment: Any = None,
    lock_tool_identities: Any = None,
    evidence_schema: Any = None,
    s04_budget_attestation: Any = None,
    initial_state: Any = None,
) -> dict[str, Any]:
    """Build a deterministic prospective manifest from explicit inputs.

    Missing signability inputs produce ``DRAFT_UNSIGNABLE`` rather than being
    guessed.  A supplied but malformed identity or attestation is rejected.
    """

    validate_epoch_id(epoch_id)
    tasks, fixture_oracle_index = _normalize_task_pins(task_pins)
    epoch_root = f"{AUTHORITY_ROOT}/epochs/{epoch_id}"
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "authority_lineage": {
            "authority_root": AUTHORITY_ROOT,
            "materialization": "FUTURE_EXTERNAL_ACTION",
            "epoch_lineage": "PROSPECTIVE_NEW_EPOCH",
            "lifetime_cap_carries_across_epochs": True,
            "cap_reset": False,
        },
        "epoch_id": epoch_id,
        "epoch_namespace": {
            "authority_root": AUTHORITY_ROOT,
            "epoch_root": epoch_root,
            "run_root": f"{epoch_root}/runs",
            "workspace_root": f"{epoch_root}/workspaces",
            "reservation_root": f"{epoch_root}/reservations",
            "aggregate_root": f"{epoch_root}/aggregates",
            "materialized_by_compiler": False,
        },
        "manifest_hash_scope": {
            "algorithm": "SHA-256",
            "bytes": "EXACT_UTF8_MANIFEST_JSON_WITH_FINAL_LF",
            "self_hash_field": "FORBIDDEN",
            "owner_signoff_sidecar": "EXCLUDED",
        },
        "canonical_fable": copy.deepcopy(CANONICAL_FABLE),
        "canonical_harness": copy.deepcopy(CANONICAL_HARNESS),
        "final_runtime_authority": _normalize_final_runtime(final_runtime_authority),
        "provider_adapter": _normalize_provider_adapter(provider_adapter),
        "provider_runtime": _normalize_provider_runtime(provider_runtime),
        "fixture_oracle_index": fixture_oracle_index,
        "tasks": tasks,
        "schedule": {
            "pairs": _schedule_records(),
            "stratum_c": "NOT_EVALUATED",
        },
        "budget": _build_budget(s04_budget_attestation, epoch_id),
        "runtime_containment": _normalize_runtime_containment(runtime_containment),
        "lock_semantics": _normalize_lock_semantics(lock_tool_identities),
        "evidence_schema": _normalize_evidence_schema(evidence_schema),
        "historical_quarantine": {
            "R1": {
                "classification": "FORENSIC_ONLY",
                "countable": False,
                "mutation": "FORBIDDEN",
                "analysis_pool": "EXCLUDED",
                "content_copied": False,
            },
            "R2": {
                "classification": "UNSIGNED_PREPARATION_ONLY",
                "countable": False,
                "resume": "FORBIDDEN",
                "content_copied": False,
            },
        },
        "claim_boundary": {
            "prospective_execution_claim": "NOT_YET_EXECUTED",
            "historical_runs_countable": False,
            "historical_spend_retained_in_lifetime_ledger": True,
            "stratum_c": "NOT_EVALUATED",
        },
        "lifecycle": {
            "phase": "PROSPECTIVE",
            "historical_resume": "FORBIDDEN",
            "initial_state": _initial_state_counts(initial_state),
            "run_classification_semantics": {
                "NOT_RUN_BUDGET_EXHAUSTED": {"executed": False},
                "EXECUTED_COST_UNRESOLVED": {
                    "executed": True,
                    "conservative_debit_usd": PER_INVOCATION_CAP_USD,
                },
            },
        },
        "forbidden_actions": [
            "CREATE_REAL_AUTHORITY_ROOT",
            "CREATE_REAL_EPOCH_ROOT",
            "SIGN_MANIFEST_IN_COMPILER",
            "INVOKE_PROVIDER_IN_COMPILER",
            "COPY_HISTORICAL_RUN_CONTENT",
            "RESUME_HISTORICAL_R2",
            "RESET_LIFETIME_CAP",
        ],
        "publication_gate": {},
        "owner_signoff_gate": {},
        "status": "DRAFT_UNSIGNABLE",
    }
    gates = _derive_gates(manifest)
    manifest["publication_gate"] = _publication_gate(gates)
    signable = manifest["publication_gate"]["status"] == "PASS"
    manifest["owner_signoff_gate"] = _owner_signoff_gate(signable)
    manifest["status"] = (
        "SEALED_REQUIRES_EXTERNAL_OWNER_SIGNOFF" if signable else "DRAFT_UNSIGNABLE"
    )
    validate_manifest(manifest)
    return manifest


compile_manifest = build_manifest


def validate_manifest(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate schema, fixed decisions, arithmetic, and derived gates."""

    source = _require_mapping(manifest, "manifest")
    _reject_non_json_contract_types(source)
    if set(source) != _TOP_LEVEL_KEYS:
        missing = sorted(_TOP_LEVEL_KEYS - set(source))
        extra = sorted(set(source) - _TOP_LEVEL_KEYS)
        _fail(f"manifest top-level mismatch; missing={missing}, extra={extra}")
    if "manifest_sha256" in source:
        _fail("manifest must not contain a self-hash field")
    if source["schema"] != MANIFEST_SCHEMA:
        _fail("manifest schema is invalid")
    epoch_id = validate_epoch_id(source["epoch_id"])

    authority_lineage = {
        "authority_root": AUTHORITY_ROOT,
        "materialization": "FUTURE_EXTERNAL_ACTION",
        "epoch_lineage": "PROSPECTIVE_NEW_EPOCH",
        "lifetime_cap_carries_across_epochs": True,
        "cap_reset": False,
    }
    if source["authority_lineage"] != authority_lineage:
        _fail("authority_lineage differs from the fixed prospective lineage")
    epoch_root = f"{AUTHORITY_ROOT}/epochs/{epoch_id}"
    expected_namespace = {
        "authority_root": AUTHORITY_ROOT,
        "epoch_root": epoch_root,
        "run_root": f"{epoch_root}/runs",
        "workspace_root": f"{epoch_root}/workspaces",
        "reservation_root": f"{epoch_root}/reservations",
        "aggregate_root": f"{epoch_root}/aggregates",
        "materialized_by_compiler": False,
    }
    if source["epoch_namespace"] != expected_namespace:
        _fail("epoch_namespace escapes or changes the prospective namespace")
    expected_hash_scope = {
        "algorithm": "SHA-256",
        "bytes": "EXACT_UTF8_MANIFEST_JSON_WITH_FINAL_LF",
        "self_hash_field": "FORBIDDEN",
        "owner_signoff_sidecar": "EXCLUDED",
    }
    if source["manifest_hash_scope"] != expected_hash_scope:
        _fail("manifest_hash_scope is not the fixed exact-byte contract")
    if source["canonical_fable"] != CANONICAL_FABLE:
        _fail("canonical_fable pins differ from the CTO-fixed identities")
    if source["canonical_harness"] != CANONICAL_HARNESS:
        _fail("canonical_harness pins differ from the base harness identities")

    final_input = _final_runtime_input(
        _require_mapping(source["final_runtime_authority"], "final_runtime_authority")
    )
    if source["final_runtime_authority"] != _normalize_final_runtime(final_input):
        _fail("final_runtime_authority status is inconsistent with its identities")
    adapter_input = _provider_adapter_input(
        _require_mapping(source["provider_adapter"], "provider_adapter")
    )
    if source["provider_adapter"] != _normalize_provider_adapter(adapter_input):
        _fail("provider_adapter status or no-invocation contract is inconsistent")
    provider_input = _provider_runtime_input(
        _require_mapping(source["provider_runtime"], "provider_runtime")
    )
    if source["provider_runtime"] != _normalize_provider_runtime(provider_input):
        _fail("provider_runtime status or identity is inconsistent")

    task_input = _task_pins_input(source["tasks"])
    expected_tasks, expected_index = _normalize_task_pins(task_input)
    if source["tasks"] != expected_tasks:
        _fail("task pins or task-to-pair mapping are inconsistent")
    if source["fixture_oracle_index"] != expected_index:
        _fail("fixture_oracle_index does not exactly mirror task pins")
    expected_schedule = {"pairs": _schedule_records(), "stratum_c": "NOT_EVALUATED"}
    if source["schedule"] != expected_schedule:
        _fail("schedule differs from the eight CTO-fixed pair orders")

    budget = _require_mapping(source["budget"], "budget")
    attestation_input = _s04_attestation_input(
        _require_mapping(budget.get("s04_budget_attestation"), "budget.s04_budget_attestation")
    )
    if budget != _build_budget(attestation_input, epoch_id):
        _fail("budget ledger, decimal arithmetic, or s04 authority is inconsistent")

    containment_input = _runtime_containment_input(
        _require_mapping(source["runtime_containment"], "runtime_containment")
    )
    if source["runtime_containment"] != _normalize_runtime_containment(containment_input):
        _fail("runtime_containment status is inconsistent with its policy")
    lock_input = _lock_tools_input(
        _require_mapping(source["lock_semantics"], "lock_semantics")
    )
    if source["lock_semantics"] != _normalize_lock_semantics(lock_input):
        _fail("lock_semantics status or fixed lock ordering is inconsistent")
    evidence_input = _evidence_schema_input(
        _require_mapping(source["evidence_schema"], "evidence_schema")
    )
    if source["evidence_schema"] != _normalize_evidence_schema(evidence_input):
        _fail("evidence_schema status or countability rule is inconsistent")

    expected_quarantine = {
        "R1": {
            "classification": "FORENSIC_ONLY",
            "countable": False,
            "mutation": "FORBIDDEN",
            "analysis_pool": "EXCLUDED",
            "content_copied": False,
        },
        "R2": {
            "classification": "UNSIGNED_PREPARATION_ONLY",
            "countable": False,
            "resume": "FORBIDDEN",
            "content_copied": False,
        },
    }
    if source["historical_quarantine"] != expected_quarantine:
        _fail("historical quarantine differs from the fixed R1/R2 policy")
    expected_claim_boundary = {
        "prospective_execution_claim": "NOT_YET_EXECUTED",
        "historical_runs_countable": False,
        "historical_spend_retained_in_lifetime_ledger": True,
        "stratum_c": "NOT_EVALUATED",
    }
    if source["claim_boundary"] != expected_claim_boundary:
        _fail("claim_boundary is inconsistent with prospective-only evaluation")

    lifecycle = _require_mapping(source["lifecycle"], "lifecycle")
    if set(lifecycle) != {
        "phase",
        "historical_resume",
        "initial_state",
        "run_classification_semantics",
    }:
        _fail("lifecycle has an invalid field set")
    initial_input = _initial_state_input(
        _require_mapping(lifecycle["initial_state"], "lifecycle.initial_state")
    )
    expected_lifecycle = {
        "phase": "PROSPECTIVE",
        "historical_resume": "FORBIDDEN",
        "initial_state": _initial_state_counts(initial_input),
        "run_classification_semantics": {
            "NOT_RUN_BUDGET_EXHAUSTED": {"executed": False},
            "EXECUTED_COST_UNRESOLVED": {
                "executed": True,
                "conservative_debit_usd": PER_INVOCATION_CAP_USD,
            },
        },
    }
    if lifecycle != expected_lifecycle:
        _fail("lifecycle or run execution classification is inconsistent")

    expected_forbidden = [
        "CREATE_REAL_AUTHORITY_ROOT",
        "CREATE_REAL_EPOCH_ROOT",
        "SIGN_MANIFEST_IN_COMPILER",
        "INVOKE_PROVIDER_IN_COMPILER",
        "COPY_HISTORICAL_RUN_CONTENT",
        "RESUME_HISTORICAL_R2",
        "RESET_LIFETIME_CAP",
    ]
    if source["forbidden_actions"] != expected_forbidden:
        _fail("forbidden_actions differs from the compiler boundary")

    gates = _derive_gates(source)
    expected_publication = _publication_gate(gates)
    if source["publication_gate"] != expected_publication:
        _fail("publication_gate does not truthfully reflect signability inputs")
    signable = expected_publication["status"] == "PASS"
    if source["owner_signoff_gate"] != _owner_signoff_gate(signable):
        _fail("owner_signoff_gate is inconsistent with publication gates")
    expected_status = (
        "SEALED_REQUIRES_EXTERNAL_OWNER_SIGNOFF" if signable else "DRAFT_UNSIGNABLE"
    )
    if source["status"] != expected_status:
        _fail("manifest status overstates or understates signability")
    return source


def serialize_manifest(manifest: Mapping[str, Any]) -> bytes:
    """Return canonical UTF-8 JSON bytes with exactly one final LF."""

    validate_manifest(manifest)
    encoded = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return encoded + b"\n"


canonical_manifest_bytes = serialize_manifest


def _reject_json_constant(value: str) -> None:
    _fail(f"JSON constant {value} is not permitted")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _load_json_bytes(data: bytes, label: str) -> Any:
    if not isinstance(data, bytes):
        _fail(f"{label} must be bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestValidationError(f"{label} is not UTF-8: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"{label} is not valid JSON: {exc}") from exc


def validate_manifest_bytes(data: bytes) -> Mapping[str, Any]:
    """Validate both the manifest model and its canonical exact-byte form."""

    if not data.endswith(b"\n"):
        _fail("manifest bytes must include the final LF")
    parsed = _load_json_bytes(data, "manifest")
    manifest = _require_mapping(parsed, "manifest")
    validate_manifest(manifest)
    canonical = serialize_manifest(manifest)
    if data != canonical:
        _fail("manifest bytes are not deterministic canonical serialization")
    return manifest


def manifest_sha256(manifest_or_bytes: Mapping[str, Any] | bytes) -> str:
    """Hash exact manifest bytes; mappings are canonicalized first."""

    if isinstance(manifest_or_bytes, bytes):
        data = manifest_or_bytes
    else:
        data = serialize_manifest(manifest_or_bytes)
    return hashlib.sha256(data).hexdigest()


def _path_is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _assert_safe_output_path(path: Path) -> None:
    resolved = path.expanduser().resolve(strict=False)
    for forbidden in FORBIDDEN_OUTPUT_ROOTS:
        if _path_is_within(resolved, Path(forbidden)):
            _fail(f"compiler output under real authority/history root is forbidden: {resolved}")


def write_manifest(path: str | os.PathLike[str], manifest: Mapping[str, Any]) -> str:
    """Write a new manifest file without creating parents or overwriting data."""

    destination = Path(path)
    _assert_safe_output_path(destination)
    if not destination.parent.is_dir():
        _fail("manifest output parent must already exist")
    data = serialize_manifest(manifest)
    try:
        with destination.open("xb") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise ManifestValidationError("manifest output already exists") from exc
    return manifest_sha256(data)


def read_manifest(path: str | os.PathLike[str]) -> tuple[Mapping[str, Any], bytes]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ManifestValidationError(f"manifest cannot be read: {exc}") from exc
    return validate_manifest_bytes(data), data


def validate_owner_signoff(
    manifest_or_bytes: Mapping[str, Any] | bytes,
    owner_signoff: Mapping[str, Any],
) -> bool:
    """Validate an external sidecar against the exact immutable manifest bytes."""

    if isinstance(manifest_or_bytes, bytes):
        manifest = validate_manifest_bytes(manifest_or_bytes)
        manifest_bytes = manifest_or_bytes
    else:
        manifest = validate_manifest(manifest_or_bytes)
        manifest_bytes = serialize_manifest(manifest)
    if manifest["status"] != "SEALED_REQUIRES_EXTERNAL_OWNER_SIGNOFF":
        raise OwnerSignoffValidationError("draft/unsignable manifest cannot accept signoff")

    signoff = _require_mapping(owner_signoff, "owner_signoff")
    _reject_non_json_contract_types(signoff, "owner_signoff")
    missing = [field for field in OWNER_SIGNOFF_REQUIRED_FIELDS if field not in signoff]
    if missing:
        raise OwnerSignoffValidationError(
            f"owner_signoff missing required fields: {', '.join(missing)}"
        )
    if signoff["schema"] != OWNER_SIGNOFF_SCHEMA:
        raise OwnerSignoffValidationError("owner_signoff.schema is invalid")
    if signoff["decision"] != "AUTHORIZE":
        raise OwnerSignoffValidationError("owner_signoff.decision must be AUTHORIZE")
    if signoff["epoch_id"] != manifest["epoch_id"]:
        raise OwnerSignoffValidationError("owner_signoff epoch does not match manifest")
    actual_hash = manifest_sha256(manifest_bytes)
    if signoff["manifest_sha256"] != actual_hash:
        raise OwnerSignoffValidationError("owner_signoff hash does not match exact manifest bytes")

    budget = manifest["budget"]
    expected_amounts = {
        "lifetime_hard_cap_usd": budget["lifetime_hard_cap_usd"],
        "historical_spend_usd": budget["historical_charged_spend_usd"],
        "max_additional_spend_usd": budget["max_additional_spend_usd"],
    }
    for field, expected in expected_amounts.items():
        try:
            _money(signoff[field], f"owner_signoff.{field}")
        except ManifestValidationError as exc:
            raise OwnerSignoffValidationError(str(exc)) from exc
        if signoff[field] != expected:
            raise OwnerSignoffValidationError(
                f"owner_signoff.{field} does not match the lifetime ledger"
            )
    try:
        _normalize_authorization_source(
            signoff["authorization_source_identity"],
            "owner_signoff.authorization_source_identity",
        )
    except ManifestValidationError as exc:
        raise OwnerSignoffValidationError(str(exc)) from exc
    return True


validate_signoff = validate_owner_signoff


def read_owner_signoff(path: str | os.PathLike[str]) -> Mapping[str, Any]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise OwnerSignoffValidationError(f"owner signoff cannot be read: {exc}") from exc
    parsed = _load_json_bytes(data, "owner_signoff")
    return _require_mapping(parsed, "owner_signoff")


def _load_build_inputs(path: str | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ManifestValidationError(f"build inputs cannot be read: {exc}") from exc
    parsed = _load_json_bytes(data, "build_inputs")
    return dict(_require_mapping(parsed, "build_inputs"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="compile a new prospective manifest")
    build.add_argument("--epoch-id", required=True)
    build.add_argument("--inputs", help="JSON object containing optional external pins")
    build.add_argument("--output", required=True)

    validate = subparsers.add_parser("validate", help="validate canonical manifest bytes")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--owner-signoff")

    digest = subparsers.add_parser("hash", help="validate and hash exact manifest bytes")
    digest.add_argument("--manifest", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "build":
            inputs = _load_build_inputs(arguments.inputs)
            manifest = build_manifest(arguments.epoch_id, **inputs)
            digest = write_manifest(arguments.output, manifest)
            result = {
                "epoch_id": manifest["epoch_id"],
                "manifest_sha256": digest,
                "status": manifest["status"],
            }
        elif arguments.command == "validate":
            manifest, data = read_manifest(arguments.manifest)
            signoff_status = "NOT_PROVIDED"
            if arguments.owner_signoff:
                validate_owner_signoff(data, read_owner_signoff(arguments.owner_signoff))
                signoff_status = "VALID"
            result = {
                "epoch_id": manifest["epoch_id"],
                "manifest_sha256": manifest_sha256(data),
                "status": manifest["status"],
                "owner_signoff": signoff_status,
            }
        else:
            manifest, data = read_manifest(arguments.manifest)
            result = {
                "epoch_id": manifest["epoch_id"],
                "manifest_sha256": manifest_sha256(data),
            }
    except (ManifestValidationError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "AUTHORITY_ROOT",
    "CANONICAL_FABLE",
    "CANONICAL_HARNESS",
    "CTO_PROSPECTIVE_EPOCH_ID",
    "GATE_ORDER",
    "HISTORICAL_FORENSIC_SPEND_USD",
    "LIFETIME_HARD_CAP_USD",
    "MANIFEST_SCHEMA",
    "ManifestValidationError",
    "NOMINAL_MAX_ADDITIONAL_USD",
    "OWNER_SIGNOFF_SCHEMA",
    "OwnerSignoffValidationError",
    "PAIR_RESERVATION_USD",
    "PER_INVOCATION_CAP_USD",
    "S04_ATTESTATION_SCHEMA",
    "S04_COST_STATE",
    "S04_RUN_ID",
    "TASK_IDS",
    "build_manifest",
    "canonical_manifest_bytes",
    "compile_manifest",
    "manifest_sha256",
    "read_manifest",
    "read_owner_signoff",
    "serialize_manifest",
    "validate_epoch_id",
    "validate_manifest",
    "validate_manifest_bytes",
    "validate_owner_signoff",
    "validate_signoff",
    "write_manifest",
]


if __name__ == "__main__":
    raise SystemExit(main())
