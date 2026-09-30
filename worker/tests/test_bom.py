"""部品表の読み取り。2026-09-30 のサンプル（HDBY003920 ハイキパネル/FP）の配置を再現して確かめる。"""
import io
import unittest

from dwgworker import bom

H = 8.0
PAGE_H = 937.0


def it(text, x, y_img):
    """画像の座標（y 下向き）→ 上向きの y"""
    return {"text": text, "x": float(x), "y": PAGE_H - y_img, "h": H}


# 見出し（一番下）、その上に 1〜6 行目、さらに上に空行。表の下は表題欄
SAMPLE = [
    # 見出しの 2 段目（上）
    it("+新", 697, 801), it("見出", 707, 801), it("特", 860, 801), it("記", 1040, 801), it("材 料", 1150, 801), it("寸 法", 1215, 801),
    # 見出し（下）
    it("-旧", 697, 810), it("図番号", 707, 810), it("図 番 又 は 品 番", 722, 810), it("名 称 / 規 格", 880, 810),
    it("員 数", 1020, 810), it("材 質", 1095, 810), it("厚 さ", 1135, 810), it("幅(直径)", 1170, 810), it("長 さ", 1215, 810),
    # 行（下から 1, 2, …）
    it("1", 703, 790), it("HDBY003930", 720, 790), it("フレーム/ハイキパネル", 855, 790), it("11.03", 1262, 790),
    it("2", 703, 772), it("P", 720, 772), it("エンボスカラーDR323E-914×2443×0.25", 855, 772), it("1", 1030, 772),
    it("C01", 1100, 772), it("0.25", 1138, 772), it("914.00", 1170, 772), it("2443", 1215, 772), it("5.00", 1255, 772),
    it("3", 703, 753), it("P", 720, 753), it("カラーDR323-914×2381×0.35t", 855, 753), it("1", 1030, 753),
    it("C02", 1100, 753), it("0.35", 1138, 753), it("914.00", 1170, 753), it("2381", 1215, 753),
    it("4", 703, 734), it("D", 720, 734), it("ハッポウウレタン-853×2381×33", 855, 734), it("1", 1030, 734), it("D03", 1100, 734),
    it("5", 703, 719), it("0277-PEF-BLACK", 720, 719), it("パッキン/2×10W×2M", 855, 719), it("3", 1030, 719),
    it("6", 703, 702), it("0277-No.510×18W", 720, 702), it("リョウメンテープ/18×50m", 855, 702), it("1", 1030, 702),
    # 表題欄（見出しより下）：拾わない
    it("適用型式又は特記", 740, 828), it("型式", 860, 858), it("CAK-A", 960, 858), it("名称", 850, 872), it("ハイキパネル/FP", 850, 885),
    it("HDBY003920", 1160, 885),
    # 図の中の文字・注記
    it("特記以外の詳細は、HUCP101040 による。", 975, 590), it("898", 850, 110),
]


class BomExtractTests(unittest.TestCase):
    def test_sample_table(self):
        rows = bom.extract(SAMPLE)
        self.assertEqual([r["item_no"] for r in rows], ["1", "2", "3", "4", "5", "6"])
        r1 = rows[0]
        self.assertEqual((r1["part_no"], r1["name"], r1["ref_drawing_no"]), ("HDBY003930", "フレーム/ハイキパネル", "HDBY003930"))
        r2 = rows[1]
        self.assertEqual((r2["part_no"], r2["qty"], r2["material"], r2["thickness"], r2["width"], r2["length"]),
                         ("P", "1", "C01", "0.25", "914.00", "2443"))
        self.assertIn("エンボスカラー", r2["name"])
        self.assertEqual(r2["ref_drawing_no"], "")
        self.assertEqual((rows[4]["part_no"], rows[4]["qty"]), ("0277-PEF-BLACK", "3"))
        self.assertNotIn("5.00", r2["length"])  # 表の右の欄は拾わない
        self.assertFalse(any("CAK-A" in r["raw_text"] or "特記" == r["name"] for r in rows))  # 表題欄・見出しの 2 段目

    def test_header_on_top_table(self):
        items = [{"text": t, "x": x, "y": y, "h": 3.5} for t, x, y in [
            ("NO", 0, 100), ("品番", 10, 100), ("品名", 50, 100), ("数量", 100, 100), ("材質", 120, 100),
            ("1", 0, 94), ("UH-3600-01", 10, 94), ("柱", 50, 94), ("4", 102, 94), ("SS400", 120, 94),
            ("2", 0, 88), ("UH-3602", 10, 88), ("梁", 50, 88), ("2", 102, 88), ("SS400", 120, 88),
        ]]
        rows = bom.extract(items)
        self.assertEqual([(r["item_no"], r["part_no"], r["qty"], r["ref_drawing_no"]) for r in rows],
                         [("1", "UH-3600-01", "4", "UH-3600-01"), ("2", "UH-3602", "2", "UH-3602")])

    def test_no_table(self):
        self.assertEqual(bom.extract([it("注記", 10, 10), it("名称", 50, 50)]), [])

    def test_note_refs(self):
        self.assertEqual(bom.note_refs(["1.特記以外の詳細は、HUCP101040 による。", "2. 内壁・外壁とフレームは両面テープ"], "HDBY003920"),
                         ["HUCP101040"])


class BomDxfTests(unittest.TestCase):
    def test_dxf_parse_includes_bom(self):
        import ezdxf
        from dwgworker import parse_dxf
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        for i in SAMPLE:
            msp.add_text(i["text"], height=H, dxfattribs={"insert": (i["x"], i["y"])})
        buf = io.StringIO()
        doc.write(buf)
        res, _ = parse_dxf.parse(buf.getvalue().encode("utf-8"), "HDBY003920 ハイキパネル.dxf")
        d = res["drawings"][0]
        self.assertEqual(len(d["bom"]), 6)
        self.assertEqual(d["bom"][0]["ref_drawing_no"], "HDBY003930")
        self.assertEqual(d["drawing"]["attributes"].get("note_refs"), ["HUCP101040"])


if __name__ == "__main__":
    unittest.main()
