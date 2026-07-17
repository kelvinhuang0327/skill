# Mechanical Compiler Prototype

Status: `DRAFT_FOR_OWNER_REVIEW`. This directory is an offline Stage 1 prototype; it does not activate the control plane or authorize either pilot.

## Boundary

- Ruby standard library only; no package install or new dependency.
- Manifest/profile/project/view inputs are read-only.
- Rendered Worker, role, and `active_task.md` content goes to stdout only.
- The tool never writes a repository or project, opens a DB/runtime, creates a remote, pushes, calls GitHub, starts a pilot, or edits legacy prompts.
- Files under `compiled/` are generated build outputs. The compiler remains stdout-only; an authorized build step captures stdout at the four exact compiled paths and verifies fresh byte equality.

## Commands

```sh
ruby prototype/control_plane.rb validate-schema MANIFEST
ruby prototype/control_plane.rb lint MANIFEST --project-root ROOT [--profile PATH] [--view PATH] [--source-label LABEL] [--seen-task-id ID]
ruby prototype/control_plane.rb render-worker MANIFEST --project-root ROOT [--profile PATH] [--source-label LABEL] [--seen-task-id ID]
ruby prototype/control_plane.rb render-active-task MANIFEST --project-root ROOT [--profile PATH] [--source-label LABEL] [--seen-task-id ID]
ruby prototype/control_plane.rb check-active-task MANIFEST VIEW --project-root ROOT [--profile PATH] [--source-label LABEL] [--seen-task-id ID]
ruby prototype/control_plane.rb compile-role handoff|ceo|cto|planner
ruby prototype/control_plane.rb validate-role handoff|ceo|cto|planner COMPILED_ROLE
ruby prototype/control_plane.rb render-role handoff|ceo|cto|planner # compatibility alias
ruby prototype/control_plane.rb manifest-sha MANIFEST
```

`--project-root` is required for full L1–L22 evaluation. The linter reads `.ai/agent-profile.yaml` and the four manifest-declared context files from that root; `--profile` can point to an explicit read-only profile fixture. `--seen-task-id` is repeatable and supplies the task-ID set against which L1 uniqueness is checked. A stable `--source-label` makes projection source banners independent of invocation spelling.

Exit status is `0` for PASS/successful rendering, `1` for schema/lint/drift rejection, and `2` for usage or unreadable input.

## Mechanical role compilation

`compile-role` never reads the existing compiled outputs. It extracts one thin role profile plus shared and role-required authoritative sections from the five durable sources, embeds version/schema metadata, a length-framed combined durable-source fingerprint, and the byte count and SHA-256 identity of every source. It emits no timestamp, project memory, or authorization. Identical durable bytes therefore produce byte-identical Handoff, CEO, CTO, and Planner output.

Handoff, CEO, and CTO do not embed the complete Worker template or manifest schema. Planner alone embeds `WORKER_TASK_TEMPLATE §T`, the active-task projection, and the manifest schema. `validate-role` rejects stale build bytes and emits stable semantic error codes for role-boundary or Shared Core divergence.

The committed build targets are:

- `compiled/HANDOFF_REPORTER.compiled.md`
- `compiled/CEO_DECISION_REVIEW.compiled.md`
- `compiled/CTO_TECHNICAL_REVIEW.compiled.md`
- `compiled/PLANNER_COMPILER.compiled.md`

## Verification

```sh
ruby prototype/test_control_plane.rb
```

The suite validates all three examples, exercises one named negative fixture for every L1–L22 rule, checks render refusal on lint failure, compiles all four roles twice, compares fresh bytes with every committed output, verifies shared-block identity and role boundaries, runs twelve exact-code negative semantic cases, checks legacy prompt identities and 100% compatibility-mapping coverage, validates Markdown fence/heading structure, tampers with projection content and manifest SHA, and verifies stdout-only CLI execution leaves repository files unchanged. Test attachments, invalid roles, and views are created only in automatically removed temporary directories.
