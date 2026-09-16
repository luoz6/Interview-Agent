"""Canonical question feedback contracts used by reports and evaluations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.knowledge.evidence import SafeKnowledgeCitation

__all__ = [
    "DimensionScores",
    "ScoreEvaluation",
    "ReportMissingTechnicalPointV2",
    "FeedbackReference",
    "InterviewFeedback",
]


class DimensionScores(BaseModel):
    breadth: int | None = Field(default=None, ge=0, le=100)
    depth: int | None = Field(default=None, ge=0, le=100)
    architecture: int | None = Field(default=None, ge=0, le=100)
    engineering: int | None = Field(default=None, ge=0, le=100)
    communication: int | None = Field(default=None, ge=0, le=100)


class ScoreEvaluation(BaseModel):
    status: Literal["evaluated", "not_evaluated", "insufficient_evidence"]
    reason_code: str
    score: int | None = Field(default=None, ge=0, le=100)
    evidence_count: int = Field(default=0, ge=0)
    eligible_count: int = Field(default=0, ge=0)
    evaluated_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_status(self) -> "ScoreEvaluation":
        if self.status == "evaluated" and self.score is None:
            raise ValueError("evaluated entries require a numeric score")
        if self.status != "evaluated" and self.score is not None:
            raise ValueError("non-evaluated entries cannot contain a numeric score")
        if self.evaluated_count > self.eligible_count:
            raise ValueError("evaluated_count cannot exceed eligible_count")
        return self


class ReportMissingTechnicalPointV2(BaseModel):
    point_id: str = Field(min_length=1, max_length=160)
    topic: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=2000)
    observation_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class FeedbackReference(BaseModel):
    chunk_id: str
    title: str
    source_type: str
    excerpt: str


class InterviewFeedback(BaseModel):
    question_id: str = Field(description="Question identifier")
    question_text: str = Field(description="Original interview question text")
    user_answer: str = Field(description="Summary of the candidate answer")
    answer_state: Literal["answered", "skipped", "unanswered"] = "answered"
    score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Question score from 0 to 100",
    )
    dimension_scores: DimensionScores
    evaluation_status: Literal[
        "evaluated", "not_evaluated", "insufficient_evidence"
    ] = "evaluated"
    evaluation_reason_code: str = "sufficient_evidence"
    evidence_count: int = Field(default=0, ge=0)
    dimension_evaluations: dict[str, ScoreEvaluation] = Field(default_factory=dict)
    applicable_dimensions: list[str] = Field(default_factory=list)
    dimension_evidence: list[dict[str, Any]] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    rationale: str = Field(description="Why the score was assigned")
    critique: str = Field(description="Main flaw or critique")
    better_answer: str = Field(description="Improved answer to practice")
    answer_structure_suggestion: str | None = Field(
        default=None,
        max_length=2000,
    )
    missing_technical_points: list[ReportMissingTechnicalPointV2] = Field(
        default_factory=list,
    )
    example_rewrite: str | None = Field(default=None, max_length=4000)
    example_rewrite_evidence_refs: list[str] = Field(default_factory=list)
    references: list[FeedbackReference]
    knowledge_citations: list[SafeKnowledgeCitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evaluation_state(self) -> "InterviewFeedback":
        values = self.dimension_scores.model_dump().values()
        if self.evaluation_status == "evaluated" and self.score is None:
            raise ValueError("evaluated feedback requires a score")
        if self.evaluation_status != "evaluated":
            if self.score is not None or any(value is not None for value in values):
                raise ValueError("non-evaluated feedback cannot contain numeric scores")
        if self.example_rewrite and not self.example_rewrite_evidence_refs:
            raise ValueError("example rewrite requires candidate evidence refs")
        if not self.example_rewrite and self.example_rewrite_evidence_refs:
            raise ValueError("example rewrite refs require example rewrite text")
        return self
