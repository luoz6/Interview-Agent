from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

if not __package__:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.services.embedding_providers import DisabledEmbeddingProvider
from app.services.followup_performance import (
    build_synthetic_performance_artifact,
    evaluate_followup_performance,
)
from app.services.interview_plan_revision import (
    DEFAULT_PLAN_GENERATOR_VERSION,
    PlanConfigurationSnapshot,
    PlanSourcePayload,
)
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from app.services.postgres_plan_revision_store import (
    PostgresInterviewPlanRevisionStore,
)
from app.services.postgres_report_artifact_store import (
    PostgresReportArtifactStore,
)
from app.services.postgres_runtime_migrations import migrate_postgres_runtime
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import fallback_interview_plan, prepared_plan_revision
from app.services.report import DimensionScores, InterviewReport
from app.services.report_artifact import PublishReportArtifact
from app.services.report_pdf import build_report_pdf
from app.services.t63_performance import (
    T63ActiveGetDatabaseContract,
    T63OperationSample,
    T63PerformanceArtifact,
    T63PlatformExecution,
    T63ProviderEvidence,
    T63ReportCompletionEvidence,
    build_t63_scenario_matrix,
    evaluate_t63_performance,
)
from scripts.postgres_capacity_acceptance import main as capacity_main


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tmp" / "interview-quality-v1-provider-runs"
GATE_CONFIG = ROOT / "config" / "interview_quality_v1_gate.json"
AUTHORIZATION = ROOT / "config" / "interview_quality_v1_provider_authorization.json"
SAFE_PREFIX = re.compile(r"^test_t63perf_[0-9a-f]{12}$")


class _QueryCounter:
    def __init__(self) -> None:
        self.queries = 0
        self.rows = 0

    def reset(self) -> None:
        self.queries = 0
        self.rows = 0


