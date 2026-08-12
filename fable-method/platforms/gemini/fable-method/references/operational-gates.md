# Operational gates

## Contents

- [Packet and authority](#packet-and-authority)
- [Runtime outputs](#runtime-outputs)
- [Attempts and process termination](#attempts-and-process-termination)
- [Git action tiers](#git-action-tiers)
- [Worktrees and mutation evidence](#worktrees-and-mutation-evidence)
- [Continuity](#continuity)

These are conditional details for the shared workflow. They do not broaden a
Packet or authorize an action that the Packet forbids.

## Packet and authority

When a Packet supplies Phase 0 or an equivalent pre-mutation gate, complete it
before the first write. Retain exact commands, results, authority HEAD/tree,
branch/detached state, staged/dirty/untracked inventories, ownership, and
required stability snapshots. A final snapshot cannot replace missing
pre-mutation evidence.

If a Packet requires a clean or owned worktree, do not adopt dirty, staged,
untracked, or differently checked-out content merely because paths match the
future allowlist. Require an explicit takeover decision with exact paths,
hashes, refs, ownership transfer, and allowed continuation actions.

Before using a mandatory Packet command or environment, inspect it. Replacing
it with a convenient command, random port, alternate entry point, or unallowed
output root is a contract conflict:

```text
PLANNER_PACKET_CONTRACT_CONFLICT
REQUIRED_METHOD:
ACTUAL_AVAILABLE_METHOD:
BEHAVIORAL_DIFFERENCE:
EVIDENCE_IMPACT:
SMALLEST_SAFE_NEXT_ACTION:
```

Permission, capability, or API failures are `UNRESOLVED`; they are not proof
that a branch, ruleset, review, resource, or previous mutation is absent. When
a repository and ref are pinned, retain repository, exact ref, path, symbol,
and evidence classification for every load-bearing conclusion.

## Runtime outputs

Before a test, browser, server, reporter, profile, cache, trace, video,
screenshot, log, PID, or temporary-output command:

1. inspect direct and indirect output paths;
2. resolve OS temporary paths when discoverable;
3. compare every output root with the Packet's runtime-output allowlist;
4. record before-state;
5. stop before execution if any path is outside the allowlist.

Use:

```text
ARTIFACT_OUTPUT_PATH_CONFLICT
EXPECTED_OUTPUT_ALLOWLIST:
ACTUAL_OUTPUT_PATH:
OUTPUT_SOURCE:
CLEANED_LATER:
AUTHORIZED:
SMALLEST_SAFE_NEXT_ACTION:
```

Cleanup does not retroactively authorize an output. A created-then-deleted
artifact remains in the write ledger.

## Attempts and process termination

For every non-trivial retry, keep:

```text
ATTEMPT_LEDGER:
attempt number; command/action; HEAD/tree; start/end state; result;
failure/timeout; artifacts written/overwritten/deleted; termination method;
whether later evidence superseded it.
```

Keep failed, aborted, timed-out, hung, import-failed, assertion-failed,
terminated, rewritten, overwritten, and deleted-artifact attempts. A final
successful attempt can be named `FINAL_SUCCESSFUL_ATTEMPT`, but does not erase
earlier failures.

For a hanging process, prefer application shutdown, then close the owning
browser/context/server, then ordinary termination when authorized. If only
force termination remains and force is forbidden, stop with:

```text
STOP_FORCE_PROCESS_TERMINATION_REQUIRED
PROCESS:
PID:
GRACEFUL_ACTIONS_ATTEMPTED:
CURRENT_STATE:
TASK_OWNED:
FORCE_AUTHORIZED: NO
REMAINING_RISK:
SMALLEST_SAFE_NEXT_ACTION:
```

Every termination belongs in `PROCESS_TERMINATION_LEDGER`.

## Git action tiers

Worktree mode does not authorize publication. Resolve these independently:

```text
REMOTE_STATUS: NONE | CONFIGURED | UNKNOWN
COMMIT_AUTHORIZED: YES | NO
PUSH_AUTHORIZED: YES | NO
DRAFT_PR_AUTHORIZED: YES | NO
MARK_READY_AUTHORIZED: YES | NO
MERGE_AUTHORIZED: YES | NO
LOCAL_INTEGRATION_AUTHORIZED: YES | NO
LOCAL_BRANCH_DELETE_AUTHORIZED: YES | NO
REMOTE_BRANCH_DELETE_AUTHORIZED: YES | NO
```

Default absent fields to `NO`. Completion does not imply commit; commit does
not imply push; push does not imply PR, readiness, merge, or deletion. Report
unauthorized actions as `PENDING` or `NOT APPLICABLE` and never classify the
local implementation as failed solely because publication was not authorized.

## Worktrees and mutation evidence

Use the exact Packet worktree path. Never create fallback, backup, scratch,
sibling, or alternate workspaces. For an existing worktree, classify it as
exact-head, behind remote, stable task-owned dirty, ownership unresolved,
duplicate-dirty blocked, already released clean baseline, absent, or unsafe.
Ownership unresolved and unsafe states stop execution.

Before each load-bearing mutation, retain:

```text
MUTATION_NAME:
COMMAND_OR_TOOL:
RESULT:
READ_AFTER_WRITE:
FINAL_STATE:
```

This applies to source writes, Git lifecycle, worktree changes, runtime
cleanup, evidence roots, manifests, and checksums.

## Continuity

At stable milestones or before handoff, preserve observable state only:

```text
CONTEXT_CHECKPOINT
CURRENT_MILESTONE:
LIVE_EXECUTION_STATE:
FORWARD_PLAN:
LEDGER_REFERENCES:
```

Include exact repo/branch/HEAD/tree, worktree, dirty/staged paths, active
processes, pending external mutations, next action/milestone, blockers,
settled decisions, and unresolved Owner decisions. Continuation never expands
authorization and never preserves private chain-of-thought.
