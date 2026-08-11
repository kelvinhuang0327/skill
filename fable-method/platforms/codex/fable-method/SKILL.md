---
name: fable-method
description: Primary Fable entry for task classification and state-changing implementation. Before external tools, classifies implementation, read-only completion review, planning-only, or pure Q&A; routes reviews to fable-judge; then detects authoritative planner packets, checks contract conflicts, performs bounded preflight, chooses FAST, STANDARD, STANDARD_JUDGED, or LOOP_JUDGED, tracks filesystem writes, and closes claims with evidence. Use when the user says "/fable-method", "use the fable method", or "approach this like Fable", or for a non-trivial implementation task with no more specific skill. Do not auto-trigger the implementation lifecycle for pure questions or planning-only requests. Legacy plan, audit, and report subcommands remain supported.
---

# The Fable Method

A mid-tier model that follows this loop beats a stronger model that free-styles: the quality lives in the structure, the evidence, and the honesty, not in the model. The loop is self-contained. Follow it literally. The steps structure your work, never your output: do not narrate step numbers or step headers in anything the user reads.

This is the single top-level Worker entry for the Fable skill family. It guides model behavior; it does not mechanically enforce these rules. When this Worker contract conflicts with the generic steps later in this file, the Worker contract wins.

## Context Capacity Resolution and Safe Compaction

Before using any token count, context percentage, or long-context cost threshold, resolve and report these fields independently:

```text
ACTIVE_MODEL:
<exact model ID or UNKNOWN>

ACTIVE_MODEL_SOURCE:
RUNTIME_STATUS |
EXPLICIT_SESSION_CONFIG |
CONFIG_ONLY |
USER_PROVIDED |
UNKNOWN

EFFECTIVE_CONTEXT_WINDOW:
<token count or UNKNOWN>

CONTEXT_WINDOW_SOURCE:
RUNTIME_METADATA |
OFFICIAL_MODEL_METADATA |
CONFIG_OVERRIDE |
UNKNOWN

CURRENT_CONTEXT_TOKENS:
<number or UNKNOWN>

CURRENT_CONTEXT_PERCENT:
<number or UNKNOWN>

CURRENT_CONTEXT_USAGE_SOURCE:
RUNTIME_METADATA |
HARNESS_METER |
PLATFORM_WARNING |
HEURISTIC |
UNKNOWN

LONG_CONTEXT_BILLING_THRESHOLD:
<number | NOT_APPLICABLE | UNKNOWN>

LONG_CONTEXT_THRESHOLD_SOURCE:
RUNTIME_METADATA |
CURRENT_OFFICIAL_POLICY |
USER_PROVIDED |
UNKNOWN

CONTEXT_CAPABILITY_RESOLUTION:
EXACT | DERIVED | UNKNOWN
```

Apply these resolution rules:

- Do not infer the active model merely because the environment is called Codex, Claude, Fable, or another product name.
- `CONFIG_ONLY` is provisional unless the current session confirms that model.
- Do not infer current context usage from the maximum context window.
- Do not infer a billing threshold or multiplier from context-window size.
- Model context capacity and long-context billing policy are separate facts.
- Only make a cost-multiplier claim when the active model, applicable product or plan, and current billing threshold are all resolved from current authoritative information.
- When `LONG_CONTEXT_BILLING_THRESHOLD` is `UNKNOWN`, report:

```text
COST_MULTIPLIER_CLAIM:
NOT ALLOWED
```

### Context pressure policy

Use exact percentage thresholds only when `CURRENT_CONTEXT_USAGE_SOURCE` is `RUNTIME_METADATA` or `HARNESS_METER`.

At 50%:

- Start a context-pressure assessment.
- Do not compact solely because the threshold was reached.

Between 65% and 75%:

- Prefer creating a checkpoint and compacting at a stable task boundary.
- Do not interrupt an unresolved external mutation or an active final-tree verification merely to compact.

Before 80%:

- Before beginning another large analysis, implementation phase, Judge run, or state-changing lifecycle action, create a complete handoff checkpoint.

When current usage is unavailable, set `CONTEXT_CAPABILITY_RESOLUTION` to `DERIVED` or `UNKNOWN` and assess checkpoint need from these heuristic signals:

- the platform or harness emits a context or compaction warning;
- the transcript has accumulated substantial tool output, logs, or pasted reports;
- the task has undergone multiple retries, Judge runs, scope expansions, or continuations;
- the conversation begins to confuse the project, repository, path, commit, task ID, or stop token;
- the next action begins a new large implementation phase;
- the next action changes Agent, model, or conversation;
- the next action is expected to produce substantial output;
- a stable milestone has completed and is suitable for a checkpoint.

Heuristics never establish an exact context percentage. Report instead:

```text
CURRENT_CONTEXT_PERCENT:
UNKNOWN

CURRENT_CONTEXT_USAGE_SOURCE:
HEURISTIC

CONTEXT_PRESSURE_LEVEL:
LOW | MODERATE | HIGH | CRITICAL
```

## Long-Horizon State Continuity

Compaction and session continuation must preserve operational state, not
summarize conversation text. Checkpoint on milestones, not on a new
context-percentage estimate: emit `CONTEXT_CHECKPOINT` after a stable milestone
when another state-changing phase will follow, before a state-changing phase
when the current live state would otherwise be lost, or before a session or
agent handoff.

Under that marker, record only what is not already preserved by existing
ledgers:
- exact repo, branch, HEAD/tree, worktree, dirty/staged paths;
- active processes, servers, and pending external mutations;
- immediate next action, next milestone, and foreseeable blockers;
- settled decisions that must not be reopened, and unresolved Owner decisions.

Reference rather than duplicate the existing filesystem/runtime accounting,
attempt history, command evidence, and lifecycle counts.

Never preserve or request private chain-of-thought; preserve only observable
decisions, evidence, tool results, and execution state.

Continuation never expands authorization. Existing authorization, dependency,
scope, contract-conflict, stop-finality, and standing-prohibition rules apply
unchanged after compaction. Ambiguity affecting product semantics,
dependencies, data, external mutation, destructive cleanup, branch/PR scope,
or Owner-owned policy must stop through the existing mechanism instead of
being improvised to preserve momentum.

### Safe checkpoint boundary

Do not compact in the middle of any of these states unless the checkpoint records the unresolved state exactly:

- an external mutation result is `UNKNOWN`;
- read-after-write verification is incomplete;
- dirty ownership is unresolved;
- source or test edits are complete but final-tree verification is incomplete;
- a Judge is running or its verdict has not been received;
- modified files, runtime outputs, or failed attempts have not been recorded;
- task scope conflicts with Owner authorization.

Compaction is not a success classification and never clears a blocker.

### Required handoff checkpoint

Before compaction, create this self-contained checkpoint in the transcript:

```text
CONTEXT_HANDOFF_CHECKPOINT

PROJECT:
CURRENT_TASK_ID:
CURRENT_GOAL:

AUTHORITY:
- Owner authorization:
- Authorized actions:
- Forbidden actions:
- Handoff authority:
- AI context authority:
- Dependency authorization:

REPOSITORY_STATE:
- Canonical repository:
- Branch:
- HEAD:
- Tree:
- Worktree:
- Staged paths:
- Dirty paths:
- Modified paths:
- PR:
- CI:

ACTUAL_WORK_COMPLETED:
- Exact completed work:
- Commits:
- Push/PR/merge actions:
- External mutations:
- Files created/modified/deleted:

VERIFICATION:
- Tests actually run:
- Results:
- Tests NOT RUN:
- Reused evidence:
- Judge mode:
- Judge provider:
- Judge input HEAD/tree:
- Judge verdict:
- Final-tree evidence validity:

ATTEMPT_LEDGER:
- Failed attempts:
- Aborted attempts:
- Ambiguous mutations:
- Read-after-write results:
- Process terminations:
- Runtime outputs created:
- Runtime outputs modified:
- Runtime outputs deleted:

LIFECYCLE:
- Implementation:
- PR publication:
- Post-merge:
- Branch cleanup:
- Full lifecycle closed:

CURRENT_BLOCKER:
NEXT_SINGLE_ACTION:
STOP_CONDITIONS:
UNRESOLVED_UNKNOWNS:
```

