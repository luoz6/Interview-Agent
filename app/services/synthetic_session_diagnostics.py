from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.followup_diagnostics import (
    FollowupDiagnosticInput,
    FollowupPolicySnapshot,
    diagnose_followup,
)
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewFeedback
from app.services.report_contract import assemble_interview_report_from_feedbacks


RM5_SCENARIO_SCHEMA = "full-interview-synthetic-scenarios-v1"
RM5_SCENARIO_VERSION = "rm5-synthetic-scenarios-2026-08-18-v1"
RM5_ARTIFACT_SCHEMA = "rm5-synthetic-session-artifact-v1"
RM5_OBSERVER_SCHEMA = "rm5-synthetic-diagnostic-observer-v1"
RM5_COMPLETION_STATUS = "RM5_SYNTHETIC_DIAGNOSTIC_COMPLETE"
RM5_FAILURE_STATUS = "RM5_SYNTHETIC_DIAGNOSTIC_FAILED"
RM5_EXECUTION_MODE = "offline_contract"
RM5_PROVIDER_BUDGET = 0


class SyntheticQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["project", "technical", "system-design", "behavioral"]
    prompt: str = Field(min_length=1)
    focus: str = Field(min_length=1)


class SyntheticPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    questions: list[SyntheticQuestion] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def unique_question_ids(self) -> "SyntheticPlan":
        ids = [item.id for item in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("RM5 question ids must be unique")
        return self


class SyntheticAnswerBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    keywords: list[str] = Field(min_length=1)
    answers: list[str] = Field(min_length=1, max_length=3)


class SyntheticScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1)
    job_description: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    job_tags: list[str] = Field(min_length=1)
    single_question_gap_topic: str = Field(min_length=1)
    fixture_plan: SyntheticPlan
    answer_branches: list[SyntheticAnswerBranch] = Field(min_length=1)
    default_answers: list[str] = Field(min_length=1, max_length=3)


class SyntheticScenarioSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["full-interview-synthetic-scenarios-v1"]
    scenario_version: Literal["rm5-synthetic-scenarios-2026-08-18-v1"]
    scenarios: list[SyntheticScenario] = Field(min_length=6, max_length=8)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def default_rm5_scenario_path(repository_root: Path) -> Path:
    return repository_root / "tests" / "golden" / "full_interview_scenarios_v1.json"


def load_rm5_scenarios(path: Path | str) -> tuple[SyntheticScenarioSet, str]:
    source = Path(path)
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw.decode("utf-8"))
    scenarios = SyntheticScenarioSet.model_validate(payload)
    if scenarios.schema_version != RM5_SCENARIO_SCHEMA:
        raise ValueError("RM5 scenario schema mismatch")
    if scenarios.scenario_version != RM5_SCENARIO_VERSION:
        raise ValueError("RM5 scenario version mismatch")
    _assert_synthetic_safe(payload)
    return scenarios, digest


