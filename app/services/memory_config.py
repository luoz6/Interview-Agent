from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Mapping
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.graphs.interview_state import SUPPORTED_INTERVIEW_GRAPH_VERSIONS
from app.services.model_capabilities import ModelCapabilityRegistry


logger = logging.getLogger(__name__)
T = TypeVar("T")
_DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_warned_legacy_variables: set[str] = set()


class FrozenMemoryModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InterviewGraphMemoryConfig(FrozenMemoryModel):
    runtime_enabled: bool = True
    version: Literal["langgraph-v1", "langgraph-v2"] = "langgraph-v1"
    rollout_percent: int = Field(default=0, ge=0, le=100)


class ModelMemoryConfig(FrozenMemoryModel):
    provider: str = "openai-compatible"
    model: str = "deepseek-v4-pro"
    custom_base_url: bool = False
    context_window_tokens: int | None = Field(default=None, gt=0)
    protocol_reserve_tokens: int = Field(default=512, ge=0)
    structured_output_reserve_tokens: int = Field(default=2048, ge=0)
    safety_margin_tokens: int = Field(default=1024, ge=0)
    tokenizer_family: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]{1,128}$",
    )


class BudgetEnforcementConfig(FrozenMemoryModel):
    prep: bool = False
    interview: bool = False
    review: bool = False
    report: bool = False


class BudgetMemoryConfig(FrozenMemoryModel):
    mode: Literal["disabled", "shadow", "enforce"] = "disabled"
    shadow_enabled: bool = False
    followup_policy: str = "followup-context-v2"
    review_policy: str = "question-review-context-v2"
    enforcement: BudgetEnforcementConfig = Field(
        default_factory=BudgetEnforcementConfig
    )


class CompressionMemoryConfig(FrozenMemoryModel):
    mode: Literal["disabled", "shadow", "consume"] = "disabled"
    interview_question_memory: bool = False
    evidence: bool = False
    prep: bool = False
    review: bool = False


class SelectionMemoryConfig(FrozenMemoryModel):
    exact_recent_questions: int = Field(default=1, ge=1)
    max_memory_units: int = Field(default=4, ge=1)
    max_memory_tokens: int = Field(default=2500, ge=1)
    eligibility_utilization_basis_points: int = Field(
        default=8000,
        ge=1,
        le=10_000,
    )


class RetentionMemoryConfig(FrozenMemoryModel):
    session_days: int = Field(default=90, ge=1)
    report_days: int = Field(default=90, ge=1)
    artifact_unreferenced_hours: int = Field(default=24, ge=1)
    artifact_failed_hours: int = Field(default=24, ge=1)
    prep_ref_hours: int = Field(default=168, ge=1)
    checkpoint_days: int = Field(default=30, ge=1)
    cleanup_batch_size: int = Field(default=200, ge=1, le=100_000)


class ArtifactMemoryConfig(FrozenMemoryModel):
    lease_seconds: int = Field(default=60, ge=1)
    store_available: bool = True


class PrivacyMemoryConfig(FrozenMemoryModel):
    deployment_id: str = "single-tenant-local"
    trusted_local_deletion_enabled: bool = False
    trusted_local_metrics_enabled: bool = False


class LongTermMemoryConfig(FrozenMemoryModel):
    mode: Literal[
        "disabled",
        "write_shadow",
        "read_shadow",
        "local_consume",
    ] = "disabled"
    write_shadow_enabled: bool = False
    read_shadow_enabled: bool = False
    trusted_local_api_enabled: bool = False
    local_principal_enabled: bool = False
    local_principal_id: str = Field(
        default="local-owner",
        pattern=r"^[A-Za-z0-9_.-]{1,128}$",
    )
    local_consumption_enabled: bool = False
    consent_policy_version: str = "principal-memory-consent-v1"
    fact_schema_version: str = "principal-memory-fact-v1"
    taxonomy_version: str = "principal-memory-taxonomy-v1"
    max_proposals_per_session: int = Field(default=8, ge=1, le=32)
    max_shadow_facts: int = Field(default=6, ge=1, le=32)
    max_shadow_tokens: int = Field(default=800, ge=1, le=8000)
    proposal_retention_days: int = Field(default=30, ge=1)
    active_fact_default_days: int = Field(default=365, ge=1)


