#!/usr/bin/env python3
"""Offline acceptance tests for the canonical Claude executor."""

from __future__ import annotations

import subprocess
import sys
import json
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock


# Importing sibling modules must not leave __pycache__ in the repository.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_executor  # noqa: E402
import runner  # noqa: E402


REQUIRED_VERSION = "2.1.245 (Claude Code)"
REAL_PROVIDER = "/Users/kelvin/.local/bin/claude"
OFFLINE_MODEL = "offline-model"


def write_fake_provider(root: Path, version: str = REQUIRED_VERSION) -> Path:
    provider = root / "fake claude provider"
    script = f"""#!/bin/sh
if [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then
  printf '%s\\n' '{version}'
  exit 0
fi
if [ -n "${{FAKE_ARGV_PATH:-}}" ]; then
  : > "$FAKE_ARGV_PATH"
  for arg in "$@"; do
    printf '%s\\n' "$arg" >> "$FAKE_ARGV_PATH"
  done
fi
if [ -n "${{FAKE_STDIN_PATH:-}}" ]; then
  /bin/cat > "$FAKE_STDIN_PATH"
else
  /bin/cat > /dev/null
fi
if [ -n "${{FAKE_EXECUTED_PATH:-}}" ]; then
  printf 'executed\\n' > "$FAKE_EXECUTED_PATH"
fi
case "${{FAKE_MODE:-valid}}" in
  valid)
    printf '%s\\n' '{{"type":"system","subtype":"init","session_id":"provider-session","model":"offline-model"}}'
    printf '%s\\n' '{{"type":"assistant","session_id":"provider-session","message":{{"id":"message-1","model":"offline-model"}}}}'
    printf '%s\\n' '{{"type":"result","subtype":"success","session_id":"provider-session","modelUsage":{{"offline-model":{{"input_tokens":1,"output_tokens":1}}}},"total_cost_usd":"0.1250000","num_turns":1}}'
    ;;
  ordered)
    printf '%s\\n' '{{"type":"system","subtype":"init","session_id":"provider-session","model":"offline-model","sequence":1}}'
    printf '%s\\n' '{{"type":"assistant","session_id":"provider-session","message":{{"id":"message-1","model":"offline-model"}},"sequence":2}}'
    printf '%s\\n' '{{"type":"result","subtype":"success","session_id":"provider-session","modelUsage":{{"offline-model":{{"input_tokens":1,"output_tokens":1}}}},"total_cost_usd":"0.1250000","num_turns":1,"sequence":3}}'
    ;;
  surfaces-complete)
    printf '%s\\n' '{{"type":"system","subtype":"init","session_id":"provider-session","model":"offline-model","skills":["reference-skill"],"output_style":"default","hooks":[],"agents_md":[],"user_rules":[],"instruction_sources":["cli-default"]}}'
    printf '%s\\n' '{{"type":"assistant","session_id":"provider-session","message":{{"id":"message-1","model":"offline-model"}}}}'
    printf '%s\\n' '{{"type":"result","subtype":"success","session_id":"provider-session","modelUsage":{{"offline-model":{{"input_tokens":1,"output_tokens":1}}}},"total_cost_usd":"0.1250000","num_turns":1}}'
    ;;
  surfaces-ambiguous)
    printf '%s\\n' '{{"type":"system","subtype":"init","session_id":"provider-session","model":"offline-model","skills":["reference-skill"],"output_style":"default","outputStyle":"default","hooks":[],"agents_md":[],"user_rules":[],"instruction_sources":["cli-default"]}}'
    printf '%s\\n' '{{"type":"assistant","session_id":"provider-session","message":{{"id":"message-1","model":"offline-model"}}}}'
    printf '%s\\n' '{{"type":"result","subtype":"success","session_id":"provider-session","modelUsage":{{"offline-model":{{"input_tokens":1,"output_tokens":1}}}},"total_cost_usd":"0.1250000","num_turns":1}}'
    ;;
  malformed)
    printf '%s\\n' '{{"sequence":1}}' '{{not-json}}'
    ;;
  nonzero)
    printf '%s\\n' '{{"sequence":1}}'
    printf '%s\\n' 'provider failed' >&2
    exit 23
    ;;
  stderr-json)
    printf '%s\\n' '{{"type":"system","subtype":"init","session_id":"provider-session","model":"offline-model"}}'
    printf '%s\\n' '{{"type":"assistant","session_id":"provider-session","message":{{"id":"message-1","model":"offline-model"}}}}'
    printf '%s\\n' '{{"type":"result","subtype":"success","session_id":"provider-session","modelUsage":{{"offline-model":{{"input_tokens":1,"output_tokens":1}}}},"total_cost_usd":"0.1250000","num_turns":1}}'
    printf '%s\\n' '{{"source":"stderr"}}' >&2
    ;;
  *)
    printf '%s\\n' 'unknown fake mode' >&2
    exit 24
    ;;
esac
"""
    provider.write_text(script, encoding="utf-8")
    provider.chmod(0o700)
    return provider


