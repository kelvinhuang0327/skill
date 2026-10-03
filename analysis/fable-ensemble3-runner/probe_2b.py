#!/usr/bin/env python3
"""Stage 2B probe, offline implementation: registered probes A-D, an independent ledger, exact request and response records.

    python3 probe_2b.py register --profile P --engineering-bank DIR --out REGISTRATION.json
    python3 probe_2b.py run      --registration R --out DIR --mode SIMULATION
    python3 probe_2b.py run      --registration R --out DIR --mode LIVE --approval-file F     (blocked until every gate holds)
    python3 probe_2b.py recover  --out DIR
    python3 probe_2b.py report   --out DIR

The probe is single-shot with ZERO retries: no request is ever sent twice.  A definite 4xx answer to the two requests that
are themselves experiments (C: the smallest generation cap; D2: the pre-count with extra fields) is recorded as the
outcome and the probe moves on; every other failure stops it, and what was sent and charged so far is kept.  A request
that was sent and has no recorded outcome is UNKNOWN, never repeated.  The module only records evidence; it never decides
BENCHMARK_READY.  This file is a tool: running it LIVE spends money and needs the Owner's written approval, a key in the
environment, and the registration the approval is bound to.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import urllib.error
from decimal import Decimal
from pathlib import Path
from typing import Callable, Dict, List, Optional

import _lab  # noqa: F401
import build_engineering_bank as eb
import decision_engine as engine
import execution_profile as ep
import exploration_runner as xr
import responses_adapter as api
from simulated_transport import SimulatedResponses

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REGISTRATION_ARTIFACT = "FABLE_ENSEMBLE3_STAGE2B_PROBE_REGISTRATION"
REPORT_ARTIFACT = "FABLE_ENSEMBLE3_STAGE2B_PROBE_REPORT"
APPROVAL_SCOPE = "STAGE2B_LIVE_PROBE"
MODES = ("SIMULATION", "LIVE")
FIXTURE_A, FIXTURE_B = "eng-resume-dead-child", "eng-foreign-writer"
CAP_TEST_TOKENS = 16
COUNT_RESERVE, GENERATION_RESERVE = "0.05", "0.55"
DEFAULT_CAP_USD = "2.00"


class ProbeBlocked(Exception):
    """The probe must not start; nothing was sent."""


class ProbeStop(Exception):
    """The probe stops here; everything sent so far is recorded."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


# --- registration ----------------------------------------------------------------------------------------------------
def step_definitions() -> List[dict]:
    count = {"path": "responses/input_tokens", "reserve_usd": COUNT_RESERVE, "rejection_is_an_outcome": False}
    generation = {"path": "responses", "reserve_usd": GENERATION_RESERVE, "rejection_is_an_outcome": False}
    return [
        {"id": "A", "kind": "ADAPTER_FULL_PATH", "item": FIXTURE_A, "requests": [dict(count), dict(generation)]},
        {"id": "B", "kind": "ADAPTER_FULL_PATH", "item": FIXTURE_B, "requests": [dict(count), dict(generation)]},
        {"id": "C", "kind": "GENERATION_CAP", "item": FIXTURE_A, "max_output_tokens": CAP_TEST_TOKENS,
         "requests": [{**generation, "rejection_is_an_outcome": True}]},
        {"id": "D", "kind": "PRECOUNT_SURFACE", "item": FIXTURE_A,
         "requests": [{**count, "fields": "ADAPTER"},
                      {**count, "fields": "ADAPTER_PLUS_TEXT_FORMAT_AND_REASONING", "rejection_is_an_outcome": True}]},
    ]


