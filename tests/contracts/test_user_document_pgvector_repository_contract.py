from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.adapters.pgvector.codec import PgVectorCodec
from app.adapters.pgvector.user_document_repository import (
    PgVectorUserDocumentChunkRepository,
)
from app.domain.knowledge.user_document import UserDocumentChunk
from app.ports.user_documents import UserDocumentChunkRepositoryPort


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)
OWNER_A = "principal-a"
OWNER_B = "principal-b"
ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_PATH = (
    ROOT / "app" / "adapters" / "pgvector" / "user_document_repository.py"
)


class ScriptedCursor:
    def __init__(self, connection) -> None:
        self.connection = connection
        self._row = None
        self._rows: list[tuple[object, ...]] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None) -> None:
        rendered = str(statement)
        self.connection.calls.append(("execute", rendered, params))
        result = (
            self.connection.results.pop(0)
            if self.connection.results
            else None
        )
        if isinstance(result, list):
            self._rows = result
            self._row = None
            self.rowcount = len(result)
        elif isinstance(result, int):
            self._rows = []
            self._row = None
            self.rowcount = result
        else:
            self._rows = []
            self._row = result
            self.rowcount = 0 if result is None else 1

    def executemany(self, statement, params) -> None:
        rows = list(params)
        self.connection.calls.append(("executemany", str(statement), rows))
        self._row = None
        self._rows = []
        self.rowcount = len(rows)

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class ScriptedConnection:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str, object | None]] = []

    def cursor(self) -> ScriptedCursor:
        return ScriptedCursor(self)


class ScriptedProvider:
    def __init__(self, results=()) -> None:
        self.connection_object = ScriptedConnection(results)
        self.leases = 0
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def connection(self):
        self.leases += 1
        try:
            yield self.connection_object
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


def _repository(
    results=(), *, embedding_dimension: int = 3
) -> tuple[PgVectorUserDocumentChunkRepository, ScriptedProvider]:
    provider = ScriptedProvider(results)
    repository = object.__new__(PgVectorUserDocumentChunkRepository)
    repository._connection_provider = provider
    repository.table_prefix = "interview"
    repository.documents_table = "interview_user_documents"
    repository.revisions_table = "interview_user_document_revisions"
    repository.chunks_table = "interview_user_document_chunks"
    repository.embedding_dimension = embedding_dimension
    repository.codec = PgVectorCodec()
    repository.schema_mode = "validate"
    return repository, provider


def _chunk(
    *,
    document_id: str | None = None,
    revision_id: str | None = None,
    chunk_id: str | None = None,
    owner: str = OWNER_A,
    position: int = 1,
    embedding: tuple[float, ...] = (0.125, -0.25, 0.5),
    section_label: str | None = None,
) -> UserDocumentChunk:
    return UserDocumentChunk(
        chunk_id=chunk_id or str(uuid4()),
        owner_principal_id=owner,
        document_id=document_id or str(uuid4()),
        document_revision_id=revision_id or str(uuid4()),
        position=position,
        title="PostgreSQL notes",
        section_label=section_label,
        content="Owner-scoped retrieval content.",
        content_sha256="c" * 64,
        embedding=embedding,
        embedding_identity="fake:model:revision:3",
        created_at=NOW,
    )


def _chunk_row(chunk: UserDocumentChunk) -> tuple[object, ...]:
    return (
        chunk.chunk_id,
        chunk.owner_principal_id,
        chunk.document_id,
        chunk.document_revision_id,
        chunk.position,
        chunk.title,
        chunk.section_label,
        chunk.content,
        chunk.content_sha256,
        "[0.125,-0.25,0.5]",
        chunk.embedding_identity,
        chunk.created_at,
    )


def _calls(provider: ScriptedProvider):
    return provider.connection_object.calls


def _normalized(statement: str) -> str:
    return " ".join(statement.split()).casefold()


def test_repository_satisfies_the_existing_runtime_checkable_port():
    repository, _provider = _repository()

    assert isinstance(repository, UserDocumentChunkRepositoryPort)


def test_constructor_requires_explicit_validate_mode_and_validates_schema(
    monkeypatch,
):
    provider = ScriptedProvider()
    with pytest.raises(ValueError, match="explicit schema_mode"):
        PgVectorUserDocumentChunkRepository(
            embedding_dimension=3,
            connection_provider=provider,
        )

    validated = []
    monkeypatch.setattr(
        "app.adapters.pgvector.user_document_repository."
        "validate_user_materials_schema",
        lambda supplied, *, table_prefix: validated.append(
            (supplied, table_prefix)
        ),
    )
    repository = PgVectorUserDocumentChunkRepository(
        embedding_dimension=3,
        connection_provider=provider,
        schema_mode="validate",
    )
    assert repository.embedding_dimension == 3
    assert repository.chunks_table == "interview_user_document_chunks"
    assert validated == [(provider, "interview")]

    with pytest.raises(ValueError, match="operator-owned"):
        PgVectorUserDocumentChunkRepository(
            embedding_dimension=3,
            connection_provider=provider,
            schema_mode="migrate",
        )


