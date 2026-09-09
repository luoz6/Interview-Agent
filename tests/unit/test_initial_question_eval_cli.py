from __future__ import annotations

import json
import hashlib
from types import SimpleNamespace

import pytest

import scripts.evaluate_initial_question_quality as cli
from app.services.followup_provider_preflight import (
    DeepSeekDiscoverySnapshot,
    ProviderPrice,
)
from app.services.evaluator_candidate_identity import (
    EVALUATOR_CANDIDATE_IDENTITY_VERSION,
)
from app.services.initial_question_eval import build_synthetic_initial_question_attempts
from app.services.llm import (
    PLAN_GENERATION_PROMPT_SHA256,
    PLAN_GENERATION_PROMPT_VERSION,
    PLAN_QUALITY_REPAIR_PROMPT_SHA256,
    PLAN_QUALITY_REPAIR_PROMPT_VERSION,
)
from app.services.interview_plan_revision import v2_plan_to_legacy
from app.services.t65_provider_evidence import build_t65_usage_cost_ledger


def no_repair_lifecycle_metadata() -> dict[str, object]:
    return {
        "initial_hard_finding_codes": [],
        "quality_repair_triggered": False,
        "quality_repair_succeeded": None,
        "post_repair_hard_finding_codes": None,
        "quality_repair_prompt_version": PLAN_QUALITY_REPAIR_PROMPT_VERSION,
        "quality_repair_prompt_sha256": PLAN_QUALITY_REPAIR_PROMPT_SHA256,
        "provider_response_id_sha256s": ["a" * 64],
    }


@pytest.mark.parametrize(
    "run_id",
    ["..", "../outside", r"C:\outside\run", r"\\server\share\run"],
)
def test_t57_cli_rejects_unsafe_run_id_before_discovery_provider_or_write(
    monkeypatch, tmp_path, run_id
):
    output_root = tmp_path / "safe"
    monkeypatch.setattr(
        cli,
        "discover_deepseek_provider",
        lambda **_kwargs: pytest.fail("unsafe run-id must stop before discovery"),
    )
    monkeypatch.setattr(
        cli,
        "OpenAIInterviewLLM",
        lambda *_args, **_kwargs: pytest.fail(
            "unsafe run-id must stop before Provider construction"
        ),
    )

    with pytest.raises(SystemExit, match="invalid --run-id"):
        cli.main(
            [
                "--mode",
                "provider",
                "--scope",
                "smoke",
                "--purpose",
                "evaluation",
                "--partition",
                "all",
                "--out",
                str(output_root),
                "--run-id",
                run_id,
            ]
        )

    assert not output_root.exists()


def test_plan_prompt_identity_fails_before_provider_discovery_or_write(
    monkeypatch, tmp_path
):
    output_root = tmp_path / "prompt-drift"
    monkeypatch.setattr(
        cli,
        "verify_plan_generation_prompt_identity",
        lambda: (_ for _ in ()).throw(RuntimeError("drift")),
    )
    monkeypatch.setattr(
        cli,
        "discover_deepseek_provider",
        lambda **_kwargs: pytest.fail("prompt drift must stop before discovery"),
    )

    with pytest.raises(SystemExit, match="prompt integrity check failed"):
        cli.main(
            [
                "--mode", "provider",
                "--scope", "smoke",
                "--purpose", "evaluation",
                "--partition", "all",
                "--out", str(output_root),
                "--run-id", "prompt-drift",
            ]
        )

    assert not output_root.exists()