def build_registration(profile_path, engineering_manifest, cap_usd: str = DEFAULT_CAP_USD) -> dict:
    profile = engine.read_json(profile_path)
    manifest = engine.read_json(engineering_manifest)
    if manifest.get("purpose") != "ENGINEERING_ONLY" or {FIXTURE_A, FIXTURE_B} - {row["item_id"] for row in manifest["items"]}:
        raise ValueError("the probe uses two ENGINEERING_ONLY fixtures and never a formal item")
    steps = step_definitions()
    requests = [r for s in steps for r in s["requests"]]
    reserved = sum(Decimal(r["reserve_usd"]) for r in requests)
    if reserved > Decimal(cap_usd):
        raise ValueError(f"reservations {reserved} exceed the cap {cap_usd}")
    return {
        "artifact": REGISTRATION_ARTIFACT, "schema_version": 1, "status": "REGISTERED_NOT_APPROVED_NOT_EXECUTED",
        "model": profile["model"], "effort": profile["effort"], "cost_basis": profile["cost"]["basis"],
        "runner_sources": ep.runner_sources(),
        "profile": ep.pin(profile_path), "instruction": {"ref": profile["instruction"]["ref"], "sha256": profile["instruction"]["sha256"]},
        "adapter": profile["provider"]["adapter"], "engineering_manifest": ep.pin(engineering_manifest),
        "steps": steps, "max_requests": len(requests), "max_generation_requests": sum(r["path"] == "responses" for r in requests),
        "retries": 0, "total_reserved_usd": str(reserved), "cap_usd": cap_usd,
        "rules": {
            "zero_retry": "no request is sent twice; a failed or unknown request is recorded and never repeated",
            "rejection_outcomes": "a definite 4xx to C or D2 is the experiment's outcome: recorded, the step ends, the probe goes on; "
                                  "C is not repeated with another cap value (a new registration is needed for that)",
            "unknown": "a request with an INTENT and no recorded outcome is UNKNOWN and is charged its registered reservation",
            "approval": "LIVE needs a written Owner approval bound to this registration, a key in the environment, and data-egress consent",
            "declaration": "the probe writes evidence and a candidate interface with status PROBE_EVIDENCE_PENDING_JUDGE; only a Judge may mark BENCHMARK_READY",
        },
    }


def load_registration(path) -> dict:
    registration = engine.read_json(path)
    if registration.get("artifact") != REGISTRATION_ARTIFACT or registration.get("steps") != step_definitions():
        raise ProbeBlocked("REGISTRATION_STEPS_ALTERED")  # the steps are fixed by this code, not by an editable file
    requests = [r for s in registration["steps"] for r in s["requests"]]
    if (registration.get("retries") != 0 or registration.get("max_requests") != len(requests)
            or registration.get("max_generation_requests") != sum(r["path"] == "responses" for r in requests)):
        raise ProbeBlocked("REGISTRATION_LIMITS_ALTERED")
    if Decimal(registration["total_reserved_usd"]) != sum(Decimal(r["reserve_usd"]) for r in requests) \
            or Decimal(registration["total_reserved_usd"]) > Decimal(registration["cap_usd"]):
        raise ProbeBlocked("REGISTRATION_COST_ALTERED")
    return registration


def probe_gate(registration_path, *, mode: str, approval_file=None, env=None) -> dict:
    """Every reason the probe must not run in this mode; returns the loaded registration when there is none."""
    env = os.environ if env is None else env
    registration = load_registration(registration_path)
    reasons = []
    for name in ("profile", "adapter", "engineering_manifest", "instruction"):
        record = registration[name]
        if not Path(record["ref"]).is_file() or ep.sha256_file(record["ref"]) != record["sha256"]:
            reasons.append(f"ARTIFACT_CHANGED:{name}")
    reasons += ep.runner_drift(registration.get("runner_sources"))  # the code that will run is the code this registration was made for
    if not reasons:
        reasons += ep.check_profile(registration["profile"]["ref"], mode="SIMULATION")["reasons"]
    if mode == "LIVE":
        reasons += xr.validate_approval(approval_file, registration_path, scope=APPROVAL_SCOPE, total_required=Decimal(registration["cap_usd"]),
                                        bound_key="registration_sha256") if approval_file else ["APPROVAL_MISSING_OR_INVALID"]
        if not env.get("OPENAI_API_KEY"):  # presence only; the value is never read into any record
            reasons.append("LIVE_KEY_MISSING")
    if reasons:
        raise ProbeBlocked(",".join(sorted(set(reasons))))
    return registration


# --- the probe -------------------------------------------------------------------------------------------------------
class _StepTransport:
    def __init__(self, probe: "Probe", step: dict):
        self.probe, self.step = probe, step

    def __call__(self, path: str, body: dict) -> dict:
        return self.probe.send(self.step, path, body)


