from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from pydantic import ValidationError

from app.services.context_artifacts import (
    AnchoredCompressedUnit,
    ArtifactPayload,
    CompressionSourceSegment,
    ContextArtifactValidationFailed,
    EvidenceCompressionArtifact,
    PrepContextArtifact,
    QuestionConversationArtifact,
    QuestionMemoryArtifact,
    canonical_json,
    parse_artifact_payload,
)
from app.services.context_compression_intent import CompressionIntent
from app.services.context_compression_request import ResolvedCompressionRequest
from app.services.token_estimation import TokenEstimator


_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?(?![A-Za-z0-9_])")
_IDENTIFIER_RE = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+|"
    r"[a-z]+(?:[A-Z][A-Za-z0-9]*)+)\b"
)


@dataclass(frozen=True)
class CompressionValidationStats:
    source_segment_count: int
    output_unit_count: int
    supporting_excerpt_count: int
    estimated_output_tokens: int


@dataclass(frozen=True)
class ValidatedCompressionArtifact:
    payload: ArtifactPayload
    stats: CompressionValidationStats


def validate_compression_artifact(
    *,
    request: ResolvedCompressionRequest,
    payload: dict,
    estimator: TokenEstimator,
    model: str,
    expected_question_id_sha256: str | None = None,
    expected_evidence_content_sha256: str | None = None,
    expected_session_scope_sha256: str | None = None,
    expected_question_focus_sha256: str | None = None,
    expected_source_manifest_sha256: str | None = None,
) -> ValidatedCompressionArtifact:
    """Validate one payload against one authoritative resolved request."""

    if not isinstance(request, ResolvedCompressionRequest):
        raise TypeError("request must be a ResolvedCompressionRequest")
    policy = request.policy
    source_segments = request.source_segments

    try:
        validated = parse_artifact_payload(policy.artifact_type, payload)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ContextArtifactValidationFailed(
            "context artifact payload schema is invalid"
        ) from exc
    resolved_intent = None
    if request.intent is not None:
        try:
            resolved_intent = CompressionIntent.model_validate(
                request.intent
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise ContextArtifactValidationFailed(
                "context artifact compression intent is invalid"
            ) from exc
    if validated.schema_version != policy.output_schema_version:
        raise ContextArtifactValidationFailed(
            "context artifact output schema version is invalid"
        )
    if isinstance(validated, QuestionConversationArtifact):
        if (
            expected_question_id_sha256 is None
            or validated.question_id_sha256 != expected_question_id_sha256
        ):
            raise ContextArtifactValidationFailed(
                "context artifact question identity is invalid"
            )
    if isinstance(validated, QuestionMemoryArtifact):
        expected_digests = (
            (
                validated.session_scope_sha256,
                expected_session_scope_sha256,
                "session scope",
            ),
            (
                validated.question_id_sha256,
                expected_question_id_sha256,
                "question identity",
            ),
            (
                validated.question_focus_sha256,
                expected_question_focus_sha256,
                "question focus",
            ),
            (
                validated.source_manifest_sha256,
                expected_source_manifest_sha256,
                "source manifest",
            ),
        )
        for actual, expected, label in expected_digests:
            if expected is None or actual != expected:
                raise ContextArtifactValidationFailed(
                    f"context artifact {label} is invalid"
                )
    if isinstance(validated, EvidenceCompressionArtifact):
        if (
            expected_evidence_content_sha256 is None
            or validated.evidence_content_sha256
            != expected_evidence_content_sha256
        ):
            raise ContextArtifactValidationFailed(
                "context artifact evidence identity is invalid"
            )

    source_by_digest: dict[str, CompressionSourceSegment] = {}
    source_indexes: set[int] = set()
    for source in source_segments:
        if source.content_sha256 in source_by_digest or source.segment_index in source_indexes:
            raise ContextArtifactValidationFailed(
                "context artifact source manifest is not unique"
            )
        source_by_digest[source.content_sha256] = source
        source_indexes.add(source.segment_index)

    units = list(_iter_units(validated))
    if len(units) > policy.max_output_units:
        raise ContextArtifactValidationFailed(
            "context artifact output exceeds the unit limit"
        )
    if isinstance(
        validated,
        (QuestionConversationArtifact, QuestionMemoryArtifact),
    ):
        source_message_count = sum(
            source.segment_type == "conversation_message"
            for source in source_segments
        )
        if validated.source_message_count != source_message_count:
            raise ContextArtifactValidationFailed(
                "context artifact source count is invalid"
            )

    supporting_excerpt_count = 0
    output_text_parts: list[str] = []

    for unit in units:
        output_text_parts.append(unit.summary)
        anchored_sources = []
        for digest in unit.source_segment_sha256:
            source = source_by_digest.get(digest)
            if source is None:
                raise ContextArtifactValidationFailed(
                    "context artifact contains an unknown source anchor"
                )
            anchored_sources.append(source)

        anchored_text = "\n".join(source.content for source in anchored_sources)
        anchored_numbers = _extract_numbers(anchored_text)
        anchored_identifiers = _extract_identifiers(anchored_text)

        # Supporting excerpts are additional provenance and may come from a
        # different cited source than the summary. They never replace the
        # independent exact-source summary requirement below.
        for excerpt in unit.supporting_excerpts:
            supporting_excerpt_count += 1
            if not any(excerpt in source.content for source in anchored_sources):
                raise ContextArtifactValidationFailed(
                    "context artifact supporting excerpt is not grounded"
                )
            if (
                estimator.estimate_text(excerpt, model=model)
                > policy.max_supporting_excerpt_tokens
            ):
                raise ContextArtifactValidationFailed(
                    "context artifact exceeds the supporting excerpt budget"
                )

        if resolved_intent is not None:
            if not unit.supporting_excerpts:
                raise ContextArtifactValidationFailed(
                    "context artifact intent-aware unit requires a supporting excerpt"
                )
            if not any(
                unit.summary in source.content for source in anchored_sources
            ):
                raise ContextArtifactValidationFailed(
                    "context artifact intent-aware summary is not an exact source excerpt"
                )

        summary_numbers = _extract_numbers(unit.summary)
        summary_identifiers = _extract_identifiers(unit.summary)
        if not summary_numbers.issubset(
            anchored_numbers
        ) or not summary_identifiers.issubset(
            anchored_identifiers
        ):
            raise ContextArtifactValidationFailed(
                "context artifact summary failed source grounding"
            )

    if isinstance(validated, EvidenceCompressionArtifact):
        for excerpt in validated.exact_excerpts:
            output_text_parts.append(excerpt)
            if not any(excerpt in source.content for source in source_segments):
                raise ContextArtifactValidationFailed(
                    "context artifact exact excerpt is not grounded"
                )
            if (
                estimator.estimate_text(excerpt, model=model)
                > policy.max_supporting_excerpt_tokens
            ):
                raise ContextArtifactValidationFailed(
                    "context artifact exceeds the supporting excerpt budget"
                )

    if resolved_intent is not None:
        source_text = "\n".join(source.content for source in source_segments)
        output_text = "\n".join(output_text_parts)
        if "numbers" in resolved_intent.preserve and not _extract_numbers(
            source_text
        ).issubset(_extract_numbers(output_text)):
            raise ContextArtifactValidationFailed(
                "context artifact omitted a required number"
            )
        if "identifiers" in resolved_intent.preserve and not _extract_identifiers(
            source_text
        ).issubset(_extract_identifiers(output_text)):
            raise ContextArtifactValidationFailed(
                "context artifact omitted a required identifier"
            )

    estimated_output_tokens = estimator.estimate_text(
        canonical_json(validated),
        model=model,
    )
    if (
        estimated_output_tokens
        > request.resolved_target_output_tokens
    ):
        raise ContextArtifactValidationFailed(
            "context artifact exceeds the output budget"
        )

    return ValidatedCompressionArtifact(
        payload=validated,
        stats=CompressionValidationStats(
            source_segment_count=len(source_segments),
            output_unit_count=len(units),
            supporting_excerpt_count=supporting_excerpt_count,
            estimated_output_tokens=estimated_output_tokens,
        ),
    )


def _iter_units(payload: ArtifactPayload) -> Iterable[AnchoredCompressedUnit]:
    if isinstance(payload, QuestionConversationArtifact):
        yield from payload.units
        yield from payload.unresolved_topics
        return
    if isinstance(payload, QuestionMemoryArtifact):
        yield from payload.claims
        yield from payload.unresolved_topics
        return
    if isinstance(payload, EvidenceCompressionArtifact):
        yield from payload.units
        return
    if isinstance(payload, PrepContextArtifact):
        yield from payload.role_units
        yield from payload.responsibility_units
        yield from payload.experience_units
        yield from payload.project_units
        yield from payload.constraint_units


def _extract_numbers(text: str) -> set[str]:
    """Return the deliberately narrow numeric-literal preservation contract."""

    return set(_NUMBER_RE.findall(text))


def _extract_identifiers(text: str) -> set[str]:
    """Return snake_case and lower-camel-case identifiers only."""

    return set(_IDENTIFIER_RE.findall(text))
