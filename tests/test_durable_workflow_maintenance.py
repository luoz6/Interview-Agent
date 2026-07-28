from app.services.durable_workflow_maintenance import (
    DurableWorkflowMaintenanceService,
    MaintenanceResult,
)


class WorkflowStore:
    def __init__(self, result=2, error=None):
        self.result = result
        self.error = error
        self.hours = []

    def clear_applied_command_payloads_older_than(self, *, hours):
        self.hours.append(hours)
        if self.error:
            raise self.error
        return self.result


class GenerationStore:
    def __init__(self, result=3):
        self.result = result
        self.hours = []

    def cleanup_completed_chunks_older_than(self, *, hours):
        self.hours.append(hours)
        return self.result


class SignalStore:
    def __init__(self, result=4):
        self.result = result
        self.hours = []

    def cleanup_older_than(self, *, hours):
        self.hours.append(hours)
        return self.result


def make_service(workflow=None, generations=None, signals=None):
    return DurableWorkflowMaintenanceService(
        workflow_store=workflow or WorkflowStore(),
        generation_store=generations or GenerationStore(),
        signal_store=signals,
        retention_hours=24,
        signal_retention_hours=168,
        interval_seconds=3600,
    )


def test_run_once_uses_bounded_database_retention_methods():
    workflow = WorkflowStore()
    generations = GenerationStore()
    signals = SignalStore()
    service = make_service(workflow, generations, signals)

    result = service.run_once()

    assert result == MaintenanceResult(
        cleared_command_payloads=2,
        deleted_generation_chunks=3,
        deleted_runtime_signal_buckets=4,
    )
    assert workflow.hours == [24]
    assert generations.hours == [24]
    assert signals.hours == [168]
    assert service.last_error_code is None


def test_failed_pass_is_retryable_without_logging_private_detail(caplog):
    workflow = WorkflowStore(error=RuntimeError("private database detail"))
    service = make_service(workflow, GenerationStore())

    assert service.run_once() is None
    assert service.last_error_code == "durable_maintenance_failed"
    assert "private database detail" not in caplog.text

    workflow.error = None
    assert service.run_once() is not None
    assert service.last_error_code is None


def test_start_is_idempotent_and_shutdown_joins():
    service = make_service()

    service.start()
    thread = service._thread
    service.start()
    service.shutdown(wait=True)

    assert thread is service._thread
    assert thread is not None
    assert not thread.is_alive()


def test_invalid_maintenance_bounds_fail_closed():
    for kwargs in (
        {"retention_hours": 0, "interval_seconds": 1},
        {"retention_hours": 1, "interval_seconds": 0},
    ):
        try:
            DurableWorkflowMaintenanceService(
                workflow_store=WorkflowStore(),
                generation_store=GenerationStore(),
                **kwargs,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid maintenance bound was accepted")
