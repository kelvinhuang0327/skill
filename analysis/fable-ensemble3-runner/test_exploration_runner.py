#!/usr/bin/env python3
"""Offline tests for the formal exploration runner.  No socket is ever opened and no model is ever called.

The sealed engineering bank (8 items) and a simulated Responses endpoint exercise the real adapter, ledger, checker and
scorer.  Every fault is injected at the provider boundary; a real child process is SIGKILLed mid-call for the crash test.
Run: python3 -B -m unittest -q test_exploration_runner
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import io
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _lab  # noqa: E402,F401
import budget_ledger as bl  # noqa: E402
import build_engineering_bank as eb  # noqa: E402
import decision_engine as engine  # noqa: E402
import execution_profile as ep  # noqa: E402
import exploration_runner as xr  # noqa: E402
import offline_replay as replay  # noqa: E402
import responses_adapter as api  # noqa: E402
from simulated_transport import SimulatedResponses  # noqa: E402

CAP = Decimal("0.55")
OK_COST = Decimal("0.000396")  # (1200 in x 0.25 + 80 out x 1.20) / 1e6, the simulated server's default usage
LABEL_KEYS = {"acceptable", "unsafe", "oracle", "oracle_ref", "oracle_sha256", "expected_answer", "rationale"}


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        eb.build(self.root / "bank")
        self.manifest = self.root / "bank" / "manifest.json"
        _, self.public, self.labels = xr.load_bank(self.manifest)
        # the whole suite runs with the network cut: any attempt to reach it is a test failure
        for target in ("urllib.request.urlopen", "socket.socket.connect"):
            patcher = mock.patch(target, side_effect=AssertionError("the network must never be touched in tests"))
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- builders ----------------------------------------------------------------------------------------------------
    def answer(self, item):
        return self.labels[item["item_id"]]["acceptable"][0]

    def make(self, name="run", *, faults=(), budget="50", rounds=5, seed=6302, instructions="synthetic instructions", log=True, **sim_kwargs):
        sim = SimulatedResponses(self.public, answer_for=self.answer, faults=faults,
                                 log_path=(self.root / f"{name}-server.jsonl") if log else None, **sim_kwargs)
        runner = xr.Runner(self.root / name, mode="SIMULATION", bank_manifest=self.manifest, instructions=instructions, budget=budget,
                           attempt_cap=CAP, target_rounds=rounds, seed=seed, transport=sim)
        return runner, sim

    def run_it(self, name="run", **kwargs):
        runner, sim = self.make(name, **kwargs)
        result = runner.start()
        return result, runner, sim

    # --- readers -----------------------------------------------------------------------------------------------------
    def ledger(self, name="run") -> dict:
        return json.loads((self.root / name / "ledger.json").read_text())

    def slot(self, item_id, round_index=0, name="run") -> dict:
        return self.ledger(name)["slots"][f"{item_id}:{round_index}"]

    def attempts(self, name="run"):
        return xr.AppendLog(self.root / name / "attempts.jsonl").records()

    def generations(self, sim, item_id):
        return [r for r in sim.requests if r["path"] == "responses" and r["item_id"] == item_id]

    def counts(self, sim, item_id):
        return [r for r in sim.requests if r["path"] == "responses/input_tokens" and r["item_id"] == item_id]

    def first_item(self, runner) -> str:
        return runner.order[0]

    def assertBlocks(self, regex, call, exception=xr.RunBlocked):
        """`call` must raise `exception` with `regex` in its text.  Any other outcome is a failed assertion, not a crash:
        a guard that is gone must not be mistaken for a test that broke."""
        try:
            call()
        except exception as caught:
            self.assertRegex(str(caught), regex)
        except Exception as other:
            self.fail(f"expected {exception.__name__} matching {regex!r}, got {type(other).__name__}: {other}")
        else:
            self.fail(f"expected {exception.__name__} matching {regex!r}: nothing was raised")

    def completes(self, call):
        """`call` must return.  A raised error is a failed assertion here, not a crash: the run-state gate turns several defects
        into a loud RunBlocked at the end of a run, and the mutation rule counts an assertion, never an ERROR."""
        try:
            return call()
        except Exception as exc:
            self.fail(f"expected the call to return, got {type(exc).__name__}: {exc}")

    def crashes(self, call):
        """`call` must end in the injected KeyboardInterrupt.  A refusal or any other error is a failed assertion, not a crash."""
        try:
            call()
        except KeyboardInterrupt:
            return
        except Exception as exc:
            self.fail(f"expected the injected crash, got {type(exc).__name__}: {exc}")
        self.fail("expected the injected crash: the call returned")


class CleanRunTests(RunnerCase):
    def test_a_clean_run_settles_every_slot_and_replays_identically(self):
        result, runner, sim = self.run_it()
        self.assertEqual((result["state"], result["halt"]), ("COMPLETE", None))
        state = self.ledger()
        self.assertEqual(len(state["slots"]), 40)
        self.assertTrue(all(s["state"] == "FINAL" and s["status"] == "OK" and s["held"] == "0" for s in state["slots"].values()))
        self.assertEqual(len(sim.requests), 80)  # one input count and one generation per slot, nothing else
        for item_id in self.public:
            self.assertEqual((len(self.counts(sim, item_id)), len(self.generations(sim, item_id))), (5, 5))
        self.assertEqual(Decimal(state["spent_upper"]), 40 * OK_COST)
        replayed = xr.replay_run(self.root / "run")
        self.assertIs(replayed["matches_saved_runs"], True)
        self.assertEqual(replayed["report"], result["report"])  # every field, floats included
        self.assertEqual(result["report"]["kind"], "SIMULATION_NOT_A_RESULT")
        self.assertIs(result["report"]["statistical_claims_allowed"], False)
        self.assertEqual(result["report"]["run_statuses"], {"OK": 40})

    def test_the_ledger_total_equals_the_settled_costs_in_the_attempt_log(self):
        faults = [{"path": "responses", "item_id": "eng-foreign-writer", "occurrence": 1, "fault": "timeout"}]
        result, _, _ = self.run_it(faults=faults)
        settled = [r for r in self.attempts() if r["phase"] == "SETTLED"]
        total = sum((CAP if r["cost_upper_usd"] is None else Decimal(r["cost_upper_usd"])) for r in settled)
        self.assertEqual(Decimal(self.ledger()["spent_upper"]), total)
        self.assertEqual(sum(1 for r in settled if r["charged_cap_because_unknown"]), 1)

    def test_the_provider_only_ever_sees_the_public_item_and_the_pinned_instructions(self):
        _, _, sim = self.run_it(instructions="the pinned pack")
        pack_sha = xr.sha256_text("the pinned pack")
        for request in sim.requests:
            sent = json.loads(request["input"])
            self.assertFalse(LABEL_KEYS & set(sent), request["item_id"])
            self.assertEqual(request["input"], json.dumps(self.public[request["item_id"]], sort_keys=True, separators=(",", ":")))
            self.assertEqual(request["instructions_sha256"], pack_sha)

    def test_the_schedule_is_fixed_by_the_seed_and_identical_in_every_round(self):
        _, runner, sim = self.run_it(seed=6302)
        generation_order = [r["item_id"] for r in sim.requests if r["path"] == "responses"]
        expected = sorted(self.public)
        random.Random(6302).shuffle(expected)
        self.assertEqual(runner.order, expected)
        self.assertEqual(generation_order, expected * 5)
        other, other_sim = self.make("other", seed=7)
        other.start()
        self.assertNotEqual([r["item_id"] for r in other_sim.requests if r["path"] == "responses"][:8], expected)

    def test_the_attempt_log_orders_start_request_response_and_settlement(self):
        _, runner, _ = self.run_it()
        item = self.first_item(runner)
        rows = [r for r in self.attempts() if r.get("slot") == f"{item}:0"]
        self.assertEqual([(r["phase"], r.get("path")) for r in rows], [
            ("START", None), ("REQUEST", "responses/input_tokens"), ("RESPONSE", "responses/input_tokens"),
            ("REQUEST", "responses"), ("RESPONSE", "responses"), ("SETTLED", None)])
        request = rows[3]
        self.assertNotIn("instructions", request["body"])  # the 200 KB pack is recorded by hash, not repeated per call
        self.assertEqual(request["instructions_sha256"], xr.sha256_text("synthetic instructions"))
        sent = api.build_request(self.public[item], "synthetic instructions")  # exactly what the adapter hands to its transport
        self.assertEqual(request["body_sha256"], xr.sha256_text(json.dumps(sent)))

    def test_every_write_stays_inside_the_run_directory_and_is_accounted(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.run_it()
        out = self.root / "run"
        after_outside = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file() and out not in p.parents and p.name != "run-server.jsonl"}
        self.assertEqual(before, after_outside)
        listing = json.loads((out / "write_ledger.json").read_text())
        on_disk = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and p.name != "write_ledger.json")
        self.assertEqual(sorted(f["path"] for f in listing["files"]), on_disk)
        for entry in listing["files"]:
            self.assertEqual(ep.sha256_file(out / entry["path"]), entry["sha256"])
        self.assertEqual(listing["writes_outside_out_dir"], [])
        self.assertEqual([p for p in out.rglob("*.tmp")], [])

    def test_no_network_was_touched(self):
        self.run_it()
        self.assertEqual(urllib_calls(), 0)


def urllib_calls() -> int:
    import urllib.request
    return getattr(urllib.request.urlopen, "call_count", 0)


class BankLoadingTests(RunnerCase):
    def test_a_bank_changed_after_it_was_frozen_is_refused(self):
        row = json.loads(self.manifest.read_text())["items"][0]
        public = Path(row["public_ref"])
        original = public.read_bytes()
        try:
            public.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "fixture or oracle changed after freeze"):
                xr.load_bank(self.manifest)
        finally:
            public.write_bytes(original)
        oracle = Path(row["oracle_ref"])
        original = oracle.read_bytes()
        try:
            oracle.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "fixture or oracle changed after freeze"):
                xr.load_bank(self.manifest)
        finally:
            oracle.write_bytes(original)
        self.assertEqual(len(xr.load_bank(self.manifest)[1]), 8)

    def test_an_oracle_that_does_not_bind_its_fixture_is_refused_even_when_its_own_hash_is_updated(self):
        manifest = json.loads(self.manifest.read_text())
        row = manifest["items"][0]
        oracle = Path(row["oracle_ref"])
        label = json.loads(oracle.read_text())
        label["public_sha256"] = "0" * 64  # a label written for some other fixture
        oracle.write_text(json.dumps(label))
        row["oracle_sha256"] = ep.sha256_file(oracle)
        other = self.root / "rebound.json"
        other.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "oracle does not bind this fixture"):
            xr.load_bank(other)

    def test_an_unknown_bank_purpose_is_refused(self):
        manifest = json.loads(self.manifest.read_text())
        manifest["purpose"] = "SOMETHING_ELSE"
        other = self.root / "purpose.json"
        other.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "unknown bank purpose"):
            xr.load_bank(other)
        for purpose in xr.BANK_PURPOSES:
            manifest["purpose"] = purpose
            other.write_text(json.dumps(manifest))
            self.assertEqual(len(xr.load_bank(other)[1]), 8)


class PlanningTests(RunnerCase):
    def test_rounds_are_whole_and_below_three_never_start(self):
        round_cost = Decimal(8) * 2 * CAP  # 8.80
        for budget, planned in ((round_cost * 5, 5), (round_cost * 4, 4), (round_cost * 3, 3), (round_cost * 3 + Decimal("8.79"), 3)):
            runner, _ = self.make(f"b{planned}-{budget}", budget=str(budget))
            result = runner.start()
            self.assertEqual(result["report"]["planned_rounds"], planned, budget)
            self.assertEqual(result["report"]["rounds_reserved"], planned)
            self.assertEqual(len(self.ledger(f"b{planned}-{budget}")["slots"]), 8 * planned)
        for budget in (round_cost * 3 - Decimal("0.01"), Decimal(0), round_cost):
            runner, sim = self.make(f"low-{budget}", budget=str(budget))
            with self.assertRaisesRegex(xr.RunBlocked, "BUDGET_BELOW_THREE_ROUNDS"):
                runner.start()
            self.assertEqual(sim.requests, [])

    def test_a_live_formal_run_needs_every_round_reserved_while_an_engineering_run_may_plan_fewer(self):
        round_cost = Decimal(8) * 2 * CAP
        formal, _ = self.make("formal4", budget=str(round_cost * 4))
        formal.purpose, formal.mode = "FORMAL_DECISION_EXPLORATION", "LIVE"
        with self.assertRaisesRegex(xr.RunBlocked, "WHOLE_ROUNDS_NOT_RESERVED"):
            formal._plan()
        formal.budget = round_cost * 5
        self.assertEqual(formal._plan(), 5)
        engineering, _ = self.make("eng4", budget=str(round_cost * 4))
        engineering.mode = "LIVE"
        self.assertEqual(engineering._plan(), 4)  # the purpose, not the mode alone, decides
        simulated, _ = self.make("sim4", budget=str(round_cost * 4))
        simulated.purpose = "FORMAL_DECISION_EXPLORATION"
        self.assertEqual(simulated._plan(), 4)

    def test_the_planner_matches_the_sealed_replay_formula(self):
        for budget in ("26.4", "35.2", "44", "100"):
            runner, _ = self.make(f"p{budget}", budget=budget)
            expected = min(5, int(Decimal(budget) // (8 * 2 * CAP)))
            self.assertEqual(runner._plan(), expected)

    def test_rounds_not_planned_stay_not_run_in_the_fixed_denominator(self):
        result, _, _ = self.run_it(budget=str(Decimal(8) * 2 * CAP * 3))
        runs = json.loads((self.root / "run" / "runs.json").read_text())
        for rows in runs.values():
            self.assertEqual([r["status"] for r in rows], ["OK", "OK", "OK", "NOT_RUN", "NOT_RUN"])

    def test_a_round_that_cannot_be_reserved_ends_the_run_cleanly_between_rounds(self):
        runner, sim = self.make()
        original = bl.Ledger.reserve_round

        def refuse_third(self_, round_index, item_ids):
            if round_index == 2:
                raise bl.BudgetStop("whole round including retries cannot be reserved")
            return original(self_, round_index, item_ids)

        with mock.patch.object(bl.Ledger, "reserve_round", refuse_third):
            result = runner.start()
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(result["report"]["rounds_reserved"], 2)
        self.assertEqual(len(sim.requests), 32)  # two whole rounds, never a partial third
        statuses = {tuple(r["status"] for r in rows) for rows in json.loads((self.root / "run" / "runs.json").read_text()).values()}
        self.assertEqual(statuses, {("OK", "OK", "NOT_RUN", "NOT_RUN", "NOT_RUN")})

    def test_an_unreservable_round_ends_the_run_with_two_whole_rounds_and_no_gap_in_the_books(self):
        # the same defect as above seen through the run-state gate: a skipped round leaves a gap, which the gate refuses at the
        # end of the run, so the old test errors; this one asserts on it
        runner, sim = self.make("gap")
        original = bl.Ledger.reserve_round

        def refuse_third(self_, round_index, item_ids):
            if round_index == 2:
                raise bl.BudgetStop("whole round including retries cannot be reserved")
            return original(self_, round_index, item_ids)

        with mock.patch.object(bl.Ledger, "reserve_round", refuse_third):
            result = self.completes(runner.start)
        self.assertEqual((result["state"], result["report"]["rounds_reserved"]), ("COMPLETE", 2))
        self.assertEqual(len(sim.requests), 32)
        self.assertEqual(sorted(self.ledger("gap")["slots"]), sorted(f"{item}:{n}" for item in runner.order for n in (0, 1)))


class PureFunctionTests(unittest.TestCase):
    def test_raw_upper_cost_matches_the_adapters_own_upper_bound(self):
        for inputs, outputs, cached in ((1200, 80, 0), (0, 0, 0), (180000, 4096, 10), (272000, 1, 0), (272001, 4096, 5), (1050000, 4096, 0)):
            usage = {"input_tokens": inputs, "output_tokens": outputs, "input_tokens_details": {"cached_tokens": cached}}
            self.assertEqual(xr.raw_upper_cost({"usage": usage}), Decimal(api.usage_estimate(usage)[1]), (inputs, outputs))

    def test_raw_upper_cost_keeps_counting_past_the_adapters_bounds_and_refuses_untrustworthy_usage(self):
        over = xr.raw_upper_cost({"usage": {"input_tokens": 2_000_000, "output_tokens": 100, "input_tokens_details": {"cached_tokens": 0}}})
        self.assertEqual(over, Decimal("1.00018"))
        self.assertGreater(over, CAP)
        for bad in (None, {}, {"usage": None}, {"usage": {}}, {"usage": {"input_tokens": "5", "output_tokens": 1}},
                    {"usage": {"input_tokens": True, "output_tokens": 1}}, {"usage": {"input_tokens": 1.5, "output_tokens": 1}},
                    {"usage": {"input_tokens": -1, "output_tokens": 1}}, {"usage": {"input_tokens": 1, "output_tokens": 1,
                                                                                    "input_tokens_details": {"cached_tokens": -3}}}):
            try:
                cost = xr.raw_upper_cost(bad)
            except Exception as exc:  # unusable usage must give None; a crash here would lose the charge
                self.fail(f"{bad!r}: raised {type(exc).__name__} instead of returning None")
            self.assertIsNone(cost, bad)

    def test_classification_table(self):
        http = lambda code: xr.urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(b"{}"))  # noqa: E731
        reason = lambda exc: xr.urllib.error.URLError(exc)  # noqa: E731
        table = [
            # exception, generation_sent, response_received, expected
            (xr.RunnerIOError("disk"), False, False, "PERSISTENCE"),
            (xr.RunnerIOError("disk"), True, True, "PERSISTENCE"),
            (api.CapabilityBlocked("x"), False, False, "CAPABILITY"),
            (api.CapabilityBlocked("x"), True, True, "CAPABILITY"),
            (KeyError("usage"), True, True, "OTHER"),
            # a response arrived and something broke afterwards: it is paid for and unusable, whatever the exception looks like
            (TimeoutError(), True, True, "OTHER"),
            (ConnectionResetError(), True, True, "OTHER"),
            (http(503), True, True, "OTHER"),
            (http(400), True, True, "OTHER"),
            (TimeoutError(), False, False, "INFRA"),
            (TimeoutError(), True, False, "TIMEOUT"),
            (xr.socket.timeout(), True, False, "TIMEOUT"),
            (reason(TimeoutError()), True, False, "TIMEOUT"),
            (reason(TimeoutError()), False, False, "INFRA"),
            (reason("unreachable"), False, False, "INFRA"),
            (reason("unreachable"), True, False, "UNCERTAIN"),
            (ConnectionResetError(), False, False, "INFRA"),
            (ConnectionResetError(), True, False, "UNCERTAIN"),
            (http(429), True, False, "INFRA"),
            (http(503), True, False, "INFRA"),
            (http(500), False, False, "INFRA"),
            (http(502), False, False, "INFRA"),
            (http(500), True, False, "UNCERTAIN"),
            (http(504), True, False, "UNCERTAIN"),
            (http(400), False, False, "REJECTED"),
            (http(401), True, False, "REJECTED"),
            (http(404), True, False, "REJECTED"),
            (ValueError("boom"), False, False, "OTHER"),
        ]
        for exc, sent, received, expected in table:
            self.assertEqual(xr.classify(exc, generation_sent=sent, response_received=received), expected, (type(exc).__name__, sent, received))


class FaultTests(RunnerCase):
    ITEM = "eng-foreign-writer"

    def rule(self, fault, path="responses", occurrence=1, item=None):
        return {"path": path, "item_id": item or self.ITEM, "occurrence": occurrence, "fault": fault}

    def runs(self, name="run"):
        return json.loads((self.root / name / "runs.json").read_text())

    def report(self, name="run"):
        return json.loads((self.root / name / "report.json").read_text())

    def state(self, name="run"):
        return json.loads((self.root / name / "run_state.json").read_text())

    # --- uncertain starts are charged the cap and never sent again -------------------------------------------------------
    def test_a_timeout_after_the_generation_request_is_final_charged_the_cap_and_never_resent(self):
        result, runner, sim = self.run_it(faults=[self.rule("timeout")])
        self.assertEqual(result["state"], "COMPLETE")
        slot = self.slot(self.ITEM)
        self.assertEqual((slot["state"], slot["status"], slot["attempts"]), ("FINAL", "TIMEOUT", 1))
        self.assertEqual(slot["receipts"], [{"status": "TIMEOUT", "cost": str(CAP), "cost_known": False}])
        self.assertEqual(len(self.generations(sim, self.ITEM)), 5)  # round 0 once, rounds 1-4 once each: no sixth, no resend
        self.assertEqual(Decimal(self.ledger()["spent_upper"]), 39 * OK_COST + CAP)
        self.assertEqual(self.runs()[self.ITEM][0]["status"], "TIMEOUT")
        errors = [r for r in self.attempts() if r["phase"] == "ERROR" and r["slot"] == f"{self.ITEM}:0"]
        self.assertEqual([(r["path"], r["error"]["type"]) for r in errors], [("responses", "TimeoutError")])  # the failure is on record
        rows = [r["phase"] for r in self.attempts() if r.get("slot") == f"{self.ITEM}:0"]
        self.assertNotIn("RESPONSE", rows[rows.index("ERROR"):])  # and nothing pretends a response arrived

    def test_a_failed_request_leaves_its_error_record_and_the_run_still_closes_with_a_valid_log(self):
        # a record under any other phase name is refused by the run-state gate when the run closes; assert on that, not on a crash
        result, runner, sim = self.completes(lambda: self.run_it("errrec", faults=[self.rule("timeout")]))
        self.assertEqual(result["state"], "COMPLETE")
        errors = [r for r in self.attempts("errrec") if r["phase"] == "ERROR" and r["slot"] == f"{self.ITEM}:0"]
        self.assertEqual([(r["path"], r["error"]["type"]) for r in errors], [("responses", "TimeoutError")])
        phases = {r["phase"] for r in self.attempts("errrec")}
        self.assertEqual(phases - {"ROUND_RESERVED", "START", "REQUEST", "RESPONSE", "ERROR", "SETTLED", "RESUME"}, set())

    def test_other_uncertain_failures_after_the_generation_request_end_as_incomplete_without_a_resend(self):
        for fault in ("http_500", "conn_error", "url_error"):
            name = f"unc-{fault}"
            _, _, sim = self.run_it(name, faults=[self.rule(fault)])
            slot = self.slot(self.ITEM, name=name)
            self.assertEqual((slot["state"], slot["status"], slot["attempts"]), ("FINAL", "INCOMPLETE", 1), fault)
            self.assertEqual(slot["receipts"][0]["cost_known"], False)
            self.assertEqual(len(self.generations(sim, self.ITEM)), 5, fault)

    # --- failures that provably never reached generation are retried once -----------------------------------------------
    def test_failures_before_generation_are_retried_once_and_succeed(self):
        for fault in ("timeout", "socket_timeout", "conn_error", "url_error", "http_429", "http_500", "http_503"):
            name = f"count-{fault}"
            result, _, sim = self.run_it(name, faults=[self.rule(fault, path="responses/input_tokens")])
            slot = self.slot(self.ITEM, name=name)
            self.assertEqual((result["state"], slot["state"], slot["status"], slot["attempts"]), ("COMPLETE", "FINAL", "OK", 2), fault)
            self.assertEqual(slot["receipts"][0], {"status": "INFRA_ERROR", "cost": str(CAP), "cost_known": False})
            self.assertEqual((len(self.counts(sim, self.ITEM)), len(self.generations(sim, self.ITEM))), (6, 5), fault)

    def test_a_definite_not_processed_answer_to_the_generation_request_is_retried_once(self):
        for fault in ("http_429", "http_503"):
            name = f"gen-{fault}"
            result, _, sim = self.run_it(name, faults=[self.rule(fault)])
            slot = self.slot(self.ITEM, name=name)
            self.assertEqual((result["state"], slot["status"], slot["attempts"]), ("COMPLETE", "OK", 2), fault)
            self.assertEqual(len(self.generations(sim, self.ITEM)), 6, fault)

    def test_two_infra_errors_end_as_infra_fail_inside_the_denominator_and_the_run_goes_on(self):
        faults = [self.rule("conn_error", path="responses/input_tokens", occurrence=1), self.rule("conn_error", path="responses/input_tokens", occurrence=2)]
        result, _, sim = self.run_it(faults=faults)
        slot = self.slot(self.ITEM)
        self.assertEqual((result["state"], slot["state"], slot["status"], slot["attempts"]), ("COMPLETE", "FINAL", "INFRA_FAIL", 2))
        self.assertEqual([r["cost"] for r in slot["receipts"]], [str(CAP), str(CAP)])
        self.assertEqual(len(self.generations(sim, self.ITEM)), 4)  # the failed slot never reached generation
        self.assertEqual(self.runs()[self.ITEM][0]["status"], "INFRA_FAIL")
        self.assertGreater(self.report()["metrics"]["infra_fail_rate"]["estimate"], 0)

    def test_the_worst_case_burns_both_attempts_of_every_slot_and_still_fits_the_reservation(self):
        faults = [self.rule("conn_error", path="responses/input_tokens", occurrence=n, item=item) for item in self.public for n in (1, 2)]
        result, _, _ = self.run_it(faults=faults)
        state = self.ledger()
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(Decimal(state["spent_upper"]), 8 * 2 * CAP + 32 * OK_COST)
        self.assertTrue(all(s["held"] == "0" for s in state["slots"].values()))
        self.assertEqual(self.state()["state"], "COMPLETE")

    # --- stop rules: nothing after the halt is sent ----------------------------------------------------------------------
    def test_rejected_requests_halt_the_run_and_nothing_after_them_is_sent(self):
        for fault, path in (("http_400", "responses"), ("http_401", "responses"), ("http_400", "responses/input_tokens"),
                            ("http_401", "responses/input_tokens")):
            name = f"rej-{fault}-{path.replace('/', '_')}"
            runner, sim = self.make(name, faults=[self.rule(fault, path=path)])
            result = runner.start()
            position = runner.order.index(self.ITEM)
            self.assertEqual(result["state"], "HALTED", name)
            self.assertEqual((result["halt"]["reason"], result["halt"]["slot"]), ("REJECTED", f"{self.ITEM}:0"))
            self.assertEqual(len(sim.requests), 2 * position + (1 if path.endswith("input_tokens") else 2), name)
            slot = self.slot(self.ITEM, name=name)
            self.assertEqual((slot["status"], slot["receipts"][0]["cost_known"]), ("INCOMPLETE", False))
            self.assertEqual((self.state(name)["state"], self.report(name)["halted"], self.report(name)["scoring"]), ("HALTED", True, None))
            statuses = [self.runs(name)[item][0]["status"] for item in runner.order]
            self.assertEqual(statuses, ["OK"] * position + ["INCOMPLETE"] + ["NOT_RUN"] * (7 - position))
            again, _ = self.make(name)
            with self.assertRaisesRegex(xr.RunBlocked, "RUN_IS_HALTED"):
                again.resume()

    def test_a_provider_identity_violation_halts_but_the_paid_response_is_charged_from_its_usage(self):
        for fault, field, value in (("wrong_model", "model", "gpt-6-luna"), ("wrong_tier", "service_tier", "priority")):
            name = f"id-{fault}"
            result, _, sim = self.run_it(name, faults=[self.rule(fault)])
            slot = self.slot(self.ITEM, name=name)
            self.assertEqual((result["state"], result["halt"]["reason"]), ("HALTED", "CAPABILITY"), fault)
            self.assertEqual((slot["state"], slot["status"]), ("FINAL", "INVALID_OUTPUT"))
            self.assertEqual(slot["receipts"], [{"status": "INVALID_OUTPUT", "cost": str(OK_COST), "cost_known": True}])  # from usage, not zero, not the cap
            rows = [r for r in self.attempts(name) if r.get("slot") == f"{self.ITEM}:0"]
            saved = [i for i, r in enumerate(rows) if r["phase"] == "RESPONSE" and r["path"] == "responses"]
            self.assertEqual(len(saved), 1, fault)  # the paid response is on disk
            raw = saved[0]
            self.assertLess(raw, [r["phase"] for r in rows].index("SETTLED"))  # the raw response was on disk before settlement
            self.assertEqual(rows[raw]["response"][field], value)

    def test_a_response_without_usage_is_charged_the_cap_and_halts(self):
        result, _, _ = self.run_it(faults=[self.rule("no_usage")])
        slot = self.slot(self.ITEM)
        self.assertEqual((result["state"], result["halt"]["reason"], slot["status"]), ("HALTED", "OTHER", "INVALID_OUTPUT"))
        self.assertEqual(slot["receipts"], [{"status": "INVALID_OUTPUT", "cost": str(CAP), "cost_known": False}])

    def test_usage_above_the_attempt_cap_is_charged_in_full_and_stops_everything(self):
        result, runner, _ = self.run_it(faults=[self.rule("huge_usage")])
        position = runner.order.index(self.ITEM)
        state = self.ledger()
        self.assertEqual((result["state"], result["halt"]["reason"]), ("HALTED", "ATTEMPT_CAP_OVERRUN"))
        self.assertIs(state["overrun"], True)
        self.assertEqual(state["slots"][f"{self.ITEM}:0"]["state"], "OVERRUN")
        self.assertEqual(Decimal(state["spent_upper"]), position * OK_COST + Decimal("1.00018"))  # the real cost counted, above the cap
        self.assertEqual(self.runs()[self.ITEM][0]["status"], "INVALID_OUTPUT")
        self.assertEqual(self.state()["state"], "HALTED")

    def test_an_input_count_over_the_cap_halts_before_any_generation(self):
        result, _, sim = self.run_it(faults=[self.rule("count_over", path="responses/input_tokens")])
        slot = self.slot(self.ITEM)
        self.assertEqual((result["state"], result["halt"]["reason"]), ("HALTED", "CAPABILITY"))
        self.assertEqual(self.generations(sim, self.ITEM), [])
        self.assertEqual((slot["status"], slot["receipts"][0]["cost"]), ("INCOMPLETE", str(CAP)))

    def test_malformed_provider_behaviour_two_messages_and_tool_calls_halts(self):
        for fault in ("two_messages", "tool_call"):
            name = f"cap-{fault}"
            result, _, _ = self.run_it(name, faults=[self.rule(fault)])
            slot = self.slot(self.ITEM, name=name)
            self.assertEqual((result["state"], result["halt"]["reason"], slot["status"]), ("HALTED", "CAPABILITY", "INVALID_OUTPUT"), fault)
            self.assertEqual(slot["receipts"][0]["cost"], str(OK_COST))

    # --- model-behaviour failures are failed runs, not stops --------------------------------------------------------------
    def test_output_cap_and_unusable_output_are_failed_runs_with_their_usage_and_the_run_continues(self):
        expected = {"incomplete_cap": ("BUDGET_HALT", Decimal("0.0052152")), "incomplete_other": ("INCOMPLETE", OK_COST),
                    "bad_json": ("INVALID_OUTPUT", OK_COST)}
        for fault, (status, cost) in expected.items():
            name = f"fail-{fault}"
            result, _, _ = self.run_it(name, faults=[self.rule(fault)])
            slot = self.slot(self.ITEM, name=name)
            self.assertEqual((result["state"], slot["status"], slot["attempts"]), ("COMPLETE", status, 1), fault)
            self.assertEqual(slot["receipts"], [{"status": status, "cost": str(cost), "cost_known": True}], fault)
            self.assertEqual(self.report(name)["run_statuses"][status], 1, fault)

    # --- the unexpected ------------------------------------------------------------------------------------------------
    def test_an_unexpected_exception_halts_and_charges_the_cap(self):
        original = api.ResponsesAdapter.invoke

        def flaky(adapter, item):
            if item["item_id"] == self.ITEM:
                raise RuntimeError("boom")
            return original(adapter, item)

        runner, _ = self.make()
        with mock.patch.object(api.ResponsesAdapter, "invoke", flaky):
            result = runner.start()
        slot = self.slot(self.ITEM)
        self.assertEqual((result["state"], result["halt"]["reason"], slot["status"]), ("HALTED", "OTHER", "INCOMPLETE"))
        self.assertEqual(slot["receipts"][0], {"status": "INCOMPLETE", "cost": str(CAP), "cost_known": False})

    def test_a_failure_to_persist_halts_and_never_loses_what_was_paid_for(self):
        original = xr.AppendLog.append

        def failing(log, record, phase="RESPONSE"):
            if record.get("phase") == phase and record.get("path") == "responses" and record.get("slot") == f"{self.ITEM}:0":
                raise OSError("disk full")
            return original(log, record)

        # the response arrived but could not be written: it is still charged from its own usage
        runner, _ = self.make("persist-response")
        with mock.patch.object(xr.AppendLog, "append", failing):
            result = runner.start()
        slot = self.slot(self.ITEM, name="persist-response")
        self.assertIsNotNone(result["halt"])
        self.assertEqual((result["halt"]["reason"], slot["status"]), ("PERSISTENCE", "INVALID_OUTPUT"))
        self.assertEqual(slot["receipts"][0]["cost"], str(OK_COST))
        # the request could not be written, so it was never sent
        runner, sim = self.make("persist-request")
        with mock.patch.object(xr.AppendLog, "append", lambda log, record: failing(log, record, "REQUEST")):
            result = runner.start()
        self.assertIsNotNone(result["halt"])
        self.assertEqual(result["halt"]["reason"], "PERSISTENCE")
        self.assertEqual(self.generations(sim, self.ITEM), [])
        self.assertEqual(self.slot(self.ITEM, name="persist-request")["status"], "INCOMPLETE")


class CrashAndResumeTests(RunnerCase):
    """A real child process is SIGKILLed mid-call; the resumed run must never send that call again."""

    ITEM = "eng-foreign-writer"

    def child(self, command, crash_item="none"):
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run([sys.executable, "-B", str(HERE / "crash_driver.py"), str(self.root), command, crash_item],
                              capture_output=True, text=True, cwd=str(HERE), env=env, timeout=180)

    def server_log(self):
        return [json.loads(line) for line in (self.root / "server.jsonl").read_text().splitlines()]

    def interrupt(self, name="run", item=None):
        """An in-process interruption: KeyboardInterrupt escapes the adapter call for one item."""
        item = item or self.ITEM
        original = api.ResponsesAdapter.invoke

        def interrupted(adapter, current):
            if current["item_id"] == item:
                raise KeyboardInterrupt()
            return original(adapter, current)

        runner, sim = self.make(name)
        with mock.patch.object(api.ResponsesAdapter, "invoke", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                runner.start()
        return runner, sim

    def test_a_process_killed_mid_call_resumes_without_resending_that_call(self):
        probe, _ = self.make("probe", log=False)
        victim = probe.order[3]
        first = self.child("start", victim)
        self.assertEqual(first.returncode, -9, first.stderr[-300:])  # SIGKILL: no handler, no cleanup, nothing settled
        slot = self.slot(victim)
        self.assertEqual((slot["state"], slot["attempts"]), ("RUNNING", 1))  # the START_INTENT is durable, nothing else is
        self.assertEqual(json.loads((self.root / "run" / "run_state.json").read_text())["state"], "RUNNING")
        before = self.server_log()
        self.assertEqual([r["item_id"] for r in before if r["path"] == "responses"][-1], victim)  # the request had left
        self.assertEqual(len([r for r in before if r["path"] == "responses"]), 4)  # three settled slots and the victim

        second = self.child("resume")
        self.assertEqual(second.returncode, 0, second.stderr[-300:])
        self.assertEqual(json.loads(second.stdout)["state"], "COMPLETE")
        state = self.ledger()
        slot = state["slots"][f"{victim}:0"]
        self.assertEqual((slot["state"], slot["status"], slot["attempts"]), ("FINAL", "INCOMPLETE", 1))
        self.assertEqual(slot["receipts"], [{"status": "INCOMPLETE", "cost": str(CAP), "cost_known": False}])
        self.assertEqual(Decimal(state["spent_upper"]), 39 * OK_COST + CAP)
        log = self.server_log()
        self.assertEqual(len([r for r in log if r["path"] == "responses" and r["item_id"] == victim]), 5)  # once per round, never twice
        self.assertEqual(len([r for r in log if r["path"] == "responses"]), 40)
        resumed = [r for r in xr.AppendLog(self.root / "run" / "attempts.jsonl").records() if r["phase"] == "RESUME"]
        self.assertEqual([r["recovered_uncertain"] for r in resumed], [[f"{victim}:0"]])
        runs = json.loads((self.root / "run" / "runs.json").read_text())
        self.assertEqual(runs[victim][0]["status"], "INCOMPLETE")
        self.assertEqual(xr.replay_run(self.root / "run")["runs"], runs)

    def test_an_interrupt_mid_slot_leaves_the_slot_running_and_resume_recovers_it(self):
        runner, sim = self.interrupt()
        position = runner.order.index(self.ITEM)
        self.assertEqual(self.slot(self.ITEM)["state"], "RUNNING")
        self.assertEqual(len([r for r in sim.requests if r["path"] == "responses"]), position)
        again, sim2 = self.make("run", log=False)
        result = again.resume()
        self.assertEqual(result["state"], "COMPLETE")
        slot = self.slot(self.ITEM)
        self.assertEqual((slot["status"], slot["receipts"][0]["cost"]), ("INCOMPLETE", str(CAP)))
        self.assertEqual(len([r for r in sim2.requests if r["path"] == "responses"]), 40 - position - 1)  # the victim slot is not sent again

    def test_a_run_killed_after_an_overrun_halts_on_resume_instead_of_crashing_every_time(self):
        # The ledger records an overrun before the HALTED state is written; a kill in that window leaves RUNNING with an overrun
        # on record.  Resume must end it as a halt that sends nothing, not crash on every attempt and leave a run nobody can close.
        order = sorted(self.public)
        random.Random(6302).shuffle(order)
        result, _, _ = self.run_it("zombie", faults=[{"path": "responses", "item_id": order[-1], "occurrence": 1, "fault": "huge_usage"}])
        self.assertEqual((result["state"], result["halt"]["reason"]), ("HALTED", "ATTEMPT_CAP_OVERRUN"))
        (self.root / "zombie" / "run_state.json").write_text(xr.canonical({"state": "RUNNING", "t": 0}))
        for later in ("report.json", "write_ledger.json"):  # a kill before the state is written leaves neither: both are written after it
            (self.root / "zombie" / later).unlink()
        again, sim = self.make("zombie", log=False)
        try:
            resumed = again.resume()
        except Exception as exc:
            self.fail(f"resume crashed: {type(exc).__name__}: {exc}")
        self.assertEqual((resumed["state"], resumed["halt"]["reason"]), ("HALTED", "LEDGER_CONFLICT"))
        self.assertEqual(sim.requests, [])
        self.assertEqual(json.loads((self.root / "zombie" / "run_state.json").read_text())["state"], "HALTED")
        self.assertIs(self.ledger("zombie")["overrun"], True)

    def test_resume_refuses_a_halted_a_complete_a_changed_or_an_absent_run(self):
        done, _, _ = self.run_it("done")
        again, sim = self.make("done")
        with self.assertRaisesRegex(xr.RunBlocked, "RUN_IS_COMPLETE"):
            again.resume()
        self.assertEqual(sim.requests, [])
        absent, _ = self.make("absent")
        with self.assertRaisesRegex(xr.RunBlocked, "NO_RUN_TO_RESUME"):
            absent.resume()
        self.interrupt("changed")
        for kwargs, expected in (({"seed": 1}, "seed"), ({"budget": "60"}, "budget_usd"), ({"instructions": "other pack"}, "instructions_sha256"),
                                 ({"rounds": 4}, "target_rounds")):
            other, other_sim = self.make("changed", log=False, **kwargs)
            with self.assertRaisesRegex(xr.RunBlocked, f"RESUME_IDENTITY_MISMATCH:.*{expected}"):
                other.resume()
            self.assertEqual(other_sim.requests, [])
            self.assertEqual(self.slot(self.ITEM, name="changed")["state"], "RUNNING")  # a refused resume changes nothing
        self.interrupt("recoded")
        with mock.patch.object(ep, "runner_sources", lambda: {"exploration_runner.py": "0" * 64}):  # the code changed since the run began
            other, other_sim = self.make("recoded", log=False)
            self.assertBlocks("RESUME_IDENTITY_MISMATCH:.*runner_sources", other.resume)
        self.assertEqual(other_sim.requests, [])
        self.assertEqual(self.slot(self.ITEM, name="recoded")["state"], "RUNNING")

    def test_resume_refuses_when_the_runner_code_changed_since_the_run_began(self):
        # the same guard as the last part of the test above, on its own: a manifest without a usable code pin is refused by the
        # run-state gate before the identity comparison, so the combined test errors for that mutant; this one asserts on it
        self.interrupt("recoded-alone")
        with mock.patch.object(ep, "runner_sources", lambda: {"exploration_runner.py": "0" * 64}):
            other, other_sim = self.make("recoded-alone", log=False)
            self.assertBlocks("RESUME_IDENTITY_MISMATCH:.*runner_sources", other.resume)
        self.assertEqual(other_sim.requests, [])
        self.assertEqual(self.slot(self.ITEM, name="recoded-alone")["state"], "RUNNING")

    def test_start_never_relaunches_an_existing_run(self):
        self.run_it("used")
        again, sim = self.make("used")
        self.assertBlocks("OUT_DIR_NOT_EMPTY", again.start)  # not merely a later FileExistsError from the manifest's exclusive write
        self.assertEqual(sim.requests, [])
        (self.root / "empty").mkdir()
        runner, _ = self.make("empty")
        self.assertEqual(runner.start()["state"], "COMPLETE")

    def test_a_second_runner_cannot_resume_a_directory_another_holds(self):
        self.interrupt("held")
        descriptor = os.open(self.root / "held" / "run.lock", os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            other, sim = self.make("held", log=False)
            with self.assertRaisesRegex(xr.RunBlocked, "ANOTHER_RUNNER_HOLDS_THIS_DIRECTORY"):
                other.resume()
            self.assertEqual(sim.requests, [])
        finally:
            os.close(descriptor)
        free, _ = self.make("held", log=False)
        self.assertEqual(free.resume()["state"], "COMPLETE")

    def test_the_lock_is_released_when_a_run_ends_or_is_interrupted(self):
        self.interrupt("lock")
        descriptor = os.open(self.root / "lock" / "run.lock", os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)  # would raise BlockingIOError if the interrupted run still held it
        finally:
            os.close(descriptor)

    def test_wiring_errors_surface_before_anything_is_written(self):
        runner = xr.Runner(self.root / "nowire", mode="SIMULATION", bank_manifest=self.manifest, instructions="x", budget="50", attempt_cap=CAP)
        with self.assertRaisesRegex(xr.RunBlocked, "SIMULATION_NEEDS_A_SIMULATED_TRANSPORT"):
            runner.start()
        self.assertFalse((self.root / "nowire").exists())
        self.interrupt("wired")
        broken = xr.Runner(self.root / "wired", mode="SIMULATION", bank_manifest=self.manifest, instructions="synthetic instructions",
                           budget="50", attempt_cap=CAP)
        before = (self.root / "wired" / "ledger.json").read_bytes()
        with self.assertRaisesRegex(xr.RunBlocked, "SIMULATION_NEEDS_A_SIMULATED_TRANSPORT"):
            broken.resume()
        self.assertEqual((self.root / "wired" / "ledger.json").read_bytes(), before)  # nothing was recovered or changed


class ReplayTests(RunnerCase):
    def test_replay_never_writes_anything(self):
        self.run_it()
        out = self.root / "run"
        before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
        mtimes = {p: p.stat().st_mtime_ns for p in before}
        xr.replay_run(out)
        self.assertEqual(before, {p: p.read_bytes() for p in out.rglob("*") if p.is_file()})
        self.assertEqual(mtimes, {p: p.stat().st_mtime_ns for p in before})

    def test_replay_refuses_an_unrecovered_run_a_changed_bank_and_a_missing_ledger(self):
        helper = CrashAndResumeTests("test_start_never_relaunches_an_existing_run")
        helper.root, helper.make, helper.assertRaises = self.root, self.make, self.assertRaises
        helper.interrupt("open")
        with self.assertRaisesRegex(ValueError, "unsettled slot"):
            xr.replay_run(self.root / "open")
        self.run_it("closed")
        self.manifest.write_text(self.manifest.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "the bank changed since this run"):
            xr.replay_run(self.root / "closed")
        self.manifest.write_text(self.manifest.read_text().rstrip(" "))
        (self.root / "closed" / "ledger.json").rename(self.root / "closed" / "ledger.moved")
        with self.assertRaisesRegex(ValueError, "no ledger to replay"):
            xr.replay_run(self.root / "closed")

    def test_saved_runs_covers_every_ledger_state_and_agrees_with_the_sealed_replay_when_it_can(self):
        self.run_it()
        ledger = bl.Ledger(self.root / "run" / "ledger.json", budget="50", attempt_cap=CAP)
        first = sorted(self.public)[0]
        self.assertEqual(xr.saved_runs(self.public, ledger, 5), replay.replay_saved(self.public, ledger, rounds=5))
        self.assertEqual(xr.saved_runs(self.public, ledger, 7)[first][5:], [{"status": "NOT_RUN"}, {"status": "NOT_RUN"}])
        slots = ledger.state["slots"]
        slots[f"{first}:0"]["state"] = "RETRY_READY"
        slots[f"{first}:1"]["state"] = "RESERVED"
        slots[f"{first}:2"].update(state="OVERRUN", receipts=[{"status": "BUDGET_HALT", "cost": "9", "cost_known": True}])
        slots[f"{first}:3"].update(state="OVERRUN", receipts=[{"status": "OK", "cost": "9", "cost_known": True}])
        rows = xr.saved_runs(self.public, ledger, 5)[first]
        self.assertEqual([r["status"] for r in rows], ["INFRA_FAIL", "NOT_RUN", "BUDGET_HALT", "INCOMPLETE", "OK"])
        slots[f"{first}:4"]["state"] = "RUNNING"
        with self.assertRaisesRegex(ValueError, "unsettled slot"):
            xr.saved_runs(self.public, ledger, 5)


class ModeAndGateTests(RunnerCase):
    ITEM = "eng-foreign-writer"

    def live_runner(self, name="live", **kwargs):
        return xr.Runner(self.root / name, mode="LIVE", bank_manifest=self.manifest, instructions="synthetic instructions", budget="50",
                         attempt_cap=CAP, **kwargs)

    def test_live_mode_uses_the_live_adapter_through_a_recording_transport_and_never_the_network(self):
        fake = SimulatedResponses(self.public, answer_for=self.answer, log_path=self.root / "live-server.jsonl")
        runner = self.live_runner()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-NOT-A-REAL-KEY"}), \
                mock.patch.object(api.ResponsesAdapter, "_post", lambda adapter, path, body: fake(path, body)):
            result = runner.start()
        self.assertEqual((result["state"], result["report"]["kind"]), ("COMPLETE", "EXPLORATION_RESULT_UNREVIEWED"))
        self.assertEqual(len(fake.requests), 80)
        self.assertEqual(urllib_calls(), 0)
        self.assertNotIn("sk-test", (self.root / "live" / "attempts.jsonl").read_text())

    def test_live_without_a_key_is_blocked_before_anything_is_written(self):
        runner = self.live_runner("nokey")
        with mock.patch.dict(os.environ):
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaisesRegex(xr.RunBlocked, "LIVE_KEY_MISSING"):
                runner.start()
        self.assertFalse((self.root / "nokey").exists())

    def test_live_forbids_an_injected_transport_and_simulation_forbids_the_live_adapter(self):
        injected = self.live_runner("inj", transport=SimulatedResponses(self.public))
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-NOT-A-REAL-KEY"}):
            with self.assertRaisesRegex(xr.RunBlocked, "LIVE_FORBIDS_AN_INJECTED_TRANSPORT"):
                injected.start()
        self.assertFalse((self.root / "inj").exists())
        runner, _ = self.make("simlive")
        with mock.patch.object(api.ResponsesAdapter, "live", True, create=True):
            real_init = api.ResponsesAdapter.__init__

            def init(adapter, instructions, transport=None):
                real_init(adapter, instructions, transport)
                adapter.live = True  # a simulation that somehow got the live adapter must refuse to run

            with mock.patch.object(api.ResponsesAdapter, "__init__", init):
                with self.assertRaisesRegex(xr.RunBlocked, "SIMULATION_MUST_NOT_USE_THE_LIVE_ADAPTER"):
                    runner.start()
        self.assertFalse((self.root / "simlive").exists())

    def test_live_needs_the_live_adapter(self):
        real_init = api.ResponsesAdapter.__init__

        def init(adapter, instructions, transport=None):
            real_init(adapter, instructions, transport)
            adapter.live = False

        runner = self.live_runner("notlive")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-NOT-A-REAL-KEY"}), mock.patch.object(api.ResponsesAdapter, "__init__", init):
            with self.assertRaisesRegex(xr.RunBlocked, "LIVE_NEEDS_THE_LIVE_ADAPTER"):
                runner.start()
        self.assertFalse((self.root / "notlive").exists())

    def test_a_receipt_from_the_wrong_mode_or_with_an_unknown_status_halts_and_is_charged(self):
        original = api.ResponsesAdapter.invoke
        cases = {"RECEIPT_MODE_MISMATCH": lambda r: {**r, "mode": "LIVE"}, "UNKNOWN_RECEIPT_STATUS": lambda r: {**r, "status": "WEIRD"},
                 "INCONSISTENT_RECEIPT": lambda r: {**r, "output": {}}}
        for reason, mutate in cases.items():
            def lying(adapter, item, mutate=mutate):
                receipt = original(adapter, item)
                return mutate(receipt) if item["item_id"] == self.ITEM else receipt

            runner, _ = self.make(reason.lower())
            with mock.patch.object(api.ResponsesAdapter, "invoke", lying):
                result = runner.start()
            slot = self.slot(self.ITEM, name=reason.lower())
            self.assertEqual(result["state"], "HALTED", reason)
            self.assertEqual((result["halt"]["reason"], slot["status"]), (reason, "INVALID_OUTPUT"), reason)
            self.assertEqual(slot["receipts"], [{"status": "INVALID_OUTPUT", "cost": str(OK_COST), "cost_known": True}], reason)

    def test_a_receipt_from_the_wrong_mode_or_one_the_checker_rejects_is_halted_and_never_booked_as_a_result(self):
        # accepting either kind of receipt books an OK slot whose verified run is not OK; the run-state gate refuses that when the
        # run closes, so the old test errors; this one asserts on it
        original = api.ResponsesAdapter.invoke
        cases = {"RECEIPT_MODE_MISMATCH": lambda r: {**r, "mode": "LIVE"}, "INCONSISTENT_RECEIPT": lambda r: {**r, "output": {}}}
        for reason, mutate in cases.items():
            def lying(adapter, item, mutate=mutate):
                receipt = original(adapter, item)
                return mutate(receipt) if item["item_id"] == self.ITEM else receipt

            name = "booked-" + reason.lower()
            runner, _ = self.make(name)
            with mock.patch.object(api.ResponsesAdapter, "invoke", lying):
                result = self.completes(runner.start)
            slot = self.slot(self.ITEM, name=name)
            self.assertEqual(result["state"], "HALTED", reason)
            self.assertEqual((result["halt"]["reason"], slot["status"]), (reason, "INVALID_OUTPUT"), reason)
            self.assertEqual(slot["verified_run"]["status"], "INVALID_OUTPUT", reason)

    def test_a_receipt_without_a_readable_status_or_cost_halts_and_is_charged_the_cap(self):
        original = api.ResponsesAdapter.invoke
        without = lambda key: (lambda r: {k: v for k, v in r.items() if k != key})
        cases = {"no_cost": (without("cost_upper_usd"), "MALFORMED_RECEIPT"), "nan_cost": (lambda r: {**r, "cost_upper_usd": "NaN"}, "MALFORMED_RECEIPT"),
                 "negative_cost": (lambda r: {**r, "cost_upper_usd": "-1"}, "MALFORMED_RECEIPT"),
                 "text_cost": (lambda r: {**r, "cost_upper_usd": "plenty"}, "MALFORMED_RECEIPT"),
                 "boolean_cost": (lambda r: {**r, "cost_upper_usd": True}, "MALFORMED_RECEIPT"),
                 "no_status": (without("status"), "MALFORMED_RECEIPT"), "list_status": (lambda r: {**r, "status": ["OK"]}, "MALFORMED_RECEIPT"),
                 "not_a_dict": (lambda r: None, "MALFORMED_RECEIPT"), "no_output": (without("output"), "INCONSISTENT_RECEIPT"),
                 "unknown_output_status": (lambda r: {**r, "output": {**r["output"], "status": "WEIRD"}}, "INCONSISTENT_RECEIPT")}
        for name, (mutate, reason) in cases.items():
            def lying(adapter, item, mutate=mutate):
                receipt = original(adapter, item)
                return mutate(receipt) if item["item_id"] == self.ITEM else receipt

            runner, sim = self.make(name)
            try:
                with mock.patch.object(api.ResponsesAdapter, "invoke", lying):
                    result = runner.start()
            except Exception as exc:  # a malformed receipt must end the run as a halt, never as a crash that leaves the slot RUNNING
                self.fail(f"{name}: {type(exc).__name__}: {exc}")
            slot = self.slot(self.ITEM, name=name)
            self.assertEqual(result["state"], "HALTED", name)
            self.assertEqual((result["halt"]["reason"], slot["status"]), (reason, "INVALID_OUTPUT"), name)
            known = reason == "INCONSISTENT_RECEIPT"  # the cost was readable there
            self.assertEqual(slot["receipts"], [{"status": "INVALID_OUTPUT", "cost": str(OK_COST if known else CAP), "cost_known": known}], name)
            self.assertFalse(any(s["state"] == "RUNNING" for s in self.ledger(name)["slots"].values()), name)
            self.assertEqual(len([r for r in sim.requests if r["path"] == "responses"]), runner.order.index(self.ITEM) + 1, name)  # nothing after it

    def test_the_owner_approval_is_validated_field_by_field_and_bound_to_the_profile(self):
        profile = self.root / "profile.json"
        profile.write_text('{"p": 1}')
        good = {"approved": True, "approved_by": "Owner", "approved_on": "2026-10-04", "scope": "FORMAL_EXPLORATION_R1",
                "data_egress": "synthetic items and public skill text to api.openai.com", "profile_sha256": ep.sha256_file(profile),
                "max_total_reservation_usd": "253.00"}
        path = self.root / "approval.json"

        def check(**changes):
            record = {**good, **changes}
            path.write_text(json.dumps({k: v for k, v in record.items() if v is not None}))
            return xr.validate_approval(path, profile, scope="FORMAL_EXPLORATION_R1", total_required=Decimal("253.00"))

        self.assertEqual(check(), [])
        self.assertIn("APPROVAL_NOT_GRANTED", check(approved=False))
        self.assertIn("APPROVAL_NOT_GRANTED", check(approved="yes"))
        for key in ("approved_by", "approved_on", "data_egress"):
            self.assertIn(f"APPROVAL_FIELD_MISSING:{key}", check(**{key: None}))
            self.assertIn(f"APPROVAL_FIELD_MISSING:{key}", check(**{key: "  "}))
        self.assertIn("APPROVAL_SCOPE_MISMATCH", check(scope="OTHER"))
        self.assertIn("APPROVAL_NOT_FOR_THIS_PROFILE", check(profile_sha256="0" * 64))
        self.assertIn("APPROVAL_CAP_BELOW_REQUIRED", check(max_total_reservation_usd="252.99"))
        self.assertIn("APPROVAL_CAP_INVALID", check(max_total_reservation_usd="plenty"))
        self.assertEqual(xr.validate_approval(self.root / "absent.json", profile, scope="x", total_required=Decimal(1)), ["APPROVAL_MISSING_OR_INVALID"])
        path.write_text("[]")
        self.assertEqual(xr.validate_approval(path, profile, scope="x", total_required=Decimal(1)), ["APPROVAL_MISSING_OR_INVALID"])


class RealArtifactRunnerTests(unittest.TestCase):
    """The real v5 profile through the real command line, with the network cut."""

    @classmethod
    def setUpClass(cls):
        evidence = ep.Path(os.environ.get("FABLE_ENSEMBLE3_EVIDENCE", "/Users/kelvin/VibeCoding-WorkSpace/fable-ensemble3-r1-evidence"))
        needed = [evidence / "formal-bank-v5/review-final/protocol.with-review.draft.json", evidence / "formal-bank-v5/review-final/bank_review.json",
                  evidence / "formal-bank-v5/manifest.json", evidence / "instruction-source-v2/candidate_instructions.md",
                  evidence / "instruction-source-v2/manifest.json"]
        missing = [str(p) for p in needed if not p.exists()]
        if missing:
            raise AssertionError(f"real v5 artifacts not found (set FABLE_ENSEMBLE3_EVIDENCE): {missing}")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        profile = ep.build_profile(protocol=needed[0], receipt=needed[1], bank_manifest=needed[2], pack=needed[3], pack_manifest=needed[4],
                                   source_commit="d037a9a8c612957540ff03c2d46b5b3ab8f0583a",
                                   sealed_commit="32d5c9991c71d5b4025c015c395c9d41226a6d45", repo=ep.ANALYSIS.parent)
        cls.profile = cls.root / "profile.json"
        cls.profile.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        for target in ("urllib.request.urlopen", "socket.socket.connect"):
            patcher = mock.patch(target, side_effect=AssertionError("the network must never be touched in tests"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def cli(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = xr.main(argv)
        return code, json.loads(buffer.getvalue())

    def test_a_live_run_is_blocked_with_the_real_profile_and_nothing_is_written(self):
        out = self.root / "live-blocked"
        with mock.patch.dict(os.environ):
            os.environ.pop("OPENAI_API_KEY", None)
            code, result = self.cli(["run", "--profile", str(self.profile), "--out", str(out), "--mode", "LIVE"])
        self.assertEqual((code, result["state"], result["generation_calls"]), (2, "BLOCKED", 0))
        for reason in ("APPROVAL_MISSING_OR_INVALID", "LIVE_KEY_MISSING", "LIVE_PROFILE_NOT_FROZEN", "PREFLIGHT:PROTOCOL_NOT_FROZEN"):
            self.assertIn(reason, result["reasons"])
        self.assertFalse(out.exists())

    def test_the_formal_bank_runs_in_simulation_only_with_the_explicit_flag_and_the_whole_pipeline_holds_at_scale(self):
        out = self.root / "formal-sim"
        code, result = self.cli(["run", "--profile", str(self.profile), "--out", str(out), "--mode", "SIMULATION"])
        self.assertEqual(code, 2)
        self.assertIn("FORMAL_BANK_SIMULATION_NEEDS_--allow-formal-simulation", result["reasons"])
        self.assertFalse(out.exists())
        code, result = self.cli(["run", "--profile", str(self.profile), "--out", str(out), "--mode", "SIMULATION", "--allow-formal-simulation"])
        self.assertEqual((code, result["state"], result["kind"]), (0, "COMPLETE", "SIMULATION_NOT_A_RESULT"))
        report = json.loads((out / "report.json").read_text())
        self.assertEqual((report["items"], report["families"], report["rounds_reserved"], report["run_statuses"]), (46, 12, 5, {"OK": 230}))
        self.assertIs(report["statistical_claims_allowed"], False)
        self.assertEqual(Decimal(report["ledger"]["budget_usd"]), Decimal("253.00"))  # the whole five-round reservation
        self.assertLess(Decimal(report["ledger"]["spent_upper_usd"]), 1)
        server = [json.loads(line) for line in (out / "simulated_server_log.jsonl").read_text().splitlines()]
        self.assertEqual(len(server), 460)  # 230 slots, one input count and one generation each
        manifest = json.loads((out / "run_manifest.json").read_text())
        self.assertEqual(manifest["profile"]["sha256"], ep.sha256_file(self.profile))
        self.assertEqual(manifest["bank_purpose"], "FORMAL_DECISION_EXPLORATION")
        code, replayed = self.cli(["replay", "--out", str(out)])
        self.assertEqual((code, replayed["state"], replayed["matches_saved_runs"], replayed["report"]), (0, "COMPLETE", True, report))

    def test_a_broken_profile_is_blocked_not_a_crash(self):
        for name, content in (("garbage.json", "{not json"), ("empty.json", "{}"), ("missing.json", None)):
            path = self.root / name
            if content is not None:
                path.write_text(content)
            for mode in ("SIMULATION", "LIVE"):
                out = self.root / f"out-{name}-{mode}"
                try:
                    code, result = self.cli(["run", "--profile", str(path), "--out", str(out), "--mode", mode])
                except Exception as exc:  # a broken profile must be BLOCKED, never a stack trace
                    self.fail(f"the command line crashed on {name} in {mode}: {type(exc).__name__}: {exc}")
                self.assertEqual((code, result["state"]), (2, "BLOCKED"), (name, mode))
                self.assertTrue(any(r.startswith("PROFILE_") for r in result["reasons"]), result["reasons"])
                self.assertFalse(out.exists())

    def test_the_command_line_reports_a_halt_with_its_own_exit_code(self):
        out = self.root / "halted-cli"

        def rejecting(public, **kwargs):
            return SimulatedResponses(public, faults=[{"path": "responses", "item_id": None, "occurrence": 1, "fault": "http_400"}], **kwargs)

        with mock.patch.object(xr, "SimulatedResponses", rejecting):
            code, result = self.cli(["run", "--profile", str(self.profile), "--out", str(out), "--mode", "SIMULATION", "--allow-formal-simulation"])
        self.assertEqual((code, result["state"], result["halt"]["reason"]), (3, "HALTED", "REJECTED"))
        self.assertEqual(json.loads((out / "run_state.json").read_text())["state"], "HALTED")
        code, resumed = self.cli(["resume", "--profile", str(self.profile), "--out", str(out), "--mode", "SIMULATION", "--allow-formal-simulation"])
        self.assertEqual((code, resumed["state"]), (2, "BLOCKED"))  # a halted run can neither resume nor be extended
        self.assertIn("RUN_IS_HALTED", ",".join(resumed["reasons"]))

    def test_R3B_a_reopened_certified_halted_run_cannot_resume_through_the_cli(self):
        out = self.root / "r3b-certified-halted-cli"

        def rejecting(public, **kwargs):
            return SimulatedResponses(public, faults=[{"path": "responses", "item_id": None, "occurrence": 1, "fault": "http_400"}], **kwargs)

        argv = ["--profile", str(self.profile), "--out", str(out), "--mode", "SIMULATION", "--allow-formal-simulation"]
        with mock.patch.object(xr, "SimulatedResponses", rejecting):
            code, halted = self.cli(["run"] + argv)
        self.assertEqual((code, halted["state"], halted["halt"]["reason"]), (3, "HALTED", "REJECTED"))
        self.assertTrue((out / "write_ledger.json").is_file())
        (out / "run_state.json").write_text(xr.canonical({"state": "RUNNING", "t": 0}))
        before = directory_digest(out)
        server = (out / "simulated_server_log.jsonl").read_bytes()
        code, refused = self.cli(["resume"] + argv)
        self.assertEqual((code, refused["state"]), (2, "BLOCKED"))
        self.assertEqual(refused["generation_calls"], 0)
        self.assertIn("RUN_IS_CERTIFIED", ",".join(refused["reasons"]))
        self.assertIn("WRITE_LEDGER_EXISTS_NOT_RECERTIFIED", (refused.get("integrity") or {}).get("flags", []))
        self.assertEqual((directory_digest(out), (out / "simulated_server_log.jsonl").read_bytes()), (before, server))

    def test_R3B_the_command_line_reports_generations_sent_before_a_refused_close(self):
        out = self.root / "r3b-refused-close-cli"
        original_append = xr.AppendLog.append

        def rejecting(public, **kwargs):
            return SimulatedResponses(public, faults=[{"path": "responses", "item_id": None, "occurrence": 1, "fault": "http_400"}], **kwargs)

        def lose_rejected_settlement(log, record):
            if record.get("phase") == "SETTLED" and record.get("kind") == "REJECTED":
                return
            return original_append(log, record)

        with mock.patch.object(xr, "SimulatedResponses", rejecting), mock.patch.object(xr.AppendLog, "append", lose_rejected_settlement):
            code, refused = self.cli(["run", "--profile", str(self.profile), "--out", str(out), "--mode", "SIMULATION", "--allow-formal-simulation"])
        requests = [json.loads(line) for line in (out / "simulated_server_log.jsonl").read_text().splitlines()]
        sent = sum(request["path"] == "responses" for request in requests)
        self.assertEqual((code, refused["state"]), (2, "BLOCKED"))
        self.assertIn("TERMINAL_RUN_JOURNAL_INCOMPLETE:HALTED_REJECTED:1_MISSING", ",".join(refused["reasons"]))
        self.assertGreater(sent, 0)
        self.assertEqual(refused["generation_calls"], sent)
        self.assertGreater(refused["generation_calls"], 0)

    def test_check_command_exit_codes(self):
        self.assertEqual(self.cli(["check", "--profile", str(self.profile)])[0], 0)
        code, result = self.cli(["check", "--profile", str(self.profile), "--mode", "LIVE"])
        self.assertEqual((code, result["status"]), (2, "BLOCKED"))

    def test_R3_the_command_line_carries_the_verdict_of_a_run_and_of_a_refused_resume(self):
        out = self.root / "r3-halted-cli"

        def rejecting(public, **kwargs):
            return SimulatedResponses(public, faults=[{"path": "responses", "item_id": None, "occurrence": 1, "fault": "http_400"}], **kwargs)

        with mock.patch.object(xr, "SimulatedResponses", rejecting):
            ran = self.cli(["run", "--profile", str(self.profile), "--out", str(out), "--mode", "SIMULATION", "--allow-formal-simulation"])
        gate = xr.inspect_run(out).get("integrity")
        resumed = self.cli(["resume", "--profile", str(self.profile), "--out", str(out), "--mode", "SIMULATION", "--allow-formal-simulation"])
        checked = self.cli(["check", "--profile", str(self.profile), "--out", str(out)])
        replayed = self.cli(["replay", "--out", str(out)])
        self.assertEqual({"run": (ran[0], ran[1].get("integrity")), "resume": (resumed[0], resumed[1].get("state"), resumed[1].get("integrity")),
                          "check": (checked[0], checked[1].get("status"), checked[1].get("run_integrity")), "replay": (replayed[0], replayed[1].get("integrity"))},
                         {"run": (3, gate), "resume": (2, "BLOCKED", gate), "check": (0, "PASS", gate), "replay": (0, gate)})
        self.assertEqual(((gate or {}).get("status"), (gate or {}).get("state"), (gate or {}).get("halt_reason")), ("CLEAN", "HALTED", "REJECTED"))


def directory_digest(out):
    entries = [(str(p.relative_to(out)), hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "DIRECTORY")
               for p in sorted(out.rglob("*"))] if out.exists() else []
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


class RepairIntegrityTests(RunnerCase):
    """R1 A-M acceptance; observations include the entire directory and server counts."""

    def note(self, case, **result):
        print("REPAIR_OBSERVATION " + json.dumps({"case": case, "test": self._testMethodName, **result}, sort_keys=True))

    def interrupted(self, name="run"):
        runner, sim = self.make(name, log=False)
        victim = runner.order[3]
        def dies(path, body):
            response = sim(path, body)
            if path == "responses" and json.loads(body["input"])["item_id"] == victim:
                raise KeyboardInterrupt("request already reached the simulated server")
            return response
        dies.is_simulated = True
        runner.transport = dies
        with self.assertRaises(KeyboardInterrupt):
            runner.start()
        return runner, sim

    def cli(self, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = xr.main(argv)
        return code, json.loads(output.getvalue())

    def deny_all(self, case, reason, name="run"):
        out = self.root / name
        runner, sim = self.make(name, log=False)
        entrances = {
            "inspect_run": lambda: xr.inspect_run(out), "Runner.resume": runner.resume,
            "replay_run": lambda: xr.replay_run(out), "write_ledger": runner._write_accounting,
            "report_finalize": lambda: runner._finalize(None),
        }
        for entrance, call in entrances.items():
            before = directory_digest(out)
            self.assertBlocks(reason, call)
            after = directory_digest(out)
            self.assertEqual(after, before, entrance)
            self.assertEqual(sim.requests, [], entrance)
            self.note(case, entrance=entrance, before_sha256=before, after_sha256=after, new_requests=0, outcome="BLOCKED")
        for command in ("resume", "replay", "check"):
            argv = [command, "--out", str(out)]
            if command != "replay":
                argv += ["--profile", str(self.root / "unused-profile.json")]
            if command == "resume":
                argv += ["--mode", "SIMULATION"]
            before = directory_digest(out)
            try:
                code, result = self.cli(argv)
            except Exception as exc:  # a raw error instead of a BLOCKED answer is a failed assertion, not a crash
                self.fail(f"CLI {command}: expected a BLOCKED answer, got {type(exc).__name__}: {exc}")
            self.assertEqual((code, result["state"], result["generation_calls"]), (2, "BLOCKED", 0))
            self.assertRegex(";".join(result["reasons"]), reason)
            self.assertTrue(all("," not in r for r in result["reasons"]))
            self.assertEqual(directory_digest(out), before)
            self.note(case, entrance="CLI " + command, before_sha256=before, after_sha256=directory_digest(out),
                      new_requests=0, outcome="BLOCKED", reasons=result["reasons"])

    def child(self, command, victim="none"):
        return subprocess.run([sys.executable, "-B", str(HERE / "crash_driver.py"), str(self.root), command, victim],
                              capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "TMPDIR": str(self.root),
                              "PYTHONDONTWRITEBYTECODE": "1"}, timeout=180)

    def server(self):
        return [json.loads(line) for line in (self.root / "server.jsonl").read_text().splitlines()]

    def killed(self, command="start"):
        runner, _ = self.make(log=False)
        victim = runner.order[3]
        child = self.child(command, victim)
        self.assertEqual(child.returncode, -9, child.stderr)
        self.assertEqual(len([r for r in self.server() if r["path"] == "responses"]), 4)
        return victim

    def test_A_sigkill_then_missing_ledger_is_blocked_without_writes_or_resends(self):
        self.killed()
        (self.root / "run/ledger.json").unlink()
        server_before = (self.root / "server.jsonl").read_bytes()
        before = directory_digest(self.root / "run")
        child = self.child("resume")
        self.assertEqual(child.returncode, 2, child.stderr)
        self.assertRegex(json.loads(child.stdout)["reason"], "LEDGER_MISSING")
        self.assertEqual(directory_digest(self.root / "run"), before)
        self.assertEqual((self.root / "server.jsonl").read_bytes(), server_before)
        self.note("A", kill_returncode=-9, server_generations_before=4, server_generations_after=4, new_requests=0,
                  before_sha256=before, after_sha256=directory_digest(self.root / "run"), outcome="BLOCKED")
        self.deny_all("A", "LEDGER_MISSING")

    def test_B_sigkill_then_older_snapshot_is_blocked_for_resume_and_replay(self):
        self.killed()
        (self.root / "run/ledger.json").write_bytes((self.root / "ledger-snapshot.json").read_bytes())
        before = directory_digest(self.root / "run")
        server_before = (self.root / "server.jsonl").read_bytes()
        child = self.child("resume")
        self.assertEqual(child.returncode, 2, child.stderr)
        self.assertRegex(json.loads(child.stdout)["reason"], "LEDGER_BEHIND_ATTEMPT")
        self.assertEqual(directory_digest(self.root / "run"), before)
        self.assertEqual((self.root / "server.jsonl").read_bytes(), server_before)
        self.note("B", kill_returncode=-9, server_generations_before=4, server_generations_after=4, new_requests=0,
                  before_sha256=before, after_sha256=directory_digest(self.root / "run"), outcome="BLOCKED")
        self.deny_all("B", "LEDGER_BEHIND_ATTEMPT")

    def test_C_invalid_ledger_bytes_and_shapes_are_named_and_every_entrance_is_read_only(self):
        self.interrupted()
        path = self.root / "run/ledger.json"
        original = path.read_bytes()
        state = json.loads(original)
        variants = [b"", b"garbage", b"{}", b"[]", b'{"slots": []}',
                    xr.canonical({**state, "slots": []}).encode(), xr.canonical({**state, "spent_upper": "NaN"}).encode(),
                    xr.canonical({**state, "spent_upper": "0"}).encode(), xr.canonical({**state, "events": {}}).encode(),
                    xr.canonical({**state, "overrun": True}).encode()]
        failures = []  # collected, then asserted once: a failure inside subTest is not seen by the mutation engine's classifier
        for index, content in enumerate(variants):
            path.write_bytes(content)
            try:
                self.deny_all("C", "LEDGER")
            except AssertionError as exc:
                failures.append((index, str(exc)[:300]))
        path.write_bytes(original)
        self.assertEqual(failures, [])

    def test_D_missing_unparseable_and_malformed_manifest_or_state_are_blocked(self):
        self.interrupted()
        failures = []  # collected, then asserted once: a failure inside subTest is not seen by the mutation engine's classifier
        for filename in ("run_manifest.json", "run_state.json"):
            path = self.root / "run" / filename
            original = path.read_bytes()
            for content in (None, b"garbage", b"{}", b"[]"):
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                try:
                    self.deny_all("D", "NO_RUN_TO_RESUME|RUN_MANIFEST|RUN_STATE")
                except AssertionError as exc:
                    failures.append((filename, content, str(exc)[:300]))
                finally:
                    path.write_bytes(original)
        self.assertEqual(failures, [])

    def test_E_missing_mid_corruption_torn_tail_and_bad_log_shapes_never_get_certified(self):
        self.interrupted()
        path = self.root / "run/attempts.jsonl"
        original = path.read_bytes()
        lines = original.splitlines(keepends=True)
        # the first three are refused for their own named reason (a missing log would otherwise also be caught, later and under another
        # name, by the bound on how far the ledger may be ahead of the log); the shapes below them only need an ATTEMPTS-style block
        variants = [(None, "ATTEMPTS_MISSING_OR_UNREADABLE"), (b"".join(lines[:2] + [b"broken\n"] + lines[3:]), "ATTEMPTS_INVALID_LINE"),
                    (original + b'{"phase":', "ATTEMPTS_TORN_TAIL"), (original + b"{}\n", "ATTEMPTS|LEDGER_OR_ATTEMPTS"),
                    (original + b"[]\n", "ATTEMPTS|LEDGER_OR_ATTEMPTS"), (original + b"\n", "ATTEMPTS|LEDGER_OR_ATTEMPTS")]
        failures = []  # collected, then asserted once: a failure inside subTest is not seen by the mutation engine's classifier
        for index, (content, reason) in enumerate(variants):
            if content is None:
                path.unlink()
            else:
                path.write_bytes(content)
            try:
                self.deny_all("E", reason)
            except AssertionError as exc:
                failures.append((index, str(exc)[:300]))
            finally:
                path.write_bytes(original)
        self.assertEqual(failures, [])

    def test_F_each_ledger_before_log_window_recovers_without_repeating_a_started_call(self):
        for phase in ("START", "SETTLED", "ROUND_RESERVED"):
            name = "window-" + phase
            runner, sim = self.make(name, log=False)
            append, fired = xr.AppendLog.append, []
            def crash(log, record):
                if record["phase"] == phase and not fired:
                    fired.append(True)
                    raise KeyboardInterrupt("crash between durable ledger and log")
                return append(log, record)
            with mock.patch.object(xr.AppendLog, "append", crash), self.assertRaises(KeyboardInterrupt):
                runner.start()
            before = directory_digest(runner.out_dir)
            inspection = xr.inspect_run(runner.out_dir)
            self.assertTrue(inspection["reconciliation"]["ledger_ahead"])
            started = {(key, n) for key, s in inspection["ledger"]["slots"].items() for n in range(1, s["attempts"] + 1)}
            resumed, server2 = self.make(name, log=False)
            result = self.completes(resumed.resume)  # a legitimate crash window must resume; a refusal here is a failed assertion
            self.assertEqual(result["state"], "COMPLETE")
            for key, n in started:
                self.assertEqual(self.ledger(name)["slots"][key]["attempts"], n)
                self.assertEqual(len(self.generations(server2, key.rsplit(":", 1)[0])), 4)
            all_gens = [r for r in sim.requests + server2.requests if r["path"] == "responses"]
            self.assertEqual(len(all_gens), 39 if phase == "START" else 40)
            logged = sum((CAP if r["cost_upper_usd"] is None else Decimal(r["cost_upper_usd"]))
                         for r in self.attempts(name) if r["phase"] == "SETTLED")
            self.assertGreaterEqual(Decimal(self.ledger(name)["spent_upper"]), logged)
            resume_record = [r for r in self.attempts(name) if r["phase"] == "RESUME"][-1]
            self.assertEqual(resume_record["reconciliation"], inspection["reconciliation"])
            self.note("F", window=phase, before_sha256=before, after_sha256=directory_digest(runner.out_dir),
                      server_generations=len(all_gens), spent_upper=self.ledger(name)["spent_upper"],
                      logged_spent=str(logged), outcome="COMPLETE", reconciliation=resume_record["reconciliation"])

    def test_G_sigkill_after_durable_overrun_response_charges_actual_cost_and_halts(self):
        victim = self.killed("start-overrun")
        before = directory_digest(self.root / "run")
        server_before = (self.root / "server.jsonl").read_bytes()
        result = self.child("resume")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = json.loads(result.stdout)
        self.assertIsNotNone(result["halt"])
        self.assertEqual((result["state"], result["halt"]["reason"]), ("HALTED", "ATTEMPT_CAP_OVERRUN"))
        ledger = self.ledger()
        self.assertEqual(Decimal(ledger["spent_upper"]), Decimal("1.001368"))
        self.assertIs(ledger["overrun"], True)
        self.assertEqual(Decimal(ledger["slots"][f"{victim}:0"]["receipts"][0]["cost"]), Decimal("1.00018"))
        self.assertEqual((self.root / "server.jsonl").read_bytes(), server_before)
        self.note("G", kill_returncode=-9, before_sha256=before, after_sha256=directory_digest(self.root / "run"),
                  server_generations_before=4, server_generations_after=4, new_requests=0,
                  spent_upper=ledger["spent_upper"], outcome="HALTED ATTEMPT_CAP_OVERRUN")

    def test_H_instruction_swap_after_check_is_blocked_before_send(self):
        import test_execution_profile as tp
        directory = self.root / "profile-input"
        directory.mkdir()
        world = tp.World(directory)
        profile = world.save()
        check = ep.check_profile
        def replace_after_check(*args, **kwargs):
            result = check(*args, **kwargs)
            world.pack.write_text("replacement instructions")
            return result
        out = self.root / "race"
        before = directory_digest(out)
        with mock.patch.object(ep, "check_profile", replace_after_check):
            try:
                code, result = self.cli(["run", "--profile", str(profile), "--out", str(out), "--mode", "SIMULATION",
                                        "--allow-formal-simulation"])
            except Exception as exc:
                self.fail(f"expected a named block before any send: {type(exc).__name__}: {exc}")
        self.assertEqual((code, result["state"], result["generation_calls"]), (2, "BLOCKED", 0))
        self.assertIn("INSTRUCTION_HASH_MISMATCH_OR_UNREADABLE", result["reasons"])
        self.assertFalse(out.exists())
        self.note("H", new_requests=0, before_sha256=before, after_sha256=directory_digest(out), outcome="BLOCKED")

    def test_H_runner_checks_the_profile_pin_and_actual_outgoing_instructions(self):
        profile = self.root / "pin-profile.json"
        profile.write_text(xr.canonical({"instruction": {"sha256": xr.sha256_text("pinned")},
                                       "bank": {"manifest": ep.pin(self.manifest)}}))
        runner, sim = self.make("pin", log=False)
        runner = xr.Runner(runner.out_dir, mode="SIMULATION", bank_manifest=self.manifest, instructions="wrong",
                           budget="50", attempt_cap=CAP, transport=sim, profile=profile)
        self.assertBlocks("INSTRUCTIONS_DO_NOT_MATCH_PROFILE", runner.start)
        self.assertEqual(sim.requests, [])
        self.assertFalse(runner.out_dir.exists())
        log = xr.AppendLog(self.root / "unused-log.jsonl")
        recorder = xr.Recorder(sim, log, xr.sha256_text("pinned"))
        self.assertBlocks("SENT_INSTRUCTIONS_HASH_MISMATCH", lambda: recorder("responses", {"instructions": "wrong"}))
        self.assertFalse(log.path.exists())
        self.assertEqual(sim.requests, [])

    def test_I_complete_or_identity_or_lock_or_wiring_refusals_preserve_every_byte(self):
        self.run_it()
        out = self.root / "run"
        before = directory_digest(out)
        runner, sim = self.make(log=False)
        self.assertBlocks("RUN_IS_COMPLETE", runner.resume)
        self.assertEqual(directory_digest(out), before)
        self.assertEqual(sim.requests, [])
        self.note("I", entrance="Runner.resume COMPLETE", new_requests=0, before_sha256=before,
                  after_sha256=directory_digest(out), outcome="BLOCKED RUN_IS_COMPLETE")
        self.interrupted("open")
        out = self.root / "open"
        before = directory_digest(out)
        for kwargs, reason in (({"instructions": "different"}, "RESUME_IDENTITY_MISMATCH"), ({}, "SIMULATION_NEEDS")):
            runner, sim = self.make("open", log=False, **kwargs)
            if not kwargs:
                runner.transport = None
            self.assertBlocks(reason, runner.resume)
            self.assertEqual(directory_digest(out), before)
            self.assertEqual(sim.requests, [])
        descriptor = os.open(out / "run.lock", os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            runner, sim = self.make("open", log=False)
            self.assertBlocks("ANOTHER_RUNNER_HOLDS", runner.resume)
            self.assertEqual(directory_digest(out), before)
            self.assertEqual(sim.requests, [])
        finally:
            os.close(descriptor)
        self.note("I", entrance="identity/wiring/lock", new_requests=0, before_sha256=before,
                  after_sha256=directory_digest(out), outcome="BLOCKED")

    def test_J_both_help_entries_without_B_leave_no_bytecode_in_external_copy(self):
        clone = self.root / "bytecode-copy"
        for name in ("fable-ensemble3-runner", "fable-ensemble3-r1", "fable-ensemble3-lab", "fable-ensemble3-bank"):
            shutil.copytree(HERE.parent / name, clone / "analysis" / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        package = clone / "analysis/fable-ensemble3-runner"
        for entry in ("exploration_runner.py", "probe_2b.py"):
            before = directory_digest(package)
            result = subprocess.run([sys.executable, str(package / entry), "--help"], capture_output=True, text=True,
                                    env={"PATH": "/usr/bin:/bin", "TMPDIR": str(self.root)}, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(package.rglob("*.pyc")), [])
            self.assertEqual(list(package.rglob("__pycache__")), [])
            self.assertEqual(list(clone.rglob("*.pyc")), [])
            self.assertEqual(directory_digest(package), before)
            self.note("J", entry=entry, no_B=True, bytecode=[], before_sha256=before,
                      after_sha256=directory_digest(package), outcome="PASS")
        # R3 (legacy R7 selector, additions only): Apple's Python sends unsuppressed bytecode to a per-user cache when HOME is set,
        # and a harness may hand this child its own PYTHONPYCACHEPREFIX; either way the copy above stays clean and proves nothing.
        # -X pycache_prefix outranks both, so here every unsuppressed write of the copied product lands in a directory this test owns.
        for entry in ("exploration_runner.py", "probe_2b.py"):
            prefix = self.root / f"J-prefix-{entry}"
            result = subprocess.run([sys.executable, "-X", f"pycache_prefix={prefix}", str(package / entry), "--help"], capture_output=True,
                                    text=True, env={"PATH": "/usr/bin:/bin", "TMPDIR": str(self.root)}, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(p.name for p in prefix.rglob("*.pyc") if clone.name in p.parts), [], entry)
            self.note("J", entry=entry, no_B=True, explicit_cache_prefix=True, bytecode=[], outcome="PASS")

    def test_K_simulation_rejects_unmarked_callable_and_live_adapter_before_any_write(self):
        for index, transport in enumerate((lambda path, body: {}, api.ResponsesAdapter("x")._post)):
            runner, sim = self.make("unmarked-" + str(index), log=False)
            runner.transport = transport
            before = directory_digest(runner.out_dir)
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-NOT-A-REAL-KEY"}):
                self.assertBlocks("SIMULATION_TRANSPORT_NOT_SIMULATED", runner.start)
            self.assertFalse(runner.out_dir.exists())
            self.assertEqual(sim.requests, [])
            self.assertEqual(urllib_calls(), 0)
            self.assertEqual(directory_digest(runner.out_dir), before)
            self.note("K", caller="Runner", transport=index, new_requests=0, network_calls=0, outcome="BLOCKED",
                      before_sha256=before, after_sha256=directory_digest(runner.out_dir))
        self.assertIs(SimulatedResponses.is_simulated, True)

    def test_L_judge_appendix_A_now_blocks_with_only_the_four_pre_kill_generations(self):
        _, server = self.interrupted()
        self.assertEqual(len([r for r in server.requests if r["path"] == "responses"]), 4)
        (self.root / "run/ledger.json").unlink()
        before = directory_digest(self.root / "run")
        runner, _ = self.make(log=False)
        runner.transport = server
        self.assertBlocks("LEDGER_MISSING", runner.resume)
        self.assertEqual(len([r for r in server.requests if r["path"] == "responses"]), 4)
        self.assertEqual(directory_digest(self.root / "run"), before)
        self.note("L", server_generations_before=4, server_generations_after=4, new_requests=0,
                  before_sha256=before, after_sha256=directory_digest(self.root / "run"), outcome="BLOCKED")

    def test_M_normal_kill_resumes_and_pre_first_save_initialization_remains_legal(self):
        victim = self.killed()
        before = directory_digest(self.root / "run")
        child = self.child("resume")
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout)["state"], "COMPLETE")
        self.assertEqual(len([r for r in self.server() if r["path"] == "responses"]), 40)
        self.assertEqual(len([r for r in self.server() if r["path"] == "responses" and r["item_id"] == victim]), 5)
        self.assertEqual(Decimal(self.ledger()["spent_upper"]), 39 * OK_COST + CAP)
        self.note("M", kill_returncode=-9, before_sha256=before, after_sha256=directory_digest(self.root / "run"),
                  server_generations_before=4, server_generations_after=40, spent_upper=self.ledger()["spent_upper"], outcome="COMPLETE")
        runner, server = self.make("first-save", log=False)
        with mock.patch.object(bl.Ledger, "_save", side_effect=KeyboardInterrupt("before first save")), self.assertRaises(KeyboardInterrupt):
            runner.start()
        self.assertFalse((runner.out_dir / "ledger.json").exists())
        inspection = xr.inspect_run(runner.out_dir)
        self.assertTrue(inspection["reconciliation"]["pre_first_save"])
        resumed, server = self.make("first-save", log=False)
        result = resumed.resume()
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual(len([r for r in server.requests if r["path"] == "responses"]), 40)
        self.note("M", entrance="pre_first_save", server_generations=40, outcome="COMPLETE")

    def test_bank_manifest_and_fixtures_are_hashed_and_parsed_from_one_read(self):
        read_bytes = Path.read_bytes
        counts = {}
        paths = {self.manifest} | {Path(row[ref]) for row in json.loads(self.manifest.read_bytes())["items"]
                                  for ref in ("public_ref", "oracle_ref")}
        def swap_after_read(path):
            content = read_bytes(path)
            if path in paths:
                counts[str(path)] = counts.get(str(path), 0) + 1
                path.write_bytes(b"replacement non-JSON")
            return content
        with mock.patch.object(Path, "read_bytes", swap_after_read):
            try:
                bank, public, labels = xr.load_bank(self.manifest)
            except Exception as exc:
                self.fail(f"bank must consume the already hashed bytes: {type(exc).__name__}: {exc}")
        self.assertEqual(public, self.public)
        self.assertEqual(labels, self.labels)
        self.assertEqual(counts, {str(path): 1 for path in paths})

    def test_log_settlement_round_and_overrun_cannot_lead_the_ledger(self):
        self.interrupted()
        ledger_path, log_path = self.root / "run/ledger.json", self.root / "run/attempts.jsonl"
        original_ledger, original_log = ledger_path.read_bytes(), log_path.read_bytes()
        ledger = json.loads(original_ledger)
        settled = next(r for r in self.attempts() if r["phase"] == "SETTLED")
        slot = ledger["slots"][settled["slot"]]
        ledger["spent_upper"] = str(Decimal(ledger["spent_upper"]) - Decimal(slot["receipts"][0]["cost"]))
        slot.update(state="RUNNING", receipts=[], held=str(2 * CAP))
        slot.pop("status"); slot.pop("verified_run")
        ledger_path.write_text(xr.canonical(ledger))
        self.deny_all("B", "LEDGER_BEHIND_SETTLED")
        ledger_path.write_bytes(original_ledger)
        log_path.write_bytes(original_log + json.dumps({"phase": "ROUND_RESERVED", "round": 1, "t": 0}).encode() + b"\n")
        self.deny_all("B", "LEDGER_BEHIND_ROUND_RESERVED")
        records = [json.loads(line) for line in original_log.splitlines()]
        next(r for r in records if r["phase"] == "SETTLED")["kind"] = "ATTEMPT_CAP_OVERRUN"
        log_path.write_text("".join(json.dumps(r) + "\n" for r in records))
        self.deny_all("B", "LEDGER_BEHIND_OVERRUN")

    def test_N_an_emptied_or_truncated_log_far_behind_the_ledger_is_refused(self):
        self.interrupted()
        out = self.root / "run"
        path = out / "attempts.jsonl"
        original = path.read_bytes()
        lines = original.splitlines(keepends=True)
        self.completes(lambda: xr.inspect_run(out))  # the untouched log is accepted
        failures = []
        for index, content in enumerate((b"", b"".join(lines[:1]), b"".join(lines[:3]))):
            path.write_bytes(content)  # the ledger still shows three settled slots and a started fourth: far more than one crash window
            try:
                self.deny_all("N", "ATTEMPTS_BEHIND_LEDGER")
            except AssertionError as exc:
                failures.append((index, str(exc)[:300]))
            finally:
                path.write_bytes(original)
        self.assertEqual(failures, [])

    def test_O_several_crashes_in_a_row_in_different_windows_still_resume_and_never_resend(self):
        name = "chain"
        out = self.root / name
        append = xr.AppendLog.append
        sims = []
        # four START windows in a row leave three recovered settles by the fifth resume: without the acknowledgement of recovered
        # slots that already exceeds the tolerance, so the chain is long enough to need it
        for cycle, window in enumerate(("START", "START", "START", "START", "SETTLED", "START")):
            runner, sim = self.make(name, log=False)
            sims.append(sim)
            fired = []

            def crash(log, record, window=window, fired=fired):
                if record["phase"] == window and not fired:
                    fired.append(True)
                    raise KeyboardInterrupt("crash between a durable ledger write and its log record")
                return append(log, record)

            with mock.patch.object(xr.AppendLog, "append", crash):
                self.crashes(runner.start if cycle == 0 else runner.resume)
            self.completes(lambda: xr.inspect_run(out))  # the state each crash leaves is still a legitimate one
        final, sim = self.make(name, log=False)
        sims.append(sim)
        result = self.completes(final.resume)
        self.assertEqual(result["state"], "COMPLETE")
        sent = [r for s in sims for r in s.requests if r["path"] == "responses"]
        per_item = {}
        for r in sent:
            per_item[r["item_id"]] = per_item.get(r["item_id"], 0) + 1
        self.assertLessEqual(len(sent), 40)
        self.assertLessEqual(max(per_item.values()), 5)  # one call per item and round: nothing was sent twice
        logged = sum((CAP if r["cost_upper_usd"] is None else Decimal(r["cost_upper_usd"])) for r in self.attempts(name) if r["phase"] == "SETTLED")
        self.assertGreaterEqual(Decimal(self.ledger(name)["spent_upper"]), logged)

    def test_P_a_crash_inside_resume_after_recovery_and_before_its_log_record_resumes_again(self):
        name = "midresume"
        out = self.root / name
        append = xr.AppendLog.append
        runner, sim = self.make(name, log=False)
        fired = []

        def crash_at_start(log, record):
            if record["phase"] == "START" and not fired:
                fired.append(True)
                raise KeyboardInterrupt("crash after the ledger begin, before the START record")
            return append(log, record)

        with mock.patch.object(xr.AppendLog, "append", crash_at_start):
            self.crashes(runner.start)
        middle, sim2 = self.make(name, log=False)

        def crash_at_resume(log, record):
            if record["phase"] == "RESUME":
                raise KeyboardInterrupt("crash after the recovery was saved, before its record")
            return append(log, record)

        with mock.patch.object(xr.AppendLog, "append", crash_at_resume):
            self.crashes(middle.resume)
        self.assertEqual(sim2.requests, [])
        self.completes(lambda: xr.inspect_run(out))  # two unacknowledged records (the original gap and the recovered settle) are tolerated
        final, sim3 = self.make(name, log=False)
        result = self.completes(final.resume)
        self.assertEqual(result["state"], "COMPLETE")
        sent = [r for s in (sim, sim2, sim3) for r in s.requests if r["path"] == "responses"]
        self.assertEqual(len(sent), 39)  # the first call never left, and nothing was sent twice


# --- R2: an independent oracle for the journal gate ------------------------------------------------------------------------------
# Written from the R2 Packet, not from the product: it never calls the runner's gate code and never reads its constant.
ORACLE_BOUND = 3  # the Packet's absolute bound on ledger facts that have no concrete attempt-log record
ORACLE_SEED = 20261005
PAYLOAD_PHASES = ("REQUEST", "RESPONSE", "ERROR")


def oracle_truth(ledger: dict) -> dict:
    """Every fact the ledger holds, by identity, with the content a record of it must carry."""
    truth = {}
    for key, slot in ledger["slots"].items():
        truth[("RR", int(key.rsplit(":", 1)[1]))] = None
        for n in range(1, slot["attempts"] + 1):
            truth[("START", key, n)] = None
        for n, receipt in enumerate(slot["receipts"], 1):
            truth[("SETTLED", key, n)] = (receipt["status"], Decimal(receipt["cost"]), receipt["cost_known"])
    return truth


def oracle_fact(row: dict, cap: Decimal):
    """(identity, content) of a concrete fact record or of a request/response/error row; None for anything else (RESUME)."""
    phase = row["phase"]
    if phase == "ROUND_RESERVED":
        return ("RR", row["round"]), None
    if phase == "START":
        return ("START", row["slot"], row["attempt"]), None
    if phase == "SETTLED":
        cost = row["cost_upper_usd"]
        return ("SETTLED", row["slot"], row["attempt"]), (row["status"], cap if cost is None else Decimal(cost), cost is not None)
    if phase in PAYLOAD_PHASES:
        return ("PAYLOAD", row["slot"], row["attempt"]), None
    return None


def oracle_needs(fact) -> list:
    """Facts that must be on record earlier: the causal order in which the runner writes."""
    if fact[0] == "RR":
        return [("RR", fact[1] - 1)] if fact[1] > 0 else []
    if fact[0] == "START":
        return [("RR", int(fact[1].rsplit(":", 1)[1]))] + ([("SETTLED", fact[1], fact[2] - 1)] if fact[2] > 1 else [])
    return [("START", fact[1], fact[2])]  # a settlement, a request, a response or an error needs its start


def oracle_verdict(ledger: dict, records: list) -> str:
    """ACCEPT unless the log is ahead of the ledger, a fact is on record twice or out of order, or more than ORACLE_BOUND
    ledger facts have no concrete record.  Positions and set differences only; RESUME records are never read."""
    cap, truth = Decimal(ledger["attempt_cap"]), oracle_truth(ledger)
    concrete = [(index, found) for index, found in ((i, oracle_fact(row, cap)) for i, row in enumerate(records)) if found]
    for _, (fact, content) in concrete:
        identity = ("START",) + fact[1:] if fact[0] == "PAYLOAD" else fact
        if identity not in truth or (fact[0] != "PAYLOAD" and truth[identity] != content):
            return "LOG_AHEAD_OF_LEDGER"
    position = {}
    for index, (fact, _) in concrete:
        if fact[0] != "PAYLOAD":
            if fact in position:
                return "DUPLICATE_FACT"
            position[fact] = index
    for index, (fact, _) in concrete:
        if any(position.get(need, len(records)) > index for need in oracle_needs(fact)):
            return "OUT_OF_ORDER"
    return "ACCEPT" if len(set(truth) - set(position)) <= ORACLE_BOUND else "MISSING_BEYOND_BOUND"


def oracle_frontier(ledger: dict, records: list) -> list:
    """Indexes of fact records that nothing on record depends on: the only records a crash could have lost."""
    cap = Decimal(ledger["attempt_cap"])
    found = [(i, oracle_fact(row, cap)) for i, row in enumerate(records)]
    needed = {need for _, f in found if f for need in oracle_needs(f[0])}
    return [i for i, f in found if f and f[0][0] != "PAYLOAD" and f[0] not in needed]


def fact_name(fact) -> str:
    return f"ROUND_RESERVED:{fact[1]}" if fact[0] == "RR" else f"{fact[0]}:{fact[1]}:{fact[2]}"


def forged_record(ledger: dict, fact) -> dict:
    """A shape-valid concrete record of `fact`, marked reconciled, carrying the content the ledger gives it."""
    marks = {"t": 1.0, "reconciled": True, "derived_from": "ledger.json", "original_payload": "LOST"}
    if fact[0] == "RR":
        return {"phase": "ROUND_RESERVED", "round": fact[1], **marks}
    if fact[0] == "START":
        return {"phase": "START", "slot": fact[1], "attempt": fact[2], **marks}
    receipt = ledger["slots"][fact[1]]["receipts"][fact[2] - 1]
    return {"phase": "SETTLED", "slot": fact[1], "attempt": fact[2], "status": receipt["status"], "kind": "RECONCILED_FROM_LEDGER",
            "detail": "forged", "cost_upper_usd": receipt["cost"] if receipt["cost_known"] else None,
            "charged_cap_because_unknown": not receipt["cost_known"], "ledger_state": None, **marks}


def absent_records(ledger: dict) -> list:
    """Shape-valid reconciled records of facts the ledger does not hold: an unreserved round, an unstarted attempt, a wrong settlement."""
    slots = ledger["slots"]
    fresh = next(key for key, slot in sorted(slots.items()) if slot["attempts"] < 2)
    settled = next(key for key, slot in sorted(slots.items()) if slot["receipts"])
    wrong = forged_record(ledger, ("SETTLED", settled, 1))
    wrong["status"] = "INCOMPLETE" if wrong["status"] == "OK" else "OK"
    last_round = max(int(key.rsplit(":", 1)[1]) for key in slots)
    return [forged_record(ledger, ("RR", last_round + 1)), forged_record(ledger, ("START", fresh, slots[fresh]["attempts"] + 1)), wrong]


def forged_resume(claims: list, k: int) -> dict:
    """A shape-valid RESUME record that claims k backfilled facts, in one of several shapes."""
    record = {"phase": "RESUME", "t": 2.0, "recovered_uncertain": [], "reconciliation": {"pre_first_save": False, "ledger_ahead": claims[:k]}}
    if k % 3 == 1:
        record["recovered_uncertain"] = ["no-such-item:9"]
    elif k % 3 == 2:
        record.update(backfilled=claims[:k], acknowledged=True)
    else:
        record["reconciliation"]["verified_by"] = "the journal itself"
    return record


class ProductGate:
    """The product's own gate, `xr.inspect_run`, on in-memory records: only the four file reads are replaced, every decision is
    the product's.  A cross-check in the random test compares it with the unpatched file path."""

    def __init__(self, out: Path):
        self.out, self.rows = out, None
        self.parsed = {name: json.loads((out / name).read_bytes()) for name in ("run_manifest.json", "run_state.json", "ledger.json")}

    def __enter__(self):
        self.patches = [mock.patch.object(xr, "_read_state", lambda path, reason: self.parsed[Path(path).name]),
                        mock.patch.object(xr.AppendLog, "records", lambda log: self.rows)]
        for patcher in self.patches:
            patcher.start()
        return self

    def __exit__(self, *exc):
        for patcher in reversed(self.patches):
            patcher.stop()

    def __call__(self, rows: list) -> str:
        self.rows = rows
        return gate_outcome(lambda: xr.inspect_run(self.out))


