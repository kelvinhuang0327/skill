# FABLE_CACHE_REUSE_CHURN_RCA_R1 — Report

RCA / measurement only. No Fable, Planner, Judge, subagent, or cache
configuration behavior was modified. `analysis/token-attribution-r1/**`
was read, not written.

## Correction note (FABLE_REQUEST_LEVEL_MEASUREMENT_CORRECTION_R1)

This analysis originally treated usage-bearing assistant JSONL records as
independent model requests. Later request-level analysis
(`analysis/cache-miss-diagnostic-r1/`) demonstrated that multi-content-block
responses can create multiple records sharing one `requestId`. This version
deduplicates by requestId before building the turn sequence
(`build_deduplicated_turns_and_events` replaces `build_turns`); every
downstream computation is otherwise unchanged. The headline consequence:
**the "three tight repeat-bursts" this report originally described do not
exist** — they were consecutive duplicate JSONL lines of the same single
real request being compared against each other as if they were separate
calls. The burst detector, unmodified, now finds zero burst groups on the
same underlying data (see below).

## Request identity verification

| | Value |
|---|---:|
| USAGE_RECORD_COUNT | 402 |
| UNIQUE_REQUEST_COUNT | 162 |
| DUPLICATED_REQUEST_RECORD_COUNT | 240 |
| MISSING_REQUEST_ID_COUNT | 0 |
| CONFLICTING_USAGE_WITHIN_REQUEST_COUNT | 0 |
| NONCONTIGUOUS_REQUESTID_GROUPS | 0 |

`requestId` is present on every usage-bearing record; zero groups contain
conflicting usage (no arbitrary-row-choice case arises); zero groups are
split across non-adjacent raw lines (confirms a tool_result never
interleaves inside one request's own multi-line span, which is what makes
the simple "one events-between bucket per request" construction valid here).
These figures are independently reproduced by this script and match
`analysis/cache-miss-diagnostic-r1/`'s figures for the same session exactly
— two independently written analyzers converging on the same ground truth.

## Base and target

- Prior commit used as base: `1bc1025` (`FABLE_TOKEN_COST_ATTRIBUTION_R1`),
  confirmed an ancestor of live HEAD before starting.
- Measurement cutoff reused unchanged from the prior task:
  `2026-08-23T12:05:44Z`.
- Target session ("S1") re-derived independently by this task's own script
  using the identical selection rule as the prior task. Re-derived
  `CONTEXT_HIGH_WATER = 637,127` tokens matches both prior reports exactly.
- **162 real requests** (402 raw JSONL lines), total `cache_creation =
  1,564,018` tokens — corrected from the original 402/4,841,260 (see
  "Required correction comparison").

## Reproduction

```bash
python3 measure_cache_churn.py \
  --cutoff 2026-08-23T12:05:44Z \
  --exclude-session <this-measurement-session-id> \
  --out metrics.json
```

Run twice against the same cutoff; both runs produced byte-identical output
(verified).

## The mandatory distinction, checked first

> "high cache_creation + high prior-prefix cache_read" would mean a growing
> tail, not a cache failure.

That is still not what was found, at request level either.
`PRIOR_PREFIX_REUSE_RATIO` for this session's 162 real requests remains
sharply **bimodal**: 158 sit at or above 0.80 (near-perfect reuse), and
**3** sit at or below 0.1584 with `cache_creation_tokens` simultaneously at
or above 50% of the entire prior context (a full or near-full rebuild). Zero
requests land in between (`PARTIAL_REUSE` share of cache_creation = 0.0%).
The bimodal shape survives correction; what changes is the count on the
"miss" side — 3 real misses, not 12 line-counted ones.

## Cache-creation-weighted classification

| Reuse state | Request count | Share of total cache_creation |
|---|---:|---:|
| NORMAL_REUSE_CANDIDATE (ratio ≥ 0.80) | 158 | 30.66% |
| PARTIAL_REUSE | 0 | 0.0% |
| MATERIAL_PREFIX_LOSS_CANDIDATE (ratio < 0.50 and cache_creation ≥ 50% of prior) | 3 | 65.89% |
| INITIAL_TURN_NO_PRIOR (cold start) | 1 | 3.45% |

```text
CACHE_CHURN_MECHANISM: MIXED_CACHE_BEHAVIOR
```

