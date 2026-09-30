"""部品表（品番表）の読み取り。座標付きの文字（DXF の文字・OCR の結果）から表を組み立てる。

社内の書式（2026-09-30 サンプルで確認）:
  ・表題欄のすぐ上にあり、見出しの行（図番又は品番／特記 名称／規格／員数／材質／厚さ／幅(直径)／長さ）が一番下
  ・1 行目が見出しのすぐ上、2 行目がその上…と下から積み上がる
  ・左端の細い列に行番号（見出番号）
見出しが上にある一般的な表（下へ積み上がる）にも対応する（行の多い向きを選ぶ）。

items: [{"text", "x", "y", "h"}]  y は上向き正（DXF と同じ。PDF・OCR も parse 側で上向きにそろえている）
"""
from __future__ import annotations

import re
import statistics
import unicodedata

from . import titleblock

# 見出しの語 → 列。空白を除き NFKC＋大文字で比べる。長い語から順に当てる
HEADER_WORDS = {
    "part_no": ["図番又は品番", "図番または品番", "図番・品番", "部品番号", "品番", "図番", "PARTNO", "PARTNO."],
    "name": ["特記名称", "名称/規格", "名称・規格", "品名", "名称", "規格", "NAME", "DESCRIPTION"],
    "qty": ["員数", "数量", "個数", "QTY", "Q'TY"],
    "material": ["材質", "材料", "MATERIAL"],
    "thickness": ["厚さ", "厚"],
    "width": ["幅(直径)", "幅", "直径"],
    "length": ["長さ", "長"],
    "note": ["備考", "摘要", "REMARKS"],
    "weight": ["計算質量", "単重", "質量", "重量"],
    "supply": ["支給", "社内", "外注", "購入"],
    "item_no": ["見出番号", "見出", "NO.", "NO", "番号"],
}
# 見出しの 2 段目・枠の飾りなど、行として拾わない語
HEADER_NOISE = {"+新見出", "-旧図番号", "新見出", "旧図番号", "特記", "材料寸法", "材料", "寸法", "新", "旧", "+新", "-旧", "新旧", "+", "-", "記", "特", "称", "名", "格", "規",
                "集成", "取付", "部品", "単一", "給", "実"}
_WORD_TO_COL = sorted(((unicodedata.normalize("NFKC", w).upper(), c) for c, ws in HEADER_WORDS.items() for w in ws),
                      key=lambda x: -len(x[0]))
KEY_COLS = {"part_no", "name", "qty"}
MAX_ROWS = 60
_NUM = re.compile(r"^\d{1,3}$")


def _key(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "").upper())


def _lines(items):
    """y の近い文字を 1 行にまとめる（上から順）。各行は x 順の文字のリスト。"""
    if not items:
        return []
    hs = [max(i.get("h") or 1.0, 1e-6) for i in items]
    tol = statistics.median(hs) * 0.5
    rows = []
    for it in sorted(items, key=lambda i: -i["y"]):
        if rows and abs(rows[-1]["y"] - it["y"]) <= tol:
            rows[-1]["items"].append(it)
        else:
            rows.append({"y": it["y"], "items": [it]})
    for r in rows:
        r["items"].sort(key=lambda i: i["x"])
    return rows


def _width(text, h):
    """文字列のおおよその幅（全角は文字の高さの 0.9 倍、半角は 0.5 倍）。"""
    return sum(h * (0.9 if unicodedata.east_asian_width(ch) in "WF" else 0.5) for ch in text)


def _cells(line_items, merge=True):
    """同じ行で近い文字をつなげて 1 つのセルにする（見出しが 1 文字ずつ別の文字として置かれた図面など）。
    merge=False なら文字ごとに 1 セル（表の行は列をまたいでつなげない）。"""
    cells = []
    for it in line_items:
        h = max(it.get("h") or 1.0, 1e-6)
        w = _width(it["text"], h)
        if merge and cells and it["x"] - cells[-1]["x1"] < h * 1.2:
            c = cells[-1]
            c["text"] += (" " if it["x"] - c["x1"] > h * 0.6 else "") + it["text"]
            c["x1"] = max(c["x1"], it["x"] + w)
        else:
            cells.append({"text": it["text"], "x0": it["x"], "x1": it["x"] + w, "h": h})
    for c in cells:
        c["xc"] = (c["x0"] + c["x1"]) / 2
    return cells


def _header_cols(cells):
    """見出しの行なら {列: x中心} を返す。同じ列に当たる語が複数あれば、長い語（より確かな方）を採る。
    例：左端の「見出図番号」の「図番」より、「図番又は品番」を品番の列にする。"""
    found = {}
    for c in cells:
        k = _key(c["text"])
        for word, col in _WORD_TO_COL:
            if word in k:
                if col not in found or len(word) > found[col][1]:
                    found[col] = (c["xc"], len(word))
                k = k.replace(word, "", 1)
                if not k:
                    break
    found = {col: v[0] for col, v in found.items()}
    if len(found) >= 3 and len(KEY_COLS & set(found)) >= 2:
        return found
    return None


def _is_noise(cells):
    """見出しの 2 段目（特記・材料寸法・新旧など）だけの行。数字がなく、短い語だけの行も見出しの一部とみなす。"""
    keys = [_key(c["text"]) for c in cells]
    words = {w for w, _ in _WORD_TO_COL}
    if all(k in HEADER_NOISE or k in words or not k for k in keys):
        return True
    return not any(ch.isdigit() for k in keys for ch in k) and all(len(k) <= 4 for k in keys)


