from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.interview_question_quality import (
    HARD_QUESTION_QUALITY_CODES,
    SOFT_QUESTION_QUALITY_CODES,
    QuestionQualityInput,
    assess_interview_question_quality,
    compare_question_texts,
    normalize_question_text,
)
from app.services.prep import InterviewQuestion


def question(
    question_ref: str,
    prompt: str,
    *,
    focus: str = "cache consistency",
    question_type: str = "technical",
    difficulty: str | None = "intermediate",
    expected_followups: int | None = 1,
) -> QuestionQualityInput:
    return QuestionQualityInput(
        question_ref=question_ref,
        prompt=prompt,
        focus=focus,
        question_type=question_type,
        difficulty=difficulty,
        expected_followups=expected_followups,
    )


def test_normalization_is_unicode_punctuation_and_whitespace_stable():
    assert normalize_question_text(
        "  ＨＯＷ\t did YOU handle Redis—failover？  "
    ) == "how did you handle redis failover"
    assert normalize_question_text("缓存，一致性！？") == "缓存 一致性"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            "How did you handle cache invalidation in your Redis project?",
            "In your Redis project, how did you handle cache invalidation?",
        ),
        (
            "请说明你在项目中如何处理缓存一致性问题？",
            "请说明：你在项目中，如何处理缓存一致性问题。",
        ),
    ],
)
def test_bilingual_near_duplicate_uses_deterministic_overlap(left, right):
    comparison = compare_question_texts(left, right)

    assert comparison.near_duplicate is True
    assert comparison.different_assessment_boundaries is False
    assert max(
        comparison.sequence_ratio,
        comparison.token_overlap,
        comparison.cjk_bigram_overlap,
    ) >= 0.78


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Explain Redis.", "Explain Kafka."),
        ("如何保证 Redis 缓存一致性？", "如何处理 Redis 节点故障恢复？"),
        (
            "How do you keep Redis cache consistent during writes?",
            "How do you recover Redis after a node failure?",
        ),
    ],
)
def test_short_or_same_topic_different_boundary_is_not_near_duplicate(left, right):
    comparison = compare_question_texts(left, right)

    assert comparison.near_duplicate is False


@pytest.mark.parametrize(
    "prompt",
    [
        (
            "Explain the architecture, compare three alternatives, and describe "
            "the rollout and monitoring plan."
        ),
        "请说明整体架构、分析故障模式，同时比较方案并描述上线监控。",
        "How would you shard it? Why? What happens during failover?",
    ],
)
def test_overloaded_multi_ask_is_a_hard_violation(prompt):
    report = assess_interview_question_quality(
        [question("q1", prompt, focus="distributed storage")]
    )

    assert [item.code for item in report.hard_violations] == [
        "overloaded_multi_ask"
    ]
    assert report.hard_violations[0].question_refs == ("q1",)


@pytest.mark.parametrize(
    "prompt",
    [
        "How would you prevent cache stampede?",
        "Why Redis?",
        "如何避免缓存击穿？",
        "请比较一致性与延迟的权衡。",
    ],
)
def test_one_short_coherent_ask_is_not_overloaded(prompt):
    report = assess_interview_question_quality(
        [question("q1", prompt, focus="cache resilience")]
    )

    assert "overloaded_multi_ask" not in {
        item.code for item in report.hard_violations
    }


@pytest.mark.parametrize(
    "prompt",
    [
        "The correct answer is fencing tokens; can you repeat it?",
        "Your answer must include exactly-once delivery.",
        "标准答案是使用唯一请求键，请解释这个答案。",
        "参考答案：先写事务日志。请复述。",
    ],
)
def test_explicit_answer_leakage_is_a_hard_violation(prompt):
    report = assess_interview_question_quality(
        [question("q1", prompt, focus="idempotency")]
    )

    assert "answer_leakage" in {item.code for item in report.hard_violations}


def test_soft_signals_are_discrete_and_do_not_create_an_arbitrary_score():
    report = assess_interview_question_quality(
        [
            question(
                "q1",
                "Explain queues.",
                focus="technical",
                difficulty="advanced",
                expected_followups=1,
            ),
            question(
                "q2",
                "What is Redis?",
                focus="Redis fundamentals",
                difficulty="intermediate",
                expected_followups=1,
            ),
        ]
    )

    assert {item.code for item in report.soft_warnings} == {
        "generic_focus",
        "candidate_specificity_missing",
        "advanced_depth_missing",
        "followup_affordance_missing",
    }
    assert not hasattr(report, "score")
    assert not hasattr(report, "total")


