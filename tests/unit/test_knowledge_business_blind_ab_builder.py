import json
from pathlib import Path

from scripts.build_knowledge_business_blind_ab import (
    DEFAULT_BASELINE,
    DEFAULT_CANDIDATE,
    DEFAULT_CORPUS,
    DEFAULT_DATASET,
    _annotation_template,
    build_business_dataset,
)
from app.services.knowledge_business_eval import build_blind_business_eval_package


def test_builder_freezes_fifty_real_engine_outputs_without_human_labels(tmp_path):
    calls = []

    def invoke(prompt):
        calls.append(prompt)
        return {
            "followup": "你会如何验证这个机制在故障条件下仍然成立？",
            "reviewer_runs": [
                {"score": 61, "confidence": "medium", "text": "机制存在，但验证证据不足。"},
                {"score": 59, "confidence": "low", "text": "回答覆盖部分机制，缺少失败边界。"},
            ],
        }

    dataset, receipt = build_business_dataset(
        retrieval_dataset_path=DEFAULT_DATASET,
        baseline_path=DEFAULT_BASELINE,
        candidate_path=DEFAULT_CANDIDATE,
        corpus_dir=DEFAULT_CORPUS,
        case_families=25,
        invoke=invoke,
        cache_path=tmp_path / "cache.json",
    )

    dataset.validate_release_shape()
    assert len(dataset.cases) == 50
    assert sum(case.split == "holdout" for case in dataset.cases) == 12
    assert {case.target for case in dataset.cases} == {"followup", "reviewer"}
    assert len({case.scenario_type for case in dataset.cases}) == 8
    assert len(calls) == 50
    assert receipt["provider_bundle_calls"] == 50
    assert all(
        len(output.repeated_scores) == 2
        for case in dataset.cases
        if case.target == "reviewer"
        for output in (case.baseline_output, case.candidate_output)
    )
    assert any(
        case.system_failure_scenario for case in dataset.cases if case.target == "reviewer"
    )


def test_builder_resumes_provider_cache_and_blank_template_stays_unannotated(tmp_path):
    cache = tmp_path / "cache.json"

    def invoke(_prompt):
        return {
            "followup": "请补充一个可复现的验证方法？",
            "reviewer_runs": [
                {"score": 50, "confidence": "low", "text": "证据有限。"},
                {"score": 50, "confidence": "low", "text": "仍需补充验证。"},
            ],
        }

    first, _ = build_business_dataset(
        retrieval_dataset_path=DEFAULT_DATASET,
        baseline_path=DEFAULT_BASELINE,
        candidate_path=DEFAULT_CANDIDATE,
        corpus_dir=DEFAULT_CORPUS,
        case_families=25,
        invoke=invoke,
        cache_path=cache,
    )
    second, receipt = build_business_dataset(
        retrieval_dataset_path=DEFAULT_DATASET,
        baseline_path=DEFAULT_BASELINE,
        candidate_path=DEFAULT_CANDIDATE,
        corpus_dir=DEFAULT_CORPUS,
        case_families=25,
        invoke=lambda _prompt: (_ for _ in ()).throw(AssertionError("cache miss")),
        cache_path=cache,
    )
    assert len(second.cases) == len(first.cases)
    assert receipt["provider_bundle_calls"] == 0
    assert receipt["provider_cache_hits"] == 50

    package, _ = build_blind_business_eval_package(
        second, split="tuning", seed="test-secret"
    )
    template = _annotation_template(package)
    assert template["records"] == []
    assert template["consensus"] == []
    assert template["instructions"]["do_not_fill_with_model_generated_ratings"] is True
    assert "legacy" not in json.dumps(package.model_dump(mode="json"))
