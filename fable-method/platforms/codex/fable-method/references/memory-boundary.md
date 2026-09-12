# Memory boundary

Use this before reading project memory as if it were authority, or before
creating or modifying any memory, handoff, or checkpoint file.

Memory is context only and never repository authority. It cannot establish
authorization, HEAD/tree, test results, completion, deployment, or publication
status. Live repository state, Git state, and freshly executed verification
override conflicting memory. Read project memory only when the active contract
or Packet identifies it as relevant. Do not create or modify `.ai/`,
`MEMORY.md`, memory logs, persistent handoffs, checkpoints, or agent-state files
unless the Packet authorizes the exact path and purpose; an ordinary final
report never authorizes a persistent memory write. Product-level ChatGPT,
Codex, Claude, or Gemini memory settings are outside Fable Method authority
and must not be changed.
