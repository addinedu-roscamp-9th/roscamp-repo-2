from __future__ import annotations

import json
import urllib.request
from typing import Any

from PyQt6.QtCore import QRunnable


class HttpJob(QRunnable):
    def __init__(self, method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 0.6):
        super().__init__()
        self.method = method
        self.url = url
        self.payload = payload
        self.timeout = timeout

    def run(self):
        try:
            m = self.method.upper()

            if m == "GET":
                req = urllib.request.Request(self.url, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    resp.read()
                return

            body = b""
            headers: dict[str, str] = {}
            if self.payload is not None:
                body = json.dumps(self.payload).encode("utf-8")
                headers["Content-Type"] = "application/json"

            req = urllib.request.Request(self.url, data=body, headers=headers, method=m)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp.read()

        except Exception:
            return


def http_get_json(url: str, timeout: float = 0.6) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
        return json.loads(data)
    except Exception:
        return None
