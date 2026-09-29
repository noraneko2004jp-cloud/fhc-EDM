"""Mac mini の解析ワーカー専用の社内API。

ワーカーは DB に直接つながず、このAPIだけを使う:
  POST scan          巡回で見つけたファイル一覧を送る（新規・変更はジョブ化）
  POST scan/finish   巡回の終わりを知らせる（見つからなくなったファイルを印付け）
  POST jobs/claim    次の仕事をもらう
  POST jobs/<id>/result  解析結果とサムネイルを送る
  POST jobs/<id>/fail    失敗を知らせる（3回まで再試行）
  GET  health        状態確認
"""
import datetime as dt
import functools
import hmac
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import BomItem, Drawing, Job, Page, SourceFile
from .text import build_search_text

MAX_ATTEMPTS = 3
STALE_AFTER = dt.timedelta(minutes=30)
KINDS = {".dxf": SourceFile.Kind.DXF, ".pdf": SourceFile.Kind.PDF}


def _client_ip(request):
    return request.META.get("REMOTE_ADDR", "")


def worker_api(view):
    """トークン（と、設定があれば接続元IP）で認証する。"""

    @csrf_exempt
    @login_not_required
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        token = settings.WORKER_TOKEN
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token or not hmac.compare_digest(given, token):
            return JsonResponse({"error": "認証に失敗しました。WORKER_TOKEN を確認してください"}, status=401)
        if settings.WORKER_ALLOWED_IPS and _client_ip(request) not in settings.WORKER_ALLOWED_IPS:
            return JsonResponse({"error": f"接続元 {_client_ip(request)} は許可されていません"}, status=403)
        return view(request, *args, **kwargs)

    return wrapped


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None


@require_GET
@worker_api
def health(request):
    counts = {s: Job.objects.filter(state=s).count() for s in (Job.State.QUEUED, Job.State.RUNNING, Job.State.FAILED)}
    return JsonResponse({"ok": True, "jobs": counts, "files": SourceFile.objects.count()})


@require_POST
@worker_api
def scan(request):
    data = _body(request)
    if data is None or not isinstance(data.get("files"), list):
        return JsonResponse({"error": "files の配列が必要です"}, status=400)
    now = timezone.now()
    created = changed = unchanged = skipped = 0
    for f in data["files"]:
        path = str(f.get("path", "")).replace("\\", "/").lstrip("/")
        kind = KINDS.get(Path(path).suffix.lower())
        if not path or not kind:
            skipped += 1
            continue
        size = int(f["size"])
        mtime = dt.datetime.fromtimestamp(float(f["mtime"]), tz=dt.timezone.utc)
        with transaction.atomic():
            obj, is_new = SourceFile.objects.select_for_update().get_or_create(
                path=path, defaults={"kind": kind, "size": size, "mtime": mtime, "last_seen": now}
            )
            if is_new:
                created += 1
                Job.objects.create(file=obj)
                continue
            if obj.size != size or obj.mtime != mtime or obj.status == SourceFile.Status.MISSING:
                changed += 1
                obj.size, obj.mtime, obj.status, obj.error = size, mtime, SourceFile.Status.PENDING, ""
                if not obj.jobs.filter(state__in=[Job.State.QUEUED, Job.State.RUNNING]).exists():
                    Job.objects.create(file=obj)
            else:
                unchanged += 1
            obj.last_seen = now
            obj.save()
    return JsonResponse({"created": created, "changed": changed, "unchanged": unchanged, "skipped": skipped})


@require_POST
@worker_api
def scan_finish(request):
    data = _body(request) or {}
    try:
        started = dt.datetime.fromtimestamp(float(data["started_at"]), tz=dt.timezone.utc)
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"error": "started_at（巡回開始時刻のUNIX秒）が必要です"}, status=400)
    if not data.get("complete"):
        return JsonResponse({"missing": 0, "note": "途中で終わった巡回のため、見つからないファイルの印付けは行いません"})
    missing = (
        SourceFile.objects.exclude(status=SourceFile.Status.MISSING)
        .filter(last_seen__lt=started)
        .update(status=SourceFile.Status.MISSING)
    )
    return JsonResponse({"missing": missing})


