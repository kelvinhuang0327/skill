# FABLE_CACHE_MISS_DIAGNOSTIC_ATTRIBUTION_R1 — Report

Existing-evidence-only diagnostic RCA. No Fable, Planner, Judge, cache
configuration, or live skill was touched. No new debug-enabled reproduction
was run. `analysis/token-attribution-r1/**` and
`analysis/cache-reuse-churn-r1/**` were read for context and are unmodified.

## Headline result

The prior RCA's "three tight repeat-bursts, calls 3–9 seconds apart, each
failing to read the cache the immediately preceding call had just written"
is **not three-to-four repeated cache misses**. It is **one real API request
per burst**, logged as multiple separate JSONL lines because that response
contained multiple content blocks (`thinking`, `text`, `tool_use`), each
split onto its own line while sharing one `requestId` and carrying an
identical copy of that request's `usage` and `diagnostics`. There is no
"why does the second call fail to read the first call's cache" question to
answer, because there is no second call. And for the one real request each
burst actually is, Anthropic's own API tells us why it missed — directly,
by name, no inference required.

## Base and target

- Prior commits verified present on HEAD before starting: `1bc1025`,
  `361e84a`.
- Target session ("S1") re-derived independently, same rule as both prior
  tasks. Re-derived `CONTEXT_HIGH_WATER = 637,127` matches both prior
  reports exactly.
- Measurement cutoff reused unchanged: `2026-08-23T12:05:44Z`.
- Debug log evidence: `~/.claude/debug/latest` exists only as a symlink to a
  file that is not present on disk (0 files under `~/.claude/debug/`).
  `DEBUG_LOG_EVIDENCE: NOT AVAILABLE` — not a task failure, per the packet.

## Reproduction

```bash
python3 measure_cache_miss.py \
  --cutoff 2026-08-23T12:05:44Z \
  --exclude-session <this-measurement-session-id> \
  --out metrics.json
```

Run twice; both runs produced byte-identical output (re-verified again after
one script revision made mid-investigation, to add a counterexample check).

## Schema probe (required before building the analyzer)

Probed structurally against the real historical S1 records, not assumed:

| Field | Status | Detail |
|---|---|---|
| `message.diagnostics` | PRESENT | 10 of 402 lines |
| `message.diagnostics.cache_miss_reason` | PRESENT | 10 of 402 lines |
| `requestId` | PRESENT | 402 of 402 |
| `entrypoint` | PRESENT | 402 of 402, constant `claude-desktop` |
| `version` | PRESENT | 402 of 402, constant `2.1.227` |
| `message.model` | PRESENT | 402 of 402, constant `claude-opus-5` |
| `usage.service_tier` | PRESENT | 402 of 402, constant `standard` |
| `usage.speed` | PRESENT | 402 of 402, constant `standard` |
| `effort` | PRESENT | 402 of 402, constant `max` |
| `usage.cache_creation.ephemeral_1h_input_tokens` | PRESENT | 402 of 402 |
| `usage.cache_creation.ephemeral_5m_input_tokens` | PRESENT | 402 of 402, always `0` |
| `usage.iterations` | PRESENT | 402 of 402, always length 1 |
| `attributionSkill` | PRESENT | 17 of 402 (sparse; only tags fable-method-attributed turns) |
| `message.stop_reason` | PRESENT | 402 of 402 |
| `parentUuid` / `uuid` | PRESENT | 402 of 402 — used only in-process to verify request-chaining structure, never persisted |
| tool-definition/tool-schema identity | **NOT SAFELY ATTRIBUTABLE** | only tool_use *calls* are logged, never the tool schema offered to the model |
| system-context identity/fingerprint | **NOT SAFELY ATTRIBUTABLE** | the rendered system prompt is not logged as content in this format (confirmed already in the prior RCA); `cache_miss_reason.type` is the only first-party signal for this |
| retry/error fields | **NOT SAFELY ATTRIBUTABLE** | no retry count, error code, or transport-level field exists on assistant records in this schema |
| transport/client metadata | PRESENT (partial) | `entrypoint`/`version` only; no lower-level transport metadata is logged |

No request-shape hash/fingerprint was constructed for tools or system
context: the API's own `cache_miss_reason` field is a better, first-party
signal than a locally reconstructed proxy would be, and no safely
canonicalizable raw payload exists locally to hash in the first place.

## Duplicate-record guard (the load-bearing check this task required)

- 402 JSONL assistant records in S1 map to only **162 distinct `requestId`
  values**.
- Group sizes: 28 requests logged as 1 line, 54 as 2 lines, 55 as 3 lines,
  24 as 4 lines, 1 as 5 lines.
- Verified — not assumed — that every one of the 162 groups carries
  **identical** `usage` (all four token counts) **and** identical
  `entrypoint`, `version`, `model`, `effort`, `attributionSkill`,
  `service_tier`, and `diagnostics` across every member line. **Zero
  mismatched groups.** `uuid`/`parentUuid` confirm the split lines chain
  linearly within one request (verified directly for the three flagged
  bursts; the same requestId-grouping guarantee applies to all 162 groups).
