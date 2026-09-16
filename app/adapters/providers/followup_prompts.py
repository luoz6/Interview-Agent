from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.context.budget import FOLLOWUP_CONTEXT_POLICY
from app.domain.interview.decision_store import DecisionContract
from app.domain.interview.followup_prompts import (
    FOLLOWUP_DECISION_PROMPT_SHA256,
    FOLLOWUP_DECISION_PROMPT_VERSION,
    FOLLOWUP_GENERATION_PROMPT_SHA256,
    FOLLOWUP_GENERATION_PROMPT_VERSION,
    generation_context_for_decision,
    generation_context_for_target,
    render_followup_decision_prompt,
    render_followup_generation_prompt,
    resolve_followup_decision_output_mode,
)
from app.ports.followup_decision import (
    DecisionProviderResult,
    ProviderModelMismatchError as ProviderModelMismatchContract,
)
from app.runtime.provider_usage import (
    begin_provider_attempt,
    extract_provider_usage,
    publish_provider_response,
)

class StructuredFollowupOutputError(ValueError):
    """A Provider response arrived but did not satisfy DecisionContract."""

    def __init__(self, message: str, *, response: object) -> None:
        super().__init__(message)
        usage = extract_provider_usage(response) or {}
        self.input_tokens = usage.get("provider_input_tokens")
        self.output_tokens = usage.get("provider_output_tokens")
        self.cached_input_tokens = usage.get("provider_cached_input_tokens")
        self.provider_model = _provider_model(response)
        self.provider_response_id = _provider_response_id(response)


class ProviderModelMismatchError(
    StructuredFollowupOutputError, ProviderModelMismatchContract
):
    """A metered response identified a model outside the exact authorization."""



