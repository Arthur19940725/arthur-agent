from __future__ import annotations

import logging
from typing import Any, Protocol

from langchain_core.tools import tool

from api.monitor import monitor
from tools.outcomes import ToolFailure, expose_failures

logger = logging.getLogger(__name__)


class RagFlowClient(Protocol):
    def list_chats(self, **kwargs) -> list[Any]: ...


def _map_upstream_failure(error: Exception) -> ToolFailure:
    if isinstance(error, TimeoutError):
        return ToolFailure("upstream_timeout", "知识库服务响应超时", retryable=True)
    if isinstance(error, (ConnectionError, OSError)):
        return ToolFailure("upstream_unavailable", "知识库服务暂不可用", retryable=True)
    return ToolFailure("upstream_failure", "知识库服务返回异常", retryable=False)


def create_ragflow_tools(client: RagFlowClient):
    @tool
    def get_assistant_list() -> str:
        """列出 RAGFlow 助手、描述及其关联知识库。"""
        monitor.report_tool("RAGFlow助手列表查询工具", {})
        try:
            chats = client.list_chats()
        except Exception as exc:
            raise _map_upstream_failure(exc) from None
        if not chats:
            return "没有任何可用助手"
        lines = []
        for chat in chats:
            dataset_names = [dataset["name"] for dataset in (chat.datasets or [])]
            lines.append(
                f"助手名称:{chat.name};功能介绍：{chat.description}; "
                f"关联的知识库：{'、'.join(dataset_names)}"
            )
        return "\n".join(lines)

    @tool
    def create_ask_delete(chat_name: str, question: str) -> str:
        """向指定 RAGFlow 助手进行一次临时会话提问，并在完成后删除会话。"""
        monitor.report_tool(
            "RAGFlow提问工具",
            {"chat_name": chat_name, "question": question},
        )
        try:
            chats = client.list_chats(name=chat_name)
        except Exception as exc:
            raise _map_upstream_failure(exc) from None
        if not chats:
            raise ToolFailure("not_found", "未找到指定的知识库助手", retryable=False)
        chat = chats[0]
        session = None
        try:
            session = chat.create_session(name="temp_session_ask")
            result = ""
            for part in session.ask(question=question, stream=True):
                result = part.content
        except Exception as exc:
            if session is not None:
                try:
                    chat.delete_sessions(ids=[session.id])
                except Exception:
                    logger.warning("RAGFlow temporary session cleanup failed after request failure")
            raise _map_upstream_failure(exc) from None
        try:
            chat.delete_sessions(ids=[session.id])
        except Exception:
            raise ToolFailure("cleanup_failed", "知识库临时会话清理失败", retryable=True) from None
        return result

    return expose_failures(get_assistant_list), expose_failures(create_ask_delete)
