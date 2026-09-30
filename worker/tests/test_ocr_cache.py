"""OCR の結果の保存・再利用（2 回目は OCR しない）。"""
import tempfile
import unittest
from unittest import mock

import pymupdf

from dwgworker import parse_pdf
from dwgworker.config import CONFIG


def blank_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=842, height=595)
    return doc.tobytes()


class OcrCacheTests(unittest.TestCase):
    def test_second_parse_uses_saved_ocr(self):
        items = [{"text": "HUXP010143", "x": 700.0, "y": 30.0, "h": 8.0, "w": 60.0, "tokens": []}]
        calls = []

        def fake_ocr(page, dpi=None, pix=None):
            calls.append(1)
            return [dict(i) for i in items]

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(CONFIG, "ocr_cache_dir", d), mock.patch.object(CONFIG, "ocr_enabled", True), \
                mock.patch.object(parse_pdf.ocr_mac, "available", return_value=True), \
                mock.patch.object(parse_pdf.ocr_mac, "render", return_value=None), \
                mock.patch.object(parse_pdf.ocr_mac, "ocr_page", side_effect=fake_ocr), \
                mock.patch.object(parse_pdf.imagelines, "from_pixmap", return_value=[(10.0, 20.0, 80.0)]):
            data = blank_pdf()
            r1, _ = parse_pdf.parse(data, "a.pdf")
            r2, _ = parse_pdf.parse(data, "a.pdf")
            self.assertEqual(len(calls), 1)
            t1 = r1["drawings"][0]["pages"][0]["text"]
            self.assertEqual(t1, r2["drawings"][0]["pages"][0]["text"])
            self.assertIn("HUXP010143", t1)
            with mock.patch.object(parse_pdf.ocr_mac, "available", return_value=False):
                r3, _ = parse_pdf.parse(data, "a.pdf")  # OCR が使えない所でも保存があれば読める
            self.assertIn("HUXP010143", r3["drawings"][0]["pages"][0]["text"])


if __name__ == "__main__":
    unittest.main()
