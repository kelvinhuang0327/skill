#!/usr/bin/env python3
"""Fail-closed Claude subprocess adapter for the canonical ablation harness.

The adapter deliberately owns only the provider process boundary.  Every
experimental decision remains frozen in the manifest and arrives through the
exact :class:`runner.ModelInvocation` built by the harness.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from runner import ModelInvocation


PROVIDER_EXECUTABLE = "/Users/kelvin/.local/bin/claude"
REQUIRED_PROVIDER_VERSION = "2.1.245 (Claude Code)"


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
    ) -> None:
        super().__init__(message)
        self.argv = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ProviderOutputError(ClaudeExecutorError):
    """Required provider stdout was not strict line-oriented JSON objects."""

    def __init__(
        self,
        message: str,
        *,
        line_number: int | None,
        stdout: bytes,
        stderr: bytes,
    ) -> None:
        super().__init__(message)
        self.line_number = line_number
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class ClaudeExecutionResult(Sequence[Mapping[str, Any]]):
    """Successful process evidence while remaining iterable as runner events."""

    argv: tuple[str, ...]
    events: tuple[Mapping[str, Any], ...]
    stdout: bytes
    stderr: bytes
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


class ClaudeExecutor:
    """Execute one canonical ``ModelInvocation`` at the pinned process boundary."""

    def __init__(
        self,
        provider_executable: str = PROVIDER_EXECUTABLE,
        required_provider_version: str = REQUIRED_PROVIDER_VERSION,
    ) -> None:
        if not isinstance(provider_executable, str) or not provider_executable:
            raise ValueError("provider_executable must be a non-empty string")
        if not isinstance(required_provider_version, str) or not required_provider_version:
            raise ValueError("required_provider_version must be a non-empty string")
        self.provider_executable = provider_executable
        self.required_provider_version = required_provider_version

    def verify_provider_version(self) -> str:
        """Read the exact provider version without sending a model prompt."""

        argv = (self.provider_executable, "--version")
        try:
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

        self.verify_provider_version()
        try:
            completed = subprocess.run(
                invocation.argv,
                input=invocation.prompt.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=invocation.cwd,
                env=dict(invocation.environment),
                check=False,
                shell=False,
            )
        except OSError as exc:
            raise ProviderProcessError(
                f"provider process failed to start: {exc}",
                argv=invocation.argv,
                returncode=None,
            ) from exc

        if completed.returncode != 0:
            raise ProviderProcessError(
                f"provider process exited {completed.returncode}",
                argv=invocation.argv,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )

        events = _parse_required_jsonl(completed.stdout, completed.stderr)
        return ClaudeExecutionResult(
            argv=invocation.argv,
            events=events,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )


__all__ = [
    "ClaudeExecutionResult",
    "ClaudeExecutor",
    "ClaudeExecutorError",
    "InvocationContractError",
    "PROVIDER_EXECUTABLE",
    "ProviderOutputError",
    "ProviderProcessError",
    "ProviderVersionError",
    "REQUIRED_PROVIDER_VERSION",
]
