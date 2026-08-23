from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from api.admission import TaskAdmission
from api.monitor import ConnectionManager
from api.task_store import TaskStore

logger = logging.getLogger(__name__)
Runner = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    users: int = 0


class TaskService:
    """Owns task state transitions, admission leases, locks, and background jobs."""

    def __init__(
        self,
        store: TaskStore,
        admission: TaskAdmission,
        events: ConnectionManager,
    ) -> None:
        self.store = store
        self.admission = admission
        self.events = events
        self.background_tasks: set[asyncio.Task] = set()
        self._locks: dict[str, _LockEntry] = {}
        self._locks_guard = asyncio.Lock()

    @property
    def lock_count(self) -> int:
        return len(self._locks)

    def ensure_owner(self, thread_id: str, owner: str) -> None:
        self.store.ensure_owner(thread_id, owner)

    def claim(self, thread_id: str, owner: str) -> None:
        self.store.claim_thread(thread_id, owner)

    def get(self, thread_id: str, owner: str) -> dict[str, Any]:
        self.ensure_owner(thread_id, owner)
        return self.store.get_task(thread_id)

    def approval_consumed(self, thread_id: str, approval_id: str) -> bool:
        return self.store.approval_consumed(thread_id, approval_id)

    def consume_approval(self, thread_id: str, approval_id: str) -> bool:
        return self.store.consume_approval(thread_id, approval_id)

    def schedule(
        self,
        *,
        owner: str,
        thread_id: str,
        query: str,
        runner: Runner,
        resume_decisions=None,
        approval_id: str | None = None,
    ) -> asyncio.Task:
        lease = self.admission.acquire(owner, thread_id)
        try:
            if resume_decisions is None:
                self.store.create_task(thread_id, owner, query)
            else:
                self.store.ensure_owner(thread_id, owner)
                if approval_id is None:
                    raise ValueError("approval_id is required when resuming a task")
                if not self.store.consume_approval(thread_id, approval_id):
                    raise ValueError("approval already processed")
                self.store.set_status(thread_id, "started", approvals=[])
            task = asyncio.create_task(
                self._run(
                    lease=lease,
                    thread_id=thread_id,
                    query=query,
                    runner=runner,
                    resume_decisions=resume_decisions,
                )
            )
        except Exception:
            lease.release()
            raise
        self.background_tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    @asynccontextmanager
    async def locked(self, thread_id: str):
        async with self._locks_guard:
            entry = self._locks.setdefault(thread_id, _LockEntry(asyncio.Lock()))
            entry.users += 1
        await entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            async with self._locks_guard:
                entry.users -= 1
                if entry.users == 0 and not entry.lock.locked():
                    self._locks.pop(thread_id, None)

    async def shutdown(self) -> None:
        tasks = list(self.background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.background_tasks.clear()

    async def _run(
        self,
        *,
        lease,
        thread_id: str,
        query: str,
        runner: Runner,
        resume_decisions,
    ) -> dict[str, Any]:
        try:
            async with self.locked(thread_id):
                self.store.set_status(thread_id, "running")
                if resume_decisions is not None:
                    event_name = (
                        "approval_rejected"
                        if any(decision.get("type") == "reject" for decision in resume_decisions)
                        else "approval_resumed"
                    )
                    await self.events.publish(
                        thread_id,
                        {
                            "type": "monitor_event",
                            "event": event_name,
                            "message": "审批决定已提交，任务继续执行",
                            "data": {},
                        },
                    )
                result = await runner(
                    query,
                    thread_id,
                    resume_decisions=resume_decisions,
                )
                status = result.get("status")
                if status == "completed":
                    self.store.set_status(
                        thread_id,
                        "completed",
                        result=str(result.get("result", "")),
                        approvals=[],
                    )
                elif status == "waiting_for_approval":
                    self.store.set_status(
                        thread_id,
                        "waiting_for_approval",
                        approvals=result.get("approvals", []),
                    )
                elif status == "error":
                    self.store.set_status(
                        thread_id,
                        "error",
                        result=str(result.get("result", "")),
                        approvals=[],
                    )
                return result
        except asyncio.CancelledError:
            self.store.set_status(
                thread_id,
                "interrupted",
                result="task interrupted by service shutdown",
            )
            raise
        except Exception as exc:
            await self.events.publish(
                thread_id,
                {
                    "type": "monitor_event",
                    "event": "error",
                    "message": "任务执行失败",
                    "data": {"error_type": type(exc).__name__},
                },
            )
            raise
        finally:
            lease.release()

    def _finished(self, task: asyncio.Task) -> None:
        self.background_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("background agent task failed: %s", type(error).__name__)
