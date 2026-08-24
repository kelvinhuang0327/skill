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
distinct evidence layers a claim actually rests on:

- **MATERIALIZATION** — does the expected canonical materialization exist at
  the platform's live target and match the expected source identity? Typical
  evidence: `sync-platforms.sh --check`, `activate-live.sh --check`, or an
  equivalent deterministic content comparison. This does not prove the agent
  loaded the skill.
- **DISCOVERY** — did the actual target agent/runtime discover or expose the
  skill to the model? Evidence must come from the target platform/runtime
  itself (e.g. a platform-native skill listing, a model-visible skill
  registry or prompt inspection, or another deterministic agent-side
  discovery mechanism — derive the applicable mechanism from the actual
  platform, do not treat any specific example as mandatory). A filesystem
  match alone cannot produce `DISCOVERY: PASS`.
- **EXECUTION** — did the actual target agent/runtime execute the skill
  behavior successfully? Evidence requires a bounded behavior
  execution/dogfood observed on the target platform (e.g. a fresh-session
  checkpoint continuation, a harmless trigger proving loaded instructions
  were followed, or existing equivalent execution evidence). Installation,
  file presence, or discovery alone cannot produce `EXECUTION: PASS`.

Each layer resolves to one of the same values used elsewhere in this
document — `PASS`, `NOT RUN`, or `BLOCKED` — meaning direct evidence exists,
that exact layer has not been tested, or it was required/attempted but a
concrete blocker prevented establishing it. Never infer a `PASS` from a
different layer or a different consumer. In particular:

```text
MATERIALIZATION: PASS  does NOT imply  DISCOVERY: PASS
DISCOVERY: PASS        does NOT imply  EXECUTION: PASS
```

Report the three layers as one compact table:

| Consumer | Materialization | Discovery | Execution | Evidence |
|---|---|---|---|---|
| Claude | PASS | PASS | PASS | <minimal refs> |
| Codex | PASS | PASS | PASS | <minimal refs> |
| Gemini | PASS | NOT RUN | NOT RUN | <minimal refs> |

The row above is an example of shape only, not a canonical result — populate
it from the evidence actually available to the current task. Keep the
Evidence column compact and load-bearing (a command, a commit, a session
observation); do not paste full logs. Do not add separate
`AFFECTED_CONSUMERS` or `CONSUMER_STATE` fields once this table is present —
one view owns this concern.

This view is descriptive, not a release gate: an incomplete row (e.g.
`MATERIALIZATION: PASS` with `DISCOVERY`/`EXECUTION: NOT RUN`) is a more
accurate statement than an unqualified "verified," and is not by itself a
task failure. Whether a given task's acceptance requires Discovery or
Execution evidence remains task-specific, decided by that task's own
acceptance criteria — not a universal consequence of using this view.

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
