import asyncio
import unittest

from api.monitor import ConnectionManager
from api.task_store import TaskStore


class InspectingSocket:
    def __init__(self, store, *, fail=False):
        self.store = store
        self.fail = fail
        self.sent = []

    async def send_json(self, event):
        persisted = self.store.get_task("thread-1")["events"]
        if not persisted or persisted[-1]["seq"] != event["seq"]:
            raise AssertionError("event was delivered before it was recorded")
        if self.fail:
            raise RuntimeError("socket disconnected")
        self.sent.append(event)


class EventDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = TaskStore(":memory:")
        self.store.create_task("thread-1", "user-1", "query")
        self.manager = ConnectionManager(queue_size=2)
        await self.manager.start(self.store, asyncio.get_running_loop())

    async def asyncTearDown(self):
        await self.manager.stop()
        self.store.close()

    async def test_publish_records_before_websocket_delivery(self):
        socket = InspectingSocket(self.store)
        self.manager.active_connections["thread-1"] = socket

        stored = await self.manager.publish(
            "thread-1",
            {"type": "monitor_event", "event": "tool_start", "data": {}},
        )

        self.assertEqual(socket.sent, [stored])

    async def test_delivery_failure_keeps_replayable_event(self):
        socket = InspectingSocket(self.store, fail=True)
        self.manager.active_connections["thread-1"] = socket

        stored = await self.manager.publish(
            "thread-1",
            {"type": "monitor_event", "event": "tool_start", "data": {}},
        )

        self.assertEqual(self.store.get_task("thread-1")["events"], [stored])
        self.assertNotIn("thread-1", self.manager.active_connections)

    async def test_threadsafe_emit_is_drained_on_shutdown(self):
        socket = InspectingSocket(self.store)
        self.manager.active_connections["thread-1"] = socket

        self.manager.publish_from_context(
            {"type": "monitor_event", "event": "task_result", "data": {"result": "done"}},
            thread_id="thread-1",
        )
        await self.manager.drain()

        self.assertEqual(socket.sent[0]["event"], "task_result")
        self.assertEqual(self.store.get_task("thread-1")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
