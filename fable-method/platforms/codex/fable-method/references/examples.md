# Worked examples

Each example makes the definition of done and observed verification explicit.

## 1. Simple — FAST

“Rename `getUsrData` to `getUserData` in `api.ts`.” One known file, a small
change, no new behavior: edit, run the existing typecheck/build, and report the
observed result. If search reveals multiple files, say so and switch to
`STANDARD`.

## 2. Question or assessment

“Why is the dashboard slow?” Change nothing. Done means a cause backed by file,
line, network, or measurement evidence. Read the data-fetch and render paths,
observe requests, state one cause and one recommended fix, and offer the fix as
a separate future task.

## 3. Normal — STANDARD

“Fix the failing date test.” Done means the named test and the tests that
cover the changed code pass. Read test and implementation, surface a surprise
if the test is right and the code drops timezone information, make the
smallest change, run the focused checks, and report the real counts.

## 4. Hard or research — STANDARD / ENHANCED

“Make the nightly import job finish in under ten minutes.” The cause and the
right change are not known yet. Profile once, name the two or three candidate
causes, take the cheapest observation that tells them apart, pick one, and
implement it. Verify the invariants the change could break (same output rows,
no lost records) with checks that fail on the unfixed code, then report
`ENHANCED_EVIDENCE`.

## 5. Plan-first

“Analyze project configuration and propose a global standard.” A broad or
outward change is plan-first. Enumerate actual configurations, read them,
state conflicts and one recommendation with verification for each step, then
stop for approval. Do not edit during the plan.

## Compact flow without a Packet

1. Classify the ask: plan-first beats task; a mixed “why and fix” is a task;
   a pure question changes nothing. Ask one pointed question only when
   evidence cannot resolve materially different deliverables.
2. Define done as an observable result and name its verification.
3. Pick the complexity flow from `SKILL.md`, read the primary sources it needs,
   and make one evidence-backed recommendation or change.

Load the matching domain adapter for non-coding work. A researchable unknown
gets bounded research first; pure inference gets an explicitly low-confidence
answer or one pointed question.
