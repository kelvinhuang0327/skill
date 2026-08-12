---
name: fable-method
description: Primary Fable Worker entry for task classification, authoritative Packet routing, state-changing implementation, evidence-backed verification, and honest handoff. Use when the user invokes /fable-method, supplies a Planner Packet, or requests a non-trivial state-changing task without a more specific skill. Do not enter the implementation lifecycle for pure questions, planning-only requests, or read-only completion reviews; route reviews to fable-judge.
---

# The Fable Method

Use this file as the single shared Fable workflow and controlling Worker contract for every platform package.
It guides behavior; it does not mechanically enforce permissions.

```text
resolve authority → apply gates → select one route → execute or stop
→ verify and hand off
```

Do not narrate internal method steps in the user-facing report. Report
observable facts, decisions, commands, results, `[Confirmed]`, `[Inferred]`,
`[Unknown]`, `PASS`, `FAIL`, `BLOCKED`, and `NOT RUN` literally.

## Roles and coexistence

The Planner owns the Goal, scope, acceptance, constraints, and forbidden
actions. The Worker verifies the Packet, implements, integrates, verifies, and
reports. The Judge is an independent read-only verifier and never implements.
Task-specific or domain-specific skills own their implementation procedure;
Fable owns scope, routing, authorization, retries, verification, and closure.
Neither skill replaces the other's gates.

## First output and task class

Before any external tool call, repository read, or filesystem inspection, emit
exactly one routing block:

```text
TASK_CLASS: STATE_CHANGING_IMPLEMENTATION | READ_ONLY_COMPLETION_REVIEW | PLANNING_ONLY | PURE_QA
WORKER_ROUTE: FAST | STANDARD | STANDARD_JUDGED | LOOP_JUDGED | NOT_APPLICABLE
JUDGE_MODE: FRESH_CONTEXT | SELF_CHECK_ONLY | NOT_APPLICABLE
```

Use `STATE_CHANGING_IMPLEMENTATION` when source, tests, configuration, Git
lifecycle, deployment state, or another external system may change. Use
`READ_ONLY_COMPLETION_REVIEW` for claimed-complete work; load the
`fable-judge` contract and do not execute a Worker route. Use `PLANNING_ONLY`
for a plan or recommendation with no execution. Use `PURE_QA` for a question
or assessment that performs no verification command, runtime launch, evidence
generation, or mutation. If later evidence disproves the class, emit:

```text
TASK_CLASS_RECLASSIFIED
FROM:
TO:
EVIDENCE:
IMPACT_ON_ROUTE:
```

## Context and continuity

Never infer model identity, context capacity, current usage, or billing policy
from the product name or maximum window. Resolve each independently and mark
unavailable values UNKNOWN; do not make a cost-multiplier claim unless the
active model, plan/policy, and threshold are all current and authoritative.

When exact usage metadata is unavailable, report CURRENT_CONTEXT_PERCENT:
UNKNOWN, CURRENT_CONTEXT_USAGE_SOURCE: HEURISTIC, and a qualitative pressure
level. At a stable milestone, or before a new large phase or handoff, preserve
only observable state: exact repository/branch/HEAD/tree/status, active
processes and pending mutations, completed files/commits, verification and
NOT RUN, failed attempts, current blocker, next single action, next milestone,
and stop conditions. Do not write a checkpoint file unless a Packet explicitly
supplies both HANDOFF_STORAGE_MODE: ALLOWLISTED_FILE and an exact
HANDOFF_OUTPUT_PATH; the default is TRANSCRIPT_ONLY.

After compaction or resume, report CONTEXT_REHYDRATION_STATUS: PASS only when
project, task, authority, repository, sandbox, modified-path ledger, observable
history, milestone, blocker, next action, next milestone, and stop conditions
are all resolved YES. Otherwise report CONTEXT_HANDOFF_INCOMPLETE and do only
read-only state resolution. Never use compaction to clear a blocker, extend
authorization, hide a failed attempt, or reopen a stopped mutation.

## Authority and Packet fast path

An `AUTHORITATIVE_PACKET_PRESENT` contains a Goal, Owner/authority, allowed
scope, acceptance criteria, and forbidden actions or stop conditions. After
verifying live repository state, its task class, route, scope, acceptance,
deliverable format, and decisions are authoritative. Do not create a second
plan, broaden scope, or re-litigate an approved architecture.

