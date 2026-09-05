# Diff / Patch Coverage

## Contents

- [Scope and eligibility](#scope-and-eligibility)
- [What it measures](#what-it-measures)
- [What it does NOT prove](#what-it-does-not-prove)
- [Relationship to Test Falsifiability](#relationship-to-test-falsifiability)
- [Relationship to property-based verification](#relationship-to-property-based-verification)
- [Relationship to regression bisection](#relationship-to-regression-bisection)
- [Dependency and eligibility policy](#dependency-and-eligibility-policy)
- [Same-tree evidence](#same-tree-evidence)
- [Helper contract](#helper-contract)
- [Reporting format](#reporting-format)

## Scope and eligibility

Diff coverage is an on-demand, conditional reference pattern that mechanically
answers one narrow question: which added/modified lines in this task's diff
were actually executed by the repository's already-configured coverage run?
It is not a mandatory Fable gate; use it only when a task actually needs this
measurement.

Apply it only when BOTH are true:

1. the task added or modified production/source lines;
2. the repository already has an approved native coverage-capable test
   command that can emit LCOV, Cobertura, or JaCoCo output.

If either condition is false - no source changed, or no coverage tool is
already configured - the result is:

```text
DIFF_COVERAGE: NOT_APPLICABLE
```

Never install a coverage generator, add a coverage dependency, or change the
repository's coverage configuration in order to make this reference
applicable.

## What it measures

Changed-line execution adequacy only: of the lines a unified diff actually
adds or modifies, how many were exercised (hit count greater than zero) by
the coverage report the repository's own test command already produced. The
diff itself defines the line universe; the helper never infers semantic
executability from source text, and unrelated unchanged lines are never
counted.

## What it does NOT prove

- Covered does not mean correct - a line can execute and still compute the
  wrong result.
- 100% diff coverage does not mean defect-free.
- Diff coverage does not prove [test falsifiability](test-falsifiability.md) -
  a covered line can still be guarded by a vacuous assertion that would pass
  either way.
- Diff coverage does not replace
  [property-based verification](property-based-verification.md) or explicit
  invariants.
- Diff coverage does not localize historical regressions; it says nothing
  about *when* a defect was introduced, only whether the current change's
  lines executed.

## Relationship to Test Falsifiability

[Test Falsifiability](test-falsifiability.md) asks: can the claimed
test/check fail when its guarded behavior is broken? Diff Coverage asks: did
the tests execute the production lines this task changed? A line can be
covered (executed) by a test that would never fail no matter what the line
does - the two questions are complementary, and neither substitutes for the
other.

## Relationship to property-based verification

[Property-based verification](property-based-verification.md) explores an
input domain against declared invariants; diff coverage measures only
whether the changed lines executed, regardless of how thoroughly. A changed
line can be fully covered by one example-based test and never touched by any
generated input, or the reverse.

## Relationship to regression bisection

[Regression bisection](regression-bisection.md) searches history or a
change-set for where a defect was introduced. Diff coverage never looks at
history; it measures execution of the current change-set's lines against the
current coverage run only.

## Dependency and eligibility policy

Fable MUST NOT automatically install diff-cover, Codecov tooling,
coverage.py, Istanbul/nyc, JaCoCo, SimpleCov, or any other coverage
generator. The helper only parses a coverage report a project's own,
already-configured test command produced; it never provisions one. When no
such report/format already exists, eligibility condition 2 fails and the
result is `NOT_APPLICABLE`.

## Same-tree evidence

The coverage report and the task's diff must correspond to the same
load-bearing HEAD/tree: a report generated against a different commit proves
nothing about the current diff. The coverage file itself is not Git identity
evidence - reuse the task's existing exact HEAD/tree evidence rather than
re-deriving it from the report.

## Helper contract

The implementation lives at `fable-method/scripts/diff_coverage.rb` and is
not shipped with the installed skill, so callers invoke it from a checkout of
the Fable method repository. It is a pure, dependency-free Ruby module (XML
parsing uses only the Ruby standard library's REXML):

```ruby
require_relative '<repo>/fable-method/scripts/diff_coverage'

Fable::DiffCoverage.measure(diff: diff_text, format: 'LCOV', coverage: report_text, repo_root: nil)
```

`format` is one of `LCOV`, `COBERTURA`, or `JACOCO` - never sniffed or
guessed. `repo_root` is optional and used only to relativize an absolute
coverage-report path; an absolute path with no supplied root, or one outside
it, fails closed rather than being guessed at. A CLI wrapper reads a JSON
object (`diff`, `format`, `coverage`, `repo_root`) on stdin and exits `2` with
`{"error":"INVALID_INPUT"}` on stderr for invalid input:

```bash
ruby fable-method/scripts/diff_coverage.rb < input.json
```

Path identity is always an exact match after normalization (leading `./`,
Git `a/`/`b/` diff prefixes, and an in-root absolute path) - never fuzzy or
basename matching, and never a choice among ambiguous suffix matches. A
changed file legitimately absent from the coverage report has its changed
lines counted uncovered; a genuinely malformed or ambiguous report (bad
records, invalid XML, conflicting hit counts for the same line, an
unresolvable absolute path) fails closed with a diagnostic instead of
guessing. JaCoCo file identity is the package name plus the sourcefile name;
when a project's source-root layout does not make that string equal the
diff's repo-relative path, the affected lines fall out as "absent from
report" (uncovered), not a fuzzy or partial match.

The result is measurement only - the helper never imposes a pass/threshold
verdict:

```json
{
  "STATUS": "MEASURED",
  "COVERAGE_FORMAT": "LCOV",
  "TOTAL_CHANGED_LINES": 2,
  "COVERED_CHANGED_LINES": 1,
  "UNCOVERED_CHANGED_LINES": 1,
  "DIFF_COVERAGE_PERCENT": 50.0,
  "UNCOVERED": [{"file": "lib/foo.rb", "line": 12}]
}
```

`UNCOVERED` is always ordered file path ascending, then line number
ascending. Zero changed lines returns `STATUS: NOT_APPLICABLE` with a `null`
percentage rather than a misleading `100%`. If a caller wants a pass/fail
threshold, it must declare and apply that threshold itself against
`DIFF_COVERAGE_PERCENT` - Fable defines no default numeric threshold, and the
helper accepts no threshold input at all.

Focused coverage lives in `fable-method/test/test_diff_coverage.rb`; run it
with `ruby fable-method/test/test_diff_coverage.rb` from the repository root.

## Reporting format

```text
DIFF_COVERAGE: MEASURED | NOT_APPLICABLE
COVERAGE_FORMAT: LCOV | COBERTURA | JACOCO
SOURCE_HEAD:
SOURCE_TREE:
TOTAL_CHANGED_LINES:
COVERED_CHANGED_LINES:
UNCOVERED_CHANGED_LINES:
DIFF_COVERAGE_PERCENT:
UNCOVERED:
THRESHOLD: NOT_DECLARED | <caller-declared value>
OBSERVED:
```

`SOURCE_HEAD`/`SOURCE_TREE` are the Worker's own same-tree evidence, not
helper output. `THRESHOLD`/`OBSERVED` are populated only when a caller
explicitly declared a threshold and compared it itself; the default is
`THRESHOLD: NOT_DECLARED`.
