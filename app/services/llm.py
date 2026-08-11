import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterator, Literal, Protocol

from pydantic import ValidationError

from app.runtime.config import load_llm_runtime_settings, load_provider_credentials
from app.runtime.config.environment import environment_value
from app.services.context_budget import (
    context_enforcement_enabled,
    FOLLOWUP_CONTEXT_POLICY,
    OperationContextPolicy,
    PLAN_CONTEXT_POLICY,
    REPORT_CONTEXT_POLICY,
    RenderedPromptGuard,
)
from app.services.context_selection import (
    build_interview_context,
    truncate_text_to_tokens,
)
from app.services.provider_usage import (
    begin_provider_attempt,
    publish_prompt_measurement,
    publish_provider_response,
    publish_plan_context_selection,
)
from app.services.context_runtime import (
    ContextRuntime,
    ContextRuntimeConfig,
    build_context_runtime,
)
from app.services.context_language import classify_context_language
from app.services.model_capabilities import ContextConfigurationError
from app.services.interview_status_projection import (
    INTERVIEW_STATUS_ROLE,
    is_valid_interview_status_message,
)
from app.services.principal_memory_sink_policy import (
    ASSISTANCE_CONTEXT_KIND,
    FOLLOWUP_GENERATION_SINK,
    assert_principal_memory_sink,
)

if TYPE_CHECKING:
    from app.services.interview_plan_revision import PlanConfigurationSnapshot
    from app.services.report import InterviewReport

logger = logging.getLogger(__name__)

REPORT_EVIDENCE_PROMPT_VERSION = "stage40-evidence-v1"
RAW_ONLY_PLAN_MODELS = frozenset({"deepseek-v4-pro"})


class MissingLLMConfigError(RuntimeError):
    """LLM configuration is missing, usually OPENAI_API_KEY."""


def resolve_plan_output_mode(
    model: str,
) -> Literal["structured_first", "raw_only"]:
    """Choose the production plan protocol before any Provider request."""

    return "raw_only" if model in RAW_ONLY_PLAN_MODELS else "structured_first"


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = "deepseek-v4-pro"
    base_url: str | None = None
    temperature: float = 0.2
    request_timeout_seconds: float = 120.0
    max_retries: int = 1
    context_window_tokens: int | None = None
    protocol_reserve_tokens: int = 512
    structured_output_reserve_tokens: int = 2048
    context_safety_margin_tokens: int = 1024
    tokenizer_family: str | None = None
    plan_output_mode: Literal["structured_first", "raw_only"] = "structured_first"

    @classmethod
    def from_env(cls, *, memory=None) -> "LLMConfig":
        from app.runtime.config.memory import load_effective_memory_config

        api_key = load_provider_credentials().openai_api_key
        if not api_key:
            raise MissingLLMConfigError("OPENAI_API_KEY is required")

        if memory is None:
            memory = load_effective_memory_config().model
        runtime_settings = load_llm_runtime_settings()
        return cls(
            api_key=api_key,
            model=memory.model,
            base_url=runtime_settings.base_url,
            temperature=runtime_settings.temperature,
            request_timeout_seconds=runtime_settings.request_timeout_seconds,
            max_retries=runtime_settings.max_retries,
            context_window_tokens=memory.context_window_tokens,
            protocol_reserve_tokens=memory.protocol_reserve_tokens,
            structured_output_reserve_tokens=(
                memory.structured_output_reserve_tokens
            ),
            context_safety_margin_tokens=memory.safety_margin_tokens,
            tokenizer_family=memory.tokenizer_family,
            plan_output_mode=resolve_plan_output_mode(memory.model),
        )


class InterviewLLM(Protocol):
    config: LLMConfig
    chat_model: Any

    def generate_plan(
        self,
        job_description: str,
        resume_text: str,
        knowledge_context: list[dict] | None = None,
        configuration: "PlanConfigurationSnapshot | None" = None,
    ):
        """Generate the interview plan from JD and resume."""

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        """Generate a follow-up question from recent context."""

    def stream_followup(self, context: list[dict[str, str]]) -> Iterator[str]:
        """Stream a follow-up question from recent context."""

    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> "InterviewReport":
        """Generate a structured expert report."""