def gate_outcome(call) -> str:
    try:
        call()
    except xr.RunBlocked as blocked:
        return "REFUSED:" + str(blocked)
    except Exception as exc:  # a crash is a disagreement with any verdict, never a refusal
        return f"CRASHED:{type(exc).__name__}:{exc}"
    return "ACCEPTED"


def exhaustive_damage(rows: list):
    """Every single and double deletion, duplication and reordering of `rows`."""
    n = len(rows)
    for i in range(n):
        yield "delete-1", rows[:i] + rows[i + 1:]
    for i, j in itertools.combinations(range(n), 2):
        yield "delete-2", [row for k, row in enumerate(rows) if k not in (i, j)]
    for i in range(n):
        for j in range(n + 1):
            yield "duplicate-1", rows[:j] + [rows[i]] + rows[j:]
    for i, j in itertools.combinations(range(n), 2):
        yield "duplicate-2", [x for k, row in enumerate(rows) for x in ([row, row] if k in (i, j) else [row])]
    for i in range(n):
        rest = rows[:i] + rows[i + 1:]
        for j in range(n):
            if j != i:
                yield "move-1", rest[:j] + [rows[i]] + rest[j:]
    for i, j in itertools.combinations(range(n), 2):
        swapped = list(rows)
        swapped[i], swapped[j] = rows[j], rows[i]
        yield "swap-2", swapped


