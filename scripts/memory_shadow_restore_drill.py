from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable

from app.services.context_artifacts import (
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
)
from app.services.in_memory_context_artifact_store import (
    InMemoryContextArtifactStore,
)
from app.services.in_memory_principal_memory import (
    InMemoryPrincipalMemoryFactStore,
)
from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.principal_memory_contracts import (
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
)
from app.services.question_memory_index import QuestionMemoryIndexEntry
from app.services.report import DimensionScores, InterviewReport
from app.services.session import InterviewSessionStore
from app.services.session_deletion import (
    InMemorySessionDeletionJobStore,
    SessionDeletionService,
)
from app.services.session_deletion_tombstones import (
    InMemorySessionDeletionTombstoneStore,
)
from app.services.session_deletion_worker import SessionDeletionWorker
from scripts.replay_session_deletion_tombstones import replay_tombstones


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)
FAULT_BOUNDARIES = (
    "after_workflow_purge",
    "after_question_memory_purge",
    "after_artifact_ref_purge",
    "after_principal_memory_purge",
    "after_business_session_purge",
    "after_tombstone_complete",
)
PRIVATE_RESIDUE_CATEGORIES = (
    "business_sessions",
    "workflow_state",
    "messages",
    "reports",
    "question_memory",
    "artifact_owner_refs",
    "principal_memory_facts",
    "principal_memory_effects",
    "session_bound_consent_bindings",
)
_BLOCKED_EVIDENCE_TERMS = (
    "session_id",
    "principal_id",
    "fact_id",
    "normalized_fact",
    "source_manifest",
    "source_excerpt",
    "artifact_ref",
    "prompt",
    "answer",
    "resume",
    "postgresql://",
    "database_fingerprint",
    "table_prefix",
)


class _NoProviderLLM:
    def generate_followup(self, context):  # pragma: no cover - safety tripwire
        raise AssertionError("restore drill must not call a provider")


class _RestoredWorkflowState:
    def __init__(self) -> None:
        self._rows = {"checkpoint": 1, "control": 1, "generation": 1}

    def purge_session(self, session_locator: str) -> dict[str, int]:
        del session_locator
        counts = dict(self._rows)
        self._rows = {key: 0 for key in self._rows}
        return counts

    def residue(self) -> int:
        return sum(self._rows.values())


class _RestoredPrincipalStore(InMemoryPrincipalMemoryFactStore):
    def __init__(self) -> None:
        super().__init__()
        self._effects_by_source: dict[str, int] = {}

    def add_effect(self, source_locator: str) -> None:
        self._effects_by_source[source_locator] = (
            self._effects_by_source.get(source_locator, 0) + 1
        )

    def purge_by_session(self, source_session_id: str) -> int:
        facts = super().purge_by_session(source_session_id)
        effects = self._effects_by_source.pop(source_session_id, 0)
        return facts + effects

    def effect_residue(self, source_locator: str) -> int:
        return self._effects_by_source.get(source_locator, 0)


class _ReportJobStore:
    def __init__(self, job_locator: str) -> None:
        self._job_locator = job_locator

    def get_job_by_session(self, session_locator: str) -> dict[str, str]:
        del session_locator
        return {"job_id": self._job_locator}


class _FailOnce:
    def __init__(self, target: str) -> None:
        self.target = target
        self.failed = False

    def __call__(self, boundary, job) -> None:
        del job
        if boundary == self.target and not self.failed:
            self.failed = True
            raise RuntimeError("injected restore replay process loss")


