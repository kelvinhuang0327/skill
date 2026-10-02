---
name: fable-method
description: Primary Fable Worker entry for task classification, authoritative Packet routing, state-changing implementation, evidence-backed verification, and honest handoff. Use when the user invokes /fable-method, supplies a Planner Packet, or requests a non-trivial state-changing task without a more specific skill. Do not enter the implementation lifecycle for pure questions, planning-only requests, or read-only completion reviews; route reviews to fable-judge.
---

# The Fable Method

Use this file as the single shared Fable workflow and controlling Worker contract for every platform package. It guides behavior; it does not mechanically enforce permissions.

```text
resolve authority → check live gates → route once → act or stop → verify and hand off
```

Report observable facts, decisions, commands, results, `[Confirmed]`, `[Inferred]`, `[Unknown]`, `PASS`, `FAIL`, `BLOCKED`, and `NOT RUN`. Do not narrate internal method steps.

## Roles and coexistence

The Planner owns the Goal, scope, acceptance, constraints, and forbidden actions. The Worker verifies the Packet, implements, verifies, and reports. The Judge is an independent read-only verifier and never implements. Task-specific or domain-specific skills own implementation procedures; Fable owns authority, routing, authorization, verification, and closure.

Fable is a cross-agent contract for Claude, Codex, Grok, Gemini, and compatible runtimes; it does not select the Worker. `SINGLE_WRITER_PER_TASK: YES`: one Worker owns writes to a worktree/task state. Concurrent runtimes use intentionally isolated worktrees/branches with explicit ownership.

## Project profile

An optional `PROJECT_PROFILE: FORMAL_SECURE | PERSONAL_FAST` changes execution posture only. `FORMAL_SECURE` tightens authority, data, write, publication, and verification handling; missing or ambiguous authorization fails closed. `PERSONAL_FAST` favors focused work but adds no automatic Judge, full suite, evidence bundle, roadmap work, or research-grade sealing beyond the existing route and Judge rules. Neither value changes task class, route, or Worker selection.

`IMPLEMENTATION_DEPTH: NORMAL | ENHANCED` is independent of model, native reasoning effort, Judge, and route. Default to `NORMAL` unless a selection condition applies; use [implementation depth](references/implementation-depth.md) when the Packet supplies a value or a selection condition needs review.

## First output and task class

Before any external tool call, repository read, or filesystem inspection, emit exactly one routing block:

```text
TASK_CLASS: STATE_CHANGING_IMPLEMENTATION | READ_ONLY_COMPLETION_REVIEW | PLANNING_ONLY | PURE_QA
WORKER_ROUTE: FAST | STANDARD | STANDARD_JUDGED | LOOP_JUDGED | NOT_APPLICABLE
JUDGE_MODE: FRESH_CONTEXT | SELF_CHECK_ONLY | NOT_APPLICABLE
```

Use `STATE_CHANGING_IMPLEMENTATION` when source, tests, configuration, Git lifecycle, deployment state, or another external system may change. Use `READ_ONLY_COMPLETION_REVIEW` for claimed-complete work; it is not itself a Judge trigger. Resolve its trigger and mode before dispatch. With a mandatory trigger, use the resolved Judge mode and no Worker route; without fresh-context capability, self-check only and do not claim independent `VERIFIED`. A mandatory trigger with `NOT_APPLICABLE` is `STOP: JUDGE_MODE_CONTRACT_CONFLICT`. Use `PLANNING_ONLY` for plans and `PURE_QA` for questions that run no checks, launch no runtime, create no evidence, and change nothing. Planning and pure QA never dispatch a Judge. When no mandatory Judge trigger applies and mode is `NOT_APPLICABLE`, set `JUDGE_DISPATCH: SUPPRESSED`. A mandatory Judge trigger cannot be suppressed or silently overridden.

If later evidence disproves the class, emit:

```text
TASK_CLASS_RECLASSIFIED
FROM:
TO:
EVIDENCE:
IMPACT_ON_ROUTE:
```

## Context and continuity