- Raw (per-line) summed `cache_creation`: **4,841,260**. Deduplicated
  (per-request) summed `cache_creation`: **1,564,018**. The prior RCA's
  sum-based total was inflated **3.10×** for this session.
- `CONTEXT_HIGH_WATER` (a `max`, not a `sum`) is **unaffected** by this —
  637,127 stands exactly as reported in both prior tasks, since
  `max(x, x, x) = x` regardless of duplication.

**This is a reproducible defect in the prior task's counting methodology**
(neither prior task was asked to check per-request deduplication; this task
is the first to require it). Per this task's own instruction to flag,
rather than silently recalculate around, a found defect: `PREFIX_REBUILD_
DOMINANT` in `analysis/cache-reuse-churn-r1/report.md`, and the absolute
`cache_creation`/`NOVEL_TOKEN_VOLUME` totals in `analysis/token-attribution-
r1/report.md`, were computed by summing per-JSONL-line rather than
per-request, and are likely inflated by a similar mechanism wherever a
session's assistant turns split across multiple content blocks. Both files
are left unmodified, as this task's scope requires; this note is the
flagged correction.

## Corrected mechanism classification (deduplicated, 162 requests)

| Reuse state | Requests | Share of total cache_creation |
|---|---:|---:|
| NORMAL_REUSE_CANDIDATE | 158 | 30.66% |
| MATERIAL_PREFIX_LOSS_CANDIDATE | 3 | 65.89% |
| PARTIAL_REUSE | 0 | 0.0% |
| INITIAL_REQUEST_NO_PRIOR | 1 | 3.45% |

```text
CACHE_CHURN_MECHANISM (deduplicated): MIXED_CACHE_BEHAVIOR
```

65.89% falls just under the pre-declared 70% dominance bar the prior RCA
used — under the corrected denominator, the mechanism no longer meets the
`PREFIX_REBUILD_DOMINANT` bar on its own numeric terms, though the practical
finding (3 large real misses dominate token cost far more than 158 healthy
requests) is unchanged in substance. Reported exactly, not rounded to fit
either label.

## Material events: exactly 3, not 9–13

| Label | Content blocks in the one real request | cache_creation | cache_read | Reuse ratio | `cache_miss_reason.type` | Gap before |
|---|---|---:|---:|---:|---|---:|
| M1 | thinking, text, tool_use | 191,282 | 32,846 | 0.1584 | `system_changed` (137,955 tokens reported missed) | 2,533s (42 min) |
| M2 | thinking, text, tool_use, tool_use | 275,806 | 0 | 0.0 | `previous_message_not_found` | 9,521s (2.6 hr) |
| M3 | thinking, text, tool_use | 563,442 | 0 | 0.0 | `previous_message_not_found` | 3,884s (65 min) |

All three: `entrypoint_changed=NO`, `version_changed=NO`, `model_changed=NO`,
`effort_changed=NO` — refuting D2 and D5 outright.