def random_damage(rng: random.Random, ledger: dict, rows: list):
    """One to four random damages; frontier deletions are favoured so that the bound itself is crossed often."""
    rows, names = list(rows), []
    for _ in range(rng.randint(1, 4)):
        op = rng.choice(("frontier", "frontier", "frontier", "delete", "duplicate", "move", "swap", "truncate"))
        names.append(op)
        if op == "frontier":
            for _ in range(rng.randint(1, 5)):
                frontier = oracle_frontier(ledger, rows)
                if frontier:
                    rows.pop(rng.choice(frontier))
        elif op == "delete":
            rows.pop(rng.randrange(len(rows)))
        elif op == "duplicate":
            rows.insert(rng.randrange(len(rows) + 1), rows[rng.randrange(len(rows))])
        elif op == "move":
            row = rows.pop(rng.randrange(len(rows)))
            rows.insert(rng.randrange(len(rows) + 1), row)
        elif op == "swap":
            i, j = rng.randrange(len(rows)), rng.randrange(len(rows))
            rows[i], rows[j] = rows[j], rows[i]
        else:
            del rows[len(rows) - rng.randint(1, 12):]
    return "+".join(names), rows


class JournalHelpers:
    """Run builders shared by the R2 tests (no tests of its own)."""

    def crash_on(self, phase, slot=None):
        append, fired = xr.AppendLog.append, []

        def crash(log, record):
            if record["phase"] == phase and (slot is None or record.get("slot") == slot) and not fired:
                fired.append(True)
                raise KeyboardInterrupt(f"crash between the ledger write and its {phase} record")
            return append(log, record)

        return mock.patch.object(xr.AppendLog, "append", crash)

    def dying(self, sim, item, occurrence):
        calls = []

        def dies(path, body):
            response = sim(path, body)
            if path == "responses" and json.loads(body["input"])["item_id"] == item:
                calls.append(1)
                if len(calls) == occurrence:
                    raise KeyboardInterrupt("the request already reached the simulated server")
            return response

        dies.is_simulated = True
        return dies

    def journal_runner(self, name, bank, budget, faults=()):
        _, public, _ = xr.load_bank(bank)
        sim = SimulatedResponses(public, answer_for=self.answer, faults=faults)
        runner = xr.Runner(self.root / name, mode="SIMULATION", bank_manifest=bank, instructions="synthetic instructions",
                           budget=budget, attempt_cap=CAP, target_rounds=5, seed=6302, transport=sim)
        return runner, sim

    def small_base(self) -> Path:
        """Two items, three rounds: an infra retry, a crash in the START window and its resume, then a kill mid-call."""
        manifest = json.loads(self.manifest.read_text())
        manifest["items"] = manifest["items"][:2]
        bank = self.root / "small-bank.json"
        bank.write_text(json.dumps(manifest))
        budget = str(2 * 2 * CAP * 3)
        first, _ = self.journal_runner("small", bank, budget)
        a, b = first.order
        first.transport = SimulatedResponses(first.public, answer_for=self.answer, faults=[
            {"path": "responses/input_tokens", "item_id": a, "occurrence": 1, "fault": "conn_error"}])
        with self.crash_on("START", f"{b}:0"):
            self.crashes(first.start)
        second, sim = self.journal_runner("small", bank, budget)
        second.transport = self.dying(sim, a, 2)
        self.crashes(second.resume)
        return self.root / "small"

    def large_base(self) -> Path:
        """Eight items, five rounds: a retry, crashes in the SETTLED, ROUND_RESERVED and START windows, then a kill mid-call."""
        first, _ = self.journal_runner("large", self.manifest, "50")
        order = first.order
        first.transport = SimulatedResponses(self.public, answer_for=self.answer, faults=[
            {"path": "responses/input_tokens", "item_id": order[2], "occurrence": 1, "fault": "conn_error"}])
        with self.crash_on("SETTLED", f"{order[5]}:1"):
            self.crashes(first.start)
        for phase, slot in (("ROUND_RESERVED", None), ("START", f"{order[0]}:3")):
            again, _ = self.journal_runner("large", self.manifest, "50")
            with self.crash_on(phase, slot):
                self.crashes(again.resume)
        last, sim = self.journal_runner("large", self.manifest, "50")
        last.transport = self.dying(sim, order[4], 2)
        self.crashes(last.resume)
        return self.root / "large"


