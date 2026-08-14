from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.rag.access import (
    require_corpus_write,
    require_eval_artifacts,
    require_live_inspector,
    require_rag_console,
)
from app.api.rag.models import (
    ArtifactCatalogResponse,
    ArtifactDetailResponse,
    CorpusResponse,
    CorpusReleaseRequest,
    CorpusReleaseResponse,
    CorpusValidateRequest,
    CorpusValidateResponse,
    EvalCasesResponse,
    EvidenceTraceResponse,
    NoEvidenceConfusionSummary,
    PairedEvaluationsResponse,
    RagOverviewResponse,
    RetrievalCompareRequest,
    RetrievalInspectionRequest,
    SafeRetrievalCompareResponse,
    SafeRetrievalInspectionResponse,
)
from app.api.shared.dependencies import (
    get_rag_corpus_write_service,
    get_rag_diagnostics_service,
)
from app.application.knowledge.corpus_write_service import (
    CorpusConflictError,
    CorpusWriteUnavailable,
)
from app.application.knowledge.diagnostics_service import (
    DiagnosticCapacityExhausted,
    DiagnosticIdentityConflict,
)


class SafeDiagnosticRoute(APIRoute):
    """Prevent request bodies, including live queries, from entering 422 responses."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "code": "RAG_REQUEST_INVALID",
                        "message": "The diagnostic request did not pass validation.",
                        "retryable": False,
                    },
                )

        return safe_handler


router = APIRouter(
    prefix="/rag",
    tags=["rag-console"],
    route_class=SafeDiagnosticRoute,
)


@router.get("/overview", response_model=RagOverviewResponse)
def rag_overview(
    request: Request,
    _access=Depends(require_rag_console),
    service=Depends(get_rag_diagnostics_service),
):
    return service.overview()


@router.post("/inspections", response_model=SafeRetrievalInspectionResponse)
def run_inspection(
    payload: RetrievalInspectionRequest,
    request: Request,
    _access=Depends(require_live_inspector),
    service=Depends(get_rag_diagnostics_service),
):
    try:
        return service.inspect(payload)
    except DiagnosticCapacityExhausted as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "RAG_DIAGNOSTIC_CAPACITY_EXHAUSTED",
                "message": "Live diagnostic capacity is temporarily exhausted.",
                "retryable": True,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RAG_INSPECTION_REQUEST_REJECTED",
                "message": "The diagnostic request is not allowed.",
                "retryable": False,
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RAG_RETRIEVAL_UNAVAILABLE",
                "message": "Retrieval diagnostics are unavailable.",
                "retryable": True,
            },
        ) from exc


@router.post(
    "/inspections/compare",
    response_model=SafeRetrievalCompareResponse,
)
def compare_inspections(
    payload: RetrievalCompareRequest,
    request: Request,
    _access=Depends(require_live_inspector),
    service=Depends(get_rag_diagnostics_service),
):
    try:
        return service.compare(payload)
    except DiagnosticCapacityExhausted as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "RAG_DIAGNOSTIC_CAPACITY_EXHAUSTED",
                "message": "实时诊断并发已满，请稍后重试。",
                "retryable": True,
            },
        ) from exc
    except DiagnosticIdentityConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RAG_COMPARE_IDENTITY_CONFLICT",
                "message": "两侧检索未使用同一语料版本，本次结果已拒绝。",
                "retryable": True,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RAG_COMPARE_REQUEST_REJECTED",
                "message": "该比较请求不符合诊断约束。",
                "retryable": False,
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RAG_COMPARE_UNAVAILABLE",
                "message": "双引擎比较当前不可用。",
                "retryable": True,
            },
        ) from exc


@router.get("/evaluations", response_model=ArtifactCatalogResponse)
def list_evaluations(
    request: Request,
    _access=Depends(require_eval_artifacts),
    service=Depends(get_rag_diagnostics_service),
):
    return service.evaluations()


@router.get(
    "/evaluations/{artifact_sha256}",
    response_model=ArtifactDetailResponse,
)
def get_evaluation(
    artifact_sha256: str,
    request: Request,
    _access=Depends(require_eval_artifacts),
    service=Depends(get_rag_diagnostics_service),
):
    try:
        return service.evaluation(artifact_sha256)
    except (KeyError, ValueError, OSError):
        raise HTTPException(status_code=404, detail="not found") from None


@router.get("/evaluations-paired", response_model=PairedEvaluationsResponse)
def list_paired_evaluations(
    request: Request,
    _access=Depends(require_eval_artifacts),
    service=Depends(get_rag_diagnostics_service),
):
    return service.paired_evaluations()


@router.get(
    "/evaluations/{artifact_sha256}/cases",
    response_model=EvalCasesResponse,
)
def evaluation_cases(
    artifact_sha256: str,
    request: Request,
    _access=Depends(require_eval_artifacts),
    service=Depends(get_rag_diagnostics_service),
):
    try:
        return service.evaluation_cases(artifact_sha256)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="not found") from None


@router.get(
    "/evaluations/{artifact_sha256}/no-evidence",
    response_model=NoEvidenceConfusionSummary,
)
def evaluation_no_evidence(
    artifact_sha256: str,
    request: Request,
    _access=Depends(require_eval_artifacts),
    service=Depends(get_rag_diagnostics_service),
):
    try:
        return service.no_evidence_summary(artifact_sha256)
    except (KeyError, ValueError, OSError):
        raise HTTPException(status_code=404, detail="not found") from None


@router.get(
    "/evaluations/{artifact_sha256}/cases/{case_id}/diagnostic-snapshot",
    response_model=SafeRetrievalInspectionResponse,
)
def evaluation_snapshot(
    artifact_sha256: str,
    case_id: str,
    request: Request,
    _access=Depends(require_eval_artifacts),
    service=Depends(get_rag_diagnostics_service),
):
    try:
        return service.artifact_replay(artifact_sha256, case_id)
    except (KeyError, ValueError, OSError):
        raise HTTPException(status_code=404, detail="not found") from None


@router.get("/corpus", response_model=CorpusResponse)
def rag_corpus(
    request: Request,
    _access=Depends(require_rag_console),
    service=Depends(get_rag_diagnostics_service),
):
    return service.corpus()


@router.post(
    "/corpus/drafts/validate",
    response_model=CorpusValidateResponse,
)
def validate_corpus_draft(
    payload: CorpusValidateRequest,
    request: Request,
    _access=Depends(require_corpus_write),
    service=Depends(get_rag_corpus_write_service),
):
    try:
        return service.validate(payload.entry)
    except CorpusWriteUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RAG_CORPUS_WRITE_UNAVAILABLE",
                "message": "知识语料写入服务当前不可用。",
                "retryable": True,
            },
        ) from exc


@router.post(
    "/corpus/releases/activate",
    response_model=CorpusReleaseResponse,
)
def activate_corpus_release(
    payload: CorpusReleaseRequest,
    request: Request,
    _access=Depends(require_corpus_write),
    service=Depends(get_rag_corpus_write_service),
):
    try:
        return service.release(payload)
    except CorpusConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RAG_CORPUS_RELEASE_CONFLICT",
                "message": "语料已发生变化，请重新加载并再次预检。",
                "retryable": False,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RAG_CORPUS_RELEASE_REJECTED",
                "message": "资料未通过发布校验，请重新预检。",
                "retryable": False,
            },
        ) from exc
    except (CorpusWriteUnavailable, RuntimeError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RAG_CORPUS_RELEASE_UNAVAILABLE",
                "message": "资料发布未完成，当前语料保持不变。",
                "retryable": True,
            },
        ) from exc


@router.get(
    "/evidence-traces/{trace_id}",
    response_model=EvidenceTraceResponse,
)
def evidence_trace(
    trace_id: str,
    request: Request,
    _access=Depends(require_rag_console),
    service=Depends(get_rag_diagnostics_service),
):
    try:
        return service.evidence_trace(trace_id)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail="not found") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="trace store unavailable") from exc
