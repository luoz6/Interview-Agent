"""Unit tests for the report evaluation CLI using an isolated fake provider."""

import json
from pathlib import Path

import pytest

from app.services.llm import LLMConfig
from app.services.report_eval_dataset import load_evaluation_dataset
from scripts import evaluate_report_quality as cli


class JsonMessage:
    def __init__(self, content):
        self.content = content


class StaticJsonChatModel:
    def __init__(self, payload):
        self.payload = payload
        self.invoke_calls = 0
        self.structured_output_calls = 0

    def invoke(self, prompt):
        self.invoke_calls += 1
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)
        return JsonMessage(content)

    def with_structured_output(self, schema, method=None):
        self.structured_output_calls += 1
        return self


class InnerModel:
    def __init__(self):
        self.invoke_calls = 0
        self.structured = None

    def invoke(self, prompt):
        self.invoke_calls += 1
        return JsonMessage("{}")

    def with_structured_output(self, schema, method=None):
        self.structured = InnerModel()
        return self.structured


def first_case():
    return load_evaluation_dataset(Path("tests/golden/report_quality_v1.json")).cases[0]


def provider_payload(case):
    return {
        "session_id": "stage40",
        "question_results": [
            {
                "question_id": case.case_id,
                "dimension_evidence": [
                    {
                        "dimension": "depth",
                        "observed": [case.required_observations[0]],
                        "missing": [],
                        "quality_signals": ["concept", "concrete_steps"],
                    },
                    {
                        "dimension": "engineering",
                        "observed": [case.required_observations[1]],
                        "missing": [],
                        "quality_signals": ["concrete_steps", "fallback"],
                    },
                    {
                        "dimension": "breadth",
                        "observed": [case.required_observations[0]],
                        "missing": [],
                        "quality_signals": ["concept"],
                    },
                    {
                        "dimension": "communication",
                        "observed": [case.required_observations[0]],
                        "missing": [],
                        "quality_signals": ["clarity"],
                    },
                ],
                "rationale": "??????????????",
                "critique": "???????????",
                "better_answer": "????????????",
                "reference_chunk_ids": [case.reference.chunk_id],
            }
        ],
    }


def test_cli_defaults_to_two_runs_and_fifty_provider_invocations():
    args = cli.build_parser().parse_args([])
    assert args.runs_per_case == 2
    assert args.max_provider_invocations == 50
    assert args.provider == "deepseek"


def test_budgeted_chat_model_counts_raw_and_structured_invocations():
    budget = cli.ProviderInvocationBudget(2)
    inner = InnerModel()
    model = cli.BudgetedChatModel(inner, budget)

    model.invoke("raw")
    model.with_structured_output(dict).invoke("structured")

    assert budget.used == 2
    with pytest.raises(cli.ProviderInvocationBudgetExhausted):
        model.invoke("too much")


def test_provider_budget_enforces_run_level_prior_usage():
    budget = cli.ProviderInvocationBudget(3, prior_used=2)

    budget.consume()

    assert budget.used == 1
    assert budget.prior_used == 2
    with pytest.raises(cli.ProviderInvocationBudgetExhausted, match="cumulative"):
        budget.consume()


def test_deepseek_case_evaluator_uses_raw_only_and_writes_trace(tmp_path):
    case = first_case()
    budget = cli.ProviderInvocationBudget(2)
    model = StaticJsonChatModel(provider_payload(case))
    evaluator = cli.DeepSeekCaseEvaluator(
        chat_model=cli.BudgetedChatModel(model, budget),
        budget=budget,
    )

    result = evaluator.evaluate_case(
        case,
        session_id="stage40-case-1",
        run_number=1,
        trace_dir=tmp_path,
    )

    assert result["case_id"] == case.case_id
    assert result["fallback"] is False
    assert result["provider_invocations"] == 1
    assert budget.used == 1
    assert model.invoke_calls == 1
    assert model.structured_output_calls == 0
    assert list((tmp_path / "stage40-case-1").glob("*_raw_json.json"))
    assert list((tmp_path / "stage40-case-1").glob("*_normalized_payload.json"))


def test_deepseek_case_evaluator_counts_invalid_json_as_fallback(tmp_path):
    case = first_case()
    budget = cli.ProviderInvocationBudget(1)
    evaluator = cli.DeepSeekCaseEvaluator(
        chat_model=cli.BudgetedChatModel(StaticJsonChatModel("not json"), budget),
        budget=budget,
    )

    result = evaluator.evaluate_case(
        case,
        session_id="stage40-invalid-1",
        run_number=1,
        trace_dir=tmp_path,
    )

    assert result["fallback"] is True
    assert result["score"] is None
    assert result["provider_invocations"] == 1
    assert list((tmp_path / "stage40-invalid-1").glob("*_report_output_format_error.json"))