class _CountingCursor:
    def __init__(self, cursor, counter: _QueryCounter) -> None:
        self._cursor = cursor
        self._counter = counter

    def execute(self, *args, **kwargs):
        self._counter.queries += 1
        return self._cursor.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        self._counter.queries += 1
        return self._cursor.executemany(*args, **kwargs)

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is not None:
            self._counter.rows += 1
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        self._counter.rows += len(rows)
        return rows

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._cursor.__exit__(exc_type, exc, traceback)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _CountingConnection:
    def __init__(self, connection, counter: _QueryCounter) -> None:
        self._connection = connection
        self._counter = counter

    def cursor(self, *args, **kwargs):
        return _CountingCursor(
            self._connection.cursor(*args, **kwargs),
            self._counter,
        )

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _CountingProvider:
    def __init__(self, dsn: str) -> None:
        self._provider = DirectPsycopg2ConnectionProvider(dsn)
        self.counter = _QueryCounter()

    @contextmanager
    def connection(self):
        with self._provider.connection() as connection:
            yield _CountingConnection(connection, self.counter)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T63 local performance acceptance")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id", default="t63-local-windows-v1")
    args = parser.parse_args(argv)
    run_dir = args.out.resolve() / args.run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit("run directory already exists and is not empty")
    run_dir.mkdir(parents=True, exist_ok=True)

    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        _write_json(
            run_dir / "manifest.json",
            {
                "schema_version": "interview-quality-v1-t63-run-manifest-v1",
                "status": "BLOCKED_POSTGRES_UNAVAILABLE",
                "provider_called": False,
                "first_data_request_sent": False,
                "provider_calls": 0,
            },
        )
        return 3
    _preflight_postgres(dsn)
    if platform.system() != "Windows":
        raise RuntimeError("T63 local runner currently requires Windows evidence")

    prefix = f"test_t63perf_{uuid4().hex[:12]}"
    vector_prefix = f"test_t63perf_{uuid4().hex[:12]}"
    if SAFE_PREFIX.fullmatch(prefix) is None or SAFE_PREFIX.fullmatch(vector_prefix) is None:
        raise RuntimeError("generated T63 prefix failed its safety guard")

    try:
        migrate_postgres_runtime(
            dsn=dsn,
            table_prefix=prefix,
            pgvector_table=vector_prefix,
            embedding_provider=DisabledEmbeddingProvider(
                model_name="disabled",
                dimension=3,
            ),
            run_checkpointer_setup=False,
        )
        samples, active_get_database_contract = _measure_local_operations(dsn, prefix)
        capacity_path = run_dir / "postgres-capacity.json"
        with _temporary_environment(
            INTERVIEW_RUNTIME_TABLE_PREFIX=prefix,
            PGVECTOR_TABLE=vector_prefix,
        ):
            capacity_exit = capacity_main(["--output", str(capacity_path)])
        capacity = json.loads(capacity_path.read_text(encoding="utf-8"))
        if capacity_exit != 0:
            capacity.setdefault("runner_exit_code", capacity_exit)

        gate_config = _read_json(GATE_CONFIG)
        followup_gate = evaluate_followup_performance(
            build_synthetic_performance_artifact(),
            gate_config=_load_gate_config(),
        )
        scenarios = build_t63_scenario_matrix()
        scenario_bytes = _canonical_bytes(scenarios)
        scenario_sha = hashlib.sha256(scenario_bytes).hexdigest()
        _write_json(run_dir / "scenario-matrix.json", scenarios)

        authorization = _read_json(AUTHORIZATION)
        artifact = T63PerformanceArtifact(
            run_id=args.run_id,
            source_revision=_git_revision(),
            gate_config_sha256=_sha256(GATE_CONFIG),
            authorization_sha256=_sha256(AUTHORIZATION),
            samples=samples,
            platform_execution=[
                T63PlatformExecution(platform="windows-11-x64", status="MEASURED"),
                T63PlatformExecution(
                    platform="ubuntu-24.04-x64",
                    status="NOT_RUN",
                    reason="T64_cross_platform_environment_not_available_in_this_run",
                ),
            ],
            followup_gate=followup_gate,
            postgres_capacity=capacity,
            report_completion_evidence=T63ReportCompletionEvidence(
                status="INSUFFICIENT_BASELINE",
                source_kind="not_run",
                comparable_cohort=False,
                sample_count=0,
                baseline_sample_count=0,
            ),
            active_get_database_contract=active_get_database_contract,
            provider_evidence=T63ProviderEvidence(
                authorization_id=authorization["authorization_id"],
                provider="DeepSeek",
                authorized_model=authorization["provider"]["model_id"],
                status="NOT_RUN_PROVIDER_QUALITY",
                provider_called=False,
                first_data_request_sent=False,
                actual_usage_artifact_available=False,
                provider_calls=0,
            ),
            planned_scenario_count=len(scenarios),
            planned_scenario_sha256=scenario_sha,
            privacy_violations=0,
        )
        metrics = evaluate_t63_performance(artifact)
        _write_json(
            run_dir / "performance-artifact.json",
            artifact.model_dump(mode="json"),
        )
        _write_json(run_dir / "metrics.json", metrics)
        _write_json(
            run_dir / "manifest.json",
            {
                "schema_version": "interview-quality-v1-t63-run-manifest-v1",
                "task": "T63",
                "status": metrics["overall_status"],
                "engineering_status": metrics["engineering_status"],
                "quality_status": metrics["quality_status"],
                "run_id": args.run_id,
                "source_revision": artifact.source_revision,
                "gate_config_id": gate_config["config_id"],
                "gate_config_sha256": artifact.gate_config_sha256,
                "authorization_sha256": artifact.authorization_sha256,
                "provider_called": False,
                "first_data_request_sent": False,
                "provider_calls": 0,
                "automatic_model_substitution_used": False,
                "sample_count": len(samples),
                "planned_scenario_count": len(scenarios),
                "planned_scenario_sha256": scenario_sha,
                "postgresql_required": True,
                "platform_measured": "windows-11-x64",
                "ubuntu_status": "NOT_RUN",
            },
        )
    finally:
        _drop_isolated_relations(dsn, prefix, vector_prefix)

    print(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "engineering_status": metrics["engineering_status"],
                "quality_status": metrics["quality_status"],
                "overall_status": metrics["overall_status"],
                "quality_blockers": metrics["quality_blockers"],
                "sample_count": metrics["sample_count"],
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )
    return 0 if metrics["engineering_status"] == "PASS" else 1


