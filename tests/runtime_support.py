from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from api.admission import AdmissionSettings, TaskAdmission
from api.monitor import manager
from api.runtime import AppRuntime
from api.task_service import TaskService
from api.task_store import TaskStore


class EmptySnapshot:
    interrupts = ()


class FakeGraph:
    async def aget_state(self, config):
        return EmptySnapshot()


class FakeAgentRuntime:
    def __init__(self, runner=None):
        self.graph = FakeGraph()
        self._runner = runner

    async def run(self, query, thread_id, *, resume_decisions=None):
        if self._runner is not None:
            return await self._runner(
                query,
                thread_id,
                resume_decisions=resume_decisions,
            )
        await asyncio.sleep(0)
        return {"status": "completed", "result": ""}


def make_test_lifespan(
    *,
    runner=None,
    admission_settings: AdmissionSettings | None = None,
    store_kwargs: dict | None = None,
):
    @asynccontextmanager
    async def test_lifespan(app):
        store = TaskStore(":memory:", **(store_kwargs or {}))
        await manager.start(store, asyncio.get_running_loop())
        tasks = TaskService(
            store,
            TaskAdmission(admission_settings) if admission_settings else TaskAdmission.from_env(),
            manager,
        )
        app.state.runtime = AppRuntime(
            agent=FakeAgentRuntime(runner),
            tasks=tasks,
            events=manager,
        )
        try:
            yield
        finally:
            await tasks.shutdown()
            await manager.stop()
            store.close()

    return test_lifespan
