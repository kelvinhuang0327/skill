#!/usr/bin/env python3
"""Offline acceptance tests for the sealed-authority P01 bridge."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path


sys.dont_write_bytecode = True
MODULE_PATH = Path(__file__).with_name("build_execution_manifest.py")
SPEC = importlib.util.spec_from_file_location(
    "fable_ablation_execution_manifest", MODULE_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {MODULE_PATH}")
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


SEALED_ROOT = Path(
    "/Users/kelvin/FableAblation-Epoch-56a62a62e5aae9fe3a230cbbed831a2a"
)
SEALED_MANIFEST_PATH = SEALED_ROOT / "build" / "manifest.json"
SEALED_MANIFEST_SHA256 = (
    "49aa913fdf5487b839565dba8a57de243b936a61e0c70a384fdc4df73b2fbd94"
)
A01_PROMPT_SHA256 = (
    "3489c5e8c4e6e1781f3fd30d2e7417777b9a5cb8f652baf2cfde456328d2c14f"
)
A01_FIXTURE_SHA256 = (
    "eca51c4223038de8af070718b3b4435806546bc14b9e68d90a5d84ad38d66e31"
)
A01_ORACLE_SHA256 = (
    "84e7b8e5f15808ce8e44cfcd0a263368371626b79011214035379d828af65f79"
)


class ExecutionManifestBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw = SEALED_MANIFEST_PATH.read_bytes()
        if hashlib.sha256(raw).hexdigest() != SEALED_MANIFEST_SHA256:
            raise AssertionError("the exact sealed authority manifest changed")
        cls.sealed = json.loads(raw.decode("utf-8"))

    def derive(self, **kwargs: object) -> dict[str, object]:
        return builder.derive_p01_execution_manifest(
            self.sealed,
            expected_sealed_manifest_sha256=SEALED_MANIFEST_SHA256,
            **kwargs,
        )

    def test_01_sealed_v3_authority_is_not_runner_execution_input(self) -> None:
        with self.assertRaisesRegex(
            builder.ExecutionManifestError, "sealed v3 authority"
        ):
            builder.validate_execution_manifest(self.sealed)

    def test_02_p01_is_exactly_two_slots_off_then_on(self) -> None:
        manifest = self.derive()
        self.assertEqual(len(manifest["schedule"]), 2)
        self.assertEqual(
            [item["condition"] for item in manifest["schedule"]], ["OFF", "ON"]
        )
        self.assertEqual(manifest["pair"]["arm_order"], ["OFF", "ON"])

    def test_03_only_a01_is_present(self) -> None:
        manifest = self.derive()
        self.assertEqual(manifest["pair"]["task_id"], "A01")
        self.assertEqual([item["task_id"] for item in manifest["tasks"]], ["A01"])
        self.assertEqual(
            [item["task_id"] for item in manifest["schedule"]], ["A01", "A01"]
        )

    def test_04_full_prompt_hash_matches_sealed_pin(self) -> None:
        manifest = self.derive()
        prompt = manifest["tasks"][0]["prompt"].encode("utf-8")
        self.assertEqual(hashlib.sha256(prompt).hexdigest(), A01_PROMPT_SHA256)
        self.assertEqual(
            manifest["authority"]["corpus_identity"]["prompt_sha256"],
            A01_PROMPT_SHA256,
        )

    def test_05_full_fixture_hash_matches_sealed_pin(self) -> None:
        corpus = builder.recovered_a01_corpus()
        self.assertEqual(
            hashlib.sha256(corpus.fixture_bytes).hexdigest(), A01_FIXTURE_SHA256
        )
        manifest = self.derive()
        self.assertEqual(
            manifest["tasks"][0]["fixture"]["sha256"], A01_FIXTURE_SHA256
        )

    def test_06_full_oracle_hash_matches_sealed_pin(self) -> None:
        corpus = builder.recovered_a01_corpus()
        self.assertEqual(
            hashlib.sha256(corpus.oracle_bytes).hexdigest(), A01_ORACLE_SHA256
        )
        manifest = self.derive()
        self.assertEqual(
            manifest["tasks"][0]["oracle"]["sha256"], A01_ORACLE_SHA256
        )

    def test_07_wrong_prompt_is_rejected(self) -> None:
        corpus = replace(builder.recovered_a01_corpus(), prompt="wrong prompt")
        with self.assertRaisesRegex(
            builder.ExecutionManifestError, "prompt_sha256"
        ):
            self.derive(corpus=corpus)

    def test_08_wrong_fixture_is_rejected(self) -> None:
        original = builder.recovered_a01_corpus()
        corpus = replace(original, fixture_bytes=original.fixture_bytes + b"tamper")
        with self.assertRaisesRegex(
            builder.ExecutionManifestError, "fixture_sha256"
        ):
            self.derive(corpus=corpus)

    def test_09_wrong_oracle_is_rejected(self) -> None:
        original = builder.recovered_a01_corpus()
        corpus = replace(original, oracle_bytes=original.oracle_bytes + b"tamper")
        with self.assertRaisesRegex(
            builder.ExecutionManifestError, "oracle_sha256"
        ):
            self.derive(corpus=corpus)

    def test_10_arm_order_mutation_is_rejected(self) -> None:
        mutated = copy.deepcopy(self.sealed)
        mutated["schedule"]["pairs"][0]["arm_order"] = ["ON", "OFF"]
        with self.assertRaises(builder.ExecutionManifestError):
            builder.derive_p01_execution_manifest(mutated)

    def test_11_extra_task_or_pair_injection_is_rejected(self) -> None:
        manifest = self.derive()
        extra_task = copy.deepcopy(manifest)
        extra_task["tasks"].append(copy.deepcopy(extra_task["tasks"][0]))
        extra_pair = copy.deepcopy(manifest)
        extra_pair["pair"]["extra_pair"] = "P02"
        for candidate in (extra_task, extra_pair):
            with self.subTest(candidate=candidate):
                with self.assertRaises(builder.ExecutionManifestError):
                    builder.validate_execution_manifest(candidate)

    def test_12_historical_result_or_run_state_contamination_is_rejected(self) -> None:
        original = builder.recovered_a01_corpus()
        for key in ("result", "run_path", "session_id", "total_cost_usd"):
            metadata = dict(original.task_metadata)
            metadata[key] = "historical-contamination"
            with self.subTest(key=key):
                with self.assertRaises(builder.ExecutionManifestError):
                    self.derive(corpus=replace(original, task_metadata=metadata))

    def test_13_serialization_and_hash_are_deterministic(self) -> None:
        first = self.derive()
        second = self.derive()
        self.assertEqual(first, second)
        self.assertEqual(
            builder.serialize_execution_manifest(first),
            builder.serialize_execution_manifest(second),
        )
        self.assertEqual(
            builder.execution_manifest_sha256(first),
            builder.execution_manifest_sha256(second),
        )

    def test_14_existing_runner_selects_both_derived_slots(self) -> None:
        manifest = self.derive()
        selected = [
            builder.runner.select_manifest_slot(manifest, item["run_id"])
            for item in manifest["schedule"]
        ]
        self.assertEqual([item.condition for item in selected], ["OFF", "ON"])
        self.assertEqual([item.task_id for item in selected], ["A01", "A01"])

    def test_15_off_on_command_neutrality_is_exact(self) -> None:
        manifest = self.derive()
        self.assertEqual(
            manifest["treatment"]["off"]["command"],
            manifest["treatment"]["on"]["command"],
        )
        selected = [
            builder.runner.select_manifest_slot(manifest, item["run_id"])
            for item in manifest["schedule"]
        ]
        self.assertEqual(selected[0].command, selected[1].command)

    def test_16_treatment_or_purity_semantic_conflict_fails_closed(self) -> None:
        original = builder.recovered_a01_corpus()

        command_conflict = copy.deepcopy(original.execution_metadata)
        command_conflict["treatment"]["on"]["command"][-1] = "1"
        purity_conflict = copy.deepcopy(original.execution_metadata)
        purity_conflict["purity_gate"][
            "must_be_exactly_equal_between_reference_and_run"
        ].remove("agents")
        path_dependent_conflict = copy.deepcopy(original.execution_metadata)
        path_dependent_conflict["treatment"]["off"]["command"][0] = "claude"
        path_dependent_conflict["treatment"]["on"]["command"][0] = "claude"

        for metadata in (
            command_conflict,
            purity_conflict,
            path_dependent_conflict,
        ):
            with self.subTest(metadata=metadata):
                with self.assertRaisesRegex(
                    builder.ExecutionManifestError,
                    "EXECUTION_MANIFEST_SEMANTICS_AMBIGUOUS",
                ):
                    self.derive(
                        corpus=replace(original, execution_metadata=metadata)
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
