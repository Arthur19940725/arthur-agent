from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from agent.runtime import AgentRuntime, open_agent_runtime
from api.admission import TaskAdmission
from api.monitor import ConnectionManager, manager
from api.task_service import TaskService
from api.task_store import TaskStore


@dataclass(frozen=True)
class AppRuntime:
    agent: AgentRuntime
    tasks: TaskService
    events: ConnectionManager


@asynccontextmanager
async def open_app_runtime(project_root: Path) -> AsyncIterator[AppRuntime]:
    store = TaskStore.from_env(project_root)
    store.reconcile_incomplete()
    await manager.start(store, asyncio.get_running_loop())
    tasks = TaskService(store, TaskAdmission.from_env(), manager)
    try:
        async with open_agent_runtime() as agent:
            yield AppRuntime(agent=agent, tasks=tasks, events=manager)
    finally:
        await tasks.shutdown()
        await manager.stop()
        store.cleanup()
        store.close()
