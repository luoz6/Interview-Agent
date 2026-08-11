# Principal Memory Read Shadow Zero-Injection Runbook

Run Task 7 only after Write Shadow and proposal quality are PASS. Use synthetic,
explicitly confirmed active facts with current Consent and taxonomy. This
runbook permits deterministic would-select only; it never permits consumption.

Execute the isolated 300-sample matrix with the externally approved Owned
Scope. The protected environment must provide the PostgreSQL Approval fields,
`EVIDENCE_REVISION`, `EVIDENCE_HMAC_KEY_ID` and
`EVIDENCE_HMAC_SECRET_B64` described by the Write Shadow runbook:

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.principal_memory_read_shadow `
  --execute --scope-prefix $approvedScopePrefix `
  --output reports/memory/read-shadow-evidence-v1.json --samples 300
~~~

The matrix covers relevant facts, exclusive conflicts, revoked Consent,
deleted source, expiry, unconfirmed proposals, cross-Principal isolation and
fact/token caps. Active facts are created only by explicit synthetic fixture
confirmation; model proposals are not batch-promoted.

Provider Context is canonicalized with Unicode NFC, sorted keys, compact JSON
separators and stable message order. Only equality/violation counts are stored;
digest values and context content are never persisted. On a digest mismatch,
the in-memory canonical snapshot restores the original context and the call
fails open to the deterministic path.

All `hard_invariants` must be zero, P95 regression at 200+ samples must be at
most 20%, cleanup residue must be zero, and Read Shadow must return to disabled.
Question, score, report, evidence and API outputs must remain unchanged.

~~~text
PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS
PROMPT_ISOLATION=PASS
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~

Stop immediately on any unconfirmed/revoked/expired/deleted/cross-Principal or
conflicting selection, context/business-output mutation, cap violation,
privacy artifact hit, or cleanup failure. Do not log a DSN, fingerprint,
prefix, identifier, fact value, digest, Prompt, Answer, Excerpt or payload.