@pytest.mark.parametrize("dimension", [True, 0, -1, 3.0])
def test_constructor_rejects_invalid_embedding_dimensions(dimension):
    with pytest.raises(ValueError, match="positive integer"):
        PgVectorUserDocumentChunkRepository(
            embedding_dimension=dimension,
            connection_provider=ScriptedProvider(),
            schema_mode="validate",
        )


def test_list_maps_the_frozen_chunk_shape_and_nullable_section():
    chunk = _chunk(section_label=None)
    repository, provider = _repository([[_chunk_row(chunk)]])

    assert repository.list_revision_chunks(
        owner_principal_id=OWNER_A,
        document_revision_id=chunk.document_revision_id,
    ) == (chunk,)

    _kind, statement, params = _calls(provider)[0]
    assert "user-materials:list-revision-chunks" in statement
    assert "order by position asc, chunk_id asc" in _normalized(statement)
    assert params == (OWNER_A, chunk.document_revision_id)


def test_replacement_locks_scope_then_atomically_deletes_and_inserts():
    document_id = str(uuid4())
    revision_id = str(uuid4())
    chunks = (
        _chunk(document_id=document_id, revision_id=revision_id),
        _chunk(
            document_id=document_id,
            revision_id=revision_id,
            position=2,
            section_label="Indexes",
        ),
    )
    repository, provider = _repository([(revision_id,), 0])

    assert repository.replace_revision_chunks(
        owner_principal_id=OWNER_A,
        document_id=document_id,
        document_revision_id=revision_id,
        chunks=chunks,
    ) == 2

    lock, delete, insert = _calls(provider)
    assert "user-materials:lock-chunk-revision" in lock[1]
    assert lock[2] == (OWNER_A, document_id, revision_id)
    assert "for update" in _normalized(lock[1])
    assert "user-materials:replace-delete-chunks" in delete[1]
    assert delete[2] == (OWNER_A, document_id, revision_id)
    assert insert[0] == "executemany"
    assert "user-materials:replace-insert-chunks" in insert[1]
    assert [row[0] for row in insert[2]] == [OWNER_A, OWNER_A]
    assert [row[9] for row in insert[2]] == [
        "[0.12500000,-0.25000000,0.50000000]",
        "[0.12500000,-0.25000000,0.50000000]",
    ]
    assert provider.commits == 1
    assert provider.rollbacks == 0


def test_repeating_the_same_replacement_replays_identical_scoped_work():
    document_id = str(uuid4())
    revision_id = str(uuid4())
    chunks = (_chunk(document_id=document_id, revision_id=revision_id),)
    repository, provider = _repository(
        [(revision_id,), 1, (revision_id,), 1]
    )

    for _ in range(2):
        assert repository.replace_revision_chunks(
            owner_principal_id=OWNER_A,
            document_id=document_id,
            document_revision_id=revision_id,
            chunks=chunks,
        ) == 1

    calls = _calls(provider)
    assert calls[:3] == calls[3:]
    assert provider.commits == 2


def test_missing_revision_is_non_enumerable_and_rolls_back():
    chunk = _chunk()
    repository, provider = _repository([None])

    with pytest.raises(ValueError, match="^document revision not found$"):
        repository.replace_revision_chunks(
            owner_principal_id=OWNER_B,
            document_id=chunk.document_id,
            document_revision_id=chunk.document_revision_id,
            chunks=(chunk.model_copy(update={"owner_principal_id": OWNER_B}),),
        )

    assert len(_calls(provider)) == 1
    assert _calls(provider)[0][2] == (
        OWNER_B,
        chunk.document_id,
        chunk.document_revision_id,
    )
    assert provider.rollbacks == 1


def test_replacement_rejects_invalid_chunks_before_opening_a_connection():
    document_id = str(uuid4())
    revision_id = str(uuid4())
    good = _chunk(document_id=document_id, revision_id=revision_id)
    cases = (
        (
            (good.model_copy(update={"owner_principal_id": OWNER_B}),),
            "scope",
        ),
        ((good, good), "unique"),
        ((good.model_copy(update={"position": 2}),), "contiguous"),
        (
            (good.model_copy(update={"embedding": (0.1, 0.2)}),),
            "dimension",
        ),
    )

    for chunks, message in cases:
        repository, provider = _repository()
        with pytest.raises(ValueError, match=message):
            repository.replace_revision_chunks(
                owner_principal_id=OWNER_A,
                document_id=document_id,
                document_revision_id=revision_id,
                chunks=chunks,
            )
        assert provider.leases == 0

    invalid_values = good.model_dump(mode="python")
    invalid_values["title"] = ""
    invalid = UserDocumentChunk.model_construct(**invalid_values)
    repository, provider = _repository()
    with pytest.raises(ValidationError, match="title"):
        repository.replace_revision_chunks(
            owner_principal_id=OWNER_A,
            document_id=document_id,
            document_revision_id=revision_id,
            chunks=(invalid,),
        )
    assert provider.leases == 0


