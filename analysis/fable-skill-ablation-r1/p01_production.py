#!/usr/bin/env python3
"""Canonical production wiring for the prospective P01 / A01 OFF->ON pair.

This module owns exactly the dependency surface that :mod:`epoch_pair_driver`
declares but deliberately does not construct:

    * the deterministic A01 workspace materializer (OFF and ON);
    * the pre-treatment reference-init authority loader;
    * :func:`build_p01_dependencies`;
    * the one sanctioned non-test caller of ``epoch_pair_driver.execute_pair``.

It is inert on import.  Nothing here materializes the canonical epoch root,
reserves budget, acquires the production lock, or launches a provider: the
only launch authority remains the existing sanctioned composition chain
(``epoch_provider_composition`` -> ``epoch_controller`` -> the mandatory
sandbox helper), reached solely through ``execute_pair``.

The module contains no provider-spawning code at all.  The single external
process it runs is ``git``, used read-only against the pinned carrier objects
and to build the declared initial task tree inside a throwaway run workspace.

The one-time zero-turn reference-init capture that *produces*
``p01_reference_init.json`` lives in :mod:`p01_reference_capture`; this module
only consumes an already-persisted artifact, and only against an explicit,
caller-supplied SHA256.
"""

from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
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
        raise ImportError(f"required production sibling is missing: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


driver = _load_sibling(
    "fable_ablation_epoch_pair_driver", "epoch_pair_driver.py", "epoch_pair_driver"
)
execution_builder = driver.execution_builder
compiler = driver.compiler
runner = driver.runner
controller = driver.controller
claude = _load_sibling(
    "fable_ablation_claude_executor", "claude_executor.py", "claude_executor"
)


# --- Reference artifact contract ------------------------------------------
#
# The persisted artifact is the *pre-treatment* provider ``system/init``
# configuration inventory and nothing else.  It is not outcome evidence, not a
# synthetic event, and not an OFF/ON self-reference.  The contract lives here
# because the production loader is its strictest consumer; the capture module
# imports these names so exactly one definition exists.

REFERENCE_INIT_SCHEMA = "FABLE_ABLATION_P01_REFERENCE_INIT/v1"
REFERENCE_CAPTURE_MODE = "ZERO_TURN_INIT_ONLY"
REFERENCE_ZERO_USD = "0.0000000"
# Frozen identity of the reference CLI input, encoded in the existing
# capture_configuration prompt_length / prompt_sha256 fields.  The persisted
# record does not store the prompt string.  Length 1 plus this digest is the
# only persisted encoding of exact "x" / 0x78.  This is REFERENCE_CLI_INPUT
# only: not PAID_TASK_PROMPT, not treatment content, not a user objective,
# and not MODEL/ASSISTANT_OUTPUT.
REFERENCE_PROMPT_LENGTH = 1
REFERENCE_PROMPT_SHA256 = (
    "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
)
REFERENCE_ARTIFACT_PATH = Path(__file__).with_name("p01_reference_init.json")

CARRIER_DESTINATION = ".claude/skills/fable-method"
CARRIER_IDENTITY_KEYS = frozenset(
    {"head", "tree", "carrier_subtree", "carrier_skill_md_sha256"}
)

REFERENCE_RECORD_KEYS = frozenset(
    {
        "schema",
        "epoch_id",
        "pair_id",
        "task_id",
        "capture_mode",
        "sealed_manifest_sha256",
        "provider_binary",
        "capture_configuration",
        "init_event",
        "init_event_sha256",
        "observed_model_turn_count",
        "observed_assistant_message_count",
        "observed_total_cost_usd",
    }
)
PROVIDER_BINARY_KEYS = frozenset({"executable", "realpath", "sha256", "version"})
CAPTURE_CONFIGURATION_KEYS = frozenset(
    {
        "argv",
        "budget_ceiling_present",
        "max_budget_usd",
        "logical_reference_budget_usd",
        "max_turns",
        "prompt_sha256",
        "prompt_length",
        "environment_keys",
        "sandbox_profile_sha256",
    }
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_OID_RE = re.compile(r"[0-9a-f]{40}")

# Mirrors p01_reference_capture.py's own flag constants: this module never
# imports a sibling's private symbols, so the same flag names are restated
# here to derive the physical budget/turns evidence from argv independently.
_BUDGET_FLAG = "--max-budget-usd"
_TURNS_FLAG = "--max-turns"
_GIT_TREE_ENTRY_RE = re.compile(r"^([0-7]{6}) (blob|tree|commit) ([0-9a-f]{40})\t(.*)$")

# Deterministic, host-independent Git behaviour.  ``core.excludesFile`` and
# ``core.attributesFile`` are pinned to os.devnull because the ambient global
# excludes file on the authoring host silently changes which fixture paths a
# plain ``git add -A`` would track, which would make the declared
# ``initial_git_tree`` reproducible only by accident.
_GIT_HERMETIC_CONFIG = (
    "-c",
    "core.excludesFile=" + os.devnull,
    "-c",
    "core.attributesFile=" + os.devnull,
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.fileMode=true",
    "-c",
    "core.symlinks=true",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "protocol.file.allow=never",
)
_GIT_FIXED_IDENTITY = {
    "GIT_AUTHOR_NAME": "Fable Ablation Materializer",
    "GIT_AUTHOR_EMAIL": "materializer@fable-ablation.invalid",
    "GIT_COMMITTER_NAME": "Fable Ablation Materializer",
    "GIT_COMMITTER_EMAIL": "materializer@fable-ablation.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}
_INITIAL_COMMIT_MESSAGE = "initial task fixture"
_INITIAL_BRANCH = "main"


class ProductionWiringError(RuntimeError):
    """Base class for fail-closed P01 production wiring errors."""


class MaterializationError(ProductionWiringError):
    """The deterministic A01 workspace could not be proven correct."""


class CarrierAuthorityError(ProductionWiringError):
    """The pinned fable-method carrier identity could not be proven."""


class ReferenceArtifactError(ProductionWiringError):
    """The pre-treatment reference-init authority is absent or unproven."""


class ProductionAuthorityError(ProductionWiringError):
    """A production precondition failed before any pair execution."""


# --- Bounded ``git`` boundary ---------------------------------------------


def _git_environment() -> dict:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(_GIT_FIXED_IDENTITY)
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_SYSTEM"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_ASKPASS"] = ""
    environment["LC_ALL"] = "C"
    return environment


def _git(repository: Path, *arguments: str) -> bytes:
    """Run one read/local ``git`` command; the executable is always ``git``."""

    if not arguments or not all(isinstance(part, str) and part for part in arguments):
        raise ProductionWiringError("git arguments must be non-empty strings")
    command = ["git", "-C", str(repository), *_GIT_HERMETIC_CONFIG, *arguments]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
            env=_git_environment(),
        )
    except OSError as exc:
        raise ProductionWiringError(f"git invocation failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ProductionWiringError(
            f"git {' '.join(arguments)} failed ({result.returncode}): {detail}"
        )
    return result.stdout


def _git_text(repository: Path, *arguments: str) -> str:
    return _git(repository, *arguments).decode("utf-8", errors="strict").strip()


# --- Pinned carrier authority ---------------------------------------------


@dataclass(frozen=True)
class CarrierBlob:
    """One exact blob of the pinned treatment carrier."""

    relative_path: str
    mode: str
    oid: str
    data: bytes


@dataclass(frozen=True)
class CarrierSource:
    """A verified, Git-reachable binding of the pinned fable-method carrier."""

    repository: Path
    head: str
    tree: str
    carrier_subtree: str
    carrier_skill_md_sha256: str
    carrier_subtree_path: str

    @property
    def identity(self) -> dict:
        return {
            "head": self.head,
            "tree": self.tree,
            "carrier_subtree": self.carrier_subtree,
            "carrier_skill_md_sha256": self.carrier_skill_md_sha256,
        }


def _validate_carrier_identity(identity: Any) -> dict:
    if not isinstance(identity, Mapping):
        raise CarrierAuthorityError("carrier identity must be an object")
    if set(identity) != CARRIER_IDENTITY_KEYS:
        raise CarrierAuthorityError("carrier identity field set is invalid")
    for key in ("head", "tree", "carrier_subtree"):
        value = identity.get(key)
        if not isinstance(value, str) or _GIT_OID_RE.fullmatch(value) is None:
            raise CarrierAuthorityError(f"carrier identity {key} is not a Git object id")
    digest = identity.get("carrier_skill_md_sha256")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise CarrierAuthorityError("carrier SKILL.md SHA256 shape is invalid")
    return dict(identity)


def _parse_tree_entries(raw: bytes, label: str) -> list:
    entries = []
    for chunk in raw.split(b"\x00"):
        if not chunk:
            continue
        try:
            line = chunk.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise CarrierAuthorityError(f"{label}:undecodable_tree_entry") from exc
        match = _GIT_TREE_ENTRY_RE.match(line)
        if match is None:
            raise CarrierAuthorityError(f"{label}:unparseable_tree_entry")
        entries.append(
            {
                "mode": match.group(1),
                "type": match.group(2),
                "oid": match.group(3),
                "path": match.group(4),
            }
        )
    return entries


def resolve_carrier_source(
    identity: Mapping = None, repository: Path = None
) -> CarrierSource:
    """Bind the pinned carrier to real, reachable Git objects.

    The carrier bytes may only come from the pinned Git authority reachable
    from the existing repository: no working-tree copy, no network, and no
    filesystem path outside the object database is consulted.
    """

    pins = _validate_carrier_identity(
        compiler.CANONICAL_FABLE if identity is None else identity
    )
    root = Path(__file__).resolve().parents[2] if repository is None else Path(repository)
    try:
        toplevel = Path(_git_text(root, "rev-parse", "--show-toplevel"))
    except ProductionWiringError as exc:
        raise CarrierAuthorityError(
            f"CARRIER_GIT_AUTHORITY_UNAVAILABLE:{exc}"
        ) from exc

    try:
        if _git_text(toplevel, "cat-file", "-t", pins["head"]) != "commit":
            raise CarrierAuthorityError("pinned carrier head is not a commit")
        observed_tree = _git_text(toplevel, "rev-parse", pins["head"] + "^{tree}")
        if observed_tree != pins["tree"]:
            raise CarrierAuthorityError("pinned carrier head does not yield the pinned tree")
        if _git_text(toplevel, "cat-file", "-t", pins["carrier_subtree"]) != "tree":
            raise CarrierAuthorityError("pinned carrier subtree is not a tree")
        reachable = _parse_tree_entries(
            _git(toplevel, "ls-tree", "-r", "-t", "-z", pins["tree"]),
            "carrier_authority_tree",
        )
    except ProductionWiringError as exc:
        if isinstance(exc, CarrierAuthorityError):
            raise
        raise CarrierAuthorityError(f"CARRIER_GIT_AUTHORITY_UNAVAILABLE:{exc}") from exc

    subtree_paths = sorted(
        entry["path"]
        for entry in reachable
        if entry["type"] == "tree" and entry["oid"] == pins["carrier_subtree"]
    )
    if len(subtree_paths) != 1:
        raise CarrierAuthorityError(
            "pinned carrier subtree is not reachable exactly once from the pinned tree"
        )

    source = CarrierSource(
        repository=toplevel,
        head=pins["head"],
        tree=pins["tree"],
        carrier_subtree=pins["carrier_subtree"],
        carrier_skill_md_sha256=pins["carrier_skill_md_sha256"],
        carrier_subtree_path=subtree_paths[0],
    )
    # Prove the declared SKILL.md content hash now, before any caller can be
    # tempted to treat mere object reachability as carrier authority.
    read_carrier_blobs(source)
    return source


def read_carrier_blobs(carrier: CarrierSource) -> tuple:
    """Read and verify every pinned carrier blob without writing anything."""

    entries = _parse_tree_entries(
        _git(carrier.repository, "ls-tree", "-r", "-z", carrier.carrier_subtree),
        "carrier_subtree",
    )
    if not entries:
        raise CarrierAuthorityError("pinned carrier subtree is empty")

    blobs = []
    for entry in entries:
        if entry["type"] != "blob" or entry["mode"] not in ("100644", "100755"):
            raise CarrierAuthorityError(
                f"pinned carrier entry is not a regular blob:{entry['path']}"
            )
        _validate_relative_path(entry["path"], "carrier")
        data = _git(carrier.repository, "cat-file", "blob", entry["oid"])
        blobs.append(
            CarrierBlob(
                relative_path=entry["path"],
                mode=entry["mode"],
                oid=entry["oid"],
                data=data,
            )
        )

    skill_md = [blob for blob in blobs if blob.relative_path == "SKILL.md"]
    if len(skill_md) != 1:
        raise CarrierAuthorityError("pinned carrier does not contain exactly one SKILL.md")
    observed = driver._sha256(skill_md[0].data)
    if observed != carrier.carrier_skill_md_sha256:
        raise CarrierAuthorityError("CARRIER_HASH_MISMATCH:SKILL.md")
    return tuple(blobs)


def _validate_relative_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise MaterializationError(f"{label}:path_must_be_a_non_empty_string")
    if value.startswith("/") or "\\" in value:
        raise MaterializationError(f"{label}:path_must_be_relative_posix:{value}")
    relative = Path(value)
    if relative.is_absolute():
        raise MaterializationError(f"{label}:path_must_be_relative:{value}")
    for part in relative.parts:
        if part in ("", ".", ".."):
            raise MaterializationError(f"{label}:path_traversal_rejected:{value}")
    return relative


# --- Deterministic A01 materializer ---------------------------------------


def _decode_declared_artifact(record: Any, label: str) -> bytes:
    """Recover exact bytes only after the declared size and SHA256 both hold."""

    if not isinstance(record, Mapping):
        raise MaterializationError(f"{label}:artifact_record_must_be_an_object")
    if set(record) != {"filename", "encoding", "size", "sha256", "data"}:
        raise MaterializationError(f"{label}:artifact_field_set_is_invalid")
    if record.get("encoding") != "base64":
        raise MaterializationError(f"{label}:unsupported_artifact_encoding")
    filename = record.get("filename")
    if not isinstance(filename, str) or not filename or "/" in filename:
        raise MaterializationError(f"{label}:artifact_filename_is_invalid")
    declared_size = record.get("size")
    declared_sha256 = record.get("sha256")
    if not isinstance(declared_size, int) or isinstance(declared_size, bool) or declared_size <= 0:
        raise MaterializationError(f"{label}:declared_size_is_invalid")
    if not isinstance(declared_sha256, str) or _SHA256_RE.fullmatch(declared_sha256) is None:
        raise MaterializationError(f"{label}:declared_sha256_is_invalid")
    payload = record.get("data")
    if not isinstance(payload, str) or not payload:
        raise MaterializationError(f"{label}:artifact_payload_is_invalid")
    try:
        data = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError) as exc:
        raise MaterializationError(f"{label}:artifact_payload_is_not_base64") from exc
    if len(data) != declared_size:
        raise MaterializationError(f"{label}:declared_size_mismatch")
    if driver._sha256(data) != declared_sha256:
        raise MaterializationError(f"{label}:declared_sha256_mismatch")
    return data


