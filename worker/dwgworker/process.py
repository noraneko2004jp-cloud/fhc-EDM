"""1 ファイルを読み、種類に応じて解析する（子プロセスで実行される）。"""
from __future__ import annotations

import hashlib
import traceback

from . import parse_dxf, parse_pdf, source


def process(job: dict) -> dict:
    try:
        data = source.read_bytes(job["path"])
        sha = hashlib.sha256(data).hexdigest()
        parser = parse_dxf if job["kind"] == "dxf" else parse_pdf
        result, thumb = parser.parse(data, job["path"])
        result.pop("items", None)
        result["sha256"] = sha
        return {"ok": True, "job_id": job["job_id"], "result": result, "thumb": thumb}
    except Exception as e:
        return {"ok": False, "job_id": job["job_id"], "error": f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}",
                "permanent": isinstance(e, (ValueError, FileNotFoundError))}
