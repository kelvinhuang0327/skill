#!/usr/bin/env python3
"""Offline tests for the Stage 2B probe.  No socket is ever opened and no model is ever called.

The registration is built against the real v5 profile; the endpoint is a simulated Responses server whose answers and
faults the tests control.  A real child process is SIGKILLed mid-request for the crash test.
Run: python3 -B -m unittest -q test_probe_2b
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _lab  # noqa: E402,F401
import execution_profile as ep  # noqa: E402
import exploration_runner as xr  # noqa: E402
import probe_2b as pb  # noqa: E402
import responses_adapter as api  # noqa: E402
from simulated_transport import SimulatedResponses  # noqa: E402

EVIDENCE = Path(os.environ.get("FABLE_ENSEMBLE3_EVIDENCE", "/Users/kelvin/VibeCoding-WorkSpace/fable-ensemble3-r1-evidence"))
SEALED = "32d5c9991c71d5b4025c015c395c9d41226a6d45"
SOURCE = "d037a9a8c612957540ff03c2d46b5b3ab8f0583a"
KEY = "sk-test-NOT-A-REAL-KEY-0000"
# A and B: input count 0.05 + generation (1200 in x 0.25 + 80 out x 1.20)/1e6; C: 16 output tokens; D: two counts at 0.05
HAPPY_SPENT = 4 * Decimal("0.05") + 2 * Decimal("0.000396") + Decimal("0.0003192")


class ProbeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        needed = {"protocol": EVIDENCE / "formal-bank-v5/review-final/protocol.with-review.draft.json",
                  "receipt": EVIDENCE / "formal-bank-v5/review-final/bank_review.json", "bank": EVIDENCE / "formal-bank-v5/manifest.json",
                  "pack": EVIDENCE / "instruction-source-v2/candidate_instructions.md", "pack_manifest": EVIDENCE / "instruction-source-v2/manifest.json"}
        missing = [str(p) for p in needed.values() if not p.exists()]
        if missing:
            raise AssertionError(f"real v5 artifacts not found (set FABLE_ENSEMBLE3_EVIDENCE): {missing}")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        profile = ep.build_profile(protocol=needed["protocol"], receipt=needed["receipt"], bank_manifest=needed["bank"], pack=needed["pack"],
                                   pack_manifest=needed["pack_manifest"], source_commit=SOURCE, sealed_commit=SEALED, repo=ep.ANALYSIS.parent)
        cls.profile = cls.base / "profile.json"
        cls.profile.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n")
        pb.eb.build(cls.base / "eng")
        cls.eng = cls.base / "eng" / "manifest.json"
        cls.pack = needed["pack"]
        cls.instructions = cls.pack.read_text(encoding="utf-8")
        cls.registration = cls.base / "registration.json"
        xr.exclusive_write(cls.registration, xr.canonical(pb.build_registration(cls.profile, cls.eng)))
        _, cls.public, cls.labels = xr.load_bank(cls.eng)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.base))
        for target in ("urllib.request.urlopen", "socket.socket.connect"):
            patcher = mock.patch(target, side_effect=AssertionError("the network must never be touched in tests"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def sim(self, faults=(), **kwargs):
        return SimulatedResponses(self.public, answer_for=lambda item: self.labels[item["item_id"]]["acceptable"][0], faults=faults, **kwargs)

    def probe(self, name="probe", *, faults=(), mode="SIMULATION", registration=None, transport="sim", **sim_kwargs):
        transport = self.sim(faults, **sim_kwargs) if transport == "sim" else transport
        return pb.Probe(self.work / name, registration or self.registration, mode=mode, instructions=self.instructions, public=self.public,
                        transport=transport), transport

    def run_probe(self, name="probe", **kwargs):
        probe, sim = self.probe(name, **kwargs)
        return probe.run(), probe, sim

    def ledger(self, name="probe") -> dict:
        return json.loads((self.work / name / "probe_ledger.json").read_text())

    def rule(self, fault, step_path="responses", item="eng-resume-dead-child", occurrence=1):
        return {"path": step_path, "item_id": item, "occurrence": occurrence, "fault": fault}

    def approval(self, **changes) -> Path:
        record = {"approved": True, "approved_by": "Owner", "approved_on": "2026-10-04", "scope": pb.APPROVAL_SCOPE,
                  "registration_sha256": ep.sha256_file(self.registration), "max_total_reservation_usd": "2.00",
                  "data_egress": "synthetic engineering fixtures and the public skill reference text to api.openai.com"}
        record.update(changes)
        path = self.work / "approval.json"
        path.write_text(json.dumps({k: v for k, v in record.items() if v is not None}))
        return path

    def assertBlocks(self, regex, call):
        """`call` must raise ProbeBlocked with `regex` in its text.  Any other outcome is a failed assertion, not a crash:
        a guard that is gone must not be mistaken for a test that broke."""
        try:
            call()
        except pb.ProbeBlocked as caught:
            self.assertRegex(str(caught), regex)
        except Exception as other:
            self.fail(f"expected ProbeBlocked matching {regex!r}, got {type(other).__name__}: {other}")
        else:
            self.fail(f"expected ProbeBlocked matching {regex!r}: nothing was raised")


class RegistrationTests(ProbeCase):
    def test_the_registration_pins_everything_and_fits_the_cap(self):
        registration = json.loads(self.registration.read_text())
        self.assertEqual(registration["steps"], pb.step_definitions())
        self.assertEqual((registration["max_requests"], registration["max_generation_requests"], registration["retries"]), (7, 3, 0))
        self.assertEqual((registration["total_reserved_usd"], registration["cap_usd"]), ("1.85", "2.00"))
        self.assertEqual(registration["status"], "REGISTERED_NOT_APPROVED_NOT_EXECUTED")
        self.assertEqual(registration.get("runner_sources"), ep.runner_sources())  # an approval bound to this file covers this code
        for name in ("profile", "adapter", "engineering_manifest", "instruction"):
            self.assertEqual(ep.sha256_file(registration[name]["ref"]), registration[name]["sha256"], name)
        self.assertEqual(registration["instruction"]["sha256"], ep.sha256_file(self.pack))
        self.assertEqual((registration["model"], registration["effort"]), (api.MODEL, api.EFFORT))
        self.assertEqual([s["id"] for s in registration["steps"]], ["A", "B", "C", "D"])
        self.assertEqual(registration["steps"][2]["max_output_tokens"], 16)
        outcome_requests = [(s["id"], i) for s in registration["steps"] for i, r in enumerate(s["requests"]) if r["rejection_is_an_outcome"]]
        self.assertEqual(outcome_requests, [("C", 0), ("D", 1)])  # only the two requests that are themselves experiments

    def test_a_registration_that_exceeds_the_cap_or_names_a_formal_item_is_refused(self):
        with self.assertRaisesRegex(ValueError, "exceed the cap"):
            pb.build_registration(self.profile, self.eng, "1.84")
        self.assertEqual(pb.build_registration(self.profile, self.eng, "1.85")["total_reserved_usd"], "1.85")
        formal = json.loads(self.eng.read_text())
        formal["purpose"] = "FORMAL_DECISION_EXPLORATION"
        other = self.work / "formal.json"
        other.write_text(json.dumps(formal))
        with self.assertRaisesRegex(ValueError, "never a formal item"):
            pb.build_registration(self.profile, other)
        formal["purpose"] = "ENGINEERING_ONLY"
        formal["items"] = [row for row in formal["items"] if row["item_id"] != pb.FIXTURE_B]
        other.write_text(json.dumps(formal))
        with self.assertRaisesRegex(ValueError, "never a formal item"):
            pb.build_registration(self.profile, other)

    def test_a_tampered_registration_is_blocked_by_its_own_arithmetic(self):
        original = json.loads(self.registration.read_text())

        def blocked(mutate, reason):
            record = copy.deepcopy(original)
            mutate(record)
            path = self.work / "tampered.json"
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(pb.ProbeBlocked, reason):
                pb.load_registration(path)

        blocked(lambda r: r["steps"][0]["requests"].append(dict(r["steps"][0]["requests"][0])), "REGISTRATION_STEPS_ALTERED")
        blocked(lambda r: r["steps"][2].update(max_output_tokens=4096), "REGISTRATION_STEPS_ALTERED")
        blocked(lambda r: r["steps"][3]["requests"][1].update(rejection_is_an_outcome=False), "REGISTRATION_STEPS_ALTERED")
        blocked(lambda r: r.update(artifact="OTHER"), "REGISTRATION_STEPS_ALTERED")
        blocked(lambda r: r.update(retries=1), "REGISTRATION_LIMITS_ALTERED")
        blocked(lambda r: r.update(max_requests=8), "REGISTRATION_LIMITS_ALTERED")
        blocked(lambda r: r.update(max_generation_requests=4), "REGISTRATION_LIMITS_ALTERED")
        blocked(lambda r: r.update(total_reserved_usd="1.00"), "REGISTRATION_COST_ALTERED")
        blocked(lambda r: r.update(cap_usd="1.00"), "REGISTRATION_COST_ALTERED")
        self.assertEqual(pb.load_registration(self.registration)["cap_usd"], "2.00")

    def test_registration_is_deterministic_and_the_cli_never_overwrites_it(self):
        first = pb.build_registration(self.profile, self.eng)
        self.assertEqual(first, pb.build_registration(self.profile, self.eng))
        out = self.work / "registration.json"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(pb.main(["register", "--profile", str(self.profile), "--engineering-bank", str(self.base / "eng"), "--out", str(out)]), 0)
        self.assertEqual(json.loads(buffer.getvalue())["sha256"], ep.sha256_file(out))
        with self.assertRaises(FileExistsError), contextlib.redirect_stdout(io.StringIO()):
            pb.main(["register", "--profile", str(self.profile), "--engineering-bank", str(self.base / "eng"), "--out", str(out)])


class ProbeRunTests(ProbeCase):
    def test_a_clean_probe_settles_all_four_steps_with_exactly_seven_requests(self):
        report, probe, sim = self.run_probe()
        ledger = self.ledger()
        self.assertEqual((report["ledger_state"], report["stop"]), ("COMPLETE", None))
        self.assertEqual({sid: s["state"] for sid, s in ledger["steps"].items()}, {"A": "SETTLED", "B": "SETTLED", "C": "SETTLED", "D": "SETTLED"})
        self.assertEqual((ledger["requests_sent"], ledger["generation_sent"]), (7, 3))
        self.assertEqual([(r["path"], r["item_id"]) for r in sim.requests],
                         [("responses/input_tokens", pb.FIXTURE_A), ("responses", pb.FIXTURE_A), ("responses/input_tokens", pb.FIXTURE_B),
                          ("responses", pb.FIXTURE_B), ("responses", pb.FIXTURE_A), ("responses/input_tokens", pb.FIXTURE_A),
                          ("responses/input_tokens", pb.FIXTURE_A)])
        self.assertEqual(Decimal(ledger["spent_upper_usd"]), HAPPY_SPENT)
        self.assertEqual(Decimal(ledger["reserved_usd"]), Decimal("1.85"))
        for step in ledger["steps"].values():
            for record in step["requests"]:
                self.assertEqual(record["state"], "RECORDED")
                self.assertEqual(ep.sha256_file(self.work / "probe" / record["request_file"]), record["request_sha256"])
                self.assertEqual(ep.sha256_file(self.work / "probe" / record["response_file"]), record["response_sha256"])

    def test_the_stored_requests_are_exactly_what_the_adapter_would_send(self):
        self.run_probe()
        sent = json.loads((self.work / "probe" / "requests" / "A-2.request.json").read_text())
        self.assertEqual((self.work / "probe" / "requests" / "A-2.request.json").read_text(),
                         json.dumps(api.build_request(self.public[pb.FIXTURE_A], self.instructions)))
        self.assertEqual(sent["max_output_tokens"], api.OUTPUT_LIMIT)
        cap_request = json.loads((self.work / "probe" / "requests" / "C-1.request.json").read_text())
        self.assertEqual(cap_request["max_output_tokens"], 16)
        d1 = json.loads((self.work / "probe" / "requests" / "D-1.request.json").read_text())
        d2 = json.loads((self.work / "probe" / "requests" / "D-2.request.json").read_text())
        self.assertEqual(sorted(d1), ["input", "instructions", "model", "tools"])  # the adapter's pre-count field set
        self.assertEqual(sorted(d2), ["input", "instructions", "model", "reasoning", "text", "tools"])
        self.assertEqual({k: v for k, v in d2.items() if k not in ("text", "reasoning")}, d1)

    def test_the_observations_a_judge_needs_are_read_from_the_saved_files(self):
        report, _, _ = self.run_probe()
        seen = report["observations"]
        self.assertEqual((seen["model_observed"], seen["model_matches_registered"], seen["service_tier_observed"]), (api.MODEL, True, "default"))
        self.assertEqual(seen["generation_cap"], {"requested": 16, "status": "incomplete", "incomplete_reason": "max_output_tokens",
                                                  "output_tokens": 16, "reasoning_tokens": None, "rejected": None, "enforced": True})
        self.assertEqual(seen["stateless"], {"requests_clean": True, "responses_clean": True, "b_independent_of_a": True})
        self.assertEqual(seen["pre_count"], {"adapter_fields": 1000, "with_text_format_and_reasoning": 1350, "extra_fields_rejected": None,
                                             "actual_input_tokens_A": 1200, "actual_minus_adapter_pre_count": 200})
        candidate = report["candidate_interface"]
        self.assertEqual(candidate, {"status": "PROBE_EVIDENCE_PENDING_JUDGE", "mode": "SIMULATION", "model_observed": api.MODEL,
                                     "effort_requested": api.EFFORT, "generation_cap_verified": True, "stateless_isolation_verified": True,
                                     "usage_accounting_verified": True, "cost_basis": json.loads(self.registration.read_text())["cost_basis"],
                                     "instruction_sha256": ep.sha256_file(self.pack)})

    def test_the_probe_never_declares_the_interface_ready_and_a_simulation_never_claims_live(self):
        for name, kwargs in (("ok", {}), ("nocap", {"enforce_output_cap": False}), ("wrong", {"faults": [self.rule("wrong_model")]})):
            report, _, _ = self.run_probe(name, **kwargs)
            self.assertEqual(report["candidate_interface"]["status"], "PROBE_EVIDENCE_PENDING_JUDGE", name)
            self.assertEqual(report["candidate_interface"]["mode"], "SIMULATION", name)
            if name != "wrong":  # the wrong-model run stops before B; A's receipt never exists
                for step in "AB":
                    receipt = self.ledger(name)["steps"][step].get("receipt") or {}
                    self.assertEqual(receipt.get("mode"), "SIMULATED_TRANSPORT", (name, step))

    def test_the_stored_responses_are_exactly_what_the_server_returned(self):
        received, sim = [], self.sim()

        def tee(path, body):
            response = sim(path, body)
            received.append(response)
            return response

        tee.is_simulated = True
        self.run_probe(transport=tee)
        records = [r for sid in "ABCD" for r in self.ledger()["steps"][sid]["requests"]]
        self.assertEqual(len(records), len(received))
        for record, response in zip(records, received):
            self.assertEqual((self.work / "probe" / record["response_file"]).read_text(), json.dumps(response), record["response_file"])

    def test_a_server_that_ignores_the_cap_is_reported_as_not_enforcing_it(self):
        report, _, _ = self.run_probe(enforce_output_cap=False)
        self.assertEqual(report["ledger_state"], "COMPLETE")
        self.assertIs(report["candidate_interface"]["generation_cap_verified"], False)
        self.assertEqual(report["observations"]["generation_cap"]["status"], "completed")
        self.assertIs(report["observations"]["generation_cap"]["enforced"], False)

    def test_the_two_experiment_rejections_are_outcomes_never_retried(self):
        # A's generation is occurrence 1 of the generation path for this fixture; C's is occurrence 2
        report, _, sim = self.run_probe("cap", faults=[{"path": "responses", "item_id": pb.FIXTURE_A, "occurrence": 2, "fault": "http_400"}])
        ledger = self.ledger("cap")
        self.assertEqual(ledger["steps"]["C"]["state"], "SETTLED_WITH_REJECTION")
        self.assertEqual(ledger["steps"]["C"]["requests"][0]["state"], "REJECTED")
        self.assertEqual(ledger["steps"]["D"]["state"], "SETTLED")  # the probe went on
        self.assertEqual(len([r for r in sim.requests if r["path"] == "responses" and r["item_id"] == pb.FIXTURE_A]), 2)  # A once, C once: no retry
        self.assertEqual(report["observations"]["generation_cap"]["rejected"]["http_status"], 400)
        self.assertIs(report["candidate_interface"]["generation_cap_verified"], False)
        self.assertEqual(Decimal(ledger["spent_upper_usd"]), HAPPY_SPENT - Decimal("0.0003192") + Decimal("0.55"))  # unknown cost: the reservation
        report, _, sim = self.run_probe("extras", reject_count_extras=True)
        ledger = self.ledger("extras")
        self.assertEqual((ledger["steps"]["D"]["state"], ledger["steps"]["D"]["requests"][1]["state"]), ("SETTLED_WITH_REJECTION", "REJECTED"))
        self.assertEqual(len([r for r in sim.requests if r["path"] == "responses/input_tokens"]), 4)  # A, B, D1, D2 once each
        self.assertEqual(report["observations"]["pre_count"]["extra_fields_rejected"]["http_status"], 400)
        self.assertEqual(report["ledger_state"], "COMPLETE")

    def test_a_rejection_anywhere_else_stops_the_probe(self):
        for name, rule in (("a-gen", {"path": "responses", "item_id": pb.FIXTURE_A, "occurrence": 1, "fault": "http_400"}),
                           ("a-count", {"path": "responses/input_tokens", "item_id": pb.FIXTURE_A, "occurrence": 1, "fault": "http_401"}),
                           ("d1", {"path": "responses/input_tokens", "item_id": pb.FIXTURE_A, "occurrence": 2, "fault": "http_400"})):
            report, _, _ = self.run_probe(name, faults=[rule])
            self.assertEqual(report["ledger_state"], "STOPPED", name)
            self.assertEqual(report["stop"]["reason"], "HTTP_ERROR", name)
        ledger = self.ledger("a-gen")
        self.assertEqual({sid: s["state"] for sid, s in ledger["steps"].items()},
                         {"A": "STOPPED", "B": "PLANNED", "C": "PLANNED", "D": "PLANNED"})

    def test_a_failure_in_the_full_path_stops_the_probe_and_keeps_what_was_sent(self):
        report, _, sim = self.run_probe(faults=[self.rule("timeout")])
        ledger = self.ledger()
        self.assertEqual((report["ledger_state"], report["stop"]["step"], report["stop"]["reason"]), ("STOPPED", "A", "TimeoutError"))
        self.assertEqual(len(sim.requests), 2)  # the count and the generation: never a third, never a resend
        self.assertEqual([r["state"] for r in ledger["steps"]["A"]["requests"]], ["RECORDED", "ERROR"])
        self.assertEqual(Decimal(ledger["spent_upper_usd"]), Decimal("0.05") + Decimal("0.55"))  # unknown cost: the reservation, never zero
        self.assertEqual([ledger["steps"][s]["state"] for s in "BCD"], ["PLANNED"] * 3)  # not started, distinguishable from unknown
        self.assertEqual(report["accounting"]["unknown_requests"], 0)

    def test_a_wrong_model_is_recorded_verbatim_and_stops_the_probe(self):
        report, _, _ = self.run_probe(faults=[self.rule("wrong_model")])
        ledger = self.ledger()
        seen = report["observations"]
        self.assertEqual((report["stop"]["reason"], seen["model_observed"], seen["model_matches_registered"]), ("CAPABILITY", "gpt-6-luna", False))
        self.assertEqual(json.loads((self.work / "probe" / "responses" / "A-2.response.json").read_text())["model"], "gpt-6-luna")
        self.assertEqual(ledger["steps"]["A"]["requests"][1]["cost_upper_usd"], "0.000396")  # charged from the response's own usage
        self.assertIs(report["candidate_interface"]["usage_accounting_verified"], False)

    def test_a_response_without_usage_is_charged_its_reservation_and_stops(self):
        report, _, _ = self.run_probe(faults=[self.rule("no_usage")])
        record = self.ledger()["steps"]["A"]["requests"][1]
        self.assertEqual((report["ledger_state"], record["state"], record["cost_upper_usd"]), ("STOPPED", "RECORDED", "0.55"))
        self.assertIs(report["candidate_interface"]["usage_accounting_verified"], False)

    def test_the_limits_hold_even_when_the_code_is_driven_off_script(self):
        def driven(name):
            probe, sim = self.probe(name)
            probe._wire()
            (self.work / name).mkdir(parents=True)
            for sub in ("requests", "responses"):
                (self.work / name / sub).mkdir()
            probe.ledger = {"mode": "SIMULATION", "cap_usd": str(probe.cap), "reserved_usd": "0", "spent_upper_usd": "0", "requests_sent": 0,
                            "generation_sent": 0, "state": "RUNNING", "stop": None,
                            "steps": {s["id"]: {"state": "PLANNED", "requests": []} for s in probe.registration["steps"]}}
            return probe, sim, probe.registration["steps"][0], api.build_request(self.public[pb.FIXTURE_A], self.instructions)

        probe, sim, step, body = driven("path")
        with self.assertRaises(pb.ProbeStop) as caught:
            probe.send(step, "responses", body)  # A's first request must be the input count
        self.assertEqual(caught.exception.reason, "UNREGISTERED_REQUEST")
        probe, sim, step, body = driven("extra")
        probe.send(step, "responses/input_tokens", body)
        probe.send(step, "responses", body)
        with self.assertRaises(pb.ProbeStop) as caught:
            probe.send(step, "responses", body)  # a third request in a two-request step
        self.assertEqual(caught.exception.reason, "UNREGISTERED_REQUEST")
        self.assertEqual(len(sim.requests), 2)
        for field, value, reason in (("max_requests", 1, "REQUEST_BUDGET_EXHAUSTED"), ("max_generation_requests", 0, "REQUEST_BUDGET_EXHAUSTED")):
            probe, sim, step, body = driven(f"lim-{field}")
            probe.registration[field] = value
            probe.send(step, "responses/input_tokens", body)
            with self.assertRaises(pb.ProbeStop) as caught:
                probe.send(step, "responses", body)
            self.assertEqual(caught.exception.reason, reason, field)
        probe, sim, step, body = driven("cap")
        probe.cap = Decimal("0.50")  # below one generation's reservation after the count's
        probe.send(step, "responses/input_tokens", body)
        with self.assertRaises(pb.ProbeStop) as caught:
            probe.send(step, "responses", body)
        self.assertEqual(caught.exception.reason, "RESERVATION_CAP")
        self.assertEqual(len(sim.requests), 1)  # refused before anything was sent

    def test_the_probe_is_single_shot_and_the_report_is_a_pure_function_of_the_saved_files(self):
        report, probe, sim = self.run_probe()
        self.assertEqual(pb.analyse(self.work / "probe"), report)
        self.assertEqual(json.loads((self.work / "probe" / "probe_report.json").read_text()), report)
        again, sim2 = self.probe("probe")
        self.assertBlocks("PROBE_ALREADY_STARTED", again.run)  # not merely a later FileExistsError from the registration copy
        self.assertEqual(sim2.requests, [])
        listing = json.loads((self.work / "probe" / "write_accounting.json").read_text())
        on_disk = sorted(str(p.relative_to(self.work / "probe")) for p in (self.work / "probe").rglob("*") if p.is_file() and p.name != "write_accounting.json")
        self.assertEqual(sorted(f["path"] for f in listing["files"]), on_disk)
        self.assertEqual(listing["writes_outside_out_dir"], [])

    def test_wiring_errors_surface_before_anything_is_written(self):
        simulated, _ = self.probe("nosim", transport=None)
        with self.assertRaisesRegex(pb.ProbeBlocked, "SIMULATION_NEEDS_A_SIMULATED_TRANSPORT"):
            simulated.run()
        wrong = pb.Probe(self.work / "wronginstr", self.registration, mode="SIMULATION", instructions="another pack", public=self.public,
                         transport=self.sim())
        with self.assertRaisesRegex(pb.ProbeBlocked, "INSTRUCTIONS_DO_NOT_MATCH_THE_REGISTRATION"):
            wrong.run()
        injected, _ = self.probe("liveinj", mode="LIVE")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": KEY}), self.assertRaisesRegex(pb.ProbeBlocked, "LIVE_FORBIDS_AN_INJECTED_TRANSPORT"):
            injected.run()
        nokey, _ = self.probe("nokey", mode="LIVE", transport=None)
        with mock.patch.dict(os.environ):
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaisesRegex(pb.ProbeBlocked, "LIVE_KEY_MISSING"):
                nokey.run()
        for name in ("nosim", "wronginstr", "liveinj", "nokey"):
            self.assertFalse((self.work / name).exists(), name)

    def test_a_second_probe_cannot_run_in_a_directory_another_holds(self):
        # Another process can hold the directory only between the emptiness check and the lock, so the contention is simulated
        # at the one call that decides it: an exclusive non-blocking lock must be requested, losing it must block the probe
        # before anything is written or sent, and the descriptor opened for the lock must not be leaked.
        probe, sim = self.probe("held")
        operations = []

        def contended(descriptor, operation):
            operations.append(operation)
            raise BlockingIOError(11, "Resource temporarily unavailable")

        with mock.patch.object(pb.fcntl, "flock", side_effect=contended):
            with self.assertRaisesRegex(pb.ProbeBlocked, "ANOTHER_PROBE_HOLDS_THIS_DIRECTORY"):
                probe.run()
        self.assertEqual(operations, [fcntl.LOCK_EX | fcntl.LOCK_NB])
        self.assertIsNone(probe._lock_fd)
        self.assertEqual(sim.requests, [])
        self.assertFalse((self.work / "held" / "probe_ledger.json").exists())
        self.assertFalse((self.work / "held" / "registration.json").exists())


CHILD = r'''
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import _lab
import exploration_runner as xr
import probe_2b as pb
from simulated_transport import SimulatedResponses
registration = pb.load_registration(sys.argv[2])
_, public, labels = xr.load_bank(registration["engineering_manifest"]["ref"])
instructions = Path(registration["instruction"]["ref"]).read_text(encoding="utf-8")
sim = SimulatedResponses(public, answer_for=lambda item: labels[item["item_id"]]["acceptable"][0],
                         faults=json.loads(sys.argv[4]) if len(sys.argv) > 4
                         else [{"path": "responses", "item_id": "eng-resume-dead-child", "occurrence": 1, "fault": "crash"}],
                         log_path=Path(sys.argv[3]) / "server.jsonl")
pb.Probe(Path(sys.argv[3]) / "probe", sys.argv[2], mode="SIMULATION", instructions=instructions, public=public, transport=sim).run()
'''


class CrashTests(ProbeCase):
    def test_a_kill_during_the_last_request_defeats_only_the_accounting_claim(self):
        fault = [{"path": "responses/input_tokens", "item_id": pb.FIXTURE_A, "occurrence": 3, "fault": "crash"}]  # D's second count
        child = subprocess.run([sys.executable, "-B", "-c", CHILD, str(HERE), str(self.registration), str(self.work), json.dumps(fault)],
                               capture_output=True, text=True, cwd=str(HERE), env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=180)
        self.assertEqual(child.returncode, -9, child.stderr[-300:])
        self.assertEqual([r["state"] for r in self.ledger()["steps"]["D"]["requests"]], ["RECORDED", "INTENT"])
        view = pb.analyse(self.work / "probe")
        self.assertEqual(view["accounting"]["unknown_requests"], 1)
        candidate = view["candidate_interface"]
        self.assertIs(candidate["usage_accounting_verified"], False)  # a request with no recorded outcome has no verified cost
        self.assertIs(candidate["generation_cap_verified"], True)  # the saved evidence behind the other claims still stands
        self.assertIs(candidate["stateless_isolation_verified"], True)

    def test_a_killed_probe_leaves_an_intent_that_recover_marks_unknown_and_nothing_is_resent(self):
        child = subprocess.run([sys.executable, "-B", "-c", CHILD, str(HERE), str(self.registration), str(self.work)], capture_output=True,
                               text=True, cwd=str(HERE), env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=180)
        self.assertEqual(child.returncode, -9, child.stderr[-300:])  # SIGKILL in the middle of A's generation request
        ledger = self.ledger()
        self.assertEqual(ledger["steps"]["A"]["state"], "STARTED")
        self.assertEqual([r["state"] for r in ledger["steps"]["A"]["requests"]], ["RECORDED", "INTENT"])  # the intent was durable
        self.assertEqual([ledger["steps"][s]["state"] for s in "BCD"], ["PLANNED"] * 3)  # not started: distinguishable from unknown
        view = pb.analyse(self.work / "probe")
        self.assertEqual([r["state"] for r in view["steps"]["A"]["requests"]], ["RECORDED", "UNKNOWN"])  # started, outcome unknown
        self.assertEqual(view["accounting"]["unknown_requests"], 1)
        self.assertIs(view["candidate_interface"]["usage_accounting_verified"], False)
        self.assertEqual(pb.recover(self.work / "probe"), ["A:2"])
        ledger = self.ledger()
        self.assertEqual((ledger["state"], ledger["stop"]["reason"], ledger["steps"]["A"]["state"]), ("STOPPED", "RECOVERED_UNKNOWN", "UNKNOWN_AFTER_INTENT"))
        self.assertEqual(ledger["steps"]["A"]["requests"][1]["state"], "UNKNOWN")
        self.assertEqual(Decimal(ledger["spent_upper_usd"]), Decimal("0.05") + Decimal("0.55"))  # unknown is charged its reservation
        self.assertEqual(pb.recover(self.work / "probe"), [])  # idempotent
        again, sim = self.probe("probe")
        with self.assertRaisesRegex(pb.ProbeBlocked, "PROBE_ALREADY_STARTED"):
            again.run()
        self.assertEqual(sim.requests, [])
        server = [json.loads(line) for line in (self.work / "server.jsonl").read_text().splitlines()]
        self.assertEqual([(r["path"], r["item_id"]) for r in server], [("responses/input_tokens", pb.FIXTURE_A), ("responses", pb.FIXTURE_A)])


class GateTests(ProbeCase):
    ENV = {"OPENAI_API_KEY": KEY}

    def blocked(self, **kwargs) -> str:
        with self.assertRaises(pb.ProbeBlocked) as caught:
            pb.probe_gate(self.registration, **kwargs)
        return str(caught.exception)

    def passes(self, **kwargs) -> dict:
        try:
            return pb.probe_gate(self.registration, **kwargs)
        except pb.ProbeBlocked as blocked:
            self.fail(f"the gate blocked a probe that should run: {blocked}")

    def test_simulation_needs_no_approval_and_no_key(self):
        self.assertEqual(self.passes(mode="SIMULATION", env={})["cap_usd"], "2.00")

    def test_live_needs_an_approval_bound_to_this_registration_a_key_and_the_egress_consent(self):
        self.assertIn("APPROVAL_MISSING_OR_INVALID", self.blocked(mode="LIVE", env=self.ENV))
        self.assertIn("LIVE_KEY_MISSING", self.blocked(mode="LIVE", approval_file=self.approval(), env={}))
        self.assertEqual(self.passes(mode="LIVE", approval_file=self.approval(), env=self.ENV)["cap_usd"], "2.00")
        for changes, reason in (({"approved": False}, "APPROVAL_NOT_GRANTED"), ({"scope": "FORMAL_EXPLORATION_R1"}, "APPROVAL_SCOPE_MISMATCH"),
                                ({"registration_sha256": "0" * 64}, "APPROVAL_NOT_FOR_THIS_REGISTRATION"),
                                ({"max_total_reservation_usd": "1.99"}, "APPROVAL_CAP_BELOW_REQUIRED"),
                                ({"data_egress": " "}, "APPROVAL_FIELD_MISSING:data_egress"),
                                ({"approved_by": None}, "APPROVAL_FIELD_MISSING:approved_by"),
                                ({"profile_sha256": ep.sha256_file(self.profile), "registration_sha256": None}, "APPROVAL_NOT_FOR_THIS_REGISTRATION")):
            self.assertIn(reason, self.blocked(mode="LIVE", approval_file=self.approval(**changes), env=self.ENV), changes)

    def test_an_artifact_changed_after_registration_blocks_the_probe(self):
        for path, name in ((self.profile, "profile"), (self.eng, "engineering_manifest")):
            original = path.read_bytes()
            try:
                path.write_bytes(original + b" ")
                self.assertIn(f"ARTIFACT_CHANGED:{name}", self.blocked(mode="SIMULATION", env={}))
            finally:
                path.write_bytes(original)
        self.assertEqual(self.passes(mode="SIMULATION", env={})["cap_usd"], "2.00")

    def test_the_runner_code_must_still_be_the_code_the_registration_was_made_for(self):
        original = json.loads(self.registration.read_text())

        def message(record) -> str:
            path = self.work / "other-code.json"
            path.write_text(json.dumps(record))
            with self.assertRaises(pb.ProbeBlocked) as caught:
                pb.probe_gate(path, mode="SIMULATION", env={})
            return str(caught.exception)

        for name in ep.RUNNER_FILES:  # the profile still matches this code, so only the registration's own pin can notice
            altered = copy.deepcopy(original)
            altered["runner_sources"][name] = "0" * 64
            self.assertEqual(message(altered), f"RUNNER_SOURCE_DRIFT:{name}")
        for broken in (None, {}, {"probe_2b.py": original["runner_sources"]["probe_2b.py"]}):
            self.assertEqual(message({**original, "runner_sources": broken}), "RUNNER_SOURCE_PIN_MALFORMED")

    def test_the_command_line_blocks_a_live_probe_without_approval_and_writes_nothing(self):
        out = self.work / "cli-live"
        buffer = io.StringIO()
        with mock.patch.dict(os.environ), contextlib.redirect_stdout(buffer):
            os.environ.pop("OPENAI_API_KEY", None)
            code = pb.main(["run", "--registration", str(self.registration), "--out", str(out), "--mode", "LIVE"])
        result = json.loads(buffer.getvalue())
        self.assertEqual((code, result["state"], result["generation_calls"]), (2, "BLOCKED", 0))
        self.assertIn("APPROVAL_MISSING_OR_INVALID", result["reasons"])
        self.assertIn("LIVE_KEY_MISSING", result["reasons"])
        self.assertFalse(out.exists())

    def test_the_command_line_runs_the_simulation_and_reports_it_as_simulated(self):
        out = self.work / "cli-sim"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = pb.main(["run", "--registration", str(self.registration), "--out", str(out), "--mode", "SIMULATION"])
        result = json.loads(buffer.getvalue())
        self.assertEqual((code, result["state"], result["requests_are_simulated"]), (0, "COMPLETE", True))
        self.assertEqual(result["candidate_interface"]["status"], "PROBE_EVIDENCE_PENDING_JUDGE")
        self.assertEqual(len((out / "simulated_server_log.jsonl").read_text().splitlines()), 7)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(pb.main(["report", "--out", str(out)]), 0)
        self.assertEqual(json.loads(buffer.getvalue()), pb.analyse(out))

    def test_the_command_line_exits_with_its_own_code_when_the_probe_stops(self):
        out = self.work / "cli-stop"
        buffer = io.StringIO()
        real = pb.SimulatedResponses
        faulty = lambda *args, **kwargs: real(*args, faults=[self.rule("http_400")], **kwargs)  # noqa: E731
        with mock.patch.object(pb, "SimulatedResponses", faulty), contextlib.redirect_stdout(buffer):
            code = pb.main(["run", "--registration", str(self.registration), "--out", str(out), "--mode", "SIMULATION"])
        result = json.loads(buffer.getvalue())
        self.assertEqual((code, result["state"]), (3, "STOPPED"))
        self.assertEqual(result["stop"]["reason"], "HTTP_ERROR")

    def test_a_live_probe_uses_the_adapters_wire_code_without_the_network_and_keeps_the_key_out_of_every_file(self):
        fake = self.sim(log_path=self.work / "live-server.jsonl")
        probe = pb.Probe(self.work / "live", self.registration, mode="LIVE", instructions=self.instructions, public=self.public)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": KEY}), \
                mock.patch.object(api.ResponsesAdapter, "_post", lambda adapter, path, body: fake(path, body)):
            report = probe.run()
        self.assertEqual(report["ledger_state"], "COMPLETE")
        self.assertEqual(len(fake.requests), 7)
        self.assertEqual(self.ledger("live")["steps"]["A"]["receipt"]["mode"], "LIVE")
        self.assertEqual(report["candidate_interface"]["status"], "PROBE_EVIDENCE_PENDING_JUDGE")  # even a live pass is not a verdict
        for path in (self.work / "live").rglob("*"):
            if path.is_file():
                self.assertNotIn(KEY, path.read_text(), path.name)


class AnalysisGuardTests(ProbeCase):
    """The report must read the evidence, not trust it: each guard is exercised by a server that lies in one specific way."""

    def lying(self, modify):
        sim = self.sim()

        def transport(path, body):
            response = sim(path, body)
            return modify(copy.deepcopy(response), body) if path == "responses" else response

        transport.is_simulated = True
        return transport

    def report(self, modify, name="lie"):
        report, _, _ = self.run_probe(name, transport=self.lying(modify))
        return report

    def test_a_response_that_reports_store_true_or_a_previous_response_is_not_stateless(self):
        for field, value in (("store", True), ("previous_response_id", "resp_earlier")):
            report = self.report(lambda r, b, f=field, v=value: {**r, f: v}, f"lie-{field}")
            self.assertEqual(report["ledger_state"], "COMPLETE", field)
            self.assertEqual(report["observations"]["stateless"]["responses_clean"], False, field)
            self.assertIs(report["candidate_interface"]["stateless_isolation_verified"], False, field)
        silent = self.report(lambda r, b: {k: v for k, v in r.items() if k != "store"}, "lie-silent")
        self.assertIs(silent["candidate_interface"]["stateless_isolation_verified"], False)  # a missing report is not a verification

    def test_the_second_request_must_not_depend_on_the_first(self):
        report = self.report(lambda r, b: {**r, "id": "resp_same"})
        self.assertIs(report["observations"]["stateless"]["b_independent_of_a"], False)
        self.assertIs(report["candidate_interface"]["stateless_isolation_verified"], False)

    def test_unusable_usage_on_the_cap_probe_defeats_the_accounting_claim(self):
        def bad_c(response, body):
            if body.get("max_output_tokens") == 16:
                response["usage"]["output_tokens"] = 16.0  # a float is not a token count
            return response

        report = self.report(bad_c)
        self.assertEqual(report["ledger_state"], "COMPLETE")
        self.assertIs(report["candidate_interface"]["usage_accounting_verified"], False)
        self.assertIs(report["candidate_interface"]["generation_cap_verified"], False)  # an unreadable count proves no cap either

    def test_a_second_request_that_quotes_the_first_response_id_is_not_independent(self):
        real = api.build_request

        def quoting(item, instructions):
            body = real(item, instructions)
            return {**body, "metadata": {"note": "continues resp_A_fixed"}} if item["item_id"] == pb.FIXTURE_B else body

        def name_it(response, body):
            if json.loads(body["input"])["item_id"] == pb.FIXTURE_A and body.get("max_output_tokens") == api.OUTPUT_LIMIT:
                response["id"] = "resp_A_fixed"  # the ids differ, so only the quotation shows the dependence
            return response

        with mock.patch.object(api, "build_request", quoting):
            report = self.report(name_it, "quote")
        self.assertEqual(report["ledger_state"], "COMPLETE")
        self.assertIs(report["observations"]["stateless"]["b_independent_of_a"], False)
        self.assertIs(report["candidate_interface"]["stateless_isolation_verified"], False)

    def test_a_cap_probe_that_did_not_end_as_incomplete_for_the_cap_does_not_prove_the_cap(self):
        for name, change in (("completed", lambda r: {**r, "status": "completed"}),
                             ("other-reason", lambda r: {**r, "incomplete_details": {"reason": "content_filter"}})):
            report = self.report(lambda r, b, change=change: change(r) if b.get("max_output_tokens") == 16 else r, f"cap-{name}")
            self.assertEqual(report["ledger_state"], "COMPLETE", name)
            self.assertIs(report["observations"]["generation_cap"]["enforced"], False, name)
            self.assertIs(report["candidate_interface"]["generation_cap_verified"], False, name)

    def test_a_ledger_whose_books_do_not_balance_or_that_exceeds_its_cap_defeats_the_accounting_claim(self):
        self.run_probe("books")
        out = self.work / "books"
        original = (out / "probe_ledger.json").read_text()
        self.assertIs(pb.analyse(out)["candidate_interface"]["usage_accounting_verified"], True)
        for field, value in (("spent_upper_usd", "9.99"), ("cap_usd", "0.10")):
            tampered = json.loads(original)
            tampered[field] = value
            (out / "probe_ledger.json").write_text(json.dumps(tampered))
            try:
                self.assertIs(pb.analyse(out)["candidate_interface"]["usage_accounting_verified"], False, field)
            finally:
                (out / "probe_ledger.json").write_text(original)

    def test_a_server_that_announces_the_cap_and_overshoots_it_does_not_enforce_it(self):
        report, _, _ = self.run_probe("overshoot", faults=[{"path": "responses", "item_id": pb.FIXTURE_A, "occurrence": 2, "fault": "cap_overshoot"}])
        self.assertEqual(report["observations"]["generation_cap"]["output_tokens"], 21)
        self.assertIs(report["candidate_interface"]["generation_cap_verified"], False)

    def test_a_server_error_on_the_cap_request_is_not_a_definite_rejection_and_stops_the_probe(self):
        report, _, sim = self.run_probe("c500", faults=[{"path": "responses", "item_id": pb.FIXTURE_A, "occurrence": 2, "fault": "http_500"}])
        ledger = self.ledger("c500")
        self.assertEqual(report["ledger_state"], "STOPPED")
        self.assertEqual((report["stop"]["step"], report["stop"]["reason"]), ("C", "HTTP_ERROR"))
        self.assertEqual(ledger["steps"]["C"]["requests"][0]["state"], "ERROR")  # not REJECTED: a 5xx proves nothing about the cap
        self.assertEqual(ledger["steps"]["D"]["state"], "PLANNED")

    def test_a_saved_file_that_no_longer_matches_the_ledger_voids_every_claim(self):
        self.run_probe("tamper")
        out = self.work / "tamper"
        self.assertIs(pb.analyse(out)["integrity"]["files_match_recorded_hashes"], True)
        for name in ("responses/A-2.response.json", "requests/B-2.request.json"):
            path = out / name
            original = path.read_bytes()
            try:
                path.write_bytes(original + b" ")
                report = pb.analyse(out)
                self.assertIs(report["integrity"]["files_match_recorded_hashes"], False, name)
                for field in ("generation_cap_verified", "stateless_isolation_verified", "usage_accounting_verified"):
                    self.assertIs(report["candidate_interface"][field], False, (name, field))
            finally:
                path.write_bytes(original)
        path = out / "responses" / "C-1.response.json"
        saved = path.read_bytes()
        try:
            path.unlink()
            self.assertIs(pb.analyse(out)["integrity"]["files_match_recorded_hashes"], False)  # a missing file is a mismatch too
        finally:
            path.write_bytes(saved)
        self.assertIs(pb.analyse(out)["candidate_interface"]["usage_accounting_verified"], True)

    def test_a_profile_that_fails_its_own_identity_check_blocks_the_probe(self):
        from test_execution_profile import World
        with tempfile.TemporaryDirectory() as tmp:
            world = World(tmp, bank_pack_sha="f" * 64)
            profile = world.save()
            registration = Path(tmp) / "registration.json"
            xr.exclusive_write(registration, xr.canonical(pb.build_registration(profile, self.eng)))
            with self.assertRaisesRegex(pb.ProbeBlocked, "PACK_BANK_MISMATCH"):
                pb.probe_gate(registration, mode="SIMULATION", env={})


class ClientStatelessnessTests(ProbeCase):
    def test_a_request_body_that_carries_state_is_not_stateless_even_when_the_server_is_clean(self):
        real = api.build_request
        for key, value in (("previous_response_id", "resp_earlier"), ("conversation", "conv_1"), ("store", True), ("tools", [{"type": "function"}])):
            def tainted(item, instructions, key=key, value=value):
                return {**real(item, instructions), key: value}

            with mock.patch.object(api, "build_request", tainted):
                report, _, _ = self.run_probe(f"tainted-{key}")
            self.assertEqual(report["observations"]["stateless"]["requests_clean"], False, key)
            self.assertIs(report["candidate_interface"]["stateless_isolation_verified"], False, key)
        clean, _, _ = self.run_probe("clean-again")
        self.assertIs(clean["candidate_interface"]["stateless_isolation_verified"], True)


class SimulationBoundaryTests(ProbeCase):
    def test_unmarked_transport_and_live_post_are_blocked_before_any_probe_write(self):
        for index, transport in enumerate((lambda path, body: {}, api.ResponsesAdapter(self.instructions)._post)):
            out = self.work / ("unmarked-" + str(index))
            probe = pb.Probe(out, self.registration, mode="SIMULATION", instructions=self.instructions,
                             public=self.public, transport=transport)
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-NOT-A-REAL-KEY"}):
                self.assertBlocks("SIMULATION_TRANSPORT_NOT_SIMULATED", probe.run)
            self.assertFalse(out.exists())
            self.assertEqual(urllib.request.urlopen.call_count, 0)
            self.assertEqual(socket.socket.connect.call_count, 0)
            print("REPAIR_OBSERVATION " + json.dumps({"case": "K", "caller": "Probe", "transport": index,
                  "new_requests": 0, "network_calls": 0, "writes": 0, "outcome": "BLOCKED"}))


if __name__ == "__main__":
    unittest.main()
