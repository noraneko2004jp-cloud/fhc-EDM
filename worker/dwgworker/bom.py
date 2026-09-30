"""部品表（品番表）の読み取り。座標付きの文字（DXF の文字・OCR の結果）から表を組み立てる。

社内の書式（2026-09-30 サンプル画像と実 DXF 35 件で確認。HCP・HCCA・HCY・HDZY 系で同じ様式）:
  ・表題欄のすぐ上にあり、見出しが一番下。1 行目が見出しのすぐ上、2 行目がその上…と下から積み上がる
  ・見出しは 3 段に分かれている（「特記・厚さ・幅(直径)・長さ・質量・計」「図番又は品番・材質」「名称/規格・員数・材料寸法」）
  ・行番号は部品の文字より 1.5 ほど上、質量は 0.5 ほど下にずれて置かれている
  ・行が多いと、図面の左下に同じ見出しの表がもう 1 つあり、続きの行（6 行目以降など）が入る
  ・表の上に注記が続くことがある
  ・部品表のない単品図面は、表題欄の「組立図番・員数」に親の組立図の図番が書かれている
列の境目は、DXF なら表の縦の罫線から正確に決める。罫線がない（スキャン PDF の OCR）ときは見出しの位置から推し量る。
見出しが上にある一般的な表（下へ積み上がる）にも対応する（行の多い向きを選ぶ）。

items:  [{"text", "x", "y", "h"}]  y は上向き正（DXF と同じ。PDF・OCR も parse 側で上向きにそろえている）
vlines: [(x, y下端, y上端)]  縦の線（DXF の罫線）。なくてもよい
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
    "total": ["計"],
    "supply": ["支給", "社内", "外注", "購入"],
    "item_no": ["見出番号", "見出", "NO.", "NO", "番号"],
}
# 表の見出しの目印になる語（「図番」だけは表題欄にもあるので使わない）
ANCHOR_WORDS = ["図番又は品番", "図番または品番", "図番・品番", "部品番号", "品番", "PARTNO", "PARTNO."]
# 見出しの飾り・2 段目にだけ出る語
HEADER_NOISE = {"+新見出", "-旧図番号", "新見出", "旧図番号", "-図番号", "図番号", "番号", "出", "見", "図", "番", "号", "特記",
                "材料寸法", "材料", "寸法", "新", "旧", "+新", "-旧", "新旧", "+", "-", "記", "特", "称", "名", "格", "規",
                "集成", "取付", "部品", "単一", "給", "実", "社内外"}
# 表題欄にだけある語。これを含む行は部品表の見出しにしない
TITLEBLOCK_MARKERS = ["組立図番", "適用型式", "認可", "点検", "製図", "年月日", "尺度", "表面処理", "得意先", "製番", "USER", "GROUP"]
_WORD_TO_COL = sorted(((unicodedata.normalize("NFKC", w).upper(), c) for c, ws in HEADER_WORDS.items() for w in ws),
                      key=lambda x: -len(x[0]))
_WORDS = {w for w, _ in _WORD_TO_COL}
_ANCHORS = sorted({unicodedata.normalize("NFKC", w).upper() for w in ANCHOR_WORDS}, key=len, reverse=True)
KEY_COLS = {"part_no", "name", "qty"}
STORED_COLS = {"item_no", "part_no", "name", "qty", "material", "thickness", "width", "length", "note"}
MAX_ROWS = 80
_NUM = re.compile(r"^\d{1,3}$")
_LEAD_NUM = re.compile(r"^(\d{1,3})\s+(\S.*)$")


def _key(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "").upper())


def _clean(s: str) -> str:
    """表示・Excel 用：半角カナを全角に、全角英数字を半角に（NFKC）。空白の連続は 1 つに。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


def _h(it):
    return max(it.get("h") or 1.0, 1e-6)


def _width(text, h):
    """文字列のおおよその幅（全角は文字の高さと同じ、半角はその半分）。"""
    return sum(h * (1.0 if unicodedata.east_asian_width(ch) in "WFA" and not ch.isascii() else 0.5) for ch in text)


def _lines(items):
    """高さの近い文字を 1 行にまとめる（上から順）。隣り合う文字どうしの高さの差で鎖のようにつなぐので、
    行番号（1.5 上）や質量（0.5 下）のように少しずれて置かれた文字も同じ行に入る。"""
    if not items:
        return []
    tol = statistics.median(_h(i) for i in items) * 0.55
    rows = []
    for it in sorted(items, key=lambda i: -i["y"]):
        if rows and rows[-1]["ylast"] - it["y"] <= tol:
            rows[-1]["items"].append(it)
            rows[-1]["ylast"] = it["y"]
        else:
            rows.append({"y": it["y"], "ylast": it["y"], "items": [it]})
    for r in rows:
        r["items"].sort(key=lambda i: i["x"])
    return rows


