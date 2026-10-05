#!/usr/bin/env python3
"""Test support: run or resume the engineering-bank SIMULATION in a child process that may be SIGKILLed mid-call.

    crash_driver.py WORK_DIR start|resume [ITEM_ID_TO_CRASH_ON | none]

The simulated server logs the request and then kills this very process, so the death happens with the request sent and
nothing settled, exactly like a crash.  The server log in WORK_DIR proves afterwards what was received, and how often.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import _lab  # noqa: F401
import budget_ledger as bl
import exploration_runner as xr
from simulated_transport import SimulatedResponses


def main() -> None:
    work, command = Path(sys.argv[1]), sys.argv[2]
    crash_item = sys.argv[3] if len(sys.argv) > 3 else "none"
    manifest = work / "bank" / "manifest.json"
    _, public, labels = xr.load_bank(manifest)
    fault = "huge_usage" if command == "start-overrun" else "crash"
    faults = [] if crash_item == "none" else [{"path": "responses", "item_id": crash_item, "occurrence": 1, "fault": fault}]
    transport = SimulatedResponses(public, answer_for=lambda item: labels[item["item_id"]]["acceptable"][0], faults=faults,
                                   log_path=work / "server.jsonl")
    runner = xr.Runner(work / "run", mode="SIMULATION", bank_manifest=manifest, instructions="synthetic instructions", budget="50",
                       attempt_cap="0.55", transport=transport)
    if crash_item != "none":
        begin = bl.Ledger.begin
        def snapshot_before_begin(ledger, slot):
            if slot == f"{crash_item}:0":
                (work / "ledger-snapshot.json").write_bytes(ledger.path.read_bytes())
            return begin(ledger, slot)
        bl.Ledger.begin = snapshot_before_begin
    if command == "start-overrun":
        finish = runner._finish
        def kill_before_finish(slot, *args, **kwargs):
            if slot == f"{crash_item}:0":
                os.kill(os.getpid(), signal.SIGKILL)
            return finish(slot, *args, **kwargs)
        runner._finish = kill_before_finish
    try:
        result = runner.start() if command.startswith("start") else runner.resume()
    except xr.RunBlocked as exc:
        print(json.dumps({"state": "BLOCKED", "reason": str(exc)}))
        raise SystemExit(2)
    print(json.dumps({"state": result["state"], "halt": result["halt"]}))


if __name__ == "__main__":
    main()
