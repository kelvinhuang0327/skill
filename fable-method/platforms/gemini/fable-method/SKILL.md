---
name: fable-method
description: A step-by-step problem-solving loop (classify the ask, define done, gather evidence, decide, act surgically, verify by observation, report outcome-first). Use when the user says "/fable-method", "use the fable method", or "approach this like Fable", or proactively when starting any multi-step task that no task-specific skill covers. Subcommands - plan (stop after the plan), audit (grade finished work against the loop), report (rewrite an answer outcome-first).
trigger: /fable-method
---

# Worker Protocol Overlay

This overlay governs execution of authoritative Planner Task Packets.

When an authoritative Planner Packet is present, it outranks the generic
problem-solving loop for task class, scope, authorization, repository identity,
worktree routing, Judge routing, lifecycle, evidence reuse and terminal output.

The generic Fable loop remains active only as a supporting evidence and
verification method. It must not re-plan or broaden an authoritative Packet.

## First Substantive Output Contract

Before any external tool call, shell command, repository read, connector call or
filesystem inspection, output exactly these routing fields:

TASK_CLASS: <STATE_CHANGING_IMPLEMENTATION | READ_ONLY_COMPLETION_REVIEW | PLANNING_ONLY | PURE_QA>

WORKER_ROUTE: <FAST | STANDARD | STANDARD_JUDGED | LOOP_JUDGED | NOT_APPLICABLE>

JUDGE_MODE: <FRESH_CONTEXT | SELF_CHECK_ONLY | NOT_APPLICABLE>

These markers must precede every tool call. A later report cannot repair a
missing first-output contract.

## Task Classes

STATE_CHANGING_IMPLEMENTATION:
Changes source, tests, configuration, Git lifecycle, PR state, deployment state
or another external system.

READ_ONLY_COMPLETION_REVIEW:
Verifies completed work without source or state mutation.

PLANNING_ONLY:
Produces a plan, inventory, handoff, audit or implementation recommendation,
without applying product changes.

PURE_QA:
Answers a question without project mutation or a formal completion audit.

## Runtime Classification and Attempt Integrity Gates

### PURE_QA Mechanical Boundary

TASK_CLASS is a closed enum.

PURE_QA is allowed only when the task:

- answers a question;
- performs no project verification command;
- launches no browser, server, test runner or long-running process;
- creates no evidence package, screenshot, manifest, report or runtime artifact;
- performs no filesystem, Git or external-system mutation.

If the task will execute tests, start a server, launch a browser, inspect a
live runtime, or create evidence artifacts, it is not PURE_QA.

Use:

TASK_CLASS: READ_ONLY_COMPLETION_REVIEW

for verification, acceptance, audit and evidence-generation tasks that do
not modify product source or Git state.

Use:

TASK_CLASS: STATE_CHANGING_IMPLEMENTATION

when product source, tests, configuration, Git state or external systems may
be changed.

Task-specific descriptions belong in TASK_SUBTYPE or REPRODUCTION_MODE,
never in TASK_CLASS.

Before the first tool call, mechanically check whether the planned commands
include any of:

- test runner;
- browser;
- server;
- runtime process;
- screenshot;
- evidence root;
- manifest;
- generated report.

If yes, PURE_QA is invalid and must be corrected before execution.

### Runtime Output Allowlist Gate

Temporary output is still output.

Every file or directory created by a test, browser, server, reporter,
profile, cache, trace, video, screenshot, log or operating-system temp API
must be inside the Packet's RUNTIME_OUTPUT_ALLOWLIST before execution.

This includes:

- browser profiles;
- Chromium user-data directories;
- Playwright test-results;
- traces;
- videos;
- screenshots;
- HTML, JSON and blob reporters;
- server logs;
- PID files;
- cache directories;
- os.tmpdir() output;
- mkdtemp() output;
- temporary files later deleted by finally/cleanup handlers.

Cleanup after execution does not retroactively authorize an output path.

A runtime path that is automatically deleted is still:

FILES_WRITTEN_DURING_TASK

and, after deletion:

FILES_DELETED_BEFORE_END

Before running an output-producing command:

1. inspect the test, script and config;
2. resolve direct and indirect output paths;
3. resolve operating-system temporary paths when statically or dynamically
   discoverable;
4. compare every output root against RUNTIME_OUTPUT_ALLOWLIST;
5. record before-state;
6. STOP before execution if any path is outside the allowlist.

Use:

ARTIFACT_OUTPUT_PATH_CONFLICT

EXPECTED_OUTPUT_ALLOWLIST:
ACTUAL_OUTPUT_PATH:
OUTPUT_SOURCE:
CLEANED_LATER:
AUTHORIZED:
SMALLEST_SAFE_NEXT_ACTION:

Do not execute first and justify the path afterward.

### Retry and Attempt Ledger Gate

A successful retry does not erase earlier failures.

Every non-trivial task that performs retries must maintain:

ATTEMPT_LEDGER:

For each attempt record:

- attempt number;
- command or action;
- tree/head identity;
- start and end state;
- result;
- failure or timeout;
- artifacts written;
- artifacts overwritten;
- artifacts deleted;
- process termination method;
- whether later evidence superseded the attempt.

Final reports must include every:

- failed attempt;
- aborted attempt;
- timeout;
- hung process;
- import or environment failure;
- assertion failure;
- process termination;
- rewritten temporary script;
- overwritten task-owned artifact;
- deleted temporary artifact.

A final successful attempt may be reported separately as:

FINAL_SUCCESSFUL_ATTEMPT:

but the overall task report must not say:

FAILURES: NONE

when earlier attempts failed or were aborted.

Earlier failed attempts do not necessarily invalidate a later successful
exact-tree run, but they remain part of execution history and filesystem
accounting.

### Process Termination and Force Gate

SIGKILL, kill -9 and equivalent hard termination are force actions.

If the Packet forbids force, the Worker must not use them.

Preferred shutdown order:

1. application-specific graceful shutdown;
2. close browser/context/server through the owning API;
3. SIGTERM or ordinary process termination when authorized;
4. wait for bounded graceful exit;
5. STOP if only force termination remains.

If only SIGKILL or kill -9 can stop the process and force is forbidden,
return:

STOP_FORCE_PROCESS_TERMINATION_REQUIRED

