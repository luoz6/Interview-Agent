from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.knowledge.source_scope import SelectedUserDocumentRevision
from app.domain.knowledge.user_document import (
    USER_DOCUMENT_MAX_BYTES,
    USER_DOCUMENT_SUPPORTED_EXTENSIONS,
    USER_DOCUMENT_SUPPORTED_MEDIA_TYPES,
    USER_MATERIALS_CAPABILITIES,
    USER_MATERIALS_PERSISTENCE_PORTS,
    UserDocumentInternalStage,
    UserDocumentPublicStatus,
)
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanV2,
    PlanConfigurationSnapshot,
    build_interview_knowledge_scope_snapshot,
    plan_payload_sha256,
)


def _plan_payload() -> dict[str, object]:
    return {
        "title": "用户资料契约计划",
        "configuration_snapshot": PlanConfigurationSnapshot(
            difficulty="intermediate",
            target_duration_minutes=15,
            focus_preset="project_review",
            question_type_budget={"project": 1},
            expected_followup_budget=1,
            generator_version="plan-generator-v2-test",
            followup_policy_version="fixed_v1",
        ),
        "questions": (
            InterviewPlanQuestionV2(
                question_id=str(uuid4()),
                position=1,
                question_text="请说明项目架构与关键取舍。",
                focus="项目架构",
                question_type="project",
                difficulty="intermediate",
                expected_minutes=8,
                expected_followups=1,
                origin="generated",
            ),
        ),
    }


def _selected_revision() -> SelectedUserDocumentRevision:
    return SelectedUserDocumentRevision(
        document_id=str(uuid4()),
        document_revision_id=str(uuid4()),
        content_sha256="a" * 64,
        allowed_usages=("feedback", "question", "follow_up"),
    )


def test_user_document_lifecycle_and_ingest_limits_are_frozen():
    assert {status.value for status in UserDocumentPublicStatus} == {
        "processing",
        "ready",
        "failed",
        "disabled",
        "deleting",
    }
    assert {stage.value for stage in UserDocumentInternalStage} == {
        "validation",
        "extraction",
        "chunking",
        "embedding",
        "indexing",
    }
    assert USER_DOCUMENT_MAX_BYTES == 1_048_576
    assert USER_DOCUMENT_SUPPORTED_EXTENSIONS == frozenset({".md", ".txt"})
    assert USER_DOCUMENT_SUPPORTED_MEDIA_TYPES == frozenset(
        {"text/markdown", "text/plain"}
    )


def test_materials_freeze_exactly_two_capabilities_and_two_persistence_ports():
    assert USER_MATERIALS_CAPABILITIES == (
        "USER_MATERIALS_ENABLED",
        "USER_MATERIALS_INGEST_ENABLED",
    )
    assert USER_MATERIALS_PERSISTENCE_PORTS == (
        "UserDocumentStorePort",
        "UserDocumentChunkRepositoryPort",
    )


def test_legacy_plan_without_scope_maps_to_system_knowledge_only():
    plan = InterviewPlanV2.model_validate(_plan_payload())

    assert plan.knowledge_scope.include_system_knowledge is True
    assert plan.knowledge_scope.selected_documents == ()
    assert plan.knowledge_scope.created_at is None
    assert len(plan.knowledge_scope.selection_sha256) == 64
    assert plan_payload_sha256(plan) == plan_payload_sha256(
        InterviewPlanV2.model_validate(plan.model_dump(mode="json"))
    )


def test_selected_revision_is_immutable_canonical_and_part_of_plan_hash():
    selected = _selected_revision()
    assert selected.allowed_usages == ("question", "follow_up", "feedback")
    scope = build_interview_knowledge_scope_snapshot(
        include_system_knowledge=False,
        selected_documents=(selected,),
        created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    baseline = InterviewPlanV2.model_validate(_plan_payload())
    scoped = InterviewPlanV2.model_validate(
        {**_plan_payload(), "knowledge_scope": scope}
    )

    assert scoped.knowledge_scope.selected_documents == (selected,)
    assert plan_payload_sha256(scoped) != plan_payload_sha256(baseline)
    with pytest.raises(ValidationError, match="frozen"):
        scoped.knowledge_scope.include_system_knowledge = True


def test_scope_builder_hashes_the_same_canonical_document_order_it_persists():
    first = SelectedUserDocumentRevision(
        document_id="00000000-0000-0000-0000-000000000001",
        document_revision_id="00000000-0000-0000-0000-000000000011",
        content_sha256="1" * 64,
        allowed_usages=("question",),
    )
    second = SelectedUserDocumentRevision(
        document_id="00000000-0000-0000-0000-000000000002",
        document_revision_id="00000000-0000-0000-0000-000000000012",
        content_sha256="2" * 64,
        allowed_usages=("feedback",),
    )

    scope = build_interview_knowledge_scope_snapshot(
        include_system_knowledge=True,
        selected_documents=(second, first),
        created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    plan = InterviewPlanV2.model_validate(
        {**_plan_payload(), "knowledge_scope": scope}
    )

    assert plan.knowledge_scope.selected_documents == (first, second)


def test_plan_rejects_a_scope_with_a_forged_selection_hash():
    scope = build_interview_knowledge_scope_snapshot(
        include_system_knowledge=True,
        selected_documents=(_selected_revision(),),
        created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    forged = scope.model_copy(update={"selection_sha256": "0" * 64})

    with pytest.raises(ValidationError, match="selection_sha256 does not match"):
        InterviewPlanV2.model_validate(
            {**_plan_payload(), "knowledge_scope": forged}
        )