def make_invocation(
    provider: Path | str,
    cwd: Path,
    *,
    argv_tail: tuple[str, ...] = (
        "--model", OFFLINE_MODEL, "--output-format", "stream-json"
    ),
    prompt: str = "offline prompt",
    environment: dict[str, str] | None = None,
) -> runner.ModelInvocation:
    return runner.ModelInvocation(
        argv=(str(provider), *argv_tail),
        cwd=str(cwd),
        prompt=prompt,
        environment=dict(environment or {}),
        task_visible_run_id="session-0123456789abcdef0123456789abcdef",
    )


def valid_provider_events() -> list[dict[str, Any]]:
    return [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "provider-session",
            "model": OFFLINE_MODEL,
        },
        {
            "type": "assistant",
            "session_id": "provider-session",
            "message": {"id": "message-1", "model": OFFLINE_MODEL},
        },
        {
            "type": "result",
            "subtype": "success",
            "session_id": "provider-session",
            "modelUsage": {
                OFFLINE_MODEL: {"input_tokens": 1, "output_tokens": 1}
            },
            "total_cost_usd": "0.1250000",
            "num_turns": 1,
        },
    ]


def encode_provider_events(events: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


def offline_version_probe() -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        ["offline-provider", "--version"],
        0,
        (REQUIRED_VERSION + "\n").encode("utf-8"),
        b"",
    )


