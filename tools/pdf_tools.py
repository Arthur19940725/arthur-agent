from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool

from api.context import require_workspace
from api.monitor import monitor
from api.workspace import WorkspaceBoundaryError
from tools.outcomes import ToolFailure, expose_failures
from utils.pdf_converter import convert_markdown_to_pdf


@tool
def convert_md_to_pdf(
    md_filename: Annotated[str, "要转换的 Markdown 文件名（包含.md后缀），不要传目录"],
    pdf_filename: Annotated[str | None, "输出 PDF 文件名（可选，默认与源文件同名）"] = None,
) -> str:
    """将当前会话工作目录中的 Markdown 转换为 PDF。优先 WeasyPrint，失败时回退 Word。"""
    monitor.report_tool(
        "Markdown转PDF工具",
        {"md_filename": md_filename, "pdf_filename": pdf_filename},
    )

    try:
        workspace = require_workspace()
        md_name = str(Path(md_filename).with_suffix(".md"))
        md_abs_path = workspace.resolve_artifact(md_name)

        if not md_abs_path.exists():
            raise ToolFailure("not_found", "待转换的 Markdown 文件不存在", retryable=False)

        if pdf_filename:
            pdf_name = str(Path(pdf_filename).with_suffix(".pdf"))
            pdf_abs_path = workspace.resolve_artifact(pdf_name)
        else:
            pdf_abs_path = md_abs_path.with_suffix(".pdf")

        result = convert_markdown_to_pdf(md_abs_path, pdf_abs_path)
        if not result.startswith("PDF 已生成"):
            raise ToolFailure("conversion_failed", "PDF 转换失败", retryable=False)
        return result

    except ToolFailure:
        raise
    except WorkspaceBoundaryError:
        raise ToolFailure("invalid_path", "文件路径不在当前会话工作区", retryable=False) from None
    except OSError:
        raise ToolFailure("filesystem_error", "PDF 文件读写失败", retryable=True) from None


convert_md_to_pdf = expose_failures(convert_md_to_pdf)