def _cells(line_items, merge=True):
    """同じ行で近い文字をつなげて 1 つのセルにする（見出しが 1 文字ずつ別の文字として置かれた図面など）。
    merge=False なら文字ごとに 1 セル。"""
    cells = []
    for it in line_items:
        h = _h(it)
        w = _width(unicodedata.normalize("NFKC", it["text"]), h)
        both_num = cells and _clean(it["text"]).isdigit() and _clean(cells[-1]["text"]).split()[-1].isdigit()
        if merge and cells and it["x"] - cells[-1]["x1"] < h * 1.2 and not both_num:  # 数字どうしは別の欄
            c = cells[-1]
            gap = " " if it["x"] - c["x1"] > h * 0.6 else ""
            c["text"] += gap + it["text"]
            c["x1"] = max(c["x1"], it["x"] + w)
        else:
            cells.append({"text": it["text"], "x0": it["x"], "x1": it["x"] + w, "h": h})
    for c in cells:
        c["xc"] = (c["x0"] + c["x1"]) / 2
    return cells


def _words(cell):
    """セルの中の見出しの語 → [(列, 語の左端 x, 語の中心 x, 語の長さ)]。
    「厚　さ 幅(直径) 長　さ」のように 1 つの文字に複数の見出しが入っていても、それぞれの位置を出す。"""
    t = unicodedata.normalize("NFKC", cell["text"]).upper()
    if _key(t) in HEADER_NOISE:
        return []
    idx = [i for i, ch in enumerate(t) if not ch.isspace()]
    k = "".join(t[i] for i in idx)
    used = [False] * len(k)
    out = []
    for word, col in _WORD_TO_COL:
        start = k.find(word)
        while start >= 0:
            if not any(used[start:start + len(word)]):
                for j in range(start, start + len(word)):
                    used[j] = True
                x0 = cell["x0"] + _width(t[:idx[start]], cell["h"])
                x1 = cell["x0"] + _width(t[:idx[start + len(word) - 1] + 1], cell["h"])
                out.append((col, x0, (x0 + x1) / 2, len(word)))
            start = k.find(word, start + 1)
    return out


def _has_marker(cells):
    return any(m in _key(c["text"]) for c in cells for m in TITLEBLOCK_MARKERS)


def _headerish(cells):
    """見出しの段らしい行（数字がなく、見出しの語・飾りの語が大半）。"""
    if not cells or _has_marker(cells):
        return False
    keys = [_key(c["text"]) for c in cells]
    hits = sum(1 for k in keys if k in HEADER_NOISE or any(w in k for w in _WORDS))
    nums = [w for c in cells for w in _clean(c["text"]).split() if any(ch.isdigit() for ch in w)]
    if nums:
        # 枝番の見出し（140 150 160 170 など）が「特記・厚さ・幅…」と同じ段にある図面：短い数字だけなら見出しの段とみなす
        return all(re.fullmatch(r"\d{1,4}", w) for w in nums) and hits >= 3 and hits >= (len(keys) - len(nums)) * 0.5
    return hits >= max(1, len(keys) * 0.6)


def _block_cols(lines):
    """見出しの段（複数行）から {列: (左端 x, 中心 x, 語の長さ)}。同じ列なら長い語（より確か）を採る。
    まず文字ごとに、次につなげたセル（1 文字ずつ置かれた見出し）で探す。"""
    found = {}
    for ln in lines:
        for merge in (False, True):
            for c in _cells(ln["items"], merge=merge):
                for col, x0, xc, n in _words(c):
                    if col not in found or n > found[col][2]:
                        found[col] = (x0, xc, n)
    return found


def _ok(cols):
    return len(cols) >= 3 and len(KEY_COLS & set(cols)) >= 2


def _qty(v):
    m = re.search(r"\d+(?:\.\d+)?", (v or "").replace(",", ""))
    return m.group(0) if m else ""


# ---- 列の割り当て ----

def _brackets(vlines, x_lo, x_hi, y_lo, y_hi, h):
    """表の縦の罫線から列の境目（x の並び）。行の範囲（y_lo〜y_hi）を通る、文字の高さ以上の長さの線だけ。"""
    if not vlines:
        return []
    xs = sorted({round(x, 1) for x, a, b in vlines
                 if x_lo <= x <= x_hi and (b - a) >= h * 0.9 and b > y_lo and a < y_hi})
    merged = []
    for x in xs:
        if not merged or x - merged[-1] > h * 0.3:
            merged.append(x)
    return merged


