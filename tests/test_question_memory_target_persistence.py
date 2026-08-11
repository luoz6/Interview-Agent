from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.context_artifacts import (
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextCompressorConfig,
    canonical_identity_payload,
)
from app.services.context_budget import (
    DynamicCompressionTargetPolicy,
    allocate_dynamic_compression_target,
)
from app.services.context_compression import QUESTION_MEMORY_COMPRESSION_POLICY
from app.services.context_compression_request import (
    ResolvedCompressionRequest,
    bind_resolved_target_to_identity,
)
from app.services.context_compression_runner import ContextCompressionRunner
from app.services.in_memory_context_artifact_store import (
    InMemoryContextArtifactStore,
)
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.memory_config import load_effective_memory_config
from app.services.postgres_question_memory_index import (
    PostgresQuestionMemoryIndexStore,
)
from app.services.question_memory import QuestionMemoryCoordinator
from app.services.question_memory_index import QuestionMemoryIndexEntry
from tests.test_question_memory import (
    CompressorAgent,
    ParentOwnership,
    make_state,
    make_structured_selection,
)


_MISSING = object()
_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
_TARGET_POLICY = DynamicCompressionTargetPolicy(
    floor_tokens=256,
    source_ratio_basis_points=2_500,
    allowed_target_tokens=(256, 512, 1_024, 1_536, 2_000),
)
_SOURCE_FRAME = (
    ("interviewer", "old cache question"),
    ("candidate", "old cache answer"),
)
_RETAINED_FRAME = (
    ("interviewer", "current system question"),
    ("candidate", "current system answer"),
)


def _entry_values(**changes):
    question_id = changes.pop("question_id", "q1")
    skill_tags = changes.pop("skill_tags", ["idempotency"])
    topic_codes = changes.pop(
        "unresolved_topic_codes",
        ["missing_tradeoff"],
    )
    values = {
        "session_id": "session-1",
        "question_id": question_id,
        "question_id_sha256": sha256(question_id.encode()).hexdigest(),
        "focus_sha256": "1" * 64,
        "focus_tags": ["distributed_systems"],
        "skill_tags": skill_tags,
        "skill_tag_sha256": [
            sha256(value.encode()).hexdigest() for value in skill_tags
        ],
        "unresolved_topic_codes": topic_codes,
        "unresolved_topic_sha256": [
            sha256(value.encode()).hexdigest() for value in topic_codes
        ],
        "artifact_ref": "context-artifact-ref:memory-1",
        "artifact_sha256": "2" * 64,
        "policy_version": "question-memory-v1",
        "source_manifest_sha256": "3" * 64,
        "source_message_count": 2,
        "source_max_sequence_no": 4,
        "created_at": _NOW,
    }
    values.update(changes)
    return values


def _model_entry(*, target=_MISSING, **changes):
    values = _entry_values(**changes)
    if target is not _MISSING:
        values["resolved_target_output_tokens"] = target
    return QuestionMemoryIndexEntry(**values)


def _injected_entry(*, target, **changes):
    """Inject Phase-C state without making old production models validate it."""

    return _model_entry(**changes).model_copy(
        update={"resolved_target_output_tokens": target}
    )


def test_index_model_declares_nullable_target_and_keeps_payload_text_out():
    field = QuestionMemoryIndexEntry.model_fields.get(
        "resolved_target_output_tokens"
    )

    assert field is not None
    assert field.default is None

    legacy = _model_entry()
    dynamic = _model_entry(target=512)
    assert legacy.resolved_target_output_tokens is None
    assert dynamic.resolved_target_output_tokens == 512
    assert "summary" not in QuestionMemoryIndexEntry.model_fields
    assert not any(
        "excerpt" in name.casefold()
        for name in QuestionMemoryIndexEntry.model_fields
    )


@pytest.mark.parametrize("target", (256, 512, 1_024, 2_000))
def test_index_model_accepts_positive_dynamic_and_fixed_targets(target):
    entry = _model_entry(target=target)

    assert entry.resolved_target_output_tokens == target


@pytest.mark.parametrize("target", (0, -1, True, 512.0, "512"))
def test_index_model_rejects_non_positive_or_non_integer_targets(target):
    with pytest.raises(ValidationError) as captured:
        _model_entry(target=target)

    target_errors = [
        error
        for error in captured.value.errors()
        if error["loc"] == ("resolved_target_output_tokens",)
    ]
    assert target_errors
    assert all(error["type"] != "extra_forbidden" for error in target_errors)