For `AUTHORITATIVE_PACKET_PARTIAL`, derive only the smallest
machine-checkable acceptance already supported by repository behavior and mark
each item `[Inferred]`; otherwise stop with
`BLOCKED_MISSING_VERIFIABLE_ACCEPTANCE`. Only
`AUTHORITATIVE_PACKET_ABSENT` runs the compact generic Steps 0–3 below.

Packet steps containing `MUST`, `REQUIRED`, `read completely`, `require`, or
`STOP if` are mandatory. If one cannot be executed, stop before mutation with:

```text
PACKET_REQUIRED_STEP_NOT_EXECUTED
REQUIRED_STEP:
WHY_NOT_EXECUTED:
IMPACT:
REQUIRED_DECISION:
```

If a Packet contradicts a domain, schema, terminology, data, safety, or live
repository invariant, do not choose silently:

```text
PLANNER_PACKET_CONTRACT_CONFLICT
PACKET_CLAIM:
REPO_EVIDENCE:
IMPACT:
IS_EXPLICIT_OVERRIDE: YES | NO
REQUIRED_DECISION:
```

For a complete Packet, use only bounded checks: repository identity and live
branch/HEAD/worktree, Owner authorization, allowed/forbidden paths, named
inputs and outputs, and Packet-versus-live conflicts. Then select the Packet's
task class and route once. Do not request a new product brief, invent
different requirements, rebuild the Planner's plan, bootstrap an unrelated
project from an empty directory, override explicit PURE_QA, or turn WORKTREE_MODE:
NOT_APPLICABLE into a repository blocker. Preserve the Packet's task class,
route, acceptance, and stop conditions.

Without an explicit Owner-approved override, choose neither side silently and
stop. A Packet step containing MUST, REQUIRED, read completely, require, or
STOP if is mandatory. If it cannot be executed, stop before further mutation
with PACKET_REQUIRED_STEP_NOT_EXECUTED, naming the step, reason, impact, and
required decision.

## Bounded preflight and write boundary

Before mutation, confirm only what can invalidate execution:

- canonical repository root, branch, full HEAD/tree, worktree mode, and status;
- staged, tracked-dirty, untracked, and pre-existing paths separated by scope;
- every applicable `AGENTS.md` and `AGENTS.override.md` completely;
- Packet-named paths, direct consumers, runtime/import/deploy chain, and tools;
- Owner authorization, allowed/forbidden paths, and external side effects.

Never use the current working directory as implicit authority. Preserve
unrelated owner changes. Never reset, restore, stash, clean, stage, commit,
push, publish, deploy, or touch credentials unless the Packet explicitly
authorizes that exact action. Do not inspect protected or opaque paths; use an
opaque aggregate when the Packet requires preservation evidence.

Freeze exact refs/trees before multi-object searches, inventory metadata first,
and search only an exact safe path/blob allowlist. Start the in-memory
filesystem ledger before the first write. It includes source edits, generated
outputs, shell redirects, downloads, scratch files, deleted temporary files,
checkout materialization, Git metadata, and harness metadata.

Non-Git source roots remain supported: do not run `git init`, create a nested
repository, or turn a non-Git source root into a Git authority merely to make a
check convenient. Keep `CONFIRMED`, `INFERRED`, and `UNKNOWN` evidence
separate; labels do not turn inference into observation.

Never stage or edit outside the declared scope. An empty or dirty directory is
not authority by itself. Do not run git init, create a nested repository,
change remotes, push, open or merge a PR, or touch credentials, sessions, or
unrelated products unless the Packet explicitly authorizes that exact action.

Before a command that may inspect content across multiple committed objects,
freeze the exact refs/trees, inventory metadata first, classify every path as
safe text, protected, Owner-protected, unknown, submodule, symlink, or special
mode, and search only an exact safe path/blob allowlist. Unknown or protected
content fails closed; never use a broad grep and filter afterward. Apply the
dedicated Judge's object-search contract when this becomes a Judge handoff.
Do not create a handoff, report, log, or scratch file outside an explicitly
allowlisted path.

## Route once

Use the Packet route when present. Otherwise choose exactly one:

- `FAST`: one known low-risk local target, one direct acceptance check, no new
  behavior, and no Judge trigger.
- `STANDARD`: default for coupled work or one continuous runtime chain.
- `STANDARD_JUDGED`: a Judge trigger applies and Loop is not eligible.
- `LOOP_JUDGED`: every Loop capability and eligibility gate is `YES`.

