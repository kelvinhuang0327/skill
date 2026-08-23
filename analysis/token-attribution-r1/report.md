# FABLE_TOKEN_COST_ATTRIBUTION_R1 — Report

Measurement only. No optimization implemented. All numbers below are
reproduced by `measure.py`, run twice against the same frozen input boundary
(byte-identical output both times).

## Reproduction

```bash
python3 measure.py \
  --cutoff 2026-08-23T12:05:44Z \
  --exclude-session <this-measurement-session-id> \
  --out metrics.json
```

`metrics.json` in this directory is the exact captured output of that run.
Run it twice; the two outputs are byte-identical (verified).

## Sample

- TOTAL_SESSION_COUNT: 28 (all `*.jsonl` files under the authorized transcript
  directory as of the frozen cutoff)
- EXCLUDED_SESSION_COUNT: 4 — the session performing this measurement, plus
  sessions with no parseable main-thread usage record after the cutoff
- ELIGIBLE_SESSION_COUNT: 24
- TOP_N: 5, ranked by CONTEXT_HIGH_WATER (main-thread only)
- MEASUREMENT_CUTOFF: `2026-08-23T12:05:44Z` — frozen before inspecting any
  session content, satisfying the reproducibility requirement
- Sessions are referred to only as S1–S5 (rank order). No session ID,
  filename, or UUID appears in this report or in `metrics.json` (verified by
  pattern scan — zero UUID-shaped strings in the committed metrics file).
- The previously-discussed "~344k/475k" sessions are plausibly among S2–S5 by
  magnitude, but exact correspondence is not claimed — anonymization is not
  broken to check.

## Two headline metrics — kept separate as required

| | S1 | S2 | S3 | S4 | S5 |
|---|---:|---:|---:|---:|---:|
| CONTEXT_HIGH_WATER (tokens) | 637,127 | 284,255 | 262,736 | 256,177 | 246,881 |
| NOVEL_TOKEN_VOLUME (tokens) | 5,695,587 | 1,177,269 | 1,838,765 | 1,119,375 | 747,007 |
| main-thread calls | 402 | 122 | 202 | 100 | 126 |

**CONTEXT_HIGH_WATER** = max per-call `(input + cache_read + cache_creation)`.
This is the number that produces figures like "637k tokens" — a single
call's prompt size, not a spend total.

**NOVEL_TOKEN_VOLUME** = sum over the session of `(input + cache_creation +
output)`, deliberately excluding `cache_read` (replayed, not newly
processed). This is not a monetary cost. `MONETARY_COST: NOT MEASURABLE` —
no billing data exists in the transcript and none was looked up.

Raw components (sum across S1–S5, exact from `usage`):

| Component | Tokens | Share of NOVEL_TOKEN_VOLUME |
|---|---:|---:|
| cache_creation | 8,405,342 | 79.5% |
| output | 2,165,311 | 20.5% |
| uncached input | 7,350 | 0.07% |
| **NOVEL_TOKEN_VOLUME total** | **10,578,003** | 100% |

This 3-way split is a different axis from the A–G attribution categories
below (it is "what kind of usage counter," not "what kind of content"). It is
reported here because it drives the single most important finding in this
report (see "S1 cache-creation anomaly").

## H1 — subagent return (early falsification, run first)

- Actual subagent-spawn tool name, derived from data (not assumed): **`Agent`**.
  Derivation rule: a tool_use is a spawn call iff its paired `toolUseResult`
  carries `agentId` + `totalToolUseCount` — a structural signature, not a
  hardcoded name match. Confirmed empirically; a prior casual `grep` for the
  literal string `"Task"` in this same transcript store returns zero hits,
  and `Agent` is the only tool name ever paired with that signature.
- `isSidechain: true` **never occurs** anywhere in the authorized transcript
  store — 0 of 4,991 lines across all 28 files. Subagent internal turns are
  not written into the parent transcript at all in this logging setup; only
  the final return (via `toolUseResult`) reaches the main thread.
  **Correction to a prior informal observation**: an earlier ad hoc `grep -c
  'isSidechain'` on one file reported "424," but that counts every line
  carrying the *field name* (present on ~all lines, value almost always
  `false`), not lines where the value is `true`. That number was never a
  spawn or sidechain count. Flagging this because it's exactly the kind of
  proxy-for-the-real-thing error this task exists to prevent.
- SUBAGENT_SPAWN_COUNT across S1–S5: **3 total** (S1: 0, S2: 0, S3: 1, S4: 2,
  S5: 0).
- SUBAGENT_RETURN_PAYLOAD_BYTES: 47,144 bytes combined (exact, UTF-8).
- SUBAGENT_SPAWN_PROMPT_BYTES: 37,219 bytes combined (exact).

