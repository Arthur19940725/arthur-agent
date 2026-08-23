import tempfile
import unittest
from pathlib import Path

from api.context import bind_run_context, require_workspace, reset_run_context
from api.workspace import SessionWorkspace, WorkspaceBoundaryError, validate_thread_id


class WorkspaceBoundaryTests(unittest.TestCase):
    def test_workspace_accepts_only_thread_scoped_relative_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = SessionWorkspace(Path(temp_dir), "thread-1")
            workspace.prepare()

            self.assertEqual(
                workspace.resolve_artifact("nested/report.md"),
                workspace.output_dir / "nested" / "report.md",
            )
            for unsafe in ("../outside.txt", str(Path(temp_dir).resolve()), "C:\\Windows\\win.ini"):
                with self.subTest(path=unsafe), self.assertRaises(WorkspaceBoundaryError):
                    workspace.resolve_artifact(unsafe)

    def test_missing_run_context_fails_closed(self):
        with self.assertRaises(WorkspaceBoundaryError):
            require_workspace()

    def test_context_binds_workspace_not_a_raw_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = SessionWorkspace(Path(temp_dir), "thread-1")
            tokens = bind_run_context("thread-1", workspace)
            try:
                self.assertIs(require_workspace(), workspace)
            finally:
                reset_run_context(tokens)

    def test_symlink_escape_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = SessionWorkspace(root, "thread-1")
            workspace.prepare()
            outside = root / "outside"
            outside.mkdir()
            link = workspace.output_dir / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable in this environment")

            with self.assertRaises(WorkspaceBoundaryError):
                workspace.resolve_artifact("link/secret.txt")

    def test_session_root_symlink_escape_is_rejected_when_supported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "output").mkdir()
            outside = root / "outside"
            outside.mkdir()
            session_root = root / "output" / "session_thread-1"
            try:
                session_root.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable in this environment")

            workspace = SessionWorkspace(root, "thread-1")
            with self.assertRaises(WorkspaceBoundaryError):
                workspace.prepare()

    def test_listing_exposes_relative_paths_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = SessionWorkspace(Path(temp_dir), "thread-1")
            workspace.prepare()
            target = workspace.resolve_artifact("nested/report.md")
            target.parent.mkdir()
            target.write_text("hello", encoding="utf-8")

            items = workspace.list_artifacts()

            self.assertEqual(items[0]["path"], "nested/report.md")
            self.assertNotIn(str(Path(temp_dir)), str(items))

    def test_thread_id_validation_is_shared_and_deterministic(self):
        self.assertEqual(validate_thread_id("thread_1"), "thread_1")
        for value in ("", "../escape", "has space", "a" * 129):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_thread_id(value)


if __name__ == "__main__":
    unittest.main()
