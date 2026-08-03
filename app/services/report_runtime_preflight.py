from __future__ import annotations

from dataclasses import dataclass
import os

from app.services.config import (
    derive_pgvector_table_names,
    get_embedding_settings,
    get_pgvector_table,
    get_postgres_dsn,
    get_report_runtime_profile,
    get_runtime_table_prefix,
)
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider


@dataclass(frozen=True)
class ReportRuntimePreflightCheck:
    code: str
    passed: bool
    required: bool = True


@dataclass(frozen=True)
class ReportRuntimePreflightResult:
    profile: str
    ready: bool
    checks: tuple[ReportRuntimePreflightCheck, ...]

    @property
    def failed_codes(self) -> tuple[str, ...]:
        return tuple(
            check.code for check in self.checks if check.required and not check.passed
        )


def run_report_runtime_preflight(
    *,
    check_external: bool = False,
    connect=None,
) -> ReportRuntimePreflightResult:
    profile = get_report_runtime_profile()
    settings = get_embedding_settings()
    checks = [
        ReportRuntimePreflightCheck(
            code="runtime_profile_coherent",
            passed=profile.configuration_valid,
        )
    ]

    if profile.preview:
        checks.extend(
            [
                ReportRuntimePreflightCheck(
                    code="memory_report_jobs_selected",
                    passed=profile.report_job_store == "memory",
                ),
                ReportRuntimePreflightCheck(
                    code="static_knowledge_selected",
                    passed=profile.knowledge_store == "static",
                ),
            ]
        )
    else:
        checks.extend(
            [
                ReportRuntimePreflightCheck(
                    code="embedding_provider_enabled",
                    passed=settings.provider_name != "disabled",
                ),
                ReportRuntimePreflightCheck(
                    code="embedding_credentials_present",
                    passed=bool(os.getenv("SILICONFLOW_API_KEY", "").strip()),
                ),
            ]
        )
        if check_external:
            checks.extend(_postgres_checks(settings.dimension, connect=connect))

    return ReportRuntimePreflightResult(
        profile=profile.name,
        ready=all(check.passed for check in checks if check.required),
        checks=tuple(checks),
    )


def _postgres_checks(expected_dimension: int, *, connect=None):
    provider = DirectPsycopg2ConnectionProvider(
        get_postgres_dsn(),
        connect=connect,
        connect_kwargs={"connect_timeout": 3},
    )
    try:
        with provider.exclusive_connection(autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                postgres_reachable = cursor.fetchone() == (1,)
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                )
                pgvector_available = bool(cursor.fetchone()[0])
                versions_table, releases_table = derive_pgvector_table_names(
                    get_pgvector_table()
                )
                report_jobs_table = f"{get_runtime_table_prefix()}_report_jobs"
                cursor.execute(
                    "SELECT to_regclass(%s), to_regclass(%s), to_regclass(%s)",
                    (report_jobs_table, versions_table, releases_table),
                )
                report_relation, versions_relation, releases_relation = cursor.fetchone()
                active_corpus = False
                dimension_matches = False
                if releases_relation is not None:
                    from psycopg2 import sql

                    cursor.execute(
                        sql.SQL(
                            "SELECT embedding_dimension FROM {releases} "
                            "WHERE status = 'active' LIMIT 1"
                        ).format(releases=sql.Identifier(releases_table))
                    )
                    row = cursor.fetchone()
                    active_corpus = row is not None
                    dimension_matches = bool(
                        row and int(row[0]) == expected_dimension
                    )
        return [
            ReportRuntimePreflightCheck("postgres_reachable", postgres_reachable),
            ReportRuntimePreflightCheck(
                "report_job_schema_available", report_relation is not None
            ),
            ReportRuntimePreflightCheck("pgvector_extension_available", pgvector_available),
            ReportRuntimePreflightCheck(
                "knowledge_schema_available",
                versions_relation is not None and releases_relation is not None,
            ),
            ReportRuntimePreflightCheck("active_corpus_available", active_corpus),
            ReportRuntimePreflightCheck(
                "embedding_dimension_matches_corpus", dimension_matches
            ),
        ]
    except Exception:
        return [
            ReportRuntimePreflightCheck("postgres_reachable", False),
            ReportRuntimePreflightCheck("report_job_schema_available", False),
            ReportRuntimePreflightCheck("pgvector_extension_available", False),
            ReportRuntimePreflightCheck("knowledge_schema_available", False),
            ReportRuntimePreflightCheck("active_corpus_available", False),
            ReportRuntimePreflightCheck(
                "embedding_dimension_matches_corpus", False
            ),
        ]
