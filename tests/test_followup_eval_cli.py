import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.decision_store import DecisionContract
from app.services.followup_eval import build_synthetic_fixture_replay
from app.services.followup_eval import SavedFollowupProviderArtifact
from app.services.followup_provider_preflight import (
    DeepSeekDiscoverySnapshot,
    ProviderPrice,
)
from app.services.interview_quality_dataset import load_interview_quality_dataset
from app.services.interview_quality_provider_authorization import (
    load_provider_authorization,
)
from scripts import evaluate_followup_quality as cli
from app.services.t65_provider_evidence import build_t65_usage_cost_ledger


DATASET_PATH = Path(
    "tests/golden/interview_quality_v1/followup-decision-quality-v2.json"
)


def valid_discovery(*, models=("deepseek-v4-pro",), priced=True):
    return DeepSeekDiscoverySnapshot(
        observed_at="2026-08-05T00:00:00Z",
        models_endpoint_ok=True,
        model_ids=list(models),
        pricing_page_ok=True,
        prices=(
            {
                "deepseek-v4-pro": ProviderPrice(
                    cache_hit_input_per_million=0.1,
                    cache_miss_input_per_million=0.2,
                    output_per_million=0.3,
                )
            }
            if priced
            else {}
        ),
    )


def test_fixture_cli_writes_complete_offline_evidence_without_claiming_calls(tmp_path):
    exit_code = cli.main(
        [
            "--mode",
            "fixture-replay",
            "--out",
            str(tmp_path),
            "--run-id",
            "fixture-full",
        ]
    )
    run_dir = tmp_path / "fixture-full"
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))

    assert exit_code == 2
    assert manifest["provider_called"] is False
    assert manifest["provider_invocations_this_run"] == 0
    assert manifest["inference_attempted"] == 0
    assert manifest["inference_metered"] == 0
    assert manifest["planned_inference_requests"] is None
    assert manifest["formal_evidence_eligible"] is False
    assert manifest["evidence_origin"] == "synthetic_fixture"
    assert manifest["recorded_or_simulated_provider_invocations"] > 0
    assert metrics["automated_status"] == "PASS"
    assert metrics["quality_status"] == "BLOCKED_PENDING_INDEPENDENT_REVIEW"
    assert metrics["sequence_replay"]["sequence_count"] == 20
    assert (run_dir / "synthetic-fixture-replay.json").exists()
    assert "api_key" not in (run_dir / "manifest.json").read_text(
        encoding="utf-8"
    ).casefold()


