from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.domain.knowledge.engine import (
    KnowledgeEngine,
    RuntimeEngineExecution,
    RuntimeFallbackReason,
)
from app.services.prep import KnowledgeBindingSnapshot


def test_runtime_execution_requires_an_explicit_hybrid_to_legacy_fallback():
    with pytest.raises(ValidationError, match="fallback reason"):
        RuntimeEngineExecution(
            requested_engine=KnowledgeEngine.HYBRID_V2,
            effective_engine=KnowledgeEngine.LEGACY,
            retrieval_availability="available",
            engine_version="compatibility-v1",
        )

    execution = RuntimeEngineExecution(
        requested_engine=KnowledgeEngine.HYBRID_V2,
        effective_engine=KnowledgeEngine.LEGACY,
        fallback_reason=RuntimeFallbackReason.CANDIDATE_ENGINE_FAILED,
        retrieval_availability="available",
        engine_version="compatibility-v1",
    )

    assert execution.fallback_reason == RuntimeFallbackReason.CANDIDATE_ENGINE_FAILED


def test_legacy_assignment_snapshot_is_readable_and_migrates_to_execution():
    snapshot = KnowledgeBindingSnapshot.model_validate(
        {
            "prep_run_id": "prep-legacy",
            "corpus_manifest_sha256": "a" * 64,
            "status": "completed",
            "knowledge_engine_assignment": {
                "session_id_sha256": sha256(b"prep-legacy").hexdigest(),
                "engine": "hybrid-v2",
                "assignment_version": "knowledge-assignment-v1",
                "bucket": 2,
                "rollout_percent": 5,
            },
        }
    )

    assert snapshot.knowledge_engine_assignment is not None
    assert snapshot.knowledge_engine_execution is not None
    assert snapshot.knowledge_engine_execution.effective_engine == (
        KnowledgeEngine.HYBRID_V2
    )
    assert snapshot.knowledge_engine_execution.migrated_from_legacy_assignment is True


def test_new_snapshot_does_not_require_a_legacy_assignment():
    execution = RuntimeEngineExecution(
        requested_engine=KnowledgeEngine.LEGACY,
        effective_engine=KnowledgeEngine.LEGACY,
        retrieval_availability="available",
        engine_version="compatibility-v1",
    )
    snapshot = KnowledgeBindingSnapshot(
        prep_run_id="prep-new",
        corpus_manifest_sha256="b" * 64,
        status="completed",
        knowledge_engine_execution=execution,
    )

    assert snapshot.knowledge_engine_assignment is None
    assert snapshot.knowledge_engine_execution is execution
