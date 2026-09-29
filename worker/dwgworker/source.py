"""図面の置き場所を巡回・読み取りする。smb:// ならファイルサーバー（読み取り専用）、それ以外はローカルフォルダ。"""
from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath

from .config import CONFIG

EXTS = {".dxf", ".pdf"}


def _is_smb(root):
    return root.startswith("smb://")


def _unc(root, rel=""):
    base = "\\\\" + root[len("smb://"):].strip("/").replace("/", "\\")
    return base + ("\\" + rel.replace("/", "\\") if rel else "")


def _smb_login(root):
    import smbclient

    server = root[len("smb://"):].split("/", 1)[0]
    user = CONFIG.smb_user.replace("/", "\\")  # 「DOMAIN/名前」でも「DOMAIN\\名前」として扱う
    smbclient.register_session(server, username=user, password=CONFIG.smb_password)
    return smbclient


def _excluded(rel):
    return any(x and x in "/" + rel for x in CONFIG.exclude)


def walk(root=None):
    """(相対パス, サイズ, 更新UNIX秒) を順に返す。"""
    root = root or CONFIG.source_root
    if not root:
        raise RuntimeError("SOURCE_ROOT が設定されていません")
    if _is_smb(root):
        smb = _smb_login(root)
        stack = [""]
        while stack:
            rel_dir = stack.pop()
            for e in smb.scandir(_unc(root, rel_dir)):
                rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
                if _excluded(rel):
                    continue
                if e.is_dir():
                    stack.append(rel)
                elif PurePosixPath(e.name).suffix.lower() in EXTS:
                    st = e.stat()
                    yield rel, st.st_size, st.st_mtime
    else:
        base = Path(root)
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = Path(dirpath).relative_to(base).as_posix()
            dirnames[:] = [d for d in dirnames if not _excluded(f"{rel_dir}/{d}")]
            for name in filenames:
                if Path(name).suffix.lower() not in EXTS:
                    continue
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                if _excluded(rel):
                    continue
                st = os.stat(Path(dirpath) / name)
                if stat.S_ISREG(st.st_mode):
                    yield rel, st.st_size, st.st_mtime


def read_bytes(rel, root=None) -> bytes:
    root = root or CONFIG.source_root
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"不正なパス: {rel}")
    if _is_smb(root):
        smb = _smb_login(root)
        with smb.open_file(_unc(root, rel), mode="rb", share_access="rw") as f:
            return f.read()
    return (Path(root) / p).read_bytes()
