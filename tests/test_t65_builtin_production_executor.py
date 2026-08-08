from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.t65_builtin_production_executor import (
    T65ApiProbeResult,
    T65BuiltinExecutorConfig,
    T65BuiltinExecutorError,
    T65CleanupHooks,
    T65CleanupState,
    T65CommandSpec,
    T65LaunchOptions,
    T65OrchestrationDependencies,
    T65PopenSpec,
    T65ReadinessObservation,
    T65ReadinessMarker,
    accept_owned_readiness,
    bind_owned_t65_process,
    build_report_worker_command_spec,
    build_sse_resume_request,
    build_t65_popen_spec,
    build_t65_process_environment,
    build_t65_readiness_marker,
    build_uvicorn_command_spec,
    evaluate_api_probe,
    generate_t65_prefix_pair,
    report_signal_for_config,
    run_t65_owned_orchestration,
    start_owned_t65_process,
    synthesize_t65_usage,
    terminate_owned_t65_process,
    validate_t65_popen_spec,
    validate_t65_prefix_pair,
    verify_t65_readiness_marker,
)


def _config(**updates) -> T65BuiltinExecutorConfig:
    values = {
        "run_id": "t65-phase-a",
        "candidate_revision": "1" * 40,
        "candidate_tree": "2" * 40,
        "authorization_id": "authorization-1",
        "authorization_sha256": "3" * 64,
        "executor_sha256": "4" * 64,
    }
    values.update(updates)
    return T65BuiltinExecutorConfig(**values)


def _prefixes():
    return validate_t65_prefix_pair(
        "test_t65perf_aaaaaaaaaaaa",
        "test_t65perf_bbbbbbbbbbbb",
    )


def test_frozen_executor_config_is_secret_free_canonical_and_not_formal_evidence():
    config = _config()

    assert config.report_enabled is False
    assert config.provider_name == "DeepSeek"
    assert config.model_id == "deepseek-v4-pro"
    assert len(config.config_sha256) == 64
    assert "api_key" not in config.model_dump()
    assert "evidence_origin" not in config.model_dump()
    assert "formal" not in json.dumps(config.model_dump())
    with pytest.raises(ValidationError):
        config.run_id = "changed"


@pytest.mark.parametrize(
    "updates",
    [
        {"run_id": "unsafe run"},
        {"candidate_revision": "bad"},
        {"candidate_tree": "f" * 39},
        {"authorization_sha256": "A" * 64},
        {"executor_sha256": "0" * 63},
        {"provider_name": "OpenAI"},
        {"model_id": "deepseek-chat"},
        {"base_url": "https://api.deepseek.com/v1"},
        {"context_window_tokens": 64_000},
        {"report_enabled": True},
        {"authorization_id": "authorization\x00injected"},
        {"authorization_id": "authorization\nsecond-line"},
    ],
)
def test_executor_config_fails_closed_on_identity_model_and_report_drift(updates):
    with pytest.raises(ValidationError):
        _config(**updates)


def test_prefix_generation_is_deterministic_with_injected_entropy_and_validated():
    tokens = iter(("aaaaaaaaaaaa", "bbbbbbbbbbbb"))
    pair = generate_t65_prefix_pair(token_factory=lambda _: next(tokens))

    assert pair.runtime_prefix == "test_t65perf_aaaaaaaaaaaa"
    assert pair.vector_prefix == "test_t65perf_bbbbbbbbbbbb"
    assert pair.vector_versions_table.endswith("_versions")
    assert pair.vector_releases_table.endswith("_releases")


@pytest.mark.parametrize(
    ("runtime", "vector"),
    [
        ("interview", "test_t65perf_bbbbbbbbbbbb"),
        ("test_t65perf_AAAAAAAAAAAA", "test_t65perf_bbbbbbbbbbbb"),
        ("test_t65perf_aaaaaaaaaaa", "test_t65perf_bbbbbbbbbbbb"),
        ("test_t65perf_aaaaaaaaaaaa", "test_t65perf_aaaaaaaaaaaa"),
        ("test_t65perf_aaaaaaaaaaaa", "vector"),
    ],
)
def test_prefix_validation_rejects_collision_and_nonisolated_names(runtime, vector):
    with pytest.raises(T65BuiltinExecutorError):
        validate_t65_prefix_pair(runtime, vector)


def test_prefix_generation_rejects_bad_or_colliding_entropy():
    with pytest.raises(T65BuiltinExecutorError):
        generate_t65_prefix_pair(token_factory=lambda _: "a" * 12)
    with pytest.raises(T65BuiltinExecutorError):
        generate_t65_prefix_pair(token_factory=lambda _: "not-hex-value")


