from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore, Lock

from app.application.knowledge.diagnostic_models import (
    ArtifactCatalogResponse,
    ArtifactDetailResponse,
    ArtifactSummary,
    ConsumerActionRecord,
    CorpusResponse,
    CorpusUnitSummary,
    EvalCaseSummary,
    EvalCasesResponse,
    EvidenceTraceResponse,
    EvidenceTraceStage,
    PairedEvaluationSummary,
    PairedEvaluationsResponse,
    RagCapabilitySummary,
    RagOverviewResponse,
    RetrievalCompareRequest,
    RetrievalInspectionRequest,
    SafeCompareSide,
    SafeRankingExplanation,
    SafeInspectionInputs,
    SafeRankChange,
    SafeEvidenceTraceRef,
    SafeRetrievalCandidate,
    SafeRetrievalCompareResponse,
    SafeRetrievalInspectionResponse,
    SafeTopKOverlap,
    NoEvidenceConfusionSummary,
)
from app.application.knowledge.retrieval_profiles import (
    compatibility_profile,
    resolve_runtime_profile,
)
from app.domain.knowledge.retrieval import (
    RetrievalHardConstraints,
    RetrievalRequest,
    RetrievalRoutingHints,
)
from app.runtime.config import (
    load_knowledge_runtime_settings,
    load_rag_console_runtime_settings,
)
from app.services.knowledge_eval_artifacts_v3 import (
    KnowledgeEvalArtifactV3,
    RetrievalDiagnosticSnapshotV1,
    load_eval_artifact_v3,
    load_retrieval_diagnostic_snapshot_v1,
)
from app.services.knowledge_eval_dataset_v3 import load_knowledge_retrieval_dataset_v3


ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "app" / "data" / "knowledge_v2" / "manifest.json"
ARTIFACT_ROOTS = (ROOT / "eval" / "knowledge-v3" / "machine-preannotation",)
SNAPSHOT_ROOT = ROOT / "eval" / "knowledge-v3" / "diagnostic-snapshots"
LIVE_INSPECTION_MAX_CONCURRENCY = 2
COMPARE_TIMEOUT_SECONDS = 10.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DiagnosticCapacityExhausted(RuntimeError):
    """Raised before retrieval when the bounded live-diagnostic lane is full."""


class DiagnosticIdentityConflict(RuntimeError):
    """Raised when two successful compare sides did not use one corpus identity."""


class DiagnosticCapacityGuard:
    """Small process-local guard around live diagnostics, not retrieval channels."""

    def __init__(self, max_concurrency: int = LIVE_INSPECTION_MAX_CONCURRENCY) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self._semaphore = BoundedSemaphore(max_concurrency)

    def acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        self._semaphore.release()


_LIVE_INSPECTION_CAPACITY = DiagnosticCapacityGuard()


