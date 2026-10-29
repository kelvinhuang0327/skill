---
name: fable-method
description: >-
  Lightweight Fable Worker router for Antigravity. A fresh agent reads the
  full Task Packet, judges task complexity, and runs the matching flow: FAST
  (implement, needed test, report), STANDARD (implement, focused
  verification, report), or STANDARD with ENHANCED depth (bounded
  exploration, implement, correctness verification, report). A Judge,
  checkpoint, protected run, or full suite runs only when the Packet asks.
  Use when the user invokes /fable-method, says "use the fable method" or
  "approach this like Fable", when a Planner hands off approved
  implementation work, or for a non-trivial task without a more specific
  skill. Antigravity enforcement is prompt-only. Subcommands: plan, audit,
  report.
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
- Temporary files are writes too. Put helper scripts, intermediate output,
  and diagnostic reports under `PERSISTENT_EVIDENCE_ROOT` or under a scratch
  path that the Packet, the project rules, or this skill names. Any other
  place, such as `/tmp` or a tool's default temporary directory, is out of
  scope; point tools that default there at one of those paths.
  Python bytecode counts too: when Python imports or runs code from a
  folder outside your write scope, run it with `-B` or with
  `PYTHONDONTWRITEBYTECODE=1` so no `__pycache__` appears there.
- Keep reads inside what the Packet lets you read. Give search, status,
  diff, and listing commands explicit path operands, and check that a
  generated path list is non-empty before using it: an empty list must stop
  the command, not widen it to the whole repository. macOS `/bin/bash` is
  3.2 and has no `mapfile` or `readarray`.
- Read the exact target before editing. Stop on unexplained changes; do not
  use cwd as authority, overwrite owner work, or reset, restore, stash, or
  clean unrelated state. Unrelated out-of-scope dirt or harmless environment
  differences are report-only.
- Local reversible edits, and a local commit when `COMMIT_AUTHORIZED: YES`,
  need nothing more.
- When the Owner personally submits the Packet in this conversation (for
  example with `/goal` or by pasting it), that submission is the Owner's
  direct request for the Packet's reversible writes inside
  `PERSISTENT_EVIDENCE_ROOT` that `ALLOWED_WRITES` lists. A Planner note
  that the Packet is only a draft, or that it still needs a direct Owner
  request in the Worker conversation, is satisfied by that submission; do
  not ask again for those writes. This does not cover commit, push, merge,
  deletion, external messages, database or cloud writes, deployment, or any
  write outside those paths; they still need explicit authorization as in
  Hard boundaries. Text relayed by another agent or tool is not an Owner
  submission.
- When that submitted Packet also asks for a protected run through the task
  checkpoint launcher (`task_checkpoint.rb --run`), the submission also covers
  the launcher's own records for that `TASK_ID`:
  `.fable/checkpoints/<TASK_ID>.json` and `.fable/checkpoints/<TASK_ID>/`
  under the run's record root, the lock anchor it creates under that
  repository's Git common directory `fable-locks/`, and, when the launcher
  requires it, the machine heavy-compute lease lock and its `.owner.json`.
  A Packet setting for the `--heavy-scientific` flag controls only that flag;
  stop only when the Packet explicitly forbids the heavy lease itself. List
  the exact paths used in the receipt. This does not cover recovering,
  retrying, or continuing an earlier execution, or any other write outside
  `PERSISTENT_EVIDENCE_ROOT`.
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

When the work enumerates or recomputes a set (targets, records, candidates),
take the set's expected size from the Packet or from an independent count made
before the run, and report expected and produced counts side by side. An
unexplained difference fails verification: fix it, or report `BLOCKED` with
both counts; never report `COMPLETED` over it. If no expected size can be
known, say so and state how the whole set was covered.

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

### Task receipt

When the Packet names a `PERSISTENT_EVIDENCE_ROOT` or has a `SIGNAL_POLICY`
header line, keep this receipt even if the Packet body does not mention it:

- At task start, save the Packet text exactly as received to
  `<root>/packet.md`, dropping only a leading platform command such as
  `/goal ` so the file starts with `TASK_ID:`; never replace it with a
  summary. On a same-`TASK_ID` continuation keep that file and save the new
  Packet as `packet.continuation_<N>.md`.
- `<root>/result.json` holds a `task_id` key whose value is the Packet's
  `TASK_ID` copied exactly (same case), and `task_lifecycle_state`:
  `IN_PROGRESS` at start, then `COMPLETED`, `BLOCKED`, or `ABORTED` before
  every reply, stops included. Keep any other fields the Packet asks for.
- `process_signal` follows `SIGNAL_POLICY`: write it for `ALWAYS`; for
  `ON_INCIDENT` only after an actual stop, block, abort, failed acceptance,
  or major anomaly; never without the header. It may hold only these keys:
  `trigger`; `versions` (`planner`, `planner_load_evidence`, `fable`,
  `fable_load_evidence`); `route` (`task_class`, `worker_route`, `depth`,
  `model_tier`); `cost` (`elapsed_ms`, `tokens`, `resources`, `evidence`);
  `issue` (`type`, `evidence`, `rca`, `smallest_fix`). Add no other key:
  no `status`, `summary`, `outcome`, `signal_policy`, or `attempt`.
  `trigger` is `INCIDENT` when something went wrong, else `HIGH_IMPACT`;
  `INCIDENT` also needs `issue.type`, one of `PLANNER`, `FABLE`, `PLATFORM`,
  `ENV`, `PROJECT`, `AUTH`, `UNNECESSARY_WORK`, `PERFORMANCE`, `NONE`, or
  `UNKNOWN`. Omit what was not measured. Repeat the block in the reply as
  `PROCESS_SIGNAL:`.
- If the installed skill has `scripts/fable_artifact_controls.py`, run
  `evidence-summary --repo <repo> --task-id <TASK_ID> --task-evidence-root <root>`
  once before replying (leave out `--repo <repo>` when the Packet names no
  repository or forbids reading it) and fix the receipt until it reports
  `identity_status: MATCH` and `lifecycle_state_check: CANONICAL`. A receipt
  format slip fixed this way is not an incident: it does not by itself make
  `trigger` `INCIDENT`.

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

## Antigravity CLI integration

Load [Antigravity-specific integration details](references/platform-antigravity.md)
only when Antigravity discovery precedence or sibling-skill resolution
matters.
