#!/usr/bin/env python3
"""Build and verify the de-identified real-sampled R2 corpus.

All source content is loaded from exact Git blobs reachable from the Packet's
pinned base commit. Persisted fixtures contain only sanitized excerpts and
opaque document/version aliases; repository paths never enter the fixtures.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
R1_ROOT = ROOT.parent / "fable-report-structural-audit-r1"
SPEC_PATH = ROOT / "corpus_spec.json"
FIXTURES_DIR = ROOT / "fixtures"
EXPECTED_PATH = ROOT / "expected" / "labels.json"
SNAPSHOT_PATH = ROOT / "metrics.json"

R3 = "R3_PASS_NOT_RUN_CONFLICT"
R4 = "R4_DUPLICATE_TERMINAL_FIELD_CONFLICT"
REAL_PROVENANCE = "REAL_SAMPLED_DEIDENTIFIED"


class VerificationError(Exception):
    """A corpus provenance, sanitization, label, or snapshot error."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise VerificationError(f"git {' '.join(args)} failed: {detail}")
    try:
        return result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise VerificationError(f"unsafe UTF-8 from git {' '.join(args)}: {exc}") from exc


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot load {path.name}: {exc}") from exc


def _load_spec() -> dict[str, Any]:
    payload = _load_json(SPEC_PATH)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise VerificationError("invalid corpus_spec.json schema")
    if not isinstance(payload.get("sources"), list) or not isinstance(payload.get("cases"), list):
        raise VerificationError("corpus_spec.json is missing sources or cases")
    return payload


def _reachable_object_paths(base_commit: str) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for line in _git("rev-list", "--objects", base_commit).splitlines():
        oid, separator, path = line.partition(" ")
        if separator and path:
            mapping.setdefault(oid, set()).add(path)
    return mapping


def _load_sources(spec: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    base_commit = spec.get("authority_base_commit")
    if not isinstance(base_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", base_commit):
        raise VerificationError("invalid authority base commit")
    if _git("cat-file", "-t", base_commit).strip() != "commit":
        raise VerificationError("authority base object is not a commit")

    reachable = _reachable_object_paths(base_commit)
    sources: dict[str, dict[str, Any]] = {}
    document_paths: dict[str, str] = {}
    source_text: dict[str, str] = {}

    for row in spec["sources"]:
        if not isinstance(row, dict):
            raise VerificationError("source row is not an object")
        required = {"source_id", "document_id", "version_id", "blob_oid"}
        if not required.issubset(row):
            raise VerificationError("source row is missing required fields")
        source_id = row["source_id"]
        document_id = row["document_id"]
        oid = row["blob_oid"]
        if source_id in sources or not all(isinstance(row[key], str) for key in required):
            raise VerificationError(f"invalid or duplicate source: {source_id!r}")
        if not re.fullmatch(r"[0-9a-f]{40}", oid):
            raise VerificationError(f"invalid blob OID for {source_id}")
        if _git("cat-file", "-t", oid).strip() != "blob":
            raise VerificationError(f"source {source_id} is not a blob")
        paths = sorted(path for path in reachable.get(oid, set()) if Path(path).name == "report.md")
        if len(paths) != 1:
            raise VerificationError(f"source {source_id} is not uniquely reachable as tracked report.md")
        path = paths[0]
        previous = document_paths.setdefault(document_id, path)
        if previous != path:
            raise VerificationError(f"document alias {document_id} maps to multiple paths")
        if any(other_id != document_id and other_path == path for other_id, other_path in document_paths.items()):
            raise VerificationError(f"tracked report path has multiple document aliases: {document_id}")
        raw = _git("cat-file", "blob", oid)
        if "\x00" in raw:
            raise VerificationError(f"unsafe NUL byte in source {source_id}")
        sources[source_id] = row
        source_text[source_id] = raw

    return sources, source_text


def _deidentify(text: str) -> str:
    text = re.sub(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|xox[baprs]-[A-Za-z0-9-]{12,}|AKIA[0-9A-Z]{16})\b", "<TOKEN>", text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}", "Bearer <TOKEN>", text)
    text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "<PRIVATE_KEY_HEADER>", text)
    text = re.sub(r"\breq_[A-Za-z0-9_-]+\b", "<REQUEST_ID>", text)
    text = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b", "<UUID>", text)
    text = re.sub(r"(?<![A-Za-z0-9])[0-9a-fA-F]{7,40}(?![A-Za-z0-9])", "<REVISION>", text)
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "<EMAIL>", text)
    text = re.sub(r"(?<!\w)(?:/Users|/home)/[^\s`'\"<>]+", "<LOCAL_PATH>", text)
    text = re.sub(r"(?<!\w)~/(?:[^\s`'\"<>]+)", "<LOCAL_PATH>", text)
    text = re.sub(r"(?<![A-Za-z0-9_.-])(?:analysis|fable-method|prompt)/[A-Za-z0-9_.*+?@%=:,/-]+", "<REPO_PATH>", text)
    text = re.sub(r"\bFABLE_[A-Z0-9_]+\b", "TASK_ALIAS", text)
    text = re.sub(r"(?i)Fable", "FRAMEWORK", text)
    text = re.sub(r"(?i)\bAnthropic\b", "PROVIDER", text)
    text = re.sub(r"(?i)\bClaude(?:-desktop|-opus-[0-9]+)?\b", "RUNTIME", text)
    text = re.sub(r"(?i)\bCodex\b", "RUNTIME", text)
    text = re.sub(r"(?i)\bGemini\b", "RUNTIME", text)
    return text


