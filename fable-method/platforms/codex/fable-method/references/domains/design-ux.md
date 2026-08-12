# Domain adapter: design and UX

Applies when the deliverable is visual or interactive: UI components, pages,
layouts, design reviews, brand surfaces, or presentations. The loop is
unchanged; these definitions replace coding defaults.

## Minimum evidence set (binding, before any pixel)

1. **Design-system rules**: `brand.md`, tokens, or component conventions; if
   none exists, say so before inventing one.
2. **Existing surfaces**: neighboring pages/components opened and viewed.
3. **Interaction states**: hover, focus, loading, error, empty, and overflow,
   not only the happy path.

## Evidence and primary sources

The rendered artifact is primary; code is a claim about it. Intent lives in
brand rules, tokens, and referenced designs, never aesthetic memory.

## Authority order

Explicit user/client direction > brand.md and tokens > referenced design file >
existing conventions > aesthetic preference. Surface conflicts.

## Verification by observation

- Render and inspect the surface, at more than one width when responsive.
- Trace colors, spacing, radii, and type to tokens; search for raw values.
- Compute contrast, inspect focus, label controls, and walk the keyboard path.
- See every required loading, error, empty, and overflow state.

## Fraud table (for fable-judge)

| Fraud | Symptom |
|---|---|
| Unrendered “done” | “Matches the design” with no render or screenshot |
| Token betrayal | Hardcoded colors, pixels, or fonts beside tokens |
| Asserted accessibility | Accessibility claim without contrast or keyboard evidence |
| Happy-path-only | Error, empty, loading, or overflow state is missing or silent |
| Off-family surface | New work is visibly foreign to neighboring pages |
| Placeholder debris | Lorem ipsum, dummy images, or dead links remain |

## Done, by example

“The pricing page is done” means rendered and reviewed at two widths, token
values used, contrast computed, all states present, and sibling consistency
checked. Not: “it compiles and looks fine.”
