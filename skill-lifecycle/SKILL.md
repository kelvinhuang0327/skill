---
name: skill-lifecycle
description: Candidate-development discipline for proposing, reviewing, refining, and evidencing a new or changed Skill before anyone decides whether to promote it. Freeze a candidate, get a fresh prospective review, record attributable refinements, organize evaluation evidence, run a deterministic integrity check, and hand a prepared package to whoever holds promotion authority. Use when developing or evaluating a Skill candidate. It never authorizes promotion and replaces no existing authority.
---

# skill-lifecycle

A candidate-development Skill. It helps you get a Skill candidate into a state
where someone else can make a promotion decision on real evidence.

```text
freeze → fresh prospective review → explicit refinement → evaluation evidence
→ deterministic integrity check → promotion-review handoff
```

Full procedure: [references/CANDIDATE_LIFECYCLE.md](references/CANDIDATE_LIFECYCLE.md).
Mechanical checker: [scripts/validate_evidence.py](scripts/validate_evidence.py).

## Role boundary — read before using this Skill

This Skill owns a workflow. It owns no authority. Anything in the left column
below belongs to someone else and this Skill must not act as if it holds it.

**Fable owns:**

- execution authority;
- route;
- safety;
- Judge handoff;
- publication boundary.

**Planner owns:**

- task synthesis;
- task-specific acceptance;
- scope;
- constraints.

**Owner owns:**

- semantic and product decisions;
- high-risk authorization;
- actual promotion authorization where applicable.

**Judge owns:**

- independent verification when required by Fable.

**skill-lifecycle owns only:**

- candidate freezing;
- prospective omission/assumption review discipline;
- candidate refinement history;
- candidate-local advisory memory guidance;
- evaluation evidence organization;
- mechanical evidence integrity/completeness checks;
- promotion-review preparation.

It never authorizes promotion.

This Skill is not a promotion authority, not a replacement for Fable, not a
replacement for Planner, not a replacement for Judge, not an authorization
layer, and not an autonomous skill-mutating system. It performs no automatic
candidate repair. It reads and organizes; it does not decide.

## What the lifecycle concludes

The strongest conclusion available here is:

```text
READY_FOR_PROMOTION_REVIEW
```

That means exactly one thing: the candidate and its evidence package are
prepared for the applicable Owner / Fable / Judge decision.

It does **not** mean any of:

```text
PROMOTION_AUTHORIZED
PROMOTION_VERIFIED
CANONICAL
DEPLOYED
```

Reaching `READY_FOR_PROMOTION_REVIEW` with outstanding
`MANUAL_REVIEW_REQUIRED` items is normal and expected. Those items are the
work a reviewer still has to do, not residue to be cleared before handoff.

## The six steps

1. **FREEZE.** Write an immutable `candidate_v1` before implementation and
   before review. Bind candidate identity, target skill, source identity,
   hypothesis, scope, known assumptions, planned positive evidence, planned
   negative/falsification evidence, and known unresolved semantics. Once
   reviewed, `candidate_v1` must not be rewritten.
2. **FRESH PROSPECTIVE REVIEW.** Use a fresh context where practical. Give the
   reviewer only the minimum pinned candidate and source evidence needed to
   find omitted assumptions, portability assumptions, incomplete scope,
   contradictory evidence planning, weak negative evidence, and unresolved
   semantics. Withhold later outcome evidence. The reviewer is **advisory** and
   is **not** a Fable Judge.
3. **REFINEMENT.** Create `candidate_v2`, `candidate_v3`, and so on. Never
   overwrite earlier candidate history. Every material delta states why it
   changed and which review finding or piece of evidence caused it.
4. **EVALUATION.** Collect positive, negative, near-miss, determinism/
   regression, and value evidence appropriate to the candidate. A candidate is
   not required to be code-backed. For a code-backed deterministic candidate,
   declared negative fixtures alone are **not** sufficient evidence — prefer
   actual execution and falsification evidence.
5. **MECHANICAL INTEGRITY CHECK.** Run `validate_evidence.py`, and use it only
   for properties it can actually establish.