class EffectiveMemoryConfig(FrozenMemoryModel):
    schema_version: Literal["memory-runtime-config-v1"] = (
        "memory-runtime-config-v1"
    )
    interview_graph: InterviewGraphMemoryConfig
    model: ModelMemoryConfig
    budget: BudgetMemoryConfig
    compression: CompressionMemoryConfig
    selection: SelectionMemoryConfig = Field(default_factory=SelectionMemoryConfig)
    retention: RetentionMemoryConfig
    artifact: ArtifactMemoryConfig
    privacy: PrivacyMemoryConfig
    long_term: LongTermMemoryConfig = Field(default_factory=LongTermMemoryConfig)
    legacy_environment_used: bool = False


def load_effective_memory_config(
    environ: Mapping[str, str] | None = None,
) -> EffectiveMemoryConfig:
    env = os.environ if environ is None else environ
    legacy_used: set[str] = set()

    def resolve(
        new_name: str,
        legacy_name: str,
        parser: Callable[[str, str], T],
        default: T,
    ) -> T:
        new_present = new_name in env and str(env[new_name]).strip() != ""
        legacy_present = (
            legacy_name in env and str(env[legacy_name]).strip() != ""
        )
        new_value = parser(new_name, str(env[new_name])) if new_present else None
        legacy_value = (
            parser(legacy_name, str(env[legacy_name]))
            if legacy_present
            else None
        )
        if new_present and legacy_present and new_value != legacy_value:
            raise ValueError(
                f"conflicting memory configuration: {new_name} and {legacy_name}"
            )
        if legacy_present:
            legacy_used.add(legacy_name)
        return new_value if new_present else legacy_value if legacy_present else default

    runtime_enabled = resolve(
        "MEMORY_INTERVIEW_GRAPH_RUNTIME_ENABLED",
        "INTERVIEW_LANGGRAPH_RUNTIME_ENABLED",
        _parse_bool,
        True,
    )
    graph_version = resolve(
        "MEMORY_INTERVIEW_GRAPH_VERSION",
        "INTERVIEW_LANGGRAPH_VERSION",
        _parse_graph_version,
        "langgraph-v1",
    )
    rollout_percent = resolve(
        "MEMORY_INTERVIEW_GRAPH_ROLLOUT_PERCENT",
        "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT",
        _parse_percent,
        0,
    )
    context_window = resolve(
        "MEMORY_MODEL_CONTEXT_WINDOW_TOKENS",
        "LLM_CONTEXT_WINDOW_TOKENS",
        _parse_positive_int,
        None,
    )
    protocol_reserve = resolve(
        "MEMORY_MODEL_PROTOCOL_RESERVE_TOKENS",
        "LLM_CONTEXT_PROTOCOL_RESERVE_TOKENS",
        _parse_non_negative_int,
        512,
    )
    structured_reserve = resolve(
        "MEMORY_MODEL_STRUCTURED_OUTPUT_RESERVE_TOKENS",
        "LLM_STRUCTURED_OUTPUT_RESERVE_TOKENS",
        _parse_non_negative_int,
        2048,
    )
    safety_margin = resolve(
        "MEMORY_MODEL_SAFETY_MARGIN_TOKENS",
        "LLM_CONTEXT_SAFETY_MARGIN_TOKENS",
        _parse_non_negative_int,
        1024,
    )
    budget_shadow = resolve(
        "MEMORY_BUDGET_SHADOW_ENABLED",
        "CONTEXT_BUDGET_SHADOW_ENABLED",
        _parse_bool,
        False,
    )
    enforcement = BudgetEnforcementConfig(
        prep=resolve(
            "MEMORY_BUDGET_ENFORCEMENT_PREP",
            "CONTEXT_BUDGET_PREP_ENFORCEMENT",
            _parse_bool,
            False,
        ),
        interview=resolve(
            "MEMORY_BUDGET_ENFORCEMENT_INTERVIEW",
            "CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT",
            _parse_bool,
            False,
        ),
        review=resolve(
            "MEMORY_BUDGET_ENFORCEMENT_REVIEW",
            "CONTEXT_BUDGET_REVIEW_ENFORCEMENT",
            _parse_bool,
            False,
        ),
        report=resolve(
            "MEMORY_BUDGET_ENFORCEMENT_REPORT",
            "CONTEXT_BUDGET_REPORT_ROUTING",
            _parse_bool,
            False,
        ),
    )
    derived_budget_mode = (
        "enforce"
        if any(enforcement.model_dump().values())
        else "shadow" if budget_shadow else "disabled"
    )
    budget_mode = _resolve_new_only(
        env,
        "MEMORY_BUDGET_MODE",
        _parse_budget_mode,
        derived_budget_mode,
    )
    if (
        env.get("MEMORY_BUDGET_MODE", "").strip()
        and any(
            env.get(name, "").strip()
            for name in (
                "CONTEXT_BUDGET_SHADOW_ENABLED",
                "CONTEXT_BUDGET_PREP_ENFORCEMENT",
                "CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT",
                "CONTEXT_BUDGET_REVIEW_ENFORCEMENT",
                "CONTEXT_BUDGET_REPORT_ROUTING",
            )
        )
        and budget_mode != derived_budget_mode
    ):
        raise ValueError(
            "conflicting memory configuration: MEMORY_BUDGET_MODE and legacy budget gates"
        )

    compression_shadow = resolve(
        "MEMORY_COMPRESSION_SHADOW_ENABLED",
        "CONTEXT_COMPRESSION_SHADOW_ENABLED",
        _parse_bool,
        False,
    )
    compression_prep = resolve(
        "MEMORY_COMPRESSION_PREP",
        "CONTEXT_COMPRESSION_PREP_ENABLED",
        _parse_bool,
        False,
    )
    compression_interview = resolve(
        "MEMORY_COMPRESSION_INTERVIEW_QUESTION_MEMORY",
        "CONTEXT_COMPRESSION_INTERVIEW_ENABLED",
        _parse_bool,
        False,
    )
    compression_evidence = resolve(
        "MEMORY_COMPRESSION_EVIDENCE",
        "CONTEXT_COMPRESSION_EVIDENCE_ENABLED",
        _parse_bool,
        False,
    )
    compression_review = resolve(
        "MEMORY_COMPRESSION_REVIEW",
        "CONTEXT_COMPRESSION_REVIEW_ENABLED",
        _parse_bool,
        False,
    )
    derived_compression_mode = (
        "consume"
        if any(
            (
                compression_prep,
                compression_interview,
                compression_evidence,
                compression_review,
            )
        )
        else "shadow" if compression_shadow else "disabled"
    )
    compression_mode = _resolve_new_only(
        env,
        "MEMORY_COMPRESSION_MODE",
        _parse_compression_mode,
        derived_compression_mode,
    )
    if (
        env.get("MEMORY_COMPRESSION_MODE", "").strip()
        and any(
            env.get(name, "").strip()
            for name in (
                "CONTEXT_COMPRESSION_SHADOW_ENABLED",
                "CONTEXT_COMPRESSION_PREP_ENABLED",
                "CONTEXT_COMPRESSION_INTERVIEW_ENABLED",
                "CONTEXT_COMPRESSION_EVIDENCE_ENABLED",
                "CONTEXT_COMPRESSION_REVIEW_ENABLED",
            )
        )
        and compression_mode != derived_compression_mode
    ):
        raise ValueError(
            "conflicting memory configuration: MEMORY_COMPRESSION_MODE and legacy compression gates"
        )

    base_url = env.get("OPENAI_BASE_URL", "").strip()
    trusted_provider_base_url = any(
        host in base_url.casefold()
        for host in ("api.deepseek.com", "api.openai.com")
    )
    config = EffectiveMemoryConfig(
        interview_graph=InterviewGraphMemoryConfig(
            runtime_enabled=runtime_enabled,
            version=graph_version,
            rollout_percent=rollout_percent,
        ),
        model=ModelMemoryConfig(
            provider=env.get("LLM_PROVIDER", "openai-compatible").strip()
            or "openai-compatible",
            model=env.get("OPENAI_MODEL", "deepseek-v4-pro").strip()
            or "deepseek-v4-pro",
            custom_base_url=bool(base_url) and not trusted_provider_base_url,
            context_window_tokens=context_window,
            protocol_reserve_tokens=protocol_reserve,
            structured_output_reserve_tokens=structured_reserve,
            safety_margin_tokens=safety_margin,
            tokenizer_family=resolve(
                "MEMORY_MODEL_TOKENIZER_FAMILY",
                "LLM_TOKENIZER_FAMILY",
                _parse_optional_identifier,
                None,
            ),
        ),
        budget=BudgetMemoryConfig(
            mode=budget_mode,
            shadow_enabled=budget_shadow,
            enforcement=enforcement,
        ),
        compression=CompressionMemoryConfig(
            mode=compression_mode,
            interview_question_memory=compression_interview,
            evidence=compression_evidence,
            prep=compression_prep,
            review=compression_review,
        ),
        retention=RetentionMemoryConfig(
            artifact_unreferenced_hours=resolve(
                "MEMORY_RETENTION_ARTIFACT_UNREFERENCED_HOURS",
                "CONTEXT_ARTIFACT_UNREFERENCED_RETENTION_HOURS",
                _parse_positive_int,
                24,
            ),
            artifact_failed_hours=resolve(
                "MEMORY_RETENTION_ARTIFACT_FAILED_HOURS",
                "CONTEXT_ARTIFACT_FAILED_RETENTION_HOURS",
                _parse_positive_int,
                24,
            ),
            prep_ref_hours=resolve(
                "MEMORY_RETENTION_PREP_REF_HOURS",
                "CONTEXT_ARTIFACT_PREP_REF_RETENTION_HOURS",
                _parse_positive_int,
                168,
            ),
            cleanup_batch_size=resolve(
                "MEMORY_RETENTION_CLEANUP_BATCH_SIZE",
                "CONTEXT_ARTIFACT_CLEANUP_BATCH_SIZE",
                _parse_positive_int,
                200,
            ),
            session_days=_new_positive(env, "MEMORY_RETENTION_SESSION_DAYS", 90),
            report_days=_new_positive(env, "MEMORY_RETENTION_REPORT_DAYS", 90),
            checkpoint_days=_new_positive(
                env,
                "MEMORY_RETENTION_CHECKPOINT_DAYS",
                30,
            ),
        ),
        artifact=ArtifactMemoryConfig(
            lease_seconds=resolve(
                "MEMORY_ARTIFACT_LEASE_SECONDS",
                "CONTEXT_ARTIFACT_LEASE_SECONDS",
                _parse_positive_int,
                60,
            ),
            store_available=_new_bool(
                env,
                "MEMORY_ARTIFACT_STORE_AVAILABLE",
                True,
            ),
        ),
        privacy=PrivacyMemoryConfig(
            deployment_id=resolve(
                "MEMORY_PRIVACY_DEPLOYMENT_ID",
                "CONTEXT_ARTIFACT_DEPLOYMENT_SCOPE",
                _parse_deployment_id,
                "single-tenant-local",
            ),
            trusted_local_deletion_enabled=_new_bool(
                env,
                "MEMORY_TRUSTED_LOCAL_DELETION_ENABLED",
                False,
            ),
            trusted_local_metrics_enabled=_new_bool(
                env,
                "MEMORY_TRUSTED_LOCAL_METRICS_ENABLED",
                False,
            ),
        ),
        long_term=LongTermMemoryConfig(
            mode=_parse_long_term_mode(
                "MEMORY_LONG_TERM_MODE",
                env.get("MEMORY_LONG_TERM_MODE", "disabled"),
            ),
            write_shadow_enabled=_new_bool(
                env, "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED", False
            ),
            read_shadow_enabled=_new_bool(
                env, "MEMORY_LONG_TERM_READ_SHADOW_ENABLED", False
            ),
            trusted_local_api_enabled=_new_bool(
                env,
                "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED",
                False,
            ),
            local_principal_enabled=_new_bool(
                env,
                "MEMORY_LOCAL_PRINCIPAL_ENABLED",
                False,
            ),
            local_principal_id=(
                _parse_required_identifier(
                    "MEMORY_LOCAL_PRINCIPAL_ID",
                    str(env["MEMORY_LOCAL_PRINCIPAL_ID"]),
                )
                if "MEMORY_LOCAL_PRINCIPAL_ID" in env
                else "local-owner"
            ),
            local_consumption_enabled=_new_bool(
                env,
                "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED",
                False,
            ),
            consent_policy_version=_resolve_new_only(
                env,
                "MEMORY_LONG_TERM_CONSENT_POLICY_VERSION",
                _parse_required_identifier,
                "principal-memory-consent-v1",
            ),
            fact_schema_version=_resolve_new_only(
                env,
                "MEMORY_LONG_TERM_FACT_SCHEMA_VERSION",
                _parse_required_identifier,
                "principal-memory-fact-v1",
            ),
            taxonomy_version=_resolve_new_only(
                env,
                "MEMORY_LONG_TERM_TAXONOMY_VERSION",
                _parse_required_identifier,
                "principal-memory-taxonomy-v1",
            ),
            max_proposals_per_session=_new_positive(
                env, "MEMORY_LONG_TERM_MAX_PROPOSALS_PER_SESSION", 8
            ),
            max_shadow_facts=_new_positive(
                env, "MEMORY_LONG_TERM_MAX_SHADOW_FACTS", 6
            ),
            max_shadow_tokens=_new_positive(
                env, "MEMORY_LONG_TERM_MAX_SHADOW_TOKENS", 800
            ),
            proposal_retention_days=_new_positive(
                env, "MEMORY_LONG_TERM_PROPOSAL_RETENTION_DAYS", 30
            ),
            active_fact_default_days=_new_positive(
                env, "MEMORY_LONG_TERM_ACTIVE_FACT_DEFAULT_DAYS", 365
            ),
        ),
        legacy_environment_used=bool(legacy_used),
    )
    _validate_effective_config(config)
    if environ is None:
        _warn_for_legacy_variables(legacy_used)
    return config


