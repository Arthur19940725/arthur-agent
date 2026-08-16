from agent.subagents.knowledge_base_agent import knowledge_base_agent
from agent.subagents.database_query_agent import database_query_agent
from agent.subagents.network_search_agent import network_search_agent
from agent.subagents.weather_query_agent import weather_query_agent
from agent.subagents.stock_analysis_agent import stock_analysis_agent

# main_agent tool导入
from tools.markdown_tools import generate_markdown
from tools.pdf_tools import convert_md_to_pdf
from tools.upload_file_read_tool import read_file_content
from tools.db_tools import execute_sql_query

from deepagents import create_deep_agent

from agent.llm import model
from agent.prompts import main_agent_content
from agent.reflection_middleware import SubagentReflectionMiddleware

from api.monitor import monitor
import asyncio
import uuid
import shutil
from pathlib import Path

from api.context import set_session_context, reset_session_context, set_thread_context

from langchain_core.messages import AIMessage
from langgraph.types import Command


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

def create_main_agent(checkpointer):
    business_subagents = [
        database_query_agent,
        network_search_agent,
        knowledge_base_agent,
        weather_query_agent,
        stock_analysis_agent,
    ]
    reflection_middleware = SubagentReflectionMiddleware()
    configured_subagents = [
        {
            **subagent,
            "middleware": [
                *subagent.get("middleware", []),
                reflection_middleware,
            ],
        }
        for subagent in business_subagents
    ]

    return create_deep_agent(
       model=model,
       system_prompt=main_agent_content['system_prompt'],
       tools=[generate_markdown, convert_md_to_pdf, read_file_content, execute_sql_query],
       checkpointer=checkpointer,
       subagents=configured_subagents,
       interrupt_on=SENSITIVE_TOOL_APPROVALS,
    )

# 执行
"""
  1. 执行主智能体 一定选异步，原因：对应多个客户端
  2. 什么时候触发我们智能体的调用或者执行？？？
  3. 客户端 -> api/task -> fastapi 接口 -> 异步执行 -> main_agent的运行 （异步方法）
  4. main_agent执行stream流式处理 -> 调用工具 -> 已经埋好了点
                                   调用子智能体 -> 结果解析 -> name = task -> monitor -> 发送子智能体
                                   调用最终结果 -> 结果 -> monitor -> 发送结果的方法
                                   开启调用以后 -> 当前会话 -> 文件夹地址 -> 推送到前端
"""



project_root_path = Path(__file__).parents[1].resolve() # 绝对 解析路径标识以及软连接
# project_root_path = Path(__file__).parents[1].absolute() # 绝对
# main_agent.invoke()
# main_agent.stream()
# main_agent.astream() [选他]
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
        approvals.append({
            "approval_id": approval_id,
            "action_requests": action_requests,
            "review_configs": review_configs,
        })
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
                print(f"主智能体执行结果，最终结果：{last_msg.content[:]}")
                monitor.report_task_result(last_msg.content)
                return {"status": "completed", "result": last_msg.content}

    return {"status": "completed", "result": ""}


async def run_deep_agent(main_agent, task_query, session_id, resume_decisions=None):
    """执行或恢复一个使用 SQLite checkpoint 持久化的主智能体会话。"""
    print(f"当前会话的main_agent开始执行了！ 会话id:{session_id}")
    session_dir = project_root_path / "output" / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir_str = str(session_dir).replace("\\", "/")
    relative_session_dir_str = str(session_dir.relative_to(project_root_path)).replace("\\", "/")

    updated_info_prompt = ""
    if resume_decisions is None:
        updated_dir_path = project_root_path / "updated" / f"session_{session_id}"
        if updated_dir_path.exists():
            files = [f.name for f in updated_dir_path.iterdir() if f.is_file()]
            if files:
                for filename in files:
                    shutil.copy2(updated_dir_path / filename, session_dir / filename)
                updated_info_prompt = (
                    "\n    [已上传文件] 已加载到工作目录:\n"
                    + "\n".join([f"    - {f}" for f in files])
                    + "\n    请优先使用工具（read_file_content）读取并参考这些文件。"
                )

    session_dir_token = set_session_context(session_dir_str)
    session_id_token = set_thread_context(session_id)
    monitor.report_session_dir(session_dir_str)
    config = {"configurable": {"thread_id": session_id}}

    if resume_decisions is None:
        path_instruction = f"""
        【工作环境指令】
        工作目录: {relative_session_dir_str}
        {updated_info_prompt}

        规则：
        1. 新生成文件必须保存到工作目录：'{relative_session_dir_str}/filename'
        2. 读取已上传的文件时，请直接将文件名（例如：'开篇.txt'）作为 filename 参数传入（read_file_content）读取工具，不要带上任何目录前缀。
        3. 使用相对路径，禁止使用绝对路径
        4. 若存在上传文件，请先分析内容
        """
        graph_input = {
            "messages": [{"role": "user", "content": task_query + path_instruction}]
        }
    else:
        graph_input = Command(resume={"decisions": resume_decisions})
        monitor.report_approval_resumed(resume_decisions)

    try:
        return await _stream_agent(main_agent, graph_input, config)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        monitor._emit("error", f"执行主智能体发生异常：{exc}")
        return {"status": "error", "result": str(exc)}
    finally:
        reset_session_context(session_dir_token, session_id_token)

