"""図面の原本を読むための入口。SOURCE_ROOT が smb:// ならファイルサーバー、それ以外はローカルフォルダ。

ファイルサーバーへは読み取り専用で開くだけで、書き込みは一切しない。
"""
from pathlib import Path, PurePosixPath

from django.conf import settings


def _safe_rel(rel: str) -> PurePosixPath:
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("不正なパスです")
    return p


def open_source(rel: str):
    """相対パスのファイルを読み取り専用で開き、バイナリのファイルオブジェクトを返す。"""
    root = settings.SOURCE_ROOT
    relp = _safe_rel(rel)
    if root.startswith("smb://"):
        import smbclient

        host_share = root[len("smb://"):].strip("/")
        server = host_share.split("/", 1)[0]
        smbclient.register_session(server, username=settings.SMB_USER.replace("/", "\\"), password=settings.SMB_PASSWORD)
        unc = "\\\\" + host_share.replace("/", "\\") + "\\" + str(relp).replace("/", "\\")
        return smbclient.open_file(unc, mode="rb", share_access="rw")
    return open(Path(root) / relp, "rb")