def _extract_fixture(fixture_bytes: bytes, destination: Path) -> tuple:
    """Extract the A01 fixture with an explicit, hardened member policy."""

    written = []
    try:
        archive = tarfile.open(fileobj=io.BytesIO(fixture_bytes), mode="r:*")
    except tarfile.TarError as exc:
        raise MaterializationError(f"fixture:not_a_readable_archive:{exc}") from exc
    with archive:
        for member in archive.getmembers():
            relative = _validate_relative_path(member.name, "fixture")
            target = destination / relative
            if member.isdir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise MaterializationError(
                    f"fixture:unsupported_member_type:{member.name}"
                )
            handle = archive.extractfile(member)
            if handle is None:
                raise MaterializationError(f"fixture:unreadable_member:{member.name}")
            payload = handle.read()
            if len(payload) != member.size:
                raise MaterializationError(f"fixture:short_member:{member.name}")
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            mode = 0o755 if member.mode & 0o100 else 0o644
            descriptor = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode
            )
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
            os.chmod(target, mode)
            written.append(relative.as_posix())
    if not written:
        raise MaterializationError("fixture:archive_contains_no_regular_file")
    return tuple(sorted(written))


def _write_exact_bytes(path: Path, data: bytes, mode: int, label: str) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode
        )
    except FileExistsError as exc:
        raise MaterializationError(f"{label}:target_already_exists:{path}") from exc
    except OSError as exc:
        raise MaterializationError(f"{label}:create_failed:{exc.errno}") from exc
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise MaterializationError(f"{label}:short_write")
            offset += written
    finally:
        os.close(descriptor)
    os.chmod(path, mode)


