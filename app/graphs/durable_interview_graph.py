from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from threading import Event, Lock, Thread
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.examiner import fallback_followup
from app.graphs.durable_interview_state import DurableInterviewState
from app.services.agent_runtime import AgentExecutionContext
from app.services.interview_generation_store import ChunkCoalescer
from app.services.interview_generation_store import GenerationAlreadyCompleted
from app.services.followup_diagnostics import (
    FollowupDiagnosticInput,
    FollowupPolicySnapshot,
    diagnose_followup,
)
from app.services.knowledge_binding import resolve_evidence_by_ids
from app.services.runtime_work import (
    RuntimeFailure,
    classify_runtime_failure,
    retry_delay_seconds,
)
from app.services.workflow_thread_lock import GenerationLeaseLost
from app.services.context_budget import (
    FOLLOWUP_CONTEXT_POLICY,
    context_enforcement_enabled,
)
from app.services.context_selection import (
    ContextSelectionStats,
    build_interview_context,
)
from app.services.context_runtime import ContextRuntime, get_context_runtime


class GenerationLeaseHeartbeat:
    def __init__(
        self,
        *,
        generation_store,
        attempt,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        self.generation_store = generation_store
        self.attempt = attempt
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.interval_seconds = max(0.1, lease_seconds / 3)
        self._stop = Event()
        self._lost = Event()
        self._failure_lock = Lock()
        self._failure: Exception | None = None
        self._thread: Thread | None = None

    def __enter__(self):
        self._thread = Thread(
            target=self._run,
            name="generation-lease-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 2))

    def ensure_owned(self) -> None:
        if self._lost.is_set():
            error = GenerationLeaseLost(
                "generation attempt lease is no longer owned"
            )
            with self._failure_lock:
                failure = self._failure
            if failure is not None:
                raise error from failure
            raise error

    def _mark_lost(self, failure: Exception | None = None) -> None:
        with self._failure_lock:
            if self._lost.is_set():
                return
            self._failure = failure
            self._lost.set()

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.interval_seconds):
                if not self.generation_store.heartbeat_attempt(
                    self.attempt.generation_id,
                    self.attempt.attempt_number,
                    self.worker_id,
                    lease_token=self.attempt.lease_token,
                    fencing_version=self.attempt.fencing_version,
                    lease_seconds=self.lease_seconds,
                ):
                    self._mark_lost()
                    return
        except Exception as exc:
            self._mark_lost(exc)


@dataclass
class DurableInterviewGraphDependencies:
    workflow_store: Any
    project_state: Callable[[DurableInterviewState], dict] | None = None
    generation_store: Any | None = None
    decision_service: Any | None = None
    examiner: Any | None = None
    knowledge_repository: Any | None = None
    report_job_queue: Any | None = None
    context_builder: Callable[[DurableInterviewState], list[dict[str, str]]] | None = None
    context_runtime: ContextRuntime | None = None
    context_artifact_coordinator: Any | None = None
    evidence_artifact_coordinator: Any | None = None
    question_memory_coordinator: Any | None = None
    principal_memory_shadow: Any | None = None
    principal_memory_consumer: Any | None = None
    coalescer_factory: Callable[[], ChunkCoalescer] = ChunkCoalescer
    worker_id: str = "durable-interview-worker"
    generation_lease_seconds: int = 60
    failure_classifier: Callable[[Exception], RuntimeFailure] = (
        classify_runtime_failure
    )
    generation_heartbeat_factory: Callable[..., Any] = (
        GenerationLeaseHeartbeat
    )


def initialize_session(state: DurableInterviewState) -> dict:
    return {}


def project_state_node(state, deps) -> dict:
    if deps.project_state is not None:
        return deps.project_state(state)
    projection = deps.workflow_store.project_state(state)
    updates = {
        "state_version": projection.state_version,
        "command_outcome": None,
        "generation_outcome": None,
        "generated_text": None,
        "retry_resume_attempt": None,
        "retry_validation": None,
    }
    if state["command_outcome"] == "completed":
        updates["active_command_id"] = None
        updates["command_type"] = None
        updates.update(
            {
                "active_decision_id": None,
                "decision_action": None,
                "decision_reason_code": None,
                "decision_gap_type": None,
                "decision_gap_summary": None,
                "decision_outcome": None,
            }
        )
    if state.get("workflow_engine") == "langgraph-v2" and state.get(
        "active_context_artifact_ref"
    ):
        updates.update(
            {
                "active_context_artifact_ref": None,
                "active_context_artifact_sha256": None,
                "active_context_artifact_type": None,
                "active_context_policy_version": None,
                "context_route": None,
            }
        )
    return updates


def wait_for_answer(state: DurableInterviewState) -> dict:
    payload = interrupt(
        {
            "kind": "answer_command",
            "session_id": state["session_id"],
            "state_version": state["state_version"],
        }
    )
    return {"active_command_id": payload["command_id"]}


def validate_command(state, deps) -> dict:
    command = deps.workflow_store.get_command(
        state["session_id"], state["active_command_id"]
    )
    if command.status == "applied":
        return {"active_command_id": None, "command_outcome": "duplicate"}
    if command.status == "conflict":
        return {"active_command_id": None, "command_outcome": "conflict"}
    if command.expected_version != state["state_version"]:
        deps.workflow_store.mark_command_conflict(
            state["session_id"],
            command.command_id,
            state["state_version"],
        )
        return {"active_command_id": None, "command_outcome": "conflict"}
    return {
        "command_type": command.command_type,
        "command_outcome": "accepted",
    }


def append_candidate_answer(state, deps) -> dict:
    command = deps.workflow_store.get_command(
        state["session_id"], state["active_command_id"]
    )
    questions = state["plan_snapshot"]["questions"]
    question = questions[state["current_index"]]
    return {
        "messages": [
            *state["messages"],
            {
                "role": "candidate",
                "content": command.answer_text.strip(),
                "question_id": question["id"],
            },
        ]
    }


def apply_skip(state) -> dict:
    questions = state["plan_snapshot"]["questions"]
    question = questions[state["current_index"]]
    next_index = state["current_index"] + 1
    if next_index >= len(questions):
        return {
            "skipped_question_ids": [
                *state["skipped_question_ids"],
                question["id"],
            ],
            "interview_status": "finished",
            "current_index": len(questions),
            "current_followup_count": 0,
            "closed_gap_ids": [],
            "command_outcome": "completed",
        }
    next_question = questions[next_index]
    return {
        "skipped_question_ids": [
            *state["skipped_question_ids"],
            question["id"],
        ],
        "current_index": next_index,
        "current_followup_count": 0,
        "closed_gap_ids": [],
        "messages": [
            *state["messages"],
            {
                "role": "interviewer",
                "content": next_question["prompt"],
                "question_id": next_question["id"],
            },
        ],
        "command_outcome": "completed",
    }


def apply_finish(state) -> dict:
    return {
        "interview_status": "finished",
        "current_index": len(state["plan_snapshot"]["questions"]),
        "command_outcome": "completed",
    }


def prepare_or_load_decision(state, deps) -> dict:
    if deps.decision_service is None:
        raise RuntimeError("durable interview Decision service is unavailable")
    request = _build_followup_diagnostic_input(state)
    diagnostics = diagnose_followup(request)
    record = deps.decision_service.store.prepare(
        session_id=state["session_id"],
        source_command_id=state["active_command_id"],
        input_sha256=diagnostics.input_sha256,
    )
    updates = {
        "active_decision_id": record.decision_id,
        "followup_policy_version": request.policy.policy_version,
        "current_followup_count": request.followup_count,
        "decision_outcome": "pending",
    }
    if record.status == "completed":
        updates.update(_decision_state_updates(record.final_decision))
    return updates


def execute_decision_attempt(state, deps) -> dict:
    if deps.decision_service is None:
        raise RuntimeError("durable interview Decision service is unavailable")
    request = _build_followup_diagnostic_input(state)
    result = deps.decision_service.execute(
        request,
        source_command_id=state["active_command_id"],
        worker_id=deps.worker_id,
    )
    if result.decision_id != state["active_decision_id"]:
        raise RuntimeError("prepared Decision identity changed during execution")
    if result.status != "completed" or result.decision is None:
        # A concurrent worker still owns the durable Lease.  Propagating this
        # condition lets the command delivery retry after Lease expiry without
        # advancing the graph or creating a second Decision.
        raise RuntimeError("durable interview Decision attempt is still leased")
    return _decision_state_updates(result.decision)


def prepare_generation(state, deps) -> dict:
    if state.get("decision_action") != "follow_up" or not state.get(
        "active_decision_id"
    ):
        raise RuntimeError("follow-up generation requires a completed Decision")
    question = _current_question(state)
    generation = deps.generation_store.prepare_generation(
        session_id=state["session_id"],
        source_command_id=state["active_command_id"],
        question_id=question["id"],
        source_decision_id=state["active_decision_id"],
    )
    return {
        "generation_id": generation.generation_id,
        "generation_attempt": generation.active_attempt,
    }


def generate_followup(state, deps) -> dict:
    coalescer = deps.coalescer_factory()
    attempt = None
    try:
        attempt = deps.generation_store.start_or_reclaim_attempt(
            state["generation_id"],
            state["generation_attempt"],
            worker_id=deps.worker_id,
            lease_seconds=deps.generation_lease_seconds,
        )
    except GenerationAlreadyCompleted:
        generation = deps.generation_store.get_by_id(state["generation_id"])
        return {
            "generation_outcome": "completed",
            "generated_text": generation.final_text or "",
        }
    chunks: list[str] = []
    sequence = 0
    is_v2 = state.get("workflow_engine") == "langgraph-v2"
    context = None
    if not is_v2:
        context = (
            deps.context_builder(state)
            if deps.context_builder is not None
            else _build_examiner_context(
                state,
                deps.knowledge_repository,
                deps.context_runtime,
            )
        )
    artifact_context = None
    parent_ownership = None
    try:
        heartbeat_context = deps.generation_heartbeat_factory(
            generation_store=deps.generation_store,
            attempt=attempt,
            worker_id=deps.worker_id,
            lease_seconds=deps.generation_lease_seconds,
        )
        with heartbeat_context as heartbeat:
            if is_v2:
                from app.services.interview_context_artifacts import (
                    GenerationAttemptOwnership,
                )

                parent_ownership = GenerationAttemptOwnership(
                    deps.generation_store,
                    attempt,
                    worker_id=deps.worker_id,
                )
                if deps.context_builder is not None:
                    deterministic_context = deps.context_builder(state)
                    selection_stats = None
                else:
                    (
                        deterministic_context,
                        selection_stats,
                    ) = _build_examiner_context_selection(
                        state,
                        deps.knowledge_repository,
                        deps.context_runtime,
                    )
                question_memory_enabled = (
                    state.get("memory_policy_version") == "question-memory-v1"
                    and deps.question_memory_coordinator is not None
                )
                conversation_artifact_enabled = (
                    state.get("memory_policy_version")
                    == "question-conversation-v1"
                    and deps.context_artifact_coordinator is not None
                )
                artifact_context = (
                    deps.question_memory_coordinator.build_context(
                        state=state,
                        deterministic_context=deterministic_context,
                        parent_ownership=parent_ownership,
                    )
                    if question_memory_enabled
                    else deps.context_artifact_coordinator.build_context(
                        state=state,
                        deterministic_context=deterministic_context,
                        parent_ownership=parent_ownership,
                        selection_stats=selection_stats,
                    )
                    if conversation_artifact_enabled
                    else None
                )
                context = (
                    artifact_context.context_messages
                    if artifact_context is not None
                    else deterministic_context
                )
                if deps.evidence_artifact_coordinator is not None:
                    evidence_context = (
                        deps.evidence_artifact_coordinator.build_interview_context(
                            state=state,
                            context_messages=context,
                            parent_ownership=parent_ownership,
                            worker_id=deps.worker_id,
                            selection_stats=selection_stats,
                        )
                    )
                    context = evidence_context.context_messages
                    if (
                        evidence_context.artifact_ref is not None
                        or evidence_context.route == "artifact_fallback"
                    ):
                        artifact_context = evidence_context
                parent_ownership.ensure_owned()
            question = _current_question(state)
            focus_tokens = {
                token.strip().casefold()
                for token in str(question.get("focus", "")).replace(",", " ").split()
                if token.strip()
            }
            role_tags = set(state.get("job_tags", []))
            consume_prepared = None
            consume_base_context = [dict(message) for message in (context or [])]
            if deps.principal_memory_consumer is not None:
                try:
                    consume_prepared = deps.principal_memory_consumer.prepare(
                        provider_context=[
                            dict(message) for message in consume_base_context
                        ],
                        current_tags=focus_tokens,
                        role_tags=role_tags,
                        now=datetime.now(timezone.utc),
                        session_id=state["session_id"],
                    )
                except Exception:
                    consume_prepared = None
                    context = consume_base_context
            if (
                deps.principal_memory_shadow is not None
                and deps.principal_memory_shadow.is_enabled
            ):
                deps.principal_memory_shadow.observe(
                    provider_context=context or [],
                    current_tags=focus_tokens,
                    role_tags=role_tags,
                    now=datetime.now(timezone.utc),
                    session_id=state["session_id"],
                )
            if consume_prepared is not None:
                try:
                    consume_result = deps.principal_memory_consumer.finalize(
                        consume_prepared,
                        now=datetime.now(timezone.utc),
                    )
                    context = consume_result.provider_context
                    try:
                        from app.services.memory_metrics import (
                            publish_principal_local_consume_metric,
                        )

                        publish_principal_local_consume_metric(
                            outcome=consume_result.outcome,
                            reason=consume_result.reason,
                            selected_count=consume_result.selected_count,
                            estimated_input_tokens=(
                                consume_result.estimated_tokens
                            ),
                        )
                    except Exception:
                        # Telemetry is never allowed to change interview output.
                        pass
                except Exception:
                    context = consume_base_context
            for chunk in deps.examiner.stream_followup_attempt(
                context=context or [],
                execution_context=AgentExecutionContext(
                    correlation_id=state["session_id"],
                    causation_id=state["active_command_id"],
                    agent="examiner",
                    operation="generate_followup",
                    phase="interview",
                    session_id=state["session_id"],
                    question_id=_current_question(state)["id"],
                    state_version=state["state_version"],
                    command_id=state["active_command_id"],
                    attempt_number=attempt.attempt_number,
                ),
            ):
                chunks.append(chunk)
                persisted = coalescer.add(chunk)
                if not persisted:
                    continue
                heartbeat.ensure_owned()
                sequence += 1
                deps.generation_store.append_chunk(
                    attempt.generation_id,
                    attempt.attempt_number,
                    sequence,
                    persisted,
                    lease_token=attempt.lease_token,
                    fencing_version=attempt.fencing_version,
                )
            final_chunk = coalescer.flush()
            if final_chunk:
                heartbeat.ensure_owned()
                sequence += 1
                deps.generation_store.append_chunk(
                    attempt.generation_id,
                    attempt.attempt_number,
                    sequence,
                    final_chunk,
                    lease_token=attempt.lease_token,
                    fencing_version=attempt.fencing_version,
                )
            final_text = "".join(chunks).strip()
            heartbeat.ensure_owned()
            if parent_ownership is not None:
                parent_ownership.ensure_owned()
            deps.generation_store.complete_attempt(
                attempt.generation_id,
                attempt.attempt_number,
                final_text,
                lease_token=attempt.lease_token,
                fencing_version=attempt.fencing_version,
            )
        if parent_ownership is not None:
            parent_ownership.ensure_owned()
        artifact_updates = (
            {
                "active_context_artifact_ref": artifact_context.artifact_ref,
                "active_context_artifact_sha256": artifact_context.artifact_sha256,
                "active_context_artifact_type": artifact_context.artifact_type,
                "active_context_policy_version": artifact_context.policy_version,
                "context_route": artifact_context.route,
            }
            if artifact_context is not None
            else {}
        )
        return {
            "generation_outcome": "completed",
            "generated_text": final_text,
            **artifact_updates,
        }
    except Exception as exc:
        failure = deps.failure_classifier(exc)
        # Unknown exceptions include programming defects and injected/process
        # loss after a durable write. Treating them as provider failures would
        # both hide the original interruption and may attempt to fail an
        # already-completed generation. Let LangGraph recovery replay the
        # checkpoint boundary instead.
        if failure.code in {"unexpected_error", "generation_lease_lost"}:
            raise
        code = failure.code
        if attempt is not None:
            deps.generation_store.fail_attempt(
                attempt.generation_id,
                attempt.attempt_number,
                code,
                lease_token=attempt.lease_token,
                fencing_version=attempt.fencing_version,
            )
        return {
            "generation_outcome": (
                "retryable" if failure.retryable else "terminal"
            ),
            "last_error_code": code,
        }


def enqueue_retry(state, deps) -> dict:
    next_attempt = state["generation_attempt"] + 1
    scheduled = deps.workflow_store.enqueue_retry(
        session_id=state["session_id"],
        generation_id=state["generation_id"],
        next_attempt_number=next_attempt,
        delay_seconds=retry_delay_seconds(state["generation_attempt"]),
    )
    return {
        "expected_retry_attempt": next_attempt,
        "next_retry_at": scheduled.available_at.isoformat(),
    }


def wait_for_retry(state) -> dict:
    payload = interrupt(
        {
            "kind": "retry_timer",
            "generation_id": state["generation_id"],
            "next_attempt_number": state["generation_attempt"] + 1,
        }
    )
    return {"retry_resume_attempt": payload["next_attempt_number"]}


def validate_retry(state) -> dict:
    if state["retry_resume_attempt"] != state["expected_retry_attempt"]:
        return {
            "retry_resume_attempt": None,
            "retry_validation": "stale",
        }
    return {
        "generation_attempt": state["expected_retry_attempt"],
        "expected_retry_attempt": None,
        "retry_resume_attempt": None,
        "retry_validation": "accepted",
        "next_retry_at": None,
    }


def prepare_retry(state) -> dict:
    return {"retry_validation": None, "generation_outcome": None}


def commit_interviewer_message(state) -> dict:
    question = _current_question(state)
    return {
        "messages": [
            *state["messages"],
            {
                "role": "interviewer",
                "content": state["generated_text"],
                "question_id": question["id"],
            },
        ],
        "generation_id": None,
        "current_followup_count": state["current_followup_count"] + 1,
        "expected_retry_attempt": None,
        "next_retry_at": None,
        "command_outcome": "completed",
    }


def fallback_followup_node(state) -> dict:
    question = _current_question(state)
    return {"generated_text": fallback_followup(question["focus"])}


def commit_next_question(state) -> dict:
    questions = state["plan_snapshot"]["questions"]
    next_index = state["current_index"] + 1
    if next_index >= len(questions):
        return {
            "interview_status": "finished",
            "current_index": len(questions),
            "current_followup_count": 0,
            "closed_gap_ids": [],
            "command_outcome": "completed",
        }
    question = questions[next_index]
    return {
        "current_index": next_index,
        "current_followup_count": 0,
        "closed_gap_ids": [],
        "messages": [
            *state["messages"],
            {
                "role": "interviewer",
                "content": question["prompt"],
                "question_id": question["id"],
            },
        ],
        "command_outcome": "completed",
    }


def emit_report_event(state, deps) -> dict:
    if deps.report_job_queue is not None:
        deps.report_job_queue.enqueue_report_request(state["session_id"])
    return {}


def route_validated_command(state) -> str:
    if state["command_outcome"] in {"duplicate", "conflict"}:
        return "wait_for_answer"
    if state["command_type"] == "answer":
        return "append_candidate_answer"
    if state["command_type"] == "skip":
        return "apply_skip"
    return "apply_finish"


def route_after_projection(state) -> str:
    if state["interview_status"] == "finished":
        return "emit_report_event"
    if state["generation_id"] is not None:
        return "generate_followup"
    return "wait_for_answer"


def route_decision(state) -> str:
    if state.get("decision_outcome") != "completed":
        raise RuntimeError("cannot route an incomplete durable Decision")
    if state.get("decision_action") == "follow_up":
        return "prepare_generation"
    if state.get("decision_action") == "next_question":
        return "commit_next_question"
    raise RuntimeError("persisted durable Decision has no valid action")


def route_generation(state) -> str:
    if state["generation_outcome"] == "completed":
        return "commit_interviewer_message"
    if (
        state["generation_outcome"] == "retryable"
        and state["generation_attempt"] < 3
    ):
        return "enqueue_retry"
    return "fallback_followup"


def route_validated_retry(state) -> str:
    if state["retry_validation"] == "accepted":
        return "prepare_retry"
    return "wait_for_retry"


def _current_question(state) -> dict:
    return state["plan_snapshot"]["questions"][state["current_index"]]


def _build_followup_diagnostic_input(state) -> FollowupDiagnosticInput:
    question = _current_question(state)
    question_messages = [
        message
        for message in state["messages"]
        if message.get("question_id") == question["id"]
    ]
    candidate_answers = [
        message["content"]
        for message in question_messages
        if message["role"] == "candidate"
    ]
    interviewer_messages = [
        message["content"]
        for message in question_messages
        if message["role"] == "interviewer"
    ]
    asked_followups = interviewer_messages[1:]
    configuration = state.get("configuration_snapshot") or {}
    policy_version = configuration.get("followup_policy_version", "fixed_v1")
    max_followups = 1 if policy_version == "fixed_v1" else 2
    policy = FollowupPolicySnapshot(
        policy_version=policy_version,
        max_followups=max_followups,
    )
    return FollowupDiagnosticInput(
        session_status=state["interview_status"],
        session_id=state["session_id"],
        question_id=question["id"],
        question_text=question["prompt"],
        focus=question.get("focus", ""),
        candidate_answers=candidate_answers,
        asked_followups=asked_followups,
        followup_count=len(asked_followups),
        closed_gap_ids=list(state.get("closed_gap_ids") or []),
        # Bound plan evidence may include resume or job-description material;
        # do not relabel it as public knowledge for Decision input.
        public_knowledge_summary="",
        policy=policy,
    )


def _decision_state_updates(decision) -> dict:
    if decision is None:
        raise RuntimeError("completed Decision is missing its final payload")
    return {
        "decision_action": decision.action,
        "decision_reason_code": decision.reason_code,
        "decision_gap_type": decision.gap_type,
        "decision_gap_summary": decision.gap_summary,
        "followup_policy_version": decision.policy_version,
        "closed_gap_ids": list(decision.closed_gap_ids),
        "decision_outcome": "completed",
    }


def _recent_conversation_messages(
    state,
    context_runtime: ContextRuntime | None = None,
) -> list[dict[str, str]]:
    return _recent_conversation_selection(state, context_runtime)[0]


def _recent_conversation_selection(
    state,
    context_runtime: ContextRuntime | None = None,
) -> tuple[list[dict[str, str]], ContextSelectionStats]:
    if not context_enforcement_enabled(FOLLOWUP_CONTEXT_POLICY.operation):
        selected = [
            {"role": message["role"], "content": message["content"]}
            for message in state["messages"][-4:]
        ]
        return selected, ContextSelectionStats(
            source_message_count=len(state["messages"]),
            selected_message_count=len(selected),
            dropped_message_count=max(0, len(state["messages"]) - len(selected)),
        )
    question_id = _current_question(state)["id"]
    runtime = context_runtime or get_context_runtime()
    estimator = runtime.estimator_resolution.estimator
    model = runtime.model_profile.model
    budget = runtime.budget_resolver.resolve(
        profile=runtime.model_profile,
        policy=FOLLOWUP_CONTEXT_POLICY,
    )
    selection_budget = runtime.budget_resolver.resolve_selection_budget(
        budget=budget,
        policy=FOLLOWUP_CONTEXT_POLICY,
    )
    selected, stats = build_interview_context(
        state["messages"],
        current_question_id=question_id,
        policy=FOLLOWUP_CONTEXT_POLICY,
        selection_budget=selection_budget,
        estimator=estimator,
        model=model,
    )
    return selected, stats


def _build_examiner_context(
    state,
    repository,
    context_runtime: ContextRuntime | None = None,
) -> list[dict[str, str]]:
    return _build_examiner_context_selection(
        state,
        repository,
        context_runtime,
    )[0]


def _build_examiner_context_selection(
    state,
    repository,
    context_runtime: ContextRuntime | None = None,
) -> tuple[list[dict[str, str]], ContextSelectionStats]:
    question = _current_question(state)
    evidence_messages = []
    if repository is None:
        return _recent_conversation_selection(state, context_runtime)
    resolution = resolve_evidence_by_ids(
        repository,
        evidence_ids=question.get("evidence_ids", []),
        expected_hashes=question.get("evidence_sha256", {}),
        expected_manifest_sha256=state["plan_snapshot"].get(
            "corpus_manifest_sha256"
        ),
    )
    if resolution.retrieval_path == "bound_evidence_ids":
        evidence_messages = resolution.messages
    if not context_enforcement_enabled(FOLLOWUP_CONTEXT_POLICY.operation):
        recent, stats = _recent_conversation_selection(state, context_runtime)
        return [*recent, *evidence_messages], ContextSelectionStats(
            source_message_count=stats.source_message_count,
            selected_message_count=stats.selected_message_count,
            dropped_message_count=stats.dropped_message_count,
            truncated_message_count=stats.truncated_message_count,
            source_evidence_count=len(evidence_messages),
            selected_evidence_count=len(evidence_messages),
        )
    runtime = context_runtime or get_context_runtime()
    estimator = runtime.estimator_resolution.estimator
    model = runtime.model_profile.model
    budget = runtime.budget_resolver.resolve(
        profile=runtime.model_profile,
        policy=FOLLOWUP_CONTEXT_POLICY,
    )
    selection_budget = runtime.budget_resolver.resolve_selection_budget(
        budget=budget,
        policy=FOLLOWUP_CONTEXT_POLICY,
    )
    context, stats = build_interview_context(
        state["messages"],
        current_question_id=question["id"],
        evidence_messages=evidence_messages,
        policy=FOLLOWUP_CONTEXT_POLICY,
        selection_budget=selection_budget,
        estimator=estimator,
        model=model,
    )
    return context, stats


def build_durable_interview_graph(
    deps: DurableInterviewGraphDependencies,
    *,
    checkpointer,
):
    return build_durable_interview_graph_for_schema(
        deps,
        state_schema=DurableInterviewState,
        checkpointer=checkpointer,
    )


def build_durable_interview_graph_for_schema(
    deps: DurableInterviewGraphDependencies,
    *,
    state_schema,
    checkpointer,
):
    builder = StateGraph(state_schema)
    builder.add_node("initialize_session", initialize_session)
    builder.add_node("project_state", partial(project_state_node, deps=deps))
    builder.add_node("wait_for_answer", wait_for_answer)
    builder.add_node(
        "validate_command", partial(validate_command, deps=deps)
    )
    builder.add_node(
        "append_candidate_answer",
        partial(append_candidate_answer, deps=deps),
    )
    builder.add_node("apply_skip", apply_skip)
    builder.add_node("apply_finish", apply_finish)
    builder.add_node(
        "prepare_or_load_decision",
        partial(prepare_or_load_decision, deps=deps),
    )
    builder.add_node(
        "execute_decision_attempt",
        partial(execute_decision_attempt, deps=deps),
    )
    builder.add_node(
        "prepare_generation", partial(prepare_generation, deps=deps)
    )
    builder.add_node(
        "generate_followup", partial(generate_followup, deps=deps)
    )
    builder.add_node("enqueue_retry", partial(enqueue_retry, deps=deps))
    builder.add_node("wait_for_retry", wait_for_retry)
    builder.add_node("validate_retry", validate_retry)
    builder.add_node("prepare_retry", prepare_retry)
    builder.add_node(
        "commit_interviewer_message", commit_interviewer_message
    )
    builder.add_node("fallback_followup", fallback_followup_node)
    builder.add_node("commit_next_question", commit_next_question)
    builder.add_node(
        "emit_report_event", partial(emit_report_event, deps=deps)
    )
    builder.add_edge(START, "initialize_session")
    builder.add_edge("initialize_session", "project_state")
    builder.add_conditional_edges("project_state", route_after_projection)
    builder.add_edge("wait_for_answer", "validate_command")
    builder.add_conditional_edges(
        "validate_command", route_validated_command
    )
    builder.add_edge("append_candidate_answer", "prepare_or_load_decision")
    builder.add_edge("prepare_or_load_decision", "execute_decision_attempt")
    builder.add_conditional_edges("execute_decision_attempt", route_decision)
    builder.add_edge("prepare_generation", "project_state")
    builder.add_conditional_edges("generate_followup", route_generation)
    builder.add_edge(
        "commit_interviewer_message", "project_state"
    )
    builder.add_edge("enqueue_retry", "wait_for_retry")
    builder.add_edge("wait_for_retry", "validate_retry")
    builder.add_conditional_edges(
        "validate_retry", route_validated_retry
    )
    builder.add_edge("prepare_retry", "generate_followup")
    builder.add_edge("fallback_followup", "commit_interviewer_message")
    builder.add_edge("commit_next_question", "project_state")
    builder.add_edge("apply_skip", "project_state")
    builder.add_edge("apply_finish", "project_state")
    builder.add_edge("emit_report_event", END)
    return builder.compile(checkpointer=checkpointer)
