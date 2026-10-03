#!/usr/bin/env python3
"""Formal exploration runner: provider -> checker -> ledger -> scorer, in SIMULATION or LIVE mode (stdlib only).

    python3 exploration_runner.py run    --profile P --out DIR --mode SIMULATION [--allow-formal-simulation]
    python3 exploration_runner.py run    --profile P --out DIR --mode LIVE --approval-file F      (blocked until every gate holds)
    python3 exploration_runner.py resume --profile P --out DIR --mode SIMULATION|LIVE [...]
    python3 exploration_runner.py replay --out DIR
    python3 exploration_runner.py check  --profile P [--mode LIVE]

Accounting rules (the point of this module):
  * a durable START_INTENT is written (fsync) before every request; a slot left RUNNING by a crash is never re-sent:
    it is recovered as INCOMPLETE and charged the attempt cap;
  * the raw response, with its usage, is on disk before the adapter parses or validates it; a response the adapter
    rejects is charged from its own usage (or the cap when the usage cannot be trusted), never as free;
  * unknown cost is charged the cap, never zero; a cost above the cap stops the run;
  * only a request that provably never reached generation, or a definite "not processed" answer, is retried, once;
  * a provider identity or capability violation, a rejected request or an unexpected error halts the run: its slots are
    kept, nothing is scored, and the run can be neither resumed nor extended.
Never relaunches an existing run; resume continues only an interrupted one, after recovery.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import random
import socket
import sys
import time
import urllib.error
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Callable, Dict, List, Optional

import _lab  # noqa: F401  (puts the sealed lab on sys.path and checks where each module came from)
import budget_ledger as bl
import decision_engine as engine
import execution_profile as ep
import offline_replay as replay
import responses_adapter as api
from simulated_transport import SimulatedResponses

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
MODES = ("SIMULATION", "LIVE")
BANK_PURPOSES = ("ENGINEERING_ONLY", "FORMAL_DECISION_EXPLORATION")
MANIFEST_ARTIFACT = "FABLE_ENSEMBLE3_R1_RUN_MANIFEST"
REPORT_METRICS = (
    "single", "single_gated", "maj3", "gated", "cov3", "disagreement", "coverage_gap", "vote_effect", "checker_effect",
    "aggregation_effect", "total_effect", "crit_single", "crit_single_gated", "crit_maj3", "crit_gated", "crit_checker",
    "crit_reduction", "vote_waste", "policy_loss", "agent_failure_rate", "infra_fail_rate", "unknown_rate", "esc_single_gated",
    "esc_gated", "wrong_selected", "correct_exists_but_escalated", "escalate_created",
)


class RunBlocked(Exception):
    """The run must not start (or resume); nothing was sent."""


class RunHalt(Exception):
    """The run stops now; the slot in `slot` was already settled in the ledger."""

    def __init__(self, reason: str, slot: Optional[str] = None, detail: str = ""):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.slot, self.detail = reason, slot, detail


class RunnerIOError(Exception):
    """The runner could not persist what it must persist; continuing would lose evidence."""


# --- durable files ---------------------------------------------------------------------------------------------------
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def exclusive_write(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_dir(path.parent)


class AppendLog:
    """JSON-lines file; every record is fsynced before append() returns."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def append(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        created = not self.path.exists()
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(descriptor, line.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if created:
            _fsync_dir(self.path.parent)

    def records(self) -> List[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]


# --- inputs ----------------------------------------------------------------------------------------------------------
def load_bank(manifest_path):
    """(manifest, public items, labels).  Every hash is re-checked; the provider only ever gets `public` items."""
    manifest = engine.read_json(manifest_path)
    if manifest.get("purpose") not in BANK_PURPOSES:
        raise ValueError("unknown bank purpose")
    public, labels = {}, {}
    for row in manifest["items"]:
        for ref, digest in (("public_ref", "public_sha256"), ("oracle_ref", "oracle_sha256")):
            if ep.sha256_file(row[ref]) != row[digest]:
                raise ValueError("fixture or oracle changed after freeze")
        item = engine.read_json(row["public_ref"])
        engine.validate_public(item)
        label = engine.read_json(row["oracle_ref"])
        if item["item_id"] != row["item_id"] or label["item_id"] != row["item_id"] or label["public_sha256"] != row["public_sha256"]:
            raise ValueError("oracle does not bind this fixture")
        public[row["item_id"]], labels[row["item_id"]] = item, label
    return manifest, public, labels


def raw_upper_cost(response) -> Optional[Decimal]:
    """Upper bound of what a received response cost, from its own usage; None when the usage is not usable numbers.

    The same formula the adapter uses for its conservative upper bound, but without its bounds checks: a response the
    adapter refuses still cost what its usage says, and that must reach the ledger.
    """
    try:
        usage = response["usage"]
        inputs, outputs = usage["input_tokens"], usage["output_tokens"]
        cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0)
    except (KeyError, TypeError, AttributeError):
        return None
    if any(type(n) is not int or n < 0 for n in (inputs, outputs, cached)):
        return None
    long_context = inputs > 272000
    return (Decimal(inputs) * Decimal("0.25") * (2 if long_context else 1)
            + Decimal(outputs) * Decimal("1.20") * (Decimal("1.5") if long_context else 1)) / Decimal(1000000)