def _initialize_task_repository(
    workspace: Path, tracked: Sequence[str], expected_tree: str
) -> str:
    _git(workspace, "init", "-q", "-b", _INITIAL_BRANCH)
    hooks = workspace / ".git" / "hooks"
    if hooks.is_dir() and not hooks.is_symlink():
        for entry in sorted(hooks.iterdir()):
            if entry.is_file() and not entry.is_symlink():
                entry.unlink()
    if tracked:
        _git(workspace, "add", "--", *tracked)
    _git(workspace, "commit", "-q", "-m", _INITIAL_COMMIT_MESSAGE)
    observed = _git_text(workspace, "rev-parse", "HEAD^{tree}")
    if observed != expected_tree:
        raise MaterializationError(
            f"initial_git_tree_mismatch:expected={expected_tree}:observed={observed}"
        )
    return observed


def assert_no_forbidden_model_tokens(
    workspace: Path, forbidden_tokens: Sequence[str]
) -> None:
    """Affirm no condition/run identity reached any model-visible name."""

    root = Path(workspace)
    for current_root, directories, filenames in os.walk(root, followlinks=False):
        current = Path(current_root)
        for name in list(directories) + list(filenames):
            entry = current / name
            if entry.is_symlink():
                raise MaterializationError(
                    f"materialized_workspace_contains_symlink:{entry.relative_to(root)}"
                )
            relative = entry.relative_to(root).as_posix()
            for token in forbidden_tokens:
                if runner._contains_token(name, token) or runner._contains_token(
                    relative, token
                ):
                    raise MaterializationError(
                        f"forbidden_model_token_leaked:{relative}:{token}"
                    )


