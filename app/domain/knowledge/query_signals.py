from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.knowledge.lexical import normalize_lexical_text
from app.domain.knowledge.retrieval import RetrievalCandidate


QuerySignal = Literal[
    "lexical_dominant",
    "semantic_dominant",
    "balanced",
]

_QUOTED_PHRASE = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{2,})[\"'“”‘’]")
_ACRONYM = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9+#._-]{1,}(?![A-Za-z0-9])")
_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


class QuerySignalDecision(BaseModel):
    """Privacy-safe deterministic decision used immediately before Fusion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_signal: QuerySignal
    semantic_weight: float = Field(gt=0)
    lexical_weight: float = Field(gt=0)
    reason_codes: tuple[str, ...] = ()


class QuerySignalAnalyzer:
    VERSION = "query-signal-v1"

    def decide(
        self,
        query_text: str,
        *,
        semantic_candidates: Sequence[RetrievalCandidate],
        lexical_candidates: Sequence[RetrievalCandidate],
        base_semantic_weight: float,
        base_lexical_weight: float,
        enabled: bool,
        semantic_available: bool,
        lexical_available: bool,
    ) -> QuerySignalDecision:
        if not enabled:
            return QuerySignalDecision(
                query_signal="balanced",
                semantic_weight=base_semantic_weight,
                lexical_weight=base_lexical_weight,
                reason_codes=("query_aware_fusion_disabled",),
            )

        if semantic_available and not lexical_available:
            return self._decision(
                "semantic_dominant",
                base_semantic_weight,
                base_lexical_weight,
                ("lexical_channel_unavailable",),
            )
        if lexical_available and not semantic_available:
            return self._decision(
                "lexical_dominant",
                base_semantic_weight,
                base_lexical_weight,
                ("semantic_channel_unavailable",),
            )
        if not semantic_available and not lexical_available:
            return QuerySignalDecision(
                query_signal="balanced",
                semantic_weight=base_semantic_weight,
                lexical_weight=base_lexical_weight,
                reason_codes=("retrieval_channels_unavailable",),
            )

        normalized_query = normalize_lexical_text(query_text)
        compact_query = "".join(normalized_query.split())
        character_count = max(1, len(compact_query))
        cjk_ratio = len(_CJK_CHARACTER.findall(compact_query)) / character_count
        quoted_phrase = bool(_QUOTED_PHRASE.search(query_text))
        acronym = bool(_ACRONYM.search(query_text))

        matched_terms = {
            normalize_lexical_text(term)
            for candidate in lexical_candidates
            for term in candidate.matched_terms
            if normalize_lexical_text(term)
        }
        alias_hits = _metadata_phrase_hits(
            normalized_query,
            (*semantic_candidates, *lexical_candidates),
            "aliases",
        )
        technical_hits = _metadata_phrase_hits(
            normalized_query,
            (*semantic_candidates, *lexical_candidates),
            "technical_terms",
        )
        exact_terms = matched_terms | alias_hits | technical_hits
        exact_density = sum(len(term.replace(" ", "")) for term in exact_terms)
        exact_density = min(1.0, exact_density / character_count)

        lexical_reasons: list[str] = []
        if alias_hits:
            lexical_reasons.append("exact_alias_match")
        if technical_hits:
            lexical_reasons.append("exact_technical_term_match")
        if acronym:
            lexical_reasons.append("acronym_signal")
        if quoted_phrase:
            lexical_reasons.append("quoted_phrase_signal")
        if len(matched_terms) >= 2:
            lexical_reasons.append("multiple_exact_terms")

        long_cjk_paraphrase = (
            character_count >= 24
            and cjk_ratio >= 0.45
            and not alias_hits
            and not technical_hits
            and len(matched_terms) <= 1
            and exact_density < 0.18
        )
        if long_cjk_paraphrase:
            return self._decision(
                "semantic_dominant",
                base_semantic_weight,
                base_lexical_weight,
                ("long_cjk_paraphrase", "weak_exact_term_support"),
            )

        strong_lexical = bool(
            alias_hits
            or technical_hits
            or (acronym and matched_terms)
            or (quoted_phrase and exact_terms)
            or len(matched_terms) >= 2
        )
        if strong_lexical:
            return self._decision(
                "lexical_dominant",
                base_semantic_weight,
                base_lexical_weight,
                tuple(lexical_reasons) or ("strong_exact_term_support",),
            )

        reasons = ["mixed_or_ambiguous_signals"]
        if matched_terms:
            reasons.append("limited_exact_term_support")
        if character_count >= 24 and cjk_ratio >= 0.45:
            reasons.append("natural_language_signal")
        return self._decision(
            "balanced",
            base_semantic_weight,
            base_lexical_weight,
            tuple(reasons),
        )

    @staticmethod
    def _decision(
        signal: QuerySignal,
        base_semantic_weight: float,
        base_lexical_weight: float,
        reason_codes: tuple[str, ...],
    ) -> QuerySignalDecision:
        multipliers = {
            "lexical_dominant": (0.8, 1.4),
            "semantic_dominant": (1.3, 0.7),
            "balanced": (1.0, 1.0),
        }
        semantic_multiplier, lexical_multiplier = multipliers[signal]
        return QuerySignalDecision(
            query_signal=signal,
            semantic_weight=round(base_semantic_weight * semantic_multiplier, 6),
            lexical_weight=round(base_lexical_weight * lexical_multiplier, 6),
            reason_codes=reason_codes,
        )


def _metadata_phrase_hits(
    normalized_query: str,
    candidates: Sequence[RetrievalCandidate],
    key: str,
) -> set[str]:
    hits: set[str] = set()
    for candidate in candidates:
        raw_values = candidate.chunk.metadata.get(key, [])
        if isinstance(raw_values, str):
            values = (raw_values,)
        elif isinstance(raw_values, list):
            values = tuple(value for value in raw_values if isinstance(value, str))
        else:
            values = ()
        for value in values:
            normalized = normalize_lexical_text(value)
            if len(normalized.replace(" ", "")) >= 2 and normalized in normalized_query:
                hits.add(normalized)
    return hits
