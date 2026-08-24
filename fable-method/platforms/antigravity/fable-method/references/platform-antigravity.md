# Antigravity CLI integration details

## Contents

- [Prompt-only enforcement](#prompt-only-enforcement)
- [Discovery roots and precedence](#discovery-roots-and-precedence)
- [Sibling skill resolution](#sibling-skill-resolution)

## Prompt-only enforcement

Antigravity CLI (`agy`) does not provide a hook that makes the Fable prompt
mechanically enforceable. A report that says a route, gate, or check happened
still requires the command or observation described by the shared workflow.
Do not treat the absence of a hook as permission to skip a gate.

## Discovery roots and precedence

Antigravity scans multiple global skill roots and merges the results by skill
name, confirmed by direct testing against the installed `agy` binary (its own
official docs did not document this):

- `~/.gemini/config/skills/<name>/SKILL.md` — Antigravity's own global root.
- `~/.gemini/skills/<name>/SKILL.md` — the legacy Gemini CLI root, still
  scanned for compatibility since Antigravity shares Gemini CLI's
  `~/.gemini/` namespace.
- `~/.gemini/config/plugins/<name>/skills/<skill-name>/SKILL.md` — the
  `plugin.json`-wrapped plugin system, a separate mechanism entirely
  (`agy plugin install/import/validate/list`).
- Workspace `.agents/skills/<name>/SKILL.md`, scoped to the open workspace.

When two roots define a same-named skill, `~/.gemini/config/skills/` wins
over the legacy `~/.gemini/skills/` root (confirmed with distinguishable
probe content in both locations simultaneously). This platform's live
installation target is deliberately the `config/skills/` root rather than a
plugin bundle: a plugin's manifest and nesting are unnecessary complexity
here, since a bare directory in the higher-precedence root already wins over
whatever the pre-existing Gemini CLI installation left behind. Do not repackage
this platform as a plugin on the assumption that `agy plugin validate`'s
manifest requirement applies to bare-root discovery too — the two mechanisms
are independent, and `plugin validate` says nothing about whether a directory
is discoverable through the bare-root path.

Confirm current discovery with Antigravity's own `/skills` listing
(`--output-format json` for a scriptable form and its `path` field), not
filesystem existence alone — that is the only way to tell which root actually
won a name collision.

## Sibling skill resolution

Prefer the relative sibling paths named by the shared workflow. If an install
cannot resolve them, inspect the current Antigravity installation and use only
the confirmed absolute path for `fable-loop` or `fable-judge`. If no
fresh-context verifier exists, mark `JUDGE_MODE: SELF_CHECK_ONLY` and do not
claim an independent `VERIFIED` result for a Judge-gated task.
