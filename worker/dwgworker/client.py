"""Ubuntu の社内API を呼ぶ。ワーカーが知っているのは SERVER_URL と WORKER_TOKEN だけ。"""
from __future__ import annotations

import json

import requests

from .config import CONFIG


class Api:
    def __init__(self, cfg=CONFIG):
        self.base = cfg.server_url + "/api/internal"
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {cfg.token}"
        self.name = cfg.worker_name

    def _check(self, r):
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", r.text)
            except ValueError:
                msg = r.text[:300]
            raise RuntimeError(f"サーバーが {r.status_code} を返しました: {msg}")
        return r.json()

    def health(self):
        return self._check(self.s.get(f"{self.base}/health", timeout=10))

    def scan(self, files):
        return self._check(self.s.post(f"{self.base}/scan", json={"files": files}, timeout=120))

    def scan_finish(self, started_at, complete):
        return self._check(self.s.post(f"{self.base}/scan/finish", json={"started_at": started_at, "complete": complete}, timeout=60))

    def claim(self, limit):
        return self._check(self.s.post(f"{self.base}/jobs/claim", json={"worker": self.name, "limit": limit}, timeout=30))["jobs"]

    def result(self, job_id, data, thumb: bytes | None):
        files = {"thumbnail": ("thumb.png", thumb, "image/png")} if thumb else None
        return self._check(self.s.post(f"{self.base}/jobs/{job_id}/result", data={"data": json.dumps(data, ensure_ascii=False)}, files=files, timeout=120))

    def fail(self, job_id, error, permanent=False):
        return self._check(self.s.post(f"{self.base}/jobs/{job_id}/fail", json={"error": error, "permanent": permanent}, timeout=30))
