"""動作確認用の架空の図面を作る（実際の図面が届くまでの仮データ）。

  python samples/make_samples.py samples/out

・属性付き表題欄の DXF（基本ハウス・派生）
・ラベルと値を文字で書いただけの DXF
・テキスト層つき PDF（CAD出力を想定）
・文字のない PDF（スキャン図面を想定 → OCR待ちになる）
"""
import sys
from pathlib import Path

import ezdxf
import pymupdf

BOM_BASE = [
    ("1", "UF-3600", "床フレーム 3600", "1", "SS400", "UH-3601"),
    ("2", "UC-2400", "コーナー柱 H2400", "4", "STKR400", "UH-3602"),
    ("3", "WP-900", "外壁パネル（無窓）", "6", "GL鋼板", ""),
    ("4", "AS-1812", "アルミサッシ 1800×1200", "2", "AL", ""),
    ("5", "DR-800", "スチールドア W800", "1", "SPCC", ""),
]


def title_block_def(doc):
    blk = doc.blocks.new("TITLE_BLOCK")
    blk.add_lwpolyline([(0, 0), (180, 0), (180, 40), (0, 40)], close=True)
    for y in (10, 20, 30):
        blk.add_line((0, y), (180, y))
    blk.add_line((90, 0), (90, 40))
    for tag, x, y in [("図番", 5, 32), ("REV", 95, 32), ("品名", 5, 22), ("材質", 95, 22), ("尺度", 5, 12), ("日付", 95, 12)]:
        blk.add_text(tag, height=2.5, dxfattribs={"insert": (x, y + 4)})
        blk.add_attdef(tag, (x + 20, y), dxfattribs={"height": 3.5})
    return blk


def house_dxf(path, no, rev, title, w=3600, windows=2, bom=BOM_BASE, note=""):
    doc = ezdxf.new("R2018", setup=True)
    doc.styles.add("JP", font="msgothic.ttc")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (w, 0), (w, 2400), (0, 2400)], close=True, dxfattribs={"const_width": 60})
    for i in range(windows):
        x = w * (i + 1) / (windows + 1)
        msp.add_line((x - 500, 0), (x + 500, 0), dxfattribs={"color": 5})
    msp.add_text(f"{w}", height=120, dxfattribs={"insert": (w / 2, -300)})
    if note:
        msp.add_text(note, height=100, dxfattribs={"insert": (0, 2700)})
    # 部品表
    x0, y0 = w + 400, 2400
    for r, row in enumerate([("No.", "品番", "品名", "数量", "材質", "図番")] + list(bom)):
        for c, (val, dx) in enumerate(zip(row, (0, 200, 900, 2300, 2600, 3200))):
            if val:
                msp.add_text(val, height=80, dxfattribs={"insert": (x0 + dx, y0 - r * 150)})
    title_block_def(doc)
    ins = msp.add_blockref("TITLE_BLOCK", (w + 400, -1200), dxfattribs={"xscale": 20, "yscale": 20})
    ins.add_auto_attribs({"図番": no, "REV": rev, "品名": title, "材質": "—", "尺度": "1:50", "日付": "2025-10-06"})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)


def plain_label_dxf(path):
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_circle((0, 0), 50)
    for label, val, y in [("図番", "UH-3602", 0), ("品名", "コーナー柱 H2400", -10), ("材質", "STKR400", -20)]:
        msp.add_text(label, height=4, dxfattribs={"insert": (100, y)})
        msp.add_text(val, height=4, dxfattribs={"insert": (125, y)})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)


def text_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)  # A3 横（pt）
    page.draw_rect(pymupdf.Rect(20, 20, 1170, 822), width=1.5)
    page.draw_rect(pymupdf.Rect(200, 200, 700, 520), width=3)
    lines = [("図番：UH-3600A", 860, 740), ("品名：ユニットハウス 3600 寒冷地仕様", 860, 760),
             ("材質：—", 860, 780), ("尺度：1:50", 1040, 780), ("REV：A", 1040, 740)]
    for t, x, y in lines:
        page.insert_text((x, y), t, fontname="japan", fontsize=11)
    page.insert_text((860, 200), "部品表  UF-3600 床フレーム 3600  1  SS400", fontname="japan", fontsize=9)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def scanned_pdf(path):
    src = pymupdf.open()
    p = src.new_page(width=1190, height=842)
    p.draw_rect(pymupdf.Rect(20, 20, 1170, 822), width=1.5)
    p.insert_text((860, 760), "UH-4200 休憩所", fontname="japan", fontsize=14)
    pix = p.get_pixmap(dpi=100)
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=842)
    page.insert_image(page.rect, stream=pix.tobytes("png"))  # 画像だけ＝文字のないスキャン図面
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def main(out):
    out = Path(out)
    house_dxf(out / "design/UH/UH-3600/UH-3600_C.dxf", "UH-3600", "C", "ユニットハウス 3600 事務所仕様 組立図")
    house_dxf(out / "design/UH/UH-3600/派生/UH-3600-01_A.dxf", "UH-3600-01", "A", "ユニットハウス 3600 トイレ付事務所",
              bom=BOM_BASE + [("6", "TL-01", "洋式トイレユニット", "1", "", "")])
    house_dxf(out / "design/UH/UH-3600/派生/UH-3600-02_A.dxf", "UH-3600-02", "A", "ユニットハウス 3600 倉庫仕様", windows=0,
              bom=[b for b in BOM_BASE if b[1] != "AS-1812"] + [("6", "SH-2400", "軽量シャッター W2400", "1", "GL鋼板", "")])
    house_dxf(out / "案件/2025/B建設様/UH3600_喫煙所.dxf", "", "", "", note="喫煙所 UH-3600を基に換気扇2台追加")
    plain_label_dxf(out / "design/UH/parts/UH-3602_A.dxf")
    text_pdf(out / "design/UH/UH-3600/UH-3600A_A.pdf")
    scanned_pdf(out / "scan/2024/UH-4200_B.pdf")
    for p in sorted(out.rglob("*.*")):
        print(p.relative_to(out))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples/out")
