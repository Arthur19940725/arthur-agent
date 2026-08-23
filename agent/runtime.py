from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from dotenv import find_dotenv, load_dotenv

from agent.llm import DeepSeekSettings, create_model_bundle
from agent.main_agent import create_main_agent, run_deep_agent
from agent.persistence import open_sqlite_checkpointer


@dataclass(frozen=True)
class AgentRuntime:
    """Owns the initialized agent graph and all external service adapters."""

    graph: Any

    async def run(self, query: str, thread_id: str, *, resume_decisions=None):
        return await run_deep_agent(
            self.graph,
            query,
            thread_id,
            resume_decisions=resume_decisions,
        )


def _enabled(values: Mapping[str, str], *names: str) -> bool:
    return all(bool(values.get(name)) for name in names)


@asynccontextmanager
async def open_agent_runtime(
    environ: Mapping[str, str] | None = None,
) -> AsyncIterator[AgentRuntime]:
    load_dotenv(find_dotenv())
    values = os.environ if environ is None else environ
    models = create_model_bundle(DeepSeekSettings.from_env(values))

    tavily_key = values.get("TAVILY_API_KEY")
    if not tavily_key:
        raise ValueError("missing Tavily configuration: TAVILY_API_KEY")
    from tavily import TavilyClient

    search_client = TavilyClient(api_key=tavily_key)

    ragflow_client = None
    if _enabled(values, "RAGFLOW_API_KEY", "RAGFLOW_API_URL"):
        from ragflow_sdk import RAGFlow

        ragflow_client = RAGFlow(
            api_key=values["RAGFLOW_API_KEY"],
            base_url=values["RAGFLOW_API_URL"],
        )

    database_enabled = _enabled(
        values,
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE",
    )

    async with open_sqlite_checkpointer() as checkpointer:
        yield AgentRuntime(
            create_main_agent(
                checkpointer,
                models=models,
                search_client=search_client,
                ragflow_client=ragflow_client,
                database_enabled=database_enabled,
            )
        )