The checkpoint must:

- be self-contained and never use “same as above,” “same as previous,” or equivalent references;
- preserve exact repository, path, SHA, branch, PR, and stop-token identities;
- distinguish completed, `NOT RUN`, `BLOCKED`, and `UNKNOWN` states;
- preserve earlier failed, timed-out, and aborted attempts;
- never extend prior authorization automatically to a new task;
- exclude private chain-of-thought and record only transferable decisions, state, and evidence.

Use this default:

```text
HANDOFF_STORAGE_MODE:
TRANSCRIPT_ONLY
```

In transcript-only mode, do not create a handoff Markdown file, JSON snapshot, log, scratch script, or temporary file. This storage boundary applies equally to the Worker, Judge, and every subagent.

Only a Task Packet that explicitly provides both fields below authorizes file storage:

```text
HANDOFF_STORAGE_MODE:
ALLOWLISTED_FILE

HANDOFF_OUTPUT_PATH:
<exact authorized path>
```

Deleting an unauthorized handoff file does not make the earlier write compliant.

### Rehydration check

After compaction, session resume, or checkpoint-based continuation, report this check before any state-changing action:

```text
CONTEXT_REHYDRATION_STATUS:
PASS | INCOMPLETE

PROJECT_IDENTITY_RESOLVED:
YES | NO

TASK_IDENTITY_RESOLVED:
YES | NO

AUTHORITY_RESOLVED:
YES | NO

REPOSITORY_STATE_RESOLVED:
YES | NO

SANDBOX_STATE_RESOLVED:
YES | NO

MODIFIED_PATH_LEDGER_RESOLVED:
YES | NO

OBSERVABLE_HISTORY_RESOLVED:
YES | NO

CURRENT_MILESTONE_RESOLVED:
YES | NO

CURRENT_BLOCKER_RESOLVED:
YES | NO

NEXT_SINGLE_ACTION_RESOLVED:
YES | NO

NEXT_MILESTONE_RESOLVED:
YES | NO

STOP_CONDITIONS_RESOLVED:
YES | NO
```

Continue mutation only when every load-bearing field is `YES`. Otherwise report:

```text
CONTEXT_HANDOFF_INCOMPLETE
```

When the handoff is incomplete:

- do not guess missing content;
- do not substitute the current working directory for authority;
- do not repeat a completed mutation;
- do not delete or overwrite content with unknown ownership;
- perform only handoff completion or read-only state resolution.

Compaction does not repair, conceal, or authorize an earlier unauthorized write, failed attempt, unresolved mutation, zero-write or allowlist violation, or runtime-output violation. It does not create dependency authorization or extend commit, push, PR, merge, cleanup, or other mutation authority. Stop tokens remain in force after compaction. Judge continuity cannot be inferred from a summary, and current-tree verdict remains separate from historical execution provenance.

## Usage

```
/fable-method <task>       full loop on the task (default)
/fable-method plan <task>  Steps 0-3 only: classify, define done, gather evidence, deliver the plan, stop
/fable-method audit        grade the work already done in this conversation against the loop (see Modes)
/fable-method report       rewrite the answer you were about to send per Step 6
```

Pure questions and planning-only requests do not auto-activate the Worker workflow. If the user explicitly invokes a legacy subcommand, run that subcommand without pretending it is an implementation route.

## Worker entry contract

### Classify the task before any external tool

Classify from the user-visible request before the first external tool call. Use exactly one Task Class:

- **STATE_CHANGING_IMPLEMENTATION**: the task may modify in-scope state. Only this class may enter FAST, STANDARD, STANDARD_JUDGED, or LOOP_JUDGED.
- **READ_ONLY_COMPLETION_REVIEW**: the task reviews claimed-complete work without modifying it. Use sibling `../fable-judge/SKILL.md`; never label a read-only PR or completion review FAST, STANDARD, STANDARD_JUDGED, or LOOP_JUDGED.
- **PLANNING_ONLY**: produce only the requested plan; do not enter the Worker implementation lifecycle.
- **PURE_QA**: answer without entering the Worker implementation lifecycle.

Make these three lines the Skill's first substantive output, before any external tool call:

```text
TASK_CLASS: <class>
WORKER_ROUTE: <route or NOT_APPLICABLE>
JUDGE_MODE: <mode or NOT_APPLICABLE>
```

For READ_ONLY_COMPLETION_REVIEW, output `WORKER_ROUTE: NOT_APPLICABLE` and `JUDGE_MODE: FRESH_CONTEXT` when a fresh verifier is available; otherwise output `JUDGE_MODE: SELF_CHECK_ONLY`. For PLANNING_ONLY and PURE_QA, output `WORKER_ROUTE: NOT_APPLICABLE` and `JUDGE_MODE: NOT_APPLICABLE`. For STATE_CHANGING_IMPLEMENTATION, choose the route from the visible request and Planner Packet; use `JUDGE_MODE: FRESH_CONTEXT` for judged routes when available, `SELF_CHECK_ONLY` when not, and `NOT_APPLICABLE` for unjudged routes.

If later external evidence disproves the initial class, output this block before continuing, then recompute the route and Judge mode. Never reclassify silently.

```text
TASK_CLASS_RECLASSIFIED
FROM:
TO:
EVIDENCE:
IMPACT_ON_ROUTE:
```

### Detect the Planner Packet

Classify supplied task specifications before running the generic planning steps:

- **AUTHORITATIVE_PACKET_PRESENT**: includes Goal, allowed scope, acceptance criteria, and constraints or forbidden actions. Treat Goal, allowed scope, forbidden actions, acceptance target, and deliverable format as authoritative subject to the contract conflict gate below. Verify repository assumptions, but do not rebuild the full plan, reclassify the whole product, redo architecture decisions, ask questions it already answered, create a second plan artifact, or create roadmap, SPEC, or PROGRESS files.
- **AUTHORITATIVE_PACKET_PARTIAL**: includes Goal and Scope, but acceptance is incomplete. Infer only the smallest machine-checkable acceptance from existing test commands, build commands, runtime behavior, or established test patterns. Mark every derived criterion `[Inferred]`. If credible acceptance cannot be derived, stop with `BLOCKED_MISSING_VERIFIABLE_ACCEPTANCE`.
- **AUTHORITATIVE_PACKET_ABSENT**: only this state may run the full generic Steps 0-3. Produce a minimal execution contract, not a long governance document.

For PRESENT or PARTIAL, do not run a second complete planning pass. Extract the packet's Goal, Scope, criteria, constraints, risks, and deliverable format, then proceed to bounded preflight.

### Check Planner Packet contract conflicts

Do not let a Planner Packet silently override an existing domain invariant, canonical schema contract, repository-wide terminology contract, data or safety invariant, or actual repository state. When a Packet claim conflicts with repository evidence, output:

```text
PLANNER_PACKET_CONTRACT_CONFLICT

PACKET_CLAIM:
REPO_EVIDENCE:
IMPACT:
IS_EXPLICIT_OVERRIDE: YES|NO
REQUIRED_DECISION:
```

If no explicit Owner-approved override exists, choose neither side silently, do not claim fully VERIFIED, and request a Planner or Owner decision. If the Packet explicitly identifies an intentional new contract or Owner-approved override, follow the new contract, preserve the evidence of that authorization, and disclose the difference from the old contract in the final report.

### Run bounded preflight

Before mutation, confirm only what can invalidate execution:

- canonical repository, branch, HEAD, and worktree status;
- applicable `AGENTS.md` and `AGENTS.override.md` files;
- packet-named files and paths exist;
- actual import, route, deployment, and runtime chain that reaches the target;
- contradictions between the packet and repository reality.

Before the first behavioral edit, identify the bounded impact surface: target
definition/module; direct callers/consumers; direct tests; and active runtime
entry, registration, configuration, or import path. Use `NONE` when absent; do
not expand to repository-wide survey unless the Packet, policy, or risk
requires it.

If a contradiction changes scope, architecture, or acceptance, surface it and stop or request the missing authority. Otherwise output one normal routing line and continue:

```text
WORKER_ROUTE: <route> — <one concrete reason>
```

