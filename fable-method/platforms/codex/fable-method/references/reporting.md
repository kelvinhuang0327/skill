# Outcome-first reporting

## Contents

- [Compact Worker report](#compact-worker-report)
- [Evidence labels](#evidence-labels)
- [Lifecycle closure](#lifecycle-closure)

Use the compact form for ordinary `FAST`/`STANDARD` work. Use the full
evidence handoff for Judge-gated work, a blocked result, or a Packet that names
additional fields.

## Compact Worker report

```text
STATUS: <what happened, in plain language>
ROUTE: <the selected WORKER_ROUTE>
CHANGED: <authorized files/surfaces touched>
VERIFIED: <commands/observations and real output>
NOT RUN / BLOCKED: <checks not run and why>
RISKS: <remaining unknowns or caveats>
```

Every `VERIFIED` claim must trace to an observed command or runtime result.
Never replace evidence with “should work”, “looks correct”, or “likely passes”.
If a prescribed follow-up was deliberately not taken, name it as:

```text
PENDING: <action> - awaiting your authorization
```

## Evidence labels

Use `[Confirmed]` for direct command or observation, `[Inferred]` for a
machine-checkable conclusion derived from observed state, and `[Unknown]` when
the required source or runtime evidence was unavailable. A successful retry can
be named:

```text
FINAL_SUCCESSFUL_ATTEMPT:
OVERALL_TASK_CONTRACT_RESULT:
```

Earlier failures, aborts, timeouts, and terminations remain in the attempt and
filesystem ledgers.

## Lifecycle closure

`FULL_PR_LIFECYCLE_CLOSED: YES` requires implementation, authorized
publication/merge, post-merge verification, workspace and branch cleanup, and
no unresolved blocker. For a local uncommitted Worker handoff, use:

```text
PR_PUBLICATION_STATUS: NOT_AUTHORIZED
POSTMERGE_LIFECYCLE_STATUS: NOT_APPLICABLE
BRANCH_CLEANUP_STATUS: NOT_APPLICABLE
FULL_PR_LIFECYCLE_CLOSED: NO
```

Do not prewrite a Judge verdict, claim publication, or call local completion a
closed PR lifecycle.
