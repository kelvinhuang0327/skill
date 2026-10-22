
## Artifact budgets and worktree lifecycle

At task start, classify the requested workspace as WORKTREE_LIFECYCLE: PERSISTENT_RESEARCH or TEMPORARY_TASK. If the Packet does not decide, use PERSISTENT_RESEARCH. A temporary task worktree may be auto-closed only when the Owner authorized that exact lifecycle before creation and the task records AUTO_CLOSE_AUTHORIZED: YES. This does not authorize cleanup of pre-existing worktrees.

Use create-worktree to prepare and create the exact absent path, branch, base ref, and task ID. For a temporary task, pass --auto-close-authorized only when the Owner approved that lifecycle before creation; the helper writes its one-use record in Git common metadata immediately before creating the worktree. Do not manually create or adopt a different path. An existing worktree has no matching preauthorization record and cannot pass the close helper.

For this local Codex installation, use scripts/fable_artifact_controls.py for disk admission, temporary Observer/debug JSONL, and eligible temporary-worktree closure. At each task start, classify its storage use and run space-check against the filesystem where the work or outputs will be written; run it again when the task ends. Less than 15 GiB free reports a warning. Less than 8 GiB blocks creation of a research worktree and launch of a high-disk task. Ordinary work may continue with a warning. Read-only diagnostics and Owner-authorized space-recovery work are admitted below either threshold, while still reporting low space. The check is on demand and has no background service:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py space-check --path <existing-output-volume> --operation <ordinary|research-worktree|high-disk-task|read-only-diagnostic|authorized-reclaim> --phase <before|after>

Use create-worktree for every new research worktree so the capacity block runs immediately before git worktree add. Select persistent-research by default. Select temporary-task only when the task has that lifecycle; include --auto-close-authorized or --close-policy AUTO_CLOSE_AFTER_MERGE only when the Owner approved auto-close before the exact worktree is created:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py create-worktree --repo <canonical-repository> --worktree <exact-absent-path> --base <durable-base-ref> --branch <task-branch> --task-id <task-id> --lifecycle <persistent-research|temporary-task> [--auto-close-authorized] [--close-policy <PRESERVE|AUTO_CLOSE_AFTER_MERGE>] [--merge-target-ref <ref>] [--persistent-evidence-root <path>]

Launch high-disk tasks through run-gated so a low-space result prevents the command from starting and the available-space check runs again after it exits:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py run-gated --path <existing-output-volume> --operation <high-disk-task|ordinary|read-only-diagnostic|authorized-reclaim> -- <command and arguments>

Before launching or retrying pytest with an explicit --basetemp, validate the scratch path. It must be outside the repository and every nested Git worktree, and must not contain protected path components used by the data boundary checks. If blocked, select a neutral external scratch path and validate it before launching pytest:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py validate-test-scratch --repo <canonical-repository> --path <pytest-basetemp>

After a failed or blocked execution, inspect the existing records before retrying. evidence-summary reads execution JSON and its durable captures, emits source paths and SHA-256 values, identifies repeated exact output and evidence-backed problem candidates, and writes no summary file. Use candidate findings to propose the smallest change and a verification method; never automatically rewrite Skill rules from a single failure or an unverified aggregate. Treat missing retry, Judge, task-completion, and lifecycle fields as unknown rather than estimating them:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py evidence-summary --repo <canonical-repository> [--task-id <task-id>]

Only temporary, non-formal Observer/debug JSONL belongs in the helper's .fable/temporary/<task-id>/logs/ area. Append one JSON object per event with append-log:

    python3 -B <installed-fable-method>/scripts/fable_artifact_controls.py append-log --repo <canonical-repository> --task-id <task-id> --record-json '<one JSON object>'

It rotates at 1 MiB and caps all active and rotated log bytes at 10 MiB per task. Once the next event would exceed that budget, the helper reports its byte count and SHA-256 and drops the event. The helper does not trim formal captures, receipts, certificates, acceptance evidence, or files named by SHA256SUMS. Keep one full test-output source; summaries and other records should store its path, byte count, and SHA-256. The protected task_checkpoint.rb --run capture remains the canonical complete stdout/stderr record, and its execution JSON points to that capture.

For an authorized temporary worktree close, first save a manifest outside the worktree listing every required formal result by absolute path and SHA-256. Confirm the task is complete and all agents, project runtimes, and task-relevant recurring schedulers are finished. The helper independently checks the repository execution-claim registry, every task execution record and its durable capture, launchd and cron bindings, and open files. It checks these again after five seconds, while re-reading Git state. The worktree must have no tracked changes, untracked files, or ignored files, and its exact HEAD must be merged into the durable base ref (supporting both normal merges and verified squash merges with provenance/tree/patch evidence). Under FABLE_AUTO_WORKTREE_RECLAIM_V1, when a temporary worktree was preauthorized with AUTO_CLOSE_AFTER_MERGE, run close-temporary-worktree with the repository, worktree, base, evidence manifest, and task ID; the helper automatically verifies all safety and quiescence gates without requiring repeated manual authorization flags. Immediately before removal, the helper atomically claims the exact preauthorization record and rechecks its bytes; a missing or changed record blocks removal while preserving the worktree. It then invokes only ordinary git worktree remove with core.fsmonitor=false. It never deletes the branch or uses a force fallback. If any gate is unknown or fails, leave the worktree in place and report the specific blocker.
