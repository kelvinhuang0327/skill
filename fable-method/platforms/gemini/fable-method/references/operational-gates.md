# Operational gates

## Contents

- [Packet and authority](#packet-and-authority)
- [Authorization evidence and conversation boundary](#authorization-evidence-and-conversation-boundary)
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

## Authorization evidence and conversation boundary

A standalone Owner authorization is evidence only where the Worker can
observe it directly. Distinguish the evidence source explicitly:

```text
AUTHORIZATION_SOURCE: CURRENT_WORKER_CONVERSATION_USER_MESSAGE
```

is valid when the exact Owner words are directly observable as a user
message in this Worker's own conversation, the exact action is covered, the
exact target is covered, and the authorization has not been superseded. By
itself,

```text
AUTHORIZATION_SOURCE: QUOTED_IN_PACKET_OR_HANDOFF
```

is not valid: a token quoted inside a Packet, handoff report, Planner
summary, or evidence file may bind or describe scope, but does not
substitute for the direct Owner message when standalone authorization is
required — whether the quote originated in an earlier turn, a different
agent, or the current Planner.

When the Packet and the Worker are not guaranteed to share a conversation,
the Owner delivers standalone authorization as two separate messages: the
exact token as its own message into the target Worker conversation first,
then the Packet. The Packet may still quote the token for scope binding, but
must state that the quote is not itself the evidence.

When the current Worker conversation already contains the exact direct
Owner authorization, the requested action stays within that exact scope, and
every other live gate still passes:

```text
REDUNDANT_CONFIRMATION_REQUIRED: NO
```

Proceed rather than asking again merely because the action is high-risk or
because a Packet also quotes the token. This stops applying the moment scope
or target changes, a fallback was not authorized, the authorization is
ambiguous, or the Worker has only ever seen a quoted token rather than a
direct message. A newly discovered action, target, fallback, or remote
mutation never inherits a prior authorization; treat it as
`PENDING: <exact new action> - awaiting your authorization`. One direct
standalone authorization may still name several exact high-risk actions in
one envelope (see Git action tiers below) — the conversation boundary governs
how that envelope must be delivered, not how many actions it may contain.

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
LOCAL_WORKTREE_REMOVAL_AUTHORIZED: YES | NO
LOCAL_BRANCH_DELETE_AUTHORIZED: YES | NO
FORCE_FALLBACK_AUTHORIZED: YES | NO
REMOTE_BRANCH_DELETE_AUTHORIZED: YES | NO
```

Default absent fields to `NO`. Completion does not imply commit; commit does
not imply push; push does not imply PR, readiness, merge, or deletion. Report
unauthorized actions as `PENDING` or `NOT APPLICABLE` and never classify the
local implementation as failed solely because publication was not authorized.

These tiers stay independently permissioned — local worktree removal, local
branch normal deletion, an exact force fallback, remote branch deletion, and
PR mutation are five separate permissions — but one standalone Owner
authorization may list several of them together in one exact envelope when
every target, action, expected tip/identity, and fallback precondition in it
is named explicitly. An action or fallback the envelope does not name stays
unauthorized, and a newly discovered target is never authorized merely
because it resembles a named one:

```text
UNLISTED_ACTION: NOT AUTHORIZED
UNLISTED_FALLBACK: NOT AUTHORIZED
NEWLY_DISCOVERED_TARGET: NOT AUTHORIZED
```

See [Authorization evidence and conversation
boundary](#authorization-evidence-and-conversation-boundary) above for how
that one standalone authorization must reach this Worker's own conversation
before any of these permissions take effect.

`FORCE_FALLBACK_AUTHORIZED: YES` takes effect only for the exact fallback the
Packet names, and only while every gate below still holds live: the lineage
or lifecycle verdict it depended on is unchanged, the target's tip is
unchanged, any successor integration remains reachable, the target is not
checked out or otherwise in active use, no new commits or task-owned
dirty/untracked state exist on it, and the primary action's refusal is
attributable only to expected Git ancestry/semantics rather than an
unexplained state change. If any gate fails, stop or skip that target instead
of falling back; a generic cleanup authorization never substitutes for this.

For a multi-target lifecycle bundle, default to skipping an unsafe or
drifted target, recording the exact reason, and continuing the remaining
independently authorized targets, unless the Packet declares the bundle
atomic. Still stop the entire bundle for a wrong repository, authorization
ambiguity, canonical authority instability, a shared destructive-scope
mismatch, or evidence corruption that affects the whole bundle rather than
one target.

When the Packet marks prior lifecycle or lineage evidence reusable, apply the
same bounded-check principle as any other pinned locator: verify the evidence
source and the exact live target identities and gates it names, then act — do
not rebuild the Planner's lineage analysis, run a generic authority search, or
redo a full reconciliation. A contradiction invalidates only the affected
evidence and stops or skips that target.

## Worktrees and mutation evidence

Use the exact Packet worktree path. Never create fallback, backup, scratch,
sibling, or alternate workspaces. For an existing worktree, classify it as
exact-head, behind remote, stable task-owned dirty, ownership unresolved,
duplicate-dirty blocked, already released clean baseline, absent, or unsafe.
Ownership unresolved and unsafe states stop execution.

Writer evidence is scoped, not name-based. Use
`TaskCheckpoint.scope_qualified_active_writer?` with before/after snapshots of
the exact branch, HEAD, tree, status, and task-owned paths. Snapshot drift is
active mutation. With stable snapshots, a process counts only when observed
target paths overlap the selected worktree or ownership surface; an unrelated
`pytest`, `python`, or Agent process elsewhere is not
`ACTIVE_CONCURRENT_MUTATION`. The default quiescence observation is a bounded
approximately-five-second interval, not a magic constant or a polling loop.

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

When persisting durable continuation state across sessions/models, use the
contract and bounded live reconciliation algorithm in [task-checkpoint](task-checkpoint.md).