def memory_readiness_payload(config: EffectiveMemoryConfig) -> dict:
    payload = {
        "schema_version": config.schema_version,
        "configuration_valid": True,
        "budget_mode": config.budget.mode,
        "compression_mode": config.compression.mode,
        "long_term_mode": config.long_term.mode,
        "local_principal_enabled": config.long_term.local_principal_enabled,
        "local_consumption_enabled": (
            config.long_term.local_consumption_enabled
        ),
        "interview_graph_version": config.interview_graph.version,
        "interview_graph_rollout_percent": (
            config.interview_graph.rollout_percent
        ),
        "legacy_environment_used": config.legacy_environment_used,
        "consumption_ready": True,
        "reason": None,
    }
    if (
        config.compression.mode == "consume"
        and config.compression.interview_question_memory
    ):
        try:
            from app.services.knowledge_profile import (
                P1_REQUIRED_COVERED_TAGS,
                load_active_knowledge_covered_tags,
            )

            covered = load_active_knowledge_covered_tags()
            if not P1_REQUIRED_COVERED_TAGS.issubset(covered):
                raise ValueError("minimum P1 coverage is unavailable")
        except (OSError, ValueError):
            payload.update(
                {
                    "configuration_valid": False,
                    "consumption_ready": False,
                    "reason": "knowledge_coverage_unavailable",
                }
            )
    return payload


