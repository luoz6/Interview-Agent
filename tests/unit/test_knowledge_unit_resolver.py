from app.adapters.knowledge.pilot_unit_resolver import (
    PilotKnowledgeUnitResolver,
    default_knowledge_unit_resolver,
)
from app.application.knowledge.followup_gap_service import FollowupGapService
from app.domain.knowledge.models import KnowledgeChunk


def _chunk(chunk_id: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title="Redis 分布式锁的安全边界",
        content="authoritative evidence body",
        source_type="theory",
        domain="redis",
        tags=["redis", "分布式锁"],
        metadata={},
    )


def test_pilot_resolver_reuses_authoritative_existing_chunk_identity():
    unit = PilotKnowledgeUnitResolver().resolve([_chunk("redis_distributed_lock")])

    assert unit.knowledge_unit_id == "redis-distributed-lock"
    assert unit.source_references == ("redis_distributed_lock",)
    assert unit.review_status == "reviewed"


def test_unpiloted_evidence_preserves_current_behavior():
    assert PilotKnowledgeUnitResolver().resolve([_chunk("redis_consistency")]) is None


def test_pilot_resolver_covers_the_three_plan_pilot_topics():
    resolver = PilotKnowledgeUnitResolver()

    mysql = resolver.resolve([_chunk("mysql_indexing")])
    rocketmq = resolver.resolve([_chunk("rocketmq_delivery")])

    assert mysql.knowledge_unit_id == "mysql-indexing"
    assert mysql.domain == "mysql"
    assert mysql.review_status == "reviewed"
    assert "write amplification" in mysql.expert_signals
    assert rocketmq.knowledge_unit_id == "rocketmq-delivery"
    assert rocketmq.domain == "rocketmq"
    assert rocketmq.review_status == "reviewed"
    assert "transaction message checkback" in rocketmq.expert_signals


def test_default_resolver_enables_real_redis_vertical_slice_without_copying_body():
    context = FollowupGapService(default_knowledge_unit_resolver()).build_context(
        candidate_answer="I set expire and call delete when work completes.",
        bound_references=[_chunk("redis_distributed_lock")],
    )

    assert context is not None
    assert context.brief.target_signal == "owner token"
    assert "authoritative evidence body" not in context.as_message()["content"]