def test_api_environment_is_new_allowlist_and_maps_only_deepseek_credential():
    environment = build_t65_process_environment(
        config=_config(),
        prefixes=_prefixes(),
        process_role="api",
        base_environment={
            "PATH": "C:/Python",
            "SystemRoot": "C:/Windows",
            "HTTP_PROXY": "http://proxy.invalid",
            "HTTPS_PROXY": "http://proxy.invalid",
            "NO_PROXY": "*",
            "SILICONFLOW_API_KEY": "embedding-secret",
            "ANTHROPIC_API_KEY": "other-secret",
            "AZURE_OPENAI_API_KEY": "azure-secret",
            "DEEPSEEK_API_KEY": "parent-deepseek-secret",
            "OPENAI_API_KEY": "parent-openai-secret",
            "OPENAI_MODEL": "unapproved-model",
            "INTERVIEW_TABLE_PREFIX": "shared",
            "RANDOM_PARENT_STATE": "must-not-inherit",
        },
        postgres_dsn="postgresql://runtime-secret",
        deepseek_api_key="authorized-deepseek-secret",
    ).as_dict()

    assert environment["OPENAI_API_KEY"] == "authorized-deepseek-secret"
    assert environment["OPENAI_MODEL"] == "deepseek-v4-pro"
    assert environment["OPENAI_BASE_URL"] == "https://api.deepseek.com"
    assert environment["OPENAI_MAX_RETRIES"] == "0"
    assert environment["POSTGRES_RUNTIME_AUTO_MIGRATE"] == "false"
    assert environment["INTERVIEW_EVENT_BACKEND"] == "local"
    assert environment["T65_REPORT_ENABLED"] == "false"
    assert environment["T65_EXECUTION_SCOPE"] == "interview_only"
    for forbidden in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SILICONFLOW_API_KEY",
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "INTERVIEW_TABLE_PREFIX",
        "RANDOM_PARENT_STATE",
        "REPORT_RUNTIME_PROFILE",
        "REPORT_JOB_STORE",
        "REPORT_WORKER",
        "REPORT_ARTIFACT_READ_MODE",
        "KNOWLEDGE_STORE",
        "PGVECTOR_TABLE",
        "EMBEDDING_PROVIDER",
    ):
        assert forbidden not in environment


def test_process_environment_repr_and_config_identity_never_contain_secrets():
    process_environment = build_t65_process_environment(
        config=_config(),
        prefixes=_prefixes(),
        process_role="api",
        base_environment={"PATH": "C:/Python"},
        postgres_dsn="postgresql://user:dsn-canary@host/database",
        deepseek_api_key="deepseek-key-canary",
    )
    marker = build_t65_readiness_marker(
        config=_config(),
        prefixes=_prefixes(),
        process_role="api",
        process_id=123,
    )

    for rendered in (
        repr(process_environment),
        json.dumps(_config().canonical_payload()),
        _config().config_sha256,
        marker.canonical_line(),
    ):
        assert "dsn-canary" not in rendered
        assert "deepseek-key-canary" not in rendered
    second = build_t65_process_environment(
        config=_config(),
        prefixes=_prefixes(),
        process_role="api",
        base_environment={"PATH": "C:/Python"},
        postgres_dsn="postgresql://user:dsn-canary@host/database",
        deepseek_api_key="deepseek-key-canary",
    )
    assert process_environment is not second
    assert process_environment != second
    assert not hasattr(process_environment, "values")
    exported = process_environment.as_dict()
    exported["OPENAI_API_KEY"] = "changed-copy"
    assert process_environment.as_dict()["OPENAI_API_KEY"] == "deepseek-key-canary"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"postgres_dsn": ""},
        {"deepseek_api_key": ""},
        {"process_role": "report_worker", "worker_id": None},
        {"process_role": "report_worker", "worker_id": "unsafe worker"},
        {"process_role": "api", "worker_id": "worker-1"},
        {"postgres_dsn": "postgresql://host/db\x00bad"},
        {"deepseek_api_key": "key\nsecond-line"},
        {"base_environment": {"PATH": "C:/Python\x00bad"}},
    ],
)
def test_environment_construction_fails_closed_on_missing_or_cross_role_state(kwargs):
    values = {
        "config": _config(),
        "prefixes": _prefixes(),
        "process_role": "api",
        "base_environment": {},
        "postgres_dsn": "postgresql://runtime-secret",
        "deepseek_api_key": "deepseek-secret",
        "worker_id": None,
    }
    values.update(kwargs)
    with pytest.raises(T65BuiltinExecutorError):
        build_t65_process_environment(**values)


def test_command_spec_is_fixed_loopback_and_report_worker_is_blocked(tmp_path: Path):
    python = (tmp_path / "python.exe").absolute()
    root = (tmp_path / "repo").absolute()
    api = build_uvicorn_command_spec(
        python_executable=python,
        repository_root=root,
        port=48123,
    )
    assert api.argv == (
        str(python),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "48123",
        "--no-proxy-headers",
    )
    assert "0.0.0.0" not in api.argv
    assert api.cwd == root
    with pytest.raises(T65BuiltinExecutorError, match="embedding authorization"):
        build_report_worker_command_spec(
            config=_config(),
            python_executable=python,
            repository_root=root,
        )


