"""PDF の解析：テキスト層（なければ OCR）の文字を座標付きで取り出し、表題欄を読み、1 ページ目の PNG サムネイルを作る。

2026-09-29 の実図面確認では「図面原紙･資料 PDF」「図面 … PDF」はほぼすべてコピー機（RICOH）で
取り込んだ画像だけの PDF だったため、Mac mini では macOS Vision で OCR する。
1 つの PDF に複数の図面（ページごとに別の図番）が入っている「図面一式」もある。
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


def parse(data: bytes, path: str) -> tuple[dict, bytes | None]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages, first_items = [], []
    scanned = ocr_pages = 0
    page_codes = {}
    ocr_seconds = 0.0
    for i, page in enumerate(doc):
        text = page.get_text("text")
        items = _text_items(page)
        source = "text"
        if len(text.strip()) < MIN_CHARS_PER_PAGE:
            scanned += 1
            if CONFIG.ocr_enabled and ocr_mac.available() and ocr_pages < CONFIG.ocr_max_pages:
                t0 = time.time()
                try:
                    items = ocr_mac.ocr_page(page, dpi=CONFIG.ocr_dpi)
                    text = "\n".join(it["text"] for it in items)
                    source = "ocr"
                    ocr_pages += 1
                except Exception as e:
                    log.warning("OCR に失敗 %s p%d: %s", path, i + 1, e)
                ocr_seconds += time.time() - t0
        text = titleblock.compact_codes(titleblock.norm(text))
        codes = title_block_codes(items, page)
        if codes:
            page_codes[i + 1] = codes
        all_codes = titleblock.find_codes(text)
        if all_codes:
            text += "\n[図番・品番] " + " ".join(all_codes)
        pages.append({"page_no": i + 1, "text": text, "text_source": source})
        if i == 0:
            first_items = items
    fields, source, conf = titleblock.resolve(path, [], first_items, base_source="text")
    needs_ocr = scanned > ocr_pages
    if ocr_pages:
        if source != "filename":
            source = "ocr"
        if not fields["drawing_no"] and page_codes.get(1):  # ファイル名に図番がなければ表題欄の最下段の図番
            fields["drawing_no"] = page_codes[1][0]
            conf = min(conf, 0.75) if conf > 0.3 else 0.7
    elif needs_ocr and source != "filename":
        source = "ocr"
    thumb = None
    if len(doc):
        p0 = doc[0]
        zoom = CONFIG.thumb_width / max(p0.rect.width, p0.rect.height, 1)
        thumb = p0.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    result = {
        "drawing": {**{k: fields[k] for k in ("drawing_no", "revision", "title", "material", "scale", "drawn_date")},
                    "source": source, "confidence": conf, "needs_ocr": needs_ocr,
                    "attributes": {"pages": len(doc), "scanned_pages": scanned, "ocr_pages": ocr_pages,
                                   "ocr_seconds": round(ocr_seconds, 1), "producer": doc.metadata.get("producer", ""),
                                   "page_codes": page_codes,
                                   **{k: fields[k] for k in ("model", "sheet_title", "sheet_no", "job_no", "file_title") if fields[k]}}},
        "pages": pages,
        "bom": [],
        "items": first_items,
    }
    return result, thumb