@dataclass(frozen=True)
class MaterializationReceipt:
    """Observable facts about one completed materialization."""

    condition: str
    workspace_path: str
    initial_git_tree: str
    tracked_paths: tuple
    untracked_protected_paths: tuple
    carrier_installed: bool
    carrier_blob_count: int
    held_out_oracle_path: str


def build_p01_materializer(
    *, carrier: CarrierSource, hold_out_root: Path
) -> Callable[[Any], None]:
    """Return the canonical deterministic materializer for the P01 pair.

    The returned callable is a pure function of :class:`runner.WorkspacePlan`
    plus the two explicitly bound authorities above.  It reads no historical
    run state, outcome, or evidence: every byte it writes comes from the
    already-hash-verified execution-manifest metadata carried on the slot, or
    from the pinned carrier object database.
    """

    carrier_blobs = read_carrier_blobs(carrier)
    hold_out = Path(hold_out_root)
    if not hold_out.is_absolute():
        raise ProductionAuthorityError("held-out oracle root must be an absolute path")
    receipts = {}

    def materialize(plan: Any) -> None:
        receipt = materialize_p01_workspace(
            plan, carrier=carrier, carrier_blobs=carrier_blobs, hold_out_root=hold_out
        )
        receipts[receipt.workspace_path] = receipt

    materialize.receipts = receipts  # type: ignore[attr-defined]
    materialize.carrier = carrier  # type: ignore[attr-defined]
    materialize.hold_out_root = hold_out  # type: ignore[attr-defined]
    return materialize


def materialize_p01_workspace(
    plan: Any,
    *,
    carrier: CarrierSource,
    carrier_blobs: Sequence[CarrierBlob] = None,
    hold_out_root: Path,
) -> MaterializationReceipt:
    """Materialize exactly one A01 arm workspace and prove every declared pin."""

    slot = getattr(plan, "slot", None)
    workspace_path = getattr(plan, "workspace_path", None)
    if slot is None or not isinstance(workspace_path, str) or not workspace_path:
        raise MaterializationError("workspace plan is not a runner.WorkspacePlan")
    if slot.task_id != execution_builder.TASK_ID:
        raise MaterializationError(f"unsupported task for P01 materializer:{slot.task_id}")
    if slot.condition not in execution_builder.ARM_ORDER:
        raise MaterializationError(f"unsupported condition:{slot.condition}")

    workspace = Path(workspace_path)
    if not workspace.is_dir() or workspace.is_symlink():
        raise MaterializationError("workspace must already be a real directory")
    if any(workspace.iterdir()):
        raise MaterializationError("workspace must be empty before materialization")

    hold_out = Path(hold_out_root).resolve()
    resolved_workspace = workspace.resolve()
    if hold_out == resolved_workspace or _is_within(hold_out, resolved_workspace):
        raise MaterializationError("held-out oracle root must lie outside the workspace")
    if _is_within(resolved_workspace, hold_out):
        raise MaterializationError("workspace must lie outside the held-out oracle root")

    task = slot.task_metadata
    if not isinstance(task, Mapping):
        raise MaterializationError("slot task metadata must be an object")
    expected_tree = task.get("initial_git_tree")
    if not isinstance(expected_tree, str) or _GIT_OID_RE.fullmatch(expected_tree) is None:
        raise MaterializationError("declared initial_git_tree is invalid")
    protected = task.get("protected_paths")
    if not isinstance(protected, list) or not all(
        isinstance(item, str) and item for item in protected
    ):
        raise MaterializationError("declared protected_paths are invalid")
    protected_set = {
        _validate_relative_path(item, "protected_path").as_posix() for item in protected
    }

    fixture_record = task.get("fixture")
    oracle_record = task.get("oracle")
    fixture_bytes = _decode_declared_artifact(fixture_record, "fixture")
    oracle_bytes = _decode_declared_artifact(oracle_record, "oracle")

    oracle_path = hold_out / plan.opaque_identity / oracle_record["filename"]

    extracted = _extract_fixture(fixture_bytes, workspace)
    missing_protected = protected_set.difference(extracted)
    if missing_protected:
        raise MaterializationError(
            "declared protected paths absent from fixture:" + ",".join(sorted(missing_protected))
        )
    tracked = tuple(path for path in extracted if path not in protected_set)
    if not tracked:
        raise MaterializationError("fixture yields no trackable path")

    initial_tree = _initialize_task_repository(workspace, tracked, expected_tree)

    treatment = slot.treatment_metadata
    if not isinstance(treatment, Mapping):
        raise MaterializationError("slot treatment metadata must be an object")
    if treatment.get("run_root_contains_carrier") is not slot.expected_fable_engaged:
        raise MaterializationError("treatment carrier declaration is self-inconsistent")

    carrier_destination = workspace / CARRIER_DESTINATION
    installed = 0
    if slot.expected_fable_engaged:
        declared = treatment.get("carrier")
        if not isinstance(declared, Mapping) or set(declared) != {"destination", "identity"}:
            raise MaterializationError("ON treatment does not declare an exact carrier")
        if declared.get("destination") != CARRIER_DESTINATION:
            raise MaterializationError("ON carrier destination differs from the pinned path")
        if _validate_carrier_identity(declared.get("identity")) != carrier.identity:
            raise CarrierAuthorityError(
                "ON carrier identity differs from the bound authority"
            )
        blobs = (
            read_carrier_blobs(carrier) if carrier_blobs is None else tuple(carrier_blobs)
        )
        skill_md = [blob for blob in blobs if blob.relative_path == "SKILL.md"]
        if (
            len(skill_md) != 1
            or driver._sha256(skill_md[0].data) != carrier.carrier_skill_md_sha256
        ):
            raise CarrierAuthorityError("CARRIER_HASH_MISMATCH:SKILL.md")
        for blob in blobs:
            _write_exact_bytes(
                carrier_destination / blob.relative_path,
                blob.data,
                0o755 if blob.mode == "100755" else 0o644,
                "carrier",
            )
            installed += 1
        if not (carrier_destination / "SKILL.md").is_file():
            raise MaterializationError("ON carrier SKILL.md was not installed")
    elif carrier_destination.exists():
        raise MaterializationError("OFF workspace must not contain the treatment carrier")

    assert_no_forbidden_model_tokens(workspace, slot.forbidden_model_tokens)

    # The A01 oracle is a *held-out* grader: it is run against the finished
    # work directory afterwards and must never become model-visible, so it is
    # written outside the workspace under the explicitly bound held-out root.
    # It is written last so no failed materialization leaves held-out residue.
    _write_exact_bytes(oracle_path, oracle_bytes, 0o600, "held_out_oracle")

    return MaterializationReceipt(
        condition=slot.condition,
        workspace_path=str(workspace),
        initial_git_tree=initial_tree,
        tracked_paths=tracked,
        untracked_protected_paths=tuple(sorted(protected_set)),
        carrier_installed=bool(slot.expected_fable_engaged),
        carrier_blob_count=installed,
        held_out_oracle_path=str(oracle_path),
    )


