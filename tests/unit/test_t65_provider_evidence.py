from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.followup_provider_preflight import (
    DeepSeekDiscoverySnapshot,
    ProviderPrice,
)
from app.services.interview_quality_provider_authorization import (
    load_provider_authorization,
)
from app.services import independent_review_handoff
from app.services import t65_provider_evidence
from app.services.independent_review_handoff import (
    DetachedSignatureEvidence,
    canonical_sha256,
)
from app.services.report_calibration_dataset import (
    CalibrationDataset,
    load_calibration_dataset,
)
from app.services.t65_provider_evidence import (
    PerformanceSignal,
    SafeReportCaptureRecorder,
    SafeReportProviderAttempt,
    build_performance_observability,
    build_t65_usage_cost_ledger,
    evaluate_t65_report_preflight,
    _verify_git_candidate,
)
from app.services.t65_provider_http_transport import (
    T65DeepSeekSyncTransport,
    T65ProviderTransportIdentity,
    verify_t65_provider_attempt_ledger,
)


ROOT = Path(__file__).resolve().parents[2]
AUTHORIZATION = ROOT / "config" / "interview_quality_v1_provider_authorization.json"
GATE = ROOT / "config" / "interview_quality_v1_gate.json"
DATASET = (
    ROOT
    / "tests"
    / "golden"
    / "interview_quality_v1"
    / "report-score-calibration-v1.json"
)
CANDIDATE_REVISION = "a" * 40
CANDIDATE_TREE = "b" * 40


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _approved_dataset() -> CalibrationDataset:
    source = load_calibration_dataset(DATASET)
    cases = [
        case.model_copy(
            update={
                "annotation": case.annotation.model_copy(
                    update={
                        "review_status": "approved",
                        "reviewer_id": f"independent-{index}",
                    }
                )
            }
        )
        for index, case in enumerate(source.cases, start=1)
    ]
    return source.model_copy(update={"cases": cases})


def _discovery(*, model=True, pricing=True) -> DeepSeekDiscoverySnapshot:
    price = ProviderPrice(
        cache_hit_input_per_million=0.1,
        cache_miss_input_per_million=0.2,
        output_per_million=0.3,
    )
    return DeepSeekDiscoverySnapshot(
        observed_at="2026-08-07T00:00:00Z",
        models_endpoint_ok=True,
        model_ids=["deepseek-v4-pro"] if model else ["deepseek-v4-flash"],
        pricing_page_ok=True,
        prices={"deepseek-v4-pro": price} if pricing else {},
    )


def _preflight_files(tmp_path: Path, dataset: CalibrationDataset):
    dataset_path = _write_json(
        tmp_path / "report-score-calibration-v1.json",
        dataset.model_dump(mode="json"),
    )
    dataset_manifest = _write_json(
        tmp_path / "report-score-calibration-v1.manifest.json",
        {
            "schema_version": "report-score-calibration-manifest-v1",
            "dataset_file": dataset_path.name,
            "dataset_sha256": _sha256(dataset_path),
            "case_count": len(dataset.cases),
            "dev_case_count": sum(case.partition == "dev" for case in dataset.cases),
            "blind_case_count": sum(
                case.partition == "blind" for case in dataset.cases
            ),
            "review_status": dataset.review_status,
            "gate_eligible": dataset.gate_eligible,
        },
    )
    authorization_copy = tmp_path / AUTHORIZATION.name
    authorization_copy.write_bytes(AUTHORIZATION.read_bytes())
    gate_copy = tmp_path / GATE.name
    gate_copy.write_bytes(GATE.read_bytes())
    execution_manifest = _write_json(
        tmp_path / "execution-manifest.json",
        {
            "gate_0": {
                "gate_config_sha256": _sha256(gate_copy),
                "provider_authorization_sha256": _sha256(authorization_copy),
            },
            "authorization_revision_20260807": {
                "current_authorization_sha256": _sha256(authorization_copy)
            },
            "t65_authorization_revalidation": {
                "provider_candidate_revision": CANDIDATE_REVISION,
                "provider_candidate_tree": CANDIDATE_TREE,
            },
        },
    )
    return (
        dataset_path,
        dataset_manifest,
        gate_copy,
        authorization_copy,
        execution_manifest,
    )


def _evaluate(tmp_path: Path, dataset: CalibrationDataset, **overrides):
    files = _preflight_files(tmp_path, dataset)
    values = {
        "authorization": load_provider_authorization(files[3]),
        "dataset": dataset,
        "dataset_path": files[0],
        "dataset_manifest_path": files[1],
        "gate_config_path": files[2],
        "authorization_path": files[3],
        "execution_manifest_path": files[4],
        "candidate_revision": CANDIDATE_REVISION,
        "candidate_tree": CANDIDATE_TREE,
        "worktree_clean": True,
        "prompt_version": "stage40-evidence-v1",
        "prompt_sha256": "c" * 64,
        "rubric_version": "interview-quality-rubric-v3.3-candidate",
        "rubric_sha256": "d" * 64,
        "context_window_tokens": 128_000,
        "credential_present": True,
        "evidence_persistence_available": True,
        "discovery": _discovery(),
        "partition": "all",
        "blind_partition_released": True,
    }
    values.update(overrides)
    return evaluate_t65_report_preflight(**values)


def test_report_preflight_rejects_candidate_revision_or_tree_mismatch_before_discovery(
    tmp_path,
):
    result = _evaluate(
        tmp_path,
        _approved_dataset(),
        candidate_revision="e" * 40,
        discovery=None,
        partition="dev",
    )

    assert result.allowed is False
    assert "PROVIDER_CANDIDATE_MISMATCH" in result.hard_stop_conditions
    assert result.discovery is None
    assert result.model_available is False


def test_report_preflight_rejects_dirty_worktree_before_discovery(tmp_path):
    result = _evaluate(
        tmp_path,
        _approved_dataset(),
        worktree_clean=False,
        discovery=None,
        partition="dev",
    )

    assert "PROVIDER_CANDIDATE_MISMATCH" in result.hard_stop_conditions


