from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.interview_plan_editor import (
    InterviewPlanEditor,
    PlanEditRequest,
    PlanOperation,
)
from app.services.interview_plan_knowledge import (
    valid_question_knowledge,
)
from app.services.interview_plan_revision import (
    PlanConfigurationSnapshot,
    PlanSourcePayload,
    legacy_plan_to_v2,
    plan_payload_sha256,
    v2_plan_to_legacy,
)
from app.services.interview_plan_revision_store import (
    InMemoryInterviewPlanRevisionStore,
)
from app.services.knowledge_binding import KnowledgeBindingResolver
from app.services.postgres_plan_revision_store import (
    PostgresInterviewPlanRevisionStore,
)
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    KnowledgeBindingSnapshot,
    KnowledgeEvidenceRef,
    PrepContext,
    PrepQuestionHint,
)


MANIFEST_SHA256 = "a" * 64
EVIDENCE_HASHES = {
    "evidence-redis": "b" * 64,
    "evidence-postgres": "c" * 64,
}


def configuration() -> PlanConfigurationSnapshot:
    return PlanConfigurationSnapshot(
        difficulty="advanced",
        target_duration_minutes=30,
        focus_preset="technical_depth",
        question_type_budget={
            "project": 1,
            "technical": 1,
            "system-design": 1,
        },
        expected_followup_budget=3,
        generator_version="plan-generator-v2",
        followup_policy_version="fixed_v1",
    )


def grounded_legacy_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Grounded plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Explain the reliability project.",
                focus="project evidence",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis consistency tradeoffs.",
                focus="redis evidence",
            ),
            InterviewQuestion(
                id="q3",
                kind="system-design",
                prompt="Design a resilient PostgreSQL service.",
                focus="postgres evidence",
            ),
        ],
        prep_context=PrepContext(
            schema_version="v2",
            summary="Grounded synthetic context",
            knowledge_status="completed",
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    evidence_ids=["evidence-redis"],
                    evidence_titles=["Redis reliability"],
                ),
                PrepQuestionHint(
                    question_id="q2",
                    evidence_ids=["evidence-redis"],
                    evidence_titles=["Redis reliability"],
                ),
                PrepQuestionHint(
                    question_id="q3",
                    evidence_ids=["evidence-postgres"],
                    evidence_titles=["PostgreSQL resilience"],
                ),
            ],
            evidence_refs=[
                KnowledgeEvidenceRef(
                    evidence_id="evidence-redis",
                    title="Redis reliability",
                    domain="redis",
                    source_type="theory",
                    content_sha256=EVIDENCE_HASHES["evidence-redis"],
                    corpus_manifest_sha256=MANIFEST_SHA256,
                    candidate_summary="Synthetic Redis evidence",
                ),
                KnowledgeEvidenceRef(
                    evidence_id="evidence-postgres",
                    title="PostgreSQL resilience",
                    domain="postgresql",
                    source_type="theory",
                    content_sha256=EVIDENCE_HASHES["evidence-postgres"],
                    corpus_manifest_sha256=MANIFEST_SHA256,
                    candidate_summary="Synthetic PostgreSQL evidence",
                ),
            ],
            binding_snapshot=KnowledgeBindingSnapshot(
                prep_run_id="prep-t54",
                corpus_manifest_sha256=MANIFEST_SHA256,
                queries=[],
                status="completed",
            ),
        ),
    )


def setup_editor():
    store = InMemoryInterviewPlanRevisionStore()
    initial = store.create_initial(
        source_payload=PlanSourcePayload(
            job_description="Synthetic backend role",
            resume_text="Private synthetic resume marker",
        ),
        plan=legacy_plan_to_v2(
            grounded_legacy_plan(),
            configuration_snapshot=configuration(),
        ),
        retention_policy="test-v1",
        generator_version="plan-generator-v2",
    )
    return store, InterviewPlanEditor(store), initial


def request(operation, *, revision=1, request_id="t54-request"):
    return PlanEditRequest(
        expected_revision=revision,
        request_id=request_id,
        operations=[operation],
    )


