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
    def __init__(self, config: LLMConfig | None = None, chat_model=None) -> None:
        self.config = config
        self.chat_model = chat_model or self._build_chat_model(config or LLMConfig.from_env())

    def generate_plan(self, job_description: str, resume_text: str):
        from app.services.prep import InterviewPlan

        prompt = (
            "浣犳槸涓€涓弗鏍肩殑鎶€鏈潰璇曞畼銆傝鏍规嵁鍊欓€変汉鐨?JD 鍜岀畝鍘嗙敓鎴愪竴浠界粨鏋勫寲闈㈣瘯澶х翰銆俓n"
            "瑕佹眰锛歕n"
            "1. 鑷冲皯鍖呭惈椤圭洰棰樸€佹妧鏈繁鎸栭銆佺郴缁熻璁￠涓夌被闂銆俓n"
            "2. 闂蹇呴』璐村悎鍊欓€変汉鐨勭湡瀹炵粡鍘嗭紝涓嶈鐢熸垚娉涙硾鑰岃皥鐨勯棶棰樸€俓n"
            "3. 杈撳嚭蹇呴』涓ユ牸绗﹀悎 InterviewPlan 鐨勭粨鏋勫寲瀛楁銆俓n\n"
            f"JD:\n{job_description}\n\n"
            f"绠€鍘?\n{resume_text}"
        )
        structured_model = self.chat_model.with_structured_output(
            InterviewPlan,
            method="json_schema",
        )
        return structured_model.invoke(prompt)

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
        from app.services.report import InterviewReport
        from app.services.report import ReportGenerationFailed, ReportOutputFormatError

        prompt = self._build_report_prompt(
            plan=plan,
            evaluation_items=evaluation_items,
            session_id=session_id,
        )
        structured_error: Exception | None = None
        try:
            return self._invoke_structured_report(prompt, InterviewReport)
        except ReportOutputFormatError as exc:
            structured_error = exc
            logger.warning(
                "Structured report output was invalid",
                extra={"session_id": session_id, "reason": str(exc)},
            )
        except Exception as exc:
            structured_error = exc
            logger.warning(
                "Structured report output failed, trying raw JSON path",
                extra={"session_id": session_id, "reason": str(exc)},
            )

        try:
            return self._invoke_raw_json_report(prompt, InterviewReport, evaluation_items)
        except ReportOutputFormatError:
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
        return (
            "You are a strict technical interview coach. Generate a structured "
            "expert interview report.\n"
            "Rules:\n"
            "1. Use only the supplied transcript and retrieved references.\n"
            "2. Return one feedback item for every evaluation item.\n"
            "3. Scores must be integers from 0 to 100.\n"
            "4. dimension_scores must include breadth, depth, architecture, "
            "engineering, communication.\n"
            "5. rationale must explain the scoring and mention reference gaps "
            "when relevant.\n"
            "6. references must only include retrieved evidence.\n"
            "7. Keep highlights to one to three items.\n\n"
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

    def _invoke_raw_json_report(self, prompt: str, schema, evaluation_items: list[dict]):
        fallback_prompt = (
            f"{prompt}\n\n"
            "Return valid JSON only. The JSON must match the InterviewReport schema exactly. "
            "Do not wrap the JSON in markdown code fences."
        )
        message = self.chat_model.invoke(fallback_prompt)
        content = str(getattr(message, "content", message)).strip()
        return self._validate_report_json_content(content, schema, evaluation_items)

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

    def _validate_report_json_content(
        self,
        content: str,
        schema,
        evaluation_items: list[dict],
    ):
        from app.services.report import ReportOutputFormatError

        try:
            payload = json.loads(_extract_json_object(content))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ReportOutputFormatError(
                f"raw report JSON schema validation failed: {exc}"
            ) from exc

        try:
            return schema.model_validate(payload)
        except ValidationError:
            normalized_payload = self._normalize_report_payload(payload, evaluation_items)

        try:
            return schema.model_validate(normalized_payload)
        except ValidationError as exc:
            raise ReportOutputFormatError(
                f"raw report JSON schema validation failed: {exc}"
            ) from exc

    def _normalize_report_payload(self, payload: Any, evaluation_items: list[dict]) -> Any:
        if not isinstance(payload, dict):
            return payload

        normalized = dict(payload)
        overall_dimension_scores = normalized.get("overall_dimension_scores")
        if not isinstance(overall_dimension_scores, dict):
            provider_dimension_scores = normalized.get("dimension_scores")
            if isinstance(provider_dimension_scores, dict):
                overall_dimension_scores = dict(provider_dimension_scores)
                normalized["overall_dimension_scores"] = overall_dimension_scores

        feedbacks = normalized.get("feedbacks")
        if not isinstance(feedbacks, list):
            provider_feedbacks = normalized.get("feedback_items")
            if not isinstance(provider_feedbacks, list):
                provider_feedbacks = normalized.get("evaluation_results")
            if isinstance(provider_feedbacks, list):
                feedbacks = provider_feedbacks

        reference_lookup = self._build_reference_lookup(normalized.get("references"))
        evaluation_item_lookup = {
            item.get("question_id"): item
            for item in evaluation_items
            if isinstance(item, dict) and isinstance(item.get("question_id"), str)
        }
        for evaluation_item in evaluation_item_lookup.values():
            self._add_reference_items(
                reference_lookup,
                evaluation_item.get("scoring_references"),
            )
            self._add_reference_items(
                reference_lookup,
                evaluation_item.get("answer_references"),
            )

        if isinstance(feedbacks, list):
            normalized["feedbacks"] = [
                self._normalize_feedback_item(
                    feedback,
                    evaluation_item_lookup,
                    reference_lookup,
                    overall_dimension_scores,
                )
                for feedback in feedbacks
            ]

        if not isinstance(normalized.get("highlights"), list):
            highlights = self._derive_report_highlights(normalized.get("feedbacks"))
            if highlights:
                normalized["highlights"] = highlights

        if not isinstance(normalized.get("overall_dimension_scores"), dict):
            derived_dimension_scores = self._derive_overall_dimension_scores(
                normalized.get("feedbacks")
            )
            if derived_dimension_scores is not None:
                normalized["overall_dimension_scores"] = derived_dimension_scores

        if not normalized.get("summary"):
            summary = self._derive_report_summary(
                normalized.get("highlights"),
                normalized.get("feedbacks"),
            )
            if summary:
                normalized["summary"] = summary

        if not isinstance(normalized.get("overall_score"), int):
            overall_score = self._derive_score_from_dimension_scores(
                normalized.get("overall_dimension_scores")
            )
            if overall_score is not None:
                normalized["overall_score"] = overall_score

        return normalized

    def _normalize_feedback_item(
        self,
        feedback: Any,
        evaluation_item_lookup: dict[str, dict],
        reference_lookup: dict[str, dict[str, str]],
        overall_dimension_scores: Any,
    ) -> Any:
        if not isinstance(feedback, dict):
            return feedback

        normalized = dict(feedback)
        question_id = normalized.get("question_id")
        evaluation_item = (
            evaluation_item_lookup.get(question_id)
            if isinstance(question_id, str)
            else None
        )
        resolved_references = self._resolve_feedback_references(
            normalized.get("references"),
            reference_lookup,
            feedback,
            evaluation_item,
        )
        normalized["references"] = resolved_references

        if not normalized.get("question_text") and evaluation_item:
            question_text = str(evaluation_item.get("question_text") or "").strip()
            if question_text:
                normalized["question_text"] = question_text

        if not normalized.get("user_answer"):
            user_answer = self._build_user_answer(evaluation_item)
            if user_answer:
                normalized["user_answer"] = user_answer

        if not isinstance(normalized.get("dimension_scores"), dict):
            score = normalized.get("score")
            if isinstance(score, int):
                normalized["dimension_scores"] = _uniform_dimension_scores(score)
            elif isinstance(overall_dimension_scores, dict):
                normalized["dimension_scores"] = dict(overall_dimension_scores)

        if not isinstance(normalized.get("score"), int):
            score = self._derive_score_from_dimension_scores(
                normalized.get("dimension_scores")
            )
            if score is not None:
                normalized["score"] = score

        if not normalized.get("rationale"):
            rationale = self._build_feedback_rationale(normalized)
            if rationale:
                normalized["rationale"] = rationale

        if not normalized.get("critique"):
            critique = self._build_feedback_critique(normalized)
            if critique:
                normalized["critique"] = critique

        if not normalized.get("better_answer"):
            suggested_improvements = str(normalized.get("suggested_improvements") or "").strip()
            if suggested_improvements:
                normalized["better_answer"] = suggested_improvements

        if not normalized.get("better_answer"):
            better_answer = self._select_better_answer(
                evaluation_item,
                resolved_references,
                reference_lookup,
            )
            if better_answer:
                normalized["better_answer"] = better_answer
            elif normalized.get("user_answer"):
                normalized["better_answer"] = normalized["user_answer"]

        return normalized

    @staticmethod
    def _build_reference_lookup(references: Any) -> dict[str, dict[str, str]]:
        lookup: dict[str, dict[str, str]] = {}
        OpenAIInterviewLLM._add_reference_items(lookup, references)
        return lookup

    @staticmethod
    def _resolve_feedback_references(
        references: Any,
        reference_lookup: dict[str, dict[str, str]],
        feedback: dict[str, Any],
        evaluation_item: dict | None,
    ) -> list[dict[str, str]]:
        reference_ids = OpenAIInterviewLLM._collect_reference_ids(
            references,
            feedback,
            evaluation_item,
        )

        resolved: list[dict[str, str]] = []
        seen_chunk_ids: set[str] = set()
        for reference_id in reference_ids:
            matched_reference = reference_lookup.get(reference_id)
            if matched_reference is None:
                continue
            chunk_id = str(matched_reference.get("chunk_id") or "").strip()
            if not chunk_id or chunk_id in seen_chunk_ids:
                continue
            resolved.append(dict(matched_reference))
            seen_chunk_ids.add(chunk_id)
        return resolved

    @staticmethod
    def _build_user_answer(evaluation_item: dict | None) -> str | None:
        if not isinstance(evaluation_item, dict):
            return None

        messages = evaluation_item.get("messages")
        if not isinstance(messages, list):
            return None

        preferred_roles = {"candidate", "user"}
        candidate_messages = [
            str(message.get("content") or "").strip()
            for message in messages
            if isinstance(message, dict)
            and message.get("role") in preferred_roles
            and str(message.get("content") or "").strip()
        ]
        if candidate_messages:
            return "\n".join(candidate_messages)

        any_messages = [
            str(message.get("content") or "").strip()
            for message in messages
            if isinstance(message, dict) and str(message.get("content") or "").strip()
        ]
        if any_messages:
            return "\n".join(any_messages)
        return None

    @staticmethod
    def _select_better_answer(
        evaluation_item: dict | None,
        resolved_references: list[dict[str, str]],
        reference_lookup: dict[str, dict[str, str]],
    ) -> str | None:
        if isinstance(evaluation_item, dict):
            answer_references = evaluation_item.get("answer_references")
            if isinstance(answer_references, list):
                for answer_reference in answer_references:
                    if not isinstance(answer_reference, dict):
                        continue
                    chunk_id = answer_reference.get("chunk_id")
                    if not isinstance(chunk_id, str):
                        continue
                    matched_reference = reference_lookup.get(chunk_id)
                    excerpt = str(matched_reference.get("excerpt") or "").strip() if matched_reference else ""
                    if excerpt:
                        return excerpt

        for reference in resolved_references:
            excerpt = str(reference.get("excerpt") or "").strip()
            if excerpt:
                return excerpt
        return None

    @staticmethod
    def _derive_report_summary(highlights: Any, feedbacks: Any) -> str | None:
        if isinstance(highlights, list):
            summary = " ".join(
                str(highlight).strip()
                for highlight in highlights
                if str(highlight).strip()
            ).strip()
            if summary:
                return summary

        if isinstance(feedbacks, list):
            for feedback in feedbacks:
                if not isinstance(feedback, dict):
                    continue
                rationale = str(feedback.get("rationale") or "").strip()
                if rationale:
                    return rationale
        return None

    @staticmethod
    def _derive_report_highlights(feedbacks: Any) -> list[str]:
        if not isinstance(feedbacks, list):
            return []

        highlights: list[str] = []
        for feedback in feedbacks:
            if not isinstance(feedback, dict):
                continue
            raw_highlights = feedback.get("highlights")
            if not isinstance(raw_highlights, list):
                continue
            for highlight in raw_highlights:
                text = str(highlight).strip()
                if text and text not in highlights:
                    highlights.append(text)
                    if len(highlights) == 3:
                        return highlights
        return highlights

    @staticmethod
    def _derive_overall_dimension_scores(feedbacks: Any) -> dict[str, int] | None:
        if not isinstance(feedbacks, list):
            return None

        keys = ("breadth", "depth", "architecture", "engineering", "communication")
        totals = {key: 0 for key in keys}
        count = 0

        for feedback in feedbacks:
            if not isinstance(feedback, dict):
                continue
            dimension_scores = feedback.get("dimension_scores")
            if not isinstance(dimension_scores, dict):
                continue
            values: dict[str, int] = {}
            for key in keys:
                value = dimension_scores.get(key)
                if not isinstance(value, (int, float)):
                    values = {}
                    break
                values[key] = int(value)
            if not values:
                continue
            count += 1
            for key in keys:
                totals[key] += values[key]

        if count == 0:
            return None
        return {key: round(totals[key] / count) for key in keys}

    @staticmethod
    def _build_feedback_rationale(feedback: dict[str, Any]) -> str | None:
        parts: list[str] = []

        strengths = feedback.get("strengths")
        if isinstance(strengths, list):
            items = [str(item).strip() for item in strengths if str(item).strip()]
            if items:
                parts.append("Strengths: " + " ".join(items))

        weaknesses = feedback.get("weaknesses")
        if isinstance(weaknesses, list):
            items = [str(item).strip() for item in weaknesses if str(item).strip()]
            if items:
                parts.append("Weaknesses: " + " ".join(items))

        gaps = feedback.get("gaps")
        if isinstance(gaps, list):
            items = []
            for gap in gaps:
                if not isinstance(gap, dict):
                    continue
                missing = str(gap.get("missing") or "").strip()
                if missing:
                    items.append(missing)
            if items:
                parts.append("Reference gaps: " + " ".join(items))

        rationale = " ".join(parts).strip()
        return rationale or None

    @staticmethod
    def _build_feedback_critique(feedback: dict[str, Any]) -> str | None:
        weaknesses = feedback.get("weaknesses")
        if isinstance(weaknesses, list):
            for weakness in weaknesses:
                text = str(weakness).strip()
                if text:
                    return text

        rationale = str(feedback.get("rationale") or "").strip()
        return rationale or None

    @staticmethod
    def _derive_score_from_dimension_scores(dimension_scores: Any) -> int | None:
        if not isinstance(dimension_scores, dict):
            return None

        values = [
            int(value)
            for value in dimension_scores.values()
            if isinstance(value, (int, float))
        ]
        if not values:
            return None
        return round(sum(values) / len(values))

    @staticmethod
    def _collect_reference_ids(
        references: Any,
        feedback: dict[str, Any],
        evaluation_item: dict | None,
    ) -> list[str]:
        reference_ids: list[str] = []

        if isinstance(references, list):
            for reference in references:
                if isinstance(reference, str) and reference not in reference_ids:
                    reference_ids.append(reference)
                elif isinstance(reference, dict):
                    chunk_id = str(reference.get("chunk_id") or "").strip()
                    if chunk_id and chunk_id not in reference_ids:
                        reference_ids.append(chunk_id)

        gaps = feedback.get("gaps")
        if isinstance(gaps, list):
            for gap in gaps:
                if not isinstance(gap, dict):
                    continue
                chunk_id = str(gap.get("reference_chunk_id") or "").strip()
                if chunk_id and chunk_id not in reference_ids:
                    reference_ids.append(chunk_id)

        if not reference_ids and isinstance(evaluation_item, dict):
            for key in ("scoring_references", "answer_references"):
                candidate_references = evaluation_item.get(key)
                if not isinstance(candidate_references, list):
                    continue
                for reference in candidate_references:
                    if not isinstance(reference, dict):
                        continue
                    chunk_id = str(reference.get("chunk_id") or "").strip()
                    if chunk_id and chunk_id not in reference_ids:
                        reference_ids.append(chunk_id)

        return reference_ids

    @staticmethod
    def _add_reference_items(
        lookup: dict[str, dict[str, str]],
        references: Any,
    ) -> None:
        if not isinstance(references, list):
            return

        for reference in references:
            if not isinstance(reference, dict):
                continue
            chunk_id = str(reference.get("chunk_id") or "").strip()
            if not chunk_id:
                continue
            title = str(reference.get("title") or chunk_id).strip()
            source_type = str(reference.get("source_type") or "reference").strip()
            excerpt = str(
                reference.get("excerpt")
                or reference.get("content")
                or reference.get("missing")
                or title
            ).strip()
            lookup[chunk_id] = {
                "chunk_id": chunk_id,
                "title": title,
                "source_type": source_type,
                "excerpt": excerpt,
            }

    @staticmethod
    def _classify_report_failure(exc: Exception, prior_error: Exception | None):
        from app.services.report import ReportGenerationFailed

        message = str(exc)
        if prior_error is not None:
            message = f"{message}; structured_error={prior_error}"
        return ReportGenerationFailed(message)

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
        "浣犳槸涓€涓湁鍘嬭揩鎰熶絾涓撲笟鐨勬妧鏈潰璇曞畼銆傝鏍规嵁涓嬮潰鏈€杩戝嚑杞潰璇曚笂涓嬫枃锛?"
        "鍙敓鎴愪竴涓皷閿愮殑杩介棶銆俓n"
        "瑕佹眰锛歕n"
        "1. 杩介棶蹇呴』鍩轰簬鍊欓€変汉鍒氭墠鐨勫洖绛斻€俓n"
        "2. 浼樺厛杩介棶鍙栬垗銆佽竟鐣屾潯浠躲€佹晠闅滃厹搴曘€佹€ц兘鐡堕鎴栨簮鐮佸師鐞嗐€俓n"
        "3. 涓嶈杈撳嚭瑙ｉ噴锛屼笉瑕佽緭鍑哄閬撻锛屽彧杈撳嚭杩介棶鏈韩銆俓n\n"
        f"涓婁笅鏂?\n{transcript}"
    )


def _extract_json_object(content: str) -> str:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("report response did not contain a JSON object")
    return content[start : end + 1]


def _uniform_dimension_scores(score: int) -> dict[str, int]:
    return {
        "breadth": score,
        "depth": score,
        "architecture": score,
        "engineering": score,
        "communication": score,
    }