@pytest.mark.parametrize("port", [0, 1023, 65536, True, "8000"])
def test_uvicorn_command_rejects_unsafe_ports(tmp_path: Path, port):
    with pytest.raises(T65BuiltinExecutorError):
        build_uvicorn_command_spec(
            python_executable=(tmp_path / "python.exe").absolute(),
            repository_root=(tmp_path / "repo").absolute(),
            port=port,
        )


def test_command_specs_require_absolute_paths():
    with pytest.raises(T65BuiltinExecutorError):
        build_uvicorn_command_spec(
            python_executable=Path("python"),
            repository_root=Path("repo"),
            port=48123,
        )


def test_readiness_marker_is_canonical_hash_bound_and_identity_checked():
    marker = build_t65_readiness_marker(
        config=_config(),
        prefixes=_prefixes(),
        process_role="api",
        process_id=123,
    )

    assert marker.canonical_line() == marker.canonical_line()
    parsed = verify_t65_readiness_marker(
        marker.canonical_line(),
        config=_config(),
        prefixes=_prefixes(),
        expected_role="api",
    )
    assert parsed == marker
    assert "test_t65perf" not in marker.canonical_line()
    assert "postgresql" not in marker.canonical_line()

    with pytest.raises(T65BuiltinExecutorError, match="embedding authorization"):
        build_t65_readiness_marker(
            config=_config(),
            prefixes=_prefixes(),
            process_role="report_worker",
            process_id=123,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["payload"].update(run_id="different-run"),
        lambda value: value["payload"].update(model_id="deepseek-chat"),
        lambda value: value["payload"].update(process_role="report_worker"),
        lambda value: value["payload"].update(runtime_prefix_sha256="0" * 64),
        lambda value: value.update(payload_sha256="0" * 64),
    ],
)
def test_readiness_marker_rejects_hash_identity_role_and_model_drift(mutation):
    marker = build_t65_readiness_marker(
        config=_config(),
        prefixes=_prefixes(),
        process_role="api",
        process_id=123,
    ).model_dump(mode="json")
    mutation(marker)

    with pytest.raises(T65BuiltinExecutorError):
        verify_t65_readiness_marker(
            marker,
            config=_config(),
            prefixes=_prefixes(),
            expected_role="api",
        )


def test_readiness_model_rejects_extra_fields_even_with_rehashed_payload():
    marker = build_t65_readiness_marker(
        config=_config(),
        prefixes=_prefixes(),
        process_role="api",
        process_id=123,
    ).model_dump(mode="json")
    marker["unexpected"] = True
    with pytest.raises(ValidationError):
        T65ReadinessMarker.model_validate(marker)


def test_sse_resume_request_is_loopback_cursor_bound_and_path_encoded():
    request = build_sse_resume_request(
        port=48123,
        session_id="session/id",
        command_id="command id",
        last_event_id="generation-1:2:7",
    )

    assert request.method == "GET"
    assert request.url == (
        "http://127.0.0.1:48123/api/interviews/session%2Fid/"
        "commands/command%20id/stream"
    )
    assert ("Last-Event-ID", "generation-1:2:7") in request.headers


@pytest.mark.parametrize(
    "values",
    [
        {"last_event_id": ""},
        {"last_event_id": "event\r\nAuthorization: secret"},
        {"session_id": "bad\x00session"},
        {"command_id": "bad\ncommand"},
    ],
)
def test_sse_resume_request_rejects_empty_and_header_injection(values):
    kwargs = {
        "port": 48123,
        "session_id": "session-1",
        "command_id": "command-1",
        "last_event_id": "generation-1:1:1",
    }
    kwargs.update(values)
    with pytest.raises(T65BuiltinExecutorError):
        build_sse_resume_request(**kwargs)


def test_usage_synthesis_preserves_unknown_instead_of_coercing_zero():
    summary = synthesize_t65_usage(
        {
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 1,
            "provider_input_tokens": 100,
            "provider_output_tokens": 20,
            "provider_cached_input_tokens": None,
            "decision_output_tokens": 4,
            "followup_output_tokens": 16,
        }
    )

    assert summary.provider_attempt_count == 1
    assert summary.provider_cached_input_tokens is None
    assert synthesize_t65_usage().provider_attempt_count is None


def test_usage_synthesis_sums_only_complete_component_fields():
    summary = synthesize_t65_usage(
        {
            "provider_attempt_count": 1,
            "provider_input_tokens": 10,
            "provider_output_tokens": 2,
        },
        {
            "provider_attempt_count": 2,
            "provider_input_tokens": 20,
            "provider_output_tokens": None,
        },
    )

    assert summary.provider_attempt_count == 3
    assert summary.provider_input_tokens == 30
    assert summary.provider_output_tokens is None
    assert summary.provider_metered_attempt_count is None