class RagArtifactCatalog:
    """Read-only, path-free adapter over explicitly allowlisted roots."""

    def __init__(
        self,
        roots: tuple[Path, ...] = ARTIFACT_ROOTS,
        snapshot_root: Path = SNAPSHOT_ROOT,
        historical_holdout_roots: tuple[Path, ...] = ARTIFACT_ROOTS,
    ) -> None:
        self._roots = tuple(path.resolve() for path in roots)
        self._snapshot_root = snapshot_root.resolve()
        self._historical_holdout_roots = frozenset(
            path.resolve() for path in historical_holdout_roots
        )

    def list(self) -> ArtifactCatalogResponse:
        artifacts = []
        for path in self._artifact_paths():
            try:
                artifact = load_eval_artifact_v3(path)
            except (ValueError, OSError):
                continue
            if artifact.split == "holdout" and not self._is_historical_holdout_path(path):
                continue
            artifacts.append(self._summary(artifact))
        return ArtifactCatalogResponse(
            artifacts=tuple(sorted(artifacts, key=lambda item: item.created_at, reverse=True))
        )

    def load(self, artifact_sha256: str) -> KnowledgeEvalArtifactV3:
        for path in self._artifact_paths():
            try:
                artifact = load_eval_artifact_v3(path)
            except (ValueError, OSError):
                continue
            if (
                artifact.artifact_sha256 == artifact_sha256
                and (
                    artifact.split != "holdout"
                    or self._is_historical_holdout_path(path)
                )
            ):
                return artifact
        raise KeyError("artifact not found")

    def cases(self, artifact_sha256: str) -> EvalCasesResponse:
        artifact = self.load(artifact_sha256)
        dataset = self.dataset_for(artifact)
        dataset_cases = {item.case_id: item for item in dataset.cases}
        cases = tuple(
            EvalCaseSummary(
                case_id=case.case_id,
                case_type=dataset_cases[case.case_id].case_type,
                evaluation_group=dataset_cases[case.case_id].evaluation_group,
                primary_relevant_chunk_ids=dataset_cases[case.case_id].primary_relevant_chunk_ids,
                accepted_related_chunk_ids=dataset_cases[case.case_id].accepted_related_chunk_ids,
                excluded_chunk_ids=dataset_cases[case.case_id].excluded_chunk_ids,
                expected_no_evidence=dataset_cases[case.case_id].expected_no_evidence,
                availability=case.availability.value,
                selected_evidence_ids=case.selected_evidence_ids,
                declared_no_evidence=case.declared_no_evidence,
                latency_ms=case.latency_ms,
                reason_codes=case.reason_codes,
                diagnostic_fidelity=(
                    "full_snapshot"
                    if self.has_valid_snapshot(artifact_sha256, case.case_id)
                    else "partial_historical"
                ),
                diagnostic_snapshot_ref=(
                    case.case_id
                    if self.has_valid_snapshot(artifact_sha256, case.case_id)
                    else None
                ),
            )
            for case in artifact.cases
            if case.case_id in dataset_cases
        )
        return EvalCasesResponse(artifact_sha256=artifact_sha256, cases=cases)

    def paired(self) -> PairedEvaluationsResponse:
        from app.services.knowledge_eval_artifacts_v3 import KnowledgeEvalPairedArtifactV3

        comparisons = []
        for path in self._json_paths():
            try:
                value = KnowledgeEvalPairedArtifactV3.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if value.split == "holdout" and not self._is_historical_holdout_path(path):
                    continue
                baseline = self.load(value.baseline_artifact_sha256)
                candidate = self.load(value.candidate_artifact_sha256)
            except (ValueError, OSError, KeyError):
                continue
            comparisons.append(
                PairedEvaluationSummary(
                    artifact_sha256=value.artifact_sha256,
                    dataset_version=value.dataset_version,
                    split=value.split,
                    baseline_artifact_sha256=value.baseline_artifact_sha256,
                    candidate_artifact_sha256=value.candidate_artifact_sha256,
                    baseline_engine_version=baseline.identity.engine_version,
                    candidate_engine_version=candidate.identity.engine_version,
                    metrics=tuple(
                        item.model_dump(mode="json") for item in value.comparison.metrics
                    ),
                    case_type_deltas=value.comparison.case_type_deltas,
                )
            )
        return PairedEvaluationsResponse(comparisons=tuple(comparisons))

    def detail(self, artifact_sha256: str) -> ArtifactDetailResponse:
        artifact = self.load(artifact_sha256)
        comparisons = tuple(
            item
            for item in self.paired().comparisons
            if artifact_sha256
            in {item.baseline_artifact_sha256, item.candidate_artifact_sha256}
        )
        return ArtifactDetailResponse(
            artifact=self._summary(artifact),
            paired_comparisons=comparisons,
        )

    def snapshot(
        self, artifact_sha256: str, case_id: str
    ) -> RetrievalDiagnosticSnapshotV1:
        artifact = self.load(artifact_sha256)
        if case_id not in {item.case_id for item in artifact.cases}:
            raise KeyError("case not found")
        path = self.snapshot_path(artifact_sha256, case_id)
        snapshot = load_retrieval_diagnostic_snapshot_v1(path)
        if snapshot.artifact_sha256 != artifact_sha256 or snapshot.case_id != case_id:
            raise ValueError("snapshot identity mismatch")
        return snapshot

    def snapshot_path(self, artifact_sha256: str, case_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", artifact_sha256) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", case_id
        ):
            raise ValueError("invalid artifact reference")
        return self._snapshot_root / artifact_sha256 / f"{case_id}.json"

    def has_valid_snapshot(self, artifact_sha256: str, case_id: str) -> bool:
        try:
            snapshot = load_retrieval_diagnostic_snapshot_v1(
                self.snapshot_path(artifact_sha256, case_id)
            )
        except (ValueError, OSError):
            return False
        return snapshot.artifact_sha256 == artifact_sha256 and snapshot.case_id == case_id

    def _artifact_paths(self):
        for path in self._json_paths():
            yield path

    def _is_historical_holdout_path(self, path: Path) -> bool:
        return path.resolve().parent in self._historical_holdout_roots

    def _json_paths(self):
        for root in self._roots:
            if not root.is_dir():
                continue
            for path in root.glob("*.json"):
                resolved = path.resolve()
                if resolved.parent == root:
                    yield resolved

    def _summary(self, artifact: KnowledgeEvalArtifactV3) -> ArtifactSummary:
        has_snapshots = all(
            self.has_valid_snapshot(artifact.artifact_sha256, case.case_id)
            for case in artifact.cases
        ) and bool(artifact.cases)
        return ArtifactSummary(
            artifact_sha256=artifact.artifact_sha256,
            schema_version=artifact.schema_version,
            dataset_version=artifact.dataset_version,
            split=artifact.split,
            engine_version=artifact.identity.engine_version,
            created_at=artifact.created_at,
            case_count=artifact.metrics.case_count,
            benchmark_type="demo_diagnostic_dataset",
            label_source="curated_machine_assisted",
            purpose="engineering_comparison",
            diagnostic_status=(
                "historical_compatible" if artifact.split == "holdout" else "current"
            ),
            corpus_manifest_sha256=artifact.identity.corpus_manifest_sha256,
            embedding_provider=artifact.identity.provider_name,
            embedding_model=artifact.identity.model_name,
            embedding_revision=artifact.identity.model_revision,
            embedding_dimension=artifact.identity.embedding_dimension,
            code_revision=artifact.identity.code_revision,
            code_tree_sha256=artifact.identity.code_tree_sha256,
            profile_id=artifact.identity.profile_id,
            profile_version=artifact.identity.profile_version,
            profile_sha256=artifact.identity.profile_sha256,
            diagnostic_fidelity=("full_snapshot" if has_snapshots else "partial_historical"),
            metrics=artifact.metrics.model_dump(mode="json"),
        )

    def dataset_for(self, artifact: KnowledgeEvalArtifactV3):
        """Return the validated Dataset paired with an allowlisted Artifact."""

        for root in self._roots:
            path = root / "dataset.json"
            if not path.is_file():
                continue
            try:
                dataset = load_knowledge_retrieval_dataset_v3(
                    path,
                    manifest=_manifest(),
                    require_diagnostic_integrity=False,
                )
            except (ValueError, OSError):
                continue
            if dataset.version == artifact.dataset_version:
                return dataset
        raise KeyError("artifact dataset not found")


