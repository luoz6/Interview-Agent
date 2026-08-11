from __future__ import annotations

import ast
from pathlib import Path

from app.adapters.memory.context_artifacts import ContextArtifactMemoryAdapter
from app.adapters.pgvector.codec import PgVectorCodec
from app.adapters.postgres.context_artifacts import ContextArtifactPostgresAdapter
from app.domain.context.artifacts import ContextArtifactIntegrityPolicy
from app.domain.knowledge import KnowledgeChunk, KnowledgeQuery, KnowledgeReranker
from app.domain.memory.facts import PrincipalMemoryConflict, transition_fact
from app.ports.context_artifacts import ContextArtifactStore
from app.ports.principal_identity import PrincipalIdentityResolver
from app.ports.runtime import (
    EmbeddingPort,
    EmbeddingProvider,
    KnowledgeRepository,
    KnowledgeRepositoryPort,
)
from app.services.principal_memory_consent import PrincipalMemoryConsentPolicy
from app.services.principal_memory_context import PrincipalMemoryContextRenderer
from app.services.principal_memory_control import PrincipalMemoryControlPolicy
from app.services.principal_memory_ledger import PrincipalMemoryLedger
from app.services.principal_memory_lifecycle import PrincipalMemoryLifecycle
from app.services.principal_memory_retrieval import PrincipalMemorySelector
from app.services.principal_memory_rights import PrincipalMemoryRightsService
from app.services.principal_memory_shadow import PrincipalMemoryShadowObserver
from app.services.knowledge_ingestion import KnowledgeReleaseService


ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }


def test_wave_g_named_boundaries_are_concrete_and_ports_are_not_duplicated():
    assert all(
        isinstance(value, type)
        for value in (
            PrincipalMemoryConsentPolicy,
            PrincipalMemoryControlPolicy,
            PrincipalMemoryLifecycle,
            PrincipalMemorySelector,
            PrincipalMemoryContextRenderer,
            PrincipalMemoryRightsService,
            PrincipalMemoryLedger,
            PrincipalMemoryShadowObserver,
            KnowledgeChunk,
            KnowledgeQuery,
            KnowledgeReranker,
            KnowledgeReleaseService,
            PgVectorCodec,
            ContextArtifactIntegrityPolicy,
            ContextArtifactMemoryAdapter,
            ContextArtifactPostgresAdapter,
            PrincipalMemoryConflict,
        )
    )
    assert getattr(PrincipalIdentityResolver, "_is_runtime_protocol", False)
    assert EmbeddingPort is EmbeddingProvider
    assert KnowledgeRepositoryPort is KnowledgeRepository
    assert isinstance(ContextArtifactMemoryAdapter(), ContextArtifactStore)


def test_domain_contracts_do_not_depend_on_services_or_adapters():
    domain_paths = (
        ROOT / "app" / "domain" / "knowledge" / "models.py",
        ROOT / "app" / "domain" / "knowledge" / "reranking.py",
        ROOT / "app" / "domain" / "context" / "artifacts.py",
        ROOT / "app" / "domain" / "memory" / "contracts.py",
        ROOT / "app" / "domain" / "memory" / "facts.py",
    )
    for path in domain_paths:
        imports = _imports(path)
        assert not any(
            module.startswith(("app.services", "app.adapters"))
            for module in imports
        ), path

    assert not (ROOT / "app" / "services" / "vector_store.py").exists()
    assert (ROOT / "app" / "adapters" / "pgvector" / "repository.py").exists()
    assert callable(transition_fact)
    assert not (
        ROOT / "app" / "services" / "principal_memory_contracts.py"
    ).exists()
    assert not (
        ROOT / "app" / "services" / "in_memory_principal_memory.py"
    ).exists()
    assert not (
        ROOT / "app" / "services" / "postgres_principal_memory.py"
    ).exists()


def test_context_artifact_adapters_share_the_domain_integrity_policy():
    assert not (ROOT / "app" / "services" / "context_artifacts.py").exists()
    assert not (ROOT / "app" / "services" / "context_artifact_store.py").exists()
    assert not (
        ROOT / "app" / "services" / "in_memory_context_artifact_store.py"
    ).exists()
    for path in (
        ROOT / "app" / "adapters" / "memory" / "context_artifacts.py",
        ROOT / "app" / "adapters" / "postgres" / "context_artifacts.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "ContextArtifactIntegrityPolicy" in source
        assert "CONTEXT_ARTIFACT_PURPOSE_CONTRACT" in source
        assert "app.domain.context.artifacts" in _imports(path)


def test_runtime_composition_uses_canonical_principal_memory_names():
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "app" / "services" / "runtime.py",
            ROOT / "app" / "api" / "memory" / "routes.py",
        )
    )
    for name in (
        "PrincipalMemoryConsentPolicy",
        "PrincipalMemoryControlPolicy",
        "PrincipalMemoryLifecycle",
        "PrincipalMemorySelector",
        "PrincipalMemoryShadowObserver",
        "PrincipalMemoryRightsService",
    ):
        assert name in sources
    for legacy_name in (
        "PrincipalMemoryConsentService",
        "PrincipalMemoryControlService",
        "PrincipalMemoryLifecycleService",
        "PrincipalMemoryRetriever",
        "PrincipalMemoryShadowService",
    ):
        assert legacy_name not in sources
