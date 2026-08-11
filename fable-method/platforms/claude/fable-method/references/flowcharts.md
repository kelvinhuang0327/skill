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

## 1a. The state-changing Worker router

A `task`-shaped, coding-domain ask (chart 1's `DOM -->|coding| LOOP2` branch) that turns out to be state-changing enters this router. The reviewed-head lifecycle exception is decided here — right after bounded preflight, before the normal Judge Gate — never inside chart 1's generic loop.

```mermaid
flowchart TD
    PRE["Bounded preflight complete<br/>(canonical repo, branch, HEAD,<br/>worktree state, packet vs. repo)"] --> LIFEX{"Reviewed-head lifecycle<br/>exception valid?"}
    LIFEX -->|"YES - all six hold: independent<br/>fixed-head review PASS/VERIFIED;<br/>live PR head == reviewed head;<br/>exact-head CI green; changed paths<br/>== reviewed scope; no source/test<br/>changes this task; only authorized<br/>lifecycle actions (mark ready, merge,<br/>post-merge verification, branch<br/>cleanup, worktree restoration)"| LIFEOK["TASK_CLASS: STATE_CHANGING_IMPLEMENTATION<br/>WORKER_ROUTE: STANDARD<br/>JUDGE_MODE: NOT_APPLICABLE<br/>-> authorized lifecycle actions<br/>-> LIFECYCLE_CLOSURE"]
    LIFEX -->|"NO - condition unmet, or head/<br/>scope/CI/mergeability/authorization<br/>drifted since the review. Never<br/>merge a replacement head under<br/>the exception"| JGATE["Continue the normal Judge Gate:<br/>Route Decision (FAST / STANDARD /<br/>STANDARD_JUDGED / LOOP_JUDGED),<br/>then a Judge pass if the gate fires"]
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
    WHY --> LADDER["Failure Attribution Ladder:<br/>1 harness wrong? 2 new code<br/>actually running? 3 then the product"]
    LADDER -->|"mechanical mistake in the change"| BACK4["Back to Step 4"]
    LADDER -->|"it surprises you or contradicts<br/>your understanding"| BACK2["Back to Step 2"]
    BACK4 --> CNT{"Third evidence-backed attempt<br/>on the same issue? Or blocked by<br/>anything outside your control?"}
    BACK2 --> CNT
    CNT -->|"no (2nd attempt)"| FULLLADDER["Attempt Semantics: 2nd failure<br/>forces the full ladder before<br/>touching anything again"]
    FULLLADDER --> V
    CNT -->|"no (1st attempt)"| V
    CNT -->|yes| HAND["BLOCKED_AFTER_THREE_EVIDENCE_BACKED_ATTEMPTS.<br/>Hand back with each hypothesis,<br/>change, command, and real output"]
```

## 7. Judging finished work (fable-judge)

```mermaid
flowchart TD
    START["A report says 'done'"] --> FC{"Fresh context, no memory<br/>of the Worker's own reasoning?"}
    FC -->|yes| MODE["JUDGE_MODE: FRESH_CONTEXT"]
    FC -->|no| MODE2["JUDGE_MODE: SELF_CHECK_ONLY<br/>(can never output plain VERIFIED)"]
    MODE --> R["Re-read the Planner Packet<br/>(the packet, not the Worker's<br/>restatement of it)"]
    MODE2 --> R
    R --> C["Collect its claims:<br/>done what, verified what,<br/>touched what"]
    C --> D["Diff against ground truth:<br/>git diff, or pristine copy.<br/>The diff outranks the report"]
    D --> DEPTH{"Judge depth?"}
    DEPTH -->|"BOUNDED (default first pass)"| BND["Focused independent reproduction<br/>+ reuse valid same-final-tree evidence.<br/>Do not re-run an already-valid full suite"]
    DEPTH -->|"FULL (named trigger: security/auth,<br/>migration, payment, irreversible effect,<br/>deployment, un-isolable shared core,<br/>or Worker evidence missing/invalid)"| FUL["Independently reproduce load-bearing<br/>criteria; own the local full suite only<br/>if Worker's same-tree evidence is<br/>missing or invalidated"]
    DEPTH -->|"DELTA (re-judge after<br/>one bounded remediation)"| DEL["Check only: original finding,<br/>remediation diff, finding-specific tests,<br/>impacted regression slice, scope, ledger"]
    BND --> CHK["No valid reused evidence and no<br/>reproduction this pass = NOT RUN.<br/>Genuinely irreproducible = UNVERIFIABLE.<br/>Never assumed true either way"]
    FUL --> CHK
    DEL --> CHK
    CHK --> F["Hunt the fraud table<br/>(the domain's own, for non-code work):<br/>weakened checks, false completion,<br/>scope creep, spec betrayal, debris"]
    F --> VDT{"What survived?"}
    VDT -->|"every claim reproduced, no frauds,<br/>FRESH_CONTEXT"| V1["VERIFIED"]
    VDT -->|"sound, but some claims<br/>could not be re-run, or SELF_CHECK_ONLY"| V2["VERIFIED_WITH_CAVEATS,<br/>each caveat listed"]
    VDT -->|"a claim failed reproduction<br/>or a fraud was found"| V3["REFUTED: name the claim,<br/>show the contradicting output,<br/>state the smallest fix.<br/>One bounded remediation cycle,<br/>then re-judge; still REFUTED -><br/>BLOCKED_AFTER_JUDGE_REFUTATION"]
    VDT -->|"environment/credentials/access<br/>not available to check a claim"| V4["UNVERIFIABLE"]
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
    D -->|yes| GATES{"Passes fable-method's Loop<br/>Capability Gate AND Loop<br/>Eligibility Gate? (hard/risky/<br/>many-files/slow-tests alone<br/>do not pass the gates)"}
    GATES -->|yes| L["fable-loop (LOOP_JUDGED)"]
    GATES -->|no| STDJ["fable-method STANDARD<br/>or STANDARD_JUDGED instead"]
    D -->|no| E{"A sector none of the shipped<br/>domain adapters covers,<br/>needing its own?"}
    E -->|yes| FD["fable-domain: generate the<br/>adapter + trap + smoke-eval bundle"]
    E -->|no| M["fable-method inline"]
```

## Reading these as a model

Follow the arrows literally; a diamond is a decision you must actually make, not narrative. When a box names an artifact (the INTENT line, the plan artifact, the caveat list), producing it is not optional. When a box says STOP, stop.

## Provenance

These charts began as introspection and were then checked against observed behavior: bare Fable 5 agents run on real problems with their full tool-call transcripts extracted (eval round 10). The observation validated the core paths (spec read before any edit, twin bug found via the README, verification of every mode, assumption stated on ambiguity) and corrected the charts in three places: the ORIENT box at the start of evidence gathering, the expensive-vs-chained nuance on parallelization, and the cleanup rule in the report step. Where introspection and observation disagreed, observation won.

Round 11 repeated the protocol for chart 5: the gates were drafted first, then bare Fable 5 ran the new trap fixtures (one of two bare runs took the unauthorized deploy after reading the same evidence as the run that refused, which is why the gate lives at the decision point and why docs-are-not-authorization is spelled out), and the first Haiku transfer runs showed the mid-tier failure is silently dropping the documented follow-up rather than taking it, which added the deliberately-not-taken caveat rule to the report step. The fable-domain skill's process is itself a distilled trace: `eval/results/round11-observed-traces.json`.
