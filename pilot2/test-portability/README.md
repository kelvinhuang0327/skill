# Pilot 2 Test Portability — Preparation Only

This package prepares `CONTROL_PLANE_V1_PILOT2_TEST_PORTABILITY_REPAIR_R1`; Pilot 2 has not been executed or authorized by this preparation commit.

## Bound package

- Manifest: `pilot2/test-portability/manifest-medium.yaml`
- Exact base: `31d24f4cc0f6a730a781889c56e4881c04acd7d4`
- Future branch: `codex/control-plane-v1-pilot2-test-portability-r1`
- Reusable worktree: `/Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1-agent`
- Future allowed writes, exactly:
  - `prototype/test_control_plane.rb`
  - `prototype/README.md`

## Required future verification

The future Worker must run the complete suite under both locales:

```sh
LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby prototype/test_control_plane.rb
LC_ALL=C LANG=C ruby prototype/test_control_plane.rb
```

Legacy validation must fail closed: all missing legacy paths must be reported together with a nonzero result, and byte-count, line-count, or SHA-256 mismatch must each fail. The Worker may not skip, substitute, synthesize, or silently accept a missing or mismatched legacy prompt; all four authorized legacy identities must remain exact.

## Authorization and boundaries

A separate Owner authorization for this future Pilot 2 task must replace `PENDING_OWNER_TOKEN` through the declared `MEDIUM / FAST / SINGLE_PROMPT` flow. Until then, the rendered Worker prompt must stop at `WORKER_WAITING_OWNER`.

This package authorizes no Pilot 2 execution, DB or data access, project runtime, network, remote, push, PR, activation, active-task projection, memory write, or legacy replacement. Automatically removed temporary fixtures are the only allowed test side effect. The future implementation must create exactly one local commit, restore the clean reusable worktree detached at the pinned base, retain its task branch, and undergo mandatory independent fixed-head review before acceptance.
