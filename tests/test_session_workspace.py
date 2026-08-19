import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.context import reset_session_context, set_session_context
from tools.markdown_tools import generate_markdown
from tools.upload_file_read_tool import list_session_files, read_file_content


class SessionWorkspaceTests(unittest.TestCase):
    def test_list_and_read_files_stay_inside_session_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            (session / "开篇.txt").write_text("hello", encoding="utf-8")
            token = set_session_context(str(session))
            try:
                listing = list_session_files.invoke({})
                self.assertIn("开篇.txt", listing)
                self.assertEqual(
                    read_file_content.invoke({"filename": "开篇.txt"}),
                    "hello",
                )
            finally:
                reset_session_context(token)

    def test_generate_markdown_uses_filename_only_and_ignores_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir)
            token = set_session_context(str(session))
            try:
                result = generate_markdown.invoke({
                    "filename": "../escape/report.md",
                    "content": "# title\n\nbody",
                })
                self.assertIn("report.md", result)
                self.assertTrue((session / "report.md").exists())
                self.assertFalse((session.parent / "escape" / "report.md").exists())
            finally:
                reset_session_context(token)


class RunDeepAgentInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_message_is_raw_query_without_workspace_path(self):
        captured = {}

        async def fake_stream(main_agent, graph_input, config):
            captured["graph_input"] = graph_input
            captured["config"] = config
            return {"status": "completed", "result": "ok"}

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("agent.main_agent.project_root_path", Path(temp_dir)), \
                    patch("agent.main_agent._stream_agent", new=fake_stream):
                from agent.main_agent import run_deep_agent

                await run_deep_agent(object(), "今天杭州天气？", "thread-1")

        self.assertEqual(
            captured["graph_input"]["messages"][0]["content"],
            "今天杭州天气？",
        )
        self.assertEqual(captured["config"]["recursion_limit"], 40)
        self.assertNotIn("工作目录", captured["graph_input"]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