def _extent(vlines, xs, y0, h, up=True):
    """表の縦の罫線をたどって、表の端（上向きなら上端）の y を出す。行ごとに切れた罫線もつないでたどる。
    列ごとの端の中央値を使う（図面の外枠の長い線に引っぱられないように）。"""
    ends = []
    for x in xs:
        segs = [(a, b) for sx, a, b in vlines if abs(sx - x) <= h * 0.3 and b - a >= h * 0.9]
        cur = y0
        if up:
            for a, b in sorted(segs):
                if a <= cur + h * 0.6 and b > cur:
                    cur = b
        else:
            for a, b in sorted(segs, key=lambda s: -s[1]):
                if b >= cur - h * 0.6 and a < cur:
                    cur = a
        if cur != y0:
            ends.append(cur)
    return statistics.median(ends) if ends else None


def _label(bounds, cols, h):
    """境目の間（列）に見出しの語を当てる → [(左, 右, 列名 or None)]"""
    spans = [[a, b, None, 0] for a, b in zip(bounds, bounds[1:])]
    for col, (x0, _xc, n) in cols.items():
        x = x0 + h * 0.5
        for s in spans:
            if s[0] <= x < s[1] and (s[2] is None or n > s[3]):
                s[2], s[3] = col, n
    # 行番号の列は、品番の列のすぐ左（見出しの「見出」が読めないとき）
    labels = [s[2] for s in spans]
    if "item_no" not in labels and "part_no" in labels:
        i = labels.index("part_no")
        if i > 0 and spans[i - 1][2] is None:
            spans[i - 1][2] = "item_no"
    # 員数の欄の中の細い区切り：枝番（HCP4419140ｰ70 など）ごとの員数の欄。2 つ目以降を qty2, qty3… にする
    labels = [s[2] for s in spans]
    if "qty" in labels:
        n = 2
        for s in spans[labels.index("qty") + 1:]:
            if s[2] is not None:
                break
            s[2] = f"qty{n}"
            n += 1
    return [(a, b, c) for a, b, c, _ in spans]


def _assign_by_lines(cells, spans, h):
    out = {}
    for c in cells:
        x = c["x0"] + h * 0.3
        col = next((lab for a, b, lab in spans if a <= x < b), None)
        if col is None or (col not in STORED_COLS and not _QTYN.match(col)):
            continue
        if col == "item_no" and not _NUM.match(_clean(c["text"])):
            continue
        out[col] = (out.get(col, "") + " " + c["text"]).strip()
    return out


_QTYN = re.compile(r"^qty\d+$")


def _assign_by_header(cells, cols):
    """罫線がないとき：表の文字は列の左に寄せて書かれるので、文字の左端寄りの位置で一番近い見出しの列を選ぶ。"""
    names = sorted(cols, key=lambda c: cols[c][1])
    xs = [cols[c][1] for c in names]
    gap = (xs[-1] - xs[0]) / max(len(xs) - 1, 1)
    out = {}
    for c in cells:
        pos = c["x0"] + min((c["x1"] - c["x0"]) / 2, gap * 0.4)
        order = sorted(range(len(xs)), key=lambda i: abs(xs[i] - pos))
        col = names[order[0]]
        is_num = bool(_NUM.match(c["text"].strip()))
        if is_num and ("part_no" not in cols or pos < cols["part_no"][1] - gap * 0.3):
            col = "item_no"
        elif col == "item_no" and not is_num:
            col = next(names[i] for i in order if names[i] != "item_no")
        if col not in STORED_COLS:
            continue
        out[col] = (out.get(col, "") + " " + c["text"]).strip()
    return out


def _fix_lead_num(row):
    m = _LEAD_NUM.match(row.get("part_no", ""))
    if m and not row.get("item_no"):
        row["item_no"], row["part_no"] = m.group(1), m.group(2)
    return row


