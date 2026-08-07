from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.context_budget import PLAN_CONTEXT_POLICY
from app.services.interview_plan_budget import (
    INTERVIEW_PLAN_BUDGET_VERSION,
    allocate_expected_followups,
    allocate_main_answer_minutes,
    assess_interview_plan_budget,
)
from app.services.interview_plan_knowledge import unbound_question_knowledge
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanV2,
    PlanConfigurationSnapshot,
    plan_payload_sha256,
)
from app.services.interview_quality_dataset import (
    InitialQuestionCaseInput,
    InterviewQualityDataset,
    canonical_json_bytes,
)
from app.services.interview_quality_gate import (
    GateConfig,
    MetricEvaluation,
    evaluate_metric,
)


InitialExecutionSource = Literal[
    "synthetic_fixture_replay",
    "saved_provider_replay",
    "live_provider",
]


class InitialQuestionReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    review_status: Literal["pending", "reviewed"]
    reviewer_kind: Literal["unassigned", "synthetic_fixture", "independent_human"]
    jd_resume_relevant: bool | None = None
    configured_focus_covered: bool | None = None
    difficulty_fit: bool | None = None
    single_clear_answerable: bool | None = None
    answerable_within_budget: bool | None = None
    definition_only: bool | None = None
    within_plan_duplicate: bool | None = None
    reference_or_internal_evidence_leak: bool | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def validate_review_completion(self):
        values = (
            self.jd_resume_relevant,
            self.configured_focus_covered,
            self.difficulty_fit,
            self.single_clear_answerable,
            self.answerable_within_budget,
            self.definition_only,
            self.within_plan_duplicate,
            self.reference_or_internal_evidence_leak,
        )
        if self.review_status == "reviewed":
            if any(value is None for value in values):
                raise ValueError("reviewed questions require every rubric judgment")
            if not (self.rationale or "").strip():
                raise ValueError("reviewed questions require a rationale")
        elif any(value is not None for value in values) or self.rationale is not None:
            raise ValueError("pending questions cannot carry partial judgments")
        if self.review_status == "pending" and self.reviewer_kind != "unassigned":
            raise ValueError("pending questions require an unassigned reviewer")
        if self.review_status == "reviewed" and self.reviewer_kind == "unassigned":
            raise ValueError("reviewed questions require a real reviewer kind")
        return self


class PlanContextBudgetEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_source: Literal["synthetic_measurement", "runtime_measurement"]
    enforcement_required: Literal[True] = True
    estimated_input_tokens: int = Field(ge=0)
    available_input_tokens: int = Field(ge=1)
    estimator_fallback_used: bool
    knowledge_candidate_count: int = Field(ge=0)
    retained_knowledge_candidate_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_budget(self):
        if self.retained_knowledge_candidate_count > self.knowledge_candidate_count:
            raise ValueError("retained knowledge candidates cannot exceed input")
        return self

    @property
    def within_budget(self) -> bool:
        return self.estimated_input_tokens <= self.available_input_tokens

    @property
    def grounding_retained(self) -> bool:
        return self.retained_knowledge_candidate_count == self.knowledge_candidate_count


class InitialQuestionEvalAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    run_number: int = Field(ge=1)
    partition: Literal["train", "dev", "blind-test"]
    execution_source: InitialExecutionSource
    plan: InterviewPlanV2
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviews: tuple[InitialQuestionReview, ...]
    context_budget: PlanContextBudgetEvidence
    provider_name: str | None = None
    provider_model: str | None = None
    provider_invocations: int = Field(default=0, ge=0)
    provider_metered_invocations: int = Field(default=0, ge=0)
    provider_retries: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    latency_seconds: float = Field(default=0, ge=0)
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_attempt(self):
        actual_hash = plan_payload_sha256(self.plan)
        if self.plan_sha256 != actual_hash:
            raise ValueError("plan_sha256 does not match normalized plan")
        question_ids = [question.question_id for question in self.plan.questions]
        review_ids = [review.question_id for review in self.reviews]
        if review_ids != question_ids:
            raise ValueError("reviews must cover every question once in plan order")
        if self.provider_metered_invocations > self.provider_invocations:
            raise ValueError("metered invocations cannot exceed total invocations")
        if self.execution_source == "live_provider":
            if not self.provider_name or not self.provider_model:
                raise ValueError("live Provider attempts require Provider identity")
            if self.provider_invocations < 1:
                raise ValueError("live Provider attempts require an invocation")
            if self.provider_metered_invocations != self.provider_invocations:
                raise ValueError("every live Provider invocation must be metered")
            if (
                self.input_tokens is None
                or self.output_tokens is None
                or self.cached_input_tokens is None
            ):
                raise ValueError("live Provider attempts require token usage")
            if self.latency_seconds <= 0 or self.response_sha256 is None:
                raise ValueError("live Provider attempts require latency and response hash")
        elif self.provider_invocations or self.provider_metered_invocations:
            raise ValueError("offline replay cannot claim live Provider invocations")
        return self


class InitialQuestionProviderArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["initial-question-provider-replay-v1"] = (
        "initial-question-provider-replay-v1"
    )
    source: Literal["synthetic_fixture", "local_redacted_provider_output"]
    dataset_id: Literal["initial-question-quality-v2"]
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_name: str | None = None
    model_id: str | None = None
    capture_status: Literal["complete", "hard_stopped"] = "complete"
    hard_stop_conditions: tuple[str, ...] = ()
    outbound_requests_attempted: int = Field(default=0, ge=0)
    outbound_requests_metered: int = Field(default=0, ge=0)
    attempts: tuple[InitialQuestionEvalAttempt, ...]

    @model_validator(mode="after")
    def validate_capture(self):
        keys = [(item.case_id, item.run_number) for item in self.attempts]
        if len(keys) != len(set(keys)):
            raise ValueError("Provider replay attempt keys must be unique")
        if self.capture_status == "hard_stopped" and not self.hard_stop_conditions:
            raise ValueError("hard-stopped captures require stop conditions")
        if self.capture_status == "complete" and self.hard_stop_conditions:
            raise ValueError("complete captures cannot carry stop conditions")
        if self.outbound_requests_metered > self.outbound_requests_attempted:
            raise ValueError("metered outbound requests cannot exceed attempts")
        if self.source == "local_redacted_provider_output":
            if not self.provider_name or not self.model_id:
                raise ValueError("real saved output requires Provider identity")
            if self.capture_status == "complete" and any(
                item.provider_model != self.model_id for item in self.attempts
            ):
                raise ValueError("saved response model metadata does not match artifact")
            if self.capture_status == "complete" and (
                self.outbound_requests_attempted
                != sum(item.provider_invocations for item in self.attempts)
                or self.outbound_requests_metered != self.outbound_requests_attempted
            ):
                raise ValueError("complete captures require exact request accounting")
        elif self.provider_name is not None or self.model_id is not None:
            raise ValueError("synthetic fixtures cannot claim Provider identity")
        elif self.outbound_requests_attempted or self.outbound_requests_metered:
            raise ValueError("synthetic fixtures cannot claim outbound requests")
        return self


