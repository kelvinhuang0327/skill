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
in one Lifecycle closure bundle. Wait for one standalone Owner authorization
that names the exact worktree removal, the exact normal local delete, an
exact force fallback with its gates, and the exact remote delete. Re-check
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

A Planner conversation records the Owner's standalone authorization, then
hands a Worker Packet to a separate Worker conversation that only quotes the
token. That quote is metadata, not evidence: the Worker has not observed the
Owner say it, so it holds the high-risk step and asks the Owner to send the
authorization directly into this conversation first. Once the Owner pastes
that same standalone authorization as its own message here, then sends the
Packet, the Worker checks the message against the exact action and target,
treats it as valid evidence, and proceeds without asking a third time. Had
Owner and Worker shared one conversation from the start, that same direct
message would already have been sufficient — no separate handoff needed. In
every case, an action outside the authorized envelope — an unlisted remote
deletion, say — stays `PENDING` regardless of how the original authorization
arrived.
