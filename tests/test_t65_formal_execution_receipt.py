from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.t65_formal_execution_receipt import (
    REQUIRED_FORMAL_EXECUTOR_PATHS,
    T65CleanupReceipt,
    T65FormalExecutionReceipt,
    T65FormalExecutorCodeFile,
    T65FormalExecutorManifestReceipt,
    T65FormalProviderLedgerReceipt,
    T65FormalReceiptError,
    T65FormalRunIdentity,
    T65PersistedCaptureReceipt,
    T65ReportExecutionReceipt,
    T65RuntimeEvidenceReceipt,
    T65SqlEvidenceReceipt,
    build_formal_executor_manifest_receipt,
    resolve_t65_execution_route,
    validate_t65_formal_receipt_structure,
    validate_t65_formal_route,
)
from app.services.t65_runtime_performance import RuntimeEvidenceBuildResult


def _h(char: str) -> str:
    return char * 64


def _identity(**updates) -> T65FormalRunIdentity:
    values = {
        "run_id": "t65-formal-1",
        "candidate_revision": "1" * 40,
        "candidate_tree": "2" * 40,
        "capture_plan_sha256": _h("3"),
        "gate_config_sha256": _h("4"),
        "authorization_id": "interview-quality-unlimited",
        "authorization_sha256": _h("5"),
        "execution_manifest_sha256": _h("6"),
    }
    values.update(updates)
    return T65FormalRunIdentity(**values)


def _manifest(*, omit: str | None = None) -> T65FormalExecutorManifestReceipt:
    files = [
        T65FormalExecutorCodeFile(path=path, raw_sha256=_h("7"))
        for path in sorted(REQUIRED_FORMAL_EXECUTOR_PATHS)
        if path != omit
    ]
    return build_formal_executor_manifest_receipt(
        candidate_revision="1" * 40,
        candidate_tree="2" * 40,
        files=files,
    )


def _receipt(**updates) -> T65FormalExecutionReceipt:
    identity = updates.pop("run_identity", _identity())
    manifest = updates.pop("executor_manifest", _manifest())
    response_id = _h("8")
    values = {
        "run_identity": identity,
        "executor_manifest": manifest,
        "orchestration_config_sha256": _h("9"),
        "owned_process_ids": (321,),
        "readiness_receipt_sha256": _h("a"),
        "api_probe_receipt_sha256": _h("b"),
        "persisted_capture": T65PersistedCaptureReceipt(
            run_id=identity.run_id,
            capture_run_id=identity.run_id,
            capture_artifact_sha256=_h("c"),
            sample_count=1,
        ),
        "sql_evidence": T65SqlEvidenceReceipt(
            run_id=identity.run_id,
            runtime_prefix="test_t65perf_aaaaaaaaaaaa",
            sql_receipt_sha256=_h("d"),
            session_ids=("session-1",),
            provider_invocation_count=1,
            provider_trace_id_sha256s=(response_id,),
        ),
        "provider_ledgers": (
            T65FormalProviderLedgerReceipt(
                ledger_sha256=_h("e"),
                run_id_sha256=__import__("hashlib").sha256(
                    identity.run_id.encode()
                ).hexdigest(),
                candidate_revision_sha256=__import__("hashlib").sha256(
                    identity.candidate_revision.encode()
                ).hexdigest(),
                candidate_tree_sha256=__import__("hashlib").sha256(
                    identity.candidate_tree.encode()
                ).hexdigest(),
                authorization_id_sha256=__import__("hashlib").sha256(
                    identity.authorization_id.encode()
                ).hexdigest(),
                authorization_sha256=identity.authorization_sha256,
                executor_sha256=manifest.executor_sha256,
                process_role="api",
                process_id=321,
                start_count=1,
                finish_count=1,
                success_count=1,
                error_count=0,
                sequence_first=1,
                sequence_last=1,
                provider_response_id_sha256s=(response_id,),
            ),
        ),
        "runtime_evidence": T65RuntimeEvidenceReceipt(
            run_id=identity.run_id,
            source_capture_sha256=_h("c"),
            performance_artifact_sha256=_h("f"),
            metrics_sha256=_h("0"),
        ),
        "cleanup": T65CleanupReceipt(run_id=identity.run_id),
        "report": T65ReportExecutionReceipt(
            run_id=identity.run_id,
            status="BLOCKED",
            blocker="SILICONFLOW_EMBEDDING_AUTHORIZATION_REQUIRED",
        ),
    }
    values.update(updates)
    return T65FormalExecutionReceipt(**values)


