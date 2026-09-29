import io

from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.conf import settings
from pathlib import Path

from .models import AuditLog, Drawing
from .search import search
from .sources import open_source


def index(request):
    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    terms, qs = search(q, kind)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "drawings/search.html", {
        "q": q, "kind": kind, "terms": terms, "page": page, "total": page.paginator.count,
    })


def detail(request, pk):
    d = get_object_or_404(Drawing.objects.select_related("file"), pk=pk)
    AuditLog.objects.create(user=request.user, action=AuditLog.Action.VIEW, target=d.file.path)
    revisions = Drawing.objects.filter(drawing_no=d.drawing_no).exclude(pk=d.pk).select_related("file") if d.drawing_no else []
    siblings = d.file.drawings.exclude(pk=d.pk).order_by("page_no")  # 同じ図面一式の他のページ
    return render(request, "drawings/detail.html", {
        "d": d, "bom": d.bom_items.all(), "revisions": revisions, "siblings": siblings, "q": request.GET.get("q", ""),
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