class _Clock:
    def __init__(self):
        self.value = _NOW

    def __call__(self):
        return self.value

    def advance(self):
        self.value += timedelta(seconds=1)


def test_in_memory_active_historical_and_supersede_round_trip_target():
    clock = _Clock()
    store = InMemoryQuestionMemoryIndexStore(clock=clock)
    first = store.activate(_injected_entry(target=512, created_at=clock()))
    clock.advance()
    second = store.activate(
        _injected_entry(
            target=1_024,
            artifact_ref="context-artifact-ref:memory-2",
            artifact_sha256="4" * 64,
            source_manifest_sha256="5" * 64,
            source_max_sequence_no=5,
            created_at=clock(),
        )
    )

    active = store.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )
    historical = store.get_historical(first.artifact_ref)
    assert active == second
    assert active.resolved_target_output_tokens == 1_024
    assert historical.status == "superseded"
    assert historical.resolved_target_output_tokens == 512


def test_in_memory_legacy_null_round_trips_without_inventing_authority():
    store = InMemoryQuestionMemoryIndexStore()
    legacy = store.activate(_injected_entry(target=None))

    active = store.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )
    historical = store.get_historical(legacy.artifact_ref)
    assert active.resolved_target_output_tokens is None
    assert historical.resolved_target_output_tokens is None


