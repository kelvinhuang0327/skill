# Candidate lifecycle

The procedure behind [../SKILL.md](../SKILL.md). Read that file first for the
role boundary; nothing here grants authority it withholds.

```text
1 FREEZE
2 FRESH PROSPECTIVE REVIEW
3 REFINEMENT
4 EVALUATION
5 MECHANICAL INTEGRITY CHECK
6 PROMOTION-REVIEW HANDOFF
```

The order is the point. Each step exists to make a specific failure attributable
rather than arguable.

---

## 1. FREEZE

Write `candidate_v1` **before** implementation and **before** review.

Bind:

```text
candidate identity            a stable id that changes when the candidate changes
target skill                  what would be created or modified
source identity               the pinned artifact the candidate reasons about,
                              by path plus SHA-256
hypothesis                    what you expect to be true, stated so it could fail
scope                         in_scope, out_of_scope, and why the line is there
known assumptions             environment, portability, input, and semantic
planned positive evidence     what a working candidate would demonstrate
planned negative evidence     what would falsify it
known unresolved semantics    questions you cannot answer yet, named
```

Once the candidate has been reviewed, `candidate_v1` **must not be rewritten**.
A frozen candidate that can be edited after review proves nothing, because the
review can no longer be attributed to anything.

Freeze order should be observable, not asserted — record hashes of the frozen
files, and record them again after the later steps so drift is visible.

### Why freezing is the load-bearing step

The candidate is the thing under review. If it moves, three later claims quietly
become unfalsifiable: that the reviewer saw this candidate, that a refinement was
caused by a finding rather than by hindsight, and that the evidence was planned
rather than back-fitted. Freezing is cheap; reconstructing intent is not.

---

## 2. FRESH PROSPECTIVE REVIEW

Use a fresh context where practical — a reviewer with no memory of authoring the
candidate.

Give the reviewer only the minimum pinned evidence needed to find:

- omitted assumptions;
- portability and environment assumptions;
- incomplete scope;
- contradictory evidence planning;
- weak negative evidence;
- unresolved semantics.

**Withhold later outcome evidence.** If the reviewer can see how it turned out,
you are measuring hindsight.

The reviewer is **advisory**. It is **not** a Fable Judge, it does not verify
completion claims, and it does not satisfy any Fable Judge trigger. Record what
the reviewer actually read, so contamination is checkable rather than assumed
away.

### What to expect from this step

This is where undeclared assumptions are actually found. A mechanical check
iterates the assumptions you declared; it is structurally blind to the ones you
did not. In the prospective experiment the fresh reviewer found 15 material
undeclared assumptions and three outright defects in the frozen candidate — a
false coverage claim, an internally unsatisfiable evidence plan, and a negative
set a broken implementation would have passed. The mechanical checks named none
of them until a human had written them into the manifest.

Two confounds worth carrying forward honestly: the reviewer's yield depends
heavily on the audit surface it is given, and an author who knows a review is
coming writes a better-than-typical candidate. Findings are a lower bound.

---

## 3. REFINEMENT

Create `candidate_v2`, `candidate_v3`, and so on. **Never overwrite earlier
candidate history.**

Every material delta states:

```text
what changed
why it changed
which review finding or piece of evidence caused it
```

A refinement with no attributable cause is a rewrite. Keep both readable: the
history is what lets a later reviewer see whether a narrowing was a deliberate
scope decision or a quiet retreat from a failing claim.

When a review finding causes a narrowing, say so as a narrowing. "Deferred, and
here is the clause we are not covering" survives review. "Covered" for something
never evaluated does not.

---

## 4. EVALUATION

Collect the evidence appropriate to **this** candidate:

```text
positive                cases the candidate should handle
negative/falsification  cases that would expose it if it were wrong
near-miss               cases that look like failures but are not, and vice versa
determinism/regression  stability across reruns; nothing else already broken
value                   whether it is worth having, measured rather than asserted
```

**A candidate is not required to be code-backed.** A prose candidate can be
evaluated; it simply has different evidence.

**For a code-backed deterministic candidate, declared negative fixtures alone
are not sufficient evidence.** Prefer actual execution and falsification
evidence. A declared negative list describes intent; only running something
shows what the negatives catch.

That distinction was measured, not assumed. In the closure experiment a
candidate's real negative set was reported `ADEQUATE_BY_SHAPE` — every declared
negative case had a fixture. Executing a single mutation showed the mutant
survived every one of them. One case the author never wrote killed it. Shape and
execution flatly disagreed.

If your candidate is code-backed, consider mutation-testing your own evidence:
disable each check in a scratch copy outside the repository and confirm some
case fails. It is the cheapest way to find a check nothing covers.

---

## 5. MECHANICAL INTEGRITY CHECK

Run [../scripts/validate_evidence.py](../scripts/validate_evidence.py):

```bash
python3 skill-lifecycle/scripts/validate_evidence.py path/to/manifest.json
```