def test_report_preflight_without_discovery_can_never_authorize_case_data(tmp_path):
    result = _evaluate(
        tmp_path,
        _approved_dataset(),
        discovery=None,
        partition="dev",
    )

    assert result.allowed is False
    assert result.discovery is None
    assert "MODEL_VERSION_DRIFT" in result.hard_stop_conditions
    assert "USAGE_METERING_UNAVAILABLE" in result.hard_stop_conditions


def test_report_preflight_blocks_pending_independent_review_and_unreleased_blind_partition(
    tmp_path,
):
    result = _evaluate(
        tmp_path,
        load_calibration_dataset(DATASET),
        discovery=None,
        blind_partition_released=False,
    )

    assert "INDEPENDENT_REVIEW_NOT_COMPLETE" in result.hard_stop_conditions
    assert "BLIND_PARTITION_NOT_RELEASED" in result.hard_stop_conditions
    assert result.gate_eligible is False


def test_report_preflight_accepts_frozen_approved_exact_model_inputs(tmp_path):
    result = _evaluate(tmp_path, _approved_dataset())

    assert result.allowed is True
    assert result.model_available is True
    assert result.pricing_available is True
    assert result.candidate_manifest_match is True
    assert result.redaction_preflight_passed is True


def test_report_preflight_requires_exact_model_pricing_and_128k_context_capability(
    tmp_path,
):
    result = _evaluate(
        tmp_path,
        _approved_dataset(),
        context_window_tokens=64_000,
        discovery=_discovery(model=False, pricing=False),
    )

    assert "CONTEXT_WINDOW_CAPABILITY_UNAVAILABLE" in result.hard_stop_conditions
    assert "MODEL_VERSION_DRIFT" in result.hard_stop_conditions


@pytest.mark.parametrize(
    "stage,payload",
    [
        ("raw_content", {"payload": {"score": 80}}),
        ("structured_payload", {"request_headers": {"x": "secret"}}),
        ("normalized_payload", {"api_key": "plain-key"}),
        ("normalized_payload", {"provider_secret": "plain-secret"}),
    ],
)
def test_safe_report_capture_rejects_unsafe_stage_and_credential_fields(
    stage, payload
):
    recorder = SafeReportCaptureRecorder()

    with pytest.raises(ValueError, match="unsafe|blocked"):
        recorder.record(session_id="safe-session", stage=stage, payload=payload)


def test_safe_report_capture_is_in_memory_allow_listed_and_consumed_once():
    recorder = SafeReportCaptureRecorder()
    recorder.record(
        session_id="safe-session",
        stage="normalized_payload",
        payload={
            "payload": {
                "score": 82,
                "content": "provider-private-canary",
            }
        },
    )

    capture = recorder.consume()
    serialized = json.dumps(capture, ensure_ascii=False)
    assert capture["normalized_payload"]["top_level_keys"] == ["payload"]
    assert len(capture["normalized_payload"]["payload_sha256"]) == 64
    assert "provider-private-canary" not in serialized
    assert recorder.consume() == {}


def test_safe_report_capture_redacts_generated_semantic_fields_to_metadata():
    recorder = SafeReportCaptureRecorder()
    canaries = {
        "answer": "candidate-answer-canary",
        "observed": ["provider-observed-canary"],
        "rationale": "provider-rationale-canary",
        "critique": "provider-critique-canary",
        "better_answer": "provider-better-answer-canary",
        "response_id": "provider-response-id-canary",
    }
    recorder.record(
        session_id="safe-session",
        stage="normalized_payload",
        payload={"payload": canaries},
    )

    serialized = json.dumps(recorder.consume(), ensure_ascii=False)
    for canary in canaries.values():
        if isinstance(canary, str):
            assert canary not in serialized
    assert "provider-observed-canary" not in serialized


def test_complete_report_attempt_requires_metered_usage_latency_and_payload():
    with pytest.raises(ValidationError, match="complete attempts require"):
        SafeReportProviderAttempt(
            case_id="case-1",
            partition="dev",
            run_number=1,
            response_sha256="a" * 64,
            provider_model="deepseek-v4-pro",
            provider_attempts=1,
            provider_metered_attempts=0,
            retry_count=0,
            capture_status="complete",
        )


def _usage_manifest(
    path: Path,
    *,
    dimension: str,
    attempted=1,
    metered=1,
    retries=0,
    input_tokens=100,
    output_tokens=20,
    cached_input_tokens=10,
    estimated_cost=0.01,
) -> Path:
    return _write_json(
        path,
        {
            "dimension": dimension,
            "run_id": f"run-{dimension}",
            "schema_version": {
                "initial_question": "initial-question-quality-run-v1",
                "followup": "followup-quality-run-v1",
                "report_scoring": "t65-report-scoring-run-v1",
            }[dimension],
            "authorization_sha256": "f" * 64,
            "authorization_id": "authorization-test",
            "provider": "DeepSeek",
            "model": "deepseek-v4-pro",
            "candidate_revision": CANDIDATE_REVISION,
            "candidate_tree": CANDIDATE_TREE,
            "discovery_requests": 2,
            "inference_attempted": attempted,
            "inference_metered": metered,
            "retries": retries,
            "planned_inference_requests": attempted - retries,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
            "estimated_cost": estimated_cost,
            "quality_status": "PASS",
            "decision": "PASS",
            "hard_stop_conditions": [],
            "mode": "provider",
            "scope": "full",
            "evidence_origin": "live_provider",
            "formal_evidence_eligible": True,
            "worktree_clean": True,
            "provider_attempt_receipt_sha256": "9" * 64,
        },
    )


def _source_bindings(paths: list[Path]) -> dict[str, str]:
    return {
        json.loads(path.read_text(encoding="utf-8"))["dimension"]: _sha256(path)
        for path in paths
    }