65.89% falls just under the pre-declared 70% dominance threshold — the
mechanism no longer meets `PREFIX_REBUILD_DOMINANT` on its own numeric
terms. The practical finding (3 large real misses dominate token cost far
more than any of the 158 healthy requests individually) is unchanged in
substance; it is reported exactly, not rounded to fit either label. This
matches `analysis/cache-miss-diagnostic-r1/`'s independently computed
30.66%/65.89%/0%/3.45% split exactly.

## The dominant structure, corrected: zero repeat-bursts, not three

The original report's central finding was "three tight repeat-bursts... as
if the immediately preceding write were invisible" — consecutive turns that
rebuild an almost identical-sized cache seconds apart with nothing new in
between. The burst detector that found that pattern is **unmodified** by
this correction. Run against the deduplicated 162-request sequence, it now
finds:

| Variant | Burst groups | Requests involved | Cache_creation "wasted" on repeats |
|---|---:|---:|---:|
| STRICT | **0** | 0 | 0 (0.0%) |
| LOOSE | **0** | 0 | 0 (0.0%) |

This is not a reclassification — it is the same detector, same thresholds,
finding nothing, because the pattern it was built to catch (near-identical
cache_creation with near-zero intervening bytes, in immediate succession)
is exactly what a single API response's split JSONL lines look like when
miscounted as separate calls. The three turn-triples the original report
named (line-indices, not request-indices) were the three material events'
own 3–4 split lines each. Deduplicated, each is exactly **one** request:

| Request (turn index) | cache_creation | cache_read | Reuse ratio | Gap before |
|---:|---:|---:|---:|---:|
| 48 | 191,282 | 32,846 | 0.1584 | 2,533s (42 min) |
| 61 | 275,806 | 0 | 0.0 | 9,521s (2.6 hr) |
| 137 | 563,442 | 0 | 0.0 | 3,884s (65 min) |

There is no "second and third call, 3–9 seconds later, that fails to read
the immediately preceding write" — there was never a second or third call.
The next real request after each of these three reads the full amount back
correctly (confirmed independently in `analysis/cache-miss-diagnostic-r1/`,
which additionally cross-references each event against the API's own
`cache_miss_reason` field — not duplicated here).

