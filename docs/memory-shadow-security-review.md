# Memory Shadow security, privacy, and fairness review

This operational review is for security reviewers, privacy owners, and Shadow
operators. It validates the current synthetic zero-injection release candidate.
It does not authorize production Shadow or Principal Memory consumption.

## Review outcome

The review passes only when every row below has machine evidence and no hard
stop is present.

| Area | Required evidence | Passing condition |
|---|---|---|
| Identity | explicit/absent resolver tests and cross-Principal store isolation | no inference from resume, contact, network, device, browser, or embedding data; exact deployment/Principal match |
| Consent | purpose and policy-version matrix plus revoke races | purposes do not imply one another; old policy and revoked Consent fail closed |
| Prompt injection | seven synthetic attack intents | no automatic active/confirmed fact, no scoring/report/Knowledge mutation, no cross-Principal disclosure |
| Fairness | taxonomy audit and zero-injection behavior test | no protected, personality, hiring, or historical-score category; no question/scoring/report mutation |
| Knowledge Firewall | dependency/source audit, loader rejection, no embedding conversion | zero Principal dependency or scope in public Knowledge paths |
| Deletion | lifecycle, tombstone, and old-backup restore drills | private residue zero and public Knowledge unchanged |
| Artifacts | recursive observation/evidence/status audit | no private key, subject locator, DSN, source, Prompt, answer, resume, report, or provider payload |

The approved accessibility taxonomy is a bounded, directly declared
interaction accommodation. It remains outside scoring, reports, and Prompt.
This exception must not be generalized into health or disability inference.

## Execute the review

```powershell
python -m pytest -q tests/contracts/test_memory_shadow_privacy.py tests/unit/test_memory_shadow_fairness.py tests/architecture/test_principal_memory_isolation.py tests/architecture/test_principal_memory_consumption_boundary.py tests/unit/test_principal_memory_isolation.py
python -m scripts.memory_shadow_security_review --execute --output reports/memory/records/security-review-record.json
$record = 'reports/memory/records/security-review-record.json'
$digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $record).Hash.ToLowerInvariant()
python -m scripts.memory_operational_input_evidence security --input-record $record --expected-input-sha256 $digest --synthetic
```

The runner audits repository observation artifacts whose names contain
`observation`, `evidence`, or `status` and whose extensions are JSON, JSONL,
log, Markdown, or text. It stores only aggregate counts. It never stores the
offending value or path in evidence.

The first file is a hash-bound intermediate review record, not trusted release
evidence. The second command converts it to the strict
`OperationalSecurityEvidencePayload`, applies its Policy, and writes the signed
`reports/memory/operational-security-evidence-v1.json` Bundle. Do not copy the
intermediate record into `docs/` or provide it directly to Operational Shadow.

## Hard-stop response

Any cross-Principal or no-Consent write/read, protected taxonomy hit, Prompt or
business-state mutation, public Knowledge dependency/mutation, private artifact
content, or deletion residue causes `review_status=BLOCKED`. The operator must:

1. keep all relevant modes disabled;
2. stop new Shadow worker leasing and prohibit expansion;
3. retain the tombstone and minimal aggregate evidence;
4. preserve the deterministic Interview path;
5. notify the operator and, for privacy-scope failures, the privacy owner;
6. remediate and rerun the complete review from a clean revision.

Passing this review permits inclusion in the operational Shadow approval
packet only. It does not permit a production canary, Prompt injection of facts,
or consumption implementation.
