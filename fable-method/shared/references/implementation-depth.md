# Implementation depth

`IMPLEMENTATION_DEPTH` names how the Worker allocates effort against
*this task's own unresolved uncertainty* — it is a work mechanism, not a
model or a native-reasoning-effort setting. Model choice and native reasoning
effort remain Owner-controlled and are never inferred, changed, or claimed
changed by selecting a depth. Judge involvement and Judge depth are a
separate axis, governed entirely by [Judge handoff](judge-handoff.md); this
file never adds a second Judge-trigger list and selecting a depth here never
creates, removes, or resizes a Judge requirement.

```text
IMPLEMENTATION_DEPTH: NORMAL | ENHANCED
```

## NORMAL (default)

Read authoritative sources — see [authority sources](authority-sources.md)
when a load-bearing signal might not be one — pick the smallest correct
change, and verify with the acceptance check plus the surrounding health
check `VERIFY_WORLD_NOT_SELF_REPORT` already requires. `NORMAL` still permits
whatever RCA the defect actually needs; it does not mean skipping
investigation, only that no extra deliverable is owed beyond the normal
verified result.

## ENHANCED

Use only for the specific decision that is genuinely uncertain, not the whole
task. Before mutating the at-risk area, name each invariant or ordering
property the change could break and the check that would catch a violation of
it. Prefer a check that can be shown to fail on the unfixed defect over one
that has only ever been observed green, per
[test falsifiability](test-falsifiability.md) — reuse an existing check that
already demonstrates this rather than adding a new one. When two or more
concrete root causes remain after one cheap localization step and they would
lead to different fixes, get the cheapest observation that discriminates
between them before choosing.

Report once, appended to the normal handoff:

```text
ENHANCED_EVIDENCE:
- INVARIANT: <the property that could break>
  CHECK: <what was run or observed>
  RESULT: <RUN_THIS_TASK | REUSED_EXACT_TREE_EVIDENCE, and the outcome>
```

One check can cover more than one listed invariant; cite it once and list
every invariant it covers rather than re-running it per row. `ENHANCED` never
by itself requires re-running unrelated already-valid coverage, adding new
acceptance criteria, or a full-suite run beyond what
[Judge handoff](judge-handoff.md#depth-and-evidence-reuse) or this task's own
acceptance already requires.

## Selecting a depth

Use the Packet's value when present. Absent a Packet value, select `NORMAL`
unless one of these applies, and name which one:

- a [workload-shape trigger](judge-handoff.md#depth-and-evidence-reuse) is
  materially exercised by behavior this task actually implements or modifies
  — not merely mentioned in surrounding history, a commit message, or a
  file's name;
- one cheap localization step leaves two or more concrete root causes that
  would lead to different fixes;
- authoritative sources conflict and the conflict survives an
  [authority-sources](authority-sources.md) check.

Waiting on an external result, elapsed time, file count, token spend, or a
repeated failure is never by itself a reason to select `ENHANCED`. Select at
most once automatically per task; after that, reassess only on new material
evidence, not on renewed uncertainty about a question already answered.

## What selecting a depth never does

- It never changes `WORKER_ROUTE`. Route follows [Route
  once](../SKILL.md#route-once) on its own terms: `FAST` already requires "no
  new behavior" and "one direct acceptance check", so a task that genuinely
  needs `ENHANCED` was very likely never a correct `FAST` classification in
  the first place — that is evidence the route call needs revisiting under
  the existing route contract, not a new cross-rule that lets a depth label
  override route.
- It never creates, removes, resizes, or reconciles a Judge trigger or
  `JUDGE_DEPTH`; those are computed only from [Judge
  handoff](judge-handoff.md), independent of `IMPLEMENTATION_DEPTH`, and the
  Judge floor there binds regardless of who supplied the value.
- It never changes model, native reasoning effort, agent count, budget, scope,
  or acceptance criteria.
- Selecting `ENHANCED` for a contradictory-authority reason authorizes bounded
  read-only reconciliation of that contradiction, nothing more. If the
  contradiction is actually an authorization or capability gap, the existing
  `STOP`/`BLOCKED` boundary in `SKILL.md` still applies unchanged; `ENHANCED`
  is not a route around it.