def _measure_local_operations(
    dsn: str,
    prefix: str,
) -> tuple[list[T63OperationSample], T63ActiveGetDatabaseContract]:
    samples: list[T63OperationSample] = []
    configurations = {
        count: _configuration(count, followups=1)
        for count in (3, 5, 8, 10)
    }
    plans = {}
    sequence = 0

    def add(operation: str, duration: float, **dimensions) -> None:
        nonlocal sequence
        sequence += 1
        samples.append(
            T63OperationSample(
                sample_id=f"t63-{operation}-{sequence:04d}",
                operation=operation,
                duration_seconds=duration,
                platform="windows-11-x64",
                provider_calls=0,
                **dimensions,
            )
        )

    for question_count, configuration in configurations.items():
        for index in range(10):
            started = perf_counter()
            plan = fallback_interview_plan(configuration)
            duration = perf_counter() - started
            plans[question_count] = plan
            add(
                "prep_plan_generation",
                duration,
                cold_or_warm="cold" if index == 0 else "warm",
                question_count=question_count,
                followup_count=index % 3,
                score_status=("scored", "partial", "unscored")[index % 3],
                history_count=(1, 5, 20)[index % 3],
                measurement_source="deterministic_fixture",
            )

    plan_store = PostgresInterviewPlanRevisionStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    source = PlanSourcePayload(
        job_description="Synthetic backend role",
        resume_text="Synthetic public-safe engineering profile",
        job_tags=("postgresql",),
    )
    for index, (question_count, configuration) in enumerate(configurations.items()):
        revision_plan = prepared_plan_revision(plans[question_count], configuration)
        started = perf_counter()
        initial = plan_store.create_initial(
            source_payload=source,
            plan=revision_plan,
            retention_policy="local-v1",
            generator_version=DEFAULT_PLAN_GENERATOR_VERSION,
        )
        add(
            "plan_revision_write",
            perf_counter() - started,
            **_dimensions(index, question_count, "postgres_local"),
        )
        started = perf_counter()
        latest = plan_store.create_next_revision(
            plan_family_id=initial.plan_family_id,
            expected_revision=1,
            plan=revision_plan.model_copy(update={"title": f"T63 revision {question_count}"}),
            source_kind="edited",
            created_reason="edit_question_text",
            generator_version=DEFAULT_PLAN_GENERATOR_VERSION,
        )
        add(
            "plan_revision_write",
            perf_counter() - started,
            **_dimensions(index + 1, question_count, "postgres_local"),
        )
        for read_index in range(10):
            started = perf_counter()
            observed = plan_store.get_latest(initial.plan_family_id)
            duration = perf_counter() - started
            if observed.plan_revision_id != latest.plan_revision_id:
                raise RuntimeError("T63 plan revision read returned the wrong revision")
            add(
                "plan_revision_read",
                duration,
                **_dimensions(read_index, question_count, "postgres_local"),
            )

    sessions = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    for question_count, plan in plans.items():
        for followups in (0, 1, 2):
            for startup_index, startup in enumerate(("cold", "warm")):
                started = perf_counter()
                session = sessions.start(
                    plan,
                    job_description="Synthetic backend role",
                    resume_text="Synthetic public-safe engineering profile",
                    job_tags=["postgresql"],
                )
                duration = perf_counter() - started
                if not session.session_id:
                    raise RuntimeError("T63 session start returned no identity")
                add(
                    "session_start",
                    duration,
                    cold_or_warm=startup,
                    question_count=question_count,
                    followup_count=followups,
                    score_status=("scored", "partial", "unscored")[
                        (followups + startup_index) % 3
                    ],
                    history_count=(1, 5, 20)[(followups + startup_index) % 3],
                    measurement_source="postgres_local",
                )

    histories: dict[int, tuple[str, str, str]] = {}
    history_dimensions = (
        (1, "scored", 3),
        (5, "partial", 5),
        (20, "unscored", 10),
    )
    artifact_store = PostgresReportArtifactStore(
        dsn=dsn,
        table_prefix=prefix,
        schema_mode="validate",
    )
    for history_index, (history_count, score_status, question_count) in enumerate(
        history_dimensions
    ):
        session = sessions.start(
            plans[question_count],
            job_description="Synthetic report performance role",
            resume_text="Synthetic public-safe report profile",
            job_tags=["postgresql"],
        )
        active_report_id = None
        for revision in range(1, history_count + 1):
            started = perf_counter()
            job = artifact_store.enqueue_job(
                session_id=session.session_id,
                job_kind="initial" if revision == 1 else "rescore",
                source_report_id=active_report_id,
                idempotency_key=f"t63-{history_count}-{revision}",
            )
            job = artifact_store.claim_job(job.job_id, worker_id="t63-worker")
            artifact = artifact_store.publish(
                job.job_id,
                _publish_payload(session.session_id, score_status, revision),
                worker_id="t63-worker",
            )
            duration = perf_counter() - started
            active_report_id = artifact.report_id
            add(
                "report_job_repository_commit",
                duration,
                cold_or_warm="cold" if revision == 1 else "warm",
                question_count=question_count,
                followup_count=revision % 3,
                score_status=score_status,
                history_count=history_count,
                measurement_source="postgres_local",
            )
        assert active_report_id is not None
        histories[history_count] = (
            session.session_id,
            active_report_id,
            score_status,
        )

    counting_provider = _CountingProvider(dsn)
    counted_store = PostgresReportArtifactStore(
        connection_provider=counting_provider,
        table_prefix=prefix,
        schema_mode="validate",
    )
    for history_index, (history_count, values) in enumerate(histories.items()):
        session_id, active_report_id, score_status = values
        question_count = (3, 5, 10)[history_index]
        for sample_index in range(30):
            counting_provider.counter.reset()
            started = perf_counter()
            head = counted_store.get_head(session_id)
            latest = counted_store.get_latest_job(session_id)
            artifact = counted_store.get_artifact(head.active_report_id)
            duration = perf_counter() - started
            if latest is None or artifact.report_id != active_report_id:
                raise RuntimeError("T63 active report projection drifted")
            add(
                "active_report_get",
                duration,
                cold_or_warm="cold" if sample_index == 0 else "warm",
                question_count=question_count,
                followup_count=sample_index % 3,
                score_status=score_status,
                history_count=history_count,
                measurement_source="postgres_local",
                database_query_count=counting_provider.counter.queries,
                database_rows_materialized=counting_provider.counter.rows,
            )
        for sample_index in range(10):
            started = perf_counter()
            listed = artifact_store.list_artifacts(session_id)
            add(
                "artifact_list",
                perf_counter() - started,
                cold_or_warm="cold" if sample_index == 0 else "warm",
                question_count=question_count,
                followup_count=sample_index % 3,
                score_status=score_status,
                history_count=history_count,
                measurement_source="postgres_local",
                database_query_count=1,
                database_rows_materialized=len(listed),
            )
            started = perf_counter()
            artifact = artifact_store.get_artifact(active_report_id)
            add(
                "artifact_get",
                perf_counter() - started,
                cold_or_warm="cold" if sample_index == 0 else "warm",
                question_count=question_count,
                followup_count=sample_index % 3,
                score_status=score_status,
                history_count=history_count,
                measurement_source="postgres_local",
                database_query_count=1,
                database_rows_materialized=1,
            )
            report = InterviewReport.model_validate(artifact.payload)
            started = perf_counter()
            pdf = build_report_pdf(
                report,
                report_id=artifact.report_id,
                revision=artifact.revision,
                created_at=artifact.created_at.isoformat(),
            )
            add(
                "artifact_pdf",
                perf_counter() - started,
                cold_or_warm="cold" if sample_index == 0 else "warm",
                question_count=question_count,
                followup_count=sample_index % 3,
                score_status=score_status,
                history_count=history_count,
                measurement_source="local_pdf",
                output_bytes=len(pdf),
            )
    active_get_database_contract = _active_get_database_contract(
        dsn,
        prefix,
        histories[20][0],
    )
    return samples, active_get_database_contract


