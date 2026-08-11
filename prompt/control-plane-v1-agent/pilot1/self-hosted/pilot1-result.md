# Self-Hosted Pilot 1 Durable Evidence

## Result

- Status: `CONTROL_PLANE_V1_PILOT1_SELF_HOSTED_READ_ONLY_PASS_WITH_RISKS`
- Execution target: `/Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1`
- Integrated commit: `c7718c9bf6074a579efa3935104460a35f9e50aa`
- Integrated tree: `c6cdd97e934deb9776639305ae86b179f3d2f39e`
- Pilot task ID: `CONTROL_PLANE_V1_PILOT1_SELF_HOSTED_READ_ONLY_ATTACHMENT`
- Original Pilot execution date/time: `UNKNOWN` — the accepted Worker handoff timestamp was not supplied to this evidence-recording task.
- Load-bearing verification time: `2026-07-18T15:31:25+08:00`

## Evidence provenance

- Original Pilot execution evidence: `source = Worker handoff; accepted by the Owner task specification; not independently re-executed by this evidence-recording task`.
- Current load-bearing verification: `source = CONTROL_PLANE_V1_PILOT1_DURABLE_EVIDENCE_RECORD_R1 commands observed at c7718c9bf6074a579efa3935104460a35f9e50aa`.
- The complete Pilot conversation flow was not rerun. Current verification covers the load-bearing local identities, validation, rendering, attachment binding, and canonical no-mutation boundary required to record the accepted result.

## Pilot matrix

| Area | Accepted Pilot result | Original evidence source | Current load-bearing verification |
|---|---|---|---|
| Attachment discovery | `PASS` | Worker handoff | `PASS` — profile plus four required context files exist and resolve project-locally. |
| Phase 0A live state | `PASS` | Worker handoff | `PASS` — canonical `main` is clean at the integrated commit and tree; no remote or Git lock exists. |
| Phase 0B context and memory | `PASS` | Worker handoff | `PASS` — attachment version/schema resolve; memory retrieval is bounded to 3 and writes are forbidden by the Pilot manifest. |
| Manifest resolution | `PASS` | Worker handoff | `PASS` — schema and lint L1–L22 reproduced; shape is `LOW / FAST / NONE / NOT_APPLICABLE`. |
| Precedence behavior | `PASS` | Worker handoff | `NOT_RUN` — the complete Pilot conversation flow was deliberately not rerun; live-state precedence remains required by `AGENT_CORE §13`. |
| No-mutation proof | `PASS` | Worker handoff | `PASS` — canonical `main` stayed clean and byte-identical during current load-bearing validation. |

## Manifest and attachment verification

- Manifest schema: `PASS`
- Manifest lint: `L1–L22 PASS`
- Task shape: `LOW / FAST / NONE / NOT_APPLICABLE`
- Control-plane version: `1.1.0-draft.1`
- Schema version: `1`
- Required context files: `4 / 4 present`
- Bounded memory maximum: `3`
- Memory write: `forbidden`
- `active_task` projection: `disabled`

## Rendered Worker prompt

- Rendered bytes: `15,505`
- SHA-256: `230fbcd9a4dcef08a386e1f08e9a5a0b77fe2383ba95c7954fd999a68d0b9082`
- Two-render byte equality: `PASS`
- Temporary render files: automatically removed

## Regression verification

- Command: `LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby prototype/test_control_plane.rb`
- Result: `34 runs, 1,814 assertions, 0 failures, 0 errors, 0 skips`
- Evidence head: `c7718c9bf6074a579efa3935104460a35f9e50aa`

## No-mutation and side-effect record

- Original Pilot before/after no-mutation result: `PASS`; `source = Worker handoff; not independently re-executed in this recording task`.
- Cross-project access during the valid fresh Pilot execution: `NONE`; `source = Worker handoff`.
- Writes during Pilot: `NONE`; `source = Worker handoff`.
- Current evidence-recording verification observed canonical `main` before and after at commit `c7718c9bf6074a579efa3935104460a35f9e50aa`, tree `c6cdd97e934deb9776639305ae86b179f3d2f39e`, clean and without a remote.
- Current task used no project DB, data, runtime, registry, deployment, network, or external side effect.

Authorized lifecycle mutations outside the read-only Pilot were:

- local `main` fast-forward from `7d646b3f2d7978c92cb1b69a69033260b500295f` to `c7718c9bf6074a579efa3935104460a35f9e50aa`;
- reusable-worktree detach/alignment to `c7718c9bf6074a579efa3935104460a35f9e50aa`.

## Remaining risks

- No remote CI exists; verification is local only.
- This durable evidence was recorded after the original Pilot execution.
- A platform commentary preamble occurred before the original Pilot's first tool action but performed no filesystem access.
- Memory keyword normalization remains not mechanically specified.
- The original Worker-handoff execution timestamp was not supplied to this evidence-recording task and remains `UNKNOWN`.

## Explicit non-claims

- Pilot 2 was not run and is not authorized by this record.
- The control plane is not activated.
- Legacy prompts are not replaced or deprecated and remain the fallback.
- This record makes no production-readiness claim.
- Historical memory does not replace live-state verification; future current-state checks remain required.

## Integrity pins

- Durable-source fingerprint: `1c247dae7630dfc646e2ac7e4fd0f923bb729c4390f4f5d627d89997a83d8d91`
- Legacy Handoff SHA-256: `11d7bbd9138caf19c41f2f94f250818a21e1b15a050610e254dbd0f9ea38f846`
- Legacy CEO SHA-256: `bbedaa3dc4652eae5b376a042d6cac99ac50182de025caf934f69afe9da1a695`
- Legacy CTO SHA-256: `5611f4eda4b184a506f661581c6563f45e110b47cb7ff3fe97935e29bf014dae`
- Legacy Planner SHA-256: `9bb4bd4682e30106ac1822f159742b25dcd9b9c5855c9b7414839b063a4f2743`
