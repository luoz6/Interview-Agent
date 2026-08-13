import json

from app.adapters.knowledge.metadata_unit_resolver import (
    MetadataKnowledgeUnitResolver,
)
from app.application.knowledge.followup_gap_service import FollowupGapService
from app.domain.knowledge.followup_gap import (
    FollowupTargetKind,
    analyze_answer_gap,
    select_followup_brief,
)
from app.domain.knowledge.knowledge_unit import KnowledgeUnit
from app.domain.knowledge.models import KnowledgeChunk


def _unit() -> KnowledgeUnit:
    return KnowledgeUnit(
        knowledge_unit_id="redis-distributed-lock",
        domain="redis",
        topic="distributed-lock",
        aliases=("distributed lock",),
        technical_terms=("expire", "delete"),
        expected_signals=(
            "owner token",
            "atomic compare-and-delete",
            "lease expiry",
            "fencing token",
        ),
        failure_modes=("delete another owner's lock",),
        hard_negatives=("unconditional delete",),
        follow_up_triggers=(
            "probe ownership verification before release",
            "probe atomic release",
        ),
    )


def test_redis_answer_gap_is_deterministic_and_answer_specific():
    unit = _unit()

    first = analyze_answer_gap("设置 expire，业务执行完后 delete。", unit)
    second = analyze_answer_gap("设置 expire，业务执行完后 delete。", unit)

    assert first == second
    assert first is not None
    assert first.mentioned_signals == ("expire", "delete")
    assert first.missing_signals == (
        "owner token",
        "atomic compare-and-delete",
        "lease expiry",
        "fencing token",
    )
    assert first.incorrect_signals == ()
    assert select_followup_brief(first).target_signal == "owner token"


def test_incorrect_signal_is_selected_before_a_missing_signal():
    analysis = analyze_answer_gap(
        "I use an unconditional delete after the work finishes.",
        _unit(),
    )

    brief = select_followup_brief(analysis)

    assert analysis.incorrect_signals == ("unconditional delete",)
    assert brief.target_kind == FollowupTargetKind.INCORRECT
    assert brief.target_signal == "unconditional delete"


def test_empty_answer_produces_no_misleading_gap():
    assert analyze_answer_gap("  \n ", _unit()) is None


def test_discussing_a_failure_mode_is_not_itself_an_incorrect_claim():
    analysis = analyze_answer_gap(
        "The main risk is deleting another owner's lock, so I verify ownership.",
        _unit(),
    )

    assert analysis.incorrect_signals == ()


def test_metadata_resolver_and_context_never_copy_raw_evidence_content():
    chunk = KnowledgeChunk(
        chunk_id="redis-lock",
        title="Redis lock",
        content="PRIVATE FULL EVIDENCE BODY",
        source_type="theory",
        domain="redis",
        tags=["redis"],
        metadata={"knowledge_unit": _unit().model_dump(mode="json")},
    )
    service = FollowupGapService(MetadataKnowledgeUnitResolver())

    context = service.build_context(
        candidate_answer="set expire then delete",
        bound_references=[chunk],
    )

    assert context is not None
    message = context.as_message()
    payload = json.loads(message["content"])
    assert message["role"] == "knowledge_gap"
    assert payload["brief"]["target_signal"] == "owner token"
    assert "PRIVATE FULL EVIDENCE BODY" not in message["content"]


def test_invalid_or_conflicting_metadata_fails_closed():
    resolver = MetadataKnowledgeUnitResolver()
    invalid = {"metadata": {"knowledge_unit": {"domain": "redis"}}}
    other = _unit().model_copy(update={"knowledge_unit_id": "other-unit"})
    conflicting = [
        {"metadata": {"knowledge_unit": _unit().model_dump(mode="json")}},
        {"metadata": {"knowledge_unit": other.model_dump(mode="json")}},
    ]

    assert resolver.resolve([invalid]) is None
    assert resolver.resolve(conflicting) is None