class Repository:
    def get_by_ids(self, evidence_ids, *, expected_hashes):
        return SimpleNamespace(
            found=[
                {
                    "chunk_id": evidence_id,
                    "source_type": "theory",
                    "content": f"Synthetic content for {evidence_id}",
                    "metadata": {
                        "content_sha256": expected_hashes[evidence_id],
                        "corpus_manifest_sha256": MANIFEST_SHA256,
                    },
                }
                for evidence_id in evidence_ids
            ],
            missing=[],
            version_mismatch=[],
        )


def test_generation_builds_uuid_bound_knowledge_and_runtime_resolves_it():
    _, _, initial = setup_editor()
    question_ids = [item.question_id for item in initial.plan.questions]
    hint_ids = [
        item["question_id"]
        for item in initial.plan.prep_context["question_hints"]
    ]

    assert hint_ids == question_ids
    assert all(not question_id.startswith("q") for question_id in question_ids)
    for question in initial.plan.questions:
        binding = question.knowledge_binding
        assert binding["status"] == "valid"
        assert binding["reason_code"] == "grounded_generation"
        assert binding["corpus_manifest_sha256"] == MANIFEST_SHA256

    legacy = v2_plan_to_legacy(initial.plan)
    assert set(legacy.prep_context.question_bindings) == set(question_ids)
    resolution = KnowledgeBindingResolver(Repository()).resolve(
        legacy,
        question_ids[1],
    )
    assert resolution.retrieval_path == "bound_evidence_ids"
    assert resolution.evidence_ids == ["evidence-redis"]


@pytest.mark.parametrize("operation_name,field_name", [
    ("edit_question_text", "question_text"),
    ("edit_focus", "focus"),
])
def test_content_edit_invalidates_binding_and_audit_contains_only_hashes(
    operation_name,
    field_name,
):
    store, editor, initial = setup_editor()
    question = initial.plan.questions[0]
    private_value = "Private edited content that must not enter audit"
    operation = {
        "op": operation_name,
        "question_id": question.question_id,
        field_name: private_value,
    }

    revised = editor.apply(initial.plan_family_id, request(operation))

    changed = revised.plan.questions[0]
    assert changed.question_id == question.question_id
    assert changed.knowledge_binding["status"] == "invalidated"
    assert changed.knowledge_binding["reason_code"] == "question_content_changed"
    assert changed.knowledge_binding["evidence_ids"] == []
    assert revised.audit.source_sha256 == initial.source_sha256
    assert revised.audit.parent_plan_sha256 == initial.plan_sha256
    assert revised.audit.result_plan_sha256 == revised.plan_sha256
    audited = revised.audit.operations[0]
    assert audited.actor == "user"
    assert audited.source_question_id == question.question_id
    assert audited.result_question_id == question.question_id
    assert audited.knowledge_binding_action == "invalidate"
    assert audited.knowledge_binding_status == "invalidated"
    assert audited.knowledge_binding_reason_code == "question_content_changed"
    assert field_name in audited.field_diffs
    serialized = json.dumps(revised.audit.model_dump(mode="json"))
    assert private_value not in serialized
    assert "Private synthetic resume marker" not in serialized
    assert store.get_by_id(revised.plan_revision_id).audit == revised.audit


def test_move_preserves_binding_and_delete_preserves_other_identities():
    store, editor, initial = setup_editor()
    moved_question = initial.plan.questions[0]
    preserved_binding = moved_question.knowledge_binding
    moved = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "move_question",
                "question_id": moved_question.question_id,
                "to_position": 3,
            },
            request_id="t54-move",
        ),
    )
    remaining_before_delete = {
        item.question_id: item.knowledge_binding
        for item in moved.plan.questions
        if item.question_id != moved_question.question_id
    }
    deleted = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "delete_question",
                "question_id": moved_question.question_id,
            },
            revision=2,
            request_id="t54-delete",
        ),
    )

    assert moved.plan.questions[2].question_id == moved_question.question_id
    assert moved.plan.questions[2].knowledge_binding == preserved_binding
    assert moved.audit.operations[0].knowledge_binding_action == "preserve"
    assert [item.position for item in deleted.plan.questions] == [1, 2]
    assert {
        item.question_id: item.knowledge_binding
        for item in deleted.plan.questions
    } == remaining_before_delete
    assert deleted.audit.operations[0].source_question_id == moved_question.question_id
    assert deleted.audit.operations[0].result_question_id is None
    assert deleted.audit.operations[0].knowledge_binding_action == "remove"
    assert [item.question_id for item in store.list_revisions(initial.plan_family_id)[0].plan.questions] == [
        item.question_id for item in initial.plan.questions
    ]


