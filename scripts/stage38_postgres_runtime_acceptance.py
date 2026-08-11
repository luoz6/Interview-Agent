from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Sequence
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.postgres_session import PostgresInterviewSessionStore
from app.domain.interview.errors import SessionVersionConflict
from app.ports.postgres_scope import PostgresScopeError
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    Stage38AcceptanceEvidencePayload,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.policies import Stage38AcceptanceEvidencePolicy
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    approved_postgres_scope,
    load_receipt_signer,
    require_environment_value,
)
from scripts.stage38_acceptance_support import (
    FakeStage38InterviewLLM,
    make_stage38_plan,
    make_stage38_report,
)


SAFE_TABLE_PREFIX = re.compile(r"^test_stage38_[0-9a-f]{12}$")


class AcceptanceGateError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def require_gate(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise AcceptanceGateError(code, detail)


def assert_safe_table_prefix(table_prefix: str) -> None:
    require_gate(
        SAFE_TABLE_PREFIX.fullmatch(table_prefix) is not None,
        "STAGE38_TABLE_PREFIX_UNSAFE",
        "table prefix must be a generated Stage 38 isolation prefix",
    )


def make_store(dsn: str, table_prefix: str) -> PostgresInterviewSessionStore:
    assert_safe_table_prefix(table_prefix)
    return PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
        llm=FakeStage38InterviewLLM(),
    )


def start_session(store: PostgresInterviewSessionStore):
    return store.start(
        make_stage38_plan(),
        job_description="Backend role using FastAPI, Redis, and PostgreSQL.",
        resume_text="Built FastAPI services with Redis cache-aside and PostgreSQL.",
        job_tags=["python", "fastapi", "redis", "postgresql"],
    )


def count_messages(snapshot: dict, role: str) -> int:
    return len([message for message in snapshot["messages"] if message["role"] == role])


def run_acceptance(
    *,
    dsn: str,
    table_prefix: str,
) -> dict:
    assert_safe_table_prefix(table_prefix)
    store = make_store(dsn, table_prefix)
    tables = store.list_runtime_tables()
    require_gate(
        bool(tables),
        "STAGE38_SCHEMA_INITIALIZATION_FAILED",
        "isolated runtime schema did not create any tables",
    )
    stale_session = start_session(store)
    try:
        store.submit_answer(
            stale_session.session_id,
            "I used Redis cache-aside.",
            expected_version=0,
            command_id="cmd-stale",
        )
        raise AcceptanceGateError(
            "STAGE38_STALE_VERSION_ACCEPTED",
            "stale command unexpectedly succeeded",
        )
    except SessionVersionConflict as exc:
        require_gate(
            exc.expected_version == 0 and exc.actual_version == 1,
            "STAGE38_VERSION_CONFLICT_MISMATCH",
            "version conflict did not preserve expected=0 actual=1",
        )
    duplicate_session = start_session(store)
    first_turn = store.submit_answer(
        duplicate_session.session_id,
        "I built a FastAPI API with Redis.",
        expected_version=1,
        command_id="cmd-answer",
    )
    duplicate_turn = store.submit_answer(
        duplicate_session.session_id,
        "I built a FastAPI API with Redis.",
        expected_version=1,
        command_id="cmd-answer",
    )
    duplicate_snapshot = store.snapshot(duplicate_session.session_id)
    require_gate(
        duplicate_turn.follow_up == first_turn.follow_up,
        "STAGE38_IDEMPOTENT_RESPONSE_MISMATCH",
        "duplicate command returned a different follow-up",
    )
    require_gate(
        duplicate_snapshot["state_version"] == 2
        and duplicate_snapshot["checkpoint_version"] == 2
        and duplicate_snapshot["last_command_id"] == "cmd-answer"
        and count_messages(duplicate_snapshot, "candidate") == 1,
        "STAGE38_IDEMPOTENCY_STATE_MISMATCH",
        "duplicate command changed persisted session state",
    )
    stream_session = start_session(store)
    prepared = store.prepare_streaming_answer(
        stream_session.session_id,
        "I protected PostgreSQL during cache misses.",
        expected_version=1,
        command_id="cmd-stream",
    )
    require_gate(
        prepared.stream_follow_up is True,
        "STAGE38_STREAM_PREPARATION_FAILED",
        "streaming answer was not prepared for follow-up generation",
    )
    finalized = store.complete_streaming_answer(
        stream_session.session_id,
        follow_up_text="Please explain cache miss protection.",
        expected_version=2,
        command_id="cmd-stream",
    )
    duplicate_finalized = store.complete_streaming_answer(
        stream_session.session_id,
        follow_up_text="Please explain cache miss protection.",
        expected_version=2,
        command_id="cmd-stream",
    )
    stream_snapshot = store.snapshot(stream_session.session_id)
    require_gate(
        duplicate_finalized == finalized,
        "STAGE38_STREAM_REPLAY_MISMATCH",
        "replayed stream completion returned a different result",
    )
    require_gate(
        stream_snapshot["state_version"] == 3
        and stream_snapshot["checkpoint_version"] == 3
        and stream_snapshot["last_command_id"] == "cmd-stream"
        and count_messages(stream_snapshot, "candidate") == 1,
        "STAGE38_STREAM_STATE_MISMATCH",
        "stream completion did not advance persisted state exactly once",
    )
    report_session = start_session(store)
    store.finish(
        report_session.session_id,
        expected_version=1,
        command_id="cmd-finish",
    )
    require_gate(
        store.mark_report_processing(report_session.session_id) is True,
        "STAGE38_REPORT_PROCESSING_TRANSITION_FAILED",
        "report processing transition was rejected",
    )
    processing_snapshot = store.snapshot(report_session.session_id)
    require_gate(
        processing_snapshot["phase"] == "review"
        and processing_snapshot["phase_status"] == "active"
        and processing_snapshot["review_status"] == "processing"
        and processing_snapshot["state_version"] == 3
        and processing_snapshot["last_command_id"] == "cmd-finish",
        "STAGE38_REPORT_PROCESSING_STATE_MISMATCH",
        "processing snapshot did not preserve the expected report lifecycle state",
    )
    store.save_report(
        report_session.session_id,
        make_stage38_report(report_session.session_id),
    )
    completed_snapshot = store.snapshot(report_session.session_id)
    require_gate(
        completed_snapshot["phase_status"] == "completed"
        and completed_snapshot["review_status"] == "completed"
        and completed_snapshot["state_version"] == 4
        and completed_snapshot["last_command_id"] == "cmd-finish",
        "STAGE38_REPORT_COMPLETION_STATE_MISMATCH",
        "completed snapshot did not preserve report lifecycle state",
    )
    recovered = make_store(dsn, table_prefix)
    recovered_snapshot = recovered.snapshot(report_session.session_id)
    recovered_record = recovered.get_report_record(report_session.session_id)
    require_gate(
        recovered_snapshot["state_version"] == 4
        and recovered_snapshot["last_command_id"] == "cmd-finish"
        and recovered_record is not None
        and recovered_record.status == "completed",
        "STAGE38_REINSTANTIATION_STATE_MISMATCH",
        "reinstantiated store did not recover the completed report state",
    )
    return {
        "schema_initialized": True,
        "stale_version_rejected": True,
        "duplicate_command_idempotent": True,
        "stream_completion_exactly_once": True,
        "report_lifecycle_preserved": True,
        "reinstantiation_recovered": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--table-prefix",
        default=f"test_stage38_{uuid4().hex[:12]}",
        help="Externally approved exact Owned PostgreSQL scope prefix.",
    )
    parser.add_argument(
        "--output",
        default="reports/acceptance/stage38-postgres-runtime-evidence-v1.json",
        help="Protected Stage 38 Evidence Bundle output path.",
    )
    return parser


