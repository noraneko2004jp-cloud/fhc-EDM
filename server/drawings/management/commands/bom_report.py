"""部品表の読み取り結果のまとめ（精度の確認・調整する図面選びに使う）。

  docker compose exec web python manage.py bom_report
  docker compose exec -T web python manage.py bom_report --worst 40 > bom_worst.csv           # 品番の崩れが多い PDF（CSV）
  docker compose exec -T web python manage.py bom_report --worst 40 --by qty > bom_qty.csv    # 員数が読めていない PDF（CSV）

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
        parser.add_argument("--by", choices=["part", "qty"], default="part", help="--worst の並べ方（qty：員数のない行が多い順）")
        parser.add_argument("--days", type=int, default=3, help="失敗したジョブを数える日数")
        parser.add_argument("--depth", type=int, default=3, help="フォルダ別の集計で数える階層")

    def handle(self, worst=0, days=3, by="part", depth=3, **_):
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
                c["qty"] += qty is not None
        if worst:
            self._worst(per, worst, by)
            return
        for kind in sorted(stats):
            s = stats[kind]
            n = s["rows"]
            drawings = BomItem.objects.filter(drawing__file__kind=kind).values("drawing").distinct().count()
            self.stdout.write(f"== {kind.upper()}：部品表あり {drawings} 件 / 行 {n}")
            self.stdout.write(f"  品番あり {_pct(s['part'], n)}　うち形がよい {_pct(s['good'], s['part'])}")
            self.stdout.write(f"  名称あり {_pct(s['name'], n)}　員数あり {_pct(s['qty'], n)}")
            self.stdout.write(f"  関連図面つき {s['ref']} 行　うち図面が見つかる {s['ref_found']}（{_pct(s['ref_found'], s['ref'])}）")
        self._qty_breakdown(per, depth)
        since = timezone.now() - timedelta(days=days)
        failed = Job.objects.filter(state=Job.State.FAILED, updated_at__gte=since)
        self.stdout.write(f"== 失敗したジョブ（{days} 日以内）：{failed.count()} 件")
        errs = Counter((e or "").splitlines()[0][:80] if e else "(なし)" for e in failed.values_list("error", flat=True))
        for e, k in errs.most_common(8):
            self.stdout.write(f"  {k:6d}  {e}")
        last = failed.order_by("-updated_at").values_list("updated_at", flat=True).first()
        if last:
            self.stdout.write(f"  いちばん新しい失敗：{timezone.localtime(last):%Y-%m-%d %H:%M}")

    def _qty_breakdown(self, per, depth):
        """PDF の員数：図面ごとの埋まり具合と、フォルダ別の割合（どの種類の図面で読めていないかを見る）。"""
        if not per:
            return
        bands = Counter()
        for c in per.values():
            r = c["qty"] / c["rows"]
            bands["全行に員数あり" if r >= 0.999 else "8割以上" if r >= 0.8 else "一部（8割未満）" if r > 0 else "員数が1行もない"] += 1
        self.stdout.write("== PDF の員数（図面ごと）")
        for k in ("全行に員数あり", "8割以上", "一部（8割未満）", "員数が1行もない"):
            self.stdout.write(f"  {k}：{bands[k]} 件（{_pct(bands[k], len(per))}）")
        folders = defaultdict(Counter)
        paths = dict(Drawing.objects.filter(pk__in=list(per)).values_list("pk", "file__path"))
        for did, c in per.items():
            f = "/".join(paths.get(did, "").split("/")[:-1][:depth])
            folders[f]["rows"] += c["rows"]
            folders[f]["qty"] += c["qty"]
            folders[f]["n"] += 1
        self.stdout.write(f"== PDF の員数（フォルダ別・{depth} 階層・行の多い順）")
        for f, c in sorted(folders.items(), key=lambda kv: -kv[1]["rows"])[:25]:
            self.stdout.write(f"  {_pct(c['qty'], c['rows']):>4}  {c['n']:5d} 件 {c['rows']:6d} 行  {f}")

    def _worst(self, per, n, by="part"):
        if by == "qty":
            cand = [(c["rows"] - c["qty"], 1 - c["qty"] / c["rows"], c["rows"], did) for did, c in per.items() if c["rows"] >= 3]
        else:
            cand = [(c["bad"] / c["rows"], c["noname"] / c["rows"], c["rows"], did) for did, c in per.items() if c["rows"] >= 3]
        cand.sort(reverse=True)
        top = cand[:n]
        paths = dict(Drawing.objects.filter(pk__in=[t[3] for t in top]).values_list("pk", "file__path"))
        nos = dict(Drawing.objects.filter(pk__in=[t[3] for t in top]).values_list("pk", "drawing_no"))
        w = csv.writer(self.stdout, lineterminator="\n")
        if by == "qty":
            w.writerow(["図面ID", "図番", "行数", "員数のない行", "員数のない割合", "ファイル"])
            for miss, rate, rows, did in top:
                w.writerow([did, nos.get(did, ""), rows, miss, f"{rate:.2f}", paths.get(did, "")])
            return
        w.writerow(["図面ID", "図番", "行数", "品番の形が崩れた割合", "名称なしの割合", "ファイル"])
        for bad, noname, rows, did in top:
            w.writerow([did, nos.get(did, ""), rows, f"{bad:.2f}", f"{noname:.2f}", paths.get(did, "")])
