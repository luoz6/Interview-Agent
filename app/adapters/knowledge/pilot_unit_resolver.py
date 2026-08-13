from __future__ import annotations

from typing import Any

from app.domain.knowledge.knowledge_unit import (
    EvaluationLevel,
    KnowledgeReviewStatus,
    KnowledgeUnit,
)


REDIS_DISTRIBUTED_LOCK_UNIT = KnowledgeUnit(
    knowledge_unit_id="redis-distributed-lock",
    domain="redis",
    topic="distributed-lock",
    aliases=("Redis 锁", "distributed lock"),
    technical_terms=("expire", "delete", "Lua", "owner token"),
    expected_signals=(
        "owner token",
        "atomic compare-and-delete",
        "lease expiry",
    ),
    failure_modes=(
        "old owner deletes a newly acquired lock",
        "work continues after lease expiry",
    ),
    hard_negatives=("unconditional delete",),
    weak_answer_signals=("expire", "delete"),
    expert_signals=("fencing token",),
    follow_up_triggers=(
        "probe ownership verification before release",
        "probe atomic compare-and-delete",
        "probe work that outlives the lease",
        "probe fencing token for stale owners",
    ),
    evaluation_levels=(
        EvaluationLevel(
            level="advanced",
            required_signals=(
                "owner token",
                "atomic compare-and-delete",
                "fencing token",
            ),
        ),
    ),
    source_references=("redis_distributed_lock",),
    review_status=KnowledgeReviewStatus.REVIEWED,
)

MYSQL_INDEXING_UNIT = KnowledgeUnit(
    knowledge_unit_id="mysql-indexing",
    domain="mysql",
    topic="indexing",
    aliases=("leftmost prefix", "covering index"),
    technical_terms=("B+Tree", "leftmost-prefix", "covering-index", "EXPLAIN"),
    expected_signals=(
        "leftmost prefix",
        "covering index",
        "query plan validation",
    ),
    failure_modes=(
        "range predicate prevents later columns from narrowing the scan",
        "wide covering index increases write and storage cost",
    ),
    hard_negatives=("always put the highest-cardinality column first",),
    weak_answer_signals=("add an index", "use B+Tree"),
    expert_signals=("data distribution drift", "write amplification"),
    follow_up_triggers=(
        "probe query predicate and ordering shape",
        "probe covering-index tradeoffs",
        "probe EXPLAIN and production validation",
    ),
    evaluation_levels=(
        EvaluationLevel(
            level="advanced",
            required_signals=(
                "leftmost prefix",
                "covering index",
                "query plan validation",
                "write amplification",
            ),
        ),
    ),
    source_references=("mysql_indexing",),
    review_status=KnowledgeReviewStatus.REVIEWED,
)

ROCKETMQ_DELIVERY_UNIT = KnowledgeUnit(
    knowledge_unit_id="rocketmq-delivery",
    domain="rocketmq",
    topic="delivery-semantics",
    aliases=("at least once", "consumer retry", "消费确认"),
    technical_terms=(
        "at-least-once",
        "message id",
        "idempotency",
        "transaction message",
    ),
    expected_signals=(
        "business processing before success acknowledgement",
        "duplicate delivery",
        "idempotent side effect",
    ),
    failure_modes=(
        "success returned before business processing loses work",
        "crash after side effect before acknowledgement repeats work",
    ),
    hard_negatives=("RocketMQ guarantees business exactly once",),
    weak_answer_signals=("retry", "message id"),
    expert_signals=(
        "transaction message checkback",
        "deduplication in the business transaction",
    ),
    follow_up_triggers=(
        "probe crash points around processing and acknowledgement",
        "probe idempotent side-effect design",
        "probe transaction-message checkback and cross-system boundaries",
    ),
    evaluation_levels=(
        EvaluationLevel(
            level="advanced",
            required_signals=(
                "business processing before success acknowledgement",
                "duplicate delivery",
                "idempotent side effect",
                "transaction message checkback",
            ),
        ),
    ),
    source_references=("rocketmq_delivery",),
    review_status=KnowledgeReviewStatus.REVIEWED,
)


class PilotKnowledgeUnitResolver:
    """Small reviewed catalog keyed by existing authoritative evidence IDs."""

    _UNITS = {
        "redis_distributed_lock": REDIS_DISTRIBUTED_LOCK_UNIT,
        "mysql_indexing": MYSQL_INDEXING_UNIT,
        "rocketmq_delivery": ROCKETMQ_DELIVERY_UNIT,
    }

    def resolve(self, references: list[Any]) -> KnowledgeUnit | None:
        evidence_ids = {_value(reference, "chunk_id") for reference in references}
        evidence_ids.discard(None)
        matches = {
            unit
            for evidence_id in evidence_ids
            if (unit := self._UNITS.get(str(evidence_id))) is not None
        }
        return matches.pop() if len(matches) == 1 else None


class ChainedKnowledgeUnitResolver:
    def __init__(self, *resolvers) -> None:
        self.resolvers = resolvers

    def resolve(self, references: list[Any]) -> KnowledgeUnit | None:
        for resolver in self.resolvers:
            unit = resolver.resolve(references)
            if unit is not None:
                return unit
        return None


def _value(item: Any, key: str):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def default_knowledge_unit_resolver() -> ChainedKnowledgeUnitResolver:
    from app.adapters.knowledge.metadata_unit_resolver import (
        MetadataKnowledgeUnitResolver,
    )

    return ChainedKnowledgeUnitResolver(
        MetadataKnowledgeUnitResolver(),
        PilotKnowledgeUnitResolver(),
    )
