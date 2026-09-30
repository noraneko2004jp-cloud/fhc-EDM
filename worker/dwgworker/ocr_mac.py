"""macOS の Vision フレームワークで OCR する（Mac mini 専用）。

スキャンした図面 PDF（コピー機で取り込んだ文字のない PDF）のページを画像にして、
日本語と英数字を座標付きで読み取る。Linux などでは available() が False になり、何もしない。
"""
from __future__ import annotations

import sys

_ok = None


def available() -> bool:
    global _ok
    if _ok is None:
        if sys.platform != "darwin":
            _ok = False
        else:
            try:
                import Vision  # noqa: F401  pyobjc-framework-Vision
                from Foundation import NSData  # noqa: F401
                _ok = True
            except Exception:
                _ok = False
    return _ok


def ocr_png(png: bytes, languages=("ja-JP", "en-US")) -> list[dict]:
    """PNG を OCR し、[{"text", "conf", "x", "y", "w", "h"}] を返す（0〜1 の比率、原点は左下）。"""
    import Vision
    from Foundation import NSData

    data = NSData.dataWithBytes_length_(png, len(png))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(list(languages))
    req.setUsesLanguageCorrection_(False)  # 図番などの記号列を辞書で直させない
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR に失敗しました: {err}")
    out = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        c = cands[0]
        bb = obs.boundingBox()
        text = str(c.string())
        box = {"x": float(bb.origin.x), "y": float(bb.origin.y), "w": float(bb.size.width), "h": float(bb.size.height)}
        out.append({"text": text, "conf": float(c.confidence()), **box, "tokens": _tokens(c, text, box)})
    return out


def _tokens(cand, text, box) -> list[dict]:
    """空白で区切った語ごとの位置（部品表の欄ごとに分けるため）。
    Vision は隣り合う欄の文字を 1 つのまとまりとして返すことがあるので、語の位置は Vision に語ごとに聞く。
    聞けないときは文字数の割合で分ける。"""
    import re

    out = []
    n = max(len(text), 1)
    for m in re.finditer(r"\S+", text):
        b = None
        try:
            obs, _err = cand.boundingBoxForRange_error_((m.start(), m.end() - m.start()), None)
            if obs is not None:
                r = obs.boundingBox()
                b = {"x": float(r.origin.x), "y": float(r.origin.y), "w": float(r.size.width), "h": float(r.size.height)}
        except Exception:  # noqa: BLE001
            b = None
        if b is None:
            b = {"x": box["x"] + box["w"] * m.start() / n, "y": box["y"], "w": box["w"] * (m.end() - m.start()) / n, "h": box["h"]}
        out.append({"text": m.group(0), **b})
    return out


def render(page, dpi=300):
    """OCR 用のグレー画像（罫線の検出にも同じ画像を使う）。"""
    import pymupdf

    return page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)


def ocr_page(page, dpi=300, pix=None) -> list[dict]:
    """PyMuPDF のページを OCR し、ページ座標（pt、y は上向き）の items を返す。
    各 item の tokens に、空白で区切った語ごとの位置も入れる。"""
    pix = pix or render(page, dpi)
    W, H = page.rect.width, page.rect.height
    items = []
    for r in ocr_png(pix.tobytes("png")):
        toks = [{"text": t["text"], "x": t["x"] * W, "y": t["y"] * H, "w": t["w"] * W, "h": t["h"] * H}
                for t in r.get("tokens") or []]
        items.append({"text": r["text"], "conf": r["conf"], "x": r["x"] * W, "y": r["y"] * H,
                      "w": r["w"] * W, "h": r["h"] * H, "tokens": toks})
    # 上から下、左から右の順に並べる
    items.sort(key=lambda i: (-round(i["y"] + i["h"], -1), i["x"]))
    return items


def lines(items: list[dict]) -> list[str]:
    """同じ高さに並んだ文字のかたまりを左から右へ 1 行にまとめる（表の行を 1 行として読むため）。"""
    rows = []
    for it in sorted(items, key=lambda i: -(i["y"] + i["h"] / 2)):
        cy = it["y"] + it["h"] / 2
        for r in rows:
            if abs(r["cy"] - cy) < max(r["h"], it["h"]) * 0.5:
                r["items"].append(it)
                break
        else:
            rows.append({"cy": cy, "h": it["h"], "items": [it]})
    return [" ".join(i["text"] for i in sorted(r["items"], key=lambda i: i["x"])) for r in rows]