PROCESS:
PID:
GRACEFUL_ACTIONS_ATTEMPTED:
CURRENT_STATE:
TASK_OWNED:
FORCE_AUTHORIZED: NO
REMAINING_RISK:
SMALLEST_SAFE_NEXT_ACTION:

Do not continue the task after an unauthorized force termination.

Every process termination must appear in:

PROCESS_TERMINATION_LEDGER:

### Service Worker Identity Gate

Service-worker control is not proof of service-worker identity.

When a task claims that a specific service worker controls a page, verify
all of:

- formal page URL;
- formal boot path;
- registration source file and symbol;
- registration worker URL;
- registration scope;
- navigator.serviceWorker.controller.scriptURL;
- active registration.active.scriptURL;
- registration scope observed at runtime;
- worker source file corresponding to the runtime script URL.

If multiple registration sources exist, keep them separate.

Do not merge:

- one page's registration source;
- another page's worker file;
- a generic controller-active result;

into one PASS claim.

Required report fields:

FORMAL_PAGE_URL:
FORMAL_BOOT_REGISTRATION_SYMBOL:
FORMAL_REGISTERED_WORKER_URL:
FORMAL_REGISTRATION_SCOPE:
RUNTIME_CONTROLLER_SCRIPT_URL:
RUNTIME_ACTIVE_SCRIPT_URL:
RUNTIME_REGISTRATION_SCOPE:
IDENTITY_MATCH:
MULTIPLE_REGISTRATIONS_OBSERVED:

If identity is unresolved:

SERVICE_WORKER_IDENTITY_UNRESOLVED

Do not claim that a named worker controls the formal page.

### Mandatory Packet Step Integrity

The Worker may not replace a mandatory Packet command or environment with a
convenient substitute without reporting a contract conflict.

Examples:

- replacing the required server command with a custom server;
- replacing the required baseURL with a random port;
- replacing formal boot flow with a secondary HTML entry;
- replacing an allowed output root with os.tmpdir();
- replacing independent Judge execution with same-context self-check.

When a mandatory environment cannot be used:

PLANNER_PACKET_CONTRACT_CONFLICT

REQUIRED_METHOD:
ACTUAL_AVAILABLE_METHOD:
BEHAVIORAL_DIFFERENCE:
EVIDENCE_IMPACT:
SMALLEST_SAFE_NEXT_ACTION:

## Worker Routes

FAST:
Only for truly trivial, low-risk, reversible changes when every FAST condition
is satisfied. Outward lifecycle actions are never FAST.

STANDARD:
Normal bounded implementation or authorized lifecycle work without an
independent Judge requirement.

STANDARD_JUDGED:
State-changing or high-value work requiring a fresh-context Judge.

LOOP_JUDGED:
Complex multi-stage work requiring bounded Worker/Judge remediation cycles.

NOT_APPLICABLE:
Read-only planning, metadata-only inspection or pure Q&A where no Worker
execution route applies.

A reviewed-head lifecycle exception may use STANDARD with
JUDGE_MODE: NOT_APPLICABLE only when all are true:

- the exact head was already independently reviewed;
- current head, base, cumulative scope and required checks still match;
- no source or test edit is made;
- the current task performs only explicitly authorized lifecycle actions;
- all lifecycle preconditions are revalidated from live evidence.

## Planner Packet Authority and Conflict Gate

The current authoritative Planner Packet defines:

- task scope;
- allowed and forbidden paths;
- authority sources;
- authorization;
- repository and ref identity;
- worktree mode;
- Judge policy;
- lifecycle actions;
- required evidence;
- STOP and terminal vocabulary.

Do not silently optimize away mandatory Packet steps.

A Packet conflict exists only when a correctly resolved authoritative source
materially contradicts the Packet.

Missing evidence, an unresolved handoff or absence in an unrelated repository is
not automatically a Planner Packet conflict.

## Handoff Authority Resolution

Supported authority modes:

- SELF_CONTAINED_INLINE
- REFERENCED_HANDOFF
- REPOSITORY_PINNED
- INHERITED_PROJECT_CHAIN
- NONE_REQUIRED

Before reporting missing authority, perform one bounded pass over:

1. inline Packet evidence;
2. attached or named artifacts;
3. referenced prior handoffs, reports or manifests;
4. pinned repository/ref/path/symbol;
5. explicitly inherited project-chain context.

Never infer authority solely from the current working directory (`CURRENT_WORKING_DIRECTORY_USED_AS_IMPLICIT_AUTHORITY` is forbidden).

If unresolved, return:

HANDOFF_AUTHORITY_UNRESOLVED

MISSING_AUTHORITY:
AUTHORITY_MODE:
SOURCES_CHECKED:
LAST_RESOLVED_HANDOFF:
IMPACT:
REQUIRED_HANDOFF_REPAIR:

## Owner Authorization

A first non-empty line of:

Owner Authorization: <TOKEN>

followed by the task specification is valid embedded single-prompt
authorization unless the Packet explicitly declares a genuinely high-risk
standalone authorization requirement.

Do not request the same Owner Authorization again.

Owner Authorization and platform/harness permission are different.

When an already-authorized action is blocked only by an interactive permission
gate, report:

HARNESS_PERMISSION_BLOCKED

OWNER_AUTHORIZATION: ALREADY_PRESENT
AUTHORIZED_ACTION:
BLOCKED_TOOL_OR_COMMAND:
MUTATIONS_ALREADY_COMPLETED:
MUTATIONS_NOT_COMPLETED:
REQUIRED_USER_ACTION: approve the platform or harness permission prompt

Do not reinterpret a harness gate as missing Owner Authorization.
Do not bypass it with force, a weaker command or an alternate lifecycle path.

## Git Action Authorization

Worktree Mode does not authorize Git publication actions.

Every Packet must independently resolve:

REMOTE_STATUS: <NONE | CONFIGURED | UNKNOWN>
COMMIT_AUTHORIZED: <YES | NO>
PUSH_AUTHORIZED: <YES | NO>
DRAFT_PR_AUTHORIZED: <YES | NO>
MARK_READY_AUTHORIZED: <YES | NO>
MERGE_AUTHORIZED: <YES | NO>
LOCAL_INTEGRATION_AUTHORIZED: <YES | NO>
LOCAL_BRANCH_DELETE_AUTHORIZED: <YES | NO>
REMOTE_BRANCH_DELETE_AUTHORIZED: <YES | NO>