def test_saved_replay_cli_consumes_frozen_artifact_without_network(tmp_path):
    dataset = load_interview_quality_dataset(DATASET_PATH)
    digest = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    artifact = build_synthetic_fixture_replay(dataset, dataset_sha256=digest)
    source = tmp_path / "saved.json"
    source.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")

    exit_code = cli.main(
        [
            "--mode",
            "saved-replay",
            "--responses",
            str(source),
            "--out",
            str(tmp_path),
            "--run-id",
            "saved-full",
        ]
    )

    assert exit_code == 2
    assert (tmp_path / "saved-full" / "normalized-saved-replay.json").exists()
    manifest = json.loads(
        (tmp_path / "saved-full" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["provider_invocations_this_run"] == 0
    assert manifest["formal_evidence_eligible"] is False
    assert manifest["evidence_origin"] == "saved_replay"


def test_provider_cli_stops_on_current_model_drift_before_building_model(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-not-written")
    monkeypatch.setattr(
        cli,
        "discover_deepseek_provider",
        lambda **kwargs: valid_discovery(
            models=("deepseek-chat", "deepseek-v4-flash"),
            priced=False,
        ),
    )
    monkeypatch.setattr(
        cli,
        "_record_live_provider_responses",
        lambda *args, **kwargs: pytest.fail("live Provider must not be constructed"),
    )

    exit_code = cli.main(
        [
            "--mode",
            "provider",
            "--scope",
            "smoke",
            "--out",
            str(tmp_path),
            "--run-id",
            "provider-drift",
        ]
    )
    manifest_text = (tmp_path / "provider-drift" / "manifest.json").read_text(
        encoding="utf-8"
    )
    manifest = json.loads(manifest_text)

    assert exit_code == 2
    assert manifest["provider_called"] is False
    assert manifest["decision"] == "BLOCKED_MODEL_VERSION_DRIFT"
    assert manifest["hard_stop_conditions"] == ["MODEL_VERSION_DRIFT"]
    assert "secret-not-written" not in manifest_text


def test_live_path_uses_authorized_model_not_environment_model(monkeypatch, tmp_path):
    case_id = "followup-strong-redis-cache-consistency"
    captured = {}

    class ChatModel:
        def __init__(self):
            self.invoke_calls = 0

        def bind(self, **kwargs):
            captured["bind"] = kwargs
            return self

        def invoke(self, prompt):
            self.invoke_calls += 1
            decision = DecisionContract(
                action="next_question",
                answer_state="complete",
                gap_type="none",
                gap_summary="",
                reason_code="answer_complete",
                decision_confidence="high",
                closed_gap_ids=[],
                policy_version="adaptive_v1",
            )
            return SimpleNamespace(
                content=decision.model_dump_json(),
                usage_metadata={
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "input_token_details": {"cache_read": 0},
                },
                response_metadata={"model_name": "deepseek-v4-pro"},
                id="decision-response",
            )

        def with_structured_output(self, *args, **kwargs):
            raise AssertionError("authorized deepseek-v4-pro must use raw_only")

    def build_model(config):
        captured["config"] = config
        return ChatModel()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-not-written")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(
        cli,
        "discover_deepseek_provider",
        lambda **kwargs: valid_discovery(),
    )
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(build_model),
    )

    exit_code = cli.main(
        [
            "--mode",
            "provider",
            "--case-id",
            case_id,
            "--out",
            str(tmp_path),
            "--run-id",
            "provider-one",
        ]
    )
    manifest = json.loads(
        (tmp_path / "provider-one" / "manifest.json").read_text(encoding="utf-8")
    )

    assert exit_code == 2  # One case is below formal Gate sample sizes.
    assert captured["config"].model == "deepseek-v4-pro"
    assert captured["config"].base_url == "https://api.deepseek.com"
    assert captured["config"].max_retries == 0
    assert captured["bind"] == {"max_tokens": 300}
    assert manifest["decision_output_mode"] == "raw_only"
    assert manifest["provider_preflight"]["environment_model_ignored"] is True
    assert manifest["provider_called"] is True
    assert manifest["provider_invocations_this_run"] == 1
    assert manifest["planned_inference_requests"] == 1
    assert manifest["inference_attempted"] == 1
    assert manifest["inference_metered"] == 1
    assert manifest["cached_input_tokens"] == 0
    assert manifest["formal_evidence_eligible"] is False
    assert "USAGE_METERING_UNAVAILABLE" not in manifest["hard_stop_conditions"]
    saved = json.loads(
        (tmp_path / "provider-one" / "saved-provider-replay.json").read_text(
            encoding="utf-8"
        )
    )
    recorded = saved["cases"][0]["decision_attempts"][0]
    assert recorded["latency_seconds"] > 0
    assert recorded["input_tokens"] == 20
    assert recorded["output_tokens"] == 5
    assert recorded["provider_model"] == "deepseek-v4-pro"


def test_development_mode_cannot_consume_blind_partition():
    with pytest.raises(SystemExit, match="cannot consume blind-test"):
        cli.main(
            [
                "--mode",
                "fixture-replay",
                "--purpose",
                "development",
                "--partition",
                "blind-test",
            ]
        )


def test_cli_refuses_to_mix_evidence_into_an_existing_run(tmp_path):
    run_dir = tmp_path / "existing"
    run_dir.mkdir()
    (run_dir / "stale.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="run directory already exists"):
        cli.main(
            [
                "--mode",
                "fixture-replay",
                "--out",
                str(tmp_path),
                "--run-id",
                "existing",
            ]
        )


def test_live_timeout_stops_before_an_unmetered_retry(monkeypatch):
    dataset = load_interview_quality_dataset(DATASET_PATH)
    case = next(
        case
        for case in dataset.cases
        if case.case_id == "followup-gap-redis-cache-consistency"
    )

    class TimeoutModel:
        def __init__(self):
            self.invoke_calls = 0

        def bind(self, **kwargs):
            return self

        def invoke(self, prompt):
            self.invoke_calls += 1
            raise TimeoutError("no metered response")

        def with_structured_output(self, *args, **kwargs):
            raise AssertionError("authorized deepseek-v4-pro must use raw_only")

    model = TimeoutModel()
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(lambda config: model),
    )

    artifact, stops = cli._record_live_provider_responses(
        dataset.model_copy(update={"cases": [case]}),
        dataset_sha256=hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
        authorization=load_provider_authorization(
            Path("config/interview_quality_v1_provider_authorization.json")
        ),
        api_key="not-serialized",
        timeout_seconds=1,
    )

    assert model.invoke_calls == 1
    assert stops == ["USAGE_METERING_UNAVAILABLE"]
    assert len(artifact.cases[0].decision_attempts) == 1
    assert artifact.cases[0].decision_attempts[0].kind == "timeout"


def test_live_missing_cached_usage_hard_stops_after_one_decision(monkeypatch):
    dataset = load_interview_quality_dataset(DATASET_PATH)
    case = next(
        case
        for case in dataset.cases
        if case.case_id == "followup-strong-redis-cache-consistency"
    )

    class MissingCachedUsageModel:
        def __init__(self):
            self.invoke_calls = 0

        def bind(self, **kwargs):
            return self

        def invoke(self, prompt):
            self.invoke_calls += 1
            decision = DecisionContract(
                action="next_question",
                answer_state="complete",
                gap_type="none",
                gap_summary="",
                reason_code="answer_complete",
                decision_confidence="high",
                closed_gap_ids=[],
                policy_version="adaptive_v1",
            )
            return SimpleNamespace(
                content=decision.model_dump_json(),
                usage_metadata={"input_tokens": 20, "output_tokens": 5},
                response_metadata={"model_name": "deepseek-v4-pro"},
                id="decision-response-missing-cache",
            )

        def with_structured_output(self, *args, **kwargs):
            raise AssertionError("authorized deepseek-v4-pro must use raw_only")

    model = MissingCachedUsageModel()
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(lambda config: model),
    )

    artifact, stops = cli._record_live_provider_responses(
        dataset.model_copy(update={"cases": [case]}),
        dataset_sha256=hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
        authorization=load_provider_authorization(
            Path("config/interview_quality_v1_provider_authorization.json")
        ),
        api_key="not-serialized",
        timeout_seconds=1,
    )

    assert model.invoke_calls == 1
    assert stops == ["USAGE_METERING_UNAVAILABLE"]
    assert artifact.capture_status == "hard_stopped"
    assert len(artifact.cases) == 1
    attempt = artifact.cases[0].decision_attempts[0]
    assert attempt.input_tokens is None
    assert attempt.output_tokens is None
    assert attempt.cached_input_tokens is None