class _FramingEstimator:
    def __init__(self, *, source_tokens, forbid_dynamic_frames=False):
        self.source_tokens = source_tokens
        self.forbid_dynamic_frames = forbid_dynamic_frames
        self.dynamic_frames = []

    def estimate_messages(self, messages, *, model):
        frame = tuple(
            (str(item.get("role", "")), str(item.get("content", "")))
            for item in messages
        )
        if frame in {_SOURCE_FRAME, _RETAINED_FRAME}:
            if self.forbid_dynamic_frames:
                raise AssertionError(
                    "legacy indexed target must bypass the current allocator"
                )
            self.dynamic_frames.append(frame)
            return self.source_tokens if frame == _SOURCE_FRAME else 800
        return sum(
            self.estimate_message(message, model=model) for message in messages
        )

    def estimate_message(self, message, *, model):
        return self.estimate_text(str(message.get("content", "")), model=model)

    def estimate_text(self, text, *, model):
        return max(1, len(text.encode("utf-8")) // 20)


def _runtime(*, source_tokens, forbid_dynamic_frames=False):
    estimator = _FramingEstimator(
        source_tokens=source_tokens,
        forbid_dynamic_frames=forbid_dynamic_frames,
    )
    return SimpleNamespace(
        estimator_resolution=SimpleNamespace(estimator=estimator),
        model_profile=SimpleNamespace(model="gpt-4o"),
        dynamic_compression_target_policy=_TARGET_POLICY,
    )


class _CountingIndex(InMemoryQuestionMemoryIndexStore):
    def __init__(self):
        super().__init__()
        self.activate_calls = 0

    def activate(self, entry):
        self.activate_calls += 1
        return super().activate(entry)


def _coordinator(*, agent, index_store, artifact_store, runtime):
    selection = load_effective_memory_config({}).selection
    return QuestionMemoryCoordinator(
        runner=ContextCompressionRunner(artifact_store, lease_seconds=30),
        compressor_agent=agent,
        compressor_config=ContextCompressorConfig(
            provider="openai-compatible",
            model="gpt-4o",
            base_url_identity="https://api.example.com/v1",
            temperature=0,
            request_timeout_seconds=30,
            timeout_policy_version="timeout-v1",
            max_retries=1,
            structured_output_mode="json_schema",
            tokenizer_family=None,
        ),
        context_runtime=runtime,
        index_store=index_store,
        deployment_scope="single-tenant-test",
        exact_recent_questions=selection.exact_recent_questions,
        max_memory_units=selection.max_memory_units,
        max_memory_tokens=selection.max_memory_tokens,
        task_intent_enabled=False,
        clock=lambda: _NOW,
    )


def _dynamic_selection(state, *, selectable_content_tokens=4_000):
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    return replace(
        selection,
        stats=replace(
            selection.stats,
            selectable_content_tokens=selectable_content_tokens,
        ),
    )


def _capture_runner(coordinator):
    calls = []
    resolutions = []
    resolve = coordinator.runner.resolve

    def capture(**kwargs):
        calls.append(kwargs)
        resolution = resolve(**kwargs)
        resolutions.append(resolution)
        return resolution

    coordinator.runner.resolve = capture
    return calls, resolutions


def _closed_source(coordinator, *, state, selection):
    deterministic = list(selection.provider_messages)
    validated = coordinator._validated_selection_sources(
        state,
        selection=selection,
        deterministic_context=deterministic,
    )
    sources = coordinator._closed_question_sources(
        state,
        validated_selection=validated,
    )
    assert len(sources) == 1
    return sources[0]


def _entry_for_source(
    coordinator,
    *,
    state,
    source,
    artifact_ref,
    artifact_sha256,
    indexed_target=_MISSING,
):
    focus_tags = coordinator._focus_taxonomy(source["question"])
    skill_tags = coordinator._skill_taxonomy(state, source["question"])
    entry = QuestionMemoryIndexEntry(
        session_id=state["session_id"],
        question_id=source["question"]["id"],
        question_id_sha256=source["question_id_sha256"],
        focus_sha256=source["focus_sha256"],
        focus_tags=focus_tags,
        skill_tags=skill_tags,
        skill_tag_sha256=[
            sha256(value.encode()).hexdigest() for value in skill_tags
        ],
        unresolved_topic_codes=[],
        unresolved_topic_sha256=[],
        artifact_ref=artifact_ref,
        artifact_sha256=artifact_sha256,
        policy_version=QUESTION_MEMORY_COMPRESSION_POLICY.policy_version,
        source_manifest_sha256=source["manifest"].sha256,
        source_message_count=len(source["messages"]),
        source_max_sequence_no=source["max_sequence_no"],
        created_at=_NOW,
    )
    if indexed_target is not _MISSING:
        entry = entry.model_copy(
            update={"resolved_target_output_tokens": indexed_target}
        )
    return entry


def _seed_completed_artifact(
    *,
    coordinator,
    index_store,
    state,
    selection,
    artifact_target,
    indexed_target=_MISSING,
):
    source = _closed_source(coordinator, state=state, selection=selection)
    request = ResolvedCompressionRequest(
        policy=QUESTION_MEMORY_COMPRESSION_POLICY,
        intent=None,
        source_segments=tuple(source["segments"]),
        resolved_target_output_tokens=artifact_target,
        target_policy=(None if artifact_target == 2_000 else _TARGET_POLICY),
    )
    ownership = ParentOwnership()
    resolution = coordinator.runner.resolve(
        identity_material=coordinator._identity(source, intent=None),
        request=request,
        estimator=coordinator.context_runtime.estimator_resolution.estimator,
        model=coordinator.context_runtime.model_profile.model,
        compressor=lambda actual: coordinator.compressor_agent.compress(
            request=actual,
            expected_session_scope_sha256=source["session_scope_sha256"],
            expected_question_id_sha256=source["question_id_sha256"],
            expected_question_focus_sha256=source["focus_sha256"],
            expected_source_manifest_sha256=source["manifest"].sha256,
        ),
        worker_id=ownership.worker_id,
        owner_type="interview_session",
        owner_key=state["session_id"],
        purpose="interview_question_memory",
        parent_ownership=ownership,
        expected_session_scope_sha256=source["session_scope_sha256"],
        expected_question_id_sha256=source["question_id_sha256"],
        expected_question_focus_sha256=source["focus_sha256"],
        expected_source_manifest_sha256=source["manifest"].sha256,
    )
    assert resolution.route == "artifact_created"
    assert resolution.record.identity.material.target_output_tokens == artifact_target
    assert resolution.record.identity.material.identity_schema_version is None
    entry = _entry_for_source(
        coordinator,
        state=state,
        source=source,
        artifact_ref=resolution.ref.artifact_ref,
        artifact_sha256=resolution.ref.artifact_sha256,
        indexed_target=indexed_target,
    )
    return resolution, index_store.activate(entry), source


def test_dynamic_create_persists_512_then_fresh_1024_policy_reuses_exact_v0():
    state = make_state()
    selection = _dynamic_selection(state)
    artifact_store = InMemoryContextArtifactStore()
    index_store = _CountingIndex()
    first_agent = CompressorAgent()
    first = _coordinator(
        agent=first_agent,
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=_runtime(source_tokens=2_000),
    )
    first_calls, first_resolutions = _capture_runner(first)

    created = first.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    entry_before_restart = index_store.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )

    assert created.route == "artifact_created"
    assert first_agent.calls == 1
    assert first_calls[0]["request"].resolved_target_output_tokens == 512
    assert first_calls[0]["request"].resolved_target_authority == (
        "policy_resolution"
    )
    assert entry_before_restart.resolved_target_output_tokens == 512
    assert first_resolutions[0].record.identity.material.target_output_tokens == 512
    assert first_resolutions[0].record.identity.material.identity_schema_version is None

    restart_agent = CompressorAgent()
    restarted = _coordinator(
        agent=restart_agent,
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=_runtime(source_tokens=3_000),
    )
    restart_calls, restart_resolutions = _capture_runner(restarted)
    assert allocate_dynamic_compression_target(
        source_tokens=3_000,
        policy=_TARGET_POLICY,
        policy_hard_cap_tokens=2_000,
        remaining_business_budget_tokens=3_200,
    ) == 1_024

    reused = restarted.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert reused.route == "memory_index_retrieved"
    assert restart_agent.calls == 0
    assert restart_calls[0]["request"].resolved_target_output_tokens == 512
    assert restart_calls[0]["request"].resolved_target_authority == (
        "persisted_index"
    )
    assert restart_calls[0]["request"].target_policy is _TARGET_POLICY
    assert restart_calls[0]["identity_material"].identity_schema_version is None
    assert restart_resolutions[0].ref == first_resolutions[0].ref
    assert (
        restart_resolutions[0].record.identity.artifact_key
        == first_resolutions[0].record.identity.artifact_key
    )
    assert index_store.activate_calls == 1
    assert index_store.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    ) == entry_before_restart