def _attach_attempt_receipts(
    paths: list[Path], *, revision: str, tree: str
) -> tuple[dict[str, Path], dict[str, Path]]:
    receipts: dict[str, Path] = {}
    ledgers: dict[str, Path] = {}
    for index, path in enumerate(paths, start=1):
        source = json.loads(path.read_text(encoding="utf-8"))
        dimension = source["dimension"]
        attempted = source["inference_attempted"]
        role = "report_worker" if dimension == "report_scoring" else "api"
        identity = T65ProviderTransportIdentity(
            run_id=source["run_id"],
            process_role=role,
            candidate_revision=revision,
            candidate_tree=tree,
            authorization_id=source["authorization_id"],
            authorization_sha256=source["authorization_sha256"],
            executor_sha256="8" * 64,
        )
        ledger_directory = path.parent / f"{dimension}-attempt-ledger"
        transport = T65DeepSeekSyncTransport(
            delegate=httpx.MockTransport(
                lambda request, dimension=dimension: httpx.Response(
                    200,
                    headers={
                        "x-request-id": (
                            f"fixture-{dimension}-{request.headers['x-test-sequence']}"
                        )
                    },
                )
            ),
            ledger_directory=ledger_directory,
            identity=identity,
            expected_identity=identity,
        )
        with httpx.Client(transport=transport) as client:
            for sequence in range(1, attempted + 1):
                client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"x-test-sequence": str(sequence)},
                    json={"model": "deepseek-v4-pro", "messages": []},
                )
        recomputed = verify_t65_provider_attempt_ledger(
            transport.ledger_path,
            expected_identity=identity,
            expected_process_id=os.getpid(),
        )
        receipt_path = path.with_name(path.stem + "-receipt.json")
        receipt = recomputed.as_dict()
        _write_json(receipt_path, receipt)
        source.update(
            provider_attempt_receipt_sha256=_sha256(receipt_path),
            provider_attempt_ledger_sha256=receipt["ledger_sha256"],
            provider_attempt_process_role=role,
            provider_attempt_process_id=os.getpid(),
            executor_sha256="8" * 64,
        )
        _write_json(path, source)
        receipts[dimension] = receipt_path
        ledgers[dimension] = transport.ledger_path
    return receipts, ledgers


def _execution_manifest(
    path: Path, *, sources: list[Path], revision: str, tree: str
) -> Path:
    return _write_json(
        path,
        {
            "schema_version": "interview-quality-v1-t65-control-manifest-v1",
            "t65_provider_evidence": {
                "candidate_revision": revision,
                "candidate_tree": tree,
                "authorization_sha256": "f" * 64,
                "authorization_id": "authorization-test",
                "provider": "DeepSeek",
                "model": "deepseek-v4-pro",
                "executor_sha256": "8" * 64,
                "source_manifest_sha256s": _source_bindings(sources),
            },
        },
    )


def test_usage_ledger_keeps_missing_tokens_or_cost_null_and_blocks(tmp_path):
    paths = [
        _usage_manifest(tmp_path / f"{name}.json", dimension=name)
        for name in ("initial_question", "followup")
    ]
    paths.append(
        _usage_manifest(
            tmp_path / "report.json",
            dimension="report_scoring",
            output_tokens=None,
            estimated_cost=None,
        )
    )

    ledger = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        expected_source_manifest_sha256s=_source_bindings(paths),
        execution_manifest_sha256="e" * 64,
    )

    assert ledger.quality_status == "BLOCKED_USAGE_INCOMPLETE"
    assert ledger.totals["output_tokens"] is None
    assert ledger.totals["estimated_cost"] is None
    assert "USAGE_METERING_UNAVAILABLE" in ledger.hard_stop_conditions


def test_usage_ledger_complete_self_report_sums_but_cannot_formally_pass(tmp_path):
    paths = [
        _usage_manifest(
            tmp_path / f"{name}.json",
            dimension=name,
            attempted=2,
            metered=2,
            retries=0,
        )
        for name in ("initial_question", "followup", "report_scoring")
    ]

    ledger = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        expected_source_manifest_sha256s=_source_bindings(paths),
        execution_manifest_sha256="e" * 64,
    )

    assert ledger.quality_status == "BLOCKED_USAGE_INCOMPLETE"
    assert "EXTERNAL_GATE_AUTHORITY_NOT_TRUSTED" in ledger.hard_stop_conditions
    assert "PROVIDER_CANDIDATE_MISMATCH" in ledger.hard_stop_conditions
    assert ledger.execution_signature_verified is False
    assert ledger.candidate_repository_verified is False
    assert ledger.totals["inference_attempted"] == 6
    assert ledger.totals["retries"] == 0
    assert ledger.totals["estimated_cost"] == pytest.approx(0.03)


def test_usage_ledger_accepts_synthetic_trusted_key_signature_and_verified_candidate(
    tmp_path, monkeypatch
):
    repository = tmp_path / "candidate"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t65-test@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T65 Test"], cwd=repository, check=True
    )
    (repository / "candidate.txt").write_text("frozen\n", encoding="utf-8")
    subprocess.run(["git", "add", "candidate.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "freeze"], cwd=repository, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "show", "-s", "--format=%T", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    paths = [
        _usage_manifest(tmp_path / f"{name}.json", dimension=name)
        for name in ("initial_question", "followup", "report_scoring")
    ]
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["candidate_revision"] = revision
        payload["candidate_tree"] = tree
        _write_json(path, payload)
    receipt_paths, ledger_paths = _attach_attempt_receipts(
        paths, revision=revision, tree=tree
    )
    execution_manifest = _execution_manifest(
        tmp_path / "execution-manifest.json",
        sources=paths,
        revision=revision,
        tree=tree,
    )

    execution_sha = _sha256(execution_manifest)
    key = Ed25519PrivateKey.generate()
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_sha = hashlib.sha256(public_raw).hexdigest()
    signature_payload = {
        "schema_version": "detached-signature-evidence-v1",
        "signature_id": "t65-control-signature-test",
        "signer_authority_id": "external-t65-coordinator-test",
        "signed_artifact_sha256": execution_sha,
        "algorithm": "ed25519-sha256-binding-v1",
        "public_key_sha256": public_sha,
        "signature_base64": base64.b64encode(
            key.sign(bytes.fromhex(execution_sha))
        ).decode("ascii"),
        "signed_at": datetime.now(timezone.utc),
        "synthetic_fixture": False,
    }
    signature_payload["signature_record_sha256"] = canonical_sha256(
        signature_payload
    )
    signature = DetachedSignatureEvidence.model_validate(signature_payload)
    monkeypatch.setattr(
        independent_review_handoff,
        "TRUSTED_GATE_AUTHORITY_PUBLIC_KEY_SHA256",
        frozenset({public_sha}),
    )
    monkeypatch.setattr(
        t65_provider_evidence,
        "_verify_git_candidate",
        lambda candidate, *, expected_revision, expected_tree: (
            candidate == repository
            and expected_revision == revision
            and expected_tree == tree
        ),
    )

    ledger = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=revision,
        expected_tree=tree,
        authorization_sha256="f" * 64,
        expected_authorization_id="authorization-test",
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        execution_manifest_path=execution_manifest,
        receipt_paths_by_dimension=receipt_paths,
        ledger_paths_by_dimension=ledger_paths,
        execution_signature=signature,
        execution_public_key_pem=public_pem,
        execution_authority_id="external-t65-coordinator-test",
        candidate_repository=repository,
    )

    assert ledger.quality_status == "PASS"
    assert ledger.execution_signature_verified is True
    assert ledger.candidate_repository_verified is True
    assert ledger.execution_authority_public_key_sha256 == public_sha


