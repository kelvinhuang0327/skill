## Antigravity CLI integration

Antigravity skills are prompt text: no hook mechanically enforces a skipped
gate, route, or evidence claim. Treat every shared rule as session discipline
and never infer that a command was blocked because a prompt described it.

Antigravity's global skill root is `~/.gemini/config/skills/`, a bare
`<name>/SKILL.md` directory exactly like the Codex, Claude, and Gemini CLI
platforms already use — not a `plugin.json`-wrapped bundle. It takes
precedence over the legacy Gemini CLI root (`~/.gemini/skills/`) when both
define a same-named skill. When relative sibling paths cannot be resolved,
resolve the Antigravity installation's confirmed `fable-loop` and
`fable-judge` paths from the live environment; never guess or silently
substitute a different verifier.

Load [Antigravity-specific integration details](references/platform-antigravity.md)
only when Antigravity discovery precedence or sibling-skill resolution
matters.
