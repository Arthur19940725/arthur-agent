import unittest
from unittest.mock import patch

from agent.llm import model, pro_model
from agent.main_agent import create_main_agent


class MainAgentConfigurationTests(unittest.TestCase):
    @patch("agent.main_agent.create_deep_agent")
    def test_main_agent_uses_pro_and_collapses_single_tool_experts(self, create_deep_agent):
        create_main_agent(checkpointer=object())

        kwargs = create_deep_agent.call_args.kwargs
        self.assertIs(kwargs["model"], pro_model)

        subagents = kwargs["subagents"]
        self.assertEqual(
            [item["name"] for item in subagents],
            ["数据库查询助手", "网络搜索助手", "RAGFlow助手"],
        )
        for subagent in subagents:
            self.assertNotIn("middleware", subagent)
            self.assertIs(subagent["model"], model)

        tool_names = [tool.name for tool in kwargs["tools"]]
        self.assertEqual(
            set(tool_names),
            {
                "generate_markdown",
                "convert_md_to_pdf",
                "read_file_content",
                "list_session_files",
                "execute_sql_query",
                "get_stock_quote",
                "get_weather",
            },
        )
        self.assertEqual(
            set(kwargs["interrupt_on"]),
            {"generate_markdown", "convert_md_to_pdf", "execute_sql_query"},
        )
        self.assertNotIn("只能交给文件生成助手", kwargs["system_prompt"])
        self.assertIn("当前不支持直接生成 Word", kwargs["system_prompt"])
        self.assertIn("get_stock_quote", kwargs["system_prompt"])
        self.assertIn("get_weather", kwargs["system_prompt"])
        self.assertIn("「茅台现在多少钱」", kwargs["system_prompt"])
        self.assertIn("「杭州今天天气」", kwargs["system_prompt"])
        for subagent in subagents:
            self.assertIn("使用时机：", subagent["description"])
            self.assertIn("不要用于：", subagent["description"])


if __name__ == "__main__":
    unittest.main()
