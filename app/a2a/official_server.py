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

from app.a2a.cards.platform import A2A_PLATFORM_AGENT_CARD
from app.a2a.official import to_official_agent_card


class OfficialA2AAgentExecutor(AgentExecutor):
    def __init__(self, invoker) -> None:
        self.invoker = invoker

    async def execute(self, context, event_queue) -> None:
        text = context.get_user_input().strip()
        payload = json.loads(text or "{}")
        artifact = self.invoker.invoke(
            agent_id=payload["agent_id"],
            skill=payload["skill"],
            request=payload.get("input") or {},
        )
        message = types.Message(
            message_id=f"msg-{context.task_id or context.context_id}",
            context_id=context.context_id or "",
            role=types.Role.ROLE_AGENT,
            parts=[
                types.Part(
                    text=artifact.model_dump_json(),
                )
            ],
        )
        await event_queue.enqueue_event(message)

    async def cancel(self, context, event_queue) -> None:
        return


def install_official_a2a_routes(app, *, invoker) -> None:
    official_card = to_official_agent_card(A2A_PLATFORM_AGENT_CARD)
    task_store = InMemoryTaskStore()
    queue_manager = InMemoryQueueManager()
    handler = LegacyRequestHandler(
        OfficialA2AAgentExecutor(invoker),
        task_store,
        official_card,
        queue_manager,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(official_card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/jsonrpc"),
        rest_routes=create_rest_routes(handler),
    )
