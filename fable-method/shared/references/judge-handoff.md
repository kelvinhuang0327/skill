# Judge handoff contract

## Contents

- [When the Judge gate fires](#when-the-judge-gate-fires)
- [Depth and evidence reuse](#depth-and-evidence-reuse)
- [Depth reconciliation](#depth-reconciliation)
- [Remediation limit](#remediation-limit)
- [Durable terminal capture](#durable-terminal-capture)
- [Handoff payload](#handoff-payload)
- [Verdict and lifecycle boundaries](#verdict-and-lifecycle-boundaries)

This reference describes the Worker-to-Judge boundary. The Judge is an
independent, read-only verifier; a Worker report is never an independent
verdict.

## When the Judge gate fires

The Judge trigger has one definition, in `SKILL.md` "Route once": a listed
category and material consequence together. Do not restate or widen that list
here. `LOOP_JUDGED` is judged by route definition, not by category.

`READ_ONLY_COMPLETION_REVIEW` goes directly to `fable-judge` and has no Worker
route. A Worker with no fresh-context capability may self-check only, must mark
`JUDGE_MODE: SELF_CHECK_ONLY`, and must not claim independent `VERIFIED` for a
Judge-gated task.

## Depth and evidence reuse

This section is the canonical Judge-depth contract: the single normative source
for `JUDGE_DEPTH` on every platform. A Planner, a Worker, and a platform Judge
skill each derive depth from the triggers below and must not maintain a
divergent list. It is distinct from the Judge trigger above, which decides only
whether a Judge runs at all; firing that gate never by itself selects `FULL`.

Choose exactly one depth:

- `BOUNDED`: initial focused independent reproduction; default.
- `FULL`: at least one named trigger below fires.
- `DELTA`: the one permitted remediation's finding, diff, tests, and impacted
  regression slice.

Subject-matter triggers describe what the change touches:

- security, authentication, or authorization code;
- database migration or production-data write;
- payment or another irreversible external side effect;
- deployment or cutover;
- shared-core change whose risk focused verification cannot isolate;
- final-suite evidence missing when due, incomplete in command, exit status,
  output summary, environment, or final-tree identity, internally
  contradictory, or not reproducible;
- the Planner or Owner explicitly asked for full independent reproduction.

Workload-shape triggers describe what the acceptance criteria demand, and fire
when the requested scope materially exercises:

- crash-safety or kill-and-resume reproduction;
- fault injection;
- concurrency or race-condition execution;
- transaction rollback or recovery validation;
- durability, resumability, or idempotency after interrupted execution;
- orphan-prevention validation;
- security or authorization adversarial testing.

The Planner authors acceptance criteria, so it observes a workload-shape
trigger first and emits `JUDGE_DEPTH: FULL` at synthesis time rather than
declaring `BOUNDED` and leaving the correction to the Worker or the Judge. An
exhaustive review of all changed tests is a cost signal, not by itself a `FULL`
trigger.

A supplied depth never lowers a fired trigger. A Packet declaring `BOUNDED`
while its acceptance fires a trigger above is a contract error: name the
mislabel and verify at `FULL`. Never run a `FULL` workload under a `BOUNDED`
label, and never drop items to make the label true. Escalation from outside is
legitimate; silent de-escalation is not.

Reuse evidence only when command, environment, HEAD, and tree are identical and
the evidence was not invalidated. Run the complete local suite at most once per
final tree. Under ordinary judged timing, run focused acceptance and the
impacted regression slice before the initial bounded Judge; run the complete
suite after the Judge or permitted remediation. A load-bearing edit after a
full suite invalidates it. `FULL` does not mean rerunning an already-valid
same-final-tree full suite a second time.

Before handoff, state:

```text
JUDGE_DEPTH: BOUNDED | FULL | DELTA
JUDGE_DEPTH_REASON:
```

`JUDGE_DEPTH_REASON` names the actual trigger, evidence gap, or remediation
state. "High quality", "complex task", "important", "many files", "safer", and
"thorough" are adjectives, not triggers.

## Depth reconciliation

A Packet may declare its own expected Judge depth; this section states how
that declared depth reconciles with the canonical required depth above, so an
under-specified Packet escalates exactly once instead of repeating an
unexplained `STOP`. Both depths, and the reconciliation between them, are
derived fresh from the current Packet, its acceptance criteria, and the
exact-tree evidence already gathered — never from a separate registry, and
never persisted past the current task's evidence state.

Before the first Judge handoff attempt, state:

```text
PACKET_JUDGE_DEPTH: NOT_APPLICABLE | BOUNDED | FULL | DELTA
CANONICAL_REQUIRED_JUDGE_DEPTH: NOT_APPLICABLE | BOUNDED | FULL | DELTA
JUDGE_DEPTH_RECONCILIATION: MATCH | ESCALATION_REQUIRED
MISSING_JUDGE_EVIDENCE: <exact evidence list | NONE>
IMPLEMENTATION_MUTATION_REQUIRED: YES | NO
```

`PACKET_JUDGE_DEPTH` is `NOT_APPLICABLE` only when the Packet names no depth.
`CANONICAL_REQUIRED_JUDGE_DEPTH` is computed from [Depth and evidence
reuse](#depth-and-evidence-reuse) exactly as written there; this section adds
no second trigger list. Compare the two on the single ordering
`BOUNDED < FULL`. `DELTA` never enters that ordering on either side: it is the
[Remediation limit](#remediation-limit) re-Judge state and reconciles by that
section's own rule, not this one; a `NOT_APPLICABLE` canonical depth means no
Judge applies at all, so nothing here can fire.

`JUDGE_DEPTH_RECONCILIATION: MATCH` when the Packet's depth is
`NOT_APPLICABLE` or already at or above the canonical required depth on that
ordering; proceed normally. `JUDGE_DEPTH_RECONCILIATION: ESCALATION_REQUIRED`
when the canonical required depth is strictly deeper than the Packet's
declared depth. A depth mismatch by itself is never evidence that the
implementation is wrong, that source remediation is needed, or that a new
worktree or sibling task is required — it states only that a deeper Judge,
and the evidence a deeper Judge needs, must still be produced.

Name the missing evidence precisely, e.g. `MISSING_JUDGE_EVIDENCE:
FULL_SUITE` for a `BOUNDED → FULL` escalation. `MISSING_JUDGE_EVIDENCE: NONE`
whenever valid same-exact-tree evidence for the required depth already exists
under the [reuse rule above](#depth-and-evidence-reuse) — identical command,
environment, HEAD, and tree, not invalidated — regardless of whether that
evidence predates this reconciliation or was supplied afterward; proceed
directly to that depth's Judge. Reuse, never replay. A tree change after that
evidence was captured invalidates it under the same reuse rule, so a later
reconciliation against the changed tree must treat the evidence as missing
again, never as still satisfying the escalation.

`IMPLEMENTATION_MUTATION_REQUIRED: NO` whenever the escalation is a pure
depth or evidence gap — the ordinary case, since the trigger that raised the
required depth describes what the change touches or what the acceptance
criteria demand, not a defect in what was built. Reserve
`IMPLEMENTATION_MUTATION_REQUIRED: YES` for the narrow case where the deeper
depth's evidence cannot be produced against the current tree at all because a
capability the acceptance criteria demand is genuinely absent, not merely not
yet run; treat that as a Planner Delta or Owner decision, never a silent
Worker guess.

When `IMPLEMENTATION_MUTATION_REQUIRED: NO`, request exactly one Continuation
Delta limited to `MISSING_JUDGE_EVIDENCE` and stop there: keep the same
branch, the same worktree, the same implementation tree, and every existing
exact-tree evidence artifact already gathered. Do not re-implement, reset, or
search for a different answer.

A later continuation against the same implementation tree, the same required
depth, and the same missing evidence must restate this exact escalation
block — `JUDGE_DEPTH_RECONCILIATION`, `CANONICAL_REQUIRED_JUDGE_DEPTH`,
`MISSING_JUDGE_EVIDENCE`, and `IMPLEMENTATION_MUTATION_REQUIRED` — rather than
an unexplained repeated `STOP`/`BLOCKED`. Once the named evidence is actually
supplied at the same exact tree, `MISSING_JUDGE_EVIDENCE` becomes `NONE` and
the task proceeds straight to the required depth's Judge without a second
escalation.

## Remediation limit

Allow at most one bounded remediation after a `REFUTED` Judge finding. Rerun
the finding-specific checks and, if load-bearing code/tests changed, the
complete local suite once on the remediated tree. Then hand off a `DELTA`
re-Judge. If the same finding is refuted again, stop with
`BLOCKED_AFTER_JUDGE_REFUTATION`; do not start another cycle.

## Durable terminal capture

When a Judge-owned long-running command has a load-bearing terminal result,
persist it with `DurableCommandCapture`
(`fable-method/scripts/task_checkpoint.rb`) to
`.fable/checkpoints/<task_id>/captures/<capture_id>.json` before that result
is relied upon for the final verdict: exact argv, exact stdout, exact stderr,
underlying process exit status, and start/end timestamps. The durable file,
not UI or subagent streaming, is the sole authority for that result.

`DurableCommandCapture#verdict` returns `PASS` only when the record is
complete and the exit status is exactly `0`; a non-zero exit status is always
`FAIL`, never `PASS`. Missing, unreadable, or incomplete evidence is always
`UNKNOWN_UNVERIFIABLE` rather than an inferred `PASS` or a guessed failure
class. Losing one command's evidence does not invalidate a sibling capture
file; rerun only the specific unresolved load-bearing command.

`DurableCommandCapture` never reads or persists the parent environment: it
captures exactly the argv it was given and exactly the stdout/stderr the
command produced, reusing this repository's existing lack of a broader
redaction system rather than inventing a new one.

## Handoff payload

Provide the original Packet and forbidden actions; repository, branch, HEAD,
tree, worktree and status; actual diff; exact authorized scope; acceptance
criteria; route and Judge mode/depth; commands, exit statuses, and raw
summaries (durable per [Durable terminal capture](#durable-terminal-capture)
when load-bearing); runtime evidence; all unknowns and failed attempts; and
the complete filesystem write/retained/deleted ledger.

The Judge re-derives its verdict from the Packet, diff, and evidence. Do not
pass internal reasoning or a persuasive summary as evidence.

Final-tree identity is Worker-owned: the Planner supplies Judge mode/depth but
must not predict or prefill final HEAD/tree values. The Worker records the
observed final HEAD/tree after implementation, and the Judge evaluates exactly
that identity read-only.

## Verdict and lifecycle boundaries

`PASS` is observed evidence, not a synonym for Judge `VERIFIED`. `NOT RUN` is
not pass, and unavailable reproduction is `UNVERIFIABLE`. The final
classification must agree with the Judge's verdict and any required
remediation. Do not commit, push, publish, deploy, merge, or clean up merely
because a Worker believes the implementation is ready; those actions need
their own Packet authorization and lifecycle evidence.