def test_custom_question_is_explicitly_unbound_and_cannot_claim_grounding():
    _, editor, initial = setup_editor()
    fake_binding = valid_question_knowledge(
        evidence_ids=["evidence-redis"],
        evidence_content_sha256={
            "evidence-redis": EVIDENCE_HASHES["evidence-redis"]
        },
        corpus_manifest_sha256=MANIFEST_SHA256,
        reason_code="grounded_generation",
    ).model_dump(mode="json")
    with pytest.raises(ValidationError, match="cannot claim knowledge grounding"):
        PlanOperation(
            op="add_custom_question",
            question_text="Custom question",
            focus="custom focus",
            question_type="behavioral",
            difficulty="advanced",
            expected_minutes=4,
            expected_followups=0,
            knowledge_binding=fake_binding,
        )

    revised = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "add_custom_question",
                "question_text": "Custom question",
                "focus": "custom focus",
                "question_type": "behavioral",
                "difficulty": "advanced",
                "expected_minutes": 4,
                "expected_followups": 0,
            },
            request_id="t54-custom",
        ),
    )
    custom = revised.plan.questions[-1]
    assert custom.origin == "custom"
    assert custom.knowledge_binding["status"] == "unbound"
    assert custom.knowledge_binding["reason_code"] == "custom_question"
    assert revised.audit.operations[0].knowledge_binding_action == "unbound"
    assert revised.audit.operations[0].knowledge_binding_status == "unbound"
    assert revised.audit.operations[0].knowledge_binding_reason_code == "custom_question"
    assert revised.audit.operations[0].source_question_id is None
    assert revised.audit.operations[0].result_question_id == custom.question_id


def test_regeneration_rebuilds_valid_binding_and_records_question_lineage():
    _, editor, initial = setup_editor()
    replaced = initial.plan.questions[1]
    regenerated_binding = valid_question_knowledge(
        evidence_ids=["evidence-postgres"],
        evidence_content_sha256={
            "evidence-postgres": EVIDENCE_HASHES["evidence-postgres"]
        },
        corpus_manifest_sha256=MANIFEST_SHA256,
        reason_code="grounded_generation",
    ).model_dump(mode="json")
    revised = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "regenerate_question",
                "question_id": replaced.question_id,
                "question_text": "Regenerated PostgreSQL question",
                "focus": "postgres resilience",
                "question_type": "technical",
                "difficulty": "advanced",
                "expected_minutes": 8,
                "expected_followups": 1,
                "knowledge_binding": regenerated_binding,
            },
            request_id="t54-regenerate",
        ),
    )

    replacement = revised.plan.questions[1]
    assert replacement.question_id != replaced.question_id
    assert replacement.replaces_question_id == replaced.question_id
    assert replacement.knowledge_binding["status"] == "valid"
    assert replacement.knowledge_binding["reason_code"] == "provider_regenerated"
    audit = revised.audit.operations[0]
    assert audit.actor == "provider"
    assert audit.source_question_id == replaced.question_id
    assert audit.result_question_id == replacement.question_id
    assert audit.knowledge_binding_action == "rebuild"
    assert audit.knowledge_binding_status == "valid"
    assert audit.knowledge_binding_reason_code == "provider_regenerated"


