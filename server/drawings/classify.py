"""グループ化のための分類：型式（型式系統）と図番の系列。

保存時（api._save_drawing）と、ルールを変えたあとの一括やり直し（manage.py reclassify）で使う。
ルールは実データを見ながら育てる前提。変えたら `python manage.py reclassify` で全件に当て直す。
"""
import re
from pathlib import PurePosixPath

from .text import norm

# 型式：「CAK-A」「CAK-40A」「JH-20」のように 英字2〜5 + ハイフン + 英数字。
# ハイフンの後ろが 3 桁以上の数字だけ（UH-3600 など）は図番なので型式にしない。
_MODEL = re.compile(r"(?<![A-Z0-9])([A-Z]{2,5})-([0-9A-Z]{1,5})(?![0-9A-Z])")
# フォルダ名が「CAG」「JH 多用途」のように英字だけの語で始まるものは型式系統とみなす
_FOLDER_FAMILY = re.compile(r"^([A-Z]{2,5})(?:[\s_]|$)")
NOT_MODEL = {"PDF", "DXF", "DWG", "JW", "JWW", "JWC", "RICOH", "SCAN", "FAX", "OLD", "NEW", "TEMP", "TMP", "DATA"}
NONE = "_"  # 型式・系列が分からないもの（画面では「（型式なし）」など）


def _model_in(text):
    for m in _MODEL.finditer(norm(text)):
        head, tail = m.groups()
        if head in NOT_MODEL or (tail.isdigit() and len(tail) >= 3):
            continue
        return f"{head}-{tail}"
    return ""


def model_of(path: str, attrs: dict | None = None) -> tuple[str, str]:
    """(型式系統, 型式) を返す。例 ("CAK", "CAK-40A")。分からなければ ("_", "_")。
    探す順：表題欄・ファイル名の読み取り結果（attributes.model）→ ファイル名 → 深いフォルダから順にフォルダ名。"""
    attrs = attrs or {}
    p = PurePosixPath(path or "")
    raw = attrs.get("model")
    raw = norm(raw) if isinstance(raw, str) else ""
    code = (_model_in(raw) or (raw.split() or [""])[0]) if raw else ""
    if not code:
        stem = re.sub(r"^(DXF|DWG|JW)", "", norm(p.stem))  # 「dxfCAK-40A …」
        code = _model_in(stem)
    if not code:
        for part in reversed(p.parts[:-1]):
            code = _model_in(part)
            if code:
                break
            m = _FOLDER_FAMILY.match(norm(part))
            if m and m.group(1) not in NOT_MODEL:
                code = m.group(1)
                break
    if not code:
        return NONE, NONE
    code = code[:64]
    family = re.match(r"[A-Z]+", code)
    return (family.group(0) if family else code)[:16], code


_NO = re.compile(r"^([A-Z]{1,4})(-?)(\d+)(X*)(-\d{1,3})?")


def series_of(drawing_no: str) -> tuple[str, str]:
    """(頭の英字, 系列) を返す。同じ部品の差替・改訂・枝番が同じ系列に入るようにする。
      HB0011XXXX → ("HB", "HB0011")     … X の前まで
      HDBY003920 → ("HDBY", "HDBY0039") … 数字 6 桁以上は頭の 4 桁
      UH-3600-01 → ("UH", "UH-3600")    … 枝番を外す
      1234567890 → ("数字", "123456")
    """
    no = norm(drawing_no)
    if not no:
        return NONE, NONE
    m = _NO.match(no)
    if m:
        letters, hy, digits, xs, _branch = m.groups()
        if xs or len(digits) <= 4:
            core = digits
        elif len(digits) == 5:
            core = digits[:-1]
        else:
            core = digits[:4]
        return letters, f"{letters}{hy}{core}"[:64]
    if re.fullmatch(r"\d{10}", no):
        return "数字", no[:6]
    return "他", no[:64]


def apply(drawing) -> None:
    """Drawing に型式・系列を入れる（保存はしない）。分類で思わぬ値があっても保存や起動を止めない。"""
    attrs = drawing.attributes if isinstance(drawing.attributes, dict) else {}
    try:
        drawing.model_family, drawing.model_code = model_of(drawing.file.path, attrs)
    except Exception:  # noqa: BLE001
        drawing.model_family, drawing.model_code = NONE, NONE
    try:
        drawing.series_prefix, drawing.series = series_of(drawing.drawing_no)
    except Exception:  # noqa: BLE001
        drawing.series_prefix, drawing.series = NONE, NONE
