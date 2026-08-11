# Domain adapter: TEMPLATE (the schema every adapter conforms to)

This file is the explicit schema behind every adapter in this directory. Use it with the installed `skill-creator` instructions when the user asks to create or update a reusable domain adapter; fable-judge reads adapters through `../../../fable-judge/SKILL.md`. The schema was distilled from the seven hand-written adapters and historical adapter-creation trials, but it has no runtime dependency on an eval bundle or a separate generator Skill.

An adapter changes only the nouns, never the loop. It answers, for one sector: what counts as evidence, who the authority is, what verification by observation means, and what the frauds are. If a section below cannot be filled with content genuinely different from the coding default, the sector does not need an adapter.

Replace every `<...>` slot. Keep the section headers exactly as written (CI greps for them). Target length: 35-50 lines. Medical and clinical work deliberately gets no adapter: it needs qualified review, not a checklist.

---

# Domain adapter: <sector>

Applies when the deliverable is <the sector's actual outputs, concretely>. The loop is unchanged; these definitions replace the coding defaults. <One boundary sentence naming the nearest adapter or the coding default, and which side of the line takes over when.>

## Workflow (steps + flowchart)

<For a newly created or substantially updated adapter, provide the ordered, concrete steps a practitioner or a lesser model follows in this domain, each naming what to open, produce, or check, followed by a mermaid flowchart. This is the user-facing "how to work in this domain" artifact; it must be followable, not aspirational.>

```mermaid
flowchart TD
    <domain steps as decision/action nodes, following the arrows literally>
```

## Minimum evidence set (binding, before any <the sector's first act: writing, aggregate, figure, pixel...>)

1. **<The governing document or ground truth of this sector>**: <what must actually be opened, and what to do when it does not exist>.
2. **<The subject's own facts>**: <the primary material claims must trace to>.
3. **<One live external reference>**: <the thing fetched now, not recalled>.

## Evidence and primary sources

<Two or three sentences: what counts as a primary source here, and the sector's signature non-evidence (the thing that looks like evidence but is decoration).>

## Authority order

<A single ordered chain using ">", from explicit user/client instruction down to your own preference or memory. Then one sentence: the sector's classic conflict and which side wins.>

## Verification by observation

- <3 to 5 bullets. Each one: what "observed" means for this sector's claims, checks that must actually be run or opened, exactness requirements (names, prices, dates, versions).>
- <Include the sector's equivalent of "rendered surfaces are actually rendered and looked at".>

## Fraud table (for fable-judge)

| Fraud | Symptom |
|---|---|
| <6 or 7 rows. Name each fraud in two or three words> | <the observable symptom a judge can hunt by diffing, re-running, or re-fetching> |

## Done, by example

"<A typical deliverable> is done" means: <the observed checklist in one sentence>. Not: "<the sector's classic hollow claim>".

## Sources

<For a newly created or substantially updated adapter, provide one line per regulation, policy, figure, or practice the adapter names: the link plus the access date. An adapter claim with no source line is memory wearing a suit; either fetch it or cut it.>