## Hypothesis verdicts (C1–C7)

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| C1 | NORMAL_INCREMENTAL_REUSE | **REFUTED** (unchanged conclusion) | Normal-reuse requests hold 30.66% of cache_creation, still far under the 70% bar; still bimodal, not gradual. |
| C2 | PREFIX_REBUILD | **NOT CONFIRMED at the pre-declared threshold** (changed from CONFIRMED) | 65.89% of cache_creation occurs in the 3 requests with `PRIOR_PREFIX_REUSE_RATIO` ≤ 0.1584 — under the pre-declared 70% bar. The original 71.79% CONFIRMED verdict was computed from line-duplicated data (see "Required correction comparison"); the corrected figure does not clear the bar the original verdict rested on. |
| C3 | CONTEXT_REWRITE_OR_COMPACTION | **NOT MEASURABLE** (structural) / **REFUTED** (heuristic) — unchanged | Re-verified independently: no key matching `compact`/`summar`/`truncat`/`reset` exists in any structural field across all 995 raw lines in the session (0 matches; this raw-line total is unaffected by request-level dedup, since it counts every JSONL line, not just usage-bearing assistant ones). `COMPACTION_CANDIDATE` fires on 0 of 161 eligible deduplicated requests (was 0 of 401 line-counted turns — same conclusion, corrected denominator). |
| C4 | MODEL_OR_RUNTIME_SHAPE_CHANGE | **REFUTED** (unchanged) | `model` is `claude-opus-5` on all 162 real requests (zero transitions); `service_tier` is `standard` throughout; exclusively `ephemeral_1h` cache-breakpoint usage (162/162); `attributionSkill` is `fable-method` throughout. |
| C5 | TIME_GAP_EFFECT | **TIME_GAP_CORRELATED for 2 of 3, with the 3rd independently explained by a different, non-competing mechanism** (sharpened from "partial") | The entry-gap figures were never affected by the duplication bug (they compare a burst's first line against its true predecessor, which the bug never touched) — 2 of the 3 real material events (turns 61, 137) begin after gaps exceeding 3,600s; the 3rd (turn 48) begins after 2,533s. `analysis/cache-miss-diagnostic-r1/` independently resolves this: the two >3,600s events both carry `cache_miss_reason=previous_message_not_found` (0 counterexamples in either direction across the session), while turn 48 carries `cache_miss_reason=system_changed` — a distinct, separately-diagnosed cause, not a stray counterexample to the time-gap pattern. |
| C6 | TOOL_RESULT_GROWTH | **REFUTED** (strengthened) | Recomputed on deduplicated data: for every observed tool (Bash, Read, Edit, Write, AskUserQuestion), the material-event rate immediately after that tool is used is **exactly 0%**, versus 1.9–5.3% when not preceded by that tool — the opposite of what "large tool output invalidates the cache" predicts, and a cleaner (all-zero) result than the original line-counted data showed. |
| C7 | CACHE_MECHANISM_NOT_IDENTIFIABLE | **REFUTED** (changed from "confirmed for one sub-question") | The original sub-question this hypothesis partially confirmed — "why does the immediate repeat fail to read the cache its predecessor just wrote" — no longer applies: there was never a repeat to explain (see above). The actual mechanism for the 3 real events is directly identifiable from first-party evidence (`cache_miss_reason`, per `analysis/cache-miss-diagnostic-r1/`), not merely "not identifiable from transcript-only evidence" as originally concluded. |

## Root-cause verdict

```text
CACHE_CHURN_MECHANISM: MIXED_CACHE_BEHAVIOR
```

The original `ROOT_CAUSE_NOT_IDENTIFIED` verdict rested entirely on
condition 5 failing because "the within-burst repeat-miss mechanism has a
plausible unmeasured competing explanation (client-side retry, a
server-side cache-propagation gap) that this transcript cannot rule out."
That premise is dissolved by request-level correction: there was no
within-burst repeat, so there is nothing for a retry or propagation-gap
hypothesis to compete to explain. This script's own corrected data
independently confirms three of the five original root-cause conditions
even more cleanly than before (mechanism measurable: yes; control comparison
discriminates against C1/C4/C6: yes, now with a cleaner all-zero C6 result;
competing hypotheses falsified: yes — C1, C4, C6, and now C7 as originally
framed). This document does not re-derive a positive root-cause verdict for
*why* the 3 real requests individually missed the cache — that specific
question was the deeper mandate of, and is directly answered by, the
separate `analysis/cache-miss-diagnostic-r1/` task (D1 CONFIRMED via the
API's own `cache_miss_reason` field), which this report defers to rather
than duplicates.

## Material-event and control comparison (full magnitude-ranked set)

The magnitude-ranked `MATERIAL_CACHE_EVENTS` set (minimal set covering ≥80%
of cache_creation by raw size — a broader, differently-defined set than the
3 reuse-ratio-classified events above, since it also includes
large-absolute-value `NORMAL_REUSE_CANDIDATE` requests) is now **25
requests covering 80.02%** (corrected from 51 turns / 80.12% — the old count
included duplicate lines of the same large request appearing as separate
"top" entries):

| | MATERIAL_CACHE_EVENTS (n=25) | Normal-reuse controls (n=158) |
|---|---:|---:|
| avg time gap from previous request | 906.9s | 97.2s |
| median time gap | 55.1s | 26.5s |
| avg tool-result bytes since previous call | 4,340.0 | 2,137.9 |
| avg subagent-return bytes since previous call | 0.0 | 0.0 |
| skill-attribution-change prevalence | 4.0% | 0.63% |
| model-change prevalence | 0.0% | 0.0% |

## Time-gap buckets (all 162 real requests)

| Bucket | Requests | Material requests | Material share of bucket | Cache_creation sum |
|---|---:|---:|---:|---:|
| 0–10s | 14 | 0 | 0.0% | 41,250 |
| 10–60s | 116 | 0 | 0.0% | 334,220 |
| 60–300s | 15 | 0 | 0.0% | 34,315 |
| 300–1,800s | 12 | 0 | 0.0% | 62,022 |
| ≥1,800s | 4 | 3 | **75.0%** | 1,038,253 |

(A `NOT_MEASURABLE` bucket of 1 — the cold-start request with no prior turn
— is omitted from percentages, consistent with the original report.) The
0–10s bucket collapsed from 236 line-counted turns to 14 real requests: most
of the old "near-instant" turns were duplicate lines of the same response
logged back to back, not 236 genuinely rapid-fire model calls. The ≥1,800s
bucket's 75% material share is unchanged in substance (same 3 real events,
same 4-request bucket).

## Tool-result association (session-wide, all 162 real requests)

| Tool | Requests preceded by this tool | Material-event rate when preceded | Material-event rate when not preceded | Result bytes | Share of tool-result bytes |
|---|---:|---:|---:|---:|---:|
| Bash | 104 | 0.0% | 5.26% | 203,310 | 60.2% |
| Read | 18 | 0.0% | 2.1% | 126,928 | 37.6% |
| Edit | 13 | 0.0% | 2.03% | 5,283 | 1.6% |
| AskUserQuestion | 3 | 0.0% | 1.9% | 1,314 | 0.4% |
| Write | 4 | 0.0% | 1.91% | 958 | 0.3% |

Result-byte totals and shares are byte-based and unchanged from the original
report (each tool_result is one distinct event regardless of request-line
duplication). The "preceded by this tool" counts and material-event rates
are corrected. Every tool now shows an exact 0% material-event rate when
immediately preceding a request — a cleaner refutation of C6 than the
original line-counted data (which showed a low but nonzero 1.68% for Bash,
itself likely a residual line-duplication artifact).

## Compaction / reset

Unchanged conclusion, re-verified independently against the same 995 raw
lines (this total does not depend on request-level deduplication — it counts
every JSONL line in the session, not just usage-bearing assistant ones): 0
structural keys matching `compact`/`summar`/`truncat`/`reset` anywhere.
`COMPACTION_CANDIDATE` fires on 0 of 161 eligible deduplicated requests
(corrected denominator; was 0 of 401 line-counted turns). No
compaction-candidate evidence in this session at all, confirmed rather than
assumed absent, at request level.

## Required correction comparison

| Metric | LINE_RECORD_METHOD (superseded) | REQUEST_LEVEL_METHOD (corrected) | MATERIAL_IMPACT |
|---|---:|---:|---|
| Main-thread call count | 402 | 162 | YES |
| Total cache_creation tokens | 4,841,260 | 1,564,018 | YES |
| MATERIAL_PREFIX_LOSS_CANDIDATE count | 12 | 3 | YES |
| MATERIAL_PREFIX_LOSS_CANDIDATE share of cache_creation | 71.79% | 65.89% | YES |
| Mechanism classification | PREFIX_REBUILD_DOMINANT | MIXED_CACHE_BEHAVIOR | YES |
| Repeated-rebuild burst groups (STRICT) | 3 | 0 | YES |
| Magnitude-ranked material-event count (≥80% coverage) | 51 | 25 | YES |
| Root-cause verdict | ROOT_CAUSE_NOT_IDENTIFIED | premise dissolved; deeper cause answered by cache-miss-diagnostic-r1 | YES |

The 402/4,841,260/12/71.79%/PREFIX_REBUILD_DOMINANT/3-bursts/51-events
figures above are the superseded line-record interpretation, retained here
only for this comparison; they no longer describe a current measurement
anywhere else in this report.

## Privacy / scope compliance

- No session ID, UUID, transcript filename, command text, tool argument
  text, tool result text, or prompt text appears in this report, in
  `measure_cache_churn.py`, or in `metrics.json` (pattern-scanned for
  UUID-shaped strings: 0 matches across both files).
- `analysis/token-attribution-r1/**` was read for context and is unchanged.
- `analysis/cache-miss-diagnostic-r1/**` was read for cross-reference only
  and is unmodified.
- No file under `fable-method/` or `prompt/` was modified.
- No file outside `analysis/cache-reuse-churn-r1/` was written.
- No production-workspace transcript directory was read.

## NEXT_MEASUREMENT_DIRECTION — resolved

The original next-step direction asked for request-level correlation data
to determine whether the within-burst repeat-miss was a retry, a
concurrent/parallel dispatch, or a genuine cache-propagation gap. That
question is now moot: there was no within-burst repeat (see above). The
`requestId` field this correction used to prove that is itself the
request-level correlation data the original direction asked for; no further
measurement task is needed on this specific question. `analysis/cache-miss-diagnostic-r1/`
separately and more deeply answers the adjacent "why did each real request
individually miss the cache" question via the API's own `cache_miss_reason`
field.