def _revalidate(receipt: T65FormalExecutionReceipt, **updates):
    values = receipt.model_dump(mode="python")
    values.update(updates)
    return T65FormalExecutionReceipt.model_validate(values)


def test_run_identity_hash_is_canonical_stable_and_strict():
    first = _identity()
    second = T65FormalRunIdentity.model_validate(first.model_dump())
    assert first.identity_sha256 == second.identity_sha256
    assert len(first.identity_sha256) == 64
    with pytest.raises(ValidationError):
        T65FormalRunIdentity(**first.model_dump(), unexpected=True)


@pytest.mark.parametrize(
    "updates",
    [
        {"run_id": "unsafe run"},
        {"candidate_revision": "not-git"},
        {"capture_plan_sha256": "A" * 64},
        {"authorization_id": "bad\nidentity"},
    ],
)
def test_run_identity_rejects_invalid_identifiers_and_hashes(updates):
    with pytest.raises(ValidationError):
        _identity(**updates)


def test_executor_manifest_hash_is_recomputed_and_required_surface_is_complete():
    manifest = _manifest()
    assert len(manifest.executor_sha256) == 64
    values = manifest.model_dump()
    values["executor_sha256"] = _h("f")
    with pytest.raises(ValidationError, match="canonical manifest"):
        T65FormalExecutorManifestReceipt(**values)
    with pytest.raises(ValidationError, match="exact formal surface"):
        _manifest(omit="app/services/t65_builtin_production_executor.py")
    assert "app/services/config.py" in REQUIRED_FORMAL_EXECUTOR_PATHS
    assert "app/services/postgres_identifiers.py" in REQUIRED_FORMAL_EXECUTOR_PATHS


def test_executor_manifest_rejects_duplicate_or_unsafe_paths():
    with pytest.raises(ValidationError):
        T65FormalExecutorCodeFile(path="../escape.py", raw_sha256=_h("1"))
    manifest = _manifest()
    values = manifest.model_dump()
    values["files"] = values["files"] + (values["files"][0],)
    with pytest.raises(ValidationError, match="uniquely sorted"):
        T65FormalExecutorManifestReceipt(**values)
    extra = T65FormalExecutorCodeFile(
        path="unreviewed/extra.py", raw_sha256=_h("8")
    )
    with pytest.raises(ValidationError, match="unexpected=.*unreviewed/extra.py"):
        build_formal_executor_manifest_receipt(
            candidate_revision=manifest.candidate_revision,
            candidate_tree=manifest.candidate_tree,
            files=(*manifest.files, extra),
        )


@pytest.mark.parametrize(
    "route",
    [
        "fixture_diagnostic",
        "saved_replay_diagnostic",
        "injected_diagnostic",
        "builtin_unavailable",
    ],
)
def test_diagnostic_routes_can_never_be_formal(route):
    assert validate_t65_formal_route(route=route, receipt=_receipt()) is False


def test_arbitrary_hash_and_direct_runtime_result_are_not_formal_receipts():
    assert validate_t65_formal_route(route="builtin_candidate", receipt=_h("a")) is False
    direct = RuntimeEvidenceBuildResult.model_construct(status="COMPLETE")
    assert validate_t65_formal_route(route="builtin_candidate", receipt=direct) is False


def test_route_resolution_is_mutually_exclusive_and_fail_closed():
    assert resolve_t65_execution_route(
        provider_mode="provider",
        source_capture_present=False,
        capture_executor_present=False,
    ) == "builtin_unavailable"
    assert resolve_t65_execution_route(
        provider_mode="provider",
        source_capture_present=False,
        capture_executor_present=False,
        builtin_enabled=True,
    ) == "builtin_candidate"
    with pytest.raises(T65FormalReceiptError, match="mutually exclusive"):
        resolve_t65_execution_route(
            provider_mode="provider",
            source_capture_present=True,
            capture_executor_present=True,
        )


def test_consistent_receipt_only_validates_as_offline_structure_not_formal():
    receipt = _receipt()
    assert validate_t65_formal_receipt_structure(
        receipt=receipt,
        expected_identity=receipt.run_identity,
        expected_executor_manifest=receipt.executor_manifest,
    ) is True
    assert validate_t65_formal_receipt_structure(
        receipt=receipt, expected_identity=_identity(run_id="other")
    ) is False
    assert validate_t65_formal_route(route="builtin_candidate", receipt=receipt) is False


