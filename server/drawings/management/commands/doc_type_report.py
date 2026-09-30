"""文書種別の判定の内訳：種別ごとに、判定理由・多いフォルダ・ファイル名の例を出す（判定ルールの調整用）。

  docker compose exec web python manage.py doc_type_report                 # 全種別
  docker compose exec web python manage.py doc_type_report application     # 1 種別だけ
  docker compose exec web python manage.py doc_type_report other --samples 30
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from drawings import classify
from drawings.models import Drawing, Page


class Command(BaseCommand):
    help = "文書種別の判定理由・フォルダ・例"

    def add_arguments(self, parser):
        parser.add_argument("doc_type", nargs="?", choices=[k for k, _ in classify.DOC_TYPES])
        parser.add_argument("--samples", type=int, default=12)
        parser.add_argument("--depth", type=int, default=3, help="フォルダを何階層目までで数えるか")

    def handle(self, doc_type, samples, depth, **_):
        reasons, folders, examples, total = defaultdict(Counter), defaultdict(Counter), defaultdict(list), Counter()
        ids = list(Drawing.objects.order_by("id").values_list("id", flat=True))
        for i in range(0, len(ids), 1000):
            chunk = ids[i:i + 1000]
            texts = {}
            for did, text in Page.objects.filter(drawing_id__in=chunk).order_by("drawing_id", "page_no").values_list("drawing_id", "text"):
                if len(texts.setdefault(did, [])) < 3:
                    texts[did].append((text or "")[:3000])
            for d in Drawing.objects.filter(id__in=chunk).select_related("file"):
                t, why = classify.explain(d.file.path, d.drawing_no, d.source, d.confidence, d.file.kind,
                                          "\n".join(texts.get(d.id, [])))
                if d.doc_type_fixed:
                    t, why = d.doc_type, "手で確定"
                if doc_type and t != doc_type:
                    continue
                total[t] += 1
                reasons[t][why.split("（")[0]] += 1  # フォルダ名の理由はフォルダを除いて数える
                folders[t]["/".join(d.file.path.split("/")[:-1][:depth]) or "（最上位）"] += 1
                if len(examples[t]) < samples:
                    examples[t].append(f"{d.file.path}  ［{why}］")
        labels = dict(classify.DOC_TYPES)
        for t, n in total.most_common():
            self.stdout.write(f"\n==== {labels[t]}（{t}）{n} 件 ====")
            self.stdout.write("-- 判定理由")
            for why, c in reasons[t].most_common(15):
                self.stdout.write(f"{c:>7}  {why}")
            self.stdout.write(f"-- 多いフォルダ（{depth} 階層まで）")
            for f, c in folders[t].most_common(15):
                self.stdout.write(f"{c:>7}  {f}")
            self.stdout.write("-- 例")
            for e in examples[t]:
                self.stdout.write(f"         {e}")
        self.stdout.write("\n※ 今の DB の値ではなく、今のルールで判定し直した結果です（DB に反映するには reclassify）")
