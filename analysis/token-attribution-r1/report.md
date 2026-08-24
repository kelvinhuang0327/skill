# FABLE_TOKEN_COST_ATTRIBUTION_R1 — Report

Measurement only. No optimization implemented. All numbers below are
reproduced by `measure.py`, run twice against the same frozen input boundary
(byte-identical output both times).

## Correction note (FABLE_REQUEST_LEVEL_MEASUREMENT_CORRECTION_R1)

This analysis originally treated usage-bearing assistant JSONL records as
independent model requests. Later request-level analysis
(`analysis/cache-miss-diagnostic-r1/`) demonstrated that multi-content-block
responses can create multiple records sharing one `requestId`. This version
deduplicates usage accounting by `requestId`. Byte-count categories (B–F
below) are unaffected — verified, not assumed (see "Request identity
verification") — because each content block is logged on exactly one JSONL
line even when a response spans several lines.

## Reproduction

```bash
python3 measure.py \
  --cutoff 2026-08-23T12:05:44Z \
  --exclude-session <this-measurement-session-id> \
  --out metrics.json
```

`metrics.json` in this directory is the exact captured output of that run.
Run it twice; the two outputs are byte-identical (verified).

## Request identity verification

Required guard, run across all five sampled sessions before recomputing any
usage-derived metric:

| | S1 | S2 | S3 | S4 | S5 | **Total** |
|---|---:|---:|---:|---:|---:|---:|
| USAGE_RECORD_COUNT | 402 | 122 | 202 | 100 | 126 | **952** |
| UNIQUE_REQUEST_COUNT | 162 | 47 | 88 | 34 | 56 | **387** |
| DUPLICATED_REQUEST_RECORD_COUNT | 240 | 75 | 114 | 66 | 70 | **565** |
| MISSING_REQUEST_ID_COUNT | 0 | 0 | 0 | 0 | 0 | **0** |
| CONFLICTING_USAGE_WITHIN_REQUEST_COUNT | 0 | 0 | 0 | 0 | 0 | **0** |

`requestId` is present on 100% of usage-bearing records in every sampled
session; zero requestId groups contain conflicting usage (so the "no
arbitrary row choice" concern does not arise — the deterministic rule is
simply "first chronological member," and every member already agrees).
`main_thread_call_count` below is now `UNIQUE_REQUEST_COUNT`, not raw line
count; `main_thread_raw_line_count` is reported alongside it for
transparency.

A second, separate check confirmed content blocks (text/thinking/tool_use)
are **never** duplicated across a requestId's split lines — each block is
logged on exactly one line — so the byte-count categories (B, C-bytes, D, E,
F below) require no correction. A third check confirmed
`output_tokens_details.thinking_tokens` **is** duplicated identically across
split lines (342/342 multi-line groups checked, zero exceptions), so it is
deduplicated the same way as the other usage fields.

## Sample

- TOTAL_SESSION_COUNT: 31, ELIGIBLE_SESSION_COUNT: 25, EXCLUDED_SESSION_COUNT: 6
  (all `*.jsonl` files under the authorized transcript directory as of
  re-run time). These raw counts are higher than the original run's 28/24/4
  — new unrelated sessions have been created in this project since the
  original 2026-08-23 measurement. This is expected corpus drift, not part
  of this correction: every new session postdates the frozen cutoff and is
  excluded by the cutoff filter, so it cannot enter TOP_N. Confirmed: the
  frozen cutoff reproduces the identical S1–S5 sessions as the original run
  (identical CONTEXT_HIGH_WATER values below, which is direct proof of
  session identity since it is computed independently of file-listing
  drift).
- TOP_N: 5, ranked by CONTEXT_HIGH_WATER (main-thread only)
- MEASUREMENT_CUTOFF: `2026-08-23T12:05:44Z` — unchanged, reused from the
  original measurement
- Sessions are referred to only as S1–S5 (rank order). No session ID,
  filename, or UUID appears in this report or in `metrics.json`.

## Two headline metrics — kept separate as required

| | S1 | S2 | S3 | S4 | S5 |
|---|---:|---:|---:|---:|---:|
| CONTEXT_HIGH_WATER (tokens) | 637,127 | 284,255 | 262,736 | 256,177 | 246,881 |
| NOVEL_TOKEN_VOLUME (tokens) | 1,891,467 | 384,606 | 735,520 | 333,990 | 312,515 |
| main-thread calls (unique requests) | 162 | 47 | 88 | 34 | 56 |
| main-thread raw JSONL lines | 402 | 122 | 202 | 100 | 126 |

**CONTEXT_HIGH_WATER** = max per-call `(input + cache_read + cache_creation)`.
Recomputed at request level and unchanged from the original report — a `max`
over duplicate-carrying records equals the same `max` over deduplicated
records whenever duplicates carry identical values, which every group here
does (verified above, not assumed).

**NOVEL_TOKEN_VOLUME** = sum over the session of `(input + cache_creation +
output)` per unique request, deliberately excluding `cache_read`. This is
not a monetary cost. `MONETARY_COST: NOT MEASURABLE`.

Raw components (sum across S1–S5, exact from `usage`, deduplicated):

| Component | Tokens | Share of NOVEL_TOKEN_VOLUME |
|---|---:|---:|
| cache_creation | 2,856,085 | 78.1% |
| output | 799,418 | 21.9% |
| uncached input | 2,595 | 0.07% |
| **NOVEL_TOKEN_VOLUME total** | **3,658,098** | 100% |

## H1 — subagent return (early falsification, run first)

Unaffected by this correction: subagent-spawn identification and byte counts
come from `toolUseResult`/content-block data, not usage records, and are
verified unaffected by request-line duplication (see "Request identity
verification").

- Spawn tool name: **`Agent`** (structural signature: `agentId` +
  `totalToolUseCount`).
- `isSidechain: true` still never occurs anywhere in the authorized
  transcript store.
- SUBAGENT_SPAWN_COUNT across S1–S5: **3 total** (S1: 0, S2: 0, S3: 1, S4: 2,
  S5: 0) — identical to the original measurement.
- SUBAGENT_RETURN_PAYLOAD_BYTES: 47,144 bytes combined. SUBAGENT_SPAWN_PROMPT_BYTES: 37,219 bytes combined.

**H1 SUBAGENT_RETURN: REFUTED** (unchanged). The two highest-context
sessions (S1, S2) spawned zero subagents; the entire top-5 subagent-return
payload (47KB) is negligible against every other measured category.

## Attribution categories (A–G)

| Cat | Label | Measurement | Unit | Status | Limitation |
|---|---|---:|---|---|---|
| A | ALWAYS_LOADED | 53,958 | tokens | Measurable (1 session only) | Only S1 was a cold start. Unchanged value — the cold-start floor is the first chronological request's own usage, unaffected by later duplicate lines. |
| B | USER_TURNS | 118,442 | bytes | Measured (unchanged) | Text blocks under `role=user`, excluding tool_result blocks. |
| C | ASSISTANT_TEXT | 238,124 | bytes | Measured (unchanged) | `type=text` blocks under `role=assistant`. |
| C | ASSISTANT_THINKING (bytes) | 2,119,551 | bytes | Measured (unchanged) | `type=thinking` blocks. |
| C | ASSISTANT_THINKING (exact tokens) | 313,239 | tokens | Measured, partial coverage | `usage.output_tokens_details.thinking_tokens`, deduplicated: present on 222/387 unique requests in S1–S5, absent on 165 (162 of which are S1 — S1 has zero thinking coverage at any content-block granularity). |
| D | TOOL_USE_INPUTS | 426,099 | bytes | Measured (unchanged) | Broken down by tool name below. |
| E | TOOL_RESULT_PAYLOADS | 887,517 | bytes | Measured (unchanged) | Excludes subagent-identified results (counted in F). |
| F | SUBAGENT_RETURNS | 47,144 | bytes | Measured (unchanged) | — |
| G | JUDGE_RELATED | 0 | tokens (exact) | Measured, likely incomplete | `attributionSkill=='fable-judge'` tags 0 unique requests within S1–S5 (unchanged). |

**D breakdown by tool (bytes, unchanged):** Bash 278,657 · Agent 59,263 ·
Write 35,034 · Skill 19,383 · Edit 22,478 · AskUserQuestion 6,523 · Read
4,552 · ScheduleWakeup 209.

**E breakdown by tool (bytes, unchanged):** Bash 676,990 · Read 200,527 ·
Edit 5,922 · Write 1,372 · AskUserQuestion 1,486 · Agent 1,088 · Skill 87 ·
ScheduleWakeup 45.

## The byte-vs-token trap, caught by this task's own guardrail

By raw bytes, `ASSISTANT_THINKING` still looks dominant: 2,119,551 bytes is
≈55% of the ~3.84M-byte grand total across B/C/D/E/F (all byte figures
unchanged by this correction), comfortably clearing the 30%-and-1.5×-runner-up
rule against the next-largest byte category (E, 887,517 bytes).

**That conclusion still does not survive exact-token measurement**, and the
margin is essentially unchanged by the correction. All thinking bytes come
from S2–S5 — S1 emitted the `thinking` content type on zero of its 162 real
calls. Measured in exact tokens instead of bytes, `ASSISTANT_THINKING` is
313,239 / 3,658,098 = **8.56%** of NOVEL_TOKEN_VOLUME (old, superseded
line-record figure: 912,137 / 10,578,003 = 8.6% — the ratio barely moved
because both the numerator and denominator were inflated by roughly
proportional duplication). Nowhere near dominant either way.

## S1 cache-creation, corrected

S1 accounts for 1,564,018 of the 2,856,085 total cache_creation tokens
across S1–S5 (54.8%), spread over **162 real requests** — an average of
~9,654 new tokens per real request. Its cold-start floor (category A) is
53,958 tokens: 3.45% of S1's own corrected cache_creation total (1.48% of
the full S1–S5 NOVEL_TOKEN_VOLUME total).

The original ("line-record") version of this section described this as
"repeated, per-call cache-creation churn, turn after turn, not a one-time
load" and treated it as a single large unattributed anomaly. That framing
does not survive request-level correction. `analysis/cache-miss-diagnostic-r1/`
(read-only reference; not modified or duplicated here) established, for this
exact session, that S1's cache_creation decomposes as:

- **65.89%** — exactly 3 real requests with an explicit, first-party
  `cache_miss_reason` (1 `system_changed`, 2 `previous_message_not_found`,
  the latter two tied with zero counterexamples to the only 2 idle gaps over
  3,600s in the entire session). Not repeated churn — 3 discrete, fully
  diagnosed events.
- **30.66%** — ordinary incremental cache growth on 158 healthy
  normal-reuse requests (reuse ratio ≥0.80). This is the unavoidable cost of
  a session that reaches a 637K-token context, not a distinct phenomenon
  needing its own attribution.
- **3.45%** — the one cold-start request.

`analysis/cache-reuse-churn-r1/` (corrected alongside this report) reaches
the identical 65.89%/30.66%/3.45% split independently, and its own
tool-result-association numbers now show **zero** of the 3 real material
events were immediately preceded by a Bash result (`material_event_prevalence_when_preceded:
0.0` for every tool) — direct, in-scope evidence against the tool-output-variability
mechanism this report originally floated as a candidate direction (see H2).

**What is still not resolved, and is not resolved by this correction
either:** no exact split of `cache_creation` into content categories (D vs E
vs C-text) exists without a real tokenizer — deduplication corrects the
count, it does not add tokenizer-level attribution. This specific limitation
is unchanged from the original report.

## Hypothesis verdicts

| Hypothesis | Verdict | Basis / unit |
|---|---|---|
| H1 SUBAGENT_RETURN | **REFUTED** (unchanged) | Exact: 3 spawns, 47,144 bytes, across the 2 highest-context sessions (S1, S2) zero spawns occurred. |
| H2 MAIN_THREAD_RETRIEVAL | **REFUTED for its proposed mechanism; byte category itself still real** | E remains the largest measured byte category after thinking (887,517 bytes, unchanged), but the specific candidate mechanism this report floated for S1 ("tool-output variability... repeatedly invalidating the cache prefix") is now directly contradicted: `cache-reuse-churn-r1`'s corrected data shows 0 of S1's 3 real material cache events were preceded by a Bash result, and `cache-miss-diagnostic-r1` shows the actual causes are idle-gap eviction (2 of 3) and one system/skill-context change — neither is tool-output variability. No exact token share for E still exists without a tokenizer. |
| H3 ALWAYS_LOADED | **REFUTED** (unchanged conclusion, updated magnitude) | Exact: the cold-start floor (53,958 tokens) is 1.48% of the corrected S1–S5 NOVEL_TOKEN_VOLUME total and 3.45% of S1's own corrected cache_creation — nowhere near dominant either way. |
| H4 JUDGE_REREAD | **REFUTED for this sample** (unchanged) | Exact: 0 of S1–S5's unique requests carry a `fable-judge` attribution; the 3 heuristically Judge-like subagent spawns are the same 47,144-byte F figure already refuted as negligible under H1. |
| H5 NO_DOMINANT_SOURCE | **REFUTED** (unchanged conclusion, updated magnitude and now-understood mechanism) | Cannot be declared: cache_creation (78.1% of the corrected NOVEL_TOKEN_VOLUME, still concentrated in S1) remains a real, exact, large phenomenon with no clean A–G content-category mapping. Its *mechanism* is now well understood (3 diagnosed cache-miss events + ordinary incremental growth), but mechanism is not content attribution — declaring "no dominant source" would still mean ignoring the largest number in the dataset. |

## Terminal attribution verdict

```text
ATTRIBUTION_INCOMPLETE
```

Reason: the pre-declared dominance rule (≥30% and ≥1.5× runner-up) still
cannot be safely evaluated over the A–G content categories, because the
largest exact-token phenomenon — cache_creation, 78.1% of the corrected
NOVEL_TOKEN_VOLUME — has no exact content-category attribution available
from transcript data alone. Request-level correction changed the magnitude
(78.1% vs the original 79.5%) and, via `cache-miss-diagnostic-r1` and the
corrected `cache-reuse-churn-r1`, resolved *why* the cache_creation happened
(3 diagnosed miss events plus ordinary incremental growth, not mysterious
repeated churn) — but it did not resolve *what content category* the
resulting tokens belong to, which is the specific question this dominance
rule needs answered and remains NOT MEASURABLE without a tokenizer.

**What is safely established, independent of that open question:**
subagent-return compression would still save approximately nothing in the
sessions that cost the most (H1, unchanged). The tool-output-variability
mechanism floated as a candidate direction for S1's cache-creation is now
actively contradicted, not merely unconfirmed (H2, strengthened by this
correction) — any future investigation of S1-shaped sessions should start
from idle-gap cache eviction and system/skill-context changes, not tool
output size.

## Required correction comparison

| Metric | LINE_RECORD_METHOD (superseded) | REQUEST_LEVEL_METHOD (corrected) | MATERIAL_IMPACT |
|---|---:|---:|---|
| S1 main-thread calls | 402 | 162 | YES |
| S1 cache_creation tokens | 4,841,260 | 1,564,018 | YES |
| Total cache_creation (S1–S5) | 8,405,342 | 2,856,085 | YES |
| Total NOVEL_TOKEN_VOLUME (S1–S5) | 10,578,003 | 3,658,098 | YES |
| cache_creation share of NOVEL_TOKEN_VOLUME | 79.5% | 78.1% | NO (small shift) |
| Terminal verdict | ATTRIBUTION_INCOMPLETE | ATTRIBUTION_INCOMPLETE | NO (same label, materially different reasoning — see above) |

The 402/4,841,260/8,405,342/10,578,003 figures above are the superseded
line-record interpretation, retained here only for this comparison; they no
longer describe a current measurement anywhere else in this report.

## Candidate follow-up directions (not authorized by this task, not a new goal)

- Instrument or replay a sample of S1-shaped sessions with an actual
  tokenizer to split `cache_creation` by content role (D vs E vs C-text) —
  still the one measurement this task (and its correction) could not make
  exact.
- The Bash-stdout-variability candidate direction from the original report
  is superseded by this correction's evidence (see H2) and should not be the
  starting point for a future investigation; idle-gap cache eviction and
  system/skill-context changes are the evidenced mechanisms instead.

These are directions for a possible next measurement task, not an
optimization implementation, and not an instruction to open one.

## Privacy / scope compliance

- No transcript text, prompt text, user/assistant quotations, session IDs,
  UUIDs, or raw filenames appear in this report or in `metrics.json`.
- No production-workspace transcript directory was read.
- No file outside `analysis/token-attribution-r1/` was written.
- No file under `fable-method/` or `prompt/` was modified.
- `analysis/cache-miss-diagnostic-r1/` was read for cross-reference only and
  is unmodified.
