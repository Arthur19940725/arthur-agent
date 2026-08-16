import asyncio
import unittest
import uuid

from fastapi import HTTPException

from api.admission import AdmissionSettings, TaskAdmission
from api.auth import Principal
from api.history import EventHistory
from api.monitor import manager
from api import server


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class ApiResourceLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = FakeClock()
        server.app.state.background_tasks = set()
        server.app.state.main_agent = object()
        server.app.state.thread_locks = {}
        server.app.state.thread_owners = {}
        server.app.state.consumed_approvals = set()
        server.app.state.admission = TaskAdmission(
            AdmissionSettings(
                max_active_per_user=1,
                max_active_process=2,
                rate_limit=2,
                rate_window_seconds=10,
                rate_history_ttl_seconds=60,
                max_query_bytes=8,
            ),
            clock=self.clock,
        )
        manager.task_status.clear()
        manager.event_history = EventHistory(max_events=2, max_bytes=128, ttl_seconds=60, clock=self.clock)
        self.release = asyncio.Event()
        self.started = asyncio.Queue()

        async def runner(main_agent, query, thread_id, **kwargs):
            await self.started.put(thread_id)
            await self.release.wait()

        server.app.state.agent_runner = runner
        self.principal = Principal(subject="user-a")

    async def asyncTearDown(self):
        self.release.set()
        tasks = list(server.app.state.background_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_normal_request_keeps_response_shape_and_releases_slot(self):
        thread_id = str(uuid.uuid4())
        response = await server.run_task(
            server.TaskRequest(query="hello", thread_id=thread_id),
            self.principal,
        )
        self.assertEqual(
            response,
            {
                "status": "started",
                "thread_id": thread_id,
                "result_url": f"/api/task/{thread_id}",
            },
        )
        await self.started.get()
        self.assertEqual(server.app.state.admission.active_process, 1)
        self.release.set()
        await asyncio.gather(*list(server.app.state.background_tasks))
        await asyncio.sleep(0)
        self.assertEqual(server.app.state.admission.active_process, 0)
        self.assertFalse(server.app.state.background_tasks)

    async def test_query_byte_limit_returns_413_without_reservation(self):
        with self.assertRaises(HTTPException) as raised:
            await server.run_task(server.TaskRequest(query="你你你"), self.principal)
        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(raised.exception.detail["code"], "query_too_large")
        self.assertEqual(server.app.state.admission.active_process, 0)
        self.assertFalse(server.app.state.background_tasks)

    async def test_active_thread_and_user_limits_return_409(self):
        server.app.state.admission.settings = AdmissionSettings(
            max_active_per_user=1,
            max_active_process=2,
            rate_limit=10,
            rate_window_seconds=10,
            rate_history_ttl_seconds=60,
            max_query_bytes=8,
        )
        first_thread = str(uuid.uuid4())
        await server.run_task(
            server.TaskRequest(query="one", thread_id=first_thread),
            self.principal,
        )
        await self.started.get()

        with self.assertRaises(HTTPException) as same_thread:
            await server.run_task(
                server.TaskRequest(query="two", thread_id=first_thread),
                self.principal,
            )
        self.assertEqual(same_thread.exception.status_code, 409)
        self.assertEqual(same_thread.exception.detail["code"], "thread_active")

        with self.assertRaises(HTTPException) as user_limit:
            await server.run_task(
                server.TaskRequest(query="two", thread_id=str(uuid.uuid4())),
                self.principal,
            )
        self.assertEqual(user_limit.exception.status_code, 409)
        self.assertEqual(user_limit.exception.detail["code"], "user_active_limit")

    async def test_rate_limit_returns_429_with_retry_after(self):
        server.app.state.admission.settings = AdmissionSettings(
            max_active_per_user=2,
            max_active_process=2,
            rate_limit=1,
            rate_window_seconds=10,
            rate_history_ttl_seconds=60,
            max_query_bytes=8,
        )
        lease = server.app.state.admission.acquire("user-a", str(uuid.uuid4()))
        lease.release()
        with self.assertRaises(HTTPException) as raised:
            await server.run_task(server.TaskRequest(query="hello"), self.principal)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.detail["code"], "rate_limited")
        self.assertEqual(raised.exception.headers["Retry-After"], "10")

    async def test_history_drop_does_not_delete_result(self):
        thread_id = str(uuid.uuid4())
        manager.mark_started(thread_id, "query")
        result = "complete-result-" * 20
        manager._record_event(thread_id, {
            "type": "monitor_event",
            "event": "task_result",
            "message": "done",
            "data": {"result": result},
        })
        snapshot = manager.get_task(thread_id)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["result"], result)
        self.assertEqual(snapshot["events"], [])
        self.assertEqual(snapshot["dropped_events"], 1)


if __name__ == "__main__":
    unittest.main()