def test_fixture_cli_writes_full_evidence_and_returns_blocked_not_pass(tmp_path):
    code = cli.main(
        [
            "--mode", "fixture-replay",
            "--scope", "full",
            "--purpose", "evaluation",
            "--partition", "all",
            "--out", str(tmp_path),
            "--run-id", "fixture-run",
        ]
    )
    manifest = json.loads(
        (tmp_path / "fixture-run" / "manifest.json").read_text(encoding="utf-8")
    )
    metrics = json.loads(
        (tmp_path / "fixture-run" / "metrics.json").read_text(encoding="utf-8")
    )

    assert code == 2
    assert manifest["provider_called"] is False
    assert manifest["provider_invocations_this_run"] == 0
    assert manifest["decision"] == "BLOCKED_SYNTHETIC_FIXTURE_ONLY"
    assert manifest["quality_status"] == "BLOCKED_SYNTHETIC_FIXTURE_ONLY"
    assert manifest["evidence_origin"] == "synthetic_fixture"
    assert manifest["formal_evidence_eligible"] is False
    assert manifest["engineering_evidence_complete"] is False
    assert manifest["planned_inference_requests"] == 0
    assert manifest["inference_attempted"] == 0
    assert manifest["inference_metered"] == 0
    assert manifest["retries"] == 0
    assert manifest["planned_inference_requests"] + manifest["retries"] == manifest["inference_attempted"]
    assert manifest["input_tokens"] == 0
    assert manifest["output_tokens"] == 0
    assert manifest["cached_input_tokens"] == 0
    assert manifest["estimated_cost"] == 0.0
    assert len(manifest["candidate_revision"]) == 40
    assert len(manifest["candidate_tree"]) == 40
    assert manifest["candidate_identity_version"] == (
        EVALUATOR_CANDIDATE_IDENTITY_VERSION
    )
    if manifest["worktree_clean"]:
        assert manifest["candidate_unstaged_tracked_diff_sha256"] is None
        assert manifest["candidate_staged_diff_sha256"] is None
        assert manifest["candidate_untracked_safe_manifest_sha256"] is None
    else:
        assert len(manifest["candidate_unstaged_tracked_diff_sha256"]) == 64
        assert len(manifest["candidate_staged_diff_sha256"]) == 64
        assert len(manifest["candidate_untracked_safe_manifest_sha256"]) == 64
    assert manifest["candidate_untracked_safe_file_count"] >= 0
    assert manifest["candidate_untracked_excluded_file_count"] >= 0
    assert manifest["authorization_sha256"]
    assert manifest["provider"]
    assert manifest["model"]
    assert manifest["plan_output_mode"] == "raw_only"
    assert manifest["plan_generation_prompt_version"] == PLAN_GENERATION_PROMPT_VERSION
    assert manifest["plan_generation_prompt_sha256"] == PLAN_GENERATION_PROMPT_SHA256
    assert manifest["quality_repair_prompt_version"] == (
        PLAN_QUALITY_REPAIR_PROMPT_VERSION
    )
    assert manifest["quality_repair_prompt_sha256"] == (
        PLAN_QUALITY_REPAIR_PROMPT_SHA256
    )
    assert manifest["initial_hard_finding_codes"] == []
    assert manifest["quality_repair_triggered"] is False
    assert manifest["quality_repair_succeeded"] is None
    assert manifest["post_repair_hard_finding_codes"] is None
    assert metrics["automated_status"] == "PASS"
    assert metrics["attempt_count"] == 24
    assert metrics["provider_usage"]["recorded_source_invocations"] == 0


def test_development_cli_cannot_consume_blind_test(tmp_path):
    with pytest.raises(SystemExit, match="cannot consume blind-test"):
        cli.main(
            [
                "--purpose", "development",
                "--partition", "all",
                "--out", str(tmp_path),
            ]
        )


