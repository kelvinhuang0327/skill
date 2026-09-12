#!/usr/bin/env python3
"""Offline acceptance tests for the canonical Claude executor."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


# Importing sibling modules must not leave __pycache__ in the repository.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_executor  # noqa: E402
import runner  # noqa: E402


REQUIRED_VERSION = "2.1.245 (Claude Code)"
REAL_PROVIDER = "/Users/kelvin/.local/bin/claude"


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
    printf '%s\\n' '{{"type":"system","subtype":"init","sequence":1}}'
    ;;
  ordered)
    printf '%s\\n' '{{"sequence":1}}' '{{"sequence":2}}' '{{"sequence":3}}'
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
    printf '%s\\n' '{{"source":"stdout"}}'
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
    argv_tail: tuple[str, ...] = ("--output-format", "stream-json"),
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


class ClaudeExecutorOfflineTests(unittest.TestCase):
    def test_01_argv_is_passed_without_shell_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = write_fake_provider(root)
            argv_record = root / "argv.txt"
            stdin_record = root / "stdin.txt"
            sentinel = root / "shell-expanded"
            literal_arguments = (
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

            self.assertEqual(list(result), [{"source": "stdout"}])
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
        calls: list[tuple[str, ...]] = []

        def fake_run(
            argv: tuple[str, ...], **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            captured = tuple(argv)
            calls.append(captured)
            self.assertEqual(captured[0], offline_provider)
            self.assertNotEqual(captured[0], REAL_PROVIDER)
            if captured == (offline_provider, "--version"):
                return subprocess.CompletedProcess(
                    captured,
                    0,
                    stdout=(REQUIRED_VERSION + "\n").encode("utf-8"),
                    stderr=b"",
                )
            return subprocess.CompletedProcess(
                captured,
                0,
                stdout=b'{"type":"system","subtype":"init"}\n',
                stderr=b"",
            )

        with tempfile.TemporaryDirectory() as temporary:
            invocation = make_invocation(offline_provider, Path(temporary))
            with mock.patch.object(
                claude_executor.subprocess, "run", side_effect=fake_run
            ):
                result = claude_executor.ClaudeExecutor(offline_provider)(invocation)

        self.assertEqual(len(calls), 2)
        self.assertEqual(result[0]["subtype"], "init")
        self.assertTrue(all(call[0] != REAL_PROVIDER for call in calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