def test_searches_bind_owner_one_allowlist_and_one_global_limit():
    first_revision_id = str(uuid4())
    second_revision_id = str(uuid4())
    chunk = _chunk(revision_id=first_revision_id)
    repository, provider = _repository(
        [[_chunk_row(chunk)], [_chunk_row(chunk)]]
    )

    assert repository.search_semantic(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(
            first_revision_id,
            second_revision_id,
            first_revision_id,
        ),
        query_embedding=(0.125, -0.25, 0.5),
        limit=7,
    ) == (chunk,)
    assert repository.search_lexical(
        owner_principal_id=OWNER_B,
        allowed_document_revision_ids=(first_revision_id,),
        query_text="  owner scoped  ",
        limit=5,
    ) == (chunk,)

    semantic, lexical = _calls(provider)
    semantic_sql = _normalized(semantic[1])
    assert "embedding <=> %s::vector asc" in semantic_sql
    assert "chunk_id asc" in semantic_sql
    assert semantic_sql.count("limit %s") == 1
    assert semantic[2] == (
        OWNER_A,
        [first_revision_id, second_revision_id],
        "[0.12500000,-0.25000000,0.50000000]",
        7,
    )

    lexical_sql = _normalized(lexical[1])
    assert "lexical_document @@" in lexical_sql
    assert "websearch_to_tsquery('simple', %s)" in lexical_sql
    assert "ts_rank_cd(" in lexical_sql
    assert "chunk_id asc" in lexical_sql
    assert lexical_sql.count("limit %s") == 1
    assert lexical[2] == (
        OWNER_B,
        [first_revision_id],
        "owner scoped",
        "owner scoped",
        5,
    )


def test_empty_search_scope_short_circuits_and_inputs_are_validated():
    revision_id = str(uuid4())
    repository, provider = _repository()

    assert repository.search_semantic(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(),
        query_embedding=(0.125, -0.25, 0.5),
        limit=3,
    ) == ()
    assert repository.search_lexical(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_text="   ",
        limit=3,
    ) == ()
    assert provider.leases == 0

    for limit in (0, True):
        with pytest.raises(ValueError, match="positive integer"):
            repository.search_semantic(
                owner_principal_id=OWNER_A,
                allowed_document_revision_ids=(revision_id,),
                query_embedding=(0.125, -0.25, 0.5),
                limit=limit,
            )
    for vector in ((0.1, 0.2), (0.1, float("nan"), 0.3), (0.1, None, 0.3)):
        with pytest.raises(ValueError, match="dimension"):
            repository.search_semantic(
                owner_principal_id=OWNER_A,
                allowed_document_revision_ids=(revision_id,),
                query_embedding=vector,
                limit=3,
            )
    assert provider.leases == 0


def test_delete_zero_hit_and_follow_up_empty_reads_remain_owner_scoped():
    document_id = str(uuid4())
    revision_id = str(uuid4())
    repository, provider = _repository([3, [], [], [], 0])

    assert repository.delete_by_revision(
        owner_principal_id=OWNER_A,
        document_revision_id=revision_id,
    ) == 3
    assert repository.list_revision_chunks(
        owner_principal_id=OWNER_A,
        document_revision_id=revision_id,
    ) == ()
    assert repository.search_semantic(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_embedding=(0.125, -0.25, 0.5),
        limit=2,
    ) == ()
    assert repository.search_lexical(
        owner_principal_id=OWNER_A,
        allowed_document_revision_ids=(revision_id,),
        query_text="missing",
        limit=2,
    ) == ()
    assert repository.delete_by_document(
        owner_principal_id=OWNER_B,
        document_id=document_id,
    ) == 0

    calls = _calls(provider)
    assert [call[2][0] for call in calls] == [
        OWNER_A,
        OWNER_A,
        OWNER_A,
        OWNER_A,
        OWNER_B,
    ]
    assert calls[2][2][1] == [revision_id]
    assert calls[3][2][1] == [revision_id]


def test_every_repository_sql_statement_has_explicit_owner_scope():
    tree = ast.parse(REPOSITORY_PATH.read_text(encoding="utf-8"))
    statements = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "user-materials:" in node.value
    ]

    assert len(statements) == 8
    for statement in statements:
        normalized = _normalized(statement)
        assert "owner_principal_id" in normalized
        if " insert into " in f" {normalized} ":
            assert "owner_principal_id," in normalized
        else:
            assert "where owner_principal_id=%s" in normalized

    semantic = next(
        statement
        for statement in statements
        if "search-semantic-chunks" in statement
    )
    lexical = next(
        statement
        for statement in statements
        if "search-lexical-chunks" in statement
    )
    assert _normalized(semantic).count("limit %s") == 1
    assert _normalized(lexical).count("limit %s") == 1


def test_repository_source_adds_no_fusion_rerank_corpus_or_provider_calls():
    source = REPOSITORY_PATH.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "reciprocal_rank_fusion",
        "rrf",
        "rerank",
        "corpus",
        "embeddingprovider",
        "embedding_provider",
    ):
        assert forbidden not in source
