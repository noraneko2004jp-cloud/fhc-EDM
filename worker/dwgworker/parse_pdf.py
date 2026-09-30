"""PDF の解析：テキスト層（なければ OCR）の文字を座標付きで取り出し、表題欄を読み、サムネイルを作る。

2026-09-29 の実図面確認では、図面の PDF はほぼすべてコピー機（RICOH）で取り込んだ画像だけの PDF だったため、
Mac mini では macOS Vision で OCR する。
「図面一式」のように 1 つの PDF の各ページが別の図面（別の図番）になっているものは、ページごとに別の図面として返す。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from pathlib import Path

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


OCR_CACHE_VERSION = 1


def _cache_file(key: str | None, i: int) -> Path | None:
    if not key or not CONFIG.ocr_cache_dir:
        return None
    return Path(CONFIG.ocr_cache_dir) / key[:2] / f"{key}-p{i + 1}-{CONFIG.ocr_dpi}.json.gz"


def _cache_load(f: Path | None):
    if f is None or not f.exists():
        return None
    try:
        d = json.loads(gzip.decompress(f.read_bytes()))
        if d.get("v") == OCR_CACHE_VERSION:
            return d
    except Exception as e:  # noqa: BLE001  壊れた保存は無視して OCR し直す
        log.warning("OCR の保存を読めません %s: %s", f, e)
    return None


def _cache_save(f: Path | None, items, vlines) -> None:
    if f is None:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(json.dumps({"v": OCR_CACHE_VERSION, "items": items, "vlines": vlines},
                                                 ensure_ascii=False).encode()))
        tmp.replace(f)
    except Exception as e:  # noqa: BLE001  保存できなくても解析は続ける
        log.warning("OCR の結果を保存できません %s: %s", f, e)


def _read_page(page, path, i, budget, want_lines=False, key=None) -> dict:
    """want_lines：部品表を読むとき、罫線（縦の線）も集める。
    key：PDF の中身のハッシュ。OCR の結果を保存・再利用する（OCR したページは罫線も一緒に保存する）。"""
    text = page.get_text("text")
    source, secs = "text", 0.0
    vlines = []
    scanned = len(text.strip()) < MIN_CHARS_PER_PAGE
    if not scanned and want_lines:
        vlines = _vector_vlines(page)
    items = _text_items(page, vlines)
    if scanned and CONFIG.ocr_enabled and budget > 0:
        cf = _cache_file(key, i)
        cached = _cache_load(cf)
        if cached is not None:
            items, vlines = cached["items"], [tuple(v) for v in cached["vlines"]]
            text = "\n".join(ocr_mac.lines(items))
            source = "ocr"
        elif ocr_mac.available():
            t0 = time.time()
            try:
                pix = ocr_mac.render(page, dpi=CONFIG.ocr_dpi)
                items = ocr_mac.ocr_page(page, pix=pix)
                text = "\n".join(ocr_mac.lines(items))
                source = "ocr"
                vlines = imagelines.from_pixmap(pix, page.rect.width, page.rect.height)
                _cache_save(cf, items, vlines)
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


QTY_FILL_OK = 0.8  # 員数が入っている行がこの割合より少なければ、員数の欄を読み直す


def _qty_rects(info) -> list[tuple]:
    """員数の欄の、行ごとの範囲（ページ座標 (x0, y0, x1, y1)）と文字の高さ。"""
    rects = []
    for t in info.get("qty") or []:
        h = t["h"]
        for y in t["rows"]:
            rects.append(((t["x0"] - h * 0.3, y - h * 0.5, t["x1"] + h * 0.3, y + h * 1.4), h))
    return rects


def _lone_ones(page, rect, h, dpi=300) -> list[dict]:
    """欄の中にぽつんとある「1」を画像から探し、OCR の item の形で返す（Vision は細い「1」を 1 文字だけだと落とす）。"""
    import numpy as np

    W, H = page.rect.width, page.rect.height
    x0, _, x1, _ = rect
    y = rect[1] + h * 0.5  # 行の文字の下端
    clip = pymupdf.Rect(max(0.0, x0), max(0.0, H - (y + h * 1.2)), min(W, x1), min(H, H - (y - h * 0.2)))
    if clip.is_empty:
        return []
    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=pymupdf.csGRAY, alpha=False)
    g = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, 0]
    s = pix.width / clip.width
    out = []
    for cx, top, bot in imagelines.lone_ones(g, h * s):
        gx, gw = clip.x0 + cx / s - 1.0, 2.0
        gy, gh = H - (clip.y0 + bot / s), (bot - top) / s
        tok = {"text": "1", "x": gx, "y": gy, "w": gw, "h": gh}
        out.append({**tok, "conf": 0.5, "tokens": [dict(tok)], "lone_one": True})
    return out


def _qty_reread(page, key, page_index, rects) -> list[dict]:
    """員数の欄を行ごとに読み直す：高い解像度の OCR と、画像から「1」を探す。結果は OCR の保存と同じ場所に保存。"""
    import hashlib as _h

    tag = _h.sha1(json.dumps([[round(v, 1) for v in r] + [round(h, 2)] for r, h in rects]).encode()).hexdigest()[:10]
    f = _cache_file(key, page_index)
    f = f.with_name(f.name.replace(".json.gz", f"-qty2-{tag}.json.gz")) if f else None
    cached = _cache_load(f)
    if cached is not None:
        return cached["items"]
    out = []
    for r, h in rects:
        found = []
        if ocr_mac.available():
            try:
                found = [i for i in ocr_mac.ocr_region(page, r) if (i.get("text") or "").strip()]
            except Exception as e:  # noqa: BLE001
                log.warning("員数の読み直しに失敗: %s", e)
        try:
            ones = _lone_ones(page, r, h)
        except Exception as e:  # noqa: BLE001
            log.warning("員数の「1」の検出に失敗: %s", e)
            ones = []
        # OCR が同じ場所に何か読めていれば、そちらを使う
        ones = [o for o in ones if not any(abs((i["x"] + i["w"] / 2) - o["x"] - 1) < h * 0.6 for i in found)]
        out.extend(found + ones)
    _cache_save(f, out, [])
    return out


def _merge_extra(items, extra):
    """読み直した文字のうち、もとの OCR にない位置のものだけ足す。"""
    add = []
    for e in extra:
        if not (e.get("text") or "").strip():
            continue
        cx, cy = e["x"] + e["w"] / 2, e["y"] + e["h"] / 2
        if any(i["x"] - 1 <= cx <= i["x"] + i.get("w", 0) + 1 and i["y"] - 1 <= cy <= i["y"] + i.get("h", 0) + 1
               for i in items):
            continue
        add.append({**e, "reread": True})
    return items + add


def _bom(items, own_no, force, vlines=None, reread=None):
    """部品表と注記の参照図番。スキャン PDF の部品表は BOM_PDF=1 か force のときだけ。
    reread：員数の欄を読み直す関数（行ごとの範囲のリスト → items）。OCR のページで員数の少ない表に使う。"""
    is_ocr = any(i.get("tokens") is not None for i in items)
    refs = {"note_refs": bom.note_refs([i["text"] for i in items], own_no),
            "assembly_refs": bom.assembly_refs(items, own_no, ocr=is_ocr)}
    refs = {k: v for k, v in refs.items() if v}
    if not (CONFIG.bom_pdf or force):
        return [], refs
    vl = vlines or []
    try:
        info = {}
        rows = bom.extract(bom.cells_from_ocr(items, vl), vl, info)
        if is_ocr and reread and rows and sum(1 for r in rows if r["qty"]) < len(rows) * QTY_FILL_OK:
            rects = _qty_rects(info)
            extra = reread(rects) if rects else []
            if extra:
                again = bom.extract(bom.cells_from_ocr(_merge_extra(items, extra), vl), vl)
                if len(again) >= len(rows) and sum(1 for r in again if r["qty"]) > sum(1 for r in rows if r["qty"]):
                    rows = again
        return rows, refs
    except Exception as e:  # noqa: BLE001
        log.warning("部品表の読み取りに失敗: %s", e)
        return [], refs


def _rereader(doc, key, p):
    i = p["page_no"] - 1
    return lambda rects: _qty_reread(doc[i], key, i, rects)


def parse(data: bytes, path: str, force_bom: bool = False, debug: dict | None = None) -> tuple[dict, dict[int, bytes]]:
    """({"drawings": [...]}, {ページ番号: サムネイルPNG}) を返す。
    debug に dict を渡すと、ページごとの OCR の結果（語ごとの位置）と罫線を入れて返す（調整用）。"""
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = []
    ocr_left = CONFIG.ocr_max_pages
    key = hashlib.sha256(data).hexdigest()
    for i, page in enumerate(doc):
        p = _read_page(page, path, i, ocr_left, want_lines=CONFIG.bom_pdf or force_bom, key=key)
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
            rows, refs = _bom(p["items"], f["drawing_no"], force_bom, p["vlines"],
                              _rereader(doc, key, p) if p["text_source"] == "ocr" else None)
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
                          pages[0]["vlines"] if pages else [],
                          _rereader(doc, key, pages[0]) if pages and pages[0]["text_source"] == "ocr" else None)
        attrs = {**common, "tb_codes": pages[0]["tb_codes"][:5] if pages else [], **refs}
        drawings.append({"page_no": 1, "drawing": _drawing(fields, source, conf, needs_ocr, attrs),
                         "pages": [{"page_no": p["page_no"], "text": p["text"], "text_source": p["text_source"]} for p in pages],
                         "bom": rows})
        if len(doc):
            thumbs[1] = _thumb(doc[0])
    return {"drawings": drawings, "items": pages[0]["items"] if pages else []}, thumbs
