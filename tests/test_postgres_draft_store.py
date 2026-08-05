from datetime import datetime

import pytest

from app.services.postgres_draft_store import PostgresDraftStore
from app.services.postgres_prep_plan_store import PostgresPrepPlanStore
from app.services.prep_plans import PrepPlanError
from tests.postgres_support import drop_runtime_tables
from tests.test_interview_launch import sample_plan


pytestmark = pytest.mark.pg_runtime


def test_postgres_draft_survives_store_restart_and_preserves_expiry(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        first_store = PostgresDraftStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        saved = first_store.save(
            job_description="Backend role",
            resume_text="Built a resilient API",
            job_tags=["backend", "python"],
            title="Backend draft",
        )

        restarted_store = PostgresDraftStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="validate",
        )
        restored = restarted_store.get(saved["draft_id"])
        assert restored["job_description"] == "Backend role"
        assert restored["resume_text"] == "Built a resilient API"
        assert restored["job_tags"] == ["backend", "python"]
        assert restored["durability"] == "postgres"

        original_expiry = datetime.fromisoformat(restored["expires_at"])
        updated = restarted_store.save(
            draft_id=saved["draft_id"],
            job_description="Updated backend role",
            resume_text="Built and operated a resilient API",
            job_tags=["backend"],
        )
        assert datetime.fromisoformat(updated["expires_at"]) == original_expiry

        assert restarted_store.delete(saved["draft_id"]) is True
        with pytest.raises(ValueError, match="draft not found"):
            restarted_store.get(saved["draft_id"])
    finally:
        drop_runtime_tables(postgres_dsn, prefix)


def test_postgres_draft_delete_cascades_only_unconsumed_plan_content(
    postgres_dsn,
    runtime_table_prefix,
):
    prefix = runtime_table_prefix
    try:
        drafts = PostgresDraftStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        plans = PostgresPrepPlanStore(
            dsn=postgres_dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        draft = drafts.save(
            job_description="Backend role",
            resume_text="Built APIs",
        )
        plan = plans.create(
            plan=sample_plan(),
            job_description="Backend role",
            resume_text="Built APIs",
            job_tags=["backend"],
            source_draft_id=draft["draft_id"],
        )

        assert drafts.delete(draft["draft_id"]) is True
        with pytest.raises(PrepPlanError) as removed:
            plans.get(plan["plan_id"])
        assert removed.value.code == "PREP_PLAN_NOT_FOUND"
    finally:
        drop_runtime_tables(postgres_dsn, prefix)
