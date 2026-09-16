import importlib

import app.runtime.interview_workflow as canonical_workflow


def _module(name):
    return importlib.import_module(name)


def test_legacy_workflow_modules_preserve_canonical_module_identity():
    assert (
        _module("app.runtime.principal_memory_tasks").PrincipalMemoryProposalProcessor
        is _module("app.application.memory.proposals").PrincipalMemoryProposalProcessor
    )


def test_workflow_purge_uses_the_injected_checkpointer_runtime():
    deleted_threads = []

    class Checkpointer:
        def delete_thread(self, session_id):
            deleted_threads.append(session_id)

    class WorkflowStore:
        @staticmethod
        def delete_session_control_rows(session_id):
            assert session_id == "session-1"
            return 2

    class GenerationStore:
        @staticmethod
        def delete_session_rows(session_id):
            assert session_id == "session-1"
            return 3

    workflow = canonical_workflow.InterviewWorkflowService(
        legacy_store=object(),
        workflow_store=WorkflowStore(),
        generation_store=GenerationStore(),
        graph_registry=object(),
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
        default_graph_version="langgraph-v3",
        checkpointer_runtime_getter=lambda: Checkpointer(),
    )

    assert workflow.purge_session("session-1") == {
        "workflow_control_rows": 2,
        "generation_rows": 3,
    }
    assert deleted_threads == ["session-1"]
