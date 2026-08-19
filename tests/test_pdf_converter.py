import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.pdf_converter import convert_markdown_to_pdf


class PdfConverterTests(unittest.TestCase):
    def test_weasyprint_success_writes_pdf_name_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = Path(temp_dir) / "note.md"
            pdf_path = Path(temp_dir) / "note.pdf"
            md_path.write_text("# hi", encoding="utf-8")
            with patch(
                "utils.pdf_converter.convert_md_to_pdf_via_weasyprint",
                return_value="PDF 已生成：note.pdf",
            ) as weasy, patch(
                "utils.pdf_converter.convert_md_to_pdf_via_word"
            ) as word:
                result = convert_markdown_to_pdf(md_path, pdf_path)
            self.assertEqual(result, "PDF 已生成：note.pdf")
            weasy.assert_called_once()
            word.assert_not_called()

    def test_falls_back_to_word_when_weasyprint_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = Path(temp_dir) / "note.md"
            pdf_path = Path(temp_dir) / "note.pdf"
            md_path.write_text("# hi", encoding="utf-8")
            with patch(
                "utils.pdf_converter.convert_md_to_pdf_via_weasyprint",
                side_effect=RuntimeError("missing gtk"),
            ), patch(
                "utils.pdf_converter.convert_md_to_pdf_via_word",
                return_value=f"成功转换: {pdf_path} (Word引擎)",
            ):
                result = convert_markdown_to_pdf(md_path, pdf_path)
            self.assertEqual(result, "PDF 已生成：note.pdf")


if __name__ == "__main__":
    unittest.main()
