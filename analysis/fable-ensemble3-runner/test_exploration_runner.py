#!/usr/bin/env python3
"""Offline tests for the formal exploration runner.  No socket is ever opened and no model is ever called.

The sealed engineering bank (8 items) and a simulated Responses endpoint exercise the real adapter, ledger, checker and
scorer.  Every fault is injected at the provider boundary; a real child process is SIGKILLed mid-call for the crash test.
Run: python3 -B -m unittest -q test_exploration_runner
"""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import random
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
        self.assertEqual((result["halt"]["reason"], slot["status"]), ("PERSISTENCE", "INVALID_OUTPUT"))
        self.assertEqual(slot["receipts"][0]["cost"], str(OK_COST))
        # the request could not be written, so it was never sent
        runner, sim = self.make("persist-request")
        with mock.patch.object(xr.AppendLog, "append", lambda log, record: failing(log, record, "REQUEST")):
            result = runner.start()
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

    def test_check_command_exit_codes(self):
        self.assertEqual(self.cli(["check", "--profile", str(self.profile)])[0], 0)
        code, result = self.cli(["check", "--profile", str(self.profile), "--mode", "LIVE"])
        self.assertEqual((code, result["status"]), (2, "BLOCKED"))


if __name__ == "__main__":
    unittest.main()