def test_recovery_usage_requires_every_field_explicitly_zero():
    zero = {field: 0 for field in (
        "provider_attempt_count",
        "provider_metered_attempt_count",
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_cached_input_tokens",
        "decision_output_tokens",
        "followup_output_tokens",
    )}
    assert synthesize_t65_usage(zero, recovery=True).provider_attempt_count == 0

    for mutation in (
        {**zero, "provider_attempt_count": 1},
        {**zero, "provider_cached_input_tokens": None},
    ):
        with pytest.raises(T65BuiltinExecutorError, match="explicitly prove zero"):
            synthesize_t65_usage(mutation, recovery=True)


@pytest.mark.parametrize(
    "usage",
    [
        {"provider_attempt_count": -1},
        {"provider_attempt_count": True},
        {
            "provider_attempt_count": 1,
            "provider_metered_attempt_count": 2,
        },
        {
            "provider_input_tokens": 10,
            "provider_cached_input_tokens": 11,
        },
    ],
)
def test_usage_synthesis_rejects_invalid_and_inconsistent_values(usage):
    with pytest.raises(T65BuiltinExecutorError):
        synthesize_t65_usage(usage)


def test_report_disabled_has_explicit_blocked_signal_without_fake_timing():
    signal = report_signal_for_config(_config())

    assert signal.status == "BLOCKED"
    assert signal.seconds is None
    assert signal.reason == "SILICONFLOW_EMBEDDING_AUTHORIZATION_REQUIRED"


def test_cleanup_state_machine_enforces_shutdown_evidence_drop_order():
    state = T65CleanupState()
    for phase in (
        "requests_stopped",
        "api_stopped",
        "worker_stopped",
        "evidence_finalized",
        "relations_dropped",
        "temporary_files_removed",
        "complete",
    ):
        state = state.advance(phase)

    assert state.cleanup_complete is True
    assert state.hard_stop_conditions == ()


def test_cleanup_state_rejects_skips_repeats_and_backwards_transition():
    state = T65CleanupState()
    for invalid in ("api_stopped", "initialized", "complete"):
        with pytest.raises(T65BuiltinExecutorError):
            state.advance(invalid)
    state = state.advance("requests_stopped")
    with pytest.raises(T65BuiltinExecutorError):
        state.advance("requests_stopped")
    with pytest.raises(T65BuiltinExecutorError):
        state.advance("initialized")


def test_cleanup_errors_are_aggregated_without_secret_messages_and_block_completion():
    state = T65CleanupState().advance("requests_stopped")
    state = state.record_error(
        code="API_SHUTDOWN_FAILED",
        error=RuntimeError("postgresql://user:secret@host/database"),
    )
    state = state.record_error(
        code="API_SHUTDOWN_FAILED",
        error=TimeoutError("another secret"),
    )
    state = state.advance("api_stopped")
    state = state.record_error(
        code="WORKER_SHUTDOWN_FAILED",
        error=RuntimeError("provider-key"),
    )

    assert state.cleanup_complete is False
    assert state.hard_stop_conditions == (
        "API_SHUTDOWN_FAILED",
        "WORKER_SHUTDOWN_FAILED",
    )
    assert "secret" not in repr(state)
    assert "provider-key" not in repr(state)


def test_cleanup_rejects_unstable_error_codes():
    with pytest.raises(T65BuiltinExecutorError):
        T65CleanupState().record_error(
            code="bad-code",
            error=RuntimeError("ignored"),
        )


class _FakeProcess:
    def __init__(self, *, pid: int = 321, log=None, wait_outcomes=None):
        self.pid = pid
        self.log = log if log is not None else []
        self.wait_outcomes = list(wait_outcomes or [0])
        self.exit_code = None

    def poll(self):
        self.log.append("poll")
        return self.exit_code

    def terminate(self):
        self.log.append("terminate")

    def kill(self):
        self.log.append("kill")

    def wait(self, timeout):
        self.log.append(("wait", timeout))
        outcome = self.wait_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        self.exit_code = outcome
        return outcome


def _process_environment():
    return build_t65_process_environment(
        config=_config(),
        prefixes=_prefixes(),
        process_role="api",
        base_environment={"PATH": "C:/Python"},
        postgres_dsn="postgresql://runtime-secret",
        deepseek_api_key="deepseek-secret",
    )


def _command(tmp_path: Path):
    return build_uvicorn_command_spec(
        python_executable=(tmp_path / "python.exe").absolute(),
        repository_root=(tmp_path / "repo").absolute(),
        port=48123,
    )


