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
