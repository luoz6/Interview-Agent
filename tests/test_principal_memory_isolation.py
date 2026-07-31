from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from tests.test_in_memory_principal_memory import make_fact


def test_same_fact_value_is_isolated_between_principals_and_deletions():
    store = InMemoryPrincipalMemoryFactStore()
    alpha = make_fact(principal_id="principal-a", session_id="session-a")
    beta = make_fact(principal_id="principal-b", session_id="session-b")
    store.create_proposal(alpha)
    store.create_proposal(beta)

    assert store.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-a",
        limit=10,
    ) == [alpha]
    assert store.purge_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-a",
    ) == 1
    assert store.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-b",
        limit=10,
    ) == [beta]


def test_unknown_principal_query_does_not_reveal_fact_existence():
    store = InMemoryPrincipalMemoryFactStore()
    store.create_proposal(make_fact(principal_id="principal-a"))
    assert store.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-unknown",
        limit=10,
    ) == []
