#!/usr/bin/env python3
"""Offline acceptance tests for the prospective epoch manifest compiler."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
MODULE_PATH = Path(__file__).with_name("build_epoch_manifest.py")
SPEC = importlib.util.spec_from_file_location("fable_epoch_manifest", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {MODULE_PATH}")
compiler = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compiler
SPEC.loader.exec_module(compiler)


EPOCH_ID = compiler.CTO_PROSPECTIVE_EPOCH_ID


def sha256(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def git_oid(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def artifact(label: str) -> dict[str, str]:
    return {"blob_oid": git_oid(label), "sha256": sha256(label)}


def binary_identity(label: str) -> dict[str, str]:
    return {
        "binary_path": f"/opt/fable-test/{label}",
        "sha256": sha256(label),
        "version": f"{label} 1.0",
    }


def authorization_source(label: str) -> dict[str, str]:
    return {
        "kind": "OFFLINE_TEST_OWNER_RECORD",
        "identifier": label,
        "sha256": sha256(label),
    }


def complete_inputs() -> dict[str, Any]:
    task_pins = {
        task_id: {
            "prompt_sha256": sha256(f"{task_id}:prompt"),
            "fixture_sha256": sha256(f"{task_id}:fixture"),
            "oracle_sha256": sha256(f"{task_id}:oracle"),
        }
        for task_id in compiler.TASK_IDS
    }
    return {
        "final_runtime_authority": {
            "commit": git_oid("final-runtime-commit"),
            "tree": git_oid("final-runtime-tree"),
            "runner": artifact("final-runner"),
            "controller": artifact("final-controller"),
            "sandbox": artifact("final-sandbox"),
        },
        "provider_adapter": artifact("provider-adapter"),
        "provider_runtime": binary_identity("provider"),
        "task_pins": task_pins,
        "runtime_containment": {
            "policy_id": "fable-prospective-epoch-containment-v1",
            "policy_sha256": sha256("containment-policy"),
            "epoch_write_scope": "PROSPECTIVE_EPOCH_ONLY",
            "network_policy": "PROVIDER_ADAPTER_ONLY",
            "provider_subprocess_required": True,
        },
        "lock_tool_identities": {
            "lockf": binary_identity("lockf"),
            "lsof": binary_identity("lsof"),
            "sandbox-exec": binary_identity("sandbox-exec"),
        },
        "evidence_schema": {
            "schema": "FABLE_ABLATION_RUN_EVIDENCE/v1",
            "sha256": sha256("evidence-schema"),
        },
        "s04_budget_attestation": {
            "schema": compiler.S04_ATTESTATION_SCHEMA,
            "decision": "AUTHORIZE_UNKNOWN_HISTORICAL_COST_HANDLING",
            "epoch_id": EPOCH_ID,
            "run_id": compiler.S04_RUN_ID,
            "cost_state": compiler.S04_COST_STATE,
            "historical_charged_spend_usd": "0.2660758",
            "lifetime_hard_cap_usd": compiler.LIFETIME_HARD_CAP_USD,
            "unknown_cost_treatment": (
                "OWNER_ACCEPTS_RECORDED_TOTAL_AS_LIFETIME_CHARGE"
            ),
            "authorization_source_identity": authorization_source(
                "synthetic-s04-attestation"
            ),
        },
        "initial_state": {
            "run_count": 0,
            "workspace_count": 0,
            "reservation_count": 0,
            "aggregate_count": 0,
        },
    }


def signable_manifest() -> dict[str, Any]:
    return compiler.build_manifest(EPOCH_ID, **complete_inputs())


def owner_signoff(manifest_bytes: bytes, epoch_id: str = EPOCH_ID) -> dict[str, Any]:
    manifest = compiler.validate_manifest_bytes(manifest_bytes)
    return {
        "schema": compiler.OWNER_SIGNOFF_SCHEMA,
        "decision": "AUTHORIZE",
        "epoch_id": epoch_id,
        "manifest_sha256": compiler.manifest_sha256(manifest_bytes),
        "lifetime_hard_cap_usd": manifest["budget"]["lifetime_hard_cap_usd"],
        "historical_spend_usd": manifest["budget"]["historical_charged_spend_usd"],
        "max_additional_spend_usd": manifest["budget"]["max_additional_spend_usd"],
        "authorization_source_identity": authorization_source(
            "synthetic-owner-signoff"
        ),
    }


class ProspectiveEpochManifestTests(unittest.TestCase):
    def test_01_deterministic_serialization_is_byte_identical(self) -> None:
        first = compiler.serialize_manifest(signable_manifest())
        second = compiler.serialize_manifest(signable_manifest())
        self.assertEqual(first, second)
        self.assertEqual(compiler.manifest_sha256(first), compiler.manifest_sha256(second))

    def test_02_manifest_hash_includes_exact_final_lf(self) -> None:
        data = compiler.serialize_manifest(signable_manifest())
        self.assertTrue(data.endswith(b"\n"))
        self.assertFalse(data.endswith(b"\n\n"))
        self.assertEqual(compiler.manifest_sha256(data), hashlib.sha256(data).hexdigest())
        self.assertNotEqual(
            compiler.manifest_sha256(data), hashlib.sha256(data[:-1]).hexdigest()
        )
        with self.assertRaises(compiler.ManifestValidationError):
            compiler.validate_manifest_bytes(data[:-1])

    def test_03_owner_sidecar_is_excluded_from_manifest_hash(self) -> None:
        manifest = signable_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            digest = compiler.write_manifest(manifest_path, manifest)
            before = compiler.manifest_sha256(manifest_path.read_bytes())
            sidecar_path = root / "owner-signoff.json"
            sidecar_path.write_text(
                json.dumps(owner_signoff(manifest_path.read_bytes()), sort_keys=True),
                encoding="utf-8",
            )
            after = compiler.manifest_sha256(manifest_path.read_bytes())
            self.assertEqual(digest, before)
            self.assertEqual(before, after)
            sidecar_path.write_text("{}\n", encoding="utf-8")
            self.assertEqual(before, compiler.manifest_sha256(manifest_path.read_bytes()))

    def test_04_malformed_epoch_ids_are_rejected(self) -> None:
        invalid = (
            "56a62a62e5aae9fe3a230cbbed831a2",
            "56a62a62e5aae9fe3a230cbbed831a2aa",
            "56A62A62E5AAE9FE3A230CBBED831A2A",
            "56a62a62-e5aa-e9fe-3a23-0cbbed831a2a",
            "g6a62a62e5aae9fe3a230cbbed831a2a",
        )
        for epoch_id in invalid:
            with self.subTest(epoch_id=epoch_id):
                with self.assertRaises(compiler.ManifestValidationError):
                    compiler.build_manifest(epoch_id)

    def test_05_schedule_and_stratum_c_are_exact(self) -> None:
        manifest = compiler.build_manifest(EPOCH_ID)
        self.assertEqual(
            manifest["schedule"]["pairs"],
            [
                {"pair_id": "P01", "task_id": "A01", "arm_order": ["OFF", "ON"]},
                {"pair_id": "P02", "task_id": "B01", "arm_order": ["ON", "OFF"]},
                {"pair_id": "P03", "task_id": "A02", "arm_order": ["ON", "OFF"]},
                {"pair_id": "P04", "task_id": "B02", "arm_order": ["OFF", "ON"]},
                {"pair_id": "P05", "task_id": "A03", "arm_order": ["OFF", "ON"]},
                {"pair_id": "P06", "task_id": "B03", "arm_order": ["ON", "OFF"]},
                {"pair_id": "P07", "task_id": "A04", "arm_order": ["ON", "OFF"]},
                {"pair_id": "P08", "task_id": "B04", "arm_order": ["OFF", "ON"]},
            ],
        )
        self.assertEqual(manifest["schedule"]["stratum_c"], "NOT_EVALUATED")

    def test_06_task_pins_and_fixture_oracle_index_are_exact(self) -> None:
        inputs = complete_inputs()
        manifest = compiler.build_manifest(EPOCH_ID, **inputs)
        self.assertEqual([task["task_id"] for task in manifest["tasks"]], list(compiler.TASK_IDS))
        for task, index_entry in zip(
            manifest["tasks"], manifest["fixture_oracle_index"]["entries"]
        ):
            supplied = inputs["task_pins"][task["task_id"]]
            self.assertEqual(task["prompt_sha256"], supplied["prompt_sha256"])
            self.assertEqual(task["fixture_sha256"], supplied["fixture_sha256"])
            self.assertEqual(task["oracle_sha256"], supplied["oracle_sha256"])
            self.assertEqual(index_entry["fixture_sha256"], task["fixture_sha256"])
            self.assertEqual(index_entry["oracle_sha256"], task["oracle_sha256"])

    def test_07_budget_uses_exact_decimal_strings_without_float(self) -> None:
        manifest = signable_manifest()
        budget = manifest["budget"]
        self.assertEqual(budget["nominal_max_additional_usd"], "9.7339242")
        self.assertEqual(budget["max_additional_spend_usd"], "9.7339242")
        self.assertEqual(budget["pair_reservation_invariant_lhs_usd"], "4.2660758")
        self.assertLessEqual(
            Decimal(budget["pair_reservation_invariant_lhs_usd"]),
            Decimal(budget["lifetime_hard_cap_usd"]),
        )
        inputs = complete_inputs()
        inputs["s04_budget_attestation"]["historical_charged_spend_usd"] = 0.2660758
        with self.assertRaises(compiler.ManifestValidationError):
            compiler.build_manifest(EPOCH_ID, **inputs)

    def test_08_lifetime_cap_never_resets_across_epochs(self) -> None:
        first = compiler.build_manifest(EPOCH_ID)
        second = compiler.build_manifest("0123456789abcdef0123456789abcdef")
        for manifest in (first, second):
            self.assertFalse(manifest["budget"]["cap_reset"])
            self.assertFalse(manifest["authority_lineage"]["cap_reset"])
            self.assertTrue(
                manifest["authority_lineage"]["lifetime_cap_carries_across_epochs"]
            )
        mutated = copy.deepcopy(first)
        mutated["budget"]["cap_reset"] = True
        with self.assertRaises(compiler.ManifestValidationError):
            compiler.validate_manifest(mutated)

    def test_09_historical_spend_is_retained_when_runs_are_noncountable(self) -> None:
        manifest = compiler.build_manifest(EPOCH_ID)
        self.assertFalse(manifest["historical_quarantine"]["R1"]["countable"])
        self.assertFalse(manifest["historical_quarantine"]["R2"]["countable"])
        self.assertFalse(manifest["claim_boundary"]["historical_runs_countable"])
        self.assertTrue(
            manifest["claim_boundary"]["historical_spend_retained_in_lifetime_ledger"]
        )
        self.assertEqual(
            manifest["budget"]["historical_forensic_spend_usd"], "0.2660758"
        )

    def test_10_s04_unknown_cost_is_never_zero(self) -> None:
        draft = compiler.build_manifest(EPOCH_ID)
        unknown = draft["budget"]["historical_unknown_cost"]
        self.assertEqual(unknown["run_id"], "s04-B01-r0-OFF")
        self.assertEqual(unknown["cost_state"], "UNKNOWN_PERMANENTLY")
        self.assertFalse(unknown["treated_as_zero"])
        self.assertIsNone(draft["budget"]["historical_charged_spend_usd"])
        self.assertEqual(
            draft["budget"]["budget_authority_status"],
            "BLOCKED_UNKNOWN_HISTORICAL_COST",
        )
        inputs = complete_inputs()
        inputs["s04_budget_attestation"]["unknown_cost_treatment"] = "TREAT_AS_ZERO"
        with self.assertRaises(compiler.ManifestValidationError):
            compiler.build_manifest(EPOCH_ID, **inputs)

    def test_11_missing_s04_attestation_blocks_signability(self) -> None:
        inputs = complete_inputs()
        inputs["s04_budget_attestation"] = None
        manifest = compiler.build_manifest(EPOCH_ID, **inputs)
        self.assertEqual(manifest["status"], "DRAFT_UNSIGNABLE")
        gates = manifest["publication_gate"]["gates"]
        self.assertFalse(gates["lifetime_budget_authority"]["passed"])
        self.assertFalse(gates["s04_budget_attestation"]["passed"])
        self.assertIn(
            "BLOCKED_UNKNOWN_HISTORICAL_COST",
            manifest["publication_gate"]["blockers"],
        )

    def test_12_missing_final_runtime_identity_blocks_signability(self) -> None:
        inputs = complete_inputs()
        inputs["final_runtime_authority"] = None
        manifest = compiler.build_manifest(EPOCH_ID, **inputs)
        self.assertEqual(manifest["status"], "DRAFT_UNSIGNABLE")
        self.assertFalse(
            manifest["publication_gate"]["gates"]["final_runtime_identity"]["passed"]
        )
        self.assertEqual(
            manifest["final_runtime_authority"]["status"], "UNKNOWN_REQUIRED_INPUT"
        )

    def test_13_missing_provider_adapter_identity_blocks_signability(self) -> None:
        inputs = complete_inputs()
        inputs["provider_adapter"] = None
        manifest = compiler.build_manifest(EPOCH_ID, **inputs)
        self.assertEqual(manifest["status"], "DRAFT_UNSIGNABLE")
        self.assertFalse(
            manifest["publication_gate"]["gates"]["provider_adapter_identity"][
                "passed"
            ]
        )
        self.assertIn(
            "MISSING_PROVIDER_ADAPTER_IDENTITY",
            manifest["publication_gate"]["blockers"],
        )

    def test_14_mutated_manifest_invalidates_existing_signoff(self) -> None:
        original = signable_manifest()
        original_bytes = compiler.serialize_manifest(original)
        signoff = owner_signoff(original_bytes)
        mutated = copy.deepcopy(original)
        mutated["provider_runtime"]["version"] = "provider 1.0+mutated"
        mutated_bytes = compiler.serialize_manifest(mutated)
        self.assertNotEqual(original_bytes, mutated_bytes)
        with self.assertRaises(compiler.OwnerSignoffValidationError):
            compiler.validate_owner_signoff(mutated_bytes, signoff)

    def test_15_owner_signoff_wrong_hash_is_rejected(self) -> None:
        data = compiler.serialize_manifest(signable_manifest())
        signoff = owner_signoff(data)
        signoff["manifest_sha256"] = "0" * 64
        with self.assertRaises(compiler.OwnerSignoffValidationError):
            compiler.validate_owner_signoff(data, signoff)

    def test_16_owner_signoff_wrong_epoch_is_rejected(self) -> None:
        data = compiler.serialize_manifest(signable_manifest())
        signoff = owner_signoff(data)
        signoff["epoch_id"] = "0123456789abcdef0123456789abcdef"
        with self.assertRaises(compiler.OwnerSignoffValidationError):
            compiler.validate_owner_signoff(data, signoff)

    def test_17_historical_r1_r2_content_never_enters_new_namespace(self) -> None:
        manifest = compiler.build_manifest(EPOCH_ID)
        namespace = manifest["epoch_namespace"]
        for name, path in namespace.items():
            if name == "materialized_by_compiler":
                continue
            self.assertTrue(path.startswith(compiler.AUTHORITY_ROOT))
            self.assertNotIn("fable-ablation-r1", path)
            self.assertNotIn("fable-ablation-r2", path)
        initial = manifest["lifecycle"]["initial_state"]
        self.assertFalse(initial["historical_content_copied"])
        self.assertFalse(manifest["historical_quarantine"]["R1"]["content_copied"])
        self.assertFalse(manifest["historical_quarantine"]["R2"]["content_copied"])

    def test_18_final_signable_status_requires_every_gate(self) -> None:
        complete = signable_manifest()
        self.assertEqual(
            complete["status"], "SEALED_REQUIRES_EXTERNAL_OWNER_SIGNOFF"
        )
        self.assertEqual(complete["publication_gate"]["status"], "PASS")
        self.assertTrue(
            all(
                gate["passed"]
                for gate in complete["publication_gate"]["gates"].values()
            )
        )
        self.assertEqual(
            complete["owner_signoff_gate"]["status"], "REQUIRED_EXTERNAL_SIDECAR"
        )

        missing_cases = (
            "final_runtime_authority",
            "provider_adapter",
            "provider_runtime",
            "task_pins",
            "runtime_containment",
            "lock_tool_identities",
            "evidence_schema",
            "s04_budget_attestation",
            "initial_state",
        )
        for missing in missing_cases:
            with self.subTest(missing=missing):
                inputs = complete_inputs()
                inputs[missing] = None
                draft = compiler.build_manifest(EPOCH_ID, **inputs)
                self.assertEqual(draft["status"], "DRAFT_UNSIGNABLE")
                self.assertEqual(draft["publication_gate"]["status"], "BLOCKED")

    def test_19_pair_reservation_is_atomic_and_cap_checked(self) -> None:
        manifest = signable_manifest()
        budget = manifest["budget"]
        self.assertEqual(budget["per_invocation_cap_usd"], "2.0000000")
        self.assertEqual(budget["pair_reservation_usd"], "4.0000000")
        self.assertTrue(budget["reservation_precedes_either_arm"])
        self.assertEqual(budget["pair_reservation_capacity"], "AVAILABLE")

        inputs = complete_inputs()
        inputs["s04_budget_attestation"]["historical_charged_spend_usd"] = (
            "6.0000001"
        )
        blocked = compiler.build_manifest(EPOCH_ID, **inputs)
        self.assertEqual(blocked["budget"]["pair_reservation_capacity"], "EXCEEDED")
        self.assertEqual(blocked["status"], "DRAFT_UNSIGNABLE")

    def test_20_started_unknown_and_budget_exhausted_are_distinct(self) -> None:
        manifest = compiler.build_manifest(EPOCH_ID)
        started = manifest["budget"]["unresolved_started_arm"]
        not_run = manifest["budget"]["not_run_budget_exhausted"]
        self.assertEqual(started["conservative_debit_usd"], "2.0000000")
        self.assertTrue(started["applies_only_after_arm_started"])
        self.assertTrue(
            manifest["lifecycle"]["run_classification_semantics"][
                "EXECUTED_COST_UNRESOLVED"
            ]["executed"]
        )
        self.assertFalse(not_run["executed"])
        self.assertEqual(not_run["debit_usd"], "0.0000000")
        self.assertFalse(
            manifest["lifecycle"]["run_classification_semantics"][
                "NOT_RUN_BUDGET_EXHAUSTED"
            ]["executed"]
        )

    def test_21_noncanonical_manifest_bytes_are_rejected(self) -> None:
        manifest = signable_manifest()
        pretty = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with self.assertRaises(compiler.ManifestValidationError):
            compiler.validate_manifest_bytes(pretty)

    def test_22_valid_owner_signoff_binds_budget_and_manifest(self) -> None:
        data = compiler.serialize_manifest(signable_manifest())
        signoff = owner_signoff(data)
        signoff["owner_note"] = "sidecar extensions remain outside the manifest hash"
        self.assertTrue(compiler.validate_owner_signoff(data, signoff))

    def test_23_draft_manifest_cannot_accept_owner_signoff(self) -> None:
        draft = compiler.build_manifest(EPOCH_ID)
        draft_bytes = compiler.serialize_manifest(draft)
        synthetic = {
            "schema": compiler.OWNER_SIGNOFF_SCHEMA,
            "decision": "AUTHORIZE",
            "epoch_id": EPOCH_ID,
            "manifest_sha256": compiler.manifest_sha256(draft_bytes),
            "lifetime_hard_cap_usd": "10.0000000",
            "historical_spend_usd": "0.2660758",
            "max_additional_spend_usd": "9.7339242",
            "authorization_source_identity": authorization_source("invalid-draft-signoff"),
        }
        with self.assertRaises(compiler.OwnerSignoffValidationError):
            compiler.validate_owner_signoff(draft_bytes, synthetic)

    def test_24_real_authority_root_output_is_refused(self) -> None:
        manifest = compiler.build_manifest(EPOCH_ID)
        forbidden = Path(compiler.AUTHORITY_ROOT) / "epochs" / EPOCH_ID / "manifest.json"
        with self.assertRaises(compiler.ManifestValidationError):
            compiler.write_manifest(forbidden, manifest)

    def test_25_initial_state_lists_are_counted_without_copying_content(self) -> None:
        inputs = complete_inputs()
        inputs["initial_state"] = {
            "runs": ["historical-secret-run-name"],
            "workspaces": [],
            "reservations": [],
            "aggregates": [],
        }
        manifest = compiler.build_manifest(EPOCH_ID, **inputs)
        initial = manifest["lifecycle"]["initial_state"]
        self.assertEqual(initial["status"], "NONEMPTY")
        self.assertEqual(initial["run_count"], 1)
        self.assertNotIn("historical-secret-run-name", compiler.serialize_manifest(manifest).decode())
        self.assertEqual(manifest["status"], "DRAFT_UNSIGNABLE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