**Recovery is immediate and exact for all three**: the very next request
after each one reads back *exactly* that event's own `cache_creation` value
(M1's next request reads 224,128, more than covering M1's 191,282; M2's next
reads exactly 275,806; M3's next reads exactly 563,442). This directly
confirms each event correctly wrote a usable cache that the next real
request used normally — there is no lingering corruption, and no
"intra-burst" failure to explain, because there was never an intra-burst.

## D1–D8 verdicts

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| D1 | EXPLICIT_CACHE_MISS_DIAGNOSTIC | **CONFIRMED** | `message.diagnostics.cache_miss_reason.type` identifies all 3 real material events by name (1 `system_changed`, 2 `previous_message_not_found`) and appears on **zero** of the 158 healthy control requests — perfect 1:1 correspondence in both directions. |
| D2 | CLIENT_OR_ENTRYPOINT_CHANGE | **REFUTED** | `entrypoint`/`version` constant across all 162 deduplicated requests in the session; zero transitions anywhere, not just at the boundaries. |
| D3 | SYSTEM_CONTEXT_CHANGE | **PARTIAL** | M1's own diagnostic literally says `system_changed`. The one observable structural correlate, `skill_attribution_changed = YES`, occurs at M1 — but it also occurs exactly once among the 158 controls **without** producing any miss. Rate is still far higher in material events (1 of 3 = 33%) than controls (1 of 158 = 0.6%), but the one control counterexample means skill-attribution-change is not shown to be a *sufficient* condition, only a plausible correlate, with n=1 material occurrence too small to fully resolve further from local evidence alone. |
| D4 | TOOLSET_CHANGE | **NOT MEASURABLE** | No per-request tool-schema payload is logged in this transcript format (see schema probe). M1's own diagnostic names the cause as `system_changed`, not a separate `tools_changed` category, so this remains unmeasured rather than contradicted. |
| D5 | EFFORT_OR_REQUEST_MODE_CHANGE | **REFUTED** | `effort` is `max` on all 162 deduplicated requests; zero transitions in the entire session. |
| D6 | RETRY_OR_REISSUE_PATH | **REFUTED as hypothesized; CONFIRMED as a logging artifact** | There is no retry or alternate execution path. Each "burst" is exactly one real request (one `requestId`, one linear `uuid`/`parentUuid` chain, byte-identical `usage`/`diagnostics` across every split line, verified with zero exceptions across all 162 request groups in the session). The apparent multi-second-apart repeats reported in the prior RCA are an artifact of counting each content-block-split JSONL line as an independent request. |
| D7 | TTL_OR_IDLE_ENTRY_ONLY | **CONFIRMED_ALIGNED (reframed)** | The original framing ("explains entry, not intra-burst misses") is moot once D6 is resolved — there is no intra-burst to explain. What remains: M2 and M3 (both `previous_message_not_found`) have the **only two** idle gaps exceeding 3,600 seconds anywhere in the entire 162-request session. Zero of 158 controls have a gap over 3,600 seconds at all — meaning there is no counterexample in either direction within this session (no long gap ever occurred without a miss, and no miss of this type ever occurred without a long gap). This is stated as an exact, zero-counterexample structural correlation with the session's exclusive use of the `ephemeral_1h`-labeled cache breakpoint (402/402 requests write only to that breakpoint type, never `ephemeral_5m`) — not as a claim about Anthropic's undocumented internal TTL implementation. Absolute sample size for this correlation is small (n=2). |
| D8 | REQUEST_LEVEL_CAUSE_NOT_OBSERVABLE | **REFUTED** | A request-level cause *is* observable — directly, via the first-party diagnostic field. This is the task's most important negative-of-a-negative result: existing local evidence was sufficient after all, once request-level deduplication was applied first. |

## Root-cause verdict

```text
CACHE_MISS_ROOT_CAUSE:
Layer 1 (fully confirmed, resolves the packet's exact question — "why does
the second request fail to read the cache written by the first request
seconds earlier"): it doesn't. There is no second request. Each burst is one
real API request whose multi-content-block response was logged as multiple
JSONL lines sharing one requestId, each carrying a duplicate usage/
diagnostics object. This satisfies the root-cause standard's five conditions
without caveat: it aligns exactly with all three boundaries, no control
exhibits it (every control's request produces exactly one line-group with
consistent content), it fully explains the apparent repetition (there is
none to explain), it falsifies the retry/reissue hypothesis directly, and it
rests on directly observed structural fields (requestId, uuid, parentUuid),
not an assumption.

Layer 2 (the one real miss per event, categorized with certainty, explained
with one confirmed and one partial caveat): D1 is fully confirmed - all 3
real misses are explicitly diagnosed by Anthropic's own API, by name, with
perfect discrimination against 158 healthy controls. Within that: the single
system_changed event (M1) correlates with the session's only other
skill-attribution transition, but that same transition occurs once more
without a miss, so this part stays PARTIAL rather than fully closed. The two
previous_message_not_found events (M2, M3) correlate exactly (2 of 2, 0
counterexamples in 158 controls) with idle gaps exceeding 3,600 seconds in a
session that exclusively uses the 1-hour cache breakpoint - stated as an
aligned structural correlation, not an assertion about undocumented
Anthropic internals.
```

This is reported as a genuine root-cause finding, not
`ROOT_CAUSE_NOT_IDENTIFIED`, because Layer 1 meets all five required
conditions cleanly and by itself fully resolves the specific mechanistic
question this task was opened to answer. Layer 2's partial elements are
reported honestly as partial rather than rounded up to "fully identified."

```text
NEXT_REQUIRED_EVIDENCE:
None required to close this task's primary question (Layer 1 closes it).
If the Layer-2 system_changed/skill-attribution correlation is worth fully
resolving (n=1, one uncontrolled counterexample), the next evidence type
needed is per-request skill-context identity (which skill was attributed
immediately before vs at M1 and at the one control counterexample) - not a
new high-context reproduction, and not authorized by this task.
```

## Privacy / scope compliance

- No session ID, request ID (the literal `req_...` strings), UUID,
  transcript filename, prompt text, command text, or tool-result text
  appears in this report, in `measure_cache_miss.py`, or in `metrics.json`
  (scanned: 0 UUID-shaped matches, 0 `req_`-prefixed string matches in the
  committed JSON).
- `uuid`/`parentUuid`/`requestId` raw values were used only transiently,
  in-process, to verify chaining structure and grouping; only derived
  counts, booleans, and category labels were ever written to disk.
- `analysis/token-attribution-r1/**` and `analysis/cache-reuse-churn-r1/**`
  were read and are unmodified.
- No file under `fable-method/` or `prompt/` was modified.
- No file outside `analysis/cache-miss-diagnostic-r1/` was written.
- No production-workspace transcript directory was read.
- No new debug-enabled reproduction, proxy, or API interception was run.
