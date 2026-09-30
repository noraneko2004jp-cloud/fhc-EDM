"""解析し直したいファイルを、Mac mini の解析の待ち行列に入れ直す（新しいファイルより後回しにする）。

  docker compose exec web python manage.py requeue --kind dxf --dry-run   # 件数だけ見る
  docker compose exec web python manage.py requeue --kind dxf             # DXF を全部入れ直す
  docker compose exec web python manage.py requeue --kind pdf --path "図面 DXF・DWG・JW・PDF/CAK" --limit 30
"""
from django.core.management.base import BaseCommand

from drawings.models import Job, SourceFile

LOW_PRIORITY = 200  # 巡回で見つかった新しいファイル（100）より後


class Command(BaseCommand):
    help = "ファイルを解析の待ち行列に入れ直す（部品表の読み取りを追加したときなど）"

    def add_arguments(self, parser):
        parser.add_argument("--kind", choices=["dxf", "pdf"], help="種類で絞る")
        parser.add_argument("--path", default="", help="このフォルダ（共有フォルダからのパス）の中だけ")
        parser.add_argument("--limit", type=int, default=0, help="最大件数（0 は全部）")
        parser.add_argument("--dry-run", action="store_true", help="件数だけ表示して入れない")

    def handle(self, kind=None, path="", limit=0, dry_run=False, **_):
        qs = SourceFile.objects.exclude(status=SourceFile.Status.MISSING).order_by("path")
        if kind:
            qs = qs.filter(kind=kind)
        if path:
            qs = qs.filter(path__startswith=path.strip("/") + "/")
        busy = set(Job.objects.filter(state__in=[Job.State.QUEUED, Job.State.RUNNING]).values_list("file_id", flat=True))
        ids = [i for i in qs.values_list("id", flat=True) if i not in busy]
        if limit:
            ids = ids[:limit]
        if dry_run:
            self.stdout.write(f"入れ直す対象 {len(ids)} 件（すでに待ち・実行中のものは除く）。反映は --dry-run を外して実行")
            return
        Job.objects.bulk_create([Job(file_id=i, priority=LOW_PRIORITY) for i in ids], batch_size=2000)
        self.stdout.write(f"{len(ids)} 件を解析の待ち行列に入れました（新しいファイルより後に処理します）")