def _render_result(result: dict) -> None:
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)


def _dry_run_result(table_prefix: str) -> dict:
    return {
        "schema_version": "stage38-postgres-runtime-acceptance-v2",
        "stage": "Stage 38 Postgres Runtime Acceptance",
        "mode": "DRY_RUN",
        "status": "not_run",
        "database_connection_attempted": False,
        "table_prefix": table_prefix,
        "required_authorization": [
            "--execute",
            "POSTGRES_DSN",
            "POSTGRES_ACCEPTANCE_APPROVAL_ID",
            "POSTGRES_ACCEPTANCE_APPROVAL_RECEIPT_SHA256",
            "POSTGRES_ACCEPTANCE_APPROVED_FINGERPRINT",
            "POSTGRES_ACCEPTANCE_DATABASE_ALLOWLIST",
            "POSTGRES_ACCEPTANCE_APPROVAL_EXPIRES_AT",
            "EVIDENCE_REVISION",
            "EVIDENCE_HMAC_KEY_ID",
            "EVIDENCE_HMAC_SECRET_B64",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assert_safe_table_prefix(args.table_prefix)
    if not args.execute:
        _render_result(_dry_run_result(args.table_prefix))
        return 0

    try:
        dsn = require_environment_value(os.environ, "POSTGRES_DSN")
        revision = require_environment_value(os.environ, "EVIDENCE_REVISION")
        signer = load_receipt_signer(os.environ)
    except AcceptanceConfigurationError as exc:
        raise AcceptanceGateError(
            exc.code,
            "protected acceptance configuration is invalid",
        ) from None

    active = None
    observations = None
    try:
        with approved_postgres_scope(
            dsn=dsn,
            scope_prefix=args.table_prefix,
            environ=os.environ,
        ) as active:
            observations = run_acceptance(
                dsn=dsn,
                table_prefix=args.table_prefix,
            )
    except (AcceptanceConfigurationError, PostgresScopeError) as exc:
        raise AcceptanceGateError(
            exc.code,
            "Owned PostgreSQL scope was rejected",
        ) from None

    if active is None or observations is None or active.lease.cleanup_receipt is None:
        raise AcceptanceGateError(
            "STAGE38_CLEANUP_RECEIPT_MISSING",
            "Owned PostgreSQL scope did not produce a cleanup receipt",
        )
    cleanup = active.lease.cleanup_receipt
    payload = Stage38AcceptanceEvidencePayload(
        schema_version="stage38-acceptance-evidence-v1",
        synthetic=True,
        **observations,
        cleanup_ownership_verified=cleanup.ownership_verified,
        cleanup_target_verified=cleanup.target_verified,
        cleanup_residue_count=cleanup.residue_count,
    )
    policy_result = Stage38AcceptanceEvidencePolicy().evaluate(payload)
    evidence_scope = "stage38.postgres-runtime.controlled"
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="stage38-acceptance-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.stage38-postgres-runtime-acceptance",
        tool_version="3.0.0",
        revision=revision,
        scope=evidence_scope,
    )
    verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    AtomicEvidenceWriter(
        post_write_verifier=lambda value: verifier.verify(
            value,
            expected_revision=revision,
            expected_scope=evidence_scope,
        )
    ).write(Path(args.output), bundle)
    for line in render_gate_lines(bundle):
        print(line)
    return 0 if policy_result.verification_status.value == "PASS" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcceptanceGateError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "stage38-postgres-runtime-acceptance-v2",
                    "status": "blocked",
                    "gate_code": exc.code,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
