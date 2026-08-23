# FABLE_CACHE_REUSE_CHURN_RCA_R1 — Report

RCA / measurement only. No Fable, Planner, Judge, subagent, or cache
configuration behavior was modified. `analysis/token-attribution-r1/**`
was read, not written.

## Base and target

- Prior commit used as base: `1bc1025` (`FABLE_TOKEN_COST_ATTRIBUTION_R1`),
  confirmed an ancestor of live HEAD before starting.
- Measurement cutoff reused unchanged from the prior task:
  `2026-08-23T12:05:44Z`.
- Target session ("S1") re-derived independently by this task's own script
  using the identical selection rule as the prior task (top session by
  CONTEXT_HIGH_WATER, excluding the measuring session) — not copied from the
  prior artifacts. Re-derived `CONTEXT_HIGH_WATER = 637,127` tokens matches
  the prior report's S1 value exactly, confirming the two tasks are looking
  at the same session without either one persisting its identifier anywhere.
- 402 main-thread calls, total `cache_creation = 4,841,260` tokens (matches
  prior report).

## Reproduction

```bash
python3 measure_cache_churn.py \
  --cutoff 2026-08-23T12:05:44Z \
  --exclude-session <this-measurement-session-id> \
  --out metrics.json
```

Run twice against the same cutoff; both runs produced byte-identical output
(verified, including after two subsequent script revisions made while
investigating the data — each revision was re-verified for determinism
before use).

## The mandatory distinction, checked first

> "high cache_creation + high prior-prefix cache_read" would mean a growing
> tail, not a cache failure.

That is not what was found. `PRIOR_PREFIX_REUSE_RATIO` for this session is
sharply **bimodal**, not a smooth gradient: 389 of 402 turns sit at or above
0.80 (near-perfect reuse), and 12 sit at exactly **0.0** with
`cache_creation_tokens` simultaneously at or above 50% of the entire prior
context (a full rebuild). Zero turns land in between
(`PARTIAL_REUSE` share of cache_creation = 0.0%). This session does not have
a "somewhat leaky" cache — it has occasional complete, total misses.

## Cache-creation-weighted classification

| Reuse state | Turn count | Share of total cache_creation |
|---|---:|---:|
| NORMAL_REUSE_CANDIDATE (ratio ≥ 0.80) | 389 | 27.1% |
| PARTIAL_REUSE | 0 | 0.0% |
| MATERIAL_PREFIX_LOSS_CANDIDATE (ratio < 0.50 and cache_creation ≥ 50% of prior) | 12 | 71.79% |
| INITIAL_TURN_NO_PRIOR (cold start) | 1 | 1.11% |

```text
CACHE_CHURN_MECHANISM: PREFIX_REBUILD_DOMINANT
```

71.79% ≥ the pre-declared 70% dominance threshold. This is measured, not
estimated — every figure above comes directly from exact `usage` token
counts, no byte-to-token conversion involved anywhere in this table.

## The dominant structure: three tight repeat-bursts, not gradual drift

Ranking all 402 turns by `cache_creation_tokens` and taking the minimal set
covering ≥80% of the session total (the required `MATERIAL_CACHE_EVENTS`
set) yields 51 turns, covering 80.12%. But most of that mass concentrates
into an even smaller, sharper structure: **consecutive turns that rebuild an
almost identical-sized cache from zero, seconds apart, with nothing new in
between.**

A burst detector (consecutive turns, cache_creation sizes within 2% of each
other, near-zero intervening tool/subagent bytes) found this, reported in
two variants side by side rather than one overwriting the other — the
thresholds were fixed only after one instance was already visible during
investigation, not frozen blind like the reuse-ratio thresholds, so both a
strict and a conservative-loose reading are shown for honesty about that:

| Variant | Intervening-byte allowance | Burst groups | Turns involved | Share of total cache_creation "wasted" on repeats alone |
|---|---:|---:|---:|---:|
| STRICT | 0 bytes | 3 | 9 | 42.6% |
| LOOSE | 5,000 bytes + allows session cold-start as an anchor | 4 | 13 | 50.5% |

The three groups both variants agree on:

| Turns | Repeats | Cache_creation per repeat | Gap before burst | Bytes between repeats |
|---|---:|---:|---:|---:|
| 121, 122, 123 | 3 | 191,282 | 2,533s (42 min) | 0 |
| 150, 151, (152, 153) | 3–4 | 275,806 | 9,521s (2.6 hr) | 0 |
| 332, 333, 334 | 3 | 563,442 | 3,884s (64.7 min) | 0 |

In every group: the second and third calls, **3–9 seconds** after the
previous one wrote a fresh cache of that exact size, show
`cache_read_input_tokens = 0` and write an almost identical-sized fresh
cache again — as if the immediately preceding write were invisible to them.
The very next turn after each burst ends reads the full amount back
correctly. **Just 9–13 of 402 turns account for 43–51% of the entire
session's cache-creation token volume.** This is a small number of sharp
mechanical events, not a diffuse accumulation.