class RagDiagnosticsService:
    def __init__(
        self,
        repository=None,
        catalog: RagArtifactCatalog | None = None,
        session_store=None,
        capacity_guard: DiagnosticCapacityGuard | None = None,
        compare_timeout_seconds: float = COMPARE_TIMEOUT_SECONDS,
    ) -> None:
        if compare_timeout_seconds <= 0:
            raise ValueError("compare_timeout_seconds must be positive")
        self._repository = repository
        self._catalog = catalog or RagArtifactCatalog()
        self._session_store = session_store
        self._capacity_guard = capacity_guard or _LIVE_INSPECTION_CAPACITY
        self._compare_timeout_seconds = compare_timeout_seconds

    def overview(self) -> RagOverviewResponse:
        settings = load_knowledge_runtime_settings()
        console = load_rag_console_runtime_settings()
        manifest = _manifest()
        profiles = tuple(
            resolve_runtime_profile(intent, settings).model_dump(mode="json")
            for intent in (
                _intent("prep"),
                _intent("followup"),
                _intent("question_review"),
                _intent("report_repair"),
            )
        )
        artifacts = self._catalog.list().artifacts
        tuning_case_count = max(
            (item.case_count for item in artifacts if item.split == "tuning"),
            default=0,
        )
        diagnostic_case_count = max(
            (item.case_count for item in artifacts if item.split == "holdout"),
            default=0,
        )
        return RagOverviewResponse(
            generated_at=_utc_now(),
            project_scope="learning_project_technical_showcase",
            current_engine=settings.engine,
            comparison_engines=("legacy", "hybrid-v2"),
            remote_reranker_enabled=settings.remote_reranker_enabled,
            evidence_gate_enabled=settings.evidence_gate_enabled,
            corpus={
                "version": str(manifest.get("corpus_version", "unknown")),
                "manifest_sha256": str(manifest.get("corpus_manifest_sha256", "")),
                "chunk_count": int(manifest.get("chunk_count", 0)),
            },
            embedding=_embedding_identity(self._catalog),
            profiles=profiles,
            component_versions={
                "retrieval": settings.retrieval_engine_version,
                "fusion": settings.fusion_version,
                "reranker": settings.reranker_version,
                "evidence_gate": settings.evidence_gate_version,
                "taxonomy": settings.taxonomy_version,
            },
            capabilities=RagCapabilitySummary(
                diagnostic_ui=console.diagnostic_ui_enabled,
                live_inspector=console.live_inspector_enabled,
                eval_artifacts=console.eval_artifact_access_enabled,
                authored_eval_queries=console.authored_eval_query_access_enabled,
                corpus_write=console.corpus_write_enabled,
            ),
            technologies=(
                "Semantic Retrieval",
                "Lexical Retrieval",
                "Weighted RRF Fusion",
                "Deterministic Rerank",
                "Evidence Gate",
                "Evidence Binding / Replay",
                "Versioned Corpus",
                "Privacy-safe Diagnostics",
            ),
            diagnostic_dataset={
                "label": "Demo Diagnostic Dataset",
                "curation": "Curated / Machine-assisted",
                "tuning_case_count": tuning_case_count,
                "diagnostic_case_count": diagnostic_case_count,
                "human_annotator_count": 0,
                "production_claim": False,
            },
            experiment_findings=(
                "现有机器辅助诊断制品不能证明 Hybrid 已整体优于 Legacy。",
                "No-evidence 仍是当前最明确的算法缺口。",
                "请在检索诊断中按候选与流水线阶段解释差异。",
            ),
            demo_boundaries=(
                "仅用于本地学习项目与技术展示。",
                "不包含生产 Shadow、Canary、Promotion 或 Legacy 退役流程。",
                "实时问题不写入 URL，安全响应不回显问题原文。",
            ),
        )

    def inspect(self, payload: RetrievalInspectionRequest) -> SafeRetrievalInspectionResponse:
        if self._repository is None:
            raise RuntimeError("knowledge repository unavailable")
        settings = load_knowledge_runtime_settings()
        profile = resolve_runtime_profile(payload.intent, settings)
        if payload.profile_id not in {profile.profile_id, f"{profile.profile_id}@{profile.profile_version}"}:
            raise ValueError("profile_id is not allowed for this intent")
        request = RetrievalRequest(
            query_text=payload.query_text,
            intent=payload.intent,
            profile_id=profile.profile_id,
            hard_constraints=RetrievalHardConstraints(source_types=payload.source_types),
            routing_hints=RetrievalRoutingHints(
                domains=payload.domains,
                topics=payload.topics,
                canonical_tags=payload.canonical_tags,
            ),
        )
        active_profile = (
            compatibility_profile(
                minimum_score=settings.minimum_score,
                evidence_limit=profile.evidence_limit,
            )
            if payload.engine == "legacy"
            else profile
        )
        inspect_retrieval = getattr(self._repository, "inspect_retrieval", None)
        if not callable(inspect_retrieval):
            raise RuntimeError("knowledge diagnostic retrieval unavailable")
        if not self._capacity_guard.acquire():
            raise DiagnosticCapacityExhausted("live diagnostic capacity exhausted")
        try:
            result = inspect_retrieval(
                request,
                profile=active_profile,
                engine=payload.engine,
            )
        finally:
            self._capacity_guard.release()
        return _inspection_response(
            result,
            mode=payload.mode,
            diagnostic_fidelity="live",
            inspection_inputs=SafeInspectionInputs(
                intent=payload.intent.value,
                requested_domains=payload.domains,
                requested_topics=payload.topics,
                canonical_tags=payload.canonical_tags,
                source_types=payload.source_types,
            ),
        )

    def compare(
        self,
        payload: RetrievalCompareRequest,
    ) -> SafeRetrievalCompareResponse:
        """Compare Legacy and Hybrid once without mutating runtime or persisting traces."""

        if self._repository is None:
            raise RuntimeError("knowledge repository unavailable")
        settings = load_knowledge_runtime_settings()
        profile = resolve_runtime_profile(payload.intent, settings)
        if payload.profile_id not in {
            profile.profile_id,
            f"{profile.profile_id}@{profile.profile_version}",
        }:
            raise ValueError("profile_id is not allowed for this intent")
        request = RetrievalRequest(
            query_text=payload.query_text,
            intent=payload.intent,
            profile_id=profile.profile_id,
            hard_constraints=RetrievalHardConstraints(
                source_types=payload.source_types
            ),
            routing_hints=RetrievalRoutingHints(
                domains=payload.domains,
                topics=payload.topics,
                canonical_tags=payload.canonical_tags,
            ),
        )
        inspection_inputs = SafeInspectionInputs(
            intent=payload.intent.value,
            requested_domains=payload.domains,
            requested_topics=payload.topics,
            canonical_tags=payload.canonical_tags,
            source_types=payload.source_types,
        )
        inspect_retrieval = getattr(self._repository, "inspect_retrieval", None)
        if not callable(inspect_retrieval):
            raise RuntimeError("knowledge diagnostic retrieval unavailable")
        if not self._capacity_guard.acquire():
            raise DiagnosticCapacityExhausted("live diagnostic capacity exhausted")

        profiles = {
            "legacy": compatibility_profile(
                minimum_score=settings.minimum_score,
                evidence_limit=profile.evidence_limit,
            ),
            "hybrid": profile,
        }
        executor = None
        futures = {}
        pending_futures = set()
        try:
            executor = ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="rag-compare",
            )
            for side, side_profile in profiles.items():
                future = executor.submit(
                    inspect_retrieval,
                    request,
                    profile=side_profile,
                    engine=("legacy" if side == "legacy" else "hybrid-v2"),
                )
                futures[side] = future
                pending_futures.add(future)
            done, pending = wait(
                futures.values(),
                timeout=self._compare_timeout_seconds,
            )
            pending_futures = set(pending)
            for future in pending:
                future.cancel()
            sides = {
                side: self._compare_side(
                    future,
                    completed=future in done,
                    inspection_inputs=inspection_inputs,
                )
                for side, future in futures.items()
            }
        finally:
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            if pending_futures:
                _release_capacity_when_complete(
                    pending_futures,
                    self._capacity_guard,
                )
            else:
                self._capacity_guard.release()

        legacy = sides["legacy"]
        hybrid = sides["hybrid"]
        legacy_inspection = legacy.inspection
        hybrid_inspection = hybrid.inspection
        corpus_manifest_sha256 = _compare_corpus_identity(
            legacy_inspection,
            hybrid_inspection,
        )
        diff = _compare_inspections(legacy_inspection, hybrid_inspection)
        return SafeRetrievalCompareResponse(
            created_at=_utc_now(),
            request_id=request.request_id,
            requested_profile_id=profile.profile_id,
            corpus_manifest_sha256=corpus_manifest_sha256,
            legacy=legacy,
            hybrid=hybrid,
            **diff,
        )

    @staticmethod
    def _compare_side(
        future,
        *,
        completed: bool,
        inspection_inputs: SafeInspectionInputs,
    ) -> SafeCompareSide:
        if not completed:
            return SafeCompareSide(
                status="timeout",
                failure_code="retrieval_timeout",
            )
        try:
            result = future.result()
        except Exception:
            return SafeCompareSide(
                status="failed",
                failure_code="retrieval_failed",
            )
        return SafeCompareSide(
            status="success",
            inspection=_inspection_response(
                result,
                mode="live",
                diagnostic_fidelity="live",
                inspection_inputs=inspection_inputs,
            ),
        )

    def artifact_replay(
        self, artifact_sha256: str, case_id: str
    ) -> SafeRetrievalInspectionResponse:
        try:
            snapshot = self._catalog.snapshot(artifact_sha256, case_id)
        except FileNotFoundError:
            return self._partial_replay(artifact_sha256, case_id)
        return _snapshot_response(snapshot)

    def _partial_replay(self, artifact_sha256: str, case_id: str):
        artifact = self._catalog.load(artifact_sha256)
        case = next((item for item in artifact.cases if item.case_id == case_id), None)
        if case is None:
            raise KeyError("case not found")
        candidates = tuple(
            SafeRetrievalCandidate(
                candidate_id=item.chunk_id,
                title=item.chunk_id,
                safe_excerpt="Not recorded by this artifact schema",
                domain="not_recorded",
                topic="",
                tags=(),
                source_type="not_recorded",
                authority_status="not_recorded",
                content_sha256="",
                corpus_manifest_sha256=artifact.identity.corpus_manifest_sha256,
                semantic_rank=None,
                semantic_score=None,
                lexical_rank=None,
                lexical_score=None,
                fusion_rank=None,
                fusion_score=None,
                rerank_rank=item.rank,
                rerank_score=item.score,
                channel_hits=item.channels,
                matched_terms=(),
                ranking_explanation=None,
                selected=item.chunk_id in case.selected_evidence_ids,
            )
            for item in case.candidates
        )
        return SafeRetrievalInspectionResponse(
            request_id=f"artifact:{artifact_sha256}:{case_id}",
            mode="artifact_replay",
            created_at=artifact.created_at,
            diagnostic_fidelity="partial_historical",
            engine=artifact.identity.engine_version,
            profile_id=artifact.identity.profile_id,
            profile_version=artifact.identity.profile_version,
            trace_schema_version="not_recorded",
            inspection_inputs=SafeInspectionInputs(),
            query_facts={"status": "not_recorded"},
            resolved_profile={},
            routing_summary={},
            channel_summary=(),
            candidates=candidates,
            evidence_decision=None,
            consumer_action=ConsumerActionRecord(),
            latency_ms={"total": case.latency_ms},
            degraded_reasons=case.reason_codes,
            component_versions={},
            provider_call_possible=False,
            artifact_identity=_artifact_identity(artifact),
            artifact_sha256=artifact_sha256,
            case_id=case_id,
        )

    def evaluations(self):
        return self._catalog.list()

    def evaluation(self, artifact_sha256: str):
        return self._catalog.detail(artifact_sha256)

    def paired_evaluations(self):
        return self._catalog.paired()

    def no_evidence_summary(self, artifact_sha256: str):
        artifact = self._catalog.load(artifact_sha256)
        dataset = self._catalog.dataset_for(artifact)
        cases = {item.case_id: item for item in dataset.cases if item.split == artifact.split}
        correct_evidence = false_abstention = false_evidence = correct_abstention = 0
        for result in artifact.cases:
            case = cases.get(result.case_id)
            if case is None:
                continue
            actual_no_evidence = bool(case.expected_no_evidence)
            abstained = bool(result.declared_no_evidence)
            if not actual_no_evidence and not abstained:
                correct_evidence += 1
            elif not actual_no_evidence and abstained:
                false_abstention += 1
            elif actual_no_evidence and not abstained:
                false_evidence += 1
            else:
                correct_abstention += 1
        total = correct_evidence + false_abstention + false_evidence + correct_abstention
        expected_no_evidence = false_evidence + correct_abstention
        abstentions = false_abstention + correct_abstention
        precision = correct_abstention / abstentions if abstentions else 0.0
        recall = correct_abstention / expected_no_evidence if expected_no_evidence else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return NoEvidenceConfusionSummary(
            correct_evidence=correct_evidence,
            false_abstention=false_abstention,
            false_evidence=false_evidence,
            correct_abstention=correct_abstention,
            total_case_count=total,
            expected_no_evidence_count=expected_no_evidence,
            abstention_count=abstentions,
            no_evidence_prevalence=(expected_no_evidence / total if total else 0.0),
            abstention_rate=(abstentions / total if total else 0.0),
            precision=precision,
            recall=recall,
            f1=f1,
        )

    def evidence_trace(self, trace_id: str) -> EvidenceTraceResponse:
        if self._session_store is None:
            raise RuntimeError("evidence trace store unavailable")
        state = self._session_store.get(trace_id)
        if state.get("deletion_status") == "deleting":
            raise KeyError("trace not found")
        plan = state.get("plan")
        prep_context = getattr(plan, "prep_context", None)
        binding_snapshot = getattr(prep_context, "binding_snapshot", None)
        stages: list[EvidenceTraceStage] = []
        bundle = getattr(binding_snapshot, "base_evidence_bundle", None)
        safe_refs = {
            item.evidence_id: _safe_trace_ref(item)
            for item in getattr(bundle, "candidate_evidence_refs", ()) or ()
        }
        if bundle is not None:
            stages.append(
                EvidenceTraceStage(
                    stage="base_evidence_bundle",
                    recording_status="recorded",
                    record_id=bundle.bundle_id,
                    evidence_ids=tuple(
                        item.evidence_id for item in bundle.candidate_evidence_refs
                    ),
                    evidence_refs=tuple(safe_refs.values()),
                    corpus_manifest_sha256=bundle.corpus_manifest_sha256,
                    created_at=bundle.created_at,
                    note="Frozen retrieval candidate lineage.",
                )
            )
        else:
            stages.append(_not_recorded_stage("base_evidence_bundle"))
        question_bindings = tuple(
            getattr(binding_snapshot, "question_evidence_bindings", ()) or ()
        )
        if question_bindings:
            stages.extend(
                EvidenceTraceStage(
                    stage="question_evidence_binding",
                    recording_status="recorded",
                    record_id=item.binding_id,
                    parent_record_id=item.bundle_id,
                    evidence_ids=item.selected_evidence_ids,
                    evidence_refs=tuple(
                        safe_refs[evidence_id]
                        for evidence_id in item.selected_evidence_ids
                        if evidence_id in safe_refs
                    ),
                    decision=item.decision,
                    created_at=item.created_at,
                    note=f"Question binding {item.question_id}",
                )
                for item in question_bindings
            )
        else:
            stages.append(_not_recorded_stage("question_evidence_binding"))
        records = tuple(self._session_store.list_question_evaluations(trace_id))
        review_bindings = tuple(
            record.review_evidence_binding
            for record in records
            if record.review_evidence_binding is not None
        )
        if review_bindings:
            for item in review_bindings:
                supplemental_refs = {
                    reference.evidence_id: _safe_trace_ref(reference)
                    for reference in item.supplemental_evidence_refs
                }
                stage_refs = {**safe_refs, **supplemental_refs}
                stages.append(
                    EvidenceTraceStage(
                        stage="review_evidence_binding",
                        recording_status="recorded",
                        record_id=item.binding_id,
                        parent_record_id=item.parent_question_binding_id,
                        evidence_ids=item.final_evidence_ids,
                        evidence_refs=tuple(
                            stage_refs[evidence_id]
                            for evidence_id in item.final_evidence_ids
                            if evidence_id in stage_refs
                        ),
                        decision=item.decision,
                        created_at=item.created_at,
                        note="Persisted reviewer evidence selection.",
                    )
                )
                stages.append(
                    EvidenceTraceStage(
                        stage="reviewer_decision",
                        recording_status="recorded",
                        record_id=f"decision:{item.binding_id}",
                        parent_record_id=item.binding_id,
                        evidence_ids=item.final_evidence_ids,
                        evidence_refs=tuple(
                            stage_refs[evidence_id]
                            for evidence_id in item.final_evidence_ids
                            if evidence_id in stage_refs
                        ),
                        decision=item.decision,
                        created_at=item.created_at,
                        note="Decision projected from the persisted review binding.",
                    )
                )
        else:
            stages.append(_not_recorded_stage("review_evidence_binding"))
            stages.append(_not_recorded_stage("reviewer_decision"))
        stages.append(_not_recorded_stage("followup_decision"))
        return EvidenceTraceResponse(
            trace_id=trace_id,
            generated_at=_utc_now(),
            stages=tuple(stages),
        )

    def evaluation_cases(self, artifact_sha256: str):
        return self._catalog.cases(artifact_sha256)

    def corpus(self) -> CorpusResponse:
        console = load_rag_console_runtime_settings()
        catalog_getter = getattr(self._repository, "get_corpus_catalog", None)
        if callable(catalog_getter):
            try:
                catalog = catalog_getter()
            except RuntimeError:
                catalog = {}
            if catalog:
                units = tuple(
                    _catalog_unit(item, catalog["corpus_version"])
                    for item in catalog.get("units", ())
                )
                return CorpusResponse(
                    corpus_version=catalog["corpus_version"],
                    manifest_sha256=catalog["manifest_sha256"],
                    chunk_count=int(catalog["chunk_count"]),
                    embedding=catalog["embedding"],
                    activation_status="active",
                    retired_versions=tuple(catalog.get("retired_versions", ())),
                    write_enabled=console.corpus_write_enabled,
                    units=units,
                )
        manifest = _manifest()
        units = tuple(
            CorpusUnitSummary(
                unit_id=str(item["chunk_id"]),
                title=str(item["title"]),
                domain=str(item["domain"]),
                topic=str(item.get("topic") or ""),
                source_type=str(item["source_type"]),
                tags=tuple(item.get("tags") or ()),
                aliases=tuple(item.get("aliases") or ()),
                source_authority="not_recorded",
                review_status="not_recorded",
                version=str(manifest.get("corpus_version", "unknown")),
                retirement_status="not_recorded",
                embedding_status="not_recorded",
                content_sha256=str(item.get("content_sha256") or ""),
            )
            for item in manifest.get("chunks", ())
        )
        return CorpusResponse(
            corpus_version=str(manifest.get("corpus_version", "unknown")),
            manifest_sha256=str(manifest.get("corpus_manifest_sha256", "")),
            chunk_count=len(units),
            embedding=_embedding_identity(self._catalog),
            activation_status="not_recorded",
            retired_versions=(),
            write_enabled=console.corpus_write_enabled,
            units=units,
        )




