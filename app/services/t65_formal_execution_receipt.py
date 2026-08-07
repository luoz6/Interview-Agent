from __future__ import annotations

from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Literal, Mapping, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+-]{1,128}$")
_SAFE_PREFIX_RE = re.compile(r"^test_t65perf_[0-9a-f]{12}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

REQUIRED_FORMAL_EXECUTOR_PATHS = frozenset(
    {
        "app/api/routes.py",
        "app/graphs/durable_interview_graph.py",
        "app/main.py",
        "app/services/config.py",
        "app/services/decision_store.py",
        "app/services/followup_decision_service.py",
        "app/services/followup_diagnostics.py",
        "app/services/followup_prompts.py",
        "app/services/interview_event_stream.py",
        "app/services/interview_generation_store.py",
        "app/services/llm.py",
        "app/services/postgres_decision_store.py",
        "app/services/postgres_identifiers.py",
        "app/services/provider_usage.py",
        "app/services/runtime.py",
        "app/services/runtime_events.py",
        "app/services/t65_builtin_production_executor.py",
        "app/services/t65_formal_execution_receipt.py",
        "app/services/t65_production_capture.py",
        "app/services/t65_provider_http_transport.py",
        "app/services/t65_runtime_performance.py",
        "scripts/run_t65_runtime_performance.py",
    }
)


class T65FormalReceiptError(ValueError):
    """The offline formal-execution contract was not satisfied."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_sha256(value: Mapping[str, object]) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _text_sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _require_sha256(value: str, label: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


class T65FormalRunIdentity(_FrozenModel):
    schema_version: Literal["t65-formal-run-identity-v1"] = (
        "t65-formal-run-identity-v1"
    )
    run_id: str
    candidate_revision: str
    candidate_tree: str
    capture_plan_sha256: str
    gate_config_sha256: str
    authorization_id: str = Field(min_length=1, max_length=200)
    authorization_sha256: str
    execution_manifest_sha256: str

    @model_validator(mode="after")
    def validate_identity(self):
        if _SAFE_RUN_ID_RE.fullmatch(self.run_id) is None:
            raise ValueError("run_id must be a bounded safe identifier")
        if _CONTROL_RE.search(self.authorization_id):
            raise ValueError("authorization_id contains a control character")
        for label, value in (
            ("candidate_revision", self.candidate_revision),
            ("candidate_tree", self.candidate_tree),
        ):
            if _GIT_OBJECT_RE.fullmatch(value) is None:
                raise ValueError(f"{label} must be a git object id")
        for label, value in (
            ("capture_plan_sha256", self.capture_plan_sha256),
            ("gate_config_sha256", self.gate_config_sha256),
            ("authorization_sha256", self.authorization_sha256),
            ("execution_manifest_sha256", self.execution_manifest_sha256),
        ):
            _require_sha256(value, label)
        return self

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def identity_sha256(self) -> str:
        return _canonical_sha256(self.canonical_payload())


class T65FormalExecutorCodeFile(_FrozenModel):
    path: str
    raw_sha256: str

    @model_validator(mode="after")
    def validate_file(self):
        path = PurePosixPath(self.path)
        if (
            not self.path
            or "\\" in self.path
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != self.path
            or _CONTROL_RE.search(self.path)
        ):
            raise ValueError("executor path must be a canonical safe relative POSIX path")
        _require_sha256(self.raw_sha256, "raw_sha256")
        return self


class T65FormalExecutorManifestReceipt(_FrozenModel):
    schema_version: Literal["t65-production-executor-code-manifest-v1"] = (
        "t65-production-executor-code-manifest-v1"
    )
    candidate_revision: str
    candidate_tree: str
    files: tuple[T65FormalExecutorCodeFile, ...]
    executor_sha256: str

    @model_validator(mode="after")
    def validate_manifest(self):
        for label, value in (
            ("candidate_revision", self.candidate_revision),
            ("candidate_tree", self.candidate_tree),
        ):
            if _GIT_OBJECT_RE.fullmatch(value) is None:
                raise ValueError(f"{label} must be a git object id")
        paths = tuple(item.path for item in self.files)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("executor files must be uniquely sorted by path")
        actual = set(paths)
        missing = REQUIRED_FORMAL_EXECUTOR_PATHS.difference(actual)
        unexpected = actual.difference(REQUIRED_FORMAL_EXECUTOR_PATHS)
        if missing or unexpected:
            raise ValueError(
                "executor manifest does not match exact formal surface: "
                f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
            )
        _require_sha256(self.executor_sha256, "executor_sha256")
        if self.executor_sha256 != _canonical_sha256(self.canonical_payload()):
            raise ValueError("executor_sha256 does not match the canonical manifest")
        return self

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_revision": self.candidate_revision,
            "candidate_tree": self.candidate_tree,
            "files": [item.model_dump(mode="json") for item in self.files],
        }


class T65PersistedCaptureReceipt(_FrozenModel):
    schema_version: Literal["t65-persisted-capture-receipt-v1"] = (
        "t65-persisted-capture-receipt-v1"
    )
    run_id: str
    capture_run_id: str
    capture_artifact_sha256: str
    storage_kind: Literal["atomic_fsync_local"] = "atomic_fsync_local"
    written_atomically: Literal[True] = True
    fsync_completed: Literal[True] = True
    reopened_and_verified: Literal[True] = True
    sample_count: int = Field(gt=0)

    @field_validator("capture_artifact_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        return _require_sha256(value, "capture_artifact_sha256")


class T65SqlEvidenceReceipt(_FrozenModel):
    schema_version: Literal["t65-sql-evidence-receipt-v1"] = (
        "t65-sql-evidence-receipt-v1"
    )
    run_id: str
    runtime_prefix: str
    sql_receipt_sha256: str
    session_ids: tuple[str, ...]
    provider_invocation_count: int = Field(gt=0)
    provider_trace_id_sha256s: tuple[str, ...]
    usage_complete: Literal[True] = True

    @model_validator(mode="after")
    def validate_sql(self):
        if _SAFE_PREFIX_RE.fullmatch(self.runtime_prefix) is None:
            raise ValueError("runtime_prefix is not an owned T65 namespace")
        _require_sha256(self.sql_receipt_sha256, "sql_receipt_sha256")
        if not self.session_ids or len(self.session_ids) != len(set(self.session_ids)):
            raise ValueError("SQL evidence requires unique nonempty session_ids")
        if any(not item or _CONTROL_RE.search(item) for item in self.session_ids):
            raise ValueError("session_ids must be safe nonempty identifiers")
        if len(self.provider_trace_id_sha256s) != self.provider_invocation_count:
            raise ValueError("SQL trace count must equal provider invocation count")
        if len(set(self.provider_trace_id_sha256s)) != len(self.provider_trace_id_sha256s):
            raise ValueError("SQL provider trace ids must be unique")
        for item in self.provider_trace_id_sha256s:
            _require_sha256(item, "provider_trace_id_sha256")
        return self


class T65FormalProviderLedgerReceipt(_FrozenModel):
    schema_version: Literal["t65-provider-attempt-ledger-receipt-v1"] = (
        "t65-provider-attempt-ledger-receipt-v1"
    )
    ledger_sha256: str
    run_id_sha256: str
    candidate_revision_sha256: str
    candidate_tree_sha256: str
    authorization_id_sha256: str
    authorization_sha256: str
    executor_sha256: str
    process_role: Literal["api", "report_worker"]
    process_id: int = Field(gt=0)
    start_count: int = Field(gt=0)
    finish_count: int = Field(gt=0)
    success_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    sequence_first: int = Field(gt=0)
    sequence_last: int = Field(gt=0)
    provider_response_id_sha256s: tuple[str, ...]
    response_id_missing_count: Literal[0] = 0
    duplicate_response_id_count: Literal[0] = 0
    complete: Literal[True] = True
    failure_code: None = None

    @model_validator(mode="after")
    def validate_ledger(self):
        for label, value in (
            ("ledger_sha256", self.ledger_sha256),
            ("run_id_sha256", self.run_id_sha256),
            ("candidate_revision_sha256", self.candidate_revision_sha256),
            ("candidate_tree_sha256", self.candidate_tree_sha256),
            ("authorization_id_sha256", self.authorization_id_sha256),
            ("authorization_sha256", self.authorization_sha256),
            ("executor_sha256", self.executor_sha256),
        ):
            _require_sha256(value, label)
        if self.start_count != self.finish_count:
            raise ValueError("ledger starts and finishes must balance")
        if self.success_count + self.error_count != self.finish_count:
            raise ValueError("ledger terminal outcome counts must balance")
        if self.sequence_last < self.sequence_first:
            raise ValueError("ledger sequence range is reversed")
        expected_event_count = self.start_count + self.finish_count
        if self.sequence_last - self.sequence_first + 1 != expected_event_count:
            raise ValueError("ledger sequence span must equal start and finish events")
        if len(self.provider_response_id_sha256s) != self.success_count:
            raise ValueError("every successful Provider call requires a response id")
        if len(set(self.provider_response_id_sha256s)) != len(
            self.provider_response_id_sha256s
        ):
            raise ValueError("Provider response ids must be unique")
        for item in self.provider_response_id_sha256s:
            _require_sha256(item, "provider_response_id_sha256")
        return self


class T65RuntimeEvidenceReceipt(_FrozenModel):
    schema_version: Literal["t65-runtime-evidence-receipt-v1"] = (
        "t65-runtime-evidence-receipt-v1"
    )
    run_id: str
    input_kind: Literal["persisted_reopened_capture"] = "persisted_reopened_capture"
    source_kind: Literal["live_provider"] = "live_provider"
    source_capture_sha256: str
    runtime_result_status: Literal["COMPLETE"] = "COMPLETE"
    provider_name: Literal["DeepSeek"] = "DeepSeek"
    model_id: Literal["deepseek-v4-pro"] = "deepseek-v4-pro"
    performance_artifact_sha256: str
    metrics_sha256: str

    @model_validator(mode="after")
    def validate_runtime(self):
        for label, value in (
            ("source_capture_sha256", self.source_capture_sha256),
            ("performance_artifact_sha256", self.performance_artifact_sha256),
            ("metrics_sha256", self.metrics_sha256),
        ):
            _require_sha256(value, label)
        return self


class T65CleanupReceipt(_FrozenModel):
    schema_version: Literal["t65-cleanup-receipt-v1"] = "t65-cleanup-receipt-v1"
    run_id: str
    cleanup_complete: Literal[True] = True
    owned_processes_exited: Literal[True] = True
    provider_ledgers_sealed: Literal[True] = True
    runtime_namespace_empty: Literal[True] = True
    vector_namespace_empty: Literal[True] = True
    sentinel_relations_preserved: Literal[True] = True
    temporary_secrets_removed: Literal[True] = True
    hard_stop_conditions: tuple[()] = ()

    @property
    def cleanup_receipt_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="json"))


class T65ReportExecutionReceipt(_FrozenModel):
    schema_version: Literal["t65-report-execution-receipt-v1"] = (
        "t65-report-execution-receipt-v1"
    )
    run_id: str
    status: Literal["COMPLETE", "BLOCKED"]
    embedding_provider: Literal["siliconflow"] | None = None
    embedding_authorization_sha256: str | None = None
    report_artifact_sha256: str | None = None
    report_sql_receipt_sha256: str | None = None
    sample_count: int = Field(default=0, ge=0)
    blocker: str | None = None

    @model_validator(mode="after")
    def validate_report(self):
        hashes = (
            self.embedding_authorization_sha256,
            self.report_artifact_sha256,
            self.report_sql_receipt_sha256,
        )
        if self.status == "COMPLETE":
            if self.embedding_provider != "siliconflow" or any(x is None for x in hashes):
                raise ValueError("complete report requires SiliconFlow authorization and receipts")
            if self.sample_count < 1 or self.blocker is not None:
                raise ValueError("complete report requires samples and no blocker")
            for value in hashes:
                _require_sha256(value, "report hash")  # type: ignore[arg-type]
        else:
            if not self.blocker or _CONTROL_RE.search(self.blocker):
                raise ValueError("blocked report requires a safe blocker")
            if (
                self.embedding_provider is not None
                or any(x is not None for x in hashes)
                or self.sample_count != 0
            ):
                raise ValueError("blocked report cannot expose formal artifacts")
        return self


class T65FormalExecutionReceipt(_FrozenModel):
    schema_version: Literal["t65-formal-execution-receipt-v1"] = (
        "t65-formal-execution-receipt-v1"
    )
    run_identity: T65FormalRunIdentity
    executor_manifest: T65FormalExecutorManifestReceipt
    execution_profile: Literal["builtin-interview-only-report-blocked-v1"] = (
        "builtin-interview-only-report-blocked-v1"
    )
    orchestration_status: Literal["COMPLETE"] = "COMPLETE"
    orchestration_config_sha256: str
    owned_process_ids: tuple[int, ...]
    readiness_receipt_sha256: str
    api_probe_receipt_sha256: str
    persisted_capture: T65PersistedCaptureReceipt
    sql_evidence: T65SqlEvidenceReceipt
    provider_ledgers: tuple[T65FormalProviderLedgerReceipt, ...]
    runtime_evidence: T65RuntimeEvidenceReceipt
    cleanup: T65CleanupReceipt
    report: T65ReportExecutionReceipt

    @model_validator(mode="after")
    def validate_cross_receipt_consistency(self):
        identity = self.run_identity
        for label, value in (
            ("orchestration_config_sha256", self.orchestration_config_sha256),
            ("readiness_receipt_sha256", self.readiness_receipt_sha256),
            ("api_probe_receipt_sha256", self.api_probe_receipt_sha256),
        ):
            _require_sha256(value, label)
        if self.executor_manifest.candidate_revision != identity.candidate_revision:
            raise ValueError("executor candidate revision does not match run identity")
        if self.executor_manifest.candidate_tree != identity.candidate_tree:
            raise ValueError("executor candidate tree does not match run identity")
        run_ids = {
            self.persisted_capture.run_id,
            self.persisted_capture.capture_run_id,
            self.sql_evidence.run_id,
            self.runtime_evidence.run_id,
            self.cleanup.run_id,
            self.report.run_id,
        }
        if run_ids != {identity.run_id}:
            raise ValueError("cross-receipt run identity mismatch")
        if self.persisted_capture.capture_artifact_sha256 != self.runtime_evidence.source_capture_sha256:
            raise ValueError("runtime evidence is not bound to the persisted capture hash")
        if (
            self.report.status != "BLOCKED"
            or self.report.blocker
            != "SILICONFLOW_EMBEDDING_AUTHORIZATION_REQUIRED"
        ):
            raise ValueError(
                "builtin interview-only structure requires the authorized report blocker"
            )
        if not self.provider_ledgers:
            raise ValueError("formal execution requires sealed Provider ledgers")
        if len(self.provider_ledgers) != 1 or self.provider_ledgers[0].process_role != "api":
            raise ValueError(
                "builtin interview-only structure requires exactly one API Provider ledger"
            )
        if not self.owned_process_ids or tuple(sorted(set(self.owned_process_ids))) != self.owned_process_ids:
            raise ValueError("owned process ids must be nonempty, unique, and sorted")
        if tuple(sorted(item.process_id for item in self.provider_ledgers)) != self.owned_process_ids:
            raise ValueError("owned process ids must exactly match Provider ledgers")
        expected = {
            "run_id_sha256": _text_sha256(identity.run_id),
            "candidate_revision_sha256": _text_sha256(identity.candidate_revision),
            "candidate_tree_sha256": _text_sha256(identity.candidate_tree),
            "authorization_id_sha256": _text_sha256(identity.authorization_id),
            "authorization_sha256": identity.authorization_sha256,
            "executor_sha256": self.executor_manifest.executor_sha256,
        }
        for ledger in self.provider_ledgers:
            for field, value in expected.items():
                if getattr(ledger, field) != value:
                    raise ValueError(f"Provider ledger {field} mismatch")
        starts = sum(item.start_count for item in self.provider_ledgers)
        if starts != self.sql_evidence.provider_invocation_count:
            raise ValueError("Provider ledger starts do not match SQL invocation count")
        response_ids = tuple(
            sorted(
                response_id
                for ledger in self.provider_ledgers
                for response_id in ledger.provider_response_id_sha256s
            )
        )
        if response_ids != tuple(sorted(self.sql_evidence.provider_trace_id_sha256s)):
            raise ValueError("Provider ledger response ids do not match SQL trace ids")
        return self

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def receipt_sha256(self) -> str:
        return _canonical_sha256(self.canonical_payload())


T65ExecutionRoute = Literal[
    "fixture_diagnostic",
    "saved_replay_diagnostic",
    "injected_diagnostic",
    "builtin_unavailable",
    "builtin_candidate",
]


def resolve_t65_execution_route(
    *,
    provider_mode: Literal["fixture", "provider"],
    source_capture_present: bool,
    capture_executor_present: bool,
    builtin_enabled: bool = False,
) -> T65ExecutionRoute:
    """Resolve one mutually exclusive route without claiming formal eligibility."""

    selected = sum((source_capture_present, capture_executor_present, builtin_enabled))
    if selected > 1:
        raise T65FormalReceiptError("execution routes are mutually exclusive")
    if provider_mode == "fixture":
        if selected:
            raise T65FormalReceiptError("fixture mode cannot select a Provider execution route")
        return "fixture_diagnostic"
    if source_capture_present:
        return "saved_replay_diagnostic"
    if capture_executor_present:
        return "injected_diagnostic"
    if builtin_enabled:
        return "builtin_candidate"
    return "builtin_unavailable"


def validate_t65_formal_route(
    *,
    route: T65ExecutionRoute,
    receipt: object,
    expected_identity: T65FormalRunIdentity | None = None,
    expected_executor_manifest: T65FormalExecutorManifestReceipt | None = None,
) -> bool:
    """Fail closed until trusted external verification is implemented.

    ``T65FormalExecutionReceipt`` proves only that a self-reported receipt is
    structurally and internally consistent.  Hash-shaped strings and boolean
    assertions are forgeable.  A future formal verifier must independently
    establish the signed candidate/execution identity and reopen and rehash
    the capture, SQL receipt, Provider ledgers, and output artifacts before it
    can mint a module-owned verified result.  B1 deliberately has no such
    verifier, so no route is formally eligible yet.
    """

    del route, receipt, expected_identity, expected_executor_manifest
    return False


def validate_t65_formal_receipt_structure(
    *,
    receipt: object,
    expected_identity: T65FormalRunIdentity | None = None,
    expected_executor_manifest: T65FormalExecutorManifestReceipt | None = None,
) -> bool:
    """Check offline structure only; this never grants formal eligibility."""

    try:
        if isinstance(receipt, T65FormalExecutionReceipt):
            raw = receipt.model_dump(mode="python")
            validated = T65FormalExecutionReceipt.model_validate(raw)
        elif isinstance(receipt, Mapping):
            validated = T65FormalExecutionReceipt.model_validate(dict(receipt))
        elif isinstance(receipt, (str, bytes, bytearray)):
            validated = T65FormalExecutionReceipt.model_validate_json(receipt)
        else:
            return False
    except (AttributeError, TypeError, ValueError, ValidationError):
        return False
    if expected_identity is not None and validated.run_identity != expected_identity:
        return False
    if (
        expected_executor_manifest is not None
        and validated.executor_manifest != expected_executor_manifest
    ):
        return False
    return True


def build_formal_executor_manifest_receipt(
    *,
    candidate_revision: str,
    candidate_tree: str,
    files: Sequence[T65FormalExecutorCodeFile],
) -> T65FormalExecutorManifestReceipt:
    ordered = tuple(sorted(files, key=lambda item: item.path))
    payload: dict[str, object] = {
        "schema_version": "t65-production-executor-code-manifest-v1",
        "candidate_revision": candidate_revision,
        "candidate_tree": candidate_tree,
        "files": [item.model_dump(mode="json") for item in ordered],
    }
    return T65FormalExecutorManifestReceipt(
        candidate_revision=candidate_revision,
        candidate_tree=candidate_tree,
        files=ordered,
        executor_sha256=_canonical_sha256(payload),
    )