def test_live_model_mismatch_hard_stops_after_one_metered_decision(monkeypatch):
    dataset = load_interview_quality_dataset(DATASET_PATH)
    case = next(
        case
        for case in dataset.cases
        if case.case_id == "followup-strong-redis-cache-consistency"
    )

    class WrongModel:
        def __init__(self):
            self.invoke_calls = 0

        def bind(self, **kwargs):
            return self

        def invoke(self, prompt):
            self.invoke_calls += 1
            decision = DecisionContract(
                action="next_question",
                answer_state="complete",
                gap_type="none",
                gap_summary="",
                reason_code="answer_complete",
                decision_confidence="high",
                closed_gap_ids=[],
                policy_version="adaptive_v1",
            )
            return SimpleNamespace(
                content=decision.model_dump_json(),
                usage_metadata={
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "input_token_details": {"cache_read": 0},
                },
                response_metadata={"model_name": "deepseek-v4-flash"},
                id="decision-response-wrong-model",
            )

    model = WrongModel()
    monkeypatch.setattr(
        cli.OpenAIInterviewLLM,
        "_build_chat_model",
        staticmethod(lambda config: model),
    )

    artifact, stops = cli._record_live_provider_responses(
        dataset.model_copy(update={"cases": [case]}),
        dataset_sha256=hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
        authorization=load_provider_authorization(
            Path("config/interview_quality_v1_provider_authorization.json")
        ),
        api_key="not-serialized",
        timeout_seconds=1,
    )

    assert model.invoke_calls == 1
    assert stops == ["PROVIDER_OR_MODEL_MISMATCH"]
    assert artifact.capture_status == "hard_stopped"
    attempt = artifact.cases[0].decision_attempts[0]
    assert attempt.input_tokens == 20
    assert attempt.output_tokens == 5
    assert attempt.cached_input_tokens == 0
    assert attempt.provider_model == "deepseek-v4-flash"


