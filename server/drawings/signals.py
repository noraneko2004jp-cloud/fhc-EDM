"""Admin グループへの出し入れで、管理画面に入る権限（is_staff / is_superuser）を合わせる。"""
from django.contrib.auth.models import Group, User
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from .access import ADMIN


def _sync(user):
    in_admin = user.groups.filter(name=ADMIN).exists()
    if in_admin and not (user.is_staff and user.is_superuser):
        user.is_staff = user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
    elif not in_admin and user.is_superuser:
        # 最後の管理者は外さない（誰も管理画面に入れなくなるのを防ぐ）
        if User.objects.filter(is_superuser=True, is_active=True).exclude(pk=user.pk).exists():
            user.is_staff = user.is_superuser = False
            user.save(update_fields=["is_staff", "is_superuser"])


@receiver(m2m_changed, sender=User.groups.through)
def groups_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if reverse:  # グループ側から人を出し入れした
        if isinstance(instance, Group) and instance.name == ADMIN:
            users = User.objects.filter(pk__in=pk_set or [])
            for u in users:
                _sync(u)
    else:
        _sync(instance)


@receiver(post_save, sender=User)
def superuser_joins_admin(sender, instance, **kwargs):
    """createsuperuser などで作った管理者も Admin グループに入れる（グループを正とする）。"""
    if instance.is_superuser and not instance.groups.filter(name=ADMIN).exists():
        g = Group.objects.filter(name=ADMIN).first()
        if g:
            instance.groups.add(g)