def build_rm5_artifact(
    *,
    scenario_path: Path | str,
    output_dir: Path | str,
    scenario_ids: list[str] | None = None,
    run_id: str = "rm5-offline-diagnostic-v1",
    fail_after: int | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    scenarios, scenario_sha256 = load_rm5_scenarios(scenario_path)
    selected = _select_scenarios(scenarios, scenario_ids)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    records_dir = target / "session-records"
    records_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = target / "multi-question-artifacts"
    reports_dir.mkdir(parents=True, exist_ok=True)
    existing = _load_existing_records(records_dir) if resume else {}

    manifest: dict[str, Any] = {
        "schema_version": RM5_ARTIFACT_SCHEMA,
        "run_id": run_id,
        "scenario_schema_version": scenarios.schema_version,
        "scenario_version": scenarios.scenario_version,
        "scenario_file_sha256": scenario_sha256,
        "selected_scenario_ids": [item.scenario_id for item in selected],
        "execution_mode": RM5_EXECUTION_MODE,
        "model_identity": "not_run_offline_contract",
        "provider_budget": RM5_PROVIDER_BUDGET,
        "provider_called": False,
        "provider_invocations": 0,
        "real_postgres_dependency": False,
        "diagnostic_only": True,
        "formal_evidence_eligible": False,
        "terminal": False,
        "status": "RUNNING",
        "resume_supported": True,
        "resumed": bool(resume),
        "completed_scenario_count": 0,
        "failed_scenario_id": None,
        "failure_code": None,
        "records": [],
    }
    _write_json(target / "manifest.json", manifest)

    try:
        completed = 0
        for scenario in selected:
            if scenario.scenario_id in existing:
                record = existing[scenario.scenario_id]
            else:
                if fail_after is not None and completed >= fail_after:
                    raise _RM5InjectedFailure(scenario.scenario_id)
                record = _build_session_record(scenario, run_id=run_id)
                _write_json(records_dir / f"{scenario.scenario_id}.json", record)
                _write_json(
                    reports_dir / f"{scenario.scenario_id}.json",
                    {
                        "schema_version": "rm5-multi-question-report-artifact-v1",
                        "status": RM5_COMPLETION_STATUS,
                        "scenario_id": scenario.scenario_id,
                        "question_ids": record["report"]["question_ids"],
                        "report_sha256": record["report_sha256"],
                        "evidence_refs": record["report"]["evidence_refs"],
                        "observations": record["report"]["observations"],
                        "summary_claims": record["report"]["summary_claims"],
                        "priority_actions": record["report"]["priority_actions"],
                        "review_scope": "development_diagnostic",
                        "formal_evidence_eligible": False,
                    },
                )
            manifest["records"].append(record)
            completed += 1
            manifest["completed_scenario_count"] = completed
            _write_json(target / "manifest.json", manifest)
        manifest["terminal"] = True
        manifest["status"] = RM5_COMPLETION_STATUS
        manifest["completion_status"] = RM5_COMPLETION_STATUS
    except _RM5InjectedFailure as exc:
        manifest["terminal"] = True
        manifest["status"] = RM5_FAILURE_STATUS
        manifest["completion_status"] = RM5_FAILURE_STATUS
        manifest["failed_scenario_id"] = exc.scenario_id
        manifest["failure_code"] = "INJECTED_FAILURE_FOR_RESUME_CONTRACT"
    except Exception as exc:
        manifest["terminal"] = True
        manifest["status"] = RM5_FAILURE_STATUS
        manifest["completion_status"] = RM5_FAILURE_STATUS
        manifest["failure_code"] = type(exc).__name__
        manifest["failure_message"] = str(exc)[:300]
    _write_json(target / "manifest.json", manifest)
    _write_json(
        target / "diagnostic-observer.json",
        {
            "schema_version": RM5_OBSERVER_SCHEMA,
            "run_id": run_id,
            "review_scope": "development_diagnostic",
            "reviewer_kind": "diagnostic_observer",
            "review_status": "reviewed",
            "formal_evidence_eligible": False,
            "provider_called": False,
            "terminal_status": manifest["status"],
        },
    )
    return manifest


def replay_rm5_artifact(
    *,
    scenario_path: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    scenarios, digest = load_rm5_scenarios(scenario_path)
    target = Path(output_dir)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("scenario_file_sha256") != digest:
        raise ValueError("RM5 scenario file hash mismatch")
    if manifest.get("scenario_version") != scenarios.scenario_version:
        raise ValueError("RM5 scenario version mismatch")
    if manifest.get("provider_called") or manifest.get("provider_invocations"):
        raise ValueError("RM5 offline artifact contains Provider invocations")
    for item in manifest.get("records", []):
        path = target / "session-records" / f"{item['scenario_id']}.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved != item or saved.get("report_sha256") != _report_hash(saved):
            raise ValueError("RM5 session record replay mismatch")
    return manifest


def _build_session_record(scenario: SyntheticScenario, *, run_id: str) -> dict[str, Any]:
    questions = scenario.fixture_plan.questions
    plan = InterviewPlan(
        title=scenario.fixture_plan.title,
        questions=[InterviewQuestion(**item.model_dump()) for item in questions],
    )
    feedbacks: list[InterviewFeedback] = []
    followups: list[dict[str, Any]] = []
    for index, question in enumerate(plan.questions):
        branch = _select_branch(question, scenario)
        answers = branch.answers if branch else scenario.default_answers
        answer = answers[0]
        followup_answer = answers[1] if len(answers) > 1 else answer
        followup_text = _followup_prompt(question.focus)
        diagnostic = diagnose_followup(
            FollowupDiagnosticInput(
                session_id=f"{run_id}:{scenario.scenario_id}",
                question_id=question.id,
                question_text=question.prompt,
                focus=question.focus,
                candidate_answers=[answer, followup_answer],
                asked_followups=[followup_text],
                followup_count=1,
                policy=FollowupPolicySnapshot(
                    policy_version="fixed_v1",
                    max_followups=2,
                    max_context_chars=6000,
                    empty_clarification_limit=1,
                ),
            )
        )
        if diagnostic.deterministic_decision is None:
            raise ValueError("RM5 follow-up did not produce a deterministic decision")
        decision = diagnostic.deterministic_decision
        followups.append(
            {
                "question_id": question.id,
                "followup_text": followup_text,
                "answer": followup_answer,
                "diagnostics_version": diagnostic.diagnostics_version,
                "decision_action": decision.action,
                "reason_code": decision.reason_code,
                "provider_allowed": diagnostic.provider_allowed,
                "stop_decision": decision.action == "next_question",
            }
        )
        score = min(95, 68 + index * 3 + (4 if len(answers) > 1 else 0))
        feedbacks.append(
            InterviewFeedback(
                question_id=question.id,
                question_text=question.prompt,
                user_answer=answer,
                score=score,
                dimension_scores=DimensionScores(
                    breadth=score - 2,
                    depth=score,
                    architecture=score - 1,
                    engineering=score + 1,
                    communication=score + 2,
                ),
                evaluation_status="evaluated",
                evaluation_reason_code="synthetic_fixed_answer",
                evidence_count=1,
                applicable_dimensions=["depth", "engineering"],
                dimension_evidence=[
                    {
                        "dimension": "engineering",
                        "observed": ["固定 synthetic answer contains a concrete step."],
                        "missing": [] if len(answers) > 1 else ["verification boundary"],
                        "quality_signals": ["concrete_steps"],
                    }
                ],
                highlights=["synthetic fixed answer is replayable"],
                rationale="Deterministic RM5 fixture feedback; not a Provider judgment.",
                critique=(
                    "固定回答未覆盖完整验证边界。"
                    if len(answers) == 1
                    else "固定回答覆盖了步骤，但仍需在真实语境中观察。"
                ),
                better_answer="补充机制、失败边界、验证指标和回滚条件。",
                references=[],
            )
        )
    report = assemble_interview_report_from_feedbacks(
        session_id=f"{run_id}:{scenario.scenario_id}",
        feedbacks=feedbacks,
        report_path="full_session",
    )
    report_view = {
        "schema_version": report.report_schema_version,
        "question_ids": [item.question_id for item in report.feedbacks],
        "evidence_refs": [item.model_dump(mode="json") for item in report.evidence_refs],
        "observations": [item.model_dump(mode="json") for item in report.technical_appendix.observations],
        "summary_claims": [item.model_dump(mode="json") for item in report.summary_observations],
        "priority_actions": [item.model_dump(mode="json") for item in report.priority_actions],
    }
    report_sha256 = canonical_sha256(report_view)
    fixed_answers = {
        item.id: (
            (_select_branch(item, scenario).answers)
            if _select_branch(item, scenario)
            else scenario.default_answers
        )
        for item in questions
    }
    record = {
        "schema_version": "rm5-safe-session-record-v1",
        "run_id": run_id,
        "scenario_id": scenario.scenario_id,
        "scenario_version": RM5_SCENARIO_VERSION,
        "scenario_identity": {
            "job_description_sha256": hashlib.sha256(
                scenario.job_description.encode("utf-8")
            ).hexdigest(),
            "resume_sha256": hashlib.sha256(
                scenario.resume_text.encode("utf-8")
            ).hexdigest(),
            "expected_topics": [item.focus for item in questions],
            "fixed_answers_sha256": canonical_sha256(fixed_answers),
        },
        "model_identity": "not_run_offline_contract",
        "question_ids": [item.id for item in questions],
        "question_texts": [item.prompt for item in questions],
        "candidate_answer_branch": {
            item.id: (_select_branch(item, scenario).branch_id if _select_branch(item, scenario) else "default")
            for item in questions
        },
        "followup_texts": [item["followup_text"] for item in followups],
        "decisions": followups,
        "stop_decisions": [item["stop_decision"] for item in followups],
        "per_question": [
            {
                "question_id": item.question_id,
                "score": item.score,
                "score_status": item.evaluation_status,
            }
            for item in feedbacks
        ],
        "report_sha256": report_sha256,
        "report": report_view,
        "provider_budget": RM5_PROVIDER_BUDGET,
        "provider_called": False,
        "provider_invocations": 0,
        "real_postgres_dependency": False,
        "diagnostic_only": True,
        "formal_evidence_eligible": False,
        "artifact_paths": [
            f"session-records/{scenario.scenario_id}.json",
            f"multi-question-artifacts/{scenario.scenario_id}.json",
        ],
    }
    record["record_sha256"] = canonical_sha256(record)
    return record


def _report_hash(record: dict[str, Any]) -> str:
    report = record.get("report")
    return canonical_sha256(report) if report is not None else ""


def _select_scenarios(
    scenarios: SyntheticScenarioSet,
    scenario_ids: list[str] | None,
) -> list[SyntheticScenario]:
    if scenario_ids:
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("unknown or duplicate RM5 scenario id")
        selected = [item for item in scenarios.scenarios if item.scenario_id in scenario_ids]
        if len(selected) != len(set(scenario_ids)):
            raise ValueError("unknown or duplicate RM5 scenario id")
    else:
        selected = list(scenarios.scenarios)
    if not 6 <= len(selected) <= 8:
        raise ValueError("RM5 requires 6 to 8 synthetic scenarios")
    return selected


def _select_branch(
    question: SyntheticQuestion | InterviewQuestion,
    scenario: SyntheticScenario,
) -> SyntheticAnswerBranch | None:
    prompt = question.prompt.casefold()
    ranked = sorted(
        (
            sum(keyword.casefold() in prompt for keyword in branch.keywords),
            -index,
            branch,
        )
        for index, branch in enumerate(scenario.answer_branches)
        if any(keyword.casefold() in prompt for keyword in branch.keywords)
    )
    return ranked[-1][2] if ranked else None


def _followup_prompt(focus: str) -> str:
    return f"请补充 {focus} 的失败边界、验证方式和停止条件。"


def _load_existing_records(records_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in records_dir.glob("*.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        records[item["scenario_id"]] = item
    return records


def _assert_synthetic_safe(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    forbidden_patterns = (
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
        r"(?i)(api[_-]?key|access[_-]?token|password)\s*[:=]",
    )
    if any(re.search(pattern, serialized, flags=re.IGNORECASE) for pattern in forbidden_patterns):
        raise ValueError("RM5 fixture contains prohibited personal or secret data")


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class _RM5InjectedFailure(RuntimeError):
    def __init__(self, scenario_id: str) -> None:
        self.scenario_id = scenario_id


__all__ = [
    "RM5_ARTIFACT_SCHEMA",
    "RM5_COMPLETION_STATUS",
    "RM5_FAILURE_STATUS",
    "RM5_OBSERVER_SCHEMA",
    "RM5_SCENARIO_VERSION",
    "build_rm5_artifact",
    "canonical_sha256",
    "default_rm5_scenario_path",
    "load_rm5_scenarios",
    "replay_rm5_artifact",
]