def _catalog_unit(item: dict, corpus_version: str) -> CorpusUnitSummary:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    authority = metadata.get("authority_metadata")
    authority_status = (
        str(authority.get("status", "not_recorded"))
        if isinstance(authority, dict)
        else "not_recorded"
    )
    return CorpusUnitSummary(
        unit_id=str(item.get("unit_id", "")),
        title=str(item.get("title", "")),
        domain=str(item.get("domain", "")),
        topic=str(metadata.get("topic", "")),
        source_type=str(item.get("source_type", "")),
        tags=tuple(item.get("tags") or ()),
        aliases=tuple(metadata.get("aliases") or ()),
        source_authority=authority_status,
        review_status=str(metadata.get("review_status", "not_recorded")),
        version=corpus_version,
        retirement_status="active",
        embedding_status="active",
        content_sha256=str(item.get("content_sha256", "")),
    )


def _not_recorded_stage(stage: str) -> EvidenceTraceStage:
    return EvidenceTraceStage(
        stage=stage,
        recording_status="not_recorded",
        note="No persisted record is available; no value was inferred.",
    )


def _safe_trace_ref(reference) -> SafeEvidenceTraceRef:
    return SafeEvidenceTraceRef(
        evidence_id=reference.evidence_id,
        title=reference.title,
        domain=reference.domain,
        topic=reference.topic,
        source_type=reference.source_type,
        content_sha256=reference.content_sha256,
        corpus_manifest_sha256=reference.corpus_manifest_sha256,
    )