@pytest.mark.parametrize(
    ("mutation", "expected_stop"),
    [
        ("missing_mapping", "SOURCE_CAPTURE_INCOMPLETE"),
        ("arbitrary_source_hash", "SOURCE_CAPTURE_HASH_MISMATCH"),
        ("extra_raw_field", "SOURCE_CAPTURE_INCOMPLETE"),
        ("coerced_count", "SOURCE_CAPTURE_INCOMPLETE"),
        ("candidate_identity", "SOURCE_CAPTURE_HASH_MISMATCH"),
        ("process_identity", "SOURCE_CAPTURE_INCOMPLETE"),
        ("invalid_source_type", "SOURCE_CAPTURE_INCOMPLETE"),
        ("count_drift", "SOURCE_CAPTURE_INCOMPLETE"),
        ("ledger_binding", "SOURCE_CAPTURE_HASH_MISMATCH"),
        ("incomplete", "SOURCE_CAPTURE_INCOMPLETE"),
    ],
)
def test_usage_ledger_fails_closed_on_untrusted_receipt_artifacts(
    tmp_path, mutation, expected_stop
):
    paths = [
        _usage_manifest(tmp_path / f"{name}.json", dimension=name)
        for name in ("initial_question", "followup", "report_scoring")
    ]
    receipts, ledgers = _attach_attempt_receipts(
        paths, revision=CANDIDATE_REVISION, tree=CANDIDATE_TREE
    )
    source_path = paths[1]
    receipt_path = receipts["followup"]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    if mutation == "missing_mapping":
        del receipts["followup"]
    elif mutation == "arbitrary_source_hash":
        source["provider_attempt_receipt_sha256"] = "a" * 64
    elif mutation == "extra_raw_field":
        receipt["raw_response"] = "must never be trusted"
    elif mutation == "coerced_count":
        receipt["start_count"] = "1"
    elif mutation == "candidate_identity":
        receipt["candidate_revision_sha256"] = "a" * 64
    elif mutation == "process_identity":
        source["provider_attempt_process_id"] += 1
    elif mutation == "invalid_source_type":
        source["provider_attempt_process_role"] = ["api"]
    elif mutation == "count_drift":
        receipt.update(
            start_count=2,
            finish_count=2,
            success_count=2,
            sequence_last=2,
            response_id_missing_count=2,
        )
    elif mutation == "ledger_binding":
        receipt["ledger_sha256"] = "b" * 64
    else:
        receipt["complete"] = False

    if mutation in {
        "extra_raw_field",
        "coerced_count",
        "candidate_identity",
        "count_drift",
        "ledger_binding",
        "incomplete",
    }:
        _write_json(receipt_path, receipt)
        source["provider_attempt_receipt_sha256"] = _sha256(receipt_path)
    _write_json(source_path, source)
    execution_manifest = _execution_manifest(
        tmp_path / "execution-manifest.json",
        sources=paths,
        revision=CANDIDATE_REVISION,
        tree=CANDIDATE_TREE,
    )

    ledger = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_authorization_id="authorization-test",
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        execution_manifest_path=execution_manifest,
        receipt_paths_by_dimension=receipts,
        ledger_paths_by_dimension=ledgers,
    )

    followup = next(item for item in ledger.runs if item.dimension == "followup")
    assert followup.status == "BLOCKED"
    assert expected_stop in ledger.hard_stop_conditions


@pytest.mark.parametrize("artifact_kind", ["receipt", "ledger"])
@pytest.mark.parametrize("alias_kind", ["same_path", "copy", "hardlink"])
def test_usage_ledger_rejects_cross_dimension_artifact_reuse(
    tmp_path, artifact_kind, alias_kind
):
    paths = [
        _usage_manifest(tmp_path / f"{name}.json", dimension=name)
        for name in ("initial_question", "followup", "report_scoring")
    ]
    receipts, ledgers = _attach_attempt_receipts(
        paths, revision=CANDIDATE_REVISION, tree=CANDIDATE_TREE
    )
    mappings = receipts if artifact_kind == "receipt" else ledgers
    original = mappings["initial_question"]
    if alias_kind == "same_path":
        replacement = original
    else:
        replacement = tmp_path / f"aliased-{artifact_kind}-{alias_kind}"
        if alias_kind == "copy":
            shutil.copyfile(original, replacement)
        else:
            os.link(original, replacement)
    mappings["followup"] = replacement

    followup_path = next(
        path
        for path in paths
        if json.loads(path.read_text(encoding="utf-8"))["dimension"] == "followup"
    )
    followup = json.loads(followup_path.read_text(encoding="utf-8"))
    binding_field = (
        "provider_attempt_receipt_sha256"
        if artifact_kind == "receipt"
        else "provider_attempt_ledger_sha256"
    )
    followup[binding_field] = _sha256(replacement)
    _write_json(followup_path, followup)
    execution_manifest = _execution_manifest(
        tmp_path / "execution-manifest.json",
        sources=paths,
        revision=CANDIDATE_REVISION,
        tree=CANDIDATE_TREE,
    )

    result = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_authorization_id="authorization-test",
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        execution_manifest_path=execution_manifest,
        receipt_paths_by_dimension=receipts,
        ledger_paths_by_dimension=ledgers,
    )

    assert result.quality_status == "BLOCKED_USAGE_INCOMPLETE"
    assert "SOURCE_CAPTURE_INCOMPLETE" in result.hard_stop_conditions


