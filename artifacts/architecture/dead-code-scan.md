# P9 Dead Code Scan

Status: P9-T02 COMPLETE - evidence scan only; no deletion performed

## Evidence Model

- Static: references from `app/**/*.py` and `scripts/**/*.py`.
- Runtime wiring: the subset originating in main, Runtime, API, Agents, Graphs, or A2A modules, including framework decorators and qualified strings.
- Tests: references from `tests/**/*.py`.
- A deletion-review candidate must be zero in all three columns.
- Package initializers and explicit external entrypoints are excluded.

## Summary

- App files scanned: 521
- Script files scanned: 48
- Test files scanned: 474
- Top-level symbols scanned: 2948
- Parse errors: 0
- Dynamic import sites reviewed: 7
- Unresolved dynamic import calls: 2
- Triple-zero modules: 3
- Triple-zero symbols: 31 (11 private)
- Additional symbols covered by triple-zero modules: 12
- Test-only modules: 11
- Test-only symbols: 50

## Reviewed Disposition

| category | count | disposition | finding |
| --- | ---: | --- | --- |
| triple_zero_modules | 3 | `dedicated_cleanup_candidate` | The module candidates are legacy T63/T65 evaluation implementations. Their former one-off scripts/tests are absent and no current Python consumer or runtime wiring remains. |
| private_symbols | 11 | `higher_confidence_cleanup_candidate` | Private triple-zero helpers have no detected production, runtime, or test reachability; remove only in a dedicated change with regression tests. |
| public_symbols | 20 | `external_contract_review_required` | Public triple-zero symbols may still have external import consumers; zero repository reachability alone is insufficient for deletion. |
| test_only | 61 | `retain` | Test-only modules and symbols have explicit test reachability and do not satisfy the triple-zero rule. |
| unresolved_dynamic_imports | 2 | `reviewed_no_candidate_target` | Unresolved calls are test helpers whose call sites supply literal Runtime module names; exact string scanning records those targets separately. |

## Triple-Zero Module Candidates

| module | static | runtime | tests | decision |
| --- | ---: | ---: | ---: | --- |
| `app.evals.t63_performance` | 0 | 0 | 0 | `triple_zero_deletion_review_candidate` |
| `app.evals.t65_builtin_production_executor` | 0 | 0 | 0 | `triple_zero_deletion_review_candidate` |
| `app.evals.t65_runtime_performance` | 0 | 0 | 0 | `triple_zero_deletion_review_candidate` |

## Triple-Zero Symbol Candidates

| symbol | kind | visibility | static | runtime | tests | confidence |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `app.a2a.observability:_utc_now_iso` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.a2a.official_cards:official_card_for` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.adapters.memory.session_store:_public_session_plan_snapshot` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.adapters.providers.t65_provider_http_transport:finalize_t65_provider_attempt_ledger` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.api.reports.routes:_report_job_id_for_session` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.application.report.evaluator:_average_dimension_scores` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.application.report.evaluator:_average_score` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.domain.context.failure_containment:failure_state_metric_dimensions` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.domain.context.selection:_fit_messages_to_remaining` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.domain.context.source_identity:build_conversation_source_identity` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.domain.context.source_identity:build_evidence_source_identity` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.domain.interview.scheduling.decisions:_require_step_budget` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.domain.report.scoring:_dimension_applies` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.domain.report.scoring:resolve_report_scoring_rubric` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.domain.report.view:_identity_payload` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.evals.report_calibration_dataset:canonical_calibration_sha256` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.evals.t65_formal_execution_receipt:build_formal_executor_manifest_receipt` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.evals.t65_formal_execution_receipt:resolve_t65_execution_route` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.evals.t65_formal_execution_receipt:validate_t65_formal_receipt_structure` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.evals.t65_production_capture:T65IncrementalSSEParser` | class | public | 0 | 0 | 0 | `review_public_contract` |
| `app.evals.t65_production_capture:build_t65_cleanup_target_plan` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.evals.t65_production_capture:build_t65_executor_code_manifest` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.evals.t65_production_capture:get_t65_controlled_http_clients` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.evals.t65_provider_evidence:build_performance_observability` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.ports.knowledge:EvidenceLookupPort` | class | public | 0 | 0 | 0 | `review_public_contract` |
| `app.ports.postgres_migrations:PostgresMigrationHarnessPort` | class | public | 0 | 0 | 0 | `review_public_contract` |
| `app.ports.runtime:ReportWorker` | class | public | 0 | 0 | 0 | `review_public_contract` |
| `app.runtime.config.compatibility:_strict_bool` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.runtime.config.loader:_percent` | function | private | 0 | 0 | 0 | `higher_private_symbol` |
| `app.runtime.context_runtime:build_budget_shadow_observation` | function | public | 0 | 0 | 0 | `review_public_contract` |
| `app.runtime.interview_prep:enforce_generated_intent_plan` | function | public | 0 | 0 | 0 | `review_public_contract` |

## Test-Only Modules

These are retained: test reachability means they do not satisfy the deletion rule.

| module | static | runtime | tests | decision |
| --- | ---: | ---: | ---: | --- |
| `app.a2a.contracts.common` | 0 | 0 | 1 | `test_only` |
| `app.a2a.official_cards` | 0 | 0 | 1 | `test_only` |
| `app.application.interview.plan_generation_policy` | 0 | 0 | 1 | `test_only` |
| `app.application.report.evaluator` | 0 | 0 | 5 | `test_only` |
| `app.evals.report_calibration_runner` | 0 | 0 | 1 | `test_only` |
| `app.evals.t65_production_capture` | 0 | 0 | 1 | `test_only` |
| `app.ports.principal_identity` | 0 | 0 | 2 | `test_only` |
| `app.ports.principal_memory` | 0 | 0 | 3 | `test_only` |
| `app.ports.principal_memory_consent` | 0 | 0 | 1 | `test_only` |
| `app.ports.principal_memory_control` | 0 | 0 | 1 | `test_only` |
| `app.ports.question_memory` | 0 | 0 | 1 | `test_only` |

## Review Boundary

Triple-zero means eligible for deletion review, not proven safe to delete. Reflection, external consumers, serialized import paths, and public API compatibility still require human or integration evidence. P9-T03 has the stricter version-removal gate.
