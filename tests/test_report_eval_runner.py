from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.report_eval_artifacts import EvaluationArtifactStore
from app.services.report_eval_dataset import load_evaluation_dataset
from app.services.report_eval_runner import EvaluationRunner


class FakeEvaluator:
    def __init__(self):
        self.calls = []

    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        self.calls.append((case.case_id, run_number))
        return normalized_attempt(case, run_number)


class TransientEvaluator(FakeEvaluator):
    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        self.calls.append((case.case_id, run_number))
        if len(self.calls) == 1:
            raise RuntimeError("429 rate limit")
        return normalized_attempt(case, run_number)


class PermanentEvaluator(FakeEvaluator):
    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        self.calls.append((case.case_id, run_number))
        raise ValueError("invalid provider payload")


class FallbackThenSuccessEvaluator(FakeEvaluator):
    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        self.calls.append((case.case_id, run_number))
        result = normalized_attempt(case, run_number)
        if len(self.calls) == 1:
            result["fallback"] = True
        return result


class ProviderInvocationBudgetExhausted(RuntimeError):
    pass


class BudgetEvaluator(FakeEvaluator):
    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        self.calls.append((case.case_id, run_number))
        raise ProviderInvocationBudgetExhausted("budget exhausted")


class WrappedBudgetEvaluator(FakeEvaluator):
    def evaluate_case(self, case, *, session_id, run_number, trace_dir):
        self.calls.append((case.case_id, run_number))
        try:
            raise ProviderInvocationBudgetExhausted("budget exhausted")
        except ProviderInvocationBudgetExhausted as exc:
            raise RuntimeError("report generation failed") from exc


def one_case_dataset():
    dataset = load_evaluation_dataset(Path("tests/golden/report_quality_v1.json"))
    return SimpleNamespace(version=dataset.version, cases=[dataset.cases[0]])


def normalized_attempt(case, run_number):
    return {
        "case_id": case.case_id,
        "group_id": case.group_id,
        "quality_level": case.quality_level,
        "run_number": run_number,
        "score": case.expected_score_range[0],
        "answer": case.answer,
        "observed": case.required_observations,
        "required_observations": case.required_observations,
        "forbidden_claims": case.forbidden_claims,
        "applicable_dimensions": case.expected_applicable_dimensions,
        "expected_applicable_dimensions": case.expected_applicable_dimensions,
        "fallback": False,
        "output_text": "",
    }


def make_store(tmp_path):
    return EvaluationArtifactStore.create(root=tmp_path, run_id="run-1", manifest={})


def test_runner_obeys_attempt_cap_and_resume(tmp_path):
    dataset = one_case_dataset()
    evaluator = FakeEvaluator()
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=make_store(tmp_path), sleep=lambda _: None)

    runner.run(dataset=dataset, runs_per_case=2, max_attempts=1)
    runner.run(dataset=dataset, runs_per_case=2, max_attempts=1)

    assert evaluator.calls == [(dataset.cases[0].case_id, 1), (dataset.cases[0].case_id, 2)]


def test_runner_retries_transient_failure(tmp_path):
    dataset = one_case_dataset()
    evaluator = TransientEvaluator()
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=make_store(tmp_path), sleep=lambda _: None)

    completed = runner.run(dataset=dataset, runs_per_case=1, max_attempts=1)

    assert len(evaluator.calls) == 2
    assert len(completed) == 1


def test_runner_retries_fallback_result(tmp_path):
    dataset = one_case_dataset()
    evaluator = FallbackThenSuccessEvaluator()
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=make_store(tmp_path), sleep=lambda _: None)

    completed = runner.run(dataset=dataset, runs_per_case=1, max_attempts=1)

    assert len(evaluator.calls) == 2
    assert completed[0]["fallback"] is False


def test_runner_records_permanent_failure_and_leaves_attempt_pending(tmp_path):
    dataset = one_case_dataset()
    evaluator = PermanentEvaluator()
    store = make_store(tmp_path)
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=store, sleep=lambda _: None)

    assert runner.run(dataset=dataset, runs_per_case=1, max_attempts=1) == []
    assert len(evaluator.calls) == 1
    assert store.pending_attempts([dataset.cases[0].case_id], runs_per_case=1) == [(dataset.cases[0].case_id, 1)]
    assert (store.attempt_directory(dataset.cases[0].case_id, 1) / "error.json").exists()


def test_runner_propagates_provider_budget_exhaustion(tmp_path):
    dataset = one_case_dataset()
    evaluator = BudgetEvaluator()
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=make_store(tmp_path), sleep=lambda _: None)

    with pytest.raises(ProviderInvocationBudgetExhausted, match="budget exhausted"):
        runner.run(dataset=dataset, runs_per_case=1, max_attempts=1)


def test_runner_propagates_wrapped_provider_budget_exhaustion(tmp_path):
    dataset = one_case_dataset()
    evaluator = WrappedBudgetEvaluator()
    runner = EvaluationRunner(evaluator=evaluator, artifact_store=make_store(tmp_path), sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="report generation failed"):
        runner.run(dataset=dataset, runs_per_case=1, max_attempts=1)
