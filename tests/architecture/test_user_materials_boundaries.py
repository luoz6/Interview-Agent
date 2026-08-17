from __future__ import annotations

import ast
from pathlib import Path

from app.domain.knowledge.user_document import (
    USER_MATERIALS_CAPABILITIES,
    USER_MATERIALS_PERSISTENCE_PORTS,
)


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> set[str]:
    tree = _tree(path)
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return modules


def _symbols(path: Path) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            values.add(node.name)
        elif isinstance(node, ast.Name):
            values.add(node.id)
        elif isinstance(node, ast.Attribute):
            values.add(node.attr)
        elif isinstance(node, ast.alias):
            values.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(node.value)
    return values


def _material_paths() -> tuple[Path, ...]:
    paths = {
        APP / "domain" / "knowledge" / "source_scope.py",
        APP / "domain" / "knowledge" / "user_document.py",
        APP / "adapters" / "knowledge" / "source_aware_retriever.py",
        APP / "services" / "interview_knowledge_scope.py",
    }
    for pattern in (
        "app/application/materials/**/*.py",
        "app/api/materials/**/*.py",
        "app/adapters/**/*user_document*.py",
        "app/ports/user_document*.py",
    ):
        paths.update(ROOT.glob(pattern))
    return tuple(sorted(path for path in paths if path.exists()))


def test_user_materials_and_global_corpus_lifecycles_are_bilaterally_separate():
    material_paths = _material_paths()
    assert material_paths
    forbidden_corpus_modules = {
        "app.application.knowledge.corpus_write_service",
        "app.services.knowledge_corpus_schema",
        "app.api.rag.routes",
    }
    forbidden_corpus_symbols = {
        "CorpusCreateVersionRequest",
        "KnowledgeCorpusWriteService",
        "RAG_CORPUS_WRITE_ENABLED",
        "activate_version",
        "create_version",
        "retire_version",
    }
    for path in material_paths:
        assert not (_imports(path) & forbidden_corpus_modules), path
        assert not (_symbols(path) & forbidden_corpus_symbols), path

    corpus_paths = (
        APP / "application" / "knowledge" / "corpus_write_service.py",
        APP / "services" / "knowledge_corpus_schema.py",
        APP / "api" / "rag" / "routes.py",
    )
    for path in corpus_paths:
        imports = _imports(path)
        assert not any(
            module.startswith(
                (
                    "app.application.materials",
                    "app.api.materials",
                    "app.domain.knowledge.source_scope",
                    "app.domain.knowledge.user_document",
                    "app.ports.user_document",
                )
            )
            for module in imports
        ), path


def test_materials_contract_adds_no_rbac_or_role_hierarchy():
    assert USER_MATERIALS_CAPABILITIES == (
        "USER_MATERIALS_ENABLED",
        "USER_MATERIALS_INGEST_ENABLED",
    )
    forbidden_tokens = {"account", "admin", "login", "rbac", "role", "tenant"}
    for path in _material_paths():
        identifier_tokens = {
            token.casefold()
            for symbol in _symbols(path)
            for token in symbol.replace("-", "_").replace(".", "_").split("_")
        }
        assert not (identifier_tokens & forbidden_tokens), path

    principal_port = APP / "ports" / "principal_identity.py"
    assert "PrincipalIdentityResolver" in _symbols(principal_port)


def test_materials_domain_application_and_api_do_not_depend_on_principal_memory():
    forbidden_module_prefixes = (
        "app.domain.memory",
        "app.ports.principal_memory",
        "app.services.principal_memory",
        "app.adapters.memory.principal_memory",
        "app.adapters.postgres.principal_memory",
    )
    forbidden_symbols = {
        "PrincipalMemoryFact",
        "PrincipalMemoryFactStorePort",
        "PrincipalMemoryProposal",
        "PrincipalMemoryProposalProcessor",
        "PrincipalMemorySelector",
        "get_principal_memory_consume_service",
        "get_principal_memory_fact_store",
        "get_principal_memory_proposal_processor",
        "get_principal_memory_shadow_service",
    }

    for path in _material_paths():
        assert not any(
            module.startswith(forbidden_module_prefixes)
            for module in _imports(path)
        ), path
        assert not (_symbols(path) & forbidden_symbols), path


