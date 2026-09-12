# Test falsifiability

A newly-added test/check that only ever passes proves nothing: if it would
still pass with the guarded defect left in place, its `PASS` is vacuous.
Before citing new coverage as completion evidence, confirm — when safely and
proportionately possible — that it can actually fail for the defect it claims
to detect.

## When it applies

Only when the current task adds or materially changes a test/check meant to
guard a specific defect, invariant, regression, or gate. Pre-existing coverage
the task did not touch never needs this, and it is not a blanket
mutation-testing requirement over the whole suite.

## Sequence

1. Run the new/changed test/check and confirm it is green.
2. Identify the smallest safe mutation that invalidates exactly the guarded
   behavior.
3. Apply it only in task-owned local state or a controlled test fixture/seam
   — never production data, external service state, live deployment/runtime,
   secrets/auth state, destructive filesystem state, or another Agent/Owner's
   dirty work.
4. Re-run the same test/check and require it to fail for the expected reason.
5. Restore the mutation completely.
6. Re-run the test/check and require `PASS`.

The mutation is evidence only; it never lands in the final tree.

## Dependency-boundary structure

When a change's correctness turns on real dependency structure — a
dependency fingerprint, import closure, resource manifest, or similar graph
the fix reasons over — a simplified fixture is not sufficient evidence by
itself unless it demonstrably reproduces that structure; generalizing from
an unrepresentative fixture to the real repository is not evidence the fix
works there. When safe and relevant to acceptance, exercise a bounded case
against real repository topology, or show concretely that the fixture
covers the load-bearing structure (import breadth, resource references, or
graph shape) the fix depends on.

Verify both directions of the identity the fix computes: a noncausal change
— one the guarded structure does not depend on — must leave that identity
unchanged, and a causal change — one it does depend on — must change it. A
check that only ever exercises one direction cannot distinguish a real
dependency boundary from an accidental one.

Reuse coverage that already exercises the real structure instead of adding
another probe. The `NOT_SAFE` / `NOT_APPLICABLE` boundary below still
applies: when no safe bounded case is possible, say so and report the
untested scope honestly rather than marking it `PASS`.

## Safety and `NOT_APPLICABLE`

If no safe bounded mutation is possible, report `NOT_SAFE` and proceed — by
itself this never blocks an otherwise valid task. Coverage that is not newly
added or materially changed is `NOT_APPLICABLE`. Never broaden authorization
just to run this check.

## Reporting

```text
FALSIFIABILITY_CHECK: CONFIRMED | NOT_SAFE | NOT_APPLICABLE
TARGET: <the new or changed test/check>
MUTATION: <what was changed, if CONFIRMED>
OBSERVED: <expected failure observed, if CONFIRMED>
RESTORE: PASS
FINAL_REVERIFY: PASS
```

`NOT_SAFE` and `NOT_APPLICABLE` need only a short reason and are not
themselves a task failure.