SENSITIVE_TEXT_PATTERNS = {
    "absolute_user_path": re.compile(r"(?<!\w)(?:/Users|/home)/"),
    "home_shorthand": re.compile(r"(?<!\w)~/"),
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "uuid": re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"),
    "request_id": re.compile(r"\breq_[A-Za-z0-9_-]+\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "common_secret": re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|xox[baprs]-[A-Za-z0-9-]{12,}|AKIA[0-9A-Z]{16})\b"),
    "task_identifier": re.compile(r"(?i)FABLE"),
    "runtime_identifier": re.compile(r"(?i)\b(?:Anthropic|Claude|Codex|Gemini)\b"),
}


def _build_cases(spec: dict[str, Any], sources: dict[str, dict[str, Any]], source_text: dict[str, str]) -> list[dict[str, Any]]:
    built: list[dict[str, Any]] = []
    ids: set[str] = set()
    for row in spec["cases"]:
        if not isinstance(row, dict):
            raise VerificationError("case row is not an object")
        required = {"id", "rule", "source_id", "line_start", "line_end", "rationale"}
        if not required.issubset(row):
            raise VerificationError("case row is missing required fields")
        case_id = row["id"]
        source_id = row["source_id"]
        if case_id in ids or not isinstance(case_id, str):
            raise VerificationError(f"duplicate or invalid case ID: {case_id!r}")
        ids.add(case_id)
        if row["rule"] not in {R3, R4} or source_id not in sources:
            raise VerificationError(f"invalid rule or source for {case_id}")
        start = row["line_start"]
        end = row["line_end"]
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise VerificationError(f"invalid line range for {case_id}")
        lines = source_text[source_id].splitlines()
        if end > len(lines):
            raise VerificationError(f"line range exceeds source for {case_id}")
        raw_excerpt = "\n".join(lines[start - 1 : end]) + "\n"
        text = _deidentify(raw_excerpt)
        sensitive_matches = [name for name, pattern in SENSITIVE_TEXT_PATTERNS.items() if pattern.search(text)]
        if sensitive_matches:
            raise VerificationError(f"sensitive text remains in {case_id}: {', '.join(sensitive_matches)}")
        source = sources[source_id]
        built.append(
            {
                "expected_conflict": False,
                "id": case_id,
                "provenance": REAL_PROVENANCE,
                "rationale": row["rationale"],
                "rule": row["rule"],
                "source": {
                    "deidentified_excerpt_sha256": _sha256_text(text),
                    "document_id": source["document_id"],
                    "line_end": end,
                    "line_start": start,
                    "raw_excerpt_sha256": _sha256_text(raw_excerpt),
                    "source_id": source_id,
                    "version_id": source["version_id"],
                },
                "text": text,
            }
        )
    return sorted(built, key=lambda case: case["id"])


def _fixture_payload(cases: list[dict[str, Any]], rule: str) -> dict[str, Any]:
    return {"cases": [case for case in cases if case["rule"] == rule], "schema_version": 1}


def _labels_payload(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "labels": [
            {
                "expected_conflict": case["expected_conflict"],
                "id": case["id"],
                "provenance": case["provenance"],
                "rule": case["rule"],
            }
            for case in cases
        ],
        "schema_version": 1,
    }


def _load_detector() -> Any:
    detector_path = R1_ROOT / "audit_report.py"
    module_spec = importlib.util.spec_from_file_location("audit_report_r1", detector_path)
    if module_spec is None or module_spec.loader is None:
        raise VerificationError("cannot load R1 detector")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    return module


def _exact_rule_metrics(metrics: dict[str, Any], rule: str) -> dict[str, int]:
    row = metrics["metrics"][rule]
    return {key: int(row[key]) for key in ("TP", "FP", "TN", "FN", "cases", "expected_conflict", "expected_non_conflict")}


