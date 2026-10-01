# Failure modes: symptom → prevention

Use this as the `/fable-method audit` checklist and when a verification failure
needs diagnosis.

| # | Failure mode | Symptom | Prevented by |
|---|---|---|---|
| 1 | Unprompted fixing | A question caused edits | Task-class gate |
| 2 | Wrong deliverable | Interpretation A was built instead of B | Ambiguous-scope question |
| 3 | Re-litigation | Settled owner decisions were reopened | Packet authority |
| 4 | Fake done | No named way to check the result | Definition of done |
| 5 | Invented API | Signature or endpoint was recalled | Primary-source/recall gate |
| 6 | Sequential crawling | Independent lookups were serialized | Parallel evidence reads |
| 7 | Context flooding | Whole files/logs were dumped | Narrow one-level references |
| 8 | Analysis paralysis | Research continued after the action was fixed | Time-boxed lookup rounds |
| 9 | Plowing through surprise | Contradictory evidence was ignored | Surprise re-routing |
| 10 | Option dump | No recommendation was made | One recommendation rule |
| 11 | Scope creep | Drive-by refactors appeared | Exact scope/smallest change |
| 12 | Silent step dropping | A required item quietly never happened | Written checklist and audit |
| 13 | Retry thrash | Blind or speculative retries continued without reducing uncertainty | Falsifiable-hypothesis and exhaustion gates |
| 14 | Verification theater | “Should work” replaced a run | Observed target + surrounding check |
| 15 | Unauthorized action | Push/deploy/send followed documentation alone | Quoted authorization |
| 16 | Dropped follow-up | Required deploy/restart was omitted from report | `PENDING` caveat |
| 17 | Missed twins | One defect site was fixed without a sweep | `TWINS` search |
| 18 | Costume rigor | Thorough-looking claims had no evidence | Fit gate and runnable checks |
| 19 | Non-authoritative signal | A stale, cached, or wrong-scope source was trusted as current/ground truth | Authority-source check |

Skipped steps create the corresponding risk. A claimed-but-unobserved step is
verification theater, not a pass.

## Acceptance failures, retries, and stop tokens

On acceptance failure, attribute in order: harness/fixture/command, then the
deployment or execution chain, then the product invariant. Each continuation
must test a falsifiable hypothesis and materially reduce uncertainty. When a
correction is applied, rerun the real check and retain its actual output. Keep
an `ATTEMPT_LEDGER` for failures, retries, timeouts, terminations, overwritten
or deleted artifacts, and superseded evidence.

Identical blind retries and speculative patches are not evidence progress.
Evidence-progressing RCA has no arbitrary numeric ceiling, but stop when scope,
safety, authority, capability, proportionality, or discriminating evidence is
exhausted. External credentials, permissions, missing runtimes, and unresolved
authority remain blockers.

A stop token is final for the current task authority: no mutation, equivalent
command substitution, metadata workaround, upstream rewrite, or retry under a
different action class until a new authoritative Owner instruction or valid
Continuation Delta. `DO_NOT_POLL`: report the stop and end the turn; do not
sleep, poll, schedule a wakeup, or re-check while waiting, except for the
deferred queue's single required recheck.

When a fixed defect came from a construct that could plausibly recur elsewhere,
search the safe project for it and report:

```text
TWINS: searched <pattern> - found <N> other sites: <files or none>
```

Skip that search for a one-off or locally scoped defect.