def _kind(row):
    """行の種類：row（部品の行）／num（行番号だけ）／cont（名称の 2 行目）／other（表の外の文字など）"""
    keys = {k for k, v in row.items() if v and not k.startswith("_")}
    if not keys:
        return "empty"
    if not row.get("part_no") and _key(row.get("name", "")) in HEADER_NOISE | _WORDS:
        return "header"  # 見出しの段の続き（「特記」の欄と、枝番の見出し 140 など）
    if keys and all(_QTYN.match(k) for k in keys - {"qty"}) and not row.get("part_no") and not row.get("name"):
        return "extra" if keys - {"qty"} or row.get("qty") else "empty"
    if keys == {"item_no"} or (keys == {"part_no"} and _NUM.match(row["part_no"])):
        return "num"
    if all(k in {"qty", "material", "thickness", "width", "length", "note"} or _QTYN.match(k) for k in keys):
        return "extra"  # 2 段の高さの行で、員数などだけが行の真ん中に置かれたもの
    if (row.get("qty") and _qty(row["qty"])) or (row.get("part_no") and (row.get("name") or row.get("material"))):
        return "row"
    if row.get("item_no") and (row.get("part_no") or row.get("name")):
        return "row"
    if keys == {"name"}:
        return "cont"
    return "other"


def _rows(lines, start, step, assign, x_lo, x_hi, pitch, y_end=None):
    """見出しの段の外側（start から step の向き）へ表の行を集める。y_end は罫線から分かった表の端。"""
    found, misses = [], 0
    last_y = lines[start - step]["y"] if 0 <= start - step < len(lines) else None
    i = start
    while 0 <= i < len(lines) and len(found) < MAX_ROWS:
        ln = lines[i]
        if last_y is not None and abs(ln["y"] - last_y) > pitch * 4:  # 表の外に出た
            break
        if y_end is not None and ((step < 0 and ln["ylast"] > y_end) or (step > 0 and ln["y"] < y_end)):
            break  # 罫線の外（表の上の図・注記）
        its = [t for t in ln["items"] if x_lo <= t["x"] <= x_hi]
        if not its:
            i += step
            continue
        cells = _cells(its)
        if _has_marker(cells) or (_headerish(cells) and _ok(_block_cols([{"items": its}]))):  # 表題欄・別の表の見出し
            break
        if _headerish(cells):
            last_y = ln["y"]
            i += step
            continue
        row = _fix_lead_num(assign(_cells(its, merge=False)))
        kind = _kind(row)
        if kind in ("empty", "header"):
            pass
        elif kind == "other":
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
    """行番号だけの行を隣の行に付け、名称の 2 行目を前の行の名称に足す（罫線のないスキャン図面向け）。"""
    rows = []
    for r in found:
        if r["_kind"] == "cont":
            if rows:  # 名称の 2 行目：上にある方を先に（読む順）
                prev = rows[-1]
                parts = [r["name"], prev.get("name", "")] if r["_y"] > prev["_y"] else [prev.get("name", ""), r["name"]]
                prev["name"] = " ".join(p for p in parts if p)
            continue
        rows.append(r)
    # 員数などだけの段：その欄が空いている、高さの一番近い行に入れる
    for k, r in enumerate(rows):
        if r["_kind"] != "extra":
            continue
        fields = [f for f in r if not f.startswith("_") and r[f]]
        near = [x for x in rows if x["_kind"] == "row" and not any(x.get(f) for f in fields)]
        if near:
            best = min(near, key=lambda x: abs(x["_y"] - r["_y"]))
            for f in fields:
                best[f] = r[f]
        r["_kind"] = "used"
    out = []
    for k, r in enumerate(rows):
        if r["_kind"] != "num":
            out.append(r)
            continue
        v = int(r.get("item_no") or r.get("part_no"))
        prev = out[-1] if out and out[-1]["_kind"] == "row" and not out[-1].get("item_no") else None
        prev_num = next((int(x["item_no"]) for x in reversed(out[:-1]) if str(x.get("item_no", "")).isdigit()), None)
        nxt = rows[k + 1] if k + 1 < len(rows) and rows[k + 1]["_kind"] == "row" and not rows[k + 1].get("item_no") else None
        if prev is not None and (prev_num is None or prev_num == v - 1 or nxt is None):
            prev["item_no"] = str(v)
        elif nxt is not None:
            nxt["item_no"] = str(v)
    return [r for r in out if r["_kind"] == "row"]