class StructuredFollowupDecisionProvider:
    prompt_version = FOLLOWUP_DECISION_PROMPT_VERSION
    prompt_sha256 = FOLLOWUP_DECISION_PROMPT_SHA256

    def __init__(
        self,
        chat_model,
        *,
        max_tokens: int = 300,
        output_mode: FollowupDecisionOutputMode = "structured_first",
        expected_model: str | None = None,
    ) -> None:
        if output_mode not in {"structured_first", "raw_only"}:
            raise ValueError(
                f"unsupported followup Decision output_mode: {output_mode}"
            )
        self.chat_model = chat_model
        self.max_tokens = max_tokens
        self.output_mode = output_mode
        self.expected_model = expected_model

    def __call__(self, context: dict[str, object]):
        if self.output_mode == "raw_only":
            return self._invoke_raw(context)
        return self._invoke_structured(context)

    def _invoke_structured(self, context: dict[str, object]):
        prompt = render_followup_decision_prompt(context)
        try:
            structured = self.chat_model.with_structured_output(
                DecisionContract,
                method="json_schema",
                include_raw=True,
            )
        except TypeError:
            # Lightweight test doubles and older LangChain adapters may not
            # expose include_raw. Production uses it so Provider usage remains
            # measurable without placing token fields in DecisionContract.
            structured = self.chat_model.with_structured_output(
                DecisionContract,
                method="json_schema",
            )
        if hasattr(structured, "bind"):
            structured = structured.bind(max_tokens=self.max_tokens)
        begin_provider_attempt()
        result = structured.invoke(prompt)
        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = (
            result.get("parsed")
            if isinstance(result, dict) and "parsed" in result
            else result
        )
        publish_provider_response(raw or result)
        if isinstance(result, dict) and result.get("parsing_error") is not None:
            raise StructuredFollowupOutputError(
                "Provider Decision response failed structured parsing",
                response=raw or result,
            )
        if parsed is None:
            raise StructuredFollowupOutputError(
                "Provider Decision response did not contain a parsed value",
                response=raw or result,
            )
        try:
            decision = (
                parsed
                if isinstance(parsed, DecisionContract)
                else DecisionContract.model_validate(parsed)
            )
        except Exception as exc:
            raise StructuredFollowupOutputError(
                "Provider Decision response failed schema validation",
                response=raw or result,
            ) from exc
        usage = extract_provider_usage(raw or result) or {}
        self._assert_expected_model(raw or result)
        return DecisionProviderResult(
            decision=decision,
            input_tokens=usage.get("provider_input_tokens"),
            output_tokens=usage.get("provider_output_tokens"),
            cached_input_tokens=usage.get("provider_cached_input_tokens"),
            provider_model=_provider_model(raw or result),
            provider_response_id=_provider_response_id(raw or result),
        )

    def _invoke_raw(self, context: dict[str, object]):
        prompt = render_followup_decision_prompt(context)
        model = self.chat_model
        if hasattr(model, "bind"):
            model = model.bind(max_tokens=self.max_tokens)
        begin_provider_attempt()
        response = model.invoke(prompt)
        publish_provider_response(response)
        try:
            payload = json.loads(
                _response_text(response),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
            if not isinstance(payload, dict):
                raise ValueError("Provider Decision JSON root must be an object")
            decision = DecisionContract.model_validate(payload)
        except Exception as exc:
            raise StructuredFollowupOutputError(
                "Provider Decision raw response failed JSON/schema validation",
                response=response,
            ) from exc
        self._assert_expected_model(response)
        usage = extract_provider_usage(response) or {}
        return DecisionProviderResult(
            decision=decision,
            input_tokens=usage.get("provider_input_tokens"),
            output_tokens=usage.get("provider_output_tokens"),
            cached_input_tokens=usage.get("provider_cached_input_tokens"),
            provider_model=_provider_model(response),
            provider_response_id=_provider_response_id(response),
        )

    def _assert_expected_model(self, response: object) -> None:
        actual_model = _provider_model(response)
        if self.expected_model is not None and actual_model != self.expected_model:
            raise ProviderModelMismatchError(
                "Provider Decision response model did not match authorization",
                response=response,
            )


def build_followup_decision_provider(
    chat_model,
    *,
    model: str,
    max_tokens: int = 300,
) -> StructuredFollowupDecisionProvider:
    """Build the production/evaluator Decision path from one exact-model rule."""

    if not isinstance(model, str) or not model:
        raise ValueError(
            "followup Decision provider requires an exact configured model"
        )
    output_mode = resolve_followup_decision_output_mode(model)
    return StructuredFollowupDecisionProvider(
        chat_model,
        max_tokens=max_tokens,
        output_mode=output_mode,
        expected_model=model,
    )


def build_followup_decision_provider_for_llm(
    llm,
    *,
    max_tokens: int = 300,
) -> StructuredFollowupDecisionProvider:
    """Build a production Decision Provider from an LLM's exact config identity."""

    config = getattr(llm, "config", None)
    model = getattr(config, "model", None)
    if not isinstance(model, str) or not model:
        raise ValueError(
            "followup Decision provider requires an exact configured model"
        )
    return build_followup_decision_provider(
        llm.chat_model,
        model=model,
        max_tokens=max_tokens,
    )


def _response_text(response: object) -> str:
    content = getattr(response, "content", response)
    if not isinstance(content, str):
        raise ValueError("Provider Decision response content must be text")
    value = content.strip()
    if not value:
        raise ValueError("Provider Decision response content is empty")
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Provider Decision JSON contains a duplicate key")
        result[key] = value
    return result


class FollowupGenerationProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    provider_model: str | None = None
    provider_response_id: str | None = None


class StructuredFollowupGenerationProvider:
    prompt_version = FOLLOWUP_GENERATION_PROMPT_VERSION
    prompt_sha256 = FOLLOWUP_GENERATION_PROMPT_SHA256

    def __init__(
        self,
        chat_model,
        *,
        max_tokens: int = FOLLOWUP_CONTEXT_POLICY.max_output_tokens,
    ) -> None:
        self.chat_model = chat_model
        self.max_tokens = max_tokens

    def __call__(
        self,
        context: list[dict[str, Any]],
    ) -> FollowupGenerationProviderResult:
        prompt = render_followup_generation_prompt(context)
        model = self.chat_model
        if hasattr(model, "bind"):
            model = model.bind(max_tokens=self.max_tokens)
        begin_provider_attempt()
        response = model.invoke(prompt)
        publish_provider_response(response)
        usage = extract_provider_usage(response) or {}
        content = getattr(response, "content", response)
        return FollowupGenerationProviderResult(
            text=str(content or "").strip(),
            input_tokens=usage.get("provider_input_tokens"),
            output_tokens=usage.get("provider_output_tokens"),
            cached_input_tokens=usage.get("provider_cached_input_tokens"),
            provider_model=_provider_model(response),
            provider_response_id=_provider_response_id(response),
        )


def _provider_model(response: object) -> str | None:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("model_name") or metadata.get("model")
    return value if isinstance(value, str) and value else None


def _provider_response_id(response: object) -> str | None:
    value = getattr(response, "id", None)
    return value if isinstance(value, str) and value else None
