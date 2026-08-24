# Antigravity CLI integration details

## Contents

- [Prompt-only enforcement](#prompt-only-enforcement)
- [Plugin packaging](#plugin-packaging)
- [Sibling skill resolution](#sibling-skill-resolution)

## Prompt-only enforcement

Antigravity CLI (`agy`) does not provide a hook that makes the Fable prompt
mechanically enforceable. A report that says a route, gate, or check happened
still requires the command or observation described by the shared workflow.
Do not treat the absence of a hook as permission to skip a gate.

## Plugin packaging

Unlike the Claude, Codex, and Gemini CLI platforms, Antigravity does not
discover a bare `SKILL.md` directory on its own: `agy plugin validate`
requires a `plugin.json` manifest at the plugin root, with skill content
nested under `skills/<skill-name>/`. This repository's materialization for
this platform already produces that shape; do not flatten it back to a bare
skill directory or drop `plugin.json` on the assumption that Antigravity
behaves like Gemini CLI. Confirm current discovery with Antigravity's own
`/skills` listing (`--output-format json` for a scriptable form), not
filesystem existence alone — a plugin can exist on disk and still be disabled
in `config.json`.

## Sibling skill resolution

Prefer the relative sibling paths named by the shared workflow. If an install
cannot resolve them, inspect the current Antigravity installation and use only
the confirmed absolute path for `fable-loop` or `fable-judge`. If no
fresh-context verifier exists, mark `JUDGE_MODE: SELF_CHECK_ONLY` and do not
claim an independent `VERIFIED` result for a Judge-gated task.
