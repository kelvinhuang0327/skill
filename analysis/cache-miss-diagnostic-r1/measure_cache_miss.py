#!/usr/bin/env python3
"""FABLE_CACHE_MISS_DIAGNOSTIC_ATTRIBUTION_R1 deterministic measurement script.

Uses only existing local transcript evidence for the same target session
("S1") identified by the prior two tasks. Re-derives S1 internally by the
identical selection rule; its real session id is never printed or persisted.

CORE FINDING THIS SCRIPT ESTABLISHES: a single Claude Code JSONL transcript
can (and for this session's material events, does) log ONE real API request
as MULTIPLE separate "assistant" records - one per content block (thinking /
text / tool_use) - each carrying an identical copy of that one request's
`usage` and `diagnostics`. Treating each JSONL line as an independent
request (as both prior tasks in this chain did, since neither was asked to
check this) inflates SUM-based token totals and manufactures the appearance
of repeated "intra-burst" cache misses seconds apart, when the underlying
diagnostic evidence shows there is exactly one real request - and one real
cache-miss event - per burst.

This script performs the required duplicate-record guard using `requestId`
before any cache-reuse analysis, then rebuilds the reuse-ratio timeline on
the deduplicated request sequence, and cross-references the API's own
`message.diagnostics.cache_miss_reason` field (a direct, first-party signal
this task's schema probe is required to check for, not assume).

Usage:
    python3 measure_cache_miss.py --cutoff <ISO8601-UTC> \
        --exclude-session <this-measurement-session-id> \
        [--out metrics.json]

Two runs with the same arguments must produce byte-identical output.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime

TRANSCRIPT_DIR = os.path.expanduser(
    "~/.claude/projects/-Users-kelvin-VibeCoding-WorkSpace-skill"
)

NORMAL_REUSE_MIN_RATIO = 0.80
MATERIAL_LOSS_MAX_RATIO = 0.50
MATERIAL_LOSS_MIN_CREATION_FRACTION_OF_PRIOR = 0.50
MECHANISM_DOMINANCE_THRESHOLD = 0.70


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_lines(path, cutoff):
    out = []
    with open(path, "r", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = parse_ts(d.get("timestamp"))
            if t is not None and t > cutoff:
                continue
            out.append(d)
    return out


def context_high_water_for_session(lines):
    best = 0
    any_usage = False
    for d in lines:
        if d.get("isSidechain"):
            continue
        m = d.get("message") or {}
        if m.get("role") != "assistant":
            continue
        u = m.get("usage")
        if not isinstance(u, dict):
            continue
        v = (u.get("input_tokens", 0) or 0) + \
            (u.get("cache_read_input_tokens", 0) or 0) + \
            (u.get("cache_creation_input_tokens", 0) or 0)
        best = max(best, v)
        any_usage = True
    return best, any_usage


def find_target_session(cutoff, excluded):
    best = None
    for path in sorted(glob.glob(os.path.join(TRANSCRIPT_DIR, "*.jsonl"))):
        sid = os.path.splitext(os.path.basename(path))[0]
        if sid in excluded:
            continue
        lines = load_lines(path, cutoff)
        if not lines:
            continue
        chw, any_usage = context_high_water_for_session(lines)
        if any_usage and (best is None or chw > best[1]):
            best = (path, chw, lines)
    return best


SCHEMA_PROBE_FIELDS = {
    "message.diagnostics": lambda d: (d.get("message") or {}).get("diagnostics", "__MISSING__"),
    "message.diagnostics.cache_miss_reason": lambda d: ((d.get("message") or {}).get("diagnostics") or {}).get("cache_miss_reason", "__MISSING__") if isinstance((d.get("message") or {}).get("diagnostics"), dict) else "__MISSING__",
    "requestId": lambda d: d.get("requestId", "__MISSING__"),
    "entrypoint": lambda d: d.get("entrypoint", "__MISSING__"),
    "version": lambda d: d.get("version", "__MISSING__"),
    "message.model": lambda d: (d.get("message") or {}).get("model", "__MISSING__"),
    "usage.service_tier": lambda d: ((d.get("message") or {}).get("usage") or {}).get("service_tier", "__MISSING__"),
    "usage.speed": lambda d: ((d.get("message") or {}).get("usage") or {}).get("speed", "__MISSING__"),
    "effort": lambda d: d.get("effort", "__MISSING__"),
    "usage.cache_creation.ephemeral_1h_input_tokens": lambda d: (((d.get("message") or {}).get("usage") or {}).get("cache_creation") or {}).get("ephemeral_1h_input_tokens", "__MISSING__"),
    "usage.cache_creation.ephemeral_5m_input_tokens": lambda d: (((d.get("message") or {}).get("usage") or {}).get("cache_creation") or {}).get("ephemeral_5m_input_tokens", "__MISSING__"),
    "usage.iterations": lambda d: ((d.get("message") or {}).get("usage") or {}).get("iterations", "__MISSING__"),
    "attributionSkill": lambda d: d.get("attributionSkill", "__MISSING__"),
    "message.stop_reason": lambda d: (d.get("message") or {}).get("stop_reason", "__MISSING__"),
    "parentUuid": lambda d: d.get("parentUuid", "__MISSING__"),
    "uuid": lambda d: d.get("uuid", "__MISSING__"),
}


def run_schema_probe(assistant_lines):
    result = {}
    for label, fn in SCHEMA_PROBE_FIELDS.items():
        present = present_null = absent = 0
        for d in assistant_lines:
            try:
                v = fn(d)
            except Exception:
                v = "__MISSING__"
            if v == "__MISSING__":
                absent += 1
            elif v is None:
                present_null += 1
            else:
                present += 1
        if present == 0 and present_null == 0:
            status = "ABSENT"
        elif present == 0:
            status = "PRESENT_NULL"
        else:
            status = "PRESENT"
        result[label] = {"status": status, "present": present, "present_null": present_null, "absent": absent}
    # Fields the packet asks about that this schema does not carry at all,
    # explicitly enumerated as NOT SAFELY ATTRIBUTABLE rather than silently
    # skipped, per the packet's required PRESENT/PRESENT_NULL/ABSENT/
    # NOT-SAFELY-ATTRIBUTABLE taxonomy.
    result["tool_definitions_identity_structural"] = {"status": "NOT SAFELY ATTRIBUTABLE",
        "reason": "no per-request tool-schema payload is logged in this transcript format; only tool_use CALLS are logged, not the tool definitions offered to the model"}
    result["system_context_identity_structural"] = {"status": "NOT SAFELY ATTRIBUTABLE",
        "reason": "the rendered system prompt is not logged as transcript content in this format (confirmed in the prior RCA task); only message.diagnostics.cache_miss_reason.type can directly report a system-context change, which is a first-party signal, not a locally reconstructed fingerprint"}
    result["retry_or_error_fields"] = {"status": "NOT SAFELY ATTRIBUTABLE",
        "reason": "no retry count, error code, or HTTP-transport field is logged on assistant records in this schema; message.stop_reason and requestId are the only request-outcome/identity signals available"}
    result["transport_client_metadata"] = {"status": "PRESENT (partial)",
        "reason": "entrypoint and version are present and stable per request; no lower-level transport (TLS/connection/retry-transport) metadata is logged"}
    return result


def build_deduplicated_requests(lines):
    """Group main-thread assistant usage-bearing lines by requestId. Verify
    (not assume) that usage and structural fields are identical within each
    group, then return one representative record per request, in
    chronological order by first occurrence. This is the duplicate-record
    guard required before any cache analysis."""
    tool_id_to_name = {}
    order = []
    groups = {}

    for d in lines:
        if d.get("isSidechain"):
            continue
        m = d.get("message") or {}
        role = m.get("role")
        content = m.get("content")
        if role == "assistant":
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tool_id_to_name[b.get("id")] = b.get("name")
            usage = m.get("usage")
            if isinstance(usage, dict):
                rid = d.get("requestId")
                if rid not in groups:
                    groups[rid] = []
                    order.append(rid)
                groups[rid].append(d)

    mismatches = {}
    field_getters = {
        "usage_key": lambda d: (
            d["message"]["usage"].get("input_tokens"),
            d["message"]["usage"].get("cache_creation_input_tokens"),
            d["message"]["usage"].get("cache_read_input_tokens"),
            d["message"]["usage"].get("output_tokens"),
        ),
        "entrypoint": lambda d: d.get("entrypoint"),
        "version": lambda d: d.get("version"),
        "model": lambda d: d["message"].get("model"),
        "effort": lambda d: d.get("effort"),
        "attribution_skill": lambda d: d.get("attributionSkill"),
        "service_tier": lambda d: d["message"]["usage"].get("service_tier"),
        "diagnostics": lambda d: json.dumps(d["message"].get("diagnostics"), sort_keys=True, default=str),
    }
    for rid, members in groups.items():
        for fname, getter in field_getters.items():
            vals = set(getter(d) for d in members)
            if len(vals) > 1:
                mismatches.setdefault(rid, []).append(fname)

    requests = []
    for rid in order:
        members = sorted(groups[rid], key=lambda d: parse_ts(d.get("timestamp")) or datetime.min)
        rep = members[0]
        usage = rep["message"]["usage"]
        cd = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
        diag = rep["message"].get("diagnostics")
        cache_miss_type = None
        cache_missed_tokens = None
        if isinstance(diag, dict):
            cmr = diag.get("cache_miss_reason")
            if isinstance(cmr, dict):
                cache_miss_type = cmr.get("type")
                cache_missed_tokens = cmr.get("cache_missed_input_tokens")
        requests.append({
            "request_line_count": len(members),
            "timestamp": parse_ts(rep.get("timestamp")),
            "uncached_input_tokens": usage.get("input_tokens", 0) or 0,
            "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
            "output_tokens": usage.get("output_tokens", 0) or 0,
            "entrypoint": rep.get("entrypoint"),
            "version": rep.get("version"),
            "model": rep["message"].get("model"),
            "effort": rep.get("effort"),
            "attribution_skill": rep.get("attributionSkill"),
            "service_tier": usage.get("service_tier"),
            "ephemeral_1h_tokens": cd.get("ephemeral_1h_input_tokens"),
            "ephemeral_5m_tokens": cd.get("ephemeral_5m_input_tokens"),
            "cache_miss_reason_type": cache_miss_type,
            "cache_missed_input_tokens": cache_missed_tokens,
            "content_block_types_in_request": [
                b.get("type") for m2 in members
                for b in (m2["message"].get("content") or [])
                if isinstance(b, dict)
            ],
        })

    inflation = {
        "distinct_requests": len(requests),
        "raw_line_count": sum(r["request_line_count"] for r in requests),
        "raw_summed_cache_creation": sum(
            groups[rid][0]["message"]["usage"].get("cache_creation_input_tokens", 0) or 0
            for rid in order for _ in groups[rid]
        ),
        "dedup_summed_cache_creation": sum(r["cache_creation_tokens"] for r in requests),
        "request_line_count_distribution": {},
        "mismatched_requestId_groups": mismatches,
    }
    for r in requests:
        k = str(r["request_line_count"])
        inflation["request_line_count_distribution"][k] = inflation["request_line_count_distribution"].get(k, 0) + 1

    return requests, inflation


def classify_and_annotate(requests):
    total_cache_creation = sum(r["cache_creation_tokens"] for r in requests)
    for i, r in enumerate(requests):
        total_input_context = r["uncached_input_tokens"] + r["cache_creation_tokens"] + r["cache_read_tokens"]
        r["total_input_context"] = total_input_context
        if i == 0:
            r["prior_context"] = None
            r["prior_prefix_reuse_ratio"] = None
            r["time_gap_seconds_from_previous_request"] = None
            r["reuse_state"] = "INITIAL_REQUEST_NO_PRIOR"
            r["entrypoint_changed"] = "NOT MEASURABLE"
            r["version_changed"] = "NOT MEASURABLE"
            r["model_changed"] = "NOT MEASURABLE"
            r["effort_changed"] = "NOT MEASURABLE"
            r["service_tier_changed"] = "NOT MEASURABLE"
            r["skill_attribution_changed"] = "NOT MEASURABLE"
            continue
        prev = requests[i - 1]
        prior_context = prev["uncached_input_tokens"] + prev["cache_creation_tokens"] + prev["cache_read_tokens"]
        r["prior_context"] = prior_context
        r["prior_prefix_reuse_ratio"] = round(r["cache_read_tokens"] / prior_context, 4) if prior_context else None
        if r["timestamp"] and prev["timestamp"]:
            r["time_gap_seconds_from_previous_request"] = (r["timestamp"] - prev["timestamp"]).total_seconds()
        else:
            r["time_gap_seconds_from_previous_request"] = None

        ratio = r["prior_prefix_reuse_ratio"]
        if ratio is None:
            r["reuse_state"] = "NOT_MEASURABLE"
        elif ratio >= NORMAL_REUSE_MIN_RATIO:
            r["reuse_state"] = "NORMAL_REUSE_CANDIDATE"
        elif ratio < MATERIAL_LOSS_MAX_RATIO and prior_context and \
                r["cache_creation_tokens"] >= MATERIAL_LOSS_MIN_CREATION_FRACTION_OF_PRIOR * prior_context:
            r["reuse_state"] = "MATERIAL_PREFIX_LOSS_CANDIDATE"
        else:
            r["reuse_state"] = "PARTIAL_REUSE"

        r["entrypoint_changed"] = "YES" if r["entrypoint"] != prev["entrypoint"] else "NO"
        r["version_changed"] = "YES" if r["version"] != prev["version"] else "NO"
        r["model_changed"] = "YES" if r["model"] != prev["model"] else "NO"
        r["effort_changed"] = "YES" if r["effort"] != prev["effort"] else "NO"
        r["service_tier_changed"] = "YES" if r["service_tier"] != prev["service_tier"] else "NO"
        r["skill_attribution_changed"] = "YES" if r["attribution_skill"] != prev["attribution_skill"] else "NO"

    return total_cache_creation


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", required=True)
    ap.add_argument("--exclude-session", action="append", default=[])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cutoff = parse_ts(args.cutoff)
    if cutoff is None:
        print(f"FATAL: bad --cutoff {args.cutoff!r}", file=sys.stderr)
        sys.exit(2)

    excluded = set(args.exclude_session)
    found = find_target_session(cutoff, excluded)
    if found is None:
        print("FATAL: no eligible target session found", file=sys.stderr)
        sys.exit(2)
    _target_path, target_chw, lines = found

    assistant_lines = [
        d for d in lines
        if not d.get("isSidechain")
        and (d.get("message") or {}).get("role") == "assistant"
        and isinstance((d.get("message") or {}).get("usage"), dict)
    ]

    schema_probe = run_schema_probe(assistant_lines)
    requests, dedup_guard = build_deduplicated_requests(lines)
    total_cache_creation = classify_and_annotate(requests)

    by_state_creation = {}
    for r in requests:
        by_state_creation[r["reuse_state"]] = by_state_creation.get(r["reuse_state"], 0) + r["cache_creation_tokens"]

    def share(state):
        return by_state_creation.get(state, 0) / total_cache_creation if total_cache_creation else 0.0

    normal_share = share("NORMAL_REUSE_CANDIDATE")
    material_share = share("MATERIAL_PREFIX_LOSS_CANDIDATE")

    if total_cache_creation == 0:
        mechanism = "NOT_DETERMINABLE"
    elif normal_share >= MECHANISM_DOMINANCE_THRESHOLD:
        mechanism = "NORMAL_INCREMENTAL_REUSE"
    elif material_share >= MECHANISM_DOMINANCE_THRESHOLD:
        mechanism = "PREFIX_REBUILD_DOMINANT"
    else:
        mechanism = "MIXED_CACHE_BEHAVIOR"

    # Anonymized material/control mapping
    material_indices = [i for i, r in enumerate(requests) if r["reuse_state"] == "MATERIAL_PREFIX_LOSS_CANDIDATE"]
    control_indices = [i for i, r in enumerate(requests) if r["reuse_state"] == "NORMAL_REUSE_CANDIDATE"]

    material_events = []
    for k, i in enumerate(material_indices, start=1):
        r = requests[i]
        prev_r = requests[i - 1] if i > 0 else None
        next_r = requests[i + 1] if i + 1 < len(requests) else None
        material_events.append({
            "label": f"M{k}",
            "request_line_count": r["request_line_count"],
            "content_block_types_in_request": r["content_block_types_in_request"],
            "cache_creation_tokens": r["cache_creation_tokens"],
            "cache_read_tokens": r["cache_read_tokens"],
            "prior_context": r["prior_context"],
            "prior_prefix_reuse_ratio": r["prior_prefix_reuse_ratio"],
            "time_gap_seconds_from_previous_request": r["time_gap_seconds_from_previous_request"],
            "time_gap_exceeds_3600s": (r["time_gap_seconds_from_previous_request"] or 0) > 3600,
            "cache_miss_reason_type": r["cache_miss_reason_type"],
            "cache_missed_input_tokens": r["cache_missed_input_tokens"],
            "entrypoint_changed": r["entrypoint_changed"],
            "version_changed": r["version_changed"],
            "model_changed": r["model_changed"],
            "effort_changed": r["effort_changed"],
            "service_tier_changed": r["service_tier_changed"],
            "skill_attribution_changed": r["skill_attribution_changed"],
            "ephemeral_1h_tokens": r["ephemeral_1h_tokens"],
            "ephemeral_5m_tokens": r["ephemeral_5m_tokens"],
            "entry_transition": {
                "from_reuse_state": prev_r["reuse_state"] if prev_r else None,
                "from_cache_miss_reason_type": prev_r["cache_miss_reason_type"] if prev_r else None,
            },
            "recovery_transition": {
                "to_reuse_state": next_r["reuse_state"] if next_r else None,
                "to_cache_read_tokens": next_r["cache_read_tokens"] if next_r else None,
                "to_prior_prefix_reuse_ratio": next_r["prior_prefix_reuse_ratio"] if next_r else None,
            },
        })

    control_sample = []
    for k, i in enumerate(control_indices, start=1):
        r = requests[i]
        control_sample.append({
            "label": f"C{k}",
            "cache_creation_tokens": r["cache_creation_tokens"],
            "prior_prefix_reuse_ratio": r["prior_prefix_reuse_ratio"],
            "time_gap_seconds_from_previous_request": r["time_gap_seconds_from_previous_request"],
            "time_gap_exceeds_3600s": (r["time_gap_seconds_from_previous_request"] or 0) > 3600,
            "cache_miss_reason_type": r["cache_miss_reason_type"],
            "entrypoint_changed": r["entrypoint_changed"],
            "version_changed": r["version_changed"],
            "model_changed": r["model_changed"],
            "effort_changed": r["effort_changed"],
            "skill_attribution_changed": r["skill_attribution_changed"],
        })

    cache_miss_reason_counts_material = {}
    for r in requests:
        if r["reuse_state"] == "MATERIAL_PREFIX_LOSS_CANDIDATE":
            k = r["cache_miss_reason_type"] or "null"
            cache_miss_reason_counts_material[k] = cache_miss_reason_counts_material.get(k, 0) + 1
    cache_miss_reason_counts_control = {}
    for r in requests:
        if r["reuse_state"] in ("NORMAL_REUSE_CANDIDATE", "INITIAL_REQUEST_NO_PRIOR"):
            k = r["cache_miss_reason_type"] or "null"
            cache_miss_reason_counts_control[k] = cache_miss_reason_counts_control.get(k, 0) + 1

    controls_long_gap = [
        r for idx, r in enumerate(requests)
        if r["reuse_state"] in ("NORMAL_REUSE_CANDIDATE", "PARTIAL_REUSE")
        and (r["time_gap_seconds_from_previous_request"] or 0) > 3600
    ]
    controls_long_gap_with_miss = [r for r in controls_long_gap if r["cache_miss_reason_type"] is not None]
    controls_skill_change = [
        r for r in requests
        if r["reuse_state"] in ("NORMAL_REUSE_CANDIDATE", "PARTIAL_REUSE")
        and r["skill_attribution_changed"] == "YES"
    ]
    controls_skill_change_with_miss = [r for r in controls_skill_change if r["cache_miss_reason_type"] is not None]

    counterexample_checks = {
        "controls_with_gap_over_3600s_count": len(controls_long_gap),
        "controls_with_gap_over_3600s_that_show_a_cache_miss": len(controls_long_gap_with_miss),
        "controls_with_gap_over_3600s_with_NO_miss_i.e._counterexamples_to_TTL_theory": len(controls_long_gap) - len(controls_long_gap_with_miss),
        "controls_with_skill_attribution_change_count": len(controls_skill_change),
        "controls_with_skill_attribution_change_that_show_a_cache_miss": len(controls_skill_change_with_miss),
        "material_events_with_gap_over_3600s": sum(1 for m in material_events if m["time_gap_exceeds_3600s"]),
        "material_events_with_skill_attribution_change": sum(1 for m in material_events if m["skill_attribution_changed"] == "YES"),
    }

    models_seen = sorted({r["model"] for r in requests if r["model"] is not None})
    entrypoints_seen = sorted({r["entrypoint"] for r in requests if r["entrypoint"] is not None})
    versions_seen = sorted({r["version"] for r in requests if r["version"] is not None})
    efforts_seen = sorted({r["effort"] for r in requests if r["effort"] is not None})
    service_tiers_seen = sorted({r["service_tier"] for r in requests if r["service_tier"] is not None})

    report = {
        "measurement_cutoff_utc": args.cutoff,
        "target_session_label": "S1",
        "target_context_high_water_tokens": target_chw,
        "schema_probe": schema_probe,
        "duplicate_record_guard": dedup_guard,
        "deduplicated_request_count": len(requests),
        "raw_jsonl_assistant_line_count": len(assistant_lines),
        "total_cache_creation_tokens_deduplicated": total_cache_creation,
        "cache_creation_share_by_reuse_state_deduplicated": {
            k: round(v, 4) for k, v in {
                "NORMAL_REUSE_CANDIDATE": normal_share,
                "MATERIAL_PREFIX_LOSS_CANDIDATE": material_share,
                "PARTIAL_REUSE": share("PARTIAL_REUSE"),
                "INITIAL_REQUEST_NO_PRIOR": share("INITIAL_REQUEST_NO_PRIOR"),
            }.items()
        },
        "cache_churn_mechanism_classification_deduplicated": mechanism,
        "material_request_count": len(material_indices),
        "control_request_count": len(control_indices),
        "cache_miss_reason_by_material_event": cache_miss_reason_counts_material,
        "cache_miss_reason_by_control": cache_miss_reason_counts_control,
        "counterexample_checks": counterexample_checks,
        "models_seen": models_seen,
        "entrypoints_seen": entrypoints_seen,
        "versions_seen": versions_seen,
        "efforts_seen": efforts_seen,
        "service_tiers_seen": service_tiers_seen,
        "material_events": material_events,
        "control_sample": control_sample,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main()
