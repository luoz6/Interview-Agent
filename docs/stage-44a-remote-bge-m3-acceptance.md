# Stage 44A Remote BGE-M3 Acceptance

Status: `PASS`

Acceptance run: `2026-07-22T05:41:27Z`

Final verification: `2026-07-22T06:33:12Z`

## Scope

Stage 44A replaces the local embedding runtime with the explicitly enabled
SiliconFlow `BAAI/bge-m3` provider, activates the existing 25-unit corpus
through versioned pgvector tables, and preserves Stage 42 retrieval and
historical evidence contracts. It uses the existing `interview` PostgreSQL
database and adds no database, container, multi-user behavior, or voice
capability.

## Release Identity

| Field | Accepted value |
| --- | --- |
| Run ID | `20260722T054127Z-stage44a-bge-m3` |
| Provider | `siliconflow` |
| Model | `BAAI/bge-m3` |
| Model revision | `siliconflow-bge-m3-20260721` |
| Dimension | 1024 |
| Table prefix | `knowledge_chunks_stage44a_rc` |
| Corpus version | `stage44a-bge-m3-v1` |
| Active chunks | 25 |
| Corpus manifest SHA-256 | `44f2eba0bfb87e99cfd4bfb4834d2ed8e5f97b79eb51e0a46782decb075a8beb` |
| Dataset version | `stage42-knowledge-retrieval-v1` |
| Dataset SHA-256 | `0b0ac1788285be786c5f50c92f4309d17a134404f1b292d4fe228326133fad97` |
| Implementation baseline | `9a6487d` |
| Final verification commit | `0c4134b8f5b35f560a8b5a765ebda83cbe0f8ef6` |
| Artifact path | `reports/stage44a-acceptance/20260722T054127Z-stage44a-bge-m3` |

## Gates

| Gate | Status | Evidence |
| --- | --- | --- |
| Provider/configuration contracts | PASS | Focused Stage 44A suite: 113 passed and 9 pgvector-gated tests; the 9 pgvector tests passed separately against `interview` |
| SiliconFlow retry/redaction contracts | PASS | MockTransport covers batching, retry classification, response validation, redaction, and safe metrics |
| Real SiliconFlow provider | PASS | 32 requests, zero retries, zero provider errors |
| Versioned PostgreSQL schema | PASS | Isolated schema, activation, search, and historical lookup tests passed |
| Atomic 25-unit activation | PASS | Active corpus identity and count match the sealed acceptance metrics |
| V1 retrieval metrics | PASS | All Stage 42 metric gates passed |
| Historical evidence replay | PASS | Retired hash lookup passed and Reviewer lookup made zero embedding calls |
| Artifact privacy audit | PASS | 32 whitelisted files, 14,707 bytes, zero privacy violations |
| Python regression | PASS | 820 passed, 1 explicitly gated real-LLM smoke test skipped |
| JavaScript/CSS/browser regression | PASS | CSS build and 7 JavaScript syntax checks passed; Playwright: 15 passed, 9 expected project/credential skips |
| Stage 40 regression | PASS | Existing 163-file artifact inventory audited successfully |
| Stage 42 regression | PASS | Existing five-file sealed inventory audited after CRLF/LF canonicalization; historical manifest was not rewritten |
| Stage 43 regression | PASS | Included in the complete Python and browser suites |
| No local embedding model | PASS | Clean lock environment has neither local model package; `pip check` and 3 focused tests passed |
| Secret rotation and isolation | PASS | Operator confirmed local-only rotated credential setup; the credential is absent from code, environment templates, logs, and sealed artifacts |

## Retrieval Metrics

| Metric | Result | Gate |
| --- | ---: | ---: |
| Hit@3 | 1.00 | >= 0.90 |
| Mean reciprocal rank | 0.98 | >= 0.75 |
| Question/evidence binding | 1.00 | 1.00 |
| Evidence continuity | 1.00 | 1.00 |
| Invalid reference rate | 0.00 | 0.00 |
| False-positive rate | 0.20 | <= 0.20 |
| Observation completeness | 1.00 | 1.00 |
| Retrieval p95 | 326.968 ms | <= 1500 ms |

Provider HTTP latency was 255.759 ms at p50 and 298.446 ms at p95. No retry
or error code was recorded.

## Storage Strategy

Stage 44A uses exact pgvector cosine scan. It creates no IVFFLAT or HNSW
index. With 25 active chunks and a measured retrieval p95 of 326.968 ms, an ANN
index is not justified. ANN may be reconsidered in Stage 44B or later only when
corpus-size and latency measurements demonstrate a need.

## Privacy And Reproducibility

The Stage 44A auditor verified the manifest, relative paths, sizes, SHA-256
hashes, whitelist, passing metrics, and sensitive-content rules for all 32
artifacts. The acceptance evidence contains no API key, DSN, authorization
header, absolute path, request text, knowledge content, provider response,
resume, job description, email, or phone number.

The Stage 42 audit fix canonicalizes CRLF to LF only for `.json` and `.md`
inventory bytes. Binary files and other suffixes remain byte-for-byte strict,
privacy scanning still reads original content, and the historical Stage 42
manifest remains unchanged.
