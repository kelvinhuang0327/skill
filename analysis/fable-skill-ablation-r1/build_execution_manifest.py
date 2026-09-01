#!/usr/bin/env python3
"""Derive the one runner-consumable P01 execution manifest.

The sealed prospective-epoch manifest is authority, but it deliberately omits
the literal experimental payload consumed by :mod:`runner`.  This module
bridges that boundary without importing any historical run result or path.
Only the independently hash-pinned A01 prompt, fixture, oracle, and the
condition-neutral historical execution semantics are retained here.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence


def _load_sibling(module_name: str, filename: str, *aliases: str) -> Any:
    for name in (module_name, *aliases):
        module = sys.modules.get(name)
        if module is not None:
            return module
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"required execution-manifest sibling is missing: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


compiler = _load_sibling(
    "fable_epoch_manifest", "build_epoch_manifest.py", "build_epoch_manifest"
)
runner = _load_sibling("fable_ablation_runner", "runner.py", "runner")
claude = _load_sibling(
    "fable_ablation_claude_executor", "claude_executor.py", "claude_executor"
)


EXECUTION_MANIFEST_SCHEMA = "FABLE_ABLATION_PAIR_EXECUTION_MANIFEST/v1"
PAIR_ID = "P01"
TASK_ID = "A01"
ARM_ORDER = ("OFF", "ON")
EXPECTED_EPOCH_ID = compiler.CTO_PROSPECTIVE_EPOCH_ID
EXPECTED_LEDGER_CAP_USD = "7.7339242"
EXPECTED_INVOCATION_CAP_USD = "2.0000000"


class ExecutionManifestError(ValueError):
    """The authority or recovered payload cannot prove the P01 contract."""


_A01_PROMPT = (
    "This repository contains a small sequence utility in `seqtools.py`.\n\n"
    "`chunk(seq, size)` is supposed to split a sequence into consecutive chunks "
    "of length `size`, where the final chunk may be shorter than `size` if the "
    "sequence does not divide evenly. The full behaviour is specified in "
    "`README.md`.\n\nIt currently does not behave as specified. Fix `seqtools.py` "
    "so that `chunk` matches the specification in `README.md` exactly.\n\n"
    "Do not change `README.md`."
)

_A01_FIXTURE_BASE64 = """
H4sIAAAAAAAC/+2WPW/bMBCGPfNXHJzFbhRVjh0HCJoCRZuhQ5cG6GIYJiOfJLYypZJUW/fX90jFifPRMcmQewZL
5n2QPInvKS2116VpLA6ejIyYz2bxSty/Ztnx9PY+jp/OT7MBZINnoHNeWYDBK2W1are5yitcrd6KNyn9EQPm9fD1
4sOnLxfpZj14sfM/ySaz++d/Mufz/ywcgMOfvmlqJ8TBAeRVZ36MaCgBp//iWIjLttbegaQxCdr4BhTU2nloCsgb
4zDvvP6FfaRLAElM9lxqNKWvKJyyyUTgnxxbD75CKLRRdR8GvytNUZTOK21cNFvc0L02JWiPGwfKrGGjtnCF4KrG
erRpWBzmutC58roxZ0Icgex3sJgkx8k0mSUnyXyZwPFYwtF7kIswvkwWZKHfYFvKx4L+H/JIADmf7DvTyB0nsk93
9mj4GEsFyiKo+rfaOmhr2ivIUDQJzdV3zD2V0mKp7LpG50IlQ1G0aTsfnliHJkfw2xbTMFOsLmzoXQ71UdA2Tsen
Qg8MSyoVfC56L3h3Dpmk3Eo7BPlN1R1eWNtYmbLwv0J2p586/4t9/53M53f1nzrC9JT1/zkYDoeXG1XXt6JSYd2i
dSlZhBBrLB40hTMBRIgMvQFk7A3XzeFhS9jvAr1QkdTEDJeIcPP9AUVj+77QhdXsC3u6my9edQE3QtavJNDr2a2c
jYbRaSeJO0EcjmNEQyp6Dotl/BMmDi+Bpx1QHlPiKEvCksOWx3DUT3cIkzvbv06TqrZFsx4F6Q7+i5jprM93GAOW
435Si76zJgSx0jIMwzAMwzAMwzAMwzAMwzAMwzBPxD9Llxd/ACgAAA==
"""

_A01_ORACLE_BASE64 = """
IyEvdXNyL2Jpbi9lbnYgcHl0aG9uMwoiIiJIZWxkLW91dCBjb3JyZWN0bmVzcyBvcmFjbGUgZm9yIEEwMS4KClVz
YWdlOiBweXRob24zIEEwMS5weSA8cnVuLXdvcmstZGlyZWN0b3J5PgpFeGl0IGNvZGUgMCBtZWFucyBQQVNTLiBB
bnkgb3RoZXIgZXhpdCBjb2RlIG1lYW5zIEZBSUwuCgpUaGlzIG9yYWNsZSBuZXZlciB3cml0ZXMgaW50byB0aGUg
ZGlyZWN0b3J5IGl0IGlzIGdpdmVuLgoiIiIKCmltcG9ydCBoYXNobGliCmltcG9ydCBvcwppbXBvcnQgc2h1dGls
CmltcG9ydCBzdWJwcm9jZXNzCmltcG9ydCBzeXMKaW1wb3J0IHRlbXBmaWxlCgpUQVNLX0lEID0gJ0EwMScKUFJP
VEVDVEVEID0geydSRUFETUUubWQnOiAnNWQxNjdmZjY2ODI1NDRjYjUxYjQ4OWY3NGYyYjA1NmI0OGUwNDRkMWFj
MGEwZTk3MDJhMTA3MTYzNGQ4OWRmMid9ClJVTl9TVUlURSA9IEZhbHNlCkNIRUNLUyA9ICdcbmltcG9ydCBzZXF0
b29sc1xuYXNzZXJ0IHNlcXRvb2xzLmNodW5rKFsxLCAyLCAzLCA0LCA1LCA2XSwgMikgPT0gW1sxLCAyXSwgWzMs
IDRdLCBbNSwgNl1dXG5hc3NlcnQgc2VxdG9vbHMuY2h1bmsoWzEsIDIsIDMsIDQsIDVdLCAyKSA9PSBbWzEsIDJd
LCBbMywgNF0sIFs1XV1cbmFzc2VydCBzZXF0b29scy5jaHVuayhbMSwgMiwgM10sIDUpID09IFtbMSwgMiwgM11d
XG5hc3NlcnQgc2VxdG9vbHMuY2h1bmsoW10sIDMpID09IFtdXG5hc3NlcnQgc2VxdG9vbHMuY2h1bmsoWzFdLCAx
KSA9PSBbWzFdXVxuYXNzZXJ0IHNlcXRvb2xzLmNodW5rKCJhYmNkZSIsIDIpID09IFtbImEiLCAiYiJdLCBbImMi
LCAiZCJdLCBbImUiXV1cbmFzc2VydCBzZXF0b29scy5jaHVuaygoMSwgMiwgMywgNCksIDMpID09IFtbMSwgMiwg
M10sIFs0XV1cbmFzc2VydCBhbGwoaXNpbnN0YW5jZShjLCBsaXN0KSBmb3IgYyBpbiBzZXF0b29scy5jaHVuaygi
YWJjIiwgMikpXG5mb3IgYmFkIGluICgwLCAtMSk6XG4gICAgdHJ5OlxuICAgICAgICBzZXF0b29scy5jaHVuayhb
MSwgMl0sIGJhZClcbiAgICBleGNlcHQgVmFsdWVFcnJvcjpcbiAgICAgICAgcGFzc1xuICAgIGVsc2U6XG4gICAg
ICAgIHJhaXNlIEFzc2VydGlvbkVycm9yKCJzaXplPSVyIG11c3QgcmFpc2UgVmFsdWVFcnJvciIgJSAoYmFkLCkp
XG4nCgoKZGVmIGZhaWwobWVzc2FnZSk6CiAgICBzeXMuc3RkZXJyLndyaXRlKCJGQUlMWyVzXTogJXNcbiIgJSAo
VEFTS19JRCwgbWVzc2FnZSkpCiAgICByZXR1cm4gMQoKCmRlZiBzaGEyNTZfZmlsZShwYXRoKToKICAgIHdpdGgg
b3BlbihwYXRoLCAicmIiKSBhcyBoYW5kbGU6CiAgICAgICAgcmV0dXJuIGhhc2hsaWIuc2hhMjU2KGhhbmRsZS5y
ZWFkKCkpLmhleGRpZ2VzdCgpCgoKZGVmIG1haW4oKToKICAgIGlmIGxlbihzeXMuYXJndikgIT0gMjoKICAgICAg
ICByZXR1cm4gZmFpbCgiZXhwZWN0ZWQgZXhhY3RseSBvbmUgYXJndW1lbnQ6IHRoZSBydW4gd29yayBkaXJlY3Rv
cnkiKQogICAgc3JjID0gb3MucGF0aC5hYnNwYXRoKHN5cy5hcmd2WzFdKQogICAgaWYgbm90IG9zLnBhdGguaXNk
aXIoc3JjKToKICAgICAgICByZXR1cm4gZmFpbCgibm90IGEgZGlyZWN0b3J5OiAlcyIgJSAoc3JjLCkpCgogICAg
Zm9yIHJlbCBpbiBzb3J0ZWQoUFJPVEVDVEVEKToKICAgICAgICBwYXRoID0gb3MucGF0aC5qb2luKHNyYywgcmVs
KQogICAgICAgIGlmIG5vdCBvcy5wYXRoLmlzZmlsZShwYXRoKToKICAgICAgICAgICAgcmV0dXJuIGZhaWwoInBy
b3RlY3RlZCBmaWxlIG1pc3Npbmcgb3Igbm90IGEgZmlsZTogJXMiICUgKHJlbCwpKQogICAgICAgIGlmIHNoYTI1
Nl9maWxlKHBhdGgpICE9IFBST1RFQ1RFRFtyZWxdOgogICAgICAgICAgICByZXR1cm4gZmFpbCgicHJvdGVjdGVk
IGZpbGUgd2FzIG1vZGlmaWVkOiAlcyIgJSAocmVsLCkpCgogICAgdG1wID0gdGVtcGZpbGUubWtkdGVtcChwcmVm
aXg9Im9yYWNsZS0lcy0iICUgKFRBU0tfSUQsKSkKICAgIHRyeToKICAgICAgICB3b3JrID0gb3MucGF0aC5qb2lu
KHRtcCwgIndvcmsiKQogICAgICAgIHNodXRpbC5jb3B5dHJlZShzcmMsIHdvcmssIHN5bWxpbmtzPVRydWUpCiAg
ICAgICAgZW52ID0gZGljdChvcy5lbnZpcm9uKQogICAgICAgIGVudlsiUFlUSE9ORE9OVFdSSVRFQllURUNPREUi
XSA9ICIxIgogICAgICAgIGVudi5wb3AoIlBZVEhPTlBBVEgiLCBOb25lKQoKICAgICAgICBpZiBSVU5fU1VJVEU6
CiAgICAgICAgICAgIHJlc3VsdCA9IHN1YnByb2Nlc3MucnVuKAogICAgICAgICAgICAgICAgW3N5cy5leGVjdXRh
YmxlLCAiLW0iLCAidW5pdHRlc3QiLCAiZGlzY292ZXIiLCAiLXMiLCAidGVzdHMiLCAiLXQiLCAiLiJdLAogICAg
ICAgICAgICAgICAgY3dkPXdvcmssIGVudj1lbnYsIGNhcHR1cmVfb3V0cHV0PVRydWUsIHRleHQ9VHJ1ZSwgdGlt
ZW91dD02MDAsCiAgICAgICAgICAgICkKICAgICAgICAgICAgaWYgcmVzdWx0LnJldHVybmNvZGUgIT0gMDoKICAg
ICAgICAgICAgICAgIHJldHVybiBmYWlsKCJidW5kbGVkIHRlc3Qgc3VpdGUgZmFpbGVkOlxuJXMiICUgKHJlc3Vs
dC5zdGRlcnJbLTQwMDA6XSwpKQoKICAgICAgICBjaGVja3NfcGF0aCA9IG9zLnBhdGguam9pbih3b3JrLCAiX29y
YWNsZV9jaGVja3MucHkiKQogICAgICAgIHdpdGggb3BlbihjaGVja3NfcGF0aCwgInciLCBlbmNvZGluZz0idXRm
LTgiKSBhcyBoYW5kbGU6CiAgICAgICAgICAgIGhhbmRsZS53cml0ZShDSEVDS1MpCiAgICAgICAgcmVzdWx0ID0g
c3VicHJvY2Vzcy5ydW4oCiAgICAgICAgICAgIFtzeXMuZXhlY3V0YWJsZSwgIl9vcmFjbGVfY2hlY2tzLnB5Il0s
CiAgICAgICAgICAgIGN3ZD13b3JrLCBlbnY9ZW52LCBjYXB0dXJlX291dHB1dD1UcnVlLCB0ZXh0PVRydWUsIHRp
bWVvdXQ9NjAwLAogICAgICAgICkKICAgICAgICBpZiByZXN1bHQucmV0dXJuY29kZSAhPSAwOgogICAgICAgICAg
ICByZXR1cm4gZmFpbCgiaGVsZC1vdXQgY2hlY2tzIGZhaWxlZDpcbiVzIiAlIChyZXN1bHQuc3RkZXJyWy00MDAw
Ol0sKSkKICAgIGZpbmFsbHk6CiAgICAgICAgc2h1dGlsLnJtdHJlZSh0bXAsIGlnbm9yZV9lcnJvcnM9VHJ1ZSkK
CiAgICBzeXMuc3Rkb3V0LndyaXRlKCJQQVNTWyVzXVxuIiAlIChUQVNLX0lELCkpCiAgICByZXR1cm4gMAoKCmlm
IF9fbmFtZV9fID09ICJfX21haW5fXyI6CiAgICByYWlzZSBTeXN0ZW1FeGl0KG1haW4oKSkK
"""

_R2_HISTORICAL_COMMAND = (
    "/Users/kelvin/.local/bin/claude",
    "--setting-sources",
    "project",
    "-p",
    "--model",
    "claude-sonnet-5",
    "--effort",
    "medium",
    "--output-format",
    "stream-json",
    "--verbose",
    "--no-session-persistence",
    "--permission-mode",
    "bypassPermissions",
    "--autocompact",
    "200000",
    "--max-budget-usd",
    "2",
)


def _decoded_base64(value: str, label: str) -> bytes:
    try:
        return base64.b64decode("".join(value.split()), validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"embedded {label} is not valid base64") from exc


def _default_task_metadata() -> dict[str, Any]:
    return {
        "task_id": TASK_ID,
        "prompt_is_identical_for_both_conditions": True,
        "initial_git_tree": "ada884f2465e84f140a6c2cdd00ad591c5da9ddb",
        "protected_paths": ["README.md"],
        "runs_bundled_suite": False,
    }


def _default_execution_metadata() -> dict[str, Any]:
    command = list(_R2_HISTORICAL_COMMAND)
    return {
        "treatment": {
            "off": {
                "label": "FABLE_OFF",
                "command": command,
                "run_root_contains_carrier": False,
            },
            "on": {
                "label": "FABLE_ON",
                "command": list(command),
                "run_root_contains_carrier": True,
            },
        },
        "purity_gate": {
            "must_be_exactly_equal_between_reference_and_run": [
                "tools",
                "agents",
                "mcp_servers",
            ]
        },
    }


@dataclass(frozen=True)
class RecoveredA01Corpus:
    """The only historical bytes/semantics allowed into the new epoch."""

    prompt: str
    fixture_bytes: bytes
    oracle_bytes: bytes
    task_metadata: Mapping[str, Any] = field(default_factory=_default_task_metadata)
    execution_metadata: Mapping[str, Any] = field(
        default_factory=_default_execution_metadata
    )


def recovered_a01_corpus() -> RecoveredA01Corpus:
    return RecoveredA01Corpus(
        prompt=_A01_PROMPT,
        fixture_bytes=_decoded_base64(_A01_FIXTURE_BASE64, "A01 fixture"),
        oracle_bytes=_decoded_base64(_A01_ORACLE_BASE64, "A01 oracle"),
    )


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
        raise ExecutionManifestError(f"value is not canonical JSON: {exc}") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionManifestError(f"{label} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ExecutionManifestError(
            f"{label} fields differ; missing={sorted(keys - set(value))}, "
            f"extra={sorted(set(value) - keys)}"
        )


def _single_task_pin(sealed: Mapping[str, Any]) -> Mapping[str, Any]:
    tasks = sealed.get("tasks")
    if not isinstance(tasks, list):
        raise ExecutionManifestError("sealed tasks must be a list")
    matches = [item for item in tasks if isinstance(item, Mapping) and item.get("task_id") == TASK_ID]
    if len(matches) != 1:
        raise ExecutionManifestError("sealed authority must contain exactly one A01 pin")
    pin = matches[0]
    if pin.get("pair_id") != PAIR_ID or pin.get("status") != "PINNED":
        raise ExecutionManifestError("sealed A01 pin is not the pinned P01 task")
    return pin


def _validate_sealed_authority(sealed_manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    sealed = _require_mapping(sealed_manifest, "sealed_manifest")
    try:
        compiler.validate_manifest(sealed)
    except Exception as exc:
        raise ExecutionManifestError(f"sealed manifest validation failed: {exc}") from exc
    if sealed.get("schema") != compiler.MANIFEST_SCHEMA:
        raise ExecutionManifestError("sealed authority schema is not prospective epoch v3")
    if sealed.get("status") != "SEALED_REQUIRES_EXTERNAL_OWNER_SIGNOFF":
        raise ExecutionManifestError("sealed authority is not signable and sealed")
    if sealed.get("epoch_id") != EXPECTED_EPOCH_ID:
        raise ExecutionManifestError("sealed authority epoch differs from the fixed epoch")

    pairs = sealed.get("schedule", {}).get("pairs")
    p01 = [item for item in pairs or () if isinstance(item, Mapping) and item.get("pair_id") == PAIR_ID]
    if len(p01) != 1 or p01[0] != {
        "pair_id": PAIR_ID,
        "task_id": TASK_ID,
        "arm_order": list(ARM_ORDER),
    }:
        raise ExecutionManifestError("sealed P01 must be exactly A01 OFF then ON")
    _single_task_pin(sealed)

    budget = _require_mapping(sealed.get("budget"), "sealed budget")
    if budget.get("max_additional_spend_usd") != EXPECTED_LEDGER_CAP_USD:
        raise ExecutionManifestError("new-epoch ledger cap is not 7.7339242")
    if budget.get("per_invocation_cap_usd") != EXPECTED_INVOCATION_CAP_USD:
        raise ExecutionManifestError("per-invocation cap is not 2.0000000")
    if budget.get("reservation_precedes_either_arm") is not True:
        raise ExecutionManifestError("sealed authority does not require pair-first reservation")
    return sealed, compiler.manifest_sha256(sealed)


def _argument_after(command: Sequence[str], flag: str) -> str | None:
    positions = [index for index, value in enumerate(command) if value == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        return None
    return command[positions[0] + 1]


def _validate_recovered_corpus(corpus: RecoveredA01Corpus, sealed: Mapping[str, Any]) -> None:
    if not isinstance(corpus.prompt, str) or not corpus.prompt:
        raise ExecutionManifestError("recovered A01 prompt must be a non-empty string")
    if not isinstance(corpus.fixture_bytes, bytes) or not corpus.fixture_bytes:
        raise ExecutionManifestError("recovered A01 fixture must be exact non-empty bytes")
    if not isinstance(corpus.oracle_bytes, bytes) or not corpus.oracle_bytes:
        raise ExecutionManifestError("recovered A01 oracle must be exact non-empty bytes")
    pin = _single_task_pin(sealed)
    observed = {
        "prompt_sha256": _sha256(corpus.prompt.encode("utf-8")),
        "fixture_sha256": _sha256(corpus.fixture_bytes),
        "oracle_sha256": _sha256(corpus.oracle_bytes),
    }
    for field, digest in observed.items():
        if pin.get(field) != digest or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ExecutionManifestError(f"SEALED_A01_CORPUS_HASH_MISMATCH:{field}")

    task_metadata = _require_mapping(corpus.task_metadata, "historical A01 task metadata")
    _exact_keys(
        task_metadata,
        {
            "task_id",
            "prompt_is_identical_for_both_conditions",
            "initial_git_tree",
            "protected_paths",
            "runs_bundled_suite",
        },
        "historical A01 task metadata",
    )
    if task_metadata.get("task_id") != TASK_ID:
        raise ExecutionManifestError("historical corpus task is not A01")
    if task_metadata.get("prompt_is_identical_for_both_conditions") is not True:
        raise ExecutionManifestError("historical A01 prompt is not condition-neutral")
    if task_metadata.get("protected_paths") != ["README.md"]:
        raise ExecutionManifestError("historical A01 protected-path semantics conflict")

    execution = _require_mapping(
        corpus.execution_metadata, "historical execution metadata"
    )
    _exact_keys(execution, {"treatment", "purity_gate"}, "historical execution metadata")
    treatment = _require_mapping(execution.get("treatment"), "historical treatment")
    _exact_keys(treatment, {"off", "on"}, "historical treatment")
    expected_arms = {
        "off": ("FABLE_OFF", False),
        "on": ("FABLE_ON", True),
    }
    commands: list[tuple[str, ...]] = []
    for name, (label, carrier_present) in expected_arms.items():
        arm = _require_mapping(treatment.get(name), f"historical treatment.{name}")
        _exact_keys(
            arm,
            {"label", "command", "run_root_contains_carrier"},
            f"historical treatment.{name}",
        )
        if arm.get("label") != label or arm.get("run_root_contains_carrier") is not carrier_present:
            raise ExecutionManifestError("EXECUTION_MANIFEST_SEMANTICS_AMBIGUOUS:treatment")
        command = arm.get("command")
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ExecutionManifestError("historical treatment command is invalid")
        commands.append(tuple(command))
    if commands[0] != commands[1]:
        raise ExecutionManifestError("EXECUTION_MANIFEST_SEMANTICS_AMBIGUOUS:command")

    provider = _require_mapping(sealed.get("provider_runtime"), "sealed provider runtime")
    if commands[0][0] != provider.get("binary_path"):
        raise ExecutionManifestError(
            "EXECUTION_MANIFEST_SEMANTICS_AMBIGUOUS:provider_executable"
        )
    if _argument_after(commands[0], "--model") != claude.CTO_MODEL_ID:
        raise ExecutionManifestError("EXECUTION_MANIFEST_SEMANTICS_AMBIGUOUS:model")
    if _argument_after(commands[0], "--effort") != claude.CTO_EFFORT:
        raise ExecutionManifestError("EXECUTION_MANIFEST_SEMANTICS_AMBIGUOUS:effort")
    historical_cap = _argument_after(commands[0], "--max-budget-usd")
    try:
        cap = Decimal(historical_cap or "")
    except Exception as exc:
        raise ExecutionManifestError("historical invocation cap is invalid") from exc
    if cap != Decimal(EXPECTED_INVOCATION_CAP_USD):
        raise ExecutionManifestError("EXECUTION_MANIFEST_SEMANTICS_AMBIGUOUS:cap")

    purity = _require_mapping(execution.get("purity_gate"), "historical purity gate")
    _exact_keys(
        purity,
        {"must_be_exactly_equal_between_reference_and_run"},
        "historical purity gate",
    )
    dimensions = purity.get("must_be_exactly_equal_between_reference_and_run")
    if (
        not isinstance(dimensions, list)
        or len(dimensions) != len(set(dimensions))
        or set(dimensions) != set(runner.REQUIRED_PURITY_DIMENSIONS)
    ):
        raise ExecutionManifestError("EXECUTION_MANIFEST_SEMANTICS_AMBIGUOUS:purity")


def _run_id(manifest_sha256: str, position: int) -> str:
    material = f"{EXPECTED_EPOCH_ID}:{manifest_sha256}:{PAIR_ID}:{TASK_ID}:{position}"
    return "run-" + _sha256(material.encode("ascii"))[:32]


def p01_run_ids(sealed_manifest_sha256: str) -> tuple[str, str]:
    """Return the stable, opaque orchestrator identities for the frozen pair."""

    if re.fullmatch(r"[0-9a-f]{64}", sealed_manifest_sha256 or "") is None:
        raise ExecutionManifestError("sealed manifest SHA256 shape is invalid")
    return (_run_id(sealed_manifest_sha256, 0), _run_id(sealed_manifest_sha256, 1))


def derive_p01_execution_manifest(
    sealed_manifest: Mapping[str, Any],
    *,
    corpus: RecoveredA01Corpus | None = None,
    expected_sealed_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic, self-contained P01/A01 OFF->ON mapping."""

    sealed, sealed_sha256 = _validate_sealed_authority(sealed_manifest)
    if (
        expected_sealed_manifest_sha256 is not None
        and expected_sealed_manifest_sha256 != sealed_sha256
    ):
        raise ExecutionManifestError("sealed manifest SHA256 does not match authority")
    recovered = recovered_a01_corpus() if corpus is None else corpus
    _validate_recovered_corpus(recovered, sealed)

    provider_runtime = copy.deepcopy(dict(sealed["provider_runtime"]))
    command = list(claude.build_cto_argv(provider_runtime["binary_path"]))
    if _argument_after(command, "--max-budget-usd") != EXPECTED_INVOCATION_CAP_USD:
        raise ExecutionManifestError("current executor cap conflicts with sealed authority")

    prompt_hash = _sha256(recovered.prompt.encode("utf-8"))
    fixture_hash = _sha256(recovered.fixture_bytes)
    oracle_hash = _sha256(recovered.oracle_bytes)
    semantics_hash = _sha256(_canonical_json_bytes(recovered.execution_metadata))
    run_ids = p01_run_ids(sealed_sha256)

    manifest = {
        "schema": EXECUTION_MANIFEST_SCHEMA,
        "epoch_id": EXPECTED_EPOCH_ID,
        "authority": {
            "sealed_manifest_schema": compiler.MANIFEST_SCHEMA,
            "sealed_manifest_sha256": sealed_sha256,
            "final_runtime_commit": sealed["final_runtime_authority"]["commit"],
            "final_runtime_tree": sealed["final_runtime_authority"]["tree"],
            "provider_runtime": provider_runtime,
            "canonical_fable": copy.deepcopy(dict(sealed["canonical_fable"])),
            "corpus_identity": {
                "prompt_sha256": prompt_hash,
                "fixture_sha256": fixture_hash,
                "oracle_sha256": oracle_hash,
                "historical_execution_semantics_sha256": semantics_hash,
            },
        },
        "pair": {
            "pair_id": PAIR_ID,
            "task_id": TASK_ID,
            "arm_order": list(ARM_ORDER),
        },
        "schedule": [
            {
                "run_id": run_ids[0],
                "condition": "OFF",
                "task_id": TASK_ID,
                "order_index": 1,
                "position_in_pair": 0,
            },
            {
                "run_id": run_ids[1],
                "condition": "ON",
                "task_id": TASK_ID,
                "order_index": 2,
                "position_in_pair": 1,
            },
        ],
        "tasks": [
            {
                "task_id": TASK_ID,
                "prompt": recovered.prompt,
                "prompt_sha256": prompt_hash,
                "prompt_is_identical_for_both_conditions": True,
                "initial_git_tree": recovered.task_metadata["initial_git_tree"],
                "protected_paths": list(recovered.task_metadata["protected_paths"]),
                "runs_bundled_suite": recovered.task_metadata["runs_bundled_suite"],
                "fixture": {
                    "filename": "A01.tar.gz",
                    "encoding": "base64",
                    "size": len(recovered.fixture_bytes),
                    "sha256": fixture_hash,
                    "data": base64.b64encode(recovered.fixture_bytes).decode("ascii"),
                },
                "oracle": {
                    "filename": "A01.py",
                    "encoding": "base64",
                    "size": len(recovered.oracle_bytes),
                    "sha256": oracle_hash,
                    "data": base64.b64encode(recovered.oracle_bytes).decode("ascii"),
                },
            }
        ],
        "treatment": {
            "delta_is_exactly": "skills: REF vs REF + fable-method",
            "off": {
                "label": "FABLE_OFF",
                "command": list(command),
                "run_root_contains_carrier": False,
            },
            "on": {
                "label": "FABLE_ON",
                "command": list(command),
                "run_root_contains_carrier": True,
                "carrier": {
                    "destination": ".claude/skills/fable-method",
                    "identity": copy.deepcopy(dict(sealed["canonical_fable"])),
                },
            },
        },
        "purity_gate": {
            "must_be_exactly_equal_between_reference_and_run": sorted(
                runner.REQUIRED_PURITY_DIMENSIONS
            )
        },
    }
    validate_execution_manifest(manifest)
    return manifest


