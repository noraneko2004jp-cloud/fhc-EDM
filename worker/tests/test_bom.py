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


def t(text, x, y, h=3.5):
    return {"text": text, "x": float(x), "y": float(y), "h": h}


# 実 DXF（HCY1300213 ｶｲﾀﾞﾝﾔﾈｸﾐﾀﾃ など）で見えた崩れ方を再現した表
#   ・見出しが 3 段（図番又は品番／特記・厚さ・幅・長さ／名称・員数・材質）
#   ・行番号が部品の文字より少し上にずれた行、行番号が品番とくっついた行
#   ・名称の 2 行目、表の上の注記、購入品の品番（塗料）
REAL = [
    # 表題欄（見出しより下）
    t("適用型式又は特記", 20, 88), t("製　図", 60, 88), t("組　立　図　番", 5, 80),
    # 見出し（3 段）
    t("名 称 / 規 格", 60, 100), t("員 数", 120, 100), t("材 質", 135, 100),
    t("特　　記", 60, 103.5), t("厚　さ", 150, 103.5), t("幅(直径)", 163, 103.5), t("長　さ", 185, 103.5),
    t("出", 5, 107), t("図　番　又　は　品　番", 20, 107),
    # 行（下から）
    t("1 HCY1301213", 5, 114), t("ｶｲﾀﾞﾝﾔﾈｺｯｶｸ", 60, 114), t("1", 122, 114),
    t("2", 5, 121), t("X", 20, 121), t("EKｶｸﾅﾐK2ｶﾞﾀ/片山鉄建", 60, 121), t("1", 122, 121), t("0.27", 150, 121), t("820.00", 163, 121), t("4100", 185, 121),
    t("3", 5, 130.5), t("P/ｺ", 20, 128), t("ｶﾗｰDR323-50×77×80×0.8", 60, 128), t("1", 122, 128), t("0.8", 150, 128), t("203.00", 163, 128), t("1100", 185, 128),
    t("4", 5, 135), t("0280-RG-TS-2311-16KG", 20, 135), t("ﾄﾘｮｳ/濃茶/#100ｶﾞﾙｺｰﾄ", 60, 135), t("1", 122, 135),
    t("5", 5, 142), t("6139-FMJ13", 20, 142), t("ﾄﾞﾘﾙｽｸﾘｭｰ/4×13", 60, 142), t("60", 122, 142),
    t("6", 5, 149), t("X", 20, 149), t("ﾘﾙｶｶﾞﾙｺｰﾄ#100ﾌﾞﾗｯｸ", 60, 149),
    t("ｱﾙｸｱ#730ﾌﾞﾗｯｸ/松岡塗料", 60, 153),
    # 表の上の注記（拾わない）
    t("注記：1.溶接要領はHU90002205/歩廊による", 20, 165), t("反対側は開けない", 30, 171), t("4-φ14穴", 40, 177),
]

# 部品表のない単品図面（HDZY007970 など）：表題欄の「組立図番・員数」を部品表と取り違えない。親の図番は別に拾う
SINGLE = [
    t("適用型式又は特記", 20, 50), t("認可 点検 製　図 黒埼", 60, 50),
    t("名称", 60, 40), t("材質", 100, 40), t("員数", 120, 40),
    t("HDZY007950 1", 5, 30),
    t("組　立　図　番 員数", 5, 26), t("ﾊﾟﾈﾙ　ﾌﾚｰﾑ", 60, 26), t("HDZY007970", 150, 26),
]


