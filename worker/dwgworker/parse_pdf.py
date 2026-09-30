"""PDF の解析：テキスト層（なければ OCR）の文字を座標付きで取り出し、表題欄を読み、サムネイルを作る。

2026-09-29 の実図面確認では、図面の PDF はほぼすべてコピー機（RICOH）で取り込んだ画像だけの PDF だったため、
Mac mini では macOS Vision で OCR する。
「図面一式」のように 1 つの PDF の各ページが別の図面（別の図番）になっているものは、ページごとに別の図面として返す。
"""
from __future__ import annotations

import logging
import time

import pymupdf

from . import bom, imagelines, ocr_mac, titleblock
from .config import CONFIG

log = logging.getLogger("dwgworker")
MIN_CHARS_PER_PAGE = 30
# 表題欄を探す範囲（ページ右下。幅・高さに対する比率）
TB_X_MIN, TB_Y_MAX = 0.55, 0.22
SIMPLE_FIELDS = ("drawing_no", "revision", "title", "material", "scale", "drawn_date")
EXTRA_FIELDS = ("model", "sheet_title", "sheet_no", "job_no", "file_title")


def _text_items(page, vlines=None) -> list[dict]:
    """テキスト層の文字を座標付きで（y は文字の基準線、上向き）。CAD から書き出した PDF は、隣り合う欄の文字が
    1 つのかたまりにまとめられていることがあるので、文字と文字の間が空いているところで分ける。"""
    height = page.rect.height
    items = []
    for b in page.get_text("rawdict")["blocks"]:
        for line in b.get("lines", []):
            for sp in line["spans"]:
                size = sp.get("size") or 1.0
                base = sp.get("origin", (0, 0))[1]
                piece, x0, last = [], None, None
                for ch in sp.get("chars", []):
                    c = ch["c"]
                    cx0, _, cx1, _ = ch["bbox"]
                    gap = cx0 - last if last is not None else 0
                    y = height - base
                    ruled = last is not None and any(last - size * 0.3 <= vx <= (cx0 + cx1) / 2 and a <= y + size * 0.3 <= b
                                                     for vx, a, b in (vlines or ()))
                    if (gap > size * 0.6 or ruled) and piece:  # 間が空いている・罫線がある＝別の文字（隣の欄）
                        t = "".join(piece).strip()
                        if t:
                            items.append({"text": t, "x": x0, "y": height - base, "h": size})
                        piece, x0 = [], None
                    if x0 is None and not c.isspace():
                        x0 = cx0
                    if x0 is not None:
                        piece.append(c)
                    last = cx1
                t = "".join(piece).strip()
                if t:
                    items.append({"text": t, "x": x0, "y": height - base, "h": size})
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


def _vector_vlines(page) -> list[tuple[float, float, float]]:
    """CAD から書き出した PDF の縦の線（部品表の罫線）。ページ座標（pt、y 上向き）。"""
    H = page.rect.height
    out = []
    try:
        for path in page.get_drawings():
            for it in path.get("items", []):
                if it[0] == "l":
                    a, b = it[1], it[2]
                    if abs(a.x - b.x) < 0.3 and abs(a.y - b.y) > 0.5:
                        out.append((float(a.x), float(H - max(a.y, b.y)), float(H - min(a.y, b.y))))
                elif it[0] == "re":
                    r = it[1]
                    for x in (r.x0, r.x1):
                        out.append((float(x), float(H - r.y1), float(H - r.y0)))
    except Exception as e:  # noqa: BLE001
        log.warning("PDF の線を読めません: %s", e)
    return out


def _read_page(page, path, i, budget, want_lines=False) -> dict:
    """want_lines：部品表を読むとき、罫線（縦の線）も集める。"""
    text = page.get_text("text")
    source, secs = "text", 0.0
    vlines = []
    scanned = len(text.strip()) < MIN_CHARS_PER_PAGE
    if not scanned and want_lines:
        vlines = _vector_vlines(page)
    items = _text_items(page, vlines)
    if scanned and CONFIG.ocr_enabled and ocr_mac.available() and budget > 0:
        t0 = time.time()
        try:
            pix = ocr_mac.render(page, dpi=CONFIG.ocr_dpi)
            items = ocr_mac.ocr_page(page, pix=pix)
            text = "\n".join(ocr_mac.lines(items))
            source = "ocr"
            if want_lines:
                vlines = imagelines.from_pixmap(pix, page.rect.width, page.rect.height)
        except Exception as e:
            log.warning("OCR に失敗 %s p%d: %s", path, i + 1, e)
        secs = time.time() - t0
    text = titleblock.compact_codes(titleblock.norm(text))
    codes = titleblock.find_codes(text)
    if codes:
        text += "\n[図番・品番] " + " ".join(codes)
    return {"page_no": i + 1, "text": text, "text_source": source, "items": items, "scanned": scanned,
            "tb_codes": title_block_codes(items, page), "seconds": secs, "vlines": vlines}


