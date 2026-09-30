"""一覧の見せ方（一覧／フォルダ別／型式別／図番系列別）と、その絞り込み・並び・階層。

どの見せ方でも一覧の 1 件は「1 ファイル」（図面一式の PDF は 1 件にまとめる）。
代表として出す図面は、そのファイルの中で条件に合った最初のページ。
詳細画面の前後移動も同じ並びを使うので、一覧と詳細でここを共有する。
"""
import re
from urllib.parse import urlencode

from django.db.models import Count, F, Min, Q, Value
from django.db.models.functions import StrIndex, Substr

from . import access
from .classify import NONE
from .models import Drawing
from .search import search

VIEWS = {"list": "一覧", "folder": "フォルダ別", "model": "型式別", "series": "図番系列別"}
STATE_KEYS = ("q", "kind", "t", "v", "f", "m", "s")
DOC_FILTERS = {"drawing": "製品図面", "all": "すべての文書", "site": "敷地・土地図", "contract": "契約書・見積",
               "application": "申請書類", "other": "その他"}
MAX_CHILDREN = 300  # 左の階層に一度に出す数（図番の系列は数千になるため）
NONE_LABEL = {"model": "（型式なし）", "series": "（図番なし）"}


def state_from(get, default_view="list", see_all=True):
    st = {k: (get.get(k) or "").strip() for k in STATE_KEYS}
    # 文書種別：既定は製品図面。ゲストは製品図面だけ（URL で変えても無視）
    if st["t"] not in DOC_FILTERS or not see_all:
        st["t"] = "drawing"
    if st["v"] not in VIEWS:
        st["v"] = default_view
    # 見せ方に関係のない絞り込みは外す（タブを切り替えたときに残らないように）
    for key, view in (("f", "folder"), ("m", "model"), ("s", "series")):
        if st["v"] != view:
            st[key] = ""
    return st


def qs_string(st, **extra):
    """空の値を省いたクエリ文字列（先頭の ? 付き）。"""
    kw = {**{k: st.get(k, "") for k in STATE_KEYS}, **extra}
    if kw.get("v") == "list":
        kw["v"] = ""  # 既定の見せ方は URL に出さない
    if kw.get("t") == "drawing":
        kw["t"] = ""
    kw = {k: v for k, v in kw.items() if v not in ("", None)}
    return "?" + urlencode(kw) if kw else ""


def _two_level(value, field_hi, field_lo):
    """「CAK」→ 系統で、「CAK/CAK-A」→ 型式で絞る。"""
    hi, _, lo = value.partition("/")
    return Q(**{field_lo: lo}) if lo else Q(**{field_hi: hi})


def filtered(st, user):
    """(展開語, 条件に合い、その人が見てよい図面の QuerySet)。"""
    terms, qs = search(st["q"], st["kind"])
    qs = access.visible(user, qs)
    if st["t"] != "all":
        qs = qs.filter(doc_type=st["t"])
    if st["f"]:
        qs = qs.filter(file__path__startswith=st["f"].rstrip("/") + "/")
    if st["m"]:
        qs = qs.filter(_two_level(st["m"], "model_family", "model_code"))
    if st["s"]:
        qs = qs.filter(_two_level(st["s"], "series_prefix", "series"))
    return terms, qs


def groups(qs, view):
    """1 ファイル 1 件にまとめた並び。values: file_id, first_page, hits, key_no, path。"""
    g = (qs.order_by().values("file_id")
         .annotate(first_page=Min("page_no"), hits=Count("id"),
                   key_no=Min("drawing_no", filter=~Q(drawing_no="")), path=Min("file__path"),
                   model=Min("model_code")))
    if view == "folder":
        return g.order_by("path")
    if view == "model":
        return g.order_by("model", F("key_no").asc(nulls_last=True), "path")
    return g.order_by(F("key_no").asc(nulls_last=True), "path")  # 図番のないものは後ろへ


