"""PDF の解析：テキスト層（なければ OCR）の文字を座標付きで取り出し、表題欄を読み、サムネイルを作る。

2026-09-29 の実図面確認では、図面の PDF はほぼすべてコピー機（RICOH）で取り込んだ画像だけの PDF だったため、
Mac mini では macOS Vision で OCR する。
「図面一式」のように 1 つの PDF の各ページが別の図面（別の図番）になっているものは、ページごとに別の図面として返す。
"""
from __future__ import annotations

import logging
import time

import pymupdf

from . import ocr_mac, titleblock
from .config import CONFIG

log = logging.getLogger("dwgworker")
MIN_CHARS_PER_PAGE = 30
# 表題欄を探す範囲（ページ右下。幅・高さに対する比率）
TB_X_MIN, TB_Y_MAX = 0.55, 0.22
SIMPLE_FIELDS = ("drawing_no", "revision", "title", "material", "scale", "drawn_date")
EXTRA_FIELDS = ("model", "sheet_title", "sheet_no", "job_no", "file_title")


def _text_items(page) -> list[dict]:
    height = page.rect.height
    items = []
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            t = "".join(s["text"] for s in line["spans"]).strip()
            if not t:
                continue
            x0, y0, x1, y1 = line["bbox"]
            for part in (t.split("  ") if "  " in t else [t]):
                items.append({"text": part.strip(), "x": x0, "y": height - y1, "h": y1 - y0})
    return items


def title_block_codes(items: list[dict], page) -> list[str]:
    """ページ右下（表題欄）にある図番らしい文字列。下にあるものほど先（表題欄の図番欄は最下段）。"""
    W, H = page.rect.width, page.rect.height
    found = []
    for it in sorted(items, key=lambda i: i["y"]):
        if it["x"] >= W * TB_X_MIN and it["y"] <= H * TB_Y_MAX:
            for c in titleblock.find_codes(it["text"]):
                if c not in found:
                    found.append(c)
    return found


def _thumb(page) -> bytes:
    zoom = CONFIG.thumb_width / max(page.rect.width, page.rect.height, 1)
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")


def _read_page(page, path, i, budget) -> dict:
    text = page.get_text("text")
    items = _text_items(page)
    source, secs = "text", 0.0
    scanned = len(text.strip()) < MIN_CHARS_PER_PAGE
    if scanned and CONFIG.ocr_enabled and ocr_mac.available() and budget > 0:
        t0 = time.time()
        try:
            items = ocr_mac.ocr_page(page, dpi=CONFIG.ocr_dpi)
            text = "\n".join(ocr_mac.lines(items))
            source = "ocr"
        except Exception as e:
            log.warning("OCR に失敗 %s p%d: %s", path, i + 1, e)
        secs = time.time() - t0
    text = titleblock.compact_codes(titleblock.norm(text))
    codes = titleblock.find_codes(text)
    if codes:
        text += "\n[図番・品番] " + " ".join(codes)
    return {"page_no": i + 1, "text": text, "text_source": source, "items": items, "scanned": scanned,
            "tb_codes": title_block_codes(items, page), "seconds": secs}


def _drawing(fields, source, conf, needs_ocr, attrs):
    return {**{k: fields.get(k, "") for k in SIMPLE_FIELDS}, "source": source, "confidence": conf, "needs_ocr": needs_ocr,
            "attributes": {**attrs, **{k: fields[k] for k in EXTRA_FIELDS if fields.get(k)}}}


def parse(data: bytes, path: str) -> tuple[dict, dict[int, bytes]]:
    """({"drawings": [...]}, {ページ番号: サムネイルPNG}) を返す。"""
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = []
    ocr_left = CONFIG.ocr_max_pages
    for i, page in enumerate(doc):
        p = _read_page(page, path, i, ocr_left)
        if p["text_source"] == "ocr":
            ocr_left -= 1
        pages.append(p)
    common = {"pages": len(doc), "producer": doc.metadata.get("producer", ""),
              "scanned_pages": sum(p["scanned"] for p in pages),
              "ocr_pages": sum(p["text_source"] == "ocr" for p in pages),
              "ocr_seconds": round(sum(p["seconds"] for p in pages), 1)}
    fields, source, conf = titleblock.resolve(path, [], pages[0]["items"] if pages else [], base_source="text")
    any_ocr = common["ocr_pages"] > 0
    needs_ocr = common["scanned_pages"] > common["ocr_pages"]
    if (any_ocr or needs_ocr) and source not in ("filename",):
        source = "ocr"

    # 図面一式：2 ページ以上で表題欄に図番があれば、ページごとに別の図面にする
    split = len(pages) > 1 and sum(1 for p in pages if p["tb_codes"]) >= 2
    drawings, thumbs = [], {}
    if split:
        for p in pages:
            f = dict(fields)
            f["drawing_no"] = p["tb_codes"][0] if p["tb_codes"] else ""
            pf, _, _ = titleblock.resolve("", [], p["items"])  # そのページの表題欄の名称・尺度など
            for k in ("title", "material", "scale", "drawn_date", "sheet_title"):
                f[k] = pf.get(k, "")
            if not f["title"]:
                f["title"] = f"{fields.get('title', '')}（{p['page_no']}/{len(pages)}）".strip()
            f["file_title"] = fields.get("title", "")
            pconf = 0.75 if p["tb_codes"] else 0.4
            attrs = {**common, "page": p["page_no"], "tb_codes": p["tb_codes"][:5]}
            drawings.append({"page_no": p["page_no"],
                             "drawing": _drawing(f, "ocr" if p["text_source"] == "ocr" else source, pconf,
                                                 p["scanned"] and p["text_source"] != "ocr", attrs),
                             "pages": [{"page_no": p["page_no"], "text": p["text"], "text_source": p["text_source"]}],
                             "bom": []})
            thumbs[p["page_no"]] = _thumb(doc[p["page_no"] - 1])
    else:
        if not fields["drawing_no"] and pages and pages[0]["tb_codes"]:  # ファイル名に図番がなければ表題欄の図番
            fields["drawing_no"] = pages[0]["tb_codes"][0]
            conf = 0.75
        attrs = {**common, "tb_codes": pages[0]["tb_codes"][:5] if pages else []}
        drawings.append({"page_no": 1, "drawing": _drawing(fields, source, conf, needs_ocr, attrs),
                         "pages": [{"page_no": p["page_no"], "text": p["text"], "text_source": p["text_source"]} for p in pages],
                         "bom": []})
        if len(doc):
            thumbs[1] = _thumb(doc[0])
    return {"drawings": drawings, "items": pages[0]["items"] if pages else []}, thumbs
