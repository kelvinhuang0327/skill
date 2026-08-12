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
| 13 | Retry thrash | Same fix was tried indefinitely | Three-attempt bound |
| 14 | Verification theater | “Should work” replaced a run | Observed target + surrounding check |
| 15 | Unauthorized action | Push/deploy/send followed documentation alone | Quoted authorization |
| 16 | Dropped follow-up | Required deploy/restart was omitted from report | `PENDING` caveat |
| 17 | Missed twins | One defect site was fixed without a sweep | `TWINS` search |
| 18 | Costume rigor | Thorough-looking claims had no evidence | Fit gate and runnable checks |

Skipped steps create the corresponding risk. A claimed-but-unobserved step is
verification theater, not a pass.
