# ThreeUI → Vue adaptation: worked example

This is the concrete provenance record and Vue contract for one adaptation
done with this Skill. It illustrates the procedure in `../SKILL.md`; it is
not itself the general contract, and a future adaptation is not required to
reuse these exact prop names or this exact consuming component.

## Provenance

| Field | Value |
|---|---|
| Source repository | [`MengTo/threeui`](https://github.com/MengTo/threeui) |
| Pinned commit | `68802d5428071ada5c20db8094b1649e6bb770ed` |
| Selected path | `src/shaders/neuform-isolated/sources/gradient-pill-button.html` |
| License | MIT (`LICENSE` at the repository root, same commit) |
| Copyright | Copyright (c) 2026 Meng To |

Full license text, reproduced because the MIT license requires it to
accompany any copy or substantial portion of the software it covers:

```text
MIT License

Copyright (c) 2026 Meng To

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Any consumer of this Skill's output must keep this table and license text (or
an equivalent NOTICE-style reference to it) reachable from the adapted
component — do not drop attribution once the visual work is "just code."

## What the source actually is

`gradient-pill-button.html` is a self-contained demo preview page, not a
component file: a full `<!DOCTYPE html>` document that loads the Tailwind CDN
script, and inside its `<body>` nests a *second*, redundant `<html><head>
</head><body>...</body></html>` around the actual button markup — an
artifact of how the demo snippet was generated, not something to preserve.
After the button markup, the page adds an IIFE that measures and re-centers
the `.component-wrapper` with double `requestAnimationFrame` calls, a chain
of `setTimeout` re-checks (50/150/300/500/1000 ms), and a `ResizeObserver`
that re-triggers the same centering — all preview-only plumbing for viewing
the snippet in isolation.

The button itself, stripped of Tailwind class names, is:

- pill-shaped (fully rounded);
- a soft, vertically-toned gradient fill with a very subtle top-to-bottom
  darkening (`from-black/10 via-black/20 to-black/10` — designed against a
  white demo background, not a value to copy onto a dark host UI);
- stacked, low-opacity box-shadows for a soft elevated look, plus a thin
  1px gradient "ring" drawn with a `::before` pseudo-element using
  `mask-composite: exclude` (an inline `--border-gradient` custom property
  feeding a `linear-gradient`);
  a label (`text-sm font-medium`, `tracking-tight`) plus a trailing
  16×16 arrow-right glyph;
- a hover state that lightens the background.

One class in the source, `hover:bg-slate-50 hover:text-slate-`, is itself a
truncated/invalid Tailwind utility (`text-slate-` has no shade suffix) — read
this as a generation artifact of the demo snippet, not as a value to
faithfully reproduce.

## What was kept vs. adapted vs. discarded

| Decision | Detail |
|---|---|
| Kept (visual language) | Pill shape, layered soft elevation, a subtle gradient fill, a thin gradient-toned ring border, trailing arrow glyph, hover lightening |
| Adapted (not copied verbatim) | The gradient/shadow *technique* (host-token-driven gradient fill + `::before` mask-composite ring) was re-implemented against the host's own dark-theme design tokens (`--gradient-primary`, `--border-color`, `--text-primary`, etc.) instead of the source's literal light-mode `black/N%` values, per Skill step 4 |
| Discarded (demo scaffolding) | Outer `<!DOCTYPE>`/nested `<html>`/`<head>`/`<body>`, the Tailwind CDN `<script>`, the centering `requestAnimationFrame`/`setTimeout` IIFE, the demo `ResizeObserver` wiring |
| Not reused | The Tailwind utility classes themselves — the host does not load Tailwind, so classes were translated to scoped CSS against host tokens rather than partially reintroducing a CDN/utility-class dependency |

## Vue contract used for this adaptation

A gradient-pill-button-shaped component exposes:

| Prop | Type | Notes |
|---|---|---|
| `label` | `string` | button text |
| `disabled` | `boolean` (default `false`) | disables the native `<button>` |
| `loading` | `boolean` (default `false`) | shows a busy state; see below |
| `loadingLabel` | `string?` | optional label swap while loading |

| Emit | When |
|---|---|
| `click` | only on a real user interaction where `disabled` and `loading` are both false |

Interaction/accessibility requirements:

- render a real `<button type="button">`, never a `<div>`/`<a>` standing in
  for one;
- set the native `disabled` attribute when `disabled` is true, and also
  treat `loading` as non-interactive (guard the click handler itself so a
  second click during an in-flight `loading` state cannot emit twice — do
  not rely on CSS `pointer-events` alone);
- set `aria-busy="true"` while `loading`;
- keep a visible `:focus-visible` outline, matching the host's existing
  focus treatment where the host already defines one;
- keep the trailing icon generic (a plain arrow glyph is fine); don't pull
  in an icon font/library for one glyph.

## Host inspection checklist (repeat this for any future consumer)

Before writing the component, confirm and record:

1. Vue version and whether the host uses `<script setup>` SFCs;
2. whether components carry their own `<style>` blocks or rely on one shared
   stylesheet — if shared, prefer scoped `<style scoped>` in the new
   component rather than editing the shared stylesheet, so the adaptation
   stays isolated;
3. the existing CSS custom properties for color/gradient/radius/shadow/focus
   roles, and reuse them by name;
4. existing loading/empty/error primitives, so the consuming page reuses
   them instead of duplicating that logic.
