"""Offline stand-in for the Responses endpoint: deterministic answers, injectable faults and a server-side request log.

It never opens a socket.  Every request is appended to the log and fsynced BEFORE it is acted on, so a test that kills
the client mid-call can still prove what the "server" received and that nothing was received twice.  Used only by the
runner's SIMULATION mode and by tests; it is labelled a simulation everywhere it appears.
"""
from __future__ import annotations

import collections
import hashlib
import io
import json
import os
import signal
import socket
import time
import urllib.error
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

import _lab  # noqa: F401  (puts the sealed lab on sys.path)
import responses_adapter as api


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.openai.com/v1/responses", code, "simulated", {}, io.BytesIO(b'{"error": {"message": "simulated"}}'))


class SimulatedResponses:
    """Behaves like the `transport(path, body)` the adapter calls.

    faults: iterable of {"path": "responses"|"responses/input_tokens", "item_id": id or None, "occurrence": n or None,
            "fault": name}.  A rule matches when every given key matches; the first matching rule fires.
    """

    is_simulated = True

    FAULTS = ("timeout", "socket_timeout", "conn_error", "url_error", "http_400", "http_401", "http_429", "http_500", "http_503",
              "wrong_model", "wrong_tier", "no_usage", "huge_usage", "incomplete_cap", "incomplete_other", "bad_json",
              "two_messages", "tool_call", "count_over", "cap_overshoot", "hang", "crash")

    def __init__(self, items: Dict[str, dict], *, answer_for: Optional[Callable[[dict], str]] = None, faults: Iterable[dict] = (),
                 log_path=None, count_tokens: int = 1000, usage=(1200, 80, 0), hang_seconds: float = 120.0,
                 enforce_output_cap: bool = True, count_surface_overhead: Optional[int] = 350, reject_count_extras: bool = False):
        self.items = items
        self.answer_for = answer_for or (lambda item: item["disposition_enum"][0])
        self.faults = [dict(rule) for rule in faults]
        for rule in self.faults:
            if rule["fault"] not in self.FAULTS:
                raise ValueError(f"unknown fault {rule['fault']!r}")
        self.log_path = Path(log_path) if log_path else None
        self.count_tokens, self.usage, self.hang_seconds = count_tokens, usage, hang_seconds
        # what the real endpoints might do, as options the Stage-2B probe is meant to observe
        self.enforce_output_cap, self.count_surface_overhead, self.reject_count_extras = enforce_output_cap, count_surface_overhead, reject_count_extras
        self.seen = collections.Counter()
        self.requests: list = []
        self.sequence = 0

    # --- the server's own record --------------------------------------------------------------------------------------
    def _log(self, record: dict) -> None:
        self.requests.append(record)
        if self.log_path is None:
            return
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        descriptor = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(descriptor, line.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def __call__(self, path: str, body: dict) -> dict:
        item_id = json.loads(body["input"])["item_id"]
        self.sequence += 1
        self.seen[(path, item_id)] += 1
        occurrence = self.seen[(path, item_id)]
        self._log({"seq": self.sequence, "path": path, "item_id": item_id, "occurrence": occurrence,
                   "input": body["input"], "body_sha256": _sha(json.dumps(body, sort_keys=True)),
                   "instructions_sha256": _sha(body.get("instructions", "")), "max_output_tokens": body.get("max_output_tokens")})
        for rule in self.faults:
            if all(rule.get(key) in (None, value) for key, value in (("path", path), ("item_id", item_id), ("occurrence", occurrence))):
                return self._fault(rule["fault"], path, body, item_id)
        return self._normal(path, item_id, body)

    # --- answers ----------------------------------------------------------------------------------------------------
    def _response(self, item_id: str, *, text: Optional[str] = None, **overrides) -> dict:
        item = self.items[item_id]
        if text is None:
            text = json.dumps({"item_id": item_id, "disposition": self.answer_for(item),
                               "evidence_refs": sorted({c for checks in item["required_checks"].values() for c in checks}),
                               "contradictions": []})
        response = {"id": f"resp_sim_{self.sequence}", "model": api.MODEL, "service_tier": "default", "status": "completed",
                    "store": False, "previous_response_id": None,
                    "usage": {"input_tokens": self.usage[0], "output_tokens": self.usage[1],
                              "input_tokens_details": {"cached_tokens": self.usage[2]}},
                    "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}
        response.update(overrides)
        return response

    def _normal(self, path: str, item_id: str, body: Optional[dict] = None) -> dict:
        body = body or {}
        if path == "responses/input_tokens":
            extras = {"text", "reasoning"} & set(body)
            if extras and self.reject_count_extras:
                raise http_error(400)
            return {"input_tokens": self.count_tokens + (self.count_surface_overhead or 0 if extras else 0)}
        if self.enforce_output_cap and body.get("max_output_tokens") is not None and body["max_output_tokens"] < self.usage[1]:
            response = self._response(item_id, status="incomplete", incomplete_details={"reason": "max_output_tokens"})
            response["usage"]["output_tokens"] = body["max_output_tokens"]  # the server stopped generating at the cap
            return response
        return self._response(item_id)

    def _fault(self, fault: str, path: str, body: dict, item_id: str) -> dict:
        if fault == "timeout":
            raise TimeoutError("simulated timeout")
        if fault == "socket_timeout":
            raise socket.timeout("timed out")
        if fault == "conn_error":
            raise ConnectionResetError("simulated connection reset")
        if fault == "url_error":
            raise urllib.error.URLError("simulated unreachable host")
        if fault.startswith("http_"):
            raise http_error(int(fault[5:]))
        if fault == "hang":
            time.sleep(self.hang_seconds)
            return self._normal(path, item_id, body)
        if fault == "crash":
            os.kill(os.getpid(), signal.SIGKILL)  # the process dies here, mid-call, with no cleanup at all
        if fault == "count_over":
            return {"input_tokens": api.INPUT_LIMIT + 1}
        if path != "responses":
            return self._normal(path, item_id, body)
        if fault == "wrong_model":
            return self._response(item_id, model="gpt-6-luna")
        if fault == "wrong_tier":
            return self._response(item_id, service_tier="priority")
        if fault == "no_usage":
            response = self._response(item_id)
            del response["usage"]
            return response
        if fault == "huge_usage":
            return self._response(item_id, usage={"input_tokens": 2_000_000, "output_tokens": 100, "input_tokens_details": {"cached_tokens": 0}})
        if fault == "incomplete_cap":
            response = self._response(item_id, status="incomplete", incomplete_details={"reason": "max_output_tokens"})
            response["usage"]["output_tokens"] = api.OUTPUT_LIMIT
            return response
        if fault == "cap_overshoot":
            response = self._response(item_id, status="incomplete", incomplete_details={"reason": "max_output_tokens"})
            response["usage"]["output_tokens"] = (body.get("max_output_tokens") or 0) + 5  # claims the cap, exceeds it
            return response
        if fault == "incomplete_other":
            return self._response(item_id, status="incomplete", incomplete_details={"reason": "content_filter"})
        if fault == "bad_json":
            return self._response(item_id, text="this is not json")
        if fault == "two_messages":
            response = self._response(item_id)
            response["output"].append(dict(response["output"][0]))
            return response
        if fault == "tool_call":
            response = self._response(item_id)
            response["output"].insert(0, {"type": "function_call", "name": "shell", "arguments": "{}"})
            return response
        raise AssertionError(f"unhandled fault {fault}")
