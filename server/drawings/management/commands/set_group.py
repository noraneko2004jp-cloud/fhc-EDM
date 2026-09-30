"""ユーザーの権限（Admin / User / Guest）を設定する。ユーザーがいなければ作る。

  docker compose exec web python manage.py set_group yamada user          # 既存ユーザーを User に
  docker compose exec web python manage.py set_group guest01 guest --create   # 作ってパスワードを聞く
  docker compose exec web python manage.py set_group --list               # 一覧
"""
import getpass

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError

from drawings.access import ADMIN, GUEST, LABELS, USER, level

NAMES = {"admin": ADMIN, "user": USER, "guest": GUEST}


class Command(BaseCommand):
    help = "ユーザーを Admin / User / Guest のどれか 1 つのグループに入れる"

    def add_arguments(self, parser):
        parser.add_argument("username", nargs="?")
        parser.add_argument("group", nargs="?", choices=list(NAMES))
        parser.add_argument("--create", action="store_true", help="ユーザーがいなければ作る（パスワードを聞く）")
        parser.add_argument("--list", action="store_true", help="ユーザーと権限の一覧")

    def handle(self, username, group, create, list, **_):
        if list:
            for u in User.objects.order_by("username"):
                lv = level(u)
                self.stdout.write(f"{u.username:<20} {LABELS[lv]}（{lv}）{'' if u.is_active else ' ・無効'}")
            return
        if not username or not group:
            raise CommandError("使い方: set_group ユーザー名 admin|user|guest [--create]  または  set_group --list")
        u = User.objects.filter(username=username).first()
        if not u:
            if not create:
                raise CommandError(f"ユーザー {username} がいません。作るときは --create を付けてください")
            pw = getpass.getpass("パスワード: ")
            if len(pw) < 8 or pw != getpass.getpass("もう一度: "):
                raise CommandError("パスワードが 8 文字未満か、1 回目と一致しません")
            u = User.objects.create_user(username, password=pw)
        target = Group.objects.get(name=NAMES[group])
        others = Group.objects.filter(name__in=[ADMIN, USER, GUEST]).exclude(pk=target.pk)
        u.groups.remove(*others)
        u.groups.add(target)
        u.refresh_from_db()
        self.stdout.write(f"{u.username} を {LABELS[level(u)]}（{level(u)}）にしました")
