from __future__ import annotations

from dataclasses import dataclass

from app.a2a.adapters import register_default_a2a_adapters
from app.a2a.client import InProcessA2AClient
from app.a2a.invocation.a2a import A2AAgentInvoker
from app.a2a.invocation.local import LocalAgentInvoker
from app.a2a.observability import AgentTaskLog
from app.a2a.server import LocalA2AServer


@dataclass(frozen=True)
class A2ARuntime:
    server: LocalA2AServer
    client: InProcessA2AClient
    invoker: A2AAgentInvoker
    local_invoker: LocalAgentInvoker
    observability: AgentTaskLog


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
    return A2ARuntime(
        server=server,
        client=client,
        invoker=A2AAgentInvoker(client=client),
        local_invoker=LocalAgentInvoker(),
        observability=observability,
    )