### Choose exactly one route

- **FAST**: local, low-risk change; target is known; direct acceptance exists. Run `preflight → edit → acceptance → compact report`.
- **STANDARD**: default. Use for tightly coupled work, one runtime chain, shared mutable state, a root cause needing continuous context, or unclear parallel benefit.
- **STANDARD_JUDGED**: use when independent verification is required but safe parallel execution is not.
- **LOOP_JUDGED**: use only when every Loop capability and eligibility gate below is YES. Loop work never bypasses Judge.

Do not route to Loop merely because a task is hard, risky, spans many files, requests high quality, or has slow tests.

### Apply the Judge gate

Require a Judge when any one is true:

- authentication, authorization, security, payment, finance, or irreversible operations;
- database schema, migration, or production-data writes;
- shared core modules or changes spanning runtime layers;
- real UI, browser, device, or external-side-effect validation;
- Loop was used;
- acceptance failed or the Worker performed a repair retry;
- the Planner asks for independent verification;
- evidence is incomplete or a material `[Unknown]` remains.

Multiple files alone do not activate Judge. If the gate is true and Loop is ineligible, select STANDARD_JUDGED.

### Select Judge depth before handoff

Every Judge invocation must use exactly one depth: `BOUNDED`, `FULL`, or `DELTA`. Before calling the Judge, output:

```text
JUDGE_DEPTH: <BOUNDED|FULL|DELTA>
JUDGE_DEPTH_REASON: <one concrete sentence>
```

Use `BOUNDED` as the default initial depth for `STANDARD_JUDGED`. It requires focused independent reproduction plus repository identity, actual-diff, scope-creep, test-weakening, primary-invariant/runtime-evidence, filesystem-ledger, and verdict-consistency checks. A Planner's full-suite command alone does not make the Judge rerun that suite when valid same-final-tree evidence exists.

Use `FULL` only when at least one of these triggers applies, and name the exact trigger in `JUDGE_DEPTH_REASON` and the final report:

- security, authentication, or authorization;
- a database migration or production-data write;
- payment or an irreversible external side effect;
- deployment or cutover;
- a shared-core change with broad risk that cannot be verified locally;
- the Worker did not run the complete suite on the final tree;
- the Worker's full-suite evidence lacks the complete command, exit status/output summary, environment, or final-tree identity;
- evidence is contradictory, suspected false, or irreproducible;
- the Planner or Owner explicitly requires full independent reproduction.

Use `DELTA` only for a Re-Judge after remediation. Verify the original finding, remediation diff, finding-specific tests, impacted regression slice, scope and ledger, and whether the original verdict can change. Upgrade `DELTA` to `FULL` only when the remediation changes shared core, or prior full-suite evidence remains invalid at handoff because the Worker could not reestablish it on the remediated final tree; state why. Judge depth changes verification breadth, never verdict or evidence-on-close standards.

### Bound Judge remediation and lifecycle closure

Use this fixed flow:

```text
Initial Judge: BOUNDED or FULL
→ at most one bounded remediation
→ Re-Judge: DELTA by default
```

If remediation changes load-bearing code or tests, invalidate the earlier full-suite evidence and have the Worker run the complete suite once on the remediated final tree before the `DELTA` handoff. This owner rerun is not a Re-Judge full-suite rerun and does not by itself upgrade `DELTA` to `FULL`.

If the same finding fails the Re-Judge, output `BLOCKED_AFTER_JUDGE_REFUTATION`; do not enter another Judge/fix cycle. A prior `STANDARD_JUDGED` route never makes the Re-Judge `FULL` by itself.

After authorized commit, push, and exact-head CI, output `LIFECYCLE_CLOSURE` and check only the pushed commit, PR head, exact-head CI, mergeability, branch, clean/restored worktree, unchanged primary checkout, and external effects. Do not run another implementation Judge unless the PR head changed, CI contradicts local evidence, code changed after commit, or a new load-bearing finding appeared.

### Lifecycle Reporting Integrity Gate

Terminal reporting must separate these statuses:

```text
IMPLEMENTATION_LIFECYCLE_STATUS:
NOT_STARTED | IN_PROGRESS | COMPLETE | BLOCKED | NOT_APPLICABLE

PR_PUBLICATION_STATUS:
NOT_APPLICABLE | NOT_CREATED | DRAFT_OPEN | READY_OPEN | MERGED | BLOCKED

POSTMERGE_LIFECYCLE_STATUS:
NOT_APPLICABLE | NOT_STARTED | IN_PROGRESS | COMPLETE | BLOCKED

BRANCH_CLEANUP_STATUS:
NOT_APPLICABLE | RETAINED_WHILE_PR_OPEN | DELETED | ALREADY_ABSENT | BLOCKED
```

Use `FULL_PR_LIFECYCLE_CLOSED: YES` only when the PR is merged, the actual merge commit is verified, the fixed head is contained in the target branch, required post-merge CI passed, any reusable-worktree lifecycle completed, local and remote branches were safely deleted or were already absent, and no cleanup blocker remains. Otherwise use `FULL_PR_LIFECYCLE_CLOSED: NO`.

A Draft or Ready unmerged PR always requires:

```text
FULL_PR_LIFECYCLE_CLOSED: NO
```

Even when implementation, tests, Judge, publication, exact-head CI, and reusable-worktree release are complete, a Draft PR with retained branches reports:

```text
IMPLEMENTATION_LIFECYCLE_STATUS: COMPLETE
PR_PUBLICATION_STATUS: DRAFT_OPEN
POSTMERGE_LIFECYCLE_STATUS: NOT_STARTED
BRANCH_CLEANUP_STATUS: RETAINED_WHILE_PR_OPEN
FULL_PR_LIFECYCLE_CLOSED: NO
BLOCKED: NONE
```

For a Ready PR whose authorized merge cannot complete because of an unresolved external lock, report:

```text
IMPLEMENTATION_LIFECYCLE_STATUS: COMPLETE
PR_PUBLICATION_STATUS: READY_OPEN
POSTMERGE_LIFECYCLE_STATUS: BLOCKED
BRANCH_CLEANUP_STATUS: RETAINED_WHILE_PR_OPEN
FULL_PR_LIFECYCLE_CLOSED: NO
BLOCKED:
- merge operation unresolved
```

When a PR is merged but required post-merge CI or cleanup cannot complete, report `PR_PUBLICATION_STATUS: MERGED`, `POSTMERGE_LIFECYCLE_STATUS: BLOCKED`, and `FULL_PR_LIFECYCLE_CLOSED: NO`.

Keep non-execution separate from blockage:

```text
NOT RUN:
Actions intentionally not executed because they were not authorized, out of scope, not applicable, or deferred to a later lifecycle task.

BLOCKED:
Actions authorized or required for the current task that could not complete because a gate, failure, unavailable dependency, external lock, permission issue, or unresolved authority prevented them.
```

Never report `NOT RUN / BLOCKED: None` when an action was intentionally not executed. List each category separately; for example, an unauthorized merge is `NOT RUN: Merge — not authorized` with `BLOCKED: None`, never a blocked merge.

While a PR is Draft or Ready and branches are retained, do not say `lifecycle-closed`, `fully closed`, `merge lifecycle complete`, `branches cleaned`, or `publication complete` when that wording could imply a merge. Prefer: `Implementation, verification, Draft PR publication, exact-head CI, and reusable-worktree release completed. PR merge lifecycle remains intentionally open.`

### Terminal Reporting Precision Gates

Apply these gates mechanically whenever their fields are applicable. Keep closed fields scalar: do not encode a transition such as `A → B` in one field, and use `NONE`, `NOT_APPLICABLE`, or an allowed enum instead of silently omitting an axis.

#### `VALID_OWNER_AUTHORIZATION_LINE_GATE`

Treat a user-visible first line matching `Owner Authorization: <EXACT_TOKEN>` as valid when the Packet also says `OWNER_AUTHORIZATION_STATUS: PRESENT` and `SECOND_CONFIRMATION_REQUIRED: NO`. The prefix, cross-session takeover, and missing prior conversational memory do not weaken that authority or require a second confirmation. Report:

```text
AUTHORIZATION_FIRST_LINE_VALID: YES | NO | NOT_APPLICABLE
OWNER_AUTHORIZATION_STATUS: PRESENT | ABSENT | NOT_APPLICABLE
SECOND_CONFIRMATION_REQUESTED: YES | NO | NOT_APPLICABLE
AUTHORIZATION_CAVEAT: NONE | DETAILS
```

#### `WORKTREE_REPORTING_SEPARATION_GATE`

Report mode, entry route, release action, switch action, and final state separately:

```text
WORKTREE_MODE:
NOT_APPLICABLE | REUSABLE_AGENT_WORKTREE | EPHEMERAL_TASK_WORKTREE | EXISTING_TASK_WORKTREE

WORKTREE_STATE_ROUTE_AT_ENTRY:
ACTIVE_EXACT_PR_HEAD | ACTIVE_BEHIND_REMOTE_PR_HEAD | ACTIVE_STABLE_TASK_OWNED_DIRTY |
DIRTY_OWNERSHIP_UNRESOLVED | SAFE_FAST_FORWARD_BLOCKED_BY_DIRTY_DUPLICATE |
ALREADY_RELEASED_CLEAN_BASELINE | EXISTING_PATH_ABSENT | UNKNOWN_UNSAFE_STATE | NOT_APPLICABLE

REUSABLE_WORKSPACE_RELEASE_ACTION:
NOT_APPLICABLE | ALREADY_COMPLETE | PERFORMED | PERFORMED_POSTMERGE_BASELINE_REFRESH | BLOCKED

REUSABLE_WORKSPACE_SWITCH_PERFORMED:
YES | NO | NOT_APPLICABLE

REUSABLE_FINAL_STATE:
DETACHED_AT_ORIGIN_MAIN_CLEAN | DETACHED_AT_LOCAL_MAIN_CLEAN | ACTIVE_TASK_BRANCH_CLEAN |
ACTIVE_TASK_OWNED_DIRTY | ABSENT | NOT_APPLICABLE | DETAILS
```

Never put a worktree mode in the entry-route field. A performed switch requires `REUSABLE_WORKSPACE_SWITCH_PERFORMED: YES` and cannot use `ALREADY_COMPLETE`; a post-merge refresh requires `PERFORMED_POSTMERGE_BASELINE_REFRESH`.

#### `JUDGE_EVIDENCE_INVALIDATION_GATE`

Preserve the complete Initial-to-DELTA chronology:

```text
INITIAL_JUDGE_INPUT_HEAD:
INITIAL_JUDGE_INPUT_TREE:
INITIAL_JUDGE_VERDICT:
POST_INITIAL_JUDGE_SOURCE_OR_TEST_EDIT: YES | NO
INITIAL_JUDGE_EVIDENCE_VALID_FOR_FINAL_TREE: YES | NO
DELTA_REJUDGE_REQUIRED: YES | NO | NOT_APPLICABLE
DELTA_REJUDGE_INPUT_HEAD:
DELTA_REJUDGE_INPUT_TREE:
DELTA_REJUDGE_VERDICT:
POST_DELTA_JUDGE_SOURCE_OR_TEST_EDIT: YES | NO | NOT_APPLICABLE
DELTA_JUDGE_EVIDENCE_VALID_FOR_FINAL_TREE: YES | NO | NOT_APPLICABLE
FINAL_JUDGE_INPUT_HEAD:
FINAL_JUDGE_INPUT_TREE:
FINAL_JUDGE_VERDICT:
FINAL_TREE_JUDGE_CONTINUITY: PASS | FAIL | NOT_APPLICABLE
```

Any source or test edit after the Initial Judge makes its verdict invalid for the final tree. Only a DELTA Judge followed by no source or test edit may validate that remediated final tree. Require final HEAD/tree to equal the final Judge input HEAD/tree. Never reuse an Initial `REFUTED` verdict as final validation; list only unaffected sections under `UNAFFECTED_EVIDENCE_REUSED_BY_DELTA`.

#### `JUDGE_RUN_COUNTER_INTEGRITY_GATE`

When `JUDGE_MODE: NOT_APPLICABLE`, require:

```text
INITIAL_JUDGE_RUNS: 0
DELTA_REJUDGE_RUNS: 0
FULL_JUDGE_RUNS: 0
NEW_JUDGE_RUNS: NONE
```

A Worker completion verdict is not a Judge run. Otherwise count actual Initial, DELTA, and FULL invocations only.

#### `WRITE_CLASSIFICATION_GATE`

Classify writes by cause rather than by final pathname:

```text
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
PRODUCT_FILES_EDITED_DURING_TASK:
```

`GIT_METADATA_WRITES` may be an umbrella heading only; keep network metadata, worktree metadata, and harness metadata separate beneath it. Never classify source/test edits as runtime output, checkout materialization as a Worker product edit, or refs/reflogs as test cache.

#### `RETAINED_FILE_LEDGER_GATE`

Partition retained state by task origin:

```text
TASK_CREATED_FILES_RETAINED:
TASK_CREATED_FILES_DELETED:
PRE_EXISTING_FILES_RETAINED_UNCHANGED:
PRE_EXISTING_FILES_MODIFIED_AND_RATIFIED:
FILES_MODIFIED_DURING_TASK:
```

If any pre-existing file is retained unchanged, an aggregate `FILES_RETAINED_AT_END` cannot be `NONE`. `TASK_CREATED_FILES_RETAINED: NONE` is valid when only pre-existing files remain.

#### `RUNTIME_SIDE_EFFECT_PREFLIGHT_GATE`

Before any mandatory command under a strict runtime allowlist, inspect package scripts and relevant TypeScript `incremental`, `composite`, and `tsBuildInfoFile` settings; Vitest, Vite, Jest, pytest, Ruff, UV, and npm caches/logs; browser profiles; and generated/build outputs. Report:

```text
EXPECTED_RUNTIME_WRITES:
ACTUAL_RUNTIME_WRITES:
UNEXPECTED_RUNTIME_WRITES:
RUNTIME_WRITES_TASK_CREATED:
RUNTIME_WRITES_TASK_MODIFIED:
RUNTIME_WRITES_PRE_EXISTING_UNATTRIBUTED:
RUNTIME_WRITES_PRE_EXISTING_RATIFIED:
RUNTIME_OUTPUT_RESTORATION_AUTHORITY:
RUNTIME_OUTPUT_CLEANUP_AUTHORIZED:
```

If a mandatory command is known to write outside the allowlist, do not run it. Emit the Packet stop token or `PLANNER_PACKET_CONTRACT_CONFLICT`, with `COMMAND_RUN: NO` and `UNAUTHORIZED_WRITE_CREATED: NO`. Do not guess a cache-redirection flag or environment variable, and fail closed on an unexpected write.

#### `PROTECTED_IMMUTABLE_OBJECT_SEARCH_PREFLIGHT`

Before any command that may inspect content from multiple committed objects, first enumerate and classify the complete planned search universe without reading blob bytes. This includes `git grep` (including `-l`, `-q`, or exit-status-only use), `git log -S`/`-G`, `git show --textconv`, `git rev-list --objects` followed by content inspection, `git cat-file --batch`/`--batch-command`, exported-object scans, custom blob scanners, and archive extraction followed by search. Emit:

```text
SEARCH_INTENT:
SEARCH_AUTHORITY_REFS:
- <EXACT REF/COMMIT/TREE>
PLANNED_SEARCH_COMMAND_FAMILY:
PLANNED_SEARCH_UNIVERSE:
- <EXACT TREES/PATHS/BLOBS>
PACKET_PROTECTED_PATHS:
- <EXACT PATHS/PATTERNS>
DEFAULT_PROTECTED_BINARY_PATTERNS:
- "*.db"
- "*.sqlite"
- "*.sqlite3"
- "*.db-wal"
- "*.db-shm"
- "*.mdb"
- "*.parquet"
- "*.feather"
- "*.arrow"
- "*.pkl"
- "*.pickle"
- "*.bin"
- "*.backup"
- "*.bak"
PACKET_PROTECTED_CONTENT_CLASSES:
- <DB/SNAPSHOT/SECRET/OWNER_PROTECTED/OTHER>
SAFE_TEXT_PATHS:
- <EXACT ENUMERATED PATHS>
SAFE_BLOB_IDS:
- <EXACT BLOBS IF PATH RESOLUTION IS NOT AVAILABLE>
PROTECTED_OBJECTS_PRESENT_IN_SEARCH_UNIVERSE:
YES | NO | UNKNOWN
COMMAND_PATH_RESTRICTED_TO_SAFE_SET:
YES | NO
SEARCH_COMMAND_ALLOWED:
YES | NO
```