def _assign(cells, cols):
    """セルを列に割り当てる。表の文字は列の左に寄せて書かれるので、文字の左端寄りの位置で一番近い見出しの列を選ぶ。"""
    names = sorted(cols, key=lambda c: cols[c])
    xs = [cols[c] for c in names]
    gap = (xs[-1] - xs[0]) / max(len(xs) - 1, 1)
    out = {}
    for c in cells:
        pos = c["x0"] + min((c["x1"] - c["x0"]) / 2, gap * 0.4)
        col = names[min(range(len(xs)), key=lambda i: abs(xs[i] - pos))]
        # 見出しの左端より左にある短い数字は行番号
        if pos < xs[0] and _NUM.match(c["text"].strip()):
            col = "item_no"
        out[col] = (out.get(col, "") + " " + c["text"]).strip()
    return out


def _qty(v):
    m = re.search(r"\d+(?:\.\d+)?", (v or "").replace(",", ""))
    return m.group(0) if m else ""


def _rows(lines, start, step, cols, x_lo, x_hi, pitch):
    rows, last_y, blank = [], lines[start]["y"], 0
    i = start + step
    while 0 <= i < len(lines) and len(rows) < MAX_ROWS:
        ln = lines[i]
        if abs(ln["y"] - last_y) > pitch * 4:  # 表の外に出た
            break
        its = [t for t in ln["items"] if x_lo <= t["x"] <= x_hi]
        if not its:
            blank += 1
            if blank > 3:
                break
            i += step
            continue
        cells = _cells(its)
        if _header_cols(cells):  # 別の表の見出し
            break
        if _is_noise(cells):
            last_y = ln["y"]
            i += step
            continue
        row = _assign(_cells(its, merge=False), cols)
        if row.get("part_no") or row.get("name"):
            rows.append({**row, "_y": ln["y"]})
            blank = 0
        last_y = ln["y"]
        i += step
    return rows


def extract(items) -> list[dict]:
    """部品表の行のリストを返す。見つからなければ []。
    行: {item_no, part_no, name, qty, material, thickness, width, length, note, ref_drawing_no, raw_text, confidence}"""
    lines = _lines([i for i in items if (i.get("text") or "").strip()])
    best = None
    for idx, ln in enumerate(lines):
        cells = _cells(ln["items"])
        cols = _header_cols(cells)
        if not cols:
            continue
        span = max(cols.values()) - min(cols.values())
        gap = span / max(len(cols) - 1, 1)  # 列のおおよその間隔
        h = statistics.median(c["h"] for c in cells)
        # 左は行番号の列まで、右は最後の見出しの列の幅まで（その右の質量・支給欄などは拾わない）
        xs = sorted(cols.values())
        first_gap = xs[1] - xs[0] if len(xs) > 1 else gap
        last_gap = xs[-1] - xs[-2] if len(xs) > 1 else gap
        x_lo, x_hi = xs[0] - max(first_gap * 1.2, h * 4), xs[-1] + max(last_gap * 0.6, h * 1.5)
        pitch = h * 2.5
        up = _rows(lines, idx, -1, cols, x_lo, x_hi, pitch)    # 見出しの上へ（社内の書式）
        down = _rows(lines, idx, +1, cols, x_lo, x_hi, pitch)  # 見出しの下へ（一般的な表）
        rows = up if len(up) >= len(down) else down
        if rows and (best is None or len(rows) > len(best)):
            best = rows
    if not best:
        return []
    out = []
    for n, r in enumerate(best, 1):
        part = (r.get("part_no") or "").strip()
        codes = titleblock.find_codes(part)  # 図番は品番の列からだけ（名称の「DR323E」のような塗装・材料記号を拾わない）
        item_no = r.get("item_no", "").strip()
        filled = sum(1 for k in ("part_no", "name", "qty", "material") if r.get(k))
        out.append({
            "item_no": item_no if _NUM.match(item_no) else str(n),
            "part_no": part[:64],
            "name": (r.get("name") or "").strip()[:255],
            "qty": _qty(r.get("qty")),
            "material": (r.get("material") or "").strip()[:128],
            "thickness": (r.get("thickness") or "").strip()[:32],
            "width": (r.get("width") or "").strip()[:32],
            "length": (r.get("length") or "").strip()[:32],
            "note": (r.get("note") or "").strip()[:255],
            "ref_drawing_no": codes[0] if codes else "",
            "raw_text": " | ".join(f"{k}={v}" for k, v in r.items() if not k.startswith("_")),
            "confidence": round(0.5 + 0.1 * filled + (0.1 if _NUM.match(item_no) else 0), 2),
        })
    # 行番号の順（1 から）に並べる
    out.sort(key=lambda r: int(r["item_no"]) if r["item_no"].isdigit() else 999)
    return out


_NOTE_REF = re.compile(r"(による|参照|に準ずる|と同じ|に同じ|を見よ)")


def note_refs(lines_text: list[str], own_no: str = "") -> list[str]:
    """注記の「特記以外の詳細は、HUCP101040 による」のような参照図番。"""
    out = []
    for t in lines_text:
        if _NOTE_REF.search(t or ""):
            for c in titleblock.find_codes(t):
                if c != own_no and c not in out:
                    out.append(c)
    return out[:20]