def _decode_artifact(record: Mapping[str, Any], label: str) -> bytes:
    _exact_keys(
        record,
        {"filename", "encoding", "size", "sha256", "data"},
        label,
    )
    if record.get("encoding") != "base64":
        raise ExecutionManifestError(f"{label} encoding is not base64")
    try:
        raw = base64.b64decode(record.get("data"), validate=True)
    except (ValueError, TypeError) as exc:
        raise ExecutionManifestError(f"{label} data is invalid base64") from exc
    if record.get("size") != len(raw) or record.get("sha256") != _sha256(raw):
        raise ExecutionManifestError(f"{label} bytes do not match their identity")
    return raw


def validate_execution_manifest(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the strict P01 bridge before runner selection or persistence."""

    source = _require_mapping(manifest, "execution manifest")
    if source.get("schema") == compiler.MANIFEST_SCHEMA:
        raise ExecutionManifestError("sealed v3 authority is not runner execution input")
    _exact_keys(
        source,
        {
            "schema",
            "epoch_id",
            "authority",
            "pair",
            "schedule",
            "tasks",
            "treatment",
            "purity_gate",
        },
        "execution manifest",
    )
    if source.get("schema") != EXECUTION_MANIFEST_SCHEMA:
        raise ExecutionManifestError("execution manifest schema is invalid")
    if source.get("epoch_id") != EXPECTED_EPOCH_ID:
        raise ExecutionManifestError("execution manifest epoch is invalid")

    authority = _require_mapping(source.get("authority"), "execution authority")
    _exact_keys(
        authority,
        {
            "sealed_manifest_schema",
            "sealed_manifest_sha256",
            "final_runtime_commit",
            "final_runtime_tree",
            "provider_runtime",
            "canonical_fable",
            "corpus_identity",
        },
        "execution authority",
    )
    manifest_sha = authority.get("sealed_manifest_sha256")
    if authority.get("sealed_manifest_schema") != compiler.MANIFEST_SCHEMA or not isinstance(
        manifest_sha, str
    ) or re.fullmatch(r"[0-9a-f]{64}", manifest_sha) is None:
        raise ExecutionManifestError("execution authority does not bind sealed v3 bytes")

    pair = _require_mapping(source.get("pair"), "execution pair")
    if pair != {"pair_id": PAIR_ID, "task_id": TASK_ID, "arm_order": list(ARM_ORDER)}:
        raise ExecutionManifestError("execution pair must be exactly P01/A01/OFF->ON")

    tasks = source.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1:
        raise ExecutionManifestError("execution manifest must contain exactly one task")
    task = _require_mapping(tasks[0], "execution task")
    _exact_keys(
        task,
        {
            "task_id",
            "prompt",
            "prompt_sha256",
            "prompt_is_identical_for_both_conditions",
            "initial_git_tree",
            "protected_paths",
            "runs_bundled_suite",
            "fixture",
            "oracle",
        },
        "execution task",
    )
    if task.get("task_id") != TASK_ID or task.get("prompt_is_identical_for_both_conditions") is not True:
        raise ExecutionManifestError("execution task is not condition-neutral A01")
    prompt = task.get("prompt")
    if not isinstance(prompt, str) or task.get("prompt_sha256") != _sha256(prompt.encode("utf-8")):
        raise ExecutionManifestError("execution prompt identity is invalid")
    fixture = _decode_artifact(_require_mapping(task.get("fixture"), "fixture"), "fixture")
    oracle = _decode_artifact(_require_mapping(task.get("oracle"), "oracle"), "oracle")

    corpus_identity = _require_mapping(authority.get("corpus_identity"), "corpus identity")
    _exact_keys(
        corpus_identity,
        {
            "prompt_sha256",
            "fixture_sha256",
            "oracle_sha256",
            "historical_execution_semantics_sha256",
        },
        "corpus identity",
    )
    if corpus_identity.get("prompt_sha256") != task.get("prompt_sha256"):
        raise ExecutionManifestError("prompt identity differs from authority")
    if corpus_identity.get("fixture_sha256") != _sha256(fixture):
        raise ExecutionManifestError("fixture identity differs from authority")
    if corpus_identity.get("oracle_sha256") != _sha256(oracle):
        raise ExecutionManifestError("oracle identity differs from authority")

    schedule = source.get("schedule")
    if not isinstance(schedule, list) or len(schedule) != 2:
        raise ExecutionManifestError("execution schedule must contain exactly two slots")
    expected_schedule = [
        {
            "run_id": _run_id(manifest_sha, 0),
            "condition": "OFF",
            "task_id": TASK_ID,
            "order_index": 1,
            "position_in_pair": 0,
        },
        {
            "run_id": _run_id(manifest_sha, 1),
            "condition": "ON",
            "task_id": TASK_ID,
            "order_index": 2,
            "position_in_pair": 1,
        },
    ]
    if schedule != expected_schedule:
        raise ExecutionManifestError("execution schedule is not frozen OFF then ON")

    treatment = _require_mapping(source.get("treatment"), "execution treatment")
    _exact_keys(treatment, {"delta_is_exactly", "off", "on"}, "execution treatment")
    if treatment.get("delta_is_exactly") != "skills: REF vs REF + fable-method":
        raise ExecutionManifestError("execution treatment delta is not the intended Fable surface")
    off = _require_mapping(treatment.get("off"), "execution treatment.off")
    on = _require_mapping(treatment.get("on"), "execution treatment.on")
    _exact_keys(off, {"label", "command", "run_root_contains_carrier"}, "execution treatment.off")
    _exact_keys(on, {"label", "command", "run_root_contains_carrier", "carrier"}, "execution treatment.on")
    if off.get("label") != "FABLE_OFF" or off.get("run_root_contains_carrier") is not False:
        raise ExecutionManifestError("OFF treatment semantics are invalid")
    if on.get("label") != "FABLE_ON" or on.get("run_root_contains_carrier") is not True:
        raise ExecutionManifestError("ON treatment semantics are invalid")
    if off.get("command") != on.get("command"):
        raise ExecutionManifestError("OFF/ON command neutrality invariant failed")
    provider = _require_mapping(authority.get("provider_runtime"), "provider runtime")
    expected_command = list(claude.build_cto_argv(provider.get("binary_path")))
    if off.get("command") != expected_command:
        raise ExecutionManifestError("execution command differs from current canonical executor")
    carrier = _require_mapping(on.get("carrier"), "ON carrier")
    if carrier != {
        "destination": ".claude/skills/fable-method",
        "identity": authority.get("canonical_fable"),
    }:
        raise ExecutionManifestError("ON carrier differs from sealed canonical Fable identity")

    purity = _require_mapping(source.get("purity_gate"), "execution purity gate")
    _exact_keys(
        purity,
        {"must_be_exactly_equal_between_reference_and_run"},
        "execution purity gate",
    )
    dimensions = purity.get("must_be_exactly_equal_between_reference_and_run")
    if not isinstance(dimensions, list) or set(dimensions) != set(runner.REQUIRED_PURITY_DIMENSIONS):
        raise ExecutionManifestError("execution purity dimensions differ from runner floor")

    try:
        selected = [runner.select_manifest_slot(source, item["run_id"]) for item in schedule]
    except Exception as exc:
        raise ExecutionManifestError(f"runner slot-selection contract rejected manifest: {exc}") from exc
    if [item.condition for item in selected] != list(ARM_ORDER):
        raise ExecutionManifestError("runner selected a non-frozen arm order")
    return source


def serialize_execution_manifest(manifest: Mapping[str, Any]) -> bytes:
    validate_execution_manifest(manifest)
    return _canonical_json_bytes(manifest) + b"\n"


def execution_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return _sha256(serialize_execution_manifest(manifest))


__all__ = [
    "ARM_ORDER",
    "EXECUTION_MANIFEST_SCHEMA",
    "ExecutionManifestError",
    "PAIR_ID",
    "RecoveredA01Corpus",
    "TASK_ID",
    "derive_p01_execution_manifest",
    "execution_manifest_sha256",
    "p01_run_ids",
    "recovered_a01_corpus",
    "serialize_execution_manifest",
    "validate_execution_manifest",
]