def _inspection_response(
    result,
    *,
    mode,
    diagnostic_fidelity,
    inspection_inputs: SafeInspectionInputs,
):
    selected = {item.chunk_id for item in result.selected_evidence}
    trace = result.trace
    return SafeRetrievalInspectionResponse(
        request_id=result.request_id,
        mode=mode,
        created_at=_utc_now(),
        diagnostic_fidelity=diagnostic_fidelity,
        engine=result.retrieval_engine_version,
        profile_id=trace.profile_id,
        profile_version=result.profile_version,
        trace_schema_version=trace.trace_schema_version,
        inspection_inputs=inspection_inputs,
        query_facts=(
            trace.sanitized_query_facts.model_dump(mode="json")
            if trace.sanitized_query_facts
            else {}
        ),
        resolved_profile=(trace.resolved_profile.model_dump(mode="json") if trace.resolved_profile else {}),
        routing_summary=(trace.routing_hints.model_dump(mode="json") if trace.routing_hints else {}),
        channel_summary=tuple(item.model_dump(mode="json") for item in trace.channels),
        candidates=tuple(_safe_candidate(item, selected) for item in result.candidates),
        evidence_decision=result.evidence_decision,
        consumer_action=ConsumerActionRecord(),
        latency_ms=dict(trace.latency_breakdown_ms),
        degraded_reasons=tuple(result.degraded_reasons),
        component_versions=(trace.component_versions.model_dump(mode="json") if trace.component_versions else {}),
        provider_call_possible=True,
        artifact_identity={},
    )


