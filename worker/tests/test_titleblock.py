import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dwgworker import parse_dxf, parse_pdf, titleblock

ROOT = Path(__file__).resolve().parents[2]


class TitleBlockTests(unittest.TestCase):
    def test_attribs_win(self):
        f, src, conf = titleblock.resolve("x/UH-9999_Z.dxf", [{"tag": "図番", "text": "UH-3600-01"}, {"tag": "REV", "text": "A"}], [])
        self.assertEqual((f["drawing_no"], f["revision"], src), ("UH-3600-01", "A", "attrib"))

    def test_label_right_of(self):
        items = [{"text": "図番", "x": 0, "y": 0, "h": 4}, {"text": "UH-3602", "x": 20, "y": 0, "h": 4}]
        self.assertEqual(titleblock.resolve("a.dxf", [], items)[0]["drawing_no"], "UH-3602")

    def test_inline_label(self):
        self.assertEqual(titleblock.from_positioned([{"text": "図番：UH-3600A", "x": 0, "y": 0, "h": 1}])["drawing_no"], "UH-3600A")

    def test_table_header_ignored(self):
        items = [{"text": t, "x": i * 10, "y": 0, "h": 2} for i, t in enumerate(["品番", "品名", "数量", "図番"])]
        items.append({"text": "UH-3601", "x": 30, "y": -3, "h": 2})
        self.assertNotIn("drawing_no", titleblock.from_positioned(items))

    def test_filename_fallback(self):
        f, src, conf = titleblock.resolve("scan/UH-4200_B.pdf")
        self.assertEqual((f["drawing_no"], f["revision"], src, conf), ("UH-4200", "B", "filename", 0.6))


class RealConventionTests(unittest.TestCase):
    """2026-09-29 に確認した実際のファイル名・表題欄の書き方"""

    def test_part_filename(self):
        f, rule = titleblock.from_filename("図面原紙･資料 PDF/HB0011XXXX差替3 スペーサー.pdf")
        self.assertTrue(rule)
        self.assertEqual((f["drawing_no"], f["revision"], f["title"]), ("HB0011XXXX", "3", "スペーサー"))
        f, _ = titleblock.from_filename("HB0300XXXX  額縁／間仕切ﾄﾞｱ.pdf")
        self.assertEqual(f["title"], "額縁/間仕切ドア")

    def test_job_and_dated_filenames(self):
        f, _ = titleblock.from_filename("20-032 CAK-A 多用途ﾊｳｽ 図面一式.pdf")
        self.assertEqual((f["job_no"], f["model"]), ("20-032", "CAK-A"))
        f, _ = titleblock.from_filename("dxfCAK-40A 田の字36R-221003.dxf")
        self.assertEqual((f["model"], f["title"], f["revision"]), ("CAK-40A", "田の字36R", "2022-10-03"))

    def test_codes_from_spaced_ocr(self):
        self.assertEqual(titleblock.find_codes("H B 0 0 1 1 X X X X と HUCP240054"), ["HB0011XXXX", "HUCP240054"])

    def test_english_title_block_value_below_right(self):
        items = [{"text": "TIPE", "x": 0, "y": 10, "h": 2}, {"text": "CAK-40A", "x": 13, "y": 5, "h": 3},
                 {"text": "DRAWING NO", "x": 50, "y": 0, "h": 2}, {"text": "2", "x": 62, "y": -5, "h": 3}]
        p = titleblock.from_positioned(items)
        self.assertEqual((p.get("model"), p.get("sheet_no"), p.get("drawing_no")), ("CAK-40A", "2", None))

    def test_shift_jis_dxf_without_codepage(self):
        body = ("  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1009\n  0\nENDSEC\n"
                "  0\nSECTION\n  2\nENTITIES\n" + "".join(
                    f"  0\nTEXT\n  8\n0\n 10\n0\n 20\n{i}\n 30\n0\n 40\n1\n  1\n喫煙ハウス 平・立面図{i}\n" for i in range(15))
                + "  0\nENDSEC\n  0\nEOF\n").encode("cp932")
        fixed, changed = parse_dxf.fix_japanese_codepage(body)
        self.assertTrue(changed)
        r, _ = parse_dxf.parse(body, "dxfCAK-40A 喫煙-220323.dxf")
        self.assertIn("喫煙ハウス", r["pages"][0]["text"])


class IncludeTests(unittest.TestCase):
    def test_include_top_folders_only(self):
        from dwgworker import source
        from dwgworker.config import CONFIG
        with tempfile.TemporaryDirectory() as d:
            for rel in ["図面 DXF・DWG・JW・PDF/a/UH-1.dxf", "図面原紙･資料 PDF/UH-1.pdf", "Ricohｽｷｬﾅｰ/s.pdf", "top.pdf"]:
                p = Path(d) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x")
            old = CONFIG.include
            try:
                CONFIG.include = ["図面 DXF・DWG・JW・PDF", "図面原紙・資料 PDF"]  # 全角の「・」でも半角「･」のフォルダに一致
                got = sorted(r for r, _, _ in source.walk(d))
            finally:
                CONFIG.include = old
            self.assertEqual(got, ["図面 DXF・DWG・JW・PDF/a/UH-1.dxf", "図面原紙･資料 PDF/UH-1.pdf"])


class SampleParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        subprocess.run([sys.executable, str(ROOT / "samples/make_samples.py"), cls.tmp.name], check=True, capture_output=True)
        cls.out = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def parse(self, rel):
        p = self.out / rel
        mod = parse_dxf if p.suffix == ".dxf" else parse_pdf
        return mod.parse(p.read_bytes(), rel)

    def test_dxf_attrib(self):
        r, thumb = self.parse("design/UH/UH-3600/派生/UH-3600-01_A.dxf")
        self.assertEqual(r["drawing"]["drawing_no"], "UH-3600-01")
        self.assertEqual(r["drawing"]["source"], "attrib")
        self.assertTrue(thumb and thumb.startswith(b"\x89PNG"))

    def test_pdf_text_and_scan(self):
        r, _ = self.parse("design/UH/UH-3600/UH-3600A_A.pdf")
        self.assertEqual(r["drawing"]["drawing_no"], "UH-3600A")
        self.assertFalse(r["drawing"]["needs_ocr"])
        r, _ = self.parse("scan/2024/UH-4200_B.pdf")
        from dwgworker import ocr_mac
        if ocr_mac.available():  # Mac mini：画像だけの PDF を OCR して文字が取れる
            self.assertEqual(r["drawing"]["attributes"]["ocr_pages"], 1)
            self.assertFalse(r["drawing"]["needs_ocr"])
            self.assertIn("UH-4200", r["pages"][0]["text"])
        else:  # OCR できない環境では OCR 待ちになる
            self.assertTrue(r["drawing"]["needs_ocr"])


if __name__ == "__main__":
    unittest.main()
