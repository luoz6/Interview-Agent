"""Deterministic, side-effect-free interview question quality signals.

This module deliberately reports discrete findings instead of combining unlike
quality concerns into a score.  It has no provider, persistence, or prompt
dependencies, so callers can decide separately where and how findings should
be enforced.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any


HARD_QUESTION_QUALITY_CODES = (
    "near_duplicate_question",
    "overloaded_multi_ask",
    "answer_leakage",
)

SOFT_QUESTION_QUALITY_CODES = (
    "generic_focus",
    "candidate_specificity_missing",
    "advanced_depth_missing",
    "followup_affordance_missing",
)


_SAFE_QUESTION_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,99}\Z")
_LATIN_TOKEN = re.compile(r"[a-z0-9]+")

_GENERIC_FOCUS_VALUES = frozenset(
    {
        "",
        "general",
        "technical",
        "fundamentals",
        "system design",
        "project",
        "behavioral",
        "通用",
        "技术",
        "基础",
        "系统设计",
        "项目",
        "行为",
    }
)

_ASSESSMENT_BOUNDARY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "consistency",
        (
            r"\bconsisten(?:cy|t)\b",
            r"\binvalidat(?:e|ed|es|ing|ion)\b",
            "一致性",
            "缓存失效",
        ),
    ),
    (
        "resilience",
        (
            r"\bresilien(?:ce|t)\b",
            r"\bfail(?:ure|over|ed|ing)?\b",
            r"\brecover(?:y|ed|ing)?\b",
            r"\bdisaster\b",
            "故障",
            "恢复",
            "容灾",
            "降级",
        ),
    ),
    (
        "tradeoff",
        (
            r"\btrade\s*offs?\b",
            r"\bbalanc(?:e|ed|es|ing)\b",
            "权衡",
            "取舍",
        ),
    ),
    (
        "capacity",
        (
            r"\bscal(?:e|ed|es|ing|ability|able)\b",
            r"\bcapacity\b",
            "容量",
            "扩展",
            "伸缩",
        ),
    ),
    (
        "performance",
        (
            r"\bperformance\b",
            r"\blatenc(?:y|ies)\b",
            r"\bthroughput\b",
            "性能",
            "延迟",
            "吞吐",
        ),
    ),
    (
        "security",
        (
            r"\bsecur(?:ity|e)\b",
            r"\bauth(?:entication|orization)?\b",
            "安全",
            "鉴权",
            "认证",
        ),
    ),
    (
        "observability",
        (
            r"\bobservability\b",
            r"\bmonitor(?:ing|ed)?\b",
            r"\btrac(?:e|ing)\b",
            r"\bmetrics?\b",
            "可观测",
            "监控",
            "链路追踪",
        ),
    ),
    (
        "concurrency",
        (
            r"\bconcurren(?:cy|t)\b",
            r"\brace condition\b",
            r"\block(?:ing|ed|s)?\b",
            "并发",
            "竞态",
            "锁",
        ),
    ),
    (
        "testing",
        (
            r"\btest(?:ing|ed|s)?\b",
            r"\bvalidat(?:e|ed|es|ing|ion)\b",
            "测试",
            "验证",
        ),
    ),
    (
        "data_integrity",
        (
            r"\btransactions?\b",
            r"\bindexes?\b",
            r"\bdata integrity\b",
            "事务",
            "索引",
            "数据完整性",
        ),
    ),
)

_CANDIDATE_ANCHOR_PATTERNS = (
    r"\byou(?:r|rs)?\b",
    r"\bresume\b",
    r"\bproject\b",
    r"\bexperience\b",
    r"\bbuilt\b",
    r"\bimplemented\b",
    r"\bowned\b",
    r"\bpersonally\b",
    r"\bled\b",
    "你",
    "您",
    "简历",
    "项目",
    "经历",
    "负责",
    "主导",
    "亲自",
    "实践",
)

_OPEN_ENDED_PATTERNS = (
    r"\bhow\b",
    r"\bwhy\b",
    r"\bdescribe\b",
    r"\bexplain\b",
    r"\bcompare\b",
    r"\bdesign\b",
    r"\bwalk\s+(?:me\s+)?through\b",
    r"\bdiscuss\b",
    r"\banaly[sz]e\b",
    r"\bevaluate\b",
    r"\bwhat\s+(?:would|did|happened|happens)\b",
    "如何",
    "为什么",
    "怎么",
    "请说明",
    "请描述",
    "解释",
    "比较",
    "设计",
    "复盘",
    "权衡",
    "故障",
)

_MULTI_ASK_CUE_PATTERNS = (
    r"\bexplain\b",
    r"\bcompare\b",
    r"\bdescribe\b",
    r"\banaly[sz]e\b",
    r"\bevaluate\b",
    r"\bdesign\b",
    r"\bjustify\b",
    r"\boutline\b",
    r"\bidentify\b",
    r"\bdiscuss\b",
    r"\bassess\b",
    r"\bpropose\b",
    r"\bdetail\b",
    r"\bwalk\s+(?:me\s+)?through\b",
    "说明",
    "分析",
    "比较",
    "描述",
    "设计",
    "解释",
    "评估",
    "论证",
    "列出",
)

_MULTI_ASK_CONNECTOR = re.compile(
    r"[,;]|\b(?:and|also|then|while|plus)\b|[、，；]|同时|并且|以及|然后|并"
)
_NUMBERED_SUBITEM = re.compile(
    r"(?m)(?:^|\s)(?:\(?\d{1,2}\)|\d{1,2}[.)、]|\([a-z]\)|[a-z][.)])\s*"
)

_ANSWER_LEAKAGE_PATTERNS = (
    r"\b(?:the\s+)?(?:correct|ideal|reference|expected)\s+answer\s+"
    r"(?:is|equals|should|must)\b",
    r"\b(?:your|the)\s+answer\s+must\s+include\b",
    r"\byou\s+should\s+(?:answer|say)\b",
    r"(?:标准|参考|正确|理想|预期)答案(?:\s*(?:是|为)\s*|\s+)",
    r"答案\s*必须\s*(?:包含|包括)",
    r"你(?:应该|应当)\s*(?:回答|说)",
)

_EVIDENCE_SUMMARIES = {
    "near_duplicate_question": (
        "Questions have substantially overlapping wording and assessment intent."
    ),
    "overloaded_multi_ask": (
        "Question contains multiple independently assessable asks."
    ),
    "answer_leakage": (
        "Question explicitly supplies or prescribes content for the answer."
    ),
    "generic_focus": "Question focus is missing or only names a generic category.",
    "candidate_specificity_missing": (
        "Question lacks an explicit candidate, project, or experience anchor."
    ),
    "advanced_depth_missing": (
        "Advanced question lacks a concrete depth or trade-off boundary."
    ),
    "followup_affordance_missing": (
        "Question expecting follow-ups lacks an open-ended exploration cue."
    ),
}


@dataclass(frozen=True, slots=True)
class QuestionQualityInput:
    """Minimal, model-independent input required by the quality checks."""

    question_ref: str
    prompt: str
    focus: str = ""
    question_type: str = ""
    difficulty: str | None = None
    expected_followups: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.question_ref, str) or not _SAFE_QUESTION_REF.fullmatch(
            self.question_ref
        ):
            raise ValueError(
                "question_ref must be a non-empty safe identifier of at most "
                "100 characters"
            )

    @classmethod
    def from_question(cls, question: object) -> QuestionQualityInput:
        """Adapt existing legacy or V2 question-shaped data without coupling."""

        question_ref = _first_present(question, "question_id", "id")
        prompt = _first_present(question, "question_text", "prompt")
        question_type = _first_present(
            question, "question_type", "kind", default=""
        )
        return cls(
            question_ref=str(question_ref) if question_ref is not None else "",
            prompt=str(prompt) if prompt is not None else "",
            focus=str(_read_field(question, "focus", "") or ""),
            question_type=str(question_type or ""),
            difficulty=_optional_string(_read_field(question, "difficulty", None)),
            expected_followups=_read_field(question, "expected_followups", None),
        )


@dataclass(frozen=True, slots=True)
class QuestionTextComparison:
    near_duplicate: bool
    different_assessment_boundaries: bool
    sequence_ratio: float
    token_overlap: float
    cjk_bigram_overlap: float
    cjk_trigram_overlap: float


@dataclass(frozen=True, slots=True)
class QuestionQualitySignal:
    code: str
    evidence_summary: str
    question_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterviewQuestionQualityReport:
    hard_violations: tuple[QuestionQualitySignal, ...]
    soft_warnings: tuple[QuestionQualitySignal, ...]


def normalize_question_text(value: str) -> str:
    """Return a comparison form stable across Unicode width and punctuation."""

    if not isinstance(value, str):
        raise TypeError("question text must be a string")

    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if character.isspace() or category.startswith(("P", "Z")):
            characters.append(" ")
        elif category.startswith("C"):
            characters.append(" ")
        else:
            characters.append(character)
    return " ".join("".join(characters).split())


def compare_question_texts(left: str, right: str) -> QuestionTextComparison:
    """Compare two prompts using deterministic Latin and CJK overlap signals."""

    normalized_left = normalize_question_text(left)
    normalized_right = normalize_question_text(right)
    compact_left = normalized_left.replace(" ", "")
    compact_right = normalized_right.replace(" ", "")

    sequence_ratio = _sequence_ratio(compact_left, compact_right)
    left_tokens = tuple(_LATIN_TOKEN.findall(normalized_left))
    right_tokens = tuple(_LATIN_TOKEN.findall(normalized_right))
    token_overlap = _set_overlap(left_tokens, right_tokens)

    left_cjk = "".join(character for character in compact_left if _is_cjk(character))
    right_cjk = "".join(
        character for character in compact_right if _is_cjk(character)
    )
    cjk_bigram_overlap = _ngram_overlap(left_cjk, right_cjk, size=2)
    cjk_trigram_overlap = _ngram_overlap(left_cjk, right_cjk, size=3)

    left_boundaries = _assessment_boundaries(normalized_left)
    right_boundaries = _assessment_boundaries(normalized_right)
    different_boundaries = bool(
        left_boundaries
        and right_boundaries
        and left_boundaries.isdisjoint(right_boundaries)
    )

    exact_match = bool(compact_left) and compact_left == compact_right
    near_duplicate = exact_match
    if not exact_match and not different_boundaries:
        near_duplicate = _is_near_duplicate(
            compact_left=compact_left,
            compact_right=compact_right,
            left_tokens=left_tokens,
            right_tokens=right_tokens,
            left_cjk=left_cjk,
            right_cjk=right_cjk,
            sequence_ratio=sequence_ratio,
            token_overlap=token_overlap,
            cjk_bigram_overlap=cjk_bigram_overlap,
            cjk_trigram_overlap=cjk_trigram_overlap,
        )

    return QuestionTextComparison(
        near_duplicate=near_duplicate,
        different_assessment_boundaries=different_boundaries,
        sequence_ratio=sequence_ratio,
        token_overlap=token_overlap,
        cjk_bigram_overlap=cjk_bigram_overlap,
        cjk_trigram_overlap=cjk_trigram_overlap,
    )


def assess_interview_question_quality(
    questions: Sequence[QuestionQualityInput | object],
) -> InterviewQuestionQualityReport:
    """Return stable hard violations and soft warnings for ``questions``."""

    prepared = tuple(
        item
        if isinstance(item, QuestionQualityInput)
        else QuestionQualityInput.from_question(item)
        for item in questions
    )

    hard_violations: list[QuestionQualitySignal] = []
    soft_warnings: list[QuestionQualitySignal] = []

    for left_index, left in enumerate(prepared):
        for right in prepared[left_index + 1 :]:
            if compare_question_texts(left.prompt, right.prompt).near_duplicate:
                hard_violations.append(
                    _signal(
                        "near_duplicate_question",
                        left.question_ref,
                        right.question_ref,
                    )
                )

    for item in prepared:
        if _is_overloaded_multi_ask(item.prompt):
            hard_violations.append(
                _signal("overloaded_multi_ask", item.question_ref)
            )

    for item in prepared:
        if _contains_pattern(item.prompt, _ANSWER_LEAKAGE_PATTERNS):
            hard_violations.append(_signal("answer_leakage", item.question_ref))

    for item in prepared:
        if normalize_question_text(item.focus) in _GENERIC_FOCUS_VALUES:
            soft_warnings.append(_signal("generic_focus", item.question_ref))

    for item in prepared:
        if not _contains_pattern(item.prompt, _CANDIDATE_ANCHOR_PATTERNS):
            soft_warnings.append(
                _signal("candidate_specificity_missing", item.question_ref)
            )

    for item in prepared:
        if _is_advanced(item.difficulty) and not _has_advanced_depth(item.prompt):
            soft_warnings.append(
                _signal("advanced_depth_missing", item.question_ref)
            )

    for item in prepared:
        if _expects_followups(item.expected_followups) and not _contains_pattern(
            item.prompt, _OPEN_ENDED_PATTERNS
        ):
            soft_warnings.append(
                _signal("followup_affordance_missing", item.question_ref)
            )

    return InterviewQuestionQualityReport(
        hard_violations=tuple(hard_violations),
        soft_warnings=tuple(soft_warnings),
    )


def _first_present(
    value: object, *names: str, default: Any = None
) -> Any:
    for name in names:
        candidate = _read_field(value, name, None)
        if candidate is not None:
            return candidate
    return default


def _read_field(value: object, name: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _sequence_ratio(left: str, right: str) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _set_overlap(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return 2.0 * len(left_set & right_set) / (len(left_set) + len(right_set))


def _ngram_overlap(left: str, right: str, *, size: int) -> float:
    if len(left) < size or len(right) < size:
        return 0.0
    left_ngrams = {left[index : index + size] for index in range(len(left) - size + 1)}
    right_ngrams = {
        right[index : index + size] for index in range(len(right) - size + 1)
    }
    return _set_overlap(tuple(left_ngrams), tuple(right_ngrams))


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _assessment_boundaries(normalized_text: str) -> frozenset[str]:
    return frozenset(
        boundary
        for boundary, patterns in _ASSESSMENT_BOUNDARY_PATTERNS
        if _contains_normalized_pattern(normalized_text, patterns)
    )


def _is_near_duplicate(
    *,
    compact_left: str,
    compact_right: str,
    left_tokens: Sequence[str],
    right_tokens: Sequence[str],
    left_cjk: str,
    right_cjk: str,
    sequence_ratio: float,
    token_overlap: float,
    cjk_bigram_overlap: float,
    cjk_trigram_overlap: float,
) -> bool:
    if not compact_left or not compact_right:
        return False

    latin_substantive = min(len(left_tokens), len(right_tokens)) >= 5
    cjk_substantive = min(len(left_cjk), len(right_cjk)) >= 8
    if not latin_substantive and not cjk_substantive:
        return False

    shared_latin_tokens = len(set(left_tokens) & set(right_tokens))
    if sequence_ratio >= 0.92:
        return True
    if (
        latin_substantive
        and shared_latin_tokens >= 4
        and token_overlap >= 0.86
        and sequence_ratio >= 0.52
    ):
        return True
    if (
        cjk_substantive
        and cjk_bigram_overlap >= 0.78
        and (cjk_trigram_overlap >= 0.62 or sequence_ratio >= 0.80)
    ):
        return True
    return bool(
        sequence_ratio >= 0.86
        and (
            (shared_latin_tokens >= 4 and token_overlap >= 0.75)
            or (cjk_substantive and cjk_bigram_overlap >= 0.72)
        )
    )


def _is_overloaded_multi_ask(prompt: str) -> bool:
    normalized_width = unicodedata.normalize("NFKC", prompt).casefold()
    if normalized_width.count("?") >= 2:
        return True
    if len(_NUMBERED_SUBITEM.findall(normalized_width)) >= 2:
        return True

    cue_count = sum(
        len(re.findall(pattern, normalized_width))
        for pattern in _MULTI_ASK_CUE_PATTERNS
    )
    return cue_count >= 3 and bool(_MULTI_ASK_CONNECTOR.search(normalized_width))


def _contains_pattern(value: str, patterns: Sequence[str]) -> bool:
    normalized = normalize_question_text(value)
    return _contains_normalized_pattern(normalized, patterns)


def _contains_normalized_pattern(
    normalized_value: str, patterns: Sequence[str]
) -> bool:
    return any(re.search(pattern, normalized_value) for pattern in patterns)


def _is_advanced(difficulty: str | None) -> bool:
    return bool(difficulty and normalize_question_text(difficulty) == "advanced")


def _has_advanced_depth(prompt: str) -> bool:
    normalized = normalize_question_text(prompt)
    return bool(
        _assessment_boundaries(normalized)
        or re.search(r"\bboundar(?:y|ies)\b", normalized)
        or "边界" in normalized
    )


def _expects_followups(expected_followups: int | None) -> bool:
    return bool(
        isinstance(expected_followups, int)
        and not isinstance(expected_followups, bool)
        and expected_followups > 0
    )


def _signal(code: str, *question_refs: str) -> QuestionQualitySignal:
    return QuestionQualitySignal(
        code=code,
        evidence_summary=_EVIDENCE_SUMMARIES[code],
        question_refs=tuple(question_refs),
    )
