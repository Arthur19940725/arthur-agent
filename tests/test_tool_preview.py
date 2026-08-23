import unittest

from agent.tool_preview import preview_action_requests, preview_tool_args


class ToolPreviewTests(unittest.TestCase):
    def test_markdown_content_is_replaced_with_preview(self):
        content = "A" * 500
        previewed = preview_tool_args(
            "generate_markdown",
            {"filename": "report.md", "content": content},
        )
        self.assertEqual(previewed["filename"], "report.md")
        self.assertEqual(previewed["content_chars"], 500)
        self.assertTrue(previewed["content_preview"].endswith("…"))
        self.assertNotIn("content", previewed)
        self.assertLess(len(previewed["content_preview"]), 500)

    def test_sql_args_are_kept_in_full(self):
        args = {"query": "SELECT * FROM holdings WHERE symbol = '600519'"}
        self.assertEqual(preview_tool_args("execute_sql_query", args), args)

    def test_action_requests_preview_markdown_only(self):
        actions = preview_action_requests(
            [
                {"name": "generate_markdown", "args": {"filename": "a.md", "content": "B" * 401}},
                {"name": "execute_sql_query", "args": {"query": "SELECT 1"}},
            ]
        )
        self.assertNotIn("content", actions[0]["args"])
        self.assertEqual(actions[1]["args"]["query"], "SELECT 1")


if __name__ == "__main__":
    unittest.main()
