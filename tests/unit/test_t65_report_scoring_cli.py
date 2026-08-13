from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
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
from app.services.independent_review_handoff import (
    DetachedSignatureEvidence,
    canonical_sha256,
)
from app.services.report_eval_artifacts import EvaluationArtifactStore
from app.services.report_calibration_dataset import load_calibration_dataset
from scripts import build_t65_provider_evidence
from scripts import evaluate_t65_report_scoring


ROOT = Path(__file__).resolve().parents[2]
DATASET = (
    ROOT / "tests" / "golden" / "interview_quality_v1" / "report-score-calibration-v1.json"
)


def _blocked_cli_args(tmp_path: Path, run_id: str) -> list[str]:
    return [
        "--mode",
        "provider",
        "--scope",
        "smoke",
        "--partition",
        "dev",
        "--context-window-tokens",
        "128000",
        "--out",
        str(tmp_path),
        "--run-id",
        run_id,
    ]


def test_report_cli_argument_contract_uses_exit_two():
    with pytest.raises(SystemExit) as exc:
        evaluate_t65_report_scoring.main(
            ["--mode", "provider", "--scope", "smoke", "--smoke-case-count", "2"]
        )
    assert exc.value.code == 2


def test_report_cli_writes_initial_and_local_preflight_before_discovery_or_data(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden_discovery(**_kwargs):
        raise AssertionError("discovery must not run after local blockers")

    monkeypatch.setattr(
        evaluate_t65_report_scoring,
        "discover_deepseek_provider",
        forbidden_discovery,
    )
    exit_code = evaluate_t65_report_scoring.main(
        _blocked_cli_args(tmp_path, "local-block")
    )
    assert exit_code == 2
    run_dir = tmp_path / "local-block"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["decision"] == "BLOCKED"
    assert manifest["provider_called"] is False
    assert manifest["first_data_request_sent"] is False
    assert manifest["discovery_requests"] == 0
    assert manifest["local_preflight"]["discovery"] is None
    assert manifest["hard_stop_conditions"]
    assert manifest["formal_evidence_eligible"] is False
    assert manifest["engineering_evidence_complete"] is False
    assert not (run_dir / "attempt-start-ledger.jsonl").exists()
    assert not (run_dir / "local-redacted-provider-capture.json").exists()


def test_report_cli_environment_model_and_base_url_cannot_override_authorization(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_MODEL", "unauthorized-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.invalid")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        evaluate_t65_report_scoring,
        "discover_deepseek_provider",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("discovery must not run")
        ),
    )
    assert (
        evaluate_t65_report_scoring.main(
            _blocked_cli_args(tmp_path, "environment-ignored")
        )
        == 2
    )
    manifest = json.loads(
        (tmp_path / "environment-ignored" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["model"] == "deepseek-v4-pro"
    assert manifest["provider"] == "DeepSeek"
    assert manifest["environment_model_ignored"] is True
    serialized = json.dumps(manifest)
    assert "unauthorized-model" not in serialized
    assert "untrusted.invalid" not in serialized


def test_report_cli_refuses_existing_run_dir(tmp_path):
    run_dir = tmp_path / "existing"
    run_dir.mkdir()
    marker = run_dir / "user-owned.txt"
    marker.write_text("preserve", encoding="utf-8")
    assert evaluate_t65_report_scoring.main(_blocked_cli_args(tmp_path, "existing")) == 2
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert not (run_dir / "manifest.json").exists()


def test_forged_execution_manifest_cannot_release_blind_before_discovery(
    tmp_path, monkeypatch
):
    forged = json.loads(
        evaluate_t65_report_scoring.DEFAULT_EXECUTION_MANIFEST.read_text(
            encoding="utf-8"
        )
    )
    forged.setdefault("task_status", {})["T26"] = "PASS"
    forged.setdefault("t26", {})["review_status"] = "PASS"
    execution_path = tmp_path / "forged-execution.json"
    execution_path.write_text(json.dumps(forged), encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        evaluate_t65_report_scoring,
        "discover_deepseek_provider",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("untrusted blind release must stop before discovery")
        ),
    )
    argv = _blocked_cli_args(tmp_path, "forged-blind-release")
    argv[argv.index("dev")] = "blind-test"
    argv.extend(["--execution-manifest", str(execution_path)])

    assert evaluate_t65_report_scoring.main(argv) == 2
    manifest = json.loads(
        (tmp_path / "forged-blind-release" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["blind_release_authority_verified"] is False
    assert "BLIND_PARTITION_NOT_RELEASED" in manifest["hard_stop_conditions"]
    assert manifest["provider_called"] is False
    assert manifest["discovery_requests"] == 0
    assert manifest["first_data_request_sent"] is False


def test_blind_release_accepts_only_signature_from_frozen_trust_anchor(
    tmp_path, monkeypatch
):
    execution_path = tmp_path / "execution.json"
    execution_payload = {
        "task_status": {"T26": "PASS"},
        "t26": {"review_status": "PASS"},
    }
    execution_path.write_text(json.dumps(execution_payload), encoding="utf-8")
    execution_sha = hashlib.sha256(execution_path.read_bytes()).hexdigest()
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
        "signature_id": "t27-blind-release-test",
        "signer_authority_id": "external-t27-authority-test",
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
    signature_path = tmp_path / "execution.signature.json"
    signature_path.write_text(signature.model_dump_json(), encoding="utf-8")
    public_key_path = tmp_path / "authority.pem"
    public_key_path.write_bytes(public_pem)

    assert (
        evaluate_t65_report_scoring._trusted_blind_partition_release(
            execution_path=execution_path,
            execution_manifest=execution_payload,
            signature_path=signature_path,
            public_key_path=public_key_path,
            authority_id="external-t27-authority-test",
        )
        is False
    )
    monkeypatch.setattr(
        independent_review_handoff,
        "TRUSTED_GATE_AUTHORITY_PUBLIC_KEY_SHA256",
        frozenset({public_sha}),
    )
    assert (
        evaluate_t65_report_scoring._trusted_blind_partition_release(
            execution_path=execution_path,
            execution_manifest=execution_payload,
            signature_path=signature_path,
            public_key_path=public_key_path,
            authority_id="external-t27-authority-test",
        )
        is True
    )


def test_usage_builder_keeps_missing_cost_null_and_blocks(tmp_path, monkeypatch):
    revision = "1" * 40
    tree = "2" * 40
    paths = []
    for dimension in ("initial_question", "followup", "report_scoring"):
        payload = _usage_manifest(dimension, revision=revision, tree=tree)
        if dimension == "followup":
            payload["estimated_cost"] = None
        path = tmp_path / f"{dimension}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)
    out = tmp_path / "ledger.json"
    execution_manifest = _usage_execution_manifest(
        tmp_path, paths, revision=revision, tree=tree
    )
    monkeypatch.setenv(
        "T65_FROZEN_EXECUTION_MANIFEST_SHA256",
        hashlib.sha256(execution_manifest.read_bytes()).hexdigest(),
    )
    argv = ["usage-ledger"]
    for path in paths:
        argv.extend(("--manifest", str(path)))
    argv.extend(
        (
            "--candidate-revision",
            revision,
            "--candidate-tree",
            tree,
            "--authorization-sha256",
            "3" * 64,
            "--authorization-id",
            "authorization-test",
            "--provider",
            "DeepSeek",
            "--model",
            "deepseek-v4-pro",
            "--execution-manifest",
            str(execution_manifest),
            "--out",
            str(out),
        )
    )
    assert build_t65_provider_evidence.main(argv) == 2
    ledger = json.loads(out.read_text(encoding="utf-8"))
    assert ledger["quality_status"] == "BLOCKED_USAGE_INCOMPLETE"
    assert "SOURCE_CAPTURE_INCOMPLETE" in ledger["hard_stop_conditions"]
    assert ledger["totals"]["estimated_cost"] is None
    followup = next(item for item in ledger["runs"] if item["dimension"] == "followup")
    assert followup["estimated_cost"] is None
    assert "estimated_cost" in followup["missing_fields"]


def test_usage_builder_self_made_control_manifest_cannot_pass(tmp_path, monkeypatch):
    revision = "1" * 40
    tree = "2" * 40
    paths = []
    for dimension in ("initial_question", "followup", "report_scoring"):
        path = tmp_path / f"{dimension}.json"
        path.write_text(
            json.dumps(_usage_manifest(dimension, revision=revision, tree=tree)),
            encoding="utf-8",
        )
        paths.append(path)
    out = tmp_path / "complete-ledger.json"
    execution_manifest = _usage_execution_manifest(
        tmp_path, paths, revision=revision, tree=tree
    )
    monkeypatch.setenv(
        "T65_FROZEN_EXECUTION_MANIFEST_SHA256",
        hashlib.sha256(execution_manifest.read_bytes()).hexdigest(),
    )
    argv = ["usage-ledger"]
    for path in paths:
        argv.extend(("--manifest", str(path)))
    argv.extend(
        (
            "--candidate-revision",
            revision,
            "--candidate-tree",
            tree,
            "--authorization-sha256",
            "3" * 64,
            "--authorization-id",
            "authorization-test",
            "--provider",
            "DeepSeek",
            "--model",
            "deepseek-v4-pro",
            "--execution-manifest",
            str(execution_manifest),
            "--out",
            str(out),
        )
    )
    assert build_t65_provider_evidence.main(argv) == 2
    ledger = json.loads(out.read_text(encoding="utf-8"))
    assert ledger["quality_status"] == "BLOCKED_USAGE_INCOMPLETE"
    assert "SOURCE_CAPTURE_INCOMPLETE" in ledger["hard_stop_conditions"]
    assert "EXTERNAL_GATE_AUTHORITY_NOT_TRUSTED" in ledger["hard_stop_conditions"]
    assert "PROVIDER_CANDIDATE_MISMATCH" in ledger["hard_stop_conditions"]
    assert ledger["execution_signature_verified"] is False
    assert ledger["candidate_repository_verified"] is False
    assert ledger["totals"]["inference_attempted"] == 3
    assert ledger["totals"]["estimated_cost"] == pytest.approx(0.03)


def test_usage_builder_receipt_mapping_parser_rejects_ambiguity(tmp_path):
    receipt = tmp_path / "receipt.json"
    assert build_t65_provider_evidence._parse_dimension_paths(
        [f"followup={receipt}"], label="receipt"
    ) == {"followup": receipt}
    with pytest.raises(ValueError, match="one unique"):
        build_t65_provider_evidence._parse_dimension_paths(
            [f"followup={receipt}", f"followup={receipt}"], label="receipt"
        )
    with pytest.raises(ValueError, match="one unique"):
        build_t65_provider_evidence._parse_dimension_paths(
            [f"unknown={receipt}"], label="receipt"
        )


def test_usage_builder_passes_all_explicit_receipt_and_ledger_mappings(
    tmp_path, monkeypatch
):
    dimensions = ("initial_question", "followup", "report_scoring")
    receipts = {name: tmp_path / f"{name}-receipt.json" for name in dimensions}
    ledgers = {name: tmp_path / f"{name}-attempts.jsonl" for name in dimensions}
    captured = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        payload = {
            "schema_version": "t65-usage-cost-ledger-v1",
            "quality_status": "BLOCKED_USAGE_INCOMPLETE",
        }
        return SimpleNamespace(
            quality_status="BLOCKED_USAGE_INCOMPLETE",
            model_dump=lambda mode=None: payload,
            model_dump_json=lambda: json.dumps(payload),
        )

    monkeypatch.setattr(
        build_t65_provider_evidence, "build_t65_usage_cost_ledger", fake_builder
    )
    argv = [
        "usage-ledger",
        "--manifest",
        str(tmp_path / "source.json"),
        "--candidate-revision",
        "1" * 40,
        "--candidate-tree",
        "2" * 40,
        "--authorization-sha256",
        "3" * 64,
        "--authorization-id",
        "authorization-test",
        "--provider",
        "DeepSeek",
        "--model",
        "deepseek-v4-pro",
        "--execution-manifest",
        str(tmp_path / "external-control-manifest.json"),
        "--out",
        str(tmp_path / "out.json"),
    ]
    for dimension in dimensions:
        argv.extend(("--receipt", f"{dimension}={receipts[dimension]}"))
        argv.extend(("--attempt-ledger", f"{dimension}={ledgers[dimension]}"))

    assert build_t65_provider_evidence.main(argv) == 2
    assert captured["receipt_paths_by_dimension"] == receipts
    assert captured["ledger_paths_by_dimension"] == ledgers
    assert captured["execution_manifest_path"] == (
        tmp_path / "external-control-manifest.json"
    )
    assert "expected_source_manifest_sha256s" not in captured
    assert "expected_executor_sha256" not in captured
    assert "execution_manifest_sha256" not in captured


def test_safe_scoring_result_excludes_answer_and_generated_output():
    dataset = load_calibration_dataset(DATASET)
    case = dataset.cases[0]
    result = evaluate_t65_report_scoring.AttemptResult(
        case_id=case.case_id,
        group_id=case.group_id,
        quality_level=case.quality_label,
        run_number=1,
        score=90,
        expected_score_range=case.expected_score_range,
        language=case.language,
        question_type=case.question_type,
        answer=case.answer,
        observed=[case.required_evidence[0]],
        required_observations=list(case.required_evidence),
        forbidden_claims=list(case.forbidden_claims),
        applicable_dimensions=["depth"],
        expected_applicable_dimensions=["depth"],
        output_text="generated private coaching text",
    )
    safe = evaluate_t65_report_scoring._safe_scoring_result(result)
    serialized = json.dumps(safe, ensure_ascii=False)
    assert "answer" not in safe
    assert "observed" not in safe
    assert case.answer not in serialized
    assert result.observed[0] not in serialized
    assert result.output_text not in serialized
    assert len(safe["output_sha256"]) == 64


def test_saved_replay_is_always_diagnostic_and_never_formal_pass():
    manifest = {
        "decision": "PASS",
        "quality_status": "PASS",
        "formal_evidence_eligible": True,
        "hard_stop_conditions": [],
    }

    evaluate_t65_report_scoring._mark_replay_diagnostic(manifest)

    assert manifest == {
        "decision": "BLOCKED_DIAGNOSTIC_ONLY",
        "quality_status": "BLOCKED_DIAGNOSTIC_ONLY",
        "formal_evidence_eligible": False,
        "hard_stop_conditions": ["SOURCE_CAPTURE_INCOMPLETE"],
    }


def test_saved_replay_metrics_cannot_expose_a_root_formal_pass():
    payload = {"decision": "PASS", "passed": True}
    evaluate_t65_report_scoring._mark_replay_metrics_diagnostic(payload)

    assert payload["decision"] == "BLOCKED_DIAGNOSTIC_ONLY"
    assert payload["passed"] is False
    assert payload["formal_evidence_eligible"] is False


def test_report_usage_missing_cached_tokens_is_blocked_not_assumed_zero():
    usage = {
        "provider_attempt_count": 1,
        "provider_metered_attempt_count": 1,
        "provider_usage_available": True,
        "provider_model": "deepseek-v4-pro",
        "provider_input_tokens": 100,
        "provider_output_tokens": 20,
    }
    assert (
        evaluate_t65_report_scoring._usage_stop(usage, "deepseek-v4-pro")
        == "USAGE_METERING_UNAVAILABLE"
    )


def test_report_attempt_keeps_missing_request_usage_null_when_blocked():
    dataset = load_calibration_dataset(DATASET)
    case = dataset.cases[0]
    attempt, stop = evaluate_t65_report_scoring._complete_attempt(
        case,
        run_number=1,
        model="deepseek-v4-pro",
        elapsed=0.25,
        usage={"provider_model": "deepseek-v4-pro"},
        safe_payload={"scoring_result": {"score": 80}},
    )

    assert stop == "USAGE_METERING_UNAVAILABLE"
    assert attempt.provider_attempts is None
    assert attempt.provider_metered_attempts is None
    assert attempt.retry_count is None


def test_report_provider_hard_stop_prevents_the_next_case(tmp_path, monkeypatch):
    dataset = load_calibration_dataset(DATASET)
    authorization = load_provider_authorization()
    discovery = _discovery()
    store = EvaluationArtifactStore.create(
        root=tmp_path,
        run_id="hard-stop",
        manifest={"run_id": "hard-stop", "first_data_request_sent": False},
    )
    manifest = store.read_manifest()

    class FailingLLM:
        def __init__(self, *_args, **_kwargs):
            self.provider_attempt_hook = _kwargs["provider_attempt_hook"]

        def generate_report(self, *_args, **_kwargs):
            self.provider_attempt_hook()
            raise RuntimeError("provider failure text must not persist")

    monkeypatch.setattr(evaluate_t65_report_scoring, "OpenAIInterviewLLM", FailingLLM)
    monkeypatch.setattr(
        evaluate_t65_report_scoring,
        "consume_provider_context_metadata",
        lambda: {
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 0,
            "provider_usage_available": False,
            "provider_model": "deepseek-v4-pro",
        },
    )
    capture, results = evaluate_t65_report_scoring._record_provider(
        dataset.cases[:2],
        runs_per_case=1,
        run_id="hard-stop",
        dataset=dataset,
        dataset_sha256=evaluate_t65_report_scoring._sha256(DATASET),
        authorization=authorization,
        authorization_sha256=evaluate_t65_report_scoring._sha256(
            evaluate_t65_report_scoring.DEFAULT_AUTHORIZATION
        ),
        api_key="not-persisted",
        context_window_tokens=128000,
        timeout_seconds=1,
        discovery=discovery,
        candidate_revision="1" * 40,
        candidate_tree="2" * 40,
        store=store,
        manifest=manifest,
        grounding_ngram_min_coverage=0.6,
    )
    assert results == []
    assert len(capture.attempts) == 1
    assert capture.capture_status == "hard_stopped"
    assert capture.hard_stop_conditions == [
        "USAGE_METERING_UNAVAILABLE",
        "EVIDENCE_PERSISTENCE_UNAVAILABLE",
    ]
    persisted = (store.run_dir / "local-redacted-provider-capture.json").read_text(
        encoding="utf-8"
    )
    assert "provider failure text" not in persisted
    assert "not-persisted" not in persisted
    ledger_lines = (store.run_dir / "attempt-start-ledger.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(ledger_lines) == 1
    persisted_manifest = store.read_manifest()
    assert persisted_manifest["attempt_start_persisted"] is True
    assert persisted_manifest["first_data_request_sent"] is True


def _usage_manifest(dimension: str, *, revision: str, tree: str) -> dict:
    return {
        "schema_version": {
            "initial_question": "initial-question-quality-run-v1",
            "followup": "followup-quality-run-v1",
            "report_scoring": "t65-report-scoring-run-v1",
        }[dimension],
        "dimension": dimension,
        "run_id": f"run-{dimension}",
        "authorization_sha256": "3" * 64,
        "provider": "DeepSeek",
        "model": "deepseek-v4-pro",
        "candidate_revision": revision,
        "candidate_tree": tree,
        "discovery_requests": 2,
        "inference_attempted": 1,
        "inference_metered": 1,
        "retries": 0,
        "planned_inference_requests": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_input_tokens": 10,
        "estimated_cost": 0.01,
        "quality_status": "PASS",
        "decision": "PASS",
        "hard_stop_conditions": [],
        "mode": "provider",
        "scope": "full",
        "evidence_origin": "live_provider",
        "formal_evidence_eligible": True,
    }


def _usage_execution_manifest(
    tmp_path: Path,
    paths: list[Path],
    *,
    revision: str,
    tree: str,
) -> Path:
    source_bindings = {
        json.loads(path.read_text(encoding="utf-8"))["dimension"]: hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }
    path = tmp_path / "usage-execution-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "interview-quality-v1-t65-control-manifest-v1",
                "t65_provider_evidence": {
                    "candidate_revision": revision,
                    "candidate_tree": tree,
                    "authorization_sha256": "3" * 64,
                    "authorization_id": "authorization-test",
                    "provider": "DeepSeek",
                    "model": "deepseek-v4-pro",
                    "executor_sha256": "8" * 64,
                    "source_manifest_sha256s": source_bindings,
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _discovery() -> DeepSeekDiscoverySnapshot:
    return DeepSeekDiscoverySnapshot(
        observed_at="2026-08-07T00:00:00Z",
        models_endpoint_ok=True,
        model_ids=["deepseek-v4-pro"],
        pricing_page_ok=True,
        prices={
            "deepseek-v4-pro": ProviderPrice(
                cache_hit_input_per_million=0.1,
                cache_miss_input_per_million=0.2,
                output_per_million=0.3,
            )
        },
    )
