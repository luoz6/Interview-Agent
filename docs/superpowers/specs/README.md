# Historical design specifications

This directory is an immutable archive of design snapshots written for the repository state identified by each filename. The documents preserve earlier problem framing, design decisions, proposed paths, and acceptance ideas; they are not current runbooks, release gates, or command references.

## Execution rules

- Do not execute commands or restore files merely because an archived specification mentions them.
- Module names, test paths, stage names, schemas, fixed test counts, revisions, and generated-artifact locations may have been retired or replaced.
- Do not edit archived specification bodies to make historical designs appear current. Record replacements in current authoritative documentation instead.
- Resolve any useful historical idea against the current contracts and repository before implementation.

## Current authoritative sources

- Refactoring scope, status, dependencies, and remaining work: `docs/refactoring-plan.md`.
- Structured requirements, decisions, tasks, gates, runbooks, and release state: `contracts/*.yaml`.
- Generated maintainer references: `docs/generated/`.
- Local runtime operation: `docs/local-v1-runbook.md`.
- Current repository commands: `README.md`, current runbooks referenced by `contracts/runbooks.yaml`, and the relevant CLI `--help` output.

Old module names, test paths, and stage labels found in this directory are historical evidence. Their presence does not require the repository to restore them or make them executable.