def _safe_candidate(item, selected):
    metadata = item.chunk.metadata
    authority = metadata.get("authority_metadata") or {}
    authority_status = str(authority.get("review_status") or metadata.get("review_status") or "not_recorded")
    excerpt = safe_diagnostic_excerpt(metadata.get("candidate_summary"))
    if not excerpt:
        excerpt = "Safe excerpt not recorded for this knowledge unit."
    return SafeRetrievalCandidate(
        candidate_id=item.chunk_id,
        title=item.chunk.title,
        safe_excerpt=excerpt,
        domain=item.chunk.domain,
        topic=str(metadata.get("topic") or ""),
        tags=tuple(item.chunk.tags),
        source_type=item.chunk.source_type,
        authority_status=authority_status,
        content_sha256=str(metadata.get("content_sha256") or ""),
        corpus_manifest_sha256=str(metadata.get("corpus_manifest_sha256") or ""),
        semantic_rank=item.semantic_rank,
        semantic_score=item.semantic_score,
        lexical_rank=item.lexical_rank,
        lexical_score=item.lexical_score,
        fusion_rank=item.fusion_rank,
        fusion_score=item.fusion_score,
        rerank_rank=item.rerank_rank,
        rerank_score=item.rerank_score,
        channel_hits=tuple(item.channel_hits),
        matched_terms=tuple(item.matched_terms),
        ranking_explanation=(
            SafeRankingExplanation.model_validate(item.ranking_explanation.model_dump())
            if item.ranking_explanation
            else None
        ),
        selected=item.chunk_id in selected,
    )


