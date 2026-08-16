import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.context import reset_session_context, set_session_context
from tools.db_tools import validate_sql_query
from utils.path_utils import resolve_path


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
        self.assertEqual(validate_sql_query("WITH x AS (SELECT 1) SELECT * FROM x"), "WITH x AS (SELECT 1) SELECT * FROM x")

    def test_path_resolution_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = Path(temp_dir) / "session"
            session.mkdir()
            self.assertEqual(
                Path(resolve_path("nested/report.md", str(session))),
                session / "nested" / "report.md",
            )
            with self.assertRaises(ValueError):
                resolve_path("../outside.txt", str(session))


if __name__ == "__main__":
    unittest.main()
