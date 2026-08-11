from __future__ import annotations

import base64
from dataclasses import replace
import json
import os
from secrets import token_urlsafe
from uuid import uuid4

import psycopg2
from psycopg2 import extensions, sql
import pytest

from app.adapters.postgres.owned_scope import (
    OwnedPostgresScope,
    Psycopg2OwnedScopeBackend,
)
from app.ports.postgres_scope import (
    PostgresOwnershipLost,
    PostgresPermissionDenied,
    PostgresTargetMismatch,
)
from app.services.postgres_connections import DirectPsycopg2ConnectionProvider
from contracts.evidence import EvidenceRegistry, EvidenceVerifier, HmacReceiptSigner
from scripts import stage43b_recovery_acceptance as stage43b
from scripts.postgres_acceptance_support import load_postgres_scope_approval


pytestmark = pytest.mark.pg_runtime


def _backend(dsn: str) -> Psycopg2OwnedScopeBackend:
    return Psycopg2OwnedScopeBackend(
        DirectPsycopg2ConnectionProvider(
            dsn,
            connect_kwargs={"connect_timeout": 3},
        )
    )


def _prefix(label: str) -> str:
    return f"test_{label}_{uuid4().hex[:12]}"


def _approval(prefix: str):
    return load_postgres_scope_approval(
        os.environ,
        scope_prefix=prefix,
        namespace="POSTGRES_TEST",
    )


def test_real_target_ownership_cleanup_and_zero_residue(postgres_dsn):
    prefix = _prefix("c6scope")
    backend = _backend(postgres_dsn)
    approval = _approval(prefix)
    assert backend.inspect_identity().fingerprint == approval.approved_target_fingerprint
    scope = OwnedPostgresScope(backend)

    with scope.open(approval) as lease:
        payload_table = f"{prefix}_payload"
        with backend._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE TABLE {table} (id BIGINT PRIMARY KEY)").format(
                        table=sql.Identifier(payload_table)
                    )
                )
        scope.assert_owned(lease)

        marker_table = f"{prefix}_ownership"
        with backend._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET ownership_token = %s::uuid"
                    ).format(table=sql.Identifier(marker_table)),
                    (uuid4().hex,),
                )
        with pytest.raises(PostgresOwnershipLost):
            scope.assert_owned(lease)
        with backend._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET ownership_token = %s::uuid"
                    ).format(table=sql.Identifier(marker_table)),
                    (lease.ownership_token,),
                )
        scope.assert_owned(lease)

    receipt = lease.cleanup_receipt
    assert receipt is not None
    assert receipt.target_fingerprint == approval.approved_target_fingerprint
    assert receipt.ownership_verified is True
    assert receipt.target_verified is True
    assert receipt.resources_examined == 2
    assert receipt.resources_removed == 2
    assert receipt.residue_count == 0
    with backend._connection() as connection:
        with connection.cursor() as cursor:
            assert backend._relations(cursor, prefix) == []


def test_real_wrong_target_is_rejected_before_scope_creation(postgres_dsn):
    prefix = _prefix("c6wrong")
    backend = _backend(postgres_dsn)
    approval = replace(_approval(prefix), approved_target_fingerprint="f" * 64)

    with pytest.raises(PostgresTargetMismatch):
        with OwnedPostgresScope(backend).open(approval):
            pytest.fail("wrong target must not yield an owned scope")

    with backend._connection() as connection:
        with connection.cursor() as cursor:
            assert backend._relations(cursor, prefix) == []


