import hashlib
import json

from langgraph.checkpoint.memory import InMemorySaver

from app.graphs.durable_review_graph import (
    DurableReviewGraphDependencies,
    build_durable_review_graph,
)
from app.graphs.durable_review_state import make_durable_review_initial_state
from app.services.report import InterviewReport, ReportEvidenceRefV2
from app.services.report_artifact_store import InMemoryReportArtifactStore
from app.services.report_contract import assemble_interview_report
from app.services.report_degraded import build_degraded_report_from_feedbacks
from app.services.report_provider_adapter import normalize_provider_payload
from app.services.report_runtime_quality import evaluate_runtime_report_quality
from tests.test_durable_review_graph import FakeStore
from tests.test_durable_review_state import make_finished_state, make_job
from tests.test_report_artifact_store import publish_payload, start_job


QUESTION_TEXT = "请说明 Redis 缓存一致性的生产实现。"
CANDIDATE_ANSWER = (
    "我先提交数据库事务，再删除缓存，并通过重试、回滚和 p95 监控验证结果。"
)


def _report() -> InterviewReport:
    evaluation_items = [
        {
            "question_id": "q1",
            "question_text": QUESTION_TEXT,
            "question_kind": "technical",
            "focus": "Redis consistency",
            "answer_state": "answered",
            "messages": [
                {
                    "role": "candidate",
                    "content": CANDIDATE_ANSWER,
                    "question_id": "q1",
                }
            ],
            "scoring_references": [],
            "answer_references": [],
        }
    ]
    normalized = normalize_provider_payload(
        {
            "question_results": [
                {
                    "question_id": "q1",
                    "question_text": QUESTION_TEXT,
                    "dimension_evidence": [
                        {
                            "dimension": "depth",
                            "observed": [CANDIDATE_ANSWER],
                            "missing": ["metric_gap: 缺少明确的指标基线"],
                            "quality_signals": [],
                        }
                    ],
                    "rationale": "回答给出了数据库提交、缓存删除和故障恢复顺序。",
                    "critique": "仍需补充指标基线、观察窗口和验收条件。",
                    "reference_chunk_ids": [],
                    "highlights": ["回答说明了缓存一致性主路径。"],
                }
            ]
        },
        evaluation_items,
    )
    return assemble_interview_report(
        session_id="session-gate",
        question_results=normalized.question_results,
        reference_lookup=normalized.reference_lookup,
    )


def _manifest() -> dict:
    question = {
        "question_id": "q1",
        "kind": "technical",
        "prompt_sha256": hashlib.sha256(QUESTION_TEXT.encode("utf-8")).hexdigest(),
        "answer_state": "answered",
        "message_content_sha256": [],
        "evidence_ids": [],
        "evidence_sha256": {},
        "input_sha256": "1" * 64,
    }
    return {
        "session_id": "session-gate",
        "finished_state_version": 4,
        "plan_sha256": "2" * 64,
        "corpus_manifest_sha256": None,
        "message_refs": [],
        "questions": [question],
        "input_sha256": "3" * 64,
    }