def test_usage_ledger_rejects_reused_run_role_process_identity(tmp_path):
    paths = [
        _usage_manifest(tmp_path / f"{name}.json", dimension=name)
        for name in ("initial_question", "followup", "report_scoring")
    ]
    initial = json.loads(paths[0].read_text(encoding="utf-8"))
    followup = json.loads(paths[1].read_text(encoding="utf-8"))
    followup["run_id"] = initial["run_id"]
    _write_json(paths[1], followup)
    receipts, ledgers = _attach_attempt_receipts(
        paths, revision=CANDIDATE_REVISION, tree=CANDIDATE_TREE
    )
    execution_manifest = _execution_manifest(
        tmp_path / "execution-manifest.json",
        sources=paths,
        revision=CANDIDATE_REVISION,
        tree=CANDIDATE_TREE,
    )

    result = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_authorization_id="authorization-test",
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        execution_manifest_path=execution_manifest,
        receipt_paths_by_dimension=receipts,
        ledger_paths_by_dimension=ledgers,
    )

    assert result.quality_status == "BLOCKED_USAGE_INCOMPLETE"
    assert "SOURCE_CAPTURE_INCOMPLETE" in result.hard_stop_conditions


def test_usage_ledger_reads_execution_manifest_from_one_secure_snapshot(
    tmp_path, monkeypatch
):
    paths = [
        _usage_manifest(tmp_path / f"{name}.json", dimension=name)
        for name in ("initial_question", "followup", "report_scoring")
    ]
    receipts, ledgers = _attach_attempt_receipts(
        paths, revision=CANDIDATE_REVISION, tree=CANDIDATE_TREE
    )
    execution_manifest = _execution_manifest(
        tmp_path / "external-control-manifest.json",
        sources=paths,
        revision=CANDIDATE_REVISION,
        tree=CANDIDATE_TREE,
    )
    original = t65_provider_evidence._read_json_snapshot
    reads = []

    def tracked_snapshot(path):
        if Path(path) == execution_manifest:
            reads.append(Path(path))
        return original(path)

    monkeypatch.setattr(
        t65_provider_evidence, "_read_json_snapshot", tracked_snapshot
    )

    result = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_authorization_id="authorization-test",
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        execution_manifest_path=execution_manifest,
        receipt_paths_by_dimension=receipts,
        ledger_paths_by_dimension=ledgers,
    )

    assert result.execution_manifest_sha256 == _sha256(execution_manifest)
    assert reads == [execution_manifest]


def test_usage_ledger_rejects_signed_sha_spliced_with_independent_bindings(
    tmp_path, monkeypatch
):
    paths = [
        _usage_manifest(tmp_path / f"{name}.json", dimension=name)
        for name in ("initial_question", "followup", "report_scoring")
    ]
    receipts, ledgers = _attach_attempt_receipts(
        paths, revision=CANDIDATE_REVISION, tree=CANDIDATE_TREE
    )
    signed_sha = "e" * 64
    key = Ed25519PrivateKey.generate()
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_sha = hashlib.sha256(public_raw).hexdigest()
    signature_payload = {
        "schema_version": "detached-signature-evidence-v1",
        "signature_id": "splice-attack-signature",
        "signer_authority_id": "external-t65-coordinator-test",
        "signed_artifact_sha256": signed_sha,
        "algorithm": "ed25519-sha256-binding-v1",
        "public_key_sha256": public_sha,
        "signature_base64": base64.b64encode(
            key.sign(bytes.fromhex(signed_sha))
        ).decode("ascii"),
        "signed_at": datetime.now(timezone.utc),
        "synthetic_fixture": False,
    }
    signature_payload["signature_record_sha256"] = canonical_sha256(
        signature_payload
    )
    monkeypatch.setattr(
        independent_review_handoff,
        "TRUSTED_GATE_AUTHORITY_PUBLIC_KEY_SHA256",
        frozenset({public_sha}),
    )
    monkeypatch.setattr(
        t65_provider_evidence, "_verify_git_candidate", lambda *a, **k: True
    )

    result = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_authorization_id="authorization-test",
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        expected_source_manifest_sha256s=_source_bindings(paths),
        execution_manifest_sha256=signed_sha,
        expected_executor_sha256="8" * 64,
        receipt_paths_by_dimension=receipts,
        ledger_paths_by_dimension=ledgers,
        execution_signature=DetachedSignatureEvidence.model_validate(
            signature_payload
        ),
        execution_public_key_pem=public_pem,
        execution_authority_id="external-t65-coordinator-test",
        candidate_repository=tmp_path,
    )

    assert result.execution_signature_verified is True
    assert result.candidate_repository_verified is True
    assert result.quality_status == "BLOCKED_USAGE_INCOMPLETE"
    assert "SOURCE_CAPTURE_INCOMPLETE" in result.hard_stop_conditions


