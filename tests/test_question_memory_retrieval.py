from datetime import datetime, timezone

from app.services.question_memory_retrieval import rank_question_memory_entries
from tests.test_question_memory_index_contracts import make_entry


def test_question_memory_ranking_prefers_focus_then_skill_then_recency():
    older_match = make_entry(
        artifact_ref="context-artifact-ref:older",
        focus_tags=["cache_consistency"],
        skill_tags=["idempotency"],
        source_max_sequence_no=3,
    )
    recent_unrelated = make_entry(
        question_id="q2",
        artifact_ref="context-artifact-ref:recent",
        focus_tags=["testing"],
        skill_tags=["testing"],
        source_max_sequence_no=9,
    )

    ranked = rank_question_memory_entries(
        [recent_unrelated, older_match],
        focus_tags={"cache_consistency"},
        skill_tags={"idempotency"},
        unresolved_topic_codes=set(),
    )

    assert ranked[0].artifact_ref == older_match.artifact_ref