def build_report(public: Dict[str, dict], labels: Dict[str, dict], runs: Dict[str, List[dict]], *, kind: str, ledger_state: dict) -> dict:
    """Score the fixed pool with the reviewed R2 policy; every metric R2 defines, with its family-cluster interval."""
    es = replay.scorer()
    items = [es.Item(i, p["family"], "DECISION", frozenset(p["disposition_enum"]), frozenset(labels[i]["acceptable"]),
                     frozenset(labels[i]["unsafe"])) for i, p in public.items()]
    verified = {i: [es.Run(status=r["status"], disposition=r.get("disposition"), eligible=r.get("eligible"),
                           contradicts=frozenset(r.get("contradicts", []))) for r in rows] for i, rows in runs.items()}
    pool = es.evaluate_pool(items, verified)
    intervals = es.pool_intervals(pool, list(REPORT_METRICS))
    try:
        classification = list(es.classify(intervals))
    except ValueError as exc:
        classification = ["UNCLASSIFIED", str(exc)[:200]]
    statuses: Dict[str, int] = {}
    for rows in runs.values():
        for row in rows:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    return {
        "kind": kind,
        "statistical_claims_allowed": False,
        "items": pool.n_items, "families": pool.n_families, "unresolved": pool.unresolved,
        "run_statuses": dict(sorted(statuses.items())),
        "always_escalate_success": sum("ESCALATE" in labels[i]["acceptable"] for i in public) / len(public),
        "metrics": {name: None if iv is None else {"estimate": iv.est, "lo": iv.lo, "hi": iv.hi, "families": iv.families}
                    for name, iv in intervals.items()},
        "classification": classification,
        "ledger": {"spent_upper_usd": ledger_state["spent_upper"], "attempt_cap_usd": ledger_state["attempt_cap"],
                   "budget_usd": ledger_state["budget"], "overrun": ledger_state["overrun"]},
    }


# --- the provider side -----------------------------------------------------------------------------------------------
class Recorder:
    """Transport wrapper.  The request is on disk before it is sent; the raw response is on disk before anyone parses it."""

    def __init__(self, inner: Callable, log: AppendLog):
        self.inner, self.log = inner, log
        self.slot = self.attempt = None
        self.generation_sent = False
        self.last_generation = None

    def begin_attempt(self, slot: str, attempt: int) -> None:
        self.slot, self.attempt, self.generation_sent, self.last_generation = slot, attempt, False, None

    def _append(self, record: dict) -> None:
        try:
            self.log.append({"slot": self.slot, "attempt": self.attempt, "t": time.time(), **record})
        except OSError as exc:
            raise RunnerIOError(f"cannot persist {record.get('phase')}: {exc}") from exc

    def __call__(self, path: str, body: dict) -> dict:
        instructions = body.get("instructions")
        self._append({"phase": "REQUEST", "path": path, "body_sha256": sha256_text(json.dumps(body)),
                      "instructions_sha256": None if instructions is None else sha256_text(instructions),
                      "body": {key: value for key, value in body.items() if key != "instructions"}})
        if path == "responses":
            self.generation_sent = True
        try:
            response = self.inner(path, body)
        except BaseException as exc:
            self._append({"phase": "ERROR", "path": path, "error": {"type": type(exc).__name__, "text": str(exc)[:300],
                                                                      "http_status": getattr(exc, "code", None)}})
            raise
        if path == "responses":
            self.last_generation = response  # kept in memory first, so its cost is known even if the append below fails
        self._append({"phase": "RESPONSE", "path": path, "response": response})
        return response


