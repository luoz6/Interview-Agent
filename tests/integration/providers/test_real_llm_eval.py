"""Opt-in real-model provider integration coverage."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from time import monotonic
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.examiner import ExaminerAgent
from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.domain.interview.question_intent import QuestionIntentV1
from app.graphs.durable_interview_graph import (
    DurableInterviewGraphDependencies,
    build_durable_interview_graph_v3,
)
from app.graphs.durable_interview_state_v3 import make_durable_initial_state_v3
from app.services.context_budget import MAIN_QUESTION_CONTEXT_POLICY
from app.services.evaluator import build_fallback_report
from app.services.interview_generation_store import GenerationAlreadyCompleted
from app.services.interview_plan_revision import (
    InterviewPlanQuestionV2,
    InterviewPlanV2,
    InterviewPlanV3,
    default_plan_configuration,
    legacy_interview_knowledge_scope_snapshot,
    plan_payload_sha256,
    v2_plan_to_legacy,
)
from app.services.llm import OpenAIInterviewLLM
from app.services.main_question_generation import (
    MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS,
    MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS,
    MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS,
    load_main_question_generation_settings,
)
from app.services.report_quality import collect_report_quality_issues
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session import InterviewSessionStore
from app.services.session_plan_binding import (
    SessionPlanBinding,
    legacy_session_plan_binding,
)
from tests.browser_v3_runtime import BrowserV3GenerationStore
from tests.eval_support import GoldenVectorStore, load_all_cases, make_state
from tests.real_llm_jit_support import (
    JIT_MAIN_QUESTION_SCOPE,
    LEGACY_REVIEWER_SMOKE_SCOPE,
    LEDGER_PATH_ENV,
    RECEIPT_PATH_ENV,
    RECEIPT_SHA_ENV,
    RealJitAuthorizationError,
    RealJitAuthorizationReceipt,
    RealJitRequestLedger,
    authorized_provider_attempt_hook,
    canonical_receipt_sha256,
)


REAL_JIT_FLAG = "RUN_REAL_LLM_JIT_EVAL"
REAL_SMOKE_FLAG = "RUN_REAL_LLM_EVAL"
TIMEOUT_TEST_MAX_SECONDS = 10
TIMEOUT_DIAGNOSTIC_MAX_MS = TIMEOUT_TEST_MAX_SECONDS * 1000


def _case_by_id(case_id: str) -> dict:
    return next(case for case in load_all_cases() if case["id"] == case_id)


def _require_authorized_real_provider(
    *,
    flag: str,
    workload: str,
    authorization_scope: str,
):
    """Treat the opt-in as workload selection, never Provider authorization."""

    if os.getenv(flag) != "1":
        pytest.skip(f"Set {flag}=1 to enable the real {workload}")
    try:
        return authorized_provider_attempt_hook(scope=authorization_scope)
    except RealJitAuthorizationError as exc:
        pytest.fail(str(exc), pytrace=False)


def _require_real_jit_config():
    return _require_authorized_real_provider(
        flag=REAL_JIT_FLAG,
        workload="JIT diagnostic",
        authorization_scope=JIT_MAIN_QUESTION_SCOPE,
    )


def _require_real_smoke_config():
    return _require_authorized_real_provider(
        flag=REAL_SMOKE_FLAG,
        workload="legacy reviewer smoke eval",
        authorization_scope=LEGACY_REVIEWER_SMOKE_SCOPE,
    )


def _jit_state():
    plan = InterviewPlanV3(
        title="Synthetic JIT Provider diagnostic",
        configuration_snapshot=default_plan_configuration(),
        knowledge_scope=legacy_interview_knowledge_scope_snapshot(),
        questions=(
            QuestionIntentV1(
                question_id="q1",
                position=1,
                kind="project",
                focus="Redis 库存一致性",
                difficulty="advanced",
                assessment_goals=("failure_mode", "recovery", "tradeoff"),
                expected_minutes=5,
                expected_followups=1,
                knowledge_binding={},
            ),
        ),
    )
    binding = SessionPlanBinding(
        plan_origin="plan_revision",
        plan_revision_id="00000000-0000-4000-8000-000000000101",
        plan_family_id="00000000-0000-4000-8000-000000000102",
        revision=1,
        plan_sha256=plan_payload_sha256(plan),
        configuration_snapshot=plan.configuration_snapshot.model_dump(mode="json"),
        plan_snapshot=plan.model_dump(mode="json"),
    )
    return make_durable_initial_state_v3(
        "real-jit-synthetic-session",
        plan,
        plan_binding=binding,
        job_description="Synthetic backend engineer role",
        resume_text="Synthetic Redis and messaging project",
        job_tags=["Redis"],
    )


def _run_jit_generation(llm: OpenAIInterviewLLM):
    state = _jit_state()
    generations = BrowserV3GenerationStore()
    deps = DurableInterviewGraphDependencies(
        workflow_store=object(),
        project_state=lambda current: {
            "state_version": int(current["state_version"]) + 1,
        },
        generation_store=generations,
        examiner=ExaminerAgent(llm=llm),
    )
    graph = build_durable_interview_graph_v3(
        deps,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": state["session_id"]}}
    graph.invoke(state, config=config)
    snapshot = graph.get_state(config)
    assert snapshot.next == ("wait_for_answer",)
    return snapshot.values, generations, graph, config


def _authorization_payload(*, scope=JIT_MAIN_QUESTION_SCOPE, **overrides):
    if scope == JIT_MAIN_QUESTION_SCOPE:
        schema_version = "interview-jit-provider-authorization-v1"
        max_requests = 3
        max_input_tokens_per_request = 12000
        max_output_tokens_per_request = 160
    elif scope == LEGACY_REVIEWER_SMOKE_SCOPE:
        schema_version = "interview-provider-authorization-v1"
        max_requests = 4
        max_input_tokens_per_request = 24000
        max_output_tokens_per_request = 4096
    else:
        raise ValueError(f"unsupported local authorization scope: {scope}")
    payload = {
        "schema_version": schema_version,
        "authorization_scope": scope,
        "authorization_id": "jit-m6-local-test",
        "base_url": "https://provider.example",
        "model": "deepseek-v4-pro",
        "expires_at": "2099-01-01T00:00:00Z",
        "data_classification": "synthetic_only",
        "max_requests": max_requests,
        "max_input_tokens_per_request": max_input_tokens_per_request,
        "max_output_tokens_per_request": max_output_tokens_per_request,
        "max_total_input_tokens": max_requests * max_input_tokens_per_request,
        "max_total_output_tokens": max_requests * max_output_tokens_per_request,
    }
    payload.update(overrides)
    return payload


def _configure_local_authorization(monkeypatch, tmp_path, payload=None):
    payload = payload or _authorization_payload()
    receipt_path = tmp_path / "authorization.json"
    ledger_path = tmp_path / "requests.json"
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv(RECEIPT_PATH_ENV, str(receipt_path))
    monkeypatch.setenv(RECEIPT_SHA_ENV, canonical_receipt_sha256(payload))
    monkeypatch.setenv(LEDGER_PATH_ENV, str(ledger_path))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", payload["base_url"])
    monkeypatch.setenv("OPENAI_MODEL", payload["model"])
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "0")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "128000")
    for name in (
        "MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS",
        "MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS",
        "MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    return receipt_path, ledger_path


def test_real_jit_diagnostic_defaults_and_provider_timeout_binding(monkeypatch):
    for name in (
        "MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS",
        "MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS",
        "MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = load_main_question_generation_settings()
    assert (
        settings.max_provider_invocations
        == MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS
        == 2
    )
    assert (
        settings.attempt_timeout_seconds
        == MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS
        == 20
    )
    assert (
        settings.total_timeout_seconds
        == MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS
        == 30
    )

    class BoundModel:
        def __init__(self):
            self.bind_calls = []

        def bind(self, **kwargs):
            self.bind_calls.append(kwargs)
            return self

        def invoke(self, _prompt):
            return SimpleNamespace(content="Redis 库存一致性失败时你会如何恢复？")

    model = BoundModel()
    llm = OpenAIInterviewLLM(chat_model=model)
    llm.generate_main_question(
        intent=_jit_state()["plan_snapshot"]["questions"][0],
        timeout_seconds=20,
    )
    assert model.bind_calls == [
        {
            "max_tokens": llm.context_runtime.budget_resolver.resolve(
                profile=llm.model_profile,
                policy=MAIN_QUESTION_CONTEXT_POLICY,
            ).max_output_tokens,
            "timeout": 20,
        }
    ]

    monkeypatch.setenv("MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS", "3")
    with pytest.raises(ValueError, match="between 1 and 2"):
        load_main_question_generation_settings()


def test_real_jit_harness_runs_the_complete_v3_graph():
    class LocalModel:
        def bind(self, **_kwargs):
            return self

        def invoke(self, _prompt):
            return SimpleNamespace(
                content="Redis 库存一致性失败时你会如何恢复？"
            )

    attempts = []
    llm = OpenAIInterviewLLM(
        chat_model=LocalModel(),
        provider_attempt_hook=lambda: attempts.append("attempt"),
    )
    state, generations, graph, graph_config = _run_jit_generation(llm)

    rendered = state["rendered_questions"]["q1"]
    assert rendered["render_mode"] == "generated"
    assert rendered["provider_invocation_count"] == 1
    assert rendered["fallback_used"] is False
    assert attempts == ["attempt"]
    assert generations.get_by_id(rendered["generation_id"]).status == "completed"
    assert graph.get_state(graph_config).next == ("wait_for_answer",)

    graph.invoke(_jit_state(), config=graph_config)
    replay = graph.get_state(graph_config)
    assert replay.next == ("wait_for_answer",)
    replayed = replay.values["rendered_questions"]["q1"]
    assert replayed["text"] == rendered["text"]
    for field in (
        "provider_invocation_count",
        "generation_latency_ms",
        "fallback_used",
        "safe_reason_code",
    ):
        assert replayed[field] == rendered[field]
    assert attempts == ["attempt"]


def test_real_jit_opt_in_fails_closed_without_structured_authorization(monkeypatch):
    monkeypatch.setenv(REAL_JIT_FLAG, "1")
    for name in (
        RECEIPT_PATH_ENV,
        RECEIPT_SHA_ENV,
        LEDGER_PATH_ENV,
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(pytest.fail.Exception, match="missing authorization fields"):
        _require_real_jit_config()


def test_real_smoke_opt_in_fails_closed_without_structured_authorization(monkeypatch):
    monkeypatch.setenv(REAL_SMOKE_FLAG, "1")
    for name in (
        RECEIPT_PATH_ENV,
        RECEIPT_SHA_ENV,
        LEDGER_PATH_ENV,
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(pytest.fail.Exception, match="missing authorization fields"):
        _require_real_smoke_config()


def test_real_smoke_authorization_is_bound_to_report_workload(monkeypatch, tmp_path):
    _configure_local_authorization(monkeypatch, tmp_path)
    with pytest.raises(RealJitAuthorizationError, match="selected workload"):
        authorized_provider_attempt_hook(scope=LEGACY_REVIEWER_SMOKE_SCOPE)

    payload = _authorization_payload(scope=LEGACY_REVIEWER_SMOKE_SCOPE)
    _configure_local_authorization(monkeypatch, tmp_path, payload)
    _config, ledger, provider_attempt_hook = authorized_provider_attempt_hook(
        scope=LEGACY_REVIEWER_SMOKE_SCOPE
    )
    assert ledger.receipt.authorization_scope == LEGACY_REVIEWER_SMOKE_SCOPE
    assert ledger.receipt.max_requests == 4
    assert ledger.receipt.max_input_tokens_per_request == 24000
    assert ledger.receipt.max_output_tokens_per_request == 4096
    assert [provider_attempt_hook() for _index in range(4)] == [1, 2, 3, 4]
    with pytest.raises(RealJitAuthorizationError, match="budget exhausted"):
        provider_attempt_hook()
    assert ledger.snapshot()["consumed_requests"] == 4


def test_real_provider_receipt_requires_authorization_scope(monkeypatch, tmp_path):
    payload = _authorization_payload()
    payload.pop("authorization_scope")
    _configure_local_authorization(monkeypatch, tmp_path, payload)

    with pytest.raises(RealJitAuthorizationError, match="receipt is invalid"):
        authorized_provider_attempt_hook()


@pytest.mark.parametrize(
    ("scope", "schema_version"),
    [
        (
            JIT_MAIN_QUESTION_SCOPE,
            "interview-provider-authorization-v1",
        ),
        (
            LEGACY_REVIEWER_SMOKE_SCOPE,
            "interview-jit-provider-authorization-v1",
        ),
    ],
)
def test_real_provider_receipt_rejects_cross_scope_schema(
    monkeypatch,
    tmp_path,
    scope,
    schema_version,
):
    payload = _authorization_payload(scope=scope, schema_version=schema_version)
    _configure_local_authorization(monkeypatch, tmp_path, payload)

    with pytest.raises(RealJitAuthorizationError, match="receipt is invalid"):
        authorized_provider_attempt_hook(scope=scope)


def test_real_jit_receipt_hash_fails_closed(monkeypatch, tmp_path):
    _configure_local_authorization(monkeypatch, tmp_path)
    monkeypatch.setenv(RECEIPT_SHA_ENV, "0" * 64)
    with pytest.raises(RealJitAuthorizationError, match="hash mismatch"):
        authorized_provider_attempt_hook()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "max_requests": 4,
                "max_total_input_tokens": 48000,
                "max_total_output_tokens": 640,
            },
            "max_requests=3",
        ),
        ({"max_total_input_tokens": 35999}, "total input budget"),
        ({"max_total_output_tokens": 479}, "total output budget"),
        ({"data_classification": "internal"}, "synthetic data"),
    ],
)
def test_real_jit_receipt_fields_fail_closed(
    monkeypatch,
    tmp_path,
    overrides,
    message,
):
    invalid = _authorization_payload(**overrides)
    _configure_local_authorization(monkeypatch, tmp_path, invalid)
    with pytest.raises(RealJitAuthorizationError, match=message):
        authorized_provider_attempt_hook()


@pytest.mark.parametrize(
    ("environment_name", "value", "message"),
    [
        ("OPENAI_MODEL", "unauthorized-model", "model is not authorized"),
        (
            "OPENAI_BASE_URL",
            "https://other-provider.example",
            "URL is not authorized",
        ),
    ],
)
def test_real_jit_runtime_identity_must_match_receipt(
    monkeypatch,
    tmp_path,
    environment_name,
    value,
    message,
):
    _configure_local_authorization(monkeypatch, tmp_path)
    monkeypatch.setenv(environment_name, value)
    with pytest.raises(RealJitAuthorizationError, match=message):
        authorized_provider_attempt_hook()


def test_real_jit_ledger_is_atomic_and_exhausts_at_three(tmp_path):
    receipt = RealJitAuthorizationReceipt.model_validate(_authorization_payload())
    receipt_sha = canonical_receipt_sha256(receipt.model_dump(mode="json"))
    ledger_path = tmp_path / "requests.json"
    ledger_path.touch()
    ledger = RealJitRequestLedger(
        ledger_path,
        receipt=receipt,
        receipt_sha256=receipt_sha,
    )

    def consume_or_reject(_index):
        try:
            return ledger.consume()
        except RealJitAuthorizationError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(consume_or_reject, range(8)))

    assert sorted(item for item in outcomes if item is not None) == [1, 2, 3]
    assert outcomes.count(None) == 5
    assert ledger.snapshot()["consumed_requests"] == 3
    with pytest.raises(RealJitAuthorizationError, match="budget exhausted"):
        ledger.consume()


def test_real_jit_ledger_never_overwrites_foreign_authorization(tmp_path):
    receipt = RealJitAuthorizationReceipt.model_validate(_authorization_payload())
    receipt_sha = canonical_receipt_sha256(receipt.model_dump(mode="json"))
    ledger_path = tmp_path / "requests.json"
    original = '{"authorization_id":"someone-else","consumed_requests":1}'
    ledger_path.write_text(original, encoding="utf-8")
    ledger = RealJitRequestLedger(
        ledger_path,
        receipt=receipt,
        receipt_sha256=receipt_sha,
    )

    with pytest.raises(RealJitAuthorizationError, match="another authorization"):
        ledger.consume()
    assert ledger_path.read_text(encoding="utf-8") == original


def test_real_jit_ledger_rechecks_expiry_before_consuming(tmp_path):
    payload = _authorization_payload(expires_at="2030-01-01T00:00:00Z")
    receipt = RealJitAuthorizationReceipt.model_validate(payload)
    receipt_sha = canonical_receipt_sha256(payload)
    ledger_path = tmp_path / "requests.json"
    current_time = [datetime(2029, 12, 31, tzinfo=timezone.utc)]
    ledger = RealJitRequestLedger(
        ledger_path,
        receipt=receipt,
        receipt_sha256=receipt_sha,
        clock=lambda: current_time[0],
    )

    assert ledger.consume() == 1
    current_time[0] = datetime(2030, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(RealJitAuthorizationError, match="authorization is expired"):
        ledger.consume()
    assert ledger.snapshot()["consumed_requests"] == 1


def test_real_jit_ledger_is_atomic_across_processes(tmp_path):
    receipt_payload = _authorization_payload()
    receipt_path = tmp_path / "authorization.json"
    receipt_path.write_text(json.dumps(receipt_payload), encoding="utf-8")
    ledger_path = tmp_path / "process-requests.json"
    receipt_sha = canonical_receipt_sha256(receipt_payload)
    script = "\n".join(
        (
            "import json, sys",
            "from pathlib import Path",
            "from tests.real_llm_jit_support import (",
            "    RealJitAuthorizationError, RealJitAuthorizationReceipt,",
            "    RealJitRequestLedger)",
            "payload = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))",
            "receipt = RealJitAuthorizationReceipt.model_validate(payload)",
            "ledger = RealJitRequestLedger(Path(sys.argv[1]), receipt=receipt, "
            "receipt_sha256=sys.argv[3])",
            "try:",
            "    ledger.consume()",
            "except RealJitAuthorizationError:",
            "    raise SystemExit(3)",
        )
    )
    project_root = Path(__file__).resolve().parents[3]
    processes = []
    try:
        for _index in range(6):
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(ledger_path),
                        str(receipt_path),
                        receipt_sha,
                    ],
                    cwd=project_root,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        results = [process.communicate(timeout=30) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    return_codes = [process.returncode for process in processes]
    assert return_codes.count(0) == 3, results
    assert return_codes.count(3) == 3, results

    receipt = RealJitAuthorizationReceipt.model_validate(receipt_payload)
    ledger = RealJitRequestLedger(
        ledger_path,
        receipt=receipt,
        receipt_sha256=receipt_sha,
    )
    assert ledger.snapshot()["consumed_requests"] == 3


@pytest.mark.real_llm
def test_real_jit_main_question_records_bounded_success_diagnostics():
    config, ledger, provider_attempt_hook = _require_real_jit_config()
    consumed_before = ledger.snapshot()["consumed_requests"]
    llm = OpenAIInterviewLLM(
        config=config,
        provider_attempt_hook=provider_attempt_hook,
    )

    state, _generations, _graph, _graph_config = _run_jit_generation(llm)
    rendered = state["rendered_questions"]["q1"]
    text = rendered["text"]
    consumed_after = ledger.snapshot()["consumed_requests"]

    assert re.search(r"[\u3400-\u9fff]", text)
    assert text.endswith(("？", "?"))
    assert text.count("？") + text.count("?") == 1
    assert "Redis" in text or "库存一致性" in text
    assert rendered["render_mode"] == "generated"
    assert rendered["provider_invocation_count"] in (1, 2)
    assert rendered["generation_latency_ms"] >= 0
    assert rendered["fallback_used"] is False
    assert rendered["safe_reason_code"] == "generated"
    assert (
        consumed_after - consumed_before
        == rendered["provider_invocation_count"]
    )


@pytest.mark.real_llm
def test_real_jit_one_millisecond_timeout_falls_back_and_replay_cannot_overwrite(
    monkeypatch,
):
    monkeypatch.setenv("MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS", "2")
    monkeypatch.setenv("MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS", "0.001")
    monkeypatch.setenv("MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS", "30")
    config, ledger, provider_attempt_hook = _require_real_jit_config()
    consumed_before = ledger.snapshot()["consumed_requests"]
    llm = OpenAIInterviewLLM(
        config=config,
        provider_attempt_hook=provider_attempt_hook,
    )

    outer_started = monotonic()
    state, generations, graph, graph_config = _run_jit_generation(llm)
    outer_elapsed_seconds = monotonic() - outer_started
    rendered = state["rendered_questions"]["q1"]
    consumed_after = ledger.snapshot()["consumed_requests"]
    assert outer_elapsed_seconds < TIMEOUT_TEST_MAX_SECONDS
    assert consumed_after - consumed_before == 1
    assert rendered["provider_invocation_count"] == 1
    assert rendered["render_mode"] == "fallback"
    assert rendered["fallback_reason_code"] == "provider_timeout"
    assert rendered["fallback_used"] is True
    assert rendered["safe_reason_code"] == "provider_timeout"
    assert rendered["generation_latency_ms"] >= 0
    assert rendered["generation_latency_ms"] < TIMEOUT_DIAGNOSTIC_MAX_MS

    generation = generations.get_by_id(rendered["generation_id"])
    expected_diagnostics = {
        "provider_invocation_count": rendered["provider_invocation_count"],
        "generation_latency_ms": rendered["generation_latency_ms"],
        "fallback_used": rendered["fallback_used"],
        "safe_reason_code": rendered["safe_reason_code"],
    }
    assert {
        field: getattr(generation, field) for field in expected_diagnostics
    } == expected_diagnostics
    final_text = generation.final_text
    assert final_text == rendered["text"]
    # This is only an in-memory completed-write guard. Production lease and
    # fencing evidence belongs to the protected PostgreSQL suite.
    with pytest.raises(GenerationAlreadyCompleted):
        generations.complete_attempt(
            generation.generation_id,
            generation.active_attempt,
            "late Provider result must not win",
            lease_token="stale-lease",
            fencing_version=0,
            result_mode="generated",
            provider_invocation_count=1,
            generation_latency_ms=1,
            fallback_used=False,
            safe_reason_code="generated",
        )
    assert generations.get_by_id(generation.generation_id).final_text == final_text
    persisted = generations.get_by_id(generation.generation_id)
    assert {
        field: getattr(persisted, field) for field in expected_diagnostics
    } == expected_diagnostics

    replay_consumed_before = ledger.snapshot()["consumed_requests"]
    graph.invoke(_jit_state(), config=graph_config)
    replay_snapshot = graph.get_state(graph_config)
    replay_consumed_after = ledger.snapshot()["consumed_requests"]
    replayed_rendered = replay_snapshot.values["rendered_questions"]["q1"]
    replayed_generation = generations.get_by_id(generation.generation_id)
    assert replay_snapshot.next == ("wait_for_answer",)
    assert replay_consumed_after - replay_consumed_before == 0
    assert replayed_rendered["text"] == final_text
    assert {
        field: replayed_rendered[field] for field in expected_diagnostics
    } == expected_diagnostics
    assert replayed_generation.final_text == final_text
    assert {
        field: getattr(replayed_generation, field)
        for field in expected_diagnostics
    } == expected_diagnostics


def _assert_legacy_v1_v2_replay_never_calls_provider(
    *,
    config,
    ledger,
    provider_attempt_hook,
):
    consumed_before = ledger.snapshot()["consumed_requests"]

    class ProviderTripwireModel:
        def bind(self, **_kwargs):
            return self

        def invoke(self, _prompt):
            raise AssertionError("legacy replay must not call the Provider")

    llm = OpenAIInterviewLLM(
        config=config,
        chat_model=ProviderTripwireModel(),
        provider_attempt_hook=provider_attempt_hook,
    )

    v1_plan = InterviewPlan(
        title="Legacy V1 interview",
        questions=[
            InterviewQuestion(
                id="legacy-q1",
                kind="technical",
                prompt="请解释旧版缓存一致性方案。",
                focus="缓存一致性",
            )
        ],
    )
    v1_binding = legacy_session_plan_binding(v1_plan)

    configuration = default_plan_configuration()
    v2_plan = InterviewPlanV2(
        title="Legacy V2 interview",
        configuration_snapshot=configuration,
        questions=(
            InterviewPlanQuestionV2(
                question_id="00000000-0000-4000-8000-000000000201",
                position=1,
                question_text="请说明旧版消息投递失败如何恢复。",
                focus="消息恢复",
                question_type="project",
                difficulty="intermediate",
                expected_minutes=5,
                expected_followups=1,
                origin="generated",
            ),
        ),
    )
    v2_binding = SessionPlanBinding(
        plan_origin="plan_revision",
        plan_revision_id="00000000-0000-4000-8000-000000000202",
        plan_family_id="00000000-0000-4000-8000-000000000203",
        revision=1,
        plan_sha256=plan_payload_sha256(v2_plan),
        configuration_snapshot=configuration.model_dump(mode="json"),
        plan_snapshot=v2_plan.model_dump(mode="json"),
    )

    cases = (
        ("v1", v1_plan, v1_binding, "请解释旧版缓存一致性方案。"),
        (
            "v2",
            v2_plan_to_legacy(v2_plan),
            v2_binding,
            "请说明旧版消息投递失败如何恢复。",
        ),
    )
    for version, plan, binding, expected_question in cases:
        store = InterviewSessionStore(llm=llm)
        session_id = f"real-jit-legacy-{version}"
        first = store.start(
            plan,
            job_description="Synthetic legacy role",
            resume_text="Synthetic legacy resume",
            job_tags=["legacy"],
            plan_binding=binding,
            session_id=session_id,
        )
        replay = store.start(
            plan,
            job_description="Synthetic legacy role",
            resume_text="Synthetic legacy resume",
            job_tags=["legacy"],
            plan_binding=binding,
            session_id=session_id,
        )
        assert first.current_question.prompt == expected_question
        assert replay.current_question.prompt == expected_question

        report = build_fallback_report(store.get(session_id))
        store.save_report(session_id, report)
        record = store.get_report_record(session_id)
        assert record is not None
        replayed_record = record.__class__.model_validate(
            record.model_dump(mode="json")
        )
        assert replayed_record.report.feedbacks[0].question_text == expected_question

    assert ledger.snapshot()["consumed_requests"] == consumed_before


def test_real_jit_legacy_v1_v2_replay_is_provider_free(monkeypatch, tmp_path):
    _configure_local_authorization(monkeypatch, tmp_path)
    monkeypatch.setenv(REAL_JIT_FLAG, "1")
    config, ledger, provider_attempt_hook = _require_real_jit_config()

    _assert_legacy_v1_v2_replay_never_calls_provider(
        config=config,
        ledger=ledger,
        provider_attempt_hook=provider_attempt_hook,
    )


@pytest.mark.real_llm
def test_real_jit_authorized_legacy_v1_v2_replay_never_calls_provider():
    config, ledger, provider_attempt_hook = _require_real_jit_config()
    _assert_legacy_v1_v2_replay_never_calls_provider(
        config=config,
        ledger=ledger,
        provider_attempt_hook=provider_attempt_hook,
    )


@pytest.mark.real_llm
def test_real_llm_smoke_cases_pass_quality_gates():
    config, ledger, provider_attempt_hook = _require_real_smoke_config()
    consumed_before = ledger.snapshot()["consumed_requests"]
    llm = OpenAIInterviewLLM(
        config=config,
        provider_attempt_hook=provider_attempt_hook,
    )
    evaluator = ShadowReviewerAgent(
        llm=llm,
        vector_store=GoldenVectorStore(),
    )

    case_request_deltas = []
    for case_id in ["redis-strong-cache-aside", "redis-weak-basic-cache"]:
        consumed_before_case = ledger.snapshot()["consumed_requests"]
        report = evaluator.evaluate(make_state(_case_by_id(case_id)))
        consumed_after_case = ledger.snapshot()["consumed_requests"]
        case_request_delta = consumed_after_case - consumed_before_case
        case_request_deltas.append(case_request_delta)

        # These are the authoritative InterviewReport provenance fields. The
        # summary is deterministic by design; deterministic summary mode is
        # not evidence that the report itself used the safe fallback path.
        assert report.is_fallback is False
        assert report.generation_status == "complete"
        assert report.generation_reason_code == "normal"
        assert report.report_path == "full_session"
        assert report.technical_appendix.report_path == "full_session"
        assert report.technical_appendix.summary_generation_mode == "deterministic"
        assert "deterministic_fallback" not in (
            report.technical_appendix.summary_generation_mode
        )

        if llm.report_output_mode == "raw_only":
            assert case_request_delta == 1
        else:
            # Structured-first may legitimately issue one raw retry after a
            # structured response-format rejection; both are Provider paths,
            # while the report provenance above proves no safe report fallback.
            assert case_request_delta in (1, 2)
        issues = collect_report_quality_issues(report, expected_question_count=1)
        assert issues == [], f"{case_id}: {issues}"
        assert 0 <= report.overall_score <= 100

    consumed_after = ledger.snapshot()["consumed_requests"]
    assert sum(case_request_deltas) == consumed_after - consumed_before
    assert all(delta >= 1 for delta in case_request_deltas)
