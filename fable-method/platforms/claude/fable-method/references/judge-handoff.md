# Judge handoff contract

## Contents

- [When the Judge gate fires](#when-the-judge-gate-fires)
- [Depth and evidence reuse](#depth-and-evidence-reuse)
- [Remediation limit](#remediation-limit)
- [Handoff payload](#handoff-payload)
- [Verdict and lifecycle boundaries](#verdict-and-lifecycle-boundaries)

This reference describes the Worker-to-Judge boundary. The Judge is an
independent, read-only verifier; a Worker report is never an independent
verdict.

## When the Judge gate fires

The Judge trigger has one definition, in `SKILL.md` "Route once": a listed
category and material consequence together. Do not restate or widen that list
here. A single acceptance failure is not a trigger; a second retry whose cause
is still unattributed is. `LOOP_JUDGED` is judged by route definition, not by
category.

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

## Remediation limit

Allow at most one bounded remediation after a `REFUTED` Judge finding. Rerun
the finding-specific checks and, if load-bearing code/tests changed, the
complete local suite once on the remediated tree. Then hand off a `DELTA`
re-Judge. If the same finding is refuted again, stop with
`BLOCKED_AFTER_JUDGE_REFUTATION`; do not start another cycle.

## Handoff payload

Provide the original Packet and forbidden actions; repository, branch, HEAD,
tree, worktree and status; actual diff; exact authorized scope; acceptance
criteria; route and Judge mode/depth; commands, exit statuses, and raw
summaries; runtime evidence; all unknowns and failed attempts; and the complete
filesystem write/retained/deleted ledger.

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
