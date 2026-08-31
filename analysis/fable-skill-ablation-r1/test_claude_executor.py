#!/usr/bin/env python3
"""Offline acceptance tests for the evidence-bearing Claude adapter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import signal
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True


def load_sibling(name: str, filename: str) -> Any:
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load_sibling("fable_ablation_runner", "runner.py")
claude = load_sibling("fable_ablation_claude_executor", "claude_executor.py")


PROVIDER_SESSION_ID = "provider-session-001"
OPAQUE_ID_A = "0123456789abcdef0123456789abcdef"
OPAQUE_ID_B = "fedcba9876543210fedcba9876543210"


def init_record(
    *,
    skills: Sequence[str] = ("reference-skill",),
    tools: Sequence[str] = claude.CTO_TOOLSET,
    agents: Sequence[str] = ("general-purpose",),
    mcp_servers: Sequence[str] = (),
    plugins: Sequence[str] = (),
    session_id: str = PROVIDER_SESSION_ID,
    model: str = claude.CTO_MODEL_ID,
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "system",
        "subtype": "init",
        "session_id": session_id,
        "model": model,
        "effort": claude.CTO_EFFORT,
        "tools": list(tools),
        "agents": list(agents),
        "mcp_servers": list(mcp_servers),
        "plugins": list(plugins),
        "permissionMode": claude.CTO_PERMISSION_MODE,
        "skills": list(skills),
    }
    record.update(extra)
    return record


def assistant_record(
    message_id: str = "msg-001",
    *,
    session_id: str = PROVIDER_SESSION_ID,
    model: str = claude.CTO_MODEL_ID,
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "assistant",
        "session_id": session_id,
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": "offline"}],
        },
    }
    record.update(extra)
    return record


def result_record(
    *,
    session_id: str = PROVIDER_SESSION_ID,
    model_usage: Mapping[str, Any] | None = None,
    total_cost_usd: Any = "0.1250000",
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": session_id,
        "num_turns": 1,
        "modelUsage": (
            {claude.CTO_MODEL_ID: {"input_tokens": 10, "output_tokens": 5}}
            if model_usage is None
            else dict(model_usage)
        ),
        "total_cost_usd": total_cost_usd,
    }
    record.update(extra)
    return record


def successful_records(
    *,
    skills: Sequence[str] = ("reference-skill",),
    tools: Sequence[str] = claude.CTO_TOOLSET,
) -> list[dict[str, Any]]:
    return [
        init_record(skills=skills, tools=tools),
        assistant_record(),
        result_record(),
    ]


def jsonl(records: Sequence[Mapping[str, Any]]) -> bytes:
    return b"\n".join(
        json.dumps(record, separators=(",", ":")).encode("utf-8")
        for record in records
    ) + b"\n"


class FakeProcess:
    def __init__(
        self,
        stdout: bytes,
        stderr: bytes = b"",
        *,
        returncode: int = 0,
        pid: int = 4321,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.pid = pid
        self.communicate_calls: list[dict[str, Any]] = []

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> Any:
        self.communicate_calls.append({"input": input, "timeout": timeout})
        return self.stdout, self.stderr

    def poll(self) -> int:
        return self.returncode


class TimeoutThenTerminateProcess(FakeProcess):
    def __init__(self, partial_stdout: bytes, partial_stderr: bytes) -> None:
        super().__init__(partial_stdout, partial_stderr, returncode=0, pid=9876)
        self.call_count = 0

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> Any:
        self.communicate_calls.append({"input": input, "timeout": timeout})
        self.call_count += 1
        if self.call_count == 1:
            raise subprocess.TimeoutExpired(
                cmd="fake-claude",
                timeout=timeout or 0,
                output=self.stdout,
                stderr=self.stderr,
            )
        self.returncode = -signal.SIGTERM
        return self.stdout, self.stderr


class TimeoutThenKillProcess(FakeProcess):
    def __init__(self, partial_stdout: bytes, partial_stderr: bytes) -> None:
        super().__init__(partial_stdout, partial_stderr, returncode=0, pid=6789)
        self.call_count = 0

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> Any:
        self.communicate_calls.append({"input": input, "timeout": timeout})
        self.call_count += 1
        if self.call_count <= 2:
            raise subprocess.TimeoutExpired(
                cmd="fake-claude",
                timeout=timeout or 0,
                output=self.stdout,
                stderr=self.stderr,
            )
        self.returncode = -signal.SIGKILL
        return self.stdout, self.stderr


class PopenRecorder:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> FakeProcess:
        self.calls.append((args, kwargs))
        return self.process


class ClaudeExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binary = runner.ProviderBinaryIdentity(
            executable="/offline/fake/claude",
            realpath="/offline/fake/claude",
            sha256="b" * 64,
            version="offline-1.0",
        )
        self.policy = claude.make_cto_policy(
            self.binary,
            timeout_seconds=12.5,
            expected_tools=claude.CTO_TOOLSET,
            expected_agents=("general-purpose",),
            expected_mcp_servers=(),
            expected_plugins=(),
            secret_environment_keys=("ANTHROPIC_API_KEY",),
            termination_grace_seconds=0.25,
        )

    def invocation(
        self,
        *,
        opaque_id: str = OPAQUE_ID_A,
        prompt: str = "Perform the offline fixture check.",
        argv: Sequence[str] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> Any:
        return runner.ModelInvocation(
            argv=tuple(self.policy.argv_template if argv is None else argv),
            cwd=f"/opaque/session-{opaque_id}",
            prompt=prompt,
            environment=(
                {"PATH": "/offline/bin", "ANTHROPIC_API_KEY": "fake-secret-value"}
                if environment is None
                else dict(environment)
            ),
            task_visible_run_id=f"session-{opaque_id}",
        )

    def execute(
        self,
        *,
        records: Sequence[Mapping[str, Any]] | None = None,
        raw_stdout: bytes | None = None,
        stderr: bytes = b"provider diagnostic",
        returncode: int = 0,
        invocation: Any | None = None,
        process: FakeProcess | None = None,
        group_signal: Any | None = None,
    ) -> tuple[Any, PopenRecorder, FakeProcess]:
        stdout = jsonl(successful_records() if records is None else records)
        if raw_stdout is not None:
            stdout = raw_stdout
        fake_process = process or FakeProcess(
            stdout, stderr=stderr, returncode=returncode
        )
        recorder = PopenRecorder(fake_process)
        execution = claude.execute_claude(
            self.invocation() if invocation is None else invocation,
            self.policy,
            popen_factory=recorder,
            binary_identity_resolver=lambda _: self.binary,
            adapter_identity_resolver=lambda: self.policy.adapter_source,
            group_signal=(lambda _pgid, _sig: None) if group_signal is None else group_signal,
        )
        return execution, recorder, fake_process

    def test_01_exact_cto_argv_has_no_positional_prompt(self) -> None:
        expected = (
            "/offline/fake/claude",
            "--bare",
            "--print",
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
            "--verbose",
            "--forward-subagent-text",
            "--model",
            "claude-sonnet-5",
            "--effort",
            "medium",
            "--max-budget-usd",
            "2.0000000",
            "--max-turns",
            "32",
            "--no-session-persistence",
            "--no-chrome",
            "--tools",
            "Bash,Read,Edit,Write,Skill",
            "--allowedTools",
            "Bash",
            "Read",
            "Edit",
            "Write",
            "Skill",
            "--disallowedTools",
            "mcp__*",
            "--permission-mode",
            "bypassPermissions",
            "--add-dir",
            ".",
        )
        self.assertEqual(self.policy.argv_template, expected)
        self.assertNotIn("Perform the offline fixture check.", expected)
        self.assertFalse(hasattr(self.policy, "condition"))

    def test_02_exact_stdin_and_process_contract_with_replacement_env(self) -> None:
        prompt = "first line\nsecond line"
        invocation = self.invocation(prompt=prompt)
        execution, recorder, process = self.execute(invocation=invocation)

        self.assertEqual(len(recorder.calls), 1)
        args, kwargs = recorder.calls[0]
        self.assertEqual(args, (self.policy.argv_template,))
        self.assertEqual(kwargs["cwd"], invocation.cwd)
        self.assertEqual(kwargs["env"], dict(invocation.environment))
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["close_fds"], True)
        self.assertIs(kwargs["start_new_session"], True)
        self.assertEqual(process.communicate_calls[0]["input"], prompt.encode("utf-8"))
        self.assertFalse(process.communicate_calls[0]["input"].endswith(b"\n"))
        self.assertEqual(
            execution.invocation.stdin_sha256,
            hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(execution.invocation.stdin_length, len(prompt.encode("utf-8")))
        env_evidence = {item.key: item for item in execution.invocation.environment}
        self.assertEqual(env_evidence["ANTHROPIC_API_KEY"].value, "REDACTED")
        self.assertTrue(env_evidence["ANTHROPIC_API_KEY"].redacted)
        self.assertEqual(env_evidence["PATH"].value, "/offline/bin")
        self.assertNotIn("fake-secret-value", repr(execution.as_record()))

    def test_03_every_forbidden_flag_is_rejected_without_spawn(self) -> None:
        for forbidden in sorted(claude.PROHIBITED_FLAGS):
            with self.subTest(flag=forbidden):
                invocation = self.invocation(argv=self.policy.argv_template + (forbidden,))
                execution, recorder, _ = self.execute(invocation=invocation)
                self.assertEqual(recorder.calls, [])
                self.assertIn(f"prohibited_flag:{forbidden}", execution.validation_errors)
                self.assertFalse(execution.run_countable)

    def test_04_success_retains_raw_jsonl_process_and_sealed_evidence(self) -> None:
        records = successful_records()
        raw = jsonl(records)
        execution, _, _ = self.execute(raw_stdout=raw, stderr=b"exact-stderr")

        self.assertEqual(execution.raw_stdout, raw)
        self.assertEqual(execution.raw_stderr, b"exact-stderr")
        self.assertEqual(execution.raw_jsonl_records, tuple(records))
        self.assertEqual(execution.init_index, 0)
        self.assertEqual(execution.terminal_result_index, 2)
        self.assertEqual(execution.session_id, PROVIDER_SESSION_ID)
        self.assertEqual(execution.provider_binary_identity, self.binary)
        self.assertEqual(execution.adapter_identity, self.policy.adapter_source)
        self.assertEqual(execution.model_usage, records[-1]["modelUsage"])
        self.assertEqual(str(execution.total_cost_usd), "0.1250000")
        self.assertTrue(execution.evidence_sealed)
        self.assertTrue(execution.run_countable, execution.validation_errors)

    def test_05_duplicate_assistant_ids_are_retained_and_deduplicable(self) -> None:
        records = [
            init_record(),
            assistant_record("msg-duplicate"),
            assistant_record("msg-duplicate"),
            result_record(),
        ]
        execution, _, _ = self.execute(records=records)
        self.assertEqual(
            execution.assistant_message_ids, ("msg-duplicate", "msg-duplicate")
        )
        self.assertEqual(execution.unique_assistant_message_ids, ("msg-duplicate",))
        self.assertTrue(execution.run_countable, execution.validation_errors)

    def test_06_duplicate_json_key_is_strictly_rejected(self) -> None:
        raw = (
            jsonl([init_record()])
            + b'{"type":"assistant","session_id":"provider-session-001",'
            + b'"message":{"id":"first","id":"second",'
            + b'"model":"claude-sonnet-5"}}\n'
            + jsonl([result_record()])
        )
        execution, _, _ = self.execute(raw_stdout=raw)
        self.assertTrue(
            any(error.startswith("duplicate_json_key:1:id") for error in execution.validation_errors)
        )
        self.assertEqual(execution.raw_stdout, raw)
        self.assertFalse(execution.run_countable)

    def test_07_malformed_utf8_is_rejected_while_prior_records_remain(self) -> None:
        raw = jsonl([init_record()]) + b"\xff\n" + jsonl(
            [assistant_record(), result_record()]
        )
        execution, _, _ = self.execute(raw_stdout=raw)
        self.assertIn("malformed_utf8:1", execution.validation_errors)
        self.assertEqual(len(execution.raw_jsonl_records), 3)
        self.assertEqual(execution.raw_stdout, raw)
        self.assertFalse(execution.run_countable)

    def test_08_missing_and_multiple_init_are_rejected(self) -> None:
        cases = {
            "missing_init": [assistant_record(), result_record()],
            "multiple_init": [
                init_record(),
                init_record(),
                assistant_record(),
                result_record(),
            ],
        }
        for expected_error, records in cases.items():
            with self.subTest(error=expected_error):
                execution, _, _ = self.execute(records=records)
                self.assertIn(expected_error, execution.validation_errors)
                self.assertFalse(execution.run_countable)

    def test_09_missing_terminal_and_terminal_not_last_are_rejected(self) -> None:
        cases = {
            "missing_terminal_result": [init_record(), assistant_record()],
            "terminal_result_not_last": [
                init_record(),
                assistant_record(),
                result_record(),
                {"type": "system", "subtype": "status", "session_id": PROVIDER_SESSION_ID},
            ],
        }
        for expected_error, records in cases.items():
            with self.subTest(error=expected_error):
                execution, _, _ = self.execute(records=records)
                self.assertIn(expected_error, execution.validation_errors)
                self.assertFalse(execution.run_countable)

    def test_10_model_mismatch_is_rejected(self) -> None:
        cases = [
            [init_record(model="fallback-model"), assistant_record(), result_record()],
            [
                init_record(),
                assistant_record(model="fallback-model"),
                result_record(),
            ],
            [
                init_record(),
                assistant_record(),
                result_record(model_usage={"fallback-model": {"input_tokens": 1}}),
            ],
        ]
        for records in cases:
            with self.subTest(records=records):
                execution, _, _ = self.execute(records=records)
                self.assertTrue(
                    any("model" in error for error in execution.validation_errors),
                    execution.validation_errors,
                )
                self.assertFalse(execution.run_countable)

    def test_11_nonzero_exit_retains_all_raw_and_process_evidence(self) -> None:
        records = successful_records()
        raw = jsonl(records)
        execution, _, _ = self.execute(
            raw_stdout=raw,
            stderr=b"nonzero diagnostic",
            returncode=7,
        )
        self.assertEqual(execution.process.returncode, 7)
        self.assertEqual(execution.process.status, "EXITED")
        self.assertEqual(execution.process.pid, 4321)
        self.assertEqual(execution.process.pgid, 4321)
        self.assertEqual(execution.raw_stdout, raw)
        self.assertEqual(execution.raw_stderr, b"nonzero diagnostic")
        self.assertEqual(execution.raw_jsonl_records, tuple(records))
        self.assertIn("process_exit_nonzero:7", execution.validation_errors)
        self.assertTrue(execution.evidence_sealed)
        self.assertFalse(execution.run_countable)

    def test_12_timeout_retains_partial_evidence_and_terminates_process_group(self) -> None:
        partial_stdout = jsonl([init_record()])
        partial_stderr = b"partial diagnostic"
        process = TimeoutThenTerminateProcess(partial_stdout, partial_stderr)
        signals: list[tuple[int, int]] = []
        execution, _, _ = self.execute(
            process=process,
            group_signal=lambda pgid, sent_signal: signals.append((pgid, sent_signal)),
        )
        self.assertEqual(signals, [(9876, signal.SIGTERM)])
        self.assertTrue(execution.process.timed_out)
        self.assertEqual(execution.process.pid, 9876)
        self.assertEqual(execution.process.pgid, 9876)
        self.assertEqual(execution.process.status, "TIMED_OUT_TERMINATED")
        self.assertEqual(execution.process.returncode, -signal.SIGTERM)
        self.assertEqual(execution.raw_stdout, partial_stdout)
        self.assertEqual(execution.raw_stderr, partial_stderr)
        self.assertIn("process_timeout", execution.validation_errors)
        self.assertTrue(execution.evidence_sealed)
        self.assertFalse(execution.run_countable)

    def test_13_timeout_escalates_to_whole_process_group_sigkill(self) -> None:
        process = TimeoutThenKillProcess(jsonl([init_record()]), b"partial")
        signals: list[tuple[int, int]] = []
        execution, _, _ = self.execute(
            process=process,
            group_signal=lambda pgid, sent_signal: signals.append((pgid, sent_signal)),
        )
        self.assertEqual(
            signals,
            [(6789, signal.SIGTERM), (6789, signal.SIGKILL)],
        )
        self.assertEqual(execution.process.status, "TIMED_OUT_KILLED")
        self.assertEqual(
            execution.process.termination_method,
            "PROCESS_GROUP_SIGTERM_THEN_SIGKILL",
        )
        self.assertFalse(execution.run_countable)

    def test_14_session_inventory_permission_usage_and_cost_fail_closed(self) -> None:
        cases = {
            "session_ids_inconsistent": [
                init_record(),
                assistant_record(session_id="other-session"),
                result_record(),
            ],
            "init_tools_mismatch": successful_records(tools=("Read",)),
            "init_permission_mode_mismatch": [
                init_record(permissionMode="default"),
                assistant_record(),
                result_record(),
            ],
            "modelUsage_missing": [
                init_record(),
                assistant_record(),
                result_record(model_usage={}),
            ],
            "total_cost_usd_not_finite": [
                init_record(),
                assistant_record(),
                result_record(total_cost_usd="NaN"),
            ],
        }
        for expected_error, records in cases.items():
            with self.subTest(error=expected_error):
                execution, _, _ = self.execute(records=records)
                self.assertIn(expected_error, execution.validation_errors)
                self.assertFalse(execution.run_countable)

    def test_15_missing_main_loop_assistant_is_rejected(self) -> None:
        records = [
            init_record(),
            assistant_record(parent_tool_use_id="tool-parent"),
            result_record(),
        ]
        execution, _, _ = self.execute(records=records)
        self.assertIn("missing_main_loop_assistant", execution.validation_errors)
        self.assertFalse(execution.run_countable)

    def test_16_secret_output_is_redacted_and_never_countable(self) -> None:
        secret = "fake-secret-value"
        raw = jsonl(successful_records()) + secret.encode("utf-8")
        execution, _, _ = self.execute(raw_stdout=raw)
        self.assertNotIn(secret.encode("utf-8"), execution.raw_stdout)
        self.assertIn(b"REDACTED", execution.raw_stdout)
        self.assertIn(
            "secret_value_redacted_from_stdout", execution.validation_errors
        )
        self.assertNotIn(secret, repr(execution.as_record()))
        self.assertFalse(execution.run_countable)

    def test_17_provider_or_adapter_identity_mismatch_prevents_spawn(self) -> None:
        recorder = PopenRecorder(FakeProcess(jsonl(successful_records())))
        mismatched_binary = replace(self.binary, sha256="d" * 64)
        execution = claude.execute_claude(
            self.invocation(),
            self.policy,
            popen_factory=recorder,
            binary_identity_resolver=lambda _: mismatched_binary,
            adapter_identity_resolver=lambda: self.policy.adapter_source,
            group_signal=lambda _pgid, _sig: None,
        )
        self.assertEqual(recorder.calls, [])
        self.assertIn("provider_binary_identity_mismatch", execution.validation_errors)
        self.assertEqual(execution.process.status, "PREFLIGHT_REJECTED")
        self.assertFalse(execution.run_countable)

    def test_18_condition_neutral_comparison_allows_only_fable_carrier_delta(self) -> None:
        off_execution, _, _ = self.execute(records=successful_records())
        on_invocation = self.invocation(opaque_id=OPAQUE_ID_B)
        on_execution, _, _ = self.execute(
            records=successful_records(
                skills=("reference-skill", claude.FABLE_CARRIER_IDENTITY)
            ),
            invocation=on_invocation,
        )
        comparison = claude.compare_condition_neutral_evidence(
            off_execution, on_execution
        )
        self.assertTrue(comparison.passed, comparison.errors)
        self.assertTrue(
            comparison.checks["argv_equal_after_opaque_root_normalization"]
        )
        self.assertTrue(comparison.checks["prompt_hash_equal"])
        self.assertTrue(comparison.checks["environment_keys_equal"])
        self.assertTrue(comparison.checks["secret_fingerprints_equal"])
        self.assertTrue(
            comparison.checks["only_allowed_fable_carrier_inventory_delta"]
        )

    def test_19_condition_neutral_comparison_detects_actual_tool_asymmetry(self) -> None:
        left, _, _ = self.execute(records=successful_records())
        right, _, _ = self.execute(
            records=successful_records(tools=(*claude.CTO_TOOLSET, "Unexpected")),
            invocation=self.invocation(opaque_id=OPAQUE_ID_B),
        )
        comparison = claude.compare_condition_neutral_evidence(left, right)
        self.assertFalse(comparison.passed)
        self.assertFalse(comparison.checks["actual_init_tools_equal"])

    def test_20_tampering_breaks_seal_and_countability(self) -> None:
        execution, _, _ = self.execute()
        self.assertTrue(execution.run_countable)
        tampered = replace(execution, raw_stderr=b"tampered")
        self.assertFalse(tampered.evidence_sealed)
        self.assertFalse(tampered.run_countable)


    def test_21_post_kill_drain_is_bounded_and_preserves_latest_partial_output(self) -> None:
        class NeverFinishes(FakeProcess):
            def communicate(self, input: bytes | None = None, timeout: float | None = None) -> Any:
                self.communicate_calls.append({"input": input, "timeout": timeout})
                count = len(self.communicate_calls)
                self.returncode = None
                raise subprocess.TimeoutExpired(
                    cmd="offline-only",
                    timeout=timeout or 0,
                    output=jsonl([init_record()]) + f"partial-{count}".encode(),
                    stderr=f"diagnostic-{count}".encode(),
                )

        for signal_fails in (False, True):
            with self.subTest(signal_fails=signal_fails):
                process = NeverFinishes(b"")

                def send_signal(_pgid: int, _signal: int) -> None:
                    if signal_fails:
                        raise OSError("offline signal failure")

                execution, _, _ = self.execute(process=process, group_signal=send_signal)
                self.assertEqual(
                    [call["timeout"] for call in process.communicate_calls],
                    [self.policy.timeout_seconds, self.policy.termination_grace_seconds,
                     self.policy.termination_grace_seconds],
                )
                self.assertEqual(execution.process.status, "TIMED_OUT_TERMINATION_UNRESOLVED")
                self.assertTrue(execution.raw_stdout.endswith(b"partial-3"))
                self.assertEqual(execution.raw_stderr, b"diagnostic-3")
                self.assertTrue(execution.evidence_sealed)
                self.assertFalse(execution.run_countable)

    def test_22_session_ids_are_checked_on_every_stream_record(self) -> None:
        extra_records = [
            {"type": "system", "subtype": "status"},
            {"type": "user", "message": {"role": "user"}},
            {"type": "tool_progress"},
            {"type": "stream_event", "event": {}},
            assistant_record("msg-subagent", is_subagent=True),
            assistant_record("msg-parented", parent_tool_use_id="tool-parent"),
            assistant_record("msg-subagent-flag", subagent=True),
        ]
        for extra in extra_records:
            for session in ("other-session", "", None, 123, []):
                with self.subTest(record_type=extra["type"], session=session):
                    record = dict(extra, session_id=session)
                    execution, _, _ = self.execute(
                        records=[init_record(), assistant_record(), record, result_record()]
                    )
                    self.assertTrue(execution.evidence_sealed)
                    self.assertFalse(execution.run_countable)
                    self.assertTrue(any("session_id" in error for error in execution.validation_errors))
        missing_session = assistant_record("msg-subagent", is_subagent=True)
        del missing_session["session_id"]
        execution, _, _ = self.execute(
            records=[init_record(), assistant_record(), missing_session, result_record()]
        )
        self.assertFalse(execution.run_countable)
        matching = dict(extra_records[0], session_id=PROVIDER_SESSION_ID)
        execution, _, _ = self.execute(
            records=[init_record(), assistant_record(), matching, result_record()]
        )
        self.assertTrue(execution.run_countable, execution.validation_errors)

    def test_23_model_usage_rejects_invalid_numeric_evidence(self) -> None:
        invalid_usage = [
            None, [], {}, True, "10",
            {"input_tokens": -1}, {"input_tokens": True}, {"input_tokens": "10"},
            {"input_tokens": 1.5}, {"input_tokens": None},
            {"outputTokens": -1}, {"webSearchRequests": 0.5},
            {"input_tokens": float("nan")}, {"input_tokens": float("inf")},
            {"costUSD": -0.01}, {"costUSD": float("-inf")},
        ]
        for usage in invalid_usage:
            with self.subTest(usage=usage):
                records = [
                    init_record(), assistant_record(),
                    result_record(model_usage={claude.CTO_MODEL_ID: usage}),
                ]
                execution, _, _ = self.execute(records=records)
                self.assertEqual(execution.raw_stdout, jsonl(records))
                self.assertTrue(execution.evidence_sealed)
                self.assertFalse(execution.run_countable)

    def test_24_invalid_turn_counts_never_become_countable(self) -> None:
        for turns in (None, True, False, -1, 1.5, "1", {}, [], 33, float("inf")):
            with self.subTest(turns=turns):
                records = [init_record(), assistant_record(), result_record(num_turns=turns)]
                execution, _, _ = self.execute(records=records)
                self.assertEqual(execution.raw_stdout, jsonl(records))
                self.assertTrue(execution.evidence_sealed)
                self.assertFalse(execution.run_countable)

    def test_25_terminal_outcome_must_affirm_success(self) -> None:
        outcomes = [
            {"subtype": value}
            for value in (None, "", "error_max_turns", "error_during_execution", [], {}, True)
        ] + [{"is_error": value} for value in (None, 0, 1, "false", [], {})]
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                records = [init_record(), assistant_record(), result_record(**outcome)]
                execution, _, _ = self.execute(records=records)
                self.assertEqual(execution.raw_stdout, jsonl(records))
                self.assertIn("terminal_result_reports_error", execution.validation_errors)
                self.assertTrue(execution.evidence_sealed)
                self.assertFalse(execution.run_countable)

    def test_26_nonfinite_json_numbers_preserve_raw_failure_evidence(self) -> None:
        for number in (b"1e999", b"-1e999", b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(number=number):
                raw = (
                    jsonl([init_record(), assistant_record()])
                    + b'{"type":"system","subtype":"status","duration":' + number + b'}\n'
                    + jsonl([result_record()])
                )
                execution, _, _ = self.execute(raw_stdout=raw)
                self.assertEqual(execution.raw_stdout, raw)
                self.assertEqual(len(execution.raw_jsonl_records), 3)
                self.assertTrue(any(error.startswith("malformed_jsonl:2:") for error in execution.validation_errors))
                self.assertTrue(execution.evidence_sealed)
                self.assertFalse(execution.run_countable)

    def test_27_escaped_surrogates_and_deep_json_preserve_failure_evidence(self) -> None:
        malformed = [
            jsonl([assistant_record(extra="\ud800")]),
            b'{"type":"assistant","\\ud800":1,"\\ud800":2}\n',
            b'{"type":"system","nested":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}\n",
        ]
        for record in malformed:
            with self.subTest(record_length=len(record)):
                raw = jsonl([init_record()]) + record + jsonl([assistant_record(), result_record()])
                execution, _, _ = self.execute(raw_stdout=raw)
                self.assertEqual(execution.raw_stdout, raw)
                self.assertTrue(execution.evidence_sealed)
                self.assertFalse(execution.run_countable)

    def test_28_secret_aliases_are_redacted_without_losing_environment_fingerprints(self) -> None:
        environment = {
            "ANTHROPIC_API_KEY": "synthetic-secret-long",
            "TOKEN": "synthetic-secret",
            "COPY": "prefix:synthetic-secret-long",
            "HTTP_AUTHORIZATION": "synthetic-bearer",
            "PATH": "/offline/bin",
        }
        execution, _, _ = self.execute(invocation=self.invocation(environment=environment))
        evidence = {item.key: item for item in execution.invocation.environment}
        for key in ("ANTHROPIC_API_KEY", "TOKEN", "COPY", "HTTP_AUTHORIZATION"):
            self.assertTrue(evidence[key].redacted)
            self.assertEqual(evidence[key].value, "REDACTED")
            self.assertEqual(evidence[key].sha256_fingerprint, hashlib.sha256(environment[key].encode()).hexdigest())
        serialized = json.dumps(execution.as_record(), ensure_ascii=False)
        self.assertNotIn("synthetic-secret", serialized)
        self.assertNotIn("synthetic-bearer", serialized)
        self.assertEqual(evidence["PATH"].value, "/offline/bin")
        self.assertTrue(execution.run_countable, execution.validation_errors)

    def test_29_json_escaped_secrets_are_redacted_in_values_keys_and_stderr(self) -> None:
        for secret in ('synthetic-"quoted"\\line\n雪', "synthetic-secret"):
            with self.subTest(secret_length=len(secret)):
                records = [
                    init_record(), assistant_record(extra={secret: secret}), result_record()
                ]
                raw = jsonl(records).replace(b"synthetic", b"syn\\u0074hetic")
                stderr = json.dumps({"diagnostic": secret}).encode()
                execution, _, _ = self.execute(
                    raw_stdout=raw, stderr=stderr,
                    invocation=self.invocation(environment={"ANTHROPIC_API_KEY": secret}),
                )
                self.assertNotIn(secret, json.dumps(execution.as_record(), ensure_ascii=False))
                self.assertNotIn(secret, execution.raw_stdout.decode())
                self.assertNotIn(secret, execution.raw_stderr.decode())
                self.assertIn("secret_value_redacted_from_stdout", execution.validation_errors)
                self.assertIn("secret_value_redacted_from_stderr", execution.validation_errors)
                self.assertEqual(len(execution.raw_jsonl_records), 3)
                self.assertTrue(execution.evidence_sealed)
                self.assertFalse(execution.run_countable)

    def test_30_provider_metadata_and_rejected_argv_do_not_leak_environment_secrets(self) -> None:
        secret = "fake-secret-value"
        self.binary = replace(self.binary, version=secret)
        self.policy = replace(self.policy, provider_binary=self.binary)
        execution, _, _ = self.execute()
        self.assertNotIn(secret, json.dumps(execution.as_record()))
        self.assertIn("secret_value_redacted_from_provider_identity", execution.validation_errors)
        self.assertFalse(execution.run_countable)
        execution, recorder, _ = self.execute(
            invocation=self.invocation(argv=self.policy.argv_template + (secret,))
        )
        self.assertEqual(recorder.calls, [])
        self.assertNotIn(secret, json.dumps(execution.as_record()))
        self.assertIn("secret_value_redacted_from_invocation", execution.validation_errors)
        self.assertTrue(execution.evidence_sealed)

    def test_31_start_and_identity_failures_preserve_safe_sealed_evidence(self) -> None:
        def fail(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("fake-secret-value")

        for boundary in ("popen_factory", "binary_identity_resolver", "adapter_identity_resolver"):
            with self.subTest(boundary=boundary):
                recorder = PopenRecorder(FakeProcess(jsonl(successful_records())))
                dependencies = {
                    "popen_factory": recorder,
                    "binary_identity_resolver": lambda _: self.binary,
                    "adapter_identity_resolver": lambda: self.policy.adapter_source,
                    "group_signal": lambda _pgid, _sig: None,
                }
                dependencies[boundary] = fail
                execution = claude.execute_claude(self.invocation(), self.policy, **dependencies)
                self.assertEqual(recorder.calls, [])
                self.assertTrue(execution.evidence_sealed)
                self.assertFalse(execution.run_countable)
                self.assertNotIn("fake-secret-value", json.dumps(execution.as_record()))

    def test_32_post_sigterm_exception_retains_newly_captured_bytes(self) -> None:
        class DrainFailure(FakeProcess):
            def communicate(self, input: bytes | None = None, timeout: float | None = None) -> Any:
                self.communicate_calls.append({"input": input, "timeout": timeout})
                if len(self.communicate_calls) == 1:
                    raise subprocess.TimeoutExpired(
                        "offline", timeout or 0, output=jsonl([init_record()]), stderr=b"first"
                    )
                error = OSError("offline drain failure")
                error.output = jsonl([init_record(), assistant_record()])
                error.stderr = b"newly-captured"
                raise error

        execution, _, _ = self.execute(process=DrainFailure(b""))
        self.assertEqual(execution.raw_stdout, jsonl([init_record(), assistant_record()]))
        self.assertEqual(execution.raw_stderr, b"newly-captured")
        self.assertTrue(execution.evidence_sealed)
        self.assertFalse(execution.run_countable)

    def test_33_binary_version_probe_is_bounded_and_fully_injected(self) -> None:
        calls: list[Any] = []

        def fake_version(argv: Any, **kwargs: Any) -> Any:
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout=b"offline-version\n", stderr=b"")

        source = Path(__file__).resolve()
        identity = claude.resolve_provider_binary_identity(
            str(source), version_runner=fake_version
        )
        self.assertEqual(identity.sha256, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(identity.version, "offline-version")
        self.assertEqual(calls[0][0], [str(source), "--version"])
        self.assertEqual(calls[0][1]["timeout"], 10.0)
        self.assertEqual(calls[0][1]["env"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