def _popen_spec(tmp_path: Path, *, platform="windows"):
    return build_t65_popen_spec(
        command=_command(tmp_path),
        environment=_process_environment(),
        config=_config(),
        platform=platform,
    )


def _ready_marker(pid=321):
    return build_t65_readiness_marker(
        config=_config(),
        prefixes=_prefixes(),
        process_role="api",
        process_id=pid,
    )


def _ready_probe():
    return T65ApiProbeResult(
        health_http_status=200,
        health_payload={"status": "ok"},
        runtime_http_status=200,
        runtime_payload={
            "runtime_store": "postgres",
            "session_store": "PostgresInterviewSessionStore",
            "event_backend": "local",
            "report_runtime_ready": False,
            "knowledge_runtime_ready": False,
            "embedding_provider": "disabled",
            "configuration_warnings": ["pgvector_requires_embedding_provider"],
        },
    )


def test_popen_specs_model_hidden_windows_group_and_posix_session(tmp_path: Path):
    windows = _popen_spec(tmp_path, platform="windows")
    posix = _popen_spec(tmp_path, platform="posix")

    assert windows.launch == T65LaunchOptions(
        platform="windows",
        creationflags=0x200,
        startupinfo_flags=0x1,
        show_window=0,
        start_new_session=False,
    )
    assert posix.launch == T65LaunchOptions(
        platform="posix",
        creationflags=0,
        startupinfo_flags=0,
        show_window=None,
        start_new_session=True,
    )
    assert windows.stdin_mode == "DEVNULL"
    assert windows.stdout_mode == windows.stderr_mode == "PIPE"
    assert windows.encoding == "utf-8"
    assert "deepseek-secret" not in repr(windows)
    assert "runtime-secret" not in repr(windows)
    assert validate_t65_popen_spec(windows, config=_config()) is windows


def test_popen_spec_rejects_arbitrary_command_role_environment_and_launch_drift(
    tmp_path: Path,
):
    command = _command(tmp_path)
    bad_command = T65CommandSpec(
        process_role="api",
        argv=(*command.argv[:-1], "--proxy-headers"),
        cwd=command.cwd,
    )
    with pytest.raises(T65BuiltinExecutorError):
        build_t65_popen_spec(
            command=bad_command,
            environment=_process_environment(),
            config=_config(),
            platform="windows",
        )

    forged_values = _process_environment().as_dict()
    forged_values["OPENAI_MODEL"] = "unapproved-model"
    forged_environment = type(_process_environment())(
        process_role="api",
        _values=forged_values,
    )
    with pytest.raises(T65BuiltinExecutorError, match="fixed identity drifted"):
        build_t65_popen_spec(
            command=command,
            environment=forged_environment,
            config=_config(),
            platform="windows",
        )

    forged_values = _process_environment().as_dict()
    forged_values["SILICONFLOW_API_KEY"] = "secret"
    forged_environment = type(_process_environment())(
        process_role="api",
        _values=forged_values,
    )
    with pytest.raises(T65BuiltinExecutorError, match="non-allowlisted"):
        build_t65_popen_spec(
            command=command,
            environment=forged_environment,
            config=_config(),
            platform="posix",
        )

    spec = _popen_spec(tmp_path)
    drifted = T65PopenSpec(
        command=spec.command,
        environment=spec.environment,
        launch=T65LaunchOptions(
            platform="windows",
            creationflags=0,
            startupinfo_flags=0,
            show_window=None,
            start_new_session=False,
        ),
        stdin_mode=spec.stdin_mode,
        stdout_mode=spec.stdout_mode,
        stderr_mode=spec.stderr_mode,
        text=True,
        encoding=spec.encoding,
        config_sha256=spec.config_sha256,
        spec_sha256=spec.spec_sha256,
    )
    with pytest.raises(T65BuiltinExecutorError, match="launch options drifted"):
        validate_t65_popen_spec(drifted, config=_config())

    with pytest.raises(T65BuiltinExecutorError):
        build_t65_popen_spec(
            command=command,
            environment=_process_environment(),
            config=_config(run_id="different-run"),
            platform="windows",
        )


def test_owned_process_is_bound_to_started_handle_pid_role_and_config(tmp_path: Path):
    seen = []
    handle = _FakeProcess(pid=432)
    owned = start_owned_t65_process(
        spec=_popen_spec(tmp_path),
        config=_config(),
        starter=lambda spec: seen.append(spec) or handle,
    )

    assert len(seen) == 1
    assert seen[0].spec_sha256 == _popen_spec(tmp_path).spec_sha256
    assert owned.pid == 432
    assert owned.process_role == "api"
    assert owned.config_sha256 == _config().config_sha256
    assert "deepseek-secret" not in repr(owned)

    handle.pid = 999
    with pytest.raises(T65BuiltinExecutorError, match="PID drifted"):
        terminate_owned_t65_process(
            owned,
            config=_config(),
            graceful_timeout_seconds=1,
            kill_timeout_seconds=1,
        )


