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
ruby prototype/control_plane.rb role-size-report
ruby prototype/control_plane.rb render-role handoff|ceo|cto|planner # compatibility alias
ruby prototype/control_plane.rb manifest-sha MANIFEST
```

`--project-root` is required for full L1–L22 evaluation. The linter reads `.ai/agent-profile.yaml` and the four manifest-declared context files from that root; `--profile` can point to an explicit read-only profile fixture. `--seen-task-id` is repeatable and supplies the task-ID set against which L1 uniqueness is checked. A stable `--source-label` makes projection source banners independent of invocation spelling.

Exit status is `0` for PASS/successful rendering, `1` for schema/lint/drift rejection, and `2` for usage or unreadable input.

## Mechanical role compilation

`compile-role` never reads the existing compiled outputs. It extracts one thin role profile plus role-selected canonical capsules from the five durable sources, embeds version/schema metadata, a length-framed combined durable-source fingerprint, and the byte count and SHA-256 identity of every source. Capsule selectors fail closed when an authoritative source marker disappears. The build emits no timestamp, project memory, or authorization, so identical durable bytes produce byte-identical Handoff, CEO, CTO, and Planner output.

Handoff uses `EVIDENCE_MIN`, `PRECEDENCE_MIN`, `MEMORY_READ_MIN`, and `REVIEW_BOUNDARY_MIN`. CEO adds only authorization escalation; CTO adds attachment handling; neither gets lifecycle or compiler capsules. Planner alone receives routing, worktree, manifest, and Worker-compilation capsules. Its manifest capsule mechanically derives the schema shape and compacts L1–L22 rather than embedding the full YAML file; its Worker capsule derives rendering rules, authorization variants, section order, and the active-task projection without duplicating the full template.

Hard byte ceilings are Handoff 8,872; CEO 12,509; CTO 10,530; Planner 21,919; aggregate 53,830. `role-size-report` returns each gate plus the preferred aggregate target status (`PASS` at 50,000 bytes or less). `validate-role` rejects stale bytes, over-budget artifacts, forbidden compiler leakage, duplicated Shared Core text, and capsule divergence with stable semantic error codes.

The committed build targets are:

- `compiled/HANDOFF_REPORTER.compiled.md`
- `compiled/CEO_DECISION_REVIEW.compiled.md`
- `compiled/CTO_TECHNICAL_REVIEW.compiled.md`
- `compiled/PLANNER_COMPILER.compiled.md`

## Verification

```sh
ruby prototype/test_control_plane.rb
```

The suite validates all three examples, exercises one named negative fixture for every L1–L22 rule, checks render refusal on lint failure, compiles all four roles twice, enforces individual and aggregate size gates, compares fresh bytes with every committed output, verifies selected-capsule identity and role boundaries, runs the twelve convergence semantic cases plus eight minification-specific exact-code cases, checks legacy prompt identities and 100% compatibility-mapping coverage, validates Markdown fence/heading structure, tampers with projection content and manifest SHA, and verifies stdout-only CLI execution leaves repository files unchanged. Test attachments, invalid roles, and views are created only in automatically removed temporary directories.
