#!/usr/bin/env python3
"""Test support: run or resume the engineering-bank SIMULATION in a child process that may be SIGKILLed mid-call.

    crash_driver.py WORK_DIR start|resume [ITEM_ID_TO_CRASH_ON | none]

The simulated server logs the request and then kills this very process, so the death happens with the request sent and
nothing settled, exactly like a crash.  The server log in WORK_DIR proves afterwards what was received, and how often.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import _lab  # noqa: F401
import exploration_runner as xr
from simulated_transport import SimulatedResponses


def main() -> None:
    work, command = Path(sys.argv[1]), sys.argv[2]
    crash_item = sys.argv[3] if len(sys.argv) > 3 else "none"
    manifest = work / "bank" / "manifest.json"
    _, public, labels = xr.load_bank(manifest)
    faults = [] if crash_item == "none" else [{"path": "responses", "item_id": crash_item, "occurrence": 1, "fault": "crash"}]
    transport = SimulatedResponses(public, answer_for=lambda item: labels[item["item_id"]]["acceptable"][0], faults=faults,
                                   log_path=work / "server.jsonl")
    runner = xr.Runner(work / "run", mode="SIMULATION", bank_manifest=manifest, instructions="synthetic instructions", budget="50",
                       attempt_cap="0.55", transport=transport)
    result = runner.start() if command == "start" else runner.resume()
    print(json.dumps({"state": result["state"], "halt": result["halt"]}))


if __name__ == "__main__":
    main()