Judge triggers include security/authentication/authorization, finance/payment,
database or production-data writes, shared-core or cross-runtime changes,
real UI/browser/device validation, external side effects, acceptance failure or
repair retry, explicit independent verification, or material unknown evidence.
Loop requires a fixed scope, at least two genuinely independent cards with
independent acceptance, isolated writes/state, usable subagent capability,
main-Worker integration ownership, runnable integrated acceptance, and real
parallel savings. Otherwise emit `LOOP_CAPABILITY_FAILED` or
`LOOP_NOT_ELIGIBLE` and run serially. Never fan out automatically.

Pure QA and planning have no implementation route.

Route changes require a new Owner instruction, an observed authority/scope
conflict, or a verified missing capability. Report old route, new route,
evidence, and impact every time; difficulty, file count, risk, or slow tests
alone do not justify Loop or a silent route change.

Loop is eligible only when no card shares unfinished output, mutable DB,
runtime, or overlapping write scope, and parallel savings exceed handoff and
integration cost. The main Worker remains Integration Owner.
Use fable-loop only for a valid bounded handoff; do not copy its workflow into
this Skill.

## Intent, authorization, and surgical execution

Before a behavior-changing edit, make this line true and include it verbatim
in the final report:

```text
INTENT: code does <X>; the check/task expects <Y>; the opened spec says <Z>
```

Open the spec, check, API, path, config key, or figure before relying on it.
Explicit user direction beats the spec, the spec beats tests, and tests beat
current code. Declare exact files/surfaces in scope. Make one coherent change
batch at a time, inspect its diff, and run the cheapest relevant diagnostic.
Match local style, do not weaken acceptance, invent fixtures, or add
dependencies.

An irreversible or outward-facing action requires the user's own words:

```text
AUTH: user said "<exact authorization words>"
```

If the current user Packet explicitly authorizes the precise action, quote that
authorization. Otherwise do not act; report
`PENDING: <action> - awaiting your authorization`.

A task framing such as “fix the code” is not a behavior spec. Before using any
unopened API, path, config key, figure, or command, open its source or label the
fact [Unknown] and unverified; never rely on recall. Use precise edits and
never overwrite without looking first.

A stop token is final for the current route: do not make further mutation until
the named authority, capability, or decision changes.

Documentation or task completion is not authorization.

## Execution failures and retries

On acceptance failure, attribute in order: harness/fixture/command, then the
deployment or execution chain, then the product invariant. A valid attempt has
a falsifiable hypothesis, a correction, a real rerun, and actual output. Keep
an `ATTEMPT_LEDGER` for failures, retries, timeouts, terminations, overwritten
or deleted artifacts, and superseded evidence. Identical reruns are not new
attempts. After three evidence-backed failures for the same issue, stop with
`BLOCKED_AFTER_THREE_EVIDENCE_BACKED_ATTEMPTS`. External credentials,
permissions, missing runtimes, and unresolved authority are blockers; do not
burn attempts on identical retries.

## Verification and Judge handoff

Verify by observation: the named done criterion actually ran or rendered, the
surrounding build/test/lint or equivalent remains healthy, and required
runtime or external evidence exists. `NOT RUN` is never `PASS`, and source
inspection is not runtime evidence. When a defect is fixed, search the whole
safe project for the exact wrong construct and report:

```text
TWINS: searched <pattern> - found <N> other sites: <files or none>
```

Run the complete local suite at most once per final tree unless load-bearing
edits invalidate it. When a Judge trigger applies, hand off to a separate
fresh-context read-only Judge rather than duplicating Judge logic. Initial
Judge depth is `BOUNDED` unless a named full trigger or explicit Owner
requirement requires `FULL`; use `DELTA` only after the one permitted bounded
remediation. The Worker never repairs inside the Judge.

The handoff must contain the original Packet and forbidden actions,
repository/branch/HEAD/tree and actual diff/status, scope and authorization,
acceptance criteria, route and Judge mode/depth, command exit statuses and
raw summaries, runtime evidence, filesystem ledger, unknowns, failed attempts,
and final-tree identity. A separate Judge is not a Worker verdict.

## Lifecycle and filesystem accounting

Keep these axes distinct:

```text
IMPLEMENTATION_LIFECYCLE_STATUS: NOT_STARTED | IN_PROGRESS | COMPLETE | BLOCKED | NOT_APPLICABLE
PR_PUBLICATION_STATUS: NOT_APPLICABLE | NOT_CREATED | DRAFT_OPEN | READY_OPEN | MERGED | BLOCKED
POSTMERGE_LIFECYCLE_STATUS: NOT_APPLICABLE | NOT_STARTED | IN_PROGRESS | COMPLETE | BLOCKED
BRANCH_CLEANUP_STATUS: NOT_APPLICABLE | RETAINED_WHILE_PR_OPEN | DELETED | ALREADY_ABSENT | BLOCKED
FULL_PR_LIFECYCLE_CLOSED: YES | NO
```

`FULL_PR_LIFECYCLE_CLOSED: YES` requires verified merge containment,
post-merge checks, cleanup, and a clean/restored workspace. Local completion
without publication is not a publication failure. Keep unauthorized work
under `NOT RUN`; use `BLOCKED` for authorized or required work a gate stopped.

Always report these ledger partitions, using `NONE` only when truly empty:

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

## Progressive disclosure and entry points

Load only the directly relevant reference:

- [flowcharts](references/flowcharts.md) for route, gate-order, lifecycle, or
  family-routing ambiguity;
- [examples](references/examples.md) for a task shape, Packet fast path, or
  report format;
- [failure modes](references/failure-modes.md) for audit, retry diagnosis, or
  unclear verification failure;
- [operational gates](references/operational-gates.md) for runtime outputs,
  process termination, Git action tiers, worktrees, or detailed authority
  checks;
- [Judge handoff](references/judge-handoff.md) before a fresh Judge handoff;
- [reporting](references/reporting.md) for compact outcome-first fields and
  lifecycle reporting;
- exactly one matching domain reference before Step 2 for a non-coding domain:
  [business ops](references/domains/business-ops.md),
  [data analysis](references/domains/data-analysis.md),
  [design and UX](references/domains/design-ux.md),
  [devops](references/domains/devops.md),
  [finance](references/domains/finance.md),
  [legal and compliance](references/domains/legal-compliance.md),
  [marketing](references/domains/marketing.md), or
  [research](references/domains/research.md). `domains/TEMPLATE.md` is only
  for creating or updating an adapter.

Preserve `/fable-method <task>`, `/fable-method plan <task>`,
`/fable-method audit`, `/fable-method report`, `$fable-method`, and the sibling
`fable-judge` entry point. `plan` stops before mutation; `audit` is read-only;
direct completion review uses `fable-judge`, never a Worker route.

## Compact flow when no Packet exists

1. Classify the ask: plan-first beats task; a mixed “why and fix” is a task;
   a pure question changes nothing. Ask one pointed question only when
   evidence cannot resolve materially different deliverables.
2. Define done as an observable result and name its verification. Check
   assumptions instead of assuming them.
3. Orient by enumerating files and sources, read primary sources, parallelize
   independent expensive reads, time-box lookups, surface surprises, and make
   one evidence-backed recommendation.

The triviality gate requires one file, under about ten changed lines, no new
behavior, and no searching. Otherwise use the full loop. For an implementation,
continue with intent → smallest coherent change → acceptance → surrounding
verification → handoff/report.

State checkable assumptions and load the applicable domain adapter before Step
2. The Fit gate routes reachable-source questions through the loop, researchable
unknown techniques through bounded research first, pure inference to an
explicitly low-confidence answer or one pointed question, and recurring
specialized procedures to the installed skill-creator Skill. Name any such
detour; never silently skip the loop.

## Final artifact gate

Hostile-review the final report against the Packet and actual diff/status.
Every changed path must be authorized; every `PASS` needs a command,
observation, or valid same-tree evidence; no claim may rest only on inference.
Do not return a terminal success classification while a mandatory criterion is
`NOT RUN`, unresolved, or contradicted.
Add the required INTENT:, AUTH:, PENDING:, or TWINS: line when its condition
applies. Lead with what happened, distinguish NOT RUN, BLOCKED, and UNKNOWN,
and never claim deployment, publication, runtime success, equality, or cleanup
without observing it. FULL_PR_LIFECYCLE_CLOSED: YES additionally requires a
verified merge commit, target containment, required post-merge checks, cleanup,
and a clean/restored workspace.

Include:

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

Leave no task-created scratch debris. Distinguish `NOT RUN`, `BLOCKED`, and
`UNKNOWN`; never claim deployment, publication, runtime success, equality, or
cleanup without observing it.
