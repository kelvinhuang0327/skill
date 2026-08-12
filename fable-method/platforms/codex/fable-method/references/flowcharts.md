# The workflow, drawn

## Contents

1. [Master router](#1-master-router)
2. [Packet and route router](#2-packet-and-route-router)
3. [Evidence and intent gates](#3-evidence-and-intent-gates)
4. [Verification and Judge](#4-verification-and-judge)
5. [Family router](#5-family-router)

These charts are executable summaries. Diamonds are decisions that require
observation; they do not add rules beyond `SKILL.md` and the linked references.

## 1. Master router

```mermaid
flowchart TD
    IN["Any incoming ask"] --> TC{"Task class?"}
    TC -->|"STATE_CHANGING_IMPLEMENTATION"| OUT["Emit TASK_CLASS + WORKER_ROUTE + JUDGE_MODE"]
    TC -->|"READ_ONLY_COMPLETION_REVIEW"| J["Use fable-judge; no Worker route"]
    TC -->|"PLANNING_ONLY"| P["Plan only; no implementation lifecycle"]
    TC -->|"PURE_QA"| Q["Answer; change nothing"]
    OUT --> TRIV{"One file, <10 lines,<br/>no new behavior/searching?"}
    TRIV -->|yes| FAST["Make change, run obvious check, report"]
    TRIV -->|no or unsure| FIT{"Where does the answer live?"}
    FIT -->|"reachable source"| SHAPE{"Question, plan, or task?"}
    FIT -->|"researchable unknown"| RES["Bounded research, then loop"]
    FIT -->|"only inference"| INF["Flag low confidence or ask one question"]
    SHAPE -->|question| Q
    SHAPE -->|plan| P
    SHAPE -->|task| ROUTE["Packet/preflight route"]
    RES --> ROUTE
```

If evidence disproves the initial task class, emit `TASK_CLASS_RECLASSIFIED`
with `FROM`, `TO`, `EVIDENCE`, and `IMPACT_ON_ROUTE` before switching branches.

## 2. Packet and route router

```mermaid
flowchart TD
    T["State-changing task"] --> PS{"Packet state?"}
    PS -->|PRESENT| PC{"Packet conflicts with invariant or live repo?"}
    PS -->|PARTIAL| AC{"Can minimal acceptance be inferred?"}
    PS -->|ABSENT| CONTRACT["Create minimum execution contract"]
    PC -->|no| KEEP["Use Packet; do not re-plan"]
    PC -->|yes| OV{"Explicit Owner override?"}
    OV -->|no| STOP["PLANNER_PACKET_CONTRACT_CONFLICT"]
    OV -->|yes| KEEP
    AC -->|no| BLOCK["BLOCKED_MISSING_VERIFIABLE_ACCEPTANCE"]
    AC -->|yes| KEEP
    CONTRACT --> PF["Bounded preflight"]
    KEEP --> PF
    PF --> JG{"Judge gate?"}
    JG -->|yes| LOOPCAP{"Loop capability + eligibility all YES?"}
    LOOPCAP -->|yes| LOOP["LOOP_JUDGED; integrate then Judge"]
    LOOPCAP -->|no| STDJ["STANDARD_JUDGED; serial Worker then Judge"]
    JG -->|no| LOCAL{"Known low-risk local target?"}
    LOCAL -->|yes| FAST["FAST"]
    LOCAL -->|no| STD["STANDARD"]
```

## 3. Evidence and intent gates

```mermaid
flowchart TD
    ORIENT["Enumerate safe sources"] --> READ["Read primary source and direct chain"]
    READ --> SURPRISE{"Contradiction?"}
    SURPRISE -->|yes| ROUTE["State surprise; update done or class"]
    SURPRISE -->|no| DONE["Define observable done"]
    ROUTE --> DONE
    DONE --> INTENT["INTENT: code X; check Y; spec Z"]
    INTENT --> AGREE{"X, Y, Z agree?"}
    AGREE -->|no| CONFLICT["Surface conflict; do not edit"]
    AGREE -->|yes| AUTH{"Outward or irreversible action?"}
    AUTH -->|yes and quoted| ACT["Record AUTH; act"]
    AUTH -->|yes without quote| PEND["Do not act; record PENDING"]
    AUTH -->|no| ACT
```

## 4. Verification and Judge

```mermaid
flowchart TD
    RUN["Run named acceptance"] --> A{"Done criterion observed?"}
    A -->|no| LADDER["Harness → execution chain → product"]
    A -->|yes| B{"Surrounding build/test/lint healthy?"}
    B -->|no| LADDER
    B -->|yes| TWIN["If defect fixed, search exact wrong construct"]
    TWIN --> REPORT["Report evidence and ledger"]
    LADDER --> RETRY{"Evidence-backed attempts < 3?"}
    RETRY -->|yes| FIX["Falsifiable correction and real rerun"]
    RETRY -->|no| STOP["BLOCKED_AFTER_THREE_EVIDENCE_BACKED_ATTEMPTS"]
    REPORT --> GATE{"Judge gate?"}
    GATE -->|no| HANDOFF["Worker handoff"]
    GATE -->|yes| DEPTH["BOUNDED / FULL / DELTA fresh Judge"]
    DEPTH --> VERDICT["Judge verdict; at most one remediation"]
```

## 5. Family router

```mermaid
flowchart TD
    ASK["What is in front of you?"] --> CLASS{"Task class?"}
    CLASS -->|implementation| WORKER["fable-method Worker"]
    CLASS -->|completion review| JUDGE["fable-judge read-only"]
    CLASS -->|plan or QA| FINDINGS["No implementation lifecycle"]
    WORKER --> ROUTE{"Worker route?"}
    ROUTE -->|"FAST or STANDARD"| INLINE["Execute in main Worker"]
    ROUTE -->|STANDARD_JUDGED| INDEPENDENT["Serial Worker, then Judge"]
    ROUTE -->|LOOP_JUDGED| BACKEND["fable-loop cards, integration, then Judge"]
```
