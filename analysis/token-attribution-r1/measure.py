#!/usr/bin/env python3
"""FABLE_TOKEN_COST_ATTRIBUTION_R1 deterministic measurement script.

Measures where context growth and novel token volume come from in this
project's Claude Code session transcripts. Produces two distinct headline
metrics per session (never conflated):

  CONTEXT_HIGH_WATER  - max single-call prompt footprint (main thread only)
  NOVEL_TOKEN_VOLUME   - sum of non-cache-read tokens across main-thread calls

All session identifiers are anonymized to S1..Sn (ranked by
CONTEXT_HIGH_WATER) in every report-facing output. This script does not
persist raw transcript content anywhere; it only ever prints/writes
aggregate numbers, byte counts, and category labels.

Usage:
    python3 measure.py --cutoff <ISO8601-UTC> \
        --exclude-session <this-measurement-session-id> \
        [--out metrics.json]

The transcript directory and the excluded session id are NOT hardcoded as
magic constants inside the measurement logic below beyond this file's own
default TRANSCRIPT_DIR (the authorized scope root, which is a project path,
not a session identifier). The excluded session is supplied by the caller
at run time, discovered dynamically from CLAUDE_CODE_SESSION_ID at the time
of invocation, so this script stays reusable for a future re-run rather than
being frozen to today's session.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

TRANSCRIPT_DIR = os.path.expanduser(
    "~/.claude/projects/-Users-kelvin-VibeCoding-WorkSpace-skill"
)
TOP_N = 5


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_lines(path, cutoff):
    """Read one transcript file, dropping records after the frozen cutoff."""
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


def block_text_bytes(block):
    if isinstance(block, dict):
        if isinstance(block.get("text"), str):
            return len(block["text"].encode("utf-8"))
        if "input" in block:
            return len(json.dumps(block["input"], ensure_ascii=False).encode("utf-8"))
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
    """A subagent-spawn tool result carries this signature regardless of the
    tool's display name. This derives identity from evidence (the presence
    of agent-run metadata) instead of assuming a hardcoded tool name such as
    "Task"."""
    return (
        isinstance(tur, dict)
        and "agentId" in tur
        and "totalToolUseCount" in tur
    )


class SessionAccum:
    def __init__(self, session_id):
        self.session_id = session_id
        self.main_usage = []  # list of (timestamp, dict usage) main-thread only
        self.sidechain_record_count = 0
        self.subagent_spawns = []  # list of dicts: {name, prompt_bytes, return_bytes, internal_usage, internal_total_tokens, internal_tool_use_count, judge_like}
        self.tool_use_input_bytes = {}  # name -> bytes (main thread, excludes subagent prompt handled separately in D too - see note)
        self.tool_result_bytes = {}  # name -> bytes (main thread, excludes subagent-identified results)
        self.user_text_bytes = 0
        self.assistant_text_bytes = 0
        self.assistant_thinking_bytes = 0
        self.judge_attributed_usage = []  # exact main-thread usage dicts tagged attributionSkill=='fable-judge'
        self.first_main_usage_seen = None  # (cache_read, cache_creation) of first chronological main usage record
        self.thinking_tokens_exact_sum = 0
        self.thinking_tokens_field_present_count = 0
        self.thinking_tokens_field_absent_count = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", required=True, help="ISO8601 UTC cutoff, e.g. 2026-08-23T12:05:44Z")
    ap.add_argument("--exclude-session", action="append", default=[],
                     help="Session id to exclude (repeatable). Pass the id of the session performing this measurement.")
    ap.add_argument("--out", default=None, help="Optional path to write sanitized JSON metrics")
    args = ap.parse_args()

    cutoff = parse_ts(args.cutoff)
    if cutoff is None:
        print(f"FATAL: could not parse --cutoff {args.cutoff!r}", file=sys.stderr)
        sys.exit(2)

    files = sorted(glob.glob(os.path.join(TRANSCRIPT_DIR, "*.jsonl")))
    excluded = set(args.exclude_session)

    total_session_count = 0
    eligible = {}
    excluded_count = 0

    for path in files:
        sid = os.path.splitext(os.path.basename(path))[0]
        total_session_count += 1
        if sid in excluded:
            excluded_count += 1
            continue

        lines = load_lines(path, cutoff)
        if not lines:
            excluded_count += 1
            continue

        acc = SessionAccum(sid)

        # Pass 1: build tool_use_id -> name map from MAIN-THREAD assistant messages,
        # in file order (chronological within a session's own log).
        tool_id_to_name = {}
        for d in lines:
            if d.get("isSidechain"):
                continue
            m = d.get("message") or {}
            if m.get("role") != "assistant":
                continue
            content = m.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tool_id_to_name[b.get("id")] = b.get("name")

        # Pass 2: walk lines in order, accumulate all metrics.
        for d in lines:
            is_side = bool(d.get("isSidechain"))
            if is_side:
                acc.sidechain_record_count += 1
                continue

            m = d.get("message") or {}
            role = m.get("role")
            usage = m.get("usage")
            ts = parse_ts(d.get("timestamp"))
            attr = d.get("attributionSkill")

            if role == "assistant" and isinstance(usage, dict):
                acc.main_usage.append((ts, usage))
                if acc.first_main_usage_seen is None:
                    acc.first_main_usage_seen = (
                        usage.get("cache_read_input_tokens", 0) or 0,
                        usage.get("cache_creation_input_tokens", 0) or 0,
                    )
                if attr == "fable-judge":
                    acc.judge_attributed_usage.append(usage)
                otd = usage.get("output_tokens_details")
                if isinstance(otd, dict) and "thinking_tokens" in otd:
                    acc.thinking_tokens_exact_sum += otd.get("thinking_tokens") or 0
                    acc.thinking_tokens_field_present_count += 1
                else:
                    acc.thinking_tokens_field_absent_count += 1

            content = m.get("content")
            if not isinstance(content, list):
                continue

            for b in content:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")

                if btype == "text" and role == "user":
                    acc.user_text_bytes += block_text_bytes(b)
                elif btype == "text" and role == "assistant":
                    acc.assistant_text_bytes += block_text_bytes(b)
                elif btype == "thinking" and role == "assistant":
                    acc.assistant_thinking_bytes += block_text_bytes(b)
                elif btype == "tool_use" and role == "assistant":
                    name = b.get("name") or "UNKNOWN"
                    nbytes = len(json.dumps(b.get("input"), ensure_ascii=False, default=str).encode("utf-8"))
                    acc.tool_use_input_bytes[name] = acc.tool_use_input_bytes.get(name, 0) + nbytes
                elif btype == "tool_result":
                    tool_use_id = b.get("tool_use_id")
                    name = tool_id_to_name.get(tool_use_id, "UNKNOWN")
                    tur = d.get("toolUseResult")

                    if is_subagent_result(tur):
                        return_bytes = content_bytes(tur.get("content"))
                        prompt_val = tur.get("prompt")
                        prompt_bytes = len(prompt_val.encode("utf-8")) if isinstance(prompt_val, str) else 0
                        internal_usage = tur.get("usage") if isinstance(tur.get("usage"), dict) else None
                        prompt_lower = (prompt_val or "").lower()
                        judge_like = "judge" in prompt_lower[:2000] or (tur.get("agentType") == "general-purpose" and "adversarial" in prompt_lower[:2000])
                        acc.subagent_spawns.append({
                            "name": name,
                            "prompt_bytes": prompt_bytes,
                            "return_bytes": return_bytes,
                            "internal_total_tokens": tur.get("totalTokens"),
                            "internal_tool_use_count": tur.get("totalToolUseCount"),
                            "internal_usage": internal_usage,
                            "judge_like": judge_like,
                        })
                    else:
                        rbytes = content_bytes(b.get("content"))
                        if rbytes == 0 and isinstance(tur, dict):
                            # Bash-shaped results keep payload under toolUseResult, not message.content
                            rbytes = len((tur.get("stdout") or "").encode("utf-8")) + \
                                     len((tur.get("stderr") or "").encode("utf-8"))
                        acc.tool_result_bytes[name] = acc.tool_result_bytes.get(name, 0) + rbytes

        if not acc.main_usage:
            excluded_count += 1
            continue

        eligible[sid] = acc

    eligible_session_count = len(eligible)

    def context_high_water(acc):
        best = 0
        for _, u in acc.main_usage:
            v = (u.get("input_tokens", 0) or 0) + \
                (u.get("cache_read_input_tokens", 0) or 0) + \
                (u.get("cache_creation_input_tokens", 0) or 0)
            best = max(best, v)
        return best

    def novel_token_volume(acc):
        return sum(
            (u.get("input_tokens", 0) or 0) +
            (u.get("cache_creation_input_tokens", 0) or 0) +
            (u.get("output_tokens", 0) or 0)
            for _, u in acc.main_usage
        )

    ranked = sorted(eligible.values(), key=context_high_water, reverse=True)
    top = ranked[:TOP_N]

    report = {
        "measurement_cutoff_utc": args.cutoff,
        "total_session_count": total_session_count,
        "eligible_session_count": eligible_session_count,
        "excluded_session_count": excluded_count,
        "excluded_reason": "current measurement session and/or empty/unparseable after cutoff",
        "top_n": len(top),
        "sessions": [],
    }

    # ---- per-session anonymized metrics ----
    agg_prompt_bytes = 0
    agg_return_bytes = 0
    agg_spawn_count = 0
    agg_sidechain_records = 0
    agg_user_bytes = 0
    agg_assistant_text_bytes = 0
    agg_assistant_thinking_bytes = 0
    tool_use_input_totals = {}
    tool_result_totals = {}
    cold_start_baselines = []
    resumed_count = 0
    judge_attributed_token_sum = 0
    judge_attributed_line_count_top = 0
    judge_like_spawn_count = 0
    judge_like_spawn_return_bytes = 0
    thinking_tokens_exact_total = 0
    thinking_present_total = 0
    thinking_absent_total = 0
    novel_token_volume_total = 0

    for idx, acc in enumerate(top, start=1):
        label = f"S{idx}"
        chw = context_high_water(acc)
        ntv = novel_token_volume(acc)
        uncached_in = sum((u.get("input_tokens", 0) or 0) for _, u in acc.main_usage)
        cache_creation = sum((u.get("cache_creation_input_tokens", 0) or 0) for _, u in acc.main_usage)
        cache_read = sum((u.get("cache_read_input_tokens", 0) or 0) for _, u in acc.main_usage)
        output = sum((u.get("output_tokens", 0) or 0) for _, u in acc.main_usage)

        spawn_prompt_bytes = sum(s["prompt_bytes"] for s in acc.subagent_spawns)
        spawn_return_bytes = sum(s["return_bytes"] for s in acc.subagent_spawns)

        cold_read, cold_creation = acc.first_main_usage_seen or (None, None)
        is_cold_start = cold_read == 0
        if is_cold_start:
            cold_start_baselines.append(cold_creation)
        else:
            resumed_count += 1

        sess_judge_tokens = sum(
            (u.get("input_tokens", 0) or 0) +
            (u.get("cache_creation_input_tokens", 0) or 0) +
            (u.get("output_tokens", 0) or 0)
            for u in acc.judge_attributed_usage
        )
        judge_attributed_token_sum += sess_judge_tokens
        judge_attributed_line_count_top += len(acc.judge_attributed_usage)

        for s in acc.subagent_spawns:
            if s["judge_like"]:
                judge_like_spawn_count += 1
                judge_like_spawn_return_bytes += s["return_bytes"]

        report["sessions"].append({
            "label": label,
            "context_high_water_tokens": chw,
            "novel_token_volume_tokens": ntv,
            "uncached_input_tokens": uncached_in,
            "cache_creation_tokens": cache_creation,
            "cache_read_tokens": cache_read,
            "output_tokens": output,
            "main_thread_call_count": len(acc.main_usage),
            "subagent_spawn_count": len(acc.subagent_spawns),
            "sidechain_record_count": acc.sidechain_record_count,
            "subagent_spawn_prompt_bytes": spawn_prompt_bytes,
            "subagent_return_payload_bytes": spawn_return_bytes,
            "cold_start": is_cold_start,
            "always_loaded_baseline_tokens_if_cold_start": cold_creation if is_cold_start else None,
            "judge_attributed_main_thread_lines": len(acc.judge_attributed_usage),
            "judge_attributed_novel_tokens_lower_bound": sess_judge_tokens,
            "judge_like_subagent_spawns": sum(1 for s in acc.subagent_spawns if s["judge_like"]),
            "thinking_tokens_exact_sum": acc.thinking_tokens_exact_sum,
            "thinking_tokens_field_present_count": acc.thinking_tokens_field_present_count,
            "thinking_tokens_field_absent_count": acc.thinking_tokens_field_absent_count,
        })

        agg_prompt_bytes += spawn_prompt_bytes
        agg_return_bytes += spawn_return_bytes
        agg_spawn_count += len(acc.subagent_spawns)
        agg_sidechain_records += acc.sidechain_record_count
        agg_user_bytes += acc.user_text_bytes
        agg_assistant_text_bytes += acc.assistant_text_bytes
        agg_assistant_thinking_bytes += acc.assistant_thinking_bytes
        for k, v in acc.tool_use_input_bytes.items():
            tool_use_input_totals[k] = tool_use_input_totals.get(k, 0) + v
        for k, v in acc.tool_result_bytes.items():
            tool_result_totals[k] = tool_result_totals.get(k, 0) + v
        thinking_tokens_exact_total += acc.thinking_tokens_exact_sum
        thinking_present_total += acc.thinking_tokens_field_present_count
        thinking_absent_total += acc.thinking_tokens_field_absent_count
        novel_token_volume_total += ntv

    report["aggregate_across_top_n"] = {
        "B_user_turns_bytes": agg_user_bytes,
        "C_assistant_text_bytes": agg_assistant_text_bytes,
        "C_assistant_thinking_bytes": agg_assistant_thinking_bytes,
        "D_tool_use_input_bytes_by_tool": tool_use_input_totals,
        "E_tool_result_bytes_by_tool_excl_subagent": tool_result_totals,
        "F_subagent_spawn_count": agg_spawn_count,
        "F_subagent_spawn_prompt_bytes": agg_prompt_bytes,
        "F_subagent_return_payload_bytes": agg_return_bytes,
        "sidechain_record_count_total": agg_sidechain_records,
        "cold_start_session_count": len(cold_start_baselines),
        "resumed_session_count": resumed_count,
        "A_always_loaded_baseline_tokens_cold_start_sessions": cold_start_baselines,
        "G_judge_attributed_main_thread_novel_tokens_lower_bound": judge_attributed_token_sum,
        "G_judge_attributed_main_thread_line_count": judge_attributed_line_count_top,
        "G_judge_like_subagent_spawn_count": judge_like_spawn_count,
        "G_judge_like_subagent_return_payload_bytes": judge_like_spawn_return_bytes,
        "C_thinking_tokens_exact_total": thinking_tokens_exact_total,
        "C_thinking_tokens_field_present_count": thinking_present_total,
        "C_thinking_tokens_field_absent_count": thinking_absent_total,
        "novel_token_volume_total_across_top_n": novel_token_volume_total,
        "C_thinking_share_of_novel_token_volume": (
            round(thinking_tokens_exact_total / novel_token_volume_total, 4)
            if novel_token_volume_total else None
        ),
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