`UNKNOWN` fails closed. If any protected object is present and the content command is not restricted to the exact safe set, set `SEARCH_COMMAND_ALLOWED: NO` and `COMMAND_EXECUTED: NO`. Filename-only, binary-suppressed, quiet, summary, and exit-code-only modes still inspect content. Exclusions must apply before the content command; never search broadly and filter afterward. Packet exact protected paths outrank defaults, a Packet exception for one binary path never permits its class, and a content/MIME guess never overrides path protection. Classify symlinks, submodules, Git LFS pointers, and special modes explicitly rather than as safe text. If no explicit safe set can be formed, emit `STOP_BEFORE_PROTECTED_OBJECT_SEARCH`.

#### `SAFE_GIT_OBJECT_SEARCH_ROUTER`

Use these ordered routes for read-only authority, provenance, forensic, or committed-object content searches:

1. **Inventory only.** Pin exact refs/commits/trees and use `git ls-tree`, metadata-only `git rev-list --objects`, `git cat-file -t`, or `git cat-file -s` to obtain only paths, object IDs, types, sizes, modes, and ref/tree identity. Do not read blob bytes.
2. **Classify paths.** Assign exactly one of `SAFE_TEXT`, `PROTECTED_CONTENT`, `OWNER_PROTECTED`, `UNKNOWN`, `SUBMODULE`, or `SYMLINK_OR_MODE_SPECIAL`. Only `SAFE_TEXT` may proceed; `UNKNOWN` may not.
3. **Search explicit content.** Search only an `EXACT_SAFE_PATH_LIST` or `EXACT_SAFE_BLOB_ID_LIST`. For Git, use `--literal-pathspecs` or `:(literal)` exact pathspecs in a command such as `git grep <pattern> <exact-ref> -- <exact-safe-path...>`; batch long lists without falling back to a broad ref. Do not create a scratch path list unless runtime output is authorized.
4. **Hash exact content.** Resolve the exact blob ID first. Hash only that blob. A protected blob requires explicit Packet authorization even for SHA-256, and a Packet prohibition on hashing is absolute.

An unrestricted `git grep <pattern> <broad-ref>`, a content scanner fed every object from `git rev-list --objects`, or an unrestricted batch object reader is forbidden whenever protected or unknown objects may be included.

#### `PROTECTED_BOUNDARY_VIOLATION_GATE`

If an executed command read Packet-forbidden content, report:

```text
PROTECTED_CONTENT_SCAN_OCCURRED:
YES
WORKER_CAUSED:
YES | NO | UNKNOWN
EXACT_COMMAND:
EXACT_REFS_OR_TREES:
PROTECTED_PATHS_OR_BLOBS_SCANNED:
- <EXACT IDENTITIES>
SEMANTIC_CONTENT_PRINTED:
YES | NO
FILESYSTEM_WRITE_OCCURRED:
YES | NO
BOUNDARY_COMPLIANCE_VERDICT:
REFUTED
RETROACTIVE_REMEDIATION_POSSIBLE:
NO
```

No printed content and no SQLite open do not restore compliance: content inspection already occurred. Transcript rewriting or a DELTA Judge cannot erase it. Stop success-token, publication, adoption, and decision-ready claims. Preserve an otherwise sound technical finding only as `PROVISIONALLY_SUPPORTED`; a new clean retry must repeat the full load-bearing search from safe inputs.

#### `JUDGE_REPRODUCIBLE_TRANSCRIPT_EVIDENCE_GATE`

Before a Fresh Judge receives any load-bearing matrix, inventory, truth table, or digest, include:

```text
EVIDENCE_OBJECT_NAME:
CANONICAL_ROWS:
- <EVERY LOAD-BEARING ROW, NOT A SUMMARY ONLY>
CANONICAL_ROW_SCHEMA:
CANONICAL_ROW_ORDER:
CANONICAL_VALUE_NORMALIZATION:
CANONICAL_SERIALIZATION:
<EXACT UTF-8/SEPARATOR/NEWLINE RULE>
WORKER_COMPUTED_SHA256:
SOURCE_OBJECT_IDENTITIES:
- <REF/PATH/BLOB/SHA256>
JUDGE_INPUT_INCLUDES_ALL_ROWS:
YES | NO
JUDGE_CAN_RECOMPUTE_DIGEST_WITHOUT_WORKER_MEMORY:
YES | NO
```

Counts, a digest, and examples alone are insufficient. In-memory dataframes, objects, shell variables, and hidden session state are not Fresh Judge authority. The Judge must receive every constitutive row and enough immutable source identities to reproduce it. If all rows do not safely fit in the Judge input, emit `JUDGE_EVIDENCE_PORTABILITY_BLOCKED` and require Planner authorization for a durable transcript artifact; never silently omit rows. If independent recomputation is `NO`, do not claim the digest was independently verified. A digest mismatch or failed recomputation forbids `VERIFIED`.

#### `SEARCH_UNIVERSE_COMPLETENESS_GATE`

For authority or provenance searches, mechanically derive sets and report:

```text
FROZEN_REF_SNAPSHOT_COUNT:
FROZEN_REF_SNAPSHOT_DIGEST:
UNIQUE_REF_TIP_OID_COUNT:
UNIQUE_REF_TIP_OID_DIGEST:
UNIQUE_CANDIDATE_PATH_COUNT:
UNIQUE_REF_PATH_PAIR_COUNT:
UNIQUE_REF_PATH_BLOB_TRIPLE_COUNT:
CONTRACT_AUTHORITY_PATH_COUNT:
SAFE_TEXT_CANDIDATE_COUNT:
PROTECTED_CANDIDATE_COUNT:
UNKNOWN_CANDIDATE_COUNT:
CONTENT_SEARCHED_SAFE_PATH_COUNT:
CONTENT_SEARCHED_SAFE_BLOB_COUNT:
PROTECTED_CONTENT_SEARCHED_COUNT:
PATH_KEY:
path
REF_PATH_KEY:
(ref, path)
REF_PATH_BLOB_KEY:
(ref, path, blob_sha)
```

Never manually total or mix path, ref/path, ref/path/blob, ref-tip, or authority-tree counts. Freeze refs before derivation. Later unrelated ref drift is `REF_DRIFT_CLASSIFICATION: UNRELATED_REF_DRIFT_TOLERATED` when the pinned contract/candidate objects remain readable and byte-identical; report `AUTHORITY_OBJECT_CONTINUITY: PASS`.

#### `VERDICT_AXIS_SEPARATION_GATE`

Report these axes independently:

```text
SUBSTANTIVE_TECHNICAL_CONCLUSION:
VERIFIED | PROVISIONALLY_SUPPORTED | REFUTED | BLOCKED_UNVERIFIABLE | NOT_APPLICABLE
TASK_BOUNDARY_COMPLIANCE_VERDICT:
VERIFIED | VERIFIED_WITH_CAVEATS | REFUTED | BLOCKED_UNVERIFIABLE
CURRENT_TASK_EXECUTION_PROVENANCE:
VERIFIED | VERIFIED_WITH_CAVEATS | PARTIAL | BLOCKED_UNVERIFIABLE
FINAL_JUDGE_VERDICT:
VERIFIED | VERIFIED_WITH_CAVEATS | REFUTED | BLOCKED_UNVERIFIABLE | NOT_APPLICABLE
FINAL_DECISION_ADOPTION_STATUS:
ADOPTED | NOT_ADOPTED | NOT_APPLICABLE
```

Execution being completely recorded proves provenance, not compliance. A technically sound run that scanned protected content is `PROVISIONALLY_SUPPORTED` / `REFUTED` / accurately observed provenance / `REFUTED` / `NOT_ADOPTED`.