def test_provider_full_manifest_is_native_t65_usage_source(monkeypatch, tmp_path):
    revision = "1" * 40
    tree = "2" * 40
    dataset = load_interview_quality_dataset(DATASET_PATH)
    dataset_sha256 = hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest()
    fixture = build_synthetic_fixture_replay(dataset, dataset_sha256=dataset_sha256)
    cases = []
    for saved in fixture.cases:
        decision = saved.decision_attempts[0].model_copy(
            update={
                "input_tokens": 10,
                "output_tokens": 2,
                "cached_input_tokens": 0,
                "provider_model": "deepseek-v4-pro",
                "latency_seconds": 0.01,
            }
        )
        generation_attempts = []
        if decision.kind == "success" and decision.payload["action"] == "follow_up":
            generation_attempts = [
                saved.generation_attempts[-1].model_copy(
                    update={
                        "input_tokens": 8,
                        "output_tokens": 3,
                        "cached_input_tokens": 1,
                        "provider_model": "deepseek-v4-pro",
                        "latency_seconds": 0.01,
                    }
                )
            ]
        cases.append(
            saved.model_copy(
                update={
                    "decision_attempts": [decision],
                    "generation_attempts": generation_attempts,
                }
            )
        )
    capture = SavedFollowupProviderArtifact(
        source="local_redacted_provider_output",
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset_sha256,
        provider_name="DeepSeek",
        model_id="deepseek-v4-pro",
        cases=cases,
    )
    real_calculate = cli.calculate_followup_metrics

    def passing_metrics(*args, **kwargs):
        metrics = real_calculate(*args, **kwargs)
        metrics["automated_status"] = "PASS"
        metrics["independent_review_status"] = "PASS"
        metrics["quality_status"] = "PASS"
        return metrics

    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-serialized")
    monkeypatch.setattr(cli, "_candidate_identity", lambda: (revision, tree, True))
    monkeypatch.setattr(cli, "discover_deepseek_provider", lambda **kwargs: valid_discovery())
    monkeypatch.setattr(
        cli,
        "_record_live_provider_responses",
        lambda *args, **kwargs: (capture, []),
    )
    monkeypatch.setattr(cli, "calculate_followup_metrics", passing_metrics)

    exit_code = cli.main(
        [
            "--mode",
            "provider",
            "--scope",
            "full",
            "--out",
            str(tmp_path),
            "--run-id",
            "provider-full-contract",
        ]
    )
    manifest_path = tmp_path / "provider-full-contract" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert manifest["candidate_revision"] == revision
    assert manifest["candidate_tree"] == tree
    assert manifest["worktree_clean"] is True
    assert manifest["evidence_origin"] == "live_provider"
    assert manifest["formal_evidence_eligible"] is False
    assert manifest["engineering_evidence_complete"] is True
    assert manifest["decision"] == manifest["quality_status"] == "BLOCKED_NON_FORMAL_EVIDENCE"
    assert manifest["inference_attempted"] == manifest["inference_metered"]
    assert manifest["planned_inference_requests"] + manifest["retries"] == manifest["inference_attempted"]
    assert manifest["input_tokens"] > 0
    assert manifest["output_tokens"] > 0
    assert manifest["cached_input_tokens"] >= 0
    assert manifest["estimated_cost"] is not None

    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    authorization_sha = hashlib.sha256(
        Path("config/interview_quality_v1_provider_authorization.json").read_bytes()
    ).hexdigest()
    ledger = build_t65_usage_cost_ledger(
        manifest_paths=[manifest_path],
        expected_revision=revision,
        expected_tree=tree,
        authorization_sha256=authorization_sha,
        expected_provider="DeepSeek",
        expected_model="deepseek-v4-pro",
        expected_source_manifest_sha256s={"followup": manifest_sha},
        execution_manifest_sha256="3" * 64,
    )
    followup = next(item for item in ledger.runs if item.dimension == "followup")
    assert followup.status == "BLOCKED"
    assert followup.missing_fields == ["provider_attempt_receipt_sha256"]
    assert "EVIDENCE_PERSISTENCE_UNAVAILABLE" in ledger.hard_stop_conditions
    assert "EXTERNAL_GATE_AUTHORITY_NOT_TRUSTED" in ledger.hard_stop_conditions
    assert "PROVIDER_CANDIDATE_MISMATCH" in ledger.hard_stop_conditions