class Probe:
    def __init__(self, out_dir, registration_path, *, mode: str, instructions: str, public: Dict[str, dict],
                 transport: Optional[Callable] = None):
        if mode not in MODES:
            raise ProbeBlocked("MODE_INVALID")
        self.out, self.mode = Path(out_dir), mode
        self.registration_path = Path(registration_path)
        self.registration = load_registration(registration_path)
        self.instructions, self.public, self.transport = instructions, public, transport
        self.cap = Decimal(self.registration["cap_usd"])
        self.inner: Optional[Callable] = None
        self.ledger: dict = {}
        self._lock_fd: Optional[int] = None

    def _wire(self) -> None:
        if xr.sha256_text(self.instructions) != self.registration["instruction"]["sha256"]:
            raise ProbeBlocked("INSTRUCTIONS_DO_NOT_MATCH_THE_REGISTRATION")
        if self.mode == "SIMULATION":
            if self.transport is None:
                raise ProbeBlocked("SIMULATION_NEEDS_A_SIMULATED_TRANSPORT")
            self.inner = self.transport
        else:
            if self.transport is not None:
                raise ProbeBlocked("LIVE_FORBIDS_AN_INJECTED_TRANSPORT")
            live = api.ResponsesAdapter(self.instructions)
            try:
                live.preflight()
            except api.CapabilityBlocked as exc:
                raise ProbeBlocked(f"LIVE_KEY_MISSING: {exc}") from exc
            self.inner = live._post  # the adapter's own wire code; every request still passes through `send`

    def _save(self) -> None:
        xr.atomic_write(self.out / "probe_ledger.json", xr.canonical(self.ledger))

    def _spend(self, amount: Decimal) -> None:
        self.ledger["spent_upper_usd"] = str(Decimal(self.ledger["spent_upper_usd"]) + amount)

    def run(self) -> dict:
        if self.out.exists() and any(self.out.iterdir()):
            raise ProbeBlocked("PROBE_ALREADY_STARTED: a probe is single-shot; recover or report it")
        self._wire()
        for name in ("requests", "responses"):
            (self.out / name).mkdir(parents=True, exist_ok=True)
        self._lock_fd = os.open(self.out / "probe.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise ProbeBlocked("ANOTHER_PROBE_HOLDS_THIS_DIRECTORY")
        try:
            xr.exclusive_write(self.out / "registration.json", self.registration_path.read_text())  # the evidence stands alone
            self.ledger = {
                "artifact": "FABLE_ENSEMBLE3_STAGE2B_PROBE_LEDGER", "registration_sha256": ep.sha256_file(self.registration_path),
                "mode": self.mode, "cap_usd": str(self.cap), "reserved_usd": "0", "spent_upper_usd": "0", "requests_sent": 0,
                "generation_sent": 0, "state": "RUNNING", "stop": None,
                "steps": {step["id"]: {"state": "PLANNED", "requests": []} for step in self.registration["steps"]},
            }
            self._save()
            for step in self.registration["steps"]:
                try:
                    self._step(step)
                except ProbeStop as stop:
                    self.ledger["steps"][step["id"]]["state"] = "STOPPED"
                    self.ledger.update(state="STOPPED", stop={"step": step["id"], "reason": stop.reason, "detail": stop.detail})
                    self._save()
                    break
            else:
                self.ledger["state"] = "COMPLETE"
                self._save()
            report = analyse(self.out)
            xr.atomic_write(self.out / "probe_report.json", xr.canonical(report))
            files = [{"path": str(p.relative_to(self.out)), "bytes": p.stat().st_size, "sha256": ep.sha256_file(p)}
                     for p in sorted(self.out.rglob("*")) if p.is_file() and p.name != "write_accounting.json"]
            xr.atomic_write(self.out / "write_accounting.json", xr.canonical({"out_dir": str(self.out.resolve()), "files": files, "writes_outside_out_dir": []}))
            return report
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _step(self, step: dict) -> None:
        state = self.ledger["steps"][step["id"]]
        state["state"] = "STARTED"
        self._save()
        item = self.public[step["item"]]
        transport = _StepTransport(self, step)
        try:
            if step["kind"] == "ADAPTER_FULL_PATH":
                adapter = api.ResponsesAdapter(self.instructions, transport=transport)
                if self.mode == "LIVE":
                    adapter.live = True  # receipts say LIVE; the real wire code sits inside `self.inner`
                receipt = adapter.invoke(item)
                state["receipt"] = {key: receipt.get(key) for key in ("status", "observed_model", "provider_response_id", "usage",
                                                                       "estimated_usd", "cost_upper_usd", "instruction_sha256", "mode")}
            elif step["kind"] == "GENERATION_CAP":
                body = api.build_request(item, self.instructions)
                body["max_output_tokens"] = step["max_output_tokens"]
                transport("responses", body)
            else:  # PRECOUNT_SURFACE
                full = api.build_request(item, self.instructions)
                base = {key: full[key] for key in ("model", "instructions", "input", "tools")}
                transport("responses/input_tokens", base)
                transport("responses/input_tokens", {**base, "text": full["text"], "reasoning": full["reasoning"]})
        except ProbeStop:
            raise
        except urllib.error.HTTPError as exc:
            records = state["requests"]
            planned = step["requests"][len(records) - 1] if records else None
            if planned is not None and planned["rejection_is_an_outcome"] and 400 <= exc.code < 500:
                records[-1]["state"] = "REJECTED"  # the experiment's answer, kept; the probe moves on and never repeats it
                state["state"] = "SETTLED_WITH_REJECTION"
                self._save()
                return
            raise ProbeStop("HTTP_ERROR", str(exc.code)) from exc
        except api.CapabilityBlocked as exc:
            raise ProbeStop("CAPABILITY", str(exc)[:200]) from exc
        except Exception as exc:
            raise ProbeStop(type(exc).__name__, str(exc)[:200]) from exc
        state["state"] = "SETTLED"
        self._save()

    def send(self, step: dict, path: str, body: dict) -> dict:
        """The only way a request leaves.  The exact request and an INTENT are durable before it is sent; the exact
        response is durable before anything parses it; nothing is ever sent twice."""
        state = self.ledger["steps"][step["id"]]
        index = len(state["requests"])
        if index >= len(step["requests"]) or step["requests"][index]["path"] != path:
            raise ProbeStop("UNREGISTERED_REQUEST", f"{step['id']} request {index + 1} to {path}")
        reserve = Decimal(step["requests"][index]["reserve_usd"])
        if self.ledger["requests_sent"] >= self.registration["max_requests"] or (
                path == "responses" and self.ledger["generation_sent"] >= self.registration["max_generation_requests"]):
            raise ProbeStop("REQUEST_BUDGET_EXHAUSTED")
        if Decimal(self.ledger["reserved_usd"]) + reserve > self.cap:
            raise ProbeStop("RESERVATION_CAP")
        data = json.dumps(body)  # byte for byte what ResponsesAdapter._post puts on the wire
        name = f"{step['id']}-{index + 1}"
        xr.exclusive_write(self.out / "requests" / f"{name}.request.json", data)
        record = {"n": self.ledger["requests_sent"] + 1, "path": path, "state": "INTENT", "reserved_usd": str(reserve),
                  "request_file": f"requests/{name}.request.json", "request_sha256": xr.sha256_text(data), "t": time.time()}
        state["requests"].append(record)
        self.ledger["requests_sent"] += 1
        self.ledger["generation_sent"] += int(path == "responses")
        self.ledger["reserved_usd"] = str(Decimal(self.ledger["reserved_usd"]) + reserve)
        self._save()  # the INTENT is durable before the request leaves
        try:
            response = self.inner(path, body)
        except Exception as exc:  # a kill or Ctrl-C leaves the INTENT, which `recover` turns into UNKNOWN
            record.update(state="ERROR", error={"type": type(exc).__name__, "text": str(exc)[:300], "http_status": getattr(exc, "code", None)})
            self._spend(reserve)  # unknown cost is charged the registered reservation, never zero
            self._save()
            raise
        text = json.dumps(response)
        xr.exclusive_write(self.out / "responses" / f"{name}.response.json", text)  # the raw response, before anything reads it
        cost = reserve if path != "responses" else (xr.raw_upper_cost(response) or reserve)
        record.update(state="RECORDED", response_file=f"responses/{name}.response.json", response_sha256=xr.sha256_text(text), cost_upper_usd=str(cost))
        self._spend(cost)
        self._save()
        return response


def recover(out_dir) -> List[str]:
    """Turn every request that was sent and has no outcome into UNKNOWN, charged its reservation; nothing is re-sent."""
    out = Path(out_dir)
    ledger = json.loads((out / "probe_ledger.json").read_text())
    changed = []
    for step_id, step in ledger["steps"].items():
        for record in step["requests"]:
            if record["state"] == "INTENT":
                record["state"] = "UNKNOWN"
                ledger["spent_upper_usd"] = str(Decimal(ledger["spent_upper_usd"]) + Decimal(record["reserved_usd"]))
                step["state"] = "UNKNOWN_AFTER_INTENT"
                changed.append(f"{step_id}:{record['n']}")
    if changed:
        ledger.update(state="STOPPED", stop={"step": changed[0].split(":")[0], "reason": "RECOVERED_UNKNOWN", "detail": ",".join(changed)})
        xr.atomic_write(out / "probe_ledger.json", xr.canonical(ledger))
    return changed


# --- what the evidence says ------------------------------------------------------------------------------------------
def analyse(out_dir) -> dict:
    """The report, derived only from the persisted ledger, requests and responses.  Never declares BENCHMARK_READY."""
    out = Path(out_dir)
    ledger = json.loads((out / "probe_ledger.json").read_text())
    registration = json.loads((out / "registration.json").read_text())

    def record(step: str, n: int) -> Optional[dict]:
        records = ledger["steps"][step]["requests"]
        return records[n - 1] if n <= len(records) else None

    def stored(step: str, n: int, kind: str) -> Optional[dict]:
        entry = record(step, n)
        if entry is None or not entry.get(f"{kind}_file") or not (out / entry[f"{kind}_file"]).is_file():
            return None
        return json.loads((out / entry[f"{kind}_file"]).read_text())

    def intact(kind: str) -> bool:
        for step in ledger["steps"].values():
            for entry in step["requests"]:
                name = entry.get(f"{kind}_file")
                if name and (not (out / name).is_file() or xr.sha256_text((out / name).read_text(encoding="utf-8")) != entry[f"{kind}_sha256"]):
                    return False
        return True

    integrity = intact("request") and intact("response")
    a, b, c = stored("A", 2, "response"), stored("B", 2, "response"), stored("C", 1, "response")
    count_a, d1, d2 = stored("A", 1, "response"), stored("D", 1, "response"), stored("D", 2, "response")
    request_a, request_b = stored("A", 2, "request"), stored("B", 2, "request")

    def valid_usage(response) -> bool:
        try:
            api.usage_estimate(response["usage"])
            return True
        except (KeyError, TypeError, ValueError, api.CapabilityBlocked):
            return False

    observed_model = (a or {}).get("model")
    cap_requested = registration["steps"][2]["max_output_tokens"]
    output_tokens = ((c or {}).get("usage") or {}).get("output_tokens")
    cap_enforced = integrity and bool(c) and c.get("status") == "incomplete" and (c.get("incomplete_details") or {}).get("reason") == "max_output_tokens" \
        and isinstance(output_tokens, int) and output_tokens <= cap_requested
    c_record, d2_record = record("C", 1), record("D", 2)

    bodies = [json.loads((out / r["request_file"]).read_text()) for s in ("A", "B", "C") for r in ledger["steps"][s]["requests"]
              if r["path"] == "responses" and (out / r["request_file"]).is_file()]
    responses = [r for r in (a, b, c) if r is not None]
    requests_clean = bool(bodies) and all("previous_response_id" not in x and "conversation" not in x and x.get("tools") == []
                                           and x.get("store") is False for x in bodies)
    responses_clean = bool(responses) and all(r.get("store") is False and r.get("previous_response_id") in (None, "") for r in responses)
    independent = bool(a and b and request_b) and a.get("id") != b.get("id") and str(a.get("id")) not in json.dumps(request_b)
    stateless = bool(a and b) and requests_clean and responses_clean and independent and integrity

    charged = sum((Decimal(r["cost_upper_usd"]) if r["state"] == "RECORDED" else Decimal(r["reserved_usd"]))
                  for s in ledger["steps"].values() for r in s["requests"] if r["state"] != "INTENT")
    unknown = [r for s in ledger["steps"].values() for r in s["requests"] if r["state"] == "INTENT"]
    usage_ok = bool(a and b) and valid_usage(a) and valid_usage(b) and (c is None or valid_usage(c))
    accounting = usage_ok and not unknown and charged == Decimal(ledger["spent_upper_usd"]) and charged <= Decimal(ledger["cap_usd"]) and integrity

    actual_input = ((a or {}).get("usage") or {}).get("input_tokens")
    sent_instructions = (request_a or {}).get("instructions")
    report = {
        "artifact": REPORT_ARTIFACT, "mode": ledger["mode"], "registration_sha256": ledger["registration_sha256"],
        "ledger_state": ledger["state"], "stop": ledger["stop"], "integrity": {"files_match_recorded_hashes": integrity},
        "steps": {sid: {"state": step["state"], "requests": [{"n": r["n"], "path": r["path"], "state": "UNKNOWN" if r["state"] == "INTENT" else r["state"]}
                                                           for r in step["requests"]]} for sid, step in ledger["steps"].items()},
        "accounting": {"requests_sent": ledger["requests_sent"], "generation_sent": ledger["generation_sent"], "reserved_usd": ledger["reserved_usd"],
                       "spent_upper_usd": ledger["spent_upper_usd"], "cap_usd": ledger["cap_usd"], "unknown_requests": len(unknown)},
        "observations": {
            "model_observed": observed_model, "model_matches_registered": observed_model == registration["model"],
            "service_tier_observed": (a or {}).get("service_tier"),
            "generation_cap": {"requested": cap_requested, "status": (c or {}).get("status"), "incomplete_reason": ((c or {}).get("incomplete_details") or {}).get("reason"),
                               "output_tokens": output_tokens, "reasoning_tokens": (((c or {}).get("usage") or {}).get("output_tokens_details") or {}).get("reasoning_tokens"),
                               "rejected": None if not c_record or c_record["state"] != "REJECTED" else c_record.get("error"), "enforced": cap_enforced},
            "stateless": {"requests_clean": requests_clean, "responses_clean": responses_clean, "b_independent_of_a": independent},
            "pre_count": {"adapter_fields": (count_a or d1 or {}).get("input_tokens"),
                          "with_text_format_and_reasoning": None if d2 is None else d2.get("input_tokens"),
                          "extra_fields_rejected": None if not d2_record or d2_record["state"] != "REJECTED" else d2_record.get("error"),
                          "actual_input_tokens_A": actual_input,
                          "actual_minus_adapter_pre_count": None if actual_input is None or not count_a else actual_input - count_a.get("input_tokens", 0)},
        },
        "candidate_interface": {
            "status": "PROBE_EVIDENCE_PENDING_JUDGE", "mode": "LIVE" if ledger["mode"] == "LIVE" else "SIMULATION",
            "model_observed": observed_model, "effort_requested": ((request_a or {}).get("reasoning") or {}).get("effort"),
            "generation_cap_verified": cap_enforced, "stateless_isolation_verified": stateless, "usage_accounting_verified": accounting,
            "cost_basis": registration["cost_basis"],
            "instruction_sha256": None if sent_instructions is None else xr.sha256_text(sent_instructions),
        },
    }
    assert report["candidate_interface"]["status"] == "PROBE_EVIDENCE_PENDING_JUDGE"  # the probe never declares readiness
    return report


# --- command line ----------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    reg = commands.add_parser("register")
    reg.add_argument("--profile", required=True)
    reg.add_argument("--engineering-bank", required=True)
    reg.add_argument("--out", required=True)
    reg.add_argument("--cap-usd", default=DEFAULT_CAP_USD)
    run = commands.add_parser("run")
    run.add_argument("--registration", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--mode", required=True, choices=MODES)
    run.add_argument("--approval-file")
    for name in ("recover", "report"):
        commands.add_parser(name).add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "register":
            bank = Path(args.engineering_bank)
            if not (bank / "manifest.json").is_file():
                eb.build(bank)  # the sealed engineering fixtures, built once and pinned by the registration
            registration = build_registration(args.profile, bank / "manifest.json", args.cap_usd)
            xr.exclusive_write(Path(args.out), xr.canonical(registration))
            print(json.dumps({"registered": str(args.out), "sha256": ep.sha256_file(args.out), "status": registration["status"],
                              "total_reserved_usd": registration["total_reserved_usd"], "cap_usd": registration["cap_usd"]}, indent=2))
            return 0
        if args.command == "recover":
            print(json.dumps({"unknown_requests": recover(args.out)}, indent=2))
            return 0
        if args.command == "report":
            print(json.dumps(analyse(args.out), indent=2, sort_keys=True))
            return 0
        registration = probe_gate(args.registration, mode=args.mode, approval_file=args.approval_file)
        _, public, labels = xr.load_bank(registration["engineering_manifest"]["ref"])
        instructions = Path(registration["instruction"]["ref"]).read_text(encoding="utf-8")
        transport = None
        if args.mode == "SIMULATION":
            transport = SimulatedResponses(public, answer_for=lambda item: labels[item["item_id"]]["acceptable"][0],
                                           log_path=Path(args.out) / "simulated_server_log.jsonl")
        report = Probe(args.out, args.registration, mode=args.mode, instructions=instructions, public=public, transport=transport).run()
    except (ProbeBlocked, ValueError) as blocked:
        print(json.dumps({"state": "BLOCKED", "reasons": str(blocked).split(","), "generation_calls": 0}, indent=2))
        return 2
    print(json.dumps({"state": report["ledger_state"], "stop": report["stop"], "candidate_interface": report["candidate_interface"],
                      "requests_are_simulated": args.mode == "SIMULATION"}, indent=2, sort_keys=True))
    return 0 if report["ledger_state"] == "COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
