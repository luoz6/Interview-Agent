from copy import deepcopy
import inspect

from app.agents.examiner import (
    ExaminerAgent,
    fallback_followup as examiner_fallback_followup,
)
from app.graphs.interview_state import (
    InterviewState,
    build_initial_state,
    count_candidate_answers_for_question,
    get_current_question,
    MemoryPolicyVersion,
)
from app.services.llm import InterviewLLM
from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentExecutionRunner,
    correlation_id_from_plan,
    evidence_ids_for_question,
)
from app.services.knowledge_binding import KnowledgeBindingResolver
from app.services.prep import InterviewPlan
from app.services.context_budget import (
    FOLLOWUP_CONTEXT_POLICY,
    context_enforcement_enabled,
)
from app.services.context_selection import build_interview_context
from app.services.context_runtime import ContextRuntime, get_context_runtime
from app.services.session_plan_binding import SessionPlanBinding
from app.services.decision_store import InMemoryDecisionStore
from app.services.followup_diagnostics import (
    FollowupDiagnosticInput,
    FollowupPolicySnapshot,
)
from app.services.followup_decision_service import (
    FollowupDecisionExecutionService,
)

INTERVIEW_FINISHED_MESSAGE = "本次模拟面试已结束。"


class InterviewGraphRunner:
    def __init__(
        self,
        llm: InterviewLLM | None = None,
        examiner=None,
        knowledge_binding_resolver: KnowledgeBindingResolver | None = None,
        execution_runner: AgentExecutionRunner | None = None,
        context_runtime: ContextRuntime | None = None,
        decision_service=None,
    ) -> None:
        self._llm = llm
        self._examiner = examiner or ExaminerAgent(
            llm=llm,
            execution_runner=execution_runner,
        )
        self._knowledge_binding_resolver = (
            knowledge_binding_resolver or KnowledgeBindingResolver()
        )
        # Reuse the LLM's runtime when available, but keep global resolution
        # lazy. With enforcement disabled, the legacy graph must not require
        # production context-window configuration.
        self._context_runtime = context_runtime or getattr(
            llm,
            "context_runtime",
            None,
        )
        self._decision_service = decision_service or FollowupDecisionExecutionService(
            store=InMemoryDecisionStore(),
            provider=None,
        )

    def start(
        self,
        session_id: str,
        plan: InterviewPlan,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        memory_policy_version: MemoryPolicyVersion = "deterministic-v1",
        plan_binding: SessionPlanBinding | None = None,
    ) -> InterviewState:
        return build_initial_state(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
            memory_policy_version=memory_policy_version,
            plan_binding=plan_binding,
        )

    def submit_answer(
        self,
        state: InterviewState,
        answer: str,
        *,
        command_id: str | None = None,
    ) -> InterviewState:
        next_state = _append_candidate_answer(state, answer)
        next_state = brain_node(
            next_state,
            self._llm,
            examiner=self._examiner,
            knowledge_binding_resolver=self._knowledge_binding_resolver,
            context_runtime=self._context_runtime,
            command_id=command_id,
            decision_service=self._decision_service,
        )
        return speaker_node(next_state)

    def prepare_answer(
        self,
        state: InterviewState,
        answer: str,
        *,
        command_id: str | None = None,
    ) -> InterviewState:
        next_state = _append_candidate_answer(state, answer)
        return brain_node(
            next_state,
            self._llm,
            examiner=self._examiner,
            knowledge_binding_resolver=self._knowledge_binding_resolver,
            context_runtime=self._context_runtime,
            generate_followup_text=False,
            command_id=command_id,
            decision_service=self._decision_service,
        )

    def finalize_prepared_answer(
        self,
        state: InterviewState,
        *,
        follow_up: str | None = None,
    ) -> InterviewState:
        next_state = deepcopy(state)
        if follow_up is not None and next_state["decision"] is not None:
            next_state["decision"]["follow_up"] = follow_up
        return speaker_node(next_state)

    def stream_followup(self, state: InterviewState):
        question = get_current_question(state)
        focus = question.focus if question is not None else "current question"
        yield from _stream_examiner_followup(
            self._examiner,
            context=_build_followup_context(
                state,
                self._knowledge_binding_resolver,
                context_runtime=self._context_runtime,
            ),
            focus=focus,
            execution_context=_examiner_execution_context(state),
        )


def brain_node(
    state: InterviewState,
    llm: InterviewLLM | None,
    *,
    examiner=None,
    knowledge_binding_resolver: KnowledgeBindingResolver | None = None,
    context_runtime: ContextRuntime | None = None,
    generate_followup_text: bool = True,
    command_id: str | None = None,
    decision_service=None,
) -> InterviewState:
    question = get_current_question(state)
    if question is None:
        state["decision"] = {
            "action": "finish",
            "follow_up": None,
            "reason": "all_questions_completed",
        }
        return state

    resolved_decision_service = decision_service or FollowupDecisionExecutionService(
        store=InMemoryDecisionStore(),
        provider=None,
    )
    request = _build_followup_diagnostic_input(state, question)
    effective_command_id = command_id or (
        state.get("last_command_id")
        or f"legacy-answer-{state['state_version']}-{question.id}-"
        f"{len(request.candidate_answers)}"
    )
    result = resolved_decision_service.execute(
        request,
        source_command_id=effective_command_id,
        worker_id="legacy-interview-worker",
    )
    if result.decision is None:
        raise RuntimeError("legacy Decision execution did not complete")
    contract = result.decision
    follow_up = None
    if generate_followup_text and contract.action == "follow_up":
        resolved_examiner = examiner or ExaminerAgent(llm=llm)
        follow_up = _generate_examiner_followup(
            resolved_examiner,
            context=_build_followup_context(
                state,
                knowledge_binding_resolver,
                context_runtime=context_runtime,
            ),
            focus=question.focus,
            execution_context=_examiner_execution_context(
                state,
                command_id=command_id,
            ),
        )

    state["decision"] = {
        "action": contract.action,
        "follow_up": follow_up,
        "reason": contract.reason_code,
    }
    state["decision_id"] = result.decision_id
    state["decision_action"] = contract.action
    state["decision_reason_code"] = contract.reason_code
    state["decision_gap_type"] = contract.gap_type
    state["decision_gap_summary"] = contract.gap_summary
    state["followup_policy_version"] = contract.policy_version
    state["closed_gap_ids"] = list(contract.closed_gap_ids)
    state["current_followup_count"] = len(request.asked_followups)
    return state


