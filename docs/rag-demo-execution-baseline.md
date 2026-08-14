# RAG Learning Demo Execution Baseline

> HISTORICAL BASELINE SNAPSHOT. Use the current architecture, Eval and Console guides for active instructions.

Status: active implementation baseline  
Captured: 2026-08-14 (Asia/Hong_Kong)  
Purpose: reference evidence for the RAG learning/demo simplification work

## Source identity

| Item | Value |
|---|---|
| Baseline commit | `36c273e687315f536596e160e3c9833e451fc216` |
| Implementation branch | `codex/rag-learning-demo-simplification-v1` |
| Archive ref | `archive/rag-production-governance-v1` |
| Approved plan | `C:\Users\admin\Downloads\2026-08-14-interview-agent-rag-learning-demo-simplification-plan.md` |
| Plan SHA-256 at baseline freeze | `5bf49de13bf26940e6abb56a889b66f3b529fc0632f62eebd1b4692570f7a603` |
| Current revised plan SHA-256 | `d249321bd075c47c692eeb9acd7f61fada151fe4acf6a22752a93d4fee7f2256` |

The archive ref preserves the complete pre-simplification rollout, Shadow,
promotion, release-governance, and business-evaluation implementation. It is the
recovery source if a later migration cannot preserve runtime or artifact
compatibility.

## Runtime identity before modification

| Field | Value |
|---|---|
| Formal engine | `legacy` |
| Candidate engine | `hybrid-v2` |
| Hybrid rollout | `0%` |
| Shadow | disabled |
| Corpus version | `memory-p1-zh-v4` |
| Corpus manifest SHA-256 | `deb709817c6ea1ac89db8f0452f1183d0168952d5d568e08b704869c90555e84` |
| Chunk count | `31` |
| Embedding provider | `siliconflow` |
| Embedding model | `BAAI/bge-m3` |
| Embedding revision | `siliconflow-bge-m3-rmqv4-2026-08-13` |

Capturing this baseline did not publish a corpus, call the embedding provider,
run a business-schema migration, or modify PostgreSQL data.

## Verification baseline

### RAG and Knowledge affected matrix

The offline unit, contract, acceptance, and API architecture matrix selected all
`test_knowledge*.py` and `test_rag*.py` files in the relevant suites plus the API
router architecture test:

```text
282 passed
1 pre-existing Starlette TestClient deprecation warning
```

### Frontend

```text
Vitest:                 12 files / 130 tests passed
ESLint:                 passed with zero warnings
Production build:       passed
Initial JS gzip budget: passed (67,551 <= 67,584 bytes)
Initial CSS budget:     passed
Protected-route lazy:   passed
```

### Repository-wide backend suite

The unfiltered repository-wide suite was executed to preserve honest baseline
evidence:

```text
4,366 passed
15 skipped
70 failed
130 setup errors
1 warning
```

The setup errors are dominated by configured PostgreSQL tests correctly refusing
to run without a current structured external-scope approval. The remaining
failures include the already-known incompatibility between the local business
PostgreSQL schema and the current application migration level. These failures
exist at the untouched baseline and are not treated as a green gate for this
RAG-only refactor.

No protected PostgreSQL test, schema migration, corpus activation, or Provider
call is authorized by this baseline record. Final verification must compare the
same affected offline matrix and frontend gates, and must report protected
PostgreSQL coverage separately unless a new valid authorization is present.

## Completion evidence policy

Each implementation stage must record evidence that covers its actual change:

- corpus recovery requires deterministic unit tests and, when separately
  authorized, isolated PostgreSQL validation;
- runtime migration requires legacy-state compatibility, explicit-engine, and
  fallback tests;
- Compare Mode requires backend contract, privacy, cancellation, and frontend
  behavior tests;
- evaluation simplification requires diagnostic-integrity and historical
  artifact compatibility tests;
- UI/documentation work requires frontend tests, lint, build, bundle budgets,
  active-document scans, and accurate runtime/API state.

The final audit must not claim that the repository-wide backend suite is green
unless the protected PostgreSQL scope is valid and every remaining baseline
failure has been resolved or explicitly removed from the product scope.
