# Claude Code integration details

## Contents

- [Prompt-only enforcement](#prompt-only-enforcement)
- [Sibling skill resolution](#sibling-skill-resolution)

## Prompt-only enforcement

Claude Code does not provide a hook that makes the Fable prompt mechanically
enforceable. A report that says a route, gate, or check happened still requires
the command or observation described by the shared workflow. Do not treat the
absence of a hook as permission to skip a gate.

## Sibling skill resolution

Prefer the relative sibling paths named by the shared workflow. If an install
cannot resolve them, inspect the current Claude installation and use only the
confirmed absolute path for `fable-loop` or `fable-judge`. If no fresh-context
verifier exists, mark `JUDGE_MODE: SELF_CHECK_ONLY` and do not claim an
independent `VERIFIED` result for a Judge-gated task.
