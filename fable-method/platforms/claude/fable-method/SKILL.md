---
name: fable-method
description: >-
  Lightweight Fable Worker router for Claude Code. A fresh agent reads the
  full Task Packet, judges task complexity, and runs the matching flow: FAST
  (implement, needed test, report), STANDARD (implement, focused
  verification, report), or STANDARD with ENHANCED depth (bounded
  exploration, implement, correctness verification, report). A Judge,
  checkpoint, protected run, or full suite runs only when the Packet asks.
  Use when the user invokes /fable-method, says "use the fable method" or
  "approach this like Fable", when a Planner hands off approved
  implementation work, or for a non-trivial task without a more specific
  skill. Claude enforcement is prompt-only. Subcommands: plan, audit, report.
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

Emit one routing block; copy the Packet's values when it supplies them. Its
position relative to loading this Skill, reading the Packet, or reading
project rules does not matter and is never an incident.
When the Packet arrives only as an attached or pasted-text file (for example
a Codex `/goal` attachment), read the complete Task Packet; if the platform
requires `get_goal`, call it. Read the Packet-named project rules and any
needed Skill references before acting.

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

## Pre-execution TASK_ID guard

Before executing a Packet classified as `STATE_CHANGING_IMPLEMENTATION`,
check whether it requires a canonical Fable receipt: it names
`PERSISTENT_EVIDENCE_ROOT` or includes a `SIGNAL_POLICY` header. If either
receipt trigger is present, require a non-empty, explicit `TASK_ID` header
before execution. When `TASK_ID` is missing or blank, stop before execution
and before any mutation. Do not infer or synthesize an ID, and do not create
or claim the canonical receipt. Report `BLOCKED_MISSING_TASK_ID` and request
a corrected Packet with an explicit `TASK_ID`.

This guard applies only to state-changing Packets that require a canonical
Fable receipt. Keep pure questions (`PURE_QA`), planning-only work
(`PLANNING_ONLY`), read-only completion reviews
(`READ_ONLY_COMPLETION_REVIEW`), and the compact flow without a Packet usable.

## Execution

- Keep writes inside the Packet's allowed scope. Adjacent files count only
  when acceptance needs them; a new outcome needs a Planner Delta.
- Temporary files are writes too. Put helper scripts, intermediate output,
  and diagnostic reports under `PERSISTENT_EVIDENCE_ROOT` or under a scratch
  path that the Packet, the project rules, or this skill names. Any other
  place, such as `/tmp` or a tool's default temporary directory, is out of
  scope; point tools that default there at one of those paths.
- When the repository or Packet specifies a Python interpreter, use that exact
  interpreter for every Python command, including one-line helpers and receipt
  utilities. For disk-space checks, prefer shell `df`; do not invoke system
  `python` or `python3` as a shortcut.
  Python bytecode counts too: when Python imports or runs code from a
  folder outside your write scope, run it with `-B` or with
  `PYTHONDONTWRITEBYTECODE=1` so no `__pycache__` appears there.
- Keep reads inside what the Packet lets you read. Give search, status,
  diff, and listing commands explicit path operands, and check that a
  generated path list is non-empty before using it: an empty list must stop
  the command, not widen it to the whole repository. macOS `/bin/bash` is
  3.2 and has no `mapfile` or `readarray`.
- Before long historical strategy/backfill work maps strategy semantics or
  starts an expensive replay, resolve each Packet-named source pin (exact
  commit, blob, or path at revision) against only the permitted repository
  refs/object database. For Git, verify the exact commit/object and path (for
  example, `git cat-file -e <commit>^{commit}`,
  `git cat-file -e <commit>:<path>`, or
  `git cat-file -e <blob>^{blob}`) and record the resolved commit/path/blob.
  If the exact source is unavailable, leave that strategy identity unresolved
  and its source semantics `UNKNOWN`; do not map or replay that identity. A
  strategy name, ticket count, present-day adapter, or partial golden output
  cannot substitute for source semantics.
