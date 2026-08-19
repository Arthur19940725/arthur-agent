import logging
from pathlib import Path

import markdown

from utils.word_converter import convert_md_to_pdf_via_word


def _render_html(md_abs_path: Path) -> str:
    md_content = md_abs_path.read_text(encoding="utf-8")
    html_body = markdown.markdown(md_content, extensions=["tables", "fenced_code"])
    return f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: "Microsoft YaHei", "SimHei", sans-serif; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid black; padding: 8px; }}
            pre {{ background-color: #f5f5f5; padding: 10px; border-radius: 4px; }}
            code {{ font-family: "Consolas", "Monaco", monospace; }}
        </style>
    </head>
    <body>
        {html_body}
    </body>
    </html>
    """


def convert_md_to_pdf_via_weasyprint(md_abs_path: Path, pdf_abs_path: Path) -> str:
    from weasyprint import HTML

    HTML(string=_render_html(md_abs_path), base_url=str(md_abs_path.parent)).write_pdf(pdf_abs_path)
    if pdf_abs_path.exists():
        return f"PDF 已生成：{pdf_abs_path.name}"
    return f"转换完成但未生成文件: {pdf_abs_path.name}"


def convert_markdown_to_pdf(md_abs_path: Path, pdf_abs_path: Path) -> str:
    """Prefer WeasyPrint; fall back to Word COM on Windows if needed."""
    try:
        return convert_md_to_pdf_via_weasyprint(md_abs_path, pdf_abs_path)
    except Exception as exc:
        logging.warning("WeasyPrint 转换失败，回退 Word 引擎: %s", exc)
        result = convert_md_to_pdf_via_word(md_abs_path, pdf_abs_path)
        if result.startswith("成功转换"):
            return f"PDF 已生成：{pdf_abs_path.name}"
        return result
