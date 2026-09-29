"""PDF の解析：テキスト層を座標付きで取り出し、表題欄を読み、1 ページ目の PNG サムネイルを作る。
文字がほとんど取れないページはスキャン図面とみなし、OCR 待ちにする（OCR は Phase 3）。"""
from __future__ import annotations

import pymupdf

from . import titleblock
from .config import CONFIG

MIN_CHARS_PER_PAGE = 30


def parse(data: bytes, path: str) -> tuple[dict, bytes | None]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages, items = [], []
    scanned = 0
    for i, page in enumerate(doc):
        text = page.get_text("text")
        if len(text.strip()) < MIN_CHARS_PER_PAGE:
            scanned += 1
        pages.append({"page_no": i + 1, "text": text, "text_source": "text"})
        if i == 0:
            height = page.rect.height
            for b in page.get_text("dict")["blocks"]:
                for line in b.get("lines", []):
                    t = "".join(s["text"] for s in line["spans"]).strip()
                    if not t:
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    for part in (t.split("  ") if "  " in t else [t]):
                        items.append({"text": part.strip(), "x": x0, "y": height - y1, "h": y1 - y0})
    fields, source, conf = titleblock.resolve(path, [], items, base_source="text")
    needs_ocr = scanned == len(doc) and len(doc) > 0
    if needs_ocr and source != "filename":
        source = "ocr"
    thumb = None
    if len(doc):
        p0 = doc[0]
        zoom = CONFIG.thumb_width / max(p0.rect.width, 1)
        thumb = p0.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    result = {
        "drawing": {**fields, "source": source, "confidence": conf, "needs_ocr": needs_ocr,
                    "attributes": {"pages": len(doc), "scanned_pages": scanned, "producer": doc.metadata.get("producer", "")}},
        "pages": pages,
        "bom": [],
        "items": items,
    }
    return result, thumb
