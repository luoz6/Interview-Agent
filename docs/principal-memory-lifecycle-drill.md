# Principal Memory Lifecycle and Deletion Drill

Run this Task 8 how-to only with synthetic Principals in the strict isolated
Staging prefix. The drill grants operation-specific Consent, creates proposals,
explicitly confirms and rejects facts, verifies supersede semantics, performs
Read Shadow, revokes Consent, then purges Session facts and the Principal
Consent record.

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.principal_memory_lifecycle_drill `
  --execute --expected-database-fingerprint $approvedFingerprint
~~~

The race matrix revokes Consent during extraction and after selection but
before observation commit. Both boundaries must fail closed with zero unsafe
writes or selections. Confirm also rechecks `fact_storage` Consent after source
validation, immediately before transition.

Accept only when active/superseded/rejected counts are each one, selection
stops immediately after revoke, Session purge deletes all three source facts,
Principal purge removes Consent, all residues are zero and the isolated prefix
cleanup is zero. Do not persist identifiers, fact values, source locators,
Prompt content, DSNs, fingerprints or prefixes.

~~~text
PRINCIPAL_MEMORY_LIFECYCLE_DRILL=PASS
CONSENT_RACE_SAFETY=PASS
PRIVATE_DATA_RESIDUE=0
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~
