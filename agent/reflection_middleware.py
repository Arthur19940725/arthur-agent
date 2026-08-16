"""Post-run reflection for business subagents."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)


REFLECTION_PROMPT = """复核你刚才准备提交的结果，并直接输出修订后的最终结果。
检查以下内容：
1. 是否完整回答了委派任务；
2. 事实、数字和结论是否有工具结果或用户提供的信息支撑；
3. 是否遗漏了关键条件、风险、限制或数据来源；
4. 是否把推断误写成事实。

如果原结果已经正确，保持其内容不变。不要描述复核过程，不要输出“原回答”或“修改说明”。"""


class SubagentReflectionMiddleware(AgentMiddleware[Any, Any, Any]):
    """Ask a subagent to review its final answer once before returning it."""

    def __init__(
        self,
        reflection_prompt: str = REFLECTION_PROMPT,
        *,
        fallback_on_error: bool = True,
    ) -> None:
        self.reflection_prompt = reflection_prompt
        self.fallback_on_error = fallback_on_error

    @staticmethod
    def _is_final_response(response: ModelResponse[Any]) -> bool:
        """Return whether the model response is not requesting another tool call."""
        if not response.result:
            return False
        last_message = response.result[-1]
        return isinstance(last_message, AIMessage) and not last_message.tool_calls

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        first_response = await handler(request)

        if not self._is_final_response(first_response):
            return first_response

        reflection_request = request.override(
            messages=[
                *request.messages,
                *first_response.result,
                HumanMessage(content=self.reflection_prompt),
            ],
            tools=[],
            tool_choice="none",
            response_format=None,
        )

        try:
            reflected_response = await handler(reflection_request)
        except Exception:
            if not self.fallback_on_error:
                raise
            logger.warning(
                "Subagent reflection failed; returning the original response",
                exc_info=True,
            )
            return first_response

        if not reflected_response.result or not self._is_final_response(reflected_response):
            return first_response
        return reflected_response

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        first_response = handler(request)

        if not self._is_final_response(first_response):
            return first_response

        reflection_request = request.override(
            messages=[
                *request.messages,
                *first_response.result,
                HumanMessage(content=self.reflection_prompt),
            ],
            tools=[],
            tool_choice="none",
            response_format=None,
        )

        try:
            reflected_response = handler(reflection_request)
        except Exception:
            if not self.fallback_on_error:
                raise
            logger.warning(
                "Subagent reflection failed; returning the original response",
                exc_info=True,
            )
            return first_response

        if not reflected_response.result or not self._is_final_response(reflected_response):
            return first_response
        return reflected_response
