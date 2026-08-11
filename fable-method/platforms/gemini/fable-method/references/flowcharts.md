# The workflow, drawn

The same method as decision flowcharts. Each chart is executable pseudocode: a model can follow the arrows literally, and a human can audit exactly what happens at every branch. Nothing here adds rules; every box traces to a numbered rule in SKILL.md or a skill in the family.

## 1. The master router: any problem, start to finish

```mermaid
flowchart TD
    IN["Any incoming ask"] --> TRIV{"Trivial?<br/>one file, under 10 lines,<br/>no new behavior, no searching"}
    TRIV -->|yes| DOIT["Do it, run the one obvious check,<br/>report in two sentences"]
    TRIV -->|"no, or unsure"| FIT{"Fit gate:<br/>where does the answer live?"}
    FIT -->|"reachable sources"| SHAPE{"What shape is the ask?"}
    FIT -->|"unknown but researchable"| RES["Research it first<br/>(Step 2 budget), then loop"]
    FIT -->|"only your own inference"| INFER["Say so, no costume.<br/>Ask, or flag low-confidence"]
    FIT -->|"specialized + recurring"| MK["Make a skill (fable-domain)"]
    RES --> SHAPE
    SHAPE -->|"question or assessment"| ASSESS["Diagnose only, change nothing.<br/>Findings plus one recommendation"]
    SHAPE -->|"plan-first: ambiguous scope,<br/>irreversible actions, or a plan was asked for"| PLANF["Build the plan artifact.<br/>STOP for approval"]
    SHAPE -->|task| DOM{"Which domain?"}
    DOM -->|coding| LOOP2["Run the loop:<br/>evidence, decide, act, verify"]
    DOM -->|"marketing, research, data,<br/>business, finance, legal, design, devops"| ADAPT["Load the domain adapter.<br/>Its minimum evidence set is binding"]
    ADAPT --> LOOP2
    LOOP2 --> JPASS["Judge pass before presenting:<br/>every claim observed, or relabeled a caveat"]
    ASSESS --> JPASS
    JPASS --> OUT["Report, outcome first,<br/>honest caveats"]
```

## 2. Classifying the ask (Step 0, with tie-breaks)

```mermaid
flowchart TD
    A["Read the ask.<br/>Extract stated constraints and<br/>decisions already made"] --> B{"Any plan-first signal?<br/>ambiguous scope, irreversible or<br/>outward-facing action, plan requested"}
    B -->|yes| P["Plan-first.<br/>It beats task on any tie"]
    B -->|no| C{"Question mixed with task?<br/>'why is this failing, and fix it'"}
    C -->|yes| T2["Task, whose final report<br/>must also answer the question"]
    C -->|no| D{"Pure question?"}
    D -->|yes| Q["Assessment: change nothing"]
    D -->|no| T["Task"]
    P --> AMB{"Can evidence settle<br/>which deliverable is meant?"}
    AMB -->|yes| GO["Proceed and let Step 2 settle it"]
    AMB -->|"no, only the user can"| ASK["Ask exactly ONE pointed question,<br/>stating your recommended interpretation.<br/>Then wait"]
```

## 3. Gathering evidence (Step 2, bounded)

```mermaid
flowchart TD
    O["ORIENT: enumerate what exists.<br/>List the directory, glob the project,<br/>before reading anything specific"] --> S["Domain adapter loaded?<br/>Open its minimum evidence set first"]
    S --> B1["Round 1: independent, expensive lookups<br/>(web, docs, subagents, many files)<br/>in ONE parallel batch.<br/>A few small local reads may chain<br/>when each shapes the next"]
    B1 --> N1{"Did anything contradict<br/>your expectation?"}
    N1 -->|yes| SUR["SURPRISE: state it to the user"]
    SUR --> R{"What does it change?"}
    R -->|"what done means"| U1["Update the definition of done"]
    R -->|"what the user is asking"| U0["Go back to Step 0"]
    R -->|neither| CONT["Report it and continue"]
    N1 -->|no| N2{"Do you still lack evidence<br/>that would change your action?"}
    N2 -->|yes| B2["Round 2, the follow-up"]
    N2 -->|no| DONE["Stop gathering. More research<br/>cannot change the action"]
    B2 --> N3{"Still missing something decisive?"}
    N3 -->|"yes, and you can state why"| B3["Round 3, with the stated reason"]
    N3 -->|no| DONE
```

## 4. The intent gate (Step 4, before any behavior change)

