"""ログインユーザーの権限（3 段階）。グループで決める。

  Admin : すべて見られる・管理画面に入れる（Admin グループに入れると管理者権限が自動で付く）
  User  : すべて見られる（管理画面には入れない）
  Guest : 「製品図面」と判定されたものだけ見られる

どのグループにも入っていない人は Guest と同じ扱い（安全側）。superuser は常に Admin 扱い。
図面を見せる・探す・ダウンロードするところは、必ず visible() を通す。
"""
from .classify import GUEST_TYPES
from .models import Drawing

ADMIN, USER, GUEST = "Admin", "User", "Guest"
LABELS = {ADMIN: "管理者", USER: "ユーザー", GUEST: "ゲスト"}


def level(user) -> str:
    if not user or not user.is_authenticated:
        return GUEST
    if user.is_superuser:
        return ADMIN
    names = set(user.groups.values_list("name", flat=True))
    for g in (ADMIN, USER):
        if g in names:
            return g
    return GUEST


def can_see_all(user) -> bool:
    return level(user) in (ADMIN, USER)


def visible(user, qs=None):
    """その人が見てよい図面だけに絞った QuerySet。"""
    qs = Drawing.objects.all() if qs is None else qs
    return qs if can_see_all(user) else qs.filter(doc_type__in=GUEST_TYPES)


def context(request):
    """テンプレートで使う：access_level（Admin/User/Guest）と表示名。"""
    lv = level(getattr(request, "user", None))
    return {"access_level": lv, "access_label": LABELS[lv]}
