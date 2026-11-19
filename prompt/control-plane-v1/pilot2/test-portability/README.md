# Pilot 2 Test Portability

Pilot 2 task `CONTROL_PLANE_V1_PILOT2_TEST_PORTABILITY_REPAIR_R1` executed and passed. Durable evidence is recorded in `pilot2/test-portability/pilot2-result.md`.

## Bound package

- Classification: `CONTROL_PLANE_V1_PILOT2_TEST_PORTABILITY_PASS`
- Manifest: `pilot2/test-portability/manifest-medium.yaml`
- Exact base: `31d24f4cc0f6a730a781889c56e4881c04acd7d4`
- Reviewed execution commit: `12f056d85c9bdab3c09a28a9c46ce4a6393afc9e`
- Integration branch: `codex/control-plane-v1-pilot2-integration-evidence-promotion-r1`
- Integration merge commit: `05cc9d8715fa5a2c986d4a9996b0436c81977d91`
- Integration merge tree: `64897e43b4047013ec971b9f075ba8109013de20`
- Reusable worktree: `/Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1-agent`
- Implemented paths, exactly:
  - `prototype/test_control_plane.rb`
  - `prototype/README.md`

The manifest is retained byte-for-byte as the historical preparation record. Its `PENDING_OWNER_TOKEN` and `NOT_RUN` fields describe that earlier preparation state and are superseded for result status by the durable evidence artifact; the manifest is not rewritten into a post-execution policy source.

## Verification result

- `C.UTF-8`: `38 runs / 1,853 assertions / 0 failures / 0 errors / 0 skips`
- `C`: `38 runs / 1,853 assertions / 0 failures / 0 errors / 0 skips`
- Fail-closed negative checks: missing-all, missing-one, byte-count mismatch, line-count mismatch, and SHA-256 mismatch all returned nonzero with their exact diagnostic category.
- No legacy input was skipped, substituted, synthesized, or silently accepted.

## Current boundary

The result is local-only. No remote, push, PR, DB, data, project runtime, network, activation, active-task projection, or legacy replacement occurred. The four external legacy prompts remain unchanged and authoritative fallback. Promotion requires a separate Owner decision; this result does not authorize it.