def classify(exc: BaseException, *, generation_sent: bool, response_received: bool) -> str:
    """What a failed attempt means.  INFRA: provably not processed, retry once.  TIMEOUT / UNCERTAIN: the generation
    request left and the outcome is unknown, never resend.  REJECTED / CAPABILITY / PERSISTENCE / OTHER: stop the run."""
    if isinstance(exc, RunnerIOError):
        return "PERSISTENCE"
    if isinstance(exc, api.CapabilityBlocked):
        return "CAPABILITY"
    if response_received:
        return "OTHER"  # a response arrived and then something broke: it is paid for and unusable
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429 or exc.code == 503 or (500 <= exc.code <= 599 and not generation_sent):
            return "INFRA"
        return "UNCERTAIN" if 500 <= exc.code <= 599 else "REJECTED"
    timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or (
        isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout)))
    if timed_out:
        return "TIMEOUT" if generation_sent else "INFRA"
    if isinstance(exc, (urllib.error.URLError, ConnectionError, OSError)):
        return "UNCERTAIN" if generation_sent else "INFRA"
    return "OTHER"


def rounds_reserved(ledger_state: dict, order: List[str], planned: int) -> int:
    return sum(1 for index in range(planned) if f"{order[0]}:{index}" in ledger_state["slots"])


def finished_report(public, labels, runs, *, mode: str, ledger_state: dict, order: List[str], planned: int, target: int) -> dict:
    kind = "SIMULATION_NOT_A_RESULT" if mode == "SIMULATION" else "EXPLORATION_RESULT_UNREVIEWED"
    report = build_report(public, labels, runs, kind=kind, ledger_state=ledger_state)
    report.update(halted=False, planned_rounds=planned, rounds_reserved=rounds_reserved(ledger_state, order, planned), target_rounds=target)
    return report


_SCORER = None


def scorer():
    global _SCORER
    if _SCORER is None:
        _SCORER = replay.scorer()
    return _SCORER


def saved_runs(public: Dict[str, dict], ledger: bl.Ledger, rounds: int) -> Dict[str, List[dict]]:
    """Verified runs per item from the ledger alone: the fixed denominator, NOT_RUN where nothing started."""
    result = {}
    for item_id in public:
        rows = []
        for index in range(rounds):
            slot = ledger.state["slots"].get(f"{item_id}:{index}")
            if slot is None or slot["state"] == "RESERVED":
                rows.append({"status": "NOT_RUN"})
            elif slot["state"] == "FINAL":
                rows.append(slot["verified_run"])
            elif slot["state"] == "RETRY_READY":  # the first attempt ended in an infra error and no retry was made
                rows.append({"status": "INFRA_FAIL", "disposition": None, "eligible": None, "contradicts": []})
            elif slot["state"] == "OVERRUN":
                last = slot["receipts"][-1]["status"]
                rows.append({"status": last if last in engine.FAILURES else "INCOMPLETE", "disposition": None, "eligible": None, "contradicts": []})
            else:
                raise ValueError(f"unsettled slot {item_id}:{index}: recover uncertain starts before replay")
        result[item_id] = rows
    return result