def calculate_initial_question_metrics(
    dataset: InterviewQualityDataset,
    attempts: list[InitialQuestionEvalAttempt],
    *,
    gate_config: GateConfig,
) -> dict[str, Any]:
    if dataset.dataset_id != "initial-question-quality-v2":
        raise ValueError("initial question metrics require initial-question-quality-v2")
    case_by_id = {case.case_id: case for case in dataset.cases}
    _validate_attempt_coverage(case_by_id, attempts)
    all_reviews = [review for attempt in attempts for review in attempt.reviews]
    reviews_complete = all(review.review_status == "reviewed" for review in all_reviews)
    total_questions = len(all_reviews)

    if reviews_complete:
        values = {
            "jd_resume_relevance_rate": (
                _ratio(sum(bool(item.jd_resume_relevant) for item in all_reviews), total_questions),
                total_questions,
            ),
            "configured_focus_coverage_rate": (
                _ratio(sum(bool(item.configured_focus_covered) for item in all_reviews), total_questions),
                total_questions,
            ),
            "within_plan_duplicate_rate": (
                _ratio(sum(bool(item.within_plan_duplicate) for item in all_reviews), total_questions),
                total_questions,
            ),
            "difficulty_fit_rate": (
                _ratio(sum(bool(item.difficulty_fit) for item in all_reviews), total_questions),
                total_questions,
            ),
            "single_clear_answerable_rate": (
                _ratio(sum(bool(item.single_clear_answerable) for item in all_reviews), total_questions),
                total_questions,
            ),
            "reference_or_internal_evidence_leak_count": (
                float(sum(bool(item.reference_or_internal_evidence_leak) for item in all_reviews)),
                total_questions,
            ),
        }
    else:
        values = {
            name: (0.0, 0)
            for name in (
                "jd_resume_relevance_rate",
                "configured_focus_coverage_rate",
                "within_plan_duplicate_rate",
                "difficulty_fit_rate",
                "single_clear_answerable_rate",
                "reference_or_internal_evidence_leak_count",
            )
        }
    snapshot_matches = sum(
        item.plan_sha256 == item.session_snapshot_sha256 for item in attempts
    )
    values["preview_session_plan_hash_match_rate"] = (
        _ratio(snapshot_matches, len(attempts)),
        len(attempts),
    )
    evaluations: list[MetricEvaluation] = [
        evaluate_metric(
            gate_config,
            f"initial_question_quality.{name}",
            actual=actual,
            sample_size=sample_size,
        )
        for name, (actual, sample_size) in values.items()
    ]

    budget_assessments = [assess_interview_plan_budget(item.plan) for item in attempts]
    exact_budget_passes = sum(item.status == "PASS" for item in budget_assessments)
    question_count_matches = sum(
        item.question_count == sum(item.expected_question_type_budget.values())
        for item in budget_assessments
    )
    estimated_duration_matches = sum(
        item.acceptable_estimated_minutes_min
        <= item.estimate.estimated_minutes
        <= item.acceptable_estimated_minutes_max
        for item in budget_assessments
    )
    context_budget_passes = sum(
        item.context_budget.enforcement_required and item.context_budget.within_budget
        for item in attempts
    )
    grounding_retention_passes = sum(
        item.context_budget.grounding_retained for item in attempts
    )
    if reviews_complete:
        answerable_within_budget = _ratio(
            sum(bool(item.answerable_within_budget) for item in all_reviews),
            total_questions,
        )
        non_definition_only_plans = _ratio(
            sum(
                any(not bool(review.definition_only) for review in attempt.reviews)
                for attempt in attempts
            ),
            len(attempts),
        )
        stability = _quality_stability(case_by_id, attempts)
    else:
        answerable_within_budget = None
        non_definition_only_plans = None
        stability = None
    deterministic_failures = []
    if exact_budget_passes != len(attempts):
        deterministic_failures.append("plan_budget_not_exact")
    if context_budget_passes != len(attempts):
        deterministic_failures.append("context_budget_regression")
    if grounding_retention_passes != len(attempts):
        deterministic_failures.append("grounding_context_not_retained")
    if reviews_complete and answerable_within_budget != 1.0:
        deterministic_failures.append("question_not_answerable_within_budget")
    if reviews_complete and non_definition_only_plans != 1.0:
        deterministic_failures.append("definition_only_plan")

    gate_status = (
        "FAIL"
        if any(item.status == "FAIL" for item in evaluations) or deterministic_failures
        else "INSUFFICIENT_SAMPLE"
        if any(
            item.status in {"INSUFFICIENT_SAMPLE", "INSUFFICIENT_BASELINE"}
            for item in evaluations
        )
        else "PASS"
    )
    independent = all(
        review.reviewer_kind == "independent_human"
        and review.review_status == "reviewed"
        for review in all_reviews
    )
    dataset_gate_eligible = all(
        case.gate_eligible and case.annotation.review_status == "reviewed"
        for case in dataset.cases
    )
    real_provider = all(
        item.execution_source in {"live_provider", "saved_provider_replay"}
        for item in attempts
    )
    quality_status = (
        "FAIL_AUTOMATED"
        if gate_status == "FAIL"
        else "BLOCKED_PENDING_INDEPENDENT_REVIEW"
        if not reviews_complete
        else "BLOCKED_INSUFFICIENT_SAMPLE"
        if gate_status == "INSUFFICIENT_SAMPLE"
        else "BLOCKED_SYNTHETIC_FIXTURE_ONLY"
        if not real_provider
        else "BLOCKED_PENDING_INDEPENDENT_REVIEW"
        if not independent or not dataset_gate_eligible
        else "PASS"
    )
    def complete_token_total(field: str) -> int | None:
        if not attempts or sum(item.provider_invocations for item in attempts) == 0:
            return 0
        values = [getattr(item, field) for item in attempts]
        return sum(values) if all(value is not None for value in values) else None

    return {
        "dataset_case_count": len(dataset.cases),
        "attempt_count": len(attempts),
        "question_count": total_questions,
        "runs_per_case": {
            case_id: len([item for item in attempts if item.case_id == case_id])
            for case_id in sorted(case_by_id)
        },
        "metric_values": {name: actual for name, (actual, _sample) in values.items()},
        "metric_evaluations": [item.model_dump(mode="json") for item in evaluations],
        "plan_budget_gate": {
            "version": INTERVIEW_PLAN_BUDGET_VERSION,
            "status": "PASS" if exact_budget_passes == len(attempts) else "FAIL",
            "exact_pass_count": exact_budget_passes,
            "question_count_match_rate": _ratio(question_count_matches, len(attempts)),
            "estimated_duration_fit_rate": _ratio(estimated_duration_matches, len(attempts)),
            "warning_counts": dict(
                Counter(code for item in budget_assessments for code in item.warning_codes)
            ),
            "blocking_counts": dict(
                Counter(code for item in budget_assessments for code in item.blocking_codes)
            ),
        },
        "context_and_grounding_gate": {
            "status": (
                "PASS"
                if context_budget_passes == len(attempts)
                and grounding_retention_passes == len(attempts)
                else "FAIL"
            ),
            "context_budget_no_regression_rate": _ratio(context_budget_passes, len(attempts)),
            "grounding_context_retention_rate": _ratio(grounding_retention_passes, len(attempts)),
            "maximum_budget_utilization": max(
                item.context_budget.estimated_input_tokens
                / item.context_budget.available_input_tokens
                for item in attempts
            ),
        },
        "semantic_checks": {
            "reviews_complete": reviews_complete,
            "answerable_within_budget_rate": answerable_within_budget,
            "non_definition_only_plan_rate": non_definition_only_plans,
            "case_quality_stability_rate": stability,
            "cross_run_exact_duplicate_case_count": _cross_run_exact_duplicates(attempts),
            "cross_run_mean_question_overlap_rate": _cross_run_mean_overlap(attempts),
        },
        "provider_usage": {
            "provider_invocations_this_run": sum(item.provider_invocations for item in attempts),
            "provider_metered_invocations": sum(item.provider_metered_invocations for item in attempts),
            "provider_retries": sum(item.provider_retries for item in attempts),
            "input_tokens": complete_token_total("input_tokens"),
            "output_tokens": complete_token_total("output_tokens"),
            "cached_input_tokens": complete_token_total("cached_input_tokens"),
            "latency_seconds": sum(item.latency_seconds for item in attempts),
        },
        "deterministic_failures": deterministic_failures,
        "automated_status": gate_status,
        "independent_review_status": "COMPLETE" if independent else "PENDING",
        "dataset_gate_eligible": dataset_gate_eligible,
        "quality_status": quality_status,
    }


