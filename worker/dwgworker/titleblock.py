"""表題欄の読み取り。DXF の属性（ATTRIB）、「図番：値」のようなラベルと値の並び、ファイル名の順に探す。

社内の表題欄の項目名が決まったら、FIELD_TAGS と FIELD_LABELS に追加する。
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from .config import CONFIG

FIELDS = ("drawing_no", "revision", "title", "material", "scale", "drawn_date")

# DXF のブロック属性のタグ名（大文字で比較）
FIELD_TAGS = {
    "drawing_no": {"図番", "DWG_NO", "DWGNO", "DRAWING_NO", "DRAWINGNO", "ZUBAN", "DWG.NO", "NO"},
    "revision": {"REV", "REVISION", "改訂", "版"},
    "title": {"品名", "名称", "図名", "件名", "TITLE", "NAME"},
    "material": {"材質", "MATERIAL", "MAT"},
    "scale": {"尺度", "SCALE"},
    "drawn_date": {"日付", "作成日", "製図日", "DATE"},
}
# 図面上に文字として書かれているラベル
FIELD_LABELS = {
    "drawing_no": {"図番", "図面番号", "DWG NO", "DWG.NO", "DRAWING NO"},
    "revision": {"改訂", "REV", "REV.", "版"},
    "title": {"品名", "名称", "図名", "件名", "TITLE"},
    "material": {"材質", "MATERIAL"},
    "scale": {"尺度", "SCALE"},
    "drawn_date": {"日付", "作成日", "製図日", "DATE"},
}
_LABEL_TO_FIELD = {unicodedata.normalize("NFKC", lab).upper(): f for f, labs in FIELD_LABELS.items() for lab in labs}
_INLINE = re.compile(r"^\s*(" + "|".join(sorted(map(re.escape, _LABEL_TO_FIELD), key=len, reverse=True)) + r")\s*[:：]\s*(.+)$", re.I)
# 部品表の見出し行に並ぶ語。これが同じ高さに 3 つ以上あれば表の見出しとみなし、表題欄のラベルとして扱わない
_TABLE_HEADER_WORDS = set(_LABEL_TO_FIELD) | {"NO", "NO.", "品番", "部品番号", "数量", "個数", "員数", "QTY", "PART NO", "備考"}
_REV_IN_NAME = re.compile(r"(?:[_\- ]REV\.?[_\- ]?|_)([A-Z]|\d{1,2})$", re.I)


def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def from_attribs(attribs: list[dict]) -> dict:
    """attribs: [{"tag":..., "text":...}]"""
    out = {}
    for a in attribs:
        tag = norm(a.get("tag")).upper()
        val = norm(a.get("text"))
        if not val:
            continue
        for f, tags in FIELD_TAGS.items():
            if tag in tags and f not in out:
                out[f] = val
    return out


def from_positioned(items: list[dict]) -> dict:
    """items: [{"text", "x", "y", "h"}]（y は上向き正）。ラベルの右隣、なければ真下の文字を値とみなす。"""
    out = {}
    for it in items:
        m = _INLINE.match(norm(it["text"]))
        if m:
            f = _LABEL_TO_FIELD[norm(m.group(1)).upper()]
            out.setdefault(f, m.group(2).strip())
    for it in items:
        f = _LABEL_TO_FIELD.get(norm(it["text"]).rstrip(":：").upper())
        if not f or f in out:
            continue
        h = max(it.get("h") or 1.0, 1e-6)
        same_row = sum(1 for o in items if abs(o["y"] - it["y"]) < 0.6 * h and norm(o["text"]).upper() in _TABLE_HEADER_WORDS)
        if same_row >= 3:
            continue
        best, best_d = None, None
        for o in items:
            if o is it or not norm(o["text"]) or norm(o["text"]).upper() in _LABEL_TO_FIELD:
                continue
            dx, dy = o["x"] - it["x"], o["y"] - it["y"]
            right = 0 < dx < 25 * h and abs(dy) < 0.8 * h
            below = -4 * h < dy < 0 and abs(dx) < 6 * h
            if right or below:
                d = abs(dx) + 3 * abs(dy)
                if best_d is None or d < best_d:
                    best, best_d = o, d
        if best:
            out[f] = norm(best["text"])
    return out


def from_filename(path: str) -> dict:
    stem = PurePosixPath(path).stem
    s = norm(stem).upper()
    out = {}
    m = re.search(CONFIG.drawing_no_regex, s)
    if m:
        out["drawing_no"] = m.group(0)
        rest = s[m.end():]
        r = _REV_IN_NAME.search(rest) if rest else None
        if r:
            out["revision"] = r.group(1)
    return out


def resolve(path: str, attribs=None, positioned=None, base_source="text") -> tuple[dict, str, float]:
    """(値の辞書, 読み取り元, 信頼度) を返す。図番の出どころで信頼度を決める。"""
    a = from_attribs(attribs or [])
    p = from_positioned(positioned or [])
    fn = from_filename(path)
    merged = {}
    for f in FIELDS:
        merged[f] = a.get(f) or p.get(f) or fn.get(f) or ""
    if a.get("drawing_no"):
        return merged, "attrib", 0.97
    if p.get("drawing_no"):
        return merged, base_source, 0.85
    if fn.get("drawing_no"):
        return merged, "filename", 0.6
    return merged, base_source, 0.3