@require_POST
@worker_api
def claim(request):
    data = _body(request) or {}
    worker = str(data.get("worker", ""))[:64]
    limit = max(1, min(int(data.get("limit", 1)), 20))
    stages = data.get("stages") or [Job.Stage.PARSE]
    now = timezone.now()
    # 途中で止まったワーカーの仕事を戻す
    Job.objects.filter(state=Job.State.RUNNING, updated_at__lt=now - STALE_AFTER).update(state=Job.State.QUEUED)
    with transaction.atomic():
        jobs = list(
            Job.objects.select_for_update(skip_locked=True)
            .select_related("file")
            .filter(state=Job.State.QUEUED, stage__in=stages)
            .order_by("priority", "id")[:limit]
        )
        for j in jobs:
            j.state, j.worker, j.attempts = Job.State.RUNNING, worker, j.attempts + 1
            j.save(update_fields=["state", "worker", "attempts", "updated_at"])
    return JsonResponse({"jobs": [
        {"job_id": j.id, "stage": j.stage, "file_id": j.file_id, "path": j.file.path, "kind": j.file.kind,
         "size": j.file.size, "mtime": j.file.mtime.timestamp()}
        for j in jobs
    ]})


def _num(v):
    try:
        return None if v in (None, "") else float(str(v).replace(",", ""))
    except ValueError:
        return None


@require_POST
@worker_api
def result(request, job_id):
    try:
        job = Job.objects.select_related("file").get(pk=job_id, state=Job.State.RUNNING)
    except Job.DoesNotExist:
        return JsonResponse({"error": "実行中のジョブが見つかりません"}, status=404)
    try:
        data = json.loads(request.POST.get("data") or request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "data（JSON）が読めません"}, status=400)

    d = data.get("drawing") or {}
    bom = data.get("bom") or []
    pages = data.get("pages") or []
    f = job.file
    with transaction.atomic():
        drawing, _ = Drawing.objects.update_or_create(file=f, defaults={
            "drawing_no": (d.get("drawing_no") or "")[:64],
            "revision": (d.get("revision") or "")[:16],
            "title": (d.get("title") or "")[:255],
            "material": (d.get("material") or "")[:128],
            "scale": (d.get("scale") or "")[:32],
            "drawn_date": (d.get("drawn_date") or "")[:32],
            "source": d.get("source") or Drawing.Source.TEXT,
            "confidence": float(d.get("confidence", 1.0)),
            "needs_ocr": bool(d.get("needs_ocr")),
            "attributes": d.get("attributes") or {},
        })
        drawing.pages.all().delete()
        Page.objects.bulk_create([
            Page(drawing=drawing, page_no=int(p.get("page_no", i + 1)), text=p.get("text", ""),
                 text_source=p.get("text_source") or drawing.source)
            for i, p in enumerate(pages)
        ])
        drawing.bom_items.all().delete()
        BomItem.objects.bulk_create([
            BomItem(drawing=drawing, row=i + 1, item_no=str(b.get("item_no", ""))[:16],
                    part_no=str(b.get("part_no", ""))[:64], name=str(b.get("name", ""))[:255],
                    qty=_num(b.get("qty")), material=str(b.get("material", ""))[:128],
                    ref_drawing_no=str(b.get("ref_drawing_no", ""))[:64], raw_text=str(b.get("raw_text", "")),
                    confidence=float(b.get("confidence", 1.0)))
            for i, b in enumerate(bom)
        ])
        thumb = request.FILES.get("thumbnail")
        if thumb:
            rel = f"thumbs/{f.id // 1000:04d}/{f.id}.png"
            dest = Path(settings.MEDIA_ROOT) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as out:
                for chunk in thumb.chunks():
                    out.write(chunk)
            drawing.thumbnail = rel
        drawing.search_text = build_search_text(
            drawing.drawing_no, drawing.revision, drawing.title, drawing.material, f.path,
            *[" ".join([b.part_no, b.name, b.material, b.ref_drawing_no]) for b in drawing.bom_items.all()],
            *[(p.get("text") or "")[:4000] for p in pages],
        )
        drawing.save()
        f.sha256 = (data.get("sha256") or "")[:64]
        f.status, f.error = SourceFile.Status.DONE, ""
        f.save(update_fields=["sha256", "status", "error", "updated_at"])
        job.state, job.error = Job.State.DONE, ""
        job.save(update_fields=["state", "error", "updated_at"])
    return JsonResponse({"ok": True, "drawing_id": drawing.id})


@require_POST
@worker_api
def fail(request, job_id):
    data = _body(request) or {}
    try:
        job = Job.objects.select_related("file").get(pk=job_id, state=Job.State.RUNNING)
    except Job.DoesNotExist:
        return JsonResponse({"error": "実行中のジョブが見つかりません"}, status=404)
    job.error = str(data.get("error", ""))[:4000]
    retry = job.attempts < MAX_ATTEMPTS and not data.get("permanent")
    job.state = Job.State.QUEUED if retry else Job.State.FAILED
    job.save(update_fields=["state", "error", "updated_at"])
    if not retry:
        job.file.status, job.file.error = SourceFile.Status.ERROR, job.error
        job.file.save(update_fields=["status", "error", "updated_at"])
    return JsonResponse({"ok": True, "retry": retry})
