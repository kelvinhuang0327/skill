#!/usr/bin/env python3
"""Offline advisory checks for mechanically provable report contradictions.

The detector deliberately recognizes a small structural grammar. It does not
score evidence, infer that two vaguely similar descriptions name the same
surface, or decide whether a reported verdict is credible.

Exit behavior:
  * audit finding present: 0
  * audit clean: 0
  * invalid invocation, unreadable input, or unsafe decoding: nonzero
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DETECTOR_VERSION = "1.0.0-r1"
R3 = "R3_PASS_NOT_RUN_CONFLICT"
R4 = "R4_DUPLICATE_TERMINAL_FIELD_CONFLICT"
R6 = "R6_REPETITION_LOOP"
RULES = (R3, R4, R6)
PROVENANCE_LABELS = ("SYNTHETIC", "REAL_SAMPLED_DEIDENTIFIED")
MAX_REPORT_BYTES = 5 * 1024 * 1024

STATUS_PASS = "PASS"
STATUS_NOT_RUN = "NOT RUN"

CHECK_KEYS = {"check", "surface", "檢查", "检查", "檢查項目", "检查项目", "表面"}
SCOPE_KEYS = {"scope", "範圍", "范围"}
STATUS_KEYS = {"status", "狀態", "状态"}

TABLE_CHECK_HEADERS = CHECK_KEYS | {"check name", "surface name", "檢查名稱", "检查名称"}
TABLE_SCOPE_HEADERS = SCOPE_KEYS
TABLE_STATUS_HEADERS = STATUS_KEYS | {"result", "結果", "结果"}

FENCE_RE = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
BLOCK_FIELD_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?P<key>CHECK|SURFACE|SCOPE|STATUS|"
    r"檢查項目|检查项目|檢查|检查|表面|範圍|范围|狀態|状态)\s*[:：=]\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)
KEY_VALUE_STATUS_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?P<label>.+?)\s*[:：=]\s*"
    r"(?P<status>PASS|NOT(?:[ _-]+)RUN|通過|通过|未執行|未执行)\s*[.;。]?\s*$",
    re.IGNORECASE,
)
PROSE_EN_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:check|surface)\s+"
    r"(?P<name>`[^`]+`|\"[^\"]+\"|'[^']+')"
    r"(?:\s+(?:in|under)\s+scope\s+(?P<scope>`[^`]+`|\"[^\"]+\"|'[^']+'))?"
    r"\s+(?:has\s+)?status\s*[:=]\s*"
    r"(?P<status>PASS|NOT(?:[ _-]+)RUN)\s*[.;]?\s*$",
    re.IGNORECASE,
)
PROSE_ZH_RE = re.compile(
    r"^\s*(?:檢查|检查|表面)\s*(?P<name>「[^」]+」|『[^』]+』|`[^`]+`)"
    r"(?:\s*[（(]\s*(?:範圍|范围)\s*[:：]\s*(?P<scope>[^）)]+)\s*[）)])?"
    r"\s*(?:狀態|状态)\s*[:：=]\s*"
    r"(?P<status>PASS|NOT(?:[ _-]+)RUN|通過|通过|未執行|未执行)\s*[。.]?\s*$",
    re.IGNORECASE,
)
TERMINAL_FIELD_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*|`)?(?P<field>[A-Z][A-Z0-9_]{2,})(?:\*\*|`)?"
    r"\s*:\s*(?P<value>[A-Z][A-Z0-9_ -]*[A-Z0-9])\s*[.;]?\s*$"
)


class AuditInputError(Exception):
    """An invalid, unreadable, or unsafely decoded input."""


@dataclass(frozen=True)
class VisibleLine:
    number: int
    text: str | None


@dataclass(frozen=True)
class StatusObservation:
    scope_key: str
    surface_key: str
    scope_display: str | None
    surface_display: str
    status: str
    line: int
    form: str


def _strip_paired_markup(value: str) -> str:
    value = unicodedata.normalize("NFKC", value.strip())
    pairs = (
        ("**", "**"),
        ("__", "__"),
        ("`", "`"),
        ('"', '"'),
        ("'", "'"),
        ("「", "」"),
        ("『", "』"),
    )
    changed = True
    while changed and value:
        changed = False
        for left, right in pairs:
            if value.startswith(left) and value.endswith(right) and len(value) > len(left) + len(right):
                value = value[len(left) : len(value) - len(right)].strip()
                changed = True
                break
    return value


def _display_token(value: str) -> str:
    return re.sub(r"\s+", " ", _strip_paired_markup(value)).strip()


def _identity_token(value: str) -> str:
    return _display_token(value).casefold()


def _header_token(value: str) -> str:
    return _identity_token(value).rstrip(":：")


def _canonical_status(value: str) -> str | None:
    token = _display_token(value).rstrip(".;。").casefold()
    token_with_spaces = re.sub(r"[ _-]+", " ", token)
    if token_with_spaces in {"pass", "通過", "通过"}:
        return STATUS_PASS
    if token_with_spaces in {"not run", "未執行", "未执行"}:
        return STATUS_NOT_RUN
    return None


def _parse_scoped_label(raw: str) -> tuple[str | None, str]:
    value = _display_token(raw)
    bracketed = re.fullmatch(r"\[([^\]]+)\]\s+(.+)", value)
    if bracketed:
        return _display_token(bracketed.group(1)), _display_token(bracketed.group(2))
    if "::" in value:
        scope, surface = value.split("::", 1)
        if scope.strip() and surface.strip():
            return _display_token(scope), _display_token(surface)
    return None, value


def _visible_lines(text: str) -> list[VisibleLine]:
    visible: list[VisibleLine] = []
    fence_char: str | None = None
    fence_length = 0
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE_RE.match(line)
        if fence_char is None:
            if match:
                fence = match.group("fence")
                fence_char = fence[0]
                fence_length = len(fence)
                visible.append(VisibleLine(number, None))
            else:
                visible.append(VisibleLine(number, line))
            continue
        if match:
            fence = match.group("fence")
            if fence[0] == fence_char and len(fence) >= fence_length:
                fence_char = None
                fence_length = 0
        visible.append(VisibleLine(number, None))
    return visible


def _append_observation(
    observations: list[StatusObservation],
    *,
    scope: str | None,
    surface: str,
    status: str | None,
    line: int,
    form: str,
) -> None:
    surface_display = _display_token(surface)
    scope_display = _display_token(scope) if scope else None
    if not surface_display or status is None:
        return
    observations.append(
        StatusObservation(
            scope_key=_identity_token(scope_display or ""),
            surface_key=_identity_token(surface_display),
            scope_display=scope_display,
            surface_display=surface_display,
            status=status,
            line=line,
            form=form,
        )
    )


def _collect_status_blocks(lines: list[VisibleLine]) -> list[StatusObservation]:
    observations: list[StatusObservation] = []
    current_surface: str | None = None
    current_scope: str | None = None
    statuses: list[tuple[str, int]] = []

    def flush() -> None:
        nonlocal current_surface, current_scope, statuses
        if current_surface:
            for status, line_number in statuses:
                _append_observation(
                    observations,
                    scope=current_scope,
                    surface=current_surface,
                    status=status,
                    line=line_number,
                    form="status_block",
                )
        current_surface = None
        current_scope = None
        statuses = []

    for item in lines:
        if item.text is None or not item.text.strip():
            flush()
            continue
        match = BLOCK_FIELD_RE.match(item.text)
        if not match:
            flush()
            continue
        key = _header_token(match.group("key"))
        value = match.group("value")
        if key in CHECK_KEYS:
            if current_surface is not None:
                flush()
            current_scope, current_surface = _parse_scoped_label(value)
        elif key in SCOPE_KEYS:
            current_scope = _display_token(value)
        elif key in STATUS_KEYS and current_surface is not None:
            status = _canonical_status(value)
            if status is not None:
                statuses.append((status, item.number))
    flush()
    return observations


def _split_table_row(line: str) -> list[str] | None:
    if line.count("|") < 2:
        return None
    stripped = line.strip()
    cells = stripped.split("|")
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    if len(cells) < 2:
        return None
    return [cell.strip() for cell in cells]


def _is_table_separator(cells: Iterable[str]) -> bool:
    cells = list(cells)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _collect_tables(lines: list[VisibleLine]) -> list[StatusObservation]:
    observations: list[StatusObservation] = []
    index = 0
    while index + 1 < len(lines):
        header_line = lines[index]
        separator_line = lines[index + 1]
        if header_line.text is None or separator_line.text is None:
            index += 1
            continue
        headers = _split_table_row(header_line.text)
        separator = _split_table_row(separator_line.text)
        if not headers or not separator or len(headers) != len(separator) or not _is_table_separator(separator):
            index += 1
            continue
        normalized = [_header_token(cell) for cell in headers]
        check_index = next((i for i, cell in enumerate(normalized) if cell in TABLE_CHECK_HEADERS), None)
        status_index = next((i for i, cell in enumerate(normalized) if cell in TABLE_STATUS_HEADERS), None)
        scope_index = next((i for i, cell in enumerate(normalized) if cell in TABLE_SCOPE_HEADERS), None)
        if check_index is None or status_index is None:
            index += 1
            continue
        row_index = index + 2
        while row_index < len(lines):
            row_line = lines[row_index]
            if row_line.text is None or not row_line.text.strip():
                break
            row = _split_table_row(row_line.text)
            if row is None or len(row) != len(headers):
                break
            status = _canonical_status(row[status_index])
            if status is not None:
                scope = row[scope_index] if scope_index is not None else None
                _append_observation(
                    observations,
                    scope=scope,
                    surface=row[check_index],
                    status=status,
                    line=row_line.number,
                    form="table",
                )
            row_index += 1
        index = max(index + 1, row_index)
    return observations


def detect_r3(text: str) -> list[dict[str, Any]]:
    lines = _visible_lines(text)
    observations = _collect_status_blocks(lines)
    observations.extend(_collect_tables(lines))

    reserved_labels = CHECK_KEYS | SCOPE_KEYS | STATUS_KEYS
    for item in lines:
        if item.text is None:
            continue
        key_value = KEY_VALUE_STATUS_RE.match(item.text)
        if key_value:
            raw_label = key_value.group("label")
            if _header_token(raw_label) not in reserved_labels:
                scope, surface = _parse_scoped_label(raw_label)
                _append_observation(
                    observations,
                    scope=scope,
                    surface=surface,
                    status=_canonical_status(key_value.group("status")),
                    line=item.number,
                    form="label_value",
                )

        prose = PROSE_EN_RE.match(item.text)
        if prose:
            _append_observation(
                observations,
                scope=prose.group("scope"),
                surface=prose.group("name"),
                status=_canonical_status(prose.group("status")),
                line=item.number,
                form="explicit_prose",
            )
        prose_zh = PROSE_ZH_RE.match(item.text)
        if prose_zh:
            _append_observation(
                observations,
                scope=prose_zh.group("scope"),
                surface=prose_zh.group("name"),
                status=_canonical_status(prose_zh.group("status")),
                line=item.number,
                form="explicit_prose_zh",
            )

    unique = {
        (obs.scope_key, obs.surface_key, obs.status, obs.line, obs.form): obs
        for obs in observations
    }
    grouped: dict[tuple[str, str], list[StatusObservation]] = defaultdict(list)
    for obs in unique.values():
        grouped[(obs.scope_key, obs.surface_key)].append(obs)

    findings: list[dict[str, Any]] = []
    for key in sorted(grouped):
        group = sorted(grouped[key], key=lambda obs: (obs.line, obs.form, obs.status))
        statuses = {obs.status for obs in group}
        if {STATUS_PASS, STATUS_NOT_RUN}.issubset(statuses):
            first = group[0]
            findings.append(
                {
                    "identity": {
                        "scope": first.scope_display,
                        "surface": first.surface_display,
                        "mechanical_key": (
                            f"{first.scope_key}::{first.surface_key}"
                            if first.scope_key
                            else first.surface_key
                        ),
                    },
                    "not_run_locations": [
                        {"line": obs.line, "form": obs.form}
                        for obs in group
                        if obs.status == STATUS_NOT_RUN
                    ],
                    "pass_locations": [
                        {"line": obs.line, "form": obs.form}
                        for obs in group
                        if obs.status == STATUS_PASS
                    ],
                    "rule": R3,
                }
            )
    return findings


def _is_terminal_field(field: str) -> bool:
    return (
        field.endswith(("_STATUS", "_VERDICT", "_DISPOSITION"))
        or field in {"VERDICT", "FINAL_DISPOSITION", "FULL_PR_LIFECYCLE_CLOSED"}
    )


def _canonical_terminal_value(value: str) -> str:
    value = unicodedata.normalize("NFKC", value.strip()).upper()
    return re.sub(r"[ -]+", "_", value)


def detect_r4(text: str) -> list[dict[str, Any]]:
    values: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for item in _visible_lines(text):
        if item.text is None:
            continue
        match = TERMINAL_FIELD_RE.match(item.text)
        if not match:
            continue
        field = match.group("field")
        raw_value = match.group("value").strip()
        if not _is_terminal_field(field) or "|" in raw_value:
            continue
        values[field].append((_canonical_terminal_value(raw_value), raw_value, item.number))

    findings: list[dict[str, Any]] = []
    for field in sorted(values):
        entries = values[field]
        canonical_values = sorted({entry[0] for entry in entries})
        if len(canonical_values) < 2:
            continue
        grouped_locations: dict[str, list[int]] = defaultdict(list)
        displays: dict[str, str] = {}
        for canonical, raw, line in entries:
            grouped_locations[canonical].append(line)
            displays.setdefault(canonical, raw)
        findings.append(
            {
                "field": field,
                "rule": R4,
                "values": [
                    {
                        "canonical_value": value,
                        "display_value": displays[value],
                        "lines": sorted(grouped_locations[value]),
                    }
                    for value in canonical_values
                ],
            }
        )
    return findings


def _repetition_candidate(line: str | None) -> str | None:
    if line is None:
        return None
    stripped = line.strip()
    if len(stripped) < 8:
        return None
    if re.match(r"^#{1,6}\s+", stripped):
        return None
    if stripped.count("|") >= 2:
        return None
    if re.match(r"^(?:[-*+]\s+|>\s*)", stripped):
        return None
    if re.fullmatch(r"[-*_]{3,}", stripped):
        return None
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", stripped))


def detect_r6(text: str) -> list[dict[str, Any]]:
    visible = _visible_lines(text)
    candidates = [_repetition_candidate(item.text) for item in visible]
    findings: list[dict[str, Any]] = []
    covered: set[int] = set()

    index = 0
    while index < len(candidates):
        value = candidates[index]
        if value is None:
            index += 1
            continue
        end = index + 1
        while end < len(candidates) and candidates[end] == value:
            end += 1
        repeats = end - index
        if repeats >= 8:
            findings.append(
                {
                    "kind": "consecutive_identical_line",
                    "line_end": visible[end - 1].number,
                    "line_start": visible[index].number,
                    "preview": value[:160],
                    "repeats": repeats,
                    "rule": R6,
                    "severity": "ADVISORY",
                }
            )
            covered.update(range(index, end))
        index = end

    index = 0
    while index < len(candidates):
        if index in covered or candidates[index] is None:
            index += 1
            continue
        matched = False
        for block_size in range(2, 5):
            block = candidates[index : index + block_size]
            if len(block) < block_size or any(value is None for value in block) or len(set(block)) < 2:
                continue
            repeats = 1
            cursor = index + block_size
            while candidates[cursor : cursor + block_size] == block:
                repeats += 1
                cursor += block_size
            if repeats >= 5 and not any(position in covered for position in range(index, cursor)):
                findings.append(
                    {
                        "block_lines": block_size,
                        "kind": "consecutive_repeating_block",
                        "line_end": visible[cursor - 1].number,
                        "line_start": visible[index].number,
                        "preview": block,
                        "repeats": repeats,
                        "rule": R6,
                        "severity": "ADVISORY",
                    }
                )
                covered.update(range(index, cursor))
                index = cursor
                matched = True
                break
        if not matched:
            index += 1

    return sorted(findings, key=lambda finding: (finding["line_start"], finding["kind"]))


def analyze_text(text: str, source: str) -> dict[str, Any]:
    r3_findings = detect_r3(text)
    r4_findings = detect_r4(text)
    r6_findings = detect_r6(text)
    total = len(r3_findings) + len(r4_findings) + len(r6_findings)
    return {
        "detector_version": DETECTOR_VERSION,
        "finding_present": total > 0,
        "findings": {R3: r3_findings, R4: r4_findings, R6: r6_findings},
        "semantic_judgement": "NOT_IMPLEMENTED",
        "source": source,
        "summary": {
            R3: len(r3_findings),
            R4: len(r4_findings),
            R6: len(r6_findings),
            "total": total,
        },
    }


def _validate_report_text(text: str, source: str) -> None:
    if "\x00" in text:
        raise AuditInputError(f"unsafe NUL byte in {source}")
    if len(text.encode("utf-8")) > MAX_REPORT_BYTES:
        raise AuditInputError(f"input exceeds {MAX_REPORT_BYTES} bytes: {source}")


def _decode_utf8(data: bytes, source: str) -> str:
    if len(data) > MAX_REPORT_BYTES:
        raise AuditInputError(f"input exceeds {MAX_REPORT_BYTES} bytes: {source}")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AuditInputError(f"unsafe UTF-8 decoding for {source}: {exc}") from exc
    _validate_report_text(text, source)
    return text


def _read_report(path_value: str) -> tuple[str, str]:
    if path_value == "-":
        return _decode_utf8(sys.stdin.buffer.read(MAX_REPORT_BYTES + 1), "<stdin>"), "<stdin>"
    path = Path(path_value)
    try:
        if not path.is_file():
            raise AuditInputError(f"not a readable regular file: {path_value}")
        data = path.read_bytes()
    except OSError as exc:
        raise AuditInputError(f"cannot read {path_value}: {exc}") from exc
    return _decode_utf8(data, path.name), path.name


def _load_json(path: Path) -> Any:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AuditInputError(f"cannot read {path}: {exc}") from exc
    text = _decode_utf8(data, path.name)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AuditInputError(f"invalid JSON in {path}: {exc}") from exc


def _load_corpus(fixtures_dir: Path, expected_path: Path) -> list[dict[str, Any]]:
    if not fixtures_dir.is_dir():
        raise AuditInputError(f"fixtures directory is unreadable: {fixtures_dir}")
    fixture_files = sorted(fixtures_dir.glob("*.json"))
    if not fixture_files:
        raise AuditInputError(f"no JSON fixtures found in {fixtures_dir}")

    cases: list[dict[str, Any]] = []
    for fixture_file in fixture_files:
        payload = _load_json(fixture_file)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
            raise AuditInputError(f"invalid fixture schema: {fixture_file}")
        for case in payload["cases"]:
            if not isinstance(case, dict):
                raise AuditInputError(f"fixture case is not an object: {fixture_file}")
            required = {"id", "rule", "expected_conflict", "provenance", "text", "rationale"}
            if not required.issubset(case):
                raise AuditInputError(f"fixture case missing required fields in {fixture_file}")
            if case["rule"] not in RULES or type(case["expected_conflict"]) is not bool:
                raise AuditInputError(f"invalid rule or label for fixture {case.get('id')}")
            if case["provenance"] not in PROVENANCE_LABELS:
                raise AuditInputError(f"invalid provenance for fixture {case.get('id')}")
            if not isinstance(case["text"], str) or not isinstance(case["rationale"], str):
                raise AuditInputError(f"invalid text or rationale for fixture {case.get('id')}")
            _validate_report_text(case["text"], str(case["id"]))
            cases.append(case)

    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise AuditInputError("duplicate fixture id")

    expected_payload = _load_json(expected_path)
    if not isinstance(expected_payload, dict) or expected_payload.get("schema_version") != 1:
        raise AuditInputError(f"invalid expected-label schema: {expected_path}")
    expected_rows = expected_payload.get("labels")
    if not isinstance(expected_rows, list):
        raise AuditInputError(f"expected labels are missing: {expected_path}")
    expected = {
        row.get("id"): (row.get("rule"), row.get("expected_conflict"), row.get("provenance"))
        for row in expected_rows
        if isinstance(row, dict)
    }
    fixture_labels = {
        case["id"]: (case["rule"], case["expected_conflict"], case["provenance"])
        for case in cases
    }
    if len(expected) != len(expected_rows) or expected != fixture_labels:
        raise AuditInputError("expected labels do not exactly match fixture labels")
    return sorted(cases, key=lambda case: case["id"])


def _evaluate_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        audit = analyze_text(case["text"], case["id"])
        detected = bool(audit["findings"][case["rule"]])
        results.append(
            {
                "correct": detected == case["expected_conflict"],
                "detected": detected,
                "expected_conflict": case["expected_conflict"],
                "id": case["id"],
                "provenance": case["provenance"],
                "rule": case["rule"],
            }
        )
    return results


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_corpus_metrics(fixtures_dir: Path, expected_path: Path) -> dict[str, Any]:
    cases = _load_corpus(fixtures_dir, expected_path)
    first = _evaluate_cases(cases)
    second = _evaluate_cases(cases)
    byte_identical = _canonical_json_bytes(first) == _canonical_json_bytes(second)

    metrics_by_rule: dict[str, dict[str, int]] = {}
    for rule in RULES:
        rows = [row for row in first if row["rule"] == rule]
        metrics_by_rule[rule] = {
            "FN": sum(row["expected_conflict"] and not row["detected"] for row in rows),
            "FP": sum(not row["expected_conflict"] and row["detected"] for row in rows),
            "TN": sum(not row["expected_conflict"] and not row["detected"] for row in rows),
            "TP": sum(row["expected_conflict"] and row["detected"] for row in rows),
            "cases": len(rows),
            "expected_conflict": sum(row["expected_conflict"] for row in rows),
            "expected_non_conflict": sum(not row["expected_conflict"] for row in rows),
        }

    false_positives = [
        {"case_id": row["id"], "rule": row["rule"]}
        for row in first
        if not row["expected_conflict"] and row["detected"]
    ]
    false_negatives = [
        {"case_id": row["id"], "rule": row["rule"]}
        for row in first
        if row["expected_conflict"] and not row["detected"]
    ]
    provenance_counts = {
        label: sum(case["provenance"] == label for case in cases)
        for label in PROVENANCE_LABELS
    }
    all_correct = all(row["correct"] for row in first)
    return {
        "case_results": first,
        "corpus_label_integrity": "PASS",
        "corpus_provenance_counts": provenance_counts,
        "corpus_total": len(cases),
        "detector_version": DETECTOR_VERSION,
        "deterministic_replay": {
            "byte_identical": byte_identical,
            "complete_corpus_replays": 2,
            "status": "PASS" if byte_identical else "FAIL",
        },
        "false_negative_ledger": false_negatives,
        "false_positive_ledger": false_positives,
        "manual_error_classification": (
            "NOT_APPLICABLE_NO_FP_OR_FN"
            if not false_positives and not false_negatives
            else "REQUIRED"
        ),
        "metrics": metrics_by_rule,
        "overall_corpus_result": "PASS" if all_correct and byte_identical else "FAIL",
        "semantic_judgement": "NOT_IMPLEMENTED",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="audit one UTF-8 report")
    audit_parser.add_argument("report", help="report path, or - for stdin")

    corpus_parser = subparsers.add_parser("corpus", help="replay the labelled fixture corpus twice")
    corpus_parser.add_argument("--fixtures", required=True, type=Path)
    corpus_parser.add_argument("--expected", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            text, source = _read_report(args.report)
            payload = analyze_text(text, source)
            exit_code = 0
        else:
            payload = build_corpus_metrics(args.fixtures, args.expected)
            exit_code = 0 if payload["overall_corpus_result"] == "PASS" else 1
        sys.stdout.buffer.write(_canonical_json_bytes(payload))
        return exit_code
    except AuditInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