def test_preseeded_512_target_is_restart_authority_over_current_1024_choice():
    state = make_state()
    selection = _dynamic_selection(state)
    artifact_store = InMemoryContextArtifactStore()
    index_store = _CountingIndex()
    seed = _coordinator(
        agent=CompressorAgent(),
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=_runtime(source_tokens=2_000),
    )
    seeded, entry, _source = _seed_completed_artifact(
        coordinator=seed,
        index_store=index_store,
        state=state,
        selection=selection,
        artifact_target=512,
        indexed_target=512,
    )
    restart_agent = CompressorAgent()
    restarted = _coordinator(
        agent=restart_agent,
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=_runtime(source_tokens=3_000),
    )
    calls, resolutions = _capture_runner(restarted)

    result = restarted.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "memory_index_retrieved"
    assert restart_agent.calls == 0
    assert calls[0]["request"].resolved_target_output_tokens == 512
    rebound = ContextArtifactIdentity.from_material(
        bind_resolved_target_to_identity(
            calls[0]["identity_material"],
            calls[0]["request"],
        )
    )
    assert rebound.artifact_key == seeded.record.identity.artifact_key
    assert resolutions[0].ref == seeded.ref
    assert resolutions[0].record.identity == seeded.record.identity
    assert index_store.activate_calls == 1
    assert index_store.get_historical(entry.artifact_ref) == entry


def test_legacy_null_reuses_fixed_2000_v0_without_running_allocator():
    state = make_state()
    selection = _dynamic_selection(state)
    artifact_store = InMemoryContextArtifactStore()
    index_store = _CountingIndex()
    seed = _coordinator(
        agent=CompressorAgent(),
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=_runtime(source_tokens=2_000, forbid_dynamic_frames=True),
    )
    seeded, entry, _source = _seed_completed_artifact(
        coordinator=seed,
        index_store=index_store,
        state=state,
        selection=selection,
        artifact_target=2_000,
        indexed_target=None,
    )
    restart_agent = CompressorAgent()
    restart_runtime = _runtime(
        source_tokens=3_000,
        forbid_dynamic_frames=True,
    )
    restarted = _coordinator(
        agent=restart_agent,
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=restart_runtime,
    )
    calls, resolutions = _capture_runner(restarted)

    result = restarted.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "memory_index_retrieved"
    assert restart_agent.calls == 0
    assert restart_runtime.estimator_resolution.estimator.dynamic_frames == []
    assert calls[0]["request"].resolved_target_output_tokens == 2_000
    assert calls[0]["request"].target_policy is None
    assert calls[0]["request"].resolved_target_authority == (
        "policy_resolution"
    )
    assert calls[0]["identity_material"].identity_schema_version is None
    assert resolutions[0].ref == seeded.ref
    assert resolutions[0].record.identity.artifact_key == (
        seeded.record.identity.artifact_key
    )
    assert entry.resolved_target_output_tokens is None
    assert index_store.activate_calls == 1