class OpenAIInterviewLLM:
    def __init__(
        self,
        config: LLMConfig | None = None,
        chat_model=None,
        trace_recorder=None,
        report_output_mode: Literal["structured_first", "raw_only"] | None = None,
        context_runtime: ContextRuntime | None = None,
        provider_attempt_hook: Callable[[], None] | None = None,
    ) -> None:
        from app.services.report_trace import ReportTraceRecorder

        resolved_config = config or (
            LLMConfig(api_key="injected-chat-model")
            if chat_model is not None
            else LLMConfig.from_env()
        )
        if resolved_config.plan_output_mode not in {"structured_first", "raw_only"}:
            raise ValueError(
                "unsupported plan_output_mode: "
                f"{resolved_config.plan_output_mode}"
            )
        self.config = resolved_config
        self.chat_model = chat_model or self._build_chat_model(resolved_config)
        runtime = context_runtime or build_context_runtime(
            ContextRuntimeConfig(
                model=resolved_config.model,
                base_url=resolved_config.base_url,
                context_window_tokens=resolved_config.context_window_tokens,
                protocol_reserve_tokens=resolved_config.protocol_reserve_tokens,
                structured_output_reserve_tokens=(
                    resolved_config.structured_output_reserve_tokens
                ),
                safety_margin_tokens=resolved_config.context_safety_margin_tokens,
                tokenizer_family=resolved_config.tokenizer_family,
            )
        )
        if runtime.model_profile.model != resolved_config.model:
            from app.services.model_capabilities import ContextConfigurationError

            raise ContextConfigurationError(
                "LLM model and ContextRuntime model must match"
            )
        self.context_runtime = runtime
        self.model_profile = runtime.model_profile
        self.token_estimator = runtime.estimator_resolution
        self._budget_resolver = runtime.budget_resolver
        self._prompt_guard = RenderedPromptGuard()
        self.trace_recorder = trace_recorder or ReportTraceRecorder.from_env()
        self._provider_attempt_hook = provider_attempt_hook
        self.plan_output_mode = resolved_config.plan_output_mode
        configured_mode = (
            report_output_mode or load_llm_runtime_settings().report_output_mode
        )
        if configured_mode not in {"structured_first", "raw_only"}:
            raise ValueError(f"unsupported OPENAI_REPORT_OUTPUT_MODE: {configured_mode}")
        self.report_output_mode = configured_mode

    def generate_plan(
        self,
        job_description: str,
        resume_text: str,
        knowledge_context: list[dict] | None = None,
        configuration: "PlanConfigurationSnapshot | None" = None,
    ):
        from app.services.prep import (
            enforce_generated_interview_plan,
            InterviewPlan,
            validate_generation_configuration,
        )

        configuration = (
            validate_generation_configuration(configuration)
            if configuration is not None
            else None
        )

        assert_principal_memory_sink(
            operation="plan_generation",
            payload={"knowledge_context": knowledge_context},
        )
        knowledge_candidate_count = len(knowledge_context or [])
        job_description, resume_text, knowledge_context = self._fit_plan_inputs(
            job_description=job_description,
            resume_text=resume_text,
            knowledge_context=knowledge_context,
        )
        publish_plan_context_selection(
            candidate_count=knowledge_candidate_count,
            retained_count=len(knowledge_context or []),
        )
        prompt = self._build_plan_prompt(
            job_description=job_description,
            resume_text=resume_text,
            knowledge_context=knowledge_context,
            configuration=configuration,
        )
        self._guard_prompt(
            prompt,
            PLAN_CONTEXT_POLICY,
            force_enforcement=True,
        )
        if self.plan_output_mode == "raw_only":
            payload = self._invoke_raw_json_plan(
                prompt,
                force_context_enforcement=True,
            )
            try:
                generated = InterviewPlan.model_validate(payload)
            except ValidationError as exc:
                raise ValueError(
                    f"raw interview plan JSON schema validation failed: {exc}"
                ) from exc
        else:
            try:
                generated = self._invoke_structured_plan(prompt, InterviewPlan)
            except Exception as exc:
                logger.warning(
                    "Structured interview plan output failed, trying raw JSON path",
                    extra={"error_code": type(exc).__name__},
                )
                payload = self._invoke_raw_json_plan(
                    prompt,
                    force_context_enforcement=True,
                )
                try:
                    generated = InterviewPlan.model_validate(payload)
                except ValidationError as exc:
                    raise ValueError(
                        f"raw interview plan JSON schema validation failed: {exc}"
                    ) from exc
        if configuration is None:
            return generated
        return enforce_generated_interview_plan(generated, configuration)

    def _build_plan_prompt(
        self,
        *,
        job_description: str,
        resume_text: str,
        knowledge_context: list[dict] | None = None,
        configuration: "PlanConfigurationSnapshot | None" = None,
    ) -> str:
        from app.services.interview_plan_budget import QUESTION_TYPE_ORDER

        if configuration is None:
            question_kinds = ["project", "technical", "system-design"]
            count_instruction = "Return exactly 3 to 5 questions."
            id_instruction = (
                "Use unique consecutive ids q1, q2, q3, and continue in order "
                "if more questions are returned."
            )
            configuration_section = ""
        else:
            question_kinds = [
                question_type
                for question_type in QUESTION_TYPE_ORDER
                for _ in range(
                    configuration.question_type_budget.get(question_type, 0)
                )
            ]
            target_count = len(question_kinds)
            difficulty_guidance = {
                "foundation": (
                    "Prefer clear fundamentals and concrete examples; avoid hidden "
                    "advanced prerequisites."
                ),
                "intermediate": (
                    "Require real constraints, implementation choices, and trade-offs."
                ),
                "advanced": (
                    "Probe complex constraints, failure modes, scale, and evolution cost."
                ),
            }[configuration.difficulty]
            focus_guidance = {
                "technical_depth": (
                    "Emphasize implementation depth, boundaries, diagnostics, and failure modes."
                ),
                "system_design": (
                    "Emphasize architecture, capacity, reliability, and system trade-offs."
                ),
                "project_review": (
                    "Emphasize ownership, decisions, evidence, delivery, and outcomes."
                ),
                "balanced": (
                    "Balance project evidence, technical depth, design, and collaboration."
                ),
            }[configuration.focus_preset]
            count_instruction = (
                f"Return exactly {target_count} questions with this exact kind budget: "
                f"{json.dumps(configuration.question_type_budget, sort_keys=True)}."
            )
            id_instruction = (
                f"Use unique consecutive ids q1 through q{target_count} in order."
            )
            configuration_section = (
                "\nConfigured generation contract:\n"
                f"- target_duration_minutes: {configuration.target_duration_minutes}\n"
                f"- difficulty: {configuration.difficulty}\n"
                f"- focus_preset: {configuration.focus_preset}\n"
                f"- expected_followup_budget: {configuration.expected_followup_budget}\n"
                f"- max_followups_per_question: {configuration.max_followups_per_question}\n"
                f"- difficulty guidance: {difficulty_guidance}\n"
                f"- focus guidance: {focus_guidance}\n"
                "The duration is an estimate, not an exact-time promise. The service "
                "assigns per-question minute and follow-up estimates locally.\n"
            )
        expected_shape = {
            "title": "Backend interview plan",
            "questions": [
                {
                    "id": f"q{index}",
                    "kind": kind,
                    "prompt": "Ask one concrete interview question.",
                    "focus": "What this question evaluates.",
                }
                for index, kind in enumerate(question_kinds, start=1)
            ],
        }
        knowledge_section = ""
        if knowledge_context:
            knowledge_section = (
                "\n\nTrusted knowledge candidates (safe metadata only):\n"
                f"{json.dumps(knowledge_context, ensure_ascii=False, indent=2)}\n"
                "Use these candidates to make questions more specific. Do not copy a "
                "benchmark answer into a question and do not invent evidence IDs."
            )
        return (
            "You are a senior technical interviewer.\n"
            "Create a focused mock interview plan from the job description and resume.\n"
            f"{count_instruction}\n"
            "Each question kind must be one of: project, technical, system-design, behavioral.\n"
            f"{id_instruction}\n"
            "Questions should be specific to the candidate's resume and the target job.\n"
            "Do not generate prep_context; the service enriches the plan with Knowledge Agent metadata locally.\n"
            "Return valid JSON only. Do not return markdown.\n"
            f"{configuration_section}"
            "Use this JSON shape exactly:\n"
            f"{json.dumps(expected_shape, ensure_ascii=False, indent=2)}\n\n"
            f"Job description:\n{job_description}\n\n"
            f"Resume:\n{resume_text}"
            f"{knowledge_section}"
        )

    def _invoke_structured_plan(self, prompt: str, schema):
        try:
            structured_model = self.chat_model.with_structured_output(
                schema,
                method="json_schema",
                include_raw=True,
            )
        except TypeError:
            # Older adapters and lightweight test doubles may not expose
            # include_raw. Production requests it so plan evaluation can prove
            # that every outbound request has usage and model metadata.
            structured_model = self.chat_model.with_structured_output(
                schema,
                method="json_schema",
            )
        if hasattr(structured_model, "bind"):
            structured_model = structured_model.bind(
                max_tokens=PLAN_CONTEXT_POLICY.max_output_tokens
            )
        self._begin_provider_attempt()
        result = structured_model.invoke(prompt)
        wrapped = isinstance(result, dict) and any(
            key in result for key in ("raw", "parsed", "parsing_error")
        )
        raw = result.get("raw") if wrapped else None
        parsed = result.get("parsed") if wrapped else result
        publish_provider_response(raw or result)
        if wrapped and result.get("parsing_error") is not None:
            raise ValueError("structured interview plan response failed parsing")
        if parsed is None:
            raise ValueError("structured interview plan response has no parsed value")
        if isinstance(parsed, schema):
            return parsed
        return schema.model_validate(parsed)

    def _invoke_raw_json_plan(
        self,
        prompt: str,
        *,
        force_context_enforcement: bool = False,
    ) -> dict[str, Any]:
        fallback_prompt = (
            f"{prompt}\n\n"
            "Return valid JSON only. Use the JSON shape exactly. "
            "Do not wrap the JSON in markdown code fences."
        )
        self._guard_prompt(
            fallback_prompt,
            PLAN_CONTEXT_POLICY,
            force_enforcement=force_context_enforcement,
        )
        message = self._invoke_chat(fallback_prompt, PLAN_CONTEXT_POLICY)
        content = str(getattr(message, "content", message)).strip()
        return self._parse_raw_json_payload(content)

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        assert_principal_memory_sink(
            operation=FOLLOWUP_GENERATION_SINK,
            payload=context,
        )
        if context_enforcement_enabled(FOLLOWUP_CONTEXT_POLICY.operation):
            context = self._fit_followup_context(context)
        prompt = _build_followup_prompt(context)
        self._guard_prompt(prompt, FOLLOWUP_CONTEXT_POLICY)
        message = self._invoke_chat(prompt, FOLLOWUP_CONTEXT_POLICY)
        return str(getattr(message, "content", message)).strip()

    def stream_followup(self, context: list[dict[str, str]]) -> Iterator[str]:
        assert_principal_memory_sink(
            operation=FOLLOWUP_GENERATION_SINK,
            payload=context,
        )
        if context_enforcement_enabled(FOLLOWUP_CONTEXT_POLICY.operation):
            context = self._fit_followup_context(context)
        prompt = _build_followup_prompt(context)
        self._guard_prompt(prompt, FOLLOWUP_CONTEXT_POLICY)
        for chunk in self._stream_chat(prompt, FOLLOWUP_CONTEXT_POLICY):
            text = str(getattr(chunk, "content", "") or "")
            if text:
                yield text

    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> "InterviewReport":
        from app.services.report import ReportGenerationFailed, ReportOutputFormatError
        from app.services.report_provider_adapter import ProviderQuestionResultsEnvelope

        provider_evaluation_items = _provider_visible_report_items(
            evaluation_items
        )
        assert_principal_memory_sink(
            operation="report_generation",
            payload={"plan": plan, "evaluation_items": provider_evaluation_items},
        )
        prompt = self._build_report_prompt(
            plan=plan,
            evaluation_items=provider_evaluation_items,
            session_id=session_id,
        )
        self._guard_prompt(prompt, REPORT_CONTEXT_POLICY)
        structured_error: Exception | None = None
        if self.report_output_mode == "structured_first":
            try:
                provider_payload = self._invoke_structured_report(
                    prompt,
                    ProviderQuestionResultsEnvelope,
                )
                return self._normalize_and_assemble_report(
                    provider_payload,
                    evaluation_items,
                    session_id=session_id,
                )
            except ReportOutputFormatError as exc:
                structured_error = exc
                self._record_trace(
                    session_id,
                    "structured_output_error",
                    {"error_code": type(exc).__name__},
                )
                logger.warning(
                    "Structured report output was invalid",
                    extra={
                        "session_id": session_id,
                        "error_code": type(exc).__name__,
                    },
                )
            except Exception as exc:
                structured_error = exc
                self._record_trace(
                    session_id,
                    "structured_output_error",
                    {"error_code": type(exc).__name__},
                )
                logger.warning(
                    "Structured report output failed, trying raw JSON path",
                    extra={
                        "session_id": session_id,
                        "error_code": type(exc).__name__,
                    },
                )

        try:
            provider_payload = self._invoke_raw_json_report(
                prompt,
                session_id=session_id,
            )
            return self._normalize_and_assemble_report(
                provider_payload,
                evaluation_items,
                session_id=session_id,
            )
        except ReportOutputFormatError as exc:
            self._record_trace(
                session_id,
                "report_output_format_error",
                {"error_code": type(exc).__name__},
            )
            raise
        except Exception as exc:
            raise self._classify_report_failure(exc, structured_error) from exc

    def _build_report_prompt(
        self,
        *,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> str:
        expected_shape = {
            "session_id": session_id,
            "question_results": [
                {
                    "question_id": "q1",
                    "dimension_evidence": [
                        {
                            "dimension": "depth",
                            "observed": [
                                "Candidate explained the concrete mechanism present in their answer."
                            ],
                            "missing": [
                                "Candidate did not explain the failure mode."
                            ],
                            "quality_signals": [],
                        }
                    ],
                    "rationale": "Explain the evidence in Simplified Chinese.",
                    "critique": "State the biggest missing point in Simplified Chinese.",
                    "reference_chunk_ids": ["redis-1", "redis-2"],
                    "highlights": ["Mentioned cache-aside tradeoffs."],
                }
            ],
        }
        return (
            f"Evidence prompt version: {REPORT_EVIDENCE_PROMPT_VERSION}.\n"
            "You are a strict technical interview coach.\n"
            "Return valid JSON only. Do not return markdown.\n"
            "Return exactly one question_results item for each evaluation item.\n"
            "All user-facing fields must be written in Simplified Chinese.\n"
            "Keep literal identifiers like Redis, Kafka, MySQL, p95, and API names unchanged when needed.\n"
            "When non_authoritative_reference_context is present, only use reference_chunk_ids listed there; otherwise use ids from the supplied evaluation_items references.\n"
            "Non-authoritative reference context is guidance only: never treat it as a candidate exact quote or authoritative scoring evidence.\n"
            "Do not invent new chunk ids.\n"
            "The backend computes all numeric scores from evidence.\n"
            "Do not return score or dimension_scores for any question.\n"
            "Do not return overall_score, overall_dimension_scores, summary, or reference objects.\n"
            "Do not return better_answer or any rewritten candidate experience; the backend derives bounded answer guidance.\n"
            "For each question, return exactly one dimension_evidence item for every applicable dimension listed in the evaluation item context.\n"
            "If an applicable dimension has no support, return observed as an empty list and explain the missing evidence in missing.\n"
            "Do not merge evidence for several dimensions into one dimension item.\n"
            "Each observed item must be a short continuous excerpt copied from the candidate answer.\n"
            "Do not prefix observed excerpts with phrases like candidate said, candidate explained, or the answer is clear.\n"
            "Do not put evaluator judgments, communication-quality summaries, or inferred capabilities in observed; put those only in rationale.\n"
            "Do not award evidence from the question text, job description, reference answer, or benchmark alone.\n"
            "Always return quality_signals as an empty list; the backend derives scoring signals deterministically from the candidate answer.\n"
            "For evaluation items sourced from question_evaluation_record, preserve the supplied validated dimension_evidence observed excerpts exactly; do not invent replacement excerpts.\n"
            "Use this JSON shape exactly:\n"
            f"{json.dumps(expected_shape, ensure_ascii=False, indent=2)}\n\n"
            f"session_id: {session_id}\n\n"
            f"plan_title: {plan.title}\n\n"
            "questions:\n"
            f"{json.dumps([question.model_dump() for question in plan.questions], ensure_ascii=False, indent=2)}\n\n"
            "evaluation_items:\n"
            f"{json.dumps(evaluation_items, ensure_ascii=False, indent=2)}"
        )

    def _invoke_structured_report(self, prompt: str, schema):
        structured_model = self.chat_model.with_structured_output(
            schema,
            method="json_schema",
        )
        if hasattr(structured_model, "bind"):
            structured_model = structured_model.bind(
                max_tokens=REPORT_CONTEXT_POLICY.max_output_tokens
            )
        self._begin_provider_attempt()
        result = structured_model.invoke(prompt)
        publish_provider_response(result)
        return self._coerce_report_result(result, schema)

    def _invoke_raw_json_report(
        self,
        prompt: str,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        fallback_prompt = (
            f"{prompt}\n\n"
            "Return valid JSON only. Use the JSON shape exactly. "
            "Do not wrap the JSON in markdown code fences."
        )
        self._guard_prompt(fallback_prompt, REPORT_CONTEXT_POLICY)
        message = self._invoke_chat(fallback_prompt, REPORT_CONTEXT_POLICY)
        content = str(getattr(message, "content", message)).strip()
        self._record_trace(
            session_id,
            "raw_json",
            {"raw_content": content},
        )
        return self._parse_raw_json_payload(content)

    def _parse_raw_json_payload(self, content: str) -> dict[str, Any]:
        from app.services.report import ReportOutputFormatError

        try:
            return json.loads(_extract_json_object(content))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ReportOutputFormatError(
                f"raw LLM JSON response parsing failed: {exc}"
            ) from exc

    def _normalize_and_assemble_report(
        self,
        payload: Any,
        evaluation_items: list[dict],
        *,
        session_id: str,
    ):
        from app.services.report import ReportOutputFormatError
        from app.services.report_contract import assemble_interview_report
        from app.services.report_provider_adapter import normalize_provider_payload

        try:
            if isinstance(payload, dict):
                self._record_trace(
                    session_id,
                    "raw_payload",
                    {"payload": payload},
                )
            else:
                self._record_trace(
                    session_id,
                    "structured_payload",
                    {"payload": payload.model_dump(exclude_none=True)},
                )
            normalized = normalize_provider_payload(payload, evaluation_items)
            self._record_trace(
                session_id,
                "normalized_payload",
                {"payload": normalized.model_dump()},
            )
            return assemble_interview_report(
                session_id=session_id,
                question_results=normalized.question_results,
                reference_lookup=normalized.reference_lookup,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise ReportOutputFormatError(
                f"provider payload normalization failed: {exc}"
            ) from exc

    def _coerce_report_result(self, result, schema):
        from app.services.report import ReportOutputFormatError

        if isinstance(result, schema):
            return result
        try:
            return schema.model_validate(result)
        except ValidationError as exc:
            raise ReportOutputFormatError(
                f"structured output schema validation failed: {exc}"
            ) from exc

    @staticmethod
    def _classify_report_failure(exc: Exception, prior_error: Exception | None):
        from app.services.report import ReportGenerationFailed

        message = str(exc)
        if prior_error is not None:
            message = f"{message}; structured_error={prior_error}"
        return ReportGenerationFailed(message)

    def _record_trace(self, session_id: str, stage: str, payload: dict[str, Any]) -> None:
        try:
            self.trace_recorder.record(
                session_id=session_id,
                stage=stage,
                payload=payload,
            )
        except Exception:
            logger.debug(
                "Failed to record report trace artifact",
                extra={"session_id": session_id, "stage": stage},
            )

    @staticmethod
    def _build_chat_model(config: LLMConfig):
        from langchain_openai import ChatOpenAI

        kwargs = {
            "api_key": config.api_key,
            "model": config.model,
            "temperature": config.temperature,
            "timeout": config.request_timeout_seconds,
            "max_retries": config.max_retries,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        transport_mode = str(
            environment_value("T65_PROVIDER_TRANSPORT_MODE", "")
        ).strip()
        if transport_mode:
            if transport_mode != "builtin_production":
                raise ContextConfigurationError(
                    "unsupported T65 Provider transport mode"
                )
            if (
                config.model != "deepseek-v4-pro"
                or (config.base_url or "").rstrip("/")
                != "https://api.deepseek.com"
                or config.max_retries != 0
            ):
                raise ContextConfigurationError(
                    "formal T65 transport requires the frozen DeepSeek model, endpoint, and zero SDK retries"
                )
            from app.services.t65_provider_http_transport import (
                get_t65_provider_http_clients,
            )

            clients = get_t65_provider_http_clients()
            kwargs["http_client"] = clients.sync_client
            kwargs["http_async_client"] = clients.async_client
            kwargs["http_socket_options"] = ()
        return ChatOpenAI(**kwargs)

    def _guard_prompt(
        self,
        prompt: str,
        policy: OperationContextPolicy,
        *,
        force_enforcement: bool = False,
    ):
        budget = self._budget_resolver.resolve(
            profile=self.model_profile,
            policy=policy,
        )
        measurement = self._prompt_guard.measure(
            prompt=prompt,
            budget=budget,
            estimator=self.token_estimator,
        )
        publish_prompt_measurement(
            measurement,
            language_bucket=classify_context_language(prompt),
        )
        # Publish the privacy-safe measurement before enforcement. Calling
        # validate() directly would raise before rejected requests can be
        # observed by Agent telemetry and Context Canary.
        if force_enforcement or context_enforcement_enabled(policy.operation):
            self._prompt_guard.enforce(measurement, budget=budget)
        return measurement

    def _fit_followup_context(
        self,
        context: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        budget = self._budget_resolver.resolve(
            profile=self.model_profile,
            policy=FOLLOWUP_CONTEXT_POLICY,
        )
        try:
            selection_budget = self._budget_resolver.resolve_selection_budget(
                budget=budget,
                policy=FOLLOWUP_CONTEXT_POLICY,
            )
        except ContextConfigurationError:
            # The rendered prompt guard below remains authoritative and emits
            # ContextBudgetExceeded when even the fixed prompt cannot fit.
            return [dict(item) for item in context]

        assistance = [
            dict(item)
            for item in context
            if item.get("role") == "system"
            and item.get("context_kind") == ASSISTANCE_CONTEXT_KIND
            and str(item.get("content", "")).startswith(
                "[Non-authoritative historical preference]\n"
            )
            and str(item.get("content", "")).endswith(
                "[/Non-authoritative historical preference]"
            )
        ]
        if len(assistance) != 1:
            assistance = []
        status_candidates = [
            dict(item)
            for item in context
            if item.get("role") == INTERVIEW_STATUS_ROLE
            and is_valid_interview_status_message(item)
        ]
        status_message = (
            status_candidates[0] if len(status_candidates) == 1 else None
        )
        conversation = [
            dict(item)
            for item in context
            if item.get("role") not in {"knowledge_agent", "knowledge_evidence"}
            and item.get("context_kind") != ASSISTANCE_CONTEXT_KIND
            and item.get("role") != INTERVIEW_STATUS_ROLE
        ]
        evidence = [
            dict(item)
            for item in context
            if item.get("role") in {"knowledge_agent", "knowledge_evidence"}
        ]
        latest_candidate = next(
            (
                index
                for index in range(len(conversation) - 1, -1, -1)
                if conversation[index].get("role") == "candidate"
            ),
            None,
        )
        current_interviewer = (
            next(
                (
                    index
                    for index in range(latest_candidate - 1, -1, -1)
                    if conversation[index].get("role") == "interviewer"
                ),
                None,
            )
            if latest_candidate is not None
            else None
        )
        normalized = []
        for index, item in enumerate(conversation):
            question_id = (
                "current"
                if index in {latest_candidate, current_interviewer}
                else f"history-{index}"
            )
            normalized.append({**item, "question_id": question_id})
        selected, _ = build_interview_context(
            normalized,
            current_question_id="current",
            evidence_messages=evidence,
            policy=FOLLOWUP_CONTEXT_POLICY,
            selection_budget=selection_budget,
            estimator=self.token_estimator.estimator,
            model=self.model_profile.model,
        )
        if assistance:
            candidate_index = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if selected[index].get("role") == "candidate"
                ),
                None,
            )
            if candidate_index is not None:
                candidate = selected[candidate_index]
                with_assistance = [
                    *selected[:candidate_index],
                    *selected[candidate_index + 1 :],
                    assistance[0],
                    candidate,
                ]
                cost = self.token_estimator.estimator.estimate_messages(
                    with_assistance,
                    model=self.model_profile.model,
                )
                if cost <= selection_budget.selectable_content_tokens:
                    selected = with_assistance
        if status_message is not None:
            return self._fit_status_prefixed_followup_context(
                status_message=status_message,
                selected=selected,
                available_input_tokens=budget.available_input_tokens,
            )
        return selected

    def _fit_status_prefixed_followup_context(
        self,
        *,
        status_message: dict[str, str],
        selected: list[dict[str, str]],
        available_input_tokens: int,
    ) -> list[dict[str, str]]:
        fitted = [dict(status_message), *[dict(item) for item in selected]]
        estimator = self.token_estimator.estimator
        model = self.model_profile.model

        def fits(items):
            return estimator.estimate_text(
                _build_followup_prompt(items),
                model=model,
            ) <= available_input_tokens

        if fits(fitted):
            return fitted
        latest_candidate = next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if selected[index].get("role") == "candidate"
            ),
            None,
        )
        current_interviewer = (
            next(
                (
                    index
                    for index in range(latest_candidate - 1, -1, -1)
                    if selected[index].get("role") == "interviewer"
                ),
                None,
            )
            if latest_candidate is not None
            else None
        )
        mandatory_indexes = {latest_candidate, current_interviewer} - {None}
        retained = [
            (index, dict(item)) for index, item in enumerate(selected)
        ]
        for index in range(len(selected)):
            if index in mandatory_indexes:
                continue
            retained = [item for item in retained if item[0] != index]
            candidate = [
                dict(status_message),
                *[item for _source_index, item in retained],
            ]
            if fits(candidate):
                return candidate
        # Status is optional relative to the Task-7-preexisting mandatory
        # business context. If the minimum status-prefixed business set cannot
        # fit, omit status and preserve the exact prior fitted context. The
        # existing rendered prompt guard remains authoritative when the
        # business context itself cannot fit.
        return [dict(item) for item in selected]

    def _fit_plan_inputs(
        self,
        *,
        job_description: str,
        resume_text: str,
        knowledge_context: list[dict] | None,
    ) -> tuple[str, str, list[dict] | None]:
        budget = self._budget_resolver.resolve(
            profile=self.model_profile,
            policy=PLAN_CONTEXT_POLICY,
        )
        # Reserve 20% for fixed instructions, the response schema and JSON
        # framing. The final rendered-prompt guard remains authoritative.
        content_budget = max(1, budget.available_input_tokens * 80 // 100)
        jd_budget = max(1, content_budget * 35 // 100)
        resume_budget = max(1, content_budget * 50 // 100)
        knowledge_budget = max(1, content_budget - jd_budget - resume_budget)
        estimator = self.token_estimator.estimator
        job_description, _ = truncate_text_to_tokens(
            job_description,
            token_budget=jd_budget,
            estimator=estimator,
            model=self.model_profile.model,
        )
        resume_text, _ = truncate_text_to_tokens(
            resume_text,
            token_budget=resume_budget,
            estimator=estimator,
            model=self.model_profile.model,
        )
        selected_knowledge: list[dict] = []
        remaining = knowledge_budget
        for item in knowledge_context or []:
            serialized = json.dumps(item, ensure_ascii=False, sort_keys=True)
            cost = estimator.estimate_text(
                serialized,
                model=self.model_profile.model,
            )
            if cost > remaining:
                break
            selected_knowledge.append(item)
            remaining -= cost
        return (
            job_description,
            resume_text,
            selected_knowledge or None,
        )

    def _invoke_chat(self, prompt: str, policy: OperationContextPolicy):
        model = self.chat_model
        if hasattr(model, "bind"):
            model = model.bind(max_tokens=policy.max_output_tokens)
        self._begin_provider_attempt()
        response = model.invoke(prompt)
        publish_provider_response(response)
        return response

    def _stream_chat(self, prompt: str, policy: OperationContextPolicy):
        model = self.chat_model
        if hasattr(model, "bind"):
            model = model.bind(max_tokens=policy.max_output_tokens)
        self._begin_provider_attempt()

        def iterate():
            for chunk in model.stream(prompt):
                publish_provider_response(chunk)
                yield chunk

        return iterate()

    def _begin_provider_attempt(self) -> None:
        if self._provider_attempt_hook is not None:
            self._provider_attempt_hook()
        begin_provider_attempt()


def _provider_visible_report_items(
    evaluation_items: list[dict],
) -> list[dict]:
    provider_items = []
    for evaluation_item in evaluation_items:
        provider_item = dict(evaluation_item)
        if "non_authoritative_reference_context" in provider_item:
            provider_item.pop("scoring_references", None)
            provider_item.pop("answer_references", None)
        provider_items.append(provider_item)
    return provider_items


def _build_followup_prompt(context: list[dict[str, str]]) -> str:
    from app.services.followup_prompts import render_followup_generation_prompt

    return render_followup_generation_prompt(context)


def _extract_json_object(content: str) -> str:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM response did not contain a JSON object")
    return content[start : end + 1]
