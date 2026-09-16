# Services Migration Map

## Method

- Input facts: P0A-T02 `dependency-edges.json` and file line counts.
- Classification: deterministic filename/static-signal rules, with explicit UNKNOWN reasons.
- Suggested target and phase are initial signals, not final migration decisions.

## Classification Counts

| classification | modules |
| --- | ---: |
| DOMAIN_RULE | 0 |
| APPLICATION_USE_CASE | 0 |
| PERSISTENCE | 0 |
| PROVIDER | 0 |
| RUNTIME | 0 |
| WORKFLOW | 0 |
| EVAL | 0 |
| DIAGNOSTIC | 0 |
| COMPATIBILITY | 0 |
| LEGACY | 0 |
| UNKNOWN | 0 |

## Suggested Target Counts

| suggested_target | modules |
| --- | ---: |

## Migration Phase Counts

| migration_phase | modules |
| --- | ---: |

## High-Risk Modules

| module | loc | imports_count | imported_by_count | suggested_target | phase |
| --- | ---: | ---: | ---: | --- | --- |

## Top Imported Modules

| module | imported_by_count | imports_count | loc | classification |
| --- | ---: | ---: | ---: | --- |

## Zero Incoming App Imports

These modules are not imported by any other `app/**/*.py` file in the static scan. They are marked `dead_code_candidate=needs_review`, not proven dead code.

| module | imports_count | loc | classification | suggested_target |
| --- | ---: | ---: | --- | --- |

## Limitations

- `imported_by_count` only counts static `app.* -> app.services.*` internal imports; runtime string imports, Celery task names, scripts, and tests are excluded.
- Classification is an inventory signal, not a completed domain responsibility audit.
- No services file was deleted or changed.
