# Pilot 2 Test Portability Durable Evidence

## Result

- Classification: `CONTROL_PLANE_V1_PILOT2_TEST_PORTABILITY_PASS`
- Pilot task: `CONTROL_PLANE_V1_PILOT2_TEST_PORTABILITY_REPAIR_R1`
- Integration branch: `codex/control-plane-v1-pilot2-integration-evidence-promotion-r1`
- Load-bearing verification time: `2026-07-18T18:09:22+08:00`
- Canonical base: `31d24f4cc0f6a730a781889c56e4881c04acd7d4`
- Preparation commit: `8a5607f53f7274d2d57beaa98303b3634bff67db`
- Reviewed execution commit: `12f056d85c9bdab3c09a28a9c46ce4a6393afc9e`
- Integration merge commit: `05cc9d8715fa5a2c986d4a9996b0436c81977d91`
- Integration merge tree: `64897e43b4047013ec971b9f075ba8109013de20`
- Merge parents, in order: `8a5607f53f7274d2d57beaa98303b3634bff67db`, `12f056d85c9bdab3c09a28a9c46ce4a6393afc9e`

## Evidence provenance

- Worker execution report: `source = current Owner task specification carrying forward the accepted Worker report; historical source, not treated as current-head proof`.
- Independent fixed-head review: `PASS`; `source = current Owner task specification`.
- Current load-bearing evidence: commands observed on the combined integration branch at merge head `05cc9d8715fa5a2c986d4a9996b0436c81977d91`.
- Current combined verification independently reran the full suites, manifest/schema/lint gates, role validators, generation equality, integrity identities, and controlled negative cases.

## Implementation scope

The reviewed execution commit changes exactly:

- `prototype/test_control_plane.rb`
- `prototype/README.md`

The combined tree adds the two preparation files and changes only those two implementation paths relative to canonical base.

## Locale matrix

| Locale | Command | Result at merge head |
|---|---|---|
| `C.UTF-8` | `LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby prototype/test_control_plane.rb` | `PASS — 38 runs, 1,853 assertions, 0 failures, 0 errors, 0 skips` |
| `C` | `LC_ALL=C LANG=C ruby prototype/test_control_plane.rb` | `PASS — 38 runs, 1,853 assertions, 0 failures, 0 errors, 0 skips` |

## Fail-closed negative matrix

| Case | Required result | Observed diagnostic |
|---|---|---|
| All legacy prompts missing | Nonzero | `missing legacy prompt` × 4 |
| One legacy prompt missing | Nonzero | `missing legacy prompt` × 1 |
| Byte-count mismatch | Nonzero | `byte count mismatch` × 1 |
| Line-count mismatch | Nonzero | `line count mismatch` × 1 |
| SHA-256 mismatch | Nonzero | `SHA-256 mismatch` × 1 |

The repository test selector for the documented negative cases also passed: `4 runs, 49 assertions, 0 failures, 0 errors, 0 skips`.

## Combined validation

- Strict UTF-8 manifest read: `PASS`
- YAML parse and schema validation: `PASS`
- Lint L1–L22: `PASS`
- Manifest shape: `MEDIUM / FAST / SINGLE_PROMPT / REUSABLE`
- Exact two-path implementation scope, bounded memory read, forbidden memory write, no DB/network/external side effect, and independent review requirement: `PASS`
- Ruby syntax for compiler and tests: `PASS`
- All tracked YAML: `12 files parsed — PASS`
- Four compiled-role validators: `PASS`
- Role and aggregate size gates: `PASS`; Planner is `21,888 / 21,919` bytes
- Fresh compiled-output equality: `PASS`
- `git diff --check`: `PASS`
- Durable policy sources and compiled role outputs versus canonical base: byte-identical

## Fail-closed guarantees

- Missing or mismatched legacy inputs never cause a skip.
- No external legacy prompt is substituted, synthesized, or silently accepted.
- The four external legacy files remain the authoritative inputs.
- SHA-256 is calculated from raw file bytes.
- Controlled negative fixtures exist only in automatically removed operating-system temporary directories and do not mutate repository sources.

## Scope and lifecycle

- Pilot 2 implementation consists of one execution commit and received an independent fixed-head `PASS` review.
- The integration merge is automatic and conflict-free with the required parent order.
- Reusable-worktree restoration is the prescribed post-evidence-commit lifecycle action. This artifact does not pre-claim that later Git action; the final handoff supplies its live command/ref evidence.
- No remote exists; no fetch, push, GitHub repository, or PR was used.
- No repository outside the two authorized control-plane paths was accessed.
- No DB, data, project runtime, registry, deployment, network, publication, activation, or legacy replacement occurred.

## Integrity pins

- Durable-source fingerprint: `1c247dae7630dfc646e2ac7e4fd0f923bb729c4390f4f5d627d89997a83d8d91`
- Legacy Handoff: `11,830 bytes`, `487 lines`, SHA-256 `11d7bbd9138caf19c41f2f94f250818a21e1b15a050610e254dbd0f9ea38f846`
- Legacy CEO: `16,679 bytes`, `709 lines`, SHA-256 `bbedaa3dc4652eae5b376a042d6cac99ac50182de025caf934f69afe9da1a695`
- Legacy CTO: `14,041 bytes`, `593 lines`, SHA-256 `5611f4eda4b184a506f661581c6563f45e110b47cb7ff3fe97935e29bf014dae`
- Legacy Planner: `29,226 bytes`, `1,090 lines`, SHA-256 `9bb4bd4682e30106ac1822f159742b25dcd9b9c5855c9b7414839b063a4f2743`

## Remaining risks

- Verification is local-only; no remote CI exists.
- One reviewer-authored inline probe was invalid under locale `C`. It is excluded because the required file-based checks passed; current verification reproduced that wrapper limitation before rerunning the actual subprocess checks correctly.
- Promotion has not been approved.
- The control plane remains inactive and the legacy prompts remain fallback.

## Explicit non-claims

- Pilot 3 was not run.
- No promotion or activation occurred.
- No legacy prompt was replaced, deprecated, moved, or modified.
- This evidence makes no production-readiness claim.
- Historical reports and memory do not replace future live-state verification.