class JournalOracleTests(JournalHelpers, RunnerCase):
    """R2-3: the product gate and an independent oracle must agree on every damaged log, with and without forged records."""

    def compare(self, gate, ledger, family, rows, ks, case_index, tally) -> None:
        truth, cap = oracle_truth(ledger), Decimal(ledger["attempt_cap"])
        present = {found[0] for found in (oracle_fact(row, cap) for row in rows) if found and found[0][0] != "PAYLOAD"}
        missing = sorted(set(truth) - present)
        claims = [fact_name(f) for f in missing] + [fact_name(f) for f in sorted(truth) if f not in set(missing)]
        refill = missing[0] if missing else sorted(present)[case_index % len(present)]
        for forged_kind, forged in (("none", []), ("reconciled-absent", [absent_records(ledger)[case_index % 3]]),
                                    ("reconciled-present", [forged_record(ledger, refill)])):
            plain = gate(rows + forged)
            for k in [None] + ks:
                variant = rows + forged + ([] if k is None else [forged_resume(claims, k)])
                verdict, product = oracle_verdict(ledger, variant), (plain if k is None else gate(variant))
                tally["comparisons"] += 1
                tally["oracle"][verdict] = tally["oracle"].get(verdict, 0) + 1
                outcome = product.split(":")[0] if not product.startswith("REFUSED") else "REFUSED:" + product.split(":")[1]
                tally["product"][outcome] = tally["product"].get(outcome, 0) + 1
                where = {"family": family, "case": case_index, "forged": forged_kind, "resume_claims": k, "oracle": verdict,
                         "product": product[:160], "missing_concrete": len(missing)}
                if (verdict == "ACCEPT") != (product == "ACCEPTED"):
                    cause = ("SELF_VOUCHING_RESUME_FLIPPED_A_REFUSAL" if k is not None and plain != "ACCEPTED" and product == "ACCEPTED"
                             else f"{verdict}->{outcome}")
                    tally["disagreement_causes"][cause] = tally["disagreement_causes"].get(cause, 0) + 1
                    tally["disagreements"] += 1
                    if len(tally["samples"]) < 12:
                        tally["samples"].append(where)
                if k is not None and product != plain:  # I4: a RESUME record must change neither the decision nor the reason
                    tally["resume_changed_outcome"] += 1
                    if len(tally["i4_samples"]) < 6:
                        tally["i4_samples"].append({**where, "without_resume": plain[:160]})

    @staticmethod
    def new_tally(**extra) -> dict:
        return {"comparisons": 0, "disagreements": 0, "resume_changed_outcome": 0, "oracle": {}, "product": {},
                "disagreement_causes": {}, "samples": [], "i4_samples": [], "families": {}, **extra}

    def test_R2_oracle_agrees_with_the_gate_on_every_single_and_double_damage_of_a_small_run(self):
        out = self.small_base()
        rows = xr.AppendLog(out / "attempts.jsonl").records()
        frozen = json.dumps(rows, sort_keys=True)
        ledger = json.loads((out / "ledger.json").read_text())
        tally = self.new_tally(base_records=len(rows), base_ledger_facts=len(oracle_truth(ledger)), base=oracle_verdict(ledger, rows))
        with ProductGate(out) as gate:
            tally["base_product"] = gate(rows)
            for index, (family, damaged) in enumerate(exhaustive_damage(rows)):
                tally["families"][family] = tally["families"].get(family, 0) + 1
                self.compare(gate, ledger, family, damaged, [1 + index % 8], index, tally)
        tally["cases"] = sum(tally["families"].values())
        print("R2_ORACLE " + json.dumps({"test": "exhaustive-small", **tally}, sort_keys=True))
        n = len(rows)
        self.assertEqual(tally["families"], {"delete-1": n, "delete-2": n * (n - 1) // 2, "duplicate-1": n * (n + 1),
                                             "duplicate-2": n * (n - 1) // 2, "move-1": n * (n - 1), "swap-2": n * (n - 1) // 2})
        self.assertEqual((tally["base"], tally["base_product"]), ("ACCEPT", "ACCEPTED"))
        self.assertEqual(json.dumps(rows, sort_keys=True), frozen)  # nothing the gate read was changed by it
        self.assertEqual((tally["disagreements"], tally["samples"]), (0, []))
        self.assertEqual((tally["resume_changed_outcome"], tally["i4_samples"]), (0, []))

    def test_R2_oracle_agrees_with_the_gate_on_seeded_random_damage_of_a_larger_run(self):
        out = self.large_base()
        rows = xr.AppendLog(out / "attempts.jsonl").records()
        ledger = json.loads((out / "ledger.json").read_text())
        rng = random.Random(ORACLE_SEED)
        cases = [random_damage(rng, ledger, rows) for _ in range(520)]
        tally = self.new_tally(seed=ORACLE_SEED, base_records=len(rows), base_ledger_facts=len(oracle_truth(ledger)),
                               base=oracle_verdict(ledger, rows))
        with ProductGate(out) as gate:
            tally["base_product"] = gate(rows)
            for index, (family, damaged) in enumerate(cases):
                tally["families"][family.split("+")[0]] = tally["families"].get(family.split("+")[0], 0) + 1
                self.compare(gate, ledger, family, damaged, list(range(1, 9)), index, tally)
            mocked = [gate(damaged) for _, damaged in cases[:60]]
        copy = self.root / "large-file-path"
        shutil.copytree(out, copy)
        unpatched = []
        for _, damaged in cases[:60]:  # the same decisions through the real file reads, nothing patched
            (copy / "attempts.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in damaged))
            unpatched.append(gate_outcome(lambda: xr.inspect_run(copy)).replace(str(copy), str(out)))
        tally["cases"] = len(cases)
        tally["file_path_cross_check"] = {"cases": len(unpatched), "equal": mocked == unpatched}
        print("R2_ORACLE " + json.dumps({"test": "random-large", **tally}, sort_keys=True))
        self.assertGreaterEqual(tally["cases"], 500)
        self.assertEqual((tally["base"], tally["base_product"]), ("ACCEPT", "ACCEPTED"))
        self.assertEqual(mocked, unpatched)
        self.assertGreater(min(tally["oracle"].get(v, 0) for v in ("ACCEPT", "MISSING_BEYOND_BOUND", "OUT_OF_ORDER", "DUPLICATE_FACT")), 0)
        self.assertEqual((tally["disagreements"], tally["samples"]), (0, []))
        self.assertEqual((tally["resume_changed_outcome"], tally["i4_samples"]), (0, []))


def fact_of(row: dict):
    """The product's identity of a concrete fact record, as it lists missing facts (any other record gets a non-matching one)."""
    return ("ROUND_RESERVED", row.get("round"), None) if row["phase"] == "ROUND_RESERVED" else (row["phase"], row.get("slot"), row.get("attempt"))


def moved_before(rows: list, i: int, j: int) -> list:
    """rows with rows[i] moved to just before the record that was at index j."""
    result = list(rows)
    row = result.pop(i)
    result.insert(j if j < i else j - 1, row)
    return result


class R2IntegrityTests(JournalHelpers, RunnerCase):
    """R2-1/R2-4/R2-6: named regressions for I1-I5, the write-back windows, the SIMULATION boundary and the bytecode check."""

    ENTRANCES = ("inspect_run", "Runner.resume", "replay_run", "report", "write_ledger", "CLI resume", "CLI replay", "CLI check")

    def cli(self, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = xr.main(argv)
        return code, json.loads(output.getvalue())

    def rows_of(self, name) -> list:
        return xr.AppendLog(self.root / name / "attempts.jsonl").records()

    def write_rows(self, name, rows) -> None:
        (self.root / name / "attempts.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))

    def interrupted_at(self, name, position, faults=()):
        """A run killed in-process mid-call: the generation of order[position] in round 0 reached the server, nothing settled it."""
        runner, sim = self.make(name, log=False, faults=faults)
        runner.transport = self.dying(sim, runner.order[position], 1)
        self.crashes(runner.start)
        return runner, sim

    def sigkilled(self) -> str:
        """A real child process SIGKILLed mid-call (the crash driver): four generations reached the server, the fourth unsettled."""
        probe, _ = self.make(log=False)
        victim = probe.order[3]
        child = subprocess.run([sys.executable, "-B", str(HERE / "crash_driver.py"), str(self.root), "start", victim],
                               capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "TMPDIR": str(self.root),
                                                                    "PYTHONDONTWRITEBYTECODE": "1"}, timeout=180)
        self.assertEqual(child.returncode, -9, child.stderr)
        return victim

    def kill_at_append(self, n):
        append, calls = xr.AppendLog.append, []

        def kill(log, record):
            calls.append(record["phase"])
            if len(calls) == n:
                raise KeyboardInterrupt(f"killed at write {n} ({record['phase']})")
            return append(log, record)

        return mock.patch.object(xr.AppendLog, "append", kill)

    def refused_everywhere(self, case, reason, name="run") -> list:
        """I5 at all eight entrances: a refusal naming `reason`, no request reaches the server, not one byte of the run directory
        (the lock included) changes.  Failures are collected and asserted once, so a missing guard is a FAIL, never an ERROR."""
        out = self.root / name
        runner, sim = self.make(name, log=False)
        server_log = out / "simulated_server_log.jsonl"
        calls = [("inspect_run", lambda: xr.inspect_run(out)), ("Runner.resume", runner.resume), ("replay_run", lambda: xr.replay_run(out)),
                 ("report", lambda: runner._finalize(None)), ("write_ledger", runner._write_accounting)]
        for command in ("resume", "replay", "check"):
            argv = [command, "--out", str(out)] + ([] if command == "replay" else ["--profile", str(self.root / "unused-profile.json")])
            argv += ["--mode", "SIMULATION"] if command == "resume" else []

            def cli_call(argv=argv):
                try:
                    code, printed = self.cli(argv)
                except Exception as exc:
                    return f"CRASHED:{type(exc).__name__}:{exc}"
                if (code, printed.get("state"), printed.get("generation_calls")) == (2, "BLOCKED", 0):
                    return "REFUSED:" + ";".join(printed.get("reasons", []))
                return f"NOT_REFUSED:exit_{code}:{json.dumps(printed)[:120]}"
            calls.append(("CLI " + command, cli_call))
        observations = []
        for entrance, call in calls:
            before, server_before = directory_digest(out), len(sim.requests)
            cli_server_before = len(server_log.read_text().splitlines()) if server_log.exists() else 0
            outcome = call() if entrance.startswith("CLI") else gate_outcome(call)
            cli_server_after = len(server_log.read_text().splitlines()) if server_log.exists() else 0
            row = {"case": case, "entrance": entrance, "outcome": outcome[:240], "before_sha256": before, "after_sha256": directory_digest(out),
                   "server_requests": len(sim.requests) - server_before + cli_server_after - cli_server_before}
            row["ok"] = bool(outcome.startswith("REFUSED:") and re.search(reason, outcome) and row["before_sha256"] == row["after_sha256"]
                             and row["server_requests"] == 0)
            observations.append(row)
            print("R2_OBSERVATION " + json.dumps({"test": self._testMethodName, **row}, sort_keys=True))
        self.assertEqual([r["entrance"] for r in observations], list(self.ENTRANCES))
        self.assertEqual([r for r in observations if not r["ok"]], [])
        return observations

    # --- R2-4: the continuation Judge's counterexample class, fixed as a regression ------------------------------------------
    def test_R2_forged_resume_claiming_backfilled_facts_is_refused_at_every_entrance(self):
        """F1-R1-JOURNAL-ACKNOWLEDGEMENT: the ledger holds eight facts the log has no record of and a RESUME record claims they were
        written back.  Claims of 1..8, the log emptied or only its fact records deleted, after an in-process interrupt and after a
        real SIGKILL: every entrance refuses, nothing is sent, nothing changes."""
        failures = []
        for base in ("interrupt", "sigkill"):
            if base == "interrupt":
                self.interrupted_at("run", 3)
            else:
                shutil.rmtree(self.root / "run")
                self.sigkilled()
            path = self.root / "run" / "attempts.jsonl"
            original, rows = path.read_bytes(), self.rows_of("run")
            facts = sorted(oracle_truth(json.loads((self.root / "run" / "ledger.json").read_text())))
            self.assertEqual(len(facts), 8)  # one reserved round, four starts, three settlements
            claims = [fact_name(fact) for fact in facts]
            payload = [row for row in rows if row["phase"] in PAYLOAD_PHASES]
            variants = [(f"{base}: log emptied, RESUME claims {k}", [forged_resume(claims, k)], "ATTEMPTS_BEHIND_LEDGER") for k in range(1, 9)]
            variants += [(f"{base}: fact records deleted, RESUME claims {k}", payload + [forged_resume(claims, k)],
                          "ATTEMPTS_OUT_OF_ORDER:REQUEST_BEFORE_ITS_START") for k in range(1, 9)]
            variants.append((f"{base}: log emptied, three RESUME shapes each claim all eight", [forged_resume(claims, k) for k in (8, 9, 10)],
                             "ATTEMPTS_BEHIND_LEDGER"))
            for label, content, reason in variants:
                self.write_rows("run", content)
                try:
                    self.refused_everywhere("R2-4 " + label, reason)
                except AssertionError as exc:
                    failures.append((label, str(exc)[:300]))
                finally:
                    path.write_bytes(original)
            # the intact run with the same forged note appended: the note neither blocks nor helps, the run resumes as before
            path.write_bytes(original + (json.dumps(forged_resume(claims, 8)) + "\n").encode())
            if gate_outcome(lambda: xr.inspect_run(self.root / "run")) != "ACCEPTED":
                failures.append((base, "an intact run with a forged RESUME appended was refused"))
            resumed, sim = self.make(log=False)
            outcome = gate_outcome(resumed.resume)
            generations = len([r for r in sim.requests if r["path"] == "responses"])
            if (outcome, generations) != ("ACCEPTED", 36):  # the started fourth call is never sent again
                failures.append((base, outcome, generations))
        self.assertEqual(failures, [])

    # --- I2: the bound is absolute and counts concrete records only --------------------------------------------------------
    def test_R2_the_bound_counts_concrete_records_only_three_missing_pass_four_never(self):
        self.interrupted_at("run", 5)
        path = self.root / "run" / "attempts.jsonl"
        original, rows = path.read_bytes(), self.rows_of("run")
        ledger = json.loads((self.root / "run" / "ledger.json").read_text())
        claims = [fact_name(fact) for fact in sorted(oracle_truth(ledger))]
        settled = [i for i, row in enumerate(rows) if row["phase"] == "SETTLED"]  # nothing on record depends on a settlement here
        self.assertEqual(len(settled), 5)
        table, failures = [], []
        for count in range(6):
            damaged = [row for i, row in enumerate(rows) if i not in settled[:count]]
            for k in (None, 1, count, 8):
                self.write_rows("run", damaged + ([] if k is None else [forged_resume(claims, k)]))
                outcome = gate_outcome(lambda: xr.inspect_run(self.root / "run"))
                expected = "ACCEPTED" if count <= 3 else "REFUSED:ATTEMPTS_BEHIND_LEDGER"
                table.append({"missing": count, "resume_claims": k, "outcome": outcome})
                if not outcome.startswith(expected):
                    failures.append(table[-1])
        print("R2_BOUND " + json.dumps(table, sort_keys=True))
        four_missing = [row for i, row in enumerate(rows) if i not in settled[:4]]
        self.write_rows("run", four_missing + [forged_resume(claims, 8)])
        try:
            self.refused_everywhere("I2 four missing, RESUME claims all", "ATTEMPTS_BEHIND_LEDGER")
        except AssertionError as exc:
            failures.append(("entrances", str(exc)[:300]))
        # the trust boundary, asserted rather than only written down: a forged concrete record that agrees with the ledger cannot
        # be told from a real one, so four missing plus one such record is three missing
        lost = fact_of(rows[settled[0]])
        self.write_rows("run", four_missing + [forged_record(ledger, ("SETTLED", lost[1], lost[2]))])
        if gate_outcome(lambda: xr.inspect_run(self.root / "run")) != "ACCEPTED":
            failures.append("a ledger-consistent forged record was told apart")
        # three missing is a legal state: resume writes the three back, one record each, and finishes without resending anything
        self.write_rows("run", [row for i, row in enumerate(rows) if i not in settled[:3]])
        resumed, sim = self.make(log=False)
        outcome = gate_outcome(resumed.resume)
        written_back = sorted(fact_of(r) for r in self.rows_of("run") if r.get("reconciled") is True)
        if (outcome, len([r for r in sim.requests if r["path"] == "responses"]), written_back) != (
                "ACCEPTED", 34, sorted(fact_of(rows[i]) for i in settled[:3])):
            failures.append(("resume of three missing", outcome, written_back))
        path.write_bytes(original)
        self.assertEqual(failures, [])

    # --- duplicates and causal order ---------------------------------------------------------------------------------------
    def test_R2_duplicate_and_out_of_order_facts_are_refused_by_name_at_every_entrance(self):
        order = sorted(self.public)
        random.Random(6302).shuffle(order)
        runner, sim = self.make("order", log=False, faults=[{"path": "responses/input_tokens", "item_id": order[0], "occurrence": 1,
                                                             "fault": "conn_error"}])
        runner.transport = self.dying(sim, order[2], 2)  # its round-1 call: two reserved rounds and a retried first slot on record
        self.crashes(runner.start)
        path = self.root / "order" / "attempts.jsonl"
        original, rows = path.read_bytes(), self.rows_of("order")
        ledger = json.loads((self.root / "order" / "ledger.json").read_text())

        def at(phase, slot=None, attempt=1, **match):
            return next(i for i, r in enumerate(rows) if r["phase"] == phase and (slot is None or (r["slot"], r["attempt"]) == (slot, attempt))
                        and all(r.get(k) == v for k, v in match.items()))
        rr0, rr1 = at("ROUND_RESERVED", round=0), at("ROUND_RESERVED", round=1)
        first, second = f"{order[0]}:0", f"{order[1]}:0"
        cases = [
            ("a reserved round recorded twice", rows[:rr0 + 1] + [rows[rr0]] + rows[rr0 + 1:], "ATTEMPTS_DUPLICATE_ROUND_RESERVED"),
            ("round 1 recorded before round 0", moved_before(rows, rr1, rr0), "ATTEMPTS_OUT_OF_ORDER:ROUND_BEFORE_THE_PREVIOUS_ROUND"),
            ("a round-1 start before its round", moved_before(rows, at("START", f"{order[0]}:1"), rr1), "ATTEMPTS_OUT_OF_ORDER:START_BEFORE_ITS_ROUND"),
            ("round 1 deleted while its starts remain", rows[:rr1] + rows[rr1 + 1:], "ATTEMPTS_OUT_OF_ORDER:START_BEFORE_ITS_ROUND"),
            ("the retry started before the first attempt settled", moved_before(rows, at("START", first, 2), at("SETTLED", first, 1)),
             "ATTEMPTS_OUT_OF_ORDER:RETRY_BEFORE_THE_FIRST_SETTLEMENT"),
            ("a settlement before its start", moved_before(rows, at("SETTLED", second), at("START", second)),
             "ATTEMPTS_OUT_OF_ORDER:SETTLED_BEFORE_ITS_START"),
            ("a request before its start", moved_before(rows, at("REQUEST", second), at("START", second)),
             "ATTEMPTS_OUT_OF_ORDER:REQUEST_BEFORE_ITS_START"),
            ("a start deleted while its requests and settlement remain", [r for i, r in enumerate(rows) if i != at("START", second)],
             "ATTEMPTS_OUT_OF_ORDER:REQUEST_BEFORE_ITS_START"),
        ]
        failures = []
        for label, damaged, reason in cases:
            present = {fact_of(r) for r in damaged if r["phase"] in ("ROUND_RESERVED", "START", "SETTLED")}
            claims = [fact_name(f) for f in sorted(oracle_truth(ledger))]
            for forged in ([], [forged_resume(claims, 8)]):  # a RESUME claiming everything changes nothing
                self.write_rows("order", damaged + forged)
                try:
                    self.refused_everywhere(label + (" + forged RESUME" if forged else ""), "^REFUSED:" + re.escape(reason) + "$", "order")
                except AssertionError as exc:
                    failures.append((label, bool(forged), str(exc)[:300]))
                finally:
                    path.write_bytes(original)
            self.assertLessEqual(len({fact_of(r) for r in rows if r["phase"] in ("ROUND_RESERVED", "START", "SETTLED")} - present), 1)
        self.assertEqual(failures, [])

    # --- I4: RESUME is an audit note ---------------------------------------------------------------------------------------
    def test_R2_any_resume_record_anywhere_changes_no_decision_and_no_reason(self):
        self.interrupted_at("run", 5)
        out = self.root / "run"
        rows, ledger = self.rows_of("run"), json.loads((out / "ledger.json").read_text())
        truth = sorted(oracle_truth(ledger))
        claims = [fact_name(f) for f in truth] + ["SETTLED:no-such-item:0:1", "ROUND_RESERVED:7"]
        settled = [i for i, row in enumerate(rows) if row["phase"] == "SETTLED"]
        start = next(i for i, row in enumerate(rows) if row["phase"] == "START")
        corpus = [("intact", rows)] + [(f"{n} settlements deleted", [r for i, r in enumerate(rows) if i not in settled[:n]]) for n in range(1, 6)]
        corpus += [("a start deleted", rows[:start] + rows[start + 1:]), ("a start recorded twice", rows[:start] + [rows[start]] + rows[start:]),
                   ("log ahead of the ledger", rows + [absent_records(ledger)[1]]), ("log emptied", [])]
        shapes = [forged_resume(claims, k) for k in range(1, len(claims) + 1)] + [
            {"phase": "RESUME", "t": 0}, {"phase": "RESUME", "t": 1, "reconciliation": None, "recovered_uncertain": None},
            {"phase": "RESUME", "t": 2, "reconciliation": "everything was written back", "recovered_uncertain": "all"},
            {"phase": "RESUME", "t": 3.5, "reconciliation": claims, "recovered_uncertain": sorted(ledger["slots"]) + ["nope:0"]},
            {"phase": "RESUME", "t": 4, "reconciliation": {"pre_first_save": True, "ledger_ahead": claims * 3}, "trusted": True}]
        rng, changed, outcomes = random.Random(ORACLE_SEED), [], {}
        with ProductGate(out) as gate:
            for label, damaged in corpus:
                plain = gate(damaged)
                outcomes[label] = plain[:80]
                for index, shape in enumerate(shapes):
                    for position in sorted({0, len(damaged), rng.randrange(len(damaged) + 1)}):
                        if gate(damaged[:position] + [shape] + damaged[position:]) != plain:
                            changed.append((label, index, position))
                if gate(damaged + shapes) != plain:
                    changed.append((label, "every shape at once"))
        print("R2_I4 " + json.dumps({"corpus": outcomes, "shapes": len(shapes), "changed": len(changed)}, sort_keys=True))
        self.assertEqual(outcomes["intact"], "ACCEPTED")
        self.assertTrue(outcomes["4 settlements deleted"].startswith("REFUSED:ATTEMPTS_BEHIND_LEDGER"))
        self.assertEqual(changed, [])

    # --- I3: the write-back and its windows ---------------------------------------------------------------------------------
    def test_R2_resume_writes_each_lost_fact_back_as_its_own_reconciled_record_before_RESUME_and_any_request(self):
        for window in ("ROUND_RESERVED", "START", "SETTLED"):
            name = "backfill-" + window
            runner, sim = self.make(name, log=False)
            with self.crash_on(window):
                self.crashes(runner.start)
            inspection = self.completes(lambda: xr.inspect_run(runner.out_dir))
            missing, slots, kept = inspection["missing"], inspection["ledger"]["slots"], len(inspection["records"])
            self.assertEqual(len(missing), 1, window)  # a legitimate crash leaves exactly one ledger fact without its record
            resumed, sim2 = self.make(name, log=False)
            self.assertEqual(self.completes(resumed.resume)["state"], "COMPLETE", window)
            new = self.rows_of(name)[kept:]
            phases = [row["phase"] for row in new]
            backfill = new[:len(missing)]
            self.assertEqual([fact_of(row) for row in backfill], missing, window)
            for row, fact in zip(backfill, missing):
                self.assertEqual((row.get("reconciled"), row.get("derived_from"), row.get("original_payload")), (True, "ledger.json", "LOST"))
                if fact[0] == "SETTLED":
                    receipt = slots[fact[1]]["receipts"][fact[2] - 1]
                    self.assertEqual((row["status"], row["cost_upper_usd"], row["charged_cap_because_unknown"]),
                                     (receipt["status"], receipt["cost"] if receipt["cost_known"] else None, not receipt["cost_known"]))
            resume_at = phases.index("RESUME")
            self.assertLess(resume_at, phases.index("REQUEST"), window)  # RESUME, and every write-back, precede every new request
            recovered = [(r["phase"], r.get("status"), r.get("kind"), r.get("cost_upper_usd")) for r in new[len(missing):resume_at]]
            self.assertEqual(recovered, [("SETTLED", "INCOMPLETE", "RECOVER_UNCERTAIN_NO_RELAUNCH", None)] if window == "START" else [], window)
            final = xr.inspect_run(runner.out_dir)
            self.assertEqual(final["missing"], [], window)  # every ledger fact now has its own record
            logged = sum((CAP if r["cost_upper_usd"] is None else Decimal(r["cost_upper_usd"])) for r in final["records"] if r["phase"] == "SETTLED")
            self.assertEqual(Decimal(final["ledger"]["spent_upper"]), logged, window)  # the books and the log agree exactly
            generations = [r["item_id"] for r in sim.requests + sim2.requests if r["path"] == "responses"]
            self.assertEqual(len(generations), 39 if window == "START" else 40, window)
            self.assertLessEqual(max(generations.count(item) for item in set(generations)), 5)
            print("R2_WINDOW " + json.dumps({"window": window, "missing_before": inspection["reconciliation"]["ledger_ahead"], "resume_at": resume_at,
                                             "first_request_at": phases.index("REQUEST"), "recovered": recovered}, sort_keys=True))

    def test_R2_a_kill_at_any_write_of_resume_bookkeeping_resumes_and_never_resends(self):
        """Three lost settlements (the bound) and an uncertain slot: resume writes three records back, then the recovery's record,
        then RESUME, then the next slot's START.  Kill it at each of those six writes, then twice more at the first write of the
        next resume; every state in between must pass the gate and the last resume must finish without resending anything."""
        runner, _ = self.interrupted_at("base", 3)
        victim, rows = runner.order[3], self.rows_of("base")
        settled = [i for i, row in enumerate(rows) if row["phase"] == "SETTLED"]
        self.write_rows("base", [row for i, row in enumerate(rows) if i not in settled])
        self.assertEqual(len(xr.inspect_run(self.root / "base")["missing"]), 3)
        expected_missing = {1: 3, 2: 2, 3: 1, 4: 1, 5: 0, 6: 1}
        trail = []
        for kill_at in range(1, 7):
            name = f"kill-{kill_at}"
            shutil.copytree(self.root / "base", self.root / name)
            sims = []
            for round_, at in enumerate((kill_at, 1, 1)):
                again, sim = self.make(name, log=False)
                sims.append(sim)
                with self.kill_at_append(at):
                    self.crashes(again.resume)
                state = self.completes(lambda: xr.inspect_run(self.root / name))
                trail.append({"kill_at": kill_at, "kill": round_, "missing": state["reconciliation"]["ledger_ahead"],
                              "running": sorted(k for k, s in state["ledger"]["slots"].items() if s["state"] == "RUNNING")})
                if round_ == 0:
                    self.assertEqual(len(state["missing"]), expected_missing[kill_at], kill_at)
            last, sim = self.make(name, log=False)
            sims.append(sim)
            self.assertEqual(self.completes(last.resume)["state"], "COMPLETE", kill_at)
            sent = [r["item_id"] for s in sims for r in s.requests if r["path"] == "responses"]
            self.assertEqual(sent.count(victim), 4, kill_at)  # its round-0 call reached the server before the first kill: never again
            self.assertLessEqual(max(sent.count(item) for item in set(sent)), 5, kill_at)
            final = xr.inspect_run(self.root / name)
            logged = sum((CAP if r["cost_upper_usd"] is None else Decimal(r["cost_upper_usd"])) for r in final["records"] if r["phase"] == "SETTLED")
            self.assertEqual((final["missing"], Decimal(final["ledger"]["spent_upper"])), ([], logged), kill_at)
        print("R2_KILL_TRAIL " + json.dumps(trail, sort_keys=True))

    def test_R2_a_kill_on_either_side_of_the_first_ledger_save_resumes_and_sends_each_call_once(self):
        for side, target in (("before", "_save"), ("after", "reserve_round")):
            name = f"first-save-{side}"
            runner, sim = self.make(name, log=False)
            with mock.patch.object(bl.Ledger, target, side_effect=KeyboardInterrupt(f"killed {side} the first ledger save")):
                self.crashes(runner.start)
            self.assertEqual((runner.out_dir / "ledger.json").exists(), side == "after", side)
            inspection = self.completes(lambda: xr.inspect_run(runner.out_dir))
            self.assertEqual((inspection["missing"], inspection["reconciliation"]["pre_first_save"]), ([], side == "before"), side)
            resumed, sim2 = self.make(name, log=False)
            self.assertEqual(self.completes(resumed.resume)["state"], "COMPLETE", side)
            generations = [r["item_id"] for r in sim.requests + sim2.requests if r["path"] == "responses"]
            self.assertEqual((len(generations), max(generations.count(item) for item in set(generations))), (40, 5), side)

    # --- R2-6(c): the SIMULATION boundary, every kind of transport ------------------------------------------------------------
    def test_R2_simulation_accepts_only_a_transport_marked_simulated_before_any_write(self):
        class Unmarked:
            def __call__(self, path, body):
                raise AssertionError("a transport that is not marked simulated must never be called")

        class TruthyMarker(Unmarked):
            is_simulated = "yes"  # truthy, but not True

        class Marked:
            is_simulated = True

            def __init__(self, inner):
                self.inner = inner

            def __call__(self, path, body):
                return self.inner(path, body)

        refused = [("plain function", lambda path, body: {}), ("real adapter _post", api.ResponsesAdapter("x")._post),
                   ("unmarked object", Unmarked()), ("object with a truthy non-True marker", TruthyMarker())]
        self.interrupted_at("open", 3)
        open_before = directory_digest(self.root / "open")
        for index, (label, transport) in enumerate(refused):
            runner, sim = self.make(f"refused-{index}", log=False)
            runner.transport = transport
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-NOT-A-REAL-KEY"}):  # the network is cut in setUp
                self.assertBlocks("SIMULATION_TRANSPORT_NOT_SIMULATED", runner.start)
                again, again_sim = self.make("open", log=False)
                again.transport = transport
                self.assertBlocks("SIMULATION_TRANSPORT_NOT_SIMULATED", again.resume)
            self.assertFalse(runner.out_dir.exists(), label)
            self.assertEqual((sim.requests, again_sim.requests, urllib_calls()), ([], [], 0), label)
            self.assertEqual(directory_digest(self.root / "open"), open_before, label)
            print("R2_OBSERVATION " + json.dumps({"case": "SIMULATION boundary", "caller": "Runner", "transport": label,
                                                  "outcome": "BLOCKED", "server_requests": 0, "network_calls": 0}))
        for index, (label, wrap) in enumerate((("SimulatedResponses", lambda inner: inner), ("marked simulated object", Marked))):
            runner, sim = self.make(f"marked-{index}", log=False)
            runner.transport = wrap(sim)
            self.assertEqual(self.completes(runner.start)["state"], "COMPLETE", label)
            self.assertEqual(len(sim.requests), 80, label)
            print("R2_OBSERVATION " + json.dumps({"case": "SIMULATION boundary", "caller": "Runner", "transport": label,
                                                  "outcome": "COMPLETE", "server_requests": 80}))

    # --- R2-6(b): a bytecode check that can fail --------------------------------------------------------------------------------
    def test_R2_bytecode_writes_are_observable_through_an_explicit_cache_prefix_and_none_happen(self):
        """Apple's Python sends bytecode to a per-user cache, so an empty package directory alone proves nothing.  With
        PYTHONPYCACHEPREFIX every write that is not suppressed lands in a directory of ours: first a copy with the suppression
        removed must write there, then the code as shipped must write nothing there, for both entry points."""
        copies = {"suppression removed": self.root / "bytecode-control", "as shipped": self.root / "bytecode-shipped"}
        for root in copies.values():
            for name in ("fable-ensemble3-runner", "fable-ensemble3-r1", "fable-ensemble3-lab", "fable-ensemble3-bank"):
                shutil.copytree(HERE.parent / name, root / "analysis" / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for path in (copies["suppression removed"] / "analysis" / "fable-ensemble3-runner").glob("*.py"):
            path.write_text(path.read_text().replace("sys.dont_write_bytecode = True", "sys.dont_write_bytecode = False"))
        found = {}
        for label, root in copies.items():
            for entry in ("exploration_runner.py", "probe_2b.py"):
                prefix = self.root / f"prefix-{label.replace(' ', '-')}-{entry}"
                result = subprocess.run([sys.executable, str(root / "analysis/fable-ensemble3-runner" / entry), "--help"], capture_output=True,
                                        text=True, env={"PATH": "/usr/bin:/bin", "TMPDIR": str(self.root), "PYTHONPYCACHEPREFIX": str(prefix)},
                                        timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                found[f"{label} / {entry}"] = sorted(p.name for p in prefix.rglob("*.pyc") if root.name in p.parts)
        print("R2_BYTECODE " + json.dumps(found, sort_keys=True))
        for entry in ("exploration_runner.py", "probe_2b.py"):
            self.assertTrue(any(name.startswith("_lab.") for name in found[f"suppression removed / {entry}"]), entry)  # the check can see a write
            self.assertEqual(found[f"as shipped / {entry}"], [], entry)
        self.assertEqual([p for root in copies.values() for p in (root / "analysis").rglob("*.pyc") if root.name == "bytecode-shipped"], [])


# --- R3: one terminal-integrity verdict, carried by every exit ---------------------------------------------------------------------
# Written from the R3 Packet (D0-1..D0-6), not from the product: the oracle reuses the independent R2 oracle above, never calls the
# runner's gate and never reads its constants.  The two flag names are the Packet's (D0-3) and the README's name for D0-4's
# "resumable, with a gap"; an exit that carries no verdict at all carries no flag.
TERMINAL_KINDS = ("COMPLETE", "REJECTED", "CAPABILITY", "ATTEMPT_CAP_OVERRUN", "OTHER", "PERSISTENCE")
PERSISTENCE_FLAG, RESUMABLE_FLAG = "INCOMPLETE_JOURNAL_BY_PERSISTENCE_HALT", "RESUMABLE_JOURNAL_GAP"
R3_ENTRANCES = ("inspect_run", "replay_run", "report", "write_ledger", "CLI check --out", "CLI replay", "Runner.resume",
                "Runner API write accounting")
R3_READ_ONLY = ("inspect_run", "replay_run", "CLI check --out", "CLI replay")
R3_WRITERS = ("write_ledger", "Runner API write accounting")


def oracle_missing(ledger: dict, records: list) -> set:
    """Ledger facts with no concrete record of their own: a set difference, nothing else."""
    cap = Decimal(ledger["attempt_cap"])
    present = {found[0] for found in (oracle_fact(row, cap) for row in records) if found and found[0][0] != "PAYLOAD"}
    return set(oracle_truth(ledger)) - present


def oracle_reconciled(records: list) -> int:
    return sum(1 for row in records if row.get("phase") in ("ROUND_RESERVED", "START", "SETTLED") and row.get("reconciled") is True)


def oracle_terminal(state: dict, ledger: dict, records: list, certified=None) -> str:
    """REFUSE, FLAG_PERSISTENCE, FLAG_RESUMABLE or ACCEPT.  The R2 rules decide first.  A journal whose certified records are known
    and differ from them is refused (D0-5, R3-3).  Then a finished run may lack no ledger fact (D0-2), except that a PERSISTENCE
    halt is flagged (D0-3); a running run with a gap is flagged as resumable (D0-4); everything else passes."""
    if oracle_verdict(ledger, records) != "ACCEPT" or (certified is not None and records != certified):
        return "REFUSE"
    if not oracle_missing(ledger, records):
        return "ACCEPT"
    if state["state"] == "RUNNING":
        return "FLAG_RESUMABLE"
    return "FLAG_PERSISTENCE" if state["state"] == "HALTED" and state.get("reason") == "PERSISTENCE" else "REFUSE"


def verdict_class(integrity) -> str:
    """The class of the verdict an exit carries; an absent verdict carries no flag."""
    flags = (integrity or {}).get("flags") or []
    return "FLAG_PERSISTENCE" if PERSISTENCE_FLAG in flags else "FLAG_RESUMABLE" if RESUMABLE_FLAG in flags else "ACCEPT"


def journal_text(rows: list) -> str:
    """Rows exactly as the runner appends them, so an untouched list reproduces the journal byte for byte."""
    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n" for row in rows)


def directory_files(out: Path) -> dict:
    return {str(p.relative_to(out)): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def restore_directory(out: Path, files: dict) -> None:
    for path in [p for p in out.rglob("*") if p.is_file() and str(p.relative_to(out)) not in files]:
        path.unlink()
    for name, content in files.items():
        if not (out / name).is_file() or (out / name).read_bytes() != content:
            (out / name).write_bytes(content)


class R3Gate(ProductGate):
    """The product's own gate on in-memory records (see ProductGate), answering with the class of the verdict it returns."""

    def __call__(self, rows: list):
        self.rows = rows
        try:
            inspection = xr.inspect_run(self.out)
        except xr.RunBlocked as blocked:
            return "REFUSE", None, "REFUSED:" + str(blocked)
        except Exception as exc:  # a crash is a disagreement with any verdict, never a refusal
            return "CRASHED", None, f"CRASHED:{type(exc).__name__}:{exc}"
        return verdict_class(inspection.get("integrity")), inspection.get("integrity"), "ACCEPTED"


class TerminalHelpers(JournalHelpers):
    """Finished-run builders and an every-exit observer shared by the R3 tests (no tests of its own)."""

    def cli(self, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = xr.main(argv)
        return code, json.loads(output.getvalue())

    def rows_of(self, name) -> list:
        return xr.AppendLog(self.root / name / "attempts.jsonl").records()

    def interrupted_at(self, name, position):
        """A run killed in-process mid-call: the generation of order[position] in round 0 reached the server, nothing settled it."""
        runner, sim = self.make(name, log=False)
        runner.transport = self.dying(sim, runner.order[position], 1)
        self.crashes(runner.start)
        return runner, sim

    def small_bank(self) -> Path:
        bank = self.root / "r3-small-bank.json"
        if not bank.exists():
            manifest = json.loads(self.manifest.read_text())
            manifest["items"] = manifest["items"][:2]
            bank.write_text(json.dumps(manifest))
        return bank

    def r3_profile(self) -> Path:
        """A profile that passes its own SIMULATION check, so `check --out` can show what the run adds to a PASS."""
        import test_execution_profile as tp
        directory = self.root / "r3-profile"
        if not (directory / "profile.json").exists():
            directory.mkdir(exist_ok=True)
            tp.World(directory).save()
        return directory / "profile.json"

    def terminal_base(self, name, kind, bank=None, budget="50"):
        """A finished run that ends in `kind`: COMPLETE, or HALTED for that reason at the second item's round-1 call.  Its first
        slot is retried once after an infra error before generation, so second attempts and what they depend on are on record."""
        runner, _ = self.journal_runner(name, bank or self.manifest, budget)
        order = runner.order
        faults = [{"path": "responses/input_tokens", "item_id": order[0], "occurrence": 1, "fault": "conn_error"}]
        fault = {"REJECTED": "http_400", "CAPABILITY": "wrong_model", "ATTEMPT_CAP_OVERRUN": "huge_usage", "OTHER": "no_usage"}.get(kind)
        if fault:
            faults.append({"path": "responses", "item_id": order[1], "occurrence": 2, "fault": fault})
        runner.transport = SimulatedResponses(runner.public, answer_for=self.answer, faults=faults)
        append, fired = xr.AppendLog.append, []

        def disk_full_once(log, record):  # PERSISTENCE: the paid response of that call cannot be written, once
            if kind == "PERSISTENCE" and record["phase"] == "RESPONSE" and record.get("path") == "responses" \
                    and record.get("slot") == f"{order[1]}:1" and not fired:
                fired.append(True)
                raise OSError(28, "No space left on device (injected)")
            return append(log, record)

        with mock.patch.object(xr.AppendLog, "append", disk_full_once):
            result = self.completes(runner.start)
        self.assertEqual((result["state"], (result["halt"] or {}).get("reason")),
                         ("COMPLETE", None) if kind == "COMPLETE" else ("HALTED", kind))
        return runner

    def twin(self, runner):
        """Another Runner bound to the same run (bank, budget, seed), with a simulated server of its own."""
        sim = SimulatedResponses(runner.public, answer_for=self.answer)
        return xr.Runner(runner.out_dir, mode="SIMULATION", bank_manifest=runner.bank_manifest, instructions="synthetic instructions",
                         budget=str(runner.budget), attempt_cap=CAP, target_rounds=runner.target_rounds, seed=runner.seed,
                         transport=sim), sim

    @staticmethod
    def final_settlements(rows, ledger) -> list:
        """Indexes of settlements nothing on record depends on: the last attempt of each slot."""
        return [i for i, row in enumerate(rows) if row["phase"] == "SETTLED" and row["attempt"] == ledger["slots"][row["slot"]]["attempts"]]

    def refusal(self, call):
        """(the refusal's name, the verdict it carries), or ("RETURNED", None)."""
        try:
            call()
        except xr.RunBlocked as blocked:
            return str(blocked).split(":")[0], getattr(blocked, "integrity", None)
        except Exception as exc:
            return f"CRASHED:{type(exc).__name__}", None
        return "RETURNED", None

    def every_exit(self, runner, *, profile, check_result=None) -> list:
        """All eight R3 exits on the run directory, in R3_ENTRANCES order, each judged only by what it outputs: the class of the
        verdict it carries (a refusal made before the gate accepted carries none), the requests that reached any server, and the
        directory digest before and after.  A file an exit adds is recorded and removed, so every exit sees the same bytes."""
        out = runner.out_dir
        files, observations = directory_files(out), []
        twin, sim = self.twin(runner)
        server = out / "simulated_server_log.jsonl"

        def sent():
            return len(sim.requests) + len(getattr(runner.transport, "requests", [])) + (len(server.read_text().splitlines()) if server.exists() else 0)

        def guarded(call):
            try:
                return call()
            except xr.RunBlocked as blocked:
                carried = getattr(blocked, "integrity", None)
                return ("REFUSE" if carried is None else verdict_class(carried)), carried, "REFUSED:" + str(blocked)
            except Exception as exc:
                return "CRASHED", None, f"CRASHED:{type(exc).__name__}:{exc}"

        def inspected():
            verdict = xr.inspect_run(out).get("integrity")
            return verdict_class(verdict), verdict, "ACCEPTED"

        def replayed():
            verdict = xr.replay_run(out).get("integrity")
            return verdict_class(verdict), verdict, "RETURNED"

        def closed():  # report: a finished run must never be closed (and so re-reported and re-certified) again
            return "CLOSED_A_FINISHED_RUN_AGAIN", twin._finalize(None).get("integrity"), "RETURNED"

        def resumed():
            return "RESUMED_A_FINISHED_RUN", twin.resume().get("integrity"), "RETURNED"

        def certified_by(writer):
            def call():
                writer._write_accounting()
                verdict = json.loads((out / "write_ledger.json").read_text()).get("integrity")
                return verdict_class(verdict), verdict, "CERTIFIED"
            return call

        def command(argv):
            def call():
                code, printed = self.cli(argv)
                if code == 2 and printed.get("state") == "BLOCKED":
                    carried = printed.get("integrity")
                    return ("REFUSE" if carried is None else verdict_class(carried)), carried, "REFUSED:" + ",".join(printed["reasons"])
                verdict = printed.get("run_integrity") if argv[0] == "check" else printed.get("integrity")
                flagged = (verdict or {}).get("status") == "FLAGGED"
                consistent = code == (4 if flagged else 0) and (argv[0] != "check" or printed.get("status") == ("FLAGGED" if flagged else "PASS"))
                return (verdict_class(verdict) if consistent else f"EXIT_{code}_STATUS_{printed.get('status')}"), verdict, f"EXIT_{code}"
            return call

        calls = {"inspect_run": inspected, "replay_run": replayed, "report": closed, "write_ledger": certified_by(twin),
                 "CLI check --out": command(["check", "--profile", str(profile), "--out", str(out)]),
                 "CLI replay": command(["replay", "--out", str(out)]), "Runner.resume": resumed,
                 "Runner API write accounting": certified_by(runner)}
        for entrance in R3_ENTRANCES:
            before, requests = directory_digest(out), sent()
            patch = (mock.patch.object(ep, "check_profile", lambda *a, **k: copy.deepcopy(check_result))
                     if check_result is not None and entrance == "CLI check --out" else contextlib.nullcontext())
            with patch:
                cls, carried, text = guarded(calls[entrance])
            after = directory_digest(out)
            row = {"entrance": entrance, "class": cls, "carried": carried, "text": text[:240], "requests": sent() - requests,
                   "before_sha256": before, "after_sha256": after, "added": [], "altered": []}
            if after != before:
                now = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
                row["added"] = sorted(now - set(files))
                row["altered"] = sorted(name for name in files if name not in now or (out / name).read_bytes() != files[name])
                restore_directory(out, files)
            observations.append(row)
        return observations

    @staticmethod
    def exit_problems(observations, expected, *, certified) -> list:
        """What is wrong with each exit, given the oracle's class: a refusal is the gate's own, everywhere, with no write and no
        request; otherwise every exit carries the gate's verdict verbatim, read-only exits write nothing, a finished run is
        never closed or resumed again, and a write ledger is written once, alone, and only where none exists."""
        reference, problems = observations[0], []
        for row in observations:
            wrong = []
            if row["class"] != expected:
                wrong.append("CLASS")
            if row["requests"]:
                wrong.append("REQUESTS")
            if expected == "REFUSE":
                if row["before_sha256"] != row["after_sha256"]:
                    wrong.append("WROTE")
                if row["text"] != reference["text"]:
                    wrong.append("NOT_THE_GATE_REFUSAL")
            else:
                if row["carried"] != reference["carried"] or row["carried"] is None:
                    wrong.append("VERDICT_NOT_CARRIED")
                if row["entrance"] in R3_WRITERS and not certified:
                    if (row["text"], row["added"], row["altered"]) != ("CERTIFIED", ["write_ledger.json"], []):
                        wrong.append("NOT_CERTIFIED_ONCE")
                elif row["before_sha256"] != row["after_sha256"]:
                    wrong.append("WROTE")
                if row["entrance"] in ("report", "Runner.resume") and not row["text"].startswith("REFUSED:RUN_IS_"):
                    wrong.append("FINISHED_RUN_REOPENED")
                if row["entrance"] in R3_WRITERS and certified and not row["text"].startswith("REFUSED:WRITE_LEDGER_ALREADY_CERTIFIED"):
                    wrong.append("CERTIFIED_AGAIN")
            if wrong:
                problems.append((row["entrance"], wrong, row["class"], row["text"][:120]))
        return problems


class TerminalOracleTests(TerminalHelpers, RunnerCase):
    """R3-4: the R2 oracle extended to every finished state, and compared with all eight exits, not only the gate."""

    STRIDE = 37  # the all-exit pass: every delete-1 case, every delete-2 case that removes two fact records, and every 37th other

    @staticmethod
    def new_tally(**extra) -> dict:
        return JournalOracleTests.new_tally(exit_cases=0, exit_disagreements=0, exit_causes={}, exit_samples=[], bases={}, **extra)

    def real_judge(self, out):
        """The gate through the real file reads: the damaged rows are written as the runner writes them, then restored."""
        path = out / "attempts.jsonl"

        def judge(rows):
            original = path.read_bytes()
            path.write_text(journal_text(rows))
            try:
                verdict = xr.inspect_run(out).get("integrity")
            except xr.RunBlocked as blocked:
                return "REFUSE", None, "REFUSED:" + str(blocked)
            except Exception as exc:
                return "CRASHED", None, f"CRASHED:{type(exc).__name__}:{exc}"
            finally:
                path.write_bytes(original)
            return verdict_class(verdict), verdict, "ACCEPTED"
        return judge

    def gate_compare(self, judge, state, ledger, kind, family, rows, index, tally, *, forged_too=True, certified=None) -> None:
        """Oracle vs the gate on `rows` and, when `forged_too`, plus a forged reconciled record (of an absent fact, or of a fact the
        ledger holds) and plus a forged RESUME note.  An accepted verdict must also name exactly the oracle's missing facts and
        count its reconciled records; a RESUME note may change nothing."""
        truth = oracle_truth(ledger)
        missing = sorted(oracle_missing(ledger, rows))
        claims = [fact_name(f) for f in missing] + [fact_name(f) for f in sorted(set(truth) - set(missing))]
        variants = [("none", [])]
        if forged_too:
            refill = missing[0] if missing else sorted(truth)[index % len(truth)]
            variants += [("reconciled-absent", [absent_records(ledger)[index % 3]]), ("reconciled-present", [forged_record(ledger, refill)])]
        for forged_kind, forged in variants:
            plain = None
            for k in ((None, 1 + index % 8) if forged_too else (None,)):
                variant = rows + forged + ([] if k is None else [forged_resume(claims, k)])
                expected = oracle_terminal(state, ledger, variant, certified)
                cls, verdict, text = judge(variant)
                tally["comparisons"] += 1
                tally["oracle"][expected] = tally["oracle"].get(expected, 0) + 1
                tally["product"][cls] = tally["product"].get(cls, 0) + 1
                cause = None
                if cls != expected:
                    cause = f"{expected}->{cls}"
                elif cls != "REFUSE":
                    named = sorted(fact_name(f) for f in oracle_missing(ledger, variant))
                    if verdict is None:
                        cause = "NO_VERDICT_RETURNED"
                    elif (verdict.get("state"), sorted(verdict.get("missing") or []), verdict.get("missing_count"), verdict.get("reconciled")) \
                            != (state["state"], named, len(named), oracle_reconciled(variant)):
                        cause = "VERDICT_CONTENT"
                if cause:
                    tally["disagreements"] += 1
                    tally["disagreement_causes"][cause] = tally["disagreement_causes"].get(cause, 0) + 1
                    if len(tally["samples"]) < 12:
                        tally["samples"].append({"base": kind, "family": family, "case": index, "forged": forged_kind, "resume_claims": k,
                                                 "oracle": expected, "product": text[:160], "verdict": verdict})
                if k is None:
                    plain = (cls, verdict, text)
                elif (cls, verdict, text) != plain:
                    tally["resume_changed_outcome"] += 1
                    if len(tally["i4_samples"]) < 6:
                        tally["i4_samples"].append({"base": kind, "case": index, "with_resume": text[:120], "without": plain[2][:120]})

    def exit_compare(self, runner, state, ledger, kind, family, rows, tally, *, profile, check_result, certified=None) -> None:
        out = runner.out_dir
        expected = oracle_terminal(state, ledger, rows, certified)
        path = out / "attempts.jsonl"
        original = path.read_bytes()
        path.write_text(journal_text(rows))
        try:
            observations = self.every_exit(runner, profile=profile, check_result=check_result)
        finally:
            path.write_bytes(original)
        tally["exit_cases"] += 1
        tally["exit_oracle"] = tally.get("exit_oracle", {})
        tally["exit_oracle"][expected] = tally["exit_oracle"].get(expected, 0) + 1
        for entrance, wrong, cls, text in self.exit_problems(observations, expected, certified=certified is not None):
            tally["exit_disagreements"] += 1
            for cause in wrong:
                key = f"{entrance}:{cause}"
                tally["exit_causes"][key] = tally["exit_causes"].get(key, 0) + 1
            if len(tally["exit_samples"]) < 16:
                tally["exit_samples"].append({"base": kind, "family": family, "certified": certified is not None, "entrance": entrance,
                                              "oracle": expected, "product": cls, "wrong": wrong, "text": text})

    def small_finished_bases(self):
        """The six small finished runs (two items, three rounds), uncertified, then R2's small running base: (kind, runner or
        None, out, rows, ledger, state).  Uncertified here: certified journals are the random tests' first half."""
        bank, budget = self.small_bank(), str(2 * 2 * CAP * 3)
        bases = [(kind, self.terminal_base("t-" + kind.lower(), kind, bank, budget)) for kind in TERMINAL_KINDS] + [("RUNNING", None)]
        for kind, runner in bases:
            out = self.small_base() if runner is None else runner.out_dir
            if (out / "write_ledger.json").exists():
                (out / "write_ledger.json").unlink()
            rows, ledger = xr.AppendLog(out / "attempts.jsonl").records(), json.loads((out / "ledger.json").read_text())
            self.assertEqual(journal_text(rows), (out / "attempts.jsonl").read_text())  # the harness writes what the runner writes
            yield kind, runner, out, rows, ledger, json.loads((out / "run_state.json").read_text())

    def large_finished_bases(self):
        """The six large finished runs (eight items, five rounds), as they finished, certified, with 520 seeded random damages each."""
        for kind in TERMINAL_KINDS:
            runner = self.terminal_base("big-" + kind.lower(), kind)
            out = runner.out_dir
            rows, ledger = xr.AppendLog(out / "attempts.jsonl").records(), json.loads((out / "ledger.json").read_text())
            self.assertEqual(journal_text(rows), (out / "attempts.jsonl").read_text())
            rng = random.Random(f"{ORACLE_SEED}:{kind}")
            yield kind, runner, out, rows, ledger, json.loads((out / "run_state.json").read_text()), [random_damage(rng, ledger, rows) for _ in range(520)]

    def test_R3_oracle_agrees_with_the_gate_on_every_single_and_double_damage_of_each_finished_state(self):
        tally = self.new_tally(test="exhaustive-small-gate")
        for kind, runner, out, rows, ledger, state in self.small_finished_bases():
            with R3Gate(out) as gate:
                base = gate(rows)
                tally["bases"][kind] = {"records": len(rows), "ledger_facts": len(oracle_truth(ledger)), "oracle": oracle_terminal(state, ledger, rows),
                                        "product": base[0], "verdict_status": (base[1] or {}).get("status"), "cases": 0}
                for index, (family, damaged) in enumerate(exhaustive_damage(rows)):
                    tally["families"][family] = tally["families"].get(family, 0) + 1
                    tally["bases"][kind]["cases"] += 1
                    self.gate_compare(gate, state, ledger, kind, family, damaged, index, tally)
        tally["cases"] = sum(tally["families"].values())
        print("R3_ORACLE " + json.dumps(tally, sort_keys=True))
        self.assertEqual({kind: (b["oracle"], b["product"]) for kind, b in tally["bases"].items()},
                         {kind: ("ACCEPT", "ACCEPT") for kind in TERMINAL_KINDS + ("RUNNING",)})
        self.assertGreater(min(tally["oracle"].get(v, 0) for v in ("ACCEPT", "REFUSE", "FLAG_PERSISTENCE", "FLAG_RESUMABLE")), 0)
        self.assertEqual((tally["disagreements"], tally["samples"]), (0, []))
        self.assertEqual((tally["resume_changed_outcome"], tally["i4_samples"]), (0, []))

    def test_R3_oracle_agrees_with_every_exit_on_single_and_double_damage_of_each_finished_state(self):
        profile = self.r3_profile()
        check_result = ep.check_profile(profile)  # the profile half of `check` does not depend on the run: computed once, for speed
        tally = self.new_tally(test="exhaustive-small-exits", stride=self.STRIDE)
        for kind, runner, out, rows, ledger, state in self.small_finished_bases():
            if runner is None:
                continue  # a running run's write exits would carry it on: its exits are R2's, its verdicts the gate test's
            facts = sum(row["phase"] in ("ROUND_RESERVED", "START", "SETTLED") for row in rows)
            tally["bases"][kind] = {"records": len(rows), "exit_cases": 0}
            for index, (family, damaged) in enumerate(exhaustive_damage(rows)):
                two_facts = family == "delete-2" and sum(r["phase"] in ("ROUND_RESERVED", "START", "SETTLED") for r in damaged) == facts - 2
                if family == "delete-1" or two_facts or index % self.STRIDE == 0:
                    tally["bases"][kind]["exit_cases"] += 1
                    tally["families"][family] = tally["families"].get(family, 0) + 1
                    self.exit_compare(runner, state, ledger, kind, family, damaged, tally, profile=profile, check_result=check_result)
        print("R3_ORACLE " + json.dumps(tally, sort_keys=True))
        self.assertGreaterEqual(tally["exit_cases"], 600)
        self.assertGreater(min(tally["exit_oracle"].get(v, 0) for v in ("ACCEPT", "REFUSE", "FLAG_PERSISTENCE")), 0)
        self.assertEqual((tally["exit_disagreements"], tally["exit_samples"]), (0, []))

    def test_R3_oracle_agrees_with_the_gate_on_seeded_random_damage_of_each_large_finished_state_certified_or_not(self):
        tally = self.new_tally(test="random-large-gate", seed=f"{ORACLE_SEED}:<kind>", cross_check={})
        for kind, runner, out, rows, ledger, state, cases in self.large_finished_bases():
            tally["bases"][kind] = {"records": len(rows), "cases": len(cases)}
            judge = self.real_judge(out)  # certified: the write ledger the run wrote is in place and lists exactly these records
            for index, (family, damaged) in enumerate(cases):
                tally["families"][family.split("+")[0]] = tally["families"].get(family.split("+")[0], 0) + 1
                self.gate_compare(judge, state, ledger, kind, family, damaged, index, tally, forged_too=False, certified=rows)
            (out / "write_ledger.json").unlink()  # uncertified: the same damage, plus forged records and notes, in memory
            with R3Gate(out) as gate:
                for index, (family, damaged) in enumerate(cases):
                    self.gate_compare(gate, state, ledger, kind, family, damaged, index, tally)
                mocked = [gate(damaged) for _, damaged in cases[:40]]
            unpatched = [self.real_judge(out)(damaged) for _, damaged in cases[:40]]
            tally["cross_check"][kind] = mocked == unpatched
        tally["cases"] = sum(b["cases"] for b in tally["bases"].values())
        print("R3_ORACLE " + json.dumps(tally, sort_keys=True))
        self.assertGreaterEqual(min(b["cases"] for b in tally["bases"].values()), 500)
        self.assertEqual(tally["cross_check"], {kind: True for kind in TERMINAL_KINDS})
        self.assertGreater(min(tally["oracle"].get(v, 0) for v in ("ACCEPT", "REFUSE", "FLAG_PERSISTENCE")), 0)
        self.assertEqual((tally["disagreements"], tally["samples"]), (0, []))
        self.assertEqual((tally["resume_changed_outcome"], tally["i4_samples"]), (0, []))

    def test_R3_oracle_agrees_with_every_exit_on_seeded_random_damage_of_each_large_finished_state_certified_or_not(self):
        profile = self.r3_profile()
        check_result = ep.check_profile(profile)
        tally = self.new_tally(test="random-large-exits", seed=f"{ORACLE_SEED}:<kind>", per_variant=24)
        for kind, runner, out, rows, ledger, state, cases in self.large_finished_bases():
            tally["bases"][kind] = {"records": len(rows), "certified_exit_cases": 24, "uncertified_exit_cases": 24}
            for family, damaged in cases[:24]:
                self.exit_compare(runner, state, ledger, kind, family, damaged, tally, profile=profile, check_result=check_result, certified=rows)
            (out / "write_ledger.json").unlink()
            for family, damaged in cases[:24]:
                self.exit_compare(runner, state, ledger, kind, family, damaged, tally, profile=profile, check_result=check_result)
        print("R3_ORACLE " + json.dumps(tally, sort_keys=True))
        self.assertEqual(tally["exit_cases"], len(TERMINAL_KINDS) * 48)
        self.assertGreater(min(tally["exit_oracle"].get(v, 0) for v in ("ACCEPT", "REFUSE", "FLAG_PERSISTENCE")), 0)
        self.assertEqual((tally["exit_disagreements"], tally["exit_samples"]), (0, []))


class TerminalMatrixTests(TerminalHelpers, RunnerCase):
    """R3-2: {COMPLETE, HALTED for each reason} x {0..4 missing settlements} x {all eight exits} x {journal changed after a clean
    finish without its write ledger, journal changed after the write ledger was written}."""

    @staticmethod
    def expectation(kind, source, missing) -> str:
        if missing == 0:
            return "CLEAN"
        if missing == 4:
            return "REFUSE:ATTEMPTS_BEHIND_LEDGER"
        if kind != "PERSISTENCE":
            return "REFUSE:TERMINAL_RUN_JOURNAL_INCOMPLETE"
        return "REFUSE:WRITE_LEDGER_MISMATCH:attempts.jsonl" if source == "certified" else "FLAGGED"

    def cell_problems(self, observations, expectation, *, kind, missing, certified) -> list:
        if expectation.startswith("REFUSE:"):
            problems = self.exit_problems(observations, "REFUSE", certified=certified)
            if not observations[0]["text"].startswith("REFUSED:" + expectation[len("REFUSE:"):]):
                problems.append(("inspect_run", ["REASON"], observations[0]["class"], observations[0]["text"][:120]))
            return problems
        cls = "FLAG_PERSISTENCE" if expectation == "FLAGGED" else "ACCEPT"
        problems = self.exit_problems(observations, cls, certified=certified)
        verdict = observations[0]["carried"] or {}
        want = (("FLAGGED", [PERSISTENCE_FLAG]) if expectation == "FLAGGED" else ("CLEAN", []), missing, "HALTED" if kind != "COMPLETE" else "COMPLETE")
        if ((verdict.get("status"), verdict.get("flags")), verdict.get("missing_count"), verdict.get("state")) != want:
            problems.append(("inspect_run", ["VERDICT_CONTENT"], observations[0]["class"], json.dumps(verdict)[:120]))
        return problems

    def test_R3_terminal_matrix_every_finished_state_every_missing_count_every_exit_both_sources(self):
        profile = self.r3_profile()
        failures, cells = [], 0
        for kind in TERMINAL_KINDS:
            runner = self.terminal_base("m-" + kind.lower(), kind)
            out = runner.out_dir
            rows, ledger = xr.AppendLog(out / "attempts.jsonl").records(), json.loads((out / "ledger.json").read_text())
            settled = self.final_settlements(rows, ledger)
            self.assertGreaterEqual(len(settled), 4, kind)
            certificate, journal = (out / "write_ledger.json").read_bytes(), (out / "attempts.jsonl").read_bytes()
            for source in ("certified", "uncertified"):
                if source == "uncertified":
                    (out / "write_ledger.json").unlink()
                for missing in range(5):
                    dropped = set(settled[len(settled) - missing:])
                    (out / "attempts.jsonl").write_text(journal_text([r for i, r in enumerate(rows) if i not in dropped]))
                    expectation = self.expectation(kind, source, missing)
                    observations = self.every_exit(runner, profile=profile)
                    problems = self.cell_problems(observations, expectation, kind=kind, missing=missing, certified=source == "certified")
                    if expectation == "FLAGGED":  # once certified with its flags, it is certified once and stays flagged
                        twin, sim = self.twin(runner)
                        files = directory_files(out)
                        first = self.refusal(twin._write_accounting)
                        written = directory_files(out)
                        again = self.refusal(twin._write_accounting)
                        try:
                            later = xr.inspect_run(out)
                        except xr.RunBlocked as blocked:
                            later = {"refused": str(blocked)}
                        carried = observations[0]["carried"]
                        if (first, again, directory_files(out), later.get("integrity"), later.get("certificate"), sim.requests) != (
                                ("RETURNED", None), ("WRITE_LEDGER_ALREADY_CERTIFIED", carried), written, carried, "VERIFIED", []):
                            problems.append(("write_ledger", ["NOT_WRITE_ONCE"], "", f"{first} {again} {later.get('refused', '')}"[:160]))
                        restore_directory(out, files)
                    for row in observations:
                        cells += 1
                        print("R3_MATRIX " + json.dumps({"state": kind, "source": source, "missing": missing, "expected": expectation,
                                                         "entrance": row["entrance"], "outcome": row["text"], "class": row["class"],
                                                         "flags": (row["carried"] or {}).get("flags"), "requests": row["requests"],
                                                         "before_sha256": row["before_sha256"], "after_sha256": row["after_sha256"],
                                                         "added": row["added"]}, sort_keys=True))
                    failures += [(kind, source, missing) + problem for problem in problems]
                (out / "attempts.jsonl").write_bytes(journal)
            (out / "write_ledger.json").write_bytes(certificate)
        self.assertEqual(cells, len(TERMINAL_KINDS) * 2 * 5 * len(R3_ENTRANCES))
        self.assertEqual(failures, [])


class R3IntegrityTests(TerminalHelpers, RunnerCase):
    """R3-1/R3-3/D0-3/D0-5: named regressions for the terminal verdict, its carriage, the close, and the write-once ledger."""

    def drop(self, out, indexes) -> list:
        rows = xr.AppendLog(out / "attempts.jsonl").records()
        (out / "attempts.jsonl").write_text(journal_text([r for i, r in enumerate(rows) if i not in set(indexes)]))
        return [rows[i] for i in indexes]

    def test_R3B_only_a_second_regular_link_of_the_write_ledger_is_exempt(self):
        self.completes(lambda: self.run_it("r3b-publication"))
        out = self.root / "r3b-publication"
        certificate = out / "write_ledger.json"
        profile = self.r3_profile()
        genuine = out / ("write_ledger.json." + "a" * 32 + ".tmp")
        os.link(certificate, genuine)
        before = directory_digest(out)
        for call in (lambda: xr.inspect_run(out), lambda: xr.replay_run(out)):
            self.assertEqual(self.completes(call)["certificate"], "VERIFIED")
            self.assertEqual(directory_digest(out), before)
        for argv, key in ((["check", "--profile", str(profile), "--out", str(out)], "run_certificate"),
                          (["replay", "--out", str(out)], "certificate")):
            code, accepted = self.completes(lambda: self.cli(argv))
            self.assertEqual((code, accepted[key]), (0, "VERIFIED"))
            self.assertEqual(directory_digest(out), before)
        genuine.unlink()

        elsewhere = self.root / "r3b-other-file"
        elsewhere.write_bytes(certificate.read_bytes())
        cases = (
            ("equal-content copy", "write_ledger.json." + "b" * 32 + ".tmp", "copy"),
            ("symlink to certificate", "write_ledger.json." + "c" * 32 + ".tmp", "symlink-certificate"),
            ("symlink elsewhere", "write_ledger.json." + "d" * 32 + ".tmp", "symlink-elsewhere"),
            ("hard link in subdirectory", "nested/write_ledger.json." + "e" * 32 + ".tmp", "hardlink"),
            ("short name", "write_ledger.json." + "f" * 31 + ".tmp", "hardlink"),
            ("long name", "write_ledger.json." + "f" * 33 + ".tmp", "hardlink"),
            ("uppercase name", "write_ledger.json." + "A" * 32 + ".tmp", "hardlink"),
            ("different content", "write_ledger.json." + "0" * 32 + ".tmp", "different"),
            ("unrelated file", ".DS_Store", "different"),
        )
        observed, expected = {}, {}
        for label, relative, kind in cases:
            extra = out / relative
            extra.parent.mkdir(exist_ok=True)
            if kind == "hardlink":
                os.link(certificate, extra)
            elif kind.startswith("symlink"):
                extra.symlink_to(certificate if kind == "symlink-certificate" else elsewhere)
            else:
                extra.write_bytes(certificate.read_bytes() if kind == "copy" else b"not the write ledger")
            try:
                before = directory_digest(out)
                reason = "WRITE_LEDGER_UNLISTED:" + relative
                texts = []
                for call in (lambda: xr.inspect_run(out), lambda: xr.replay_run(out)):
                    try:
                        call()
                        texts.append("RETURNED")
                    except xr.RunBlocked as blocked:
                        texts.append(str(blocked))
                    except Exception as exc:  # a crash is another observation, never a refusal
                        texts.append(f"CRASHED:{type(exc).__name__}")
                for argv in (["check", "--profile", str(profile), "--out", str(out)], ["replay", "--out", str(out)]):
                    code, printed = self.completes(lambda: self.cli(argv))
                    texts.append((code, printed.get("state"), reason in printed.get("reasons", [])))
                observed[label] = (texts, directory_digest(out) == before)
            finally:
                extra.unlink()
                if extra.parent != out:
                    extra.parent.rmdir()
            expected[label] = ([reason, reason, (2, "BLOCKED", True), (2, "BLOCKED", True)], True)
        self.assertEqual(observed, expected)  # one assertion outside any subTest: the mutation classifier only sees those

    def test_R3_a_finished_run_lacking_a_fact_is_refused_unless_a_persistence_halt_left_it_which_is_flagged(self):
        bank, budget = self.small_bank(), str(2 * 2 * CAP * 3)
        observed, expected = {}, {}
        for kind in TERMINAL_KINDS:
            runner = self.terminal_base("f1-" + kind.lower(), kind, bank, budget)
            out = runner.out_dir
            (out / "write_ledger.json").unlink()  # the gate's own rule, without the write ledger's help
            clean = xr.inspect_run(out).get("integrity") or {}
            rows, ledger = xr.AppendLog(out / "attempts.jsonl").records(), json.loads((out / "ledger.json").read_text())
            lost = self.drop(out, [self.final_settlements(rows, ledger)[-1]])[0]
            try:
                verdict = xr.inspect_run(out).get("integrity") or {}
                outcome = ("ACCEPTED", verdict.get("status"), verdict.get("flags"), verdict.get("missing"))
            except xr.RunBlocked as blocked:
                outcome = ("REFUSED", str(blocked).split(":")[0])
            observed[kind] = ((clean.get("status"), clean.get("flags"), clean.get("missing")), outcome)
            expected[kind] = (("CLEAN", [], []), ("ACCEPTED", "FLAGGED", [PERSISTENCE_FLAG], [f"SETTLED:{lost['slot']}:{lost['attempt']}"])
                              if kind == "PERSISTENCE" else ("REFUSED", "TERMINAL_RUN_JOURNAL_INCOMPLETE"))
        print("R3_GATE " + json.dumps(observed, sort_keys=True))
        self.assertEqual(observed, expected)

    def test_R3_every_exit_carries_the_one_gate_verdict_verbatim(self):
        bank, budget, profile = self.small_bank(), str(2 * 2 * CAP * 3), self.r3_profile()
        # a PERSISTENCE halt with two settlements it never wrote, not yet certified
        runner = self.terminal_base("f2-persist", "PERSISTENCE", bank, budget)
        out = runner.out_dir
        (out / "write_ledger.json").unlink()
        rows, ledger = xr.AppendLog(out / "attempts.jsonl").records(), json.loads((out / "ledger.json").read_text())
        self.drop(out, self.final_settlements(rows, ledger)[-2:])
        # Every direct product call below goes through `completes`: a refusal or crash where a value is expected is a failed
        # assertion, never an ERROR (the mutation rule counts assertions only).
        gate = self.completes(lambda: xr.inspect_run(out)).get("integrity") or {}
        self.assertEqual((gate.get("status"), gate.get("flags"), gate.get("missing_count")), ("FLAGGED", [PERSISTENCE_FLAG], 2))
        twin, sim = self.twin(runner)
        carried = {"replay_run": self.completes(lambda: xr.replay_run(out)).get("integrity")}
        code, printed = self.completes(lambda: self.cli(["check", "--profile", str(profile), "--out", str(out)]))
        carried["CLI check --out"] = (code, printed.get("status"), printed.get("run_integrity"))
        code, printed = self.completes(lambda: self.cli(["replay", "--out", str(out)]))
        carried["CLI replay"] = (code, printed.get("integrity"))
        carried["report"] = self.refusal(lambda: twin._finalize(None))
        carried["Runner.resume"] = self.refusal(twin.resume)
        carried["write_ledger"] = self.refusal(twin._write_accounting)
        carried["write_ledger content"] = json.loads((out / "write_ledger.json").read_text()).get("integrity") if (out / "write_ledger.json").exists() else None
        carried["write_ledger again"] = self.refusal(twin._write_accounting)
        replayed = self.completes(lambda: xr.replay_run(out))
        carried["replay_run after certification"] = (replayed.get("integrity"), replayed.get("certificate"))
        self.assertEqual(carried, {"replay_run": gate, "CLI check --out": (4, "FLAGGED", gate), "CLI replay": (4, gate),
                                   "report": ("RUN_IS_HALTED", gate), "Runner.resume": ("RUN_IS_HALTED", gate), "write_ledger": ("RETURNED", None),
                                   "write_ledger content": gate, "write_ledger again": ("WRITE_LEDGER_ALREADY_CERTIFIED", gate),
                                   "replay_run after certification": (gate, "VERIFIED")})
        self.assertEqual(sim.requests, [])
        # a run that crashed in the START window: the gap it resumed with is noted, and the record written back stays flagged
        name = "f2-backfill"
        first, _ = self.make(name, log=False)
        with self.crash_on("START"):
            self.crashes(first.start)
        out = self.root / name
        gap = self.completes(lambda: xr.inspect_run(out)).get("integrity") or {}
        code, printed = self.completes(lambda: self.cli(["check", "--profile", str(profile), "--out", str(out)]))
        self.assertEqual((gap.get("state"), gap.get("flags"), gap.get("missing_count"), code, printed.get("status"), printed.get("run_integrity")),
                         ("RUNNING", [RESUMABLE_FLAG], 1, 4, "FLAGGED", gap))
        resumed, sim2 = self.make(name, log=False)
        result = self.completes(resumed.resume)
        final = self.completes(lambda: xr.inspect_run(out)).get("integrity") or {}
        self.assertEqual((final.get("state"), final.get("status"), final.get("flags"), final.get("reconciled"), final.get("missing")),
                         ("COMPLETE", "FLAGGED", ["RECONCILED_FROM_LEDGER"], 1, []))
        note = [r for r in xr.AppendLog(out / "attempts.jsonl").records() if r["phase"] == "RESUME"][-1]
        replayed = self.completes(lambda: xr.replay_run(out))
        code, printed = self.completes(lambda: self.cli(["check", "--profile", str(profile), "--out", str(out)]))
        code2, printed2 = self.completes(lambda: self.cli(["replay", "--out", str(out)]))
        self.assertEqual({"RESUME note": note["reconciliation"]["ledger_ahead"], "Runner.resume": result.get("integrity"),
                          "report.json": json.loads((out / "report.json").read_text()).get("integrity"),
                          "write_ledger.json": json.loads((out / "write_ledger.json").read_text()).get("integrity"),
                          "replay_run": replayed.get("integrity"), "replay_run report": (replayed.get("report") or {}).get("integrity"),
                          "CLI check --out": (code, printed.get("status"), printed.get("run_integrity")), "CLI replay": (code2, printed2.get("integrity"))},
                         {"RESUME note": gap.get("missing"), "Runner.resume": final, "report.json": final, "write_ledger.json": final, "replay_run": final,
                          "replay_run report": final, "CLI check --out": (4, "FLAGGED", final), "CLI replay": (4, final)})
        self.assertEqual(len([r for r in sim2.requests if r["path"] == "responses"]), 39)

    def test_R3_check_out_prints_the_run_verdict_and_is_never_a_plain_pass_for_a_run_the_gate_flags(self):
        profile = self.r3_profile()
        self.run_it("f5-clean")
        runner = self.terminal_base("f5-persist", "PERSISTENCE", self.small_bank(), str(2 * 2 * CAP * 3))
        (runner.out_dir / "write_ledger.json").unlink()
        rows, ledger = xr.AppendLog(runner.out_dir / "attempts.jsonl").records(), json.loads((runner.out_dir / "ledger.json").read_text())
        self.drop(runner.out_dir, self.final_settlements(rows, ledger)[-1:])
        self.run_it("f5-damaged")
        self.drop(self.root / "f5-damaged", [max(self.final_settlements(self.rows_of("f5-damaged"), self.ledger("f5-damaged")))])
        gap, _ = self.make("f5-gap", log=False)
        with self.crash_on("SETTLED"):
            self.crashes(gap.start)
        observed = {}
        for name in ("f5-clean", "f5-persist", "f5-damaged", "f5-gap"):
            code, printed = self.cli(["check", "--profile", str(profile), "--out", str(self.root / name)])
            verdict = printed.get("run_integrity") or {}
            observed[name] = (code, printed.get("status") or printed.get("state"), verdict.get("status"), verdict.get("flags"),
                              [r.split(":")[0] for r in printed.get("reasons", [])])
        self.assertEqual(observed, {"f5-clean": (0, "PASS", "CLEAN", [], []), "f5-persist": (4, "FLAGGED", "FLAGGED", [PERSISTENCE_FLAG], []),
                                    "f5-damaged": (2, "BLOCKED", None, None, ["TERMINAL_RUN_JOURNAL_INCOMPLETE"]),
                                    "f5-gap": (4, "FLAGGED", "FLAGGED", [RESUMABLE_FLAG], [])})

    def test_R3_a_write_ledger_is_written_once_and_a_reopened_certified_run_is_never_certified_again(self):
        _, runner, _ = self.run_it("f3")
        out = self.root / "f3"
        certificate, before = (out / "write_ledger.json").read_bytes(), directory_digest(out)
        twin, sim = self.make("f3", log=False)
        refused = {"fresh writer": self.refusal(twin._write_accounting)[0], "the run's own writer": self.refusal(runner._write_accounting)[0],
                   "close again": self.refusal(lambda: twin._finalize(None))[0]}
        self.assertEqual(refused, {"fresh writer": "WRITE_LEDGER_ALREADY_CERTIFIED", "the run's own writer": "WRITE_LEDGER_ALREADY_CERTIFIED",
                                   "close again": "RUN_IS_COMPLETE"})
        self.assertEqual((directory_digest(out), (out / "write_ledger.json").read_bytes(), sim.requests), (before, certificate, []))
        self.interrupted_at("f3-open", 3)
        open_before = directory_digest(self.root / "f3-open")
        writer, _ = self.make("f3-open", log=False)
        self.assertEqual(self.refusal(writer._write_accounting)[0], "WRITE_LEDGER_RUN_NOT_TERMINAL")
        self.assertEqual(directory_digest(self.root / "f3-open"), open_before)
        # a certified run reopened by rewriting its state cannot resume; the close retains its second defence
        (out / "run_state.json").write_text(xr.canonical({"state": "RUNNING", "t": 0}))
        self.assertIn("WRITE_LEDGER_EXISTS_NOT_RECERTIFIED", (xr.inspect_run(out).get("integrity") or {}).get("flags", []))
        again, sim2 = self.make("f3", log=False)
        reopened_before = directory_digest(out)
        reason, verdict = self.refusal(again.resume)
        self.assertEqual(reason, "RUN_IS_CERTIFIED")
        self.assertIn("WRITE_LEDGER_EXISTS_NOT_RECERTIFIED", (verdict or {}).get("flags", []))
        self.assertEqual((directory_digest(out), (out / "write_ledger.json").read_bytes(), sim2.requests), (reopened_before, certificate, []))
        closed = self.completes(lambda: runner._finalize(None))
        self.assertEqual((closed["state"], (out / "write_ledger.json").read_bytes(), sim2.requests), ("COMPLETE", certificate, []))
        self.assertIn("WRITE_LEDGER_EXISTS_NOT_RECERTIFIED", (closed.get("integrity") or {}).get("flags", []))
        self.assertIn("WRITE_LEDGER_EXISTS_NOT_RECERTIFIED", (json.loads((out / "report.json").read_text()).get("integrity") or {}).get("flags", []))
        self.assertBlocks("WRITE_LEDGER_MISMATCH", lambda: xr.inspect_run(out))

    def test_R3_any_byte_changed_after_certification_is_refused_by_every_read_only_exit(self):
        profile = self.r3_profile()
        self.run_it("f4")
        out = self.root / "f4"
        files, rows = directory_files(out), self.rows_of("f4")
        listing = json.loads(files["write_ledger.json"])
        request = next(i for i, r in enumerate(rows) if r["phase"] == "REQUEST")
        # a settlement and the next slot's start: causally unrelated, so the R2 rules accept the swap and only the certificate sees it
        a = max(i for i in range(len(rows) - 1) if rows[i]["phase"] == "SETTLED" and rows[i + 1]["phase"] == "START")
        swapped = list(rows)
        swapped[a], swapped[a + 1] = rows[a + 1], rows[a]
        report = json.loads(files["report.json"])
        changes = {
            "journal: a request record deleted": ("attempts.jsonl", journal_text(rows[:request] + rows[request + 1:]), "WRITE_LEDGER_MISMATCH:attempts.jsonl"),
            "journal: a RESUME note appended": ("attempts.jsonl", journal_text(rows + [forged_resume([], 1)]), "WRITE_LEDGER_MISMATCH:attempts.jsonl"),
            "journal: two unrelated settlements swapped": ("attempts.jsonl", journal_text(swapped), "WRITE_LEDGER_MISMATCH:attempts.jsonl"),
            "journal: the same records, spaced differently": ("attempts.jsonl", "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                                                              "WRITE_LEDGER_MISMATCH:attempts.jsonl"),
            "ledger: the same content, formatted differently": ("ledger.json", json.dumps(json.loads(files["ledger.json"])), "WRITE_LEDGER_MISMATCH:ledger.json"),
            "report edited": ("report.json", xr.canonical({**report, "classification": ["EDITED"]}), "WRITE_LEDGER_MISMATCH:report.json"),
            "runs edited": ("runs.json", xr.canonical({}), "WRITE_LEDGER_MISMATCH:runs.json"),
            "state rewritten": ("run_state.json", xr.canonical({"state": "COMPLETE", "t": 1.0}), "WRITE_LEDGER_MISMATCH:run_state.json"),
            "a file added": ("notes.txt", "added after the run", "WRITE_LEDGER_UNLISTED:notes.txt"),
            "a listed file removed": ("runs.json", None, "WRITE_LEDGER_MISMATCH:runs.json"),
            "the certificate's verdict edited": ("write_ledger.json", xr.canonical({**listing, "integrity": {**listing.get("integrity", {}), "status": "EDITED"}}),
                                                 "WRITE_LEDGER_INTEGRITY_MISMATCH"),
            "the certificate unreadable": ("write_ledger.json", "{not json", "WRITE_LEDGER_INVALID_OR_UNREADABLE"),
        }
        failures = []
        for label, (name, content, reason) in changes.items():
            restore_directory(out, files)
            if content is None:
                (out / name).unlink()
            else:
                (out / name).write_text(content)
            for entrance, call in (("inspect_run", lambda: xr.inspect_run(out)), ("replay_run", lambda: xr.replay_run(out)),
                                   ("CLI check --out", lambda: self.cli(["check", "--profile", str(profile), "--out", str(out)])),
                                   ("CLI replay", lambda: self.cli(["replay", "--out", str(out)]))):
                before = directory_digest(out)
                try:
                    value = call()
                    text = ("REFUSED:" + ",".join(value[1]["reasons"]) if isinstance(value, tuple) and value[1].get("state") == "BLOCKED"
                            else f"NOT_REFUSED:{json.dumps(value if isinstance(value, tuple) else sorted(value))[:120]}")
                except xr.RunBlocked as blocked:
                    text = "REFUSED:" + str(blocked)
                except Exception as exc:
                    text = f"CRASHED:{type(exc).__name__}:{exc}"
                ok = text.startswith("REFUSED:" + reason) and directory_digest(out) == before
                print("R3_CERTIFICATE " + json.dumps({"change": label, "entrance": entrance, "outcome": text[:160], "ok": ok}, sort_keys=True))
                if not ok:
                    failures.append((label, entrance, text[:160]))
        restore_directory(out, files)
        self.assertEqual(failures, [])

    def test_R3_a_run_that_lost_a_fact_closes_flagged_only_after_a_persistence_halt_and_is_otherwise_refused_before_it_closes(self):
        ITEM = "eng-foreign-writer"
        probe, _ = self.make("f6-probe", log=False)
        lost = f"{probe.order[0]}:0"  # the first slot's settlement is the fact the disk loses; the stop comes later, at ITEM
        self.assertNotEqual(probe.order[0], ITEM)
        observed = {}
        for case in ("persistence-halt", "rejected-halt", "complete", "settlement-write-fails"):
            name = "f6-" + case
            faults = [{"path": "responses", "item_id": ITEM, "occurrence": 1, "fault": "http_400"}] if case == "rejected-halt" else []
            runner, sim = self.make(name, log=False, faults=faults)
            out, seen = runner.out_dir, {}
            append, finalize = xr.AppendLog.append, xr.Runner._finalize

            def lossy(log, record, case=case, lost=lost, seen=seen):
                if record["phase"] == "SETTLED" and record.get("slot") == lost and "lost" not in seen:
                    seen["lost"] = True
                    if case == "settlement-write-fails":
                        raise OSError(28, "No space left on device (injected)")
                    return None  # the disk acknowledged the record and lost it
                if case == "persistence-halt" and record["phase"] == "RESPONSE" and record.get("path") == "responses" \
                        and record.get("slot") == f"{ITEM}:0":
                    raise OSError(28, "No space left on device (injected)")
                return append(log, record)

            def watched(runner_, halted, seen=seen):
                seen["before_close"] = directory_digest(runner_.out_dir)
                return finalize(runner_, halted)

            with mock.patch.object(xr.AppendLog, "append", lossy), mock.patch.object(xr.Runner, "_finalize", watched):
                try:
                    result = runner.start()
                    outcome = ("RETURNED", result["state"], (result["halt"] or {}).get("reason"), result.get("integrity"))
                except xr.RunBlocked as blocked:
                    outcome = ("REFUSED", str(blocked))
                except OSError as exc:
                    outcome = ("OSERROR", str(exc))
            state = json.loads((out / "run_state.json").read_text())["state"]
            # through `completes`: a refusal or crash where a value is expected is a failed assertion, never an ERROR
            verdict = (self.completes(lambda: xr.inspect_run(out)).get("integrity") or {})
            observed[case] = {"outcome": outcome[:2] if outcome[0] != "RETURNED" else outcome[:3], "state": state,
                              "verdict": (verdict.get("status"), verdict.get("flags"), verdict.get("missing")),
                              "close_reached": "before_close" in seen,
                              "unchanged_by_the_close": (seen["before_close"] == directory_digest(out))
                              if "before_close" in seen and outcome[0] != "RETURNED" else None,
                              "files": sorted(p.name for p in out.iterdir())}
            if outcome[0] == "RETURNED":
                certificate = self.completes(lambda: json.loads((out / "write_ledger.json").read_text())).get("integrity")
                observed[case]["carried"] = {"result": outcome[3] == verdict,
                                             "report.json": self.completes(lambda: json.loads((out / "report.json").read_text())).get("integrity") == verdict,
                                             "write_ledger.json": certificate == verdict, "certificate": self.completes(lambda: xr.inspect_run(out)).get("certificate"),
                                             "resume": self.refusal(self.twin(runner)[0].resume) == ("RUN_IS_HALTED", verdict)}
            else:  # left RUNNING with the gap: a clean resume writes the lost fact back before anything else
                kept = len(xr.AppendLog(out / "attempts.jsonl").records())
                again, sim2 = self.make(name, log=False)
                closed = self.completes(again.resume)
                new = xr.AppendLog(out / "attempts.jsonl").records()[kept:]
                observed[case]["resume"] = {"first_record": (new[0]["phase"], new[0].get("slot"), new[0].get("reconciled")),
                                            "flags": (closed.get("integrity") or {}).get("flags")}
                print("R3_CLOSE " + json.dumps({"case": case, "resumed_state": closed["state"], "halt": closed["halt"],
                                                "new_generations": len([r for r in sim2.requests if r["path"] == "responses"])}, sort_keys=True))
        print("R3_CLOSE " + json.dumps(observed, sort_keys=True, default=str))
        named = f"SETTLED:{lost}:1"
        written_back = {"first_record": ("SETTLED", lost, True), "flags": ["RECONCILED_FROM_LEDGER"]}
        self.assertEqual({case: {k: v for k, v in row.items() if k != "files"} for case, row in observed.items()}, {
            "persistence-halt": {"outcome": ("RETURNED", "HALTED", "PERSISTENCE"), "state": "HALTED", "close_reached": True,
                                 "verdict": ("FLAGGED", [PERSISTENCE_FLAG], [named]), "unchanged_by_the_close": None,
                                 "carried": {"result": True, "report.json": True, "write_ledger.json": True, "certificate": "VERIFIED", "resume": True}},
            "rejected-halt": {"outcome": ("REFUSED", "TERMINAL_RUN_JOURNAL_INCOMPLETE:HALTED_REJECTED:1_MISSING"), "state": "RUNNING",
                              "close_reached": True, "verdict": ("FLAGGED", [RESUMABLE_FLAG], [named]), "unchanged_by_the_close": True,
                              "resume": written_back},
            "complete": {"outcome": ("REFUSED", "TERMINAL_RUN_JOURNAL_INCOMPLETE:COMPLETE:1_MISSING"), "state": "RUNNING", "close_reached": True,
                         "verdict": ("FLAGGED", [RESUMABLE_FLAG], [named]), "unchanged_by_the_close": True, "resume": written_back},
            # a settlement that fails to write loudly never reaches the close at all: the run stops RUNNING with the gap (D0-3 evidence)
            "settlement-write-fails": {"outcome": ("OSERROR", "[Errno 28] No space left on device (injected)"), "state": "RUNNING",
                                       "close_reached": False, "verdict": ("FLAGGED", [RESUMABLE_FLAG], [named]), "unchanged_by_the_close": None,
                                       "resume": written_back}})
        self.assertNotIn("report.json", observed["complete"]["files"])  # the refused close wrote nothing: no report, no write ledger
        self.assertNotIn("write_ledger.json", observed["rejected-halt"]["files"])


if __name__ == "__main__":
    unittest.main()