def test_markdown_report_contains_release_decision():
    content = cli.render_markdown(
        {
            "passed": False,
            "ranking_accuracy": 0.80,
            "evidence_grounding_rate": 0.95,
            "fallback_rate": 0.0,
            "max_score_delta": 4,
            "completed_attempt_count": 39,
            "expected_attempt_count": 40,
            "failed_gates": ["ranking_accuracy"],
            "blocking_failures": [],
        }
    )
    assert "Stage 40 Release Decision: FAIL" in content
    assert "ranking_accuracy" in content
    assert "39/40" in content


def run_main(monkeypatch, tmp_path, *, payload, runs_per_case=1, budget=5):
    case = first_case()
    model = StaticJsonChatModel(payload(case) if callable(payload) else payload)
    monkeypatch.setattr(
        cli.LLMConfig,
        "from_env",
        classmethod(
            lambda cls: LLMConfig(
                api_key="secret",
                model="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
            )
        ),
    )
    monkeypatch.setattr(cli.OpenAIInterviewLLM, "_build_chat_model", staticmethod(lambda config: model))
    exit_code = cli.main(
        [
            "--case-id",
            case.case_id,
            "--runs-per-case",
            str(runs_per_case),
            "--max-provider-invocations",
            str(budget),
            "--out",
            str(tmp_path),
            "--run-id",
            "test-run",
        ]
    )
    return exit_code, model, tmp_path / "test-run"


def test_main_returns_two_when_completed_run_has_insufficient_gate_sample(monkeypatch, tmp_path):
    exit_code, model, run_dir = run_main(monkeypatch, tmp_path, payload=provider_payload)

    assert exit_code == 2
    assert model.invoke_calls == 1
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["base_url_host"] == "api.deepseek.com"
    assert "secret" not in (run_dir / "manifest.json").read_text(encoding="utf-8")
    assert manifest["decision"] == "INSUFFICIENT_SAMPLE"
    assert len(manifest["rubric_sha256"]) == 64
    assert manifest["max_provider_invocations"] == 5
    assert manifest["authorization_receipt"]["passed"] is True
    assert manifest["authorization_receipt"]["task"] == "T27"
    assert manifest["authorization_receipt"]["data_categories"] == [
        "public_technical_material",
        "synthetic_candidate_answers",
    ]
    assert len(manifest["authorization_receipt_sha256"]) == 64


def test_main_returns_two_for_fallback_run_below_formal_sample_size(monkeypatch, tmp_path):
    exit_code, _, run_dir = run_main(monkeypatch, tmp_path, payload="not json")

    assert exit_code == 2
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["fallback_rate"] == 1.0


def test_main_returns_two_when_budget_exhausts_before_all_attempts(monkeypatch, tmp_path):
    exit_code, model, run_dir = run_main(
        monkeypatch,
        tmp_path,
        payload=provider_payload,
        runs_per_case=2,
        budget=1,
    )

    assert exit_code == 2
    assert model.invoke_calls == 1
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["decision"] == "INCOMPLETE"
    assert manifest["completed_attempts"] == 1


def test_resume_with_complete_v2_binding_is_safe_and_does_not_repeat_attempt(
    monkeypatch, tmp_path
):
    first_code, first_model, run_dir = run_main(
        monkeypatch, tmp_path, payload=provider_payload
    )
    assert first_code == 2
    assert first_model.invoke_calls == 1
    resumed_model = StaticJsonChatModel(provider_payload(first_case()))
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(lambda _config: resumed_model),
    )

    resumed_code = cli.main(
        [
            "--case-id",
            first_case().case_id,
            "--runs-per-case",
            "1",
            "--max-provider-invocations",
            "5",
            "--out",
            str(tmp_path),
            "--run-id",
            "test-run",
            "--resume",
        ]
    )

    assert resumed_code == 2
    assert resumed_model.invoke_calls == 0
    assert json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))[
        "schema_version"
    ] == cli.RUN_MANIFEST_SCHEMA


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "report-quality-evaluation-run-v1"),
        ("run_id", "other-run"),
        ("dataset_sha256", "0" * 64),
        ("case_ids", ["other-case"]),
        ("provider", "OtherProvider"),
        ("model", "other-model"),
        ("prompt_version", "other-prompt"),
        ("rubric_sha256", "0" * 64),
        ("authorization_sha256", "0" * 64),
        ("authorization_receipt_sha256", "0" * 64),
        ("max_provider_invocations", 999),
    ],
)
def test_resume_manifest_drift_fails_before_model_or_provider(
    monkeypatch, tmp_path, field, value
):
    run_main(monkeypatch, tmp_path, payload=provider_payload)
    manifest_path = tmp_path / "test-run" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(
            lambda _config: pytest.fail(
                "resume binding must fail before model construction"
            )
        ),
    )

    with pytest.raises(SystemExit, match="resume manifest mismatch"):
        cli.main(
            [
                "--case-id",
                first_case().case_id,
                "--runs-per-case",
                "1",
                "--out",
                str(tmp_path),
                "--run-id",
                "test-run",
                "--resume",
            ]
        )


