import os

import pytest

from scripts import memory_shadow_staging_preflight as staging
from tests.memory_shadow_staging_fixtures import (
    staging_declaration,
    staging_rc_evidence,
)


@pytest.mark.pg_runtime
def test_live_staging_preflight_migrates_metrics_and_cleans(postgres_dsn):
    prefix = staging.make_staging_prefix()

    result = staging.run_live_preflight(
        declaration=staging_declaration(),
        rc_evidence=staging_rc_evidence(),
        environ=os.environ,
        dsn=postgres_dsn,
        table_prefix=prefix,
    )

    assert result["passed"] is True
    assert result["migration_validated"] is True
    assert result["durable_metrics_validated"] is True
    assert result["rollback_verified"] is True
    assert result["cleanup_residue"] == 0
