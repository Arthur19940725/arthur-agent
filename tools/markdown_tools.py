from pathlib import Path

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated
from langchain_core.tools import tool
from api.monitor import monitor
from api.context import get_session_context
from utils.path_utils import resolve_path
from agent.tool_preview import preview_tool_args


@tool
def generate_markdown(
        content: Annotated[str, "要写入 Markdown 文档的正文，必须是已经收集到的完整证据，禁止占位符"],
        filename: Annotated[str, "文件名，例如 report.md；不要包含目录或绝对路径"],
):
    """把正文写入当前会话工作目录中的 Markdown 文件。工作目录由系统注入，不要传路径。"""
    monitor.report_tool(
        "Markdown文档生成工具",
        preview_tool_args("generate_markdown", {"filename": filename, "content": content}),
    )
    filename = Path(filename).name
    if not filename.endswith(".md"):
        filename += ".md"

    session_dir = get_session_context()
    file_path = Path(resolve_path(filename, session_dir))
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Markdown 文件已成功生成：{filename}"
    except Exception as exc:
        return f"生成Markdown文件失败: {exc}"
