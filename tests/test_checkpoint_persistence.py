import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.persistence import open_sqlite_checkpointer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class CounterState(TypedDict):
    count: int


async def increment(state: CounterState) -> CounterState:
    return {"count": state["count"] + 1}


def build_graph(checkpointer: AsyncSqliteSaver):
    builder = StateGraph(CounterState)
    builder.add_node("increment", increment)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    return builder.compile(checkpointer=checkpointer)


class CheckpointPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistence_context_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "nested" / "checkpoints.sqlite"
            with patch("agent.persistence.get_checkpoint_db_path", return_value=db_path):
                async with open_sqlite_checkpointer() as saver:
                    self.assertTrue(db_path.parent.is_dir())
                    self.assertIsInstance(saver, AsyncSqliteSaver)

            self.assertTrue(db_path.is_file())

    async def test_checkpoint_survives_reopening_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "checkpoints.sqlite"
            config = {"configurable": {"thread_id": "conversation-1"}}

            async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
                graph = build_graph(saver)
                first_result = await graph.ainvoke({"count": 0}, config)
                self.assertEqual(first_result["count"], 1)

            async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
                graph = build_graph(saver)
                restored_state = await graph.aget_state(config)
                self.assertEqual(restored_state.values["count"], 1)

                resumed_result = await graph.ainvoke({"count": 10}, config)
                self.assertEqual(resumed_result["count"], 11)

    async def test_threads_are_isolated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "checkpoints.sqlite"
            first_config = {"configurable": {"thread_id": "conversation-1"}}
            second_config = {"configurable": {"thread_id": "conversation-2"}}

            async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
                graph = build_graph(saver)
                await graph.ainvoke({"count": 0}, first_config)
                second_result = await graph.ainvoke({"count": 20}, second_config)
                self.assertEqual(second_result["count"], 21)

                first_state = await graph.aget_state(first_config)
                second_state = await graph.aget_state(second_config)
                self.assertEqual(first_state.values["count"], 1)
                self.assertEqual(second_state.values["count"], 21)


if __name__ == "__main__":
    unittest.main()
