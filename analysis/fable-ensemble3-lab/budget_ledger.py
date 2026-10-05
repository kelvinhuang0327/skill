"""Atomic single-writer ledger: reserve entire rounds including the one allowed retry."""
from decimal import Decimal
import json
import os
from pathlib import Path
import uuid


class BudgetStop(Exception):
    pass


class StateConflict(Exception):
    pass


def money(value):
    if isinstance(value, bool):
        raise ValueError("boolean cost")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("invalid monetary amount")
    return result


class Ledger:
    def __init__(self, path, budget, attempt_cap, max_attempts=2):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if max_attempts != 2 or money(attempt_cap) <= 0:
            raise ValueError("one original attempt and at most one infra retry")
        self.budget, self.cap = money(budget), money(attempt_cap)
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            if self.state["budget"] != str(self.budget) or self.state["attempt_cap"] != str(self.cap):
                raise StateConflict("cannot change a live budget")
        else:
            self.state = {"budget": str(self.budget), "attempt_cap": str(self.cap),
                          "spent_upper": "0", "slots": {}, "overrun": False, "events": []}
            self._save()

    @property
    def held(self):
        return sum((money(s["held"]) for s in self.state["slots"].values()), Decimal(0))

    def _save(self):
        # Single Worker owns this file; there is no concurrent-writer claim.
        temp = self.path.with_name(self.path.name+"."+uuid.uuid4().hex+".tmp")
        try:
            with temp.open("x") as stream:
                stream.write(json.dumps(self.state, sort_keys=True, indent=2)+"\n")
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, self.path)
            fd = os.open(str(self.path.parent), os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if temp.exists(): temp.unlink()

    def reserve_round(self, round_index, item_ids):
        if len(set(item_ids)) != len(item_ids) or not item_ids:
            raise ValueError("unique nonempty round")
        keys = [f"{item_id}:{round_index}" for item_id in item_ids]
        if any(key in self.state["slots"] for key in keys):
            raise StateConflict("round already reserved")
        if self.state["overrun"] or any(s["state"] == "RUNNING" for s in self.state["slots"].values()):
            raise StateConflict("unsettled attempt must be recovered before any new round")
        required = len(keys)*2*self.cap
        if money(self.state["spent_upper"])+self.held+required > self.budget:
            raise BudgetStop("whole round including retries cannot be reserved")
        for key in keys:
            self.state["slots"][key] = {"state": "RESERVED", "attempts": 0, "held": str(2*self.cap), "receipts": []}
        self.state["events"].append({"event": "RESERVE_ROUND", "round": round_index, "slots": keys})
        self._save()
        return keys

    def begin(self, slot):
        record = self.state["slots"][slot]
        if self.state["overrun"] or record["state"] not in {"RESERVED", "RETRY_READY"} or record["attempts"] >= 2:
            raise StateConflict("slot cannot begin; never resume an uncertain provider invocation")
        record["attempts"] += 1; record["state"] = "RUNNING"
        self.state["events"].append({"event": "START_INTENT", "slot": slot, "attempt": record["attempts"]})
        self._save()

    def finish(self, slot, status, cost, verified_run=None):
        record = self.state["slots"][slot]
        if record["state"] != "RUNNING":
            raise StateConflict("finish without a durable start")
        known = cost is not None
        charged = money(cost) if known else self.cap
        record["receipts"].append({"status": status, "cost": str(charged), "cost_known": known})
        self.state["spent_upper"] = str(money(self.state["spent_upper"])+charged)
        if charged > self.cap:
            record["state"] = "OVERRUN"; record["held"] = "0"; self.state["overrun"] = True
            self._save()
            raise BudgetStop("backend violated its advertised attempt cap; all further starts stopped")
        record["held"] = str(money(record["held"])-self.cap)
        if status == "INFRA_ERROR" and record["attempts"] == 1:
            record["state"] = "RETRY_READY"
        else:
            record["state"] = "FINAL"; record["held"] = "0"
            record["status"] = "INFRA_FAIL" if status == "INFRA_ERROR" else status
            record["verified_run"] = verified_run or {"status": record["status"], "disposition": None, "eligible": None, "contradicts": []}
        self._save()
        return record["state"]

    def recover_uncertain(self):
        """Fail uncertain started slots and charge the cap; never silently repeat the call."""
        for slot, record in self.state["slots"].items():
            if record["state"] == "RUNNING":
                self.state["spent_upper"] = str(money(self.state["spent_upper"])+self.cap)
                record["receipts"].append({"status": "INCOMPLETE", "cost": str(self.cap), "cost_known": False})
                record["state"] = "FINAL"; record["status"] = "INCOMPLETE"; record["held"] = "0"
                record["verified_run"] = {"status": "INCOMPLETE", "disposition": None, "eligible": None, "contradicts": []}
                self.state["events"].append({"event": "RECOVER_UNCERTAIN_NO_RELAUNCH", "slot": slot})
        self._save()
