# Gemini integration gates

## Contents

- [Runtime output and process gates](#runtime-output-and-process-gates)
- [Authority and worktree gates](#authority-and-worktree-gates)
- [Phase 0 and mutation evidence](#phase-0-and-mutation-evidence)
- [Judge terminal and evidence seal](#judge-terminal-and-evidence-seal)
- [Continuity](#continuity)

These are Gemini-harness integration rules. The shared Fable workflow remains
the authority for task class, scope, Packet acceptance, route, authorization,
and lifecycle vocabulary.

## Runtime output and process gates

Before any test, browser, server, reporter, profile, cache, trace, video,
screenshot, log, PID file, or temporary-output command:

1. inspect direct and indirect output paths, including OS temporary paths;
2. compare every output root with the Packet's `RUNTIME_OUTPUT_ALLOWLIST`;
3. record before-state;
4. stop before execution if a path is not allowed.

Use this stop record:

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
output remains in `FILES_WRITTEN_DURING_TASK` and
`FILES_DELETED_BEFORE_END`.

For a hanging process, prefer graceful application shutdown, then normal
termination when authorized. If only `SIGKILL`/`kill -9` remains and force is
forbidden, stop with `STOP_FORCE_PROCESS_TERMINATION_REQUIRED` and record the
process, PID, attempted graceful actions, ownership, risk, and safe next step.
Every termination belongs in `PROCESS_TERMINATION_LEDGER`.

When a task claims a named service worker controls a page, keep each
registration source separate and verify the formal page URL, boot symbol,
registered worker URL/scope, controller script URL, active script URL, runtime
scope, worker source, and whether multiple registrations exist. If identities
do not resolve, return `SERVICE_WORKER_IDENTITY_UNRESOLVED`; generic
controller-active evidence is not enough.

## Authority and worktree gates

Load the Skill and Packet before any decision-bearing repository read. A Packet
Phase 0 must finish before the first write and retain exact commands, results,
stability snapshots when required, authority HEAD/tree, branch state, staged/
dirty/untracked inventories, ownership, and refs. A final snapshot cannot
replace a missing pre-write gate.

When the Packet requires two stability snapshots, retain the exact fields:

```text
PHASE0_COMPLETED_BEFORE_FIRST_WRITE: YES
PHASE0_SNAPSHOT_COUNT: 2
PHASE0_SNAPSHOTS_IDENTICAL: YES
PHASE0_AUTHORITY_RESOLVED: YES
```

Do not adopt dirty, staged, untracked, or differently checked-out content merely
because paths match an allowlist. Require an explicit takeover Packet with
exact paths/hashes, refs, ownership transfer, and permitted actions.

Permission or capability failures are `UNRESOLVED`, not evidence that a branch,
ruleset, review, resource, or prior mutation is absent. When a Packet pins a
repository and ref, use the exact repository and immutable ref for load-bearing
reads; never substitute the current working directory or current main.

Retain `ALL_LOAD_BEARING_READS_USED_EXACT_REPOSITORY_AND_REF: YES` when that
Packet gate applies. A permission error remains `UNRESOLVED`.

Use the exact Packet worktree path. Do not create fallback, backup, scratch,
sibling, or alternate workspaces. If the Packet declares an existing worktree,
classify its live state before action; `DIRTY_OWNERSHIP_UNRESOLVED` and
`UNKNOWN_UNSAFE_STATE` stop execution.

If a dirty takeover is explicitly allowed, retain
`DIRTY_STATE_TAKEOVER_EXPLICITLY_AUTHORIZED: YES`; otherwise record `NO` and
stop. Never use the current working directory as implicit authority.

Git actions are independent decisions. `COMMIT_AUTHORIZED` does not imply
push; push does not imply PR, readiness, merge, or branch deletion. Missing
authorization is `PENDING` or `NOT APPLICABLE`, not implementation failure.

## Phase 0 and mutation evidence

Before any load-bearing mutation, retain:

```text
MUTATION_NAME:
COMMAND_OR_TOOL:
RESULT:
READ_AFTER_WRITE:
FINAL_STATE:
```

The same evidence rule applies to runtime cleanup, worktree changes, evidence
roots, manifests, and checksums. Once an exact stop condition occurs, perform
no later mutation or alternate method without a new explicit Owner override;
report `POST_STOP_MUTATIONS: NONE`.

If a future execution Packet is drafted, it must be self-contained: exact
Owner authorization, `/fable-method`, mode, repository/ref/tree, worktree,
tracked paths, output roots, native commands, Judge policy, mutation limits,
read-after-write checks, lifecycle routes, and explicit no-publication limits.

Retain `FUTURE_PACKET_SELF_CONTAINED_AND_PLACEHOLDER_FREE: YES` only after
checking that its repository/ref, paths, output roots, commands, Judge policy,
and publication limits are exact and placeholder-free.

## Judge terminal and evidence seal

After invoking a required fresh Judge, enter
`WAITING_FOR_JUDGE_TERMINAL_VERDICT`. Do not commit, integrate, publish,
switch/release a worktree, delete a branch, generate a final report, manifest,
or checksum before the terminal verdict is received. Retain the Judge provider,
session, input HEAD/tree, depth, verdict, and chronology.

Do not prewrite a presumed Judge report or lifecycle conclusion. Run Packet-
required post-Judge checks in the Packet's order. Integration requires
`JUDGE_TERMINAL_VERDICT: VERIFIED`, no post-Judge source/test edit, complete
post-Judge acceptance, equal candidate/Judge trees, and a valid base.

Retain `JUDGE_VERDICT_RECEIVED_BEFORE_NEXT_MUTATION: YES` and
`JUDGE_REPORT_CREATED_AFTER_VERDICT: YES`. When the Packet requires each
post-Judge check, record its actual result rather than substituting a prior
green run:

```text
POST_JUDGE_FINAL_SUITE_RUN:
POST_JUDGE_FOCUSED_TEST_RUN:
POST_JUDGE_LINT_RUN:
POST_JUDGE_TYPECHECK_RUN:
POST_JUDGE_BUILD_RUN:
POST_JUDGE_BROWSER_VALIDATION_RUN:
POST_JUDGE_FINAL_ACCEPTANCE_FAILURES:
```

Create a final evidence seal only after the terminal verdict, post-Judge checks,
authorized lifecycle actions, reports, and final runtime ledger. Generate a
manifest before checksums, verify them, and make no later evidence-file edit.

The seal records:

```text
SEAL_CREATED_AFTER_JUDGE: YES
SEAL_CREATED_AFTER_FINAL_ACCEPTANCE: YES
SEAL_CREATED_AFTER_LIFECYCLE_ACTIONS: YES
POST_SEAL_EVIDENCE_EDIT: NO
RUNTIME_LEDGER_UPDATED_AFTER_TRANSIENT_CLEANUP: YES
```

## Continuity

At stable milestones or before a handoff, preserve observable state only:

```text
CONTEXT_CHECKPOINT
CURRENT_MILESTONE:
LIVE_EXECUTION_STATE:
FORWARD_PLAN:
LEDGER_REFERENCES:
```

Include exact repository/branch/HEAD/tree, worktree, dirty/staged paths,
active processes, pending external mutations, next action/milestone,
foreseeable blockers, settled decisions, and unresolved Owner decisions.
Continuation never expands authorization.
