import unittest
from unittest.mock import patch

from agent.main_agent import create_main_agent


class MainAgentConfigurationTests(unittest.TestCase):
    @patch("agent.main_agent.create_deep_agent")
    def test_business_subagents_receive_reflection_middleware(self, create_deep_agent):
        create_main_agent(checkpointer=object())

        subagents = create_deep_agent.call_args.kwargs["subagents"]
        self.assertEqual(len(subagents), 5)
        for subagent in subagents:
            self.assertEqual(len(subagent["middleware"]), 1)
            self.assertEqual(
                subagent["middleware"][0].__class__.__name__,
                "SubagentReflectionMiddleware",
            )

        kwargs = create_deep_agent.call_args.kwargs
        self.assertEqual(
            set(kwargs["interrupt_on"]),
            {"generate_markdown", "convert_md_to_pdf", "execute_sql_query"},
        )
        for config in kwargs["interrupt_on"].values():
            self.assertEqual(config["allowed_decisions"], ["approve", "reject"])
        self.assertIn("execute_sql_query", [tool.name for tool in kwargs["tools"]])
        stock_agent = next(
            subagent for subagent in subagents if subagent["name"] == "股票分析助手"
        )
        self.assertIn("model", stock_agent)
        self.assertIn("tools", stock_agent)



if __name__ == "__main__":
    unittest.main()
