# Mechanical Compiler Prototype

Status: `DRAFT_FOR_OWNER_REVIEW`. This directory is an offline Stage 1 prototype; it does not activate the control plane or authorize either pilot.

## Boundary

- Ruby standard library only; no package install or new dependency.
- Manifest/profile/project/view inputs are read-only.
- Rendered Worker, role, and `active_task.md` content goes to stdout only.
- The tool never writes a repository or project, opens a DB/runtime, creates a remote, pushes, calls GitHub, starts a pilot, or edits legacy prompts.
- Existing files under `compiled/` remain unchanged documentary outputs. `render-role` proves a mechanical durable-source recipe but does not promote or overwrite those files.

## Commands

```sh
ruby prototype/control_plane.rb validate-schema MANIFEST
ruby prototype/control_plane.rb lint MANIFEST --project-root ROOT [--profile PATH] [--view PATH] [--source-label LABEL] [--seen-task-id ID]
ruby prototype/control_plane.rb render-worker MANIFEST --project-root ROOT [--profile PATH] [--source-label LABEL] [--seen-task-id ID]
ruby prototype/control_plane.rb render-active-task MANIFEST --project-root ROOT [--profile PATH] [--source-label LABEL] [--seen-task-id ID]
ruby prototype/control_plane.rb check-active-task MANIFEST VIEW --project-root ROOT [--profile PATH] [--source-label LABEL] [--seen-task-id ID]
ruby prototype/control_plane.rb render-role handoff|planner
ruby prototype/control_plane.rb manifest-sha MANIFEST
```

`--project-root` is required for full L1–L22 evaluation. The linter reads `.ai/agent-profile.yaml` and the four manifest-declared context files from that root; `--profile` can point to an explicit read-only profile fixture. `--seen-task-id` is repeatable and supplies the task-ID set against which L1 uniqueness is checked. A stable `--source-label` makes projection source banners independent of invocation spelling.

Exit status is `0` for PASS/successful rendering, `1` for schema/lint/drift rejection, and `2` for usage or unreadable input.

## Mechanical role rendering

`render-role` never reads the existing compiled outputs. It extracts named authoritative sections from the five durable sources in a fixed recipe, embeds a SHA-256 identity for every durable source, and emits no timestamp or project memory. Identical durable bytes therefore produce byte-identical Handoff and Planner output.

## Verification

```sh
ruby prototype/test_control_plane.rb
```

The suite validates all three examples, exercises one named negative fixture for every L1–L22 rule, checks render refusal on lint failure, renders Worker/role/projection outputs twice for byte identity, tampers with projection content and manifest SHA, and verifies stdout-only CLI execution leaves repository files unchanged. Test attachments and views are created only in automatically removed temporary directories.
