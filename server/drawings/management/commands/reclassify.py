"""文書種別・型式・図番の系列の分け方（classify.py）を変えたあと、全図面に当て直す。

  docker compose exec web python manage.py reclassify
"""
from django.core.management.base import BaseCommand

from drawings import classify
from drawings.models import Drawing, Page

FIELDS = ["doc_type", "model_family", "model_code", "series_prefix", "series"]


class Command(BaseCommand):
    help = "全図面の文書種別・型式・図番の系列を classify.py のルールで決め直す"

    def handle(self, *args, **opts):
        batch, changed, total = [], 0, 0
        ids = list(Drawing.objects.order_by("id").values_list("id", flat=True))
        for i in range(0, len(ids), 1000):
            chunk = ids[i:i + 1000]
            texts = {}
            for did, text in Page.objects.filter(drawing_id__in=chunk).order_by("drawing_id", "page_no").values_list("drawing_id", "text"):
                if len(texts.setdefault(did, [])) < 3:
                    texts[did].append((text or "")[:3000])
            for d in Drawing.objects.filter(id__in=chunk).select_related("file"):
                self._one(d, texts, batch)
                total += 1
                if len(batch) >= 2000:
                    Drawing.objects.bulk_update(batch, FIELDS)
                    changed += len(batch)
                    batch = []
        if batch:
            Drawing.objects.bulk_update(batch, FIELDS)
            changed += len(batch)
        self.stdout.write(f"{total} 件中 {changed} 件を更新しました（種別を手で確定したものは種別を変えません）")

    def _one(self, d, texts, batch):
        before = [getattr(d, f) for f in FIELDS]
        classify.apply(d, text="\n".join(texts.get(d.id, [])))
        if [getattr(d, f) for f in FIELDS] != before:
            batch.append(d)