def build_synthetic_initial_question_attempts(
    dataset: InterviewQualityDataset,
) -> list[InitialQuestionEvalAttempt]:
    attempts: list[InitialQuestionEvalAttempt] = []
    for case in dataset.cases:
        item = InitialQuestionCaseInput.model_validate(case.input)
        configuration = PlanConfigurationSnapshot.model_validate(item.configuration)
        for run_number in range(1, item.runs_per_case + 1):
            plan = _synthetic_plan(
                case.case_id,
                item,
                configuration,
                run_number,
                language=case.language,
            )
            plan_sha256 = plan_payload_sha256(plan)
            prompt_size = len(
                canonical_json_bytes(
                    {
                        "job_description": item.job_description,
                        "resume_summary": item.resume_summary,
                        "configuration": item.configuration,
                        "knowledge_context": item.knowledge_context,
                    }
                )
            )
            estimated_tokens = max(1, (prompt_size + 3) // 4)
            attempts.append(
                InitialQuestionEvalAttempt(
                    case_id=case.case_id,
                    run_number=run_number,
                    partition=case.partition,
                    execution_source="synthetic_fixture_replay",
                    plan=plan,
                    plan_sha256=plan_sha256,
                    session_snapshot_sha256=plan_sha256,
                    reviews=tuple(
                        InitialQuestionReview(
                            question_id=question.question_id,
                            review_status="reviewed",
                            reviewer_kind="synthetic_fixture",
                            jd_resume_relevant=True,
                            configured_focus_covered=True,
                            difficulty_fit=True,
                            single_clear_answerable=True,
                            answerable_within_budget=True,
                            definition_only=False,
                            within_plan_duplicate=False,
                            reference_or_internal_evidence_leak=False,
                            rationale="Synthetic strong-plan fixture for evaluator arithmetic only.",
                        )
                        for question in plan.questions
                    ),
                    context_budget=PlanContextBudgetEvidence(
                        evidence_source="synthetic_measurement",
                        estimated_input_tokens=estimated_tokens,
                        available_input_tokens=PLAN_CONTEXT_POLICY.input_cap_tokens,
                        estimator_fallback_used=False,
                        knowledge_candidate_count=len(item.knowledge_context),
                        retained_knowledge_candidate_count=len(item.knowledge_context),
                    ),
                )
            )
    return attempts


def fixture_artifact(
    dataset: InterviewQualityDataset,
    *,
    dataset_sha256: str,
) -> InitialQuestionProviderArtifact:
    return InitialQuestionProviderArtifact(
        source="synthetic_fixture",
        dataset_id="initial-question-quality-v2",
        dataset_sha256=dataset_sha256,
        attempts=tuple(build_synthetic_initial_question_attempts(dataset)),
    )


def load_initial_question_provider_artifact(
    path: Path | str,
    *,
    dataset: InterviewQualityDataset,
    dataset_path: Path | str,
) -> InitialQuestionProviderArtifact:
    artifact = InitialQuestionProviderArtifact.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    dataset_sha256 = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    if artifact.dataset_id != dataset.dataset_id:
        raise ValueError("saved artifact dataset ID mismatch")
    if artifact.dataset_sha256 != dataset_sha256:
        raise ValueError("saved artifact dataset hash mismatch")
    return artifact


def saved_replay_attempts(
    artifact: InitialQuestionProviderArtifact,
) -> list[InitialQuestionEvalAttempt]:
    if artifact.capture_status != "complete":
        raise ValueError("hard-stopped Provider captures cannot be replayed as complete")
    source: InitialExecutionSource = (
        "synthetic_fixture_replay"
        if artifact.source == "synthetic_fixture"
        else "saved_provider_replay"
    )
    return [
        item.model_copy(
            update={
                "execution_source": source,
                "provider_invocations": 0,
                "provider_metered_invocations": 0,
                "provider_retries": 0,
            }
        )
        for item in artifact.attempts
    ]


def render_initial_question_report(metrics: dict[str, Any]) -> str:
    lines = [
        "# Initial question quality evaluation",
        "",
        f"- automated status: `{metrics['automated_status']}`",
        f"- quality status: `{metrics['quality_status']}`",
        f"- dataset cases: `{metrics['dataset_case_count']}`",
        f"- plan attempts: `{metrics['attempt_count']}`",
        f"- evaluated questions: `{metrics['question_count']}`",
        f"- plan budget gate: `{metrics['plan_budget_gate']['status']}`",
        f"- context/grounding gate: `{metrics['context_and_grounding_gate']['status']}`",
        "",
        "## Frozen GateConfig metrics",
        "",
        "| Metric | Actual | Status |",
        "| --- | ---: | --- |",
    ]
    for item in metrics["metric_evaluations"]:
        lines.append(
            f"| `{item['metric_key']}` | {item['actual']:.6g} | `{item['status']}` |"
        )
    lines.extend(
        [
            "",
            "Synthetic fixture replay validates evaluator arithmetic only and cannot establish real Provider quality.",
        ]
    )
    return "\n".join(lines) + "\n"


def _synthetic_plan(
    case_id: str,
    item: InitialQuestionCaseInput,
    configuration: PlanConfigurationSnapshot,
    run_number: int,
    *,
    language: str,
) -> InterviewPlanV2:
    kinds = [
        kind
        for kind in ("project", "technical", "system-design", "behavioral")
        for _ in range(configuration.question_type_budget.get(kind, 0))
    ]
    expected_followups = allocate_expected_followups(
        expected_followup_budget=configuration.expected_followup_budget,
        question_count=len(kinds),
        max_followups_per_question=configuration.max_followups_per_question,
    )
    main_minutes = allocate_main_answer_minutes(
        target_duration_minutes=configuration.target_duration_minutes,
        expected_followups=expected_followups,
    )
    role_term = item.role_keywords[(run_number - 1) % len(item.role_keywords)]
    focus_term = item.focus_evidence[(run_number - 1) % len(item.focus_evidence)]
    questions = []
    for position, (kind, expected_minutes, followups) in enumerate(
        zip(kinds, main_minutes, expected_followups, strict=True),
        start=1,
    ):
        variant = (
            ("constraints and validation" if run_number == 1 else "failure modes and improvement")
            if language == "en"
            else ("约束与验证" if run_number == 1 else "失败模式与改进")
        )
        if language == "en" and (item.scenario_domain == "system_design" or kind == "system-design"):
            prompt = (
                f"For the {role_term} scenario, design one concrete solution and explain "
                f"{focus_term}, {variant}, and how you would validate the key assumption?"
            )
        elif language == "en" and kind == "project":
            prompt = (
                f"Choose the resume project most relevant to {role_term} and explain your "
                f"ownership, key decision, {focus_term}, and {variant}?"
            )
        elif language == "en" and kind == "behavioral":
            prompt = (
                f"When a {role_term} delivery faced disagreement or risk, how did you define "
                f"ownership, act on {focus_term}, and validate the outcome?"
            )
        elif language == "en":
            prompt = (
                f"For the {role_term} requirement, explain one concrete implementation "
                f"boundary, {focus_term}, {variant}, and the diagnostic evidence you would use?"
            )
        elif item.scenario_domain == "system_design" or kind == "system-design":
            prompt = (
                f"围绕 {role_term}，请设计一个满足岗位约束的方案，并说明{focus_term}、"
                f"{variant}以及你会如何验证关键假设？"
            )
        elif kind == "project":
            prompt = (
                f"请选取简历中与 {role_term} 最相关的项目，说明你的职责、关键决策、"
                f"{focus_term}和{variant}？"
            )
        elif kind == "behavioral":
            prompt = (
                f"在推进 {role_term} 相关工作时遇到分歧或风险，你如何界定责任、"
                f"围绕{focus_term}采取行动并验证结果？"
            )
        else:
            prompt = (
                f"针对岗位中的 {role_term} 场景，请说明一次具体实现的边界、"
                f"{focus_term}、{variant}以及排障证据？"
            )
        question_id = str(
            uuid5(NAMESPACE_URL, f"initial-question-v1:{case_id}:{run_number}:{position}")
        )
        questions.append(
            InterviewPlanQuestionV2(
                question_id=question_id,
                position=position,
                question_text=prompt,
                focus=f"{configuration.focus_preset}: {focus_term}",
                question_type=kind,
                difficulty=configuration.difficulty,
                expected_minutes=expected_minutes,
                expected_followups=followups,
                origin="generated",
                knowledge_binding=unbound_question_knowledge(
                    "synthetic_evaluation_fixture"
                ).model_dump(mode="json"),
            )
        )
    return InterviewPlanV2(
        title=f"{item.scenario_domain} {configuration.focus_preset} run {run_number}",
        configuration_snapshot=configuration,
        questions=tuple(questions),
    )


def _validate_attempt_coverage(
    case_by_id: dict[str, Any],
    attempts: list[InitialQuestionEvalAttempt],
) -> None:
    keys = [(item.case_id, item.run_number) for item in attempts]
    if len(keys) != len(set(keys)):
        raise ValueError("initial question attempt keys must be unique")
    unknown = sorted({item.case_id for item in attempts} - set(case_by_id))
    if unknown:
        raise ValueError(f"unknown initial question cases: {unknown}")
    expected_keys = {
        (case_id, run_number)
        for case_id, case in case_by_id.items()
        for run_number in range(
            1,
            InitialQuestionCaseInput.model_validate(case.input).runs_per_case + 1,
        )
    }
    if set(keys) != expected_keys:
        missing = sorted(expected_keys - set(keys))
        extra = sorted(set(keys) - expected_keys)
        raise ValueError(f"initial question attempt coverage mismatch missing={missing} extra={extra}")
    for attempt in attempts:
        case = case_by_id[attempt.case_id]
        if attempt.partition != case.partition:
            raise ValueError("attempt partition does not match dataset")
        expected_configuration = PlanConfigurationSnapshot.model_validate(
            InitialQuestionCaseInput.model_validate(case.input).configuration
        )
        if attempt.plan.configuration_snapshot != expected_configuration:
            raise ValueError("attempt configuration does not match frozen dataset")


def _quality_stability(
    case_by_id: dict[str, Any],
    attempts: list[InitialQuestionEvalAttempt],
) -> float:
    by_case: dict[str, list[tuple[bool, ...]]] = defaultdict(list)
    for attempt in attempts:
        signature = tuple(
            all(
                bool(getattr(review, field))
                for review in attempt.reviews
            )
            for field in (
                "jd_resume_relevant",
                "configured_focus_covered",
                "difficulty_fit",
                "single_clear_answerable",
                "answerable_within_budget",
            )
        )
        by_case[attempt.case_id].append(signature)
    stable = sum(len(set(values)) == 1 for values in by_case.values())
    return _ratio(stable, len(case_by_id))


def _cross_run_exact_duplicates(attempts: list[InitialQuestionEvalAttempt]) -> int:
    grouped: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for item in attempts:
        grouped[item.case_id].append(
            tuple(_normalize_question(question.question_text) for question in item.plan.questions)
        )
    return sum(len(values) != len(set(values)) for values in grouped.values())


def _cross_run_mean_overlap(attempts: list[InitialQuestionEvalAttempt]) -> float:
    grouped: dict[str, list[set[str]]] = defaultdict(list)
    for item in attempts:
        grouped[item.case_id].append(
            {_normalize_question(question.question_text) for question in item.plan.questions}
        )
    overlaps = []
    for values in grouped.values():
        first, second = values[0], values[1]
        overlaps.append(_ratio(len(first & second), len(first | second)))
    return sum(overlaps) / len(overlaps) if overlaps else 0.0


def _normalize_question(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