def _is_within(candidate: Path, ancestor: Path) -> bool:
    try:
        candidate.relative_to(ancestor)
    except ValueError:
        return False
    return True


# --- Reference-init authority ---------------------------------------------


def canonical_reference_bytes(record: Mapping) -> bytes:
    """Return the one deterministic on-disk representation of the artifact."""

    return driver._canonical_json_bytes(record) + b"\n"


def reference_artifact_sha256(record: Mapping) -> str:
    """Return the exact SHA256 a future paid authorization would pin."""

    return driver._sha256(canonical_reference_bytes(record))


def init_event_sha256(event: Mapping) -> str:
    return driver._sha256(driver._canonical_json_bytes(event))


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ReferenceArtifactError(reason)


def _derive_budget_ceiling_from_argv(argv: Sequence[str]) -> tuple:
    """Return ``(budget_ceiling_present, max_budget_usd)`` purely from argv.

    Evidence of the physical CLI request only, never a standalone constant:
    an absent flag means an absent physical ceiling.
    """

    values = list(argv)
    if _BUDGET_FLAG not in values:
        return False, None
    if values.count(_BUDGET_FLAG) != 1:
        raise ReferenceArtifactError("reference capture argv budget flag is ambiguous")
    index = values.index(_BUDGET_FLAG)
    if index + 1 >= len(values):
        raise ReferenceArtifactError("reference capture argv budget flag has no value")
    return True, values[index + 1]


def _derive_configured_max_turns_from_argv(argv: Sequence[str]) -> int:
    """Return the configured/requested max-turns value purely from argv.

    Evidence of the CLI request only: not the terminal ``num_turns``, not an
    observed turn count, and not an ex-ante zero-turn guarantee.
    """

    values = list(argv)
    if values.count(_TURNS_FLAG) != 1:
        raise ReferenceArtifactError("reference capture argv turns flag is not exactly one")
    index = values.index(_TURNS_FLAG)
    if index + 1 >= len(values):
        raise ReferenceArtifactError("reference capture argv turns flag has no value")
    try:
        return int(values[index + 1])
    except ValueError as exc:
        raise ReferenceArtifactError(
            "reference capture argv turns value is not an integer"
        ) from exc


def validate_reference_record(
    record: Mapping,
    *,
    expected_sealed_manifest_sha256: str = driver.SEALED_MANIFEST_SHA256,
) -> Mapping:
    """Validate the minimal reference artifact contract; absence never passes."""

    _require(isinstance(record, Mapping), "reference record must be an object")
    _require(set(record) == REFERENCE_RECORD_KEYS, "reference field set is invalid")
    _require(record.get("schema") == REFERENCE_INIT_SCHEMA, "reference schema is invalid")
    _require(record.get("epoch_id") == driver.EPOCH_ID, "reference epoch identity is invalid")
    _require(
        record.get("pair_id") == execution_builder.PAIR_ID,
        "reference pair identity is invalid",
    )
    _require(
        record.get("task_id") == execution_builder.TASK_ID,
        "reference task identity is invalid",
    )
    _require(
        record.get("capture_mode") == REFERENCE_CAPTURE_MODE,
        "reference capture mode is not zero-turn init-only",
    )
    _require(
        record.get("sealed_manifest_sha256") == expected_sealed_manifest_sha256,
        "reference does not bind the exact sealed manifest",
    )

    provider = record.get("provider_binary")
    _require(isinstance(provider, Mapping), "reference provider identity must be an object")
    _require(set(provider) == PROVIDER_BINARY_KEYS, "reference provider field set is invalid")
    for key in ("executable", "realpath", "version"):
        value = provider.get(key)
        _require(
            isinstance(value, str) and bool(value.strip()),
            f"reference provider {key} is invalid",
        )
    _require(
        isinstance(provider.get("sha256"), str)
        and _SHA256_RE.fullmatch(provider["sha256"]) is not None,
        "reference provider SHA256 is invalid",
    )

    configuration = record.get("capture_configuration")
    _require(
        isinstance(configuration, Mapping),
        "reference capture configuration must be an object",
    )
    _require(
        set(configuration) == CAPTURE_CONFIGURATION_KEYS,
        "reference capture configuration field set is invalid",
    )
    argv = configuration.get("argv")
    _require(
        isinstance(argv, list)
        and bool(argv)
        and all(isinstance(part, str) and part for part in argv),
        "reference capture argv is invalid",
    )
    budget_ceiling_present, derived_max_budget_usd = _derive_budget_ceiling_from_argv(argv)
    _require(
        configuration.get("budget_ceiling_present") is budget_ceiling_present,
        "reference capture budget ceiling presence does not match argv",
    )
    _require(
        configuration.get("max_budget_usd") == derived_max_budget_usd,
        "reference capture max_budget_usd does not match argv",
    )
    _require(
        budget_ceiling_present is False,
        "reference capture argv must omit the physical budget ceiling",
    )
    _require(
        configuration.get("logical_reference_budget_usd") == REFERENCE_ZERO_USD,
        "reference capture logical budget is not exactly 0.0000000",
    )
    derived_max_turns = _derive_configured_max_turns_from_argv(argv)
    _require(
        type(configuration.get("max_turns")) is int
        and configuration.get("max_turns") == derived_max_turns,
        "reference capture max_turns does not match argv",
    )
    _require(configuration.get("max_turns") == 0, "reference capture max_turns is not 0")
    _require(
        isinstance(configuration.get("prompt_sha256"), str)
        and _SHA256_RE.fullmatch(configuration["prompt_sha256"]) is not None,
        "reference capture prompt SHA256 is invalid",
    )
    _require(
        type(configuration.get("prompt_length")) is int
        and configuration.get("prompt_length") == REFERENCE_PROMPT_LENGTH,
        "reference capture prompt length is not the frozen identity",
    )
    _require(
        configuration.get("prompt_sha256") == REFERENCE_PROMPT_SHA256,
        "reference capture prompt SHA256 is not the frozen identity",
    )
    keys = configuration.get("environment_keys")
    _require(
        isinstance(keys, list)
        and all(isinstance(item, str) and item for item in keys)
        and sorted(set(keys)) == list(keys),
        "reference capture environment keys are invalid",
    )
    _require(
        isinstance(configuration.get("sandbox_profile_sha256"), str)
        and _SHA256_RE.fullmatch(configuration["sandbox_profile_sha256"]) is not None,
        "reference capture sandbox profile identity is invalid",
    )

    event = record.get("init_event")
    _require(isinstance(event, Mapping), "reference init event must be an object")
    _require(
        event.get("apiKeySource") != "none",
        "reference init event is not a capture: apiKeySource is none",
    )
    _require(
        record.get("init_event_sha256") == init_event_sha256(event),
        "reference init event SHA256 does not bind the recorded event",
    )
    _require(
        record.get("observed_model_turn_count") == 0
        and not isinstance(record.get("observed_model_turn_count"), bool),
        "reference capture observed a model turn",
    )
    _require(
        record.get("observed_assistant_message_count") == 0
        and not isinstance(record.get("observed_assistant_message_count"), bool),
        "reference capture observed an assistant message",
    )
    _require(
        record.get("observed_total_cost_usd") == REFERENCE_ZERO_USD,
        "reference capture cost is not exactly 0.0000000",
    )
    return record


