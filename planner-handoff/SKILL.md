---
name: planner-handoff
description: "Generate Worker task packets and the accompanying completion/RCA handoff using Planner v5.5. Use when explicitly acting as Planner to produce next-task prompts or invoking planner-handoff. Do not use during ordinary Worker execution or routine Worker reporting."
---

# Planner Handoff

Read the bundled [Planner v5.5](references/planner-handoff-v5.5.md) before preparing the handoff. It is the deployed template for this skill; do not substitute an archived Planner prompt or infer instructions from the current working directory.

The primary user workflow is Owner → Web ChatGPT Planner → task packet → Work Agent. This native skill is an optional Planner-role entry; ordinary Workers consume the generated packet and their applicable project rules, without loading this skill or the full Planner template. Local installation does not configure or activate the owner's Web ChatGPT Planner.

Act as Planner, preserving the user's current goal, boundaries and applicable prior authorization. Report the actual loaded template path and version briefly. This receipt establishes loading only; decisions and completion claims still need their own evidence.

The template's scope is planning and handoff. Do not implement, launch Workers, mutate memory, publish, clean a workspace, or reopen a closed investigation merely because a template or example mentions those actions. Use the actual request to resolve authority.

Carry necessary project-rule locators into each executable handoff. A shared installation does not make one project's RUNBOOK or facts apply to other personal or company projects. Keep UNKNOWN distinct from passing and separate bounded closeout from technical completion.

Use the available `fable-method` skill for canonical Fable classification and Worker-contract references when they are required by the handoff. Do not enter its implementation lifecycle for planning. A missing required skill or rule is an explicit capability gap, not permission to invent a substitute.

Emit each genuinely needed Agent prompt in a separate Markdown code block with model tier, remaining-task model, exact scope and falsifiable acceptance. Return `NONE REQUIRED` when there is no executable next task; do not create parallel cards that depend on each other's unfinished outputs.

This is an on-demand Planner entry. Saving or synchronizing it does not prove discovery, activation, or future compliance in another platform or session.
