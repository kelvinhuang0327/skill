# Workspace containment

Use this before creating a worktree, clone, or any sibling workspace
directory.

The canonical repository, or an explicitly named existing worktree, is the only
persistent write root. `LOCAL_COMMIT_ONLY` does not authorize a clone, sibling
repository, new worktree, evidence directory, backup directory, agent-state
directory, or `*-agent`, `*-pNN`, `*-validation`, or `*-evidence` directory.
Never run `git worktree add`, `git clone`, or copy/rsync the repository outside
the canonical root unless the Packet contains all of:

```text
CREATE_EXTERNAL_WORKSPACE: YES
EXACT_ABSOLUTE_PATH: <path>
CLEANUP_DISPOSITION: <retain-or-remove>
```

If an external workspace appears useful but these fields are absent, continue
inside the canonical repository when safe; otherwise stop for exact-path
authorization. Disposable intermediate output may use only an OS temporary
directory and must not become a persistent project sibling. Report unexpected
external paths and never automatically delete them. Do not modify, move, or
delete any pre-existing sibling directory.

Containment is task-relative to the current execution interval: compare only
T0 and T1. A path absent at T0 is
`HISTORICAL_EXTERNAL_ABSENCE_ACCEPTED_AS_CURRENT_BASELINE`; do not infer its
history or recreate it. An unattributed sibling change is
`EXTERNAL_WORKSPACE_CHANGE_OBSERVED`, report-only, and non-blocking; only direct
task attribution or impact on the canonical repository or a required input can
fail containment.