Default every field to NO when absent.

Implementation completion does not imply commit authorization.
Commit authorization does not imply push authorization.
Push authorization does not imply PR authorization.
PR authorization does not imply mark-ready or merge authorization.
Merge authorization does not imply branch deletion authorization.

Execute only actions explicitly authorized by the current Packet.

When a Git action is not authorized, report it as PENDING or NOT APPLICABLE.
Do not classify the implementation itself as failed solely because publication
was not authorized.

## Worktree Lifecycle Routing

Supported modes:

- NOT_APPLICABLE
- REUSABLE_AGENT_WORKTREE
- EPHEMERAL_TASK_WORKTREE
- EXISTING_TASK_WORKTREE

Use the exact Packet path. Never create fallback, backup, scratch, sibling or
alternate workspaces.

For EXISTING_TASK_WORKTREE, classify:

- ACTIVE_EXACT_PR_HEAD
- ACTIVE_BEHIND_REMOTE_PR_HEAD
- ACTIVE_STABLE_TASK_OWNED_DIRTY
- DIRTY_OWNERSHIP_UNRESOLVED
- SAFE_FAST_FORWARD_BLOCKED_BY_DIRTY_DUPLICATE
- ALREADY_RELEASED_CLEAN_BASELINE
- EXISTING_PATH_ABSENT
- UNKNOWN_UNSAFE_STATE

ALREADY_RELEASED_CLEAN_BASELINE requires:

- exact reusable path exists;
- detached HEAD;
- HEAD equals current fetched origin/main;
- clean status;
- no staged files;
- no active Worker;
- PR and task refs remain resolvable;
- no task-owned dirty content remains.

When already released:

- do not checkout the task branch;
- do not recreate a task worktree;
- do not repeat an unnecessary detach;
- perform idempotent lifecycle verification only;
- retain task branches while the PR remains open.

Report:

WORKTREE_STATE_ROUTE: ALREADY_RELEASED_CLEAN_BASELINE
REUSABLE_WORKSPACE_RELEASE_ACTION: ALREADY_COMPLETE
REUSABLE_WORKSPACE_SWITCH_PERFORMED: NO

## Judge and Evidence Policy

JUDGE_MODE:

- FRESH_CONTEXT
- SELF_CHECK_ONLY
- NOT_APPLICABLE

JUDGE_DEPTH when a Judge is used:

- BOUNDED
- FULL
- DELTA

Do not invent composite Judge-depth values.

Initial judged implementation normally uses BOUNDED.
Use FULL only for a concrete FULL trigger or explicit Planner/Owner requirement.
Use DELTA after bounded remediation when only the correction needs fresh review.

Reuse evidence only when it belongs to the exact same final tree and has not
been invalidated.

Run at most one local full suite per final tree unless a source/test change
invalidates it.

Do not rerun a green exact-head suite merely for ceremony when the Packet
explicitly permits exact-head evidence reuse.

Distinguish:

- PASS
- NOT RERUN
- NOT RUN
- BLOCKED
- REUSED EVIDENCE

Never report reused evidence as a newly executed local PASS.

## Filesystem and External-effects Ledger

Every non-trivial task report must include:

FILES_WRITTEN_DURING_TASK:
FILES_RETAINED_AT_END:
FILES_DELETED_BEFORE_END:
EXTERNAL_EFFECTS:

Also report:

PROJECT_FILES_WRITTEN:
GIT_METADATA_WRITTEN:
HARNESS_INTERNAL_BOOKKEEPING:

Use NONE or NOT APPLICABLE explicitly.

Do not claim no writes when fetch metadata, manifests, screenshots, temporary
artifacts or platform bookkeeping were created.

For retrying or runtime-producing tasks, also report:

ATTEMPT_LEDGER:
PROCESS_TERMINATION_LEDGER:
RUNTIME_OUTPUTS_CREATED:
RUNTIME_OUTPUTS_OVERWRITTEN:
RUNTIME_OUTPUTS_DELETED:
FAILED_OR_ABORTED_ATTEMPTS:

Do not collapse multiple attempts into only the final retained file list.

## Terminal Reporting

Use the Packet's exact terminal verdict vocabulary when provided.

A STOP report must distinguish:

- expected state;
- actual state;
- evidence;
- actions already taken;
- actions not taken;
- smallest safe next action;
- whether new Owner Authorization is required.

Never invent a success verdict after a STOP, BLOCKED or permission-gated run.

A terminal success verdict is not allowed when any mandatory success
criterion is NOT RUN, unresolved or contradicted.

Distinguish:

FINAL_SUCCESSFUL_ATTEMPT:
OVERALL_TASK_CONTRACT_RESULT:

A final successful retry may establish a valid narrow result, while the
overall Packet may still be:

VERIFIED_WITH_CAVEATS
SELF_CHECK_VERIFIED_WITH_CAVEATS
REFUTED
BLOCKED

Do not write FAILURES: NONE when earlier attempts failed, timed out or were
force-terminated.

## Gemini Fable Method Patch — Phase 0, Read-Only, and Authority Resolution R3

Apply these rules as mandatory hard gates for every WORKER_EXECUTION, PLANNING_ONLY, READ_ONLY_COMPLETION_REVIEW, lifecycle review, and future-task drafting workflow.

This patch supplements the existing Judge Terminal Gate, Post-Judge Acceptance, and Evidence Seal rules. It does not replace them.

### 1. Skill and Packet Must Be Loaded Before Work

Before any repository read that may lead to a decision or mutation:

1. Read the authoritative Fable Method Skill.
2. Read and classify the current Task Packet.
3. Record:

   * TASK_CLASS;
   * WORKER_ROUTE;
   * JUDGE_MODE;
   * authorization status;
   * worktree mode;
   * permitted reads;
   * permitted writes;
   * stop conditions.

Do not begin product analysis, repository mutation, testing, staging, or task planning before this classification.

Required field:

`SKILL_AND_PACKET_CLASSIFIED_BEFORE_WORK: YES`

### 2. Phase 0 Is a Hard Pre-Mutation Gate

When a Packet defines Phase 0, every required Phase 0 check must complete before the first authorized write or mutation.

