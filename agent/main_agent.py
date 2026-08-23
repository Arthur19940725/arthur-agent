import asyncio
import logging
from pathlib import Path

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langgraph.types import Command

from agent.llm import ModelBundle
from agent.prompts import main_agent_content
from agent.subagents.database_query_agent import build_database_query_agent
from agent.subagents.knowledge_base_agent import build_knowledge_base_agent
from agent.subagents.network_search_agent import build_network_search_agent
from agent.tool_preview import preview_action_requests
from api.context import bind_run_context, reset_run_context
from api.monitor import monitor
from api.workspace import SessionWorkspace
from tools.db_tools import execute_sql_query, get_table_data, list_sql_tables
from tools.markdown_tools import generate_markdown
from tools.pdf_tools import convert_md_to_pdf
from tools.ragflow_tools import create_ragflow_tools
from tools.stock_tool import get_stock_quote
from tools.tavily_tool import create_internet_search
from tools.upload_file_read_tool import list_session_files, read_file_content
from tools.weather_tool import get_weather

logger = logging.getLogger(__name__)

SENSITIVE_TOOL_APPROVALS = {
    "generate_markdown": {
        "allowed_decisions": ["approve", "reject"],
        "description": "生成或覆盖 Markdown 文件前需要用户确认。",
    },
    "convert_md_to_pdf": {
        "allowed_decisions": ["approve", "reject"],
        "description": "生成或覆盖 PDF 文件前需要用户确认。",
    },
    "execute_sql_query": {
        "allowed_decisions": ["approve", "reject"],
        "description": "执行自定义 SQL 前需要用户确认。",
    },
}


def create_main_agent(
    checkpointer,
    *,
    models: ModelBundle,
    search_client,
    ragflow_client=None,
    database_enabled: bool = False,
):
    main_tools = [
        generate_markdown,
        convert_md_to_pdf,
        read_file_content,
        list_session_files,
        get_stock_quote,
        get_weather,
    ]
    subagents = [
        build_network_search_agent(
            models.flash,
            [create_internet_search(search_client)],
        )
    ]
    if database_enabled:
        main_tools.append(execute_sql_query)
        subagents.insert(
            0,
            build_database_query_agent(models.flash, [list_sql_tables, get_table_data]),
        )
    if ragflow_client is not None:
        subagents.append(
            build_knowledge_base_agent(models.flash, create_ragflow_tools(ragflow_client))
        )
    return create_deep_agent(
        model=models.pro,
        system_prompt=main_agent_content["system_prompt"],
        tools=main_tools,
        checkpointer=checkpointer,
        subagents=subagents,
        interrupt_on={
            name: config
            for name, config in SENSITIVE_TOOL_APPROVALS.items()
            if name in {tool.name for tool in main_tools}
        },
    )


project_root_path = Path(__file__).parents[1].resolve()


def _serialize_interrupts(raw_interrupts):
    approvals = []
    for interrupt_item in raw_interrupts or ():
        approval_id = getattr(interrupt_item, "id", None)
        value = getattr(interrupt_item, "value", None)
        if isinstance(interrupt_item, dict):
            approval_id = approval_id or interrupt_item.get("id")
            value = value or interrupt_item.get("value")
        if not approval_id or not isinstance(value, dict):
            raise ValueError("LangGraph 返回了无法识别的审批中断")
        action_requests = value.get("action_requests")
        review_configs = value.get("review_configs")
        if not isinstance(action_requests, list) or not isinstance(review_configs, list):
            raise ValueError("审批中断缺少 action_requests 或 review_configs")
        approvals.append(
            {
                "approval_id": approval_id,
                "action_requests": preview_action_requests(action_requests),
                "review_configs": review_configs,
            }
        )
    if not approvals:
        raise ValueError("审批中断为空")
    return approvals


async def _stream_agent(main_agent, graph_input, config):
    async for chunk in main_agent.astream(graph_input, config=config):
        if "__interrupt__" in chunk:
            approvals = _serialize_interrupts(chunk["__interrupt__"])
            monitor.report_waiting_for_approval(approvals)
            return {"status": "waiting_for_approval", "approvals": approvals}

        for node_name, state in chunk.items():
            if not state or "messages" not in state:
                continue
            messages = state["messages"]
            if not messages or not isinstance(messages, list):
                continue
            last_msg = messages[-1]
            if node_name != "model" or not isinstance(last_msg, AIMessage):
                continue
            if last_msg.tool_calls:
                for tool_call in last_msg.tool_calls:
                    if tool_call["name"] == "task":
                        monitor.report_assistant(
                            tool_call["args"]["subagent_type"],
                            {"description": tool_call["args"]["description"]},
                        )
            elif last_msg.content:
                monitor.report_task_result(last_msg.content)
                return {"status": "completed", "result": last_msg.content}

    return {"status": "completed", "result": ""}


async def run_deep_agent(main_agent, task_query, session_id, resume_decisions=None):
    """执行或恢复一个使用 SQLite checkpoint 持久化的主智能体会话。"""
    logger.info("agent run started", extra={"thread_id": session_id})
    workspace = SessionWorkspace(project_root_path, session_id)
    workspace.prepare()

    if resume_decisions is None:
        workspace.import_uploads()

    context_tokens = bind_run_context(session_id, workspace)
    try:
        monitor.report_session_dir(session_id)
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 40}
        if resume_decisions is None:
            graph_input = {"messages": [{"role": "user", "content": task_query}]}
        else:
            graph_input = Command(resume={"decisions": resume_decisions})
        return await _stream_agent(main_agent, graph_input, config)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        monitor._emit(
            "error",
            "任务执行失败",
            {"error_type": type(exc).__name__},
        )
        return {"status": "error", "result": "任务执行失败"}
    finally:
        reset_run_context(context_tokens)
