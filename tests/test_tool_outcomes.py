import json
import unittest

from tools.db_tools import get_table_data
from tools.outcomes import ToolFailure, render_tool_failure
from tools.ragflow_tools import create_ragflow_tools
from tools.tavily_tool import create_internet_search


class FailingSearchClient:
    def search(self, **kwargs):
        raise TimeoutError("secret provider diagnostics")


class FailingRagFlowClient:
    def list_chats(self, **kwargs):
        raise ConnectionError("internal host and token")


class FailingChat:
    def create_session(self, **kwargs):
        raise RuntimeError("secret RAGFlow session diagnostics")


class SessionFailingRagFlowClient:
    def list_chats(self, **kwargs):
        return [FailingChat()]


class ToolOutcomeTests(unittest.TestCase):
    def test_failure_payload_has_stable_machine_readable_fields(self):
        payload = json.loads(
            render_tool_failure(ToolFailure("invalid_input", "参数无效", retryable=False))
        )
        self.assertEqual(
            payload,
            {
                "ok": False,
                "code": "invalid_input",
                "message": "参数无效",
                "retryable": False,
            },
        )

    def test_search_adapter_maps_timeout_without_leaking_provider_details(self):
        search = create_internet_search(FailingSearchClient())

        result = search.invoke({"query": "test"})

        payload = json.loads(result)
        self.assertEqual(payload["code"], "upstream_timeout")
        self.assertTrue(payload["retryable"])
        self.assertNotIn("secret provider diagnostics", result)

    def test_ragflow_adapter_maps_connection_failure(self):
        assistant_list, _ = create_ragflow_tools(FailingRagFlowClient())

        result = assistant_list.invoke({})

        payload = json.loads(result)
        self.assertEqual(payload["code"], "upstream_unavailable")
        self.assertTrue(payload["retryable"])
        self.assertNotIn("internal host", result)

    def test_ragflow_adapter_maps_session_failure(self):
        _, ask = create_ragflow_tools(SessionFailingRagFlowClient())

        result = ask.invoke({"chat_name": "assistant", "question": "question"})

        payload = json.loads(result)
        self.assertEqual(payload["code"], "upstream_failure")
        self.assertNotIn("secret RAGFlow session diagnostics", result)

    def test_database_adapter_maps_invalid_identifier(self):
        result = get_table_data.invoke({"table_name": "users; DROP TABLE users"})

        payload = json.loads(result)
        self.assertEqual(payload["code"], "invalid_input")
        self.assertFalse(payload["retryable"])


if __name__ == "__main__":
    unittest.main()
