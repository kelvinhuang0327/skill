---
name: fable-method
description: Lightweight Fable Worker router. A fresh agent reads the full Task Packet, judges task complexity, and runs the matching flow (FAST, STANDARD, or STANDARD with ENHANCED depth), verifying only what the task needs. Use when the user invokes /fable-method, supplies a Planner Packet, or requests a non-trivial state-changing task without a more specific skill. Do not enter the implementation flow for pure questions, planning-only requests, or read-only completion reviews; route reviews to fable-judge.
---

# The Fable Method

Fable is a complexity router: judge how hard the task is, run the matching
flow, and report what was observed. It guides behavior; it does not
mechanically enforce permissions.

```text
read the whole Packet → classify complexity → run that flow → report
```

## Fresh agent

Every Worker is a fresh agent. The Task Packet, plus the project rules it
names, is the complete input: read it fully, then execute. Treat the facts and
decisions it states as given.

By default, do not search earlier agents' transcripts or history, re-check
another agent's identity, session, or continuity, rebuild the Planner's
analysis, write checkpoints, deferred queues, ledgers, or duplicate receipts,
take protected-run slots or migrate locks, dispatch a Judge, or run a full
suite. Check a stated fact against live state only when acting on it would
otherwise fail.

## Hard boundaries

These are the only default stops.

1. No push, merge, deletion, external message, or database or cloud write
   without explicit Owner authorization in the Packet: an outward-facing,
   irreversible, destructive, production, deployment, or other high-risk
   action requires direct Owner authorization for the exact action and
   target. The Owner's words in the Packet or in this conversation count.
   Without them, report `PENDING: <action> - awaiting your authorization`
   and finish the rest.
2. Never overwrite, reset, stash, clean, or discard someone else's
   uncommitted or active work.
3. `NOT RUN` is never `PASS`, and neither is `UNKNOWN`.
4. Stop only on material conflicts involving explicit authorization,
   required inputs, or risk of overwriting active work. Everything else is
   noted in the report, not a stop.

## First output and task class

Before any tool call, emit one routing block. Copy the Packet's values when it
supplies them.

```text
TASK_CLASS: STATE_CHANGING_IMPLEMENTATION | READ_ONLY_COMPLETION_REVIEW | PLANNING_ONLY | PURE_QA
WORKER_ROUTE: FAST | STANDARD | STANDARD_JUDGED | LOOP_JUDGED | NOT_APPLICABLE
IMPLEMENTATION_DEPTH: NORMAL | ENHANCED
JUDGE_MODE: FRESH_CONTEXT | SELF_CHECK_ONLY | NOT_APPLICABLE
```

`IMPLEMENTATION_DEPTH` applies only to `STATE_CHANGING_IMPLEMENTATION`.
`PLANNING_ONLY` and `PURE_QA` change nothing and have no route. Planning and
pure QA never dispatch a Judge. Use `READ_ONLY_COMPLETION_REVIEW` for
claimed-complete work; it is not itself a Judge trigger. Resolve its trigger
and mode before dispatch. With a mandatory trigger, use the resolved Judge
mode and no Worker route; without fresh-context capability, self-check only
and do not claim independent `VERIFIED`. When no mandatory Judge trigger
applies and mode is `NOT_APPLICABLE`, set `JUDGE_DISPATCH: SUPPRESSED`. A
mandatory Judge trigger cannot be suppressed or silently overridden.

If the work turns out simpler or harder than classified, switch flow and say
so in one line (`TASK_CLASS_RECLASSIFIED` or `ROUTE: <old> -> <new>`, with the
evidence).

## Route once

Use the Packet's route and depth when it supplies them. Otherwise judge
complexity and choose exactly one flow:

| Complexity | `WORKER_ROUTE` | `IMPLEMENTATION_DEPTH` | Flow |
|---|---|---|---|
| Simple | `FAST` | `NORMAL` | implement → the test that proves it → report |
| Normal | `STANDARD` | `NORMAL` | implement → focused verification → report |
| Hard or research | `STANDARD` | `ENHANCED` | bounded exploration → implement → correctness verification → report |

- `FAST`: one known low-risk local target, one direct acceptance check, no new
  behavior, and no Judge trigger.
- `STANDARD` with `NORMAL` depth: the default, for coupled work or one
  continuous runtime chain.
- `STANDARD` with `ENHANCED` depth: an
  [implementation-depth](references/implementation-depth.md) trigger holds,
  for example the right change is not known yet and candidates have to be
  explored, or a cheap localization leaves several root causes with different
  fixes.