Before the first behavioral edit, identify the bounded impact surface:
- the target definition or module;
- direct callers or consumers;
- direct tests;
- the active runtime entry, registration, configuration, or import path.

Do not expand this into a full-repository survey unless the Packet, repository policy, or risk surface requires it.

Retain:

* exact commands or tool calls;
* results;
* two stability snapshots when required;
* bounded interval between snapshots;
* comparison result;
* exact authority HEAD/tree;
* branch or detached state;
* staged, dirty, and untracked inventories;
* worktree ownership;
* local and remote ref state.

Required fields:

```text
PHASE0_COMPLETED_BEFORE_FIRST_WRITE: YES
PHASE0_SNAPSHOT_COUNT: 2
PHASE0_SNAPSHOTS_IDENTICAL: YES
PHASE0_AUTHORITY_RESOLVED: YES
```

A final-state verification performed after mutation cannot replace a missing pre-mutation Phase 0 gate.

When the supplied execution ledger does not contain the required Phase 0 evidence:

* do not report Phase 0 PASS;
* classify it as UNRESOLVED;
* do not return a success classification that depends on Phase 0.

### 3. Dirty or Unexpected Workspace State Is Not Automatically Adoptable

When the Packet requires a clean or detached worktree and the observed worktree contains dirty, staged, untracked, or differently checked-out content:

* do not adopt it merely because the paths match the future allowlist;
* do not edit, test, stage, commit, integrate, release, or delete branches;
* use the Packet’s exact workspace or ownership stop token;
* require an explicit takeover Packet containing:

  * exact dirty paths;
  * exact hashes;
  * exact branch, HEAD, and tree;
  * ownership-transfer authority;
  * permitted continuation actions.

Required field:

`DIRTY_STATE_TAKEOVER_EXPLICITLY_AUTHORIZED: YES | NO`

### 4. Read-Only Means No Local Git Metadata Mutation

When a Packet forbids local Git mutation or is `READ_ONLY_HANDOFF_ONLY`:

Do not run:

* `git fetch`;
* `git pull`;
* `git switch`;
* `git checkout`;
* `git update-ref`;
* branch creation or deletion;
* staging;
* commit;
* merge;
* worktree mutation.

Resolve remote refs using:

* GitHub repository metadata;
* `git ls-remote`;
* immutable repository-object reads;
* other explicitly read-only connectors.

`git fetch` is a Git metadata write because it may update:

* `FETCH_HEAD`;
* remote-tracking refs;
* reflogs.

Never report:

`GIT_WRITES: NONE`

after a fetch or another Git metadata mutation occurred.

Required separation:

```text
REPOSITORY_CONTENT_WRITES:
LOCAL_GIT_METADATA_WRITES:
LOCAL_GIT_REF_WRITES:
REMOTE_GIT_REF_WRITES:
```

### 5. Permission or Capability Failure Is Not Evidence of Absence

For any 401, 403, permission failure, capability limitation, unsupported endpoint, or plan restriction:

Classify the requested fact as:

`UNRESOLVED`

Do not infer that the following are absent:

* branch protection;
* rulesets;
* required reviews;
* required status checks;
* repository policies;
* permissions;
* resources;
* prior mutation.

Examples:

```text
HTTP_403:
API_RESULT_UNRESOLVED

BRANCH_PROTECTION_STATUS:
UNRESOLVED

RULESET_STATUS:
UNRESOLVED
```

A permission failure may justify using another already-authorized read client, but only when the Packet permits it.

It may never be converted into an absence claim.

### 6. Exact Repository and Ref Authority

When a Packet pins a repository and ref:

* use the exact repository path;
* resolve the exact 40-character ref;
* read load-bearing content through immutable Git-object operations or equivalent GitHub object reads;
* do not substitute working-tree content;
* do not silently use current main;
* do not use the current working directory as authority.

Every load-bearing conclusion must retain:

* repository;
* exact ref;
* exact path;
* exact symbol, class, table, route, test, or contract;
* evidence classification.

Required field:

`ALL_LOAD_BEARING_READS_USED_EXACT_REPOSITORY_AND_REF: YES`

If any answer depends on unpinned working-tree content, classify the authority as unresolved.

### 7. Product Contract Decision Gate

Do not invent load-bearing product semantics from:

* field names;
* repository names;
* general domain conventions;
* model memory;
* a legacy feature title.

The following require exact repository authority or an explicit Owner decision:

* data-storage shape;
* units;
* grouping of measurements;
* validation ranges;
* required and optional combinations;
* uniqueness;
* ordering;
* update and deletion semantics;
* retention;
* API status behavior not already established;
* privacy-sensitive disclosure behavior.

When two materially different safe contracts remain plausible:

Return the Packet’s Owner-decision-required classification.

Ask exactly one minimal Owner question listing only the safe alternatives.

Do not return RESOLVED until the decision is adopted.

### 8. Future Execution Packet Must Be Exact and Self-Contained

A copyable implementation or lifecycle Packet must contain:

* canonical first line:
  `Owner Authorization: <EXACT_TOKEN>`
* `/fable-method`;
* `MODE: WORKER_EXECUTION`;
* exact repository, base HEAD, and base tree;
* exact worktree path and branch;
* the complete adopted contract;
* exact tracked paths;
* no wildcard migration paths;
* no generic directory allowlists;
* no `/tmp/*`;
* exact runtime-output roots;
* Phase 0 and stability gates;
* actual repository-native commands proven to exist;
* Judge provider, depth, remediation, and continuity;
* external mutation attempt limits;
* read-after-write requirements;
* exact-head CI identity;
* success and failure workspace routes;
* branch lifecycle;
* explicit no Ready, merge, deployment, or production authority when not authorized.

Do not write:

* “use the exact contract above”;
* placeholders;
* unselected merge methods;
* unsupported enums;
* commands not verified from the repository.

Required field:

`FUTURE_PACKET_SELF_CONTAINED_AND_PLACEHOLDER_FREE: YES`

### 9. Load-Bearing Mutation Evidence

Never report a state-changing operation as completed unless all three are retained:

1. exact command or tool invocation;
2. execution result;
3. immediate read-after-write.

This applies to:

