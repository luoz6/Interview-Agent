from __future__ import annotations

import pytest

from tests import postgres_support


def test_missing_postgres_dsn_skips(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    postgres_support.reset_postgres_availability_cache()

    with pytest.raises(pytest.skip.Exception, match="not configured"):
        postgres_support.require_postgres_dsn()


def test_configured_unreachable_postgres_dsn_fails(monkeypatch):
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://unreachable/test")
    postgres_support.reset_postgres_availability_cache()
    monkeypatch.setattr(
        "psycopg2.connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("unreachable")),
    )

    with pytest.raises(pytest.fail.Exception, match="configured POSTGRES_DSN is unreachable"):
        postgres_support.require_postgres_dsn()

    postgres_support.reset_postgres_availability_cache()


def test_generated_prefix_is_registered_only_inside_tracking_scope():
    with postgres_support.track_runtime_table_prefixes() as prefixes:
        first = postgres_support.make_runtime_table_prefix("generation")
        second = postgres_support.make_runtime_table_prefix("workflow")

    assert prefixes == {first, second}
    assert all(postgres_support.SAFE_TEST_PREFIX.fullmatch(item) for item in prefixes)


def test_generated_prefix_enters_owned_scope_before_registration():
    events = []

    with postgres_support.track_runtime_table_prefixes(
        scope_opener=lambda prefix: events.append(("opened", prefix))
    ) as prefixes:
        prefix = postgres_support.make_runtime_table_prefix("session")
        events.append(("created", prefix))

    assert events == [("opened", prefix), ("created", prefix)]
    assert prefixes == {prefix}
