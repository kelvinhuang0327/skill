# Regression bisection and change-set minimization

## Contents

- [Scope and core principles](#scope-and-core-principles)
- [Relationship to property-based verification](#relationship-to-property-based-verification)
- [Mode A: commit-granularity bisection](#mode-a-commit-granularity-bisection)
- [Mode A limitations](#mode-a-limitations)
- [Mode B: change-set minimization (ddmin)](#mode-b-change-set-minimization-ddmin)
- [Mode B limitations](#mode-b-limitations)
- [Reporting format](#reporting-format)

## Scope and core principles

Regression bisection is an on-demand, conditional reference pattern for
locating which historical commit, or which unit within one uncommitted
change-set, introduced an observed regression. It is not a mandatory Fable
gate; use it only when a task actually needs to localize a regression's
origin.

Two independent localization modes cover the two shapes this problem takes:

- **Mode A (commit granularity)** applies when a clean version-control range
  exists between a known-good and a known-bad state; it uses `git bisect` to
  binary-search that range.
- **Mode B (change-set granularity)** applies when there is no clean commit
  boundary — one oversized commit, one uncommitted working-tree diff, or one
  squashed change-set — and instead searches within that change-set using
  delta-debugging (ddmin) minimization.

Both modes require the same three things: a deterministic oracle (a repro
command with a stable pass/fail signal), an isolated worktree owned by this
task rather than the Owner's active or dirty state, and honest reporting when
the evidence does not support a single, clean answer.

## Relationship to property-based verification

[Property-based verification](property-based-verification.md) and regression
bisection localize different things and are complementary, not duplicates:

- **Property-based verification** fixes the code at one version and searches
  the *input domain*, shrinking a generated failing input to its minimal
  reproducing form.
- **Regression bisection / ddmin** fixes the input (the deterministic repro)
  and searches the *history or change-set domain* — which commit, or which
  subset of an uncommitted diff, introduced the failure.

A task can use both in sequence: shrink a failing input with property-based
verification first, then bisect history or a change-set for that same
now-minimal input.

## Mode A: commit-granularity bisection

Preconditions:

- a deterministic repro command that runs non-interactively and exits `0` on
  good, non-`125` non-zero on bad;
- a known-good boundary (a commit/ref where the repro passes) and a
  known-bad boundary (a commit/ref, or the current working state, where it
  fails);
- an isolated worktree dedicated to the bisect run — never the Owner's active
  or dirty worktree — consistent with Fable's `SINGLE_WRITER_PER_TASK`
  ownership discipline.

Workflow, run from the isolated worktree:

1. `git bisect start`
2. `git bisect bad <bad-ref>`
3. `git bisect good <good-ref>`
4. `git bisect run <deterministic-repro-command>` — Git checks out each
   candidate commit and uses the command's exit status as the verdict for
   that commit only, per Git's own `bisect run` semantics:
   - `0`: good;
   - `1`–`127` except `125`: bad;
   - `125`: this commit cannot be tested — Git marks it skipped (as `git
     bisect skip` would) and continues around it;
   - anything else (`128` or above, including death by signal): aborts the
     bisect run outright; never reinterpret an abort as a verdict.
5. Git reports either a single first-bad commit, or that it could not
   converge past a range because of adjacent skips.
6. Run `git bisect reset` afterward unconditionally — whether bisect
   converged or aborted — to restore the isolated worktree's original HEAD.
   Reset is cleanup; it is not evidence that the reported result is correct.

Report the exact commit only when the run converged cleanly: no abort, and
no skips immediately bracketing the reported commit. When commits near the
boundary were skipped, or bisect narrowed to a range rather than one commit,
report that surviving range and say so — do not round an inconclusive range
down to a single named culprit.

## Mode A limitations

- **Non-monotonic history**: bisect's binary search assumes the good/bad
  property changes exactly once across the searched range. A range where the
  defect appears, is masked, and reappears (an intervening revert,
  cherry-pick, or a second, independent regression) breaks that assumption —
  the search can converge on a commit that is not the true or only cause.
  Treat a bisect verdict as provisional whenever the range contains a known
  revert, cherry-pick, or merge of unrelated fixes, and corroborate it (for
  example, confirm the reported commit alone reproduces the defect against
  its immediate parent) before reporting a `CULPRIT`.
- **Flaky repro**: if the repro command's pass/fail signal is not
  deterministic (timing, concurrency, network access, uncontrolled test
  order), individual `bisect run` steps can mislabel a commit and silently
  corrupt the search. Confirm the repro is deterministic — repeat it at both
  boundaries a few times — before trusting a bisect verdict; if it cannot be
  made deterministic, do not run automated `bisect run`, and report the flake
  instead of a culprit.

## Mode B: change-set minimization (ddmin)

Use Mode B when there is no clean commit boundary to bisect, so the unit of
search is hunks or files within one change-set rather than commits in
history.

Preconditions:

- the same deterministic-oracle requirement as Mode A;
- every unit under test (file or hunk) must be independently and safely
  applicable in isolation, and in whatever subset combination is tested,
  against the same known-good base;
- partitions are constructed only from the Owner's own candidate change-set;
  never mutate Owner-unrelated dirty state (another in-progress file, another
  worktree) to build a partition.

Workflow:

1. Partition the candidate change-set into roughly equal, independently
   applicable units.
2. Apply each half (subset) and, separately, its complement to the known-good
   base, and run the deterministic oracle against each.
3. If a subset alone reproduces the failure, recurse into that subset and
   discard the rest; if its complement alone reproduces it, recurse into the
   complement instead.
4. If neither a subset nor its complement alone reproduces the failure but
   the full set does, increase granularity (smaller subsets) and retry; once
   granularity cannot increase further, retain the remaining units as jointly
   required.
5. Stop at a 1-minimal failure-inducing set: no remaining unit can be removed
   without the oracle flipping from bad to good.

## Mode B limitations

- **Apply conflict / dependency**: when a unit only applies cleanly on top of
  another unit already applied, or applying a subset leaves the tree unable
  to build or parse for a reason unrelated to the property under test, that
  subset cannot be independently evaluated. Report this as a limitation —
  which units could not be isolated, and why — instead of silently treating
  whichever smaller set happened to apply as a valid minimal subset.
- **Owner-unrelated state**: never construct a partition by resetting,
  stashing, or discarding dirty state this task does not own. If isolating a
  clean base would require touching state outside the task's declared scope,
  stop and report the limitation rather than mutate it.

## Reporting format

```text
LOCALIZATION_METHOD: BISECT | DDMIN_CHANGE_SET
GOOD_BOUNDARY: <commit/ref | NOT_APPLICABLE>
BAD_BOUNDARY: <commit/ref | working state>
REPRO_COMMAND: <exact command>
CULPRIT: <commit SHA | minimal change set | UNRESOLVED>
OBSERVED: <exit codes / oracle outcomes actually observed>
RESTORE: PASS | FAIL
```
