# Self-Hosted Pilot 1 — Preparation Only

This package prepares, but does not execute, Pilot 1. No control-plane activation, legacy-prompt replacement, remote publication, or external side effect is authorized.

## Package

- Manifest: `pilot1/self-hosted/manifest-low.yaml`
- Target: `/Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1`
- Task: `CONTROL_PLANE_V1_PILOT1_SELF_HOSTED_READ_ONLY_ATTACHMENT`
- Shape: `LOW / FAST / NONE / NOT_APPLICABLE`

## Prerequisites for a later Pilot run

1. Local `main` remains clean at `7d646b3f2d7978c92cb1b69a69033260b500295f`, and the exact target checkout cleanly exposes the Owner-reviewed preparation commit descended from that base. Making that commit available at the target is a separate pre-Pilot lifecycle action.
2. The five repo-local `.ai` attachment files are present and project-local.
3. Profile version `1.1.0-draft.1` and schema version `1` resolve against the five durable sources.
4. The manifest passes schema validation and lint L1–L22.
5. Memory retrieval is bounded and memory write is forbidden.
6. The four compiled role prompts match fresh compilation and the durable-source fingerprint.
7. `active_task` projection remains disabled while manifest-SHA protection remains part of Shared Core policy.
8. The rendered Worker prompt is deterministic, strictly read-only, and reviewed before execution.
9. The Owner separately authorizes the read-only Pilot 1 execution task.

## Command for the later authorized run

From the repository root, generate the exact Worker prompt with:

```sh
LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby prototype/control_plane.rb render-worker pilot1/self-hosted/manifest-low.yaml --project-root /Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1 --source-label pilot1/self-hosted/manifest-low.yaml
```

The command renders the task on stdout. Supplying that rendered prompt to a Worker is Pilot 1 execution and requires the separate Owner authorization named above. Do not execute the rendered prompt during preparation review.

## Boundaries and rollback

Legacy prompts remain the active fallback. This package does not activate or replace anything. If the Owner rejects the preparation, rollback is limited to removing the unmerged local preparation branch; local `main` and the legacy fallback need no rollback.