- `STANDARD_JUDGED` and `LOOP_JUDGED`: the same flows plus a Judge, only when
  requested. `LOOP_JUDGED` also needs independent cards with isolated writes.
  Never fan out automatically.

After a passing FAST gate, proceed directly from named inputs → implement → required
acceptance → specifically authorized local commit/publication if applicable → handoff.
Do not process non-applicable references or repeat discovery. Once the cause
and authorized fix are known on a resolved FAST task, implement directly
without a second planning phase.

Bounded exploration is time-boxed and aimed at choosing between named
candidates; stop exploring once the choice is made. Correctness verification
names the invariants the change could break and runs the checks that would
catch a violation, preferring checks shown to fail on the unfixed code.

A mandatory Judge trigger is an explicit request: the Packet routes
`STANDARD_JUDGED` or `LOOP_JUDGED`, sets `JUDGE_MODE: FRESH_CONTEXT`, or the
Owner asks for a Judge. Fable never creates a Judge from risk categories or
file counts. A single acceptance failure is not a trigger by itself.

## Execution

- Keep writes inside the Packet's allowed scope. Adjacent files count only
  when acceptance needs them; a new outcome needs a Planner Delta.
- Read the exact target before editing. Stop on unexplained changes; do not
  use cwd as authority, overwrite owner work, or reset, restore, stash, or
  clean unrelated state. Unrelated out-of-scope dirt or harmless environment
  differences are report-only.
- Local reversible edits, and a local commit when `COMMIT_AUTHORIZED: YES`,
  need nothing more.
- Retry a failure only with a hypothesis the next attempt tests; stop when
  scope, safety, authority, capability, proportionality, or discriminating
  evidence is exhausted. Then report the blocker; see
  [failure modes](references/failure-modes.md).
- A stop is final for the current task authority: no mutation, no equivalent
  command substitution, no metadata workaround, no upstream rewrite, and no
  retry under a different action class until a new authoritative Owner
  instruction or valid Continuation Delta.
- Write only the deliverables the Packet names, such as its `result.json`;
  add no extra reports, logs, or receipts.

## Verification

Run the named acceptance and only directly relevant surrounding checks. That
means the one test for `FAST`, focused checks on the changed behavior for
`STANDARD`, and the invariant checks for `ENHANCED`. When the declared acceptance surface
includes the directly bound consumer of a changed runtime component, run the
consumer's focused smoke alongside the focused check for that component;
checking component identity alone does not verify its consumer API.

A full suite, Judge, evidence pass, or repeated review runs only when the
Packet's acceptance names it. Once acceptance passes, stop verifying.
Verification is an observation, not a command run or "should work". Reuse
evidence from the exact same tree instead of re-running it, and label it
`REUSED_EXACT_TREE_EVIDENCE`.

## Report

```text
STATUS: <what happened, in plain language>
ROUTE: <WORKER_ROUTE> / <IMPLEMENTATION_DEPTH> (<DEPTH_SOURCE>)
CHANGED: <authorized files/surfaces touched>
VERIFIED: <commands/observations and real output>
NOT RUN / BLOCKED: <checks not run and why>
RISKS: <remaining unknowns or caveats>
```

`DEPTH_SOURCE` is `PLANNER_SUPPLIED` when the Packet set the depth, else
`SKILL_FALLBACK`. Add `ENHANCED_EVIDENCE` for `ENHANCED` work and a `PENDING:`
line for anything boundary 1 held back. Never claim publication, deployment,
runtime success, or cleanup that was not observed. Add other fields only when
the Packet names them; see [reporting](references/reporting.md).

## Explicit Packet triggers

Ordinary tasks need none of these. Load a procedure only when the Packet or
the Owner explicitly asks for it, or the task actually performs the action:

| Trigger | Procedure |
|---|---|
| A Judge request (see Route once) | [Judge handoff](references/judge-handoff.md) |
| `LOOP_JUDGED` execution cards | sibling `fable-loop` |
| Push, PR, merge, or branch or worktree deletion | [Git action tiers](references/operational-gates.md#git-action-tiers) |
| A production or deployment mutation | [production mutation preflight](references/operational-gates.md#production-mutation-harness-preflight) |
| A Fable live activation | [live-activation handoff](references/operational-gates.md#exact-pinned-live-activation-compact-handoff) |
| `PROJECT_PROFILE: FORMAL_SECURE` | the strict gates in [operational gates](references/operational-gates.md) and the full evidence handoff in [reporting](references/reporting.md) |
| `WORKTREE_CLOSE_POLICY: AUTO_CLOSE_AFTER_MERGE` | [auto worktree reclaim](references/operational-gates.md#auto-worktree-reclaim) |
| CPU-heavy work on the shared workstation | [resource budget](references/operational-gates.md#shared-workstation-resource-budget) |
| A checkpoint or deferred blocked-task queue | [task checkpoints](references/task-checkpoint.md) |
| A protected or long-running launch | [protected run entrypoint](references/task-checkpoint.md#protected-run-entrypoint), [bounded launcher fallback](references/task-checkpoint.md#bounded-launcher-fallback) |
| Continuing or recovering a prior long-running execution | [long-running execution recovery](references/task-checkpoint.md#long-running-execution-recovery) |
| An ambiguous remote mutation result | [post-attempt remote observation](references/task-checkpoint.md#post-attempt-remote-observation) |

Long-running compute exclusion — the heavy compute slot, protected runs, and
device/inode locks — belongs to the B-lane launcher, not to ordinary Fable
work. Helper scripts such as `fable-method/scripts/task_checkpoint.rb` are
optional conveniences; their absence alone never blocks a task. When a
triggered procedure needs a missing helper, follow that procedure's own
fallback.

Other references load only when their condition applies:
[examples](references/examples.md) for a missing Packet,
[implementation depth](references/implementation-depth.md),
[authority sources](references/authority-sources.md),
[test falsifiability](references/test-falsifiability.md),
[property-based verification](references/property-based-verification.md),
[regression bisection](references/regression-bisection.md),
[diff coverage](references/diff-coverage.md),
[generic ranking](references/generic-ranking.md),
[workspace containment](references/workspace-containment.md) before a new
worktree or sibling workspace, [memory boundary](references/memory-boundary.md)
before memory writes, [flowcharts](references/flowcharts.md), and one matching
domain reference for non-coding work.

## Entry points

Preserve `/fable-method <task>`, `/fable-method plan <task>`,
`/fable-method audit`, `/fable-method report`, `$fable-method`, and the sibling
`fable-judge` entry point. `plan` stops before mutation; `audit` is read-only
and uses [failure modes](references/failure-modes.md); direct completion
review uses `fable-judge`, never a Worker route. Without a Packet, follow the
[compact flow](references/examples.md#compact-flow-without-a-packet).


## Artifact budgets and worktree lifecycle

At task start, classify the requested workspace as WORKTREE_LIFECYCLE: PERSISTENT_RESEARCH or TEMPORARY_TASK. If the Packet does not decide, use PERSISTENT_RESEARCH. A temporary task worktree may be auto-closed only when the Owner authorized that exact lifecycle before creation and the task records AUTO_CLOSE_AUTHORIZED: YES. This does not authorize cleanup of pre-existing worktrees.

Use create-worktree to prepare and create the exact absent path, branch, base ref, and task ID. For a temporary task, pass --auto-close-authorized only when the Owner approved that lifecycle before creation; the helper writes its one-use record in Git common metadata immediately before creating the worktree. Do not manually create or adopt a different path. An existing worktree has no matching preauthorization record and cannot pass the close helper.

For this local Codex installation, use scripts/fable_artifact_controls.py for disk admission, temporary Observer/debug JSONL, and eligible temporary-worktree closure. At each task start, classify its storage use and run space-check against the filesystem where the work or outputs will be written; run it again when the task ends. Less than 15 GiB free reports a warning. Less than 8 GiB blocks creation of a research worktree and launch of a high-disk task. Ordinary work may continue with a warning. Read-only diagnostics and Owner-authorized space-recovery work are admitted below either threshold, while still reporting low space. The check is on demand and has no background service:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py space-check --path <existing-output-volume> --operation <ordinary|research-worktree|high-disk-task|read-only-diagnostic|authorized-reclaim> --phase <before|after>

Use create-worktree for every new research worktree so the capacity block runs immediately before git worktree add. Select persistent-research by default. Select temporary-task only when the task has that lifecycle; include --auto-close-authorized or --close-policy AUTO_CLOSE_AFTER_MERGE only when the Owner approved auto-close before the exact worktree is created:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py create-worktree --repo <canonical-repository> --worktree <exact-absent-path> --base <durable-base-ref> --branch <task-branch> --task-id <task-id> --lifecycle <persistent-research|temporary-task> [--auto-close-authorized] [--close-policy <PRESERVE|AUTO_CLOSE_AFTER_MERGE>] [--merge-target-ref <ref>] [--persistent-evidence-root <path>]

Launch high-disk tasks through run-gated so a low-space result prevents the command from starting and the available-space check runs again after it exits:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py run-gated --path <existing-output-volume> --operation <high-disk-task|ordinary|read-only-diagnostic|authorized-reclaim> -- <command and arguments>

Before launching or retrying pytest with an explicit --basetemp, validate the scratch path. It must be outside the repository and every nested Git worktree, and must not contain protected path components used by the data boundary checks. If blocked, select a neutral external scratch path and validate it before launching pytest:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py validate-test-scratch --repo <canonical-repository> --path <pytest-basetemp>

After a failed or blocked execution, inspect the existing records before retrying. evidence-summary reads execution JSON and its durable captures, emits source paths and SHA-256 values, identifies repeated exact output and evidence-backed problem candidates, and writes no summary file. Use candidate findings to propose the smallest change and a verification method; never automatically rewrite Skill rules from a single failure or an unverified aggregate. Treat missing retry, Judge, task-completion, and lifecycle fields as unknown rather than estimating them:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py evidence-summary --repo <canonical-repository> [--task-id <task-id>]

Only temporary, non-formal Observer/debug JSONL belongs in the helper's .fable/temporary/<task-id>/logs/ area. Append one JSON object per event with append-log:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py append-log --repo <canonical-repository> --task-id <task-id> --record-json '<one JSON object>'

It rotates at 1 MiB and caps all active and rotated log bytes at 10 MiB per task. Once the next event would exceed that budget, the helper reports its byte count and SHA-256 and drops the event. The helper does not trim formal captures, receipts, certificates, acceptance evidence, or files named by SHA256SUMS. Keep one full test-output source; summaries and other records should store its path, byte count, and SHA-256. The protected task_checkpoint.rb --run capture remains the canonical complete stdout/stderr record, and its execution JSON points to that capture.

For an authorized temporary worktree close, first save a manifest outside the worktree listing every required formal result by absolute path and SHA-256. Confirm the task is complete and all agents, project runtimes, and task-relevant recurring schedulers are finished. The helper independently checks the repository execution-claim registry, every task execution record and its durable capture, launchd and cron bindings, and open files. It checks these again after five seconds, while re-reading Git state. The worktree must have no tracked changes, untracked files, or ignored files, and its exact HEAD must be merged into the durable base ref (supporting both normal merges and verified squash merges with provenance/tree/patch evidence). Under FABLE_AUTO_WORKTREE_RECLAIM_V1, when a temporary worktree was preauthorized with AUTO_CLOSE_AFTER_MERGE, run close-temporary-worktree with the repository, worktree, base, evidence manifest, and task ID; the helper automatically verifies all safety and quiescence gates without requiring repeated manual authorization flags. Immediately before removal, the helper atomically claims the exact preauthorization record and rechecks its bytes; a missing or changed record blocks removal while preserving the worktree. It then invokes only ordinary git worktree remove with core.fsmonitor=false. It never deletes the branch or uses a force fallback. If any gate is unknown or fails, leave the worktree in place and report the specific blocker.

## Codex Runtime Materialization

- **Source-owned Script:** `fable-method/scripts/task_checkpoint.rb` is pinned to Git blob `0851b2fa877cb3f9692eb0ef84b543caa9088ccf` from commit `32cbf4e85222ccbb40ec8cd5b5dfc6568e5138dd`.
- **Materialized Runtime Asset:** the launcher is written byte-for-byte as `scripts/task_checkpoint.rb` with the manifest-declared executable mode.

- **Required Companion:** `scripts/paused_run_continuation.rb` is loaded only for `--continue-paused-run`, pinned to Git blob `91592132733be6b697bdbef667b97d9065c829c1` from commit `e6f8581cdabe21f3683b8a5d8effb6855d98a041`.
- **Local Artifact Controls:** `scripts/fable_artifact_controls.py` is a standard-library helper copied from `fable-method/scripts/fable_artifact_controls.py` with SHA-256 `d730c65cf476f031ab9e1ea2d242ddcbcd0d9b6ed69794e637f340da8eb54df4`.
- **Installed Runtime:** the Codex installation path in `platforms.yaml` is deployment metadata only; this generator does not write there.
- **Live Activation:** NOT RUN. Activation is a separate operation outside this source candidate.

A Codex bundle is Runtime Distribution Ready only when every manifest-declared asset matches its declared SHA-256 and mode; pinned assets also match their Git blob. The platform generator's `--check` verifies that state. A documentation-only bundle is not Runtime Distribution Ready.
