#!/usr/bin/env python3
"""Offline lifecycle acceptance tests for the canonical P01 pair driver."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest import mock


sys.dont_write_bytecode = True
MODULE_PATH = Path(__file__).with_name("epoch_pair_driver.py")
SPEC = importlib.util.spec_from_file_location("fable_ablation_epoch_pair_driver", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {MODULE_PATH}")
driver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = driver
SPEC.loader.exec_module(driver)


SEALED_ROOT = Path(
    "/Users/kelvin/FableAblation-Epoch-56a62a62e5aae9fe3a230cbbed831a2a"
)
SESSION_ID = "offline-provider-session"


def init_event(*, engaged: bool = False) -> dict[str, Any]:
    skills = ["reference-skill"]
    if engaged:
        skills.append("fable-method")
    return {
        "type": "system",
        "subtype": "init",
        "session_id": SESSION_ID,
        "skills": skills,
        "tools": ["Read", "Edit"],
        "agents": ["general-purpose"],
        "mcp_servers": ["local-test"],
    }


def git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr}"
        )
    return result.stdout.strip()


class Materializer:
    def __init__(self) -> None:
        self.engaged_by_workspace: dict[str, bool] = {}

    def __call__(self, plan: Any) -> None:
        root = Path(plan.workspace_path)
        self.engaged_by_workspace[str(root)] = plan.slot.expected_fable_engaged
        (root / "README.md").write_text("Sequence fixture specification.\n", encoding="utf-8")
        (root / "seqtools.py").write_text("def chunk(seq, size):\n    return []\n", encoding="utf-8")
        if plan.slot.expected_fable_engaged:
            skill = root / ".claude" / "skills" / "fable-method"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("canonical treatment carrier\n", encoding="utf-8")
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.name", "Offline Pair Test")
        git(root, "config", "user.email", "offline-pair@example.invalid")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "fixture baseline")


def provider_execution(
    invocation: Any,
    *,
    engaged: bool,
    cost: Decimal | None,
) -> Any:
    terminal_cost = None if cost is None else driver.compiler.canonical_usd_text(cost)
    records = [
        init_event(engaged=engaged),
        {
            "type": "assistant",
            "session_id": SESSION_ID,
            "message": {"id": "msg-offline", "model": "offline-model"},
        },
        {
            "type": "result",
            "subtype": "success",
            "session_id": SESSION_ID,
            "modelUsage": {"offline-model": {"input_tokens": 1}},
            "total_cost_usd": terminal_cost,
        },
    ]
    raw_stdout = b"\n".join(
        json.dumps(record, separators=(",", ":")).encode("utf-8")
        for record in records
    ) + b"\n"
    prompt_bytes = invocation.prompt.encode("utf-8")
    environment = tuple(
        driver.runner.EnvironmentValueEvidence(
            key=key,
            value="REDACTED",
            sha256_fingerprint=hashlib.sha256(value.encode("utf-8")).hexdigest(),
            redacted=True,
        )
        for key, value in sorted(invocation.environment.items())
    )
    execution = driver.runner.ProviderExecution(
        adapter_identity=driver.runner.AdapterIdentity(
            name="offline-composed-adapter",
            version="1.0",
            source_realpath="/offline/adapter.py",
            source_sha256="a" * 64,
        ),
        provider_binary_identity=driver.runner.ProviderBinaryIdentity(
            executable=invocation.argv[0],
            realpath="/offline/provider",
            sha256="b" * 64,
            version="offline-provider-1.0",
        ),
        provider_policy_identity="c" * 64,
        provider_policy={"identity": "offline-policy"},
        invocation=driver.runner.ProviderInvocationEvidence(
            argv=tuple(invocation.argv),
            cwd=invocation.cwd,
            task_visible_run_id=invocation.task_visible_run_id,
            stdin_sha256=hashlib.sha256(prompt_bytes).hexdigest(),
            stdin_length=len(prompt_bytes),
            environment=environment,
            shell=False,
            close_fds=True,
            start_new_session=True,
            timeout_seconds=1.0,
        ),
        process=driver.runner.ProcessEvidence(
            pid=4321,
            pgid=4321,
            returncode=0,
            status="EXITED",
            timed_out=False,
            termination_attempted=False,
            termination_method=None,
            signals_sent=(),
            shell=False,
            close_fds=True,
            start_new_session=True,
            timeout_seconds=1.0,
        ),
        raw_stdout=raw_stdout,
        raw_stderr=b"",
        raw_jsonl_records=tuple(records),
        init_index=0,
        assistant_message_ids=("msg-offline",),
        terminal_result_index=2,
        session_id=SESSION_ID,
        model_usage={"offline-model": {"input_tokens": 1}},
        total_cost_usd=cost,
        validation_errors=(),
        sandbox_identity={
            "physical_argv": ["/usr/bin/sandbox-exec", "-f", "/offline/profile", *invocation.argv],
            "sandbox_exec_sha256": "d" * 64,
            "profile_sha256": "e" * 64,
            "session_root": invocation.cwd,
        },
    )
    return execution.sealed()


class FakeComposition:
    def __init__(
        self,
        materializer: Materializer,
        *,
        costs: Sequence[str | None] = ("0.5000000", "0.7500000"),
        behaviors: Sequence[str] = ("success", "success"),
    ) -> None:
        self.materializer = materializer
        self.costs = list(costs)
        self.behaviors = list(behaviors)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        ledger: Any,
        slot_id: str,
        sealed_invocation_cap: Decimal,
        policy: Any,
        invocation: Any,
        session_root: str,
    ) -> Any:
        del policy, session_root
        before = ledger.load()
        self.calls.append(
            {
                "slot_id": slot_id,
                "entry_transitions": [entry["transition"] for entry in before.entries],
                "outstanding_total": before.outstanding_total,
                "outstanding_slots": tuple(sorted(before.outstanding_reservations)),
            }
        )
        ledger.provider_start_intent(slot_id, sealed_invocation_cap)
        behavior = self.behaviors[len(self.calls) - 1]
        if behavior == "spawn_failure":
            raise OSError("synthetic spawn failure after durable intent")
        raw_cost = self.costs[len(self.calls) - 1]
        cost = None if raw_cost is None else Decimal(raw_cost)
        engaged = self.materializer.engaged_by_workspace[invocation.cwd]
        return types.SimpleNamespace(
            provider_execution=provider_execution(
                invocation, engaged=engaged, cost=cost
            )
        )


class TokenFactory:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.value:032x}"


class RecordingLock:
    def __init__(self, path: Path, events: list[str]) -> None:
        self.path = path
        self.events = events

    def acquire(self) -> None:
        self.events.append("lock")


class SimulatedCrash(RuntimeError):
    pass


class FailingSettlementLedger(driver.controller.BudgetLedger):
    def settle(self, slot_id: str, terminal_cost: Decimal | str | int) -> Mapping[str, Any]:
        raise driver.controller.LedgerError("synthetic settlement failure")


class EpochPairDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.epoch_root = self.root / "epoch"
        for name in ("reservations", "workspaces", "runs", "aggregates"):
            (self.epoch_root / name).mkdir(parents=True, exist_ok=True)

        authority = self.root / "authority"
        (authority / "build").mkdir(parents=True)
        (authority / "decision").mkdir()
        shutil.copyfile(
            SEALED_ROOT / "build" / "manifest.json",
            authority / "build" / "manifest.json",
        )
        shutil.copyfile(
            SEALED_ROOT / "build" / "owner-signoff.json",
            authority / "build" / "owner-signoff.json",
        )
        shutil.copyfile(
            SEALED_ROOT / "authority" / "owner-signoff-decision.txt",
            authority / "decision" / "owner-signoff-decision.txt",
        )
        self.paths = driver.EpochPaths(
            epoch_root=self.epoch_root,
            manifest_path=authority / "build" / "manifest.json",
            owner_signoff_path=authority / "build" / "owner-signoff.json",
            owner_signoff_decision_path=(
                authority / "decision" / "owner-signoff-decision.txt"
            ),
            lock_path=self.root / "epoch.lock",
        )
        self.events: list[str] = []
        self.materializer = Materializer()
        self.tokens = TokenFactory()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def paid_authorization(self) -> dict[str, Any]:
        return {
            "schema": driver.PAID_PAIR_AUTHORIZATION_SCHEMA,
            "decision": "AUTHORIZE_PAID_PAIR",
            "epoch_id": driver.EPOCH_ID,
            "manifest_sha256": driver.SEALED_MANIFEST_SHA256,
            "pair_id": "P01",
            "task_id": "A01",
            "arm_order": ["OFF", "ON"],
            "maximum_pair_spend_usd": "4.0000000",
            "per_invocation_cap_usd": "2.0000000",
            "pre_pair_headroom_usd": "7.7339242",
        }

    def dependencies(
        self,
        composition: FakeComposition | None = None,
        **overrides: Any,
    ) -> driver.PairRuntimeDependencies:
        fake = composition or FakeComposition(self.materializer)
        values: dict[str, Any] = {
            "reference_events": (init_event(),),
            "materializer": self.materializer,
            "policy": object(),
            "environment": {},
            "opaque_id_factory": self.tokens,
            "lock_factory": lambda path: RecordingLock(path, self.events),
            "host_provider": lambda: "offline-host",
            "boot_provider": lambda: "offline-boot",
            "process_scanner": lambda roots: (),
            "creation_identity_provider": lambda: {
                "host": "offline-host",
                "boot_id": "offline-boot",
                "pid": 123,
                "start_identity": "offline-start",
            },
            "time_ns_provider": lambda: 1788235200000000000,
            "composition_executor": fake,
        }
        values.update(overrides)
        return driver.PairRuntimeDependencies(**values)

    def execute(
        self,
        *,
        dependencies: driver.PairRuntimeDependencies | None = None,
        authorization: Mapping[str, Any] | None | object = object(),
    ) -> Mapping[str, Any]:
        paid = self.paid_authorization() if type(authorization) is object else authorization
        return driver.execute_pair(
            self.paths,
            paid_authorization=paid,
            dependencies=dependencies or self.dependencies(),
        )

    def load_manifest(self) -> Mapping[str, Any]:
        return json.loads(self.paths.manifest_path.read_text(encoding="utf-8"))

    def test_01_lock_is_acquired_before_authority_load(self) -> None:
        original = driver.controller.load_manifest_after_lock

        def observed_loader(*args: Any, **kwargs: Any) -> Any:
            self.events.append("authority")
            return original(*args, **kwargs)

        with mock.patch.object(
            driver.controller, "load_manifest_after_lock", side_effect=observed_loader
        ):
            self.execute()
        self.assertEqual(self.events[:2], ["lock", "authority"])

    def test_02_wrong_manifest_sha_fails_before_epoch_mutation(self) -> None:
        self.paths.manifest_path.write_bytes(self.paths.manifest_path.read_bytes() + b" ")
        with self.assertRaises(driver.AuthorityPreflightError):
            self.execute()
        self.assertFalse(self.paths.epoch_open_seal.exists())
        self.assertFalse(self.paths.ledger.exists())

    def test_03_invalid_signoff_provenance_hash_is_rejected(self) -> None:
        self.paths.owner_signoff_decision_path.write_bytes(
            self.paths.owner_signoff_decision_path.read_bytes() + b"tamper\n"
        )
        with self.assertRaisesRegex(
            driver.AuthorityPreflightError, "authorization_source_identity"
        ):
            self.execute()
        self.assertFalse(self.paths.epoch_open_seal.exists())

    def test_04_missing_paid_authorization_is_rejected(self) -> None:
        with self.assertRaises(driver.PaidAuthorizationError):
            self.execute(authorization=None)
        self.assertFalse(self.paths.epoch_open_seal.exists())

    def test_05_paid_authorization_wrong_pair_is_rejected(self) -> None:
        authorization = self.paid_authorization()
        authorization["pair_id"] = "P02"
        with self.assertRaises(driver.PaidAuthorizationError):
            self.execute(authorization=authorization)

    def test_06_paid_authorization_wrong_max_spend_is_rejected(self) -> None:
        authorization = self.paid_authorization()
        authorization["maximum_pair_spend_usd"] = "5.0000000"
        with self.assertRaises(driver.PaidAuthorizationError):
            self.execute(authorization=authorization)

    def test_07_budget_ledger_is_initialized_with_7_7339242(self) -> None:
        observed: list[Decimal] = []

        def factory(path: Path, cap: Decimal) -> Any:
            observed.append(cap)
            return driver.controller.BudgetLedger(path, cap)

        self.execute(dependencies=self.dependencies(ledger_factory=factory))
        self.assertEqual(observed, [Decimal("7.7339242")])

    def test_08_lifetime_10_cap_is_rejected_for_new_epoch(self) -> None:
        manifest = copy.deepcopy(self.load_manifest())
        manifest["budget"]["max_additional_spend_usd"] = "10.0000000"
        with self.assertRaisesRegex(driver.PairRecoveryError, "7.7339242"):
            driver.build_budget_ledger(self.paths, manifest)

    def test_09_both_2_usd_reservations_precede_first_arm(self) -> None:
        fake = FakeComposition(self.materializer)
        self.execute(dependencies=self.dependencies(fake))
        first = fake.calls[0]
        self.assertEqual(first["entry_transitions"], ["reservation", "reservation"])
        self.assertEqual(first["outstanding_total"], Decimal("4.0000000"))
        self.assertEqual(len(first["outstanding_slots"]), 2)

    def test_10_no_third_pair_level_reservation_is_created(self) -> None:
        self.execute()
        ledger = driver.controller.BudgetLedger(
            self.paths.ledger, Decimal("7.7339242")
        )
        reservations = [
            entry for entry in ledger.load().entries if entry["transition"] == "reservation"
        ]
        self.assertEqual([entry["amount"] for entry in reservations], ["2.0000000", "2.0000000"])

    def test_11_arm_order_is_exactly_off_then_on(self) -> None:
        fake = FakeComposition(self.materializer)
        self.execute(dependencies=self.dependencies(fake))
        run_ids = driver.execution_builder.p01_run_ids(driver.SEALED_MANIFEST_SHA256)
        self.assertEqual([call["slot_id"] for call in fake.calls], list(run_ids))

    def test_12_fake_provider_full_pair_succeeds(self) -> None:
        aggregate = self.execute()
        self.assertEqual(aggregate["arm_order"], ["OFF", "ON"])
        self.assertEqual(aggregate["total_pair_cost_usd"], "1.2500000")
        self.assertEqual(aggregate["remaining_headroom_usd"], "6.4839242")
        self.assertTrue(self.paths.pair_aggregate.is_file())

    def test_13_run_evidence_round_trips_complete_and_rejects_duplicates(self) -> None:
        self.execute()
        run_id = driver.execution_builder.p01_run_ids(driver.SEALED_MANIFEST_SHA256)[0]
        path = self.paths.run_evidence(run_id)
        persisted = driver.read_run_evidence_record(path, run_id)
        self.assertTrue(persisted.state.complete)
        self.assertEqual(persisted.record["schema"], driver.RUN_EVIDENCE_SCHEMA)
        provider = persisted.record["run_evidence"]["provider_execution"]
        self.assertTrue(provider["argv_lanes"]["physically_sandboxed"])
        self.assertIsNotNone(provider["sandbox_identity"])
        self.assertEqual(provider["total_cost_usd"], "0.5000000")
        self.assertRegex(provider["raw_stdout_sha256"], r"^[0-9a-f]{64}$")

        duplicate = self.root / "duplicate-run-evidence.json"
        raw = path.read_bytes()
        duplicate.write_bytes(raw.replace(b'{"provider_start_entry_sha256"', b'{"schema":"duplicate","provider_start_entry_sha256"', 1))
        with self.assertRaisesRegex(driver.PairDriverError, "duplicate JSON key"):
            driver.read_run_evidence_record(duplicate, run_id)

    def test_14_existing_runtime_state_requires_durable_epoch_open_seal(self) -> None:
        ledger = driver.controller.BudgetLedger(
            self.paths.ledger, Decimal("7.7339242")
        )
        ledger.reserve(
            driver.execution_builder.p01_run_ids(driver.SEALED_MANIFEST_SHA256)[0],
            Decimal("2.0000000"),
        )
        with self.assertRaisesRegex(driver.EpochOpenError, "durable epoch-open seal"):
            self.execute()

    def test_15_reconciliation_cannot_self_prove_current_host_boot(self) -> None:
        seal_dependencies = self.dependencies(
            host_provider=lambda: "sealed-host",
            boot_provider=lambda: "sealed-boot",
        )
        driver.ensure_epoch_open_seal(
            self.paths,
            driver.SEALED_MANIFEST_SHA256,
            seal_dependencies,
            current_host="sealed-host",
            current_boot_id="sealed-boot",
        )
        with self.assertRaisesRegex(driver.PairRecoveryError, "host_identity_mismatch"):
            self.execute()

    def test_16_restart_reuses_off_and_runs_only_unstarted_on(self) -> None:
        first_fake = FakeComposition(self.materializer, costs=("0.5000000", "0.7500000"))
        calls = 0

        def crash_before_second(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SimulatedCrash("between pair members")
            return driver.runner.execute_manifest_slot(*args, **kwargs)

        with self.assertRaises(SimulatedCrash):
            self.execute(
                dependencies=self.dependencies(
                    first_fake, run_slot_executor=crash_before_second
                )
            )
        run_ids = driver.execution_builder.p01_run_ids(driver.SEALED_MANIFEST_SHA256)
        self.assertTrue(self.paths.run_evidence(run_ids[0]).exists())
        self.assertFalse(self.paths.run_evidence(run_ids[1]).exists())
        before = driver.controller.BudgetLedger(
            self.paths.ledger, Decimal("7.7339242")
        ).load()
        self.assertEqual(before.outstanding_reservations[run_ids[1]], Decimal("2.0000000"))

        second_fake = FakeComposition(self.materializer, costs=("0.7500000",))
        aggregate = self.execute(dependencies=self.dependencies(second_fake))
        self.assertEqual([call["slot_id"] for call in first_fake.calls], [run_ids[0]])
        self.assertEqual([call["slot_id"] for call in second_fake.calls], [run_ids[1]])
        self.assertEqual(aggregate["total_pair_cost_usd"], "1.2500000")

    def test_17_started_unresolved_blocks_recovery_without_rerun(self) -> None:
        dependencies = self.dependencies()
        driver.ensure_epoch_open_seal(
            self.paths,
            driver.SEALED_MANIFEST_SHA256,
            dependencies,
            current_host="offline-host",
            current_boot_id="offline-boot",
        )
        ledger = driver.controller.BudgetLedger(
            self.paths.ledger, Decimal("7.7339242")
        )
        run_ids = driver.execution_builder.p01_run_ids(driver.SEALED_MANIFEST_SHA256)
        ledger.reserve(run_ids[0], Decimal("2.0000000"))
        ledger.reserve(run_ids[1], Decimal("2.0000000"))
        ledger.provider_start_intent(run_ids[0], Decimal("2.0000000"))
        fake = FakeComposition(self.materializer)
        with self.assertRaisesRegex(driver.PairRecoveryError, "unresolved_provider_start_intent"):
            self.execute(dependencies=self.dependencies(fake))
        self.assertEqual(fake.calls, [])

    def test_18_spawn_failure_preserves_durable_start_intent(self) -> None:
        fake = FakeComposition(
            self.materializer,
            costs=(None,),
            behaviors=("spawn_failure",),
        )
        with self.assertRaisesRegex(driver.PairExecutionError, "unknown provider charge"):
            self.execute(dependencies=self.dependencies(fake))
        run_ids = driver.execution_builder.p01_run_ids(driver.SEALED_MANIFEST_SHA256)
        snapshot = driver.controller.BudgetLedger(
            self.paths.ledger, Decimal("7.7339242")
        ).load()
        self.assertIn(run_ids[0], snapshot.started_unresolved)
        self.assertIn(run_ids[1], snapshot.outstanding_reservations)
        self.assertIsNone(
            driver.read_run_evidence_record(
                self.paths.run_evidence(run_ids[0]), run_ids[0]
            ).record["terminal"]
        )

    def test_19_known_provider_charge_settles_exactly(self) -> None:
        self.execute()
        snapshot = driver.controller.BudgetLedger(
            self.paths.ledger, Decimal("7.7339242")
        ).load()
        self.assertEqual(snapshot.lifetime_total_spend, Decimal("1.2500000"))
        settlements = [
            entry["amount"]
            for entry in snapshot.entries
            if entry["transition"] == "settlement"
        ]
        self.assertEqual(settlements, ["0.5000000", "0.7500000"])

    def test_20_unknown_charge_is_not_zero_and_blocks_next_arm(self) -> None:
        fake = FakeComposition(self.materializer, costs=(None,), behaviors=("success",))
        with self.assertRaisesRegex(driver.PairExecutionError, "unknown provider charge"):
            self.execute(dependencies=self.dependencies(fake))
        snapshot = driver.controller.BudgetLedger(
            self.paths.ledger, Decimal("7.7339242")
        ).load()
        self.assertEqual(snapshot.lifetime_total_spend, Decimal("0"))
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(self.paths.pair_aggregate.exists())

    def test_21_settlement_failure_blocks_second_arm(self) -> None:
        fake = FakeComposition(self.materializer, costs=("0.5000000",))

        def factory(path: Path, cap: Decimal) -> Any:
            return FailingSettlementLedger(path, cap)

        with self.assertRaisesRegex(driver.PairExecutionError, "settlement failed"):
            self.execute(
                dependencies=self.dependencies(fake, ledger_factory=factory)
            )
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(self.paths.pair_aggregate.exists())

    def test_22_pair_aggregate_is_written_only_after_both_terminal(self) -> None:
        fake = FakeComposition(self.materializer)
        calls = 0

        def stop_after_off(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                self.assertFalse(self.paths.pair_aggregate.exists())
                raise SimulatedCrash("before ON")
            return driver.runner.execute_manifest_slot(*args, **kwargs)

        with self.assertRaises(SimulatedCrash):
            self.execute(
                dependencies=self.dependencies(fake, run_slot_executor=stop_after_off)
            )
        self.assertFalse(self.paths.pair_aggregate.exists())

    def test_23_canonical_output_paths_are_exact(self) -> None:
        paths = driver.EpochPaths.canonical()
        root = Path(
            "/Users/kelvin/fable-ablation/epochs/56a62a62e5aae9fe3a230cbbed831a2a"
        )
        self.assertEqual(paths.ledger, root / "reservations" / "ledger.jsonl")
        self.assertEqual(paths.epoch_open_seal, root / "reservations" / "epoch-open.json")
        self.assertEqual(paths.execution_manifest, root / "runs" / "P01-execution-manifest.json")
        run_id = driver.execution_builder.p01_run_ids(driver.SEALED_MANIFEST_SHA256)[0]
        self.assertEqual(paths.run_evidence(run_id), root / "runs" / run_id / "run-evidence.json")
        self.assertEqual(paths.pair_aggregate, root / "aggregates" / "P01.json")

    def test_24_treatment_identity_never_leaks_into_model_visible_run_identity(self) -> None:
        self.execute()
        for run_id in driver.execution_builder.p01_run_ids(driver.SEALED_MANIFEST_SHA256):
            self.assertNotIn("OFF", run_id)
            self.assertNotIn("ON", run_id)
            persisted = driver.read_run_evidence_record(
                self.paths.run_evidence(run_id), run_id
            )
            visible = persisted.record["run_evidence"]["model_visible"]
            flattened = json.dumps(visible, sort_keys=True)
            self.assertNotIn("FABLE_OFF", flattened)
            self.assertNotIn("FABLE_ON", flattened)
            self.assertNotIn(run_id, flattened)

    def test_25_production_driver_has_no_direct_provider_bypass(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("subprocess.Popen", source)
        self.assertNotIn("import subprocess", source)
        self.assertIn("composition.execute_reserved_provider_slot", source)
        self.assertIn("runner.execute_manifest_slot", source)
        self.assertIn("controller.BudgetLedger", source)

    def test_26_persisted_on_only_reservation_order_is_rejected(self) -> None:
        fake = FakeComposition(self.materializer)
        dependencies = self.dependencies(fake)
        driver.ensure_epoch_open_seal(
            self.paths,
            driver.SEALED_MANIFEST_SHA256,
            dependencies,
            current_host="offline-host",
            current_boot_id="offline-boot",
        )
        ledger = driver.controller.BudgetLedger(
            self.paths.ledger, Decimal("7.7339242")
        )
        run_ids = driver.execution_builder.p01_run_ids(
            driver.SEALED_MANIFEST_SHA256
        )
        ledger.reserve(run_ids[1], Decimal("2.0000000"))
        before = ledger.load()

        with self.assertRaisesRegex(
            driver.PairRecoveryError, "exact OFF-to-ON prefix"
        ):
            self.execute(dependencies=dependencies)

        after = ledger.load()
        self.assertEqual(after.entries, before.entries)
        self.assertEqual(fake.calls, [])

    def test_27_run_evidence_directory_entries_fsync_runs_parent(self) -> None:
        calls: list[Path] = []
        original = driver._fsync_directory

        def recording_fsync(path: Path) -> None:
            calls.append(path)
            original(path)

        with mock.patch.object(
            driver, "_fsync_directory", side_effect=recording_fsync
        ):
            self.execute()

        run_ids = driver.execution_builder.p01_run_ids(
            driver.SEALED_MANIFEST_SHA256
        )
        self.assertEqual(calls.count(self.paths.runs), 3)
        for run_id in run_ids:
            run_directory = self.paths.runs / run_id
            child_fsync_index = calls.index(run_directory)
            self.assertGreater(child_fsync_index, 0)
            self.assertEqual(calls[child_fsync_index - 1], self.paths.runs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
