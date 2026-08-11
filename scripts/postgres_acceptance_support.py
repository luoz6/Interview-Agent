from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from app.adapters.postgres.owned_scope import (
    OwnedPostgresScope,
    Psycopg2OwnedScopeBackend,
)
from app.ports.postgres_scope import OwnedPostgresLease, PostgresCleanupResidue
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from app.ports.postgres_scope import PostgresScopeApproval
from contracts.evidence.receipt import HmacReceiptSigner


class AcceptanceConfigurationError(ValueError):
    code = "ACCEPTANCE_CONFIGURATION_INVALID"


@dataclass(frozen=True)
class ActivePostgresScope:
    scope: OwnedPostgresScope
    lease: OwnedPostgresLease


@contextmanager
def approved_postgres_scope(
    *,
    dsn: str,
    scope_prefix: str,
    environ: Mapping[str, str],
) -> Iterator[ActivePostgresScope]:
    approval = load_postgres_scope_approval(
        environ,
        scope_prefix=scope_prefix,
    )
    scope = OwnedPostgresScope(
        Psycopg2OwnedScopeBackend(
            DirectPsycopg2ConnectionProvider(
                dsn,
                connect_kwargs={"connect_timeout": 3},
            )
        )
    )
    lease = None
    with scope.open(approval) as lease:
        scope.assert_owned(lease)
        yield ActivePostgresScope(scope=scope, lease=lease)
        scope.assert_owned(lease)
    receipt = lease.cleanup_receipt
    if receipt is None:
        raise PostgresCleanupResidue("PostgreSQL cleanup receipt is missing")
    if (
        not receipt.ownership_verified
        or not receipt.target_verified
        or receipt.residue_count != 0
    ):
        raise PostgresCleanupResidue("PostgreSQL cleanup was not proven")


def require_environment_value(
    environ: Mapping[str, str],
    name: str,
) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise AcceptanceConfigurationError(f"required environment is missing: {name}")
    return value


def load_receipt_signer(
    environ: Mapping[str, str],
) -> HmacReceiptSigner:
    key_id = require_environment_value(environ, "EVIDENCE_HMAC_KEY_ID")
    encoded_secret = require_environment_value(
        environ,
        "EVIDENCE_HMAC_SECRET_B64",
    )
    try:
        secret = base64.b64decode(encoded_secret, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AcceptanceConfigurationError(
            "EVIDENCE_HMAC_SECRET_B64 must be strict base64"
        ) from exc
    if len(secret) < 32:
        raise AcceptanceConfigurationError(
            "decoded Evidence HMAC secret must contain at least 32 bytes"
        )
    return HmacReceiptSigner(key_id=key_id, secret=secret)


def load_postgres_scope_approval(
    environ: Mapping[str, str],
    *,
    scope_prefix: str,
    namespace: str = "POSTGRES_ACCEPTANCE",
) -> PostgresScopeApproval:
    approval_id = require_environment_value(environ, f"{namespace}_APPROVAL_ID")
    receipt_sha256 = require_environment_value(
        environ,
        f"{namespace}_APPROVAL_RECEIPT_SHA256",
    )
    fingerprint = require_environment_value(
        environ,
        f"{namespace}_APPROVED_FINGERPRINT",
    )
    allowlist_value = require_environment_value(
        environ,
        f"{namespace}_DATABASE_ALLOWLIST",
    )
    expires_value = require_environment_value(
        environ,
        f"{namespace}_APPROVAL_EXPIRES_AT",
    )
    allowlist = frozenset(
        item.strip() for item in allowlist_value.split(",") if item.strip()
    )
    try:
        expires_at = datetime.fromisoformat(expires_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AcceptanceConfigurationError(
            f"{namespace}_APPROVAL_EXPIRES_AT must be an ISO-8601 timestamp"
        ) from exc
    if expires_at.tzinfo is None:
        raise AcceptanceConfigurationError(
            f"{namespace}_APPROVAL_EXPIRES_AT must be timezone-aware"
        )
    return PostgresScopeApproval(
        approval_id=approval_id,
        approval_receipt_sha256=receipt_sha256,
        approved_target_fingerprint=fingerprint,
        database_allowlist=allowlist,
        scope_prefix=scope_prefix,
        expires_at=expires_at,
    )
