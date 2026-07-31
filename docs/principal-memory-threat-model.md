# Principal Memory Threat Model

Principal Memory is an isolated, consented, non-authoritative personal fact system. It is not public knowledge, scoring evidence, a hiring signal, or a Prompt input in this phase. The only implemented read path is zero-injection read-shadow.

| Threat | Boundary and mitigation | Verification |
|---|---|---|
| Principal collision | Identity is explicitly resolved; no resume/contact/device/network/model inference or automatic merging | identity source audit and cross-principal store tests |
| Deployment confusion | Every consent, fact, query, transition and purge is scoped by deployment plus principal | in-memory/PostgreSQL isolation tests |
| Stale consent | Consent is read at proposal execution, fact confirmation and every read-shadow operation | revoke-after-enqueue and revoke-before-read tests |
| Source deletion race | Worker and lifecycle re-read authoritative session state; deletion purges source facts before business-session removal | deletion worker fault matrix and tombstone replay |
| Prompt injection | Provider output is schema-validated; taxonomy and exact excerpt are revalidated against authoritative messages; model output remains proposed | extractor/task privacy tests |
| Taxonomy bypass | Canonical JSON accepts one allowlisted key/value and Unicode NFC; free text, scores, company/project/personality and hiring conclusions are rejected | contract tests |
| Public corpus contamination | Principal stores have no vector column or public corpus foreign key; knowledge code cannot import Principal Fact Store | knowledge firewall tests |
| Metric/log leakage | Metrics contain aggregate counts only; trace sanitizer blocks principal/fact/source/normalized fields | metric and trace sentinel tests |
| Backup resurrection | Operator tombstone replay re-runs session-scoped Principal Memory purge | deletion replay contract |
| Malicious replay | Fact and effect IDs are deterministic; writes are idempotent; transitions use expected version | replay and CAS tests |
| Cross-principal cache collision | No shared artifact/vector cache is used for Principal Memory; all reads include deployment/principal | isolation tests |
| Historical score anchoring | Evaluation/report/knowledge paths cannot read Principal Fact Store and read-shadow does not modify scoring evidence | Prompt/scoring source audit |

Residual risk: natural-language intent cannot be fully proven automatically. The system therefore stores only controlled taxonomy values, retains exact-source digests, requires explicit confirmation before activation, and blocks consumption. Production observation and authenticated self-service identity are not part of this phase.
