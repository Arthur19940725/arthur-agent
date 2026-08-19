import logging
from pathlib import Path

try:
    from typing import Annotated, Optional
except ImportError:
    from typing_extensions import Annotated, Optional

from langchain_core.tools import tool
from api.monitor import monitor
from api.context import get_session_context
from utils.path_utils import resolve_path
from utils.pdf_converter import convert_markdown_to_pdf


@tool
def convert_md_to_pdf(
        md_filename: Annotated[str, "要转换的 Markdown 文件名（包含.md后缀），不要传目录"],
        pdf_filename: Annotated[Optional[str], "输出 PDF 文件名（可选，默认与源文件同名）"] = None
) -> str:
    """将当前会话工作目录中的 Markdown 转换为 PDF。优先 WeasyPrint，失败时回退 Word。"""
    monitor.report_tool(
        "Markdown转PDF工具",
        {"md_filename": md_filename, "pdf_filename": pdf_filename},
    )

    try:
        session_dir = get_session_context()
        md_name = Path(md_filename).name
        md_abs_path = Path(resolve_path(str(Path(md_name).with_suffix(".md")), session_dir))

        if not md_abs_path.exists():
            return f"错误：文件不存在 {md_name}"

        if pdf_filename:
            pdf_name = Path(pdf_filename).name
            pdf_abs_path = Path(resolve_path(str(Path(pdf_name).with_suffix(".pdf")), session_dir))
        else:
            pdf_abs_path = md_abs_path.with_suffix(".pdf")

        return convert_markdown_to_pdf(md_abs_path, pdf_abs_path)

    except Exception as e:
        logging.error(f"转换失败: {e}", exc_info=True)
        return f"转换失败: {str(e)}"


if __name__ == '__main__':
    get_session_context = lambda: "./test_session_123"
    Path("./test_session_123").mkdir(parents=True, exist_ok=True)
    Path("./test_session_123/测试文件.md").write_text(
        "# 标题\n\n测试内容\n\n|A|B|\n|---|---|\n|1|2|",
        encoding="utf-8",
    )
    print(convert_md_to_pdf.invoke({"md_filename": "测试文件.md"}))