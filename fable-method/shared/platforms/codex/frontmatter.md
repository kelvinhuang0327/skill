---
name: fable-method
description: Lightweight Fable Worker router. A fresh agent reads the full Task Packet, judges task complexity, and runs the matching flow (FAST, STANDARD, or STANDARD with ENHANCED depth), verifying only what the task needs. Use when the user invokes /fable-method, supplies a Planner Packet, or requests a non-trivial state-changing task without a more specific skill. Do not enter the implementation flow for pure questions, planning-only requests, or read-only completion reviews; route reviews to fable-judge.
---
