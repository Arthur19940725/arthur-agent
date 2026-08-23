from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool

from agent.tool_preview import preview_tool_args
from api.context import require_workspace
from api.monitor import monitor
from api.workspace import WorkspaceBoundaryError
from tools.outcomes import ToolFailure, expose_failures


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
    try:
        filename = str(Path(filename).with_suffix(".md"))
        file_path = require_workspace().resolve_artifact(filename)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Markdown 文件已成功生成：{filename}"
    except WorkspaceBoundaryError:
        raise ToolFailure("invalid_path", "文件路径不在当前会话工作区", retryable=False) from None
    except OSError:
        raise ToolFailure("filesystem_error", "Markdown 文件写入失败", retryable=True) from None


generate_markdown = expose_failures(generate_markdown)
