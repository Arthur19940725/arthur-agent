from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from api.context import get_thread_context
from api.task_store import TaskStore

logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket transport Adapter; TaskStore remains the event source of truth."""

    def __init__(self, *, queue_size: int = 256) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self.active_connections: dict[str, WebSocket] = {}
        self.store: TaskStore | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._queue_size = queue_size
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] | None = None
        self._worker: asyncio.Task | None = None
        self.live_delivery_drops = 0

    async def start(self, store: TaskStore, loop: asyncio.AbstractEventLoop) -> None:
        if self._worker is not None:
            raise RuntimeError("connection manager already started")
        self.store = store
        self.loop = loop
        self._queue = asyncio.Queue(maxsize=self._queue_size)
        self._worker = asyncio.create_task(self._deliver_queued())

    async def stop(self) -> None:
        if self._worker is None:
            return
        await self.drain()
        self._worker.cancel()
        await asyncio.gather(self._worker, return_exceptions=True)
        self._worker = None
        self._queue = None
        self.loop = None
        self.store = None
        self.active_connections.clear()

    async def drain(self) -> None:
        if self._queue is not None:
            await self._queue.join()

    async def publish(self, thread_id: str, message: dict[str, Any]) -> dict[str, Any]:
        stored = self._record(thread_id, message)
        await self._deliver(thread_id, stored)
        return stored

    def publish_from_context(
        self,
        message: dict[str, Any],
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any] | None:
        target = thread_id or get_thread_context()
        if target is None or self.store is None or self.loop is None or self._queue is None:
            return None
        stored = self._record(target, message)
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self.loop:
            self._enqueue(target, stored)
        else:
            self.loop.call_soon_threadsafe(self._enqueue, target, stored)
        return stored

    def get_task(self, thread_id: str) -> dict[str, Any]:
        if self.store is None:
            raise RuntimeError("connection manager is not started")
        return self.store.get_task(thread_id)

    async def register_authenticated(self, websocket: WebSocket, thread_id: str) -> None:
        if self.store is None:
            raise RuntimeError("connection manager is not started")
        self.active_connections[thread_id] = websocket
        for event in self.store.replay(thread_id):
            await websocket.send_json(event)

    def disconnect(self, websocket: WebSocket, thread_id: str) -> None:
        if self.active_connections.get(thread_id) is websocket:
            del self.active_connections[thread_id]

    def _record(self, thread_id: str, message: dict[str, Any]) -> dict[str, Any]:
        if self.store is None:
            raise RuntimeError("connection manager is not started")
        return self.store.append_event(thread_id, message)

    def _enqueue(self, thread_id: str, event: dict[str, Any]) -> None:
        if self._queue is None:
            return
        try:
            self._queue.put_nowait((thread_id, event))
        except asyncio.QueueFull:
            self.live_delivery_drops += 1
            logger.warning(
                "live event queue full; event remains available for replay",
                extra={"thread_id": thread_id, "seq": event.get("seq")},
            )

    async def _deliver_queued(self) -> None:
        assert self._queue is not None
        while True:
            thread_id, event = await self._queue.get()
            try:
                await self._deliver(thread_id, event)
            finally:
                self._queue.task_done()

    async def _deliver(self, thread_id: str, event: dict[str, Any]) -> None:
        websocket = self.active_connections.get(thread_id)
        if websocket is None:
            return
        try:
            await websocket.send_json(event)
        except Exception:
            self.disconnect(websocket, thread_id)
            logger.warning(
                "websocket delivery failed; event remains available for replay",
                extra={"thread_id": thread_id, "seq": event.get("seq")},
            )


manager = ConnectionManager()


class ToolMonitor:
    def _emit(self, event_type: str, message: str, data: dict[str, Any] | None = None) -> None:
        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": message,
            "data": data or {},
        }
        manager.publish_from_context(payload)
        logger.info("agent event %s: %s", event_type, message)

    def report_tool(self, tool_name: str, args: dict[str, Any] | None = None) -> None:
        self._emit(
            "tool_start", f"开始执行工具: {tool_name}", {"tool_name": tool_name, "args": args or {}}
        )

    def report_assistant(self, assistant_name: str, args: dict[str, Any] | None = None) -> None:
        self._emit(
            "assistant_call",
            f"正在调用助手: {assistant_name}",
            {"assistant_name": assistant_name, "args": args or {}},
        )

    def report_task_result(self, result: str) -> None:
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_session_dir(self, thread_id: str) -> None:
        self._emit("session_created", "会话工作目录已准备", {"thread_id": thread_id})

    def report_waiting_for_approval(self, approvals: list[dict]) -> None:
        self._emit(
            "waiting_for_approval",
            "敏感操作等待人工确认",
            {"approvals": approvals},
        )


monitor = ToolMonitor()
