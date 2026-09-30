"""文書種別・型式・図番の系列の分け方（classify.py）を変えたあと、全図面に当て直す。

  docker compose exec web python manage.py reclassify --dry-run   # 書き換えずに、種別がどう変わるかだけ見る
  docker compose exec web python manage.py reclassify             # 反映する

管理画面で種別を手で直したもの（種別を手で確定）は、種別を変えない。
"""
from collections import Counter

from django.core.management.base import BaseCommand

from drawings import classify
from drawings.models import Drawing, Page

FIELDS = ["doc_type", "model_family", "model_code", "series_prefix", "series"]


class Command(BaseCommand):
    help = "全図面の文書種別・型式・図番の系列を classify.py のルールで決め直す"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="DB は書き換えず、種別の変化だけ表示する")
        parser.add_argument("--show", type=int, default=100,
                            help="製品図面に変わるもの（ゲストに見えるようになるもの）を何件まで表示するか")

    def handle(self, *args, dry_run=False, show=100, **opts):
        moves, to_drawing = Counter(), []
        batch, changed, total = [], 0, 0
        ids = list(Drawing.objects.order_by("id").values_list("id", flat=True))
        for i in range(0, len(ids), 1000):
            chunk = ids[i:i + 1000]
            texts = {}
            rows = Page.objects.filter(drawing_id__in=chunk).order_by("drawing_id", "page_no").values_list("drawing_id", "text")
            for did, text in rows:
                if len(texts.setdefault(did, [])) < 3:
                    texts[did].append((text or "")[:3000])
            for d in Drawing.objects.filter(id__in=chunk).select_related("file"):
                total += 1
                before = [getattr(d, f) for f in FIELDS]
                classify.apply(d, text="\n".join(texts.get(d.id, [])))
                if d.doc_type != before[0]:
                    moves[(before[0], d.doc_type)] += 1
                    if d.doc_type == classify.DRAWING:
                        to_drawing.append(f"{before[0]:>12} → drawing  {d.file.path}")
                if [getattr(d, f) for f in FIELDS] != before:
                    changed += 1
                    if not dry_run:
                        batch.append(d)
                if len(batch) >= 2000:
                    Drawing.objects.bulk_update(batch, FIELDS)
                    batch = []
        if batch:
            Drawing.objects.bulk_update(batch, FIELDS)

        self.stdout.write("== 文書種別の変化 ==")
        for (a, b), n in moves.most_common():
            self.stdout.write(f"{n:>7}  {a} → {b}")
        if not moves:
            self.stdout.write("      （変化なし）")
        if to_drawing:
            self.stdout.write(f"== 製品図面に変わるもの（ゲストに見えるようになる）{len(to_drawing)} 件 ==")
            for line in to_drawing[:show]:
                self.stdout.write("  " + line)
        if dry_run:
            self.stdout.write(f"※ 試しに判定しただけです（DB は変えていません）。変わるのは {changed} 件。"
                              "反映は --dry-run を外して実行")
        else:
            self.stdout.write(f"{total} 件中 {changed} 件を更新しました（種別を手で確定したものは種別を変えません）")
