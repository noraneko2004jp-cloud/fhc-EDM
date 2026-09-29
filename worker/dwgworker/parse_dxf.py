"""DXF の解析：文字（座標付き）とブロック属性を取り出し、表題欄を読み、PNG サムネイルを作る。"""
from __future__ import annotations

import io
import re
import logging

import ezdxf
from ezdxf import recover

from . import titleblock
from .config import CONFIG

log = logging.getLogger("dwgworker")
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


_CP_RE = re.compile(rb"(\s*9\r?\n\$DWGCODEPAGE\r?\n\s*3\r?\n)([^\r\n]*)")
_VER_RE = re.compile(rb"(\s*9\r?\n\$ACADVER\r?\n\s*1\r?\n[^\r\n]*\r?\n)")


def fix_japanese_codepage(data: bytes) -> tuple[bytes, bool]:
    """文字コードの指定がない（または ANSI_1252 の）古い DXF に Shift_JIS の日本語が入っていれば ANSI_932 として読ませる。

    Jw_cad などが出力した R12 形式の DXF でよく起きる。指定どおり読むと日本語が文字化けし、
    画層名の不正で読み込み自体が失敗することもある。
    """
    m = _CP_RE.search(data[:20000])
    current = m.group(2).strip().upper() if m else b""
    if current and current not in (b"ANSI_1252", b"DWGCODEPAGE"):
        return data, False
    high = sum(1 for b in data if b >= 0x80)
    if high < 20:
        return data, False
    text = data.decode("cp932", errors="replace")
    if text.count("\ufffd") > high * 0.01:  # Shift_JIS として読めないなら触らない
        return data, False
    if m:
        return data[:m.start(2)] + b"ANSI_932" + data[m.end(2):], True
    v = _VER_RE.search(data[:20000])
    if not v:
        return data, False
    nl = b"\r\n" if b"\r\n" in v.group(1) else b"\n"
    ins = b"  9" + nl + b"$DWGCODEPAGE" + nl + b"  3" + nl + b"ANSI_932" + nl
    return data[:v.end()] + ins + data[v.end():], True


def parse(data: bytes, path: str) -> tuple[dict, bytes | None]:
    data, cp_fixed = fix_japanese_codepage(data)
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
    thumb, thumb_error = None, ""
    try:
        target = paper[0] if paper and len(msp) == 0 else msp
        thumb = _render_png(doc, target)
    except Exception as e:  # サムネイルが作れなくても文字の読み取り結果は登録する
        thumb_error = f"{type(e).__name__}: {e}"[:300]
        log.warning("サムネイル作成に失敗 %s: %s", path, thumb_error)
    result = {
        "drawing": {**{k: fields[k] for k in ("drawing_no", "revision", "title", "material", "scale", "drawn_date")},
                    "source": source, "confidence": conf, "needs_ocr": False,
                    "attributes": {"dxf_version": doc.dxfversion, "codepage": doc.header.get("$DWGCODEPAGE", ""),
                                   "audit_errors": len(auditor.errors), "codepage_fixed": cp_fixed, "attribs": attribs[:200],
                                   **{k: fields[k] for k in ("model", "sheet_title", "sheet_no", "job_no", "file_title") if fields[k]},
                                   **({"thumb_error": thumb_error} if thumb_error else {})}},
        "pages": [{"page_no": 1, "text": text, "text_source": "attrib" if attribs else "text"}],
        "bom": [],  # 部品表の抽出は Phase 2（サンプル図面の形式を見て実装）
        "items": items,
    }
    return result, thumb
