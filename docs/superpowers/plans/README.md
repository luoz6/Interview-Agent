# Historical implementation plans

This directory is an immutable archive of implementation plans written for the repository state identified by each filename. The documents explain past decisions and acceptance work; they are not current runbooks, release gates, or command references.

## Execution rules

- Do not execute a command from an archived plan without resolving it against the current repository first.
- Paths, module names, test locations, fixed test counts, revisions, run IDs, ports, and generated-artifact locations may have been retired or replaced.
- A command in an archived plan does not become a required gate merely because the historical document still contains it.
- Do not edit historical plan bodies to make old results appear current. Record replacements in current authoritative documentation instead.

## Current authoritative sources

- Refactoring scope, status, dependencies, and remaining work: `docs/refactoring-plan.md`.
- Structured requirements, decisions, tasks, gates, runbooks, and release state: `contracts/*.yaml`.
- Generated maintainer references: `docs/generated/`.
- Local runtime operation: `docs/local-v1-runbook.md`.
- Current repository commands: `README.md`, current runbooks referenced by `contracts/runbooks.yaml`, and the CLI `--help` output.

## Retired command families

The following historical modules have been replaced and must not be restored merely to make an archived command runnable:

| Historical command family | Current owner |
|---|---|
| `scripts.audit_stage40_artifacts` | `python -m scripts.release_artifact_audit --profile stage40` |
| `scripts.audit_stage42_artifacts` | `python -m scripts.release_artifact_audit --profile stage42` |
| `scripts.audit_stage44a_artifacts` | `python -m scripts.release_artifact_audit --profile stage44a` |
| `scripts.audit_stage44b1_artifacts` | `python -m scripts.release_artifact_audit --profile stage44b1` |
| `scripts.run_stage44a_acceptance` | `python -m scripts.knowledge_acceptance stage44a` |
| `scripts.run_stage44b1_acceptance` | `python -m scripts.knowledge_acceptance stage44b1` |
| `scripts.langgraph_stage47_acceptance` | `python -m scripts.repository_acceptance stage47` |
| `scripts.agent_runtime_stage47_2_acceptance` | `python -m scripts.repository_acceptance stage47_2` |
| `scripts.postgres_stage48_acceptance` | `python -m scripts.repository_acceptance stage48` |
| `scripts.langgraph_stage49_acceptance` | `python -m scripts.repository_acceptance stage49` |
| `scripts.langgraph_recovery_acceptance` | `python -m scripts.langgraph_acceptance recovery` |
| `scripts.langgraph_dual_workflow_acceptance` | `python -m scripts.langgraph_acceptance dual` |

Legacy API, Service Config, Draft, Session Error, and PostgreSQL Repository aggregation modules referenced by older plans have also been removed after their callers migrated to authoritative modules. Their historical references are evidence of the earlier repository shape, not active compatibility requirements.
