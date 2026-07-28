from __future__ import annotations

import pytest

from tests.postgres_support import (
    make_runtime_table_prefix,
    require_postgres_dsn,
)


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


@pytest.fixture
def runtime_table_prefix(request) -> str:
    return make_runtime_table_prefix(request.node.name[:36])
