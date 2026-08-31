# Fable report structural audit — real corpus R2

STATUS: INSUFFICIENT_REAL_CORPUS

FINAL_DISPOSITION: NEEDS_MORE_REAL_CORPUS

The existing R3/R4 detector was retained unchanged and replayed against a
de-identified real-sampled corpus derived only from tracked Git report
history. The bounded history supplied enough negative examples to reach the
numeric targets, but it supplied no naturally coexisting R3 or R4 conflict
case. Synthetic positive replacements were not created.

INTENT: code does evaluate a 68-case synthetic R1 corpus with no retained real samples; the check/task expects the unchanged R3/R4 detector to be measured against de-identified tracked report history; the opened spec says unavailable real conflict cases must not be replaced synthetically and semantic judgement must remain unimplemented.

## Scope

Chosen write root:

`analysis/fable-report-structural-audit-r2/**`

Retained artifacts:

- `corpus_spec.json`: exact Git-blob provenance and labelled source ranges.
- `verify_r2.py`: tracked-Git provenance, de-identification, fixture, replay,
  and metrics verifier.
- `fixtures/r3_real.json`: 24 de-identified R3 excerpts.
- `fixtures/r4_real.json`: 12 de-identified R4 excerpts.
- `expected/labels.json`: exact label/provenance manifest.
- `metrics.json`: deterministic separated and descriptive metrics snapshot.
- `report.md`: this report.

No canonical Fable, Planner, Judge, prompt, runtime, CI, lifecycle, or
publication file was changed. The R1 detector remains version `1.0.0-r1`,
with SHA-256
`2d120b4d1dae080c0c47c49b4fecfd951e3b7f498b10aa2338da805c121062cc`.

## Corpus discovery and class availability

[Confirmed] Bounded path and Git-object inventory found four distinct tracked
`report.md` documents and seven reachable current/historical versions at the
pinned source boundary. All seven whole-report detector audits were clean.
A manual inventory of exact R3 status observations and exact R4 terminal-field
assignments found no naturally coexisting conflict:

- no identical scoped/unscoped check surface had both `PASS` and `NOT RUN`;
- no exact direct terminal field had two different concrete values.

The real corpus therefore retains all 36 selected real negatives and no real
positive. It uses four distinct documents and all seven versions. Because the
required conflict class is absent, the result is
`INSUFFICIENT_REAL_CORPUS` even though the raw case-count targets are met.

No cross-version fragments were concatenated to manufacture a conflict. No
synthetic R1 case was relabelled as real.

## SYNTHETIC_R1_METRICS

| Rule | Cases | Expected conflict | Expected non-conflict | TP | FP | TN | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| R3_PASS_NOT_RUN_CONFLICT | 40 | 20 | 20 | 20 | 0 | 20 | 0 |
| R4_DUPLICATE_TERMINAL_FIELD_CONFLICT | 16 | 8 | 8 | 8 | 0 | 8 | 0 |
| R6_REPETITION_LOOP | 12 | 6 | 6 | 6 | 0 | 6 | 0 |

ORIGINAL_R1_REGRESSION: PASS

## REAL_SAMPLED_R2_METRICS

| Rule | Cases | Expected conflict | Expected non-conflict | TP | FP | TN | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| R3_PASS_NOT_RUN_CONFLICT | 24 | 0 | 24 | 0 | 0 | 24 | 0 |
| R4_DUPLICATE_TERMINAL_FIELD_CONFLICT | 12 | 0 | 12 | 0 | 0 | 12 | 0 |

REAL_R3_COUNTS: TP 0, FP 0, TN 24, FN 0

REAL_R4_COUNTS: TP 0, FP 0, TN 12, FN 0

The zero FN counts do not estimate real-positive sensitivity: this bounded
repository history contains no real positive class. The negative-only sample
does provide real-corpus false-positive evidence for these exact excerpts.

REAL_FALSE_POSITIVE_LEDGER: NONE

REAL_FALSE_NEGATIVE_LEDGER: NONE

All FP/FN lists were inspected; both are empty. No remediation rule was
triggered, so the detector was not modified and no regression fixture was
added as a detector-error case.

## COMBINED_DESCRIPTIVE_METRICS

| Rule | Cases | Expected conflict | Expected non-conflict | TP | FP | TN | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| R3_PASS_NOT_RUN_CONFLICT | 64 | 20 | 44 | 20 | 0 | 44 | 0 |
| R4_DUPLICATE_TERMINAL_FIELD_CONFLICT | 28 | 8 | 20 | 8 | 0 | 20 | 0 |

These are descriptive sums only. They are not a merged production
false-positive rate, a production sensitivity estimate, or a promotion
threshold.

## Provenance and de-identification

