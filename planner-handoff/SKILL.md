---
name: planner-handoff
description: "Generate Worker task packets and the accompanying completion/RCA handoff using Planner v5.5.1. Use when explicitly acting as Planner to produce next-task prompts or invoking planner-handoff. Do not use during ordinary Worker execution or routine Worker reporting."
---

# Planner Handoff

Read the bundled [Planner v5.5.1](references/planner-handoff-v5.5.1.md) before preparing the handoff. It is the deployed template for this skill; do not substitute an archived Planner prompt or infer instructions from the current working directory.

The primary user workflow is Owner → Web ChatGPT Planner → task packet → Work Agent. This native skill is an optional Planner-role entry; ordinary Workers consume the generated packet and their applicable project rules, without loading this skill or the full Planner template. Local installation does not configure or activate the owner's Web ChatGPT Planner.

Act as Planner, preserving the user's current goal, boundaries and applicable prior authorization. Report the actual loaded template path and version briefly. This receipt establishes loading only; decisions and completion claims still need their own evidence.

### Optional local routing structure check

When a local Planner has CLI access, it may check a drafted executable Worker Packet with the published routing validator before delivering it:

```sh
ruby \
  /Users/kelvin/VibeCoding-WorkSpace/skill/fable-method/scripts/explicit_contract.rb \
  --check-routing-packet \
  /absolute/path/to/packet.md
```

Replace the final argument with the packet's absolute path. Use this existing checker only; do not add a second routing parser. `ROUTING_STRUCTURE_PASS` means only that the primary routing fields form a canonical structural combination. It does not establish Owner authorization, task correctness, a Judge PASS, task completion, or measured success. For `ROUTING_STRUCTURE_INVALID`, correct the specific contradictory packet fields under the canonical rules; do not change those rules. `INSUFFICIENT_PACKET_FIELDS` is not a pass: do not infer missing values, and obtain them from authoritative task information before treating the packet as executable.

Web Planners and platforms without local CLI access continue to follow the existing canonical text rules. CLI availability is not a universal prerequisite and does not create a generic `BLOCKED` status. This check does not dispatch a Judge, require a full test suite, or add another Planner execution phase.

The template's scope is planning and handoff. Do not implement, launch Workers, mutate memory, publish, clean a workspace, or reopen a closed investigation merely because a template or example mentions those actions. Use the actual request to resolve authority.

Carry necessary project-rule locators into each executable handoff. A shared installation does not make one project's RUNBOOK or facts apply to other personal or company projects. Keep UNKNOWN distinct from passing and separate bounded closeout from technical completion.

Use the available `fable-method` skill for canonical Fable classification and Worker-contract references when they are required by the handoff. Do not enter its implementation lifecycle for planning. A missing required skill or rule is an explicit capability gap, not permission to invent a substitute.

Emit each genuinely needed Agent prompt in a separate Markdown code block with model tier, remaining-task model, exact scope and falsifiable acceptance. Return `NONE REQUIRED` when there is no executable next task; do not create parallel cards that depend on each other's unfinished outputs.

This is an on-demand Planner entry. Saving or synchronizing it does not prove discovery, activation, or future compliance in another platform or session.