def test_regeneration_hash_mismatch_is_invalidated_instead_of_claiming_grounding():
    _, editor, initial = setup_editor()
    replaced = initial.plan.questions[1]
    mismatched_binding = valid_question_knowledge(
        evidence_ids=["evidence-redis"],
        evidence_content_sha256={"evidence-redis": "d" * 64},
        corpus_manifest_sha256=MANIFEST_SHA256,
        reason_code="grounded_generation",
    ).model_dump(mode="json")
    revised = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "regenerate_question",
                "question_id": replaced.question_id,
                "question_text": "Regenerated mismatched question",
                "focus": "mismatched evidence",
                "question_type": "technical",
                "difficulty": "advanced",
                "expected_minutes": 8,
                "expected_followups": 1,
                "knowledge_binding": mismatched_binding,
            },
            request_id="t54-regenerate-mismatch",
        ),
    )

    binding = revised.plan.questions[1].knowledge_binding
    assert binding["status"] == "invalidated"
    assert binding["reason_code"] == "evidence_hash_mismatch"
    assert binding["evidence_ids"] == []
    assert revised.audit.operations[0].knowledge_binding_status == "invalidated"
    assert revised.audit.operations[0].knowledge_binding_reason_code == (
        "evidence_hash_mismatch"
    )


def test_restore_and_regenerate_all_audits_keep_configuration_diff_empty():
    _, editor, initial = setup_editor()
    changed = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "edit_focus",
                "question_id": initial.plan.questions[0].question_id,
                "focus": "changed focus",
            },
            request_id="t54-before-restore",
        ),
    )
    restored = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "restore_revision",
                "target_revision_id": initial.plan_revision_id,
            },
            revision=2,
            request_id="t54-restore",
        ),
    )
    regenerated_plan = initial.plan.model_copy(update={"title": "Regenerated all"})
    regenerated = editor.apply(
        initial.plan_family_id,
        request(
            {
                "op": "regenerate_all",
                "regenerated_plan": regenerated_plan.model_dump(mode="json"),
            },
            revision=3,
            request_id="t54-regenerate-all",
        ),
    )

    assert changed.configuration_snapshot == restored.configuration_snapshot
    assert restored.plan == initial.plan
    assert restored.audit.configuration_diff == {}
    assert restored.audit.operations[0].target_revision_id == initial.plan_revision_id
    assert restored.audit.operations[0].knowledge_binding_action == "restore"
    assert regenerated.audit.configuration_diff == {}
    assert regenerated.audit.operations[0].actor == "provider"
    assert regenerated.audit.operations[0].knowledge_binding_action == "rebuild_all"
    assert regenerated.plan_sha256 == plan_payload_sha256(regenerated.plan)


@pytest.mark.pg_runtime
def test_postgres_round_trips_hash_only_audit(
    postgres_dsn,
    runtime_table_prefix,
):
    store = PostgresInterviewPlanRevisionStore(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
        schema_mode="migrate",
    )
    initial = store.create_initial(
        source_payload=PlanSourcePayload(
            job_description="Private PostgreSQL role marker",
            resume_text="Private PostgreSQL resume marker",
        ),
        plan=legacy_plan_to_v2(
            grounded_legacy_plan(),
            configuration_snapshot=configuration(),
        ),
        retention_policy="test-v1",
        generator_version="plan-generator-v2",
    )
    edited = InterviewPlanEditor(store).apply(
        initial.plan_family_id,
        request(
            {
                "op": "edit_question_text",
                "question_id": initial.plan.questions[0].question_id,
                "question_text": "Private PostgreSQL edit marker",
            },
            request_id="t54-postgres-audit",
        ),
    )

    reloaded = store.get_by_id(edited.plan_revision_id)
    assert reloaded.audit == edited.audit
    assert reloaded.audit.parent_plan_sha256 == initial.plan_sha256
    serialized = json.dumps(reloaded.audit.model_dump(mode="json"))
    assert "Private PostgreSQL role marker" not in serialized
    assert "Private PostgreSQL resume marker" not in serialized
    assert "Private PostgreSQL edit marker" not in serialized