@dataclass
class _RestoredSnapshot:
    session_locator: str
    principal_locator: str
    review_job_locator: str
    session_store: InterviewSessionStore
    workflow_state: _RestoredWorkflowState
    question_memory: InMemoryQuestionMemoryIndexStore
    artifacts: InMemoryContextArtifactStore
    principal_memory: _RestoredPrincipalStore
    report_jobs: _ReportJobStore

    def worker(
        self,
        *,
        jobs: InMemorySessionDeletionJobStore,
        tombstones: InMemorySessionDeletionTombstoneStore,
        fault_injector=None,
    ) -> SessionDeletionWorker:
        return SessionDeletionWorker(
            job_store=jobs,
            session_store=self.session_store,
            workflow_service=self.workflow_state,
            question_memory_index=self.question_memory,
            context_artifact_store=self.artifacts,
            report_job_store=self.report_jobs,
            tombstone_store=tombstones,
            principal_memory_store=self.principal_memory,
            fault_injector=fault_injector,
        )

    def counts(self) -> dict[str, int]:
        try:
            state = self.session_store.get(self.session_locator)
        except ValueError:
            state = None
        session_count = int(state is not None)
        message_count = len(state.get("messages", [])) if state else 0
        report_count = int(
            state is not None
            and self.session_store.get_report_record(self.session_locator) is not None
        )
        question_count = len(
            self.question_memory.list_active(
                session_id=self.session_locator,
                policy_version="question-memory-v1",
                limit=20,
            )
        )
        owner_ref_count = sum(
            1
            for row in self.artifacts._refs.values()
            if row.owner_key in {self.session_locator, self.review_job_locator}
        )
        fact_count = len(
            self.principal_memory.list_by_principal(
                deployment_id="single-tenant-local",
                principal_id=self.principal_locator,
                limit=20,
                include_terminal=True,
            )
        )
        return {
            "business_sessions": session_count,
            "workflow_state": self.workflow_state.residue(),
            "messages": message_count,
            "reports": report_count,
            "question_memory": question_count,
            "artifact_owner_refs": owner_ref_count,
            "principal_memory_facts": fact_count,
            "principal_memory_effects": self.principal_memory.effect_residue(
                self.session_locator
            ),
            # V1 has principal-scoped Consent and an explicit resolver, not a
            # persisted session-scoped consent or identity-binding record.
            "session_bound_consent_bindings": 0,
        }


def _opaque_locator(kind: str, cycle: int) -> str:
    return f"restore-{kind}-{sha256(f'{kind}:{cycle}'.encode()).hexdigest()[:24]}"


def _artifact_identity(
    *, artifact_type: str, output_schema_version: str, seed: str
) -> ContextArtifactIdentity:
    digest = lambda suffix: sha256(f"{seed}:{suffix}".encode()).hexdigest()
    return ContextArtifactIdentity.from_material(
        ContextArtifactIdentityMaterial(
            artifact_type=artifact_type,
            privacy_scope_sha256=digest("privacy"),
            source_sha256=digest("source"),
            source_manifest_sha256=digest("manifest"),
            semantic_focus_sha256=digest("focus"),
            compression_policy_version="restore-drill-v1",
            prompt_contract_version="restore-drill-v1",
            output_schema_version=output_schema_version,
            compressor_provider="disabled",
            compressor_model="disabled",
            compressor_settings_sha256=digest("settings"),
            target_output_tokens=128,
        )
    )


def _seed_artifact(
    store: InMemoryContextArtifactStore,
    *,
    identity: ContextArtifactIdentity,
    payload: dict,
    owner_type: str,
    owner_key: str,
    purpose: str,
):
    claim = store.claim(identity, worker_id="restore-drill", lease_seconds=60)
    record = store.complete(claim, payload)
    return store.create_owner_ref(
        record,
        owner_type=owner_type,
        owner_key=owner_key,
        purpose=purpose,
    )


def _make_fact(
    *, principal_locator: str, session_locator: str, cycle: int
) -> PrincipalMemoryFact:
    normalized = canonical_principal_fact({"confirmed_skill": "python"})
    values = {
        "deployment_id": "single-tenant-local",
        "principal_id": principal_locator,
        "fact_type": "confirmed_skill",
        "normalized_fact": normalized,
        "source_manifest_sha256": sha256(
            f"manifest:{cycle}".encode()
        ).hexdigest(),
        "source_excerpt_sha256": sha256(
            f"excerpt:{cycle}".encode()
        ).hexdigest(),
        "consent_policy_version": "principal-memory-consent-v1",
        "taxonomy_version": "principal-memory-taxonomy-v1",
    }
    return PrincipalMemoryFact(
        fact_id=derive_principal_fact_id(**values),
        **values,
        confidence=0.9,
        authority="model_proposed",
        source_session_id=session_locator,
        created_at=NOW,
    )