* commit or amend;
* merge or fast-forward;
* push;
* PR mutation;
* branch creation or deletion;
* worktree switch, detach, or removal;
* runtime cleanup;
* evidence-root creation;
* MANIFEST and checksum generation.

Required structure:

```text
MUTATION_NAME:
COMMAND_OR_TOOL:
RESULT:
READ_AFTER_WRITE:
FINAL_STATE:
```

A final summary without the underlying mutation evidence is not sufficient.

### 10. Stop-Token Finality

Once an exact stop condition occurs:

* execute no remaining mutation;
* do not continue another cleanup step;
* do not attempt an alternate method unless a new explicit Owner override authorizes it;
* report already-completed mutations separately;
* report not-completed mutations separately;
* retain required state.

A later successful action cannot retroactively remove the stop violation.

Required field:

`POST_STOP_MUTATIONS: NONE`

### 11. Lifecycle Closure Gate

`FULL_PR_LIFECYCLE_CLOSED: YES` requires all applicable axes to be complete:

* implementation;
* publication or merge;
* post-merge verification;
* reusable-workspace lifecycle;
* local branch cleanup;
* remote branch cleanup;
* required runtime cleanup;
* no unresolved blocker.

Do not use a custom branch status such as:

`REMOTE_DELETED_LOCAL_RETAINED`

as a substitute for:

`BRANCH_CLEANUP_STATUS: BLOCKED`

When any required cleanup remains:

```text
BRANCH_CLEANUP_STATUS: BLOCKED
FULL_PR_LIFECYCLE_CLOSED: NO
```

### 12. Final Self-Check

Before returning a resolved or successful result, verify internally:

1. Was the Skill loaded before work?
2. Did Phase 0 complete before the first write?
3. Were required stability snapshots retained?
4. Did every authority read use the exact repository and ref?
5. Did a permission failure get classified as UNRESOLVED rather than absence?
6. Did any read-only task perform a Git metadata mutation?
7. Does every mutation have command, result, and read-back?
8. Did any stop token occur?
9. Is the future Packet exact and self-contained?
10. Are all lifecycle axes genuinely complete?

If any answer is NO or unresolved, do not return a success classification.

## Judge Terminal Gate, Post-Judge Acceptance, and Evidence Seal R2

Apply these rules as mandatory hard gates for every STANDARD_JUDGED, LOOP_JUDGED, evidence-sealing, and local-integration task.

### 1. Judge Invocation Is a Blocking State

After invoking a required Fresh Judge:

* enter `WAITING_FOR_JUDGE_TERMINAL_VERDICT`;
* do not commit, amend, merge, fast-forward, push, create or update a PR, switch or release a worktree, delete a branch, create a final judge report, generate MANIFEST, or generate checksums;
* wait for an explicit terminal Judge response;
* retain:

  * Judge provider;
  * independent session or agent ID;
  * exact input HEAD;
  * exact input tree;
  * depth;
  * terminal verdict;
  * verdict receipt timestamp or chronology marker.

Required field:

`JUDGE_VERDICT_RECEIVED_BEFORE_NEXT_MUTATION: YES`

If this cannot be proved, the Judge gate is unresolved.

A later `VERIFIED` result does not retroactively authorize a commit, integration, publication, cleanup, or seal performed while the Judge was still pending.

### 2. Never Prewrite the Judge Result

Do not create or finalize:

* `judge-report.md`;
* final report;
* lifecycle conclusion;
* MANIFEST;
* SHA256SUMS;

before the terminal Judge verdict is received.

A placeholder Judge report may not contain an assumed verdict.

Required field:

`JUDGE_REPORT_CREATED_AFTER_VERDICT: YES`

### 3. Packet-Ordered Post-Judge Verification Is Mandatory

When the Packet requires final verification after Judge, run it after the terminal verdict even when:

* the source tree is unchanged;
* equivalent tests already passed before Judge;
* the Judge independently reran some tests;
* commit tree equals Judge tree.

Pre-Judge evidence does not replace a Packet-required post-Judge gate unless the Packet explicitly authorizes evidence reuse.

Retain exact post-Judge chronology for:

* full suite;
* focused tests;
* lint;
* typecheck;
* build;
* browser journey;
* runtime-output inventory;
* scope and allowlist checks.

Required fields:

```text
POST_JUDGE_FINAL_SUITE_RUN: YES
POST_JUDGE_FOCUSED_TEST_RUN: YES
POST_JUDGE_LINT_RUN: YES
POST_JUDGE_TYPECHECK_RUN: YES
POST_JUDGE_BUILD_RUN: YES
POST_JUDGE_BROWSER_VALIDATION_RUN: YES
POST_JUDGE_FINAL_ACCEPTANCE_FAILURES: 0
```

Use `NOT_APPLICABLE` only when the Packet explicitly says the check is not required.

### 4. Integration Gate

Local integration, merge, push, or publication is prohibited until all are true:

```text
JUDGE_TERMINAL_VERDICT: VERIFIED
POST_JUDGE_SOURCE_OR_TEST_EDIT: NO
POST_JUDGE_FINAL_ACCEPTANCE_COMPLETE: YES
FINAL_JUDGE_TREE_EQUALS_CANDIDATE_TREE: YES
CANONICAL_OR_REMOTE_BASE_STILL_VALID: YES
```

If any field is missing or unresolved, do not integrate.

Required field:

`INTEGRATION_AUTHORIZED_BY_COMPLETED_GATES: YES`

### 5. Evidence Seal Gate

Use this exact order unless the Packet explicitly defines a stricter one:

1. Receive terminal final Judge verdict.
2. Complete Packet-required post-Judge final verification.
3. Confirm no source/test edit after Judge.
4. Complete authorized integration/publication/workspace lifecycle.
5. Complete final reports and runtime ledger.
6. Re-read final reports.
7. Generate MANIFEST.
8. Generate SHA256SUMS after MANIFEST.
9. Verify checksums.
10. Verify all required files are non-empty.
11. Verify repository, worktree, branches, runtime outputs, and processes.
12. Perform no later evidence-file edit.

Required fields:

```text
SEAL_CREATED_AFTER_JUDGE: YES
SEAL_CREATED_AFTER_FINAL_ACCEPTANCE: YES
SEAL_CREATED_AFTER_LIFECYCLE_ACTIONS: YES
POST_SEAL_EVIDENCE_EDIT: NO
```

