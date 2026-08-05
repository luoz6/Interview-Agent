from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from app.services.prep import InterviewPlan, InterviewQuestion


class PrepPlanError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = dict(details or {})

    def public_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": deepcopy(self.details),
        }


def plan_not_found(plan_id: str) -> PrepPlanError:
    return PrepPlanError(
        "PREP_PLAN_NOT_FOUND",
        "面试计划不存在，请重新生成。",
        status_code=404,
        details={"plan_id": plan_id},
    )


def plan_expired(plan_id: str) -> PrepPlanError:
    return PrepPlanError(
        "PREP_PLAN_EXPIRED",
        "面试计划已过期，请重新生成。",
        status_code=410,
        details={"plan_id": plan_id},
    )


def build_prep_plan_record(
    *,
    plan: InterviewPlan,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
    durability: str,
    created_at: datetime,
    expires_at: datetime,
    source_draft_id: str | None = None,
) -> dict[str, Any]:
    plan_id = f"prep_{uuid4()}"
    questions = []
    for position, question in enumerate(plan.questions, start=1):
        questions.append(
            {
                "question_id": f"pq_{uuid4()}",
                "position": position,
                "kind": question.kind,
                "prompt": question.prompt,
                "focus": question.focus,
                "required": False,
                "enabled": True,
                "source_signals": _source_signals(plan, question.id),
            }
        )
    source_sha256 = source_digest(job_description, resume_text)
    public = {
        "plan_id": plan_id,
        "plan_version": 1,
        "state": "editable",
        "expires_at": _iso(expires_at),
        "source_sha256": source_sha256,
        "title": plan.title,
        "questions": questions,
        "prep_context": _public_context(plan),
        "job_tags": list(job_tags),
        "durability": durability,
    }
    return {
        "public": public,
        "internal_plan": plan.model_dump(mode="json"),
        "job_description": job_description,
        "resume_text": resume_text,
        "job_tags": list(job_tags),
        "source_draft_id": source_draft_id,
        "source_sha256": source_sha256,
        "created_at": _iso(created_at),
        "updated_at": _iso(created_at),
        "expires_at": _iso(expires_at),
        "state": "editable",
        "consumed_session_id": None,
        "consumed_command_id": None,
        "consumed_plan_version": None,
        "versions": {
            1: version_snapshot(public, change_type="created")
        },
    }


