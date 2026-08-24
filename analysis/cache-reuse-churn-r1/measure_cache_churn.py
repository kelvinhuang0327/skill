#!/usr/bin/env python3
"""FABLE_CACHE_REUSE_CHURN_RCA_R1 deterministic measurement script.

Distinguishes normal append-only cache growth from material cache-prefix
invalidation/rebuild in the single highest-CONTEXT_HIGH_WATER session
identified by the prior FABLE_TOKEN_COST_ATTRIBUTION_R1 task ("S1").

The target session is RE-DERIVED internally by the same selection rule used
in analysis/token-attribution-r1/measure.py (top session by
CONTEXT_HIGH_WATER, excluding the session performing measurement, under the
same frozen cutoff). Its real session id is never printed to stdout, written
to any output file, or otherwise persisted by this script - only the label
"S1" appears in any report-facing structure.

CORRECTION (FABLE_REQUEST_LEVEL_MEASUREMENT_CORRECTION_R1): this script
originally treated every usage-bearing assistant JSONL record as one model
call ("turn"). FABLE_CACHE_MISS_DIAGNOSTIC_ATTRIBUTION_R1 proved a single API
response with multiple content blocks (thinking/text/tool_use) is logged as
multiple JSONL records sharing one requestId, each carrying an identical
copy of that request's `usage`. Comparing "prior turn" usage between two such
duplicate records (which are the same request, not consecutive requests)
manufactured spurious near-zero reuse ratios - the exact shape of the "three
repeated rebuild bursts" this script originally reported. `build_turns` is
replaced by `build_deduplicated_turns_and_events`, which applies the same
requestId duplicate-record guard proven in cache-miss-diagnostic-r1 before
building the turn sequence; every downstream computation (reuse-state
classification, burst detection, material-event selection, tool-result
association, time-gap buckets) is unchanged and now runs on real requests.

Usage:
    python3 measure_cache_churn.py --cutoff <ISO8601-UTC> \
        --exclude-session <this-measurement-session-id> \
        [--out metrics.json]

Two runs with the same --cutoff and --exclude-session must produce
byte-identical output.
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

# Pre-declared thresholds - frozen before this script was run against the
# target session's real numbers. Do not tune these after seeing results.
NORMAL_REUSE_MIN_RATIO = 0.80
MATERIAL_LOSS_MAX_RATIO = 0.50
MATERIAL_LOSS_MIN_CREATION_FRACTION_OF_PRIOR = 0.50
MATERIAL_EVENT_COVERAGE = 0.80
MECHANISM_DOMINANCE_THRESHOLD = 0.70
COMPACTION_CANDIDATE_DROP_RATIO = 0.20  # total context drops below 20% of prior turn's

# Repeated-rebuild burst detector: consecutive turns whose cache_creation
# size is nearly identical AND which have near-zero intervening tool/subagent
# bytes are flagged as one burst. This tests whether a rebuild is driven by
# NEW content (would show intervening bytes and a differing size) or is a
# same-shaped repeat with nothing materially new appended.
#
# NOTE ON METHOD HONESTY: these two specific numbers (2% size tolerance, byte
# cutoff) were fixed only AFTER an initial manual inspection of one instance
# (turns 332-334) suggested the pattern existed - unlike the dominance
# thresholds inherited from the prior task, they were not frozen blind. To
# avoid quietly curve-fitting a threshold to one example, both a STRICT
# variant (matching exactly what that first instance showed: 0 intervening
# bytes) and a LOOSE variant (a round, clearly-conservative 5000-byte
# allowance, i.e. under 1% of the smallest rebuild event's own size, plus
# allowing the session's cold-start turn to anchor a burst) are computed and
# reported side by side. Neither replaces the other.
BURST_SIZE_TOLERANCE = 0.02  # 2% relative difference in cache_creation_tokens
BURST_VARIANTS = [
    {"name": "STRICT", "max_intervening_bytes": 0, "allow_initial_anchor": False},
    {"name": "LOOSE", "max_intervening_bytes": 5000, "allow_initial_anchor": True},
]


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
    """Re-derive the S1 target using the identical rule as the prior
    attribution task: top session by CONTEXT_HIGH_WATER, main-thread only,
    excluding the measuring session. Returns (path, chw); path is used only
    internally by this process and is never written to any output."""
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


def block_text_bytes(block):
    if isinstance(block, dict):
        if isinstance(block.get("text"), str):
            return len(block["text"].encode("utf-8"))
        if "input" in block:
            return len(json.dumps(block["input"], ensure_ascii=False, default=str).encode("utf-8"))
        if "content" in block:
            return content_bytes(block["content"])
        return len(json.dumps(block, ensure_ascii=False, default=str).encode("utf-8"))
    if isinstance(block, str):
        return len(block.encode("utf-8"))
    return 0


def content_bytes(content):
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    if isinstance(content, list):
        return sum(block_text_bytes(b) for b in content)
    if isinstance(content, dict):
        return len(json.dumps(content, ensure_ascii=False, default=str).encode("utf-8"))
    return 0


def is_subagent_result(tur):
    return isinstance(tur, dict) and "agentId" in tur and "totalToolUseCount" in tur


def build_deduplicated_turns_and_events(lines):
    """Duplicate-record guard (required before any cache-reuse analysis),
    then walk one session's lines in file order to produce:
      turns: list of dicts, one per unique requestId (a real model call),
        chronological order - same shape as the pre-correction per-line
        "turn" dicts, so every downstream computation is unchanged.
      events_between: parallel list; events_between[i] = list of
        (tool_name, bytes, is_subagent) tool_result events that occurred
        strictly between request i-1 and request i (events_between[0] =
        events before the first request). A multi-line request's own split
        lines never contain a tool_result of their own (verified: a
        tool_result can only follow a fully-emitted tool_use, so it cannot
        interleave inside one response's own split lines); a bucket opens
        only on a requestId's first occurrence.
      guard: USAGE_RECORD_COUNT / UNIQUE_REQUEST_COUNT /
        DUPLICATED_REQUEST_RECORD_COUNT / MISSING_REQUEST_ID_COUNT /
        CONFLICTING_USAGE_WITHIN_REQUEST_COUNT / NONCONTIGUOUS_REQUESTID_GROUPS
        (the last is a direct check of the interleaving assumption above,
        not an assumption itself).

    A record with no requestId is treated as its own singleton group (never
    merged with another record), the same conservative default used in
    analysis/token-attribution-r1/measure.py.
    """
    tool_id_to_name = {}
    order = []
    groups = {}
    usage_line_positions = {}
    events_between = [[]]  # bucket 0 = before first request
    usage_line_index = -1

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
                usage_line_index += 1
                rid = d.get("requestId")
                key = rid if rid is not None else ("__MISSING_REQUEST_ID__", id(d))
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                    events_between.append([])
                    usage_line_positions[key] = []
                groups[key].append(d)
                usage_line_positions[key].append(usage_line_index)
                continue  # this line's own content has no tool_result to log

        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                tool_use_id = b.get("tool_use_id")
                name = tool_id_to_name.get(tool_use_id, "UNKNOWN")
                tur = d.get("toolUseResult")
                subagent = is_subagent_result(tur)
                if subagent:
                    nbytes = content_bytes(tur.get("content"))
                else:
                    nbytes = content_bytes(b.get("content"))
                    if nbytes == 0 and isinstance(tur, dict):
                        nbytes = len((tur.get("stdout") or "").encode("utf-8")) + \
                                 len((tur.get("stderr") or "").encode("utf-8"))
                events_between[-1].append((name, nbytes, subagent))

    # events_between has len(order)+1 buckets; drop the trailing empty bucket
    # (events after the last request are not "between" two model calls).
    events_between = events_between[:len(order) + 1]

    noncontiguous = 0
    for key in order:
        positions = usage_line_positions[key]
        if positions[-1] - positions[0] != len(positions) - 1:
            noncontiguous += 1

    missing_rid = sum(1 for key in order if isinstance(key, tuple))

    conflicts = 0
    for key in order:
        members = groups[key]
        if len(members) < 2:
            continue
        keyset = set()
        for d in members:
            u = d["message"]["usage"]
            keyset.add((
                u.get("input_tokens"),
                u.get("cache_creation_input_tokens"),
                u.get("cache_read_input_tokens"),
                u.get("output_tokens"),
            ))
        if len(keyset) > 1:
            conflicts += 1

    usage_record_count = sum(len(groups[k]) for k in order)
    unique_request_count = len(order)

    turns = []
    for key in order:
        members = sorted(groups[key], key=lambda d: parse_ts(d.get("timestamp")) or datetime.min)
        rep = members[0]
        m = rep["message"]
        usage = m["usage"]
        cd = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
        turns.append({
            "timestamp": parse_ts(rep.get("timestamp")),
            "model": m.get("model"),
            "attribution_skill": rep.get("attributionSkill"),
            "usage": usage,
            "service_tier": usage.get("service_tier"),
            "ephemeral_1h_tokens": cd.get("ephemeral_1h_input_tokens"),
            "ephemeral_5m_tokens": cd.get("ephemeral_5m_input_tokens"),
        })

    guard = {
        "usage_record_count": usage_record_count,
        "unique_request_count": unique_request_count,
        "duplicated_request_record_count": usage_record_count - unique_request_count,
        "missing_request_id_count": missing_rid,
        "conflicting_usage_within_request_count": conflicts,
        "noncontiguous_requestId_groups": noncontiguous,
    }
    return turns, events_between, guard


def classify_state(ratio):
    if ratio is None:
        return "NOT_APPLICABLE_FIRST_TURN"
    if ratio >= NORMAL_REUSE_MIN_RATIO:
        return "NORMAL_REUSE_CANDIDATE"
    return None  # decided by caller with the cache-creation fraction too


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
    _target_path, target_chw, lines = found  # _target_path used only in-process, never emitted

    turns, events_between, request_identity_guard = build_deduplicated_turns_and_events(lines)
    n = len(turns)

    per_turn = []
    for i, t in enumerate(turns):
        u = t["usage"]
        uncached_in = u.get("input_tokens", 0) or 0
        cache_creation = u.get("cache_creation_input_tokens", 0) or 0
        cache_read = u.get("cache_read_input_tokens", 0) or 0
        output = u.get("output_tokens", 0) or 0
        total_input_context = uncached_in + cache_creation + cache_read

        prior_context = None
        prior_ratio = None
        time_gap_s = None
        model_change = "NOT MEASURABLE"
        skill_change = "NOT MEASURABLE"
        compaction_candidate = "NOT MEASURABLE"

        if i > 0:
            prev = turns[i - 1]
            pu = prev["usage"]
            prior_context = (pu.get("input_tokens", 0) or 0) + \
                            (pu.get("cache_creation_input_tokens", 0) or 0) + \
                            (pu.get("cache_read_input_tokens", 0) or 0)
            if prior_context > 0:
                prior_ratio = cache_read / prior_context
            if t["timestamp"] and prev["timestamp"]:
                time_gap_s = (t["timestamp"] - prev["timestamp"]).total_seconds()
            if t["model"] is not None and prev["model"] is not None:
                model_change = "YES" if t["model"] != prev["model"] else "NO"
            skill_change = "YES" if t["attribution_skill"] != prev["attribution_skill"] else "NO"
            if prior_context > 0:
                compaction_candidate = "YES" if total_input_context < COMPACTION_CANDIDATE_DROP_RATIO * prior_context else "NO"

        tool_bytes_window = {}
        subagent_bytes_window = 0
        for name, nbytes, subagent in events_between[i]:
            if subagent:
                subagent_bytes_window += nbytes
            else:
                tool_bytes_window[name] = tool_bytes_window.get(name, 0) + nbytes

        state = None
        if prior_ratio is None:
            state = "INITIAL_TURN_NO_PRIOR"
        elif prior_ratio >= NORMAL_REUSE_MIN_RATIO:
            state = "NORMAL_REUSE_CANDIDATE"
        elif prior_ratio < MATERIAL_LOSS_MAX_RATIO and prior_context and \
                cache_creation >= MATERIAL_LOSS_MIN_CREATION_FRACTION_OF_PRIOR * prior_context:
            state = "MATERIAL_PREFIX_LOSS_CANDIDATE"
        else:
            state = "PARTIAL_REUSE"

        per_turn.append({
            "turn_index": i + 1,
            "uncached_input_tokens": uncached_in,
            "cache_creation_tokens": cache_creation,
            "cache_read_tokens": cache_read,
            "output_tokens": output,
            "total_input_context": total_input_context,
            "prior_context": prior_context,
            "prior_prefix_reuse_ratio": round(prior_ratio, 4) if prior_ratio is not None else None,
            "cache_creation_fraction_of_prior": round(cache_creation / prior_context, 4) if prior_context else None,
            "time_gap_seconds_from_previous_turn": time_gap_s,
            "model_change": model_change,
            "skill_attribution_change": skill_change,
            "compaction_or_reset_marker": "NOT MEASURABLE (no structural marker in schema)",
            "compaction_candidate_token_drop": compaction_candidate,
            "tool_result_bytes_since_previous_call_by_tool": tool_bytes_window,
            "tool_result_bytes_since_previous_call_total": sum(tool_bytes_window.values()),
            "subagent_return_bytes_since_previous_call": subagent_bytes_window,
            "reuse_state": state,
            "ephemeral_1h_tokens": t.get("ephemeral_1h_tokens"),
            "ephemeral_5m_tokens": t.get("ephemeral_5m_tokens"),
            "service_tier": t.get("service_tier"),
        })

    for pt in per_turn:
        pt["intervening_bytes_total"] = pt["tool_result_bytes_since_previous_call_total"] + \
                                         pt["subagent_return_bytes_since_previous_call"]

    def detect_bursts(max_intervening_bytes, allow_initial_anchor):
        anchor_states = {"MATERIAL_PREFIX_LOSS_CANDIDATE"}
        if allow_initial_anchor:
            anchor_states.add("INITIAL_TURN_NO_PRIOR")
        group_of = [None] * len(per_turn)
        group_id = 0
        prev_in_burst = False
        for i, pt in enumerate(per_turn):
            is_repeat = False
            if i > 0 and pt["reuse_state"] == "MATERIAL_PREFIX_LOSS_CANDIDATE" \
                    and per_turn[i - 1]["reuse_state"] in anchor_states:
                prev_cc = per_turn[i - 1]["cache_creation_tokens"]
                cur_cc = pt["cache_creation_tokens"]
                if prev_cc > 0 and abs(cur_cc - prev_cc) / prev_cc <= BURST_SIZE_TOLERANCE \
                        and pt["intervening_bytes_total"] <= max_intervening_bytes:
                    is_repeat = True
            if is_repeat:
                if not prev_in_burst:
                    group_id += 1
                    group_of[i - 1] = group_id
                group_of[i] = group_id
                prev_in_burst = True
            else:
                prev_in_burst = False

        groups = {}
        for i, g in enumerate(group_of):
            if g is not None:
                groups.setdefault(g, []).append(per_turn[i])

        summary = []
        for g, members in sorted(groups.items()):
            members_sorted = sorted(members, key=lambda p: p["turn_index"])
            entry_gap = members_sorted[0]["time_gap_seconds_from_previous_turn"]
            summary.append({
                "burst_group": g,
                "turn_indices": [m["turn_index"] for m in members_sorted],
                "repeat_count": len(members_sorted),
                "cache_creation_tokens_each": members_sorted[0]["cache_creation_tokens"],
                "cache_creation_tokens_wasted": sum(m["cache_creation_tokens"] for m in members_sorted[1:]),
                "entry_time_gap_seconds_from_previous_turn": entry_gap,
                "entry_gap_exceeds_3600s": (entry_gap is not None and entry_gap > 3600),
                "intervening_bytes_within_burst": sum(m["intervening_bytes_total"] for m in members_sorted[1:]),
            })
        return summary, group_of

    burst_variants_result = {}
    strict_group_of = None
    for variant in BURST_VARIANTS:
        summary, group_of = detect_bursts(variant["max_intervening_bytes"], variant["allow_initial_anchor"])
        total_wasted = sum(b["cache_creation_tokens_wasted"] for b in summary)
        after_long_gap = sum(1 for b in summary if b["entry_gap_exceeds_3600s"])
        burst_variants_result[variant["name"]] = {
            "max_intervening_bytes": variant["max_intervening_bytes"],
            "allow_initial_anchor": variant["allow_initial_anchor"],
            "burst_group_count": len(summary),
            "bursts_starting_after_gap_over_3600s": after_long_gap,
            "total_cache_creation_tokens_wasted_on_repeats": total_wasted,
            "groups": summary,
        }
        if variant["name"] == "STRICT":
            strict_group_of = group_of

    for i, pt in enumerate(per_turn):
        pt["repeated_rebuild_burst_group"] = strict_group_of[i]

    total_cache_creation = sum(pt["cache_creation_tokens"] for pt in per_turn)
    for variant_name, v in burst_variants_result.items():
        v["share_of_total_cache_creation"] = round(
            v["total_cache_creation_tokens_wasted_on_repeats"] / total_cache_creation, 4
        ) if total_cache_creation else None

    by_state_creation = {}
    for pt in per_turn:
        by_state_creation[pt["reuse_state"]] = by_state_creation.get(pt["reuse_state"], 0) + pt["cache_creation_tokens"]

    def share(state):
        return by_state_creation.get(state, 0) / total_cache_creation if total_cache_creation else 0.0

    normal_share = share("NORMAL_REUSE_CANDIDATE")
    material_share = share("MATERIAL_PREFIX_LOSS_CANDIDATE")
    partial_share = share("PARTIAL_REUSE")
    initial_share = share("INITIAL_TURN_NO_PRIOR")

    if total_cache_creation == 0:
        mechanism = "NOT_DETERMINABLE"
    elif normal_share >= MECHANISM_DOMINANCE_THRESHOLD:
        mechanism = "NORMAL_INCREMENTAL_REUSE"
    elif material_share >= MECHANISM_DOMINANCE_THRESHOLD:
        mechanism = "PREFIX_REBUILD_DOMINANT"
    else:
        mechanism = "MIXED_CACHE_BEHAVIOR"

    # Material cache events: minimal turn set (ranked by cache_creation desc)
    # covering >=80% of total cache_creation.
    ranked = sorted(per_turn, key=lambda pt: pt["cache_creation_tokens"], reverse=True)
    material_events = []
    running = 0
    for pt in ranked:
        if running >= MATERIAL_EVENT_COVERAGE * total_cache_creation and material_events:
            break
        material_events.append(pt)
        running += pt["cache_creation_tokens"]
    material_event_turn_indices = {pt["turn_index"] for pt in material_events}

    control_events = [pt for pt in per_turn if pt["reuse_state"] == "NORMAL_REUSE_CANDIDATE"]

    def group_stats(group, label):
        if not group:
            return {"label": label, "count": 0}
        gaps = [g["time_gap_seconds_from_previous_turn"] for g in group if g["time_gap_seconds_from_previous_turn"] is not None]
        tool_bytes = [g["tool_result_bytes_since_previous_call_total"] for g in group]
        subagent_bytes = [g["subagent_return_bytes_since_previous_call"] for g in group]
        skill_changes = sum(1 for g in group if g["skill_attribution_change"] == "YES")
        model_changes = sum(1 for g in group if g["model_change"] == "YES")
        compaction_candidates = sum(1 for g in group if g["compaction_candidate_token_drop"] == "YES")
        return {
            "label": label,
            "count": len(group),
            "avg_time_gap_seconds": round(sum(gaps) / len(gaps), 1) if gaps else None,
            "median_time_gap_seconds": sorted(gaps)[len(gaps) // 2] if gaps else None,
            "avg_tool_result_bytes_since_previous_call": round(sum(tool_bytes) / len(tool_bytes), 1) if tool_bytes else None,
            "avg_subagent_return_bytes_since_previous_call": round(sum(subagent_bytes) / len(subagent_bytes), 1) if subagent_bytes else None,
            "skill_attribution_change_prevalence": round(skill_changes / len(group), 4),
            "model_change_prevalence": round(model_changes / len(group), 4),
            "compaction_candidate_prevalence": round(compaction_candidates / len(group), 4),
        }

    material_stats = group_stats(material_events, "MATERIAL_CACHE_EVENTS")
    control_stats = group_stats(control_events, "NORMAL_REUSE_CANDIDATE_CONTROLS")

    # Tool-result breakdown across the WHOLE session (not just material-event windows)
    tool_totals = {}
    for pt in per_turn:
        for name, nbytes in pt["tool_result_bytes_since_previous_call_by_tool"].items():
            tool_totals[name] = tool_totals.get(name, 0) + nbytes
    total_tool_bytes = sum(tool_totals.values())

    tool_association = {}
    for name in tool_totals:
        preceded = [pt for pt in per_turn if pt["tool_result_bytes_since_previous_call_by_tool"].get(name, 0) > 0]
        not_preceded = [pt for pt in per_turn if pt["tool_result_bytes_since_previous_call_by_tool"].get(name, 0) == 0
                        and pt["reuse_state"] != "INITIAL_TURN_NO_PRIOR"]
        preceded_material = sum(1 for pt in preceded if pt["reuse_state"] == "MATERIAL_PREFIX_LOSS_CANDIDATE")
        not_preceded_material = sum(1 for pt in not_preceded if pt["reuse_state"] == "MATERIAL_PREFIX_LOSS_CANDIDATE")
        tool_association[name] = {
            "call_count_turns_preceded_by_this_tool": len(preceded),
            "material_event_prevalence_when_preceded": round(preceded_material / len(preceded), 4) if preceded else None,
            "material_event_prevalence_when_not_preceded": round(not_preceded_material / len(not_preceded), 4) if not_preceded else None,
            "result_bytes_total": tool_totals[name],
            "share_of_tool_result_bytes": round(tool_totals[name] / total_tool_bytes, 4) if total_tool_bytes else None,
        }

    # Time-gap buckets (deterministic, pre-declared)
    def bucket_gap(g):
        if g is None:
            return "NOT_MEASURABLE"
        if g < 10:
            return "0-10s"
        if g < 60:
            return "10-60s"
        if g < 300:
            return "60-300s"
        if g < 1800:
            return "300-1800s"
        return ">=1800s"

    time_buckets = {}
    for pt in per_turn:
        b = bucket_gap(pt["time_gap_seconds_from_previous_turn"])
        tb = time_buckets.setdefault(b, {"count": 0, "material_count": 0, "cache_creation_sum": 0})
        tb["count"] += 1
        tb["cache_creation_sum"] += pt["cache_creation_tokens"]
        if pt["reuse_state"] == "MATERIAL_PREFIX_LOSS_CANDIDATE":
            tb["material_count"] += 1
    for b, tb in time_buckets.items():
        tb["material_share_within_bucket"] = round(tb["material_count"] / tb["count"], 4) if tb["count"] else None

    models_seen = sorted({t["model"] for t in turns if t["model"] is not None})
    skills_seen = sorted({t["attribution_skill"] for t in turns if t["attribution_skill"] is not None})
    service_tiers_seen = sorted({t["service_tier"] for t in turns if t.get("service_tier") is not None})
    ephemeral_1h_calls = sum(1 for t in turns if (t.get("ephemeral_1h_tokens") or 0) > 0)
    ephemeral_5m_calls = sum(1 for t in turns if (t.get("ephemeral_5m_tokens") or 0) > 0)

    report = {
        "measurement_cutoff_utc": args.cutoff,
        "target_session_label": "S1",
        "target_context_high_water_tokens": target_chw,
        "main_thread_turn_count": n,
        "main_thread_raw_line_count": request_identity_guard["usage_record_count"],
        "request_identity_guard": request_identity_guard,
        "total_cache_creation_tokens": total_cache_creation,
        "cache_creation_share_by_reuse_state": {
            "NORMAL_REUSE_CANDIDATE": round(normal_share, 4),
            "PARTIAL_REUSE": round(partial_share, 4),
            "MATERIAL_PREFIX_LOSS_CANDIDATE": round(material_share, 4),
            "INITIAL_TURN_NO_PRIOR": round(initial_share, 4),
        },
        "cache_churn_mechanism_classification": mechanism,
        "repeated_rebuild_bursts": {
            "definition": "consecutive turns with cache_creation within "
                           f"{BURST_SIZE_TOLERANCE*100:.0f}% of each other and near-zero "
                           "intervening tool/subagent bytes; STRICT and LOOSE are reported "
                           "side by side rather than one replacing the other (see method note "
                           "in source) since both thresholds were fixed only after an initial "
                           "one-instance inspection, not frozen blind",
            "variants": burst_variants_result,
        },
        "material_cache_events": {
            "coverage_target": MATERIAL_EVENT_COVERAGE,
            "event_count": len(material_events),
            "event_turn_indices": sorted(material_event_turn_indices),
            "cache_creation_covered": running,
            "cache_creation_covered_fraction": round(running / total_cache_creation, 4) if total_cache_creation else None,
            "stats": material_stats,
        },
        "control_group_stats": control_stats,
        "tool_result_association": tool_association,
        "time_gap_buckets": time_buckets,
        "models_seen": models_seen,
        "skill_attributions_seen": skills_seen,
        "service_tiers_seen": service_tiers_seen,
        "cache_breakpoint_type_usage": {
            "calls_writing_ephemeral_1h_tokens": ephemeral_1h_calls,
            "calls_writing_ephemeral_5m_tokens": ephemeral_5m_calls,
            "note": "this session writes exclusively to one breakpoint type if one count is 0",
        },
        "per_turn": per_turn,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main()
