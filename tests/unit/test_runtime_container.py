"""Unit tests for RuntimeContainer ownership and lifecycle state."""

from __future__ import annotations

import pytest

from app.runtime.container import RuntimeContainer, build_runtime_container


def test_container_owns_typed_config_and_lazy_singletons():
    container = build_runtime_container(
        environ={"INTERVIEW_RUNTIME_STORE": "memory"}
    )
    created: list[object] = []

    first = container.get_or_create("store", lambda: _new_object(created))
    second = container.get_or_create("store", lambda: _new_object(created))

    assert first is second
    assert len(created) == 1
    assert container.config.core.runtime_store == "memory"
    assert container.snapshot().config_loaded is True
    assert container.snapshot().instance_keys == ("store",)


def test_container_lifecycle_is_explicit_and_closed_container_cannot_reopen():
    container = build_runtime_container(
        environ={"INTERVIEW_RUNTIME_STORE": "memory"}
    )

    container.mark_open()
    assert container.state == "open"
    assert container.begin_close() is True
    assert container.begin_close() is False
    container.finish_close()

    assert container.state == "closed"
    assert container.snapshot().instance_keys == ()
    with pytest.raises(RuntimeError, match="cannot be reopened"):
        container.mark_open()


def test_container_flags_and_metadata_are_cleared_on_close():
    container = RuntimeContainer()
    identities = container.metadata("identities", set)
    identities.add(("table_prefix", "one"))
    container.set_flag("started", True)
    container.set("resource", object())

    container.begin_close()
    container.finish_close()

    snapshot = container.snapshot()
    assert snapshot.flag_keys == ()
    assert snapshot.instance_keys == ()
    assert snapshot.metadata_keys == ()
    with pytest.raises(RuntimeError, match="closed RuntimeContainer"):
        container.metadata("identities", set)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda container: container.set("resource", object()),
        lambda container: container.get_or_create("resource", object),
        lambda container: container.set_flag("started", True),
        lambda container: container.metadata("lock", object),
        lambda container: container.config,
    ],
)
def test_closed_container_rejects_new_state(mutate):
    container = RuntimeContainer()
    container.begin_close()
    container.finish_close()

    with pytest.raises(RuntimeError, match="closed RuntimeContainer"):
        mutate(container)


def test_closing_container_allows_in_flight_dependency_resolution():
    container = RuntimeContainer()
    container.mark_open()
    assert container.begin_close() is True

    resource = container.get_or_create("resource", object)

    assert container.get("resource") is resource
    container.finish_close()
    assert container.snapshot().instance_keys == ()


def _new_object(created: list[object]) -> object:
    value = object()
    created.append(value)
    return value
