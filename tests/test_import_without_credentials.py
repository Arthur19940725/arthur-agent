import os
import subprocess
import sys
import unittest
from pathlib import Path


class ImportWithoutCredentialsTests(unittest.TestCase):
    def test_agent_modules_import_without_external_credentials(self):
        project_root = Path(__file__).parents[1]
        env = os.environ.copy()
        for name in (
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_BASE_URL",
            "LLM_DEEPSEEK_MODEL",
            "LLM_DEEPSEEK_PRO",
            "TAVILY_API_KEY",
            "RAGFLOW_API_KEY",
            "RAGFLOW_API_URL",
            "MYSQL_USER",
            "MYSQL_PASSWORD",
            "MYSQL_DATABASE",
        ):
            env.pop(name, None)
        env["PYTHONUTF8"] = "1"

        completed = subprocess.run(
            [sys.executable, "-c", "import agent.llm; import agent.main_agent"],
            cwd=project_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main()
