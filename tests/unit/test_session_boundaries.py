import app.adapters.memory.session_store as canonical_session
import app.adapters.persistence.postgres.session_store as postgres_session


def test_postgres_session_store_reuses_the_canonical_session_base():
    assert postgres_session.InterviewSessionStore is canonical_session.InterviewSessionStore
    assert (
        postgres_session.PostgresInterviewSessionStore.__mro__[1]
        is canonical_session.InterviewSessionStore
    )


def test_postgres_session_store_selects_postgres_launch_adapters():
    assert postgres_session.PostgresInterviewSessionStore.durability == "postgres"
