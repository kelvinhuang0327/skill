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

## Reviewed baseline and current pilot status

- Reviewed repair commit: `7d646b3f2d7978c92cb1b69a69033260b500295f`
- Reviewed repair tree: `91e22a30a355043813ef7ee85439534090944f3d`
- Control-plane version: `1.1.0-draft.1`
- Manifest schema version: `1`

Pilot 1 passed with durable evidence recorded in `pilot1/self-hosted/pilot1-result.md`. Pilot 2 passed the test-portability workflow and an independent fixed-head review; its integration candidate combines preparation commit `8a5607f53f7274d2d57beaa98303b3634bff67db` with reviewed execution commit `12f056d85c9bdab3c09a28a9c46ce4a6393afc9e` in merge commit `05cc9d8715fa5a2c986d4a9996b0436c81977d91` on `codex/control-plane-v1-pilot2-integration-evidence-promotion-r1`.

The standalone control-plane repository has no remote; its reviewed source, pilot evidence, and decision packet are now preserved under `prompt/control-plane-v1/` in the existing `skill` repository. This subtree integration does not promote or activate the control plane. The four byte-pinned legacy prompts remain external, unchanged, and authoritative fallback files; no legacy prompt is replaced or deprecated.
