import unittest
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class ApprovalState(TypedDict):
    executed: int


def approval_node(state: ApprovalState):
    decision = interrupt({
        "action_requests": [{"name": "sensitive", "args": {}}],
        "review_configs": [{"action_name": "sensitive", "allowed_decisions": ["approve", "reject"]}],
    })
    return {"executed": state["executed"] + (decision["type"] == "approve")}


def build_graph(checkpointer):
    builder = StateGraph(ApprovalState)
    builder.add_node("approval", approval_node)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    return builder.compile(checkpointer=checkpointer)


class HumanApprovalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_interrupt_resumes_with_same_thread(self):
        graph = build_graph(InMemorySaver())
        config = {"configurable": {"thread_id": "approval-test"}}
        first = [chunk async for chunk in graph.astream({"executed": 0}, config=config)]
        self.assertIn("__interrupt__", first[-1])
        snapshot = await graph.aget_state(config)
        self.assertEqual(len(snapshot.interrupts), 1)

        result = await graph.ainvoke(
            Command(resume={"type": "approve"}),
            config=config,
        )
        self.assertEqual(result["executed"], 1)

    async def test_reject_does_not_execute_sensitive_action(self):
        graph = build_graph(InMemorySaver())
        config = {"configurable": {"thread_id": "reject-test"}}
        await graph.ainvoke({"executed": 0}, config=config)
        result = await graph.ainvoke(
            Command(resume={"type": "reject"}),
            config=config,
        )
        self.assertEqual(result["executed"], 0)


if __name__ == "__main__":
    unittest.main()
