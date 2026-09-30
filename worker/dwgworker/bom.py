"""部品表（品番表）の読み取り。座標付きの文字（DXF の文字・OCR の結果）から表を組み立てる。

社内の書式（2026-09-30 サンプルと実 DXF 30 件で確認）:
  ・表題欄のすぐ上にあり、見出しが一番下。1 行目が見出しのすぐ上、2 行目がその上…と下から積み上がる
  ・見出しは 2〜3 段に分かれていることが多い（「図番又は品番」「名称/規格・員数・材質」「特記・材料寸法」「厚さ・幅(直径)・長さ」）
  ・左端の細い列に行番号（見出番号）。行番号が部品の文字と少しずれた高さに置かれている図面がある
  ・表の上に注記（「注記：1.溶接要領は…」）が続くことがある
  ・部品表のない単品図面は、表題欄の「組立図番・員数」に親の組立図の図番が書かれている
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
    "material": ["材質", "MATERIAL"],
    "thickness": ["厚さ", "厚"],
    "width": ["幅(直径)", "幅", "直径"],
    "length": ["長さ", "長"],
    "note": ["備考", "摘要", "REMARKS"],
    "weight": ["計算質量", "単重", "質量", "重量"],
    "supply": ["支給", "社内", "外注", "購入"],
    "item_no": ["見出番号", "見出", "NO.", "NO", "番号"],
}
# 見出しの飾り・2 段目にだけ出る語（これだけの行は見出しの一部）
HEADER_NOISE = {"+新見出", "-旧図番号", "新見出", "旧図番号", "出", "特記", "材料寸法", "材料", "寸法", "新", "旧", "+新", "-旧", "新旧",
                "+", "-", "記", "特", "称", "名", "格", "規", "集成", "取付", "部品", "単一", "給", "実", "社内外"}
# 表題欄にだけある語。これを含む行は部品表の見出しにしない（単品図面の「組立図番・員数」欄を部品表と取り違えない）
TITLEBLOCK_MARKERS = ["組立図番", "適用型式", "認可", "点検", "製図", "年月日", "尺度", "表面処理", "得意先", "製番", "USER", "GROUP"]
_WORD_TO_COL = sorted(((unicodedata.normalize("NFKC", w).upper(), c) for c, ws in HEADER_WORDS.items() for w in ws),
                      key=lambda x: -len(x[0]))
KEY_COLS = {"part_no", "name", "qty"}
MAX_ROWS = 80
_NUM = re.compile(r"^\d{1,3}$")
_LEAD_NUM = re.compile(r"^(\d{1,3})\s+(\S.*)$")


def _key(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "").upper())


def _clean(s: str) -> str:
    """表示・Excel 用：半角カナを全角に、全角英数字を半角に（NFKC）。空白の連続は 1 つに。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


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


def _match_cols(cells, raw=None):
    """セルの中の見出しの語 → {列: (x中心, 語の長さ)}。同じ列なら長い語（より確か）を採る。
    raw（つなげる前の文字ごとのセル）を先に見る。「厚さ」「幅(直径)」のように近くに並んだ見出しが 1 つのセルに
    つながると、どちらも同じ位置になってしまうため。1 文字ずつ置かれた見出しは、つなげたセルの方で拾う。"""
    found = {}
    if raw:
        found = _match_cols(raw)
    for c in cells:
        k = _key(c["text"])
        for word, col in _WORD_TO_COL:
            if word in k:
                if col not in found or (len(word) > found[col][1] and not raw):
                    found[col] = (c["xc"], len(word))
                k = k.replace(word, "", 1)
                if not k:
                    break
    return found


def _has_marker(cells):
    return any(m in _key(c["text"]) for c in cells for m in TITLEBLOCK_MARKERS)


def _header_cols(cells, raw=None):
    """見出しの行なら {列: x中心} を返す。"""
    if _has_marker(cells):
        return None
    found = {col: v[0] for col, v in _match_cols(cells, raw).items()}
    if len(found) >= 3 and len(KEY_COLS & set(found)) >= 2:
        return found
    return None


def _headerish(cells):
    """見出しの一部らしい行（数字がなく、見出しの語・飾りの語だけ）。"""
    if not cells or _has_marker(cells):
        return False
    keys = [_key(c["text"]) for c in cells]
    if any(ch.isdigit() for k in keys for ch in k):
        return False
    words = {w for w, _ in _WORD_TO_COL}
    rest = [k for k in keys if k and k not in HEADER_NOISE and k not in words]
    return bool(_match_cols(cells)) and all(any(w in k for w in words) for k in rest)


def _is_noise(cells):
    """見出しの飾りだけの行。数字がなく、短い語だけの行も見出しの一部とみなす。"""
    keys = [_key(c["text"]) for c in cells]
    words = {w for w, _ in _WORD_TO_COL}
    if all(k in HEADER_NOISE or k in words or not k for k in keys):
        return True
    return not any(ch.isdigit() for k in keys for ch in k) and all(len(k) <= 3 for k in keys)