def test_git_candidate_check_uses_minimal_environment_and_fixed_config_guards(
    tmp_path, monkeypatch
):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / ".git" / "objects" / "info").mkdir(parents=True)
    (actual / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
    )
    trusted_git = tmp_path / "trusted" / "git.exe"
    trusted_git.parent.mkdir()
    _write_native_test_binary(trusted_git)
    trusted_hash = hashlib.sha256(trusted_git.read_bytes()).hexdigest()
    revision = "a" * 40
    tree = "b" * 40
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "--get-regexp" in argv:
            return SimpleNamespace(stdout="", returncode=1)
        if "--show-toplevel" in argv:
            return SimpleNamespace(stdout=str(actual) + "\n", returncode=0)
        if "HEAD^{commit}" in argv:
            return SimpleNamespace(stdout=revision + "\n", returncode=0)
        if "--format=%T" in argv:
            return SimpleNamespace(stdout=tree + "\n", returncode=0)
        if "ls-files" in argv:
            return SimpleNamespace(stdout="H candidate.txt\n", returncode=0)
        assert "--porcelain=v1" in argv
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(
        t65_provider_evidence, "_REAL_GIT_BINARY_CANDIDATES", (trusted_git,)
    )
    monkeypatch.setattr(
        t65_provider_evidence,
        "TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        MappingProxyType(
            {
                t65_provider_evidence._git_candidate_key(
                    trusted_git
                ): frozenset({trusted_hash})
            }
        ),
    )
    monkeypatch.setattr(t65_provider_evidence.subprocess, "run", fake_run)
    for name, value in {
        "HOME": str(tmp_path / "malicious-home"),
        "USERPROFILE": str(tmp_path / "malicious-profile"),
        "XDG_CONFIG_HOME": str(tmp_path / "malicious-xdg"),
        "LD_PRELOAD": str(tmp_path / "malicious.so"),
        "DYLD_INSERT_LIBRARIES": str(tmp_path / "malicious.dylib"),
        "PATH": str(tmp_path / "malicious-path"),
        "GIT_DIR": str(tmp_path / "redirected.git"),
        "GIT_CONFIG_GLOBAL": str(tmp_path / "malicious.gitconfig"),
    }.items():
        monkeypatch.setenv(name, value)

    assert _verify_git_candidate(
        actual, expected_revision=revision, expected_tree=tree
    ) is True
    assert len(calls) == 6
    for argv, kwargs in calls:
        assert argv[:10] == [
            str(trusted_git),
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            f"core.attributesFile={os.devnull}",
        ]
        assert argv[10:12] == ["-C", str(actual.resolve())]
        assert kwargs["cwd"] == trusted_git.parent
        environment = kwargs["env"]
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
        assert environment["GIT_OPTIONAL_LOCKS"] == "0"
        assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert environment["GIT_TERMINAL_PROMPT"] == "0"
        assert environment["LANG"] == "C"
        assert environment["LC_ALL"] == "C"
        for prohibited in (
            "HOME",
            "USERPROFILE",
            "XDG_CONFIG_HOME",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
            "PATH",
            "GIT_DIR",
        ):
            assert prohibited not in environment


def _write_minimal_pe(path: Path) -> None:
    payload = bytearray(128)
    payload[:2] = b"MZ"
    payload[60:64] = (64).to_bytes(4, "little")
    payload[64:68] = b"PE\x00\x00"
    path.write_bytes(payload)


def _write_native_test_binary(path: Path) -> None:
    shutil.copy2(sys.executable, path)


@pytest.mark.parametrize(
    "relative",
    (
        "commondir",
        "config.worktree",
        "objects/info/alternates",
    ),
)
def test_git_candidate_rejects_external_repository_indirection_before_execution(
    tmp_path, monkeypatch, relative
):
    repository = tmp_path / "candidate"
    (repository / ".git" / "objects" / "info").mkdir(parents=True)
    (repository / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
    )
    target = repository / ".git" / Path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("external-object-store\n", encoding="utf-8")
    monkeypatch.setattr(
        t65_provider_evidence,
        "_trusted_git_executable",
        lambda: (_ for _ in ()).throw(
            AssertionError("Git selection must not occur for redirected metadata")
        ),
    )

    assert _verify_git_candidate(
        repository,
        expected_revision="a" * 40,
        expected_tree="b" * 40,
    ) is False


def test_git_metadata_resolver_supports_bounded_linked_worktree_layout(tmp_path):
    repository = tmp_path / "candidate"
    repository.mkdir()
    common = tmp_path / "common" / ".git"
    git_directory = common / "worktrees" / "candidate"
    git_directory.mkdir(parents=True)
    (common / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
    )
    (git_directory / "commondir").write_text("../..\n", encoding="utf-8")
    (repository / ".git").write_text(
        f"gitdir: {git_directory.as_posix()}\n", encoding="utf-8"
    )

    assert t65_provider_evidence._resolve_git_metadata(repository) == (
        git_directory.resolve(),
        common.resolve(),
    )


def test_git_candidate_rejects_local_include_and_external_filter_config(
    tmp_path, monkeypatch
):
    repository = tmp_path / "candidate"
    (repository / ".git" / "objects" / "info").mkdir(parents=True)
    (repository / ".git" / "config").write_text(
        "[include]\n\tpath = malicious.config\n", encoding="utf-8"
    )
    binary = tmp_path / "trusted" / "git.exe"
    binary.parent.mkdir()
    _write_native_test_binary(binary)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(
        t65_provider_evidence, "_REAL_GIT_BINARY_CANDIDATES", (binary,)
    )
    monkeypatch.setattr(
        t65_provider_evidence,
        "TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        MappingProxyType(
            {
                t65_provider_evidence._git_candidate_key(binary): frozenset(
                    {digest}
                )
            }
        ),
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert "--get-regexp" in argv
        return SimpleNamespace(stdout="include.path\nfilter.evil.process\n", returncode=0)

    monkeypatch.setattr(t65_provider_evidence.subprocess, "run", fake_run)
    assert _verify_git_candidate(
        repository,
        expected_revision="a" * 40,
        expected_tree="b" * 40,
    ) is False
    assert len(calls) == 1


def test_git_hash_is_rechecked_around_every_process_execution(tmp_path, monkeypatch):
    repository = tmp_path / "candidate"
    (repository / ".git" / "objects" / "info").mkdir(parents=True)
    (repository / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
    )
    binary = tmp_path / "trusted" / "git.exe"
    binary.parent.mkdir()
    _write_native_test_binary(binary)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(
        t65_provider_evidence, "_REAL_GIT_BINARY_CANDIDATES", (binary,)
    )
    monkeypatch.setattr(
        t65_provider_evidence,
        "TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        MappingProxyType(
            {
                t65_provider_evidence._git_candidate_key(binary): frozenset(
                    {digest}
                )
            }
        ),
    )

    def mutate_during_run(argv, **kwargs):
        binary.write_bytes(binary.read_bytes() + b"replaced")
        return SimpleNamespace(stdout="", returncode=1)

    monkeypatch.setattr(
        t65_provider_evidence.subprocess, "run", mutate_during_run
    )
    assert _verify_git_candidate(
        repository,
        expected_revision="a" * 40,
        expected_tree="b" * 40,
    ) is False


