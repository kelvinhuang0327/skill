# Control Plane V1 Promotion Decision Packet

Status: `WAITING_FOR_OWNER_DECISION`

This packet presents evidence and options only. It does not authorize or perform promotion, activation, deprecation, replacement, publication, or a change to local `main`.

## A. Candidate Identity

- Integration branch: `codex/control-plane-v1-pilot2-integration-evidence-promotion-r1`
- Integration merge commit: `05cc9d8715fa5a2c986d4a9996b0436c81977d91`
- Integration merge tree: `64897e43b4047013ec971b9f075ba8109013de20`
- Evidence/decision commit: reported in the final handoff after this packet is committed; its self-referential SHA cannot be embedded in its own contents
- Control-plane version: `1.1.0-draft.1`
- Manifest schema version: `1`
- Durable-source fingerprint: `1c247dae7630dfc646e2ac7e4fd0f923bb729c4390f4f5d627d89997a83d8d91`

## B. Four VNext Prompt Identities

| Prompt | Bytes | SHA-256 | Markers | Deterministic generation |
|---|---:|---|---|---|
| `compiled/HANDOFF_REPORTER.compiled.md` | 6,485 | `335ee4585a9ab643323290dca2748872b65c20cc1071ed0bd42af6fdefc0f351` | `GENERATED_BUILD_OUTPUT`, `DRAFT_FOR_OWNER_REVIEW`, `DO_NOT_EDIT` | `PASS — fresh bytes equal committed output` |
| `compiled/CEO_DECISION_REVIEW.compiled.md` | 7,105 | `6ec1c0588b87859943bd72196c9b6ab99209e8f7191e2c2e0d0d156372bf3abb` | `GENERATED_BUILD_OUTPUT`, `DRAFT_FOR_OWNER_REVIEW`, `DO_NOT_EDIT` | `PASS — fresh bytes equal committed output` |
| `compiled/CTO_TECHNICAL_REVIEW.compiled.md` | 8,654 | `6b94fb1c3ac984cbc18eafe2b56cc33a3a33e93653a2b015f4890823038a5751` | `GENERATED_BUILD_OUTPUT`, `DRAFT_FOR_OWNER_REVIEW`, `DO_NOT_EDIT` | `PASS — fresh bytes equal committed output` |
| `compiled/PLANNER_COMPILER.compiled.md` | 21,888 | `433295b9eb7a97c32b112e80484aa7ba69313f5ad2dc33560f51fe0fe6dc4d84` | `GENERATED_BUILD_OUTPUT`, `DRAFT_FOR_OWNER_REVIEW`, `DO_NOT_EDIT` | `PASS — fresh bytes equal committed output` |

All four role validators and individual/aggregate size gates pass. Aggregate size is `44,132 / 53,830` bytes and remains under the preferred `50,000`-byte target.

## C. Legacy Prompt Identities

| Legacy prompt | Accepted SHA-256 |
|---|---|
| Handoff | `11d7bbd9138caf19c41f2f94f250818a21e1b15a050610e254dbd0f9ea38f846` |
| CEO | `bbedaa3dc4652eae5b376a042d6cac99ac50182de025caf934f69afe9da1a695` |
| CTO | `5611f4eda4b184a506f661581c6563f45e110b47cb7ff3fe97935e29bf014dae` |
| Planner | `9bb4bd4682e30106ac1822f159742b25dcd9b9c5855c9b7414839b063a4f2743` |

The four legacy prompts are unchanged, outside the repository, still the authoritative fallback, and not deprecated.

## D. Validation History