def test_owned_process_operations_reject_manually_forged_typed_handle(tmp_path: Path):
    legitimate = bind_owned_t65_process(
        spec=_popen_spec(tmp_path),
        config=_config(),
        handle=_FakeProcess(pid=321),
    )
    forged = type(legitimate)(
        process_role=legitimate.process_role,
        pid=legitimate.pid,
        config_sha256=legitimate.config_sha256,
        spec_sha256=legitimate.spec_sha256,
        _handle=legitimate._handle,
        _ownership_seal=object(),
    )

    with pytest.raises(T65BuiltinExecutorError, match="not created by the owner"):
        terminate_owned_t65_process(
            forged,
            config=_config(),
            graceful_timeout_seconds=1,
            kill_timeout_seconds=1,
        )
    with pytest.raises(T65BuiltinExecutorError, match="not created by the owner"):
        accept_owned_readiness(
            owned=forged,
            observation=T65ReadinessObservation(
                stdout_lines=(_ready_marker().canonical_line(),)
            ),
            config=_config(),
            prefixes=_prefixes(),
        )


@pytest.mark.parametrize("bad_pid", [0, -1, True, "321", None])
def test_owned_process_rejects_invalid_child_pid(tmp_path: Path, bad_pid):
    handle = _FakeProcess()
    handle.pid = bad_pid
    with pytest.raises(T65BuiltinExecutorError):
        bind_owned_t65_process(
            spec=_popen_spec(tmp_path),
            config=_config(),
            handle=handle,
        )


def test_readiness_accepts_one_canonical_marker_for_owned_live_pid(tmp_path: Path):
    owned = bind_owned_t65_process(
        spec=_popen_spec(tmp_path),
        config=_config(),
        handle=_FakeProcess(pid=321),
    )
    marker = accept_owned_readiness(
        owned=owned,
        observation=T65ReadinessObservation(
            stdout_lines=(_ready_marker().canonical_line() + "\r\n",)
        ),
        config=_config(),
        prefixes=_prefixes(),
    )

    assert marker.payload.process_id == owned.pid


@pytest.mark.parametrize(
    "observation",
    [
        T65ReadinessObservation(stdout_lines=()),
        T65ReadinessObservation(
            stdout_lines=(
                _ready_marker().canonical_line(),
                _ready_marker().canonical_line(),
            )
        ),
        T65ReadinessObservation(stdout_lines=("ordinary stdout log",)),
        T65ReadinessObservation(
            stdout_lines=(_ready_marker().canonical_line(),), timed_out=True
        ),
        T65ReadinessObservation(
            stdout_lines=(_ready_marker().canonical_line(),), exit_code=1
        ),
        T65ReadinessObservation(
            stdout_lines=(json.dumps(_ready_marker().model_dump(mode="json")),)
        ),
    ],
)
def test_readiness_rejects_missing_duplicate_log_timeout_exit_and_noncanonical_marker(
    tmp_path: Path, observation
):
    owned = bind_owned_t65_process(
        spec=_popen_spec(tmp_path),
        config=_config(),
        handle=_FakeProcess(pid=321),
    )
    with pytest.raises(T65BuiltinExecutorError):
        accept_owned_readiness(
            owned=owned,
            observation=observation,
            config=_config(),
            prefixes=_prefixes(),
        )


def test_readiness_rejects_marker_pid_mismatch_and_child_exit_race(tmp_path: Path):
    live = _FakeProcess(pid=321)
    owned = bind_owned_t65_process(
        spec=_popen_spec(tmp_path), config=_config(), handle=live
    )
    with pytest.raises(T65BuiltinExecutorError, match="PID"):
        accept_owned_readiness(
            owned=owned,
            observation=T65ReadinessObservation(
                stdout_lines=(_ready_marker(pid=999).canonical_line(),)
            ),
            config=_config(),
            prefixes=_prefixes(),
        )

    live.exit_code = 1
    with pytest.raises(T65BuiltinExecutorError, match="exited"):
        accept_owned_readiness(
            owned=owned,
            observation=T65ReadinessObservation(
                stdout_lines=(_ready_marker().canonical_line(),)
            ),
            config=_config(),
            prefixes=_prefixes(),
        )


