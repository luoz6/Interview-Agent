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
    assert result["score"] == 0
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
        classmethod(lambda cls: LLMConfig(api_key="secret", model="fake", base_url="https://api.example.com/v1")),
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


def test_main_returns_zero_for_complete_passing_run(monkeypatch, tmp_path):
    exit_code, model, run_dir = run_main(monkeypatch, tmp_path, payload=provider_payload)

    assert exit_code == 0
    assert model.invoke_calls == 1
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["base_url_host"] == "api.example.com"
    assert "secret" not in (run_dir / "manifest.json").read_text(encoding="utf-8")
    assert manifest["decision"] == "PASS"


def test_main_returns_one_for_completed_fallback_failure(monkeypatch, tmp_path):
    exit_code, _, run_dir = run_main(monkeypatch, tmp_path, payload="not json")

    assert exit_code == 1
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
