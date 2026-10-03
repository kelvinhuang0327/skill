#!/usr/bin/env python3
"""Offline tests for the execution profile and its startup identity check (never calls a model).

A synthetic world of consistent artifacts is built in a temp directory; each test breaks exactly one thing and expects
exactly the reasons that break implies.  The real v5 artifacts are used once, for the integration test.
Run: python3 -B -m unittest -q test_execution_profile
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import execution_profile as ep  # noqa: E402

API = ep.api
EVIDENCE = Path(os.environ.get("FABLE_ENSEMBLE3_EVIDENCE", "/Users/kelvin/VibeCoding-WorkSpace/fable-ensemble3-r1-evidence"))
SEALED = "32d5c9991c71d5b4025c015c395c9d41226a6d45"
SOURCE = "d037a9a8c612957540ff03c2d46b5b3ab8f0583a"
REPO = ep.ANALYSIS.parent
KEY = {"OPENAI_API_KEY": "sk-test-NOT-A-REAL-KEY-0000"}


def write(path: Path, value) -> Path:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return path


class World:
    """Consistent synthetic artifacts plus a profile built from them; overrides break one link before pinning."""

    def __init__(self, tmp, **overrides):
        self.dir = Path(tmp)
        self.pack = self.dir / "pack.md"
        self.pack.write_text(overrides.get("pack_text", "synthetic instruction pack\n"))
        pack_sha = ep.sha256_file(self.pack)
        self.pack_manifest = write(self.dir / "pack_manifest.json", {
            "instruction_sha256": overrides.get("manifest_pack_sha", pack_sha), "source_commit": overrides.get("manifest_source", SOURCE)})
        self.bank = write(self.dir / "bank_manifest.json", {
            "purpose": "FORMAL_DECISION_EXPLORATION", "instruction_pack_sha256": overrides.get("bank_pack_sha", pack_sha),
            "items": [{"item_id": f"fb-f01-{i}"} for i in range(46)]})
        bank_sha = ep.sha256_file(self.bank)
        self.receipt = write(self.dir / "receipt.json", {
            "verdict": overrides.get("verdict", "VERIFIED"), "scope": overrides.get("scope", "FORMAL_BANK_AND_ORACLE"),
            "bank_manifest_sha256": overrides.get("receipt_bank_sha", bank_sha),
            "instruction_sha256": overrides.get("receipt_pack_sha", pack_sha)})
        protocol = {
            "status": "DRAFT_NOT_FROZEN", "model": overrides.get("model", API.MODEL), "effort": overrides.get("effort", API.EFFORT),
            "cost_basis": "TEST_BASIS", "attempt_reservation_usd": overrides.get("attempt", "0.55"),
            "total_reservation_usd": overrides.get("total"), "target_runs": overrides.get("target_runs", 5), "schedule_seed": 6302,
            "bank": {**ep.pin(self.bank), "sha256": overrides.get("protocol_bank_sha", bank_sha)},
            "bank_review": {**ep.pin(self.receipt), "sha256": overrides.get("protocol_receipt_sha", ep.sha256_file(self.receipt))},
            "instruction_surface": {**ep.pin(self.pack), "sha256": overrides.get("protocol_pack_sha", pack_sha)},
        }
        self.protocol = write(self.dir / "protocol.json", protocol)
        self.profile = ep.build_profile(protocol=self.protocol, receipt=self.receipt, bank_manifest=self.bank, pack=self.pack,
                                        pack_manifest=self.pack_manifest, source_commit=SOURCE, sealed_commit=SEALED, repo=REPO)
        self.path = self.dir / "profile.json"

    def save(self, profile=None) -> Path:
        return write(self.path, self.profile if profile is None else profile)

    def check(self, profile=None, **kwargs) -> dict:
        kwargs.setdefault("env", {})
        return ep.check_profile(self.save(profile), **kwargs)


class ProfileTests(unittest.TestCase):
    def world(self, **overrides) -> World:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return World(tmp.name, **overrides)

    def reasons(self, result):
        return set(result["reasons"])

    # --- the profile itself --------------------------------------------------------------------------------------------
    def test_a_consistent_profile_passes_simulation(self):
        result = self.world().check()
        self.assertEqual((result["status"], result["reasons"], result["generation_calls"]), ("PASS", [], 0))

    def test_a_missing_or_corrupt_profile_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in (("absent.json", None), ("garbage.json", "{not json"), ("list.json", "[]"),
                                  ("dup.json", '{"a": 1, "a": 2}')):
                path = Path(tmp) / name
                if content is not None:
                    path.write_text(content)
                self.assertEqual(ep.check_profile(path)["reasons"], ["PROFILE_MISSING_OR_INVALID"], name)

    def test_every_required_field_and_the_schema_are_required(self):
        world = self.world()
        for key in ep.REQUIRED:
            profile = {k: v for k, v in world.profile.items() if k != key}
            self.assertIn(f"PROFILE_FIELD_MISSING:{key}", world.check(profile)["reasons"], key)
        self.assertEqual(world.check({**world.profile, "schema_version": 2})["reasons"], ["PROFILE_SCHEMA_INVALID"])
        self.assertEqual(world.check({**world.profile, "artifact": "OTHER"})["reasons"], ["PROFILE_SCHEMA_INVALID"])
        self.assertEqual(world.check()["status"], "PASS")

    def test_an_unknown_mode_blocks(self):
        self.assertEqual(self.world().check(mode="DRY_RUN")["reasons"], ["MODE_INVALID"])

    def test_a_malformed_body_blocks_instead_of_crashing(self):
        world = self.world()
        profile = copy.deepcopy(world.profile)
        profile["instruction"] = "not a mapping"
        result = world.check(profile)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("PROFILE_SHAPE_INVALID", result["reasons"])

    # --- pinned artifacts are re-hashed ---------------------------------------------------------------------------------
    def test_every_pinned_artifact_is_rehashed_and_a_missing_one_is_reported(self):
        world = self.world()
        world.save()
        # the bank manifest changes too, which also breaks the receipt and protocol bindings; the pin reason must be present
        for path, name in ((world.pack, "instruction"), (world.pack_manifest, "instruction_manifest"), (world.bank, "bank_manifest"),
                           (world.receipt, "bank_receipt"), (world.protocol, "protocol")):
            original = path.read_bytes()
            try:
                path.write_bytes(original + b" ")
                self.assertIn(f"ARTIFACT_HASH_MISMATCH:{name}", ep.check_profile(world.path, env={})["reasons"], name)
                path.unlink()
                self.assertIn(f"ARTIFACT_MISSING:{name}", ep.check_profile(world.path, env={})["reasons"], name)
            finally:
                path.write_bytes(original)
        self.assertEqual(ep.check_profile(world.path, env={})["status"], "PASS")

    def test_policy_adapter_and_lab_pins_are_rehashed(self):
        world = self.world()
        for path, expected in ((("policy", "sha256"), "ARTIFACT_HASH_MISMATCH:policy"),
                               (("provider", "adapter", "sha256"), "ARTIFACT_HASH_MISMATCH:adapter"),
                               (("lab", "decision_engine.py", "sha256"), "ARTIFACT_HASH_MISMATCH:lab:decision_engine.py")):
            profile = copy.deepcopy(world.profile)
            node = profile
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = "0" * 64
            self.assertIn(expected, world.check(profile)["reasons"], expected)

    # --- one instruction pack, everywhere ------------------------------------------------------------------------------
    def test_a_stale_pack_is_blocked_wherever_it_hides(self):
        fresh = self.world()
        stale_sha = ep.sha256_file(fresh.pack)
        with mock.patch.dict(ep.KNOWN_STALE_PACKS, {stale_sha: "test"}):
            self.assertIn("STALE_INSTRUCTION_PACK", fresh.check()["reasons"])  # the profile itself is bound to a stale pack
        profile = copy.deepcopy(fresh.profile)
        profile["instruction"]["superseded_sha256"] = [ep.sha256_file(fresh.pack)]
        self.assertIn("STALE_INSTRUCTION_PACK", fresh.check(profile)["reasons"])  # listed as superseded by the profile
        old = next(iter(ep.KNOWN_STALE_PACKS))
        for override in ("bank_pack_sha", "receipt_pack_sha", "protocol_pack_sha", "manifest_pack_sha"):
            world = self.world(**{override: old})
            self.assertIn("STALE_INSTRUCTION_PACK", world.check()["reasons"], override)

    def test_the_four_artifacts_must_agree_about_the_pack(self):
        other = "f" * 64
        for override, reason in (("bank_pack_sha", "PACK_BANK_MISMATCH"), ("receipt_pack_sha", "PACK_RECEIPT_MISMATCH"),
                                 ("protocol_pack_sha", "PACK_PROTOCOL_MISMATCH"), ("manifest_pack_sha", "PACK_MANIFEST_MISMATCH")):
            self.assertEqual(self.reasons(self.world(**{override: other}).check()), {reason}, override)
        self.assertEqual(self.reasons(self.world(manifest_source="1" * 40).check()), {"PACK_SOURCE_COMMIT_MISMATCH"})

    def test_conflict_markers_in_the_pack_are_blocked(self):
        for text in ("a\n<<<<<<< HEAD\nb\n", "a\n>>>>>>> 0fd7c60\nb\n", "a\n=======\nb\n", "a\n<<<<<<<\nb\n"):
            self.assertEqual(self.reasons(self.world(pack_text=text).check()), {"INSTRUCTION_HAS_CONFLICT_MARKERS"}, text)
        self.assertEqual(self.world(pack_text="a\n<<<<<<<<< not a marker run of eight\n= = =\n").check()["status"], "PASS")

    # --- one bank, one receipt ------------------------------------------------------------------------------------------
    def test_bank_receipt_and_protocol_must_agree(self):
        other = "e" * 64
        self.assertEqual(self.reasons(self.world(receipt_bank_sha=other).check()), {"BANK_RECEIPT_MISMATCH"})
        self.assertEqual(self.reasons(self.world(protocol_bank_sha=other).check()), {"BANK_PROTOCOL_MISMATCH"})
        self.assertEqual(self.reasons(self.world(protocol_receipt_sha=other).check()), {"RECEIPT_PROTOCOL_MISMATCH"})

    def test_the_receipt_must_be_a_verified_formal_bank_review(self):
        for override in ({"verdict": "REFUTED"}, {"verdict": "VERIFIED_WITH_CAVEATS"}, {"scope": "ENGINEERING"}):
            self.assertEqual(self.reasons(self.world(**override).check()), {"RECEIPT_NOT_VERIFIED"}, override)

    def test_bank_shape_must_match_the_pin(self):
        world = self.world()
        profile = copy.deepcopy(world.profile)
        profile["bank"]["items"] = 45
        self.assertEqual(self.reasons(world.check(profile)), {"BANK_SHAPE_MISMATCH"})
        profile = copy.deepcopy(world.profile)
        profile["bank"]["purpose"] = "ENGINEERING_ONLY"
        self.assertEqual(self.reasons(world.check(profile)), {"BANK_SHAPE_MISMATCH"})

    # --- policy, model, provider ----------------------------------------------------------------------------------------
    def test_the_policy_must_be_the_reviewed_r2_and_parameters_must_match_the_protocol(self):
        world = self.world()
        profile = copy.deepcopy(world.profile)
        profile["policy"]["sha256"] = "0" * 64
        self.assertEqual(self.reasons(world.check(profile)), {"ARTIFACT_HASH_MISMATCH:policy", "POLICY_NOT_THE_REVIEWED_R2"})
        for key, value in (("quorum", 1), ("target_runs", 3), ("schedule_seed", 1)):
            profile = copy.deepcopy(world.profile)
            profile["policy"][key] = value
            self.assertEqual(self.reasons(world.check(profile)), {"POLICY_PARAMETER_MISMATCH"}, key)
        self.assertEqual(self.reasons(self.world(target_runs=3).check()), {"REGISTERED_R1_RUN_COUNT_MISMATCH"})

    def test_model_effort_and_provider_constants_must_match_the_adapter(self):
        self.assertEqual(self.reasons(self.world(model="gpt-6-luna").check()), {"MODEL_MISMATCH"})
        self.assertEqual(self.reasons(self.world(effort="high").check()), {"EFFORT_MISMATCH"})
        world = self.world()
        for key, value in (("input_token_limit", 999999), ("output_token_limit", 1), ("service_tier", "priority"), ("store", True)):
            profile = copy.deepcopy(world.profile)
            profile["provider"][key] = value
            self.assertEqual(self.reasons(world.check(profile)), {"PROVIDER_CONSTANT_MISMATCH"}, key)

    def test_the_attempt_reservation_must_equal_the_adapter_and_the_protocol(self):
        self.assertEqual(self.reasons(self.world(attempt="0.06").check()), {"COST_RESERVATION_MISMATCH"})
        world = self.world()
        profile = copy.deepcopy(world.profile)
        profile["cost"]["basis"] = "OTHER_BASIS"
        self.assertEqual(self.reasons(world.check(profile)), {"COST_BASIS_MISMATCH"})
        profile = copy.deepcopy(world.profile)
        profile["cost"]["max_attempts_per_slot"] = 3
        self.assertIn("COST_BASIS_MISMATCH", world.check(profile)["reasons"])

    def test_live_requires_the_whole_five_round_reservation(self):
        # 0.55 x 2 x 46 x 5 = 253.00
        for total, expected in ((None, "TOTAL_COST_CAP_UNFROZEN"), ("151.80", "REGISTERED_WHOLE_ROUNDS_NOT_RESERVED"),
                                ("252.99", "REGISTERED_WHOLE_ROUNDS_NOT_RESERVED")):
            world = self.world(total=total)
            self.assertIn(expected, world.check(mode="LIVE", env=KEY)["reasons"], total)
        world = self.world(total="253.00")
        live = world.check(mode="LIVE", env=KEY)["reasons"]
        self.assertNotIn("TOTAL_COST_CAP_UNFROZEN", live)
        self.assertNotIn("REGISTERED_WHOLE_ROUNDS_NOT_RESERVED", live)
        self.assertNotIn("COST_TOTAL_PROTOCOL_MISMATCH", live)
        profile = copy.deepcopy(world.profile)
        profile["cost"]["total_reservation_usd"] = "300"
        self.assertIn("COST_TOTAL_PROTOCOL_MISMATCH", world.check(profile, mode="LIVE", env=KEY)["reasons"])
        self.assertNotIn("TOTAL_COST_CAP_UNFROZEN", self.world().check()["reasons"])  # simulation may leave the total open

    # --- the sealed source ---------------------------------------------------------------------------------------------
    def test_sealed_source_drift_missing_and_extra_files_block(self):
        world = self.world()
        name = "analysis/fable-ensemble3-lab/budget_ledger.py"
        profile = copy.deepcopy(world.profile)
        profile["sealed_source"]["files"][name] = "0" * 64
        self.assertIn(f"SEALED_SOURCE_DRIFT:{name}", world.check(profile)["reasons"])
        profile = copy.deepcopy(world.profile)
        profile["sealed_source"]["files"]["analysis/fable-ensemble3-lab/never_existed.py"] = "0" * 64
        self.assertIn("SEALED_SOURCE_MISSING:analysis/fable-ensemble3-lab/never_existed.py", world.check(profile)["reasons"])
        profile = copy.deepcopy(world.profile)
        del profile["sealed_source"]["files"][name]
        result = world.check(profile)["reasons"]
        self.assertIn(f"SEALED_SOURCE_EXTRA:{name}", result)
        self.assertIn("LAB_NOT_SEALED:budget_ledger.py", result)

    def test_the_runner_package_is_outside_the_sealed_set(self):
        self.assertEqual(len(self.world().profile["sealed_source"]["files"]), 28)
        self.assertTrue(all(not name.startswith("analysis/fable-ensemble3-runner") for name in self.world().profile["sealed_source"]["files"]))

    def test_the_runner_code_is_pinned_and_a_changed_missing_or_malformed_pin_blocks(self):
        world = self.world()
        self.assertEqual(world.profile["runner_sources"], ep.runner_sources())
        self.assertEqual(sorted(world.profile["runner_sources"]), sorted(ep.RUNNER_FILES))
        def reasons(profile=None):
            try:
                return world.check(profile)["reasons"]
            except Exception as exc:  # the check must report a problem with the code, never crash on it
                self.fail(f"the check crashed instead of reporting: {type(exc).__name__}: {exc}")

        # the drift check reads the package directory when it runs, so a scratch copy of the code stands in for the package
        with tempfile.TemporaryDirectory() as scratch:
            for name in ep.RUNNER_FILES:
                (Path(scratch) / name).write_bytes((HERE / name).read_bytes())
            with mock.patch.object(ep, "HERE", Path(scratch)):
                self.assertEqual(reasons(), [])  # an identical copy is the same code
                (Path(scratch) / "probe_2b.py").write_text("# changed after the profile was made\n")
                self.assertEqual(reasons(), ["RUNNER_SOURCE_DRIFT:probe_2b.py"])
                (Path(scratch) / "exploration_runner.py").unlink()
                self.assertEqual(reasons(), ["RUNNER_SOURCE_DRIFT:probe_2b.py", "RUNNER_SOURCE_MISSING:exploration_runner.py"])
        pins = world.profile["runner_sources"]
        for broken in (None, [], {}, {**pins, "extra.py": "0" * 64}, {k: v for k, v in pins.items() if k != "_lab.py"}):
            self.assertEqual(reasons({**world.profile, "runner_sources": broken}), ["RUNNER_SOURCE_PIN_MALFORMED"], broken)

    def test_lab_pins_must_equal_the_sealed_files(self):
        world = self.world()
        profile = copy.deepcopy(world.profile)
        profile["lab"]["offline_replay.py"]["sha256"] = "0" * 64
        result = world.check(profile)["reasons"]
        self.assertIn("LAB_NOT_SEALED:offline_replay.py", result)
        self.assertIn("ARTIFACT_HASH_MISMATCH:lab:offline_replay.py", result)

    # --- live mode -------------------------------------------------------------------------------------------------------
    def test_live_mode_needs_a_frozen_profile_a_key_a_ready_interface_and_a_clean_preflight(self):
        reasons = self.world().check(mode="LIVE", env={})["reasons"]
        for expected in ("LIVE_PROFILE_NOT_FROZEN", "LIVE_KEY_MISSING", "LIVE_INTERFACE_NOT_READY", "TOTAL_COST_CAP_UNFROZEN"):
            self.assertIn(expected, reasons)
        self.assertTrue(any(r.startswith("PREFLIGHT:") for r in reasons))
        with_key = self.world().check(mode="LIVE", env=KEY)["reasons"]
        self.assertNotIn("LIVE_KEY_MISSING", with_key)
        self.assertNotIn("LIVE_KEY_MISSING", self.world().check(mode="SIMULATION", env={})["reasons"])
        self.assertEqual(self.world().check(mode="SIMULATION", env={})["status"], "PASS")

    def test_a_frozen_profile_with_a_ready_interface_still_needs_the_preflight(self):
        world = self.world(total="253.00")
        interface = write(world.dir / "interface.json", {"status": "BENCHMARK_READY", "mode": "LIVE",
                                                         "instruction_sha256": ep.sha256_file(world.pack)})
        profile = copy.deepcopy(world.profile)
        profile["status"] = "FROZEN"
        profile["interface"] = ep.pin(interface)
        result = world.check(profile, mode="LIVE", env=KEY)["reasons"]
        self.assertNotIn("LIVE_PROFILE_NOT_FROZEN", result)
        self.assertNotIn("LIVE_INTERFACE_NOT_READY", result)
        self.assertNotIn("LIVE_KEY_MISSING", result)
        self.assertIn("PREFLIGHT:PROTOCOL_NOT_FROZEN", result)  # the synthetic protocol is a draft
        write(interface, {"status": "PARTIAL_NOT_BENCHMARK_READY", "mode": "LIVE", "instruction_sha256": ep.sha256_file(world.pack)})
        profile["interface"] = ep.pin(interface)
        self.assertIn("LIVE_INTERFACE_NOT_READY", world.check(profile, mode="LIVE", env=KEY)["reasons"])

    def test_the_key_value_never_appears_in_a_result(self):
        world = self.world()
        text = json.dumps(world.check(mode="LIVE", env=KEY))
        self.assertNotIn(KEY["OPENAI_API_KEY"], text)
        self.assertNotIn("sk-test", text)

    # --- the bank is only rebuilt with an explicit --pack -----------------------------------------------------------------
    def test_deep_rebuild_always_passes_an_explicit_pack_and_compares_the_manifest(self):
        world = self.world()
        world.save()
        calls = []

        def make(manifest_text):
            def fake(argv, **kwargs):
                calls.append(list(argv))
                if manifest_text is not None:
                    destination = Path(argv[3])
                    destination.mkdir(parents=True)
                    (destination / "manifest.json").write_text(manifest_text)
                return mock.Mock(returncode=0 if manifest_text is not None else 1, stdout="", stderr="")
            return fake

        with mock.patch.object(ep.subprocess, "run", side_effect=make(world.bank.read_text())):
            self.assertEqual(ep.check_profile(world.path, env={}, deep=True)["status"], "PASS")
        self.assertEqual(len(calls), 1)  # a deep check really rebuilds, exactly once
        argv = calls[0]
        self.assertTrue(argv[2].endswith("build_formal_bank.py"))
        self.assertIn("--pack", argv)
        self.assertEqual(argv[argv.index("--pack") + 1], str(world.pack.resolve()))
        with mock.patch.object(ep.subprocess, "run", side_effect=make('{"different": true}\n')):
            self.assertEqual(ep.check_profile(world.path, env={}, deep=True)["reasons"], ["BANK_REBUILD_MISMATCH"])
        with mock.patch.object(ep.subprocess, "run", side_effect=make(None)):
            self.assertEqual(ep.check_profile(world.path, env={}, deep=True)["reasons"], ["BANK_REBUILD_FAILED"])
        with mock.patch.object(ep.subprocess, "run", side_effect=AssertionError("a plain check must never rebuild")):
            self.assertEqual(ep.check_profile(world.path, env={})["status"], "PASS")


class RealArtifactTests(unittest.TestCase):
    """The real v5 bank, receipt, pack and protocol draft: the integration the synthetic world only imitates."""

    @classmethod
    def setUpClass(cls):
        cls.paths = {
            "protocol": EVIDENCE / "formal-bank-v5/review-final/protocol.with-review.draft.json",
            "receipt": EVIDENCE / "formal-bank-v5/review-final/bank_review.json",
            "bank_manifest": EVIDENCE / "formal-bank-v5/manifest.json",
            "pack": EVIDENCE / "instruction-source-v2/candidate_instructions.md",
            "pack_manifest": EVIDENCE / "instruction-source-v2/manifest.json",
            "old_pack": EVIDENCE / "instruction-source/candidate_instructions.md",
            "old_pack_manifest": EVIDENCE / "instruction-source/manifest.json",
        }
        missing = [str(p) for p in cls.paths.values() if not p.exists()]
        if missing:  # a missing artifact must fail loudly: this test is the real-world binding
            raise AssertionError(f"real v5 artifacts not found (set FABLE_ENSEMBLE3_EVIDENCE): {missing}")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.profile = ep.build_profile(
            protocol=cls.paths["protocol"], receipt=cls.paths["receipt"], bank_manifest=cls.paths["bank_manifest"],
            pack=cls.paths["pack"], pack_manifest=cls.paths["pack_manifest"], source_commit=SOURCE, sealed_commit=SEALED, repo=REPO)
        cls.path = write(Path(cls.tmp.name) / "profile.json", cls.profile)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_real_profile_passes_simulation_and_the_deep_rebuild(self):
        self.assertEqual(ep.check_profile(self.path, env={})["reasons"], [])
        self.assertEqual(ep.check_profile(self.path, env={}, deep=True)["reasons"], [])
        self.assertEqual(self.profile["bank"]["items"], 46)
        self.assertEqual(len(self.profile["sealed_source"]["files"]), 28)
        self.assertEqual(self.profile["instruction"]["sha256"], "13adfbf489ba5ad9ed4a6dea86ecbc742c8976cb67d7a12c3f4f79e01f278958")

    def test_the_real_profile_is_blocked_live_until_the_owner_gates_are_met(self):
        reasons = set(ep.check_profile(self.path, mode="LIVE", env={})["reasons"])
        for expected in ("LIVE_PROFILE_NOT_FROZEN", "LIVE_KEY_MISSING", "LIVE_INTERFACE_NOT_READY", "TOTAL_COST_CAP_UNFROZEN",
                         "PREFLIGHT:PROTOCOL_NOT_FROZEN", "PREFLIGHT:LIVE_INTERFACE_EXIT_NOT_MET", "PREFLIGHT:TOTAL_COST_CAP_UNFROZEN",
                         "PREFLIGHT:INTERFACE_INSTRUCTION_IDENTITY_INVALID"):
            self.assertIn(expected, reasons)
        self.assertNotIn("PREFLIGHT:FORMAL_BANK_REVIEW_MISSING", reasons)  # the receipt did clear that gate
        self.assertFalse(any(r.startswith(("ARTIFACT_", "PACK_", "BANK_", "STALE", "SEALED", "RECEIPT_")) for r in reasons), reasons)

    def test_the_old_v1_pack_cannot_be_used_with_the_real_bank(self):
        profile = copy.deepcopy(self.profile)
        profile["instruction"] = {**profile["instruction"], **ep.pin(self.paths["old_pack"]), "manifest": ep.pin(self.paths["old_pack_manifest"])}
        result = set(ep.check_profile(write(Path(self.tmp.name) / "stale.json", profile), env={})["reasons"])
        for expected in ("STALE_INSTRUCTION_PACK", "INSTRUCTION_HAS_CONFLICT_MARKERS", "PACK_BANK_MISMATCH", "PACK_RECEIPT_MISMATCH",
                         "PACK_PROTOCOL_MISMATCH"):
            self.assertIn(expected, result)

    def test_the_real_pack_has_no_conflict_marker(self):
        text = self.paths["pack"].read_text(encoding="utf-8")
        self.assertIsNone(ep.CONFLICT_MARKER.search(text))
        self.assertIsNotNone(ep.CONFLICT_MARKER.search(self.paths["old_pack"].read_text(encoding="utf-8")))


class CliTests(unittest.TestCase):
    def run_main(self, argv):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ep.main(argv)
        return code, json.loads(buffer.getvalue())

    def args(self, world, out):
        return ["build", "--protocol", str(world.protocol), "--receipt", str(world.receipt), "--bank-manifest", str(world.bank),
                "--pack", str(world.pack), "--pack-manifest", str(world.pack_manifest), "--source-commit", SOURCE,
                "--sealed-commit", SEALED, "--repo", str(REPO), "--out", str(out)]

    def test_build_publishes_only_a_passing_profile_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as bad_dir, tempfile.TemporaryDirectory() as good_dir:
            bad = World(bad_dir, bank_pack_sha="f" * 64)
            out = Path(bad_dir) / "published.json"
            code, result = self.run_main(self.args(bad, out))
            self.assertEqual(code, 2)
            self.assertIn("PACK_BANK_MISMATCH", result["reasons"])
            self.assertFalse(out.exists())
            self.assertEqual([p.name for p in Path(bad_dir).glob("*.tmp")], [])
            good = World(good_dir)
            out = Path(good_dir) / "published.json"
            code, result = self.run_main(self.args(good, out))
            self.assertEqual((code, result["reasons"]), (0, []))
            self.assertEqual(json.loads(out.read_text()), good.profile)
            with self.assertRaises(FileExistsError):
                self.run_main(self.args(good, out))
            self.assertEqual(json.loads(out.read_text()), good.profile)
            self.assertEqual([p.name for p in Path(good_dir).glob("*.tmp")], [])

    def test_check_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = World(tmp)
            path = world.save()
            self.assertEqual(self.run_main(["check", str(path)])[0], 0)
            code, result = self.run_main(["check", str(path), "--mode", "LIVE"])
            self.assertEqual((code, result["status"]), (2, "BLOCKED"))


if __name__ == "__main__":
    unittest.main()