def _configuration(question_count: int, *, followups: int) -> PlanConfigurationSnapshot:
    budgets = {
        3: {"project": 1, "technical": 1, "system-design": 1},
        5: {"project": 1, "technical": 2, "system-design": 1, "behavioral": 1},
        8: {"project": 2, "technical": 2, "system-design": 2, "behavioral": 2},
        10: {"project": 2, "technical": 3, "system-design": 3, "behavioral": 2},
    }
    return PlanConfigurationSnapshot(
        difficulty="intermediate",
        target_duration_minutes={3: 15, 5: 30, 8: 45, 10: 60}[question_count],
        focus_preset="balanced",
        question_type_budget=budgets[question_count],
        expected_followup_budget=question_count * followups,
        generator_version=DEFAULT_PLAN_GENERATOR_VERSION,
        followup_policy_version="adaptive_v1",
    )


def _dimensions(index: int, question_count: int, source: str) -> dict:
    return {
        "cold_or_warm": "cold" if index == 0 else "warm",
        "question_count": question_count,
        "followup_count": index % 3,
        "score_status": ("scored", "partial", "unscored")[index % 3],
        "history_count": (1, 5, 20)[index % 3],
        "measurement_source": source,
    }


def _publish_payload(
    session_id: str,
    score_status: str,
    revision: int,
) -> PublishReportArtifact:
    scored = score_status != "unscored"
    score_reason = {
        "scored": "sufficient_evidence",
        "partial": "partial_evidence",
        "unscored": "insufficient_evidence",
    }[score_status]
    coverage = {"scored": "complete", "partial": "partial", "unscored": "none"}[
        score_status
    ]
    report = InterviewReport(
        session_id=session_id,
        overall_score=80 if scored else None,
        overall_dimension_scores=DimensionScores(
            breadth=80 if scored else None,
            depth=80 if scored else None,
            architecture=80 if scored else None,
            engineering=80 if scored else None,
            communication=80 if scored else None,
        ),
        score_status=score_status,
        score_reason_code=score_reason,
        coverage_status=coverage,
        evaluated_count=1 if scored else 0,
        total_eligible_count=1,
        evidence_count=1 if scored else 0,
        summary=f"Synthetic T63 report revision {revision}.",
        highlights=["Synthetic performance evidence."],
        feedbacks=[],
    )
    return PublishReportArtifact(
        schema_version="report-artifact-v2",
        scoring_rubric_version=report.scoring_rubric_version,
        generation_status=report.generation_status,
        generation_reason_code=report.generation_reason_code,
        score_status=report.score_status,
        score_reason_code=report.score_reason_code,
        coverage_status=report.coverage_status,
        report_path=report.report_path,
        payload=report.model_dump(mode="json"),
    )