```mermaid
flowchart TD
    E["About to change behavior"] --> I["Write the line:<br/>INTENT: code does X, check expects Y,<br/>spec says Z. Open the spec to fill Z"]
    I --> AGR{"Do X, Y, Z all agree?"}
    AGR -->|yes| GO["Smallest correct change.<br/>INTENT line goes in the report"]
    AGR -->|no| AUTH{"Who wins?<br/>user statement beats spec,<br/>spec beats checks,<br/>checks beat current code"}
    AUTH --> NOTE["'fix the code' or 'make tests pass'<br/>is task framing, NOT a statement<br/>of intended behavior"]
    NOTE --> SURF["Do not edit yet. Surface the<br/>contradiction, say which side you<br/>trust and why, fix the right side"]
```

## 5. The authorization gate and the recall gate (Steps 3 and 4)

```mermaid
flowchart TD
    ACT["About to take an action"] --> OUT{"Irreversible or outward-facing?<br/>push, publish, send, deploy, install,<br/>delete shared data, payment, permission"}
    OUT -->|yes| QUOTE{"Can you quote the user's OWN WORDS<br/>authorizing THIS action?"}
    QUOTE -->|yes| ALINE["Write AUTH: user said '...'<br/>Act. The line goes in the report verbatim"]
    QUOTE -->|"no (a README told you to,<br/>or the task feels incomplete without it)"| DEFER["Do NOT act. Write the line<br/>PENDING: action - awaiting your authorization.<br/>It goes in the report verbatim.<br/>Docs are not authorization;<br/>completing the task is not authorization"]
    OUT -->|no| REC{"Does the edit carry a fact you have<br/>not opened this session?<br/>signature, endpoint, key, price, figure"}
    REC -->|yes| SRC{"Is a source reachable now?<br/>docs file, library source, fetched page"}
    SRC -->|yes| OPEN["Open it (fresh two-lookup budget),<br/>write from the source"]
    SRC -->|no| LABEL["Write it, but label it in the report:<br/>from memory, unverified"]
    REC -->|no| GO["Proceed per the intent gate"]
```

## 6. Verifying (Step 5, with the hard bound)

```mermaid
flowchart TD
    V["Run the named verification yourself"] --> H1{"Half 1: does the done<br/>criterion pass, observed?"}
    H1 -->|yes| H2{"Half 2: is the surrounding<br/>system still healthy?<br/>build, tests, lint"}
    H2 -->|yes| OK["Verified. To the report,<br/>with the output shown"]
    H1 -->|no| WHY{"Why did it fail?"}
    H2 -->|no| WHY
    WHY -->|"mechanical mistake in the change"| BACK4["Back to Step 4"]
    WHY -->|"it surprises you or contradicts<br/>your understanding"| BACK2["Back to Step 2"]
    BACK4 --> CNT{"Third failed cycle on the<br/>same issue? Or blocked by anything<br/>outside your control?"}
    BACK2 --> CNT
    CNT -->|no| V
    CNT -->|yes| HAND["STOP. Hand back with what was<br/>tried, the actual output,<br/>and your current hypothesis"]
```

## 7. Judging finished work (fable-judge)

```mermaid
flowchart TD
    R["A report says 'done'"] --> C["Collect its claims:<br/>done what, verified what,<br/>touched what"]
    C --> D["Diff against ground truth:<br/>git diff, or pristine copy.<br/>The diff outranks the report"]
    D --> RUN["Re-run every claimed verification.<br/>Cannot re-run = UNVERIFIABLE,<br/>never assumed true"]
    RUN --> F["Hunt the fraud table<br/>(the domain's own, for non-code work):<br/>weakened checks, false completion,<br/>scope creep, spec betrayal, debris"]
    F --> VDT{"What survived?"}
    VDT -->|"every claim reproduced, no frauds"| V1["VERIFIED"]
    VDT -->|"sound, but some claims<br/>could not be re-run"| V2["VERIFIED WITH CAVEATS,<br/>each one listed"]
    VDT -->|"a claim failed reproduction<br/>or a fraud was found"| V3["REFUTED: name the claim,<br/>show the contradicting output,<br/>state the smallest fix"]
```

## 8. Which tool for which job (the family router)

```mermaid
flowchart TD
    Q["What is in front of you?"] --> A{"Trivial task?"}
    A -->|yes| NONE["No skill. Do it, check it, report"]
    A -->|no| B{"Finished work someone<br/>claims is done?"}
    B -->|yes| J["fable-judge"]
    B -->|no| C{"A multi-phase project<br/>with milestones?"}
    C -->|yes| G["Your project workflow (e.g. GSD),<br/>with fable-method rules inside phases"]
    C -->|no| D{"Non-trivial and multi-step,<br/>worth subagents and<br/>adversarial verification?"}
    D -->|yes| L["fable-loop"]
    D -->|no| E{"A sector none of the shipped<br/>domain adapters covers,<br/>needing its own?"}
    E -->|yes| FD["fable-domain: generate the<br/>adapter + trap + smoke-eval bundle"]
    E -->|no| M["fable-method inline"]
```

