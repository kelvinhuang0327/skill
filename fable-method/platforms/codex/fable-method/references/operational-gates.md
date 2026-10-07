# Operational gates

## Contents

- [Packet and authority](#packet-and-authority)
- [Authorization evidence and conversation boundary](#authorization-evidence-and-conversation-boundary)
- [Production mutation harness preflight](#production-mutation-harness-preflight)
- [Runtime outputs](#runtime-outputs)
- [Shared workstation resource budget](#shared-workstation-resource-budget)
- [Attempts and process termination](#attempts-and-process-termination)
- [Git action tiers](#git-action-tiers)
- [Worktrees and mutation evidence](#worktrees-and-mutation-evidence)
- [Continuity](#continuity)

These are conditional details for the shared workflow. They do not broaden a
Packet or authorize an action that the Packet forbids. An explicitly
forbidden command, path, source, transcript, or evidence class must never be
used as a fallback because preferred evidence is incomplete. Transcript is
not an authority fallback by default.

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

### Exact Packet resolution

The executable Packet is Worker authority after the Planner resolves the
authority chain. A complete Packet supplies Goal, Owner, scope, acceptance,
deliverable format, and forbidden actions or stop conditions. Verify at most
one pinned supporting locator. After routing, authorization, and repository
identity are confirmed, read a Packet-named input through its exact locator
first. If it is readable and matches, stop broad discovery for that authority;
do not scan workspaces, branches, worktrees, or transcripts to reconstruct it.
This only forbids reconstructing Packet authority; it does not prohibit
task-scoped source lookup after authority is resolved.

When the locator is unreadable or mismatched, distinguish `ABSENT`, permission
denied, network/read error, and identity mismatch. Do not guess a substitute or
bypass a STOP. Only use bounded adjacent resolution already allowed by the
Packet; a missing cross-lane input is `UPSTREAM_AUTHORITY_NOT_READY`.

For `AUTHORITATIVE_PACKET_PARTIAL`, infer only the smallest machine-checkable
acceptance supported by repository behavior and mark each item `[Inferred]`;
otherwise stop with `BLOCKED_MISSING_VERIFIABLE_ACCEPTANCE`. Packet steps marked
`MUST`, `REQUIRED`, `read completely`, `require`, or `STOP if` are mandatory. If
one cannot be executed, stop before mutation and report the exact step, reason,
impact, and required decision as `PACKET_REQUIRED_STEP_NOT_EXECUTED`.

If a Packet conflicts with a domain, schema, terminology, data, safety, or live
repository invariant, do not silently choose either side. Without an explicit
Owner-approved override, stop with `PLANNER_PACKET_CONTRACT_CONFLICT` and name
the Packet claim, repository evidence, impact, override status, and required
decision. For a complete Packet, limit live checks to repository/branch/HEAD/
worktree, Owner authorization, allowed and forbidden paths, named inputs and
outputs, and Packet-versus-live conflicts. Preserve its task class, route,
acceptance, deliverable, and stop conditions; do not re-plan or create a new
product brief.

For a target already in exact cleanup scope, `ALREADY_ABSENT` requires both its
filesystem path and Git registration to be absent; confirm an exact local
branch ref separately. An exact local branch ref confirmed absent makes only
that branch `ALREADY_ABSENT`; it does not establish another worktree's
absence. Read errors or insufficient permissions are not absence. Once
confirmed absent, stop searching and do not call delete; current absence alone
does not prove this task deleted the target. For a confirmed-absent target,
report `ALREADY_ABSENT`, do not execute delete, and do not claim this task
caused the absence.

### Bounded preflight and write boundary

Before mutation, confirm only facts that can invalidate execution: canonical
repository, branch, full HEAD/tree, worktree mode and status; staged, tracked,
untracked, and pre-existing paths by scope; applicable `AGENTS.md` and
`AGENTS.override.md`; Packet-named paths, direct consumers, runtime/import/
deploy chain, tools, authorization, and external effects.

The preflight STOP conditions are wrong repository, incompatible base/ref,
overlapping dirty ownership, active concurrent mutation, missing required
capability, or an explicit safety restriction. A compatible descendant,
unrelated out-of-scope dirt, or harmless environment difference is report-only.
When exact untracked-file count or identity is load-bearing, use a file-complete
inventory such as `git status --porcelain=v1 --untracked-files=all`; a collapsed
directory entry does not establish file cardinality. Do not require this
inventory when exact count or identity is immaterial.

For runtime/worktree cleanup or mutation, `ACTIVE_RUNTIME_OWNERSHIP` includes
either a running process owning or depending on the target, or a loaded/enabled
recurring scheduler bound to the target or its runtime source. Task-relevant
schedulers include launchd, cron, systemd, or an equivalent recurring
scheduler. When applicable, inspect only task-relevant schedule state,
WorkingDirectory, executable/interpreter, script path, and
import/module-root/PYTHONPATH bindings. A loaded/enabled scheduler bound to
the target worktree/source retains active ownership unless an authorized
ownership transition removes or repoints the binding. `NO_CURRENT_PROCESS`
does not establish `NO_ACTIVE_RUNTIME_OWNERSHIP`; do not turn this into a
workspace-wide audit.

Before deleting or replacing a checkout/worktree that is or was the exact
deployed runtime source, require `DEPLOYED_HEAD` to remain reachable through an
explicitly recognized durable Git source authority appropriate to the task.
Content-equivalent code/tree on main is NOT sufficient evidence that the exact
deployed source may be discarded. If exact `DEPLOYED_HEAD` has no durable
source authority, STOP: `DEPLOYED_HEAD_DURABLE_SOURCE_AUTHORITY_MISSING`. The
Worker MUST NOT automatically create a branch/tag/ref to satisfy this gate.
Creating or changing a preservation ref remains a separate Git mutation and
requires applicable task authority / authorization.

Make ownership explicit: `READ_BEFORE_EDIT: REQUIRED`,
`UNEXPLAINED_CONCURRENT_MUTATION: STOP`, and
`STALE_ASSUMPTION_AFTER_EXTERNAL_CHANGE: RE-READ BEFORE WRITE`. Re-read the
exact target before each edit. Preserve unrelated Owner state; never use cwd as
implicit authority, stage or edit outside declared scope, or reset, restore,
stash, or clean unrelated work. Adjacent source, tests, or configuration count
as scope only when acceptance demonstrably requires them. A new outcome,
unrelated subsystem, or material risk change requires a Planner Delta. Use an
opaque aggregate for protected paths; do not inspect protected or opaque
content.

Before inspecting content across committed objects, freeze exact refs/trees,
inventory metadata, classify paths as safe, protected, Owner-protected,
unknown, submodule, symlink, or special mode, and search only an exact safe
path/blob allowlist. Unknown or protected content fails closed. Start the
in-memory filesystem ledger before the first write; include source edits,
generated/runtime outputs, scratch, deleted temporaries, worktree materialization,
and Git/harness metadata. Do not create reports, logs, or scratch files outside
an explicitly authorized path.

For large structured command/tool output used as authority, capture it
completely, parse or filter it internally, then project only a bounded summary
to the conversational or harness surface; never derive an authority, count,
identity, or completeness claim from display output that may have been
truncated. If complete capture cannot be established and the missing portion
could alter the decision, state `UNKNOWN` rather than treat the displayed
subset as complete. This does not require a new durable evidence store; use
in-process parsing or an existing safe temporary mechanism.

Non-Git source roots remain supported: do not run `git init`, create a nested
repository, or turn a non-Git source root into a Git authority. Keep
`CONFIRMED`, `INFERRED`, and `UNKNOWN` evidence distinct.

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
the Owner sends one direct message into the target Worker conversation that
carries the Packet together with the exact action and target
(`AUTHORIZATION_HANDOFF_MODE: OWNER_DIRECT_PACKET`); a separate
authorization-only message is never required. A token the Packet quotes from
another conversation binds scope only and is not itself the evidence.

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

An explicit high-risk authorization may combine the exact action and target
with the executable Worker Packet in one direct Owner message
(`OWNER_DIRECT_PACKET_AUTHORIZATION`); an authorization-only message is not
required. Reuse only an applicable direct authorization in the same Worker
conversation. Every envelope names both exact action and exact target. An
irreversible or outward-facing action requires the user's own words as
`AUTH: user said "<exact authorization words>"`; quote the Packet only when it
directly authorizes that exact action and target, otherwise report
`PENDING: <action> - awaiting your authorization`.

Fail loudly: `UNSUPPORTED_REQUIRED_CAPABILITY -> STOP`,
`AMBIGUOUS_HIGH_RISK_AUTHORIZATION -> DENY / STOP`, and
`MISSING_REQUIRED_SECURITY_ENFORCEMENT -> REPORT, DO NOT PRETEND ENFORCED`.
Use the front-door `CAPABILITY_STATUS`: `ALLOWED` requires direct evidence;
`UNKNOWN` is never allowed. `BLOCKED` means direct evidence shows the required
execution path is unavailable or denied. After `BLOCKED`, do not repeat the
same preflight, seek repeated authorization as a substitute, or change
execution path to bypass the block. Retry only on exact
`CAPABILITY_STATE_CHANGED_EVIDENCE`. Owner authorization and harness capability
are separate facts; capability status does not change Planner routing
semantics.

## Production mutation harness preflight

Before executing a production or deployment mutation, resolve the exact
production entrypoint and the actual harness/wrapper/launcher chain that will
invoke it. Before consuming the real mutation, exercise that same harness
chain through a non-mutating execution-capability probe when the entrypoint
supports one — a read-only plan, a dry-run, a `--help`/version/capability
probe, or another explicitly non-mutating path through the same launcher.
This contract does not prescribe one universal probe command; the Worker
selects whichever non-mutating path the actual entrypoint supports, and this
preflight does not apply to an ordinary non-production command.

```text
HARNESS_EXECUTION_PERMISSION: ALLOWED | UNKNOWN | BLOCKED
```

This is [`CAPABILITY_STATUS`](../SKILL.md#intent-authorization-and-surgical-execution)'s
existing tri-state under one added constraint: the probe must run through the
exact same harness/wrapper/launcher chain that will invoke the production
mutation, not a different entry point merely assumed equivalent. If that
identical chain cannot be exercised non-mutatingly, report

```text
HARNESS_EXECUTION_PERMISSION: UNKNOWN
```

and stop before the production mutation rather than switching to an alternate
wrapper, introducing a heredoc/tmp-shell workaround, or assuming a different
invocation shape is equivalent. Owner authorization and harness capability
remain separate gates: a standalone Owner authorization for the mutation does
not itself establish `HARNESS_EXECUTION_PERMISSION`, and an `ALLOWED` probe
result never substitutes for standalone Owner authorization where one is
required.

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

## Shared workstation resource budget

This budget applies only to CPU-heavy work; do not artificially limit ordinary
low-CPU commands. CPU-heavy work includes replay, backtesting, simulation,
optimization, statistical resampling, batch feature generation,
multiprocessing/process pools, and CPU-heavy parallel test execution.

```text
RESOURCE_POLICY:
SHARED_WORKSTATION

CPU_BOUND_DEFAULT_WORKERS:
2

CPU_BOUND_MAX_WORKERS_WITHOUT_OWNER_AUTHORIZATION:
2

AUTO_CPU_SCALING:
FORBIDDEN

ALL_CORE_EXECUTION:
FORBIDDEN

WORKSTATION_SATURATION:
FORBIDDEN

LONGER_RUNTIME_PREFERRED_OVER_SATURATION:
YES
```

A Worker may reduce CPU-heavy concurrency from 2 to 1 without authorization.
It must not raise concurrency above 2 without direct Owner authorization; a
10-worker request is rejected without that authorization. Do not silently
increase concurrency.

Never use unrestricted CPU-heavy worker selection such as `--workers auto`,
`--workers > 2`, `-j auto`, `pytest -n auto`, `os.cpu_count()` worker pools,
`multiprocessing.cpu_count()` worker pools, `nproc`-derived pools, or duplicate
concurrent CPU-heavy runs for the same task.

Process-worker limits do not prevent hidden BLAS/OpenMP thread
oversubscription. Where technically applicable and semantics-preserving,
constrain:

```text
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
```

If more than 2 workers are genuinely required, stop and emit:

```text
RESOURCE_BUDGET_INCREASE_REQUIRED
WORKLOAD:
CURRENT_LIMIT: 2
REQUESTED_WORKERS:
WHY_TWO_WORKERS_ARE_INSUFFICIENT:
SEMANTIC_EFFECT_OF_WORKER_COUNT:
```

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

`FORCE_FALLBACK_AUTHORIZED: NO` is the live NON_FORCE Git authorization.
When it is `NO`, reject every Git force-family operation below. `-f` counts
only on a `git` argv, not on unrelated tools:

```text
GIT_FORCE_FAMILY:
--force
-f
--force-with-lease
--force-if-includes
```

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

For publication-bound work, the Final artifact gate's existing changed-path
authorization requirement resolves at PR-equivalent scope, not commit-local
scope. Fresh-resolve and freeze the intended canonical publication base and
the exact candidate head, then compute the changed-path scope as `git diff
--name-only <canonical-base>...<candidate-head>` (or an equivalent provider
compare API with the same base/head semantics), and validate that path set
against the task's authorized publication scope:

```text
PUBLICATION_SCOPE_AUTHORITY = INTENDED_CANONICAL_BASE ... CANDIDATE_HEAD
COMMIT_LOCAL_DIFF != INTENDED_PR_DIFF
```

`COMMIT_LOCAL_DIFF` — candidate-parent → candidate-head — is commit-local
evidence only and MUST NOT be accepted as proof that the intended PR is
scope-clean. If canonical-base → candidate-head contains unauthorized
ancestry paths, stop before push, PR creation, mark-ready, or merge. This
replaces the prior changed-path interpretation; it is not a second
publication-scope gate.

Before an authorized lifecycle mutation, read live state. If the desired state
already holds and exact identity matches, accept `SKIP_ALREADY_COMPLETE` /
`ALREADY_SATISFIED` without repeating the mutation; if a same-role resource has
conflicting identity, stop with `STOP_UNRESOLVED`.

Before a long Ready/merge/publication lifecycle, inspect only open PRs in the
same repository for overlap with already-known load-bearing paths. Path overlap
alone does not stop work. If an overlapping open PR also claims a competing
architecture decision, successor, canonical authority, or supersession,
stop with `OVERLAPPING_AUTHORITY_PUBLICATION_ORDER_REQUIRED` for publication
order resolution; do not create another governance tracker.

When merge/publication acceptance depends on exact-head verification and
canonical main advanced, inspect bounded path overlap and direct consumers of
candidate-modified state. Run only the focused prospective integration check
when a direct dependency exists; main advancing alone does not require a full
suite.

## Worktrees and mutation evidence

Use the exact Packet worktree path. Never create fallback, backup, scratch,
sibling, or alternate workspaces. For an existing worktree, classify it as
exact-head, behind remote, stable task-owned dirty, ownership unresolved,
duplicate-dirty blocked, already released clean baseline, absent, or unsafe.
Ownership unresolved and unsafe states stop execution.

Writer evidence is scoped, not name-based. Explicit bounded before/after
snapshots are authoritative. `TaskCheckpoint.scope_qualified_active_writer?`
is an optional convenience in the source helper
(`fable-method/scripts/task_checkpoint.rb`); the canonical platform build
excludes executable helpers, and helper absence is never a blocker. If it is
unavailable, capture the exact selected worktree and task-owned paths, branch,
HEAD, tree, full status (including staged, unstaged, and untracked paths), and
each owned path's identity/content metadata; repeat the same capture after a
bounded interval. Any difference is active mutation. With stable snapshots,
count a process only when directly observed target paths overlap the selected
worktree or ownership surface; a process name alone is insufficient. If writer
ownership remains unresolved, fail closed. The default quiescence observation
is about five seconds, but callers may choose another bounded interval.

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

When a mutation actually changes an installed/runtime HEAD, tree, executable
binding, working-directory binding, plist binding, or equivalent production
runtime identity, capture the transition provenance directly from the action:
before identity (`RUNTIME_HEAD_BEFORE`, `RUNTIME_TREE_BEFORE`,
`RUNTIME_BINDING_BEFORE`), action and timestamp (`RUNTIME_TRANSITION_ACTION`,
`RUNTIME_TRANSITION_AT`, `RUNTIME_TRANSITION_TASK_OR_RUN_ID`), after identity
(`RUNTIME_HEAD_AFTER`, `RUNTIME_TREE_AFTER`, `RUNTIME_BINDING_AFTER`), and
pre-transition identity (`ROLLBACK_TARGET`). The terminal handoff must report
these fields under `RUNTIME_TRANSITION_OCCURRED: YES`. Tasks that perform no
runtime identity transition report `RUNTIME_TRANSITION_OCCURRED: NO` without
the transition-specific fields.

This is reporting provenance only. It does not authorize runtime mutation or
launchctl actions, does not weaken standalone Owner authorization, does not
create automatic rollback, and does not permit inferring transition ownership
from reflog history.

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
