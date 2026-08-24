# Domain adapter: devops and infrastructure

Applies when the deliverable changes how a system runs: infrastructure-as-code,
CI/CD, deployment or rollback scripts, monitoring, alerts, runbooks, or
postmortems. Coding remains the adapter for a script's own logic; this adapter
takes over when correctness depends on live state, blast radius, or an
irreversible action.

## Minimum evidence set (binding, before any change is applied)

1. **Current live state**: running config, deployed version, or infra state,
   never assumed to match the repository.
2. **Governing runbook or policy**: change-management doc, SLO, or on-call
   runbook; state the assumption if none exists.
3. **One live platform reference**: current provider docs or CLI behavior,
   fetched now.

## Evidence and primary sources

Observed system state, plan output, re-read config, metrics, and logs are
primary; IaC is a claim about what should run. A green pipeline or zero exit is
not proof of health; post-change health evidence is.

## Authority order

Explicit user/owner instruction > runbook/policy > observed platform behavior >
IaC intent > “this should be fine.” If repo and system disagree, diagnose from
the running system and name which side caused drift.

## Verification by observation

- Confirm application to the target system with read-after-change evidence.
- Name blast radius before irreversible/shared-state action and review a
  rollback or dry-run path.
- Check service health after the change; do not loosen alerts to look clean.
- Deploy, apply, rotate, revoke, restart, or edit shared/prod infra only with
  quoted user authorization.

## Fraud table (for fable-judge)

| Fraud | Symptom |
|---|---|
| Big-bang deploy | All traffic changes with no staged rollout or blast radius |
| Silenced alerting | Threshold widened or check disabled instead of fixing cause |
| Untested rollback | Rollback is claimed but never reviewed or dry-run |
| Config drift denial | Repo is claimed to match live state without checking |
| Fabricated postmortem | Root cause or timeline is not reproduced from logs |
| Secret in the clear | Credentials appear in IaC, configs, or logs |
| Unauthorized production touch | Shared/prod action has no quoted authorization |

## Done, by example

“The staging deploy is done” means plan reviewed, change confirmed live,
health checked, rollback stated, and any unauthorized prod/shared step named as
pending. Not: “the pipeline is green.”

## Sources

- Google SRE Workbook, “Canarying Releases”: https://sre.google/workbook/canarying-releases/ (accessed 2026-07-11)
- Google SRE Book, “Postmortem Culture”: https://sre.google/sre-book/postmortem-culture/ (accessed 2026-07-11)
- AWS Well-Architected, “Make frequent, small, reversible changes”: https://docs.aws.amazon.com/wellarchitected/latest/operational-excellence-pillar/ops_dev_integ_freq_sm_rev_chg.html (accessed 2026-07-11)
- OWASP, “Secrets Management Cheat Sheet”: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html (accessed 2026-07-11)
- CIS Benchmarks: https://www.cisecurity.org/cis-benchmarks (accessed 2026-07-11)