## Hypothesis verdicts (C1–C7)

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| C1 | NORMAL_INCREMENTAL_REUSE | **REFUTED** | Normal-reuse turns hold only 27.1% of cache_creation, far under the 70% bar; the distribution is bimodal, not gradual. |
| C2 | PREFIX_REBUILD | **CONFIRMED** | 71.79% of cache_creation occurs in turns with `PRIOR_PREFIX_REUSE_RATIO = 0.0`, exceeding the pre-declared 70% threshold; concentrated in 3 tight, structurally identical repeat-bursts. |
| C3 | CONTEXT_REWRITE_OR_COMPACTION | **NOT MEASURABLE** (structural) / **REFUTED** (heuristic) | No compaction/reset record type exists anywhere in this harness's transcript schema (verified: no key containing "compact", "summar-", "truncat-", or "reset" appears on any of the session's 995 raw lines; the only "compact" substring hits are the word appearing inside ordinary prose text, 16 of them, none in a structural field). The token-count-drop heuristic (`COMPACTION_CANDIDATE`) fires on exactly **0 of 401** eligible turns — including at all three burst events, where context size stays flat or grows, it never drops. |
| C4 | MODEL_OR_RUNTIME_SHAPE_CHANGE | **REFUTED** | `model` is `claude-opus-5` on all 402 calls (zero transitions); `service_tier` is `standard` throughout; the session writes exclusively to the `ephemeral_1h` cache-breakpoint type (402/402 calls, 0 ever use `ephemeral_5m`); `attributionSkill` is `fable-method` throughout (one value, never changes). No observable request-shape property changes anywhere in the session, let alone specifically at the three burst events. |
| C5 | TIME_GAP_EFFECT | **TIME_GAP_CORRELATED (partial)** | 2 of 3 bursts (STRICT) begin after a gap exceeding 3,600 seconds, aligning with this session's exclusive use of the 1-hour-labeled cache breakpoint — stated as a correlation with the observed breakpoint type, not as a claim about undocumented TTL internals. But burst 1 begins after only 2,533s (42 min), and the LOOSE variant's earliest burst (turns 1–3) begins 2.8s into the session with no gap at all. Time gap explains at most half the bursts; it is not a complete account. |
| C6 | TOOL_RESULT_GROWTH | **REFUTED** as a trigger for material/burst events | Control comparison: for every observed tool (Bash, Read, Edit, Write, AskUserQuestion), the fraction of turns that are `MATERIAL_PREFIX_LOSS_CANDIDATE` immediately after that tool is used is **equal to or lower** than when it is not used (e.g. Bash: 1.68% vs 3.55%; Read/Edit/Write/AskUserQuestion: 0% vs ~3%) — the opposite of what a "large tool output invalidates the cache" story predicts. All three STRICT burst groups have exactly 0 tool/subagent bytes between their repeated rebuilds. Tool-result bytes remain the largest measured contributor to ordinary session-wide byte growth (consistent with the prior task's report) — that is a separate, valid finding about the tail, correctly not conflated with what triggers a prefix-rebuild. |
| C7 | CACHE_MECHANISM_NOT_IDENTIFIABLE | **CONFIRMED for one specific sub-question; REFUTED for the aggregate question** | The aggregate pattern (what dominates, how much, and its structural shape) is fully measurable and reported above. What is NOT identifiable from transcript-only evidence: why the 2nd and 3rd call in each burst fail to read a cache their immediate predecessor wrote 3–9 seconds earlier. No request-correlation id, retry/error metadata, or server-side cache-hit telemetry exists in this transcript schema to distinguish a client-side retry, concurrent/parallel dispatch, or a genuine cache-propagation gap. |

## Root-cause verdict

```text
CACHE_CHURN_MECHANISM: PREFIX_REBUILD_DOMINANT
ROOT_CAUSE_NOT_IDENTIFIED
```

A named `CACHE_CHURN_ROOT_CAUSE` is not reported. Checking the packet's five
conditions explicitly: the mechanism is measurable (yes); a candidate
trigger — idle gap — repeatedly aligns with material events (partially: 2 of
3–4, not all); control comparison supports discrimination (yes — it
actively discriminates *against* C1, C4, and C6); at least one competing
hypothesis is falsified (yes — three are: C1, C4, C6 all cleanly refuted);
**but** no material unmeasured factor could equally explain the result
(**fails** — the within-burst repeat-miss mechanism has a plausible
unmeasured competing explanation, e.g. client-side retry or a server-side
cache-propagation gap, that this transcript cannot rule out). Condition 5
fails, so `ROOT_CAUSE_NOT_IDENTIFIED` is the correct, evidence-bound
verdict rather than `PREFIX_REBUILD_DOMINANT` alone.

This still substantially narrows the space: three specific, sharply-defined,
timestamped mechanical events explain 43–51% of one session's entire novel
token cost, and four of the five most intuitive explanations for them
(gradual drift, tool-output growth, model/runtime shape change, and
compaction) are each individually and directly refuted by this session's own
data — not merely undetermined.

## Material-event and control comparison (full magnitude-ranked set)

For completeness against the required acceptance criterion, the full
magnitude-ranked `MATERIAL_CACHE_EVENTS` set (51 turns covering 80.12% of
cache_creation — a broader set than the 3 burst groups, since it also
includes some large-absolute-value `NORMAL_REUSE_CANDIDATE` turns where the
prior context was already very large) compares to normal-reuse controls as:

| | MATERIAL_CACHE_EVENTS (n=51) | Normal-reuse controls (n=389) |
|---|---:|---:|
| avg time gap from previous turn | 423.2s | 39.5s |
| median time gap | 5.7s | 7.6s |
| avg tool-result bytes since previous call | 1,413.8 | 866.3 |
| avg subagent-return bytes since previous call | 0.0 | 0.0 |
| skill-attribution-change prevalence | 1.96% | 0.26% |
| model-change prevalence | 0.0% | 0.0% |

The mean time-gap difference is driven by the handful of true burst-entry
events; the median gap is actually *similar* between the two groups (both
single-digit seconds), underscoring that most of this "material" set by
magnitude alone is not gap-related — only the true bursts are.

## Time-gap buckets (all 402 turns)

| Bucket | Turns | Material turns | Material share of bucket | Cache_creation sum |
|---|---:|---:|---:|---:|
| 0–10s | 236 | 8 | 3.4% | 3,058,525 |
| 10–60s | 133 | 1 | 0.8% | 596,146 |
| 60–300s | 16 | 0 | 0.0% | 32,356 |
| 300–1,800s | 12 | 0 | 0.0% | 62,022 |
| ≥1,800s | 4 | 3 | **75.0%** | 1,038,253 |

The ≥1,800s bucket shows a strikingly elevated material share (75%) — but
it is only 4 turns wide, so this is suggestive on a very small base, not a
large-sample statistical result. It is consistent with, and does not
contradict, the partial C5 verdict above.

## Tool-result association (session-wide, all 402 turns)

| Tool | Turns preceded by this tool | Material-event rate when preceded | Material-event rate when not preceded | Result bytes | Share of tool-result bytes |
|---|---:|---:|---:|---:|---:|
| Bash | 119 | 1.68% | 3.55% | 203,310 | 60.2% |
| Read | 26 | 0.0% | 3.20% | 126,928 | 37.6% |
| Edit | 24 | 0.0% | 3.18% | 5,283 | 1.6% |
| Write | 5 | 0.0% | 3.03% | 958 | 0.3% |
| AskUserQuestion | 3 | 0.0% | 3.02% | 1,314 | 0.4% |

No tool shows an elevated material-event rate when it immediately precedes a
turn. This is the direct evidence behind the C6 refutation above.

## Compaction / reset

`COMPACTION_OR_RESET_MARKER` is `NOT MEASURABLE` on every turn — this
transcript schema has no structural record type for it (verified against
every key name on all 995 raw lines in the session, not just the 402
model-call lines). The token-drop heuristic explicitly required by the
packet in place of that (`COMPACTION_CANDIDATE`, fired only on an actual
context-size drop, never merely inferred from a token-count change without
structural support) returns `NO` on all 401 eligible turns. There is no
compaction-candidate evidence in this session at all, confirmed rather than
assumed absent.

## Privacy / scope compliance

- No session ID, UUID, transcript filename, command text, tool argument
  text, tool result text, or prompt text appears in this report, in
  `measure_cache_churn.py`, or in `metrics.json` (pattern-scanned for
  UUID-shaped strings: 0 matches across both files).
- `analysis/token-attribution-r1/**` was read for context and is unchanged.
- No file under `fable-method/` or `prompt/` was modified.
- No file outside `analysis/cache-reuse-churn-r1/` was written.
- No production-workspace transcript directory was read.

## NEXT_MEASUREMENT_DIRECTION

```text
NEXT_MEASUREMENT_DIRECTION:
Obtain request-level correlation for the three identified burst windows
(turns 121-123, 150-153, 332-334, identified here only by turn index and
relative timestamp) - retry counts, error/stop_reason, or any client- or
server-side request-correlation data - to determine whether the
within-burst repeat-miss is a retry, a concurrent/parallel dispatch, or a
genuine cache-propagation gap. This transcript format alone cannot resolve
that question (see C7).
```

This is a direction for a possible future measurement task. It is not
authorized by this task and is not itself opened here.
