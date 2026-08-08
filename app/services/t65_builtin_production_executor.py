from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
from types import MappingProxyType
from typing import Callable, Literal, Mapping, Protocol, Sequence
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.config import derive_pgvector_table_names
from app.services.postgres_identifiers import validate_runtime_table_prefix


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+-]{1,128}$")
_SAFE_PREFIX_RE = re.compile(r"^test_t65perf_[0-9a-f]{12}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

_PROVIDER_NAME = "DeepSeek"
_MODEL_ID = "deepseek-v4-pro"
_BASE_URL = "https://api.deepseek.com"
_OWNED_PROCESS_SEAL = object()

_OS_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
    }
)


class T65BuiltinExecutorError(ValueError):
    """A frozen offline executor invariant was violated."""


class T65BuiltinExecutorConfig(BaseModel):
    """Hashable, secret-free configuration for the future live executor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["t65-builtin-production-executor-config-v1"] = (
        "t65-builtin-production-executor-config-v1"
    )
    run_id: str
    candidate_revision: str
    candidate_tree: str
    authorization_id: str = Field(min_length=1, max_length=200)
    authorization_sha256: str
    executor_sha256: str
    provider_name: Literal["DeepSeek"] = "DeepSeek"
    model_id: Literal["deepseek-v4-pro"] = "deepseek-v4-pro"
    base_url: Literal["https://api.deepseek.com"] = "https://api.deepseek.com"
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    context_window_tokens: Literal[128_000] = 128_000
    report_enabled: Literal[False] = False

    @field_validator("authorization_id")
    @classmethod
    def validate_authorization_id(cls, value: str) -> str:
        if _CONTROL_RE.search(value):
            raise ValueError("authorization_id contains a control character")
        return value

    @model_validator(mode="after")
    def validate_identity(self):
        if _SAFE_RUN_ID_RE.fullmatch(self.run_id) is None:
            raise ValueError("run_id must be a bounded safe identifier")
        for label, value in (
            ("candidate_revision", self.candidate_revision),
            ("candidate_tree", self.candidate_tree),
        ):
            if _GIT_OBJECT_RE.fullmatch(value) is None:
                raise ValueError(f"{label} must be a git object id")
        for label, value in (
            ("authorization_sha256", self.authorization_sha256),
            ("executor_sha256", self.executor_sha256),
        ):
            if _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"{label} must be a SHA-256 digest")
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.deepseek.com"
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("only the authorized DeepSeek origin is allowed")
        return self

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def config_sha256(self) -> str:
        return _canonical_sha256(self.canonical_payload())


@dataclass(frozen=True)
class T65PrefixPair:
    runtime_prefix: str
    vector_prefix: str
    vector_versions_table: str
    vector_releases_table: str


def generate_t65_prefix_pair(
    *,
    token_factory: Callable[[int], str] = secrets.token_hex,
) -> T65PrefixPair:
    """Generate two isolated prefixes; no external state is touched."""

    runtime = f"test_t65perf_{token_factory(6)}"
    vector = f"test_t65perf_{token_factory(6)}"
    return validate_t65_prefix_pair(runtime, vector)


def validate_t65_prefix_pair(
    runtime_prefix: str,
    vector_prefix: str,
) -> T65PrefixPair:
    for label, value in (
        ("runtime", runtime_prefix),
        ("vector", vector_prefix),
    ):
        if not isinstance(value, str) or _SAFE_PREFIX_RE.fullmatch(value) is None:
            raise T65BuiltinExecutorError(
                f"{label} prefix must be test_t65perf_<12 lowercase hex>"
            )
    if runtime_prefix == vector_prefix:
        raise T65BuiltinExecutorError("runtime and vector prefixes must be distinct")
    try:
        validate_runtime_table_prefix(runtime_prefix)
        versions, releases = derive_pgvector_table_names(vector_prefix)
    except ValueError as exc:
        raise T65BuiltinExecutorError("PostgreSQL prefix is unsafe") from exc
    return T65PrefixPair(
        runtime_prefix=runtime_prefix,
        vector_prefix=vector_prefix,
        vector_versions_table=versions,
        vector_releases_table=releases,
    )


@dataclass(frozen=True, repr=False, eq=False)
class T65ProcessEnvironment:
    process_role: Literal["api"]
    _values: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(self._values)))

    def as_dict(self) -> dict[str, str]:
        return dict(self._values)

    def __repr__(self) -> str:
        return (
            "T65ProcessEnvironment(process_role='api', "
            f"variable_names={tuple(sorted(self._values))!r})"
        )


def build_t65_process_environment(
    *,
    config: T65BuiltinExecutorConfig,
    prefixes: T65PrefixPair,
    process_role: Literal["api", "report_worker"],
    base_environment: Mapping[str, str],
    postgres_dsn: str,
    deepseek_api_key: str,
    worker_id: str | None = None,
) -> T65ProcessEnvironment:
    """Build a new allowlisted environment without inheriting Provider state."""

    if process_role != "api":
        raise T65BuiltinExecutorError(
            "report worker execution is not authorized for interview-only capture"
        )
    if worker_id is not None:
        raise T65BuiltinExecutorError("worker_id is unavailable in interview-only capture")
    if not postgres_dsn.strip():
        raise T65BuiltinExecutorError("POSTGRES_DSN is required")
    if not deepseek_api_key.strip():
        raise T65BuiltinExecutorError("DeepSeek credential is required")

    _require_safe_environment_value("POSTGRES_DSN", postgres_dsn)
    _require_safe_environment_value("OPENAI_API_KEY", deepseek_api_key)

    values: dict[str, str] = {}
    for key, value in base_environment.items():
        rendered = str(value)
        if key in _OS_ENV_ALLOWLIST and rendered:
            _require_safe_environment_value(key, rendered)
            values[key] = rendered
    values.update(
        {
            "PYTHONUTF8": "1",
            "POSTGRES_DSN": postgres_dsn,
            "INTERVIEW_RUNTIME_STORE": "postgres",
            "INTERVIEW_RUNTIME_TABLE_PREFIX": prefixes.runtime_prefix,
            "POSTGRES_RUNTIME_AUTO_MIGRATE": "false",
            "INTERVIEW_EVENT_BACKEND": "local",
            "OPENAI_API_KEY": deepseek_api_key,
            "OPENAI_BASE_URL": config.base_url,
            "OPENAI_MODEL": config.model_id,
            "OPENAI_TEMPERATURE": "0.2",
            "OPENAI_REQUEST_TIMEOUT_SECONDS": _format_float(
                config.request_timeout_seconds
            ),
            "OPENAI_MAX_RETRIES": "0",
            "OPENAI_REPORT_OUTPUT_MODE": "raw_only",
            "MEMORY_MODEL_CONTEXT_WINDOW_TOKENS": str(
                config.context_window_tokens
            ),
            "MEMORY_BUDGET_MODE": "disabled",
            "MEMORY_COMPRESSION_MODE": "disabled",
            "MEMORY_LONG_TERM_MODE": "disabled",
            # No REPORT_*, KNOWLEDGE_STORE, PGVECTOR_TABLE or embedding setting is
            # declared. PostgreSQL interview capture is not a preview report, and
            # DeepSeek-only authorization cannot claim the durable report profile.
            "T65_REPORT_ENABLED": "false",
            "T65_EXECUTION_SCOPE": "interview_only",
            "T65_RUN_ID": config.run_id,
            "T65_AUTHORIZATION_ID": config.authorization_id,
            "T65_AUTHORIZATION_SHA256": config.authorization_sha256,
            "T65_EXECUTOR_SHA256": config.executor_sha256,
            "T65_EXECUTOR_CONFIG_SHA256": config.config_sha256,
        }
    )
    forbidden = _forbidden_environment_names(values)
    if forbidden:
        raise T65BuiltinExecutorError(
            "constructed environment contains forbidden credentials or proxy state"
        )
    return T65ProcessEnvironment(process_role="api", _values=values)


def _require_safe_environment_value(name: str, value: str) -> None:
    if _CONTROL_RE.search(value):
        raise T65BuiltinExecutorError(
            f"environment value contains a control character: {name}"
        )


def _forbidden_environment_names(environment: Mapping[str, str]) -> tuple[str, ...]:
    forbidden: list[str] = []
    for name in environment:
        upper = name.upper()
        if upper in {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "SILICONFLOW_API_KEY",
            "DEEPSEEK_API_KEY",
            "INTERVIEW_TABLE_PREFIX",
        }:
            forbidden.append(name)
        if (
            (upper.endswith("_API_KEY") or upper.endswith("_TOKEN"))
            and upper != "OPENAI_API_KEY"
        ):
            forbidden.append(name)
    return tuple(sorted(set(forbidden)))


@dataclass(frozen=True)
class T65CommandSpec:
    process_role: Literal["api", "report_worker"]
    argv: tuple[str, ...]
    cwd: Path


@dataclass(frozen=True)
class T65LaunchOptions:
    platform: Literal["windows", "posix"]
    creationflags: int
    startupinfo_flags: int
    show_window: int | None
    start_new_session: bool


@dataclass(frozen=True, repr=False, eq=False)
class T65PopenSpec:
    command: T65CommandSpec
    environment: T65ProcessEnvironment
    launch: T65LaunchOptions
    stdin_mode: Literal["DEVNULL"]
    stdout_mode: Literal["PIPE"]
    stderr_mode: Literal["PIPE"]
    text: Literal[True]
    encoding: Literal["utf-8"]
    config_sha256: str
    spec_sha256: str

    def __repr__(self) -> str:
        return (
            "T65PopenSpec(process_role='api', "
            f"platform={self.launch.platform!r}, spec_sha256={self.spec_sha256!r})"
        )

    def environment_copy(self) -> dict[str, str]:
        return self.environment.as_dict()


def build_t65_popen_spec(
    *,
    command: T65CommandSpec,
    environment: T65ProcessEnvironment,
    config: T65BuiltinExecutorConfig,
    platform: Literal["windows", "posix"],
) -> T65PopenSpec:
    _validate_api_command(command)
    if environment.process_role != command.process_role:
        raise T65BuiltinExecutorError("command and environment roles do not match")
    values = environment.as_dict()
    _validate_built_api_environment(values, config=config)
    if values.get("T65_EXECUTOR_CONFIG_SHA256") != config.config_sha256:
        raise T65BuiltinExecutorError("process environment config identity drifted")
    if values.get("T65_RUN_ID") != config.run_id:
        raise T65BuiltinExecutorError("process environment run identity drifted")
    if platform == "windows":
        launch = T65LaunchOptions(
            platform="windows",
            creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
            startupinfo_flags=0x00000001,  # STARTF_USESHOWWINDOW
            show_window=0,  # SW_HIDE
            start_new_session=False,
        )
    elif platform == "posix":
        launch = T65LaunchOptions(
            platform="posix",
            creationflags=0,
            startupinfo_flags=0,
            show_window=None,
            start_new_session=True,
        )
    else:  # pragma: no cover - typing boundary
        raise T65BuiltinExecutorError("unsupported process platform")
    payload: dict[str, object] = {
        "schema_version": "t65-popen-spec-v1",
        "process_role": command.process_role,
        "argv": list(command.argv),
        "cwd": str(command.cwd),
        "launch": {
            "platform": launch.platform,
            "creationflags": launch.creationflags,
            "startupinfo_flags": launch.startupinfo_flags,
            "show_window": launch.show_window,
            "start_new_session": launch.start_new_session,
        },
        "stdin": "DEVNULL",
        "stdout": "PIPE",
        "stderr": "PIPE",
        "text": True,
        "encoding": "utf-8",
        "config_sha256": config.config_sha256,
    }
    return T65PopenSpec(
        command=command,
        environment=environment,
        launch=launch,
        stdin_mode="DEVNULL",
        stdout_mode="PIPE",
        stderr_mode="PIPE",
        text=True,
        encoding="utf-8",
        config_sha256=config.config_sha256,
        spec_sha256=_canonical_sha256(payload),
    )


def _validate_built_api_environment(
    values: Mapping[str, str],
    *,
    config: T65BuiltinExecutorConfig,
) -> None:
    required = {
        "PYTHONUTF8": "1",
        "INTERVIEW_RUNTIME_STORE": "postgres",
        "POSTGRES_RUNTIME_AUTO_MIGRATE": "false",
        "INTERVIEW_EVENT_BACKEND": "local",
        "OPENAI_BASE_URL": config.base_url,
        "OPENAI_MODEL": config.model_id,
        "OPENAI_TEMPERATURE": "0.2",
        "OPENAI_REQUEST_TIMEOUT_SECONDS": _format_float(
            config.request_timeout_seconds
        ),
        "OPENAI_MAX_RETRIES": "0",
        "OPENAI_REPORT_OUTPUT_MODE": "raw_only",
        "MEMORY_MODEL_CONTEXT_WINDOW_TOKENS": str(config.context_window_tokens),
        "MEMORY_BUDGET_MODE": "disabled",
        "MEMORY_COMPRESSION_MODE": "disabled",
        "MEMORY_LONG_TERM_MODE": "disabled",
        "T65_REPORT_ENABLED": "false",
        "T65_EXECUTION_SCOPE": "interview_only",
        "T65_RUN_ID": config.run_id,
        "T65_AUTHORIZATION_ID": config.authorization_id,
        "T65_AUTHORIZATION_SHA256": config.authorization_sha256,
        "T65_EXECUTOR_SHA256": config.executor_sha256,
        "T65_EXECUTOR_CONFIG_SHA256": config.config_sha256,
    }
    if any(values.get(key) != value for key, value in required.items()):
        raise T65BuiltinExecutorError("API environment fixed identity drifted")
    allowed = set(required) | set(_OS_ENV_ALLOWLIST) | {
        "POSTGRES_DSN",
        "INTERVIEW_RUNTIME_TABLE_PREFIX",
        "OPENAI_API_KEY",
    }
    if set(values) - allowed:
        raise T65BuiltinExecutorError("API environment contains non-allowlisted state")
    for secret_name in ("POSTGRES_DSN", "OPENAI_API_KEY"):
        secret = values.get(secret_name)
        if not isinstance(secret, str) or not secret.strip():
            raise T65BuiltinExecutorError("API environment credential is unavailable")
        _require_safe_environment_value(secret_name, secret)
    prefix = values.get("INTERVIEW_RUNTIME_TABLE_PREFIX", "")
    if _SAFE_PREFIX_RE.fullmatch(prefix) is None:
        raise T65BuiltinExecutorError("API runtime prefix is not isolated")
    validate_runtime_table_prefix(prefix)
    if _forbidden_environment_names(values):
        raise T65BuiltinExecutorError("API environment contains forbidden state")


def validate_t65_popen_spec(
    spec: T65PopenSpec,
    *,
    config: T65BuiltinExecutorConfig,
) -> T65PopenSpec:
    if not isinstance(spec, T65PopenSpec):
        raise T65BuiltinExecutorError("owned process requires a T65 Popen spec")
    rebuilt = build_t65_popen_spec(
        command=spec.command,
        environment=spec.environment,
        config=config,
        platform=spec.launch.platform,
    )
    if (
        spec.spec_sha256 != rebuilt.spec_sha256
        or spec.config_sha256 != rebuilt.config_sha256
        or spec.launch != rebuilt.launch
        or spec.stdin_mode != rebuilt.stdin_mode
        or spec.stdout_mode != rebuilt.stdout_mode
        or spec.stderr_mode != rebuilt.stderr_mode
        or spec.text is not True
        or spec.encoding != "utf-8"
    ):
        raise T65BuiltinExecutorError("Popen spec identity or launch options drifted")
    return spec


def _validate_api_command(command: T65CommandSpec) -> None:
    if not isinstance(command, T65CommandSpec) or command.process_role != "api":
        raise T65BuiltinExecutorError("only the owned API command is allowed")
    argv = command.argv
    if (
        len(argv) != 9
        or argv[1:4] != ("-m", "uvicorn", "app.main:app")
        or argv[4:6] != ("--host", "127.0.0.1")
        or argv[6] != "--port"
        or argv[8:] != ("--no-proxy-headers",)
    ):
        raise T65BuiltinExecutorError("Uvicorn command is not the fixed loopback spec")
    try:
        port = int(argv[7])
    except (TypeError, ValueError):
        raise T65BuiltinExecutorError("Uvicorn command port is invalid") from None
    expected = build_uvicorn_command_spec(
        python_executable=Path(argv[0]),
        repository_root=command.cwd,
        port=port,
    )
    if command != expected:
        raise T65BuiltinExecutorError("Uvicorn command identity drifted")


def build_uvicorn_command_spec(
    *,
    python_executable: Path,
    repository_root: Path,
    port: int,
) -> T65CommandSpec:
    python, root = _validated_command_roots(python_executable, repository_root)
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise T65BuiltinExecutorError("Uvicorn port must be between 1024 and 65535")
    return T65CommandSpec(
        process_role="api",
        argv=(
            str(python),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-proxy-headers",
        ),
        cwd=root,
    )


def build_report_worker_command_spec(
    *,
    config: T65BuiltinExecutorConfig,
    python_executable: Path,
    repository_root: Path,
) -> T65CommandSpec:
    del python_executable, repository_root
    if config.report_enabled is False:
        raise T65BuiltinExecutorError(
            "report worker command is blocked without embedding authorization"
        )
    raise T65BuiltinExecutorError(
        "report worker command requires a separately authorized configuration"
    )


def _validated_command_roots(
    python_executable: Path,
    repository_root: Path,
) -> tuple[Path, Path]:
    python = Path(python_executable)
    root = Path(repository_root)
    if not python.is_absolute() or not root.is_absolute():
        raise T65BuiltinExecutorError("command paths must be absolute")
    if not python.name or not root.name:
        raise T65BuiltinExecutorError("command paths must identify bounded targets")
    return python, root


class T65ReadinessPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["t65-process-readiness-v1"] = "t65-process-readiness-v1"
    run_id: str
    process_role: Literal["api", "report_worker"]
    process_id: int = Field(gt=0)
    candidate_revision: str
    candidate_tree: str
    authorization_sha256: str
    executor_sha256: str
    executor_config_sha256: str
    runtime_prefix_sha256: str
    vector_prefix_sha256: str
    provider_name: Literal["DeepSeek"] = "DeepSeek"
    model_id: Literal["deepseek-v4-pro"] = "deepseek-v4-pro"
    report_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_hashes(self):
        if _SAFE_RUN_ID_RE.fullmatch(self.run_id) is None:
            raise ValueError("invalid readiness run identity")
        for value in (self.candidate_revision, self.candidate_tree):
            if _GIT_OBJECT_RE.fullmatch(value) is None:
                raise ValueError("invalid readiness candidate identity")
        for value in (
            self.authorization_sha256,
            self.executor_sha256,
            self.executor_config_sha256,
            self.runtime_prefix_sha256,
            self.vector_prefix_sha256,
        ):
            if _SHA256_RE.fullmatch(value) is None:
                raise ValueError("invalid readiness digest")
        return self


class T65ReadinessMarker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: T65ReadinessPayload
    payload_sha256: str

    @model_validator(mode="after")
    def validate_digest(self):
        if self.payload_sha256 != _canonical_sha256(
            self.payload.model_dump(mode="json")
        ):
            raise ValueError("readiness payload hash mismatch")
        return self

    def canonical_line(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def build_t65_readiness_marker(
    *,
    config: T65BuiltinExecutorConfig,
    prefixes: T65PrefixPair,
    process_role: Literal["api", "report_worker"],
    process_id: int,
) -> T65ReadinessMarker:
    if process_role != "api":
        raise T65BuiltinExecutorError(
            "report worker readiness is blocked without embedding authorization"
        )
    payload = T65ReadinessPayload(
        run_id=config.run_id,
        process_role=process_role,
        process_id=process_id,
        candidate_revision=config.candidate_revision,
        candidate_tree=config.candidate_tree,
        authorization_sha256=config.authorization_sha256,
        executor_sha256=config.executor_sha256,
        executor_config_sha256=config.config_sha256,
        runtime_prefix_sha256=_text_sha256(prefixes.runtime_prefix),
        vector_prefix_sha256=_text_sha256(prefixes.vector_prefix),
        provider_name=config.provider_name,
        model_id=config.model_id,
        report_enabled=False,
    )
    return T65ReadinessMarker(
        payload=payload,
        payload_sha256=_canonical_sha256(payload.model_dump(mode="json")),
    )


def verify_t65_readiness_marker(
    marker: T65ReadinessMarker | Mapping[str, object] | str,
    *,
    config: T65BuiltinExecutorConfig,
    prefixes: T65PrefixPair,
    expected_role: Literal["api", "report_worker"],
) -> T65ReadinessMarker:
    try:
        if isinstance(marker, str):
            parsed = T65ReadinessMarker.model_validate_json(marker)
        elif isinstance(marker, T65ReadinessMarker):
            parsed = T65ReadinessMarker.model_validate(marker.model_dump(mode="json"))
        else:
            parsed = T65ReadinessMarker.model_validate(marker)
    except (ValueError, TypeError) as exc:
        raise T65BuiltinExecutorError("readiness marker is invalid") from exc
    expected = build_t65_readiness_marker(
        config=config,
        prefixes=prefixes,
        process_role=expected_role,
        process_id=parsed.payload.process_id,
    )
    if parsed != expected:
        raise T65BuiltinExecutorError("readiness identity does not match executor")
    return parsed


class T65ProcessHandle(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float) -> int: ...


@dataclass(frozen=True, repr=False, eq=False)
class T65OwnedProcess:
    process_role: Literal["api"]
    pid: int
    config_sha256: str
    spec_sha256: str
    _handle: T65ProcessHandle
    _ownership_seal: object

    def __repr__(self) -> str:
        return (
            "T65OwnedProcess(process_role='api', "
            f"pid={self.pid}, config_sha256={self.config_sha256!r}, "
            f"spec_sha256={self.spec_sha256!r})"
        )


def start_owned_t65_process(
    *,
    spec: T65PopenSpec,
    config: T65BuiltinExecutorConfig,
    starter: Callable[[T65PopenSpec], T65ProcessHandle],
) -> T65OwnedProcess:
    validate_t65_popen_spec(spec, config=config)
    handle = starter(spec)
    return bind_owned_t65_process(spec=spec, config=config, handle=handle)


def bind_owned_t65_process(
    *,
    spec: T65PopenSpec,
    config: T65BuiltinExecutorConfig,
    handle: T65ProcessHandle,
) -> T65OwnedProcess:
    validate_t65_popen_spec(spec, config=config)
    pid = getattr(handle, "pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise T65BuiltinExecutorError("owned child PID is invalid")
    for method in ("poll", "terminate", "kill", "wait"):
        if not callable(getattr(handle, method, None)):
            raise T65BuiltinExecutorError("owned child handle is incomplete")
    return T65OwnedProcess(
        process_role="api",
        pid=pid,
        config_sha256=config.config_sha256,
        spec_sha256=spec.spec_sha256,
        _handle=handle,
        _ownership_seal=_OWNED_PROCESS_SEAL,
    )


@dataclass(frozen=True)
class T65ReadinessObservation:
    stdout_lines: tuple[str, ...]
    timed_out: bool = False
    exit_code: int | None = None


def accept_owned_readiness(
    *,
    owned: T65OwnedProcess,
    observation: T65ReadinessObservation,
    config: T65BuiltinExecutorConfig,
    prefixes: T65PrefixPair,
) -> T65ReadinessMarker:
    _validate_owned_identity(owned, config=config)
    if observation.timed_out:
        raise T65BuiltinExecutorError("owned API readiness timed out")
    if observation.exit_code is not None:
        raise T65BuiltinExecutorError("owned API exited before readiness completed")
    if len(observation.stdout_lines) != 1:
        raise T65BuiltinExecutorError(
            "owned API stdout must contain exactly one readiness marker"
        )
    raw = observation.stdout_lines[0]
    if not isinstance(raw, str):
        raise T65BuiltinExecutorError("readiness stdout must be UTF-8 text")
    line = raw.removesuffix("\n").removesuffix("\r")
    if not line or _CONTROL_RE.search(line):
        raise T65BuiltinExecutorError("readiness stdout line is not canonical text")
    marker = verify_t65_readiness_marker(
        line,
        config=config,
        prefixes=prefixes,
        expected_role=owned.process_role,
    )
    if line != marker.canonical_line():
        raise T65BuiltinExecutorError("readiness marker is not canonical JSON")
    if marker.payload.process_id != owned.pid:
        raise T65BuiltinExecutorError("readiness PID does not match owned child")
    if owned._handle.poll() is not None:
        raise T65BuiltinExecutorError("owned API exited while readiness was accepted")
    return marker


def _validate_owned_identity(
    owned: T65OwnedProcess,
    *,
    config: T65BuiltinExecutorConfig,
) -> None:
    if not isinstance(owned, T65OwnedProcess):
        raise T65BuiltinExecutorError("operation requires an owned child handle")
    if owned._ownership_seal is not _OWNED_PROCESS_SEAL:
        raise T65BuiltinExecutorError("process handle was not created by the owner")
    if owned.process_role != "api" or owned.config_sha256 != config.config_sha256:
        raise T65BuiltinExecutorError("owned child identity drifted")
    if getattr(owned._handle, "pid", None) != owned.pid:
        raise T65BuiltinExecutorError("owned child handle PID drifted")


class T65ApiProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    health_http_status: int = Field(ge=100, le=599)
    health_payload: dict[str, object]
    runtime_http_status: int = Field(ge=100, le=599)
    runtime_payload: dict[str, object]


class T65ApiReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_status: Literal["READY", "BLOCKED"]
    report_status: Literal["BLOCKED"] = "BLOCKED"
    report_reason: Literal["SILICONFLOW_EMBEDDING_AUTHORIZATION_REQUIRED"] = (
        "SILICONFLOW_EMBEDDING_AUTHORIZATION_REQUIRED"
    )
    hard_stop_conditions: tuple[str, ...] = ()


def evaluate_api_probe(result: T65ApiProbeResult) -> T65ApiReadiness:
    stops: list[str] = []
    if result.health_http_status != 200 or result.health_payload != {"status": "ok"}:
        stops.append("API_HEALTH_NOT_READY")
    runtime = result.runtime_payload
    if result.runtime_http_status != 200:
        stops.append("API_RUNTIME_NOT_READY")
    else:
        expected = {
            "runtime_store": "postgres",
            "session_store": "PostgresInterviewSessionStore",
            "event_backend": "local",
        }
        if any(runtime.get(key) != value for key, value in expected.items()):
            stops.append("API_RUNTIME_IDENTITY_MISMATCH")
        if (
            runtime.get("report_runtime_ready") is not False
            or runtime.get("knowledge_runtime_ready") is not False
            or runtime.get("embedding_provider") != "disabled"
        ):
            stops.append("UNEXPECTED_REPORT_CAPABILITY")
        warnings = runtime.get("configuration_warnings")
        if not isinstance(warnings, list) or "pgvector_requires_embedding_provider" not in warnings:
            stops.append("REPORT_BLOCKER_NOT_DECLARED")
    return T65ApiReadiness(
        api_status="BLOCKED" if stops else "READY",
        hard_stop_conditions=tuple(dict.fromkeys(stops)),
    )


TerminationStatus = Literal[
    "already_exited",
    "terminated_gracefully",
    "killed",
    "blocked",
]


@dataclass(frozen=True)
class T65TerminationResult:
    status: TerminationStatus
    pid: int
    actions: tuple[str, ...]
    exit_code: int | None
    hard_stop_conditions: tuple[str, ...] = ()


def terminate_owned_t65_process(
    owned: T65OwnedProcess,
    *,
    config: T65BuiltinExecutorConfig,
    graceful_timeout_seconds: float,
    kill_timeout_seconds: float,
) -> T65TerminationResult:
    _validate_owned_identity(owned, config=config)
    if graceful_timeout_seconds <= 0 or kill_timeout_seconds <= 0:
        raise T65BuiltinExecutorError("owned process stop bounds must be positive")
    handle = owned._handle
    existing = handle.poll()
    if existing is not None:
        return T65TerminationResult(
            status="already_exited",
            pid=owned.pid,
            actions=("poll",),
            exit_code=existing,
        )
    actions = ["poll", "terminate", "wait_graceful"]
    handle.terminate()
    try:
        exit_code = handle.wait(timeout=graceful_timeout_seconds)
        return T65TerminationResult(
            status="terminated_gracefully",
            pid=owned.pid,
            actions=tuple(actions),
            exit_code=exit_code,
        )
    except TimeoutError:
        actions.extend(("kill", "wait_kill"))
        handle.kill()
        try:
            exit_code = handle.wait(timeout=kill_timeout_seconds)
        except TimeoutError:
            return T65TerminationResult(
                status="blocked",
                pid=owned.pid,
                actions=tuple(actions),
                exit_code=handle.poll(),
                hard_stop_conditions=("OWNED_PROCESS_DID_NOT_EXIT",),
            )
        return T65TerminationResult(
            status="killed",
            pid=owned.pid,
            actions=tuple(actions),
            exit_code=exit_code,
        )


@dataclass(frozen=True)
class T65LoopbackRequestSpec:
    method: Literal["GET"]
    url: str
    headers: tuple[tuple[str, str], ...]


def build_sse_resume_request(
    *,
    port: int,
    session_id: str,
    command_id: str,
    last_event_id: str,
) -> T65LoopbackRequestSpec:
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise T65BuiltinExecutorError("loopback port is invalid")
    for label, value in (
        ("session_id", session_id),
        ("command_id", command_id),
        ("last_event_id", last_event_id),
    ):
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 512
            or _CONTROL_RE.search(value)
        ):
            raise T65BuiltinExecutorError(f"{label} is not a safe cursor value")
    path = (
        "/api/interviews/"
        + quote(session_id, safe="")
        + "/commands/"
        + quote(command_id, safe="")
        + "/stream"
    )
    return T65LoopbackRequestSpec(
        method="GET",
        url=f"http://127.0.0.1:{port}{path}",
        headers=(("Accept", "text/event-stream"), ("Last-Event-ID", last_event_id)),
    )


_USAGE_FIELDS = (
    "provider_attempt_count",
    "provider_metered_attempt_count",
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_cached_input_tokens",
    "decision_output_tokens",
    "followup_output_tokens",
)


class T65UsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_attempt_count: int | None = Field(default=None, ge=0)
    provider_metered_attempt_count: int | None = Field(default=None, ge=0)
    provider_input_tokens: int | None = Field(default=None, ge=0)
    provider_output_tokens: int | None = Field(default=None, ge=0)
    provider_cached_input_tokens: int | None = Field(default=None, ge=0)
    decision_output_tokens: int | None = Field(default=None, ge=0)
    followup_output_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_usage(self):
        if (
            self.provider_cached_input_tokens is not None
            and self.provider_input_tokens is not None
            and self.provider_cached_input_tokens > self.provider_input_tokens
        ):
            raise ValueError("cached input tokens cannot exceed input tokens")
        if (
            self.provider_metered_attempt_count is not None
            and self.provider_attempt_count is not None
            and self.provider_metered_attempt_count > self.provider_attempt_count
        ):
            raise ValueError("metered attempts cannot exceed attempts")
        return self


def synthesize_t65_usage(
    *components: Mapping[str, int | None],
    recovery: bool = False,
) -> T65UsageSummary:
    if not components:
        components = ({},)
    totals: dict[str, int | None] = {}
    for field in _USAGE_FIELDS:
        observed: list[int] = []
        complete = True
        for component in components:
            value = component.get(field)
            if value is None:
                complete = False
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise T65BuiltinExecutorError(f"invalid usage field: {field}")
            observed.append(value)
        totals[field] = sum(observed) if complete else None
    try:
        summary = T65UsageSummary.model_validate(totals)
    except ValueError as exc:
        raise T65BuiltinExecutorError("Provider usage is internally inconsistent") from exc
    if recovery and any(getattr(summary, field) != 0 for field in _USAGE_FIELDS):
        raise T65BuiltinExecutorError(
            "SSE recovery must explicitly prove zero Provider usage"
        )
    return summary


class T65ReportSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["report_complete"] = "report_complete"
    status: Literal["BLOCKED"] = "BLOCKED"
    seconds: None = None
    reason: Literal["SILICONFLOW_EMBEDDING_AUTHORIZATION_REQUIRED"] = (
        "SILICONFLOW_EMBEDDING_AUTHORIZATION_REQUIRED"
    )


def report_signal_for_config(config: T65BuiltinExecutorConfig) -> T65ReportSignal:
    if config.report_enabled is not False:  # pragma: no cover - Pydantic invariant
        raise T65BuiltinExecutorError("report execution is not authorized")
    return T65ReportSignal()


CleanupPhase = Literal[
    "initialized",
    "requests_stopped",
    "api_stopped",
    "worker_stopped",
    "evidence_finalized",
    "relations_dropped",
    "temporary_files_removed",
    "complete",
]

_CLEANUP_ORDER: tuple[CleanupPhase, ...] = (
    "initialized",
    "requests_stopped",
    "api_stopped",
    "worker_stopped",
    "evidence_finalized",
    "relations_dropped",
    "temporary_files_removed",
    "complete",
)


@dataclass(frozen=True)
class T65CleanupError:
    phase: CleanupPhase
    code: str
    error_type: str


@dataclass(frozen=True)
class T65CleanupState:
    phase: CleanupPhase = "initialized"
    errors: tuple[T65CleanupError, ...] = ()

    def advance(self, next_phase: CleanupPhase) -> T65CleanupState:
        try:
            current_index = _CLEANUP_ORDER.index(self.phase)
            next_index = _CLEANUP_ORDER.index(next_phase)
        except ValueError as exc:  # pragma: no cover - typing boundary
            raise T65BuiltinExecutorError("unknown cleanup phase") from exc
        if next_index != current_index + 1:
            raise T65BuiltinExecutorError("cleanup phases must advance exactly once")
        return replace(self, phase=next_phase)

    def record_error(
        self,
        *,
        code: str,
        error: BaseException,
    ) -> T65CleanupState:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code):
            raise T65BuiltinExecutorError("cleanup error code is invalid")
        item = T65CleanupError(
            phase=self.phase,
            code=code,
            # Do not persist exception messages; they can contain DSNs or keys.
            error_type=type(error).__name__,
        )
        return replace(self, errors=(*self.errors, item))

    @property
    def cleanup_complete(self) -> bool:
        return self.phase == "complete" and not self.errors

    @property
    def hard_stop_conditions(self) -> tuple[str, ...]:
        if not self.errors:
            return ()
        return tuple(dict.fromkeys(item.code for item in self.errors))


@dataclass(frozen=True)
class T65CleanupHooks:
    requests_stopped: Callable[[], None]
    api_stopped: Callable[[], None]
    worker_stopped: Callable[[], None]
    evidence_finalized: Callable[[], None]
    relations_dropped: Callable[[], None]
    temporary_files_removed: Callable[[], None]


@dataclass(frozen=True)
class T65OrchestrationDependencies:
    migrate: Callable[[], object]
    start: Callable[[T65PopenSpec], T65ProcessHandle]
    observe_readiness: Callable[[T65OwnedProcess], T65ReadinessObservation]
    probe_api: Callable[[T65OwnedProcess], T65ApiProbeResult]
    capture: Callable[[T65OwnedProcess], object]
    build_evidence: Callable[
        [object, T65ReadinessMarker, T65ApiReadiness], object
    ]
    cleanup: T65CleanupHooks


@dataclass(frozen=True)
class T65OrchestrationError:
    phase: str
    code: str
    error_type: str


@dataclass(frozen=True, repr=False)
class T65OrchestrationResult:
    status: Literal["COMPLETE", "BLOCKED"]
    completed_phases: tuple[str, ...]
    process_pid: int | None
    readiness: T65ReadinessMarker | None
    api_readiness: T65ApiReadiness | None
    termination: T65TerminationResult | None
    evidence: object | None
    cleanup: T65CleanupState
    errors: tuple[T65OrchestrationError, ...]
    hard_stop_conditions: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            f"T65OrchestrationResult(status={self.status!r}, "
            f"completed_phases={self.completed_phases!r}, "
            f"process_pid={self.process_pid!r}, "
            f"hard_stop_conditions={self.hard_stop_conditions!r})"
        )


def run_t65_owned_orchestration(
    *,
    config: T65BuiltinExecutorConfig,
    prefixes: T65PrefixPair,
    popen_spec: T65PopenSpec,
    dependencies: T65OrchestrationDependencies,
    graceful_timeout_seconds: float = 10.0,
    kill_timeout_seconds: float = 5.0,
) -> T65OrchestrationResult:
    """Run injected orchestration without implementing any external adapter."""

    validate_t65_popen_spec(popen_spec, config=config)
    completed: list[str] = []
    errors: list[T65OrchestrationError] = []
    owned: T65OwnedProcess | None = None
    marker: T65ReadinessMarker | None = None
    api_readiness: T65ApiReadiness | None = None
    capture: object | None = None
    evidence: object | None = None
    termination: T65TerminationResult | None = None

    try:
        dependencies.migrate()
        completed.append("migration")
    except BaseException as exc:
        _append_orchestration_error(
            errors, phase="migration", code="MIGRATION_FAILED", error=exc
        )

    if not errors:
        try:
            owned = start_owned_t65_process(
                spec=popen_spec,
                config=config,
                starter=dependencies.start,
            )
            completed.append("start")
        except BaseException as exc:
            _append_orchestration_error(
                errors, phase="start", code="PROCESS_START_FAILED", error=exc
            )

    if not errors and owned is not None:
        try:
            observation = dependencies.observe_readiness(owned)
            marker = accept_owned_readiness(
                owned=owned,
                observation=observation,
                config=config,
                prefixes=prefixes,
            )
            api_readiness = evaluate_api_probe(dependencies.probe_api(owned))
            if api_readiness.api_status != "READY":
                raise T65BuiltinExecutorError("API probes did not become ready")
            completed.append("readiness")
        except BaseException as exc:
            _append_orchestration_error(
                errors, phase="readiness", code="READINESS_FAILED", error=exc
            )

    if not errors and owned is not None:
        try:
            capture = dependencies.capture(owned)
            completed.append("capture")
        except BaseException as exc:
            _append_orchestration_error(
                errors, phase="capture", code="CAPTURE_FAILED", error=exc
            )

    if owned is not None:
        try:
            termination = terminate_owned_t65_process(
                owned,
                config=config,
                graceful_timeout_seconds=graceful_timeout_seconds,
                kill_timeout_seconds=kill_timeout_seconds,
            )
            completed.append("stop")
            if termination.status == "blocked":
                errors.append(
                    T65OrchestrationError(
                        phase="stop",
                        code="OWNED_PROCESS_DID_NOT_EXIT",
                        error_type="TimeoutError",
                    )
                )
        except BaseException as exc:
            _append_orchestration_error(
                errors, phase="stop", code="PROCESS_STOP_FAILED", error=exc
            )

    if capture is not None and marker is not None and api_readiness is not None:
        try:
            evidence = dependencies.build_evidence(capture, marker, api_readiness)
            completed.append("evidence")
        except BaseException as exc:
            _append_orchestration_error(
                errors, phase="evidence", code="EVIDENCE_BUILD_FAILED", error=exc
            )

    cleanup = _run_t65_cleanup_hooks(dependencies.cleanup)
    completed.append("cleanup")
    hard_stops = tuple(
        dict.fromkeys(
            [item.code for item in errors]
            + list(cleanup.hard_stop_conditions)
        )
    )
    complete = (
        not hard_stops
        and evidence is not None
        and marker is not None
        and api_readiness is not None
        and api_readiness.api_status == "READY"
        and termination is not None
        and termination.status != "blocked"
        and cleanup.cleanup_complete
    )
    return T65OrchestrationResult(
        status="COMPLETE" if complete else "BLOCKED",
        completed_phases=tuple(completed),
        process_pid=owned.pid if owned is not None else None,
        readiness=marker,
        api_readiness=api_readiness,
        termination=termination,
        evidence=evidence,
        cleanup=cleanup,
        errors=tuple(errors),
        hard_stop_conditions=hard_stops,
    )


def _run_t65_cleanup_hooks(hooks: T65CleanupHooks) -> T65CleanupState:
    state = T65CleanupState()
    ordered = (
        ("requests_stopped", hooks.requests_stopped, "REQUEST_STOP_FAILED"),
        ("api_stopped", hooks.api_stopped, "API_SHUTDOWN_FAILED"),
        ("worker_stopped", hooks.worker_stopped, "WORKER_SHUTDOWN_FAILED"),
        (
            "evidence_finalized",
            hooks.evidence_finalized,
            "EVIDENCE_FINALIZE_FAILED",
        ),
        ("relations_dropped", hooks.relations_dropped, "RELATION_CLEANUP_FAILED"),
        (
            "temporary_files_removed",
            hooks.temporary_files_removed,
            "TEMPORARY_CLEANUP_FAILED",
        ),
    )
    for phase, hook, code in ordered:
        try:
            hook()
        except BaseException as exc:
            state = state.record_error(code=code, error=exc)
        state = state.advance(phase)
    return state.advance("complete")


def _append_orchestration_error(
    errors: list[T65OrchestrationError],
    *,
    phase: str,
    code: str,
    error: BaseException,
) -> None:
    errors.append(
        T65OrchestrationError(
            phase=phase,
            code=code,
            # Exception messages can contain DSNs, answers, or credentials.
            error_type=type(error).__name__,
        )
    )


def _canonical_sha256(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _format_float(value: float) -> str:
    return format(value, ".15g")
