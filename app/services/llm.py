import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Protocol

from pydantic import ValidationError

if TYPE_CHECKING:
    from app.services.report import InterviewReport

logger = logging.getLogger(__name__)


class MissingLLMConfigError(RuntimeError):
    """LLM configuration is missing, usually OPENAI_API_KEY."""


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = "deepseek-v4-pro"
    base_url: str | None = None
    temperature: float = 0.2

    @classmethod
    def from_env(cls) -> "LLMConfig":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise MissingLLMConfigError("OPENAI_API_KEY is required")

        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "deepseek-v4-pro"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.2")),
        )


class InterviewLLM(Protocol):
    def generate_plan(self, job_description: str, resume_text: str):
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
    ) -> None:
        from app.services.report_trace import ReportTraceRecorder

        self.config = config
        self.chat_model = chat_model or self._build_chat_model(config or LLMConfig.from_env())
        self.trace_recorder = trace_recorder or ReportTraceRecorder.from_env()

    def generate_plan(self, job_description: str, resume_text: str):
        from app.services.prep import InterviewPlan

        prompt = self._build_plan_prompt(
            job_description=job_description,
            resume_text=resume_text,
        )
        try:
            return self._invoke_structured_plan(prompt, InterviewPlan)
        except Exception as exc:
            logger.warning(
                "Structured interview plan output failed, trying raw JSON path",
                extra={"reason": str(exc)},
            )

        payload = self._invoke_raw_json_plan(prompt)
        try:
            return InterviewPlan.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"raw interview plan JSON schema validation failed: {exc}") from exc

    def _build_plan_prompt(self, *, job_description: str, resume_text: str) -> str:
        expected_shape = {
            "title": "Backend interview plan",
            "questions": [
                {
                    "id": "q1",
                    "kind": "project",
                    "prompt": "Ask one concrete interview question.",
                    "focus": "What this question evaluates.",
                },
                {
                    "id": "q2",
                    "kind": "technical",
                    "prompt": "Ask one concrete interview question.",
                    "focus": "What this question evaluates.",
                },
                {
                    "id": "q3",
                    "kind": "system-design",
                    "prompt": "Ask one concrete interview question.",
                    "focus": "What this question evaluates.",
                },
            ],
        }
        return (
            "You are a senior technical interviewer.\n"
            "Create a focused mock interview plan from the job description and resume.\n"
            "Return exactly 3 to 5 questions.\n"
            "Each question kind must be one of: project, technical, system-design, behavioral.\n"
            "Use stable ids q1, q2, q3, and continue in order if more questions are needed.\n"
            "Questions should be specific to the candidate's resume and the target job.\n"
            "Return valid JSON only. Do not return markdown.\n"
            "Use this JSON shape exactly:\n"
            f"{json.dumps(expected_shape, ensure_ascii=False, indent=2)}\n\n"
            f"Job description:\n{job_description}\n\n"
            f"Resume:\n{resume_text}"
        )

    def _invoke_structured_plan(self, prompt: str, schema):
        structured_model = self.chat_model.with_structured_output(
            schema,
            method="json_schema",
        )
        result = structured_model.invoke(prompt)
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)

    def _invoke_raw_json_plan(self, prompt: str) -> dict[str, Any]:
        fallback_prompt = (
            f"{prompt}\n\n"
            "Return valid JSON only. Use the JSON shape exactly. "
            "Do not wrap the JSON in markdown code fences."
        )
        message = self.chat_model.invoke(fallback_prompt)
        content = str(getattr(message, "content", message)).strip()
        return self._parse_raw_json_payload(content)

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        prompt = _build_followup_prompt(context)
        message = self.chat_model.invoke(prompt)
        return str(getattr(message, "content", message)).strip()

    def stream_followup(self, context: list[dict[str, str]]) -> Iterator[str]:
        prompt = _build_followup_prompt(context)
        for chunk in self.chat_model.stream(prompt):
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

        prompt = self._build_report_prompt(
            plan=plan,
            evaluation_items=evaluation_items,
            session_id=session_id,
        )
        structured_error: Exception | None = None
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
                {"error": str(exc), "error_type": type(exc).__name__},
            )
            logger.warning(
                "Structured report output was invalid",
                extra={"session_id": session_id, "reason": str(exc)},
            )
        except Exception as exc:
            structured_error = exc
            self._record_trace(
                session_id,
                "structured_output_error",
                {"error": str(exc), "error_type": type(exc).__name__},
            )
            logger.warning(
                "Structured report output failed, trying raw JSON path",
                extra={"session_id": session_id, "reason": str(exc)},
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
                {"error": str(exc), "error_type": type(exc).__name__},
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
                    "score": 81,
                    "dimension_scores": {
                        "breadth": 80,
                        "depth": 78,
                        "architecture": 82,
                        "engineering": 84,
                        "communication": 81,
                    },
                    "rationale": "Tie the score to the candidate's actual answer and cited evidence.",
                    "critique": "State the biggest missing point.",
                    "better_answer": "Give a concise improved answer.",
                    "reference_chunk_ids": ["redis-1", "redis-2"],
                    "highlights": ["Mentioned cache-aside tradeoffs."],
                }
            ],
        }
        return (
            "You are a strict technical interview coach.\n"
            "Return valid JSON only. Do not return markdown.\n"
            "Return exactly one question_results item for each evaluation item.\n"
            "Only use reference_chunk_ids that appear in the supplied evaluation_items references.\n"
            "Do not invent new chunk ids.\n"
            "Do not return overall_score, overall_dimension_scores, summary, or reference objects.\n"
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
        result = structured_model.invoke(prompt)
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
        message = self.chat_model.invoke(fallback_prompt)
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
                exc_info=True,
            )

    @staticmethod
    def _build_chat_model(config: LLMConfig):
        from langchain_openai import ChatOpenAI

        kwargs = {
            "api_key": config.api_key,
            "model": config.model,
            "temperature": config.temperature,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return ChatOpenAI(**kwargs)


def _build_followup_prompt(context: list[dict[str, str]]) -> str:
    transcript = "\n".join(
        f"{item['role']}: {item['content']}" for item in context if item.get("content")
    )
    return (
        "You are a professional technical interviewer.\n"
        "Based on the recent interview context, ask exactly one sharp follow-up question.\n"
        "The follow-up must be grounded in the candidate's latest answer.\n"
        "Prefer tradeoffs, edge cases, fallback plans, performance bottlenecks, or source-code reasoning.\n"
        "Return only the follow-up question, without explanation.\n\n"
        f"Recent context:\n{transcript}"
    )


def _extract_json_object(content: str) -> str:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM response did not contain a JSON object")
    return content[start : end + 1]