def test_index_target_conflicting_with_artifact_identity_fails_closed():
    state = make_state()
    selection = _dynamic_selection(state)
    artifact_store = InMemoryContextArtifactStore()
    index_store = _CountingIndex()
    seed = _coordinator(
        agent=CompressorAgent(),
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=_runtime(source_tokens=2_000),
    )
    seeded, entry, _source = _seed_completed_artifact(
        coordinator=seed,
        index_store=index_store,
        state=state,
        selection=selection,
        artifact_target=512,
        indexed_target=1_024,
    )
    restart_agent = CompressorAgent()
    restarted = _coordinator(
        agent=restart_agent,
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=_runtime(source_tokens=3_000),
    )
    calls, _resolutions = _capture_runner(restarted)

    result = restarted.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "deterministic"
    assert restart_agent.calls == 0
    assert len(calls) == 1
    request = calls[0]["request"]
    assert request.resolved_target_output_tokens == 1_024
    assert request.target_policy is _TARGET_POLICY
    assert artifact_store.get_terminal_by_key(
        seeded.record.identity.artifact_key
    ) == seeded.record
    assert artifact_store.load_ref(
        seeded.ref,
        owner_type="interview_session",
        owner_key=state["session_id"],
        purpose="interview_question_memory",
        expected_identity=seeded.record.identity,
    ) == seeded.record
    assert index_store.activate_calls == 1
    assert index_store.get_historical(entry.artifact_ref) == entry


def test_indexed_target_with_missing_artifact_falls_back_without_provider():
    state = make_state()
    selection = _dynamic_selection(state)
    artifact_store = InMemoryContextArtifactStore()
    index_store = _CountingIndex()
    coordinator = _coordinator(
        agent=CompressorAgent(),
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=_runtime(source_tokens=3_000),
    )
    source = _closed_source(coordinator, state=state, selection=selection)
    entry = index_store.activate(
        _entry_for_source(
            coordinator,
            state=state,
            source=source,
            artifact_ref="context-artifact-ref:missing-memory",
            artifact_sha256="9" * 64,
            indexed_target=512,
        )
    )
    calls, _resolutions = _capture_runner(coordinator)

    result = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "deterministic"
    assert coordinator.compressor_agent.calls == 0
    assert len(calls) == 1
    request = calls[0]["request"]
    assert request.resolved_target_output_tokens == 512
    assert request.target_policy is _TARGET_POLICY
    assert index_store.activate_calls == 1
    assert index_store.get_historical(entry.artifact_ref) == entry


class _PostgresCursor:
    def __init__(self, connection):
        self.connection = connection
        self.one = None
        self.all = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        rendered = str(statement)
        self.connection.calls.append((rendered, params))
        self.one = None
        self.all = []
        if "SELECT artifact_ref" in rendered and "FOR UPDATE" in rendered:
            return
        if "INSERT INTO" in rendered or "UPDATE" in rendered:
            return
        if "WHERE artifact_ref=%s" in rendered:
            values = self.connection.historical.get(params[0])
            self.one = self.connection.row(values) if values else None
            return
        if "status='active'" in rendered:
            self.one = self.connection.row(self.connection.active)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all


class _PostgresConnection:
    def __init__(self):
        self.calls = []
        self.commits = 0
        self.active = None
        self.historical = {}

    def cursor(self):
        return _PostgresCursor(self)

    def commit(self):
        self.commits += 1

    @staticmethod
    def row(values):
        if values is None:
            return None
        columns = PostgresQuestionMemoryIndexStore._columns().split(",")
        return tuple(values[name] for name in columns)


class _PostgresProvider:
    def __init__(self):
        self.connection_object = _PostgresConnection()

    @contextmanager
    def connection(self):
        yield self.connection_object


def _postgres_store(monkeypatch):
    monkeypatch.setattr(
        PostgresQuestionMemoryIndexStore,
        "_ensure_schema",
        lambda _self: None,
    )
    provider = _PostgresProvider()
    return (
        PostgresQuestionMemoryIndexStore(
            connection_provider=provider,
            table_prefix="memory_target",
            schema_mode="migrate",
        ),
        provider.connection_object,
    )