Add `--json` for machine-readable output, `--base-dir` to root relative paths
somewhere other than the manifest's own directory.

Use it **only for properties it can actually establish**.

### Manifest shape

```json
{
  "manifest_version": "1",
  "candidate": {
    "candidate_id": "cand-<name>-<short-hash>",
    "target_skill": "<skill this candidate is for>",
    "candidate_kind": "CODE_BACKED_DETERMINISTIC | PROSE_ONLY | OTHER",
    "frozen_revision": "candidate_v1",
    "source_identity": {
      "path": "relative/or/absolute/path",
      "sha256": "<64 hex>",
      "addressability": "MACHINE_ADDRESSABLE | NOT_MACHINE_ADDRESSABLE",
      "identifier_prefix": "L"
    }
  },
  "source_references": [
    {"reference_id": "REF1", "cited_identifier": "L8",
     "purpose": "IN_SCOPE | SCOPE_EXCLUSION", "claim": "<what is claimed>"}
  ],
  "rule_trigger_domains": {
    "L10": ["allowed_files.is_empty", "policies.merge.is_set"]
  },
  "evidence_plan": {
    "outcome_vocabulary": ["PASS", "FAIL", "NOT_APPLICABLE"],
    "fixtures": [
      {"fixture_id": "P1", "rule_id": "L10",
       "kind": "POSITIVE | NEGATIVE | NEAR_MISS",
       "condition": {"allowed_files.is_empty": false},
       "expected_outcome": "PASS"}
    ]
  },
  "claims": {"negative_falsification_evidence": true},
  "evaluations": [
    {"evaluation_id": "EVAL_FALSIFICATION", "candidate_id": "<same as above>",
     "kind": "POSITIVE | NEGATIVE_FALSIFICATION | DETERMINISM | VALUE",
     "artifact": {"path": "evidence/mutation.txt", "sha256": "<64 hex>",
                  "execution": "EXECUTED | DECLARED_ONLY"}}
  ],
  "unresolved_semantics": [
    {"item_id": "SEM1", "question": "<the open question>",
     "status": "OPEN | DEFERRED_OUT_OF_SCOPE | RESOLVED",
     "resolution_evidence_ref": "<required when RESOLVED>"}
  ]
}
```

`addressability` is a property of a specific source file, never a general
guarantee. `MACHINE_ADDRESSABLE` means the source writes its own per-clause
identifiers (for example a leading `# L8 ` comment) so a cited identifier can be
resolved. Most sources do not. Declaring it when it is not true buys nothing:
the checker will simply fail to find identifiers that exist.

`rule_trigger_domains` is optional but recommended. It restricts a fixture's
condition to that rule's declared trigger variables, which is what stops the
same real state being re-described under a second variable name to hide a
contradiction. Residual, stated rather than hidden: an author who misdeclares
the domain itself moves the defect into fixture misdeclaration, which this
check does not address.

### The seven checks

| | Check | Fails when |
| --- | --- | --- |
| A | candidate identity binding | no frozen identity or revision; an evaluation binds to a different candidate, or to none |
| B | pinned source identity | a source is cited but not pinned; a readable source hashes differently than cited |
| C | referenced artifact integrity | a cited artifact is uncited, missing, or hashes differently |
| D | evidence-plan internal consistency | incompatible outcomes at an identical condition; one fixture id with conflicting definitions; an outcome outside the declared vocabulary; a condition variable outside the declared domain |
| E | machine-addressable source references | a cited identifier does not exist in an addressable source, or a reference cites nothing |
| F | negative/falsification completeness | a code-backed candidate **claims** falsification evidence and cites no executed, verifiable artifact |
| G | unresolved semantics preservation | an item is marked `RESOLVED` with no resolution evidence reference |

Check D is the only one allowed to be load-bearing. Its claim is an internal
property of the plan — joint satisfiability is not a proxy for some external
fact, it *is* the fact. The other six are integrity checks whose semantic
counterpart is explicitly deferred to a reviewer.

Check F is triggered by the manifest's **own claim**. A candidate that makes no
falsification claim is not failed for lacking one; the requirement is "back the
claim you made".

An unreadable pinned source in this environment is `WARN`, not `FAIL` —
validation environments legitimately differ. It is also not a verification, and
`EVIDENCE_INTEGRITY` degrades to `PARTIAL_UNVERIFIABLE_IN_THIS_ENVIRONMENT` to
say so.

### Output contract

Stable field order, every list sorted:

```text
STATUS                     INTEGRITY_COMPLETE | INTEGRITY_INCOMPLETE | INPUT_UNUSABLE
CANDIDATE_ID
EVIDENCE_INTEGRITY         VERIFIED | PARTIAL_UNVERIFIABLE_IN_THIS_ENVIRONMENT | NOT_ESTABLISHED
PROMOTION_AUTHORIZED       false, always
FAILED_CHECKS
WARNINGS
MANUAL_REVIEW_REQUIRED
STRUCTURAL_FACTS_VERIFIED
```

