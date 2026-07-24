from __future__ import annotations

import hashlib
import json
from typing import Literal, TypedDict

from pydantic import BaseModel, Field


def _sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ReviewMessageReference(BaseModel):
    sequence_no: int
    role: Literal["interviewer", "candidate"]
    question_id: str | None = None
    content_sha256: str


class ReviewQuestionInput(BaseModel):
    question_id: str
    kind: str
    prompt_sha256: str
    answer_state: Literal["answered", "skipped", "unanswered"]
    message_content_sha256: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_sha256: dict[str, str] = Field(default_factory=dict)
    input_sha256: str


class DurableReviewInputManifest(BaseModel):
    session_id: str
    finished_state_version: int
    plan_sha256: str
    corpus_manifest_sha256: str | None = None
    message_refs: list[ReviewMessageReference]
    questions: list[ReviewQuestionInput]
    input_sha256: str

    @classmethod
    def from_finished_state(cls, state: dict) -> "DurableReviewInputManifest":
        plan = state["plan"]
        prep_context = plan.prep_context
        evidence_hashes = (
            {
                item.evidence_id: item.content_sha256
                for item in prep_context.evidence_refs
            }
            if prep_context is not None
            else {}
        )
        evidence_ids = (
            {
                item.question_id: list(item.evidence_ids)
                for item in prep_context.question_hints
            }
            if prep_context is not None
            else {}
        )
        corpus_manifest_sha256 = (
            prep_context.binding_snapshot.corpus_manifest_sha256
            if prep_context is not None
            and prep_context.binding_snapshot is not None
            else None
        )
        messages = list(state.get("messages", []))
        message_refs = [
            ReviewMessageReference(
                sequence_no=index,
                role=message["role"],
                question_id=message.get("question_id"),
                content_sha256=_sha256(message["content"]),
            )
            for index, message in enumerate(messages, start=1)
        ]
        answered_question_ids = {
            message["question_id"]
            for message in messages
            if message["role"] == "candidate" and message.get("question_id")
        }
        skipped_question_ids = set(state.get("skipped_question_ids", []))
        questions = []
        for question in plan.questions:
            if question.id in answered_question_ids:
                answer_state = "answered"
            elif question.id in skipped_question_ids:
                answer_state = "skipped"
            else:
                answer_state = "unanswered"
            bound_evidence_ids = evidence_ids.get(question.id, [])
            question_message_hashes = [
                item.content_sha256
                for item in message_refs
                if item.question_id == question.id
            ]
            question_payload = {
                "question_id": question.id,
                "kind": question.kind,
                "prompt_sha256": _sha256(question.prompt),
                "answer_state": answer_state,
                "message_content_sha256": question_message_hashes,
                "evidence_ids": bound_evidence_ids,
                "evidence_sha256": {
                    evidence_id: evidence_hashes[evidence_id]
                    for evidence_id in bound_evidence_ids
                    if evidence_id in evidence_hashes
                },
            }
            questions.append(
                ReviewQuestionInput(
                    **question_payload,
                    input_sha256=_sha256(question_payload),
                )
            )
        plan_sha256 = _sha256(
            [
                {
                    "id": question.id,
                    "kind": question.kind,
                    "prompt_sha256": _sha256(question.prompt),
                    "focus_sha256": _sha256(question.focus),
                }
                for question in plan.questions
            ]
        )
        manifest_payload = {
            "session_id": state["session_id"],
            "finished_state_version": state["state_version"],
            "plan_sha256": plan_sha256,
            "corpus_manifest_sha256": corpus_manifest_sha256,
            "message_refs": [item.model_dump(mode="json") for item in message_refs],
            "questions": [item.model_dump(mode="json") for item in questions],
        }
        return cls(
            **manifest_payload,
            input_sha256=_sha256(manifest_payload),
        )


class DurableReviewState(TypedDict):
    job_id: str
    session_id: str
    review_engine: Literal["langgraph-review-v1"]
    review_graph_schema_version: Literal["langgraph-review-v1"]
    review_input_manifest: dict
    missing_question_ids: list[str]
    completed_question_ids: list[str]
    failed_question_ids: list[str]
    next_batch_start: int
    current_batch_question_ids: list[str]
    provider_attempt: int
    expected_retry_attempt: int | None
    retry_resume_attempt: int | None
    quality_repair_count: int
    report_sha256: str | None
    report_ref: str | None
    error_code: str | None
    current_question_id: str | None
    validation_outcome: Literal["passed", "failed"] | None
    generation_outcome: Literal["completed", "retryable", "terminal"] | None
    quality_issues: list[dict]


def make_durable_review_initial_state(job: dict, finished_state: dict) -> DurableReviewState:
    manifest = DurableReviewInputManifest.from_finished_state(finished_state)
    return {
        "job_id": job["job_id"],
        "session_id": finished_state["session_id"],
        "review_engine": "langgraph-review-v1",
        "review_graph_schema_version": job["review_graph_schema_version"],
        "review_input_manifest": manifest.model_dump(mode="json"),
        "missing_question_ids": [item.question_id for item in manifest.questions],
        "completed_question_ids": [],
        "failed_question_ids": [],
        "next_batch_start": 0,
        "current_batch_question_ids": [],
        "provider_attempt": 1,
        "expected_retry_attempt": None,
        "retry_resume_attempt": None,
        "quality_repair_count": 0,
        "report_sha256": None,
        "report_ref": None,
        "error_code": None,
        "current_question_id": None,
        "validation_outcome": None,
        "generation_outcome": None,
        "quality_issues": [],
    }


def review_thread_id(job_id: str) -> str:
    return f"review:{job_id}"


def is_reusable_for_review(
    record,
    manifest: DurableReviewInputManifest,
    *,
    question_id: str,
    graph_schema_version: str,
) -> bool:
    question = next(
        (item for item in manifest.questions if item.question_id == question_id),
        None,
    )
    return bool(
        question is not None
        and record.status == "completed"
        and record.feedback is not None
        and record.review_input_sha256 == manifest.input_sha256
        and record.question_input_sha256 == question.input_sha256
        and record.review_graph_schema_version == graph_schema_version
        and record.output_sha256
    )
