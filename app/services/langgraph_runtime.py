from __future__ import annotations

import os
from contextlib import AbstractContextManager
from typing import Any, Callable


class PostgresCheckpointerRuntime:
    def __init__(
        self,
        dsn: str,
        *,
        saver_factory: Callable[[str], AbstractContextManager] | None = None,
    ) -> None:
        self.dsn = dsn
        self._factory = saver_factory or self._default_saver_factory
        self._context: AbstractContextManager | None = None
        self._saver: Any | None = None

    def start(self):
        if self._saver is not None:
            return self._saver
        os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
        self._context = self._factory(self.dsn)
        self._saver = self._context.__enter__()
        self._saver.setup()
        return self._saver

    @property
    def saver(self):
        if self._saver is None:
            raise RuntimeError("LangGraph checkpointer is not started")
        return self._saver

    def delete_thread(self, session_id: str) -> None:
        self.saver.delete_thread(session_id)

    def shutdown(self) -> None:
        context, self._context = self._context, None
        self._saver = None
        if context is not None:
            context.__exit__(None, None, None)

    @staticmethod
    def _default_saver_factory(dsn: str):
        from langgraph.checkpoint.postgres import PostgresSaver

        return PostgresSaver.from_conn_string(dsn)


class VersionedGraphRegistry:
    def __init__(self) -> None:
        self._graphs: dict[str, Any] = {}

    def register(self, graph_schema_version: str, graph: Any) -> None:
        if graph_schema_version in self._graphs:
            raise ValueError(
                f"graph already registered: {graph_schema_version}"
            )
        self._graphs[graph_schema_version] = graph

    def get(self, graph_schema_version: str):
        try:
            return self._graphs[graph_schema_version]
        except KeyError as exc:
            raise ValueError(
                f"unsupported graph version: {graph_schema_version}"
            ) from exc


# Kept while interview callers migrate to the generic registry name.
VersionedInterviewGraphRegistry = VersionedGraphRegistry