def _validate_effective_config(config: EffectiveMemoryConfig) -> None:
    graph = config.interview_graph
    if graph.version not in SUPPORTED_INTERVIEW_GRAPH_VERSIONS:
        raise ValueError("unsupported durable interview graph version")
    if graph.rollout_percent > 0 and not graph.runtime_enabled:
        raise ValueError("interview graph rollout requires runtime enabled")
    if (
        graph.version == "langgraph-v2"
        and graph.rollout_percent > 0
        and (
            config.budget.mode != "enforce"
            or not config.budget.enforcement.interview
        )
    ):
        raise ValueError("langgraph-v2 rollout requires interview budget enforcement")
    if config.compression.evidence and not (
        config.compression.interview_question_memory
        or config.compression.review
    ):
        raise ValueError(
            "evidence consumption requires interview or review compression"
        )
    if config.compression.mode == "consume":
        if not config.budget.enforcement.interview:
            raise ValueError(
                "compression consumption requires interview budget enforcement"
            )
        if not config.artifact.store_available:
            raise ValueError(
                "compression consumption requires an artifact store"
            )
    ModelCapabilityRegistry().resolve(
        provider=config.model.provider,
        model=config.model.model,
        configured_context_window_tokens=config.model.context_window_tokens,
        protocol_reserve_tokens=config.model.protocol_reserve_tokens,
        structured_output_reserve_tokens=(
            config.model.structured_output_reserve_tokens
        ),
        safety_margin_tokens=config.model.safety_margin_tokens,
        custom_base_url=config.model.custom_base_url,
    )
    long_term = config.long_term
    if long_term.mode == "disabled" and (
        long_term.write_shadow_enabled or long_term.read_shadow_enabled
    ):
        raise ValueError("disabled long-term memory cannot enable shadow operations")
    if long_term.mode == "write_shadow" and not long_term.write_shadow_enabled:
        raise ValueError("write_shadow mode requires its explicit write gate")
    if long_term.mode == "read_shadow" and not (
        long_term.write_shadow_enabled and long_term.read_shadow_enabled
    ):
        raise ValueError("read_shadow mode requires explicit write and read gates")
    if (
        long_term.local_principal_enabled
        and config.privacy.deployment_id != "single-tenant-local"
    ):
        raise ValueError(
            "Local Principal requires the single-tenant-local deployment scope"
        )
    if (
        long_term.local_consumption_enabled
        and long_term.mode != "local_consume"
    ):
        raise ValueError(
            "local consumption gate requires local_consume mode"
        )
    if long_term.mode == "local_consume":
        if not long_term.local_principal_enabled:
            raise ValueError("local_consume mode requires its local Principal gate")
        if not long_term.trusted_local_api_enabled:
            raise ValueError("local_consume mode requires its trusted-local API gate")
        if not (
            long_term.write_shadow_enabled and long_term.read_shadow_enabled
        ):
            raise ValueError(
                "local_consume mode requires explicit write and read shadow gates"
            )
        if not long_term.local_consumption_enabled:
            raise ValueError("local_consume mode requires its local consumption gate")


