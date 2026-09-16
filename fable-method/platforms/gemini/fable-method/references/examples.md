# Worked examples

Each example makes the definition of done and observed verification explicit.

## 1. Trivial gate

“Rename `getUsrData` to `getUserData` in `api.ts`.” One file, under ten lines,
no new behavior, and no search needed: edit, run the existing typecheck/build,
and report the observed result. If search reveals multiple files, reclassify
and enter the full loop.

## 2. Question or assessment

“Why is the dashboard slow?” Change nothing. Done means a cause backed by file,
line, network, or measurement evidence. Read the data-fetch and render paths,
observe requests, state one cause and one recommended fix, and offer the fix as
a separate future task.

## 3. Task

“Fix the failing date test.” Done means the named test and the surrounding suite
pass. Read test and implementation, surface a surprise if the test is right
and the code drops timezone information, make the smallest change, run the
named acceptance and surrounding suite, and report the real counts.

## 4. Plan-first

“Analyze project configuration and propose a global standard.” A broad or
outward change is plan-first. Enumerate actual configurations, read them,
state conflicts and one recommendation with verification for each step, then
stop for approval. Do not edit during the plan.

## 5. Lifecycle closure — superseded branch

An original PR closed unmerged; its successor merged after a mechanical
cherry-pick, so the original is fully superseded. A clean local worktree, a
local branch still pointing at the original SHA, and the matching remote
branch all remain. Done means every one of those surfaces reaches a verified
terminal state, not just the local ones.

Run one bounded closure preflight to confirm the supersession still holds,
then expose every remaining surface — worktree, local branch, remote branch —
in one Lifecycle closure bundle. Obtain one direct Owner authorization that
names the exact worktree removal, the exact normal local delete, an exact
force fallback with its gates, and the exact remote delete. That authorization
may be in the same direct Owner user message as the executable Worker Packet.
Re-check
target identities, try the normal local delete first, and only use the force
fallback if it refuses solely because of cherry-pick ancestry and every
fallback gate still passes. If one target has drifted, skip it and continue
the others. Report the exact residuals and the true `FULL_PR_LIFECYCLE_CLOSED`
state.

If the force fallback or the remote delete was not explicitly part of that
one authorization, stop or leave that action `PENDING` exactly as for any
other unauthorized destructive or remote action — a generic cleanup
authorization never covers either one.

## 6. Authorization conversation boundary

The Owner may send exactly one direct user message into the target Worker
conversation containing both the explicit exact authorization scope and the
executable Worker Packet. That one message is sufficient evidence and task
handoff:

```text
OWNER_ACTION_AUTHORIZATION: PASS
TASK_HANDOFF: PASS
SEPARATE_AUTHORIZATION_ONLY_MESSAGE_REQUIRED: NO
```

An earlier direct Owner authorization in the same Worker conversation may be
reused while its exact action and target scope remain applicable. A Planner
summary, assistant-authored claim, or quote from another conversation is
metadata rather than authorization. In every case, an action outside the
authorized envelope — an unlisted remote deletion, say — stays `PENDING`.
