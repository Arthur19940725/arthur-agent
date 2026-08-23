from typing import Annotated

from langchain_core.tools import tool

from api.context import require_workspace
from api.monitor import monitor
from api.workspace import WorkspaceBoundaryError
from tools.outcomes import ToolFailure, expose_failures

# 尝试导入可选依赖，实现按需加载
try:
    import docx
except ImportError:
    docx = None

try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import pandas as pd
except ImportError:
    pd = None


# def read_file_content(filename: str, instruction: str = "提取全部内容") -> str:
#     """
#     读取指定文件的内容。支持 Markdown(.md)、Word(.docx)、PDF(.pdf) 和 Excel(.xlsx/.xls)。
#     对于 Excel 文件，会自动提供数据统计信息（head 和 describe）。

#     Args:
#         filename: 要读取的文件名或路径（支持 .md, .docx, .pdf, .xlsx, .xls）
#         instruction: 对提取内容的具体指令（例如：'提取摘要', '统计数据'）


@tool
def list_session_files() -> str:
    """列出当前会话工作目录中的文件。读取用户上传或已生成的文件前先调用本工具，不要猜测路径。"""
    monitor.report_tool("会话文件列表工具", {})
    try:
        workspace = require_workspace()
    except WorkspaceBoundaryError:
        raise ToolFailure(
            "workspace_unavailable", "当前任务没有会话工作区", retryable=False
        ) from None
    try:
        files = [item["path"] for item in workspace.list_artifacts()]
    except WorkspaceBoundaryError:
        raise ToolFailure("invalid_path", "会话工作区边界无效", retryable=False) from None
    if not files:
        return "工作目录为空，没有可读取的文件。"
    return "工作目录文件：\n" + "\n".join(f"- {name}" for name in files)


@tool
def read_file_content(
    filename: Annotated[str, "要读取的文件名或路径（支持 .md, .docx, .pdf, .xlsx, .xls）"],
    instruction: Annotated[
        str, "对提取内容的具体指令（例如：'提取摘要', '统计数据'）"
    ] = "提取全部内容",
) -> str:
    """
    读取指定文件的内容。支持 Markdown(.md)、Word(.docx)、PDF(.pdf) 和 Excel(.xlsx/.xls)。
    对于 Excel 文件，会自动提供数据统计信息（head 和 describe）。
    """
    monitor.report_tool("文件内容读取工具", {"filename": filename, "instruction": instruction})

    # ====================== 1. Path 重构路径解析 ======================
    try:
        file_path = require_workspace().resolve_artifact(filename)
    except WorkspaceBoundaryError:
        raise ToolFailure("invalid_path", "文件路径不在当前会话工作区", retryable=False) from None

    # 检查文件是否存在（替代os.path.exists）
    if not file_path.exists():
        raise ToolFailure("not_found", "指定文件不存在", retryable=False)

    # 获取后缀名（替代os.path.splitext，自动转小写）
    ext = file_path.suffix.lower()

    try:
        if ext in [".md", ".txt"]:
            # Path直接读取文本（替代open + os.path）
            return file_path.read_text(encoding="utf-8")

        elif ext == ".docx":
            if docx is None:
                raise ToolFailure("capability_unavailable", "未安装 Word 读取能力", retryable=False)
            doc = docx.Document(str(file_path))  # 转字符串传给docx
            full_text = [para.text for para in doc.paragraphs]
            return "\n".join(full_text)

        elif ext == ".pdf":
            if pypdf is None:
                raise ToolFailure("capability_unavailable", "未安装 PDF 读取能力", retryable=False)
            reader = pypdf.PdfReader(str(file_path))  # 转字符串传给pypdf
            text = "\n".join([page.extract_text() or "" for page in reader.pages])
            return text

        elif ext in [".xlsx", ".xls"]:
            if pd is None:
                raise ToolFailure(
                    "capability_unavailable", "未安装 Excel 读取能力", retryable=False
                )

            try:
                df = pd.read_excel(str(file_path))  # 转字符串传给pandas
            except Exception:
                raise ToolFailure(
                    "document_parse_failed", "Excel 文件读取失败", retryable=False
                ) from None

            result = [
                f"文件: {filename}",
                f"行数: {len(df)}, 列数: {len(df.columns)}",
                f"列名: {', '.join(df.columns.astype(str))}",
                "\n[前5行数据预览]:",
                df.head().to_string(index=False),
                "\n[统计描述]:",
                df.describe().to_string(),
            ]
            return "\n".join(result)

        else:
            # 尝试作为纯文本读取
            try:
                return file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise ToolFailure(
                    "unsupported_format", "不支持该文件格式", retryable=False
                ) from None

    except ToolFailure:
        raise
    except WorkspaceBoundaryError:
        raise ToolFailure("invalid_path", "文件路径不在当前会话工作区", retryable=False) from None
    except OSError:
        raise ToolFailure("filesystem_error", "文件读取失败", retryable=True) from None


list_session_files = expose_failures(list_session_files)
read_file_content = expose_failures(read_file_content)
