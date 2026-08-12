# Domain adapter: TEMPLATE (the schema every adapter conforms to)

Use this schema with the installed `skill-creator` guidance when creating or
updating a reusable domain adapter. An adapter changes only the nouns, never
the Fable loop: evidence, authority, verification by observation, and the
domain's frauds. It has no runtime dependency on an evaluation bundle.

Replace every `<...>` slot. Keep the section headers exactly as written.
Target length: 35–50 lines. Medical and clinical work deliberately gets no
adapter: it needs qualified review, not a checklist.

---

# Domain adapter: <sector>

Applies when the deliverable is <the sector's actual outputs, concretely>. The
loop is unchanged; these definitions replace coding defaults. <One boundary
sentence naming the nearest adapter and when it takes over.>

## Workflow (steps + flowchart)

<Ordered concrete steps naming what to open, produce, or check, followed by a
mermaid flowchart. The workflow must be followable, not aspirational.>

```mermaid
flowchart TD
    <domain steps as decision/action nodes, following the arrows literally>
```

## Minimum evidence set (binding, before any <the sector's first act>)

1. **<Governing document or ground truth>**: <what must be opened and what to
   do when it does not exist>.
2. **<Subject's own facts>**: <the primary material claims must trace to>.
3. **<One live external reference>**: <the thing fetched now, not recalled>.

## Evidence and primary sources

<What counts as a primary source here and the sector's signature non-evidence.>

## Authority order

<A single ordered chain using `>`, from explicit user/client instruction down to
preference or memory. Then name the classic conflict and which side wins.>

## Verification by observation

- <3–5 bullets naming what must actually be run or opened and exactness rules.>
- <Include the sector's equivalent of rendered surfaces being rendered and seen.>

## Fraud table (for fable-judge)

| Fraud | Symptom |
|---|---|
| <6–7 named frauds> | <observable symptom a Judge can reproduce> |

## Done, by example

"<Typical deliverable> is done" means: <observed checklist>. Not: "<classic
hollow claim>".

## Sources

<For newly created or substantially updated adapters, one line per regulation,
policy, figure, or practice: link plus access date.>