def _drawing(fields, source, conf, needs_ocr, attrs):
    return {**{k: fields.get(k, "") for k in SIMPLE_FIELDS}, "source": source, "confidence": conf, "needs_ocr": needs_ocr,
            "attributes": {**attrs, **{k: fields[k] for k in EXTRA_FIELDS if fields.get(k)}}}


def _bom(items, own_no, force, vlines=None):
    """部品表と注記の参照図番。スキャン PDF の部品表は試験中のため、BOM_PDF=1 か force のときだけ。"""
    is_ocr = any(i.get("tokens") is not None for i in items)
    refs = {"note_refs": bom.note_refs([i["text"] for i in items], own_no),
            "assembly_refs": bom.assembly_refs(items, own_no, ocr=is_ocr)}
    refs = {k: v for k, v in refs.items() if v}
    if not (CONFIG.bom_pdf or force):
        return [], refs
    try:
        return bom.extract(bom.cells_from_ocr(items, vlines or []), vlines or []), refs
    except Exception as e:  # noqa: BLE001
        log.warning("部品表の読み取りに失敗: %s", e)
        return [], refs


def parse(data: bytes, path: str, force_bom: bool = False, debug: dict | None = None) -> tuple[dict, dict[int, bytes]]:
    """({"drawings": [...]}, {ページ番号: サムネイルPNG}) を返す。
    debug に dict を渡すと、ページごとの OCR の結果（語ごとの位置）と罫線を入れて返す（調整用）。"""
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = []
    ocr_left = CONFIG.ocr_max_pages
    for i, page in enumerate(doc):
        p = _read_page(page, path, i, ocr_left, want_lines=CONFIG.bom_pdf or force_bom)
        if p["text_source"] == "ocr":
            ocr_left -= 1
        pages.append(p)
        if debug is not None:
            debug.setdefault("pages", []).append({"page_no": p["page_no"], "w": page.rect.width, "h": page.rect.height,
                                                  "text_source": p["text_source"], "items": p["items"],
                                                  "vlines": p["vlines"]})
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
    # （表題欄に図番がないページが大半の資料・申請図書などは、分けずに 1 件の資料として扱う）
    with_codes = sum(1 for p in pages if p["tb_codes"])
    split = len(pages) > 1 and with_codes >= 2 and with_codes >= len(pages) * 0.5
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
            rows, refs = _bom(p["items"], f["drawing_no"], force_bom, p["vlines"])
            attrs = {**common, "page": p["page_no"], "tb_codes": p["tb_codes"][:5], **refs}
            drawings.append({"page_no": p["page_no"],
                             "drawing": _drawing(f, "ocr" if p["text_source"] == "ocr" else source, pconf,
                                                 p["scanned"] and p["text_source"] != "ocr", attrs),
                             "pages": [{"page_no": p["page_no"], "text": p["text"], "text_source": p["text_source"]}],
                             "bom": rows})
            thumbs[p["page_no"]] = _thumb(doc[p["page_no"] - 1])
    else:
        if not fields["drawing_no"] and pages and pages[0]["tb_codes"]:  # ファイル名に図番がなければ表題欄の図番
            fields["drawing_no"] = pages[0]["tb_codes"][0]
            conf = 0.75
        rows, refs = _bom(pages[0]["items"] if pages else [], fields.get("drawing_no", ""), force_bom,
                          pages[0]["vlines"] if pages else [])
        attrs = {**common, "tb_codes": pages[0]["tb_codes"][:5] if pages else [], **refs}
        drawings.append({"page_no": 1, "drawing": _drawing(fields, source, conf, needs_ocr, attrs),
                         "pages": [{"page_no": p["page_no"], "text": p["text"], "text_source": p["text_source"]} for p in pages],
                         "bom": rows})
        if len(doc):
            thumbs[1] = _thumb(doc[0])
    return {"drawings": drawings, "items": pages[0]["items"] if pages else []}, thumbs
