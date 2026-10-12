# Authority sources

A claim can be wrong not because the reasoning was bad but because the
observed signal was never the authoritative source for that claim. Independent
re-reads of the same non-authoritative signal agree with each other and are
still wrong together, so this is a lookup problem, not a reasoning-depth
problem. Use this before treating a load-bearing signal as ground truth, and
add a row here only for a real recurring incident — this is a table of known
traps, not a general reference on how to investigate.

| Claim type | Authoritative source | Known misleading signal | Minimal safe check | Still STOP when |
|---|---|---|---|---|
| Remote branch's current state | `git ls-remote origin refs/heads/<branch>`, or a fresh fetch | A local `origin/<branch>` tracking ref that has not been fetched recently; a checkout sitting on an old or unrelated branch | Compare the tracking ref against a fresh `ls-remote` before trusting either | Authority to act on the true remote state (push/merge/publish) is itself unresolved |
| Which repository a `gh`/git command targets | `gh repo view`, or an explicit `-R owner/repo` on every call | Bare `gh` resolves the repo from cwd; the harness resets cwd between calls, so "not found" can be a wrong-repo artifact | Pass `-R owner/repo` explicitly, or confirm cwd immediately before the call | The correct repository is itself ambiguous or unnamed by the task |
| CI/check status for a commit | The check-runs API for the exact head SHA (`gh pr checks`, or `.../commits/<sha>/check-runs`) | The legacy combined-status endpoint can report "pending" with zero contexts on a repo with no combined-status checks configured, which reads like a real pending check | Read check-runs for the exact SHA, not just combined status | A check is genuinely failing or not yet run |
| CLI flag/option support | The exact installed binary's real behavior, or its registered-options list, for the version actually in use | `--help` omitting a flag; a hidden or undocumented option can still be registered and functional | Try the flag against the real binary, or inspect it directly, scoped to the exact installed version | Behavior differs by version and the running version is unconfirmed |
| Shared/canonical file content or line count | `git show <confirmed-fresh-canonical-ref>:<path>` | Reading the same path from a local checkout on an old or unrelated branch, which can be behind canonical without the working tree looking dirty | Confirm the ref actually read from equals a freshly verified canonical ref before trusting content or counts from it | The canonical ref itself cannot be established |
| "Resource/manifest unreadable" style errors | The actual filesystem layout at the failure site | The error text can read like content corruption when the real cause is a missing file the code expects to sit beside the one you moved or extracted | Inspect the directory the failing read expects, not only the target file | The missing dependency is something you lack authority to add or restore |
| A local note, checkpoint, or memory about a path or historical state | The live filesystem/repository at the time of use | Point-in-time notes describe a path, SHA, or PID that may since have moved, been renamed, or exited | Verify existence or current value before concluding something is missing, unchanged, or present | Verification itself is unsafe or unauthorized, or stays genuinely inconclusive — report that honestly rather than trusting the note or guessing |
| A test-suite red result | The failing assertion's actual content, under the project's required locale/environment | A default `C` locale can produce spurious encoding-related failures unrelated to the code change | Rerun under the project's required locale/environment before trusting red as real | The failure persists under the correct environment |

None of these checks grant new authorization. A resolved authority-source
check can turn a false `BLOCKED`/`STOP` into progress; it never turns a real
one into permission to act.
