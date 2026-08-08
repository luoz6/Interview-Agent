from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from app.graphs.durable_interview_state import make_durable_initial_state
from app.graphs.durable_interview_state_v2 import make_durable_initial_state_v2
from app.graphs.interview_state import (
    choose_workflow_engine,
    default_memory_policy_for_engine,
    is_durable_interview_version,
)
from app.services.interview_event_stream import InterviewEventStreamService
from app.services.runtime_events import AcceptedInterviewCommand
from app.services.session_plan_binding import session_plan_binding_from_state
from app.services.workflow_thread_lock import (
    NoopWorkflowThreadLock,
    interview_thread_identity,
)


PENDING_ACTION_BY_NODE = {
    "wait_for_answer": "waiting_for_answer",
    "validate_command": "validating_answer",
    "append_candidate_answer": "accepting_answer",
    "guard_after_answer": "analyzing_answer",
    "prepare_or_load_decision": "analyzing_answer",
    "execute_decision_attempt": "analyzing_answer",
    "guard_after_decision": "analyzing_answer",
    "guard_before_generation": "organizing_followup",
    "prepare_generation": "organizing_followup",
    "generate_followup": "generating_followup",
    "wait_for_retry": "waiting_for_retry",
    "validate_retry": "recovering_followup",
    "prepare_retry": "recovering_followup",
    "fallback_followup": "organizing_followup",
    "terminate_followup_generation": "committing_state",
    "commit_next_question": "committing_state",
    "project_state": "committing_state",
}


