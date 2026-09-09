from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


OPT_IN_ENV = "RUN_POSTGRES_V3_CHECKPOINTER_RECOVERY"
RECEIPT_PATH_ENV = "POSTGRES_V3_CHECKPOINTER_AUTHORIZATION_RECEIPT_PATH"
RECEIPT_SHA_ENV = "POSTGRES_V3_CHECKPOINTER_AUTHORIZATION_SHA256"
RECEIPT_SCHEMA_VERSION = (
    "interview-postgres-v3-checkpointer-recovery-authorization-v1"
)
AUTHORIZED_DATABASE = "interview"
AUTHORIZED_RELATIONS = (
    "public.checkpoint_blobs",
    "public.checkpoint_writes",
    "public.checkpoints",
)
AUTHORIZED_THREAD_ID_PREFIX = "test_jit_v3_"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_THREAD_ID = re.compile(r"^test_jit_v3_[0-9a-f]{32}$")


class CheckpointerRecoveryNotEnabled(RuntimeError):
    pass


class CheckpointerRecoveryAuthorizationError(RuntimeError):
    pass


class CheckpointerRecoveryAuthorizationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    approval_id: str = Field(min_length=1, max_length=128)
    target_fingerprint: str
    database: str
    relations: tuple[str, ...]
    thread_id_prefix: str
    expires_at: str

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != RECEIPT_SCHEMA_VERSION:
            raise ValueError("unsupported checkpointer authorization schema")
        return value

    @field_validator("approval_id")
    @classmethod
    def validate_approval_id(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) is None:
            raise ValueError("approval_id is invalid")
        return value

    @field_validator("target_fingerprint")
    @classmethod
    def validate_target_fingerprint(cls, value: str) -> str:
        if _SHA256_HEX.fullmatch(value) is None:
            raise ValueError("target_fingerprint must be lowercase SHA-256")
        return value

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("expires_at is invalid") from exc
        if parsed.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_row_scope(self):
        if self.database != AUTHORIZED_DATABASE:
            raise ValueError("authorization is not bound to database=interview")
        if self.relations != AUTHORIZED_RELATIONS:
            raise ValueError("authorization relations exceed the checkpoint row scope")
        if self.thread_id_prefix != AUTHORIZED_THREAD_ID_PREFIX:
            raise ValueError("authorization thread_id_prefix is invalid")
        return self

    def assert_current(self, *, now: datetime | None = None) -> None:
        expires_at = datetime.fromisoformat(
            self.expires_at.replace("Z", "+00:00")
        )
        if expires_at <= (now or datetime.now(timezone.utc)):
            raise CheckpointerRecoveryAuthorizationError(
                "checkpointer recovery authorization is expired"
            )


def canonical_authorization_sha256(payload: dict) -> str:
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


def load_checkpointer_recovery_authorization(
    environment: Mapping[str, str],
    *,
    current_target_fingerprint: str,
    current_database: str,
    now: datetime | None = None,
) -> CheckpointerRecoveryAuthorizationReceipt:
    if str(environment.get(OPT_IN_ENV, "")).strip() != "1":
        raise CheckpointerRecoveryNotEnabled(
            f"set {OPT_IN_ENV}=1 to enable the PostgreSQL V3 recovery test"
        )
    missing = [
        name
        for name in (RECEIPT_PATH_ENV, RECEIPT_SHA_ENV)
        if not str(environment.get(name, "")).strip()
    ]
    if missing:
        raise CheckpointerRecoveryAuthorizationError(
            "checkpointer recovery opt-in is missing authorization fields: "
            + ", ".join(missing)
        )
    supplied_path = Path(str(environment[RECEIPT_PATH_ENV])).expanduser()
    if supplied_path.is_symlink():
        raise CheckpointerRecoveryAuthorizationError(
            "checkpointer authorization receipt cannot be a symlink"
        )
    receipt_path = supplied_path.resolve()
    if not receipt_path.is_file():
        raise CheckpointerRecoveryAuthorizationError(
            "checkpointer authorization receipt is unavailable"
        )
    try:
        raw = json.loads(
            receipt_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
        )
        if not isinstance(raw, dict):
            raise ValueError("receipt root must be an object")
        receipt = CheckpointerRecoveryAuthorizationReceipt.model_validate(raw)
    except (OSError, ValueError) as exc:
        raise CheckpointerRecoveryAuthorizationError(
            f"checkpointer authorization receipt is invalid: {exc}"
        ) from exc
    expected_sha = str(environment[RECEIPT_SHA_ENV]).strip()
    actual_sha = canonical_authorization_sha256(raw)
    if (
        _SHA256_HEX.fullmatch(expected_sha) is None
        or not hmac.compare_digest(actual_sha, expected_sha)
    ):
        raise CheckpointerRecoveryAuthorizationError(
            "checkpointer authorization receipt hash mismatch"
        )
    if current_database != AUTHORIZED_DATABASE:
        raise CheckpointerRecoveryAuthorizationError(
            "current PostgreSQL database is not authorized"
        )
    if not hmac.compare_digest(
        receipt.target_fingerprint, current_target_fingerprint
    ):
        raise CheckpointerRecoveryAuthorizationError(
            "current PostgreSQL target fingerprint is not authorized"
        )
    receipt.assert_current(now=now)
    return receipt