def test_real_permission_denied_is_stable_and_leaves_no_scope(postgres_dsn):
    admin_dsn = os.getenv("POSTGRES_SCOPE_ADMIN_DSN", "").strip()
    if not admin_dsn:
        pytest.skip("POSTGRES_SCOPE_ADMIN_DSN is required for the permission contract")

    prefix = _prefix("c6perm")
    role = "test_c6_denied_" + uuid4().hex[:12]
    role_password = token_urlsafe(32)
    admin_backend = _backend(postgres_dsn)
    with psycopg2.connect(admin_dsn, connect_timeout=3) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {role} LOGIN PASSWORD %s "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(
                    role=sql.Identifier(role)
                ),
                (role_password,),
            )
            cursor.execute(
                sql.SQL("GRANT pg_monitor TO {role}").format(
                    role=sql.Identifier(role)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA public FROM {role}").format(
                    role=sql.Identifier(role)
                )
            )

    restricted_dsn = extensions.make_dsn(
        admin_dsn,
        user=role,
        password=role_password,
    )
    restricted_backend = _backend(restricted_dsn)
    restricted_identity = restricted_backend.inspect_identity()
    approval = replace(
        _approval(prefix),
        approved_target_fingerprint=restricted_identity.fingerprint,
    )
    try:
        with pytest.raises(PostgresPermissionDenied):
            with OwnedPostgresScope(restricted_backend).open(approval):
                pytest.fail("permission-denied target must not yield an owned scope")
        with admin_backend._connection() as connection:
            with connection.cursor() as cursor:
                assert admin_backend._relations(cursor, prefix) == []
    finally:
        with psycopg2.connect(admin_dsn, connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP OWNED BY {role}").format(
                        role=sql.Identifier(role)
                    )
                )
                cursor.execute(
                    sql.SQL("DROP ROLE {role}").format(
                        role=sql.Identifier(role)
                    )
                )


def test_stage43b_cli_binds_real_cleanup_receipt_to_evidence(
    monkeypatch,
    tmp_path,
):
    class SyntheticAcceptance:
        def setup(self):
            return None

        def run_check(self, name):
            return {"status": "PASS", "check": name}

        def cleanup(self):
            return None

    secret = b"s" * 32
    output = tmp_path / "stage43b-real-scope-evidence.json"
    prefix = _prefix("s43b")
    monkeypatch.setattr(
        stage43b,
        "PostgresCeleryAcceptance",
        lambda **_kwargs: SyntheticAcceptance(),
    )
    monkeypatch.setenv("EVIDENCE_REVISION", "c6a43be")
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "c6-stage43b-key")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )
    monkeypatch.setenv(
        "POSTGRES_ACCEPTANCE_APPROVAL_ID",
        os.environ["POSTGRES_TEST_APPROVAL_ID"],
    )
    monkeypatch.setenv(
        "POSTGRES_ACCEPTANCE_APPROVAL_RECEIPT_SHA256",
        os.environ["POSTGRES_TEST_APPROVAL_RECEIPT_SHA256"],
    )
    monkeypatch.setenv(
        "POSTGRES_ACCEPTANCE_APPROVED_FINGERPRINT",
        os.environ["POSTGRES_TEST_APPROVED_FINGERPRINT"],
    )
    monkeypatch.setenv(
        "POSTGRES_ACCEPTANCE_DATABASE_ALLOWLIST",
        os.environ["POSTGRES_TEST_DATABASE_ALLOWLIST"],
    )
    monkeypatch.setenv(
        "POSTGRES_ACCEPTANCE_APPROVAL_EXPIRES_AT",
        os.environ["POSTGRES_TEST_APPROVAL_EXPIRES_AT"],
    )

    assert stage43b.main(
        [
            "--synthetic",
            "--table-prefix",
            prefix,
            "--output",
            str(output),
        ]
    ) == 0

    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=HmacReceiptSigner(
            key_id="c6-stage43b-key",
            secret=secret,
        ),
    ).verify(
        json.loads(output.read_text(encoding="utf-8")),
        expected_revision="c6a43be",
        expected_scope="stage43b.recovery.acceptance",
    )
    assert verified.payload.cleanup_completed is True
    assert verified.payload.cleanup_ownership_verified is True
    assert verified.payload.cleanup_target_verified is True
    assert verified.payload.cleanup_residue_count == 0
    assert verified.payload.cleanup_receipt_sha256 is not None
    assert verified.payload.target_fingerprint == os.environ[
        "POSTGRES_ACCEPTANCE_APPROVED_FINGERPRINT"
    ]
