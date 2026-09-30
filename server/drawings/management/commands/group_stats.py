"""グループ化の確認用：フォルダ・型式・図番の系列ごとの件数を出す（分け方の調整に使う）。

  docker compose exec web python manage.py group_stats            # 全部
  docker compose exec web python manage.py group_stats folder --depth 3
  docker compose exec web python manage.py group_stats model --top 60
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Count

from drawings.models import Drawing


class Command(BaseCommand):
    help = "フォルダ・型式・図番の系列ごとの件数"

    def add_arguments(self, parser):
        parser.add_argument("what", nargs="?", default="all", choices=["all", "folder", "model", "series"])
        parser.add_argument("--depth", type=int, default=2, help="フォルダを何階層目まで数えるか")
        parser.add_argument("--top", type=int, default=40)

    def handle(self, what, depth, top, **_):
        if what in ("all", "folder"):
            c = Counter()
            for path in Drawing.objects.values_list("file__path", flat=True).iterator(chunk_size=5000):
                parts = path.split("/")[:-1]
                for n in range(1, min(depth, len(parts)) + 1):
                    c["/".join(parts[:n])] += 1
            self.stdout.write(f"== フォルダ（{depth} 階層まで・図面数） ==")
            for k in sorted(c):
                self.stdout.write(f"{c[k]:>7}  {'  ' * k.count('/')}{k.rsplit('/', 1)[-1]}")
        for field, label, sub in (("model_family", "型式系統", "model_code"), ("series_prefix", "図番の頭", "series")):
            if what not in ("all", "model" if field == "model_family" else "series"):
                continue
            self.stdout.write(f"== {label}（上位 {top}） ==")
            for r in Drawing.objects.values(field).annotate(n=Count("id")).order_by("-n")[:top]:
                subs = (Drawing.objects.filter(**{field: r[field]}).values(sub).annotate(n=Count("id")).order_by("-n")[:6])
                self.stdout.write(f"{r['n']:>7}  {r[field] or '（未分類）'}   例: " + ", ".join(f"{s[sub]}({s['n']})" for s in subs))
