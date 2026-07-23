from __future__ import annotations

from uuid import uuid4

from app.graphs.durable_interview_state import make_durable_initial_state
from app.graphs.interview_state import choose_workflow_engine
from app.services.interview_event_stream import InterviewEventStreamService
from app.services.runtime_events import AcceptedInterviewCommand


PENDING_ACTION_BY_NODE = {
    "wait_for_answer": "waiting_for_answer",
    "generate_followup": "generating_followup",
    "wait_for_retry": "waiting_for_retry",
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
    ) -> None:
        self.legacy_store = legacy_store
        self.workflow_store = workflow_store
        self.generation_store = generation_store
        self.graph_registry = graph_registry
        self.runtime_store = runtime_store
        self.runtime_enabled = runtime_enabled
        self.rollout_percent = rollout_percent
        self.default_graph_version = default_graph_version
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
    ):
        session_id = str(uuid4())
        engine = choose_workflow_engine(
            session_id,
            runtime_store=self.runtime_store,
            runtime_enabled=self.runtime_enabled,
            rollout_percent=self.rollout_percent,
        )
        if engine == "legacy":
            return self.legacy_store.start(
                plan,
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                session_id=session_id,
            )
        self.legacy_store.insert_durable_session_shell(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
        )
        self.graph_for_version(self.default_graph_version).invoke(
            make_durable_initial_state(session_id, plan),
            config={"configurable": {"thread_id": session_id}},
        )
        return self.legacy_store._to_turn(
            self.legacy_store.get(session_id), follow_up=None
        )

    def graph_for_version(self, version: str):
        return self.graph_registry.get(version)

    def graph_for_session(self, session_id: str):
        state = self.legacy_store.get(session_id)
        version = state.get("graph_schema_version")
        if state.get("workflow_engine") != "langgraph-v1" or not version:
            raise ValueError("session is not a durable graph session")
        return self.graph_for_version(version)

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
        if state.get("workflow_engine") != "langgraph-v1":
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
            stream_url=(
                f"/api/interviews/{session_id}/commands/"
                f"{record.command_id}/stream"
            ),
        )

    def snapshot(self, session_id: str) -> dict:
        snapshot = self.legacy_store.snapshot(session_id)
        if snapshot.get("workflow_engine") != "langgraph-v1":
            state = self.legacy_store.get(session_id)
            if state.get("workflow_engine") != "langgraph-v1":
                return snapshot
        graph_state = self.graph_for_session(session_id).get_state(
            {"configurable": {"thread_id": session_id}}
        )
        next_node = graph_state.next[0] if graph_state.next else None
        snapshot["pending_action"] = PENDING_ACTION_BY_NODE.get(next_node)
        snapshot["workflow_engine"] = "langgraph-v1"
        return snapshot