def _compare_corpus_identity(
    legacy: SafeRetrievalInspectionResponse | None,
    hybrid: SafeRetrievalInspectionResponse | None,
) -> str | None:
    identities: list[str] = []
    for inspection in (legacy, hybrid):
        if inspection is None:
            continue
        side_identities = {
            str(value)
            for value in (
                inspection.component_versions.get("corpus_manifest_sha256"),
                *(item.corpus_manifest_sha256 for item in inspection.candidates),
            )
            if value
        }
        if len(side_identities) > 1:
            raise DiagnosticIdentityConflict("one compare side used mixed corpus identities")
        identities.extend(side_identities)
    unique = set(identities)
    if len(unique) > 1:
        raise DiagnosticIdentityConflict("compare sides used different corpus identities")
    return next(iter(unique), None)


def _release_capacity_when_complete(futures, guard: DiagnosticCapacityGuard) -> None:
    """Retain capacity for timed-out work until every provider thread exits."""

    lock = Lock()
    remaining = {"count": len(futures)}

    def release_one(_future) -> None:
        should_release = False
        with lock:
            remaining["count"] -= 1
            should_release = remaining["count"] == 0
        if should_release:
            guard.release()

    for future in futures:
        future.add_done_callback(release_one)


def _compare_inspections(
    legacy: SafeRetrievalInspectionResponse | None,
    hybrid: SafeRetrievalInspectionResponse | None,
) -> dict:
    if legacy is None or hybrid is None:
        return {
            "top_k_overlap": None,
            "rank_changes": (),
            "selected_evidence_changed": None,
            "evidence_decision_changed": None,
            "latency_delta_ms": None,
        }

    k = 5
    legacy_ids = [item.candidate_id for item in legacy.candidates[:k]]
    hybrid_ids = [item.candidate_id for item in hybrid.candidates[:k]]
    hybrid_id_set = set(hybrid_ids)
    overlap_ids = tuple(item for item in legacy_ids if item in hybrid_id_set)

    legacy_rank = {
        item.candidate_id: index
        for index, item in enumerate(legacy.candidates, start=1)
    }
    hybrid_rank = {
        item.candidate_id: index
        for index, item in enumerate(hybrid.candidates, start=1)
    }
    legacy_selected = {
        item.candidate_id for item in legacy.candidates if item.selected
    }
    hybrid_selected = {
        item.candidate_id for item in hybrid.candidates if item.selected
    }
    ordered_ids = tuple(dict.fromkeys((*legacy_rank, *hybrid_rank)))
    rank_changes = tuple(
        SafeRankChange(
            candidate_id=candidate_id,
            legacy_rank=legacy_rank.get(candidate_id),
            hybrid_rank=hybrid_rank.get(candidate_id),
            rank_delta=(
                legacy_rank[candidate_id] - hybrid_rank[candidate_id]
                if candidate_id in legacy_rank and candidate_id in hybrid_rank
                else None
            ),
            legacy_selected=candidate_id in legacy_selected,
            hybrid_selected=candidate_id in hybrid_selected,
        )
        for candidate_id in ordered_ids
    )

    legacy_decision = (
        legacy.evidence_decision.model_dump(mode="json")
        if legacy.evidence_decision is not None
        else None
    )
    hybrid_decision = (
        hybrid.evidence_decision.model_dump(mode="json")
        if hybrid.evidence_decision is not None
        else None
    )
    legacy_latency = legacy.latency_ms.get("total")
    hybrid_latency = hybrid.latency_ms.get("total")
    latency_delta = (
        round(float(hybrid_latency) - float(legacy_latency), 4)
        if legacy_latency is not None and hybrid_latency is not None
        else None
    )
    return {
        "top_k_overlap": SafeTopKOverlap(
            k=k,
            overlap_count=len(overlap_ids),
            overlap_ratio=len(overlap_ids) / k,
            candidate_ids=overlap_ids,
        ),
        "rank_changes": rank_changes,
        "selected_evidence_changed": legacy_selected != hybrid_selected,
        "evidence_decision_changed": legacy_decision != hybrid_decision,
        "latency_delta_ms": latency_delta,
    }


