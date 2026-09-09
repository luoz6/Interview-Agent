from __future__ import annotations

from dataclasses import dataclass

from app.a2a.adapters import register_default_a2a_adapters
from app.a2a.client import InProcessA2AClient
from app.a2a.invocation.a2a import A2AAgentInvoker
from app.a2a.invocation.local import LocalAgentInvoker
from app.a2a.observability import AgentTaskLog
from app.a2a.server import LocalA2AServer
from app.a2a.registry import AgentRegistry
from app.a2a.cards import (
    EXAMINER_AGENT_CARD,
    KNOWLEDGE_AGENT_CARD,
    REVIEWER_AGENT_CARD,
    REPORT_COACH_AGENT_CARD,
)


@dataclass(frozen=True)
class A2ARuntime:
    server: LocalA2AServer
    client: InProcessA2AClient
    invoker: A2AAgentInvoker
    local_invoker: LocalAgentInvoker
    observability: AgentTaskLog
    registry: AgentRegistry


def build_local_a2a_runtime(
    *,
    llm=None,
    vector_store=None,
    execution_runner=None,
) -> A2ARuntime:
    observability = AgentTaskLog()
    server = LocalA2AServer(observability=observability)
    register_default_a2a_adapters(
        server,
        llm=llm,
        vector_store=vector_store,
        execution_runner=execution_runner,
    )
    client = InProcessA2AClient(server=server)
    local_invoker = LocalAgentInvoker()
    for agent_id, skill in server.registered_skills:
        handler = server.get_handler(agent_id=agent_id, skill=skill)
        if handler is not None:
            local_invoker.register(
                agent_id=agent_id,
                skill=skill,
                handler=handler,
            )
    registry = AgentRegistry()
    for card in (
        EXAMINER_AGENT_CARD,
        KNOWLEDGE_AGENT_CARD,
        REVIEWER_AGENT_CARD,
        REPORT_COACH_AGENT_CARD,
    ):
        registry.register_card(card)
    validation_errors = registry.validate(server)
    if validation_errors:
        raise RuntimeError("; ".join(validation_errors))
    return A2ARuntime(
        server=server,
        client=client,
        invoker=A2AAgentInvoker(client=client),
        local_invoker=local_invoker,
        observability=observability,
        registry=registry,
    )