def _restore_snapshot(cycle: int) -> _RestoredSnapshot:
    session_locator = _opaque_locator("session", cycle)
    principal_locator = _opaque_locator("principal", cycle)
    review_job_locator = _opaque_locator("review", cycle)
    sessions = InterviewSessionStore(llm=_NoProviderLLM())
    sessions.start(
        InterviewPlan(
            title="Synthetic restore drill",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Synthetic reliability question",
                    focus="reliability",
                )
            ],
        ),
        job_description="Synthetic role",
        resume_text="Synthetic source",
        job_tags=["reliability"],
        session_id=session_locator,
    )
    sessions.finish(session_locator)
    sessions.save_report(
        session_locator,
        InterviewReport(
            session_id=session_locator,
            overall_score=80,
            overall_dimension_scores=DimensionScores(
                breadth=80,
                depth=80,
                architecture=80,
                engineering=80,
                communication=80,
            ),
            summary="Synthetic report",
            highlights=["Synthetic highlight"],
            feedbacks=[],
        ),
    )

    artifacts = InMemoryContextArtifactStore()
    memory_identity = _artifact_identity(
        artifact_type="question_memory",
        output_schema_version="question-memory-v1",
        seed=f"memory:{cycle}",
    )
    memory_ref = _seed_artifact(
        artifacts,
        identity=memory_identity,
        payload={
            "schema_version": "question-memory-v1",
            "authority": "non_authoritative",
            "session_scope_sha256": sha256(session_locator.encode()).hexdigest(),
            "question_id_sha256": sha256(b"q1").hexdigest(),
            "question_focus_sha256": sha256(b"reliability").hexdigest(),
            "source_manifest_sha256": sha256(
                f"question:{cycle}".encode()
            ).hexdigest(),
            "source_message_count": 1,
            "claims": [],
            "unresolved_topics": [],
        },
        owner_type="interview_session",
        owner_key=session_locator,
        purpose="interview_question_memory",
    )
    review_identity = _artifact_identity(
        artifact_type="question_conversation",
        output_schema_version="question-conversation-v1",
        seed=f"review:{cycle}",
    )
    _seed_artifact(
        artifacts,
        identity=review_identity,
        payload={
            "schema_version": "question-conversation-v1",
            "question_id_sha256": sha256(b"q1").hexdigest(),
            "units": [],
            "unresolved_topics": [],
            "source_message_count": 1,
        },
        owner_type="review_job",
        owner_key=review_job_locator,
        purpose="review_context",
    )

    question_memory = InMemoryQuestionMemoryIndexStore()
    question_memory.activate(
        QuestionMemoryIndexEntry(
            session_id=session_locator,
            question_id="q1",
            question_id_sha256=sha256(b"q1").hexdigest(),
            focus_sha256=sha256(b"reliability").hexdigest(),
            focus_tags=["reliability"],
            skill_tags=["reliability"],
            skill_tag_sha256=[sha256(b"reliability").hexdigest()],
            unresolved_topic_codes=[],
            unresolved_topic_sha256=[],
            artifact_ref=memory_ref.artifact_ref,
            artifact_sha256=memory_ref.artifact_sha256,
            policy_version="question-memory-v1",
            source_manifest_sha256=sha256(
                f"question:{cycle}".encode()
            ).hexdigest(),
            source_message_count=1,
            source_max_sequence_no=1,
            created_at=NOW,
        )
    )
    principal = _RestoredPrincipalStore()
    principal.create_proposal(
        _make_fact(
            principal_locator=principal_locator,
            session_locator=session_locator,
            cycle=cycle,
        )
    )
    principal.add_effect(session_locator)
    return _RestoredSnapshot(
        session_locator=session_locator,
        principal_locator=principal_locator,
        review_job_locator=review_job_locator,
        session_store=sessions,
        workflow_state=_RestoredWorkflowState(),
        question_memory=question_memory,
        artifacts=artifacts,
        principal_memory=principal,
        report_jobs=_ReportJobStore(review_job_locator),
    )


def _delete_and_export_tombstone(snapshot: _RestoredSnapshot):
    jobs = InMemorySessionDeletionJobStore()
    tombstones = InMemorySessionDeletionTombstoneStore()
    service = SessionDeletionService(
        session_store=snapshot.session_store,
        job_store=jobs,
        tombstone_store=tombstones,
    )
    service.request(snapshot.session_locator)
    snapshot.worker(jobs=jobs, tombstones=tombstones).run_once()
    return tombstones.get_for_session(snapshot.session_locator)


def _public_knowledge_fingerprint() -> tuple[int, str]:
    roots = (ROOT / "app" / "data" / "knowledge", ROOT / "app" / "data" / "knowledge_v2")
    files = sorted(
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
    )
    digest = sha256()
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return len(files), digest.hexdigest()


def _sum_counts(target: dict[str, int], values: dict[str, int]) -> None:
    for key in PRIVATE_RESIDUE_CATEGORIES:
        target[key] += int(values[key])