def drawings_for(rows):
    """groups() の 1 ページ分 → [(行, 代表の図面, そのファイルの図面数)]"""
    rows = list(rows)
    if not rows:
        return []
    cond = Q()
    for r in rows:
        cond |= Q(file_id=r["file_id"], page_no=r["first_page"])
    found = {(d.file_id, d.page_no): d for d in Drawing.objects.select_related("file").filter(cond)}
    sizes = dict(Drawing.objects.filter(file_id__in=[r["file_id"] for r in rows]).order_by()
                 .values("file_id").annotate(n=Count("id")).values_list("file_id", "n"))
    return [(r, found[(r["file_id"], r["first_page"])], sizes.get(r["file_id"], 1))
            for r in rows if (r["file_id"], r["first_page"]) in found]


# ---- 左側の階層 ----

def _folder_children(qs, prefix):
    """prefix（"" か "a/b"）の直下のフォルダと、そのフォルダ以下の図面数。"""
    start = len(prefix) + 2 if prefix else 1
    base = qs.filter(file__path__startswith=prefix + "/") if prefix else qs
    rest = Substr("file__path", start)
    rows = (base.order_by()
            .annotate(rest=rest)
            .filter(rest__contains="/")
            .annotate(child=Substr(rest, 1, StrIndex(rest, Value("/")) - 1))
            .values("child").annotate(n=Count("id")).order_by("child"))
    return sorted(((r["child"], r["n"]) for r in rows), key=lambda x: natural(x[0]))


def natural(s):
    """「sub2」が「sub10」より前に来る並び。"""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", s or "") if t]


def tree(st, qs):
    """見せ方に応じた階層：[{label, value, n, depth, open, current}]"""
    v = st["v"]
    nodes = []
    if v == "folder":
        cur = st["f"].strip("/")
        parts = cur.split("/") if cur else []

        def walk(prefix, depth):
            for name, n in _folder_children(qs, prefix):
                value = f"{prefix}/{name}" if prefix else name
                on_path = depth < len(parts) and parts[depth] == name
                nodes.append({"label": name, "value": value, "n": n, "depth": depth,
                              "open": on_path, "current": value == cur})
                if on_path:
                    walk(value, depth + 1)

        walk("", 0)
    elif v in ("model", "series"):
        hi, lo = ("model_family", "model_code") if v == "model" else ("series_prefix", "series")
        sel = st["m"] if v == "model" else st["s"]
        sel_hi, _, sel_lo = sel.partition("/")
        for r in qs.order_by().values(hi).annotate(n=Count("id")).order_by(hi):
            h = r[hi] or NONE
            nodes.append({"label": NONE_LABEL[v] if h == NONE else h, "value": h, "n": r["n"], "depth": 0,
                          "open": h == sel_hi, "current": h == sel_hi and not sel_lo})
            if h == sel_hi and h != NONE:
                kids = qs.filter(**{hi: h}).order_by().values(lo).annotate(n=Count("id")).order_by(lo)
                shown = list(kids[:MAX_CHILDREN])
                if sel_lo and all(c[lo] != sel_lo for c in shown):  # 選択中のものは必ず出す
                    shown += [c for c in kids.filter(**{lo: sel_lo})]
                for c in shown:
                    val = f"{h}/{c[lo]}"
                    nodes.append({"label": c[lo], "value": val, "n": c["n"], "depth": 1, "open": False,
                                  "current": val == sel})
                if len(shown) >= MAX_CHILDREN:
                    rest = kids.count() - len(shown)
                    if rest > 0:
                        nodes.append({"label": f"… 他 {rest} 件（検索語で絞り込めます）", "value": None, "n": "",
                                      "depth": 1, "open": False, "current": False})
    return nodes


def crumbs(st):
    """選択中の階層のパンくず：[(表示, 値)]"""
    v = st["v"]
    sel = {"folder": st["f"], "model": st["m"], "series": st["s"]}.get(v, "")
    if not sel:
        return []
    sep = "/"
    parts = sel.strip("/").split(sep)
    out = []
    for i, p in enumerate(parts):
        label = NONE_LABEL.get(v, p) if p == NONE else p
        out.append((label, sep.join(parts[:i + 1])))
    return out