def test_candidate_specific_advanced_question_with_affordance_is_clean():
    report = assess_interview_question_quality(
        [
            question(
                "q1",
                (
                    "In your payment project, how did you balance consistency and "
                    "latency during a regional failure?"
                ),
                focus="regional consistency tradeoff",
                difficulty="advanced",
                expected_followups=2,
            ),
            question(
                "q2",
                "请复盘你负责的消息系统在故障恢复时如何权衡可用性与一致性？",
                focus="消息恢复边界",
                difficulty="advanced",
                expected_followups=2,
            ),
        ]
    )

    assert report.hard_violations == ()
    assert report.soft_warnings == ()


def test_signal_order_codes_and_evidence_are_stable_and_privacy_safe():
    questions = [
        question(
            "q1",
            "How did you handle cache invalidation in your Redis project?",
        ),
        question(
            "q2",
            "In your Redis project, how did you handle cache invalidation?",
        ),
        question(
            "q3",
            (
                "The correct answer is Redis. Explain storage, compare databases, "
                "and describe rollout monitoring. SECRET_RESUME_SENTENCE"
            ),
            focus="general",
            difficulty="advanced",
        ),
    ]

    first = assess_interview_question_quality(questions)
    second = assess_interview_question_quality(questions)

    assert first == second
    assert [item.code for item in first.hard_violations] == [
        "near_duplicate_question",
        "overloaded_multi_ask",
        "answer_leakage",
    ]
    assert first.hard_violations[0].question_refs == ("q1", "q2")
    assert tuple(HARD_QUESTION_QUALITY_CODES) == (
        "near_duplicate_question",
        "overloaded_multi_ask",
        "answer_leakage",
    )
    assert tuple(SOFT_QUESTION_QUALITY_CODES) == (
        "generic_focus",
        "candidate_specificity_missing",
        "advanced_depth_missing",
        "followup_affordance_missing",
    )
    assert "SECRET_RESUME_SENTENCE" not in repr(first)
    assert all(
        signal.evidence_summary
        and "SECRET_RESUME_SENTENCE" not in signal.evidence_summary
        for signal in (*first.hard_violations, *first.soft_warnings)
    )


def test_input_adapter_supports_existing_legacy_and_v2_question_fields():
    legacy = InterviewQuestion(
        id="q1",
        kind="technical",
        prompt="How did you tune your Redis cache?",
        focus="cache performance",
    )
    v2 = SimpleNamespace(
        question_id="12345678-1234-5678-1234-567812345678",
        question_type="system-design",
        question_text="How would you design your cache failover boundary?",
        focus="cache failover",
        difficulty="advanced",
        expected_followups=2,
    )

    legacy_input = QuestionQualityInput.from_question(legacy)
    v2_input = QuestionQualityInput.from_question(v2)

    assert legacy_input.question_ref == "q1"
    assert legacy_input.prompt == legacy.prompt
    assert legacy_input.difficulty is None
    assert v2_input.question_ref == v2.question_id
    assert v2_input.prompt == v2.question_text
    assert v2_input.question_type == "system-design"
    assert v2_input.expected_followups == 2


def test_assessment_is_pure_deterministic_and_performs_no_file_io(monkeypatch):
    inputs = [
        question(
            "q1",
            "In your project, how did you recover a failed write?",
            focus="write recovery",
        )
    ]
    before = deepcopy(inputs)

    monkeypatch.setattr(
        "builtins.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("quality helper must not perform file I/O")
        ),
    )
    first = assess_interview_question_quality(inputs)
    second = assess_interview_question_quality(tuple(inputs))

    assert first == second
    assert inputs == before


@pytest.mark.parametrize("question_ref", ["", "contains raw text", "x" * 101])
def test_question_refs_are_bounded_safe_identifiers(question_ref):
    with pytest.raises(ValueError, match="question_ref"):
        question(question_ref, "How did you test it?")