def _snapshot_response(snapshot: RetrievalDiagnosticSnapshotV1):
    candidates = tuple(
        SafeRetrievalCandidate(
            candidate_id=item.chunk_id,
            title=item.title,
            safe_excerpt="Frozen diagnostic metadata; full content is not exposed.",
            domain=item.domain,
            topic=item.topic,
            tags=item.tags,
            source_type=item.source_type,
            authority_status="not_recorded",
            content_sha256=item.content_sha256,
            corpus_manifest_sha256=str(snapshot.component_versions.get("corpus_manifest_sha256", "")),
            semantic_rank=item.semantic_rank,
            semantic_score=item.semantic_score,
            lexical_rank=item.lexical_rank,
            lexical_score=item.lexical_score,
            fusion_rank=item.fusion_rank,
            fusion_score=item.fusion_score,
            rerank_rank=item.rerank_rank,
            rerank_score=item.rerank_score,
            channel_hits=item.channel_hits,
            matched_terms=item.matched_terms,
            ranking_explanation=(SafeRankingExplanation.model_validate(item.ranking_explanation) if item.ranking_explanation else None),
            selected=item.selected,
        )
        for item in snapshot.candidates
    )
    return SafeRetrievalInspectionResponse(
        request_id=snapshot.request_id,
        mode="artifact_replay",
        created_at=snapshot.created_at,
        diagnostic_fidelity="full_snapshot",
        engine=snapshot.engine_version,
        profile_id=snapshot.profile_id,
        profile_version=snapshot.profile_version,
        trace_schema_version=snapshot.trace_schema_version,
        inspection_inputs=SafeInspectionInputs(intent="eval"),
        query_facts={"query_sha256": snapshot.query_sha256, "character_count": snapshot.query_character_count},
        resolved_profile={},
        routing_summary={},
        channel_summary=(),
        candidates=candidates,
        evidence_decision=snapshot.evidence_decision,
        consumer_action=ConsumerActionRecord(),
        latency_ms=snapshot.latency_breakdown_ms,
        degraded_reasons=snapshot.degraded_reasons,
        component_versions=snapshot.component_versions,
        provider_call_possible=False,
        artifact_identity={
            "artifact_sha256": snapshot.artifact_sha256,
            "frozen_at": snapshot.created_at.isoformat(),
            "corpus_manifest_sha256": str(
                snapshot.component_versions.get("corpus_manifest_sha256", "")
            ),
        },
        artifact_sha256=snapshot.artifact_sha256,
        case_id=snapshot.case_id,
    )


def _manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _artifact_identity(artifact: KnowledgeEvalArtifactV3) -> dict[str, str | int | bool]:
    return {
        "dataset_version": artifact.dataset_version,
        "dataset_sha256": artifact.dataset_sha256,
        "split": artifact.split,
        "artifact_sha256": artifact.artifact_sha256,
        "frozen_at": artifact.created_at.isoformat(),
        "code_revision": artifact.identity.code_revision,
        "code_tree_sha256": artifact.identity.code_tree_sha256,
        "corpus_manifest_sha256": artifact.identity.corpus_manifest_sha256,
        "embedding_provider": artifact.identity.provider_name,
        "embedding_model": artifact.identity.model_name,
        "embedding_revision": artifact.identity.model_revision,
        "embedding_dimension": artifact.identity.embedding_dimension,
    }


def safe_diagnostic_excerpt(value, *, limit: int = 320) -> str:
    """Normalize a pre-approved summary without falling back to chunk content."""

    if limit < 0:
        raise ValueError("limit must not be negative")
    normalized = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    normalized = " ".join(normalized.split())
    return normalized[:limit]


def _embedding_identity(catalog: RagArtifactCatalog):
    artifacts = catalog.list().artifacts
    if not artifacts:
        return {"provider": "not_recorded", "model": "not_recorded", "revision": "not_recorded", "dimension": 0}
    try:
        artifact = catalog.load(artifacts[0].artifact_sha256)
    except KeyError:
        return {"provider": "not_recorded", "model": "not_recorded", "revision": "not_recorded", "dimension": 0}
    identity = artifact.identity
    return {"provider": identity.provider_name, "model": identity.model_name, "revision": identity.model_revision, "dimension": identity.embedding_dimension}


def _intent(value: str):
    from app.domain.knowledge.retrieval import RetrievalIntent

    return RetrievalIntent(value)
