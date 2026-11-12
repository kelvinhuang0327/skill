# Self-Hosted Pilot 1 — PASS_WITH_RISKS

Pilot 1 executed and passed with risks. Durable evidence is recorded at `pilot1/self-hosted/pilot1-result.md` and bound to integrated commit `c7718c9bf6074a579efa3935104460a35f9e50aa`. The control plane remains inactive, and the legacy prompts remain the active fallback.

## Package

- Manifest: `pilot1/self-hosted/manifest-low.yaml`
- Target: `/Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1`
- Task: `CONTROL_PLANE_V1_PILOT1_SELF_HOSTED_READ_ONLY_ATTACHMENT`
- Shape: `LOW / FAST / NONE / NOT_APPLICABLE`
- Result: `CONTROL_PLANE_V1_PILOT1_SELF_HOSTED_READ_ONLY_PASS_WITH_RISKS`
- Evidence: `pilot1/self-hosted/pilot1-result.md`
- Commit binding: `c7718c9bf6074a579efa3935104460a35f9e50aa`

## Recorded status

1. Local `main` is clean at `c7718c9bf6074a579efa3935104460a35f9e50aa`.
2. The five repo-local `.ai` attachment files are present and project-local.
3. Profile version `1.1.0-draft.1` and schema version `1` resolve against the five durable sources.
4. The manifest passes schema validation and lint L1–L22.
5. Memory retrieval is bounded and memory write is forbidden.
6. The four compiled role prompts match fresh compilation and the durable-source fingerprint.
7. `active_task` projection remains disabled while manifest-SHA protection remains part of Shared Core policy.
8. The rendered Worker prompt is deterministic and strictly read-only.
9. Pilot 1 passed with risks; Pilot 2 requires separate Owner authorization.

## Reproduction command

From the repository root, reproduce the exact Worker prompt with:

```sh
LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby prototype/control_plane.rb render-worker pilot1/self-hosted/manifest-low.yaml --project-root /Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1 --source-label pilot1/self-hosted/manifest-low.yaml
```

The command renders the task on stdout; it does not rerun Pilot 1. Historical memory and this evidence artifact do not replace future live-state checks.

## Boundaries and rollback

Legacy prompts remain the active fallback. The control plane remains inactive, and this Pilot 1 record neither authorizes Pilot 2 nor replaces or deprecates any legacy prompt.
