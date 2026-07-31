from app.services.context_compression_eligibility import (
    CompressionEligibilityReason,
    ContextCompressionEligibilityPolicy,
)
from app.services.context_selection import ContextSelectionStats


def evaluate(stats, *, target="question_conversation"):
    return ContextCompressionEligibilityPolicy().evaluate(
        selection_stats=stats,
        target_artifact_type=target,
        source_unit_count=2,
        source_manifest_sha256="a" * 64,
    )


def test_short_context_without_loss_is_not_eligible():
    result = evaluate(
        ContextSelectionStats(
            source_message_count=2,
            selected_message_count=2,
        )
    )

    assert result.eligible is False
    assert result.reason is None
    assert result.dropped_count == 0
    assert result.truncated_count == 0


def test_dropped_older_conversation_is_eligible_with_stable_reason():
    result = evaluate(
        ContextSelectionStats(
            source_message_count=6,
            selected_message_count=4,
            dropped_message_count=2,
        )
    )

    assert result.eligible is True
    assert result.reason is CompressionEligibilityReason.OLDER_COMPLETE_TURN_WOULD_DROP
    assert result.policy_version == "context-compression-eligibility-v1"
    assert result.source_manifest_sha256 == "a" * 64


def test_truncated_evidence_is_eligible_with_evidence_reason():
    result = evaluate(
        ContextSelectionStats(
            source_evidence_count=2,
            selected_evidence_count=1,
            dropped_evidence_count=1,
            truncated_evidence_count=1,
        ),
        target="evidence_compression",
    )

    assert result.eligible is True
    assert result.reason is (
        CompressionEligibilityReason.EVIDENCE_REPRESENTATION_EXCESSIVE_TRUNCATION
    )
    assert result.dropped_count == 1
    assert result.truncated_count == 1


def test_missing_selection_stats_fails_safe_to_not_eligible():
    result = evaluate(None)

    assert result.eligible is False
    assert result.reason is None