def speaker_node(state: InterviewState) -> InterviewState:
    decision = state["decision"]
    if decision is None:
        state["status"] = "finished"
        state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
        return state

    action = decision["action"]
    question = get_current_question(state)

    if action == "follow_up" and question is not None:
        output = decision.get("follow_up") or fallback_followup(question.focus)
        state["pending_output"] = output
        state["messages"].append(
            {"role": "interviewer", "content": output, "question_id": question.id}
        )
        state["current_followup_count"] += 1
        return state

    if action == "next_question":
        state["current_index"] += 1
        state["current_followup_count"] = 0
        state["closed_gap_ids"] = []
        next_question = get_current_question(state)
        if next_question is None:
            state["status"] = "finished"
            state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
            return state
        state["pending_output"] = next_question.prompt
        state["messages"].append(
            {
                "role": "interviewer",
                "content": next_question.prompt,
                "question_id": next_question.id,
            }
        )
        return state

    state["current_index"] = len(state["plan"].questions)
    state["status"] = "finished"
    state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
    state["messages"].append(
        {
            "role": "interviewer",
            "content": INTERVIEW_FINISHED_MESSAGE,
            "question_id": None,
        }
    )
    return state


def fallback_followup(focus: str) -> str:
    return examiner_fallback_followup(focus)


def _append_candidate_answer(state: InterviewState, answer: str) -> InterviewState:
    next_state = deepcopy(state)
    question = get_current_question(next_state)
    if question is None:
        next_state["status"] = "finished"
        next_state["decision"] = {
            "action": "finish",
            "follow_up": None,
            "reason": "all_questions_completed",
        }
        next_state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
        return next_state

    next_state["messages"].append(
        {
            "role": "candidate",
            "content": answer.strip(),
            "question_id": question.id,
        }
    )
    return next_state


def _build_followup_diagnostic_input(
    state: InterviewState,
    question,
) -> FollowupDiagnosticInput:
    question_messages = [
        message
        for message in state["messages"]
        if message.get("question_id") == question.id
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
    return FollowupDiagnosticInput(
        session_status=(
            "finished" if state["status"] == "finished" else "active"
        ),
        session_id=state["session_id"],
        question_id=question.id,
        question_text=question.prompt,
        focus=question.focus,
        candidate_answers=candidate_answers,
        asked_followups=asked_followups,
        followup_count=len(asked_followups),
        closed_gap_ids=list(state.get("closed_gap_ids") or []),
        public_knowledge_summary="",
        policy=FollowupPolicySnapshot(
            policy_version=policy_version,
            max_followups=1 if policy_version == "fixed_v1" else 2,
        ),
    )


def _build_followup_context(
    state: InterviewState,
    knowledge_binding_resolver: KnowledgeBindingResolver | None = None,
    *,
    context_runtime: ContextRuntime | None = None,
) -> list[dict[str, str]]:
    question = get_current_question(state)
    question_id = question.id if question is not None else ""
    resolver = knowledge_binding_resolver or KnowledgeBindingResolver()
    resolution = resolver.resolve(state["plan"], question_id)
    if not context_enforcement_enabled(FOLLOWUP_CONTEXT_POLICY.operation):
        recent_messages = [
            {"role": message["role"], "content": message["content"]}
            for message in state["messages"][-4:]
        ]
        return recent_messages + resolution.messages
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
    context, _ = build_interview_context(
        state["messages"],
        current_question_id=question_id,
        evidence_messages=resolution.messages,
        policy=FOLLOWUP_CONTEXT_POLICY,
        selection_budget=selection_budget,
        estimator=estimator,
        model=model,
    )
    return context


def _examiner_execution_context(
    state: InterviewState,
    *,
    command_id: str | None = None,
) -> AgentExecutionContext:
    question = get_current_question(state)
    question_id = question.id if question is not None else None
    effective_command_id = (
        command_id if command_id is not None else state.get("last_command_id")
    )
    return AgentExecutionContext(
        correlation_id=correlation_id_from_plan(
            state["plan"],
            session_id=state["session_id"],
        ),
        causation_id=effective_command_id,
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id=state["session_id"],
        question_id=question_id,
        state_version=state["state_version"],
        command_id=effective_command_id,
        evidence_ids=evidence_ids_for_question(state["plan"], question_id),
    )


def _supports_execution_context(method) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "execution_context"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _generate_examiner_followup(
    examiner,
    *,
    context: list[dict[str, str]],
    focus: str,
    execution_context: AgentExecutionContext,
) -> str:
    kwargs = {"context": context, "focus": focus}
    if _supports_execution_context(examiner.generate_followup):
        kwargs["execution_context"] = execution_context
    return examiner.generate_followup(**kwargs)


def _stream_examiner_followup(
    examiner,
    *,
    context: list[dict[str, str]],
    focus: str,
    execution_context: AgentExecutionContext,
):
    kwargs = {"context": context, "focus": focus}
    if _supports_execution_context(examiner.stream_followup):
        kwargs["execution_context"] = execution_context
    yield from examiner.stream_followup(**kwargs)
