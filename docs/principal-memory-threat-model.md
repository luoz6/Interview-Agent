# Principal Memory threat model

Principal Memory is an isolated, consented, non-authoritative personal fact
system. It is not public knowledge, scoring evidence, a hiring signal, or a
Prompt input in this phase. The only implemented read path is zero-injection
Read Shadow. Principal Memory consumption is blocked.

## Assets and trust boundaries

The protected assets are explicit Principal identity, purpose-scoped Consent,
source-bound proposed and active facts, lifecycle state, deletion tombstones,
and aggregate Shadow evidence. Interview questions, candidate responses,
resumes, reports, source excerpts, source digests, fact values, and provider
payloads must not appear in operational evidence.

The trust boundaries are:

1. An authenticated or explicitly configured identity resolver supplies the
   deployment and Principal. Resume/contact data, request metadata, device
   identifiers, embeddings, and model output are never identity sources.
2. Consent authorizes one named purpose at operation time. `proposal_write`,
   `fact_storage`, and `read_shadow` do not imply one another.
3. The extractor is untrusted. Its output is schema checked, exact-source
   checked, canonicalized to one allowlisted taxonomy value, and stored only as
   an unconfirmed proposal.
4. Public Knowledge and Principal Memory are separate stores and dependency
   graphs. Neither ingestion nor retrieval crosses that boundary.
5. Read Shadow may observe eligibility and selection but cannot mutate Prompt,
   provider request, questions, scoring evidence, report data, or business
   state.

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

## Fairness boundary

The taxonomy rejects personality, integrity, emotion, mental or physical
health, politics, religion, ethnicity, race, marital or pregnancy status, age,
hiring recommendations, recruiting outcomes, and historical scores. An
approved accessibility preference is limited to a user-declared UI/interaction
enum. It is not scoring evidence and remains excluded from Prompt and reports.
Any protected-category proposal, selection, or behavior mutation is a privacy
hard stop, not merely a quality label.

## Prompt-injection boundary

Candidate text may request permanent storage, Consent bypass, automatic active
status, scoring changes, public Knowledge writes, cross-Principal disclosure,
or deletion bypass. Such text is data, never an instruction to the memory
system. A valid extraction can only become `model_proposed`, `proposed`, and
`user_confirmed=false`; invalid or non-taxonomy output is rejected. Revoked or
missing Consent prevents the proposal event or cancels it at execution.

## Failure response

Cross-Principal access, no-Consent access, protected-category content, Prompt
isolation failure, public Knowledge mutation, or private observation-artifact
content immediately blocks Shadow expansion and new Shadow worker leasing. The
corresponding modes return to disabled, minimal aggregate evidence is retained,
and the deterministic Interview path remains available. Privacy-scope failures
require operator and privacy-owner notification.

## Residual risk

Natural-language intent cannot be fully proven automatically. Controlled
taxonomy reduces but does not eliminate inference and proxy risk. Source
digests prove provenance boundaries, not semantic truth. The system therefore
requires explicit confirmation before activation, performs operation-time
Consent checks, keeps Read Shadow at zero injection, audits proposal quality,
and blocks consumption. Production observation and authenticated self-service
identity are outside this phase.