def test_materials_contract_freezes_only_two_persistence_ports():
    assert USER_MATERIALS_PERSISTENCE_PORTS == (
        "UserDocumentStorePort",
        "UserDocumentChunkRepositoryPort",
    )
    implemented = {
        node.name
        for path in (APP / "ports").glob("user_document*.py")
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef) and node.name.endswith("Port")
    }
    assert implemented <= set(USER_MATERIALS_PERSISTENCE_PORTS)


def test_materials_contract_has_one_scope_resolver_and_four_services():
    service_classes = {
        node.name
        for path in _material_paths()
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef)
        and node.name.endswith(("Service", "Resolver"))
    }
    assert service_classes == {
        "UserDocumentService",
        "UserDocumentIngestionService",
        "UserDocumentDeletionService",
        "InterviewKnowledgeScopeResolver",
    }
    scope_resolvers = [
        path.relative_to(ROOT).as_posix()
        for path in _material_paths()
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef)
        and node.name == "InterviewKnowledgeScopeResolver"
    ]
    assert scope_resolvers == ["app/services/interview_knowledge_scope.py"]


def test_user_materials_do_not_create_a_fusion_or_rrf_implementation():
    offenders = []
    for path in _material_paths():
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                tokens = node.name.casefold().replace("-", "_").split("_")
                if "fusion" in tokens or "rrf" in tokens:
                    offenders.append((path.relative_to(ROOT).as_posix(), node.name))
    assert offenders == []


def test_authoritative_fusion_still_accepts_only_semantic_and_lexical_channels():
    fusion_path = APP / "domain" / "knowledge" / "fusion.py"
    fusion_tree = _tree(fusion_path)
    functions = {
        node.name: node
        for node in fusion_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in (
        "fuse_retrieval_candidates",
        "weighted_reciprocal_rank_fusion",
        "rank_normalized_score_fusion",
    ):
        assert [arg.arg for arg in functions[name].args.args] == [
            "semantic",
            "lexical",
        ]

    hybrid_tree = _tree(
        APP / "application" / "knowledge" / "hybrid_retrieval_service.py"
    )
    calls = [
        node
        for node in ast.walk(hybrid_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "fuse_retrieval_candidates"
    ]
    assert len(calls) == 1
    assert len(calls[0].args) == 2
    assert not {
        keyword.arg for keyword in calls[0].keywords
    }.intersection({"material", "source", "system", "user"})


def test_citation_model_write_projector_and_read_sanitizer_are_unique():
    definitions: dict[str, list[str]] = {
        "SafeKnowledgeCitation": [],
        "project_safe_knowledge_citations": [],
        "sanitize_report_knowledge_citations_for_read": [],
    }
    for path in APP.rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in definitions:
                    definitions[node.name].append(path.relative_to(ROOT).as_posix())

    assert definitions == {
        "SafeKnowledgeCitation": ["app/domain/knowledge/evidence.py"],
        "project_safe_knowledge_citations": ["app/services/knowledge_citations.py"],
        "sanitize_report_knowledge_citations_for_read": [
            "app/services/knowledge_citations.py"
        ],
    }


def test_frontend_has_no_second_citation_client_hook_or_store():
    frontend = ROOT / "frontend" / "src"
    production_mentions = {
        path.relative_to(ROOT).as_posix()
        for pattern in ("*.js", "*.jsx")
        for path in frontend.rglob(pattern)
        if not path.name.endswith(".test.jsx")
        and not path.name.endswith(".test.js")
        and "knowledge_citations" in path.read_text(encoding="utf-8")
    }
    assert production_mentions == {
        "frontend/src/pages/InterviewPage.jsx",
        "frontend/src/pages/ReportDetailPage.jsx",
    }

    for boundary in ("api", "hooks", "stores"):
        directory = frontend / boundary
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.suffix in {".js", ".jsx"}:
                assert "knowledge_citations" not in path.read_text(encoding="utf-8"), path
