"""PostgreSQL integration coverage."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.runtime.config.memory import load_effective_memory_config
from app.adapters.postgres.principal_memory import PostgresPrincipalMemoryFactStore
from app.services.postgres_principal_memory_consent import (
    PostgresPrincipalMemoryConsentStore,
)
from app.services.postgres_principal_memory_control import (
    PostgresPrincipalMemoryControlStore,
)
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import (
    PrincipalMemoryConsent,
    PrincipalMemoryConsentService,
)
from app.services.principal_memory_consume import PrincipalMemoryLocalConsumeService
from app.services.principal_memory_control import PrincipalMemoryControlService
from app.domain.memory.contracts import canonical_principal_fact
from app.services.principal_memory_lifecycle import PrincipalMemoryLifecycleService
from tests.postgres_support import assert_safe_test_prefix


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


class Sessions:
    def get(self, session_id):
        return {"session_id": session_id, "deletion_status": "active"}


class WordEstimator:
    def estimate_text(self, text, *, model):
        del model
        return len(text.replace("\n", " ").split())


def config():
    return load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "local_consume",
            "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
            "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true",
        }
    )


def consumer(*, facts, consents, controls, resolver, settings):
    control_service = PrincipalMemoryControlService(
        identity_resolver=resolver,
        store=controls,
        clock=lambda: NOW,
    )
    consent_service = PrincipalMemoryConsentService(
        identity_resolver=resolver,
        store=consents,
        policy_version=settings.long_term.consent_policy_version,
        control_service=control_service,
    )
    return (
        PrincipalMemoryLocalConsumeService(
            fact_store=facts,
            consent_service=consent_service,
            identity_resolver=resolver,
            session_store=Sessions(),
            config=settings,
            estimator=WordEstimator(),
            model="synthetic-model",
        ),
        consent_service,
        control_service,
    )


@pytest.mark.pg_runtime
def test_postgres_local_consume_survives_restart_and_rechecks_durable_disable(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    assert_safe_test_prefix(prefix)
    settings = config()
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="trusted_local",
        clock=lambda: NOW,
    )
    facts = PostgresPrincipalMemoryFactStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    consents = PostgresPrincipalMemoryConsentStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    controls = PostgresPrincipalMemoryControlStore(
        dsn=postgres_dsn,
        table_prefix=prefix,
        schema_mode="migrate",
    )
    try:
        consents.grant(
            PrincipalMemoryConsent(
                deployment_id="single-tenant-local",
                principal_id="local-owner",
                policy_version=settings.long_term.consent_policy_version,
                allowed_purposes=["fact_storage", "local_consume"],
                granted_at=NOW,
            )
        )
        first_consumer, consent_service, _ = consumer(
            facts=facts,
            consents=consents,
            controls=controls,
            resolver=resolver,
            settings=settings,
        )
        lifecycle = PrincipalMemoryLifecycleService(
            identity_resolver=resolver,
            consent_service=consent_service,
            fact_store=facts,
            session_store=Sessions(),
            config=settings,
            clock=lambda: NOW,
        )
        lifecycle.declare(
            fact_type="declared_preference",
            normalized_fact=canonical_principal_fact(
                {"interview_language": "mixed"}
            ),
        )
        base = [
            {"role": "interviewer", "content": "Why?"},
            {"role": "candidate", "content": "For isolation."},
        ]
        prepared = first_consumer.prepare(
            provider_context=base,
            current_tags=set(),
            role_tags=set(),
            now=NOW,
            session_id="session-postgres-local",
        )

        restarted_facts = PostgresPrincipalMemoryFactStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode="validate"
        )
        restarted_consents = PostgresPrincipalMemoryConsentStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode="validate"
        )
        restarted_controls = PostgresPrincipalMemoryControlStore(
            dsn=postgres_dsn, table_prefix=prefix, schema_mode="validate"
        )
        restarted, _, restarted_control = consumer(
            facts=restarted_facts,
            consents=restarted_consents,
            controls=restarted_controls,
            resolver=resolver,
            settings=settings,
        )
        consumed = restarted.finalize(prepared, now=NOW)
        assert consumed.outcome == "consumed"
        assert consumed.selected_count == 1

        second = restarted.prepare(
            provider_context=base,
            current_tags=set(),
            role_tags=set(),
            now=NOW,
            session_id="session-postgres-local",
        )
        restarted_control.set_global_enabled(False)
        suppressed = restarted.finalize(second, now=NOW)
        assert suppressed.provider_context == base
        assert suppressed.outcome != "consumed"
    finally:
        import psycopg2
        from psycopg2 import sql

        with psycopg2.connect(postgres_dsn) as connection:
            with connection.cursor() as cursor:
                for table in (
                    facts.effects_table,
                    facts.table,
                    consents.table,
                    controls.table,
                ):
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                            table=sql.Identifier(table)
                        )
                    )
