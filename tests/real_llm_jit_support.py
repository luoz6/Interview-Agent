"""Fail-closed authorization and quota support for opt-in real Provider tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from time import monotonic, sleep
from typing import Callable
from uuid import uuid4
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.context_budget import (
    MAIN_QUESTION_CONTEXT_POLICY,
    REPORT_CONTEXT_POLICY,
)
from app.services.llm import LLMConfig
from app.services.main_question_generation import (
    MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS,
    MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS,
    MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS,
    load_main_question_generation_settings,
)


RECEIPT_PATH_ENV = "REAL_LLM_JIT_AUTHORIZATION_RECEIPT_PATH"
RECEIPT_SHA_ENV = "REAL_LLM_JIT_AUTHORIZATION_SHA256"
LEDGER_PATH_ENV = "REAL_LLM_JIT_LEDGER_PATH"
JIT_MAIN_QUESTION_SCOPE = "jit_main_question"
LEGACY_REVIEWER_SMOKE_SCOPE = "legacy_reviewer_smoke"
_SUPPORTED_SCOPES = frozenset(
    {JIT_MAIN_QUESTION_SCOPE, LEGACY_REVIEWER_SMOKE_SCOPE}
)


class RealJitAuthorizationError(RuntimeError):
    pass


class RealJitAuthorizationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    authorization_scope: str
    authorization_id: str = Field(min_length=1, max_length=128)
    base_url: str
    model: str = Field(min_length=1, max_length=128)
    expires_at: str
    data_classification: str
    max_requests: int = Field(ge=1)
    max_input_tokens_per_request: int = Field(ge=1)
    max_output_tokens_per_request: int = Field(ge=1)
    max_total_input_tokens: int = Field(ge=1)
    max_total_output_tokens: int = Field(ge=1)

    @field_validator("schema_version")
    @classmethod
    def validate_schema(cls, value: str) -> str:
        if value not in {
            "interview-jit-provider-authorization-v1",
            "interview-provider-authorization-v1",
        }:
            raise ValueError("unsupported real Provider authorization schema")
        return value

    @field_validator("authorization_scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        if value not in _SUPPORTED_SCOPES:
            raise ValueError("unsupported real Provider authorization scope")
        return value

    @field_validator("authorization_id")
    @classmethod
    def validate_authorization_id(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) is None:
            raise ValueError("authorization_id is invalid")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("authorized base_url must be an HTTPS origin")
        return value.rstrip("/")

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("expires_at is invalid") from exc
        if parsed.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_limits(self):
        if self.data_classification != "synthetic_only":
            raise ValueError("only synthetic data is authorized")
        expected_schema = {
            JIT_MAIN_QUESTION_SCOPE: "interview-jit-provider-authorization-v1",
            LEGACY_REVIEWER_SMOKE_SCOPE: "interview-provider-authorization-v1",
        }[self.authorization_scope]
        if self.schema_version != expected_schema:
            raise ValueError("authorization schema and scope do not match")
        if self.max_total_input_tokens != (
            self.max_requests * self.max_input_tokens_per_request
        ):
            raise ValueError("authorized total input budget is inconsistent")
        if self.max_total_output_tokens != (
            self.max_requests * self.max_output_tokens_per_request
        ):
            raise ValueError("authorized total output budget is inconsistent")
        return self

    def assert_not_expired(self, *, now: datetime | None = None) -> None:
        expires_at = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        if expires_at <= (now or datetime.now(timezone.utc)):
            raise RealJitAuthorizationError("real JIT authorization is expired")


def canonical_receipt_sha256(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _reject_duplicate_object_keys(pairs):
    payload = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate receipt field: {key}")
        payload[key] = value
    return payload


def _scope_limits(scope: str):
    if scope == JIT_MAIN_QUESTION_SCOPE:
        return 3, MAIN_QUESTION_CONTEXT_POLICY
    if scope == LEGACY_REVIEWER_SMOKE_SCOPE:
        return 4, REPORT_CONTEXT_POLICY
    raise RealJitAuthorizationError("unsupported real Provider authorization scope")


def load_real_jit_authorization(*, scope: str = JIT_MAIN_QUESTION_SCOPE):
    environment = os.environ
    required = (
        RECEIPT_PATH_ENV,
        RECEIPT_SHA_ENV,
        LEDGER_PATH_ENV,
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
    )
    missing = [name for name in required if not str(environment.get(name, "")).strip()]
    if missing:
        raise RealJitAuthorizationError(
            "real Provider opt-in is missing authorization fields: "
            + ", ".join(missing)
        )
    supplied_receipt_path = Path(environment[RECEIPT_PATH_ENV]).expanduser()
    if supplied_receipt_path.is_symlink():
        raise RealJitAuthorizationError(
            "real JIT authorization receipt cannot be a symlink"
        )
    receipt_path = supplied_receipt_path.resolve()
    if not receipt_path.is_file():
        raise RealJitAuthorizationError("real JIT authorization receipt is unavailable")
    try:
        raw = json.loads(
            receipt_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
        )
        if not isinstance(raw, dict):
            raise ValueError("receipt root must be an object")
        receipt = RealJitAuthorizationReceipt.model_validate(raw)
    except (OSError, ValueError) as exc:
        raise RealJitAuthorizationError(
            f"real JIT authorization receipt is invalid: {exc}"
        ) from exc
    actual_sha = canonical_receipt_sha256(raw)
    expected_sha = str(environment[RECEIPT_SHA_ENV]).strip()
    if (
        re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None
        or not hmac.compare_digest(actual_sha, expected_sha)
    ):
        raise RealJitAuthorizationError("real JIT authorization receipt hash mismatch")
    receipt.assert_not_expired()

    max_requests, context_policy = _scope_limits(scope)
    if receipt.authorization_scope != scope:
        raise RealJitAuthorizationError(
            "real Provider authorization scope does not match the selected workload"
        )
    if receipt.max_requests != max_requests:
        raise RealJitAuthorizationError(
            f"real Provider {scope} workload requires max_requests={max_requests}"
        )
    if receipt.max_input_tokens_per_request != context_policy.input_cap_tokens:
        raise RealJitAuthorizationError(
            "authorized input budget does not match runtime policy"
        )
    if receipt.max_output_tokens_per_request != context_policy.max_output_tokens:
        raise RealJitAuthorizationError(
            "authorized output budget does not match runtime policy"
        )

    if str(environment.get("OPENAI_MAX_RETRIES", "")) != "0":
        raise RealJitAuthorizationError("OPENAI_MAX_RETRIES=0 is required")
    config = LLMConfig.from_env()
    if (config.base_url or "").rstrip("/") != receipt.base_url:
        raise RealJitAuthorizationError("configured Provider URL is not authorized")
    if config.model != receipt.model:
        raise RealJitAuthorizationError("configured Provider model is not authorized")
    if scope == JIT_MAIN_QUESTION_SCOPE:
        settings = load_main_question_generation_settings()
        if settings.max_provider_invocations != MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS:
            raise RealJitAuthorizationError("main-question runtime budget drifted")
        if (
            settings.attempt_timeout_seconds > MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS
            or settings.total_timeout_seconds > MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS
        ):
            raise RealJitAuthorizationError("main-question timeout budget increased")
    return receipt, actual_sha, config


class RealJitRequestLedger:
    def __init__(
        self,
        path: Path,
        *,
        receipt,
        receipt_sha256: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        supplied_path = path.expanduser()
        supplied_lock_path = supplied_path.with_name(supplied_path.name + ".lock")
        if supplied_path.is_symlink() or supplied_lock_path.is_symlink():
            raise RealJitAuthorizationError(
                "real JIT ledger path cannot be a symlink"
            )
        self.path = supplied_path.resolve()
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.receipt = receipt
        self.receipt_sha256 = receipt_sha256
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        if not self.path.parent.is_dir():
            raise RealJitAuthorizationError("real JIT ledger parent does not exist")

    def consume(self) -> int:
        with self._exclusive_lock():
            self.receipt.assert_not_expired(now=self._clock())
            payload = self._load_or_initialize()
            consumed = payload["consumed_requests"]
            if consumed >= self.receipt.max_requests:
                raise RealJitAuthorizationError("real JIT Provider request budget exhausted")
            payload["consumed_requests"] = consumed + 1
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._atomic_write(payload)
            return payload["consumed_requests"]

    def snapshot(self) -> dict:
        with self._exclusive_lock():
            return dict(self._load_or_initialize())

    def _load_or_initialize(self) -> dict:
        if self.path.is_symlink():
            raise RealJitAuthorizationError(
                "real JIT ledger path cannot be a symlink"
            )
        if self.path.exists() and self.path.stat().st_size:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RealJitAuthorizationError("real JIT ledger is invalid") from exc
            expected = {
                "schema_version": "interview-jit-provider-ledger-v1",
                "authorization_id": self.receipt.authorization_id,
                "receipt_sha256": self.receipt_sha256,
                "max_requests": self.receipt.max_requests,
            }
            expected_keys = {
                *expected,
                "consumed_requests",
                "created_at",
                "updated_at",
            }
            if (
                not isinstance(payload, dict)
                or set(payload) != expected_keys
                or any(payload.get(key) != value for key, value in expected.items())
            ):
                raise RealJitAuthorizationError(
                    "real JIT ledger belongs to another authorization"
                )
            consumed = payload.get("consumed_requests")
            if (
                isinstance(consumed, bool)
                or not isinstance(consumed, int)
                or not 0 <= consumed <= self.receipt.max_requests
            ):
                raise RealJitAuthorizationError("real JIT ledger count is invalid")
            for field in ("created_at", "updated_at"):
                try:
                    timestamp = datetime.fromisoformat(
                        str(payload[field]).replace("Z", "+00:00")
                    )
                except ValueError as exc:
                    raise RealJitAuthorizationError(
                        "real JIT ledger timestamp is invalid"
                    ) from exc
                if timestamp.tzinfo is None:
                    raise RealJitAuthorizationError(
                        "real JIT ledger timestamp is timezone-naive"
                    )
            return payload
        now = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": "interview-jit-provider-ledger-v1",
            "authorization_id": self.receipt.authorization_id,
            "receipt_sha256": self.receipt_sha256,
            "max_requests": self.receipt.max_requests,
            "consumed_requests": 0,
            "created_at": now,
            "updated_at": now,
        }

    def _atomic_write(self, payload: dict) -> None:
        if self.path.is_symlink():
            raise RealJitAuthorizationError(
                "real JIT ledger path cannot be a symlink"
            )
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        descriptor = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            stream = os.fdopen(descriptor, "wb")
            descriptor = None
            with stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()

    @contextmanager
    def _exclusive_lock(self):
        if self.lock_path.is_symlink():
            raise RealJitAuthorizationError(
                "real JIT ledger lock cannot be a symlink"
            )
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        acquired = False
        deadline = monotonic() + 10
        try:
            if os.fstat(descriptor).st_size < 1:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            while not acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except (BlockingIOError, OSError):
                    if monotonic() >= deadline:
                        raise RealJitAuthorizationError(
                            "real JIT ledger lock is unavailable"
                        )
                    sleep(0.02)
            yield
        finally:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def authorized_provider_attempt_hook(*, scope: str = JIT_MAIN_QUESTION_SCOPE):
    receipt, receipt_sha256, config = load_real_jit_authorization(scope=scope)
    ledger = RealJitRequestLedger(
        Path(os.environ[LEDGER_PATH_ENV]),
        receipt=receipt,
        receipt_sha256=receipt_sha256,
    )
    return config, ledger, ledger.consume