def _evaluate(report: InterviewReport, *, raw_payload=None, digest=None, manifest=None):
    raw_payload = raw_payload or report.model_dump(mode="json")
    digest = digest or hashlib.sha256(
        json.dumps(
            raw_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    manifest = manifest or _manifest()
    return evaluate_runtime_report_quality(
        report,
        expected_question_count=1,
        expected_questions=manifest["questions"],
        expected_session_id="session-gate",
        expected_report_sha256=digest,
        artifact_schema_version="report-artifact-v2",
        raw_payload=raw_payload,
        review_input_manifest=manifest,
        expected_candidate_answers={"q1": CANDIDATE_ANSWER},
    )


def _codes(result) -> set[str]:
    return {item.code for item in result.structured_blocking_issues}


def test_safe_degraded_summary_preserves_scores_and_passes_runtime_gate():
    base = _report()
    degraded = build_degraded_report_from_feedbacks(
        session_id=base.session_id,
        feedbacks=base.feedbacks,
        failed_components=["summary"],
        source_failure_code="provider_timeout",
        report_path="microbatch",
    )

    result = _evaluate(degraded)

    assert result.blocking_issues == []
    assert degraded.generation_status == "degraded"
    assert degraded.score_status == "scored"
    assert degraded.overall_score == base.overall_score
    assert degraded.technical_appendix.summary_generation_mode == (
        "deterministic_fallback"
    )


def test_runtime_gate_accepts_canonical_v2_report_and_complete_lineage():
    result = _evaluate(_report())

    assert result.blocking_issues == []
    assert result.warning_issues == []


def test_runtime_gate_recalculates_question_and_aggregate_numbers():
    report = _report()
    report.feedbacks[0].score = (report.feedbacks[0].score or 0) + 1

    result = _evaluate(report)

    assert "question_score_recalculation_mismatch" in _codes(result)
    assert "aggregate_recalculation_mismatch" in _codes(result)


def test_runtime_gate_enforces_frozen_question_identity_and_answer_state():
    report = _report()
    manifest = _manifest()
    manifest["questions"][0]["answer_state"] = "skipped"

    result = _evaluate(report, manifest=manifest)

    assert "answer_state_mismatch" in _codes(result)
    assert "unscored_null_violation" in _codes(result)


def test_runtime_gate_rejects_cross_session_or_mutated_candidate_evidence():
    report = _report()
    report.evidence_refs[0].question_id = "q-other"
    report.evidence_refs[0].excerpt = "Not the candidate answer"

    result = _evaluate(report)

    assert "cross_session_evidence_ref" in _codes(result)
    assert "candidate_namespace_violation" in _codes(result)


def test_runtime_gate_rejects_mutated_answer_and_invented_observed_excerpt():
    report = _report()
    report.feedbacks[0].user_answer = "被替换的候选人回答。"
    report.feedbacks[0].dimension_evidence[0]["observed"] = ["候选人从未说过的内容"]

    result = _evaluate(report)

    assert "candidate_answer_mismatch" in _codes(result)
    assert "observed_evidence_not_in_answer" in _codes(result)


def test_runtime_gate_requires_refs_on_every_key_claim_and_action():
    report = _report()
    report.summary_observations[0].observation_refs = []
    report.priority_actions[0].evidence_refs = []

    result = _evaluate(report)

    assert "ungrounded_key_output" in _codes(result)


def test_runtime_gate_requires_candidate_and_guidance_reference_closure():
    report = _report()
    candidate_ref = report.evidence_refs.pop(0)
    assert candidate_ref.namespace == "candidate"

    result = _evaluate(report)

    assert "candidate_evidence_missing" in _codes(result)
    assert "technical_point_grounding_invalid" in _codes(result)


def test_runtime_gate_reads_raw_payload_before_extra_fields_are_discarded():
    report = _report()
    raw = report.model_dump(mode="json")
    raw["principal_memory"] = {"company": "Acme"}

    result = _evaluate(report, raw_payload=raw)

    assert "forbidden_report_field" in _codes(result)
    assert "artifact_hash_mismatch" in _codes(result)


def test_runtime_gate_blocks_numeric_placeholder_and_metadata_omission():
    report = _report()
    report.feedbacks[0].better_answer += " [实际 QPS 900000]"
    raw = report.model_dump(mode="json")
    raw.pop("scoring_rubric_sha256")

    result = _evaluate(report, raw_payload=raw)

    assert "numeric_placeholder_violation" in _codes(result)
    assert "artifact_metadata_incomplete" in _codes(result)


def test_runtime_gate_returns_codes_for_invalid_evidence_shapes_and_namespaces():
    report = _report()
    report.feedbacks[0].dimension_evidence = [
        {"dimension": "unknown", "observed": [], "missing": []}
    ]
    report.evidence_refs.append(
        ReportEvidenceRefV2(
            evidence_ref_id="wrong-reference-id",
            namespace="reference",
            question_id="q1",
            source_id="knowledge-1",
            excerpt="Generic reference text.",
        )
    )

    result = _evaluate(report)

    assert "dimension_evidence_invalid" in _codes(result)
    assert "reference_namespace_violation" in _codes(result)


def test_validation_exception_becomes_structured_failure_and_never_commits():
    store = FakeStore()
    commits = []
    graph = build_durable_review_graph(
        DurableReviewGraphDependencies(
            workflow_store=store,
            review_question=lambda state, question_id: None,
            generate_report=lambda state: {
                "report_ref": "invalid-report",
                "report_sha256": "5" * 64,
            },
            validate_report=lambda state: (_ for _ in ()).throw(
                ValueError("invalid payload with candidate text")
            ),
            commit_report=lambda state: commits.append(state),
            max_quality_repairs=0,
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        make_durable_review_initial_state(make_job(), make_finished_state()),
        {"configurable": {"thread_id": "review:validation-exception"}},
    )

    assert commits == []
    assert result["quality_issues"] == [
        {
            "code": "report_validation_failed",
            "description": "runtime report validation did not complete",
            "question_id": None,
        }
    ]


def test_failed_quality_gate_never_calls_commit_or_switches_active_head():
    artifacts = InMemoryReportArtifactStore()
    first_job = start_job(artifacts)
    first = artifacts.publish(
        first_job.job_id,
        publish_payload(),
        worker_id="worker-1",
    )
    rescore = start_job(
        artifacts,
        key="runtime-gate-failure",
        kind="rescore",
        source_report_id=first.report_id,
    )
    store = FakeStore()
    commit_calls = []
    graph = build_durable_review_graph(
        DurableReviewGraphDependencies(
            workflow_store=store,
            review_question=lambda state, question_id: None,
            generate_report=lambda state: {
                "report_ref": "invalid-report",
                "report_sha256": "4" * 64,
            },
            validate_report=lambda state: (
                "failed",
                [
                    {
                        "code": "artifact_hash_mismatch",
                        "description": "invalid report",
                    }
                ],
            ),
            commit_report=lambda state: commit_calls.append(state),
            max_quality_repairs=0,
        ),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        make_durable_review_initial_state(make_job(), make_finished_state()),
        {"configurable": {"thread_id": "review:runtime-gate-failure"}},
    )
    artifacts.fail_job(rescore.job_id, error_code="report_quality_failed")

    assert result["validation_outcome"] == "failed"
    assert commit_calls == []
    assert store.failed[-1][1] == "report_quality_failed"
    assert artifacts.get_head("session-1").active_report_id == first.report_id
    assert len(artifacts.list_artifacts("session-1")) == 1
