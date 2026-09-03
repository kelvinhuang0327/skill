# Outcome-first reporting

## Contents

- [Compact Worker report](#compact-worker-report)
- [Evidence labels](#evidence-labels)
- [Platform consumer evidence view](#platform-consumer-evidence-view)
- [Lifecycle closure](#lifecycle-closure)

Use the compact form for ordinary `FAST`/`STANDARD` work. Use the full
evidence handoff for Judge-gated work, a blocked result, or a Packet that names
additional fields.

## Compact Worker report

```text
STATUS: <what happened, in plain language>
ROUTE: <the selected WORKER_ROUTE>
CHANGED: <authorized files/surfaces touched>
VERIFIED: <commands/observations and real output>
NOT RUN / BLOCKED: <checks not run and why>
RISKS: <remaining unknowns or caveats>
```

Every `VERIFIED` claim must trace to an observed command or runtime result.
Never replace evidence with “should work”, “looks correct”, or “likely passes”.
Command execution alone is not `PASS`. A load-bearing `PASS` requires the
exact observed result to satisfy acceptance. A non-zero `git diff --check`
cannot be reported `PASS`.
If a prescribed follow-up was deliberately not taken, name it as:

```text
PENDING: <action> - awaiting your authorization
```

## Evidence labels

Use `[Confirmed]` for direct command or observation, `[Inferred]` for a
machine-checkable conclusion derived from observed state, and `[Unknown]` when
the required source or runtime evidence was unavailable. A successful retry can
be named:

```text
FINAL_SUCCESSFUL_ATTEMPT:
OVERALL_TASK_CONTRACT_RESULT:
```

Earlier failures, aborts, timeouts, and terminations remain in the attempt and
filesystem ledgers.

## Platform consumer evidence view

A Consumer is a real downstream runtime/platform that consumes a Fable
materialization — for this repository, at least Claude, Codex, and Gemini.
"Platform verified" is ambiguous: it can mean anything from "files were
copied" to "an agent ran the skill end-to-end." State exactly which of three
distinct evidence layers a claim actually rests on, and against which exact
materialization identity (a commit or tree) — not merely "the repo" or "the
platform" — since more than one identity is routinely in play at once.

- **MATERIALIZATION** — does the target actually match the identity being
  reported as current? Two distinct surfaces exist and evidence must say
  which one was checked: *repo platform materialization* (is the
  repo-committed platform copy under `fable-method/platforms/<name>/` in
  sync with the shared source — `sync-platforms.sh --check`) and *live
  consumer installation* (does the runtime's actual live install target
  match a given identity — `activate-live.sh --check` or an equivalent
  deterministic comparison). A check against either surface that finds an
  exact match to the identity being reported as current is `PASS`. A check
  that finds a real, known, but **not current** identity —
  `EXACT_HISTORICAL_MATERIALIZATION` in `activate-live.sh` terms — is not
  `PASS`; report it as:
  ```text
  MATERIALIZATION: NOT CURRENT @ <historical identity>
  ```
  This is not `NOT RUN` either: the target was actually inspected and a
  concrete answer exists, it simply is not the identity being asked about.
  Neither surface alone proves the agent loaded the skill.
- **DISCOVERY** — did the actual target agent/runtime discover or expose the
  skill to the model, at a specific materialization identity? Evidence must
  come from the target platform/runtime itself (e.g. a platform-native skill
  listing, a model-visible skill registry or prompt inspection, or another
  deterministic agent-side discovery mechanism — derive the applicable
  mechanism from the actual platform, do not treat any specific example as
  mandatory). A filesystem match alone cannot produce `DISCOVERY: PASS`.
- **EXECUTION** — did the actual target agent/runtime execute the skill
  behavior successfully, at a specific materialization identity? Evidence
  requires a bounded behavior execution/dogfood observed on the target
  platform (e.g. a fresh-session checkpoint continuation, a harmless trigger
  proving loaded instructions were followed, or existing equivalent
  execution evidence). Installation, file presence, or discovery alone
  cannot produce `EXECUTION: PASS`.

Each layer resolves to `PASS`, `NOT RUN`, or `BLOCKED` — the same values used
elsewhere in this document, meaning direct evidence exists (for the identity
being reported), that exact layer has not been tested at all, or it was
required/attempted but a concrete blocker prevented establishing it — plus,
for MATERIALIZATION only, the `NOT CURRENT @ <identity>` outcome defined
above. This is not a new global lifecycle enum, only a compact, view-local
way to say "checked, and the answer is a specific non-current identity"
without misusing `NOT RUN` for a check that actually ran. Never infer a
`PASS` from a different layer, a different consumer, or a different
materialization identity than the one actually tested:

```text
MATERIALIZATION: PASS @ X   does NOT imply   DISCOVERY: PASS @ X
DISCOVERY: PASS @ X         does NOT imply   EXECUTION: PASS @ X
(any evidence) @ X          does NOT imply   (same evidence) @ Y, for X != Y
```

Cite the identity a `PASS` was observed against (e.g. `PASS @ ec654dd`)
whenever more than one identity is in play, including in Discovery and
Execution — a Discovery/Execution `PASS` observed against an older identity
does not automatically carry forward to a newer canonical identity. Evidence
from one identity may be reused for another only when the report explicitly
states why the relevant behavior/content is unchanged between them (e.g.
citing a diff showing no relevant change); do not assume this silently, do
not build a general reuse mechanism for it, and prefer leaving the newer
identity `NOT RUN` over an unjustified carry-forward.

Report the three layers as one compact table (repo materialization and live
installation share this same table — the Evidence column says which surface
each cell is about):

| Consumer | Materialization | Discovery | Execution | Evidence |
|---|---|---|---|---|
| Claude | PASS | PASS | PASS | <minimal refs> |
| Codex | NOT CURRENT @ \<commit\> | NOT RUN | NOT RUN | <minimal refs> |
| Gemini | NOT CURRENT @ \<commit\> | NOT RUN | NOT RUN | <minimal refs> |

The rows above are an example of shape only, not a canonical result —
populate them from the evidence actually available to the current task, and
name the identity whenever it is not unambiguously "current HEAD." Keep the
Evidence column compact and load-bearing (a command, a commit, a session
observation); do not paste full logs. Do not add separate
`AFFECTED_CONSUMERS` or `CONSUMER_STATE` fields once this table is present —
one view owns this concern, for either surface.

This view is descriptive, not a release gate: an incomplete or
not-current row (e.g. `MATERIALIZATION: NOT CURRENT @ ec654dd` with
`DISCOVERY`/`EXECUTION: NOT RUN`) is a more accurate statement than an
unqualified "verified," and is not by itself a task failure. Whether a given
task's acceptance requires current materialization, Discovery, or Execution
evidence remains task-specific, decided by that task's own acceptance
criteria — not a universal consequence of using this view.

## Lifecycle closure

`FULL_PR_LIFECYCLE_CLOSED: YES` requires every applicable lifecycle surface —
implementation, PR/publication, post-merge verification, local worktree,
local branch, remote branch, and task artifacts/durable evidence — to have a
verified terminal disposition: removed, deleted, merged, archived, explicitly
retained, or `NOT_APPLICABLE`. A surface that is unknown, unresolved, or
waiting on unissued authorization keeps `FULL_PR_LIFECYCLE_CLOSED: NO` even if
every other surface is closed. A deliberately retained remote branch or
artifact can still be terminal when that retention is itself the intended
disposition; an accidentally unaddressed one is a residual, not a closure.

Report exactly what remains open:

```text
LIFECYCLE_RESIDUALS: NONE
```

or, itemized with the exact reason each item stayed open:

```text
LIFECYCLE_RESIDUALS:
- origin/example-branch: remote deletion not authorized
- worktree/path: active concurrent task
```

For a local uncommitted Worker handoff, use:

```text
PR_PUBLICATION_STATUS: NOT_APPLICABLE
POSTMERGE_LIFECYCLE_STATUS: NOT_APPLICABLE
BRANCH_CLEANUP_STATUS: NOT_APPLICABLE
FULL_PR_LIFECYCLE_CLOSED: NO
```

Do not prewrite a Judge verdict, claim publication, or call local completion a
closed PR lifecycle.
