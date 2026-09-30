"""関連図面：部品表の「図番又は品番」と注記の参照図番（「…は HUCP101040 による」）から、図面どうしをつなぐ。

保存時に関連表を作るのではなく、表示のたびに図番で引く（相手の図面が後から解析されても、すぐにつながるように）。
どちらの向きも、見る人の権限（access.visible）で絞る。
"""
from django.db.models import Q

from . import access
from .models import BomItem, Drawing


def _by_no(user, numbers, exclude_pk):
    """図番 → [図面]（見てよいものだけ）"""
    numbers = [n for n in dict.fromkeys(numbers) if n]
    if not numbers:
        return {}
    out = {}
    for d in access.visible(user, Drawing.objects.select_related("file")).filter(drawing_no__in=numbers).exclude(pk=exclude_pk):
        out.setdefault(d.drawing_no, []).append(d)
    return out


def uses(user, d, bom):
    """この図面が使う部品の図面：[{no, via, rows, drawings}]"""
    refs = {}
    for b in bom:
        if b.ref_drawing_no:
            refs.setdefault(b.ref_drawing_no, {"no": b.ref_drawing_no, "via": "部品表", "rows": []})["rows"].append(b)
    attrs = d.attributes if isinstance(d.attributes, dict) else {}
    for no in attrs.get("note_refs") or []:
        refs.setdefault(no, {"no": no, "via": "注記", "rows": []})
    found = _by_no(user, list(refs), d.pk)
    for r in refs.values():
        r["drawings"] = found.get(r["no"], [])
    return list(refs.values())


def used_by(user, d, limit=100):
    """この図面を使っている図面（部品表か注記にこの図番があるもの）：[{drawing, via, row}]"""
    if not d.drawing_no:
        return []
    out, seen = [], set()
    rows = (BomItem.objects.select_related("drawing__file")
            .filter(Q(ref_drawing_no=d.drawing_no) | Q(part_no=d.drawing_no))
            .exclude(drawing_id=d.pk).order_by("drawing__drawing_no")[:limit * 2])
    visible = set(access.visible(user).filter(pk__in=[r.drawing_id for r in rows]).values_list("pk", flat=True))
    for r in rows:
        if r.drawing_id in visible and r.drawing_id not in seen:
            seen.add(r.drawing_id)
            out.append({"drawing": r.drawing, "via": "部品表", "row": r})
    notes = (access.visible(user, Drawing.objects.select_related("file"))
             .filter(attributes__note_refs__contains=[d.drawing_no]).exclude(pk=d.pk)[:limit])
    for x in notes:
        if x.pk not in seen:
            seen.add(x.pk)
            out.append({"drawing": x, "via": "注記", "row": None})
    return out[:limit]