@dataclass(frozen=True)
class LoadedReference:
    """A validated pre-treatment reference-init authority."""

    path: Path
    sha256: str
    record: Mapping
    events: tuple
    init: Any

    @property
    def skill_identities(self) -> frozenset:
        return self.init.skill_identities

    @property
    def inventories(self) -> Mapping:
        return self.init.inventories


@dataclass(frozen=True)
class TreatmentDeltaExpectation:
    """The truthful, per-arm classification of the reference/run comparison."""

    reference_skill_identities: frozenset
    off_expected_skill_identities: frozenset
    on_expected_skill_identities: frozenset
    off_evidence_class: str = "CONFIGURATION_AND_MATERIALIZER_STABILITY_EVIDENCE"
    on_evidence_class: str = "LOAD_BEARING_NON_VACUOUS_TREATMENT_PURITY_EVIDENCE"

    def expected_for_condition(self, condition: str) -> frozenset:
        if condition == "OFF":
            return self.off_expected_skill_identities
        if condition == "ON":
            return self.on_expected_skill_identities
        raise ReferenceArtifactError(f"unsupported condition:{condition}")

    def evidence_class_for_condition(self, condition: str) -> str:
        if condition == "OFF":
            return self.off_evidence_class
        if condition == "ON":
            return self.on_evidence_class
        raise ReferenceArtifactError(f"unsupported condition:{condition}")


def treatment_delta_expectation(reference: LoadedReference) -> TreatmentDeltaExpectation:
    """Derive the exact OFF/ON skill-inventory expectation from the reference.

    ON is the load-bearing, non-vacuous check: the run inventory must be the
    reference inventory plus exactly ``fable-method``.  OFF equality proves the
    configuration and materializer stayed stable; it is *not* independent proof
    of treatment purity, and this classification says so rather than letting a
    passing OFF arm masquerade as treatment evidence.
    """

    baseline = frozenset(reference.skill_identities)
    if not baseline:
        raise ReferenceArtifactError("reference skill inventory is empty")
    if runner.FABLE_SKILL_IDENTITY in baseline:
        raise ReferenceArtifactError(
            "reference skill inventory already contains fable-method"
        )
    return TreatmentDeltaExpectation(
        reference_skill_identities=baseline,
        off_expected_skill_identities=baseline,
        on_expected_skill_identities=baseline | {runner.FABLE_SKILL_IDENTITY},
    )


def load_reference_init(
    path: Path = None,
    *,
    expected_reference_sha256: str,
    expected_sealed_manifest_sha256: str = driver.SEALED_MANIFEST_SHA256,
) -> LoadedReference:
    """Load the persisted reference authority against an exact caller SHA256.

    Neither a filename, nor a compiler ``VALID`` verdict, nor a well-formed
    schema is sufficient authority: the caller must name the exact artifact
    bytes it authorized, and every semantic gate below must hold affirmatively.
    """

    if (
        not isinstance(expected_reference_sha256, str)
        or _SHA256_RE.fullmatch(expected_reference_sha256) is None
    ):
        raise ReferenceArtifactError(
            "an exact 64-hex expected_reference_sha256 is required"
        )
    artifact = REFERENCE_ARTIFACT_PATH if path is None else Path(path)
    try:
        raw = driver._secure_read(artifact, "p01_reference_init")
    except driver.PairDriverError as exc:
        raise ReferenceArtifactError(f"reference artifact is unreadable:{exc}") from exc

    observed = driver._sha256(raw)
    if observed != expected_reference_sha256:
        raise ReferenceArtifactError(
            f"reference artifact SHA256 mismatch:expected={expected_reference_sha256}"
            f":observed={observed}"
        )

    try:
        record = driver._load_unique_json(raw, "p01_reference_init")
    except driver.PairDriverError as exc:
        raise ReferenceArtifactError(f"reference artifact is not strict JSON:{exc}") from exc

    validate_reference_record(
        record, expected_sealed_manifest_sha256=expected_sealed_manifest_sha256
    )
    if canonical_reference_bytes(record) != raw:
        raise ReferenceArtifactError(
            "reference artifact bytes are not the canonical serialization"
        )

    events = (copy.deepcopy(dict(record["init_event"])),)
    init = runner.parse_init_events(events, sorted(runner.REQUIRED_PURITY_DIMENSIONS))
    if not init.init_event_found or not init.valid:
        raise ReferenceArtifactError(
            "reference init event is not a valid resolved system/init:"
            + ",".join(init.errors)
        )
    if not init.skills_resolved:
        raise ReferenceArtifactError("reference skill inventory did not resolve")
    if not init.skill_identities:
        raise ReferenceArtifactError("reference skill inventory is empty")
    if runner.FABLE_SKILL_IDENTITY in init.skill_identities:
        raise ReferenceArtifactError(
            "reference skill inventory already contains fable-method"
        )
    for dimension in sorted(runner.REQUIRED_PURITY_DIMENSIONS):
        if dimension not in init.inventories:
            raise ReferenceArtifactError(f"reference omits required dimension:{dimension}")
        if not init.inventories[dimension]:
            raise ReferenceArtifactError(f"reference dimension is empty:{dimension}")

    return LoadedReference(
        path=artifact, sha256=observed, record=record, events=events, init=init
    )


