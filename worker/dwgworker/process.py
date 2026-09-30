"""1 ファイルを読み、種類に応じて解析する（子プロセスで実行される）。"""
from __future__ import annotations

import hashlib
import re
import traceback

from . import parse_dxf, parse_pdf, source


_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")


def clean(v):
    """送信できない文字を取り除く。
    ・NUL などの制御文字（PostgreSQL が保存できない）
    ・読めなかったバイトの代わりに入る「半端なサロゲート」（\\udc90 など。DXF の文字コードが不正な場合に起きる）
    """
    if isinstance(v, str):
        return _BAD.sub("", v)
    if isinstance(v, list):
        return [clean(x) for x in v]
    if isinstance(v, dict):
        return {clean(k): clean(x) for k, x in v.items()}
    return v


def process(job: dict) -> dict:
    try:
        data = source.read_bytes(job["path"])
        sha = hashlib.sha256(data).hexdigest()
        parser = parse_dxf if job["kind"] == "dxf" else parse_pdf
        result, thumbs = parser.parse(data, job["path"])
        result.pop("items", None)
        result = clean(result)
        result["sha256"] = sha
        return {"ok": True, "job_id": job["job_id"], "result": result, "thumbs": thumbs}
    except Exception as e:
        return {"ok": False, "job_id": job["job_id"], "error": f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}",
                "permanent": isinstance(e, (ValueError, FileNotFoundError))}