def _table(items, anchor, others, vlines):
    """見出しの目印（「図番又は品番」）1 つぶんの表を読む。"""
    h = _h(anchor)
    x_lo = anchor["x"] - h * 11
    # 右に別の表（続きの表）があれば、その手前まで。高さが違っても（左下の続きの表と右の表など）重ならないように
    right = [o["x"] for o in others if o["x"] > anchor["x"] + h * 20]
    x_hi = min([anchor["x"] + h * 90] + [x - h * 11 for x in right])
    sub = [i for i in items if x_lo <= i["x"] <= x_hi]
    lines = _lines(sub)
    idx = next((n for n, ln in enumerate(lines) if any(it is anchor for it in ln["items"])), None)
    if idx is None:
        return []
    top = bottom = idx
    while top - 1 >= 0 and lines[top - 1]["ylast"] - lines[top]["y"] <= h * 2.0 and _headerish(_cells(lines[top - 1]["items"])):
        top -= 1
    while bottom + 1 < len(lines) and lines[bottom]["ylast"] - lines[bottom + 1]["y"] <= h * 2.0 \
            and _headerish(_cells(lines[bottom + 1]["items"])):
        bottom += 1
    cols = _block_cols(lines[top:bottom + 1])
    if not _ok(cols):
        return []
    pitch = h * 2.5
    col_xs = [v[0] for v in cols.values()]
    t_lo, t_hi = min(col_xs) - h * 10, max(col_xs) + h * 12
    best = []
    for start, step, y_lo, y_hi in ((top - 1, -1, lines[top]["y"], lines[top]["y"] + pitch * 3),
                                    (bottom + 1, +1, lines[bottom]["ylast"] - pitch * 3, lines[bottom]["ylast"])):
        if not 0 <= start < len(lines):
            continue
        bounds = _brackets(vlines, t_lo, t_hi, y_lo, y_hi, h)
        spans = _label(bounds, cols, h) if len(bounds) >= 3 else []
        if spans and any(lab == "part_no" for _, _, lab in spans):
            lo, hi = bounds[0] - h * 0.5, bounds[-1] + h * 0.5
            y0 = lines[top]["y"] if step < 0 else lines[bottom]["ylast"]
            end = _extent(vlines, bounds, y0, h, up=step < 0)
            rows = _rows(lines, start, step, lambda cells, s=spans: _assign_by_lines(cells, s, h), lo, hi, pitch, end)
        else:
            xs = sorted(v[1] for v in cols.values())
            first_gap = xs[1] - xs[0] if len(xs) > 1 else h * 10
            last_gap = xs[-1] - xs[-2] if len(xs) > 1 else h * 10
            lo, hi = xs[0] - max(first_gap * 1.2, h * 4), xs[-1] + max(last_gap * 0.6, h * 1.5)
            rows = _rows(lines, start, step, lambda cells: _assign_by_header(cells, cols), lo, hi, pitch)
        if len(rows) > len(best):
            best = rows
    return best


def _ref_no(part: str) -> str:
    """品番の列の文字が図番そのもの（先頭が図番で、後ろは空白か枝番）のときだけ関連図面にする。
    「0280-RG-TS-2311-16KG」のような購入品・塗料の品番の一部を図番と取り違えない。"""
    p = _clean(part).upper()
    for c in titleblock.find_codes(p):
        if p == c or p.startswith(c + " ") or re.fullmatch(re.escape(c) + r"(-\d{1,3})?", p):
            return c
    return ""


def extract(items, vlines=None) -> list[dict]:
    """部品表の行のリストを返す。見つからなければ []。図面の中に表が複数（続きの表）あれば、行番号順にまとめる。
    行: {item_no, part_no, name, qty, material, thickness, width, length, note, ref_drawing_no, raw_text, confidence}"""
    items = [i for i in items if (i.get("text") or "").strip()]
    anchors = []
    for it in items:
        k = _key(it["text"])
        if any(a in k for a in _ANCHORS) and not any(m in k for m in TITLEBLOCK_MARKERS):
            if not any(abs(a["x"] - it["x"]) < _h(it) * 2 and abs(a["y"] - it["y"]) < _h(it) * 2 for a in anchors):
                anchors.append(it)
    tables = [t for a in anchors if (t := _table(items, a, anchors, vlines or []))]
    if not tables:
        return []
    out, seen = [], set()
    for rows in tables:
        for r in rows:
            part = _clean(r.get("part_no"))
            item_no = _clean(r.get("item_no"))
            subs = sorted((k for k in r if _QTYN.match(k)), key=lambda k: int(k[3:]))
            if subs:  # 枝番ごとの員数
                per = [_qty(r.get("qty"))] + [_qty(r.get(f"qty{n}")) for n in range(2, int(subs[-1][3:]) + 1)]
                last = max(i for i, v in enumerate(per) if v) if any(per) else -1
                if last >= 1:
                    r["note"] = (_clean(r.get("note")) + " 枝番別の員数: " + " / ".join(v or "-" for v in per[:last + 1])).strip()
                if not _qty(r.get("qty")):
                    r["qty"] = next((v for v in per if v), "")
            key = (item_no, part, _clean(r.get("name")))
            if key in seen:
                continue
            seen.add(key)
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
        h = _h(lab)
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