# --- Dependency construction ----------------------------------------------

_SANCTIONED_DEPENDENCY_FIELDS = frozenset({"reference_events", "materializer", "policy"})


def build_p01_dependencies(
    *,
    reference: LoadedReference,
    carrier: CarrierSource,
    hold_out_root: Path,
    provider_binary: Any,
    policy: Any = None,
    environment: Mapping = None,
    **overrides: Any,
) -> Any:
    """Construct the existing ``PairRuntimeDependencies`` from real authorities.

    Nothing about the driver, executor, ledger, sandbox, settlement, evidence,
    or paid-authorization logic is reimplemented here: the provider policy is
    the existing :func:`claude_executor.make_cto_policy`, and every remaining
    boundary keeps the dataclass default that already points at the verified
    runtime component.
    """

    if not isinstance(reference, LoadedReference):
        raise ProductionAuthorityError("a validated LoadedReference is required")
    if not isinstance(carrier, CarrierSource):
        raise ProductionAuthorityError("a verified CarrierSource is required")
    conflicting = _SANCTIONED_DEPENDENCY_FIELDS.intersection(overrides)
    if conflicting:
        raise ProductionAuthorityError(
            "sanctioned dependency fields cannot be overridden:"
            + ",".join(sorted(conflicting))
        )
    resolved_policy = (
        claude.make_cto_policy(provider_binary) if policy is None else policy
    )
    values = {
        "reference_events": reference.events,
        "materializer": build_p01_materializer(
            carrier=carrier, hold_out_root=hold_out_root
        ),
        "policy": resolved_policy,
        "environment": dict(environment or {}),
    }
    values.update(overrides)
    return driver.PairRuntimeDependencies(**values)


# --- The one sanctioned production entrypoint ------------------------------


@dataclass(frozen=True)
class ProductionRunConfig:
    """Every input the sanctioned production caller needs, stated explicitly."""

    expected_reference_sha256: str
    paid_authorization_path: Path
    hold_out_root: Path
    provider_binary_path: str
    expected_provider_sha256: str
    expected_provider_version: str
    carrier_repository: Path = None
    reference_artifact_path: Path = REFERENCE_ARTIFACT_PATH
    expected_manifest_sha256: str = driver.SEALED_MANIFEST_SHA256
    epoch_paths: Any = None
    environment: Mapping = field(default_factory=dict)
    dependency_overrides: Mapping = field(default_factory=dict)

    def resolved_paths(self) -> Any:
        return driver.EpochPaths.canonical() if self.epoch_paths is None else self.epoch_paths


def load_paid_authorization(path: Path) -> Mapping:
    """Read the exact paid-pair authorization record; semantics stay in the driver."""

    try:
        raw = driver._secure_read(Path(path), "paid_pair_authorization")
    except driver.PairDriverError as exc:
        raise ProductionAuthorityError(
            f"paid-pair authorization is unreadable:{exc}"
        ) from exc
    try:
        record = driver._load_unique_json(raw, "paid_pair_authorization")
    except driver.PairDriverError as exc:
        raise ProductionAuthorityError(
            f"paid-pair authorization is not strict JSON:{exc}"
        ) from exc
    if record.get("schema") != driver.PAID_PAIR_AUTHORIZATION_SCHEMA:
        raise ProductionAuthorityError("paid-pair authorization schema is invalid")
    if record.get("decision") != "AUTHORIZE_PAID_PAIR":
        raise ProductionAuthorityError("paid-pair authorization decision is invalid")
    return record


def resolve_pinned_provider_binary(
    config: ProductionRunConfig,
    *,
    provider_identity_resolver: Callable[[str], Any],
) -> Any:
    """Resolve the provider binary identity and require the exact declared pins."""

    if _SHA256_RE.fullmatch(config.expected_provider_sha256 or "") is None:
        raise ProductionAuthorityError("expected provider SHA256 shape is invalid")
    if not config.expected_provider_version.strip():
        raise ProductionAuthorityError("expected provider version is required")
    try:
        identity = provider_identity_resolver(config.provider_binary_path)
    except Exception as exc:
        raise ProductionAuthorityError(
            f"provider binary identity could not be resolved:{exc}"
        ) from exc
    if getattr(identity, "sha256", None) != config.expected_provider_sha256:
        raise ProductionAuthorityError("provider binary SHA256 differs from the pin")
    if getattr(identity, "version", None) != config.expected_provider_version:
        raise ProductionAuthorityError("provider binary version differs from the pin")
    if getattr(identity, "executable", None) != config.provider_binary_path:
        raise ProductionAuthorityError("provider binary executable differs from the pin")
    return identity


