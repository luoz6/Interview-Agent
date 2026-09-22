from __future__ import annotations

from collections.abc import Callable, Iterable
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4

from app.domain.agent_streaming import (
    AgentStreamEvent,
    AgentStreamIdentity,
    CommittedStreamArtifact,
)
from app.domain.report.models import utc_now_iso


class AgentStreamSubscription:
    def __init__(
        self,
        broker: "_AgentStreamBroker",
        *,
        buffer_size: int,
        callback: Callable[[AgentStreamEvent], Any] | None,
    ) -> None:
        if buffer_size < 1:
            raise ValueError("observer buffer size must be positive")
        self.subscription_id = f"observer-{uuid4().hex}"
        self._broker = broker
        self._events: Queue[AgentStreamEvent] = Queue(maxsize=buffer_size)
        self._disconnected = Event()
        if callback is not None:
            Thread(
                target=self._deliver_callback,
                args=(callback,),
                name=f"agent-stream-{self.subscription_id}",
                daemon=True,
            ).start()

    @property
    def disconnected(self) -> bool:
        return self._disconnected.is_set()

    def next_event(self, *, timeout: float | None = None) -> AgentStreamEvent:
        try:
            return self._events.get(timeout=timeout)
        except Empty as exc:
            raise TimeoutError("no stream event available") from exc

    def drain(self) -> list[AgentStreamEvent]:
        events: list[AgentStreamEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except Empty:
                return events

    def close(self) -> None:
        if not self._disconnected.is_set():
            self._disconnected.set()
            self._broker.unsubscribe(self.subscription_id)

    def _offer(self, event: AgentStreamEvent) -> None:
        if self.disconnected:
            return
        try:
            self._events.put_nowait(event)
        except Full:
            self.close()

    def _deliver_callback(
        self,
        callback: Callable[[AgentStreamEvent], Any],
    ) -> None:
        while not self.disconnected:
            try:
                event = self.next_event(timeout=0.1)
            except TimeoutError:
                continue
            try:
                callback(event)
            except Exception:
                self.close()


class _AgentStreamBroker:
    def __init__(self) -> None:
        self._lock = Lock()
        self._subscriptions: dict[str, AgentStreamSubscription] = {}

    def subscribe(
        self,
        *,
        buffer_size: int,
        callback: Callable[[AgentStreamEvent], Any] | None,
    ) -> AgentStreamSubscription:
        subscription = AgentStreamSubscription(
            self,
            buffer_size=buffer_size,
            callback=callback,
        )
        with self._lock:
            self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    def unsubscribe(self, subscription_id: str) -> None:
        with self._lock:
            self._subscriptions.pop(subscription_id, None)

    def publish(self, event: AgentStreamEvent) -> None:
        with self._lock:
            subscriptions = tuple(self._subscriptions.values())
        for subscription in subscriptions:
            subscription._offer(event)


class AgentStreamInvocationWorker:
    """Runtime-owned invocation whose observers cannot cancel its commit."""

    def __init__(
        self,
        *,
        identity: AgentStreamIdentity,
        invoke: Callable[[], Iterable[str]],
        commit: Callable[[str], CommittedStreamArtifact],
        after_completed: Callable[[], Any] | None = None,
        retryable: bool | Callable[[Exception], bool] = False,
    ) -> None:
        self.identity = identity
        self._invoke = invoke
        self._commit = commit
        self._after_completed = after_completed
        self._retryable = retryable
        self._broker = _AgentStreamBroker()
        self._sequence = 0
        self._started = False
        self._start_lock = Lock()
        self._finished = Event()
        self._succeeded = False
        self._error: Exception | None = None
        self._thread: Thread | None = None

    @property
    def stream_id(self) -> str:
        return self.identity.stream_id

    @property
    def succeeded(self) -> bool:
        return self._succeeded

    @property
    def error(self) -> Exception | None:
        return self._error

    def subscribe(
        self,
        *,
        buffer_size: int,
        callback: Callable[[AgentStreamEvent], Any] | None = None,
    ) -> AgentStreamSubscription:
        return self._broker.subscribe(
            buffer_size=buffer_size,
            callback=callback,
        )

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                raise RuntimeError("stream invocation already started")
            self._started = True
            self._thread = Thread(
                target=self._run,
                name=f"agent-stream-{self.identity.task_id}",
                daemon=False,
            )
            self._thread.start()

    def join(self, *, timeout: float | None = None) -> bool:
        if not self._started:
            raise RuntimeError("stream invocation has not started")
        return self._finished.wait(timeout=timeout)

    def _run(self) -> None:
        try:
            self._emit("STARTED")
            chunks: list[str] = []
            for chunk in self._invoke():
                if not isinstance(chunk, str):
                    raise TypeError("Agent stream chunks must be strings")
                if not chunk:
                    continue
                chunks.append(chunk)
                self._emit("DELTA", delta=chunk)
            committed = self._commit("".join(chunks))
            if not isinstance(committed, CommittedStreamArtifact):
                raise TypeError("stream commit returned an invalid result")
            self._emit(
                "COMPLETED",
                final_text=committed.final_text,
                artifact_ref=committed.artifact_ref,
            )
        except Exception as exc:
            self._error = exc
            retryable = (
                self._retryable(exc)
                if callable(self._retryable)
                else self._retryable
            )
            self._emit(
                "FAILED",
                error_code=getattr(exc, "code", type(exc).__name__),
                retryable=bool(retryable),
            )
        else:
            try:
                if self._after_completed is not None:
                    self._after_completed()
            except Exception as exc:
                # COMPLETED is terminal once published. Preserve the boundary
                # failure for the owner without emitting a second terminal event.
                self._error = exc
            else:
                self._succeeded = True
        finally:
            self._finished.set()

    def _emit(self, event_type: str, **payload: Any) -> None:
        self._sequence += 1
        event = AgentStreamEvent(
            event_type=event_type,
            execution_id=self.identity.execution_id,
            task_id=self.identity.task_id,
            logical_attempt=self.identity.logical_attempt,
            agent_id=self.identity.agent_id,
            skill=self.identity.skill,
            stream_id=self.stream_id,
            sequence=self._sequence,
            event_id=f"{self.stream_id}:{self._sequence}",
            question_id=self.identity.question_id,
            emitted_at=utc_now_iso(),
            **payload,
        )
        self._broker.publish(event)


__all__ = ["AgentStreamInvocationWorker", "AgentStreamSubscription"]
