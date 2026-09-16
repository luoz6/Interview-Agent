# ADR: contracts Boundary

- Status: Accepted
- Date: 2026-09-11
- Scope: repository-level top-level `contracts/`
- Related: P0D-T01

## Decision

`contracts/` is the repository-level executable/governance contract boundary:

- It defines versioned, machine-readable evidence/policy/contract models.
- It is not an application adapter.
- It is not a domain layer.
- It must not import `app.*`.
- `app.*` may import `contracts.*` only for stable pure contract/digest/canonicalization utilities that are framework-free and side-effect-free.

Default target:

```text
contracts → app
禁止

app → contracts
仅稳定纯契约允许
```

## Why contracts is not adapters

Adapters bind application ports to concrete technologies. Contracts do not bind a port to PostgreSQL, Redis, HTTP, or a provider.

Current evidence:

- `contracts/evidence/*` models evidence envelopes, payloads, receipts, digests, verification, and privacy rules.
- `contracts/policies/*` models promotion/release/acceptance policies.
- These are protocol/governance facts, not infrastructure implementations.

If contracts lived under adapters, it would imply contracts can be replaced with another infrastructure implementation. That is incorrect: contracts are the stable rule surface other components must satisfy.

## Why contracts is not domain

Domain owns Interview business rules and transaction invariants. Contracts owns cross-cutting evidence/governance rules used by scripts, tests, and repository acceptance gates.

Current evidence:

- `contracts.evidence` is used by:
  - `scripts/repository_acceptance.py`
  - `scripts/stage43b_recovery_acceptance.py`
  - `tests/contracts/test_evidence_contract.py`
  - `app/ports/postgres_scope.py`
  - `app/adapters/postgres/owned_scope.py`
  - `app/evals/postgres_capacity.py`
- `contracts/` does not import `app.*`.

Placing these in domain would force business-layer modules to depend on release/evidence governance concepts, which is not their responsibility.

## Allowed Importers

The following may import `contracts.*`:

- Repository-level scripts and acceptance runners.
- Tests that verify contract schemas/policies.
- `app` adapters/ports/evals only when they consume stable pure contract primitives such as:
  - canonical JSON/digest helpers
  - privacy-safe payload models
  - HMAC receipt signers
- Future runtime/application modules may import contracts only through explicit stable contract exports.

## Forbidden Direction

`contracts/**` must never import:

```text
app.*
```

This prevents contracts from becoming coupled to runtime composition, SQL, providers, or business services.

## Stable Pure Contract Allowlist

`app → contracts` is allowed only for:

```text
contracts.evidence.canonical
contracts.evidence.digest
contracts.evidence.envelope
contracts.evidence.payloads
contracts.evidence.privacy
contracts.evidence.receipt
contracts.evidence.status
contracts.policies
```

These modules must remain:

- side-effect-free
- framework-free
- deterministic
- import-time safe
- independent of `app.*`

## Current Known app → contracts Imports

Observed current imports:

```text
app/ports/postgres_scope.py
    from contracts.evidence.digest import canonical_sha256

app/adapters/postgres/owned_scope.py
    from contracts.evidence.digest import canonical_sha256

app/evals/postgres_capacity.py
    from contracts.evidence.payloads import CapacityEvidencePayload
```

These are currently accepted because they consume stable pure contract primitives. They must be reviewed if the imported contract becomes application-aware or infrastructure-coupled.

## Consequences

Positive:

- contracts remain independently versioned and testable.
- repository acceptance/evidence logic does not leak into app domain/adapters.
- contract violations are easier to detect.

Costs:

- `app` modules must be explicit when importing contracts.
- `contracts` cannot reuse `app` helpers; any shared utility must live in `contracts` or a lower common package.

## Non-Goals

This ADR does not:

- move `contracts/`.
- change current import statements.
- merge `contracts/` with `app/a2a/contracts`.
- define every future contract payload.