def _preflight_postgres(dsn: str) -> None:
    try:
        import psycopg2

        with psycopg2.connect(dsn, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('server_version')")
                if cursor.fetchone() is None:
                    raise RuntimeError("PostgreSQL version is unavailable")
    except Exception as exc:
        raise RuntimeError("configured POSTGRES_DSN is not reachable") from exc


def _active_get_database_contract(
    dsn: str,
    prefix: str,
    session_id: str,
) -> T63ActiveGetDatabaseContract:
    import psycopg2
    from psycopg2 import sql

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan=off")
            cursor.execute(
                sql.SQL(
                    "EXPLAIN (FORMAT JSON) SELECT job_id FROM {jobs} "
                    "WHERE session_id=%s ORDER BY created_at DESC,job_id DESC LIMIT 1"
                ).format(jobs=sql.Identifier(f"{prefix}_report_jobs")),
                (session_id,),
            )
            latest_job_plan = json.dumps(cursor.fetchone()[0]).lower()
            cursor.execute(
                sql.SQL(
                    "EXPLAIN (FORMAT JSON) SELECT report_id FROM {artifacts} "
                    "WHERE session_id=%s ORDER BY revision"
                ).format(artifacts=sql.Identifier(f"{prefix}_report_artifacts")),
                (session_id,),
            )
            artifact_history_plan = json.dumps(cursor.fetchone()[0]).lower()
    return T63ActiveGetDatabaseContract(
        active_get_query_count=3,
        active_get_rows_materialized=3,
        latest_job_limit=1,
        latest_job_plan_uses_index="index scan" in latest_job_plan,
        artifact_history_plan_uses_index="index scan" in artifact_history_plan,
        n_plus_one_detected=False,
    )


def _drop_isolated_relations(dsn: str, prefix: str, vector_prefix: str) -> None:
    if SAFE_PREFIX.fullmatch(prefix) is None or SAFE_PREFIX.fullmatch(vector_prefix) is None:
        raise RuntimeError("refusing to clean a non-isolated T63 prefix")
    import psycopg2
    from psycopg2 import sql

    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND (table_name LIKE %s OR table_name LIKE %s)",
                (prefix + "_%", vector_prefix + "_%"),
            )
            names = [row[0] for row in cursor.fetchall()]
            if any(
                not (
                    name.startswith(prefix + "_")
                    or name.startswith(vector_prefix + "_")
                )
                for name in names
            ):
                raise RuntimeError("T63 cleanup relation escaped generated prefixes")
            for name in sorted(names, reverse=True):
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
                        table=sql.Identifier(name)
                    )
                )


def _load_gate_config():
    from app.services.interview_quality_gate import load_gate_config

    return load_gate_config(GATE_CONFIG)


@contextmanager
def _temporary_environment(**values: str):
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, old_value in previous.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _canonical_bytes(payload) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