**H1 SUBAGENT_RETURN: REFUTED.**
`SUBAGENT_COMPRESSION_EXPECTED_SAVING: NEGLIGIBLE_FOR_OBSERVED_TOP_SESSIONS` —
the two highest-cost sessions (S1, S2) spawned zero subagents; the entire
top-5 combined subagent-return payload (47KB) is a small fraction of any
other measured category. `FABLE_SUBAGENT_EVIDENCE_COMPRESSION_R1` stays
correctly withdrawn on this evidence. Continuing to H2–H5 as required.

## Attribution categories (A–G)

Two different units are used and never mixed into one "share": exact tokens
where the data supports it, otherwise exact UTF-8 payload bytes. No
byte→token conversion estimate was applied anywhere.

| Cat | Label | Measurement | Unit | Status | Limitation |
|---|---|---:|---|---|---|
| A | ALWAYS_LOADED | 53,958 | tokens | Measurable (1 session only) | Only S1 was a cold start (`cache_read==0` on its first call); S2–S5 are resumed sessions, so their individual baseline is NOT MEASURABLE from that session alone — not borrowed from S1. |
| B | USER_TURNS | 118,442 | bytes | Measured | Text blocks under `role=user`, excluding tool_result blocks. |
| C | ASSISTANT_TEXT | 238,124 | bytes | Measured | `type=text` blocks under `role=assistant`. |
| C | ASSISTANT_THINKING (bytes) | 2,119,551 | bytes | Measured | `type=thinking` blocks. |
| C | ASSISTANT_THINKING (exact tokens) | 912,137 | tokens | Measured, partial coverage | `usage.output_tokens_details.thinking_tokens`, present on 547/952 main-thread assistant calls in S1–S5, absent on 405 (all 405 absences are S1 — see below). |
| D | TOOL_USE_INPUTS | 426,099 | bytes | Measured | Broken down by tool name below. |
| E | TOOL_RESULT_PAYLOADS | 887,517 | bytes | Measured | Broken down by tool name below; excludes subagent-identified results (counted in F instead). |
| F | SUBAGENT_RETURNS | 47,144 | bytes | Measured (see H1) | — |
| G | JUDGE_RELATED | 0 | tokens (exact, lower bound) | Measured, likely incomplete | `attributionSkill=='fable-judge'` tags 0 main-thread lines within S1–S5 (it tags only 15 lines total across the *entire* 28-session store, none of which fall in the top 5). 3 of the 5 subagent spawns *heuristically* look Judge-related by prompt content (`judge_like`), contributing the same 47,144 bytes already counted in F. `NOT MEASURABLE` beyond this: whether Judge-triggered *main-thread* reasoning outside those 15 tagged lines exists. |

**D breakdown by tool (bytes, sum across S1–S5):**
Bash 278,657 · Agent 59,263 · Write 35,034 · Skill 19,383 · Edit 22,478 ·
AskUserQuestion 6,523 · Read 4,552 · ScheduleWakeup 209.

**E breakdown by tool (bytes, sum across S1–S5, excludes subagent results):**
Bash 676,990 · Read 200,527 · Edit 5,922 · Write 1,372 · AskUserQuestion 1,486
· Agent 1,088 · Skill 87 · ScheduleWakeup 45.

## The byte-vs-token trap, caught by this task's own guardrail

By raw bytes, `ASSISTANT_THINKING` looks dominant: 2,119,551 bytes is 54.7% of
the ~3.87M-byte grand total across B/C/D/E/F, comfortably clearing the
30%-and-1.5×-runner-up rule.

**That conclusion does not survive exact-token measurement.** All 2,119,551
thinking bytes come from S2–S5 — S1 (54% of the entire top-5
NOVEL_TOKEN_VOLUME) emitted the `thinking` content type on **zero** of its
402 calls (`thinking_tokens_field_absent_count: 402` for S1 specifically).
Measured in exact tokens instead of bytes, `ASSISTANT_THINKING` is
912,137 / 10,578,003 = **8.6%** of NOVEL_TOKEN_VOLUME — nowhere near
dominant. The byte-based ranking and the token-based ranking disagree because
they are drawn from different, non-overlapping populations of calls. Per the
packet's explicit rule, the byte figure is reported above labeled as bytes;
it is not permitted to stand in for a token-dominance claim, and here doing
so would have been actively wrong.

## S1 cache-creation anomaly (exact tokens, unresolved category)

S1 alone accounts for 4,841,260 of the 8,405,342 total cache_creation tokens
across S1–S5 (57.6%), spread over 402 calls — an average of ~12,048 *new*
tokens written to cache on nearly every single call. Its own cold-start
floor (category A) is only 53,958 tokens: **1.1%** of its total
cache_creation. The remaining 98.9% is fresh cache-creation happening
*repeatedly, turn after turn*, not a one-time load.