def _sum_metrics(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    return {key: left[key] + right[key] for key in left}


def _build_metrics(spec: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    detector = _load_detector()
    synthetic = detector.build_corpus_metrics(R1_ROOT / "fixtures", R1_ROOT / "expected" / "labels.json")
    real = detector.build_corpus_metrics(FIXTURES_DIR, EXPECTED_PATH)

    expected_synthetic = {
        R3: {"TP": 20, "FP": 0, "TN": 20, "FN": 0, "cases": 40, "expected_conflict": 20, "expected_non_conflict": 20},
        R4: {"TP": 8, "FP": 0, "TN": 8, "FN": 0, "cases": 16, "expected_conflict": 8, "expected_non_conflict": 8},
        detector.R6: {"TP": 6, "FP": 0, "TN": 6, "FN": 0, "cases": 12, "expected_conflict": 6, "expected_non_conflict": 6},
    }
    synthetic_metrics = {rule: _exact_rule_metrics(synthetic, rule) for rule in expected_synthetic}
    if synthetic_metrics != expected_synthetic or synthetic["corpus_total"] != 68 or synthetic["overall_corpus_result"] != "PASS":
        raise VerificationError("original R1 synthetic regression failed")

    real_metrics = {rule: _exact_rule_metrics(real, rule) for rule in (R3, R4)}
    expected_real = {
        R3: {"TP": 0, "FP": 0, "TN": 24, "FN": 0, "cases": 24, "expected_conflict": 0, "expected_non_conflict": 24},
        R4: {"TP": 0, "FP": 0, "TN": 12, "FN": 0, "cases": 12, "expected_conflict": 0, "expected_non_conflict": 12},
    }
    if real_metrics != expected_real or real["corpus_total"] != 36 or real["overall_corpus_result"] != "PASS":
        raise VerificationError("real R2 corpus replay failed")

    used_source_ids = {case["source"]["source_id"] for case in cases}
    used_documents = {case["source"]["document_id"] for case in cases}
    if len(used_documents) < 3:
        raise VerificationError("fewer than three distinct real source documents")

    combined = {
        rule: _sum_metrics(synthetic_metrics[rule], real_metrics[rule])
        for rule in (R3, R4)
    }
    detector_bytes = (R1_ROOT / "audit_report.py").read_bytes()
    return {
        "audit_status": "INSUFFICIENT_REAL_CORPUS",
        "combined_descriptive_metrics": {
            "caveat": "DESCRIPTIVE_SUM_ONLY_NOT_A_PRODUCTION_FALSE_POSITIVE_RATE",
            "metrics": combined,
        },
        "detector": {
            "modified_for_r2": False,
            "sha256": hashlib.sha256(detector_bytes).hexdigest(),
            "version": detector.DETECTOR_VERSION,
        },
        "final_disposition": "NEEDS_MORE_REAL_CORPUS",
        "original_r1_regression": "PASS",
        "real_corpus": {
            "case_total": len(cases),
            "conflict_class_available": False,
            "false_negative_ledger": real["false_negative_ledger"],
            "false_positive_ledger": real["false_positive_ledger"],
            "metrics": real_metrics,
            "provenance": "TRACKED_GIT_ONLY",
            "source_documents": len(used_documents),
            "source_versions": len(used_source_ids),
        },
        "replay": {
            "real_internal_replay": real["deterministic_replay"],
            "synthetic_internal_replay": synthetic["deterministic_replay"],
        },
        "semantic_judgement": "NOT_IMPLEMENTED",
        "synthetic_r1_metrics": synthetic_metrics,
    }


def _verify_retained_fixtures(cases: list[dict[str, Any]]) -> None:
    expected = {
        FIXTURES_DIR / "r3_real.json": _fixture_payload(cases, R3),
        FIXTURES_DIR / "r4_real.json": _fixture_payload(cases, R4),
        EXPECTED_PATH: _labels_payload(cases),
    }
    for path, payload in expected.items():
        retained = _load_json(path)
        if retained != payload:
            raise VerificationError(f"retained artifact does not match tracked-source derivation: {path.name}")


def _render_artifact(name: str, cases: list[dict[str, Any]]) -> str:
    if name == "r3":
        return _canonical_json(_fixture_payload(cases, R3))
    if name == "r4":
        return _canonical_json(_fixture_payload(cases, R4))
    if name == "labels":
        return _canonical_json(_labels_payload(cases))
    raise VerificationError(f"unknown render artifact: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    render = subparsers.add_parser("render", help="render one derived fixture artifact")
    render.add_argument("artifact", choices=("r3", "r4", "labels"))
    check = subparsers.add_parser("check", help="verify provenance, fixtures, detector replay, and optional snapshot")
    check.add_argument("--snapshot", action="store_true", help="require metrics.json to match computed metrics")
    args = parser.parse_args(argv)

    try:
        spec = _load_spec()
        sources, source_text = _load_sources(spec)
        cases = _build_cases(spec, sources, source_text)
        if args.command == "render":
            sys.stdout.write(_render_artifact(args.artifact, cases))
            return 0
        _verify_retained_fixtures(cases)
        metrics = _build_metrics(spec, cases)
        if args.snapshot and _load_json(SNAPSHOT_PATH) != metrics:
            raise VerificationError("metrics.json does not match computed metrics")
        sys.stdout.write(_canonical_json(metrics))
        return 0
    except VerificationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
