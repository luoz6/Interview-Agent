import pytest

from app.services.drafts import AnonymousDraftStore


def test_save_draft_creates_id_timestamps_and_tags():
    store = AnonymousDraftStore()

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


def test_save_draft_updates_existing_id():
    store = AnonymousDraftStore()
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
    store = AnonymousDraftStore()

    with pytest.raises(ValueError, match=message):
        store.save(
            job_description=job_description,
            resume_text=resume_text,
            job_tags=["python"],
        )


def test_get_missing_draft_raises_value_error():
    store = AnonymousDraftStore()

    with pytest.raises(ValueError, match="draft not found"):
        store.get("missing")


def test_clear_removes_all_drafts():
    store = AnonymousDraftStore()
    draft = store.save(
        job_description="Backend role using Python.",
        resume_text="Built APIs.",
        job_tags=["python"],
    )

    store.clear()

    with pytest.raises(ValueError, match="draft not found"):
        store.get(draft["draft_id"])