def test_raw_json_roundtrip_cannot_mint_formal_eligibility():
    receipt = _receipt()
    forged = T65FormalExecutionReceipt.model_validate_json(receipt.model_dump_json())
    assert validate_t65_formal_receipt_structure(receipt=forged) is True
    assert validate_t65_formal_route(route="builtin_candidate", receipt=forged) is False
    assert validate_t65_formal_receipt_structure(receipt=receipt.model_dump_json()) is True
    assert validate_t65_formal_receipt_structure(
        receipt=json.loads(receipt.model_dump_json())
    ) is True
    assert validate_t65_formal_receipt_structure(receipt="{}") is False
    wrong_schema = receipt.model_dump(mode="json")
    wrong_schema["schema_version"] = "wrong"
    assert validate_t65_formal_receipt_structure(receipt=wrong_schema) is False


@pytest.mark.parametrize(
    "forged",
    [
        T65FormalExecutionReceipt.model_construct(),
        T65FormalExecutionReceipt.model_construct(schema_version="wrong"),
        T65FormalExecutionReceipt.model_construct(
            schema_version="t65-formal-execution-receipt-v1",
            owned_process_ids=(),
        ),
    ],
)
def test_model_construct_cannot_bypass_structure_validation(forged):
    assert validate_t65_formal_receipt_structure(receipt=forged) is False
    assert validate_t65_formal_route(route="builtin_candidate", receipt=forged) is False


def test_transport_unsupported_worker_role_is_rejected():
    values = _receipt().provider_ledgers[0].model_dump()
    values["process_role"] = "worker"
    with pytest.raises(ValidationError):
        T65FormalProviderLedgerReceipt(**values)


def test_receipt_hash_is_stable_changes_on_drift_and_has_no_origin_flag():
    first = _receipt()
    second = _receipt(api_probe_receipt_sha256=_h("c"))
    assert first.receipt_sha256 == _receipt().receipt_sha256
    assert first.receipt_sha256 != second.receipt_sha256
    serialized = json.dumps(first.model_dump(mode="json"))
    assert "evidence_origin" not in serialized
    assert "formal_evidence_eligible" not in serialized


def test_missing_or_incomplete_provider_ledger_is_rejected():
    with pytest.raises(ValidationError, match="requires sealed Provider ledgers"):
        _receipt(provider_ledgers=())
    ledger = _receipt().provider_ledgers[0].model_dump()
    ledger["complete"] = False
    with pytest.raises(ValidationError):
        T65FormalProviderLedgerReceipt(**ledger)


@pytest.mark.parametrize(
    ("sequence_first", "sequence_last"),
    [(1, 2), (1, 3), (2, 2)],
)
def test_provider_ledger_sequence_span_must_equal_attempted_requests(
    sequence_first, sequence_last
):
    ledger = _receipt().provider_ledgers[0].model_dump()
    ledger.update(
        {"sequence_first": sequence_first, "sequence_last": sequence_last}
    )
    with pytest.raises(ValidationError, match="sequence span"):
        T65FormalProviderLedgerReceipt(**ledger)


def test_event_count_pseudo_v1_receipt_is_intentionally_fail_closed():
    ledger = _receipt().provider_ledgers[0].model_dump()
    ledger["sequence_last"] = ledger["start_count"] + ledger["finish_count"]

    with pytest.raises(ValidationError, match="pseudo-v1 receipts are intentionally rejected"):
        T65FormalProviderLedgerReceipt(**ledger)


def test_provider_ledger_identity_and_process_binding_are_enforced():
    receipt = _receipt()
    ledger = receipt.provider_ledgers[0].model_dump()
    ledger["run_id_sha256"] = _h("f")
    bad = T65FormalProviderLedgerReceipt(**ledger)
    with pytest.raises(ValidationError, match="run_id_sha256 mismatch"):
        _revalidate(receipt, provider_ledgers=(bad,))
    with pytest.raises(ValidationError, match="exactly match"):
        _revalidate(receipt, owned_process_ids=(999,))


def test_sql_count_and_trace_ids_must_match_ledgers():
    receipt = _receipt()
    sql = receipt.sql_evidence.model_dump()
    sql["provider_invocation_count"] = 2
    sql["provider_trace_id_sha256s"] = (_h("8"), _h("9"))
    with pytest.raises(ValidationError, match="starts do not match"):
        _revalidate(receipt, sql_evidence=T65SqlEvidenceReceipt(**sql))
    sql["provider_invocation_count"] = 1
    sql["provider_trace_id_sha256s"] = (_h("9"),)
    with pytest.raises(ValidationError, match="response ids do not match"):
        _revalidate(receipt, sql_evidence=T65SqlEvidenceReceipt(**sql))


