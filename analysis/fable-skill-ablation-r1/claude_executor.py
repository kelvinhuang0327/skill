#!/usr/bin/env python3
"""Fail-closed Claude subprocess adapter for the canonical ablation harness.

The adapter deliberately owns only the provider process boundary.  Every
experimental decision remains frozen in the manifest and arrives through the
exact :class:`runner.ModelInvocation` built by the harness.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import runner
from runner import InitEvidence, ModelInvocation


PROVIDER_EXECUTABLE = "/Users/kelvin/.local/bin/claude"
REQUIRED_PROVIDER_VERSION = "2.1.245 (Claude Code)"
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 300.0
DEFAULT_TERMINATION_GRACE_SECONDS = 2.0
MAX_DIAGNOSTIC_STREAM_BYTES = 4096

_MODEL_FAMILY_ALIASES = {"sonnet", "opus", "haiku", "fable"}
_MODEL_FAMILY_PATTERN = re.compile(
    r"(?:^|[./])claude-(?:3(?:-\d+)?-)?(sonnet|opus|haiku|fable)(?=$|[-.:/@])",
    re.IGNORECASE,
)


class ClaudeExecutorError(RuntimeError):
    """Base class for a fail-closed provider-boundary failure."""


class InvocationContractError(ClaudeExecutorError):
    """The caller did not supply the canonical invocation object unchanged."""


class ProviderVersionError(ClaudeExecutorError):
    """The provider executable could not prove the required version."""

    def __init__(
        self,
        message: str,
        *,
        executable: str,
        required_version: str,
        observed_version: str | None,
        returncode: int | None,
        stderr: bytes = b"",
    ) -> None:
        super().__init__(message)
        self.executable = executable
        self.required_version = required_version
        self.observed_version = observed_version
        self.returncode = returncode
        self.stderr = stderr


class ProviderProcessError(ClaudeExecutorError):
    """The provider child failed to start or exited non-zero."""

    def __init__(
        self,
        message: str,
        *,
        argv: tuple[str, ...],
        returncode: int | None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        timed_out: bool = False,
        termination_method: str | None = None,
        signals_sent: Sequence[str] = (),
        cleanup_complete: bool = True,
        child_pid: int | None = None,
        spawn_started_at: str | None = None,
        spawned_at: str | None = None,
        ready_at: str | None = None,
        runtime_executable: str | None = None,
        runtime_version: str | None = None,
    ) -> None:
        super().__init__(message)
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.termination_method = termination_method
        self.signals_sent = tuple(signals_sent)
        self.cleanup_complete = cleanup_complete
        self.child_pid = child_pid if type(child_pid) is int and child_pid > 0 else None
        self.spawn_started_at = spawn_started_at
        self.spawned_at = spawned_at
        self.ready_at = ready_at
        self.runtime_executable = runtime_executable
        self.runtime_version = runtime_version

    @staticmethod
    def _stream_record(value: bytes) -> dict[str, Any]:
        excerpt = value[:MAX_DIAGNOSTIC_STREAM_BYTES]
        return {
            "byte_count": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
            "excerpt": excerpt.decode("utf-8", errors="replace"),
            "excerpt_truncated": len(value) > MAX_DIAGNOSTIC_STREAM_BYTES,
        }

    def diagnostic_record(self) -> dict[str, Any]:
        """Return bounded, JSON-safe details for the existing result record."""

        return {
            "argv": list(self.argv),
            "child_pid": self.child_pid,
            "spawn_started_at": self.spawn_started_at,
            "spawned_at": self.spawned_at,
            # Popen returning is the only ready signal this adapter observes;
            # it does not infer application readiness from a timeout.
            "ready_at": self.ready_at,
            "runtime_executable": self.runtime_executable,
            "runtime_version": self.runtime_version,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "termination_method": self.termination_method,
            "signals_sent": list(self.signals_sent),
            "cleanup_complete": self.cleanup_complete,
            "stdout": self._stream_record(self.stdout),
            "stderr": self._stream_record(self.stderr),
        }


class ProviderOutputError(ClaudeExecutorError):
    """Required provider stdout was not strict line-oriented JSON objects."""

    def __init__(
        self,
        message: str,
        *,
        line_number: int | None,
        stdout: bytes,
        stderr: bytes,
        validation_errors: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.line_number = line_number
        self.stdout = stdout
        self.stderr = stderr
        self.validation_errors = tuple(validation_errors)


@dataclass(frozen=True)
class ClaudeExecutionResult(Sequence[Mapping[str, Any]]):
    """Successful process evidence while remaining iterable as runner events."""

    argv: tuple[str, ...]
    events: tuple[Mapping[str, Any], ...]
    stdout: bytes
    stderr: bytes
    # Bound to the exact executable path and the version observed by this
    # call's own preflight check (never a caller-supplied label), so the
    # runner-owned cross-provider gate can confirm this provider's identity
    # did not silently drift between its OFF and ON arms.
    provider_identity: str
    returncode: int = 0

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(
        self, index: int | slice
    ) -> Mapping[str, Any] | Sequence[Mapping[str, Any]]:
        return self.events[index]


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _parse_required_jsonl(
    stdout: bytes, stderr: bytes
) -> tuple[Mapping[str, Any], ...]:
    if not stdout:
        raise ProviderOutputError(
            "provider stdout contained no required JSONL events",
            line_number=None,
            stdout=stdout,
            stderr=stderr,
        )
    try:
        text = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProviderOutputError(
            f"provider stdout was not valid UTF-8: {exc}",
            line_number=None,
            stdout=stdout,
            stderr=stderr,
        ) from exc

    events: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise ProviderOutputError(
                "provider stdout contained a blank JSONL record",
                line_number=line_number,
                stdout=stdout,
                stderr=stderr,
            )
        try:
            event = json.loads(line, object_pairs_hook=_object_without_duplicate_keys)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderOutputError(
                f"provider stdout line {line_number} was malformed JSON: {exc}",
                line_number=line_number,
                stdout=stdout,
                stderr=stderr,
            ) from exc
        if not isinstance(event, Mapping):
            raise ProviderOutputError(
                f"provider stdout line {line_number} was not a JSON object",
                line_number=line_number,
                stdout=stdout,
                stderr=stderr,
            )
        events.append(event)

    if not events:
        raise ProviderOutputError(
            "provider stdout contained no required JSONL events",
            line_number=None,
            stdout=stdout,
            stderr=stderr,
        )
    return tuple(events)


def _expected_model_identity(argv: Sequence[str]) -> str | None:
    model_values: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in {"--model", "-m"}:
            if index + 1 < len(argv):
                model_values.append(argv[index + 1])
                index += 1
            else:
                model_values.append("")
        elif argument.startswith("--model="):
            model_values.append(argument.partition("=")[2])
        index += 1

    if len(model_values) > 1 or (model_values and not model_values[0]):
        raise InvocationContractError(
            "ModelInvocation argv must not contain an ambiguous model identity"
        )
    return model_values[0] if model_values else None


def _model_identity_matches(expected: str | None, observed: str | None) -> bool:
    """Match exact model names or a supported family alias to its resolved ID."""

    if expected is None:
        return True
    if expected == observed:
        return True
    alias = expected.casefold()
    if alias.endswith("[1m]"):
        alias = alias[:-4]
    if alias not in _MODEL_FAMILY_ALIASES or not isinstance(observed, str):
        return False
    match = _MODEL_FAMILY_PATTERN.search(observed)
    return match is not None and match.group(1).casefold() == alias


def _main_loop_assistant(record: Mapping[str, Any]) -> bool:
    return (
        record.get("type") == "assistant"
        and not record.get("parent_tool_use_id")
        and not record.get("is_subagent")
        and not record.get("subagent")
    )


def _valid_model_usage(value: Any) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    for model, usage in value.items():
        if not isinstance(model, str) or not model:
            return False
        if not isinstance(usage, Mapping) or not usage:
            return False
        for metric, amount in usage.items():
            if (
                not isinstance(metric, str)
                or not metric
                or isinstance(amount, bool)
                or not isinstance(amount, (int, float))
            ):
                return False
            try:
                numeric_amount = Decimal(str(amount))
            except (InvalidOperation, ValueError):
                return False
            if not numeric_amount.is_finite() or numeric_amount < 0:
                return False
            if metric.casefold().endswith(("tokens", "requests", "window")) and type(amount) is not int:
                return False
    return True


def _validate_provider_stream(
    events: Sequence[Mapping[str, Any]], expected_model: str | None
) -> tuple[str, ...]:
    errors: list[str] = []
    init_indices = [
        index
        for index, event in enumerate(events)
        if event.get("type") == "system" and event.get("subtype") == "init"
    ]
    result_indices = [
        index for index, event in enumerate(events) if event.get("type") == "result"
    ]
    if not init_indices:
        errors.append("missing_init")
    elif len(init_indices) > 1:
        errors.append("multiple_init")
    if not result_indices:
        errors.append("missing_terminal_result")
    elif len(result_indices) > 1:
        errors.append("multiple_terminal_result")
    if len(result_indices) == 1 and result_indices[0] != len(events) - 1:
        errors.append("terminal_result_not_last")

    required_session_indices = set(init_indices + result_indices)
    session_ids: list[str] = []
    for index, event in enumerate(events):
        if (
            index not in required_session_indices
            and event.get("type") != "assistant"
            and "session_id" not in event
        ):
            continue
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            errors.append(f"session_id_missing:{index}")
        else:
            session_ids.append(session_id)
    if not session_ids or len(set(session_ids)) != 1:
        errors.append("session_ids_inconsistent")

    init_model: str | None = None
    if len(init_indices) == 1:
        raw_init_model = events[init_indices[0]].get("model")
        if isinstance(raw_init_model, str) and raw_init_model:
            init_model = raw_init_model
        else:
            errors.append("init_model_missing")
        if expected_model is not None and not _model_identity_matches(
            expected_model, init_model
        ):
            errors.append("init_model_mismatch")

    for index, event in enumerate(events):
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        message_id = message.get("id") if isinstance(message, Mapping) else None
        if not isinstance(message_id, str) or not message_id:
            errors.append(f"assistant_message_id_missing:{index}")

    main_assistant_indices = [
        index for index, event in enumerate(events) if _main_loop_assistant(event)
    ]
    if not main_assistant_indices:
        errors.append("missing_main_loop_assistant")
    assistant_models = [
        message.get("model")
        for index in main_assistant_indices
        if isinstance((message := events[index].get("message")), Mapping)
    ]
    # For a family alias such as ``sonnet``, the stream reports the resolved
    # versioned model ID. Bind all assistant and usage evidence to that ID.
    resolved_model = init_model or expected_model
    if resolved_model is None:
        resolved_model = next(
            (model for model in assistant_models if isinstance(model, str) and model),
            None,
        )
    if resolved_model is None:
        errors.append("model_identity_unresolved")
    for index in main_assistant_indices:
        message = events[index].get("message")
        model = message.get("model") if isinstance(message, Mapping) else None
        if model != resolved_model:
            errors.append(f"assistant_model_mismatch:{index}")
    if init_model is not None and init_model != resolved_model:
        errors.append("init_model_mismatch")

    if len(result_indices) == 1:
        terminal = events[result_indices[0]]
        if (
            terminal.get("subtype") != "success"
            or terminal.get("is_error", False) is not False
        ):
            errors.append("terminal_result_reports_error")

        usage = terminal.get("modelUsage")
        if not isinstance(usage, Mapping) or not usage:
            errors.append("modelUsage_missing")
        else:
            if not _valid_model_usage(usage):
                errors.append("modelUsage_invalid")
            if resolved_model is None or resolved_model not in usage:
                errors.append("modelUsage_expected_model_missing")

        raw_cost = terminal.get("total_cost_usd")
        if isinstance(raw_cost, bool) or raw_cost is None or raw_cost == "":
            errors.append("total_cost_usd_missing_or_invalid")
        else:
            try:
                cost = Decimal(str(raw_cost))
            except (InvalidOperation, ValueError):
                errors.append("total_cost_usd_missing_or_invalid")
            else:
                if not cost.is_finite() or cost < 0:
                    errors.append("total_cost_usd_invalid")

        if "num_turns" in terminal and (
            type(terminal["num_turns"]) is not int or terminal["num_turns"] < 0
        ):
            errors.append("terminal_turn_count_invalid")

    return tuple(dict.fromkeys(errors))


def _merge_partial_output(previous: bytes, latest: bytes | None) -> bytes:
    if not latest:
        return previous
    if not previous or latest.startswith(previous):
        return latest
    return previous + latest


class ClaudeExecutor:
    """Execute one canonical ``ModelInvocation`` at the pinned process boundary."""

    def __init__(
        self,
        provider_executable: str = PROVIDER_EXECUTABLE,
        required_provider_version: str = REQUIRED_PROVIDER_VERSION,
        *,
        launch: Callable[..., subprocess.Popen[bytes]] | None = None,
        version_probe: Callable[[], subprocess.CompletedProcess[bytes]] | None = None,
        timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        termination_grace_seconds: float = DEFAULT_TERMINATION_GRACE_SECONDS,
    ) -> None:
        if not isinstance(provider_executable, str) or not provider_executable:
            raise ValueError("provider_executable must be a non-empty string")
        if not isinstance(required_provider_version, str) or not required_provider_version:
            raise ValueError("required_provider_version must be a non-empty string")
        if launch is not None and not callable(launch):
            raise ValueError("launch must be callable when provided")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if (
            isinstance(termination_grace_seconds, bool)
            or not isinstance(termination_grace_seconds, (int, float))
            or not math.isfinite(float(termination_grace_seconds))
            or termination_grace_seconds <= 0
        ):
            raise ValueError(
                "termination_grace_seconds must be a positive finite number"
            )
        self.provider_executable = provider_executable
        self.required_provider_version = required_provider_version
        self.timeout_seconds = float(timeout_seconds)
        self.termination_grace_seconds = float(termination_grace_seconds)
        # The sanctioned sandbox composition (epoch_provider_composition.py)
        # injects its launcher here.  The direct path remains available for
        # offline callers and uses the same bounded process-group contract.
        self.launch = launch
        if version_probe is not None and not callable(version_probe):
            raise ValueError("version_probe must be callable when provided")
        self.version_probe = version_probe

    def verify_provider_version(self) -> str:
        """Read the exact provider version without sending a model prompt."""

        argv = (self.provider_executable, "--version")
        try:
            if self.version_probe is not None:
                completed = self.version_probe()
            else:
                completed = subprocess.run(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    shell=False,
                )
        except OSError as exc:
            raise ProviderVersionError(
                f"provider version command failed to start: {exc}",
                executable=self.provider_executable,
                required_version=self.required_provider_version,
                observed_version=None,
                returncode=None,
            ) from exc

        if completed.returncode != 0:
            raise ProviderVersionError(
                f"provider version command exited {completed.returncode}",
                executable=self.provider_executable,
                required_version=self.required_provider_version,
                observed_version=None,
                returncode=completed.returncode,
                stderr=completed.stderr,
            )
        try:
            observed = completed.stdout.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise ProviderVersionError(
                f"provider version output was not valid UTF-8: {exc}",
                executable=self.provider_executable,
                required_version=self.required_provider_version,
                observed_version=None,
                returncode=completed.returncode,
                stderr=completed.stderr,
            ) from exc
        if observed != self.required_provider_version:
            raise ProviderVersionError(
                "provider executable version did not match the required pin",
                executable=self.provider_executable,
                required_version=self.required_provider_version,
                observed_version=observed,
                returncode=completed.returncode,
                stderr=completed.stderr,
            )
        return observed

    def __call__(self, invocation: ModelInvocation) -> ClaudeExecutionResult:
        """Run the exact invocation and return its ordered JSONL events."""

        if not isinstance(invocation, ModelInvocation):
            raise InvocationContractError(
                "executor requires the canonical runner.ModelInvocation instance"
            )
        if not invocation.argv or invocation.argv[0] != self.provider_executable:
            raise InvocationContractError(
                "ModelInvocation argv does not begin with the pinned provider executable"
            )
        if not all(isinstance(part, str) and part for part in invocation.argv):
            raise InvocationContractError("ModelInvocation argv must contain non-empty strings")
        if not isinstance(invocation.prompt, str):
            raise InvocationContractError("ModelInvocation prompt must be a string")
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in invocation.environment.items()
        ):
            raise InvocationContractError(
                "ModelInvocation environment must contain only strings"
            )
        expected_model = _expected_model_identity(invocation.argv)

        observed_version = self.verify_provider_version()
        stdout, stderr, returncode = self._run_provider_process(
            invocation, provider_version=observed_version
        )

        if returncode != 0:
            raise ProviderProcessError(
                f"provider process exited {returncode}",
                argv=invocation.argv,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )

        events = _parse_required_jsonl(stdout, stderr)
        validation_errors = _validate_provider_stream(events, expected_model)
        if validation_errors:
            raise ProviderOutputError(
                "provider JSONL stream failed semantic validation: "
                + ",".join(validation_errors),
                line_number=None,
                stdout=stdout,
                stderr=stderr,
                validation_errors=validation_errors,
            )
        return ClaudeExecutionResult(
            argv=invocation.argv,
            events=events,
            stdout=stdout,
            stderr=stderr,
            provider_identity=f"{self.provider_executable}@{observed_version}",
            returncode=returncode,
        )

    def _run_provider_process(
        self, invocation: ModelInvocation, *, provider_version: str | None = None
    ) -> tuple[bytes, bytes, int]:
        """Run the provider in a new session with bounded group termination."""

        launch_kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": invocation.cwd,
            "env": dict(invocation.environment),
            "shell": False,
            "close_fds": True,
            "start_new_session": True,
        }
        spawn_started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        try:
            if self.launch is None:
                process = subprocess.Popen(invocation.argv, **launch_kwargs)
            else:
                process = self.launch(invocation.argv, **launch_kwargs)
        except OSError as exc:
            raise ProviderProcessError(
                f"provider process failed to start: {exc}",
                argv=invocation.argv,
                returncode=None,
                stdout=getattr(exc, "output", b"") or b"",
                stderr=getattr(exc, "stderr", b"") or b"",
                child_pid=getattr(exc, "pid", None),
                spawn_started_at=spawn_started_at,
                runtime_executable=self.provider_executable,
                runtime_version=provider_version,
            ) from exc
        spawned_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        child_pid = getattr(process, "pid", None)
        if type(child_pid) is not int or child_pid <= 0:
            child_pid = None

        try:
            stdout, stderr = process.communicate(
                input=invocation.prompt.encode("utf-8"),
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as timeout_error:
            stdout = timeout_error.output or b""
            stderr = timeout_error.stderr or b""
            signals_sent: list[str] = []
            termination_steps: list[str] = []

            def send_signal(signum: int) -> None:
                signal_name = signal.Signals(signum).name
                process_group_id = getattr(process, "pid", None)
                group_signal = getattr(os, "killpg", None)
                if type(process_group_id) is int and process_group_id > 0 and callable(group_signal):
                    try:
                        group_signal(process_group_id, signum)
                    except ProcessLookupError:
                        termination_steps.append(f"{signal_name}_GROUP_ALREADY_EXITED")
                        return
                    except OSError:
                        pass
                    else:
                        signals_sent.append(signal_name)
                        termination_steps.append(signal_name)
                        return

                fallback = getattr(
                    process, "terminate" if signum == signal.SIGTERM else "kill", None
                )
                if callable(fallback):
                    try:
                        fallback()
                    except OSError:
                        termination_steps.append(f"{signal_name}_PROCESS_SIGNAL_FAILED")
                    else:
                        signals_sent.append(f"{signal_name}_PROCESS")
                        termination_steps.append(f"{signal_name}_PROCESS")
                else:
                    termination_steps.append(f"{signal_name}_UNAVAILABLE")

            def process_group_alive() -> bool:
                process_group_id = getattr(process, "pid", None)
                group_signal = getattr(os, "killpg", None)
                if type(process_group_id) is int and process_group_id > 0 and callable(group_signal):
                    try:
                        group_signal(process_group_id, 0)
                    except ProcessLookupError:
                        return False
                    except OSError:
                        # An unobservable process group is not proof of cleanup.
                        return True
                    return True
                process_poll = getattr(process, "poll", None)
                return process_poll() is None if callable(process_poll) else True

            def wait_for_process_group_exit(deadline: float) -> bool:
                while process_group_alive():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    time.sleep(min(0.01, remaining))
                return True

            send_signal(signal.SIGTERM)
            cleanup_complete = False
            termination_deadline = time.monotonic() + self.termination_grace_seconds
            try:
                stdout_after_term, stderr_after_term = process.communicate(
                    timeout=self.termination_grace_seconds
                )
                stdout = _merge_partial_output(stdout, stdout_after_term)
                stderr = _merge_partial_output(stderr, stderr_after_term)
            except subprocess.TimeoutExpired as term_timeout:
                stdout = _merge_partial_output(stdout, term_timeout.output)
                stderr = _merge_partial_output(stderr, term_timeout.stderr)
            except OSError as communication_error:
                stdout = _merge_partial_output(
                    stdout, getattr(communication_error, "output", None)
                )
                stderr = _merge_partial_output(
                    stderr, getattr(communication_error, "stderr", None)
                )

            term_group_exited = wait_for_process_group_exit(termination_deadline)
            if not term_group_exited:
                send_signal(signal.SIGKILL)
                kill_deadline = time.monotonic() + self.termination_grace_seconds
                try:
                    stdout_after_kill, stderr_after_kill = process.communicate(
                        timeout=self.termination_grace_seconds
                    )
                    stdout = _merge_partial_output(stdout, stdout_after_kill)
                    stderr = _merge_partial_output(stderr, stderr_after_kill)
                except subprocess.TimeoutExpired as kill_timeout:
                    stdout = _merge_partial_output(stdout, kill_timeout.output)
                    stderr = _merge_partial_output(stderr, kill_timeout.stderr)
                    process_kill = getattr(process, "kill", None)
                    if callable(process_kill):
                        try:
                            process_kill()
                        except OSError:
                            termination_steps.append("PROCESS_KILL_FAILED")
                        else:
                            signals_sent.append("SIGKILL_PROCESS")
                            termination_steps.append("SIGKILL_PROCESS")
                    try:
                        stdout_after_final_kill, stderr_after_final_kill = process.communicate(
                            timeout=self.termination_grace_seconds
                        )
                        stdout = _merge_partial_output(stdout, stdout_after_final_kill)
                        stderr = _merge_partial_output(stderr, stderr_after_final_kill)
                    except (subprocess.TimeoutExpired, OSError):
                        pass
                kill_group_exited = wait_for_process_group_exit(kill_deadline)
                cleanup_complete = process.poll() is not None and kill_group_exited
            else:
                cleanup_complete = process.poll() is not None

            returncode = process.poll()
            raise ProviderProcessError(
                "provider process timed out after "
                f"{self.timeout_seconds:.3f}s; "
                f"termination={'->'.join(termination_steps) or 'unavailable'}; "
                f"cleanup_complete={str(cleanup_complete).lower()}",
                argv=invocation.argv,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                termination_method="->".join(termination_steps) or "termination_unavailable",
                signals_sent=signals_sent,
                cleanup_complete=cleanup_complete,
                child_pid=child_pid,
                spawn_started_at=spawn_started_at,
                spawned_at=spawned_at,
                ready_at=spawned_at,
                runtime_executable=self.provider_executable,
                runtime_version=provider_version,
            ) from timeout_error
        except OSError as exc:
            raise ProviderProcessError(
                f"provider process communication failed: {exc}",
                argv=invocation.argv,
                returncode=None,
                stdout=getattr(exc, "output", b"") or b"",
                stderr=getattr(exc, "stderr", b"") or b"",
                child_pid=child_pid,
                spawn_started_at=spawn_started_at,
                spawned_at=spawned_at,
                ready_at=spawned_at,
                runtime_executable=self.provider_executable,
                runtime_version=provider_version,
            ) from exc

        returncode = process.returncode
        if returncode is None:
            returncode = process.poll()
        return stdout, stderr, returncode


def instruction_surface_evidence(result: ClaudeExecutionResult) -> InitEvidence:
    """Extract this Claude run's required instruction-surface evidence.

    A thin, Claude-specific convenience over the provider-neutral decoder:
    all alias resolution, content binding, and fail-closed semantics live in
    ``runner.parse_init_events`` so there is exactly one place that decides
    what counts as a resolved surface.  This function makes no pass/fail
    judgement of its own -- it only exposes evidence for the runner-owned
    gate to compare.
    """

    return runner.parse_init_events(list(result))


__all__ = [
    "ClaudeExecutionResult",
    "ClaudeExecutor",
    "ClaudeExecutorError",
    "DEFAULT_PROVIDER_TIMEOUT_SECONDS",
    "DEFAULT_TERMINATION_GRACE_SECONDS",
    "InvocationContractError",
    "PROVIDER_EXECUTABLE",
    "ProviderOutputError",
    "ProviderProcessError",
    "ProviderVersionError",
    "REQUIRED_PROVIDER_VERSION",
    "instruction_surface_evidence",
]