class BomRealPatternTests(unittest.TestCase):
    def test_multiline_header_offsets_and_notes(self):
        rows = bom.extract(REAL)
        self.assertEqual([r["item_no"] for r in rows], ["1", "2", "3", "4", "5", "6"])
        r = {x["item_no"]: x for x in rows}
        self.assertEqual((r["1"]["part_no"], r["1"]["ref_drawing_no"], r["1"]["name"]), ("HCY1301213", "HCY1301213", "カイダンヤネコッカク"))
        self.assertEqual((r["2"]["thickness"], r["2"]["width"], r["2"]["length"], r["2"]["material"]), ("0.27", "820.00", "4100", ""))
        self.assertEqual((r["3"]["part_no"], r["3"]["qty"]), ("P/コ", "1"))  # ずれた行番号を正しい行に
        self.assertEqual(r["4"]["ref_drawing_no"], "")  # 塗料の品番の一部を図番にしない
        self.assertEqual(r["5"]["qty"], "60")
        self.assertIn("松岡塗料", r["6"]["name"])  # 名称の 2 行目
        self.assertFalse(any("注記" in x["raw_text"] or "図番又は品番" in x["raw_text"].replace(" ", "").replace("　", "") for x in rows))

    def test_single_part_drawing_has_no_bom_but_parent(self):
        self.assertEqual(bom.extract(SINGLE), [])
        self.assertEqual(bom.assembly_refs(SINGLE, "HDZY007970"), ["HDZY007950"])


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


# ---- 社内の様式そのもの（実 DXF 5 件で確かめた配置。文字は架空）----
# 見出しの「図番又は品番」の左端を 0 とした、列の罫線と文字の位置
TPL_LINES = [-26.89, -23.89, -20.89, -14.89, 45.11, 135.11, 168.11, 177.11, 192.11, 210.11, 225.11, 232.61, 240.11]
TPL_SUBLINES = [144.11, 150.11, 156.11, 162.11]  # 員数の欄の中の細い区切り（見出しの段より上だけ）
TPL_HEADER = [  # (dx, dy, 文字, 高さ)
    (-25.93, 3.5, "＋", 3.0), (-22.93, 3.5, "新", 3.0), (-19.43, 3.5, "見", 3.0), (-16.93, 3.5, "出", 3.0),
    (63.0, 3.0, "特　　　　　　　　　　　記", 3.0), (181.4, 3.0, "厚　さ", 3.0), (195.5, 3.0, "幅(直径)", 3.0),
    (212.0, 3.0, "長　さ", 3.0), (226.8, 2.75, "質量", 2.5), (235.9, 2.75, "計", 2.5),
    (-22.93, 0.5, "出", 3.0), (171.0, 0.5, "材質", 3.0), (0.0, 0.0, "図　番　又　は　品　番", 3.0),
    (63.0, -2.0, "名　　称　　/　　規　　格", 3.0), (135.0, -2.0, "員　　　　数", 3.0), (189.0, -2.0, "材　　　料　　　寸　　　法", 3.0),
    (-25.93, -2.5, "－", 3.0), (-22.93, -2.5, "図", 3.0), (-19.43, -2.5, "番", 3.0), (-16.93, -2.5, "号", 3.0),
]


def tpl_table(ox, oy, rows, n_rows):
    """様式の表 1 つ分の (文字, 縦線)。rows: [(番号, 品番, 名称, 員数, 材質, 厚さ, 幅, 長さ, 質量)]"""
    items = [t(s, ox + dx, oy + dy, hh) for dx, dy, s, hh in TPL_HEADER]
    top = oy + 3 + 8 * n_rows
    lines = [(ox + dx, oy - 3, top) for dx in TPL_LINES] + [(ox + dx, oy + 2, top) for dx in TPL_SUBLINES]
    for k, (no, part, name, qty, mat, th, wd, ln, wt) in enumerate(rows):
        y = oy + 7.9 + 8 * k
        items += [t(str(no), ox - 18.46, y + 1.5, 3.2), t(part, ox - 13.5, y, 3.2), t(name, ox + 46.5, y, 3.2)]
        for val, dx in ((qty, 136.5), (mat, 169.5), (th, 178.5), (wd, 193.5), (ln, 211.5)):
            if val:
                items.append(t(val, ox + dx, y, 3.2))
        if wt:
            items += [t(wt, ox + 226.35, y - 0.5, 3.2), t(wt, ox + 233.85, y - 0.5, 3.2)]
    return items, lines


