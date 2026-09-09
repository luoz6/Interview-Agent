from datetime import datetime, timedelta, timezone
import json

import pytest

from tests.postgres_v3_checkpointer_support import (
    AUTHORIZED_DATABASE,
    AUTHORIZED_RELATIONS,
    AUTHORIZED_THREAD_ID_PREFIX,
    AuthorizedCheckpointRowScope,
    CheckpointerRecoveryAuthorizationError,
    CheckpointerRecoveryNotEnabled,
    OPT_IN_ENV,
    RECEIPT_PATH_ENV,
    RECEIPT_SCHEMA_VERSION,
    RECEIPT_SHA_ENV,
    canonical_authorization_sha256,
    load_checkpointer_recovery_authorization,
)


NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
TARGET_FINGERPRINT = "5" * 64


def _receipt(**overrides):
    payload = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "approval_id": "postgres-v3-recovery-test",
        "target_fingerprint": TARGET_FINGERPRINT,
        "database": AUTHORIZED_DATABASE,
        "relations": list(AUTHORIZED_RELATIONS),
        "thread_id_prefix": AUTHORIZED_THREAD_ID_PREFIX,
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    payload.update(overrides)
    return payload


def _environment(tmp_path, payload):
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return {
        OPT_IN_ENV: "1",
        RECEIPT_PATH_ENV: str(path),
        RECEIPT_SHA_ENV: canonical_authorization_sha256(payload),
    }


def test_checkpointer_recovery_without_opt_in_is_disabled():
    with pytest.raises(CheckpointerRecoveryNotEnabled):
        load_checkpointer_recovery_authorization(
            {},
            current_target_fingerprint=TARGET_FINGERPRINT,
            current_database=AUTHORIZED_DATABASE,
            now=NOW,
        )


def test_checkpointer_recovery_opt_in_requires_receipt_fields():
    with pytest.raises(
        CheckpointerRecoveryAuthorizationError,
        match=RECEIPT_PATH_ENV,
    ):
        load_checkpointer_recovery_authorization(
            {OPT_IN_ENV: "1"},
            current_target_fingerprint=TARGET_FINGERPRINT,
            current_database=AUTHORIZED_DATABASE,
            now=NOW,
        )


def test_checkpointer_recovery_receipt_is_bound_by_canonical_hash(tmp_path):
    payload = _receipt()
    environment = _environment(tmp_path, payload)

    receipt = load_checkpointer_recovery_authorization(
        environment,
        current_target_fingerprint=TARGET_FINGERPRINT,
        current_database=AUTHORIZED_DATABASE,
        now=NOW,
    )

    assert receipt.approval_id == "postgres-v3-recovery-test"
    environment[RECEIPT_SHA_ENV] = "0" * 64
    with pytest.raises(
        CheckpointerRecoveryAuthorizationError,
        match="hash mismatch",
    ):
        load_checkpointer_recovery_authorization(
            environment,
            current_target_fingerprint=TARGET_FINGERPRINT,
            current_database=AUTHORIZED_DATABASE,
            now=NOW,
        )


def test_checkpointer_recovery_rejects_receipt_prefix_drift(tmp_path):
    payload = _receipt(thread_id_prefix="test_other_")

    with pytest.raises(
        CheckpointerRecoveryAuthorizationError,
        match="thread_id_prefix",
    ):
        load_checkpointer_recovery_authorization(
            _environment(tmp_path, payload),
            current_target_fingerprint=TARGET_FINGERPRINT,
            current_database=AUTHORIZED_DATABASE,
            now=NOW,
        )


def test_checkpoint_row_scope_rejects_nonisolated_thread_id(tmp_path):
    receipt = load_checkpointer_recovery_authorization(
        _environment(tmp_path, _receipt()),
        current_target_fingerprint=TARGET_FINGERPRINT,
        current_database=AUTHORIZED_DATABASE,
        now=NOW,
    )
    scope = AuthorizedCheckpointRowScope(receipt)

    class NoConnection:
        def connection(self):
            raise AssertionError("invalid thread must fail before PostgreSQL")

    with pytest.raises(
        CheckpointerRecoveryAuthorizationError,
        match="isolated test identity",
    ):
        scope.inventory_for_write(
            NoConnection(), "test_jit_v3_not-random-hex"
        )
