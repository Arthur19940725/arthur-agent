import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.context import bind_run_context, reset_run_context
from api.workspace import SessionWorkspace
from tools.markdown_tools import generate_markdown
from tools.upload_file_read_tool import list_session_files, read_file_content


class SessionWorkspaceTests(unittest.TestCase):
    def test_list_and_read_files_stay_inside_session_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = SessionWorkspace(Path(temp_dir), "thread-1")
            workspace.prepare()
            (workspace.output_dir / "开篇.txt").write_text("hello", encoding="utf-8")
            tokens = bind_run_context("thread-1", workspace)
            try:
                listing = list_session_files.invoke({})
                self.assertIn("开篇.txt", listing)
                self.assertEqual(
                    read_file_content.invoke({"filename": "开篇.txt"}),
                    "hello",
                )
            finally:
                reset_run_context(tokens)

    def test_generate_markdown_rejects_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = SessionWorkspace(Path(temp_dir), "thread-1")
            workspace.prepare()
            tokens = bind_run_context("thread-1", workspace)
            try:
                result = generate_markdown.invoke(
                    {
                        "filename": "../escape/report.md",
                        "content": "# title\n\nbody",
                    }
                )
                self.assertEqual(json.loads(result)["code"], "invalid_path")
            finally:
                reset_run_context(tokens)


class RunDeepAgentInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_message_is_raw_query_without_workspace_path(self):
        captured = {}

        async def fake_stream(main_agent, graph_input, config):
            captured["graph_input"] = graph_input
            captured["config"] = config
            return {"status": "completed", "result": "ok"}

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("agent.main_agent.project_root_path", Path(temp_dir)),
                patch("agent.main_agent._stream_agent", new=fake_stream),
            ):
                from agent.main_agent import run_deep_agent

                await run_deep_agent(object(), "今天杭州天气？", "thread-1")

        self.assertEqual(
            captured["graph_input"]["messages"][0]["content"],
            "今天杭州天气？",
        )
        self.assertEqual(captured["config"]["recursion_limit"], 40)
        self.assertNotIn("工作目录", captured["graph_input"]["messages"][0]["content"])

    async def test_run_failure_does_not_expose_raw_exception(self):
        async def failing_stream(main_agent, graph_input, config):
            raise RuntimeError("secret provider diagnostics")

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("agent.main_agent.project_root_path", Path(temp_dir)),
                patch("agent.main_agent._stream_agent", new=failing_stream),
            ):
                from agent.main_agent import run_deep_agent

                result = await run_deep_agent(object(), "query", "thread-1")

        self.assertEqual(result, {"status": "error", "result": "任务执行失败"})
        self.assertNotIn("secret provider diagnostics", str(result))


if __name__ == "__main__":
    unittest.main()
