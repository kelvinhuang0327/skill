# Project Context — control-plane-v1

## Sources and generated outputs

The five durable sources of truth are:

- `AGENT_CORE.md`
- `ROLE_PROFILES.md`
- `ROUTING_AND_LIFECYCLE.md`
- `TASK_MANIFEST.schema.yaml`
- `WORKER_TASK_TEMPLATE.md`

The four files under `compiled/` are generated role-prompt outputs. They are inactive Owner-review candidates and must be regenerated mechanically from the durable sources rather than edited by hand.

`prototype/control_plane.rb` is the offline compiler and validator. `prototype/test_control_plane.rb` is its full mechanical test suite. Both use the Ruby standard library and are not a project runtime or activation path.

## Reviewed baseline

- Reviewed repair commit: `7d646b3f2d7978c92cb1b69a69033260b500295f`
- Reviewed repair tree: `91e22a30a355043813ef7ee85439534090944f3d`
- Control-plane version: `1.1.0-draft.1`
- Manifest schema version: `1`

The four byte-pinned legacy prompts remain external fallback files and are not replaced, moved, or modified by this repository. Pilot 1 has not run; its execution evidence remains unresolved until a separate read-only execution task is authorized.