- Mechanical compiler validation established deterministic manifest lint/render/projection behavior, L1–L22 rejection coverage, role compilation, and fresh-output equality.
- Four-role semantic review produced the role-boundary and compatibility mapping; prompt minification retained hard role-size gates.
- Targeted semantic repair commit `7d646b3f2d7978c92cb1b69a69033260b500295f` restored candidate-memory, active-task projection, attachment, and authorization boundaries.
- Pilot 1 LOW read-only result: `CONTROL_PLANE_V1_PILOT1_SELF_HOSTED_READ_ONLY_PASS_WITH_RISKS`; durable evidence is `pilot1/self-hosted/pilot1-result.md`.
- Pilot 2 MEDIUM mutation workflow result: `CONTROL_PLANE_V1_PILOT2_TEST_PORTABILITY_PASS`; both locale suites and all fail-closed legacy-input checks pass.
- Independent fixed-head Pilot 2 review: `PASS` at reviewed execution commit `12f056d85c9bdab3c09a28a9c46ce4a6393afc9e`.

## E. Role and Workflow Readiness

| Capability | Evidence-supported status |
|---|---|
| Handoff → Planner → Worker → Reviewer | `PARTIALLY VALIDATED`: role generation/boundaries are mechanically validated; Pilot 2 exercised Worker plus independent Reviewer, but a full external multi-project chain has not run. |
| Technical escalation through CTO | `MECHANICALLY VALIDATED`: routing, attachment, authorization, and role-semantic guards pass; no live technical-escalation Pilot has run. |
| Strategic escalation through CEO/Owner | `MECHANICALLY VALIDATED`: role boundaries and authorization escalation pass; no STANDALONE/activation Pilot has run. |
| `NONE`, `SINGLE_PROMPT`, `STANDALONE` | Rendering/lint tests cover all three. Pilot 1 exercised `NONE`; Pilot 2 exercised `SINGLE_PROMPT`; `STANDALONE` has not been operationally exercised. |
| `NOT_APPLICABLE`, `REUSABLE` worktrees | Pilot 1 exercised `NOT_APPLICABLE`; Pilot 2 exercised local `REUSABLE` implementation. This integration's clean restoration must be verified in the final handoff. |
| Local commit and clean restoration | Supported for the two local Pilots and this integration lifecycle; no remote lifecycle was used. |
| Memory/evidence precedence | Mechanically validated and reflected in append-only durable evidence; future live state remains authoritative and adversarial multi-project adoption is untested. |

No untested route is claimed as fully validated.

## F. Remaining Risks

- No remote or remote CI exists; the repository and all verification are local-only.
- Legacy replacement has not been tested.
- Activation rollback has not been tested.
- The Planner compiled prompt has only `31 bytes` of hard-gate headroom (`21,888 / 21,919`).
- Optional formatting and portability caveats remain; notably, a reviewer-authored inline probe was invalid under locale `C`, while the required file-based checks passed.
- Pilot 3 has not run.
- External multi-project attachment and adoption have not been tested.

## G. Promotion Options

### Option A — Shadow Promotion

Recommended default.

- VNext compiled prompts become preferred/default candidates.
- Legacy prompts remain available as fallback.
- No deletion or deprecation yet.
- Run two normal project tasks in shadow mode.
- Compare VNext output with legacy expectations.
- Control plane remains `DRAFT_FOR_OWNER_REVIEW` or equivalent shadow status.

### Option B — Formal Local Promotion

- VNext compiled prompts become the official local defaults.
- Legacy prompts receive deprecation banners but remain preserved.
- Control-plane status changes from draft to locally active.
- Requires separate standalone Owner authorization because it changes the adopted operational process.
- No remote publication.

### Option C — Continue Evaluation

- Keep current draft/fallback arrangement.
- Run another Pilot or multi-project attachment test.
- No promotion.

### Option D — Reject Promotion

- Keep legacy prompts as official.
- Preserve VNext artifacts for future analysis.
- No deletion.

## H. Recommended Decision

Recommend **Option A — Shadow Promotion** because both local Pilots and the deterministic portability gates passed, while remote CI, rollback, STANDALONE execution, and multi-project adoption remain untested. This recommendation is not an authorization or promotion decision.

## I. Exact Actions Requiring Future Authorization

- Integrating the final candidate branch into local `main`.
- Selecting Option A, B, C, or D.
- Changing draft or active status.
- Modifying legacy prompts.
- Deprecating legacy prompts or legacy workflows.
- Activating the control plane or an executable registry entry.
- Publishing to a remote or any external system.
