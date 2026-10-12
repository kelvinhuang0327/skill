# Outcome-first reporting

## Contents

- [Compact Worker report](#compact-worker-report)
- [Evidence labels](#evidence-labels)
- [Platform consumer evidence view](#platform-consumer-evidence-view)
- [Runtime transition provenance](#runtime-transition-provenance)
- [Destructive action provenance](#destructive-action-provenance)
- [Ownership and quiescence blocker evidence](#ownership-and-quiescence-blocker-evidence)
- [Lifecycle closure](#lifecycle-closure)
- [Context and continuity](#context-and-continuity)
- [Filesystem accounting](#filesystem-accounting)
- [Final artifact gate](#final-artifact-gate)

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

Label verification provenance as `RUN_THIS_TASK` for checks actually executed
this task/current phase, or `REUSED_EXACT_TREE_EVIDENCE` when valid evidence is
reused for an identical command, environment, HEAD, and tree. Never call reused
evidence a rerun. Reuse does not require rerunning a check solely to obtain a
fresh label; keep `NOT RUN` distinct from `PASS`.

`VERIFY_WORLD_NOT_SELF_REPORT`: prefer an external observation of changed
behavior when practical — call the endpoint, exercise the affected UI,
re-read/diff the mutated file, or run a read-only query when database state is
load-bearing. A Worker saying it works is not verification, and this rule does
not add an automatic browser, database, full-suite, or Judge requirement.

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

## Runtime transition provenance

When a Worker actually changes an installed/runtime HEAD, tree, executable
binding, working-directory binding, plist binding, or equivalent production
runtime identity, the terminal handoff must explicitly report the transition
provenance.

Capture that information in the same terminal handoff instead of forcing a
future Agent to reconstruct it from reflog. Do not infer or fabricate
transition ownership from later reflog/history.

This is reporting provenance only. It must NOT:
- authorize runtime mutation;
- authorize launchctl actions;
- weaken standalone Owner authorization;
- create automatic rollback;
- create a new runtime registry;
- require permanent evidence files;
- require extra commands when the transition information is already naturally
  known to the Worker that performed the action;
- make ordinary non-runtime tasks emit irrelevant transition fields.

For a task that itself performs a runtime identity transition, terminal handoff
must report:

```text
RUNTIME_TRANSITION_OCCURRED: YES
RUNTIME_HEAD_BEFORE: <exact | NOT_APPLICABLE>
RUNTIME_TREE_BEFORE: <exact | NOT_APPLICABLE>
RUNTIME_HEAD_AFTER: <exact | NOT_APPLICABLE>
RUNTIME_TREE_AFTER: <exact | NOT_APPLICABLE>
RUNTIME_TRANSITION_AT: <timestamp>
RUNTIME_TRANSITION_ACTION: <exact action>
RUNTIME_TRANSITION_TASK_OR_RUN_ID: <exact current task/run identity if available>
RUNTIME_BINDING_BEFORE: <exact executable/worktree/plist/service binding | NOT_APPLICABLE>
RUNTIME_BINDING_AFTER: <exact executable/worktree/plist/service binding | NOT_APPLICABLE>
ROLLBACK_TARGET: <exact pre-transition identity | NOT_APPLICABLE>
```

If the current task did not perform a runtime identity transition:

```text
RUNTIME_TRANSITION_OCCURRED: NO
```

Ordinary tasks with `RUNTIME_TRANSITION_OCCURRED: NO` remain backward compatible
and do not emit the transition-specific fields above.

## Destructive action provenance

When a Worker actually performs a destructive filesystem / durable-resource
removal authorized by its Packet, terminal handoff must report the destructive
action provenance. This is reporting provenance only; it never authorizes a
destructive action, and authorization remains governed by
[operational gates](operational-gates.md) and the existing authorization
evidence vocabulary.

For an action this Worker actually performed, report:

```text
DESTRUCTIVE_ACTION_OCCURRED: YES
DESTRUCTIVE_TARGET: <exact target or compact exact target set>
DESTRUCTIVE_TARGET_PRESTATE: <exact observed state>
DESTRUCTIVE_ACTION: <exact primitive/action>
DESTRUCTIVE_ACTION_AT: <timestamp>
DESTRUCTIVE_ACTION_AUTHORIZATION_SOURCE: <existing authorization evidence vocabulary>
DESTRUCTIVE_ACTION_TASK_OR_RUN_ID: <exact current task/run identity if naturally available>
DESTRUCTIVE_TARGET_POSTSTATE: <exact observed state>
```

`DESTRUCTIVE_ACTION_AUTHORIZATION_SOURCE` reuses the existing authorization
evidence vocabulary; it does not define a second authority model. If the
current Worker performs no destructive action:

```text
DESTRUCTIVE_ACTION_OCCURRED: NO
```

Do not require the detailed YES-only fields for ordinary non-destructive tasks.

A target observed as `ALREADY_ABSENT` means the current Worker did not need to
perform deletion; it must not be reported as evidence that this Worker
executed a destructive action. A later observer may report current absence,
but must not infer who deleted the target without provenance evidence.

This contract does not require a new persistent receipt/evidence framework,
registry, evidence database, receipt file, ledger service, or runtime storage.
Do not require an extra command solely for reporting when the Worker naturally
knows the information from the action it just performed.

## Ownership and quiescence blocker evidence

When a task-relevant runtime ownership or quiescence gate — [scope-qualified
writer and quiescence checks](task-checkpoint.md#scope-qualified-writer-and-quiescence-checks),
[worktrees and mutation evidence](operational-gates.md#worktrees-and-mutation-evidence),
or an equivalent load-bearing observation — blocks execution, the terminal or
blocked handoff must report the exact evidence naturally available from that
observation, for every load-bearing matched owner:

```text
OWNER_PID:
OWNER_PPID:
OWNER_EXECUTABLE:
OWNER_ARGV:
OWNER_CWD:
OWNER_MATCHED_EVIDENCE:
OWNER_ROLE_MARKERS:
OWNER_SOURCE_OR_RUNTIME_MATCH:
OWNER_LOCK_STATES:
OWNERSHIP_OBSERVED_AT:
```

Report `UNKNOWN` for a field that is genuinely unavailable rather than
inventing a value. This is diagnostic reporting only: it must NOT define a
second ownership classifier, exempt a process from an existing scoped
ownership rule, alter role semantics, perform a workspace-wide process scan
beyond what the blocking check already inspected, or weaken any existing
scope-qualified ownership or quiescence rule. The purpose is that a later RCA
can distinguish, for example, a real scheduler owner from a caller-chain
observer, a wrapper, or a lock holder without reconstructing the failed
process snapshot from chat history. Do not require an extra command solely
for this report when the blocking check already naturally observed these
fields.

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

## Context and continuity

Never infer model identity, context capacity, current usage, or billing policy
from the product name or maximum window. Resolve each independently and mark
unavailable values `UNKNOWN`; do not make a cost-multiplier claim unless the
active model, plan/policy, and threshold are current and authoritative.

When exact usage metadata is unavailable, report
`CURRENT_CONTEXT_PERCENT: UNKNOWN`,
`CURRENT_CONTEXT_USAGE_SOURCE: HEURISTIC`, and a qualitative pressure level.
At a stable milestone or before a large phase/handoff, preserve only observed
state: exact repository/branch/HEAD/tree/status, active processes and pending
mutations, completed files/commits, verification and `NOT RUN`, failed
attempts, blocker, next action/milestone, and stop conditions. Do not write a
checkpoint unless the Packet supplies both
`HANDOFF_STORAGE_MODE: ALLOWLISTED_FILE` and an exact `HANDOFF_OUTPUT_PATH`;
default to `TRANSCRIPT_ONLY`.

After compaction or resume, report `CONTEXT_REHYDRATION_STATUS: PASS` only when
project, task, authority, repository, sandbox, modified-path ledger, observable
history, milestone, blocker, next action, next milestone, and stop conditions
are all resolved. Otherwise report `CONTEXT_HANDOFF_INCOMPLETE` and do only
read-only state resolution. Resume never clears a blocker, extends
authorization, hides a failed attempt, or reopens a stopped mutation.

## Filesystem accounting

Keep these lifecycle axes distinct:

```text
IMPLEMENTATION_LIFECYCLE_STATUS: NOT_STARTED | IN_PROGRESS | COMPLETE | BLOCKED | NOT_APPLICABLE
PR_PUBLICATION_STATUS: NOT_APPLICABLE | NOT_CREATED | DRAFT_OPEN | READY_OPEN | MERGED | BLOCKED
POSTMERGE_LIFECYCLE_STATUS: NOT_APPLICABLE | NOT_STARTED | IN_PROGRESS | COMPLETE | BLOCKED
BRANCH_CLEANUP_STATUS: NOT_APPLICABLE | RETAINED_WHILE_PR_OPEN | DELETED | ALREADY_ABSENT | BLOCKED
FULL_PR_LIFECYCLE_CLOSED: YES | NO
```

Terminal absence proves current state only. `ALREADY_ABSENT` does not by
itself prove `DELETED_BY_THIS_TASK`. A handoff claim that this task deleted,
removed, changed, or otherwise caused a destructive mutation must be supported
by an exact entry in the existing task command or filesystem ledger recording
the action and its actual observed result or exit status, not by terminal-state
evidence alone.

`FULL_PR_LIFECYCLE_CLOSED: YES` requires verified merge containment,
post-merge checks, cleanup, and a clean/restored workspace. Local completion
without publication is not a publication failure. Keep unauthorized actions
under `NOT RUN`; use `BLOCKED` for authorized or required work a gate stopped.

Every load-bearing `BLOCKED` gate in a terminal handoff includes exactly one
compact inline blocker record — `BLOCKER_CODE:`, `BLOCKER_DETAIL:`, and
`SMALLEST_NEXT_ACTION:` (or a named-gate prefix, such as
`A2_BLOCKER_CODE:`) — so a downstream Agent can select the next action without
local filesystem access to the originating Agent. The inline record is a
transfer summary, not a second authority: a durable artifact or exact locator
remains canonical for full evidence, but must never be the sole carrier of the
fact needed to decide what happens next. It does not replace artifact paths,
hashes, full evidence, runtime receipts, or exact authority locators.

```text
BLOCKER_CODE:
BLOCKER_DETAIL:
SMALLEST_NEXT_ACTION:
```

`BLOCKER_DETAIL` states the actual missing or invalid fact when known — for
example, a missing strategy, draw, config, seed, unsupported capability, or
unresolved authority — never a vague `see artifact`, `blocked`, or `needs
investigation` once the exact blocking fact was observed. When genuinely
unknown, state `UNKNOWN` and name the smallest bounded resolution action.
`SMALLEST_NEXT_ACTION` is one bounded progress action, not a roadmap. Each
independently blocked gate carries its own record rather than one blocker
duplicated under aliases. A `COMPLETE` handoff needs no blocker record.

For judged, publication-bound, or Tier-2 runtime work, report the complete
ledger with `NONE` only when a partition is truly empty:

```text
FILES_WRITTEN_DURING_TASK:
FILES_RETAINED_AT_END:
FILES_DELETED_BEFORE_END:
TASK_CREATED_FILES_RETAINED:
TASK_CREATED_FILES_DELETED:
PRE_EXISTING_FILES_RETAINED_UNCHANGED:
PRE_EXISTING_FILES_MODIFIED_AND_RATIFIED:
FILES_MODIFIED_DURING_TASK:
REPOSITORY_FILES_MODIFIED:
TOOLCHAIN_RUNTIME_OUTPUTS_CREATED:
TOOLCHAIN_RUNTIME_OUTPUTS_MODIFIED:
PRE_EXISTING_RUNTIME_OUTPUTS_RETAINED_UNCHANGED:
WORKTREE_MATERIALIZATION_CREATED:
WORKTREE_MATERIALIZATION_UPDATED:
WORKTREE_MATERIALIZATION_REMOVED:
GIT_NETWORK_METADATA_WRITES:
GIT_WORKTREE_METADATA_WRITES:
HARNESS_GIT_METADATA_WRITES:
```

Keep `TASK_COMMIT`, `TASK_TREE`, `FINAL_HEAD`, `FINAL_TREE`,
`CANONICAL_FINAL_HEAD`, `CANONICAL_FINAL_TREE`, and `COMMIT_LINK` distinct.
Local-only commits use `COMMIT_LINK: NOT_APPLICABLE`.

## Final artifact gate

Before a behavior-changing edit, include this exact intent line in the final
report:

```text
INTENT: code does <X>; the check/task expects <Y>; the opened spec says <Z>
```

Hostile-review the final report against the Packet and actual diff/status.
Every changed path must be authorized; every `PASS` needs an observed command,
observation, or valid same-tree evidence. Do not return terminal success while
a mandatory criterion is `NOT RUN`, unresolved, or contradicted. Add the
required `AUTH:`, `PENDING:`, or `TWINS:` line when its condition applies. Lead
with what happened; distinguish `NOT RUN`, `BLOCKED`, and `UNKNOWN`. Never
claim deployment, publication, runtime success, equality, or cleanup without
observing it. `FULL_PR_LIFECYCLE_CLOSED: YES` also requires a verified merge
commit, target containment, required post-merge checks, cleanup, and a
clean/restored workspace.

When a task changes runtime identity, report `RUNTIME_TRANSITION_OCCURRED: YES`
with before and after HEAD/tree/bindings, action, timestamp, task/run ID, and
rollback target. Otherwise report `RUNTIME_TRANSITION_OCCURRED: NO`.

For judged or publication-bound work, also include:

```text
LOCAL_FULL_SUITE_RUNS:
FOCUSED_TEST_RUNS:
INITIAL_JUDGE_RUNS:
DELTA_REJUDGE_RUNS:
FULL_JUDGE_RUNS:
EXACT_HEAD_CI_RUNS:
REUSED_EVIDENCE:
INVALIDATED_EVIDENCE:
```

Leave no task-created scratch debris.