class ClaudeExecutorOfflineTests(unittest.TestCase):
    def test_01_argv_is_passed_without_shell_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            argv_record = root / "argv.txt"
            stdin_record = root / "stdin.txt"
            sentinel = root / "shell-expanded"
            literal_arguments = (
                "--model",
                OFFLINE_MODEL,
                "literal;touch",
                "*.json",
                f"$(touch {sentinel})",
                "value with spaces",
            )
            invocation = make_invocation(
                provider,
                root,
                argv_tail=literal_arguments,
                environment={
                    "FAKE_ARGV_PATH": str(argv_record),
                    "FAKE_STDIN_PATH": str(stdin_record),
                },
            )

            result = claude_executor.ClaudeExecutor(str(provider))(invocation)

            self.assertEqual(
                argv_record.read_text(encoding="utf-8").splitlines(),
                list(literal_arguments),
            )
            self.assertFalse(sentinel.exists())
            self.assertEqual(result.argv, invocation.argv)

    def test_02_stdin_receives_prompt_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            stdin_record = root / "stdin.bin"
            prompt = "first line\n第二行\nno-added-newline"
            invocation = make_invocation(
                provider,
                root,
                prompt=prompt,
                environment={"FAKE_STDIN_PATH": str(stdin_record)},
            )

            claude_executor.ClaudeExecutor(str(provider))(invocation)

            self.assertEqual(stdin_record.read_bytes(), prompt.encode("utf-8"))

    def test_03_valid_jsonl_events_remain_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(
                provider,
                root,
                environment={"FAKE_MODE": "ordered"},
            )

            result = claude_executor.ClaudeExecutor(str(provider))(invocation)

            self.assertEqual([event["sequence"] for event in result], [1, 2, 3])
            self.assertEqual(result.returncode, 0)

    def test_04_malformed_required_jsonl_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(
                provider,
                root,
                environment={"FAKE_MODE": "malformed"},
            )

            with self.assertRaises(claude_executor.ProviderOutputError) as caught:
                claude_executor.ClaudeExecutor(str(provider))(invocation)

            self.assertEqual(caught.exception.line_number, 2)

    def test_05_nonzero_child_exit_is_surfaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(
                provider,
                root,
                environment={"FAKE_MODE": "nonzero"},
            )

            with self.assertRaises(claude_executor.ProviderProcessError) as caught:
                claude_executor.ClaudeExecutor(str(provider))(invocation)

            self.assertEqual(caught.exception.returncode, 23)
            self.assertEqual(caught.exception.stderr, b"provider failed\n")
            self.assertIn(b'"sequence":1', caught.exception.stdout)

    def test_06_stderr_is_never_treated_as_a_result_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(
                provider,
                root,
                environment={"FAKE_MODE": "stderr-json"},
            )

            result = claude_executor.ClaudeExecutor(str(provider))(invocation)

            self.assertEqual(len(result), 3)
            self.assertEqual(result[0]["subtype"], "init")
            self.assertEqual(result[-1]["type"], "result")
            self.assertEqual(result.stderr, b'{"source":"stderr"}\n')

    def test_07_version_mismatch_blocks_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root, "0.0.0 (Fake Claude)")
            executed = root / "executed.txt"
            invocation = make_invocation(
                provider,
                root,
                environment={"FAKE_EXECUTED_PATH": str(executed)},
            )

            with self.assertRaises(claude_executor.ProviderVersionError) as caught:
                claude_executor.ClaudeExecutor(str(provider))(invocation)

            self.assertEqual(caught.exception.observed_version, "0.0.0 (Fake Claude)")
            self.assertFalse(executed.exists())

    def test_08_matching_fake_version_permits_fake_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            executed = root / "executed.txt"
            invocation = make_invocation(
                provider,
                root,
                environment={"FAKE_EXECUTED_PATH": str(executed)},
            )
            executor = claude_executor.ClaudeExecutor(str(provider))

            self.assertEqual(executor.verify_provider_version(), REQUIRED_VERSION)
            result = executor(invocation)

            self.assertTrue(executed.is_file())
            self.assertEqual(result[0]["type"], "system")

    def test_09_model_invocation_is_not_silently_reconstructed(self) -> None:
        lookalike = SimpleNamespace(
            argv=("/offline/fake-claude", "--output-format", "stream-json"),
            cwd="/tmp",
            prompt="offline prompt",
            environment={},
            task_visible_run_id="session-0123456789abcdef0123456789abcdef",
        )
        executor = claude_executor.ClaudeExecutor("/offline/fake-claude")

        with mock.patch.object(claude_executor.subprocess, "run") as run:
            with self.assertRaises(claude_executor.InvocationContractError):
                executor(lookalike)  # type: ignore[arg-type]

        run.assert_not_called()

    def test_10_real_provider_inference_is_never_invoked(self) -> None:
        offline_provider = "/offline/fake-claude"
        launched_argv: list[tuple[str, ...]] = []

        def fake_launch(argv: tuple[str, ...], **kwargs: Any) -> "FakeLaunchedProcess":
            launched_argv.append(tuple(argv))
            self.assertEqual(argv[0], offline_provider)
            self.assertNotEqual(argv[0], REAL_PROVIDER)
            return FakeLaunchedProcess(stdout=encode_provider_events(valid_provider_events()))

        with tempfile.TemporaryDirectory() as temporary:
            invocation = make_invocation(offline_provider, Path(temporary))
            result = claude_executor.ClaudeExecutor(
                offline_provider,
                launch=fake_launch,
                version_probe=offline_version_probe,
            )(invocation)

        self.assertEqual(len(launched_argv), 1)
        self.assertEqual(result[0]["subtype"], "init")
        self.assertTrue(all(argv[0] != REAL_PROVIDER for argv in launched_argv))

    def test_11_provider_identity_binds_executable_and_observed_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(provider, root)

            result = claude_executor.ClaudeExecutor(str(provider))(invocation)

            self.assertEqual(result.provider_identity, f"{provider}@{REQUIRED_VERSION}")

    def test_12_provider_identity_is_stable_across_off_and_on_arms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            executor = claude_executor.ClaudeExecutor(str(provider))
            off_invocation = make_invocation(provider, root, prompt="off arm prompt")
            on_invocation = make_invocation(provider, root, prompt="on arm prompt")

            off_result = executor(off_invocation)
            on_result = executor(on_invocation)

            self.assertEqual(off_result.provider_identity, on_result.provider_identity)

    def test_13_instruction_surface_evidence_resolves_all_five_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(
                provider, root, environment={"FAKE_MODE": "surfaces-complete"}
            )

            result = claude_executor.ClaudeExecutor(str(provider))(invocation)
            evidence = claude_executor.instruction_surface_evidence(result)

            self.assertTrue(
                evidence.instruction_surfaces_resolved, evidence.instruction_surface_errors
            )
            self.assertEqual(set(evidence.instruction_surfaces), set(runner.INSTRUCTION_SURFACES))

    def test_14_instruction_surface_evidence_fails_closed_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            # Default "valid" mode has a semantically complete provider stream
            # but no instruction surfaces -- exactly the donor bug shape this
            # gate must reject.
            invocation = make_invocation(provider, root)

            result = claude_executor.ClaudeExecutor(str(provider))(invocation)
            evidence = claude_executor.instruction_surface_evidence(result)

            self.assertFalse(evidence.instruction_surfaces_resolved)
            self.assertEqual(
                len(evidence.instruction_surface_errors), len(runner.INSTRUCTION_SURFACES)
            )

    def test_15_instruction_surface_evidence_rejects_ambiguous_alias_even_when_equal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(
                provider, root, environment={"FAKE_MODE": "surfaces-ambiguous"}
            )

            result = claude_executor.ClaudeExecutor(str(provider))(invocation)
            evidence = claude_executor.instruction_surface_evidence(result)

            self.assertFalse(evidence.instruction_surfaces_resolved)
            self.assertIn(
                "ambiguous_instruction_surface_alias:output_style",
                evidence.instruction_surface_errors,
            )


