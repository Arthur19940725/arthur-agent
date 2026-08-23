import asyncio
import unittest

from api.admission import AdmissionSettings, TaskAdmission
from api.monitor import ConnectionManager
from api.task_service import TaskService
from api.task_store import TaskStore


class TaskServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = TaskStore(":memory:")
        self.manager = ConnectionManager()
        await self.manager.start(self.store, asyncio.get_running_loop())
        self.service = TaskService(
            self.store,
            TaskAdmission(
                AdmissionSettings(
                    max_active_per_user=1,
                    max_active_process=1,
                    rate_limit=10,
                    rate_window_seconds=60,
                    rate_history_ttl_seconds=60,
                    max_query_bytes=1024,
                )
            ),
            self.manager,
        )

    async def asyncTearDown(self):
        await self.service.shutdown()
        await self.manager.stop()
        self.store.close()

    async def test_schedule_persists_result_and_releases_resources(self):
        async def runner(query, thread_id, **kwargs):
            return {"status": "completed", "result": f"{thread_id}:{query}"}

        task = self.service.schedule(
            owner="user-1",
            thread_id="thread-1",
            query="hello",
            runner=runner,
        )
        await task
        await asyncio.sleep(0)

        saved = self.service.get("thread-1", "user-1")
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["result"], "thread-1:hello")
        self.assertEqual(self.service.admission.active_process, 0)
        self.assertEqual(self.service.lock_count, 0)
        self.assertFalse(self.service.background_tasks)

    async def test_runner_exception_is_recorded_without_leaking_exception_text(self):
        async def runner(query, thread_id, **kwargs):
            raise RuntimeError("secret upstream detail")

        task = self.service.schedule(
            owner="user-1",
            thread_id="thread-1",
            query="hello",
            runner=runner,
        )
        with self.assertRaises(RuntimeError):
            await task
        await asyncio.sleep(0)

        saved = self.service.get("thread-1", "user-1")
        self.assertEqual(saved["status"], "error")
        self.assertEqual(saved["result"], "任务执行失败")
        self.assertNotIn("secret upstream detail", str(saved))
        self.assertEqual(self.service.lock_count, 0)

    async def test_resume_records_one_rejection_event(self):
        self.store.create_task("thread-1", "user-1", "query")

        async def runner(query, thread_id, **kwargs):
            return {"status": "completed", "result": "stopped"}

        task = self.service.schedule(
            owner="user-1",
            thread_id="thread-1",
            query="",
            runner=runner,
            resume_decisions=[{"type": "reject"}],
            approval_id="approval-1",
        )
        await task

        events = self.store.get_task("thread-1")["events"]
        self.assertEqual(
            [event["event"] for event in events if event["event"].startswith("approval_")],
            ["approval_rejected"],
        )


if __name__ == "__main__":
    unittest.main()
