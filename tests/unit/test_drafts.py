import pytest
from datetime import datetime, timedelta, timezone

from app.domain.interview.drafts import DraftWriteConflict
from app.services.in_memory_draft_store import InMemoryDraftStore
from app.services.interview_plan_revision import PlanSourcePayload, source_payload_sha256


def test_save_draft_creates_id_timestamps_and_tags():
    store = InMemoryDraftStore()

    draft = store.save(
        job_description="Backend role using Python and Redis.",
        resume_text="Built Redis APIs.",
        job_tags=["python", "redis"],
        title="Backend prep",
    )

    assert draft["draft_id"].startswith("draft_")
    assert draft["job_description"] == "Backend role using Python and Redis."
    assert draft["resume_text"] == "Built Redis APIs."
    assert draft["job_tags"] == ["python", "redis"]
    assert draft["title"] == "Backend prep"
    assert draft["created_at"]
    assert draft["updated_at"] == draft["created_at"]
    assert draft["durability"] == "memory"
    assert draft["expires_at"]
    assert len(draft["draft_id"].removeprefix("draft_")) == 36
    assert draft["plan_status"] == "no_plan"
    assert draft["plan_family_id"] is None
    assert draft["latest_plan_revision_id"] is None
    assert draft["draft_version"] == 1


def test_save_draft_updates_existing_id():
    store = InMemoryDraftStore()
    created = store.save(
        job_description="Backend role using Python.",
        resume_text="Built APIs.",
        job_tags=["python"],
        title="Initial",
    )

    updated = store.save(
        draft_id=created["draft_id"],
        job_description="Backend role using Python and FastAPI.",
        resume_text="Built FastAPI APIs.",
        job_tags=["python", "fastapi"],
        title="Updated",
    )

    assert updated["draft_id"] == created["draft_id"]
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]
    assert updated["job_tags"] == ["python", "fastapi"]
    assert store.get(created["draft_id"])["title"] == "Updated"
    assert updated["draft_version"] == 2


@pytest.mark.parametrize(
    ("job_description", "resume_text", "message"),
    [
        ("", "Built APIs.", "job_description is required"),
        ("   ", "Built APIs.", "job_description is required"),
        ("Backend role using Python.", "", "resume_text is required"),
        ("Backend role using Python.", "   ", "resume_text is required"),
    ],
)
def test_save_draft_rejects_blank_required_text(job_description, resume_text, message):
    store = InMemoryDraftStore()

    with pytest.raises(ValueError, match=message):
        store.save(
            job_description=job_description,
            resume_text=resume_text,
            job_tags=["python"],
        )


def test_get_missing_draft_raises_value_error():
    store = InMemoryDraftStore()

    with pytest.raises(ValueError, match="draft not found"):
        store.get("missing")


def test_draft_preserves_revision_and_marks_it_stale_after_source_edit():
    store = InMemoryDraftStore()
    job_description = "Backend role using Python."
    resume_text = "Built APIs."
    tags = ["python"]
    source_sha256 = source_payload_sha256(
        PlanSourcePayload(
            job_description=job_description,
            resume_text=resume_text,
            job_tags=tags,
        )
    )
    created = store.save(
        job_description=job_description,
        resume_text=resume_text,
        job_tags=tags,
        plan_family_id="family-1",
        latest_plan_revision_id="revision-1",
        plan_source_sha256=source_sha256,
    )
    refreshed = store.get(created["draft_id"])
    edited = store.save(
        draft_id=created["draft_id"],
        job_description="Backend role using Python and PostgreSQL.",
        resume_text=resume_text,
        job_tags=["python", "postgresql"],
    )

    assert created["plan_status"] == refreshed["plan_status"] == "active"
    assert refreshed["latest_plan_revision_id"] == "revision-1"
    assert edited["plan_status"] == "stale"
    assert edited["plan_family_id"] == "family-1"
    assert edited["latest_plan_revision_id"] == "revision-1"


def test_clear_removes_all_drafts():
    store = InMemoryDraftStore()
    draft = store.save(
        job_description="Backend role using Python.",
        resume_text="Built APIs.",
        job_tags=["python"],
    )

    store.clear()

    with pytest.raises(ValueError, match="draft not found"):
        store.get(draft["draft_id"])


def test_delete_and_fixed_expiry_make_draft_unavailable():
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    clock = {"value": now}
    store = InMemoryDraftStore(
        ttl=timedelta(hours=1),
        clock=lambda: clock["value"],
    )
    draft = store.save(
        job_description="Backend role",
        resume_text="Built APIs",
    )
    updated = store.save(
        draft_id=draft["draft_id"],
        job_description="Backend role updated",
        resume_text="Built APIs",
    )
    assert updated["expires_at"] == draft["expires_at"]
    clock["value"] = now + timedelta(hours=1, seconds=1)
    with pytest.raises(ValueError, match="draft not found"):
        store.get(draft["draft_id"])

    second = store.save(job_description="Backend role", resume_text="Built APIs")
    assert store.delete(second["draft_id"]) is True
    assert store.delete(second["draft_id"]) is False


def test_two_candidates_from_same_version_allow_only_one_commit():
    store = InMemoryDraftStore()
    created = store.save(job_description="Backend role", resume_text="Built APIs")
    first = store.prepare_save(
        draft_id=created["draft_id"],
        job_description="First edit",
        resume_text="Built APIs",
    )
    second = store.prepare_save(
        draft_id=created["draft_id"],
        job_description="Second edit",
        resume_text="Built APIs",
    )

    committed = store.commit_save(first)

    assert committed["draft_version"] == 2
    with pytest.raises(DraftWriteConflict):
        store.commit_save(second)


def test_delete_recreate_rejects_prepared_candidate_from_old_epoch():
    store = InMemoryDraftStore()
    created = store.save(job_description="Backend role", resume_text="Built APIs")
    stale = store.prepare_save(
        draft_id=created["draft_id"],
        job_description="Stale edit",
        resume_text="Built APIs",
    )
    assert store.delete(created["draft_id"]) is True
    recreated = store.save(
        draft_id=created["draft_id"],
        job_description="Recreated role",
        resume_text="Built APIs",
    )

    assert recreated["draft_version"] > created["draft_version"]
    with pytest.raises(DraftWriteConflict):
        store.commit_save(stale)


def test_title_only_edit_keeps_bound_plan_active_and_clear_plan_removes_binding():
    store = InMemoryDraftStore()
    job_description = "Backend role"
    resume_text = "Built APIs"
    tags = ["backend"]
    digest = source_payload_sha256(
        PlanSourcePayload(
            job_description=job_description,
            resume_text=resume_text,
            job_tags=tags,
        )
    )
    created = store.save(
        job_description=job_description,
        resume_text=resume_text,
        job_tags=tags,
        plan_family_id="family-1",
        latest_plan_revision_id="revision-1",
        plan_source_sha256=digest,
    )
    titled = store.save(
        draft_id=created["draft_id"],
        job_description=job_description,
        resume_text=resume_text,
        job_tags=tags,
        title="Renamed",
    )
    cleared = store.save(
        draft_id=created["draft_id"],
        job_description=job_description,
        resume_text=resume_text,
        job_tags=tags,
        clear_plan=True,
    )

    assert titled["plan_status"] == "active"
    assert cleared["plan_status"] == "no_plan"
    assert cleared["plan_family_id"] is None
    assert cleared["latest_plan_revision_id"] is None
    assert cleared["plan_source_sha256"] is None
