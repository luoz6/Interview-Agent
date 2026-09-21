# MA0-T06 - Multi-Agent Architecture Ratchet

## Status

```text
TASK = MA0-T06
STATUS = COMPLETE
SCHEDULER_ARCHITECTURE_RATCHET = ACTIVE
TASK_ACCEPTANCE = PASS
NEXT_TASK = NOT_STARTED
```

The ratchet is enforced by
`tests/architecture/test_scheduler_architecture_ratchet.py` using the existing
AST dependency scanner in `tools/architecture/scan_dependencies.py`. It does not
import application modules while scanning.

## Zero-Tolerance Rules

| Source prefix | Forbidden target prefix | Current violations |
| --- | --- | ---: |
| `app.application.scheduling` | `app.adapters` | 0 |
| `app.application.scheduling` | `app.runtime` | 0 |
| `app.application.scheduling` | `app.a2a` | 0 |
| `app.ports` | `app.a2a` | 0 |
| `app.domain` | `app.a2a` | 0 |

Matching includes both the named module and every descendant module. The
`app.a2a` rules use the module prefix directly because the general scanner maps
that package to its `other` layer rather than to a dedicated layer.

## Enforcement Model

- The production-tree test scans all current `app/**/*.py` files and requires
  the complete forbidden-edge set to be empty.
- There is no Scheduler baseline, allowlist, grandfathered violation, or legacy
  exception. Any matching edge fails the test immediately.
- `app/application/scheduling` does not exist yet. An absent slice is a valid
  zero state; files added below that path are included automatically by the
  repository-wide scan.
- Existing dependencies outside the five rules do not become Scheduler
  exceptions and are not copied into this gate.
- Scanner self-tests build temporary modules and prove detection of all five
  rules through `import`, `from ... import ...`, and relative import syntax.
- Parse errors fail the production-tree gate rather than producing an
  unverified zero count.

## Generated Architecture Artifacts

Adding the T06 test changed repository-wide test-file inventory metadata. The
existing generators refreshed:

- `artifacts/architecture/dead-code-scan.json`
- `artifacts/architecture/dead-code-scan.md`
- `artifacts/architecture/legacy-version-removal.json`
- `artifacts/architecture/legacy-version-removal.md`

The committed baseline contained 409 test files. The current worktree contains
411: one characterization-test module from MA0-T04 and the T06 architecture
test. The dead-code candidate sets, legacy-module decisions, application file
count, and production dependency results did not change.

## Verification

Executed on 2026-09-17:

```text
Scheduler ratchet and synthetic detection:
2 passed in 2.18s

Scheduler ratchet plus dependency scanner and existing dependency ratchet:
13 passed in 12.76s

Generated artifact consistency checks:
2 passed in 14.77s

Complete architecture gate:
122 passed, 4 warnings in 181.73s
```

The four warnings are existing Pydantic JSON-schema warnings emitted while
building the composed OpenAPI schema. They are unrelated to dependency scanning
or the Scheduler rules.

## Acceptance Decision

```text
PASS
```

Reason: all five required dependency counts are zero, the gate has no exception
mechanism, synthetic violations prove every rule is enforceable, and the full
architecture suite passes. MA0-T07 has not been started.
