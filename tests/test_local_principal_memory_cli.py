from __future__ import annotations

from argparse import Namespace

from scripts import local_principal_memory


class Service:
    def __init__(self, *, ready=True):
        self.ready = ready

    def status(self):
        return {
            "schema_version": "principal-memory-local-operations-v1",
            "local_consume_ready": self.ready,
            "gate_codes": [] if self.ready else ["DURABLE_METRICS_INCOMPLETE"],
        }

    def cleanup(self, *, batch_size):
        return {"status": "completed", "facts_expired": batch_size}

    def require_maintenance_boundary(self):
        return None


def test_preflight_exit_code_tracks_readiness(monkeypatch):
    monkeypatch.setattr(local_principal_memory, "_service", lambda: Service())
    payload, code = local_principal_memory.execute(Namespace(command="preflight"))
    assert code == 0
    assert payload["local_consume_ready"] is True

    monkeypatch.setattr(
        local_principal_memory, "_service", lambda: Service(ready=False)
    )
    payload, code = local_principal_memory.execute(Namespace(command="preflight"))
    assert code == 1
    assert payload["gate_codes"] == ["DURABLE_METRICS_INCOMPLETE"]


def test_mutating_commands_require_explicit_execute(monkeypatch):
    monkeypatch.setattr(
        local_principal_memory,
        "_service",
        lambda: (_ for _ in ()).throw(AssertionError("must not build runtime")),
    )
    payload, code = local_principal_memory.execute(
        Namespace(command="cleanup", execute=False, batch_size=200)
    )
    assert code == 1
    assert payload["gate_codes"] == ["EXECUTION_NOT_AUTHORIZED"]


def test_cleanup_outputs_aggregate_counts(monkeypatch):
    monkeypatch.setattr(local_principal_memory, "_service", lambda: Service())
    payload, code = local_principal_memory.execute(
        Namespace(command="cleanup", execute=True, batch_size=17)
    )
    assert code == 0
    assert payload == {"status": "completed", "facts_expired": 17}


def test_replay_checks_maintenance_boundary_before_reading_ledger(monkeypatch):
    calls = []

    class Blocked(Service):
        def require_maintenance_boundary(self):
            calls.append("boundary")
            raise RuntimeError("POSTGRES_RUNTIME_REQUIRED")

    monkeypatch.setattr(local_principal_memory, "_service", lambda: Blocked())
    payload = Namespace(
        command="replay-tombstones",
        execute=True,
        ledger="private-ledger.jsonl",
    )
    try:
        local_principal_memory.execute(payload)
    except RuntimeError as exc:
        assert str(exc) == "POSTGRES_RUNTIME_REQUIRED"
    else:
        raise AssertionError("replay must fail before reading the ledger")
    assert calls == ["boundary"]


def test_main_redacts_private_operation_failure(monkeypatch, capsys):
    class Failing(Service):
        def cleanup(self, *, batch_size):
            raise RuntimeError(
                "postgresql://operator:secret@127.0.0.1/private local-owner"
            )

    monkeypatch.setattr(local_principal_memory, "_service", lambda: Failing())

    assert local_principal_memory.main(["cleanup", "--execute"]) == 1

    output = capsys.readouterr().out
    assert "OPERATION_FAILED" in output
    assert "postgresql://" not in output
    assert "secret" not in output
    assert "local-owner" not in output
