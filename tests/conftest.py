from __future__ import annotations

from contextlib import ExitStack
import os

import pytest

from app.adapters.postgres.owned_scope import (
    OwnedPostgresScope,
    Psycopg2OwnedScopeBackend,
)
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from tests.postgres_support import (
    make_runtime_table_prefix,
    require_postgres_dsn,
    track_runtime_table_prefixes,
)
from scripts.postgres_acceptance_support import load_postgres_scope_approval


POSTGRES_RUNTIME_MARKERS = (
    "pg_runtime",
    "pg_jobs",
    "pg_control",
    "langgraph_recovery",
    "langgraph_review_recovery",
    "langgraph_dual_canary",
    "langgraph_fencing_canary",
)


def pytest_runtest_setup(item) -> None:
    if any(item.get_closest_marker(name) for name in POSTGRES_RUNTIME_MARKERS):
        require_postgres_dsn()


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    return require_postgres_dsn()


@pytest.fixture(autouse=True)
def cleanup_registered_postgres_prefixes():
    configured_dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not configured_dsn:
        with track_runtime_table_prefixes():
            yield
        return

    required_environment = {
        "approval_id": os.getenv("POSTGRES_TEST_APPROVAL_ID", "").strip(),
        "approval_receipt_sha256": os.getenv(
            "POSTGRES_TEST_APPROVAL_RECEIPT_SHA256", ""
        ).strip(),
        "approved_target_fingerprint": os.getenv(
            "POSTGRES_TEST_APPROVED_FINGERPRINT", ""
        ).strip(),
        "database_allowlist": os.getenv(
            "POSTGRES_TEST_DATABASE_ALLOWLIST", ""
        ).strip(),
        "expires_at": os.getenv("POSTGRES_TEST_APPROVAL_EXPIRES_AT", "").strip(),
    }
    scope = None
    leases = []
    with ExitStack() as stack:
        def open_scope(prefix: str) -> None:
            nonlocal scope
            missing = sorted(
                key for key, value in required_environment.items() if not value
            )
            if missing:
                pytest.fail(
                    "configured PostgreSQL tests require external scope approval: "
                    + ", ".join(missing),
                    pytrace=False,
                )
            if scope is None:
                dsn = require_postgres_dsn()
                backend = Psycopg2OwnedScopeBackend(
                    DirectPsycopg2ConnectionProvider(
                        dsn,
                        connect_kwargs={"connect_timeout": 3},
                    )
                )
                scope = OwnedPostgresScope(backend)
            approval_environment = {
                "POSTGRES_TEST_APPROVAL_ID": required_environment["approval_id"],
                "POSTGRES_TEST_APPROVAL_RECEIPT_SHA256": required_environment[
                    "approval_receipt_sha256"
                ],
                "POSTGRES_TEST_APPROVED_FINGERPRINT": required_environment[
                    "approved_target_fingerprint"
                ],
                "POSTGRES_TEST_DATABASE_ALLOWLIST": required_environment[
                    "database_allowlist"
                ],
                "POSTGRES_TEST_APPROVAL_EXPIRES_AT": required_environment[
                    "expires_at"
                ],
            }
            try:
                approval = load_postgres_scope_approval(
                    approval_environment,
                    scope_prefix=prefix,
                    namespace="POSTGRES_TEST",
                )
            except ValueError as exc:
                pytest.fail(str(exc), pytrace=False)
            leases.append(stack.enter_context(scope.open(approval)))

        with track_runtime_table_prefixes(scope_opener=open_scope):
            yield

    for lease in leases:
        receipt = lease.cleanup_receipt
        if receipt is None or receipt.residue_count != 0:
            pytest.fail(
                f"PostgreSQL owned-scope cleanup was not proven for {lease.scope_prefix}",
                pytrace=False,
            )


@pytest.fixture
def runtime_table_prefix(request) -> str:
    return make_runtime_table_prefix(request.node.name[:36])
