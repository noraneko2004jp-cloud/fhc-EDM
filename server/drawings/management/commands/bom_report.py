"""部品表の読み取り結果のまとめ（精度の確認・調整する図面選びに使う）。

  docker compose exec web python manage.py bom_report
  docker compose exec web python manage.py bom_report --worst 40 > bom_worst.csv   # 崩れの多い PDF の一覧（CSV）

品番の「形がよい」＝図番の形（HUXP010143、HB0011XXXX、0016608250 など）にそのまま合うもの。
OCR の読み違いがあると形が崩れるので、PDF の読み取り精度の目安になる。
"""
import csv
import re
from collections import Counter, defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from drawings.models import BomItem, Drawing, Job

CODE = re.compile(r"(?:[A-Z]{1,5}-?\d{3,8}(?:X{2,6})?(?:-\d{1,3})?[A-Z]?|\d{10})")


def _pct(a, b):
    return f"{a / b * 100:.0f}%" if b else "-"


class Command(BaseCommand):
    help = "部品表の読み取り結果のまとめ"

    def add_arguments(self, parser):
        parser.add_argument("--worst", type=int, default=0, help="品番の形が崩れている割合の高い PDF を N 件、CSV で出す")
        parser.add_argument("--days", type=int, default=3, help="失敗したジョブを数える日数")

    def handle(self, worst=0, days=3, **_):
        known = set(Drawing.objects.exclude(drawing_no="").values_list("drawing_no", flat=True))
        per = defaultdict(lambda: Counter())
        stats = defaultdict(Counter)
        rows = BomItem.objects.values_list("drawing_id", "drawing__file__kind", "part_no", "name", "qty", "ref_drawing_no")
        for did, kind, part, name, qty, ref in rows.iterator(chunk_size=5000):
            s = stats[kind]
            s["rows"] += 1
            good = bool(part) and bool(CODE.fullmatch(part))
            s["part"] += bool(part)
            s["good"] += good
            s["name"] += bool(name)
            s["qty"] += qty is not None
            s["ref"] += bool(ref)
            s["ref_found"] += bool(ref) and ref in known
            if kind == "pdf":
                c = per[did]
                c["rows"] += 1
                c["bad"] += bool(part) and not good
                c["noname"] += not name
        if worst:
            self._worst(per, worst)
            return
        for kind in sorted(stats):
            s = stats[kind]
            n = s["rows"]
            drawings = BomItem.objects.filter(drawing__file__kind=kind).values("drawing").distinct().count()
            self.stdout.write(f"== {kind.upper()}：部品表あり {drawings} 件 / 行 {n}")
            self.stdout.write(f"  品番あり {_pct(s['part'], n)}　うち形がよい {_pct(s['good'], s['part'])}")
            self.stdout.write(f"  名称あり {_pct(s['name'], n)}　員数あり {_pct(s['qty'], n)}")
            self.stdout.write(f"  関連図面つき {s['ref']} 行　うち図面が見つかる {s['ref_found']}（{_pct(s['ref_found'], s['ref'])}）")
        since = timezone.now() - timedelta(days=days)
        failed = Job.objects.filter(state=Job.State.FAILED, updated_at__gte=since)
        self.stdout.write(f"== 失敗したジョブ（{days} 日以内）：{failed.count()} 件")
        errs = Counter((e or "").splitlines()[0][:80] if e else "(なし)" for e in failed.values_list("error", flat=True))
        for e, k in errs.most_common(8):
            self.stdout.write(f"  {k:6d}  {e}")

    def _worst(self, per, n):
        cand = [(c["bad"] / c["rows"], c["noname"] / c["rows"], c["rows"], did) for did, c in per.items() if c["rows"] >= 3]
        cand.sort(reverse=True)
        top = cand[:n]
        paths = dict(Drawing.objects.filter(pk__in=[t[3] for t in top]).values_list("pk", "file__path"))
        nos = dict(Drawing.objects.filter(pk__in=[t[3] for t in top]).values_list("pk", "drawing_no"))
        w = csv.writer(self.stdout, lineterminator="\n")
        w.writerow(["図面ID", "図番", "行数", "品番の形が崩れた割合", "名称なしの割合", "ファイル"])
        for bad, noname, rows, did in top:
            w.writerow([did, nos.get(did, ""), rows, f"{bad:.2f}", f"{noname:.2f}", paths.get(did, "")])