#### `JUDGE_REMEDIATION_ELIGIBILITY_GATE`

Classify every Judge finding:

```text
FINDING_TYPE:
REPORT_DEFECT | EXECUTION_BOUNDARY_VIOLATION | AUTHORITY_DEFECT | MUTATION_VIOLATION
TRANSCRIPT_REMEDIATION_ELIGIBLE:
YES | NO
```

Only report defects such as an incorrect count, omitted existing row/evidence, formula transcription error, or descriptive misclassification may use transcript remediation, and only when source objects remain intact and no boundary violation occurred. Protected-content reads, unauthorized filesystem/Git/DB mutation, no-fetch/no-network violations, dirty-checkout authority, Owner-protected reads, allowlist escape, irreversible external mutation, or irretrievably non-portable Judge evidence are not remediable. For `NO`, report:

```text
DELTA_REJUDGE_REQUIRED:
NO
DELTA_REJUDGE_RUN:
NO
CLEAN_RETRY_REQUIRED:
YES
```

For an eligible report-only correction, report `DELTA_REJUDGE_REQUIRED: YES` and follow the existing one-remediation limit.

#### Protected-object static acceptance table

These cases are binding self-tests for the rules above:

| Scenario | Input | Required result |
|---|---|---|
| 1. Broad Git grep with committed DB | Protected `backup/data.db`; planned `git grep -l token <ref>` | `PROTECTED_OBJECTS_PRESENT_IN_SEARCH_UNIVERSE: YES`; `COMMAND_PATH_RESTRICTED_TO_SAFE_SET: NO`; `SEARCH_COMMAND_ALLOWED: NO`; `COMMAND_EXECUTED: NO` |
| 2. Exact safe text paths | DB present; exact `requirements.txt` and workflow YAML pathspecs | `SEARCH_COMMAND_ALLOWED: YES`; `PROTECTED_CONTENT_SEARCHED_COUNT: 0` |
| 3. Filename-only misconception | `git grep -l` | `CONTENT_INSPECTION_OCCURRED_IF_EXECUTED: YES`; never metadata-only |
| 4. Hash prohibited DB blob | Packet forbids reading or hashing snapshot | `HASH_AUTHORIZED: NO`; `COMMAND_EXECUTED: NO` |
| 5. Unknown file type | Extensionless path with unknown classification | `CLASSIFICATION: UNKNOWN`; `CONTENT_SEARCH_ALLOWED: NO` |
| 6. Judge matrix only in memory | Count and digest, no canonical rows | `JUDGE_CAN_RECOMPUTE_DIGEST_WITHOUT_WORKER_MEMORY: NO`; `FINAL_JUDGE_VERIFIED_ALLOWED: NO` |
| 7. Full transcript matrix | All rows, order, serialization, and source IDs present | `JUDGE_CAN_RECOMPUTE_DIGEST_WITHOUT_WORKER_MEMORY: YES` |
| 8. Transcript-only count remediation | Wrong candidate count; unchanged sources; no violation | `TRANSCRIPT_REMEDIATION_ELIGIBLE: YES`; `DELTA_REJUDGE_REQUIRED: YES` |
| 9. Protected scan already occurred | Broad search read DB blob; no output or write | `TASK_BOUNDARY_COMPLIANCE_VERDICT: REFUTED`; `TRANSCRIPT_REMEDIATION_ELIGIBLE: NO`; `CLEAN_RETRY_REQUIRED: YES` |
| 10. Technical result survives provisionally | Correct Route 3 evidence plus protected scan | `SUBSTANTIVE_TECHNICAL_CONCLUSION: PROVISIONALLY_SUPPORTED`; `FINAL_DECISION_ADOPTION_STATUS: NOT_ADOPTED` |
| 11. Separate inventory counts | 12 paths; 15 ref/path/blob triples; 10 authority paths | `UNIQUE_CANDIDATE_PATH_COUNT: 12`; `UNIQUE_REF_PATH_BLOB_TRIPLE_COUNT: 15`; `CONTRACT_AUTHORITY_PATH_COUNT: 10` |
| 12. Ref drift with stable objects | Unrelated ref changed; pinned objects readable and identical | `REF_DRIFT_CLASSIFICATION: UNRELATED_REF_DRIFT_TOLERATED`; `AUTHORITY_OBJECT_CONTINUITY: PASS` |

#### `LIFECYCLE_AXIS_INTEGRITY_GATE`

Keep technical completion independent from publication and cleanup. When final tests and the final Judge pass but publication is blocked by runtime, permission, or an external gate, report:

```text
IMPLEMENTATION_LIFECYCLE_STATUS: COMPLETE
PR_PUBLICATION_STATUS: BLOCKED
POSTMERGE_LIFECYCLE_STATUS: NOT_STARTED
BRANCH_CLEANUP_STATUS: BLOCKED
FULL_PR_LIFECYCLE_CLOSED: NO
CURRENT_TREE_TECHNICAL_VERDICT: VERIFIED
```

Use `IMPLEMENTATION_LIFECYCLE_STATUS: BLOCKED` only when required implementation/remediation is incomplete, final tests fail, the required final Judge is missing or refuted, the implementation contract is incomplete, or missing acceptance prevents technical completion.

#### `PROVENANCE_AXIS_SEPARATION_GATE`

Report current execution, inherited history, and current-tree technical validity independently:

```text
CURRENT_TASK_EXECUTION_PROVENANCE:
VERIFIED | PARTIAL | BLOCKED_UNVERIFIABLE | NOT_APPLICABLE

INHERITED_HISTORICAL_EXECUTION_PROVENANCE:
VERIFIED | PARTIAL | BLOCKED_UNVERIFIABLE | UNAVAILABLE | NOT_APPLICABLE

CURRENT_TREE_TECHNICAL_VERDICT:
VERIFIED | VERIFIED_WITH_CAVEATS | REFUTED | BLOCKED_UNVERIFIABLE | NOT_APPLICABLE
```

Incomplete inherited provenance never downgrades a fully observed current-task execution. Source-only review does not by itself make historical provenance partial.

#### `EXTERNAL_MUTATION_ATTEMPT_LEDGER_GATE`

Record every external mutation attempt independently and never erase an earlier failure:

```text
MUTATION_NAME:
ATTEMPT_NUMBER:
TOOL_OR_ENDPOINT:
METHOD:
FIXED_HEAD:
SCOPE:
RESULT: DEFINITE_SUCCESS | DEFINITE_FAILURE | UNKNOWN
MUTATION_APPLIED: YES | NO | UNKNOWN
READ_AFTER_WRITE:
RETRY_ELIGIBLE: YES | NO
RETRY_REASON:
```

Distinguish a definitive permission denial from a timeout, 5xx, or ambiguous result. A definitive failure plus read-after-write proof that no mutation occurred may use an authorized fallback. An ambiguous result may not switch tool or endpoint. For flattened scenario reports, preserve per-attempt facts such as `ATTEMPT_1_RESULT`, `ATTEMPT_1_MUTATION_APPLIED`, and `ATTEMPT_2_RESULT`.

#### `EXACT_CLEANUP_NO_RELOCATION_GATE`

This gate is a narrow exception to, and takes precedence over, the authorized-fallback rule in `EXTERNAL_MUTATION_ATTEMPT_LEDGER_GATE`.

When a Packet authorizes deletion of one exact path:

```text
AUTHORIZED_CLEANUP_PATH:
<EXACT_PATH>
```

the exact path and exact cleanup action are the entire cleanup authority. They do not authorize an alternate method, source, or destination. If the original deletion command is rejected, denied, or permission-blocked, report:

```text
ORIGINAL_CLEANUP_COMPLETED:
NO

ALTERNATE_METHOD_ALLOWED:
NO
```

The Worker must not:

- move the path to Trash;
- rename it;
- move it to another runtime root;
- copy then delete;
- use Python, Finder, a filesystem API, or another shell command as a substitute;
- claim cleanup merely because the original path became absent.

Owner authorization for the exact deletion does not satisfy or bypass a platform or harness permission prompt and does not expand the authorized action. Stop immediately without a substitute and respond:

```text
HARNESS_PERMISSION_BLOCKED

OWNER_AUTHORIZATION:
ALREADY_PRESENT

AUTHORIZED_ACTION:
<EXACT_CLEANUP_ACTION>

BLOCKED_TOOL_OR_COMMAND:
<EXACT_REJECTED_ACTION>

MUTATIONS_ALREADY_COMPLETED:
<EXACT_LEDGER>

MUTATIONS_NOT_COMPLETED:
<EXACT_CLEANUP>

REQUIRED_USER_ACTION:
approve the original platform or harness permission prompt
```

Cleanup completion requires proving that the authorized content was actually deleted, not merely that the original pathname is absent. If content is moved outside the authorized cleanup root, classify:

```text
UNEXPECTED_RUNTIME_WRITE:
YES

RUNTIME_RELOCATION_OUTSIDE_ALLOWLIST:
YES

TASK_CONTENT_ACTUALLY_DELETED:
NO

RUNTIME_CLEANUP_STATUS:
INCOMPLETE

TASK_BOUNDARY_COMPLIANCE_VERDICT:
REFUTED
```

Report the source and surviving-content facts separately:

```text
ORIGINAL_PATH_PRESENT:
CONTENT_PRESENT_ELSEWHERE:
ACTUAL_DESTINATION:
AUTHORIZED_DESTINATION:
```

An exact Trash path named as `AUTHORIZED_CLEANUP_PATH` authorizes deletion of only that exact Trash target. It does not authorize Trash as an alternate destination for any other path, and no parent, sibling, child, or newly created Trash target is implied.

Binding static acceptance scenarios:

| Scenario | Input | Required result |
|---|---|---|
| 1. Exact deletion succeeds | The exact authorized deletion command succeeds | `ORIGINAL_CLEANUP_COMPLETED: YES`; `RUNTIME_CLEANUP_STATUS: COMPLETE` |
| 2. Permission blocks deletion | The exact deletion is denied or permission-blocked | No substitute; `HARNESS_PERMISSION_BLOCKED`; `ALTERNATE_METHOD_ALLOWED: NO` |
| 3. `rm` is rejected | The authorized `rm` command is rejected | No move to Trash and no alternate command, API, or tool |
| 4. Original absent, content survives | The original path is absent but the content exists elsewhere | `TASK_CONTENT_ACTUALLY_DELETED: NO`; `RUNTIME_CLEANUP_STATUS: INCOMPLETE`, with the four separate location fields |
| 5. Trash relocation occurs | Content was moved to Trash instead of deleted | `UNEXPECTED_RUNTIME_WRITE: YES`; `RUNTIME_RELOCATION_OUTSIDE_ALLOWLIST: YES`; `TASK_BOUNDARY_COMPLIANCE_VERDICT: REFUTED` |
| 6. Exact Trash target authorized | The Packet authorizes deletion of one exact Trash target | Only that exact target may be deleted; it may not be used as an alternate destination |

#### `EVIDENCE_CLASSIFICATION_GATE`

Classify evidence by final-tree validity:

```text
NEW_FINAL_TREE_EVIDENCE:
REUSED_FINAL_TREE_EVIDENCE:
INVALIDATED_EVIDENCE:
UNAFFECTED_EVIDENCE_REUSED_BY_DELTA:
```

Do not call evidence created in the current run reused. Any Initial-Judge remediation requires the initial candidate and initial final-tree verdict under `INVALIDATED_EVIDENCE`; never use a `REFUTED` verdict as final-tree validation.

#### `BRANCH_STATUS_ACTION_GATE`

Separate observed status from performed action:

```text
TASK_BRANCH_INITIAL_STATUS:
NOT_CREATED | PRESENT_LOCAL_ONLY | PRESENT_LOCAL_REMOTE | ABSENT

TASK_BRANCH_FINAL_STATUS:
RETAINED_LOCAL_REMOTE | RETAINED_LOCAL_ONLY | DELETED | ALREADY_ABSENT | NOT_APPLICABLE

BRANCH_DELETION_PERFORMED_THIS_TASK:
YES | NO | NOT_APPLICABLE
```

A never-created branch is not `DELETED`; an observed-absent branch is `ALREADY_ABSENT` with deletion `NO`; retaining a branch while its PR is open is not cleanup failure. Keep local and remote ref observations explicit.

#### `COMMIT_TREE_IDENTITY_GATE`

Report commit and tree identities without substitution:

```text
TASK_COMMIT:
TASK_TREE:
FINAL_HEAD:
FINAL_TREE:
CANONICAL_FINAL_HEAD:
CANONICAL_FINAL_TREE:
COMMIT_LINK:
```

Never label a tree SHA as a commit or a commit SHA as a tree. With no remote or push, a local-only commit requires `COMMIT_LINK: NOT_APPLICABLE`; never fabricate a GitHub URL.

### Apply the Loop capability gate

Every item must be YES:

1. This is the main Worker, not a nested subagent.
2. The Codex environment exposes usable subagent capability.
3. Write work can be isolated, or every card is read-only.
4. The main Worker can be the sole Integration Owner.
5. The main Worker can run integrated end-to-end acceptance.

If any item is NO or UNKNOWN, emit `LOOP_CAPABILITY_FAILED` and fall back to STANDARD or STANDARD_JUDGED.

### Apply the Loop eligibility gate

Every item must be YES:

1. Scope is fixed.
2. At least two genuinely independent cards exist.
3. Every card has independent acceptance.
4. No card needs another unfinished card's output.
5. Cards do not share a mutable database or runtime.
6. Write scopes do not overlap.
7. One Integration Owner is named.
8. Integrated acceptance is defined.
9. Parallel savings exceed handoff and integration cost.

If any item is NO or UNKNOWN, emit `LOOP_NOT_ELIGIBLE` and fall back to STANDARD or STANDARD_JUDGED. If all capability and eligibility items are YES, select LOOP_JUDGED and hand only execution cards to sibling `../fable-loop/SKILL.md`.

Deeper material loads on demand: `references/failure-modes.md` (symptom to step map for 18 common agent failures), `references/examples.md` (generic worked examples), `references/domains/` (domain adapters; `references/domains/TEMPLATE.md` is their schema), and `references/flowcharts.md` (the executable routing summary). Use the installed `skill-creator` skill if the user asks for a new reusable specialized procedure; do not assume a separate generator skill exists.

**Domain adapters.** Coding is the default domain. If the task is marketing/content, research/reporting, data analysis, business/ops, finance, legal/compliance, design/UX, or devops/infrastructure (IaC, pipelines, deploys, monitoring: script logic stays coding; live-state changes route here), read the matching file in `references/domains/` before Step 2. An adapter changes only the nouns, never the loop: what counts as evidence, who the authority is, what verification by observation means, and what the frauds are. Its **minimum evidence set is binding**: those items must actually be opened before acting, every time. Research is never optional; the adapter defines how much is enough. Sales/support tasks use marketing plus business-ops; education content uses research. Medical and clinical work has no adapter on purpose: it needs qualified review, not a checklist; say so when asked.

**Triviality gate (run first).** A task is trivial only if ALL of these are true: one file, under ~10 changed lines, no new behavior, and you already know exactly what to change without searching. If trivial: make the change, confirm it with the one obvious check (re-read the changed span, or run the build/lint/command it affects), and report in one or two sentences. Everything else, and anything you are unsure about, gets the full loop.

**Fit gate (run next, before Step 0).** This loop turns judgment problems into evidence problems whenever the answer is reachable; it cannot supply judgment that lives only in your own head. So first locate where the answer is, and route:

- **In sources you can open** (a spec, file, dataset, check, or docs): run the loop. This is the default.
- **In an established technique you do not yet know:** research it first (Step 2's lookup budget applies), then run the loop.
- **Only in your own inference, nothing to open or look up:** say so. Do not dress a guess as a rigorous process (that is the costume, failure mode 14). Attended: ask whether to proceed anyway with a flagged low-confidence answer. Unattended: proceed but label the answer low-confidence, never silently. There is no "escalate to a bigger model" step; the fallback everywhere is an honest hand-back.
- **In a specialized procedure the base model lacks, and it recurs (or the user asked for reusable tooling):** use the installed `skill-creator` instructions to update or create the requested reusable Skill; do not assume an uninstalled generator exists.

Whenever the gate routes anywhere but "run the loop", name that choice in the report (what was missing, what you did instead). A silent detour is indistinguishable from a skipped step.

## Step 0 - Classify the ask when no authoritative packet exists

Run Steps 0-3 for AUTHORITATIVE_PACKET_ABSENT or an explicitly invoked legacy mode. For PRESENT or PARTIAL implementation packets, the Worker entry contract already supplies the execution contract; skip this full planning pass.

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

1. **Intent gate, before any behavior-changing edit.** Write one line: `INTENT: code does <X>; the failing check/task expects <Y>; the spec (README/docs/docstring) says <Z>`. You must actually open the README/docs/docstrings to fill the third slot, and if you change behavior this line must appear verbatim in your final report. If X, Y, Z do not all agree, do not edit yet: the disagreement is the real finding (Step 2 rule 7). Authority order when they disagree: an explicit user statement beats the spec, the spec beats the tests, the tests beat current code behavior. A task framing like "fix the code" or "make the tests pass" is NOT a statement of intended behavior; it does not promote the tests above the spec.
2. **Recall gate, before first use of anything you have not opened this session.** An API signature, endpoint, config key, price, figure, or regulation written from memory is not evidence. Stop and open its source now (the docs file, the library source, a fetched page; a fresh two-lookup budget as in Step 2), or, if no source is reachable, write it and label it in the report as memory, unverified. Discovering ignorance re-opens Step 2 exactly like a surprise does.

Implement one coherent change batch at a time. After each material batch,
inspect the actual diff and run the cheapest directly relevant diagnostic or
focused test; understand failure before the next behavioral edit. A batch
is a meaningful unit, not each line or file. Do not run full suite or invoke a
Judge per batch; retain proportional rules. On failure, distinguish product,
test, environment, wrong command, stale build/runtime path, or contract
conflict. Do not weaken acceptance or chase green; use existing attempt,
conflict, and stop rules.

3. **Smallest correct change.** Touch only what the task needs. Match the existing style even if you would do it differently.
4. **Precise edits over rewrites.** Rewrite a whole file only if you authored it this session or have fully read it.
5. **Track multi-part work.** Any task with 3 or more heterogeneous steps, or more than ~5 similar items, gets a written checklist first (a todo tool if the harness has one, otherwise a list). Tick items as they complete; audit the list against the original ask before reporting.
6. **Never destroy without looking.** Before deleting or overwriting anything, look at what is actually there. If it contradicts how it was described, stop and surface that.
7. **Failed-edit recovery ladder.** Re-read the exact region, adjust the match, retry once. Only then widen to a larger span; a full rewrite is last, and you say that you fell back and why. Never retry a failed call verbatim.
8. **Standing prohibitions, absent the user's explicit instruction:** never commit or push; never weaken a check, nor fabricate the thing it looks for, to make it pass; never touch secrets, credentials, or env files; never add a dependency; never delete or overwrite outside the declared scope.

## Step 5 - Verify by observation

For one final implementation tree, run the complete local suite at most once unless evidence is invalidated. The Worker is the default local full-suite owner; a `FULL` Judge becomes the owner only when valid Worker full-suite evidence is absent or invalid. `BOUNDED` and `DELTA` Judges never rerun the complete suite. Exact-head CI is the post-push full-suite independent reproduction. If any load-bearing code or test changes after the suite, record `INVALIDATED_EVIDENCE: prior full suite` and run the complete suite once on the new final tree. Metadata, PR-body, or branch-lifecycle-only changes do not invalidate test evidence.

Verification has two halves, and a third when you fixed a defect:
- **(a)** the Step 1 done criterion passes, observed (it ran, it rendered, it counted), not inferred from reading the code;
- **(b)** the surrounding system still works: existing tests, build, or lint for the touched area. A green targeted check with a broken build is a failed verification.
- **(c) Twin check, whenever you fixed a defect.** A bug found in one place is presumed to recur elsewhere until you have searched. Name the exact wrong construct, search the whole project for it, and write one line that must appear verbatim in your report: `TWINS: searched <the pattern> - found <N> other sites: <files, or "none">`. Fix them or list them; a completeness claim with no search behind it is failure mode 14.

On an acceptance failure, attribute it before editing:

1. **Harness**: confirm the command reaches the intended suite, expected value and fixture are current, the driver reads the correct files, and the verification tool itself works.
2. **Deployment/execution chain**: confirm the new code is loaded, rebuild or restart requirements are met, caches and bundles are fresh, the canonical repository/worktree is active, and runtime wiring reaches the changed file. Use a version-specific behavior signature when loading is uncertain.
3. **Product**: only after excluding the first two layers, repair the violated invariant rather than its surface symptom.

An evidence-backed attempt must include a falsifiable hypothesis, a corresponding code or environment correction, a real acceptance rerun, and the actual output. Repeating the same command is not a new attempt. After the first failure, correct from the observed output. After the second, force the complete Harness → Deployment → Product ladder. After the third evidence-backed failure on the same issue, stop with `BLOCKED_AFTER_THREE_EVIDENCE_BACKED_ATTEMPTS` and report the attempts and current hypothesis.

When blocked by anything outside your control (credentials, environment, permissions), stop and report the blocker; do not spend the three-attempt budget on identical reruns.

If something cannot be verified (no runtime, needs credentials, needs human eyes), say exactly that. Never let an unverified claim pass as a verified one.

## Step 6 - Report outcome-first

At completion, use self-review or the authoritative Judge flow in
order: (1) contract compliance—mandatory requirements, allowed scope, forbidden
actions, acceptance criteria; (2) engineering quality—correctness, regression
risk, maintainability, test strength. Neither lens excuses the other; reuse
exact-final-tree evidence instead of redundant verification.

For ordinary FAST and STANDARD implementation work, use this compact evidence report unless the Planner supplied a stricter format:

```text
STATUS:
ROUTE:
CHANGED:
VERIFIED:
IMPLEMENTATION_LIFECYCLE_STATUS:
PR_PUBLICATION_STATUS:
POSTMERGE_LIFECYCLE_STATUS:
BRANCH_CLEANUP_STATUS:
FULL_PR_LIFECYCLE_CLOSED:
NOT RUN:
BLOCKED:
RISKS:
FILES_WRITTEN_DURING_TASK:
FILES_RETAINED_AT_END:
FILES_DELETED_BEFORE_END:
LOCAL_FULL_SUITE_RUNS:
FOCUSED_TEST_RUNS:
INITIAL_JUDGE_RUNS:
DELTA_REJUDGE_RUNS:
FULL_JUDGE_RUNS:
EXACT_HEAD_CI_RUNS:
REUSED_EVIDENCE:
INVALIDATED_EVIDENCE:
```

Every final implementation report, including judged routes and lifecycle closure, must include the eight verification fields above. Use `[Confirmed]`, `[Inferred]`, `[Unknown]`, `PASS`, `FAIL`, `BLOCKED`, and `NOT RUN` literally. Every PASS needs a command, runtime observation, or explicitly identified reusable evidence that satisfies the Judge reuse gate. Judged routes may add the Judge's per-criterion verdict table, but must not replace evidence with process narration. Keep the verification fields concise: use integer counts for runs and name reused or invalidated evidence, or `NONE`.

Maintain an in-memory filesystem write ledger from the first write. Include every created or modified file: intended source edits, `/tmp` scratch files, downloaded diffs or logs, shell-redirection outputs, temporary scripts or JSON, generated reports, memory files, and files later deleted. List every written path under `FILES_WRITTEN_DURING_TASK`; partition those paths between `FILES_RETAINED_AT_END` and `FILES_DELETED_BEFORE_END`. Report `NONE` only when the corresponding set is truly empty. Creating a file and deleting it before the report never permits `FILES_WRITTEN_DURING_TASK: NONE`.

When a task forbids every filesystem write, prefer API responses, pipes, stdout, or in-memory processing. If a required tool cannot operate without writing, stop before the write and report the limitation; never write, delete, and then claim no write occurred.

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
