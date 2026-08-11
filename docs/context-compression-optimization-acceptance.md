# Adaptive Context Compression Repository Acceptance

## Decision

The repository acceptance command is:

```powershell
F:\python3.11\python.exe scripts/context_compression_repository_acceptance.py
```

A successful run emits exactly:

```text
READY_FOR_SHADOW
```

This status means the repository is ready for a separately authorized deployed
shadow observation. It is not production readiness, deployment authorization,
or permission to enable compression consumption. Tasks 11 and 12 remain outside
this acceptance boundary.

## Fail-closed preflight

Before running tests, the entry point verifies all of the following:

- the historical memory-system plan remains traceable to its pinned Spec;
- every one of the 27 `MEM-CTX-*` requirements occurs exactly once as a
  normative Spec requirement, is referenced by the adaptive plan, and has one
  Spec verification mapping;
- every test module declared by adaptive Tasks 0 through 10 is present in the
  fixed suite or in the explicit reviewed exemption manifest;
- the recovery matrix contains exactly 24 scenarios and every scenario points
  to at least one module in the fixed suite;
- every fixed-suite path exists, with no duplicates;
- committed rollout, enforcement, consumption, and trusted-local defaults remain
  disabled;
- bounded acceptance artifacts contain neither secret sentinels nor blocked raw
  content, identifier, credential, digest, or owner-key fields.

The reviewed exemption manifest is intentionally empty. Adding an exemption
requires an exact `tests/*.py` path and a rationale prefixed with `reviewed:`;
unknown or unreviewed entries fail the gate.

## Fixed test suite

The fixed suite is the exact Task 10 matrix:

```text
tests/test_memory_config.py
tests/test_agent_runtime_composition.py
tests/test_context_budget.py
tests/test_context_selection.py
tests/test_context_source_identity.py
tests/test_context_compression_eligibility.py
tests/test_context_compressor.py
tests/test_context_compression_validation.py
tests/test_context_compression_runner.py
tests/test_context_artifacts.py
tests/test_context_artifact_contracts.py
tests/test_context_artifact_store_postgres.py
tests/test_interview_context_artifacts.py
tests/test_evidence_context_artifacts.py
tests/test_question_memory.py
tests/test_question_memory_retrieval.py
tests/test_question_memory_recovery.py
tests/test_interview_status_projection.py
tests/test_context_compression_failure_containment.py
tests/test_context_compression_failure_store_postgres.py
tests/test_context_compression_shadow_acceptance.py
tests/test_durable_interview_state.py
tests/test_durable_interview_graph.py
tests/test_session_deletion_worker.py
tests/test_memory_metrics.py
tests/test_memory_system_optimization_acceptance.py
tests/test_context_compression_repository_acceptance.py
```

Pytest runs with the `pg_runtime` marker excluded. PostgreSQL contract tests use
fakes or test doubles unless a separately authorized runtime test explicitly
enables a real database.

## Scenario evidence

| Required scenario | Primary executable evidence |
|---|---|
| All gates disabled | `test_memory_config.py`, `test_agent_runtime_composition.py` |
| Short shadow context | `test_context_compression_eligibility.py` |
| Follow-up demand 6,687 / availability 8,360 stays below threshold | `test_context_compression_eligibility.py` |
| Rounded 8,000 bp but cross-product below threshold | `test_context_compression_eligibility.py` |
| 80% pre-loss shadow | `test_context_compression_eligibility.py`, `test_context_compression_runner.py` |
| Deduplication shadow | `test_context_source_identity.py`, `test_context_selection.py` |
| Business eligible while post-dedup shadow is below threshold | `test_context_compression_eligibility.py`, `test_evidence_context_artifacts.py` |
| Deduplication enforcement | `test_context_source_identity.py`, `test_context_selection.py` |
| Valid Artifact consumption | `test_context_compression_runner.py`, `test_interview_context_artifacts.py` |
| Completed Artifact reuse | `test_context_artifacts.py`, `test_context_compression_runner.py` |
| Invalid compression fallback | `test_context_compression_validation.py`, `test_context_compression_runner.py` |
| Provider circuit open | `test_context_compression_failure_containment.py` |
| Validation source quarantined | `test_context_compression_failure_containment.py` |
| Same text under two question identities | `test_context_source_identity.py`, `test_question_memory.py` |
| Oversized mandatory bounded-raw set | `test_context_budget.py`, `test_context_selection.py` |
| Identity-v0 reload | `test_context_artifact_contracts.py`, `test_context_artifact_store_postgres.py` |
| Identity-v1 reload | `test_context_artifact_contracts.py`, `test_context_artifact_store_postgres.py` |
| Quarantined source owner isolation | failure-containment and failure-store tests |
| Concurrent half-open probes | failure-containment and failure-store tests |
| Parent lease loss | runner and durable-graph tests |
| Digest conflict | Artifact-store and runner tests |
| Existing v1 checkpoint | `test_durable_interview_state.py` |
| Existing v2 compatibility checkpoint | durable-state and durable-graph tests |
| Session deletion | `test_session_deletion_worker.py` |

The synthetic golden acceptance dataset supplies fixed inputs, a fixed
timezone-aware clock, and an injected replay Provider. Its results are
deterministic, the model judge is advisory only, and all 16 case observations
must validate against the production `CompressionObservation` model.

## External-service boundary

The acceptance entry point creates a sanitized child-process environment. It
removes OpenAI, Azure OpenAI, Anthropic, Google/Gemini, Cohere, and Mistral API
keys, removes `POSTGRES_DSN` and `DATABASE_URL`, disables the opt-in Task 8 live
PostgreSQL tests, disables third-party pytest plugin auto-loading, and excludes
the `pg_runtime` marker. Therefore the fixed repository gate makes zero real
Provider calls and zero real PostgreSQL calls.

PostgreSQL-focused modules validate mappings, fencing, conflicts, persistence,
retention, and deletion through controlled doubles. Runtime constructors are
not permitted to create schema; schema changes remain migration-owned.

## Authority and rollback boundary

Raw messages and Evidence remain authoritative. Compression Artifacts and the
Interview Semantic Status are non-authoritative projections. Any Provider,
validation, lease, store, metrics, or projection failure must preserve the
deterministic business fallback. Compression, task-aware consumption, budget
enforcement, and deduplication enforcement remain independently reversible and
disabled by committed default.

`READY_FOR_SHADOW` authorizes none of the following without a separate user and
operator decision: real Provider traffic, real PostgreSQL execution, migration,
deployed shadow observation, consume canary, rollout percentage changes, or
production promotion.
