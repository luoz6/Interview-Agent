# Principal Memory Proposal Review Protocol

This how-to defines the Task 6 quality review gate. It uses only controlled
synthetic fixtures in this phase; raw candidate material is not authorized.

Review exactly 300 proposals and assign one label: `correct`, `unsupported`,
`over_generalized`, `wrong_taxonomy`, `stale_source`, `conflict`,
`privacy_sensitive`, `not_useful`, `duplicate`, or `review_unavailable`.

Only `correct` is accepted. Every other label is rejected from later Read
Shadow eligibility. The reviewer must compare the proposal with the current
authoritative source, consent purpose, taxonomy and direct-statement rule.

Hard gates are:

- `privacy_sensitive_count=0`;
- `unsupported_rate<0.02`;
- `stale_source_accepted_count=0`;
- at least 300 reviewed proposals;
- all Task 5 Write Shadow invariants remain zero.

The quality artifact stores label counts and rates only. It must not contain a
Principal, Session, Fact or Question identifier, normalized value, Prompt,
Answer, Excerpt, source digest, Resume, DSN, database fingerprint or Provider
payload.

For the synthetic controlled matrix, 285 cases are correct and 15 are
deliberate negative/boundary cases. Negative cases remain rejected and exist
to prove label attribution; they are not accepted proposals.

Successful output permits Task 7 Read Shadow zero-injection only:

~~~text
PRINCIPAL_PROPOSAL_QUALITY=PASS
PRINCIPAL_READ_SHADOW_ZERO_INJECTION=AUTHORIZED_FOR_STAGING
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~
