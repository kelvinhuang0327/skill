#!/usr/bin/env python3
"""Offline acceptance for the canonical P01 production wiring.

Every test here runs with a fake/injected provider boundary.  No provider is
invoked, no canonical epoch root is materialized, no lock or reservation is
taken, and no reference artifact is created.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


sys.dont_write_bytecode = True
MODULE_PATH = Path(__file__).with_name("p01_production.py")
SPEC = importlib.util.spec_from_file_location("fable_ablation_p01_production", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {MODULE_PATH}")
production = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = production
SPEC.loader.exec_module(production)

driver = production.driver
runner = production.runner
execution_builder = production.execution_builder
claude = production.claude

SOURCE_DIRECTORY = MODULE_PATH.parent
REPOSITORY_ROOT = MODULE_PATH.resolve().parents[2]
SEALED_MANIFEST_PATH = Path(
    "/Users/kelvin/FableAblation-Epoch-56a62a62e5aae9fe3a230cbbed831a2a/build/manifest.json"
)
SEALED_SHA256 = driver.SEALED_MANIFEST_SHA256
_EXECUTION_MANIFEST: Mapping = {}


def execution_manifest() -> Mapping:
    global _EXECUTION_MANIFEST
    if not _EXECUTION_MANIFEST:
        sealed = json.loads(SEALED_MANIFEST_PATH.read_text(encoding="utf-8"))
        _EXECUTION_MANIFEST = execution_builder.derive_p01_execution_manifest(
            sealed, expected_sealed_manifest_sha256=SEALED_SHA256
        )
    return copy.deepcopy(dict(_EXECUTION_MANIFEST))


def run_ids() -> tuple:
    return execution_builder.p01_run_ids(SEALED_SHA256)


def plain_git(repository: Path, *arguments: str) -> bytes:
    """An independent git path, so tests never trust the module under test."""

    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(arguments)} failed: {result.stderr.decode('utf-8', 'replace')}"
        )
    return result.stdout


def reference_init_event(
    *,
    skills=("reference-skill", "another-skill"),
    tools=("Bash", "Read", "Edit"),
    agents=("general-purpose",),
    mcp_servers=("local-reference",),
    drop=(),
    empty=(),
) -> dict:
    event = {
        "type": "system",
        "subtype": "init",
        "session_id": "reference-session",
        "model": claude.CTO_MODEL_ID,
        "skills": list(skills),
        "tools": list(tools),
        "agents": list(agents),
        "mcp_servers": list(mcp_servers),
    }
    for name in drop:
        event.pop(name, None)
    for name in empty:
        event[name] = []
    return event


def reference_record(event: Mapping = None, **overrides: Any) -> dict:
    resolved = reference_init_event() if event is None else copy.deepcopy(dict(event))
    record = {
        "schema": production.REFERENCE_INIT_SCHEMA,
        "epoch_id": driver.EPOCH_ID,
        "pair_id": execution_builder.PAIR_ID,
        "task_id": execution_builder.TASK_ID,
        "capture_mode": production.REFERENCE_CAPTURE_MODE,
        "sealed_manifest_sha256": SEALED_SHA256,
        "provider_binary": {
            "executable": "/offline/claude",
            "realpath": "/offline/claude",
            "sha256": "a" * 64,
            "version": "2.1.245 (Claude Code)",
        },
        "capture_configuration": {
            "argv": ["/offline/claude", "--max-turns", "0"],
            "budget_ceiling_present": False,
            "max_budget_usd": None,
            "logical_reference_budget_usd": "0.0000000",
            "max_turns": 0,
            "prompt_sha256": hashlib.sha256(b"").hexdigest(),
            "prompt_length": 0,
            "environment_keys": [],
            "sandbox_profile_sha256": "b" * 64,
        },
        "init_event": resolved,
        "init_event_sha256": production.init_event_sha256(resolved),
        "observed_model_turn_count": 0,
        "observed_assistant_message_count": 0,
        "observed_total_cost_usd": "0.0000000",
    }
    record.update(overrides)
    return record


def write_reference(path: Path, record: Mapping) -> str:
    data = production.canonical_reference_bytes(record)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def paid_authorization_record() -> dict:
    return {
        "schema": driver.PAID_PAIR_AUTHORIZATION_SCHEMA,
        "decision": "AUTHORIZE_PAID_PAIR",
        "epoch_id": driver.EPOCH_ID,
        "manifest_sha256": SEALED_SHA256,
        "pair_id": "P01",
        "task_id": "A01",
        "arm_order": ["OFF", "ON"],
        "maximum_pair_spend_usd": "4.0000000",
        "per_invocation_cap_usd": "2.0000000",
        "pre_pair_headroom_usd": "7.7339242",
    }


class FakeProviderIdentity:
    def __init__(self, executable: str) -> None:
        self.executable = executable
        self.realpath = "/offline/claude"
        self.sha256 = "c" * 64
        self.version = "2.1.245 (Claude Code)"


class MaterializerTestCase(unittest.TestCase):
    carrier = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.carrier = production.resolve_carrier_source(repository=REPOSITORY_ROOT)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()
        self.hold_out = self.root / "held-out"
        self.hold_out.mkdir()
        self.counter = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def token(self) -> str:
        self.counter += 1
        return "%032x" % self.counter

    def materialize(
        self,
        condition: str,
        *,
        manifest: Mapping = None,
        carrier: Any = None,
        hold_out_root: Path = None,
    ):
        resolved_manifest = execution_manifest() if manifest is None else manifest
        off_id, on_id = run_ids()
        run_id = off_id if condition == "OFF" else on_id
        slot = runner.select_manifest_slot(resolved_manifest, run_id)
        workspace, opaque = runner.create_condition_neutral_workspace(
            self.workspaces, self.token
        )
        plan = runner.WorkspacePlan(
            slot=slot,
            workspace_path=str(workspace.resolve()),
            opaque_identity=opaque,
        )
        receipt = production.materialize_p01_workspace(
            plan,
            carrier=self.carrier if carrier is None else carrier,
            hold_out_root=self.hold_out if hold_out_root is None else hold_out_root,
        )
        return slot, workspace, receipt


class P01MaterializerTests(MaterializerTestCase):
    def test_01_off_materialization_succeeds(self) -> None:
        slot, workspace, receipt = self.materialize("OFF")
        self.assertEqual(receipt.condition, "OFF")
        self.assertEqual(receipt.initial_git_tree, slot.task_metadata["initial_git_tree"])
        self.assertEqual(receipt.tracked_paths, (".gitignore", "seqtools.py"))
        self.assertTrue((workspace / "seqtools.py").is_file())
        self.assertTrue((workspace / "README.md").is_file())
        self.assertFalse(receipt.carrier_installed)

    def test_02_on_materialization_succeeds(self) -> None:
        slot, workspace, receipt = self.materialize("ON")
        self.assertEqual(receipt.condition, "ON")
        self.assertEqual(receipt.initial_git_tree, slot.task_metadata["initial_git_tree"])
        self.assertTrue(receipt.carrier_installed)
        self.assertGreater(receipt.carrier_blob_count, 0)

    def test_03_off_workspace_has_no_carrier(self) -> None:
        _slot, workspace, _receipt = self.materialize("OFF")
        self.assertFalse((workspace / production.CARRIER_DESTINATION).exists())
        found = [
            name
            for _root, directories, files in os.walk(workspace)
            for name in list(directories) + list(files)
            if name == runner.FABLE_SKILL_IDENTITY
        ]
        self.assertEqual(found, [])

    def test_04_on_workspace_has_exact_pinned_carrier(self) -> None:
        _slot, workspace, receipt = self.materialize("ON")
        listing = plain_git(
            REPOSITORY_ROOT, "ls-tree", "-r", "-z", self.carrier.carrier_subtree
        )
        expected = {}
        for chunk in listing.split(b"\x00"):
            if not chunk:
                continue
            metadata, path = chunk.decode("utf-8").split("\t", 1)
            _mode, kind, oid = metadata.split(" ")
            self.assertEqual(kind, "blob")
            expected[path] = plain_git(REPOSITORY_ROOT, "cat-file", "blob", oid)

        installed = {}
        carrier_root = workspace / production.CARRIER_DESTINATION
        for current, _directories, files in os.walk(carrier_root):
            for name in files:
                entry = Path(current) / name
                installed[str(entry.relative_to(carrier_root))] = entry.read_bytes()

        self.assertEqual(sorted(installed), sorted(expected))
        self.assertEqual(installed, expected)
        self.assertEqual(receipt.carrier_blob_count, len(expected))
        self.assertEqual(
            hashlib.sha256((carrier_root / "SKILL.md").read_bytes()).hexdigest(),
            production.compiler.CANONICAL_FABLE["carrier_skill_md_sha256"],
        )

    def test_05_carrier_byte_or_hash_mismatch_fails_closed(self) -> None:
        tampered_identity = dict(self.carrier.identity)
        tampered_identity["carrier_skill_md_sha256"] = "f" * 64
        with self.assertRaises(production.CarrierAuthorityError):
            production.resolve_carrier_source(
                identity=tampered_identity, repository=REPOSITORY_ROOT
            )

        wrong_hash_source = production.CarrierSource(
            repository=self.carrier.repository,
            head=self.carrier.head,
            tree=self.carrier.tree,
            carrier_subtree=self.carrier.carrier_subtree,
            carrier_skill_md_sha256="e" * 64,
            carrier_subtree_path=self.carrier.carrier_subtree_path,
        )
        with self.assertRaisesRegex(
            production.CarrierAuthorityError, "CARRIER_HASH_MISMATCH"
        ):
            production.read_carrier_blobs(wrong_hash_source)

        manifest = execution_manifest()
        manifest["treatment"]["on"]["carrier"]["identity"]["carrier_subtree"] = "0" * 40
        with self.assertRaisesRegex(
            production.CarrierAuthorityError, "differs from the bound authority"
        ):
            self.materialize("ON", manifest=manifest)

    def test_05b_materializer_reverifies_carrier_bytes_itself(self) -> None:
        """Even a compromised blob reader cannot install unpinned carrier bytes."""

        genuine = production.read_carrier_blobs(self.carrier)
        tampered = tuple(
            blob
            if blob.relative_path != "SKILL.md"
            else production.CarrierBlob(
                relative_path=blob.relative_path,
                mode=blob.mode,
                oid=blob.oid,
                data=blob.data + b"\ntampered\n",
            )
            for blob in genuine
        )
        with mock.patch.object(
            production, "read_carrier_blobs", return_value=tampered
        ):
            with self.assertRaisesRegex(
                production.CarrierAuthorityError, "CARRIER_HASH_MISMATCH"
            ):
                self.materialize("ON")
        # The positive control: the same call path succeeds on genuine bytes.
        _slot, workspace, receipt = self.materialize("ON")
        self.assertEqual(receipt.carrier_blob_count, len(genuine))
        self.assertEqual(
            hashlib.sha256(
                (workspace / production.CARRIER_DESTINATION / "SKILL.md").read_bytes()
            ).hexdigest(),
            self.carrier.carrier_skill_md_sha256,
        )

    def test_06_fixture_hash_mismatch_fails_closed(self) -> None:
        manifest = execution_manifest()
        manifest["tasks"][0]["fixture"]["sha256"] = "d" * 64
        with self.assertRaisesRegex(
            production.MaterializationError, "fixture:declared_sha256_mismatch"
        ):
            self.materialize("OFF", manifest=manifest)

        size_manifest = execution_manifest()
        size_manifest["tasks"][0]["fixture"]["size"] += 1
        with self.assertRaisesRegex(
            production.MaterializationError, "fixture:declared_size_mismatch"
        ):
            self.materialize("OFF", manifest=size_manifest)

    def test_07_oracle_hash_mismatch_fails_closed(self) -> None:
        manifest = execution_manifest()
        manifest["tasks"][0]["oracle"]["sha256"] = "d" * 64
        with self.assertRaisesRegex(
            production.MaterializationError, "oracle:declared_sha256_mismatch"
        ):
            self.materialize("OFF", manifest=manifest)
        self.assertEqual(sorted(self.hold_out.iterdir()), [])

        size_manifest = execution_manifest()
        size_manifest["tasks"][0]["oracle"]["size"] -= 1
        with self.assertRaisesRegex(
            production.MaterializationError, "oracle:declared_size_mismatch"
        ):
            self.materialize("OFF", manifest=size_manifest)
        self.assertEqual(sorted(self.hold_out.iterdir()), [])

    def test_07b_no_failed_materialization_leaves_held_out_residue(self) -> None:
        broken_fixture = execution_manifest()
        broken_fixture["tasks"][0]["fixture"]["sha256"] = "d" * 64
        with self.assertRaises(production.MaterializationError):
            self.materialize("OFF", manifest=broken_fixture)
        self.assertEqual(sorted(self.hold_out.iterdir()), [])

        broken_carrier = execution_manifest()
        broken_carrier["treatment"]["on"]["carrier"]["destination"] = ".claude/elsewhere"
        with self.assertRaises(production.MaterializationError):
            self.materialize("ON", manifest=broken_carrier)
        self.assertEqual(sorted(self.hold_out.iterdir()), [])

        # The positive control: a successful arm does write the held-out oracle.
        _slot, _workspace, receipt = self.materialize("OFF")
        self.assertTrue(Path(receipt.held_out_oracle_path).is_file())

    def test_08_initial_workspace_git_state_satisfies_runner_contract(self) -> None:
        for condition in ("OFF", "ON"):
            with self.subTest(condition=condition):
                slot, workspace, receipt = self.materialize(condition)
                state = runner.capture_git_state(workspace)
                self.assertTrue(state.state_resolved, msg=state.errors)
                self.assertEqual(
                    state.final_head_tree, slot.task_metadata["initial_git_tree"]
                )
                self.assertEqual(state.final_head_tree, receipt.initial_git_tree)
                self.assertNotEqual(state.filesystem_state_representation, "UNRESOLVED")
                self.assertFalse(state.staged_diff_present)
                self.assertFalse(state.unstaged_diff_present)

    def test_09_forbidden_model_tokens_do_not_leak(self) -> None:
        for condition in ("OFF", "ON"):
            with self.subTest(condition=condition):
                slot, workspace, _receipt = self.materialize(condition)
                opaque = workspace.name[len("session-"):]
                invocation = runner.build_model_invocation(
                    slot, workspace, opaque, environment={}
                )
                audit = runner.audit_model_visible_surfaces(
                    invocation, workspace, slot.forbidden_model_tokens
                )
                self.assertTrue(audit.passed, msg=(audit.leaks, audit.errors))
                self.assertIn("ON", slot.forbidden_model_tokens)
                self.assertIn("OFF", slot.forbidden_model_tokens)
                production.assert_no_forbidden_model_tokens(
                    workspace, slot.forbidden_model_tokens
                )

    def test_09b_forbidden_token_detector_is_not_vacuous(self) -> None:
        slot, workspace, _receipt = self.materialize("OFF")
        (workspace / "notes-OFF.txt").write_text("leak\n", encoding="utf-8")
        with self.assertRaisesRegex(
            production.MaterializationError, "forbidden_model_token_leaked"
        ):
            production.assert_no_forbidden_model_tokens(
                workspace, slot.forbidden_model_tokens
            )

    def test_26_oracle_is_held_out_of_the_model_visible_workspace(self) -> None:
        _slot, workspace, receipt = self.materialize("OFF")
        oracle = Path(receipt.held_out_oracle_path)
        self.assertTrue(oracle.is_file())
        with self.assertRaises(ValueError):
            oracle.resolve().relative_to(workspace.resolve())
        workspace_names = [
            name
            for _root, directories, files in os.walk(workspace)
            for name in list(directories) + list(files)
        ]
        self.assertNotIn("A01.py", workspace_names)
        self.assertIn(b"Held-out correctness oracle", oracle.read_bytes())
        manifest = execution_manifest()
        self.assertEqual(
            hashlib.sha256(oracle.read_bytes()).hexdigest(),
            manifest["tasks"][0]["oracle"]["sha256"],
        )

    def test_26b_held_out_root_inside_workspace_is_refused(self) -> None:
        off_id, _on_id = run_ids()
        slot = runner.select_manifest_slot(execution_manifest(), off_id)
        workspace, opaque = runner.create_condition_neutral_workspace(
            self.workspaces, self.token
        )
        plan = runner.WorkspacePlan(
            slot=slot, workspace_path=str(workspace.resolve()), opaque_identity=opaque
        )
        with self.assertRaisesRegex(
            production.MaterializationError, "held-out oracle root must lie outside"
        ):
            production.materialize_p01_workspace(
                plan, carrier=self.carrier, hold_out_root=workspace / "inside"
            )


class P01ReferenceLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.artifact = self.root / "p01_reference_init.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def load(self, record: Mapping = None, *, digest: str = None):
        resolved = reference_record() if record is None else record
        observed = write_reference(self.artifact, resolved)
        return production.load_reference_init(
            self.artifact,
            expected_reference_sha256=observed if digest is None else digest,
        )

    def test_10_missing_reference_artifact_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "unreadable"
        ):
            production.load_reference_init(
                self.artifact, expected_reference_sha256="a" * 64
            )

    def test_11_expected_reference_sha_missing_fails_closed(self) -> None:
        write_reference(self.artifact, reference_record())
        with self.assertRaises(TypeError):
            production.load_reference_init(self.artifact)  # type: ignore[call-arg]
        for candidate in (None, "", "abc", "A" * 64, "g" * 64):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(
                    production.ReferenceArtifactError, "expected_reference_sha256"
                ):
                    production.load_reference_init(
                        self.artifact, expected_reference_sha256=candidate
                    )

    def test_12_wrong_reference_sha_fails_closed(self) -> None:
        record = reference_record()
        write_reference(self.artifact, record)
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "SHA256 mismatch"
        ):
            production.load_reference_init(
                self.artifact, expected_reference_sha256="0" * 64
            )
        # The falsifiability control: only the exact bytes are accepted.
        loaded = self.load(record)
        self.assertEqual(
            loaded.sha256, hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        )
        self.artifact.write_bytes(
            production.canonical_reference_bytes(record)[:-1] + b" \n"
        )
        with self.assertRaises(production.ReferenceArtifactError):
            production.load_reference_init(
                self.artifact, expected_reference_sha256=loaded.sha256
            )

    def test_13_duplicate_json_keys_are_rejected(self) -> None:
        record = reference_record()
        text = production.canonical_reference_bytes(record).decode("utf-8").rstrip("\n")
        duplicated = text[:-1] + ',"schema":"' + production.REFERENCE_INIT_SCHEMA + '"}\n'
        raw = duplicated.encode("utf-8")
        self.artifact.write_bytes(raw)
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "duplicate JSON key"
        ):
            production.load_reference_init(
                self.artifact,
                expected_reference_sha256=hashlib.sha256(raw).hexdigest(),
            )

    def test_14_malformed_init_is_rejected(self) -> None:
        malformed = reference_init_event()
        malformed["subtype"] = "not-init"
        with self.assertRaises(production.ReferenceArtifactError):
            self.load(reference_record(event=malformed))

        not_system = reference_init_event()
        not_system["type"] = "assistant"
        with self.assertRaises(production.ReferenceArtifactError):
            self.load(reference_record(event=not_system))

        record = reference_record()
        record["init_event_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "init event SHA256"
        ):
            self.load(record)

    def test_15_reference_containing_fable_method_is_rejected(self) -> None:
        engaged = reference_init_event(
            skills=("reference-skill", runner.FABLE_SKILL_IDENTITY)
        )
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "already contains fable-method"
        ):
            self.load(reference_record(event=engaged))

    def test_16_missing_required_purity_dimension_is_rejected(self) -> None:
        for dimension in sorted(runner.REQUIRED_PURITY_DIMENSIONS):
            with self.subTest(dimension=dimension):
                event = reference_init_event(drop=(dimension,))
                with self.assertRaises(production.ReferenceArtifactError):
                    self.load(reference_record(event=event))

    def test_17_empty_required_purity_dimension_is_rejected(self) -> None:
        for dimension in sorted(runner.REQUIRED_PURITY_DIMENSIONS):
            with self.subTest(dimension=dimension):
                event = reference_init_event(empty=(dimension,))
                with self.assertRaisesRegex(
                    production.ReferenceArtifactError, "dimension is empty"
                ):
                    self.load(reference_record(event=event))

    def test_18_empty_reference_skill_inventory_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "skill inventory is empty"
        ):
            self.load(reference_record(event=reference_init_event(skills=())))

        without_skills = reference_init_event(drop=("skills",))
        with self.assertRaises(production.ReferenceArtifactError):
            self.load(reference_record(event=without_skills))

    def test_18b_contract_violations_are_rejected(self) -> None:
        cases = {
            "schema": "OTHER/v1",
            "capture_mode": "ONE_TURN",
            "pair_id": "P02",
            "task_id": "B01",
            "epoch_id": "0" * 32,
            "sealed_manifest_sha256": "0" * 64,
            "observed_total_cost_usd": "0.0000001",
            "observed_model_turn_count": 1,
            "observed_assistant_message_count": 1,
        }
        for key, value in cases.items():
            with self.subTest(field=key):
                record = reference_record()
                record[key] = value
                with self.assertRaises(production.ReferenceArtifactError):
                    self.load(record)

        extra = reference_record()
        extra["treatment_outcome"] = {"disposition": "SUCCESS"}
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "field set is invalid"
        ):
            self.load(extra)

    def configuration(self, **overrides: Any) -> dict:
        base = copy.deepcopy(reference_record()["capture_configuration"])
        base.update(overrides)
        return base

    def test_18c_argv_omits_budget_but_record_claims_a_value_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "max_budget_usd does not match argv"
        ):
            self.load(
                reference_record(
                    capture_configuration=self.configuration(max_budget_usd="0.0000000")
                )
            )

    def test_18d_argv_omits_budget_but_record_claims_presence_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            production.ReferenceArtifactError,
            "budget ceiling presence does not match argv",
        ):
            self.load(
                reference_record(
                    capture_configuration=self.configuration(budget_ceiling_present=True)
                )
            )

    def test_18e_argv_with_a_present_budget_ceiling_is_still_rejected(self) -> None:
        # Self-consistent with argv is not enough: the reference contract
        # requires the physical ceiling to be omitted outright.
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "must omit the physical budget ceiling"
        ):
            self.load(
                reference_record(
                    capture_configuration=self.configuration(
                        argv=[
                            "/offline/claude",
                            "--max-budget-usd",
                            "0.0000000",
                            "--max-turns",
                            "0",
                        ],
                        budget_ceiling_present=True,
                        max_budget_usd="0.0000000",
                    )
                )
            )

    def test_18f_logical_reference_budget_mismatch_is_rejected(self) -> None:
        for bad in ("0.0000001", "1.0000000", "", None, 0):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(
                    production.ReferenceArtifactError,
                    "logical budget is not exactly 0.0000000",
                ):
                    self.load(
                        reference_record(
                            capture_configuration=self.configuration(
                                logical_reference_budget_usd=bad
                            )
                        )
                    )

    def test_18g_max_turns_mismatched_with_argv_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "max_turns does not match argv"
        ):
            self.load(
                reference_record(
                    capture_configuration=self.configuration(
                        argv=["/offline/claude", "--max-turns", "5"], max_turns=0
                    )
                )
            )

    def test_18h_max_turns_consistent_but_nonzero_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "max_turns is not 0"
        ):
            self.load(
                reference_record(
                    capture_configuration=self.configuration(
                        argv=["/offline/claude", "--max-turns", "5"], max_turns=5
                    )
                )
            )

    def test_18i_max_turns_boolean_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            production.ReferenceArtifactError, "max_turns does not match argv"
        ):
            self.load(
                reference_record(capture_configuration=self.configuration(max_turns=False))
            )

    def test_18j_valid_omitted_budget_configuration_is_accepted(self) -> None:
        loaded = self.load(
            reference_record(capture_configuration=self.configuration())
        )
        configuration = loaded.record["capture_configuration"]
        self.assertIs(configuration["budget_ceiling_present"], False)
        self.assertIsNone(configuration["max_budget_usd"])
        self.assertEqual(configuration["logical_reference_budget_usd"], "0.0000000")
        self.assertEqual(configuration["max_turns"], 0)

    def test_18k_api_key_source_none_is_rejected_without_requiring_field(self) -> None:
        none_event = reference_init_event()
        none_event["apiKeySource"] = "none"
        with self.assertRaisesRegex(
            production.ReferenceArtifactError,
            "reference init event is not a capture: apiKeySource is none",
        ):
            production.validate_reference_record(reference_record(event=none_event))

        absent = reference_record()
        self.assertNotIn("apiKeySource", absent["init_event"])
        self.assertIs(production.validate_reference_record(absent), absent)

        non_none_event = reference_init_event()
        non_none_event["apiKeySource"] = "ANTHROPIC_API_KEY"
        non_none = reference_record(event=non_none_event)
        self.assertIs(production.validate_reference_record(non_none), non_none)

    def test_21_on_delta_is_reference_plus_exactly_fable_method(self) -> None:
        loaded = self.load()
        delta = production.treatment_delta_expectation(loaded)
        self.assertEqual(
            delta.on_expected_skill_identities,
            delta.reference_skill_identities | {runner.FABLE_SKILL_IDENTITY},
        )
        self.assertEqual(
            delta.evidence_class_for_condition("ON"),
            "LOAD_BEARING_NON_VACUOUS_TREATMENT_PURITY_EVIDENCE",
        )
        passing = runner.SurfaceAudit(passed=True)
        engaged = reference_init_event(
            skills=tuple(sorted(delta.on_expected_skill_identities))
        )
        purity = runner.evaluate_purity(
            [engaged],
            loaded.events,
            expected_fable_engaged=True,
            frozen_dimensions=sorted(runner.REQUIRED_PURITY_DIMENSIONS),
            surface_audit=passing,
        )
        self.assertTrue(purity.purity_pass, msg=purity.reasons)

        # Non-vacuity: an ON run whose inventory did not gain fable-method,
        # and an ON run that gained something else, both fail.
        vacuous = runner.evaluate_purity(
            [reference_init_event()],
            loaded.events,
            expected_fable_engaged=True,
            frozen_dimensions=sorted(runner.REQUIRED_PURITY_DIMENSIONS),
            surface_audit=passing,
        )
        self.assertFalse(vacuous.purity_pass)
        extra = runner.evaluate_purity(
            [
                reference_init_event(
                    skills=tuple(sorted(delta.on_expected_skill_identities))
                    + ("unexpected-skill",)
                )
            ],
            loaded.events,
            expected_fable_engaged=True,
            frozen_dimensions=sorted(runner.REQUIRED_PURITY_DIMENSIONS),
            surface_audit=passing,
        )
        self.assertFalse(extra.purity_pass)

    def test_22_off_equality_is_preserved_and_truthfully_classified(self) -> None:
        loaded = self.load()
        delta = production.treatment_delta_expectation(loaded)
        self.assertEqual(
            delta.off_expected_skill_identities, delta.reference_skill_identities
        )
        self.assertEqual(
            delta.evidence_class_for_condition("OFF"),
            "CONFIGURATION_AND_MATERIALIZER_STABILITY_EVIDENCE",
        )
        self.assertNotEqual(
            delta.evidence_class_for_condition("OFF"),
            delta.evidence_class_for_condition("ON"),
        )
        passing = runner.SurfaceAudit(passed=True)
        equal = runner.evaluate_purity(
            [reference_init_event()],
            loaded.events,
            expected_fable_engaged=False,
            frozen_dimensions=sorted(runner.REQUIRED_PURITY_DIMENSIONS),
            surface_audit=passing,
        )
        self.assertTrue(equal.purity_pass, msg=equal.reasons)
        drifted = runner.evaluate_purity(
            [reference_init_event(tools=("Bash",))],
            loaded.events,
            expected_fable_engaged=False,
            frozen_dimensions=sorted(runner.REQUIRED_PURITY_DIMENSIONS),
            surface_audit=passing,
        )
        self.assertFalse(drifted.purity_pass)


class P01DependencyAndEntrypointTests(MaterializerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.artifact = self.root / "p01_reference_init.json"
        self.reference_sha256 = write_reference(self.artifact, reference_record())
        self.authorization = self.root / "paid-authorization.json"
        self.authorization.write_bytes(
            json.dumps(paid_authorization_record(), sort_keys=True).encode("utf-8")
        )
        self.epoch_paths = driver.EpochPaths(
            epoch_root=self.root / "epoch",
            manifest_path=self.root / "manifest.json",
            owner_signoff_path=self.root / "owner-signoff.json",
            owner_signoff_decision_path=self.root / "owner-signoff-decision.txt",
            lock_path=self.root / "epoch.lock",
        )

    def config(self, **overrides: Any) -> Any:
        values = {
            "expected_reference_sha256": self.reference_sha256,
            "paid_authorization_path": self.authorization,
            "hold_out_root": self.hold_out,
            "provider_binary_path": "/offline/claude",
            "expected_provider_sha256": "c" * 64,
            "expected_provider_version": "2.1.245 (Claude Code)",
            "carrier_repository": REPOSITORY_ROOT,
            "reference_artifact_path": self.artifact,
            "epoch_paths": self.epoch_paths,
        }
        values.update(overrides)
        return production.ProductionRunConfig(**values)

    def loaded_reference(self):
        return production.load_reference_init(
            self.artifact, expected_reference_sha256=self.reference_sha256
        )

    def test_19_valid_fake_reference_builds_pair_runtime_dependencies(self) -> None:
        dependencies = production.build_p01_dependencies(
            reference=self.loaded_reference(),
            carrier=self.carrier,
            hold_out_root=self.hold_out,
            provider_binary=runner.ProviderBinaryIdentity(
                executable="/offline/claude",
                realpath="/offline/claude",
                sha256="c" * 64,
                version="2.1.245 (Claude Code)",
            ),
        )
        self.assertIsInstance(dependencies, driver.PairRuntimeDependencies)
        self.assertTrue(callable(dependencies.materializer))
        self.assertEqual(len(dependencies.reference_events), 1)
        self.assertEqual(dependencies.reference_events[0]["subtype"], "init")
        self.assertIsInstance(dependencies.policy, claude.ClaudeProviderPolicy)
        # Every unspecified boundary keeps the verified runtime default.
        self.assertIs(dependencies.run_slot_executor, runner.execute_manifest_slot)
        self.assertIs(
            dependencies.composition_executor,
            production.driver.composition.execute_reserved_provider_slot,
        )
        self.assertIs(dependencies.lock_factory, driver.controller.EpochLock)
        with self.assertRaises(production.ProductionAuthorityError):
            production.build_p01_dependencies(
                reference=self.loaded_reference(),
                carrier=self.carrier,
                hold_out_root=self.hold_out,
                provider_binary=None,
                policy=object(),
                materializer=lambda plan: None,
            )

    def test_20_existing_make_cto_policy_is_used_not_duplicated(self) -> None:
        identity = runner.ProviderBinaryIdentity(
            executable="/offline/claude",
            realpath="/offline/claude",
            sha256="c" * 64,
            version="2.1.245 (Claude Code)",
        )
        sentinel = object()
        with mock.patch.object(
            production.claude, "make_cto_policy", return_value=sentinel
        ) as maker:
            dependencies = production.build_p01_dependencies(
                reference=self.loaded_reference(),
                carrier=self.carrier,
                hold_out_root=self.hold_out,
                provider_binary=identity,
            )
        maker.assert_called_once_with(identity)
        self.assertIs(dependencies.policy, sentinel)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("ClaudeProviderPolicy(", source)
        self.assertNotIn("build_cto_argv", source)

    def test_23_sanctioned_production_caller_reaches_execute_pair(self) -> None:
        aggregate = {"schema": driver.PAIR_AGGREGATE_SCHEMA}
        with mock.patch.object(
            production.driver, "execute_pair", return_value=aggregate
        ) as execute:
            observed = production.run_p01_production(
                self.config(),
                provider_identity_resolver=FakeProviderIdentity,
            )
        self.assertIs(observed, aggregate)
        execute.assert_called_once()
        call = execute.call_args
        self.assertIs(call.args[0], self.epoch_paths)
        self.assertEqual(call.kwargs["paid_authorization"], paid_authorization_record())
        self.assertEqual(call.kwargs["expected_manifest_sha256"], SEALED_SHA256)
        dependencies = call.kwargs["dependencies"]
        self.assertIsInstance(dependencies, driver.PairRuntimeDependencies)
        self.assertEqual(len(dependencies.reference_events), 1)
        self.assertIsInstance(dependencies.policy, claude.ClaudeProviderPolicy)

    def test_23b_injected_boundaries_reach_a_real_arm_materialization(self) -> None:
        """The injected materializer really builds both arms end to end."""

        with mock.patch.object(production.driver, "execute_pair") as execute:
            production.run_p01_production(
                self.config(), provider_identity_resolver=FakeProviderIdentity
            )
        materializer = execute.call_args.kwargs["dependencies"].materializer
        manifest = execution_manifest()
        for run_id, condition in zip(run_ids(), ("OFF", "ON")):
            slot = runner.select_manifest_slot(manifest, run_id)
            workspace, opaque = runner.create_condition_neutral_workspace(
                self.workspaces, self.token
            )
            materializer(
                runner.WorkspacePlan(
                    slot=slot,
                    workspace_path=str(workspace.resolve()),
                    opaque_identity=opaque,
                )
            )
            carrier_present = (workspace / production.CARRIER_DESTINATION).is_dir()
            self.assertEqual(carrier_present, condition == "ON")
            self.assertTrue(runner.capture_git_state(workspace).state_resolved)

    def test_24_missing_paid_authority_refuses_before_execute_pair(self) -> None:
        self.authorization.unlink()
        with mock.patch.object(production.driver, "execute_pair") as execute:
            with self.assertRaises(production.ProductionAuthorityError):
                production.run_p01_production(
                    self.config(), provider_identity_resolver=FakeProviderIdentity
                )
        execute.assert_not_called()

        for mutation in ({"decision": "NOT_AUTHORIZED"}, {"schema": "OTHER/v1"}):
            with self.subTest(mutation=mutation):
                record = paid_authorization_record()
                record.update(mutation)
                self.authorization.write_bytes(
                    json.dumps(record, sort_keys=True).encode("utf-8")
                )
                with mock.patch.object(production.driver, "execute_pair") as execute:
                    with self.assertRaises(production.ProductionAuthorityError):
                        production.run_p01_production(
                            self.config(),
                            provider_identity_resolver=FakeProviderIdentity,
                        )
                execute.assert_not_called()

    def test_24b_every_reference_failure_refuses_before_execute_pair(self) -> None:
        cases = {
            "absent_artifact": lambda: self.config(
                reference_artifact_path=self.root / "missing.json"
            ),
            "wrong_expected_sha": lambda: self.config(
                expected_reference_sha256="0" * 64
            ),
            "provider_pin_mismatch": lambda: self.config(
                expected_provider_sha256="9" * 64
            ),
            "absent_hold_out_root": lambda: self.config(
                hold_out_root=self.root / "no-such-root"
            ),
        }
        for name, factory in cases.items():
            with self.subTest(case=name):
                with mock.patch.object(production.driver, "execute_pair") as execute:
                    with self.assertRaises(production.ProductionWiringError):
                        production.run_p01_production(
                            factory(), provider_identity_resolver=FakeProviderIdentity
                        )
                execute.assert_not_called()

    def test_24c_reference_with_fable_method_refuses_before_execute_pair(self) -> None:
        engaged = reference_init_event(
            skills=("reference-skill", runner.FABLE_SKILL_IDENTITY)
        )
        digest = write_reference(self.artifact, reference_record(event=engaged))
        with mock.patch.object(production.driver, "execute_pair") as execute:
            with self.assertRaises(production.ReferenceArtifactError):
                production.run_p01_production(
                    self.config(expected_reference_sha256=digest),
                    provider_identity_resolver=FakeProviderIdentity,
                )
        execute.assert_not_called()

    def test_24d_missing_purity_dimension_refuses_before_execute_pair(self) -> None:
        for dimension in sorted(runner.REQUIRED_PURITY_DIMENSIONS):
            with self.subTest(dimension=dimension):
                digest = write_reference(
                    self.artifact,
                    reference_record(event=reference_init_event(drop=(dimension,))),
                )
                with mock.patch.object(production.driver, "execute_pair") as execute:
                    with self.assertRaises(production.ReferenceArtifactError):
                        production.run_p01_production(
                            self.config(expected_reference_sha256=digest),
                            provider_identity_resolver=FakeProviderIdentity,
                        )
                execute.assert_not_called()

    def test_24e_main_refuses_and_reports_without_executing(self) -> None:
        with mock.patch.object(production.driver, "execute_pair") as execute:
            code = production.main(
                [
                    "--expected-reference-sha256",
                    "0" * 64,
                    "--paid-authorization",
                    str(self.authorization),
                    "--hold-out-root",
                    str(self.hold_out),
                    "--provider-binary",
                    "/offline/claude",
                    "--expected-provider-sha256",
                    "c" * 64,
                    "--expected-provider-version",
                    "2.1.245 (Claude Code)",
                    "--reference-artifact",
                    str(self.artifact),
                ]
            )
        self.assertEqual(code, 2)
        execute.assert_not_called()


class P01SourceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = MODULE_PATH.read_text(encoding="utf-8")

    def test_25_no_direct_provider_or_subprocess_bypass(self) -> None:
        for forbidden in (
            "Popen",
            "os.execv",
            "os.posix_spawn",
            "os.system",
            "sandbox-exec",
            "build_sandboxed_command",
            "spawn_sandboxed_provider",
            "start_reserved_provider",
            "execute_reserved_provider_slot",
            "execute_claude",
            "/Users/kelvin/.local/bin/claude",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)
        self.assertEqual(
            sorted(set(re.findall(r"subprocess\.\w+", self.source))),
            ["subprocess.PIPE", "subprocess.run"],
        )
        commands = re.findall(r'command = \[([^\]]*)\]', self.source)
        self.assertEqual(len(commands), 1)
        self.assertTrue(commands[0].strip().startswith('"git"'))

    def test_27_execute_pair_has_exactly_one_non_test_caller(self) -> None:
        callers = []
        for path in sorted(SOURCE_DIRECTORY.glob("*.py")):
            if path.name.startswith("test_") or path.name == "epoch_pair_driver.py":
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if re.search(r"(?<![\w.])(?:\w+\.)?execute_pair\s*\(", line):
                    callers.append(f"{path.name}:{number}")
        self.assertEqual(len(callers), 1, msg=callers)
        self.assertTrue(callers[0].startswith("p01_production.py:"), msg=callers)

    def test_28_reference_artifact_is_not_created_by_this_module(self) -> None:
        self.assertFalse(production.REFERENCE_ARTIFACT_PATH.exists())
        self.assertEqual(
            production.REFERENCE_ARTIFACT_PATH,
            SOURCE_DIRECTORY / "p01_reference_init.json",
        )

    def test_29_canonical_epoch_root_and_lock_remain_absent(self) -> None:
        self.assertFalse(driver.CANONICAL_AUTHORITY_ROOT.exists())
        self.assertFalse(driver.CANONICAL_EPOCH_ROOT.exists())
        self.assertFalse(driver.controller.PRODUCTION_LOCK_PATH.exists())

    def test_30_module_constructs_no_lock_ledger_or_reservation(self) -> None:
        for forbidden in (
            "EpochLock(",
            "BudgetLedger(",
            "provider_start_intent",
            "ensure_pair_reservations(",
            "write_pair_aggregate(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
