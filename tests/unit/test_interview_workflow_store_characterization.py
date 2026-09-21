from app.adapters.persistence.postgres.interview_workflow_store import (
    PostgresInterviewWorkflowStore,
)


def test_durable_command_payload_digest_binds_type_version_and_exact_answer():
    digest = PostgresInterviewWorkflowStore._payload_sha256(
        "answer",
        7,
        "  exact answer  ",
    )

    assert digest == (
        "736db867c75d8599c3e6fd924e6408863"
        "ef890fc12b53ac2df633f6aab7582d6"
    )
    assert digest != PostgresInterviewWorkflowStore._payload_sha256(
        "answer",
        8,
        "  exact answer  ",
    )
    assert digest != PostgresInterviewWorkflowStore._payload_sha256(
        "answer",
        7,
        "exact answer",
    )
    assert digest != PostgresInterviewWorkflowStore._payload_sha256(
        "skip",
        7,
        None,
    )
