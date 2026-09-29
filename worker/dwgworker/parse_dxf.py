"""DXF の解析：文字（座標付き）とブロック属性を取り出し、表題欄を読み、PNG サムネイルを作る。"""
from __future__ import annotations

import io

import ezdxf
from ezdxf import recover

from . import titleblock
from .config import CONFIG

TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB"}

# 図面指定のフォント（MSゴシック、SHX など）が無いときの代わりの日本語フォント。先に見つかったものを使う
JP_FALLBACK_FONTS = [
    "ヒラギノ角ゴシック W3.ttc", "Hiragino Sans GB.ttc", "NotoSansCJK-Regular.ttc", "NotoSansJP-Regular.ttf",
    "NotoSansCJKjp-Regular.otf", "msgothic.ttc", "YuGothR.ttc",
]
_font_ready = False


def _setup_fonts():
    global _font_ready
    if _font_ready:
        return
    from ezdxf.fonts import fonts

    fm = fonts.font_manager
    for name in ([CONFIG.dxf_fallback_font] if CONFIG.dxf_fallback_font else []) + JP_FALLBACK_FONTS:
        if fm.has_font(name):
            fm._fallback_font_name = name  # ezdxf に公開APIがないため内部値を設定
            break
    _font_ready = True


def _text_of(e):
    t = e.dxftype()
    if t == "MTEXT":
        return e.plain_text()
    return e.dxf.get("text", "")


def _collect(entities, out_items, out_attribs, depth=0):
    for e in entities:
        t = e.dxftype()
        if t in TEXT_TYPES:
            txt = (_text_of(e) or "").strip()
            if txt:
                ins = e.dxf.get("insert", (0, 0, 0))
                h = e.dxf.get("char_height" if t == "MTEXT" else "height", 1.0) or 1.0
                out_items.append({"text": txt, "x": float(ins[0]), "y": float(ins[1]), "h": float(h), "layer": e.dxf.get("layer", "")})
        elif t == "INSERT" and depth < 4:
            for a in e.attribs:
                txt = (a.dxf.get("text", "") or "").strip()
                out_attribs.append({"tag": a.dxf.get("tag", ""), "text": txt, "block": e.dxf.name})
                if txt:
                    ins = a.dxf.get("insert", (0, 0, 0))
                    out_items.append({"text": txt, "x": float(ins[0]), "y": float(ins[1]), "h": float(a.dxf.get("height", 1.0) or 1.0), "layer": a.dxf.get("layer", "")})
            try:
                _collect([v for v in e.virtual_entities() if v.dxftype() != "ATTDEF"], out_items, out_attribs, depth + 1)
            except Exception:  # 壊れたブロックは飛ばす
                pass


def _render_png(doc, layout_obj) -> bytes:
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout
    from ezdxf.addons.drawing import pymupdf as pmb

    _setup_fonts()
    backend = pmb.PyMuPdfBackend()
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                               color_policy=config.ColorPolicy.BLACK)
    Frontend(RenderContext(doc), backend, config=cfg).draw_layout(layout_obj, finalize=True)
    page = layout.Page(420, 297, layout.Units.mm, margins=layout.Margins.all(5))
    dpi = max(36, int(CONFIG.thumb_width / (420 / 25.4)))
    return backend.get_pixmap_bytes(page, fmt="png", settings=layout.Settings(fit_page=True), dpi=dpi)


def parse(data: bytes, path: str) -> tuple[dict, bytes | None]:
    doc, auditor = recover.read(io.BytesIO(data))
    msp = doc.modelspace()
    items, attribs = [], []
    _collect(msp, items, attribs)
    # 表題欄がペーパー空間にある図面も多い
    paper = [lay for lay in doc.layouts if lay.name != "Model" and len(lay) > 0]
    for lay in paper:
        _collect(lay, items, attribs)
    fields, source, conf = titleblock.resolve(path, attribs, items, base_source="attrib")
    if source == "attrib" and conf < 0.9:
        source = "text"
    text = "\n".join(i["text"] for i in items)
    thumb = None
    try:
        target = paper[0] if paper and len(msp) == 0 else msp
        thumb = _render_png(doc, target)
    except Exception:
        thumb = None
    result = {
        "drawing": {**fields, "source": source, "confidence": conf, "needs_ocr": False,
                    "attributes": {"dxf_version": doc.dxfversion, "codepage": doc.header.get("$DWGCODEPAGE", ""),
                                   "audit_errors": len(auditor.errors), "attribs": attribs[:200]}},
        "pages": [{"page_no": 1, "text": text, "text_source": "attrib" if attribs else "text"}],
        "bom": [],  # 部品表の抽出は Phase 2（サンプル図面の形式を見て実装）
        "items": items,
    }
    return result, thumb