After compaction or resume, do only read-only state resolution until the project, task, authority, repository, worktree, status, modified paths, observable history, milestone, blocker, next action, and stop conditions are resolved. Never use resume to clear a blocker, extend authorization, or reopen a stopped mutation. Use [reporting](references/reporting.md#context-and-continuity) for context fields and [task checkpoints](references/task-checkpoint.md) before any authorized checkpoint write.

## Deferred blocked-task queue

Use this exception only when the Planner explicitly classifies Task A's blocker as transient-eligible and Task B is independent with an executable Owner-authorized Packet at a durable locator. Semantic, authorization, safety, database-authority, and permanent blockers never qualify. Load the exact one-task/one-recheck procedure from [task checkpoints](references/task-checkpoint.md#deferred-blocked-task-queue) only when this exception applies.

## Authority and Packet fast path

A complete authoritative Packet supplies the Goal, Owner/authority, allowed scope, acceptance criteria, deliverable format, and forbidden actions or stop conditions. After one bounded live identity check, treat its resolved facts and decisions as inputs: do not re-plan, repeat discovery, or reconstruct the same authority. Use its exact locator for the first Packet-specified content lookup; verify at most one pinned supporting locator. A missing, mismatched, or contradictory required locator stops execution. See [operational gates](references/operational-gates.md#packet-and-authority) for partial Packets, mandatory steps, and edge cases.

## Bounded preflight and write boundary

The front-door kernel resolves only:

1. Owner authority and exact repository, worktree, and compatible base.
2. Write ownership: no overlapping active writer, unresolved overlapping dirty state, or unexplained concurrent mutation.
3. Bounded authorized paths and required capabilities.
4. Explicit behavior and falsifiable acceptance.
5. Whether semantic ambiguity, production/database/deployment, data/security/destructive/recovery risk, or a Judge trigger requires fail-closed handling or escalation.

Any unresolved front-door gate blocks direct FAST entry and follows existing
fail-closed or escalation behavior.
If write ownership remains unresolved, stop before edit, verification, or
ownership mutation.

Use established facts as inputs. Repeat repository, history, worktree, or process discovery only when a live contradiction makes it load-bearing. Once the Goal, boundary, and acceptance are resolved, the Worker owns implementation details. Repair task-caused failures inside scope; do not expand for adjacent unrelated findings. After each acceptance item and required check passes, stop verification.

Required capability uses this tri-state:

```text
CAPABILITY_STATUS: ALLOWED | UNKNOWN | BLOCKED
```

Read the exact target before editing and again before each write. Stop on unexplained changes; do not use cwd as authority, overwrite owner work, or reset, restore, stash, or clean unrelated state. Unrelated out-of-scope dirt or harmless environment differences are report-only. Detailed ownership, capability, authorization, runtime, Git, and evidence procedures live in [operational gates](references/operational-gates.md) and are loaded only when their condition applies.

Do not stage or edit outside the declared scope. Adjacent source, test, or configuration paths are in scope only when demonstrably required by acceptance; a new outcome, unrelated subsystem, or material risk expansion requires a Planner Delta. Do not inspect protected or opaque paths.

A task framed only as “fix the code” lacks a behavior specification. Label unverified facts `[Unknown]`. Required capability is `ALLOWED` only with direct evidence; `UNKNOWN` and `BLOCKED` never qualify. Fail loudly rather than silently degrading or substituting an execution path.

## Route once

Use the Packet route when its gates pass. Otherwise choose exactly one existing route:

- `FAST`: one known low-risk local target, one direct acceptance check, no new behavior, and no Judge trigger.
- `STANDARD`: default for coupled work or one continuous runtime chain.
- `STANDARD_JUDGED`: a Judge trigger applies and Loop is not eligible.
- `LOOP_JUDGED`: every capability and eligibility gate is `YES`, including fixed scope, independent cards and acceptance, isolated writes, main-Worker integration ownership, and real parallel savings. Never fan out automatically.

A Judge trigger requires both a listed category and a material consequence: the change reaches an external consumer, shared runtime, production data, or is not cheaply reversible. Categories are security/authentication/authorization, finance/payment, database or production-data writes, shared-core or cross-runtime changes, real UI/browser/device validation, external side effects, explicit independent verification, or material unknown evidence. A single acceptance failure is not a trigger by itself. If no mandatory Judge applies and mode is `NOT_APPLICABLE`, set `JUDGE_DISPATCH: SUPPRESSED`; do not auto-create or escalate a Judge.

After a passing FAST gate, proceed directly from named inputs → implement → required acceptance → specifically authorized local commit/publication if applicable → handoff. Do not process non-applicable references or repeat discovery. FAST reduces unnecessary work, never safety.

Planning and pure QA have no implementation route. Route changes require a new Owner instruction, an observed authority/scope conflict, or verified missing capability; difficulty, file count, risk, or slow checks alone do not justify a change. For route-order ambiguity, use [flowcharts](references/flowcharts.md).

## Intent, authorization, and surgical execution

Keep every write inside the authorized scope. An executable Owner Packet authorizes reversible local edits within that scope; an outward-facing, irreversible, destructive, production, deployment, or other high-risk action requires direct Owner authorization for the exact action and target. A stop token is final for the current task authority: no mutation, no equivalent command substitution, no metadata workaround, no upstream rewrite, and no retry under a different action class until a new authoritative Owner instruction or valid Continuation Delta. Documentation or task completion is not authorization.

Before a production or deployment mutation, exercise this same tri-state through the exact harness/wrapper/launcher chain that will invoke the mutation; see [production mutation harness preflight](references/operational-gates.md#production-mutation-harness-preflight). Read [operational gates](references/operational-gates.md#authorization-evidence-and-conversation-boundary) for authorization evidence, capability enforcement, Git action tiers, and stop details.

## Execution failures and retries

A failed acceptance is attributed and retried only through a falsifiable hypothesis that reduces uncertainty. Repair task-caused failures within scope; stop when scope, safety, authority, capability, proportionality, or discriminating evidence is exhausted. See [failure modes](references/failure-modes.md) for retry, stop, and recurrence-search details.

## Verification and Judge handoff

Verify by observation; source inspection or command execution alone is not a passing result. `NOT RUN` is never `PASS`. Run the named acceptance and only directly relevant surrounding checks. Stop after acceptance and required checks pass. For Judge triggers, evidence reuse, depth, Fresh Context handoff, and the one-remediation limit, use [Judge handoff](references/judge-handoff.md).

## Lifecycle and filesystem accounting

Keep implementation, publication, post-merge, cleanup, and full-closure statuses distinct. Local completion without publication is not a publication failure. Use [reporting](references/reporting.md) for blocker records, filesystem ledgers, runtime provenance, and lifecycle closure. Consult [operational gates](references/operational-gates.md#git-action-tiers) before any Git lifecycle action except an ordinary local commit on an already-resolved FAST task when `COMMIT_AUTHORIZED: YES`, the exact repository/worktree and write scope are known, and the commit target/history identity is unambiguous. The exception applies only to a non-destructive local commit with no force/fallback, local branch/worktree deletion, remote mutation (including push, Draft/Ready PR, or merge), or authorization conflict; commit directly without loading the detailed Git-action tiers. Consult the reference for every other Git action or whenever authorization, scope, or identity is missing, ambiguous, or conflicted.

## Progressive disclosure and entry points

Load only the directly relevant reference: [examples](references/examples.md) for a missing Packet or task shape; [operational gates](references/operational-gates.md) for authority, authorization, capability, writers, worktrees, production, publication, runtime, or CPU procedures; [task checkpoints](references/task-checkpoint.md) for deferred work and protected-run recovery; [protected run entrypoint](references/task-checkpoint.md#protected-run-entrypoint) when a required protected launch applies; [bounded launcher fallback](references/task-checkpoint.md#bounded-launcher-fallback) for an authorized long-running launch; [post-attempt remote observation](references/task-checkpoint.md#post-attempt-remote-observation) after an ambiguous remote mutation; [Judge handoff](references/judge-handoff.md) for Judge decisions; [failure modes](references/failure-modes.md) for audit or retries; [reporting](references/reporting.md) for handoff and lifecycle details; [implementation depth](references/implementation-depth.md) for a supplied value or selection trigger; [authority sources](references/authority-sources.md) when a load-bearing signal may not be authoritative; [workspace containment](references/workspace-containment.md) before a new worktree or sibling workspace; [memory boundary](references/memory-boundary.md) before memory or checkpoint files; and [test falsifiability](references/test-falsifiability.md) before citing changed checks. Use one matching domain reference before Step 2 for non-coding work. Use property-based verification, regression bisection, diff coverage, or generic ranking only when their named condition applies.

Preserve `/fable-method <task>`, `/fable-method plan <task>`, `/fable-method audit`, `/fable-method report`, `$fable-method`, and the sibling `fable-judge` entry point. `plan` stops before mutation; `audit` is read-only; direct completion review uses `fable-judge`, never a Worker route.

## Compact flow when no Packet exists

Use the bounded no-Packet flow in [worked examples](references/examples.md#compact-flow-without-a-packet); do not turn it into a second plan when an authoritative Packet already resolves the work.

## Final artifact gate

Every changed path must be authorized and every `PASS` tied to observed evidence. Do not report terminal success while a mandatory criterion is `NOT RUN`, unresolved, or contradicted. Distinguish `NOT RUN`, `BLOCKED`, and `UNKNOWN`; never claim publication, runtime success, or cleanup without observing it. See [outcome-first reporting](references/reporting.md#final-artifact-gate) and [Judge handoff](references/judge-handoff.md).
