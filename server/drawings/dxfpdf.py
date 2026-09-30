"""DXF から印刷用の PDF を作る（図面の尺度どおり＝元の用紙サイズで原寸）。

社内の DXF は紙の上の大きさ（mm）で描かれている（A2 の図枠なら 594×420）。図枠の大きさから用紙（A4〜A0）を決め、
その用紙の PDF に 1:1 で描く。A2 の図面は A2 の PDF になる（A3 の紙に刷るときはプリンター側で縮小）。
実寸（モデル空間に 1:1）で描かれた図面は、表題欄の尺度（1/30 など）で割って用紙を探す。
用紙が決まらない図面は、全体が入る A3 に縮小して「縮尺なし」と書き添える。

文字コードの直し方・日本語フォントの選び方は worker/dwgworker/parse_dxf.py と同じ（サーバーは worker を読み込めないため写し）。
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# 用紙（横長の向き、mm）。小さい順
PAPERS = [("A4", 297.0, 210.0), ("A3", 420.0, 297.0), ("A2", 594.0, 420.0), ("A1", 841.0, 594.0), ("A0", 1189.0, 841.0)]
# 図枠と用紙の差として認める割合（内枠は用紙より 10〜35mm ほど小さい）
FRAME_SLACK = 0.13
JP_FALLBACK_FONTS = [
    "NotoSansCJK-Regular.ttc", "NotoSansCJKjp-Regular.otf", "NotoSansJP-Regular.ttf", "ipaexg.ttf", "ipag.ttf",
    "ヒラギノ角ゴシック W3.ttc", "msgothic.ttc", "YuGothR.ttc",
]
_font_ready = False


@dataclass
class Sheet:
    paper: str          # "A2" など。決まらないときは ""
    width: float        # 用紙の大きさ（mm）
    height: float
    box: tuple          # 描く範囲（DXF 座標）(x0, y0, x1, y1)
    factor: float       # DXF 座標 ÷ factor ＝ 紙の上の mm
    exact: bool         # 尺度どおりか（False は A3 に縮小）


# ---- 文字コード（parse_dxf.fix_japanese_codepage と同じ）----
_CP_RE = re.compile(rb"(\s*9\r?\n\$DWGCODEPAGE\r?\n\s*3\r?\n)([^\r\n]*)")
_VER_RE = re.compile(rb"(\s*9\r?\n\$ACADVER\r?\n\s*1\r?\n[^\r\n]*\r?\n)")


def fix_japanese_codepage(data: bytes) -> bytes:
    m = _CP_RE.search(data[:20000])
    current = m.group(2).strip().upper() if m else b""
    if current and current not in (b"ANSI_1252", b"DWGCODEPAGE"):
        return data
    high = sum(1 for b in data if b >= 0x80)
    if high < 20:
        return data
    text = data.decode("cp932", errors="replace")
    if text.count("�") > high * 0.01:
        return data
    if m:
        return data[:m.start(2)] + b"ANSI_932" + data[m.end(2):]
    v = _VER_RE.search(data[:20000])
    if not v:
        return data
    nl = b"\r\n" if b"\r\n" in v.group(1) else b"\n"
    ins = b"  9" + nl + b"$DWGCODEPAGE" + nl + b"  3" + nl + b"ANSI_932" + nl
    return data[:v.end()] + ins + data[v.end():]


def _setup_fonts():
    global _font_ready
    if _font_ready:
        return
    from ezdxf.fonts import fonts

    fm = fonts.font_manager
    for name in JP_FALLBACK_FONTS:
        if fm.has_font(name):
            fm._fallback_font_name = name  # ezdxf に公開APIがないため内部値を設定
            break
    _font_ready = True


def scale_denominator(scale: str) -> float | None:
    """表題欄の尺度「1/30」「1:30」「S=1/30」「A2 1/30」→ 30。読めなければ None。"""
    import unicodedata

    s = unicodedata.normalize("NFKC", scale or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[/:]\s*(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return b / a if a > 0 and b > 0 else None


# ---- 図枠と用紙 ----
def _paper_for(w: float, h: float):
    """図枠の大きさ (w, h)（紙の上の mm）に合う用紙。(名前, 用紙幅, 用紙高さ) か None。"""
    for name, pw, ph in PAPERS:
        if w < h:
            pw, ph = ph, pw
        if pw * (1 - FRAME_SLACK) <= w <= pw * 1.01 and ph * (1 - FRAME_SLACK) <= h <= ph * 1.01:
            return name, pw, ph
    return None


def _candidates(layout_obj):
    """図枠らしい長方形（DXF 座標）。ブロック（図枠が部品として入っていることが多い）、閉じた長方形の線、
    いちばん長い横線 2 本と縦線 2 本で囲まれた範囲。"""
    from ezdxf import bbox

    out = []
    for e in layout_obj.query("INSERT"):
        try:
            b = bbox.extents([e], fast=True)
        except Exception:  # noqa: BLE001
            continue
        if b.has_data:
            out.append((b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y))
    hs, vs = [], []
    for e in layout_obj.query("LINE LWPOLYLINE POLYLINE"):
        try:
            if e.dxftype() == "LINE":
                pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
            elif e.dxftype() == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                if e.closed and pts:
                    pts.append(pts[0])
            else:
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and pts:
                    pts.append(pts[0])
        except Exception:  # noqa: BLE001
            continue
        if e.dxftype() != "LINE" and len(pts) in (4, 5):
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.append((min(xs), min(ys), max(xs), max(ys)))
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if abs(y0 - y1) < 0.01 and abs(x0 - x1) > 0:
                hs.append((abs(x1 - x0), min(x0, x1), max(x0, x1), y0))
            elif abs(x0 - x1) < 0.01 and abs(y0 - y1) > 0:
                vs.append((abs(y1 - y0), min(y0, y1), max(y0, y1), x0))
    hs.sort(reverse=True)
    vs.sort(reverse=True)
    if len(hs) >= 2 and len(vs) >= 2:
        h2, v2 = hs[:2], vs[:2]
        out.append((min(h[1] for h in h2), min(h[3] for h in h2), max(h[2] for h in h2), max(h[3] for h in h2)))
        if abs(v2[0][3] - v2[1][3]) > 1e-6:
            out.append((min(v[3] for v in v2), min(v[1] for v in v2), max(v[3] for v in v2), max(v[2] for v in v2)))
    return out


def find_sheet(layout_obj, scale: str = "", paper_space: bool = False) -> Sheet:
    """描く範囲と用紙を決める。"""
    from ezdxf import bbox

    ext = bbox.extents(layout_obj, fast=True)
    if not ext.has_data:
        raise ValueError("図面に描かれたものがありません")
    whole = (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
    den = None if paper_space else scale_denominator(scale)
    factors = [1.0] + ([den] if den and abs(den - 1) > 1e-6 else [])
    best = None
    for f in factors:
        for r in _candidates(layout_obj) + [whole]:
            w, h = (r[2] - r[0]) / f, (r[3] - r[1]) / f
            p = _paper_for(w, h)
            if p and (best is None or w * h > best[0]):
                best = (w * h, p, r, f)
        if best:
            break
    if best:
        _, (name, pw, ph), r, f = best
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        box = (cx - pw * f / 2, cy - ph * f / 2, cx + pw * f / 2, cy + ph * f / 2)
        return Sheet(name, pw, ph, box, f, True)
    # 図枠が見つからない：全体が入るいちばん小さい用紙に原寸で。A0 にも入らなければ A3 に縮小
    for f in factors:
        w, h = (whole[2] - whole[0]) / f, (whole[3] - whole[1]) / f
        for name, pw, ph in PAPERS:
            if w < h:
                pw, ph = ph, pw
            if w <= pw and h <= ph:
                cx, cy = (whole[0] + whole[2]) / 2, (whole[1] + whole[3]) / 2
                return Sheet(name, pw, ph, (cx - pw * f / 2, cy - ph * f / 2, cx + pw * f / 2, cy + ph * f / 2), f, True)
    w, h = whole[2] - whole[0], whole[3] - whole[1]
    pw, ph = (420.0, 297.0) if w >= h else (297.0, 420.0)
    k = max(w / pw, h / ph)
    cx, cy = (whole[0] + whole[2]) / 2, (whole[1] + whole[3]) / 2
    return Sheet("", pw, ph, (cx - pw * k / 2, cy - ph * k / 2, cx + pw * k / 2, cy + ph * k / 2), k, False)


def render(data: bytes, scale: str = "", title: str = "") -> tuple[bytes, Sheet]:
    """DXF の中身 → (PDF, 用紙)。"""
    import ezdxf  # noqa: F401
    import pymupdf
    from ezdxf import recover
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout
    from ezdxf.addons.drawing import pymupdf as pmb
    from ezdxf.math import BoundingBox2d

    doc, _auditor = recover.read(io.BytesIO(fix_japanese_codepage(data)))
    msp = doc.modelspace()
    target, paper_space = msp, False
    if len(msp) == 0:
        paper = [lay for lay in doc.layouts if lay.name != "Model" and len(lay)]
        if paper:
            target, paper_space = paper[0], True
    sheet = find_sheet(target, scale, paper_space)
    _setup_fonts()
    backend = pmb.PyMuPdfBackend()
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                               color_policy=config.ColorPolicy.BLACK)
    Frontend(RenderContext(doc), backend, config=cfg).draw_layout(target, finalize=True)
    page = layout.Page(sheet.width, sheet.height, layout.Units.mm, margins=layout.Margins.all(0))
    x0, y0, x1, y1 = sheet.box
    pdf = backend.get_pdf_bytes(page, settings=layout.Settings(fit_page=True, crop_at_margins=True),
                                render_box=BoundingBox2d([(x0, y0), (x1, y1)]))
    out = pymupdf.open(stream=pdf, filetype="pdf")
    meta = {"title": title or "", "creator": "図面検索", "producer": "ezdxf / PyMuPDF"}
    try:
        out.set_metadata(meta)
    except Exception:  # noqa: BLE001
        pass
    if not sheet.exact:
        pg = out[0]
        pg.insert_text((12, pg.rect.height - 8), "縮尺なし（用紙を判定できないため A3 に縮小）", fontsize=7,
                       fontname="japan", color=(0.4, 0.4, 0.4))
    return out.tobytes(garbage=3, deflate=True), sheet
