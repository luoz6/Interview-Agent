from __future__ import annotations

import ast
from pathlib import Path
import re

from app.adapters.postgres.user_materials_schema import (
    user_materials_relation_names,
    user_materials_schema_statements,
)


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
SCHEMA_HELPER = APP / "adapters" / "postgres" / "user_materials_schema.py"


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def test_only_the_two_scoped_materials_postgres_adapters_are_implemented():
    store_path = APP / "adapters" / "postgres" / "user_documents.py"
    repository_path = (
        APP / "adapters" / "pgvector" / "user_document_repository.py"
    )
    assert store_path.exists()
    assert repository_path.exists()

    tree = ast.parse(SCHEMA_HELPER.read_text(encoding="utf-8"))
    forbidden_functions = {
        "create_document",
        "create_revision",
        "delete_by_document",
        "delete_by_revision",
        "delete_document",
        "get_document",
        "get_revision",
        "list_documents",
        "list_revision_chunks",
        "replace_revision_chunks",
        "save_document",
        "search_lexical",
        "search_semantic",
    }
    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not (functions & forbidden_functions)
    assert not [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]

    store_tree = ast.parse(store_path.read_text(encoding="utf-8"))
    assert {
        node.name for node in ast.walk(store_tree) if isinstance(node, ast.ClassDef)
    } == {"PostgresUserDocumentStore"}
    store_functions = {
        node.name
        for node in ast.walk(store_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not (
        store_functions
        & {
            "delete_by_document",
            "delete_by_revision",
            "list_revision_chunks",
            "replace_revision_chunks",
            "search_lexical",
            "search_semantic",
        }
    )

    repository_tree = ast.parse(repository_path.read_text(encoding="utf-8"))
    assert {
        node.name
        for node in ast.walk(repository_tree)
        if isinstance(node, ast.ClassDef)
    } == {"PgVectorUserDocumentChunkRepository"}
    repository_public_methods = {
        node.name
        for node in ast.walk(repository_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert repository_public_methods == {
        "delete_by_document",
        "delete_by_revision",
        "list_revision_chunks",
        "replace_revision_chunks",
        "search_lexical",
        "search_semantic",
    }
    repository_source = repository_path.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "reciprocal_rank_fusion",
        "rrf",
        "rerank",
        "corpus",
    ):
        assert forbidden not in repository_source


def test_materials_schema_is_not_wired_into_global_runtime_or_rag_migration():
    helper_module = "app.adapters.postgres.user_materials_schema"
    for relative_path in (
        "services/runtime.py",
        "services/postgres_runtime_migrations.py",
        "adapters/postgres/migration_harness.py",
        "runtime/config/loader.py",
    ):
        source = (APP / relative_path).read_text(encoding="utf-8")
        assert helper_module not in source
        assert "migrate_user_materials_schema" not in source
        assert "validate_user_materials_schema" not in source


def test_materials_schema_creates_exactly_three_independent_table_families():
    statements = user_materials_schema_statements(
        table_prefix="interview",
        embedding_dimension=1536,
    )
    create_tables = [
        statement
        for statement in statements
        if _normalized(statement).startswith("create table")
    ]
    created_relations = {
        match.group(1)
        for statement in create_tables
        if (
            match := re.search(
                r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"([^"]+)"',
                statement,
                flags=re.IGNORECASE,
            )
        )
    }
    assert len(create_tables) == 3
    assert created_relations == set(user_materials_relation_names("interview"))

    combined = "\n".join(statements)
    lowered = _normalized(combined)
    assert "drop " not in lowered
    assert "truncate " not in lowered
    assert "insert into " not in lowered
    assert "update " not in lowered
    assert "delete from " not in lowered
    assert "_versions" not in lowered
    assert "_releases" not in lowered
    assert "corpus" not in lowered
    assert "manifest_sha256" not in lowered
    alter_targets = re.findall(
        r'ALTER\s+TABLE\s+"([^"]+)"',
        combined,
        flags=re.IGNORECASE,
    )
    assert alter_targets == ["interview_user_documents"]


def test_every_materials_relation_is_owner_scoped_and_cross_owner_safe():
    statements = user_materials_schema_statements(
        table_prefix="interview",
        embedding_dimension=1536,
    )
    create_tables = {
        relation: _normalized(statement)
        for relation in user_materials_relation_names("interview")
        for statement in statements
        if f'create table if not exists "{relation}"' in _normalized(statement)
    }
    assert set(create_tables) == set(user_materials_relation_names("interview"))
    assert all(
        "owner_principal_id text not null" in statement
        for statement in create_tables.values()
    )
    assert (
        "primary key (owner_principal_id, document_id)"
        in create_tables["interview_user_documents"]
    )
    assert (
        "foreign key (owner_principal_id, document_id) references "
        '"interview_user_documents" (owner_principal_id, document_id) '
        "on delete cascade"
        in create_tables["interview_user_document_revisions"]
    )
    chunk_ddl = create_tables["interview_user_document_chunks"]
    assert (
        "foreign key ( owner_principal_id, document_id, "
        "document_revision_id ) references "
        '"interview_user_document_revisions" ( owner_principal_id, '
        "document_id, document_revision_id ) on delete cascade"
        in chunk_ddl
    )

    combined = _normalized("\n".join(statements))
    assert (
        "foreign key ( owner_principal_id, document_id, active_revision_id ) "
        "references "
        '"interview_user_document_revisions" ( owner_principal_id, '
        "document_id, document_revision_id )"
        in combined
    )


def test_chunks_hold_vectors_and_one_postgres_lexical_channel_in_row():
    statements = user_materials_schema_statements(
        table_prefix="interview",
        embedding_dimension=1536,
    )
    combined = _normalized("\n".join(statements))
    assert "embedding vector(1536) not null" in combined
    assert "lexical_document tsvector generated always as" in combined
    assert "using gin (lexical_document)" in combined
    assert "using hnsw (embedding vector_cosine_ops)" in combined
    assert "embedding table" not in combined
    for forbidden in (
        "create_version",
        "activate_version",
        "retire_version",
        "reciprocal_rank_fusion",
        "evidence_gate",
        "rerank",
    ):
        assert forbidden not in combined