def test_provider_cli_stops_before_data_request_on_model_drift(
    monkeypatch, tmp_path
):
    price = ProviderPrice(
        cache_hit_input_per_million=0.1,
        cache_miss_input_per_million=0.2,
        output_per_million=0.3,
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-not-written")
    monkeypatch.setattr(
        cli,
        "discover_deepseek_provider",
        lambda **_kwargs: DeepSeekDiscoverySnapshot(
            observed_at="2026-08-06T00:00:00Z",
            models_endpoint_ok=True,
            model_ids=["deepseek-chat", "deepseek-v4-flash"],
            pricing_page_ok=True,
            prices={"deepseek-chat": price, "deepseek-v4-flash": price},
        ),
    )

    code = cli.main(
        [
            "--mode", "provider",
            "--scope", "smoke",
            "--purpose", "evaluation",
            "--partition", "all",
            "--out", str(tmp_path),
            "--run-id", "provider-drift",
        ]
    )
    manifest_path = tmp_path / "provider-drift" / "manifest.json"
    text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(text)

    assert code == 2
    assert manifest["provider_called"] is False
    assert manifest["first_data_request_sent"] is False
    assert manifest["hard_stop_conditions"] == ["MODEL_VERSION_DRIFT"]
    assert "secret-not-written" not in text
    assert manifest["evidence_origin"] == "provider_smoke"
    assert manifest["formal_evidence_eligible"] is False
    assert manifest["engineering_evidence_complete"] is False
    assert manifest["planned_inference_requests"] + manifest["retries"] == manifest["inference_attempted"]


def test_real_t57_producer_manifest_satisfies_t65_shape_contract(tmp_path):
    code = cli.main(
        [
            "--mode", "fixture-replay",
            "--scope", "full",
            "--purpose", "evaluation",
            "--partition", "all",
            "--out", str(tmp_path),
            "--run-id", "t57-contract",
        ]
    )
    manifest_path = tmp_path / "t57-contract" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    ledger = build_t65_usage_cost_ledger(
        manifest_paths=[manifest_path],
        expected_revision=manifest["candidate_revision"],
        expected_tree=manifest["candidate_tree"],
        authorization_sha256=manifest["authorization_sha256"],
        expected_provider=manifest["provider"],
        expected_model=manifest["model"],
        expected_source_manifest_sha256s={"initial_question": source_sha256},
        execution_manifest_sha256="d" * 64,
    )
    initial_question = next(
        item for item in ledger.runs if item.dimension == "initial_question"
    )

    assert code == 2
    assert initial_question.missing_fields == ["provider_attempt_receipt_sha256"]
    assert initial_question.status == "BLOCKED"
    assert initial_question.evidence_origin == "synthetic_fixture"
    assert initial_question.formal_evidence_eligible is False
    assert initial_question.planned_requests + initial_question.retries == initial_question.inference_attempted


def test_nonempty_run_directory_is_rejected(tmp_path):
    run_dir = tmp_path / "existing"
    run_dir.mkdir()
    (run_dir / "stale.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="already exists"):
        cli.main(["--out", str(tmp_path), "--run-id", "existing"])


@pytest.mark.parametrize(
    "metadata",
    [
        {"provider_input_tokens": 1, "provider_cached_input_tokens": 0},
        {"provider_input_tokens": 1, "provider_output_tokens": 1},
        {
            "provider_input_tokens": 1,
            "provider_output_tokens": "1",
            "provider_cached_input_tokens": 0,
        },
    ],
)
def test_initial_consumer_rejects_partial_or_invalid_usage(metadata):
    assert cli._complete_provider_token_usage(metadata) is None


def test_initial_consumer_preserves_explicit_zero_usage():
    metadata = {
        "provider_input_tokens": 0,
        "provider_output_tokens": 0,
        "provider_cached_input_tokens": 0,
    }
    assert cli._complete_provider_token_usage(metadata) == metadata


def test_manifest_repair_lifecycle_uses_explicit_state_not_invocation_count():
    repaired = SimpleNamespace(
        initial_hard_finding_codes=("overloaded_multi_ask",),
        quality_repair_triggered=True,
        quality_repair_succeeded=True,
        post_repair_hard_finding_codes=(),
    )
    artifact = SimpleNamespace(
        attempts=(repaired,),
        failed_generation_lifecycles=(),
        outbound_requests_attempted=99,
    )
    manifest = {}

    cli._apply_repair_lifecycle_manifest(manifest, artifact)

    assert manifest == {
        "initial_hard_finding_codes": ["overloaded_multi_ask"],
        "quality_repair_triggered": True,
        "quality_repair_succeeded": True,
        "post_repair_hard_finding_codes": [],
    }


def test_manifest_repair_lifecycle_preserves_unknown_failed_outcome():
    failed = SimpleNamespace(
        initial_hard_finding_codes=("answer_leakage",),
        quality_repair_triggered=True,
        quality_repair_succeeded=None,
        post_repair_hard_finding_codes=None,
    )
    artifact = SimpleNamespace(
        attempts=(),
        failed_generation_lifecycles=(failed,),
    )
    manifest = {}

    cli._apply_repair_lifecycle_manifest(manifest, artifact)

    assert manifest["initial_hard_finding_codes"] == ["answer_leakage"]
    assert manifest["quality_repair_triggered"] is True
    assert manifest["quality_repair_succeeded"] is None
    assert manifest["post_repair_hard_finding_codes"] is None


def test_saved_replay_repair_prompt_identity_is_fail_closed():
    current = SimpleNamespace(
        quality_repair_prompt_version=PLAN_QUALITY_REPAIR_PROMPT_VERSION,
        quality_repair_prompt_sha256=PLAN_QUALITY_REPAIR_PROMPT_SHA256,
    )
    artifact = SimpleNamespace(
        attempts=(current,),
        failed_generation_lifecycles=(),
    )
    assert cli._artifact_repair_prompt_identity_is_current(artifact) is True

    drifted = SimpleNamespace(
        quality_repair_prompt_version="plan-quality-repair-old",
        quality_repair_prompt_sha256="0" * 64,
    )
    artifact = SimpleNamespace(
        attempts=(drifted,),
        failed_generation_lifecycles=(),
    )
    assert cli._artifact_repair_prompt_identity_is_current(artifact) is False


def test_usage_manifest_keeps_all_token_totals_null_when_any_usage_is_unknown():
    attempts = (
        SimpleNamespace(
            provider_retries=0,
            provider_invocations=1,
            input_tokens=10,
            output_tokens=2,
            cached_input_tokens=1,
        ),
        SimpleNamespace(
            provider_retries=0,
            provider_invocations=1,
            input_tokens=20,
            output_tokens=None,
            cached_input_tokens=0,
        ),
    )
    artifact = SimpleNamespace(
        attempts=attempts,
        outbound_requests_attempted=2,
        outbound_requests_metered=2,
    )
    manifest = {}
    cli._apply_usage_manifest(manifest, artifact)
    assert manifest["input_tokens"] is None
    assert manifest["output_tokens"] is None
    assert manifest["cached_input_tokens"] is None
    assert manifest["estimated_cost"] is None


def test_usage_manifest_aggregates_explicit_zero_without_treating_it_as_unknown():
    attempt = SimpleNamespace(
        provider_retries=0,
        provider_invocations=1,
        input_tokens=0,
        output_tokens=0,
        cached_input_tokens=0,
    )
    artifact = SimpleNamespace(
        attempts=(attempt,),
        outbound_requests_attempted=1,
        outbound_requests_metered=1,
    )
    manifest = {}
    cli._apply_usage_manifest(manifest, artifact)
    assert manifest["input_tokens"] == 0
    assert manifest["output_tokens"] == 0
    assert manifest["cached_input_tokens"] == 0
    assert manifest["estimated_cost"] is None


def test_live_capture_hard_stops_and_keeps_manifest_null_on_partial_usage(
    monkeypatch,
):
    dataset = cli.load_interview_quality_dataset(cli.DEFAULT_DATASET)
    dataset = dataset.model_copy(update={"cases": dataset.cases[:1]})
    legacy = v2_plan_to_legacy(
        build_synthetic_initial_question_attempts(dataset)[0].plan
    )
    monkeypatch.setattr(
        cli,
        "OpenAIInterviewLLM",
        lambda _config: SimpleNamespace(
            generate_plan=lambda *_args, **_kwargs: legacy
        ),
    )
    monkeypatch.setattr(
        cli,
        "consume_provider_context_metadata",
        lambda: {
            **no_repair_lifecycle_metadata(),
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 1,
            "provider_usage_available": True,
            "provider_model": "deepseek-v4-pro",
            "provider_input_tokens": 10,
            "provider_cached_input_tokens": 0,
        },
    )
    artifact = cli._record_live_provider_responses(
        dataset,
        dataset_sha256="a" * 64,
        authorization=cli.load_provider_authorization(cli.DEFAULT_AUTHORIZATION),
        api_key="not-serialized",
        timeout_seconds=1.0,
        context_window_tokens=128_000,
        smoke=True,
    )
    assert artifact.capture_status == "hard_stopped"
    assert artifact.hard_stop_conditions == ("USAGE_METERING_UNAVAILABLE",)
    assert artifact.outbound_requests_attempted == 1
    assert artifact.outbound_requests_metered == 1
    assert artifact.attempts == ()

    manifest = {}
    cli._apply_usage_manifest(manifest, artifact)
    assert manifest["input_tokens"] is None
    assert manifest["output_tokens"] is None
    assert manifest["cached_input_tokens"] is None
    assert manifest["estimated_cost"] is None


def test_live_capture_selects_single_request_raw_json_mode(monkeypatch):
    dataset = cli.load_interview_quality_dataset(cli.DEFAULT_DATASET)
    dataset = dataset.model_copy(update={"cases": dataset.cases[:1]})
    legacy = v2_plan_to_legacy(
        build_synthetic_initial_question_attempts(dataset)[0].plan
    )
    captured_configs = []

    def build_llm(config):
        captured_configs.append(config)
        return SimpleNamespace(generate_plan=lambda *_args, **_kwargs: legacy)

    monkeypatch.setattr(cli, "OpenAIInterviewLLM", build_llm)
    monkeypatch.setattr(
        cli,
        "consume_provider_context_metadata",
        lambda: {
            **no_repair_lifecycle_metadata(),
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 1,
            "provider_usage_available": True,
            "provider_model": "deepseek-v4-pro",
            "provider_input_tokens": 10,
            "provider_output_tokens": 2,
            "provider_cached_input_tokens": 0,
        },
    )

    artifact = cli._record_live_provider_responses(
        dataset,
        dataset_sha256="a" * 64,
        authorization=cli.load_provider_authorization(cli.DEFAULT_AUTHORIZATION),
        api_key="not-serialized",
        timeout_seconds=1.0,
        context_window_tokens=128_000,
        smoke=True,
    )

    assert captured_configs[0].plan_output_mode == "raw_only"
    assert captured_configs[0].model == "deepseek-v4-pro"
    assert captured_configs[0].base_url == "https://api.deepseek.com"
    assert captured_configs[0].max_retries == 0
    assert captured_configs[0].context_window_tokens == 128_000
    assert artifact.capture_status == "complete"
    assert artifact.outbound_requests_attempted == 1
    assert artifact.outbound_requests_metered == 1
    assert artifact.schema_version == "initial-question-provider-replay-v2"
    attempt = artifact.attempts[0]
    assert attempt.initial_hard_finding_codes == ()
    assert attempt.quality_repair_triggered is False
    assert attempt.quality_repair_succeeded is None
    assert attempt.post_repair_hard_finding_codes is None
    assert attempt.quality_repair_prompt_version == (
        PLAN_QUALITY_REPAIR_PROMPT_VERSION
    )
    assert attempt.quality_repair_prompt_sha256 == (
        PLAN_QUALITY_REPAIR_PROMPT_SHA256
    )


def test_live_capture_honors_preselected_smoke_case_count(monkeypatch):
    dataset = cli.load_interview_quality_dataset(cli.DEFAULT_DATASET)
    dataset = dataset.model_copy(update={"cases": dataset.cases[:2]})
    legacy = v2_plan_to_legacy(
        build_synthetic_initial_question_attempts(dataset)[0].plan
    )
    business_calls = []

    def generate_plan(*_args, **_kwargs):
        business_calls.append("started")
        return legacy

    monkeypatch.setattr(
        cli,
        "OpenAIInterviewLLM",
        lambda _config: SimpleNamespace(generate_plan=generate_plan),
    )
    monkeypatch.setattr(
        cli,
        "consume_provider_context_metadata",
        lambda: {
            **no_repair_lifecycle_metadata(),
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 1,
            "provider_usage_available": True,
            "provider_model": "deepseek-v4-pro",
            "provider_input_tokens": 10,
            "provider_output_tokens": 2,
            "provider_cached_input_tokens": 0,
        },
    )

    artifact = cli._record_live_provider_responses(
        dataset,
        dataset_sha256="a" * 64,
        authorization=cli.load_provider_authorization(cli.DEFAULT_AUTHORIZATION),
        api_key="not-serialized",
        timeout_seconds=1.0,
        context_window_tokens=128_000,
        smoke=True,
    )

    assert business_calls == ["started", "started"]
    assert len(artifact.attempts) == 2
    assert artifact.outbound_requests_attempted == 2
    assert artifact.outbound_requests_metered == 2


def test_live_capture_stops_before_starting_the_next_business_sample(monkeypatch):
    dataset = cli.load_interview_quality_dataset(cli.DEFAULT_DATASET)
    dataset = dataset.model_copy(update={"cases": dataset.cases[:2]})
    business_calls = []

    def generate_plan(*_args, **_kwargs):
        business_calls.append("started")
        raise RuntimeError("provider request failed")

    monkeypatch.setattr(
        cli,
        "OpenAIInterviewLLM",
        lambda _config: SimpleNamespace(generate_plan=generate_plan),
    )
    monkeypatch.setattr(
        cli,
        "consume_provider_context_metadata",
        lambda: {
            **no_repair_lifecycle_metadata(),
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 0,
        },
    )

    artifact = cli._record_live_provider_responses(
        dataset,
        dataset_sha256="a" * 64,
        authorization=cli.load_provider_authorization(cli.DEFAULT_AUTHORIZATION),
        api_key="not-serialized",
        timeout_seconds=1.0,
        context_window_tokens=128_000,
        smoke=True,
    )

    assert business_calls == ["started"]
    assert artifact.capture_status == "hard_stopped"
    assert artifact.outbound_requests_attempted == 1


def test_live_capture_records_usage_and_response_id_stops_together(monkeypatch):
    dataset = cli.load_interview_quality_dataset(cli.DEFAULT_DATASET)
    dataset = dataset.model_copy(update={"cases": dataset.cases[:1]})

    monkeypatch.setattr(
        cli,
        "OpenAIInterviewLLM",
        lambda _config: SimpleNamespace(
            generate_plan=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("provider response omitted usage")
            )
        ),
    )
    monkeypatch.setattr(
        cli,
        "consume_provider_context_metadata",
        lambda: {
            **no_repair_lifecycle_metadata(),
            "provider_attempt_count": 2,
            "provider_metered_attempt_count": 1,
            "provider_usage_available": False,
            "provider_model": "deepseek-v4-pro",
            "provider_response_id_sha256s": [],
        },
    )

    artifact = cli._record_live_provider_responses(
        dataset,
        dataset_sha256="a" * 64,
        authorization=cli.load_provider_authorization(cli.DEFAULT_AUTHORIZATION),
        api_key="not-serialized",
        timeout_seconds=1.0,
        context_window_tokens=128_000,
        smoke=True,
    )

    lifecycle = artifact.failed_generation_lifecycles[0]
    assert artifact.hard_stop_conditions == (
        "USAGE_METERING_UNAVAILABLE",
        "RESPONSE_ID_EVIDENCE_UNAVAILABLE",
    )
    assert lifecycle.hard_stop_condition == "RESPONSE_ID_EVIDENCE_UNAVAILABLE"


def test_failed_repair_request_persists_redacted_lifecycle_and_exact_requests(
    monkeypatch,
):
    dataset = cli.load_interview_quality_dataset(cli.DEFAULT_DATASET)
    dataset = dataset.model_copy(update={"cases": dataset.cases[:1]})
    item = cli.InitialQuestionCaseInput.model_validate(dataset.cases[0].input)
    monkeypatch.setattr(
        cli,
        "OpenAIInterviewLLM",
        lambda _config: SimpleNamespace(
            generate_plan=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("schema failed after repair trigger")
            )
        ),
    )
    monkeypatch.setattr(
        cli,
        "consume_provider_context_metadata",
        lambda: {
            "provider_attempt_count": 2,
            "provider_metered_attempt_count": 2,
            "provider_usage_available": True,
            "provider_model": "deepseek-v4-pro",
            "provider_input_tokens": 20,
            "provider_output_tokens": 4,
            "provider_cached_input_tokens": 0,
            "provider_response_id_sha256s": ["a" * 64, "b" * 64],
            "initial_hard_finding_codes": ["overloaded_multi_ask"],
            "quality_repair_triggered": True,
            "quality_repair_succeeded": None,
            "post_repair_hard_finding_codes": None,
            "quality_repair_prompt_version": PLAN_QUALITY_REPAIR_PROMPT_VERSION,
            "quality_repair_prompt_sha256": PLAN_QUALITY_REPAIR_PROMPT_SHA256,
        },
    )

    artifact = cli._record_live_provider_responses(
        dataset,
        dataset_sha256="a" * 64,
        authorization=cli.load_provider_authorization(cli.DEFAULT_AUTHORIZATION),
        api_key="not-serialized",
        timeout_seconds=1.0,
        context_window_tokens=128_000,
        smoke=True,
    )

    assert artifact.capture_status == "hard_stopped"
    assert artifact.outbound_requests_attempted == 2
    assert artifact.outbound_requests_metered == 2
    assert artifact.attempts == ()
    assert len(artifact.failed_generation_lifecycles) == 1
    lifecycle = artifact.failed_generation_lifecycles[0]
    assert lifecycle.initial_hard_finding_codes == ("overloaded_multi_ask",)
    assert lifecycle.quality_repair_triggered is True
    assert lifecycle.quality_repair_succeeded is None
    assert lifecycle.post_repair_hard_finding_codes is None
    assert lifecycle.response_id_sha256s == ("a" * 64, "b" * 64)
    serialized = artifact.model_dump_json()
    assert item.job_description not in serialized
    assert item.resume_summary not in serialized
    assert "schema failed after repair trigger" not in serialized


