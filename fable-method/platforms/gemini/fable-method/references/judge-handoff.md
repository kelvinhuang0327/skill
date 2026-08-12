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

Use a fresh-context Judge for security/authentication/authorization,
finance/payment, database schema or production data, shared-core or
cross-runtime changes, real UI/browser/device validation, external effects,
Loop execution, acceptance failure or repair retry, explicit independent
verification, or material unknown/incomplete evidence.

`READ_ONLY_COMPLETION_REVIEW` goes directly to `fable-judge` and has no Worker
route. A Worker with no fresh-context capability may self-check only, must mark
`JUDGE_MODE: SELF_CHECK_ONLY`, and must not claim independent `VERIFIED` for a
Judge-gated task.

## Depth and evidence reuse

Choose exactly one depth:

- `BOUNDED`: initial focused independent reproduction; default.
- `FULL`: named security/authentication, database/production-data,
  payment/irreversible, deployment, un-isolable shared-core, or missing,
  invalid, or contradictory final-suite evidence.
- `DELTA`: the one permitted remediation's finding, diff, tests, and impacted
  regression slice.

Reuse evidence only when command, environment, HEAD, and tree are identical and
the evidence was not invalidated. Run the complete local suite at most once per
final tree. Under ordinary judged timing, run focused acceptance and the
impacted regression slice before the initial bounded Judge; run the complete
suite after the Judge or permitted remediation. A load-bearing edit after a
full suite invalidates it.

Before handoff, state:

```text
JUDGE_DEPTH: BOUNDED | FULL | DELTA
JUDGE_DEPTH_REASON:
```

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
