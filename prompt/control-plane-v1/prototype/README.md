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

Handoff uses `EVIDENCE_MIN`, `PRECEDENCE_MIN`, `MEMORY_READ_MIN`, `MEMORY_CANDIDATE_MIN`, and `REVIEW_BOUNDARY_MIN`. The candidate-memory capsule mechanically extracts AGENT_CORE §14 rules 7/9/10 so `core_v1` fields, append-only supersession, and the no-write boundary remain available without local source access. CEO adds only authorization escalation; CTO adds attachment handling; neither gets lifecycle or compiler capsules. Planner alone receives routing, worktree, manifest, and Worker-compilation capsules. Its manifest capsule mechanically derives the schema shape and compacts L1–L22 rather than embedding the full YAML file; its Worker capsule derives rendering rules, authorization variants, section order, and the complete active-task projection contract without duplicating the full template.

Hard byte ceilings are Handoff 8,872; CEO 12,509; CTO 10,530; Planner 21,919; aggregate 53,830. `role-size-report` returns each gate plus the preferred aggregate target status (`PASS` at 50,000 bytes or less). `validate-role` always runs structural, hard-size, shared vocabulary/fingerprint, role-semantic, forbidden-capability, and deterministic/provenance guards in that order. Byte equality is only the final provenance check; equal bytes that fail semantics return `ROLE_VALIDATOR_SEMANTIC_BYPASS`. Targeted role errors are `ROLE_HANDOFF_MEMORY_CORE_V1_INCOMPLETE`, `ROLE_HANDOFF_MEMORY_WRITE_BOUNDARY_MISSING`, `ROLE_PLANNER_ACTIVE_TASK_P3_INCOMPLETE`, `ROLE_AUTH_METADATA_BOUNDARY_MISSING`, `ROLE_AUTH_EXECUTABLE_ACTIVATION_BOUNDARY_MISSING`, and `ROLE_CTO_ATTACHMENT_DISCOVERY_INCOMPLETE`.

The committed build targets are:

- `compiled/HANDOFF_REPORTER.compiled.md`
- `compiled/CEO_DECISION_REVIEW.compiled.md`
- `compiled/CTO_TECHNICAL_REVIEW.compiled.md`
- `compiled/PLANNER_COMPILER.compiled.md`

## Verification

```sh
ruby prototype/test_control_plane.rb
```

Run the suite with both supported locale boundaries:

```sh
LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby prototype/test_control_plane.rb
LC_ALL=C LANG=C ruby prototype/test_control_plane.rb
```

The harness treats subprocess output as UTF-8 bytes before inspecting it, so a restrictive `C` locale cannot relabel valid output as US-ASCII. Legacy prerequisites are checked collectively and fail closed: every missing path is reported together, while byte-count, line-count, and SHA-256 mismatches are independently rejected. Negative legacy cases use only automatically removed temporary fixtures; the authorized identity test always reads the four external legacy paths directly and never skips, substitutes, or synthesizes them.

The suite validates all three examples, exercises one named negative fixture for every L1–L22 rule, checks render refusal on lint failure, compiles all four roles twice, enforces individual and aggregate size gates, compares fresh bytes with every committed output, verifies selected-capsule identity and role boundaries, runs 33 exact-code role-semantic cases plus eight minification-specific cases, directly exercises size and semantic guards on byte-equal output, checks legacy prompt identities and 100% compatibility-mapping coverage, validates Markdown fence/heading structure, tampers with projection content and manifest SHA, and verifies stdout-only CLI execution leaves repository files unchanged. Test attachments, invalid roles, views, and legacy negative cases are created only in automatically removed temporary directories.
