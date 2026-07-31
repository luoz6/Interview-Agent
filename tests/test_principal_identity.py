from app.ports.principal_identity import PrincipalIdentityResolver
from app.services.principal_identity import (
    ExplicitPrincipalIdentityResolver,
    NullPrincipalIdentityResolver,
)


def test_default_identity_is_unavailable_and_explicit_identity_is_not_inferred():
    assert NullPrincipalIdentityResolver().resolve() is None
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="principal-explicit",
        assurance="test",
    )

    identity = resolver.resolve()

    assert identity.principal_id == "principal-explicit"
    assert not hasattr(resolver, "resume_hash")
    assert isinstance(resolver, PrincipalIdentityResolver)


def test_identity_source_contains_no_automatic_merge_inputs():
    source = open(
        "app/services/principal_identity.py", encoding="utf-8"
    ).read().casefold()
    for forbidden in (
        "resume_hash",
        "email",
        "phone",
        "localstorage",
        "user-agent",
        "embedding",
        "candidate_name",
    ):
        assert forbidden not in source
