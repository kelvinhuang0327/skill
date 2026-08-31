#!/usr/bin/env python3
"""Evidence-bearing Claude CLI boundary for the offline ablation harness.

The adapter is capable of real subprocess execution, but every dependency is
injectable so acceptance tests use fake process objects only.  Treatment state
is deliberately absent from :class:`ClaudeProviderPolicy` and from this module.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:  # Tests load the sibling under this explicit, non-package module name.
    import fable_ablation_runner as harness
except ModuleNotFoundError:  # Direct use with this directory on sys.path.
    import runner as harness  # type: ignore[no-redef]


# The sibling harness already resolved the one canonical persisted USD
# representation authority; reuse that exact module rather than formatting
# money locally.
_usd = harness._load_usd_authority()

ADAPTER_NAME = "fable-claude-stream-json"
ADAPTER_VERSION = "3"
CTO_MODEL_ID = "claude-sonnet-5"
CTO_EFFORT = "medium"
CTO_MAX_BUDGET_USD = Decimal("2.0000000")
CTO_MAX_TURNS = 32
CTO_TOOLSET = ("Bash", "Read", "Edit", "Write", "Skill")
CTO_PERMISSION_MODE = "bypassPermissions"
FABLE_CARRIER_IDENTITY = "fable-method"
SANDBOX_PROFILE_PATH = Path(__file__).with_name("claude-runtime.sb")
SANDBOX_POLICY_IDENTITY_PREFIX = "sha256:"
_SANDBOX_POLICY_IDENTITY_RE = re.compile(
    re.escape(SANDBOX_POLICY_IDENTITY_PREFIX) + r"[0-9a-f]{64}"
)
PROHIBITED_FLAGS = frozenset(
    {
        "--continue",
        "--resume",
        "--fork-session",
        "--from-pr",
        "--teleport",
        "--cloud",
        "--remote",
    }
)

# The epoch controller accepts exactly 32 lowercase hex.  Accepting a wider
# token here would only defer a controller rejection until after preflight.
_OPAQUE_SESSION_RE = re.compile(
    r"session-[0-9a-f]{%d}" % harness.OPAQUE_SESSION_TOKEN_HEX_WIDTH
)
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|AUTHORIZATION)(?:$|_)",
    re.IGNORECASE,
)
_INVENTORY_ALIASES = {
    "tools": ("tools",),
    "agents": ("agents",),
    "mcp_servers": ("mcp_servers", "mcpServers"),
    "plugins": ("plugins",),
}
_PERMISSION_ALIASES = ("permissionMode", "permission_mode", "permission-mode")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _unique(errors: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(errors))


def observed_sandbox_policy_identity(
    profile_path: str | os.PathLike[str] = SANDBOX_PROFILE_PATH,
) -> str:
    """Bind the sandbox policy identity to the actual profile bytes.

    A caller-supplied label proves nothing about what the kernel will enforce,
    so the identity is the SHA256 of the bytes that will really be loaded.
    """

    try:
        data = Path(profile_path).read_bytes()
    except OSError as exc:
        raise harness.HarnessContractError(
            f"sandbox policy bytes are unreadable: {exc}"
        ) from exc
    return f"{SANDBOX_POLICY_IDENTITY_PREFIX}{_sha256(data)}"


def build_cto_argv(provider_executable: str) -> tuple[str, ...]:
    """Return the CTO-fixed *logical* provider argv, with no sandbox prefix.

    The physical process argv is built separately by
    ``epoch_controller.build_sandboxed_command``; this lane must never be
    redefined as the sandbox-prefixed command.
    """

    return (
        provider_executable,
        "--bare",
        "--print",
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
        "--verbose",
        "--forward-subagent-text",
        "--model",
        CTO_MODEL_ID,
        "--effort",
        CTO_EFFORT,
        "--max-budget-usd",
        _usd.canonical_usd_text(CTO_MAX_BUDGET_USD),
        "--max-turns",
        str(CTO_MAX_TURNS),
        "--no-session-persistence",
        "--no-chrome",
        "--tools",
        ",".join(CTO_TOOLSET),
        "--allowedTools",
        *CTO_TOOLSET,
        "--disallowedTools",
        "mcp__*",
        "--permission-mode",
        CTO_PERMISSION_MODE,
        "--add-dir",
        ".",
    )


@dataclass(frozen=True)
class ClaudeProviderPolicy:
    """Frozen, condition-neutral identity and invocation policy."""

    provider_binary: harness.ProviderBinaryIdentity
    adapter_source: harness.AdapterIdentity
    argv_template: tuple[str, ...]
    model_id: str
    effort: str
    max_budget_usd: Decimal
    max_turns: int
    timeout_seconds: float
    toolset: tuple[str, ...]
    permission_policy: str
    expected_tools: tuple[str, ...]
    expected_agents: tuple[str, ...]
    expected_mcp_servers: tuple[str, ...]
    expected_plugins: tuple[str, ...]
    sandbox_policy_identity: str
    secret_environment_keys: frozenset[str] = frozenset()
    termination_grace_seconds: float = 2.0

    def __post_init__(self) -> None:
        complete_binary = (
            self.provider_binary.executable
            and self.provider_binary.realpath
            and self.provider_binary.sha256
            and self.provider_binary.version
        )
        if not complete_binary:
            raise harness.HarnessContractError("provider binary identity is incomplete")
        if not re.fullmatch(r"[0-9a-f]{64}", self.provider_binary.sha256 or ""):
            raise harness.HarnessContractError("provider binary SHA256 is invalid")
        complete_adapter = (
            self.adapter_source.name
            and self.adapter_source.version
            and self.adapter_source.source_realpath
            and self.adapter_source.source_sha256
        )
        if not complete_adapter:
            raise harness.HarnessContractError("adapter source identity is incomplete")
        if not re.fullmatch(r"[0-9a-f]{64}", self.adapter_source.source_sha256 or ""):
            raise harness.HarnessContractError("adapter source SHA256 is invalid")
        if self.model_id != CTO_MODEL_ID:
            raise harness.HarnessContractError("fallback model substitution is prohibited")
        if self.effort != CTO_EFFORT:
            raise harness.HarnessContractError("provider effort must remain CTO-fixed")
        try:
            budget_text = _usd.canonical_usd_text(self.max_budget_usd)
        except _usd.CanonicalUsdError as exc:
            raise harness.HarnessContractError(
                f"provider max budget is not canonical persisted USD: {exc}"
            ) from exc
        if self.max_budget_usd != CTO_MAX_BUDGET_USD or budget_text != _usd.canonical_usd_text(
            CTO_MAX_BUDGET_USD
        ):
            raise harness.HarnessContractError("provider max budget decimal must remain exact")
        if self.max_turns != CTO_MAX_TURNS:
            raise harness.HarnessContractError("provider max turns must remain CTO-fixed")
        if self.toolset != CTO_TOOLSET:
            raise harness.HarnessContractError("provider toolset must remain CTO-fixed")
        if self.permission_policy != CTO_PERMISSION_MODE:
            raise harness.HarnessContractError("provider permission policy must remain CTO-fixed")
        if self.argv_template != build_cto_argv(self.provider_binary.executable):
            raise harness.HarnessContractError("argv template does not match the CTO contract")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise harness.HarnessContractError("timeout must be finite and positive")
        if (
            not math.isfinite(self.termination_grace_seconds)
            or self.termination_grace_seconds <= 0
        ):
            raise harness.HarnessContractError(
                "termination grace period must be finite and positive"
            )
        inventories = (
            self.expected_tools,
            self.expected_agents,
            self.expected_mcp_servers,
            self.expected_plugins,
        )
        if any(
            not isinstance(item, str) or not item
            for inventory in inventories
            for item in inventory
        ):
            raise harness.HarnessContractError("expected inventories must contain strings")
        if any(len(set(inventory)) != len(inventory) for inventory in inventories):
            raise harness.HarnessContractError("expected inventories must be unique")
        if _SANDBOX_POLICY_IDENTITY_RE.fullmatch(self.sandbox_policy_identity or "") is None:
            raise harness.HarnessContractError(
                "sandbox policy identity must bind observed profile bytes as "
                f"{SANDBOX_POLICY_IDENTITY_PREFIX}<sha256>"
            )
        if any(not isinstance(key, str) or not key for key in self.secret_environment_keys):
            raise harness.HarnessContractError("secret environment keys must be strings")

    def as_record(self) -> dict[str, Any]:
        return {
            "provider_binary": self.provider_binary.as_record(),
            "adapter_source": self.adapter_source.as_record(),
            "argv_template": list(self.argv_template),
            "model_id": self.model_id,
            "effort": self.effort,
            "max_budget_usd": _usd.canonical_usd_text(self.max_budget_usd),
            "max_turns": self.max_turns,
            "timeout_seconds": self.timeout_seconds,
            "toolset": list(self.toolset),
            "permission_policy": self.permission_policy,
            "expected_tools": list(self.expected_tools),
            "expected_agents": list(self.expected_agents),
            "expected_mcp_servers": list(self.expected_mcp_servers),
            "expected_plugins": list(self.expected_plugins),
            "sandbox_policy_identity": self.sandbox_policy_identity,
            "secret_environment_keys": sorted(self.secret_environment_keys),
            "termination_grace_seconds": self.termination_grace_seconds,
        }

    @property
    def identity_sha256(self) -> str:
        return _sha256(_canonical_json(self.as_record()))


def current_adapter_identity() -> harness.AdapterIdentity:
    source = Path(__file__).resolve(strict=True)
    return harness.AdapterIdentity(
        name=ADAPTER_NAME,
        version=ADAPTER_VERSION,
        source_realpath=str(source),
        source_sha256=_sha256(source.read_bytes()),
    )


def make_cto_policy(
    provider_binary: harness.ProviderBinaryIdentity,
    *,
    adapter_source: harness.AdapterIdentity | None = None,
    timeout_seconds: float = 300.0,
    expected_tools: Sequence[str] = CTO_TOOLSET,
    expected_agents: Sequence[str] = (),
    expected_mcp_servers: Sequence[str] = (),
    expected_plugins: Sequence[str] = (),
    sandbox_policy_identity: str | None = None,
    secret_environment_keys: Sequence[str] = (),
    termination_grace_seconds: float = 2.0,
) -> ClaudeProviderPolicy:
    """Build the fixed policy without accepting a treatment condition."""

    return ClaudeProviderPolicy(
        provider_binary=provider_binary,
        adapter_source=adapter_source or current_adapter_identity(),
        argv_template=build_cto_argv(provider_binary.executable),
        model_id=CTO_MODEL_ID,
        effort=CTO_EFFORT,
        max_budget_usd=CTO_MAX_BUDGET_USD,
        max_turns=CTO_MAX_TURNS,
        timeout_seconds=timeout_seconds,
        toolset=CTO_TOOLSET,
        permission_policy=CTO_PERMISSION_MODE,
        expected_tools=tuple(expected_tools),
        expected_agents=tuple(expected_agents),
        expected_mcp_servers=tuple(expected_mcp_servers),
        expected_plugins=tuple(expected_plugins),
        sandbox_policy_identity=(
            observed_sandbox_policy_identity()
            if sandbox_policy_identity is None
            else sandbox_policy_identity
        ),
        secret_environment_keys=frozenset(secret_environment_keys),
        termination_grace_seconds=termination_grace_seconds,
    )


def resolve_provider_binary_identity(
    executable: str,
    *,
    version_runner: Callable[..., Any] = subprocess.run,
) -> harness.ProviderBinaryIdentity:
    """Resolve realpath/hash/version before a paid provider invocation."""

    located = shutil.which(executable) if os.sep not in executable else executable
    if not located:
        raise FileNotFoundError("provider executable was not found")
    realpath = Path(located).resolve(strict=True)
    digest = _sha256(realpath.read_bytes())
    result = version_runner(
        [str(realpath), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
        env={},
        close_fds=True,
        timeout=10.0,
    )
    if result.returncode != 0:
        raise RuntimeError("provider version probe failed")
    version_bytes = result.stdout if isinstance(result.stdout, bytes) else b""
    version = version_bytes.decode("utf-8", errors="strict").strip()
    if not version:
        raise RuntimeError("provider version probe returned no version")
    return harness.ProviderBinaryIdentity(
        executable=executable,
        realpath=str(realpath),
        sha256=digest,
        version=version,
    )


def _is_secret_key(key: str, policy: ClaudeProviderPolicy) -> bool:
    return key in policy.secret_environment_keys or _SECRET_KEY_RE.search(key) is not None


def _invocation_evidence(
    invocation: harness.ModelInvocation,
    policy: ClaudeProviderPolicy,
) -> tuple[harness.ProviderInvocationEvidence, bytes, tuple[bytes, ...], tuple[str, ...]]:
    errors: list[str] = []
    try:
        stdin_bytes = invocation.prompt.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        stdin_bytes = invocation.prompt.encode("utf-8", errors="surrogatepass")
        errors.append("stdin_is_not_valid_utf8")

    environment: list[harness.EnvironmentValueEvidence] = []
    secret_text = tuple(
        value for key, value in invocation.environment.items()
        if _is_secret_key(key, policy) and value
    )
    secret_values = tuple(value.encode("utf-8", errors="surrogatepass") for value in secret_text)
    for key, value in sorted(invocation.environment.items()):
        try:
            value_bytes = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            value_bytes = value.encode("utf-8", errors="surrogatepass")
            errors.append("environment_value_not_valid_utf8")
        redacted = _is_secret_key(key, policy) or any(
            secret in value_bytes for secret in secret_values
        )
        safe_key = harness._redact_text(key, secret_text)
        if safe_key != key:
            errors.append("secret_value_redacted_from_environment_key")
        environment.append(
            harness.EnvironmentValueEvidence(
                key=safe_key,
                value="REDACTED" if redacted else value,
                sha256_fingerprint=_sha256(value_bytes),
                redacted=redacted,
            )
        )

    safe_argv = tuple(harness._redact_text(value, secret_text) for value in invocation.argv)
    safe_cwd = harness._redact_text(invocation.cwd, secret_text)
    safe_run_id = harness._redact_text(invocation.task_visible_run_id, secret_text)
    if (safe_argv, safe_cwd, safe_run_id) != (
        tuple(invocation.argv), invocation.cwd, invocation.task_visible_run_id
    ):
        errors.append("secret_value_redacted_from_invocation")
    evidence = harness.ProviderInvocationEvidence(
        argv=safe_argv,
        cwd=safe_cwd,
        task_visible_run_id=safe_run_id,
        stdin_sha256=_sha256(stdin_bytes),
        stdin_length=len(stdin_bytes),
        environment=tuple(environment),
        shell=False,
        close_fds=True,
        start_new_session=True,
        timeout_seconds=policy.timeout_seconds,
    )
    return evidence, stdin_bytes, tuple(secret_values), tuple(errors)


def _flag_name(argument: str) -> str | None:
    if not argument.startswith("--"):
        return None
    return argument.split("=", 1)[0]


def _value_after(argv: Sequence[str], flag: str) -> str | None:
    indices = [index for index, value in enumerate(argv) if value == flag]
    if len(indices) != 1 or indices[0] + 1 >= len(argv):
        return None
    return argv[indices[0] + 1]


def _validate_preflight(
    invocation: harness.ModelInvocation,
    policy: ClaudeProviderPolicy,
    adapter: harness.AdapterIdentity,
    binary: harness.ProviderBinaryIdentity,
) -> tuple[str, ...]:
    errors: list[str] = []
    flags = {_flag_name(argument) for argument in invocation.argv}
    for prohibited in sorted(PROHIBITED_FLAGS):
        if prohibited in flags:
            errors.append(f"prohibited_flag:{prohibited}")
    if tuple(invocation.argv) != policy.argv_template:
        errors.append("invocation_argv_policy_mismatch")
    if _value_after(invocation.argv, "--model") != policy.model_id:
        errors.append("requested_model_mismatch_or_fallback_substitution")
    if _value_after(invocation.argv, "--effort") != policy.effort:
        errors.append("invocation_effort_policy_mismatch")
    if _value_after(invocation.argv, "--max-budget-usd") != _usd.canonical_usd_text(
        policy.max_budget_usd
    ):
        errors.append("invocation_budget_policy_mismatch")
    if _value_after(invocation.argv, "--max-turns") != str(policy.max_turns):
        errors.append("invocation_max_turns_policy_mismatch")
    if _value_after(invocation.argv, "--permission-mode") != policy.permission_policy:
        errors.append("invocation_permission_policy_mismatch")
    if _value_after(invocation.argv, "--add-dir") != ".":
        errors.append("invocation_add_dir_mismatch")
    cwd_name = Path(invocation.cwd).name
    if _OPAQUE_SESSION_RE.fullmatch(cwd_name) is None:
        errors.append("cwd_is_not_opaque_session_workspace")
    if invocation.task_visible_run_id != cwd_name:
        errors.append("task_visible_run_id_does_not_match_opaque_workspace")
    if adapter.as_record() != policy.adapter_source.as_record():
        errors.append("adapter_source_identity_mismatch")
    if binary.as_record() != policy.provider_binary.as_record():
        errors.append("provider_binary_identity_mismatch")
    return _unique(errors)


def _no_sanctioned_process_factory(*_args: Any, **_kwargs: Any) -> Any:
    """The refusing default: there is no implicit unsandboxed launch path."""

    raise harness.HarnessContractError(
        "an explicit sandboxed process factory is required; unsandboxed "
        "subprocess.Popen is not a production default"
    )


#: Sentinel default.  Production supplies the sandboxed launcher built by
#: ``epoch_provider_composition``; tests supply a fake process factory.  Either
#: way the caller must choose, and choosing nothing fails before any spawn.
REQUIRED_PROCESS_FACTORY: Callable[..., Any] = _no_sanctioned_process_factory


def _require_explicit_process_factory(popen_factory: Callable[..., Any]) -> None:
    if popen_factory is REQUIRED_PROCESS_FACTORY:
        raise harness.HarnessContractError(
            "no explicit process factory was supplied; refusing before spawn"
        )


def _empty_process(policy: ClaudeProviderPolicy, status: str) -> harness.ProcessEvidence:
    return harness.ProcessEvidence(
        pid=None,
        pgid=None,
        returncode=None,
        status=status,
        timed_out=False,
        termination_attempted=False,
        termination_method=None,
        signals_sent=(),
        shell=False,
        close_fds=True,
        start_new_session=True,
        timeout_seconds=policy.timeout_seconds,
    )


def _bytes_or_empty(value: Any) -> bytes:
    return value if isinstance(value, bytes) else b""


def _run_process(
    invocation: harness.ModelInvocation,
    policy: ClaudeProviderPolicy,
    stdin_bytes: bytes,
    *,
    popen_factory: Callable[..., Any],
    group_signal: Callable[[int, int], Any],
) -> tuple[harness.ProcessEvidence, bytes, bytes, tuple[str, ...]]:
    errors: list[str] = []
    try:
        process = popen_factory(
            tuple(invocation.argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=invocation.cwd,
            env=dict(invocation.environment),
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
    except Exception as exc:
        return (
            _empty_process(policy, "START_FAILED"),
            b"",
            b"",
            (f"subprocess_start_failed:{type(exc).__name__}",),
        )

    pid_value = getattr(process, "pid", None)
    pid = pid_value if type(pid_value) is int and pid_value > 0 else None
    pgid = pid  # start_new_session=True makes the child its process-group leader.
    signals_sent: list[int] = []
    timed_out = False
    termination_attempted = False
    termination_method: str | None = None
    status = "EXITED"
    stdout = b""
    stderr = b""

    try:
        stdout_value, stderr_value = process.communicate(
            input=stdin_bytes, timeout=policy.timeout_seconds
        )
        stdout = _bytes_or_empty(stdout_value)
        stderr = _bytes_or_empty(stderr_value)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        termination_attempted = True
        status = "TIMED_OUT"
        stdout = _bytes_or_empty(exc.output)
        stderr = _bytes_or_empty(exc.stderr)
        if pgid is None:
            errors.append("timeout_without_process_group_id")
        else:
            try:
                group_signal(pgid, signal.SIGTERM)
                signals_sent.append(signal.SIGTERM)
                termination_method = "PROCESS_GROUP_SIGTERM"
            except Exception as signal_exc:
                errors.append(
                    f"process_group_sigterm_failed:{type(signal_exc).__name__}"
                )
        try:
            final_stdout, final_stderr = process.communicate(
                timeout=policy.termination_grace_seconds
            )
            stdout = _bytes_or_empty(final_stdout) or stdout
            stderr = _bytes_or_empty(final_stderr) or stderr
            status = "TIMED_OUT_TERMINATED"
        except subprocess.TimeoutExpired as second:
            stdout = _bytes_or_empty(second.output) or stdout
            stderr = _bytes_or_empty(second.stderr) or stderr
            if pgid is None:
                errors.append("kill_without_process_group_id")
            else:
                try:
                    group_signal(pgid, signal.SIGKILL)
                    signals_sent.append(signal.SIGKILL)
                    termination_method = "PROCESS_GROUP_SIGTERM_THEN_SIGKILL"
                except Exception as signal_exc:
                    errors.append(
                        f"process_group_sigkill_failed:{type(signal_exc).__name__}"
                    )
            try:
                final_stdout, final_stderr = process.communicate(
                    timeout=policy.termination_grace_seconds
                )
                stdout = _bytes_or_empty(final_stdout) or stdout
                stderr = _bytes_or_empty(final_stderr) or stderr
                status = "TIMED_OUT_KILLED"
            except Exception as communicate_exc:
                stdout = _bytes_or_empty(getattr(communicate_exc, "output", None)) or stdout
                stderr = _bytes_or_empty(getattr(communicate_exc, "stderr", None)) or stderr
                errors.append(
                    f"post_kill_communicate_failed:{type(communicate_exc).__name__}"
                )
                status = "TIMED_OUT_TERMINATION_UNRESOLVED"
        except Exception as communicate_exc:
            stdout = _bytes_or_empty(getattr(communicate_exc, "output", None)) or stdout
            stderr = _bytes_or_empty(getattr(communicate_exc, "stderr", None)) or stderr
            errors.append(
                f"post_sigterm_communicate_failed:{type(communicate_exc).__name__}"
            )
            status = "TIMED_OUT_TERMINATION_UNRESOLVED"
        errors.append("process_timeout")
    except Exception as exc:
        stdout = _bytes_or_empty(getattr(exc, "output", None))
        stderr = _bytes_or_empty(getattr(exc, "stderr", None))
        errors.append(f"process_communicate_failed:{type(exc).__name__}")
        status = "COMMUNICATION_FAILED"

    returncode_value = getattr(process, "returncode", None)
    returncode = returncode_value if type(returncode_value) is int else None
    if returncode is None:
        try:
            polled = process.poll()
        except Exception:
            polled = None
        returncode = polled if type(polled) is int else None
    if not timed_out and returncode != 0:
        errors.append(f"process_exit_nonzero:{returncode}")

    evidence = harness.ProcessEvidence(
        pid=pid,
        pgid=pgid,
        returncode=returncode,
        status=status,
        timed_out=timed_out,
        termination_attempted=termination_attempted,
        termination_method=termination_method,
        signals_sent=tuple(signals_sent),
        shell=False,
        close_fds=True,
        start_new_session=True,
        timeout_seconds=policy.timeout_seconds,
    )
    return evidence, stdout, stderr, _unique(errors)


def _redact_secret_bytes(
    raw: bytes, secret_values: Sequence[bytes], stream_name: str
) -> tuple[bytes, tuple[str, ...]]:
    if not secret_values:
        return raw, ()
    secret_text = tuple(secret.decode("utf-8", errors="surrogatepass") for secret in secret_values)

    def redact_json_string(match: re.Match[bytes]) -> bytes:
        try:
            value = json.loads(match.group().decode("utf-8"))
        except (UnicodeError, ValueError):
            return match.group()
        safe_value = harness._redact_text(value, secret_text)
        if safe_value == value:
            return match.group()
        return json.dumps(safe_value, ensure_ascii=True).encode("utf-8")

    # Decode individual JSON string tokens, including keys, without normalizing
    # the rest of the stream or hiding malformed records / duplicate keys.
    redacted = re.sub(rb'"(?:[^"\\]|\\.)*"', redact_json_string, raw)
    candidates = {secret for secret in secret_values if secret}
    candidates.update(
        json.dumps(secret, ensure_ascii=ascii_only)[1:-1].encode("utf-8", errors="surrogatepass")
        for secret in secret_text
        for ascii_only in (False, True)
        if secret
    )
    if candidates:
        pattern = b"|".join(re.escape(secret) for secret in sorted(candidates, key=len, reverse=True))
        redacted = re.sub(pattern, b"REDACTED", redacted)
    if redacted != raw:
        return redacted, (f"secret_value_redacted_from_{stream_name}",)
    return redacted, ()


class _DuplicateJSONKey(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(key)
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-json constant {value}")


def parse_raw_jsonl(
    raw_stdout: bytes,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[str, ...]]:
    """Strictly parse JSONL while retaining records before a malformed line."""

    records: list[Mapping[str, Any]] = []
    errors: list[str] = []
    if not raw_stdout:
        return (), ("raw_stdout_empty",)
    for line_index, raw_line in enumerate(raw_stdout.splitlines()):
        if not raw_line:
            errors.append(f"empty_jsonl_record:{line_index}")
            continue
        try:
            text = raw_line.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            errors.append(f"malformed_utf8:{line_index}")
            continue
        try:
            value = json.loads(
                text,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_non_json_constant,
            )
            # json.loads accepts overflowing floats and escaped lone surrogates.
            # Reject either before it can make evidence sealing lose the stream.
            _canonical_json(value)
            value = copy.deepcopy(value)
        except _DuplicateJSONKey as exc:
            safe_key = json.dumps(exc.key, ensure_ascii=True)[1:-1]
            errors.append(f"duplicate_json_key:{line_index}:{safe_key}")
            continue
        except (ValueError, UnicodeError, RecursionError) as exc:
            errors.append(f"malformed_jsonl:{line_index}:{type(exc).__name__}")
            continue
        if not isinstance(value, Mapping):
            errors.append(f"jsonl_record_not_object:{line_index}")
            continue
        records.append(dict(value))
    return tuple(records), _unique(errors)


def _inventory_identifiers(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, Mapping):
        if any(key in value for key in ("name", "id", "tool", "agent", "plugin")):
            values = [
                value[key]
                for key in ("name", "id", "tool", "agent", "plugin")
                if key in value
            ]
        else:
            values = list(value.keys())
    elif isinstance(value, (list, tuple)):
        values = []
        for item in value:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, Mapping):
                identities = [
                    item[key]
                    for key in ("name", "id", "tool", "agent", "plugin")
                    if key in item
                ]
                if len(identities) != 1:
                    return None
                values.append(identities[0])
            else:
                return None
    else:
        return None
    if not all(isinstance(item, str) and item for item in values):
        return None
    return tuple(values)


def _inventory_from_init(
    init_record: Mapping[str, Any], dimension: str
) -> tuple[str, ...] | None:
    aliases = _INVENTORY_ALIASES[dimension]
    found = [init_record[alias] for alias in aliases if alias in init_record]
    if len(found) != 1:
        return None
    return _inventory_identifiers(found[0])


def _permission_from_init(init_record: Mapping[str, Any]) -> str | None:
    found = [init_record[alias] for alias in _PERMISSION_ALIASES if alias in init_record]
    if len(found) != 1 or not isinstance(found[0], str):
        return None
    return found[0]


def _main_loop_assistant(record: Mapping[str, Any]) -> bool:
    return (
        record.get("type") == "assistant"
        and not record.get("parent_tool_use_id")
        and not record.get("is_subagent")
        and not record.get("subagent")
    )


@dataclass(frozen=True)
class _RecordAnalysis:
    init_index: int | None
    assistant_message_ids: tuple[str, ...]
    terminal_result_index: int | None
    session_id: str | None
    model_usage: Mapping[str, Any] | None
    total_cost_usd: Decimal | None
    errors: tuple[str, ...]


def _analyze_records(
    records: Sequence[Mapping[str, Any]], policy: ClaudeProviderPolicy
) -> _RecordAnalysis:
    errors: list[str] = []
    init_indices = [
        index
        for index, record in enumerate(records)
        if record.get("type") == "system" and record.get("subtype") == "init"
    ]
    result_indices = [
        index for index, record in enumerate(records) if record.get("type") == "result"
    ]
    if not init_indices:
        errors.append("missing_init")
    elif len(init_indices) > 1:
        errors.append("multiple_init")
    if not result_indices:
        errors.append("missing_terminal_result")
    elif len(result_indices) > 1:
        errors.append("multiple_terminal_result")
    if len(result_indices) == 1 and result_indices[0] != len(records) - 1:
        errors.append("terminal_result_not_last")

    main_assistant_indices = [
        index for index, record in enumerate(records) if _main_loop_assistant(record)
    ]
    if not main_assistant_indices:
        errors.append("missing_main_loop_assistant")

    assistant_ids: list[str] = []
    for index, record in enumerate(records):
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        message_id = message.get("id") if isinstance(message, Mapping) else None
        if not isinstance(message_id, str) or not message_id:
            errors.append(f"assistant_message_id_missing:{index}")
        else:
            assistant_ids.append(message_id)

    required_session_indices = set(init_indices + result_indices)
    session_values: list[str] = []
    for index, record in enumerate(records):
        if (
            index not in required_session_indices
            and record.get("type") != "assistant"
            and "session_id" not in record
        ):
            continue
        session_id = record.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            errors.append(f"session_id_missing:{index}")
        else:
            session_values.append(session_id)
    unique_sessions = tuple(dict.fromkeys(session_values))
    if len(unique_sessions) != 1:
        errors.append("session_ids_inconsistent")
    session_id = unique_sessions[0] if len(unique_sessions) == 1 else None

    init_record = records[init_indices[0]] if len(init_indices) == 1 else None
    if init_record is not None:
        init_model = init_record.get("model")
        if init_model != policy.model_id:
            errors.append("init_model_mismatch")
        if init_record.get("effort", policy.effort) != policy.effort:
            errors.append("init_effort_mismatch")
        expected_inventories = {
            "tools": policy.expected_tools,
            "agents": policy.expected_agents,
            "mcp_servers": policy.expected_mcp_servers,
            "plugins": policy.expected_plugins,
        }
        for dimension, expected in expected_inventories.items():
            actual = _inventory_from_init(init_record, dimension)
            if actual is None:
                errors.append(f"init_{dimension}_unresolved")
            elif actual != expected:
                errors.append(f"init_{dimension}_mismatch")
        if _permission_from_init(init_record) != policy.permission_policy:
            errors.append("init_permission_mode_mismatch")

    for index in main_assistant_indices:
        message = records[index].get("message")
        model = message.get("model") if isinstance(message, Mapping) else None
        if model != policy.model_id:
            errors.append(f"assistant_model_mismatch:{index}")

    terminal = records[result_indices[0]] if len(result_indices) == 1 else None
    model_usage: Mapping[str, Any] | None = None
    total_cost: Decimal | None = None
    if terminal is not None:
        if terminal.get("subtype") != "success" or terminal.get("is_error", False) is not False:
            errors.append("terminal_result_reports_error")
        raw_usage = terminal.get("modelUsage")
        if not isinstance(raw_usage, Mapping) or not raw_usage:
            errors.append("modelUsage_missing")
        else:
            model_usage = copy.deepcopy(dict(raw_usage))
            if not harness._valid_model_usage(model_usage):
                errors.append("modelUsage_invalid")
            if policy.model_id not in model_usage:
                errors.append("modelUsage_requested_model_missing")
        raw_cost = terminal.get("total_cost_usd")
        if isinstance(raw_cost, bool) or raw_cost is None:
            errors.append("total_cost_usd_missing_or_invalid")
        else:
            try:
                total_cost = Decimal(str(raw_cost))
            except (InvalidOperation, ValueError):
                errors.append("total_cost_usd_missing_or_invalid")
            else:
                if not total_cost.is_finite():
                    errors.append("total_cost_usd_not_finite")
                elif total_cost < 0:
                    errors.append("total_cost_usd_negative")
                elif total_cost > policy.max_budget_usd:
                    errors.append("total_cost_usd_exceeds_policy")
        turns = terminal.get("num_turns")
        if "num_turns" in terminal and (
            type(turns) is not int or turns < 0 or turns > policy.max_turns
        ):
            errors.append("terminal_turn_count_exceeds_or_violates_policy")

    return _RecordAnalysis(
        init_index=init_indices[0] if len(init_indices) == 1 else None,
        assistant_message_ids=tuple(assistant_ids),
        terminal_result_index=(result_indices[0] if len(result_indices) == 1 else None),
        session_id=session_id,
        model_usage=model_usage,
        total_cost_usd=total_cost,
        errors=_unique(errors),
    )


def execute_claude(
    invocation: harness.ModelInvocation,
    policy: ClaudeProviderPolicy,
    *,
    popen_factory: Callable[..., Any] = REQUIRED_PROCESS_FACTORY,
    binary_identity_resolver: Callable[[str], harness.ProviderBinaryIdentity] = (
        resolve_provider_binary_identity
    ),
    adapter_identity_resolver: Callable[[], harness.AdapterIdentity] = (
        current_adapter_identity
    ),
    group_signal: Callable[[int, int], Any] = os.killpg,
    sandbox_identity_resolver: Callable[[], Mapping[str, Any] | None] | None = None,
) -> harness.ProviderExecution:
    """Execute one exact provider call and always return sealed evidence.

    ``popen_factory`` has no implicit production default: omitting it raises
    before any identity resolution or spawn.  ``sandbox_identity_resolver``
    lets the composition report the physical, sandbox-prefixed lane so both
    argv lanes are sealed separately.
    """

    _require_explicit_process_factory(popen_factory)
    invocation_evidence, stdin_bytes, secret_values, invocation_errors = (
        _invocation_evidence(invocation, policy)
    )
    errors: list[str] = list(invocation_errors)

    try:
        adapter_identity = adapter_identity_resolver()
    except Exception as exc:
        adapter_identity = harness.AdapterIdentity(
            name=ADAPTER_NAME,
            version=ADAPTER_VERSION,
            source_realpath=None,
            source_sha256=None,
        )
        errors.append(f"adapter_identity_resolution_failed:{type(exc).__name__}")
    try:
        binary_identity = binary_identity_resolver(policy.provider_binary.executable)
    except Exception as exc:
        binary_identity = harness.ProviderBinaryIdentity(
            executable=policy.provider_binary.executable,
            realpath=None,
            sha256=None,
            version=None,
        )
        errors.append(f"provider_binary_resolution_failed:{type(exc).__name__}")

    errors.extend(
        _validate_preflight(invocation, policy, adapter_identity, binary_identity)
    )
    if errors:
        process = _empty_process(policy, "PREFLIGHT_REJECTED")
        raw_stdout = b""
        raw_stderr = b""
        process_errors: tuple[str, ...] = ()
    else:
        process, raw_stdout, raw_stderr, process_errors = _run_process(
            invocation,
            policy,
            stdin_bytes,
            popen_factory=popen_factory,
            group_signal=group_signal,
        )
    errors.extend(process_errors)

    safe_stdout, stdout_secret_errors = _redact_secret_bytes(
        raw_stdout, secret_values, "stdout"
    )
    safe_stderr, stderr_secret_errors = _redact_secret_bytes(
        raw_stderr, secret_values, "stderr"
    )
    errors.extend(stdout_secret_errors)
    errors.extend(stderr_secret_errors)
    records, parse_errors = parse_raw_jsonl(safe_stdout)
    errors.extend(parse_errors)
    analysis = _analyze_records(records, policy)
    errors.extend(analysis.errors)

    secret_text = tuple(value.decode("utf-8", errors="surrogatepass") for value in secret_values)
    safe_adapter = replace(
        adapter_identity,
        name=harness._redact_text(adapter_identity.name, secret_text),
        version=harness._redact_text(adapter_identity.version, secret_text),
        source_realpath=(
            None if adapter_identity.source_realpath is None
            else harness._redact_text(adapter_identity.source_realpath, secret_text)
        ),
    )
    safe_binary = replace(
        binary_identity,
        executable=harness._redact_text(binary_identity.executable, secret_text),
        realpath=(
            None if binary_identity.realpath is None
            else harness._redact_text(binary_identity.realpath, secret_text)
        ),
        version=(
            None if binary_identity.version is None
            else harness._redact_text(binary_identity.version, secret_text)
        ),
    )
    safe_policy, policy_secret_errors = _redact_secret_bytes(
        _canonical_json(policy.as_record()), secret_values, "provider_policy"
    )
    errors.extend(policy_secret_errors)
    if safe_adapter != adapter_identity or safe_binary != binary_identity:
        errors.append("secret_value_redacted_from_provider_identity")
    sandbox_identity: Mapping[str, Any] | None = None
    if sandbox_identity_resolver is not None:
        try:
            observed = sandbox_identity_resolver()
        except Exception as exc:
            errors.append(f"sandbox_identity_resolution_failed:{type(exc).__name__}")
        else:
            if observed is None:
                sandbox_identity = None
            elif isinstance(observed, Mapping):
                sandbox_identity = json.loads(
                    _redact_secret_bytes(
                        _canonical_json(dict(observed)), secret_values, "sandbox_identity"
                    )[0]
                )
            else:
                errors.append("sandbox_identity_not_a_mapping")

    execution = harness.ProviderExecution(
        adapter_identity=safe_adapter,
        provider_binary_identity=safe_binary,
        provider_policy_identity=policy.identity_sha256,
        provider_policy=json.loads(safe_policy),
        invocation=invocation_evidence,
        process=process,
        raw_stdout=safe_stdout,
        raw_stderr=safe_stderr,
        raw_jsonl_records=records,
        init_index=analysis.init_index,
        assistant_message_ids=analysis.assistant_message_ids,
        terminal_result_index=analysis.terminal_result_index,
        session_id=analysis.session_id,
        model_usage=analysis.model_usage,
        total_cost_usd=analysis.total_cost_usd,
        validation_errors=_unique(
            [harness._redact_text(error, secret_text) for error in errors]
        ),
        sandbox_identity=sandbox_identity,
    )
    return execution.sealed()


@dataclass(frozen=True)
class ClaudeExecutor:
    """Callable adapter suitable for ``ProviderExecutor`` injection.

    Construction without an explicit process factory fails immediately, so an
    executor that could reach an unsandboxed ``subprocess.Popen`` cannot even
    be built, let alone called.
    """

    policy: ClaudeProviderPolicy
    popen_factory: Callable[..., Any] = REQUIRED_PROCESS_FACTORY
    binary_identity_resolver: Callable[[str], harness.ProviderBinaryIdentity] = (
        resolve_provider_binary_identity
    )
    adapter_identity_resolver: Callable[[], harness.AdapterIdentity] = (
        current_adapter_identity
    )
    group_signal: Callable[[int, int], Any] = os.killpg
    sandbox_identity_resolver: Callable[[], Mapping[str, Any] | None] | None = None

    def __post_init__(self) -> None:
        _require_explicit_process_factory(self.popen_factory)

    def __call__(self, invocation: harness.ModelInvocation) -> harness.ProviderExecution:
        return execute_claude(
            invocation,
            self.policy,
            popen_factory=self.popen_factory,
            binary_identity_resolver=self.binary_identity_resolver,
            adapter_identity_resolver=self.adapter_identity_resolver,
            group_signal=self.group_signal,
            sandbox_identity_resolver=self.sandbox_identity_resolver,
        )


@dataclass(frozen=True)
class ConditionNeutralityComparison:
    checks: Mapping[str, bool]
    errors: tuple[str, ...]
    passed: bool

    def as_record(self) -> dict[str, Any]:
        return {
            "checks": dict(self.checks),
            "errors": list(self.errors),
            "passed": self.passed,
        }


def _normalize_opaque_root(argv: Sequence[str], cwd: str) -> tuple[str, ...]:
    candidates = {cwd, os.path.realpath(cwd)}
    normalized: list[str] = []
    for argument in argv:
        value = argument
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate:
                value = value.replace(candidate, "<OPAQUE_ROOT>")
        normalized.append(value)
    return tuple(normalized)


def _environment_fingerprints(
    execution: harness.ProviderExecution, *, redacted: bool
) -> dict[str, str]:
    return {
        item.key: item.sha256_fingerprint
        for item in execution.invocation.environment
        if item.redacted is redacted
    }


def _observed_policy_sha256(execution: harness.ProviderExecution) -> str | None:
    if execution.sandbox_identity is None:
        return None
    value = execution.sandbox_identity.get("profile_sha256")
    return value if isinstance(value, str) else None


def _init_record(execution: harness.ProviderExecution) -> Mapping[str, Any] | None:
    index = execution.init_index
    if index is None or index < 0 or index >= len(execution.raw_jsonl_records):
        return None
    return execution.raw_jsonl_records[index]


def _carrier_inventory(init_record: Mapping[str, Any] | None) -> frozenset[str] | None:
    if init_record is None:
        return None
    evidence = harness.parse_init_events([init_record])
    if not evidence.valid or not evidence.skills_resolved:
        return None
    return evidence.skill_identities


def compare_condition_neutral_evidence(
    left: harness.ProviderExecution,
    right: harness.ProviderExecution,
    *,
    allowed_fable_carrier: str = FABLE_CARRIER_IDENTITY,
) -> ConditionNeutralityComparison:
    """Compare OFF/ON evidence without accepting either condition label."""

    left_init = _init_record(left)
    right_init = _init_record(right)
    checks: dict[str, bool] = {
        "sealed_provider_evidence": left.evidence_sealed and right.evidence_sealed,
        "adapter_identity_equal": (
            left.adapter_identity.as_record() == right.adapter_identity.as_record()
        ),
        "provider_binary_identity_equal": (
            left.provider_binary_identity.as_record()
            == right.provider_binary_identity.as_record()
        ),
        "provider_policy_identity_equal": (
            left.provider_policy_identity == right.provider_policy_identity
        ),
        "argv_equal_after_opaque_root_normalization": (
            _normalize_opaque_root(left.invocation.argv, left.invocation.cwd)
            == _normalize_opaque_root(right.invocation.argv, right.invocation.cwd)
        ),
        "prompt_hash_equal": (
            left.invocation.stdin_sha256 == right.invocation.stdin_sha256
            and left.invocation.stdin_length == right.invocation.stdin_length
        ),
        "environment_keys_equal": (
            left.invocation.environment_keys == right.invocation.environment_keys
        ),
        "secret_fingerprints_equal": (
            _environment_fingerprints(left, redacted=True)
            == _environment_fingerprints(right, redacted=True)
        ),
        "nonsecret_environment_values_equal": (
            _environment_fingerprints(left, redacted=False)
            == _environment_fingerprints(right, redacted=False)
        ),
    }

    for dimension in ("tools", "agents", "mcp_servers", "plugins"):
        left_inventory = (
            None if left_init is None else _inventory_from_init(left_init, dimension)
        )
        right_inventory = (
            None if right_init is None else _inventory_from_init(right_init, dimension)
        )
        checks[f"actual_init_{dimension}_equal"] = (
            left_inventory is not None
            and right_inventory is not None
            and left_inventory == right_inventory
        )

    # The physical lane is compared after the same opaque-root normalization as
    # the logical lane, so two distinct session roots stay condition-neutral.
    # An asymmetric lane -- one side sandboxed, the other not -- is a real
    # difference and must fail rather than be ignored.
    left_physical = left.physical_argv
    right_physical = right.physical_argv
    checks["physical_argv_lane_symmetric"] = (left_physical is None) == (
        right_physical is None
    )
    checks["physical_argv_equal_after_opaque_root_normalization"] = (
        left_physical is None and right_physical is None
    ) or (
        left_physical is not None
        and right_physical is not None
        and _normalize_opaque_root(left_physical, left.invocation.cwd)
        == _normalize_opaque_root(right_physical, right.invocation.cwd)
    )
    checks["observed_sandbox_policy_bytes_equal"] = _observed_policy_sha256(
        left
    ) == _observed_policy_sha256(right)

    left_permission = None if left_init is None else _permission_from_init(left_init)
    right_permission = None if right_init is None else _permission_from_init(right_init)
    checks["actual_init_permission_mode_equal"] = (
        left_permission is not None
        and right_permission is not None
        and left_permission == right_permission
    )

    left_carriers = _carrier_inventory(left_init)
    right_carriers = _carrier_inventory(right_init)
    checks["only_allowed_fable_carrier_inventory_delta"] = (
        left_carriers is not None
        and right_carriers is not None
        and left_carriers.symmetric_difference(right_carriers)
        <= {allowed_fable_carrier.casefold()}
    )
    errors = tuple(name for name, passed in checks.items() if not passed)
    return ConditionNeutralityComparison(
        checks=checks,
        errors=errors,
        passed=bool(checks) and all(checks.values()),
    )


__all__ = [
    "ADAPTER_NAME",
    "ADAPTER_VERSION",
    "REQUIRED_PROCESS_FACTORY",
    "SANDBOX_POLICY_IDENTITY_PREFIX",
    "SANDBOX_PROFILE_PATH",
    "observed_sandbox_policy_identity",
    "CTO_EFFORT",
    "CTO_MAX_BUDGET_USD",
    "CTO_MAX_TURNS",
    "CTO_MODEL_ID",
    "CTO_PERMISSION_MODE",
    "CTO_TOOLSET",
    "ClaudeExecutor",
    "ClaudeProviderPolicy",
    "ConditionNeutralityComparison",
    "PROHIBITED_FLAGS",
    "build_cto_argv",
    "compare_condition_neutral_evidence",
    "current_adapter_identity",
    "execute_claude",
    "make_cto_policy",
    "parse_raw_jsonl",
    "resolve_provider_binary_identity",
]
