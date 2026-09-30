"""スキャン画像から表の縦の罫線を見つける（部品表の列の境目に使う。DXF の罫線と同じ形で返す）。

画素の列ごとに黒い画素が縦に続く長さを調べ、一定以上続くものを縦の線とみなす。
スキャンの傾きで線が数画素ずれても、隣り合う列の線をまとめてから返す。
"""
from __future__ import annotations

import numpy as np

MIN_LEN_MM = 3.0   # これより短い縦の黒い連なり（文字の画など）は線とみなさない
DARK = 140         # これより暗い画素を黒とみなす（0〜255）


def vertical_lines(gray: np.ndarray, page_w: float, page_h: float, region=None) -> list[tuple[float, float, float]]:
    """gray: (高さ, 幅) の 0〜255 の画像。page_w/page_h: ページの大きさ（pt）。
    region: 調べる範囲（ページ座標 pt、y 上向き） (x0, y0, x1, y1)。省略でページ全体。
    [(x, y下端, y上端)] をページ座標（pt、y 上向き）で返す。"""
    H, W = gray.shape
    sx, sy = page_w / W, page_h / H  # 画素 → pt
    c0, c1, r0, r1 = 0, W, 0, H
    if region:
        x0, y0, x1, y1 = region
        c0, c1 = max(0, int(x0 / sx)), min(W, int(x1 / sx) + 1)
        r0, r1 = max(0, int((page_h - y1) / sy)), min(H, int((page_h - y0) / sy) + 1)
    dark = gray[r0:r1, c0:c1] < DARK
    if dark.size == 0:
        return []
    pad = np.zeros((1, dark.shape[1]), dtype=bool)
    d = np.diff(np.vstack([pad, dark, pad]).astype(np.int8), axis=0)
    sr, sc = np.nonzero(d == 1)    # 黒の始まり（行, 列）
    er, ec = np.nonzero(d == -1)   # 黒の終わり
    so, eo = np.lexsort((sr, sc)), np.lexsort((er, ec))
    sr, sc, er = sr[so], sc[so], er[eo]
    length = er - sr
    min_px = MIN_LEN_MM / 25.4 * 72 / sy
    keep = length >= min_px
    segs = sorted(zip(sc[keep] + c0, sr[keep] + r0, er[keep] + r0))
    # 隣り合う列（線の太さ・傾き）で重なる区間をまとめる
    merged = []
    for col, top, bot in segs:
        for m in reversed(merged[-12:]):
            if col - m[1] <= 2 and top < m[3] and bot > m[2]:
                m[1] = col
                m[2], m[3] = min(m[2], top), max(m[3], bot)
                m[4] += 1
                break
        else:
            merged.append([col, col, top, bot, 1])
    out = []
    for c_first, c_last, top, bot, _n in merged:
        x = (c_first + c_last + 1) / 2 * sx
        out.append((x, page_h - bot * sy, page_h - top * sy))
    return out


def from_pixmap(pix, page_w: float, page_h: float, region=None):
    """PyMuPDF の Pixmap（グレー）から縦の線。"""
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, 0]
    return vertical_lines(arr, page_w, page_h, region)


def lone_ones(gray: np.ndarray, h_px: float) -> list[tuple[float, float, float]]:
    """表の欄の画像から、ぽつんと 1 つだけある「1」を探す（OCR が読み落としやすい）。
    gray：欄（1 行ぶん）を切り出した 0〜255 の画像。h_px：文字の高さ（画素）。
    罫線（縦・横に長く続く黒）を除いた後、罫線で区切られた枠ごとに、黒いかたまりが細くて文字の高さのもの 1 つだけなら「1」とみなす。
    [(x中心, y上, y下)]（画素）を返す。"""
    dark = gray < DARK
    H, W = dark.shape
    if H == 0 or W == 0:
        return []
    rule_cols = dark.mean(axis=0) > 0.85  # 欄を縦に貫く線だけ（「1」は欄の高さの 6〜7 割）
    rule_rows = dark.mean(axis=1) > 0.5
    ink = dark.copy()
    ink[:, rule_cols] = False
    ink[rule_rows, :] = False
    # 罫線のすぐ横の数画素（線の太さ・かすれ）も除く
    for c in np.nonzero(rule_cols)[0]:
        ink[:, max(0, c - 2):c + 3] = False
    for r in np.nonzero(rule_rows)[0]:
        ink[max(0, r - 2):r + 3, :] = False
    # 枠の区切り（縦の罫線）
    edges = [0] + [int(c) for c in np.nonzero(rule_cols)[0]] + [W]
    out = []
    for a, b in zip(edges, edges[1:]):
        if b - a < h_px * 0.4:
            continue
        cols = ink[:, a:b].sum(axis=0)
        runs, start = [], None
        for i, v in enumerate(list(cols) + [0]):
            if v > 0 and start is None:
                start = i
            elif v == 0 and start is not None:
                runs.append((start, i))
                start = None
        runs = [(s, e) for s, e in runs if ink[:, a + s:a + e].sum() >= h_px * 0.3]  # ごみ（点）は無視
        if len(runs) != 1:
            continue
        s, e = runs[0]
        rows = np.nonzero(ink[:, a + s:a + e].any(axis=1))[0]
        tall = rows[-1] - rows[0] + 1
        wide = e - s
        if h_px * 0.5 <= tall <= h_px * 1.3 and wide <= max(3, h_px * 0.18):  # 「2」「3」も細い書体なので幅は線の太さ程度まで
            out.append(((a + s + a + e) / 2, float(rows[0]), float(rows[-1])))
    return out
