import app.adapters.memory.principal_identity as canonical_identity
import app.adapters.memory.principal_memory_consent as canonical_consent
import app.adapters.memory.principal_memory_control as canonical_control
import app.adapters.memory.question_memory_index as canonical_question_index
from app.ports.principal_identity import PrincipalIdentityResolver
from app.adapters.memory.principal_identity import (
    ExplicitPrincipalIdentityResolver,
    NullPrincipalIdentityResolver,
)


def test_memory_adapter_modules_are_canonical_owners():
    assert canonical_identity.__name__ == "app.adapters.memory.principal_identity"
    assert canonical_consent.__name__ == "app.adapters.memory.principal_memory_consent"
    assert canonical_control.__name__ == "app.adapters.memory.principal_memory_control"
    assert canonical_question_index.__name__ == "app.adapters.memory.question_memory_index"


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
        "app/adapters/memory/principal_identity.py", encoding="utf-8"
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
