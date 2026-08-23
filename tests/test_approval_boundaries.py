import tempfile
import unittest
from pathlib import Path

from api.workspace import SessionWorkspace, WorkspaceBoundaryError
from tools.db_tools import validate_sql_query


class ApprovalBoundaryTests(unittest.TestCase):
    def test_sql_validation_rejects_mutation_and_multiple_statements(self):
        for query in (
            "UPDATE accounts SET active = 0",
            "SELECT 1; DROP TABLE accounts",
            "DROP TABLE accounts",
        ):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    validate_sql_query(query)

    def test_sql_validation_accepts_single_read_query(self):
        self.assertEqual(validate_sql_query("SELECT 1;"), "SELECT 1")
        self.assertEqual(
            validate_sql_query("WITH x AS (SELECT 1) SELECT * FROM x"),
            "WITH x AS (SELECT 1) SELECT * FROM x",
        )

    def test_path_resolution_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = SessionWorkspace(Path(temp_dir), "thread-1")
            workspace.prepare()
            self.assertEqual(
                workspace.resolve_artifact("nested/report.md"),
                workspace.output_dir / "nested" / "report.md",
            )
            with self.assertRaises(WorkspaceBoundaryError):
                workspace.resolve_artifact("../outside.txt")


if __name__ == "__main__":
    unittest.main()
