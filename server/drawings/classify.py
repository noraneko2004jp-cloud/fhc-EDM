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


# ---- 文書種別 ----
# ゲスト（Guest グループ）は DRAWING だけ見られる。判定に迷うものは OTHER にして、ゲストには見せない（安全側）。
DRAWING, SITE, CONTRACT, APPLICATION, GENERAL, OTHER = "drawing", "site", "contract", "application", "general", "other"
DOC_TYPES = [(DRAWING, "製品図面"), (SITE, "敷地・土地図"), (CONTRACT, "契約書・見積"), (APPLICATION, "申請書類"),
             (GENERAL, "一般書類"), (OTHER, "その他")]
GUEST_TYPES = {DRAWING}
# 言葉は NFKC＋大文字で比べる。先に書いた種別ほど優先（契約書に土地の話が出てくることが多いため）
TYPE_WORDS = {
    CONTRACT: ["契約書", "契約", "見積書", "見積", "請求書", "注文書", "発注書", "注文請書", "納品書", "領収書", "約款", "覚書",
               "契約金額", "請負", "収入印紙", "御中", "発注伝票", "発注", "受注", "注文"],
    APPLICATION: ["確認申請", "建築確認", "申請書", "申請図書", "届出", "許可申請", "確認済証", "検査済証", "設置届", "申請"],
    SITE: ["公図", "地番", "地積", "測量", "登記", "敷地", "土地", "案内図", "現況図", "求積", "境界", "住宅地図", "付近見取図"],
    # 2026-09-30 実データ確認：工程表・稟議書（OCR で「凛議書」）・理由書・部品寸法表など
    GENERAL: ["工程表", "稟議書", "稟議", "凛議", "理由書", "議事録", "報告書", "打合せ", "打合わせ", "依頼書", "送付状", "連絡書",
              "寸法表", "一覧表", "説明書", "取扱説明", "通知書", "回覧"],
}
# 書類が多いフォルダ。ここでは図番らしい文字があるだけでは製品図面にしない（表題欄の語もそろったときだけ）
DOCUMENT_FOLDERS = ["物件対応ファイル他"]
# スキャナーが付けた日時だけのファイル名（例 20241127081623360.pdf）。名前から手がかりが取れない
_SCANNER_NAME = re.compile(r"^\d{12,}$")
# ファイル名にあれば製品図面とみなす語（フォルダ名は「図面 DXF…」のように広すぎるので見ない）
DRAWING_NAME_WORDS = ["図面一式", "組立図", "部品図", "製作図", "詳細図", "姿図", "展開図", "平面図", "立面図", "断面図", "構造図"]
# 表題欄にある語（本文にこれが 2 つ以上あれば図面らしい）
TITLEBLOCK_WORDS = ["図番", "尺度", "SCALE", "DRAWING NO", "DWG NO", "三角法", "材質", "製図", "検図", "承認"]


def _found(text, words):
    """本文にある語。「確認申請」と「申請」のように、長い語の一部として出ただけの語は数えない。"""
    hit = [w for w in words if w in text]
    return [w for w in hit if not any(w != o and w in o for o in hit)]


def _hits(text, words):
    return len(_found(text, words))


