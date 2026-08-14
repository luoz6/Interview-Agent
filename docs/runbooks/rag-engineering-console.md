# RAG Engineering Console Runbook

The RAG engineering console is a local diagnostic and corpus-governance surface.
It is disabled by default and accepts only a real loopback client address.
Forwarded headers do not grant access. Corpus writes require an additional,
fail-closed capability and two explicit release confirmations.

## Runtime invariants

Enabling the console does not change Knowledge RAG rollout state. Keep the formal
runtime on these defaults until the independent promotion workflow approves a
change:

```text
KNOWLEDGE_ENGINE=legacy
KNOWLEDGE_HYBRID_ROLLOUT_PERCENT=0
KNOWLEDGE_SHADOW_ENABLED=false
KNOWLEDGE_REMOTE_RERANKER_ENABLED=false
```

The Overview page must continue to report promotion as blocked while independent
human tuning Ground Truth, No-evidence policy validation, and business blind A/B
are incomplete.

## Enable only the required capability

All switches default to `false`:

```text
RAG_DIAGNOSTIC_UI_ENABLED=true
RAG_LIVE_INSPECTOR_ENABLED=false
RAG_EVAL_ARTIFACT_ACCESS_ENABLED=false
RAG_EVAL_AUTHORED_QUERY_ACCESS_ENABLED=false
RAG_CORPUS_WRITE_ENABLED=false
RAG_DIAGNOSTIC_ACCESS_MODE=loopback
```

- `RAG_DIAGNOSTIC_UI_ENABLED` exposes Overview, Evidence Trace, and Corpus catalog.
- `RAG_LIVE_INSPECTOR_ENABLED` additionally permits synchronous query inspection.
- `RAG_EVAL_ARTIFACT_ACCESS_ENABLED` permits allowlisted tuning Artifact reads and
  replay. The Catalog also exposes the already-viewed 25-case machine holdout only
  as `historical_diagnostic`; it is not a sealed holdout or formal promotion
  evidence. Sealed/private holdout Artifacts remain unavailable through ordinary
  console endpoints.
- Authored Eval queries are not returned by the current API even when the reserved
  capability is enabled.
- `RAG_CORPUS_WRITE_ENABLED` permits loopback-only draft validation and explicit
  release activation. Validation never calls the embedding Provider. Activation
  requires both cost and release confirmations, verifies the active manifest,
  reuses unchanged embeddings, and only embeds new content.

Restart the API process after changing environment configuration. When a required
capability is off, or the client is not loopback, the endpoint intentionally
returns `404`.

## Snapshot-enabled Eval runs

Future Eval V3 runs can atomically freeze diagnostic sidecars from the same
retrieval results used to calculate the metrics Artifact:

```powershell
python scripts\evaluate_knowledge_retrieval_v3.py run `
  --engine hybrid-v2 `
  --ablation weighted-rrf `
  --split tuning `
  --output eval\knowledge-v3\machine-preannotation\candidate.json `
  --diagnostic-snapshot-root eval\knowledge-v3\diagnostic-snapshots