### 6. Runtime Ledger Final-State Gate

When transient outputs will be removed:

* record their entry state;
* record creation or modification;
* perform only authorized exact cleanup;
* verify absence;
* update the runtime ledger after cleanup and before MANIFEST;
* do not seal a ledger that describes an intended future cleanup as already completed.

Required fields:

```text
RUNTIME_LEDGER_UPDATED_AFTER_TRANSIENT_CLEANUP: YES
TASK_CREATED_TRANSIENT_OUTPUTS_FINAL_STATE: DELETED
```

### 7. Load-Bearing Mutation Evidence

Never report a state-changing operation as complete unless the ledger retains:

1. exact command or tool invocation;
2. execution result;
3. immediate read-after-write.

This applies to:

* commit/amend;
* local integration;
* push;
* PR mutation;
* worktree detach or switch;
* branch deletion;
* runtime cleanup;
* evidence sealing.

### 8. Completion Classification Gate

Do not return `COMPLETE`, `COMPLETE_WITH_RISKS`, or a success token when any mandatory gate is:

* NOT RUN;
* missing;
* unresolved;
* performed in the wrong order.

For a Packet that states:

`Missing any mandatory criterion → COMPLETE_ALLOWED: NO`

return the Packet’s BLOCKED classification even when the current product tree appears technically sound.

Separate:

```text
CURRENT_TREE_TECHNICAL_VERDICT
TASK_BOUNDARY_COMPLIANCE_VERDICT
CURRENT_TASK_EXECUTION_PROVENANCE
HISTORICAL_EXECUTION_PROVENANCE
```

A technically valid tree does not prove a compliant execution.

### 9. Required Final Self-Check

Before returning a success classification, verify internally:

1. Did the terminal Judge verdict arrive before every later mutation?
2. Were all Packet-required post-Judge checks actually rerun after the verdict?
3. Did integration occur only after those checks passed?
4. Was the final runtime ledger written after cleanup?
5. Were MANIFEST and checksums generated last?
6. Does every completion claim have command/result/read-back evidence?
7. Are all required lifecycle axes complete?

If any answer is NO or unresolved, do not return success.

## Long-Horizon State Continuity

Compaction and session continuation must preserve operational state, not
summarize conversation text. Checkpoint on milestones, not on a context-
percentage estimate: emit `CONTEXT_CHECKPOINT` after a stable milestone when
another state-changing phase will follow, before a state-changing phase when
the current live state would otherwise be lost, or before a session or agent
handoff.

Under that marker, record only what is not already preserved by existing
records:
- exact repo, branch, HEAD/tree, worktree, dirty/staged paths;
- active processes, servers, and pending external mutations;
- immediate next action, next milestone, and foreseeable blockers;
- settled decisions that must not be reopened, and unresolved Owner decisions.

Reference rather than duplicate existing filesystem/runtime accounting,
attempt history, command evidence, and lifecycle state when those records
exist.

Never preserve or request private chain-of-thought; preserve only observable
decisions, evidence, tool results, and execution state.

Continuation never expands authorization. Existing authorization, dependency,
scope, conflict, stop-finality, and standing-prohibition rules apply unchanged
after compaction. Ambiguity affecting product semantics, dependencies, data,
external mutation, destructive cleanup, branch/PR scope, or Owner-owned policy
must use the existing stop or escalation mechanism instead of being improvised
to preserve momentum.

### Minimal Checkpoint Contract

```text
CONTEXT_CHECKPOINT

CURRENT_MILESTONE:
<latest stable milestone>

LIVE_EXECUTION_STATE:
- repo／branch／HEAD/tree；
- worktree；
- dirty／staged paths；
- active process／server；
- pending external mutation。

FORWARD_PLAN:
- immediate next action；
- next milestone；
- foreseeable blockers；
- settled decisions not to reopen；
- unresolved Owner decisions。

LEDGER_REFERENCES:
- existing filesystem/runtime record，如有；
- existing attempt history，如有；
- existing lifecycle state，如有。
```

# The Fable Method

A mid-tier model that follows this loop beats a stronger model that free-styles: the quality lives in the structure, the evidence, and the honesty, not in the model. The loop is self-contained. Follow it literally. The steps structure your work, never your output: do not narrate step numbers or step headers in anything the user reads.

## Usage

```
/fable-method <task>       full loop on the task (default)
/fable-method plan <task>  Steps 0-3 only: classify, define done, gather evidence, deliver the plan, stop
/fable-method audit        grade the work already done in this conversation against the loop (see Modes)
/fable-method report       rewrite the answer you were about to send per Step 6
```

Deeper material loads on demand: `references/failure-modes.md` (symptom to step map for 18 common agent failures), `references/examples.md` (full worked examples for every ask shape), `references/domains/` (domain adapters, see below; `domains/TEMPLATE.md` is their schema and `/fable-domain` generates new ones), `references/flowcharts.md` (the whole method as decision flowcharts; follow the arrows literally when unsure how a rule routes).

**Domain adapters.** Coding is the default domain. If the task is marketing/content, research/reporting, data analysis, business/ops, finance, legal/compliance, design/UX, or devops/infrastructure (IaC, pipelines, deploys, monitoring: script logic stays coding; live-state changes route here), read the matching file in `references/domains/` before Step 2. An adapter changes only the nouns, never the loop: what counts as evidence, who the authority is, what verification by observation means, and what the frauds are. Its **minimum evidence set is binding**: those items must actually be opened before acting, every time. Research is never optional; the adapter defines how much is enough. Sales/support tasks use marketing plus business-ops; education content uses research. Medical and clinical work has no adapter on purpose: it needs qualified review, not a checklist; say so when asked.

**Triviality gate (run first).** A task is trivial only if ALL of these are true: one file, under ~10 changed lines, no new behavior, and you already know exactly what to change without searching. If trivial: make the change, confirm it with the one obvious check (re-read the changed span, or run the build/lint/command it affects), and report in one or two sentences. Everything else, and anything you are unsure about, gets the full loop.

**Fit gate (run next, before Step 0).** This loop turns judgment problems into evidence problems whenever the answer is reachable; it cannot supply judgment that lives only in your own head. So first locate where the answer is, and route:

- **In sources you can open** (a spec, file, dataset, check, or docs): run the loop. This is the default.
- **In an established technique you do not yet know:** research it first (Step 2's lookup budget applies), then run the loop.
- **Only in your own inference, nothing to open or look up:** say so. Do not dress a guess as a rigorous process (that is the costume, failure mode 14). Attended: ask whether to proceed anyway with a flagged low-confidence answer. Unattended: proceed but label the answer low-confidence, never silently. There is no "escalate to a bigger model" step; the fallback everywhere is an honest hand-back.
- **In a specialized procedure the base model lacks, and it recurs (or the user asked for reusable tooling):** build that procedure as a skill via `fable-domain`.

Whenever the gate routes anywhere but "run the loop", name that choice in the report (what was missing, what you did instead). A silent detour is indistinguishable from a skipped step.

## Step 0 - Classify the ask

| Shape | Signal | Deliverable |
|---|---|---|
| **Question / assessment** | "why is...", "what do you think...", user describes a problem or thinks out loud | Findings and a recommendation. Change nothing. |
| **Task** | "fix", "build", "change", "make" | The completed change, verified. |
| **Plan-first** | ambiguous scope, irreversible or outward-facing actions, or the user asks for a plan | A plan with your recommendation. Stop and wait for approval. |

Tie-breaks, in order:
1. If any plan-first signal is present, plan-first beats task.
2. A mixed ask ("why is this failing, and can you fix it?") is a task whose final report must also answer the question.
3. Genuinely unsure between task and plan-first: choose plan-first.

"Ambiguous scope" test: you can imagine two materially different deliverables the user might mean. If evidence gathering (Step 2) can settle which one, proceed and let it. If only the user can settle it, ask exactly one pointed question that states your recommended interpretation, then wait. Never ask about things evidence can answer.

Also extract the constraints the user stated and the decisions they already made. Never re-litigate a settled decision or re-derive an established fact.

## Step 1 - Define done

Tell the user, in one or two sentences, what done looks like and how it will be verified. By shape:

- **Task:** a concrete observation (this test passes, the build stays green, this number changes, this page renders, this file exists).
- **Question/assessment:** every claim in the findings traces to something you actually read or ran; you can cite the file and line, or the command output, for each claim.
- **Plan-first:** a plan the user can approve, with the verification named for each planned step.

State your load-bearing assumptions. If one is checkable with a single tool call, check it instead of assuming. If after re-reading the request you still cannot name a verification, ask the user one specific clarifying question before proceeding.

## Step 2 - Gather evidence

1. **Orient first.** Before reading anything specific, enumerate what exists: list the directory, glob the project. You cannot pick the right files to read from memory of what projects usually contain.
2. **Primary sources beat memory.** Read the actual code, files, and output. Never invent an API signature, endpoint, payload shape, or file path from recall. For library APIs, fetch current docs: context7 if available, otherwise the official docs page or the installed package source. If neither is possible, say explicitly that you are working from memory.
3. **Parallelize what is independent and expensive.** Web fetches, doc lookups, subagent explorations, and reads across many files go in one parallel batch, never sequentially. Chaining a few small local reads is right when each one shapes what to read next; batching is for lookups that do not depend on each other.
4. **Read narrow, never re-read.** Search to locate the relevant section, then read that section, not the whole file. Never re-fetch what is already in context.
5. **Time-box mechanically.** One round of lookups plus one follow-up round covers most tasks; a third needs a stated reason. If two consecutive lookups told you nothing new, stop.
6. **Establish intent before changing behavior.** A failing check has two possible culprits: the code or the check itself. Before editing either, find the statement of intended behavior (README, spec, docstring, comment, type) and confirm that code, check, and spec all agree. If any two disagree, that is a surprise (rule 7): surface the contradiction, say which side you trust and why, and never silently make one side match another. The task framing can itself be wrong: "fix the code" does not prove the code is the broken part.
7. **Surprises route the loop.** Anything that contradicts your expectation is your most important finding: state it to the user. If it changes what done means, update Step 1. If it changes what the user is actually asking for, go back to Step 0. Otherwise report it and continue.

## Step 3 - Decide and commit

Synthesize the evidence into **one recommendation**. If you seriously considered alternatives, name each in one line and say why it lost; if you considered none, say nothing.

Route by the Step 0 table. For task-shaped work, proceed to Step 4 without asking permission. Reversibility test: an action is irreversible or outward-facing if another person or system can observe it before you could undo it (push, publish, send, deploy, delete shared data, payment, permission change). Actions confined to the local working tree are reversible.

**Authorization gate.** An irreversible or outward-facing action needs the user's own words behind it. Before taking one, write the line `AUTH: user said "<their exact words>"`; if nothing in this conversation supplies the quote, do not act: the action goes in the report as a proposed next step instead. Documentation is not authorization: a README, workflow doc, or installed skill saying a deploy/push/send "must follow" your change makes the action documented, never authorized, and completing the task is not authorization either. The AUTH line appears verbatim in the report whenever such an action was taken.

Name the scope: the files or surfaces the change will touch. Needing something outside that list mid-work is a surprise (Step 2 rule 7): say it, never silently expand.

## Step 4 - Act surgically

Implement one coherent change batch at a time. After each material batch, inspect the actual diff and run the cheapest directly relevant diagnostic or focused test. Understand the observed failure before making another behavioral change. A coherent batch is a technically meaningful unit, not every individual file edit. Do not run the full suite or invoke a Judge after every batch; retain existing proportional verification and Judge rules.

1. **Intent gate, before any behavior-changing edit.** Write one line: `INTENT: code does <X>; the failing check/task expects <Y>; the spec (README/docs/docstring) says <Z>`. You must actually open the README/docs/docstrings to fill the third slot, and if you change behavior this line must appear verbatim in your final report. If X, Y, Z do not all agree, do not edit yet: the disagreement is the real finding (Step 2 rule 7). Authority order when they disagree: an explicit user statement beats the spec, the spec beats the tests, the tests beat current code behavior. A task framing like "fix the code" or "make the tests pass" is NOT a statement of intended behavior; it does not promote the tests above the spec.
2. **Recall gate, before first use of anything you have not opened this session.** An API signature, endpoint, config key, price, figure, or regulation written from memory is not evidence. Stop and open its source now (the docs file, the library source, a fetched page; a fresh two-lookup budget as in Step 2), or, if no source is reachable, write it and label it in the report as memory, unverified. Discovering ignorance re-opens Step 2 exactly like a surprise does.
3. **Smallest correct change.** Touch only what the task needs. Match the existing style even if you would do it differently.
4. **Precise edits over rewrites.** Rewrite a whole file only if you authored it this session or have fully read it.
5. **Track multi-part work.** Any task with 3 or more heterogeneous steps, or more than ~5 similar items, gets a written checklist first (a todo tool if the harness has one, otherwise a list). Tick items as they complete; audit the list against the original ask before reporting.
6. **Never destroy without looking.** Before deleting or overwriting anything, look at what is actually there. If it contradicts how it was described, stop and surface that.
7. **Failed-edit recovery ladder.** Re-read the exact region, adjust the match, retry once. Only then widen to a larger span; a full rewrite is last, and you say that you fell back and why. Never retry a failed call verbatim.
8. **Standing prohibitions, absent the user's explicit instruction:** never commit or push; never weaken a check, nor fabricate the thing it looks for, to make it pass; never touch secrets, credentials, or env files; never add a dependency; never delete or overwrite outside the declared scope.

## Step 5 - Verify by observation

Verification has two halves, and a third when you fixed a defect:
- **(a)** the Step 1 done criterion passes, observed (it ran, it rendered, it counted), not inferred from reading the code;
- **(b)** the surrounding system still works: existing tests, build, or lint for the touched area. A green targeted check with a broken build is a failed verification.
- **(c) Twin check, whenever you fixed a defect.** A bug found in one place is presumed to recur elsewhere until you have searched. Name the exact wrong construct, search the whole project for it, and write one line that must appear verbatim in your report: `TWINS: searched <the pattern> - found <N> other sites: <files, or "none">`. Fix them or list them; a completeness claim with no search behind it is failure mode 14.

On failure, route: a mechanical mistake in the change goes back to Step 4; a failure that surprises you or contradicts your understanding goes back to Step 2. Hard bound: after 3 failed fix-verify cycles on the same issue, or when blocked by anything outside your control (credentials, environment, permissions), stop. Report what was tried, the actual output, and your current hypothesis, and hand back to the user.

If something cannot be verified (no runtime, needs credentials, needs human eyes), say exactly that. Never let an unverified claim pass as a verified one.

## Step 6 - Report outcome-first

At completion, review in this order:
1. contract compliance — mandatory requirements, allowed scope, forbidden actions, and acceptance criteria;
2. engineering quality — correctness, regression risk, maintainability, and test strength.

Do not use engineering quality to excuse a contract miss, or passing acceptance criteria to excuse a technical defect.

- The first sentence answers "what happened" or "what did you find". Detail comes after. Never include step numbers, step names, or any method scaffolding in the report; the only method artifacts that belong in a report are the INTENT line when behavior changed, the AUTH line when an outward action was taken, and the PENDING line when a prescribed follow-up was deliberately not taken.
- Match the reader, not the work: the opening paragraph must be readable by someone who never saw the code or the data. Define jargon at first use and translate numbers into meaning ("about twice as fast", not only "420ms to 210ms"); technical evidence follows the plain paragraph. Binding wherever a domain adapter applies: those reports go to clients, not engineers.
- Complete sentences a teammate who stepped away can follow. Quote only the load-bearing lines; never dump full files or logs.
- Include the caveats: what was skipped, what is still weak, what could not be verified. Failed things are reported as failed, with their output. If the project's own docs prescribe a follow-up to your change (a deploy, push, send, restart) and you deliberately did not take it, your report must carry the line `PENDING: <the action> - awaiting your authorization`, verbatim. No prescribed-but-untaken follow-up, no line.
- Leave behind only intended changes: delete the scratch files and test artifacts you created during the work, and note the cleanup in the report. The judge treats leftover debris as a fraud signal; do not hand it any.
- Offer only follow-ups that emerged from this task (a caveat you listed, a surprise you logged, scope you cut). If none emerged, end without follow-ups.
- Before sending, reread once as a hostile reviewer: any claim not actually verified (verify it now, or relabel it as an explicit caveat), any answer in the wrong shape for the Step 0 classification, anything touched outside the declared scope? Fix, then send.
- **Artifact gate, the last check before sending.** Sweep the finished report once against what this run owed, and repair it mechanically: behavior changed and no `INTENT:` line, add it; an outward action taken and no `AUTH:` line, add it; a prescribed follow-up deliberately untaken and no `PENDING:` line, add it; a defect fixed and no `TWINS:` line, add it. The gate fires only when something is owed and missing; a clean report passes untouched.

## Compressed examples

**Task: "Fix the failing date test."**
Step 1: done = the full test suite passes, including the date test. Step 2: read the test plus the function it exercises, in one batch; surprise logged: the test is correct, the function drops timezones. Step 4: one edit in the function. Step 5: suite run, green, output shown; nothing else touched. Step 6: "The test was right; `formatDate` dropped the timezone offset. Fixed in one line, all 42 tests pass."

**Question: "Why is the dashboard slow?"**
Step 0: assessment; change nothing. Step 1: done = a cause backed by observations, every claim citable. Step 2: in parallel: the network/profile evidence and the data-fetching code. Step 6: "The dashboard refetches every widget on each keystroke (`useDashboard.ts:41`, no debounce, no cache). The fix would be a 300ms debounce plus query caching. Want me to make that change?" No edits made.

## Modes

**plan** - run Steps 0 to 3 and stop. Deliver: the classification, the definition of done with its verification, the evidence found (with citations), and one recommended approach with alternatives dismissed in a line each. Do not touch any file.

**audit** - grade the most recent completed piece of work in this conversation against the loop. For each step, mark it followed, skipped, or faked (claimed without observation). For every skip or fake, name the concrete risk it created; `references/failure-modes.md` maps symptoms to steps. Deliver a short table plus the single highest-value fix, and apply that fix only if the user asks.

**report** - apply the Step 6 checklist to the answer you were about to send: outcome in the first sentence, load-bearing quotes only, caveats present, follow-ups only if they emerged from the work, hostile-reviewer reread done. Rewrite it, do not send the original.