6. **PROMOTION-REVIEW HANDOFF.** Hand the prepared package to whoever holds
   the decision.

## The mechanical checker, honestly scoped

`scripts/validate_evidence.py` is an **evidence integrity and completeness**
checker. It is not a semantic-legitimacy checker and not a promotion
authority. `PROMOTION_AUTHORIZED` is a constant `false` in every code path.

Exit codes carry no Fable lifecycle meaning:

```text
0  input valid and all mechanical integrity/completeness checks pass;
   warnings and manual semantic review may still remain
1  one or more mechanical integrity/completeness requirements fail
2  invalid input / unusable manifest / execution error
```

It draws a hard line between a structural fact and a semantic claim, and it
only ever emits the left-hand side:

| It may report | It must never report | Manual review token |
| --- | --- | --- |
| `REFERENCE_EXISTS` | `EXCLUSION_LEGITIMATE` | `SEMANTIC_SCOPE_JUSTIFICATION_REQUIRES_REVIEW` |
| `ARTIFACT_IDENTITY_VERIFIED` | `EVALUATION_QUALITY_VERIFIED` | `EVALUATION_QUALITY_REQUIRES_REVIEW` |
| cited identifiers resolve | source coverage is complete | `SOURCE_COVERAGE_REQUIRES_REVIEW` |
| `FALSIFICATION_ARTIFACT_VERIFIED` | the negative set is adequate | `FALSIFICATION_ADEQUACY_REQUIRES_REVIEW` |

Recorded unresolved semantics stay recorded. Internally consistent metadata is
never grounds to reinterpret an unresolved item as resolved.

## Candidate-local advisory memory

Keep durable observations next to the candidate, not in a global store. Grade
every observation:

```text
CONFIRMED   observed directly, with a citable evidence reference
INFERRED    reasoned from evidence but not observed; say what would confirm it
UNKNOWN     an acknowledged hole, named rather than quietly omitted
OBSOLETE    superseded; kept so the refinement history stays readable
```

Memory is **advisory only**. It is scoped to one candidate or skill. It cannot
override canonical semantics and it cannot authorize any action. Do not let it
grow into a general database for this workflow, and do not implement a memory
database — a plain file beside the candidate is the whole mechanism.

## Findings this Skill must not overstate

These come from three completed experiments. The canonical Skill preserves
them as stated and must not be rewritten into a stronger claim:

```text
FREEZE_FRESH_REVIEW_PATTERN:      ADOPTED
PROSPECTIVE_ASSUMPTION_DISCOVERY: SUPPORTED
MECHANICAL_VALIDATOR_ROLE:        ADVISORY_COMPLETENESS_AND_INTEGRITY
MECHANICAL_PROMOTION_AUTHORITY:   REJECTED

SELF_ATTESTATION_CAN_BE_FULLY_CLOSED_MECHANICALLY: NO
```

Two consequences worth stating plainly, because they are easy to lose:

- **Discovery came from the sequence, not from the gates.** In the prospective
  experiment the fresh reviewer found 15 material undeclared assumptions; the
  mechanical checks found none of them, and could not — a check that iterates
  declared assumptions cannot see an undeclared one. The value of this Skill is
  the freeze-then-fresh-review discipline. The checker's contribution is to
  stop a declared set from being incomplete or self-contradictory.
- **Of five candidate mechanical closures, only one was sufficient.**
  Evidence-plan internal consistency reached `SUFFICIENT` because its
  load-bearing claim *is* an internal property. Scope-exclusion integrity,
  artifact integrity, source coverage, and falsification adequacy each proved
  identity, existence, or a proxy, while the required misstatement relocated
  one self-attested field deeper. That is why four of the seven checks are
  paired with a manual-review token instead of a verdict.

## Using this Skill inside Fable

This Skill supplies the candidate-development procedure. Fable still owns the
route, the authorization gates, the verification depth, and the Judge handoff.
When a Fable Judge trigger applies to the underlying work, hand off to the
Judge as Fable directs — the advisory reviewer in step 2 does not satisfy that
trigger and never substitutes for it.
