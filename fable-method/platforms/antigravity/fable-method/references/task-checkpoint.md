# Resumable Task Checkpoints

## Contents

- [Overview and core principles](#overview-and-core-principles)
- [Authoritative checkpoint contract](#authoritative-checkpoint-contract)
- [Storage and lifecycle](#storage-and-lifecycle)
- [Update semantics and revision protection](#update-semantics-and-revision-protection)
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
  "revision": 1
}
```

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

## Authorization boundary rules

A checkpoint preserves known authorization boundaries and tokens for reference,
but **authorization does not transfer across conversation boundaries**.

- `AUTHORIZATION_BOUNDARY: CURRENT_WORKER_CONVERSATION_STANDALONE_AUTH_REQUIRED`
- A new Worker session without direct standalone authorization in its own
  conversation must emit `AUTHORIZATION_REQUIRED` rather than performing
  high-risk actions.
- Quoted authorization strings inside the checkpoint or packet serve as scope
  specifications, not live execution permission.

## Terminal closure and archiving

When a task reaches terminal status (`COMPLETED` or `ABORTED`):

- Update `task_lifecycle_state` to `COMPLETED`.
- Retain a minimal terminal record.
- Do not maintain an ever-growing chain of checkpoint files; completed checkpoints
  may be archived to `.fable/checkpoints/archive/<task_id>.json` or cleaned up
  according to repository lifecycle rules.