def test_live_capture_model_mismatch_stops_before_next_business_sample(monkeypatch):
    dataset = cli.load_interview_quality_dataset(cli.DEFAULT_DATASET)
    dataset = dataset.model_copy(update={"cases": dataset.cases[:2]})
    legacy = v2_plan_to_legacy(
        build_synthetic_initial_question_attempts(dataset)[0].plan
    )
    business_calls = []

    def generate_plan(*_args, **_kwargs):
        business_calls.append("started")
        return legacy

    monkeypatch.setattr(
        cli,
        "OpenAIInterviewLLM",
        lambda _config: SimpleNamespace(generate_plan=generate_plan),
    )
    monkeypatch.setattr(
        cli,
        "consume_provider_context_metadata",
        lambda: {
            **no_repair_lifecycle_metadata(),
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 1,
            "provider_usage_available": True,
            "provider_model": "deepseek-v4-flash",
            "provider_input_tokens": 10,
            "provider_output_tokens": 2,
            "provider_cached_input_tokens": 0,
        },
    )

    artifact = cli._record_live_provider_responses(
        dataset,
        dataset_sha256="a" * 64,
        authorization=cli.load_provider_authorization(cli.DEFAULT_AUTHORIZATION),
        api_key="not-serialized",
        timeout_seconds=1.0,
        context_window_tokens=128_000,
        smoke=True,
    )

    assert business_calls == ["started"]
    assert artifact.capture_status == "hard_stopped"
    assert artifact.hard_stop_conditions == ("PROVIDER_OR_MODEL_MISMATCH",)
    assert artifact.outbound_requests_attempted == 1
    assert artifact.outbound_requests_metered == 1


