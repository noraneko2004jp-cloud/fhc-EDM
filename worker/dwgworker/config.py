"""ワーカー設定。worker/.env（なければ環境変数）から読む。"""
from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path


def load_env(path: Path):
    """.env を読む。同じ項目が複数行あるときは後の行を使う。すでに環境変数にある値は変えない。"""
    if not path.exists():
        return
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in ("'", '"') and v[:1] in v[1:]:
            v = v[1:v.index(v[0], 1)]
        else:
            v = "" if v.startswith("#") else re.split(r"\s+#", v, maxsplit=1)[0].strip()  # 行末の「 # コメント」を除く
        values[k.strip()] = v
    for k, v in values.items():
        os.environ.setdefault(k, v)


load_env(Path(os.environ.get("DWGFIND_ENV", Path(__file__).resolve().parent.parent / ".env")))


def _list(key, default):
    return [x.strip() for x in os.environ.get(key, default).split(",") if x.strip()]


@dataclass
class Config:
    server_url: str = os.environ.get("SERVER_URL", "http://128.131.250.252:8000").rstrip("/")
    token: str = os.environ.get("WORKER_TOKEN", "")
    source_root: str = os.environ.get("SOURCE_ROOT", "")
    smb_user: str = os.environ.get("SMB_USER", "")
    smb_password: str = os.environ.get("SMB_PASSWORD", "")
    worker_name: str = os.environ.get("WORKER_NAME", socket.gethostname())
    concurrency: int = int(os.environ.get("WORKER_CONCURRENCY", "6"))
    scan_interval_min: int = int(os.environ.get("SCAN_INTERVAL_MIN", "60"))
    # 巡回するフォルダ（共有の直下の名前、カンマ区切り。空なら全部）。全角半角の違いは無視して比べる
    include: list = field(default_factory=lambda: _list("SCAN_INCLUDE", ""))
    # 巡回から外すフォルダ（部分一致、カンマ区切り）
    exclude: list = field(default_factory=lambda: _list("SCAN_EXCLUDE", "~$,/.Trash,/#recycle,/$RECYCLE.BIN"))
    # 図番の形（社内規則に合わせて .env で上書きする）
    drawing_no_regex: str = os.environ.get("DRAWING_NO_REGEX", r"[A-Z]{1,4}-?\d{3,8}(?:X{2,6})?(?:-\d{1,3})?[A-Z]?|(?<!\d)\d{10}(?!\d)")
    dxf_fallback_font: str = os.environ.get("DXF_FALLBACK_FONT", "")
    # スキャン PDF の OCR（macOS Vision）。1 ファイルで OCR する最大ページ数
    ocr_enabled: bool = os.environ.get("OCR_ENABLED", "1") not in ("0", "false", "no")
    ocr_max_pages: int = int(os.environ.get("OCR_MAX_PAGES", "30"))
    ocr_dpi: int = int(os.environ.get("OCR_DPI", "300"))
    thumb_width: int = int(os.environ.get("THUMB_WIDTH", "1200"))
    # 部品表の読み取り。DXF は常に行う。スキャン PDF（OCR）は精度を確かめてから BOM_PDF=1 で有効にする
    bom_pdf: bool = os.environ.get("BOM_PDF", "0").strip() in ("1", "true", "yes")


CONFIG = Config()
