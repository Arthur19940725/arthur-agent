from __future__ import annotations

from typing import Any, Literal, Protocol

from langchain_core.tools import tool

from api.monitor import monitor
from tools.outcomes import ToolFailure, expose_failures


class SearchClient(Protocol):
    def search(self, **kwargs) -> Any: ...


def create_internet_search(client: SearchClient):
    @tool
    def internet_search(
        query: str,
        topic: Literal["news", "finance", "general"] = "general",
        max_results: int = 5,
        include_raw_content: bool = False,
    ):
        """搜索公开网络信息；数据库和内部知识库查询不要使用此工具。"""
        monitor.report_tool(
            "网络搜索工具",
            {
                "query": query,
                "topic": topic,
                "max_results": max_results,
                "include_raw_content": include_raw_content,
            },
        )
        try:
            return client.search(
                query=query,
                topic=topic,
                max_results=max_results,
                include_raw_content=include_raw_content,
            )
        except TimeoutError:
            raise ToolFailure(
                "upstream_timeout",
                "网络搜索服务响应超时",
                retryable=True,
            ) from None
        except (ConnectionError, OSError):
            raise ToolFailure(
                "upstream_unavailable",
                "网络搜索服务暂不可用",
                retryable=True,
            ) from None
        except Exception:
            raise ToolFailure(
                "upstream_failure",
                "网络搜索服务返回异常",
                retryable=False,
            ) from None

    return expose_failures(internet_search)
