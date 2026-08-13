from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }


def test_all_knowledge_domain_modules_are_infrastructure_free():
    for path in (ROOT / "app" / "domain" / "knowledge").glob("*.py"):
        assert not any(
            module.startswith(("app.adapters", "app.application", "app.services"))
            for module in _imports(path)
        ), path


def test_knowledge_application_does_not_import_adapters():
    for path in (ROOT / "app" / "application" / "knowledge").glob("*.py"):
        assert not any(
            module.startswith("app.adapters") for module in _imports(path)
        ), path


def test_pgvector_adapter_does_not_own_v2_business_policy():
    source = (
        ROOT / "app" / "adapters" / "pgvector" / "repository.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "weighted_reciprocal_rank_fusion",
        "RetrievalEvidenceGate",
        "EvaluationSupportGate",
        "ReviewEvidenceBinding",
        "FollowupGapService",
    ):
        assert forbidden not in source


def test_new_knowledge_code_does_not_depend_on_mutable_legacy_trace():
    paths = [
        *(ROOT / "app" / "domain" / "knowledge").glob("*.py"),
        *(ROOT / "app" / "application" / "knowledge").glob("*.py"),
        *(ROOT / "app" / "adapters" / "knowledge").glob("*.py"),
        ROOT / "app" / "ports" / "knowledge.py",
    ]
    assert all("last_search_trace" not in path.read_text(encoding="utf-8") for path in paths)


def test_fusion_has_one_authoritative_implementation():
    definitions = []
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        definitions.extend(
            (path, node.name)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "weighted_reciprocal_rank_fusion"
        )
    assert definitions == [
        (ROOT / "app" / "domain" / "knowledge" / "fusion.py", "weighted_reciprocal_rank_fusion")
    ]


def test_durable_report_generation_reuses_authoritative_feedback_lock():
    source = (ROOT / "app" / "services" / "runtime.py").read_text(encoding="utf-8")

    assert source.count("finalize_report_with_microbatch_feedback(report, records)") == 2


def test_report_pipeline_locks_final_output_to_persisted_question_records():
    source = (ROOT / "app" / "services" / "report_pipeline.py").read_text(
        encoding="utf-8"
    )

    persist_at = source.index("records = self._question_evaluations.persist(")
    lock_at = source.index("report = finalize_report_with_microbatch_feedback(")
    save_at = source.index("store.save_report(session_id, report)")
    assert persist_at < lock_at < save_at


def test_review_evidence_binding_is_persisted_as_full_record_metadata():
    evaluator = (ROOT / "app" / "services" / "evaluator_ext.py").read_text(
        encoding="utf-8"
    )
    record = (ROOT / "app" / "services" / "question_evaluations.py").read_text(
        encoding="utf-8"
    )
    mapper = (
        ROOT
        / "app"
        / "adapters"
        / "postgres"
        / "row_mappers"
        / "question_evaluation.py"
    ).read_text(encoding="utf-8")

    assert '"review_evidence_binding"' in evaluator
    assert "review_evidence_binding: ReviewEvidenceBinding | None" in record
    assert '"review_evidence_binding"' in mapper


def test_report_path_metadata_exposes_targeted_supplementation_without_raw_query():
    from app.services.question_evaluations import QuestionEvaluationRecord
    from app.services.report_pipeline import QuestionEvaluationService

    records = [
        QuestionEvaluationRecord(
            session_id="session-1",
            question_id="q1",
            status="failed",
            error="scoring failed after evidence resolution",
            retrieval_path="bound_evidence_ids",
        ),
        QuestionEvaluationRecord(
            session_id="session-1",
            question_id="q2",
            status="failed",
            error="scoring failed after evidence resolution",
            retrieval_path="bound_evidence_plus_targeted",
        ),
    ]

    assert QuestionEvaluationService.knowledge_path_metadata(records) == {
        "knowledge_path": "bound_evidence_with_targeted_supplementation"
    }


def test_runtime_reason_code_literals_are_registered_in_the_stable_contract():
    from app.domain.knowledge.retrieval import RetrievalReasonCode

    registered = {reason.value for reason in RetrievalReasonCode}
    required = {
        "semantic_timeout",
        "lexical_timeout",
        "semantic_provider_error",
        "lexical_provider_error",
        "semantic_capacity_exhausted",
        "lexical_capacity_exhausted",
        "reranker_timeout",
        "reranker_provider_error",
        "candidate_engine_failed",
        "invalid_knowledge_metadata",
        "corpus_manifest_mismatch",
        "no_relevant_candidate",
        "insufficient_signal_coverage",
        "hard_negative_risk",
        "supplemental_retrieval_required",
        "supplemental_retrieval_unavailable",
        "retrieval_unavailable",
        "evidence_gate_disabled",
        "knowledge_unit_not_reviewed",
        "evidence_task_mismatch",
        "evidence_authority_unverified",
        "evidence_authority_filtered",
    }

    assert required <= registered