def test_api_probe_distinguishes_interview_ready_from_report_blocked():
    result = evaluate_api_probe(_ready_probe())

    assert result.api_status == "READY"
    assert result.report_status == "BLOCKED"
    assert result.report_reason == "SILICONFLOW_EMBEDDING_AUTHORIZATION_REQUIRED"
    assert result.hard_stop_conditions == ()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda probe: probe.update(health_http_status=503),
        lambda probe: probe["health_payload"].update(status="degraded"),
        lambda probe: probe.update(runtime_http_status=500),
        lambda probe: probe["runtime_payload"].update(runtime_store="memory"),
        lambda probe: probe["runtime_payload"].update(report_runtime_ready=True),
        lambda probe: probe["runtime_payload"].update(embedding_provider="siliconflow"),
        lambda probe: probe["runtime_payload"].update(configuration_warnings=[]),
    ],
)
def test_api_probe_fails_closed_on_health_runtime_and_unexpected_report_drift(mutation):
    payload = _ready_probe().model_dump(mode="python")
    mutation(payload)
    assert evaluate_api_probe(T65ApiProbeResult(**payload)).api_status == "BLOCKED"


def test_owned_termination_is_bounded_graceful_then_kill(tmp_path: Path):
    graceful_handle = _FakeProcess(wait_outcomes=[0])
    graceful = bind_owned_t65_process(
        spec=_popen_spec(tmp_path), config=_config(), handle=graceful_handle
    )
    result = terminate_owned_t65_process(
        graceful,
        config=_config(),
        graceful_timeout_seconds=2,
        kill_timeout_seconds=1,
    )
    assert result.status == "terminated_gracefully"
    assert graceful_handle.log == ["poll", "terminate", ("wait", 2)]

    kill_handle = _FakeProcess(wait_outcomes=[TimeoutError(), -9])
    killed = bind_owned_t65_process(
        spec=_popen_spec(tmp_path), config=_config(), handle=kill_handle
    )
    result = terminate_owned_t65_process(
        killed,
        config=_config(),
        graceful_timeout_seconds=2,
        kill_timeout_seconds=1,
    )
    assert result.status == "killed"
    assert kill_handle.log == [
        "poll",
        "terminate",
        ("wait", 2),
        "kill",
        ("wait", 1),
    ]


def test_owned_termination_blocks_after_bounded_kill_and_accepts_no_pid(tmp_path: Path):
    handle = _FakeProcess(wait_outcomes=[TimeoutError(), TimeoutError()])
    owned = bind_owned_t65_process(
        spec=_popen_spec(tmp_path), config=_config(), handle=handle
    )
    result = terminate_owned_t65_process(
        owned,
        config=_config(),
        graceful_timeout_seconds=1,
        kill_timeout_seconds=1,
    )
    assert result.status == "blocked"
    assert result.hard_stop_conditions == ("OWNED_PROCESS_DID_NOT_EXIT",)
    with pytest.raises((TypeError, T65BuiltinExecutorError)):
        terminate_owned_t65_process(
            321,
            config=_config(),
            graceful_timeout_seconds=1,
            kill_timeout_seconds=1,
        )


def _cleanup_hooks(log, *, failing_phase=None):
    def hook(name):
        def execute():
            log.append(name)
            if name == failing_phase:
                raise RuntimeError("secret must not be recorded")

        return execute

    return T65CleanupHooks(
        requests_stopped=hook("requests_stopped"),
        api_stopped=hook("api_stopped"),
        worker_stopped=hook("worker_stopped"),
        evidence_finalized=hook("evidence_finalized"),
        relations_dropped=hook("relations_dropped"),
        temporary_files_removed=hook("temporary_files_removed"),
    )


def _orchestration_dependencies(log, handle, *, fail_capture=False, fail_cleanup=None):
    def migrate():
        log.append("migration")

    def start(spec):
        log.append("start")
        return handle

    def observe(owned):
        log.append("observe_readiness")
        return T65ReadinessObservation(
            stdout_lines=(_ready_marker(pid=owned.pid).canonical_line(),)
        )

    def probe(owned):
        log.append("probe_api")
        return _ready_probe()

    def capture(owned):
        log.append("capture")
        if fail_capture:
            raise RuntimeError("candidate answer secret")
        return {"capture": "redacted"}

    def evidence(capture_value, marker, api_readiness):
        log.append("evidence")
        return {"evidence": "redacted"}

    return T65OrchestrationDependencies(
        migrate=migrate,
        start=start,
        observe_readiness=observe,
        probe_api=probe,
        capture=capture,
        build_evidence=evidence,
        cleanup=_cleanup_hooks(log, failing_phase=fail_cleanup),
    )


def test_top_level_orchestration_orders_all_injected_phases_and_completes(tmp_path: Path):
    log = []
    handle = _FakeProcess(log=log, wait_outcomes=[0])
    result = run_t65_owned_orchestration(
        config=_config(),
        prefixes=_prefixes(),
        popen_spec=_popen_spec(tmp_path),
        dependencies=_orchestration_dependencies(log, handle),
        graceful_timeout_seconds=2,
        kill_timeout_seconds=1,
    )

    assert result.status == "COMPLETE"
    assert result.completed_phases == (
        "migration",
        "start",
        "readiness",
        "capture",
        "stop",
        "evidence",
        "cleanup",
    )
    assert log == [
        "migration",
        "start",
        "observe_readiness",
        "poll",
        "probe_api",
        "capture",
        "poll",
        "terminate",
        ("wait", 2),
        "evidence",
        "requests_stopped",
        "api_stopped",
        "worker_stopped",
        "evidence_finalized",
        "relations_dropped",
        "temporary_files_removed",
    ]
    assert result.api_readiness.report_status == "BLOCKED"
    assert result.hard_stop_conditions == ()
    assert "redacted" not in repr(result)


