---
name: fable-method
description: >-
  Top-level execution discipline for state-changing Worker tasks in Claude
  Code. Verify the Planner Packet against the real repository, choose one
  route (FAST, STANDARD, STANDARD_JUDGED, or LOOP_JUDGED), execute with an
  intent gate, verify by observation, and report with real evidence. Use when
  the user invokes /fable-method, says "use the fable method" or "approach
  this like Fable", when a Planner hands off approved implementation work, or
  for a non-trivial task without a more specific skill. Claude enforcement is
  prompt-only; no PreToolUse or SessionStart hook mechanically enforces the
  gates. Works with task-specific or domain skills; this skill owns scope,
  routing, verification, retries, and closure. Subcommands: plan, audit,
  report.
---