class FakeLaunchedProcess:
    """Stands in for a Popen an injected launcher would return."""

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = 0,
        responses: list[Any] | None = None,
        pid: int = 12345,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.responses = list(responses or [])
        self.pid = pid
        self.communicate_calls: list[tuple[bytes | None, float | None]] = []

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        self.communicate_calls.append((input, timeout))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            if not self.responses:
                self.returncode = -signal.SIGKILL
            return response
        return self.stdout, self.stderr

    def poll(self) -> int | None:
        return self.returncode


class ClaudeExecutorSemanticStreamTests(unittest.TestCase):
    def run_stream(
        self, events: list[dict[str, Any]], model: str = OFFLINE_MODEL
    ) -> claude_executor.ClaudeExecutionResult:
        invocation = make_invocation(
            "/offline/fake-claude",
            Path("/tmp"),
            argv_tail=("--model", model, "--output-format", "stream-json"),
        )
        process = FakeLaunchedProcess(stdout=encode_provider_events(events))
        executor = claude_executor.ClaudeExecutor(
            "/offline/fake-claude",
            launch=lambda argv, **kwargs: process,
            version_probe=offline_version_probe,
        )
        return executor(invocation)

    def assert_stream_rejected(
        self,
        events: list[dict[str, Any]],
        expected_error: str,
        model: str = OFFLINE_MODEL,
    ) -> None:
        with self.assertRaises(claude_executor.ProviderOutputError) as caught:
            self.run_stream(events, model=model)
        self.assertIn(expected_error, caught.exception.validation_errors)

    def test_valid_provider_stream_is_semantically_accepted(self) -> None:
        result = self.run_stream(valid_provider_events())

        self.assertEqual(len(result), 3)
        self.assertEqual(result[-1]["type"], "result")

    def test_model_family_alias_accepts_its_resolved_model_id(self) -> None:
        resolved_models = {
            "sonnet": "claude-sonnet-5",
            "sonnet[1m]": "claude-sonnet-5",
            "opus": "anthropic.claude-opus-5",
            "haiku": "claude-3-5-haiku-20241022",
        }
        for alias, resolved_model in resolved_models.items():
            with self.subTest(alias=alias):
                events = valid_provider_events()
                events[0]["model"] = resolved_model
                events[1]["message"]["model"] = resolved_model
                events[2]["modelUsage"] = {
                    resolved_model: {"input_tokens": 1, "output_tokens": 1}
                }

                result = self.run_stream(events, model=alias)

                self.assertEqual(result[0]["model"], resolved_model)

    def test_model_family_alias_rejects_a_different_resolved_family(self) -> None:
        events = valid_provider_events()
        events[0]["model"] = "claude-opus-5"
        events[1]["message"]["model"] = "claude-opus-5"
        events[2]["modelUsage"] = {
            "claude-opus-5": {"input_tokens": 1, "output_tokens": 1}
        }

        self.assert_stream_rejected(events, "init_model_mismatch", model="sonnet")

    def test_stream_rejects_missing_or_misordered_protocol_records(self) -> None:
        events = valid_provider_events()
        self.assert_stream_rejected(events[1:], "missing_init")
        self.assert_stream_rejected(events[:2], "missing_terminal_result")
        self.assert_stream_rejected([*events, dict(events[0])], "terminal_result_not_last")
        self.assert_stream_rejected([events[0], events[0], *events[1:]], "multiple_init")
        self.assert_stream_rejected([*events, events[-1]], "multiple_terminal_result")

    def test_stream_rejects_session_model_and_terminal_usage_drift(self) -> None:
        events = valid_provider_events()
        events[1]["session_id"] = "different-session"
        self.assert_stream_rejected(events, "session_ids_inconsistent")

        events = valid_provider_events()
        events[0]["model"] = "different-model"
        self.assert_stream_rejected(events, "init_model_mismatch")

        events = valid_provider_events()
        events[1]["message"]["model"] = "different-model"
        self.assert_stream_rejected(events, "assistant_model_mismatch:1")

        events = valid_provider_events()
        del events[1]["message"]["id"]
        self.assert_stream_rejected(events, "assistant_message_id_missing:1")

        events = valid_provider_events()
        events[2]["subtype"] = "error"
        events[2]["is_error"] = True
        self.assert_stream_rejected(events, "terminal_result_reports_error")

        events = valid_provider_events()
        events[2]["modelUsage"] = {"different-model": {"input_tokens": 1}}
        self.assert_stream_rejected(events, "modelUsage_expected_model_missing")

        events = valid_provider_events()
        events[2].pop("modelUsage")
        self.assert_stream_rejected(events, "modelUsage_missing")

        events = valid_provider_events()
        events[2]["modelUsage"][OFFLINE_MODEL]["input_tokens"] = -1
        self.assert_stream_rejected(events, "modelUsage_invalid")

        events = valid_provider_events()
        events[2]["modelUsage"][OFFLINE_MODEL]["input_tokens"] = 1.5
        self.assert_stream_rejected(events, "modelUsage_invalid")

        events = valid_provider_events()
        events[2]["total_cost_usd"] = "NaN"
        self.assert_stream_rejected(events, "total_cost_usd_invalid")


