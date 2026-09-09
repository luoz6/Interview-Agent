"""In-memory runtime ports for exercising the real V3 interview graph in browsers."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import RLock, Thread
from time import sleep
from types import SimpleNamespace
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from app.domain.interview.question_intent import QuestionIntentV1
from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    build_durable_interview_graph_v3,
)
from app.services.decision_store import DecisionContract, InMemoryDecisionStore
from app.services.followup_decision_service import FollowupDecisionExecutionService
from app.services.interview_generation_store import (
    GenerationAlreadyCompleted,
    InterviewGeneration,
)
from app.services.interview_plan_revision import (
    InterviewPlanV3,
    default_plan_configuration,
    legacy_interview_knowledge_scope_snapshot,
    plan_payload_sha256,
)
from app.services.interview_workflow import InterviewWorkflowService
from app.services.interview_workflow_store import (
    InterviewCommandRecord,
    ProjectionResult,
)
from app.services.langgraph_runtime import VersionedGraphRegistry
from app.services.runtime_events import AcceptedInterviewCommand
from app.services.session_plan_binding import SessionPlanBinding


class BrowserV3GenerationStore:
    """Small in-memory implementation of the production Generation port."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_id: dict[str, InterviewGeneration] = {}
        self._by_source: dict[tuple[str, str], str] = {}
        self._events: dict[str, list[SimpleNamespace]] = {}

    def prepare_generation(self, **kwargs) -> InterviewGeneration:
        key = (kwargs["session_id"], kwargs["source_command_id"])
        with self._lock:
            existing_id = self._by_source.get(key)
            if existing_id is not None:
                return self._by_id[existing_id]
            identity = kwargs.get("identity_sha256") or hashlib.sha256(
                f"{key[0]}:{key[1]}".encode("utf-8")
            ).hexdigest()
            generation = InterviewGeneration(
                generation_id=f"generation-{identity[:32]}",
                session_id=key[0],
                source_command_id=key[1],
                question_id=kwargs["question_id"],
                status="pending",
                active_attempt=1,
                final_text=None,
                source_decision_id=kwargs.get("source_decision_id"),
                decision_prompt_version=kwargs.get("decision_prompt_version"),
                decision_prompt_sha256=kwargs.get("decision_prompt_sha256"),
                generation_prompt_version=kwargs.get("generation_prompt_version"),
                generation_prompt_sha256=kwargs.get("generation_prompt_sha256"),
                generation_kind=kwargs.get("generation_kind", "followup"),
                identity_sha256=kwargs.get("identity_sha256"),
                intent_sha256=kwargs.get("intent_sha256"),
                context_sha256=kwargs.get("context_sha256"),
                knowledge_scope_sha256=kwargs.get("knowledge_scope_sha256"),
                generator_version=kwargs.get("generator_version"),
            )
            self._by_id[generation.generation_id] = generation
            self._by_source[key] = generation.generation_id
            self._events[generation.generation_id] = []
            return generation

    def get_by_id(self, generation_id: str) -> InterviewGeneration:
        with self._lock:
            return self._by_id[generation_id]

    def get_by_source_command(
        self, session_id: str, source_command_id: str
    ) -> InterviewGeneration | None:
        with self._lock:
            generation_id = self._by_source.get((session_id, source_command_id))
            return self._by_id.get(generation_id) if generation_id else None

    def start_or_reclaim_attempt(
        self, generation_id: str, attempt_number: int, **_kwargs
    ) -> SimpleNamespace:
        with self._lock:
            generation = self._by_id[generation_id]
            if generation.status == "completed":
                raise GenerationAlreadyCompleted(generation_id)
            attempt_number = max(attempt_number, generation.active_attempt)
            self._by_id[generation_id] = replace(
                generation, status="running", active_attempt=attempt_number
            )
            return SimpleNamespace(
                generation_id=generation_id,
                attempt_number=attempt_number,
                lease_token=f"browser-lease-{generation_id}-{attempt_number}",
                fencing_version=attempt_number,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
                reclaimed_after_expiry=False,
            )

    def append_chunk(
        self,
        generation_id: str,
        attempt_number: int,
        sequence: int,
        text: str,
        **_kwargs,
    ) -> None:
        with self._lock:
            events = self._events[generation_id]
            if not any(
                event.attempt_number == attempt_number
                and event.sequence == sequence
                for event in events
            ):
                events.append(
                    SimpleNamespace(
                        generation_id=generation_id,
                        attempt_number=attempt_number,
                        sequence=sequence,
                        event_type="chunk",
                        delta=text,
                    )
                )

    def complete_attempt(
        self,
        generation_id: str,
        attempt_number: int,
        text: str,
        **kwargs,
    ) -> None:
        with self._lock:
            current = self._by_id[generation_id]
            if current.status == "completed":
                raise GenerationAlreadyCompleted(generation_id)
            self._by_id[generation_id] = replace(
                current,
                status="completed",
                active_attempt=attempt_number,
                final_text=text,
                result_mode=kwargs.get("result_mode"),
                failure_reason_code=kwargs.get("failure_reason_code"),
                provider_invocation_count=kwargs.get("provider_invocation_count"),
                generation_latency_ms=kwargs.get("generation_latency_ms"),
                fallback_used=kwargs.get("fallback_used"),
                safe_reason_code=kwargs.get("safe_reason_code"),
            )

    def fail_attempt(
        self,
        generation_id: str,
        attempt_number: int,
        reason_code: str,
        **_kwargs,
    ) -> None:
        with self._lock:
            current = self._by_id[generation_id]
            self._events[generation_id].append(
                SimpleNamespace(
                    generation_id=generation_id,
                    attempt_number=attempt_number + 1,
                    sequence=0,
                    event_type="generation_reset",
                    delta="",
                )
            )
            self._by_id[generation_id] = replace(
                current,
                status="pending",
                active_attempt=attempt_number + 1,
                failure_reason_code=reason_code,
            )

    def list_events_after(
        self,
        generation_id: str,
        *,
        after_attempt: int,
        after_sequence: int,
        limit: int,
    ) -> list[SimpleNamespace]:
        with self._lock:
            return [
                event
                for event in self._events.get(generation_id, [])
                if (event.attempt_number, event.sequence)
                > (after_attempt, after_sequence)
            ][:limit]

    def heartbeat_attempt(self, *_args, **_kwargs) -> bool:
        return True

    def assert_attempt_owned(self, *_args, **_kwargs) -> bool:
        return True

    def delete_session_rows(self, session_id: str) -> int:
        with self._lock:
            keys = [key for key in self._by_source if key[0] == session_id]
            for key in keys:
                generation_id = self._by_source.pop(key)
                self._by_id.pop(generation_id, None)
                self._events.pop(generation_id, None)
            return len(keys)


