from __future__ import annotations

import json
from typing import Any

from a2a import types
from a2a.server.agent_execution import AgentExecutor
from a2a.server.events import InMemoryQueueManager
from a2a.server.request_handlers import LegacyRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater

from app.a2a.cards.platform import A2A_PLATFORM_AGENT_CARD
from app.a2a.cards import (
    EXAMINER_AGENT_CARD,
    KNOWLEDGE_AGENT_CARD,
    REVIEWER_AGENT_CARD,
    REPORT_COACH_AGENT_CARD,
)
from app.a2a.invocation.context import InvocationContext
from app.a2a.official import to_official_agent_card


class OfficialA2AAgentExecutor(AgentExecutor):
    def __init__(self, *, invoker, agent_id: str) -> None:
        self.invoker = invoker
        self.agent_id = agent_id

    async def execute(self, context, event_queue) -> None:
        text = context.get_user_input().strip()
        payload = json.loads(text or "{}")
        invocation_context = InvocationContext(
            context_id=context.context_id,
            correlation_id=context.metadata.get("correlation_id") or context.context_id,
            causation_id=context.metadata.get("causation_id"),
            parent_run_id=context.metadata.get("parent_run_id"),
            command_id=context.metadata.get("command_id"),
            idempotency_key=context.metadata.get("idempotency_key"),
        )
        updater = TaskUpdater(
            event_queue,
            task_id=context.task_id,
            context_id=context.context_id,
        )
        await updater.start_work()
        artifact = self.invoker.invoke(
            agent_id=self.agent_id,
            skill=payload["skill"],
            request=payload.get("input") or {},
            invocation_context=invocation_context,
        )
        await updater.add_artifact(
            parts=[types.Part(text=artifact.model_dump_json())],
            name=artifact.artifact_type,
            metadata={
                "domain_artifact_type": artifact.artifact_type,
                "domain_schema_version": artifact.schema_version,
            },
        )
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:
        return


def install_official_a2a_routes(app, *, invoker) -> None:
    official_card = to_official_agent_card(A2A_PLATFORM_AGENT_CARD)
    rest_routes = []
    jsonrpc_routes = []
    for card in (
        EXAMINER_AGENT_CARD,
        KNOWLEDGE_AGENT_CARD,
        REVIEWER_AGENT_CARD,
        REPORT_COACH_AGENT_CARD,
    ):
        agent_card = to_official_agent_card(card)
        task_store = InMemoryTaskStore()
        queue_manager = InMemoryQueueManager()
        handler = LegacyRequestHandler(
            OfficialA2AAgentExecutor(
                invoker=invoker,
                agent_id=card.agent_id,
            ),
            task_store,
            agent_card,
            queue_manager,
        )
        rest_routes.extend(
            create_rest_routes(
                handler,
                path_prefix=f"/a2a/{card.agent_id}",
            )
        )
        jsonrpc_routes.extend(
            create_jsonrpc_routes(
                handler,
                rpc_url=f"/a2a/{card.agent_id}/jsonrpc",
            )
        )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(official_card),
        jsonrpc_routes=jsonrpc_routes,
        rest_routes=rest_routes,
    )