```

The runner refuses to overwrite a frozen Artifact or sidecar directory. If
sidecar publication fails, it removes the newly written metrics Artifact so the
run cannot be mistaken for a complete diagnostic publication. Historical
Artifacts without validated sidecars remain `partial_historical`; the console
never reruns retrieval to fill missing history.

## Diagnostic and corpus-governance endpoints

The current console surface includes:

```text
GET  /api/rag/overview
POST /api/rag/inspections
GET  /api/rag/evaluations
GET  /api/rag/evaluations/{artifact_sha256}
GET  /api/rag/evaluations-paired
GET  /api/rag/evaluations/{artifact_sha256}/cases
GET  /api/rag/evaluations/{artifact_sha256}/no-evidence
GET  /api/rag/evaluations/{artifact_sha256}/cases/{case_id}/diagnostic-snapshot
GET  /api/rag/evidence-traces/{trace_id}
GET  /api/rag/corpus
POST /api/rag/corpus/drafts/validate
POST /api/rag/corpus/releases/activate
```

Artifact detail is the authoritative identity/governance projection for a frozen
run. Paired comparison deltas and No-evidence counts/rates are computed by the
backend; the browser does not infer them. Artifact replay is Provider-free. A
current-engine rerun is a separate Live Inspection operation, requires the live
capability and authored query access, may incur Provider cost, and must never
overwrite the frozen Artifact. The current UI does not offer rerun for historical
cases whose raw query is unavailable.

Corpus catalog reads project the authoritative active pgvector release when it is
available. Safe detail contains identity/classification/hash metadata only, never
the full body, source URL, or ingestion locator.

The browser may import UTF-8 `.md` or `.txt` files, but sends corpus entries as
JSON so multipart support is not required. Draft content remains in React memory;
it must not enter the URL, localStorage, sessionStorage, logs, or validation error
responses. A release request must contain the same entry, its validation SHA-256,
the expected active manifest SHA-256, a unique corpus version, and both explicit
confirmations. The server revalidates all fields and content before any Provider
call. A manifest mismatch returns `409` and requires a fresh validation.

Managed entries are stored below `app/data/knowledge_v2/extensions/console` and a
successful release atomically updates `manifest.json`. The existing release is
retired only inside the pgvector activation transaction. If embedding generation
fails, the new managed source is removed and the active release remains unchanged.

## Privacy and troubleshooting

- Live queries exist only in component memory and the synchronous POST body.
- Responses and Snapshot sidecars contain query hashes and safe metadata, never
  raw queries, full Knowledge bodies, embeddings, provider payloads, resumes, JDs,
  answers, or chain-of-thought.
- Evidence Trace accepts an opaque session identifier and projects only persisted
  evidence lineage. Missing consumer/follow-up records are `not_recorded`; they are
  not inferred.
- Live Inspection is protected by a process-local non-blocking capacity lane. It
  limits whole diagnostic requests without replacing the Hybrid coordinator's own
  Semantic/Lexical/Rerank capacity controls.
- `404`: capability disabled, non-loopback request, sealed Artifact, or unknown ID.
- `422`: invalid profile, query, or opaque reference. Validation responses are
  stable and do not reflect a rejected query/profile value.
- `429`: live diagnostic capacity is saturated. The stable code is
  `RAG_DIAGNOSTIC_CAPACITY_EXHAUSTED`, and the response is retryable.
- `503`: the configured retrieval or persisted lineage store is unavailable.

## Protected PostgreSQL verification

Do not run a protected PostgreSQL test merely because a DSN is locally reachable.
The fixture requires all of the following externally issued metadata:

```text
POSTGRES_TEST_APPROVAL_ID
POSTGRES_TEST_APPROVAL_RECEIPT_SHA256
POSTGRES_TEST_APPROVED_FINGERPRINT
POSTGRES_TEST_DATABASE_ALLOWLIST
POSTGRES_TEST_APPROVAL_EXPIRES_AT
```

The approved target must match, the approval must be unexpired, and the test must
use generated `test_*` table prefixes with cleanup. The protected
`test_postgres_round_trips_hash_only_audit` node already receives its prefix from
the shared `runtime_table_prefix` fixture, which opens and tracks an owned scope
before any database access. Other legacy pgvector tests using `knowledge_<uuid>`
are outside this node's authorization and must not borrow it unchanged. If any
approval field is missing, record the PostgreSQL gate as an external
authorization blocker; do not invent metadata or treat the stopped write as a
pass.

Latest local-only verification on 2026-08-13:

```text
Focused RAG backend:              41 passed
Relevant Knowledge/RAG:          316 passed; 1 protected node deselected
Architecture + Acceptance:       441 passed
Frontend:                        12 files / 129 tests passed
ESLint / production build:       passed
OpenAPI:                         60 paths / 66 operations
Initial JavaScript gzip:         67,470 / 67,584 bytes
RAG lazy chunk:                  39.44 kB raw / 11.19 kB gzip
```

The earlier unfiltered relevant Knowledge/RAG command reported `316 passed` and
one setup error listing the five missing approval fields. That historical setup
error occurred before connection or write and was the external gate, not a
product regression.

Formal protected-node verification on 2026-08-14:

```text
Approval ID:                       interview-rag-console-pg-20260814-001
Authorization receipt SHA-256:     6efbb174ea1a70268228e99d578bca67e0331c30bc6611397101cf7821a216c4
Approved / actual database:        interview / interview
Approved / actual fingerprint:     5e025dd48cab1ffe94fb19b4837cafa66c247e323a1246cb2354f18ba3b0136e
Protected pytest node:              1 passed in 0.87s
Owned-scope cleanup:                ownership verified; target verified
Post-run prefix-family residue:     0
```

The immutable local authorization receipt is stored as
`%TEMP%\interview-rag-console-pg-20260814-001.json` and is read-only. Its five
`POSTGRES_TEST_*` bindings were injected only into the protected pytest child
process. This approval covers only
`test_postgres_round_trips_hash_only_audit`; do not reuse it for another node,
for legacy `knowledge_<uuid>` integration tests, or for business tables.

Before handing off a change, run the RAG contract/unit/architecture tests, all
architecture and acceptance tests, frontend lint/tests/build, and `git diff --check`.
Also verify the effective defaults remain Legacy, 0% Hybrid rollout, Shadow off,
remote reranker off, all console capabilities off, and promotion blocked.
