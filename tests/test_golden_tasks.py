import unittest
from pathlib import Path

import yaml

from agent.main_agent import BUSINESS_SUBAGENTS, MAIN_AGENT_TOOLS


GOLDEN_PATH = Path(__file__).resolve().parents[1] / "evals" / "golden_tasks.yml"


class GoldenTaskCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with GOLDEN_PATH.open(encoding="utf-8") as handle:
            cls.payload = yaml.safe_load(handle)
        cls.tasks = cls.payload["tasks"]
        cls.tool_names = {tool.name for tool in MAIN_AGENT_TOOLS}
        cls.subagent_names = {item["name"] for item in BUSINESS_SUBAGENTS}

    def test_golden_set_has_twenty_tasks_with_unique_ids(self):
        self.assertGreaterEqual(len(self.tasks), 20)
        ids = [task["id"] for task in self.tasks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_expected_routes_exist_in_agent_catalog(self):
        for task in self.tasks:
            with self.subTest(task=task["id"]):
                self.assertTrue(task["query"].strip())
                for tool_name in task.get("expected_tools", []):
                    self.assertIn(tool_name, self.tool_names)
                for tool_name in task.get("forbidden_tools", []):
                    self.assertIn(tool_name, self.tool_names)
                for name in task.get("expected_subagents", []):
                    self.assertIn(name, self.subagent_names)
                for name in task.get("forbidden_subagents", []):
                    self.assertIn(name, self.subagent_names)

    def test_quote_and_weather_tasks_do_not_require_search(self):
        for task_id in ("quote-maotai", "weather-hangzhou", "no-search-for-weather"):
            task = next(item for item in self.tasks if item["id"] == task_id)
            self.assertNotIn("网络搜索助手", task.get("expected_subagents", []))


if __name__ == "__main__":
    unittest.main()