@pytest.mark.parametrize("index_line", ("h candidate.txt\n", "S candidate.txt\n"))
def test_git_candidate_rejects_assume_unchanged_and_skip_worktree_index_flags(
    tmp_path, monkeypatch, index_line
):
    repository = tmp_path / "candidate"
    (repository / ".git" / "objects" / "info").mkdir(parents=True)
    (repository / ".git" / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8"
    )
    trusted_git = tmp_path / "trusted" / "git.exe"
    trusted_git.parent.mkdir()
    trusted_git.write_bytes(b"execution is mocked")
    revision = "a" * 40
    tree = "b" * 40
    monkeypatch.setattr(
        t65_provider_evidence, "_trusted_git_executable", lambda: trusted_git
    )

    def fake_run(git, argv, *, common):
        if "--get-regexp" in argv:
            return SimpleNamespace(stdout="", returncode=1)
        if "--show-toplevel" in argv:
            return SimpleNamespace(stdout=str(repository) + "\n", returncode=0)
        if "HEAD^{commit}" in argv:
            return SimpleNamespace(stdout=revision + "\n", returncode=0)
        if "--format=%T" in argv:
            return SimpleNamespace(stdout=tree + "\n", returncode=0)
        if "--porcelain=v1" in argv:
            return SimpleNamespace(stdout="", returncode=0)
        assert "ls-files" in argv
        return SimpleNamespace(stdout=index_line, returncode=0)

    monkeypatch.setattr(t65_provider_evidence, "_run_trusted_git", fake_run)
    assert _verify_git_candidate(
        repository,
        expected_revision=revision,
        expected_tree=tree,
    ) is False


def test_git_no_replace_objects_disables_real_local_replace_refs(tmp_path):
    git_executable = shutil.which("git")
    if git_executable is None:
        pytest.skip("Git is unavailable for replace-ref semantics probe")
    repository = tmp_path / "replace-probe"
    repository.mkdir()

    def run_git(*args, environment=None):
        return subprocess.run(
            [git_executable, *args],
            cwd=Path(git_executable).parent,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    run_git("-C", str(repository), "init", "-q")
    run_git("-C", str(repository), "config", "user.email", "t65@example.invalid")
    run_git("-C", str(repository), "config", "user.name", "T65 Test")
    payload = repository / "candidate.txt"
    payload.write_text("original\n", encoding="utf-8")
    run_git("-C", str(repository), "add", "candidate.txt")
    run_git("-C", str(repository), "commit", "-q", "-m", "original")
    original_revision = run_git("-C", str(repository), "rev-parse", "HEAD")
    original_tree = run_git(
        "-C", str(repository), "show", "-s", "--format=%T", original_revision
    )
    payload.write_text("replacement\n", encoding="utf-8")
    run_git("-C", str(repository), "commit", "-q", "-am", "replacement")
    replacement_revision = run_git("-C", str(repository), "rev-parse", "HEAD")
    replacement_tree = run_git(
        "-C", str(repository), "show", "-s", "--format=%T", replacement_revision
    )
    run_git("-C", str(repository), "replace", original_revision, replacement_revision)

    replaced_tree = run_git(
        "-C", str(repository), "show", "-s", "--format=%T", original_revision
    )
    guarded_environment = t65_provider_evidence._trusted_git_environment()
    guarded_tree = run_git(
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-C",
        str(repository),
        "show",
        "-s",
        "--format=%T",
        original_revision,
        environment=guarded_environment,
    )

    assert replaced_tree == replacement_tree
    assert replacement_tree != original_tree
    assert guarded_environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert guarded_tree == original_tree


def test_windows_trusted_path_key_is_case_normalized(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows path normalization contract")
    binary = tmp_path / "MixedCase" / "Git.EXE"
    binary.parent.mkdir()
    _write_native_test_binary(binary)
    variant = Path(str(binary).swapcase())
    assert t65_provider_evidence._git_candidate_key(binary) == (
        t65_provider_evidence._git_candidate_key(variant)
    )


def test_git_binary_trust_is_empty_frozen_and_rejects_untrusted_raw_hash(
    tmp_path, monkeypatch
):
    assert not t65_provider_evidence.TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH
    with pytest.raises(TypeError):
        t65_provider_evidence.TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH["path"] = (
            frozenset({"a" * 64})
        )
    assert all(
        "/cmd/git.exe" not in str(path).replace("\\", "/").casefold()
        for path in t65_provider_evidence._REAL_GIT_BINARY_CANDIDATES
    )

    binary = tmp_path / "git.exe"
    _write_minimal_pe(binary)
    monkeypatch.setattr(
        t65_provider_evidence, "_REAL_GIT_BINARY_CANDIDATES", (binary,)
    )
    monkeypatch.setattr(
        t65_provider_evidence,
        "TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        MappingProxyType({}),
    )
    assert t65_provider_evidence._trusted_git_executable() is None

    correct_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(
        t65_provider_evidence,
        "TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        MappingProxyType(
            {
                t65_provider_evidence._git_candidate_key(binary): frozenset(
                    {correct_hash}
                )
            }
        ),
    )
    assert t65_provider_evidence._trusted_git_executable() is None

    _write_native_test_binary(binary)
    correct_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(
        t65_provider_evidence,
        "TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        MappingProxyType(
            {
                t65_provider_evidence._git_candidate_key(binary): frozenset(
                    {correct_hash}
                )
            }
        ),
    )
    assert t65_provider_evidence._trusted_git_executable() == binary.resolve()
    binary.write_bytes(binary.read_bytes() + b"mutated")
    assert t65_provider_evidence._trusted_git_executable() is None

    wrong_hash = "f" * 64
    monkeypatch.setattr(
        t65_provider_evidence,
        "TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        MappingProxyType(
            {
                t65_provider_evidence._git_candidate_key(binary): frozenset(
                    {wrong_hash}
                )
            }
        ),
    )
    assert t65_provider_evidence._trusted_git_executable() is None


