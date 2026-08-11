from __future__ import annotations

import re

import pytest

from app.services.context_artifact_scope import (
    StableContextArtifactPrivacyScopeResolver,
    privacy_scope_sha256,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def test_scope_resolution_is_stable_and_isolates_deployments_and_sessions():
    resolver = StableContextArtifactPrivacyScopeResolver()

    first = resolver.for_interview(
        deployment_scope="production-hk",
        session_id="session-1",
    )
    same = resolver.for_interview(
        deployment_scope="production-hk",
        session_id="session-1",
    )
    other_deployment = resolver.for_interview(
        deployment_scope="production-eu",
        session_id="session-1",
    )
    other_session = resolver.for_interview(
        deployment_scope="production-hk",
        session_id="session-2",
    )

    assert SHA256_RE.fullmatch(privacy_scope_sha256(first))
    assert first == same
    assert len({first, other_deployment, other_session}) == 3


def test_review_scope_excludes_job_and_attempt_identity_by_contract():
    resolver = StableContextArtifactPrivacyScopeResolver()

    assert resolver.for_review(
        deployment_scope="production-hk",
        session_id="session-1",
    ) == resolver.for_review(
        deployment_scope="production-hk",
        session_id="session-1",
    )
    assert "job_id" not in resolver.for_review.__annotations__


def test_prep_scope_does_not_require_a_random_prep_run_id():
    resolver = StableContextArtifactPrivacyScopeResolver()

    deployment_scope = resolver.for_prep(
        deployment_scope="production-hk",
        principal_id=None,
    )
    principal_scope = resolver.for_prep(
        deployment_scope="production-hk",
        principal_id="tenant-1",
    )

    assert SHA256_RE.fullmatch(privacy_scope_sha256(deployment_scope))
    assert principal_scope != deployment_scope


def test_scope_material_is_canonical_but_identity_receives_only_its_digest():
    resolver = StableContextArtifactPrivacyScopeResolver()
    material = resolver.for_interview(
        deployment_scope="production-hk",
        session_id="session-1",
    )

    assert '"deployment_scope":"production-hk"' in material
    assert '"session_id":"session-1"' in material
    assert "session-1" not in privacy_scope_sha256(material)

    with pytest.raises(ValueError, match="canonical JSON"):
        privacy_scope_sha256('{"session_id": "session-1"}')


@pytest.mark.parametrize(
    "unsafe",
    ["", "   ", "https://user:secret@example.com", "Bearer abcdefghijk"],
)
def test_scope_rejects_empty_or_credential_shaped_material(unsafe):
    resolver = StableContextArtifactPrivacyScopeResolver()

    with pytest.raises(ValueError, match="privacy scope"):
        resolver.for_prep(deployment_scope=unsafe, principal_id=None)