No clock, no randomness, no network. The same manifest renders byte-identically
on every run, and input array order and condition key order do not affect
output order.

### What it cannot establish

Four boundaries, each backed by a specific experimental result. A stronger
implementation of any of them was built and attacked; in each case the required
misstatement relocated one self-attested field deeper rather than disappearing.

- **A cited identifier existing does not make an exclusion legitimate.** A real
  rule about cleanup was cited to exclude a decision that was really about a
  different clause. Referential integrity accepted it. Adding containment of a
  self-declared anchor caught it — until the anchor was moved by one number, at
  which point every level accepted it again. Emits `REFERENCE_EXISTS` plus
  `SEMANTIC_SCOPE_JUSTIFICATION_REQUIRES_REVIEW`; never `EXCLUSION_LEGITIMATE`.
- **A matching hash does not make an evaluation real.** Two artifacts were built
  that both existed and both hashed exactly as cited: one a transcript of a real
  run, one typed by hand asserting `PASS`. The fabricated one was larger and more
  detailed. The integrity verdicts were identical. Emits
  `ARTIFACT_IDENTITY_VERIFIED` plus `EVALUATION_QUALITY_REQUIRES_REVIEW`; never
  `EVALUATION_QUALITY_VERIFIED`.
- **Resolving every cited identifier does not prove coverage.** Clause identity
  at line granularity did catch a real omission and named the line. Below that,
  three defensible delimiter policies disagreed with each other on four of
  twelve lines and none reproduced the author's own decomposition. There is no
  canonical sub-line clause identity to derive. Always emits
  `SOURCE_COVERAGE_REQUIRES_REVIEW` rather than fabricating completeness.
- **A verified falsification artifact is not an adequate falsification set.**
  Identity is settled by a hash; adequacy needs an implementation to mutate and
  a mutant someone thought to name. Emits `FALSIFICATION_ARTIFACT_VERIFIED` plus
  `FALSIFICATION_ADEQUACY_REQUIRES_REVIEW`, and keeps the two separate.

---

## 6. PROMOTION-REVIEW HANDOFF

The lifecycle may conclude:

```text
READY_FOR_PROMOTION_REVIEW
```

This means only that the candidate and evidence package are prepared for the
applicable Owner / Fable / Judge decision. It does not mean
`PROMOTION_AUTHORIZED`, `PROMOTION_VERIFIED`, `CANONICAL`, or `DEPLOYED`.

Hand over:

```text
the frozen candidate_v1 and every later revision, unrewritten
the review record, including what the reviewer was given
the refinement history with causes attributed
the evidence artifacts, cited by path and hash
the validator output, including every MANUAL_REVIEW_REQUIRED item
the unresolved semantics, still unresolved
the confounds and the limits of what was actually shown
```

Outstanding `MANUAL_REVIEW_REQUIRED` items are the handoff's content, not a
defect in it. Clearing them is the reviewer's job, and a package that arrives
with none of them is more likely to be hiding them than to have earned it.

### Avoiding a circular promotion argument

Evidence that exists only *because* the candidate was promoted cannot support
promoting it. Keep pre-promotion evidence and post-promotion outcome separate,
and check that the recommendation still stands with the post-promotion half
removed. The pilot made this structural rather than a matter of reviewer
discipline: stripping the post-promotion block entirely and asserting the
verdict is unchanged.

---

## Candidate-local advisory memory

Keep durable observations in a plain file beside the candidate. Grade each one:

```text
CONFIRMED   observed directly, with a citable evidence reference
INFERRED    reasoned from evidence but not observed; say what would confirm it
UNKNOWN     an acknowledged hole, named rather than quietly omitted
OBSOLETE    superseded; kept so the refinement history stays readable
```

Each observation records what it applies to, the finding, and an evidence
reference.

The contract:

```text
advisory only:                          YES
scope:                                  one candidate or skill
can override canonical semantics:       NO
can authorize any action:               NO
should become a global database:        NO
```

Do not implement a memory database. No index, no vector store, no service — a
file next to the candidate is the whole mechanism. The moment it becomes
infrastructure it acquires an authority this Skill is not allowed to hold.

`INFERRED` and `UNKNOWN` are the useful grades and the ones under pressure to
disappear. An `UNKNOWN` recorded honestly is what lets a later reviewer see a
hole; the same hole silently omitted reads as a confirmed absence.

---

## Stop conditions

Stop and report rather than working around any of these:

```text
NEW_AUTHORITY_LAYER_REQUIRED     the candidate needs someone to adjudicate;
                                 this Skill cannot be that someone
NEW_DEPENDENCY_REQUIRED          the checker is standard library only
PROMOTION_AUTHORITY_REQUIRED     escalate to the Owner; never self-grant
CANDIDATE_HISTORY_WOULD_BE_LOST  a refinement that needs to overwrite frozen
                                 history is not a refinement
```