def test_live_capture_rejects_model_drift_even_when_final_model_is_authorized(
    monkeypatch,
):
    dataset = cli.load_interview_quality_dataset(cli.DEFAULT_DATASET)
    dataset = dataset.model_copy(update={"cases": dataset.cases[:2]})
    legacy = v2_plan_to_legacy(
        build_synthetic_initial_question_attempts(dataset)[0].plan
    )
    business_calls = []

    monkeypatch.setattr(
        cli,
        "OpenAIInterviewLLM",
        lambda _config: SimpleNamespace(
            generate_plan=lambda *_args, **_kwargs: (
                business_calls.append("started") or legacy
            )
        ),
    )
    monkeypatch.setattr(
        cli,
        "consume_provider_context_metadata",
        lambda: {
            **no_repair_lifecycle_metadata(),
            "provider_attempt_count": 2,
            "provider_metered_attempt_count": 2,
            "provider_usage_available": True,
            "provider_model": "deepseek-v4-pro",
            "provider_response_models": ["deepseek-v4-pro", "deepseek-v4-flash"],
            "provider_input_tokens": 20,
            "provider_output_tokens": 4,
            "provider_cached_input_tokens": 0,
            "provider_response_id_sha256s": ["a" * 64, "b" * 64],
        },
    )

    artifact = cli._record_live_provider_responses(
        dataset,
        dataset_sha256="a" * 64,
        authorization=cli.load_provider_authorization(cli.DEFAULT_AUTHORIZATION),
        api_key="not-serialized",
        timeout_seconds=1.0,
        context_window_tokens=128_000,
        smoke=True,
    )

    assert business_calls == ["started"]
    assert artifact.hard_stop_conditions == ("PROVIDER_OR_MODEL_MISMATCH",)