This is the largest single exact-token phenomenon found in this dataset —
larger than every A–G category's token-measurable share — but it cannot be
assigned to one A–G category without a real tokenizer, because
`cache_creation` on any given call is the token-cost of *whatever new
content* (some mix of D, E, C-text) wasn't already covered by the existing
cache prefix. No per-category exact split of a `cache_creation` value is
available from transcript usage data, and estimating one from bytes is
exactly what this task was told not to do.

**What this finding does support, at byte precision only (not claimed as
token-exact):** E (tool result payloads, 887,517 bytes) is the largest
measured byte category after thinking, and S1's 402-call, high-frequency-churn
shape is consistent with tool-output variability (e.g. Bash stdout that
differs every call) repeatedly invalidating the cache prefix. This is
plausible, not proven — recorded as a candidate direction only.

## Hypothesis verdicts

| Hypothesis | Verdict | Basis / unit |
|---|---|---|
| H1 SUBAGENT_RETURN | **REFUTED** | Exact: 3 spawns, 47,144 bytes, across the 2 highest-context sessions (S1, S2) zero spawns occurred. |
| H2 MAIN_THREAD_RETRIEVAL | **NOT MEASURABLE** (at token precision) | E is the largest measured *byte* category after thinking (887,517 bytes, 22.9% of the B–F byte total) and is structurally consistent with the S1 cache-creation anomaly, but no exact token share exists without a tokenizer — reported as suggestive, not confirmed. |
| H3 ALWAYS_LOADED | **REFUTED** | Exact: the one measurable cold-start floor (53,958 tokens) is 0.51% of that same session's NOVEL_TOKEN_VOLUME — nowhere near dominant, and it cannot explain the repeated per-turn cache-creation churn (S1's floor is 1.1% of its own total cache_creation). |
| H4 JUDGE_REREAD | **REFUTED for this sample** | Exact: 0 of 15 dataset-wide `fable-judge`-attributed lines fall within S1–S5; the 3 heuristically Judge-like subagent spawns are the same 47,144-byte F figure already refuted as negligible under H1. |
| H5 NO_DOMINANT_SOURCE | **REFUTED** | Cannot be declared: the S1 cache-creation anomaly (79.5% of total NOVEL_TOKEN_VOLUME, concentrated in one session) is a real, exact, large, *unattributed* phenomenon. Declaring "no dominant source" would ignore it rather than resolve it. |

## Terminal attribution verdict

```text
ATTRIBUTION_INCOMPLETE
```

Reason: the pre-declared dominance rule (≥30% and ≥1.5× runner-up) cannot be
safely evaluated because the largest exact-token phenomenon found —
repeated, per-call cache-creation churn concentrated in S1, 79.5% of total
NOVEL_TOKEN_VOLUME across the sample — has no exact A–G category attribution
available from transcript data alone. A material category (the true
composition of that churn: tool-result-driven, tool-input-driven, or
something else) could reverse or confirm H2, and no defensible number exists
for it yet. Reporting `NO_DOMINANT_SOURCE` here would mean ignoring the
largest number in the entire dataset; reporting `FIRST_MATERIAL_CONTEXT_SOURCE`
for any A–G category would mean forcing a winner the token data does not
support (H1 refuted exactly, H3 refuted exactly, H4 refuted exactly, C
refuted exactly at token precision despite looking dominant in bytes).

**What is safely established, independent of that open question:**
subagent-return compression (the withdrawn R1 proposal) would save
approximately nothing in the sessions that actually cost the most. Any
future optimization goal should not restart from that proposal.

## Candidate follow-up directions (not authorized by this task, not a new goal)

- Instrument or replay a sample of S1-shaped sessions with an actual
  tokenizer to split `cache_creation` by content role (D vs E vs C-text),
  which is the one measurement this task could not make exact.
- Investigate whether large/variable Bash stdout is preventing prompt-cache
  prefix reuse in high-call-count sessions — the mechanism, not just the
  category, since the packet's data alone can show correlation but not cause.

These are directions for a possible next measurement task, not an
optimization implementation, and not an instruction to open one.

## Privacy / scope compliance

- No transcript text, prompt text, user/assistant quotations, session IDs,
  UUIDs, or raw filenames appear in this report or in `metrics.json` (pattern
  scan for UUID-shaped strings in `metrics.json`: 0 matches).
- No production-workspace transcript directory was read.
- No file outside `analysis/token-attribution-r1/` was written.
- No file under `fable-method/` or `prompt/` was modified.