class BrowserV3WorkflowStore:
    """Projects real graph checkpoints into the browser session repository."""

    def __init__(self, session_store) -> None:
        self.session_store = session_store
        self.commands: dict[tuple[str, str], InterviewCommandRecord] = {}
        self.bootstrap_inputs: dict[str, str] = {}
        self._lock = RLock()

    def enqueue_command(self, **kwargs) -> InterviewCommandRecord:
        key = (kwargs["session_id"], kwargs["command_id"])
        payload = {
            "command_type": kwargs["command_type"],
            "expected_version": kwargs["expected_version"],
            "answer_text": kwargs.get("answer_text"),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        with self._lock:
            existing = self.commands.get(key)
            if existing is not None:
                if existing.payload_sha256 != digest:
                    raise ValueError("browser command payload conflicts")
                return existing
            record = InterviewCommandRecord(
                session_id=key[0],
                command_id=key[1],
                command_type=kwargs["command_type"],
                expected_version=kwargs["expected_version"],
                answer_text=kwargs.get("answer_text"),
                payload_sha256=digest,
                status="pending",
                result_state_version=None,
                error_code=None,
            )
            self.commands[key] = record
            return record

    def get_command(self, session_id: str, command_id: str) -> InterviewCommandRecord:
        with self._lock:
            return self.commands[(session_id, command_id)]

    def get_command_or_none(
        self, session_id: str, command_id: str
    ) -> InterviewCommandRecord | None:
        with self._lock:
            return self.commands.get((session_id, command_id))

    def mark_command_conflict(
        self, session_id: str, command_id: str, state_version: int
    ) -> None:
        key = (session_id, command_id)
        with self._lock:
            self.commands[key] = replace(
                self.commands[key],
                status="conflict",
                result_state_version=state_version,
                error_code="state_version_conflict",
            )

    def register_bootstrap_input(
        self,
        *,
        session_id: str,
        bootstrap_input_sha256: str,
        **_kwargs,
    ) -> None:
        with self._lock:
            previous = self.bootstrap_inputs.setdefault(
                session_id, bootstrap_input_sha256
            )
            if previous != bootstrap_input_sha256:
                raise ValueError("browser bootstrap input conflicts")

    def project_state(self, state) -> ProjectionResult:
        next_version = int(state["state_version"]) + 1
        digest = hashlib.sha256(
            json.dumps(
                {
                    "session_id": state["session_id"],
                    "state_version": next_version,
                    "status": state["interview_status"],
                    "messages": state["messages"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            shell = self.session_store.get(state["session_id"])
            shell["status"] = state["interview_status"]
            shell["current_index"] = state["current_index"]
            shell["messages"] = [dict(item) for item in state["messages"]]
            shell["skipped_question_ids"] = list(state["skipped_question_ids"])
            shell["state_version"] = next_version
            shell["checkpoint_version"] = next_version
            shell["last_command_id"] = state.get("active_command_id")
            if state["interview_status"] == "finished":
                shell["phase"] = "review"
                shell["phase_status"] = "completed"
                shell["finished_at"] = shell.get("finished_at") or datetime.now(
                    timezone.utc
                ).isoformat()
            command_id = state.get("active_command_id")
            if state.get("command_outcome") == "completed" and command_id:
                key = (state["session_id"], command_id)
                command = self.commands[key]
                self.commands[key] = replace(
                    command,
                    status="applied",
                    result_state_version=next_version,
                    error_code=None,
                )
        return ProjectionResult(next_version, digest)

    def delete_session_control_rows(self, session_id: str) -> int:
        with self._lock:
            keys = [key for key in self.commands if key[0] == session_id]
            for key in keys:
                self.commands.pop(key, None)
            self.bootstrap_inputs.pop(session_id, None)
            return len(keys)


class BrowserV3Examiner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_main_question_attempt(self, **kwargs) -> str:
        self.calls.append(kwargs)
        intent = kwargs["intent"]
        if intent.focus == "超时回退验证":
            raise TimeoutError("deterministic browser provider timeout")
        questions = {
            "q1": "如果 Redis 库存扣减成功但消息投递失败，你会如何恢复？",
            "q2": "针对 RocketMQ 重试与死信，你会如何划分自动恢复和人工介入边界？",
            "q3": "面对十倍流量扩容，你如何设计容量验证与回滚门禁？",
            "q4": "生产故障信息不完整时，你如何推进定位并承担决策责任？",
            "q5": "数据库重试与幂等测试中，你会覆盖哪些部分失败场景？",
        }
        return questions[intent.question_id]


def _always_advance(_context) -> DecisionContract:
    return DecisionContract(
        action="next_question",
        answer_state="complete",
        gap_type="none",
        gap_summary="",
        reason_code="answer_complete",
        decision_confidence="high",
        closed_gap_ids=[],
        policy_version="adaptive_v1",
    )


class BrowserV3Workflow(InterviewWorkflowService):
    """Production workflow service plus synchronous test-only command delivery."""

    def submit_command(self, session_id: str, **kwargs) -> AcceptedInterviewCommand:
        accepted = super().submit_command(session_id, **kwargs)
        self.resume_command(session_id, accepted.command_id)
        return accepted


class BrowserV3Harness:
    def __init__(self, session_store) -> None:
        self.session_store = session_store
        self.workflow_store = BrowserV3WorkflowStore(session_store)
        self.generation_store = BrowserV3GenerationStore()
        self.examiner = BrowserV3Examiner()
        decision_service = FollowupDecisionExecutionService(
            store=InMemoryDecisionStore(),
            provider=_always_advance,
        )
        graph = build_durable_interview_graph_v3(
            DurableInterviewGraphDependencies(
                workflow_store=self.workflow_store,
                generation_store=self.generation_store,
                decision_service=decision_service,
                examiner=self.examiner,
            ),
            checkpointer=InMemorySaver(),
        )
        registry = VersionedGraphRegistry()
        registry.register("langgraph-v3", graph)
        self.workflow = BrowserV3Workflow(
            legacy_store=session_store,
            workflow_store=self.workflow_store,
            generation_store=self.generation_store,
            graph_registry=registry,
            runtime_store="postgres",
            runtime_enabled=True,
            rollout_percent=100,
            default_graph_version="langgraph-v3",
        )
        self.session_ids: set[str] = set()
        self.bootstrap_threads: dict[str, Thread] = {}

    def create_session(self, legacy_plan, *, mode: str) -> str:
        turn = self.session_store.start(
            legacy_plan,
            job_description="Backend engineer",
            resume_text="Built Redis and RocketMQ recovery workflows",
            job_tags=["Redis", "RocketMQ"],
        )
        session_id = turn.session_id
        plan = self._plan(fallback=mode == "fallback")
        binding = SessionPlanBinding(
            plan_origin="plan_revision",
            plan_revision_id=str(uuid4()),
            plan_family_id=str(uuid4()),
            revision=1,
            plan_sha256=plan_payload_sha256(plan),
            configuration_snapshot=plan.configuration_snapshot.model_dump(mode="json"),
            plan_snapshot=plan.model_dump(mode="json"),
        )
        shell = self.session_store.get(session_id)
        shell.update(
            {
                "plan": plan,
                "plan_origin": binding.plan_origin,
                "plan_revision_id": binding.plan_revision_id,
                "plan_family_id": binding.plan_family_id,
                "revision": binding.revision,
                "plan_sha256": binding.plan_sha256,
                "configuration_snapshot": binding.configuration_snapshot,
                "plan_snapshot": binding.plan_snapshot,
                "principal_memory_mode": binding.principal_memory_mode,
                "workflow_engine": "langgraph-v3",
                "graph_schema_version": "langgraph-v3",
                "memory_policy_version": "decision-aware-v1",
                "status": "preparing_first_question",
                "current_index": 0,
                "messages": [],
                "skipped_question_ids": [],
                "state_version": 0,
                "checkpoint_version": 0,
                "last_command_id": None,
            }
        )
        self.session_ids.add(session_id)

        def bootstrap() -> None:
            sleep(0.075)
            self.workflow.ensure_interview_bootstrapped(session_id, plan=plan)

        thread = Thread(target=bootstrap, name=f"browser-v3-{session_id}", daemon=True)
        self.bootstrap_threads[session_id] = thread
        thread.start()
        return session_id

    def stats(self, session_id: str) -> dict:
        config = {"configurable": {"thread_id": session_id}}
        snapshot = self.workflow.graph_for_session(session_id).get_state(config)
        values = snapshot.values
        rendered = values.get("rendered_questions") or {}
        return {
            "session_id": session_id,
            "graph_executed": bool(values),
            "graph_schema_version": values.get("graph_schema_version"),
            "next_nodes": list(snapshot.next or ()),
            "rendered_questions": rendered,
            "provider_call_count": len(self.examiner.calls),
            "command_count": sum(
                key[0] == session_id for key in self.workflow_store.commands
            ),
        }

    def delete(self, session_id: str) -> None:
        thread = self.bootstrap_threads.pop(session_id, None)
        if thread is not None:
            thread.join(timeout=2)
        self.workflow.graph_for_session(session_id).checkpointer.delete_thread(session_id)
        self.workflow_store.delete_session_control_rows(session_id)
        self.generation_store.delete_session_rows(session_id)
        self.session_ids.discard(session_id)

    @staticmethod
    def _plan(*, fallback: bool) -> InterviewPlanV3:
        focuses = [
            "超时回退验证" if fallback else "Redis 库存一致性",
            "RocketMQ 重试与死信",
            "十倍流量扩容",
            "生产故障责任",
            "数据库重试与幂等测试",
        ]
        kinds = ["project", "technical", "system-design", "behavioral", "technical"]
        goals = [
            ("failure_mode", "recovery"),
            ("reliability", "recovery"),
            ("scale", "tradeoff"),
            ("ownership", "collaboration"),
            ("implementation_depth", "failure_mode"),
        ]
        return InterviewPlanV3(
            title="Browser V3 真人面试",
            configuration_snapshot=default_plan_configuration(),
            knowledge_scope=legacy_interview_knowledge_scope_snapshot(),
            questions=tuple(
                QuestionIntentV1(
                    question_id=f"q{index}",
                    position=index,
                    kind=kinds[index - 1],
                    focus=focus,
                    difficulty="advanced" if index in {1, 3} else "intermediate",
                    assessment_goals=goals[index - 1],
                    expected_minutes=5,
                    expected_followups=1,
                    knowledge_binding={},
                )
                for index, focus in enumerate(focuses, start=1)
            ),
        )


class BrowserWorkflowRouter:
    def __init__(self, legacy_workflow, v3_harness: BrowserV3Harness) -> None:
        self.legacy_workflow = legacy_workflow
        self.v3_harness = v3_harness
        self.event_stream = self

    def _target(self, session_id: str):
        if session_id in self.v3_harness.session_ids:
            return self.v3_harness.workflow
        return self.legacy_workflow

    def snapshot(self, session_id: str):
        return self._target(session_id).snapshot(session_id)

    def submit_command(self, session_id: str, **kwargs):
        return self._target(session_id).submit_command(session_id, **kwargs)

    def iter_sse(self, session_id: str, command_id: str, **kwargs):
        return self._target(session_id).event_stream.iter_sse(
            session_id, command_id, **kwargs
        )
