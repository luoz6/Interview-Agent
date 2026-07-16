import app.services.runtime as runtime


class FakeService:
    def __init__(self):
        self.starts = 0
        self.shutdowns = []

    def start(self):
        self.starts += 1

    def shutdown(self, *, wait=True):
        self.shutdowns.append(wait)


def test_start_runtime_starts_postgres_local_dispatcher(monkeypatch):
    service = FakeService()
    runtime.reset_runtime_for_tests()
    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "postgres")
    monkeypatch.setattr(
        runtime,
        "get_runtime_event_backend",
        lambda: "local",
    )
    monkeypatch.setattr(
        runtime,
        "build_runtime_outbox_service",
        lambda: service,
    )

    runtime.start_runtime()
    runtime.start_runtime()
    runtime.shutdown_runtime()

    assert service.starts == 1
    assert service.shutdowns == [True]


def test_start_runtime_does_not_start_memory_dispatcher(monkeypatch):
    runtime.reset_runtime_for_tests()
    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "memory")
    monkeypatch.setattr(
        runtime,
        "get_runtime_event_backend",
        lambda: "local",
    )
    monkeypatch.setattr(
        runtime,
        "build_runtime_outbox_service",
        lambda: (_ for _ in ()).throw(
            AssertionError("memory must not build an outbox service")
        ),
    )

    runtime.start_runtime()
    runtime.shutdown_runtime()
