#!/usr/bin/env python3
"""Offline acceptance tests for the sealed R2 execution controller."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from unittest import mock


sys.dont_write_bytecode = True
MODULE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_ROOT))
import execution_controller as controller  # noqa: E402
import runner  # noqa: E402


LOGICAL_R2_ROOT = Path("/Users/kelvin/fable-ablation-r2")
REAL_PROVIDER = "/Users/kelvin/.local/bin/claude"


class StaticExecutorProbe:
    def __init__(self, *, mismatch: str | None = None) -> None:
        self.mismatch = mismatch
        self.calls = 0

    def probe(self, authority: Any) -> Any:
        self.calls += 1
        values = {
            "head": authority.executor_head,
            "tree": authority.executor_tree,
            "source_sha256": authority.executor_source_sha256,
            "test_sha256": authority.executor_test_sha256,
            "runtime_source_sha256": authority.executor_source_sha256,
        }
        if self.mismatch is not None:
            values[self.mismatch] = "0" * 64
        return controller.ExecutorIdentity(**values)


class FakeProvider:
    executable = REAL_PROVIDER

    def __init__(
        self,
        version: str,
        *,
        results: list[Any] | None = None,
        before_invoke: Callable[[Any], None] | None = None,
    ) -> None:
        self.version = version
        self.results = list(results or [])
        self.before_invoke = before_invoke
        self.version_calls = 0
        self.invocations: list[Any] = []

    def verify_version(self) -> str:
        self.version_calls += 1
        return self.version

    def invoke(self, invocation: Any) -> Any:
        self.invocations.append(invocation)
        if self.before_invoke is not None:
            self.before_invoke(invocation)
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return provider_result("PASS", "0.1")


def provider_result(
    outcome: str = "PASS",
    cost: str | None = "0.1",
    *,
    cost_status: str | None = None,
) -> Any:
    resolved_status = cost_status or ("KNOWN" if cost is not None else "UNRESOLVED")
    return controller.ProviderResult(
        outcome=outcome,
        cost_status=resolved_status,
        exact_cost=cost,
        raw_evidence=(f"offline:{outcome}:{cost}\n").encode("utf-8"),
        canonical_evidence={"offline": True, "outcome": outcome},
        result_record={"total_cost_usd": cost, "source": "offline-fake"},
    )


class OfflineR2:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "mapped-r2"
        self.root.mkdir(mode=0o700)
        shutil.copy2(LOGICAL_R2_ROOT / "manifest.json", self.root / "manifest.json")
        shutil.copy2(
            LOGICAL_R2_ROOT / ".writer-lease.json",
            self.root / ".writer-lease.json",
        )
        (self.root / ".writer-lease.json").chmod(0o600)
        shutil.copytree(LOGICAL_R2_ROOT / "fixtures", self.root / "fixtures")
        shutil.copytree(LOGICAL_R2_ROOT / "oracles", self.root / "oracles")
        (self.root / "runs").mkdir(mode=0o700)
        (self.root / "workspaces").mkdir(mode=0o700)
        self.manifest = json.loads(
            (self.root / "manifest.json").read_text(encoding="utf-8")
        )
        self.storage = controller.RootMapping(LOGICAL_R2_ROOT, self.root)
        self.authorization = controller.ExecutionAuthorization(
            manifest_sha256=controller.REQUIRED_MANIFEST_SHA256,
            epoch_id=self.manifest["authority"]["epoch_id"],
            authorization_source=self.manifest["authority"]["authorization_source"],
            owner_hash_signoff=True,
            paid_execution_authorized=True,
        )
        self.provider_version = self.manifest["provider"]["version"]
        self.invocation_counter = 0
        self.open_controllers: list[Any] = []

    def close(self) -> None:
        for active in reversed(self.open_controllers):
            active.close()
        self.open_controllers.clear()
        self.temporary.cleanup()

    def new_controller(
        self,
        *,
        provider: FakeProvider | None = None,
        probe: StaticExecutorProbe | None = None,
        event_sink: Callable[[str], None] | None = None,
        authorization: Any = None,
    ) -> tuple[Any, FakeProvider]:
        selected_provider = provider or FakeProvider(self.provider_version)
        instance = controller.ExecutionController(
            manifest_path=LOGICAL_R2_ROOT / "manifest.json",
            storage=self.storage,
            authorization=authorization or self.authorization,
            provider=selected_provider,
            executor_probe=probe or StaticExecutorProbe(),
            event_sink=event_sink,
        )
        self.open_controllers.append(instance)
        return instance, selected_provider

    def invocation(self, run_id: str) -> Any:
        self.invocation_counter += 1
        opaque = f"{self.invocation_counter:032x}"
        workspace = self.root / "workspaces" / f"session-{opaque}"
        workspace.mkdir(mode=0o700)
        slot = runner.select_manifest_slot(self.manifest, run_id)
        return runner.build_model_invocation(slot, workspace, opaque)

    def ledger(self) -> dict[str, Any]:
        return json.loads((self.root / "execution-ledger.json").read_text(encoding="utf-8"))

    def state(self) -> dict[str, Any]:
        return json.loads(
            (self.root / ".lease-execution-state.json").read_text(encoding="utf-8")
        )


class ExecutionControllerOfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.offline = OfflineR2()

    def tearDown(self) -> None:
        self.offline.close()

    def _complete_pair(
        self,
        active: Any,
        pair_index: int,
        costs: tuple[str, str] = ("0.1", "0.1"),
    ) -> None:
        keys = active.reserve_pair(pair_index)
        provider = active.provider
        provider.results.extend(
            [provider_result("PASS", costs[0]), provider_result("PASS", costs[1])]
        )
        for key in keys:
            active.invoke(key, self.offline.invocation(key.run_id))
        active.complete_pair(pair_index)

    def test_01_manifest_sha_mismatch_fails_before_lock_or_provider(self) -> None:
        manifest_path = self.offline.root / "manifest.json"
        manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
        active, provider = self.offline.new_controller()

        with self.assertRaises(controller.ManifestIdentityError):
            active.start()

        self.assertEqual(provider.version_calls, 0)
        self.assertFalse((self.offline.root / "execution-ledger.json").exists())
        self.assertFalse((self.offline.root / ".lease-execution-state.json").exists())

    def test_02_nonblocking_writer_lock_contention_fails_closed(self) -> None:
        owner, _ = self.offline.new_controller()
        owner.start()
        contender, contender_provider = self.offline.new_controller()

        with self.assertRaises(controller.WriterLockBusy):
            contender.start()

        self.assertEqual(contender_provider.version_calls, 0)
        self.assertEqual(self.offline.state()["state"], "EXECUTING")

    def test_03_first_entry_initializes_ledger_before_executing_state(self) -> None:
        events: list[str] = []
        active, provider = self.offline.new_controller(event_sink=events.append)

        result = active.start()

        self.assertTrue(result.first_entry)
        self.assertEqual(result.reconciliation, "FIRST_ENTRY")
        self.assertLess(events.index("ledger:initialized"), events.index("state:EXECUTING"))
        ledger = self.offline.ledger()
        self.assertEqual(ledger["revision"], 0)
        self.assertEqual(ledger["known_spend_usd"], "0")
        self.assertEqual(ledger["unreleased_reservation_usd"], "0")
        self.assertEqual(self.offline.state()["state"], "EXECUTING")
        self.assertEqual(provider.invocations, [])

    def test_04_dirty_first_entry_leaves_logical_state_sealed(self) -> None:
        (self.offline.root / "runs" / "ambiguous-preexisting").write_text(
            "dirty\n", encoding="utf-8"
        )
        active, provider = self.offline.new_controller()

        with self.assertRaises(controller.EntryPreconditionError):
            active.start()

        self.assertFalse((self.offline.root / "execution-ledger.json").exists())
        self.assertFalse((self.offline.root / ".lease-execution-state.json").exists())
        self.assertEqual(provider.invocations, [])

    def test_05_pair_reservation_and_intent_are_durable_before_fake_call(self) -> None:
        events: list[str] = []
        observed: dict[str, Any] = {}

        def before_call(_: Any) -> None:
            events.append("fake_provider_called")
            ledger = self.offline.ledger()
            attempt = ledger["attempts"][0]
            observed["status"] = attempt["status"]
            observed["intent"] = attempt["durable_intent"]
            observed["reserved"] = attempt["reserved_amount"]
            observed["intent_exists"] = self.offline.storage.physical(
                Path(attempt["durable_intent"]["path"])
            ).is_file()

        provider = FakeProvider(self.offline.provider_version, before_invoke=before_call)
        active, _ = self.offline.new_controller(
            provider=provider, event_sink=events.append
        )
        active.start()
        first, _ = active.reserve_pair(1)

        active.invoke(first, self.offline.invocation(first.run_id))

        self.assertEqual(observed["status"], "INTENT_PERSISTED")
        self.assertEqual(observed["reserved"], "2")
        self.assertTrue(observed["intent_exists"])
        self.assertLess(events.index("ledger:pair_reservation"), events.index("fake_provider_called"))
        self.assertLess(events.index("ledger:invocation_intent"), events.index("fake_provider_called"))
        self.assertLess(events.index("invocation_intent_file_persisted"), events.index("fake_provider_called"))

    def test_06_exact_decimal_cumulative_accounting_has_no_float_drift(self) -> None:
        provider = FakeProvider(
            self.offline.provider_version,
            results=[provider_result("PASS", "0.1"), provider_result("PASS", "0.2")],
        )
        active, _ = self.offline.new_controller(provider=provider)
        active.start()
        keys = active.reserve_pair(1)
        for key in keys:
            active.invoke(key, self.offline.invocation(key.run_id))
        active.complete_pair(1)

        ledger = self.offline.ledger()
        self.assertEqual(ledger["known_spend_usd"], "0.3")
        self.assertEqual(Decimal(ledger["known_spend_usd"]), Decimal("0.3"))
        self.assertEqual(ledger["unreleased_reservation_usd"], "0")

    def test_07_unresolved_provider_cost_is_recorded_and_never_zero(self) -> None:
        provider = FakeProvider(
            self.offline.provider_version,
            results=[provider_result("INFRA_ERROR", None)],
        )
        active, _ = self.offline.new_controller(provider=provider)
        active.start()
        first, _ = active.reserve_pair(1)

        with self.assertRaises(controller.UnresolvedProviderCost):
            active.invoke(first, self.offline.invocation(first.run_id))

        attempt = self.offline.ledger()["attempts"][0]
        self.assertEqual(attempt["cost_status"], "UNRESOLVED")
        self.assertIsNone(attempt["exact_cost"])
        self.assertNotEqual(attempt["exact_cost"], "0")
        self.assertEqual(attempt["unreleased_amount"], "2")
        self.assertEqual(
            self.offline.ledger()["unresolved_cost_items"],
            [{"run_id": first.run_id, "attempt_ordinal": 1}],
        )
        self.assertEqual(self.offline.state()["state"], "ABORTED")

    def test_08_restart_preserves_existing_nonzero_ledger(self) -> None:
        active, _ = self.offline.new_controller()
        active.start()
        first, _ = active.reserve_pair(1)
        active.provider.results.append(provider_result("PASS", "0.4"))
        active.invoke(first, self.offline.invocation(first.run_id))
        active.close()
        before = self.offline.ledger()

        restarted, restarted_provider = self.offline.new_controller()
        result = restarted.start()

        self.assertEqual(result.reconciliation, "CRASH_CASE_4")
        self.assertEqual(self.offline.ledger()["known_spend_usd"], "0.4")
        self.assertEqual(self.offline.ledger()["budget_authority"], before["budget_authority"])
        self.assertEqual(restarted_provider.invocations, [])

    def test_09_only_one_terminal_infra_error_retry_is_eligible(self) -> None:
        provider = FakeProvider(
            self.offline.provider_version,
            results=[
                provider_result("INFRA_ERROR", "0.1"),
                provider_result("PASS", "0.2"),
            ],
        )
        active, _ = self.offline.new_controller(provider=provider)
        active.start()
        first, second_member = active.reserve_pair(1)
        active.invoke(first, self.offline.invocation(first.run_id))
        self.assertTrue(active.retry_eligible(first.run_id))
        with self.assertRaises(controller.RetryNotAllowed):
            active.invoke(second_member, self.offline.invocation(second_member.run_id))

        retry = active.reserve_retry(first.run_id)
        active.invoke(retry, self.offline.invocation(retry.run_id))

        self.assertFalse(active.retry_eligible(first.run_id))
        with self.assertRaises(controller.RetryNotAllowed):
            active.reserve_retry(first.run_id)
        attempts = [
            item for item in self.offline.ledger()["attempts"] if item["run_id"] == first.run_id
        ]
        self.assertEqual([item["attempt_ordinal"] for item in attempts], [1, 2])
        self.assertEqual(self.offline.ledger()["known_spend_usd"], "0.3")

    def test_10_duplicate_restart_is_read_only_and_deterministic(self) -> None:
        active, _ = self.offline.new_controller()
        active.start()
        active.close()
        ledger_path = self.offline.root / "execution-ledger.json"
        state_path = self.offline.root / ".lease-execution-state.json"
        before = (ledger_path.read_bytes(), state_path.read_bytes())

        first_restart, _ = self.offline.new_controller()
        first_result = first_restart.start()
        first_restart.close()
        second_restart, _ = self.offline.new_controller()
        second_result = second_restart.start()
        second_restart.close()

        self.assertEqual(first_result.reconciliation, "CRASH_CASE_2")
        self.assertEqual(second_result.reconciliation, "CRASH_CASE_2")
        self.assertEqual((ledger_path.read_bytes(), state_path.read_bytes()), before)

    def test_11_crash_case_1_restores_only_missing_executing_sidecar(self) -> None:
        active, _ = self.offline.new_controller()
        active.start()
        active.close()
        state_path = self.offline.root / ".lease-execution-state.json"
        state_path.unlink()
        ledger_before = (self.offline.root / "execution-ledger.json").read_bytes()

        restarted, provider = self.offline.new_controller()
        result = restarted.start()

        self.assertEqual(result.reconciliation, "CRASH_CASE_1")
        self.assertEqual(
            (self.offline.root / "execution-ledger.json").read_bytes(), ledger_before
        )
        self.assertEqual(self.offline.state()["state"], "EXECUTING")
        self.assertEqual(provider.invocations, [])

    def test_12_crash_case_2_resumes_pristine_executing_state_without_call(self) -> None:
        active, _ = self.offline.new_controller()
        active.start()
        active.close()

        restarted, provider = self.offline.new_controller()
        result = restarted.start()

        self.assertEqual(result.reconciliation, "CRASH_CASE_2")
        self.assertEqual(result.ledger_revision, 0)
        self.assertEqual(provider.invocations, [])

    def test_13_crash_during_invocation_becomes_orphaned_abort_only(self) -> None:
        events: list[str] = []

        def crash_after_intent(event: str) -> None:
            events.append(event)
            if event == "ledger:invocation_intent":
                raise RuntimeError("simulated crash before provider boundary")

        active, provider = self.offline.new_controller(event_sink=crash_after_intent)
        active.start()
        first, _ = active.reserve_pair(1)
        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            active.invoke(first, self.offline.invocation(first.run_id))
        active.close()
        self.assertEqual(provider.invocations, [])

        restarted, restart_provider = self.offline.new_controller()
        with self.assertRaisesRegex(controller.RecoveryAborted, "CRASH_CASE_3"):
            restarted.start()

        attempt = self.offline.ledger()["attempts"][0]
        self.assertEqual(attempt["status"], "ORPHANED")
        self.assertEqual(attempt["cost_status"], "UNRESOLVED")
        self.assertEqual(self.offline.state()["state"], "ABORTED")
        self.assertEqual(restart_provider.invocations, [])

    def test_14_pair_checkpoint_cannot_precede_both_terminal_members(self) -> None:
        active, _ = self.offline.new_controller()
        active.start()
        first, second = active.reserve_pair(1)
        active.invoke(first, self.offline.invocation(first.run_id))
        with self.assertRaises(controller.OrderingError):
            active.complete_pair(1)
        self.assertEqual(self.offline.ledger()["pair_checkpoints"], [])

        active.invoke(second, self.offline.invocation(second.run_id))
        active.complete_pair(1)

        checkpoint = self.offline.ledger()["pair_checkpoints"][0]
        self.assertEqual(checkpoint["pair_index"], 1)
        self.assertEqual(
            [item["run_id"] for item in checkpoint["exact_two_members"]],
            [first.run_id, second.run_id],
        )
        self.assertEqual(len(checkpoint["terminal_commit_references"]), 2)

    def test_15_budget_cap_persists_and_next_pair_cannot_reset_it(self) -> None:
        provider = FakeProvider(self.offline.provider_version)
        active, _ = self.offline.new_controller(provider=provider)
        active.start()
        self._complete_pair(active, 1, ("2", "2"))
        self._complete_pair(active, 2, ("2", "2"))

        with self.assertRaises(controller.BudgetExhausted):
            active.reserve_pair(3)

        ledger = self.offline.ledger()
        self.assertEqual(ledger["known_spend_usd"], "8")
        self.assertEqual(ledger["budget_authority"]["total_usd"], "10")
        self.assertEqual(ledger["budget_exhausted_pairs"], [3])
        active.close()
        restarted, _ = self.offline.new_controller()
        restarted.start()
        with self.assertRaises(controller.BudgetExhausted):
            restarted.reserve_pair(3)
        self.assertEqual(self.offline.ledger()["known_spend_usd"], "8")

    def test_16_executor_head_tree_source_mismatch_blocks_initialization(self) -> None:
        active, provider = self.offline.new_controller(
            probe=StaticExecutorProbe(mismatch="tree")
        )

        with self.assertRaises(controller.ExecutorIdentityError):
            active.start()

        self.assertEqual(provider.invocations, [])
        self.assertFalse((self.offline.root / "execution-ledger.json").exists())

    def test_17_fixture_oracle_identity_mismatch_blocks_initialization(self) -> None:
        fixture = self.offline.root / "fixtures" / "A01.tar.gz"
        fixture.write_bytes(fixture.read_bytes() + b"tamper")
        active, provider = self.offline.new_controller()

        with self.assertRaises(controller.AuthorityConflict):
            active.start()

        self.assertEqual(provider.invocations, [])
        self.assertFalse((self.offline.root / "execution-ledger.json").exists())

    def test_18_provider_version_mismatch_never_reaches_inference(self) -> None:
        provider = FakeProvider("0.0.0 (Offline Wrong Version)")
        active, _ = self.offline.new_controller(provider=provider)

        with self.assertRaises(controller.ProviderIdentityError):
            active.start()

        self.assertEqual(provider.version_calls, 1)
        self.assertEqual(provider.invocations, [])
        self.assertFalse((self.offline.root / "execution-ledger.json").exists())

    def test_19_no_real_provider_or_subprocess_inference_occurs(self) -> None:
        provider = FakeProvider(self.offline.provider_version)
        active, _ = self.offline.new_controller(provider=provider)

        with mock.patch.object(
            controller.subprocess,
            "run",
            side_effect=AssertionError("subprocess boundary must remain offline"),
        ):
            active.start()
            first, _ = active.reserve_pair(1)
            active.invoke(first, self.offline.invocation(first.run_id))

        self.assertEqual(len(provider.invocations), 1)
        self.assertEqual(provider.executable, REAL_PROVIDER)
        self.assertIs(provider.invocations[0].__class__, runner.ModelInvocation)

    def test_20_conflicting_incomplete_ledger_fails_closed_without_repair(self) -> None:
        active, _ = self.offline.new_controller()
        active.start()
        active.close()
        ledger_path = self.offline.root / "execution-ledger.json"
        corrupted = self.offline.ledger()
        corrupted["known_spend_usd"] = "0.01"
        ledger_path.write_text(json.dumps(corrupted), encoding="utf-8")
        corrupted_bytes = ledger_path.read_bytes()

        restarted, provider = self.offline.new_controller()
        with self.assertRaises(controller.AuthorityConflict):
            restarted.start()

        self.assertEqual(ledger_path.read_bytes(), corrupted_bytes)
        self.assertEqual(provider.invocations, [])

    def test_21_crash_between_pairs_resumes_only_next_pair(self) -> None:
        active, _ = self.offline.new_controller()
        active.start()
        self._complete_pair(active, 1)
        active.close()

        restarted, provider = self.offline.new_controller()
        result = restarted.start()

        self.assertEqual(result.reconciliation, "CRASH_CASE_5")
        pair_two = restarted.reserve_pair(2)
        self.assertEqual(pair_two[0].run_id, self.offline.manifest["schedule"][2]["run_id"])
        self.assertEqual(provider.invocations, [])

    def test_22_explicit_known_zero_cost_is_distinct_from_unknown(self) -> None:
        provider = FakeProvider(
            self.offline.provider_version,
            results=[provider_result("INFRA_ERROR", "0")],
        )
        active, _ = self.offline.new_controller(provider=provider)
        active.start()
        first, _ = active.reserve_pair(1)

        active.invoke(first, self.offline.invocation(first.run_id))

        attempt = self.offline.ledger()["attempts"][0]
        self.assertEqual(attempt["cost_status"], "KNOWN")
        self.assertEqual(attempt["exact_cost"], "0")
        self.assertTrue(active.retry_eligible(first.run_id))

    def test_23_default_git_probe_verifies_pinned_head_tree_and_loaded_source(self) -> None:
        provider = FakeProvider(self.offline.provider_version)
        active = controller.ExecutionController(
            manifest_path=LOGICAL_R2_ROOT / "manifest.json",
            storage=self.offline.storage,
            authorization=self.offline.authorization,
            provider=provider,
        )
        self.offline.open_controllers.append(active)

        result = active.start()

        self.assertTrue(result.first_entry)
        self.assertEqual(provider.invocations, [])

    def test_24_provider_path_mismatch_blocks_before_version_or_inference(self) -> None:
        provider = FakeProvider(self.offline.provider_version)
        provider.executable = "/offline/wrong-provider"
        active, _ = self.offline.new_controller(provider=provider)

        with self.assertRaises(controller.ProviderIdentityError):
            active.start()

        self.assertEqual(provider.version_calls, 0)
        self.assertEqual(provider.invocations, [])

    def test_25_missing_direct_paid_authorization_leaves_state_sealed(self) -> None:
        denied = controller.ExecutionAuthorization(
            manifest_sha256=controller.REQUIRED_MANIFEST_SHA256,
            epoch_id=self.offline.authorization.epoch_id,
            authorization_source=self.offline.authorization.authorization_source,
            owner_hash_signoff=True,
            paid_execution_authorized=False,
        )
        active, provider = self.offline.new_controller(authorization=denied)

        with self.assertRaises(controller.AuthorityConflict):
            active.start()

        self.assertEqual(provider.version_calls, 0)
        self.assertEqual(provider.invocations, [])
        self.assertFalse((self.offline.root / "execution-ledger.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
