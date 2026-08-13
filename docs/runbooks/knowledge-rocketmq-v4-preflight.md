# RocketMQ V4 Knowledge Preflight

Run this gate before any request to load or promote the active knowledge corpus.
It is offline and read-only: it does not connect to PostgreSQL, call an embedding
provider, write artifacts, enable Hybrid, or activate Shadow.

## Command

```powershell
F:\python3.11\python.exe -m scripts.knowledge_rocketmq_v4_preflight
```

A successful repository check exits with code `0` and prints one privacy-safe
JSON object. The result must have:

```text
passed = true
repository_ready = true
external_release_ready = false
corpus.version = memory-p1-zh-v4
corpus.chunk_count = 31
rocketmq.chunk_count = 5
rocketmq.active_kafka_chunk_count = 0
runtime_defaults.engine = legacy
runtime_defaults.hybrid_rollout_percent = 0
runtime_defaults.shadow_enabled = false
legacy_compatibility.frozen = true
```

The gate rebuilds the manifest and requires the committed payload to match. It
also constructs every runtime chunk, validates the 12-case pilot and 18-case
Memory P1 datasets against current manifest IDs, resolves the reviewed
`rocketmq-delivery` Knowledge Unit, and verifies that the frozen 25-chunk V1
Kafka identity has not changed.

The output deliberately excludes query text, knowledge body, references, URLs,
JD, resume, answers, provider credentials, and database configuration.

## Failure codes

- `ACTIVE_MANIFEST_DRIFT`: the committed manifest differs from a clean rebuild;
- `ACTIVE_CORPUS_IDENTITY_MISMATCH`: version, count, or hash is not the approved
  `memory-p1-zh-v4` repository identity;
- `RUNTIME_CHUNK_CONTRACT_MISMATCH`: runtime metadata, topic, schema, count, or
  corpus binding is invalid;
- `ROCKETMQ_CORPUS_BOUNDARY_MISMATCH`: the five RocketMQ identities are missing
  or an active Kafka chunk is present;
- `ROCKETMQ_COVERAGE_MISMATCH`: active coverage does not select RocketMQ or
  incorrectly selects Kafka;
- `ACTIVE_DATASET_IDENTITY_MISMATCH`: an active dataset version or manifest
  reference is wrong;
- `ROCKETMQ_PILOT_UNIT_MISMATCH`: the reviewed pilot Unit does not resolve;
- `UNSAFE_RUNTIME_DEFAULTS`: Legacy, 0% rollout, or Shadow-off defaults changed;
- `FROZEN_V1_IDENTITY_MISMATCH`: historical Kafka compatibility was rewritten;
- `PREFLIGHT_INPUT_INVALID`: an input cannot be parsed or schema validation
  fails.

## What a pass does not authorize

`external_release_ready` is always false. A pass is not permission to load
pgvector, call a real embedding provider, run protected PostgreSQL tests,
activate Shadow, advance Canary, or claim production readiness. Those actions
still require their independent evaluation artifacts, approvals, credentials,
owned environment, observation windows, and rollback evidence.

## Read-only PostgreSQL target inspection

After the offline gate passes, inspect the configured pgvector target without
creating, updating, activating, or deleting any row:

```powershell
F:\python3.11\python.exe -m scripts.knowledge_rocketmq_v4_target_preflight
```

The connection uses a read-only transaction and a five-second statement
timeout. Output excludes the DSN and credential values. It reports the physical
target fingerprint, database name, pgvector version, versioned table presence,
release identities, active corpus identity, and the external gates that are
still absent.

`write_ready=true` requires all of the following at the same time:

- a non-expired `POSTGRES_TEST_*` approval whose database allowlist and physical
  fingerprint match the inspected target;
- `EMBEDDING_PROVIDER=siliconflow`;
- an explicit model name and a fixed model revision other than
  `siliconflow-current`;
- a configured SiliconFlow credential;
- `RUN_KNOWLEDGE_ROCKETMQ_V4_LOAD=1`;
- pgvector and the versioned release tables present;
- no different active corpus that would be retired by activation.

The command does not synthesize an approval receipt. If approval, fixed model
identity, or operator authorization is absent, it exits non-zero and leaves the
database unchanged.
