from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import app.services.runtime as runtime


class FakeControlStore:
    def __init__(self, table_prefix):
        self.table_prefix = table_prefix


def setup_function():
    runtime.reset_runtime_for_tests()


def teardown_function():
    runtime.reset_runtime_for_tests()


def test_same_table_prefix_registers_one_postgres_recorder():
    first_store = FakeControlStore("runtime_agent")
    second_store = FakeControlStore("runtime_agent")

    first = runtime.get_agent_execution_runner(control_store=first_store)
    second = runtime.get_agent_execution_runner(control_store=second_store)

    assert first is second
    assert len(runtime._agent_composite_recorder._recorders) == 2
    postgres_recorders = [
        recorder
        for recorder in runtime._agent_composite_recorder._recorders
        if recorder.__class__.__name__ == "PostgresAgentRunRecorder"
    ]
    assert len(postgres_recorders) == 1
    assert postgres_recorders[0].control_store is first_store


def test_distinct_table_prefixes_register_distinct_postgres_recorders():
    runtime.get_agent_execution_runner(
        control_store=FakeControlStore("runtime_agent_a")
    )
    runtime.get_agent_execution_runner(
        control_store=FakeControlStore("runtime_agent_b")
    )

    postgres_recorders = [
        recorder
        for recorder in runtime._agent_composite_recorder._recorders
        if recorder.__class__.__name__ == "PostgresAgentRunRecorder"
    ]
    assert len(postgres_recorders) == 2


def test_concurrent_first_access_returns_one_runner_and_composite():
    workers = 8
    barrier = Barrier(workers)

    def resolve():
        barrier.wait()
        return (
            runtime.get_agent_execution_runner(),
            runtime._agent_composite_recorder,
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda _index: resolve(), range(workers)))

    assert len({id(runner) for runner, _composite in results}) == 1
    assert len({id(composite) for _runner, composite in results}) == 1


def test_reset_clears_registered_prefixes():
    runtime.get_agent_execution_runner(
        control_store=FakeControlStore("runtime_agent")
    )

    runtime.reset_runtime_for_tests()
    runtime.get_agent_execution_runner(
        control_store=FakeControlStore("runtime_agent")
    )

    assert len(runtime._agent_composite_recorder._recorders) == 2