class InterviewWorkflowService:
    def __init__(
        self,
        *,
        legacy_store,
        workflow_store,
        generation_store,
        graph_registry,
        runtime_store: str,
        runtime_enabled: bool,
        rollout_percent: int,
        default_graph_version: str,
        thread_lock=None,
        memory_policy_resolver=None,
    ) -> None:
        self.legacy_store = legacy_store
        self.workflow_store = workflow_store
        self.generation_store = generation_store
        self.graph_registry = graph_registry
        self.runtime_store = runtime_store
        self.runtime_enabled = runtime_enabled
        self.rollout_percent = rollout_percent
        self.default_graph_version = default_graph_version
        self.memory_policy_resolver = (
            memory_policy_resolver or default_memory_policy_for_engine
        )
        self.thread_lock = thread_lock or NoopWorkflowThreadLock()
        self.event_stream = InterviewEventStreamService(
            workflow_store, generation_store
        )

    def start(
        self,
        plan,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        plan_binding=None,
        session_id: str | None = None,
    ):
        session_id = session_id or str(uuid4())
        engine = choose_workflow_engine(
            session_id,
            runtime_store=self.runtime_store,
            runtime_enabled=self.runtime_enabled,
            rollout_percent=self.rollout_percent,
            durable_version=self.default_graph_version,
        )
        memory_policy_version = self.memory_policy_resolver(engine)
        if engine == "legacy":
            return self.legacy_store.start(
                plan,
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                session_id=session_id,
                memory_policy_version=memory_policy_version,
                plan_binding=plan_binding,
            )
        self.legacy_store.insert_durable_session_shell(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            graph_version=self.default_graph_version,
            memory_policy_version=memory_policy_version,
            plan_binding=plan_binding,
        )
        self.ensure_interview_bootstrapped(session_id, plan=plan)
        return self.legacy_store._to_turn(
            self.legacy_store.get(session_id), follow_up=None
        )

    def graph_for_version(self, version: str):
        return self.graph_registry.get(version)

    def is_durable_session(self, session_id: str) -> bool:
        try:
            state = self.legacy_store.get(session_id)
        except ValueError:
            return False
        return (
            is_durable_interview_version(state.get("workflow_engine"))
            and bool(state.get("graph_schema_version"))
        )

    def graph_for_session(self, session_id: str):
        state = self.legacy_store.get(session_id)
        version = state.get("graph_schema_version")
        if not is_durable_interview_version(state.get("workflow_engine")) or not version:
            raise ValueError("session is not a durable graph session")
        return self.graph_for_version(version)

    def _invoke_locked(
        self,
        session_id: str,
        graph_input,
        *,
        reason: str,
        graph=None,
        validate_snapshot=None,
    ):
        with self.thread_lock.hold(
            interview_thread_identity(session_id),
            workflow_type="interview",
        ) as ownership:
            active_graph = graph or self.graph_for_session(session_id)
            config = {"configurable": {"thread_id": session_id}}
            if validate_snapshot is not None:
                snapshot = active_graph.get_state(config)
                if not validate_snapshot(snapshot):
                    return None
            result = active_graph.invoke(graph_input, config=config)
            if ownership is not None:
                ownership.ensure_owned()
            return result

    def ensure_interview_bootstrapped(self, session_id: str, *, plan=None):
        with self.thread_lock.hold(
            interview_thread_identity(session_id),
            workflow_type="interview",
        ) as ownership:
            public_state = self.legacy_store.get(session_id)
            if not is_durable_interview_version(public_state.get("workflow_engine")):
                raise ValueError("session is not a durable graph session")
            version = public_state.get("graph_schema_version")
            if not version:
                raise ValueError("durable graph version is missing")
            resolved_plan = plan or public_state["plan"]
            plan_binding = session_plan_binding_from_state(public_state)
            initial_state = (
                make_durable_initial_state_v2(
                    session_id,
                    resolved_plan,
                    memory_policy_version=public_state[
                        "memory_policy_version"
                    ],
                    plan_binding=plan_binding,
                )
                if version == "langgraph-v2"
                else make_durable_initial_state(
                    session_id,
                    resolved_plan,
                    plan_binding=plan_binding,
                )
            )
            canonical = json.dumps(
                {
                    "graph_schema_version": version,
                    "initial_state": initial_state,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            graph = self.graph_for_version(version)
            config = {"configurable": {"thread_id": session_id}}
            snapshot = graph.get_state(config)
            self.workflow_store.register_bootstrap_input(
                session_id=session_id,
                graph_schema_version=version,
                bootstrap_input_sha256=digest,
                require_unstarted=not bool(snapshot.values),
            )
            if snapshot.values:
                if ownership is not None:
                    ownership.ensure_owned()
                return snapshot.values
            result = graph.invoke(initial_state, config=config)
            if ownership is not None:
                ownership.ensure_owned()
            return result

    def resume_command(self, session_id: str, command_id: str) -> str:
        from langgraph.types import Command

        self._invoke_locked(
            session_id,
            Command(
                resume={
                    "kind": "answer_command",
                    "command_id": command_id,
                }
            ),
            reason="command_resume",
        )
        return "completed"

    def resume_generation_retry(
        self,
        session_id: str,
        *,
        generation_id: str,
        next_attempt_number: int,
    ) -> str:
        from langgraph.types import Command

        def is_current_retry(snapshot) -> bool:
            state = snapshot.values
            return (
                snapshot.next == ("wait_for_retry",)
                and state.get("generation_id") == generation_id
                and state.get("expected_retry_attempt")
                == next_attempt_number
            )

        result = self._invoke_locked(
            session_id,
            Command(
                resume={
                    "kind": "retry_timer",
                    "generation_id": generation_id,
                    "next_attempt_number": next_attempt_number,
                }
            ),
            reason="retry_resume",
            validate_snapshot=is_current_retry,
        )
        return "completed" if result is not None else "discarded_stale_retry"

    def submit_command(
        self,
        session_id: str,
        *,
        command_type: str,
        expected_version: int | None,
        command_id: str | None,
        answer_text: str | None = None,
    ):
        state = self.legacy_store.get(session_id)
        if not is_durable_interview_version(state.get("workflow_engine")):
            kwargs = {
                "expected_version": expected_version,
                "command_id": command_id,
            }
            if command_type == "answer":
                return self.legacy_store.submit_answer(
                    session_id, answer_text, **kwargs
                )
            return getattr(self.legacy_store, command_type)(
                session_id, **kwargs
            )
        if state.get("status") == "finished":
            existing = (
                self.workflow_store.get_command_or_none(
                    session_id, command_id
                )
                if command_id
                else None
            )
            if existing is None:
                raise ValueError("interview is already finished")
        if expected_version is None:
            raise ValueError("expected_version is required")
        command_id = command_id or f"command-{uuid4().hex}"
        record = self.workflow_store.enqueue_command(
            session_id=session_id,
            command_id=command_id,
            command_type=command_type,
            expected_version=expected_version,
            answer_text=answer_text,
        )
        return AcceptedInterviewCommand(
            session_id=session_id,
            command_id=record.command_id,
            workflow_engine=state["workflow_engine"],
            stream_url=(
                f"/api/interviews/{session_id}/commands/"
                f"{record.command_id}/stream"
            ),
        )

    def snapshot(self, session_id: str) -> dict:
        from app.services.session import interview_assistance_metadata

        snapshot = self.legacy_store.snapshot(session_id)
        if not is_durable_interview_version(snapshot.get("workflow_engine")):
            state = self.legacy_store.get(session_id)
            if not is_durable_interview_version(state.get("workflow_engine")):
                return snapshot
        graph_state = self.graph_for_session(session_id).get_state(
            {"configurable": {"thread_id": session_id}}
        )
        next_node = graph_state.next[0] if graph_state.next else None
        snapshot["pending_action"] = PENDING_ACTION_BY_NODE.get(next_node)
        snapshot["workflow_engine"] = graph_state.values.get(
            "workflow_engine",
            snapshot.get("workflow_engine"),
        )
        values = graph_state.values
        snapshot.update(
            interview_assistance_metadata(
                self.legacy_store.get(session_id),
                context_route=values.get("context_route"),
                policy_version=values.get("memory_policy_version"),
            )
        )
        active_command_id = values.get("active_command_id")
        generation_id = values.get("generation_id")
        snapshot["active_command_id"] = active_command_id
        snapshot["active_generation_id"] = generation_id
        snapshot["active_attempt_number"] = values.get(
            "generation_attempt"
        )
        policy_version = values.get(
            "followup_policy_version",
            snapshot.get("followup_policy_version", "fixed_v1"),
        )
        snapshot["followup_policy_version"] = policy_version
        snapshot["current_followup_count"] = max(
            0, min(2, int(values.get("current_followup_count") or 0))
        )
        snapshot["followup_ui_state"] = _followup_ui_state(
            values,
            next_node=next_node,
            policy_version=policy_version,
        )
        if active_command_id:
            snapshot["active_stream_url"] = (
                f"/api/interviews/{session_id}/commands/"
                f"{active_command_id}/stream"
            )
        return snapshot

    def purge_session(self, session_id: str) -> dict[str, int]:
        from app.services.runtime import (
            get_langgraph_checkpointer_runtime,
        )

        checkpointer = get_langgraph_checkpointer_runtime()
        if checkpointer is not None:
            checkpointer.delete_thread(session_id)
        return {
            "workflow_control_rows": self.workflow_store.delete_session_control_rows(
                session_id
            ),
            "generation_rows": self.generation_store.delete_session_rows(
                session_id
            ),
        }


def _followup_ui_state(values, *, next_node, policy_version: str) -> str:
    """Return the bounded public UI state without Decision reasoning fields."""

    if values.get("termination_reason_code"):
        return "degraded"
    if not values.get("active_command_id"):
        return "idle"
    pending = PENDING_ACTION_BY_NODE.get(next_node)
    if values.get("generation_id") or pending in {
        "organizing_followup",
        "generating_followup",
        "waiting_for_retry",
        "recovering_followup",
    }:
        return "generation_pending"
    if policy_version == "adaptive_v1" and pending in {
        "validating_answer",
        "accepting_answer",
        "analyzing_answer",
    }:
        return "decision_pending"
    return "generation_pending"
