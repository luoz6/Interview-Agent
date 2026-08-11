from __future__ import annotations

import ast
from pathlib import Path

from app.ports.runtime import (
    ReportJobLeaseAdapter,
    ReportJobQueue,
    ReportJobRepository,
    ReportOrphanRepair,
    ReportRetryAdapter,
)
from app.services.report_pdf import ReportPdfRenderer
from app.services.report_pipeline import (
    FullSessionEvaluationService,
    MicrobatchEvaluationService,
    QuestionEvaluationService,
    ReportAssembler,
    ReportGenerationPipeline,
    ReportProgressProjector,
    ReportQualityPolicy,
)
from app.services.report_reliability import ReportReliabilityProjector
from app.services.report_rule_score import VersionedReportRubric
from app.services.report_worker import ReportWorker


ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }


def test_report_pipeline_has_one_named_owner_per_stage():
    assert all(
        value is not None
        for value in (
            ReportGenerationPipeline,
            ReportProgressProjector,
            QuestionEvaluationService,
            MicrobatchEvaluationService,
            FullSessionEvaluationService,
            ReportAssembler,
            ReportQualityPolicy,
            ReportReliabilityProjector,
            ReportPdfRenderer,
            ReportWorker,
            VersionedReportRubric,
        )
    )


def test_report_job_queue_is_the_aggregate_of_existing_split_ports():
    assert ReportJobQueue.__mro__[1:5] == (
        ReportJobRepository,
        ReportJobLeaseAdapter,
        ReportRetryAdapter,
        ReportOrphanRepair,
    )


def test_report_task_entrypoint_delegates_pipeline_and_runtime_work_is_removed():
    task_imports = _imports(ROOT / "app" / "services" / "report_tasks.py")
    assert "app.services.report_pipeline" in task_imports
    assert not (ROOT / "app" / "services" / "runtime_work.py").exists()
