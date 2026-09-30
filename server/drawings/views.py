import io

from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.conf import settings
from pathlib import Path

from .models import AuditLog, Drawing
from .search import search
from .sources import open_source


PER_PAGE = 50


def _int(v, default=None):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def _qs(**kw):
    """空の値を省いたクエリ文字列（先頭の ? 付き）。"""
    kw = {k: v for k, v in kw.items() if v not in ("", None)}
    return "?" + urlencode(kw) if kw else ""


def _page_window(page, around=2):
    """ページ番号の並び（間は None＝「…」）。"""
    n, cur = page.paginator.num_pages, page.number
    keep = {1, n, *range(cur - around, cur + around + 1)}
    out, prev = [], 0
    for i in sorted(x for x in keep if 1 <= x <= n):
        if i - prev > 1:
            out.append(None)
        out.append(i)
        prev = i
    return out


def index(request):
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    terms, qs = search(q, kind)
    page = Paginator(qs, PER_PAGE).get_page(request.GET.get("page"))
    base = page.start_index() - 1 if page.paginator.count else 0
    items = [(base + n, d, _qs(q=q, kind=kind, i=base + n)) for n, d in enumerate(page.object_list)]
    return render(request, "drawings/search.html", {
        "q": q, "kind": kind, "terms": terms, "page": page, "total": page.paginator.count, "items": items,
        "window": _page_window(page), "page_qs": _qs(q=q, kind=kind),
    })


def detail(request, pk):
    """検索一覧から開いたときは q・kind・i（一覧での位置）を受け取り、
    「一覧に戻る」で同じページ・同じ位置へ戻れるようにし、前後の図面へ移れるようにする。"""
    d = get_object_or_404(Drawing.objects.select_related("file"), pk=pk)
    AuditLog.objects.create(user=request.user, action=AuditLog.Action.VIEW, target=d.file.path)
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    i = _int(request.GET.get("i"))
    back_page = _int(request.GET.get("p"), 1) or 1
    nav = {}
    if i is not None:
        _, qs = search(q, kind)
        window = list(qs[max(i - 1, 0):i + 2])
        cur = i - max(i - 1, 0)
        if cur < len(window) and window[cur].pk == d.pk:  # 一覧が変わっていなければ前後を出す
            back_page = i // PER_PAGE + 1
            nav["total"] = qs.count()
            nav["pos"] = i + 1
            if cur > 0:
                nav["prev"] = (window[cur - 1], _qs(q=q, kind=kind, i=i - 1))
            if cur + 1 < len(window):
                nav["next"] = (window[cur + 1], _qs(q=q, kind=kind, i=i + 1))
        else:
            back_page = i // PER_PAGE + 1
    back = reverse("index") + _qs(q=q, kind=kind, page=back_page if back_page > 1 else None)
    if i is not None:
        back += f"#d{d.pk}"
    keep = _qs(q=q, kind=kind, p=back_page if back_page > 1 else None)  # 同じファイルの図面などへ移っても戻り先を保つ
    revisions = Drawing.objects.filter(drawing_no=d.drawing_no).exclude(pk=d.pk).select_related("file") if d.drawing_no else []
    siblings = d.file.drawings.exclude(pk=d.pk).order_by("page_no")  # 同じ図面一式の他のページ
    return render(request, "drawings/detail.html", {
        "d": d, "bom": d.bom_items.all(), "revisions": revisions, "siblings": siblings, "q": q,
        "back": back, "keep": keep, "nav": nav,
    })


def thumbnail(request, pk):
    d = get_object_or_404(Drawing, pk=pk)
    if not d.thumbnail:
        raise Http404
    p = Path(settings.MEDIA_ROOT) / d.thumbnail
    if not p.exists():
        raise Http404
    resp = FileResponse(open(p, "rb"), content_type="image/png")
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


def download(request, pk):
    d = get_object_or_404(Drawing.objects.select_related("file"), pk=pk)
    try:
        fh = open_source(d.file.path)
    except (OSError, ValueError) as e:
        return HttpResponse(f"原本を開けませんでした（{e}）。ファイルサーバーへの接続設定を確認してください。", status=502)
    AuditLog.objects.create(user=request.user, action=AuditLog.Action.DOWNLOAD, target=d.file.path)
    return FileResponse(fh, as_attachment=True, filename=d.file.filename)


def bom_xlsx(request, pk):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    d = get_object_or_404(Drawing.objects.select_related("file"), pk=pk)
    wb = Workbook()
    ws = wb.active
    ws.title = "部品表"
    ws.append([f"{d.drawing_no} Rev.{d.revision}", d.title])
    ws.append([])
    head = ["No.", "品番", "品名", "数量", "材質", "図番", "信頼度"]
    ws.append(head)
    for c in ws[3]:
        c.font = Font(bold=True)
    low = PatternFill("solid", fgColor="FFF2CC")
    for b in d.bom_items.all():
        ws.append([b.item_no or b.row, b.part_no, b.name, float(b.qty) if b.qty is not None else None,
                   b.material, b.ref_drawing_no, round(b.confidence, 2)])
        if b.confidence < 0.8:
            for c in ws[ws.max_row]:
                c.fill = low
    for col, w in zip("ABCDEFG", (6, 16, 32, 8, 12, 14, 8)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    AuditLog.objects.create(user=request.user, action=AuditLog.Action.EXPORT, target=d.file.path)
    name = f"{d.drawing_no or d.file.filename}_部品表.xlsx"
    return FileResponse(buf, as_attachment=True, filename=name)
