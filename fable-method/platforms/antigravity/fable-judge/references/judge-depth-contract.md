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

### Verification temp isolation

Before a Judge-authoritative full suite or otherwise one-shot expensive
verification:

1. freeze the judged source HEAD/tree;
2. resolve the verification temp/scratch root;
3. if in-tree temp state could affect judged tree/evidence identity:
   require temp root outside the judged source worktree;
4. classify that temp resource:
   TEMPORARY_DELETE;
5. only then consume the expensive verification run.

Do not require external temp roots for every ordinary focused test. A preflight
failure on temp placement stops execution before running the expensive suite,
leaving the verification run unconsumed and the source tree uncontaminated.

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

