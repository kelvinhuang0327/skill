#!/usr/bin/env python3
"""Summarize execution evidence, validate scratch paths, bound logs and manage worktree lifecycle.

This helper uses only the Python standard library. It treats durable captures,
receipts, certificates, and checksum-listed files as evidence and never trims
them. Its log writer owns only .fable/temporary/<task-id>/logs/observer*.jsonl.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

GIB = 1024**3
MIB = 1024**2
LOG_BUDGET = 10 * MIB
LOG_ROTATE_AT = 1 * MIB
MARKER_NAME = ".temporary-log-owner.json"
TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
ARCHIVE_RE = re.compile(r"observer\.(\d{8,})\.jsonl\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
SPACE_OPERATIONS = frozenset({
    "ordinary", "research-worktree", "high-disk-task", "read-only-diagnostic", "authorized-reclaim",
})


class ControlError(RuntimeError):
    pass


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "-c", "core.fsmonitor=false", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ControlError(f"cannot run git {args[0]}: {exc}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ControlError(f"git {' '.join(args)} failed: {detail}")
    return result


def canonical_directory(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ControlError(f"{label} must be an absolute path")
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ControlError(f"{label} cannot be read: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode) or path != resolved:
        raise ControlError(f"{label} must be a canonical, non-symlink directory")
    return resolved


def canonical_regular_file(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ControlError(f"{label} must be an absolute path")
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ControlError(f"{label} cannot be read: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or path != resolved:
        raise ControlError(f"{label} must be a canonical, non-symlink regular file")
    return resolved


def disk_decision(free_bytes: int, operation: str, phase: str) -> tuple[str, str | None]:
    if operation not in SPACE_OPERATIONS or phase not in {"before", "after"}:
        raise ControlError("unsupported disk-check operation or phase")
    restricted = operation in {"research-worktree", "high-disk-task"}
    if phase == "before" and restricted and free_bytes < 8 * GIB:
        return "WARN", "available space is below 8 GiB; continuing because low space only warns"
    if free_bytes < 15 * GIB:
        if operation in {"read-only-diagnostic", "authorized-reclaim"}:
            return "WARN_EXEMPT", "available space is below 15 GiB; this operation is exempt from the creation block"
        return "WARN", "available space is below 15 GiB"
    return "PASS", None


def check_space(path_text: str, operation: str, phase: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_absolute():
        raise ControlError("--path must be absolute")
    try:
        target = path.resolve(strict=True)
        free_bytes = shutil.disk_usage(target).free
    except OSError as exc:
        raise ControlError(f"disk space cannot be measured at {path}: {exc}") from exc
    status, message = disk_decision(free_bytes, operation, phase)
    return {
        "operation": operation,
        "phase": phase,
        "path": str(target),
        "available_bytes": free_bytes,
        "available_gib": round(free_bytes / GIB, 3),
        "warning_below_gib": 15,
        "low_space_warning_below_gib": 8,
        "status": status,
        "message": message,
    }


def launch_gated_command(path_text: str, operation: str, command: list[str]) -> dict[str, Any]:
    if not command:
        raise ControlError("a non-empty argv is required after --")
    if operation not in SPACE_OPERATIONS - {"research-worktree"}:
        raise ControlError("unsupported gated task operation")
    if operation == "research-worktree":
        raise ControlError("use create-worktree for research worktree creation")
    before = check_space(path_text, operation, "before")
    if before["message"]:
        print(json.dumps(before, sort_keys=True), file=sys.stderr)
    completed = subprocess.run(command, check=False)
    after = check_space(path_text, operation, "after")
    return {
        "status": "COMPLETED" if completed.returncode == 0 else "COMMAND_FAILED",
        "operation": operation,
        "command": command,
        "returncode": completed.returncode,
        "before": before,
        "after": after,
    }


def _safe_task_id(task_id: str) -> str:
    if TASK_ID_RE.fullmatch(task_id) is None or task_id in {".", ".."}:
        raise ControlError("task ID must be one safe path component")
    return task_id


def _mkdir_no_symlink(parent: Path, name: str) -> Path:
    candidate = parent / name
    try:
        candidate.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ControlError(f"cannot create temporary log directory {candidate}: {exc}") from exc
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise ControlError(f"temporary log directory cannot be read: {candidate}: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode) or candidate.resolve(strict=True) != candidate:
        raise ControlError(f"temporary log path contains a symlink or non-directory: {candidate}")
    return candidate


def _owned_log_paths(repo: Path, task_id: str) -> tuple[Path, Path]:
    task_parent = repo / ".fable" / "temporary"
    task_already_exists = (task_parent / task_id).exists() or (task_parent / task_id).is_symlink()
    marker_already_exists = (task_parent / task_id / MARKER_NAME).exists() or (
        task_parent / task_id / MARKER_NAME
    ).is_symlink()
    fable_dir = _mkdir_no_symlink(repo, ".fable")
    temporary_dir = _mkdir_no_symlink(fable_dir, "temporary")
    task_root = _mkdir_no_symlink(temporary_dir, task_id)
    logs_dir = _mkdir_no_symlink(task_root, "logs")

    marker = task_root / MARKER_NAME
    if marker.exists() or marker.is_symlink():
        marker_path = canonical_regular_file(str(marker), "temporary log ownership marker")
        try:
            data = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlError("temporary log ownership marker is malformed") from exc
        expected = {"format_version": 1, "task_id": task_id, "kind": "TEMPORARY_DEBUG_LOGS"}
        if data != expected:
            raise ControlError("temporary log ownership marker identity mismatch")
    else:
        if task_already_exists and (list(task_root.iterdir()) != [logs_dir] or list(logs_dir.iterdir())):
            raise ControlError("refusing to adopt a pre-existing temporary task directory")
        if marker_already_exists:
            raise ControlError("temporary log ownership marker is not a regular file")
        payload = json.dumps(
            {"format_version": 1, "task_id": task_id, "kind": "TEMPORARY_DEBUG_LOGS"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    for path in (task_root, logs_dir):
        for child in path.iterdir():
            name = child.name
            if name == MARKER_NAME or name == ".append.lock":
                continue
            if name == "SHA256SUMS" or name.startswith("SHA256SUMS_") or name.endswith(".sha256"):
                raise ControlError("temporary log directory contains a checksum reference; refusing rotation")
    return task_root, logs_dir


def _log_inventory(logs_dir: Path) -> tuple[Path | None, list[tuple[int, Path]], int]:
    active = logs_dir / "observer.jsonl"
    active_path: Path | None = None
    if active.exists() or active.is_symlink():
        active_path = canonical_regular_file(str(active), "observer log")
    archives: list[tuple[int, Path]] = []
    for path in logs_dir.iterdir():
        if path.name in {".append.lock", "observer.jsonl"}:
            continue
        match = ARCHIVE_RE.fullmatch(path.name)
        if match is None:
            raise ControlError(f"unrecognized entry in temporary log directory: {path.name}")
        archives.append((int(match.group(1)), canonical_regular_file(str(path), "rotated observer log")))
    archives.sort(key=lambda item: item[0])
    total = sum(path.stat().st_size for path in ([active_path] if active_path else []))
    total += sum(path.stat().st_size for _, path in archives)
    return active_path, archives, total


def _open_log_lock(path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ControlError(f"temporary log lock cannot be opened: {exc}") from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise ControlError("temporary log lock is not a private regular file")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def append_temporary_log(repo_text: str, task_id_text: str, record: dict[str, Any]) -> dict[str, Any]:
    task_id = _safe_task_id(task_id_text)
    repo = canonical_directory(repo_text, "repository")
    top = git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top).resolve(strict=True) != repo:
        raise ControlError("--repo must name the exact Git worktree root")
    if not isinstance(record, dict):
        raise ControlError("temporary observer record must be a JSON object")
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    line = encoded + b"\n"
    record_sha = hashlib.sha256(line).hexdigest()
    if len(line) > LOG_BUDGET:
        return {
            "status": "DROPPED_OVERSIZE",
            "bytes": len(line),
            "sha256": record_sha,
            "retained_log_bytes": None,
        }

    task_root, logs_dir = _owned_log_paths(repo, task_id)
    lock_fd = _open_log_lock(logs_dir / ".append.lock")
    try:
        active_path, archives, total_bytes = _log_inventory(logs_dir)
        if total_bytes + len(line) > LOG_BUDGET:
            return {
                "status": "DROPPED_CAPACITY",
                "bytes": len(line),
                "sha256": record_sha,
                "retained_log_bytes": total_bytes,
                "budget_bytes": LOG_BUDGET,
            }

        active = logs_dir / "observer.jsonl"
        active_size = active.stat().st_size if active_path else 0
        rotated = None
        if active_size and active_size + len(line) > LOG_ROTATE_AT:
            next_sequence = (archives[-1][0] + 1) if archives else 1
            rotated = logs_dir / f"observer.{next_sequence:08d}.jsonl"
            if rotated.exists() or rotated.is_symlink():
                raise ControlError("rotated observer log destination already exists")
            os.rename(active, rotated)

        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(active, flags, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise ControlError("observer log is not a private regular file")
        with os.fdopen(fd, "ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

        _active, _archives, retained = _log_inventory(logs_dir)
        if retained > LOG_BUDGET:
            raise ControlError("temporary log budget invariant failed")
        return {
            "status": "APPENDED",
            "bytes": len(line),
            "sha256": record_sha,
            "rotated": str(rotated) if rotated else None,
            "retained_log_bytes": retained,
            "budget_bytes": LOG_BUDGET,
            "task_root": str(task_root),
        }
    finally:
        os.close(lock_fd)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _test_scratch_violations(repo: Path, path_text: str) -> list[str]:
    raw = Path(path_text)
    if not raw.is_absolute() or "\n" in path_text or "\r" in path_text:
        return ["test scratch path must be absolute and contain no newlines"]
    resolved = raw.resolve(strict=False)
    candidates = (raw, resolved)
    violations: list[str] = []
    if any("b649" in str(candidate).casefold() for candidate in candidates):
        violations.append("test scratch path contains protected B649 token")
    if any(any(part.casefold() == "lotterynew" for part in candidate.parts) for candidate in candidates):
        violations.append("test scratch path contains protected LotteryNew component")
    if any(_is_within(candidate.resolve(strict=False), repo) for candidate in candidates):
        violations.append("test scratch path is inside the repository worktree")

    # Also catch a registered or unregistered nested worktree without relying on
    # its .git entry being a directory; linked worktrees use a .git file.
    cursor = resolved
    while True:
        try:
            (cursor / ".git").lstat()
        except FileNotFoundError:
            pass
        except OSError:
            violations.append("cannot safely inspect Git worktree boundary")
            break
        else:
            violations.append("test scratch path is inside a Git worktree")
            break
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return list(dict.fromkeys(violations))


def validate_test_scratch(repo_text: str, path_text: str) -> dict[str, Any]:
    repo = canonical_directory(repo_text, "repository")
    if Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve(strict=True) != repo:
        raise ControlError("--repo must name the exact Git worktree root")
    raw = Path(path_text)
    resolved = raw.resolve(strict=False) if raw.is_absolute() else raw
    violations = _test_scratch_violations(repo, path_text)
    return {
        "status": "BLOCKED" if violations else "PASS",
        "path": str(resolved),
        "violations": violations,
        "checked_boundaries": ["repository and nested Git worktrees", "protected B649 token", "protected LotteryNew component"],
    }


def _duration_seconds(start: Any, end: Any) -> float | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        started = datetime.fromisoformat(start.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    seconds = (ended - started).total_seconds()
    return round(seconds, 3) if seconds >= 0 else None


def _basetemp_argument(command: list[str]) -> str | None:
    for index, item in enumerate(command):
        if item == "--basetemp" and index + 1 < len(command):
            return command[index + 1]
        if item.startswith("--basetemp="):
            return item.split("=", 1)[1]
    return None


def _command_outcome(record_status: Any, exit_status: Any) -> str:
    if record_status != "COMPLETED":
        return "NONTERMINAL_RECORD"
    if exit_status in (0, "0"):
        return "SUCCESS"
    if isinstance(exit_status, str) and exit_status.startswith("SIGNALED:"):
        return "INTERRUPTED"
    if isinstance(exit_status, int):
        return "COMMAND_FAILED"
    return "UNKNOWN"


def _task_evidence_file(
    directory_fd: int | None,
    root: Path,
    name: str,
) -> tuple[bytes | None, dict[str, Any]]:
    locator = str(root / name)
    if directory_fd is None:
        return None, {
            "status": "MISSING",
            "locator": locator,
            "sha256": None,
            "task_id": None,
            "parse_status": "UNKNOWN",
            "lifecycle_status": "UNKNOWN",
        }

    try:
        file_fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return None, {
            "status": "MISSING",
            "locator": locator,
            "sha256": None,
            "task_id": None,
            "parse_status": "UNKNOWN",
            "lifecycle_status": "UNKNOWN",
        }
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise ControlError(f"task evidence file must not be a symlink: {locator}") from exc
        raise ControlError(f"cannot safely open task evidence file {locator}: {exc}") from exc

    try:
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ControlError(f"task evidence file is not a regular file: {locator}")
        with os.fdopen(file_fd, "rb") as handle:
            file_fd = -1
            raw = handle.read()
    finally:
        if file_fd >= 0:
            os.close(file_fd)
    return raw, {
        "status": "PRESENT",
        "locator": locator,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "task_id": None,
        "parse_status": "UNKNOWN",
        "lifecycle_status": "UNKNOWN",
    }


def _open_task_evidence_root(root_text: str, expected_task_id: str) -> tuple[Path, int | None]:
    root = Path(root_text)
    if not root.is_absolute():
        raise ControlError("--task-evidence-root must be an absolute path")
    if ".." in root.parts:
        raise ControlError("--task-evidence-root must not contain parent-directory traversal")
    root = Path(os.path.normpath(str(root)))
    if root.name != expected_task_id:
        raise ControlError("task evidence root directory name must match --task-id")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        current_fd = os.open(root.anchor, directory_flags)
    except OSError as exc:
        raise ControlError(f"cannot safely open task evidence root {root}: {exc}") from exc

    try:
        for component in root.parts[1:]:
            try:
                next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            except FileNotFoundError:
                os.close(current_fd)
                return root, None
            except OSError as exc:
                symlink_component = False
                if exc.errno in (errno.ELOOP, errno.EMLINK, errno.ENOTDIR):
                    try:
                        component_info = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
                        symlink_component = stat.S_ISLNK(component_info.st_mode)
                    except OSError:
                        pass
                if exc.errno in (errno.ELOOP, errno.EMLINK) or symlink_component:
                    raise ControlError(f"task evidence root must not contain symlinks: {root}") from exc
                raise ControlError(f"cannot safely open task evidence root {root}: {exc}") from exc
            os.close(current_fd)
            current_fd = next_fd
        return root, current_fd
    except Exception:
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reported_acceptance_passes(result_data: dict[str, Any]) -> list[str]:
    """Read PASS claims only from one explicit, structurally valid AC block."""
    candidates: list[tuple[str, Any, int]] = []
    for field, value in result_data.items():
        if not isinstance(field, str):
            continue
        match = re.fullmatch(r"(?i:ac01_to_ac)(\d+)", field)
        if match is None:
            continue
        endpoint = int(match.group(1))
        if endpoint < 1 or match.group(1) != f"{endpoint:02d}":
            return []
        candidates.append((field, value, endpoint))

    # Multiple declarations can disagree. Do not merge them or select one.
    if len(candidates) != 1:
        return []

    _, acceptance, endpoint = candidates[0]
    if not isinstance(acceptance, dict) or not acceptance:
        return []

    reported_passes: list[str] = []
    seen_ac = False
    for field, value in acceptance.items():
        if not isinstance(field, str):
            return []
        match = re.fullmatch(r"AC(\d+)", field)
        if match is None:
            return []
        number = int(match.group(1))
        if not 1 <= number <= endpoint or field != f"AC{number:02d}":
            return []
        if not isinstance(value, (dict, str)):
            return []
        seen_ac = True

        if isinstance(value, dict):
            is_pass = value.get("status") == "PASS"
        else:
            declaration = value.strip()
            is_pass = declaration == "PASS" or declaration.startswith("PASS:")
        if is_pass:
            reported_passes.append(field)

    return sorted(reported_passes) if seen_ac else []


def _process_signal_invalid_reasons(signal: Any) -> list[str]:
    if not isinstance(signal, dict):
        return ["process_signal: expected object"]

    invalid: list[str] = []
    allowed = {"trigger", "versions", "route", "cost", "issue"}
    for key in sorted(signal.keys() - allowed):
        invalid.append(f"process_signal.{key}: unexpected key")

    trigger = signal.get("trigger")
    if "trigger" not in signal:
        invalid.append("process_signal.trigger: required")
    elif not isinstance(trigger, str) or trigger not in {"INCIDENT", "HIGH_IMPACT"}:
        invalid.append("process_signal.trigger: expected INCIDENT or HIGH_IMPACT")

    string_fields = {
        "versions": {"planner", "fable", "planner_load_evidence", "fable_load_evidence"},
        "route": {"task_class", "worker_route", "depth", "model_tier"},
        "cost": {"evidence"},
        "issue": {"evidence", "rca", "smallest_fix"},
    }
    allowed_fields = {
        **string_fields,
        "cost": {"elapsed_ms", "tokens", "resources", "evidence"},
        "issue": {"type", "evidence", "rca", "smallest_fix"},
    }
    issue_types = {
        "PLANNER", "FABLE", "PLATFORM", "ENV", "PROJECT", "AUTH",
        "UNNECESSARY_WORK", "PERFORMANCE", "NONE", "UNKNOWN",
    }

    for section in ("versions", "route", "cost", "issue"):
        if section not in signal:
            continue
        value = signal[section]
        if not isinstance(value, dict):
            invalid.append(f"process_signal.{section}: expected object")
            continue
        for key in sorted(value.keys() - allowed_fields[section]):
            invalid.append(f"process_signal.{section}.{key}: unexpected key")
        for key, field_value in value.items():
            path = f"process_signal.{section}.{key}"
            if key in string_fields[section]:
                if not isinstance(field_value, str) or not field_value.strip():
                    invalid.append(f"{path}: expected non-empty string")
            elif section == "cost" and key in {"elapsed_ms", "tokens"}:
                if field_value is not None and (type(field_value) is not int or field_value < 0):
                    invalid.append(f"{path}: expected non-negative integer or null")
            elif section == "cost" and key == "resources":
                if field_value is not None and (not isinstance(field_value, str) or not field_value.strip()):
                    invalid.append(f"{path}: expected non-empty string or null")
            elif section == "issue" and key == "type" and (
                not isinstance(field_value, str) or field_value not in issue_types
            ):
                invalid.append(f"{path}: unsupported issue type")

    if trigger == "INCIDENT":
        issue = signal.get("issue")
        if not isinstance(issue, dict) or "type" not in issue:
            invalid.append("process_signal.issue.type: required for INCIDENT")

    return sorted(set(invalid))


def _packet_next_line_values(packet_text: str, name: str) -> list[str]:
    """Read empty header fields without crossing a heading or another field."""
    header_lines: list[str] = []
    for line in packet_text.splitlines():
        if re.match(r"^#{1,6}[ \t]+", line):
            break
        header_lines.append(line)
    values: list[str] = []
    for index, line in enumerate(header_lines):
        if re.fullmatch(rf"{re.escape(name)}:[ \t]*", line) is None:
            continue
        raw = next((line.strip() for line in header_lines[index + 1:] if line.strip()), "")
        values.append("" if re.match(r"^[A-Z][A-Z0-9_]*:", raw) else raw)
    return values


def _packet_header_value(packet_text: str | None, name: str) -> str:
    if packet_text is None:
        return "ABSENT"
    header_lines: list[str] = []
    for line in packet_text.splitlines():
        if re.match(r"^#{1,6}[ \t]+", line):
            break
        header_lines.append(line)
    header_text = "\n".join(header_lines)
    matches = re.findall(rf"(?m)^{re.escape(name)}:[^\r\n]*", header_text)
    if not matches:
        return "ABSENT"
    if len(matches) != 1:
        return "MALFORMED"
    raw = matches[0].split(":", 1)[1].strip()
    if not raw:
        raw = next(iter(_packet_next_line_values(packet_text, name)), "")
    if name == "SIGNAL_POLICY":
        return raw if raw in {"ALWAYS", "ON_INCIDENT"} else "MALFORMED"

    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", raw)
    if match is None:
        return "MALFORMED"
    task_id = match.group(0)
    remainder = raw[len(task_id):]
    if remainder and not remainder[0].isspace():
        return "MALFORMED"
    if task_id == "NONE":
        return "NONE"
    if TASK_ID_RE.fullmatch(task_id) is None:
        return "MALFORMED"
    return task_id


def _process_evidence_summary(
    directory_fd: int | None,
    identity_status: str,
    packet_text: str | None,
    result_data: dict[str, Any] | None,
) -> dict[str, Any]:
    if identity_status != "MATCH":
        return {
            "status": "NOT_EVALUATED",
            "reason": "TASK_IDENTITY_NOT_MATCH",
            "independently_validated": False,
        }

    if result_data is None:
        # A matching identity always comes from one valid result object.
        raise ControlError("matching Task evidence has no parsed result object")

    receipt_count = 0
    if directory_fd is not None:
        for name in os.listdir(directory_fd):
            if re.fullmatch(r"result\.before_[1-9][0-9]*\.json", name) is None:
                continue
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                receipt_count += 1

    lifecycle = result_data.get("task_lifecycle_state")
    canonical_lifecycle_states = {"IN_PROGRESS", "BLOCKED", "COMPLETED", "ABORTED"}
    if "task_lifecycle_state" not in result_data:
        lifecycle_check = "MISSING"
    elif isinstance(lifecycle, str) and lifecycle in canonical_lifecycle_states:
        lifecycle_check = "CANONICAL"
    else:
        lifecycle_check = "NON_CANONICAL"
    signal = {"status": "ABSENT"}
    if "process_signal" in result_data:
        process_signal = result_data["process_signal"]
        invalid_reasons = _process_signal_invalid_reasons(process_signal)
        signal = (
            {"status": "INVALID", "invalid_reasons": invalid_reasons}
            if invalid_reasons
            else {"status": "REPORTED", "value": process_signal}
        )

    return {
        "status": "EVALUATED",
        "independently_validated": False,
        "lifecycle_state_check": lifecycle_check,
        "packet_header": {
            "signal_policy": _packet_header_value(packet_text, "SIGNAL_POLICY"),
            "continues_from": _packet_header_value(packet_text, "CONTINUES_FROM"),
        },
        "prior_result_receipts": receipt_count,
        "signal": signal,
    }


def summarize_task_evidence(root_text: str, expected_task_id: str) -> dict[str, Any]:
    expected_task_id = _safe_task_id(expected_task_id)
    root, directory_fd = _open_task_evidence_root(root_text, expected_task_id)
    try:
        packet_bytes, packet = _task_evidence_file(directory_fd, root, "packet.md")
        result_bytes, result = _task_evidence_file(directory_fd, root, "result.json")
        result["lifecycle_status_source"] = "UNKNOWN"

        packet_text: str | None = None
        if packet_bytes is not None:
            try:
                packet_text = packet_bytes.decode("utf-8-sig")
                packet_ids = re.findall(r"(?m)^TASK_ID:[ \t]*([^\s]+)[ \t]*\r?$", packet_text)
                packet_ids.extend(
                    value for value in _packet_next_line_values(packet_text, "TASK_ID")
                    if TASK_ID_RE.fullmatch(value) is not None
                )
            except UnicodeDecodeError:
                packet_ids = []
            if len(packet_ids) == 1:
                packet["task_id"] = packet_ids[0]
                packet["parse_status"] = "VALID_TASK_ID"
                if packet_ids[0] != expected_task_id:
                    packet["status"] = "INVALID"
                    packet["parse_status"] = "TASK_ID_MISMATCH"
            else:
                packet["status"] = "INVALID"
                packet["parse_status"] = "INVALID_TASK_ID"

        result_data: dict[str, Any] | None = None
        if result_bytes is not None:
            try:
                parsed = json.loads(result_bytes.decode("utf-8"), object_pairs_hook=_unique_json_object)
            except (UnicodeDecodeError, ValueError):
                parsed = None
            if not isinstance(parsed, dict):
                result["status"] = "INVALID"
                result["parse_status"] = "INVALID_JSON"
            else:
                result_data = parsed
                result_task_id = parsed.get("task_id")
                if not isinstance(result_task_id, str) or not result_task_id:
                    result["status"] = "INVALID"
                    result["parse_status"] = "MISSING_TASK_ID"
                elif result_task_id != expected_task_id:
                    result["task_id"] = result_task_id
                    result["status"] = "INVALID"
                    result["parse_status"] = "TASK_ID_MISMATCH"
                else:
                    result["task_id"] = result_task_id
                    result["parse_status"] = "VALID_JSON"
                    if "task_lifecycle_state" in parsed:
                        lifecycle_field = "task_lifecycle_state"
                    elif "task_local_status" in parsed:
                        lifecycle_field = "task_local_status"
                    elif "lifecycle_status" in parsed:
                        lifecycle_field = "lifecycle_status"
                    else:
                        lifecycle_field = "UNKNOWN"
                    lifecycle = parsed.get(lifecycle_field) if lifecycle_field != "UNKNOWN" else None
                    result["lifecycle_status_source"] = lifecycle_field
                    if isinstance(lifecycle, str) and lifecycle.strip():
                        result["lifecycle_status"] = lifecycle.strip()

        if packet["status"] == "INVALID" or result["status"] == "INVALID":
            identity_status = "INVALID"
        elif packet["status"] == "PRESENT" and result["status"] == "PRESENT":
            identity_status = "MATCH"
        else:
            identity_status = "UNKNOWN"

        reported_ac_passes: list[str] = []
        if result_data is not None and result["status"] == "PRESENT":
            reported_ac_passes = _reported_acceptance_passes(result_data)

        return {
            "task_id": expected_task_id,
            "task_root": str(root),
            "identity_status": identity_status,
            "packet": packet,
            "result": result,
            "reported_acceptance": {
                "status": "REPORTED" if reported_ac_passes else "UNKNOWN",
                "reported_passes": reported_ac_passes,
                "independently_validated": False,
            },
            "unresolved": {
                "FIRST_ATTEMPT_STATUS": "UNKNOWN",
                "FIRST_COMPLETION_RATE": "UNKNOWN",
                "AGENT_RETRY_COUNT": "UNKNOWN",
                "JUDGE_VERDICT": "UNKNOWN",
                "FULL_TASK_ELAPSED_TIME": "UNKNOWN",
            },
            "process_evidence": _process_evidence_summary(
                directory_fd, identity_status, packet_text, result_data,
            ),
        }
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def summarize_execution_history(
    repo_text: str,
    task_id_filter: str | None = None,
    task_evidence_root: str | None = None,
) -> dict[str, Any]:
    repo = canonical_directory(repo_text, "repository")
    if Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve(strict=True) != repo:
        raise ControlError("--repo must name the exact Git worktree root")
    if task_id_filter is not None:
        task_id_filter = _safe_task_id(task_id_filter)
    task_evidence = None
    if task_evidence_root is not None:
        if task_id_filter is None:
            raise ControlError("--task-id is required with --task-evidence-root")
        task_evidence = summarize_task_evidence(task_evidence_root, task_id_filter)
    checkpoint_root = repo / ".fable" / "checkpoints"
    if checkpoint_root.is_symlink():
        raise ControlError("checkpoint root must not be a symlink")
    records: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    referenced_captures: set[Path] = set()
    task_dirs = sorted(path for path in checkpoint_root.iterdir() if path.is_dir() and not path.is_symlink()) if checkpoint_root.is_dir() else []
    for task_dir in task_dirs:
        if task_id_filter is not None and task_dir.name != task_id_filter:
            continue
        execution_dir = task_dir / "executions"
        if not execution_dir.is_dir() or execution_dir.is_symlink():
            continue
        for record_path in sorted(execution_dir.glob("*.json")):
            if record_path.is_symlink() or not record_path.is_file():
                problems.append({"path": str(record_path), "reason": "execution record is not a regular file"})
                continue
            record_bytes = record_path.read_bytes()
            try:
                record = json.loads(record_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                problems.append({"path": str(record_path), "reason": "execution record is malformed"})
                continue
            if not isinstance(record, dict):
                problems.append({"path": str(record_path), "reason": "execution record is not an object"})
                continue
            record_task = record.get("task_id")
            execution_id = record.get("execution_id")
            if record_task != task_dir.name or execution_id != record_path.stem:
                problems.append({"path": str(record_path), "reason": "execution identity does not match its path"})
                continue
            capture_text = record.get("durable_capture_path")
            if not isinstance(capture_text, str) or not Path(capture_text).is_absolute():
                problems.append({"path": str(record_path), "reason": "durable capture reference is missing or not absolute"})
                continue
            capture_path = Path(capture_text)
            try:
                capture_path = canonical_regular_file(str(capture_path), "durable capture")
            except ControlError as exc:
                problems.append({"path": str(record_path), "reason": str(exc)})
                continue
            expected_capture_dir = (task_dir / "captures").resolve(strict=False)
            if not _is_within(capture_path, expected_capture_dir):
                problems.append({"path": str(record_path), "reason": "durable capture escapes its task capture directory"})
                continue
            capture_bytes = capture_path.read_bytes()
            try:
                capture = json.loads(capture_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                problems.append({"path": str(capture_path), "reason": "durable capture is malformed"})
                continue
            if not isinstance(capture, dict):
                problems.append({"path": str(capture_path), "reason": "durable capture is not an object"})
                continue
            if capture.get("exit_status") is None and capture.get("status") is not None:
                exit_status = capture.get("status")
            else:
                exit_status = capture.get("exit_status")
            stdout = capture.get("stdout", "")
            stderr = capture.get("stderr", "")
            if not isinstance(stdout, str) or not isinstance(stderr, str):
                problems.append({"path": str(capture_path), "reason": "capture streams are not text"})
                stdout = stdout if isinstance(stdout, str) else ""
                stderr = stderr if isinstance(stderr, str) else ""
            command_raw = capture.get("command", [])
            command = command_raw if isinstance(command_raw, list) and all(isinstance(item, str) for item in command_raw) else []
            text = stdout + "\n" + stderr
            pytest_command = any(Path(item).name == "pytest" or item == "pytest" for item in command)
            basetemp = _basetemp_argument(command) if pytest_command else None
            outcome = _command_outcome(record.get("status"), exit_status)
            signatures: list[str] = []
            if pytest_command and "LocalDataError: local data path must be outside Git worktrees" in text:
                signatures.append("pytest_temp_path_inside_git_worktree")
            if pytest_command and "HistoricalReplaySourceError: B649 source paths are protected" in text:
                signatures.append("pytest_temp_path_contains_protected_b649_token")
            fail_ids = [line.strip()[len("FAILED "):] for line in text.splitlines() if line.strip().startswith("FAILED ")]
            duration = _duration_seconds(capture.get("started_at"), capture.get("ended_at"))
            record_sha = hashlib.sha256(record_bytes).hexdigest()
            capture_sha = hashlib.sha256(capture_bytes).hexdigest()
            stdout_bytes = stdout.encode("utf-8")
            stderr_bytes = stderr.encode("utf-8")
            command_bytes = json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            referenced_captures.add(capture_path)
            rows = {
                "task_id": task_dir.name,
                "execution_id": execution_id,
                "record_status": record.get("status"),
                "outcome": outcome,
                "exit_status": exit_status,
                "duration_seconds": duration,
                "command_tool": Path(command[0]).name if command else None,
                "command_sha256": hashlib.sha256(command_bytes).hexdigest(),
                "basetemp": basetemp,
                "failure_ids": fail_ids,
                "failure_signatures": signatures,
                "record": {"path": str(record_path), "bytes": len(record_bytes), "sha256": record_sha},
                "capture": {
                    "path": str(capture_path), "bytes": len(capture_bytes), "sha256": capture_sha,
                    "stdout_bytes": len(stdout_bytes), "stderr_bytes": len(stderr_bytes),
                    "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
                    "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
                },
            }
            records.append(rows)

    outcomes = {name: sum(row["outcome"] == name for row in records) for name in ("SUCCESS", "COMMAND_FAILED", "INTERRUPTED", "NONTERMINAL_RECORD", "UNKNOWN")}
    duplicate_groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    duplicate_payload_bytes = 0
    for row in records:
        capture = row["capture"]
        payload_bytes = capture["stdout_bytes"] + capture["stderr_bytes"]
        if not payload_bytes:
            continue
        key = (row["command_sha256"], capture["stdout_sha256"], capture["stderr_sha256"], str(row["exit_status"]))
        group = duplicate_groups.setdefault(key, {
            "count": 0,
            "payload_bytes_each": payload_bytes,
            "command_sha256": key[0],
            "stdout_sha256": key[1],
            "stderr_sha256": key[2],
            "exit_status": row["exit_status"],
            "execution_ids": [],
        })
        group["count"] += 1
        group["execution_ids"].append(row["execution_id"])
        if group["count"] > 1:
            duplicate_payload_bytes += payload_bytes
    repeated_outputs = [group for group in duplicate_groups.values() if group["count"] > 1]

    boundary_rows = [row for row in records if row["failure_signatures"] and row["basetemp"]]
    boundary_sources = []
    for row in boundary_rows:
        decision = {"status": "UNKNOWN", "violations": ["not evaluated"]}
        try:
            decision = {
                "status": "BLOCKED" if _test_scratch_violations(repo, row["basetemp"]) else "PASS",
                "violations": _test_scratch_violations(repo, row["basetemp"]),
            }
        except (OSError, ValueError):
            pass
        if decision["status"] == "BLOCKED":
            boundary_sources.append({
                "task_id": row["task_id"], "execution_id": row["execution_id"],
                "outcome": row["outcome"], "exit_status": row["exit_status"],
                "duration_seconds": row["duration_seconds"], "basetemp": row["basetemp"],
                "failure_signatures": row["failure_signatures"], "validation": decision,
                "record": row["record"], "capture": row["capture"],
            })
    recommendations: list[dict[str, Any]] = []
    if len(boundary_sources) >= 2:
        recommendations.append({
            "id": "preflight-pytest-temporary-path-boundaries",
            "state": "CANDIDATE_REQUIRES_OWNER_REVIEW",
            "evidence_strength": "REPEATED_OBSERVATION",
            "occurrences": len(boundary_sources),
            "problem": "Repeated pytest runs failed after their --basetemp paths crossed Git-worktree or protected-path boundaries.",
            "minimum_improvement": "Run validate-test-scratch before pytest launch or retry; choose a neutral path outside every Git worktree and protected path component when blocked.",
            "verification": "Use the observed --basetemp values as inputs; assert both unsafe values are blocked and a neutral external path passes without launching pytest.",
            "automatically_applied": False,
            "sources": boundary_sources,
        })

    capture_files: list[Path] = []
    for task_dir in task_dirs:
        if task_id_filter is not None and task_dir.name != task_id_filter:
            continue
        capture_dir = task_dir / "captures"
        if capture_dir.is_dir() and not capture_dir.is_symlink():
            capture_files.extend(path for path in capture_dir.glob("*.json") if path.is_file() and not path.is_symlink())
    orphan_captures = []
    for path in capture_files:
        canonical = path.resolve(strict=True)
        if canonical not in referenced_captures:
            raw = path.read_bytes()
            artifact: dict[str, Any] = {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            try:
                capture_only = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                capture_only = None
            if isinstance(capture_only, dict):
                command = capture_only.get("command", [])
                artifact.update({
                    "capture_only": True,
                    "exit_status": capture_only.get("exit_status"),
                    "command_tool": Path(command[0]).name if isinstance(command, list) and command and isinstance(command[0], str) else None,
                    "duration_seconds": _duration_seconds(capture_only.get("started_at"), capture_only.get("ended_at")),
                    "stdout_bytes": len(capture_only.get("stdout", "").encode("utf-8")) if isinstance(capture_only.get("stdout", ""), str) else None,
                    "stderr_bytes": len(capture_only.get("stderr", "").encode("utf-8")) if isinstance(capture_only.get("stderr", ""), str) else None,
                })
            orphan_captures.append(artifact)
    temp_log_root = repo / ".fable" / "temporary"
    temp_logs = [path for path in temp_log_root.rglob("observer*.jsonl") if path.is_file() and not path.is_symlink()] if temp_log_root.is_dir() and not temp_log_root.is_symlink() else []
    summary = {
        "schema": "fable-evidence-feedback-v1",
        "repository": str(repo),
        "task_filter": task_id_filter,
        "inventory": {
            "task_directories": len(task_dirs),
            "execution_records": len(records),
            "record_status_completed": sum(row["record_status"] == "COMPLETED" for row in records),
            "outcomes": outcomes,
            "duration_seconds_recorded": round(sum(row["duration_seconds"] or 0 for row in records), 3),
            "execution_record_bytes": sum(row["record"]["bytes"] for row in records),
            "referenced_capture_count": len(referenced_captures),
            "referenced_capture_bytes": sum({
                row["capture"]["path"]: row["capture"]["bytes"] for row in records
            }.values()),
            "formal_capture_file_count": len(capture_files),
            "formal_capture_file_bytes": sum(path.stat().st_size for path in capture_files),
            "orphan_capture_count": len(orphan_captures),
            "orphan_capture_bytes": sum(item["bytes"] for item in orphan_captures),
            "temporary_observer_log_count": len(temp_logs),
            "temporary_observer_log_bytes": sum(path.stat().st_size for path in temp_logs),
            "exact_duplicate_stdout_stderr_bytes": duplicate_payload_bytes,
            "exact_duplicate_output_groups": repeated_outputs,
        },
        "coverage": {
            "execution_outcomes": "FROM_DURABLE_CAPTURE_EXIT_STATUS",
            "duration": "FROM_CAPTURE_TIMESTAMPS_WHEN_PRESENT",
            "retry_relationships": "NOT_RECORDED_BY_EXECUTION_SCHEMA",
            "judge_decisions": "NOT_RECORDED_BY_CHECKPOINT_SCHEMA",
            "agent_attempt_counts": "NOT_RECORDED_BY_CHECKPOINT_SCHEMA",
            "task_level_completion": "NOT_RECORDED_BY_CHECKPOINT_SCHEMA",
            "historical_worktree_lifecycle": "NOT_RECORDED_BY_CHECKPOINT_SCHEMA",
        },
        "problems": problems,
        "orphan_captures": orphan_captures,
        "recommendations": recommendations,
        "executions": records,
    }
    if task_evidence is not None:
        summary["task_evidence"] = task_evidence
    return summary


def _parse_worktree_list(repo: Path) -> set[Path]:
    output = git(repo, "worktree", "list", "--porcelain").stdout
    paths: set[Path] = set()
    for block in output.strip().split("\n\n"):
        first = next((line for line in block.splitlines() if line.startswith("worktree ")), None)
        if first:
            paths.add(Path(first[len("worktree "):]).resolve(strict=False))
    return paths


def _branch_ref_exists(repo: Path, branch: str) -> bool:
    ref = f"refs/heads/{branch}"
    result = git(repo, "show-ref", "--verify", "--quiet", ref, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.strip() or result.stdout.strip()
    raise ControlError(f"cannot verify planned branch ref {ref}: {detail}")


def _worktree_creation_baseline(
    repo: Path,
    worktree: Path,
    base: str,
    base_commit: str,
    branch: str,
) -> dict[str, Any]:
    if worktree.exists() or worktree.is_symlink():
        raise ControlError("planned worktree path already exists; refusing to adopt it")
    branch_ref = f"refs/heads/{branch}"
    if _branch_ref_exists(repo, branch):
        raise ControlError(f"planned branch ref already exists; refusing to overwrite: {branch_ref}")
    base_tree = git(repo, "rev-parse", "--verify", f"{base_commit}^{{tree}}").stdout.strip()
    common_dir = canonical_directory(
        git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip(),
        "Git common directory",
    )
    return {
        "worktree_access_scope": "TARGET_ONLY",
        "other_worktrees": "NOT_INSPECTED",
        "target_path": str(worktree),
        "target_exists": False,
        "target_registration": "NOT_ENUMERATED",
        "branch_ref": branch_ref,
        "branch_ref_exists": False,
        "base_ref": base,
        "base_commit": base_commit,
        "base_tree": base_tree,
        "repository_git_common_dir": str(common_dir),
    }


def _target_worktree_identity(repo: Path, worktree: Path) -> dict[str, Any]:
    target = canonical_directory(str(worktree), "target worktree")
    top = canonical_directory(
        git(target, "rev-parse", "--show-toplevel").stdout.strip(),
        "target worktree root",
    )
    repo_common = canonical_directory(
        git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip(),
        "repository Git common directory",
    )
    target_common = canonical_directory(
        git(target, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip(),
        "target Git common directory",
    )
    if top != target or target_common != repo_common:
        raise ControlError("target path is not a worktree of the specified repository")
    head = git(target, "rev-parse", "HEAD").stdout.strip()
    tree = git(target, "rev-parse", "HEAD^{tree}").stdout.strip()
    branch_result = git(target, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch_result.returncode not in {0, 1}:
        detail = branch_result.stderr.strip() or branch_result.stdout.strip()
        raise ControlError(f"cannot verify target worktree branch: {detail}")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "DETACHED"
    status = git(target, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching").stdout
    return {
        "path": str(target),
        "git_worktree_root": str(top),
        "git_common_dir": str(target_common),
        "head": head,
        "tree": tree,
        "branch": branch,
        "clean": status == "",
        "status_porcelain": status,
    }


def _lifecycle_directory(repo: Path, *, create: bool) -> Path:
    common_value = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    common = canonical_directory(common_value, "Git common directory")
    lifecycle = common / "fable-temporary-worktrees"
    if create:
        lifecycle = _mkdir_no_symlink(common, lifecycle.name)
    else:
        try:
            info = lifecycle.lstat()
        except FileNotFoundError as exc:
            raise ControlError("no temporary worktree preauthorization exists") from exc
        if not stat.S_ISDIR(info.st_mode) or lifecycle.resolve(strict=True) != lifecycle:
            raise ControlError("temporary worktree lifecycle directory is unsafe")
    return lifecycle


def _lifecycle_marker_path(repo: Path, worktree: Path, *, create: bool) -> Path:
    digest = hashlib.sha256(str(worktree).encode("utf-8")).hexdigest()
    return _lifecycle_directory(repo, create=create) / f"{digest}.json"


def _load_lifecycle_marker(marker: Path) -> tuple[dict[str, Any], bytes]:
    try:
        marker_file = canonical_regular_file(str(marker), "temporary worktree lifecycle record")
    except ControlError as exc:
        if not marker.exists() and not marker.is_symlink():
            raise ControlError("worktree has no preauthorization record") from exc
        raise
    marker_bytes = marker_file.read_bytes()
    try:
        data = json.loads(marker_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlError("temporary worktree lifecycle record is malformed") from exc
    if not isinstance(data, dict):
        raise ControlError("temporary worktree lifecycle record is not an object")
    return data, marker_bytes


def _replace_lifecycle_marker(marker: Path, old_bytes: bytes, data: dict[str, Any]) -> bytes:
    replacement = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}.{hashlib.sha256(old_bytes).hexdigest()[:12]}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(temporary, flags, 0o600)
    except OSError as exc:
        raise ControlError(f"cannot stage lifecycle record update: {exc}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        if marker.read_bytes() != old_bytes:
            temporary.unlink()
            raise ControlError("temporary worktree lifecycle record changed before update")
        os.replace(temporary, marker)
        directory_fd = os.open(marker.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return replacement


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _restore_claimed_lifecycle_marker(claim: Path, marker: Path) -> bool:
    """Restore a claimed marker only if the original name is still vacant."""
    try:
        os.link(claim, marker, follow_symlinks=False)
    except OSError:
        return False
    try:
        claim.unlink()
        claim.parent.rmdir()
        _fsync_directory(marker.parent)
    except OSError:
        # Both names may remain, but no authorization or evidence is discarded.
        return False
    return True


def _claim_close_authorization(marker: Path, expected_bytes: bytes) -> Path:
    """Atomically consume the active preauthorization before worktree removal.

    The rename is the close transaction's authorization point: a concurrent
    update that wins before it is observed and blocks removal; a second close
    cannot claim the now-absent marker.
    """
    claim_dir = marker.parent / f".{marker.name}.closing.{os.getpid()}.{secrets.token_hex(12)}"
    try:
        os.mkdir(claim_dir, 0o700)
    except OSError as exc:
        raise ControlError(f"cannot reserve a private authorization claim: {exc}") from exc
    claim = claim_dir / "authorization.json"
    try:
        os.rename(marker, claim)
        _fsync_directory(marker.parent)
        _fsync_directory(claim_dir)
        descriptor = os.open(
            claim,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ControlError("claimed temporary worktree authorization is not a regular file")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            claimed_bytes = b"".join(chunks)
        finally:
            os.close(descriptor)
        if claimed_bytes != expected_bytes:
            raise ControlError("temporary worktree preauthorization changed before the removal claim")
        return claim
    except Exception as exc:
        if claim.exists() or claim.is_symlink():
            restored = _restore_claimed_lifecycle_marker(claim, marker)
            if not restored:
                raise ControlError(
                    f"authorization claim failed; preserving worktree and claimed record at {claim}"
                ) from exc
        else:
            try:
                claim_dir.rmdir()
            except OSError:
                pass
        if isinstance(exc, ControlError):
            raise
        raise ControlError(f"could not claim the active worktree authorization: {exc}") from exc


def _finish_close_authorization_claim(claim: Path, marker: Path) -> None:
    try:
        claim.unlink()
        claim.parent.rmdir()
        _fsync_directory(marker.parent)
    except OSError as exc:
        raise ControlError(
            f"worktree was removed, but its claimed authorization record remains at {claim}: {exc}"
        ) from exc


def prepare_temporary_worktree(
    repo_text: str,
    worktree_text: str,
    base: str,
    branch: str,
    task_id_text: str,
    *,
    auto_close_authorized: bool = False,
    close_policy: str = "AUTO_CLOSE_AFTER_MERGE",
    merge_target_ref: str | None = None,
    persistent_evidence_root: str | None = None,
    allowed_rebuildable_caches: list[str] | None = None,
    fable_ownership: str = "fable",
) -> dict[str, Any]:
    effective_auto_close = (auto_close_authorized or close_policy == "AUTO_CLOSE_AFTER_MERGE")
    if not effective_auto_close:
        raise ControlError("temporary worktree preauthorization requires prior Owner auto-close authority")
    task_id = _safe_task_id(task_id_text)
    repo = canonical_directory(repo_text, "repository")
    if Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve(strict=True) != repo:
        raise ControlError("--repo must name the exact Git worktree root")

    planned = Path(worktree_text)
    if not planned.is_absolute() or "\n" in str(planned) or "\r" in str(planned):
        raise ControlError("planned worktree path must be absolute and contain no newlines")
    parent = canonical_directory(str(planned.parent), "planned worktree parent")
    worktree = parent / planned.name
    if worktree.exists() or worktree.is_symlink():
        raise ControlError("planned worktree path already exists; refusing to adopt it")
    base_commit = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").stdout.strip()
    if not branch or branch.startswith("-") or git(repo, "check-ref-format", "--branch", branch, check=False).returncode != 0:
        raise ControlError("planned branch is not a valid Git branch name")

    space = check_space(str(parent), "research-worktree", "before")

    before = _worktree_creation_baseline(repo, worktree, base, base_commit, branch)
    marker = _lifecycle_marker_path(repo, worktree, create=True)
    if marker.exists() or marker.is_symlink():
        raise ControlError("a lifecycle record already exists for the planned path")
    payload = {
        "format_version": 1,
        "task_id": task_id,
        "repository": str(repo),
        "worktree": str(worktree),
        "worktree_identity": str(worktree),
        "worktree_access_scope": "TARGET_ONLY",
        "other_worktrees": "NOT_INSPECTED",
        "before_create": before,
        "base_ref": base,
        "base_commit_at_authorization": base_commit,
        "branch": branch,
        "fable_ownership": fable_ownership,
        "close_policy": close_policy,
        "merge_target_ref": merge_target_ref or base,
        "persistent_evidence_root": str(Path(persistent_evidence_root).resolve(strict=False)) if persistent_evidence_root else None,
        "allowed_rebuildable_caches": allowed_rebuildable_caches or [],
        "auto_close_authorized": True,
        "state": "PREAUTHORIZED",
    }
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(marker.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return {
        "status": "PREAUTHORIZED",
        "task_id": task_id,
        "repository": str(repo),
        "worktree": str(worktree),
        "worktree_identity": str(worktree),
        "before": before,
        "worktree_access_scope": "TARGET_ONLY",
        "other_worktrees": "NOT_INSPECTED",
        "base_ref": base,
        "base_commit_at_authorization": base_commit,
        "branch": branch,
        "fable_ownership": fable_ownership,
        "close_policy": close_policy,
        "merge_target_ref": merge_target_ref or base,
        "persistent_evidence_root": str(Path(persistent_evidence_root).resolve(strict=False)) if persistent_evidence_root else None,
        "available_gib": space["available_gib"],
        "warning": space["message"] if space["status"] == "WARN" else None,
    }


def create_research_worktree(
    repo_text: str,
    worktree_text: str,
    base: str,
    branch: str,
    task_id_text: str,
    lifecycle: str,
    *,
    auto_close_authorized: bool = False,
    close_policy: str | None = None,
    merge_target_ref: str | None = None,
    persistent_evidence_root: str | None = None,
    allowed_rebuildable_caches: list[str] | None = None,
    fable_ownership: str = "fable",
) -> dict[str, Any]:
    if lifecycle not in {"persistent-research", "temporary-task"}:
        raise ControlError("lifecycle must be persistent-research or temporary-task")
    if close_policy is None:
        close_policy = "AUTO_CLOSE_AFTER_MERGE" if (lifecycle == "temporary-task" and auto_close_authorized) else ("AUTO_CLOSE_AFTER_MERGE" if lifecycle == "temporary-task" else "PRESERVE")
    effective_auto_close = (auto_close_authorized or close_policy == "AUTO_CLOSE_AFTER_MERGE")
    if effective_auto_close and lifecycle != "temporary-task":
        raise ControlError("auto-close authorization applies only to a temporary-task worktree")
    task_id = _safe_task_id(task_id_text)
    repo = canonical_directory(repo_text, "repository")
    if Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve(strict=True) != repo:
        raise ControlError("--repo must name the exact Git worktree root")
    planned = Path(worktree_text)
    if not planned.is_absolute() or "\n" in str(planned) or "\r" in str(planned):
        raise ControlError("planned worktree path must be absolute and contain no newlines")
    parent = canonical_directory(str(planned.parent), "planned worktree parent")
    worktree = parent / planned.name
    if worktree.exists() or worktree.is_symlink():
        raise ControlError("planned worktree path already exists; refusing to adopt it")
    base_commit = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").stdout.strip()
    if (
        not branch
        or branch.startswith("-")
        or git(repo, "check-ref-format", "--branch", branch, check=False).returncode != 0
    ):
        raise ControlError("planned branch is not a valid Git branch name")
    space = check_space(str(parent), "research-worktree", "before")
    if space["message"]:
        print(json.dumps(space, sort_keys=True), file=sys.stderr)

    before = None
    if lifecycle == "temporary-task" and effective_auto_close:
        preauthorization = prepare_temporary_worktree(
            str(repo),
            str(worktree),
            base,
            branch,
            task_id,
            auto_close_authorized=True,
            close_policy=close_policy,
            merge_target_ref=merge_target_ref,
            persistent_evidence_root=persistent_evidence_root,
            allowed_rebuildable_caches=allowed_rebuildable_caches,
            fable_ownership=fable_ownership,
        )
        before = preauthorization["before"]
    else:
        before = _worktree_creation_baseline(repo, worktree, base, base_commit, branch)

    current_baseline = _worktree_creation_baseline(repo, worktree, base, base_commit, branch)
    if current_baseline != before:
        raise ControlError("target path, branch ref, or base identity changed before worktree creation")

    created = git(repo, "worktree", "add", "-b", branch, str(worktree), base_commit, check=False)
    if created.returncode != 0:
        detail = created.stderr.strip() or created.stdout.strip()
        raise ControlError(f"git worktree add failed; no cleanup or retry was attempted: {detail}")

    lifecycle_record = None
    creation_after = None
    if lifecycle == "temporary-task" and effective_auto_close:
        lifecycle_record = bind_temporary_worktree(
            str(repo),
            str(worktree),
            base,
            branch,
            task_id,
        )
        creation_after = lifecycle_record["after"]
    else:
        target_identity = _target_worktree_identity(repo, worktree)
        if target_identity["branch"] != branch or target_identity["head"] != base_commit:
            raise ControlError("created worktree identity differs from the requested path, branch, or base; preserving it")
        if not target_identity["clean"]:
            raise ControlError("created worktree is not clean at the creation receipt; preserving it")
        creation_after = {
            "result": "CREATED",
            "retention_status": "PRESENT_CLEAN",
            "target_identity": target_identity,
            "worktree_access_scope": "TARGET_ONLY",
            "other_worktrees": "NOT_INSPECTED",
        }
    space_after = check_space(str(parent), "research-worktree", "after")
    return {
        "status": "CREATED",
        "lifecycle": lifecycle,
        "auto_close_authorized": effective_auto_close,
        "close_policy": close_policy if lifecycle == "temporary-task" else "PRESERVE",
        "repository": str(repo),
        "worktree": str(worktree),
        "branch": branch,
        "base": base,
        "base_commit_at_creation": base_commit,
        "before": before,
        "creation_receipt": creation_after,
        "available_gib_before": space["available_gib"],
        "warning_before": space["message"] if space["status"] == "WARN" else None,
        "available_gib_after": space_after["available_gib"],
        "status_after": space_after["status"],
        "warning_after": space_after["message"] if space_after["status"] != "PASS" else None,
        "lifecycle_record": lifecycle_record,
    }


def bind_temporary_worktree(
    repo_text: str,
    worktree_text: str,
    base: str,
    branch: str,
    task_id_text: str,
) -> dict[str, Any]:
    task_id = _safe_task_id(task_id_text)
    repo = canonical_directory(repo_text, "repository")
    if Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve(strict=True) != repo:
        raise ControlError("--repo must name the exact Git worktree root")
    worktree = canonical_directory(worktree_text, "temporary worktree")
    target_identity = _target_worktree_identity(repo, worktree)

    marker = _lifecycle_marker_path(repo, worktree, create=False)
    authorization, old_bytes = _load_lifecycle_marker(marker)
    is_authorized = (
        authorization.get("auto_close_authorized") is True
        or authorization.get("close_policy") == "AUTO_CLOSE_AFTER_MERGE"
    )
    if (
        authorization.get("format_version") != 1
        or authorization.get("state") != "PREAUTHORIZED"
        or not is_authorized
        or authorization.get("task_id") != task_id
        or authorization.get("repository") != str(repo)
        or authorization.get("worktree") != str(worktree)
        or authorization.get("base_ref") != base
        or authorization.get("branch") != branch
        or authorization.get("fable_ownership") != "fable"
    ):
        raise ControlError("new worktree does not match its exact preauthorization record")

    head = target_identity["head"]
    if target_identity["branch"] != branch:
        raise ControlError("new worktree branch differs from its preauthorized branch")
    if head != authorization.get("base_commit_at_authorization"):
        raise ControlError("new worktree HEAD differs from the exact preauthorized base commit")
    if not target_identity["clean"]:
        raise ControlError("new worktree is not clean at lifecycle binding")

    authorization["state"] = "ACTIVE"
    authorization["initial_head"] = head
    authorization["bound_base_commit"] = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").stdout.strip()
    authorization["after_create"] = {
        "result": "CREATED",
        "retention_status": "PRESENT_CLEAN",
        "target_identity": target_identity,
        "worktree_access_scope": "TARGET_ONLY",
        "other_worktrees": "NOT_INSPECTED",
    }
    updated_bytes = _replace_lifecycle_marker(marker, old_bytes, authorization)
    return {
        "status": "ACTIVE",
        "task_id": task_id,
        "worktree": str(worktree),
        "branch": branch,
        "initial_head": head,
        "base_ref": base,
        "before": authorization.get("before_create"),
        "after": authorization["after_create"],
        "worktree_access_scope": "TARGET_ONLY",
        "other_worktrees": "NOT_INSPECTED",
        "lifecycle_record_sha256": hashlib.sha256(updated_bytes).hexdigest(),
    }


def _verify_evidence_manifest(value: str, worktree: Path) -> dict[str, Any]:
    manifest_path = canonical_regular_file(value, "evidence manifest")
    if _is_within(manifest_path, worktree):
        raise ControlError("evidence manifest must be saved outside the temporary worktree")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError("evidence manifest is unreadable or malformed") from exc
    rows = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ControlError("evidence manifest must list at least one saved result")
    verified: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ControlError("evidence manifest rows must contain only absolute path and sha256")
        path = canonical_regular_file(str(row["path"]), "evidence file")
        if _is_within(path, worktree):
            raise ControlError("formal evidence must be saved outside the temporary worktree")
        if path in seen or not isinstance(row["sha256"], str) or SHA_RE.fullmatch(row["sha256"]) is None:
            raise ControlError("evidence manifest contains a duplicate path or malformed SHA256")
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed != row["sha256"]:
            raise ControlError(f"evidence SHA256 mismatch: {path}")
        seen.add(path)
        verified.append({"path": str(path), "sha256": observed, "bytes": path.stat().st_size})
    return {"manifest": str(manifest_path), "files": verified}


def _get_ancestor_pids() -> set[int]:
    pids = {os.getpid()}
    curr = os.getpid()
    for _ in range(30):
        try:
            res = subprocess.run(["ps", "-o", "ppid=", "-p", str(curr)], capture_output=True, text=True, check=False)
            if res.returncode != 0:
                break
            ppid_text = res.stdout.strip()
            if not ppid_text:
                break
            ppid = int(ppid_text)
            if ppid in pids or ppid <= 1:
                break
            pids.add(ppid)
            curr = ppid
        except Exception:
            break
    return pids


def _parse_lsof_output(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line:
            continue
        field = line[0]
        val = line[1:]
        if field == "p":
            if current is not None:
                entries.append(current)
            try:
                current = {"pid": int(val), "command": "", "files": []}
            except ValueError:
                current = None
        elif current is not None:
            if field == "c":
                current["command"] = val
            elif field in ("f", "n"):
                current["files"].append((field, val))
    if current is not None:
        entries.append(current)
    return entries


def _check_no_open_files(worktree: Path) -> None:
    lsof = shutil.which("lsof")
    if lsof is None:
        raise ControlError("lsof is unavailable; cannot establish that the worktree has no open users")

    # Stop fsmonitor daemon on this worktree if one was running
    git(worktree, "fsmonitor--daemon", "stop", check=False)

    try:
        result = subprocess.run(
            [lsof, "-nP", "-Fpcfn", "+D", str(worktree)],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError(f"worktree open-file inspection is unresolved: {exc}") from exc
    if result.stderr.strip():
        raise ControlError(f"worktree open-file inspection is unresolved: {result.stderr.strip()}")
    if result.returncode not in (0, 1):
        raise ControlError(f"worktree open-file inspection exited {result.returncode}")
    if not result.stdout.strip():
        return

    ancestor_pids = _get_ancestor_pids()
    entries = _parse_lsof_output(result.stdout)
    for entry in entries:
        pid = entry["pid"]
        cmd = entry["command"]
        # Skip lsof inspection tool itself or current python process
        if cmd == "lsof" or pid == os.getpid():
            continue
        # Check fsmonitor
        if "fsmonitor" in cmd.lower():
            git(worktree, "fsmonitor--daemon", "stop", check=False)
            time.sleep(0.5)
            if _pid_alive(pid):
                raise ControlError(f"fsmonitor daemon (pid {pid}) is running on the worktree; cannot establish safe closure")
            continue
        # Check if process is in helper's ancestor process chain (自身 shell)
        if pid in ancestor_pids:
            for f_type, f_val in entry["files"]:
                if f_type == "f" and f_val == "cwd":
                    raise ControlError("own shell working directory is inside the worktree; run close from outside")
            # If not cwd inside worktree, recognized as own shell execution context
            continue
        # Any other process is an active external user / process
        raise ControlError(f"active external process ({cmd}, pid {pid}) still has the worktree open")


def _pid_alive(pid: int) -> bool | None:
    if type(pid) is not int or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return None
    return True


def _path_mentions_worktree(value: object, worktree: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if str(worktree) in value:
        return True
    candidate = Path(value)
    if not candidate.is_absolute():
        return False
    resolved = candidate.resolve(strict=False)
    return resolved == worktree or _is_within(resolved, worktree)


def _check_execution_claims(repo: Path, worktree: Path) -> None:
    claims = repo / ".task-data" / "execution-claims"
    if not claims.exists() and not claims.is_symlink():
        return
    claims = canonical_directory(str(claims), "execution claim directory")
    try:
        claim_files = sorted(claims.iterdir())
    except OSError as exc:
        raise ControlError(f"execution claims cannot be enumerated: {exc}") from exc
    for path in claim_files:
        if path.name == ".coordination.lock":
            continue
        claim_path = canonical_regular_file(str(path), "execution claim")
        if claim_path.stat().st_size > 1024 * 1024:
            raise ControlError("execution claim exceeds the supported size")
        try:
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlError(f"execution claim is malformed: {claim_path.name}") from exc
        if not isinstance(claim, dict):
            raise ControlError(f"execution claim is not an object: {claim_path.name}")
        required = {
            "schema_version", "task_key", "owner_id", "owner_pid", "child_pid",
            "hostname", "started_at_utc", "heartbeat_at_utc", "cwd", "command", "claim_root",
        }
        if set(claim) != required or claim.get("schema_version") != 1:
            raise ControlError(f"execution claim schema is unresolved: {claim_path.name}")
        overlaps = _path_mentions_worktree(claim.get("cwd"), worktree)
        command = claim.get("command")
        if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
            raise ControlError(f"execution claim command is malformed: {claim_path.name}")
        overlaps = overlaps or any(_path_mentions_worktree(item, worktree) for item in command)
        if not overlaps:
            continue
        for key in ("owner_pid", "child_pid"):
            pid = claim.get(key)
            if pid is None and key == "child_pid":
                continue
            live = _pid_alive(pid)
            if live is None:
                raise ControlError(f"execution claim owner liveness is unresolved: {claim_path.name}")
            if live:
                raise ControlError(f"an active execution claim overlaps the worktree: {claim_path.name}")


def _check_fable_executions(repo: Path, worktree: Path, task_id: str) -> None:
    executions = repo / ".fable" / "checkpoints" / task_id / "executions"
    if not executions.exists() and not executions.is_symlink():
        return
    executions = canonical_directory(str(executions), "Fable execution directory")
    for path in sorted(executions.iterdir()):
        if path.suffix != ".json":
            raise ControlError(f"unrecognized Fable execution record: {path.name}")
        record_path = canonical_regular_file(str(path), "Fable execution record")
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlError(f"Fable execution record is malformed: {path.name}") from exc
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != 1
            or record.get("task_id") != task_id
            or record.get("execution_id") != path.stem
        ):
            raise ControlError(f"Fable execution record identity is unresolved: {path.name}")
        if record.get("status") != "COMPLETED":
            raise ControlError(f"Fable execution is not terminally completed: {path.name}")
        capture_value = record.get("durable_capture_path")
        if not isinstance(capture_value, str) or not Path(capture_value).is_absolute():
            raise ControlError(f"Fable execution has no durable capture: {path.name}")
        capture_path = canonical_regular_file(capture_value, "Fable durable capture")
        try:
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ControlError(f"Fable durable capture is malformed: {path.name}") from exc
        if (
            not isinstance(capture, dict)
            or capture.get("schema_version") != 1
            or capture.get("task_id") != task_id
            or capture.get("execution_id") != path.stem
            or not isinstance(capture.get("command"), list)
            or capture.get("exit_status") is None
            or not capture.get("started_at")
            or not capture.get("ended_at")
        ):
            raise ControlError(f"Fable durable capture identity or terminal evidence is incomplete: {path.name}")
        identity = capture.get("execution_identity")
        if not isinstance(identity, dict):
            raise ControlError(f"Fable durable capture has no execution identity: {path.name}")
        bound_worktree = identity.get("worktree_path")
        if bound_worktree and Path(str(bound_worktree)).resolve(strict=False) != worktree:
            raise ControlError(f"Fable execution belongs to a different worktree: {path.name}")


def _assert_scheduler_unbound(worktree: Path) -> None:
    launchctl = shutil.which("launchctl")
    if launchctl is None:
        raise ControlError("launchctl is unavailable; recurring scheduler ownership is unresolved")
    for domain in (f"gui/{os.getuid()}", f"user/{os.getuid()}", "system"):
        try:
            result = subprocess.run(
                [launchctl, "print", domain],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ControlError(f"launchd ownership inspection is unresolved for {domain}: {exc}") from exc
        if result.returncode != 0 or result.stderr.strip():
            raise ControlError(f"launchd ownership inspection failed for {domain}")
        if str(worktree) in result.stdout:
            raise ControlError(f"a loaded launchd job is bound to the worktree: {domain}")

    crontab = shutil.which("crontab")
    if crontab is None:
        raise ControlError("crontab is unavailable; recurring scheduler ownership is unresolved")
    try:
        result = subprocess.run(
            [crontab, "-l"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError(f"cron ownership inspection is unresolved: {exc}") from exc
    no_crontab = result.returncode == 1 and "no crontab for " in result.stderr.lower() and not result.stdout
    if result.returncode != 0 and not no_crontab:
        raise ControlError("cron ownership inspection failed")
    if str(worktree) in result.stdout:
        raise ControlError("a cron entry is bound to the worktree")


def _verify_formal_merge(
    repo: Path,
    worktree: Path,
    head: str,
    base: str,
    base_commit: str,
    branch_name: str,
    task_id: str,
) -> dict[str, Any]:
    ancestor = git(repo, "merge-base", "--is-ancestor", head, base_commit, check=False)
    if ancestor.returncode == 0:
        return {"merged": True, "type": "NORMAL_MERGE", "target_commit": base_commit}

    merge_base_res = git(repo, "merge-base", head, base_commit, check=False)
    if merge_base_res.returncode != 0 or not merge_base_res.stdout.strip():
        raise ControlError("worktree HEAD is not merged into the durable base; preserving the branch and worktree")
    merge_base = merge_base_res.stdout.strip()

    candidate_commits = [
        c.strip() for c in git(repo, "rev-list", f"{merge_base}..{base_commit}").stdout.splitlines() if c.strip()
    ]
    if not candidate_commits:
        raise ControlError("worktree HEAD is not merged into the durable base; preserving the branch and worktree")

    head_tree = git(repo, "rev-parse", f"{head}^{{tree}}").stdout.strip()
    branch_diff = git(repo, "diff", f"{merge_base}..{head}").stdout

    branch_patch_id = None
    if branch_diff.strip():
        p1 = subprocess.Popen(["git", "-C", str(repo), "-c", "core.fsmonitor=false", "diff", f"{merge_base}..{head}"], stdout=subprocess.PIPE)
        pid1_out, _ = subprocess.Popen(["git", "patch-id", "--stable"], stdin=p1.stdout, stdout=subprocess.PIPE, text=True).communicate()
        p1.wait()
        branch_patch_id = pid1_out.strip().split()[0] if pid1_out.strip() else None

    branch_files = [f.strip() for f in git(repo, "diff", "--name-only", f"{merge_base}..{head}").stdout.splitlines() if f.strip()]

    for commit in candidate_commits:
        c_tree = git(repo, "rev-parse", f"{commit}^{{tree}}").stdout.strip()
        if c_tree == head_tree:
            return {"merged": True, "type": "SQUASH_MERGE", "target_commit": commit, "proof": "IDENTICAL_TREE"}

        parent_res = git(repo, "rev-parse", f"{commit}^1", check=False)
        if parent_res.returncode == 0:
            parent = parent_res.stdout.strip()
            c_diff = git(repo, "diff", f"{parent}..{commit}").stdout
            if branch_diff and c_diff and branch_diff == c_diff:
                return {"merged": True, "type": "SQUASH_MERGE", "target_commit": commit, "proof": "IDENTICAL_DIFF"}

            if branch_patch_id:
                p2 = subprocess.Popen(["git", "-C", str(repo), "-c", "core.fsmonitor=false", "diff", f"{parent}..{commit}"], stdout=subprocess.PIPE)
                pid2_out, _ = subprocess.Popen(["git", "patch-id", "--stable"], stdin=p2.stdout, stdout=subprocess.PIPE, text=True).communicate()
                p2.wait()
                c_patch_id = pid2_out.strip().split()[0] if pid2_out.strip() else None
                if c_patch_id and branch_patch_id == c_patch_id:
                    return {"merged": True, "type": "SQUASH_MERGE", "target_commit": commit, "proof": "IDENTICAL_PATCH_ID"}

        commit_msg = git(repo, "log", "-1", "--format=%B", commit).stdout
        has_provenance = (
            head in commit_msg
            or (branch_name and branch_name != "DETACHED" and branch_name in commit_msg)
            or (task_id and task_id in commit_msg)
        )
        if has_provenance and branch_files:
            blobs_match = True
            for bf in branch_files:
                head_blob = git(repo, "rev-parse", "--verify", f"{head}:{bf}", check=False)
                c_blob = git(repo, "rev-parse", "--verify", f"{commit}:{bf}", check=False)
                if head_blob.returncode != 0 or c_blob.returncode != 0 or head_blob.stdout.strip() != c_blob.stdout.strip():
                    blobs_match = False
                    break
            if blobs_match:
                return {"merged": True, "type": "SQUASH_MERGE", "target_commit": commit, "proof": "PROVENANCE_AND_BLOBS"}

    raise ControlError("worktree HEAD is not merged into the durable base; preserving the branch and worktree")


def _worktree_snapshot(
    repo: Path,
    worktree: Path,
    base: str,
    *,
    allowed_rebuildable_caches: list[str] | None = None,
    task_id: str | None = None,
) -> tuple[str, str, str, str, dict[str, Any]]:
    wt_list = git(repo, "worktree", "list", "--porcelain").stdout
    for block in wt_list.strip().split("\n\n"):
        lines = [line.strip() for line in block.splitlines()]
        first = next((line for line in lines if line.startswith("worktree ")), None)
        if first and Path(first[len("worktree "):]).resolve(strict=False) == worktree:
            if any(line.startswith("locked") for line in lines):
                raise ControlError("worktree is locked; preserving the worktree")

    head = git(worktree, "rev-parse", "HEAD").stdout.strip()
    branch = git(worktree, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    branch_name = branch.stdout.strip() if branch.returncode == 0 else "DETACHED"
    status = git(worktree, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching").stdout
    if status:
        status_lines = [l for l in status.splitlines() if l.strip()]
        dirty = False
        untracked_regular: list[str] = []
        caches_to_clean: list[str] = []
        allowed = set(allowed_rebuildable_caches or [])
        for line in status_lines:
            code = line[:2]
            path_str = line[3:].strip()
            if any(c in "MADRCU" for c in code):
                dirty = True
                break
            top_comp = Path(path_str).parts[0] if Path(path_str).parts else path_str
            if path_str in allowed or top_comp in allowed:
                caches_to_clean.append(top_comp)
            else:
                untracked_regular.append(path_str)
        if dirty:
            raise ControlError("worktree contains modified files; preserving the worktree")
        if untracked_regular:
            raise ControlError(f"worktree contains ordinary untracked or ignored files: {', '.join(untracked_regular)}; preserving the worktree")
        for cache_name in set(caches_to_clean):
            cache_path = worktree / cache_name
            if cache_path.is_dir() and not cache_path.is_symlink():
                shutil.rmtree(cache_path)
            elif cache_path.is_file() and not cache_path.is_symlink():
                cache_path.unlink()
        status_after = git(worktree, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching").stdout
        if status_after:
            raise ControlError("worktree contains modified, untracked, or ignored files after cache cleanup")
        status = status_after

    base_commit = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").stdout.strip()
    merge_info = _verify_formal_merge(repo, worktree, head, base, base_commit, branch_name, task_id or "")
    if branch_name != "DETACHED":
        base_ref = git(repo, "rev-parse", "--symbolic-full-name", base, check=False)
        if base_ref.returncode == 0 and branch_name == base_ref.stdout.strip().removeprefix("refs/heads/"):
            raise ControlError("refusing to remove a worktree checked out on the durable base branch")
    return head, branch_name, status, base_commit, merge_info


def close_temporary_worktree(
    repo_text: str,
    worktree_text: str,
    base: str,
    evidence_manifest: str,
    task_id_text: str,
    *,
    auto_close_authorized: bool | None = None,
    task_complete: bool | None = None,
    agents_quiescent: bool | None = None,
    runtime_quiescent: bool | None = None,
    scheduler_quiescent: bool | None = None,
) -> dict[str, Any]:
    repo = canonical_directory(repo_text, "repository")
    if Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve(strict=True) != repo:
        raise ControlError("--repo must name the exact Git worktree root")
    task_id = _safe_task_id(task_id_text)
    target = canonical_directory(worktree_text, "temporary worktree")
    if target == repo:
        raise ControlError("refusing to remove the canonical checkout")
    if _is_within(Path.cwd().resolve(), target):
        raise ControlError("run close from outside the target worktree")
    if target not in _parse_worktree_list(repo):
        raise ControlError("target is not registered as a worktree of this repository")

    marker = _lifecycle_marker_path(repo, target, create=False)
    authorization, authorization_bytes = _load_lifecycle_marker(marker)
    is_preauthorized = (
        authorization.get("auto_close_authorized") is True
        or authorization.get("close_policy") == "AUTO_CLOSE_AFTER_MERGE"
    )
    if (
        authorization.get("format_version") != 1
        or authorization.get("state") != "ACTIVE"
        or not is_preauthorized
        or authorization.get("task_id") != task_id
        or authorization.get("repository") != str(repo)
        or authorization.get("worktree") != str(target)
        or authorization.get("base_ref") != base
        or authorization.get("fable_ownership") != "fable"
    ):
        raise ControlError("worktree has no active preauthorization for this task and exact path")

    if auto_close_authorized is False:
        raise ControlError("automatic close denied; auto_close_authorized is False")

    evidence = _verify_evidence_manifest(evidence_manifest, target)
    evidence_root = authorization.get("persistent_evidence_root")
    if evidence_root:
        root_path = Path(evidence_root).resolve(strict=False)
        if not _is_within(Path(evidence["manifest"]), root_path) and Path(evidence["manifest"]) != root_path:
            raise ControlError("evidence manifest is outside the preauthorized persistent evidence root")

    gates = {
        "task_complete": task_complete,
        "agents_quiescent": agents_quiescent,
        "runtime_quiescent": runtime_quiescent,
        "scheduler_quiescent": scheduler_quiescent,
    }
    explicit_denials = [name for name, passed in gates.items() if passed is False]
    if explicit_denials:
        raise ControlError("automatic close denied; unresolved gates: " + ", ".join(explicit_denials))

    allowed_caches = authorization.get("allowed_rebuildable_caches") or []
    before = _worktree_snapshot(repo, target, base, allowed_rebuildable_caches=allowed_caches, task_id=task_id)
    if before[1] != authorization.get("branch"):
        raise ControlError("worktree branch differs from its preauthorized branch")
    _check_execution_claims(repo, target)
    _check_fable_executions(repo, target, task_id)
    _assert_scheduler_unbound(target)
    _check_no_open_files(target)
    time.sleep(5)
    _check_execution_claims(repo, target)
    _check_fable_executions(repo, target, task_id)
    _assert_scheduler_unbound(target)
    _check_no_open_files(target)
    after = _worktree_snapshot(repo, target, base, allowed_rebuildable_caches=allowed_caches, task_id=task_id)
    if before[:4] != after[:4]:
        raise ControlError("worktree or base changed during the safety checks")

    # Consume the authorization atomically before the destructive operation.
    # A changed or missing marker stops here with the worktree still present.
    claim = _claim_close_authorization(marker, authorization_bytes)
    try:
        # Deliberately no --force fallback and no branch deletion.
        removed = git(repo, "worktree", "remove", str(target), check=False)
        if removed.returncode != 0:
            detail = removed.stderr.strip() or removed.stdout.strip()
            raise ControlError(f"normal git worktree remove failed; no fallback was attempted: {detail}")
        if target.exists() or target.is_symlink() or target in _parse_worktree_list(repo):
            raise ControlError("worktree removal did not reach the verified absent state")
        branch_tip = None
        if before[1] != "DETACHED":
            ref = f"refs/heads/{before[1]}"
            branch_tip = git(repo, "rev-parse", "--verify", ref).stdout.strip()
            if branch_tip != before[0]:
                raise ControlError("worktree was removed but its branch identity changed")
    except Exception as exc:
        # Re-arm only when the registered worktree demonstrably remains intact.
        # If removal may have completed, keep the claim for human reconciliation.
        try:
            worktree_remains = target.exists() and target in _parse_worktree_list(repo)
        except Exception:
            worktree_remains = False
        if worktree_remains and not _restore_claimed_lifecycle_marker(claim, marker):
            raise ControlError(
                f"worktree was preserved, but its authorization claim could not be restored; inspect {claim}"
            ) from exc
        raise
    _finish_close_authorization_claim(claim, marker)
    return {
        "status": "AUTO_CLOSED",
        "worktree": str(target),
        "head": before[0],
        "branch": before[1],
        "branch_tip_preserved": branch_tip,
        "base": base,
        "merge_type": before[4]["type"],
        "merge_target_commit": before[4]["target_commit"],
        "evidence": evidence,
        "forced": False,
        "branch_deleted": False,
    }


def _run_git_for_test(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.fsmonitor=false", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
    return result.stdout.strip()


def feedback_self_test() -> dict[str, Any]:
    checks: dict[str, bool] = {}
    canonical_tmp = Path(tempfile.gettempdir()).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="fable-feedback-test-", dir=str(canonical_tmp)) as temporary:
        root = Path(temporary)
        repo = root / "repo"
        repo.mkdir()
        _run_git_for_test(repo, "init", "--initial-branch=main")
        _run_git_for_test(repo, "config", "user.name", "Fable Feedback Test")
        _run_git_for_test(repo, "config", "user.email", "fable-feedback-test@example.invalid")
        marker = repo / "tracked.txt"
        marker.write_text("synthetic evidence fixture\n", encoding="utf-8")
        _run_git_for_test(repo, "add", "tracked.txt")
        _run_git_for_test(repo, "commit", "-m", "synthetic feedback fixture")

        task_id = "SYNTHETIC_PYTEST_BOUNDARY_R1"
        task_root = repo / ".fable" / "checkpoints" / task_id
        captures = task_root / "captures"
        executions = task_root / "executions"
        captures.mkdir(parents=True)
        executions.mkdir()
        cases = [
            {
                "execution_id": "postjudge-full-suite-r1",
                "basetemp": str(task_root / "pytest-full-r1"),
                "exit_status": 1,
                "duration_seconds": 25,
                "stdout": "E LocalDataError: local data path must be outside Git worktrees\nFAILED test_local_data_boundary\n",
            },
            {
                "execution_id": "postjudge-full-suite-r2",
                "basetemp": "/tmp/B649_SYNTHETIC-pytest-full-r2",
                "exit_status": 1,
                "duration_seconds": 2384,
                "stdout": "E HistoricalReplaySourceError: B649 source paths are protected\nFAILED test_protected_source_boundary\n",
            },
        ]
        fixture_paths: list[Path] = []
        for index, case in enumerate(cases, start=1):
            capture_path = captures / f"{case['execution_id']}.json"
            record_path = executions / f"{case['execution_id']}.json"
            started = datetime.fromisoformat(f"2026-10-20T00:00:{index:02d}+00:00")
            ended = started.timestamp() + case["duration_seconds"]
            capture = {
                "schema_version": 1,
                "command": ["pytest", "-q", "--basetemp", case["basetemp"]],
                "exit_status": case["exit_status"],
                "stdout": case["stdout"],
                "stderr": "",
                "started_at": started.isoformat().replace("+00:00", "Z"),
                "ended_at": datetime.fromtimestamp(ended, tz=started.tzinfo).isoformat().replace("+00:00", "Z"),
            }
            capture_path.write_text(json.dumps(capture, sort_keys=True) + "\n", encoding="utf-8")
            record = {
                "schema_version": 1,
                "task_id": task_id,
                "execution_id": case["execution_id"],
                "status": "COMPLETED",
                "durable_capture_path": str(capture_path),
            }
            record_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
            fixture_paths.extend((capture_path, record_path))

        before_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in fixture_paths}
        summary = summarize_execution_history(str(repo), task_id)
        risky_one = validate_test_scratch(str(repo), cases[0]["basetemp"])
        risky_two = validate_test_scratch(str(repo), cases[1]["basetemp"])
        neutral = validate_test_scratch(str(repo), str(root / "neutral-pytest-scratch"))
        after_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in fixture_paths}
        finding = summary["recommendations"][0] if summary["recommendations"] else {}
        checks["historical_boundaries_extracted_with_source_hashes"] = (
            summary["inventory"]["execution_records"] == 2
            and summary["inventory"]["outcomes"]["COMMAND_FAILED"] == 2
            and finding.get("occurrences") == 2
            and all(source.get("record", {}).get("sha256") and source.get("capture", {}).get("sha256") for source in finding.get("sources", []))
        )
        checks["unsafe_worktree_and_token_paths_blocked"] = (
            risky_one["status"] == "BLOCKED" and risky_two["status"] == "BLOCKED"
        )
        checks["neutral_external_path_passes"] = neutral["status"] == "PASS"
        checks["baseline_failed_runtime_accounted"] = (
            sum(row["duration_seconds"] or 0 for row in summary["executions"]) == 2409
        )
        checks["preflight_prevents_launch_without_test_side_effects"] = (
            sum(item["status"] == "BLOCKED" for item in (risky_one, risky_two)) == 2
            and all(before_hashes[path] == after_hashes[path] for path in before_hashes)
        )
        result = {
            "status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "baseline": {"unsafe_pytest_launches": 2, "failed_test_runtime_seconds": 2409},
            "with_preflight": {"unsafe_launches_blocked": 2, "pytest_processes_started": 0, "test_runtime_seconds": 0},
            "neutral_external_path": neutral,
            "formal_fixture_files_unchanged": all(before_hashes[path] == after_hashes[path] for path in before_hashes),
        }
    return result


def self_test() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["disk_policy"] = (
        disk_decision(14 * GIB, "research-worktree", "before")[0] == "WARN"
        and disk_decision(7 * GIB, "research-worktree", "before")[0] == "WARN"
        and "below 8 GiB" in str(disk_decision(7 * GIB, "research-worktree", "before")[1])
        and disk_decision(7 * GIB, "high-disk-task", "before")[0] == "WARN"
        and disk_decision(7 * GIB, "ordinary", "before")[0] == "WARN"
        and disk_decision(7 * GIB, "read-only-diagnostic", "before")[0] == "WARN_EXEMPT"
        and disk_decision(7 * GIB, "authorized-reclaim", "before")[0] == "WARN_EXEMPT"
        and disk_decision(7 * GIB, "high-disk-task", "after")[0] == "WARN"
    )

    canonical_tmp = Path(tempfile.gettempdir()).resolve(strict=True)
    with tempfile.TemporaryDirectory(
        prefix="fable-artifact-controls-",
        dir=str(canonical_tmp),
    ) as temporary:
        root = Path(temporary)
        repo = root / "repo"
        repo.mkdir()
        _run_git_for_test(repo, "init", "--initial-branch=main")
        _run_git_for_test(repo, "config", "user.name", "Fable Synthetic Test")
        _run_git_for_test(repo, "config", "user.email", "fable-test@example.invalid")
        base_file = repo / "base.txt"
        base_file.write_text("base\n", encoding="utf-8")
        _run_git_for_test(repo, "add", "base.txt")
        _run_git_for_test(repo, "commit", "-m", "base")

        evidence = root / "evidence"
        evidence.mkdir()
        formal = evidence / "certificate.json"
        formal.write_bytes(b'{"result":"synthetic-formal-evidence"}\n')
        formal_sha = hashlib.sha256(formal.read_bytes()).hexdigest()
        formal_manifest = evidence / "manifest.json"
        formal_manifest.write_text(
            json.dumps({"files": [{"path": str(formal), "sha256": formal_sha}]}),
            encoding="utf-8",
        )

        # Exercise admission at 7 GiB without changing the real disk state.
        original_check_space = globals()["check_space"]
        def simulated_low_space(path: str, operation: str, phase: str) -> dict[str, Any]:
            status, message = disk_decision(7 * GIB, operation, phase)
            return {
                "operation": operation,
                "phase": phase,
                "path": path,
                "available_bytes": 7 * GIB,
                "available_gib": 7.0,
                "warning_below_gib": 15,
                "low_space_warning_below_gib": 8,
                "status": status,
                "message": message,
            }

        globals()["check_space"] = simulated_low_space
        low_space_worktree = root / "created-with-low-space-warning"
        low_space_result = create_research_worktree(
            str(repo), str(low_space_worktree), "main", "codex/synthetic-low-space",
            "synthetic-low-space", "persistent-research", auto_close_authorized=False,
        )
        command_marker = root / "gated-command-was-run"
        launch_gated_command(
            str(root), "high-disk-task",
            [sys.executable, "-B", "-c", f"from pathlib import Path; Path({str(command_marker)!r}).touch()"],
        )
        globals()["check_space"] = original_check_space
        checks["low_space_warns_and_creates_worktree"] = (
            low_space_worktree.exists()
            and "below 8 GiB" in str(low_space_result.get("warning_before"))
        )
        checks["low_space_warns_and_runs_high_disk_launch"] = command_marker.exists()
        _run_git_for_test(repo, "worktree", "remove", str(low_space_worktree))

        capture_dir = repo / ".fable" / "checkpoints" / "synthetic" / "captures"
        capture_dir.mkdir(parents=True)
        capture = capture_dir / "proof.json"
        capture.write_bytes(b'{"schema_version":1,"stdout":"formal proof capture"}\n')
        capture_sum = hashlib.sha256(capture.read_bytes()).hexdigest()
        sums = capture_dir / "SHA256SUMS"
        sums.write_text(f"{capture_sum}  proof.json\n", encoding="ascii")

        before_log_bytes = 12 * MIB
        record_payload = "x" * (64 * 1024)
        submitted_payload_bytes = 0
        written_log_bytes = 0
        dropped_count = 0
        for _ in range(before_log_bytes // (64 * 1024)):
            submitted_payload_bytes += len(record_payload.encode("utf-8"))
            result = append_temporary_log(
                str(repo),
                "synthetic-log-task",
                {"stream": "debug", "payload": record_payload},
            )
            if result["status"] == "APPENDED":
                written_log_bytes += result["bytes"]
            else:
                dropped_count += 1
        log_root = repo / ".fable" / "temporary" / "synthetic-log-task" / "logs"
        after_log_bytes = sum(path.stat().st_size for path in log_root.glob("observer*.jsonl"))
        checks["bounded_rotation"] = (
            submitted_payload_bytes > after_log_bytes
            and written_log_bytes == after_log_bytes
            and after_log_bytes <= LOG_BUDGET
            and dropped_count > 0
            and len(list(log_root.glob("observer.*.jsonl"))) > 0
        )
        checks["formal_evidence_unchanged"] = (
            hashlib.sha256(capture.read_bytes()).hexdigest() == capture_sum
            and sums.read_text(encoding="ascii") == f"{capture_sum}  proof.json\n"
        )
        checks["receipt_reference_roundtrip"] = (
            formal.exists()
            and hashlib.sha256(formal.read_bytes()).hexdigest() == formal_sha
        )

        unmerged = root / "unmerged-worktree"
        prepare_temporary_worktree(
            str(repo), str(unmerged), "main", "codex/synthetic-unmerged",
            "synthetic-unmerged", auto_close_authorized=True,
        )
        _run_git_for_test(repo, "worktree", "add", "-b", "codex/synthetic-unmerged", str(unmerged), "main")
        bind_temporary_worktree(
            str(repo), str(unmerged), "main", "codex/synthetic-unmerged", "synthetic-unmerged",
        )
        (unmerged / "unmerged.txt").write_text("unmerged\n", encoding="utf-8")
        _run_git_for_test(unmerged, "add", "unmerged.txt")
        _run_git_for_test(unmerged, "commit", "-m", "unmerged")
        denied = False
        try:
            close_temporary_worktree(
                str(repo), str(unmerged), "main", str(formal_manifest),
                "synthetic-unmerged",
                auto_close_authorized=False, task_complete=True,
                agents_quiescent=True, runtime_quiescent=True, scheduler_quiescent=True,
            )
        except ControlError:
            denied = True
        checks["unauthorized_worktree_preserved"] = (
            denied and unmerged.is_dir() and unmerged in _parse_worktree_list(repo)
        )

        unmerged_denied = False
        try:
            close_temporary_worktree(
                str(repo), str(unmerged), "main", str(formal_manifest),
                "synthetic-unmerged",
                auto_close_authorized=True, task_complete=True,
                agents_quiescent=True, runtime_quiescent=True, scheduler_quiescent=True,
            )
        except ControlError as exc:
            unmerged_denied = "not merged" in str(exc)
        checks["unmerged_worktree_preserved"] = (
            unmerged_denied and unmerged.is_dir() and unmerged in _parse_worktree_list(repo)
        )

        legacy = root / "legacy-worktree"
        _run_git_for_test(repo, "worktree", "add", "-b", "codex/synthetic-legacy", str(legacy), "main")
        legacy_denied = False
        try:
            close_temporary_worktree(
                str(repo), str(legacy), "main", str(formal_manifest),
                "synthetic-legacy",
                auto_close_authorized=True, task_complete=True,
                agents_quiescent=True, runtime_quiescent=True, scheduler_quiescent=True,
            )
        except ControlError as exc:
            legacy_denied = "preauthorization" in str(exc)
        checks["preexisting_unregistered_worktree_preserved"] = (
            legacy_denied and legacy.is_dir() and legacy in _parse_worktree_list(repo)
        )

        active_claim_worktree = root / "active-claim-worktree"
        prepare_temporary_worktree(
            str(repo), str(active_claim_worktree), "main", "codex/synthetic-active-claim",
            "synthetic-active-claim", auto_close_authorized=True,
        )
        _run_git_for_test(
            repo, "worktree", "add", "-b", "codex/synthetic-active-claim",
            str(active_claim_worktree), "main",
        )
        bind_temporary_worktree(
            str(repo), str(active_claim_worktree), "main", "codex/synthetic-active-claim",
            "synthetic-active-claim",
        )
        claims_dir = repo / ".task-data" / "execution-claims"
        claims_dir.mkdir(parents=True)
        claim_path = claims_dir / "synthetic.json"
        claim_path.write_text(json.dumps({
            "schema_version": 1,
            "task_key": "synthetic",
            "owner_id": "synthetic-owner",
            "owner_pid": os.getpid(),
            "child_pid": None,
            "hostname": "synthetic",
            "started_at_utc": "2026-01-01T00:00:00+00:00",
            "heartbeat_at_utc": "2026-01-01T00:00:00+00:00",
            "cwd": str(active_claim_worktree),
            "command": ["synthetic-command"],
            "claim_root": str(claims_dir),
        }), encoding="utf-8")
        active_claim_denied = False
        try:
            close_temporary_worktree(
                str(repo), str(active_claim_worktree), "main", str(formal_manifest),
                "synthetic-active-claim",
                auto_close_authorized=True, task_complete=True,
                agents_quiescent=True, runtime_quiescent=True, scheduler_quiescent=True,
            )
        except ControlError as exc:
            active_claim_denied = "active execution claim" in str(exc)
        checks["active_agent_claim_preserved"] = (
            active_claim_denied
            and active_claim_worktree.is_dir()
            and active_claim_worktree in _parse_worktree_list(repo)
        )
        claim_path.unlink()

        active_execution_worktree = root / "active-execution-worktree"
        prepare_temporary_worktree(
            str(repo), str(active_execution_worktree), "main", "codex/synthetic-active-execution",
            "synthetic-active-execution", auto_close_authorized=True,
        )
        _run_git_for_test(
            repo, "worktree", "add", "-b", "codex/synthetic-active-execution",
            str(active_execution_worktree), "main",
        )
        bind_temporary_worktree(
            str(repo), str(active_execution_worktree), "main",
            "codex/synthetic-active-execution", "synthetic-active-execution",
        )
        executions = repo / ".fable" / "checkpoints" / "synthetic-active-execution" / "executions"
        executions.mkdir(parents=True)
        (executions / "RUN-1.json").write_text(json.dumps({
            "schema_version": 1,
            "task_id": "synthetic-active-execution",
            "execution_id": "RUN-1",
            "pid": os.getpid(),
            "status": "STARTED",
            "durable_capture_path": "",
            "started_at": "2026-01-01T00:00:00Z",
            "ended_at": None,
        }), encoding="utf-8")
        active_execution_denied = False
        try:
            close_temporary_worktree(
                str(repo), str(active_execution_worktree), "main", str(formal_manifest),
                "synthetic-active-execution",
                auto_close_authorized=True, task_complete=True,
                agents_quiescent=True, runtime_quiescent=True, scheduler_quiescent=True,
            )
        except ControlError as exc:
            active_execution_denied = "not terminally completed" in str(exc)
        checks["active_fable_execution_preserved"] = (
            active_execution_denied
            and active_execution_worktree.is_dir()
            and active_execution_worktree in _parse_worktree_list(repo)
        )

        merged = root / "merged-worktree"
        prepare_temporary_worktree(
            str(repo), str(merged), "main", "codex/synthetic-merged",
            "synthetic-merged", auto_close_authorized=True,
        )
        _run_git_for_test(repo, "worktree", "add", "-b", "codex/synthetic-merged", str(merged), "main")
        bind_temporary_worktree(
            str(repo), str(merged), "main", "codex/synthetic-merged", "synthetic-merged",
        )
        (merged / "merged.txt").write_text("merged\n", encoding="utf-8")
        _run_git_for_test(merged, "add", "merged.txt")
        _run_git_for_test(merged, "commit", "-m", "merged")
        _run_git_for_test(repo, "merge", "--no-edit", "codex/synthetic-merged")
        removed = close_temporary_worktree(
            str(repo), str(merged), "main", str(formal_manifest),
            "synthetic-merged",
            auto_close_authorized=True, task_complete=True,
            agents_quiescent=True, runtime_quiescent=True, scheduler_quiescent=True,
        )
        branch_tip = _run_git_for_test(repo, "rev-parse", "refs/heads/codex/synthetic-merged")
        checks["authorized_safe_close"] = (
            removed["status"] == "AUTO_CLOSED"
            and removed["forced"] is False
            and removed["branch_deleted"] is False
            and not merged.exists()
            and branch_tip == removed["head"]
        )
        checks["formal_evidence_survived_close"] = (
            formal.exists()
            and hashlib.sha256(formal.read_bytes()).hexdigest() == formal_sha
            and hashlib.sha256(capture.read_bytes()).hexdigest() == capture_sum
        )

        # Test positive case: close without any flags when preauthorized
        default_auto = root / "default-auto-worktree"
        create_research_worktree(
            str(repo), str(default_auto), "main", "codex/synthetic-default-auto",
            "synthetic-default-auto", "temporary-task",
            auto_close_authorized=True,
            close_policy="AUTO_CLOSE_AFTER_MERGE",
            merge_target_ref="main",
            persistent_evidence_root=str(evidence),
        )
        (default_auto / "auto.txt").write_text("auto-reclaim\n", encoding="utf-8")
        _run_git_for_test(default_auto, "add", "auto.txt")
        _run_git_for_test(default_auto, "commit", "-m", "auto-reclaim commit")
        _run_git_for_test(repo, "merge", "--no-edit", "codex/synthetic-default-auto")
        auto_removed = close_temporary_worktree(
            str(repo), str(default_auto), "main", str(formal_manifest),
            "synthetic-default-auto",
        )
        branch_auto_tip = _run_git_for_test(repo, "rev-parse", "refs/heads/codex/synthetic-default-auto")
        checks["default_auto_reclaim_without_flags"] = (
            auto_removed["status"] == "AUTO_CLOSED"
            and not default_auto.exists()
            and branch_auto_tip == auto_removed["head"]
            and auto_removed["branch_deleted"] is False
        )

        # Test positive case: squash merge reclaim
        squashed = root / "squashed-worktree"
        create_research_worktree(
            str(repo), str(squashed), "main", "codex/synthetic-squashed",
            "synthetic-squashed", "temporary-task",
            auto_close_authorized=True,
            close_policy="AUTO_CLOSE_AFTER_MERGE",
        )
        (squashed / "squashed.txt").write_text("squashed content\n", encoding="utf-8")
        _run_git_for_test(squashed, "add", "squashed.txt")
        _run_git_for_test(squashed, "commit", "-m", "squash branch commit")
        squashed_head = _run_git_for_test(squashed, "rev-parse", "HEAD")
        _run_git_for_test(repo, "merge", "--squash", "codex/synthetic-squashed")
        _run_git_for_test(repo, "commit", "-m", f"Squashed feature (commit: {squashed_head})")
        squash_removed = close_temporary_worktree(
            str(repo), str(squashed), "main", str(formal_manifest),
            "synthetic-squashed",
        )
        squash_branch_tip = _run_git_for_test(repo, "rev-parse", "refs/heads/codex/synthetic-squashed")
        checks["squash_merge_reclaim"] = (
            squash_removed["status"] == "AUTO_CLOSED"
            and squash_removed["merge_type"] == "SQUASH_MERGE"
            and not squashed.exists()
            and squash_branch_tip == squashed_head
        )

        # Test negative case: locked worktree preserved
        locked_wt = root / "locked-worktree"
        create_research_worktree(
            str(repo), str(locked_wt), "main", "codex/synthetic-locked",
            "synthetic-locked", "temporary-task",
            auto_close_authorized=True,
        )
        (locked_wt / "locked.txt").write_text("locked\n", encoding="utf-8")
        _run_git_for_test(locked_wt, "add", "locked.txt")
        _run_git_for_test(locked_wt, "commit", "-m", "locked commit")
        _run_git_for_test(repo, "merge", "--no-edit", "codex/synthetic-locked")
        _run_git_for_test(repo, "worktree", "lock", str(locked_wt), "--reason", "test lock")
        locked_denied = False
        try:
            close_temporary_worktree(
                str(repo), str(locked_wt), "main", str(formal_manifest),
                "synthetic-locked",
            )
        except ControlError as exc:
            locked_denied = "locked" in str(exc)
        checks["locked_worktree_preserved"] = (
            locked_denied and locked_wt.is_dir() and locked_wt in _parse_worktree_list(repo)
        )
        _run_git_for_test(repo, "worktree", "unlock", str(locked_wt))

        # Test negative case: ordinary untracked file preserved
        untracked_wt = root / "untracked-worktree"
        create_research_worktree(
            str(repo), str(untracked_wt), "main", "codex/synthetic-untracked",
            "synthetic-untracked", "temporary-task",
            auto_close_authorized=True,
        )
        (untracked_wt / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        _run_git_for_test(untracked_wt, "add", "tracked.txt")
        _run_git_for_test(untracked_wt, "commit", "-m", "tracked commit")
        _run_git_for_test(repo, "merge", "--no-edit", "codex/synthetic-untracked")
        (untracked_wt / "ordinary.txt").write_text("ordinary untracked file\n", encoding="utf-8")
        untracked_denied = False
        try:
            close_temporary_worktree(
                str(repo), str(untracked_wt), "main", str(formal_manifest),
                "synthetic-untracked",
            )
        except ControlError as exc:
            untracked_denied = "untracked" in str(exc)
        checks["ordinary_untracked_preserved"] = (
            untracked_denied and untracked_wt.is_dir() and untracked_wt in _parse_worktree_list(repo)
        )

        # Test positive case: declared rebuildable cache cleaned and reclaimed
        cache_wt = root / "cache-worktree"
        create_research_worktree(
            str(repo), str(cache_wt), "main", "codex/synthetic-cache",
            "synthetic-cache", "temporary-task",
            auto_close_authorized=True,
            allowed_rebuildable_caches=["__pycache__", ".pytest_cache"],
        )
        (cache_wt / "code.txt").write_text("code\n", encoding="utf-8")
        _run_git_for_test(cache_wt, "add", "code.txt")
        _run_git_for_test(cache_wt, "commit", "-m", "code commit")
        _run_git_for_test(repo, "merge", "--no-edit", "codex/synthetic-cache")
        pycache_dir = cache_wt / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "code.cpython-39.pyc").write_bytes(b"dummy bytecode\n")
        cache_removed = close_temporary_worktree(
            str(repo), str(cache_wt), "main", str(formal_manifest),
            "synthetic-cache",
        )
        checks["rebuildable_cache_cleaned_and_reclaimed"] = (
            cache_removed["status"] == "AUTO_CLOSED" and not cache_wt.exists()
        )
        summary = {
            "status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "temporary_log_input_bytes": before_log_bytes,
            "temporary_log_retained_bytes": after_log_bytes,
            "temporary_log_budget_bytes": LOG_BUDGET,
            "temporary_log_payload_submitted_bytes": submitted_payload_bytes,
            "temporary_log_bytes_written": written_log_bytes,
            "temporary_log_records_dropped": dropped_count,
            "formal_capture_sha256": capture_sum,
            "external_evidence_sha256": formal_sha,
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    space = sub.add_parser("space-check")
    space.add_argument("--path", required=True)
    space.add_argument(
        "--operation",
        required=True,
        choices=("ordinary", "research-worktree", "high-disk-task", "read-only-diagnostic", "authorized-reclaim"),
    )
    space.add_argument("--phase", required=True, choices=("before", "after"))

    log = sub.add_parser("append-log")
    log.add_argument("--repo", required=True)
    log.add_argument("--task-id", required=True)
    log.add_argument("--record-json", required=True)

    sub.add_parser("self-test")
    sub.add_parser("feedback-self-test")
    feedback = sub.add_parser("evidence-summary")
    feedback.add_argument("--repo")
    feedback.add_argument("--task-id")
    feedback.add_argument("--task-evidence-root")
    scratch = sub.add_parser("validate-test-scratch")
    scratch.add_argument("--repo", required=True)
    scratch.add_argument("--path", required=True)
    create = sub.add_parser("create-worktree")
    create.add_argument("--repo", required=True)
    create.add_argument("--worktree", required=True)
    create.add_argument("--base", required=True)
    create.add_argument("--branch", required=True)
    create.add_argument("--task-id", required=True)
    create.add_argument("--lifecycle", required=True, choices=("persistent-research", "temporary-task"))
    create.add_argument("--auto-close-authorized", action="store_true")
    create.add_argument("--close-policy", choices=("PRESERVE", "AUTO_CLOSE_AFTER_MERGE"), default=None)
    create.add_argument("--merge-target-ref", default=None)
    create.add_argument("--persistent-evidence-root", default=None)
    create.add_argument("--allowed-rebuildable-cache", action="append", default=None)

    close = sub.add_parser("close-temporary-worktree")
    close.add_argument("--repo", required=True)
    close.add_argument("--worktree", required=True)
    close.add_argument("--base", required=True)
    close.add_argument("--evidence-manifest", required=True)
    close.add_argument("--task-id", required=True)
    close.add_argument("--auto-close-authorized", action="store_true", default=None)
    close.add_argument("--task-complete", action="store_true", default=None)
    close.add_argument("--agents-quiescent", action="store_true", default=None)
    close.add_argument("--runtime-quiescent", action="store_true", default=None)
    close.add_argument("--scheduler-quiescent", action="store_true", default=None)

    run = sub.add_parser("run-gated")
    run.add_argument("--path", required=True)
    run.add_argument(
        "--operation",
        required=True,
        choices=("ordinary", "high-disk-task", "read-only-diagnostic", "authorized-reclaim"),
    )
    run.add_argument("command", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    try:
        if args.action == "space-check":
            result = check_space(args.path, args.operation, args.phase)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.action == "append-log":
            try:
                record = json.loads(args.record_json)
            except json.JSONDecodeError as exc:
                raise ControlError(f"--record-json is invalid JSON: {exc}") from exc
            result = append_temporary_log(args.repo, args.task_id, record)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.action == "self-test":
            result = self_test()
            print(json.dumps(result, sort_keys=True))
            return 0 if result["status"] == "PASS" else 1
        if args.action == "feedback-self-test":
            result = feedback_self_test()
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result["status"] == "PASS" else 1
        if args.action == "evidence-summary":
            if args.repo is not None:
                result = summarize_execution_history(args.repo, args.task_id, args.task_evidence_root)
            else:
                if args.task_evidence_root is None:
                    raise ControlError("--repo or --task-evidence-root is required")
                if args.task_id is None:
                    raise ControlError("--task-id is required with --task-evidence-root")
                task_id = _safe_task_id(args.task_id)
                result = {
                    "schema": "fable-evidence-feedback-v1",
                    "repository": None,
                    "task_filter": task_id,
                    "execution_history": "NOT_READ_WITHOUT_REPOSITORY",
                    "task_evidence": summarize_task_evidence(args.task_evidence_root, task_id),
                }
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.action == "validate-test-scratch":
            result = validate_test_scratch(args.repo, args.path)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 2 if result["status"] == "BLOCKED" else 0
        if args.action == "create-worktree":
            effective_close_policy = args.close_policy
            if effective_close_policy is None:
                effective_close_policy = "AUTO_CLOSE_AFTER_MERGE" if args.auto_close_authorized else "PRESERVE"
            result = create_research_worktree(
                args.repo,
                args.worktree,
                args.base,
                args.branch,
                args.task_id,
                args.lifecycle,
                auto_close_authorized=args.auto_close_authorized,
                close_policy=effective_close_policy,
                merge_target_ref=args.merge_target_ref,
                persistent_evidence_root=args.persistent_evidence_root,
                allowed_rebuildable_caches=args.allowed_rebuildable_cache,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.action == "run-gated":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            result = launch_gated_command(args.path, args.operation, command)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return_code = result["returncode"]
            if return_code < 0:
                os.kill(os.getpid(), -return_code)
                return 128 + -return_code
            return return_code
        result = close_temporary_worktree(
            args.repo,
            args.worktree,
            args.base,
            args.evidence_manifest,
            args.task_id,
            auto_close_authorized=args.auto_close_authorized,
            task_complete=args.task_complete,
            agents_quiescent=args.agents_quiescent,
            runtime_quiescent=args.runtime_quiescent,
            scheduler_quiescent=args.scheduler_quiescent,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except ControlError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    except OSError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": f"filesystem error: {exc}"}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
