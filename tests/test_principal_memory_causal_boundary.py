from pathlib import Path
import re

import pytest

from app.services.llm import OpenAIInterviewLLM
from app.agents.examiner import ExaminerAgent
from app.services.principal_memory_sink_policy import (
    ASSISTANCE_CONTEXT_KIND,
    ASSISTANCE_LABEL,
    ASSISTANCE_WARNING,
    CAUSAL_BOUNDARY_VIOLATION,
    FOLLOWUP_GENERATION_SINK,
    PrincipalMemoryCausalBoundaryViolation,
    assert_principal_memory_sink,
)
from tests.test_llm_service import FakeFollowupChatModel


BASE_CONTEXT = [
    {"role": "interviewer", "content": "Explain a database tradeoff."},
    {"role": "candidate", "content": "I chose stronger consistency."},
]


def assistance_message(*, value="zh_hans"):
    content = "\n".join(
        [
            f"[{ASSISTANCE_LABEL}]",
            "Use: local follow-up assistance only.",
            ASSISTANCE_WARNING,
            (
                f"- category=interview_language; value={value}; "
                "authority=user_declared; confirmation=user_confirmed; "
                "source_status=available"
            ),
            f"[/{ASSISTANCE_LABEL}]",
        ]
    )
    return {
        "role": "system",
        "content": content,
        "context_kind": ASSISTANCE_CONTEXT_KIND,
    }


def test_only_followup_sink_accepts_one_canonical_bounded_block():
    payload = [BASE_CONTEXT[0], assistance_message(), BASE_CONTEXT[1]]

    assert_principal_memory_sink(
        operation=FOLLOWUP_GENERATION_SINK,
        payload=payload,
    )

    for operation in (
        "plan_generation",
        "evaluation",
        "score",
        "evidence",
        "report_generation",
        "pdf_generation",
        "prep",
        "review",
        "knowledge",
    ):
        with pytest.raises(
            PrincipalMemoryCausalBoundaryViolation,
            match=f"^{CAUSAL_BOUNDARY_VIOLATION}$",
        ):
            assert_principal_memory_sink(operation=operation, payload=payload)


@pytest.mark.parametrize(
    "payload",
    [
        [{"role": "system", "content": "x", "safe_ref": "private"}],
        [{"role": "system", "content": "x", "fact_id": "private"}],
        [{"role": "system", "content": "x", "normalized_fact": "private"}],
        [assistance_message(), assistance_message(value="en")],
        [{**assistance_message(), "safe_ref": "private"}],
        [{**assistance_message(), "role": "candidate"}],
        [{**assistance_message(), "extra": "not allowlisted"}],
        [
            {
                "role": "system",
                "content": assistance_message()["content"],
            }
        ],
    ],
)
def test_followup_sink_fails_closed_without_echoing_private_payload(payload):
    with pytest.raises(PrincipalMemoryCausalBoundaryViolation) as raised:
        assert_principal_memory_sink(
            operation=FOLLOWUP_GENERATION_SINK,
            payload=payload,
        )
    assert str(raised.value) == CAUSAL_BOUNDARY_VIOLATION
    assert "private" not in str(raised.value)


def test_same_followup_input_changes_only_the_followup_provider_request():
    base_model = FakeFollowupChatModel()
    base_llm = OpenAIInterviewLLM(chat_model=base_model)
    base_llm.generate_followup(BASE_CONTEXT)

    memory_model = FakeFollowupChatModel()
    memory_llm = OpenAIInterviewLLM(chat_model=memory_model)
    memory_llm.generate_followup(
        [BASE_CONTEXT[0], assistance_message(), BASE_CONTEXT[1]]
    )

    assert base_model.last_prompt != memory_model.last_prompt
    assert ASSISTANCE_LABEL not in base_model.last_prompt
    assert ASSISTANCE_LABEL in memory_model.last_prompt
    assert "fact_id" not in memory_model.last_prompt
    assert "safe_ref" not in memory_model.last_prompt