def _run_fault_reclaim(boundary: str, cycle: int) -> bool:
    snapshot = _restore_snapshot(cycle)
    operator_tombstone = _delete_and_export_tombstone(snapshot)
    restored = _restore_snapshot(cycle)
    jobs = InMemorySessionDeletionJobStore()
    tombstones = InMemorySessionDeletionTombstoneStore()
    service = SessionDeletionService(
        session_store=restored.session_store,
        job_store=jobs,
        tombstone_store=tombstones,
    )
    # The replay service derives the active job from the restored session.
    # In-memory tombstones do not expose an import API; integrity of the
    # protected operator record was already validated when it was exported.
    del operator_tombstone
    service.request(restored.session_locator)
    failed = False
    try:
        restored.worker(
            jobs=jobs,
            tombstones=tombstones,
            fault_injector=_FailOnce(boundary),
        ).run_once()
    except RuntimeError as exc:
        failed = "injected restore replay process loss" in str(exc)
    if not failed:
        return False
    restored.worker(jobs=jobs, tombstones=tombstones).run_once()
    tombstones.mark_replayed(
        tombstones.get_for_session(restored.session_locator)
    )
    return sum(restored.counts().values()) == 0


def run_restore_drill(*, restore_cycles: int = 3) -> dict:
    if restore_cycles < 3:
        raise ValueError("restore drill requires at least three cycles")
    public_before = _public_knowledge_fingerprint()
    restored_totals = {key: 0 for key in PRIVATE_RESIDUE_CATEGORIES}
    residue_totals = {key: 0 for key in PRIVATE_RESIDUE_CATEGORIES}
    replayed = 0
    for cycle in range(restore_cycles):
        backup_source = _restore_snapshot(cycle)
        operator_tombstone = _delete_and_export_tombstone(backup_source)
        restored = _restore_snapshot(cycle)
        _sum_counts(restored_totals, restored.counts())
        jobs = InMemorySessionDeletionJobStore()
        tombstones = InMemorySessionDeletionTombstoneStore()
        service = SessionDeletionService(
            session_store=restored.session_store,
            job_store=jobs,
            tombstone_store=tombstones,
        )
        replay = replay_tombstones(
            [operator_tombstone],
            service=service,
            worker=restored.worker(jobs=jobs, tombstones=tombstones),
            tombstone_store=tombstones,
        )
        replayed += replay["replayed"]
        _sum_counts(residue_totals, restored.counts())

    fault_results = [
        _run_fault_reclaim(boundary, 100 + index)
        for index, boundary in enumerate(FAULT_BOUNDARIES)
    ]
    public_after = _public_knowledge_fingerprint()
    total_residue = sum(residue_totals.values())
    passed = (
        replayed == restore_cycles
        and total_residue == 0
        and all(fault_results)
        and public_before == public_after
    )
    result = {
        "schema_version": "memory-shadow-restore-drill-v1",
        "backup_restore_tombstone_replay": "PASS" if passed else "BLOCKED",
        "restore_cycles": restore_cycles,
        "tombstones_replayed": replayed,
        "fault_boundaries_exercised": len(FAULT_BOUNDARIES),
        "fault_reclaims_completed": sum(fault_results),
        "restored_rows_by_category": restored_totals,
        "residue_by_category": residue_totals,
        "restored_private_data_residue": total_residue,
        "public_knowledge_file_count": public_after[0],
        "public_knowledge_unchanged": public_before == public_after,
        "provider_calls": 0,
        "production_observation": "NOT_RUN",
        "long_term_memory_consumption": "BLOCKED",
    }
    validate_evidence_artifact(result)
    return result


def validate_evidence_artifact(value: dict) -> None:
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False).casefold()
    if any(term in rendered for term in _BLOCKED_EVIDENCE_TERMS):
        raise RuntimeError("restore drill evidence contains a private field")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run synthetic old-backup tombstone replay without providers."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--restore-cycles", type=int, default=3)
    parser.add_argument("--evidence-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.execute:
        print("mode=DRY_RUN")
        print("data_category=synthetic")
        print("provider_calls=0")
        return 0
    result = run_restore_drill(restore_cycles=args.restore_cycles)
    if args.evidence_output is not None:
        args.evidence_output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(
        "BACKUP_RESTORE_TOMBSTONE_REPLAY="
        + result["backup_restore_tombstone_replay"]
    )
    print(
        "RESTORED_PRIVATE_DATA_RESIDUE="
        + str(result["restored_private_data_residue"])
    )
    print(
        "PUBLIC_KNOWLEDGE_UNCHANGED="
        + str(result["public_knowledge_unchanged"]).lower()
    )
    return 0 if result["backup_restore_tombstone_replay"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
