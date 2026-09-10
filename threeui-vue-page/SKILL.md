---
name: threeui-vue-page
description: Adapt one exact ThreeUI (or similarly demo-packaged HTML/Tailwind) visual source into a native, accessible component for a host UI framework — never as an installed runtime dependency. Inspects the host's existing framework, component conventions, and design tokens first; separates visual language from demo scaffolding; for a Vue host, produces a real SFC with explicit props/events/state and full button/interaction semantics; preserves source provenance and license; and requires focused tests, a host build/typecheck, and real browser observation before any visual/layout behavior is called done. Use when asked to bring a ThreeUI component (or another external HTML/CSS demo source) into a product as native framework code, or to "adapt this design reference to our app."
---

# ThreeUI → Vue Page

Turns one pinned ThreeUI (or comparable demo-packaged HTML) visual source into
native host-framework code. The source is a **visual reference**, never a
runtime dependency: nothing from it is installed, executed, or embedded live
in the host application.

```text
pin exact source -> inspect host conventions -> separate visual language
from demo scaffolding -> produce native host component -> preserve provenance
-> verify (focused tests + build + real browser observation)
```

## 1. Pin the source, don't chase the package

Record, verbatim, before writing any code:

- source repository (e.g. `MengTo/threeui`);
- the exact commit/ref, not a branch name that can move;
- the exact file path selected as the visual reference;
- the license file's location and text.

Treat the selected path as one static HTML/CSS snapshot to read, not a
package to add to the host's dependency tree. Do not install the source
repository's own npm/gem package, and do not assume its sibling components
are portable just because one was successfully adapted.

## 2. Inspect the host before generating anything

Before writing the new component, read:

- the host framework and version (Vue, React, etc. — do not assume);
- existing sibling components' prop/event/slot conventions and naming;
- existing design tokens (CSS custom properties, spacing/radius/shadow
  scales, color roles) already defined for the app;
- whether the host uses scoped per-component styles or one shared
  stylesheet, and match that instead of introducing a third convention;
- existing reusable state components (empty/error/loading) so the new page
  reuses them instead of inventing parallel ones.

## 3. Separate visual language from demo scaffolding

A ThreeUI/demo source mixes three things that must be pulled apart:

| Keep (visual language) | Discard (demo scaffolding) |
|---|---|
| Shape, spacing, color/gradient roles, elevation, type treatment | Full `<!DOCTYPE>`/`<html>`/`<head>`/`<body>` wrapper (including a second one nested inside the first — a common artifact of how these demo snippets get generated) |
| The interaction states it implies (hover, disabled, loading) | CDN `<script>` tags (Tailwind CDN, icon CDNs, etc.) |
| Any generic, non-branded iconography it shows | Preview-centering `requestAnimationFrame`/`setTimeout` chains |
| — | Demo `ResizeObserver` wiring used only to size a preview frame |
| — | Any unrelated preview/demo assets or scripts |

Never re-host the source via an `<iframe>` or inject its markup with
`v-html`/`dangerouslySetInnerHTML`. The output is hand-written native code
that reproduces the *visual language*, not a live embed of the source file.

## 4. Produce native host code

For a **Vue** host, the adapted output must:

- be a real Vue SFC (`<script setup>` + `<template>`), not a wrapped HTML
  string or iframe;
- expose explicit `defineProps` / `defineEmits`, not implicit global state;
- use a real semantic element for interactive controls (a `<button>` for a
  button, not a styled `<div>` with a click handler);
- implement `disabled` and `loading` states, and suppress duplicate
  interaction while `loading` is true (guard the emit itself, not just the
  visual style);
- keep keyboard focus visible (`:focus-visible`), matching the host's
  existing focus treatment when one already exists;
- prefer scoped component styles that reference the host's existing design
  tokens (CSS custom properties, etc.) over hard-coded colors, so the
  component inherits the host's theme instead of importing the source's own
  (often light-mode, demo-only) palette verbatim.

The same steps 1–3 and 5–8 generalize to other host frameworks; step 4 is
the one place host-native idiom gets substituted in (JSX + hooks for React,
etc.). This Skill's Vue guidance is worked out in full; treat another host
framework's step 4 as needing the same rigor, not as a mechanical find/replace
of this one.

## 5. Preserve provenance

Every file that copies or materially adapts source markup/CSS must carry, in
a comment or the adjacent reference doc: the source repository, the exact
commit, the exact path, and the license under which it was published (MIT,
Apache-2.0, etc. — quoted or linked exactly, never paraphrased into a
different license). See
[references/THREEUI_VUE_ADAPTATION.md](references/THREEUI_VUE_ADAPTATION.md)
for a worked example of this bookkeeping end to end, including the concrete
prop/event contract used for a button-shaped component.

## 6. Distinguish three different things

Keep these separate, in both the code and the handoff:

- **visual inspiration** — informed the look, nothing copied;
- **copied/adapted source** — markup, class structure, or a specific CSS
  technique taken from the pinned file and reworked into host-native code;
- **product business behavior** — this Skill never supplies business logic,
  real data, or backend calls; a consuming task owns those separately, and
  any values it displays that aren't real must say so.

## 7. Verification this Skill requires

Before calling a generated page or component done:

- focused component tests for the new component(s) — normal interaction,
  disabled, loading (including "no duplicate emit while loading"), and any
  state variants the consuming task defines;
- the host's own build/typecheck command;
- a changed-path/diff review — this Skill's output should touch only the
  files the consuming task actually named;
- **real browser observation** whenever visual or layout behavior is part of
  acceptance. A passing unit-test run or jsdom render is not evidence that a
  gradient renders, that text doesn't overflow, or that a hover/focus state
  is visible — those require actually looking at the rendered page. Report
  browser validation as not run, rather than substituting build/test output
  for it, when no browser is available.

## 8. Reuse before rebuilding

Prefer the host's existing reusable components (loading/empty/error states,
layout primitives, verification/test setup) over introducing a parallel UI
system. Pull in only the ones the page actually needs — reuse is not a
target to maximize for its own sake, and forcing every available component
into one page to demonstrate reuse is itself a defect.

## Never claim

- that the source repository or its package is native to the host
  framework;
- that adapting one component proves every component in the source is
  portable the same way;
- that this Skill was globally installed or published, or that a consuming
  page was deployed, unless that specific action was actually taken and
  observed.
