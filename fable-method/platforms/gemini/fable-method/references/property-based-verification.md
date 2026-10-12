# Property-based verification

## Contents

- [Scope and core principles](#scope-and-core-principles)
- [Relationship to Test Falsifiability](#relationship-to-test-falsifiability)
- [Eligibility criteria](#eligibility-criteria)
- [Ineligible use](#ineligible-use)
- [Useful property classes](#useful-property-classes)
- [Lifecycle and counterexample shrinking](#lifecycle-and-counterexample-shrinking)
- [Dependency and framework policy](#dependency-and-framework-policy)
- [Focused regression and bounded RCA](#focused-regression-and-bounded-rca)
- [Reporting format](#reporting-format)

## Scope and core principles

Property-based verification (or Property-Based Testing, PBT) is an on-demand,
conditional verification reference pattern. Instead of testing hand-crafted,
individual input/output pairs (example-based testing), property-based testing
specifies general properties — universal invariants, relational contracts, or
algebraic laws — that must hold true across an entire input domain, and evaluates
them against many generated inputs.

Key elements of the pattern include:

1. **Generation over an input domain**: A generator defines the search space
   (e.g., arbitrary strings, integers within bounds, structured ASTs, candidate
   vectors) and synthesizes diverse, randomized, or combinatorial instances.
2. **Invariant and property checking**: An automated property oracle checks that
   every generated input satisfies the invariant. The oracle does not need to
   calculate the exact output in advance; it validates relational or algebraic
   truths (e.g., invariants, idempotence, round-trip symmetry).
3. **Reproducible seed/path/example recording**: Every test run that uses
   pseudorandom generation must log its seed, generation parameters, or the
   concrete failing input so that any failure can be deterministically replayed.
4. **Counterexample shrinking and minimization**: When a generated input
   violates a property, the verification harness systematically shrinks
   (simplifies) the input to the minimal, simplest counterexample that still
   reproduces the failure.
5. **No mandatory gate**: PBT is an optional verification recipe applied when
   a task's domain and oracle warrant it; it is not a mandatory gate for all
   Fable tasks.

## Relationship to Test Falsifiability

PBT complements Test Falsifiability; it does not replace it.

The two techniques operate on distinct dimensions of verification:

- **[Test Falsifiability](test-falsifiability.md)** confirms that a test or guard
  is capable of failing when the specific defect it guards is introduced
  (preventing vacuous passes through controlled mutation).
- **Property-based verification** explores a domain to discover unexpected edge
  cases, boundary conditions, and invariant violations across generated inputs.

A passing property-based test does not prove the test is falsifiable. If the
property oracle is trivially true or detached from the implementation under
test, the property test will pass on millions of generated inputs while
detecting nothing. Any newly added property-based test must itself satisfy
falsifiability: mutating the guarded implementation must cause the property
assertion to fail.

## Eligibility criteria

Property-based verification is appropriate for components with clear, sound
oracles and deterministic behavior, such as:

- **pure deterministic functions**: transformations with no side effects or
  external dependencies;
- **parsers, serializers, and codecs**: binary/text decoders, format parsers,
  and serialization pipelines;
- **ranking, scoring, and comparison**: ordering contracts (such as
  [generic ranking](generic-ranking.md)), metric evaluations, and tie-breaking;
- **lossless round trips**: operations with invertible forward and inverse
  transformations;
- **deterministic state-transition models**: state machines and protocol engines
  with well-defined transitions;
- **transformations with explicit algebraic or relational invariants**: algorithms
  satisfying conservation laws, symmetry, or mathematical boundaries.

## Useful property classes

When constructing properties, prefer standard algebraic and structural patterns:

- **Round-trip**: Encoding then decoding recovers the original value, i.e.,
  `decode(encode(x)) == x` or `parse(serialize(x)) == x`.
- **Idempotence**: Applying an operation multiple times yields the same result as
  applying it once, i.e., `f(f(x)) == f(x)` (e.g., path normalization, formatting,
  sorting, deduplication).
- **Monotonicity**: Preserving order across transformation, i.e., if `x <= y`,
  then `f(x) <= f(y)` (e.g., priority queues, monotonic scoring, pagination counters).
- **Permutation invariance**: The result does not depend on the order of inputs,
  i.e., `f(shuffle(xs)) == f(xs)` (e.g., set operations, multi-candidate ranking
  pools, unordered batch aggregations).
- **Antisymmetry and transitivity**: Pairwise comparison contracts where
  `compare(a, b) == -compare(b, a)` and `a > b && b > c` implies `a > c`
  (when semantically valid for the domain).
- **Bounded output ranges**: Outputs are strictly constrained within known bounds,
  i.e., `min <= f(x) <= max` or membership in an allowed categorical set.
- **Model and state invariants**: State-transition systems preserve structural
  integrity across all transitions (e.g., total resource conservation, non-negative
  counters, acyclic graphs).

## Ineligible use

Do not recommend or require routine PBT for:

- provider or model calls;
- network APIs or external HTTP endpoints;
- database mutations;
- Git lifecycle actions (branches, commits, tags, remotes);
- live activation or deployment steps;
- uncontrolled filesystem side effects;
- trivial glue, wiring, or configuration changes;
- tasks with no sound property oracle.

In these environments, stochastic generation introduces flaky failures, slows down
execution, and violates the principle of deterministic evidence.

## Lifecycle and counterexample shrinking

When a property fails during generation:

1. **Shrink**: The harness automatically reduces the failing input (e.g., removing
   elements, reducing integers toward zero, shortening strings) until the minimal
   failing input is isolated.
2. **Record**: Capture the exact minimal input, the property violated, and the
   random seed that generated it.
3. **Reproduce**: Convert the minimal counterexample into a standalone, deterministic
   unit test before modifying the implementation.
4. **Fix**: Implement the fix so the deterministic test and the general property test
   both pass.

## Dependency and framework policy

Fable MUST NOT automatically install Hypothesis, fast-check, QuickCheck, or any
other PBT framework.

Follow these strict rules:

- **Use existing project dependencies**: If the repository already has an approved
  PBT library configured (e.g., in `Gemfile`, `package.json`, or `pyproject.toml`),
  use it directly.
- **Task-specific generated checks**: If no PBT framework is installed, do not add
  one. When property verification is valuable and practical, write a bounded,
  task-specific generated or exhaustive check (e.g., iterating over boundary tables,
  combinatorial matrix loops, or deterministic pseudorandom loops with fixed seeds)
  using the project's native test runner (e.g., Minitest, Jest, pytest).
- **No new dependencies**: Introducing an external PBT package or modifying package
  manifests solely for property verification is strictly prohibited unless explicitly
  authorized by the task packet.

## Focused regression and bounded RCA

Property-based testing is a discovery mechanism; focused regression is a retention
mechanism.

1. **Promote counterexamples to focused regressions**: A minimized counterexample
   discovered by PBT must not rely on future randomized runs to catch regressions.
   Add the concrete minimal failing input as an explicit regression test in the
   project's native test suite.
2. **Bounded Root Cause Analysis (RCA)**: Minimization strips away irrelevant noise
   from the failing case, leaving only the essential defect trigger (e.g., empty
   collection, Unicode surrogate pair, negative zero, boundary overflow). Root cause
   analysis must focus strictly on the minimal failure condition rather than
   speculating about incidental data artifacts.

## Reporting format

When property-based verification is employed for a task, record the evidence in the
Worker report using this format:

```text
PROPERTY_BASED_VERIFICATION: CONFIRMED | NOT_APPLICABLE
TARGET: <component or function evaluated>
PROPERTIES_CHECKED: <property classes checked, e.g. round-trip, idempotence>
SEED: <fixed random seed or deterministic domain bounds>
SHRUNK_COUNTEREXAMPLE: NONE | <minimal reproducing input>
FOCUSED_REGRESSION_ADDED: YES | NOT_APPLICABLE
FALSIFIABILITY_CHECK: CONFIRMED | NOT_SAFE | NOT_APPLICABLE
```