def test_persisted_capture_and_runtime_hash_and_run_id_must_match():
    receipt = _receipt()
    runtime = receipt.runtime_evidence.model_dump()
    runtime["source_capture_sha256"] = _h("d")
    with pytest.raises(ValidationError, match="persisted capture hash"):
        _revalidate(receipt, runtime_evidence=T65RuntimeEvidenceReceipt(**runtime))
    capture = receipt.persisted_capture.model_dump()
    capture["capture_run_id"] = "other"
    with pytest.raises(ValidationError, match="run identity mismatch"):
        _revalidate(receipt, persisted_capture=T65PersistedCaptureReceipt(**capture))


@pytest.mark.parametrize(
    "field",
    ["written_atomically", "fsync_completed", "reopened_and_verified"],
)
def test_unpersisted_or_unverified_capture_is_rejected(field):
    values = _receipt().persisted_capture.model_dump()
    values[field] = False
    with pytest.raises(ValidationError):
        T65PersistedCaptureReceipt(**values)


@pytest.mark.parametrize(
    "field",
    [
        "cleanup_complete",
        "owned_processes_exited",
        "provider_ledgers_sealed",
        "runtime_namespace_empty",
        "vector_namespace_empty",
        "sentinel_relations_preserved",
        "temporary_secrets_removed",
    ],
)
def test_incomplete_cleanup_is_rejected(field):
    values = T65CleanupReceipt(run_id="t65-formal-1").model_dump()
    values[field] = False
    with pytest.raises(ValidationError):
        T65CleanupReceipt(**values)


def test_current_builtin_profile_records_report_blocker_and_is_never_formal():
    blocked = T65ReportExecutionReceipt(
        run_id="t65-formal-1",
        status="BLOCKED",
        blocker="SILICONFLOW_EMBEDDING_AUTHORIZATION_REQUIRED",
    )
    receipt = _revalidate(_receipt(), report=blocked)
    assert validate_t65_formal_receipt_structure(receipt=receipt) is True
    assert validate_t65_formal_route(route="builtin_candidate", receipt=receipt) is False
    values = {
        "run_id": "t65-formal-1",
        "status": "COMPLETE",
        "embedding_provider": "siliconflow",
        "embedding_authorization_sha256": _h("1"),
        "report_artifact_sha256": _h("2"),
        "report_sql_receipt_sha256": _h("3"),
        "sample_count": 1,
    }
    complete = T65ReportExecutionReceipt(**values)
    with pytest.raises(ValidationError, match="authorized report blocker"):
        _revalidate(_receipt(), report=complete)
    values["embedding_authorization_sha256"] = None
    with pytest.raises(ValidationError, match="SiliconFlow authorization"):
        T65ReportExecutionReceipt(**values)
    blocked_with_provider = blocked.model_dump()
    blocked_with_provider["embedding_provider"] = "siliconflow"
    with pytest.raises(ValidationError, match="cannot expose formal artifacts"):
        T65ReportExecutionReceipt(**blocked_with_provider)
    with pytest.raises(ValidationError):
        _revalidate(receipt, execution_profile="future-report-enabled")


def test_current_builtin_profile_rejects_report_worker_or_multiple_api_ledgers():
    receipt = _receipt()
    ledger_values = receipt.provider_ledgers[0].model_dump()
    ledger_values["process_role"] = "report_worker"
    report_worker = T65FormalProviderLedgerReceipt(**ledger_values)
    with pytest.raises(ValidationError, match="exactly one API"):
        _revalidate(receipt, provider_ledgers=(report_worker,))

    second_values = receipt.provider_ledgers[0].model_dump()
    second_values["process_id"] = 322
    second_api = T65FormalProviderLedgerReceipt(**second_values)
    with pytest.raises(ValidationError, match="exactly one API"):
        _revalidate(
            receipt,
            owned_process_ids=(321, 322),
            provider_ledgers=(receipt.provider_ledgers[0], second_api),
        )


def test_orchestration_blocked_and_missing_sql_receipt_are_rejected():
    with pytest.raises(ValidationError):
        _revalidate(_receipt(), orchestration_status="BLOCKED")
    sql = _receipt().sql_evidence.model_dump()
    sql["sql_receipt_sha256"] = None
    with pytest.raises(ValidationError):
        T65SqlEvidenceReceipt(**sql)