class Runner:
    def __init__(self, out_dir, *, mode: str, bank_manifest, instructions: str, budget, attempt_cap, target_rounds: int = 5,
                 seed: int = 6302, transport: Optional[Callable] = None, profile=None, protocol=None):
        if mode not in MODES:
            raise RunBlocked("MODE_INVALID")
        self.out_dir, self.mode = Path(out_dir), mode
        self.bank_manifest = Path(bank_manifest)
        self.manifest, self.public, self.labels = load_bank(self.bank_manifest)
        self.purpose = self.manifest["purpose"]
        self.instructions = instructions
        self.budget, self.cap = bl.money(budget), bl.money(attempt_cap)
        self.target_rounds, self.seed = target_rounds, seed
        self.transport, self.profile, self.protocol = transport, profile, protocol
        self.log = AppendLog(self.out_dir / "attempts.jsonl")
        self.order = sorted(self.public)
        random.Random(seed).shuffle(self.order)
        self.ledger: Optional[bl.Ledger] = None
        self.recorder: Optional[Recorder] = None
        self.planned = 0
        self._lock_fd: Optional[int] = None

    # --- identity, state, lock ---------------------------------------------------------------------------------------
    def _identity(self) -> dict:
        return {
            "artifact": MANIFEST_ARTIFACT, "schema_version": 1, "mode": self.mode, "bank_purpose": self.purpose,
            "bank_manifest": ep.pin(self.bank_manifest), "instructions_sha256": sha256_text(self.instructions),
            "policy_sha256": ep.sha256_file(ep.R1 / "ensemble_scoring.py"), "runner_sources": ep.runner_sources(),
            "profile": None if self.profile is None else ep.pin(self.profile),
            "protocol": None if self.protocol is None else ep.pin(self.protocol),
            "seed": self.seed, "target_rounds": self.target_rounds, "item_order": self.order,
            "budget_usd": str(self.budget), "attempt_cap_usd": str(self.cap),
        }

    def _set_state(self, state: str, **extra) -> None:
        atomic_write(self.out_dir / "run_state.json", canonical({"state": state, "t": time.time(), **extra}))

    def _lock(self) -> None:
        descriptor = os.open(self.out_dir / "run.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise RunBlocked("ANOTHER_RUNNER_HOLDS_THIS_DIRECTORY")
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        self._lock_fd = descriptor

    def _unlock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _plan(self) -> int:
        planned = scorer().plan_rounds(len(self.public), 2 * self.cap, self.budget, self.target_rounds)
        if planned == 0:
            raise RunBlocked("BUDGET_BELOW_THREE_ROUNDS")
        if self.mode == "LIVE" and self.purpose == "FORMAL_DECISION_EXPLORATION" and planned != self.target_rounds:
            raise RunBlocked("WHOLE_ROUNDS_NOT_RESERVED")
        return planned

    def _adapter(self) -> "api.ResponsesAdapter":
        self.recorder = None
        if self.mode == "SIMULATION":
            if self.transport is None:
                raise RunBlocked("SIMULATION_NEEDS_A_SIMULATED_TRANSPORT")
            holder = Recorder(self.transport, self.log)
            adapter = api.ResponsesAdapter(self.instructions, transport=holder)
            if adapter.live:
                raise RunBlocked("SIMULATION_MUST_NOT_USE_THE_LIVE_ADAPTER")
        else:
            if self.transport is not None:
                raise RunBlocked("LIVE_FORBIDS_AN_INJECTED_TRANSPORT")
            adapter = api.ResponsesAdapter(self.instructions)
            if not adapter.live:
                raise RunBlocked("LIVE_NEEDS_THE_LIVE_ADAPTER")
            try:
                adapter.preflight()  # key presence, before a single request is built
            except api.CapabilityBlocked as exc:
                raise RunBlocked(f"LIVE_KEY_MISSING: {exc}") from exc
            holder = Recorder(adapter._post, self.log)
            adapter.transport = holder
        self.recorder = holder
        return adapter

    # --- lifecycle -----------------------------------------------------------------------------------------------
    def start(self) -> dict:
        if self.out_dir.exists() and any(self.out_dir.iterdir()):
            raise RunBlocked("OUT_DIR_NOT_EMPTY: never relaunch an existing run; resume or replay it")
        self.planned = self._plan()
        adapter = self._adapter()  # wiring errors (no key, wrong transport) surface before anything is written
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._lock()
        try:
            exclusive_write(self.out_dir / "run_manifest.json",
                            canonical({**self._identity(), "planned_rounds": self.planned, "started_epoch": time.time()}))
            self._set_state("RUNNING")
            self.ledger = bl.Ledger(self.out_dir / "ledger.json", budget=self.budget, attempt_cap=self.cap)
            return self._execute(adapter)
        finally:
            self._unlock()

    def resume(self) -> dict:
        if not (self.out_dir / "run_manifest.json").is_file():
            raise RunBlocked("NO_RUN_TO_RESUME")
        self._lock()
        try:
            manifest = engine.read_json(self.out_dir / "run_manifest.json")
            state = engine.read_json(self.out_dir / "run_state.json")
            if state["state"] != "RUNNING":
                raise RunBlocked(f"RUN_IS_{state['state']}: only an interrupted run can resume")
            mismatched = sorted(key for key, value in self._identity().items() if manifest.get(key) != value)
            if mismatched:
                raise RunBlocked("RESUME_IDENTITY_MISMATCH:" + ",".join(mismatched))
            self.planned = manifest["planned_rounds"]
            adapter = self._adapter()  # before the ledger is touched: a wiring error must not recover or change anything
            self.ledger = bl.Ledger(self.out_dir / "ledger.json", budget=self.budget, attempt_cap=self.cap)
            uncertain = sorted(slot for slot, record in self.ledger.state["slots"].items() if record["state"] == "RUNNING")
            self.ledger.recover_uncertain()  # started, no receipt: INCOMPLETE and charged the cap; never sent again
            self.log.append({"phase": "RESUME", "t": time.time(), "recovered_uncertain": uncertain})
            return self._execute(adapter)
        finally:
            self._unlock()

    def _execute(self, adapter) -> dict:
        halted = None
        try:
            self._rounds(adapter)
        except RunHalt as halt:
            halted = halt
        return self._finalize(halted)

    def _rounds(self, adapter) -> None:
        for round_index in range(self.planned):
            if f"{self.order[0]}:{round_index}" not in self.ledger.state["slots"]:
                try:
                    self.ledger.reserve_round(round_index, self.order)  # the whole round, retries included, or nothing
                except bl.BudgetStop:
                    return  # the next whole round cannot be reserved: stop cleanly, the rest stays NOT_RUN
                except bl.StateConflict as exc:  # e.g. a kill left an overrun on record: end as a halt, not as a crash that repeats
                    raise RunHalt("LEDGER_CONFLICT", None, str(exc)) from exc
                self.log.append({"phase": "ROUND_RESERVED", "round": round_index, "t": time.time()})
            for item_id in self.order:
                slot = f"{item_id}:{round_index}"
                if self.ledger.state["slots"][slot]["state"] in ("RESERVED", "RETRY_READY"):
                    self._execute_slot(adapter, slot, self.public[item_id])

    # --- one slot ----------------------------------------------------------------------------------------------------
    def _execute_slot(self, adapter, slot: str, item: dict) -> None:
        while True:
            try:
                self.ledger.begin(slot)  # durable START_INTENT (fsync) before any request leaves this process
            except bl.StateConflict as exc:
                raise RunHalt("LEDGER_CONFLICT", slot, str(exc)) from exc
            attempt = self.ledger.state["slots"][slot]["attempts"]
            self.recorder.begin_attempt(slot, attempt)
            self.log.append({"phase": "START", "slot": slot, "attempt": attempt, "t": time.time()})
            try:
                receipt = adapter.invoke(item)
            except Exception as exc:  # KeyboardInterrupt/SystemExit leave the slot RUNNING on purpose: resume recovers it
                if self._settle_exception(slot, item, attempt, exc) == "RETRY":
                    continue
                return
            self._settle_receipt(slot, item, attempt, receipt)
            return

    def _log_settled(self, slot, attempt, status, cost, kind, detail, state=None) -> None:
        self.log.append({"phase": "SETTLED", "slot": slot, "attempt": attempt, "status": status, "kind": kind, "detail": detail,
                         "cost_upper_usd": None if cost is None else str(cost), "charged_cap_because_unknown": cost is None,
                         "ledger_state": state, "t": time.time()})

    def _finish(self, slot: str, status: str, cost: Optional[Decimal], item: dict, verified: Optional[dict] = None) -> str:
        if verified is None and status != "INFRA_ERROR":
            verified = engine.verify_run(item, {"status": status})
        try:
            return self.ledger.finish(slot, status, cost, verified)  # cost None is charged the cap, never zero
        except bl.BudgetStop as stop:
            raise RunHalt("ATTEMPT_CAP_OVERRUN", slot, str(stop)) from stop

    def _settle_exception(self, slot: str, item: dict, attempt: int, exc: Exception) -> str:
        recorder = self.recorder
        received = recorder.last_generation is not None
        kind = classify(exc, generation_sent=recorder.generation_sent, response_received=received)
        cost = raw_upper_cost(recorder.last_generation) if received else None
        stops = kind in ("REJECTED", "CAPABILITY", "PERSISTENCE", "OTHER")
        status = {"INFRA": "INFRA_ERROR", "TIMEOUT": "TIMEOUT", "UNCERTAIN": "INCOMPLETE"}.get(kind) or ("INVALID_OUTPUT" if received else "INCOMPLETE")
        detail = f"{type(exc).__name__}: {str(exc)[:200]}"
        try:
            state = self._finish(slot, status, cost, item)
        except RunHalt:
            self._log_settled(slot, attempt, status, cost, "ATTEMPT_CAP_OVERRUN", detail)
            raise
        self._log_settled(slot, attempt, status, cost, kind, detail, state)
        if stops:
            raise RunHalt(kind, slot, detail) from exc
        return "RETRY" if state == "RETRY_READY" else "DONE"

    def _settle_receipt(self, slot: str, item: dict, attempt: int, receipt: dict) -> None:
        status = cost = verified = None
        try:
            status, cost = receipt["status"], bl.money(receipt["cost_upper_usd"])
            if not isinstance(status, str):
                raise ValueError("status is not text")
        except (KeyError, TypeError, ValueError, ArithmeticError):
            problem, cost = "MALFORMED_RECEIPT", None  # a receipt that cannot be read is unusable and is charged the cap, never zero
        else:
            problem = None
            if receipt.get("mode") != ("SIMULATED_TRANSPORT" if self.mode == "SIMULATION" else "LIVE"):
                problem = "RECEIPT_MODE_MISMATCH"
            elif status == "OK":
                try:
                    verified = engine.verify_run(item, receipt["output"])
                    if verified["status"] != "OK":
                        problem = "INCONSISTENT_RECEIPT"
                except (KeyError, TypeError, ValueError):
                    problem = "INCONSISTENT_RECEIPT"
            elif status in engine.FAILURES:
                verified = engine.verify_run(item, {"status": status})
            else:
                problem = "UNKNOWN_RECEIPT_STATUS"
        if problem:
            self._finish(slot, "INVALID_OUTPUT", cost, item)  # paid for, unusable
            self._log_settled(slot, attempt, "INVALID_OUTPUT", cost, problem, f"receipt status {status!r}")
            raise RunHalt(problem, slot, f"receipt status {status!r}")
        state = self._finish(slot, status, cost, item, verified)
        self._log_settled(slot, attempt, status, cost, "RECEIPT", f"provider_response_id={receipt.get('provider_response_id')}", state)

    # --- the end -----------------------------------------------------------------------------------------------------
    def _finalize(self, halted: Optional[RunHalt]) -> dict:
        runs = saved_runs(self.public, self.ledger, self.target_rounds)
        atomic_write(self.out_dir / "runs.json", canonical(runs))
        kind = "SIMULATION_NOT_A_RESULT" if self.mode == "SIMULATION" else "EXPLORATION_RESULT_UNREVIEWED"
        reserved = rounds_reserved(self.ledger.state, self.order, self.planned)
        if halted is None:
            report = finished_report(self.public, self.labels, runs, mode=self.mode, ledger_state=self.ledger.state, order=self.order,
                                     planned=self.planned, target=self.target_rounds)
            state, halt = "COMPLETE", None
            self._set_state("COMPLETE")
        else:
            halt = {"reason": halted.reason, "slot": halted.slot, "detail": halted.detail}
            report = {"kind": kind, "halted": True, "halt": halt, "scoring": None, "planned_rounds": self.planned,
                      "rounds_reserved": reserved, "target_rounds": self.target_rounds,
                      "ledger": {"spent_upper_usd": self.ledger.state["spent_upper"], "attempt_cap_usd": self.ledger.state["attempt_cap"],
                                 "budget_usd": self.ledger.state["budget"], "overrun": self.ledger.state["overrun"]}}
            state = "HALTED"
            self._set_state("HALTED", **halt)
        atomic_write(self.out_dir / "report.json", canonical(report))
        self._write_accounting()
        return {"state": state, "halt": halt, "report": report, "out_dir": str(self.out_dir)}

    def _write_accounting(self) -> None:
        """Every file this run left in its directory, with hashes; the runner writes nowhere else."""
        files = [{"path": str(path.relative_to(self.out_dir)), "bytes": path.stat().st_size, "sha256": ep.sha256_file(path)}
                 for path in sorted(self.out_dir.rglob("*")) if path.is_file() and path.name != "write_ledger.json"]
        atomic_write(self.out_dir / "write_ledger.json", canonical({
            "out_dir": str(self.out_dir.resolve()), "files": files, "writes_outside_out_dir": [],
            "note": "no temporary file survives; the runner opens no path outside out_dir"}))


# --- replay ----------------------------------------------------------------------------------------------------------
def replay_run(out_dir) -> dict:
    """Rebuild a finished run's verified runs (and report) from the ledger alone; never writes anything."""
    out_dir = Path(out_dir)
    manifest = engine.read_json(out_dir / "run_manifest.json")
    bank = manifest["bank_manifest"]
    if ep.sha256_file(bank["ref"]) != bank["sha256"]:
        raise ValueError("the bank changed since this run")
    _, public, labels = load_bank(bank["ref"])
    if not (out_dir / "ledger.json").is_file():
        raise ValueError("no ledger to replay")
    ledger = bl.Ledger(out_dir / "ledger.json", budget=manifest["budget_usd"], attempt_cap=manifest["attempt_cap_usd"])
    runs = saved_runs(public, ledger, manifest["target_rounds"])
    state = engine.read_json(out_dir / "run_state.json")["state"]
    saved = engine.read_json(out_dir / "runs.json") if (out_dir / "runs.json").is_file() else None
    report = None
    if state == "COMPLETE":
        report = finished_report(public, labels, runs, mode=manifest["mode"], ledger_state=ledger.state, order=manifest["item_order"],
                                 planned=manifest["planned_rounds"], target=manifest["target_rounds"])
    return {"state": state, "runs": runs, "matches_saved_runs": None if saved is None else saved == runs, "report": report}


# --- gates and command line ------------------------------------------------------------------------------------------
def validate_approval(path, bound_path, *, scope: str, total_required: Decimal, bound_key: str = "profile_sha256") -> List[str]:
    """The Owner's written approval for a LIVE action, bound to the exact artifact `bound_path` hashes to.  Nothing here writes one."""
    try:
        approval = engine.read_json(path)
    except (OSError, ValueError, TypeError):
        return ["APPROVAL_MISSING_OR_INVALID"]
    if not isinstance(approval, dict):
        return ["APPROVAL_MISSING_OR_INVALID"]
    reasons = []
    if approval.get("approved") is not True:
        reasons.append("APPROVAL_NOT_GRANTED")
    for key in ("approved_by", "approved_on", "data_egress"):
        if not isinstance(approval.get(key), str) or not approval[key].strip():
            reasons.append(f"APPROVAL_FIELD_MISSING:{key}")
    if approval.get("scope") != scope:
        reasons.append("APPROVAL_SCOPE_MISMATCH")
    if approval.get(bound_key) != ep.sha256_file(bound_path):
        reasons.append("APPROVAL_NOT_FOR_THIS_PROFILE" if bound_key == "profile_sha256" else "APPROVAL_NOT_FOR_THIS_REGISTRATION")
    try:
        if bl.money(approval.get("max_total_reservation_usd")) < total_required:
            reasons.append("APPROVAL_CAP_BELOW_REQUIRED")
    except (ValueError, ArithmeticError):
        reasons.append("APPROVAL_CAP_INVALID")
    return reasons


def _inputs(profile_path, mode: str, budget_override):
    profile = engine.read_json(profile_path)
    instructions = Path(profile["instruction"]["ref"]).read_text(encoding="utf-8")
    cap = Decimal(profile["cost"]["attempt_reservation_usd"])
    required = cap * profile["cost"]["max_attempts_per_slot"] * profile["bank"]["items"] * profile["policy"]["target_runs"]
    total = profile["cost"].get("total_reservation_usd")
    if mode == "LIVE":
        budget = None if total is None else Decimal(str(total))  # an unfrozen total is a gate reason, not a crash
    else:
        budget = Decimal(str(budget_override if budget_override is not None else (total or required)))
    return profile, instructions, cap, required, budget


def _runner(args, *, resume: bool) -> Runner:
    profile_path = Path(args.profile)
    reasons = ep.check_profile(profile_path, mode=args.mode)["reasons"]
    if any(reason.startswith(("PROFILE_", "MODE_")) for reason in reasons):
        raise RunBlocked(",".join(sorted(set(reasons))))  # a profile that cannot even be read has no inputs to look at
    profile, instructions, cap, required, budget = _inputs(profile_path, args.mode, args.budget)
    if budget is None:
        reasons.append("TOTAL_COST_CAP_UNFROZEN")
    if args.mode == "LIVE":
        reasons += validate_approval(args.approval_file, profile_path, scope="FORMAL_EXPLORATION_R1", total_required=required) \
            if args.approval_file else ["APPROVAL_MISSING_OR_INVALID"]
    elif profile["bank"]["purpose"] == "FORMAL_DECISION_EXPLORATION" and not args.allow_formal_simulation:
        reasons.append("FORMAL_BANK_SIMULATION_NEEDS_--allow-formal-simulation")
    if reasons:
        raise RunBlocked(",".join(sorted(set(reasons))))
    runner = Runner(args.out, mode=args.mode, bank_manifest=profile["bank"]["manifest"]["ref"], instructions=instructions, budget=budget,
                    attempt_cap=cap, target_rounds=profile["policy"]["target_runs"], seed=profile["policy"]["schedule_seed"],
                    profile=profile_path, protocol=profile["protocol"]["ref"])
    if args.mode == "SIMULATION":  # answers come from the labels: a pipeline exercise, never a model result
        runner.transport = SimulatedResponses(runner.public, answer_for=lambda item: runner.labels[item["item_id"]]["acceptable"][0],
                                              log_path=Path(args.out) / "simulated_server_log.jsonl")
    return runner


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "resume"):
        sub = commands.add_parser(name)
        sub.add_argument("--profile", required=True)
        sub.add_argument("--out", required=True)
        sub.add_argument("--mode", required=True, choices=MODES)
        sub.add_argument("--approval-file")
        sub.add_argument("--allow-formal-simulation", action="store_true")
        sub.add_argument("--budget")
    replay_cmd = commands.add_parser("replay")
    replay_cmd.add_argument("--out", required=True)
    check = commands.add_parser("check")
    check.add_argument("--profile", required=True)
    check.add_argument("--mode", default="SIMULATION", choices=MODES)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            result = ep.check_profile(args.profile, mode=args.mode)
            print(json.dumps(result, indent=2))
            return 0 if not result["reasons"] else 2
        if args.command == "replay":
            result = replay_run(args.out)
            print(json.dumps({k: v for k, v in result.items() if k != "runs"}, indent=2, sort_keys=True))
            return 0
        runner = _runner(args, resume=args.command == "resume")
        result = runner.start() if args.command == "run" else runner.resume()
    except RunBlocked as blocked:
        print(json.dumps({"state": "BLOCKED", "reasons": str(blocked).split(","), "generation_calls": 0}, indent=2))
        return 2
    print(json.dumps({"state": result["state"], "halt": result["halt"], "out_dir": result["out_dir"],
                      "kind": result["report"]["kind"], "generation_calls_are_simulated": args.mode == "SIMULATION"}, indent=2))
    return 3 if result["state"] == "HALTED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
