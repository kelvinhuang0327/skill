# Fable report structural audit pilot R1

Offline, advisory pilot for mechanically provable report contradictions. It is
not integrated into Fable runtime, Planner, Judge, CI, lifecycle, or
publication, and it does not block any action.

INTENT: code does not provide this report audit; the check/task expects an offline advisory detector for mechanically provable R3/R4 contradictions and obvious R6 repetition; the opened spec says semantic evidence judgement must remain unimplemented and runtime/CI/lifecycle integration is forbidden.

## Scope and implementation

All retained changes are under
`analysis/fable-report-structural-audit-r1/**`:

- `audit_report.py`: standard-library-only detector and corpus runner.
- `fixtures/r3.json`: 40 labelled R3 cases.
- `fixtures/r4.json`: 16 labelled R4 cases.
- `fixtures/r6.json`: 12 labelled R6 cases.
- `expected/labels.json`: independent exact label/provenance manifest.
- `metrics.json`: deterministic captured corpus output.
- `report.md`: this report.

No canonical Fable source, prompt, Planner, Judge, CI, lifecycle, or
publication file was changed.

## Mechanically bounded rules

### R3_PASS_NOT_RUN_CONFLICT

The detector records a status only from one of four explicit structures:

1. a label/value line;
2. an explicit check/scope/status block;
3. a Markdown table with recognized check/surface and status headers, plus an
   optional scope header; or
4. tightly constrained quoted English or Chinese status prose.

Identity is Unicode-NFKC normalized, case-folded, and whitespace-collapsed.
Punctuation and all other name content remain significant. Explicit scope is
part of identity: the same check name under different scopes is different,
and scoped and unscoped observations are not merged. English status spelling
and the explicitly mapped Chinese equivalents are recognized mechanically.
Fenced code is excluded. No fuzzy matching, substring matching, synonym
matching, or vague-prose inference is used.

### R4_DUPLICATE_TERMINAL_FIELD_CONFLICT

The detector considers only direct uppercase key/value assignments to an
explicit terminal field: suffixes `_STATUS`, `_VERDICT`, or `_DISPOSITION`,
plus the exact fields `VERDICT`, `FINAL_DISPOSITION`, and
`FULL_PR_LIFECYCLE_CLOSED`. A finding requires at least two different
normalized concrete values for the same exact field. Alternatives containing
`|`, tables, free-form prose, and fenced code are excluded.

### R6_REPETITION_LOOP

R6 is advisory only. It flags either at least eight consecutive identical
non-structural lines, or a consecutive two-to-four-line block repeated at
least five times. Unicode and whitespace normalization are mechanical.
Markdown headings, tables, list examples, block quotes, thematic separators,
short lines, and fenced code are excluded. It does not assess ordinary
repeated headings, tables, or examples as degeneration.

## Corpus and provenance

Every case has a provenance label in its fixture and a matching entry in the
independent expected manifest.

| Provenance | Count |
|---|---:|
| SYNTHETIC | 68 |
| REAL_SAMPLED_DEIDENTIFIED | 0 |

These results measure detector correctness on this labelled corpus. Because
the corpus is entirely synthetic, they do not measure real-world production
false-positive rate or evidence quality.

| Rule | Cases | Expected conflict | Expected non-conflict | TP | FP | TN | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| R3_PASS_NOT_RUN_CONFLICT | 40 | 20 | 20 | 20 | 0 | 20 | 0 |
| R4_DUPLICATE_TERMINAL_FIELD_CONFLICT | 16 | 8 | 8 | 8 | 0 | 8 | 0 |
| R6_REPETITION_LOOP | 12 | 6 | 6 | 6 | 0 | 6 | 0 |

R3 includes tables, prose, status blocks, English/Chinese, similar-but-
different checks, repeated names under different scopes, fenced examples,
and benchmark/file names containing status-like text. R4 includes direct
conflicts and non-conflicting same-value, different-field, fenced, prose,
table, alternatives-list, and non-terminal-field controls. R6 includes six
obvious degenerations and six structural/below-threshold/progressing
controls.

Targeted feature replay results:

| Feature group | Correct cases |
|---|---:|
| Code fences | 4/4 |
| Tables | 9/9 |
| Multilingual | 9/9 |
| Similar names or explicit scopes | 5/5 |
| Status-like benchmark/file names | 3/3 |

## Reproduction and deterministic replay

```bash
PYTHONDONTWRITEBYTECODE=1 python3 audit_report.py corpus \
  --fixtures fixtures \
  --expected expected/labels.json
```

The corpus command independently evaluates the complete corpus twice before
emitting output. It was also invoked twice as separate commands; both outputs
were byte-identical to one another and to `metrics.json`.

- Captured bytes: 14,149
- SHA-256: `8bcbae4d6b78b804da3b0145c891729a68b2c047db2df4b0f727746c7cc8fd1c`
- Complete internal corpus replays per command: 2
- Additional external complete-corpus command runs compared: 2

## Tool behavior

Observed CLI results:

| Invocation class | Exit | Observed result |
|---|---:|---|
| Finding present | 0 | One R3 finding reported |
| Clean | 0 | Zero findings reported |
| Invalid invocation | 2 | Rejected by argument parser |
| Unreadable report path | 2 | Rejected as unreadable regular file |
| Invalid UTF-8 input | 2 | Rejected as unsafe decoding |

Inputs are read with strict UTF-8 decoding, capped at 5 MiB, and rejected if
they contain a NUL byte. Findings remain advisory and never produce a
nonzero audit exit.

## Attempt ledger

1. The first shell harness invocation did not reach the detector because it
   assigned zsh's read-only `status` variable. Exit 1; no artifact was
   written, overwritten, or deleted.
2. The corrected harness used the task-specific `audit_exit` variable. Exit
   0; the complete corpus passed and superseded the failed harness attempt.

## False-positive and false-negative ledgers

FALSE_POSITIVE_LEDGER: NONE

R3 FP = 0 and R4 FP = 0. There were no false positives to classify manually.
The R6 targeted advisory corpus also had FP = 0.

FALSE_NEGATIVE_LEDGER: NONE

R3 FN = 0 and R4 FN = 0. There were no false negatives to classify manually.
The R6 targeted advisory corpus also had FN = 0.

## Safety and limitations

The fixture and expected-label files were scanned for local absolute paths,
email addresses, UUIDs, common secret-token shapes, private-key headers, and
known local identifiers. Four files were scanned and zero matches were found.
No transcript store or unrelated conversation was read to build the corpus.

The detector is intentionally conservative. Reports outside its explicit
grammar can yield false negatives; that is preferable here to semantic
identity inference. It does not decide whether a verifier or coverage is
sufficient, whether a Judge verdict is credible, or whether evidence is
high-quality. It does not inspect hidden reasoning or emotional/state
markers. No promotion threshold was applied.

## Required results

R3_TP: 20
R3_FP: 0
R3_TN: 20
R3_FN: 0

R4_TP: 8
R4_FP: 0
R4_TN: 8
R4_FN: 0

R6_RESULT: PASS — 12/12 targeted cases correctly classified; advisory only.

DETERMINISTIC_REPLAY: PASS

SENSITIVE_CONTENT_SCAN: PASS

GIT_DIFF_CHECK: PASS

SEMANTIC_JUDGEMENT: NOT_IMPLEMENTED

CANONICAL_FABLE_CHANGED: NO

FINAL_DISPOSITION: WORTH_LARGER_CORPUS

The rules are suitable to retain as a non-blocking offline pilot on this
synthetic corpus. A larger de-identified corpus is required before making any
claim about production false-positive behavior.

PUSH: NOT RUN