def test_git_binary_selector_rejects_fake_file_even_when_hash_is_trusted(
    tmp_path, monkeypatch
):
    fake = tmp_path / "git.exe"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_hash = hashlib.sha256(fake.read_bytes()).hexdigest()
    monkeypatch.setattr(
        t65_provider_evidence, "_REAL_GIT_BINARY_CANDIDATES", (fake,)
    )
    monkeypatch.setattr(
        t65_provider_evidence,
        "TRUSTED_GIT_EXECUTABLE_SHA256_BY_PATH",
        MappingProxyType(
            {
                t65_provider_evidence._git_candidate_key(fake): frozenset(
                    {fake_hash}
                )
            }
        ),
    )

    assert t65_provider_evidence._trusted_git_executable() is None


@pytest.mark.parametrize(
    "field,value,stop",
    [
        ("authorization_sha256", "e" * 64, "SOURCE_CAPTURE_HASH_MISMATCH"),
        ("provider", "OtherProvider", "PROVIDER_OR_MODEL_MISMATCH"),
        ("model", "deepseek-v4-flash", "PROVIDER_OR_MODEL_MISMATCH"),
        ("quality_status", "BLOCKED", "SOURCE_CAPTURE_INCOMPLETE"),
        ("decision", "BLOCKED", "SOURCE_CAPTURE_INCOMPLETE"),
        ("mode", "saved-replay", "SOURCE_CAPTURE_INCOMPLETE"),
        ("formal_evidence_eligible", False, "SOURCE_CAPTURE_INCOMPLETE"),
        ("evidence_origin", "saved_replay", "SOURCE_CAPTURE_INCOMPLETE"),
        ("planned_inference_requests", 99, "SOURCE_CAPTURE_INCOMPLETE"),
        ("hard_stop_conditions", ["REPEATED_PROVIDER_FAILURE"], "SOURCE_CAPTURE_INCOMPLETE"),
    ],
)
def test_usage_ledger_rejects_identity_drift_and_blocked_sources(
    tmp_path, field, value, stop
):
    paths = [
        _usage_manifest(tmp_path / f"{name}.json", dimension=name)
        for name in ("initial_question", "followup", "report_scoring")
    ]
    payload = json.loads(paths[1].read_text(encoding="utf-8"))
    payload[field] = value
    paths[1].write_text(json.dumps(payload), encoding="utf-8")

    ledger = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        expected_source_manifest_sha256s=_source_bindings(paths),
        execution_manifest_sha256="e" * 64,
    )

    assert ledger.quality_status == "BLOCKED_USAGE_INCOMPLETE"
    assert stop in ledger.hard_stop_conditions
    followup = next(item for item in ledger.runs if item.dimension == "followup")
    assert followup.status == "BLOCKED"


def test_usage_ledger_rejects_attempted_metered_mismatch_and_retry_amplification(
    tmp_path,
):
    paths = [
        _usage_manifest(tmp_path / "initial.json", dimension="initial_question"),
        _usage_manifest(tmp_path / "followup.json", dimension="followup"),
        _usage_manifest(
            tmp_path / "report.json",
            dimension="report_scoring",
            attempted=4,
            metered=3,
            retries=1,
        ),
    ]

    ledger = build_t65_usage_cost_ledger(
        manifest_paths=paths,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        expected_source_manifest_sha256s=_source_bindings(paths),
        execution_manifest_sha256="e" * 64,
    )

    assert ledger.quality_status == "BLOCKED_USAGE_INCOMPLETE"
    assert "USAGE_METERING_UNAVAILABLE" in ledger.hard_stop_conditions
    assert "RETRY_AMPLIFICATION_EXCEEDED" in ledger.hard_stop_conditions


def _complete_usage_ledger(tmp_path: Path) -> Path:
    manifests = [
        _usage_manifest(tmp_path / f"{name}.json", dimension=name)
        for name in ("initial_question", "followup", "report_scoring")
    ]
    ledger = build_t65_usage_cost_ledger(
        manifest_paths=manifests,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
        authorization_sha256="f" * 64,
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        expected_source_manifest_sha256s=_source_bindings(manifests),
        execution_manifest_sha256="e" * 64,
    )
    return _write_json(
        tmp_path / "usage-ledger.json", ledger.model_dump(mode="json")
    )


def test_observability_missing_signal_is_null_and_blocked_not_zero(tmp_path):
    usage = _complete_usage_ledger(tmp_path)
    source = _write_json(
        tmp_path / "performance-source.json",
        {
            "provider": "DeepSeek",
            "model": "deepseek-v4-pro",
            "candidate_revision": CANDIDATE_REVISION,
            "candidate_tree": CANDIDATE_TREE,
            "signals": [
                {
                    "name": "generation_complete",
                    "status": "observed",
                    "seconds": 1.2,
                    "sample_count": 30,
                }
            ],
        },
    )

    evidence = build_performance_observability(
        source_paths=[source],
        usage_ledger_path=usage,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
    )

    missing = next(
        item for item in evidence.signals if item.name == "followup_first_visible"
    )
    assert missing.status == "not_observable"
    assert missing.seconds is None
    assert evidence.quality_status == "BLOCKED_PERFORMANCE_SIGNAL_NOT_OBSERVABLE"
    assert "PERFORMANCE_SIGNAL_NOT_OBSERVABLE" in evidence.hard_stop_conditions


def test_observability_never_converts_completion_latency_into_ttft(tmp_path):
    usage = _complete_usage_ledger(tmp_path)
    source = _write_json(
        tmp_path / "completion-only.json",
        {
            "provider": "DeepSeek",
            "model": "deepseek-v4-pro",
            "signals": [
                {
                    "name": "generation_complete",
                    "status": "observed",
                    "seconds": 2.5,
                    "sample_count": 30,
                }
            ],
        },
    )

    evidence = build_performance_observability(
        source_paths=[source],
        usage_ledger_path=usage,
        expected_revision=CANDIDATE_REVISION,
        expected_tree=CANDIDATE_TREE,
    )

    first_item = next(
        item for item in evidence.signals if item.name == "provider_first_item"
    )
    assert first_item.seconds is None
    assert first_item.status == "not_observable"


def test_unobserved_performance_signal_must_keep_value_null():
    with pytest.raises(ValidationError, match="must remain null"):
        PerformanceSignal(
            name="provider_first_item",
            status="not_observable",
            seconds=0,
            sample_count=0,
            reason="not captured",
        )
