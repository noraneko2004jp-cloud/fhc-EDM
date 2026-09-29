"""表題欄の読み取り。DXF の属性（ATTRIB）、ファイル名の社内ルール、「図番：値」のようなラベルと値の並びから探す。

社内の表題欄の項目名が増えたら、FIELD_TAGS と FIELD_LABELS に追加する。
2026-09-29 実図面で確認した書き方:
  ・部品図（スキャンPDF）: 表題欄に「図番」「名称」「型式」「年月日」。ファイル名「HB0011XXXX差替3 スペーサー.pdf」
  ・図面一式（スキャンPDF）: ファイル名「20-032 CAK-A 多用途ﾊｳｽ 図面一式.pdf」（案件番号・型式・名称）
  ・ハウス図（Jw_cad 由来の DXF）: 表題欄は英語ラベル TIPE / NAME / TITLE / DATE / DRAWING NO（値はラベルの右下）。
    ファイル名「dxfCAK-40A 田の字36R-221003.dxf」（型式・名称・日付）
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from .config import CONFIG

FIELDS = ("drawing_no", "revision", "title", "model", "material", "scale", "drawn_date", "sheet_title", "sheet_no", "job_no",
          "file_title")

# DXF のブロック属性のタグ名（大文字で比較）
FIELD_TAGS = {
    "drawing_no": {"図番", "DWG_NO", "DWGNO", "DRAWING_NO", "DRAWINGNO", "ZUBAN", "DWG.NO"},
    "revision": {"REV", "REVISION", "改訂", "版", "差替"},
    "title": {"品名", "名称", "図名", "件名", "NAME"},
    "sheet_title": {"TITLE"},
    "model": {"型式", "TYPE", "TIPE", "MODEL"},
    "material": {"材質", "MATERIAL", "MAT"},
    "scale": {"尺度", "SCALE"},
    "drawn_date": {"日付", "作成日", "製図日", "年月日", "DATE"},
}
# 図面上に文字として書かれているラベル
FIELD_LABELS = {
    "drawing_no": {"図番", "図面番号", "DWG NO", "DWG.NO", "DRAWING NO"},
    "revision": {"改訂", "REV", "REV.", "版"},
    "title": {"品名", "名称", "図名", "件名", "NAME"},
    "sheet_title": {"TITLE"},
    "model": {"型式", "TYPE", "TIPE", "MODEL"},
    "material": {"材質", "MATERIAL"},
    "scale": {"尺度", "SCALE"},
    "drawn_date": {"日付", "作成日", "製図日", "DATE"},
}
_LABEL_TO_FIELD = {unicodedata.normalize("NFKC", lab).upper(): f for f, labs in FIELD_LABELS.items() for lab in labs}
_INLINE = re.compile(r"^\s*(" + "|".join(sorted(map(re.escape, _LABEL_TO_FIELD), key=len, reverse=True)) + r")\s*[:：]\s*(.+)$", re.I)
# 部品表の見出し行に並ぶ語。これが同じ高さに 3 つ以上あれば表の見出しとみなし、表題欄のラベルとして扱わない
_BOM_ONLY_WORDS = {"NO", "NO.", "品番", "部品番号", "数量", "個数", "員数", "QTY", "PART NO", "備考"}
_TABLE_HEADER_WORDS = set(_LABEL_TO_FIELD) | _BOM_ONLY_WORDS
_REV_IN_NAME = re.compile(r"(?:[_\- ]REV\.?[_\- ]?|_)([A-Z]|\d{1,2})$", re.I)

# ファイル名の社内ルール（NFKC 済みの拡張子なしの名前に当てる）
_FN_PART = re.compile(r"^(?P<no>[A-Z]{2,4}\d{4,8}X{0,6})\s*(?:差替\s*(?P<rev>\d+))?\s*(?P<name>.*)$")
_FN_JOB = re.compile(r"^(?P<job>\d{2}-\d{3})\s+(?:(?P<model>[A-Z]{2,5}(?:-[0-9A-Z]{1,5})+)\s+)?(?P<name>.+)$")
_FN_DATED = re.compile(r"^(?:DXF|DWG|JW)?\s*(?P<model>[A-Z]{2,5}(?:-[0-9A-Z]{1,5})+)?\s*(?P<name>.*?)[-_ ](?P<date>\d{6})$", re.I)


def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def compact_codes(text: str) -> str:
    """OCR で「H B 0 0 1 1 X X X X」のように 1 文字ずつ離れて読まれた英数字の並びを詰める。"""
    return re.sub(r"(?<![A-Z0-9])(?:[A-Z0-9][ \t]+){5,}[A-Z0-9](?![A-Z0-9])", lambda m: re.sub(r"[ \t]+", "", m.group(0)), text)


def find_codes(text: str) -> list[str]:
    """文章中の図番・品番らしい文字列を重複なしで返す。"""
    seen = []
    for m in re.finditer(CONFIG.drawing_no_regex, compact_codes(norm(text).upper())):
        c = m.group(0)
        if c not in seen:
            seen.append(c)
    return seen


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
    """items: [{"text", "x", "y", "h"}]（y は上向き正）。ラベルの右隣、または右下・真下の文字を値とみなす。"""
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
        row = [norm(o["text"]).upper() for o in items if abs(o["y"] - it["y"]) < 0.6 * h]
        if sum(w in _TABLE_HEADER_WORDS for w in row) >= 3 and any(w in _BOM_ONLY_WORDS for w in row):
            continue  # 部品表の見出し行
        best, best_d = None, None
        for o in items:
            if o is it or not norm(o["text"]) or norm(o["text"]).upper().rstrip(":：") in _LABEL_TO_FIELD:
                continue
            dx, dy = o["x"] - it["x"], o["y"] - it["y"]
            right = 0 < dx < 25 * h and abs(dy) < 0.8 * h
            below = -4 * h < dy < -0.3 * h and -2 * h < dx < 12 * h  # 表題欄の枠の左上にラベル、右下に値
            if right or below:
                d = abs(dx) + 3 * abs(dy)
                if best_d is None or d < best_d:
                    best, best_d = o, d
        if best:
            out[f] = norm(best["text"])
    # 「DRAWING NO」が 1〜3 桁の数字だけなら図番ではなく枚番号
    dn = out.get("drawing_no", "")
    if dn.isdigit() and len(dn) <= 3:
        out["sheet_no"] = out.pop("drawing_no")
    return out


def from_filename(path: str) -> tuple[dict, bool]:
    """(値の辞書, 社内ルールに一致したか) を返す。"""
    s = norm(PurePosixPath(path).stem).upper()
    name_orig = norm(PurePosixPath(path).stem)
    m = _FN_PART.match(s)
    if m:
        name = name_orig[len(name_orig) - len(m.group("name")):].strip() if m.group("name") else ""
        return {"drawing_no": m.group("no"), "revision": m.group("rev") or "", "title": name}, True
    m = _FN_JOB.match(s)
    if m:
        name = name_orig[len(name_orig) - len(m.group("name")):].strip()
        return {"job_no": m.group("job"), "model": m.group("model") or "", "title": name}, True
    m = _FN_DATED.match(s)
    if m and (m.group("model") or m.group("name")):
        d = m.group("date")
        name = name_orig[len(name_orig) - len(s) + m.start("name"):len(name_orig) - len(s) + m.end("name")].strip()
        return {"model": m.group("model") or "", "title": name, "revision": f"20{d[:2]}-{d[2:4]}-{d[4:]}",
                "drawn_date": f"20{d[:2]}-{d[2:4]}-{d[4:]}"}, True
    out = {}
    m = re.search(CONFIG.drawing_no_regex, s)
    if m:
        out["drawing_no"] = m.group(0)
        rest = s[m.end():]
        r = _REV_IN_NAME.search(rest) if rest else None
        if r:
            out["revision"] = r.group(1)
    return out, False


def resolve(path: str, attribs=None, positioned=None, base_source="text") -> tuple[dict, str, float]:
    """(値の辞書, 読み取り元, 信頼度) を返す。図番の出どころで信頼度を決める。"""
    a = from_attribs(attribs or [])
    p = from_positioned(positioned or [])
    fn, fn_rule = from_filename(path)
    order = (a, fn, p) if fn_rule else (a, p, fn)
    merged = {f: next((src[f] for src in order if src.get(f)), "") for f in FIELDS}
    # 名称は図面に書かれたものを優先し、ファイル名の名称は別に残す（両方で検索できるように）
    if (p.get("title") or a.get("title")) and not (fn_rule and fn.get("drawing_no")):
        merged["title"] = a.get("title") or p.get("title")
    merged["file_title"] = fn.get("title", "") if fn.get("title") != merged["title"] else ""
    if not merged["title"] and merged["sheet_title"]:
        merged["title"] = merged["sheet_title"]
    if a.get("drawing_no"):
        return merged, "attrib", 0.97
    if fn_rule and fn.get("drawing_no"):
        return merged, "filename", 0.9
    if p.get("drawing_no"):
        return merged, base_source, 0.85
    if fn_rule:  # 図番はないが型式・名称がファイル名から取れた（ハウス図・図面一式）
        return merged, base_source if p else "filename", 0.8
    if fn.get("drawing_no"):
        return merged, "filename", 0.6
    return merged, base_source, 0.3
