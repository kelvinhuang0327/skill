# Generic Ranking and Comparison

## Contents

- [Scope and core principle](#scope-and-core-principle)
- [Capability surface](#capability-surface)
- [Contract input](#contract-input)
- [Candidate input](#candidate-input)
- [Result output](#result-output)
- [Required semantics](#required-semantics)
- [Worked example](#worked-example)

## Scope and core principle

This reference is the authoritative contract for Fable's generic ranking and
comparison primitive: a pure, deterministic, evidence-aware capability that is
domain-neutral and executes only the comparison contract the caller states
explicitly.

The implementation lives in the Fable method repository at
`fable-method/scripts/generic_ranking.rb` and is not shipped with the installed
skill, so callers invoke it from a checkout of that repository.

The engine never invents a metric, never invents a weight, never fills a missing
value with zero, never rescales against the observed cohort, and never uses
randomness to break a tie. Anything it cannot decide from the declared contract
is reported as unranked rather than guessed. A caller that wants a domain
judgement must supply the metric, direction, evaluation window, eligibility, and
any weights itself.

The module is pure: no IO, no clock, no environment reads, and no mutation of
caller input.

## Capability surface

```ruby
require_relative '<repo>/fable-method/scripts/generic_ranking'

Fable::Ranking.compare(candidate_a, candidate_b, contract)  # pairwise decision
Fable::Ranking.rank(candidates, contract)                   # ordered ranking
```

Both raise `Fable::Ranking::InvalidInput` when the contract or candidates are
malformed. A CLI wrapper reads JSON on stdin and exits `2` with
`{"error":"INVALID_INPUT"}` on stderr for bad input:

```bash
ruby fable-method/scripts/generic_ranking.rb rank    < input.json
ruby fable-method/scripts/generic_ranking.rb compare < input.json
```

## Contract input

| Field | Required | Meaning |
| --- | --- | --- |
| `method` | yes | `LEXICOGRAPHIC` (ordered dimensions) or `WEIGHTED` (normalized score) |
| `universe` | yes | `population` and `evaluation_window`, both non-empty strings |
| `dimensions` | yes | ordered, non-empty, unique `id`s |
| `dimensions[].direction` | yes | `HIGHER_IS_BETTER` or `LOWER_IS_BETTER` |
| `dimensions[].weight` | `WEIGHTED` only | explicit non-negative number; sum must be positive |
| `dimensions[].normalization` | `WEIGHTED` only | explicit `min` < `max` bounds |
| `tie_break` | yes | ordered rules; must end with a `candidate_id` rule |

Weights and normalization are rejected under `LEXICOGRAPHIC`, so a weighted
reading can never be applied by accident. Numbers may be integers, floats, or
decimal strings; they are compared as exact rationals, so ordering never depends
on floating-point drift.

## Candidate input

| Field | Required | Meaning |
| --- | --- | --- |
| `candidate_id` | yes | non-empty, unique within a `rank` call |
| `eligibility` | defaults to `UNRESOLVED` | `ELIGIBLE`, `INELIGIBLE`, or `UNRESOLVED` |
| `universe` | yes for eligible candidates | must equal the contract universe |
| `metrics[id].state` | yes | `VALUE_PRESENT`, `MISSING`, `UNOBSERVABLE`, or `NOT_APPLICABLE` |
| `metrics[id].value` | `VALUE_PRESENT` only | any other state carrying a value is an error |
| `metrics[id].evidence` | yes | array of refs; empty evidence fails safe |
| `metrics[id].provenance` | optional | passed through to the result unchanged |

## Result output

`rank` returns `status` (`RANKED`, `PARTIAL`, `NOT_COMPARABLE`, or `EMPTY`), the
echoed `contract`, and four disjoint partitions: `ordered`, `ineligible`,
`unresolved`, and `not_comparable`. `ties` lists every shared rank.

`status` is `RANKED` when every candidate the engine was asked to place was
placed. Ineligibility is the caller's own declaration, so it does not by itself
make a result `PARTIAL`; `PARTIAL` means the engine could not place a candidate.

Each `ordered` row carries `rank` (shared on a semantic tie), `position` (the
distinct deterministic slot), the per-dimension trace, and `comparison_to_next`.
Under `WEIGHTED` a row also carries `score` as a plain decimal string and
`score_exact` as the exact rational that actually decided the order.

`compare` returns `status`, `relation` (`A_BEFORE_B`, `B_BEFORE_A`, `TIE`, or
`NOT_COMPARABLE`), `reason`, `decisive_dimension`, `tied_dimensions`, the
per-dimension values with their directions, and the `tie_break` rules applied.

## Required semantics

**Eligibility.** Only `ELIGIBLE` candidates are ordered. `INELIGIBLE` and
`UNRESOLVED` are partitioned out, never mixed into the ranking. An omitted
`eligibility` defaults to `UNRESOLVED`, so silence never buys a rank.

**Missing evidence.** A missing value never becomes zero. `MISSING`,
`UNOBSERVABLE`, and `NOT_APPLICABLE` are preserved distinctly in the trace, and
a candidate lacking a required value moves to `not_comparable` with a
`MISSING_REQUIRED_VALUE` issue. A present value with empty evidence fails the
same way, under `MISSING_EVIDENCE`.

**Comparable universe.** A candidate whose `population` or `evaluation_window`
differs from the contract is `not_comparable` (`UNIVERSE_MISMATCH`), never
force-ranked. An unresolvable universe raises `UNRESOLVED_UNIVERSE`.

**Normalization.** Bounds are declared, not derived from the cohort. A value
outside them is refused with `OUT_OF_NORMALIZATION_RANGE` rather than clamped,
so adding a candidate can never silently restate another candidate's score.

**Ties.** A semantic tie (equal comparison keys) shares a `rank`. The declared
`tie_break` chain then fixes a distinct `position`. Because the chain must end
in `candidate_id`, ordering is total and reproducible; identical input always
yields byte-identical output.

**Explanation.** Every decision reports why: eligibility, the decisive
dimension, both values, the direction, the weights and normalization used, the
tie-break rules applied, and any missing evidence. A weighted score is always
accompanied by the per-dimension contributions that produced it.

**Provenance.** Evidence refs and provenance survive into the result for ranked
and rejected candidates alike, so any placement stays traceable to its source.

## Worked example

```ruby
contract = {
  'method' => 'WEIGHTED',
  'universe' => { 'population' => 'cohort-1', 'evaluation_window' => '2024-01-01..2024-12-31' },
  'dimensions' => [
    { 'id' => 'accuracy', 'direction' => 'HIGHER_IS_BETTER',
      'weight' => '0.7', 'normalization' => { 'min' => '0', 'max' => '1' } },
    { 'id' => 'latency', 'direction' => 'LOWER_IS_BETTER',
      'weight' => '0.3', 'normalization' => { 'min' => '0', 'max' => '200' } }
  ],
  'tie_break' => [{ 'dimension' => 'latency' },
                  { 'field' => 'candidate_id', 'direction' => 'ASCENDING' }]
}

candidate = {
  'candidate_id' => 'a',
  'eligibility' => 'ELIGIBLE',
  'universe' => contract['universe'],
  'metrics' => {
    'accuracy' => { 'state' => 'VALUE_PRESENT', 'value' => '0.72', 'evidence' => ['run/42'] },
    'latency' => { 'state' => 'VALUE_PRESENT', 'value' => '150', 'evidence' => ['run/42'] }
  }
}

Fable::Ranking.rank([candidate, other], contract)
```

Focused coverage lives in `fable-method/test/test_generic_ranking.rb`; run it
with `ruby fable-method/test/test_generic_ranking.rb` from the repository root.