def test_plan_and_report_adapters_reject_memory_before_provider_call():
    class ProviderSpy:
        calls = 0

        def invoke(self, prompt):
            self.calls += 1
            raise AssertionError("provider must not be called")

        def with_structured_output(self, schema, method=None):
            del schema, method
            return self

    provider = ProviderSpy()
    llm = OpenAIInterviewLLM(chat_model=provider)
    block = assistance_message()

    with pytest.raises(PrincipalMemoryCausalBoundaryViolation):
        llm.generate_plan("synthetic job", "synthetic resume", [block])
    with pytest.raises(PrincipalMemoryCausalBoundaryViolation):
        llm.generate_report(
            plan=object(),
            evaluation_items=[block],
            session_id="synthetic-session",
        )
    assert provider.calls == 0


def test_examiner_rejects_invalid_memory_before_custom_llm_call():
    class LLMSpy:
        calls = 0

        def generate_followup(self, context):
            del context
            self.calls += 1
            return "not reached"

        def stream_followup(self, context):
            del context
            self.calls += 1
            yield "not reached"

    llm = LLMSpy()
    examiner = ExaminerAgent(llm=llm)
    payload = [{"role": "system", "content": "x", "safe_ref": "private"}]

    with pytest.raises(PrincipalMemoryCausalBoundaryViolation):
        examiner.generate_followup(context=payload, focus="synthetic")
    with pytest.raises(PrincipalMemoryCausalBoundaryViolation):
        list(examiner.stream_followup(context=payload, focus="synthetic"))
    assert llm.calls == 0


def test_candidate_text_can_mention_internal_terms_without_false_positive():
    assert_principal_memory_sink(
        operation="report_generation",
        payload={
            "messages": [
                {
                    "role": "candidate",
                    "content": (
                        "I saw principal_memory_assistance_v1 and safe_ref in docs."
                    ),
                }
            ]
        },
    )


def test_product_docs_pin_direct_dependency_and_indirect_causal_boundary():
    paths = (
        Path("README.md"),
        Path("docs/local-v1-runbook.md"),
        Path("docs/local-principal-memory-operations.md"),
        Path("docs/local-v1-long-term-memory-acceptance.md"),
    )
    required = (
        "Local V1 is a trusted-local, default-off experiment.",
        "Principal Memory may influence follow-up generation only.",
        "Score and report modules have no direct Principal Memory dependency.",
        "No claim is made that changed interview trajectories are causally equivalent.",
    )
    forbidden = (
        "fairness validated",
        "score unaffected in all cases",
        "report causally identical",
        "production ready",
        "candidate safe",
        "Hosted C1-A equivalent",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for statement in required:
            assert statement in normalized, (path.as_posix(), statement)
        for statement in forbidden:
            assert re.search(
                rf"(?<![A-Za-z]){re.escape(statement)}(?![A-Za-z])",
                text,
                re.IGNORECASE,
            ) is None, (
                path.as_posix(),
                statement,
            )


def test_memory_center_copy_discloses_local_followup_only_boundary():
    text = Path("frontend/public/memory-center.html").read_text(encoding="utf-8")
    assert "仅限受信任的本机实验，且默认关闭" in text
    assert "只可能影响后续追问" in text
    assert "评分和报告模块不会直接读取长期记忆" in text
    assert "不代表不同面试路径具有因果等价性" in text
    assert "真实候选人生产使用仍被禁止" in text


def test_hosted_c1a_boundary_remains_zero_historical_prompt_injection():
    plan = Path(
        "docs/superpowers/plans/"
        "2026-08-04-long-term-memory-local-v1-hardening-and-hosted-v2-roadmap-v0.4-detailed.md"
    ).read_text(encoding="utf-8")
    assert "语言预填需候选人确认" in plan
    assert "accessibility UI/interaction" in plan
    assert "历史 fact 不进入 Provider Prompt" in plan