class ClaudeExecutorTimeoutTests(unittest.TestCase):
    def test_timeout_sends_term_then_kill_and_keeps_partial_output(self) -> None:
        invocation = make_invocation("/offline/fake-claude", Path("/tmp"))
        process = FakeLaunchedProcess(
            returncode=None,
            responses=[
                subprocess.TimeoutExpired(
                    invocation.argv, 0.05, output=b"partial stdout", stderr=b"partial stderr"
                ),
                subprocess.TimeoutExpired(
                    invocation.argv, 0.01, output=b"partial stdout", stderr=b"partial stderr"
                ),
                (b"partial stdout\nfinal", b"partial stderr\nfinal"),
            ],
        )
        executor = claude_executor.ClaudeExecutor(
            "/offline/fake-claude",
            launch=lambda argv, **kwargs: process,
            version_probe=offline_version_probe,
            timeout_seconds=0.05,
            termination_grace_seconds=0.01,
        )
        group_killed = False

        def kill_group(_process_group: int, signum: int) -> None:
            nonlocal group_killed
            if signum == signal.SIGKILL:
                group_killed = True
            elif signum == 0 and group_killed:
                raise ProcessLookupError

        with mock.patch.object(
            claude_executor.os, "killpg", side_effect=kill_group
        ) as killpg:
            with self.assertRaises(claude_executor.ProviderProcessError) as caught:
                executor(invocation)

        self.assertTrue(caught.exception.timed_out)
        self.assertEqual(caught.exception.signals_sent, ("SIGTERM", "SIGKILL"))
        self.assertEqual(caught.exception.termination_method, "SIGTERM->SIGKILL")
        self.assertTrue(caught.exception.cleanup_complete)
        self.assertEqual(caught.exception.stdout, b"partial stdout\nfinal")
        self.assertEqual(caught.exception.stderr, b"partial stderr\nfinal")
        sent_signals = [
            call.args[1]
            for call in killpg.call_args_list
            if call.args[1] in (signal.SIGTERM, signal.SIGKILL)
        ]
        self.assertEqual(sent_signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertIn(0, [call.args[1] for call in killpg.call_args_list])
        self.assertEqual(
            [timeout for _input, timeout in process.communicate_calls],
            [0.05, 0.01, 0.01],
        )

    def test_timeout_terminates_the_fake_provider_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready = root / "child-ready"
            terminated = root / "child-term"
            parent_terminated = root / "parent-term"
            child_pid_file = root / "child.pid"
            provider = root / "hanging-fake-provider"
            child_code = "; ".join(
                [
                    "import pathlib,signal,time",
                    f"signal.signal(signal.SIGTERM, lambda *_: pathlib.Path({str(terminated)!r}).write_text('term'))",
                    f"pathlib.Path({str(ready)!r}).touch()",
                    "time.sleep(30)",
                ]
            )
            provider_script = "\n".join(
                [
                    f"#!{sys.executable}",
                    "import pathlib,signal,subprocess,sys,time",
                    "if sys.argv[1:] == ['--version']:",
                    f"    print({REQUIRED_VERSION!r})",
                    "    raise SystemExit(0)",
                    f"signal.signal(signal.SIGTERM, lambda *_: pathlib.Path({str(parent_terminated)!r}).write_text('term'))",
                    f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}], stdout=sys.stdout, stderr=sys.stderr)",
                    f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))",
                    f"while not pathlib.Path({str(ready)!r}).exists(): time.sleep(0.01)",
                    "print('partial provider stdout', flush=True)",
                    "print('partial provider stderr', file=sys.stderr, flush=True)",
                    "child.wait()",
                ]
            )
            provider.write_text(provider_script + "\n", encoding="utf-8")
            provider.chmod(0o700)
            launched: list[subprocess.Popen[bytes]] = []

            def launch(argv: tuple[str, ...], **kwargs: Any) -> subprocess.Popen[bytes]:
                process = subprocess.Popen(argv, **kwargs)
                launched.append(process)
                return process

            executor = claude_executor.ClaudeExecutor(
                str(provider),
                launch=launch,
                version_probe=offline_version_probe,
                timeout_seconds=5.0,
                termination_grace_seconds=0.15,
            )
            invocation = make_invocation(
                provider,
                root,
                environment={
                    "FAKE_READY_MARKER": str(ready),
                    "FAKE_TERM_MARKER": str(terminated),
                },
            )

            started_at = time.monotonic()
            try:
                with self.assertRaises(claude_executor.ProviderProcessError) as caught:
                    executor(invocation)
                error = caught.exception
                elapsed = time.monotonic() - started_at
                self.assertTrue(error.timed_out)
                self.assertEqual(
                    error.signals_sent,
                    ("SIGTERM", "SIGKILL"),
                    error.termination_method,
                )
                self.assertTrue(error.cleanup_complete)
                self.assertIn("termination=SIGTERM->SIGKILL", str(error))
                self.assertIn("cleanup_complete=true", str(error))
                self.assertLess(elapsed, 7.0)
                self.assertIn(b"partial provider stdout", error.stdout)
                self.assertIn(b"partial provider stderr", error.stderr)
                self.assertTrue(terminated.is_file())
                self.assertTrue(parent_terminated.is_file())
                child_pid = int(child_pid_file.read_text(encoding="utf-8"))
                child_running = True
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    status = subprocess.run(
                        ["ps", "-o", "stat=", "-p", str(child_pid)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        text=True,
                    )
                    process_state = status.stdout.strip()
                    child_running = bool(process_state) and not process_state.startswith("Z")
                    if not child_running:
                        break
                    time.sleep(0.02)
                self.assertFalse(child_running, f"provider child {child_pid} remained alive")
            finally:
                if launched:
                    try:
                        os.killpg(launched[-1].pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    if launched[-1].poll() is None:
                        launched[-1].communicate(timeout=2)

    def test_timeout_kills_surviving_child_after_provider_closes_pipes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready = root / "child-ready"
            child_pid_file = root / "child.pid"
            provider = root / "closing-pipes-fake-provider"
            child_code = "; ".join(
                [
                    "import os,pathlib,signal,time",
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                    f"pathlib.Path({str(ready)!r}).touch()",
                    "[os.close(fd) for fd in (0, 1, 2)]",
                    "time.sleep(30)",
                ]
            )
            provider_script = "\n".join(
                [
                    f"#!{sys.executable}",
                    "import pathlib,subprocess,sys,time",
                    "if sys.argv[1:] == ['--version']:",
                    f"    print({REQUIRED_VERSION!r})",
                    "    raise SystemExit(0)",
                    f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}], stdout=sys.stdout, stderr=sys.stderr)",
                    f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))",
                    f"while not pathlib.Path({str(ready)!r}).exists(): time.sleep(0.005)",
                    "print('partial provider stdout', flush=True)",
                    "print('partial provider stderr', file=sys.stderr, flush=True)",
                    "time.sleep(30)",
                ]
            )
            provider.write_text(provider_script + "\n", encoding="utf-8")
            provider.chmod(0o700)
            launched: list[subprocess.Popen[bytes]] = []

            def launch(argv: tuple[str, ...], **kwargs: Any) -> subprocess.Popen[bytes]:
                process = subprocess.Popen(argv, **kwargs)
                launched.append(process)
                return process

            executor = claude_executor.ClaudeExecutor(
                str(provider),
                launch=launch,
                version_probe=offline_version_probe,
                timeout_seconds=2.0,
                termination_grace_seconds=0.1,
            )
            invocation = make_invocation(provider, root)

            try:
                with self.assertRaises(claude_executor.ProviderProcessError) as caught:
                    executor(invocation)
                error = caught.exception
                self.assertTrue(error.timed_out)
                self.assertEqual(error.signals_sent, ("SIGTERM", "SIGKILL"))
                self.assertTrue(error.cleanup_complete)
                self.assertIn(b"partial provider stdout", error.stdout)
                self.assertIn(b"partial provider stderr", error.stderr)
                self.assertTrue(ready.is_file())
                self.assertTrue(child_pid_file.is_file())

                child_pid = int(child_pid_file.read_text(encoding="utf-8"))
                deadline = time.monotonic() + 2.0
                child_running = True
                while time.monotonic() < deadline:
                    status = subprocess.run(
                        ["ps", "-o", "stat=", "-p", str(child_pid)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        text=True,
                    )
                    process_state = status.stdout.strip()
                    child_running = bool(process_state) and not process_state.startswith("Z")
                    if not child_running:
                        break
                    time.sleep(0.02)
                self.assertFalse(child_running, f"provider child {child_pid} remained alive")
            finally:
                if launched:
                    try:
                        os.killpg(launched[-1].pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    if launched[-1].poll() is None:
                        launched[-1].communicate(timeout=2)


class ClaudeExecutorInjectedLaunchTests(unittest.TestCase):
    """The seam epoch_provider_composition.SandboxedProviderLauncher uses."""

    def test_16_injected_launch_receives_logical_argv_and_sanctioned_kwargs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(provider, root, environment={"FOO": "bar"})
            recorded: dict[str, Any] = {}

            def launch(argv: tuple[str, ...], **kwargs: Any) -> FakeLaunchedProcess:
                recorded["argv"] = argv
                recorded["kwargs"] = kwargs
                return FakeLaunchedProcess(stdout=encode_provider_events(valid_provider_events()))

            executor = claude_executor.ClaudeExecutor(str(provider), launch=launch)
            result = executor(invocation)

            self.assertEqual(recorded["argv"], invocation.argv)
            self.assertEqual(recorded["kwargs"]["cwd"], invocation.cwd)
            self.assertEqual(recorded["kwargs"]["env"], dict(invocation.environment))
            self.assertEqual(recorded["kwargs"]["shell"], False)
            self.assertEqual(recorded["kwargs"]["close_fds"], True)
            self.assertEqual(recorded["kwargs"]["start_new_session"], True)
            self.assertEqual(recorded["kwargs"]["stdin"], subprocess.PIPE)
            self.assertEqual(recorded["kwargs"]["stdout"], subprocess.PIPE)
            self.assertEqual(recorded["kwargs"]["stderr"], subprocess.PIPE)
            self.assertEqual(result[0]["subtype"], "init")

    def test_17_injected_launch_writes_the_prompt_via_communicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            prompt = "an exact prompt\nwith a newline"
            invocation = make_invocation(provider, root, prompt=prompt)
            process = FakeLaunchedProcess(stdout=encode_provider_events(valid_provider_events()))

            executor = claude_executor.ClaudeExecutor(
                str(provider), launch=lambda argv, **kwargs: process
            )
            executor(invocation)

            self.assertEqual(
                process.communicate_calls,
                [(prompt.encode("utf-8"), claude_executor.DEFAULT_PROVIDER_TIMEOUT_SECONDS)],
            )

    def test_18_injected_launch_spawn_oserror_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(provider, root)

            def launch(argv: tuple[str, ...], **kwargs: Any) -> FakeLaunchedProcess:
                raise OSError("induced spawn failure")

            executor = claude_executor.ClaudeExecutor(str(provider), launch=launch)

            with self.assertRaises(claude_executor.ProviderProcessError) as caught:
                executor(invocation)

            self.assertIsNone(caught.exception.returncode)
            self.assertIn("induced spawn failure", str(caught.exception))

    def test_19_injected_launch_communicate_oserror_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(provider, root)

            class RaisingProcess:
                def communicate(
                    self, input: bytes | None = None, timeout: float | None = None
                ) -> Any:
                    raise OSError("induced communicate failure")

            executor = claude_executor.ClaudeExecutor(
                str(provider), launch=lambda argv, **kwargs: RaisingProcess()
            )

            with self.assertRaises(claude_executor.ProviderProcessError) as caught:
                executor(invocation)

            self.assertIsNone(caught.exception.returncode)

    def test_20_injected_launch_nonzero_returncode_is_surfaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(provider, root)
            process = FakeLaunchedProcess(
                stdout=b'{"sequence":1}\n', stderr=b"provider failed\n", returncode=23
            )

            executor = claude_executor.ClaudeExecutor(
                str(provider), launch=lambda argv, **kwargs: process
            )

            with self.assertRaises(claude_executor.ProviderProcessError) as caught:
                executor(invocation)

            self.assertEqual(caught.exception.returncode, 23)
            self.assertEqual(caught.exception.stderr, b"provider failed\n")

    def test_21_default_executor_has_no_launch_seam_installed(self) -> None:
        executor = claude_executor.ClaudeExecutor("/offline/fake-claude")

        self.assertIsNone(executor.launch)

    def test_22_default_path_still_uses_subprocess_run(self) -> None:
        # Version preflight remains a subprocess.run call while the provider
        # process itself uses the bounded Popen path and a fresh process group.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(provider, root)
            executor = claude_executor.ClaudeExecutor(str(provider))
            self.assertIsNone(executor.launch)

            with mock.patch.object(
                claude_executor.subprocess, "run", wraps=claude_executor.subprocess.run
            ) as run, mock.patch.object(
                claude_executor.subprocess, "Popen", wraps=claude_executor.subprocess.Popen
            ) as popen:
                result = executor(invocation)

            self.assertEqual(run.call_count, 1)
            self.assertEqual(popen.call_count, 2)
            self.assertTrue(popen.call_args_list[-1].kwargs["start_new_session"])
            self.assertEqual(result[0]["type"], "system")

    def test_23_composed_version_probe_does_not_use_direct_subprocess_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(provider, root)
            process = FakeLaunchedProcess(stdout=encode_provider_events(valid_provider_events()))
            probe = mock.Mock(return_value=subprocess.CompletedProcess(
                [str(provider), "--version"], 0,
                (claude_executor.REQUIRED_PROVIDER_VERSION + "\n").encode(), b""
            ))
            executor = claude_executor.ClaudeExecutor(
                str(provider), launch=lambda argv, **kwargs: process, version_probe=probe
            )
            with mock.patch.object(claude_executor.subprocess, "run") as direct:
                result = executor(invocation)
                direct.assert_not_called()
            probe.assert_called_once_with()
            self.assertEqual(result[0]["subtype"], "init")

    def test_24_failed_composed_version_pin_prevents_prompt_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            invocation = make_invocation(provider, root)
            launch = mock.Mock()
            executor = claude_executor.ClaudeExecutor(
                str(provider), launch=launch,
                version_probe=lambda: subprocess.CompletedProcess([], 0, b"wrong-version\n", b""),
            )
            with self.assertRaises(claude_executor.ProviderVersionError):
                executor(invocation)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