def public_from_record(record: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(record["public"])
    payload["state"] = record["state"]
    if record.get("consumed_session_id"):
        payload["consumed_session_id"] = record["consumed_session_id"]
    return payload


def launch_plan_from_record(record: dict[str, Any]) -> tuple[InterviewPlan, list[dict[str, Any]]]:
    enabled = sorted(
        (question for question in record["public"]["questions"] if question["enabled"]),
        key=lambda question: question["position"],
    )
    validate_public_questions(enabled)
    projected = []
    mappings = []
    for position, question in enumerate(enabled, start=1):
        session_question_id = f"q{position}"
        projected.append(
            InterviewQuestion(
                id=session_question_id,
                kind=question["kind"],
                prompt=question["prompt"],
                focus=question["focus"],
            )
        )
        mappings.append(
            {
                "plan_question_id": question["question_id"],
                "session_question_id": session_question_id,
                "position": position,
                "kind": question["kind"],
            }
        )
    original = InterviewPlan.model_validate(record["internal_plan"])
    return InterviewPlan(
        title=record["public"]["title"],
        questions=projected,
        prep_context=original.prep_context,
    ), mappings


def apply_plan_operations(
    public: dict[str, Any],
    *,
    expected_version: int,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    current_version = int(public["plan_version"])
    if expected_version != current_version:
        raise PrepPlanError(
            "PREP_PLAN_VERSION_CONFLICT",
            "计划已经更新，请确认最新版本。",
            status_code=409,
            retryable=True,
            details={"plan_id": public["plan_id"], "latest_version": current_version},
        )
    if not operations:
        raise PrepPlanError(
            "PREP_PLAN_EMPTY_OPERATION",
            "至少需要一个计划修改操作。",
            status_code=422,
        )
    seen: set[tuple[str, str]] = set()
    working = deepcopy(public)
    questions = {item["question_id"]: item for item in working["questions"]}
    for operation in operations:
        operation_type = str(operation.get("type") or "")
        question_id = str(operation.get("question_id") or "")
        key = (question_id, operation_type)
        if key in seen:
            raise PrepPlanError(
                "PREP_PLAN_DUPLICATE_OPERATION",
                "同一题目的同类操作不能重复。",
                status_code=422,
                details={"question_id": question_id, "type": operation_type},
            )
        seen.add(key)
        question = questions.get(question_id)
        if question is None:
            raise PrepPlanError(
                "PREP_PLAN_QUESTION_NOT_FOUND",
                "计划中的题目不存在。",
                status_code=422,
                details={"question_id": question_id},
            )
        if operation_type == "set_required":
            question["required"] = bool(operation.get("required"))
        elif operation_type == "set_enabled":
            enabled = bool(operation.get("enabled"))
            if not enabled and question["required"]:
                raise PrepPlanError(
                    "PREP_PLAN_REQUIRED_QUESTION_DISABLED",
                    "必问题不能被排除，请先取消必问标记。",
                    status_code=422,
                    details={"question_id": question_id},
                )
            if question["enabled"] != enabled:
                question["enabled"] = enabled
                question["position"] = None if not enabled else _next_position(working["questions"])
        elif operation_type == "set_focus":
            focus = str(operation.get("focus") or "").strip()
            if not focus:
                raise PrepPlanError(
                    "PREP_PLAN_INVALID_FOCUS",
                    "考察重点不能为空。",
                    status_code=422,
                    details={"question_id": question_id},
                )
            question["focus"] = focus
        elif operation_type == "move":
            if not question["enabled"]:
                raise PrepPlanError(
                    "PREP_PLAN_DISABLED_QUESTION_MOVE",
                    "已排除题目不能排序。",
                    status_code=422,
                    details={"question_id": question_id},
                )
            _move_question(working["questions"], question_id, operation.get("position"))
        else:
            raise PrepPlanError(
                "PREP_PLAN_UNKNOWN_OPERATION",
                "计划修改操作不受支持。",
                status_code=422,
                details={"type": operation_type},
            )
        _normalize_positions(working["questions"])
    validate_public_questions([item for item in working["questions"] if item["enabled"]])
    working["plan_version"] = current_version + 1
    return working


def validate_public_questions(enabled: list[dict[str, Any]]) -> None:
    if not 3 <= len(enabled) <= 5:
        raise PrepPlanError(
            "PREP_PLAN_QUESTION_LIMIT",
            "启用题目必须保持 3 到 5 道。",
            status_code=422,
            details={"enabled_question_count": len(enabled)},
        )
    ids = [item["question_id"] for item in enabled]
    if len(ids) != len(set(ids)):
        raise PrepPlanError(
            "PREP_PLAN_DUPLICATE_QUESTION_ID",
            "计划题目标识重复。",
            status_code=422,
        )
    if [item["position"] for item in enabled] != list(range(1, len(enabled) + 1)):
        raise PrepPlanError(
            "PREP_PLAN_INVALID_POSITION",
            "启用题目顺序不连续。",
            status_code=422,
        )


def version_snapshot(
    public: dict[str, Any],
    *,
    change_type: str,
    replaced_question_id: str | None = None,
    replacement_question_id: str | None = None,
) -> dict[str, Any]:
    return {
        "plan_id": public["plan_id"],
        "version": public["plan_version"],
        "public_snapshot": deepcopy(public),
        "change_type": change_type,
        "replaced_question_id": replaced_question_id,
        "replacement_question_id": replacement_question_id,
        "created_at": _iso(datetime.now(timezone.utc)),
    }


def source_digest(job_description: str, resume_text: str) -> str:
    canonical = json.dumps(
        {"job_description": job_description, "resume_text": resume_text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _source_signals(plan: InterviewPlan, question_id: str) -> list[str]:
    context = plan.prep_context
    if not context:
        return ["jd", "resume"]
    hint = next((item for item in context.question_hints if item.question_id == question_id), None)
    if hint and hint.evidence_ids:
        return ["jd", "resume", "knowledge"]
    return ["jd", "resume"]


def _public_context(plan: InterviewPlan) -> dict[str, Any]:
    from app.services.prep import public_interview_plan_payload

    return public_interview_plan_payload(plan).get("prep_context", {})


def _normalize_positions(questions: list[dict[str, Any]]) -> None:
    enabled = sorted(
        (item for item in questions if item["enabled"]),
        key=lambda item: item["position"] if item["position"] is not None else 10**6,
    )
    for position, item in enumerate(enabled, start=1):
        item["position"] = position
    for item in questions:
        if not item["enabled"]:
            item["position"] = None


def _move_question(questions: list[dict[str, Any]], question_id: str, requested: Any) -> None:
    enabled = sorted(
        (item for item in questions if item["enabled"]),
        key=lambda item: item["position"],
    )
    try:
        position = int(requested)
    except (TypeError, ValueError) as exc:
        raise PrepPlanError(
            "PREP_PLAN_INVALID_POSITION",
            "题目位置必须是有效整数。",
            status_code=422,
        ) from exc
    if position < 1 or position > len(enabled):
        raise PrepPlanError(
            "PREP_PLAN_INVALID_POSITION",
            "题目位置超出可用范围。",
            status_code=422,
            details={"position": position},
        )
    moving = next(item for item in enabled if item["question_id"] == question_id)
    enabled.remove(moving)
    enabled.insert(position - 1, moving)
    for index, item in enumerate(enabled, start=1):
        item["position"] = index


def _next_position(questions: list[dict[str, Any]]) -> int:
    return sum(1 for item in questions if item["enabled"])


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.isoformat()