def tpl_drawing():
    right, rl = tpl_table(0, 0, [
        (1, "ZZAB100010", "テストユカ/ドア", "1", "", "", "", "", "12.00"),
        (2, "P/L", "ﾃｽﾄﾊﾟｲﾌﾟ-25×35×1.2", "2", "0C1", "12", "58", "2438", "1.36"),
        (3, "0280-RG-TS-2311-16KG", "ﾄﾘｮｳ/ﾃｽﾄ 16kg", "1", "", "", "", "0.5m", ""),
    ], 6)
    left, ll = tpl_table(-281.5, -35, [
        (4, "ZZAB100020", "テストヤネ", "1", "", "", "", "", ""),
        (5, "P/コ", "ﾃｽﾄｱﾝｸﾞﾙ-30×25×1.6", "4", "0C2", "16", "46", "2055", "1.19"),
    ], 13)
    extra = [
        # 表の上の図の文字（表の罫線より上）：拾わない
        t("A", -10, 55, 6.0), t("8", 40, 56, 3.2), t("桁立面/窓", 50, 57, 4.8), t("注記：溶接部はスラグを除去すること", 0, 70, 4.8),
        # 表題欄（見出しの下）
        t("適用型式又は特記", 10, -6.5, 3.0), t("認可", 42.5, -6.5, 3.0), t("尺度", 122, -6.5, 3.0),
        t("ZZAB000010", -25.5, -31.3, 3.2), t("1", 5, -31.3, 3.2), t("組　立　図　番", -24, -36.6, 3.0), t("員数", 4, -36.6, 3.0),
        # 左の表の 2 段の行：名称が 2 行、員数は行の真ん中
    ]
    y6 = -35 + 7.9 + 8 * 2
    extra += [t("6", -281.5 - 18.46, y6 + 1.5, 3.2), t("X", -281.5 - 14.0, y6, 3.2), t("ﾃｽﾄﾄﾘｮｳ#100ﾌﾞﾗｯｸ", -281.5 + 46.5, y6, 3.2),
              t("ﾃｽﾄｼﾝﾅｰ/ﾃｽﾄ塗料", -281.5 + 46.5, y6 + 3.67, 3.2), t("54", -281.5 + 136.5, y6 - 4.4, 3.2)]
    return right + left + extra, rl + ll