- When an exact source pin is absent locally, probe a remote only if the
  Packet explicitly allows remote source lookup and pins one exact commit or
  blob. Fetch only that object into an isolated disposable bare probe (for
  Git, `git fetch --no-tags --no-write-fetch-head <allowed-remote>
  <exact-object>`); do not fetch tags, branches, all refs, or history, and do
  not change the destination project's refs. Record exact fetch result
  separately from local presence; keep credential-bearing URLs and command
  output out of evidence. If that exact fetch is unavailable, leave identity
  unresolved and source semantics `UNKNOWN`; never use a nearby ref or proxy
  evidence.
- Read the exact target before editing. Stop on unexplained changes; do not
  use cwd as authority, overwrite owner work, or reset, restore, stash, or
  clean unrelated state. Unrelated out-of-scope dirt or harmless environment
  differences are report-only.
- When a task requires its final snapshot to prove a checkout's `HEAD`, index,
  or working tree stayed unchanged, reserve that exact checkout from task start
  through the final snapshot. The Owner and every agent must complete any
  `commit`, `pull`, `switch`/`checkout`, staging, or other operation that would
  change those states before the task starts; if such an operation becomes
  necessary during the reservation, defer it until after the final snapshot or
  run it in a different checkout.
- Derive execution receipts from one structured source. Keep a pre-run
  fingerprint's exact canonical payload and every hashed input (branch,
  HEAD/tree, status, per-file path/status/mode/SHA) beside the digest, computed
  from that saved payload. Create generated path values once and reuse them
  for validation, command argv, and the receipt. A reusable artifact that
  calls mutable state `current` must include `observed_at` and source identity;
  after relevant parallel changes, re-read before relying on it or label the
  snapshot historical.
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
- Before showing tool output from a structured receipt, determine whether it
  contains per-run or payload-bearing fields. Never `cat` or dump the full
  document or rows; parse only an explicit field allowlist and report
  aggregated counts, statuses, or hashes. If a safe allowlist cannot be
  established, stop and mark the task `BLOCKED` without reproducing values.
- Before any evidence-summary identity query, finish writing a complete,
  readable `<root>/result.json`; do not claim canonical result validation
  while that file is missing.
- When a Packet requires `task_folder_bytes`, finish writing every required
  task receipt first. Then compute the task-folder total and files over 100 MB;
  update the required size fields in `result.json` and `final_reply.md`,
  recalculating until both records match the final files.
- If the installed skill has `scripts/fable_artifact_controls.py` and the
  Packet permits repository-wide checkpoint and history inventory, run
  `evidence-summary --repo <repo> --task-id <TASK_ID> --task-evidence-root <root>`
  once before replying. Omit `--repo <repo>` when the Packet names no
  repository or forbids repository reads.
- When the Packet permits task-local receipt reads but forbids repository-wide
  checkpoint enumeration or historical Task inventory, use the existing
  task-local mode with no `--repo`:
  `evidence-summary --task-id <exact Task ID> --task-evidence-root <exact Task root>`.
  A `--task-id` filter in repository mode does not prevent its earlier global
  enumeration. Record `execution_history` as
  `NOT_READ_WITHOUT_REPOSITORY`. This mode validates receipt identity,
  lifecycle, and signal fields only; it does not independently validate all
  acceptance criteria or actual execution history.
- If a continuation receipt is in a differently named root, add
  `--continuation-id <exact directory name>`. The root name and the result's
  `continuation_id` must match it, while the Packet and result still retain the
  original `task_id`; this does not change the checks' evidentiary scope.
- Fix the receipt until it reports `identity_status: MATCH`,
  `lifecycle_state_check: CANONICAL`, and a `signal.status` other than
  `INVALID`. A receipt format slip fixed this way is not an incident: it does
  not by itself make `trigger` `INCIDENT`.
- Lint, format, or test failures found and fixed during implementation, before
  final verification, are normal development and not incidents. Use
  `INCIDENT` only for a stop, block, abort, failed final acceptance, or a
  problem that remains or changed a result.

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

The machine-wide heavy-compute lease is paused. The launcher does not take the heavy slot or check other tasks'
heavy processes unless `FABLE_HEAVY_SCIENTIFIC_LEASE=on` is set in its environment. Do not wait for another
task's computation before starting your own; check only that no process writes to your own task's surfaces.

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

## Claude Code integration

Load [Claude-specific integration details](references/platform-claude.md) only
when Claude hook behavior or sibling-skill resolution matters.
