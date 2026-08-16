import unittest
from unittest.mock import AsyncMock

from agent.reflection_middleware import (
    REFLECTION_PROMPT,
    SubagentReflectionMiddleware,
)
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage


class ReflectionMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _request() -> ModelRequest:
        return ModelRequest(
            model=object(),
            messages=[HumanMessage(content="查询贵州茅台的实时行情")],
        )

    async def test_reflects_final_answer_once(self):
        request = self._request()
        responses = [
            ModelResponse(result=[AIMessage(content="初稿")]),
            ModelResponse(result=[AIMessage(content="修订稿")]),
        ]
        handler = AsyncMock(side_effect=responses)

        result = await SubagentReflectionMiddleware().awrap_model_call(
            request, handler
        )

        self.assertEqual(result.result[0].content, "修订稿")
        self.assertEqual(handler.await_count, 2)
        reflection_request = handler.await_args_list[1].args[0]
        self.assertEqual(reflection_request.tools, [])
        self.assertEqual(reflection_request.tool_choice, "none")
        self.assertEqual(reflection_request.messages[-1].content, REFLECTION_PROMPT)
        self.assertEqual(reflection_request.messages[-2].content, "初稿")

    async def test_does_not_reflect_when_model_requests_tools(self):
        request = self._request()
        first_response = ModelResponse(
            result=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "get_stock_quote",
                            "args": {"symbol": "600519"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
        handler = AsyncMock(return_value=first_response)

        result = await SubagentReflectionMiddleware().awrap_model_call(
            request, handler
        )

        self.assertIs(result, first_response)
        handler.assert_awaited_once_with(request)

    async def test_reflection_failure_returns_original_answer(self):
        request = self._request()
        first_response = ModelResponse(result=[AIMessage(content="可用初稿")])
        handler = AsyncMock(side_effect=[first_response, RuntimeError("暂时不可用")])

        result = await SubagentReflectionMiddleware().awrap_model_call(
            request, handler
        )

        self.assertIs(result, first_response)
        self.assertEqual(handler.await_count, 2)

    async def test_reflection_failure_can_be_configured_to_raise(self):
        request = self._request()
        handler = AsyncMock(
            side_effect=[
                ModelResponse(result=[AIMessage(content="初稿")]),
                RuntimeError("反思失败"),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "反思失败"):
            await SubagentReflectionMiddleware(
                fallback_on_error=False
            ).awrap_model_call(request, handler)


if __name__ == "__main__":
    unittest.main()