@pytest.mark.parametrize(
    "run_id",
    ["..", "../outside", r"C:\outside\run", r"\\server\share\run"],
)
@pytest.mark.parametrize("resume", [False, True])
def test_fresh_and_resume_reject_unsafe_run_id_before_any_write(
    tmp_path, run_id, resume
):
    argv = ["--out", str(tmp_path / "safe"), "--run-id", run_id]
    if resume:
        argv.append("--resume")

    with pytest.raises(SystemExit, match="invalid --run-id"):
        cli.main(argv)

    assert not (tmp_path / "safe").exists()


def test_provider_authorization_preflight_blocks_before_store_or_model(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        cli.LLMConfig,
        "from_env",
        classmethod(
            lambda cls: LLMConfig(
                api_key="secret",
                model="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
            )
        ),
    )
    monkeypatch.setattr(
        cli,
        "validate_provider_run",
        lambda *_args, **_kwargs: ("DATA_POLICY_VIOLATION",),
    )
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(lambda _config: pytest.fail("model must not be built")),
    )

    with pytest.raises(SystemExit, match="DATA_POLICY_VIOLATION"):
        cli.main(
            [
                "--case-id",
                first_case().case_id,
                "--runs-per-case",
                "1",
                "--out",
                str(tmp_path),
                "--run-id",
                "preflight-blocked",
            ]
        )

    assert not (tmp_path / "preflight-blocked").exists()


def test_nonfrozen_dataset_blocks_redaction_preflight_before_model(
    monkeypatch, tmp_path
):
    dataset = tmp_path / "report-quality-copy.json"
    dataset.write_bytes(
        Path("tests/golden/report_quality_v1.json").read_bytes() + b"\n"
    )
    monkeypatch.setattr(
        cli.LLMConfig,
        "from_env",
        classmethod(
            lambda cls: LLMConfig(
                api_key="secret",
                model="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
            )
        ),
    )
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(lambda _config: pytest.fail("model must not be built")),
    )

    with pytest.raises(SystemExit, match="REDACTION_PREFLIGHT_FAILED"):
        cli.main(
            [
                "--dataset",
                str(dataset),
                "--case-id",
                first_case().case_id,
                "--runs-per-case",
                "1",
                "--out",
                str(tmp_path),
                "--run-id",
                "dataset-blocked",
            ]
        )

    assert not (tmp_path / "dataset-blocked").exists()


def test_repeated_partial_resumes_never_exceed_cumulative_provider_cap(
    monkeypatch, tmp_path
):
    code, model, run_dir = run_main(
        monkeypatch,
        tmp_path,
        payload=provider_payload,
        runs_per_case=2,
        budget=1,
    )
    assert code == 2
    assert model.invoke_calls == 1
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(lambda _config: pytest.fail("Provider model must not be built")),
    )
    resume_argv = [
        "--case-id",
        first_case().case_id,
        "--runs-per-case",
        "2",
        "--max-provider-invocations",
        "1",
        "--out",
        str(tmp_path),
        "--run-id",
        "test-run",
        "--resume",
    ]

    for _attempt in range(2):
        with pytest.raises(SystemExit, match="cumulative provider invocation budget"):
            cli.main(resume_argv)

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider_invocations"] == 1
    assert manifest["completed_attempts"] == 1


def test_concurrent_resume_lock_blocks_before_model_or_provider(
    monkeypatch, tmp_path
):
    run_main(
        monkeypatch,
        tmp_path,
        payload=provider_payload,
        runs_per_case=2,
        budget=1,
    )
    store = cli.EvaluationArtifactStore.open(root=tmp_path, run_id="test-run")
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(lambda _config: pytest.fail("Provider model must not be built")),
    )

    with store.exclusive_run_lock():
        with pytest.raises(SystemExit, match="locked by another process"):
            cli.main(
                [
                    "--case-id",
                    first_case().case_id,
                    "--runs-per-case",
                    "2",
                    "--max-provider-invocations",
                    "1",
                    "--out",
                    str(tmp_path),
                    "--run-id",
                    "test-run",
                    "--resume",
                ]
            )