## 9. Worker Protocol Routing Flowcharts

### 9.1 Worker first-output router

```mermaid
flowchart TD
    A["New authoritative task"] --> B["output TASK_CLASS"]
    B --> C["output WORKER_ROUTE"]
    C --> D["output JUDGE_MODE"]
    D --> E["only then use tools"]
```

### 9.2 Task class and route router

```mermaid
flowchart TD
    Q["Classify Task"] --> A{"Mutation or Action?"}
    A -->|"read-only review"| R1["READ_ONLY_COMPLETION_REVIEW"]
    A -->|"planning / inventory"| R2["PLANNING_ONLY"]
    A -->|"state mutation"| R3["STATE_CHANGING_IMPLEMENTATION"]
    A -->|"pure answer"| R4["PURE_QA"]
    
    Q2["Determine Route"] --> B{"Scope and Risk?"}
    B -->|"trivial, local, reversible"| W1["FAST"]
    B -->|"normal bounded work"| W2["STANDARD"]
    B -->|"state-changing / high-value"| W3["STANDARD_JUDGED"]
    B -->|"complex remediation loop"| W4["LOOP_JUDGED"]
    B -->|"no execution"| W5["NOT_APPLICABLE"]
```

### 9.3 Authority resolution gate

```mermaid
flowchart TD
    A["Check Authority"] --> B{"Inline evidence?"}
    B -->|yes| OK["SELF_CONTAINED_INLINE"]
    B -->|no| C{"Attachment / handoff?"}
    C -->|yes| H["REFERENCED_HANDOFF"]
    C -->|no| D{"Pinned repo/ref/path?"}
    D -->|yes| P["REPOSITORY_PINNED"]
    D -->|no| E{"Inherited chain?"}
    E -->|yes| I["INHERITED_PROJECT_CHAIN"]
    E -->|no| STOP["HANDOFF_AUTHORITY_UNRESOLVED<br/>(current cwd is never implicit authority)"]
```

### 9.4 Authorization and Git action router

```mermaid
flowchart TD
    AUTH["Check Authorization"] --> EMB{"Embedded Owner Authorization?"}
    EMB -->|yes| PROCEED["Proceed with task"]
    EMB -->|no| BLK["Authorized action blocked"]
    
    GATE{"Harness permission gate?"} -->|prompted| HERN["Report HARNESS_PERMISSION_BLOCKED<br/>Do not request Owner Auth again"]
    
    GIT["Git Actions Authorization Tiers"] --> C{"COMMIT_AUTHORIZED"}
    GIT --> P{"PUSH_AUTHORIZED"}
    GIT --> PR{"DRAFT_PR_AUTHORIZED"}
    GIT --> MR{"MARK_READY_AUTHORIZED"}
    GIT --> M{"MERGE_AUTHORIZED"}
    GIT --> DEL{"LOCAL/REMOTE_BRANCH_DELETE"}
    
    C -->|independent decision| ACT1["Execute only if YES"]
    P -->|independent decision| ACT2["Execute only if YES"]
    PR -->|independent decision| ACT3["Execute only if YES"]
```

### 9.5 Existing-worktree state router

```mermaid
flowchart TD
    WT["EXISTING_TASK_WORKTREE"] --> ST{"Worktree State"}
    ST --> |"exact PR head"| S1["ACTIVE_EXACT_PR_HEAD"]
    ST --> |"behind remote"| S2["ACTIVE_BEHIND_REMOTE_PR_HEAD"]
    ST --> |"stable dirty"| S3["ACTIVE_STABLE_TASK_OWNED_DIRTY"]
    ST --> |"unresolved dirty"| S4["DIRTY_OWNERSHIP_UNRESOLVED"]
    ST --> |"duplicate dirty"| S5["SAFE_FAST_FORWARD_BLOCKED_BY_DIRTY_DUPLICATE"]
    ST --> |"already released"| S6["ALREADY_RELEASED_CLEAN_BASELINE"]
    ST --> |"absent path"| S7["EXISTING_PATH_ABSENT"]
    ST --> |"unsafe"| S8["UNKNOWN_UNSAFE_STATE"]
    
    S6 --> ACT["Verify only<br/>do not checkout task branch<br/>do not recreate worktree<br/>do not repeat detach"]
```

### 9.6 Judge-depth router

