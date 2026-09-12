# Judge canonical source migration

The behavior authority is `fable-judge/shared/SKILL.md`. Depth, evidence reuse,
reconciliation, and remediation remain owned by
`fable-method/shared/references/judge-handoff.md`. The manifest mechanically
projects only the approved sections into each Judge bundle.

## Input provenance

These unversioned live inputs were read on 2026-09-11 as migration evidence,
not copied wholesale or promoted to authority. SHA-256 values describe only
the named SKILL.md files, not entire installed bundles:

| Platform / Origin | Input path | SHA-256 |
|---|---|---|
| Codex | `/Users/kelvin/.codex/skills/fable-judge/SKILL.md` | `a1cceb4fbac46f23f471c048c96736bb6caff8ce2e27e91f6070c4204f08965b` |
| Claude | `/Users/kelvin/.claude/skills/fable-judge/SKILL.md` | `f17518d9bb103888f68aefe12800afefb350309e570956b78822925e0a0654a6` |
| Legacy Plugin | `/Users/kelvin/.gemini/config/plugins/fable-method-plugin/skills/fable-judge/SKILL.md` | `5d5c29c1a50bb50f587cb9346d68431ae3eb79833c5756dcf1c8bd7035b3ec62` |

Prior attribution of the plugin path to Gemini CLI was incorrect; that path is
legacy Antigravity plugin provenance only. Canonical native targets for Gemini
and Antigravity are now separated:

- Gemini: `/Users/kelvin/.gemini/skills/fable-judge`
- Antigravity: `/Users/kelvin/.gemini/config/skills/fable-judge`

The legacy plugin path is not a canonical Gemini or Antigravity target and is
marked RETIRE_AFTER_REPLACEMENT.

These hashes confer no historical activation trust.

## Semantic convergence

The CTO decisions `FABLE_JUDGE_CANONICAL_SOURCE_ARCHITECTURE_R1` and
`FABLE_JUDGE_FOUR_PLATFORM_RUNTIME_MAPPING_IMPLEMENTATION_R1` establish a
symmetric four-platform Judge policy across Codex, Claude, Gemini, and
Antigravity. Convergence normalizes verdict names and independent-review
boundaries, removes divergent handwritten depth triggers and unconditional
command replay, and separates finding severity from confidence with a
falsifiable suspected-finding gate.
This record is provenance, not another behavior or depth contract.

## Migration stages and retirement

1. Capture inputs and implement the Git-controlled source.
2. Materialize the four declared Judge bundles deterministically.
3. Review the exact committed candidate through a pre-existing independent
   Judge surface, without activating the candidate to judge itself.
4. A future exact Owner authorization may permit live activation and migration.

Retire legacy live copies (including the plugin copy marked
RETIRE_AFTER_REPLACEMENT) only after that separately authorized activation
has passed verification and unknown local state has been explicitly resolved.
This implementation performs no live activation, retirement, deletion, or
migration.