REAL_CORPUS_PROVENANCE: TRACKED_GIT_ONLY

The verifier resolves every source blob from the packet-pinned base commit's
reachable object graph, requires it to be a Git blob with exactly one tracked
`report.md` path, and checks that document aliases map one-to-one to distinct
paths. Fixtures retain only opaque document/version aliases, line ranges, and
content hashes; repository paths are not retained in excerpt text.

De-identification removes or replaces:

- usernames and absolute/home-relative local paths;
- repository task paths and Fable/task identifiers;
- commit-shaped values in excerpt text;
- runtime/provider identifiers;
- email addresses, UUIDs, request IDs, common token shapes, bearer tokens,
  and private-key headers.

SENSITIVE_CONTENT_SCAN: PASS

The fixture-text scan is enforced by `verify_r2.py` during every render and
check. No prohibited transcript store, chat export, unrelated repository, or
untracked operator record was read. Source reports can discuss prior
measurements, but only their committed report text was sampled.

## Replay and determinism

Primary check:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 \
  analysis/fable-report-structural-audit-r2/verify_r2.py check --snapshot
```

[Confirmed] Result: exit 0. It re-derives every fixture from tracked blobs,
checks de-identification and labels, replays synthetic R1, replays real R2,
and requires `metrics.json` to match.

Original synthetic replay:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 \
  analysis/fable-report-structural-audit-r1/audit_report.py corpus \
  --fixtures analysis/fable-report-structural-audit-r1/fixtures \
  --expected analysis/fable-report-structural-audit-r1/expected/labels.json
```

[Confirmed] Result: exit 0 and byte-equivalent to the retained R1 snapshot.

Real corpus replay used the same unchanged detector with the R2 fixtures and
labels. Two separate external invocations were compared in memory and were
byte-identical. Each invocation also performs the detector's two internal
complete-corpus evaluations.

- Synthetic external replay: PASS; shell-normalized 14,148 bytes; SHA-256
  `f6a82c60fed422ea9d86771109d698695354860709d017d73824dd241cf19c32`.
- Real external replay twice: PASS; shell-normalized 8,883 bytes; SHA-256
  `0935ecc56e2bc26627b5f3bb3eb83afc9fd5a191a728ae2d4a2350633a6f0007`.
- R2 snapshot replay twice: PASS; shell-normalized 2,441 bytes; SHA-256
  `e6d097af8cb4214e5e1b6f1ddc9f29aa31a206c80a5320124846ca7e79cccf9c`.

DETERMINISTIC_REPLAY: PASS

## Attempt ledger

1. A metadata-loop command was rejected by the orchestration parser before
   the shell ran because shell parameter syntax was interpolated as
   JavaScript. No repository or runtime artifact was written. Explicit
   `git ls-tree` commands replaced it and passed.
2. The first parallel fixture-render orchestration treated a still-running
   command as a failed completed command. No fixture artifact was written.
   A bounded completed render established exit 0 before retry.
3. The first generated fixture `apply_patch` could not create missing parent
   directories. No fixture file was created. The two exact allowed
   directories were created, the target state was re-read, and the same
   generated patch then applied successfully.
4. The first retained-data scan command was rejected by the orchestration
   parser because its embedded regular expression contained an unescaped
   command-delimiter character. The shell did not run and no artifact was
   written. The corrected scan then passed with zero matches.

No detector/test failure, timeout, process termination, overwritten owner
artifact, or out-of-scope cleanup occurred.

## Filesystem and Git accounting

- Worktree materialization created at the packet-authorized
  `<TASK_WORKTREE>`.
- Git branch created:
  `codex/fable-report-structural-audit-real-corpus-r2`.
- Repository files retained: the seven files listed under Scope, all within
  `analysis/fable-report-structural-audit-r2/**`.
- Files deleted before end: none.
- Python bytecode/cache artifacts: none.
- Canonical checkout's unrelated dirty/untracked owner paths: retained
  untouched.
- Git network metadata writes: none.

## Required results

ORIGINAL_R1_REGRESSION: PASS

REAL_CORPUS_PROVENANCE: TRACKED_GIT_ONLY

REAL_R3_COUNTS: TP 0, FP 0, TN 24, FN 0

REAL_R4_COUNTS: TP 0, FP 0, TN 12, FN 0

REAL_FALSE_POSITIVES: 0

REAL_FALSE_NEGATIVES: 0

SEMANTIC_JUDGEMENT: NOT_IMPLEMENTED

CANONICAL_FABLE_CHANGED: NO

GIT_DIFF_CHECK: PASS

FINAL_DISPOSITION: NEEDS_MORE_REAL_CORPUS

COMMIT_AUTHORIZED: YES

PUSH: NOT RUN — not authorized