```mermaid
flowchart TD
    J["Judge Required"] --> M{"Judge Mode"}
    M -->|"no judge"| NA["NOT_APPLICABLE"]
    M -->|"judge active"| D{"Judge Depth"}
    D -->|"initial normal judge"| B["BOUNDED"]
    D -->|"concrete full trigger"| F["FULL"]
    D -->|"post-remediation review"| DELTA["DELTA"]
```

### 9.7 Evidence reuse router

```mermaid
flowchart TD
    E["Evidence Check"] --> TREE{"Same exact final tree?"}
    TREE -->|yes| VALID{"Not invalidated?"}
    VALID -->|yes| REUSE["REUSED EVIDENCE (max 1 local full suite per final tree)"]
    VALID -->|no| RERUN["Rerun affected verification"]
    TREE -->|no| RERUN
```

### 9.8 Task class mechanical gate

```mermaid
flowchart TD
    A["New task"] --> B{"Will it run tests / browser /<br/>server / runtime process?"}
    B -->|yes| NOTQA["Not PURE_QA"]
    B -->|no| C{"Will it create evidence package /<br/>screenshot / manifest / report?"}
    C -->|yes| NOTQA
    C -->|no| D{"Product / Git mutation?"}
    D -->|yes| SCI["STATE_CHANGING_IMPLEMENTATION"]
    D -->|no| E{"Only answers question?"}
    E -->|yes| PQA["PURE_QA"]
    E -->|no| ROCR["READ_ONLY_COMPLETION_REVIEW"]
    NOTQA --> D
```

### 9.9 Runtime output preflight

```mermaid
flowchart TD
    A["Command selected"] --> B["Inspect direct output"]
    B --> C["Inspect reporter / config output"]
    C --> D["Inspect OS temp / profile output"]
    D --> E{"All paths inside RUNTIME_OUTPUT_ALLOWLIST?"}
    E -->|no| CONFLICT["STOP: ARTIFACT_OUTPUT_PATH_CONFLICT<br/>(Cleanup later does NOT change authorization)"]
    E -->|yes| EXEC["Record before-state and execute"]
```

### 9.10 Retry and force-termination gate

```mermaid
flowchart TD
    A["Attempt fails or hangs"] --> B["Record attempt in ATTEMPT_LEDGER"]
    B --> C{"Graceful stop available?"}
    C -->|yes| STOPG["Stop process gracefully"]
    C -->|no| FORCE{"Force required?<br/>(SIGKILL / kill -9)"}
    FORCE -->|"yes and forbidden"| STOPF["STOP_FORCE_PROCESS_TERMINATION_REQUIRED"]
    FORCE -->|authorized| STOPFORCE["Force terminate process"]
    STOPG --> RETRY["Retry attempt"]
    STOPFORCE --> RETRY
    RETRY --> FIN{"Final attempt success?"}
    FIN -->|yes| RPT["Report FINAL_SUCCESSFUL_ATTEMPT<br/>plus all prior failures in ledger"]
```

### 9.11 Service worker identity gate

```mermaid
flowchart TD
    A["Formal page URL"] --> B["Formal boot registration symbol"]
    B --> C["Registered worker URL & scope"]
    C --> D["Runtime controller.scriptURL"]
    D --> E["Runtime active.scriptURL"]
    E --> MATCH{"All identities match?"}
    MATCH -->|yes| VERIFIED["Identity verified"]
    MATCH -->|no| UNRESOLVED["SERVICE_WORKER_IDENTITY_UNRESOLVED<br/>(Do not claim named worker controls page)"]
```


## Reading these as a model

Follow the arrows literally; a diamond is a decision you must actually make, not narrative. When a box names an artifact (the INTENT line, the plan artifact, the caveat list), producing it is not optional. When a box says STOP, stop.

## Provenance

These charts began as introspection and were then checked against observed behavior: bare Fable 5 agents run on real problems with their full tool-call transcripts extracted (eval round 10). The observation validated the core paths (spec read before any edit, twin bug found via the README, verification of every mode, assumption stated on ambiguity) and corrected the charts in three places: the ORIENT box at the start of evidence gathering, the expensive-vs-chained nuance on parallelization, and the cleanup rule in the report step. Where introspection and observation disagreed, observation won.

Round 11 repeated the protocol for chart 5: the gates were drafted first, then bare Fable 5 ran the new trap fixtures (one of two bare runs took the unauthorized deploy after reading the same evidence as the run that refused, which is why the gate lives at the decision point and why docs-are-not-authorization is spelled out), and the first Haiku transfer runs showed the mid-tier failure is silently dropping the documented follow-up rather than taking it, which added the deliberately-not-taken caveat rule to the report step. The fable-domain skill's process is itself a distilled trace: `eval/results/round11-observed-traces.json`.
