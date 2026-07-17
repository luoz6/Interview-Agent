import json
from pathlib import Path

from app.services.report_eval_artifacts import EvaluationArtifactStore
from app.services.report_eval_replay import rescore_run


def test_rescore_run_uses_saved_evidence_and_updates_manifest(tmp_path):
    dataset_path = Path("tests/golden/report_quality_v1.json")
    case_id = "redis-cache-consistency-medium"
    store = EvaluationArtifactStore.create(
        root=tmp_path,
        run_id="run-1",
        manifest={
            "run_id": "run-1",
            "target_attempts": 1,
            "rubric_version": "old",
        },
    )
    attempt_dir = store.attempt_directory(case_id, 1)
    store.write_attempt(
        case_id,
        1,
        normalized={
            "case_id": case_id,
            "group_id": "redis-cache-consistency",
            "quality_level": "medium",
            "run_number": 1,
            "score": 0,
            "answer": "我会使用 cache-aside，先更新数据库，事务提交成功后删除 Redis 缓存。",
            "observed": ["先更新数据库，事务提交成功后删除 Redis 缓存。"],
            "required_observations": ["成功后删除缓存"],
            "forbidden_claims": [],
            "applicable_dimensions": ["depth", "engineering", "breadth", "communication"],
            "expected_applicable_dimensions": ["depth", "engineering", "breadth", "communication"],
            "fallback": False,
            "output_text": "",
        },
    )
    trace_dir = attempt_dir / "trace"
    trace_dir.mkdir()
    (trace_dir / "saved_normalized_payload.json").write_text(
        json.dumps({
            "payload": {
                "question_results": [{
                    "dimension_evidence": [{
                        "dimension": "depth",
                        "observed": ["先更新数据库，事务提交成功后删除 Redis 缓存。"],
                        "missing": ["没有说明失败补偿。"],
                        "quality_signals": [],
                    }]
                }]
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    metrics = rescore_run(run_dir=store.run_dir, dataset_path=dataset_path)

    assert store.load_normalized_attempts()[0]["score"] > 0
    assert metrics["completed_attempt_count"] == 1
    assert store.read_manifest()["rubric_version"] == "stage40-rubric-v2"
    assert store.read_manifest()["rescored_from_saved_evidence"] is True