def _postgres_values(*, target, **changes):
    entry = _model_entry(**changes)
    values = entry.model_dump(mode="python")
    values["resolved_target_output_tokens"] = target
    return values


def test_postgres_insert_appends_resolved_target_as_stable_last_parameter(
    monkeypatch,
):
    store, connection = _postgres_store(monkeypatch)
    entry = _injected_entry(target=512)

    activated = store.activate(entry)

    insert_calls = [
        call for call in connection.calls if "INSERT INTO" in call[0]
    ]
    assert len(insert_calls) == 1
    statement, params = insert_calls[0]
    assert "resolved_target_output_tokens" in statement
    assert len(params) == 20
    assert params[13:19] == (
        entry.source_manifest_sha256,
        entry.source_message_count,
        entry.source_max_sequence_no,
        entry.taxonomy_version,
        None,
        entry.created_at,
    )
    assert params[19] == 512
    assert json.loads(params[4]) == entry.focus_tags
    assert activated.resolved_target_output_tokens == 512
    assert connection.commits == 1


def test_postgres_columns_and_row_mapper_append_nullable_target():
    columns = PostgresQuestionMemoryIndexStore._columns().split(",")

    assert columns[-1] == "resolved_target_output_tokens"
    assert len(columns) == 23

    dynamic_values = _postgres_values(target=512)
    legacy_values = _postgres_values(target=None)
    dynamic = PostgresQuestionMemoryIndexStore._from_row(
        tuple(dynamic_values[name] for name in columns)
    )
    legacy = PostgresQuestionMemoryIndexStore._from_row(
        tuple(legacy_values[name] for name in columns)
    )
    assert dynamic.resolved_target_output_tokens == 512
    assert legacy.resolved_target_output_tokens is None


def test_postgres_active_and_historical_reads_round_trip_target(monkeypatch):
    store, connection = _postgres_store(monkeypatch)
    active = _postgres_values(target=512)
    historical = _postgres_values(
        target=512,
        status="superseded",
        superseded_at=_NOW + timedelta(seconds=1),
    )
    connection.active = active
    connection.historical[historical["artifact_ref"]] = historical

    loaded_active = store.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )
    loaded_historical = store.get_historical(historical["artifact_ref"])

    read_statements = [
        statement
        for statement, _params in connection.calls
        if "SELECT" in statement and "FOR UPDATE" not in statement
    ]
    assert len(read_statements) == 2
    assert all("resolved_target_output_tokens" in sql for sql in read_statements)
    assert loaded_active.resolved_target_output_tokens == 512
    assert loaded_historical.resolved_target_output_tokens == 512
    assert loaded_historical.status == "superseded"


def _identity_v0_material(*, target):
    return ContextArtifactIdentityMaterial(
        artifact_type="question_memory",
        privacy_scope_sha256="1" * 64,
        source_sha256="2" * 64,
        source_manifest_sha256="3" * 64,
        semantic_focus_sha256="4" * 64,
        compression_policy_version="question-memory-v1",
        prompt_contract_version="question-memory-prompt-v2",
        output_schema_version="question-memory-v1",
        compressor_provider="openai-compatible",
        compressor_model="gpt-4o",
        compressor_settings_sha256="5" * 64,
        target_output_tokens=target,
    )


@pytest.mark.parametrize(
    ("target", "expected_key"),
    (
        (
            256,
            "811ec60e7c92528539e09d9f27c79db3b50143c6ff1fced1f1f365a1e414908c",
        ),
        (
            2_000,
            "3eef1626133ab5e9dc40794445d3153ea2fa67dadf7e8c892555ec31429396f2",
        ),
    ),
)
def test_question_memory_identity_v0_goldens_use_only_canonical_target_field(
    target,
    expected_key,
):
    material = _identity_v0_material(target=target)
    payload = json.loads(canonical_identity_payload(material))

    assert set(payload) == {
        "artifact_type",
        "compression_policy_version",
        "compressor_model",
        "compressor_provider",
        "compressor_settings_sha256",
        "output_schema_version",
        "privacy_scope_sha256",
        "prompt_contract_version",
        "semantic_focus_sha256",
        "source_manifest_sha256",
        "source_sha256",
        "target_output_tokens",
    }
    assert "resolved_target_output_tokens" not in payload
    assert payload["target_output_tokens"] == target
    assert ContextArtifactIdentity.from_material(material).artifact_key == expected_key