def prepare_p01_execution(
    config: ProductionRunConfig,
    *,
    provider_identity_resolver: Callable[[str], Any] = None,
) -> tuple:
    """Run every fail-closed precondition and build the pair dependencies.

    This performs no epoch, ledger, lock, or provider action.  It returns only
    when the reference authority, paid authorization, carrier authority,
    provider pin, and dependency construction have all succeeded.
    """

    resolver = (
        claude.resolve_provider_binary_identity
        if provider_identity_resolver is None
        else provider_identity_resolver
    )
    artifact = Path(config.reference_artifact_path)
    if not artifact.exists():
        raise ReferenceArtifactError(
            f"reference artifact has not been captured yet:{artifact}"
        )
    reference = load_reference_init(
        artifact,
        expected_reference_sha256=config.expected_reference_sha256,
        expected_sealed_manifest_sha256=config.expected_manifest_sha256,
    )
    delta = treatment_delta_expectation(reference)

    authorization = load_paid_authorization(config.paid_authorization_path)

    carrier = resolve_carrier_source(repository=config.carrier_repository)

    hold_out = Path(config.hold_out_root)
    if not hold_out.is_absolute():
        raise ProductionAuthorityError("held-out oracle root must be an absolute path")
    try:
        metadata = os.lstat(hold_out)
    except OSError as exc:
        raise ProductionAuthorityError(
            f"held-out oracle root must already exist:{exc.errno}"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ProductionAuthorityError("held-out oracle root must be a real directory")

    identity = resolve_pinned_provider_binary(
        config, provider_identity_resolver=resolver
    )
    dependencies = build_p01_dependencies(
        reference=reference,
        carrier=carrier,
        hold_out_root=hold_out,
        provider_binary=identity,
        environment=config.environment,
        **dict(config.dependency_overrides),
    )
    return reference, delta, authorization, dependencies


def run_p01_production(
    config: ProductionRunConfig,
    *,
    provider_identity_resolver: Callable[[str], Any] = None,
) -> Mapping:
    """The one sanctioned non-test caller of ``epoch_pair_driver.execute_pair``.

    Every precondition is proven first and fails closed before execution:
    absent reference artifact, absent/mismatched expected reference SHA256,
    invalid reference init, missing/invalid paid authorization, unproven
    carrier or provider authority, or failed dependency construction.  The
    sealed manifest, owner signoff, epoch layout, lock, ledger, reservation,
    provider launch, and settlement all remain owned by the driver and the
    existing sanctioned composition chain.
    """

    _reference, _delta, authorization, dependencies = prepare_p01_execution(
        config, provider_identity_resolver=provider_identity_resolver
    )
    return driver.execute_pair(
        config.resolved_paths(),
        paid_authorization=authorization,
        dependencies=dependencies,
        expected_manifest_sha256=config.expected_manifest_sha256,
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p01_production",
        description=(
            "Execute or safely resume the authorized P01 / A01 OFF->ON pair. "
            "Requires an already-captured reference artifact and an exact "
            "Owner-authorized paid-pair authorization record."
        ),
    )
    parser.add_argument("--expected-reference-sha256", required=True)
    parser.add_argument("--paid-authorization", required=True, type=Path)
    parser.add_argument("--hold-out-root", required=True, type=Path)
    parser.add_argument("--provider-binary", required=True)
    parser.add_argument("--expected-provider-sha256", required=True)
    parser.add_argument("--expected-provider-version", required=True)
    parser.add_argument("--carrier-repository", default=None, type=Path)
    parser.add_argument(
        "--reference-artifact", default=REFERENCE_ARTIFACT_PATH, type=Path
    )
    parser.add_argument(
        "--expected-manifest-sha256", default=driver.SEALED_MANIFEST_SHA256
    )
    return parser


def main(argv: Sequence[str] = None) -> int:
    """Module entrypoint: directly invokable, no scratch or heredoc wrapper."""

    arguments = _build_argument_parser().parse_args(
        list(sys.argv[1:] if argv is None else argv)
    )
    config = ProductionRunConfig(
        expected_reference_sha256=arguments.expected_reference_sha256,
        paid_authorization_path=arguments.paid_authorization,
        hold_out_root=arguments.hold_out_root,
        provider_binary_path=arguments.provider_binary,
        expected_provider_sha256=arguments.expected_provider_sha256,
        expected_provider_version=arguments.expected_provider_version,
        carrier_repository=arguments.carrier_repository,
        reference_artifact_path=arguments.reference_artifact,
        expected_manifest_sha256=arguments.expected_manifest_sha256,
    )
    try:
        aggregate = run_p01_production(config)
    except (ProductionWiringError, driver.PairDriverError) as exc:
        sys.stderr.write(f"P01_PRODUCTION_REFUSED:{type(exc).__name__}:{exc}\n")
        return 2
    sys.stdout.write(
        json.dumps(aggregate, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    )
    return 0


__all__ = [
    "CARRIER_DESTINATION",
    "CARRIER_IDENTITY_KEYS",
    "CAPTURE_CONFIGURATION_KEYS",
    "CarrierAuthorityError",
    "CarrierBlob",
    "CarrierSource",
    "LoadedReference",
    "MaterializationError",
    "MaterializationReceipt",
    "PROVIDER_BINARY_KEYS",
    "ProductionAuthorityError",
    "ProductionRunConfig",
    "ProductionWiringError",
    "REFERENCE_ARTIFACT_PATH",
    "REFERENCE_CAPTURE_MODE",
    "REFERENCE_INIT_SCHEMA",
    "REFERENCE_PROMPT_LENGTH",
    "REFERENCE_PROMPT_SHA256",
    "REFERENCE_RECORD_KEYS",
    "REFERENCE_ZERO_USD",
    "ReferenceArtifactError",
    "TreatmentDeltaExpectation",
    "assert_no_forbidden_model_tokens",
    "build_p01_dependencies",
    "build_p01_materializer",
    "canonical_reference_bytes",
    "init_event_sha256",
    "load_paid_authorization",
    "load_reference_init",
    "main",
    "materialize_p01_workspace",
    "prepare_p01_execution",
    "read_carrier_blobs",
    "reference_artifact_sha256",
    "resolve_carrier_source",
    "resolve_pinned_provider_binary",
    "run_p01_production",
    "treatment_delta_expectation",
    "validate_reference_record",
]


if __name__ == "__main__":
    raise SystemExit(main())