def _assign(cells, cols):
    """セルを列に割り当てる。表の文字は列の左に寄せて書かれるので、文字の左端寄りの位置で一番近い見出しの列を選ぶ。"""
    names = sorted(cols, key=lambda c: cols[c])
    xs = [cols[c] for c in names]
    gap = (xs[-1] - xs[0]) / max(len(xs) - 1, 1)
    out = {}
    for c in cells:
        pos = c["x0"] + min((c["x1"] - c["x0"]) / 2, gap * 0.4)
        order = sorted(range(len(xs)), key=lambda i: abs(xs[i] - pos))
        col = names[order[0]]
        is_num = bool(_NUM.match(c["text"].strip()))
        # 品番の列より左にある短い数字は行番号。行番号の列には数字しか入れない（「P」「X」などの品番は隣の列へ）
        if is_num and ("part_no" not in cols or pos < cols["part_no"] - gap * 0.3):
            col = "item_no"
        elif col == "item_no" and not is_num:
            col = next(names[i] for i in order if names[i] != "item_no")
        out[col] = (out.get(col, "") + " " + c["text"]).strip()
    # 行番号が品番の文字の先頭にくっついている（「1 HCY1301213」）
    m = _LEAD_NUM.match(out.get("part_no", ""))
    if m and not out.get("item_no"):
        out["item_no"], out["part_no"] = m.group(1), m.group(2)
    return out


def _kind(row):
    """行の種類：row（部品の行）／num（行番号だけ）／cont（名称の 2 行目）／other（表の外の文字など）"""
    keys = {k for k, v in row.items() if v and not k.startswith("_")}
    if keys == {"item_no"} or (keys == {"part_no"} and _NUM.match(row["part_no"])):
        return "num"
    if row.get("qty") and _qty(row["qty"]) or (row.get("part_no") and (row.get("name") or row.get("material"))):
        return "row"
    if row.get("item_no") and (row.get("part_no") or row.get("name")):
        return "row"
    if keys == {"name"}:
        return "cont"
    return "other"


def _qty(v):
    m = re.search(r"\d+(?:\.\d+)?", (v or "").replace(",", ""))
    return m.group(0) if m else ""


def _block(lines, idx, cols, pitch):
    """見出しが何段かに分かれているとき、上下の見出しの段もまとめて列を補う。(上端, 下端, 列) を返す。"""
    top = bottom = idx
    cols = dict(cols)
    for step in (-1, 1):
        j = idx
        while 0 <= j + step < len(lines) and abs(lines[j + step]["y"] - lines[j]["y"]) <= pitch * 1.3:
            cells = _cells(lines[j + step]["items"])
            if not _headerish(cells):
                break
            for col, (xc, _) in _match_cols(cells, _cells(lines[j + step]["items"], merge=False)).items():
                cols.setdefault(col, xc)
            j += step
        if step < 0:
            top = j
        else:
            bottom = j
    return top, bottom, cols


def _rows(lines, start, step, cols, x_lo, x_hi, pitch):
    """見出しの段の外側（start から step の向き）へ表の行を集める。"""
    found, last_y, misses = [], lines[start - step]["y"], 0
    i = start
    while 0 <= i < len(lines) and len(found) < MAX_ROWS:
        ln = lines[i]
        if abs(ln["y"] - last_y) > pitch * 4:  # 表の外に出た
            break
        its = [t for t in ln["items"] if x_lo <= t["x"] <= x_hi]
        if not its:
            misses += 1
            if misses > 3:
                break
            i += step
            continue
        cells = _cells(its)
        if _header_cols(cells) or _has_marker(cells):  # 別の表の見出し・表題欄
            break
        if _is_noise(cells):
            last_y = ln["y"]
            i += step
            continue
        row = _assign(_cells(its, merge=False), cols)
        kind = _kind(row)
        if kind == "other":
            misses += 1
            if misses >= 2:  # 表の外（注記など）に出た
                break
        else:
            misses = 0
            found.append({**row, "_y": ln["y"], "_kind": kind})
        last_y = ln["y"]
        i += step
    return _tidy(found)