@dataclass(frozen=True)
class AuthorizedCheckpointRowScope:
    receipt: CheckpointerRecoveryAuthorizationReceipt

    def assert_thread_id(self, thread_id: str) -> None:
        if not thread_id.startswith(self.receipt.thread_id_prefix):
            raise CheckpointerRecoveryAuthorizationError(
                "checkpoint thread_id is outside the authorized prefix"
            )
        if _THREAD_ID.fullmatch(thread_id) is None:
            raise CheckpointerRecoveryAuthorizationError(
                "checkpoint thread_id is not an isolated test identity"
            )

    def assert_write_authorized(self, thread_id: str) -> None:
        self.assert_thread_id(thread_id)
        self.receipt.assert_current()

    def inventory_for_write(
        self, connection_provider, thread_id: str
    ) -> dict[str, int]:
        self.assert_write_authorized(thread_id)
        self._assert_relations_exist(connection_provider)
        return self._inventory(connection_provider, thread_id)

    def _assert_relations_exist(self, connection_provider) -> None:
        with connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                for relation in AUTHORIZED_RELATIONS:
                    cursor.execute("SELECT to_regclass(%s)", (relation,))
                    if cursor.fetchone()[0] is None:
                        raise CheckpointerRecoveryAuthorizationError(
                            f"authorized checkpoint relation is missing: {relation}"
                        )

    def _inventory(
        self, connection_provider, thread_id: str
    ) -> dict[str, int]:
        statements = {
            "public.checkpoint_blobs": (
                "SELECT COUNT(*) FROM public.checkpoint_blobs "
                "WHERE thread_id = %s"
            ),
            "public.checkpoint_writes": (
                "SELECT COUNT(*) FROM public.checkpoint_writes "
                "WHERE thread_id = %s"
            ),
            "public.checkpoints": (
                "SELECT COUNT(*) FROM public.checkpoints WHERE thread_id = %s"
            ),
        }
        counts = {}
        with connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                for relation in AUTHORIZED_RELATIONS:
                    cursor.execute(statements[relation], (thread_id,))
                    counts[relation] = int(cursor.fetchone()[0])
        return counts

    def cleanup_and_inventory(
        self, connection_provider, thread_id: str
    ) -> dict[str, int]:
        # Expiry blocks new writes, but cannot block exact-thread cleanup.
        self.assert_thread_id(thread_id)
        statements = (
            "DELETE FROM public.checkpoint_writes WHERE thread_id = %s",
            "DELETE FROM public.checkpoint_blobs WHERE thread_id = %s",
            "DELETE FROM public.checkpoints WHERE thread_id = %s",
        )
        with connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement, (thread_id,))
        return self._inventory(connection_provider, thread_id)


__all__ = [
    "AUTHORIZED_DATABASE",
    "AUTHORIZED_RELATIONS",
    "AUTHORIZED_THREAD_ID_PREFIX",
    "AuthorizedCheckpointRowScope",
    "CheckpointerRecoveryAuthorizationError",
    "CheckpointerRecoveryAuthorizationReceipt",
    "CheckpointerRecoveryNotEnabled",
    "OPT_IN_ENV",
    "RECEIPT_PATH_ENV",
    "RECEIPT_SCHEMA_VERSION",
    "RECEIPT_SHA_ENV",
    "canonical_authorization_sha256",
    "load_checkpointer_recovery_authorization",
]
