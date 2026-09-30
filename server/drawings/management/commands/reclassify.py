"""型式・図番の系列の分け方（classify.py）を変えたあと、全図面に当て直す。

  docker compose exec web python manage.py reclassify
"""
from django.core.management.base import BaseCommand

from drawings import classify
from drawings.models import Drawing

FIELDS = ["model_family", "model_code", "series_prefix", "series"]


class Command(BaseCommand):
    help = "全図面の型式・図番の系列を classify.py のルールで決め直す"

    def handle(self, *args, **opts):
        batch, changed, total = [], 0, 0
        for d in Drawing.objects.select_related("file").iterator(chunk_size=2000):
            before = [getattr(d, f) for f in FIELDS]
            classify.apply(d)
            total += 1
            if [getattr(d, f) for f in FIELDS] != before:
                batch.append(d)
                changed += 1
            if len(batch) >= 2000:
                Drawing.objects.bulk_update(batch, FIELDS)
                batch = []
        if batch:
            Drawing.objects.bulk_update(batch, FIELDS)
        self.stdout.write(f"{total} 件中 {changed} 件を更新しました")