def _tidy(found):
    """行番号だけの行を隣の行に付け、名称の 2 行目を前の行の名称に足す。"""
    rows = []
    for r in found:
        if r["_kind"] == "cont":
            if rows:
                rows[-1]["name"] = (rows[-1].get("name", "") + " " + r["name"]).strip()
            continue
        rows.append(r)
    out = []
    for k, r in enumerate(rows):
        if r["_kind"] != "num":
            out.append(r)
            continue
        v = int(r.get("item_no") or r.get("part_no"))
        prev = out[-1] if out and out[-1]["_kind"] == "row" and not out[-1].get("item_no") else None
        prev_num = next((int(x["item_no"]) for x in reversed(out[:-1]) if x.get("item_no", "").isdigit()), None)
        nxt = rows[k + 1] if k + 1 < len(rows) and rows[k + 1]["_kind"] == "row" and not rows[k + 1].get("item_no") else None
        if prev is not None and (prev_num is None or prev_num == v - 1 or nxt is None):
            prev["item_no"] = str(v)
        elif nxt is not None:
            nxt["item_no"] = str(v)
    return [r for r in out if r["_kind"] == "row"]


def _ref_no(part: str) -> str:
    """品番の列の文字が図番そのもの（先頭が図番で、後ろは空白か枝番）のときだけ関連図面にする。
    「0280-RG-TS-2311-16KG」のような購入品・塗料の品番の一部を図番と取り違えない。"""
    p = _clean(part).upper()
    for c in titleblock.find_codes(p):
        if p == c or p.startswith(c + " ") or re.fullmatch(re.escape(c) + r"(-\d{1,3})?", p):
            return c
    return ""


def extract(items) -> list[dict]:
    """部品表の行のリストを返す。見つからなければ []。
    行: {item_no, part_no, name, qty, material, thickness, width, length, note, ref_drawing_no, raw_text, confidence}"""
    lines = _lines([i for i in items if (i.get("text") or "").strip()])
    best = None
    for idx, ln in enumerate(lines):
        cells = _cells(ln["items"])
        cols = _header_cols(cells, _cells(ln["items"], merge=False))
        if not cols:
            continue
        h = statistics.median(c["h"] for c in cells)
        pitch = h * 2.5
        top, bottom, cols = _block(lines, idx, cols, pitch)
        xs = sorted(cols.values())
        gap = (xs[-1] - xs[0]) / max(len(xs) - 1, 1)
        first_gap = xs[1] - xs[0] if len(xs) > 1 else gap
        last_gap = xs[-1] - xs[-2] if len(xs) > 1 else gap
        # 左は行番号の列まで、右は最後の見出しの列の幅まで（その右の質量・支給欄などは拾わない）
        x_lo, x_hi = xs[0] - max(first_gap * 1.2, h * 4), xs[-1] + max(last_gap * 0.6, h * 1.5)
        up = _rows(lines, top - 1, -1, cols, x_lo, x_hi, pitch) if top > 0 else []  # 見出しの上へ（社内の書式）
        down = _rows(lines, bottom + 1, +1, cols, x_lo, x_hi, pitch) if bottom + 1 < len(lines) else []  # 下へ（一般的な表）
        rows = up if len(up) >= len(down) else down
        if rows and (best is None or len(rows) > len(best)):
            best = rows
    if not best:
        return []
    out = []
    for n, r in enumerate(best, 1):
        part = _clean(r.get("part_no"))
        item_no = (r.get("item_no") or "").strip()
        filled = sum(1 for k in ("part_no", "name", "qty", "material") if r.get(k))
        out.append({
            "item_no": item_no if _NUM.match(item_no) else "",
            "part_no": part[:64],
            "name": _clean(r.get("name"))[:255],
            "qty": _qty(r.get("qty")),
            "material": _clean(r.get("material"))[:128],
            "thickness": _clean(r.get("thickness"))[:32],
            "width": _clean(r.get("width"))[:32],
            "length": _clean(r.get("length"))[:32],
            "note": _clean(r.get("note"))[:255],
            "ref_drawing_no": _ref_no(part),
            "raw_text": " | ".join(f"{k}={v}" for k, v in r.items() if not k.startswith("_")),
            "confidence": round(min(1.0, 0.5 + 0.1 * filled + (0.1 if _NUM.match(item_no) else 0)), 2),
        })
    # 行番号の順（1 から）に並べる。番号のない行は元の順で後ろへ
    out.sort(key=lambda r: int(r["item_no"]) if r["item_no"] else 999)
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


def assembly_refs(items, own_no: str = "") -> list[str]:
    """表題欄の「組立図番」欄に書かれた親の組立図の図番（この部品を使っている図面）。
    欄の名前の真上・右隣にある図番を拾う。"""
    labels = [i for i in items if "組立図番" in _key(i.get("text"))]
    out = []
    for lab in labels:
        h = max(lab.get("h") or 1.0, 1e-6)
        x0, x1 = lab["x"] - h * 2, lab["x"] + _width(lab["text"], h) + h * 12
        for it in items:
            if it is lab or not (x0 <= it["x"] <= x1):
                continue
            dy = it["y"] - lab["y"]
            if -h * 0.6 <= dy <= h * 6:
                for c in titleblock.find_codes(it["text"]):
                    if c != own_no and c not in out:
                        out.append(c)
    return out[:10]
