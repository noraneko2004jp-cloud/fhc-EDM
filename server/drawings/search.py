"""検索：承認済み辞書で語を展開し、正規化済みの search_text を部分一致で探す。"""
from django.db.models import Q

from .models import DictionaryEntry, Drawing
from .text import norm


def expand(q: str) -> list[str]:
    """入力語と、辞書で同じ代表語に結ばれた語を返す（入力語が先頭）。"""
    q = (q or "").strip()
    nq = norm(q)
    if not nq:
        return []
    terms = [q]
    canon = set(
        DictionaryEntry.objects.filter(approved=True)
        .filter(Q(term__iexact=q) | Q(canonical__iexact=q) | Q(term__iexact=nq) | Q(canonical__iexact=nq))
        .values_list("canonical", flat=True)
    )
    if canon:
        rows = DictionaryEntry.objects.filter(approved=True, canonical__in=canon).values_list("term", "canonical")
        for t, c in rows:
            for w in (c, t):
                if norm(w) not in {norm(x) for x in terms}:
                    terms.append(w)
    return terms


def search(q: str, kind: str = ""):
    """(展開語, 図面の QuerySet) を返す。複数語はスペース区切りで AND、各語は同義語で OR。"""
    qs = Drawing.objects.select_related("file").exclude(file__status="missing")
    if kind in ("dxf", "pdf"):
        qs = qs.filter(file__kind=kind)
    shown = []
    for word in (q or "").split():
        terms = expand(word)
        shown.extend(terms)
        cond = Q()
        for t in terms:
            cond |= Q(search_text__contains=norm(t))
        qs = qs.filter(cond)
    return shown, qs.order_by("drawing_no", "revision", "id")