def _resolve_new_only(env, name, parser, default):
    raw = env.get(name)
    return parser(name, raw) if raw is not None and raw.strip() else default


def _new_positive(env, name: str, default: int) -> int:
    return _resolve_new_only(env, name, _parse_positive_int, default)


def _new_bool(env, name: str, default: bool) -> bool:
    return _resolve_new_only(env, name, _parse_bool, default)


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def _parse_positive_int(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _parse_non_negative_int(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _parse_percent(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be between 0 and 100") from exc
    if value < 0 or value > 100:
        raise ValueError(f"{name} must be between 0 and 100")
    return value


def _parse_graph_version(name: str, raw: str) -> str:
    value = raw.strip()
    if value not in SUPPORTED_INTERVIEW_GRAPH_VERSIONS:
        raise ValueError(f"{name} contains an unsupported graph version")
    return value


def _parse_budget_mode(name: str, raw: str) -> str:
    value = raw.strip().lower()
    if value not in {"disabled", "shadow", "enforce"}:
        raise ValueError(f"{name} must be disabled, shadow, or enforce")
    return value


def _parse_compression_mode(name: str, raw: str) -> str:
    value = raw.strip().lower()
    if value not in {"disabled", "shadow", "consume"}:
        raise ValueError(f"{name} must be disabled, shadow, or consume")
    return value


def _parse_long_term_mode(name: str, raw: str) -> str:
    value = raw.strip().lower()
    if value == "consume":
        raise ValueError(f"{name}=consume is not supported and cannot be downgraded")
    if value not in {"disabled", "write_shadow", "read_shadow", "local_consume"}:
        raise ValueError(
            f"{name} must be disabled, write_shadow, read_shadow, or local_consume"
        )
    return value


def _parse_required_identifier(name: str, raw: str) -> str:
    value = _parse_optional_identifier(name, raw)
    if value is None:
        raise ValueError(f"{name} must be a stable identifier")
    return value


def _parse_deployment_id(name: str, raw: str) -> str:
    value = raw.strip()
    if _DEPLOYMENT_ID.fullmatch(value) is None:
        raise ValueError(f"{name} must be a stable deployment identifier")
    return value


def _parse_optional_identifier(name: str, raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if _DEPLOYMENT_ID.fullmatch(value) is None:
        raise ValueError(f"{name} must be a stable identifier")
    return value


def _warn_for_legacy_variables(names: set[str]) -> None:
    for name in sorted(names):
        if name in _warned_legacy_variables:
            continue
        _warned_legacy_variables.add(name)
        logger.warning(
            "legacy memory environment variable is deprecated",
            extra={"variable_name": name},
        )
