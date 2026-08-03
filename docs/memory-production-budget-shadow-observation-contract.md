# Production Budget Shadow observation contract

This reference defines the offline aggregate input and sanitized observation
used after an explicitly approved Production Budget Shadow window. It does not
authorize a window, change configuration, query production systems, or contain
an external approval record.

## Schemas

The trusted metrics system exports:

```text
memory-production-budget-shadow-aggregate-input-v1
```

The offline sanitizer emits:

```text
memory-production-budget-shadow-observation-v1
```

The input and output are UTF-8 JSON objects with exact field sets. Unknown
fields are rejected instead of copied through.

## Allowed information

- public immutable Git revision;
- `BUDGET_SHADOW_ONLY` phase;
- booleans for approval, revision, scope, window, configuration, cleanup, and
  rollback verification;
- approved and observed traffic percentages;
- warm-up and observation duration;
- aggregate follow-up, control, Shadow, would-select, would-drop, fallback,
  error, latency, and stop counts;
- fixed language buckets: `zh_hans`, `en`, `mixed`, `other`;
- fixed path buckets: `answer`, `skip`, `timeout`, `other`.

All numeric values must be finite and non-negative. The approved traffic cap
must be greater than zero and no greater than 1%. Observed traffic may be zero;
zero evidence cannot produce PASS.

## Forbidden information

The contract rejects:

- the external approval record or its path;
- record, deployment, change-ticket, or approver digests;
- environment, cluster, database, schema, or table locators;
- DSNs, credentials, tokens, secrets, and private keys;
- session, principal, fact, question, message, or artifact IDs;
- Prompt, answer, resume, report, source excerpt, or Provider payload;
- unknown language/path values and free-text metric labels.

## Offline boundary

Run from the exact approved revision:

```powershell
& 'F:\python3.11\python.exe' `
  -m scripts.memory_production_budget_shadow_observation `
  --aggregate-input '<outside-repository-aggregate-input>' `
  --output '<outside-repository-sanitized-observation>'
```

Both paths must be outside the repository. The command has no application
runtime, database, HTTP, Provider, or deployment-system dependency. It prints
`RUNNER_CONFIGURATION_CHANGED=false`.

## Immutable authorization boundary

Every sanitized observation retains:

```text
principal_write_shadow_production=NOT_AUTHORIZED
principal_read_shadow_production=NOT_AUTHORIZED
long_term_memory_consumption=BLOCKED
```

The sanitized artifact proves only that an aggregate export conforms to this
repository contract. Privacy and Security must still review it before any
repository publication.