class BomTemplateTests(unittest.TestCase):
    def test_company_template_with_continuation_table(self):
        items, vlines = tpl_drawing()
        rows = bom.extract(items, vlines)
        self.assertEqual([r["item_no"] for r in rows], ["1", "2", "3", "4", "5", "6"])
        r = {x["item_no"]: x for x in rows}
        self.assertEqual((r["1"]["part_no"], r["1"]["ref_drawing_no"], r["1"]["qty"]), ("ZZAB100010", "ZZAB100010", "1"))
        self.assertEqual((r["2"]["material"], r["2"]["thickness"], r["2"]["width"], r["2"]["length"]), ("0C1", "12", "58", "2438"))
        self.assertEqual((r["3"]["ref_drawing_no"], r["3"]["length"]), ("", "0.5m"))
        # 左下の続きの表
        self.assertEqual((r["5"]["part_no"], r["5"]["qty"], r["5"]["thickness"], r["5"]["width"], r["5"]["length"]),
                         ("P/コ", "4", "16", "46", "2055"))
        self.assertEqual(r["4"]["ref_drawing_no"], "ZZAB100020")
        # 2 段の行：名称は上から、員数は行の真ん中のもの
        self.assertEqual((r["6"]["name"], r["6"]["qty"]), ("テストシンナー/テスト塗料 テストトリョウ#100ブラック", "54"))
        # 表の上の図の文字・注記、表題欄は入らない
        text = " ".join(x["raw_text"] for x in rows)
        for w in ("桁立面", "注記", "適用型式", "ZZAB000010"):
            self.assertNotIn(w, text)
        self.assertEqual(bom.assembly_refs(items, "ZZAB200000"), ["ZZAB000010"])

    def test_template_dxf_end_to_end(self):
        import ezdxf
        from dwgworker import parse_dxf
        items, vlines = tpl_drawing()
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        for i in items:
            msp.add_text(i["text"], height=i["h"], dxfattribs={"insert": (i["x"], i["y"])})
        for x, a, b in vlines:
            msp.add_line((x, a), (x, b))
        buf = io.StringIO()
        doc.write(buf)
        res, _ = parse_dxf.parse(buf.getvalue().encode("utf-8"), "ZZAB200000 テスト組立.dxf")
        d = res["drawings"][0]
        self.assertEqual([r["item_no"] for r in d["bom"]], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual(d["bom"][4]["length"], "2055")
        self.assertEqual(d["drawing"]["attributes"].get("assembly_refs"), ["ZZAB000010"])


class BomVariantTests(unittest.TestCase):
    def test_branch_number_qty_columns(self):
        """枝番（-140〜-170 など）ごとに員数の欄が分かれた図面。見出しの段に枝番の見出し（140 150…）がある。"""
        items, vlines = tpl_table(0, 0, [
            (1, "P/X", "ﾃｽﾄｶﾗｰ-40×17×0.5", "1", "", "050", "", "1930", ""),
            ("２", "P/X", "ﾃｽﾄｶﾗｰ-40×17×0.5", "", "", "050", "", "1900", ""),  # 全角の行番号、1 つ目の枝番には使わない
        ], 4)
        items += [t("140", 136.5, 3.0, 3.2), t("150", 145.5, 3.0, 3.2), t("160", 151.5, 3.0, 3.2), t("170", 157.5, 3.0, 3.2)]
        items += [t("2", 145.5, 7.9, 3.2), t("1", 157.5, 7.9, 3.2), t("1", 151.5, 15.9, 3.2)]
        rows = bom.extract(items, vlines)
        self.assertEqual([r["item_no"] for r in rows], ["1", "2"])
        self.assertEqual((rows[0]["qty"], rows[0]["note"]), ("1", "枝番別の員数: 1 / 2 / - / 1"))
        self.assertEqual((rows[1]["qty"], rows[1]["note"]), ("1", "枝番別の員数: - / - / 1"))
        self.assertEqual((rows[0]["thickness"], rows[0]["length"]), ("050", "1930"))  # 見出しの段が崩れない


def as_ocr(items, vlines):
    """様式の表の文字を、OCR の結果のような形にする：同じ高さの文字を 1 つのまとまりにし、語ごとの位置を tokens に。
    （Vision は隣り合う欄の文字を 1 つのまとまりとして返すことがある）"""
    from dwgworker import bom as b
    out = []
    for ln in b._lines(items):
        toks = []
        for it in ln["items"]:
            w = b._width(it["text"], it["h"])
            for n, word in enumerate(it["text"].split()):
                toks.append({"text": word, "x": it["x"] + n * 0.01, "y": it["y"], "w": w, "h": it["h"]})
        out.append({"text": " ".join(t["text"] for t in toks), "x": toks[0]["x"], "y": ln["y"], "h": ln["items"][0]["h"],
                    "tokens": toks})
    return out


class BomOcrTests(unittest.TestCase):
    def test_ocr_lines_are_split_by_rules(self):
        items, vlines = tpl_drawing()
        ocr = as_ocr(items, vlines)
        self.assertTrue(any(len(o["tokens"]) > 5 for o in ocr))  # 行全体が 1 つのまとまり
        rows = bom.extract(bom.cells_from_ocr(ocr, vlines), vlines)
        self.assertEqual([r["item_no"] for r in rows], ["1", "2", "3", "4", "5", "6"])
        r = {x["item_no"]: x for x in rows}
        self.assertEqual((r["2"]["part_no"], r["2"]["material"], r["2"]["length"]), ("P/L", "0C1", "2438"))
        self.assertEqual(r["1"]["ref_drawing_no"], "ZZAB100010")

    def test_dotted_part_number(self):
        items, vlines = tpl_table(0, 0, [(1, "H.C.Z.I.D.9.0.0.2.4", "カンキセン トリツケ", "1", "", "", "", "", "")], 2)
        rows = bom.extract(items, vlines)
        self.assertEqual((rows[0]["part_no"], rows[0]["ref_drawing_no"]), ("HCZID90024", "HCZID90024"))

    def test_image_vertical_lines(self):
        import numpy as np
        from dwgworker import imagelines
        img = np.full((3508, 4960), 255, np.uint8)
        img[2000:3000, 1000:1003] = 0
        for r in range(2000, 3000):  # 少し傾いた線
            c = 2000 + (r - 2000) * 8 // 1000
            img[r, c:c + 3] = 0
        img[2500:2520, 3000:3002] = 0  # 文字の画（短い）は線にしない
        ls = imagelines.vertical_lines(img, 1190.55, 841.89)
        self.assertEqual([round(x) for x, _, _ in ls], [240, 481])
        self.assertAlmostEqual(ls[0][2] - ls[0][1], 240.0, delta=1)


def scan_drawing():
    """スキャン図面（実スキャン 3 件で確かめた配置、文字は架空）：OCR の結果（tokens 付き）と、画像から見つけた縦の線。
    見出しは OCR の読み違いあり（図番又は品番 → 図番双は&番、員・数は枝番の欄に分かれる、名称/規格 → 名 株 規 格）。"""
    H = 8.6
    def ocr(text, x, y):
        return {"text": text, "x": x, "y": y, "h": H, "tokens": [{"text": text, "x": x, "y": y, "w": len(text) * 5.0, "h": H}]}
    items = [
        ocr("1見出", 54, 120.9), ocr("特", 237, 121), ocr("記", 307, 119.8), ocr("厚さ", 452, 120.9), ocr("幅（直径）", 477, 119.3),
        ocr("1長さ「質量｜計", 512, 119.7), ocr("｜番号", 55, 111.2), ocr("図番双は&番", 95, 114.9), ocr("名", 237, 111.2),
        ocr("株", 253, 110), ocr("規", 290, 111.2), ocr("格", 308, 111.2), ocr("員", 375, 108.8), ocr("数", 404, 108.8),
        ocr("材質", 431, 114.9), ocr("材", 472, 108.8), ocr("料", 494, 108.8), ocr("寸", 517, 111.2), ocr("法", 539, 110),
        # 行（下から）：行番号の「I」「03」、品番の点線、質量は右端
        ocr("I", 52, 130.9), ocr("ZZ.AB.1.0.3.1.4.0", 67, 128.5), ocr("テストパネル", 186, 128.3), ocr("40.75", 540, 127.1),
        ocr("2", 58, 147), ocr("ZZ1D90061", 67, 143.3), ocr("テストワク/20cm", 187, 145.4), ocr("12.25", 540, 142.8),
        ocr("03", 51, 162.7), ocr("ZZAB.50.0.040", 66, 159.1), ocr("テストダイ", 186, 161.1),
        ocr("4", 58, 178.8), ocr("0.0.1.66.082.5.0", 67, 176.2), ocr("テストボルト/M8x25", 185, 175.9), ocr("6", 375, 178.5),
        ocr("C02", 432, 176), ocr("160", 452, 176), ocr("7900", 478, 176), ocr("1440", 514, 176),
        # 表の上の注記（表の外）
        ocr("注記：テスト用の注記。", 214, 301.9),
    ]
    major = [49.9, 55.9, 67.8, 186.5, 364.8, 430.0, 447.8]         # 見出しの下の段まで下りる線
    upper = [382.4, 394.4, 406.3, 418.1, 477.5, 513.2]             # 見出しの上の段から始まる線（員数の枝番、寸法の区切り）
    vlines = [(x, 109.4, 289.6) for x in major] + [(x, 119.97, 289.1) for x in upper]
    vlines += [(127.3, 130.8, 289.6), (571.2, 20.4, 744.8)]        # 品番の欄の中の線、図面の外枠（表の右端）
    return items, vlines


class BomScanTests(unittest.TestCase):
    def test_scan_grid_with_misread_header(self):
        items, vlines = scan_drawing()
        rows = bom.extract(bom.cells_from_ocr(items, vlines), vlines)
        got = [(r["item_no"], r["part_no"], r["ref_drawing_no"]) for r in rows]
        self.assertEqual(got, [("1", "ZZAB103140", "ZZAB103140"), ("2", "ZZID90061", "ZZID90061"),
                               ("3", "ZZAB500040", "ZZAB500040"), ("4", "0016608250", "0016608250")])
        r4 = rows[3]
        self.assertEqual((r4["name"], r4["qty"], r4["material"], r4["thickness"], r4["width"], r4["length"]),
                         ("テストボルト/M8x25", "6", "C02", "160", "7900", "1440"))
        self.assertEqual(rows[0]["length"], "")  # 右端の質量は長さに入れない
        self.assertFalse(any("注記" in r["raw_text"] for r in rows))

    def test_scan_variants_from_real_samples(self):
        """2026-10-02 の実スキャン 8 件で見つかった崩れ：
        ・員数の枝番の線が表の上まで伸びている（図の線とつながって見える）→ 列の区切りに数える
        ・見出しの上の段から始まる線の方が多い → 表の下端は下の段
        ・長い品番が名称の欄にはみ出し、OCR が品番と名称を 1 つにする → カナで分ける
        ・見出しが読めていても、員数の欄の位置（読み直し用）が取れる"""
        items, vlines = scan_drawing()
        H = 8.6
        vlines = [(x, a, 330.0) if abs(x - 394.4) < 0.1 else (x, a, b) for x, a, b in vlines]   # 枝番の線が上に伸びる
        vlines += [(x, 119.97, 289.1) for x in (460.0, 495.0, 530.0, 545.0)]                    # 上の段から始まる線を増やす
        items = [i for i in items if i["text"] != "図番双は&番"]
        long = "6064-B1-2HD79618-UK#テストドア/ブラウン"
        items += [{"text": "図番又は品番", "x": 95, "y": 114.9, "h": H,
                   "tokens": [{"text": "図番又は品番", "x": 95, "y": 114.9, "w": 60, "h": H}]},
                  {"text": "5", "x": 58, "y": 194.4, "h": H, "tokens": [{"text": "5", "x": 58, "y": 194.4, "w": 5, "h": H}]},
                  {"text": long, "x": 67, "y": 192.0, "h": H, "tokens": [{"text": long, "x": 67, "y": 192.0, "w": 250, "h": H}]}]
        info = {}
        rows = bom.extract(bom.cells_from_ocr(items, vlines), vlines, info)
        self.assertEqual([r["item_no"] for r in rows], ["1", "2", "3", "4", "5"])
        self.assertEqual((rows[3]["part_no"], rows[3]["qty"]), ("0016608250", "6"))
        self.assertEqual((rows[4]["part_no"], rows[4]["name"]), ("6064-B1-2HD79618-UK#", "テストドア/ブラウン"))
        q = info["qty"][0]
        self.assertAlmostEqual(q["x0"], 364.8, delta=1)
        self.assertAlmostEqual(q["x1"], 430.0, delta=1)
        self.assertEqual(len(q["rows"]), 5)
        self.assertLess(q["h"], 9.0)

    def test_ocr_code_fixes(self):
        self.assertEqual(bom.ocr_code("HC.Z.1.D.9.0.0.6.1"), "HCZID90061")
        self.assertEqual(bom.ocr_code("0031008.0.0.1"), "0031008001")
        self.assertEqual(bom.ocr_code("HUXPO10143"), "HUXP010143")
        self.assertEqual(bom.ocr_code("0277-NO.510X18W"), "0277-NO.510X18W")  # 区切りが 1 つなら触らない
        self.assertEqual([bom.ocr_item_no(x) for x in ("I", "03", "|", "12")], ["1", "3", "1", "12"])

    def test_scan_assembly_ref(self):
        items = [{"text": "H.U.L.0.0,1.5,0,9.01", "x": 45.2, "y": 52.5, "h": 8.6},
                 {"text": "組立図番", "x": 52.5, "y": 42.7, "h": 8.6}, {"text": "員数", "x": 97.8, "y": 42.7, "h": 8.6}]
        self.assertEqual(bom.assembly_refs(items, "HULP003160", ocr=True), ["HUL0015090"])
