# The workflow, drawn

## Contents

1. [Master router](#1-master-router)
2. [Complexity router](#2-complexity-router)
3. [Verification](#3-verification)
4. [Family router](#4-family-router)

These charts summarize `SKILL.md`; they add no rules.

## 1. Master router

```mermaid
flowchart TD
    IN["Any incoming ask"] --> TC{"Task class?"}
    TC -->|"STATE_CHANGING_IMPLEMENTATION"| OUT["Emit routing block"]
    TC -->|"READ_ONLY_COMPLETION_REVIEW"| J["Use fable-judge; no Worker route"]
    TC -->|"PLANNING_ONLY"| P["Plan only; change nothing"]
    TC -->|"PURE_QA"| Q["Answer; change nothing"]
    OUT --> CX["Complexity router"]
```

If evidence disproves the initial task class, emit `TASK_CLASS_RECLASSIFIED`
with `FROM`, `TO`, `EVIDENCE`, and `IMPACT_ON_ROUTE` before switching branches.

## 2. Complexity router

```mermaid
flowchart TD
    T["State-changing task"] --> PK{"Packet supplies route and depth?"}
    PK -->|yes| USE["Use them"]
    PK -->|no| SIMPLE{"One known target, small change,<br/>no new behavior?"}
    SIMPLE -->|yes| FAST["FAST: implement → needed test → report"]
    SIMPLE -->|no| HARD{"Implementation-depth trigger?"}
    HARD -->|no| STD["STANDARD / NORMAL:<br/>implement → focused verification → report"]
    HARD -->|yes| ENH["STANDARD / ENHANCED:<br/>bounded exploration → implement →<br/>correctness verification → report"]
    USE --> JG{"Packet or Owner asks for a Judge?"}
    FAST --> JG
    STD --> JG
    ENH --> JG
    JG -->|no| DONE["Report"]
    JG -->|yes| JUDGE["Judge handoff"]
```

## 3. Verification

```mermaid
flowchart TD
    RUN["Run the task's acceptance checks"] --> A{"Observed result meets acceptance?"}
    A -->|yes| REPORT["Report evidence"]
    A -->|no| HYP{"Next step tests a hypothesis?"}
    HYP -->|yes| FIX["Test it; correct; rerun"]
    FIX --> RUN
    HYP -->|no| STOP["BLOCKED; report what ran out"]
```

## 4. Family router

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
