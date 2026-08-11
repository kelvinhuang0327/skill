# Repository Runbook — control-plane-v1

All commands below are local and read-only unless a separate Owner task explicitly authorizes a repository write.

## Attachment and Git checks

```sh
repo=/Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1
git -C "$repo" rev-parse --show-toplevel
git -C "$repo" rev-parse HEAD
git -C "$repo" status --porcelain=v2 --branch
test -f "$repo/.ai/agent-profile.yaml"
test -f "$repo/.ai/ai-context/PROJECT_PROFILE.md"
test -f "$repo/.ai/ai-context/PROJECT_CONTEXT.md"
test -f "$repo/.ai/ai-context/RUNBOOK.md"
test -f "$repo/.ai/ai-memory/MEMORY_LOG.md"
```

## Validator and test commands

```sh
repo=/Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1
manifest="$repo/pilot1/self-hosted/manifest-low.yaml"
LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby "$repo/prototype/control_plane.rb" validate-schema "$manifest"
LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby "$repo/prototype/control_plane.rb" lint "$manifest" --project-root "$repo" --source-label pilot1/self-hosted/manifest-low.yaml
LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby "$repo/prototype/test_control_plane.rb"
```

## Role compilation command

```sh
repo=/Users/kelvin/VibeCoding-WorkSpace/prompt/control-plane-v1
LC_ALL=C.UTF-8 LANG=C.UTF-8 ruby "$repo/prototype/control_plane.rb" compile-role handoff
```

Replace `handoff` only with one of `ceo`, `cto`, or `planner` to inspect that generated role on stdout. Do not redirect output into the repository without separate build authorization.

## Repository constraints

- Do not create a remote, push, or open a PR.
- Do not execute or activate either Pilot, activate the control plane, or replace the legacy prompts.
- Do not access a project DB, data store, runtime, registry, deployment target, or network.
- Validation may use only repository reads and automatically removed system-temporary output.
