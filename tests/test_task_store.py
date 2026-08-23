import tempfile
import unittest
from pathlib import Path

from api.task_store import TaskNotFound, TaskStore


class TaskStoreTests(unittest.TestCase):
    def test_owner_status_approval_and_events_survive_reopen(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.sqlite"
            store = TaskStore(path)
            store.create_task("thread-1", "user-1", "query")
            store.set_status("thread-1", "running")
            stored = store.append_event(
                "thread-1",
                {
                    "type": "monitor_event",
                    "event": "waiting_for_approval",
                    "data": {"approvals": ["a"]},
                },
            )
            self.assertEqual(stored["seq"], 0)
            self.assertTrue(store.consume_approval("thread-1", "approval-1"))
            store.close()

            reopened = TaskStore(path)
            reopened.ensure_owner("thread-1", "user-1")
            task = reopened.get_task("thread-1")
            self.assertEqual(task["status"], "waiting_for_approval")
            self.assertEqual(task["events"][0]["seq"], 0)
            self.assertTrue(reopened.approval_consumed("thread-1", "approval-1"))
            self.assertFalse(reopened.consume_approval("thread-1", "approval-1"))
            reopened.close()

    def test_owner_mismatch_is_indistinguishable_from_missing_task(self):
        store = TaskStore(":memory:")
        store.create_task("thread-1", "user-1", "query")
        for thread_id, owner in (("thread-1", "user-2"), ("missing", "user-1")):
            with self.subTest(thread_id=thread_id, owner=owner), self.assertRaises(TaskNotFound):
                store.ensure_owner(thread_id, owner)
        store.close()

    def test_event_retention_does_not_delete_final_result(self):
        store = TaskStore(":memory:", max_events=2, max_bytes=100_000)
        store.create_task("thread-1", "user-1", "query")
        store.append_event("thread-1", {"event": "tool_start", "data": {}})
        store.append_event("thread-1", {"event": "tool_start", "data": {}})
        store.append_event(
            "thread-1",
            {"event": "task_result", "data": {"result": "durable-result"}},
        )

        task = store.get_task("thread-1")

        self.assertEqual(task["result"], "durable-result")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(len(task["events"]), 2)
        self.assertEqual(task["dropped_events"], 1)
        store.close()

    def test_startup_reconciles_incomplete_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.sqlite"
            store = TaskStore(path)
            store.create_task("thread-1", "user-1", "query")
            store.set_status("thread-1", "running")
            store.close()

            reopened = TaskStore(path)
            self.assertEqual(reopened.reconcile_incomplete(), 1)
            task = reopened.get_task("thread-1")
            self.assertEqual(task["status"], "interrupted")
            self.assertIn("restart", task["result"])
            reopened.close()

    def test_cleanup_removes_abandoned_idle_thread(self):
        def clock():
            return 100.0

        store = TaskStore(":memory:", ttl_seconds=10, clock=clock)
        store.claim_thread("thread-1", "user-1")

        self.assertEqual(store.cleanup(now=111.0), 1)
        with self.assertRaises(TaskNotFound):
            store.get_task("thread-1")
        store.close()


if __name__ == "__main__":
    unittest.main()