def explain(path, drawing_no="", source="", confidence=0.0, kind="", text="", attrs=None) -> tuple[str, str]:
    """(文書種別, 判定理由) を返す。順番：
    1. ファイル名の言葉（契約・申請・敷地・一般書類）→ 2. フォルダ名の言葉
    3. ファイル名の図面らしい語（図面一式・組立図など）→ 製品図面
    4. 複数ページの PDF で、図面一式としてページごとに分かれていないもの → 書類（本文の語で種別、なければその他）
       （2026-09-30 実データ：複数ページの PDF はほぼ一般書類）
    5. 書類が多いフォルダ（物件対応ファイル他）：本文の語が 1 つでもあれば書類。図番＋表題欄の語がそろえば製品図面。他はその他
    6. 表題欄・ファイル名の規則で読めた図番（信頼度 0.75 以上）→ 製品図面
    7. 本文の言葉（同じ種別の別々の語が 2 つ以上）
    8. DXF、図番あり、本文に表題欄の語が 2 つ以上 → 製品図面
    9. それ以外 → その他
    """
    attrs = attrs if isinstance(attrs, dict) else {}
    p = PurePosixPath(path or "")
    name = norm(p.stem)
    parts = [norm(x) for x in p.parts[:-1]]
    for t, words in TYPE_WORDS.items():
        w = next((w for w in words if w in name), None)
        if w:
            return t, f"ファイル名「{w}」"
    for t, words in TYPE_WORDS.items():
        for part in reversed(parts):
            w = next((w for w in words if w in part), None)
            if w:
                return t, f"フォルダ名「{w}」（{part}）"
    w = next((w for w in DRAWING_NAME_WORDS if w in name), None)
    if w:
        return DRAWING, f"ファイル名「{w}」"
    body = norm((text or "")[:6000])
    found = {t: _found(body, words) for t, words in TYPE_WORDS.items()}
    best = max(found, key=lambda t: len(found[t]))
    tb_words = _hits(body, TITLEBLOCK_WORDS)
    try:
        pages = int(attrs.get("pages") or 1)
    except (TypeError, ValueError):
        pages = 1
    if (kind or "").lower() == "pdf" and pages >= 2 and "page" not in attrs:
        if found[best]:
            return best, f"複数ページのPDF・本文「{'・'.join(found[best][:3])}」"
        return OTHER, "複数ページのPDF（図面一式として分かれていない）"
    if any(f in part for f in DOCUMENT_FOLDERS for part in parts):
        if found[best]:
            return best, f"書類の多いフォルダ・本文「{'・'.join(found[best][:3])}」"
        if drawing_no and tb_words >= 2:
            return DRAWING, "書類の多いフォルダ・図番と表題欄の語"
        return OTHER, "書類の多いフォルダ・図面の決め手なし" + ("（スキャナー名）" if _SCANNER_NAME.match(name) else "")
    if drawing_no and (source in ("attrib", "filename") or (confidence or 0) >= 0.75):
        return DRAWING, "図番（表題欄・ファイル名）"
    if len(found[best]) >= 2:
        return best, "本文「" + "・".join(found[best][:3]) + "」"
    if (kind or "").lower() == "dxf":
        return DRAWING, "DXF"
    if drawing_no:
        return DRAWING, "図番（信頼度低）"
    if tb_words >= 2:
        return DRAWING, "本文に表題欄の語"
    return OTHER, "手がかりなし" if body.strip() else "本文なし（OCR未実施など）"


def doc_type_of(*args, **kwargs) -> str:
    return explain(*args, **kwargs)[0]


def apply(drawing, text=None) -> None:
    """Drawing に型式・系列・文書種別を入れる（保存はしない）。分類で思わぬ値があっても保存や起動を止めない。
    text はページ本文（省略すると DB のページから読む）。"""
    attrs = drawing.attributes if isinstance(drawing.attributes, dict) else {}
    if text is None:
        try:
            text = "\n".join(drawing.pages.order_by("page_no").values_list("text", flat=True)[:3]) if drawing.pk else ""
        except Exception:  # noqa: BLE001
            text = ""
    try:
        if not getattr(drawing, "doc_type_fixed", False):
            drawing.doc_type = doc_type_of(drawing.file.path, drawing.drawing_no, drawing.source, drawing.confidence,
                                           drawing.file.kind, text, attrs)
    except Exception:  # noqa: BLE001
        drawing.doc_type = OTHER
    try:
        drawing.model_family, drawing.model_code = model_of(drawing.file.path, attrs)
    except Exception:  # noqa: BLE001
        drawing.model_family, drawing.model_code = NONE, NONE
    try:
        # 図番の系列は製品図面だけ（契約書などの番号を図番の系列に混ぜない）
        drawing.series_prefix, drawing.series = series_of(drawing.drawing_no) if drawing.doc_type == DRAWING else (NONE, NONE)
    except Exception:  # noqa: BLE001
        drawing.series_prefix, drawing.series = NONE, NONE
