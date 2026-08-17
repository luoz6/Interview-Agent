from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "OwnedPostgresScope": ("app.adapters.postgres.owned_scope", "OwnedPostgresScope"),
    "PostgresRuntimeMigrationAdapter": (
        "app.adapters.postgres.migration_harness",
        "PostgresRuntimeMigrationAdapter",
    ),
    "PostgresMessageRepository": (
        "app.adapters.postgres.message_repository",
        "PostgresMessageRepository",
    ),
    "PostgresQuestionEvaluationRepository": (
        "app.adapters.postgres.question_evaluation_repository",
        "PostgresQuestionEvaluationRepository",
    ),
    "PostgresReportRepository": (
        "app.adapters.postgres.report_repository",
        "PostgresReportRepository",
    ),
    "PostgresRuntimeOutboxRepository": (
        "app.adapters.postgres.runtime_outbox_repository",
        "PostgresRuntimeOutboxRepository",
    ),
    "PostgresRuntimeReceiptRepository": (
        "app.adapters.postgres.runtime_receipt_repository",
        "PostgresRuntimeReceiptRepository",
    ),
    "PostgresSessionRepository": (
        "app.adapters.postgres.session_repository",
        "PostgresSessionRepository",
    ),
    "PostgresUnitOfWork": (
        "app.adapters.postgres.unit_of_work",
        "PostgresUnitOfWork",
    ),
    "Psycopg2OwnedScopeBackend": (
        "app.adapters.postgres.owned_scope",
        "Psycopg2OwnedScopeBackend",
    ),
    "RuntimeMigrationHarness": (
        "app.adapters.postgres.migration_harness",
        "RuntimeMigrationHarness",
    ),
    "PostgresUserDocumentStore": (
        "app.adapters.postgres.user_documents",
        "PostgresUserDocumentStore",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
