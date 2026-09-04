# Resumable Task Checkpoints

## Contents

- [Overview and core principles](#overview-and-core-principles)
- [Authoritative checkpoint contract](#authoritative-checkpoint-contract)
- [Storage and lifecycle](#storage-and-lifecycle)
- [Update semantics and revision protection](#update-semantics-and-revision-protection)
- [Deferred blocked-task queue](#deferred-blocked-task-queue)
- [Scope-qualified writer and quiescence checks](#scope-qualified-writer-and-quiescence-checks)
- [Long-running execution recovery](#long-running-execution-recovery)
- [Publication live-state classifier](#publication-live-state-classifier)
- [Bounded reconciliation algorithm](#bounded-reconciliation-algorithm)
- [Next action vocabulary](#next-action-vocabulary)
- [Authorization boundary rules](#authorization-boundary-rules)
- [Terminal closure and archiving](#terminal-closure-and-archiving)

## Overview and core principles

A **Resumable Task Checkpoint** persists the minimal authoritative continuation
state required for a Worker task across session or model boundaries. It frees a
resuming Worker from reconstructing task progress from chat transcripts or
re-requesting an entire Worker Packet.

The checkpoint is governed by two core principles:

1. **Durable state survives session/model changes**: continuation state is
   anchored to repo-local storage rather than ephemeral chat memory.
2. **Resume performs reconciliation before execution**: a resuming Agent inspects
   live repository, worktree, branch, PR, and authorization state before taking
   action, never trusting remembered state over live observations.

The checkpoint is **continuation state, not a second authority layer**. The
original task contract remains rooted in the initial Worker Packet referenced by
`AUTHORITATIVE_PACKET_REF`.

## Authoritative checkpoint contract

Each checkpoint is stored as a structured JSON record containing the following
minimal load-bearing fields:

```json
{
  "schema_version": 1,
  "task_id": "<TASK_ID>",
  "repository": "<ABSOLUTE_REPOSITORY_ROOT>",
  "worktree": "<ABSOLUTE_WORKTREE_PATH>",
  "authoritative_packet_ref": "<LOCATOR_OR_INHERITANCE_SOURCE>",
  "branch": "<BRANCH_NAME_OR_DETACHED>",
  "current_head": "<COMMIT_SHA_OR_UNCOMMITTED>",
  "current_tree": "<TREE_SHA_OR_UNCOMMITTED>",
  "task_lifecycle_state": "IN_PROGRESS | BLOCKED | COMPLETED | ABORTED",
  "current_blocker": "<EXACT_BLOCKER_OR_NULL>",
  "next_action": "<EXACT_NEXT_ACTION>",
  "authorization_boundary": "NONE | CURRENT_WORKER_CONVERSATION_STANDALONE_AUTH_REQUIRED | ...",
  "pr_state": "NONE | DRAFT_OPEN | READY_OPEN | MERGED | CLOSED | NULL",
  "pr_number": "<NUMBER_OR_NULL>",
  "pr_url": "<URL_OR_NULL>",
  "updated_at": "<ISO8601_TIMESTAMP>",
  "revision": 1,
  "queue_disposition": "BLOCKED_DEFERRED",
  "resume_after_task_id": "<EXACT_TASK_B_ID>",
  "next_authorized_task_packet_ref": "<DURABLE_TASK_B_PACKET_LOCATOR>",
  "deferred_resume_action": "<TASK_A_ORIGINAL_CONTINUATION_ACTION>",
  "deferred_recheck_count": 0
}
```

The five queue fields are backward-compatible and optional. A normal schema-1
checkpoint omits them. After a successful recheck, `queue_disposition` is
cleared while the other four fields remain as a consumed queue-run marker with
`deferred_recheck_count: 1`. This marker is not an active deferred task and does
not add a lifecycle state. `SCHEMA_VERSION` remains `1`.

### Field definitions

- `task_id`: Unique identifier for the task matching the Packet.
- `repository`: Absolute path to the canonical repository root.
- `worktree`: Absolute path to the active worktree or working directory.
- `authoritative_packet_ref`: Explicit, durable locator for the authoritative
  task packet. Must be resolvable by any Worker in a fresh session without
  access to originating chat history. Valid durable forms include:
  - Repo-relative file path: e.g. `prompt/Personal_Planner_Handoff_Prompt_v5.4_Lean_Final.md#task-id`,
    `.fable/packets/<task_id>.md`, or `memory/tasks/<task_id>.md`.
  - Immutable Git-backed locator: `git:<commit_sha>:<path>` or `git:<ref>:<path>`.
  - Path within repository or worktree.
  Ephemeral session URIs (e.g. `conversation://...`, `session://...`, transcript
  offsets) are invalid and fail closed. `ORIGINAL_TASK_RULES_INHERITED: YES`
  alone is insufficient without an explicit locator.
- `branch`: Current checked-out branch or detached state.
- `current_head`: Git commit SHA corresponding to the recorded milestone, or
  `UNCOMMITTED`.
- `current_tree`: Git tree SHA corresponding to the recorded milestone, or
  `UNCOMMITTED`.
- `task_lifecycle_state`: Overall lifecycle state (`IN_PROGRESS`, `BLOCKED`,
  `COMPLETED`, `ABORTED`).
- `current_blocker`: Specific root-cause or diagnostic blocker, or `null` if none.
- `next_action`: Exact, single next action to execute upon resumption.
- `authorization_boundary`: Declared authorization requirement for `next_action`.
- `pr_state`: Known PR state if the task interacts with a remote pull request.
- `updated_at`: ISO 8601 UTC timestamp of last state update.
- `revision`: Monotonically increasing integer used for optimistic concurrency
  and overwrite protection.
- `queue_disposition`: Optional queue axis. The only queue value is
  `BLOCKED_DEFERRED`; it is **not** a lifecycle enum.
- `resume_after_task_id`: Exact, independent Task B selected by the Planner.
- `next_authorized_task_packet_ref`: Durable locator for Task B's existing,
  executable Owner-authorized Packet. Ephemeral conversation/session/chat
  locators and inheritance-only statements fail closed.
- `deferred_resume_action`: Task A's original continuation action, preserved
  separately while `next_action` is `RECHECK_DEFERRED_RESUME_GATE`.
- `deferred_recheck_count`: Durable count constrained to `0` or `1`. A value of
  `1` remains durable after PASS so a fresh Worker cannot start a second
  automatic queue cycle if the same transient blocker recurs.

## Storage and lifecycle

- **Default location**: `.fable/checkpoints/<task_id>.json` relative to the
  repository root or worktree root.
- **Rule**: Exactly **ONE AUTHORITATIVE CHECKPOINT PER ACTIVE TASK**.
- Checkpoint files belong to task-owned runtime state and must not pollute
  production source code.
- If a Packet specifies `HANDOFF_STORAGE_MODE: ALLOWLISTED_FILE` and an exact
  `HANDOFF_OUTPUT_PATH`, that path is used as the checkpoint location.

## Update semantics and revision protection

- Checkpoints are written **only** when load-bearing continuation state changes:
  - implementation milestone sealed;
  - blocker identified or updated;
  - root cause resolved;
  - PR created, updated, or merged;
  - next action changed;
  - task completed or blocked.
- Do **not** write a checkpoint on every command or shell execution.
- Do **not** store command transcripts inside the checkpoint.
- Updates require incrementing `revision` by 1. A write attempting to update a
  stale revision fails closed to prevent silent overwrites.

## Deferred blocked-task queue

This is a bounded continuation exception, not a scheduler, lifecycle state, or
authority source. The Planner must explicitly classify Task A's blocker as
`TRANSIENT_ELIGIBLE`, prove Task B is independent, and provide Task B's already
executable Owner-authorized Packet through a durable locator. Semantic,
authorization, safety, database-authority, and permanent blockers are never
eligible. The Worker never scans a roadmap, searches for work, or invents Task B.

The only valid deferred Task A state is:

```text
task_lifecycle_state: BLOCKED
queue_disposition: BLOCKED_DEFERRED
current_blocker: <specific transient blocker>
next_action: RECHECK_DEFERRED_RESUME_GATE
resume_after_task_id: <exact Task B>
next_authorized_task_packet_ref: <durable Task B Packet>
deferred_resume_action: <Task A original continuation>
deferred_recheck_count: 0 | 1
```

`TaskCheckpoint#defer_for_authorized_task!` requires explicit confirmations for
transient eligibility, Task B independence, and Packet authorization; resolves
the Packet locator before changing Task A; and preserves the prior continuation
action. Its `existing_deferred_checkpoints` keyword is mandatory: omission is
an error rather than an implicit empty inventory. Persist Task A with revision
protection **before** executing Task B.
When bounded ownership surfaces are supplied, both Task A and Task B surfaces
are required and any lexical overlap rejects the independence claim; a boolean
confirmation cannot override contradictory ownership evidence.
`TaskCheckpoint.validate_deferred_limit!` evaluates the explicit active-task
inventory supplied by the Worker and permits at most one deferred checkpoint.
It does not discover or schedule tasks.

The frozen sequence is:

1. Persist eligible Task A as `BLOCKED` / `BLOCKED_DEFERRED`.
2. Execute exactly the named Task B from `next_authorized_task_packet_ref`.
3. Task B reaches an end-of-task state: `COMPLETED`, `ABORTED`, or `BLOCKED`.
   `BLOCKED` ends Task B for this queue decision; do not search for Task C.
4. Perform exactly one end-of-task recheck of Task A's transient blocker.
5. On PASS, restore Task A to `IN_PROGRESS`, clear only the active
   `queue_disposition`, resume from `deferred_resume_action`, and retain the
   Task B identity/action fields with `deferred_recheck_count: 1` as a consumed
   run marker. On FAIL, retain `BLOCKED_DEFERRED`, set
   `deferred_recheck_count: 1`, and make no second automatic recheck.
6. If the transient blocker recurs after PASS in the same queue run,
   `TaskCheckpoint#block_recurrent_transient!` returns Task A to `BLOCKED` /
   `BLOCKED_DEFERRED` with the consumed count intact. It does not expose an
   authorized deferred task, execute Task B again, or select Task C; bounded
   reconciliation returns `STOP_UNRESOLVED`.

`TaskCheckpoint#recheck_deferred_resume_gate!` requires the exact Task B
checkpoint and a terminal end-of-task state. A mismatched or still-running Task
B, a Task B that is itself deferred, a second deferred checkpoint, or a second
recheck fails closed. A fresh process reconstructs all queue state from Task A's
checkpoint and the exact Task B checkpoint; chat memory is never required.
`TaskCheckpoint#defer_for_authorized_task!` also rejects a checkpoint carrying
the consumed marker, so callers cannot bypass the recurrence rule by presenting
the same or a different Task B as a new automatic cycle.

## Scope-qualified writer and quiescence checks

Concurrent-writer detection must concern the exact worktree and task-owned
surface. A process name alone is not mutation evidence. Treat a writer as active
only when evidence connects it to that surface (for example its working/open
paths or command target can affect the worktree), or when the surface itself is
observed changing.

Before a load-bearing checkpoint, source, or Git mutation, take a scoped state
snapshot and observe it for a bounded interval. The default interval is about
five seconds: compare the exact branch/HEAD/tree/status and relevant task-owned
path identity or content metadata at both ends. This is a default observation
window, not a universal safety constant. Do not poll indefinitely. Stable scoped
evidence permits progress; unexplained scoped mutation stops the write and
requires ownership reconciliation.

`TaskCheckpoint.scope_qualified_active_writer?` evaluates this evidence without
discovering or scheduling processes. The caller supplies the exact worktree,
task-owned paths, before/after snapshots, and any observed process target paths.
Snapshot drift is active mutation. With stable snapshots, only an observed
target path overlapping the exact worktree or owned surface counts; names such
as `pytest`, `python`, or `Agent` without that path evidence do not. The exposed
default observation value is five seconds, but callers may choose another
bounded interval when the task requires it.

## Long-running execution recovery

`ExecutionRecord` (`fable-method/scripts/task_checkpoint.rb`) classifies a
task-owned execution that may have outlived its originating session, so a
resuming Worker never launches a silent duplicate of expensive or
long-running work. It never scans the OS process table or maintains a
registry; it inspects only the exact PID this task itself recorded.

**Storage**: `.fable/checkpoints/<task_id>/executions/<execution_id>.json`,
alongside the checkpoint's own `.fable/checkpoints/<task_id>.json`.

Call `ExecutionRecord.recover_before_execution(file_path)` before starting a
possibly-duplicate execution. It classifies exactly one:

| Classification | Meaning | Behavior |
|---|---|---|
| `PRIOR_PROCESS_ACTIVE` | The recorded PID is still alive. | Raises `DuplicateExecutionError`; do not start a duplicate. |
| `PRIOR_PROCESS_COMPLETED` | Status is `COMPLETED` with acceptance-complete durable evidence (see [Durable terminal capture](judge-handoff.md#durable-terminal-capture)). | Returns the existing durable capture for reuse; the upstream command is not rerun. |
| `PRIOR_PROCESS_TERMINATED_INCOMPLETE` | The recorded PID is confirmed dead, or status is `COMPLETED` without acceptance-complete evidence. | Returns the classification only; rerun eligibility is left to the original task authority, not decided here. |
| `PRIOR_PROCESS_STATE_UNRESOLVED` | Liveness could not be established, or the record exists but is unreadable/malformed. | Raises `UnresolvedExecutionStateError`; fail closed rather than risk an overlapping duplicate. |

A record that was never created at all (nothing was ever started under this
exact path — as opposed to a record that exists but fails to parse) returns a
`Recovery` with a `nil` classification rather than any of the four states
above: session or UI disappearance alone is never itself evidence of failure.

`ExecutionRecord.start!` persists a `STARTED` record with the real PID before
the long-running work begins. `#complete!` finalizes it to `COMPLETED` with a
`durable_capture_path` once the work's durable terminal evidence exists.

## Publication live-state classifier

`PublicationLiveStateClassifier` (`fable-method/scripts/task_checkpoint.rb`)
resolves what is already true about a task's Git publication lifecycle from
freshly-queried remote facts, so a stale checkpoint or Packet cannot cause an
already-completed Ready/Merge/postmerge action to be replayed, nor a
genuinely pending one to be silently skipped.

It is a **derived live view only**: it holds no `save`/`load`, adds no
lifecycle enum, and does not replace `PR_PUBLICATION_STATUS`,
`POSTMERGE_LIFECYCLE_STATUS`, `BRANCH_CLEANUP_STATUS`, or
`FULL_PR_LIFECYCLE_CLOSED`, which remain the authoritative reporting axes.
Every call re-resolves remote branch existence and PR state at classification
time; `checkpoint.pr_state` may be passed in as the identity to look up (via
`named_pr_number`) but is never itself treated as live truth. The classifier
determines **what is already true**; it never performs or authorizes a new
external action, and the Git action tiers and standalone Owner authorization
rules above are unchanged by its output.

### Derived states

| State | Meaning |
|---|---|
| `LOCAL_ONLY` | No remote branch and no associated pull request. |
| `REMOTE_BRANCH_ONLY` | Remote branch exists; no open or merged pull request claims it. |
| `DRAFT_PR_OPEN` | An open pull request exists and is still a draft. |
| `READY_PR_OPEN` | An open, non-draft pull request already exists. |
| `MERGED_POSTMERGE_PENDING` | The pull request is merged; no exact-identity verified postmerge evidence is present. |
| `MERGED_POSTMERGE_COMPLETE` | The pull request is merged and verified postmerge evidence matches this exact identity (PR number and, when both sides have one, merge commit SHA). |
| `IDENTITY_CONFLICT` | Live state cannot be trusted or safely attributed to this task (see below). Fails closed. |

### Short-circuit contract

| State | Ready | Merge | Postmerge | Terminal |
|---|---|---|---|---|
| `READY_PR_OPEN` | `SKIP_ALREADY_COMPLETE` | — | — | — |
| `MERGED_POSTMERGE_PENDING` | `SKIP_ALREADY_COMPLETE` | `SKIP_ALREADY_COMPLETE` | `VERIFY_OR_COMPLETE_MISSING_POSTMERGE` | — |
| `MERGED_POSTMERGE_COMPLETE` | `SKIP_ALREADY_COMPLETE` | `SKIP_ALREADY_COMPLETE` | `REUSE_VERIFIED_EVIDENCE_OR_VERIFY_ONLY_IF_MISSING` | `COMPLETION_HANDOFF` |
| `IDENTITY_CONFLICT` | `STOP_UNRESOLVED` | `STOP_UNRESOLVED` | `STOP_UNRESOLVED` | — |

`LOCAL_ONLY`, `REMOTE_BRANCH_ONLY`, and `DRAFT_PR_OPEN` carry no short-circuit
(a `—` cell is `nil`, not an implied action): the caller proceeds with its
Packet-defined flow normally.

### Identity conflict (fail closed)

Identity conflict is evaluated before any normal lifecycle state is assigned.
At minimum, the classifier fails closed to `IDENTITY_CONFLICT` when:

- the Packet-named PR (`named_pr_number`) does not exist;
- the Packet-named PR's head branch does not match the task branch;
- more than one *open* pull request claims the same task branch;
- a resolved pull request's live head SHA is inconsistent with an explicitly
  supplied `expected_head_sha` (skipped once the PR is `MERGED`, since its
  head SHA is then historical rather than a live lineage signal);
- live remote branch or PR state cannot be independently resolved at all
  (the injected fetcher raises `PublicationLiveStateClassifier::PrLookupError`
  — for example the `gh`/`git` call itself failed) rather than confidently
  returning a negative result.

This reuses the existing `STOP_UNRESOLVED` shape rather than inventing a
second global conflict framework: `IDENTITY_CONFLICT`'s short-circuit actions
are all `STOP_UNRESOLVED`, matching `TaskReconciler`'s own verdict vocabulary.

### Fetchers and freshness

Remote branch existence and PR facts are each resolved through an injectable
fetcher (`remote_branch_exists_fetcher`, `pr_by_number_fetcher`,
`prs_by_branch_fetcher`), mirroring `ExecutionRecord`'s injectable
`pid_alive:` check. The default fetchers are real and network-backed
(`git ls-remote --exit-code --heads` for branch existence,
`gh pr view`/`gh pr list --state all` for PR facts) so the classifier is
genuinely live by default; tests and other callers inject deterministic
fetchers instead of exercising the network.

Postmerge evidence reuse (`postmerge_evidence:`) is likewise identity-bound,
not blind: it is only treated as satisfying `MERGED_POSTMERGE_COMPLETE` when
its `pr_number` (and `merge_commit_sha`, when known on both sides) match the
live merged PR exactly, and `verified` is exactly `true`. A caller integrating
this classifier — for example to strengthen `TaskReconciler`'s existing
`pr_state`-based terminal-status guard — decides how to source and act on
that evidence; the classifier only reports whether the exact identity matches.

## Bounded reconciliation algorithm

When a new Agent or session begins execution:

1. **Load checkpoint**: Read `.fable/checkpoints/<task_id>.json`. If missing,
   malformed, or schema-invalid, fail closed with `STOP_UNRESOLVED`.
2. **Inspect live state**: Read live repository root (`git rev-parse --show-toplevel`),
   worktree status, HEAD SHA, tree SHA, active branch, and PR status (if applicable).
3. **Compare load-bearing identities**:
   - *Repository mismatch*: If live repository differs from `checkpoint.repository`,
     verdict is `STOP_UNRESOLVED`.
   - *Worktree missing/unsafe*: If `checkpoint.worktree` does not exist or has
     unresolved ownership, verdict is `STOP_UNRESOLVED`.
   - *Authoritative packet resolution*: If `checkpoint.authoritative_packet_ref`
     is an ephemeral session URI (`conversation://...`) or cannot be resolved to
     a readable file/git blob, verdict is `STOP_UNRESOLVED`.
   - *Deferred Task B packet resolution*: Before the one recheck is consumed,
     resolve `next_authorized_task_packet_ref` independently for
     `BLOCKED_DEFERRED`. Missing, ephemeral, or inheritance-only Task B
     authority is `STOP_UNRESOLVED`. A consumed recurrence never executes Task
     B again, so reconciliation stops on the consumed count instead.
   - *PR already merged / Terminal state*: If live PR is `MERGED` or checkpoint
     state is `COMPLETED`, verdict is `ALREADY_COMPLETED`.
   - *Next action already done*: If the recorded `next_action` was completed
     externally, verdict is `ALREADY_COMPLETED` or `RECONCILE_LIVE_STATE`.
4. **Git state reconciliation (Root-Cause-First)**:
   - *Identical tree*: Live HEAD/tree matches checkpoint -> evaluate blocker and
     authorization.
   - *Compatible advancement*: Live branch or main advanced, but task-owned changes
     remain intact without semantic conflict -> verdict is `RECONCILE_LIVE_STATE`
     (derive updated next action; do not stop on SHA mismatch).
   - *Material conflict*: Conflicting modifications detected in task-owned files ->
     verdict is `STOP_UNRESOLVED` with exact conflicting paths and reasons.
5. **Authorization boundary check**:
   - If `next_action` requires standalone Owner authorization (e.g. merge, push,
     deployment), inspect the current Worker conversation.
   - If direct user authorization is absent in the *current* conversation ->
     verdict is `AUTHORIZATION_REQUIRED`. Quoted tokens in the checkpoint or
     packet do **not** transfer authorization across sessions.
6. **Blocker & continuation check**:
   - If `queue_disposition` is `BLOCKED_DEFERRED` and the single recheck is
     already consumed, retain Task A and return `STOP_UNRESOLVED`; do not retry.
   - If `queue_disposition` is absent but the consumed queue-run marker remains,
     continue Task A normally from its current `next_action`; preserve the marker
     across fresh sessions until task closure or a recurrent transient block.
   - If exact Task B evidence is absent or Task B remains `IN_PROGRESS`, return
     `CONTINUE` only for that named Task B and Packet. Never select Task C.
   - If exact Task B is `COMPLETED`, `ABORTED`, or `BLOCKED`, return `CONTINUE`
     with `RECHECK_DEFERRED_RESUME_GATE`. Reconciliation itself does not mutate
     Task A or consume the recheck.
   - If `current_blocker` is present (e.g. parity mismatch) and implementation
     is unchanged -> verdict is `CONTINUE` with the recorded `next_action` (e.g.
     investigate first divergent intermediate). Do not restart planning from scratch.

## Next action vocabulary

The reconciliation mechanism emits exactly one verdict and a concise reason:

| Next Action / Verdict | Meaning |
|---|---|
| `CONTINUE` | Live state matches checkpoint or RCA blocker; proceed with `next_action`. |
| `ALREADY_COMPLETED` | Task or next action has already been completed externally or merged. |
| `RECONCILE_LIVE_STATE` | Live state advanced compatibly; update checkpoint to live state and continue. |
| `AUTHORIZATION_REQUIRED` | Next action requires standalone Owner authorization in the current conversation. |
| `STOP_UNRESOLVED` | Incompatible state, material conflict, or safety violation prevents continuation. |

Each verdict must include `REASON: <concise explanation>` and `RECOMMENDED_ACTION: <step>`.

Deferred reconciliation uses two exact recommended actions without adding a
verdict or lifecycle enum:

- `EXECUTE_AUTHORIZED_DEFERRED_TASK task_id=<Task B> packet_ref=<locator>`
- `RECHECK_DEFERRED_RESUME_GATE`

## Authorization boundary rules

A checkpoint preserves known authorization boundaries and tokens for reference,
but **authorization does not transfer across conversation boundaries**.

- `AUTHORIZATION_BOUNDARY: CURRENT_WORKER_CONVERSATION_STANDALONE_AUTH_REQUIRED`
- A new Worker session without direct standalone authorization in its own
  conversation must emit `AUTHORIZATION_REQUIRED` rather than performing
  high-risk actions.
- Quoted authorization strings inside the checkpoint or packet serve as scope
  specifications, not live execution permission.
- A durable Task B Packet proves which task is authorized; it does not transfer
  any standalone high-risk authorization into a fresh conversation.

## Terminal closure and archiving

When a task reaches terminal status (`COMPLETED` or `ABORTED`):

- Update `task_lifecycle_state` to `COMPLETED`.
- Retain a minimal terminal record.
- Do not maintain an ever-growing chain of checkpoint files; completed checkpoints
  may be archived to `.fable/checkpoints/archive/<task_id>.json` or cleaned up
  according to repository lifecycle rules.