def test_top_level_capture_and_cleanup_failures_still_stop_and_run_all_cleanup(
    tmp_path: Path,
):
    log = []
    handle = _FakeProcess(log=log, wait_outcomes=[0])
    result = run_t65_owned_orchestration(
        config=_config(),
        prefixes=_prefixes(),
        popen_spec=_popen_spec(tmp_path),
        dependencies=_orchestration_dependencies(
            log,
            handle,
            fail_capture=True,
            fail_cleanup="relations_dropped",
        ),
    )

    assert result.status == "BLOCKED"
    assert result.evidence is None
    assert result.hard_stop_conditions == (
        "CAPTURE_FAILED",
        "RELATION_CLEANUP_FAILED",
    )
    assert "stop" in result.completed_phases
    assert log[-6:] == [
        "requests_stopped",
        "api_stopped",
        "worker_stopped",
        "evidence_finalized",
        "relations_dropped",
        "temporary_files_removed",
    ]
    assert "secret" not in repr(result)
    assert "candidate answer" not in repr(result)


def test_top_level_migration_failure_never_starts_but_runs_cleanup_in_order(
    tmp_path: Path,
):
    log = []
    dependencies = _orchestration_dependencies(log, _FakeProcess(log=log))

    def fail_migration():
        log.append("migration")
        raise RuntimeError("postgresql://secret")

    dependencies = T65OrchestrationDependencies(
        migrate=fail_migration,
        start=dependencies.start,
        observe_readiness=dependencies.observe_readiness,
        probe_api=dependencies.probe_api,
        capture=dependencies.capture,
        build_evidence=dependencies.build_evidence,
        cleanup=dependencies.cleanup,
    )
    result = run_t65_owned_orchestration(
        config=_config(),
        prefixes=_prefixes(),
        popen_spec=_popen_spec(tmp_path),
        dependencies=dependencies,
    )

    assert result.status == "BLOCKED"
    assert result.process_pid is None
    assert result.hard_stop_conditions == ("MIGRATION_FAILED",)
    assert "start" not in log
    assert log == [
        "migration",
        "requests_stopped",
        "api_stopped",
        "worker_stopped",
        "evidence_finalized",
        "relations_dropped",
        "temporary_files_removed",
    ]
    assert "postgresql" not in repr(result)


@pytest.mark.parametrize(
    ("failure_phase", "expected_code", "process_started"),
    [
        ("start", "PROCESS_START_FAILED", False),
        ("readiness", "READINESS_FAILED", True),
        ("stop", "PROCESS_STOP_FAILED", True),
        ("evidence", "EVIDENCE_BUILD_FAILED", True),
    ],
)
def test_top_level_each_injected_stage_failure_remains_blocked_and_cleans_up(
    tmp_path: Path,
    failure_phase,
    expected_code,
    process_started,
):
    log = []
    handle = _FakeProcess(log=log, wait_outcomes=[0])
    base = _orchestration_dependencies(log, handle)

    def fail(*args):
        log.append(f"{failure_phase}_failed")
        raise RuntimeError("secret failure detail")

    if failure_phase == "start":
        start = fail
        observe = base.observe_readiness
        evidence = base.build_evidence
    elif failure_phase == "readiness":
        start = base.start
        observe = fail
        evidence = base.build_evidence
    elif failure_phase == "evidence":
        start = base.start
        observe = base.observe_readiness
        evidence = fail
    else:
        start = base.start
        observe = base.observe_readiness
        evidence = base.build_evidence
        handle.terminate = fail

    dependencies = T65OrchestrationDependencies(
        migrate=base.migrate,
        start=start,
        observe_readiness=observe,
        probe_api=base.probe_api,
        capture=base.capture,
        build_evidence=evidence,
        cleanup=base.cleanup,
    )
    result = run_t65_owned_orchestration(
        config=_config(),
        prefixes=_prefixes(),
        popen_spec=_popen_spec(tmp_path),
        dependencies=dependencies,
    )

    assert result.status == "BLOCKED"
    assert expected_code in result.hard_stop_conditions
    assert (result.process_pid is not None) is process_started
    assert log[-6:] == [
        "requests_stopped",
        "api_stopped",
        "worker_stopped",
        "evidence_finalized",
        "relations_dropped",
        "temporary_files_removed",
    ]
    assert "secret failure detail" not in repr(result)
